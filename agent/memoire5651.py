"""5.6.51 (audit 5.6.50, A02 à A04) : décisions du cabinet, analyse de ce qui change, lacunes.

- **A02** Décisions du cabinet : objectif du client, consigne, argument retenu ou abandonné, choix stratégique. Chacune porte un auteur, une
  date, un objet, une portée (toujours ce dossier : une décision propre au dossier ne devient pas une règle du cabinet), un motif
  facultatif, une version et la décision qu'elle remplace. L'historique est conservé ; une réanalyse n'écrase jamais une décision.
  Ces décisions sont distinctes des décisions de justice (``modele5651.judgments``).
- **A03** Journal des changements : un événement durable est écrit dans la même transaction que la nouveauté ; clé de déduplication
  dossier + objet + version/empreinte + type ; les arrivées rapprochées sont regroupées en un bilan ; une opération technique
  (réindexation, mise en forme seule) reste hors de la carte « Depuis votre dernière lecture ».
- **A04** Lacunes : donnée attendue, raison, source recherchée, responsable, étapes concernées, portée du blocage (engagement,
  paragraphe, facultatif) ; une lacune bloque ce qu'elle bloque, pas le reste.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

from .common import Stop

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d51_cabinet_decisions(id TEXT PRIMARY KEY, matter TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', author TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', version INTEGER NOT NULL DEFAULT 1,
  replaces TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_cabinet_decisions_matter ON d51_cabinet_decisions(matter, status, kind);
CREATE TABLE IF NOT EXISTS d51_changes(id INTEGER PRIMARY KEY AUTOINCREMENT, matter TEXT NOT NULL, dedup TEXT NOT NULL, kind TEXT NOT NULL,
  object_kind TEXT NOT NULL, object_id TEXT NOT NULL, version TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}',
  technical INTEGER NOT NULL DEFAULT 0, acknowledged INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, UNIQUE(matter, dedup));
CREATE INDEX IF NOT EXISTS d51_changes_matter ON d51_changes(matter, technical, id);
CREATE TABLE IF NOT EXISTS d51_reads(owner TEXT NOT NULL, matter TEXT NOT NULL, last_change INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL,
  PRIMARY KEY(owner, matter));
CREATE TABLE IF NOT EXISTS d51_gaps(id TEXT PRIMARY KEY, matter TEXT NOT NULL, mission TEXT NOT NULL DEFAULT '', expected TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', searched TEXT NOT NULL DEFAULT '', responsible TEXT NOT NULL DEFAULT '', steps TEXT NOT NULL DEFAULT '[]',
  blocking TEXT NOT NULL DEFAULT 'paragraphe', kind TEXT NOT NULL DEFAULT 'information', state TEXT NOT NULL DEFAULT 'ouverte',
  answer TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS d51_gaps_matter ON d51_gaps(matter, state);
CREATE TABLE IF NOT EXISTS d51_draft_paragraphs(matter TEXT NOT NULL, work TEXT NOT NULL, idx INTEGER NOT NULL, text_sha TEXT NOT NULL,
  text TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT 'actif', PRIMARY KEY(matter, work, idx));
'''
DECISION_KINDS = {'objectif': 'Objectif du client', 'consigne': 'Consigne du cabinet', 'argument_retenu': 'Argument retenu',
                  'argument_abandonne': 'Argument abandonné (ne plus proposer)', 'choix': 'Choix stratégique'}
CHANGE_KINDS = {'nouveau_document': 'Nouveau document', 'nouvelle_version': 'Nouvelle version d’un document', 'nouveau_courriel': 'Nouveau courriel',
                'classement': 'Document classé', 'correction': 'Correction de l’avocat', 'contradiction': 'Information contradictoire',
                'montant_modifie': 'Montant modifié', 'evenement': 'Événement de procédure', 'lecture': 'Lecture du document',
                'decision_cabinet': 'Décision du cabinet', 'projet_a_recontroler': 'Projet à recontrôler'}
BLOCKING = {'engagement': 'Bloque l’engagement (envoi, dépôt, signature)', 'paragraphe': 'Bloque seulement les passages concernés',
            'facultatif': 'Facultatif'}
GAP_KINDS = {'information': 'Information à fournir', 'fichier_illisible': 'Fichier illisible', 'connecteur': 'Échec d’un connecteur',
             'destinataire': 'Destinataire à préciser', 'decision': 'Décision de l’avocat'}
GROUP_MINUTES = 15


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:24]


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


# ------------------------------------------------------------------------------------------------ A02 décisions du cabinet
def decisions(desk, mid, active_only=True):
    ensure_schema(desk)
    sql = 'SELECT * FROM d51_cabinet_decisions WHERE matter=?' + (" AND status='active'" if active_only else '') + ' ORDER BY kind, created'
    return [dict(r) for r in desk.db.execute(sql, (mid,))]


def save_decision(desk, mid, author, data):
    """Nouvelle décision, ou nouvelle version d'une décision existante (« replaces ») : l'ancienne reste dans l'historique."""
    ensure_schema(desk)
    kind = str(data.get('kind') or '')
    if kind not in DECISION_KINDS:
        raise Stop('type_decision_cabinet_invalide')
    text = re.sub(r'\s+', ' ', str(data.get('texte') or '')).strip()
    if not 3 <= len(text) <= 2000:
        raise Stop('texte_decision_cabinet_invalide')
    motif = str(data.get('motif') or '').strip()[:1000]
    subject = re.sub(r'\s+', ' ', str(data.get('objet') or '')).strip()[:200]
    now = _now()
    replaces, version = str(data.get('remplace') or ''), 1
    if replaces:
        old = desk.db.execute("SELECT * FROM d51_cabinet_decisions WHERE id=? AND matter=?", (replaces, mid)).fetchone()
        if not old:
            raise Stop('decision_absente')
        if old['status'] != 'active':
            raise Stop('version_en_conflit')
        if data.get('version') not in (None, '') and int(data['version']) != old['version']:
            raise Stop('version_en_conflit')
        version = old['version'] + 1
        subject = subject or old['subject']
        desk.db.execute("UPDATE d51_cabinet_decisions SET status='remplacee',updated=? WHERE id=?", (now, replaces))
    ident = _sha(mid, kind, text, now, author)
    desk.db.execute('INSERT INTO d51_cabinet_decisions(id,matter,kind,subject,text,motif,author,version,replaces,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, mid, kind, subject, text, motif, author, version, replaces, now, now))
    record_change(desk, mid, 'decision_cabinet', 'decision', ident, str(version), DECISION_KINDS[kind] + ' : ' + text[:120], {'motif': motif}, commit=False)
    mark_dependents(desk, mid, 'Décision du cabinet modifiée : %s' % text[:120], commit=False)
    desk.db.commit()
    return ident


def withdraw_decision(desk, mid, ident, author):
    ensure_schema(desk)
    cur = desk.db.execute("UPDATE d51_cabinet_decisions SET status='retiree',updated=? WHERE id=? AND matter=? AND status='active'", (_now(), ident, mid))
    if not cur.rowcount:
        raise Stop('decision_absente')
    record_change(desk, mid, 'decision_cabinet', 'decision', ident, 'retrait', 'Décision du cabinet retirée par ' + author, commit=False)
    desk.db.commit()


def abandoned_arguments(desk, mid):
    return [d for d in decisions(desk, mid) if d['kind'] == 'argument_abandonne']


def conflicts(desk, mid):
    """Consignes actives qui portent sur le même objet : présentées au demandeur, jamais arbitrées en silence par le modèle."""
    by_subject = {}
    for d in decisions(desk, mid):
        if d['kind'] in ('consigne', 'choix', 'argument_retenu', 'argument_abandonne') and d['subject']:
            by_subject.setdefault(d['subject'].lower(), []).append(d)
    return [v for v in by_subject.values() if len(v) > 1]


def is_abandoned(desk, mid, text):
    """Un argument abandonné ne réapparaît pas : rapprochement par mots significatifs (au moins 60 % des mots de la décision)."""
    from .common import fold
    words = lambda s: {w for w in re.findall(r'[a-z0-9]{4,}', fold(s))}
    target = words(text)
    for d in abandoned_arguments(desk, mid):
        ref = words(d['subject'] or d['text'])
        if ref and len(ref & target) >= max(1, int(0.6 * len(ref))):
            return d
    return None


# ------------------------------------------------------------------------------------------------ A03 changements
def record_change(desk, mid, kind, object_kind, object_id, version, title, detail=None, technical=False, commit=True):
    """Événement durable dédupliqué (dossier + objet + version + type) ; renvoie l'identifiant, ou None si déjà connu."""
    ensure_schema(desk)
    dedup = _sha(mid, object_kind, object_id, version, kind)
    cur = desk.db.execute('INSERT OR IGNORE INTO d51_changes(matter,dedup,kind,object_kind,object_id,version,title,detail,technical,created) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?)', (mid, dedup, kind, object_kind, str(object_id)[:300], str(version)[:80], str(title)[:300],
                                                        json.dumps(detail or {}, ensure_ascii=False, default=str)[:8000], 1 if technical else 0, _now()))
    if commit:
        desk.db.commit()
    return cur.lastrowid if cur.rowcount else None


def changes(desk, mid, since=0, technical=False, limit=200):
    ensure_schema(desk)
    rows = desk.db.execute('SELECT * FROM d51_changes WHERE matter=? AND id>? AND technical=? ORDER BY id DESC LIMIT ?',
                           (mid, int(since or 0), 1 if technical else 0, int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d['detail'] = json.loads(d['detail'] or '{}')
        out.append(d)
    return out


def grouped(items):
    """Bilan consolidé : les changements arrivés à moins de GROUP_MINUTES les uns des autres forment un seul groupe."""
    groups, current = [], []
    for c in sorted(items, key=lambda x: x['id']):
        if current:
            last = datetime.fromisoformat(current[-1]['created'])
            if datetime.fromisoformat(c['created']) - last > timedelta(minutes=GROUP_MINUTES):
                groups.append(current)
                current = []
        current.append(c)
    if current:
        groups.append(current)
    out = []
    for g in reversed(groups):
        kinds = {}
        for c in g:
            kinds[c['kind']] = kinds.get(c['kind'], 0) + 1
        out.append({'from': g[0]['created'], 'to': g[-1]['created'], 'items': g, 'summary': ', '.join(
            '%d %s' % (n, CHANGE_KINDS.get(k, k).lower()) for k, n in sorted(kinds.items(), key=lambda x: -x[1]))})
    return out


def since_last_read(desk, mid, owner):
    ensure_schema(desk)
    row = desk.db.execute('SELECT last_change FROM d51_reads WHERE owner=? AND matter=?', (owner, mid)).fetchone()
    last = row['last_change'] if row else 0
    return {'last': last, 'groups': grouped(changes(desk, mid, last)), 'first_visit': row is None}


def mark_read(desk, mid, owner):
    ensure_schema(desk)
    top = desk.db.execute('SELECT COALESCE(MAX(id),0) FROM d51_changes WHERE matter=?', (mid,)).fetchone()[0]
    desk.db.execute('INSERT OR REPLACE INTO d51_reads(owner,matter,last_change,at) VALUES(?,?,?,?)', (owner, mid, top, _now()))
    desk.db.commit()
    return top


def index_draft(desk, mid, work, paragraphs):
    """Carte des dépendances (A03) : chaque paragraphe d'un projet produit, avec son empreinte et les sources qu'il cite."""
    ensure_schema(desk)
    desk.db.execute('DELETE FROM d51_draft_paragraphs WHERE matter=? AND work=?', (mid, work))
    for i, p in enumerate(paragraphs[:2000]):
        text = str(p.get('texte') or p.get('text') or '') if isinstance(p, dict) else str(p)
        if not text.strip():
            continue
        srcs = (p.get('source_ids') or p.get('sources') or []) if isinstance(p, dict) else []
        desk.db.execute('INSERT OR REPLACE INTO d51_draft_paragraphs(matter,work,idx,text_sha,text,sources) VALUES(?,?,?,?,?,?)',
                        (mid, work, i, _sha(text), text[:4000], json.dumps(srcs, ensure_ascii=False)[:2000]))
    desk.db.commit()


def _norm_amount(value):
    return re.sub(r'[\s  .]', '', str(value or '')).replace(',', '.')


def mark_dependents(desk, mid, reason, paths=None, amounts=None, commit=True):
    """A03 / D07 : les projets qui dépendent d'une information modifiée sont signalés « à recontrôler » ; aucun texte n'est réécrit.
    Les passages concernés sont repérés dans la carte des paragraphes produits (montant cité, source utilisée)."""
    ensure_schema(desk)
    found = {}
    wanted = [a for a in (_norm_amount(x) for x in amounts or []) if a]
    for r in desk.db.execute("SELECT work,idx,text,sources FROM d51_draft_paragraphs WHERE matter=? AND state='actif'", (mid,)).fetchall():
        flat = _norm_amount(r['text'])
        hit = any(a in flat for a in wanted) or any(p and p in r['sources'] for p in (paths or []))
        if hit:
            found.setdefault(r['work'], []).append({'paragraphe': r['idx'] + 1, 'texte': r['text'][:300]})
    out = [{'travail': w, 'passages': v[:8]} for w, v in found.items()]
    if out:
        record_change(desk, mid, 'projet_a_recontroler', 'projets', ','.join(sorted(found))[:200], _sha(reason, json.dumps(out)),
                      '%d projet(s) à recontrôler : %s' % (len(out), reason[:160]), {'projets': out, 'raison': reason}, commit=False)
    if commit:
        desk.db.commit()
    return out


# ------------------------------------------------------------------------------------------------ A04 lacunes
def gaps(desk, mid, state='ouverte'):
    ensure_schema(desk)
    rows = [dict(r) for r in desk.db.execute('SELECT * FROM d51_gaps WHERE matter=?' + (' AND state=?' if state else '') + ' ORDER BY created',
                                              (mid, state) if state else (mid,))]
    for r in rows:
        r['steps'] = json.loads(r['steps'] or '[]')
    return rows


def open_gap(desk, mid, expected, reason='', searched='', steps=None, blocking='paragraphe', kind='information', mission='', responsible=''):
    """Une lacune par donnée attendue et par mission (idempotent) ; jamais une boucle de relances pour une même erreur permanente."""
    ensure_schema(desk)
    if blocking not in BLOCKING:
        raise Stop('portee_lacune_invalide')
    if kind not in GAP_KINDS:
        raise Stop('type_lacune_invalide')
    expected = re.sub(r'\s+', ' ', str(expected or '')).strip()[:300]
    if len(expected) < 3:
        raise Stop('lacune_invalide')
    from .common import fold
    ident = _sha(mid, mission, fold(expected))
    now = _now()
    row = desk.db.execute('SELECT * FROM d51_gaps WHERE id=?', (ident,)).fetchone()
    if row:
        if row['state'] == 'ouverte':
            desk.db.execute('UPDATE d51_gaps SET attempts=attempts+1,updated=? WHERE id=?', (now, ident))
            desk.db.commit()
        return ident
    desk.db.execute('INSERT INTO d51_gaps(id,matter,mission,expected,reason,searched,responsible,steps,blocking,kind,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, mid, mission, expected, str(reason)[:500], str(searched)[:300], str(responsible)[:120], json.dumps(list(steps or [])[:30]),
                     blocking, kind, now, now))
    desk.db.commit()
    return ident


def resolve_gap(desk, mid, ident, author, answer='', state='resolue'):
    ensure_schema(desk)
    if state not in ('resolue', 'abandonnee'):
        raise Stop('etat_lacune_invalide')
    row = desk.db.execute("SELECT * FROM d51_gaps WHERE id=? AND matter=?", (ident, mid)).fetchone()
    if not row:
        raise Stop('lacune_absente')
    if row['state'] != 'ouverte':
        return dict(row)
    desk.db.execute('UPDATE d51_gaps SET state=?,answer=?,updated=? WHERE id=?', (state, str(answer)[:2000], _now(), ident))
    record_change(desk, mid, 'correction', 'lacune', ident, state, 'Information fournie : ' + row['expected'][:120] if state == 'resolue'
                  else 'Lacune abandonnée : ' + row['expected'][:120], {'answer': str(answer)[:500], 'auteur': author}, commit=False)
    desk.db.commit()
    return dict(desk.db.execute('SELECT * FROM d51_gaps WHERE id=?', (ident,)).fetchone())


def sync_gaps(desk, mid):
    """Lacunes reprises des missions (« données manquantes ») et des documents illisibles ; sans appel au modèle."""
    ensure_schema(desk)
    n = 0
    try:
        for d in desk.db.execute("SELECT d.id,d.mission,d.question,d.payload FROM decisions5614 d JOIN missions5614 m ON m.id=d.mission "
                                 "WHERE m.matter=? AND d.state='a_decider' AND d.kind='champs_manquants'", (mid,)).fetchall():
            payload = json.loads(d['payload'] or '{}')
            for f in payload.get('fields') or []:
                open_gap(desk, mid, f, 'Mission : ' + d['question'][:200], 'pièces et courriels du dossier', [payload.get('task', '')], 'paragraphe',
                         'information', d['mission'])
                n += 1
    except Exception:   # noqa: BLE001 — missions absentes
        pass
    for r in desk.db.execute("SELECT path,name,error_category FROM d51_documents WHERE matter=? AND reading IN ('erreur','protegee','non_prise_en_charge')", (mid,)).fetchall():
        open_gap(desk, mid, 'Lecture de « %s »' % r['name'], 'Document illisible (%s) : son contenu n’est pas pris en compte.' % (r['error_category'] or 'erreur'),
                 r['path'], [], 'facultatif', 'fichier_illisible')
        n += 1
    return n

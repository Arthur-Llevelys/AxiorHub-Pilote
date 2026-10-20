"""5.6.67 (point 5) : apprendre des corrections, de façon mesurable.

1. Corrections récurrentes → règles proposées. Pour chaque brouillon de l'agent rapproché avec certitude du courriel réellement envoyé
   (mémoire des envoyés), les phrases ajoutées ou retirées par l'avocat sont comparées d'un envoi à l'autre. Une phrase ajoutée (ou
   retirée) dans au moins 3 envois au même profil de destinataire (client, confrère, greffe…) devient une règle PROPOSÉE ; elle ne
   s'applique qu'après « Adopter » (règles « toujours faire comme ça », ``rules480``). Seules des phrases de forme sont retenues :
   ni chiffre, ni adresse, ni nom propre.
2. Documents produits → part conservée. Un document de l'agent (« Produire ») est relu dans Nextcloud après au moins 2 jours ; la part
   du texte gardée par l'avocat est mesurée (tel quel, léger, réécrit), par type de document.
Aucun appel au modèle ; tout reste dans les bases locales du cabinet.
"""
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .common import Stop, fold

SCHEMA = '''
CREATE TABLE IF NOT EXISTS corrections5667(id TEXT PRIMARY KEY, kind TEXT NOT NULL, sentence TEXT NOT NULL, role TEXT NOT NULL,
  count INTEGER NOT NULL, examples INTEGER NOT NULL, last_seen TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposee',
  rule_id INTEGER, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs5667(request TEXT PRIMARY KEY, matter TEXT NOT NULL, kind TEXT NOT NULL, etag TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL, similarity REAL NOT NULL, measured TEXT NOT NULL);
'''
MIN_COUNT = 3
DAYS = 120
DOC_MIN_DAYS, DOC_MAX_DAYS, DOC_BUDGET = 2, 90, 15
ALLOWED_CAPS = {'je', 'vous', 'nous', 'il', 'elle', 'ils', 'elles', 'madame', 'monsieur', 'maitre', 'cher', 'chere', 'confrere', 'consoeur',
                'bien', 'tres', 'merci', 'bonjour', 'bonsoir', 'cordialement', 'confraternellement', 'le', 'la', 'les', 'en', 'pour', 'dans',
                'tribunal', 'cour', 'greffe', 'avec', 'sans', 'n', 'je', 'veuillez', 'pourriez', 'pouvez', 'a', 'à', 'au', 'aux'}
OUTCOMES = {'tel_quel': 'Gardés tels quels', 'leger': 'Légèrement modifiés', 'reecrit': 'Réécrits'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ phrases
def sentences(text):
    from .style480 import core
    out = []
    for raw in re.split(r'(?<=[.!?])\s+|\n+', core(str(text or ''))):
        s = re.sub(r'\s+', ' ', raw).strip(' -–•')
        if s:
            out.append(s)
    return out


def generalizable(sentence):
    """Phrase de forme réutilisable : 20 à 220 caractères, sans chiffre, adresse, crochet ni nom propre."""
    s = sentence.strip()
    if not 20 <= len(s) <= 220 or re.search(r'[\d@\[\]{}<>]|https?:|www\.', s):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ’']+", s)
    for i, w in enumerate(words):
        if i and w[:1].isupper() and fold(w).strip("'’") not in ALLOWED_CAPS:
            return False
        if len(w) >= 3 and w.isupper():
            return False
    return True


def _norm(sentence):
    return re.sub(r'\s+', ' ', fold(sentence)).strip(' .!?;:,')


def diff_sentences(draft, final):
    """(phrases ajoutées par l'avocat, phrases retirées) — une phrase seulement reformulée (≥ 80 % identique) ne compte pas."""
    a, b = sentences(draft), sentences(final)
    na, nb = [_norm(x) for x in a], [_norm(x) for x in b]

    def near(x, pool):
        return any(difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() >= 0.8 for y in pool)
    added = [b[i] for i, x in enumerate(nb) if x and not near(x, na)]
    removed = [a[i] for i, x in enumerate(na) if x and not near(x, nb)]
    return added, removed


def _ident(kind, role, norm):
    return hashlib.sha256(('%s|%s|%s' % (kind, role, norm)).encode('utf-8')).hexdigest()[:20]


def refresh_proposals(desk, days=DAYS, min_count=MIN_COUNT):
    """Recalcule les règles proposées à partir des envois rapprochés d'un brouillon (lecture seule de la mémoire des envoyés)."""
    from .learning480 import _role_of
    ensure_schema(desk)
    path = Path(desk.c['state_dir']) / 'sent-memory.sqlite3'
    if not path.is_file():
        return {'examples': 0, 'proposals': 0}
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, timeout=5)
    try:
        rows = db.execute("SELECT key,sent_at,body,draft,audience FROM examples_v2 WHERE provenance='matched_draft_and_sent' AND draft<>'' "
                          "AND sent_at>=?", (since,)).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    counts, texts, last, per_role = {}, {}, {}, {}
    for key, sent_at, body, draft, audience in rows:
        role = _role_of(audience)
        if not role:
            continue
        per_role[role] = per_role.get(role, 0) + 1
        added, removed = diff_sentences(draft, body)
        seen = set()
        for kind, group in (('ajout', added), ('retrait', removed)):
            for s in group:
                if not generalizable(s):
                    continue
                k = (kind, role, _norm(s))
                if k in seen:
                    continue
                seen.add(k)
                counts[k] = counts.get(k, 0) + 1
                texts.setdefault(k, s)
                last[k] = max(last.get(k, ''), str(sent_at))
    made = 0
    now = _now()
    for (kind, role, norm), n in counts.items():
        if n < min_count:
            continue
        ident = _ident(kind, role, norm)
        old = desk.db.execute('SELECT status FROM corrections5667 WHERE id=?', (ident,)).fetchone()
        status = old['status'] if old else 'proposee'
        desk.db.execute('INSERT OR REPLACE INTO corrections5667(id,kind,sentence,role,count,examples,last_seen,status,rule_id,updated) '
                        'VALUES(?,?,?,?,?,?,?,?,(SELECT rule_id FROM corrections5667 WHERE id=?),?)',
                        (ident, kind, texts[(kind, role, norm)][:220], role, n, per_role.get(role, 0), last[(kind, role, norm)], status, ident, now))
        made += 1
    desk.db.commit()
    return {'examples': len(rows), 'proposals': made}


def proposals(desk, status='proposee'):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM corrections5667 WHERE status=? ORDER BY count DESC,last_seen DESC LIMIT 30', (status,))]


def rule_value(row):
    sentence = row['sentence'].strip().rstrip('.')
    if row['kind'] == 'ajout':
        return ('ajouter, quand c’est pertinent, la phrase « %s »' % sentence)[:300]
    return ('éviter la phrase « %s »' % sentence)[:300]


def decide(desk, ident, action):
    """« adopter » : crée la règle (portée : profil de destinataire) ; « ecarter » : la proposition ne revient plus."""
    from . import rules480, tone480
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM corrections5667 WHERE id=?', (str(ident),)).fetchone()
    if not row:
        raise Stop('proposition_absente')
    if row['status'] != 'proposee':
        raise Stop('proposition_deja_traitee')
    if action == 'ecarter':
        desk.db.execute("UPDATE corrections5667 SET status='ecartee',updated=? WHERE id=?", (_now(), row['id']))
        desk.db.commit()
        return {'ok': True, 'message': 'Proposition écartée : elle ne sera plus proposée.'}
    if action != 'adopter':
        raise Stop('decision_invalide')
    if row['role'] not in tone480.PROFILES:
        raise Stop('profil_de_ton_inconnu')
    rule = rules480.create(desk, 'consigne', rule_value(row), 'profil', row['role'], confirm='yes', origin='correction',
                           origin_ref='corrections5667:' + row['id'], evidence=int(row['count']))
    desk.db.execute("UPDATE corrections5667 SET status='adoptee',rule_id=?,updated=? WHERE id=?", (rule['id'], _now(), row['id']))
    desk.db.commit()
    return {'ok': True, 'rule': rule, 'message': 'Règle adoptée : %s' % rule['sentence']}


# ------------------------------------------------------------------------------------------------ documents
def measure_documents(desk, client=None, budget=DOC_BUDGET, now=None):
    """Compare le texte produit par l'agent avec la version actuelle du fichier (au plus ``budget`` documents par passage)."""
    from .style480 import classify
    from .documents import extract
    ensure_schema(desk)
    now = now or datetime.now(timezone.utc)
    oldest = (now - timedelta(days=DOC_MAX_DAYS)).isoformat()
    newest = (now - timedelta(days=DOC_MIN_DAYS)).isoformat()
    try:
        rows = desk.db.execute("SELECT id,matter,kind,path,result,updated FROM docreq520 WHERE status='cree' AND updated>=? AND updated<=? "
                               "ORDER BY updated DESC LIMIT 200", (oldest, newest)).fetchall()
    except sqlite3.Error:
        return {'measured': 0}
    done = 0
    for row in rows:
        if done >= budget:
            break
        try:
            produced = (json.loads(row['result'] or '{}') or {}).get('texte_produit', '')
        except (ValueError, TypeError):
            produced = ''
        if not produced or not row['path']:
            continue
        if client is None:
            from .document_projects import _dav
            client = _dav(desk)
        try:
            meta = client.stat(row['path'])
        except (Stop, OSError):
            continue
        etag = str(meta.get('etag', ''))
        old = desk.db.execute('SELECT etag FROM docs5667 WHERE request=?', (row['id'],)).fetchone()
        if old and old['etag'] == etag:
            continue
        try:   # fichier illisible ou injoignable : mesuré à un passage suivant
            current = extract(client.download(meta), row['path'].rsplit('/', 1)[-1], desk.c.get('documents', {}))
        except (Stop, OSError):
            continue
        current = current if isinstance(current, str) else str((current or {}).get('text', ''))
        outcome, similarity = classify(produced, current)
        desk.db.execute('INSERT OR REPLACE INTO docs5667(request,matter,kind,etag,outcome,similarity,measured) VALUES(?,?,?,?,?,?,?)',
                        (row['id'], row['matter'], row['kind'], etag, outcome, float(similarity), _now()))
        done += 1
    desk.db.commit()
    return {'measured': done}


def documents_dashboard(desk):
    from .docrequest520 import KINDS
    ensure_schema(desk)
    rows = [dict(r) for r in desk.db.execute('SELECT kind,outcome,similarity FROM docs5667')]
    out = []
    for kind in sorted({r['kind'] for r in rows}):
        part = [r for r in rows if r['kind'] == kind]
        n = len(part)
        out.append({'kind': kind, 'label': KINDS.get(kind, kind), 'count': n,
                    **{k: round(100 * sum(r['outcome'] == k for r in part) / n) for k in OUTCOMES},
                    'kept_pct': round(100 * sum(r['similarity'] for r in part) / n)})
    return out


def perform(desk, kind, args):
    """Travail quotidien « apprentissage5667 » : règles proposées et mesure des documents (borné, sans appel au modèle)."""
    out = refresh_proposals(desk)
    try:
        out.update(measure_documents(desk))
    except Stop as ex:
        out['documents'] = str(ex)
    return out


# ------------------------------------------------------------------------------------------------ affichage (page Progrès)
def html(desk, prefix):
    from html import escape as e
    from .tone480 import DEFAULTS
    props = proposals(desk)
    items = []
    for p in props:
        label = DEFAULTS.get(p['role'], {}).get('label', p['role'])
        items.append('<li class="pg-rule"><div><strong>%s</strong> — « %s »<br><span class="ax-muted">%d envoi(s) sur %d au profil « %s » '
                     '(dernier : %s)</span></div><div class="vf-buttons">'
                     '<form class="m5-form q51-inline" data-api="m5667/corrections/decider" data-reload><input type="hidden" name="id" value="%s">'
                     '<input type="hidden" name="action" value="adopter"><button class="ax-btn" type="submit">Adopter</button></form>'
                     '<form class="m5-form q51-inline" data-api="m5667/corrections/decider" data-reload><input type="hidden" name="id" value="%s">'
                     '<input type="hidden" name="action" value="ecarter"><button class="ax-btn ghost" type="submit">Écarter</button></form></div></li>' % (
                         'Vous ajoutez souvent' if p['kind'] == 'ajout' else 'Vous retirez souvent', e(p['sentence']), p['count'], p['examples'],
                         e(label), e(p['last_seen'][:10]), e(p['id'], quote=True), e(p['id'], quote=True)))
    corr = ('<ul class="pg-rules">%s</ul>' % ''.join(items)) if items else (
        '<p class="ax-muted">Aucune correction récurrente pour l’instant : il faut au moins %d envois corrigés de la même façon pour un même '
        'profil de destinataire.</p>' % MIN_COUNT)
    docs = documents_dashboard(desk)
    if docs:
        rows = ''.join('<tr><td>%s</td><td>%d</td><td>%d %%</td><td>%d %%</td><td>%d %%</td><td>%d %%</td></tr>' % (
            e(d['label']), d['count'], d['tel_quel'], d['leger'], d['reecrit'], d['kept_pct']) for d in docs)
        doc_html = ('<table class="ax-table"><thead><tr><th>Type</th><th>Documents</th><th>Gardés tels quels</th><th>Légèrement modifiés</th>'
                    '<th>Réécrits</th><th>Texte conservé (moyenne)</th></tr></thead><tbody>%s</tbody></table>' % rows)
    else:
        doc_html = ('<p class="ax-muted">Pas encore de mesure : un document produit est relu dans Nextcloud au moins %d jours après sa création, '
                    'pour comparer le texte de l’agent avec votre version.</p>' % DOC_MIN_DAYS)
    return ('<section class="vf-panel" id="corrections5667"><h2>Corrections récurrentes</h2><p class="ax-muted">Phrases que vous ajoutez ou '
            'retirez régulièrement des brouillons de l’agent avant envoi. Adoptée, une proposition devient une règle « toujours faire comme ça » '
            'pour ce profil de destinataire ; rien ne s’applique sans votre accord.</p>%s</section>'
            '<section class="vf-panel" id="documents5667"><h2>Documents produits : ce que vous en gardez</h2>%s</section>') % (corr, doc_html)

"""5.6.57 : parcours de préparation construits sur les données déjà tenues par le dossier (aucun second moteur).

A04 — Préparer une audience ou un rendez-vous depuis l'agenda
    Fiche immédiate, sans appel au modèle : synthèse (procédure, parties, objectifs et consignes du cabinet), chronologie (faits
    datés et contrôlés), derniers écrits (actes et conclusions), pièces clés, points ouverts (informations manquantes, arguments
    abandonnés à ne pas reprendre). Instantané daté et versionné ; « Préparer une note » lance la mission « Préparer une audience »
    (moteur 5.6.51) avec la fiche en consigne. Une date certaine vient de l'agenda ou d'un document, jamais du modèle.

A03 — Demande de pièces guidée, puis rapprochement des documents reçus
    Besoins choisis parmi les informations manquantes du dossier (ou saisis), regroupés en UNE demande au client ; texte prêt,
    déposé en brouillon dans la messagerie sur action (droit « déposer »), destinataire = correspondant « client » du dossier (jamais
    une adresse du cabinet). Les documents arrivés dans le dossier après la demande sont rapprochés par leur nom et proposés ; le
    rapprochement est confirmé par l'avocat (états « demandée », « reçue », « rapprochée ») ; la réception ne prouve ni l'exactitude ni
    la suffisance d'une pièce.

A06 — Point d'avancement client proposé après une étape importante
    Déclencheurs vérifiables (projet produit et relu, relecture appliquée, rendez-vous ou audience passés, pièce reçue rapprochée).
    Texte « Ce qui a été fait / Ce qui change / Ce qui est attendu de vous / Prochaine étape » à partir des seuls éléments
    communicables (notes internes, coûts et stratégie exclus) ; une action PRÉPARÉE n'est jamais annoncée comme accomplie. Une
    proposition refusée ne revient pas ; fréquence réglable par dossier (normale, réduite, aucune).

A05 — Diligences vers préfacture : les temps proposés par l'agent (« time500 ») apparaissent dans la facturation avec leur source,
    le traitement au regard de la convention (inclus au forfait ou à vérifier pour facturation) et deviennent des temps validés
    sur confirmation ; une préfacture « au temps passé » se prépare ensuite depuis « À faire ». Aucune durée n'est inventée.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
from html import escape as e
import json
import re

from .common import Stop, fold, load_matters, matter_display

SCHEMA = '''
CREATE TABLE IF NOT EXISTS p57_preparations(id TEXT PRIMARY KEY, matter TEXT NOT NULL, event_id TEXT NOT NULL, event_title TEXT NOT NULL,
  event_start TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, snapshot_json TEXT NOT NULL, mission TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS p57_preparations_matter ON p57_preparations(matter, event_start);
CREATE TABLE IF NOT EXISTS p57_requests(id TEXT PRIMARY KEY, matter TEXT NOT NULL, recipient TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL,
  body TEXT NOT NULL, state TEXT NOT NULL, proof_json TEXT NOT NULL DEFAULT '{}', author TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS p57_request_items(request TEXT NOT NULL, item TEXT NOT NULL, label TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  gap TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, document TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, PRIMARY KEY(request, item));
CREATE TABLE IF NOT EXISTS p57_updates(id TEXT PRIMARY KEY, matter TEXT NOT NULL, triggers_json TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
  state TEXT NOT NULL, proof_json TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS p57_updates_matter ON p57_updates(matter, state);
'''
REQUEST_STATES = {'preparee': 'Préparée', 'deposee': 'Déposée en brouillon', 'close': 'Close'}
ITEM_STATES = {'demandee': 'Demandée', 'recue': 'Reçue — à rapprocher', 'rapprochee': 'Rapprochée', 'abandonnee': 'Abandonnée'}
UPDATE_STATES = {'proposee': 'Proposé', 'deposee': 'Déposé en brouillon', 'refusee': 'Ne pas envoyer'}
FREQUENCIES = {'normale': 'Normale (après chaque étape importante)', 'reduite': 'Réduite (au plus un point par mois)', 'aucune': 'Aucune proposition'}
STOP_WORDS = {'de', 'du', 'des', 'la', 'le', 'les', 'un', 'une', 'et', 'ou', 'a', 'au', 'aux', 'en', 'pour', 'sur', 'par', 'copie', 'piece', 'document',
              'justificatif', 'justificatifs', 'votre', 'vos', 'notre', 'nos', 'l', 'd'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _matter(desk, mid):
    from .membres5650 import viewer_hidden
    if str(mid) in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(mid)), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def _date_fr(value):
    """JJ/MM/AAAA pour une date ou un horodatage complets ; une date imprécise (« 2023-09 (précision : mois) ») est rendue telle quelle."""
    v = str(value or '')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}([T ].*)?', v):
        return '%s/%s/%s' % (v[8:10], v[5:7], v[:4])
    return v


# ============================================================================================ A04 : préparation
def _event(desk, event_id):
    try:
        row = desk.db.execute('SELECT * FROM calendar_cache WHERE id=? OR uid=? ORDER BY starts LIMIT 1', (str(event_id), str(event_id))).fetchone()
    except Exception:   # noqa: BLE001 — agenda jamais lu
        row = None
    return dict(row) if row else None


KIND_LABELS = {'audience': 'Audience', 'echeance': 'Échéance', 'rendez_vous': 'Rendez-vous'}
PREP_BUTTONS = {'audience': 'Préparer l’audience', 'echeance': 'Préparer cette échéance', 'rendez_vous': 'Préparer ce rendez-vous'}


def event_kind(title):
    t = fold(title or '')
    words = ('audience', 'plaidoirie', 'refere', 'mise en etat', 'comparution', 'delibere', 'tribunal', 'cour d', 'conseil de prud')
    if t.startswith('echeance') or 'date limite' in t or 'delai' in t:     # 5.6.58 : une échéance n'est pas un rendez-vous
        return 'echeance'
    return 'audience' if any(w in t for w in words) else 'rendez_vous'


def pack(desk, mid, question=''):
    """Fiche de préparation (instantané) à partir des données du dossier ; aucune estimation de délai par le modèle."""
    from . import contexte5651, modele5651, memoire5651
    m = _matter(desk, mid)
    ctx = contexte5651.for_drafting(desk, mid, question or 'préparation de rendez-vous')
    docs = []
    try:
        docs = modele5651.documents(desk, mid)
    except Stop:
        pass
    writings = [d for d in docs if d.get('nature') in ('conclusions', 'assignation', 'requete', 'jugement', 'ordonnance', 'arret', 'mise_en_demeure', 'courrier')][:8]
    pieces = [d for d in docs if d.get('usage_piece') not in ('inconnu', '', None, 'non')][:12] or \
        [d for d in docs if d.get('nature') in ('contrat', 'constat', 'expertise', 'attestation', 'facture', 'photographie')][:12]
    gaps = []
    try:
        gaps = memoire5651.gaps(desk, mid)
    except Stop:
        pass
    return {'dossier': matter_display(m), 'client': str(m.get('client_name') or ''), 'cree_le': _now(),
            'synthese': {'procedure': ctx.get('procedure_de_reference') or {}, 'parties': ctx.get('parties') or [],
                         'objectifs_et_consignes': ctx.get('objectifs_et_consignes') or []},
            'chronologie': (ctx.get('faits_dates') or [])[:30], 'demandes': (ctx.get('demandes_et_montants') or [])[:12],
            'derniers_ecrits': [{'nom': d['name'], 'nature': modele5651.NATURES.get(d.get('nature'), d.get('nature')), 'date': d.get('doc_date') or '',
                                 'chemin': d['path']} for d in writings],
            'pieces_cles': [{'nom': d['name'], 'numero': d.get('piece_number') or '', 'date': d.get('doc_date') or '', 'chemin': d['path']} for d in pieces],
            'points_ouverts': [{'donnee': g['expected'], 'portee': memoire5651.BLOCKING.get(g['blocking'], g['blocking'])} for g in gaps[:20]],
            'a_ne_pas_reprendre': ctx.get('arguments_abandonnes_ne_pas_reprendre') or [],
            'etat': ctx.get('etat_du_contexte') or 'à jour'}


def create_preparation(desk, owner, mid, event_id='', title='', start=''):
    """Préparation d'un événement de l'agenda (ou, sans événement, du prochain rendez-vous saisi) ; une nouvelle préparation du même
    événement crée une nouvelle VERSION de la fiche (l'historique est conservé)."""
    ensure_schema(desk)
    m = _matter(desk, mid)
    ev = _event(desk, event_id) if event_id else None
    if event_id and not ev:
        raise Stop('evenement_absent')
    if ev and ev.get('matter') and str(ev['matter']) != str(m['id']):
        raise Stop('evenement_autre_dossier')
    title = (ev or {}).get('title') or str(title or 'Rendez-vous')[:200]
    start = (ev or {}).get('starts') or str(start or '')[:40]
    key = _sha(m['id'], (ev or {}).get('id') or title + start)[:32]
    snap = pack(desk, m['id'], title)
    now = _now()
    row = desk.db.execute('SELECT version FROM p57_preparations WHERE id=?', (key,)).fetchone()
    if row:
        desk.db.execute('UPDATE p57_preparations SET version=version+1,snapshot_json=?,event_title=?,event_start=?,author=?,updated=? WHERE id=?',
                        (json.dumps(snap, ensure_ascii=False), title, start, owner, now, key))
    else:
        desk.db.execute('INSERT INTO p57_preparations(id,matter,event_id,event_title,event_start,kind,snapshot_json,author,created,updated) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?)', (key, m['id'], (ev or {}).get('id') or '', title, start, event_kind(title),
                                                       json.dumps(snap, ensure_ascii=False), owner, now, now))
    desk.db.commit()
    return preparation(desk, key)


def preparation(desk, key):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM p57_preparations WHERE id=?', (str(key),)).fetchone()
    if not row:
        raise Stop('preparation_absente')
    _matter(desk, row['matter'])
    out = dict(row)
    out['snapshot'] = json.loads(out.pop('snapshot_json'))
    return out


def preparations(desk, mid, limit=10):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT id,event_title,event_start,kind,version,mission,updated FROM p57_preparations WHERE matter=? '
                                             'ORDER BY event_start DESC LIMIT ?', (str(mid), int(limit)))]


def launch_note(desk, owner, key):
    """« Préparer une note » : mission « Préparer une audience » (moteur 5.6.51) avec la fiche en consigne ; un second clic rejoint."""
    from . import missions5651
    p = preparation(desk, key)
    s = p['snapshot']
    consigne = ('Préparer la note de %s « %s » du %s. Points ouverts : %s. Ne pas reprendre : %s.' % (
        {'audience': 'l’audience', 'echeance': 'l’échéance'}.get(p['kind'], 'rendez-vous'), p['event_title'], _date_fr(p['event_start']),
        '; '.join(x['donnee'] for x in s['points_ouverts'][:8]) or 'aucun',
        '; '.join(str(x.get('argument') or '') for x in s['a_ne_pas_reprendre'][:5]) or 'rien'))[:3000]
    m = missions5651.start(desk, owner, {'matter': p['matter'], 'objectif': 'audience' if p['kind'] == 'audience' else 'autre', 'consigne': consigne,
                                         'resultat': 'Note de préparation (Word) dans le dossier', 'click_key': 'prep57-' + key[:40]})
    desk.db.execute('UPDATE p57_preparations SET mission=?,updated=? WHERE id=?', (m['id'], _now(), key))
    desk.db.commit()
    return m


def pack_html(p, prefix=''):
    s = p['snapshot']
    proc = s['synthese'].get('procedure') or {}
    li = lambda rows: ''.join(rows) or '<li class="ax-muted">Rien d’enregistré.</li>'
    return ('<article class="p57-pack" id="prep-%s"><header><h3>%s — %s</h3><p class="ax-muted">%s · fiche v%d du %s · %s</p></header>'
            '<div class="p57-grid"><section><h4>Synthèse</h4><ul>%s%s%s</ul></section><section><h4>Chronologie</h4><ul>%s</ul></section>'
            '<section><h4>Derniers écrits</h4><ul>%s</ul></section><section><h4>Pièces clés</h4><ul>%s</ul></section>'
            '<section><h4>Points ouverts</h4><ul>%s</ul>%s</section></div></article>') % (
        e(p['id'], quote=True), e(KIND_LABELS.get(p['kind'], 'Rendez-vous')), e(p['event_title']), e(_date_fr(p['event_start'])),
        p['version'], e(_date_fr(p['updated'])), e(s.get('etat') or ''),
        ''.join('<li>%s : %s</li>' % (e(k.replace('_', ' ').capitalize()), e(str(v))) for k, v in proc.items()),
        ''.join('<li>%s%s</li>' % (e(x['nom']), ' (client)' if x.get('client_du_cabinet') else '') for x in s['synthese']['parties'][:8]),
        ''.join('<li><strong>%s</strong> %s</li>' % (e(x['type']), e(x['texte'][:300])) for x in s['synthese']['objectifs_et_consignes'][:6]),
        li('<li>%s — %s <span class="ax-muted">(%s)</span></li>' % (e(_date_fr(x.get('date', ''))), e(x['texte'][:300]), e(x.get('selon', '')))
           for x in s['chronologie'][:20]),
        li('<li>%s <span class="ax-muted">%s %s</span></li>' % (e(x['nom']), e(x['nature']), e(_date_fr(x['date']))) for x in s['derniers_ecrits']),
        li('<li>%s%s <span class="ax-muted">%s</span></li>' % (('Pièce %s — ' % e(x['numero'])) if x['numero'] else '', e(x['nom']), e(_date_fr(x['date'])))
           for x in s['pieces_cles']),
        li('<li>%s <span class="ax-muted">%s</span></li>' % (e(x['donnee']), e(x['portee'])) for x in s['points_ouverts']),
        ('<p><strong>À ne pas reprendre :</strong> %s</p>' % e('; '.join(str(x.get('argument') or '') for x in s['a_ne_pas_reprendre'][:5]))) if s['a_ne_pas_reprendre'] else '')


def upcoming_events(desk, mid=None, days=21):
    now = datetime.now(timezone.utc)
    sql = 'SELECT id,uid,title,starts,ends,matter FROM calendar_cache WHERE starts>=? AND starts<?'
    params = [now.isoformat(), (now + timedelta(days=days)).isoformat()]
    if mid:
        sql += ' AND matter=?'
        params.append(str(mid))
    try:
        return [dict(r) for r in desk.db.execute(sql + ' ORDER BY starts LIMIT 20', params)]
    except Exception:   # noqa: BLE001
        return []


# ============================================================================================ A03 : demande de pièces
def _client_address(desk, m):
    from .mailbox import own_addresses
    own = own_addresses(desk.c.get('mail', {}))
    for c in m.get('correspondents') or []:
        email = str(c.get('email') or '').strip().lower()
        if c.get('role') == 'client' and '@' in email and email not in own:
            return email
    return ''


def _letter(m, items, recipient):
    lines = '\n'.join('— %s%s' % (it['label'], (' (%s)' % it['reason']) if it.get('reason') else '') for it in items)
    subject = 'Documents à nous transmettre — %s' % matter_display(m)
    body = ('Madame, Monsieur,\n\nPour avancer dans votre dossier, nous avons besoin des éléments suivants :\n\n%s\n\n'
            'Vous pouvez nous les adresser en réponse à ce message. Si l’un d’eux n’est pas disponible, indiquez-le-nous simplement : '
            'nous poursuivons en attendant les autres travaux qui n’en dépendent pas.\n\nNous restons à votre disposition.') % lines
    return subject, body


def prepare_request(desk, owner, mid, gap_ids=(), extra=''):
    """Une demande regroupée par dossier et par ensemble de besoins (idempotente : la même sélection rejoint la demande ouverte)."""
    from . import memoire5651
    ensure_schema(desk)
    m = _matter(desk, mid)
    gaps = {g['id']: g for g in memoire5651.gaps(desk, m['id']) if g.get('kind') == 'information'}   # pas les fichiers illisibles ni les décisions
    items = []
    for gid in [str(x) for x in (gap_ids or []) if str(x) in gaps][:30]:
        items.append({'item': 'g-' + gid[:24], 'label': gaps[gid]['expected'], 'reason': gaps[gid].get('reason') or '', 'gap': gid})
    for line in [x.strip(' -—•\t') for x in str(extra or '').splitlines() if x.strip()][:20]:
        items.append({'item': 'l-' + _sha(fold(line))[:24], 'label': line[:300], 'reason': '', 'gap': ''})
    if not items:
        raise Stop('demande_pieces_vide')
    rid = 'req-' + _sha(m['id'], sorted(i['item'] for i in items))[:28]
    old = desk.db.execute('SELECT state FROM p57_requests WHERE id=?', (rid,)).fetchone()
    if old and old['state'] != 'close':
        return request(desk, rid)
    recipient = _client_address(desk, m)
    subject, body = _letter(m, items, recipient)
    now = _now()
    desk.db.execute('INSERT OR REPLACE INTO p57_requests(id,matter,recipient,subject,body,state,author,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                    (rid, m['id'], recipient, subject, body, 'preparee', owner, now, now))
    for it in items:
        desk.db.execute('INSERT OR REPLACE INTO p57_request_items(request,item,label,reason,gap,state,updated) VALUES(?,?,?,?,?,?,?)',
                        (rid, it['item'], it['label'], it['reason'], it['gap'], 'demandee', now))
    desk.db.commit()
    return request(desk, rid)


def request(desk, rid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM p57_requests WHERE id=?', (str(rid),)).fetchone()
    if not row:
        raise Stop('demande_pieces_absente')
    _matter(desk, row['matter'])
    out = dict(row)
    out['proof'] = json.loads(out.pop('proof_json') or '{}')
    out['items'] = [dict(r) for r in desk.db.execute('SELECT * FROM p57_request_items WHERE request=? ORDER BY label', (rid,))]
    return out


def requests_of(desk, mid):
    ensure_schema(desk)
    return [request(desk, r['id']) for r in desk.db.execute("SELECT id FROM p57_requests WHERE matter=? AND state<>'close' ORDER BY created DESC LIMIT 10",
                                                            (str(mid),))]


def _tokens(text):
    return {w for w in re.split(r'[^a-z0-9]+', fold(text or '')) if len(w) > 2 and w not in STOP_WORDS}


def match_received(desk, rid):
    """Documents arrivés dans le dossier depuis la demande, rapprochés par leur nom (au moins deux mots significatifs communs, ou un
    seul pour un besoin d'un mot) : état « reçue — à rapprocher » ; l'avocat confirme. Rien n'est conclu sur le contenu."""
    from . import modele5651
    r = request(desk, rid)
    try:
        docs = [d for d in modele5651.documents(desk, r['matter']) if (d.get('updated') or '') >= r['created']]
    except Stop:
        docs = []
    found = 0
    for it in r['items']:
        if it['state'] != 'demandee':
            continue
        need = _tokens(it['label'])
        if not need:
            continue
        best = None
        for d in docs:
            common = need & _tokens(d['name'])
            if len(common) >= min(2, len(need)) and (best is None or len(common) > best[0]):
                best = (len(common), d['path'])
        if best:
            desk.db.execute("UPDATE p57_request_items SET state='recue',document=?,updated=? WHERE request=? AND item=? AND state='demandee'",
                            (best[1], _now(), rid, it['item']))
            found += 1
    if found:
        desk.db.commit()
    return found


def set_item(desk, owner, rid, item, state):
    from . import memoire5651
    if state not in ('rapprochee', 'demandee', 'abandonnee'):
        raise Stop('etat_piece_invalide')
    r = request(desk, rid)
    it = next((x for x in r['items'] if x['item'] == item), None)
    if not it:
        raise Stop('piece_demandee_absente')
    desk.db.execute('UPDATE p57_request_items SET state=?,document=CASE WHEN ?=\'demandee\' THEN \'\' ELSE document END,updated=? WHERE request=? AND item=?',
                    (state, state, _now(), rid, item))
    desk.db.commit()
    if state == 'rapprochee' and it['gap']:
        try:
            memoire5651.resolve_gap(desk, r['matter'], it['gap'], owner, 'Document reçu : %s' % (it['document'] or '').rsplit('/', 1)[-1])
        except Stop:
            pass
    left = desk.db.execute("SELECT COUNT(*) FROM p57_request_items WHERE request=? AND state IN ('demandee','recue')", (rid,)).fetchone()[0]
    if not left:
        desk.db.execute("UPDATE p57_requests SET state='close',updated=? WHERE id=?", (_now(), rid))
        desk.db.commit()
    return request(desk, rid)


def _deposit(desk, ident, subject, body, to, box=None):
    """Brouillon déposé dans la messagerie et relu (jamais envoyé) ; destinataire contrôlé (jamais une adresse du cabinet)."""
    from email import policy
    from email.message import EmailMessage
    from email.utils import formataddr, format_datetime
    from . import maildraft5613, sens5643
    cfg = desk.c['mail']
    if to:
        sens5643.check_recipients(to, '', cfg)
    msg = EmailMessage(policy=policy.SMTP)
    msg['From'] = formataddr((cfg.get('from_name', 'Cabinet'), cfg['from_address']))
    if to:
        msg['To'] = to
    msg['Subject'] = subject
    msg['Date'] = format_datetime(datetime.now(timezone.utc))
    msg['Message-ID'] = '<axiorhub-5657-' + ident + '@mail-agent.local>'
    msg.set_content(body.strip() + '\n\n' + str(cfg.get('signature', '')).strip() + '\n')
    folder = cfg.get('drafts', 'Drafts')
    mailbox = box or maildraft5613._mailbox(desk)                # pylint: disable=protected-access
    try:
        if not maildraft5613.find_existing(mailbox, folder, str(msg['Message-ID'])):
            mailbox.append_draft(msg)
        return maildraft5613.verify(mailbox, folder, str(msg['Message-ID']), subject, expected=msg)
    finally:
        try:
            mailbox.close()
        except Exception:   # noqa: BLE001
            pass


def deposit_request(desk, owner, rid, box=None):
    r = request(desk, rid)
    if r['state'] == 'deposee':
        return r
    proof = _deposit(desk, 'req-' + rid[-24:], r['subject'], r['body'], r['recipient'], box)
    desk.db.execute("UPDATE p57_requests SET state='deposee',proof_json=?,updated=? WHERE id=?", (json.dumps(proof, ensure_ascii=False), _now(), rid))
    desk.db.commit()
    desk.audit('pieces5657_brouillon', {'request': rid, 'matter': r['matter'], 'par': owner})
    return request(desk, rid)


# ============================================================================================ A06 : point d'avancement client
def frequency(desk, mid):
    return (desk.settings('suivi5657:frequence', {}) or {}).get(str(mid), 'normale')


def set_frequency(desk, mid, value):
    if value not in FREQUENCIES:
        raise Stop('frequence_invalide')
    _matter(desk, mid)
    data = dict(desk.settings('suivi5657:frequence', {}) or {})
    data[str(mid)] = value
    desk.setting('suivi5657:frequence', data)
    return value


# 5.6.62 : le client lit la nature du travail, jamais un nom de fichier interne (« projet_courriel_traparic_pieces_1_7.docx »)
CLIENT_KINDS = {'conclusions': 'de conclusions', 'assignation': 'd’acte de procédure', 'courrier': 'de courrier', 'courriel': 'de courriel',
                'mise_en_demeure': 'de mise en demeure', 'contrat': 'de contrat', 'note': 'de note', 'compte_rendu': 'de compte rendu'}


def _client_fact(kind):
    return 'Nous avons préparé un projet %s.' % CLIENT_KINDS.get(str(kind or ''), 'de document')


def _trigger_source(desk, cle):
    """(nature, chemin) du document à l'origine d'une étape — affiché à l'avocat seulement."""
    try:
        if cle.startswith('doc:'):
            r = desk.db.execute('SELECT kind,path FROM docreq520 WHERE id=?', (cle[4:],)).fetchone()
            if r:
                from .cockpit530 import TYPE_LABELS
                return TYPE_LABELS.get(r['kind'], 'Document'), r['path']
        if cle.startswith('rel:'):
            r = desk.db.execute('SELECT path,output_path FROM d51_review_sessions WHERE id=?', (cle[4:],)).fetchone()
            if r:
                return 'Document revu', r['output_path'] or r['path']
    except Exception:   # noqa: BLE001 — table absente
        pass
    return '', ''


def _refresh_update(desk, m, u):
    """Point proposé avant la 5.6.62 : texte client recalculé (sans nom de fichier) ; rien n'est déposé ni envoyé."""
    if 'Nous avons préparé un projet : ' not in u['body'] and 'Nous avons revu le document ' not in u['body']:
        return u
    for t in u['triggers']:
        if t['cle'].startswith('doc:'):
            r = desk.db.execute('SELECT kind FROM docreq520 WHERE id=?', (t['cle'][4:],)).fetchone()
            t['fait'] = _client_fact(r['kind'] if r else '')
        elif t['cle'].startswith('rel:'):
            t['fait'] = 'Nous avons revu un document de votre dossier.'
    subject, body = _update_text(desk, m, u['triggers'])
    desk.db.execute('UPDATE p57_updates SET triggers_json=?,subject=?,body=?,updated=? WHERE id=?',
                    (json.dumps(u['triggers'], ensure_ascii=False), subject, body, _now(), u['id']))
    desk.db.commit()
    return update(desk, u['id'])


def _sources_html(desk, m, u):
    from .cockpit530 import _matters
    root = (_matters(desk).get(m['id']) or {}).get('path', '')
    rows = []
    for t in u['triggers']:
        label, path = _trigger_source(desk, t['cle'])
        if not path:
            continue
        where = path[len(root):].lstrip('/') if root and path.startswith(root) else path
        rows.append('<li>%s — <code>%s</code> <button type="button" class="ax-btn ghost" data-open-path="%s" data-open-matter="%s">Ouvrir dans Nextcloud'
                    '</button></li>' % (e(label), e(where), e(path, quote=True), e(m['id'], quote=True)))
    return ('<details><summary>Documents concernés (non communiqués au client)</summary><ul class="d51-list">%s</ul></details>' % ''.join(rows)) if rows else ''


def _triggers(desk, mid, since):
    """Étapes vérifiables depuis ``since`` : projets produits et relus, relectures appliquées, rendez-vous passés, pièces rapprochées."""
    out = []
    q = lambda sql, params: [dict(r) for r in desk.db.execute(sql, params)]
    try:
        for r in q("SELECT id,kind,path,updated FROM docreq520 WHERE matter=? AND status='cree' AND updated>=?", (mid, since)):
            out.append({'cle': 'doc:' + r['id'], 'quand': r['updated'], 'fait': _client_fact(r['kind']), 'nature': 'prepare'})
    except Exception:   # noqa: BLE001
        pass
    try:
        for r in q("SELECT id,path,updated FROM d51_review_sessions WHERE matter=? AND output_path<>'' AND updated>=?", (mid, since)):
            out.append({'cle': 'rel:' + r['id'], 'quand': r['updated'], 'fait': 'Nous avons revu un document de votre dossier.', 'nature': 'prepare'})
    except Exception:   # noqa: BLE001
        pass
    try:
        now = _now()
        for r in q('SELECT id,title,starts FROM calendar_cache WHERE matter=? AND starts>=? AND starts<?', (mid, since, now)):
            out.append({'cle': 'ev:' + r['id'], 'quand': r['starts'], 'fait': '%s du %s.' % (r['title'] or 'Rendez-vous', _date_fr(r['starts'])),
                        'nature': 'evenement'})
    except Exception:   # noqa: BLE001
        pass
    try:
        for r in q("SELECT i.request,i.item,i.label,i.updated FROM p57_request_items i JOIN p57_requests q ON q.id=i.request WHERE q.matter=? "
                   "AND i.state='rapprochee' AND i.updated>=?", (mid, since)):
            out.append({'cle': 'piece:' + r['request'] + ':' + r['item'], 'quand': r['updated'], 'fait': 'Nous avons bien reçu : %s.' % r['label'],
                        'nature': 'recu'})
    except Exception:   # noqa: BLE001
        pass
    return out


def _update_text(desk, m, triggers):
    from . import memoire5651
    done = '\n'.join('— %s' % t['fait'] for t in triggers[:8])
    waiting = []
    try:
        waiting = [g['expected'] for g in memoire5651.gaps(desk, m['id']) if g.get('responsible') == 'client'][:5]   # rien d'interne
    except Stop:
        pass
    nxt = upcoming_events(desk, m['id'], 60)
    body = ('Madame, Monsieur,\n\nVoici un point sur votre dossier.\n\nCe qui a été fait :\n%s\n\n' % done)
    body += ('Ce qui est attendu de vous :\n%s\n\n' % '\n'.join('— %s' % w for w in waiting)) if waiting else 'Rien n’est attendu de vous pour le moment.\n\n'
    body += ('Prochaine étape : %s le %s.\n\n' % (nxt[0]['title'] or 'rendez-vous', _date_fr(nxt[0]['starts']))) if nxt else ''
    body += 'Nous restons à votre disposition pour en parler.'
    return 'Point sur votre dossier — %s' % matter_display(m), body


def propose_updates(desk, mid=None, now=None):
    """Une proposition par dossier client et par ensemble d'étapes nouvelles ; refus = jamais reproposée ; fréquence respectée."""
    from . import facturation5655
    ensure_schema(desk)
    now = now or datetime.now(timezone.utc)
    targets = [m for m in facturation5655.client_matters(desk) if mid is None or str(m['id']) == str(mid)]
    made = []
    for m in targets:
        freq = frequency(desk, m['id'])
        if freq == 'aucune':
            continue
        last = desk.db.execute('SELECT MAX(created) FROM p57_updates WHERE matter=?', (m['id'],)).fetchone()[0]
        if freq == 'reduite' and last and last > (now - timedelta(days=30)).isoformat():
            continue
        if desk.db.execute("SELECT 1 FROM p57_updates WHERE matter=? AND state='proposee'", (m['id'],)).fetchone():
            continue                                           # une seule proposition ouverte par dossier
        since = last or (now - timedelta(days=14)).isoformat()
        triggers = [t for t in _triggers(desk, m['id'], since)]
        seen = set()
        for r in desk.db.execute('SELECT triggers_json FROM p57_updates WHERE matter=?', (m['id'],)):
            seen |= {t['cle'] for t in json.loads(r['triggers_json'])}
        triggers = [t for t in triggers if t['cle'] not in seen]
        if not triggers:
            continue
        uid = 'upd-' + _sha(m['id'], sorted(t['cle'] for t in triggers))[:28]
        subject, body = _update_text(desk, m, triggers)
        desk.db.execute('INSERT OR IGNORE INTO p57_updates(id,matter,triggers_json,subject,body,state,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                        (uid, m['id'], json.dumps(triggers, ensure_ascii=False), subject, body, 'proposee', _now(), _now()))
        made.append(uid)
    if made:
        desk.db.commit()
    return made


def update(desk, uid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM p57_updates WHERE id=?', (str(uid),)).fetchone()
    if not row:
        raise Stop('point_client_absent')
    _matter(desk, row['matter'])
    out = dict(row)
    out['triggers'] = json.loads(out.pop('triggers_json'))
    return out


def decide_update(desk, owner, uid, decision):
    u = update(desk, uid)
    if decision != 'refusee':
        raise Stop('decision_invalide')
    desk.db.execute("UPDATE p57_updates SET state='refusee',updated=? WHERE id=?", (_now(), uid))
    desk.db.commit()
    return update(desk, uid)


def deposit_update(desk, owner, uid, box=None):
    u = update(desk, uid)
    if u['state'] == 'deposee':
        return u
    m = _matter(desk, u['matter'])
    proof = _deposit(desk, 'upd-' + uid[-24:], u['subject'], u['body'], _client_address(desk, m), box)
    desk.db.execute("UPDATE p57_updates SET state='deposee',proof_json=?,updated=? WHERE id=?", (json.dumps(proof, ensure_ascii=False), _now(), uid))
    desk.db.commit()
    return update(desk, uid)


def queue_items(desk, prefix='', actor='cabinet', role='administrateur'):
    """Points d'avancement proposés et pièces reçues à rapprocher : dans la file unique (« À relire »)."""
    from urllib.parse import urlencode
    ensure_schema(desk)
    from .membres5650 import viewer_hidden
    hidden = viewer_hidden() or frozenset()
    names = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    out = []
    for r in desk.db.execute("SELECT id,matter,subject,updated FROM p57_updates WHERE state='proposee'"):
        if r['matter'] in hidden or r['matter'] not in names:
            continue
        out.append({'class': 'projet', 'object': 'suivi:' + r['id'], 'revision': r['updated'], 'matter': r['matter'], 'matter_name': names[r['matter']],
                    'title': 'Étape accomplie : point d’avancement client proposé', 'consequence': 'Relisez le texte, déposez-le en brouillon ou choisissez « Ne pas envoyer ».',
                    'href': prefix + '/dossier?' + urlencode({'id': r['matter'], 'vue': 'travaux'}) + '#p57-suivi', 'choices': ('ouvrir', 'reporter')})
    for r in desk.db.execute("SELECT q.matter,q.id,COUNT(*) n,MAX(i.updated) u FROM p57_request_items i JOIN p57_requests q ON q.id=i.request "
                             "WHERE i.state='recue' GROUP BY q.id"):
        if r['matter'] in hidden or r['matter'] not in names:
            continue
        out.append({'class': 'projet', 'object': 'pieces:' + r['id'], 'revision': r['u'], 'matter': r['matter'], 'matter_name': names[r['matter']],
                    'title': '%d document(s) reçu(s) à rapprocher de votre demande de pièces' % r['n'],
                    'consequence': 'La réception ne prouve pas que la pièce est exacte ou suffisante : confirmez le rapprochement.',
                    'href': prefix + '/dossier?' + urlencode({'id': r['matter'], 'vue': 'travaux'}) + '#p57-pieces', 'choices': ('ouvrir', 'reporter')})
    return out


# ============================================================================================ A05 : diligences
def diligences(desk, mid):
    """Temps proposés (non validés) du dossier, avec leur source et leur traitement au regard de la convention."""
    from . import time500, facturation5653 as fact
    try:
        rows = time500.pending(desk, mid, 50)
    except Exception:   # noqa: BLE001
        return []
    conv = None
    try:
        conv = fact._active_convention(desk, mid)               # pylint: disable=protected-access
    except Exception:   # noqa: BLE001
        pass
    treat = ('Inclus au forfait (suivi du budget, non facturé en plus)' if conv and conv.get('subtype') == 'forfait' else
             'À vérifier pour facturation' if conv else 'À vérifier pour facturation (aucune convention enregistrée)')
    return [{'id': r['id'], 'jour': r['day'], 'minutes': r['minutes_est'], 'explication': r['explanation'], 'traitement': treat,
             'sources': [i['label'] for i in r['items'][:6]], 'estimation': True} for r in rows]


def estimate_daily(desk, stamp=None):
    """Une fois par jour : propositions de temps des dossiers clients à partir des événements vérifiables (courriels, pièces,
    livrables, rendez-vous) ; rien n'est enregistré comme temps sans confirmation."""
    from . import time500, facturation5655
    stamp = stamp or datetime.now(timezone.utc).timestamp()
    if stamp - float(desk.settings('diligences5657:last', 0) or 0) < 86400:
        return 0
    desk.setting('diligences5657:last', stamp)
    n = 0
    for m in facturation5655.client_matters(desk):
        try:
            n += time500.estimate(desk, m['id'])['created']
        except Exception:   # noqa: BLE001 — un dossier illisible n'arrête pas les autres
            continue
    return n


# ============================================================================================ blocs de la page du dossier
def matter_html(desk, prefix, mid, owner='cabinet'):
    ensure_schema(desk)
    try:
        m = _matter(desk, mid)
    except Stop:
        return ''
    from . import memoire5651
    hidden_mid = '<input type="hidden" name="matter" value="%s">' % e(m['id'], quote=True)
    # A04
    events = upcoming_events(desk, m['id'])
    preps = preparations(desk, m['id'])
    ev_rows = ''.join('<li>%s — %s <form class="m5-form d51-inline" data-api="m5657/preparation/creer" data-reload>%s<input type="hidden" name="evenement" value="%s">'
                      '<button class="ax-btn ghost" type="submit">%s</button></form></li>' % (
                          e(_date_fr(ev['starts'])), e(ev['title'] or 'Événement'), hidden_mid, e(ev['id'], quote=True),
                          PREP_BUTTONS[event_kind(ev['title'])]) for ev in events[:6])
    prep_rows = ''
    for p in preps[:3]:
        full = preparation(desk, p['id'])
        prep_rows += pack_html(full, prefix) + (
            '<form class="m5-form d51-inline" data-api="m5657/preparation/note" data-reload><input type="hidden" name="preparation" value="%s">'
            '<button class="ax-btn" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
                e(p['id'], quote=True), 'Note en cours (mission)' if p['mission'] else 'Préparer une note de %s' % (
                    {'audience': 'plaidoirie', 'echeance': 'préparation'}.get(p['kind'], 'rendez-vous'))))
    a04 = ('<section class="d51-card" id="p57-preparation"><h3>Préparation d’audience ou de rendez-vous</h3><p class="ax-muted">Fiche immédiate tirée du '
           'dossier (synthèse, chronologie, derniers écrits, pièces clés, points ouverts) ; la note de préparation est une mission relue avant usage.</p>'
           '<ul class="d51-list">%s</ul>%s</section>') % (ev_rows or '<li class="ax-muted">Aucun événement de ce dossier dans les trois prochaines semaines.</li>',
                                                          prep_rows)
    # A03
    gaps = []
    try:
        gaps = memoire5651.gaps(desk, m['id'])
    except Stop:
        pass
    reqs = requests_of(desk, m['id'])
    for r in reqs:
        match_received(desk, r['id'])
    reqs = requests_of(desk, m['id'])
    gap_boxes = ''.join('<label class="r52-day"><input type="checkbox" name="lacunes" value="%s"> %s</label>' % (e(g['id'], quote=True), e(g['expected']))
                        for g in gaps if g.get('kind') == 'information')
    req_rows = ''
    for r in reqs:
        items = ''
        for it in r['items']:
            act = ''
            if it['state'] == 'recue':
                act = ('<form class="m5-form d51-inline" data-api="m5657/pieces/rapprocher" data-reload><input type="hidden" name="demande" value="%s">'
                       '<input type="hidden" name="piece" value="%s"><input type="hidden" name="etat" value="rapprochee">'
                       '<button class="ax-btn" type="submit">Confirmer : %s</button></form>') % (e(r['id'], quote=True), e(it['item'], quote=True),
                                                                                                 e(it['document'].rsplit('/', 1)[-1]))
            items += '<li>%s <span class="d51-badge">%s</span> %s</li>' % (e(it['label']), e(ITEM_STATES[it['state']]), act)
        deposit = ('<form class="m5-form d51-inline" data-api="m5657/pieces/deposer" data-reload><input type="hidden" name="demande" value="%s">'
                   '<button class="ax-btn" type="submit">Déposer la demande en brouillon</button><p role="status" class="m5-status"></p></form>' % e(r['id'], quote=True)
                   if r['state'] == 'preparee' else '')
        req_rows += ('<article class="p57-req"><p><strong>%s</strong> — %s · destinataire : %s</p><ul>%s</ul><details><summary>Texte de la demande</summary>'
                     '<pre class="p57-text">%s</pre></details>%s</article>') % (e(r['subject']), e(REQUEST_STATES[r['state']]),
                                                                              e(r['recipient'] or 'à renseigner (aucun correspondant « client » enregistré)'), items,
                                                                              e(r['body']), deposit)
    a03 = ('<section class="d51-card" id="p57-pieces"><h3>Pièces à demander au client</h3>%s<form class="m5-form" data-api="m5657/pieces/preparer" data-reload>%s%s'
           '<label>Autres pièces (une par ligne)<textarea name="autres" rows="2" maxlength="3000"></textarea></label>'
           '<button class="ax-btn" type="submit">Préparer une demande regroupée</button><p role="status" class="m5-status"></p></form></section>') % (
        req_rows, hidden_mid, gap_boxes or '<p class="ax-muted">Aucune information manquante enregistrée : saisissez les pièces à demander.</p>')
    # A06
    ups = [_refresh_update(desk, m, update(desk, r['id'])) for r in desk.db.execute(
        "SELECT id FROM p57_updates WHERE matter=? AND state='proposee' ORDER BY created DESC LIMIT 2", (m['id'],)).fetchall()]
    up_rows = ''.join(('<article class="p57-up"><p><strong>%s</strong></p><pre class="p57-text">%s</pre>' + _sources_html(desk, m, u) +
                       '<form class="m5-form d51-inline" data-api="m5657/suivi/deposer" '
                       'data-reload><input type="hidden" name="point" value="%s"><button class="ax-btn" type="submit">Déposer en brouillon</button></form>'
                       '<form class="m5-form d51-inline" data-api="m5657/suivi/decider" data-reload><input type="hidden" name="point" value="%s">'
                       '<input type="hidden" name="decision" value="refusee"><button class="ax-btn ghost" type="submit">Ne pas envoyer</button></form></article>') % (
        e(u['subject']), e(u['body']), e(u['id'], quote=True), e(u['id'], quote=True)) for u in ups)   # noqa: E501
    freq = frequency(desk, m['id'])
    a06 = ('<section class="d51-card" id="p57-suivi"><h3>Point d’avancement client</h3><p class="ax-muted">Proposé après une étape vérifiable ; une action seulement '
           'préparée n’est jamais annoncée comme accomplie. Rien n’est envoyé.</p>%s<form class="m5-form d51-inline" data-api="m5657/suivi/preparer" data-reload>%s'
           '<label>Fréquence<select name="frequence">%s</select></label><button class="ax-btn ghost" type="submit">Enregistrer et vérifier</button>'
           '<p role="status" class="m5-status"></p></form></section>') % (
        up_rows or '<p class="ax-muted">Aucun point proposé pour l’instant.</p>', hidden_mid,
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == freq else '', e(v)) for k, v in FREQUENCIES.items()))
    # A05
    dil = diligences(desk, m['id'])
    dil_rows = ''.join(('<li>%s · %d min — %s <span class="d51-badge">%s</span><br><span class="ax-muted">Sources : %s · estimation (non mesurée)</span>'
                        '<form class="m5-form d51-inline" data-api="m5657/diligence/confirmer" data-reload><input type="hidden" name="proposition" value="%s">'
                        '<label>Durée (min)<input name="minutes" value="%d" size="4" inputmode="numeric"></label><button class="ax-btn" type="submit">Confirmer</button></form>'
                        '<form class="m5-form d51-inline" data-api="m5657/diligence/ecarter" data-reload><input type="hidden" name="proposition" value="%s">'
                        '<button class="ax-btn ghost" type="submit">Écarter</button></form></li>') % (
        e(_date_fr(d['jour'])), d['minutes'], e(d['explication']), e(d['traitement']), e(', '.join(d['sources']) or '—'), e(d['id'], quote=True),
        d['minutes'], e(d['id'], quote=True)) for d in dil[:12])
    a05 = ('<section class="d51-card" id="p57-diligences"><h3>Travail accompli — diligences proposées</h3><p class="ax-muted">Estimations tirées des événements du '
           'dossier ; un temps n’est facturable qu’après votre confirmation. Préfacture : Facturation › À faire.</p><ul class="d51-list">%s</ul></section>') % (
        dil_rows or '<li class="ax-muted">Aucune diligence proposée.</li>')
    return a04 + a03 + a06 + a05


def post(desk, owner, role, route, data, prefix=''):
    if route == 'preparation/creer':
        p = create_preparation(desk, owner, str(data.get('matter') or ''), str(data.get('evenement') or ''), str(data.get('titre') or ''),
                               str(data.get('debut') or ''))
        return {'preparation': p['id'], 'version': p['version'], 'message': 'Fiche de préparation prête (version %d).' % p['version']}
    if route == 'preparation/note':
        m = launch_note(desk, owner, str(data.get('preparation') or ''))
        return {'mission': m['id'], 'message': 'Note de préparation lancée (mission) : elle arrivera dans « À relire ».' if not m.get('rejointe')
                else 'Note de préparation déjà en cours.'}
    if route == 'pieces/preparer':
        ids = data.get('lacunes') or []
        if isinstance(ids, str):
            ids = [ids]
        r = prepare_request(desk, owner, str(data.get('matter') or ''), ids, str(data.get('autres') or ''))
        return {'demande': r['id'], 'message': 'Demande regroupée prête (%d pièce(s)) : relisez-la puis déposez-la en brouillon.' % len(r['items'])}
    if route == 'pieces/deposer':
        r = deposit_request(desk, owner, str(data.get('demande') or ''))
        return {'demande': r['id'], 'message': 'Demande déposée en brouillon et relue ; rien n’est envoyé.'}
    if route in ('pieces/rapprocher', 'pieces/statut'):
        r = set_item(desk, owner, str(data.get('demande') or ''), str(data.get('piece') or ''), str(data.get('etat') or 'rapprochee'))
        return {'demande': r['id'], 'message': 'Rapprochement enregistré.'}
    if route == 'suivi/preparer':
        mid = str(data.get('matter') or '')
        if data.get('frequence'):
            set_frequency(desk, mid, str(data['frequence']))
        made = propose_updates(desk, mid)
        return {'propositions': made, 'message': 'Point proposé.' if made else 'Réglage enregistré ; aucune nouvelle étape à annoncer.'}
    if route == 'suivi/decider':
        decide_update(desk, owner, str(data.get('point') or ''), str(data.get('decision') or ''))
        return {'message': 'Point non envoyé ; il ne sera pas reproposé.'}
    if route == 'suivi/deposer':
        deposit_update(desk, owner, str(data.get('point') or ''))
        return {'message': 'Point déposé en brouillon et relu ; rien n’est envoyé.'}
    if route == 'diligence/confirmer':
        from . import time500
        pid = str(data.get('proposition') or '')
        row = desk.db.execute('SELECT matter FROM time500_proposals WHERE id=?', (pid,)).fetchone()
        if not row:
            raise Stop('proposition_absente_ou_traitee')
        _matter(desk, row['matter'])
        out = time500.validate(desk, pid, data.get('minutes'), str(data.get('libelle') or 'Diligences (proposition confirmée)'))
        return {**out, 'message': 'Temps confirmé (%d min) : il peut être facturé.' % out['minutes']}
    if route == 'diligence/ecarter':
        from . import time500
        pid = str(data.get('proposition') or '')
        row = desk.db.execute('SELECT matter FROM time500_proposals WHERE id=?', (pid,)).fetchone()
        if not row:
            raise Stop('proposition_absente_ou_traitee')
        _matter(desk, row['matter'])
        time500.reject(desk, pid, str(data.get('note') or ''))
        return {'message': 'Proposition écartée.'}
    if route in ('contacts/reglages', 'contacts/collecter', 'contacts/classer'):
        from . import crm5657
        return crm5657.post(desk, owner, role, route, data)
    raise Stop('route_inconnue')

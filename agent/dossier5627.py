"""5.6.27 : page Dossier refondue — une seule page, simple, pour comprendre le dossier et travailler avec l'agent.

- **Fiche de travail préparée par l'agent** (travail ``fiche_dossier5627``) : résumé, rappel des faits rédigé (récit, pas une liste
  de pièces), parties au litige seulement (qualité, conseil, client du cabinet ; ni magistrats, ni commissaires de justice, ni
  personnes citées), procédure (juridiction, RG, stade, prochaine étape), chronologie des événements déterminants, enjeux et points
  à vérifier. Les pièces sont choisies selon leur nature (assignation, conclusions, décisions, contrats…) ; les factures et frais
  passent après. Chaque paragraphe cite ses pièces ; une référence inconnue est retirée, jamais inventée.
- **Corrections de l'avocat** : résumé, rappel des faits et parties se corrigent sur la page ; la correction s'affiche aussitôt et
  est transmise à l'agent comme prioritaire lors de la mise à jour suivante.
- **Travailler avec l'agent** : instruction libre ou raccourcis (courrier, conclusions, note, question) dans le dossier ; les
  réponses et documents produits s'affichent sur la page.
- Onglets : Fiche, Pièces, Courriels, Travaux de l'agent ; les vues détaillées (rubriques 5.6.24, faits à valider, chronologie
  complète, mémoire, stratégie) restent accessibles par « Plus ».
"""
from datetime import datetime, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
import re
import unicodedata
from urllib.parse import urlencode

from .common import Stop, load_matters, matter_display

SCHEMA = '''CREATE TABLE IF NOT EXISTS fiche5627(
 matter TEXT NOT NULL, version INTEGER NOT NULL, data TEXT NOT NULL, sources TEXT NOT NULL, edits TEXT NOT NULL DEFAULT '{}',
 created TEXT NOT NULL, PRIMARY KEY(matter, version));'''
JOB = 'fiche_dossier5627'
QUALITES = {'demandeur': 'Demandeur', 'defendeur': 'Défendeur', 'appelant': 'Appelant', 'intime': 'Intimé', 'intervenant': 'Intervenant',
            'client': 'Client', 'cocontractant': 'Cocontractant', 'autre': 'Autre partie'}
# Nature des pièces : les actes et décisions d'abord, les pièces comptables du cabinet en dernier.
PRIORITY = ((r'assignation|citation|requete|saisine|acte introductif', 10), (r'conclusion', 9), (r'jugement|arret|ordonnance|decision|sentence', 9),
            (r'declaration d.appel|appel', 8), (r'mise en demeure|sommation|lrar', 7),
            (r'contrat|bail|statuts|protocole|convention|cgv|devis|bon de commande|accord', 7),
            (r'constat|proces.verbal|signification|expertise|rapport', 6), (r'note|plaidoirie|consultation|synthese|memo', 5),
            (r'courrier|lettre|courriel|mail|echange', 3))
LOW = r'facture|honoraire|frais|recu|justificatif|tampon|img[_ -]?\d|scan|inscription|greffe|provision'
JURISPRUDENCE = r'(^|[\s_-])(jp|jurisprudence)([\s_-]|$)|cass|cour de cassation'
KINDS = (('question', 'Question à l’agent'), ('courrier', 'Courrier'), ('courriel', 'Projet de courriel'), ('conclusions', 'Conclusions'),
         ('note', 'Note / consultation'), ('mise_en_demeure', 'Mise en demeure'), ('assignation', 'Assignation / requête'), ('autre', 'Autre document'))
SHORTCUTS = (('courrier', 'Courrier au client', 'Prépare un courrier au client faisant le point sur le dossier : situation actuelle, prochaine étape et ce que nous attendons de lui.'),
             ('note', 'Note de synthèse', 'Prépare une note de synthèse du dossier : faits, procédure, positions des parties, risques et prochaines étapes.'),
             ('conclusions', 'Projet de conclusions', 'Prépare un projet de conclusions pour notre client à partir des pièces et des écritures adverses du dossier.'),
             ('question', 'Prochaine échéance ?', 'Quelle est la prochaine échéance ou audience de ce dossier, et que reste-t-il à faire avant ?'))


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _fold(text):
    return ''.join(ch for ch in unicodedata.normalize('NFKD', str(text or '').lower()) if not unicodedata.combining(ch))


def _matter(desk, mid):
    m = next((x for x in load_matters(desk.c) if x['id'] == str(mid or '')), None)
    if not m:
        raise Stop('dossier_absent')
    return m


def latest(desk, mid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT version,data,sources,edits,created FROM fiche5627 WHERE matter=? ORDER BY version DESC LIMIT 1', (mid,)).fetchone()
    if not row:
        return None
    try:
        return {'version': row['version'], 'data': json.loads(row['data']), 'sources': json.loads(row['sources']), 'edits': json.loads(row['edits'] or '{}'),
                'created': row['created']}
    except ValueError:
        return None


def pending(desk, mid):
    try:
        row = desk.db.execute("SELECT id,status FROM jobs WHERE kind=? AND status IN ('pending','running') AND json_extract(args,'$.matter')=? ORDER BY id DESC LIMIT 1",
                              (JOB, mid)).fetchone()
        return dict(row) if row else None
    except Exception:   # pylint: disable=broad-except
        return None


def last_error(desk, mid):
    try:
        row = desk.db.execute("SELECT result,finished FROM jobs WHERE kind=? AND status='error' AND json_extract(args,'$.matter')=? ORDER BY id DESC LIMIT 1",
                              (JOB, mid)).fetchone()
        if not row:
            return None
        return {'error': (json.loads(row['result'] or '{}') or {}).get('erreur', 'erreur'), 'updated': row['finished'] or ''}
    except Exception:   # pylint: disable=broad-except
        return None


def request(desk, mid, with_index=True):
    """Demande (ou rejoint) la préparation de la fiche ; une seule demande en file par dossier."""
    _matter(desk, mid)
    current = pending(desk, mid)
    if current:
        return {'job_id': current['id'], 'deja_en_cours': True, 'message': 'La fiche du dossier est déjà en préparation.'}
    job = desk.enqueue(JOB, {'matter': mid, 'index': bool(with_index)}, priority=5)
    desk.audit('dossier5627_fiche_demandee', {'matter': mid, 'job': job})
    return {'job_id': job, 'message': 'Préparation de la fiche lancée : lecture des pièces puis rédaction par l’agent (quelques minutes).'}


# ------------------------------------------------------------------------------------------------ sources
def _iso(value):
    """Date de fichier au format AAAA-MM-JJ, que l'index l'ait gardée en ISO ou en date HTTP (« Fri, 13 Mar 2026 14:57:33 GMT »)."""
    value = str(value or '').strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', value):
        return value[:10]
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        return ''


def _doc_score(path):
    name = _fold(PurePosixPath(path).name)
    score = 1
    for pattern, weight in PRIORITY:
        if re.search(pattern, name):
            score = max(score, weight)
    if re.search(LOW, name):
        score = min(score, 0)
    if re.search(JURISPRUDENCE, name):   # décision citée d'une autre affaire : utile, mais pas un événement du dossier
        score = min(score, 2)
    return score


def select_sources(desk, m, budget=36000):
    """Extraits des pièces du dossier, les plus utiles d'abord (nature de la pièce, puis date), dans un budget de caractères."""
    from .index import DocumentIndex
    try:
        rows = DocumentIndex(desk.c['state_dir']).db.execute(
            "SELECT path,modified,substr(text,1,6000) FROM docs WHERE matter=? AND COALESCE(error,'')='' AND COALESCE(text,'')<>''", (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    docs = sorted(((_doc_score(r[0]), _iso(r[1]), r[0], r[2]) for r in rows), key=lambda x: x[1], reverse=True)   # les plus récentes d'abord…
    docs.sort(key=lambda x: -x[0])                                                                                       # …à nature égale
    out, used = [], 0
    for n, (score, modified, path, text) in enumerate(docs, 1):
        size = 5000 if score >= 9 else (3000 if score >= 5 else 1500)
        excerpt = re.sub(r'[ \t]+', ' ', str(text))[:size].strip()
        if not excerpt:
            continue
        if used + len(excerpt) > budget:
            if score >= 9 and budget - used > 1200:
                excerpt = excerpt[:budget - used]
            else:
                continue
        out.append({'id': 'P%d' % (len(out) + 1), 'path': path, 'nom': PurePosixPath(path).name, 'date_fichier': modified[:10],
                    'nature': 'jurisprudence citée' if re.search(JURISPRUDENCE, _fold(PurePosixPath(path).name)) else '', 'extrait': excerpt})
        used += len(excerpt)
        if used >= budget or len(out) >= 40:
            break
    return out


def _validated_facts(desk, mid):
    try:
        from .legal_memory import memory_records
        return [{'id': 'V%d' % n, 'type': r['record_type'], 'texte': (r['title'] + ' — ' + r['content'])[:600], 'date': r['event_date'] or ''}
                for n, r in enumerate(memory_records(desk, mid, ['pinned', 'validated'], 40), 1)]
    except Exception:   # pylint: disable=broad-except
        return []


# ------------------------------------------------------------------------------------------------ préparation
def _clean(result, valid):
    def ids(x):
        return [i for i in (x or []) if i in valid][:8]
    out = {'resume': str(result.get('resume') or '').strip()[:3000],
           'rappel_des_faits': [{'texte': str(p['texte']).strip()[:2500], 'source_ids': ids(p['source_ids'])} for p in result.get('rappel_des_faits', []) if str(p.get('texte', '')).strip()],
           'procedure': {**{k: str(result['procedure'].get(k) or '').strip()[:400] for k in ('juridiction', 'numero_rg', 'stade', 'prochaine_etape')},
                         'source_ids': ids(result['procedure'].get('source_ids'))},
           'enjeux': [str(x).strip()[:400] for x in result.get('enjeux', []) if str(x).strip()][:6],
           'points_a_verifier': [str(x).strip()[:400] for x in result.get('points_a_verifier', []) if str(x).strip()][:8],
           'limits': [str(x).strip()[:300] for x in result.get('limits', []) if str(x).strip()][:6]}
    parties, seen = [], set()
    for p in result.get('parties', []):
        name = re.sub(r'\s+', ' ', str(p.get('nom') or '')).strip()[:200]
        key = re.sub(r'\b(sa|sas|sasu|sarl|eurl|sci|snc|selarl|ste|societe|monsieur|madame|m|mme|me|maitre)\b|[^a-z0-9 ]', ' ', _fold(name))
        key = re.sub(r'\s+', ' ', key).strip()
        if not name or key in seen or re.search(r'\b(juge|president|greffi|commissaire de justice|huissier|expert judiciaire|conseiller)\b', _fold(name)):
            continue
        seen.add(key)
        parties.append({'nom': name, 'qualite': p['qualite'] if p.get('qualite') in QUALITES else 'autre', 'conseil': str(p.get('conseil') or '').strip()[:200],
                        'client_du_cabinet': bool(p.get('client_du_cabinet')), 'source_ids': ids(p.get('source_ids'))})
    out['parties'] = parties
    events = []
    for ev in result.get('chronologie', []):
        text = str(ev.get('evenement') or '').strip()[:500]
        if text:
            events.append({'date': str(ev.get('date') or '').strip()[:10], 'evenement': text, 'source_ids': ids(ev.get('source_ids'))})
    out['chronologie'] = sorted(events, key=lambda x: (x['date'] == '', x['date']))
    return out


def prepare(desk, args):
    """Travail « fiche_dossier5627 » : indexation (facultative), choix des pièces, rédaction par le modèle, enregistrement versionné."""
    mid = str(args.get('matter') or '')
    m = _matter(desk, mid)
    ensure_schema(desk)
    if args.get('index'):
        for _ in range(6):
            res = desk.perform('index', {'matter': mid}) or {}
            if not res.get('en_attente'):
                break
    from .model import Model, routed_config, validate, FICHE5627
    cfg = routed_config(desk.c, 'legal_analysis')
    limit = int(cfg.get('max_context_chars') or desk.c.get('ollama', {}).get('max_context_chars', 65000))
    previous = latest(desk, mid)
    corrections = {k: v.get('text', '') for k, v in ((previous or {}).get('edits') or {}).items() if isinstance(v, dict)}
    sources = select_sources(desk, m, budget=max(8000, int(limit * 0.6)))
    facts = _validated_facts(desk, mid)
    if not sources and not facts:
        raise Stop('aucune_piece_lisible_dans_le_dossier')
    payload = {'dossier': {'reference': m['id'], 'nom': matter_display(m), 'client_du_cabinet': m.get('client_name', '')},
               'date_du_jour': datetime.now(timezone.utc).date().isoformat(), 'corrections_de_l_avocat': corrections,
               'faits_valides_par_avocat': facts, 'pieces': [{k: s[k] for k in ('id', 'nom', 'date_fichier', 'nature', 'extrait')} for s in sources]}
    while len(json.dumps(payload, ensure_ascii=False)) > limit - 4000 and len(payload['pieces']) > 3:
        payload['pieces'].pop()
    kept = {p['id'] for p in payload['pieces']}
    sources = [s for s in sources if s['id'] in kept]
    result = Model(cfg).ask('dossier_fiche', payload)
    validate(result, FICHE5627)
    data = _clean(result, kept | {f['id'] for f in facts})
    data['pieces_lues'] = len(sources)
    version = desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM fiche5627 WHERE matter=?', (mid,)).fetchone()[0]
    stored = [{'id': s['id'], 'path': s['path'], 'nom': s['nom']} for s in sources] + [{'id': f['id'], 'path': '', 'nom': 'Fait validé par l’avocat'} for f in facts]
    desk.db.execute('INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,?,?,?,?,?)',
                    (mid, version, json.dumps(data, ensure_ascii=False), json.dumps(stored, ensure_ascii=False), '{}', desk.now()))
    desk.db.commit()
    desk.audit('dossier5627_fiche_preparee', {'matter': mid, 'version': version, 'pieces': len(sources)})
    return {'status': 'prepared', 'matter': mid, 'version': version, 'pieces': len(sources),
            'message': 'Fiche du dossier préparée (version %d, %d pièce(s) lue(s)).' % (version, len(sources))}


def save_edit(desk, mid, section, text, owner='cabinet'):
    """Correction de l'avocat sur la dernière fiche (résumé, rappel des faits, parties) : affichée aussitôt, prioritaire ensuite."""
    if section not in ('resume', 'faits', 'parties'):
        raise Stop('rubrique_inconnue')
    text = str(text or '').replace('\r\n', '\n').strip()
    if len(text) > 20000:
        raise Stop('texte_trop_long_20000_maximum')
    current = latest(desk, mid)
    if not current:
        _matter(desk, mid)
        desk.db.execute('INSERT INTO fiche5627(matter,version,data,sources,edits,created) VALUES(?,?,?,?,?,?)',
                        (mid, 1, json.dumps({'resume': '', 'rappel_des_faits': [], 'parties': [], 'procedure': {'juridiction': '', 'numero_rg': '', 'stade': '',
                                                                                                                 'prochaine_etape': '', 'source_ids': []},
                                              'chronologie': [], 'enjeux': [], 'points_a_verifier': [], 'limits': [], 'pieces_lues': 0}), '[]', '{}', desk.now()))
        current = latest(desk, mid)
    edits = dict(current['edits'])
    if text:
        edits[section] = {'text': text, 'by': owner, 'at': desk.now()}
    else:
        edits.pop(section, None)
    desk.db.execute('UPDATE fiche5627 SET edits=? WHERE matter=? AND version=?', (json.dumps(edits, ensure_ascii=False), mid, current['version']))
    desk.db.commit()
    desk.audit('dossier5627_fiche_corrigee', {'matter': mid, 'section': section})
    return {'message': 'Correction enregistrée ; l’agent en tiendra compte à la prochaine mise à jour de la fiche.'}


# ------------------------------------------------------------------------------------------------ agent
def ask_agent(desk, mid, text, kind='question'):
    """Instruction de l'avocat dans le dossier : question (réponse sourcée) ou document à préparer (dans « À relire »)."""
    _matter(desk, mid)
    text = re.sub(r'[ \t]+', ' ', str(text or '')).strip()
    if kind not in dict(KINDS):
        raise Stop('type_de_demande_invalide')
    from . import cockpit530
    if kind == 'question':
        return cockpit530.ask(desk, {'text': text, 'matter': mid})
    if not 8 <= len(text) <= 4000:
        raise Stop('instruction_vide_ou_trop_longue')
    from . import docrequest520 as dr
    out = dr.submit(desk, text, mid, kind if kind in dr.KINDS else 'auto')
    cockpit530.ensure_schema(desk)
    now = desk.now()
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)', ('user', text, mid, '', now))
    desk.db.execute('INSERT INTO cockpit530_messages(role,text,matter,ref,created) VALUES(?,?,?,?,?)',
                    ('agent', 'Compris : je prépare ce document à partir des pièces, des courriels et de l’agenda du dossier. Il arrivera dans « À relire ».',
                     mid, 'docreq:' + out['request'], now))
    desk.db.commit()
    desk.audit('dossier5627_instruction', {'matter': mid, 'kind': kind})
    return {'message': 'Instruction transmise à l’agent.', 'matter': mid, 'ref': 'docreq:' + out['request']}


def thread_html(desk, prefix, mid, limit=8):
    """Échanges avec l'agent pour ce dossier, du plus ancien au plus récent ; (html, en attente ?)."""
    from . import cockpit530
    cockpit530.ensure_schema(desk)
    rows = list(reversed(desk.db.execute('SELECT * FROM cockpit530_messages WHERE matter=? ORDER BY id DESC LIMIT ?', (mid, limit)).fetchall()))
    if not rows:
        return ('<p class="d27-muted">Demandez à l’agent un courrier, une note, des conclusions ou posez une question sur ce dossier. '
                'Il travaille sur les pièces, les courriels et l’agenda du dossier ; rien n’est envoyé sans vous.</p>'), False
    out, waiting = [], False
    for r in rows:
        if r['role'] == 'user':
            out.append('<div class="d27-msg me"><p>%s</p><small>%s</small></div>' % (e(r['text']), e(str(r['created'])[:16].replace('T', ' '))))
            continue
        follow, wait = cockpit530._follow_up(desk, prefix, r['ref']) if r['ref'] else ('', False)   # pylint: disable=protected-access
        waiting = waiting or wait
        out.append('<div class="d27-msg agent"><p>%s</p>%s</div>' % (e(r['text']), ('<div class="d27-follow">%s</div>' % follow) if follow else ''))
    return ''.join(out), waiting


# ------------------------------------------------------------------------------------------------ rendu
def _a(prefix, href, label, cls=''):
    return '<a%s href="%s">%s</a>' % ((' class="%s"' % cls) if cls else '', e(prefix + href, quote=True), e(label))


def _refs(prefix, mid, ids, sources):
    from .fiche460 import doc_href
    by = {s['id']: s for s in sources}
    out = []
    for i in ids or []:
        s = by.get(i)
        if not s:
            continue
        if s.get('path'):
            out.append('<a class="d27-ref" href="%s" title="%s">%s</a>' % (e(prefix + doc_href(s['path'], mid), quote=True), e(s['nom'], quote=True), e(_short(s['nom']))))
        else:
            out.append('<span class="d27-ref" title="%s">fait validé</span>' % e(s['nom'], quote=True))
    return ('<span class="d27-refs">%s</span>' % ''.join(out)) if out else ''


def _short(name):
    stem = re.sub(r'\.(pdf|docx?|odt|txt|eml|msg|jpe?g|png)$', '', str(name), flags=re.I)
    return stem if len(stem) <= 42 else stem[:40] + '…'


def _date_fr(value):
    value = str(value or '')
    m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})', value)
    if m:
        return '%s/%s/%s' % (m.group(3), m.group(2), m.group(1))
    m = re.fullmatch(r'(\d{4})-(\d{2})', value)
    return ('%s/%s' % (m.group(2), m.group(1))) if m else value


def _edit_block(section, label, text, rows=6):
    return ('<form class="d27-edit" data-d27-section="%s" hidden><label><span>%s</span><textarea rows="%d" maxlength="20000">%s</textarea></label>'
            '<div class="d27-actions"><button class="ax-btn" type="submit">Enregistrer ma correction</button>'
            '<button class="ax-btn ghost" type="button" data-d27-cancel>Annuler</button></div></form>') % (e(section, quote=True), e(label), rows, e(text))


def _card(title, body, extra='', cls=''):
    return '<section class="d27-card%s"><div class="d27-card-head"><h2>%s</h2>%s</div>%s</section>' % ((' ' + cls) if cls else '', e(title), extra, body)


def _edit_button(section):
    return '<button type="button" class="d27-icon" data-d27-edit="%s" title="Corriger" aria-label="Corriger">✎</button>' % e(section, quote=True)


def _counts(desk, mid):
    out = {'pieces': 0, 'courriels': 0, 'travaux': 0}
    try:
        from .index import DocumentIndex
        out['pieces'] = DocumentIndex(desk.c['state_dir']).db.execute('SELECT COUNT(*) FROM docs WHERE matter=?', (mid,)).fetchone()[0]
    except Exception:   # pylint: disable=broad-except
        pass
    try:
        out['courriels'] = desk.db.execute('SELECT COUNT(*) FROM work_items WHERE matter=?', (mid,)).fetchone()[0]
    except Exception:   # pylint: disable=broad-except
        pass
    try:
        out['travaux'] = desk.db.execute('SELECT COUNT(*) FROM docreq520 WHERE matter=?', (mid,)).fetchone()[0]
    except Exception:   # pylint: disable=broad-except
        pass
    return out


def _next_deadline(desk, mid):
    try:
        from .fiche460 import build_fiche
        return build_fiche(desk, mid).get('next_deadline')
    except Exception:   # pylint: disable=broad-except
        return None


def header_html(desk, prefix, m, view, counts):
    mid = m['id']
    q = lambda v: '/dossier?' + urlencode({'id': mid, **({'vue': v} if v != 'fiche' else {})})
    tabs = (('fiche', 'Fiche', None), ('pieces', 'Pièces', counts['pieces']), ('courriels', 'Courriels', counts['courriels']), ('travaux', 'Travaux de l’agent', counts['travaux']))
    more = ''.join('<li>%s</li>' % _a(prefix, href, label) for href, label in (
        ('/fiche?' + urlencode({'matter': mid}), 'Vue détaillée par rubriques'), ('/fiche?' + urlencode({'matter': mid, 'onglet': 'faits'}), 'Faits proposés à valider'),
        ('/chronologie?' + urlencode({'matter': mid}), 'Chronologie complète'), ('/strategy?' + urlencode({'matter': mid}), 'Stratégie'),
        ('/modeles-word?' + urlencode({'matter': mid}), 'Créer un courrier Word'), ('/documents?' + urlencode({'matter': mid}), 'Documents du dossier'),
        ('/matter?' + urlencode({'id': mid}), 'Ancienne page (surveillance, mémoire, réglages)')))
    return ('<div class="d27" data-d27-matter="%s">'
            '<nav class="d27-back">%s</nav>'
            '<header class="d27-head"><div class="d27-title"><h1>%s</h1><p class="d27-muted">Référence %s%s</p></div>'
            '<div class="d27-head-actions"><button type="button" class="ax-btn" data-d27-prepare>Mettre à jour la fiche</button>'
            '<details class="d27-more"><summary class="ax-btn ghost">Plus ▾</summary><ul>%s</ul></details></div></header>'
            '<nav class="d27-tabs" aria-label="Vues du dossier">%s</nav>') % (
        e(mid, quote=True), _a(prefix, '/dossiers', '← Tous les dossiers'), e(matter_display(m)), e(mid),
        (' · client : ' + e(m['client_name'])) if m.get('client_name') else '', more,
        ''.join('<a href="%s"%s>%s%s</a>' % (e(prefix + q(key), quote=True), ' aria-current="page"' if key == view else '', e(label),
                                             (' <span class="d27-count">%d</span>' % n) if n else '') for key, label, n in tabs))


def fiche_html(desk, prefix, m):
    mid = m['id']
    f = latest(desk, mid)
    job = pending(desk, mid)
    err = last_error(desk, mid) if not job else None
    status = ''
    if job:
        status = '<p class="d27-status" role="status" data-d27-waiting>⏳ L’agent prépare la fiche du dossier (lecture des pièces puis rédaction)… la page se mettra à jour.</p>'
    elif err and (not f or str(err.get('updated') or '') > str(f['created'])):
        from .web440 import human
        status = '<p class="d27-status bad" role="alert">La dernière préparation a échoué : %s</p>' % e(human(str(err.get('error') or 'erreur')))
    data = (f or {}).get('data') or {}
    sources = (f or {}).get('sources') or []
    edits = (f or {}).get('edits') or {}
    when = ('Préparée le %s · version %d · %d pièce(s) lue(s)' % (_date_fr(str(f['created'])[:10]), f['version'], data.get('pieces_lues', 0))) if f and data.get('pieces_lues') is not None else ''
    # --- résumé
    if edits.get('resume'):
        resume = '<p>%s</p><p class="d27-note">Corrigé par vous le %s.</p>' % (e(edits['resume']['text']).replace('\n', '<br>'), _date_fr(str(edits['resume']['at'])[:10]))
    elif data.get('resume'):
        resume = '<p>%s</p>' % e(data['resume'])
    else:
        resume = ('<div class="d27-empty"><p>La fiche de ce dossier n’a pas encore été préparée.</p><p>L’agent lit les actes (assignation, conclusions, '
                  'décisions, contrats…) et rédige un résumé, le rappel des faits, les parties et la chronologie, avec les pièces citées.</p>'
                  '<button type="button" class="ax-btn" data-d27-prepare>Préparer la fiche du dossier</button></div>')
    resume_text = (edits.get('resume') or {}).get('text') or data.get('resume', '')
    main = _card('De quoi s’agit-il ?', status + resume + _edit_block('resume', 'Résumé du dossier', resume_text, 5),
                 ('<span class="d27-muted">%s</span>' % e(when) if when else '') + _edit_button('resume'), 'd27-summary')
    # --- rappel des faits
    if edits.get('faits'):
        faits = ''.join('<p>%s</p>' % e(p) for p in re.split(r'\n\s*\n', edits['faits']['text']) if p.strip()) + '<p class="d27-note">Rappel corrigé par vous.</p>'
        faits_text = edits['faits']['text']
    elif data.get('rappel_des_faits'):
        faits = ''.join('<p>%s %s</p>' % (e(p['texte']), _refs(prefix, mid, p['source_ids'], sources)) for p in data['rappel_des_faits'])
        faits_text = '\n\n'.join(p['texte'] for p in data['rappel_des_faits'])
    else:
        faits, faits_text = '<p class="d27-muted">Le rappel des faits apparaîtra ici une fois la fiche préparée.</p>', ''
    main += _card('Rappel des faits', faits + _edit_block('faits', 'Rappel des faits (un paragraphe par bloc, séparés par une ligne vide)', faits_text, 14),
                  _edit_button('faits'))
    # --- chronologie
    events = data.get('chronologie') or []
    if events:
        chrono = '<ol class="d27-chrono">%s</ol>' % ''.join('<li><time>%s</time><span>%s %s</span></li>' % (
            e(_date_fr(ev['date']) or 'date inconnue'), e(ev['evenement']), _refs(prefix, mid, ev['source_ids'], sources)) for ev in events)
    else:
        chrono = '<p class="d27-muted">Les événements déterminants du dossier (actes, audiences, décisions) apparaîtront ici.</p>'
    main += _card('Chronologie clé', chrono, _a(prefix, '/chronologie?' + urlencode({'matter': mid}), 'Chronologie complète', 'd27-link'))
    if data.get('enjeux') or data.get('points_a_verifier'):
        body = ''
        if data.get('enjeux'):
            body += '<h3>Enjeux</h3><ul>%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in data['enjeux'])
        if data.get('points_a_verifier'):
            body += '<h3>À vérifier</h3><ul class="d27-check">%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in data['points_a_verifier'])
        main += _card('Enjeux et points à vérifier', body)
    # --- colonne de droite : agent, parties, procédure, courriels
    thread, waiting = thread_html(desk, prefix, mid)
    options = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in KINDS)
    chips = ''.join('<button type="button" class="d27-chip" data-d27-kind="%s" data-d27-text="%s">%s</button>' % (k, e(t, quote=True), e(label)) for k, label, t in SHORTCUTS)
    side = _card('Travailler avec l’agent', (
        '<div class="d27-thread" id="d27-thread" aria-live="polite"%s>%s</div>'
        '<form class="d27-ask" id="d27-ask"><div class="d27-chips">%s</div>'
        '<label class="d27-sr" for="d27-text">Votre demande</label><textarea id="d27-text" name="text" rows="3" maxlength="4000" '
        'placeholder="Exemple : prépare une réponse au confrère sur la communication des pièces 12 à 15."></textarea>'
        '<div class="d27-ask-row"><select name="kind" aria-label="Type de demande">%s</select><button class="ax-btn" type="submit">Envoyer à l’agent</button></div>'
        '<p class="d27-muted">Les documents arrivent dans « À relire » ; rien n’est envoyé sans vous.</p></form>') % (
        ' data-d27-waiting' if waiting else '', thread, chips, options), '', 'd27-agent')
    parties_edit = (edits.get('parties') or {}).get('text')
    if parties_edit:
        parties = '<ul class="d27-parties">%s</ul><p class="d27-note">Liste corrigée par vous.</p>' % ''.join('<li>%s</li>' % e(l) for l in parties_edit.splitlines() if l.strip())
        parties_text = parties_edit
    elif data.get('parties'):
        rows = []
        for p in sorted(data['parties'], key=lambda p: not p['client_du_cabinet']):
            rows.append('<li%s><strong>%s</strong>%s<br><span class="d27-muted">%s%s</span> %s</li>' % (
                ' class="d27-client"' if p['client_du_cabinet'] else '', e(p['nom']), ' <span class="d27-badge">client</span>' if p['client_du_cabinet'] else '',
                e(QUALITES.get(p['qualite'], 'Partie')), (' · conseil : ' + e(p['conseil'])) if p['conseil'] else '', _refs(prefix, mid, p['source_ids'], sources)))
        parties = '<ul class="d27-parties">%s</ul>' % ''.join(rows)
        parties_text = '\n'.join('%s — %s%s' % (p['nom'], QUALITES.get(p['qualite'], 'Partie'), (' — conseil : ' + p['conseil']) if p['conseil'] else '') for p in data['parties'])
    else:
        client = m.get('client_name') or ''
        parties = '<ul class="d27-parties"><li class="d27-client"><strong>%s</strong> <span class="d27-badge">client</span></li></ul>' % e(client) if client else '<p class="d27-muted">Parties à identifier.</p>'
        parties_text = (client + ' — Client') if client else ''
    side += _card('Parties', parties + _edit_block('parties', 'Une partie par ligne : nom — qualité — conseil', parties_text, 6), _edit_button('parties'))
    proc = data.get('procedure') or {}
    nd = _next_deadline(desk, mid)
    rows = [(label, proc.get(key)) for key, label in (('juridiction', 'Juridiction'), ('numero_rg', 'N° RG'), ('stade', 'Stade'), ('prochaine_etape', 'Prochaine étape'))]
    if nd:
        rows.append(('Prochaine échéance', '%s — %s%s' % (nd['due_label'], nd['label'], '' if nd.get('validated') else ' (à confirmer)')))
    proc_html = '<dl class="d27-dl">%s</dl>' % ''.join('<dt>%s</dt><dd>%s</dd>' % (e(k), e(v)) for k, v in rows if v) if any(v for _, v in rows) else \
        '<p class="d27-muted">Juridiction, numéro RG et stade apparaîtront ici.</p>'
    side += _card('Procédure', proc_html + _refs(prefix, mid, proc.get('source_ids'), sources))
    mails = []
    try:
        mails = desk.db.execute('SELECT subject,sender,received,state FROM work_items WHERE matter=? ORDER BY COALESCE(received,updated) DESC LIMIT 5', (mid,)).fetchall()
    except Exception:   # pylint: disable=broad-except
        pass
    side += _card('Derniers courriels', ('<ul class="d27-mails">%s</ul>' % ''.join('<li><strong>%s</strong><br><span class="d27-muted">%s · %s</span></li>' % (
        e(r['subject'] or 'sans objet'), e(str(r['sender'] or '')[:60]), e(_date_fr(str(r['received'] or '')[:10]))) for r in mails)) if mails else
        '<p class="d27-muted">Aucun courriel rattaché à ce dossier.</p>', _a(prefix, '/courriels?vue=boite', 'Messagerie', 'd27-link'))
    if data.get('limits'):
        main += '<p class="d27-muted d27-limits">Limites : %s</p>' % e(' ; '.join(data['limits']))
    return '<div class="d27-grid"><div class="d27-main">%s</div><div class="d27-side" role="complementary" aria-label="Agent, parties et procédure">%s</div></div>' % (main, side)


def pieces_html(desk, prefix, m):
    from .fiche460 import doc_href
    from .index import DocumentIndex
    try:
        rows = DocumentIndex(desk.c['state_dir']).db.execute('SELECT path,modified,error FROM docs WHERE matter=? LIMIT 2000', (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    docs = sorted(({'path': r[0], 'name': PurePosixPath(r[0]).name, 'modified': _iso(r[1]), 'error': r[2] or ''} for r in rows), key=lambda d: d['modified'], reverse=True)
    rows = ''.join('<li data-d27-search="%s"><a href="%s">%s</a><span class="d27-muted">%s</span>%s</li>' % (
        e(_fold(d['name']), quote=True), e(prefix + doc_href(d['path'], m['id']), quote=True), e(d['name']), e(_date_fr(d['modified'])),
        ' <span class="d27-badge bad">illisible</span>' if d['error'] else '') for d in docs)
    return _card('Pièces du dossier (%d)' % len(docs), (
        '<input type="search" class="d27-filter" data-d27-filter="#d27-pieces" placeholder="Filtrer par nom" aria-label="Filtrer les pièces">'
        '<ul class="d27-list" id="d27-pieces">%s</ul>') % (rows or '<li class="d27-muted">Aucune pièce indexée : cliquez sur « Mettre à jour la fiche ».</li>'),
        _a(prefix, '/documents?' + urlencode({'matter': m['id']}), 'Gérer les documents', 'd27-link'))


STATE_LABELS = {'draft_ready': 'Brouillon prêt à relire', 'done': 'Traité', 'ignored': 'Ignoré', 'needs_decision': 'Décision à prendre', 'error': 'Erreur',
                'new': 'Nouveau', 'resolved': 'Traité'}


def courriels_html(desk, prefix, m):
    try:
        rows = desk.db.execute('SELECT subject,sender,received,state FROM work_items WHERE matter=? ORDER BY COALESCE(received,updated) DESC LIMIT 200', (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    body = ''.join('<li><strong>%s</strong><span class="d27-muted">%s · %s · %s</span></li>' % (
        e(r['subject'] or 'sans objet'), e(str(r['sender'] or '')[:80]), e(_date_fr(str(r['received'] or '')[:10])), e(STATE_LABELS.get(r['state'], r['state'] or ''))) for r in rows)
    return _card('Courriels rattachés (%d)' % len(rows), '<ul class="d27-list">%s</ul>' % (body or '<li class="d27-muted">Aucun courriel rattaché à ce dossier.</li>'),
                 _a(prefix, '/courriels?vue=boite', 'Ouvrir la messagerie', 'd27-link'))


DOC_STATUS = {'cree': 'Prêt à relire', 'en_cours': 'En rédaction', 'en_attente': 'En file', 'echec': 'Échec', 'annule': 'Arrêté', 'dossier_a_choisir': 'Dossier à préciser'}


def travaux_html(desk, prefix, m):
    from .fiche460 import doc_href
    try:
        rows = desk.db.execute('SELECT request,kind,status,path,updated FROM docreq520 WHERE matter=? ORDER BY updated DESC LIMIT 100', (m['id'],)).fetchall()
    except Exception:   # pylint: disable=broad-except
        rows = []
    body = ''.join('<li><strong>%s</strong><span class="d27-muted">%s · %s</span>%s</li>' % (
        e(r['request'][:180]), e(DOC_STATUS.get(r['status'], r['status'])), e(_date_fr(str(r['updated'])[:10])),
        (' ' + '<a href="%s">%s</a>' % (e(prefix + doc_href(r['path'], m['id']), quote=True), e(PurePosixPath(r['path']).name))) if r['path'] else '') for r in rows)
    thread, waiting = thread_html(desk, prefix, m['id'], 30)
    return (_card('Documents préparés par l’agent', '<ul class="d27-list">%s</ul>' % (body or '<li class="d27-muted">Aucun document demandé pour ce dossier.</li>'))
            + _card('Échanges avec l’agent', '<div class="d27-thread" id="d27-thread"%s>%s</div>' % (' data-d27-waiting' if waiting else '', thread)))


def page(desk, auth, prefix, args, shell):
    mid = str(args.get('id') or args.get('matter') or '')
    m = _matter(desk, mid)
    view = str(args.get('vue') or 'fiche')
    if view not in ('fiche', 'pieces', 'courriels', 'travaux'):
        view = 'fiche'
    counts = _counts(desk, mid)
    body = header_html(desk, prefix, m, view, counts)
    body += {'fiche': fiche_html, 'pieces': pieces_html, 'courriels': courriels_html, 'travaux': travaux_html}[view](desk, prefix, m)
    body += '</div>'
    head = '<script defer src="%s/static/v5627.js"></script>' % e(prefix)
    return shell(matter_display(m), body, prefix, auth['csrf'], '/dossiers', head)


# ------------------------------------------------------------------------------------------------ API
def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5627/'):].split('?', 1)[0]
    if role not in ('administrateur', 'avocat'):
        raise Stop('role_insuffisant')
    if method == 'GET':
        mid = str(args.get('matter') or '')
        if route == 'fil':
            html, waiting = thread_html(desk, prefix, _matter(desk, mid)['id'], int(args.get('n') or 8) if str(args.get('n') or '8').isdigit() else 8)
            return json_out({'html': html, 'waiting': waiting})
        if route == 'etat':
            f = latest(desk, _matter(desk, mid)['id'])
            return json_out({'pending': bool(pending(desk, mid)), 'version': f['version'] if f else 0})
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    mid = str(data.get('matter') or '')
    if route == 'fiche/preparer':
        return json_out(request(desk, mid))
    if route == 'fiche/corriger':
        return json_out(save_edit(desk, _matter(desk, mid)['id'], str(data.get('section') or ''), data.get('text', ''), owner))
    if route == 'agent':
        return json_out(ask_agent(desk, mid, data.get('text', ''), str(data.get('kind') or 'question')))
    raise Stop('route_inconnue')

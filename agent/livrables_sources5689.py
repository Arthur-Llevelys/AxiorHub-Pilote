"""5.6.89 : adaptateurs du registre des livrables : chaque producteur existant, lu dans son propre journal (audit 5.6.88, § 5, tableau « Raccorder tous les producteurs »).

Un adaptateur ne lit que des tables locales : aucun appel réseau, aucun fichier créé. Il décrit chaque sortie connue ; le registre décide si elle est nouvelle.
``rows`` rend des couples (identifiant de ligne, lecture différée) pour que chaque ligne ait son propre ``try`` (B01) : une ligne abîmée n'emporte ni les suivantes, ni les autres sources.
``signature`` est une lecture très légère : si elle n'a pas changé, la source n'est pas relue. Les états de ces sources sont ceux de ``livrables5689.etat_sources``.

Ce qui est repris d'un journal n'est que ce qu'il prouve : ``verified`` ne vaut que s'il mesurait une relecture du fichier, et il ne vaut alors que preuve de stockage (§ 40).
"""
from datetime import datetime, timezone
from functools import partial
import json
from pathlib import PurePosixPath
import sqlite3
import time

from .common import Stop, digest
from .livrables5689 import RANG, _now, nature

LIMITE = 2000
TERMINE = ('done', 'partial')


def _json(value, default=None):
    """Objet JSON d'une colonne ; une valeur abîmée est une erreur de la ligne, pas de la source."""
    data = json.loads(value) if value else ({} if default is None else default)
    if not isinstance(data, (dict, list)):
        raise ValueError('json_inattendu')
    return data


def _col(row, name, default=''):
    return row[name] if name in row.keys() else default


def _rang(path):
    return RANG.get(PurePosixPath(path).suffix.lower(), 8)


def _fichiers(files):
    """Fichiers d'un même projet : le plus utile d'abord (Word, puis PDF…), les autres sur la même carte."""
    rows = [{'path': str(f.get('path') or ''), 'sha256': str(f.get('sha256') or ''), 'size': int(f.get('bytes') or f.get('size') or 0)} for f in files if isinstance(f, dict) and f.get('path')]
    return sorted(rows, key=lambda f: _rang(f['path']))


def _signature(table, column='updated'):
    def read(desk):
        return tuple(desk.db.execute('SELECT COUNT(*), COALESCE(MAX(%s),\'\') FROM %s' % (column, table)).fetchone())
    return read


def _document_lie(desk, service, objet):
    from .livrables5689 import ensure_schema
    ensure_schema(desk)
    row = desk.db.execute("SELECT i.document_id FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service=? AND l.source_object_id=? LIMIT 1", (service, str(objet))).fetchone()
    return row[0] if row else ''


# ------------------------------------------------------------------------------------------------ demandes de documents
def _docreq(desk, r):
    res = _json(r['result'])
    sha = str(res.get('sha256') or '')
    to_complete = [str(x) for x in (res.get('to_complete') or [])]
    template = res.get('template') or {}
    legal = res.get('recherche_juridique') or {}
    path = r['path']
    x = {'service': 'docrequest520', 'object': r['id'], 'version': sha or r['updated'], 'matter': r['matter'], 'kind': r['kind'], 'title': res.get('title') or PurePosixPath(path).stem, 'path': path, 'sha256': sha,
         'verified': bool(sha) and res.get('readback_sha256') == sha, 'evidence': 'relecture au dépôt (docrequest520)', 'when': r['updated'], 'reserve': bool(to_complete),
         'reserve_note': ('À compléter : ' + ' ; '.join(to_complete[:6])) if to_complete else '', 'label': _labels_doc(r['kind']),
         'provenance': {'producteur': 'docrequest520', 'demande': r['id'], 'modele_id': template.get('id', ''), 'modele': template.get('label', ''), 'modele_sha256': template.get('sha256', ''),
                        'references_a_verifier': legal.get('references_a_verifier') or [], 'sources_juridiques': [{k: s.get(k, '') for k in ('id', 'statut', 'titre', 'reference', 'date')} for s in (legal.get('sources') or [])[:40] if isinstance(s, dict)]},
         'sources': [str(s) for s in (res.get('sources') or [])[:30]]}
    parent = _col(r, 'revision_of')
    if parent:      # une demande de correction prolonge le document de départ : nouvelle version, pas nouveau document
        x['document_id'] = _document_lie(desk, 'docrequest520', parent)
    return [x]


def _labels_doc(kind):
    from .cockpit530 import TYPE_LABELS
    return TYPE_LABELS.get(kind, 'Document')


def docreq_rows(desk):
    for r in desk.db.execute("SELECT * FROM docreq520 WHERE status='cree' AND path<>'' ORDER BY updated ASC LIMIT ?", (LIMITE,)).fetchall():
        yield r['id'], partial(_docreq, desk, r)


def docreq_running(desk):
    return desk.db.execute("SELECT COUNT(*) FROM docreq520 WHERE status IN ('en_file','en_cours','depot_en_cours')").fetchone()[0]


# ------------------------------------------------------------------------------------------------ projets Word
def _projet(service, statuses_row, label):
    r = statuses_row
    result = _json(r['result'])
    data = _json(r['data'])
    if r['status'] in TERMINE:
        files = _fichiers(result.get('created_files') or [])
        if not files:
            return []
        first, rest = files[0], files[1:]
        return [{'service': service, 'object': r['id'], 'version': first['sha256'] or r['decided'] or r['created'], 'matter': r['matter'], 'kind': _col(r, 'document_type') or 'conclusions', 'path': first['path'], 'sha256': first['sha256'], 'size': first['size'],
                 'state': 'partial' if r['status'] == 'partial' else 'published', 'reason': 'fichiers_manquants' if r['status'] == 'partial' else '', 'when': r['decided'] or r['created'], 'representations': rest,
                 'provenance': {'producteur': service, 'projet': r['id']}, 'title': label(data, first['path'])}]
    return [{'service': service, 'object': r['id'], 'version': 'projet', 'matter': r['matter'], 'kind': _col(r, 'document_type') or 'conclusions', 'state': 'preparing', 'when': r['created'], 'title': label(data, ''),
             'provenance': {'producteur': service, 'projet': r['id'], 'apercu': service}}]


def _titre_projet(data, path):
    return str(data.get('title') or data.get('titre') or (PurePosixPath(path).stem if path else '') or 'Projet de document')


def _project_rows(desk, table, service):
    for r in desk.db.execute("SELECT * FROM %s WHERE status IN ('done','partial','pending','creating') ORDER BY created ASC LIMIT ?" % table, (LIMITE,)).fetchall():
        yield r['id'], partial(_projet, service, r, _titre_projet)


def _project_signature(table):
    def read(desk):
        return tuple(desk.db.execute("SELECT COUNT(*), COALESCE(MAX(decided),''), SUM(status IN ('done','partial')) FROM %s" % table).fetchone())
    return read


def _project_running(table):
    def read(desk):
        return desk.db.execute("SELECT COUNT(*) FROM %s WHERE status IN ('pending','creating') AND expires>=?" % table, (datetime.now(timezone.utc).isoformat(),)).fetchone()[0]
    return read


# ------------------------------------------------------------------------------------------------ cessions, opérations et formulaires
def _cession(row):
    inputs = _json(row['inputs']) if str(row['inputs']).startswith('{') else {}
    reserve = bool(inputs.get('reserve'))
    formulaire = bool(inputs.get('formulaire'))
    return [{'service': 'cessions5684', 'object': '%s:%s' % (row['dossier'], row['doc']), 'version': row['version'], 'matter': row['matter'] or '', 'kind': 'formulaire' if formulaire else nature('', row['doc'].replace('_', ' '), row['path']), 'path': row['path'],
             'title': PurePosixPath(row['path']).stem, 'when': row['at'], 'state': 'published', 'reserve': reserve, 'reserve_note': 'Document préparé avec réserves : données ou mentions à compléter, ou modèle non validé.' if reserve else '',
             'protected': 'signe' if row['state'] == 'signe' else '', 'provenance': {'producteur': 'cessions5684', 'operation': row['dossier'], 'modele_id': inputs.get('modele', ''), 'modele_version': inputs.get('version', ''), 'etat_modele': inputs.get('etat_modele', ''), 'formulaire': inputs.get('formulaire', '')}}]


def cession_rows(desk):
    for r in desk.db.execute('SELECT d.*, o.matter AS matter FROM deliverables5683 d LEFT JOIN operation_dossiers5683 o ON o.id=d.dossier ORDER BY d.at ASC LIMIT ?', (LIMITE,)).fetchall():
        yield r['id'], partial(_cession, r)


def _corporate(desk, row):
    from .office440 import matter_of
    out = []
    for path in _json(row['result']).get('files') or []:
        out.append({'service': 'corporate5636', 'object': '%s:%s' % (row['id'], digest(str(path))[:16]), 'version': '1', 'matter': matter_of(desk, str(path)), 'path': str(path), 'title': PurePosixPath(str(path)).stem, 'state': 'published', 'when': row['updated'],
                    'provenance': {'producteur': 'corporate5636', 'operation': row['id'], 'nature_operation': row['kind']}})
    return out


def corporate_rows(desk):
    for r in desk.db.execute("SELECT * FROM operations5636 WHERE result LIKE '%\"files\"%' ORDER BY updated ASC LIMIT ?", (LIMITE,)).fetchall():
        yield r['id'], partial(_corporate, desk, r)


# ------------------------------------------------------------------------------------------------ courriers, notes, honoraires
def _courrier(row):
    return [{'service': 'courrier5679', 'object': row['id'], 'version': '1', 'matter': row['matter'], 'kind': 'courrier', 'label': 'Courrier', 'path': row['path'], 'title': PurePosixPath(row['path']).stem, 'state': 'published', 'when': row['updated'],
             'provenance': {'producteur': 'courrier5679'}}]


def courrier_rows(desk):
    for r in desk.db.execute("SELECT * FROM courriers5679 WHERE state='pret' AND path<>'' ORDER BY updated ASC LIMIT ?", (LIMITE,)).fetchall():
        yield r['id'], partial(_courrier, r)


def _note(desk, row):
    """Note rangée : le titre du marqueur porte « sujet — chemin ». Le brouillon a quitté la messagerie, pas le suivi."""
    from .office440 import matter_of
    subject, _, path = str(row['title']).rpartition(' — ')
    if not path.startswith('/') or not path.lower().endswith('.docx'):
        return []    # titre tronqué à la sauvegarde : le chemin n'est pas fiable, on n'invente rien
    return [{'service': 'notes5666', 'object': row['item'], 'version': '1', 'matter': matter_of(desk, path), 'kind': 'note', 'label': 'Note', 'path': path, 'title': subject or PurePosixPath(path).stem, 'state': 'published', 'when': row['at'],
             'provenance': {'producteur': 'notes5666', 'brouillon_source': row['item']}}]


def _note_journal(desk, row, titres):
    """Journal de rangement : réservation, dépôt et relecture y sont déjà prouvés, étape par étape. Il n'y a rien à refaire ici."""
    from .office440 import matter_of
    path = row['destination']
    if not path:
        return []
    state = {'reserve': 'preparing', 'publie': 'published'}.get(row['etape'], 'verified')
    return [{'service': 'notes5666', 'object': row['item'], 'version': row['empreinte'] or '1', 'matter': row['matter'] or matter_of(desk, path), 'kind': 'note', 'label': 'Note', 'path': path, 'sha256': row['empreinte'], 'title': titres.get(row['item']) or PurePosixPath(path).stem, 'state': state,
             'verified': state == 'verified', 'evidence': 'journal de rangement (notes5666)', 'when': row['updated'], 'provenance': {'producteur': 'notes5666', 'brouillon_source': row['item']}}]


def note_rows(desk):
    marks = {r['item']: r for r in desk.db.execute("SELECT item, title, at FROM cockpit530_reviewed WHERE decision='range' ORDER BY at ASC LIMIT ?", (LIMITE,)).fetchall()}
    titres = {k: str(r['title']).rpartition(' — ')[0] for k, r in marks.items()}
    journal = set()
    try:
        rows = desk.db.execute('SELECT * FROM rangement5689 ORDER BY updated ASC LIMIT ?', (LIMITE,)).fetchall()
    except sqlite3.OperationalError:    # journal créé au premier rangement
        rows = []
    for r in rows:
        journal.add(r['item'])
        yield r['item'], partial(_note_journal, desk, r, titres)
    for item, r in marks.items():
        if item not in journal:     # notes rangées avant le journal
            yield item, partial(_note, desk, r)


def note_signature(desk):
    first = tuple(desk.db.execute("SELECT COUNT(*), COALESCE(MAX(at),'') FROM cockpit530_reviewed WHERE decision='range'").fetchone())
    try:
        return first + tuple(desk.db.execute("SELECT COUNT(*), COALESCE(MAX(updated),'') FROM rangement5689").fetchone())
    except sqlite3.OperationalError:
        return first


ISSUED = ('emise', 'envoye', 'signe', 'accepte')


def _facture(row):
    files = _json(row['files_json'])
    if not isinstance(files, dict):
        raise ValueError('fichiers_inattendus')
    rows = [{'path': str(v.get('chemin') or ''), 'sha256': str(v.get('sha256') or ''), 'verified': bool(v.get('verifie_le')) and not v.get('etat')} for k, v in files.items() if isinstance(v, dict) and v.get('chemin') and k in ('docx', 'pdf')]
    rows.sort(key=lambda f: _rang(f['path']))
    if not rows:
        return []
    first = rows[0]
    title = '%s %s' % (str(row['kind']).replace('_', ' ').capitalize(), row['number'] or '')
    issued = row['state'] in ISSUED
    return [{'service': 'facturation5653', 'object': row['id'], 'version': first['sha256'] or row['version'], 'matter': row['matter'], 'kind': 'convention' if 'convention' in str(row['kind']) or 'mission' in str(row['kind']) else 'autre_document', 'path': first['path'], 'sha256': first['sha256'],
             'verified': first['verified'], 'evidence': 'relecture à la publication (facturation5653)', 'state': None if first['verified'] else 'published', 'title': title.strip(), 'when': row['updated'], 'representations': rows[1:], 'right': 'voir_honoraires', 'protected': 'emis' if issued else '',
             'review_required': not issued, 'provenance': {'producteur': 'facturation5653', 'document_financier': row['id'], 'etat': row['state']}}]


def facture_rows(desk):
    for r in desk.db.execute("SELECT * FROM b53_documents WHERE files_json NOT IN ('','{}') ORDER BY updated ASC LIMIT ?", (LIMITE,)).fetchall():
        yield r['id'], partial(_facture, r)


# ------------------------------------------------------------------------------------------------ travailleur et missions
def _production(row):
    v = _json(row['verification'])
    files = _fichiers(v.get('files') or [])
    if not files:
        return []
    first, rest = files[0], files[1:]
    proved = bool(v.get('verified'))
    return [{'service': 'production420', 'object': row['id'], 'version': first['sha256'] or row['updated'], 'matter': row['matter'], 'kind': '', 'path': first['path'], 'title': PurePosixPath(first['path']).stem, 'sha256': first['sha256'], 'size': first['size'],
             'verified': proved, 'evidence': 'relecture du travailleur (production420)', 'state': None if proved else 'published' if first['sha256'] or row['status'] != 'prepared' else 'preparing', 'when': row['updated'], 'representations': rest,
             'provenance': {'producteur': 'production420', 'tache': _col(row, 'job_id'), 'livrable': row['id']}}]


def production_rows(desk):
    for r in desk.db.execute("SELECT * FROM production_deliverables_v420 WHERE status IN ('verified','verifying','prepared') AND deliverable_kind<>'mail_draft' ORDER BY updated ASC LIMIT ?", (LIMITE,)).fetchall():
        yield r['id'], partial(_production, r)


def _mission(row):
    detail = _json(row['detail'])
    path = str(_col(row, 'remote_ref') or detail.get('path') or '')
    sha = str(detail.get('sha256') or '')
    if not path:
        return []
    return [{'service': 'missions5614', 'object': row['id'], 'version': sha or row['updated'], 'matter': _col(row, 'matter') or '', 'path': path, 'sha256': sha, 'verified': bool(sha and detail.get('verified_at')), 'evidence': 'relecture du dépôt de mission (taches5614)',
             'title': PurePosixPath(path).stem, 'when': row['updated'], 'provenance': {'producteur': 'missions5614', 'operation': row['id']}}]


def mission_rows(desk):
    for r in desk.db.execute("SELECT o.*, m.matter AS matter FROM ops5614 o LEFT JOIN missions5614 m ON m.id=o.mission WHERE o.state='confirme' AND o.remote_ref<>'' ORDER BY o.updated ASC LIMIT ?", (LIMITE,)).fetchall():
        yield r['id'], partial(_mission, r)


# ------------------------------------------------------------------------------------------------ courriels : réponses préparées et brouillons déposés
def _cache(desk):
    return desk.settings('cockpit530:drafts', None) or {}


def _logique(desk, d):
    """Identité logique d'une réponse : le courriel auquel elle répond (response_id), sinon les références IMAP. Jamais le seul objet."""
    from .reponses5671 import mail_of_draft
    response = mail_of_draft(desk, d.get('agent_key'))
    return response, response or d.get('agent_key') or str(d.get('message_id') or '').strip('<>') or str(d.get('in_reply_to') or '').strip('<>') or 'uid:%s' % d.get('uid')


def _brouillon(desk, d, validity):
    from . import cockpit530
    response, logical = _logique(desk, d)
    internal = cockpit530.is_internal_note(desk, d)
    return [{'service': 'messagerie', 'object': 'mail:%s:%s' % (validity, d['uid']), 'version': d.get('date') or '1', 'document_id': 'courriel-' + digest(str(logical))[:32], 'matter': cockpit530._draft_matter(desk, d), 'kind': 'note' if internal else 'courriel', 'label': 'Note interne' if internal else 'Courriel',
             'title': d.get('subject') or '(sans objet)', 'state': 'verified', 'when': d.get('date') or '', 'response_id': response, 'deposit_state': 'depose', 'deposit_ref': '%s:%s' % (validity, d['uid']), 'provenance': {'producteur': 'messagerie', 'references': {k: d.get(k, '') for k in ('message_id', 'in_reply_to', 'agent_key')}}}]


def _fermer_brouillons(desk, present):
    """Brouillon envoyé, classé ou supprimé : la carte quitte la file, le registre garde sa trace. Rien n'est conclu d'une messagerie injoignable."""
    now = _now()
    for row in desk.db.execute("SELECT id, source_object_id FROM prepared_items5689 WHERE source_service='messagerie' AND lifecycle='current'").fetchall():
        if row['source_object_id'] not in present:
            desk.db.execute("UPDATE prepared_items5689 SET lifecycle='archived', deposit_state='retire', updated=? WHERE id=?", (now, row['id']))
    return []


def brouillon_rows(desk):
    cache = _cache(desk)
    validity = str(cache.get('validity', ''))
    done = {r[0] for r in desk.db.execute('SELECT item FROM cockpit530_reviewed')}
    present = set()
    for d in cache.get('items', []):
        item = 'mail:%s:%s' % (validity, d.get('uid'))
        if item in done:
            continue
        present.add(item)
        yield item, partial(_brouillon, desk, d, validity)
    if cache.get('ok', True) and cache:
        yield 'fin', partial(_fermer_brouillons, desk, present)


def brouillon_signature(desk):
    cache = _cache(desk)
    done = desk.db.execute("SELECT COUNT(*) FROM cockpit530_reviewed WHERE item LIKE 'mail:%'").fetchone()[0]
    return [cache.get('ok', True), str(cache.get('validity', '')), [str(d.get('uid')) for d in cache.get('items', [])], done]


def brouillon_age(desk):
    """Âge de la dernière lecture ; ``False`` : la messagerie n'a pas répondu."""
    cache = _cache(desk)
    if not cache:
        return None
    if cache.get('ok') is False:
        return False
    return max(0, time.time() - float(cache.get('at', 0)))


def _reponse(desk, key, data, updated, subject_of):
    value = data.get('result') or {}
    return [{'service': 'reponses5671', 'object': key, 'version': updated, 'document_id': 'courriel-' + digest(key)[:32], 'matter': data.get('matter') or '', 'kind': 'courriel', 'label': 'Courriel', 'title': subject_of(key), 'state': 'preparing', 'when': updated, 'response_id': key, 'deposit_state': 'a_decider',
             'reserve': True, 'reserve_note': ' · '.join(str(x) for x in (value.get('limits') or []))[:400], 'provenance': {'producteur': 'reponses5671', 'apercu': 'reponses5671'}}]


def reponse_rows(desk):
    from .desk import report_for
    from .livrables5689 import reponse_deposee

    def subject(key):
        try:
            return str(report_for(desk.c, key).get('subject') or 'Réponse préparée')[:160]
        except Stop:
            return 'Réponse préparée'
    deposited = reponse_deposee(desk)
    for row in desk.db.execute("SELECT mail_key,data,updated FROM manual_drafts WHERE data LIKE '%\"requires_decision\": true%' ORDER BY updated ASC LIMIT 200").fetchall():
        if row['mail_key'] in deposited:     # brouillon déjà déposé : la réponse et son dépôt ne font qu'une carte
            continue
        data = _json(row['data'])
        if not (data.get('result') or {}).get('requires_decision'):
            continue
        yield row['mail_key'], partial(_reponse, desk, row['mail_key'], data, row['updated'], subject)


# Ordre utile : les journaux de création d'abord (empreinte connue), le travailleur ensuite, qui n'ajoute alors qu'un lien.
SOURCES = (
    {'id': 'docrequest520', 'label': 'Demandes de documents', 'signature': _signature('docreq520'), 'rows': docreq_rows, 'running': docreq_running},
    {'id': 'document_projects', 'label': 'Projets de documents', 'signature': _project_signature('document_projects_v220'), 'rows': lambda desk: _project_rows(desk, 'document_projects_v220', 'document_projects'), 'running': _project_running('document_projects_v220')},
    {'id': 'word_legal', 'label': 'Révisions Word', 'signature': _project_signature('word_projects_v250'), 'rows': lambda desk: _project_rows(desk, 'word_projects_v250', 'word_legal'), 'running': _project_running('word_projects_v250')},
    {'id': 'cessions5684', 'label': 'Cessions et opérations', 'signature': lambda desk: tuple(desk.db.execute("SELECT COUNT(*), COALESCE(MAX(at),''), SUM(state='signe') FROM deliverables5683").fetchone()), 'rows': cession_rows},   # « signé » ne change pas la date : compté à part
    {'id': 'corporate5636', 'label': 'Corporate', 'signature': _signature('operations5636'), 'rows': corporate_rows},
    {'id': 'bibliotheque5639', 'label': 'Bibliothèque de modèles', 'signature': lambda desk: 0, 'rows': lambda desk: ()},         # sans journal : inscrit à la génération
    {'id': 'formulaires5683', 'label': 'Formulaires', 'signature': lambda desk: 0, 'rows': lambda desk: ()},                      # idem
    {'id': 'courrier5679', 'label': 'Courriers du dossier', 'signature': _signature('courriers5679'), 'rows': courrier_rows},
    {'id': 'notes5666', 'label': 'Notes internes', 'signature': note_signature, 'rows': note_rows},
    {'id': 'facturation5653', 'label': 'Honoraires et facturation', 'signature': _signature('b53_documents'), 'rows': facture_rows},
    {'id': 'missions5614', 'label': 'Missions', 'signature': _signature('ops5614'), 'rows': mission_rows},
    {'id': 'production420', 'label': 'Travaux de l’agent', 'signature': _signature('production_deliverables_v420'), 'rows': production_rows},
    {'id': 'messagerie', 'label': 'Brouillons de la messagerie', 'signature': brouillon_signature, 'rows': brouillon_rows, 'age': brouillon_age, 'down': 'Messagerie injoignable : les brouillons déjà connus restent affichés.'},
    {'id': 'reponses5671', 'label': 'Réponses à décider', 'signature': _signature('manual_drafts'), 'rows': reponse_rows},
)

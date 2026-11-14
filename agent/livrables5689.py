"""5.6.89 : registre commun des livrables préparés (audit 5.6.88, §§ 4, 5 et 13 ; anomalies B01, B02, B03).

Avant : la collecte d'Aujourd'hui lisait deux tables dans un seul ``try`` (une ligne abîmée vidait la liste), marquait la relecture sur le chemin
(une version nouvelle restait masquée) et ignorait les producteurs qui ne passent pas par le travailleur (un acte de cession restait invisible).

Ici : une ligne par version de document, sans copie du fichier. Le chemin n'est qu'une localisation ; l'identité vient de ``office_gen5684.document_for``
(identifiant Nextcloud, conservé après un renommage) et la version de l'empreinte vérifiée ou de l'ETag. Production et relecture sont deux états séparés :
un fichier relu dans le stockage n'est pas un document validé, et une relecture ne vaut que pour la version sur laquelle elle a porté.

- Écriture : ``reserve`` avant le dépôt, ``constater`` après (relecture et manifeste), ``reconcilier`` retrouve une sortie interrompue sans la recréer.
- Lecture : ``projection``, seule source d'Aujourd'hui, de Produire et de Dossier. Elle lit des tables locales : aucun parcours de Nextcloud à l'affichage.
- Adaptateurs : ``livrables_sources5689`` relit les journaux des producteurs ; chaque ligne a son propre ``try`` et l'état de chaque source est conservé,
  pour ne jamais confondre « aucun document » et « source indisponible ».
- Reprise des données anciennes : répétable, ne crée aucun fichier, ne certifie rien. Un ancien marqueur de relecture n'est rattaché qu'à une version
  connue et antérieure à lui ; sinon il reste une trace, sans approuver quoi que ce soit.
"""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import sqlite3
import uuid

from .common import Stop, digest, fold

CABINET = 'cabinet'
NATURES = {'courrier': 'Courrier d’avocat', 'conclusions': 'Conclusions', 'assignation': 'Assignation', 'contrat': 'Contrat', 'conditions_generales': 'Conditions générales',
           'convention': 'Convention', 'formulaire': 'Formulaire', 'courriel': 'Courriel', 'note': 'Note', 'autre_document': 'Autre document'}
PRODUCTION = {'preparing': 'En préparation', 'published': 'Fichier déposé, vérification en attente', 'verified': 'Fichier vérifié', 'failed': 'Échec', 'partial': 'Reprise partielle'}
REVIEW = {'to_review': 'À relire', 'reviewed': 'Relu', 'changes_requested': 'Modifications demandées', 'not_required': 'Relecture non requise'}
STATE = {'preparing': 'En préparation', 'ready': 'Prêt à relire', 'partial': 'Incomplet', 'failed': 'Échec', 'replaced': 'Remplacé', 'archived': 'Archivé'}
DECISIONS = {'relu': 'reviewed', 'valide': 'reviewed', 'modifications': 'changes_requested'}
RETRAITS = ('ecarte', 'ignore', 'supprime', 'range')        # le document quitte la file sans être « relu »
RECENCE = 30                                                 # filtre par défaut : visible et modifiable, jamais une disparition silencieuse
RANG = {'.docx': 0, '.odt': 1, '.pdf': 2, '.md': 3, '.txt': 4}
MIME = {'.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.odt': 'application/vnd.oasis.opendocument.text', '.pdf': 'application/pdf',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.ods': 'application/vnd.oasis.opendocument.spreadsheet', '.csv': 'text/csv',
        '.txt': 'text/plain', '.md': 'text/markdown', '.eml': 'message/rfc822', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
        '.xml': 'application/xml', '.zip': 'application/zip'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_items5689(id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL DEFAULT 'cabinet', document_id TEXT NOT NULL, version_id TEXT NOT NULL, version_number INTEGER NOT NULL DEFAULT 1,
    matter_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'autre_document', label TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '', file_id TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '', etag TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0, source_service TEXT NOT NULL, source_object_id TEXT NOT NULL, source_version TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}', owner TEXT NOT NULL DEFAULT '', production_state TEXT NOT NULL, state_reason TEXT NOT NULL DEFAULT '', lifecycle TEXT NOT NULL DEFAULT 'current',
    review_state TEXT NOT NULL DEFAULT 'to_review', protected TEXT NOT NULL DEFAULT '', reserve INTEGER NOT NULL DEFAULT 0, reserve_note TEXT NOT NULL DEFAULT '', required_right TEXT NOT NULL DEFAULT '',
    response_id TEXT NOT NULL DEFAULT '', deposit_state TEXT NOT NULL DEFAULT '', deposit_ref TEXT NOT NULL DEFAULT '', legacy_item TEXT NOT NULL DEFAULT '', source_at TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL,
    UNIQUE(cabinet_id, document_id, version_id), UNIQUE(source_service, source_object_id, source_version));
CREATE INDEX IF NOT EXISTS prepared_items5689_document ON prepared_items5689(document_id, version_number);
CREATE INDEX IF NOT EXISTS prepared_items5689_list ON prepared_items5689(lifecycle, source_at);
CREATE INDEX IF NOT EXISTS prepared_items5689_matter ON prepared_items5689(matter_id, lifecycle);
CREATE INDEX IF NOT EXISTS prepared_items5689_legacy ON prepared_items5689(legacy_item);
CREATE INDEX IF NOT EXISTS prepared_items5689_path ON prepared_items5689(path);
CREATE INDEX IF NOT EXISTS prepared_items5689_sha ON prepared_items5689(sha256);
CREATE TABLE IF NOT EXISTS prepared_representations5689(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, document_id TEXT NOT NULL, version_id TEXT NOT NULL, format TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'principal',
    path TEXT NOT NULL, mime_type TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL DEFAULT '', etag TEXT NOT NULL DEFAULT '', size INTEGER NOT NULL DEFAULT 0, verified INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL,
    UNIQUE(item_id, path));
CREATE INDEX IF NOT EXISTS prepared_representations5689_path ON prepared_representations5689(path);
CREATE TABLE IF NOT EXISTS prepared_reviews5689(id TEXT PRIMARY KEY, item_id TEXT NOT NULL, cabinet_id TEXT NOT NULL DEFAULT 'cabinet', document_id TEXT NOT NULL, version_id TEXT NOT NULL, decision TEXT NOT NULL, scope TEXT NOT NULL,
    author TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'direct', at TEXT NOT NULL, UNIQUE(document_id, version_id, scope, author, decision));
CREATE INDEX IF NOT EXISTS prepared_reviews5689_item ON prepared_reviews5689(item_id, at);
CREATE TABLE IF NOT EXISTS prepared_source_links5689(item_id TEXT NOT NULL, link_kind TEXT NOT NULL DEFAULT 'producteur', source_service TEXT NOT NULL, source_object_id TEXT NOT NULL, source_version TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY(link_kind, source_service, source_object_id, source_version, item_id));
CREATE INDEX IF NOT EXISTS prepared_source_links5689_item ON prepared_source_links5689(item_id);
CREATE TABLE IF NOT EXISTS prepared_sources5689(source TEXT PRIMARY KEY, state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL DEFAULT '', errors INTEGER NOT NULL DEFAULT 0, rows INTEGER NOT NULL DEFAULT 0,
    last_ok TEXT NOT NULL DEFAULT '', last_try TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS prepared_legacy_marks5689(item TEXT PRIMARY KEY, decision TEXT NOT NULL, at TEXT NOT NULL, state TEXT NOT NULL, document_id TEXT NOT NULL DEFAULT '', version_id TEXT NOT NULL DEFAULT '', migrated TEXT NOT NULL);
'''
VERSION_PROVISOIRE = ('reserve:', 'journal:')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _instant(value):
    """Date ISO d'un journal, comparable quel que soit son format ; illisible : le passé lointain, jamais une date à venir."""
    try:
        moment = datetime.fromisoformat(str(value or '').strip().replace('Z', '+00:00'))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def provisoire(version_id):
    return str(version_id).startswith(VERSION_PROVISOIRE)


_SCHEMA_PRET = set()        # 5.6.100 (lot E) : bases déjà préparées par ce processus (même clé que ``Desk``) : un affichage n'exécute plus de DDL


def _cle_schema(desk):
    try:
        from .desk import _schema_key
        return _schema_key(desk.c)
    except Exception:   # noqa: BLE001 — objet sans configuration (essais) : la préparation est simplement refaite
        return None


def ensure_schema(desk):
    """Tables du registre. 5.6.100 (lot E) : préparées au démarrage (``Desk._prepare`` par ``produire56100.ensure_schema``) ; ensuite, une base déjà
    préparée par ce processus n'est plus retouchée à chaque lecture (une base recréée change de clé et est préparée de nouveau)."""
    cle = _cle_schema(desk)
    if cle is not None and cle in _SCHEMA_PRET:
        return
    desk.db.executescript(SCHEMA)
    desk.db.commit()
    from . import registre5693      # 5.6.93 : colonnes de collecte (N03), de contrôle (N05) et table des identités (N06), par migration idempotente
    registre5693.ensure_schema(desk)
    if cle is not None:
        _SCHEMA_PRET.add(cle)


@contextmanager
def _atomique(desk):
    """Une ligne illisible n'abandonne que ses propres écritures : chaque ligne valide est enregistrée à part. Rejouer une ligne est sans effet (``observe`` est idempotent)."""
    try:
        yield
    except BaseException:
        desk.db.rollback()
        raise
    desk.db.commit()


def nature(hint='', title='', path=''):
    """Nature d'un document pour les filtres : d'abord ce que dit le producteur, ensuite le titre et l'emplacement."""
    hint = str(hint or '')
    hints = {'mise_en_demeure': 'courrier', 'courrier': 'courrier', 'courrier_confrere': 'courrier', 'conclusions': 'conclusions', 'assignation': 'assignation', 'assignation_refere': 'assignation', 'acte_procedure': 'assignation', 'constitution': 'assignation', 'contrat': 'contrat',
             'note': 'note', 'compte_rendu': 'note', 'courriel': 'courriel', 'formulaire': 'formulaire', 'convention_honoraires': 'convention', 'convention': 'convention', 'conditions_generales': 'conditions_generales', 'cgv': 'conditions_generales'}
    if hint in hints:
        return hints[hint]
    text = ' %s %s ' % (fold(str(title or '')), fold(PurePosixPath(str(path or '')).name))
    for key, words in (('conditions_generales', ('conditions generales', ' cgv', ' cgu')), ('conclusions', ('conclusions',)), ('assignation', ('assignation', 'requete')), ('convention', ('convention',)),
                       ('formulaire', ('formulaire', 'cerfa', '2759', '2672', '2676')), ('contrat', ('contrat', 'bail', 'cession', 'statuts', 'pacte', 'protocole', 'promesse', 'lettre de mission')),
                       ('courrier', ('courrier', 'lettre', 'mise en demeure')), ('note', ('note', 'consultation', 'memo', 'compte rendu'))):
        if any(word in text for word in words):
            return key
    up = '/' + fold(str(path or ''))
    return 'assignation' if '/procedure/' in up else 'courrier' if '/correspondances/' in up else 'contrat' if '/projets/' in up else 'autre_document'


def _version_of(x):
    if x.get('sha256'):
        return 'sha256:' + str(x['sha256'])
    if x.get('etag'):
        return 'etag:' + digest(str(x['etag']))[:40]
    return 'journal:' + digest('|'.join(str(x.get(k, '')) for k in ('service', 'object', 'version')))[:40]


def _mime(path, given=''):
    return MIME.get(PurePosixPath(str(path or '')).suffix.lower()) or str(given or 'application/octet-stream')


def _stem(path):
    return str(path).rsplit('.', 1)[0].lower() if '.' in PurePosixPath(str(path)).name else str(path).lower()


def _ext(path):
    return PurePosixPath(str(path or '')).suffix.lower()


def _document_of(desk, x):
    """Identité stable (5.6.93, N06) : d'abord l'identité composite stockage + compte + identifiant de fichier, puis le lien de création du producteur, puis
    une identité provisoire de chemin. Une empreinte identique prouve un contenu identique, pas le même fichier : elle ne rapproche jamais deux documents
    (``registre5693.suggerer_doublon`` en fait une suggestion)."""
    if x.get('document_id'):
        return x['document_id']
    from . import office_gen5684 as gen, registre5693
    fileid = str(x.get('fileid') or '')
    if fileid:
        gen.schema(desk)
        known = registre5693.par_identifiant(desk, fileid)
        if known:
            return gen.document_for(desk, str(x.get('path') or known['path']), {'fileid': fileid}, str(x.get('matter') or ''))
    linked = desk.db.execute("SELECT i.document_id FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service=? AND l.source_object_id=? ORDER BY i.version_number DESC LIMIT 1",
                             (x['service'], str(x['object']))).fetchone()
    if linked:
        return linked[0]
    path = str(x.get('path') or '')
    if not path:
        return 'doc-' + digest('%s|%s' % (x['service'], x['object']))[:32]
    gen.schema(desk)
    return gen.document_for(desk, path, {'fileid': fileid}, str(x.get('matter') or ''))


def _lien(desk, item_id, service, objet, version, kind='producteur', label=''):
    desk.db.execute('INSERT OR IGNORE INTO prepared_source_links5689(item_id,link_kind,source_service,source_object_id,source_version,label,created) VALUES(?,?,?,?,?,?,?)',
                    (item_id, kind, service, str(objet), str(version), str(label)[:200], _now()))


def _representation(desk, item, path, role='principal', sha256='', etag='', size=0, verified=False, mime=''):
    """Un export ou une autre forme du même document : une ligne de plus, pas une carte de plus."""
    now = _now()
    desk.db.execute('INSERT INTO prepared_representations5689(id,item_id,document_id,version_id,format,role,path,mime_type,sha256,etag,size,verified,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
                    'ON CONFLICT(item_id, path) DO UPDATE SET version_id=excluded.version_id, sha256=CASE WHEN excluded.sha256<>\'\' THEN excluded.sha256 ELSE sha256 END, etag=CASE WHEN excluded.etag<>\'\' THEN excluded.etag ELSE etag END, '
                    'size=CASE WHEN excluded.size>0 THEN excluded.size ELSE size END, verified=MAX(verified, excluded.verified), updated=excluded.updated',
                    (uuid.uuid4().hex, item['id'], item['document_id'], item['version_id'], _ext(path).lstrip('.') or 'fichier', role, path, _mime(path, mime), str(sha256 or ''), str(etag or ''), int(size or 0), int(bool(verified)), now, now))


def _carte_du_meme_projet(desk, x):
    """DOCX, PDF et Markdown d'un même projet : une carte. Le rang du format, pas son ordre d'arrivée, décide du fichier principal."""
    path = str(x.get('path') or '')
    if not path or _ext(path) == '.docx':
        return None
    stem = _stem(path)
    for row in desk.db.execute("SELECT * FROM prepared_items5689 WHERE matter_id=? AND lifecycle IN ('current','replaced') AND path<>? AND lower(path) LIKE ? ORDER BY version_number DESC LIMIT 8",
                               (str(x.get('matter') or ''), path, stem.replace('%', '_') + '.%')):
        if _stem(row['path']) == stem and RANG.get(_ext(row['path']), 9) < RANG.get(_ext(path), 9):
            return row
    return None


def _item(desk, ident):
    row = desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (ident,)).fetchone()
    return dict(row) if row else None


def observe(desk, x):
    """Inscrit une sortie déjà connue d'un journal. Sans effet si elle l'est déjà ; aucun fichier n'est lu ni écrit."""
    service, objet, version = x['service'], str(x['object']), str(x.get('version') or '')
    known = desk.db.execute("SELECT i.* FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service=? AND l.source_object_id=? AND l.source_version=?",
                            (service, objet, version)).fetchone()
    if known:
        return _actualiser(desk, dict(known), x)
    path = str(x.get('path') or '')
    annexe = desk.db.execute("SELECT r.item_id, r.sha256, r.role FROM prepared_representations5689 r JOIN prepared_items5689 i ON i.id=r.item_id WHERE r.path=? AND r.role<>'principal' AND i.matter_id=? ORDER BY r.created DESC LIMIT 1",
                             (path, str(x.get('matter') or ''))).fetchone() if path else None
    if annexe and (not x.get('sha256') or not annexe['sha256'] or x['sha256'] == annexe['sha256']):     # annexe d'un projet déjà inscrit, revue par un autre journal : un lien de plus
        _lien(desk, annexe['item_id'], service, objet, version)
        return annexe['item_id']
    twin = _carte_du_meme_projet(desk, x)
    if twin:
        _representation(desk, dict(twin), x['path'], 'export', x.get('sha256'), x.get('etag'), x.get('size'), x.get('verified'), x.get('mime'))
        _lien(desk, twin['id'], service, objet, version)
        return twin['id']
    document_id = _document_of(desk, x)
    version_id = _version_of(x)
    row = desk.db.execute('SELECT * FROM prepared_items5689 WHERE cabinet_id=? AND document_id=? AND version_id=?', (CABINET, document_id, version_id)).fetchone()
    if row:     # la même version vue par un autre producteur (travailleur et journal de création) : un lien, pas une carte
        _lien(desk, row['id'], service, objet, version)
        return _actualiser(desk, dict(row), x)
    current = desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle='current' ORDER BY version_number DESC LIMIT 1", (document_id,)).fetchone()
    if current and provisoire(current['version_id']) and not provisoire(version_id):
        # le journal ne donnait pas d'empreinte, un autre la donne : même fichier, la version se précise au lieu de se dédoubler
        desk.db.execute('UPDATE prepared_items5689 SET version_id=?, sha256=?, etag=?, mime_type=?, updated=? WHERE id=?', (version_id, str(x.get('sha256') or ''), str(x.get('etag') or ''), _mime(path, x.get('mime')) if path else current['mime_type'], _now(), current['id']))
        _lien(desk, current['id'], service, objet, version)
        item = _item(desk, current['id'])
        if path:
            _representation(desk, item, path, 'principal', x.get('sha256'), x.get('etag'), x.get('size'), x.get('verified'), x.get('mime'))
            for other in x.get('representations') or ():
                _representation(desk, item, other['path'], 'export', other.get('sha256'), other.get('etag'), other.get('size'), other.get('verified'), other.get('mime'))
        return _actualiser(desk, item, x)
    return _nouvelle_version(desk, x, document_id, version_id, current)


def _nouvelle_version(desk, x, document_id, version_id, current=None, reserved=None):
    now = _now()
    number = desk.db.execute('SELECT COALESCE(MAX(version_number),0)+1 FROM prepared_items5689 WHERE document_id=?', (document_id,)).fetchone()[0]
    first = desk.db.execute('SELECT legacy_item FROM prepared_items5689 WHERE document_id=? ORDER BY version_number LIMIT 1', (document_id,)).fetchone()
    path = str(x.get('path') or '')
    legacy = first['legacy_item'] if first and first['legacy_item'] else ('doc:' + digest(path)[:24] if path else '')
    state = x.get('state') or ('verified' if x.get('verified') else 'published' if path else 'preparing')
    ident = uuid.uuid4().hex
    title = re.sub(r'\s+', ' ', str(x.get('title') or PurePosixPath(path).stem or 'Document')).strip()[:300]
    kind = x.get('kind') if x.get('kind') in NATURES else nature(x.get('kind'), title, path)
    review = 'not_required' if x.get('review_required') is False else 'to_review'
    desk.db.execute('INSERT INTO prepared_items5689(id,cabinet_id,document_id,version_id,version_number,matter_id,kind,label,title,mime_type,path,file_id,sha256,etag,size,source_service,source_object_id,source_version,provenance,owner,'
                    'production_state,state_reason,lifecycle,review_state,protected,reserve,reserve_note,required_right,response_id,deposit_state,deposit_ref,legacy_item,source_at,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, CABINET, document_id, version_id, number, str(x.get('matter') or ''), kind, str(x.get('label') or ''), title, _mime(path, x.get('mime')) if path else str(x.get('mime') or ''), path, str(x.get('fileid') or ''),
                     str(x.get('sha256') or ''), str(x.get('etag') or ''), int(x.get('size') or 0), x['service'], str(x['object']), str(x.get('version') or ''), json.dumps(x.get('provenance') or {}, ensure_ascii=False), str(x.get('owner') or ''),
                     state, str(x.get('reason') or ''), 'current', review, str(x.get('protected') or ''), int(bool(x.get('reserve'))), str(x.get('reserve_note') or '')[:500], str(x.get('right') or ''),
                     str(x.get('response_id') or ''), str(x.get('deposit_state') or ''), str(x.get('deposit_ref') or ''), legacy, str(x.get('when') or now), now, now))
    _lien(desk, ident, x['service'], x['object'], x.get('version') or '')
    item = _item(desk, ident)
    if path:
        _representation(desk, item, path, 'principal', x.get('sha256'), x.get('etag'), x.get('size'), x.get('verified'), x.get('mime'))
        for other in x.get('representations') or ():
            _representation(desk, item, other['path'], 'export', other.get('sha256'), other.get('etag'), other.get('size'), other.get('verified'), other.get('mime'))
    for source in x.get('sources') or ():
        _lien(desk, ident, 'source', str(source)[:300], '', 'source', str(source)[:200])
    if current:
        desk.db.execute("UPDATE prepared_items5689 SET lifecycle='replaced', updated=? WHERE id=?", (now, current['id']))
    elif path and x.get('sha256'):      # 5.6.93 (N06) : même contenu qu'un autre document : une suggestion à examiner, jamais une fusion
        from . import registre5693
        registre5693.suggerer_doublon(desk, item)
    if x.get('verified') and path and not provisoire(version_id):      # ancien indicateur : preuve de stockage, et rien d'autre (§ 40)
        from . import verification5689
        verification5689.preuve_stockage_ancienne(desk, item, str(x.get('evidence') or x['service']))
    return ident


def _actualiser(desk, row, x):
    """Une ligne déjà connue évolue : la vérification progresse, le fichier se déplace, l'état de dépôt change. Une preuve acquise ne se perd pas."""
    changes = {}
    path = str(x.get('path') or '')
    if path and path != row['path'] and not x.get('state') == 'preparing':
        changes['path'] = path
    if x.get('verified') and row['production_state'] in ('published', 'preparing', 'partial'):
        changes.update(production_state='verified', state_reason='')
    elif x.get('state') and row['production_state'] == 'preparing' and x['state'] != 'preparing':    # un échec constaté n'est jamais effacé par la seule mention d'un journal
        changes['production_state'] = x['state']
    for field, column in (('sha256', 'sha256'), ('etag', 'etag'), ('deposit_state', 'deposit_state'), ('deposit_ref', 'deposit_ref'), ('fileid', 'file_id')):
        if x.get(field) and str(x[field]) != row[column]:
            changes[column] = str(x[field])
    if x.get('protected') and x['protected'] != row['protected']:
        changes['protected'] = x['protected']
    if 'reserve' in x and int(bool(x['reserve'])) != row['reserve']:
        changes.update(reserve=int(bool(x['reserve'])), reserve_note=str(x.get('reserve_note') or '')[:500])
    if changes:
        changes['updated'] = _now()
        desk.db.execute('UPDATE prepared_items5689 SET %s WHERE id=?' % ', '.join(k + '=?' for k in changes), tuple(changes.values()) + (row['id'],))
        if 'path' in changes:
            desk.db.execute("UPDATE prepared_representations5689 SET path=?, updated=? WHERE item_id=? AND role='principal'", (path, changes['updated'], row['id']))
        if changes.get('production_state') == 'verified' and path:
            desk.db.execute("UPDATE prepared_representations5689 SET verified=1, updated=? WHERE item_id=? AND role='principal'", (changes['updated'], row['id']))
            from . import verification5689
            verification5689.preuve_stockage_ancienne(desk, _item(desk, row['id']), str(x.get('evidence') or x['service']))
    return row['id']


# -------------------------------------------------------------------------------------------- publication : réserver, déposer, vérifier, inscrire
def reserve(desk, service, objet, version, matter='', kind='', title='', path='', mime='', sha256='', provenance=None, owner='', label='', document_id='', protected='', right='', reserve_note=''):
    """Avant toute écriture externe : réserve l'identité de sortie (service + objet + version). Une seconde demande reçoit la même réservation."""
    ensure_schema(desk)
    version = str(version)
    row = desk.db.execute('SELECT * FROM prepared_items5689 WHERE source_service=? AND source_object_id=? AND source_version=?', (service, str(objet), version)).fetchone()
    if row:
        if row['production_state'] == 'failed':     # nouvel essai de la même sortie : elle redevient attendue, sans nouvelle identité
            desk.db.execute("UPDATE prepared_items5689 SET production_state='preparing', state_reason='', path=?, sha256=?, updated=? WHERE id=?", (path or row['path'], str(sha256 or row['sha256']), _now(), row['id']))
            desk.db.commit()
        return _item(desk, row['id'])
    x = {'service': service, 'object': str(objet), 'version': version, 'matter': matter, 'kind': kind, 'title': title, 'path': path, 'mime': mime, 'provenance': provenance, 'owner': owner, 'label': label,
         'document_id': document_id, 'state': 'preparing', 'protected': protected, 'right': right, 'reserve': bool(reserve_note), 'reserve_note': reserve_note}
    with _atomique(desk):
        document = _document_of(desk, x)
        current = desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle='current' ORDER BY version_number DESC LIMIT 1", (document,)).fetchone()
        ident = _nouvelle_version(desk, {**x, 'sha256': ''}, document, 'reserve:' + uuid.uuid4().hex)
        # la réservation d'une version suivante ne cache pas celle qui existe : elle n'apparaît qu'une fois le fichier vérifié
        desk.db.execute('UPDATE prepared_items5689 SET sha256=?, lifecycle=? WHERE id=?', (str(sha256 or ''), 'reserved' if current else 'current', ident))
    desk.db.commit()
    return _item(desk, ident)


class Sortie:
    """Point d'accroche d'un producteur : réserver, écrire si besoin, constater. Le suivi ne fait jamais échouer le producteur : une panne du registre se voit dans ``etat_sources`` et la réconciliation reprend le constat."""

    def __init__(self, desk, service, objet, version, **fields):
        self.desk, self.item = desk, None
        try:
            self.item = reserve(desk, service, objet, version, **fields)
        except (sqlite3.Error, Stop, KeyError, ValueError) as ex:
            desk.audit('livrables5689_reservation_impossible', {'source': service, 'cause': type(ex).__name__})

    def publiee(self, client):
        return bool(self.item) and deja_publiee(self.desk, self.item, client)

    def constater(self, client, path='', raw=None):
        if self.item:
            try:
                self.item = constater(self.desk, self.item, client, path, raw)
            except (sqlite3.Error, Stop, KeyError, ValueError) as ex:
                self.desk.audit('livrables5689_constat_impossible', {'cause': type(ex).__name__})
        return self.item


def deja_publiee(desk, reservation, client):
    """Reprise : la sortie attendue existe-t-elle, avec le contenu attendu ? Si oui on ne redépose rien (accusé perdu, arrêt entre deux étapes)."""
    path, expected = reservation.get('path'), reservation.get('sha256')
    if not path or not expected or reservation.get('production_state') == 'verified':
        return reservation.get('production_state') == 'verified'
    try:
        info = client.stat(path)
        return hashlib.sha256(client.download({**info, 'path': path})).hexdigest() == expected
    except Stop:
        return False


def _echec(desk, item_id, state, reason, controle=None):
    """État constaté, et (5.6.93, N05) erreur structurée du contrôle : un fichier absent ou différent reçoit un délai progressif avant le prochain essai,
    au lieu de revenir en tête de chaque lot. ``controle`` : compter l'échec (par défaut, pour un état « partiel », celui des contrôles)."""
    from . import registre5693
    registre5693.ensure_schema(desk)
    desk.db.execute('UPDATE prepared_items5689 SET production_state=?, state_reason=?, updated=? WHERE id=?', (state, str(reason)[:120], _now(), item_id))
    registre5693.noter_echec(desk, item_id, reason, state == 'partial' if controle is None else controle)
    desk.db.commit()


def constater(desk, reservation, client, path='', raw=None):
    """Après le dépôt : relit le fichier, compare, enregistre le manifeste. Sans relecture concluante, l'état reste celui qui est prouvé."""
    ident = reservation['id'] if isinstance(reservation, dict) else str(reservation)
    row = _item(desk, ident)
    if not row:
        raise Stop('livrable_absent')
    path = path or row['path']
    expected = hashlib.sha256(raw).hexdigest() if raw is not None else row['sha256']
    if row['production_state'] == 'verified':
        return row
    try:
        info = client.stat(path)
        data = client.download({**info, 'path': path})
    except Stop as ex:
        missing = str(ex) in ('http_404', 'fichier_nextcloud_introuvable')
        _echec(desk, ident, 'failed' if missing else 'published', 'fichier_absent_apres_depot' if missing else 'relecture_impossible')
        return _item(desk, ident)
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        _echec(desk, ident, 'failed', 'fichier_non_conforme')
        return _item(desk, ident)
    return manifeste(desk, row, path, actual, info, len(data))


def manifeste(desk, row, path, sha, info, size):
    from . import registre5693
    registre5693.ensure_schema(desk)      # colonnes de contrôle (5.6.93), avant la transaction
    now = _now()
    version_id = 'sha256:' + sha
    with _atomique(desk):
        twin = desk.db.execute('SELECT id FROM prepared_items5689 WHERE cabinet_id=? AND document_id=? AND version_id=? AND id<>?', (CABINET, row['document_id'], version_id, row['id'])).fetchone()
        if twin:    # contenu identique à une version déjà connue : la réservation s'y rattache, pas de version fantôme
            _lien(desk, twin['id'], row['source_service'], row['source_object_id'], row['source_version'])
            desk.db.execute('DELETE FROM prepared_representations5689 WHERE item_id=?', (row['id'],))
            desk.db.execute('DELETE FROM prepared_source_links5689 WHERE item_id=?', (row['id'],))
            desk.db.execute('DELETE FROM prepared_items5689 WHERE id=?', (row['id'],))
            return _item(desk, twin['id'])
        desk.db.execute("UPDATE prepared_items5689 SET lifecycle='replaced', updated=? WHERE document_id=? AND lifecycle='current' AND id<>?", (now, row['document_id'], row['id']))
        desk.db.execute("UPDATE prepared_items5689 SET version_id=?, path=?, mime_type=?, file_id=?, sha256=?, etag=?, size=?, production_state='verified', state_reason='', lifecycle='current', updated=?, "
                        "checked_at=?, next_check_at='', failure_count=0, check_error='' WHERE id=?",      # 5.6.93 (N05) : un fichier relu et conforme clôt l'incident de contrôle
                        (version_id, path, _mime(path, row['mime_type']), str(info.get('fileid') or row['file_id']), sha, str(info.get('etag') or ''), int(size), now, now, row['id']))
        item = _item(desk, row['id'])
        desk.db.execute('DELETE FROM prepared_representations5689 WHERE item_id=? AND role=\'principal\' AND path<>?', (row['id'], path))
        _representation(desk, item, path, 'principal', sha, info.get('etag'), size, True, row['mime_type'])
        if info.get('fileid'):
            from . import office_gen5684 as gen, registre5693
            gen.schema(desk)
            desk.db.execute('UPDATE office_documents5684 SET path=?, fileid=?, updated=? WHERE document_id=?', (path, str(info['fileid']), now, row['document_id']))
            registre5693.noter_identifiant(desk, info['fileid'], row['document_id'])
        from . import verification5689
        verification5689.preuve_stockage(desk, item, info, size)
    desk.audit('livrable5689_manifeste', {'document': row['document_id'][:40], 'version': version_id[:40], 'source': row['source_service']})
    return _item(desk, row['id'])


def reconcilier(desk, client, age_minutes=10, limite=30, pause=None):
    """Sortie réservée puis restée sans suite : on regarde ce qui existe ; on ne recrée rien. Absente, elle redevient un échec visible, à relancer."""
    from . import registre5693
    ensure_schema(desk)
    limit = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    done = {'verifies': 0, 'absents': 0, 'differents': 0}
    if registre5693.en_panne(desk):      # 5.6.93 (N05) : stockage en panne, attente en cours : aucune requête
        return {**done, 'arret': 'stockage_en_attente'}
    try:
        for row in desk.db.execute("SELECT * FROM prepared_items5689 WHERE production_state='preparing' AND path<>'' AND updated<=? ORDER BY updated LIMIT ?", (limit, limite)).fetchall():
            if pause:
                pause()
            try:
                info = client.stat(row['path'])
                data = client.download({**info, 'path': row['path']})
            except Stop as ex:
                if str(ex) in ('http_404', 'fichier_nextcloud_introuvable'):
                    _echec(desk, row['id'], 'failed', 'sortie_non_publiee')
                    done['absents'] += 1
                continue
            sha = hashlib.sha256(data).hexdigest()
            if row['sha256'] and sha != row['sha256']:
                _echec(desk, row['id'], 'partial', 'fichier_different_de_la_sortie_attendue')
                done['differents'] += 1
                continue
            manifeste(desk, dict(row), row['path'], sha, info, len(data))
            done['verifies'] += 1
    except registre5693.Borne as ex:     # borne de requêtes ou panne : le passage s'arrête, rien n'est conclu des sorties non relues
        done['arret'] = str(ex)
    return done


def nouvelle_version_constatee(desk, row, path, raw, info, service='synchronisation', provenance=None):
    """Le fichier a changé au même chemin (éditeur, dépôt à la main, modification acceptée). La version précédente reste au registre, remplacée ; la relecture repart de zéro."""
    sha = hashlib.sha256(raw).hexdigest()
    if sha == row['sha256']:
        return row
    now = _now()
    with _atomique(desk):
        ident = _nouvelle_version(desk, {'service': service, 'object': row['document_id'], 'version': sha, 'matter': row['matter_id'], 'kind': row['kind'], 'label': row['label'], 'title': row['title'], 'path': path,
                                         'mime': row['mime_type'], 'sha256': sha, 'etag': info.get('etag'), 'fileid': info.get('fileid') or row['file_id'], 'size': len(raw), 'state': 'verified', 'when': now,
                                         'provenance': provenance or {'producteur': service, 'version_precedente': row['version_id']}, 'protected': row['protected'], 'right': row['required_right']},
                                  row['document_id'], 'sha256:' + sha, row)
        item = _item(desk, ident)
        desk.db.execute("UPDATE prepared_representations5689 SET verified=1 WHERE item_id=? AND role='principal'", (ident,))
        from . import verification5689
        verification5689.preuve_stockage(desk, item, info, len(raw))
    desk.db.commit()
    return item


def _deplace(desk, client, row):
    """Fichier absent de son emplacement : renommé dans le même dossier ? Une seule lecture de dossier, au travail d'arrière-plan.

    5.6.93 (N06) : un renommage n'est accepté qu'avec une preuve (``registre5693.renommage_prouve``) : le même identifiant de fichier, ou, pour une ligne
    ancienne dont l'identifiant n'a jamais été lu, un candidat unique au même contenu et inconnu du registre. Un autre fichier au même contenu n'est pas
    ce document : il reste distinct."""
    from . import registre5693
    if not row['sha256'] and not row['file_id']:
        return False
    try:
        items = client.list_folder(str(PurePosixPath(row['path']).parent))
    except Stop:
        return False
    item, info, preuve = registre5693.renommage_prouve(desk, client, row, items)
    if not item:
        return False
    from . import verification5689
    verification5689.invalider(desk, row['document_id'], row['version_id'], 'stockage')
    if preuve == 'identifiant' and info.get('etag') != row['etag']:      # même fichier, déplacé ; son contenu se relit à son nouvel emplacement
        desk.db.execute('UPDATE prepared_items5689 SET path=?, updated=? WHERE id=?', (item['path'], _now(), row['id']))
        desk.db.execute("UPDATE prepared_representations5689 SET path=?, updated=? WHERE item_id=? AND role='principal'", (item['path'], _now(), row['id']))
        moved = _item(desk, row['id'])
        raw = client.download({**info, 'path': item['path']})
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            nouvelle_version_constatee(desk, moved, item['path'], raw, info)
        else:
            manifeste(desk, moved, item['path'], row['sha256'], info, len(raw))
    else:
        manifeste(desk, dict(row), item['path'], row['sha256'], {'etag': info.get('etag') or item.get('etag'), 'fileid': info.get('fileid') or row['file_id']}, info.get('_raw_size') or row['size'])
    desk.audit('registre5693_renommage', {'document': row['document_id'][:40], 'preuve': preuve})
    return True


def synchroniser_fichiers(desk, client, limite=40, pause=None, borne=None):
    """Travail d'arrière-plan, borné : c'est lui, et non l'affichage, qui compare le stockage au registre (empreintes calculées à la synchronisation).

    5.6.93 (N05) : les contrôles dus sont choisis par ``registre5693.selection`` (part réservée aux documents valides et aux versions nouvelles, rotation
    par date du dernier contrôle) ; un absent attend un délai croissant ; trois échecs du stockage d'affilée arrêtent le passage et ouvrent une attente
    croissante, sans rien reprocher aux documents ; chaque requête est comptée et le passage s'arrête à sa borne (``requetes``, ``borne``)."""
    from . import registre5693
    ensure_schema(desk)
    counted = registre5693.compter(client, borne or registre5693.REQUETES_PAR_PASSAGE)
    start = counted.requetes
    out = {'controles': 0, 'nouvelles_versions': 0, 'absents': 0, 'verifies': 0, 'requetes': 0, 'borne': counted.borne, 'panne': False, 'arret': ''}
    if registre5693.en_panne(desk):
        out.update(panne=True, arret='stockage_en_attente')
        registre5693.mesurer(desk, out)
        return out
    since = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    rows = registre5693.selection(desk, limite, since)
    try:
        for row in rows:
            if pause:
                pause()
            out['controles'] += 1
            try:
                info = counted.stat(row['path'])
            except Stop as ex:
                if str(ex) in registre5693.ABSENT:
                    if _deplace(desk, counted, row):
                        registre5693.noter_succes(desk, row['id'])
                        out['verifies'] += 1
                        continue
                    _echec(desk, row['id'], 'partial', 'fichier_introuvable_a_cet_emplacement', controle=True)
                    from . import verification5689
                    verification5689.invalider(desk, row['document_id'], row['version_id'], 'stockage')
                    out['absents'] += 1
                continue        # stockage injoignable : le document n'est pas mis en cause, il reste dû
            if row['production_state'] == 'verified' and row['etag'] and info.get('etag') == row['etag']:
                registre5693.noter_succes(desk, row['id'], info.get('fileid'))
                continue
            try:
                raw = counted.download({**info, 'path': row['path']})
            except Stop:
                continue        # lecture interrompue : le document reste dû, rien n'est conclu
            sha = hashlib.sha256(raw).hexdigest()
            if row['sha256'] and sha != row['sha256']:
                fresh = nouvelle_version_constatee(desk, dict(row), row['path'], raw, info)
                registre5693.noter_succes(desk, row['id'], info.get('fileid'))
                registre5693.noter_succes(desk, fresh['id'], info.get('fileid'))     # la version nouvelle vient d'être lue : contrôlée
                out['nouvelles_versions'] += 1
            else:
                manifeste(desk, dict(row), row['path'], sha, info, len(raw))
                registre5693.noter_succes(desk, row['id'], info.get('fileid'))
                out['verifies'] += 1
    except registre5693.Borne as ex:
        out['arret'] = str(ex)
        out['panne'] = counted.panne
    out['requetes'] = counted.requetes - start
    registre5693.noter_panne(desk, counted)
    desk.db.commit()
    registre5693.mesurer(desk, out)
    return out


# -------------------------------------------------------------------------------------------- relecture : document + version
def _portee(desk, owner, role):
    """Décision de cabinet ou simple lecture personnelle : le droit « Valider » fait la différence, pas le bouton."""
    if role == 'administrateur':
        return 'cabinet'
    from .membres5650 import effective_rights
    try:
        return 'cabinet' if effective_rights(desk.c['state_dir'], owner, role).get('valider') else 'personnel'
    except Stop:
        return 'personnel'


def relire(desk, ident, version_id, decision, owner, role, note='', scope=None, origin='direct', at=None):
    """Décision sur une version précise. La version affichée est imposée : une relecture ne vaut jamais pour un contenu que la personne n'a pas vu."""
    ensure_schema(desk)
    row = charger(desk, ident, version_id=None)
    if decision not in DECISIONS and decision not in RETRAITS:
        raise Stop('decision_invalide')
    if str(version_id) != row['version_id'] or row['lifecycle'] != 'current':
        raise Stop('version_perimee')
    return _inscrire_relecture(desk, row, decision, str(owner), scope or _portee(desk, owner, role), note, origin, at)


def _inscrire_relecture(desk, row, decision, owner, scope, note='', origin='direct', at=None):
    stamp = at or _now()
    desk.db.execute('INSERT OR IGNORE INTO prepared_reviews5689(id,item_id,cabinet_id,document_id,version_id,decision,scope,author,note,origin,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, row['id'], CABINET, row['document_id'], row['version_id'], decision, scope, owner[:160], str(note)[:1000], origin, stamp))
    if scope == 'cabinet':
        if decision in DECISIONS:
            desk.db.execute('UPDATE prepared_items5689 SET review_state=?, updated=? WHERE id=?', (DECISIONS[decision], _now(), row['id']))
        elif row['lifecycle'] == 'current':
            desk.db.execute("UPDATE prepared_items5689 SET lifecycle='archived', updated=? WHERE id=?", (_now(), row['id']))
    desk.db.commit()
    from . import verification5689
    verification5689.relecture_humaine(desk, _item(desk, row['id']))
    return _item(desk, row['id'])


def _trouver_legacy(desk, item):
    row = desk.db.execute("SELECT * FROM prepared_items5689 WHERE legacy_item=? AND lifecycle IN ('current','archived') ORDER BY version_number DESC LIMIT 1", (str(item),)).fetchone()
    return dict(row) if row else None


def depuis_cockpit(desk, item, decision, owner='', scope='cabinet'):
    """« Relu », « Valider », « Écarter », « Ignorer » d'Aujourd'hui : même décision, posée sur la version affichée. Sans document au registre : rien."""
    try:
        row = _trouver_legacy(desk, item)
        if not row:
            return None
        if not owner:
            from .membres5650 import current_author
            owner = current_author() or 'cabinet'
        return _inscrire_relecture(desk, row, decision, str(owner), scope)
    except (Stop, sqlite3.Error):   # la décision historique reste enregistrée ; la synchronisation la reprendra comme marqueur
        return None


def depuis_file(desk, item, owner, role='administrateur'):
    """« Relu » de la file unique : l'élément porte son document, directement ou par sa demande (« document:… »)."""
    try:
        ensure_schema(desk)
        objet = str(item.get('object') or '')
        row = None
        if item.get('item'):
            row = _trouver_legacy(desk, item['item'])
        elif objet.startswith('document:'):
            found = desk.db.execute("SELECT i.* FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service='docrequest520' AND l.source_object_id=? AND i.lifecycle='current' ORDER BY i.version_number DESC LIMIT 1", (objet[len('document:'):],)).fetchone()
            row = dict(found) if found else None
        if row:
            return _inscrire_relecture(desk, row, 'relu', str(owner), _portee(desk, owner, role))
    except (Stop, sqlite3.Error):
        return None
    return None


def importer_marqueurs(desk):
    """Anciens marqueurs « doc:empreinte(chemin) ». Rattachés à la dernière version connue qui existait à leur date ; sinon conservés, sans effet sur la relecture. Répétable."""
    done = {r['item']: r['at'] for r in desk.db.execute('SELECT item, at FROM prepared_legacy_marks5689')}
    try:
        rows = desk.db.execute("SELECT item, decision, at FROM cockpit530_reviewed WHERE item LIKE 'doc:%'").fetchall()
    except sqlite3.OperationalError:
        return 0
    count = 0
    for mark in rows:
        if done.get(mark['item']) == mark['at'] or mark['decision'] not in tuple(DECISIONS) + RETRAITS:
            continue
        versions = desk.db.execute('SELECT * FROM prepared_items5689 WHERE legacy_item=? ORDER BY version_number DESC', (mark['item'],)).fetchall()
        if not versions:
            continue    # document pas encore connu du registre : repris au prochain passage
        moment = _instant(mark['at'])
        known = next((v for v in versions if not provisoire(v['version_id']) and _instant(v['source_at']) <= moment), None)
        state = 'rattache' if known else 'version_inconnue'
        if known:
            _inscrire_relecture(desk, dict(known), mark['decision'], 'ancien marqueur', 'cabinet', origin='migration', at=str(mark['at']))
        desk.db.execute('INSERT OR REPLACE INTO prepared_legacy_marks5689(item,decision,at,state,document_id,version_id,migrated) VALUES(?,?,?,?,?,?,?)',
                        (mark['item'], mark['decision'], mark['at'], state, versions[0]['document_id'], known['version_id'] if known else '', _now()))
        count += 1
    desk.db.commit()
    return count


# -------------------------------------------------------------------------------------------- synchronisation locale (sans réseau)
def _sync_source(desk, src, force, fond=False):
    sid = src['id']
    row = desk.db.execute('SELECT * FROM prepared_sources5689 WHERE source=?', (sid,)).fetchone()
    now = _now()

    def store(state, message='', signature='', errors=0, count=0, ok=True):
        desk.db.execute('INSERT INTO prepared_sources5689(source,state,message,signature,errors,rows,last_ok,last_try) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET state=excluded.state, message=excluded.message, '
                        'signature=excluded.signature, errors=excluded.errors, rows=excluded.rows, last_ok=CASE WHEN excluded.last_ok<>\'\' THEN excluded.last_ok ELSE last_ok END, last_try=excluded.last_try',
                        (sid, state, message, signature, errors, count, now if ok else '', now))
    try:
        signature = json.dumps(src['signature'](desk), default=str)
    except sqlite3.OperationalError as ex:
        if 'no such table' in str(ex):     # le producteur n'a encore jamais servi : vide, pas en panne
            store('fresh', '', 'absente')
            return
        store('unavailable', 'Source illisible pour l’instant : les documents déjà connus restent affichés.', ok=False)
        return
    except Exception:   # noqa: BLE001
        store('unavailable', 'Source illisible pour l’instant : les documents déjà connus restent affichés.', ok=False)
        return
    if getattr(src.get('rows'), 'parcours', None):
        # 5.6.93 (N03) : parcours par pages, curseur persistant ; la signature n'est consommée qu'à la fin du lot, « Collecte partielle » jusque-là
        from . import registre5693
        try:
            registre5693.collecter(desk, src, force, fond, signature)
        except Exception as ex:   # noqa: BLE001 — une source en panne ne vide pas les autres ; le curseur garde la dernière page validée
            if desk.db.in_transaction:
                desk.db.rollback()
            desk.audit('livrables5689_source_illisible', {'source': sid, 'cause': type(ex).__name__})
            desk.db.execute("UPDATE prepared_sources5689 SET state='unavailable', message=?, last_try=? WHERE source=?", ('Source illisible pour l’instant : les documents déjà connus restent affichés.', now, sid))
            if not desk.db.execute('SELECT 1 FROM prepared_sources5689 WHERE source=?', (sid,)).fetchone():
                store('unavailable', 'Source illisible pour l’instant : les documents déjà connus restent affichés.', ok=False)
            desk.db.commit()
        return
    # 5.6.95 (A05) : un état « partiel » sans ligne illisible (« Collecte reprise depuis le début », posé par la réparation 5.6.93 sur une source
    # sans journal paginé) n'a rien à attendre : la source est relue une fois, ce qui rend son état réel au lieu d'un avertissement permanent.
    reprise = bool(row) and row['state'] == 'partial' and not row['errors']
    if row and row['signature'] == signature and row['state'] != 'unavailable' and not reprise and not force:
        return
    errors, count = 0, 0
    try:
        for ident, read in src['rows'](desk):
            try:
                with _atomique(desk):
                    for entry in read():
                        observe(desk, entry)
                        count += 1
            except (Stop, ValueError, TypeError, KeyError, AttributeError, sqlite3.IntegrityError) as ex:
                errors += 1
                # l'identifiant et la nature de l'erreur, jamais le contenu de la ligne ni le texte de l'exception (il peut citer ce contenu)
                desk.audit('livrables5689_ligne_illisible', {'source': sid, 'ligne': str(ident)[:80], 'cause': type(ex).__name__})
    except Exception as ex:   # noqa: BLE001 — une source en panne ne vide pas les autres
        desk.audit('livrables5689_source_illisible', {'source': sid, 'cause': type(ex).__name__})
        store('unavailable', 'Source illisible pour l’instant : les documents déjà connus restent affichés.', signature, errors, count, ok=False)
        return
    store('partial' if errors else 'fresh', ('%d ligne(s) illisible(s) ignorée(s) ; les autres documents restent affichés.' % errors) if errors else '', signature, errors, count)


def synchroniser(desk, force=False, fond=False):
    """Lecture des journaux locaux des producteurs, sans réseau et sans écrire aucun fichier. Coût négligeable quand rien n'a changé (une signature par source).
    5.6.93 (N03) : à l'affichage, une page par source au plus ; ``fond`` (travail d'arrière-plan) ou ``force`` lisent jusqu'à 40 pages par source."""
    if not (force or fond):   # 5.6.100 (intégration, § 24) : à l'affichage, une seule synchronisation par page (Aujourd'hui en demandait quatre)
        from .affichage5696 import memo_requete
        return memo_requete(('livrables5689:synchroniser', str(desk.c.get('state_dir', ''))), lambda: _synchroniser(desk, False, False))
    return _synchroniser(desk, force, fond)


def _synchroniser(desk, force, fond):
    from . import livrables_sources5689 as sources
    ensure_schema(desk)
    try:
        for src in sources.SOURCES:
            _sync_source(desk, src, force, fond)
        importer_marqueurs(desk)
        desk.db.commit()
        return True
    except sqlite3.Error:
        if desk.db.in_transaction:
            desk.db.rollback()
        return False      # base occupée : la lecture continue sur le dernier état, signalé comme ancien


def migrer(desk):
    """Reprise complète, répétable : additive, sans fichier créé ni certification."""
    return synchroniser(desk, force=True)


def etat_sources(desk, synced=True):
    from . import livrables_sources5689 as sources
    ensure_schema(desk)
    stored = {r['source']: r for r in desk.db.execute('SELECT * FROM prepared_sources5689')}
    out = []
    for src in sources.SOURCES:
        row = stored.get(src['id'])
        state, message = (row['state'], row['message']) if row else ('stale', 'Pas encore lue.')
        if state == 'fresh':
            try:
                age = src['age'](desk) if src.get('age') else None
                running = src['running'](desk) if src.get('running') else 0
            except Exception:   # noqa: BLE001
                age, running = None, 0
            count = desk.db.execute('SELECT COUNT(*) FROM prepared_source_links5689 WHERE link_kind=\'producteur\' AND source_service=?', (src.get('service', src['id']),)).fetchone()[0]
            if age is False:
                state, message = 'unavailable', src.get('down', 'Source injoignable : les éléments déjà connus restent affichés.')
            elif age is not None and age > src.get('max_age', 3600):
                state, message = 'stale', 'Dernière lecture il y a plus d’une heure.'
            elif running:
                state, message = 'running', '%d en cours de préparation.' % running
            elif not count:
                state, message = 'empty', 'Rien pour l’instant.'
        if not synced and state not in ('unavailable', 'partial'):
            state, message = 'stale', 'Base occupée : dernier état connu.'
        out.append({'id': src['id'], 'label': src['label'], 'state': state, 'message': message or 'À jour.', 'last_ok': row['last_ok'] if row else ''})
    return out


# -------------------------------------------------------------------------------------------- lecture
def charger(desk, ident, hidden=frozenset(), version_id=None, droits=None):
    """Résout un identifiant reçu : ligne ou document. Le contrôle d'accès précède tout le reste ; un dossier réservé répond comme tel, à chaque appel."""
    ensure_schema(desk)
    ident = str(ident or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident):
        raise Stop('livrable_absent')
    if version_id:
        row = desk.db.execute('SELECT * FROM prepared_items5689 WHERE version_id=? AND (document_id=? OR document_id=(SELECT document_id FROM prepared_items5689 WHERE id=?))', (str(version_id), ident, ident)).fetchone()
    else:
        row = desk.db.execute("SELECT * FROM prepared_items5689 WHERE document_id=? AND lifecycle IN ('current','archived') ORDER BY version_number DESC LIMIT 1", (ident,)).fetchone() or \
            desk.db.execute('SELECT * FROM prepared_items5689 WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Stop('version_inconnue' if version_id else 'livrable_absent')
    if row['matter_id'] in hidden:
        raise Stop('dossier_reserve')
    if hidden and (row['response_id'] or row['source_service'] == 'messagerie'):   # 5.6.100 (R04) : courriel rattaché ailleurs à un dossier
        from .acces_courriel56100 import cles_masquees                                   # réservé, ou brouillon d'origine non vérifiable : absent
        if (row['response_id'] and cles_masquees(desk, frozenset(hidden), [row['response_id']])) or                 (row['source_service'] == 'messagerie' and row['source_object_id'] not in brouillons_autorises(desk, hidden)):
            raise Stop('livrable_absent')
    if row['required_right'] and droits is not None and not droits.get(row['required_right']):
        raise Stop('role_insuffisant')
    return dict(row)


def etat_contrat(row):
    if row['lifecycle'] in ('replaced', 'archived'):
        return row['lifecycle']
    return {'verified': 'ready', 'failed': 'failed', 'partial': 'partial'}.get(row['production_state'], 'preparing')


def _emplacement(desk, row, matters):
    path = row['path']
    if not path:
        return ''
    root = (matters.get(row['matter_id']) or {}).get('path', '')
    return path[len(root):].lstrip('/') if root and path.startswith(root) else PurePosixPath(path).name


def carte(row, labels, matters, reps=(), desk=None, prefix=''):
    """Forme publique : aucun chemin complet, une référence opaque ; ``verified`` ne dit que la preuve de stockage."""
    state = etat_contrat(row)
    return {'id': row['id'], 'document_id': row['document_id'], 'version_id': row['version_id'], 'version': row['version_number'], 'matter_id': row['matter_id'], 'matter_label': labels.get(row['matter_id'], '') or ('Sans dossier' if not row['matter_id'] else row['matter_id']),
            'kind': row['kind'], 'nature': NATURES.get(row['kind'], 'Document'), 'title': row['title'], 'state': state, 'state_label': STATE[state], 'production_label': PRODUCTION.get(row['production_state'], ''), 'state_reason': row['state_reason'],
            'review': row['review_state'], 'review_label': REVIEW[row['review_state']], 'verified': row['production_state'] == 'verified', 'reserve': bool(row['reserve']), 'reserve_note': row['reserve_note'], 'protected': row['protected'],
            'location_ref': 'loc-' + row['id'], 'location': _emplacement(desk, row, matters), 'updated_at': row['source_at'] or row['updated'], 'href': prefix + '/livrables/' + row['document_id'],
            'formats': sorted({r['format'] for r in reps}), 'exports': sum(1 for r in reps if r['role'] == 'export'), 'source': row['source_service'], 'deposit_state': row['deposit_state'], 'response_id': row['response_id'],
            'controle': _controle(row)}


def _controle(row):
    """5.6.93 (N05) : état réel du suivi du fichier et actions possibles (Réessayer, Retrouver le fichier, Retirer du suivi) ; aucune requête au stockage."""
    from . import registre5693
    return registre5693.controle(row)


def _labels(desk):
    from .common import load_matters, matter_display
    try:
        matters = {str(m['id']): m for m in load_matters(desk.c)}
    except Stop:
        return {}, {}
    return {k: matter_display(m) for k, m in matters.items()}, matters


def _curseur(value):
    try:
        data = json.loads(base64.urlsafe_b64decode(str(value) + '=' * (-len(str(value)) % 4)).decode())
        return str(data[0]), str(data[1])
    except (ValueError, TypeError, IndexError, UnicodeDecodeError):
        raise Stop('curseur_invalide') from None


def _rights_clause(droits):
    excluded = [r for r in ('voir_honoraires',) if droits is not None and not droits.get(r)]
    return (" AND required_right NOT IN (%s)" % ','.join('?' for _ in excluded), excluded) if excluded else ('', [])


def reponses_masquees(desk, hidden):
    """5.6.100 (R04) : courriels (``response_id``) des livrables « courriel » que la personne ne peut pas voir d'après les rattachements
    ACTUELS du message et de son projet (le dossier copié dans la ligne du registre peut être vide ou ancien). Panne : ``controle_indisponible``."""
    if not hidden:
        return frozenset()
    from .acces_courriel56100 import cles_masquees
    try:
        cles = [r[0] for r in desk.db.execute("SELECT DISTINCT response_id FROM prepared_items5689 WHERE response_id<>''")]
    except sqlite3.Error:
        raise Stop('controle_indisponible') from None
    return cles_masquees(desk, frozenset(hidden), cles)


def brouillons_autorises(desk, hidden):
    """5.6.100 (R04) : brouillons de la messagerie (``mail:<UIDVALIDITY>:<UID>``) dont l'origine est vérifiée et visible pour la personne,
    d'après la copie locale de la boîte Brouillons ; un brouillon absent de la copie (origine non vérifiable) n'est pas restitué."""
    from .acces_courriel56100 import filtrer_brouillons_caches
    cache = desk.settings('cockpit530:drafts', None) or {}
    validity = str(cache.get('validity', ''))
    return frozenset('mail:%s:%s' % (validity, d.get('uid')) for d in filtrer_brouillons_caches(desk, frozenset(hidden), desk.c.get('mail') or {}, validity, cache.get('items', [])))


def _clause_courriels(desk, hidden):
    """Clause ajoutée AVANT comptes et LIMIT pour une personne cloisonnée : courriels masqués et brouillons non vérifiables retirés."""
    if not hidden:
        return '', []
    sql, params = '', []
    masquees = sorted(reponses_masquees(desk, hidden))
    if masquees:
        sql += ' AND response_id NOT IN (%s)' % ','.join('?' for _ in masquees)
        params += masquees
    permis = sorted(brouillons_autorises(desk, hidden))
    sql += " AND (source_service<>'messagerie'%s)" % ((' OR source_object_id IN (%s)' % ','.join('?' for _ in permis)) if permis else '')
    params += permis
    return sql, params


def _where(hidden, droits, matter=None, recherche='', etat='', desk=None):
    sql = " WHERE lifecycle IN ('archived')" if etat == 'archive' else " WHERE lifecycle='current'"
    params = []
    if hidden:
        sql += ' AND matter_id NOT IN (%s)' % ','.join('?' for _ in hidden)
        params += sorted(hidden)
    if desk is not None:   # 5.6.100 (R04) : courriels d'un dossier réservé (rattachements actuels) retirés avant comptes, extraits et LIMIT
        clause, extra = _clause_courriels(desk, hidden)
        sql += clause
        params += extra
    clause, extra = _rights_clause(droits)
    sql += clause
    params += extra
    if matter is not None:
        sql += ' AND matter_id=?'
        params.append(str(matter))
    if recherche:
        sql += ' AND lower(title) LIKE ?'
        params.append('%' + str(recherche).lower().replace('%', '').replace('_', '')[:80] + '%')
    return sql, params


def projection(desk, hidden=frozenset(), droits=None, matter=None, nature_filtre='', etat='', revue='', recence=None, recherche='', curseur='', limite=30, sync=True, prefix='', familles=None):
    """Endpoint agrégé : le contrôle d'accès entre dans la requête, avant comptes et extraits. Une source en panne donne ``complet: False``, jamais un zéro certain.
    5.6.100 (lot E, § 23) : ``familles`` (natures retenues) filtre dans la requête, avant comptes, curseur et limite (Produire : documents et notes, sans les courriels)."""
    ensure_schema(desk)
    synced = synchroniser(desk) if sync else True
    limite = max(1, min(int(limite or 30), 100))
    since = None
    if recence not in (None, '', 'tout'):
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(recence), 3650)))).isoformat()
        except (TypeError, ValueError):
            raise Stop('filtre_invalide') from None
    if nature_filtre and nature_filtre not in NATURES:
        raise Stop('filtre_invalide')
    base, params = _where(hidden, droits, matter, recherche, etat, desk=desk)
    scope = list(params)
    clause = base
    if familles is not None:
        retenues = [k for k in familles if k in NATURES]
        clause += (' AND kind IN (%s)' % ','.join('?' for _ in retenues)) if retenues else ' AND 0'
        scope += retenues
    if since:
        clause += ' AND source_at>=?'
        scope.append(since)
    if revue:
        if revue not in REVIEW:
            raise Stop('filtre_invalide')
        clause += ' AND review_state=?'
        scope.append(revue)
    if etat and etat != 'archive':
        wanted = {'pret': ('verified',), 'en_cours': ('preparing', 'published'), 'echec': ('failed', 'partial')}.get(etat)
        if not wanted:
            raise Stop('filtre_invalide')
        clause += ' AND production_state IN (%s)' % ','.join('?' for _ in wanted)
        scope += list(wanted)
    by_kind = {r[0]: r[1] for r in desk.db.execute('SELECT kind, COUNT(*) FROM prepared_items5689' + clause + ' GROUP BY kind', scope)}
    counts = {'par_nature': {k: by_kind.get(k, 0) for k in NATURES}, 'total': sum(by_kind.values())}
    limit = (datetime.now(timezone.utc) - timedelta(days=RECENCE)).isoformat()
    review_base = base + " AND review_state='to_review' AND production_state IN ('published','verified')"
    counts['a_relire'] = desk.db.execute('SELECT COUNT(*) FROM prepared_items5689' + review_base + ' AND source_at>=?', params + [limit]).fetchone()[0]
    counts['a_relire_anciens'] = desk.db.execute('SELECT COUNT(*) FROM prepared_items5689' + review_base + ' AND source_at<?', params + [limit]).fetchone()[0]
    listing = clause
    args = list(scope)
    if nature_filtre:
        listing += ' AND kind=?'
        args.append(nature_filtre)
    if curseur:
        at, ident = _curseur(curseur)
        listing += ' AND (source_at<? OR (source_at=? AND id<?))'
        args += [at, at, ident]
    rows = [dict(r) for r in desk.db.execute('SELECT * FROM prepared_items5689' + listing + ' ORDER BY source_at DESC, id DESC LIMIT ?', args + [limite + 1])]
    more = len(rows) > limite
    rows = rows[:limite]
    reps = {}
    if rows:
        for r in desk.db.execute('SELECT item_id, format, role FROM prepared_representations5689 WHERE item_id IN (%s)' % ','.join('?' for _ in rows), [x['id'] for x in rows]):
            reps.setdefault(r['item_id'], []).append(r)
    labels, matters = _labels(desk)
    sources = etat_sources(desk, synced)
    last = rows[-1] if rows else None
    next_cursor = base64.urlsafe_b64encode(json.dumps([last['source_at'], last['id']]).encode()).decode().rstrip('=') if more else None
    down = [s for s in sources if s['state'] in ('unavailable', 'partial', 'stale')]
    stamps = [s['last_ok'] for s in sources if s['last_ok']]
    return {'items': [carte(r, labels, matters, reps.get(r['id'], ()), desk, prefix) for r in rows], 'counts': counts, 'sources': sources, 'complet': not down, 'next_cursor': next_cursor,
            'filters': {'nature': nature_filtre, 'etat': etat, 'revue': revue, 'recence': recence or 'tout', 'recherche': recherche, 'dossier': matter, 'limite': limite},
            'scope': 'dossier' if matter is not None else 'cabinet', 'generated_at': _now(), 'synced_at': max(stamps) if stamps else None, 'revision': revision(desk, hidden, droits, matter)}


def revision(desk, hidden=frozenset(), droits=None, matter=None):
    """Jeton léger : la page interroge lui seul, et ne recharge liste et compteurs que s'il change ; la saisie en cours n'est pas touchée (UX11)."""
    ensure_schema(desk)
    base, params = _where(hidden, droits, matter, desk=desk)
    row = desk.db.execute('SELECT COUNT(*), COALESCE(MAX(updated),\'\') FROM prepared_items5689' + base, params).fetchone()
    return hashlib.sha256(('%s|%s' % (row[0], row[1])).encode()).hexdigest()[:16]


def a_relire(desk, labels=None, matters=None, jours=RECENCE):
    """Documents de la liste « À relire » d'Aujourd'hui : même projection que Produire et Dossier, sous la forme historique de ``cockpit530``."""
    from .cockpit530 import TYPE_LABELS
    ensure_schema(desk)
    synchroniser(desk)
    if labels is None:
        labels, matters = _labels(desk)
    since = (datetime.now(timezone.utc) - timedelta(days=jours)).isoformat()
    out = []
    hidden, droits = droits_requete()     # dossier réservé et honoraires : avant toute lecture, comme l'API
    base, params = _where(hidden, droits, desk=desk)
    for r in desk.db.execute("SELECT * FROM prepared_items5689" + base + " AND review_state='to_review' AND production_state IN ('published','verified') AND path<>'' AND source_at>=? AND kind<>'courriel' ORDER BY source_at DESC LIMIT 80", params + [since]):
        root = (matters or {}).get(r['matter_id'], {}).get('path', '')
        prov = json.loads(r['provenance'] or '{}')
        out.append({'id': r['legacy_item'] or 'doc:' + digest(r['path'])[:24], 'type': r['label'] or TYPE_LABELS.get(r['kind']) or ('Note' if r['kind'] == 'note' else NATURES.get(r['kind'], 'Document') if r['kind'] != 'autre_document' else 'Document'),
                    'title': r['title'], 'matter': r['matter_id'], 'matter_label': (labels or {}).get(r['matter_id'], ''), 'when': r['source_at'], 'path': r['path'], 'dest': r['path'][len(root):].lstrip('/') if root and r['path'].startswith(root) else r['path'],
                    'document_id': r['document_id'], 'version_id': r['version_id'], 'version': r['version_number'], 'reserve': bool(r['reserve']), 'reserve_note': r['reserve_note'], 'verified': r['production_state'] == 'verified', **({'request': prov['demande']} if prov.get('demande') else {})})
    return out


def droits_requete():
    """Droits de la personne, posés par le contrôle central sur chaque requête : les vues partagées filtrent comme l'API."""
    from .membres5650 import ai_denied, viewer_hidden
    return viewer_hidden() or frozenset(), {'voir_honoraires': 'factures' not in ai_denied()}


def lien_demande(desk, prefix, request):
    """Lien du détail d'une demande de document, s'il est au registre."""
    try:
        row = desk.db.execute("SELECT i.document_id FROM prepared_source_links5689 l JOIN prepared_items5689 i ON i.id=l.item_id WHERE l.link_kind='producteur' AND l.source_service='docrequest520' AND l.source_object_id=? ORDER BY i.version_number DESC LIMIT 1", (str(request),)).fetchone()
    except sqlite3.Error:
        return ''
    return prefix + '/livrables/' + row[0] if row else ''


def lien_chemin(desk, prefix, path, retour=''):
    """Lien du détail d'un fichier connu du registre, pour les pages productrices ; rien s'il ne l'est pas."""
    from urllib.parse import urlencode
    try:
        row = desk.db.execute("SELECT document_id FROM prepared_items5689 WHERE path=? ORDER BY version_number DESC LIMIT 1", (str(path),)).fetchone()
    except sqlite3.Error:
        return ''
    return prefix + '/livrables/' + row[0] + (('?' + urlencode({'retour': retour})) if retour else '') if row else ''


def reponse_deposee(desk):
    """Réponses dont le brouillon est déjà dans la messagerie : la carte « à décider » et celle du brouillon ne font qu'une, une seule décision (UX14)."""
    try:
        return {r[0] for r in desk.db.execute("SELECT response_id FROM prepared_items5689 WHERE response_id<>'' AND deposit_state='depose' AND lifecycle='current'")}
    except sqlite3.Error:
        return set()

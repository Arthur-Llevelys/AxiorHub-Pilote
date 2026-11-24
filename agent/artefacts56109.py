"""5.6.109 (lot V, F08) : livrables structurés (chronologies, tableaux arguments–preuves, consultations…) dans le registre canonique.

Un livrable structuré est un document du registre commun ``livrables5689`` : même ``document_id`` depuis Aujourd'hui (« Travail préparé »),
Courriels, Produire et Dossiers, même lecteur ``/livrables/{id}``, mêmes versions immuables. Ce module n'ajoute AUCUN second registre :
chaque enregistrement publie une version du registre (``livrables5689.publier_contenu``) et range le contenu JSON validé, lié par
``item_id`` à cette version, dans ``livrables_contenus56109`` — dans la même transaction (le reçu n'est rendu qu'après la validation).

Contrat des objets (dossier technique, INTEGRATION-PRODUIRE-ARTEFACTS109 § 3, contrôles repris de ``reference/artefacts109.py`` sans son
banc SQLite ``Depot``) :
- schéma fermé ``{kind, title, blocks}`` ; blocs ``heading``, ``paragraph`` et ``table`` seulement ; une ligne de tableau porte un identifiant
  stable, ses cellules, un statut (Trouvé dans le dossier, Répondu par vous, À confirmer, Inconnu) et ses sources ;
- une source est l'identifiant d'une VERSION du registre (``prepared_items5689.id``) du même dossier, résolue par le serveur avec les droits
  de la personne (dossier réservé, droit requis) ; jamais une liste de droits fournie par le navigateur ou par un modèle ; une version
  ancienne citant une source devenue interdite n'est pas restituée (``source_indisponible``) ;
- aucun HTML, aucun JavaScript, aucun calcul exécuté depuis les données : le rendu est fait par le code du produit (texte échappé). La
  fonction de code arbitraire (aperçu HTML/React généré) est DÉSACTIVÉE (``CODE_ARBITRAIRE``) tant que son isolation n'est pas qualifiée ;
- écriture : révision attendue (409 si périmée), clé d'idempotence (même clé et même contenu → même reçu ``replayed`` ; autre contenu →
  409), reçu ``{document_id, revision, durable, replayed}`` ; une modification crée toujours une nouvelle version, jamais une fusion.

Sous-agents (§ 7) : ``verifier_plan`` ferme rôles, nombre d'étapes, dépendances et budget ; ``planifier`` ne crée pas d'orchestrateur : le
plan validé devient une mission du moteur existant (``taches5614``, parcours « custom », autonomie « suggérer » : rien ne s'exécute sans
lancement explicite ; droits, pause et budget du moteur commun).
"""
import copy
import csv
from datetime import datetime, timezone
import hashlib
from html import escape as e
import io
import json
from pathlib import Path
import re
import uuid

from .common import Stop

PRODUCTEUR = 'artefacts56109'
FORMAT = 'axh-livrable/1'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,119}\Z')
CLE = re.compile(r'[A-Za-z0-9_.:-]{8,80}\Z')
DOCUMENT = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
ROLES_PLAN = {'cadrage', 'faits', 'recherche', 'redaction', 'controle', 'assemblage'}
KINDS = {'chronologie', 'arguments_preuves', 'consultation', 'contrat', 'obligations', 'audit', 'budget', 'bordereau', 'brief', 'checklist'}
STATUTS = {'trouve': 'Trouvé dans le dossier', 'repondu': 'Répondu par vous', 'a_confirmer': 'À confirmer', 'inconnu': 'Inconnu'}
# Nature du registre (filtres de Produire et d'Aujourd'hui) ; le libellé de la trame reste affiché comme type du livrable.
NATURE = {'chronologie': 'note', 'arguments_preuves': 'note', 'consultation': 'note', 'contrat': 'contrat', 'obligations': 'contrat', 'audit': 'note',
          'budget': 'note', 'bordereau': 'autre_document', 'brief': 'note', 'checklist': 'note'}
LIBELLES_KIND = {'chronologie': 'Chronologie', 'arguments_preuves': 'Arguments et preuves', 'consultation': 'Consultation', 'contrat': 'Analyse de contrat',
                 'obligations': 'Suivi des obligations', 'audit': 'Audit', 'budget': 'Budget d’honoraires', 'bordereau': 'Bordereau', 'brief': 'Brief d’audience',
                 'checklist': 'Liste de contrôle'}
ROLES_EDITION = ('administrateur', 'avocat', 'assistant')
ROLES_PLANIFICATION = ('administrateur', 'avocat')
MAX_CONTENU = 240_000            # octets du JSON canonique (le corps POST commun est borné à 260 000)
MAX_PLAN_ETAPES = 8
# Garde-fou (§ 6) : aucun programme généré (HTML, JavaScript, React, SVG actif) n'est exécuté ni affiché tant que l'isolation réseau et
# processus n'est pas qualifiée ; le rendu reste déclaratif. Ne passer à True qu'après la recette de sécurité décrite au § 6.
CODE_ARBITRAIRE = False
TYPES_CODE = {'html', 'react', 'jsx', 'javascript', 'script', 'svg', 'code', 'iframe'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS livrables_contenus56109(document_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision > 0), item_id TEXT NOT NULL,
  version_id TEXT NOT NULL, matter_id TEXT NOT NULL DEFAULT '', trame TEXT NOT NULL DEFAULT '', trame_version INTEGER NOT NULL DEFAULT 0,
  format TEXT NOT NULL, titre TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '[]',
  auteur TEXT NOT NULL, origine TEXT NOT NULL DEFAULT 'manuel', cree_le TEXT NOT NULL, PRIMARY KEY(document_id, revision), UNIQUE(item_id));
CREATE INDEX IF NOT EXISTS livrables_contenus56109_dossier ON livrables_contenus56109(matter_id, cree_le);
CREATE TABLE IF NOT EXISTS livrables_operations56109(acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, document_id TEXT NOT NULL,
  revision INTEGER NOT NULL, cree_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS livrables_plans56109(id TEXT PRIMARY KEY, document_id TEXT NOT NULL, matter_id TEXT NOT NULL DEFAULT '',
  mission_id TEXT NOT NULL DEFAULT '', plan TEXT NOT NULL, ordre TEXT NOT NULL, budget_tokens INTEGER NOT NULL, empreinte TEXT NOT NULL,
  acteur TEXT NOT NULL, cle TEXT NOT NULL, cree_le TEXT NOT NULL, UNIQUE(acteur, cle));
'''
TABLES = ('livrables_contenus56109', 'livrables_operations56109', 'livrables_plans56109')


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def ensure_schema(desk):
    """Tables du lot, au démarrage (``Desk._prepare``) ; sans changement, aucune écriture (jamais pendant une lecture)."""
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    if any(t not in existants for t in TABLES) or 'livrables_contenus56109_dossier' not in existants:
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


# ------------------------------------------------------------------------------------------------ contrat des objets
def _id(valeur, code='identifiant_invalide'):
    if not isinstance(valeur, str) or not ID.fullmatch(valeur):
        raise Stop(code)
    return valeur


def _texte(valeur, limite=20000):
    if (not isinstance(valeur, str) or len(valeur) > limite or any(ord(c) < 32 and c not in '\t\r\n' for c in valeur)
            or any(0xD800 <= ord(c) <= 0xDFFF for c in valeur)):
        raise Stop('texte_invalide')
    return valeur


def canonique(valeur):
    try:
        brut = json.dumps(valeur, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise Stop('json_invalide') from None
    if len(brut.encode('utf-8')) > MAX_CONTENU:
        raise Stop('contenu_trop_volumineux')
    return brut


def normaliser(payload):
    """Forme canonique avant contrôle : une ligne de tableau donnée comme simple liste de cellules (trames et anciens exports) reçoit un
    identifiant stable, le statut « Inconnu » et aucune source. Rien d'autre n'est complété : un champ inconnu reste refusé ensuite."""
    if not isinstance(payload, dict) or not isinstance(payload.get('blocks'), list):
        return payload
    out = copy.deepcopy(payload)
    for b in out['blocks']:
        if isinstance(b, dict) and b.get('type') == 'table' and isinstance(b.get('rows'), list):
            pris = {r.get('id') for r in b['rows'] if isinstance(r, dict)}
            n = 0
            for i, r in enumerate(b['rows']):
                if isinstance(r, list):
                    n += 1
                    while 'L%d' % n in pris:
                        n += 1
                    b['rows'][i] = {'id': 'L%d' % n, 'cells': r, 'statut': 'inconnu', 'source_ids': []}
                    pris.add('L%d' % n)
    return out


def _sources(sources, source_visible):
    if not isinstance(sources, list) or len(sources) > 30:
        raise Stop('sources_invalides')
    for s in sources:
        _id(s, 'sources_invalides')
        if not source_visible(s):
            raise Stop('source_indisponible')


def verifier_contenu(payload, source_visible):
    """Contrôle fermé (repris de ``reference/artefacts109.verifier_contenu`` et complété par ligne). ``source_visible(id)`` : la version du
    registre existe, appartient au dossier du livrable et reste visible pour la personne, à cet instant. Rend le JSON canonique."""
    if not isinstance(payload, dict) or set(payload) != {'kind', 'title', 'blocks'}:
        raise Stop('schema_invalide')
    if not isinstance(payload['kind'], str) or payload['kind'] not in KINDS:
        raise Stop('type_inconnu')
    _texte(payload['title'], 180)
    if not payload['title'].strip():
        raise Stop('titre_requis')
    blocks = payload['blocks']
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 100:
        raise Stop('blocs_invalides')
    ids = set()
    for b in blocks:
        if not isinstance(b, dict):
            raise Stop('bloc_invalide')
        if isinstance(b.get('type'), str) and b['type'].lower() in TYPES_CODE:
            raise Stop('code_arbitraire_desactive')      # § 6 : jamais de programme généré dans un livrable
        if not {'id', 'type', 'source_ids'} <= set(b):
            raise Stop('bloc_incomplet')
        if _id(b['id']) in ids:
            raise Stop('bloc_duplique')
        ids.add(b['id'])
        _sources(b['source_ids'], source_visible)
        if b['type'] in ('paragraph', 'heading'):
            if set(b) != {'id', 'type', 'source_ids', 'text'}:
                raise Stop('champ_inconnu')
            _texte(b['text'])
        elif b['type'] == 'table':
            if set(b) != {'id', 'type', 'source_ids', 'columns', 'rows'}:
                raise Stop('champ_inconnu')
            cols, rows = b['columns'], b['rows']
            if not isinstance(cols, list) or not 1 <= len(cols) <= 12:
                raise Stop('colonnes_invalides')
            for x in cols:
                _texte(x, 120)
            if not isinstance(rows, list) or len(rows) > 1000:
                raise Stop('lignes_invalides')
            lignes = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != {'id', 'cells', 'statut', 'source_ids'}:
                    raise Stop('ligne_invalide')
                if _id(row['id'], 'ligne_invalide') in lignes:
                    raise Stop('ligne_dupliquee')
                lignes.add(row['id'])
                if not isinstance(row['cells'], list) or len(row['cells']) != len(cols):
                    raise Stop('ligne_invalide')
                for x in row['cells']:
                    _texte(x)
                if row['statut'] not in STATUTS:
                    raise Stop('statut_inconnu')
                _sources(row['source_ids'], source_visible)
        else:
            raise Stop('rendu_non_autorise')
    return canonique(payload)


def sources_de(payload):
    out = []
    for b in payload.get('blocks') or ():
        for s in b.get('source_ids') or ():
            out.append(s)
        for r in b.get('rows') or ():
            if isinstance(r, dict):
                out.extend(r.get('source_ids') or ())
    return sorted(set(out))


def executer_code(*_args, **_kwargs):
    """Fonction de code arbitraire (aperçu HTML/React généré) : désactivée tant que son isolation n'est pas qualifiée (§ 6). Elle ne prend
    aucun paramètre en compte et ne lance rien ; le pont Craft de même origine n'est jamais offert à un programme généré."""
    if not CODE_ARBITRAIRE:
        raise Stop('code_arbitraire_desactive')
    raise Stop('code_arbitraire_non_qualifie')     # même activée par erreur, aucune exécution n'est câblée ici


def capacites():
    return {'rendu_declaratif': True, 'code_genere': bool(CODE_ARBITRAIRE), 'exports': ['json', 'md', 'csv'], 'format': FORMAT}


# ------------------------------------------------------------------------------------------------ trames (douze modèles de structure)
_TRAMES = {}


def chemin_trames():
    return Path(__file__).resolve().parent / 'data' / 'modeles_livrables56109.json'


def trames():
    """Douze trames déclaratives (structure, colonnes, question d'information manquante) ; à valider par le cabinet, sans texte de loi."""
    p = chemin_trames()
    st = p.stat()
    cle = (st.st_size, st.st_mtime)
    if 'data' not in _TRAMES or _TRAMES.get('cle') != cle:
        data = json.loads(p.read_text(encoding='utf-8'))
        _TRAMES.update(cle=cle, data=data, index={m['id']: m for m in data['modeles']})
    return _TRAMES['data']['modeles']


def trame(ident):
    trames()
    m = _TRAMES['index'].get(str(ident or ''))
    if not m:
        raise Stop('trame_inconnue')
    return m


def payload_initial(ident, titre=''):
    m = trame(ident)
    payload = normaliser(copy.deepcopy(m['default_payload']))
    if titre:
        payload['title'] = titre
    return payload


def trames_publiques():
    return [{'id': m['id'], 'label': m['label'], 'kind': m['kind'], 'pratique': m['practice'], 'colonnes': m['columns'], 'version': m['version'],
             'statut': m['status'], 'question': m['missing_information_question'], 'competence': m['default_skill'],
             'effets_externes': bool(m['external_effects'])} for m in trames()]


# ------------------------------------------------------------------------------------------------ droits
def _neutre(fn, *args, **kwargs):
    """Objet absent et objet interdit : même ``introuvable`` (404 neutre)."""
    try:
        return fn(*args, **kwargs)
    except Stop as ex:
        if str(ex).split(':')[0] in ('livrable_absent', 'dossier_reserve', 'version_inconnue', 'role_insuffisant', 'introuvable'):
            raise Stop('introuvable') from None
        raise


def charger(desk, document_id, hidden=frozenset(), droits=None, version_id=None):
    from . import livrables5689 as liv
    if not DOCUMENT.fullmatch(str(document_id or '')):
        raise Stop('introuvable')
    return _neutre(liv.charger, desk, str(document_id), hidden, version_id=version_id, droits=droits)


def source_visible(desk, matter, hidden, droits):
    """Fabrique du contrôle de source : version du registre, du même dossier, visible pour la personne (droits relus à chaque appel)."""
    def visible(ident):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(ident)):
            return False
        row = desk.db.execute('SELECT id, matter_id, required_right FROM prepared_items5689 WHERE id=?', (str(ident),)).fetchone()
        if not row or row['matter_id'] != matter or row['matter_id'] in hidden:
            return False
        if row['required_right'] and droits is not None and not droits.get(row['required_right']):
            return False
        return True
    return visible


def _dossier(desk, matter, hidden):
    from .common import load_matters
    matter = str(matter or '')
    if not matter:
        raise Stop('dossier_requis')
    if matter in hidden:
        raise Stop('introuvable')
    try:
        ids = {str(m['id']) for m in load_matters(desk.c)}
    except Stop:
        raise Stop('registre_dossiers_indisponible') from None
    if matter not in ids:
        raise Stop('introuvable')
    return matter


# ------------------------------------------------------------------------------------------------ écriture
def _empreinte(**champs):
    return hashlib.sha256(canonique(champs).encode('utf-8')).hexdigest()


def ecrire(desk, acteur, role, hidden, droits, payload, cle, revision, document_id=None, matter=None, trame_id='', origine='manuel'):
    """Version nouvelle d'un livrable structuré, ou création (``document_id`` absent, ``revision`` 0). Reçu durable rendu APRÈS la validation
    de la transaction qui publie la version du registre et range le contenu ; rejoue le même reçu pour la même clé et le même contenu."""
    from . import livrables5689 as liv
    if role not in ROLES_EDITION:
        raise Stop('role_interdit')
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    if type(revision) is not int or revision < 0:
        try:
            revision = int(str(revision))
        except (TypeError, ValueError):
            raise Stop('revision_invalide') from None
        if revision < 0:
            raise Stop('revision_invalide')
    ensure_schema(desk)
    if document_id:
        row = charger(desk, document_id, hidden, droits)
        if row['source_service'] != PRODUCTEUR:
            raise Stop('format_non_modifiable')       # un document Word ou PDF se modifie par son éditeur ou par proposition, jamais ici
        matter, document_id = row['matter_id'], row['document_id']
        courant = desk.db.execute('SELECT trame, trame_version FROM livrables_contenus56109 WHERE document_id=? ORDER BY revision DESC LIMIT 1',
                                  (document_id,)).fetchone()
        trame_id = courant['trame'] if courant else ''
    else:
        if revision != 0:
            raise Stop('version_perimee')
        matter = _dossier(desk, matter, hidden)
        if trame_id:
            trame(trame_id)
    payload = normaliser(payload)
    brut = verifier_contenu(payload, source_visible(desk, matter, hidden, droits))
    empreinte = _empreinte(document=document_id or 'nouveau', matter=matter, revision=revision, trame=trame_id, payload=payload)
    compte = str(acteur)[:160]
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        op = desk.db.execute('SELECT empreinte, document_id, revision FROM livrables_operations56109 WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
        if op:
            if op['empreinte'] != empreinte:
                raise Stop('cle_reutilisee')
            desk.db.commit()
            return {'document_id': op['document_id'], 'revision': op['revision'], 'durable': True, 'replayed': True}
        if not document_id:
            document_id = 'liv-' + uuid.uuid4().hex[:24]
        courante = desk.db.execute('SELECT COALESCE(MAX(revision),0) FROM livrables_contenus56109 WHERE document_id=?', (document_id,)).fetchone()[0]
        if courante != revision:
            raise Stop('version_perimee')
        if revision:
            charger(desk, document_id, hidden, droits)   # recontrôle juste avant la publication (révocation pendant la requête)
        nouvelle = revision + 1
        sha = hashlib.sha256(brut.encode('utf-8')).hexdigest()
        version_id = 'contenu:%d:%s' % (nouvelle, sha[:24])
        m = trame(trame_id) if trame_id else None
        item_id = liv.publier_contenu(desk, document_id, version_id, {
            'revision': nouvelle, 'matter': matter, 'kind': NATURE.get(payload['kind'], 'note'), 'label': m['label'] if m else LIBELLES_KIND[payload['kind']],
            'title': payload['title'], 'owner': compte, 'right': 'voir_honoraires' if payload['kind'] == 'budget' else '',
            'sources': sources_de(payload), 'provenance': {'producteur': PRODUCTEUR, 'revision': nouvelle, 'format': FORMAT, 'trame': trame_id,
                                                           'trame_version': m['version'] if m else 0, 'origine': origine, 'sha256': sha}})
        desk.db.execute('INSERT INTO livrables_contenus56109(document_id,revision,item_id,version_id,matter_id,trame,trame_version,format,titre,payload,sha256,sources,'
                        'auteur,origine,cree_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (document_id, nouvelle, item_id, version_id, matter, trame_id, m['version'] if m else 0, FORMAT, payload['title'], brut, sha,
                         json.dumps(sources_de(payload)), compte, origine, _maintenant()))
        desk.db.execute('INSERT INTO livrables_operations56109(acteur,cle,empreinte,document_id,revision,cree_le) VALUES(?,?,?,?,?,?)',
                        (compte, cle, empreinte, document_id, nouvelle, _maintenant()))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    desk.audit('livrable56109_version', {'document': document_id, 'revision': nouvelle, 'sha256': sha, 'par': compte, 'origine': origine})
    try:   # la liste « À relire » unique suit la nouvelle version (jamais bloquant)
        from .relecture56101 import projeter_apres_ecriture
        projeter_apres_ecriture(desk, 'document', document_id)
    except Exception:   # noqa: BLE001
        pass
    return {'document_id': document_id, 'revision': nouvelle, 'durable': True, 'replayed': False, 'version_id': version_id, 'sha256': sha}


def creer_depuis_trame(desk, acteur, role, hidden, droits, trame_id, matter, titre, cle):
    """« Nouveau livrable » depuis une trame : création manuelle, possible IA en pause (aucun appel de modèle)."""
    titre = _texte(str(titre or '').strip() or trame(trame_id)['label'], 180)
    return ecrire(desk, acteur, role, hidden, droits, payload_initial(trame_id, titre), cle, 0, None, matter, trame_id, 'trame')


def operation(desk, acteur, cle, hidden=frozenset(), droits=None):
    """Réconciliation d'une réponse perdue : état durable de la clé (aucun rejeu de mutation) ; clé inconnue ou document interdit : 404."""
    ensure_schema(desk)
    op = desk.db.execute('SELECT document_id, revision FROM livrables_operations56109 WHERE acteur=? AND cle=?', (str(acteur)[:160], str(cle or ''))).fetchone()
    if not op:
        raise Stop('introuvable')
    charger(desk, op['document_id'], hidden, droits)
    return {'document_id': op['document_id'], 'revision': op['revision'], 'durable': True, 'replayed': True}


# ------------------------------------------------------------------------------------------------ lecture
def _ligne_contenu(desk, document_id, revision=None, item_id=None):
    if item_id:
        r = desk.db.execute('SELECT * FROM livrables_contenus56109 WHERE item_id=?', (item_id,)).fetchone()
    elif revision:
        r = desk.db.execute('SELECT * FROM livrables_contenus56109 WHERE document_id=? AND revision=?', (document_id, int(revision))).fetchone()
    else:
        r = desk.db.execute('SELECT * FROM livrables_contenus56109 WHERE document_id=? ORDER BY revision DESC LIMIT 1', (document_id,)).fetchone()
    return dict(r) if r else None


def _table_presente(desk):
    return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='livrables_contenus56109'").fetchone())


def lire(desk, document_id, hidden=frozenset(), droits=None, revision=None):
    """Contenu d'une version : droits du document puis de CHAQUE source relus maintenant ; aucune écriture (lecture seule)."""
    row = charger(desk, document_id, hidden, droits)
    if row['source_service'] != PRODUCTEUR or not _table_presente(desk):
        raise Stop('introuvable')
    if revision not in (None, ''):
        try:
            revision = int(revision)
        except (TypeError, ValueError):
            raise Stop('introuvable') from None
    c = _ligne_contenu(desk, row['document_id'], revision or None)
    if not c:
        raise Stop('introuvable')
    payload = json.loads(c['payload'])
    verifier = source_visible(desk, c['matter_id'], hidden, droits)
    for s in sources_de(payload):
        if not verifier(s):
            raise Stop('source_indisponible')        # une ancienne version n'est pas un instantané exempté des droits
    derniere = desk.db.execute('SELECT MAX(revision) FROM livrables_contenus56109 WHERE document_id=?', (row['document_id'],)).fetchone()[0]
    return {'document_id': row['document_id'], 'revision': c['revision'], 'derniere_revision': derniere, 'version_id': c['version_id'], 'sha256': c['sha256'],
            'titre': c['titre'], 'trame': c['trame'], 'trame_version': c['trame_version'], 'format': c['format'], 'matter_id': c['matter_id'],
            'auteur': c['auteur'], 'origine': c['origine'], 'cree_le': c['cree_le'], 'payload': payload, 'statuts': STATUTS, 'capacites': capacites()}


def contenu_de_version(desk, row, hidden=frozenset(), droits=None):
    """Contenu rattaché à une version du registre (lecteur, comparaison) ; None si la version n'est pas un livrable structuré."""
    if row.get('source_service') != PRODUCTEUR or not _table_presente(desk):
        return None
    c = _ligne_contenu(desk, row['document_id'], item_id=row['id'])
    if not c:
        return None
    payload = json.loads(c['payload'])
    verifier = source_visible(desk, c['matter_id'], hidden, droits)
    if any(not verifier(s) for s in sources_de(payload)):
        raise Stop('source_indisponible')
    return {**c, 'payload': payload}


# ------------------------------------------------------------------------------------------------ rendus déclaratifs (texte échappé)
def _sources_html(ids, prefix=''):
    if not ids:
        return ''
    return '<span class="v109-sources">%s</span>' % ' '.join('<span class="v109-source">%s</span>' % e(s[:12]) for s in ids)


def rendu_html(payload, prefix=''):
    """HTML statique du livrable, produit par le code du logiciel : aucun HTML venu des données (tout est échappé), aucun script."""
    out = ['<article class="v109-livrable"><h1>%s</h1>' % e(payload['title'])]
    for b in payload['blocks']:
        if b['type'] == 'heading':
            out.append('<h2>%s</h2>%s' % (e(b['text']), _sources_html(b['source_ids'])))
        elif b['type'] == 'paragraph':
            out.append('<p>%s</p>%s' % (e(b['text']), _sources_html(b['source_ids'])))
        else:
            tete = ''.join('<th scope="col">%s</th>' % e(c) for c in b['columns']) + '<th scope="col">Statut</th><th scope="col">Sources</th>'
            lignes = ''.join('<tr data-ligne="%s">%s<td>%s</td><td>%d</td></tr>' % (
                e(r['id'], quote=True), ''.join('<td>%s</td>' % e(x) for x in r['cells']), e(STATUTS[r['statut']]), len(r['source_ids'])) for r in b['rows'])
            vide = '' if b['rows'] else '<tr><td colspan="%d">Aucune ligne pour l’instant.</td></tr>' % (len(b['columns']) + 2)
            out.append('<table class="v109-table"><thead><tr>%s</tr></thead><tbody>%s%s</tbody></table>' % (tete, lignes, vide))
    out.append('</article>')
    return ''.join(out)


def texte_lignes(payload):
    """Lignes de texte d'une version, pour « Comparer » (une ligne par titre, paragraphe et ligne de tableau)."""
    out = [payload['title']]
    for b in payload['blocks']:
        if b['type'] in ('heading', 'paragraph'):
            out.append(b['text'])
        else:
            out.append(' | '.join(b['columns']))
            out.extend('[%s] %s | %s' % (r['id'], ' | '.join(r['cells']), STATUTS[r['statut']]) for r in b['rows'])
    return out


def _md(x):
    return str(x).replace('\\', '\\\\').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def _csv_cellule(x):
    x = str(x)
    return "'" + x if x[:1] in ('=', '+', '-', '@', '\t', '\r') else x     # aucune formule importée sans choix explicite


def exporter(desk, document_id, fmt, hidden=frozenset(), droits=None, revision=None):
    """Export de données d'une version (JSON, Markdown, CSV), droits d'export relus ; l'export n'envoie rien et ne crée aucune ligne de livrable."""
    if droits is not None and not droits.get('exporter'):
        raise Stop('droit_export_retire')
    if fmt not in ('json', 'md', 'csv'):
        raise Stop('format_non_disponible')
    c = lire(desk, document_id, hidden, droits, revision)
    p = c['payload']
    base = re.sub(r'[^A-Za-z0-9_-]+', '-', p['title'])[:60].strip('-') or 'livrable'
    nom = '%s-v%d.%s' % (base, c['revision'], fmt)
    if fmt == 'json':
        corps = json.dumps({'format': FORMAT, 'document_id': c['document_id'], 'revision': c['revision'], 'version_id': c['version_id'], 'sha256': c['sha256'],
                            'trame': c['trame'], 'trame_version': c['trame_version'], 'sources': sources_de(p), 'contenu': p}, ensure_ascii=False, indent=1)
        return corps.encode('utf-8'), 'application/json; charset=utf-8', nom
    if fmt == 'md':
        lignes = ['# ' + _md(p['title']), '', '_Version %d · %s · sources : %s_' % (c['revision'], c['version_id'], ', '.join(sources_de(p)) or 'aucune'), '']
        for b in p['blocks']:
            if b['type'] == 'heading':
                lignes += ['## ' + _md(b['text']), '']
            elif b['type'] == 'paragraph':
                lignes += [_md(b['text']), '']
            else:
                cols = list(b['columns']) + ['Statut', 'Sources']
                lignes += ['| ' + ' | '.join(_md(x) for x in cols) + ' |', '|' + '---|' * len(cols)]
                lignes += ['| ' + ' | '.join([_md(x) for x in r['cells']] + [STATUTS[r['statut']], ', '.join(r['source_ids'])]) + ' |' for r in b['rows']]
                lignes.append('')
        return '\n'.join(lignes).encode('utf-8'), 'text/markdown; charset=utf-8', nom
    tampon = io.StringIO()
    w = csv.writer(tampon, delimiter=';', lineterminator='\r\n')
    for b in p['blocks']:
        if b['type'] != 'table':
            continue
        w.writerow([_csv_cellule(x) for x in ['Ligne'] + list(b['columns']) + ['Statut', 'Sources']])
        for r in b['rows']:
            w.writerow([_csv_cellule(x) for x in [r['id']] + list(r['cells']) + [STATUTS[r['statut']], ' '.join(r['source_ids'])]])
    return ('\ufeff' + tampon.getvalue()).encode('utf-8'), 'text/csv; charset=utf-8', nom


# ------------------------------------------------------------------------------------------------ sous-agents : plan fermé sous une mission
def verifier_plan(etapes, budget_tokens=24000):
    """Plan fermé (repris de ``reference/artefacts109.verifier_plan``) : rôles, nombre, dépendances, budget ; refuse cycles, rôles inconnus,
    descendants libres et assemblage sans contrôle après la rédaction. L'exécution passe ensuite par le moteur de missions, jamais par eval."""
    if type(budget_tokens) is not int or not 1 <= budget_tokens <= 100000:
        raise Stop('budget_invalide')
    if not isinstance(etapes, list) or not 1 <= len(etapes) <= MAX_PLAN_ETAPES:
        raise Stop('plan_trop_long')
    nodes, total = {}, 0
    for x in etapes:
        if not isinstance(x, dict) or set(x) != {'id', 'role', 'depends_on', 'max_tokens'}:
            raise Stop('etape_invalide')
        key = _id(x['id'], 'etape_invalide')
        if key in nodes or not isinstance(x['role'], str) or x['role'] not in ROLES_PLAN:
            raise Stop('role_ou_id_invalide')
        if type(x['max_tokens']) is not int or not 1 <= x['max_tokens'] <= 12000:
            raise Stop('budget_invalide')
        deps = x['depends_on']
        if not isinstance(deps, list) or len(deps) > MAX_PLAN_ETAPES or len(set(map(str, deps))) != len(deps):
            raise Stop('dependances_invalides')
        for d in deps:
            _id(d, 'dependances_invalides')
        nodes[key] = x
        total += x['max_tokens']
    if total > budget_tokens:
        raise Stop('budget_depasse')
    done, ordre = set(), []
    while len(done) < len(nodes):
        prets = [k for k, x in nodes.items() if k not in done and set(x['depends_on']) <= done]
        if not prets:
            raise Stop('cycle_ou_dependance_absente')
        ordre.extend(prets)
        done.update(prets)

    def ancetres(key):
        parents, vus = list(nodes[key]['depends_on']), set()
        while parents:
            k = parents.pop()
            if k not in vus:
                vus.add(k)
                parents.extend(nodes[k]['depends_on'])
        return vus
    for key, x in nodes.items():
        if x['role'] == 'assemblage':
            vus = ancetres(key)
            controles = [k for k in vus if nodes[k]['role'] == 'controle']
            if not controles:
                raise Stop('controle_requis')
            for brouillon in (k for k in vus if nodes[k]['role'] == 'redaction'):
                if not any(brouillon in ancetres(c) for c in controles):
                    raise Stop('controle_apres_redaction_requis')
    return {'order': ordre, 'max_tokens': total, 'steps': len(nodes)}


# rôle du plan → (rôle, type de tâche, type de résultat, consigne) du moteur ``taches5614`` (aucun rôle ni outil nouveau)
CORRESPONDANCE = {
    'cadrage': ('superviseur', 'cadrage', 'cadrage', 'Cadrer la demande : résultat attendu, informations manquantes, sources utiles du dossier.'),
    'faits': ('analyste_faits', 'analyse', 'chronologie', 'Extraire les faits et preuves des pièces autorisées, avec leurs sources.'),
    'recherche': ('chercheur', 'recherche', 'recherche', 'Rechercher le droit applicable et citer les sources vérifiables.'),
    'redaction': ('redacteur', 'section', 'section', 'Rédiger la partie demandée à partir des résultats des étapes précédentes.'),
    'controle': ('controleur', 'analyse', 'rapport_controle', 'Contrôler la rédaction effective : citations, assertions sans source, nombres et mentions.'),
    'assemblage': ('superviseur', 'assemblage', 'assemblage', 'Assembler le livrable contrôlé sans ajouter d’argumentation non relue.'),
}


def planifier(desk, acteur, role, hidden, droits, document_id, plan, budget_tokens, cle):
    """Plan validé → mission unique du moteur existant (``taches5614.create``, autonomie « suggérer » : rien ne part sans lancement).
    Même clé et même plan : même mission ; autre plan : 409. Pause de l'IA, droits et budget restent ceux du moteur commun."""
    from . import pause5629, taches5614
    if role not in ROLES_PLANIFICATION:
        raise Stop('role_interdit')
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    ensure_schema(desk)
    row = charger(desk, document_id, hidden, droits)
    validation = verifier_plan(plan, budget_tokens)
    empreinte = _empreinte(document=row['document_id'], version=row['version_id'], plan=plan, budget=budget_tokens)
    compte = str(acteur)[:160]
    old = desk.db.execute('SELECT * FROM livrables_plans56109 WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if old:
        if old['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        return {'mission_id': old['mission_id'], 'ordre': json.loads(old['ordre']), 'etapes': len(json.loads(old['plan'])), 'max_tokens': validation['max_tokens'],
                'replayed': True}
    pause5629.check_enqueue(desk, 'mission5614', {'matter': row['matter_id']}, None, origin='user')   # demande de l'avocat : acceptée, elle attend la reprise
    codes = {x['id']: 'T%02d' % (i + 1) for i, x in enumerate(plan)}
    taches = []
    for x in plan:
        r, t, sortie, consigne = CORRESPONDANCE[x['role']]
        taches.append({'code': codes[x['id']], 'title': '%s (%s)' % (x['id'], x['role']), 'role': r, 'type': t, 'output': sortie,
                       'depends': [codes[d] for d in x['depends_on']], 'instruction': consigne + ' Budget : %d jetons au plus.' % x['max_tokens'],
                       'required': True})
    data = {'instruction': 'Préparer le livrable « %s » (version %d) selon le plan validé ; méthode : Assistant juridique.' % (row['title'][:200], row['version_number']),
            'request_key': hashlib.sha256(('%s|%s' % (compte, cle)).encode()).hexdigest()[:40], 'matter': row['matter_id'], 'parcours': 'custom', 'tasks': taches,
            'autonomy': 'suggest', 'budget_calls': max(1, len(plan) * 4),
            'answers': {'livrable56109': {'document_id': row['document_id'], 'version_id': row['version_id'], 'budget_tokens': budget_tokens, 'ordre': validation['order']}}}
    mission = taches5614.create(desk, data, owner=compte)
    desk.db.execute('INSERT INTO livrables_plans56109(id,document_id,matter_id,mission_id,plan,ordre,budget_tokens,empreinte,acteur,cle,cree_le) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (uuid.uuid4().hex, row['document_id'], row['matter_id'], mission['id'], json.dumps(plan, ensure_ascii=False), json.dumps(validation['order']),
                     budget_tokens, empreinte, compte, cle, _maintenant()))
    desk.db.commit()
    desk.audit('livrable56109_plan', {'document': row['document_id'], 'mission': mission['id'], 'etapes': len(plan), 'max_tokens': validation['max_tokens']})
    return {'mission_id': mission['id'], 'ordre': validation['order'], 'etapes': len(plan), 'max_tokens': validation['max_tokens'], 'replayed': False}


# ------------------------------------------------------------------------------------------------ routes ``m56109/livrables/``
PREFIXE = 'm56109/livrables/'
STATUTS_HTTP = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'role_interdit': '403 Forbidden',
                'droit_export_retire': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'requete_trop_longue': '413 Payload Too Large',
                'contenu_trop_volumineux': '413 Payload Too Large', 'controle_acces_indisponible': '503 Service Unavailable',
                'registre_dossiers_indisponible': '503 Service Unavailable', 'code_arbitraire_desactive': '403 Forbidden'}
MESSAGES = {
    'introuvable': 'Livrable introuvable, ou action non permise.',
    'version_perimee': 'Le livrable a changé depuis votre affichage : rechargez-le. Rien n’a été écrasé ; votre saisie reste dans la page.',
    'cle_reutilisee': 'Cette demande a déjà servi pour un autre contenu : rechargez la page.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'role_interdit': 'Votre rôle ne permet pas cette action.',
    'droit_export_retire': 'Votre compte n’a pas le droit d’exporter ou de télécharger.',
    'source_indisponible': 'Une source citée n’est pas (ou plus) accessible avec vos droits : ce contenu n’est pas affiché ni enregistré.',
    'code_arbitraire_desactive': 'Le code généré (HTML, JavaScript) est désactivé : le livrable est affiché par les composants du logiciel.',
    'trame_inconnue': 'Trame inconnue.', 'dossier_requis': 'Choisissez un dossier.', 'format_non_modifiable': 'Ce document ne se modifie pas ici.',
    'format_non_disponible': 'Format d’export non proposé (JSON, Markdown ou CSV).', 'contenu_trop_volumineux': 'Contenu trop volumineux.',
    'registre_dossiers_indisponible': 'Registre des dossiers illisible pour l’instant : rien n’a été créé.',
}


def allowed(role, method, api):
    """``standalone_auth.allowed`` : lectures ouvertes aux rôles du cabinet (le service relit droits et dossier) ; écritures déclarées."""
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    if method in ('GET', 'HEAD'):
        return True
    if method != 'POST':
        return False
    if reste == 'creer':
        return role in ROLES_EDITION
    if len(parts) == 2 and parts[1] == 'enregistrer':
        return role in ROLES_EDITION
    if len(parts) == 2 and parts[1] == 'plan':
        return role in ROLES_PLANIFICATION
    return False


def _sortie(data, status='200 OK'):
    from .web440 import json_out
    out = json_out(data, status)
    out['headers'] = [('Cache-Control', 'private, no-store')]
    return out


def _contexte(desk, env):
    from .membres5650 import effective_rights, hidden_for
    from .web567 import actor
    owner, role = actor(env)
    try:
        return owner, role, hidden_for(desk.c['state_dir'], owner, role), effective_rights(desk.c['state_dir'], owner, role)
    except Exception:   # noqa: BLE001 — contrôle des droits illisible : jamais un accès élargi
        raise Stop('controle_acces_indisponible') from None


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('livrable_absent', 'dossier_reserve', 'version_inconnue', 'role_insuffisant'):
            code = 'introuvable'
        from .erreurs5634 import explain
        return _sortie({'error': code, 'message': MESSAGES.get(code) or explain(code, None)}, STATUTS_HTTP.get(code, '422 Unprocessable Entity'))


def _api(env, desk, auth, prefix, name, args, method):
    owner, role, hidden, droits = _contexte(desk, env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    if role not in ROLES_EDITION:
        raise Stop('role_interdit')
    if method in ('GET', 'HEAD'):
        if reste == 'trames':
            return _sortie({'trames': trames_publiques(), 'capacites': capacites()})
        if len(parts) == 2 and parts[1] == 'contenu':
            return _sortie(lire(desk, parts[0], hidden, droits, args.get('revision')))
        if len(parts) == 2 and parts[1] == 'exporter':
            corps, kind, nom = exporter(desk, parts[0], str(args.get('format') or ''), hidden, droits, args.get('revision'))
            from urllib.parse import quote
            desk.audit('livrable56109_export', {'document': parts[0][:40], 'format': str(args.get('format') or '')[:8], 'par': str(owner)[:80]})
            return {'status': '200 OK', 'kind': kind, 'body': corps, 'headers': [('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff'),
                                                                                 ('Content-Disposition', "attachment; filename*=UTF-8''" + quote(nom, safe=''))]}
        if len(parts) == 3 and parts[0] == 'operations':
            raise Stop('introuvable')
        if len(parts) == 3 and parts[1] == 'operations':
            out = operation(desk, owner, parts[2], hidden, droits)
            if out['document_id'] != parts[0]:
                raise Stop('introuvable')
            return _sortie(out)
        raise Stop('introuvable')
    if method != 'POST' or not allowed(role, method, name):
        raise Stop('role_interdit' if method == 'POST' and reste in ('creer',) or (len(parts) == 2 and parts[1] in ('enregistrer', 'plan')) else 'introuvable')
    from .web440 import check_post
    d = check_post(env, auth)
    if reste == 'creer':
        out = creer_depuis_trame(desk, owner, role, hidden, droits, str(d.get('trame') or ''), str(d.get('dossier') or ''), str(d.get('titre') or ''), d.get('cle'))
        return _sortie({**out, 'href': prefix + '/livrables/' + out['document_id'], 'message': 'Livrable créé (version %d) : il apparaît dans Produire, Aujourd’hui et le dossier.' % out['revision']})
    if parts[1] == 'enregistrer':
        out = ecrire(desk, owner, role, hidden, droits, d.get('payload'), d.get('cle'), d.get('revision'), parts[0])
        return _sortie({**out, 'message': ('Version %d déjà enregistrée (demande rejouée).' if out['replayed'] else 'Version %d enregistrée.') % out['revision']})
    budget = d.get('budget_tokens')
    out = planifier(desk, owner, role, hidden, droits, parts[0], d.get('plan'), budget if type(budget) is int else -1, d.get('cle'))
    return _sortie({**out, 'href': prefix + '/missions-complexes?id=' + out['mission_id'],
                    'message': 'Plan validé : mission créée en attente de lancement (%d étapes, %d jetons au plus). Rien n’est exécuté sans votre lancement.' % (out['etapes'], out['max_tokens'])})


# ------------------------------------------------------------------------------------------------ Produire : trois vues, lecteur, Aujourd'hui
VUES = (('creer', 'Créer'), ('livrables', 'Livrables'), ('modeles', 'Modèles'))


def vue_produire(args):
    v = str((args or {}).get('vue') or '')
    return v if v in dict(VUES) else 'livrables'


def _matters_visibles(desk, hidden):
    from .common import load_matters, matter_display
    try:
        return [(str(m['id']), matter_display(m)) for m in load_matters(desk.c) if str(m['id']) not in hidden]
    except Exception:   # noqa: BLE001 — registre illisible : aucune liste (jamais une liste élargie)
        return []


def nav_html(prefix, active):
    return ('<nav class="v109-vues" aria-label="Vues de Produire">%s</nav>' % ''.join(
        '<a href="%s"%s>%s</a>' % (e(prefix + '/production' + ('' if k == 'livrables' else '?vue=' + k), quote=True), ' aria-current="page"' if k == active else '', e(lib))
        for k, lib in VUES))


def _creer_html(desk, prefix, hidden, role):
    from .produire56100 import ia_en_pause
    liens = (('/studio', 'Rédiger', 'Acte, consultation ou courrier à partir des modèles du cabinet.'),
             ('/documents', 'Documents', 'Documents du cabinet et des dossiers.'),
             ('/playbooks', 'Parcours guidés', 'Missions et étapes spécialisées existantes.'),
             ('/outils-ia', 'Outils IA', 'Analyses ciblées (méthode par défaut : Assistant juridique).'),
             ('/missions-complexes', 'Missions', 'Suivi des missions en plusieurs étapes.'))
    cartes = ''.join('<li><a class="v109-carte" href="%s"><strong>%s</strong><span>%s</span></a></li>' % (e(prefix + h, quote=True), e(t), e(d)) for h, t, d in liens)
    options = ''.join('<option value="%s">%s</option>' % (e(m['id'], quote=True), e(m['label'])) for m in trames())
    dossiers = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(v)) for k, v in _matters_visibles(desk, hidden))
    pause = ('<p class="v109-note" role="status">IA en pause : la création manuelle, la modification et l’export restent possibles.</p>' if ia_en_pause(desk) else '')
    formulaire = ''
    if role in ROLES_EDITION:
        formulaire = ('<form class="v109-form" data-v109="creer" data-v109-api="%s"><h3>Nouveau livrable structuré</h3>%s'
                      '<label>Trame<select name="trame" required>%s</select></label><label>Dossier<select name="dossier" required><option value="">Choisir…</option>%s</select></label>'
                      '<label>Titre (facultatif)<input name="titre" maxlength="180"></label><button class="ax-btn" type="submit">Créer le livrable</button>'
                      '<p class="v109-etat" role="status" aria-live="polite"></p></form>') % (
            e(prefix + '/api440/' + PREFIXE + 'creer', quote=True), pause, options, dossiers)
    return ('<section class="ax-card v109-panneau" aria-labelledby="v109-t-creer"><h2 id="v109-t-creer">Créer</h2><ul class="v109-cartes">%s</ul>%s</section>') % (cartes, formulaire)


def _modeles_html(desk, prefix, owner, role):
    lignes = ''.join('<li><strong>%s</strong> <span class="v109-meta">%s · version %d · %s</span><span class="v109-question">%s</span></li>' % (
        e(m['label']), e({'contentieux': 'Contentieux', 'conseil': 'Conseil', 'mixte': 'Mixte'}.get(m['practice'], m['practice'])), m['version'],
        e('à valider par le cabinet' if m['status'] == 'a_valider' else m['status']), e(m['missing_information_question'])) for m in trames())
    try:
        from .modele_docx56109 import section_html as _modeles_word
        word = _modeles_word(desk, prefix, owner, role)
    except Exception:   # noqa: BLE001 — la bibliothèque Word reste accessible par sa page historique
        import logging
        logging.getLogger(__name__).exception('modèles Word du cabinet')
        word = '<p class="l89-alerte" role="status">Modèles Word du cabinet illisibles pour l’instant.</p>'
    return ('<section class="ax-card v109-panneau" aria-labelledby="v109-t-trames"><h2 id="v109-t-trames">Trames de livrables</h2>'
            '<p class="ax-muted">Douze trames de structure, à valider par le cabinet (aucun texte de loi ni clause réputée conforme). Méthode par défaut : '
            'Assistant juridique.</p><ul class="v109-trames">%s</ul></section>%s'
            '<p><a class="ax-btn ghost" href="%s">Modèle de courrier (bibliothèque existante)</a></p>') % (lignes, word, e(prefix + '/modeles-word', quote=True))


def vues_html(desk, prefix, owner, role, args):
    """Barre des trois vues de Produire (nouvelle interface seulement) et, pour Créer et Modèles, leur panneau ; Livrables garde la page
    existante (documents prêts, file de relecture, résultats) sous la barre."""
    from .membres5650 import viewer_hidden
    active = vue_produire(args)
    hidden = viewer_hidden() or frozenset()
    corps = ''
    if active == 'creer':
        corps = _creer_html(desk, prefix, hidden, role)
    elif active == 'modeles':
        corps = _modeles_html(desk, prefix, owner, role)
    return nav_html(prefix, active) + corps + '<script defer src="%s/static/v56109-produire.js"></script>' % e(prefix, quote=True)


def edition_html(desk, prefix, row, owner, role):
    """Section du lecteur commun pour un livrable structuré : édition des lignes (script), exports de données, plan des sous-agents."""
    if row.get('source_service') != PRODUCTEUR:
        return ''
    from .membres5650 import effective_rights
    try:
        droits = effective_rights(desk.c['state_dir'], owner, role)
    except Exception:   # noqa: BLE001
        droits = {}
    base = prefix + '/api440/' + PREFIXE + row['document_id']
    config = {'contenu': base + '/contenu', 'enregistrer': base + '/enregistrer', 'plan': base + '/plan', 'operations': base + '/operations/',
              'statuts': STATUTS, 'courante': row['lifecycle'] == 'current', 'modifiable': role in ROLES_EDITION and row['lifecycle'] == 'current'}
    exports = ''
    if droits.get('exporter'):
        exports = '<p class="v109-exports">Exporter cette version : %s</p>' % ' '.join(
            '<a class="ax-btn ghost" href="%s">%s</a>' % (e('%s/exporter?format=%s&revision=%s' % (base, f, _revision_de(row)), quote=True), lib)
            for f, lib in (('json', 'JSON'), ('md', 'Markdown'), ('csv', 'CSV')))
    plan = ''
    if role in ROLES_PLANIFICATION and row['lifecycle'] == 'current':
        plan = ('<details class="v109-plan"><summary>Plusieurs spécialistes (avancé)</summary><p class="ax-muted">Plan fermé : cadrage, faits, recherche, rédaction, '
                'contrôle, assemblage ; huit étapes au plus ; l’assemblage suit toujours un contrôle de la rédaction. Le plan devient une mission en attente '
                'de lancement, sans exécution automatique.</p><form class="v109-form" data-v109="plan"><label>Plan (JSON)<textarea name="plan" rows="6" '
                'spellcheck="false"></textarea></label><label>Budget de jetons<input name="budget_tokens" type="number" min="1" max="100000" value="20000"></label>'
                '<button class="ax-btn ghost" type="submit">Valider le plan</button><p class="v109-etat" role="status" aria-live="polite"></p></form></details>')
    return ('<section class="ax-card v109-edition" id="v109-edition" aria-labelledby="v109-t-edition"><h2 id="v109-t-edition">Contenu du livrable</h2>'
            '<p class="ax-muted">Chaque enregistrement crée une nouvelle version du même livrable ; les versions précédentes restent consultables et comparables. '
            'Code généré : désactivé (rendu par les composants du logiciel).</p><div class="v109-editeur" data-v109-editeur></div>'
            '<p class="v109-etat" role="status" aria-live="polite" data-v109-etat></p>%s%s<div hidden data-v109-config="%s"></div></section>'
            '<script defer src="%s/static/v56109-produire.js"></script>') % (exports, plan, e(json.dumps(config, ensure_ascii=False), quote=True), e(prefix, quote=True))


def _revision_de(row):
    try:
        return int(json.loads(row.get('provenance') or '{}').get('revision') or 0) or ''
    except (ValueError, TypeError):
        return ''


def apercu_html(desk, row):
    """Aperçu du lecteur commun (cadre isolé ``sandbox`` sans script) : rendu déclaratif de CETTE version ; source devenue interdite : rien."""
    from .livrables5689 import droits_requete
    hidden, droits = droits_requete()
    try:
        c = contenu_de_version(desk, row, hidden, droits)
    except Stop:
        return '<p>%s</p>' % e(MESSAGES['source_indisponible'])
    return rendu_html(c['payload']) if c else ''


def travaux_prets_html(desk, prefix, limite=3):
    """Cadre « Travail préparé » d'Aujourd'hui (point de montage) : les trois livrables structurés les plus récents, mêmes documents et même
    lecteur que Produire ; aucun compteur, aucun appel de modèle, lecture seule. Rien à montrer : chaîne vide."""
    from . import livrables5689 as liv
    from urllib.parse import urlencode
    try:
        hidden, droits = liv.droits_requete()
        base, params = liv._where(hidden, droits, desk=desk)
        rows = desk.db.execute('SELECT * FROM prepared_items5689' + base + ' AND source_service=? ORDER BY source_at DESC, id DESC LIMIT ?',
                               list(params) + [PRODUCTEUR, int(limite)]).fetchall()
        labels, _matters = liv._labels(desk)
    except Exception:   # noqa: BLE001 — le cadre existant reste affiché sans ce complément
        return ''
    if not rows:
        return ''
    lignes = ''.join('<li><a href="%s" data-f96-action="document" data-f96-cle="%s" data-f96-titre="%s">%s</a> <span class="v109-meta">%s · %s · version %d</span></li>' % (
        e(prefix + '/livrables/' + r['document_id'] + '?' + urlencode({'return_to': prefix + '/aujourdhui'}), quote=True), e(r['document_id'], quote=True),
        e(r['title'], quote=True), e(r['title']), e(r['label'] or 'Livrable'), e(labels.get(r['matter_id'], '') or 'Sans dossier'), r['version_number']) for r in rows)
    return ('<div class="v109-recents"><h3>Livrables structurés récents</h3><ul>%s</ul><p><a href="%s">Voir tout dans Produire</a></p></div>') % (
        lignes, e(prefix + '/production?vue=livrables', quote=True))

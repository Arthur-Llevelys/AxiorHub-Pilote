"""5.6.108 (lot S1) : synchronisation des postes du cabinet sans serveur — journal d'opérations signées, identités globales, espaces partagés.

Prolonge la liaison de la 5.6.103 (``liaison56103`` : appairage SPAKE2, sessions chiffrées, présence) : aucun second transport, aucune
seconde découverte, jamais de base SQLite partagée. Chaque poste garde sa base ; les postes s'échangent des **opérations métier versionnées**.

Décision du cabinet (18/11/2026, remplace la feuille du lot) : fonction **disponible par défaut** (mention « nouveau »), mais **aucun port
n'écoute** et aucune annonce n'est émise tant que le poste n'a pas créé ou rejoint un espace partagé ET appairé au moins un poste (seules
exceptions, à l'initiative de l'administrateur et bornées dans le temps : la durée d'un code d'appairage affiché, et la fenêtre « Rejoindre le
cabinet »). L'administrateur peut la désactiver (``sync56108:desactive``) : l'écoute se ferme. Une mise à jour ne crée aucun espace ; rien
n'est partagé sans le choix explicite des dossiers.

Modèle :

- **Identités** : chaque objet partagé (dossier ``dos-``, tâche ``tac-``, événement ``evt-``, document ``doc-``, mission ``mis-``) reçoit un
  identifiant global (``sync56108_objets``) relié à l'identifiant local ; le nom n'est jamais l'identifiant ; les chemins restent locaux.
  Personne (``pers-``) distincte de l'appareil (identifiant de liaison du poste).
- **Espace partagé** (``esp-``) : le cabinet, créé par un poste (« Créer un espace partagé ») qui en est l'autorité, rejoint par les autres
  (« Rejoindre le cabinet » : demande d'adhésion acceptée par l'administrateur du poste autorité). Un dossier réservé vit dans un
  sous-espace dont seuls les postes des personnes autorisées sont membres. Les membres et leurs clés publiques sont eux-mêmes une opération
  (``espace.membres``) signée par l'autorité de l'espace, versionnée (la plus récente l'emporte, jamais une plus ancienne).
- **Opération** : identifiant, protocole, espace, poste d'origine, séquence (par espace et par origine), acteur (personne), type (liste
  fermée ``TYPES``), objet, versions parentes, charge utile, signature Ed25519 de l'origine (clés de la liaison). Aucun SQL, chemin ou
  script fourni par un pair n'est exécuté : la charge est validée champ par champ (``sync56108_objets.valider_charge``).
- **Commande locale** : des déclencheurs SQLite inscrivent l'intention (``sync56108_capture``) DANS la transaction de la commande (création de
  dossier, tâche, événement…), sans modifier les modules métier ; ``journaliser`` la transforme ensuite en opération signée (boîte d'envoi
  transactionnelle). Une capture en attente est journalisée AVANT toute projection d'une opération reçue sur le même objet (aucune
  modification locale écrasée).
- **Réception** : forme, espace, appartenance de l'origine, signature d'origine, acteur, droits du type et charge vérifiés AVANT toute
  écriture ; puis opération, têtes, projection et reçu dans UNE transaction ; l'accusé (``ack``) n'est rendu qu'après validation. Rejouer un
  lot ne crée rien deux fois ; une opération altérée est refusée ; un trou de séquence est refusé et redemandé (curseurs par espace et
  par origine) ; un relais doit être membre de l'espace et ne devient jamais auteur.
- **Conflits** : deux versions issues du même parent sont deux têtes ; aucune n'est choisie par l'horloge ; la projection locale reste en
  l'état jusqu'à la résolution, nouvelle version qui référence les deux. Les suppressions sont des versions (pas de résurrection).
- **Retrait d'un poste** : il n'est plus membre (plus rien ne lui est envoyé) ; ses opérations directes sont refusées. Ses copies déjà
  reçues ne peuvent pas être effacées à distance.

Transport (dans une session de liaison déjà authentifiée) : ``sync.v1.capabilities``, ``sync.v1.summary``, ``sync.v1.operations``,
``sync.v1.need``, ``sync.v1.ack``, ``sync.v1.blob_need`` / ``sync.v1.blob_chunk``. Un poste 5.6.103-5.6.107 répond « trame inconnue » :
il continue d'échanger les messages, n'est jamais annoncé « à jour » pour les données métier.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

from .common import Stop

PROTOCOLE = 1
VERSION_DOMAINES = ('messages', 'dossiers', 'taches', 'agenda', 'documents', 'missions')
LOT_OPS = 100
LOT_OCTETS = 512 * 1024
MAX_CHARGE = 64 * 1024
MAX_PARENTS = 16
REJOINDRE_DUREE = 600
ETIQUETTE_OP = b'axiorhub-sync-56108 operation v1'

REGLAGE_DESACTIVE = 'sync56108:desactive'
REGLAGE_PERSONNE = 'sync56108:personne'
REGLAGE_REJOINDRE = 'sync56108:rejoindre'

OP_RE = re.compile(r'^op-[0-9a-f]{32}$')
ESP_RE = re.compile(r'^esp-[0-9a-f]{32}$')
PERS_RE = re.compile(r'^pers-[0-9a-f]{32}$')
OBJ_RE = re.compile(r'^(dos|tac|evt|doc|mis|esp|pau)-[0-9a-f]{32}$')
POSTE_RE = re.compile(r'^[0-9a-f]{32}$')

# type → préfixe de l'objet (liste fermée)
TYPES = {
    'espace.membres': 'esp', 'espace.pause': 'pau',
    'dossier.creation': 'dos', 'dossier.modification': 'dos', 'dossier.cloture': 'dos', 'dossier.resolution': 'dos',
    'tache.creation': 'tac', 'tache.modification': 'tac', 'tache.suppression': 'tac', 'tache.resolution': 'tac',
    'evenement.creation': 'evt', 'evenement.modification': 'evt', 'evenement.annulation': 'evt', 'evenement.suppression': 'evt',
    'evenement.resolution': 'evt',
    'document.version': 'doc', 'document.suppression': 'doc', 'document.resolution': 'doc',
    'mission.attribution': 'mis', 'mission.etat': 'mis', 'mission.resultat': 'mis', 'mission.abandon': 'mis', 'mission.reprise': 'mis',
}
GENRES = {'dos': 'dossier', 'tac': 'tache', 'evt': 'evenement', 'doc': 'document', 'mis': 'mission', 'esp': 'espace', 'pau': 'pause'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sync56108_espaces(id TEXT PRIMARY KEY, nom TEXT NOT NULL DEFAULT '', parent TEXT NOT NULL DEFAULT '',
  autorite TEXT NOT NULL, dossier TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0, categories TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL DEFAULT 'actif', cree_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync56108_membres(espace TEXT NOT NULL, poste TEXT NOT NULL, cle TEXT NOT NULL, nom TEXT NOT NULL DEFAULT '',
  personnes TEXT NOT NULL DEFAULT '[]', retire INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(espace, poste));
CREATE TABLE IF NOT EXISTS sync56108_personnes(gid TEXT PRIMARY KEY, nom TEXT NOT NULL DEFAULT '', poste TEXT NOT NULL DEFAULT '',
  compte TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sync56108_operations(rang INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, espace TEXT NOT NULL,
  origine TEXT NOT NULL, seq INTEGER NOT NULL, acteur TEXT NOT NULL, type TEXT NOT NULL, objet TEXT NOT NULL, parents TEXT NOT NULL DEFAULT '[]',
  digest TEXT NOT NULL, brut TEXT NOT NULL, relaye_par TEXT NOT NULL DEFAULT '', recu_le TEXT NOT NULL, UNIQUE(espace, origine, seq));
CREATE INDEX IF NOT EXISTS sync56108_operations_objet ON sync56108_operations(objet);
CREATE TABLE IF NOT EXISTS sync56108_tetes(objet TEXT NOT NULL, operation TEXT NOT NULL, PRIMARY KEY(objet, operation));
CREATE TABLE IF NOT EXISTS sync56108_objets(objet TEXT PRIMARY KEY, genre TEXT NOT NULL, espace TEXT NOT NULL, local_id TEXT,
  etat TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL DEFAULT '', projete_op TEXT NOT NULL DEFAULT '', conflit INTEGER NOT NULL DEFAULT 0,
  projection TEXT NOT NULL DEFAULT 'faite', motif TEXT NOT NULL DEFAULT '', bloque INTEGER NOT NULL DEFAULT 0, maj_le TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS sync56108_objets_local ON sync56108_objets(genre, local_id) WHERE local_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS sync56108_capture(genre TEXT NOT NULL, local_id TEXT NOT NULL, quand TEXT NOT NULL, PRIMARY KEY(genre, local_id));
CREATE TABLE IF NOT EXISTS sync56108_garde(id INTEGER PRIMARY KEY CHECK(id=1), projection INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO sync56108_garde(id, projection) VALUES(1, 0);
CREATE TABLE IF NOT EXISTS sync56108_pairs(poste TEXT NOT NULL, espace TEXT NOT NULL, origine TEXT NOT NULL, seq INTEGER NOT NULL,
  maj_le TEXT NOT NULL, PRIMARY KEY(poste, espace, origine));
CREATE TABLE IF NOT EXISTS sync56108_capacites(poste TEXT PRIMARY KEY, protocole INTEGER NOT NULL DEFAULT 0, version TEXT NOT NULL DEFAULT '',
  domaines TEXT NOT NULL DEFAULT '[]', vu_le TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync56108_adhesions(poste TEXT PRIMARY KEY, espace TEXT NOT NULL, personne TEXT NOT NULL, personne_nom TEXT NOT NULL,
  nom_poste TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL DEFAULT 'en_attente', recu_le TEXT NOT NULL, decide_le TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS sync56108_echanges(poste TEXT PRIMARY KEY, debut TEXT NOT NULL DEFAULT '', fin TEXT NOT NULL DEFAULT '',
  envoyees INTEGER NOT NULL DEFAULT 0, recues INTEGER NOT NULL DEFAULT 0, refus TEXT NOT NULL DEFAULT '', en_cours INTEGER NOT NULL DEFAULT 0,
  octets INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sync56108_partages(dossier TEXT PRIMARY KEY, espace TEXT NOT NULL, par TEXT NOT NULL DEFAULT '', quand TEXT NOT NULL);
'''

# déclencheurs de capture : (nom, table, événement, genre, colonne de l'identifiant local)
DECLENCHEURS = (
    ('dos_i', 'dossiers5697', 'INSERT', 'dossier', 'NEW.id'), ('dos_u', 'dossiers5697', 'UPDATE', 'dossier', 'NEW.id'),
    ('dpa_i', 'dossier_parties5697', 'INSERT', 'dossier', 'NEW.dossier_id'), ('dpa_d', 'dossier_parties5697', 'DELETE', 'dossier', 'OLD.dossier_id'),
    ('tac_i', 'tasks', 'INSERT', 'tache', 'NEW.id'), ('tac_u', 'tasks', 'UPDATE', 'tache', 'NEW.id'), ('tac_d', 'tasks', 'DELETE', 'tache', 'OLD.id'),
    ('t88_i', 'taches5688', 'INSERT', 'tache', 'NEW.task_id'), ('t88_u', 'taches5688', 'UPDATE', 'tache', 'NEW.task_id'),
    ('t97_i', 'taches5697', 'INSERT', 'tache', 'NEW.task_id'), ('t97_u', 'taches5697', 'UPDATE', 'tache', 'NEW.task_id'),
    ('evt_i', 'agenda5634', 'INSERT', 'evenement', 'NEW.id'), ('evt_u', 'agenda5634', 'UPDATE', 'evenement', 'NEW.id'),
    ('evt_d', 'agenda5634', 'DELETE', 'evenement', 'OLD.id'),
    ('e97_i', 'agenda5697', 'INSERT', 'evenement', 'NEW.event_id'), ('e97_u', 'agenda5697', 'UPDATE', 'evenement', 'NEW.event_id'),
)


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _sha(texte):
    return hashlib.sha256(texte.encode('utf-8') if isinstance(texte, str) else bytes(texte)).hexdigest()


def nouvel_id(prefixe):
    return '%s-%s' % (prefixe, secrets.token_hex(16))


def _table(db, nom):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nom,)).fetchone() is not None


def schema_complet(db, script, lignes=()):
    """Vrai si toutes les tables et index du script existent déjà et si les lignes d'amorçage sont présentes : le démarrage n'écrit alors
    rien (recette 5.6.44 : sans changement, aucune écriture ni verrou au démarrage). ``lignes`` : (table, clause WHERE) à trouver."""
    noms = re.findall(r'CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', script, re.I)
    existants = {r[0] for r in db.execute('SELECT name FROM sqlite_master')}
    if any(n not in existants for n in noms):
        return False
    return all(db.execute('SELECT 1 FROM %s WHERE %s' % (table, ou)).fetchone() for table, ou in lignes)


# ------------------------------------------------------------------------------------------------ schéma et réglages
def ensure_schema(desk):
    """Tables du lot, au démarrage (``Desk._prepare``) ; déclencheurs posés seulement si un espace existe (jamais pendant un GET)."""
    if desk.db.in_transaction:
        desk.db.commit()
    if not schema_complet(desk.db, SCHEMA, (('sync56108_garde', 'id=1'),)):
        desk.db.executescript(SCHEMA)
    desk.db.commit()
    from . import sync56108_missions, sync56108_fichiers, sync56108_web
    sync56108_missions.ensure_schema(desk)
    sync56108_fichiers.ensure_schema(desk)
    sync56108_web.ensure_schema(desk)
    if desk.db.execute("SELECT 1 FROM sync56108_espaces WHERE etat='actif'").fetchone():
        installer_declencheurs(desk)


def installer_declencheurs(desk):
    """Pose (idempotent) les déclencheurs de capture sur les tables métier existantes. Ils n'agissent que si un espace est actif et
    hors des projections de la synchronisation (``sync56108_garde``)."""
    from . import dossiers5697, agenda5697
    dossiers5697.ensure_schema(desk)
    agenda5697.schema(desk)
    for nom, table, evt, genre, col in DECLENCHEURS:
        if not _table(desk.db, table):
            continue
        desk.db.execute(
            "CREATE TRIGGER IF NOT EXISTS sync56108_%s AFTER %s ON %s WHEN (SELECT projection FROM sync56108_garde WHERE id=1)=0 "
            "AND EXISTS(SELECT 1 FROM sync56108_espaces WHERE etat='actif') BEGIN INSERT OR IGNORE INTO sync56108_capture(genre,local_id,quand) "
            "VALUES('%s', %s, strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ','now')); END" % (nom, evt, table, genre, col))
    desk.db.commit()
    from .sync56108_fichiers import installer_signaux
    installer_signaux(desk)   # S5 : écritures de fichiers par AxiorHub → relevé prompt des dossiers partagés


def desactivee(desk):
    try:
        return desk.settings(REGLAGE_DESACTIVE, False) is True
    except Exception:   # noqa: BLE001 — réglage illisible : prudence, rien n'écoute
        return True


def personne_locale(desk):
    p = desk.settings(REGLAGE_PERSONNE, None)
    return p if isinstance(p, dict) and PERS_RE.fullmatch(str(p.get('gid') or '')) else None


def espace_cabinet(desk):
    """Espace partagé du cabinet de ce poste (sans parent) ; None si le poste travaille seul."""
    if not _table(desk.db, 'sync56108_espaces'):
        return None
    return desk.db.execute("SELECT * FROM sync56108_espaces WHERE parent='' AND etat='actif' ORDER BY cree_le LIMIT 1").fetchone()


def rejoindre_en_cours(desk):
    r = desk.settings(REGLAGE_REJOINDRE, None)
    if not isinstance(r, dict) or float(r.get('expire') or 0) < time.time():
        return None
    return r


def ecoute_requise_db(db, state_dir):
    """Écoute (port TCP, annonce UDP) voulue ? Sur une connexion SQLite brute, en lecture seule (``poste.py`` sans ``Desk``) :
    pas désactivée ET (code d'appairage affiché OU fenêtre « Rejoindre » ouverte OU (espace actif ET au moins un poste appairé))."""
    def reglage(cle, defaut):
        row = db.execute('SELECT value FROM settings WHERE key=?', (cle,)).fetchone()
        try:
            return json.loads(row[0]) if row else defaut
        except (TypeError, ValueError):
            return None
    if reglage(REGLAGE_DESACTIVE, False) is not False:
        return False
    from .liaison56103 import code_actif
    if code_actif(state_dir):
        return True
    r = reglage(REGLAGE_REJOINDRE, None)
    if isinstance(r, dict) and float(r.get('expire') or 0) > time.time():
        return True
    if not (_table(db, 'sync56108_espaces') and _table(db, 'liaison56103_postes')):
        return False
    if not db.execute("SELECT 1 FROM sync56108_espaces WHERE etat='actif'").fetchone():
        return False
    return db.execute('SELECT 1 FROM liaison56103_postes WHERE retire=0 AND cle_signature IS NOT NULL').fetchone() is not None


def ecoute_requise(desk):
    try:
        return ecoute_requise_db(desk.db, desk.c['state_dir'])
    except Exception:   # noqa: BLE001 — base illisible : rien n'écoute
        return False


def synchro_active(desk):
    """Échanges métier possibles : pas désactivée, et espace actif ou fenêtre « Rejoindre le cabinet » ouverte (demande d'adhésion)."""
    try:
        return not desactivee(desk) and (espace_cabinet(desk) is not None or rejoindre_en_cours(desk) is not None)
    except Exception:   # noqa: BLE001 — schéma absent : rien
        return False


# ------------------------------------------------------------------------------------------------ transactions
@contextmanager
def _tx(desk, garde=True):
    """Transaction d'écriture (``BEGIN IMMEDIATE``) ; ``garde`` : les déclencheurs de capture se taisent (projection de la synchronisation)."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        if garde:
            desk.db.execute('UPDATE sync56108_garde SET projection=1 WHERE id=1')
        yield
        if garde:
            desk.db.execute('UPDATE sync56108_garde SET projection=0 WHERE id=1')
    except BaseException:
        desk.db.rollback()
        raise
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ signature
def _ident(desk):
    from .liaison56103 import identite
    return identite(desk.c['state_dir'])


def _a_signer(op):
    return ETIQUETTE_OP + canon({k: v for k, v in op.items() if k != 'sig'}).encode('utf-8')


def digest_op(op):
    return _sha(canon({k: v for k, v in op.items() if k != 'sig'}))


def signer(ident, op):
    op = dict(op)
    op['sig'] = base64.b64encode(ident.signer(_a_signer(op))).decode('ascii')
    return op


def verifier_signature(cle_b64, op):
    from .liaison56103 import _verifier
    try:
        cle = base64.b64decode(str(cle_b64 or ''), validate=True)
        sig = base64.b64decode(str(op.get('sig') or ''), validate=True)
    except (ValueError, TypeError):
        return False
    return len(cle) == 32 and len(sig) == 64 and _verifier(cle, sig, _a_signer(op))


# ------------------------------------------------------------------------------------------------ forme d'une opération
CLES_OP = ('v', 'id', 'espace', 'origine', 'seq', 'acteur', 'type', 'objet', 'parents', 'charge', 'quand', 'sig')


def valider_forme(op):
    """Forme stricte (aucune clé en plus, types, tailles) ; ``Stop('operation_invalide')`` sinon. Avant toute écriture."""
    if not isinstance(op, dict) or set(op) != set(CLES_OP):
        raise Stop('operation_invalide')
    if op['v'] != PROTOCOLE or not OP_RE.fullmatch(str(op['id'])) or not ESP_RE.fullmatch(str(op['espace'])):
        raise Stop('operation_invalide')
    if not POSTE_RE.fullmatch(str(op['origine'])) or not PERS_RE.fullmatch(str(op['acteur'])):
        raise Stop('operation_invalide')
    if type(op['seq']) is not int or not 1 <= op['seq'] < 2 ** 53:
        raise Stop('operation_invalide')
    if op['type'] not in TYPES or not OBJ_RE.fullmatch(str(op['objet'])) or not op['objet'].startswith(TYPES[op['type']] + '-'):
        raise Stop('operation_invalide')
    if op['type'] == 'espace.membres' and op['objet'] != op['espace']:
        raise Stop('operation_invalide')
    p = op['parents']
    if not isinstance(p, list) or len(p) > MAX_PARENTS or len(set(p)) != len(p) or any(not OP_RE.fullmatch(str(x)) for x in p) or p != sorted(p):
        raise Stop('operation_invalide')
    if op['type'].endswith('.resolution') and len(p) < 2:
        raise Stop('operation_invalide')
    if not isinstance(op['charge'], dict) or len(canon(op['charge'])) > MAX_CHARGE:
        raise Stop('operation_invalide')
    if not isinstance(op['quand'], str) or len(op['quand']) > 40 or not isinstance(op['sig'], str) or len(op['sig']) > 120:
        raise Stop('operation_invalide')
    return op


# ------------------------------------------------------------------------------------------------ espaces et membres
def _membre(desk, espace, poste):
    return desk.db.execute('SELECT * FROM sync56108_membres WHERE espace=? AND poste=?', (espace, poste)).fetchone()


def membre_actif(desk, espace, poste):
    m = _membre(desk, espace, poste)
    return bool(m) and not m['retire']


def espaces_communs(desk, poste, moi):
    """Espaces où ce poste ET le pair sont membres actifs (ce qui peut être échangé avec lui)."""
    return [r['id'] for r in desk.db.execute(
        "SELECT e.id FROM sync56108_espaces e JOIN sync56108_membres a ON a.espace=e.id AND a.poste=? AND a.retire=0 "
        "JOIN sync56108_membres b ON b.espace=e.id AND b.poste=? AND b.retire=0 WHERE e.etat='actif' ORDER BY e.cree_le, e.id", (moi, poste))]


def _charge_membres(desk, espace_row, membres, version):
    return {'nom': espace_row['nom'], 'parent': espace_row['parent'], 'autorite': espace_row['autorite'], 'dossier': espace_row['dossier'],
            'version': int(version), 'categories': json.loads(espace_row['categories'] or '{}'), 'membres': membres}


def _membres_actuels(desk, espace):
    return [{'poste': r['poste'], 'cle': r['cle'], 'nom': r['nom'], 'personnes': json.loads(r['personnes'] or '[]'), 'retire': bool(r['retire'])}
            for r in desk.db.execute('SELECT * FROM sync56108_membres WHERE espace=? ORDER BY poste', (espace,))]


def valider_membres(charge):
    """Charge d'un ``espace.membres`` : forme stricte."""
    if set(charge) != {'nom', 'parent', 'autorite', 'dossier', 'version', 'categories', 'membres'}:
        raise Stop('operation_invalide')
    if not isinstance(charge['nom'], str) or len(charge['nom']) > 120 or (charge['parent'] and not ESP_RE.fullmatch(str(charge['parent']))):
        raise Stop('operation_invalide')
    if not POSTE_RE.fullmatch(str(charge['autorite'])) or type(charge['version']) is not int or charge['version'] < 1:
        raise Stop('operation_invalide')
    if charge['dossier'] and not re.fullmatch(r'^dos-[0-9a-f]{32}$', str(charge['dossier'])):
        raise Stop('operation_invalide')
    if not isinstance(charge['categories'], dict) or any(k not in ('taches_sans_dossier', 'agenda_sans_dossier') or not isinstance(v, bool)
                                                        for k, v in charge['categories'].items()):
        raise Stop('operation_invalide')
    m = charge['membres']
    if not isinstance(m, list) or not 1 <= len(m) <= 64:
        raise Stop('operation_invalide')
    for x in m:
        if not isinstance(x, dict) or set(x) != {'poste', 'cle', 'nom', 'personnes', 'retire'} or not POSTE_RE.fullmatch(str(x['poste'])):
            raise Stop('operation_invalide')
        from .liaison56103 import empreinte
        try:
            cle = base64.b64decode(str(x['cle']), validate=True)
        except ValueError:
            raise Stop('operation_invalide') from None
        if len(cle) != 32 or empreinte(cle) != x['poste'] or not isinstance(x['retire'], bool) or not isinstance(x['nom'], str) or len(x['nom']) > 80:
            raise Stop('operation_invalide')
        if not isinstance(x['personnes'], list) or len(x['personnes']) > 16:
            raise Stop('operation_invalide')
        for p in x['personnes']:
            if not isinstance(p, dict) or set(p) != {'gid', 'nom'} or not PERS_RE.fullmatch(str(p['gid'])) or not isinstance(p['nom'], str) or len(p['nom']) > 120:
                raise Stop('operation_invalide')
    if charge['autorite'] not in [x['poste'] for x in m if not x['retire']]:
        raise Stop('operation_invalide')
    return charge


def _appliquer_membres(desk, op, creer=False):
    """Membres d'un espace selon une opération ``espace.membres`` ; seule une version plus récente remplace la liste (dans la transaction)."""
    ch = op['charge']
    row = desk.db.execute('SELECT * FROM sync56108_espaces WHERE id=?', (op['espace'],)).fetchone()
    if row is None:
        if not creer:
            raise Stop('espace_inconnu')
        desk.db.execute('INSERT INTO sync56108_espaces(id,nom,parent,autorite,dossier,version,categories,etat,cree_le) VALUES(?,?,?,?,?,0,?,?,?)',
                        (op['espace'], ch['nom'], ch['parent'], ch['autorite'], ch['dossier'], json.dumps(ch['categories']), 'actif', _maintenant()))
        row = desk.db.execute('SELECT * FROM sync56108_espaces WHERE id=?', (op['espace'],)).fetchone()
    if ch['version'] <= row['version']:
        return False
    if ch['autorite'] != row['autorite']:
        raise Stop('autorite_requise')
    desk.db.execute('UPDATE sync56108_espaces SET nom=?,version=?,categories=? WHERE id=?', (ch['nom'], ch['version'], json.dumps(ch['categories']), op['espace']))
    for m in ch['membres']:
        desk.db.execute('INSERT INTO sync56108_membres(espace,poste,cle,nom,personnes,retire) VALUES(?,?,?,?,?,?) ON CONFLICT(espace,poste) DO UPDATE SET '
                        'cle=excluded.cle,nom=excluded.nom,personnes=excluded.personnes,retire=excluded.retire',
                        (op['espace'], m['poste'], m['cle'], m['nom'], json.dumps(m['personnes'], ensure_ascii=False), int(m['retire'])))
        for p in m['personnes']:
            desk.db.execute('INSERT INTO sync56108_personnes(gid,nom,poste) VALUES(?,?,?) ON CONFLICT(gid) DO UPDATE SET nom=excluded.nom, poste=excluded.poste',
                            (p['gid'], p['nom'], m['poste']))
    connus = {m['poste'] for m in ch['membres']}
    for r in desk.db.execute('SELECT poste FROM sync56108_membres WHERE espace=?', (op['espace'],)).fetchall():
        if r['poste'] not in connus:   # absent de la liste la plus récente : retiré (clé gardée pour vérifier l'historique relayé)
            desk.db.execute('UPDATE sync56108_membres SET retire=1 WHERE espace=? AND poste=?', (op['espace'], r['poste']))
    return True


# ------------------------------------------------------------------------------------------------ journal : écriture commune
def _derniere_seq(desk, espace, origine):
    return desk.db.execute('SELECT COALESCE(MAX(seq),0) FROM sync56108_operations WHERE espace=? AND origine=?', (espace, origine)).fetchone()[0]


def tetes(desk, objet):
    return [r[0] for r in desk.db.execute('SELECT operation FROM sync56108_tetes WHERE objet=? ORDER BY operation', (objet,))]


def _enregistrer(desk, op, relaye_par):
    """Dans la transaction ouverte : doublon (même empreinte : déjà reçue ; autre : altérée), séquence contiguë, parents connus du même
    objet, insertion, têtes. Rend 'deja_recue', 'recu' ou 'conflit'."""
    digest = digest_op(op)
    connu = desk.db.execute('SELECT digest FROM sync56108_operations WHERE id=?', (op['id'],)).fetchone()
    if connu:
        if connu[0] != digest:
            raise Stop('operation_alteree')
        return 'deja_recue'
    if op['seq'] != _derniere_seq(desk, op['espace'], op['origine']) + 1:
        raise Stop('plage_manquante')
    for parent in op['parents']:
        row = desk.db.execute('SELECT objet FROM sync56108_operations WHERE id=?', (parent,)).fetchone()
        if not row or row[0] != op['objet']:
            raise Stop('parent_manquant')
    desk.db.execute('INSERT INTO sync56108_operations(id,espace,origine,seq,acteur,type,objet,parents,digest,brut,relaye_par,recu_le) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (op['id'], op['espace'], op['origine'], op['seq'], op['acteur'], op['type'], op['objet'],
                                                         json.dumps(op['parents']), digest, canon(op), relaye_par, _maintenant()))
    desk.db.executemany('DELETE FROM sync56108_tetes WHERE objet=? AND operation=?', [(op['objet'], p) for p in op['parents']])
    desk.db.execute('INSERT INTO sync56108_tetes(objet, operation) VALUES(?,?)', (op['objet'], op['id']))
    n = desk.db.execute('SELECT COUNT(*) FROM sync56108_tetes WHERE objet=?', (op['objet'],)).fetchone()[0]
    return 'conflit' if n > 1 else 'recu'


def _nouvelle_op(desk, ident, espace, type_, objet, parents, charge):
    """Opération locale signée (dans la transaction ouverte : la séquence est réservée sous ``BEGIN IMMEDIATE``)."""
    p = personne_locale(desk)
    if not p:
        raise Stop('personne_non_declaree')
    op = {'v': PROTOCOLE, 'id': nouvel_id('op'), 'espace': espace, 'origine': ident.id, 'seq': _derniere_seq(desk, espace, ident.id) + 1,
          'acteur': p['gid'], 'type': type_, 'objet': objet, 'parents': sorted(set(parents)), 'charge': charge, 'quand': _maintenant()}
    return signer(ident, op)


def emettre(desk, espace, type_, objet, charge, parents=None, ident=None):
    """Dans la transaction ouverte : crée, signe et enregistre une opération locale ; rend l'opération."""
    ident = ident or _ident(desk)
    if type_ not in TYPES:
        raise Stop('type_inconnu')
    if not membre_actif(desk, espace, ident.id):
        raise Stop('espace_non_membre')
    if type_ != 'espace.membres':
        from .sync56108_objets import valider_charge
        valider_charge(type_, charge)
    op = _nouvelle_op(desk, ident, espace, type_, objet, tetes(desk, objet) if parents is None else parents, charge)
    valider_forme(op)
    _enregistrer(desk, op, ident.id)
    return op


# ------------------------------------------------------------------------------------------------ créer / rejoindre
def _nom(texte, maxi=120):
    texte = re.sub(r'[\x00-\x1f\x7f<>]', '', str(texte or ''))
    return re.sub(r'\s+', ' ', texte).strip()[:maxi]


def declarer_personne(desk, nom, compte):
    """Personne de ce poste (distincte du nom de l'ordinateur) ; gardée si déjà déclarée."""
    nom = _nom(nom)
    if len(nom) < 2:
        raise Stop('personne_requise')
    p = personne_locale(desk) or {'gid': nouvel_id('pers')}
    p.update(nom=nom, compte=str(compte or 'cabinet')[:120])
    desk.setting(REGLAGE_PERSONNE, p)
    desk.db.execute('INSERT INTO sync56108_personnes(gid,nom,poste,compte) VALUES(?,?,?,?) ON CONFLICT(gid) DO UPDATE SET nom=excluded.nom,'
                    'compte=excluded.compte', (p['gid'], p['nom'], '', p['compte']))
    desk.db.commit()
    return p


def creer_espace(desk, nom, personne_nom, compte, par=''):
    """« Créer un espace partagé » : ce poste devient l'autorité de l'espace du cabinet (version 1 des membres : lui seul)."""
    ensure_schema(desk)
    if desactivee(desk):
        raise Stop('synchronisation_desactivee')
    if espace_cabinet(desk) is not None:
        raise Stop('espace_deja_present')
    ident = _ident(desk)
    p = declarer_personne(desk, personne_nom, compte)
    from .liaison56103 import nom_du_poste
    espace = nouvel_id('esp')
    nom = _nom(nom) or 'Cabinet'
    moi = {'poste': ident.id, 'cle': base64.b64encode(ident.cle_signature).decode('ascii'), 'nom': nom_du_poste(desk),
           'personnes': [{'gid': p['gid'], 'nom': p['nom']}], 'retire': False}
    with _tx(desk):
        desk.db.execute('INSERT INTO sync56108_espaces(id,nom,parent,autorite,dossier,version,categories,etat,cree_le) VALUES(?,?,?,?,?,0,?,?,?)',
                        (espace, nom, '', ident.id, '', '{}', 'actif', _maintenant()))
        desk.db.execute('INSERT INTO sync56108_membres(espace,poste,cle,nom,personnes,retire) VALUES(?,?,?,?,?,0)',
                        (espace, ident.id, moi['cle'], moi['nom'], json.dumps(moi['personnes'], ensure_ascii=False)))
        row = desk.db.execute('SELECT * FROM sync56108_espaces WHERE id=?', (espace,)).fetchone()
        op = emettre(desk, espace, 'espace.membres', espace, _charge_membres(desk, row, [moi], 1), [], ident)
        _appliquer_membres(desk, op)
        desk.db.execute('UPDATE sync56108_personnes SET poste=? WHERE gid=?', (ident.id, p['gid']))
    installer_declencheurs(desk)
    desk.audit('sync56108_espace_cree', {'espace': espace, 'par': str(par)[:80]})
    return {'espace': espace, 'nom': nom, 'personne': p['nom']}


def ouvrir_rejoindre(desk, personne_nom, compte, poste='', par=''):
    """« Rejoindre le cabinet » : personne déclarée, fenêtre de 10 minutes pendant laquelle ce poste écoute (découverte, code affiché) et
    accepte l'invitation signée d'un poste appairé ; ``poste`` : l'autorité attendue (vide : la première qui l'invite)."""
    ensure_schema(desk)
    if desactivee(desk):
        raise Stop('synchronisation_desactivee')
    if espace_cabinet(desk) is not None:
        raise Stop('espace_deja_present')
    _ident(desk)
    p = declarer_personne(desk, personne_nom, compte)
    if poste and not POSTE_RE.fullmatch(str(poste)):
        raise Stop('poste_absent')
    valeur = {'poste': str(poste or ''), 'expire': time.time() + REJOINDRE_DUREE, 'personne': p['gid']}
    desk.setting(REGLAGE_REJOINDRE, valeur)
    desk.audit('sync56108_rejoindre', {'par': str(par)[:80]})
    return valeur


def accepter_adhesion(desk, poste, par='', refuser=False):
    """Poste autorité : accepte (nouvelle version des membres) ou refuse la demande d'adhésion d'un poste appairé."""
    esp = espace_cabinet(desk)
    ident = _ident(desk)
    if esp is None or esp['autorite'] != ident.id:
        raise Stop('autorite_requise')
    a = desk.db.execute("SELECT * FROM sync56108_adhesions WHERE poste=? AND etat='en_attente'", (str(poste or ''),)).fetchone()
    if not a:
        raise Stop('introuvable')
    if refuser:
        desk.db.execute("UPDATE sync56108_adhesions SET etat='refusee',decide_le=? WHERE poste=?", (_maintenant(), a['poste']))
        desk.db.commit()
        desk.audit('sync56108_adhesion_refusee', {'poste': a['poste'], 'par': str(par)[:80]})
        return {'etat': 'refusee', 'nom': a['nom_poste']}
    from .liaison56103 import _poste
    lp = _poste(desk, a['poste'])
    if not lp:
        raise Stop('poste_non_appaire')
    membres = [m for m in _membres_actuels(desk, esp['id']) if m['poste'] != a['poste']]
    membres.append({'poste': a['poste'], 'cle': base64.b64encode(bytes(lp['cle_signature'])).decode('ascii'), 'nom': _nom(lp['nom'], 80),
                    'personnes': [{'gid': a['personne'], 'nom': a['personne_nom']}], 'retire': False})
    with _tx(desk):
        op = emettre(desk, esp['id'], 'espace.membres', esp['id'], _charge_membres(desk, esp, sorted(membres, key=lambda m: m['poste']), esp['version'] + 1),
                     None, ident)
        _appliquer_membres(desk, op)
        desk.db.execute("UPDATE sync56108_adhesions SET etat='acceptee',decide_le=? WHERE poste=?", (_maintenant(), a['poste']))
    desk.audit('sync56108_adhesion_acceptee', {'poste': a['poste'], 'par': str(par)[:80]})
    return {'etat': 'acceptee', 'nom': a['nom_poste'], 'personne': a['personne_nom']}


def retirer_poste(desk, poste, par=''):
    """Poste autorité : retire un poste de l'espace du cabinet et des sous-espaces dont il est l'autorité (plus aucune réception future)."""
    ident = _ident(desk)
    retires = 0
    for esp in desk.db.execute("SELECT * FROM sync56108_espaces WHERE autorite=? AND etat='actif'", (ident.id,)).fetchall():
        if not membre_actif(desk, esp['id'], poste) or poste == ident.id:
            continue
        membres = [dict(m, retire=True) if m['poste'] == poste else m for m in _membres_actuels(desk, esp['id'])]
        with _tx(desk):
            op = emettre(desk, esp['id'], 'espace.membres', esp['id'], _charge_membres(desk, esp, membres, esp['version'] + 1), None, ident)
            _appliquer_membres(desk, op)
        retires += 1
    if not retires:
        raise Stop('introuvable')
    desk.audit('sync56108_poste_retire', {'poste': poste, 'par': str(par)[:80]})
    return {'espaces': retires}


def accepter_invitation(desk, op, de_poste):
    """Invitation reçue dans la réponse « capacités » : dernière version signée des membres d'un espace dont ce poste est membre.
    Espace inconnu : accepté seulement (a) pour le cabinet, si ce poste a ouvert « Rejoindre » vers ce poste appairé, qui en est l'autorité ;
    (b) pour un sous-espace, si son parent est connu et que l'autorité y est membre actif. Signature vérifiée avant toute écriture."""
    valider_forme(op)
    if op['type'] != 'espace.membres':
        raise Stop('operation_invalide')
    ch = valider_membres(op['charge'])
    ident = _ident(desk)
    if ch['autorite'] != op['origine'] or ident.id not in [m['poste'] for m in ch['membres'] if not m['retire']]:
        raise Stop('invitation_refusee')
    connu = desk.db.execute('SELECT * FROM sync56108_espaces WHERE id=?', (op['espace'],)).fetchone()
    if connu:
        m = _membre(desk, op['espace'], connu['autorite'])
        if connu['autorite'] != op['origine'] or not m or not verifier_signature(m['cle'], op):
            raise Stop('signature_refusee')
        if ch['version'] <= connu['version']:
            return False
        with _tx(desk):
            _appliquer_membres(desk, op)
        return True
    if ch['parent']:
        parent = desk.db.execute("SELECT * FROM sync56108_espaces WHERE id=? AND etat='actif'", (ch['parent'],)).fetchone()
        m = _membre(desk, ch['parent'], op['origine']) if parent else None
        if not m or m['retire'] or not verifier_signature(m['cle'], op):
            raise Stop('invitation_refusee')
    else:
        r = rejoindre_en_cours(desk)
        if not r or (r['poste'] and r['poste'] != op['origine']) or op['origine'] != de_poste or espace_cabinet(desk) is not None:
            raise Stop('invitation_refusee')
        from .liaison56103 import _poste
        lp = _poste(desk, op['origine'])
        if not lp or not verifier_signature(base64.b64encode(bytes(lp['cle_signature'])).decode('ascii'), op):
            raise Stop('signature_refusee')
        moi = next(m for m in ch['membres'] if m['poste'] == ident.id)
        if r.get('personne') not in [p['gid'] for p in moi['personnes']]:
            raise Stop('invitation_refusee')
    with _tx(desk):
        _appliquer_membres(desk, op, creer=True)
    if not ch['parent']:
        p = personne_locale(desk)
        desk.db.execute('UPDATE sync56108_personnes SET poste=?, compte=? WHERE gid=?', (ident.id, p['compte'], p['gid']))
        desk.db.execute('DELETE FROM settings WHERE key=?', (REGLAGE_REJOINDRE,))
        desk.db.commit()
        installer_declencheurs(desk)
        desk.audit('sync56108_espace_rejoint', {'espace': op['espace'], 'autorite': op['origine']})
    return True


# ------------------------------------------------------------------------------------------------ réception
def _droits(desk, op, relaye_par, moi):
    """Appartenance, signature d'origine, acteur, relais et droits du type, AVANT toute écriture. Rend la ligne de membre de l'origine."""
    esp = desk.db.execute("SELECT * FROM sync56108_espaces WHERE id=? AND etat='actif'", (op['espace'],)).fetchone()
    if not esp:
        raise Stop('espace_inconnu')
    if not membre_actif(desk, op['espace'], moi):
        raise Stop('espace_non_membre')
    m = _membre(desk, op['espace'], op['origine'])
    if not m:
        raise Stop('origine_non_membre')
    if relaye_par == op['origine'] and m['retire']:
        raise Stop('poste_retire')
    if relaye_par != op['origine'] and not membre_actif(desk, op['espace'], relaye_par):
        raise Stop('relais_non_membre')
    if not verifier_signature(m['cle'], op):
        raise Stop('signature_refusee')
    if op['acteur'] not in [p['gid'] for p in json.loads(m['personnes'] or '[]')]:
        raise Stop('acteur_refuse')
    if op['type'] == 'espace.membres':
        if op['origine'] != esp['autorite']:
            raise Stop('autorite_requise')
        valider_membres(op['charge'])
    else:
        from .sync56108_objets import valider_charge, droits_type
        valider_charge(op['type'], op['charge'])
        droits_type(desk, op, esp)
    return m


def recevoir(desk, op, relaye_par, moi=None):
    """Une opération reçue d'un pair (``relaye_par`` : le poste de la session). Contrôles puis UNE transaction (opération, têtes,
    projection, reçu). Rend ``{'etat': 'recu'|'conflit'|'deja_recue', 'id'}`` ; ``Stop`` si refusée (rien n'est écrit)."""
    valider_forme(op)
    moi = moi or _ident(desk).id
    _droits(desk, op, relaye_par, moi)
    from . import sync56108_objets as O
    O.preparer(desk)   # schémas et identifiant du cabinet AVANT la transaction (ils valideraient une transaction ouverte)
    suites = []
    with _tx(desk):
        etat = _enregistrer(desk, op, relaye_par)
        if etat == 'deja_recue':
            return {'etat': etat, 'id': op['id']}
        if op['type'] == 'espace.membres':
            _appliquer_membres(desk, op)
        else:
            suites = O.apres_reception(desk, op, moi)
    for suite in suites:
        try:
            suite()
        except Exception:   # noqa: BLE001 — projection secondaire (registre JSON) refaite au prochain passage
            pass
    return {'etat': etat, 'id': op['id']}


def recevoir_lot(desk, ops, relaye_par, moi=None):
    """Lot borné ; s'arrête au premier refus (la suite dépend souvent de l'opération refusée). L'accusé ne liste que l'enregistré."""
    acceptees, refus = [], []
    for op in ops[:LOT_OPS] if isinstance(ops, list) else []:
        try:
            r = recevoir(desk, op, relaye_par, moi)
            acceptees.append(r['id'])
        except Stop as ex:
            refus.append({'id': str((op or {}).get('id') if isinstance(op, dict) else '')[:40], 'motif': str(ex).split(':')[0][:60]})
            break
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            refus.append({'id': '', 'motif': 'operation_invalide'})
            break
    return acceptees, refus


# ------------------------------------------------------------------------------------------------ journalisation des commandes locales
def journaliser(desk, limite=500):
    """Transforme les captures (posées par les déclencheurs dans la transaction de la commande) en opérations signées ; une transaction par
    objet. Rend le nombre d'opérations émises."""
    if not _table(desk.db, 'sync56108_capture') or espace_cabinet(desk) is None or desactivee(desk):
        return 0
    from . import sync56108_objets as O
    ident = _ident(desk)
    O.preparer(desk)
    n = 0
    rows = desk.db.execute("SELECT * FROM sync56108_capture ORDER BY CASE genre WHEN 'dossier' THEN 0 ELSE 1 END, quand LIMIT ?", (limite,)).fetchall()
    for cap in rows:
        try:
            with _tx(desk):
                n += O.journaliser_un(desk, ident, cap['genre'], cap['local_id'])
        except Stop as ex:   # objet invalide pour le partage : capture gardée, motif visible ; jamais d'opération partielle
            desk.db.execute('UPDATE sync56108_objets SET motif=? WHERE genre=? AND local_id=?', (str(ex)[:60], cap['genre'], cap['local_id']))
            desk.db.execute('DELETE FROM sync56108_capture WHERE genre=? AND local_id=?', (cap['genre'], cap['local_id']))
            desk.db.commit()
    return n


# ------------------------------------------------------------------------------------------------ curseurs et envoi
def curseurs(desk, espaces):
    out = {e: {} for e in espaces}
    if not espaces:
        return out
    marques = ','.join('?' for _ in espaces)
    for r in desk.db.execute('SELECT espace, origine, MAX(seq) FROM sync56108_operations WHERE espace IN (%s) GROUP BY espace, origine' % marques, espaces):
        out[r[0]][r[1]] = r[2]
    return out


def _lire_curseurs(valeur, permis):
    out = {}
    if not isinstance(valeur, dict):
        return out
    for e, origines in list(valeur.items())[:64]:
        if e not in permis or not isinstance(origines, dict):
            continue
        out[e] = {o: s for o, s in list(origines.items())[:256] if POSTE_RE.fullmatch(str(o)) and type(s) is int and 0 <= s < 2 ** 53}
    return out


def noter_accuse(desk, poste, curs):
    """Curseurs déclarés par le pair (enregistrement durable de son côté) : base du « en attente » et de l'état « À jour »."""
    for e, origines in curs.items():
        for o, s in origines.items():
            desk.db.execute('INSERT INTO sync56108_pairs(poste,espace,origine,seq,maj_le) VALUES(?,?,?,?,?) ON CONFLICT(poste,espace,origine) '
                            'DO UPDATE SET seq=MAX(seq, excluded.seq), maj_le=excluded.maj_le', (poste, e, o, s, _maintenant()))
    desk.db.commit()


def a_envoyer(desk, poste, curs, moi, limite=LOT_OPS, octets=LOT_OCTETS):
    """Opérations que le pair n'a pas (selon ses curseurs), dans l'ordre local d'enregistrement (causal), bornées."""
    espaces = espaces_communs(desk, poste, moi)
    mes = curseurs(desk, espaces)
    candidats = []
    for e in espaces:
        for o, s in mes[e].items():
            deja = curs.get(e, {}).get(o, 0)
            if s > deja:
                candidats += desk.db.execute('SELECT rang, brut FROM sync56108_operations WHERE espace=? AND origine=? AND seq>? ORDER BY seq LIMIT ?',
                                             (e, o, deja, limite)).fetchall()
    candidats.sort(key=lambda r: r[0])
    out, taille = [], 0
    for r in candidats[:limite]:
        taille += len(r[1])
        if out and taille > octets:
            break
        out.append(json.loads(r[1]))
    return out


def en_attente(desk, poste, moi):
    """Nombre d'opérations que ce pair n'a pas encore accusées (espaces communs)."""
    total = 0
    for e in espaces_communs(desk, poste, moi):
        for o, s in curseurs(desk, [e])[e].items():
            r = desk.db.execute('SELECT seq FROM sync56108_pairs WHERE poste=? AND espace=? AND origine=?', (poste, e, o)).fetchone()
            total += max(0, s - (r[0] if r else 0))
    return total


def _capacites(desk, moi, pour=None):
    from . import __version__ as version
    out = {'t': 'sync.v1.capabilities', 'v': PROTOCOLE, 'version': str(version)[:20], 'domaines': list(VERSION_DOMAINES)}
    r = rejoindre_en_cours(desk)
    p = personne_locale(desk)
    if r and p and (not r['poste'] or r['poste'] == pour):
        out['adhesion'] = {'personne': p['gid'], 'nom': p['nom']}
    return out


def _invitations(desk, poste, moi):
    """Dernière version signée des membres de chaque espace dont le pair est membre actif (connue de ce poste)."""
    out = []
    for e in espaces_communs(desk, poste, moi):
        r = desk.db.execute("SELECT brut FROM sync56108_operations WHERE espace=? AND type='espace.membres' ORDER BY rang DESC LIMIT 1", (e,)).fetchone()
        if r:
            out.append(json.loads(r[0]))
    return out


def _noter_capacites(desk, poste, obj):
    domaines = [d for d in (obj.get('domaines') or []) if d in VERSION_DOMAINES] if isinstance(obj.get('domaines'), list) else []
    desk.db.execute('INSERT INTO sync56108_capacites(poste,protocole,version,domaines,vu_le) VALUES(?,?,?,?,?) ON CONFLICT(poste) DO UPDATE SET '
                    'protocole=excluded.protocole,version=excluded.version,domaines=excluded.domaines,vu_le=excluded.vu_le',
                    (poste, int(obj.get('v') or 0) if type(obj.get('v')) is int else 0, str(obj.get('version') or '')[:20], json.dumps(domaines),
                     _maintenant()))
    desk.db.commit()


def _noter_adhesion(desk, poste, adhesion, moi):
    esp = espace_cabinet(desk)
    if not esp or esp['autorite'] != moi or not isinstance(adhesion, dict) or membre_actif(desk, esp['id'], poste):
        return
    gid, nom = str(adhesion.get('personne') or ''), _nom(adhesion.get('nom'))
    if not PERS_RE.fullmatch(gid) or len(nom) < 2:
        return
    lp = desk.db.execute('SELECT nom FROM liaison56103_postes WHERE id=?', (poste,)).fetchone()
    desk.db.execute("INSERT INTO sync56108_adhesions(poste,espace,personne,personne_nom,nom_poste,etat,recu_le) VALUES(?,?,?,?,?,'en_attente',?) "
                    "ON CONFLICT(poste) DO UPDATE SET personne=excluded.personne,personne_nom=excluded.personne_nom,nom_poste=excluded.nom_poste,"
                    "etat=CASE WHEN etat='refusee' THEN 'refusee' ELSE 'en_attente' END, recu_le=excluded.recu_le",
                    (poste, esp['id'], gid, nom, (lp['nom'] if lp else '')[:80], _maintenant()))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ côté appelé (dans la session de liaison)
def traiter(desk, poste_id, obj, donnees, moi):
    """Trames ``sync.v1.*`` reçues d'un poste appairé (session authentifiée). Refus neutre si la synchronisation est désactivée."""
    t = obj.get('t')
    if desactivee(desk):
        raise Stop('synchronisation_desactivee')
    if t == 'sync.v1.capabilities':
        _noter_capacites(desk, poste_id, obj)
        _noter_adhesion(desk, poste_id, obj.get('adhesion'), moi)
        return {**_capacites(desk, moi, poste_id), 'invitations': _invitations(desk, poste_id, moi)}
    communs = espaces_communs(desk, poste_id, moi)
    if t in ('sync.v1.summary', 'sync.v1.need'):
        journaliser(desk)   # commandes locales capturées : publiées avant de répondre (le pair ne voit jamais un état en retard)
    if t == 'sync.v1.summary':
        noter_accuse(desk, poste_id, _lire_curseurs(obj.get('curseurs'), communs))
        return {'t': 'sync.v1.summary', 'curseurs': curseurs(desk, communs)}
    if t == 'sync.v1.operations':
        journaliser(desk)   # une modification locale en attente devient d'abord une version (jamais écrasée par la réception)
        acceptees, refus = recevoir_lot(desk, obj.get('ops'), poste_id, moi)
        communs = espaces_communs(desk, poste_id, moi)
        return {'t': 'sync.v1.ack', 'acceptees': acceptees, 'refus': refus, 'curseurs': curseurs(desk, communs)}
    if t == 'sync.v1.need':
        curs = _lire_curseurs(obj.get('curseurs'), communs)
        noter_accuse(desk, poste_id, curs)
        ops = a_envoyer(desk, poste_id, curs, moi)
        return {'t': 'sync.v1.operations', 'ops': ops, 'encore': len(ops) >= LOT_OPS}
    if t == 'sync.v1.ack':
        noter_accuse(desk, poste_id, _lire_curseurs(obj.get('curseurs'), communs))
        return {'t': 'ok'}
    if t == 'sync.v1.blob_need':
        from .sync56108_fichiers import servir_bloc
        entete, data = servir_bloc(desk, poste_id, moi, obj)
        return {**entete, '_donnees': data}
    raise Stop('trame_inconnue')


# ------------------------------------------------------------------------------------------------ côté appelant
def _noter_echange(desk, poste, **champs):
    desk.db.execute('INSERT OR IGNORE INTO sync56108_echanges(poste) VALUES(?)', (poste,))
    if champs:
        desk.db.execute('UPDATE sync56108_echanges SET %s WHERE poste=?' % ','.join('%s=?' % k for k in champs), list(champs.values()) + [poste])
    desk.db.commit()


def echanger(desk, canal, poste, moi, max_tours=20):
    """Dans une session de liaison ouverte par ce poste : capacités (invitations), résumé, envoi des opérations manquantes au pair, puis
    réception de celles qui manquent ici, puis blocs de fichiers en attente. Un pair ancien (« trame inconnue ») : messages seulement."""
    if desactivee(desk):
        return {'sync': 'desactivee'}
    journaliser(desk)
    _noter_echange(desk, poste, debut=_maintenant(), en_cours=1, refus='')
    bilan = {'envoyees': 0, 'recues': 0, 'refus': ''}
    try:
        canal.envoyer(_capacites(desk, moi, poste))
        r, _ = canal.recevoir()
        if r.get('t') == 'refus':
            motif = str(r.get('motif') or '')
            if motif == 'trame_inconnue':   # 5.6.103 à 5.6.107 : messages seulement, jamais « à jour » pour les données
                _noter_capacites(desk, poste, {'v': 0, 'version': 'ancienne', 'domaines': ['messages']})
                return {'sync': 'ancien'}
            bilan['refus'] = motif[:60]
            return bilan
        _noter_capacites(desk, poste, r)
        for inv in (r.get('invitations') or [])[:16] if isinstance(r.get('invitations'), list) else []:
            try:
                accepter_invitation(desk, inv, poste)
            except Stop:
                pass
        communs = espaces_communs(desk, poste, moi)
        if not communs:
            return bilan
        canal.envoyer({'t': 'sync.v1.summary', 'curseurs': curseurs(desk, communs)})
        r, _ = canal.recevoir()
        curs = _lire_curseurs(r.get('curseurs'), communs)
        noter_accuse(desk, poste, curs)
        for _ in range(max_tours):   # envoi
            ops = a_envoyer(desk, poste, curs, moi)
            if not ops:
                break
            canal.envoyer({'t': 'sync.v1.operations', 'ops': ops})
            r, _ = canal.recevoir()
            acceptees = r.get('acceptees') if isinstance(r.get('acceptees'), list) else []
            bilan['envoyees'] += len(acceptees)
            curs = _lire_curseurs(r.get('curseurs'), espaces_communs(desk, poste, moi))
            noter_accuse(desk, poste, curs)
            if r.get('refus'):
                bilan['refus'] = str((r['refus'][0] or {}).get('motif') or 'refus')[:60]
                break
            if not acceptees:
                break
        for _ in range(max_tours):   # réception
            communs = espaces_communs(desk, poste, moi)
            canal.envoyer({'t': 'sync.v1.need', 'curseurs': curseurs(desk, communs)})
            r, _ = canal.recevoir()
            ops = r.get('ops') if isinstance(r.get('ops'), list) else []
            if not ops:
                break
            acceptees, refus = recevoir_lot(desk, ops, poste, moi)
            bilan['recues'] += len(acceptees)
            if refus:
                bilan['refus'] = refus[0]['motif']
                break
            if not r.get('encore'):
                break
        canal.envoyer({'t': 'sync.v1.ack', 'curseurs': curseurs(desk, espaces_communs(desk, poste, moi))})
        canal.recevoir()
        from .sync56108_fichiers import recuperer_blocs
        bilan['octets'] = recuperer_blocs(desk, canal, poste, moi)
        return bilan
    finally:
        _noter_echange(desk, poste, fin=_maintenant(), en_cours=0, envoyees=bilan.get('envoyees', 0), recues=bilan.get('recues', 0),
                       refus=bilan.get('refus', ''), octets=bilan.get('octets', 0) or 0)


# ------------------------------------------------------------------------------------------------ choix des dossiers à partager
def partager_dossier(desk, dossier_local, personnes=None, par=''):
    """Choix explicite d'un dossier à partager (avec ses tâches et événements). Dossier réservé : sous-espace dont seuls les postes des
    personnes autorisées (``personnes`` : identifiants globaux, la personne de ce poste comprise) sont membres."""
    esp = espace_cabinet(desk)
    if esp is None:
        raise Stop('espace_absent')
    row = desk.db.execute('SELECT * FROM dossiers5697 WHERE id=?', (str(dossier_local or ''),)).fetchone()
    if not row:
        raise Stop('introuvable')
    if desk.db.execute('SELECT 1 FROM sync56108_partages WHERE dossier=?', (row['id'],)).fetchone():
        return {'deja': True}
    cible = esp['id']
    ident = _ident(desk)
    if row['confidentialite'] == 'reserve':
        moi = personne_locale(desk)
        voulues = sorted({str(p) for p in (personnes or []) if PERS_RE.fullmatch(str(p))} | {moi['gid']})
        membres = {}
        for m in _membres_actuels(desk, esp['id']):
            if m['retire']:
                continue
            gardees = [p for p in m['personnes'] if p['gid'] in voulues]
            if gardees:
                membres[m['poste']] = dict(m, personnes=gardees)
        if len(membres) < 2:
            raise Stop('personnes_autorisees_requises')
        sous = nouvel_id('esp')
        with _tx(desk):
            desk.db.execute("INSERT INTO sync56108_espaces(id,nom,parent,autorite,dossier,version,categories,etat,cree_le) VALUES(?,?,?,?,'',0,'{}','actif',?)",
                            (sous, 'Dossier réservé', esp['id'], ident.id, _maintenant()))
            for m in membres.values():
                desk.db.execute('INSERT INTO sync56108_membres(espace,poste,cle,nom,personnes,retire) VALUES(?,?,?,?,?,0)',
                                (sous, m['poste'], m['cle'], m['nom'], json.dumps(m['personnes'], ensure_ascii=False)))
            srow = desk.db.execute('SELECT * FROM sync56108_espaces WHERE id=?', (sous,)).fetchone()
            op = emettre(desk, sous, 'espace.membres', sous, _charge_membres(desk, srow, sorted(membres.values(), key=lambda m: m['poste']), 1), [], ident)
            _appliquer_membres(desk, op)
        cible = sous
    with _tx(desk, garde=False):
        desk.db.execute('INSERT INTO sync56108_partages(dossier,espace,par,quand) VALUES(?,?,?,?)', (row['id'], cible, str(par)[:80], _maintenant()))
        desk.db.execute("INSERT OR IGNORE INTO sync56108_capture(genre,local_id,quand) VALUES('dossier',?,?)", (row['id'], _maintenant()))
        for t in desk.db.execute('SELECT id FROM tasks WHERE matter=?', (row['id'],)).fetchall() if _table(desk.db, 'tasks') else []:
            desk.db.execute("INSERT OR IGNORE INTO sync56108_capture(genre,local_id,quand) VALUES('tache',?,?)", (t[0], _maintenant()))
        for ev in desk.db.execute('SELECT id FROM agenda5634 WHERE matter=?', (row['id'],)).fetchall() if _table(desk.db, 'agenda5634') else []:
            desk.db.execute("INSERT OR IGNORE INTO sync56108_capture(genre,local_id,quand) VALUES('evenement',?,?)", (ev[0], _maintenant()))
    n = journaliser(desk)
    desk.audit('sync56108_dossier_partage', {'dossier': row['id'], 'reserve': row['confidentialite'] == 'reserve', 'par': str(par)[:80]})
    return {'espace': cible, 'operations': n}


def desactiver(desk, desactive, par=''):
    """Administrateur : désactiver ferme l'écoute (le processus de liaison s'arrête s'il ne sert qu'à la synchronisation)."""
    desk.setting(REGLAGE_DESACTIVE, bool(desactive))
    desk.audit('sync56108_desactivation', {'desactive': bool(desactive), 'par': str(par)[:80]})
    return {'desactive': bool(desactive)}


# ------------------------------------------------------------------------------------------------ résolution de conflit
def versions(desk, objet):
    """Têtes d'un objet (versions concurrentes) avec leur charge, pour la comparaison."""
    out = []
    for oid in tetes(desk, objet):
        r = desk.db.execute('SELECT brut FROM sync56108_operations WHERE id=?', (oid,)).fetchone()
        op = json.loads(r[0])
        nom = desk.db.execute('SELECT nom FROM sync56108_personnes WHERE gid=?', (op['acteur'],)).fetchone()
        out.append({'id': oid, 'type': op['type'], 'charge': op['charge'], 'origine': op['origine'], 'acteur': nom[0] if nom else '', 'quand': op['quand']})
    return out


def resoudre(desk, objet, choix, par=''):
    """L'avocat choisit une version (identifiant d'une tête) : nouvelle version qui référence toutes les têtes, projetée ici."""
    from . import sync56108_objets as O
    o = desk.db.execute('SELECT * FROM sync56108_objets WHERE objet=?', (str(objet or ''),)).fetchone()
    if not o:
        raise Stop('introuvable')
    hs = tetes(desk, o['objet'])
    if len(hs) < 2:
        raise Stop('aucun_conflit')
    if choix not in hs:
        raise Stop('version_inconnue')
    choisie = json.loads(desk.db.execute('SELECT brut FROM sync56108_operations WHERE id=?', (choix,)).fetchone()[0])
    genre = GENRES[o['objet'][:3]]
    suites = []
    with _tx(desk):
        op = emettre(desk, o['espace'], genre + '.resolution', o['objet'], dict(choisie['charge']), hs)
        suites = O.projeter_locale(desk, op)
    for suite in suites:
        try:
            suite()
        except Exception:   # noqa: BLE001
            pass
    desk.audit('sync56108_conflit_resolu', {'objet': o['objet'], 'par': str(par)[:80]})
    return {'objet': o['objet'], 'operation': op['id']}

"""5.6.109 (lot H, F09) : historique des instructions de l'assistant — une seule archive, lue sans écrire, par personne.

Rien de parallèle : les discussions restent dans ``assistant_threads`` et ``assistant_messages`` (tables canoniques de ``Desk``) ; ce module
ajoute seulement des **associations** (propriétaire, participants, préférences personnelles, groupes, liens, révisions de contexte), créées au
démarrage (``ensure_schema`` depuis ``Desk._prepare``), jamais pendant une lecture.

- **Attribution** : un fil nouveau reçoit le compte de la requête authentifiée (``membres5650.current_author``) ou l'auteur établi par le serveur
  (``auteur`` d'une mission) ; jamais un champ du navigateur ni du modèle. Les fils antérieurs restent **à attribuer** : invisibles dans
  l'historique de chacun, même sur une installation à un seul compte ; seul l'administrateur voit leur existence (date, dossier, nombre de
  messages, jamais le titre ni le contenu) et peut les attribuer à un compte du cabinet avec une preuve écrite, tracée au journal.
- **Lecture** (``fils``, ``messages``, ``recherche``, ``exporter``) : ni mise en file, ni synchronisation, ni purge, ni écriture ; utilisable
  IA en pause. Portée : cabinet, fil non supprimé, propriétaire ou participant nominatif ; puis droits ACTUELS sur le dossier et le courriel
  (``acces5697``) dans la requête, avant LIMIT et avant tout titre, extrait ou compteur ; un message dont une source citée est devenue
  interdite est masqué par une règle fixe (jamais par le modèle). Curseurs de liste opaques, signés, liés au compte, aux filtres, expirant.
- **Archive et contexte du modèle séparés** : l'archive n'est plus purgée (ni à la lecture, ni par nombre) ; le contexte transmis au modèle
  reste la mémoire bornée de ``workspace.chat`` (100 derniers échanges par portée, table ``conversations``), qui n'efface jamais l'archive.
  Anciennes purges retirées : ``integration.threads`` (90 jours, à chaque liste), ``workspace.history`` (90 jours, à chaque ouverture).
- **Reprise** : rouvrir un fil est une lecture (``reexecution: False``) ; « Continuer » crée un nouveau message par le circuit existant
  (``integration.submit_question``), qui vérifie ici le droit de continuer (propriétaire ou participant) et refuse un fil supprimé.
- **Statut réel** : état brut du message et du travail conservés, traduits pour l'écran (Envoyée, En attente, En cours, En pause, À préciser,
  Terminée, Échec, Annulée) ; un accusé d'enregistrement n'est pas une réponse.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .common import Stop

PREFIXE = 'm56109/historique/'
LECTURES = ('fils', 'fil', 'messages', 'recherche', 'exporter', 'a-attribuer', 'brouillon', 'membres', 'groupes', 'corbeille', 'politique')
ECRITURES = ('brouillon', 'attribuer')
ID_FIL = re.compile(r'^[a-f0-9]{32}$')
LIMITE_MAX = 100
RECHERCHE_MAX = 200
CURSEUR_TTL = 3600
PERIODES = {'7j': 7, '30j': 30, '90j': 90, '365j': 365}
ROLES_PARTAGE = ('viewer', 'contributor')
ROLES_LIBELLES = {'proprietaire': 'Propriétaire', 'viewer': 'Lecteur', 'contributor': 'Participant'}
ETATS = {'envoyee': 'Envoyée', 'en_attente': 'En attente', 'en_cours': 'En cours', 'en_pause': 'En pause', 'a_preciser': 'À préciser',
         'terminee': 'Terminée', 'echec': 'Échec', 'annulee': 'Annulée'}
ETATS_ACTIFS = ('envoyee', 'en_attente', 'en_cours', 'en_pause', 'a_preciser')
AVIS_ETAT = {'envoyee': '⏳ En file d’attente prioritaire.', 'en_attente': '⏳ En file d’attente prioritaire.', 'en_cours': '⏳ Réponse en cours de génération.',
             'en_pause': 'En pause : la demande est conservée et reprendra à la levée de la pause.',
             'echec': '⚠️ La réponse n’a pas pu être produite. Consultez l’opération correspondante.'}
JOBS_ACTIFS = ('pending', 'running', 'suspended', 'cancel_requested')
MASQUE = 'Contenu masqué : une source citée dans ce message ne vous est plus accessible.'
BROUILLON_MAX = 12000
CONTEXTE_BROUILLON = re.compile(r'^[A-Za-z0-9_:./?=&-]{1,200}$')
_CLE = secrets.token_bytes(32)       # curseurs : signés pour ce processus (un redémarrage les périme : « Actualiser »)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS assistant_thread_owners109(thread_id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, owner_id TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)), deleted_at TEXT, deleted_by TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1, attribue_le TEXT NOT NULL DEFAULT '', attribue_par TEXT NOT NULL DEFAULT '', preuve TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS assistant_owner109 ON assistant_thread_owners109(cabinet_id, owner_id, archived, thread_id);
CREATE TABLE IF NOT EXISTS assistant_thread_members109(thread_id TEXT NOT NULL, cabinet_id TEXT NOT NULL, user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('viewer','contributor')), granted_by TEXT NOT NULL, created_at TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(thread_id, cabinet_id, user_id));
CREATE INDEX IF NOT EXISTS assistant_shared109 ON assistant_thread_members109(cabinet_id, user_id, thread_id);
CREATE TABLE IF NOT EXISTS assistant_groups109(id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL, owner_id TEXT NOT NULL, name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1, UNIQUE(id, cabinet_id, owner_id));
CREATE TABLE IF NOT EXISTS assistant_thread_preferences109(thread_id TEXT NOT NULL, cabinet_id TEXT NOT NULL, user_id TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)), archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
  hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)), group_id TEXT, revision INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(thread_id, cabinet_id, user_id),
  FOREIGN KEY(group_id, cabinet_id, user_id) REFERENCES assistant_groups109(id, cabinet_id, owner_id) ON DELETE RESTRICT);
CREATE INDEX IF NOT EXISTS assistant_preferences109 ON assistant_thread_preferences109(cabinet_id, user_id, pinned, archived, group_id);
CREATE TABLE IF NOT EXISTS assistant_thread_links109(thread_id TEXT NOT NULL, cabinet_id TEXT NOT NULL,
  source_kind TEXT NOT NULL CHECK(source_kind IN ('matter','piece','mail','team_thread','ai_thread','deliverable')),
  source_id TEXT NOT NULL, source_version TEXT NOT NULL DEFAULT '', source_role TEXT NOT NULL CHECK(source_role IN ('association','used_context')),
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(thread_id, source_kind, source_id, source_version, source_role));
CREATE TABLE IF NOT EXISTS assistant_thread_contexts109(thread_id TEXT NOT NULL, revision INTEGER NOT NULL, matter TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '', from_message_id INTEGER NOT NULL DEFAULT 0, changed_by TEXT NOT NULL, changed_at TEXT NOT NULL,
  PRIMARY KEY(thread_id, revision));
CREATE TABLE IF NOT EXISTS assistant_drafts109(cabinet_id TEXT NOT NULL, user_id TEXT NOT NULL, contexte TEXT NOT NULL, texte TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1, maj_le TEXT NOT NULL, PRIMARY KEY(cabinet_id, user_id, contexte));
CREATE TABLE IF NOT EXISTS assistant_ops109(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
# colonnes d'une installation qui aurait appliqué le schéma indicatif du dossier technique (sans ces colonnes)
COLONNES_OWNERS = (('deleted_at', 'TEXT'), ('deleted_by', "TEXT NOT NULL DEFAULT ''"), ('revision', 'INTEGER NOT NULL DEFAULT 1'),
                   ('attribue_le', "TEXT NOT NULL DEFAULT ''"), ('attribue_par', "TEXT NOT NULL DEFAULT ''"), ('preuve', "TEXT NOT NULL DEFAULT ''"))

MESSAGES = {
    'introuvable': 'Discussion introuvable, ou vous n’y avez pas accès.',
    'role_insuffisant': 'Votre rôle ne permet pas cette action.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'curseur_invalide': 'La liste a changé ou a expiré : actualisez-la.',
    'parametre_invalide': 'Paramètre invalide.',
    'recherche_trop_longue': 'Recherche trop longue (200 caractères au plus).',
    'export_interdit': 'Votre compte ne permet pas d’exporter des données.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'operation_en_cours': 'Cette action est déjà en cours : patientez.',
    'preuve_requise': 'Indiquez la preuve de l’attribution (origine vérifiée de ces échanges).',
    'compte_inconnu': 'Ce compte n’existe pas ou n’est pas actif dans le cabinet.',
    'brouillon_trop_long': 'Brouillon trop long.',
    'requete_trop_longue': 'Demande trop longue.',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : rien n’a été affiché.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'export_interdit': '403 Forbidden',
           'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict', 'conflit_de_revision': '409 Conflict', 'curseur_invalide': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'controle_acces_indisponible': '503 Service Unavailable'}


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ------------------------------------------------------------------------------------------------ schéma (démarrage seulement)
def ensure_schema(desk):
    """``Desk._prepare`` : tables d'association (idempotent ; sans changement, aucune écriture). Jamais appelé par une lecture."""
    noms = re.findall(r'CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA)
    existants = {r[0] for r in desk.db.execute('SELECT name FROM sqlite_master')}
    if any(n not in existants for n in noms):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(assistant_thread_owners109)')}
    for nom, definition in COLONNES_OWNERS:
        if nom not in colonnes:
            desk.db.execute('ALTER TABLE assistant_thread_owners109 ADD COLUMN %s %s' % (nom, definition))
    if desk.db.in_transaction:
        desk.db.commit()
    migrer_compte_unique(desk)


# ------------------------------------------------------------------------------------------------ identité unique de l'installation (décision du cabinet)
CLE_MIGRATION = 'historique109:migration_compte_unique'
PREUVE_UNIQUE = 'identité unique de l’installation à la date de la migration (%s)'
INFO_COMPTE_UNIQUE = ('Quand une seule identité peut être l’auteur des anciennes discussions (un seul compte, ou l’identité unique du serveur sans '
                      'compte de membre), elles lui sont attribuées d’office au démarrage, une seule fois, avec une trace au journal. Avec plusieurs '
                      'identités, l’application Nextcloud ou un nombre de comptes incertain, l’attribution se fait ici, avec une preuve.')


def _lire_identites(chemin, sql):
    import sqlite3
    con = sqlite3.connect('file:%s?mode=ro' % chemin.as_posix(), uri=True, timeout=2)
    try:
        rows = con.execute(sql).fetchall()
    finally:
        con.close()
    return {str(r[0] or '').strip() for r in rows} - {''}


def _identites_installation(desk):
    """``(mode, identités actives)`` établis avec certitude, ou None. Lecture seule ; une base illisible lève (panne du décompte).

    L'identité d'un acteur suit ``web567.actor`` : en-tête posé par l'accès authentifié, sinon « cabinet ».
    - ``autonome`` (distribution autonome, ``users.sqlite3``) : l'acteur est l'adresse du compte actif (``standalone_auth``, ``owner_for``) ;
    - ``serveur_comptes`` (version serveur avec comptes, ``comptes5648.sqlite3``) : l'administrateur est TOUJOURS l'identité « cabinet »
      (``connexion5648.admin_user``), chaque membre actif son identifiant : l'administrateur seul = une identité ;
    - ``authentification_serveur`` (ni l'un ni l'autre) : aucun en-tête d'identité, toute requête est « cabinet » ; d'autres personnes ne
      peuvent apparaître que par un jeton personnel ou un en-tête, que ``_autres_identites_vues`` relève ;
    - application Nextcloud (``exapp``, ``app_api``) : l'acteur est le compte Nextcloud de la requête ; la liste des comptes Nextcloud
      autorisés n'est pas connue localement : unicité NON établie (None) ; de même si les deux registres de comptes coexistent."""
    from pathlib import Path
    from .missions567 import auth_root
    c = desk.c or {}
    if c.get('exapp') or (c.get('nextcloud') or {}).get('auth') == 'app_api':
        return None
    autonome = auth_root() / 'users.sqlite3'
    serveur = Path(c['state_dir']) / 'comptes5648.sqlite3'
    if autonome.is_file() and serveur.is_file():
        return None
    if autonome.is_file():
        return 'autonome', sorted(_lire_identites(autonome, 'SELECT email FROM users WHERE active=1'))
    if serveur.is_file():
        return 'serveur_comptes', sorted({'cabinet'} | _lire_identites(serveur, 'SELECT ident FROM users WHERE active=1'))
    return 'authentification_serveur', ['cabinet']


def _autres_identites_vues(desk, ident):
    """Autres identités déjà connues du serveur : membres vus, droits et réservations par compte, propriétaires de fils, de missions,
    d'assistances et de jetons personnels. Leur présence contredit « une seule identité ». Le jeton de service et l'identité réservée au
    participant IA ne sont pas des personnes. Registre illisible : lève (décompte incertain)."""
    from . import membres5650
    tables = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    vus = set()
    for table, colonne in (('membres5650', 'owner'), ('assistant_thread_owners109', 'owner_id'), ('missions_v567', 'owner'),
                           ('assistances5689', 'owner'), ('jetons5688', 'owner')):
        if table in tables:
            vus |= {str(r[0] or '') for r in desk.db.execute('SELECT DISTINCT %s FROM %s' % (colonne, table))}
    registre = membres5650.load(desk.c['state_dir'])
    vus |= {str(k) for k in (registre.get('droits') or {})}
    for owners in (registre.get('reserves') or {}).values():
        vus |= {str(o) for o in owners}
    return {v.strip() for v in vus} - {ident, 'jeton_service', 'pilote', ''}


def migrer_compte_unique(desk):
    """Démarrage seulement (jamais pendant un GET) : une seule décision, gardée dans ``CLE_MIGRATION``.
    - une seule identité possible (voir ``_identites_installation``) et aucune autre identité vue : les fils sans propriétaire existant à
      cet instant lui sont attribués (preuve, trace au journal avec le mode) ;
    - plusieurs identités : décision « plusieurs comptes » enregistrée, jamais rejouée ; rien n'est attribué, ni maintenant ni plus tard
      (un fil créé après l'apparition d'une seconde identité n'est jamais attribué d'office) ;
    - unicité non établie (application Nextcloud, registres contradictoires) ou panne du décompte : rien n'est attribué ni enregistré ;
      nouvel examen au démarrage suivant (c'est le cas des installations serveur laissées sans décision par la règle précédente)."""
    if desk.settings(CLE_MIGRATION, None) is not None:
        return None
    try:
        etabli = _identites_installation(desk)
        if etabli is None:
            return None
        mode, identites = etabli
        autres = _autres_identites_vues(desk, identites[0]) if len(identites) == 1 else set()
    except Exception:   # noqa: BLE001 — décompte en panne : aucune attribution, aucune décision gardée
        return None
    quand = maintenant()
    if len(identites) != 1 or autres:
        nombre = len(identites) + len(autres)
        desk.setting(CLE_MIGRATION, {'etat': 'plusieurs_comptes', 'mode': mode, 'date': quand, 'identites': nombre})
        desk.audit('historique56109_compte_unique', {'etat': 'plusieurs_comptes', 'mode': mode, 'identites': nombre})
        return 0
    ident = identites[0]
    preuve = PREUVE_UNIQUE % quand[:10]
    n = 0
    for (tid,) in desk.db.execute('SELECT t.id FROM assistant_threads t WHERE NOT EXISTS(SELECT 1 FROM assistant_thread_owners109 o '
                                  'WHERE o.thread_id=t.id)').fetchall():
        n += desk.db.execute('INSERT OR IGNORE INTO assistant_thread_owners109(thread_id,cabinet_id,owner_id,revision,attribue_le,attribue_par,preuve) '
                             'VALUES(?,?,?,1,?,?,?)', (tid, cabinet(desk), ident, quand, 'migration_compte_unique', preuve)).rowcount
    desk.db.commit()
    desk.setting(CLE_MIGRATION, {'etat': 'attribue', 'mode': mode, 'date': quand, 'identite': _empreinte(ident), 'fils': n})
    desk.audit('historique56109_compte_unique', {'etat': 'attribue', 'mode': mode, 'identite': _empreinte(ident), 'fils': n, 'preuve': preuve})
    return n


def schema_present(desk):
    try:
        return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_thread_owners109'").fetchone())
    except Exception:   # noqa: BLE001
        return False


# ------------------------------------------------------------------------------------------------ identité
def cabinet(desk):
    return str((getattr(desk, 'c', None) or {}).get('cabinet_id') or 'cabinet')[:120]


def acteur(desk, qui):
    """(cabinet, compte, rôle) ; acteur absent ou illisible : ``introuvable`` (jamais un administrateur par défaut)."""
    from . import acces5697
    try:
        a = acces5697.acteur_de(qui)
    except Stop:
        raise Stop('introuvable') from None
    return cabinet(desk), a.compte, a.role


def acteur_requete_ou_none():
    """Acteur de la requête en cours (contrôle central ``membres5650.gate``), ou None hors requête (travaux du cabinet)."""
    from . import acces5697
    a = acces5697.acteur_requete()
    return None if a == acces5697.AGENT else (a.compte, a.role)


# ------------------------------------------------------------------------------------------------ attribution (dans la transaction de l'appelant)
def attribuer_creation(desk, thread_id, auteur=''):
    """Fil NOUVEAU : propriétaire = auteur établi par le serveur, sinon compte de la requête. Hors requête et sans auteur : rien (à attribuer).
    Pas de validation ici : l'appelant (``submit_question``) valide fil, message, attribution et travail ensemble."""
    if not schema_present(desk):
        return False
    from . import membres5650
    owner = str(auteur or membres5650.current_author() or '').strip()[:200]
    if not owner:
        return False
    desk.db.execute('INSERT OR IGNORE INTO assistant_thread_owners109(thread_id,cabinet_id,owner_id,revision,attribue_le,attribue_par) VALUES(?,?,?,?,?,?)',
                    (thread_id, cabinet(desk), owner, 1, maintenant(), 'creation'))
    return True


def controler_continuation(desk, thread_id, auteur=''):
    """Nouveau message dans un fil existant : propriétaire ou participant (rôle « contributor ») seulement ; fil supprimé : refusé ; fil non
    attribué : refusé à toute personne (seuls les travaux du cabinet, sans requête, le poursuivent). Refus neutre (``conversation_autre_contexte``)."""
    if not schema_present(desk):
        return
    from . import membres5650
    compte = str(auteur or membres5650.current_author() or '').strip()
    o = desk.db.execute('SELECT * FROM assistant_thread_owners109 WHERE thread_id=?', (thread_id,)).fetchone()
    if o is not None and o['deleted_at']:
        raise Stop('conversation_supprimee')
    if not compte:
        return
    if o is None or o['cabinet_id'] != cabinet(desk):
        raise Stop('conversation_autre_contexte')
    if o['owner_id'] == compte:
        return
    m = desk.db.execute("SELECT role FROM assistant_thread_members109 WHERE thread_id=? AND cabinet_id=? AND user_id=?",
                        (thread_id, o['cabinet_id'], compte)).fetchone()
    if m is None or m['role'] != 'contributor':
        raise Stop('conversation_autre_contexte')
    from .discussions56109 import politique
    # le participant agit avec ses propres droits : le dossier du fil doit lui rester lisible
    t = desk.db.execute('SELECT matter, mail_key FROM assistant_threads WHERE id=?', (thread_id,)).fetchone()
    role = membres5650.viewer_role() if not auteur else _role_annuaire(desk, compte)
    if t is None or not _fil_lisible(desk, (compte, role), dict(t)) or not politique(desk)['internal_share_enabled']:
        raise Stop('conversation_autre_contexte')


def _role_annuaire(desk, compte):
    try:
        from . import membres5650
        return (membres5650.directory(desk).get(compte) or {}).get('role') or 'assistant'
    except Exception:   # noqa: BLE001 — annuaire illisible : le rôle le plus restreint
        return 'assistant'


# ------------------------------------------------------------------------------------------------ contrôles (droits actuels)
def _fil_lisible(desk, a, t):
    """Droits actuels sur le dossier ET le courriel du fil (``acces5697``) ; une panne du contrôle refuse."""
    from . import acces5697
    try:
        if not acces5697.peut_lire_dossier(desk, a, str(t.get('matter') or '')):
            return False
        cle = str(t.get('mail_key') or '')
        if cle and acces5697.masques(desk, a) and not acces5697.peut_lire_message(desk, a, cle):
            return False
    except Stop:
        return False
    return True


def _sources(message):
    try:
        valeur = json.loads(message.get('sources') or '[]')
    except (TypeError, ValueError):
        return None                      # illisible : traité comme une provenance inconnue
    return valeur if isinstance(valeur, list) else None


def _message_lisible(desk, a, message, caches=None):
    """Chaque source citée qui porte un dossier doit rester lisible ; provenance illisible : masquée pour un acteur qui a des restrictions."""
    from . import acces5697
    if caches is None:
        try:
            caches = acces5697.masques(desk, a)
        except Stop:
            return False
    sources = _sources(message)
    if sources is None:
        return not caches
    for s in sources:
        if isinstance(s, dict) and str(s.get('matter') or '') in caches:
            return False
    return True


def _portee(cab, compte):
    """Fils de la personne : propriétaire, ou participant nominatif ; cabinet ; non supprimé. Le texte SQL vient du code, jamais de la requête."""
    return (''' FROM assistant_threads t
      JOIN assistant_thread_owners109 o ON o.thread_id=t.id
      LEFT JOIN assistant_thread_preferences109 p ON p.thread_id=t.id AND p.cabinet_id=o.cabinet_id AND p.user_id=?
      LEFT JOIN assistant_thread_members109 mb ON mb.thread_id=t.id AND mb.cabinet_id=o.cabinet_id AND mb.user_id=? AND mb.role IN ('viewer','contributor')
      WHERE o.cabinet_id=? AND o.deleted_at IS NULL AND (o.owner_id=? OR mb.user_id IS NOT NULL)''', [compte, compte, cab, compte])


def charger_fil(desk, qui, thread_id, avec_supprime=False):
    """Fil lisible par la personne (dict avec ``role_acces``), sinon ``Stop('introuvable')`` — même réponse pour absent, interdit, supprimé,
    non attribué ou autre cabinet. Aucune écriture."""
    cab, compte, role = acteur(desk, qui)
    thread_id = str(thread_id or '')
    if not ID_FIL.match(thread_id) or not schema_present(desk):
        raise Stop('introuvable')
    sql, params = _portee(cab, compte)
    if avec_supprime:
        sql = sql.replace('o.deleted_at IS NULL AND ', '')
    row = desk.db.execute('SELECT t.*, o.owner_id AS owner_id, o.revision AS revision, o.deleted_at AS deleted_at, o.archived AS owner_archived, '
                          'mb.role AS member_role, p.pinned AS pinned, p.archived AS pref_archived, p.hidden AS hidden, p.group_id AS group_id' +
                          sql + ' AND t.id=?', (*params, thread_id)).fetchone()
    if row is None:
        raise Stop('introuvable')
    fil = dict(row)
    if not _fil_lisible(desk, (compte, role), fil):
        raise Stop('introuvable')
    fil['role_acces'] = 'proprietaire' if fil['owner_id'] == compte else fil['member_role']
    return fil


def lisible(desk, qui, thread_id):
    try:
        charger_fil(desk, qui, thread_id)
        return True
    except Stop:
        return False


# --------- raccordements des lectures historiques (``integration.threads`` / ``thread_messages``, ``workspace_ui.assistant_panel``)
def exiger_lecture_requete(desk, thread_id):
    """Lecture d'un fil par la requête en cours : hors requête (travaux du cabinet) rien ne change ; en requête, ``conversation_absente``
    (même réponse que l'absence) si la personne ne peut pas lire ce fil."""
    qui = acteur_requete_ou_none()
    if qui is None:
        return
    if not lisible(desk, qui, thread_id):
        raise Stop('conversation_absente')


def fils_recents(desk, limite=30):
    """Liste courte des fils (anciennes vues) : hors requête, la liste brute ; en requête, seulement les fils de la personne, droits appliqués
    avant LIMIT. Aucune écriture."""
    limite = max(1, min(int(limite or 30), LIMITE_MAX))
    qui = acteur_requete_ou_none()
    if qui is None:
        return [dict(x) for x in desk.db.execute('SELECT * FROM assistant_threads ORDER BY updated DESC LIMIT ?', (limite,)).fetchall()]
    if not schema_present(desk):
        return []
    out = fils(desk, qui, limite=limite)
    return [{k: x[k] for k in ('id', 'title', 'matter', 'mail_key', 'updated')} for x in out['epinglees'] + out['items']][:limite]


# ------------------------------------------------------------------------------------------------ états réels
def _etat_message(m, job):
    statut = str(m.get('status') or '')
    js = str((job or {}).get('status') or '')
    if statut == 'done':
        return 'terminee'
    if statut == 'error' or js == 'error':
        return 'echec'
    if statut == 'cancelled' or js in ('cancelled', 'cancel_requested'):
        return 'annulee'
    if statut == 'running' or js == 'running':
        return 'en_cours'
    if js == 'suspended':
        return 'en_pause'
    if js == 'pending':
        return 'en_attente'
    return 'envoyee'


def _jobs(desk, ids):
    ids = sorted({int(i) for i in ids if isinstance(i, int) or (isinstance(i, str) and i.isdigit())})
    if not ids:
        return {}
    out = {}
    for i in range(0, len(ids), 400):
        part = ids[i:i + 400]
        for r in desk.db.execute('SELECT id, status FROM jobs WHERE id IN (%s)' % ','.join('?' * len(part)), part):
            out[r['id']] = {'status': r['status']}
    return out


def _a_preciser(desk, thread_ids):
    """Fils dont la mission attend une décision de l'avocat (``missions_v567``, état « decision ») ; table absente : aucun."""
    if not thread_ids:
        return set()
    try:
        refs = ['ask:' + t for t in thread_ids]
        rows = desk.db.execute("SELECT ref FROM missions_v567 WHERE state='decision' AND ref IN (%s)" % ','.join('?' * len(refs)), refs).fetchall()
    except Exception:   # noqa: BLE001
        return set()
    return {str(r[0])[4:] for r in rows}


def _derniers(desk, thread_ids):
    """Dernier message de l'auteur et dernier message de chaque fil (état et extrait), en une requête bornée."""
    if not thread_ids:
        return {}, {}
    marks = ','.join('?' * len(thread_ids))
    users = {r['thread_id']: dict(r) for r in desk.db.execute(
        "SELECT m.* FROM assistant_messages m JOIN (SELECT thread_id, MAX(id) AS mid FROM assistant_messages WHERE role='user' AND thread_id IN (%s) "
        "GROUP BY thread_id) x ON x.mid=m.id" % marks, thread_ids)}
    derniers = {r['thread_id']: dict(r) for r in desk.db.execute(
        "SELECT m.* FROM assistant_messages m JOIN (SELECT thread_id, MAX(id) AS mid FROM assistant_messages WHERE thread_id IN (%s) "
        "GROUP BY thread_id) x ON x.mid=m.id" % marks, thread_ids)}
    return users, derniers


def _extrait(texte, n=160):
    return re.sub(r'\s+', ' ', str(texte or '')).strip()[:n]


# ------------------------------------------------------------------------------------------------ curseurs opaques
def _empreinte(*valeurs):
    return hashlib.sha256(json.dumps(valeurs, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def signer_curseur(compte, filtres, cle):
    corps = base64.urlsafe_b64encode(json.dumps({'k': cle, 'a': _empreinte(compte), 'f': _empreinte(filtres), 'x': int(time.time()) + CURSEUR_TTL},
                                                separators=(',', ':')).encode()).decode().rstrip('=')
    mac = hmac.new(_CLE, corps.encode(), hashlib.sha256).hexdigest()[:32]
    return corps + '.' + mac


def lire_curseur(jeton, compte, filtres):
    try:
        corps, mac = str(jeton).split('.', 1)
        if not hmac.compare_digest(mac, hmac.new(_CLE, corps.encode(), hashlib.sha256).hexdigest()[:32]):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(corps + '=' * (-len(corps) % 4)))
        if data['a'] != _empreinte(compte) or data['f'] != _empreinte(filtres) or int(data['x']) < time.time():
            raise ValueError
        cle = data['k']
        if not (isinstance(cle, list) and len(cle) == 2 and all(isinstance(x, str) for x in cle)):
            raise ValueError
        return tuple(cle)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        raise Stop('curseur_invalide') from None


# ------------------------------------------------------------------------------------------------ liste des fils
def _filtres(brut):
    """Filtres fermés ; toute valeur hors liste est refusée (jamais ignorée en silence)."""
    brut = dict(brut or {})
    f = {'q': str(brut.get('q') or '').strip(), 'dossier': str(brut.get('dossier') or '').strip()[:80], 'periode': str(brut.get('periode') or ''),
         'en_cours': str(brut.get('en_cours') or '') in ('1', 'true', 'oui'), 'archives': str(brut.get('archives') or '') in ('1', 'true', 'oui'),
         'groupe': str(brut.get('groupe') or '').strip()[:40]}
    if len(f['q']) > RECHERCHE_MAX:
        raise Stop('recherche_trop_longue')
    if f['periode'] and f['periode'] not in PERIODES:
        raise Stop('parametre_invalide')
    if f['groupe'] and f['groupe'] != 'aucun' and not re.fullmatch(r'[a-f0-9]{16,32}', f['groupe']):
        raise Stop('parametre_invalide')
    return f


def _like(q):
    return '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def fils(desk, qui, filtres=None, apres=None, limite=30):
    """Page de fils autorisés : ``{'epinglees', 'items', 'next', 'portee_recherche'}``. Épinglés à part (première page, hors archives).

    Droits du dossier appliqués dans la requête (avant ORDER BY / LIMIT), courriel et cabinet ensuite ligne par ligne ; le curseur avance
    sur les lignes examinées (une page courte ne signifie pas la fin). Aucun total global n'est divulgué."""
    from . import acces5697
    cab, compte, role = acteur(desk, qui)
    if type(limite) is not int or not 1 <= limite <= LIMITE_MAX:
        raise Stop('parametre_invalide')
    f = _filtres(filtres)
    if not schema_present(desk):
        return {'epinglees': [], 'items': [], 'next': None, 'portee_recherche': 'titres'}
    try:
        perimetre = acces5697.dossiers_autorises(desk, (compte, role))
    except Stop as ex:
        if str(ex) == 'controle_acces_indisponible':
            raise
        raise Stop('introuvable') from None
    base, params = _portee(cab, compte)
    frag, p2 = perimetre.sql('t.matter')
    base += ' AND ' + frag
    params += list(p2)
    base += " AND COALESCE(p.hidden,0)=0 AND COALESCE(p.archived, CASE WHEN o.owner_id=? THEN o.archived ELSE 0 END)=?"
    params += [compte, int(f['archives'])]
    if f['q']:
        base += " AND t.title LIKE ? ESCAPE '\\'"
        params.append(_like(f['q']))
    if f['dossier']:
        base += ' AND t.matter=?'
        params.append(f['dossier'])
    if f['periode']:
        base += ' AND t.updated>=?'
        params.append((datetime.now(timezone.utc) - timedelta(days=PERIODES[f['periode']])).isoformat())
    if f['groupe'] == 'aucun':
        base += ' AND p.group_id IS NULL'
    elif f['groupe']:
        if not desk.db.execute('SELECT 1 FROM assistant_groups109 WHERE id=? AND cabinet_id=? AND owner_id=?', (f['groupe'], cab, compte)).fetchone():
            raise Stop('introuvable')            # groupe d'une autre personne : même réponse qu'un groupe absent
        base += ' AND p.group_id=?'
        params.append(f['groupe'])
    if f['en_cours']:
        base += " AND EXISTS(SELECT 1 FROM assistant_messages am WHERE am.thread_id=t.id AND am.role='user' AND am.status IN ('queued','running'))"
    colonnes = ('SELECT t.*, o.owner_id AS owner_id, o.revision AS revision, mb.role AS member_role, COALESCE(p.pinned,0) AS pinned, '
                'p.group_id AS group_id')
    epinglees = []
    if apres is None and not f['archives']:
        epinglees = [dict(r) for r in desk.db.execute(colonnes + base + ' AND COALESCE(p.pinned,0)=1 ORDER BY t.updated DESC, t.id DESC LIMIT 20',
                                                       params).fetchall()]
    sql, p3 = base + ' AND COALESCE(p.pinned,0)=0', list(params)
    if f['archives']:
        sql, p3 = base, list(params)
    if apres is not None:
        u, i = lire_curseur(apres, compte, f)
        sql += ' AND (t.updated<? OR (t.updated=? AND t.id<?))'
        p3 += [u, u, i]
    rows = [dict(r) for r in desk.db.execute(colonnes + sql + ' ORDER BY t.updated DESC, t.id DESC LIMIT ?', (*p3, limite + 1)).fetchall()]
    examinees = rows[:limite]
    suivant = signer_curseur(compte, f, [str(examinees[-1]['updated'] or ''), examinees[-1]['id']]) if len(rows) > limite else None
    a = (compte, role)
    visibles_ep = [x for x in epinglees if _fil_lisible(desk, a, x)]
    visibles = [x for x in examinees if _fil_lisible(desk, a, x)]
    return {'epinglees': _decorer(desk, a, compte, visibles_ep), 'items': _decorer(desk, a, compte, visibles), 'next': suivant,
            'portee_recherche': 'titres', 'reexecution': False}


def _decorer(desk, a, compte, rows):
    """Champs affichés : titre, date, dossier ou « Sans dossier », dernier état utile, extrait autorisé, rôle d'accès, épingle, groupe."""
    if not rows:
        return []
    from . import acces5697
    from .common import load_matters, matter_display
    try:
        noms = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    except Exception:   # noqa: BLE001 — registre des dossiers illisible : l'identifiant seul
        noms = {}
    ids = [r['id'] for r in rows]
    users, derniers = _derniers(desk, ids)
    jobs = _jobs(desk, [u.get('job_id') for u in users.values()])
    preciser = _a_preciser(desk, ids)
    try:
        caches = acces5697.masques(desk, a)
    except Stop:
        caches = None
    out = []
    for r in rows:
        u = users.get(r['id'])
        etat = 'a_preciser' if r['id'] in preciser else (_etat_message(u, jobs.get(u.get('job_id'))) if u else 'envoyee')
        dernier = derniers.get(r['id'])
        extrait = ''
        if dernier and caches is not None and _message_lisible(desk, a, dernier, caches):
            extrait = _extrait(dernier.get('content'))
        mid = str(r.get('matter') or '')
        out.append({'id': r['id'], 'title': r.get('title') or 'Discussion', 'matter': mid, 'dossier': (noms.get(mid) or mid) if mid else 'Sans dossier',
                    'mail_key': r.get('mail_key') or '', 'updated': r.get('updated') or '', 'etat': etat, 'etat_libelle': ETATS[etat], 'extrait': extrait,
                    'role_acces': 'proprietaire' if r.get('owner_id') == compte else (r.get('member_role') or ''),
                    'pinned': bool(r.get('pinned')), 'group_id': r.get('group_id') or '', 'revision': int(r.get('revision') or 1)})
    return out


# ------------------------------------------------------------------------------------------------ messages d'un fil
def messages(desk, qui, thread_id, apres=0, limite=50):
    """Page de messages (``id > apres``, ordre croissant) : toute l'archive se parcourt, au-delà de 100 messages. Fil relu à chaque page ;
    chaque message est recontrôlé (sources citées). ``reexecution`` est toujours faux : lire ne relance rien."""
    if type(limite) is not int or not 1 <= limite <= LIMITE_MAX or type(apres) is not int or apres < 0:
        raise Stop('parametre_invalide')
    fil = charger_fil(desk, qui, thread_id)
    _, compte, role = acteur(desk, qui)
    from . import acces5697
    try:
        caches = acces5697.masques(desk, (compte, role))
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    rows = [dict(r) for r in desk.db.execute('SELECT * FROM assistant_messages WHERE thread_id=? AND id>? ORDER BY id LIMIT ?',
                                             (thread_id, apres, limite + 1)).fetchall()]
    examines = rows[:limite]
    jobs = _jobs(desk, [m.get('job_id') for m in examines])
    items = []
    for m in examines:
        ok = _message_lisible(desk, (compte, role), m, caches)
        sources = [s for s in (_sources(m) or []) if isinstance(s, dict)] if ok else []
        etat = _etat_message(m, jobs.get(m.get('job_id'))) if m['role'] == 'user' else ('terminee' if m.get('status') == 'done' else _etat_message(m, None))
        items.append({'id': m['id'], 'role': m['role'], 'contenu': str(m.get('content') or '') if ok else MASQUE, 'masque': not ok,
                      'etat': etat, 'etat_libelle': ETATS[etat], 'etat_source': str(m.get('status') or ''),
                      'etat_travail': str((jobs.get(m.get('job_id')) or {}).get('status') or ''), 'job_id': m.get('job_id'),
                      'sources': [{'id': str(s.get('id') or '')[:120], 'titre': str(s.get('path') or s.get('title') or s.get('titre') or s.get('id') or '')[:300],
                                   'matter': str(s.get('matter') or '')[:80]} for s in sources if s.get('kind') != 'situation_societe'][:40],
                      'created': m.get('created') or ''})
    vue = {k: fil.get(k) for k in ('id', 'title', 'matter', 'mail_key', 'created', 'updated', 'role_acces', 'revision')}
    vue['peut_continuer'] = fil['role_acces'] in ('proprietaire', 'contributor')
    vue['proprietaire'] = fil['role_acces'] == 'proprietaire'
    return {'thread': vue, 'items': items, 'next': examines[-1]['id'] if len(rows) > limite else None, 'reexecution': False}


# ------------------------------------------------------------------------------------------------ recherche (titres et messages autorisés)
def recherche(desk, qui, q, limite=30):
    """Recherche locale dans les titres ET les messages des seuls fils autorisés (portée et dossiers dans la requête, avant LIMIT) ; extraits
    tirés des seuls messages lisibles ; total = total autorisé de la page. Aucun appel au modèle ni à un service externe."""
    from . import acces5697
    cab, compte, role = acteur(desk, qui)
    q = str(q or '').strip()
    if not q:
        return {'items': [], 'total': 0, 'portee': 'titres_et_messages'}
    if len(q) > RECHERCHE_MAX:
        raise Stop('recherche_trop_longue')
    if type(limite) is not int or not 1 <= limite <= LIMITE_MAX:
        raise Stop('parametre_invalide')
    if not schema_present(desk):
        return {'items': [], 'total': 0, 'portee': 'titres_et_messages'}
    try:
        perimetre = acces5697.dossiers_autorises(desk, (compte, role))
        caches = perimetre.masques
    except Stop:
        raise Stop('controle_acces_indisponible') from None
    base, params = _portee(cab, compte)
    frag, p2 = perimetre.sql('t.matter')
    motif = _like(q)
    sql = ('SELECT t.*, o.owner_id AS owner_id, o.revision AS revision, mb.role AS member_role, COALESCE(p.pinned,0) AS pinned, p.group_id AS group_id' +
           base + ' AND ' + frag + " AND COALESCE(p.hidden,0)=0 AND (t.title LIKE ? ESCAPE '\\' OR EXISTS(SELECT 1 FROM assistant_messages sm "
           "WHERE sm.thread_id=t.id AND sm.content LIKE ? ESCAPE '\\')) ORDER BY t.updated DESC, t.id DESC LIMIT ?")
    rows = [dict(r) for r in desk.db.execute(sql, (*params, *p2, motif, motif, limite)).fetchall()]
    a = (compte, role)
    rows = [r for r in rows if _fil_lisible(desk, a, r)]
    items = _decorer(desk, a, compte, rows)
    for it in items:
        trouve = ''
        for m in desk.db.execute("SELECT * FROM assistant_messages WHERE thread_id=? AND content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 5",
                                 (it['id'], motif)).fetchall():
            if _message_lisible(desk, a, dict(m), caches):
                trouve = _extrait(m['content'], 200)
                break
        if trouve:
            it['extrait'] = trouve
        elif q.lower() not in str(it['title']).lower():
            it['_exclu'] = True                     # trouvé seulement dans un message masqué : ni titre, ni compteur
    items = [it for it in items if not it.pop('_exclu', False)]
    return {'items': items, 'total': len(items), 'portee': 'titres_et_messages'}


# ------------------------------------------------------------------------------------------------ export d'un fil (Markdown, JSON)
def exporter(desk, qui, thread_id, format_='md'):
    """Export d'un fil lisible (droit individuel « exporter » relu) ; contrôle du périmètre à la génération : un accès révoqué ne s'exporte
    plus. Pièces jamais incluses ; le manifeste dit ce qui n'est pas transporté. Ni jeton, ni secret, ni chemin du poste."""
    from . import membres5650
    cab, compte, role = acteur(desk, qui)
    if format_ not in ('md', 'json'):
        raise Stop('parametre_invalide')
    fil = charger_fil(desk, qui, thread_id)
    droits = membres5650.effective_rights(desk.c['state_dir'], compte, role)
    if not droits.get('exporter'):
        raise Stop('export_interdit')
    tous, apres = [], 0
    while True:
        page = messages(desk, qui, thread_id, apres=apres, limite=LIMITE_MAX)
        tous += page['items']
        if page['next'] is None:
            break
        apres = page['next']
    paris = ZoneInfo('Europe/Paris')

    def local(iso):
        try:
            return datetime.fromisoformat(str(iso)).astimezone(paris).strftime('%d/%m/%Y %H:%M')
        except (TypeError, ValueError):
            return ''
    manifeste = {'pieces_jointes': 'non exportées (export séparé, sur sélection)', 'messages_masques': sum(1 for m in tous if m['masque']),
                 'sources': 'références seulement (identifiant, titre, dossier)'}
    donnees = {'schema': 'historique_ia/1', 'exporte_le_utc': maintenant(), 'zone_affichage': 'Europe/Paris',
               'discussion': {k: fil.get(k) for k in ('id', 'title', 'matter', 'created', 'updated')}, 'role_acces': fil['role_acces'],
               'messages': [{k: m[k] for k in ('id', 'role', 'contenu', 'etat', 'etat_source', 'job_id', 'sources', 'created')} for m in tous],
               'manifeste': manifeste}
    try:
        desk.audit('historique56109_export', {'fil': _empreinte(thread_id), 'compte': _empreinte(compte), 'format': format_})
    except Exception:   # noqa: BLE001 — le journal ne bloque pas un export déjà autorisé
        pass
    if format_ == 'json':
        return json.dumps(donnees, ensure_ascii=False, indent=1), 'application/json; charset=utf-8', 'discussion-%s.json' % thread_id[:8]
    lignes = ['# ' + str(fil.get('title') or 'Discussion'), '', 'Dossier : %s' % (fil.get('matter') or 'Sans dossier'),
              'Exporté le %s (heure de Paris)' % local(donnees['exporte_le_utc']), '']
    for m in tous:
        lignes += ['## %s — %s (%s)' % ('Vous' if m['role'] == 'user' else 'Assistant — à relire', local(m['created']), ETATS[m['etat']]), '', m['contenu'], '']
        if m['sources']:
            lignes += ['Sources : ' + '; '.join(s['titre'] for s in m['sources']), '']
    lignes += ['---', 'Pièces jointes : non exportées. Messages masqués : %d.' % manifeste['messages_masques']]
    return '\n'.join(lignes), 'text/markdown; charset=utf-8', 'discussion-%s.md' % thread_id[:8]


# ------------------------------------------------------------------------------------------------ fils à attribuer (administrateur)
def a_attribuer(desk, qui, limite=100):
    """Fils antérieurs sans propriétaire : existence seulement (date, dossier, nombre de messages), jamais titre ni contenu. Administrateur."""
    cab, compte, role = acteur(desk, qui)
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    if not schema_present(desk):
        return {'items': [], 'total': 0}
    total = desk.db.execute('SELECT COUNT(*) FROM assistant_threads t WHERE NOT EXISTS(SELECT 1 FROM assistant_thread_owners109 o WHERE o.thread_id=t.id)').fetchone()[0]
    rows = desk.db.execute('SELECT t.id, t.matter, t.created, t.updated, (SELECT COUNT(*) FROM assistant_messages m WHERE m.thread_id=t.id) AS n '
                           'FROM assistant_threads t WHERE NOT EXISTS(SELECT 1 FROM assistant_thread_owners109 o WHERE o.thread_id=t.id) '
                           'ORDER BY t.updated DESC LIMIT ?', (max(1, min(int(limite), 500)),)).fetchall()
    return {'items': [{'id': r['id'], 'matter': r['matter'] or '', 'created': r['created'] or '', 'updated': r['updated'] or '', 'messages': r['n']} for r in rows],
            'total': total}


def attribuer_anciens(desk, qui, ids, owner, preuve, cle):
    """Attribution administrative d'anciens fils à un compte actif du cabinet, avec preuve écrite et trace ; jamais déduite d'un courriel ou
    d'un nom de poste. Un fil déjà attribué n'est pas modifié."""
    from . import membres5650
    from .discussions56109 import executer
    cab, compte, role = acteur(desk, qui)
    if role != 'administrateur':
        raise Stop('role_insuffisant')
    preuve = re.sub(r'\s+', ' ', str(preuve or '')).strip()
    if len(preuve) < 10 or len(preuve) > 500:
        raise Stop('preuve_requise')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 200 or not all(isinstance(x, str) and ID_FIL.match(x) for x in ids):
        raise Stop('parametre_invalide')
    owner = str(owner or '').strip()
    if owner not in membres5650.directory(desk):
        raise Stop('compte_inconnu')

    def faire():
        n = 0
        for tid in dict.fromkeys(ids):
            if not desk.db.execute('SELECT 1 FROM assistant_threads WHERE id=?', (tid,)).fetchone():
                continue
            cur = desk.db.execute('INSERT OR IGNORE INTO assistant_thread_owners109(thread_id,cabinet_id,owner_id,revision,attribue_le,attribue_par,preuve) '
                                  'VALUES(?,?,?,?,?,?,?)', (tid, cab, owner, 1, maintenant(), compte, preuve))
            n += cur.rowcount
        desk.audit('historique56109_attribution', {'par': compte, 'compte': _empreinte(owner), 'fils': n, 'preuve': preuve[:200]})
        return {'attribues': n}
    return executer(desk, compte, cle, 'attribuer', {'ids': sorted(set(ids)), 'owner': owner, 'preuve': preuve}, faire)


# ------------------------------------------------------------------------------------------------ brouillon du compositeur (côté serveur)
def brouillon_lire(desk, qui, contexte):
    cab, compte, _ = acteur(desk, qui)
    contexte = str(contexte or '')
    if not CONTEXTE_BROUILLON.match(contexte):
        raise Stop('parametre_invalide')
    if not schema_present(desk):
        return {'texte': '', 'revision': 0}
    r = desk.db.execute('SELECT texte, revision FROM assistant_drafts109 WHERE cabinet_id=? AND user_id=? AND contexte=?', (cab, compte, contexte)).fetchone()
    return {'texte': r['texte'] if r else '', 'revision': r['revision'] if r else 0}


def brouillon_enregistrer(desk, qui, contexte, texte):
    """Brouillon privé de la personne (un par contexte : page ou fil) ; texte vide : effacé. Remplacement idempotent (même texte, même état)."""
    cab, compte, _ = acteur(desk, qui)
    contexte = str(contexte or '')
    if not CONTEXTE_BROUILLON.match(contexte) or not isinstance(texte, str):
        raise Stop('parametre_invalide')
    if len(texte) > BROUILLON_MAX:
        raise Stop('brouillon_trop_long')
    if not texte.strip():
        desk.db.execute('DELETE FROM assistant_drafts109 WHERE cabinet_id=? AND user_id=? AND contexte=?', (cab, compte, contexte))
    else:
        desk.db.execute('INSERT INTO assistant_drafts109(cabinet_id,user_id,contexte,texte,revision,maj_le) VALUES(?,?,?,?,1,?) '
                        'ON CONFLICT(cabinet_id,user_id,contexte) DO UPDATE SET texte=excluded.texte, revision=revision+1, maj_le=excluded.maj_le '
                        'WHERE texte<>excluded.texte', (cab, compte, contexte, texte, maintenant()))
    desk.db.commit()
    return brouillon_lire(desk, qui, contexte)


# ------------------------------------------------------------------------------------------------ routes de lecture (et brouillon, attribution)
def allowed(role, method, api):
    """``standalone_auth.allowed`` (POST) : brouillon (tout rôle), attribution (administrateur)."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    if method != 'POST':
        return False
    return reste == 'brouillon' or (reste == 'attribuer' and role == 'administrateur')


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def erreur(code):
    if code in ('dossier_reserve', 'dossier_absent', 'acteur_requis', 'conversation_absente'):
        code = 'introuvable'
    return _json({'error': code, 'message': MESSAGES.get(code) or _message_discussions(code)}, STATUTS.get(code) or _statut_discussions(code))


def _message_discussions(code):
    from .discussions56109 import MESSAGES as M
    return M.get(code, 'Demande refusée.')


def _statut_discussions(code):
    from .discussions56109 import STATUTS as S
    return S.get(code, '422 Unprocessable Entity')


def _entier(v, defaut, maxi=None):
    try:
        n = int(v) if v not in (None, '') else defaut
    except (TypeError, ValueError):
        raise Stop('parametre_invalide') from None
    if n < 0 or (maxi is not None and n > maxi):
        raise Stop('parametre_invalide')
    return n


def api(env, desk, auth, prefix, name, args, method):
    """GET : fils, fil, messages, recherche, exporter, a-attribuer, brouillon, membres, groupes, corbeille, politique (aucune écriture) ;
    POST : brouillon, attribuer (CSRF, corps borné, clé d'idempotence pour l'attribution)."""
    from .web567 import actor
    qui = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if not allowed(qui[1], method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            if reste == 'fils':
                curseur = str(args.get('apres') or '') or None
                return _json(fils(desk, qui, args, apres=curseur, limite=_entier(args.get('limite'), 30, LIMITE_MAX) or 30))
            if reste in ('fil', 'messages'):
                return _json(messages(desk, qui, str(args.get('id') or ''), _entier(args.get('apres'), 0), _entier(args.get('limite'), 50, LIMITE_MAX) or 50))
            if reste == 'recherche':
                return _json(recherche(desk, qui, args.get('q'), _entier(args.get('limite'), 30, LIMITE_MAX) or 30))
            if reste == 'exporter':
                corps, kind, nom = exporter(desk, qui, str(args.get('id') or ''), str(args.get('format') or 'md'))
                return {'status': '200 OK', 'kind': kind, 'body': corps,
                        'headers': [('Cache-Control', 'private, no-store'), ('Content-Disposition', 'attachment; filename="%s"' % nom)]}
            if reste == 'a-attribuer':
                return _json(a_attribuer(desk, qui))
            if reste == 'brouillon':
                return _json(brouillon_lire(desk, qui, args.get('contexte')))
            from . import discussions56109 as D
            if reste == 'membres':
                return _json(D.membres(desk, qui, str(args.get('id') or ''), auth))
            if reste == 'groupes':
                return _json({'groupes': D.groupes(desk, qui)})
            if reste == 'corbeille':
                return _json(D.corbeille(desk, qui))
            return _json(D.politique_publique(desk))
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = 1 << 30
        if longueur > 40_000:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'brouillon':
            return _json(brouillon_enregistrer(desk, qui, d.get('contexte'), d.get('texte')))
        return _json(attribuer_anciens(desk, qui, d.get('ids'), d.get('compte'), d.get('preuve'), d.get('cle')))
    except Stop as ex:
        return erreur(str(ex).split(':')[0])


# ------------------------------------------------------------------------------------------------ vue agrandie : /assistant (nouvelle interface)
def _date_locale(iso):
    try:
        return datetime.fromisoformat(str(iso)).astimezone(ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M')
    except (TypeError, ValueError):
        return ''


def _ligne_html(prefix, x, choisi):
    from html import escape as e
    from urllib.parse import urlencode
    href = prefix + '/assistant?' + urlencode({'vue': 'historique', 'thread': x['id']})
    role = '' if x['role_acces'] == 'proprietaire' else ' · ' + ROLES_LIBELLES.get(x['role_acces'], '')
    return ('<li class="h109-ligne%s" data-h109-fil="%s" data-h109-revision="%d" data-h109-role="%s" data-h109-epingle="%s" data-h109-groupe="%s">'
            '<a class="h109-lien" href="%s"%s><span class="h109-titre">%s</span><span class="h109-meta">%s · %s · <span class="h109-etat" data-etat="%s">%s</span>%s</span>'
            '<span class="h109-extrait">%s</span></a></li>') % (
        ' h109-choisi' if choisi else '', e(x['id'], quote=True), x['revision'], e(x['role_acces'], quote=True), '1' if x['pinned'] else '0',
        e(x['group_id'], quote=True), e(href, quote=True), ' aria-current="true"' if choisi else '', e(x['title']), e(_date_locale(x['updated'])),
        e(x['dossier']), e(x['etat'], quote=True), e(x['etat_libelle']), e(role), e(x['extrait']))


def page_html(desk, qui, prefix, args, form, matters=()):
    """Historique des discussions dans la vue agrandie de l'assistant : liste (recherche, filtres Dossier, Période, En cours, Archives,
    groupe), fil choisi (demande exacte, réponse, sources, reçu de traitement, état réel), « Continuer » par le circuit existant
    (``assistant_ask``) si la personne peut continuer. Lecture seule : rien n'est mis en file, synchronisé ni purgé par l'affichage."""
    from html import escape as e
    from urllib.parse import urlencode
    from .common import matter_display
    cab, compte, role = acteur(desk, qui)
    choisi = str(args.get('thread') or '')
    try:
        liste = fils(desk, qui, args, apres=None, limite=30)
        erreur_liste = ''
    except Stop as ex:
        liste, erreur_liste = {'epinglees': [], 'items': [], 'next': None}, MESSAGES.get(str(ex), 'Liste indisponible.')
    try:
        from .discussions56109 import groupes as _groupes
        mes_groupes = _groupes(desk, qui)
    except Stop:
        mes_groupes = []
    options_dossiers = '<option value="">Tous les dossiers</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(str(m['id']), quote=True), ' selected' if str(m['id']) == str(args.get('dossier') or '') else '', e(matter_display(m)))
        for m in matters)
    periodes = '<option value="">Toute période</option>' + ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if args.get('periode') == k else '', l)
                                                                     for k, l in (('7j', '7 derniers jours'), ('30j', '30 derniers jours'), ('90j', '90 derniers jours'), ('365j', '12 derniers mois')))
    options_groupes = '<option value="">Tous les groupes</option><option value="aucun"%s>Sans groupe</option>' % (' selected' if args.get('groupe') == 'aucun' else '') + ''.join(
        '<option value="%s"%s>%s</option>' % (e(g['id'], quote=True), ' selected' if args.get('groupe') == g['id'] else '', e(g['name'])) for g in mes_groupes)

    def coche(k):
        return ' checked' if str(args.get(k) or '') in ('1', 'oui', 'true') else ''
    filtres = ('<form class="h109-filtres" method="get" action="%s" role="search" data-h109-filtres><input type="hidden" name="vue" value="historique">'
               '<label>Rechercher<input type="search" name="q" maxlength="200" value="%s" placeholder="Titre de la discussion" data-filter></label>'
               '<label>Dossier<select name="dossier" data-filter>%s</select></label><label>Période<select name="periode" data-filter>%s</select></label>'
               '<label>Groupe<select name="groupe" data-filter>%s</select></label>'
               '<label class="h109-case"><input type="checkbox" name="en_cours" value="1"%s> Mes demandes en cours</label>'
               '<label class="h109-case"><input type="checkbox" name="archives" value="1"%s> Archives</label>'
               '<button type="submit" class="ax-btn ghost">Filtrer</button></form>'
               '<p class="h109-portee">La recherche porte sur les titres ; « Rechercher dans les messages » parcourt aussi le texte de vos discussions.</p>') % (
        e(prefix + '/assistant', quote=True), e(str(args.get('q') or ''), quote=True), options_dossiers, periodes, options_groupes, coche('en_cours'), coche('archives'))
    lignes_ep = ''.join(_ligne_html(prefix, x, x['id'] == choisi) for x in liste['epinglees'])
    lignes = ''.join(_ligne_html(prefix, x, x['id'] == choisi) for x in liste['items'])
    suite = ('<a class="ax-btn ghost h109-suite" data-h109-suite href="%s">Discussions suivantes</a>' % e(prefix + '/assistant?' + urlencode(
        {**{k: v for k, v in args.items() if k in ('q', 'dossier', 'periode', 'groupe', 'en_cours', 'archives')}, 'vue': 'historique', 'apres': liste['next']}), quote=True)
             if liste['next'] else '')
    vide = '' if (lignes or lignes_ep) else '<p class="h109-vide">Aucune discussion pour ces critères.</p>'
    colonne = ('<section class="h109-liste" aria-labelledby="h109-liste-titre"><div class="h109-entete"><h2 id="h109-liste-titre">Historique</h2>'
               '<a class="ax-btn" href="%s" data-h109-nouvelle>Nouvelle discussion</a></div>%s%s%s'
               '<ul class="h109-fils" data-h109-liste aria-label="Discussions">%s</ul>%s%s'
               '<p class="h109-liens"><a href="%s">Corbeille</a> · <button type="button" class="h109-bouton-lien" data-h109-recherche-messages>Rechercher dans les messages</button></p>'
               '<div data-h109-resultats role="region" aria-live="polite" aria-label="Résultats dans les messages"></div></section>') % (
        e(prefix + '/assistant?vue=historique&nouvelle=1', quote=True), filtres,
        ('<p class="h109-alerte" role="alert">%s</p>' % e(erreur_liste)) if erreur_liste else '',
        ('<h3 class="h109-sous-titre">Épinglées</h3><ul class="h109-fils" aria-label="Discussions épinglées">%s</ul><h3 class="h109-sous-titre">Récentes</h3>' % lignes_ep) if lignes_ep else '',
        lignes, vide, suite, e(prefix + '/assistant?vue=corbeille', quote=True))
    detail = _detail_html(desk, qui, prefix, choisi, form, args) if choisi else _nouvelle_html(prefix, form, args)
    if role == 'administrateur':
        try:
            n = a_attribuer(desk, qui, 1)['total']
        except Stop:
            n = 0
        if n:
            colonne += ('<p class="h109-attribuer" role="note">%d discussion(s) antérieure(s) sans auteur établi : elles restent invisibles dans l’historique '
                        'de chacun tant qu’une attribution prouvée n’est pas faite. <a href="%s">Attribuer</a></p>') % (n, e(prefix + '/assistant?vue=attribuer', quote=True))
    # v56109-historique.js est chargé par le menu latéral sur toutes les pages (``shell501``) ; ici, seulement les actions des discussions
    return ('<div class="h109" data-h109 data-h109-compte="%s">%s%s</div><script defer src="%s"></script>') % (
        e(_empreinte(compte), quote=True), colonne, detail, e(prefix + '/static/v56109-discussions.js', quote=True))


def _nouvelle_html(prefix, form, args):
    champs = {'matter': str(args.get('matter') or ''), 'key': '', 'thread': '', 'attachment_id': ''}
    return ('<section class="h109-detail" aria-labelledby="h109-detail-titre"><h2 id="h109-detail-titre">Nouvelle discussion</h2>'
            '<p><strong>Recherche générale dans le cabinet</strong> tant qu’aucun dossier n’est choisi. Le premier message crée la discussion ; '
            'aucun dossier n’est imposé et le dossier choisi est contrôlé par le serveur.</p>%s</section>') % form(
        'assistant_ask', 'Envoyer à l’assistant', champs,   # libellé cité par la fiche « assistant » du guide d'utilisation (aide5631)
        '<label for="h109-question">Instruction</label><textarea id="h109-question" data-h109-brouillon="nouvelle" required name="question" rows="4" maxlength="12000"></textarea>')


def _detail_html(desk, qui, prefix, thread_id, form, args):
    from html import escape as e
    from urllib.parse import urlencode
    try:
        page = messages(desk, qui, thread_id, apres=0, limite=50)
    except Stop as ex:
        code = 'controle_acces_indisponible' if str(ex) == 'controle_acces_indisponible' else 'introuvable'
        return '<section class="h109-detail"><p class="h109-alerte" role="alert">%s</p></section>' % e(MESSAGES[code])
    t = page['thread']
    corps = ''
    for m in page['items']:
        qui_l = 'Vous' if m['role'] == 'user' else 'Assistant — à relire'
        sources = ''.join('<li>%s</li>' % e(s['titre']) for s in m['sources'])
        recu = (' · reçu : message %d%s' % (m['id'], (', travail %s' % m['job_id']) if m['job_id'] else '')) if m['role'] == 'user' else ''
        reprendre = ('<button type="button" class="ax-btn ghost h109-reprendre" data-h109-reprendre="%d">Reprendre cette instruction</button>' % m['id']
                     if m['role'] == 'user' and t['peut_continuer'] and not m['masque'] else '')
        if m['role'] == 'user' and m['etat'] in AVIS_ETAT:     # même avis que l'ancienne vue : un accusé d'enregistrement n'est pas une réponse
            reprendre = '<p class="h109-note" role="status">%s</p>' % e(AVIS_ETAT[m['etat']]) + reprendre
        corps += ('<article class="h109-message h109-%s" data-h109-message="%d"><header><strong>%s</strong> <small>%s · <span class="h109-etat" data-etat="%s">%s</span>%s</small></header>'
                  '<div class="h109-texte" data-h109-texte>%s</div>%s%s</article>') % (
            e(m['role'], quote=True), m['id'], qui_l, e(_date_locale(m['created'])), e(m['etat'], quote=True), e(m['etat_libelle']), e(recu),
            e(m['contenu']), ('<details><summary>Sources (%d)</summary><ul>%s</ul></details>' % (len(m['sources']), sources)) if sources else '', reprendre)
    suite = ('<button type="button" class="ax-btn ghost" data-h109-messages-suivants="%d">Messages suivants</button>' % page['next']) if page['next'] else ''
    if t['peut_continuer']:
        champs = {'matter': t['matter'] or '', 'key': t['mail_key'] or '', 'thread': t['id'], 'attachment_id': ''}
        continuer = form('assistant_ask', 'Continuer cette discussion', champs,
                         '<label for="h109-question">Nouvelle instruction</label><textarea id="h109-question" data-h109-brouillon="%s" required name="question" rows="3" maxlength="12000"></textarea>' % e(t['id'], quote=True))
    else:
        continuer = '<p class="h109-note">Vous lisez cette discussion en tant que Lecteur : vous ne pouvez pas la continuer.</p>'
    exporter_ = '<a class="ax-btn ghost" href="%s">Exporter (Markdown)</a> <a class="ax-btn ghost" href="%s">Exporter (JSON)</a>' % (
        e(prefix + '/api440/' + PREFIXE + 'exporter?' + urlencode({'id': t['id'], 'format': 'md'}), quote=True),
        e(prefix + '/api440/' + PREFIXE + 'exporter?' + urlencode({'id': t['id'], 'format': 'json'}), quote=True))
    return ('<section class="h109-detail" aria-labelledby="h109-detail-titre" data-h109-detail="%s" data-h109-revision="%d" data-h109-role="%s">'
            '<div class="h109-entete"><h2 id="h109-detail-titre" data-h109-titre>%s</h2><span class="h109-role">%s</span></div>'
            '<div class="h109-actions" data-h109-actions></div>'
            '<p class="h109-note">Rouvrir une discussion ne relance aucune instruction. « Continuer » ajoute un nouveau message ; « Reprendre cette instruction » '
            'recopie seulement son texte dans la zone de saisie.</p>'
            '<div class="h109-messages" data-h109-messages>%s</div>%s<div class="h109-composeur">%s</div><p class="h109-exports">%s</p></section>') % (
        e(t['id'], quote=True), t['revision'], e(t['role_acces'], quote=True), e(t['title'] or 'Discussion'), e(ROLES_LIBELLES.get(t['role_acces'], '')),
        corps, suite, continuer, exporter_)


def corbeille_html(desk, qui, prefix):
    from html import escape as e
    from .discussions56109 import corbeille as _corbeille
    c = _corbeille(desk, qui)
    lignes = ''.join('<li data-h109-fil="%s" data-h109-revision="%d"><span>%s</span> <small>supprimée le %s</small> %s</li>' % (
        e(x['id'], quote=True), x['revision'], e(x['title']), e(_date_locale(x['deleted_at'])),
        '<button type="button" class="ax-btn ghost" data-h109-restaurer>Restaurer</button>' if x['restaurable'] else '<small>délai dépassé</small>') for x in c['items'])
    return ('<section class="h109-corbeille" data-h109><h2>Corbeille des discussions</h2><p>Les discussions supprimées restent restaurables %d jours. '
            'Aucune pièce, aucun courriel ni aucun document n’a été supprimé avec elles.</p><ul>%s</ul>%s<p><a href="%s">Retour à l’historique</a></p></section>'
            '<script defer src="%s"></script>') % (
        c['jours'], lignes, '' if lignes else '<p class="h109-vide">La corbeille est vide.</p>', e(prefix + '/assistant?vue=historique', quote=True),
        e(prefix + '/static/v56109-discussions.js', quote=True))


def attribuer_html(desk, qui, prefix):
    from html import escape as e
    from . import membres5650
    data = a_attribuer(desk, qui, 200)
    comptes = ''.join('<option value="%s">%s</option>' % (e(k, quote=True), e(membres5650.label(v))) for k, v in sorted(membres5650.directory(desk).items()))
    lignes = ''.join('<li><label><input type="checkbox" data-h109-ancien value="%s"> %s · %s · %d message(s)</label></li>' % (
        e(x['id'], quote=True), e(_date_locale(x['updated'])), e(x['matter'] or 'Sans dossier'), x['messages']) for x in data['items'])
    return ('<section class="h109-attribution" data-h109><h2>Discussions antérieures à attribuer</h2><p>%d discussion(s) sans auteur établi. Leur titre et leur contenu '
            'ne sont pas affichés ici. Une attribution exige une preuve de leur origine (par exemple : installation à un seul compte depuis l’origine, confirmée '
            'par écrit) ; elle est tracée au journal. Elle n’est jamais déduite d’un courriel, d’un nom de poste ou du compte actuel.</p>'
            '<p class="h109-note" role="note">' + INFO_COMPTE_UNIQUE + '</p><ul>%s</ul>'
            '<label>Attribuer à<select data-h109-compte-cible>%s</select></label><label>Preuve de l’origine<textarea data-h109-preuve maxlength="500" rows="2"></textarea></label>'
            '<button type="button" class="ax-btn" data-h109-attribuer>Attribuer la sélection</button><p role="status" data-h109-etat></p></section>'
            '<script defer src="%s"></script>') % (data['total'], lignes, comptes, e(prefix + '/static/v56109-discussions.js', quote=True))


def vue_assistant(desk, qui, prefix, args, form, matters=()):
    """Point d'entrée de ``/assistant`` (nouvelle interface) : historique, corbeille ou attribution. Lecture seule."""
    from html import escape as e
    vue = str(args.get('vue') or '')
    if vue == 'corbeille':
        return corbeille_html(desk, qui, prefix)
    if vue == 'attribuer':
        try:
            return attribuer_html(desk, qui, prefix)
        except Stop:
            return '<p class="h109-alerte" role="alert">%s</p>' % e(MESSAGES['role_insuffisant'])
    if args.get('apres'):
        try:
            sous = fils(desk, qui, args, apres=str(args['apres']), limite=30)
        except Stop as ex:
            return '<p class="h109-alerte" role="alert">%s</p>' % e(MESSAGES.get(str(ex), 'Liste indisponible.'))
        suite = ('<a class="ax-btn ghost" href="%s">Discussions suivantes</a>' % e(prefix + '/assistant?vue=historique&apres=' + sous['next'], quote=True)) if sous['next'] else ''
        return '<div class="h109" data-h109><section class="h109-liste"><h2>Historique (suite)</h2><ul class="h109-fils">%s</ul>%s</section></div>' % (
            ''.join(_ligne_html(prefix, x, False) for x in sous['items']), suite)
    return page_html(desk, qui, prefix, args, form, matters)

"""5.6.103 (lot M) : connexion Microsoft simple par une application publique intégrée (identifiant client seul, aucun secret).

Point de montage pour l'assistant de mise en service (lot G)
-------------------------------------------------------------
- ``etapes_assistant(desk, owner, env=None)`` rend la liste (toujours une liste, jamais d'exception pour une application absente) des
  étapes Microsoft à afficher, chacune ``{'id', 'titre', 'etat', 'detail', 'action'}`` :

  * ``etat`` : ``'indisponible'`` (identifiant de l'application intégrée vide : « Application intégrée pas encore disponible : utilisez
    votre propre application »), ``'a_faire'``, ``'fait'``, ``'a_refaire'`` (autorisation retirée chez Microsoft : se reconnecter) ;
  * ``action`` : ``None`` ou ``{'route': 'm56103/microsoft/connexion', 'methode': 'POST', 'capacites': [...], 'mode': 'pkce'|'appareil'}``
    (le POST rend une adresse à ouvrir sur le poste, ou un code d'appareil à saisir sur microsoft.com/devicelogin pour le serveur ;
    ``m56103/microsoft/appareil/sonder`` suit ensuite le code d'appareil) ;
  * ids : ``microsoft.compte`` (compte relié), ``microsoft.courrier``, ``microsoft.agenda``, ``microsoft.fichiers``.
- ``disponible(desk)`` : vrai seulement si un identifiant d'application intégrée est renseigné. ``MESSAGE_INDISPONIBLE`` : texte à afficher.
- Le lot G n'appelle rien d'autre : aucun appel réseau, aucune écriture dans ``etapes_assistant`` (lecture seule, sûre pendant un GET).

Application intégrée
--------------------
- ``CLIENT_ID_INTEGRE`` : identifiant (client) de l'application inscrite par l'éditeur d'AxiorHub dans son locataire Entra,
  « comptes dans un annuaire organisationnel quelconque et comptes Microsoft personnels », plateforme « Applications mobiles et de
  bureau » (client public), redirection de bouclage ``http://localhost/axiorhub/microsoft-retour`` (le port est ignoré par Microsoft pour
  localhost), flux publics autorisés (code d'appareil). Renseigné depuis la 5.6.105 ; vide, l'interface dirait « pas encore
  disponible » et rien ne serait appelé. Un serveur peut renseigner sa propre application publique dans la configuration
  (``microsoft56103.client_id``) : elle passe alors avant l'application intégrée ; ce n'est jamais un secret. Voir ``docs/MICROSOFT-APPLICATION-PUBLIQUE-5.6.103.md``.
- Autorité ``common`` (comptes professionnels de n'importe quel locataire et comptes personnels).
- **Poste** : code d'autorisation avec PKCE (S256), ``state`` aléatoire gardé haché, à usage unique, valable 10 minutes, lié à la personne,
  à la génération de la connexion et à l'identifiant client ; retour sur une écoute ponctuelle ``http://localhost:<port>/axiorhub/microsoft-retour``
  (127.0.0.1 seulement), fermée après le premier retour valable ou l'expiration.
- **Serveur** : code d'appareil (RFC 8628) : ``authorization_pending`` → on attend au moins ``interval`` ; ``slow_down`` → intervalle
  augmenté de 5 s (RFC 8628 § 3.5 ; la page Microsoft ne le cite pas, il est traité quand même) ; ``authorization_declined`` → refus ;
  ``expired_token`` → expiré ; ``bad_verification_code`` → erreur. Le code d'appareil est rangé dans le coffre, jamais en base ni au journal.
- **Autorisations déléguées par capacité** (``CAPACITES``) : courrier = ``Mail.ReadWrite`` (lecture et brouillons), agenda =
  ``Calendars.ReadWrite``, fichiers = ``Files.ReadWrite`` (OneDrive de la personne), SharePoint (facultatif, consentement d'un
  administrateur) = ``Files.ReadWrite.All`` + ``Sites.Read.All`` ; toujours ``offline_access`` et ``User.Read``. **Jamais** ``Mail.Send``
  ni aucune portée de ``INTERDITES`` : refus avant la demande, et refus de la connexion si Microsoft en accorde une quand même.
  Le transport refuse aussi toute adresse d'envoi (``sendMail``, ``/send``, ``/reply``, ``/replyAll``, ``/forward``).
- **Jetons** : chiffrés dans le coffre local (``vault567``), un dossier par connexion ; jamais dans la configuration, la base ni le journal.
  Microsoft remplace le jeton de renouvellement à chaque usage : le nouveau est toujours rangé. ``invalid_grant`` ou
  ``interaction_required`` au renouvellement : état « À reconnecter », plus aucun appel.
- **Déconnexion** : Microsoft Graph ne révoque pas un jeton délégué isolé ; les jetons locaux sont effacés, la génération change (aucune
  reprise possible) et l'interface indique où retirer le consentement.
- **Transport** : ``TRANSPORT`` (tests : faux Microsoft) ; sinon ``common.HTTP`` vers les seules origines ``ORIGINES``.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import uuid

from .common import Stop

CLIENT_ID_INTEGRE = '0abc87ae-0917-4cd3-afa0-24129143d776'     # application « AxiorHub Pilote » inscrite par l'éditeur dans Entra (5.6.105 ; identifiant public, aucun secret)
AUTORITE = 'common'
LOGIN = 'https://login.microsoftonline.com'
GRAPH = 'https://graph.microsoft.com'
ORIGINES = (LOGIN, GRAPH)
CHEMIN_RETOUR = '/axiorhub/microsoft-retour'
REDIRECTION_INSCRITE = 'http://localhost' + CHEMIN_RETOUR
DEVICELOGIN = 'https://microsoft.com/devicelogin'
MESSAGE_INDISPONIBLE = 'Application intégrée pas encore disponible : utilisez votre propre application.'
DUREE_ETAT = 600                 # secondes : PKCE, 10 minutes
BAIL_RENOUVELLEMENT = 60
MARGE = 120
INTERVALLE_MIN, INTERVALLE_MAX = 5, 60
BASE = ('offline_access', 'User.Read')
CAPACITES = {'courrier': ('Mail.ReadWrite',), 'agenda': ('Calendars.ReadWrite',), 'fichiers': ('Files.ReadWrite',),
             'sharepoint': ('Files.ReadWrite.All', 'Sites.Read.All')}
LIBELLES = {'courrier': 'Outlook : lire le courrier et déposer des brouillons (jamais d’envoi)', 'agenda': 'Outlook : agenda',
            'fichiers': 'OneDrive : vos fichiers', 'sharepoint': 'SharePoint : bibliothèques du cabinet (consentement d’un administrateur)'}
# garde-fou : portées jamais demandées ni acceptées (envoi, boîtes partagées, annuaire, écriture sur tous les sites, protocoles hérités)
INTERDITES = frozenset(p.lower() for p in (
    'Mail.Send', 'Mail.Send.Shared', 'Mail.Send.All', 'Mail.ReadWrite.Shared', 'Mail.Read.Shared', 'SMTP.Send', 'EWS.AccessAsUser.All',
    'IMAP.AccessAsUser.All', 'POP.AccessAsUser.All', 'EAS.AccessAsUser.All', 'MailboxSettings.ReadWrite', 'Sites.ReadWrite.All',
    'Sites.FullControl.All', 'Sites.Manage.All', 'Directory.Read.All', 'Directory.ReadWrite.All', 'User.ReadWrite.All',
    'Calendars.ReadWrite.Shared', 'Contacts.ReadWrite.Shared'))
# actions Graph qui envoient un courriel : jamais appelées par AxiorHub, quel que soit le chemin (/me, /users/…)
ENVOI = re.compile(r'/(sendmail|send|reply|replyall|forward)/?$', re.I)
ETATS = {'en_attente': 'Connexion en attente', 'active': 'Connecté', 'a_reconnecter': 'À reconnecter', 'deconnectee': 'Déconnecté'}
MESSAGES = {
    'microsoft56103_indisponible': MESSAGE_INDISPONIBLE,
    'microsoft56103_capacite_inconnue': 'Usage Microsoft inconnu.',
    'microsoft56103_portee_interdite': 'Autorisation refusée par AxiorHub (envoi de courriel ou accès trop large).',
    'microsoft56103_portee_interdite_accordee': 'Microsoft a accordé une autorisation qu’AxiorHub refuse (envoi de courriel ou accès trop large) : '
                                               'connexion non enregistrée. Retirez cette autorisation de l’application dans Entra.',
    'microsoft56103_etat_invalide': 'Retour de Microsoft non reconnu ou déjà utilisé : recommencez la connexion.',
    'microsoft56103_etat_expire': 'La connexion a expiré (10 minutes) : recommencez.',
    'microsoft56103_autorisation_refusee': 'Autorisation refusée sur la page Microsoft : rien n’a été relié.',
    'microsoft56103_aucune_capacite_accordee': 'Microsoft n’a accordé aucune des autorisations demandées. Dans beaucoup de cabinets, un '
                                               'administrateur Microsoft 365 doit d’abord donner son consentement.',
    'microsoft56103_compte_different': 'Un autre compte Microsoft est déjà relié : déconnectez-le d’abord.',
    'microsoft56103_identite_illisible': 'Compte Microsoft illisible : connexion non enregistrée.',
    'microsoft56103_jeton_renouvellement_absent': 'Microsoft n’a pas fourni d’accès durable : recommencez la connexion.',
    'microsoft56103_reconnexion_requise': 'Autorisation Microsoft retirée ou expirée : reconnectez le compte.',
    'microsoft56103_non_connecte': 'Compte Microsoft non relié.',
    'microsoft56103_capacite_non_accordee': 'Cette autorisation Microsoft n’a pas été accordée : reconnectez en la demandant.',
    'microsoft56103_connexion_modifiee': 'La connexion a changé pendant l’opération : recommencez.',
    'microsoft56103_appareil_attente': 'En attente de votre saisie du code sur la page Microsoft.',
    'microsoft56103_appareil_refuse': 'Connexion refusée sur la page Microsoft.',
    'microsoft56103_appareil_expire': 'Le code a expiré (15 minutes) : demandez-en un nouveau.',
    'microsoft56103_appareil_inconnu': 'Code d’appareil non reconnu : demandez-en un nouveau.',
    'microsoft56103_origine_refusee': 'Adresse Microsoft non autorisée.',
    'microsoft56103_envoi_interdit': 'AxiorHub n’envoie jamais de courriel par Microsoft.',
    'microsoft56103_reponse_invalide': 'Réponse de Microsoft illisible.',
    'microsoft56103_renouvellement_en_cours': 'Renouvellement de l’accès en cours : réessayez dans un instant.',
    'microsoft56103_port_local_indisponible': 'Impossible d’ouvrir l’écoute locale du poste pour le retour de Microsoft.',
    'microsoft56103_flux_public_refuse': 'Microsoft refuse le code d’appareil pour cette application (flux publics non autorisés, ou politique '
                                         'du locataire) : connectez-vous depuis le poste ou utilisez votre propre application.',
}

TRANSPORT = None          # tests : appelable (méthode, url, en-têtes, corps) -> (statut, en-têtes, octets)
HORLOGE = time.time
_VERROUS, _VERROU = {}, threading.Lock()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS microsoft56103_connexions(id TEXT PRIMARY KEY, cabinet_id TEXT NOT NULL DEFAULT 'cabinet', owner TEXT NOT NULL,
  compte TEXT NOT NULL DEFAULT '', compte_id TEXT NOT NULL DEFAULT '', etat TEXT NOT NULL, portees TEXT NOT NULL DEFAULT '', mode TEXT NOT NULL DEFAULT '',
  client TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 1, jeton_version INTEGER NOT NULL DEFAULT 0, expire REAL NOT NULL DEFAULT 0,
  bail REAL NOT NULL DEFAULT 0, erreur TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1, cree TEXT NOT NULL, maj TEXT NOT NULL,
  UNIQUE(cabinet_id, owner));
CREATE TABLE IF NOT EXISTS microsoft56103_etats(etat_hash TEXT PRIMARY KEY, connexion_id TEXT NOT NULL, owner TEXT NOT NULL, capacites TEXT NOT NULL,
  verificateur TEXT NOT NULL, redirect TEXT NOT NULL, client TEXT NOT NULL, generation INTEGER NOT NULL, expire REAL NOT NULL,
  utilise INTEGER NOT NULL DEFAULT 0, cree TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS microsoft56103_appareils(id TEXT PRIMARY KEY, connexion_id TEXT NOT NULL, owner TEXT NOT NULL, capacites TEXT NOT NULL,
  client TEXT NOT NULL, generation INTEGER NOT NULL, code_utilisateur TEXT NOT NULL, adresse TEXT NOT NULL, intervalle INTEGER NOT NULL,
  prochain REAL NOT NULL, expire REAL NOT NULL, etat TEXT NOT NULL DEFAULT 'en_attente', erreur TEXT NOT NULL DEFAULT '', cree TEXT NOT NULL,
  maj TEXT NOT NULL);
'''


def ensure_schema(desk):
    """Création idempotente, appelée au démarrage (``Desk``) ; jamais pendant un GET."""
    desk.db.executescript(SCHEMA)


def _table_presente(desk):
    try:
        return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='microsoft56103_connexions'").fetchone())
    except Exception:   # noqa: BLE001
        return False


def _iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _hache(valeur):
    return hashlib.sha256(str(valeur).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------------------------------------ application intégrée
GUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


def client_id(desk):
    """Identifiant client de l'application publique : application propre du serveur (``microsoft56103.client_id``) si elle est
    renseignée, sinon application intégrée ; '' si aucune des deux n'est un GUID."""
    for valeur in (str(((getattr(desk, 'c', None) or {}).get('microsoft56103') or {}).get('client_id') or ''), CLIENT_ID_INTEGRE):
        valeur = str(valeur or '').strip()
        if valeur and GUID.match(valeur):
            return valeur.lower()
    return ''


def disponible(desk):
    return bool(client_id(desk))


def _exiger_client(desk):
    cid = client_id(desk)
    if not cid:
        raise Stop('microsoft56103_indisponible')        # aucune adresse construite, aucun appel
    return cid


def normaliser(portee):
    p = str(portee or '').strip()
    if p.lower().startswith(GRAPH + '/'):
        p = p[len(GRAPH) + 1:]
    return p.lower()


def portees(capacites):
    """Portées demandées pour des capacités ; garde-fou : refus d'une capacité inconnue ou d'une portée interdite."""
    voulues = sorted(set(capacites or ()))
    if not voulues or any(c not in CAPACITES for c in voulues):
        raise Stop('microsoft56103_capacite_inconnue')
    liste = list(BASE) + sorted({p for c in voulues for p in CAPACITES[c]})
    if {normaliser(p) for p in liste} & INTERDITES:
        raise Stop('microsoft56103_portee_interdite')
    return ' '.join(liste)


def capacites_accordees(portees_accordees):
    accordees = {normaliser(p) for p in str(portees_accordees or '').split()}
    return [c for c, ps in CAPACITES.items() if all(normaliser(p) in accordees for p in ps)]


def mode_pour(env):
    """``pkce`` si le navigateur est sur la machine d'AxiorHub (poste), sinon ``appareil`` (serveur)."""
    try:
        from .web5662 import local_browser
        return 'pkce' if local_browser(env or {}) else 'appareil'
    except Exception:   # noqa: BLE001 — doute : le code d'appareil fonctionne partout
        return 'appareil'


# ------------------------------------------------------------------------------------------------ transport
class ErreurMicrosoft(Stop):
    def __init__(self, code, statut=0, attente=None, oauth=''):
        super().__init__(code)
        self.statut, self.attente, self.oauth = int(statut or 0), attente, oauth


def verifier_adresse(methode, url):
    """Garde-fou commun : origine Microsoft autorisée et jamais une action d'envoi."""
    p = urlsplit(str(url or ''))
    if p.scheme != 'https' or p.username or p.password or p.fragment or ('https://' + (p.netloc or '')) not in ORIGINES:
        raise Stop('microsoft56103_origine_refusee')
    if ENVOI.search(p.path or ''):
        raise Stop('microsoft56103_envoi_interdit')


def _transport(methode, url, entetes, corps):
    verifier_adresse(methode, url)
    if TRANSPORT is not None:
        return TRANSPORT(methode, url, dict(entetes), corps)
    from .cloud5688 import CloudHTTP          # mêmes garde-fous que common.HTTP ; statut, en-têtes et corps rendus, aucune redirection suivie
    p = urlsplit(url)
    return CloudHTTP('%s://%s' % (p.scheme, p.netloc), timeout=30).call(methode, url, corps, entetes, 160_000_000)


def _json(brut):
    try:
        out = json.loads(brut or b'{}')
    except (ValueError, UnicodeError):
        raise Stop('microsoft56103_reponse_invalide') from None
    if not isinstance(out, dict):
        raise Stop('microsoft56103_reponse_invalide')
    return out


def _attente(entetes):
    from .erreurs5634 import retry_after_seconds
    return retry_after_seconds({k.lower(): v for k, v in (entetes or {}).items()}.get('retry-after'))


def _code_oauth(brut):
    try:
        err = _json(brut).get('error')
    except Stop:
        return ''
    return str(err) if isinstance(err, str) else ''


def _formulaire(url, champs):
    statut, entetes, brut = _transport('POST', url, {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
                                       urlencode(champs).encode())
    if statut >= 400:
        oauth = _code_oauth(brut)
        raise ErreurMicrosoft('microsoft56103_reconnexion_requise' if oauth in ('invalid_grant', 'interaction_required', 'consent_required')
                              else 'microsoft56103_http_%d' % statut, statut, _attente(entetes), oauth)
    return _json(brut) if brut else {}


def _point(nom):
    return '%s/%s/oauth2/v2.0/%s' % (LOGIN, AUTORITE, nom)


# ------------------------------------------------------------------------------------------------ coffre
def _vault(desk, conn_id, nom):
    return Path(desk.c['state_dir']) / 'vault' / 'microsoft56103' / re.sub(r'[^A-Za-z0-9_-]', '', str(conn_id)) / (nom + '.secret')


def _ecrire(chemin, valeur):
    from .vault567 import write
    write(chemin, valeur, limit=16000)        # les jetons Microsoft dépassent parfois 4 096 caractères


def _lire(chemin):
    """Lecture d'un secret du coffre (mêmes contrôles que ``common.read_secret``, bornes adaptées aux jetons Microsoft)."""
    from . import portable
    from .vault567 import decrypt
    p = Path(chemin)
    if not p.is_file():
        return ''
    if portable.too_open(p.stat().st_mode, 0o027) or p.stat().st_size > 32768:
        raise Stop('secret_absent_ou_permissions_trop_larges')
    valeur = decrypt(p, p.read_text(encoding='utf-8').strip())
    if not valeur or len(valeur) > 16000 or '\n' in valeur or '\r' in valeur:
        raise Stop('secret_invalide')
    return valeur


def _effacer(desk, conn_id, noms=('access', 'refresh')):
    for nom in noms:
        chemin = _vault(desk, conn_id, nom)
        if chemin.is_file():
            chemin.unlink()


# ------------------------------------------------------------------------------------------------ connexions
def connexion(desk, owner):
    if not _table_presente(desk):
        return None
    row = desk.db.execute("SELECT * FROM microsoft56103_connexions WHERE cabinet_id='cabinet' AND owner=?", (str(owner),)).fetchone()
    return dict(row) if row else None


def lire(desk, conn_id):
    row = desk.db.execute('SELECT * FROM microsoft56103_connexions WHERE id=?', (str(conn_id or ''),)).fetchone()
    if not row:
        raise Stop('microsoft56103_non_connecte')
    return dict(row)


def capacites(row):
    return capacites_accordees((row or {}).get('portees')) if row and row.get('etat') == 'active' else []


def vue(row):
    """Vue sans secret d'une connexion."""
    if not row:
        return {'etat': 'absente', 'libelle': 'Non connecté', 'capacites': [], 'compte': ''}
    return {'id': row['id'], 'compte': row['compte'], 'etat': row['etat'], 'libelle': ETATS.get(row['etat'], row['etat']), 'capacites': capacites(row),
            'mode': row['mode'], 'version': row['version'], 'generation': row['generation'], 'erreur': row['erreur'], 'maj': row['maj']}


def _avec_existantes(desk, owner, capacites_voulues):
    """Consentement additionnel : les capacités déjà accordées sont redemandées avec la nouvelle, pour qu'aucune ne soit perdue."""
    row = connexion(desk, owner)
    return sorted(set(capacites_voulues or ()) | set(capacites(row)))


def _preparer_connexion(desk, owner, cid):
    row = connexion(desk, owner)
    maintenant = _iso()
    if row is None:
        ident = 'ms-' + uuid.uuid4().hex
        desk.db.execute("INSERT INTO microsoft56103_connexions(id, owner, etat, client, cree, maj) VALUES(?,?,?,?,?,?)",
                        (ident, str(owner), 'en_attente', cid, maintenant, maintenant))
        desk.db.commit()
        return lire(desk, ident)
    if row['etat'] == 'deconnectee':
        desk.db.execute("UPDATE microsoft56103_connexions SET etat='en_attente', erreur='', version=version+1, maj=? WHERE id=?", (maintenant, row['id']))
        desk.db.commit()
        return lire(desk, row['id'])
    return row


# ------------------------------------------------------------------------------------------------ poste : PKCE et retour de bouclage
def demarrer_pkce(desk, owner, capacites_voulues, port):
    """Adresse de connexion Microsoft (navigateur du poste). ``port`` : écoute locale ouverte par ``ecouter_retour``."""
    from .adaptateurs5652 import pkce_pair
    cid = _exiger_client(desk)
    portees(capacites_voulues)                       # garde-fou sur la demande elle-même
    capacites_voulues = _avec_existantes(desk, owner, capacites_voulues)
    scope = portees(capacites_voulues)
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise Stop('microsoft56103_port_local_indisponible')
    row = _preparer_connexion(desk, owner, cid)
    verificateur, defi = pkce_pair()
    etat = secrets.token_urlsafe(32)
    redirect = 'http://localhost:%d%s' % (port, CHEMIN_RETOUR)
    t = HORLOGE()
    desk.db.execute('DELETE FROM microsoft56103_etats WHERE expire<?', (t - DUREE_ETAT,))
    desk.db.execute('INSERT INTO microsoft56103_etats(etat_hash, connexion_id, owner, capacites, verificateur, redirect, client, generation, expire, cree) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)', (_hache(etat), row['id'], str(owner), ' '.join(sorted(set(capacites_voulues))), verificateur, redirect, cid,
                                                  row['generation'], t + DUREE_ETAT, _iso()))
    desk.db.commit()
    requete = {'client_id': cid, 'response_type': 'code', 'redirect_uri': redirect, 'response_mode': 'query', 'scope': scope, 'state': etat,
               'code_challenge': defi, 'code_challenge_method': 'S256', 'prompt': 'select_account'}
    if row.get('compte'):
        requete['login_hint'] = row['compte']
    desk.audit('microsoft56103_depart', {'mode': 'pkce', 'capacites': sorted(set(capacites_voulues))})
    return {'mode': 'pkce', 'url': _point('authorize') + '?' + urlencode(requete), 'expire_secondes': DUREE_ETAT}


def _consommer(desk, etat):
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT * FROM microsoft56103_etats WHERE etat_hash=?', (_hache(etat),)).fetchone()
        if row is None or row['utilise']:
            desk.db.rollback()
            return None
        desk.db.execute('UPDATE microsoft56103_etats SET utilise=1 WHERE etat_hash=? AND utilise=0', (row['etat_hash'],))
        desk.db.commit()
        return dict(row)
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise


def terminer_pkce(desk, args):
    """Retour de Microsoft sur l'écoute locale. Rend la vue de la connexion ; lève une erreur nommée sans rien activer sinon."""
    etat, code = str(args.get('state') or ''), str(args.get('code') or '')
    if not etat or len(etat) > 200 or len(code) > 4000:
        raise Stop('microsoft56103_etat_invalide')
    demande = _consommer(desk, etat)                 # avant tout autre contrôle : un rejeu trouve l'état déjà utilisé
    if demande is None:
        raise Stop('microsoft56103_etat_invalide')
    if demande['expire'] < HORLOGE():
        raise Stop('microsoft56103_etat_expire')
    if args.get('error'):
        desk.audit('microsoft56103_refus', {'mode': 'pkce'})
        raise Stop('microsoft56103_autorisation_refusee')
    if not code:
        raise Stop('microsoft56103_etat_invalide')
    cid = _exiger_client(desk)
    if cid != demande['client']:
        raise Stop('microsoft56103_connexion_modifiee')     # identifiant client changé pendant la connexion
    jeton = _formulaire(_point('token'), {'client_id': cid, 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': demande['redirect'],
                                          'code_verifier': demande['verificateur'], 'scope': portees(demande['capacites'].split())})
    return _finaliser(desk, demande['connexion_id'], demande['generation'], jeton, 'pkce', cid)


def ecouter_retour(config, port=0, minutes=10, traiter=None):
    """Poste : écoute ponctuelle de ``http://localhost:<port>/axiorhub/microsoft-retour`` (127.0.0.1 seulement) ; s'arrête au premier
    retour traité ou à l'expiration. Rend le port réellement ouvert. ``traiter(query)`` (tests) remplace le traitement par défaut."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import os

    class Serveur(HTTPServer):
        allow_reuse_address = os.name != 'nt'

    class Gestion(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = urlsplit(self.path)
            if p.path != CHEMIN_RETOUR:
                self.send_response(404)
                self.end_headers()
                return
            args = {k: v[0] for k, v in parse_qs(p.query).items()}
            try:
                if traiter is not None:
                    titre, texte, fin = traiter(args)
                else:
                    titre, texte, fin = _traiter_retour(config, args)
            except Exception:   # noqa: BLE001 — jamais de trace ni de paramètre OAuth dans la page
                titre, texte, fin = 'Connexion non aboutie', 'Recommencez depuis AxiorHub.', False
            echap = lambda s: str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            page = ('<!doctype html><meta charset="utf-8"><title>AxiorHub Pilote</title><body style="font-family:system-ui;margin:3rem;max-width:40rem">'
                    '<h1>%s</h1><p>%s</p><p>Vous pouvez fermer cet onglet et revenir à AxiorHub.</p></body>') % (echap(titre), echap(texte))
            data = page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(data)
            if fin:
                self.server.fini = True

    serveur = None
    for candidat in ((int(port), 0) if port else (0,)):
        try:
            serveur = Serveur(('127.0.0.1', candidat), Gestion)
            break
        except OSError:
            continue
    if serveur is None:
        raise Stop('microsoft56103_port_local_indisponible')
    serveur.fini = False
    serveur.timeout = 1
    limite = time.monotonic() + minutes * 60

    def boucle():
        try:
            while not serveur.fini and time.monotonic() < limite:
                serveur.handle_request()
        finally:
            serveur.server_close()
    threading.Thread(target=boucle, name='microsoft56103-retour', daemon=True).start()
    return serveur.server_address[1]


def _traiter_retour(config, args):
    from .desk import Desk
    desk = Desk(config)
    try:
        out = terminer_pkce(desk, args)
        return 'Compte Microsoft relié', 'Compte : %s.' % out['connexion']['compte'], True
    except Stop as ex:
        code = str(ex).split(':')[0]
        # un retour étranger (état inconnu) ne ferme pas l'écoute de la tentative en cours
        return 'Connexion non aboutie', MESSAGES.get(code, 'Connexion Microsoft impossible.'), code != 'microsoft56103_etat_invalide'
    finally:
        desk.db.close()


# ------------------------------------------------------------------------------------------------ serveur : code d'appareil
def demarrer_appareil(desk, owner, capacites_voulues):
    """Demande un code d'appareil ; l'avocat le saisit sur ``verification_uri`` depuis n'importe quel appareil."""
    cid = _exiger_client(desk)
    portees(capacites_voulues)                       # garde-fou sur la demande elle-même
    capacites_voulues = _avec_existantes(desk, owner, capacites_voulues)
    scope = portees(capacites_voulues)
    row = _preparer_connexion(desk, owner, cid)
    try:
        out = _formulaire(_point('devicecode'), {'client_id': cid, 'scope': scope})
    except ErreurMicrosoft as error:
        if error.oauth in ('unauthorized_client', 'invalid_client'):
            raise Stop('microsoft56103_flux_public_refuse') from None
        raise
    code_appareil, code_utilisateur = str(out.get('device_code') or ''), str(out.get('user_code') or '')
    adresse = str(out.get('verification_uri') or DEVICELOGIN)
    if not code_appareil or not re.fullmatch(r'[A-Za-z0-9-]{4,32}', code_utilisateur) or not adresse.startswith('https://'):
        raise Stop('microsoft56103_reponse_invalide')
    intervalle = min(INTERVALLE_MAX, max(INTERVALLE_MIN, int(out.get('interval') or INTERVALLE_MIN)))
    t = HORLOGE()
    ident = 'ap-' + uuid.uuid4().hex
    _ecrire(_vault(desk, row['id'], 'appareil-' + ident), code_appareil)
    desk.db.execute('UPDATE microsoft56103_appareils SET etat=?, maj=? WHERE connexion_id=? AND etat=?', ('annule', _iso(), row['id'], 'en_attente'))
    desk.db.execute('INSERT INTO microsoft56103_appareils(id, connexion_id, owner, capacites, client, generation, code_utilisateur, adresse, intervalle, prochain, '
                    'expire, cree, maj) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, row['id'], str(owner), ' '.join(sorted(set(capacites_voulues))), cid, row['generation'], code_utilisateur, adresse[:200],
                     intervalle, t + intervalle, t + max(60, min(1800, int(out.get('expires_in') or 900))), _iso(), _iso()))
    desk.db.commit()
    desk.audit('microsoft56103_depart', {'mode': 'appareil', 'capacites': sorted(set(capacites_voulues))})
    return {'mode': 'appareil', 'flux': ident, 'code': code_utilisateur, 'adresse': adresse, 'intervalle': intervalle,
            'expire_secondes': int(max(60, min(1800, int(out.get('expires_in') or 900))))}


def _fin_appareil(desk, flux, etat, erreur=''):
    desk.db.execute('UPDATE microsoft56103_appareils SET etat=?, erreur=?, maj=? WHERE id=?', (etat, erreur, _iso(), flux['id']))
    desk.db.commit()
    _effacer(desk, flux['connexion_id'], ('appareil-' + flux['id'],))


def sonder_appareil(desk, owner, flux_id):
    """Un tour d'attente du code d'appareil (appelé par la page, au plus une fois par intervalle). Rend ``{'etat', 'message', ...}``."""
    row = desk.db.execute('SELECT * FROM microsoft56103_appareils WHERE id=?', (str(flux_id or ''),)).fetchone()
    if not row or row['owner'] != str(owner):
        raise Stop('microsoft56103_appareil_inconnu')            # le flux d'une autre personne n'existe pas pour celle-ci
    flux = dict(row)
    if flux['etat'] != 'en_attente':                     # flux déjà terminé : état rendu tel quel, aucun appel
        message = {'accepte': 'Compte Microsoft relié.', 'refuse': MESSAGES['microsoft56103_appareil_refuse'],
                   'expire': MESSAGES['microsoft56103_appareil_expire']}.get(flux['etat'], MESSAGES['microsoft56103_appareil_inconnu'])
        return {'etat': flux['etat'], 'message': message, 'intervalle': flux['intervalle']}
    t = HORLOGE()
    if t >= flux['expire']:
        _fin_appareil(desk, flux, 'expire')
        return {'etat': 'expire', 'message': MESSAGES['microsoft56103_appareil_expire']}
    if t < flux['prochain']:
        return {'etat': 'en_attente', 'message': MESSAGES['microsoft56103_appareil_attente'], 'intervalle': flux['intervalle'],
                'attendre': round(flux['prochain'] - t, 1)}                          # trop tôt : aucun appel à Microsoft
    code_appareil = _lire(_vault(desk, flux['connexion_id'], 'appareil-' + flux['id']))
    if not code_appareil:
        _fin_appareil(desk, flux, 'erreur', 'code_absent')
        return {'etat': 'erreur', 'message': MESSAGES['microsoft56103_appareil_inconnu']}
    desk.db.execute('UPDATE microsoft56103_appareils SET prochain=?, maj=? WHERE id=?', (t + flux['intervalle'], _iso(), flux['id']))
    desk.db.commit()
    try:
        jeton = _formulaire(_point('token'), {'grant_type': 'urn:ietf:params:oauth:grant-type:device_code', 'client_id': flux['client'],
                                              'device_code': code_appareil})
    except ErreurMicrosoft as error:
        if error.oauth == 'authorization_pending':
            return {'etat': 'en_attente', 'message': MESSAGES['microsoft56103_appareil_attente'], 'intervalle': flux['intervalle']}
        if error.oauth == 'slow_down':
            nouvel = min(INTERVALLE_MAX, flux['intervalle'] + 5)
            desk.db.execute('UPDATE microsoft56103_appareils SET intervalle=?, prochain=?, maj=? WHERE id=?', (nouvel, t + nouvel, _iso(), flux['id']))
            desk.db.commit()
            return {'etat': 'en_attente', 'message': MESSAGES['microsoft56103_appareil_attente'], 'intervalle': nouvel}
        if error.oauth == 'authorization_declined':
            _fin_appareil(desk, flux, 'refuse')
            desk.audit('microsoft56103_refus', {'mode': 'appareil'})
            return {'etat': 'refuse', 'message': MESSAGES['microsoft56103_appareil_refuse']}
        if error.oauth == 'expired_token':
            _fin_appareil(desk, flux, 'expire')
            return {'etat': 'expire', 'message': MESSAGES['microsoft56103_appareil_expire']}
        if error.oauth == 'bad_verification_code':
            _fin_appareil(desk, flux, 'erreur', 'bad_verification_code')
            return {'etat': 'erreur', 'message': MESSAGES['microsoft56103_appareil_inconnu']}
        if error.statut >= 500 or error.statut == 429:
            return {'etat': 'en_attente', 'message': 'Microsoft est momentanément indisponible ; nouvel essai bientôt.', 'intervalle': flux['intervalle']}
        _fin_appareil(desk, flux, 'erreur', error.oauth or str(error))
        return {'etat': 'erreur', 'message': MESSAGES.get(str(error), 'Connexion Microsoft impossible.')}
    try:
        out = _finaliser(desk, flux['connexion_id'], flux['generation'], jeton, 'appareil', flux['client'])
    except Stop as error:
        _fin_appareil(desk, flux, 'erreur', str(error).split(':')[0])
        raise
    _fin_appareil(desk, flux, 'accepte')
    return {'etat': 'accepte', 'message': 'Compte Microsoft relié : %s.' % out['connexion']['compte'], 'connexion': out['connexion']}


# ------------------------------------------------------------------------------------------------ fin commune : identité, portées, jetons
def _finaliser(desk, conn_id, generation, jeton, mode, cid):
    acces = str(jeton.get('access_token') or '')
    if not acces or str(jeton.get('token_type') or 'Bearer').lower() != 'bearer':
        raise Stop('microsoft56103_autorisation_refusee')
    accordees = {normaliser(p) for p in str(jeton.get('scope') or '').split()}
    if accordees & INTERDITES:                       # garde-fou : un jeton capable d'envoyer n'est jamais conservé
        desk.audit('microsoft56103_portee_interdite', {'mode': mode})
        raise Stop('microsoft56103_portee_interdite_accordee')
    utiles = [c for c in CAPACITES if all(normaliser(p) in accordees for p in CAPACITES[c])]
    if not utiles:
        raise Stop('microsoft56103_aucune_capacite_accordee')
    me = _appel_brut('GET', GRAPH + '/v1.0/me?$select=id,userPrincipalName,mail,displayName', acces)
    compte = str(me.get('userPrincipalName') or me.get('mail') or '').strip().lower()
    compte_id = str(me.get('id') or '').strip()
    if not compte_id or not re.fullmatch(r'[^@\s]+@[^@\s]+', compte):
        raise Stop('microsoft56103_identite_illisible')
    row = lire(desk, conn_id)
    if row['compte_id'] and row['compte_id'] != compte_id:
        raise Stop('microsoft56103_compte_different')
    renouvellement = str(jeton.get('refresh_token') or '')
    if not renouvellement and not _vault(desk, conn_id, 'refresh').is_file():
        raise Stop('microsoft56103_jeton_renouvellement_absent')
    portees_txt = ' '.join(sorted(p for p in accordees if p))
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        cur = desk.db.execute("UPDATE microsoft56103_connexions SET compte=?, compte_id=?, etat='active', portees=?, mode=?, client=?, jeton_version=jeton_version+1, "
                              "expire=?, bail=0, erreur='', version=version+1, maj=? WHERE id=? AND generation=?",
                              (compte, compte_id, portees_txt, mode, cid, HORLOGE() + max(60, int(jeton.get('expires_in') or 3600)) - MARGE, _iso(), conn_id, generation))
        if not cur.rowcount:
            raise Stop('microsoft56103_connexion_modifiee')        # déconnexion pendant la connexion : rien n'est réactivé
        _ecrire(_vault(desk, conn_id, 'access'), acces)
        if renouvellement:
            _ecrire(_vault(desk, conn_id, 'refresh'), renouvellement)
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    nouvelle = lire(desk, conn_id)
    desk.audit('microsoft56103_connecte', {'mode': mode, 'capacites': capacites(nouvelle)})
    return {'connexion': vue(nouvelle), 'capacites': capacites(nouvelle)}


# ------------------------------------------------------------------------------------------------ jeton d'accès et renouvellement
def _verrou(conn_id):
    with _VERROU:
        return _VERROUS.setdefault(conn_id, threading.Lock())


def _marquer_a_reconnecter(desk, row, cause):
    desk.db.execute("UPDATE microsoft56103_connexions SET etat='a_reconnecter', erreur=?, expire=0, bail=0, version=version+1, maj=? "
                    "WHERE id=? AND generation=? AND etat='active'", (cause[:40], _iso(), row['id'], row['generation']))
    desk.db.commit()
    _effacer(desk, row['id'], ('access',))
    desk.audit('microsoft56103_a_reconnecter', {'cause': cause[:40]})


def jeton(desk, conn_id, forcer=False):
    row = lire(desk, conn_id)
    if row['etat'] == 'a_reconnecter':
        raise Stop('microsoft56103_reconnexion_requise')
    if row['etat'] != 'active':
        raise Stop('microsoft56103_non_connecte')
    if not forcer and row['expire'] > HORLOGE() + 30:
        acces = _lire(_vault(desk, row['id'], 'access'))
        if acces:
            return acces
    with _verrou(row['id']):
        frais = lire(desk, conn_id)
        if frais['etat'] != 'active' or frais['generation'] != row['generation']:
            raise Stop('microsoft56103_reconnexion_requise' if frais['etat'] == 'a_reconnecter' else 'microsoft56103_non_connecte')
        if frais['jeton_version'] != row['jeton_version'] and frais['expire'] > HORLOGE() + 30:
            return _lire(_vault(desk, row['id'], 'access'))
        return _renouveler(desk, frais)


def _renouveler(desk, row):
    t = HORLOGE()
    cur = desk.db.execute('UPDATE microsoft56103_connexions SET bail=? WHERE id=? AND generation=? AND bail<?', (t + BAIL_RENOUVELLEMENT, row['id'], row['generation'], t))
    desk.db.commit()
    if not cur.rowcount:
        for _ in range(20):
            time.sleep(0.25)
            frais = lire(desk, row['id'])
            if frais['jeton_version'] != row['jeton_version'] and frais['etat'] == 'active':
                return _lire(_vault(desk, row['id'], 'access'))
            if frais['etat'] != 'active':
                break
        raise Stop('microsoft56103_renouvellement_en_cours')
    try:
        renouvellement = _lire(_vault(desk, row['id'], 'refresh'))
        if not renouvellement:
            _marquer_a_reconnecter(desk, row, 'jeton_absent')
            raise Stop('microsoft56103_reconnexion_requise')
        try:
            neuf = _formulaire(_point('token'), {'client_id': row['client'], 'grant_type': 'refresh_token', 'refresh_token': renouvellement,
                                                 'scope': ' '.join(list(BASE) + sorted({p for c in capacites(row) for p in CAPACITES[c]}))})
        except ErreurMicrosoft as error:
            if error.oauth in ('invalid_grant', 'interaction_required', 'consent_required'):
                _marquer_a_reconnecter(desk, row, error.oauth)
            raise
        acces = str(neuf.get('access_token') or '')
        if not acces:
            raise Stop('microsoft56103_reponse_invalide')
        if {normaliser(p) for p in str(neuf.get('scope') or '').split()} & INTERDITES:
            _marquer_a_reconnecter(desk, row, 'portee_interdite')
            raise Stop('microsoft56103_portee_interdite_accordee')
        desk.db.execute('BEGIN IMMEDIATE')
        try:
            cur = desk.db.execute("UPDATE microsoft56103_connexions SET jeton_version=jeton_version+1, expire=?, bail=0, maj=? WHERE id=? AND generation=? AND etat='active'",
                                  (HORLOGE() + max(60, int(neuf.get('expires_in') or 3600)) - MARGE, _iso(), row['id'], row['generation']))
            if not cur.rowcount:
                raise Stop('microsoft56103_non_connecte')          # déconnexion pendant le renouvellement : le nouveau jeton est jeté
            _ecrire(_vault(desk, row['id'], 'access'), acces)
            if neuf.get('refresh_token'):                           # Microsoft remplace le jeton de renouvellement à chaque usage
                _ecrire(_vault(desk, row['id'], 'refresh'), str(neuf['refresh_token']))
            desk.db.commit()
        except BaseException:
            if desk.db.in_transaction:
                desk.db.rollback()
            raise
        return acces
    finally:
        desk.db.execute('UPDATE microsoft56103_connexions SET bail=0 WHERE id=? AND bail>0', (row['id'],))
        desk.db.commit()


# ------------------------------------------------------------------------------------------------ appels Graph
def _appel_brut(methode, url, acces, corps=None, entetes=None):
    statut, rep, brut = requete_brute(methode, url, acces, None if corps is None else json.dumps(corps).encode('utf-8'),
                                      {**({'Content-Type': 'application/json'} if corps is not None else {}), **(entetes or {})})
    if statut >= 400:
        raise ErreurMicrosoft('microsoft56103_http_%d' % statut, statut, _attente(rep))
    return _json(brut) if brut else {}


def requete_brute(methode, url, acces, donnees=None, entetes=None):
    head = {'Authorization': 'Bearer ' + acces, 'Accept': 'application/json', **(entetes or {})}
    return _transport(methode, url, head, donnees)


def requete(desk, conn_id, methode, url, donnees=None, entetes=None, capacite=''):
    """Appel Graph brut pour une connexion active : rend ``(statut, en-têtes, octets)`` ; un 401 déclenche un seul renouvellement."""
    row = lire(desk, conn_id)
    if capacite and capacite not in capacites(row):
        raise Stop('microsoft56103_capacite_non_accordee')
    verifier_adresse(methode, url)
    statut, rep, brut = requete_brute(methode, url, jeton(desk, conn_id), donnees, entetes)
    if statut == 401:
        statut, rep, brut = requete_brute(methode, url, jeton(desk, conn_id, forcer=True), donnees, entetes)
    return statut, rep, brut


def appeler(desk, conn_id, methode, url, corps=None, capacite='', entetes=None):
    statut, rep, brut = requete(desk, conn_id, methode, url, None if corps is None else json.dumps(corps).encode('utf-8'),
                                {**({'Content-Type': 'application/json'} if corps is not None else {}), **(entetes or {})}, capacite)
    if statut >= 400:
        raise ErreurMicrosoft('microsoft56103_http_%d' % statut, statut, _attente(rep))
    return _json(brut) if brut else {}


def connexion_active(desk, owner, capacite=''):
    row = connexion(desk, owner)
    if not row or row['etat'] in ('deconnectee', 'en_attente'):
        raise Stop('microsoft56103_non_connecte')
    if row['etat'] == 'a_reconnecter':
        raise Stop('microsoft56103_reconnexion_requise')
    if capacite and capacite not in capacites(row):
        raise Stop('microsoft56103_capacite_non_accordee')
    return row


# ------------------------------------------------------------------------------------------------ déconnexion
def deconnecter(desk, owner):
    """Arrête tout usage (nouvelle génération), efface les jetons locaux et les codes d'appareil en attente. Microsoft Graph ne sait pas
    révoquer un jeton délégué isolé : l'interface indique où retirer le consentement."""
    row = connexion(desk, owner)
    if not row:
        raise Stop('microsoft56103_non_connecte')
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        desk.db.execute("UPDATE microsoft56103_connexions SET etat='deconnectee', portees='', expire=0, bail=0, generation=generation+1, erreur='', "
                        "version=version+1, maj=? WHERE id=?", (_iso(), row['id']))
        desk.db.execute('DELETE FROM microsoft56103_etats WHERE connexion_id=?', (row['id'],))
        flux = [r['id'] for r in desk.db.execute("SELECT id FROM microsoft56103_appareils WHERE connexion_id=? AND etat='en_attente'", (row['id'],))]
        desk.db.execute("UPDATE microsoft56103_appareils SET etat='annule', maj=? WHERE connexion_id=? AND etat='en_attente'", (_iso(), row['id']))
        desk.db.commit()
    except BaseException:
        if desk.db.in_transaction:
            desk.db.rollback()
        raise
    _effacer(desk, row['id'], ('access', 'refresh') + tuple('appareil-' + f for f in flux))
    desk.audit('microsoft56103_deconnecte', {})
    return {'connexion': vue(lire(desk, row['id'])), 'revocation': 'locale_seulement',
            'message': 'Compte Microsoft déconnecté : les jetons ont été effacés de cette installation. Pour retirer aussi l’autorisation chez '
                       'Microsoft : compte professionnel, https://myapps.microsoft.com (ou votre administrateur) ; compte personnel, '
                       'https://account.live.com/consent/Manage.'}


# ------------------------------------------------------------------------------------------------ point de montage du lot G
def etapes_assistant(desk, owner, env=None):
    """Étapes Microsoft de l'assistant de mise en service (lecture seule, aucun appel réseau). Voir l'en-tête du module."""
    mode = mode_pour(env)
    if not disponible(desk):
        return [{'id': 'microsoft.compte', 'titre': 'Compte Microsoft (Outlook, agenda, OneDrive)', 'etat': 'indisponible',
                 'detail': MESSAGE_INDISPONIBLE, 'action': None}]
    try:
        row = connexion(desk, owner)
    except Exception:   # noqa: BLE001 — base illisible : étape à faire, jamais un faux « fait »
        row = None
    accordees = set(capacites(row))
    etat_compte = 'fait' if row and row['etat'] == 'active' else ('a_refaire' if row and row['etat'] == 'a_reconnecter' else 'a_faire')
    sortie = [{'id': 'microsoft.compte', 'titre': 'Compte Microsoft (Outlook, agenda, OneDrive)', 'etat': etat_compte,
               'detail': ('Relié : %s.' % row['compte']) if etat_compte == 'fait' else (MESSAGES['microsoft56103_reconnexion_requise'] if etat_compte == 'a_refaire'
                                                                                       else 'Connexion par la page de Microsoft (%s).' % (
                                                                                           'navigateur de ce poste' if mode == 'pkce' else 'code à saisir sur microsoft.com/devicelogin')),
               'action': None if etat_compte == 'fait' else {'route': 'm56103/microsoft/connexion', 'methode': 'POST', 'capacites': ['courrier', 'agenda', 'fichiers'], 'mode': mode}}]
    for cap, titre in (('courrier', 'Outlook : courrier et brouillons'), ('agenda', 'Outlook : agenda'), ('fichiers', 'OneDrive : fichiers')):
        fait = cap in accordees
        sortie.append({'id': 'microsoft.' + cap, 'titre': titre, 'etat': 'fait' if fait else ('a_refaire' if etat_compte == 'a_refaire' else 'a_faire'),
                       'detail': LIBELLES[cap], 'action': None if fait else {'route': 'm56103/microsoft/connexion', 'methode': 'POST', 'capacites': [cap], 'mode': mode}})
    return sortie

"""5.6.88 : stockages cloud au choix, en plus de Nextcloud (Google Drive, SharePoint ou OneDrive, Dropbox).

**Sélection centrale.** ``DAV(cfg)`` reste le seul point d'entrée du code : ``load_config`` lit ``cloud5688.json`` (dossier d'état, sans
secret) et, s'il existe, ajoute ``stockage_cloud`` aux comptes ``nextcloud`` et ``nextcloud_documents`` ; ``DAV.__new__`` choisit alors la
classe du fournisseur. Sans ce fichier, rien ne change : Nextcloud ou le dossier local, comme avant. Un fichier présent mais illisible ne
ramène jamais à Nextcloud en silence (les écritures partiraient au mauvais endroit) : les accès aux fichiers s'arrêtent avec un code clair.

**Même contrat que ``DAV``.** Chemins logiques ``/racine/...`` contrôlés comme ailleurs (racines autorisées, ``.`` et « secrets » exclus) ;
``etag`` opaque ; ``put_file`` exclusif (``http_412``) ; ``replace_file`` conditionnel (``version_nextcloud_modifiee``) ; ``http_404`` et
``http_409`` aux mêmes endroits, car l'appelant en dépend ; tailles bornées ; reprise bornée sur limitation de débit ; envoi par blocs.
Les agendas ne changent pas : ils restent en CalDAV si l'accès est renseigné, comme pour le dossier local.

**Jetons.** Le mécanisme des connexions (``connexions5652``) reste l'unique détenteur : coffre chiffré, renouvellement sous verrou,
déconnexion. L'application OAuth appartient au cabinet : aucun identifiant n'est écrit dans le code. Une réponse d'un fournisseur ne choisit
jamais un hôte : seuls les hôtes de transfert listés ici sont acceptés (``TRANSFER_HOSTS``), sans redirection.

Aucun de ces fournisseurs n'a été essayé sur un vrai compte : ``aptitude`` rejoue le contrat sur le compte du cabinet avant toute bascule.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .common import HTTP, Stop, clean_path, fold, private_json, release_before_network, under
from .dav import DAV, EXCLUDED_NAMES
from .erreurs5634 import http_error

KEY = 'stockage_cloud'
SELECTION_FILE = 'cloud5688.json'
UNREADABLE = 'selection_illisible'
DEFAULT_BASE = '/AxiorHub'
F = lambda name, label, secret=False, required=True, help='': {'name': name, 'label': label, 'secret': secret, 'required': required, 'help': help}
CAPS = {'stockage.lecture': ('Lire et lister les documents', 'lecture', None), 'stockage.ecriture': ('Écrire les documents du cabinet', 'ecriture', None)}
# Connexions (mêmes fiches que connexions5652.PROVIDERS, tenues à part : elles ne sont jamais proposées comme outils à l'agent).
PROVIDERS = {
    'cloud_google_drive': {
        'label': 'Google Drive', 'family': 'stockage', 'auth': 'Client OAuth du cabinet (code d’autorisation + PKCE)',
        'access': 'https://console.cloud.google.com/apis/credentials', 'docs': 'https://developers.google.com/workspace/drive/api/guides/api-specific-auth',
        'fields': [F('client_id', 'Identifiant client OAuth'), F('client_secret', 'Secret client OAuth', True),
                   F('acces', 'Portée demandée', required=False), F('dossier_id', 'Dossier de départ, facultatif (identifiant Drive)', required=False),
                   F('base', 'Nom du dossier AxiorHub', required=False)],
        'oauth': True, 'caps': CAPS, 'usage': 'Documents du cabinet sur Google Drive.'},
    'cloud_microsoft': {
        'label': 'SharePoint ou OneDrive', 'family': 'stockage', 'auth': 'Application Entra du cabinet (code d’autorisation + PKCE)',
        'access': 'https://learn.microsoft.com/graph/auth-register-app-v2', 'docs': 'https://learn.microsoft.com/graph/api/resources/onedrive',
        'fields': [F('client_id', 'Identifiant de l’application'), F('tenant', 'Annuaire (tenant)', required=False), F('client_secret', 'Secret de l’application, facultatif', True, False),
                   F('site', 'Site SharePoint, facultatif (exemple : valbrune.sharepoint.com:/sites/cabinet)', required=False),
                   F('bibliotheque', 'Bibliothèque SharePoint, facultative', required=False), F('base', 'Nom du dossier AxiorHub', required=False)],
        'oauth': True, 'caps': CAPS, 'usage': 'Documents du cabinet sur OneDrive, ou dans une bibliothèque SharePoint.'},
    'cloud_dropbox': {
        'label': 'Dropbox', 'family': 'stockage', 'auth': 'Application Dropbox du cabinet (code d’autorisation + PKCE)',
        'access': 'https://www.dropbox.com/developers/apps', 'docs': 'https://www.dropbox.com/developers/documentation/http/documentation',
        'fields': [F('client_id', 'Clé de l’application (App key)'), F('client_secret', 'Secret de l’application, facultatif', True, False),
                   F('cloisonnement', 'Demander aussi la gestion des partages (oui ou non)', required=False),
                   F('equipe', 'Espace d’équipe (oui ou non)', required=False), F('base', 'Nom du dossier AxiorHub', required=False)],
        'oauth': True, 'caps': CAPS, 'usage': 'Documents du cabinet sur Dropbox.'},
}
# Adresses des services (remplacées par un serveur local dans les tests). Aucune n'est choisie par une réponse ou par le modèle.
ENDPOINTS = {'google_api': 'https://www.googleapis.com', 'google_oauth': 'https://oauth2.googleapis.com/revoke', 'graph': 'https://graph.microsoft.com/v1.0',
             'dropbox_api': 'https://api.dropboxapi.com', 'dropbox_content': 'https://content.dropboxapi.com'}
# Graph transmet les octets par des adresses à durée limitée, sur des hôtes qui varient selon le compte : suffixes acceptés, le reste est refusé.
TRANSFER_HOSTS = ('.sharepoint.com', '.1drv.com', '.livefilestore.com', '.svc.ms')
MAX_TRIES = 5
MAX_WAIT = 30
TOTAL_WAIT = 90
SLEEP = time.sleep
TRANSIENT = ('delai_http_depasse', 'connexion_http_indisponible')


# ------------------------------------------------------------------------------------------------ sélection
def selection_path(state_dir):
    return Path(str(state_dir)) / SELECTION_FILE


def read_selection(state_dir):
    """Stockage choisi, ou ``None`` (Nextcloud ou dossier local). Fichier présent mais illisible : ``{'provider': UNREADABLE}``, jamais ``None``."""
    path = selection_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError):
        return {'provider': UNREADABLE}
    if not isinstance(data, dict) or data.get('provider') not in PROVIDERS or not data.get('connection'):
        return {'provider': UNREADABLE}
    return data


def apply_selection(c):
    """Appelé par ``load_config`` : point central, seul à décider du stockage. Une clé laissée dans ``config.json`` est sans effet."""
    for section in ('nextcloud', 'nextcloud_documents'):
        if isinstance(c.get(section), dict):
            c[section].pop(KEY, None)
    state = c.get('state_dir')
    if not state:
        return c
    selected = read_selection(state)
    if selected:
        for section in ('nextcloud', 'nextcloud_documents'):
            if isinstance(c.get(section), dict):
                c[section][KEY] = {**selected, 'state_dir': str(state)}
    return c


def actif(c):
    """Vrai si les documents vivent chez un fournisseur cloud : les mentions et liens propres à Nextcloud n'ont alors plus de sens."""
    return any(isinstance(c.get(section), dict) and c[section].get(KEY) for section in ('nextcloud', 'nextcloud_documents'))


def ouvrir_libelle(c):
    return 'Ouvrir le fichier' if actif(c) else 'Ouvrir dans Nextcloud'


def write_selection(state_dir, value):
    private_json(selection_path(state_dir), value)


def clear_selection(state_dir):
    path = selection_path(state_dir)
    if path.exists():
        path.unlink()


def backend_class(cfg):
    """Classe du fournisseur choisi ; appelée par ``DAV.__new__``."""
    provider = str((cfg.get(KEY) or {}).get('provider') or '')
    if provider == 'cloud_google_drive':
        from .stockage_drive5688 import GoogleDriveStore
        return GoogleDriveStore
    if provider == 'cloud_microsoft':
        from .stockage_graph5688 import MicrosoftStore
        return MicrosoftStore
    if provider == 'cloud_dropbox':
        from .stockage_dropbox5688 import DropboxStore
        return DropboxStore
    raise Stop('stockage_cloud_illisible')


def adapter_class(provider):
    """Adaptateur OAuth (test de lecture, jetons, révocation) d'une connexion de stockage."""
    return backend_class({KEY: {'provider': provider}}).adapter


# ------------------------------------------------------------------------------------------------ jetons
class _TokenDesk:
    """Le strict nécessaire pour les fonctions de jetons de ``connexions5652`` : ``DAV(cfg)`` ne reçoit pas le bureau, seulement sa base."""

    def __init__(self, state_dir):
        self.c = {'state_dir': str(state_dir)}
        self.db = sqlite3.connect(str(Path(str(state_dir)) / 'desk.sqlite3'), timeout=10)
        self.db.row_factory = sqlite3.Row

    @staticmethod
    def settings(key, default=None):
        return True if key == 'connexions5653:reparation' else default       # réparation historique : jamais depuis un accès aux fichiers


class TokenSource:
    """Jeton d'accès de la connexion du cabinet, lu au dernier moment ; aucun jeton n'est conservé dans la configuration."""

    def __init__(self, state_dir, connection):
        self.state_dir, self.connection = state_dir, connection
        self.value, self.until = '', 0.0

    def get(self):
        if self.value and time.time() < self.until:
            return self.value
        from . import connexions5652
        desk = _TokenDesk(self.state_dir)
        try:
            token = connexions5652._access_token(desk, self.connection)
        finally:
            desk.db.close()
        if not token or not token.get('access_token'):
            raise Stop('connexion_a_autoriser')
        self.value, self.until = token['access_token'], time.time() + 60
        return self.value

    def expire(self):
        """Refus du fournisseur (401) : le prochain ``get`` passe par le renouvellement, sous verrou."""
        self.value, self.until = '', 0.0
        desk = _TokenDesk(self.state_dir)
        try:
            desk.db.execute("UPDATE connexions5652 SET token_expires='' WHERE id=?", (self.connection,))
            desk.db.commit()
        finally:
            desk.db.close()


# ------------------------------------------------------------------------------------------------ HTTP
class CloudHTTP(HTTP):
    """Mêmes garde-fous que ``HTTP`` (origine fixe, sans redirection ni mandataire, https hors boucle locale), avec le statut et le corps
    des erreurs : un fournisseur explique sa limitation et ses conflits dans le corps de la réponse, que ``HTTP.request`` ne rend pas."""

    def call(self, method, url, data=None, headers=None, limit=1_000_000):
        release_before_network(urllib.parse.urlsplit(url).netloc)
        p = urllib.parse.urlsplit(url)
        if (p.scheme, p.netloc) != self.origin or p.username or p.password or p.fragment:
            raise Stop('origine_http_refusee')
        req = urllib.request.Request(url, data=data, method=method, headers={**self.headers, **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                raw = r.read(limit + 1)
                if len(raw) > limit:
                    raise Stop('reponse_trop_volumineuse')
                return r.status, {k.lower(): v for k, v in r.headers.items()}, raw
        except urllib.error.HTTPError as ex:      # 308 (envoi par blocs Google) et 3xx arrivent ici : aucune redirection n'est suivie
            return ex.code, {k.lower(): v for k, v in ex.headers.items()}, ex.read(64_000)
        except TimeoutError:
            raise Stop('delai_http_depasse') from None
        except urllib.error.URLError as ex:
            raise Stop('delai_http_depasse' if isinstance(ex.reason, TimeoutError) else 'connexion_http_indisponible') from None
        except OSError:
            raise Stop('connexion_http_indisponible') from None


def host_allowed(url):
    """Adresse de transfert donnée par une réponse : https (ou boucle locale de test) et hôte attendu, sinon refus."""
    parts = urllib.parse.urlsplit(str(url or ''))
    host = (parts.hostname or '').lower()
    if parts.username or parts.password or parts.fragment or not host:
        return False
    return any(host.endswith(s) or host == s.lstrip('.') for s in TRANSFER_HOSTS) and (parts.scheme == 'https' or host in ('127.0.0.1', 'localhost'))


def http_date(value):
    """Date ISO d'un fournisseur vers le format WebDAV déjà lu partout dans le code ; vide si absente ou illisible."""
    if not value:
        return ''
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return ''
    return format_datetime(stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc), usegmt=True)


# ------------------------------------------------------------------------------------------------ contrat commun
ILLEGAL_NAME = re.compile(r'["*:<>?\\|]')


class CloudStore(DAV):
    """Base des trois fournisseurs. Les méthodes d'inventaire (``inventory``, ``inventory_page``, ``inventory_step``, ``inventory_large``)
    viennent de ``DAV`` : elles reposent sur ``list_folder`` et se comportent donc à l'identique.

    Un fournisseur écrit : ``_list``, ``_meta``, ``_read``, ``_create``, ``server_tag``, ``_put_tagged``, ``_trash``, ``_mkdir``, ``prepare`` et,
    pour le cloisonnement, ``list_permissions``, ``remove_permission``, ``permission_support``. Il n'existe volontairement aucune méthode qui
    ajoute un accès ou crée un lien de partage."""
    provider = ''
    label = ''
    versioned_read = False          # lecture adressée par version : pas de second contrôle après lecture
    strict_names = False            # noms refusés par le fournisseur : contrôlés avant l'envoi, avec un message clair

    def __init__(self, cfg):
        self.cfg = cfg
        self.cloud = dict(cfg.get(KEY) or {})
        self.generic = False
        self.refused = []
        self.http = None
        self.calendar_home = ''
        self.files = ''
        self.base = clean_path(str(self.cloud.get('base') or DEFAULT_BASE))
        if self.base == '/':
            raise Stop('dossier_base_cloud_invalide')
        self._clients = {}
        self.tokens = TokenSource(self.cloud.get('state_dir', ''), self.cloud.get('connection', ''))
        if cfg.get('url') and cfg.get('username') and (cfg.get('password_file') or cfg.get('auth') == 'app_api'):
            from .exapp5642 import nextcloud_http
            from urllib.parse import quote
            self.http = nextcloud_http(cfg['url'], cfg)     # agendas : CalDAV inchangé
            self.calendar_home = self.http.base + '/remote.php/dav/calendars/' + quote(cfg['username'], safe='') + '/'

    # ----- réseau
    def _client(self, url):
        parts = urllib.parse.urlsplit(url)
        origin = '%s://%s' % (parts.scheme, parts.netloc)
        if origin not in self._clients:
            self._clients[origin] = CloudHTTP(origin, timeout=60)
        return self._clients[origin]

    def _limited(self, status, body):
        """Limitation de débit propre au fournisseur, en plus de 429 et 503."""
        return False

    def _pause(self, status, headers, body, attempt, safe):
        """Secondes à attendre avant de réessayer, ``None`` s'il ne faut pas réessayer. Une écriture non conditionnelle n'est réessayée que sur
        un refus explicite de débit (rien n'a été écrit) ; une erreur 5xx ambiguë peut avoir abouti, elle remonte."""
        limited = status == 429 or self._limited(status, body)
        if not (limited or (status in (500, 502, 503, 504) and safe) or (status == 503 and 'retry-after' in headers)):
            return None
        raw = str(headers.get('retry-after') or '').strip()
        if raw.isdigit():
            return float(raw)
        if raw:
            try:
                return max(0.0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
        return float(min(2 ** attempt, MAX_WAIT))

    def _send(self, method, url, data=None, headers=None, limit=2_000_000, auth=True, safe=None):
        """Un appel, avec reprise bornée : au plus ``MAX_TRIES`` essais et ``TOTAL_WAIT`` secondes d'attente. Un refus 401 provoque un seul
        renouvellement du jeton. Les adresses à durée limitée s'appellent avec ``auth=False`` : le jeton ne part jamais vers elles."""
        safe = method in ('GET', 'HEAD') if safe is None else safe
        waited, refreshed = 0.0, False
        for attempt in range(MAX_TRIES):
            hdr = dict(headers or {})
            if auth:
                hdr['Authorization'] = 'Bearer ' + self.tokens.get()
            try:
                status, answer, body = self._client(url).call(method, url, data, hdr, limit)
            except Stop as ex:
                if safe and str(ex) in TRANSIENT and attempt < MAX_TRIES - 1:
                    SLEEP(min(2 ** attempt, MAX_WAIT))
                    continue
                raise
            if status == 401 and auth and not refreshed:
                refreshed = True
                self.tokens.expire()
                continue
            pause = self._pause(status, answer, body, attempt, safe)
            if pause is None:
                return status, answer, body
            if pause > MAX_WAIT or waited + pause > TOTAL_WAIT or attempt == MAX_TRIES - 1:
                raise Stop('stockage_limite_debit')
            SLEEP(pause)
            waited += pause
        raise Stop('stockage_limite_debit')

    @staticmethod
    def _json(body):
        try:
            return json.loads(body) if body else {}
        except ValueError:
            raise Stop('reponse_fournisseur_invalide') from None

    @staticmethod
    def _fail(status, method, url):
        return http_error(status, method, url.split('?', 1)[0])

    # ----- chemins
    def _check(self, path):
        path = clean_path(path)
        if not any(under(path, root) for root in self.cfg['roots']):
            raise Stop('fichier_hors_racines')
        if any(p.startswith('.') or fold(p) in EXCLUDED_NAMES for p in path.split('/') if p):
            raise Stop('repertoire_exclu')
        return path

    def _remote(self, path):
        return clean_path(self.base + path)

    def _writable(self, path):
        path = self._check(path)
        name = PurePosixPath(path).name
        if self.strict_names and (ILLEGAL_NAME.search(name) or name != name.strip() or name.endswith('.')):
            raise Stop('nom_fichier_refuse_par_le_stockage')
        return path

    def file_url(self, path):
        raise Stop('fonction_nextcloud_seulement')

    def href_path(self, href):
        raise Stop('fonction_nextcloud_seulement')

    # ----- à écrire par fournisseur
    def _list(self, path):
        raise NotImplementedError

    def _meta(self, path):
        """Métadonnées normalisées ; ``Stop('http_404')`` si le chemin n'existe pas."""
        raise NotImplementedError

    def _read(self, meta, limit):
        raise NotImplementedError

    def _create(self, path, data, content_type):
        """Création exclusive ; ``Stop('http_412')`` si le nom existe déjà."""
        raise NotImplementedError

    def server_tag(self, meta):
        """Marque de version comprise par le serveur."""
        raise NotImplementedError

    def _put_tagged(self, meta, data, tag):
        """Remplacement conditionnel, côté serveur ; ``Stop('version_nextcloud_modifiee')`` si la marque n'est plus la bonne."""
        raise NotImplementedError

    def _trash(self, meta):
        raise NotImplementedError

    def _mkdir(self, path):
        """Création d'un dossier sous un parent existant ; ``Stop('http_412')`` s'il existe déjà."""
        raise NotImplementedError

    def prepare(self):
        """Crée le dossier AxiorHub et les racines ; renvoie les réglages à garder dans la sélection (identifiants, sans secret)."""
        raise NotImplementedError

    def permission_support(self):
        return {'possible': False, 'raison': 'Ce fournisseur ne permet pas de répercuter le cloisonnement.'}

    def list_permissions(self, path):
        raise Stop('cloisonnement_non_disponible')

    def remove_permission(self, path, entry):
        raise Stop('cloisonnement_non_disponible')

    def account_identity(self):
        """Adresse du compte relié : jamais retirée, sous peine de perdre l'accès aux dossiers."""
        return ''

    # ----- lecture
    def list_folder(self, path):
        path = self._check(path)
        out = []
        for entry in self._list(path):
            name = str(entry.get('name') or '')
            try:
                if '/' in name:
                    raise Stop('chemin_refuse')
                child = clean_path(path + '/' + name)
            except Stop:
                self.refused = self.refused + [name[-120:]]
                continue
            if name.startswith('.') or fold(name) in EXCLUDED_NAMES:
                continue
            out.append({'path': child, 'directory': bool(entry.get('directory')), 'etag': entry.get('etag', ''), 'modified': entry.get('modified', ''),
                        'created': entry.get('created', ''), 'size': int(entry.get('size') or 0)})
        return sorted(out, key=lambda x: x['path'])

    def file_web_url(self, path):
        """Adresse de consultation réservée aux comptes qui ont déjà accès : aucun lien de partage n'est jamais créé."""
        meta = self._file(path)
        if not meta.get('web'):
            raise Stop('ouverture_en_ligne_indisponible')
        return meta['web']

    def _file(self, path):
        try:
            meta = self._meta(self._check(path))
        except Stop as ex:
            if str(ex) == 'http_404':
                raise Stop('fichier_nextcloud_introuvable') from None
            raise
        if meta.get('directory'):
            raise Stop('chemin_est_un_dossier')
        return meta

    def stat(self, path):
        meta = self._file(path)
        return {'path': clean_path(path), 'etag': meta.get('etag', ''), 'modified': meta.get('modified', ''), 'created': meta.get('created', ''),
                'size': int(meta.get('size') or 0), 'fileid': str(meta.get('id') or '')}

    def _checked_read(self, meta, limit):
        data = self._read(meta, limit)
        if not self.versioned_read and self._meta(meta['path']).get('etag') != meta.get('etag'):
            raise Stop('fichier_modifie_pendant_lecture')
        return data

    def download(self, item):
        limit = self.cfg.get('max_file_bytes', 15_000_000)
        if item.get('size', 0) > limit:
            raise Stop('piece_trop_volumineuse')
        meta = self._meta(self._check(item['path']))
        if meta.get('directory'):
            raise Stop('chemin_est_un_dossier')
        if int(meta.get('size') or 0) > limit:
            raise Stop('piece_trop_volumineuse')
        if item.get('etag') and item['etag'] != meta.get('etag'):
            raise Stop('http_412')
        return self._checked_read(meta, limit)

    def read_archive(self, path, limit):
        meta = self._meta(self._check(path))
        if meta.get('directory'):
            raise Stop('chemin_est_un_dossier')
        if int(meta.get('size') or 0) > int(limit):
            raise Stop('reponse_trop_volumineuse')
        return self._checked_read(meta, int(limit))

    # ----- écriture : jamais hors des racines, jamais de remplacement silencieux
    def _needs_parent(self, path):
        """WebDAV refuse d'écrire sous un dossier absent (409) et l'appelant s'y fie ; Graph et Dropbox créeraient le dossier : contrôle préalable."""
        parent = str(PurePosixPath(path).parent)
        try:
            if not self._meta(parent).get('directory'):
                raise Stop('http_409')
        except Stop as ex:
            if str(ex) == 'http_404':
                raise Stop('http_409') from None
            raise

    def put_file(self, path, data, content_type='application/octet-stream'):
        if not isinstance(data, bytes):
            raise Stop('contenu_fichier_invalide')
        limit = self.cfg.get('max_generated_file_bytes', 20_000_000)
        if not data or len(data) > limit:
            raise Stop('fichier_genere_trop_volumineux')
        path = self._writable(path)
        self._needs_parent(path)
        self._create(path, data, content_type)
        return path

    def put_archive(self, path, data, limit, content_type='application/octet-stream'):
        if not isinstance(data, bytes) or not data:
            raise Stop('contenu_fichier_invalide')
        if len(data) > int(limit):
            raise Stop('archive_trop_volumineuse')
        path = self._writable(path)
        self._needs_parent(path)
        self._create(path, data, content_type)
        return path

    def replace_file(self, path, data, etag):
        if not isinstance(data, bytes):
            raise Stop('contenu_fichier_invalide')
        limit = self.cfg.get('max_generated_file_bytes', 20_000_000)
        if not data or len(data) > limit:
            raise Stop('fichier_genere_trop_volumineux')
        if not etag:
            raise Stop('etag_nextcloud_absent')
        meta = self._file(path)
        if meta.get('etag') != etag:
            raise Stop('version_nextcloud_modifiee')
        self._put_tagged(meta, data, self.server_tag(meta))
        return path

    def trash_file(self, path, etag):
        """Corbeille du fournisseur (restaurable), jamais une suppression définitive, et seulement sur la version attendue."""
        if not etag:
            raise Stop('etag_nextcloud_absent')
        meta = self._file(path)
        if meta.get('etag') != etag:
            raise Stop('version_nextcloud_modifiee')
        self._trash(meta)
        return path

    def create_folder(self, path):
        path = clean_path(path)
        if path in {clean_path(x) for x in self.cfg['roots']}:
            raise Stop('creation_racine_refusee')
        path = self._writable(path)
        self.list_folder(str(PurePosixPath(path).parent))
        self._mkdir(path)
        return path

    def ensure_folder(self, path, boundary):
        path, boundary = clean_path(path), clean_path(boundary)
        if not under(path, boundary):
            raise Stop('destination_hors_dossier')
        current = boundary
        self.list_folder(current)
        for part in PurePosixPath(path).relative_to(PurePosixPath(boundary)).parts:
            current = clean_path(current + '/' + part)
            try:
                self.list_folder(current)
            except Stop as ex:
                if str(ex) != 'http_404':
                    raise
                try:
                    self._mkdir(self._writable(current))
                except Stop as mk:
                    if str(mk) != 'http_412':            # créé entre-temps par un autre travail : le résultat attendu est là
                        raise
        return path

    # ----- agendas : CalDAV seulement, comme pour le dossier local
    def _caldav(self):
        if self.http is None:
            raise Stop('agenda_webdav_non_configure')

    def calendars(self):
        self._caldav()
        return DAV.calendars(self)

    def events(self, *args, **kwargs):
        self._caldav()
        return DAV.events(self, *args, **kwargs)

    def todos(self, *args, **kwargs):
        self._caldav()
        return DAV.todos(self, *args, **kwargs)

    def put_todo(self, *args, **kwargs):
        self._caldav()
        return DAV.put_todo(self, *args, **kwargs)

    def put_event(self, *args, **kwargs):
        self._caldav()
        return DAV.put_event(self, *args, **kwargs)

    def delete_event(self, *args, **kwargs):
        self._caldav()
        return DAV.delete_event(self, *args, **kwargs)

    # ----- verrou local (fournisseur sans création exclusive)
    @contextmanager
    def _name_lock(self, key):
        """Deux travaux du même serveur ne créent pas le même nom en même temps ; un verrou abandonné depuis une minute est repris."""
        folder = Path(str(self.cloud.get('state_dir') or '.')) / 'verrous5688'
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = folder / hashlib.sha256(key.encode()).hexdigest()
        deadline = time.monotonic() + 20
        while True:
            try:
                os.close(os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 60:
                        lock.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise Stop('fichier_verrouille') from None
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                lock.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------------------------------------ contrôle d'aptitude
def aptitude(store, root):
    """Rejoue le contrat sur le compte du cabinet, dans la racine : ce que les faux serveurs des tests ne peuvent pas prouver. La première
    anomalie arrête le contrôle ; le fichier d'essai part ensuite à la corbeille du fournisseur."""
    steps, state = [], {'created': False}
    path = clean_path(root + '/' + 'Contrôle AxiorHub %s.txt' % uuid.uuid4().hex[:12])
    first, second, third = b'axiorhub-essai-1 ' + uuid.uuid4().hex.encode(), b'axiorhub-essai-2 ' + uuid.uuid4().hex.encode(), b'axiorhub-essai-3'

    def step(label, check):
        try:
            ok = bool(check())
            steps.append({'etape': label, 'ok': ok, 'detail': ''})
        except Stop as ex:
            steps.append({'etape': label, 'ok': False, 'detail': str(ex).split(':')[0]})
        return steps[-1]['ok']

    def expect(code, action):
        try:
            action()
        except Stop as ex:
            return str(ex) == code
        return False

    def create():
        store.put_file(path, first, 'text/plain')
        state['created'] = True
        return True

    def replace():
        info = store.stat(path)
        state['etag'], state['tag'] = info['etag'], store.server_tag(store._file(path))
        store.replace_file(path, second, info['etag'])
        return True

    ok = (step('Lister la racine', lambda: isinstance(store.list_folder(root), list)) and
          step('Créer un fichier', create) and
          step('Relire le fichier à l’identique', lambda: store.read_archive(path, 1000) == first) and
          step('Refuser une seconde création du même nom', lambda: expect('http_412', lambda: store.put_file(path, first, 'text/plain'))) and
          step('Remplacer le fichier, version à jour', replace) and
          step('Relire la nouvelle version', lambda: store.read_archive(path, 1000) == second) and
          step('Refuser une version périmée, contrôle d’AxiorHub', lambda: expect('version_nextcloud_modifiee', lambda: store.replace_file(path, third, state['etag']))) and
          step('Refuser une version périmée, contrôle du fournisseur', lambda: expect('version_nextcloud_modifiee', lambda: store._put_tagged(store._file(path), third, state['tag']))) and
          step('Le contenu n’a pas changé', lambda: store.read_archive(path, 1000) == second))
    if state['created']:
        ok = step('Mettre le fichier d’essai à la corbeille', lambda: store.trash_file(path, store.stat(path)['etag'])) and ok
    return {'ok': ok, 'etapes': steps, 'date': datetime.now(timezone.utc).isoformat(timespec='seconds')}

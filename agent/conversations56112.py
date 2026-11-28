"""5.6.112 : « Conversations IA » — l'instance Open WebUI du cabinet affichée dans AxiorHub, avec toutes ses fonctions.

Page ``/conversations-ia`` (menu principal) et onglet « Conversation IA » de la fiche du dossier (il remplace celui de la 5.6.111, dont
les missions restent ouvertes par ``vue=missions-ia``).

- AxiorHub n'embarque pas Open WebUI : il affiche l'instance du cabinet (adresse HTTPS de Paramètres › Connexions › Raccourcis des
  applications, clé ``openwebui``). Ses mises à jour s'appliquent sans nouvelle version d'AxiorHub ; son nom et son logo restent les siens
  (licence d'Open WebUI). Aucun domaine n'est codé dans le logiciel ; une adresse d'exemple vaut « non configuré ».
- Le cadre utilise la SESSION OPEN WEBUI DU NAVIGATEUR : aucun mot de passe ni en-tête d'identité n'est injecté dans le cadre (la
  connexion par en-tête de confiance d'Open WebUI permettrait, mal réglée, de se connecter à la place d'un autre ; elle n'est pas employée).
- Conversation propre au dossier : avec la clé d'API Open WebUI de la personne (saisie par elle, gardée chiffrée dans le coffre
  d'AxiorHub, jamais affichée ni journalisée), le serveur d'AxiorHub retrouve la conversation associée au dossier pour cette personne et
  cette instance (``GET /api/v1/chats/{id}`` ; essayé sur Open WebUI 0.13.5) ou la crée si elle n'existe pas ou plus (``POST /api/v1/chats/new``, titre « Dossier … »,
  rangée dans le dossier Open WebUI associé au dossier s'il y en a un) ; le cadre ouvre ``/c/{id}``. Sans clé, ou si Open WebUI refuse
  ou ne répond pas : nouvelle conversation dont la saisie est préremplie avec la référence du dossier (paramètre ``q`` : le texte est
  placé dans la saisie, rien n'est envoyé au modèle sans le clic de la personne), et la raison est dite.
- Accès au dossier relu à chaque ouverture (``conversation56111.dossier`` : refus neutre). La politique de la page n'est élargie que pour
  cette seule origine : ``frame-src`` et la délégation du micro, du presse-papiers et du plein écran (``web.py``).
- « Tester l'affichage » lit les en-têtes de la première réponse d'Open WebUI (sans identifiant) et donne le réglage à faire s'il refuse
  d'être affiché dans une autre page ; « Ouvrir dans un nouvel onglet » reste toujours disponible.

Routes ``/api440/m56112/conversations/<action>`` (POST, trois rôles, CSRF, no-store) : ``tester``, ``ouvrir`` (``{dossier}``), ``cle``
(``{cle}`` ou ``{supprimer: 1}``).
"""
from html import escape as e
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote, urlsplit

from .common import Stop, matter_display

CHEMIN = '/conversations-ia'
PREFIXE = 'm56112/conversations/'
ECRITURES = ('tester', 'ouvrir', 'cle', 'synchroniser')
ADMIN = ('synchroniser',)
MAX_POST = 20_000
REGLAGE = '/parametres?rubrique=connexions'
ROLES = ('administrateur', 'avocat', 'assistant')
CLE_FORME = re.compile(r'[A-Za-z0-9._\-]{8,512}')
ID_CONVERSATION = re.compile(r'[A-Za-z0-9\-]{8,80}')
DELAI = 12
TABLE = 'openwebui_conversations_v56112'
MESSAGES = {'role_insuffisant': 'Votre rôle ne permet pas cette action.', 'introuvable': 'Dossier introuvable.',
            'openwebui_non_configure': 'L’adresse d’Open WebUI n’est pas réglée (Paramètres › Connexions › Raccourcis des applications).',
            'requete_trop_longue': 'Demande trop longue.', 'cle_invalide': 'Cette clé n’a pas la forme d’une clé d’API Open WebUI.',
            'cle_refusee': 'Open WebUI refuse cette clé : vérifiez-la (Open WebUI › Réglages › Compte › Clés d’API).',
            'openwebui_injoignable': 'Open WebUI ne répond pas : vérifiez son adresse et réessayez.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : rien n’a été ouvert.',
            'destination_non_autorisee': 'Le mode « local strict » n’admet pas encore l’adresse d’Open WebUI : ajoutez-la aux destinations admises '
                                         '(Paramètres › Données et budget de l’IA).',
            'destination_introuvable': 'L’adresse d’Open WebUI ne se résout pas : vérifiez-la.',
            'cle_admin_absente': 'Open WebUI n’est pas encore relié à AxiorHub : lancez sudo axiorhub-mail openwebui sur le serveur.'}
STATUTS = {'role_insuffisant': '403 Forbidden', 'introuvable': '404 Not Found', 'openwebui_non_configure': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'cle_invalide': '400 Bad Request', 'cle_refusee': '409 Conflict',
           'openwebui_injoignable': '503 Service Unavailable', 'controle_acces_indisponible': '503 Service Unavailable',
           'destination_non_autorisee': '403 Forbidden', 'destination_introuvable': '503 Service Unavailable', 'cle_admin_absente': '409 Conflict'}
_VERROUS = {}
_VERROU = threading.Lock()


# ------------------------------------------------------------------------------------------------ adresse de l'instance
def adresse(desk):
    """Adresse HTTPS de l'instance Open WebUI du cabinet, ou ``''`` (non réglée, invalide, ou adresse d'exemple)."""
    from .workstation import _configured_url
    url = _configured_url(desk, 'openwebui')
    p = urlsplit(url)
    hote = (p.hostname or '').lower()
    if p.scheme != 'https' or not hote or hote.endswith(('example.com', 'example.org', 'example.net')):
        return ''
    return url


def origine(url):
    p = urlsplit(url or '')
    return (p.scheme + '://' + p.netloc) if p.scheme == 'https' and p.netloc else ''


def _racine(url):
    """Origine plus sous-chemin éventuel d'installation (Open WebUI servi sous un préfixe), sans barre finale."""
    p = urlsplit(url)
    return origine(url) + p.path.rstrip('/')


def url_saisie(base, dossier):
    """Nouvelle conversation dont la saisie est préremplie (``q`` : rien n'est envoyé sans clic)."""
    return _racine(base) + '/?q=' + quote('Dossier %s : ' % dossier, safe='')


def url_conversation(base, ident):
    return _racine(base) + '/c/' + quote(ident, safe='')


# ------------------------------------------------------------------------------------------------ clé d'API de la personne
def _chemin_cle(desk, owner):
    nom = hashlib.sha256(('openwebui56112:' + str(owner or 'cabinet')).encode('utf-8')).hexdigest()[:40]
    return Path(str(desk.c['state_dir'])) / 'secrets' / 'openwebui56112' / (nom + '.secret')


def cle_api(desk, owner):
    p = _chemin_cle(desk, owner)
    if not p.is_file():
        return ''
    from .common import read_secret
    try:
        valeur = read_secret(p)
    except Stop:
        return ''
    return valeur if CLE_FORME.fullmatch(valeur or '') else ''


class _Garde:
    local_only = False


def _appel(base, cle, methode, chemin, corps=None):
    """Appel HTTPS à l'API d'Open WebUI (certificat vérifié, sans redirection suivie) ; rend ``(statut, json ou None)``."""
    import http.client
    import ssl
    from . import common
    p = urlsplit(base)
    if common.EGRESS_GUARD is not None:   # passerelle des appels sortants (compte des destinations, mode local strict) : refus en Stop
        common.EGRESS_GUARD(_Garde(), methode, origine(base) + p.path.rstrip('/') + chemin.split('?', 1)[0], corps)
    entetes = {'Authorization': 'Bearer ' + cle, 'Accept': 'application/json', 'User-Agent': 'AxiorHub-Pilote'}
    donnees = None
    if corps is not None:
        donnees = json.dumps(corps, ensure_ascii=False).encode('utf-8')
        entetes['Content-Type'] = 'application/json'
    try:
        conn = http.client.HTTPSConnection(p.hostname, p.port or 443, timeout=DELAI, context=ssl.create_default_context())
        try:
            conn.request(methode, p.path.rstrip('/') + chemin, body=donnees, headers=entetes)
            r = conn.getresponse()
            brut = r.read(2_000_000)
            statut = r.status
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ssl.SSLError):
        raise Stop('openwebui_injoignable') from None
    try:
        return statut, json.loads(brut.decode('utf-8')) if brut else None
    except ValueError:
        return statut, None


def _cle_valide(base, cle):
    statut, corps = _appel(base, cle, 'GET', '/api/v1/chats/?page=1')
    return statut == 200 and isinstance(corps, list)


def enregistrer_cle(desk, owner, data):
    """Enregistre (après vérification par Open WebUI) ou supprime la clé d'API de la personne connectée."""
    p = _chemin_cle(desk, owner)
    if str(data.get('supprimer') or '') in ('1', 'true', 'on') or data.get('supprimer') is True:
        if p.is_file():
            p.unlink()
        desk.audit('openwebui56112_cle', {'owner': owner, 'enregistree': False})
        return {'enregistree': False, 'message': 'Clé d’API Open WebUI supprimée. Les conversations déjà créées restent dans Open WebUI.'}
    base = adresse(desk)
    if not base:
        raise Stop('openwebui_non_configure')
    cle = str(data.get('cle') or '').strip()
    if not CLE_FORME.fullmatch(cle):
        raise Stop('cle_invalide')
    if not _cle_valide(base, cle):   # route d'identité refusée aux clés d'API (« A human session is required », 0.13.5) : liste des conversations
        raise Stop('cle_refusee')
    from .vault567 import write
    write(p, cle, limit=512)
    desk.audit('openwebui56112_cle', {'owner': owner, 'enregistree': True})
    return {'enregistree': True, 'message': 'Clé enregistrée : chaque dossier ouvre désormais sa propre conversation dans Open WebUI.'}


# ------------------------------------------------------------------------------------------------ clé en main : administrateur et comptes
COURRIEL = re.compile(r'[^@\s/\\]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,}')
ACCORD = 'openwebui56112:accord'          # {'cles': bool} : transmission des clés des fournisseurs acceptée à l'installation
EMPREINTE = 'openwebui56112:synchronisation'


def _chemin_cle_admin(desk):
    return Path(str(desk.c['state_dir'])) / 'secrets' / 'openwebui56112' / 'administrateur.secret'


def cle_admin(desk):
    p = _chemin_cle_admin(desk)
    if not p.is_file():
        return ''
    from .common import read_secret
    try:
        valeur = read_secret(p)
    except Stop:
        return ''
    return valeur if CLE_FORME.fullmatch(valeur or '') else ''


def enregistrer_cle_admin(desk, cle, accord_cles=False, inscription_fermee=False):
    """Clé d'API d'un administrateur d'Open WebUI, gardée chiffrée par AxiorHub (posée par ``install-openwebui.py`` ou
    ``configure-openwebui.py``, via ``manage.py openwebui-admin``) : création des comptes et synchronisation des réglages."""
    base = adresse(desk)
    if not base:
        raise Stop('openwebui_non_configure')
    cle = str(cle or '').strip()
    if not CLE_FORME.fullmatch(cle):
        raise Stop('cle_invalide')
    statut, corps = _appel(base, cle, 'GET', '/openai/config')   # réservé aux administrateurs d'Open WebUI
    if statut != 200 or not isinstance(corps, dict):
        raise Stop('cle_refusee')
    from .vault567 import write
    write(_chemin_cle_admin(desk), cle, limit=512)
    desk.setting(ACCORD, {'cles': bool(accord_cles), 'inscription_fermee': bool(inscription_fermee)})
    desk.audit('openwebui56112_cle_admin', {'enregistree': True, 'accord_cles': bool(accord_cles)})
    return {'enregistree': True}


def compte_automatique(desk, owner, base):
    """Compte Open WebUI de la personne, à la même adresse que dans AxiorHub, créé avec la clé d'administrateur ; sa clé d'API est créée
    avec la session rendue à la création (``/api/v1/auths/add``, essayé sur la 0.13.5) puis gardée chiffrée pour elle. Rend
    ``{cle, identifiant, mot_de_passe}``, ``{existe: True}`` (compte déjà là : sa clé ne peut venir que de la personne) ou ``None``."""
    admin = cle_admin(desk)
    courriel = str(owner or '')
    if not admin or not COURRIEL.fullmatch(courriel):
        return None
    statut, liste = _appel(base, admin, 'GET', '/api/v1/users/?query=' + quote(courriel, safe=''))
    if statut != 200 or not isinstance(liste, dict):
        return None
    if any(str(u.get('email') or '').lower() == courriel.lower() for u in (liste.get('users') or []) if isinstance(u, dict)):
        return {'existe': True}
    import secrets as _secrets
    mot = _secrets.token_urlsafe(12)
    nom = courriel.split('@', 1)[0]
    try:
        from .membres5650 import directory
        nom = str((directory(desk).get(courriel) or {}).get('name') or nom)
    except Exception:   # noqa: BLE001 — annuaire illisible : la partie locale de l'adresse
        pass
    statut, cree = _appel(base, admin, 'POST', '/api/v1/auths/add', {'name': nom[:100], 'email': courriel, 'password': mot, 'role': 'user'})
    jeton = str((cree or {}).get('token') or '') if isinstance(cree, dict) else ''
    if statut != 200 or not jeton:
        return None
    statut, k = _appel(base, jeton, 'POST', '/api/v1/auths/api_key')
    cle = str((k or {}).get('api_key') or '') if isinstance(k, dict) else ''
    if statut != 200 or not CLE_FORME.fullmatch(cle):
        return None
    from .vault567 import write
    write(_chemin_cle(desk, owner), cle, limit=512)
    desk.audit('openwebui56112_compte_cree', {'owner': owner})
    return {'cle': cle, 'identifiant': courriel, 'mot_de_passe': mot}


def entrees_service(desk):
    """Réglages d'AxiorHub à reporter dans Open WebUI, lus par le service (l'outil et son jeton restent l'affaire de root)."""
    from . import ai_gateway, dictee56110, extensions364
    from . import openwebui_reglage56112 as R
    from .common import read_secret
    cfg = json.loads(json.dumps(desk.c))
    ai_gateway.load_config_settings(cfg)
    extensions364.load_config_settings(cfg)
    accord = bool((desk.settings(ACCORD, {}) or {}).get('cles'))

    def secret(path):
        try:
            return read_secret(path) if (accord and path) else ''
        except (Stop, OSError):
            return ''
    fournisseurs = R.fournisseurs_compatibles(ai_gateway.provider_registry(cfg))
    connecteurs, ecartes = R.connecteurs_mcp(cfg.get('lawve_extensions') or {})
    route = (cfg.get('model_routing') or {}).get('assistant') or {}
    tts, stt, notes = R.voix_axiorhub(cfg, dictee56110.reglages_de(cfg.get('state_dir')), 1.0,
                                      secret(((cfg.get('speech568') or {}).get('api_key_file')) or ''))
    racine = Path(__file__).resolve().parents[1]
    return {'config': cfg, 'systeme': (racine / 'integrations' / 'openwebui' / 'SYSTEM-PROMPT-AXIORHUB.md').read_text(encoding='utf-8'),
            'fournisseurs': fournisseurs, 'cles_fournisseurs': {f['id']: v for f in fournisseurs if (v := secret(f['secret_file']))},
            'ollama_url': R.vu_depuis_conteneur(str((cfg.get('ollama') or {}).get('url') or '')),
            'connecteurs': connecteurs, 'ecartes_mcp': ecartes,
            'cles_mcp': {c['id']: v for c in connecteurs if c['auth'] == 'bearer' and (v := secret(c['secret_file']))},
            'competences': R.competences_axiorhub(cfg.get('lawve_extensions') or {}),
            'modele_prefere': str(route.get('model') or '') if isinstance(route, dict) else '', 'tts': tts, 'stt': stt, 'notes_voix': notes,
            'fermer_inscription': bool((desk.settings(ACCORD, {}) or {}).get('inscription_fermee'))}


def _empreinte(e):
    """Empreinte de ce qui est reporté (sans aucune clé : seulement leur présence)."""
    vue = {k: v for k, v in e.items() if k not in ('config', 'cles_fournisseurs', 'cles_mcp')}
    vue['cles'] = sorted(list(e.get('cles_fournisseurs') or {}) + list(e.get('cles_mcp') or {}))
    vue['tts'] = {k: v for k, v in (e.get('tts') or {}).items() if k != 'API_KEY'}
    from . import openwebui_reglage56112 as R
    cfg = e.get('config') or {}
    vue['parametres'] = {m: R.parametres_axiorhub(cfg, g) for m, g in sorted(R.modeles_axiorhub(cfg).items())}
    return hashlib.sha256(json.dumps(vue, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')).hexdigest()


class _ClientService:
    """Client du réglage (``openwebui_reglage56112``) passant par ``_appel`` : passerelle des sorties, HTTPS vérifié."""

    def __init__(self, base, cle):
        self.base, self.cle = base, cle

    def appel(self, methode, chemin, corps=None):
        try:
            return _appel(self.base, self.cle, methode, chemin, corps)
        except Stop as ex:
            from .openwebui_reglage56112 import ErreurOpenWebUI
            raise ErreurOpenWebUI(str(ex).split(':')[0]) from None

    def exiger(self, methode, chemin, corps=None):
        from .openwebui_reglage56112 import Client
        return Client.exiger(self, methode, chemin, corps)


def synchroniser(desk, journal=None):
    """Reporte dans Open WebUI les réglages d'AxiorHub (toutes les étapes sauf l'outil) ; rend les résultats et garde l'empreinte."""
    base, admin = adresse(desk), cle_admin(desk)
    if not base:
        raise Stop('openwebui_non_configure')
    if not admin:
        raise Stop('cle_admin_absente')
    from . import openwebui_reglage56112 as R
    e = entrees_service(desk)
    try:
        resultats = R.regler(_ClientService(base, admin), e, tuple(x for x in R.ETAPES if x != 'outil'), journal=journal)
    except R.ErreurOpenWebUI as ex:
        raise Stop({'cle_admin_refusee': 'cle_refusee'}.get(str(ex), 'openwebui_injoignable')) from None
    desk.setting(EMPREINTE, {'empreinte': _empreinte(e), 'at': desk.now(), 'echecs': [r['etape'] for r in resultats if r['etat'] == 'echec']})
    desk.audit('openwebui56112_synchronisation', {'etapes': {r['etape']: r['etat'] for r in resultats}})
    return resultats


_SYNCHRO = {'en_cours': False}


def synchroniser_si_change(desk, delai=600):
    """Réglages d'AxiorHub modifiés depuis la dernière synchronisation (et plus de ``delai`` secondes) : synchronisation en arrière-plan.
    Appelée à l'affichage de « Conversations IA » et des Paramètres : aucune attente pour la page, aucune erreur rendue."""
    if _SYNCHRO['en_cours'] or not adresse(desk) or not cle_admin(desk):
        return False
    try:
        etat = desk.settings(EMPREINTE, {}) or {}
        from datetime import datetime, timezone
        if etat.get('at'):
            ecoule = (datetime.now(timezone.utc) - datetime.fromisoformat(str(etat['at']))).total_seconds()
            if ecoule < delai:
                return False
        if etat.get('empreinte') == _empreinte(entrees_service(desk)) and not etat.get('echecs'):
            return False
    except Exception:   # noqa: BLE001 — lecture impossible : rien n'est lancé
        return False
    config = json.loads(json.dumps(desk.c))
    _SYNCHRO['en_cours'] = True

    def fil():
        from .desk import Desk
        d = None
        try:
            d = Desk(config)
            synchroniser(d)
        except Exception:   # noqa: BLE001 — refus ou panne : rejoué au prochain affichage, rien n'est perdu
            pass
        finally:
            _SYNCHRO['en_cours'] = False
            if d is not None:
                d.db.close()
    threading.Thread(target=fil, daemon=True, name='openwebui56112-synchro').start()
    return True


# ------------------------------------------------------------------------------------------------ conversation propre au dossier
def ensure_schema(desk):
    desk.db.execute('CREATE TABLE IF NOT EXISTS %s(owner TEXT NOT NULL, matter TEXT NOT NULL, origin TEXT NOT NULL, chat_id TEXT NOT NULL, '
                    'created TEXT NOT NULL, PRIMARY KEY(owner, matter, origin))' % TABLE)


def _verrou(cle):
    with _VERROU:
        return _VERROUS.setdefault(cle, threading.Lock())


def _dossier_open_webui(desk, mid, base):
    """Identifiant du dossier Open WebUI associé au dossier (même instance), ou ``None``."""
    from .workstation import workspace_mapping
    try:
        lien = workspace_mapping(desk, mid)
    except Exception:   # noqa: BLE001 — association illisible : la conversation est créée hors dossier
        return None
    if lien and origine(lien.get('folder_url')) == origine(base) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', str(lien.get('folder_id') or '')):
        return str(lien['folder_id'])
    return None


def _creer(desk, base, cle, m):
    titre = ('Dossier ' + matter_display(m))[:200]
    conversation = {'title': titre, 'models': [], 'messages': [], 'history': {'messages': {}, 'currentId': None}, 'tags': [],
                    'params': {}, 'files': [], 'timestamp': int(time.time() * 1000)}
    dossier = _dossier_open_webui(desk, str(m['id']), base)
    statut, corps = _appel(base, cle, 'POST', '/api/v1/chats/new', {'chat': conversation, 'folder_id': dossier})
    if dossier and statut == 404:   # dossier Open WebUI supprimé ou non partagé : conversation créée hors dossier
        statut, corps = _appel(base, cle, 'POST', '/api/v1/chats/new', {'chat': conversation, 'folder_id': None})
    if statut in (401, 403):
        raise Stop('cle_refusee')
    ident = str((corps or {}).get('id') or '') if isinstance(corps, dict) else ''
    if statut >= 400 or not ID_CONVERSATION.fullmatch(ident):
        raise Stop('openwebui_injoignable')
    return ident


def ouvrir(desk, owner, m):
    """Adresse à ouvrir dans le cadre pour le dossier ``m`` : sa conversation (retrouvée ou créée), sinon une saisie préremplie."""
    base = adresse(desk)
    if not base:
        raise Stop('openwebui_non_configure')
    mid = str(m['id'])
    saisie = url_saisie(base, mid)
    cle = cle_api(desk, owner)
    ensure_schema(desk)
    o = origine(base)
    with _verrou((owner, mid, o)):
        compte = None
        if not cle:   # clé en main : compte Open WebUI de la personne créé par AxiorHub (clé d'administrateur gardée par AxiorHub)
            try:
                auto = compte_automatique(desk, owner, base)
            except Stop:
                auto = None
            if auto and auto.get('cle'):
                cle, compte = auto['cle'], {'identifiant': auto['identifiant'], 'mot_de_passe': auto['mot_de_passe']}
            else:
                existe = bool(auto and auto.get('existe'))
                return {'mode': 'saisie', 'url': saisie, 'cle': False,
                        'message': ('Un compte Open WebUI existe déjà à votre adresse : enregistrez sa clé d’API ci-dessous pour retrouver la '
                                    'conversation propre à chaque dossier.' if existe else
                                    'Nouvelle conversation, saisie préremplie avec la référence du dossier. Pour retrouver automatiquement la '
                                    'conversation propre à ce dossier, enregistrez votre clé d’API Open WebUI ci-dessous.')}
        row = desk.db.execute('SELECT chat_id FROM %s WHERE owner=? AND matter=? AND origin=?' % TABLE, (owner, mid, o)).fetchone()
        try:
            if row:
                statut, _ = _appel(base, cle, 'GET', '/api/v1/chats/' + quote(row['chat_id'], safe=''))
                if statut == 200:
                    return {'mode': 'conversation', 'url': url_conversation(base, row['chat_id']), 'cle': True, 'cree': False,
                            'message': 'Conversation du dossier ouverte.'}
                # Open WebUI 0.13.5 répond 401 pour une conversation supprimée comme pour une clé refusée : la clé est relue à part
                if statut in (401, 403) and not _cle_valide(base, cle):
                    raise Stop('cle_refusee')
                if statut not in (401, 403, 404):
                    raise Stop('openwebui_injoignable')
                desk.db.execute('DELETE FROM %s WHERE owner=? AND matter=? AND origin=?' % TABLE, (owner, mid, o))
                desk.db.commit()
            ident = _creer(desk, base, cle, m)
        except Stop as ex:
            code = str(ex).split(':')[0]
            out = {'mode': 'saisie', 'url': saisie, 'cle': True, 'erreur': code,
                   'message': MESSAGES.get(code, code) + ' Nouvelle conversation ouverte, saisie préremplie avec la référence du dossier.'}
            if compte:   # compte créé juste avant : son mot de passe initial n'est jamais perdu
                out['compte'] = compte
                out['message'] += (' Votre compte Open WebUI vient d’être créé : identifiant %s, mot de passe initial %s (à changer dans '
                                   'Open WebUI, il ne sera plus affiché).' % (compte['identifiant'], compte['mot_de_passe']))
            return out
        desk.db.execute('INSERT OR REPLACE INTO %s VALUES(?,?,?,?,?)' % TABLE, (owner, mid, o, ident, desk.now()))
        desk.db.commit()
        desk.audit('openwebui56112_conversation_creee', {'owner': owner, 'matter': mid})
        out = {'mode': 'conversation', 'url': url_conversation(base, ident), 'cle': True, 'cree': True,
               'message': 'Conversation du dossier créée dans Open WebUI.'}
        if compte:   # rendu une seule fois, à cette personne seulement ; rien n'est gardé
            out['compte'] = compte
            out['message'] = ('Votre compte Open WebUI vient d’être créé : identifiant %s, mot de passe initial %s. Connectez-vous dans le '
                              'cadre, puis changez ce mot de passe dans Open WebUI (Réglages › Compte). Il ne sera plus affiché.'
                              % (compte['identifiant'], compte['mot_de_passe']))
        return out


# ------------------------------------------------------------------------------------------------ présentation
def _role():
    from .i18n5631 import role
    return role()


def _barre(prefix, titre, url, niveau='h1', extra=''):
    return ('<div class="cv112-barre"><%s>%s</%s>' % (niveau, e(titre), niveau) +
            '<a class="ax-btn ghost" data-cv112-onglet href="' + e(url, quote=True) + '" target="_blank" rel="noopener noreferrer">Ouvrir dans un nouvel onglet</a>'
            '<form class="m5-form cv112-test" data-api="' + PREFIXE + 'tester"><button class="ax-btn ghost" type="submit">Tester l’affichage</button></form>'
            + extra + (('<a class="ax-btn ghost" href="' + e(prefix + REGLAGE, quote=True) + '">Réglages</a>') if _role() == 'administrateur' else '') + '</div>')


def _ou_regler(prefix):
    if _role() == 'administrateur':
        return ('Indiquez-la dans <a href="' + e(prefix + REGLAGE, quote=True) + '">Paramètres › Connexions › Raccourcis des applications</a> '
                '(ligne « Open WebUI », adresse HTTPS).')
    return 'Demandez à l’administrateur du cabinet de la régler (Paramètres › Connexions › Raccourcis des applications).'


def _cadre(url):
    return ('<iframe class="cv112-cadre" data-cv112-cadre title="Open WebUI — conversations IA du cabinet"' +
            (' src="' + e(url, quote=True) + '"' if url else '') +
            ' allow="microphone; clipboard-read; clipboard-write; fullscreen" referrerpolicy="strict-origin-when-cross-origin"></iframe>')


def _carte_cle(desk, owner):
    presente = bool(cle_api(desk, owner))
    etat = ('Votre clé d’API Open WebUI est enregistrée : chaque dossier ouvre sa propre conversation.' if presente else
            'Sans clé, chaque ouverture propose une nouvelle conversation préremplie avec la référence du dossier.')
    formulaire = ('<form class="m5-form" data-api="' + PREFIXE + 'cle" data-reload autocomplete="off">'
                  '<label>Clé d’API Open WebUI<input type="password" name="cle" maxlength="512" autocomplete="off" spellcheck="false" required></label>'
                  '<button class="ax-btn" type="submit">' + ('Remplacer la clé' if presente else 'Enregistrer la clé') + '</button></form>')
    if presente:
        formulaire += ('<form class="m5-form" data-api="' + PREFIXE + 'cle" data-reload data-confirm="Supprimer votre clé d’API Open WebUI ?">'
                       '<input type="hidden" name="supprimer" value="1"><button class="ax-btn ghost" type="submit">Supprimer la clé</button></form>')
    return ('<details class="cv112-cle"' + ('' if presente else ' open') + '><summary>Conversation propre à chaque dossier</summary><p>' + e(etat) +
            '</p><p class="cv112-aide">Créez la clé dans Open WebUI (Réglages › Compte › Clés d’API ; l’administrateur d’Open WebUI doit '
            'avoir autorisé les clés d’API). Elle est vérifiée auprès d’Open WebUI, gardée chiffrée sur le serveur d’AxiorHub, jamais affichée, '
            'et sert seulement à retrouver ou créer la conversation de chaque dossier.</p>' + formulaire + '</details>')


def page_html(desk, auth, prefix, env, args):
    """Page /conversations-ia : l'instance avec l'historique de tous les dossiers ; ``?dossier=`` ouvre la conversation de ce dossier."""
    from .web567 import actor
    owner = actor(env)[0]
    base = adresse(desk)
    if not base:
        return ('<section class="cv112" data-cv112><h1>Conversations IA</h1><div class="cv112-vide"><p>Cette page affiche l’instance '
                '<strong>Open WebUI</strong> du cabinet, avec ses conversations de tous les dossiers, ses modèles et ses outils.</p>'
                '<p>Son adresse n’est pas encore réglée. ' + _ou_regler(prefix) + '</p></div></section>')
    try:
        synchroniser_si_change(desk)   # réglages d'AxiorHub changés : reportés en arrière-plan (clé en main)
    except Exception:   # noqa: BLE001
        pass
    mid = str(args.get('dossier') or '')[:200]
    if mid:
        try:
            from .conversation56111 import dossier as _dossier
            _dossier(desk, actor(env), mid)
        except Stop:
            mid = ''
    if mid:
        return ('<section class="cv112" data-cv112 data-cv112-dossier="' + e(mid, quote=True) + '">' + _barre(prefix, 'Conversations IA', base) +
                '<p class="cv112-dossier-etat" role="status" data-cv112-etat>Ouverture de la conversation du dossier ' + e(mid) + '…</p>' + _cadre('') +
                _carte_cle(desk, owner) + '</section>')
    return ('<section class="cv112" data-cv112>' + _barre(prefix, 'Conversations IA', base) + _cadre(base) +
            '<p class="cv112-aide">Open WebUI s’affiche avec votre session Open WebUI dans ce navigateur : connectez-vous une fois dans le cadre. '
            'S’il reste vide, cliquez sur « Tester l’affichage ».</p>' + _carte_cle(desk, owner) + '</section>')


def page_dossier(desk, auth, prefix, args, shell, m, env=None):
    """Onglet « Conversation IA » de la fiche : Open WebUI dans un cadre, sur la conversation propre au dossier (ouverte ou créée par
    ``v56112-conversations.js`` après l'affichage : la page ne dépend jamais du temps de réponse d'Open WebUI)."""
    from . import dossier5679 as P
    if env:
        from .web567 import actor
        owner = actor(env)[0]
    else:   # personne fixée au début de la requête (i18n5631.begin, avec web567.actor) : même valeur que les routes
        from .i18n5631 import _ctx
        owner = getattr(_ctx, 'owner', '') or 'cabinet'
    body = P.header_html(desk, prefix, m, 'conversation')
    base = adresse(desk)
    missions = ('<a class="ax-btn ghost" href="' + e(prefix + '/dossier?id=' + quote(str(m['id']), safe='') + '&vue=missions-ia', quote=True) +
                '">Missions AxiorHub du dossier</a>')
    if not base:
        contenu = ('<section class="cv112 cv112-dossier"><div class="cv112-vide"><p>La conversation du dossier s’ouvre dans l’instance '
                   '<strong>Open WebUI</strong> du cabinet, dont l’adresse n’est pas encore réglée. ' + _ou_regler(prefix) + '</p><p>' + missions + '</p></div></section>')
    else:
        contenu = ('<section class="cv112 cv112-dossier" data-cv112 data-cv112-dossier="' + e(str(m['id']), quote=True) + '">' +
                   _barre(prefix, 'Conversation IA du dossier', url_saisie(base, str(m['id'])), 'h2', missions) +
                   '<p class="cv112-dossier-etat" role="status" data-cv112-etat>Ouverture de la conversation du dossier…</p>' + _cadre('') +
                   _carte_cle(desk, owner) + '</section>')
    body += '<div class="cv112-page">%s</div></div>' % contenu
    head = ''.join('<script defer src="%s/static/%s"></script>' % (e(prefix), n) for n in ('v5627.js', 'v5679.js', 'v56112-conversations.js'))
    return shell(matter_display(m), body, prefix, auth['csrf'], '/dossiers', head)


def carte_admin_html(desk, prefix):
    """Paramètres › Connexions (administrateur) : où en est Open WebUI et les deux commandes qui l'installent et le règlent."""
    base = adresse(desk)
    etat = (('Adresse réglée : <a href="' + e(base, quote=True) + '" target="_blank" rel="noopener noreferrer">' + e(base) + '</a> · '
             '<a href="' + e(prefix + CHEMIN, quote=True) + '">Ouvrir « Conversations IA »</a>') if base else
            'Adresse d’Open WebUI pas encore réglée (ligne « Open WebUI » ci-dessus, ou réglée par la commande de réglage).')
    return ('<section class="cv112-admin" id="cv112-openwebui"><h3>Open WebUI du cabinet</h3><p>' + etat + '</p>'
            '<p>Sur le serveur AxiorHub, en administrateur :</p><ol>'
            '<li><strong>Installer</strong> Open WebUI (Docker, version figée, publication HTTPS, affichage dans AxiorHub autorisé) : '
            '<code>sudo python3 /opt/axiorhub-mail-agent/current/install-openwebui.py --nom ia.&lt;votre-domaine&gt;</code> '
            '(inutile si Open WebUI est déjà en place ; <code>--mettre-a-jour</code> pour la version livrée avec cette mise à jour).</li>'
            '<li><strong>Régler</strong> Open WebUI d’après AxiorHub (outil AxiorHub, connexions IA, connecteurs MCP, compétences approuvées, '
            'modèle « AxiorHub Avocat », invites), avec une clé d’API d’administrateur d’Open WebUI : <code>sudo axiorhub-mail openwebui</code>. '
            'Les clés des fournisseurs ne sont transmises qu’après accord ; relancer met à jour sans doublon.</li></ol>'
            + (('<form class="m5-form cv112-test" data-api="' + PREFIXE + 'tester"><button class="ax-btn ghost" type="submit">Tester l’affichage dans AxiorHub</button></form>')
               if base else '') + _etat_synchro(desk) + '</section>')


def _etat_synchro(desk):
    """Relié (clé d'administrateur gardée) : dernière synchronisation, bouton, et synchronisation automatique si les réglages ont changé."""
    if not adresse(desk):
        return ''
    if not cle_admin(desk):
        return '<p class="cv112-aide">Pas encore relié à AxiorHub : la commande de réglage ci-dessus le fait (une fois).</p>'
    synchroniser_si_change(desk)
    etat = desk.settings(EMPREINTE, {}) or {}
    quand = str(etat.get('at') or '')[:16].replace('T', ' ')
    return ('<p class="cv112-aide">Relié à AxiorHub : comptes créés pour chacun, réglages (connexions IA, paramètres des modèles, voix, '
            'compétences, MCP) reportés automatiquement quand ils changent dans AxiorHub' + ((' ; dernière synchronisation : ' + e(quand) + ' (UTC)') if quand else '') +
            '.</p><form class="m5-form cv112-test" data-api="' + PREFIXE + 'synchroniser"><button class="ax-btn ghost" type="submit">Synchroniser maintenant</button></form>')


def _reglage_conseille(origine_axiorhub):
    return 'CONTENT_SECURITY_POLICY="frame-ancestors \'self\' %s"' % (origine_axiorhub or 'https://<adresse d’AxiorHub>')


def tester(desk, env=None):
    """Diagnostic : en-têtes de la première réponse d'Open WebUI (souvent la page de connexion), sans identifiant ; rien n'est créé."""
    from .talk5643 import axiorhub_origin, framing_verdict, premiere_reponse
    url = adresse(desk)
    if not url:
        raise Stop('openwebui_non_configure')
    items, statut = premiere_reponse(url)
    moi = axiorhub_origin(env)
    verdict = framing_verdict(items, origine(url), moi)
    conseil = _reglage_conseille(moi)
    if statut != 'joignable':
        message = 'Open WebUI n’a pas répondu (%s) : vérifiez son adresse dans Paramètres › Connexions.' % statut
    elif verdict['blocked']:
        regle = ('X-Frame-Options: %s' % verdict['x_frame_options']) if verdict['rule'] == 'x-frame-options' else verdict['refusing']
        message = ('Open WebUI refuse d’être affiché dans AxiorHub (%s). Dans la configuration d’Open WebUI (variables d’environnement), '
                   'ajoutez %s puis redémarrez-le ; la directive frame-ancestors l’emporte sur X-Frame-Options. En attendant, utilisez '
                   '« Ouvrir dans un nouvel onglet ».' % (regle, conseil))
    elif verdict['rule'] == 'frame-ancestors':
        message = 'Affichage autorisé : la politique d’Open WebUI accepte %s.' % (moi or 'cette adresse')
    else:
        message = ('Aucune interdiction détectée. Si le cadre reste vide, connectez-vous à Open WebUI dans le cadre ; pour une règle explicite, '
                   'ajoutez %s à sa configuration.' % conseil)
    return {'statut': statut, 'bloque': bool(verdict['blocked']) and statut == 'joignable', 'regle': verdict['rule'],
            'x_frame_options': verdict['x_frame_options'], 'frame_ancestors': verdict['frame_ancestors'], 'origine': moi,
            'conseil': conseil, 'message': message, 'noreload': True}


# ------------------------------------------------------------------------------------------------ routes /api440/m56112/conversations/*
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if reste in ADMIN:
        return method == 'POST' and role == 'administrateur'
    return method == 'POST' and reste in ECRITURES and role in ROLES


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste not in ECRITURES or method != 'POST':
        return _json({'error': 'introuvable', 'message': 'Action inconnue.'}, STATUTS['introuvable'])
    if not allowed(role, method, PREFIXE + reste):
        return _json({'error': 'role_insuffisant', 'message': MESSAGES['role_insuffisant']}, STATUTS['role_insuffisant'])
    try:
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth) or {}
        if reste == 'tester':
            return _json(tester(desk, env))
        if reste == 'cle':
            return _json(enregistrer_cle(desk, owner, data))
        if reste == 'synchroniser':
            res = synchroniser(desk)
            echecs = [r for r in res if r['etat'] == 'echec']
            return _json({'resultats': res, 'noreload': True, 'message': ('Open WebUI synchronisé avec AxiorHub.' if not echecs else
                          'Synchronisation partielle : ' + ' ; '.join(r['detail'] for r in echecs))})
        from .conversation56111 import dossier as _dossier
        _acteur, m = _dossier(desk, (owner, role), str(data.get('dossier') or ''))
        return _json(ouvrir(desk, owner, m))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': MESSAGES.get(code, code.replace('_', ' '))}, STATUTS.get(code, '400 Bad Request'))

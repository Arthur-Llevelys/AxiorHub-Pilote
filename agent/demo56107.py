"""5.6.107 (lot M) : mode Démo — un cabinet fictif prérempli, sans compte ni modèle à configurer, sans aucun effet externe.

API publique (stable, utilisée par le lot H « démo hébergée » et le lot V « vidéo ») — tout fonctionne sans interface graphique :

    from agent import demo56107
    config = demo56107.creer(dossier_vide, graine=None, origine='http://127.0.0.1:8779', prefixe='/agent-courriel', langue='fr')
        # crée une instance complète dans un dossier vide (ou absent) et rend le chemin de son ``config.json``.
        # ``langue`` : 'fr', 'en', 'de' ou 'es' (``LANGUES``) — données du pays (cabinet, dossiers, courriels, réponses de l'IA) et langue
        # de l'interface de l'instance ; ``Stop('demo_langue_inconnue')`` sinon. La langue est gardée par ``reinitialiser``.
        # Refus (``Stop('demo_dossier_non_vide')`` / ``Stop('demo_sur_installation_reelle')``) si le dossier n'est pas vide, s'il est
        # dans une installation réelle ou s'il est le dossier de données du poste réel.
    demo56107.reinitialiser(chemin, langue=None)   # chemin = config.json ou dossier de l'instance : données remises à neuf, dates
                                           # recalculées ; même langue, sauf ``langue`` explicite (le poste change ainsi de langue)
    demo56107.langue_de(chemin)            # langue de l'instance ('fr' pour une démo antérieure à la 5.6.107 multilingue)
    demo56107.actif(desk_ou_config)        # vrai seulement pour une configuration de démo complète, isolée, marquée sur le disque
    demo56107.jeton_de_session(chemin, suite='/aujourdhui')
        # chemin relatif au préfixe : '/poste-session?jeton=…&suite=…' ; à ouvrir dans le navigateur du visiteur (session de
        # « Me Claire DURAND », administratrice, cookie HttpOnly ; jeton à usage unique valable deux minutes)
    demo56107.commandes(chemin, port)      # {'interface': [argv], 'travailleur': [argv], 'env': {...}} : processus de service à lancer
                                           # (interface ``web.py serve`` et travailleur ``web.py worker`` ; jamais la veille IMAP)
    demo56107.preparer_lancement(chemin)   # remise à neuf si la démo a été créée un autre jour (dates « d'aujourd'hui »)
    demo56107.quitter_demande(chemin)      # vrai après « Quitter » dans le bandeau (le lanceur ou le répartiteur arrête l'instance)

Instance (un dossier) : ``config.json``, ``ui-auth.json`` (mode poste, jeton de lancement), ``mot-de-passe-local``, ``matters.json``,
``state/`` (bases SQLite), ``stockage/`` (fichiers des dossiers, ``nextcloud.local_path``), ``boite/`` (boîte de démonstration,
``demo56107_imap``), ``auth/`` (comptes), ``secrets/`` (valeurs fictives), ``DEMO-AXIORHUB.json`` (marqueur : jour de création, graine).
Les processus de service doivent recevoir ``AXIORHUB_DATA_DIR`` et ``AXIORHUB_AUTH_STATE`` (fournis par ``commandes``).

Garde global (``armer``) : dans un processus qui ouvre un ``Desk`` sur une configuration de démo, toute connexion sortante est refusée
au niveau des sockets (résolution de nom et ``connect``, donc SMTP, IMAP, ``common.HTTP``, ``CloudHTTP``, ``http.client``…), ``common.HTTP``
lève ``demonstration_hors_ligne`` sauf pour l'IA de démonstration (réponses préparées à la place du moteur local), la messagerie passe par la
boîte locale et l'envoi SMTP est remplacé par un dépôt dans « Envoyés (démo) ». Pendant ``creer``/``reinitialiser`` (fil appelant
seulement) le même garde s'applique, sans toucher au reste du processus appelant (répartiteur du lot H).

Données : ``agent/demo56107_donnees`` (français) et ``agent/demo56107_donnees/<en|de|es>`` (même structure, adaptées au pays) : cabinet, dossiers, agenda, tâches, opportunités, projets à relire, modèles de courriels ``*.eml.txt``, réponses de
l'IA). Tout est fictif (liste de noms des consignes, domaines example.test, « TJ Valbrune », SIREN d'essai). Rien du cabinet réel.
"""
from datetime import date, datetime, time as dtime, timedelta, timezone
import base64
import hashlib
import hmac
from html import escape as e
import json
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import sys
import threading
import time
import zlib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from zoneinfo import ZoneInfo

from .common import Stop

VERSION_DONNEES = 1
DONNEES = Path(__file__).resolve().parent / 'demo56107_donnees'
MARQUEUR = 'DEMO-AXIORHUB.json'
PORT = 8779
PREFIXE_WEB = '/agent-courriel'
ORIGINE = 'http://127.0.0.1:%d' % PORT
PARIS = ZoneInfo('Europe/Paris')
RACINE_DOSSIERS = '/Dossiers'
BOITE_ENVOYES = 'Envoyés (démo)'
SERVEUR_COURRIEL = 'mx.demo.example.test'   # serveur de réception fictif : identifiant des en-têtes Authentication-Results de la démo
LANGUES = ('fr', 'en', 'de', 'es')   # 5.6.107 (lot M) : démo en français, anglais, allemand et espagnol (interface et données)
# Textes des données produits par le code (dossiers du stockage, boîtes, mentions) : français ici ; section ``textes`` de chaque
# ``<langue>/cabinet.json`` pour les autres langues (toutes les clés sont exigées par les essais : aucun repli en français).
TEXTES_FR = {'racine_dossiers': RACINE_DOSSIERS, 'dossier_pieces': 'PIECES', 'dossier_correspondances': 'CORRESPONDANCES',
             'modeles': 'Modèles du cabinet', 'modele_fichier': 'Lettre de mission.pdf', 'modele_titre': 'Modèle de lettre de mission',
             'modele_lignes': ['Cabinet DURAND Avocats — modèle fictif.', 'Objet de la mission, honoraires, conditions de règlement.'],
             'pdf_mention': 'Document fictif — démonstration AxiorHub', 'pdf_dossier': 'Dossier : %s — %s',
             'boites': {'brouillons': 'Brouillons', 'envoyes': BOITE_ENVOYES, 'corbeille': 'Corbeille', 'indesirables': 'Indésirables', 'archives': 'Archives'},
             'mention_envoi': 'Démonstration : rien n’a été envoyé.', 'evenement': 'Événement fictif (démonstration).',
             'opportunite': 'Opportunité fictive (démonstration).', 'echeance': 'Échéance fictive (démonstration).',
             'limite_projet': 'Projet de démonstration : à relire avant tout envoi.',
             'limite_assistant': 'Réponse préparée pour la démonstration : aucun modèle n’a été appelé.', 'courriel_recu': 'Courriel reçu',
             'demande_a_traiter': 'Demande à traiter : %s', 'demande_voir': 'Demande à traiter (voir le courriel).', 'format_date': '%d/%m/%Y',
             'stade': 'mise en état', 'nature_procedure': 'fond'}
MODELE = 'demonstration'
HORS_LIGNE = 'demonstration_hors_ligne'
COMPTE = 'claire.durand'
APP_WINDOWS = 'AxiorHub Pilote'
APP_LINUX = 'axiorhub-pilote'
API = 'm56107/demo/'
ROUTES_GET = ('etat',)
ROUTES_POST = ('reinitialiser', 'quitter', 'lancer')
CLE = re.compile(r'^[A-Za-z0-9_-]{8,80}$')
MESSAGES = {HORS_LIGNE: 'Démonstration : aucune connexion extérieure (messagerie, stockage, IA, mises à jour). Rien n’a quitté ce poste.',
            'demo_dossier_non_vide': 'Le dossier choisi pour la démo n’est pas vide.',
            'demo_sur_installation_reelle': 'La démo ne peut pas être créée dans une installation réelle d’AxiorHub.',
            'demo_isolement_refuse': 'Configuration de démo incohérente : un chemin sort du dossier de la démo. Rien n’a été lancé.',
            'demo_inactive': 'Cette action n’existe qu’en mode démonstration.', 'route_inconnue': 'Introuvable.',
            'demo_deja_active': 'Cette installation est déjà la démo.', 'lancement_demo_indisponible': 'La démo ne peut être lancée que depuis AxiorHub Pilote sur ce poste.',
            'role_insuffisant': 'Action réservée à l’administrateur.', 'cle_idempotence_requise': 'Clé d’idempotence absente.'}
STATUTS = {'route_inconnue': '404 Not Found', 'demo_inactive': '404 Not Found', 'role_insuffisant': '403 Forbidden',
           'origine_ou_csrf_refuse': '403 Forbidden', 'lancement_demo_indisponible': '409 Conflict', 'demo_deja_active': '409 Conflict'}

_local = threading.local()
_ETAT = {'arme': False, 'racine': '', 'installe': False, 'origines': {}, 'ecoutes': set(), 'refus': 0}
_VERROU = threading.RLock()


# ================================================================================================ chemins et marqueur
def _racine(chemin):
    p = Path(chemin)
    if p.is_file() or p.name == 'config.json':
        p = p.parent
    return p.resolve()


def _lire_marqueur(racine):
    try:
        data = json.loads((Path(racine) / MARQUEUR).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) and data.get('demo') is True else None
    except (OSError, ValueError):
        return None


def _config_de(objet):
    if isinstance(objet, dict):
        return objet
    return getattr(objet, 'c', None) or {}


def _dans(chemin, racine):
    try:
        p = Path(chemin).resolve()
        return p == racine or racine in p.parents
    except (OSError, TypeError, ValueError):
        return False


def chemins_de(config):
    """Chemins de données d'une configuration (tous doivent rester dans le dossier de la démo)."""
    mail = config.get('mail') or {}
    out = [config.get('state_dir'), config.get('matters_file'), (config.get('nextcloud') or {}).get('local_path'),
           (config.get('nextcloud') or {}).get('password_file'), mail.get('password_file'), mail.get('demo56107_boite'),
           ((mail.get('smtp') or {}).get('password_file'))]
    return [x for x in out if x]


def actif(objet):
    """Vrai seulement pour une configuration de démo : section ``demo56107`` active, marqueur présent dans son dossier, et tous les
    chemins de données dans ce dossier. Une configuration réelle (sans section) rend toujours faux, sans lecture du disque."""
    config = _config_de(objet)
    section = config.get('demo56107') if isinstance(config, dict) else None
    if not isinstance(section, dict) or section.get('actif') is not True:
        return False
    try:
        racine = Path(section.get('racine') or '').resolve()
    except (OSError, TypeError, ValueError):
        return False
    if not str(section.get('racine') or '') or _lire_marqueur(racine) is None:
        return False
    return all(_dans(c, racine) for c in chemins_de(config))


def verifier_isolement(config):
    """Démo seulement : refus si un chemin sort du dossier de la démo, si le marqueur manque, ou si le dossier est celui d'une installation
    réelle. Appelé avant d'armer un processus et au lancement ; jamais une démo ne lit les données du cabinet."""
    section = config.get('demo56107') or {}
    if not section.get('racine'):
        raise Stop('demo_isolement_refuse')
    racine = Path(section['racine']).resolve()
    if _lire_marqueur(racine) is None or not all(_dans(c, racine) for c in chemins_de(config)):
        raise Stop('demo_isolement_refuse')
    if any(_dans(racine, reel) or _dans(reel, racine) for reel in dossiers_reels()):
        raise Stop('demo_isolement_refuse')
    return racine


def dossiers_reels(home=None):
    """Dossiers de données d'une installation réelle connue (poste Windows et Ubuntu, serveur, conteneur)."""
    explicite = home or os.environ.get('AXIORHUB_POSTE_HOME')
    home = Path(explicite or Path.home()).expanduser()
    out = []
    if os.name == 'nt':   # même règle que poste.paths : dossier personnel explicite → son AppData/Local, sinon LOCALAPPDATA
        local = (home / 'AppData' / 'Local') if explicite else Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
        out.append(local / APP_WINDOWS / 'donnees')
    out += [home / '.local' / 'share' / APP_LINUX, Path('/etc/axiorhub-mail-agent'), Path('/var/lib/axiorhub-mail-agent')]
    if os.environ.get('AXIORHUB_DATA_DIR') and _lire_marqueur(os.environ['AXIORHUB_DATA_DIR']) is None:
        out.append(Path(os.environ['AXIORHUB_DATA_DIR']))   # dossier de données d'un processus de service réel (conteneur, serveur)
    return [p.resolve() for p in out if str(p)]


def dossier_poste(home=None):
    """Dossier de la démo sur le poste : à côté des données réelles, jamais dedans."""
    explicite = home or os.environ.get('AXIORHUB_POSTE_HOME')
    home = Path(explicite or Path.home()).expanduser().absolute()
    if os.name == 'nt':
        local = (home / 'AppData' / 'Local') if explicite else Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
        return local / APP_WINDOWS / 'demo'
    return home / '.local' / 'share' / (APP_LINUX + '-demo')


def _refuser_installation_reelle(racine):
    """« Par-dessus une installation réelle » : la cible ou l'un de ses parents porte une configuration, des comptes ou une base sans le
    marqueur de démo, ou la cible est (ou contient) le dossier de données réel du poste."""
    for parent in [racine] + list(racine.parents):
        if _lire_marqueur(parent) is not None:
            continue
        if any((parent / nom).exists() for nom in ('config.json', 'ui-auth.json', 'internal-auth.json', 'matters.json')) or (parent / 'state' / 'desk.sqlite3').exists():
            raise Stop('demo_sur_installation_reelle')
    for reel in dossiers_reels():
        if _dans(racine, reel) or _dans(reel, racine):
            raise Stop('demo_sur_installation_reelle')


# ================================================================================================ garde : aucun transport sortant
class TransportDemo(ConnectionRefusedError):
    """Connexion refusée par le mode Démo (sous-classe d'``OSError`` : traitée comme un service indisponible par le code existant)."""


def _garde_active():
    return _ETAT['arme'] or bool(getattr(_local, 'creation', False))


def _boucle_locale(hote):
    return str(hote or '').strip('[]').lower() in ('127.0.0.1', '::1', 'localhost')


def _refuser(quoi):
    with _VERROU:
        _ETAT['refus'] += 1
    raise TransportDemo(111, 'Démonstration : connexion sortante refusée (%s)' % str(quoi)[:80])


def _installer_correctifs():
    """Pose une fois par processus les points de contrôle ; sans démo armée (ni création en cours dans ce fil), ils laissent tout passer."""
    with _VERROU:
        if _ETAT['installe']:
            return
        o = _ETAT['origines']
        o['connect'], o['connect_ex'], o['listen'] = socket.socket.connect, socket.socket.connect_ex, socket.socket.listen
        o['getaddrinfo'], o['gethostbyname'], o['gethostbyname_ex'] = socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex
        o['create_connection'] = socket.create_connection

        def autorise(adresse):
            # Seule exception : une connexion de bouclage vers un port ouvert en écoute PAR CE PROCESSUS après l'armement (réveil interne
            # d'un serveur). Aucun service local (moteur d'IA, synthèse vocale, bureautique) n'est joignable.
            if isinstance(adresse, tuple) and len(adresse) >= 2 and _boucle_locale(adresse[0]):
                return int(adresse[1]) in _ETAT['ecoutes']
            return False

        def connect(self, adresse):
            if _garde_active() and not autorise(adresse):
                _refuser(adresse)
            return o['connect'](self, adresse)

        def connect_ex(self, adresse):
            if _garde_active() and not autorise(adresse):
                _refuser(adresse)
            return o['connect_ex'](self, adresse)

        def listen(self, *args):
            resultat = o['listen'](self, *args)
            try:
                if _garde_active():
                    _ETAT['ecoutes'].add(int(self.getsockname()[1]))
            except (OSError, IndexError, TypeError, ValueError):
                pass
            return resultat

        def getaddrinfo(hote, *args, **kwargs):
            if _garde_active() and hote is not None and not _boucle_locale(hote):
                _refuser(hote)
            return o['getaddrinfo'](hote, *args, **kwargs)

        def gethostbyname(hote):
            if _garde_active() and not _boucle_locale(hote):
                _refuser(hote)
            return o['gethostbyname'](hote)

        def gethostbyname_ex(hote):
            if _garde_active() and not _boucle_locale(hote):
                _refuser(hote)
            return o['gethostbyname_ex'](hote)

        def create_connection(adresse, *args, **kwargs):
            if _garde_active() and not autorise(adresse):
                _refuser(adresse)
            return o['create_connection'](adresse, *args, **kwargs)

        socket.socket.connect, socket.socket.connect_ex, socket.socket.listen = connect, connect_ex, listen
        socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex = getaddrinfo, gethostbyname, gethostbyname_ex
        socket.create_connection = create_connection

        # common.HTTP : IA de démonstration (moteur local simulé) ; toute autre requête est refusée en clair.
        from . import common
        o['http_json'], o['http_request'] = common.HTTP.json, common.HTTP.request

        def http_json(self, method, path, data=None):
            if _garde_active():
                return reponse_moteur(method, path, data)
            return o['http_json'](self, method, path, data)

        def http_request(self, method, url, data=None, headers=None, limit=12_000_000):
            if _garde_active():
                with _VERROU:
                    _ETAT['refus'] += 1
                raise Stop(HORS_LIGNE)
            return o['http_request'](self, method, url, data, headers, limit)
        common.HTTP.json, common.HTTP.request = http_json, http_request

        # Messagerie : boîte locale pour la configuration de démo, refus de toute autre en démo, inchangée sinon.
        from . import mailbox
        o['imap_connect'] = mailbox.connect

        def imap_connect(cfg):
            if isinstance(cfg, dict) and cfg.get('demo56107_boite') and _boite_admise(cfg['demo56107_boite']):
                from .demo56107_imap import connexion
                return connexion(cfg)
            if _garde_active():
                raise Stop('connexion_imap_echouee')
            return o['imap_connect'](cfg)
        mailbox.connect = imap_connect
        _ETAT['installe'] = True


def _boite_admise(chemin):
    """La boîte locale n'est servie que si elle est dans le dossier d'une démo marquée (jamais un chemin quelconque d'une configuration)."""
    try:
        p = Path(chemin).resolve()
    except (OSError, TypeError, ValueError):
        return False
    return any(_lire_marqueur(parent) is not None for parent in p.parents)


class _SMTPDemo:
    """Client SMTP de démonstration : rien ne part ; la copie dans « Envoyés (démo) » porte la mention (``demo56107_imap``)."""
    envoyes = []

    def __init__(self, cfg):
        self.cfg = cfg

    def login(self, *_):
        return (235, b'demo')

    def send_message(self, message, from_addr=None, to_addrs=None):
        _SMTPDemo.envoyes.append({'a': list(to_addrs or []), 'objet': str(message.get('Subject', ''))[:200], 'quand': time.time()})
        del _SMTPDemo.envoyes[:-50]
        return {}

    def quit(self):
        return (221, b'demo')

    def starttls(self, *_a, **_k):
        return (220, b'demo')


def armer(config):
    """Processus de service d'une démo : garde global posé, puis boîte locale, IA de démonstration et envoi simulé. Une configuration
    incohérente (chemin hors du dossier de la démo) est refusée : le processus ne sert rien plutôt que de lire d'autres données."""
    racine = verifier_isolement(config)
    _installer_correctifs()
    from . import web490      # la couche web d'envoi, seule à toucher au module d'envoi
    with _VERROU:
        _ETAT['arme'], _ETAT['racine'] = True, str(racine)
        _ETAT['langue'] = langue_de(racine)   # réponses de l'IA de démonstration dans la langue de l'instance
        web490.fabrique_smtp_demo(_SMTPDemo)
    return racine


def au_bureau(config):
    """Point de montage de ``Desk.__init__`` : arme le processus pour une configuration de démo (sauf dans le fil qui construit la démo)."""
    if not isinstance(config, dict) or not config.get('demo56107'):
        return False
    if getattr(_local, 'creation', False):
        return True
    if not _ETAT['arme']:
        armer(config)
    return True


def desarmer():
    """Essais seulement : retire l'armement du processus (les points de contrôle restent posés, transparents)."""
    from . import web490
    with _VERROU:
        _ETAT['arme'], _ETAT['racine'] = False, ''
        _ETAT['ecoutes'].clear()
        precedente = web490.fabrique_smtp_demo(None)
        if precedente is not _SMTPDemo:
            web490.fabrique_smtp_demo(precedente)


def arme():
    return bool(_ETAT['arme'])


# ================================================================================================ IA de démonstration
_REPONSES = {}


def langue_active():
    """Langue des données servies par ce processus : fil de construction, sinon instance armée, sinon français."""
    return getattr(_local, 'langue', None) or _ETAT.get('langue') or 'fr'


def reponses(langue=None):
    langue = langue or langue_active()
    if langue not in _REPONSES:
        _REPONSES[langue] = json.loads((dossier_donnees(langue) / 'reponses-ia.json').read_text(encoding='utf-8'))
    return _REPONSES[langue]


def textes(langue=None):
    return _cabinet(langue or langue_active())['textes']


def _plier(texte):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFKD', str(texte or '').lower()) if not unicodedata.combining(c))


def marquer(texte):
    marque = reponses()['marque']
    texte = str(texte or '').strip()
    return texte if texte.startswith(marque) else marque + ' — ' + texte


def _dernier_utilisateur(messages):
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get('role') == 'user':
            return str(m.get('content') or '')
    return ''


def _question(contenu):
    """Question posée à l'assistant : champ ``question_avocat`` (ou voisin) de la charge JSON, sinon le début du message."""
    try:
        data = json.loads(contenu)
    except (TypeError, ValueError):
        return contenu[:600]
    pile = [data]
    while pile:
        x = pile.pop()
        if isinstance(x, dict):
            for cle in ('question_avocat', 'question', 'demande', 'instruction', 'message'):
                if isinstance(x.get(cle), str) and x[cle].strip():
                    return x[cle]
            pile.extend(x.values())
        elif isinstance(x, list):
            pile.extend(x)
    return contenu[:600]


def _choisir(liste, texte, defaut):
    f = ' ' + _plier(texte) + ' '
    meilleur, score = defaut, 0
    for item in liste:
        n = len({_plier(mot) for mot in item['mots'] if _plier(mot) in f})   # mots distincts une fois pliés
        if n > score:
            meilleur, score = item['texte'], n
    return meilleur


def _refs_courriels(contenu):
    """Références ``C1``… et objets des courriels transmis au résumé."""
    refs = []
    try:
        data = json.loads(contenu)
    except (TypeError, ValueError):
        data = None
    pile = [data] if data is not None else []
    while pile:
        x = pile.pop(0)
        if isinstance(x, dict):
            if isinstance(x.get('ref'), str) and re.fullmatch(r'C\d{1,3}', x['ref']):
                objet = next((str(x[k]) for k in ('objet', 'sujet', 'subject', 'titre') if isinstance(x.get(k), str)), '')
                refs.append((x['ref'], objet))
            pile.extend(v for v in x.values() if isinstance(v, (dict, list)))
        elif isinstance(x, list):
            pile.extend(x)
    if not refs:
        refs = [(r, '') for r in dict.fromkeys(re.findall(r'"(C\d{1,3})"', contenu))]
    return list(dict.fromkeys(refs))


def _remplir(schema, nom='', contexte=None):
    """Valeur conforme à un schéma JSON (types, énumérations, champs requis), sans contenu inventé au-delà des textes préparés."""
    contexte = contexte or {}
    if not isinstance(schema, dict):
        return marquer(reponses()['texte_generique'])
    if 'enum' in schema and schema['enum']:
        return schema['enum'][0]
    genre = schema.get('type')
    if isinstance(genre, list):
        genre = next((g for g in genre if g != 'null'), 'string')
    if genre == 'object' or 'properties' in schema:
        props = schema.get('properties') or {}
        return {k: _remplir(v, k, contexte) for k, v in props.items()}
    if genre == 'array':
        return []
    if genre == 'boolean':
        return bool(contexte.get('booleens_vrais'))
    if genre in ('integer', 'number'):
        return 0
    if any(x in nom for x in ('date', 'start', 'end', 'echeance', 'jour', 'heure')):
        return ''
    if nom in ('source_id', 'ref', 'id', 'filename', 'case_number', 'jurisdiction', 'suggested_folder', 'last_email'):
        return ''
    return marquer(contexte.get('texte') or reponses()['texte_generique'])


def repondre(req):
    """Contenu rendu par le moteur simulé pour une requête ``/api/chat`` : JSON conforme au schéma demandé, ou texte marqué."""
    req = req if isinstance(req, dict) else {}
    contenu = _dernier_utilisateur(req.get('messages'))
    schema = req.get('format')
    r = reponses()
    if not isinstance(schema, dict):
        if schema == 'json':
            return json.dumps({'texte': marquer(r['texte_generique'])}, ensure_ascii=False)
        systeme = ' '.join(str(m.get('content') or '')[:400] for m in (req.get('messages') or []) if isinstance(m, dict) and m.get('role') == 'system')
        if any(x in _plier(systeme) for x in ('piece', 'document', 'analyse')):
            return marquer(r['analyse_piece'])
        return marquer(r['texte_generique'])
    props = schema.get('properties') or {}
    if 'answer' in props:                                   # assistant (CHAT)
        sortie = _remplir(schema)
        sortie['answer'] = marquer(_choisir(r['assistant'], _question(contenu), r['assistant_generique']))
        sortie['limits'] = [textes()['limite_assistant']] if 'limits' in props else sortie.get('limits')
        return json.dumps(sortie, ensure_ascii=False)
    if 'body' in props:                                     # projet de réponse (MANUAL_DRAFT, COMPOSE)
        sortie = _remplir(schema)
        sortie['body'] = marquer(_choisir(r['projet_reponse'], contenu, r['projet_generique']))
        for cle in ('can_draft', 'requires_decision'):
            if cle in props:
                sortie[cle] = True
        if 'limits' in props:
            sortie['limits'] = [textes()['limite_projet']]
        return json.dumps(sortie, ensure_ascii=False)
    if 'synthese' in props and 'courriels' in props:      # résumé des courriels cochés (lecteur56106)
        refs = _refs_courriels(contenu)
        sortie = {'synthese': marquer(r['resume_generique']),
                  'courriels': [{'ref': ref, 'demande': (textes()['demande_a_traiter'] % objet) if objet else textes()['demande_voir'], 'echeance': ''}
                                for ref, objet in refs[:20]], 'actions': []}
        return json.dumps(sortie, ensure_ascii=False)
    if 'grounded' in props:                                 # contrôle (VERIFY) : la démonstration ne bloque pas le dépôt
        sortie = _remplir(schema, contexte={'booleens_vrais': True})
        if 'requires_lawyer' in props:
            sortie['requires_lawyer'] = True
        if 'reasons' in props:
            sortie['reasons'] = []
        return json.dumps(sortie, ensure_ascii=False)
    return json.dumps(_remplir(schema, contexte={'texte': r['analyse_piece'] if 'items' in props else r['texte_generique']}), ensure_ascii=False)


def reponse_moteur(method, path, data):
    """Moteur local simulé (même forme que les réponses d'Ollama) ; tout autre chemin : hors ligne."""
    chemin = str(path or '').split('?', 1)[0]
    if chemin.endswith('/api/show'):
        return {'capabilities': ['completion'], 'details': {'family': 'demonstration'}, 'model_info': {'demonstration.context_length': 65536}}
    if chemin.endswith('/api/tags'):
        return {'models': [{'name': MODELE, 'model': MODELE}]}
    if chemin.endswith('/api/version'):
        return {'version': '0.0-demonstration'}
    if chemin.endswith('/api/embed'):
        entrees = (data or {}).get('input') or []
        return {'embeddings': [[0.0] * 16 for _ in (entrees if isinstance(entrees, list) else [entrees])]}
    if chemin.endswith('/api/chat') and str(method).upper() == 'POST':
        contenu = repondre(data)
        return {'model': MODELE, 'done': True, 'done_reason': 'stop', 'message': {'role': 'assistant', 'content': contenu},
                'prompt_eval_count': 0, 'eval_count': max(1, len(contenu) // 4), 'total_duration': 0}
    raise Stop(HORS_LIGNE)


# ================================================================================================ fichiers générés (PDF, logo)
def _pdf_texte(valeur):
    octets = str(valeur).replace('’', "'").encode('cp1252', 'replace')
    return octets.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


def pdf(titre, lignes, mention=TEXTES_FR['pdf_mention']):
    """PDF d'une page (Helvetica, WinAnsi), lisible par l'extraction de texte ; aucun contenu réel."""
    flux = [b'BT /F2 16 Tf 56 780 Td (' + _pdf_texte(titre) + b') Tj ET', b'BT /F1 9 Tf 56 800 Td (' + _pdf_texte(mention) + b') Tj ET']
    y = 740
    for ligne in lignes:
        morceaux = [ligne[i:i + 95] for i in range(0, len(ligne), 95)] or ['']
        for m in morceaux:
            flux.append(b'BT /F1 11 Tf 56 %d Td (' % y + _pdf_texte(m) + b') Tj ET')
            y -= 18
        y -= 6
    contenu = b'\n'.join(flux)
    objets = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
              b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>',
              b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
              b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>',
              b'<< /Length %d >>\nstream\n' % len(contenu) + contenu + b'\nendstream']
    sortie, positions = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'), []
    for i, obj in enumerate(objets, 1):
        positions.append(len(sortie))
        sortie += b'%d 0 obj\n' % i + obj + b'\nendobj\n'
    xref = len(sortie)
    sortie += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objets) + 1) + b''.join(b'%010d 00000 n \n' % p for p in positions)
    sortie += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objets) + 1, xref)
    return bytes(sortie)


def logo_png(taille=128):
    """Logo fictif généré : carré bleu nuit, cadre et monogramme « D » dorés (PNG, sans bibliothèque)."""
    fond, or_ = (24, 42, 74), (201, 162, 74)
    lignes = []
    c = taille / 2.0
    for y in range(taille):
        ligne = bytearray(b'\x00')
        for x in range(taille):
            px = fond
            if min(x, y, taille - 1 - x, taille - 1 - y) in range(6, 10):
                px = or_
            dx, dy = x - (c - 22), y - c
            if 34 <= y <= taille - 35:
                if 38 <= x <= 47:
                    px = or_
                elif x > 47 and 30 <= (dx * dx * 0.55 + dy * dy) ** 0.5 <= 39 and x >= c - 22:
                    px = or_
            ligne += bytes(px)
        lignes.append(bytes(ligne))
    brut = b''.join(lignes)

    def morceau(genre, donnees):
        return struct.pack('>I', len(donnees)) + genre + donnees + struct.pack('>I', zlib.crc32(genre + donnees) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + morceau(b'IHDR', struct.pack('>IIBBBBB', taille, taille, 8, 2, 0, 0, 0))
            + morceau(b'IDAT', zlib.compress(brut, 9)) + morceau(b'IEND', b''))


# ================================================================================================ dates relatives
def aujourdhui():
    return datetime.now(PARIS).date()


def jour_ouvre(base, decalage):
    """Jour ``base + decalage`` ramené à un jour ouvré (samedi/dimanche : lundi pour l'avenir, vendredi pour le passé)."""
    j = base + timedelta(days=int(decalage))
    if j.weekday() >= 5:
        j = j + timedelta(days=7 - j.weekday()) if decalage >= 0 else j - timedelta(days=j.weekday() - 4)
    return j


def _langue(valeur):
    langue = str(valeur or 'fr').strip().lower()
    if langue not in LANGUES:
        raise Stop('demo_langue_inconnue')
    return langue


def dossier_donnees(langue='fr'):
    """Données d'une langue : le français à la racine de ``demo56107_donnees`` (inchangé), les autres dans leur sous-dossier."""
    langue = _langue(langue)
    return DONNEES if langue == 'fr' else DONNEES / langue


_CABINETS = {}


def _cabinet(langue='fr'):
    langue = _langue(langue)
    if langue not in _CABINETS:
        data = json.loads((dossier_donnees(langue) / 'cabinet.json').read_text(encoding='utf-8'))
        data['textes'] = {**TEXTES_FR, **(data.get('textes') or {})} if langue == 'fr' else data['textes']
        data['langue'] = langue
        _CABINETS[langue] = data
    return json.loads(json.dumps(_CABINETS[langue]))


def langue_de(chemin):
    marque = _lire_marqueur(_racine(chemin)) or {}
    return marque.get('langue') if marque.get('langue') in LANGUES else 'fr'


def mention_envoi(racine):
    """Mention portée par une copie « envoyée » dans la boîte de démonstration (langue de l'instance)."""
    try:
        return textes(langue_de(racine))['mention_envoi']
    except (Stop, OSError, ValueError, KeyError):
        return TEXTES_FR['mention_envoi']


def _chemin_dossier(d):
    titre = d['titre'].replace('./.', '-').replace('/', '-')            # « DURAND ./. MARTIN » (allemand) : nom de dossier lisible
    return '%s/%s %s' % (d.get('_racine') or RACINE_DOSSIERS, d['id'], titre)


# ================================================================================================ construction
def _ecrire_prive(chemin, texte):
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(chemin), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(texte)


def _configuration(racine, langue='fr'):
    tout = _cabinet(langue)
    cab, t = tout['cabinet'], tout['textes']
    secret = str(racine / 'secrets' / 'demonstration.secret')
    adresse = cab['adresse_courriel']
    return {
        'version': 1, 'mode': 'drafts', 'state_dir': str(racine / 'state'), 'matters_file': str(racine / 'matters.json'), 'max_messages_per_run': 8,
        'installation': {'done': True, 'at': 'demonstration', 'by': 'demo56107'},
        'demo56107': {'actif': True, 'racine': str(racine), 'version': VERSION_DONNEES, 'langue': tout['langue']},
        'interface': {'langue': tout['langue']},
        'allow_legal_clarification': True, 'memory': {'enabled': False},
        'mail': {'enabled': True, 'host': 'imap.demo.example.test', 'port': 993, 'username': adresse, 'password_file': secret, 'inbox': 'INBOX',
                 'drafts': t['boites']['brouillons'], 'sent': t['boites']['envoyes'], 'trash': t['boites']['corbeille'], 'from_address': adresse, 'from_name': cab['avocate'],
                 'signature': cab['signature'], 'own_addresses': [adresse, cab['adresse_accueil']], 'reply_all_known_clients': True, 'lookback_days': 0,
                 'process_seen_recent': True, 'retroactive_lookback_days': 7, 'retroactive_max_candidates_per_run': 80, 'max_thread_messages': 50,
                 'max_body_chars': 100000, 'max_message_bytes': 20000000, 'excluded_senders': [], 'excluded_domains': [], 'excluded_subject_patterns': [],
                 'demo56107_boite': str(racine / 'boite'),
                 'smtp': {'host': 'smtp.demo.example.test', 'port': 465, 'security': 'ssl', 'username': adresse, 'password_file': secret}},
        'nextcloud': {'url': 'https://stockage.demo.example.test', 'username': 'demonstration', 'password_file': secret, 'local_path': str(racine / 'stockage'),
                      'roots': [t['racine_dossiers']], 'matter_roots': [t['racine_dossiers']], 'max_depth': 8, 'max_files': 500, 'max_file_bytes': 15000000,
                      'inventory_page_files': 250, 'max_inventory_directories': 2000, 'max_analysis_files': 5000},
        'calendar': {'urls': [], 'timezone': 'Europe/Paris', 'horizon_days': 90, 'availability_days': 10, 'appointment_minutes': 30, 'buffer_minutes': 15,
                     'notice_hours': 0, 'weekdays': [0, 1, 2, 3, 4], 'working_hours': [['09:00', '12:00'], ['14:00', '18:00']]},
        'ollama': {'url': 'http://127.0.0.1:11434', 'model': MODELE, 'timeout_seconds': 60, 'num_ctx': 65536, 'max_context_chars': 90000, 'disable_thinking': True},
        'rag': {'enabled': False},
        'documents': {'ocr': False, 'max_pdf_pages': 50, 'max_file_bytes': 20000000, 'max_document_chars': 500000, 'max_documents_per_mail': 5,
                      'index_updates_per_run': 25, 'inventory_page_files': 250},
        'automation': {'health_enabled': False, 'sync_enabled': False, 'reconcile_inbox_enabled': False, 'classify_portfolio_enabled': False,
                       'index_all_enabled': False, 'daily_digest_enabled': False},
        'autonomy': {'enabled': False}, 'orchestrator': {'enabled': False}, 'production': {'enabled': False},
        'updates': {'channel': 'stable', 'metadata_url': '', 'require_signature': True},
        'model_routing': {'fast_model': MODELE, 'complex_model': MODELE, 'control_model': MODELE, 'fast_temperature': 0, 'complex_temperature': 0, 'control_temperature': 0},
        'llm': {'profile': 'juridique', 'profile_version': 1, 'context_mode': 'auto'}, 'ai_providers': {}, 'lawve_extensions': {},
        'speech568': {'external_allowed': False, 'fallback_local': True}, 'audio': {'enabled': False}, 'workstation': {}, 'meteo5696': {'enabled': False},
        'legal_memory': {'enabled': True}, 'portfolio': {'enabled': True}, 'document_projects': {'enabled': True}, 'hearing': {'enabled': True},
        'word_legal': {'enabled': True}, 'opinions': {'enabled': True}, 'cabinet_pilotage': {'enabled': True}}


def _auth(racine, origine, prefixe):
    """Compte fictif « Me Claire DURAND » (administratrice) : mot de passe aléatoire écrit en privé, connexion par jeton de lancement."""
    mot = secrets.token_urlsafe(18)
    sel = secrets.token_bytes(16)
    auth = {'username': COMPTE, 'salt': sel.hex(), 'hash': hashlib.scrypt(mot.encode(), salt=sel, n=16384, r=8, p=1).hex(),
            'csrf': secrets.token_urlsafe(32), 'origin': origine.rstrip('/'), 'prefix': prefixe, 'poste': True, 'demo56107': True}
    _ecrire_prive(racine / 'mot-de-passe-local', mot + '\n')
    _ecrire_prive(racine / 'ui-auth.json', json.dumps(auth, ensure_ascii=False) + '\n')


class _Creation:
    """Fil de construction : garde actif pour ce fil seulement, variables d'environnement de la démo le temps de la construction."""

    def __init__(self, racine, langue='fr'):
        self.racine, self.langue = racine, langue
        self.env = {'AXIORHUB_DATA_DIR': str(racine), 'AXIORHUB_AUTH_STATE': str(racine / 'auth'), 'AXIORHUB_DEMO': '1'}

    def __enter__(self):
        _installer_correctifs()
        self.avant = {k: os.environ.get(k) for k in self.env}
        self.precedent = getattr(_local, 'creation', False)
        self.langue_avant = getattr(_local, 'langue', None)
        _local.creation, _local.langue = True, self.langue
        os.environ.update(self.env)
        return self

    def __exit__(self, *exc):
        _local.creation, _local.langue = self.precedent, self.langue_avant
        for k, v in self.avant.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def creer(dossier_cible, graine=None, origine=ORIGINE, prefixe=PREFIXE_WEB, langue='fr'):
    """Instance de démo complète dans un dossier vide (ou absent) ; rend le chemin de ``config.json``."""
    langue = _langue(langue)
    racine = Path(dossier_cible).expanduser().absolute()
    _refuser_installation_reelle(racine)
    if racine.exists() and (not racine.is_dir() or any(racine.iterdir())):
        raise Stop('demo_dossier_non_vide')
    racine.mkdir(parents=True, exist_ok=True)
    racine = racine.resolve()
    graine = int(graine) if graine is not None else secrets.randbelow(10 ** 9)
    with _Creation(racine, langue):
        for sous in ('state', 'stockage', 'boite', 'auth', 'secrets', 'journaux'):
            (racine / sous).mkdir(exist_ok=True)
        try:
            os.chmod(racine, 0o700)
        except OSError:
            pass
        _ecrire_prive(racine / 'secrets' / 'demonstration.secret', 'demonstration-sans-valeur\n')
        jour = aujourdhui()
        _ecrire_prive(racine / MARQUEUR, json.dumps({'demo': True, 'version': VERSION_DONNEES, 'jour': jour.isoformat(), 'graine': graine, 'langue': langue,
                                                    'cree': datetime.now(timezone.utc).isoformat(timespec='seconds')}, ensure_ascii=False) + '\n')
        config = _configuration(racine, langue)
        _ecrire_prive(racine / 'config.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        _auth(racine, origine, prefixe)
        _semer(racine, jour, graine, langue)
    return str(racine / 'config.json')


def preparer_lancement(chemin):
    """Démo créée un autre jour : remise à neuf pour que tout paraisse « d'aujourd'hui ». Rend vrai si elle a été refaite."""
    racine = _racine(chemin)
    marque = _lire_marqueur(racine)
    if marque is None:
        raise Stop('demo_isolement_refuse')
    if marque.get('jour') != aujourdhui().isoformat() or int(marque.get('version') or 0) != VERSION_DONNEES:
        reinitialiser(racine)
        return True
    return False


def quitter_demande(chemin):
    return (_racine(chemin) / 'quitter.demande').exists()


GARDEES = frozenset(('schema_migrations', 'settings'))


def _bases(state):
    return sorted(p for p in Path(state).rglob('*.sqlite3') if p.is_file())


def _vider_base(chemin):
    """Toutes les lignes d'une base, schéma conservé (tables virtuelles vidées par elles-mêmes, jamais leurs tables internes). Restent :
    ``schema_migrations`` (sinon les migrations numérotées seraient rejouées sur un schéma déjà migré) et ``settings`` (marqueurs de reprise
    des modules ; les réglages de la démo sont réécrits par le semis)."""
    db = sqlite3.connect(str(chemin), timeout=30)
    try:
        db.execute('PRAGMA busy_timeout=30000')
        lignes = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        virtuelles = [n for n, s in lignes if str(s or '').upper().startswith('CREATE VIRTUAL TABLE')]
        internes = {n for n, _ in lignes for v in virtuelles if n != v and n.startswith(v + '_')}
        db.execute('BEGIN IMMEDIATE')
        for nom, _ in lignes:
            if nom not in internes and nom not in GARDEES:
                db.execute('DELETE FROM "%s"' % nom.replace('"', '""'))
        try:
            db.execute('DELETE FROM sqlite_sequence')
        except sqlite3.OperationalError:
            pass
        db.commit()
    finally:
        db.close()


def reinitialiser(chemin, langue=None):
    """Remise à neuf de l'instance (« Recommencer la démo ») : bases vidées puis préparées et resemées, fichiers des dossiers et boîte
    réécrits, dates recalculées. Les sessions ouvertes et le compte restent valables (la personne garde sa fenêtre)."""
    racine = _racine(chemin)
    marque = _lire_marqueur(racine)
    if marque is None:
        raise Stop('demo_isolement_refuse')
    config = json.loads((racine / 'config.json').read_text(encoding='utf-8'))
    verifier_isolement(config)
    langue = _langue(langue) if langue else (marque.get('langue') if marque.get('langue') in LANGUES else 'fr')   # la langue de l'instance est gardée
    with _Creation(racine, langue):
        state = Path(config['state_dir'])
        for base in _bases(state):
            _vider_base(base)
        for p in state.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif not p.name.endswith(('.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.lock')):
                try:
                    p.unlink()
                except OSError:
                    pass
        shutil.rmtree(racine / 'stockage', ignore_errors=True)
        (racine / 'stockage').mkdir(exist_ok=True)
        try:
            (racine / 'quitter.demande').unlink()
        except OSError:
            pass
        from . import desk as _desk
        cle = _desk._schema_key(config)
        _desk._SCHEMA_READY.discard(cle)      # préparation complète refaite (données par défaut des migrations)
        jour = aujourdhui()
        graine = int(marque.get('graine') or 0)
        _ecrire_prive(racine / MARQUEUR, json.dumps({**marque, 'version': VERSION_DONNEES, 'jour': jour.isoformat(), 'langue': langue,
                                                    'reinitialise': datetime.now(timezone.utc).isoformat(timespec='seconds')}, ensure_ascii=False) + '\n')
        _ecrire_prive(racine / 'config.json', json.dumps({**_configuration(racine, langue), 'demo56107': {**config['demo56107'], 'langue': langue}},
                                                         ensure_ascii=False, indent=2) + '\n')
        _semer(racine, jour, graine, langue)
    try:
        from . import identite56106
        identite56106.oublier(str(state))
    except Exception:   # noqa: BLE001 — cache d'affichage facultatif
        pass
    try:
        from .common import oublier_registre
        oublier_registre()
    except Exception:   # noqa: BLE001
        pass
    return str(racine / 'config.json')


# ------------------------------------------------------------------------------------------------ semis
def _semer(racine, jour, graine, langue='fr'):
    from .common import load_config, private_json
    cab = _cabinet(langue)
    t = cab['textes']
    for d in cab['dossiers']:
        d['_racine'] = t['racine_dossiers']
    rnd = random.Random(graine)
    matters = []
    for d in cab['dossiers']:
        matters.append({'id': d['id'], 'client_name': d['client'], 'path': _chemin_dossier(d), 'aliases': d['aliases'], 'references': [d['id']] + d['references'],
                        'correspondents': d['correspondants'], 'registered_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
    private_json(racine / 'matters.json', matters)
    stockage = racine / 'stockage'
    for d in cab['dossiers']:
        dossier = stockage / _chemin_dossier(d).lstrip('/')
        (dossier / t['dossier_pieces']).mkdir(parents=True, exist_ok=True)
        (dossier / t['dossier_correspondances']).mkdir(exist_ok=True)
        for nom, titre, lignes in d['pieces']:
            (dossier / t['dossier_pieces'] / nom).write_bytes(pdf(titre, [t['pdf_dossier'] % (d['id'], d['titre'])] + lignes, t['pdf_mention']))
    modeles = stockage / t['racine_dossiers'].lstrip('/') / t['modeles']
    modeles.mkdir(parents=True, exist_ok=True)
    (modeles / t['modele_fichier']).write_bytes(pdf(t['modele_titre'], t['modele_lignes'], t['pdf_mention']))
    ids = _semer_boite(racine, cab, jour, rnd)
    config = load_config(str(racine / 'config.json'))
    from .desk import Desk
    desk = Desk(config)
    try:
        _semer_bureau(desk, cab, jour, ids)
    finally:
        desk.db.close()


def _semer_boite(racine, cab, jour, rnd):
    """Courriels fictifs (modèles ``*.eml.txt``, en-têtes ``X-Demo-*`` retirés) dans la boîte locale ; dates recalculées depuis maintenant. Rend {identifiant: (boîte, uid, objet)}."""
    from .demo56107_imap import Boite
    from .mailbox import modified_utf7
    pieces = {(d['id'], p[0]): (p[1], p[2], d) for d in cab['dossiers'] for p in d['pieces']}
    maintenant = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    t = cab['textes']
    audience = next(a for a in cab['agenda'] if a['dossier'] == cab['dossiers'][0]['id'] and a['nature'] == 'audience')
    jour_audience = jour_ouvre(jour, audience['jour'])
    boite = Boite(racine / 'boite')
    shutil.rmtree(boite.messages, ignore_errors=True)
    with boite.transaction(True):
        boite.boites = {}
        boite.creer('INBOX', '', 101)
        b = t['boites']
        for nom, drapeau in ((b['brouillons'], '\\Drafts'), (b['envoyes'], '\\Sent'), (b['corbeille'], '\\Trash'), (b['indesirables'], '\\Junk'), (b['archives'], '\\Archive')):
            boite.creer(modified_utf7(nom), drapeau)
        envoyes = modified_utf7(b['envoyes'])
        modeles = sorted((dossier_donnees(cab['langue']) / 'courriels').glob('*.eml.txt'))   # modèles RFC 822 (extension .txt : jamais un vrai courriel publié)
        entrees = []
        for chemin in modeles:
            texte = chemin.read_text(encoding='utf-8')
            tete, _, corps = texte.partition('\n\n')
            meta, entetes = {}, []
            for ligne in tete.splitlines():
                nom, _, valeur = ligne.partition(':')
                if nom.startswith('X-Demo-'):
                    meta[nom[7:].lower()] = valeur.strip()
                else:
                    entetes.append((nom, valeur.strip()))
            entrees.append((meta, entetes, corps))
        ids = {}
        message_ids = {m.get('id'): '<%s@demo.example.test>' % m.get('id') for m, _, _ in entrees}
        for meta, entetes, corps in sorted(entrees, key=lambda x: -int(x[0].get('heures') or 0)):
            quand = maintenant - timedelta(hours=int(meta.get('heures') or 1), minutes=rnd.randint(0, 50))
            msg = EmailMessage()
            for nom, valeur in entetes:
                msg[nom] = valeur
            msg['Date'] = format_datetime(quand.astimezone(PARIS))
            msg['MIME-Version'] = '1.0'
            if meta.get('repond') in message_ids:
                msg['In-Reply-To'] = message_ids[meta['repond']]
                msg['References'] = message_ids[meta['repond']]
            if meta.get('liste'):
                msg['List-Id'] = '<lettre.demo.example.test>'
                msg['List-Unsubscribe'] = '<mailto:desabonnement@demo.example.test>'
            if not meta.get('sans-authentification') and meta.get('boite') == 'INBOX':
                domaine = str(msg['From']).rsplit('@', 1)[-1].strip('> ')
                msg['Authentication-Results'] = SERVEUR_COURRIEL + '; dkim=pass header.d=%s; spf=pass smtp.mailfrom=%s; dmarc=pass' % (domaine, domaine)
            msg.set_content(corps.replace('{AUDIENCE}', jour_audience.strftime(t['format_date'])))
            if meta.get('piece'):
                nom, _, mid = meta['piece'].partition('|')
                titre, lignes, d = pieces[(mid, nom)]
                msg.add_attachment(pdf(titre, [t['pdf_dossier'] % (d['id'], d['titre'])] + lignes, t['pdf_mention']), maintype='application', subtype='pdf', filename=nom)
            drapeaux = set((meta.get('drapeaux') or '').split())
            cible = 'INBOX' if meta.get('boite') == 'INBOX' else envoyes
            uid = boite.ajouter(cible, msg.as_bytes(policy=policy.SMTP), drapeaux, quand)
            ids[meta.get('id')] = (cible, uid, str(msg['Subject']), str(msg['From']), quand)
    return ids


def _semer_bureau(desk, cab, jour, ids):
    from . import agenda5634, crm5676, dossiers5697, fiche5698, improvements36, modele5651, relecture56100
    from .common import load_matters, private_json
    from .dav import DAV
    from .index import DocumentIndex
    acteur = ('cabinet', 'administrateur')
    from . import web490      # envoi autorisé comme le ferait l'administratrice (Paramètres › Envoi), par la couche web d'envoi
    web490.handle(desk, 'send/settings', {'enabled': True, 'confirm': 'yes'})
    web490.handle(desk, 'send/options', {'all_matters': True, 'confirm': 'yes'})
    for cle, valeur in (('cabinet5631:cabinet', cab['cabinet']['nom']), ('live430:enabled', False), ('demo56107:jour', jour.isoformat())):
        desk.setting(cle, valeur)
    _identite(desk, cab['cabinet'], cab['langue'])
    # Alerte anti-fraude réglée comme le ferait le cabinet (Paramètres) : serveur de messagerie de la démo (en-têtes Authentication-Results
    # « pass ») et domaines officiels de SES juridictions, plateformes et ordre fictifs ; les règles de ``fraude56106`` restent inchangées.
    from . import fraude56106
    desk.setting(fraude56106.REGLAGES, {'version': 1, 'ia_second_avis': False, 'authserv_id': [SERVEUR_COURRIEL],
                                        'domaines_officiels': list(cab['cabinet'].get('domaines_officiels') or []), 'domaines_gratuits': []})
    fraude56106.oublier()
    from .i18n5631 import save_preference   # langue de l'interface de l'instance = langue de la démo (langue du cabinet, pas une préférence figée)
    save_preference(desk.c, 'cabinet', cab['langue'], cabinet=True)
    matters = {m['id']: m for m in load_matters(desk.c)}
    index = DocumentIndex(desk.c['state_dir'])
    dav = DAV(desk.c['nextcloud'])
    for d in cab['dossiers']:
        m = matters[d['id']]
        dossiers5697.importer_un(desk, m)
        desk.db.execute('UPDATE dossiers5697 SET juridiction=?, rg=?, nature=?, resume=?, responsable=? WHERE id=?',
                        (d['juridiction'], d['rg'], d['nature'], d['resume'], cab['cabinet']['avocate'], d['id']))
        desk.db.commit()
        for n, adverse in enumerate(d['adverses']):
            fiche5698.ajouter_partie(desk, acteur, d['id'], {'nom': adverse, 'role': 'adverse'}, 'demo-partie-%s-%d' % (d['id'], n))
        modele5651.add_party(desk, d['id'], 'cabinet', d['client'], client=True)
        for adverse in d['adverses']:
            modele5651.add_party(desk, d['id'], 'cabinet', adverse,
                                 counsel=next((c['name'] for c in d['correspondants'] if c['role'] == 'confrere_adverse'), ''))
        if d['rg']:
            modele5651.save_procedure(desk, d['id'], 'cabinet', {'juridiction': d['juridiction'], 'chambre': d['chambre'], 'rg': d['rg'],
                                                                 'nature': cab['textes']['nature_procedure'], 'stade': cab['textes']['stade'], 'statut': 'active', 'reference': True})
        for decalage, texte in d['chronologie']:
            modele5651.add_assertion(desk, d['id'], 'cabinet', {'kind': 'fait', 'texte': texte, 'date': jour_ouvre(jour, decalage).isoformat()})
        index.sync(dav, m, desk.c.get('documents') or {}, budget=50)
    fiche5698.migrer_instances(desk)
    for d in cab['dossiers']:
        modele5651.rebuild(desk, d['id'], force=True)
    # Agenda (audiences et rendez-vous de la semaine) et tâches
    for a in cab['agenda']:
        j = jour_ouvre(jour, a['jour'])
        debut = datetime.combine(j, dtime.fromisoformat(a['heure']), PARIS)
        agenda5634.create(desk, {'title': a['titre'], 'kind': a['nature'], 'start': debut.isoformat(), 'duration_minutes': a['minutes'],
                                 'matter': a['dossier'], 'location': a['lieu'], 'description': cab['textes']['evenement']}, owner='cabinet', source='demo')
    for t in cab['taches']:
        improvements36.create_task(desk, t['titre'], t['dossier'], jour_ouvre(jour, t['jour']).isoformat())
    from . import echeances450
    for x in cab['echeances']:
        try:
            echeances450.create_deadline(desk, x['dossier'], x['regle'], jour_ouvre(jour, x['depart']).isoformat(), origin='manuel', status='confirmee',
                                         note=cab['textes']['echeance'])
        except Stop:
            pass
    for o in cab['opportunites']:
        crm5676.create(desk, 'cabinet', {**{k: v for k, v in o.items() if k != 'jour'}, 'next_date': jour_ouvre(jour, o['jour']).isoformat(),
                                         'notes': cab['textes']['opportunite']})
    # À relire : projets de réponse (forme des producteurs) et notes de l'IA
    state = Path(desk.c['state_dir'])
    compte = '%s@%s' % (desk.c['mail']['username'], desk.c['mail']['host'])
    for n, p in enumerate(cab['a_relire']):
        boite_nom, uid, objet, expediteur, quand = ids[p['courriel']]
        from .common import digest
        cle = digest('|'.join([compte, boite_nom, '101', str(uid)]))
        cree = (datetime.now(timezone.utc) - timedelta(minutes=20 + 7 * n)).isoformat()
        data = {'result': {'body': marquer(p['corps']), 'requires_decision': True, 'limits': [cab['textes']['limite_projet']]},
                'verification': None, 'matter': p['dossier'], 'matter_path': '', 'key': cle, 'source_hash': 'demo', 'created_at': cree,
                'recipients': p['destinataires'], 'sources': [{'id': 'incoming', 'title': cab['textes']['courriel_recu']}]}
        private_json(state / 'reports' / (cle + '.json'), {'subject': objet, 'sender': expediteur, 'matter': p['dossier'], 'received_at': quand.isoformat(),
                                                               'source_uid': str(uid), 'source_mailbox': boite_nom})   # « Message reçu » : courriel relu dans la boîte (même clé)
        desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES(?,?,?)', (cle, json.dumps(data, ensure_ascii=False), cree))
        desk.db.commit()
        relecture56100.projeter_cle(desk, cle)
    from . import relecture56101
    for n, note in enumerate(cab['notes_ia']):
        maintenant = (datetime.now(timezone.utc) - timedelta(minutes=5 + n)).isoformat()
        rid = 'dl-demo-%02d-%s' % (n, hashlib.sha256((note['titre'] + maintenant).encode()).hexdigest()[:16])
        desk.db.execute('INSERT INTO delta56100_resultats(id,cabinet,event_id,dossier,piece_id,version_id,sha256,baseline_id,partie,titre,etat,note,created,updated) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (rid, 'cabinet', 'ev-' + rid, note['dossier'], 'piece-demo-%d' % n, 'v-demo-%d' % n, 'd' * 64,
                                                               'base-demo', note['partie'], note['titre'], 'a_relire', marquer(note['note']), maintenant, maintenant))
        desk.db.commit()
        relecture56101.projeter_note(desk, rid)


def _identite(desk, cab, langue='fr'):
    """Profil du cabinet fictif et logo généré (révisions du profil, comme une saisie de l'administratrice)."""
    from . import cabinet_profile5689 as profil, identite56106
    profil.ensure_schema(desk)
    row = profil._ligne(desk)
    profil.enregistrer(desk, 'cabinet', 'administrateur', {'revision': row['revision'], 'nom_cabinet': cab['nom'], 'barreau': cab['barreau'],
                                                           'toque': cab['toque'], 'adresse': cab['adresse'], 'telephone': cab['telephone'],
                                                           'activites': cab['activites'], 'langue': langue})
    row = profil._ligne(desk)
    identite56106.deposer(desk, 'cabinet', row['revision'], base64.b64encode(logo_png()).decode('ascii'))


# ================================================================================================ lancement (lanceur poste, répartiteur)
def jeton_de_session(chemin, suite='/aujourdhui'):
    """Adresse (relative au préfixe) qui ouvre la session de la démo : jeton de lancement à usage unique (``poste_session``)."""
    from urllib.parse import urlencode
    from .poste_session import new_launch_token
    racine = _racine(chemin)
    if _lire_marqueur(racine) is None:
        raise Stop('demo_isolement_refuse')
    if not str(suite).startswith('/') or str(suite).startswith('//'):
        suite = '/aujourdhui'
    return '/poste-session?' + urlencode({'jeton': new_launch_token(racine / 'ui-auth.json'), 'suite': suite})


def environnement(chemin):
    racine = _racine(chemin)
    return {'AXIORHUB_DATA_DIR': str(racine), 'AXIORHUB_AUTH_STATE': str(racine / 'auth'), 'AXIORHUB_DEMO': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}


def commandes(chemin, port=PORT, python=None):
    """Processus de service d'une instance (sans fenêtre) : interface et travailleur. Le répertoire de travail est la racine d'AxiorHub."""
    racine = _racine(chemin)
    code = Path(__file__).resolve().parents[1]
    py = python or sys.executable
    cfg = ['--config', str(racine / 'config.json')]
    return {'interface': [py, str(code / 'web.py'), 'serve', *cfg, '--auth', str(racine / 'ui-auth.json'), '--port', str(int(port))],
            'travailleur': [py, str(code / 'web.py'), 'worker', *cfg], 'env': environnement(racine), 'cwd': str(code)}


def commande_lanceur():
    """Commande qui ouvre la démo sur ce poste (« Essayer la démo » d'une installation neuve) : exécutable Windows ou ``poste.py --demo``."""
    if getattr(sys, 'frozen', False):
        gui = Path(sys.executable).with_name(APP_WINDOWS + '.exe')
        if gui.is_file():
            return [str(gui), '--demo']
    return [sys.executable, str(Path(__file__).resolve().parents[1] / 'poste.py'), '--demo']


# ================================================================================================ interface : bandeau, visite, routes
def bandeau_html(prefix, cfg):
    """Bandeau discret et permanent, seulement en démo, dans le menu latéral, dans le flux (servi par
    ``shell501.sidebar_html`` : toutes les pages) ; la visite guidée et ses étapes sont décrites dans ``v56107-demo.js``. Démo hébergée
    (``AXIORHUB_DEMO_HOTE=1``, lot H) : « Nouvelle démo » mène à ``/_demo/session`` du répartiteur."""
    if not actif(cfg or {}):
        return ''
    p = e(prefix)
    try:
        premier = _cabinet(langue_de(cfg['demo56107']['racine']))['dossiers'][0]['id']
    except (Stop, OSError, ValueError, KeyError, IndexError):
        premier = 'DOS-001'
    return ('<div class="demo107" id="demo107" role="region" aria-label="Mode démonstration" data-demo107 data-demo107-dossier="' + e(premier) + '">'
            '<span class="demo107-marque" aria-hidden="true">●</span><strong>Mode démonstration — données fictives</strong>'
            '<span class="demo107-detail">Aucun envoi, aucune connexion extérieure, aucun modèle d’IA : tout est simulé sur ce poste.</span>'
            '<span class="demo107-actions"><button type="button" class="demo107-bouton" data-demo107-visite>Visite guidée</button>'
            + ('<a class="demo107-bouton" href="/_demo/session" data-demo107-nouvelle>Nouvelle démo</a>' if os.environ.get('AXIORHUB_DEMO_HOTE') == '1' else '') +
            '<button type="button" class="demo107-bouton" data-demo107-recommencer>Recommencer la démo</button>'
            '<button type="button" class="demo107-bouton" data-demo107-quitter>Quitter</button></span>'
            '<span class="demo107-statut" data-demo107-statut role="status" aria-live="polite"></span></div>'
            '<script defer src="' + p + '/static/v56107-demo.js"></script>')


def carte_essai_html(prefix, cfg, path, args):
    """« Essayer la démo » sur l'écran d'accueil d'une installation neuve du poste (Paramètres › Connexions, premier lancement). Servie en
    ``<template>`` après le menu latéral (tous les gabarits de page) ; ``v56107-demo.js`` la place en tête du contenu."""
    if actif(cfg or {}) or not str(path).startswith('/parametres') or str((args or {}).get('premier') or '') != '1' or not _poste():
        return ''
    return ('<template id="demo107-essai-modele"><aside class="demo107-essai" data-demo107-essai><h2>Découvrir AxiorHub avant de le configurer</h2>'
            '<p>La démo ouvre un cabinet fictif prérempli (courriels, dossiers, agenda, projets à relire) dans une fenêtre séparée, avec ses propres '
            'données : rien n’est lu ni modifié dans votre installation, rien n’est envoyé.</p>'
            '<button type="button" class="demo107-bouton demo107-principal" data-demo107-lancer>Essayer la démo</button>'
            '<span class="demo107-statut" data-demo107-statut role="status" aria-live="polite"></span></aside></template>'
            '<script defer src="' + e(prefix) + '/static/v56107-demo.js"></script>')


def _poste():
    try:
        return bool(json.loads(Path(os.environ.get('AXIORHUB_AUTH_FILE') or '').read_text(encoding='utf-8')).get('poste'))
    except (OSError, ValueError, TypeError):
        return False


def allowed(role, method, api):
    """``standalone_auth.allowed`` : lecture de l'état pour tous ; recommencer, quitter et lancer : administrateur."""
    route = str(api or '').split('?', 1)[0]
    route = route[len(API):] if route.startswith(API) else route
    if method in ('GET', 'HEAD'):
        return route in ROUTES_GET
    return route in ROUTES_POST and role == 'administrateur'


def _out(valeur, statut='200 OK'):
    return {'status': statut, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(valeur, ensure_ascii=False),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _out({'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


_RECUS = {}


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    _, role = actor(env)
    route = name.split('?', 1)[0][len(API):]
    if route not in ROUTES_GET + ROUTES_POST:
        raise Stop('route_inconnue')
    demo = actif(desk)
    if method in ('GET', 'HEAD'):
        if route not in ROUTES_GET or not demo:
            raise Stop('route_inconnue')                    # hors démo : même 404 neutre qu'une route absente
        marque = _lire_marqueur(desk.c['demo56107']['racine']) or {}
        return _out({'demo': True, 'jour': marque.get('jour', ''), 'version': marque.get('version', 0), 'refus_transport': _ETAT['refus']})
    if method != 'POST':
        raise Stop('route_inconnue')
    if route != 'lancer' and not demo:
        raise Stop('demo_inactive')
    if not allowed(role, 'POST', route):
        raise Stop('role_insuffisant')
    from .web440 import check_post
    data = check_post(env, auth)
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_idempotence_requise')
    deja = _RECUS.get((route, cle))
    if deja is not None:
        return _out(deja)
    if route == 'reinitialiser':
        reinitialiser(desk.c['demo56107']['racine'])
        out = {'fait': True, 'message': 'Démo remise à neuf : données fictives et dates d’aujourd’hui.'}
    elif route == 'quitter':
        _ecrire_prive(Path(desk.c['demo56107']['racine']) / 'quitter.demande', datetime.now(timezone.utc).isoformat(timespec='seconds') + '\n')
        out = {'fait': True, 'message': 'Démonstration terminée : vous pouvez fermer cette fenêtre. Rien n’a été envoyé ni conservé ailleurs.'}
    else:
        if demo:
            raise Stop('demo_deja_active')
        if not auth.get('poste'):
            raise Stop('lancement_demo_indisponible')
        import subprocess
        extra = {'start_new_session': True} if os.name == 'posix' else {'creationflags': getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
        subprocess.Popen(commande_lanceur(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **extra)
        out = {'fait': True, 'message': 'La démo s’ouvre dans une fenêtre séparée (quelques secondes). Votre installation n’est pas modifiée.'}
    _RECUS[(route, cle)] = out
    if len(_RECUS) > 200:
        _RECUS.clear()
    return _out(out)

"""5.6.110 (lot S) : « Installer le serveur de documents sur ce poste » — AxiorHub pilote Docker à la place de l'avocat.

Référence fonctionnelle : ``docs/INSTALLATION-EDITEUR-DOCUMENTS.md``, sections B (Windows 11) et C (Ubuntu 24.04), essayées réellement
avec Euro-Office en 5.6.108. Les commandes et les réglages en sont repris tels quels :

- image ``ghcr.io/euro-office/documentserver`` **figée par son empreinte** (celle du guide) ; téléchargée seulement si elle manque
  (``docker pull <image>@sha256:…``) ; le lancement porte ``--pull never`` : Docker ne télécharge jamais rien en douce ;
- fichier d'environnement privé ``<state_dir>/documents56110/documents.env`` (droits 600) avec ``JWT_ENABLED=true`` et ``JWT_SECRET``
  (les deux seules variables du guide) ; secret fabriqué par AxiorHub (``secrets.token_urlsafe``), jamais affiché, jamais journalisé,
  rangé dans AxiorHub comme le secret existant du serveur de documents (``office440.save_settings`` → ``office440.jwt``) ;
- ``docker run -d --name axiorhub-documents --label axiorhub=documents --restart unless-stopped -p 127.0.0.1:<port>:80
  [--add-host host.docker.internal:host-gateway sous Linux] --env-file <fichier> <image>`` ; port 8080, sinon le premier libre de 8080
  à 8099, utilisé partout (adresse du serveur, test) ;
- réglages d'AxiorHub remplis seuls (moteur Euro-Office, ``http://127.0.0.1:<port>``, secret, adresse de cette interface vue par le
  serveur de documents ``http://host.docker.internal:<port du poste><préfixe>``), puis le test existant en quatre étapes
  (``office440.diagnostic(..., reach=True)``).

Sécurité : tout appel à ``docker`` passe par ``_lancer`` (remplacée dans les essais : aucun vrai Docker), avec des arguments **en
liste**, un délai sur chaque commande et **aucune valeur venue du navigateur** (nom, étiquette, image et plage de ports figés ici ; le
navigateur n'envoie que des cases à cocher, la clé d'idempotence et la version). Un conteneur, une image ou un réseau qu'AxiorHub n'a
pas créés ne sont jamais modifiés : le conteneur porte l'étiquette ``axiorhub=documents`` ; un conteneur du même nom sans elle n'est
jamais touché ; les actions visent l'identifiant relu juste avant ; l'image n'est retirée que si AxiorHub l'a téléchargée lui-même.
Carte et routes : poste seulement (interface en écoute locale, ``auth['poste']``) ; écritures réservées à l'administrateur, CSRF, clé
d'idempotence, version (409), audit sans secret. Sur un serveur du cabinet, rien n'apparaît : le guide reste la voie.

Ubuntu : le relais local du guide (section C6, ``systemd-socket-proxyd``) demande ``sudo`` : AxiorHub ne le crée pas ; la carte le dit
et donne la section du guide. Sans ce relais, la quatrième étape du test (« le serveur de documents joint AxiorHub ») échoue.
"""
import hashlib
from html import escape as e
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .common import Stop

NOM = 'axiorhub-documents'
DEPOT = 'ghcr.io/euro-office/documentserver'
EMPREINTE = 'sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2'
IMAGE = DEPOT + '@' + EMPREINTE
ETIQUETTE_CLE, ETIQUETTE_VALEUR = 'axiorhub', 'documents'
ETIQUETTE = ETIQUETTE_CLE + '=' + ETIQUETTE_VALEUR
PORTS = tuple(range(8080, 8100))
LIEN_DOCKER = 'https://www.docker.com/products/docker-desktop/'
COMMANDE_UBUNTU = 'sudo apt update && sudo apt install -y docker.io && sudo usermod -aG docker "$USER"'   # guide, section C1
KEY = 'documents56110:installation'
KEY_PRECEDENT = 'documents56110:precedent'
KEY_TEST = 'documents56110:test'
PREFIXE = 'm56110/documents/'
ECRITURES = ('installer', 'demarrer', 'arreter', 'tester', 'desinstaller', 'revenir')
LECTURES = ('etat',)
DOSSIER = 'documents56110'
DELAIS = {'version': 15, 'inspect': 20, 'pull': 3600, 'run': 180, 'start': 90, 'stop': 90, 'rm': 90, 'rmi': 600}
DELAI_DEMARRAGE = 300                    # secondes d'attente de /healthcheck (le guide : « une à deux minutes »)
PAS = 3                                  # secondes entre deux essais de /healthcheck
MAX_POST = 4_000
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
ETAPES_INSTALLATION = ('image', 'secret', 'demarrage', 'attente', 'reglages', 'test')
ETAPES_DESINSTALLATION = ('arret', 'retrait', 'image', 'reglages')
LIBELLES_ETAPES = {
    'image': 'Image du serveur de documents', 'secret': 'Secret partagé (fichier privé)', 'demarrage': 'Démarrage du conteneur',
    'attente': 'Attente de la réponse du serveur', 'reglages': 'Réglages d’AxiorHub', 'test': 'Test de la connexion',
    'arret': 'Arrêt du conteneur', 'retrait': 'Retrait du conteneur et de son fichier d’environnement',
}
ETATS_ETAPE = {'attente': 'à faire', 'en_cours': 'en cours', 'fait': 'fait', 'echec': 'en échec', 'saute': 'sans objet'}
MESSAGES = {
    'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Réservé à l’administrateur du cabinet.',
    'version_perimee': 'Les réglages ont changé entre-temps : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'cle_reutilisee': 'Cette demande a déjà été utilisée pour une autre action : rechargez la page.',
    'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Demande refusée.',
    'stockage_indisponible': 'Dossier des données du cabinet indisponible.',
    'reserve_au_poste': 'Réservé à AxiorHub installé sur un poste : sur un serveur, suivez le guide d’installation du serveur de documents.',
    'docker_absent': 'Docker n’est pas installé sur ce poste : installez-le d’abord.',
    'docker_arrete': 'Le moteur Docker est arrêté : ouvrez Docker Desktop (ou démarrez le service docker sous Ubuntu), puis réessayez.',
    'docker_permission': 'Docker refuse l’accès à votre compte : ajoutez-le au groupe docker, fermez la session et rouvrez-la.',
    'docker_delai_depasse': 'Docker n’a pas répondu dans le délai prévu : la commande a été arrêtée. Réessayez dans un instant.',
    'docker_en_echec': 'Docker a refusé la commande : rien d’autre n’a été modifié.',
    'image_introuvable': 'Image du serveur de documents introuvable chez ghcr.io (empreinte figée) : rien n’a été installé.',
    'reseau_coupe': 'Téléchargement de l’image impossible (réseau coupé ou registre injoignable) : réessayez quand la connexion est revenue.',
    'disque_plein': 'Place insuffisante sur le disque pour l’image (plusieurs gigaoctets) : libérez de l’espace, puis réessayez.',
    'port_pris': 'Aucun port libre de 8080 à 8099 sur ce poste : fermez le programme qui les occupe, puis réessayez.',
    'conteneur_etranger': 'Un conteneur nommé axiorhub-documents existe déjà sans l’étiquette d’AxiorHub (installé à la main, par exemple avec le guide) : '
                          'AxiorHub n’y touche pas. Gérez-le avec les commandes du guide (section E) ou retirez-le vous-même.',
    'conteneur_absent': 'Le serveur de documents n’est pas installé sur ce poste.',
    'serveur_arrete': 'Le conteneur du serveur de documents s’est arrêté pendant le démarrage : consultez son journal (docker logs axiorhub-documents).',
    'serveur_sans_reponse': 'Le serveur de documents ne répond pas au bout de cinq minutes : il démarre peut-être encore. Cliquez sur « Tester » dans quelques minutes.',
    'reglages_existants': 'Des réglages d’un autre serveur de documents sont déjà enregistrés : cochez « Remplacer les réglages actuels » pour confirmer. '
                          'Ils sont gardés pour pouvoir y revenir.',
    'reglages_refuses': 'AxiorHub n’a pas pu enregistrer les réglages du serveur de documents : rien n’a été remplacé.',
    'tache_en_cours': 'Une installation ou une désinstallation est déjà en cours : attendez sa fin.',
    'test_en_echec': 'Serveur de documents installé, mais le test signale un problème : lisez le détail dans la carte.',
    'pas_de_precedent': 'Aucun réglage précédent à remettre.',
    'image_utilisee': 'L’image est encore utilisée par un autre conteneur : elle est gardée.',
    'deja_en_marche': 'Le serveur de documents est déjà en marche.',
    'deja_arrete': 'Le serveur de documents est déjà arrêté.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'reserve_au_poste': '403 Forbidden', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict',
           'tache_en_cours': '409 Conflict', 'reglages_existants': '409 Conflict', 'conteneur_etranger': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large', 'stockage_indisponible': '503 Service Unavailable',
           'docker_absent': '503 Service Unavailable', 'docker_arrete': '503 Service Unavailable', 'docker_permission': '503 Service Unavailable',
           'docker_delai_depasse': '504 Gateway Timeout', 'docker_en_echec': '502 Bad Gateway', 'image_introuvable': '502 Bad Gateway',
           'reseau_coupe': '502 Bad Gateway', 'port_pris': '409 Conflict', 'image_utilisee': '409 Conflict',
           'serveur_sans_reponse': '504 Gateway Timeout', 'serveur_arrete': '502 Bad Gateway'}
# Libellés de l'écran (traduits en anglais, allemand et espagnol dans agent/locales/ecrans_*.json)
TEXTES = {
    'titre': 'Serveur de documents sur ce poste',
    'intro': 'AxiorHub peut installer et régler pour vous le serveur de documents (Euro-Office) dans Docker, sur ce poste. Il sert à modifier les documents dans AxiorHub.',
    'docker_absent': 'Docker n’est pas installé. Docker fait tourner le serveur de documents à l’écart du reste de l’ordinateur. AxiorHub ne l’installe pas lui-même.',
    'lien_docker': 'Télécharger Docker Desktop (site officiel de Docker)',
    'apres_docker': 'Installez-le avec l’option WSL 2, ouvrez-le, puis cliquez sur « Vérifier de nouveau ».',
    'ubuntu_docker': 'Sous Ubuntu 24.04, installez Docker Engine avec cette commande, puis fermez la session et rouvrez-la :',
    'docker_arrete_windows': 'Docker est installé mais arrêté. Ouvrez Docker Desktop, attendez « Engine running », puis cliquez sur « Vérifier de nouveau ».',
    'docker_arrete_linux': 'Docker est installé mais arrêté. Démarrez-le (sudo systemctl start docker), puis cliquez sur « Vérifier de nouveau ».',
    'docker_permission': 'Docker refuse l’accès à votre compte. Ajoutez-le au groupe docker (sudo usermod -aG docker $USER), fermez la session, rouvrez-la, puis cliquez sur « Vérifier de nouveau ».',
    'docker_delai': 'Docker ne répond pas pour le moment. Attendez un instant, puis cliquez sur « Vérifier de nouveau ».',
    'verifier': 'Vérifier de nouveau',
    'docker_pret': 'Docker : prêt.',
    'conteneur_absent': 'Serveur de documents : non installé.',
    'conteneur_arrete': 'Serveur de documents : installé, arrêté.',
    'conteneur_en_marche': 'Serveur de documents : en marche, il démarre encore.',
    'conteneur_sain': 'Serveur de documents : en marche et prêt.',
    'conteneur_etranger': 'Un conteneur nommé axiorhub-documents existe déjà sans l’étiquette d’AxiorHub. AxiorHub n’y touche pas : il se gère avec les commandes du guide (section E).',
    'adresse': 'Adresse du serveur de documents :',
    'reglages_nous': 'Les réglages d’AxiorHub désignent ce serveur.',
    'reglages_pas_nous': 'Les réglages d’AxiorHub désignent un autre serveur de documents.',
    'installer': 'Installer le serveur de documents sur ce poste',
    'reprendre': 'Reprendre l’installation',
    'image_absente': 'L’image sera téléchargée une seule fois : plusieurs gigaoctets.',
    'image_presente': 'L’image est déjà sur ce poste : rien à télécharger.',
    'source': 'Source, image figée par son empreinte (la même que le guide) :',
    'memoire': 'Prévoyez au moins 8 Go de mémoire et 10 Go d’espace disque libre. L’installation prend quelques minutes, puis AxiorHub règle tout seul l’adresse, le secret et le test.',
    'autre_serveur': 'Un autre serveur de documents est déjà réglé dans AxiorHub :',
    'remplacer': 'Remplacer les réglages actuels (ils sont gardés pour pouvoir y revenir)',
    'demarrer': 'Démarrer',
    'arreter': 'Arrêter',
    'tester': 'Tester',
    'desinstaller': 'Désinstaller',
    'revenir': 'Revenir au serveur précédent',
    'precedent': 'Les réglages du serveur de documents utilisé avant cette installation sont gardés.',
    'retirer_image': 'Retirer aussi l’image (plusieurs gigaoctets ; il faudra la télécharger de nouveau pour réinstaller)',
    'image_gardee': 'L’image était déjà sur ce poste avant l’installation : AxiorHub ne la retirera pas.',
    'confirmer_desinstallation': 'Désinstaller le serveur de documents de ce poste ? Les documents restent lisibles en aperçu.',
    'confirmer_revenir': 'Remettre les réglages du serveur de documents utilisé avant cette installation ?',
    'en_cours_installation': 'Installation en cours. Vous pouvez quitter cette page : elle continue.',
    'en_cours_desinstallation': 'Désinstallation en cours.',
    'echec': 'Dernière opération en échec :',
    'reprise': 'Corrigez la cause, puis relancez : les étapes déjà faites ne sont pas refaites.',
    'termine_installation': 'Installation terminée.',
    'termine_desinstallation': 'Désinstallation terminée.',
    'test_ok': 'Dernier test : toutes les étapes sont cochées.',
    'test_ko': 'Dernier test : étapes en échec.',
    'test_detail': 'Le détail de chaque étape s’affiche avec « Tester la connexion », plus bas.',
    'ubuntu_relais': 'Sous Ubuntu, une étape reste à faire une fois, avec sudo : le relais local décrit dans la section C6 du guide. Sans lui, le serveur de documents ne joint pas AxiorHub.',
    'guide': 'Guide pas à pas (commandes manuelles)',
    'indisponible': 'Carte indisponible pour le moment : le guide reste la voie.',
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents56110_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
_VERROU = threading.Lock()               # une seule installation ou désinstallation à la fois par processus
_TACHE = {'en_cours': False, 'action': '', 'etapes': [], 'erreur': '', 'state_dir': None, 'fil': None, 'debut': 0.0, 'fin': 0.0}


def _windows():
    from . import portable
    return portable.WINDOWS


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ================================================================================================ poste
def est_poste(auth):
    """Poste : ``auth['poste']`` (fichier d'authentification du poste) et interface en écoute locale (adresse de bouclage)."""
    if not isinstance(auth, dict) or not auth.get('poste'):
        return False
    try:
        return (urlsplit(str(auth.get('origin') or '')).hostname or '') in ('127.0.0.1', 'localhost', '::1')
    except ValueError:
        return False


def adresse_retour(auth):
    """« Adresse de cette interface vue par le serveur de documents » sur un poste (guide B7 et C8) : port et préfixe du poste."""
    try:
        port = urlsplit(str((auth or {}).get('origin') or 'http://127.0.0.1:8769')).port or 8769
    except ValueError:
        port = 8769
    return 'http://host.docker.internal:%d%s' % (port, (auth or {}).get('prefix', '/agent-courriel'))


def adresse_serveur(port):
    return 'http://127.0.0.1:%d' % int(port)


# ================================================================================================ docker
def executable():
    """Chemin du client ``docker`` : ``PATH``, puis l'emplacement habituel de Docker Desktop (Windows) ou de Docker Engine (Linux)."""
    trouve = shutil.which('docker')
    if trouve:
        return trouve
    if _windows():
        candidats = [Path(os.environ.get('ProgramFiles') or r'C:\Program Files') / 'Docker' / 'Docker' / 'resources' / 'bin' / 'docker.exe']
    else:
        candidats = [Path('/usr/bin/docker'), Path('/usr/local/bin/docker'), Path('/snap/bin/docker')]
    for p in candidats:
        if p.is_file():
            return str(p)
    return None


def _env():
    """Environnement minimal des outils (``portable.tool_env``) et les seules variables propres à Docker."""
    from . import portable
    env = portable.tool_env()
    for k in ('HOME', 'USER', 'XDG_RUNTIME_DIR', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_CONFIG', 'DOCKER_CERT_PATH', 'DOCKER_TLS_VERIFY',
              'HOMEDRIVE', 'HOMEPATH'):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _lancer(args, delai):
    """Lance une commande ``docker`` (remplacée dans les essais : aucun vrai Docker). Arguments en liste, jamais de shell, délai
    obligatoire. Rend ``(code, sortie, erreurs)`` ; client absent : code 127 ; délai dépassé : ``Stop('docker_delai_depasse')``."""
    import subprocess
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise TypeError('arguments en liste de chaînes seulement')
    options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE, 'env': _env(), 'timeout': delai}
    if _windows():
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        p = subprocess.run(args, **options)
    except FileNotFoundError:
        return 127, '', 'docker introuvable'
    except subprocess.TimeoutExpired:
        raise Stop('docker_delai_depasse') from None
    except OSError:
        return 127, '', 'docker impossible à lancer'
    return p.returncode, p.stdout[:400_000].decode('utf-8', 'replace'), p.stderr[:20_000].decode('utf-8', 'replace')


def _docker(*args, delai):
    exe = executable()
    if exe is None:
        raise Stop('docker_absent')
    code, out, err = _lancer([exe] + list(args), delai)
    if code == 127 and not out and 'introuvable' in err:
        raise Stop('docker_absent')
    return code, out, err


def cause(texte):
    """Message d'erreur de Docker → cause lisible (jamais affiché tel quel)."""
    t = str(texte or '').lower()
    if any(m in t for m in ('port is already allocated', 'address already in use', 'ports are not available',
                            'only one usage of each socket address', 'an attempt was made to access a socket')):
        return 'port_pris'
    if 'permission denied' in t and ('docker' in t or 'sock' in t):
        return 'docker_permission'
    if any(m in t for m in ('cannot connect to the docker daemon', 'error during connect', 'is the docker daemon running',
                            'dockerdesktoplinuxengine', 'docker_engine', 'docker desktop is not running')):
        return 'docker_arrete'
    if 'no space left' in t:
        return 'disque_plein'
    if any(m in t for m in ('manifest unknown', 'not found', 'no such image', 'pull access denied', 'name unknown')):
        return 'image_introuvable'
    if any(m in t for m in ('dial tcp', 'i/o timeout', 'no such host', 'tls handshake', 'connection refused', 'network is unreachable',
                            'temporary failure in name resolution', 'context deadline exceeded', 'connection reset')):
        return 'reseau_coupe'
    if 'image is being used' in t or 'conflict: unable to remove' in t or 'image has dependent' in t:
        return 'image_utilisee'
    return 'docker_en_echec'


def etat_docker():
    """``absent``, ``arrete``, ``permission``, ``delai`` ou ``pret`` (``docker version``, délai court) ; jamais d'exception."""
    try:
        code, out, err = _docker('version', '--format', '{{.Server.Version}}', delai=DELAIS['version'])
    except Stop as ex:
        return {'docker': 'absent' if str(ex) == 'docker_absent' else 'delai', 'version': ''}
    if code == 0 and out.strip():
        return {'docker': 'pret', 'version': out.strip()[:40]}
    return {'docker': 'permission' if cause(err) == 'docker_permission' else 'arrete', 'version': ''}


def image_presente():
    code, _, _ = _docker('image', 'inspect', '--format', '{{.Id}}', IMAGE, delai=DELAIS['inspect'])
    return code == 0


def conteneur():
    """Conteneur ``axiorhub-documents`` relu : None s'il n'existe pas ; sinon identifiant, étiquette, état et port publié."""
    code, out, err = _docker('container', 'inspect', NOM, delai=DELAIS['inspect'])
    if code != 0:
        if 'no such' in err.lower() or out.strip() in ('', '[]'):
            return None
        raise Stop(cause(err))
    try:
        c = json.loads(out)[0]
    except (ValueError, IndexError, TypeError, KeyError):
        raise Stop('docker_en_echec') from None
    labels = ((c.get('Config') or {}).get('Labels')) or {}
    etat = c.get('State') or {}
    port = None
    for b in (((c.get('HostConfig') or {}).get('PortBindings') or {}).get('80/tcp') or []):
        try:
            port = int(b.get('HostPort'))
            break
        except (TypeError, ValueError):
            continue
    return {'id': str(c.get('Id') or '')[:80], 'notre': labels.get(ETIQUETTE_CLE) == ETIQUETTE_VALEUR, 'en_marche': bool(etat.get('Running')),
            'statut': str(etat.get('Status') or '')[:20], 'port': port}


def _http(url, delai):
    """GET sur l'adresse de bouclage (remplacée dans les essais). Rend ``(statut, corps)`` ; ``(0, b'')`` si rien ne répond."""
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'AxiorHub'}), timeout=delai) as r:
            return r.status, r.read(1000)
    except urllib.error.HTTPError as ex:
        return ex.code, b''
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b''


def sain(port):
    if not port:
        return False
    statut, corps = _http(adresse_serveur(port) + '/healthcheck', 3)
    return statut == 200 and corps.strip().lower() == b'true'


def _port_libre(port):
    """Personne n'écoute sur 127.0.0.1:<port> et AxiorHub pourrait s'y attacher (remplacée dans les essais)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        if s.connect_ex(('127.0.0.1', int(port))) == 0:
            return False
    except OSError:
        pass
    finally:
        s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pause(secondes):
    time.sleep(secondes)


# ================================================================================================ fichier d'environnement
def base(state_dir):
    return Path(str(state_dir)) / DOSSIER


def chemin_env(state_dir):
    return base(state_dir) / 'documents.env'


def _ecrire_prive(path, texte):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.%s.%s.tmp' % (path.name, secrets.token_hex(4)))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(texte)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def lire_secret(state_dir):
    """Secret du fichier d'environnement (``JWT_SECRET=…``) ; chaîne vide s'il manque ou s'il est illisible."""
    try:
        for ligne in chemin_env(state_dir).read_text(encoding='utf-8').splitlines():
            if ligne.startswith('JWT_SECRET='):
                s = ligne[len('JWT_SECRET='):].strip()
                return s if 12 <= len(s) <= 512 and all(32 < ord(c) < 127 for c in s) else ''
    except OSError:
        pass
    return ''


def ecrire_env(state_dir, secret):
    """Mêmes variables que le guide (B3, C2) : ``JWT_ENABLED=true`` et ``JWT_SECRET``."""
    _ecrire_prive(chemin_env(state_dir), 'JWT_ENABLED=true\nJWT_SECRET=%s\n' % secret)


def retirer_env(state_dir):
    try:
        chemin_env(state_dir).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        raise Stop('stockage_indisponible') from None


# ================================================================================================ réglages
def _defaut():
    return {'version': 0, 'port': None, 'image_tiree': False, 'installe_le': ''}


def installation(desk):
    v = desk.settings(KEY, None)
    out = _defaut()
    if isinstance(v, dict):
        if isinstance(v.get('version'), int) and v['version'] >= 0:
            out['version'] = v['version']
        if isinstance(v.get('port'), int) and v['port'] in PORTS:
            out['port'] = v['port']
        out['image_tiree'] = bool(v.get('image_tiree'))
        out['installe_le'] = str(v.get('installe_le') or '')[:40]
    return out


def _enregistrer(desk, **champs):
    r = installation(desk)
    r.update(champs)
    r['version'] = installation(desk)['version'] + 1
    desk.setting(KEY, r)
    return r


def reglages_office(desk, port=None):
    """Adresse enregistrée du serveur de documents ; ``nous`` : elle désigne ce serveur (port donné) ; ``autre`` : un autre serveur."""
    from . import office440
    cfg = office440.settings(desk)
    serveur = cfg['server_url']
    nous = bool(port) and serveur == adresse_serveur(port)
    precedent = desk.settings(KEY_PRECEDENT, None)
    return {'serveur': serveur, 'nous': nous, 'autre': bool(serveur) and not nous, 'jwt': cfg['jwt'], 'engine': cfg['engine'],
            'precedent': isinstance(precedent, dict) and bool(precedent.get('server_url'))}


def _chemin_precedent(desk):
    return base(desk.c['state_dir']) / 'precedent.jwt'


def garder_precedent(desk, port):
    """Réglages d'un autre serveur de documents gardés (adresse, adresse de retour, moteur, secret en fichier privé) avant d'être
    remplacés ; jamais réécrits par les nôtres lors d'une reprise."""
    from . import office440
    r = reglages_office(desk, port)
    if not r['autre']:
        return False
    stored = desk.settings('office440:config', {}) or {}
    secret = office440.jwt_secret(desk)
    if secret:
        _ecrire_prive(_chemin_precedent(desk), secret + '\n')
    desk.setting(KEY_PRECEDENT, {'server_url': r['serveur'], 'callback_base': str(stored.get('callback_base') or ''),
                                 'engine': stored.get('engine') if stored.get('engine') in ('onlyoffice', 'euro-office') else 'onlyoffice',
                                 'secret': bool(secret), 'garde_le': _now()})
    desk.audit('documents56110_reglages_gardes', {'serveur': r['serveur'], 'secret': bool(secret)})
    return True


def rendre_precedent(desk):
    """Remet les réglages gardés (« Revenir au serveur précédent », désinstallation) ; rend False s'il n'y en a pas."""
    from . import office440
    p = desk.settings(KEY_PRECEDENT, None)
    if not isinstance(p, dict) or not p.get('server_url'):
        return False
    secret = ''
    if p.get('secret'):
        try:
            secret = _chemin_precedent(desk).read_text(encoding='utf-8').strip()
        except OSError:
            secret = ''
    try:
        office440.save_settings(desk, p['server_url'], secret, p.get('callback_base', ''), p.get('engine', 'onlyoffice'))
        if not secret:
            office440.clear_secret(desk)
    except Stop:
        raise Stop('reglages_refuses') from None
    try:
        _chemin_precedent(desk).unlink()
    except OSError:
        pass
    desk.setting(KEY_PRECEDENT, {})
    desk.audit('documents56110_reglages_rendus', {'serveur': p['server_url'], 'secret': bool(secret)})
    return True


def appliquer_reglages(desk, port, secret, auth):
    """Remplit seul les réglages d'AxiorHub (guide, section D) : Euro-Office, http://127.0.0.1:<port>, secret, adresse de retour."""
    from . import office440
    try:
        office440.save_settings(desk, adresse_serveur(port), secret, adresse_retour(auth), 'euro-office')
    except Stop:
        raise Stop('reglages_refuses') from None


def effacer_nos_reglages(desk, port):
    from . import office440
    if reglages_office(desk, port)['nous']:
        office440.save_settings(desk, '', '', '', 'euro-office')
        office440.clear_secret(desk)
        return True
    return False


# ================================================================================================ état
def tache(state_dir):
    t = _TACHE if _TACHE.get('state_dir') == str(state_dir or '') else None
    if not t:
        return {'en_cours': False, 'action': '', 'etapes': [], 'erreur': '', 'message': ''}
    return {'en_cours': bool(t['en_cours']), 'action': t['action'], 'etapes': [dict(x) for x in t['etapes']], 'erreur': t['erreur'],
            'message': MESSAGES.get(t['erreur'], '') if t['erreur'] else '',
            'duree': round((t['fin'] or time.monotonic()) - t['debut'], 1) if t['debut'] else 0}


def etat(desk, auth, docker=True):
    """État lu sans effet de bord : poste ou non, Docker, conteneur (absent, étranger, arrêté, en marche, sain), image, réglages,
    tâche en cours. Sur un serveur, aucun appel à Docker."""
    state_dir = desk.c.get('state_dir')
    out = {'poste': est_poste(auth), 'systeme': 'windows' if _windows() else 'linux', 'tache': tache(state_dir)}
    if not out['poste'] or not docker:
        return out
    inst = installation(desk)
    out.update(etat_docker())
    out.update({'conteneur': 'inconnu', 'port': inst['port'], 'image': None, 'installation': inst})
    if out['docker'] == 'pret':
        try:
            c = conteneur()
            if c is None:
                out['conteneur'] = 'absent'
            elif not c['notre']:
                out['conteneur'] = 'etranger'
            else:
                out['port'] = c['port'] or inst['port']
                out['conteneur'] = 'arrete' if not c['en_marche'] else ('sain' if sain(out['port']) else 'en_marche')
            out['image'] = image_presente()
        except Stop as ex:
            out['erreur_docker'] = str(ex).split(':')[0]
    out['reglages'] = reglages_office(desk, out['port'] if out.get('conteneur') in ('arrete', 'en_marche', 'sain') else inst['port'])
    t = desk.settings(KEY_TEST, None)
    out['test'] = t if isinstance(t, dict) and isinstance(t.get('total'), int) else None
    return out


# ================================================================================================ tâche d'arrière-plan
def _debut(state_dir, action, etapes):
    _TACHE.update({'en_cours': True, 'action': action, 'etapes': [{'id': x, 'etat': 'attente', 'duree': 0} for x in etapes], 'erreur': '',
                   'state_dir': str(state_dir or ''), 'debut': time.monotonic(), 'fin': 0.0})


def _etape(ident, etat_, duree=None):
    for x in _TACHE['etapes']:
        if x['id'] == ident:
            x['etat'] = etat_
            if duree is not None:
                x['duree'] = round(duree, 1)


def _executer(etapes, fonctions):
    """Exécute les étapes dans l'ordre ; la première en échec arrête tout (état et cause gardés pour la carte)."""
    for ident in etapes:
        _etape(ident, 'en_cours')
        t0 = time.monotonic()
        try:
            r = fonctions[ident]()
        except Stop as ex:
            _etape(ident, 'echec', time.monotonic() - t0)
            raise Stop(str(ex).split(':')[0]) from None
        _etape(ident, 'saute' if r == 'saute' else 'fait', time.monotonic() - t0)


def _desk(config):
    from .desk import Desk
    return Desk(config)


def _test(desk, auth):
    """Test existant en quatre étapes (le serveur répond, api.js est joignable, le secret est accepté, le serveur de documents joint
    AxiorHub) ; résumé gardé (noms des étapes, sans détail ni secret)."""
    from . import office440
    checks = office440.diagnostic(desk, dict(auth or {}), reach=True)
    echecs = [str(c.get('name') or '')[:80] for c in checks if not c.get('ok')]
    resume = {'total': len(checks), 'reussies': len(checks) - len(echecs), 'echecs': echecs, 'le': _now()}
    desk.setting(KEY_TEST, resume)
    return resume


def _installer(ctx):
    config, auth, state_dir = ctx['config'], ctx['auth'], ctx['config']['state_dir']
    etat_ = {'port': None, 'secret': '', 'nouveau': False, 'image_tiree': False}

    def image():
        if image_presente():
            return 'saute'
        code, _, err = _docker('pull', IMAGE, delai=DELAIS['pull'])
        if code != 0:
            raise Stop(cause(err) if cause(err) != 'docker_en_echec' else 'image_introuvable')
        if not image_presente():
            raise Stop('image_introuvable')
        etat_['image_tiree'] = True

    def secret():
        s = lire_secret(state_dir)
        if s:
            etat_['secret'] = s
            return 'saute'
        etat_['secret'], etat_['nouveau'] = secrets.token_urlsafe(32), True
        try:
            ecrire_env(state_dir, etat_['secret'])
        except OSError:
            raise Stop('stockage_indisponible') from None

    def demarrage():
        c = conteneur()
        if c is not None and not c['notre']:
            raise Stop('conteneur_etranger')
        if c is not None and not etat_['nouveau'] and c['port']:
            etat_['port'] = c['port']
            if c['en_marche']:
                return 'saute'
            code, _, err = _docker('start', c['id'], delai=DELAIS['start'])
            if code != 0:
                raise Stop(cause(err))
            return None
        if c is not None:            # notre conteneur, mais secret refait : il est recréé avec le nouveau fichier
            _retirer_conteneur(c)
        essais = [p for p in PORTS if _port_libre(p)]
        for port in essais[:3]:
            args = ['run', '-d', '--name', NOM, '--label', ETIQUETTE, '--restart', 'unless-stopped', '--pull', 'never',
                    '-p', '127.0.0.1:%d:80' % port]
            if not _windows():
                args += ['--add-host', 'host.docker.internal:host-gateway']
            args += ['--env-file', str(chemin_env(state_dir)), IMAGE]
            code, _, err = _docker(*args, delai=DELAIS['run'])
            if code == 0:
                etat_['port'] = port
                return None
            motif = cause(err)
            reste = conteneur()          # conteneur créé mais non démarré (port pris) : retiré s'il est bien le nôtre
            if reste is not None and reste['notre']:
                _retirer_conteneur(reste)
            if motif != 'port_pris':
                raise Stop(motif)
        raise Stop('port_pris')

    def attente():
        fin = time.monotonic() + DELAI_DEMARRAGE
        while True:
            if sain(etat_['port']):
                return None
            c = conteneur()
            if c is None or not c['en_marche']:
                raise Stop('serveur_arrete')
            if time.monotonic() >= fin:
                raise Stop('serveur_sans_reponse')
            _pause(PAS)

    def reglages():
        d = _desk(config)
        try:
            garder_precedent(d, etat_['port'])
            appliquer_reglages(d, etat_['port'], etat_['secret'], auth)
            avant = installation(d)
            _enregistrer(d, port=etat_['port'], image_tiree=bool(avant['image_tiree'] or etat_['image_tiree']), installe_le=_now())
        finally:
            d.db.close()

    def test():
        d = _desk(config)
        try:
            ctx['resume'] = _test(d, auth)
        finally:
            d.db.close()
        if ctx['resume']['echecs']:
            raise Stop('test_en_echec')

    _executer(ETAPES_INSTALLATION, {'image': image, 'secret': secret, 'demarrage': demarrage, 'attente': attente, 'reglages': reglages, 'test': test})
    ctx['port'] = etat_['port']


def _retirer_conteneur(c):
    """Arrête puis retire un conteneur **relu juste avant** et porteur de notre étiquette, par son identifiant (jamais par son nom)."""
    if not c or not c.get('notre') or not c.get('id'):
        raise Stop('conteneur_etranger')
    if c.get('en_marche'):
        code, _, err = _docker('stop', '-t', '30', c['id'], delai=DELAIS['stop'])
        if code != 0:
            raise Stop(cause(err))
    code, _, err = _docker('rm', c['id'], delai=DELAIS['rm'])
    if code != 0 and 'no such' not in err.lower():
        raise Stop(cause(err))


def _desinstaller(ctx):
    config, state_dir = ctx['config'], ctx['config']['state_dir']
    memo = {'c': None, 'port': None}

    def arret():
        c = conteneur()
        memo['c'] = c
        if c is None:
            return 'saute'
        if not c['notre']:
            raise Stop('conteneur_etranger')
        memo['port'] = c['port']
        if not c['en_marche']:
            return 'saute'
        code, _, err = _docker('stop', '-t', '30', c['id'], delai=DELAIS['stop'])
        if code != 0:
            raise Stop(cause(err))
        memo['c'] = dict(c, en_marche=False)

    def retrait():
        c = conteneur()
        if c is not None:
            if not c['notre'] or (memo['c'] and c['id'] != memo['c']['id']):
                raise Stop('conteneur_etranger')
            _retirer_conteneur(c)
        retirer_env(state_dir)

    def image():
        if not ctx.get('retirer_image') or not ctx.get('image_tiree'):
            return 'saute'
        if not image_presente():
            return 'saute'
        code, _, err = _docker('image', 'rm', IMAGE, delai=DELAIS['rmi'])
        if code != 0:
            raise Stop(cause(err) if cause(err) in ('image_utilisee', 'docker_arrete', 'docker_permission') else 'docker_en_echec')

    def reglages():
        d = _desk(config)
        try:
            port = memo['port'] or installation(d)['port']
            if not rendre_precedent(d) and port:
                effacer_nos_reglages(d, port)
            d.setting(KEY_TEST, {})
            _enregistrer(d, port=None, image_tiree=bool(installation(d)['image_tiree'] and not ctx.get('retirer_image')), installe_le='')
        finally:
            d.db.close()

    _executer(ETAPES_DESINSTALLATION, {'arret': arret, 'retrait': retrait, 'image': image, 'reglages': reglages})


def _fil(action, ctx, owner):
    """Corps du fil d'arrière-plan : étapes, état consultable, audit de la fin (aucun secret, aucune sortie de Docker)."""
    audit, detail = 'documents56110_%s_termine' % action, {}
    try:
        (_installer if action == 'installation' else _desinstaller)(ctx)
        detail = {'port': ctx.get('port'), 'duree': round(time.monotonic() - _TACHE['debut'], 1)}
        if ctx.get('resume'):
            detail['test'] = '%d/%d' % (ctx['resume']['reussies'], ctx['resume']['total'])
    except Stop as ex:
        _TACHE['erreur'] = str(ex).split(':')[0]
        etape = next((x['id'] for x in _TACHE['etapes'] if x['etat'] == 'echec'), '')
        audit, detail = 'documents56110_%s_echec' % action, {'etape': etape, 'motif': _TACHE['erreur']}
        if ctx.get('resume'):
            detail['test'] = '%d/%d' % (ctx['resume']['reussies'], ctx['resume']['total'])
    except Exception:   # noqa: BLE001 — jamais de fil mort silencieux : l'échec est dit dans la carte
        _TACHE['erreur'] = 'docker_en_echec'
        for x in _TACHE['etapes']:
            if x['etat'] == 'en_cours':
                x['etat'] = 'echec'
        audit, detail = 'documents56110_%s_echec' % action, {'motif': 'docker_en_echec'}
    finally:
        _TACHE['en_cours'] = False
        _TACHE['fin'] = time.monotonic()
        _VERROU.release()
    try:
        d = _desk(ctx['config'])
        try:
            d.audit(audit, dict(detail, auteur=str(owner)[:120]))
        finally:
            d.db.close()
    except Exception:   # noqa: BLE001 — l'audit de fin ne doit pas masquer l'état affiché
        pass


def lancer(action, config, auth, owner='cabinet', **options):
    """Une seule tâche à la fois ; le fil met à jour l'état (étapes) interrogé par la carte."""
    if not _VERROU.acquire(blocking=False):
        raise Stop('tache_en_cours')
    _debut(config.get('state_dir'), action, ETAPES_INSTALLATION if action == 'installation' else ETAPES_DESINSTALLATION)
    ctx = dict(options, config=dict(config), auth={k: (auth or {}).get(k) for k in ('origin', 'prefix', 'poste')})
    try:
        fil = threading.Thread(target=_fil, args=(action, ctx, owner), daemon=True, name='documents56110-' + action)
        _TACHE['fil'] = fil
        fil.start()
    except Exception:   # noqa: BLE001 — fil non démarré : verrou rendu
        _TACHE['en_cours'] = False
        _VERROU.release()
        raise Stop('docker_en_echec') from None
    return fil


# ================================================================================================ routes /api440/m56110/documents/*
def allowed(role, method, api):
    """Lecture de l'état et écritures : administrateur seulement (la carte n'est montrée qu'à lui)."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if role != 'administrateur':
        return False
    if method in ('GET', 'HEAD') and reste in LECTURES:
        return True
    return method == 'POST' and reste in ECRITURES


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def message(code):
    return MESSAGES.get(code) or code.replace('_', ' ')


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    brut = str(name)
    reste = brut.split('?', 1)[0][len(PREFIXE):].strip('/')
    if (reste not in LECTURES or method != 'GET') and (reste not in ECRITURES or method != 'POST'):
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(role, method, PREFIXE + reste):
        return _json({'error': 'role_insuffisant', 'message': MESSAGES['role_insuffisant']}, STATUTS['role_insuffisant'])
    if not est_poste(auth):
        return _json({'error': 'reserve_au_poste', 'message': MESSAGES['reserve_au_poste']}, STATUTS['reserve_au_poste'])
    if method == 'GET':
        seule_tache = 'tache=1' in (brut.split('?', 1)[1] if '?' in brut else '') or str((args or {}).get('tache') or '') == '1'
        return _json(etat(desk, auth, docker=not seule_tache))
    try:
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth)
        return _json(ecrire(desk, owner, reste, data, auth))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': message(code)}, STATUTS.get(code, '400 Bad Request'))


def _recu(desk, owner, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    desk.db.executescript(SCHEMA)        # création idempotente, seulement sur une écriture (jamais pendant une lecture)
    r = desk.db.execute('SELECT empreinte,reponse FROM documents56110_recus WHERE compte=? AND route=? AND cle=?', (owner, route, cle)).fetchone()
    if r and r[0] != empreinte:
        raise Stop('cle_reutilisee')
    return json.loads(r[1]) if r else None


def _garder(desk, owner, route, cle, empreinte, out):
    desk.db.execute('INSERT OR IGNORE INTO documents56110_recus VALUES(?,?,?,?,?,?)', (owner, route, cle, empreinte, json.dumps(out, ensure_ascii=False), _now()))
    desk.db.commit()


def _vrai(v):
    return v is True or str(v).strip().lower() in ('1', 'true', 'on', 'oui')


def _exiger_docker():
    d = etat_docker()['docker']
    if d != 'pret':
        raise Stop({'absent': 'docker_absent', 'permission': 'docker_permission', 'delai': 'docker_delai_depasse'}.get(d, 'docker_arrete'))


def ecrire(desk, owner, action, d, auth):
    """Installer, démarrer, arrêter, tester, désinstaller, revenir (administrateur d'un poste ; clé d'idempotence et version ; 409)."""
    cle = str(d.get('cle') or '')
    empreinte = hashlib.sha256(json.dumps([action, {k: v for k, v in d.items() if k != 'csrf'}], sort_keys=True, default=str).encode()).hexdigest()
    ancien = _recu(desk, owner, 'documents/' + action, cle, empreinte)   # même clé, même demande : la réponse gardée, rien n'est rejoué
    if ancien is not None:
        return ancien
    inst = installation(desk)
    if str(d.get('version')) != str(inst['version']):
        raise Stop('version_perimee')
    if _TACHE['en_cours']:
        raise Stop('tache_en_cours')
    if action == 'revenir':
        if not rendre_precedent(desk):
            raise Stop('pas_de_precedent')
        v = _enregistrer(desk)
        out = {'message': 'Réglages du serveur de documents précédent remis.', 'version': v['version']}
    elif action == 'installer':
        _exiger_docker()
        c = conteneur()
        if c is not None and not c['notre']:
            raise Stop('conteneur_etranger')
        port = (c or {}).get('port') if c else None
        if reglages_office(desk, port)['autre'] and not _vrai(d.get('remplacer')):
            raise Stop('reglages_existants')
        lancer('installation', desk.c, auth, owner)
        v = _enregistrer(desk)
        desk.audit('documents56110_installation_demandee', {'image': IMAGE, 'remplacer': _vrai(d.get('remplacer')), 'version': v['version']})
        out = {'message': 'Installation lancée : suivez les étapes dans la carte.', 'version': v['version']}
    elif action == 'desinstaller':
        _exiger_docker()
        c = conteneur()
        if c is not None and not c['notre']:
            raise Stop('conteneur_etranger')
        lancer('desinstallation', desk.c, auth, owner, retirer_image=_vrai(d.get('retirer_image')), image_tiree=inst['image_tiree'])
        v = _enregistrer(desk)
        desk.audit('documents56110_desinstallation_demandee', {'retirer_image': _vrai(d.get('retirer_image')) and inst['image_tiree'], 'version': v['version']})
        out = {'message': 'Désinstallation lancée.', 'version': v['version']}
    elif action in ('demarrer', 'arreter'):
        _exiger_docker()
        c = conteneur()
        if c is None:
            raise Stop('conteneur_absent')
        if not c['notre']:
            raise Stop('conteneur_etranger')
        if action == 'demarrer':
            if c['en_marche']:
                raise Stop('deja_en_marche')
            code, _, err = _docker('start', c['id'], delai=DELAIS['start'])
        else:
            if not c['en_marche']:
                raise Stop('deja_arrete')
            code, _, err = _docker('stop', '-t', '30', c['id'], delai=DELAIS['stop'])
        if code != 0:
            desk.audit('documents56110_%s_echec' % action, {'motif': cause(err)})
            raise Stop(cause(err))
        v = _enregistrer(desk)
        desk.audit('documents56110_' + ('demarre' if action == 'demarrer' else 'arrete'), {'version': v['version']})
        out = {'message': 'Serveur de documents démarré : il répond dans une à deux minutes.' if action == 'demarrer'
               else 'Serveur de documents arrêté.', 'version': v['version']}
    else:   # tester : rien d'autre n'est écrit que le résumé du test ; pas de nouvelle version
        resume = _test(desk, auth)
        desk.audit('documents56110_teste', {'reussies': resume['reussies'], 'total': resume['total']})
        out = {'message': 'Test réussi : toutes les étapes sont cochées.' if not resume['echecs']
               else 'Test en échec : %d étape(s) sur %d.' % (len(resume['echecs']), resume['total'])}
    _garder(desk, owner, 'documents/' + action, cle, empreinte, out)
    return out


# ================================================================================================ présentation (Paramètres › Documents et procédure)
def _cle():
    return secrets.token_hex(12)


def _cache(champs):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (e(k), e(str(v))) for k, v in champs.items())


def _bouton(api_, libelle, version, extra='', ghost=True, confirm='', corps=''):
    return ('<form class="m5-form ds110-inline" data-api="%s%s" data-reload%s>%s%s<button class="ax-btn%s" type="submit">%s</button>'
            '<p role="status" class="m5-status"></p></form>') % (
        PREFIXE, api_, (' data-confirm="%s"' % e(confirm)) if confirm else '', corps, _cache(dict({'version': version, 'cle': _cle()}, **(extra or {}))),
        ' ghost' if ghost else '', e(libelle))


def _etapes_html(t):
    T = TEXTES
    lignes = []
    for x in t['etapes']:
        etats = ''.join('<span data-pour="%s"%s>%s</span>' % (k, '' if k == x['etat'] else ' hidden', e(v)) for k, v in ETATS_ETAPE.items())
        lignes.append('<li data-etape="%s" data-etat="%s"><span class="ds110-libelle">%s</span> <span class="ds110-statut">%s</span></li>' % (
            e(x['id']), e(x['etat']), e(LIBELLES_ETAPES.get(x['id'], x['id'])), etats))
    if t['en_cours']:
        tete = T['en_cours_installation'] if t['action'] == 'installation' else T['en_cours_desinstallation']
    elif t['erreur']:
        tete = T['echec']
    else:
        tete = T['termine_installation'] if t['action'] == 'installation' else T['termine_desinstallation']
    h = '<div class="ds110-tache" data-ds110-tache%s role="status" aria-live="polite"><p><strong>%s</strong></p><ol class="ds110-etapes">%s</ol>' % (
        ' data-en-cours="1"' if t['en_cours'] else '', e(tete), ''.join(lignes))
    if not t['en_cours'] and t['erreur']:
        h += '<p class="ax-alert">%s</p><p class="ax-muted">%s</p>' % (e(t['message']), e(T['reprise']))
    return h + '</div>'


def carte_html(desk, prefix, auth, role):
    """Carte « Serveur de documents sur ce poste » : poste et administrateur seulement ; chaîne vide sinon (serveur du cabinet)."""
    if role != 'administrateur' or not est_poste(auth):
        return ''
    from . import office440
    s = etat(desk, auth)
    T = TEXTES
    v = s['installation']['version']
    linux = s['systeme'] == 'linux'
    h = ['<section class="ds110-carte" id="documents56110" aria-labelledby="ds110-titre"><h3 id="ds110-titre">%s</h3><p>%s</p>' % (e(T['titre']), e(T['intro']))]
    verifier = '<p><a class="ax-btn ghost" href="%s">%s</a></p>' % (e(prefix + '/parametres?rubrique=documents#documents56110', quote=True), e(T['verifier']))
    guide = '<p class="ax-muted"><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>' % (e(office440.guide_url(), quote=True), e(T['guide']))
    t = s['tache']
    if s['docker'] != 'pret':
        if s['docker'] == 'absent':
            h.append('<p>%s</p>' % e(T['docker_absent']))
            if linux:
                h.append('<p>%s</p><pre><code>%s</code></pre>' % (e(T['ubuntu_docker']), e(COMMANDE_UBUNTU)))
            else:
                h.append('<p><a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p><p>%s</p>' % (e(LIEN_DOCKER, quote=True), e(T['lien_docker']), e(T['apres_docker'])))
        elif s['docker'] == 'permission':
            h.append('<p class="ax-alert" role="status">%s</p>' % e(T['docker_permission']))
        elif s['docker'] == 'delai':
            h.append('<p class="ax-alert" role="status">%s</p>' % e(T['docker_delai']))
        else:
            h.append('<p class="ax-alert" role="status">%s</p>' % e(T['docker_arrete_linux' if linux else 'docker_arrete_windows']))
        if t['etapes']:
            h.append(_etapes_html(t))
        h.append(verifier + guide + '</section>')
        return ''.join(h)
    c = s['conteneur']
    etat_c = {'absent': T['conteneur_absent'], 'arrete': T['conteneur_arrete'], 'en_marche': T['conteneur_en_marche'], 'sain': T['conteneur_sain']}
    lignes = ['<li>%s</li>' % e(T['docker_pret'])]
    if c in etat_c:
        lignes.append('<li><strong>%s</strong></li>' % e(etat_c[c]))
    if c in ('arrete', 'en_marche', 'sain') and s['port']:
        lignes.append('<li>%s <code>%s</code></li>' % (e(T['adresse']), e(adresse_serveur(s['port']))))
        if s['reglages']['nous'] or (s['reglages']['autre'] and not t['en_cours']):   # rien pendant l'installation (réglages remplis à la fin)
            lignes.append('<li>%s</li>' % e(T['reglages_nous'] if s['reglages']['nous'] else T['reglages_pas_nous']))
    h.append('<ul class="ds110-etat">%s</ul>' % ''.join(lignes))
    if c == 'etranger':
        h.append('<p class="ax-alert" role="status">%s</p>' % e(T['conteneur_etranger']))
    if s.get('erreur_docker'):
        h.append('<p class="ax-alert" role="status">%s</p>' % e(message(s['erreur_docker'])))
    if t['etapes']:
        h.append(_etapes_html(t))
    test = s.get('test')
    if test and c in ('en_marche', 'sain', 'arrete') and not t['en_cours']:
        if test['echecs']:
            h.append('<div class="ds110-test"><p class="ax-alert">%s</p><ul>%s</ul><p class="ax-muted">%s</p></div>' % (
                e(T['test_ko']), ''.join('<li>%s</li>' % e(n) for n in test['echecs']), e(T['test_detail'])))
        else:
            h.append('<p class="ds110-test">%s</p>' % e(T['test_ok']))
    if linux and c in ('en_marche', 'sain', 'arrete'):
        h.append('<p class="ax-muted">%s</p>' % e(T['ubuntu_relais']))
    actif = ' disabled' if t['en_cours'] else ''
    if c == 'absent' or (c in ('arrete', 'en_marche') and t['erreur'] and t['action'] == 'installation'):
        libelle = T['reprendre'] if t['erreur'] and t['action'] == 'installation' else T['installer']
        details = '<p class="ax-muted">%s</p><p class="ax-muted">%s</p><p><code>%s</code></p><p class="ax-muted">%s</p>' % (
            e(T['image_presente'] if s['image'] else T['image_absente']), e(T['source']), e(IMAGE), e(T['memoire']))
        remplacer = ''
        if s['reglages']['autre']:
            remplacer = ('<p class="ax-alert" role="status">%s <code>%s</code></p><label class="ds110-case"><input type="checkbox" name="remplacer" value="1"> %s</label>'
                         % (e(T['autre_serveur']), e(s['reglages']['serveur']), e(T['remplacer'])))
        h.append('<form class="m5-form ds110-installer" data-api="%sinstaller" data-reload>%s%s%s<button class="ax-btn" type="submit"%s>%s</button>'
                 '<p role="status" class="m5-status"></p></form>' % (PREFIXE, details, remplacer, _cache({'version': v, 'cle': _cle()}), actif, e(libelle)))
    if c in ('arrete', 'en_marche', 'sain') and not t['en_cours']:
        actions = []
        if c == 'arrete':
            actions.append(_bouton('demarrer', T['demarrer'], v, ghost=False))
        else:
            actions.append(_bouton('arreter', T['arreter'], v))
            actions.append(_bouton('tester', T['tester'], v, ghost=c != 'sain'))
        if s['installation']['image_tiree']:
            corps = '<label class="ds110-case"><input type="checkbox" name="retirer_image" value="1"> %s</label>' % e(T['retirer_image'])
        else:
            corps = '<p class="ax-muted">%s</p>' % e(T['image_gardee'])
        actions.append(_bouton('desinstaller', T['desinstaller'], v, confirm=T['confirmer_desinstallation'], corps=corps))
        h.append('<div class="ds110-actions">%s</div>' % ''.join(actions))
    if s['reglages']['precedent'] and not t['en_cours']:
        h.append('<div class="ds110-precedent"><p class="ax-muted">%s</p>%s</div>' % (
            e(T['precedent']), _bouton('revenir', T['revenir'], v, confirm=T['confirmer_revenir'])))
    h.append(verifier + guide + '</section>')
    return ''.join(h)


MARQUE = '<h2>1 · Modifier les documents dans l’interface</h2>'


def inserer_carte(body, desk, auth, prefix, role):
    """Place la carte en tête de la section « 1 · Modifier les documents dans l'interface » ; page inchangée hors poste ou sans droit."""
    if role != 'administrateur' or not est_poste(auth) or MARQUE not in body:
        return body
    try:
        carte = carte_html(desk, prefix, auth, role)
    except Exception:   # noqa: BLE001 — la rubrique reste utilisable ; l'absence est dite
        carte = '<section class="ds110-carte" id="documents56110"><h3>%s</h3><p class="ax-alert" role="status">%s</p></section>' % (
            e(TEXTES['titre']), e(TEXTES['indisponible']))
    return body.replace(MARQUE, MARQUE + carte, 1)

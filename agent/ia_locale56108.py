"""5.6.108 (lot L) : assistant « Configurer l'IA locale » (guide de démarrage et Paramètres › IA ; poste et serveur).

Demande du cabinet (19/11/2026) : le logiciel recherche si Ollama est installé, voit les modèles disponibles, suggère le téléchargement de
modèles adaptés à défaut et, le cas échéant, fait les réglages. Six étapes, toutes réservées à l'administrateur :

1. **Détection** (lecture seule) : adresse réglée, sinon ``127.0.0.1:11434`` et ``localhost`` ; version (``/api/version``) ; sur la machine
   d'AxiorHub, présence de l'exécutable aux emplacements habituels (Windows : ``%LOCALAPPDATA%\\Programs\\Ollama`` ; Ubuntu : ``ollama`` dans
   le PATH, ``/usr/bin``, ``/usr/local/bin``, unité systemd) sans rien lancer. Installé mais arrêté, absent, ou joignable : message et action
   (lien officiel, commande Ubuntu officielle affichée, jamais exécutée). **Ollama sur un autre ordinateur du réseau** : adresse saisie par
   l'administrateur, admise seulement en IP littérale d'une plage privée (10/8, 172.16/12, 192.168/16, fc00::/7), en bouclage, ou en nom
   local explicite (``*.local``, ``ollama``) ; une adresse publique est refusée. ``OLLAMA_HOST`` n'est jamais utilisé : s'il existe, il est
   montré tel quel.
2. **Machine** : mémoire vive, cœurs, cartes graphiques et mémoire vidéo (Windows : registre en lecture ; Ubuntu : ``/proc/meminfo`` ;
   ``nvidia-smi`` ou ``rocm-smi`` s'ils existent, sans rien installer). Ollama distant : modèles chargés (``/api/ps``) et mémoire vidéo saisie
   (facultative). Classe légère / moyenne / forte, expliquée.
3. **Modèles installés** (``/api/tags``, ``/api/show``) : taille, paramètres, contexte, capacités ; un modèle « :cloud » (ou qui déclare un
   hôte distant) est dit **non local** et n'est jamais proposé.
4. **Suggestions** d'après le catalogue déclaratif ``agent/data/modeles_locaux56108.json`` (modèles vérifiés, licences, liens, date du
   relevé), par rôle et par classe. Ce qui est installé passe d'abord ; un téléchargement n'est suggéré que si aucun modèle installé ne
   remplit le rôle.
5. **Téléchargement avec accord explicite** (bouton par modèle suggéré, confirmation, ``POST /api/pull`` en flux, progression, annulation,
   reprise, un seul à la fois, clé d'idempotence, journal). C'est un accès réseau **d'Ollama** à son registre : annoncé comme tel ; en mode
   local strict, refusé tant que ``registry.ollama.ai`` n'est pas admis au registre des prestataires. Jamais automatique.
6. **Réglages** : répartition rapide / complexe / contrôle (``modeles5667.apply``), modèle d'embeddings (changement annoncé et confirmé : l'index
   sémantique est recalculé au fil de l'indexation habituelle, rien n'est lancé ici), préréglage du conseil (``conseil56108.enregistrer``),
   essai court facultatif (question fictive, durée mesurée). « Revenir aux réglages précédents » rétablit l'instantané pris avant.

Aucune écriture pendant un GET ; tables créées au démarrage (``ensure_schema`` appelé par ``Desk``).
"""
from datetime import datetime, timezone
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
from urllib.parse import urlsplit
import urllib.request

from .common import HTTP, Stop

PREFIXE = 'm56108/ia-locale/'
LECTURES_GET = ('etat', 'operation')
ECRITURES = ('telecharger', 'annuler', 'appliquer', 'revenir', 'essai')
CATALOGUE = Path(__file__).resolve().parent / 'data' / 'modeles_locaux56108.json'
ADRESSE_DEFAUT = 'http://127.0.0.1:11434'
CANDIDATS = ('http://127.0.0.1:11434', 'http://localhost:11434')
RESEAUX_PRIVES = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))
NOMS_LOCAUX = ('ollama',)            # nom de service Docker, admis aussi par ``config567`` (``local_url``)
LIEN_INSTALLATION = 'https://ollama.com/download'
COMMANDE_UBUNTU = 'curl -fsSL https://ollama.com/install.sh | sh'
COMMANDE_DEMARRER_UBUNTU = 'sudo systemctl start ollama'
REGISTRE = 'registry.ollama.ai'
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
SAFE_MODELE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$')
NOM_LOCAL = re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$')
MAX_POST = 8_000
MAX_MODELES = 40
SETTING_VERSION = 'ia_locale56108:version'
ROLES = ('rapide', 'analyse', 'controle', 'embeddings')
ROLE_5667 = {'rapide': 'fast', 'analyse': 'complex', 'controle': 'control'}
LIBELLES_ROLES = {'rapide': 'Rapide (tri des courriels, pièces jointes, voix)', 'analyse': 'Analyse et rédaction',
                  'controle': 'Contrôle indépendant', 'embeddings': 'Embeddings de la recherche'}
# bornes (milliards de paramètres) d'un modèle installé qui remplit « correctement » le rôle, par classe de machine
BORNES = {'rapide': {'legere': (3.0, 9.5), 'moyenne': (3.0, 9.5), 'forte': (3.0, 9.5)},
          'analyse': {'legere': (7.0, 14.9), 'moyenne': (12.0, 24.9), 'forte': (20.0, 40.0)},
          'controle': {'legere': (7.0, 14.9), 'moyenne': (7.0, 24.9), 'forte': (12.0, 40.0)}}
BATTEMENT_S = 600                    # opération « en cours » sans signe de vie depuis 10 minutes : tenue pour interrompue
DELAI_FLUX_S = 600                   # attente maximale entre deux lignes du flux de téléchargement
QUESTION_ESSAI = 'Question d’essai fictive : en une phrase, qu’est-ce qu’une mise en demeure ?'
TEXTE_EMBED = 'Essai fictif de recherche : bail commercial du dossier DOS-001.'
SONDE_SECONDES = 600

MESSAGES = {
    'introuvable': 'Élément introuvable.',
    'role_insuffisant': 'Seul l’administrateur configure l’IA locale.',
    'cle_requise': 'Clé d’idempotence absente ou invalide : rechargez la page.',
    'cle_reutilisee': 'Cette clé a déjà servi à une autre demande : rechargez la page.',
    'version_perimee': 'Les réglages ont changé depuis l’affichage de la page : rechargez-la avant de recommencer.',
    'adresse_ollama_invalide': 'Adresse d’Ollama invalide : forme attendue http://<adresse>:11434.',
    'adresse_ollama_publique_refusee': 'Adresse refusée : seule une adresse IP du réseau privé du cabinet (10.x, 172.16-31.x, 192.168.x, fc00::/7), '
                                       'l’adresse de cet ordinateur ou un nom local explicite (….local) est admise.',
    'ollama_injoignable': 'Ollama ne répond pas à cette adresse.',
    'modele_invalide': 'Nom de modèle invalide.',
    'modele_hors_catalogue': 'Seuls les modèles suggérés par le catalogue peuvent être téléchargés d’ici.',
    'modele_cloud_refuse': 'Modèle « cloud » refusé : il s’exécute hors du cabinet.',
    'modele_non_installe': 'Ce modèle n’est pas installé dans Ollama.',
    'modele_embedding_requis': 'Le modèle d’embeddings choisi ne calcule pas d’embeddings.',
    'confirmation_requise': 'Confirmation explicite requise.',
    'confirmation_embeddings_requise': 'Changer le modèle d’embeddings impose de recalculer l’index de recherche : cochez la confirmation.',
    'telechargement_en_cours': 'Un téléchargement est déjà en cours : attendez sa fin ou annulez-le.',
    'telechargement_local_strict': 'Mode local strict : le téléchargement ferait accéder Ollama à son registre (registry.ollama.ai). Admettez ce '
                                   'registre dans Paramètres › Confidentialité, ou téléchargez le modèle vous-même avec « ollama pull ».',
    'aucune_configuration': 'Aucune configuration appliquée par l’assistant à annuler.',
    'repartition_vide': 'Aucun modèle choisi.',
    'chemin_configuration_interne_absent': 'Configuration du service introuvable : réglage impossible depuis cette page.',
    'configuration_modifiee_recharger': 'La configuration a été modifiée ailleurs : rechargez la page.',
    'requete_trop_longue': 'Demande trop longue.',
    'origine_ou_csrf_refuse': 'Demande refusée (origine ou jeton de sécurité) : rechargez la page.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict',
           'telechargement_en_cours': '409 Conflict', 'configuration_modifiee_recharger': '409 Conflict', 'ollama_injoignable': '503 Service Unavailable',
           'requete_trop_longue': '413 Payload Too Large'}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS ia_locale56108_operations(id TEXT PRIMARY KEY, type TEXT NOT NULL, cle TEXT NOT NULL UNIQUE, modele TEXT NOT NULL DEFAULT '',
  adresse_locale INTEGER NOT NULL DEFAULT 1, etat TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0, fait INTEGER NOT NULL DEFAULT 0,
  statut TEXT NOT NULL DEFAULT '', erreur TEXT NOT NULL DEFAULT '', resultat TEXT NOT NULL DEFAULT '{}', annuler INTEGER NOT NULL DEFAULT 0,
  par TEXT NOT NULL DEFAULT '', debut TEXT NOT NULL, fin TEXT NOT NULL DEFAULT '', battement REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ia_locale56108_operations_etat ON ia_locale56108_operations(type, etat);
CREATE TABLE IF NOT EXISTS ia_locale56108_configurations(id INTEGER PRIMARY KEY AUTOINCREMENT, cle TEXT NOT NULL UNIQUE, par TEXT NOT NULL,
  le TEXT NOT NULL, avant TEXT NOT NULL, apres TEXT NOT NULL, etat TEXT NOT NULL, reponse TEXT NOT NULL DEFAULT '{}');
'''
_EVENEMENTS = {}                     # opération → threading.Event (annulation dans ce processus)
_FLUX = {}                           # opération → réponse HTTP ouverte (fermée à l'annulation pour débloquer la lecture)
_VERROU = threading.Lock()
_SONDES = {}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _h(valeur):
    return html.escape(str(valeur if valeur is not None else ''), quote=True)


def demarrer(cible, *args):
    """Exécution de fond (téléchargement, essai) : un fil du processus web. Remplacé par une exécution directe dans les essais."""
    threading.Thread(target=cible, args=args, daemon=True, name='ia-locale56108').start()


def ensure_schema(desk):
    """Tables du lot (démarrage, ``Desk._prepare``) ; sans changement, aucune écriture ni verrou (recette 5.6.44)."""
    from .sync56108 import schema_complet
    if schema_complet(desk.db, SCHEMA):
        return
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ catalogue
_CATALOGUE = {}


def catalogue():
    """Catalogue déclaratif (lu une fois). Illisible : liste vide, jamais un modèle deviné."""
    if 'data' not in _CATALOGUE:
        try:
            data = json.loads(CATALOGUE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {'modeles': [], 'roles': {}, 'conseil': {}, 'classes': {}, 'licences': {}}
        data['par_nom'] = {m['nom']: m for m in data.get('modeles') or [] if isinstance(m, dict) and SAFE_MODELE.match(str(m.get('nom') or ''))}
        _CATALOGUE['data'] = data
    return _CATALOGUE['data']


def fiche_catalogue(nom):
    m = catalogue()['par_nom'].get(str(nom or ''))
    if not m:
        return None
    lic = (catalogue().get('licences') or {}).get(m.get('licence'), {})
    return {**{k: m.get(k) for k in ('nom', 'famille', 'parametres_md', 'telechargement_go', 'memoire_go', 'contexte', 'capacites', 'licence', 'fiche',
                                     'source_licence', 'releve_le')},
            'licence_libelle': lic.get('libelle') or m.get('licence'), 'licence_libre': bool(lic.get('libre'))}


# ------------------------------------------------------------------------------------------------ adresse d'Ollama
def est_cloud(nom):
    n = str(nom or '').lower()
    return n.endswith('-cloud') or n.endswith(':cloud') or ':cloud' in n


def verifier_adresse(url):
    """Adresse saisie par l'administrateur : http(s), sans identifiant, chemin, paramètre ni fragment ; hôte de bouclage, IP littérale d'une
    plage privée, ou nom local explicite (``*.local``, ``ollama``). Une IP publique ou un nom public est refusé. Port absent : 11434."""
    texte = str(url or '').strip()
    if '://' not in texte:
        texte = 'http://' + texte
    parts = urlsplit(texte)
    if parts.scheme not in ('http', 'https') or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise Stop('adresse_ollama_invalide')
    try:
        port = parts.port
    except ValueError:
        raise Stop('adresse_ollama_invalide') from None
    hote = (parts.hostname or '').lower().rstrip('.')
    if not hote or len(texte) > 300:
        raise Stop('adresse_ollama_invalide')
    try:
        ip = ipaddress.ip_address(hote)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not (ip.is_loopback or any(ip in r for r in RESEAUX_PRIVES if r.version == ip.version)):
            raise Stop('adresse_ollama_publique_refusee')
        hote_txt = '[%s]' % ip if ip.version == 6 else str(ip)
    else:
        if not NOM_LOCAL.match(hote) or not (hote == 'localhost' or hote in NOMS_LOCAUX or hote.endswith('.local')):
            raise Stop('adresse_ollama_publique_refusee')
        hote_txt = hote
    return '%s://%s:%d' % (parts.scheme, hote_txt, port or 11434)


def est_locale(url):
    """L'Ollama visé tourne-t-il sur la machine d'AxiorHub (bouclage) ?"""
    hote = (urlsplit(str(url or '')).hostname or '').lower()
    if hote == 'localhost':
        return True
    try:
        return ipaddress.ip_address(hote).is_loopback
    except ValueError:
        return False


def _client(url, timeout=4):
    return HTTP(url, local_only=True, timeout=timeout, local_hosts=NOMS_LOCAUX)


def adresse_reglee(desk):
    return str((desk.c.get('ollama') or {}).get('url') or '')


def adresse_cible(desk, data=None):
    """Adresse visée : saisie (vérifiée), sinon l'adresse réglée (normalisée si possible ; une adresse déjà admise par ``config567`` reste
    utilisable telle quelle, ``HTTP(local_only=True)`` refaisant le contrôle), sinon ``127.0.0.1:11434``."""
    if data and data.get('adresse'):
        return verifier_adresse(data['adresse'])
    reglee = adresse_reglee(desk)
    if not reglee:
        return ADRESSE_DEFAUT
    try:
        return verifier_adresse(reglee)
    except Stop:
        return reglee.rstrip('/')


# ------------------------------------------------------------------------------------------------ machine (lecture seule)
class Sonde:
    """Lecture des caractéristiques de la machine d'AxiorHub, sans rien installer ni lancer d'autre que ``nvidia-smi``/``rocm-smi`` s'ils
    existent. Remplaçable dans les essais."""

    def __init__(self, plateforme=None):
        self.plateforme = plateforme or ('windows' if os.name == 'nt' else 'linux')

    def memoire_vive_go(self):
        if self.plateforme == 'windows':
            try:
                import ctypes

                class Etat(ctypes.Structure):
                    _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong),
                                ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                                ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
                etat = Etat()
                etat.dwLength = ctypes.sizeof(Etat)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(etat)):
                    return round(etat.ullTotalPhys / 1024 ** 3, 1)
            except Exception:   # noqa: BLE001 — interface système indisponible : inconnu, jamais deviné
                return None
            return None
        try:
            for ligne in Path('/proc/meminfo').read_text(encoding='ascii', errors='replace').splitlines():
                if ligne.startswith('MemTotal:'):
                    return round(int(ligne.split()[1]) / 1024 ** 2, 1)
        except (OSError, ValueError, IndexError):
            return None
        return None

    def coeurs(self):
        return os.cpu_count()

    def _commande(self, argv):
        chemin = shutil.which(argv[0])
        if not chemin:
            return None
        try:
            out = subprocess.run([chemin] + list(argv[1:]), capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    def cartes(self):
        cartes = []
        texte = self._commande(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'])
        for ligne in (texte or '').splitlines():
            nom, _, mio = ligne.rpartition(',')
            try:
                cartes.append({'nom': nom.strip()[:80], 'vram_go': round(float(mio) / 1024, 1), 'source': 'nvidia-smi'})
            except ValueError:
                continue
        if not cartes:
            texte = self._commande(['rocm-smi', '--showmeminfo', 'vram', '--csv'])
            lignes = [x for x in (texte or '').splitlines() if x.strip()]
            if lignes:
                tete = [x.strip().lower() for x in lignes[0].split(',')]
                col = next((i for i, x in enumerate(tete) if 'vram total memory' in x), None)
                for ligne in lignes[1:]:
                    vals = ligne.split(',')
                    try:
                        cartes.append({'nom': 'AMD ' + vals[0].strip()[:40], 'vram_go': round(int(vals[col]) / 1024 ** 3, 1), 'source': 'rocm-smi'})
                    except (TypeError, ValueError, IndexError):
                        continue
        if not cartes and self.plateforme == 'windows':
            cartes = self._cartes_registre()
        return cartes

    def _cartes_registre(self):
        """Windows : classe des cartes d'affichage du registre (lecture seule) ; mémoire dédiée ``HardwareInformation.qwMemorySize``."""
        out = []
        try:
            import winreg
            racine = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, racine) as cle:
                for i in range(32):
                    try:
                        sous = winreg.EnumKey(cle, i)
                    except OSError:
                        break
                    if not sous.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(cle, sous) as k:
                            nom = str(winreg.QueryValueEx(k, 'DriverDesc')[0])[:80]
                            try:
                                taille = int(winreg.QueryValueEx(k, 'HardwareInformation.qwMemorySize')[0])
                            except OSError:
                                brut = winreg.QueryValueEx(k, 'HardwareInformation.MemorySize')[0]
                                taille = int.from_bytes(brut, 'little') if isinstance(brut, bytes) else int(brut)
                    except (OSError, ValueError, TypeError):
                        continue
                    if taille > 0:
                        out.append({'nom': nom, 'vram_go': round(taille / 1024 ** 3, 1), 'source': 'registre'})
        except Exception:   # noqa: BLE001 — registre illisible : aucune carte connue
            return []
        return out

    def executable(self):
        """Ollama installé sur cette machine ? Emplacements habituels seulement ; rien n'est lancé."""
        if self.plateforme == 'windows':
            base = os.environ.get('LOCALAPPDATA') or ''
            for chemin in ([Path(base) / 'Programs' / 'Ollama' / 'ollama.exe'] if base else []) + [Path(os.environ.get('ProgramFiles') or r'C:\Program Files') / 'Ollama' / 'ollama.exe']:
                if chemin.is_file():
                    return {'trouve': True, 'chemin': str(chemin), 'service': False}
            return {'trouve': False, 'chemin': '', 'service': False}
        service = any(Path(p).is_file() for p in ('/etc/systemd/system/ollama.service', '/usr/lib/systemd/system/ollama.service', '/lib/systemd/system/ollama.service'))
        trouve = shutil.which('ollama') or next((p for p in ('/usr/bin/ollama', '/usr/local/bin/ollama') if Path(p).is_file()), '')
        return {'trouve': bool(trouve or service), 'chemin': str(trouve or ''), 'service': service}

    def disque_libre_go(self):
        """Place libre du disque du dossier des modèles d'Ollama (emplacement par défaut) ; inconnue sinon."""
        candidats = []
        if self.plateforme == 'windows':
            if os.environ.get('USERPROFILE'):
                candidats.append(Path(os.environ['USERPROFILE']) / '.ollama')
        else:
            candidats += [Path('/usr/share/ollama/.ollama'), Path.home() / '.ollama']
        for dossier in candidats:
            if dossier.is_dir():
                try:
                    return round(shutil.disk_usage(str(dossier)).free / 1e9, 1)
                except OSError:
                    return None
        return None

    def variable_ollama_host(self):
        return str(os.environ.get('OLLAMA_HOST') or '')[:200]


def _sonde(sonde=None):
    """Résultats de la sonde gardés 10 minutes en mémoire (jamais en base) : ``nvidia-smi`` n'est pas relancé à chaque affichage."""
    if sonde is not None:
        return {'ram': sonde.memoire_vive_go(), 'coeurs': sonde.coeurs(), 'cartes': sonde.cartes(), 'executable': sonde.executable(),
                'disque': sonde.disque_libre_go(), 'ollama_host': sonde.variable_ollama_host(), 'plateforme': sonde.plateforme}
    with _VERROU:
        memo = _SONDES.get('poste')
        if memo and time.monotonic() - memo[0] < SONDE_SECONDES:
            return memo[1]
    data = _sonde(Sonde())
    with _VERROU:
        _SONDES['poste'] = (time.monotonic(), data)
    return data


def classer(ram_go=None, vram_go=None):
    """(classe, budget en Go, explication). Classe « inconnue » : suggestions de la classe légère."""
    ram, vram = float(ram_go or 0), float(vram_go or 0)
    if vram >= 20:
        return 'forte', vram, 'Carte graphique de %s Go : les modèles de 24 à 32 milliards de paramètres tiennent en mémoire vidéo.' % _go(vram)
    if vram >= 10:
        return 'moyenne', vram, 'Carte graphique de %s Go : modèles jusqu’à 14 milliards de paramètres.' % _go(vram)
    if ram >= 32:
        return 'moyenne', min(ram * 0.5, 19.9), ('Pas de carte graphique d’au moins 10 Go : %s Go de mémoire vive, modèles jusqu’à 14 milliards de '
                                                 'paramètres, réponses plus lentes.' % _go(ram))
    if vram or ram:
        budget = max(vram, min(ram * 0.5, 9.9))
        return 'legere', budget, 'Mémoire utilisable par les modèles : environ %s Go ; modèles de 3 à 9 milliards de paramètres.' % _go(budget)
    return 'inconnue', None, ('Mémoire de la machine d’Ollama inconnue : saisissez sa mémoire vidéo pour des suggestions plus justes ; en attendant, '
                              'suggestions pour une machine légère.')


def _go(x):
    return ('%.1f' % float(x)).rstrip('0').rstrip('.').replace('.', ',')


def evaluer_machine(url, locale, vram_saisie=None, sonde=None):
    """Machine qui fait tourner Ollama. Locale : sonde du poste ; distante : ``/api/ps`` (tailles chargées) et saisie facultative."""
    saisie = None
    try:
        saisie = float(str(vram_saisie).replace(',', '.')) if vram_saisie not in (None, '') else None
        if saisie is not None and not 0 < saisie <= 1024:
            saisie = None
    except ValueError:
        saisie = None
    if locale:
        s = _sonde(sonde)
        vram = max([c['vram_go'] for c in s['cartes']] or [0]) or None
        classe, budget, pourquoi = classer(s['ram'], saisie or vram)
        return {'source': 'poste', 'ram_go': s['ram'], 'coeurs': s['coeurs'], 'cartes': s['cartes'], 'vram_go': saisie or vram, 'vram_saisie': saisie,
                'disque_libre_go': s['disque'], 'classe': classe, 'budget_go': budget, 'explication': pourquoi}
    charges, vram_vue = [], None
    try:
        ps = _client(url, 4).json('GET', '/api/ps')
        for m in (ps.get('models') or [])[:20] if isinstance(ps, dict) else []:
            if isinstance(m, dict):
                charges.append({'nom': str(m.get('name') or m.get('model') or '')[:120], 'taille_go': round(int(m.get('size') or 0) / 1e9, 1),
                                'vram_go': round(int(m.get('size_vram') or 0) / 1e9, 1)})
        vram_vue = max([c['vram_go'] for c in charges] or [0]) or None
    except (Stop, ValueError, TypeError):
        charges = []
    classe, budget, pourquoi = classer(None, saisie or vram_vue)
    if not saisie and vram_vue:
        pourquoi += ' D’après les modèles chargés par Ollama, la mémoire vidéo est d’au moins %s Go.' % _go(vram_vue)
    return {'source': 'ollama_distant', 'ram_go': None, 'coeurs': None, 'cartes': [], 'vram_go': saisie or vram_vue, 'vram_saisie': saisie,
            'charges': charges, 'disque_libre_go': None, 'classe': classe, 'budget_go': budget, 'explication': pourquoi}


# ------------------------------------------------------------------------------------------------ détection (lecture seule)
def detecter(desk, adresse=None, sonde=None):
    """Ollama joignable ? Adresse saisie (vérifiée), sinon l'adresse réglée puis 127.0.0.1 et localhost. Aucune écriture."""
    reglee = adresse_reglee(desk)
    if adresse:
        essais, source = [verifier_adresse(adresse)], 'saisie'
    else:
        essais, source = ([adresse_cible(desk)] if reglee else []), 'reglage'
        essais += [c for c in CANDIDATS if c not in essais]
    vus, trouvee, version = [], '', ''
    for url in essais:
        try:
            v = _client(url, 3).json('GET', '/api/version')
            version = str((v or {}).get('version') or '')[:40] if isinstance(v, dict) else ''
            trouvee = url
            vus.append({'adresse': url, 'joignable': True, 'erreur': ''})
            break
        except Stop as ex:
            vus.append({'adresse': url, 'joignable': False, 'erreur': str(ex).split(':')[0]})
    cible = trouvee or essais[0]
    locale = est_locale(cible)
    s = _sonde(sonde)
    exe = s['executable'] if locale else None
    windows = s['plateforme'] == 'windows'
    actions = []
    if trouvee:
        etat = 'joignable'
        message = 'Ollama répond à cette adresse.'
    elif locale and exe and exe['trouve']:
        etat = 'arrete'
        message = ('Ollama est installé sur cet ordinateur mais ne répond pas : démarrez-le (menu Démarrer › Ollama).' if windows else
                   'Ollama est installé sur ce serveur mais ne répond pas : démarrez le service.')
        if not windows:
            actions.append({'type': 'commande', 'texte': COMMANDE_DEMARRER_UBUNTU})
    elif locale:
        etat = 'absent'
        message = 'Ollama n’est pas installé sur cet ordinateur. Installez-le depuis le site officiel, puis revenez ici.'
        actions.append({'type': 'lien', 'url': LIEN_INSTALLATION})
        if not windows:
            actions.append({'type': 'commande', 'texte': COMMANDE_UBUNTU})
    else:
        etat = 'distant_injoignable'
        message = ('L’Ollama de l’autre ordinateur ne répond pas : vérifiez qu’il est allumé, qu’Ollama y écoute sur le réseau '
                   '(OLLAMA_HOST=0.0.0.0 sur cet ordinateur) et que le pare-feu laisse passer le port 11434.')
        actions.append({'type': 'lien', 'url': LIEN_INSTALLATION})
    return {'adresse': cible, 'adresse_reglee': reglee, 'source': source, 'joignable': bool(trouvee), 'version': version, 'essais': vus,
            'locale': locale, 'executable': exe, 'etat': etat, 'message': message, 'actions': actions, 'plateforme': s['plateforme'],
            'ollama_host': s['ollama_host'], 'commande_jamais_executee': True}


# ------------------------------------------------------------------------------------------------ modèles installés
def modeles_installes(url):
    """``/api/tags`` puis ``/api/show`` de chaque modèle (au plus 40). Un modèle « :cloud » ou qui déclare un hôte distant est non local."""
    from .references56108 import SEUIL_PARAMETRES, taille_parametres
    from .profil5634 import model_context_limit
    client = _client(url, 8)
    tags = client.json('GET', '/api/tags')
    out = []
    for x in ((tags or {}).get('models') or [])[:MAX_MODELES] if isinstance(tags, dict) else []:
        if not isinstance(x, dict):
            continue
        nom = str(x.get('name') or x.get('model') or '')
        if not SAFE_MODELE.match(nom):
            continue
        details = x.get('details') if isinstance(x.get('details'), dict) else {}
        cloud = est_cloud(nom) or bool(x.get('remote_host') or x.get('remote_model'))
        caps, contexte = [], None
        if not cloud:
            try:
                shown = client.json('POST', '/api/show', {'model': nom})
                if isinstance(shown, dict):
                    caps = [str(c) for c in shown.get('capabilities') or [] if isinstance(c, str)][:12]
                    contexte = model_context_limit(shown)
                    cloud = bool(shown.get('remote_host') or shown.get('remote_model'))
                    if isinstance(shown.get('details'), dict):
                        details = {**details, **shown['details']}
            except Stop:
                caps = []
        taille = int(x.get('size') or 0)
        md = taille_parametres(details.get('parameter_size'))
        if md is None:
            md = round(taille / 0.6e9, 1) if taille else 0.0
        embedding = 'embedding' in caps or 'embed' in nom.lower() or nom.lower().startswith('bge-')
        out.append({'nom': nom, 'parametres_md': round(md, 2), 'taille_go': round(taille / 1e9, 1), 'contexte': contexte, 'capacites': caps,
                    'famille': _famille(nom), 'cloud': cloud, 'local': not cloud, 'embedding': embedding,
                    'petit': (not embedding) and (md <= SEUIL_PARAMETRES or (contexte is not None and contexte <= 16384))})
    return sorted(out, key=lambda m: (m['embedding'], m['parametres_md'], m['nom']))


def _famille(nom):
    from .conseil56108 import famille
    return famille(nom)


# ------------------------------------------------------------------------------------------------ suggestions
def correspond(installe, nom_catalogue):
    """``qwen3:8b`` installé sous ``qwen3:8b`` ou ``qwen3:8b-q4_K_M`` ; ``bge-m3`` sous ``bge-m3:latest``."""
    a, b = str(installe or '').lower(), str(nom_catalogue or '').lower()
    a = a if ':' in a else a + ':latest'
    b = b if ':' in b else b + ':latest'
    return a == b or a.startswith(b + '-')


def _tient(m, budget):
    return budget is None or (m.get('taille_go') or 0) * 1.2 <= budget + 0.5


def _adapte(role, m, classe, budget):
    if not m or not m['local'] or m['cloud']:
        return False
    if role == 'embeddings':
        return m['embedding']
    if m['embedding'] or not _tient(m, budget):
        return False
    lo, hi = BORNES[role][classe]
    return lo <= (m['parametres_md'] or 0) <= hi


def _installe(nom, installes):
    return next((m for m in installes if m['local'] and correspond(m['nom'], nom)), None)


def _choisir_role(role, classe, budget, installes, actuel, exclure=None, famille_exclue=None):
    """(modèle installé retenu, origine) : réglage actuel adapté, puis candidats du catalogue installés, puis autre modèle installé adapté."""
    cands = (catalogue().get('roles') or {}).get(role, {}).get(classe) or []

    def permis(m, strict_famille):
        return m and (exclure is None or m['nom'] != exclure) and (not strict_famille or famille_exclue is None or m['famille'] != famille_exclue)
    for strict in ((True, False) if famille_exclue else (False,)):
        m = next((x for x in installes if x['nom'] == actuel), None)
        if permis(m, strict) and _adapte(role, m, classe, budget):
            return m, 'actuel'
        for nom in cands:
            m = _installe(nom, installes)
            if permis(m, strict) and _adapte(role, m, classe, budget):
                return m, 'catalogue'
        autres = [x for x in installes if permis(x, strict) and _adapte(role, x, classe, budget)]
        if autres:
            m = autres[0] if role == 'rapide' else max(autres, key=lambda x: (x['parametres_md'], x['nom']))
            return m, 'installe'
    return None, 'aucun'


def _a_telecharger(role, classe, budget, installes, famille_exclue=None):
    """Premier modèle du catalogue pour ce rôle qui tient dans la machine et n'est pas installé ; famille différente pour le contrôle."""
    cands = (catalogue().get('roles') or {}).get(role, {}).get(classe) or []
    for strict in ((True, False) if famille_exclue else (False,)):
        for nom in cands:
            f = fiche_catalogue(nom)
            if not f or _installe(nom, installes) or est_cloud(nom):
                continue
            if strict and famille_exclue and f['famille'] == famille_exclue:
                continue
            if budget is None or float(f['memoire_go'] or 0) <= budget + 0.5:
                return f
    return None


def suggerer(installes, classe, budget, actuels):
    """Répartition proposée par rôle ; un téléchargement n'est suggéré que si aucun modèle installé ne remplit le rôle."""
    classe = classe if classe in ('legere', 'moyenne', 'forte') else 'legere'
    discussion = [m for m in installes if m['local'] and not m['embedding']]
    roles, telechargements = [], []
    choix = {}
    for role in ROLES:
        famille_exclue = exclure = None
        if role == 'controle' and choix.get('analyse'):
            exclure, famille_exclue = choix['analyse']['nom'], choix['analyse']['famille']
        m, origine = _choisir_role(role, classe, budget, installes, actuels.get(role), exclure, famille_exclue)
        a_dl = None
        note = ''
        if m is None:
            a_dl = _a_telecharger(role, classe, budget, installes, famille_exclue)
            if role == 'controle' and choix.get('analyse'):
                m, origine = choix['analyse'], 'meme_que_analyse'
                note = 'Aucun autre modèle installé ne convient : le contrôle reprendrait le modèle de rédaction (regard moins indépendant).'
            elif role == 'embeddings':
                courant = next((x for x in installes if x['nom'] == actuels.get('embeddings') and x['embedding']), None)
                m, origine = courant, ('actuel' if courant else 'aucun')
            elif discussion:
                m, origine = (discussion[0] if role == 'rapide' else max(discussion, key=lambda x: x['parametres_md'])), 'repli'
                note = 'Aucun modèle installé n’a la taille recommandée pour ce rôle : repli sur un modèle installé.'
        elif role == 'controle' and m['famille'] == (choix.get('analyse') or {}).get('famille'):
            a_dl = _a_telecharger(role, classe, budget, installes, famille_exclue)
            note = 'Même famille que le modèle de rédaction : un modèle d’une autre famille donnerait un regard plus indépendant.' if a_dl else ''
        choix[role] = m
        alternatives = [x['nom'] for x in installes if x['local'] and (x['embedding'] if role == 'embeddings' else not x['embedding'])]
        roles.append({'role': role, 'libelle': LIBELLES_ROLES[role], 'actuel': actuels.get(role) or '', 'choisi': m['nom'] if m else '',
                      'origine': origine, 'adapte': origine in ('actuel', 'catalogue', 'installe'), 'telecharger': a_dl, 'note': note,
                      'alternatives': alternatives, 'petit': bool(m and m.get('petit'))})
        if a_dl and a_dl['nom'] not in {t['nom'] for t in telechargements}:
            telechargements.append({**a_dl, 'pour': [role]})
        elif a_dl:
            next(t for t in telechargements if t['nom'] == a_dl['nom'])['pour'].append(role)
    return {'classe': classe, 'roles': roles, 'telechargements': telechargements}


def conseil_suggere(desk, installes, classe):
    """Préréglage du conseil : celui du catalogue pour la classe s'il est disponible, sinon le premier préréglage local disponible."""
    from . import conseil56108 as K
    locaux = [{'name': m['nom'], 'billions': m['parametres_md']} for m in installes if m['local'] and not m['embedding']]
    try:
        det = K.detection(desk, installes=locaux, api={})
        actuel = K.reglages(desk)
    except Exception:   # noqa: BLE001 — conseil illisible : aucune suggestion, jamais une valeur supposée
        return {'suggere': '', 'prereglages': [], 'actuel': {'mode': '', 'prereglage': ''}, 'motif': 'Conseil de modèles indisponible pour le moment.'}
    par_id = {p['id']: p for p in det['prereglages']}
    voulu = (catalogue().get('conseil') or {}).get(classe if classe in ('legere', 'moyenne', 'forte') else 'legere', '')
    suggere = voulu if par_id.get(voulu, {}).get('etat') == 'disponible' else next(
        (i for i in ('local_equilibre', 'local_leger') if par_id.get(i, {}).get('etat') == 'disponible'), '')
    motif = '' if suggere else 'Aucun préréglage local complet avec les modèles installés : %s.' % '; '.join(
        x for x in (par_id.get(voulu) or {}).get('manques', []) if x)
    return {'suggere': suggere, 'voulu': voulu, 'actuel': {'mode': actuel['mode'], 'prereglage': actuel['prereglage']}, 'motif': motif,
            'prereglages': [{'id': p['id'], 'libelle': p['libelle'], 'etat': p['etat'], 'etat_libelle': p['etat_libelle'], 'local': p['local'],
                             'membres': [m['modele'] for m in p['membres'] if m]} for p in det['prereglages'] if p['local']]}


# ------------------------------------------------------------------------------------------------ état (GET)
def reglages_actuels(desk):
    from .modeles5667 import current
    now = current(desk)
    for role in ROLE_5667.values():   # réglage écrit dans cette même requête (« Appliquer » puis essai) : relu, pas seulement la configuration chargée
        now[role] = desk.settings('model:role:' + role, '') or now.get(role, '')
    return {'adresse': adresse_reglee(desk), 'rapide': now.get('fast', ''), 'analyse': now.get('complex', ''), 'controle': now.get('control', ''),
            'embeddings': str((desk.c.get('rag') or {}).get('embedding_model') or 'qwen3-embedding:0.6b')}


def version(desk):
    try:
        return int(desk.settings(SETTING_VERSION, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _lignes(desk, sql, args=()):
    try:
        return [dict(r) for r in desk.db.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:   # tables absentes (base antérieure non préparée) : rien à montrer, aucune création pendant une lecture
        return []


def public_operation(row, maintenant=None):
    if not row:
        return None
    maintenant = time.time() if maintenant is None else maintenant
    etat = row['etat']
    if etat == 'en_cours' and maintenant - float(row.get('battement') or 0) > BATTEMENT_S:
        etat = 'interrompu'
    try:
        resultat = json.loads(row.get('resultat') or '{}')
    except ValueError:
        resultat = {}
    total, fait = int(row.get('total') or 0), int(row.get('fait') or 0)
    return {'id': row['id'], 'type': row['type'], 'modele': row['modele'], 'etat': etat, 'total': total, 'fait': fait,
            'pourcent': round(100 * fait / total, 1) if total else None, 'statut': row.get('statut') or '', 'erreur': row.get('erreur') or '',
            'resultat': resultat, 'debut': row['debut'], 'fin': row.get('fin') or '', 'adresse_locale': bool(row.get('adresse_locale', 1))}


def operation(desk, ident):
    rows = _lignes(desk, 'SELECT * FROM ia_locale56108_operations WHERE id=?', (str(ident or '')[:80],))
    if not rows:
        raise Stop('introuvable')
    return public_operation(rows[0])


def derniere(desk, type_):
    rows = _lignes(desk, 'SELECT * FROM ia_locale56108_operations WHERE type=? ORDER BY debut DESC, rowid DESC LIMIT 1', (type_,))
    return public_operation(rows[0]) if rows else None


def derniere_configuration(desk):
    rows = _lignes(desk, "SELECT id,par,le,etat FROM ia_locale56108_configurations WHERE etat='appliquee' ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def telechargement_permis(desk):
    """(permis, motif) : en mode local strict, seulement si le registre d'Ollama est admis au registre des prestataires."""
    from .selection_sources5697 import local_strict
    if not local_strict(desk):
        return True, ''
    try:
        from .passerelle5689 import _admis
        if REGISTRE in _admis(desk.c.get('state_dir'))[0]:
            return True, ''
    except Exception:   # noqa: BLE001 — registre illisible : refus, jamais un accès élargi
        pass
    return False, MESSAGES['telechargement_local_strict']


def etat(desk, adresse=None, vram=None, sonde=None):
    """Tout l'écran, en lecture seule (aucune écriture, R05)."""
    det = detecter(desk, adresse, sonde)
    machine = evaluer_machine(det['adresse'], det['locale'], vram, sonde) if det['joignable'] or det['locale'] else \
        {'source': 'ollama_distant', 'classe': 'inconnue', 'budget_go': None, 'explication': classer()[2], 'cartes': [], 'ram_go': None, 'coeurs': None,
         'vram_go': None, 'disque_libre_go': None, 'charges': []}
    installes, erreur = [], ''
    if det['joignable']:
        try:
            installes = modeles_installes(det['adresse'])
        except Stop as ex:
            erreur = str(ex).split(':')[0]
    actuels = reglages_actuels(desk)
    sugg = suggerer(installes, machine['classe'], machine['budget_go'], actuels) if det['joignable'] else {'classe': machine['classe'], 'roles': [], 'telechargements': []}
    permis, motif = telechargement_permis(desk)
    cat = catalogue()
    return {'version': version(desk), 'detection': det, 'machine': machine, 'installes': installes, 'erreur_modeles': erreur,
            'suggestions': sugg, 'conseil': conseil_suggere(desk, installes, machine['classe']) if det['joignable'] else None,
            'reglages': actuels, 'catalogue': {'releve_le': cat.get('releve_le', ''), 'memoire': cat.get('memoire', ''), 'nombre': len(cat['par_nom'])},
            'telechargement': derniere(desk, 'telechargement'), 'essai': derniere(desk, 'essai'),
            'derniere_configuration': derniere_configuration(desk), 'telechargement_permis': permis, 'motif_telechargement': motif,
            'reseau': ('Télécharger un modèle fait accéder Ollama (et non AxiorHub) à son registre public (registry.ollama.ai) ; aucune donnée du '
                       'cabinet n’est transmise.')}


# ------------------------------------------------------------------------------------------------ opérations de fond
def _cle(data):
    cle = str((data or {}).get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    return cle


def _ouvrir_db(state_dir):
    db = sqlite3.connect(str(Path(state_dir) / 'desk.sqlite3'), timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _maj(db, op_id, **champs):
    champs['battement'] = time.time()
    db.execute('UPDATE ia_locale56108_operations SET %s WHERE id=?' % ','.join('%s=?' % k for k in champs), tuple(champs.values()) + (op_id,))
    db.commit()


def _audit_fond(cfg, action, data):
    """Journal de fin d'une opération de fond : bureau ouvert le temps d'une écriture ; une panne du journal n'efface pas le résultat."""
    try:
        from .desk import Desk
        d = Desk(dict(cfg))
        try:
            d.audit(action, data)
        finally:
            d.db.close()
    except Exception:   # noqa: BLE001
        pass


def _annule(db, op_id):
    ev = _EVENEMENTS.get(op_id)
    if ev is not None and ev.is_set():
        return True
    row = db.execute('SELECT annuler FROM ia_locale56108_operations WHERE id=?', (op_id,)).fetchone()
    return bool(row and row[0])


def _nouvelle_operation(desk, type_, cle, modele, url, owner):
    """Insère l'opération (BEGIN IMMEDIATE : un seul téléchargement à la fois, même entre deux requêtes simultanées)."""
    existante = _lignes(desk, 'SELECT * FROM ia_locale56108_operations WHERE cle=?', (cle,))
    if existante:
        if existante[0]['type'] != type_ or existante[0]['modele'] != modele:
            raise Stop('cle_reutilisee')
        return public_operation(existante[0]), False
    from .common import release_before_network
    release_before_network('ia_locale56108')
    db = desk.db
    db.execute('BEGIN IMMEDIATE')
    try:
        limite = time.time() - BATTEMENT_S
        db.execute("UPDATE ia_locale56108_operations SET etat='interrompu', fin=? WHERE type=? AND etat='en_cours' AND battement<?", (_now(), type_, limite))
        if db.execute("SELECT 1 FROM ia_locale56108_operations WHERE type=? AND etat='en_cours'", (type_,)).fetchone():
            raise Stop('telechargement_en_cours' if type_ == 'telechargement' else 'essai_en_cours')
        op_id = '%s-%s' % (type_[:5], secrets.token_hex(8))
        db.execute('INSERT INTO ia_locale56108_operations(id,type,cle,modele,adresse_locale,etat,statut,par,debut,battement) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (op_id, type_, cle, modele, int(est_locale(url)), 'en_cours', 'demande', str(owner)[:120], _now(), time.time()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return operation(desk, op_id), True


def demander_telechargement(desk, data, owner, url=None):
    """Après confirmation explicite seulement : nom, taille, place libre et licence ont été montrés par la page (et sont rendus ici)."""
    cle = _cle(data)
    modele = str(data.get('modele') or '').strip()
    if not SAFE_MODELE.fullmatch(modele):
        raise Stop('modele_invalide')
    if est_cloud(modele):
        raise Stop('modele_cloud_refuse')
    fiche = fiche_catalogue(modele)
    if fiche is None:
        raise Stop('modele_hors_catalogue')
    if data.get('confirme') is not True:
        raise Stop('confirmation_requise')
    permis, _motif = telechargement_permis(desk)
    if not permis:
        raise Stop('telechargement_local_strict')
    url = adresse_cible(desk, data) if data.get('adresse') or not url else url
    from .pause5629 import checkpoint
    checkpoint(desk)
    op, neuve = _nouvelle_operation(desk, 'telechargement', cle, modele, url, owner)
    if neuve:
        desk.audit('ia_locale56108_telechargement_demande', {'modele': modele, 'taille_go': fiche['telechargement_go'], 'licence': fiche['licence'],
                                                             'ollama_local': est_locale(url), 'acces_reseau': 'Ollama → ' + REGISTRE})
        _EVENEMENTS[op['id']] = threading.Event()
        demarrer(telecharger, dict(desk.c), op['id'], url, modele)
    return {'operation': op, 'fiche': fiche, 'idempotent': not neuve,
            'message': 'Téléchargement de %s demandé à Ollama : la progression s’affiche ici.' % modele}


STATUTS_PULL = (('pulling manifest', 'manifeste'), ('verifying', 'verification'), ('writing manifest', 'ecriture'), ('removing', 'nettoyage'),
                ('success', 'termine'), ('pulling', 'telechargement'), ('downloading', 'telechargement'))


def _statut(texte):
    t = str(texte or '').lower()
    return next((code for motif, code in STATUTS_PULL if t.startswith(motif)), 'telechargement')


def telecharger(cfg, op_id, url, modele):
    """Corps du téléchargement (fil de fond) : ``POST /api/pull`` en flux ; progression écrite au plus une fois par seconde ; annulation par
    fermeture du flux (Ollama garde les fichiers partiels : une reprise repart de là)."""
    db = _ouvrir_db(cfg['state_dir'])
    etat_final, erreur, totaux, faits = 'erreur', '', {}, {}
    try:
        client = HTTP(url, local_only=True, timeout=DELAI_FLUX_S, local_hosts=NOMS_LOCAUX)
        req = urllib.request.Request(client.base + '/api/pull', data=json.dumps({'model': modele, 'stream': True}).encode(), method='POST',
                                     headers={**client.headers, 'Content-Type': 'application/json'})
        dernier = 0.0
        with client.opener.open(req, timeout=client.timeout) as r:
            _FLUX[op_id] = r
            _maj(db, op_id, statut='manifeste')
            while True:
                if _annule(db, op_id):
                    etat_final = 'annule'
                    break
                ligne = r.readline(1_000_001)
                if not ligne:
                    erreur = erreur or 'flux_interrompu'
                    break
                try:
                    obj = json.loads(ligne)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get('error'):
                    erreur = 'ollama:' + str(obj['error'])[:200]
                    break
                statut = _statut(obj.get('status'))
                digest = str(obj.get('digest') or '')
                if digest and obj.get('total'):
                    totaux[digest] = int(obj.get('total') or 0)
                    faits[digest] = int(obj.get('completed') or 0)
                if statut == 'termine':
                    etat_final = 'termine'
                    break
                if time.monotonic() - dernier >= 1.0:
                    _maj(db, op_id, statut=statut, total=sum(totaux.values()), fait=sum(faits.values()))
                    dernier = time.monotonic()
    except Stop as ex:
        erreur = str(ex).split(':')[0]
    except (OSError, ValueError) as ex:
        if _annule(db, op_id):
            etat_final = 'annule'
        else:
            erreur = 'flux_interrompu' if isinstance(ex, (OSError, socket.timeout)) else 'reponse_invalide'
    finally:
        _FLUX.pop(op_id, None)
        _EVENEMENTS.pop(op_id, None)
    if etat_final == 'termine':
        total = sum(totaux.values())
        _maj(db, op_id, etat='termine', statut='termine', total=total, fait=total, fin=_now())
    elif etat_final == 'annule':
        _maj(db, op_id, etat='annule', statut='annule', fin=_now())
    else:
        _maj(db, op_id, etat='erreur', statut='erreur', erreur=erreur or 'erreur_inconnue', total=sum(totaux.values()), fait=sum(faits.values()), fin=_now())
    db.close()
    _audit_fond(cfg, 'ia_locale56108_telechargement_' + ('termine' if etat_final == 'termine' else 'annule' if etat_final == 'annule' else 'erreur'),
                {'modele': modele, 'erreur': (erreur or '')[:120]})


def annuler(desk, data, owner):
    _cle(data)
    ident = str(data.get('id') or '')[:80]
    rows = _lignes(desk, "SELECT * FROM ia_locale56108_operations WHERE id=?", (ident,))
    if not rows:
        raise Stop('introuvable')
    op = public_operation(rows[0])
    if op['etat'] != 'en_cours':
        return {'operation': op, 'message': 'Rien à annuler : l’opération est déjà finie.'}
    desk.db.execute('UPDATE ia_locale56108_operations SET annuler=1 WHERE id=?', (ident,))
    desk.db.commit()
    ev = _EVENEMENTS.get(ident)
    if ev is not None:
        ev.set()
    r = _FLUX.get(ident)
    sock = getattr(getattr(getattr(r, 'fp', None), 'raw', None), '_sock', None) if r is not None else None
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    desk.audit('ia_locale56108_telechargement_annulation', {'operation': ident, 'modele': op['modele']})
    return {'operation': operation(desk, ident), 'message': 'Annulation demandée : Ollama garde la partie déjà téléchargée pour une reprise.'}


def essai(cfg, op_id, url, modeles):
    """Essai court (fil de fond) : une question fictive à chaque modèle retenu, durée mesurée ; pauses de l'IA et politique des données
    respectées avant chaque appel. ``modeles`` : [(rôle, nom, embedding, réfléchit)]."""
    from .arret5643 import before_model_call
    from .data_policy5689 import exiger
    db = _ouvrir_db(cfg['state_dir'])
    resultats = []
    try:
        client = HTTP(url, local_only=True, timeout=180, local_hosts=NOMS_LOCAUX)
        for role, nom, embedding, reflechit in modeles:
            if _annule(db, op_id):
                break
            debut = time.monotonic()
            ligne = {'role': role, 'libelle': LIBELLES_ROLES.get(role, role), 'modele': nom, 'ok': False, 'secondes': None, 'extrait': '', 'erreur': ''}
            try:
                before_model_call(cfg)
                exiger(cfg, 'embeddings' if embedding else 'llm', 'ollama', finalite='essai_modele', local=True, journal=False)
                if embedding:
                    rep = client.json('POST', '/api/embed', {'model': nom, 'input': [TEXTE_EMBED], 'truncate': True})
                    vect = (rep.get('embeddings') or [[]])[0] if isinstance(rep, dict) else []
                    ligne['ok'] = isinstance(vect, list) and len(vect) >= 8
                    ligne['extrait'] = 'Vecteur de %d dimensions.' % len(vect) if ligne['ok'] else ''
                    ligne['erreur'] = '' if ligne['ok'] else 'embeddings_invalides'
                else:
                    corps = {'model': nom, 'prompt': QUESTION_ESSAI, 'stream': False, 'options': {'num_predict': 120, 'temperature': 0}}
                    if reflechit:
                        corps['think'] = False
                    rep = client.json('POST', '/api/generate', corps)
                    texte = str(rep.get('response') or '').strip() if isinstance(rep, dict) else ''
                    ligne['ok'] = bool(texte)
                    ligne['extrait'] = texte[:240]
                    ligne['erreur'] = '' if texte else 'generation_vide'
            except Stop as ex:
                ligne['erreur'] = str(ex).split(':')[0]
            ligne['secondes'] = round(time.monotonic() - debut, 1)
            resultats.append(ligne)
            _maj(db, op_id, resultat=json.dumps({'lignes': resultats}, ensure_ascii=False), fait=len(resultats), total=len(modeles))
        final = 'annule' if _annule(db, op_id) else ('termine' if resultats and all(x['ok'] for x in resultats) else 'erreur')
        _maj(db, op_id, etat=final, statut=final, fin=_now(), erreur='' if final != 'erreur' else 'essai_partiel')
    except Exception as ex:   # noqa: BLE001 — l'essai est dit en échec, jamais réussi
        _maj(db, op_id, etat='erreur', statut='erreur', erreur=str(ex).split(':')[0][:120] if isinstance(ex, Stop) else 'erreur_inconnue', fin=_now())
    finally:
        db.close()
        _EVENEMENTS.pop(op_id, None)
    _audit_fond(cfg, 'ia_locale56108_essai', {'modeles': [m[1] for m in modeles], 'reussis': sum(1 for x in resultats if x['ok'])})


def lancer_essai(desk, data, owner, cle=None):
    cle = cle or _cle(data)
    url = adresse_cible(desk)
    actuels = reglages_actuels(desk)
    installes = {m['nom']: m for m in modeles_installes(url)}
    modeles, vus = [], set()
    for role in ROLES:
        nom = actuels.get(role) or ''
        if not nom or (role, nom) in vus:
            continue
        vus.add((role, nom))
        m = installes.get(nom)
        modeles.append((role, nom, role == 'embeddings', bool(m and 'thinking' in m['capacites'])))
    if not modeles:
        raise Stop('repartition_vide')
    from .pause5629 import checkpoint
    checkpoint(desk)
    op, neuve = _nouvelle_operation(desk, 'essai', cle, ','.join(sorted({m[1] for m in modeles}))[:300], url, owner)
    if neuve:
        _EVENEMENTS[op['id']] = threading.Event()
        demarrer(essai, dict(desk.c), op['id'], url, modeles)
    return {'operation': op, 'message': 'Essai court lancé : une question fictive à chaque modèle retenu.'}


# ------------------------------------------------------------------------------------------------ réglages (POST)
def _config_revision(env):
    from .config567 import _path, _read_text
    return int(json.loads(_read_text(_path(env))).get('config_revision567', 0))


def _ecrire_config(desk, env, valeurs):
    if not valeurs:
        return
    if not isinstance(env, dict) or not env.get('axiorhub.config_path'):
        raise Stop('chemin_configuration_interne_absent')
    from .config567 import save
    save(desk, {'revision': _config_revision(env), 'values': valeurs}, env)
    if 'ollama.url' in valeurs:
        desk.c['ollama'] = {**(desk.c.get('ollama') or {}), 'url': valeurs['ollama.url']}
    if 'rag.embedding_model' in valeurs:
        desk.c['rag'] = {**(desk.c.get('rag') or {}), 'embedding_model': valeurs['rag.embedding_model']}


def _instantane(desk):
    from . import conseil56108 as K
    r = K.reglages(desk)
    return {'adresse': adresse_reglee(desk), 'roles': {role: desk.settings('model:role:' + role, '') or '' for role in ROLE_5667.values()},
            'embeddings': str((desk.c.get('rag') or {}).get('embedding_model') or ''),
            'conseil': {'mode': r['mode'], 'prereglage': r['prereglage'], 'membres': r['membres'], 'president': r['president']}}


def _restaurer(desk, env, avant, owner):
    """Rétablit un instantané : adresse et embeddings (configuration), répartition (réglages), conseil. Rend les écarts non rétablis."""
    from . import conseil56108 as K
    ecarts = []
    valeurs = {}
    if avant.get('adresse') and avant['adresse'] != adresse_reglee(desk):
        valeurs['ollama.url'] = avant['adresse']
    if avant.get('embeddings') and avant['embeddings'] != str((desk.c.get('rag') or {}).get('embedding_model') or ''):
        valeurs['rag.embedding_model'] = avant['embeddings']
    _ecrire_config(desk, env, valeurs)
    for role, nom in (avant.get('roles') or {}).items():
        if role in ROLE_5667.values():
            desk.setting('model:role:' + role, nom or '')   # valeur vide : la répartition de la configuration reprend la main
    c = avant.get('conseil') or {}
    actuel = K.reglages(desk)
    if (c.get('mode'), c.get('prereglage')) != (actuel['mode'], actuel['prereglage']) or c.get('membres') != actuel['membres']:
        data = {'cle': 'l108r-' + secrets.token_hex(10), 'mode': c.get('mode') or 'auto', 'prereglage': c.get('prereglage') or '',
                'membres': c.get('membres') or [], 'version': actuel['version']}
        if c.get('president'):
            data['president'] = c['president']
        try:
            K.enregistrer(desk, data, owner)
        except Stop as ex:
            ecarts.append('conseil de modèles non rétabli (%s)' % str(ex).split(':')[0])
    return ecarts


def _reponse_rejouee(desk, cle):
    rows = _lignes(desk, 'SELECT * FROM ia_locale56108_configurations WHERE cle=?', (cle,))
    if not rows:
        return None
    try:
        return {**json.loads(rows[0]['reponse'] or '{}'), 'idempotent': True}
    except ValueError:
        return {'idempotent': True, 'message': 'Demande déjà traitée.'}


def _verifier_version(desk, data):
    try:
        attendue = int(data.get('version'))
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None
    if attendue != version(desk):
        raise Stop('version_perimee')


def appliquer(desk, data, owner, env=None):
    """« Appliquer cette configuration » : adresse, répartition, embeddings (confirmés), préréglage du conseil ; instantané pour revenir."""
    from . import conseil56108 as K
    from .modeles5667 import apply as appliquer_repartition
    cle = _cle(data)
    deja = _reponse_rejouee(desk, cle)
    if deja is not None:
        return deja
    _verifier_version(desk, data)
    url = adresse_cible(desk, data)
    try:
        installes = {m['nom']: m for m in modeles_installes(url)}
    except Stop:
        raise Stop('ollama_injoignable') from None
    choix = {}
    for role in ROLES:
        nom = str(data.get(role) or '').strip()
        if not nom:
            continue
        if not SAFE_MODELE.fullmatch(nom):
            raise Stop('modele_invalide')
        if est_cloud(nom) or (nom in installes and not installes[nom]['local']):
            raise Stop('modele_cloud_refuse')
        if nom not in installes:
            raise Stop('modele_non_installe')
        if role == 'embeddings' and not installes[nom]['embedding']:
            raise Stop('modele_embedding_requis')
        if role != 'embeddings' and installes[nom]['embedding']:
            raise Stop('modele_invalide')
        choix[role] = nom
    conseil = str(data.get('conseil') or '').strip()
    if conseil and conseil not in K.PAR_ID:
        raise Stop('prereglage_inconnu')
    avant = _instantane(desk)
    embedding_change = bool(choix.get('embeddings')) and choix['embeddings'] != avant['embeddings']
    if embedding_change and data.get('confirmer_embeddings') is not True:
        raise Stop('confirmation_embeddings_requise')
    if not any(choix.get(r) for r in ROLE_5667) and not embedding_change and not conseil and url == adresse_cible(desk):
        raise Stop('repartition_vide')
    from .pause5629 import checkpoint
    checkpoint(desk)
    valeurs = {}
    if url != adresse_cible(desk):
        valeurs['ollama.url'] = url
    if embedding_change:
        valeurs['rag.embedding_model'] = choix['embeddings']
    faits = []
    try:
        _ecrire_config(desk, env, valeurs)
        if 'ollama.url' in valeurs:
            faits.append('adresse d’Ollama')
        repartition = {ROLE_5667[r]: choix[r] for r in ROLE_5667 if choix.get(r)}
        if repartition:
            appliquer_repartition(desk, repartition)
            faits.append('répartition des modèles')
        if embedding_change:
            faits.append('modèle d’embeddings')
        actuel = K.reglages(desk)
        if conseil and (actuel['mode'], actuel['prereglage']) != ('prereglage', conseil):
            locaux = [{'name': m['nom'], 'billions': m['parametres_md']} for m in installes.values() if m['local'] and not m['embedding']]
            K.enregistrer(desk, {'cle': 'l108-' + secrets.token_hex(10), 'mode': 'prereglage', 'prereglage': conseil, 'version': actuel['version']},
                          owner, installes=locaux, api={})
            faits.append('préréglage du conseil')
    except Stop:
        _restaurer(desk, env, avant, owner)   # rien d'appliqué à moitié : l'instantané est rétabli
        raise
    apres = _instantane(desk)
    nouvelle = version(desk) + 1
    reindex = ''
    if embedding_change:
        reindex = _annonce_reindexation(desk, choix['embeddings'])
    reponse = {'ok': True, 'version': nouvelle, 'appliques': faits, 'reindexation': reindex,
               'message': 'Configuration appliquée. « Revenir aux réglages précédents » la défait.' if faits else 'Aucun changement à appliquer.'}
    desk.db.execute('INSERT INTO ia_locale56108_configurations(cle,par,le,avant,apres,etat,reponse) VALUES(?,?,?,?,?,?,?)',
                    (cle, str(owner)[:120], _now(), json.dumps(avant, ensure_ascii=False), json.dumps(apres, ensure_ascii=False), 'appliquee',
                     json.dumps(reponse, ensure_ascii=False)))
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (SETTING_VERSION, json.dumps(nouvelle)))
    desk.db.commit()
    desk.audit('ia_locale56108_appliquee', {'appliques': faits, 'version': nouvelle, 'embeddings_change': embedding_change,
                                           'ollama_local': est_locale(url)})
    if data.get('essai') is True:
        try:
            reponse['essai'] = lancer_essai(desk, {}, owner, cle='e' + cle[:63])['operation']
        except Stop as ex:
            reponse['essai_erreur'] = str(ex).split(':')[0]
    return reponse


def _annonce_reindexation(desk, modele):
    try:
        n = desk.db.execute('SELECT COUNT(*) FROM knowledge_chunks').fetchone()[0]
    except sqlite3.Error:
        n = 0
    return ('Nouveau modèle d’embeddings %s : les %d passages déjà indexés seront recalculés au fil de l’indexation habituelle (rien n’est lancé '
            'maintenant) ; d’ici là, la recherche s’appuie davantage sur le texte.' % (modele, n))


def revenir(desk, data, owner, env=None):
    cle = _cle(data)
    deja = _reponse_rejouee(desk, cle)
    if deja is not None:
        return deja
    _verifier_version(desk, data)
    rows = _lignes(desk, "SELECT * FROM ia_locale56108_configurations WHERE etat='appliquee' ORDER BY id DESC LIMIT 1")
    if not rows:
        raise Stop('aucune_configuration')
    avant = json.loads(rows[0]['avant'])
    from .pause5629 import checkpoint
    checkpoint(desk)
    ecarts = _restaurer(desk, env, avant, owner)
    nouvelle = version(desk) + 1
    reponse = {'ok': True, 'version': nouvelle, 'ecarts': ecarts,
               'message': 'Réglages précédents rétablis.' + (' Non rétabli : %s.' % '; '.join(ecarts) if ecarts else '')}
    desk.db.execute("UPDATE ia_locale56108_configurations SET etat='annulee' WHERE id=?", (rows[0]['id'],))
    desk.db.execute('INSERT INTO ia_locale56108_configurations(cle,par,le,avant,apres,etat,reponse) VALUES(?,?,?,?,?,?,?)',
                    (cle, str(owner)[:120], _now(), rows[0]['apres'], rows[0]['avant'], 'retour', json.dumps(reponse, ensure_ascii=False)))
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (SETTING_VERSION, json.dumps(nouvelle)))
    desk.db.commit()
    desk.audit('ia_locale56108_retour', {'configuration': rows[0]['id'], 'version': nouvelle, 'ecarts': len(ecarts)})
    return reponse


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    """Administrateur seulement, pour chaque route ; lecture : ``etat``, ``operation`` ; écriture : POST des cinq actions."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if role != 'administrateur':
        return False
    if method in ('GET', 'HEAD'):
        return reste in LECTURES_GET
    return method == 'POST' and reste in ECRITURES


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def _erreur(code):
    from .web import REASONS
    return _json({'error': code, 'message': MESSAGES.get(code) or REASONS.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste not in LECTURES_GET + ECRITURES:
        return _erreur('introuvable')
    if role != 'administrateur':
        return _erreur('role_insuffisant')     # rôle qui ne peut jamais appeler ces actions
    if not allowed(role, method, PREFIXE + reste):
        return _erreur('introuvable')
    try:
        if method in ('GET', 'HEAD'):
            if reste == 'operation':
                return _json(operation(desk, args.get('id')))
            return _json(etat(desk, str(args.get('adresse') or '').strip() or None, args.get('vram')))
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth)
        if reste == 'telecharger':
            return _json(demander_telechargement(desk, data, owner))
        if reste == 'annuler':
            return _json(annuler(desk, data, owner))
        if reste == 'appliquer':
            return _json(appliquer(desk, data, owner, env))
        if reste == 'revenir':
            return _json(revenir(desk, data, owner, env))
        return _json(lancer_essai(desk, data, owner))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _erreur(code)


# ------------------------------------------------------------------------------------------------ section (Paramètres › IA, guide)
def section_html(desk, prefix, role, guide=False):
    """Coquille de l'assistant ; le contenu (détection, machine, modèles, suggestions) est lu par ``v56108-ia-locale.js`` en GET. Lecture seule."""
    titre = '<h2>Configurer l’IA locale</h2>' if not guide else '<h3>Configurer l’IA locale</h3>'
    intro = ('<p>AxiorHub recherche Ollama, évalue la machine qui le fait tourner, liste les modèles installés et propose une répartition '
             'adaptée. Un modèle n’est téléchargé qu’après votre confirmation, et chaque réglage peut être défait.</p>')
    if role != 'administrateur':
        return '<section class="ax-card l108" id="ia-locale56108">%s%s<p class="ax-muted">Seul l’administrateur configure l’IA locale.</p></section>' % (titre, intro)
    script = '<script defer src="%s/static/v56108-ia-locale.js"></script>' % _h(prefix)
    return ('<section class="ax-card l108" id="ia-locale56108" data-api="%s" data-cle="%s">%s%s'
            '<noscript><p class="ax-alert">L’assistant a besoin de JavaScript ; sans lui, réglez l’adresse dans Connexions et les modèles ci-dessous.</p></noscript>'
            '<div class="l108-etape" id="l108-detection-bloc"><h3 class="l108-titre">1. Ollama</h3>'
            '<form class="l108-adresse" id="l108-adresse" novalidate><label for="l108-adresse-champ">Ollama sur un autre ordinateur du réseau (facultatif)</label>'
            '<div class="l108-ligne"><input id="l108-adresse-champ" name="adresse" type="text" inputmode="url" autocomplete="off" spellcheck="false" '
            'placeholder="http://serveur-ia.local:11434" maxlength="300"><button class="ax-btn ghost" type="submit">Vérifier cette adresse</button></div>'
            '<p class="ax-muted">Adresse IP du réseau privé du cabinet ou nom local (….local) ; jamais une adresse publique. Vide : l’adresse réglée, puis cet ordinateur.</p>'
            '</form><div id="l108-detection" aria-live="polite"><p class="ax-muted">Recherche d’Ollama…</p></div></div>'
            '<div class="l108-etape"><h3 class="l108-titre">2. Machine</h3><div id="l108-machine"></div>'
            '<label for="l108-vram">Mémoire vidéo de la machine d’Ollama, en Go (facultatif)</label><div class="l108-ligne">'
            '<input id="l108-vram" type="number" min="1" max="1024" step="1" inputmode="numeric"><button class="ax-btn ghost" type="button" id="l108-vram-ok">'
            'Recalculer</button></div></div>'
            '<div class="l108-etape"><h3 class="l108-titre">3. Modèles installés</h3><div id="l108-installes"></div></div>'
            '<div class="l108-etape"><h3 class="l108-titre">4. Suggestions</h3><div id="l108-suggestions"></div><div id="l108-telechargement" aria-live="polite"></div></div>'
            '<div class="l108-etape"><h3 class="l108-titre">5. Réglages</h3><form id="l108-appliquer" novalidate></form><div id="l108-retour"></div></div>'
            '<div class="l108-etape"><h3 class="l108-titre">6. Essai court</h3><div id="l108-essai" aria-live="polite"></div></div>'
            '<p class="l108-statut" role="status" aria-live="polite"></p>%s</section>') % (
        _h(PREFIXE), _h(secrets.token_hex(6)), titre, intro, script)


def carte_guide(desk, env, prefix, admin=False):
    """Carte de l'étape « IA » du guide de démarrage (``guide5631.CARTES['ia_locale']``) : même assistant que Paramètres › IA."""
    return section_html(desk, prefix, 'administrateur' if admin else 'avocat', guide=True)

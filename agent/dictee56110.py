"""5.6.110 (lot D) : dictée sur chaque poste (Windows 11 et Ubuntu 24.04), moteur whisper.cpp lancé une fois par dictée.

- **Moteur** : ``whisper-cli`` (whisper.cpp ``b5907``, licence MIT), fourni par le paquet du poste ; jamais cherché dans le ``PATH``.
  ``AXIORHUB_DICTEE`` (essais, exécution hors paquet) ; sinon Windows : dossier ``dictee`` à côté de l'exécutable de l'application
  empaquetée ; Linux : ``/usr/lib/axiorhub-pilote/dictee``. Pas de service, pas de port, pas de jeton : un processus par dictée.
- **Appel** : ``whisper-cli -m modeles/<fichier> -f travail/<jeton>/d.wav -l fr -nt -np -otxt -of travail/<jeton>/d -t <n>``, lancé
  avec ``cwd=<state_dir>/dictee56110`` et des **noms relatifs en ASCII** seulement (le moteur Windows plante si le chemin du modèle contient
  un caractère accentué, profil ``C:\\Users\\Jérôme``). Le texte est lu dans ``d.txt`` (UTF-8) ; la sortie standard n'est jamais lue.
  Le dossier de travail est supprimé après chaque dictée, même en cas d'erreur ; l'audio n'est jamais conservé ni journalisé, et aucun
  texte dicté n'entre dans les journaux ni l'audit.
- **Modèles** (dépôt ``ggerganov/whisper.cpp``, révision figée, licence MIT) : téléchargés seulement sur un clic de l'administrateur, en
  arrière-plan (fil du processus de l'interface), taille et SHA-256 vérifiées avant de garder le fichier (fichier temporaire du même
  dossier puis remplacement atomique ; aucun reste en cas d'échec). Sortie réseau explicite, comme celle des langues de l'OCR
  (``outils_ocr56106``) : elle ne passe pas par ``common.HTTP`` (donc ni par ``common.EGRESS_GUARD`` ni par le « tout en local », qui
  portent sur les données du cabinet), ne transmet aucune donnée du cabinet, est notée au journal d'audit et comptée dans les destinations
  observées (``passerelle5689.compter``, catégorie ``telechargement_modele``).
- **Activation** : réglage ``dictee:moteur56110`` (``moteur`` = ``poste``), moteur présent et fichier du modèle choisi présent à la bonne
  taille. La dictée du poste active, le navigateur envoie du WAV 16 kHz mono (``static/v56110-dictee.js``) ; sinon le chemin de la
  passerelle (``audio.dictate``) reste strictement inchangé.
"""
from array import array
import hashlib
from html import escape as e
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import struct
import sys
import threading
import time
from datetime import datetime, timezone

from .common import Stop

KEY = 'dictee:moteur56110'
PREFIXE = 'm56110/dictee/'
ECRITURES = ('installer', 'retirer', 'reglage', 'verifier')
LECTURES = ('etat',)
DOSSIER = 'dictee56110'
LINUX = '/usr/lib/axiorhub-pilote/dictee'
VERSION_MOTEUR = 'b5907'
REVISION = '5359861c739e955e79d9a303bcbc70fb988958b1'   # ggerganov/whisper.cpp (Hugging Face), révision figée
URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/%s/%s'
SOURCE = 'huggingface.co'
VC_REDIST = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
# Mesure de l'intégrateur (poste à 16 cœurs logiques, phrase de 14 secondes) : ordre de grandeur, pas une promesse.
MODELES = {
    'rapide': {'fichier': 'ggml-base-q5_1.bin', 'octets': 59707625,
               'sha256': '422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898',
               'nom': 'Rapide', 'conseil': 'pour un poste modeste', 'mesure': '2,4 s'},
    'equilibre': {'fichier': 'ggml-small-q5_1.bin', 'octets': 190085487,
                  'sha256': 'ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb',
                  'nom': 'Équilibré', 'conseil': 'conseillé', 'mesure': '5,4 s'},
    'precis': {'fichier': 'ggml-large-v3-turbo-q5_0.bin', 'octets': 574041195,
               'sha256': '394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2',
               'nom': 'Précis', 'conseil': 'lent sans processeur puissant', 'mesure': '19 s'},
}
DEFAUT_MODELE = 'equilibre'
LANGUES = {'fr': 'français', 'en': 'anglais', 'de': 'allemand', 'es': 'espagnol', 'auto': 'détection automatique'}
TAUX, CANAUX, BITS = 16000, 1, 16
MAX_SECONDES = 600                       # 10 minutes au plus
MAX_OCTETS = 20_000_000                  # limite de la route /dictation dans ce mode (8 Mo sinon) ; 10 min = 19 200 044 octets
SEUIL_SILENCE = 200                      # énergie (moyenne quadratique, échelle 16 bits) de la tranche de 30 ms la plus forte ; ≈ -44 dBFS
DELAI_TELECHARGEMENT = 120               # secondes sans réception avant abandon
MAX_POST = 4_000
MAX_TEXTE = 30_000
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
# Phrases inventées par Whisper sur du silence ou du bruit (vérifié : une seconde de silence rend la première). Rendues comme texte vide
# seulement quand elles forment toute la transcription ; comparées sans casse, ponctuation ni espaces.
PHRASES_INVENTEES = (
    'Sous-titres réalisés par la communauté d’Amara.org', 'Sous-titres réalisés para la communauté d’Amara.org',
    'Sous-titrage ST’ 501', 'Sous-titrage Société Radio-Canada', 'Sous-titrage FR 2021', 'Merci d’avoir regardé cette vidéo !',
    'Merci d’avoir regardé cette vidéo', 'Abonnez-vous !', 'Sous-titres par Amara.org',
    'Subtitles by the Amara.org community', 'Thanks for watching!', 'Thank you for watching.', 'Thank you for watching!',
    'Untertitel der Amara.org-Community', 'Untertitel im Auftrag des ZDF, 2017', 'Vielen Dank fürs Zuschauen!',
    'Subtítulos realizados por la comunidad de Amara.org', '¡Gracias por ver el video!', 'Gracias por ver el vídeo.',
)
ETIQUETTE = re.compile(r'\[(?:BLANK_AUDIO|SILENCE|MUSIC|MUSIQUE|Musique|Music|musique|music|_?BEG_?|NO_SPEECH)\]|\((?:musique|Musique|music|Music)\)')
MESSAGES = {
    'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Réservé à l’administrateur du cabinet.',
    'version_perimee': 'Les réglages ont changé entre-temps : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'cle_reutilisee': 'Cette demande a déjà été utilisée pour une autre action : rechargez la page.',
    'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Demande refusée.',
    'modele_inconnu': 'Modèle de dictée non proposé.', 'langue_inconnue': 'Langue de dictée non proposée.',
    'moteur_absent': 'Moteur de dictée absent de cette installation.',
    'modele_non_installe': 'Modèle de dictée non installé sur ce poste : installez-le d’abord.',
    'telechargement_en_cours': 'Un téléchargement de modèle est déjà en cours : attendez sa fin.',
    'espace_insuffisant': 'Place insuffisante sur le disque pour ce modèle : rien n’a été téléchargé.',
    'stockage_indisponible': 'Dossier des données du cabinet indisponible.',
    'empreinte_incorrecte': 'Fichier reçu différent du fichier attendu (taille ou empreinte) : rejeté, rien n’a été gardé.',
    'telechargement_impossible': 'Téléchargement impossible (réseau ou serveur) : rien n’a été gardé.',
    'redirection_refusee': 'Adresse de téléchargement inattendue : rejetée, rien n’a été gardé.',
    'dictee_poste_format_non_wav': 'Le navigateur n’a pas converti l’enregistrement en WAV : rechargez la page, puis réessayez avec un navigateur récent.',
    'dictee_wav_invalide': 'Enregistrement illisible : en-tête WAV incorrect.',
    'dictee_wav_format_refuse': 'Enregistrement refusé : son attendu en 16 000 Hz, mono, 16 bits.',
    'dictee_trop_longue': 'Dictée trop longue (10 minutes au plus) : dictez par parties.',
    'dictee_trop_volumineuse': 'Enregistrement trop volumineux : dictez par parties.',
    'transcription_en_cours': 'Une transcription est déjà en cours. Réessayez dans quelques secondes.',
    'dictee_poste_delai_depasse': 'La transcription a dépassé le délai prévu et a été arrêtée : choisissez un modèle plus rapide ou dictez par parties.',
    'composant_microsoft_absent': 'Composant Microsoft Visual C++ absent de ce poste : installez-le depuis le site de Microsoft, puis réessayez.',
    'modele_illisible': 'Fichier du modèle illisible : retirez le modèle, puis installez-le de nouveau.',
    'bibliotheque_systeme_absente': 'Bibliothèque du système absente pour la dictée (libgomp1) : sudo apt-get install -y libgomp1, puis réessayez.',
    'modele_empreinte_incorrecte': 'Fichier du modèle différent du fichier attendu (empreinte vérifiée) : il n’est pas utilisé. Retirez-le, puis installez le modèle depuis cette carte ou déposez le bon fichier.',
    'moteur_en_echec': 'Le moteur de dictée s’est arrêté sur une erreur : rien n’a été transcrit.',
    'dictee_poste_sortie_absente': 'Le moteur de dictée n’a rendu aucun texte : rien n’a été transcrit.',
    'transcription_invalide': 'Transcription illisible : rien n’a été inséré.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'telechargement_en_cours': '409 Conflict',
           'transcription_en_cours': '409 Conflict', 'stockage_indisponible': '503 Service Unavailable',
           'moteur_absent': '503 Service Unavailable', 'requete_trop_longue': '413 Payload Too Large',
           'telechargement_impossible': '502 Bad Gateway', 'empreinte_incorrecte': '502 Bad Gateway', 'redirection_refusee': '502 Bad Gateway',
           'composant_microsoft_absent': '503 Service Unavailable', 'bibliotheque_systeme_absente': '503 Service Unavailable',
           'modele_illisible': '503 Service Unavailable', 'modele_empreinte_incorrecte': '409 Conflict', 'moteur_en_echec': '502 Bad Gateway', 'dictee_poste_sortie_absente': '502 Bad Gateway',
           'dictee_poste_delai_depasse': '504 Gateway Timeout'}
# Libellés de l'écran (traduits en anglais, allemand et espagnol dans agent/locales/ecrans_*.json)
TEXTES = {
    'titre': 'Dictée sur ce poste',
    'intro': 'Votre voix est transcrite sur ce poste, sans service extérieur. L’enregistrement n’est jamais conservé.',
    'moteur_present': 'Moteur de dictée : fourni avec cette installation.',
    'moteur_absent': 'Moteur de dictée : absent.',
    'serveur': 'Cette installation est un serveur ou une image Docker : le moteur de dictée n’y est pas fourni dans cette version. '
               'La dictée s’installe sur chaque poste (Windows 11 ou Ubuntu 24.04).',
    'hors_paquet': 'L’application est lancée hors paquet : le moteur de dictée n’est fourni qu’avec l’application installée.',
    'paquet_sans_moteur': 'Cette application a été construite sans le moteur de dictée : installez une version qui le contient.',
    'variable': 'Le dossier indiqué par la variable AXIORHUB_DICTEE ne contient pas le moteur de dictée.',
    'sans_bouton': 'Aucune installation n’est possible ici.',
    'active': 'Dictée sur ce poste : active.',
    'inactive': 'Dictée sur ce poste : inactive.',
    'modele_installe': 'Modèle choisi : %s, installé.',
    'modele_absent': 'Modèle choisi : %s, non installé.',
    'choix': 'Modèle de dictée',
    'mesure': 'Ordre de grandeur sur un poste récent, pour une phrase de 14 secondes : Rapide 2,4 s ; Équilibré 5,4 s ; Précis 19 s.',
    'langue': 'Langue de la dictée',
    'installer': 'Installer la dictée sur ce poste',
    'source': 'Le modèle est téléchargé une seule fois depuis huggingface.co (version figée) ; sa taille et son empreinte sont vérifiées avant de le garder. '
              'Aucune donnée du cabinet n’est envoyée.',
    'verifier': 'Vérifier le moteur',
    'retirer': 'Retirer le modèle',
    'desactiver': 'Désactiver',
    'enregistrer_langue': 'Enregistrer la langue',
    'essai': 'Zone d’essai : choisissez « Confidentiel », cliquez sur le micro, parlez, puis lisez le résultat. Ce texte n’est ni enregistré ni envoyé ailleurs.',
    'msvc': 'Le composant Microsoft Visual C++ manque sur ce poste : la dictée ne peut pas démarrer.',
    'msvc_lien': 'Télécharger le composant sur le site de Microsoft',
    'admin_seul': 'Seul l’administrateur installe ou retire la dictée.',
    'installe': 'installé',
    'non_installe': 'non installé',
    'refuse': 'empreinte fausse : fichier non utilisé',
    'en_cours': 'Téléchargement en cours…',
    'echec': 'Dernier téléchargement en échec :',
    'confirmer_retrait': 'Retirer ce modèle du poste ? La dictée sur ce poste sera désactivée si c’est le modèle choisi.',
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS dictee56110_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
_VERROU = threading.Lock()                # une seule transcription à la fois par processus
_VERROU_TELE = threading.Lock()           # un seul téléchargement à la fois par processus
_TELE = {'en_cours': False, 'modele': '', 'recus': 0, 'attendus': 0, 'erreur': '', 'state_dir': None, 'fil': None}
_DERNIERE = {'erreur': ''}                # code de la dernière erreur du moteur (jamais de texte dicté)


def _windows():
    from . import portable
    return portable.WINDOWS


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ================================================================================================ démarrage
def configurer(state_dir):
    """Appelé au démarrage (``Desk``) : restes d'un arrêt brutal retirés (fichiers partiels anciens, dossiers de travail anciens). Un
    fichier en cours d'écriture par un autre processus est récent : il n'est jamais touché."""
    if not state_dir:
        return
    base = Path(str(state_dir)) / DOSSIER
    limite = time.time() - 1800
    try:
        for p in (base / 'modeles').glob('.*.part'):
            if p.stat().st_mtime < limite:
                p.unlink()
        travail = base / 'travail'
        for p in travail.iterdir() if travail.is_dir() else ():
            if p.stat().st_mtime < limite:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def ensure_schema(desk):
    """Création idempotente au démarrage (``Desk``) ; jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ================================================================================================ moteur
def dossier_moteur():
    """Dossier du moteur : ``AXIORHUB_DICTEE`` ; sinon Windows : ``dictee`` à côté de l'exécutable empaqueté ; Linux : chemin fixe."""
    from . import portable
    valeur = os.environ.get('AXIORHUB_DICTEE', '').strip()
    if valeur:
        d = Path(valeur)
    elif portable.WINDOWS:
        if not portable.frozen():
            return None
        d = Path(sys.executable).resolve().parent / 'dictee'
    else:
        d = Path(LINUX)
    return d if d.is_dir() else None


def executable():
    """Chemin de ``whisper-cli.exe`` (Windows) ou ``whisper-cli`` (Linux) ; jamais le ``PATH``. ``None`` si absent."""
    d = dossier_moteur()
    if d is None:
        return None
    exe = d / ('whisper-cli.exe' if _windows() else 'whisper-cli')
    return str(exe) if exe.is_file() else None


def raison_absence(config):
    """Pourquoi le moteur manque, en clair : ``variable``, ``serveur``, ``hors_paquet`` ou ``paquet_sans_moteur``."""
    from . import portable
    if os.environ.get('AXIORHUB_DICTEE', '').strip():
        return 'variable'
    if portable.WINDOWS:
        return 'paquet_sans_moteur' if portable.frozen() else 'hors_paquet'
    try:
        from .diagnostic5634 import install_mode
        mode = install_mode(config or {})
    except Exception:   # noqa: BLE001 — mode inconnu : la cause la plus probable est dite
        mode = 'serveur'
    return 'serveur' if mode == 'serveur' else 'paquet_sans_moteur'


def base(state_dir):
    return Path(str(state_dir)) / DOSSIER


def chemin_modele(state_dir, ident):
    return base(state_dir) / 'modeles' / MODELES[ident]['fichier']


def modele_present(state_dir, ident):
    """Fichier du modèle présent à la bonne taille (téléchargé ici ou déposé à la main)."""
    if not state_dir or ident not in MODELES:
        return False
    try:
        p = chemin_modele(state_dir, ident)
        return p.is_file() and p.stat().st_size == MODELES[ident]['octets']
    except OSError:
        return False


def modele_installe(state_dir, ident):
    """Présent à la bonne taille et pas refusé par un contrôle d'empreinte (le contrôle lui-même est fait une fois, voir ``controler``)."""
    return modele_present(state_dir, ident) and _controle_connu(state_dir, ident) is not False


# Contrôle de l'empreinte d'un modèle déposé à la main (poste sans réseau) : SHA-256 du catalogue calculé une fois, à l'activation, à
# « Vérifier le moteur » ou à la première dictée ; le résultat est gardé avec la taille et la date de modification du fichier contrôlé
# (``<state_dir>/dictee56110/controles.json``) pour ne pas recalculer à chaque dictée. Un fichier remplacé est contrôlé de nouveau.
def _fichier_controles(state_dir):
    return base(state_dir) / 'controles.json'


def _lire_controles(state_dir):
    try:
        v = json.loads(_fichier_controles(state_dir).read_text(encoding='utf-8'))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _ecrire_controle(state_dir, ident, valeur):
    f = _fichier_controles(state_dir)
    tout = _lire_controles(state_dir)
    nom = MODELES[ident]['fichier']
    if valeur is None:
        tout.pop(nom, None)
    else:
        tout[nom] = valeur
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name('.controles.%s.tmp' % secrets.token_hex(4))
        tmp.write_text(json.dumps(tout, sort_keys=True), encoding='utf-8')
        os.replace(tmp, f)
    except OSError:
        pass


def _signature(state_dir, ident):
    st = os.stat(str(chemin_modele(state_dir, ident)))   # os.stat : date à la nanoseconde, même quand Path.stat est enveloppé (essais)
    return {'octets': st.st_size, 'mtime_ns': st.st_mtime_ns, 'sha256': MODELES[ident]['sha256']}


def _controle_connu(state_dir, ident):
    """True (empreinte vérifiée), False (empreinte fausse) ou None (pas encore contrôlé, ou fichier changé depuis)."""
    try:
        sig = _signature(state_dir, ident)
    except OSError:
        return None
    rec = _lire_controles(state_dir).get(MODELES[ident]['fichier'])
    if isinstance(rec, dict) and all(rec.get(k) == v for k, v in sig.items()):
        return bool(rec.get('ok'))
    return None


def _empreinte_fichier(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def _noter_controle(state_dir, ident, ok):
    try:
        _ecrire_controle(state_dir, ident, dict(_signature(state_dir, ident), ok=bool(ok)))
    except OSError:
        pass


def controler(state_dir, ident):
    """Empreinte du modèle vérifiée une fois (résultat gardé) ; ``Stop('modele_empreinte_incorrecte')`` si elle diffère du catalogue."""
    if not modele_present(state_dir, ident):
        raise Stop('modele_non_installe')
    connu = _controle_connu(state_dir, ident)
    if connu is None:
        try:
            connu = _empreinte_fichier(chemin_modele(state_dir, ident)) == MODELES[ident]['sha256']
        except OSError:
            raise Stop('stockage_indisponible') from None
        _noter_controle(state_dir, ident, connu)
    if not connu:
        _DERNIERE['erreur'] = 'modele_empreinte_incorrecte'
        raise Stop('modele_empreinte_incorrecte')


def fils():
    """Moitié des cœurs logiques, entre 2 et 8."""
    return max(2, min(8, (os.cpu_count() or 2) // 2))


# ================================================================================================ réglage
def _defaut():
    return {'moteur': '', 'modele': DEFAUT_MODELE, 'langue': 'fr', 'version': 0}


def reglages_de(state_dir):
    """Réglage lu sans écrire (ligne absente ou illisible : valeur par défaut, dictée du poste inactive)."""
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not str(state_dir or '') or not path.is_file():
        return _defaut()
    try:
        db = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, timeout=5)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
        finally:
            db.close()
        value = json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return _defaut()
    out = _defaut()
    if isinstance(value, dict):
        if value.get('moteur') in ('poste', ''):
            out['moteur'] = value['moteur']
        if value.get('modele') in MODELES:
            out['modele'] = value['modele']
        if value.get('langue') in LANGUES:
            out['langue'] = value['langue']
        v = value.get('version')
        out['version'] = v if isinstance(v, int) and v >= 0 else 0
    return out


def active(config):
    """Dictée du poste active : réglage « poste », moteur présent, modèle choisi présent à la bonne taille. Jamais d'exception."""
    try:
        state_dir = (config or {}).get('state_dir')
        if not state_dir:
            return False
        r = reglages_de(state_dir)
        return r['moteur'] == 'poste' and executable() is not None and modele_installe(state_dir, r['modele'])
    except Exception:   # noqa: BLE001 — en cas de doute, le chemin de la passerelle reste celui d'avant
        return False


def audio_actif(config):
    """Drapeau ``audio_enabled`` de l'interface : passerelle activée (``audio.enabled``) ou dictée du poste active."""
    return bool(((config or {}).get('audio') or {}).get('enabled')) or active(config)


def active_contexte():
    """Dictée du poste active pour la configuration de la requête en cours (``shell501.context``)."""
    try:
        from .shell501 import context
        return active(context() or {})
    except Exception:   # noqa: BLE001
        return False


# ================================================================================================ état
def etat(config, admin=True):
    """Moteur (présent ou non, où, pourquoi absent), modèles, réglage, dictée active, téléchargement en cours, dernière erreur lisible."""
    state_dir = (config or {}).get('state_dir')
    r = reglages_de(state_dir)
    exe = executable()
    moteur = {'present': exe is not None, 'version': VERSION_MOTEUR}
    if exe is not None:
        if admin:
            moteur['dossier'] = str(Path(exe).parent)
    else:
        moteur['raison'] = raison_absence(config)
        moteur['explication'] = TEXTES[moteur['raison']]
    modeles = [{'id': k, 'nom': m['nom'], 'fichier': m['fichier'], 'octets': m['octets'], 'installe': modele_installe(state_dir, k),
                'empreinte_refusee': modele_present(state_dir, k) and _controle_connu(state_dir, k) is False}
               for k, m in MODELES.items()]
    t = dict(_TELE) if _TELE.get('state_dir') == str(state_dir or '') else {'en_cours': False, 'modele': '', 'recus': 0, 'attendus': 0, 'erreur': ''}
    tele = {'en_cours': bool(t['en_cours']), 'modele': t['modele'], 'recus': int(t['recus']), 'attendus': int(t['attendus']),
            'erreur': t['erreur'], 'message': MESSAGES.get(t['erreur'], '') if t['erreur'] else ''}
    if tele['en_cours'] and tele['attendus']:
        tele['texte'] = 'Téléchargement : %s Mo sur %s Mo' % (_mo(tele['recus']), _mo(tele['attendus']))
    erreur = _DERNIERE['erreur']
    return {'moteur': moteur, 'modeles': modeles, 'reglage': r, 'active': bool(r['moteur'] == 'poste' and exe is not None and modele_installe(state_dir, r['modele'])),
            'telechargement': tele, 'derniere_erreur': erreur, 'message_erreur': MESSAGES.get(erreur, '') if erreur else ''}


def _mo(octets):
    return ('%.1f' % (octets / 1e6)).replace('.', ',')


# ================================================================================================ WAV
def lire_wav(raw):
    """En-tête contrôlé (RIFF/WAVE, PCM 16 bits, mono, 16 000 Hz) ; rend les échantillons bruts (octets). ``Stop`` sinon."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise Stop('dictee_wav_invalide')
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        nom, taille = raw[pos:pos + 4], struct.unpack('<I', raw[pos + 4:pos + 8])[0]
        corps = pos + 8
        if nom == b'fmt ':
            if taille < 16 or corps + 16 > len(raw):
                raise Stop('dictee_wav_invalide')
            fmt = struct.unpack('<HHIIHH', raw[corps:corps + 16])
        elif nom == b'data':
            data = bytes(raw[corps:min(len(raw), corps + taille)])
            break
        pos = corps + taille + (taille & 1)
    if fmt is None or data is None:
        raise Stop('dictee_wav_invalide')
    format_, canaux, taux, _octets_s, _bloc, bits = fmt
    if format_ != 1 or canaux != CANAUX or taux != TAUX or bits != BITS:
        raise Stop('dictee_wav_format_refuse')
    if len(data) % 2:
        data = data[:-1]
    if len(data) // 2 > MAX_SECONDES * TAUX:
        raise Stop('dictee_trop_longue')
    return data


def wav(pcm):
    """Fichier WAV canonique (en-tête de 44 octets) autour d'échantillons 16 bits mono à 16 kHz."""
    return (b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 1, CANAUX, TAUX, TAUX * 2, 2, BITS)
            + b'data' + struct.pack('<I', len(pcm)) + pcm)


def energie(pcm):
    """Énergie de la tranche de 30 ms la plus forte (moyenne quadratique, un échantillon sur deux), en Python pur (sans ``audioop``)."""
    a = array('h')
    a.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == 'big':
        a.byteswap()
    tranche, pas, haut = 480, 2, 0.0
    for i in range(0, len(a), tranche):
        bloc = a[i:i + tranche:pas]
        if bloc:
            moyenne = sum(x * x for x in bloc) / len(bloc)
            if moyenne > haut:
                haut = moyenne
    return haut ** 0.5


def silence(pcm):
    return energie(pcm) < SEUIL_SILENCE


def _norme(texte):
    return re.sub(r'[\W_]+', '', str(texte).replace('’', "'").lower())


_INVENTEES = frozenset(_norme(p) for p in PHRASES_INVENTEES)


def nettoyer(texte):
    """Lignes du fichier de sortie réunies ; étiquettes de silence retirées ; phrase inventée seule rendue comme texte vide."""
    lignes = [l.strip() for l in str(texte).replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    out = re.sub(r'\s+', ' ', ETIQUETTE.sub(' ', ' '.join(l for l in lignes if l))).strip()
    if not _norme(out) or _norme(out) in _INVENTEES:
        return ''
    return out


# ================================================================================================ transcription
def _lancer(args, cwd, delai):
    """Lance le moteur (remplacée dans les essais : aucun vrai moteur). Sortie standard et erreurs jamais lues. Rend le code de retour ;
    à l'échéance, le processus et ses descendants sont tués (``portable.kill_tree``) et ``Stop('dictee_poste_delai_depasse')``."""
    import subprocess
    from . import portable
    options = {'cwd': str(cwd), 'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'env': portable.tool_env()}
    if portable.WINDOWS:
        options['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    else:
        options['start_new_session'] = True    # groupe de processus propre : kill_tree l'arrête entier
    p = subprocess.Popen(args, **options)
    try:
        return p.wait(timeout=delai)
    except subprocess.TimeoutExpired:
        portable.kill_tree(p.pid)
        try:
            p.kill()
            p.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise Stop('dictee_poste_delai_depasse') from None


def _code_erreur(code):
    """Code de retour du moteur → cause lisible. Windows : 0xC0000135 (DLL absente, composant Microsoft), 3 (modèle illisible)."""
    c = int(code) & 0xFFFFFFFF
    if c == 0xC0000135:
        return 'composant_microsoft_absent'
    if c == 3:
        return 'modele_illisible'
    if c == 127 and not _windows():
        return 'bibliotheque_systeme_absente'
    return 'moteur_en_echec'


def _executer(config, pcm, ident=None, langue=None):
    """Écrit ``d.wav`` dans un dossier de travail propre, lance le moteur avec des noms relatifs en ASCII, lit ``d.txt``, supprime le
    dossier de travail dans tous les cas. Rend le texte brut du fichier de sortie. Une seule exécution à la fois (verrou)."""
    state_dir = (config or {}).get('state_dir')
    exe = executable()
    if exe is None:
        raise Stop('moteur_absent')
    r = reglages_de(state_dir)
    ident = ident or r['modele']
    langue = langue or r['langue']
    controler(state_dir, ident)                  # une fois par fichier ; ensuite lu dans controles.json
    if not _VERROU.acquire(blocking=False):
        raise Stop('transcription_en_cours')
    racine = base(state_dir)
    jeton = secrets.token_hex(8)
    travail = racine / 'travail' / jeton
    try:
        try:
            travail.mkdir(parents=True)
            (travail / 'd.wav').write_bytes(wav(pcm))
        except OSError:
            raise Stop('stockage_indisponible') from None
        args = [exe, '-m', 'modeles/' + MODELES[ident]['fichier'], '-f', 'travail/%s/d.wav' % jeton, '-l', langue, '-nt', '-np',
                '-otxt', '-of', 'travail/%s/d' % jeton, '-t', str(fils())]
        delai = max(10, min(3600, int(((config or {}).get('audio') or {}).get('dictation_timeout_seconds', 180) or 180)))
        code = _lancer(args, racine, delai)
        if code != 0:
            raise Stop(_code_erreur(code))
        sortie = travail / 'd.txt'
        if not sortie.is_file():
            raise Stop('dictee_poste_sortie_absente')
        try:
            texte = sortie.read_bytes().decode('utf-8', 'replace')
        except OSError:
            raise Stop('dictee_poste_sortie_absente') from None
        _DERNIERE['erreur'] = ''
        return texte
    except Stop as ex:
        _DERNIERE['erreur'] = str(ex).split(':')[0]
        raise
    finally:
        shutil.rmtree(travail, ignore_errors=True)
        _VERROU.release()


def transcrire(config, raw):
    """WAV contrôlé → texte (vide si silence ou phrase inventée seule). Le moteur n'est pas lancé sur un silence."""
    pcm = lire_wav(raw)
    if not pcm or silence(pcm):
        return ''
    texte = nettoyer(_executer(config, pcm))
    if len(texte) > MAX_TEXTE:
        raise Stop('transcription_invalide')
    return texte


def dicter(config, raw, content_type):
    """Route ``POST /dictation`` quand la dictée du poste est active : seul ``audio/wav`` est accepté. Même forme de réponse qu'avant."""
    if content_type != 'audio/wav':
        raise Stop('dictee_poste_format_non_wav')
    if not raw or len(raw) > MAX_OCTETS:
        raise Stop('dictee_trop_volumineuse')
    return {'text': transcrire(config, raw), 'submitted': False}


def verifier(config):
    """« Vérifier le moteur » : une seconde de silence fabriquée, passée au moteur (prouve binaires, composant Microsoft, modèle lisible)."""
    debut = time.monotonic()
    texte = _executer(config, b'\0\0' * TAUX)
    duree = time.monotonic() - debut
    return {'ok': True, 'duree': round(duree, 1), 'texte_vide': nettoyer(texte) == '',
            'message': 'Moteur vérifié : réponse en %s s.' % ('%.1f' % duree).replace('.', ',')}


# ================================================================================================ téléchargement d'un modèle
def hote_permis(url):
    """HTTPS, port par défaut, sans identifiants, vers huggingface.co ou un hôte finissant par .hf.co ou .huggingface.co."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url))
        h = (u.hostname or '').lower()
        return (u.scheme == 'https' and u.port in (None, 443) and not u.username and not u.password
                and (h == 'huggingface.co' or h.endswith('.hf.co') or h.endswith('.huggingface.co')))
    except ValueError:
        return False


def _ouvrir(url, timeout):
    """Ouverture HTTPS ; redirections suivies seulement vers les hôtes permis (remplacée dans les essais : aucun réseau)."""
    import urllib.request
    if not hote_permis(url):
        raise Stop('redirection_refusee')

    class Redirection(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not hote_permis(newurl):
                raise Stop('redirection_refusee')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(Redirection)
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'AxiorHub'}), timeout=timeout)


def telecharger(state_dir, ident, ouvrir=None, progression=None):
    """Télécharge, vérifie (taille, SHA-256) et installe un modèle. Rend {'octets', 'sha256'}. ``Stop`` sinon, sans aucun reste."""
    meta = MODELES.get(ident)
    if meta is None:
        raise Stop('modele_inconnu')
    if not state_dir:
        raise Stop('stockage_indisponible')
    dossier = base(state_dir) / 'modeles'
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        libre = shutil.disk_usage(dossier).free
    except OSError:
        raise Stop('stockage_indisponible') from None
    if libre < meta['octets'] + 50_000_000:
        raise Stop('espace_insuffisant')
    final = dossier / meta['fichier']
    tmp = dossier / ('.%s.%s.part' % (meta['fichier'], secrets.token_hex(6)))
    h, recu = hashlib.sha256(), 0
    try:
        try:
            reponse = (ouvrir or _ouvrir)(URL % (REVISION, meta['fichier']), DELAI_TELECHARGEMENT)
            with reponse, open(tmp, 'wb') as f:
                while True:
                    bloc = reponse.read(1 << 20)
                    if not bloc:
                        break
                    recu += len(bloc)
                    if recu > meta['octets']:
                        raise Stop('empreinte_incorrecte')
                    h.update(bloc)
                    f.write(bloc)
                    if progression is not None:
                        progression(recu)
                f.flush()
                os.fsync(f.fileno())
        except Stop:
            raise
        except Exception:   # noqa: BLE001 — réseau, serveur, disque : rien n'est gardé
            raise Stop('telechargement_impossible') from None
        if recu != meta['octets'] or h.hexdigest() != meta['sha256']:
            raise Stop('empreinte_incorrecte')
        os.replace(tmp, final)
        _noter_controle(state_dir, ident, True)   # empreinte vérifiée pendant le téléchargement : pas de second calcul
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    try:
        from .passerelle5689 import compter
        compter(state_dir, SOURCE, 'telechargement_modele', 'admis')
    except Exception:   # noqa: BLE001 — le compte des destinations est facultatif
        pass
    return {'octets': recu, 'sha256': h.hexdigest()}


def _fil_telechargement(config, ident, owner):
    """Corps du fil d'arrière-plan : téléchargement, état consultable, audit de la fin (aucune donnée du cabinet)."""
    state_dir = config.get('state_dir')
    audit, detail = 'dictee_poste_modele_installe', {}
    try:
        bilan = telecharger(state_dir, ident, progression=lambda n: _TELE.__setitem__('recus', n))
        detail = {'modele': ident, 'octets': bilan['octets'], 'sha256': bilan['sha256'], 'revision': REVISION}
        _TELE['erreur'] = ''
    except Stop as ex:
        _TELE['erreur'] = str(ex).split(':')[0]
        audit, detail = 'dictee_poste_telechargement_refuse', {'modele': ident, 'motif': _TELE['erreur']}
    except Exception:   # noqa: BLE001 — jamais de fil mort silencieux : l'échec est dit dans la carte
        _TELE['erreur'] = 'telechargement_impossible'
        audit, detail = 'dictee_poste_telechargement_refuse', {'modele': ident, 'motif': 'telechargement_impossible'}
    finally:
        _TELE['en_cours'] = False
        _VERROU_TELE.release()
    try:
        from .desk import Desk
        d = Desk(config)
        try:
            d.audit(audit, dict(detail, auteur=str(owner)[:120]))
        finally:
            d.db.close()
    except Exception:   # noqa: BLE001 — l'audit de fin ne doit pas masquer l'état affiché
        pass


def lancer_telechargement(config, ident, owner='cabinet'):
    """Un seul téléchargement à la fois ; le fil met à jour l'état (octets reçus sur octets attendus) interrogé par la carte."""
    if ident not in MODELES:
        raise Stop('modele_inconnu')
    if not _VERROU_TELE.acquire(blocking=False):
        raise Stop('telechargement_en_cours')
    _TELE.update({'en_cours': True, 'modele': ident, 'recus': 0, 'attendus': MODELES[ident]['octets'], 'erreur': '',
                  'state_dir': str(config.get('state_dir') or '')})
    try:
        fil = threading.Thread(target=_fil_telechargement, args=(dict(config), ident, owner), daemon=True, name='dictee56110-telechargement')
        _TELE['fil'] = fil
        fil.start()
    except Exception:   # noqa: BLE001 — fil non démarré : verrou rendu
        _TELE['en_cours'] = False
        _VERROU_TELE.release()
        raise Stop('telechargement_impossible') from None
    return fil


def retirer(state_dir, ident):
    if ident not in MODELES:
        raise Stop('modele_inconnu')
    if _TELE['en_cours'] and _TELE['modele'] == ident:
        raise Stop('telechargement_en_cours')
    p = chemin_modele(state_dir, ident)
    if not p.is_file():
        raise Stop('modele_non_installe')
    try:
        p.unlink()
    except OSError:
        raise Stop('stockage_indisponible') from None
    _ecrire_controle(state_dir, ident, None)


# ================================================================================================ routes /api440/m56110/dictee/*
def allowed(role, method, api):
    """Lecture de l'état : tous les rôles ; écritures (installer, retirer, réglage, vérifier) : administrateur seulement."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD') and reste in LECTURES:
        return True
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def message(code):
    return MESSAGES.get(code) or code.replace('_', ' ')


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste in LECTURES and method == 'GET':
        return _json(etat(desk.c, admin=role == 'administrateur'))
    if reste not in ECRITURES or method != 'POST':
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(role, method, PREFIXE + reste):
        return _json({'error': 'role_insuffisant', 'message': MESSAGES['role_insuffisant']}, STATUTS['role_insuffisant'])
    try:
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth)
        return _json(ecrire(desk, owner, reste, data))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return _json({'error': code, 'message': message(code)}, STATUTS.get(code, '400 Bad Request'))


def _recu(desk, owner, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    r = desk.db.execute('SELECT empreinte,reponse FROM dictee56110_recus WHERE compte=? AND route=? AND cle=?', (owner, route, cle)).fetchone()
    if r and r[0] != empreinte:
        raise Stop('cle_reutilisee')
    return json.loads(r[1]) if r else None


def _garder(desk, owner, route, cle, empreinte, out):
    desk.db.execute('INSERT OR IGNORE INTO dictee56110_recus VALUES(?,?,?,?,?,?)', (owner, route, cle, empreinte, json.dumps(out, ensure_ascii=False), _now()))
    desk.db.commit()


def _enregistrer(desk, r, **champs):
    valeur = {k: r[k] for k in ('moteur', 'modele', 'langue')}
    valeur.update(champs)
    valeur['version'] = r['version'] + 1
    desk.setting(KEY, valeur)
    return valeur


def ecrire(desk, owner, action, d):
    """Installer, retirer, régler ou vérifier (administrateur ; clé d'idempotence et version pour les écritures ; 409 si périmée)."""
    state_dir = desk.c.get('state_dir')
    if action == 'verifier':   # rien n'est écrit : ni clé ni version ; une seule exécution à la fois (verrou du moteur)
        try:
            out = verifier(desk.c)
        except Stop as ex:
            desk.audit('dictee_poste_verification', {'resultat': str(ex).split(':')[0]})
            raise
        desk.audit('dictee_poste_verification', {'resultat': 'ok', 'duree': out['duree']})
        return {'message': out['message'], 'duree': out['duree'], 'noreload': True}
    cle = str(d.get('cle') or '')
    empreinte = hashlib.sha256(json.dumps([action, {k: v for k, v in d.items() if k != 'csrf'}], sort_keys=True, default=str).encode()).hexdigest()
    ancien = _recu(desk, owner, 'dictee/' + action, cle, empreinte)   # même clé, même demande : la réponse gardée, rien n'est rejoué
    if ancien is not None:
        return ancien
    r = reglages_de(state_dir)
    if str(d.get('version')) != str(r['version']):
        raise Stop('version_perimee')
    if action == 'installer':
        ident = str(d.get('modele') or '')
        langue = str(d.get('langue') or r['langue'])
        if ident not in MODELES:
            raise Stop('modele_inconnu')
        if langue not in LANGUES:
            raise Stop('langue_inconnue')
        if executable() is None:
            raise Stop('moteur_absent')
        if modele_present(state_dir, ident) and _controle_connu(state_dir, ident) is not False:
            controler(state_dir, ident)          # modèle déposé à la main : empreinte vérifiée avant d'activer
            v = _enregistrer(desk, r, moteur='poste', modele=ident, langue=langue)
            desk.audit('dictee_poste_activee', {'modele': ident, 'langue': langue, 'version': v['version']})
            out = {'message': 'Dictée sur ce poste activée (modèle %s).' % MODELES[ident]['nom'], 'version': v['version']}
        else:
            lancer_telechargement(desk.c, ident, owner)
            v = _enregistrer(desk, r, moteur='poste', modele=ident, langue=langue)   # active dès que le fichier vérifié est en place
            desk.audit('dictee_poste_installation_demandee', {'modele': ident, 'octets': MODELES[ident]['octets'], 'source': SOURCE,
                                                               'revision': REVISION, 'version': v['version']})
            out = {'message': 'Téléchargement du modèle lancé : la dictée sera active dès la fin de la vérification.', 'version': v['version']}
    elif action == 'retirer':
        ident = str(d.get('modele') or '')
        retirer(state_dir, ident)
        champs = {'moteur': ''} if ident == r['modele'] else {}
        v = _enregistrer(desk, r, **champs)
        desk.audit('dictee_poste_modele_retire', {'modele': ident, 'version': v['version']})
        out = {'message': 'Modèle retiré du poste : %s.' % MODELES[ident]['nom'], 'version': v['version']}
    else:   # reglage : modèle, langue, activer ou désactiver
        champs = {}
        if 'modele' in d:
            if d['modele'] not in MODELES:
                raise Stop('modele_inconnu')
            champs['modele'] = d['modele']
        if 'langue' in d:
            if d['langue'] not in LANGUES:
                raise Stop('langue_inconnue')
            champs['langue'] = d['langue']
        if 'moteur' in d:
            if d['moteur'] not in ('poste', ''):
                raise Stop('introuvable')
            champs['moteur'] = d['moteur']
        futur = dict(r, **champs)
        if futur['moteur'] == 'poste':
            if executable() is None:
                raise Stop('moteur_absent')
            controler(state_dir, futur['modele'])
        v = _enregistrer(desk, r, **champs)
        desk.audit('dictee_poste_reglage', {'moteur': v['moteur'], 'modele': v['modele'], 'langue': v['langue'], 'version': v['version']})
        out = {'message': 'Dictée sur ce poste désactivée.' if v['moteur'] != 'poste' else 'Réglage de la dictée enregistré.', 'version': v['version']}
    _garder(desk, owner, 'dictee/' + action, cle, empreinte, out)
    return out


# ================================================================================================ présentation (Paramètres › Voix)
def _cle():
    return secrets.token_hex(12)


def _taille(ident):
    return '%s Mo' % _mo(MODELES[ident]['octets'])


def _cache(champs):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (e(k), e(str(v))) for k, v in champs.items())


def section_html(desk, prefix, role):
    """Carte « Dictée sur ce poste », au-dessus de la section « Moteurs de voix »."""
    config = desk.c
    state_dir = config.get('state_dir')
    admin = role == 'administrateur'
    s = etat(config, admin=admin)
    r = s['reglage']
    T = TEXTES
    h = ['<section class="ax-card d110-carte" id="dictee56110" aria-labelledby="d110-titre"><h2 id="d110-titre">%s</h2><p>%s</p>' % (e(T['titre']), e(T['intro']))]
    if not s['moteur']['present']:
        h.append('<ul class="d110-etat"><li>%s</li></ul><p class="ax-alert" role="status">%s</p><p class="ax-muted">%s</p></section>' % (
            e(T['moteur_absent']), e(s['moteur']['explication']), e(T['sans_bouton'])))
        return ''.join(h)
    choisi = MODELES[r['modele']]
    nom_choisi = '%s (%s)' % (choisi['nom'], _taille(r['modele']))
    h.append('<ul class="d110-etat"><li>%s</li><li>%s</li><li><strong>%s</strong></li></ul>' % (
        e(T['moteur_present']), e((T['modele_installe'] if modele_installe(state_dir, r['modele']) else T['modele_absent']) % nom_choisi),
        e(T['active'] if s['active'] else T['inactive'])))
    if s['derniere_erreur'] == 'composant_microsoft_absent':
        h.append('<p class="ax-alert" role="alert">%s <a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>' % (
            e(T['msvc']), e(VC_REDIST), e(T['msvc_lien'])))
    elif s['derniere_erreur']:
        h.append('<p class="ax-alert" role="status">%s</p>' % e(s['message_erreur']))
    t = s['telechargement']
    if t['en_cours']:
        h.append('<div class="d110-progression" data-d110-progression data-en-cours="1" role="status" aria-live="polite">'
                 '<progress max="%d" value="%d" aria-labelledby="d110-progression-texte"></progress> <span id="d110-progression-texte">%s</span></div>' % (
                     t['attendus'], t['recus'], e(t.get('texte') or T['en_cours'])))
    else:
        h.append('<div class="d110-progression" data-d110-progression role="status" aria-live="polite">%s</div>' % (
            ('<p class="ax-alert">%s</p><p>%s</p>' % (e(T['echec']), e(t['message']))) if t['erreur'] else ''))
    if not admin:
        h.append('<p class="ax-muted">%s</p>' % e(T['admin_seul']))
    else:
        lignes = []
        for ident, m in MODELES.items():
            installe = modele_installe(state_dir, ident)
            lignes.append('<label class="d110-modele"><input type="radio" name="modele" value="%s"%s data-d110-modele> <strong>%s</strong>'
                          ' <span class="d110-conseil">%s</span> <span class="d110-statut">%s</span></label>' % (
                              ident, ' checked' if ident == r['modele'] else '', e('%s (%s)' % (m['nom'], _taille(ident))), e(m['conseil']),
                              e(T['installe'] if installe else (T['refuse'] if modele_present(state_dir, ident) else T['non_installe']))))
        options = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == r['langue'] else '', e(v)) for k, v in LANGUES.items())
        tailles = ''.join('<span data-d110-taille="%s"%s>(%s)</span>' % (k, '' if k == r['modele'] else ' hidden', e(_taille(k))) for k in MODELES)
        inactif = ' disabled' if t['en_cours'] else ''
        h.append('<form class="m5-form d110-installer" data-api="m56110/dictee/installer" data-reload><fieldset><legend>%s</legend>%s'
                 '<p class="ax-muted">%s</p></fieldset><label class="d110-langue">%s <select name="langue">%s</select></label>%s'
                 '<button class="ax-btn" type="submit"%s>%s %s</button><p class="ax-muted">%s</p><p role="status" class="m5-status"></p></form>' % (
                     e(T['choix']), ''.join(lignes), e(T['mesure']), e(T['langue']), options, _cache({'version': r['version'], 'cle': _cle()}),
                     inactif, e(T['installer']), tailles, e(T['source'])))
        actions = []
        if modele_installe(state_dir, r['modele']):
            actions.append('<form class="m5-form d51-inline" data-api="m56110/dictee/verifier"><button class="ax-btn" type="submit">%s</button>'
                           '<p role="status" class="m5-status"></p></form>' % e(T['verifier']))
        if s['active']:
            actions.append('<form class="m5-form d51-inline" data-api="m56110/dictee/reglage" data-reload>%s<button class="ax-btn ghost" type="submit">%s</button>'
                           '<p role="status" class="m5-status"></p></form>' % (_cache({'moteur': '', 'version': r['version'], 'cle': _cle()}), e(T['desactiver'])))
        for ident, m in MODELES.items():
            if modele_present(state_dir, ident) and not (t['en_cours'] and t['modele'] == ident):
                actions.append('<form class="m5-form d51-inline" data-api="m56110/dictee/retirer" data-reload data-confirm="%s">%s'
                               '<button class="ax-btn ghost" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
                                   e(T['confirmer_retrait']), _cache({'modele': ident, 'version': r['version'], 'cle': _cle()}), e('%s : %s' % (T['retirer'], m['nom']))))
        if actions:
            h.append('<div class="d110-actions">%s</div>' % ''.join(actions))
    if s['active']:
        h.append('<div class="d110-essai"><label for="d110-essai">%s</label><textarea id="d110-essai" rows="3" autocomplete="off" spellcheck="false"></textarea></div>' % e(T['essai']))
    h.append('</section>')
    return ''.join(h)

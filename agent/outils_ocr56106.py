"""5.6.106 (lot W) : Tesseract et Poppler embarqués dans l'application Windows ; langues de lecture des scans (OCR).

- **Recherche des outils** (``chemin``), seule fonction utilisée par la lecture des pièces (``documents``), l'OCR des pièces
  (``pieces520``), les images pour les modèles capables de voir (``vision56106``) et, par ``portable.which``, l'aperçu des Word
  (``cabinet_docs33``). Windows : dossier ``ocr`` à côté de l'exécutable (``ocr\\tesseract\\tesseract.exe``,
  ``ocr\\poppler\\pdftoppm.exe``…), avant le ``PATH`` et les dossiers d'installation habituels. Hors application empaquetée,
  ``AXIORHUB_WIN_OCR`` désigne ce dossier (essais sur la copie réduite produite par ``scripts/preparer-ocr-windows.py``). Linux :
  chemins fixes ``/usr/bin`` (paquets ``tesseract-ocr``, ``tesseract-ocr-fra``, ``poppler-utils``), jamais le ``PATH``.
- **Données de Tesseract** : ``TESSDATA_PREFIX`` (ajouté par ``portable.tool_env``) désigne ``ocr\\tesseract\\tessdata`` ; dès qu'une
  langue facultative est ajoutée, un dossier du cabinet (``<state_dir>/ocr56106/tessdata``) qui contient les langues intégrées
  (copiées) **et** les langues ajoutées, Tesseract n'acceptant qu'un seul dossier de données.
- **Langues facultatives** (Paramètres › IA › Lecture des scans, administrateur) : espagnol et allemand, téléchargés seulement à la
  demande depuis le dépôt tesseract-ocr/tessdata à un commit figé ; taille, empreinte Git (SHA-1 de l'objet) et SHA-256 vérifiées
  **avant** de garder le fichier (écriture dans un fichier temporaire du même dossier, puis remplacement atomique ; aucun fichier
  partiel ne reste) ; journal d'audit ; retrait possible. Linux : la commande ``apt`` est affichée, rien n'est téléchargé.
- **Langues utilisées** par l'OCR : réglage du cabinet (``fra+eng`` par défaut), restreint aux langues réellement installées.
"""
import hashlib
from html import escape as e
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

from .common import Stop

KEY = 'ocr:langues56106'
PREFIXE = 'm56106/ocr/'
ECRITURES = ('ajouter', 'retirer', 'langues')
OUTILS = {'tesseract': 'tesseract', 'pdftoppm': 'poppler', 'pdftotext': 'poppler', 'pdfinfo': 'poppler'}
DEFAUT = 'fra+eng'
BASE = ('fra.traineddata', 'eng.traineddata', 'osd.traineddata', 'pdf.ttf', 'configs/tsv', 'configs/pdf', 'configs/txt', 'configs/hocr')
REQUIS = ('fra.traineddata', 'eng.traineddata', 'pdf.ttf', 'configs/tsv', 'configs/pdf')
NOMS = {'fra': 'français', 'eng': 'anglais', 'spa': 'espagnol', 'deu': 'allemand', 'osd': 'orientation (osd)'}
COMMIT = 'c2b2e0df86272ce11be323f23f96cf656565ed41'   # tesseract-ocr/tessdata, 10/05/2018 (dernier changement de spa, fra et eng)
URL = 'https://github.com/tesseract-ocr/tessdata/raw/%s/%s.traineddata'
HOTES = ('github.com', 'raw.githubusercontent.com')
# Métadonnées lues le 16/11/2026 par l'API GitHub (contents?ref=COMMIT), sans télécharger les fichiers : taille et empreinte Git de
# l'objet (SHA-1 de « blob <taille>\0<contenu> »). Les mêmes métadonnées de fra et eng correspondent aux fichiers téléchargés avec
# l'accord de l'avocat. L'API ne donne pas de SHA-256 : calculé sur les fichiers téléchargés une fois au même commit (16/11/2026,
# accord de l'avocat), dont l'empreinte Git correspond. Une empreinte absente (``None``) désactive le bouton et refuse le téléchargement.
LANGUES_OPTIONNELLES = {
    'spa': {'nom': 'espagnol', 'bouton': 'Ajouter l’espagnol', 'octets': 18256019, 'git': 'f703e0535e08fce166b3554b330b28f86a369de1',
            'sha256': '0b0fcbb4665189e01ab8019e591f014dd7260460de072543edd4b2cb4ede7c96', 'apt': 'tesseract-ocr-spa'},
    'deu': {'nom': 'allemand', 'bouton': 'Ajouter l’allemand', 'octets': 15437534, 'git': '36f623a0b724d546ba831e7eeead33bf4c6cb24f',
            'sha256': '896b3b4956503ab9daa10285db330881b2d74b70d889b79262cc534b9ec699a4', 'apt': 'tesseract-ocr-deu'},
}
DELAI = 120
MAX_POST = 4_000
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
MESSAGES = {
    'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Réservé à l’administrateur du cabinet.',
    'version_perimee': 'Les réglages ont changé entre-temps : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'cle_reutilisee': 'Cette demande a déjà été utilisée pour une autre action : rechargez la page.',
    'langue_inconnue': 'Langue non proposée.', 'linux_apt': 'Sur Linux, les langues s’installent par apt (commande affichée).',
    'empreinte_a_completer': 'Empreinte SHA-256 de ce fichier non renseignée dans cette version : téléchargement refusé.',
    'tesseract_integre_absent': 'Tesseract intégré absent de cette installation : ajout impossible.',
    'stockage_indisponible': 'Dossier des données du cabinet indisponible.', 'operation_en_cours': 'Une autre opération sur les langues est en cours.',
    'empreinte_incorrecte': 'Fichier reçu différent du fichier attendu (taille ou empreinte) : rejeté, rien n’a été gardé.',
    'telechargement_impossible': 'Téléchargement impossible (réseau ou serveur) : rien n’a été gardé.',
    'redirection_refusee': 'Adresse de téléchargement inattendue : rejetée.',
    'langue_non_installee': 'Langue non installée sur ce poste.', 'aucune_langue': 'Choisissez au moins une langue.',
    'requete_trop_longue': 'Demande trop longue.', 'origine_ou_csrf_refuse': 'Demande refusée.',
}
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict',
           'stockage_indisponible': '503 Service Unavailable', 'telechargement_impossible': '502 Bad Gateway',
           'empreinte_incorrecte': '502 Bad Gateway', 'redirection_refusee': '502 Bad Gateway', 'requete_trop_longue': '413 Payload Too Large'}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS ocr56106_recus(compte TEXT NOT NULL, route TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL, reponse TEXT NOT NULL,
  at TEXT NOT NULL, PRIMARY KEY(compte, route, cle));
'''
_ETAT = {'state_dir': None}
_CACHE = {}
_VERROU = threading.Lock()


def _windows():
    from . import portable
    return portable.WINDOWS


def configurer(state_dir):
    """Appelé au démarrage (``Desk``) : dossier des données du cabinet (langues ajoutées, réglage des langues)."""
    _ETAT['state_dir'] = str(state_dir or '') or None
    _oublier()


def _assurer(state_dir):
    """Dossier du cabinet connu (sans effacer les caches s'il ne change pas)."""
    if _ETAT['state_dir'] != (str(state_dir or '') or None):
        configurer(state_dir)


def _oublier():
    """Langues relues après un changement (réglage, langue ajoutée ou retirée), y compris par l'OCR des pièces (``pieces520``)."""
    _CACHE.clear()
    try:
        from . import pieces520
        pieces520._langs.clear()
    except Exception:   # noqa: BLE001 — module absent : rien à oublier
        pass


def ensure_schema(desk):
    """Création idempotente au démarrage (``Desk``) ; jamais pendant une lecture."""
    desk.db.executescript(SCHEMA)
    desk.db.commit()


# ================================================================================================ recherche des outils
def dossier_embarque():
    """Dossier ``ocr`` de l'application Windows (à côté de l'exécutable) ou ``AXIORHUB_WIN_OCR`` ; ``None`` ailleurs."""
    if not _windows():
        return None
    from . import portable
    valeur = os.environ.get('AXIORHUB_WIN_OCR', '').strip()
    if valeur:
        d = Path(valeur)
    elif portable.frozen():
        d = Path(sys.executable).resolve().parent / 'ocr'
    else:
        return None
    return d if d.is_dir() else None


def embarque(nom):
    """Chemin de l'outil embarqué (``tesseract``, ``pdftoppm``, ``pdftotext``, ``pdfinfo``) ou ``None``."""
    sous = OUTILS.get(nom)
    d = dossier_embarque() if sous else None
    if d is None:
        return None
    exe = d / sous / (nom + '.exe')
    return str(exe) if exe.is_file() else None


def chemin(nom):
    """Chemin de l'outil : Windows, embarqué d'abord, puis ``PATH`` et dossiers d'installation habituels ; Linux, ``/usr/bin`` fixe."""
    from . import portable
    if not portable.WINDOWS:
        p = '/usr/bin/' + nom
        return p if Path(p).is_file() else None
    return embarque(nom) or portable._which(nom)


def origine(nom):
    """« intégré », « poste » (installé à part sur le poste ou le serveur) ou « absent »."""
    if embarque(nom):
        return 'intégré'
    return 'poste' if chemin(nom) else 'absent'


# ================================================================================================ données de Tesseract
def tessdata_integre():
    d = dossier_embarque()
    t = d / 'tesseract' / 'tessdata' if d else None
    return t if t is not None and t.is_dir() else None


def tessdata_cabinet():
    s = _ETAT['state_dir']
    return Path(s) / 'ocr56106' / 'tessdata' if s else None


def _complet(d):
    return d is not None and all((d / f).is_file() for f in REQUIS)


def _ajoutees(d):
    return sorted(l for l in LANGUES_OPTIONNELLES if d is not None and (d / (l + '.traineddata')).is_file())


def tessdata():
    """Dossier de données à donner à Tesseract (Windows) : celui du cabinet s'il contient une langue ajoutée et les langues intégrées,
    sinon celui de l'application ; ``None`` (Linux, ou Tesseract installé à part : il garde son propre dossier)."""
    if not _windows():
        return None
    c = tessdata_cabinet()
    if _complet(c) and _ajoutees(c):
        return c
    return tessdata_integre()


def env_tesseract():
    """Variables à ajouter à l'environnement des outils externes (``portable.tool_env``)."""
    d = tessdata()
    return {'TESSDATA_PREFIX': str(d)} if d is not None else {}


def installees():
    """Langues installées (Windows : fichiers du dossier de données) ; ``None`` si inconnu (Linux, Tesseract installé à part)."""
    d = tessdata()
    if d is None:
        return None
    return sorted(p.stem for p in d.glob('*.traineddata'))


def installees_systeme(timeout=20):
    """Linux ou Tesseract installé à part : ``tesseract --list-langs`` (lecture seule). ``None`` si l'outil manque ou échoue."""
    exe = chemin('tesseract')
    if not exe:
        return None
    try:
        from . import portable
        import subprocess
        p = portable.run_tool([exe, '--list-langs'], timeout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=portable.tool_env())
    except Exception:   # noqa: BLE001 — outil absent ou en panne : langues inconnues
        return None
    lignes = p.stdout.decode('utf-8', 'replace').splitlines()[1:] if not p.returncode else []
    return sorted(l.strip() for l in lignes if re.fullmatch(r'[a-z_]{3,12}', l.strip())) or None


def langues_installees():
    """Langues installées, connues sans outil (fichiers) ou demandées à Tesseract (``--list-langs``) ; ``None`` si inconnu."""
    presentes = installees()
    return presentes if presentes is not None else installees_systeme()


# ================================================================================================ réglage des langues utilisées
def _defaut():
    return {'langues': DEFAUT, 'version': 0}


def reglages_de(state_dir):
    """Réglage lu sans écrire (ligne absente : valeur par défaut)."""
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
        if isinstance(value.get('langues'), str) and re.fullmatch(r'[a-z]{3}(\+[a-z]{3}){0,5}', value['langues']):
            out['langues'] = value['langues']
        v = value.get('version')
        out['version'] = v if isinstance(v, int) and v >= 0 else 0
    return out


def langues():
    """Langues passées à Tesseract (« -l ») : réglage du cabinet restreint aux langues installées (Windows), ``fra+eng`` sinon."""
    maintenant = time.monotonic()
    if _CACHE.get('jusqu_a', 0) > maintenant:
        voulues = _CACHE['langues']
    else:
        voulues = reglages_de(_ETAT['state_dir'])['langues'] if _ETAT['state_dir'] else DEFAUT
        _CACHE.update({'langues': voulues, 'jusqu_a': maintenant + 10})
    presentes = installees()
    if presentes is None:
        return voulues
    garde = [l for l in voulues.split('+') if l in presentes]
    if garde:
        return '+'.join(garde)
    return '+'.join(l for l in DEFAUT.split('+') if l in presentes) or DEFAUT


# ================================================================================================ téléchargement d'une langue
def hote_permis(url):
    """Seules les adresses HTTPS de github.com et raw.githubusercontent.com (port par défaut) sont suivies."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(url))
        return u.scheme == 'https' and u.hostname in HOTES and u.port in (None, 443) and not u.username and not u.password
    except ValueError:
        return False


def _ouvrir(url, timeout):
    """Ouverture HTTPS ; redirections suivies seulement vers github.com et raw.githubusercontent.com (remplacée dans les essais)."""
    import urllib.request

    class Redirection(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not hote_permis(newurl):
                raise Stop('redirection_refusee')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(Redirection)
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'AxiorHub'}), timeout=timeout)


def _relier(source, cible):
    """Lien physique (même volume) ou copie, écrit sous un nom temporaire puis remplacé atomiquement."""
    cible.parent.mkdir(parents=True, exist_ok=True)
    tmp = cible.with_name('.%s.%s.part' % (cible.name, secrets.token_hex(4)))
    try:
        try:
            os.link(source, tmp)
        except OSError:
            shutil.copyfile(source, tmp)
        os.replace(tmp, cible)
    finally:
        if tmp.exists():
            tmp.unlink()


def _copier_base(source, cible):
    for f in BASE:
        s, c = source / f, cible / f
        if s.is_file() and (not c.is_file() or c.stat().st_size != s.stat().st_size):
            _relier(s, c)


def _nettoyer(cible):
    """Sans langue ajoutée, le dossier du cabinet ne sert plus : retiré (le dossier de l'application reprend la main)."""
    if cible is not None and cible.is_dir() and not _ajoutees(cible) and cible.name == 'tessdata' and cible.parent.name == 'ocr56106':
        shutil.rmtree(cible.parent, ignore_errors=True)


def telecharger(lang, ouvrir=None):
    """Télécharge, vérifie et installe une langue facultative (Windows). Renvoie {'octets', 'sha256'}. ``Stop`` sinon, sans reste."""
    meta = LANGUES_OPTIONNELLES.get(lang)
    if meta is None:
        raise Stop('langue_inconnue')
    if not _windows():
        raise Stop('linux_apt')
    if not meta.get('sha256'):
        raise Stop('empreinte_a_completer')
    base = tessdata_integre()
    if base is None:
        raise Stop('tesseract_integre_absent')
    cible = tessdata_cabinet()
    if cible is None:
        raise Stop('stockage_indisponible')
    try:
        cible.mkdir(parents=True, exist_ok=True)
        _copier_base(base, cible)
    except OSError:
        _nettoyer(cible)
        raise Stop('stockage_indisponible') from None
    final = cible / (lang + '.traineddata')
    tmp = cible / ('.%s.%s.part' % (lang, secrets.token_hex(6)))
    h256, hgit, recu = hashlib.sha256(), hashlib.sha1(b'blob %d\0' % meta['octets']), 0
    try:
        try:
            reponse = (ouvrir or _ouvrir)(URL % (COMMIT, lang), DELAI)
            with reponse, open(tmp, 'wb') as f:
                while True:
                    bloc = reponse.read(1 << 20)
                    if not bloc:
                        break
                    recu += len(bloc)
                    if recu > meta['octets']:
                        raise Stop('empreinte_incorrecte')
                    h256.update(bloc)
                    hgit.update(bloc)
                    f.write(bloc)
                f.flush()
                os.fsync(f.fileno())
        except Stop:
            raise
        except Exception:   # noqa: BLE001 — réseau, serveur, disque : rien n'est gardé
            raise Stop('telechargement_impossible') from None
        if recu != meta['octets'] or hgit.hexdigest() != meta['git'] or h256.hexdigest() != meta['sha256']:
            raise Stop('empreinte_incorrecte')
        os.replace(tmp, final)
    finally:
        if tmp.exists():
            tmp.unlink()
        if not final.is_file():
            _nettoyer(cible)
    _oublier()
    return {'octets': recu, 'sha256': h256.hexdigest()}


def retirer(lang):
    if lang not in LANGUES_OPTIONNELLES:
        raise Stop('langue_inconnue')
    cible = tessdata_cabinet()
    f = cible / (lang + '.traineddata') if cible is not None else None
    if f is None or not f.is_file():
        raise Stop('langue_non_installee')
    f.unlink()
    _nettoyer(cible)
    _oublier()


# ================================================================================================ routes /api440/m56106/ocr/*
def allowed(role, method, api):
    """Toutes les écritures sont réservées à l'administrateur ; aucune lecture par route (la section est rendue côté serveur)."""
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
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
        return _json({'error': code, 'message': MESSAGES.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


def _recu(desk, owner, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    r = desk.db.execute('SELECT empreinte,reponse FROM ocr56106_recus WHERE compte=? AND route=? AND cle=?', (owner, route, cle)).fetchone()
    if r and r[0] != empreinte:
        raise Stop('cle_reutilisee')
    return json.loads(r[1]) if r else None


def _garder(desk, owner, route, cle, empreinte, out):
    desk.db.execute('INSERT OR IGNORE INTO ocr56106_recus VALUES(?,?,?,?,?,?)', (owner, route, cle, empreinte, json.dumps(out, ensure_ascii=False), _now()))
    desk.db.commit()


def _enregistrer(desk, langues_, version):
    desk.setting(KEY, {'langues': langues_, 'version': version})
    _oublier()


def ecrire(desk, owner, action, d):
    """Ajouter, retirer une langue, ou choisir les langues utilisées (administrateur ; clé d'idempotence ; 409 si version périmée)."""
    cle = str(d.get('cle') or '')
    empreinte = hashlib.sha256(json.dumps([action, {k: v for k, v in d.items() if k != 'csrf'}], sort_keys=True, default=str).encode()).hexdigest()
    ancien = _recu(desk, owner, 'ocr/' + action, cle, empreinte)   # même clé, même demande : la réponse gardée, rien n'est rejoué
    if ancien is not None:
        return ancien
    r = reglages_de(desk.c['state_dir'])
    if str(d.get('version')) != str(r['version']):
        raise Stop('version_perimee')
    if not _VERROU.acquire(blocking=False):
        raise Stop('operation_en_cours')
    try:
        _assurer(desk.c['state_dir'])
        if action == 'langues':
            voulues = [l for l in ('fra', 'eng', 'spa', 'deu') if d.get('l:' + l) in (True, 'oui', 'on', '1')]
            if not voulues:
                raise Stop('aucune_langue')
            presentes = langues_installees()
            if presentes is not None and any(l not in presentes for l in voulues):
                raise Stop('langue_non_installee')
            _enregistrer(desk, '+'.join(voulues), r['version'] + 1)
            desk.audit('ocr56106_langues', {'langues': '+'.join(voulues), 'version': r['version'] + 1})
            out = {'message': 'Langues de lecture des scans enregistrées : %s.' % ', '.join(NOMS[l] for l in voulues), 'version': r['version'] + 1}
        elif action == 'ajouter':
            lang = str(d.get('langue') or '')
            try:
                bilan = telecharger(lang)
            except Stop as ex:
                desk.audit('ocr56106_langue_refusee', {'langue': lang[:8], 'motif': str(ex).split(':')[0]})
                raise
            _enregistrer(desk, r['langues'], r['version'] + 1)
            desk.audit('ocr56106_langue_ajoutee', {'langue': lang, 'octets': bilan['octets'], 'sha256': bilan['sha256'], 'commit': COMMIT})
            out = {'message': 'Langue ajoutée : %s (empreinte vérifiée). Cochez-la dans « Langues utilisées » pour la lire.' % NOMS[lang],
                   'version': r['version'] + 1}
        else:
            lang = str(d.get('langue') or '')
            retirer(lang)
            garde = '+'.join(l for l in r['langues'].split('+') if l != lang) or DEFAUT
            _enregistrer(desk, garde, r['version'] + 1)
            desk.audit('ocr56106_langue_retiree', {'langue': lang})
            out = {'message': 'Langue retirée : %s.' % NOMS[lang], 'version': r['version'] + 1}
    finally:
        _VERROU.release()
    _garder(desk, owner, 'ocr/' + action, cle, empreinte, out)
    return out


# ================================================================================================ présentation
def _libelle(nom):
    o = origine(nom)
    return {'intégré': 'intégré à l’application', 'poste': 'installé sur le poste', 'absent': 'absent'}[o]


def section_html(desk, prefix, role):
    """Paramètres › IA › « Lecture des scans (OCR) »."""
    _assurer(desk.c['state_dir'])
    r = reglages_de(desk.c['state_dir'])
    windows = _windows()
    presentes = langues_installees()
    ajoutees = _ajoutees(tessdata_cabinet()) if windows else []
    admin = role == 'administrateur'
    if presentes is None:
        etat_l = '<p class="ax-alert" role="status">Langues installées inconnues (Tesseract absent ou en panne) : la lecture des scans est indisponible.</p>'
    else:
        etat_l = '<p>Langues installées : %s.</p>' % e(', '.join(NOMS.get(l, l) for l in presentes) or 'aucune')
    choix = ''
    if admin:
        cases = ''.join('<label class="d51-check"><input type="checkbox" name="l:%s" value="oui"%s%s> %s</label> ' % (
            l, ' checked' if l in r['langues'].split('+') else '', '' if presentes is None or l in presentes else ' disabled', e(NOMS[l]))
            for l in ('fra', 'eng', 'spa', 'deu'))
        choix = ('<form class="m5-form" data-api="m56106/ocr/langues" data-reload><fieldset><legend>Langues utilisées par la lecture des scans</legend>%s'
                 '</fieldset><input type="hidden" name="version" value="%d"><input type="hidden" name="cle" value="%s">'
                 '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>') % (cases, r['version'], secrets.token_hex(12))
    else:
        choix = '<p>Langues utilisées : %s. Seul l’administrateur modifie ce réglage.</p>' % e(', '.join(NOMS.get(l, l) for l in r['langues'].split('+')))
    options = []
    for lang, meta in LANGUES_OPTIONNELLES.items():
        if not windows:
            options.append('<li>%s : <code>sudo apt install %s</code> (paquet du système ; rien n’est téléchargé par AxiorHub)</li>' % (e(meta['nom'].capitalize()), e(meta['apt'])))
            continue
        if lang in ajoutees:
            bouton = ('<form class="m5-form d51-inline" data-api="m56106/ocr/retirer" data-reload><input type="hidden" name="langue" value="%s">'
                      '<input type="hidden" name="version" value="%d"><input type="hidden" name="cle" value="%s"><button class="ax-btn ghost" type="submit">'
                      'Retirer l’%s</button><p role="status" class="m5-status"></p></form>') % (lang, r['version'], secrets.token_hex(12), e(meta['nom'])) if admin else ''
            options.append('<li>%s : ajouté. %s</li>' % (e(meta['nom'].capitalize()), bouton))
            continue
        if not meta.get('sha256'):
            options.append('<li>%s (%s Mo) : <button class="ax-btn ghost" type="button" disabled>%s</button> <span class="ax-muted">empreinte SHA-256 '
                           'non renseignée dans cette version : téléchargement indisponible.</span></li>' % (
                               e(meta['nom'].capitalize()), ('%.1f' % (meta['octets'] / 1e6)).replace('.', ','), e(meta['bouton'])))
            continue
        bouton = ('<form class="m5-form d51-inline" data-api="m56106/ocr/ajouter" data-reload><input type="hidden" name="langue" value="%s">'
                  '<input type="hidden" name="version" value="%d"><input type="hidden" name="cle" value="%s"><button class="ax-btn" type="submit">%s (%s Mo)</button>'
                  '<p role="status" class="m5-status"></p></form>') % (lang, r['version'], secrets.token_hex(12), e(meta['bouton']),
                                                                       ('%.1f' % (meta['octets'] / 1e6)).replace('.', ',')) if admin else ''
        options.append('<li>%s : non installé. %s</li>' % (e(meta['nom'].capitalize()), bouton))
    source = ('Téléchargement à la demande depuis le dépôt public tesseract-ocr/tessdata (GitHub), version figée ; taille et empreintes vérifiées '
              'avant de garder le fichier ; opération notée au journal d’audit.') if windows else ''
    return ('<section class="ax-card" id="ocr56106"><h2>Lecture des scans (OCR)</h2>'
            '<p>Les scans et les PDF sans texte sont lus sur ce poste par Tesseract ; les pages des PDF sont rendues par Poppler. Rien n’est envoyé '
            'à l’extérieur pour cette lecture.</p><p class="ax-muted">Tesseract : %s · Poppler : %s.</p>%s%s'
            '<h3>Langues facultatives</h3><ul class="d79-list">%s</ul><p class="ax-muted">%s</p></section>') % (
        e(_libelle('tesseract')), e(_libelle('pdftoppm')), etat_l, choix, ''.join(options), e(source))

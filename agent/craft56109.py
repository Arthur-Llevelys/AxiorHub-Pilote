"""Éditeurs Craft embarqués : GridCraft (tableur, .xlsx), PdfCraft (PDF) et DeckCraft (présentations, .pptx), versions d'essai.

5.6.109 (lot V, F16) : pont hôte, séances, sauvegardes et exports protégés — écrits alors sans les vraies applications.
5.6.110 (lot E) : les trois applications sont EMBARQUÉES et PROPOSÉES PAR DÉFAUT, comme WordCraft (``wordcraft56108``).

- Fichiers : archives web officielles (GridCraft 0.19.1, PdfCraft 0.21.1, DeckCraft 0.18.1 ; MIT ou Apache-2.0 au choix ; « alpha » selon
  leurs auteurs) copiées INCHANGÉES sous ``agent/static/<app>-<version>/`` ; l'empreinte SHA-256 et la taille de CHAQUE fichier du dossier
  sont déclarées ci-dessous (``RELEASES[app]['fichiers']``) et vérifiées par un essai. Seuls les fichiers marqués « servi » sont envoyés au
  navigateur, par ``/editeur/craft/<app>-<version>/<fichier>`` (rôles d'édition, éditeur activé, empreinte vérifiée) ; licences, NOTICE,
  ATTRIBUTION, HOSTING, README, ``index.html`` (script en ligne) et les règles d'hébergement accompagnent le paquet sans être servis.
- Ce que les applications font réellement depuis une page hôte (mesuré dans Edge, ``scripts/parcours_p110e.py``) : cadre de même origine
  ouvert avec ``?host=parent`` ; l'application annonce ``<app>:ready``, la page lui envoie le document (``<app>:open``, nom et octets) ;
  chaque Enregistrer ou Exporter de l'application revient à la page (``<app>:save``, nom et octets) au lieu d'un téléchargement ;
  ``<app>:dirty`` signale les modifications. Contrat commun ``craft-web-host`` décrit dans ``HOSTING.md`` de chaque archive.
- Aucun accès réseau : la colle JavaScript ne charge que le WebAssembly (et, pour PdfCraft, le travailleur et les deux modèles de
  reconnaissance de texte, servis ici) ; CSP du cadre ``connect-src 'self'``, aucune source externe ; ``window.open`` neutralisé.
- Séances : la table et les services de ``wordcraft56108`` (copie privée, candidat, contrôle, décision « Conserver » avec rapprochement des
  écritures incertaines, jamais d'écrasement : la version gardée est un fichier voisin). Ce module apporte les capacités de format (analyse
  et contrôle XLSX, PPTX ; PDF : ceux de ``wordcraft56108``).
- Exports : un export (PDF d'un classeur, image d'une page…) est un NOUVEAU fichier voisin, jamais l'enregistrement du document ; contenu
  contrôlé (signature du format), création exclusive, relecture ; cible figée par clé : une réponse perdue se relit sur la même cible.
- Commandes de l'IA (lot V) : DÉSACTIVÉES. Les commandes et paramètres du plan fermé ne correspondent pas aux commandes réelles des
  applications (par exemple ``cell.set`` attend ``input`` et non ``value`` ; PdfCraft ``page.rotate`` ne prend aucun paramètre ; DeckCraft
  n'a pas ``slide.add``). La validation reste écrite (``valider_plan``) mais la route refuse et l'écran ne les propose pas.
"""
from datetime import datetime, timezone
import hashlib
from html import escape as e
import io
import json
from pathlib import Path, PurePosixPath
import re
import secrets
import zipfile
import xml.etree.ElementTree as ET

from .common import Stop

JS, WASM, BIN, ICO = 'text/javascript', 'application/wasm', 'application/octet-stream', 'image/x-icon'
SNIPPET = 'snippets/eframe-d31a8450ea12dfc1/inline0.js'
_SNIPPET = (JS, '22a98f9d0c5a0532d291bdc9a3050439420b3d11143b4568fcfd3c66fff36995', 280)
_HTACCESS = ('', 'c92d61b428bff284fce08dc57f0255261476397f2cba5836c833974eded5c725', 495)
_HEADERS = ('', 'e3e253de74fb99a761fc91d4dec4b81ad6a1022b29c98761b5ac1e8ba5aff05a', 326)
_APACHE = ('', '0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594', 10174)
# Fichiers de chaque dossier embarqué : chemin → (type servi, ou '' pour un fichier d'accompagnement jamais servi ; SHA-256 ; octets).
RELEASES = {
    'gridcraft': {
        'libelle': 'GridCraft', 'role': 'tableur', 'version': '0.19.1', 'documents': ('xlsx',), 'exports': ('pdf', 'csv', 'tsv'),
        'archive': 'gridcraft-web-0.19.1.zip', 'taille': 9679862, 'sha256': '48c5d1b101befebb3c5ba3b7a14914f01c3207934813ed07dc4251b7ae116a78',
        'glue': 'gridcraft-web-fdc723373f2aa0d8.js', 'wasm': 'gridcraft-web-fdc723373f2aa0d8_bg.wasm', 'toile': 'gridcraft_canvas',
        'options': '&chrome=notitlebar', 'auteur': True,
        'fichiers': {
            'gridcraft-web-fdc723373f2aa0d8.js': (JS, '559114c416bb5e050b48d3615dc50be421c2ea04598ac60dd1a9c1e16027b9fb', 174295),
            'gridcraft-web-fdc723373f2aa0d8_bg.wasm': (WASM, 'e2c0add31d771b508ce1a854cd17e73384a0f68f7b5cccbb6a5021450b0b1e7c', 26298748),
            SNIPPET: _SNIPPET,
            'gridcraft-45f84b7721cb79a9.ico': ('', 'f5693fcf3f71695d5ab332d24377c80e0315f952c24ae1eb3b4203bad9ea7f07', 26895),
            'index.html': ('', '5ccba44838b19529fbf5d82b7ad7b33ab2cb523512dc743bb97ebdceae5eda0f', 1773),
            '.htaccess': _HTACCESS, '_headers': _HEADERS, 'LICENSE-APACHE': _APACHE,
            'LICENSE-MIT': ('', 'a87109bbb1ad43ca3920a5d7ff3806863b7e8470a73f4ded97a1447ac93333f5', 1101),
            'NOTICE': ('', 'c23f6974c1a3da29fff3af7ae6658849f9432624286f8462d02e9fff98755d54', 20333),
            'ATTRIBUTION.md': ('', '8a8b933beb483bbffd103d589c896ddf4c7a851a8719ce3c47de159a88aed697', 12488),
            'HOSTING.md': ('', '9f27bad0660663baf938656bd073948fe3c5101940c90d9fbc6818e5cad0319e', 10706),
            'README.md': ('', '48d0f080921f9b38970423db37dce5c0838da88826034fdf4a1b9da4144503ab', 25914),
        }},
    'pdfcraft': {
        'libelle': 'PdfCraft', 'role': 'outils PDF', 'version': '0.21.1', 'documents': ('pdf',), 'exports': ('png', 'jpg', 'jpeg', 'txt'),
        'archive': 'pdfcraft-web-0.21.1.zip', 'taille': 34367297, 'sha256': 'fe9708d46af191aebb56f70eb32cf8b112b0b9452d17ae6ba3a8c4a8665fe748',
        'glue': 'pdfcraft-web-dbf16682ba914f2f.js', 'wasm': 'pdfcraft-web-dbf16682ba914f2f_bg.wasm', 'toile': 'pdfcraft',
        'options': '', 'auteur': True, 'travailleur': 'pdfcraft-ocr-worker-b363fe94bbdd0923_loader.js',
        'fichiers': {
            'pdfcraft-web-dbf16682ba914f2f.js': (JS, '84be0ee5db2429760c8685cd119dc79b71f0dc8fa6005cf33b60adefeecd0cd0', 182585),
            'pdfcraft-web-dbf16682ba914f2f_bg.wasm': (WASM, '6b8304c45b7e27a6f9959e08c0346cc02e99d01c2f678837c2e592606aa82ef4', 60639389),
            SNIPPET: _SNIPPET,
            'pdfcraft-ocr-worker-b363fe94bbdd0923_loader.js': (JS, '6b368d6cf8e7e36691e9b4c6fa65f673c5330e6851a46b662d44240802073007', 74),
            'pdfcraft-ocr-worker-b363fe94bbdd0923.js': (JS, 'cfe89544c8d4acd5bf778be854202dc1e4b5b934702081e1023b0733fa0e542e', 13118),
            'pdfcraft-ocr-worker-b363fe94bbdd0923_bg.wasm': (WASM, '857f77c4ed7e66236c5b37f7196029cf17eca9e575513cb315a611331a324fb6', 3383385),
            'models/text-detection.onnx': (BIN, 'a917b23dbd9524b465df7e922641b3ff2981623df4ded5a0234004ef2fee7cfe', 2499479),
            'models/text-recognition.onnx': (BIN, '86c145c2edb96c8caed5b1ebb8f44d706408922211451c309c625157dd6061c5', 9713177),
            'models/ATTRIBUTION.txt': ('', '0586352bd8163e4bdcaf1c3016844149df6f1f3b01b1ba963220ce8ecdf54ae1', 1134),
            'models/text-detection.onnx.LICENCE.txt': ('', '28a9529c7d0bb4dc51f4bf5c116a3d16ef247a052f7591466768ddf563fd1cf5', 20138),
            'models/text-recognition.onnx.LICENCE.txt': ('', '28a9529c7d0bb4dc51f4bf5c116a3d16ef247a052f7591466768ddf563fd1cf5', 20138),
            'index.html': ('', 'dd873541d3f2f5f8287d117db4056abb176e13554754b5d163a1f09aeb4f2337', 1595),
            '.htaccess': _HTACCESS, '_headers': _HEADERS, 'LICENSE-APACHE': _APACHE,
            'LICENSE-MIT': ('', 'cd7032ea33b71427f188e34ffb4aee9d6868a57fd196037c5c8b2be03a2a0352', 1100),
            'NOTICE': ('', 'a7620386409bc691c7e223917db186ec447362f655fd81a1196d9853dcac1330', 7922),
            'HOSTING.md': ('', 'fbf964a7600737ff1c7503feff64ee7d4df59801b24545d824d3167c210156cf', 12570),
            'README.md': ('', '32e335ac91b2b75d78ffe923b5b494520b1e3acf6d7c4b2061d9a6b06f570700', 44086),
        }},
    'deckcraft': {
        'libelle': 'DeckCraft', 'role': 'présentations', 'version': '0.18.1', 'documents': ('pptx',), 'exports': ('pdf', 'png', 'jpg', 'jpeg', 'txt', 'md'),
        'archive': 'deckcraft-web-0.18.1.zip', 'taille': 11910503, 'sha256': '3196e537fcfab538aaa2e88584bc74e29bb584459b916ab584ac22fbafe32db5',
        'glue': 'deckcraft-web-f863e8c546b80fd1.js', 'wasm': 'deckcraft-web-f863e8c546b80fd1_bg.wasm', 'toile': 'deckcraft_canvas',
        'options': '', 'auteur': True,
        'fichiers': {
            'deckcraft-web-f863e8c546b80fd1.js': (JS, '7122e2c9d0891fad8a9da7da5aba94e32ba1aa1721cde482ee8e9ba825b93be0', 175481),
            'deckcraft-web-f863e8c546b80fd1_bg.wasm': (WASM, '3f36d649160abb0c0547574c6b0787208db7b1a48137401e2d5933c95b4cb955', 33159566),
            SNIPPET: _SNIPPET,
            'deckcraft-b8fcbb36ecec0385.ico': ('', 'ceb73e008aa2cdf4d05d6375da32c5536a2bc2637bee59cdf441e2f9f792a9cd', 22252),
            'index.html': ('', 'd78eab3b6a57a5801394486dae5ee8ed800f4d3f15ef1db7b143ee8ddb41c798', 1794),
            '.htaccess': _HTACCESS, '_headers': _HEADERS, 'LICENSE-APACHE': _APACHE,
            'LICENSE-MIT': ('', '90e96fef569474cd526350e716e4b05dd32f179ca96d39d61e6b928485e35406', 1101),
            'NOTICE': ('', 'ffb327238bcbc6fe66f77d55bbdcadeb29ccc7319e9aa96b067e735dccb750eb', 6664),
            'HOSTING.md': ('', 'eb249b44737ec4767c9588ee975e468e1d4085abfdafa52bc66ed97dc918569f', 10330),
            'README.md': ('', '439cab25aa6809bb410ca7f377ed0081ea81c47234bd94f23951e951aa7a3aea', 22808),
        }},
}
REGLAGE = 'craft56109:reglages'
DEFAUT = {app: True for app in RELEASES}           # 5.6.110 (lot E) : proposés par défaut (versions d'essai), comme WordCraft
ROLES_EDITION = ('administrateur', 'avocat', 'assistant')
MAX_FICHIER = 20_000_000                            # octets d'un document ouvert, enregistré ou exporté (= ``wordcraft56108.MAX_DOCX`` ; le serveur web refuse au-delà de 20,1 Mo)
COMMANDES_IA = False                                # commandes de l'IA non raccordées aux commandes réelles des applications (voir l'en-tête)
# CSP du cadre : celle de WordCraft (``wordcraft56108.CADRE_CSP``) plus ``worker-src 'self'`` (travailleur de reconnaissance de texte de PdfCraft) ;
# mesurée dans Edge (``scripts/parcours_p110e.py``) : aucune violation, aucune requête externe.
CADRE_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; img-src 'self'; style-src 'self'; font-src 'self' data:; "
             "worker-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
SCHEMA = '''
CREATE TABLE IF NOT EXISTS craft56109_installations(app TEXT NOT NULL, version TEXT NOT NULL, archive_sha256 TEXT NOT NULL, manifeste_sha256 TEXT NOT NULL,
  fichiers INTEGER NOT NULL, octets INTEGER NOT NULL, par TEXT NOT NULL, le TEXT NOT NULL, PRIMARY KEY(app, version));
CREATE TABLE IF NOT EXISTS craft56109_exports(acteur TEXT NOT NULL, cle TEXT NOT NULL, seance TEXT NOT NULL, cible TEXT NOT NULL, sha256 TEXT NOT NULL,
  format TEXT NOT NULL, etat TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS craft56109_plans(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, cle TEXT NOT NULL, seance TEXT NOT NULL, app TEXT NOT NULL,
  revision INTEGER NOT NULL, commandes TEXT NOT NULL, empreinte TEXT NOT NULL, cree_le TEXT NOT NULL, expire_le TEXT NOT NULL, UNIQUE(acteur, cle));
'''
# ``craft56109_installations`` (5.6.109 : archives déposées puis installées) n'est plus écrite depuis 5.6.110 ; gardée pour les bases existantes et l'export.
NOMS_SCHEMA = ('craft56109_installations', 'craft56109_exports', 'craft56109_plans')


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(n not in existants for n in NOMS_SCHEMA):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


# ------------------------------------------------------------------------------------------------ fichiers embarqués
def dossier_statique(app):
    r = RELEASES[app]
    return Path(__file__).resolve().parent / 'static' / ('%s-%s' % (app, r['version']))


def servis(app):
    """Fichiers envoyés au navigateur (chemin relatif → type)."""
    return {k: v[0] for k, v in RELEASES[app]['fichiers'].items() if v[0]}


_EMPREINTES = {}


def _sha_fichier(chemin):
    """Empreinte d'un fichier, mise en cache par taille et date (une page ne relit pas 60 Mo à chaque demande)."""
    st = chemin.stat()
    cle = (str(chemin), st.st_size, st.st_mtime_ns)
    if cle not in _EMPREINTES:
        h = hashlib.sha256()
        with open(chemin, 'rb') as f:
            for bloc in iter(lambda: f.read(1 << 20), b''):
                h.update(bloc)
        _EMPREINTES[cle] = h.hexdigest()
    return _EMPREINTES[cle]


def disponible(app):
    """Fichiers servis présents, à leur taille (contrôle rapide, sans lecture) ; l'empreinte est vérifiée à chaque envoi."""
    if app not in RELEASES:
        return False
    base = dossier_statique(app)
    try:
        return all((base / k).is_file() and (base / k).stat().st_size == v[2] for k, v in RELEASES[app]['fichiers'].items() if v[0])
    except OSError:
        return False


def verifier_fichiers(app):
    """Contrôle complet (essais, diagnostic) : liste des écarts entre le dossier embarqué et les empreintes déclarées (vide : conforme)."""
    base, attendus = dossier_statique(app), RELEASES[app]['fichiers']
    ecarts = []
    presents = sorted(p.relative_to(base).as_posix() for p in base.rglob('*') if p.is_file()) if base.is_dir() else []
    for nom in sorted(set(attendus) - set(presents)):
        ecarts.append('absent : ' + nom)
    for nom in sorted(set(presents) - set(attendus)):
        ecarts.append('en trop : ' + nom)
    for nom in sorted(set(presents) & set(attendus)):
        f = base / nom
        if f.stat().st_size != attendus[nom][2] or _sha_fichier(f) != attendus[nom][1]:
            ecarts.append('modifié : ' + nom)
    return ecarts


def detecter(desk=None):
    """État de chaque éditeur (lecture seule, aucun réseau) : ``embarque`` (fichiers présents) ou ``absent`` (fichiers manquants)."""
    out = {}
    for app, r in RELEASES.items():
        ok = disponible(app)
        out[app] = {'app': app, 'libelle': r['libelle'], 'role': r['role'], 'version': r['version'], 'etat': 'embarque' if ok else 'absent',
                    'message': ('Fourni avec AxiorHub (version %s).' % r['version']) if ok else
                               'Fichiers de l’éditeur absents ou incomplets sur cette installation : éditeur indisponible. Réinstallez AxiorHub.'}
    return out


def reglages(desk):
    try:
        brut = desk.settings(REGLAGE, None)
    except Exception:   # noqa: BLE001 — base illisible : rien n'est proposé (jamais un accès élargi sur une panne)
        return {app: False for app in RELEASES}
    if not isinstance(brut, dict):
        return dict(DEFAUT)
    return {app: bool(brut.get(app, DEFAUT[app])) for app in RELEASES}


def actif(desk, app):
    """Proposé si le réglage le permet (oui par défaut) ET si les fichiers de l'éditeur sont présents."""
    return app in RELEASES and reglages(desk).get(app, False) and disponible(app)


def regler(desk, acteur, role, app, active):
    if role != 'administrateur':
        raise Stop('role_interdit')
    if app not in RELEASES:
        raise Stop('introuvable')
    if active and not disponible(app):
        raise Stop('ressources_craft_absentes')
    valeurs = reglages(desk)
    valeurs[app] = bool(active)
    desk.setting(REGLAGE, valeurs)
    desk.audit('craft56109_regle', {'app': app, 'active': bool(active), 'par': str(acteur)[:160]})
    return reglages(desk)


def ressource(desk, app_version, chemin):
    """(chemin du fichier, type) d'un fichier servi, à son empreinte déclarée ; tout autre chemin, éditeur désactivé : introuvable."""
    m = re.fullmatch(r'(gridcraft|pdfcraft|deckcraft)-(\d+\.\d+\.\d+)', str(app_version or ''))
    if not m or m.group(2) != RELEASES[m.group(1)]['version']:
        raise Stop('introuvable')
    app = m.group(1)
    entree = RELEASES[app]['fichiers'].get(str(chemin or ''))
    if not entree or not entree[0] or not actif(desk, app):
        raise Stop('introuvable')
    fichier = dossier_statique(app).joinpath(*PurePosixPath(chemin).parts)
    if not fichier.is_file() or fichier.stat().st_size != entree[2] or _sha_fichier(fichier) != entree[1]:
        raise Stop('introuvable')                     # fichier modifié ou abîmé : jamais servi
    return fichier, entree[0]


def points_entree(desk, app):
    r = RELEASES[app]
    return r['glue'], r['wasm']


# ------------------------------------------------------------------------------------------------ capacités de format (XLSX, PPTX)
def _paquet(raw, formats):
    """Archive OOXML lue avec bornes (entrées, volume, chemins) ; macros et objets actifs refusés ; DOCTYPE refusé."""
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_FICHIER:
        raise Stop('document_illisible')
    try:
        z = zipfile.ZipFile(io.BytesIO(bytes(raw)))
        infos = z.infolist()
        if len(infos) > 5000 or sum(i.file_size for i in infos) > 200_000_000:
            raise Stop('document_illisible')
        noms = [i.filename for i in infos]
        if any(n.startswith('/') or '..' in n.split('/') or '\\' in n for n in noms):
            raise Stop('document_illisible')
        if any('vbaproject' in n.lower() or 'activex' in n.lower() for n in noms):
            raise Stop('contenu_actif_refuse')
        return z, noms
    except zipfile.BadZipFile:
        raise Stop('document_illisible') from None


def _xml(z, nom):
    data = z.read(nom)
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise Stop('document_illisible')
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise Stop('document_illisible') from None


def _textes(racine, tag):
    return ' '.join((t.text or '').strip() for t in racine.iter() if t.tag.endswith('}' + tag) and (t.text or '').strip())


def analyser_xlsx(raw):
    z, noms = _paquet(raw, ('xlsx',))
    if 'xl/workbook.xml' not in noms:
        raise Stop('document_illisible')
    partages = []
    if 'xl/sharedStrings.xml' in noms:
        partages = [_textes(si, 't') for si in _xml(z, 'xl/sharedStrings.xml') if si.tag.endswith('}si')]
    feuilles = sorted((n for n in noms if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)), key=lambda n: int(re.search(r'(\d+)', n.rsplit('/', 1)[1]).group(1)))
    textes, formules = [], 0
    for f in feuilles:
        r = _xml(z, f)
        valeurs = []
        for c in r.iter():
            if not c.tag.endswith('}c'):
                continue
            v = next((x for x in c if x.tag.endswith('}v')), None)
            if any(x.tag.endswith('}f') for x in c):
                formules += 1
            if c.get('t') == 's' and v is not None and (v.text or '').isdigit() and int(v.text) < len(partages):
                valeurs.append(partages[int(v.text)])
            elif c.get('t') == 'inlineStr':
                valeurs.append(_textes(c, 't'))
            elif v is not None:
                valeurs.append(v.text or '')
        textes.append(' | '.join(valeurs))
    return {'format': 'xlsx', 'feuilles': len(feuilles), 'formules': formules, 'textes': textes, 'liens_externes': sum(1 for n in noms if n.startswith('xl/externalLinks/')),
            'paragraphes': len(feuilles)}


def analyser_pptx(raw):
    z, noms = _paquet(raw, ('pptx',))
    if 'ppt/presentation.xml' not in noms:
        raise Stop('document_illisible')
    diapos = sorted((n for n in noms if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)), key=lambda n: int(re.search(r'(\d+)', n.rsplit('/', 1)[1]).group(1)))
    textes, masquees = [], 0
    for d in diapos:
        r = _xml(z, d)
        if r.get('show') == '0':
            masquees += 1
        textes.append(_textes(r, 't'))
    return {'format': 'pptx', 'diapositives': len(diapos), 'textes': textes, 'notes': sum(1 for n in noms if re.fullmatch(r'ppt/notesSlides/notesSlide\d+\.xml', n)),
            'commentaires': sum(1 for n in noms if n.startswith('ppt/comments/')), 'masquees': masquees, 'paragraphes': len(diapos)}


def _norm(t):
    return re.sub(r'\s+', ' ', str(t or '')).strip()


def _controle_unites(avant, apres, unite, compteurs):
    pertes = []
    for cle, libelle in compteurs:
        if apres.get(cle, 0) < avant.get(cle, 0):
            pertes.append({'code': cle, 'libelle': libelle, 'avant': avant.get(cle, 0), 'apres': apres.get(cle, 0)})
    na, nb = [_norm(x) for x in avant['textes']], [_norm(x) for x in apres['textes']]
    modifies = sum(1 for a, b in zip(na, nb) if a != b)
    ajoutes, supprimes = max(0, len(nb) - len(na)), max(0, len(na) - len(nb))
    details = [{'nature': 'modifié', 'position': i + 1, 'avant': a[:200], 'apres': b[:200]} for i, (a, b) in enumerate(zip(na, nb)) if a != b][:40]
    identique = not pertes and not modifies and not ajoutes
    if pertes:
        resume = '%d point%s à vérifier avant de garder cette version.' % (len(pertes), 's' if len(pertes) > 1 else '')
    elif identique:
        resume = 'Aucune perte relevée : même structure et même texte que l’original (mise en forme et objets non comparés).'
    else:
        resume = 'Aucune perte de structure ; texte modifié sur %d %s(s) (mise en forme et objets non comparés).' % (modifies + ajoutes, unite)
    return {'pertes': pertes, 'texte': {'modifies': modifies, 'ajoutes': ajoutes, 'supprimes': supprimes, 'espaces': 0, 'details': details, 'unite': unite},
            'resume': resume, 'identique': identique}


def controler_xlsx(avant, apres):
    return _controle_unites(avant, apres, 'feuille', (('feuilles', 'Feuilles'), ('formules', 'Formules')))


def controler_pptx(avant, apres):
    return _controle_unites(avant, apres, 'diapositive', (('diapositives', 'Diapositives'), ('notes', 'Notes'), ('commentaires', 'Commentaires')))


# Capacités déclarées (branchées par ``wordcraft56108._analyser_selon`` et ``_controler_selon``) ; le PDF garde les fonctions existantes.
CAPACITES = {'.xlsx': {'analyser': analyser_xlsx, 'controler': controler_xlsx}, '.pptx': {'analyser': analyser_pptx, 'controler': controler_pptx}}


def capacite(chemin):
    return CAPACITES.get(PurePosixPath(str(chemin or '')).suffix.lower())


def application_pour(chemin):
    ext = PurePosixPath(str(chemin or '')).suffix.lower().lstrip('.')
    return next((app for app, r in RELEASES.items() if ext in r['documents']), '')


# ------------------------------------------------------------------------------------------------ séances (services de wordcraft56108)
def ouvrir(desk, auth, acteur, app, ident_source, chemin, sources_=None):
    """Séance d'édition Craft : éditeur actif, droits de la source (même 404 neutre), format de l'application, copie privée de l'original ;
    rend la séance (sans les octets)."""
    from . import wordcraft56108 as WC
    from .pause5629 import checkpoint
    if acteur[1] not in ROLES_EDITION:
        raise Stop('role_insuffisant')
    if not actif(desk, app):
        raise Stop('craft_desactive')
    source, chemin = WC._source(desk, auth, acteur, ident_source, chemin, sources_)
    if 'ecriture' not in source.capacites():
        raise Stop('non_pris_en_charge')
    props = source.proprietes(chemin)
    if props.get('dossier'):
        raise Stop('introuvable')
    ext = PurePosixPath(chemin).suffix.lower().lstrip('.')
    if ext not in RELEASES[app]['documents']:
        raise Stop('format_non_pris_en_charge')
    data = bytes(source.lire(chemin, WC.MAX_DOCX))
    WC._analyser_selon(chemin, data)                  # format réellement lisible avant toute séance
    checkpoint(desk)
    ident = secrets.token_hex(16)
    dossier = WC._prive(WC.dossier_seance(desk, ident))
    WC._ecrire_prive(dossier / 'original.docx', data)  # nom interne de la copie privée, quel que soit le format (convention des séances)
    sha = hashlib.sha256(data).hexdigest()
    quand = _maintenant()
    desk.db.execute('INSERT INTO editeur56108_seances(id,acteur,outil,source,chemin,etag,sha256,taille,garde,etat,cree_le,maj_le) '
                    "VALUES(?,?,?,?,?,?,?,?,?,'ouverte',?,?)", (ident, acteur[0], app, source.ident, chemin, str(props.get('etag') or ''), sha, len(data),
                                                               json.dumps({'niveau': 'prudence', 'constats': []}), quand, quand))
    desk.db.commit()
    desk.audit('craft56109_ouvert', {'par': acteur[0], 'app': app, 'source': source.ident, 'chemin': chemin, 'sha256': sha})
    return {'seance': ident, 'sha256': sha, 'taille': len(data), 'nom': PurePosixPath(chemin).name, 'revision': 0, 'app': app}


def _seance(desk, acteur, ident):
    from . import wordcraft56108 as WC
    s = WC.seance(desk, acteur, ident)
    if s['outil'] not in RELEASES:
        raise Stop('introuvable')
    return s


SIGNATURES = {'pdf': (b'%PDF-',), 'png': (b'\x89PNG\r\n\x1a\n',), 'jpg': (b'\xff\xd8\xff',), 'jpeg': (b'\xff\xd8\xff',)}
TEXTES = ('csv', 'tsv', 'txt', 'md')
LIBELLE_EXPORT = {app: 'export %s' % r['libelle'] for app, r in RELEASES.items()}


def _contenu_export(fmt, data):
    if not data or len(data) > MAX_FICHIER:
        raise Stop('export_refuse')
    if fmt in SIGNATURES:
        if not bytes(data[:16]).startswith(SIGNATURES[fmt]):
            raise Stop('export_refuse')               # l'extension ne suffit pas : le contenu doit être celui du format annoncé
    elif fmt in TEXTES:
        try:
            texte = bytes(data).decode('utf-8')
        except UnicodeDecodeError:
            raise Stop('export_refuse') from None
        if '\x00' in texte:
            raise Stop('export_refuse')
    else:
        raise Stop('export_refuse')


def exporter(desk, auth, acteur, ident, fmt, data, cle, sources_=None):
    """Export d'une séance : nouveau fichier voisin (jamais l'enregistrement du document), création exclusive, relecture comparée ; cible figée
    par clé AVANT l'effet (même clé : la même cible est relue, aucune nouvelle cible)."""
    from . import wordcraft56108 as WC
    from .fichiers56103 import horodatage
    from .pause5629 import checkpoint
    from .reprise56109 import arret_central, code_de
    ensure_schema(desk)
    s = _seance(desk, acteur, ident)
    fmt = str(fmt or '').lower()
    if fmt not in RELEASES[s['outil']]['exports']:
        raise Stop('format_export_refuse')
    _contenu_export(fmt, data)
    sha = hashlib.sha256(bytes(data)).hexdigest()
    compte = str(acteur[0])[:160]
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', str(cle or '')):
        raise Stop('cle_requise')
    row = desk.db.execute('SELECT * FROM craft56109_exports WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    source, chemin = WC._source(desk, auth, acteur, s['source'], s['chemin'], sources_)
    if row:
        if row['sha256'] != sha or row['seance'] != ident or row['format'] != fmt:
            raise Stop('cle_reutilisee')
        if row['etat'] == 'verifie':
            return {'etat': 'verifie', 'nom': PurePosixPath(row['cible']).name, 'rejoue': True, 'message': 'Export déjà enregistré : « %s ».' % PurePosixPath(row['cible']).name}
        cible = row['cible']
    else:
        p = PurePosixPath(chemin)
        cible = ''
        for n in range(1, 21):
            essai = str(p.with_name('%s (%s %s%s).%s' % (p.stem, LIBELLE_EXPORT[s['outil']], horodatage(desk), '' if n == 1 else ' %d' % n, fmt)))
            try:
                source.proprietes(essai)
            except Stop as ex:
                if str(ex) == 'introuvable':
                    cible = essai
                    break
                raise
        if not cible:
            raise Stop('deja_existant')
        quand = _maintenant()
        desk.db.execute("INSERT INTO craft56109_exports(acteur,cle,seance,cible,sha256,format,etat,cree_le,maj_le) VALUES(?,?,?,?,?,?,'engage',?,?)",
                        (compte, cle, ident, cible, sha, fmt, quand, quand))
        desk.db.commit()                               # cible durable AVANT l'effet
        checkpoint(desk)
        try:
            cible_source, cible_ok = WC._source(desk, auth, acteur, s['source'], cible, sources_)
            cible_source.ecrire(cible_ok, bytes(data), None)
        except Stop as ex:
            if arret_central(ex):
                raise
            if code_de(ex) in WC.REFUS_CERTAINS:
                desk.db.execute('DELETE FROM craft56109_exports WHERE acteur=? AND cle=?', (compte, cle))
                desk.db.commit()
                raise
        except Exception:   # noqa: BLE001 — transport en panne : résultat inconnu, relu ci-dessous
            pass
    try:
        cible_source, cible_ok = WC._source(desk, auth, acteur, s['source'], cible, sources_)
        relu = cible_source.lire(cible_ok, MAX_FICHIER)
        verdict = 'verifie' if hashlib.sha256(bytes(relu)).hexdigest() == sha else 'cible_differente'
    except Stop as ex:
        if arret_central(ex):
            raise
        verdict = 'cible_absente' if code_de(ex) == 'introuvable' else 'relecture_impossible'
    except Exception:   # noqa: BLE001
        verdict = 'relecture_impossible'
    nom = PurePosixPath(cible).name
    if verdict == 'verifie':
        desk.db.execute("UPDATE craft56109_exports SET etat='verifie', motif='', maj_le=? WHERE acteur=? AND cle=?", (_maintenant(), compte, cle))
        desk.db.commit()
        desk.audit('craft56109_export', {'par': compte, 'app': s['outil'], 'chemin': s['chemin'], 'export': cible, 'sha256': sha})
        return {'etat': 'verifie', 'nom': nom, 'rejoue': bool(row), 'message': 'Export enregistré à côté du document : « %s ». Le document n’est pas modifié.' % nom}
    desk.db.execute("UPDATE craft56109_exports SET etat='a_verifier', motif=?, maj_le=? WHERE acteur=? AND cle=?", (verdict, _maintenant(), compte, cle))
    desk.db.commit()
    return {'etat': 'a_verifier', 'nom': nom, 'motif': verdict,
            'message': 'À vérifier : l’export « %s » n’a pas pu être confirmé. Renvoyer la même demande relit ce même fichier ; rien d’autre n’est écrit.' % nom}


# ------------------------------------------------------------------------------------------------ commandes IA (plan fermé ; désactivées, voir l'en-tête)
CELLULE = re.compile(r'\$?[A-Z]{1,3}\$?[1-9]\d{0,6}\Z')
PLAGE = re.compile(r'\$?[A-Z]{1,3}\$?[1-9]\d{0,6}(?::\$?[A-Z]{1,3}\$?[1-9]\d{0,6})?\Z')
MAX_COMMANDES = 20


def _txt(v, limite):
    if not isinstance(v, str) or len(v) > limite or any(ord(c) < 32 and c not in '\t\n' for c in v):
        raise Stop('parametre_refuse')
    return v


def _entier(v, bas, haut):
    if type(v) is not int or not bas <= v <= haut:
        raise Stop('parametre_refuse')
    return v


def _feuille(v):
    return _entier(v, 1, 500) if isinstance(v, int) else _txt(v, 64)


def _cell_set(p):
    _feuille(p.get('sheet'))
    if not isinstance(p.get('cell'), str) or not CELLULE.fullmatch(p['cell']):
        raise Stop('parametre_refuse')
    valeur = _txt(p.get('value'), 2000)
    formule = p.get('formula') is True
    if valeur.startswith('=') and not formule:
        raise Stop('formule_non_demandee')            # un texte commençant par « = » n'est pas importé comme formule sans choix explicite
    if formule and re.search(r'\[|https?:|\\\\|WEBSERVICE|IMPORT|HYPERLINK', valeur, re.I):
        raise Stop('formule_externe_refusee')
    if set(p) - {'sheet', 'cell', 'value', 'formula'}:
        raise Stop('parametre_refuse')


def _plage(p, *autres):
    _feuille(p.get('sheet'))
    if not isinstance(p.get('range'), str) or not PLAGE.fullmatch(p['range']):
        raise Stop('parametre_refuse')
    if set(p) - {'sheet', 'range'} - set(autres):
        raise Stop('parametre_refuse')


def _format_set(p):
    _plage(p, 'style')
    if p.get('style') not in ('bold', 'italic', 'currency', 'date', 'percent'):
        raise Stop('parametre_refuse')


def _chart_add(p):
    _plage(p, 'kind')
    if p.get('kind') not in ('bar', 'line', 'pie'):
        raise Stop('parametre_refuse')


def _annotation_add(p):
    _entier(p.get('page'), 1, 5000)
    _txt(p.get('text'), 2000)
    if p.get('kind') not in ('highlight', 'comment') or set(p) - {'page', 'text', 'kind'}:
        raise Stop('parametre_refuse')


def _page_rotate(p):
    _entier(p.get('page'), 1, 5000)
    if p.get('angle') not in (90, 180, 270) or set(p) - {'page', 'angle'}:
        raise Stop('parametre_refuse')


def _bookmark_add(p):
    _entier(p.get('page'), 1, 5000)
    _txt(p.get('title'), 200)
    if set(p) - {'page', 'title'}:
        raise Stop('parametre_refuse')


def _slide_add(p):
    _entier(p.get('after'), 0, 500)
    if p.get('layout') not in ('title', 'title_content', 'blank') or set(p) - {'after', 'layout', 'title', 'body'}:
        raise Stop('parametre_refuse')
    _txt(p.get('title', ''), 200)
    _txt(p.get('body', ''), 4000)


def _text_set(p):
    _entier(p.get('slide'), 1, 500)
    if not isinstance(p.get('shape'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', p['shape']) or set(p) - {'slide', 'shape', 'text'}:
        raise Stop('parametre_refuse')
    _txt(p.get('text'), 4000)


def _deck_chart(p):
    _entier(p.get('slide'), 1, 500)
    if p.get('kind') not in ('bar', 'line', 'pie') or not isinstance(p.get('data'), list) or not 1 <= len(p['data']) <= 50 or set(p) - {'slide', 'kind', 'data'}:
        raise Stop('parametre_refuse')
    for x in p['data']:
        if not isinstance(x, list) or len(x) != 2 or not isinstance(x[1], (int, float)) or isinstance(x[1], bool):
            raise Stop('parametre_refuse')
        _txt(x[0], 80)


# Liste fermée par application : jamais de commande générique, de chemin, de script, d'impression, de signature, d'accès réseau ni de publication.
COMMANDES = {
    'gridcraft': {'cell.set': _cell_set, 'range.read': lambda p: _plage(p), 'format.set': _format_set, 'table.add': lambda p: _plage(p), 'chart.add': _chart_add},
    'pdfcraft': {'annotation.add': _annotation_add, 'page.rotate': _page_rotate, 'bookmark.add': _bookmark_add},
    'deckcraft': {'slide.add': _slide_add, 'text.set': _text_set, 'chart.add': _deck_chart},
}


def valider_plan(desk, acteur, ident, plan, revision, cle):
    """Plan de commandes proposé : validé par le serveur, qui fait autorité ; IA en pause : refus expliqué. Non raccordé aux applications
    (``COMMANDES_IA``) : la route refuse avant d'appeler cette fonction."""
    from .arret5643 import manual_paused
    ensure_schema(desk)
    s = _seance(desk, acteur, ident)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', str(cle or '')):
        raise Stop('cle_requise')
    if manual_paused(desk) or desk.settings('missions567:pause', False):
        raise Stop('ia_en_pause')
    if type(revision) is not int or revision != int(s['revision']):
        raise Stop('version_perimee')
    if not isinstance(plan, list) or not 1 <= len(plan) <= MAX_COMMANDES:
        raise Stop('plan_refuse')
    commandes = COMMANDES[s['outil']]
    propres = []
    for x in plan:
        if not isinstance(x, dict) or set(x) != {'commande', 'parametres'} or not isinstance(x['parametres'], dict):
            raise Stop('plan_refuse')
        if x['commande'] not in commandes:
            raise Stop('commande_non_autorisee')
        commandes[x['commande']](x['parametres'])
        propres.append({'commande': x['commande'], 'parametres': x['parametres']})
    empreinte = hashlib.sha256(json.dumps({'s': ident, 'r': revision, 'p': propres}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    compte = str(acteur[0])[:160]
    old = desk.db.execute('SELECT * FROM craft56109_plans WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if old:
        if old['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        return {'plan_id': old['id'], 'commandes': json.loads(old['commandes']), 'revision': old['revision'], 'expire_le': old['expire_le'], 'rejoue': True}
    from datetime import timedelta
    expire = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec='seconds')
    pid = secrets.token_hex(16)
    desk.db.execute('INSERT INTO craft56109_plans(id,acteur,cle,seance,app,revision,commandes,empreinte,cree_le,expire_le) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (pid, compte, cle, ident, s['outil'], revision, json.dumps(propres, ensure_ascii=False), empreinte, _maintenant(), expire))
    desk.db.commit()
    desk.audit('craft56109_plan', {'par': compte, 'app': s['outil'], 'seance': ident, 'commandes': len(propres)})
    return {'plan_id': pid, 'commandes': propres, 'revision': revision, 'expire_le': expire, 'rejoue': False}


# ------------------------------------------------------------------------------------------------ routes ``m56109/editeurs/``
PREFIXE = 'm56109/editeurs/'
STATUTS_HTTP = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict',
                'original_modifie': '409 Conflict', 'ecriture_a_verifier': '409 Conflict', 'deja_existant': '409 Conflict', 'ia_en_pause': '409 Conflict',
                'role_interdit': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'craft_desactive': '403 Forbidden', 'ressources_craft_absentes': '409 Conflict',
                'commandes_ia_indisponibles': '409 Conflict',
                'origine_ou_csrf_refuse': '403 Forbidden', 'controle_acces_indisponible': '503 Service Unavailable', 'stockage_injoignable': '503 Service Unavailable',
                'requete_trop_longue': '413 Payload Too Large', 'document_word_trop_volumineux': '413 Payload Too Large'}
MESSAGES = {
    'introuvable': 'Document ou séance introuvable, ou action non permise.', 'role_interdit': 'Votre rôle ne permet pas cette action.',
    'craft_desactive': 'Cet éditeur est désactivé sur cette installation (réglage de l’administrateur).',
    'ressources_craft_absentes': 'Les fichiers de cet éditeur sont absents ou incomplets sur cette installation : il ne peut pas être activé. Réinstallez AxiorHub.',
    'version_perimee': 'Une version plus récente existe (autre onglet ?) : rechargez la page. Rien n’a été écrit.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'format_export_refuse': 'Format d’export non admis pour cet éditeur.', 'export_refuse': 'Le fichier exporté n’a pas le contenu du format annoncé : rien n’a été écrit.',
    'document_illisible': 'Le fichier reçu n’est pas lisible dans son format : rien n’a été gardé.',
    'contenu_actif_refuse': 'Le fichier contient des macros ou des objets actifs : refusé, rien n’a été gardé.',
    'document_word_trop_volumineux': 'Document trop volumineux pour cet éditeur (20 Mo au plus).',
    'format_non_pris_en_charge': 'Ce format ne s’ouvre pas dans cet éditeur.',
    'ia_en_pause': 'IA en pause : les commandes de l’IA attendent la reprise. Vos enregistrements manuels restent possibles.',
    'commandes_ia_indisponibles': 'Les commandes de l’IA ne sont pas disponibles dans ces éditeurs pour le moment : rien n’a été appliqué.',
    'commande_non_autorisee': 'Commande non autorisée pour cet éditeur.', 'parametre_refuse': 'Paramètre de commande refusé.',
    'formule_non_demandee': 'Une valeur commençant par « = » n’est importée comme formule que sur choix explicite.',
    'formule_externe_refusee': 'Formule refusée : lien ou ressource externe.', 'plan_refuse': 'Plan de commandes refusé.',
}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    if method in ('GET', 'HEAD'):
        return True
    if method != 'POST':
        return False
    if reste == 'reglage':
        return role == 'administrateur'
    if reste == 'seances':
        return role in ROLES_EDITION
    if len(parts) == 3 and parts[0] == 'seances' and parts[2] in ('candidats', 'exports', 'actions-ia', 'decision', 'fermer'):
        return role in ROLES_EDITION
    return False


def _json(data, status='200 OK'):
    from .web440 import json_out
    out = json_out(data, status)
    out['headers'] = [('Cache-Control', 'private, no-store')]
    return out


def _corps(env, auth):
    import hmac
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        longueur = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError:
        raise Stop('requete_trop_longue') from None
    if longueur > MAX_FICHIER:
        raise Stop('document_word_trop_volumineux')
    if longueur <= 0 or env.get('CONTENT_TYPE', '').split(';')[0] != 'application/octet-stream':
        raise Stop('document_illisible')
    return env['wsgi.input'].read(longueur)


def api(env, desk, auth, prefix, name, args, method, sources_=None):
    from . import wordcraft56108 as WC
    from .web567 import actor
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    try:
        if acteur[1] not in ROLES_EDITION:
            raise Stop('role_interdit')
        if method in ('GET', 'HEAD'):
            if reste == 'etat':
                etats = detecter(desk)
                regl = reglages(desk)
                visibles = {k: {**v, 'active': regl[k] and v['etat'] == 'embarque'} for k, v in etats.items()
                            if acteur[1] == 'administrateur' or (regl[k] and v['etat'] == 'embarque')}
                return _json({'editeurs': visibles, 'note': 'Versions d’essai : chaque version enregistrée est contrôlée avant d’être gardée à côté de l’original.'})
            if len(parts) == 3 and parts[0] == 'seances' and parts[2] == 'contenu':
                s = _seance(desk, acteur, parts[1])
                data, nom = WC.octets_original(desk, auth, acteur, s['id'], sources_)
                from urllib.parse import quote
                return {'status': '200 OK', 'kind': 'application/octet-stream', 'body': data,
                        'headers': [('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff'),
                                    ('Content-Disposition', "attachment; filename*=UTF-8''" + quote(nom, safe=''))]}
            if len(parts) == 4 and parts[0] == 'seances' and parts[2] == 'operations':
                _seance(desk, acteur, parts[1])
                row = desk.db.execute('SELECT etat, resultat, rapprochement FROM editeur56108_ops WHERE acteur=? AND cle=?', (str(acteur[0]), parts[3])).fetchone()
                exp = desk.db.execute('SELECT etat, cible, motif FROM craft56109_exports WHERE acteur=? AND cle=? AND seance=?', (str(acteur[0])[:160], parts[3], parts[1])).fetchone() \
                    if desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='craft56109_exports'").fetchone() else None
                if not row and not exp:
                    raise Stop('introuvable')
                if exp:
                    return _json({'etat': exp['etat'], 'nom': PurePosixPath(exp['cible']).name, 'motif': exp['motif'], 'rejoue': False})
                return _json({'etat': row['etat'], 'rapprochement': row['rapprochement'], 'resultat': json.loads(row['resultat'] or '{}')})
            raise Stop('introuvable')
        if not allowed(acteur[1], method, name):
            raise Stop('role_interdit')
        if len(parts) == 3 and parts[2] in ('candidats', 'exports'):
            corps = _corps(env, auth)
            ident = parts[1]
            if parts[2] == 'exports':
                return _json(exporter(desk, auth, acteur, ident, args.get('format'), corps, args.get('cle'), sources_))
            _seance(desk, acteur, ident)
            instantane = str(args.get('instantane') or '')
            if instantane and not re.fullmatch(r'\d{1,9}', instantane):
                raise Stop('instantane_invalide')
            empreinte = hashlib.sha256(corps).hexdigest()
            out = WC.executer(desk, acteur, args.get('cle'), 'enregistrer', {'seance': ident, 'revision': str(args.get('revision')), 'sha256': empreinte,
                                                                              **({'instantane': instantane} if instantane else {})},
                              lambda: WC.proposer(desk, auth, acteur, ident, args.get('revision'), corps, sources_))
            return _json({**out, 'durable': True, **({'instantane': int(instantane)} if instantane else {})})
        from .web440 import check_post
        d = check_post(env, auth)
        cle = d.get('cle')
        if reste == 'reglage':
            return _json({'reglages': regler(desk, acteur[0], acteur[1], str(d.get('app') or ''), bool(d.get('active'))),
                          'message': 'Réglage enregistré.'})
        if reste == 'seances':
            app, s, p = str(d.get('app') or ''), str(d.get('s') or ''), str(d.get('p') or '')
            return _json(WC.executer(desk, acteur, cle, 'craft_ouvrir', {'app': app, 's': s, 'p': p}, lambda: ouvrir(desk, auth, acteur, app, s, p, sources_)))
        ident = parts[1]
        if parts[2] == 'actions-ia':
            _seance(desk, acteur, ident)
            if not COMMANDES_IA:
                raise Stop('commandes_ia_indisponibles')
            return _json(valider_plan(desk, acteur, ident, d.get('plan'), d.get('revision'), cle))
        if parts[2] == 'fermer':
            _seance(desk, acteur, ident)
            return _json(WC.executer(desk, acteur, cle, 'fermer', {'seance': ident}, lambda: WC.fermer(desk, acteur, ident)))
        _seance(desk, acteur, ident)
        decision, revision, malgre = str(d.get('decision') or ''), d.get('revision'), bool(d.get('malgre_changement'))
        reessayer = d.get('reessayer') is True
        out = WC.executer(desk, acteur, cle, 'decider', {'seance': ident, 'revision': str(revision), 'decision': decision, 'malgre': malgre, **({'reessayer': True} if reessayer else {})},
                          lambda: WC.decider(desk, auth, acteur, ident, revision, decision, malgre, sources_, cle=cle, reessayer=reessayer))
        return _json(out, '202 Accepted' if isinstance(out, dict) and out.get('etat') == 'a_verifier' else '200 OK')
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'chemin_est_un_dossier', 'fichier_nextcloud_introuvable', 'http_404'):
            code = 'introuvable'
        return _json({'error': code, 'message': MESSAGES.get(code) or WC.message(code)}, STATUTS_HTTP.get(code, WC.STATUTS.get(code, '422 Unprocessable Entity')))


# ------------------------------------------------------------------------------------------------ boutons des autres écrans
def _actif_sur(desk, app):
    try:
        return actif(desk, app)
    except Exception:   # noqa: BLE001 — réglage ou fichiers illisibles : rien de proposé
        return False


def libelle_bouton(app):
    return 'Modifier dans %s (version d’essai)' % RELEASES[app]['libelle']


def liens(desk, prefix, ident_source, chemin, role):
    """Adresse calculée par le serveur (vide sinon) pour l'aperçu d'un document : ``craft_url`` et ``craft_libelle``."""
    from urllib.parse import urlencode
    app = application_pour(chemin)
    if not app or role not in ROLES_EDITION or not _actif_sur(desk, app):
        return {}
    return {'craft_url': prefix + '/editeur/craft?' + urlencode({'app': app, 's': ident_source, 'p': chemin}), 'craft_libelle': libelle_bouton(app)}


def bouton_html(desk, prefix, ident_source, chemin, role, titre=''):
    """« Modifier dans GridCraft / PdfCraft / DeckCraft (version d'essai) » pour un .xlsx, un .pdf ou un .pptx, si l'éditeur est actif."""
    l = liens(desk, prefix, ident_source, chemin, role)
    if not l:
        return ''
    sr = ('<span class="sr-only"> : %s</span>' % e(titre)) if titre else ''
    return '<a class="ax-btn ghost" href="%s">%s%s</a>' % (e(l['craft_url'], quote=True), e(l['craft_libelle']), sr)


def config_fichiers(desk, prefix, role):
    """Page Fichiers : page d'édition et éditeur de chaque extension active (le serveur revérifie droits, format et réglage)."""
    if role not in ROLES_EDITION:
        return {}
    apps = {ext: app for app, r in RELEASES.items() if _actif_sur(desk, app) for ext in r['documents']}
    return {'url': prefix + '/editeur/craft', 'apps': apps} if apps else {}


def libelle_fichiers(config):
    """Libellé du bouton de la page Fichiers, selon les éditeurs actifs."""
    noms = [RELEASES[a]['libelle'] for a in RELEASES if a in set(config.get('apps', {}).values())]
    return 'Modifier dans %s (version d’essai)' % ', '.join(noms)


# ------------------------------------------------------------------------------------------------ réglages (Paramètres › Documents)
DESCRIPTIONS = {
    'gridcraft': 'tableur : ouvre et modifie un classeur Excel (.xlsx) ; formules recalculées ; export PDF ou CSV.',
    'pdfcraft': 'PDF : lire, annoter, tourner, réorganiser les pages d’un PDF ; export en images ou en texte.',
    'deckcraft': 'présentations : ouvre et modifie une présentation PowerPoint (.pptx) ; export PDF ou images.',
}


def section_reglages(desk, prefix=''):
    """Réglages de l'atelier (administrateur) : chaque éditeur, sa description et sa case (cochée par défaut)."""
    etats, regl = detecter(desk), reglages(desk)
    lignes = []
    for app, x in etats.items():
        embarque = x['etat'] == 'embarque'
        case = ('<label class="e5636-switch"><input type="checkbox" data-c109-reglage="activer" data-c109-app="%s"%s%s> Proposer « %s »</label>' % (
            app, ' checked' if regl[app] and embarque else '', '' if embarque else ' disabled', e(libelle_bouton(app))))
        lignes.append('<li><strong>%s %s</strong> — <span>%s</span> <span>%s</span> %s</li>' % (e(x['libelle']), e(x['version']), e(DESCRIPTIONS[app]), e(x['message']), case))
    return ('<section class="ax-card c109-reglages" id="ax-craft-56109"><h2>Éditeurs intégrés : tableur, PDF et présentations</h2>'
            '<p class="ax-muted">GridCraft, PdfCraft et DeckCraft sont des logiciels libres (MIT ou Apache-2.0) fournis avec AxiorHub et servis par lui ; '
            'aucune donnée ne sort. Ce sont des versions d’essai (« alpha » selon leurs auteurs) : chaque enregistrement est contrôlé (structure et texte '
            'comparés à l’original) puis, si vous le décidez, gardé comme nouvelle version à côté de l’original, qui n’est jamais remplacé. Un export '
            '(PDF, images) est un fichier distinct. Les boutons « Modifier dans… » apparaissent dans l’aperçu d’un document, la page Fichiers et Produire.</p>'
            '<ul>%s</ul><p class="ax-muted">Les commandes de l’IA ne sont pas proposées dans ces éditeurs.</p>'
            '<p class="c109-etat" role="status" aria-live="polite" data-c109-etat-reglage></p><div hidden data-c109-reglages="%s"></div></section>'
            '<script defer src="%s"></script>') % (''.join(lignes), e(prefix + '/api440/' + PREFIXE, quote=True), e(prefix + '/static/v56109-craft-host.js', quote=True))


# ------------------------------------------------------------------------------------------------ pages : éditeur, cadre, fichiers servis
def page_route(env, cfg, auth, prefix, path, args, shell, sources_=None):
    from .desk import Desk
    from .web567 import actor
    acteur = actor(env)
    desk = Desk(cfg)
    try:
        m = re.fullmatch(r'/editeur/craft/((?:gridcraft|pdfcraft|deckcraft)-\d+\.\d+\.\d+)/(.+)', path)
        if m:
            if acteur[1] not in ROLES_EDITION:
                return _introuvable_brut()
            if m.group(2) == 'cadre':
                return _cadre(desk, prefix, m.group(1))
            try:
                fichier, kind = ressource(desk, m.group(1), m.group(2))
            except (Stop, OSError):
                return _introuvable_brut()
            return _envoi(fichier, kind)
        return _page(desk, auth, prefix, acteur, args, shell, sources_)
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


def _envoi(fichier, kind):
    """Fichier servi par morceaux (jamais chargé entier en mémoire) ; noms à empreinte ou dossier versionné : gardé en cache par le navigateur."""
    taille = fichier.stat().st_size

    def morceaux():
        with open(fichier, 'rb') as f:
            for bloc in iter(lambda: f.read(1 << 20), b''):
                yield bloc
    return {'status': '200 OK', 'kind': kind, 'stream': morceaux(), 'length': taille, 'body': b'',
            'headers': [('Cache-Control', 'private, max-age=31536000, immutable'), ('X-Content-Type-Options', 'nosniff')]}


def _introuvable_brut():
    return {'status': '404 Not Found', 'kind': 'text/plain; charset=utf-8', 'body': 'Introuvable.', 'headers': [('Cache-Control', 'private, no-store')]}


def _cadre(desk, prefix, app_version):
    """Cadre de même origine (``?host=parent``) : la toile au nom attendu par l'application, son script de démarrage, rien d'autre."""
    app = app_version.split('-', 1)[0]
    if app not in RELEASES or app_version != '%s-%s' % (app, RELEASES[app]['version']) or not actif(desk, app):
        return _introuvable_brut()
    r = RELEASES[app]
    p = e(prefix, quote=True)
    meta = ('<meta name="pdfcraft-ocr-worker" content="./%s">' % e(r['travailleur'], quote=True)) if r.get('travailleur') else ''
    html = ('<!doctype html><html lang="fr" data-c109="cadre" data-c109-app="%s" data-c109-base="%s/editeur/craft/%s/" data-c109-glue="%s" data-c109-wasm="%s">'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>%s'
            '<link rel="stylesheet" href="%s/static/v56109-produire.css"><script defer src="%s/static/v56109-craft-host.js"></script></head>'
            '<body class="c109-cadre-corps"><canvas id="%s" class="c109-toile"></canvas><p class="c109-chargement" data-c109-chargement>Chargement de l’éditeur…</p>'
            '</body></html>') % (app, p, e(app_version, quote=True), e(r['glue'], quote=True), e(r['wasm'], quote=True), e(r['libelle']), meta, p, p, e(r['toile'], quote=True))
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': html, 'csp': CADRE_CSP, 'frame_self': True,
            'headers': [('Cache-Control', 'private, no-store')]}


AIDES = {
    'gridcraft': 'Enregistrez dans GridCraft (Fichier › Enregistrer ou Ctrl+S) : AxiorHub reçoit le classeur, le compare à l’original et vous montre '
                 'les pertes éventuelles avant toute écriture dans le dossier. « Exporter en PDF » crée un fichier PDF distinct à côté du classeur.',
    'pdfcraft': 'Enregistrez dans PdfCraft (Fichier › Enregistrer ou Ctrl+S) : AxiorHub reçoit le PDF, compare les pages et le texte à l’original et '
                'vous montre les pertes éventuelles avant toute écriture dans le dossier.',
    'deckcraft': 'Enregistrez dans DeckCraft (Fichier › Enregistrer ou Ctrl+S) : AxiorHub reçoit la présentation, la compare à l’original et vous '
                 'montre les pertes éventuelles avant toute écriture dans le dossier. Un export (PDF, images) crée un fichier distinct.',
}


def _auteur(compte):
    from urllib.parse import quote
    return quote(str(compte or 'AxiorHub')[:60], safe='')


def _page(desk, auth, prefix, acteur, args, shell, sources_=None):
    from . import wordcraft56108 as WC
    from .web440 import page_out
    app = str(args.get('app') or '')
    s, p = str(args.get('s') or ''), str(args.get('p') or '')
    titre = 'Modifier dans un éditeur intégré'
    head = '<script defer src="%s/static/v56109-craft-host.js"></script>' % e(prefix, quote=True)

    def sortie(corps, status='200 OK'):
        out = page_out(shell(titre, '<section class="c109" aria-labelledby="c109-titre">%s</section>' % corps, prefix, auth['csrf'], '/production', head=head))
        out['status'] = status
        out['headers'] = [('Cache-Control', 'private, no-store')]
        return out
    if app not in RELEASES or acteur[1] not in ROLES_EDITION:
        return sortie('<h1 id="c109-titre">%s</h1><p class="c109-alerte" role="status">%s</p>' % (e(titre), e(MESSAGES['introuvable'])), '404 Not Found')
    if not actif(desk, app):
        return sortie('<h1 id="c109-titre">%s</h1><p class="c109-alerte" role="status">%s</p><p>Le document reste ouvrable dans le lecteur, téléchargeable et '
                      'modifiable avec les autres éditeurs du cabinet.</p>' % (e(titre), e(MESSAGES['craft_desactive'] if disponible(app) else
                                                                                       MESSAGES['ressources_craft_absentes'])), '403 Forbidden')
    try:
        source, chemin = WC._source(desk, auth, acteur, s, p, sources_)
        props = source.proprietes(chemin)
        if props.get('dossier') or PurePosixPath(chemin).suffix.lower().lstrip('.') not in RELEASES[app]['documents']:
            raise Stop('introuvable')
    except Stop:
        return sortie('<h1 id="c109-titre">%s</h1><p class="c109-alerte" role="status">%s</p>' % (e(titre), e(MESSAGES['introuvable'])), '404 Not Found')
    r = RELEASES[app]
    nom = PurePosixPath(chemin).name
    config = {'api': prefix + '/api440/' + PREFIXE, 'app': app, 's': source.ident, 'p': chemin, 'nom': nom, 'formats': {'document': list(r['documents']), 'export': list(r['exports'])},
              'cadre': '%s/editeur/craft/%s-%s/cadre?host=parent%s&author=%s' % (prefix, app, r['version'], r['options'], _auteur(acteur[0]))}
    corps = ('<div class="c109-barre"><a class="ax-btn ghost" href="%s" data-c109="retour">Retour</a><h1 id="c109-titre">%s <span class="c109-badge">%s %s, version d’essai</span></h1>'
             '<p class="c109-stockage" role="status" aria-live="polite" data-c109-etat>Document non ouvert.</p>'
             '<button type="button" class="ax-btn" data-c109="ouvrir">Ouvrir dans %s</button><button type="button" class="ax-btn ghost" data-c109="fermer" hidden>Fermer l’éditeur</button></div>'
             '<div class="c109-zone" data-c109-zone hidden><iframe class="c109-iframe" title="%s : %s" data-c109-iframe></iframe></div>'
             '<section class="c109-controle" data-c109-controle hidden aria-labelledby="c109-t-controle"><h2 id="c109-t-controle" tabindex="-1">Comparer avec l’original</h2>'
             '<div data-c109-rapport></div><p class="c109-actions"><button type="button" class="ax-btn" data-c109="garder">Conserver cette version</button>'
             '<button type="button" class="ax-btn ghost" data-c109="abandonner">Abandonner cette version</button><button type="button" class="ax-btn ghost" data-c109="verifier" hidden>'
             'Vérifier</button></p></section>'
             '<p class="c109-aide"><span>%s</span> <span>%s %s est une version d’essai (« alpha » selon ses auteurs) : l’original n’est jamais remplacé.</span></p>'
             '<div hidden data-c109-config="%s"></div>') % (
        e(prefix + '/production', quote=True), e(nom), e(r['libelle']), e(r['version']), e(r['libelle']), e(r['libelle']), e(nom), e(AIDES[app]),
        e(r['libelle']), e(r['version']), e(json.dumps(config, ensure_ascii=False), quote=True))
    return sortie(corps)

"""5.6.109 (lot V, F16) : éditeurs Craft facultatifs (GridCraft, PdfCraft, DeckCraft) — pont hôte, séances, sauvegardes et exports protégés,
commandes IA validées. TOUT est désactivé par défaut.

Aucun binaire n'est livré ni téléchargé. Le module web d'une application n'est proposable que si ses ressources compilées sont PRÉSENTES sur
l'installation : l'archive officielle de release, déposée par l'administrateur dans ``<state_dir>/craft56109/`` (paquet facultatif déjà
installé ou copie hors ligne), est reconnue par sa taille et son empreinte SHA-256 publiées (``RELEASES``, tirées du relevé
``craft109-releases.json`` du dossier technique, 20 novembre 2026) ; ``installer`` la décompresse alors (liste fermée de types, chemins
sortants, liens et volume refusés) dans un dossier VERSIONNÉ, avec un manifeste des empreintes de chaque fichier ; seuls les fichiers du
manifeste, à leur empreinte, sont servis. Ni Rust, ni Cargo, ni compilation sur le poste de l'avocat.

- Séances : la table et les services existants de ``wordcraft56108`` (copie privée, candidat, contrôle, décision « Garder » avec
  rapprochement des écritures incertaines du lot A, jamais d'écrasement) ; l'outil de la séance est le nom de l'application. Ce module ajoute
  les capacités de format (analyse et contrôle XLSX, PPTX ; PDF : ceux de ``wordcraft56108``), sans recopier le validateur DOCX.
- Exports : un export (PDF d'un classeur, PNG d'une page…) est un NOUVEAU fichier voisin, jamais l'enregistrement du classeur ou de la
  présentation ; contenu contrôlé (signature du format), création exclusive, relecture ; intention durable (cible figée par clé) : une
  réponse perdue se rapproche sur la même cible.
- Commandes IA : le serveur valide un plan fermé (commandes et paramètres par application, nombre d'effets, révision, pause de l'IA) ; le
  pont n'applique que les commandes de ce plan, et une commande à résultat incertain n'est jamais rejouée automatiquement.

Non qualifié ici (aucune archive réelle disponible) : rendu, fidélité des formats XLSX/PDF/PPTX, noms réels des points d'entrée de chaque
build web et correspondance des commandes vers l'API de chaque application. L'option reste donc désactivée par défaut.
"""
from datetime import datetime, timezone
import hashlib
from html import escape as e
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import zipfile
import xml.etree.ElementTree as ET

from .common import Stop

# Relevé des releases (dossier technique, data/craft109-releases.json) : archives web officielles, tailles et empreintes publiées.
RELEASES = {
    'gridcraft': {'libelle': 'GridCraft', 'role': 'tableur', 'version': '0.19.1', 'archive': 'gridcraft-web-0.19.1.zip', 'taille': 9679862,
                  'sha256': '48c5d1b101befebb3c5ba3b7a14914f01c3207934813ed07dc4251b7ae116a78', 'documents': ('xlsx',), 'exports': ('pdf', 'csv', 'tsv')},
    'pdfcraft': {'libelle': 'PdfCraft', 'role': 'outils PDF', 'version': '0.21.1', 'archive': 'pdfcraft-web-0.21.1.zip', 'taille': 34367297,
                 'sha256': 'fe9708d46af191aebb56f70eb32cf8b112b0b9452d17ae6ba3a8c4a8665fe748', 'documents': ('pdf',), 'exports': ('png', 'jpg', 'jpeg', 'txt')},
    'deckcraft': {'libelle': 'DeckCraft', 'role': 'présentations', 'version': '0.18.1', 'archive': 'deckcraft-web-0.18.1.zip', 'taille': 11910503,
                  'sha256': '3196e537fcfab538aaa2e88584bc74e29bb584459b916ab584ac22fbafe32db5', 'documents': ('pptx',), 'exports': ('pdf', 'png', 'jpg', 'jpeg', 'txt', 'md')},
}
REGLAGE = 'craft56109:reglages'
DEFAUT = {app: False for app in RELEASES}          # désactivé par défaut : jamais activé sans qualification du cabinet
ROLES_EDITION = ('administrateur', 'avocat', 'assistant')
TYPES_SERVIS = {'.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.css': 'text/css', '.html': 'text/html; charset=utf-8',
                '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.woff': 'font/woff',
                '.ttf': 'font/ttf', '.otf': 'font/otf'}
ACCOMPAGNEMENT = ('.txt', '.md')                    # licences, NOTICE, ATTRIBUTION : décompressés, jamais servis
MAX_ENTREES = 2000
MAX_DECOMPRESSE = 400_000_000
MAX_FICHIER = 25_000_000
CADRE_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; img-src 'self' blob: data:; style-src 'self'; font-src 'self' data:; "
             "worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
SCHEMA = '''
CREATE TABLE IF NOT EXISTS craft56109_installations(app TEXT NOT NULL, version TEXT NOT NULL, archive_sha256 TEXT NOT NULL, manifeste_sha256 TEXT NOT NULL,
  fichiers INTEGER NOT NULL, octets INTEGER NOT NULL, par TEXT NOT NULL, le TEXT NOT NULL, PRIMARY KEY(app, version));
CREATE TABLE IF NOT EXISTS craft56109_exports(acteur TEXT NOT NULL, cle TEXT NOT NULL, seance TEXT NOT NULL, cible TEXT NOT NULL, sha256 TEXT NOT NULL,
  format TEXT NOT NULL, etat TEXT NOT NULL, motif TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
CREATE TABLE IF NOT EXISTS craft56109_plans(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, cle TEXT NOT NULL, seance TEXT NOT NULL, app TEXT NOT NULL,
  revision INTEGER NOT NULL, commandes TEXT NOT NULL, empreinte TEXT NOT NULL, cree_le TEXT NOT NULL, expire_le TEXT NOT NULL, UNIQUE(acteur, cle));
'''
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


# ------------------------------------------------------------------------------------------------ ressources compilées (détection locale)
def racine(desk):
    return Path(desk.c['state_dir']) / 'craft56109'


def dossier_version(desk, app):
    r = RELEASES[app]
    return racine(desk) / ('%s-%s' % (app, r['version']))


_EMPREINTES = {}


def _sha_fichier(chemin):
    """Empreinte d'un fichier (mise en cache par taille et date : une page ne relit pas 34 Mo à chaque affichage)."""
    st = chemin.stat()
    cle = (str(chemin), st.st_size, st.st_mtime_ns)
    if cle not in _EMPREINTES:
        h = hashlib.sha256()
        with open(chemin, 'rb') as f:
            for bloc in iter(lambda: f.read(1 << 20), b''):
                h.update(bloc)
        _EMPREINTES[cle] = h.hexdigest()
    return _EMPREINTES[cle]


def _manifeste(desk, app):
    chemin = dossier_version(desk, app) / 'manifeste-axiorhub.json'
    try:
        data = json.loads(chemin.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    r = RELEASES[app]
    if not isinstance(data, dict) or data.get('app') != app or data.get('version') != r['version'] or data.get('archive_sha256') != r['sha256'] \
            or not isinstance(data.get('fichiers'), dict):
        return None
    return data


def detecter(desk):
    """État local de chaque application (lecture seule, aucun réseau) : ``installe``, ``archive_verifiee`` (présente, à installer),
    ``archive_differente`` (empreinte ou taille différente : refusée), ``absent``."""
    out = {}
    for app, r in RELEASES.items():
        etat, message = 'absent', 'Ressources compilées absentes : rien n’est téléchargé ; déposez l’archive officielle pour l’installer.'
        if _manifeste(desk, app):
            etat, message = 'installe', 'Installé (version %s, empreintes vérifiées).' % r['version']
        else:
            archive = racine(desk) / r['archive']
            try:
                if archive.is_file():
                    if archive.stat().st_size == r['taille'] and _sha_fichier(archive) == r['sha256']:
                        etat, message = 'archive_verifiee', 'Archive officielle présente et vérifiée : installation possible (administrateur).'
                    else:
                        etat, message = 'archive_differente', 'Archive présente mais différente de la release publiée : refusée.'
            except OSError:
                etat, message = 'absent', 'Archive illisible.'
        out[app] = {'app': app, 'libelle': r['libelle'], 'role': r['role'], 'version': r['version'], 'etat': etat, 'message': message,
                    'poids_archive_mo': round(r['taille'] / 1_000_000, 1)}
    return out


def installer(desk, acteur, role, app):
    """Décompresse l'archive vérifiée dans un dossier versionné (jamais par-dessus une installation existante) ; manifeste des empreintes."""
    if role != 'administrateur':
        raise Stop('role_interdit')
    if app not in RELEASES:
        raise Stop('introuvable')
    ensure_schema(desk)
    r = RELEASES[app]
    cible = dossier_version(desk, app)
    if _manifeste(desk, app):
        return {'app': app, 'version': r['version'], 'etat': 'installe', 'deja': True}
    archive = racine(desk) / r['archive']
    if not archive.is_file() or archive.stat().st_size != r['taille'] or _sha_fichier(archive) != r['sha256']:
        raise Stop('archive_craft_non_verifiee')
    temp = racine(desk) / ('.installation-%s-%s' % (app, secrets.token_hex(4)))
    fichiers, total = {}, 0
    try:
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTREES:
                raise Stop('archive_craft_refusee')
            for i in infos:
                nom = i.filename
                if i.is_dir():
                    continue
                parties = PurePosixPath(nom).parts
                if nom.startswith('/') or '\\' in nom or '..' in parties or ':' in nom or i.flag_bits & 1 or (i.external_attr >> 16) & 0o170000 == 0o120000:
                    raise Stop('archive_craft_refusee')       # chemin sortant, lien symbolique, chiffrement
                total += i.file_size
                if i.file_size > MAX_FICHIER or total > MAX_DECOMPRESSE:
                    raise Stop('archive_craft_refusee')
                ext = PurePosixPath(nom).suffix.lower()
                base = PurePosixPath(nom).name.upper()
                if ext not in TYPES_SERVIS and ext not in ACCOMPAGNEMENT and not base.startswith(('LICENSE', 'NOTICE', 'COPYING')):
                    continue                                   # type non admis : ni décompressé, ni servi
                data = z.read(i)
                dest = temp.joinpath(*parties)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, 'xb') as f:
                    f.write(data)
                if ext in TYPES_SERVIS:
                    fichiers['/'.join(parties)] = [hashlib.sha256(data).hexdigest(), len(data), TYPES_SERVIS[ext]]
        manifeste = {'app': app, 'version': r['version'], 'archive_sha256': r['sha256'], 'fichiers': fichiers, 'installe_le': _maintenant()}
        brut = json.dumps(manifeste, ensure_ascii=False, sort_keys=True, indent=1)
        (temp / 'manifeste-axiorhub.json').write_text(brut, encoding='utf-8')
        if cible.exists():
            raise Stop('installation_existante')
        os.replace(temp, cible)
    except (zipfile.BadZipFile, OSError):
        raise Stop('archive_craft_refusee') from None
    finally:
        if temp.exists():
            shutil.rmtree(temp, ignore_errors=True)
    desk.db.execute('INSERT OR REPLACE INTO craft56109_installations(app,version,archive_sha256,manifeste_sha256,fichiers,octets,par,le) VALUES(?,?,?,?,?,?,?,?)',
                    (app, r['version'], r['sha256'], hashlib.sha256(brut.encode()).hexdigest(), len(fichiers), total, str(acteur)[:160], _maintenant()))
    desk.db.commit()
    desk.audit('craft56109_installe', {'app': app, 'version': r['version'], 'fichiers': len(fichiers), 'par': str(acteur)[:160]})
    return {'app': app, 'version': r['version'], 'etat': 'installe', 'fichiers': len(fichiers)}


def reglages(desk):
    try:
        brut = desk.settings(REGLAGE, None)
    except Exception:   # noqa: BLE001 — base illisible : rien n'est proposé (jamais un accès élargi sur une panne)
        return dict(DEFAUT)
    return {app: bool(brut.get(app)) if isinstance(brut, dict) else False for app in RELEASES}


def actif(desk, app):
    """Proposé seulement si l'administrateur l'a activé ET si les ressources compilées sont installées et vérifiées (à cet instant)."""
    return app in RELEASES and reglages(desk).get(app, False) and _manifeste(desk, app) is not None


def regler(desk, acteur, role, app, active):
    if role != 'administrateur':
        raise Stop('role_interdit')
    if app not in RELEASES:
        raise Stop('introuvable')
    if active and not _manifeste(desk, app):
        raise Stop('ressources_craft_absentes')      # l'option n'est activable que si les ressources compilées sont présentes
    valeurs = reglages(desk)
    valeurs[app] = bool(active)
    desk.setting(REGLAGE, valeurs)
    desk.audit('craft56109_regle', {'app': app, 'active': bool(active), 'par': str(acteur)[:160]})
    return reglages(desk)


def ressource(desk, app_version, chemin):
    """Octets d'un fichier du manifeste, à son empreinte ; tout autre chemin : introuvable."""
    m = re.fullmatch(r'(gridcraft|pdfcraft|deckcraft)-(\d+\.\d+\.\d+)', str(app_version or ''))
    if not m or m.group(2) != RELEASES[m.group(1)]['version']:
        raise Stop('introuvable')
    app = m.group(1)
    if not actif(desk, app):
        raise Stop('introuvable')
    manifeste = _manifeste(desk, app)
    entree = manifeste['fichiers'].get(str(chemin or ''))
    if not entree:
        raise Stop('introuvable')
    fichier = dossier_version(desk, app).joinpath(*PurePosixPath(chemin).parts)
    data = fichier.read_bytes()
    if hashlib.sha256(data).hexdigest() != entree[0]:
        raise Stop('introuvable')                     # fichier modifié depuis l'installation : jamais servi
    return data, entree[2]


def points_entree(desk, app):
    """Colle JavaScript et module WebAssembly du build web (repérés dans le manifeste : ``<app>-web*.js`` et ``*_bg.wasm``)."""
    manifeste = _manifeste(desk, app) or {'fichiers': {}}
    glue = sorted(k for k in manifeste['fichiers'] if re.fullmatch(r'(?:[\w.-]+/)?%s-web[\w.-]*\.js' % app, k))
    wasm = sorted(k for k in manifeste['fichiers'] if k.endswith('_bg.wasm'))
    return (glue[0] if glue else ''), (wasm[0] if wasm else '')


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
    """Séance d'édition Craft : option active et ressources présentes, droits de la source (même 404 neutre), format de l'application,
    copie privée de l'original ; rend la séance (sans les octets)."""
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


# ------------------------------------------------------------------------------------------------ commandes IA (plan fermé validé par le serveur)
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
    """Plan de commandes proposé (par l'assistant ou saisi) : validé par le serveur, qui fait autorité ; IA en pause : refus expliqué."""
    from .arret5643 import manual_paused
    ensure_schema(desk)
    s = _seance(desk, acteur, ident)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', str(cle or '')):
        raise Stop('cle_requise')
    if manual_paused(desk) or desk.settings('missions567:pause', False):
        raise Stop('ia_en_pause')                     # les sauvegardes manuelles restent possibles ; les commandes IA attendent la reprise
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
    return {'plan_id': pid, 'commandes': propres, 'revision': revision, 'expire_le': expire, 'rejoue': False,
            'message': '%d commande(s) validée(s) : elles seront appliquées dans l’éditeur ouvert, puis vous enregistrerez et comparerez.' % len(propres)}


# ------------------------------------------------------------------------------------------------ routes ``m56109/editeurs/``
PREFIXE = 'm56109/editeurs/'
STATUTS_HTTP = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'operation_en_cours': '409 Conflict',
                'original_modifie': '409 Conflict', 'ecriture_a_verifier': '409 Conflict', 'deja_existant': '409 Conflict', 'ia_en_pause': '409 Conflict',
                'role_interdit': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'craft_desactive': '403 Forbidden', 'ressources_craft_absentes': '409 Conflict',
                'origine_ou_csrf_refuse': '403 Forbidden', 'controle_acces_indisponible': '503 Service Unavailable', 'stockage_injoignable': '503 Service Unavailable',
                'requete_trop_longue': '413 Payload Too Large', 'document_word_trop_volumineux': '413 Payload Too Large'}
MESSAGES = {
    'introuvable': 'Document ou séance introuvable, ou action non permise.', 'role_interdit': 'Votre rôle ne permet pas cette action.',
    'craft_desactive': 'Cet éditeur facultatif est désactivé sur cette installation.',
    'ressources_craft_absentes': 'Les ressources compilées de cet éditeur ne sont pas installées : l’option ne peut pas être activée. Rien n’est téléchargé.',
    'archive_craft_non_verifiee': 'Archive absente ou différente de la release publiée : installation refusée.',
    'archive_craft_refusee': 'Archive refusée (chemin sortant, lien, volume ou type de fichier non admis).',
    'version_perimee': 'Une version plus récente existe (autre onglet ?) : rechargez la page. Rien n’a été écrit.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.', 'cle_requise': 'Demande incomplète : rechargez la page.',
    'format_export_refuse': 'Format d’export non admis pour cet éditeur.', 'export_refuse': 'Le fichier exporté n’a pas le contenu du format annoncé : rien n’a été écrit.',
    'ia_en_pause': 'IA en pause : les commandes de l’IA attendent la reprise. Vos enregistrements manuels restent possibles.',
    'commande_non_autorisee': 'Commande non autorisée pour cet éditeur.', 'parametre_refuse': 'Paramètre de commande refusé.',
    'formule_non_demandee': 'Une valeur commençant par « = » n’est importée comme formule que sur choix explicite.',
    'formule_externe_refusee': 'Formule refusée : lien ou ressource externe.', 'plan_refuse': 'Plan de commandes refusé.',
}
LECTURES = ('etat',)


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    if method in ('GET', 'HEAD'):
        return True
    if method != 'POST':
        return False
    if reste in ('reglage', 'installer'):
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
                visibles = {k: {**v, 'active': regl[k] and v['etat'] == 'installe'} for k, v in etats.items() if acteur[1] == 'administrateur' or (regl[k] and v['etat'] == 'installe')}
                return _json({'editeurs': visibles, 'note': 'Formats réels non qualifiés : chaque version enregistrée est contrôlée avant d’être gardée.'})
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
        if reste == 'installer':
            return _json(installer(desk, acteur[0], acteur[1], str(d.get('app') or '')))
        if reste == 'seances':
            app, s, p = str(d.get('app') or ''), str(d.get('s') or ''), str(d.get('p') or '')
            return _json(WC.executer(desk, acteur, cle, 'craft_ouvrir', {'app': app, 's': s, 'p': p}, lambda: ouvrir(desk, auth, acteur, app, s, p, sources_)))
        ident = parts[1]
        if parts[2] == 'actions-ia':
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


# ------------------------------------------------------------------------------------------------ pages : éditeur, cadre, ressources
def bouton_html(desk, prefix, ident_source, chemin, role, titre=''):
    """« Modifier dans GridCraft / PdfCraft / DeckCraft (facultatif, version d'essai) » : seulement si l'option est active (désactivée par défaut)."""
    from urllib.parse import urlencode
    app = application_pour(chemin)
    if not app or role not in ROLES_EDITION:
        return ''
    try:
        if not actif(desk, app):
            return ''
    except Exception:   # noqa: BLE001 — réglage ou installation illisible : rien de proposé
        return ''
    sr = ('<span class="sr-only"> : %s</span>' % e(titre)) if titre else ''
    return '<a class="ax-btn ghost" href="%s">Modifier dans %s (facultatif, version d’essai)%s</a>' % (
        e(prefix + '/editeur/craft?' + urlencode({'app': app, 's': ident_source, 'p': chemin}), quote=True), e(RELEASES[app]['libelle']), sr)


def section_reglages(desk, prefix=''):
    """Réglages de l'atelier (administrateur) : état local de chaque éditeur, installation depuis l'archive déposée, activation."""
    etats, regl = detecter(desk), reglages(desk)
    lignes = []
    for app, x in etats.items():
        installe = x['etat'] == 'installe'
        actions = ''
        if x['etat'] == 'archive_verifiee':
            actions = '<button type="button" class="ax-btn ghost" data-c109-reglage="installer" data-c109-app="%s">Installer depuis l’archive vérifiée</button>' % app
        if installe:
            actions = ('<label class="e5636-switch"><input type="checkbox" data-c109-reglage="activer" data-c109-app="%s"%s> Activer %s (expérimental)</label>' % (
                app, ' checked' if regl[app] else '', e(x['libelle'])))
        lignes.append('<li><strong>%s</strong> (%s, version %s, archive %s Mo) — %s %s</li>' % (e(x['libelle']), e(x['role']), e(x['version']),
                                                                                               str(x['poids_archive_mo']).replace('.', ','), e(x['message']), actions))
    return ('<section class="ax-card c109-reglages" id="ax-craft-56109"><h2>Éditeurs facultatifs (GridCraft, PdfCraft, DeckCraft)</h2>'
            '<p class="ax-muted">Modules web en version d’essai (« alpha » selon leurs auteurs), désactivés par défaut. Aucun fichier n’est téléchargé : l’option '
            'n’est activable que si l’archive officielle a été déposée dans le dossier « craft56109 » de l’application puis installée. Formats réels non '
            'qualifiés : chaque enregistrement est contrôlé et gardé à côté de l’original ; un export est un fichier distinct.</p><ul>%s</ul>'
            '<p class="c109-etat" role="status" aria-live="polite" data-c109-etat-reglage></p><div hidden data-c109-reglages="%s"></div></section>'
            '<script defer src="%s"></script>') % (''.join(lignes), e(prefix + '/api440/' + PREFIXE, quote=True), e(prefix + '/static/v56109-craft-host.js', quote=True))


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
                data, kind = ressource(desk, m.group(1), m.group(2))
            except (Stop, OSError):
                return _introuvable_brut()
            return {'status': '200 OK', 'kind': kind, 'body': data, 'headers': [('Cache-Control', 'private, max-age=86400'), ('X-Content-Type-Options', 'nosniff')]}
        return _page(desk, auth, prefix, acteur, args, shell, sources_)
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


def _introuvable_brut():
    return {'status': '404 Not Found', 'kind': 'text/plain; charset=utf-8', 'body': 'Introuvable.', 'headers': [('Cache-Control', 'private, no-store')]}


def _cadre(desk, prefix, app_version):
    app = app_version.split('-', 1)[0]
    if not actif(desk, app):
        return _introuvable_brut()
    glue, wasm = points_entree(desk, app)
    p = e(prefix, quote=True)
    html = ('<!doctype html><html lang="fr" data-c109="cadre" data-c109-app="%s" data-c109-base="%s/editeur/craft/%s/" data-c109-glue="%s" data-c109-wasm="%s">'
            '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>'
            '<link rel="stylesheet" href="%s/static/v56109-produire.css"><script defer src="%s/static/v56109-craft-host.js"></script></head>'
            '<body class="c109-cadre-corps"><canvas id="%s_canvas" class="c109-toile"></canvas><p class="c109-chargement" data-c109-chargement>Chargement de l’éditeur…</p>'
            '</body></html>') % (app, p, e(app_version, quote=True), e(glue, quote=True), e(wasm, quote=True), e(RELEASES[app]['libelle']), p, p, app)
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': html, 'csp': CADRE_CSP, 'frame_self': True,
            'headers': [('Cache-Control', 'private, no-store')]}


def _page(desk, auth, prefix, acteur, args, shell, sources_=None):
    from . import wordcraft56108 as WC
    from .web440 import page_out
    app = str(args.get('app') or '')
    s, p = str(args.get('s') or ''), str(args.get('p') or '')
    titre = 'Modifier dans un éditeur facultatif'
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
                      'modifiable avec les autres éditeurs du cabinet.</p>' % (e(titre), e(MESSAGES['craft_desactive'])), '403 Forbidden')
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
              'cadre': '%s/editeur/craft/%s-%s/cadre?host=parent&chrome=none%s' % (prefix, app, r['version'], '&control' if app == 'pdfcraft' else ''),
              'commandes': sorted(COMMANDES[app])}
    corps = ('<div class="c109-barre"><a class="ax-btn ghost" href="%s" data-c109="retour">Retour</a><h1 id="c109-titre">%s <span class="c109-badge">%s %s, version d’essai</span></h1>'
             '<p class="c109-stockage" role="status" aria-live="polite" data-c109-etat>Document non ouvert.</p>'
             '<button type="button" class="ax-btn" data-c109="ouvrir">Ouvrir</button><button type="button" class="ax-btn ghost" data-c109="fermer" hidden>Fermer</button></div>'
             '<div class="c109-zone" data-c109-zone hidden><iframe class="c109-iframe" title="%s : %s" data-c109-iframe></iframe></div>'
             '<section class="c109-controle" data-c109-controle hidden aria-labelledby="c109-t-controle"><h2 id="c109-t-controle" tabindex="-1">Comparer avec l’original</h2>'
             '<div data-c109-rapport></div><p class="c109-actions"><button type="button" class="ax-btn" data-c109="garder">Conserver cette version</button>'
             '<button type="button" class="ax-btn ghost" data-c109="abandonner">Abandonner cette version</button><button type="button" class="ax-btn ghost" data-c109="verifier" hidden>'
             'Vérifier</button></p></section>'
             '<details class="c109-ia"><summary>Commandes proposées par l’IA</summary><p class="ax-muted">Commandes admises : %s. Le serveur valide le plan ; '
             'elles sont appliquées dans l’éditeur ouvert, puis vous enregistrez et comparez. Une commande au résultat incertain n’est jamais rejouée.</p>'
             '<label>Plan (JSON)<textarea rows="4" data-c109-plan spellcheck="false"></textarea></label><button type="button" class="ax-btn ghost" data-c109="plan">'
             'Valider et appliquer</button></details><p class="c109-aide">Formats réels non qualifiés : chaque version enregistrée est contrôlée avant d’être '
             'gardée à côté de l’original ; un export est un fichier distinct.</p><div hidden data-c109-config="%s"></div>') % (
        e(prefix + '/production', quote=True), e(nom), e(r['libelle']), e(r['version']), e(r['libelle']), e(nom), e(', '.join(sorted(COMMANDES[app]))),
        e(json.dumps(config, ensure_ascii=False), quote=True))
    return sortie(corps)

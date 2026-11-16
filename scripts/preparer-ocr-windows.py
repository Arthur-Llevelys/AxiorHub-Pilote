#!/usr/bin/env python3
"""5.6.106 (lot W) : copie réduite de Tesseract et de Poppler pour l'application Windows (binaires hors dépôt).

  python scripts/preparer-ocr-windows.py TIERS_OCR

``TIERS_OCR`` contient ``extrait\\tesseract`` (installateur UB-Mannheim 5.4.0 décompressé), ``extrait\\poppler\\poppler-26.09.0``
(publication oschwartz10612/poppler-windows 26.09.0-0) et ``telechargements\\fra.traineddata`` / ``eng.traineddata`` (dépôt
tesseract-ocr/tessdata, commit ``c2b2e0df``). Le script produit ``TIERS_OCR\\reduit`` :

- ``tesseract\\`` : ``tesseract.exe`` et **seulement** les DLL nécessaires (tables d'import et d'import différé des en-têtes PE lues
  en Python, fermeture transitive), ``tessdata\\`` avec ``fra``, ``eng``, ``osd``, les configurations utilisées par AxiorHub
  (``tsv``, ``pdf``, ``txt``, ``hocr``) et ``pdf.ttf`` (mode « textonly_pdf » des pièces rendues cherchables) ;
- ``poppler\\`` : ``pdftoppm.exe``, ``pdftotext.exe``, ``pdfinfo.exe`` (les seuls outils appelés par AxiorHub) et leurs DLL
  nécessaires. ``share\\poppler`` (poppler-data : tables CJK et cyrilliques) n'est pas repris : rendu et texte des PDF en écriture
  latine n'en dépendent pas ;
- ``licences\\`` : les textes de licence **trouvés** (aucun n'est inventé) : dans les dossiers extraits (Tesseract) et dans
  ``TIERS_OCR\\licences-amont\\<projet>\\`` (fichiers de licence pris sur les dépôts ou sites officiels de chaque projet, avec leur
  ``SOURCES.txt`` : adresse, date, SHA-256) ; les mentions de copyright lues dans les ressources de version des binaires copiés ;
  ``THIRD_PARTY_NOTICES-OCR.txt`` (licence de chaque composant, source correspondante de Poppler jointe à la publication) ;
  ``MANIFEST-ocr.json`` (taille et SHA-256 de chaque fichier, dépendances système, « licence : trouvée » ou « manquante » par
  composant, source de Poppler, ``complet``). La construction Windows n'inclut l'OCR que si ``complet`` est vrai.

5.6.106 : la source de Poppler (``telechargements\\poppler-26.09.0.tar.xz``) est vérifiée et référencée (nom, SHA-256) ; elle n'est
pas copiée dans ``reduit`` : elle est jointe à la page de version, à côté de l'installateur.

Les binaires ne sont ni modifiés ni « strippés » : simple copie. Rien n'est exécuté ni téléchargé. Une DLL absente des dossiers
extraits est une DLL du système (noyau Windows, ensembles d'API ``api-ms-win-*``) ; elle est notée dans le manifeste.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys

TESSERACT_EXE = ('tesseract.exe',)
POPPLER_EXE = ('pdftoppm.exe', 'pdftotext.exe', 'pdfinfo.exe')   # pdfimages, pdftocairo… : jamais appelés par AxiorHub
TESSDATA = ('fra.traineddata', 'eng.traineddata', 'osd.traineddata', 'pdf.ttf')
CONFIGS = ('tsv', 'pdf', 'txt', 'hocr')
TESSDATA_COMMIT = 'c2b2e0df86272ce11be323f23f96cf656565ed41'
# Empreintes des originaux (feuille du lot W) : vérifiées avant toute copie
ORIGINAUX = {'fra.traineddata': 'eac01c1d72540d6090facb7b2f42dd0a2ee8fc57c5be1b20548ae668e2761913',
             'eng.traineddata': 'daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047'}
POPPLER_VERSION = '26.09.0'
# Source correspondante de Poppler (téléchargée depuis poppler.freedesktop.org avec l'accord de l'avocat, 16/11/2026) : jointe à la
# publication de chaque version, à côté de l'installateur.
SOURCE_POPPLER = {'nom': 'poppler-%s.tar.xz' % POPPLER_VERSION, 'sha256': '8059eadb6805340768f138c465b57f8164c92b4a0773c37ef031ea6c0d987b2e',
                  'url': 'https://poppler.freedesktop.org/poppler-%s.tar.xz' % POPPLER_VERSION}
SOURCES_POPPLER = ('La source correspondante de Poppler %s est fournie avec ce binaire : fichier « %s » (SHA-256 %s), joint à la '
                   'publication de cette version d’AxiorHub Pilote, à côté de l’installateur. En complément : %s (dépôt : '
                   'https://gitlab.freedesktop.org/poppler/poppler/-/tree/poppler-%s). Binaires Windows repris tels quels de '
                   'https://github.com/oschwartz10612/poppler-windows/releases/tag/v%s-0 (construits à partir des paquets conda-forge, '
                   'recettes : https://github.com/conda-forge/poppler-feedstock).' % (
                       POPPLER_VERSION, SOURCE_POPPLER['nom'], SOURCE_POPPLER['sha256'], SOURCE_POPPLER['url'], POPPLER_VERSION, POPPLER_VERSION))
# Sources jointes à la publication (licences GPL et LGPL), téléchargées depuis les sites officiels avec l'accord de l'avocat (16/11/2026).
# ``lire`` : comment la version embarquée est lue dans la copie (chaîne d'un binaire ou ressource de version) ; une version lue
# différente de ``version`` rend la source « non correspondante ».
SOURCES = {
    'Poppler': dict(SOURCE_POPPLER, version=POPPLER_VERSION, licence='GPL-2.0-or-later', lire=None),
    'JBIG-KIT': {'nom': 'jbigkit-2.1.tar.gz', 'sha256': 'de7106b6bfaf495d6865c7dd7ac6ca1381bd12e0d81405ea81e7f2167263d932',
                 'url': 'https://www.cl.cam.ac.uk/~mgk25/jbigkit/download/jbigkit-2.1.tar.gz', 'version': '2.1', 'licence': 'GPL-2.0-or-later',
                 'lire': ('chaine', 'tesseract/libjbig-0.dll', rb'JBIG-KIT ([0-9][0-9.]*) ')},
    'libiconv': {'nom': 'libiconv-1.17.tar.gz', 'sha256': '8f74213b56238c85a50a5329f77e06198771e70dd9a739779f4c02f65d971313',
                 'url': 'https://ftp.gnu.org/pub/gnu/libiconv/libiconv-1.17.tar.gz', 'version': '1.17', 'licence': 'LGPL-2.1-or-later',
                 'signature': 'libiconv-1.17.tar.gz.sig', 'lire': ('ressource', 'tesseract/libiconv-2.dll', 'ProductVersion')},
}
SOURCES_TESSERACT = ('Tesseract 5.4.0 (UB Mannheim, construction 20240606) : https://github.com/UB-Mannheim/tesseract/wiki ; sources : '
                     'https://github.com/tesseract-ocr/tesseract/tree/5.4.0 ; données : https://github.com/tesseract-ocr/tessdata/tree/%s '
                     '(Apache-2.0).' % TESSDATA_COMMIT)


# ================================================================================================ en-têtes PE (Python standard)
class PEInvalide(ValueError):
    pass


def _sections(raw, off_sections, nombre):
    out = []
    for i in range(nombre):
        base = off_sections + 40 * i
        if base + 40 > len(raw):
            raise PEInvalide('table des sections tronquée')
        vsize, vaddr, rsize, rptr = struct.unpack_from('<IIII', raw, base + 8)
        out.append((vaddr, max(vsize, rsize), rptr, rsize))
    return out


def _decalage(sections, rva):
    for vaddr, taille, rptr, rsize in sections:
        if vaddr <= rva < vaddr + taille and rva - vaddr < rsize:
            return rptr + rva - vaddr
    raise PEInvalide('adresse hors des sections')


def _chaine(raw, off, maxi=512):
    fin = raw.find(b'\0', off, off + maxi)
    if fin < 0:
        raise PEInvalide('nom non terminé')
    return raw[off:fin].decode('ascii', 'replace')


def imports_pe(raw):
    """Noms des DLL importées (table d'import et table d'import différé), dans l'ordre, sans doublon. ``PEInvalide`` sinon."""
    if len(raw) < 0x40 or raw[:2] != b'MZ':
        raise PEInvalide('pas un exécutable MZ')
    pe = struct.unpack_from('<I', raw, 0x3C)[0]
    if pe + 24 > len(raw) or raw[pe:pe + 4] != b'PE\0\0':
        raise PEInvalide('signature PE absente')
    nombre, = struct.unpack_from('<H', raw, pe + 6)
    taille_opt, = struct.unpack_from('<H', raw, pe + 20)
    opt = pe + 24
    magic, = struct.unpack_from('<H', raw, opt)
    if magic == 0x20B:          # PE32+
        image_base, = struct.unpack_from('<Q', raw, opt + 24)
        rep = opt + 112
    elif magic == 0x10B:        # PE32
        image_base, = struct.unpack_from('<I', raw, opt + 28)
        rep = opt + 96
    else:
        raise PEInvalide('en-tête optionnel inconnu')
    nb_rep, = struct.unpack_from('<I', raw, rep - 4)
    sections = _sections(raw, opt + taille_opt, nombre)

    def repertoire(i):
        if i >= nb_rep or rep + 8 * i + 8 > len(raw):
            return 0, 0
        return struct.unpack_from('<II', raw, rep + 8 * i)

    noms = []
    rva, taille = repertoire(1)            # imports
    if rva:
        off = _decalage(sections, rva)
        for k in range(4096):
            d = raw[off + 20 * k: off + 20 * k + 20]
            if len(d) < 20 or d == b'\0' * 20:
                break
            nom_rva = struct.unpack_from('<I', d, 12)[0]
            if nom_rva:
                noms.append(_chaine(raw, _decalage(sections, nom_rva)))
    rva, taille = repertoire(13)           # imports différés (chargés à la première utilisation : gardés par prudence)
    if rva:
        off = _decalage(sections, rva)
        for k in range(4096):
            d = raw[off + 32 * k: off + 32 * k + 32]
            if len(d) < 32 or d == b'\0' * 32:
                break
            attributs, nom = struct.unpack_from('<II', d, 0)
            if not attributs & 1 and nom >= image_base:   # anciens éditeurs de liens : adresses virtuelles
                nom -= image_base
            if nom:
                noms.append(_chaine(raw, _decalage(sections, nom)))
    vus, out = set(), []
    for n in noms:
        if n.lower() not in vus:
            vus.add(n.lower())
            out.append(n)
    return out


def systeme(nom):
    """Ensembles d'API de Windows (``api-ms-win-*``, ``ext-ms-*``) et CRT universelle : fournis par Windows 10 et 11, jamais copiés."""
    n = nom.lower()
    return n.startswith(('api-ms-win-', 'ext-ms-')) or n == 'ucrtbase.dll'


def fermeture(dossier, racines, lire=None):
    """Fermeture transitive des DLL de ``dossier`` nécessaires aux ``racines``. Renvoie (fichiers locaux triés, DLL système triées).
    ``lire(chemin) -> octets`` est remplaçable dans les essais (faux en-têtes PE)."""
    dossier = Path(dossier)
    lire = lire or (lambda p: Path(p).read_bytes())
    locaux = {p.name.lower(): p.name for p in dossier.iterdir() if p.is_file()}
    garder, sys_, file = {}, set(), [r for r in racines]
    while file:
        nom = file.pop(0)
        cle = nom.lower()
        if cle in garder:
            continue
        if cle not in locaux:
            raise FileNotFoundError('racine absente : ' + nom)
        garder[cle] = locaux[cle]
        for dep in imports_pe(lire(dossier / locaux[cle])):
            d = dep.lower()
            if systeme(dep):
                sys_.add(d)
            elif d in locaux:
                if d not in garder:
                    file.append(locaux[d])
            else:
                sys_.add(d)
    return sorted(garder.values(), key=str.lower), sorted(sys_)


# ================================================================================================ ressources de version (mentions de copyright)
def version_info(raw):
    """Champs texte de la ressource de version (``CompanyName``, ``LegalCopyright``…) lus dans les octets du binaire ; {} sinon."""
    out = {}
    for cle in ('CompanyName', 'ProductName', 'FileVersion', 'ProductVersion', 'LegalCopyright', 'FileDescription'):
        motif = (cle + '\0').encode('utf-16-le')
        i = raw.find(motif)
        if i < 0:
            continue
        j = i + len(motif)
        j += (-j) % 4                                     # alignement sur 32 bits
        fin = j
        while fin + 1 < len(raw) and raw[fin:fin + 2] != b'\0\0' and fin - j < 1024:
            fin += 2
        try:
            valeur = raw[j:fin].decode('utf-16-le').strip()
        except UnicodeDecodeError:
            continue
        if valeur and valeur.isprintable():
            out[cle] = valeur
    return out


def version_lue(sortie, lire):
    """Version d'un composant lue dans la copie (chaîne d'un binaire ou champ de la ressource de version) ; ``None`` si illisible."""
    if not lire:
        return None
    mode, rel, motif = lire
    f = Path(sortie) / rel
    if not f.is_file():
        return None
    raw = f.read_bytes()
    if mode == 'chaine':
        trouve = re.search(motif, raw)
        return trouve.group(1).decode('ascii', 'replace').rstrip('.') if trouve else None
    return version_info(raw).get(motif)


# ================================================================================================ préparation
def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for bloc in iter(lambda: f.read(1 << 20), b''):
            h.update(bloc)
    return h.hexdigest()


def taille_dossier(path):
    return sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file())


def _copier(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)          # simple copie : aucun binaire modifié


LICENCES = (   # fichiers trouvés dans « extrait » : (composant, licence, chemin relatif, nom dans « licences », requis)
    ('Tesseract', 'Apache-2.0', 'tesseract/doc/LICENSE', 'tesseract/LICENSE', True),
    ('Tesseract', 'Apache-2.0', 'tesseract/doc/AUTHORS', 'tesseract/AUTHORS', False),
)
# Composants embarqués : (motif du nom de la DLL, libellé, licence, dossier de ``licences-amont``). Les identifiants de licence sont
# relevés à la lecture des fichiers officiels repris (16/11/2026) ; seul le texte copié fait foi. Un composant sans dossier
# ``licences-amont`` (ou sans ``SOURCES.txt``) est « manquante ».
COMPOSANTS = (
    ('tesseract', 'Tesseract', 'Apache-2.0', None), ('leptonica', 'Leptonica', 'licence Leptonica (type BSD)', 'leptonica'),
    ('poppler', 'Poppler', 'GPL-2.0-or-later', 'poppler'), ('icu', 'ICU', 'Unicode-3.0 (et licences tierces citées)', 'icu'),
    ('tiff', 'libtiff', 'libtiff', 'libtiff'), ('png', 'libpng', 'libpng-2.0', 'libpng'), ('openjp2', 'OpenJPEG', 'BSD-2-Clause', 'openjpeg'),
    ('jpeg', 'libjpeg-turbo', 'IJG, BSD-3-Clause, Zlib', 'libjpeg-turbo'), ('zlib', 'zlib', 'Zlib', 'zlib'),
    ('freetype', 'FreeType', 'FTL (ou GPL-2.0)', 'freetype'), ('lcms', 'Little CMS', 'MIT', 'lcms2'),
    ('webp', 'libwebp', 'BSD-3-Clause', 'libwebp'), ('sharpyuv', 'libwebp', 'BSD-3-Clause', 'libwebp'), ('gif', 'giflib', 'MIT', 'giflib'),
    ('archive', 'libarchive', 'BSD-2-Clause', 'libarchive'), ('curl', 'curl', 'curl', 'curl'), ('crypto', 'OpenSSL', 'Apache-2.0', 'openssl'),
    ('ssl', 'OpenSSL', 'Apache-2.0', 'openssl'), ('ssh2', 'libssh2', 'BSD-3-Clause', 'libssh2'), ('lzma', 'XZ Utils (liblzma)', '0BSD', 'xz'),
    ('zstd', 'Zstandard', 'BSD-3-Clause', 'zstd'), ('bz2', 'bzip2', 'bzip2-1.0.6', 'bzip2'), ('lz4', 'LZ4', 'BSD-2-Clause', 'lz4'),
    ('b2', 'libb2', 'CC0-1.0', 'libb2'), ('deflate', 'libdeflate', 'MIT', 'libdeflate'), ('lerc', 'LERC', 'Apache-2.0', 'lerc'),
    ('jbig', 'JBIG-KIT', 'GPL-2.0-or-later', 'jbigkit'), ('iconv', 'libiconv', 'LGPL-2.1-or-later', 'libiconv'),
    ('charset', 'libiconv', 'LGPL-2.1-or-later', 'libiconv'), ('expat', 'Expat', 'MIT', 'expat'),
    ('stdc++', 'Runtime GCC (libstdc++)', 'GPL-3.0-or-later avec exception GCC Runtime Library 3.1', 'gcc-runtime'),
    ('gcc_s', 'Runtime GCC (libgcc)', 'GPL-3.0-or-later avec exception GCC Runtime Library 3.1', 'gcc-runtime'),
    ('winpthread', 'MinGW-w64 winpthreads', 'MIT et BSD-3-Clause', 'winpthreads'), ('psl', 'libpsl', 'MIT', 'libpsl'),
    ('vcruntime', 'Runtime Microsoft Visual C++', 'conditions de Microsoft (« Distributable Code »)', 'microsoft-vcruntime'),
    ('msvcp', 'Runtime Microsoft Visual C++', 'conditions de Microsoft (« Distributable Code »)', 'microsoft-vcruntime'),
    ('concrt', 'Runtime Microsoft Visual C++', 'conditions de Microsoft (« Distributable Code »)', 'microsoft-vcruntime'),
    ('vcomp', 'Runtime Microsoft Visual C++', 'conditions de Microsoft (« Distributable Code »)', 'microsoft-vcruntime'),
    ('cairo', 'cairo', None, None), ('pixman', 'pixman', None, None), ('glib', 'GLib', None, None), ('gobject', 'GLib', None, None),
    ('gio', 'GLib', None, None), ('gmodule', 'GLib', None, None), ('intl', 'gettext', None, None), ('fontconfig', 'Fontconfig', None, None),
    ('harfbuzz', 'HarfBuzz', None, None), ('graphite', 'Graphite2', None, None), ('fribidi', 'FriBidi', None, None),
    ('pango', 'Pango', None, None), ('thai', 'libthai', None, None), ('datrie', 'libdatrie', None, None), ('pcre', 'PCRE2', None, None),
    ('ffi', 'libffi', None, None), ('brotli', 'Brotli', None, None), ('krb5', 'MIT Kerberos', None, None), ('nghttp', 'nghttp2', None, None),
)


def composant(nom):
    """(libellé, licence, dossier de licences-amont) d'une DLL, ou ``None`` si inconnue."""
    n = nom.lower()
    for motif, libelle, licence, dossier in COMPOSANTS:
        if motif in n:
            return libelle, licence, dossier
    return None


def _licences(tiers, sortie, manifeste):
    """Licence de chaque composant embarqué : « trouvée » (fichiers copiés dans ``licences/``) ou « manquante »."""
    extrait, amont = tiers / 'extrait', tiers / 'licences-amont'
    out = {}
    for nom_c in ('tesseract', 'poppler'):
        for f in manifeste['composants'][nom_c]:
            c = composant(f)
            if c is None:
                if f.lower().endswith('.dll'):
                    out.setdefault('inconnu : ' + f, {'licence': None, 'dossier': None, 'dll': [], 'fichiers': []})['dll'].append(nom_c + '/' + f)
                continue
            libelle, licence, dossier = c
            entree = out.setdefault(libelle, {'licence': licence, 'dossier': dossier, 'dll': [], 'fichiers': []})
            entree['dll'].append(nom_c + '/' + f)
    for libelle, entree in out.items():
        fichiers = []
        if libelle == 'Tesseract':
            for comp, _spdx, rel, cible, requis in LICENCES:
                if (extrait / rel).is_file():
                    _copier(extrait / rel, sortie / 'licences' / cible)
                    fichiers.append('licences/' + cible)
                elif requis:
                    fichiers = []
                    break
        elif entree['dossier']:
            d = amont / entree['dossier']
            textes = sorted(p for p in d.iterdir() if p.is_file() and p.name != 'SOURCES.txt') if d.is_dir() else []
            if textes and (d / 'SOURCES.txt').is_file():
                for p in textes + [d / 'SOURCES.txt']:
                    _copier(p, sortie / 'licences' / entree['dossier'] / p.name)
                    fichiers.append('licences/%s/%s' % (entree['dossier'], p.name))
        entree['fichiers'] = fichiers
        entree['statut'] = 'trouvée' if fichiers else 'manquante'
    return out


def preparer(tiers, sortie=None, originaux=None):
    tiers = Path(tiers)
    extrait = tiers / 'extrait'
    tess, pop = extrait / 'tesseract', extrait / 'poppler' / ('poppler-%s' % POPPLER_VERSION) / 'Library' / 'bin'
    sortie = Path(sortie) if sortie else tiers / 'reduit'
    originaux = ORIGINAUX if originaux is None else originaux
    for f, attendu in originaux.items():
        if sha256(tiers / 'telechargements' / f) != attendu:
            raise SystemExit('empreinte inattendue : ' + f)
    if sortie.exists():
        shutil.rmtree(sortie)
    manifeste = {'composants': {}, 'dependances_systeme': {}, 'licences': {}, 'licences_manquantes': [], 'version_info': {},
                 'fichiers': {}, 'tessdata_commit': TESSDATA_COMMIT, 'poppler': POPPLER_VERSION, 'tesseract': '5.4.0.20240606'}
    for nom_c, src, exes in (('tesseract', tess, TESSERACT_EXE), ('poppler', pop, POPPLER_EXE)):
        fichiers, sys_ = fermeture(src, exes)
        manifeste['composants'][nom_c] = fichiers
        manifeste['dependances_systeme'][nom_c] = sys_
        for f in fichiers:
            _copier(src / f, sortie / nom_c / f)
            info = version_info((src / f).read_bytes())
            if info:
                manifeste['version_info'][nom_c + '/' + f] = info
    # tessdata : fra et eng du dépôt tessdata (originaux vérifiés), osd et pdf.ttf de l'installateur, configurations utilisées
    for f in TESSDATA:
        origine = tiers / 'telechargements' / f if f in originaux else tess / 'tessdata' / f
        _copier(origine, sortie / 'tesseract' / 'tessdata' / f)
    for f in CONFIGS:
        _copier(tess / 'tessdata' / 'configs' / f, sortie / 'tesseract' / 'tessdata' / 'configs' / f)
    manifeste['licences'] = _licences(tiers, sortie, manifeste)
    manifeste['licences_manquantes'] = ['%s (%s)' % (c, ', '.join(v['dll'])) for c, v in sorted(manifeste['licences'].items())
                                        if v['statut'] != 'trouvée']
    # sources correspondantes (Poppler, JBIG-KIT, libiconv) : vérifiées et référencées (jointes à la publication, jamais copiées ici)
    manifeste['sources'], manques_sources = {}, []
    for nom, s in SOURCES.items():
        manifeste['sources'][nom] = None
        fichier = tiers / 'telechargements' / s['nom']
        lue = version_lue(sortie, s.get('lire'))
        if lue is not None and lue != s['version']:
            manques_sources.append('source de %s non correspondante : version embarquée %s, source %s' % (nom, lue, s['version']))
        elif not fichier.is_file() or sha256(fichier) != s['sha256']:
            manques_sources.append('source de %s absente ou différente : %s' % (nom, s['nom']))
        else:
            manifeste['sources'][nom] = {k: v for k, v in s.items() if k != 'lire'}
            manifeste['sources'][nom].update(octets=fichier.stat().st_size, version_lue=lue)
    manifeste['source_poppler'] = manifeste['sources']['Poppler']
    manifeste['manques'] = list(manifeste['licences_manquantes']) + manques_sources
    manifeste['complet'] = not manifeste['manques']
    (sortie / 'licences').mkdir(parents=True, exist_ok=True)
    (sortie / 'licences' / 'mentions-des-binaires.txt').write_text(
        'Mentions relevées dans les ressources de version des binaires copiés (texte d’origine, non modifié)\n\n' +
        '\n'.join('%s : %s' % (k, ' | '.join('%s=%s' % kv for kv in sorted(v.items()))) for k, v in sorted(manifeste['version_info'].items())) + '\n',
        encoding='utf-8')
    (sortie / 'THIRD_PARTY_NOTICES-OCR.txt').write_text(avis(manifeste), encoding='utf-8')
    for p in sorted(sortie.rglob('*')):
        if p.is_file() and p.name != 'MANIFEST-ocr.json':
            manifeste['fichiers'][p.relative_to(sortie).as_posix()] = {'octets': p.stat().st_size, 'sha256': sha256(p)}
    manifeste['octets'] = sum(v['octets'] for v in manifeste['fichiers'].values())
    (sortie / 'MANIFEST-ocr.json').write_text(json.dumps(manifeste, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    return manifeste


def avis(m):
    lignes = ['AVIS DES COMPOSANTS TIERS — LECTURE DES SCANS (OCR) DE L’APPLICATION WINDOWS', '=' * 78, '',
              'Le dossier « ocr » de l’application contient des programmes tiers non modifiés :', '',
              '- Tesseract OCR %s (Apache-2.0), dossier ocr/tesseract. %s' % (m['tesseract'], SOURCES_TESSERACT),
              '- Poppler %s (GPL-2.0-or-later), dossier ocr/poppler : pdftoppm, pdftotext, pdfinfo.' % m['poppler'], '',
              'SOURCE DE POPPLER (GPL) : ' + SOURCES_POPPLER, '']
    for nom in ('JBIG-KIT', 'libiconv'):
        s = (m.get('sources') or {}).get(nom) or {k: v for k, v in SOURCES[nom].items() if k != 'lire'}
        lignes.append('SOURCE DE %s (%s) : la source correspondante est fournie avec ce binaire : fichier « %s » (SHA-256 %s)%s, joint à la '
                      'publication de cette version d’AxiorHub Pilote, à côté de l’installateur. En complément : %s. Version %s lue dans le binaire '
                      '(%s) ; la recette exacte de construction de la DLL (MSYS2, installateur UB Mannheim, correctifs éventuels) n’est pas lisible '
                      'dans les binaires : la source jointe est celle de la version amont correspondante.' % (
                          nom.upper(), s['licence'], s['nom'], s['sha256'],
                          ' avec sa signature « %s »' % s['signature'] if s.get('signature') else '', s['url'], s['version'],
                          'tesseract/libjbig-0.dll' if nom == 'JBIG-KIT' else 'tesseract/libiconv-2.dll'))
        lignes.append('')
    lignes += [
              'Licence de chaque composant (textes dans ocr/licences ; chaque dossier contient un SOURCES.txt : adresse officielle, date et '
              'SHA-256 de chaque fichier) :']
    for libelle, v in sorted(m['licences'].items()):
        lignes.append('- %s : %s — licence %s%s (%s)' % (
            libelle, v['licence'] or 'non identifiée', v['statut'],
            ' : ' + ', '.join(v['fichiers']) if v['fichiers'] else '', ', '.join(v['dll'])))
    if m['manques']:
        lignes += ['', 'MANQUES (la construction Windows n’inclut pas l’OCR tant qu’ils subsistent) :'] + ['- ' + x for x in m['manques']]
    lignes += ['', 'Mentions de copyright des binaires : ocr/licences/mentions-des-binaires.txt.',
               'Fichiers, tailles et empreintes SHA-256 : ocr/MANIFEST-ocr.json.', '']
    return '\n'.join(lignes)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    tiers = Path(argv[0]).resolve()
    avant = taille_dossier(tiers / 'extrait')
    m = preparer(tiers)
    sortie = tiers / 'reduit'
    for rel, v in sorted(m['fichiers'].items()):
        print('%10d  %s  %s' % (v['octets'], v['sha256'], rel))
    print('Taille avant (extrait) : %.1f Mo ; après (reduit) : %.1f Mo' % (avant / 1e6, taille_dossier(sortie) / 1e6))
    print('DLL système (non copiées) : ' + json.dumps(m['dependances_systeme'], ensure_ascii=False))
    print('Licences introuvables : %d composant(s) ; sources : %s ; complet : %s' % (
        len(m['licences_manquantes']), ', '.join('%s %s' % (k, 'référencée' if v else 'absente') for k, v in m['sources'].items()),
        'oui' if m['complet'] else 'non'))
    for x in m['manques']:
        print('  manque : ' + x)
    return 0


if __name__ == '__main__':
    sys.exit(main())

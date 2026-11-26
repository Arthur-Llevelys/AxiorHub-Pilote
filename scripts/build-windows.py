#!/usr/bin/env python3
"""5.6.25 : construit l'application Windows 11 d'AxiorHub Pilote (à lancer sous Windows, depuis la racine de l'arbre).

  python scripts/build-windows.py SORTIE_DIR

Prérequis de construction (non embarqués dans le dépôt) : PyInstaller 6, waitress, cryptography, pypdf, tzdata et Pillow
(pour l'icône), aux versions de scripts/windows-requirements.txt.

Résultat dans SORTIE_DIR :
- AxiorHub-Pilote-Setup-<version>.exe : installateur en un seul fichier (contient le dossier de l'application compressé) ;
- AxiorHub-Pilote-<version>-windows.zip : le même dossier, pour un déploiement sans installateur ;
- leurs sommes SHA-256 (fichiers .sha256).
5.6.106 (lot W) : si ``AXIORHUB_WIN_OCR`` désigne la copie réduite de Tesseract et Poppler (``scripts/preparer-ocr-windows.py``),
elle est vérifiée (manifeste : taille et SHA-256 de chaque fichier, aucun fichier en plus) puis copiée dans le dossier ``ocr`` à côté des
exécutables, avec ``THIRD_PARTY_NOTICES-OCR.txt`` (licences, source de Poppler jointe à la publication), **seulement si elle est
complète** (licence de chaque composant trouvée, sources de Poppler, JBIG-KIT et libiconv référencées). Sinon : construction sans OCR intégré, avec un
avertissement qui liste les manques.
5.6.110 (lot D) : si ``AXIORHUB_WIN_DICTEE`` désigne la copie ``windows`` du moteur de dictée (``scripts/preparer-dictee.py``), elle
est vérifiée (manifeste : taille et SHA-256 de chaque fichier, exactement les fichiers attendus, aucun en plus) puis copiée dans le
dossier ``dictee`` à côté des exécutables, avec ``THIRD_PARTY_NOTICES-DICTEE.txt``. Variable absente : construction sans moteur de
dictée, avec un avertissement ; copie demandée mais altérée ou incomplète : construction arrêtée.
Le dossier de l'application contient deux exécutables : « AxiorHub Pilote.exe » (fenêtre, sans console) et
« axiorhub-service.exe » (services, console masquée), tous deux en mode UTF-8.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('web.py', 'manage.py', 'poste.py', 'standalone.py', 'localpaths.py', 'docker/bootstrap.py')
DATA_DIRS = ('agent', 'templates', 'examples', 'docs', 'integrations/openwebui', 'windows', 'resources')
DATA_FILES = SCRIPTS + ('LICENSE', 'NOTICE', 'AUTHORS.md', 'THIRD_PARTY_NOTICES.md', 'LOGO-LICENSE.md', 'TRADEMARKS.md', 'README.md', 'README.en.md', 'README.de.md', 'README.es.md', 'CHANGELOG.md')
THIRD_PARTY = ('waitress', 'cryptography', 'pypdf', 'tzdata', 'pypdfium2')   # 5.6.30 : pypdfium2 = rendu des pages sans Poppler
EXCLUDES = ('gi', 'pytest', 'IPython', 'matplotlib', 'numpy', 'PIL', 'lxml', 'docx')


def version():
    text = (ROOT / 'agent' / '__init__.py').read_text(encoding='utf-8')
    return text.split("__version__")[1].split("'")[1]


def script_imports():
    """Modules de la bibliothèque standard importés par les scripts exécutés à la volée (runpy) : à embarquer explicitement."""
    names = set()
    for rel in SCRIPTS:
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
    std = set(sys.stdlib_module_names)
    return sorted(n for n in names if n.split('.')[0] in std)


def datas():
    out = []
    for rel in DATA_DIRS:
        for f in sorted((ROOT / rel).rglob('*')):
            if f.is_file() and '__pycache__' not in f.parts and f.suffix not in ('.pyc',):
                out.append((str(f), str(f.parent.relative_to(ROOT))))
    for rel in DATA_FILES:
        f = ROOT / rel
        if f.is_file():
            out.append((str(f), str(Path(rel).parent)))
    return out


def icon(build):
    from PIL import Image
    target = build / 'axiorhub.ico'
    Image.open(ROOT / 'agent' / 'static' / 'axiorhub-icon.png').convert('RGBA').save(target, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return target


APP_SPEC = '''# -*- mode: python -*-
import sys
sys.setrecursionlimit(max(sys.getrecursionlimit(), 5000))   # 5.6.100 : chaîne d'imports plus profonde (remède documenté par PyInstaller)
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs
hidden = collect_submodules('agent') + collect_submodules('windows') + {hidden!r}
for name in {third!r}:
    hidden += collect_submodules(name)
datas = {datas!r} + collect_data_files('tzdata') + collect_data_files('pypdfium2') + collect_data_files('pypdfium2_raw')
hidden += collect_submodules('pypdfium2_raw')
binaries = collect_dynamic_libs('pypdfium2_raw')
a = Analysis([{entry!r}], pathex=[{root!r}], datas=datas, binaries=binaries, hiddenimports=hidden, excludes={excludes!r}, noarchive=False)
pyz = PYZ(a.pure)
options = [('X utf8=1', None, 'OPTION')]
gui = EXE(pyz, a.scripts, options, exclude_binaries=True, name='AxiorHub Pilote', console=False, icon={icon!r}, upx=False)
svc = EXE(pyz, a.scripts, options, exclude_binaries=True, name='axiorhub-service', console=True, icon={icon!r}, upx=False)
coll = COLLECT(gui, svc, a.binaries, a.datas, upx=False, name='AxiorHub Pilote')
'''

SETUP_SPEC = '''# -*- mode: python -*-
a = Analysis([{entry!r}], pathex=[{root!r}], datas={datas!r}, hiddenimports=['windows.deploiement'], excludes={excludes!r})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [('X utf8=1', None, 'OPTION')], name={name!r}, console=False, icon={icon!r}, upx=False)
'''


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


OCR_ENV = 'AXIORHUB_WIN_OCR'
AVERTISSEMENT_OCR = ('AVERTISSEMENT : %s non défini : application construite SANS Tesseract ni Poppler intégrés. Les scans ne seront '
                     'lus que si ces outils sont installés à part sur le poste.' % OCR_ENV)
AVERTISSEMENT_INCOMPLET = ('AVERTISSEMENT : copie OCR incomplète : application construite SANS Tesseract ni Poppler intégrés '
                           '(OCR inclus seulement si toutes les licences sont trouvées et les sources de Poppler, JBIG-KIT et libiconv '
                           'référencées). Manques :')
# Sources jointes à la publication, exigées par la construction (licences GPL et LGPL) : composant → début du nom de l'archive
SOURCES_REQUISES = {'Poppler': 'poppler-', 'JBIG-KIT': 'jbigkit-', 'libiconv': 'libiconv-'}


def manques_ocr(manifeste, vus):
    """5.6.106 (lot W) : ce qui empêche d'inclure l'OCR : licence non trouvée (ou fichier de licence hors de la copie vérifiée),
    source de Poppler, JBIG-KIT ou libiconv non référencée (nom et SHA-256). Liste vide : copie complète."""
    manques = []
    licences = manifeste.get('licences')
    if not isinstance(licences, dict) or not licences:
        manques.append('licences des composants absentes du manifeste')
        licences = {}
    for nom, v in sorted(licences.items()):
        v = v if isinstance(v, dict) else {}
        fichiers = v.get('fichiers') if isinstance(v.get('fichiers'), list) else []
        if v.get('statut') != 'trouvée' or not fichiers or any(f not in vus for f in fichiers):
            manques.append('licence manquante : %s' % nom)
    for x in manifeste.get('licences_manquantes') or []:
        if 'licence manquante : ' + str(x).split(' (')[0] not in manques:
            manques.append('licence manquante : %s' % x)
    sources = manifeste.get('sources') if isinstance(manifeste.get('sources'), dict) else {}
    for nom, debut in SOURCES_REQUISES.items():
        s = sources.get(nom)
        if not isinstance(s, dict) or not str(s.get('nom') or '').startswith(debut) or len(str(s.get('sha256') or '')) != 64 \
                or not str(s.get('url') or '').startswith('https://'):
            manques.append('source de %s non référencée dans le manifeste (nom, SHA-256, adresse)' % nom)
    return manques


def inclure_ocr(app_dir, source=None):
    """5.6.106 (lot W) : copie vérifiée de la copie réduite (Tesseract, Poppler, licences) dans ``app_dir/ocr``. ``None`` si non demandée."""
    source = source if source is not None else os.environ.get(OCR_ENV, '').strip()
    if not source:
        print(AVERTISSEMENT_OCR, file=sys.stderr)
        return None
    src = Path(source).resolve()
    try:
        manifeste = json.loads((src / 'MANIFEST-ocr.json').read_text(encoding='utf-8'))
        fichiers = manifeste['fichiers']
    except (OSError, ValueError, KeyError, TypeError):
        raise SystemExit('OCR : manifeste illisible dans ' + str(src)) from None
    vus = set()
    for rel, attendu in sorted(fichiers.items()):
        chemin = Path(rel)
        if chemin.is_absolute() or '..' in chemin.parts or ':' in rel:
            raise SystemExit('OCR : chemin refusé dans le manifeste : ' + rel)
        f = src / chemin
        if not f.is_file() or f.stat().st_size != attendu.get('octets') or sha256(f) != attendu.get('sha256'):
            raise SystemExit('OCR : fichier absent ou modifié : ' + rel)
        vus.add(chemin.as_posix())
    en_plus = [p.relative_to(src).as_posix() for p in src.rglob('*') if p.is_file() and p.name != 'MANIFEST-ocr.json'
               and p.relative_to(src).as_posix() not in vus]
    if en_plus:
        raise SystemExit('OCR : fichiers hors manifeste : ' + ', '.join(sorted(en_plus)[:5]))
    for exe in ('tesseract/tesseract.exe', 'poppler/pdftoppm.exe', 'poppler/pdftotext.exe', 'poppler/pdfinfo.exe', 'THIRD_PARTY_NOTICES-OCR.txt'):
        if exe not in vus:
            raise SystemExit('OCR : élément indispensable absent : ' + exe)
    manques = manques_ocr(manifeste, vus)   # choix de l'avocat : OCR inclus seulement si complet (licences et source de Poppler)
    if manques:
        print(AVERTISSEMENT_INCOMPLET + '\n' + '\n'.join('  - ' + m for m in manques), file=sys.stderr)
        return None
    cible = Path(app_dir) / 'ocr'
    if cible.exists():
        shutil.rmtree(cible)
    shutil.copytree(src, cible)
    shutil.copyfile(src / 'THIRD_PARTY_NOTICES-OCR.txt', Path(app_dir) / 'THIRD_PARTY_NOTICES-OCR.txt')
    return {'fichiers': len(vus), 'octets': sum(v['octets'] for v in fichiers.values())}


DICTEE_ENV = 'AXIORHUB_WIN_DICTEE'
AVERTISSEMENT_DICTEE = ('AVERTISSEMENT : %s non défini : application construite SANS moteur de dictée. La dictée ne pourra pas être '
                        'installée sur les postes depuis Paramètres › Voix.' % DICTEE_ENV)
DICTEE_MANIFESTE = 'MANIFEST-dictee.json'
DICTEE_NOTICE = 'THIRD_PARTY_NOTICES-DICTEE.txt'
# Exactement ces fichiers (scripts/preparer-dictee.py, BINAIRES['windows'] et TEXTES) : ni plus, ni moins
DICTEE_FICHIERS = (('whisper-cli.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll')
                   + tuple('ggml-cpu-%s.dll' % c for c in ('alderlake', 'cannonlake', 'cascadelake', 'haswell', 'icelake', 'sandybridge',
                                                         'skylakex', 'sse42', 'x64'))
                   + ('LICENSE-whisper.cpp.txt', DICTEE_NOTICE))


def inclure_dictee(app_dir, source=None):
    """5.6.110 (lot D) : copie vérifiée du moteur de dictée (whisper.cpp, Windows) dans ``app_dir/dictee``. ``None`` si non demandée."""
    source = source if source is not None else os.environ.get(DICTEE_ENV, '').strip()
    if not source:
        print(AVERTISSEMENT_DICTEE, file=sys.stderr)
        return None
    src = Path(source).resolve()
    try:
        manifeste = json.loads((src / DICTEE_MANIFESTE).read_text(encoding='utf-8'))
        fichiers = manifeste['fichiers']
        fichiers.items()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SystemExit('Dictée : manifeste illisible dans ' + str(src)) from None
    if manifeste.get('plateforme') != 'windows':
        raise SystemExit('Dictée : copie non destinée à Windows (plateforme %r)' % manifeste.get('plateforme'))
    for rel, attendu in sorted(fichiers.items()):
        if rel != Path(rel).name or rel in ('.', '..') or ':' in rel or '\\' in rel:
            raise SystemExit('Dictée : chemin refusé dans le manifeste : ' + rel)
        f = src / rel
        if not isinstance(attendu, dict) or not f.is_file() or f.stat().st_size != attendu.get('octets') or sha256(f) != attendu.get('sha256'):
            raise SystemExit('Dictée : fichier absent ou modifié : ' + rel)
    en_plus = sorted(p.relative_to(src).as_posix() for p in src.rglob('*') if p.is_file() and p.relative_to(src).as_posix() != DICTEE_MANIFESTE
                     and p.relative_to(src).as_posix() not in fichiers)
    if en_plus:
        raise SystemExit('Dictée : fichiers hors manifeste : ' + ', '.join(en_plus[:5]))
    manque = [f for f in DICTEE_FICHIERS if f not in fichiers]
    if manque:
        raise SystemExit('Dictée : copie incomplète, élément absent : ' + ', '.join(manque))
    inattendus = sorted(set(fichiers) - set(DICTEE_FICHIERS))
    if inattendus:
        raise SystemExit('Dictée : fichiers inattendus dans la copie : ' + ', '.join(inattendus[:5]))
    cible = Path(app_dir) / 'dictee'
    if cible.exists():
        shutil.rmtree(cible)
    cible.mkdir(parents=True)
    for rel in DICTEE_FICHIERS + (DICTEE_MANIFESTE,):
        shutil.copyfile(src / rel, cible / rel)
    shutil.copyfile(src / DICTEE_NOTICE, Path(app_dir) / DICTEE_NOTICE)
    return {'fichiers': len(fichiers), 'octets': sum(v['octets'] for v in fichiers.values()), 'version': manifeste.get('version')}


def pyinstaller(spec, work, dist):
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN', '--workpath', str(work),
                    '--distpath', str(dist), str(spec)], check=True, cwd=str(ROOT))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    if sys.platform != 'win32':
        print('Construction possible seulement sous Windows.')
        return 2
    out = Path(argv[0]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ver = version()
    with tempfile.TemporaryDirectory(prefix='axiorhub-windows-') as tmp:
        build = Path(tmp)
        ico = icon(build)
        spec = build / 'application.spec'
        spec.write_text(APP_SPEC.format(hidden=script_imports(), third=list(THIRD_PARTY), datas=datas(), entry=str(ROOT / 'windows' / 'lanceur.py'),
                                        root=str(ROOT), excludes=list(EXCLUDES), icon=str(ico)), encoding='utf-8')
        pyinstaller(spec, build / 'work', build / 'dist')
        app_dir = build / 'dist' / 'AxiorHub Pilote'
        for exe in ('AxiorHub Pilote.exe', 'axiorhub-service.exe'):
            if not (app_dir / exe).is_file():
                raise SystemExit('exécutable absent : ' + exe)
        ocr = inclure_ocr(app_dir)   # 5.6.106 (lot W) : Tesseract et Poppler à côté des exécutables (dossier ocr)
        dictee = inclure_dictee(app_dir)   # 5.6.110 (lot D) : moteur de dictée whisper.cpp à côté des exécutables (dossier dictee)
        archive = out / ('AxiorHub-Pilote-%s-windows.zip' % ver)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in sorted(app_dir.rglob('*')):
                if f.is_file():
                    z.write(f, str(Path('AxiorHub Pilote') / f.relative_to(app_dir)))
        (build / 'version.txt').write_text(ver + '\n', encoding='utf-8')
        setup_name = 'AxiorHub-Pilote-Setup-' + ver
        setup_spec = build / 'installateur.spec'
        setup_spec.write_text(SETUP_SPEC.format(entry=str(ROOT / 'windows' / 'installateur.py'), root=str(ROOT), name=setup_name, icon=str(ico),
                                                datas=[(str(archive), '.'), (str(build / 'version.txt'), '.'), (str(ROOT / 'LICENSE'), '.')],
                                                excludes=list(EXCLUDES)), encoding='utf-8')
        # l'archive embarquée porte le nom attendu par l'installateur
        shutil.copyfile(archive, build / 'app.zip')
        setup_spec.write_text(setup_spec.read_text(encoding='utf-8').replace(repr(str(archive)), repr(str(build / 'app.zip'))), encoding='utf-8')
        pyinstaller(setup_spec, build / 'work-setup', out)
    setup = out / (setup_name + '.exe')
    report = {}
    for f in (setup, archive):
        digest = sha256(f)
        (f.parent / (f.name + '.sha256')).write_text('%s *%s\n' % (digest, f.name), encoding='utf-8')
        report[f.name] = {'octets': f.stat().st_size, 'sha256': digest}
    report['ocr'] = ocr or 'non inclus'
    report['dictee'] = dictee or 'non inclus'
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

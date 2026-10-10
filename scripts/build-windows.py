#!/usr/bin/env python3
"""5.6.25 : construit l'application Windows 11 d'AxiorHub Pilote (à lancer sous Windows, depuis la racine de l'arbre).

  python scripts/build-windows.py SORTIE_DIR

Prérequis de construction (non embarqués dans le dépôt) : PyInstaller 6, waitress, cryptography, pypdf, tzdata et Pillow
(pour l'icône), aux versions de scripts/windows-requirements.txt.

Résultat dans SORTIE_DIR :
- AxiorHub-Pilote-Setup-<version>.exe : installateur en un seul fichier (contient le dossier de l'application compressé) ;
- AxiorHub-Pilote-<version>-windows.zip : le même dossier, pour un déploiement sans installateur ;
- leurs sommes SHA-256 (fichiers .sha256).
Le dossier de l'application contient deux exécutables : « AxiorHub Pilote.exe » (fenêtre, sans console) et
« axiorhub-service.exe » (services, console masquée), tous deux en mode UTF-8.
"""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('web.py', 'manage.py', 'poste.py', 'standalone.py', 'localpaths.py', 'docker/bootstrap.py')
DATA_DIRS = ('agent', 'templates', 'examples', 'docs', 'integrations/openwebui', 'windows')
DATA_FILES = SCRIPTS + ('LICENSE', 'NOTICE', 'AUTHORS.md', 'THIRD_PARTY_NOTICES.md', 'LOGO-LICENSE.md', 'TRADEMARKS.md', 'README.md', 'CHANGELOG.md')
THIRD_PARTY = ('waitress', 'cryptography', 'pypdf', 'tzdata')
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
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
hidden = collect_submodules('agent') + collect_submodules('windows') + {hidden!r}
for name in {third!r}:
    hidden += collect_submodules(name)
datas = {datas!r} + collect_data_files('tzdata')
a = Analysis([{entry!r}], pathex=[{root!r}], datas=datas, hiddenimports=hidden, excludes={excludes!r}, noarchive=False)
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
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Construit l'archive de livraison d'AxiorHub et la contrôle AVANT livraison avec la fonction de vérification de l'installateur.

  python3 scripts/build-release.py SORTIE.tar.gz [--modes-from ARCHIVE_PRECEDENTE.tar.gz]

- aucun fichier compilé (__pycache__, .pyc) ni aperçu de test n'entre dans l'archive ni dans le manifeste ;
- MANIFEST.sha256 est régénéré ; la feuille de style unique doit être à jour (scripts/build-css.py) ;
- l'archive produite est extraite dans un dossier temporaire et contrôlée par ``upgrade.verify`` : telle quelle, puis après
  ajout de fichiers .pyc comme en crée Python sur le serveur ; le moindre écart arrête la construction ;
- les droits d'exécution sont repris de l'archive précédente lorsqu'elle est fournie (nouveaux fichiers : 0644).
"""
import argparse
import hashlib
import io
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {'__pycache__', '.git', '.pytest_cache'}
EXCLUDE_FILES = {'MANIFEST.sha256', 'tests/preview_mt.py'}
TEXT = {'.py', '.css', '.js', '.md', '.json', '.sh', '.txt', '.yml', '.php', '.mjs', '.conf', '.service', '.timer', '.sample', '.sql', '.example'}


def version():
    return re.search(r"__version__\s*=\s*'([^']+)'", (ROOT / 'agent' / '__init__.py').read_text(encoding='utf-8')).group(1)


def files():
    out = {}
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT).as_posix()
        if not path.is_file() or path.is_symlink() or set(path.relative_to(ROOT).parts) & EXCLUDE_DIRS or rel in EXCLUDE_FILES or rel.endswith('.pyc'):
            continue
        data = path.read_bytes()
        if path.suffix.lower() in TEXT:
            data = data.replace(b'\r\n', b'\n')
        out[rel] = data
    return out


def check_css():
    sys.path.insert(0, str(ROOT / 'scripts'))
    sys.path.insert(0, str(ROOT))
    import importlib.util
    spec = importlib.util.spec_from_file_location('build_css', ROOT / 'scripts' / 'build-css.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8') != mod.build():
        raise SystemExit('ARRÊT : app520.css n’est pas à jour ; exécutez python3 scripts/build-css.py')


def build(out_path, modes_from=None):
    check_css()
    name = 'axiorhub-mail-agent-' + version()
    content = files()
    bad = [r for r in content if '__pycache__' in r or r.endswith('.pyc')]
    if bad:
        raise SystemExit('ARRÊT : fichier compilé dans le paquet : ' + bad[0])
    content['MANIFEST.sha256'] = ''.join('%s  %s\n' % (hashlib.sha256(content[r]).hexdigest(), r) for r in sorted(content)).encode()
    modes = {}
    if modes_from:
        with tarfile.open(modes_from) as ref:
            for m in ref.getmembers():
                if m.isfile():
                    modes[m.name.split('/', 1)[-1]] = m.mode
    stamp = int(time.time())
    dirs = sorted({'/'.join(r.split('/')[:i]) for r in content for i in range(1, r.count('/') + 1)})
    with tarfile.open(out_path, 'w:gz', format=tarfile.PAX_FORMAT) as tar:
        for d in [''] + dirs:
            info = tarfile.TarInfo(name + ('/' + d if d else ''))
            info.type, info.mode, info.mtime, info.uname, info.gname = tarfile.DIRTYPE, 0o755, stamp, 'root', 'root'
            tar.addfile(info)
        for rel in sorted(content):
            info = tarfile.TarInfo(name + '/' + rel)
            info.size, info.mode, info.mtime, info.uname, info.gname = len(content[rel]), modes.get(rel, 0o644), stamp, 'root', 'root'
            tar.addfile(info, io.BytesIO(content[rel]))
    verify(out_path, name)
    digest = hashlib.sha256(Path(out_path).read_bytes()).hexdigest()
    Path(str(out_path) + '.sha256').write_bytes(('%s  %s\n' % (digest, Path(out_path).name)).encode())   # LF même sous Windows (sha256sum -c)
    print('%s : %d fichiers, contrôle installateur réussi, SHA-256 %s' % (Path(out_path).name, len(content), digest))


def verify(out_path, name):
    sys.path.insert(0, str(ROOT))
    import upgrade
    with tempfile.TemporaryDirectory(prefix='axiorhub-release-') as td:
        with tarfile.open(out_path) as tar:
            for m in tar.getmembers():
                if m.name.startswith('/') or '..' in m.name.split('/') or not (m.isfile() or m.isdir()):
                    raise SystemExit('ARRÊT : entrée d’archive refusée : ' + m.name)
            if hasattr(tarfile, 'data_filter'):
                tar.extractall(td, filter='data')
            else:
                tar.extractall(td)
        root = Path(td) / name
        upgrade.verify(root)
        for where in ('__pycache__', 'agent/__pycache__'):
            (root / where).mkdir(parents=True, exist_ok=True)
            (root / where / 'simulation.cpython-313.pyc').write_bytes(b'x')
        upgrade.verify(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Construction contrôlée de l’archive AxiorHub')
    parser.add_argument('sortie')
    parser.add_argument('--modes-from', default='')
    a = parser.parse_args()
    build(a.sortie, a.modes_from or None)

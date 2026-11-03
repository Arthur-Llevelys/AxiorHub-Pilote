#!/usr/bin/env python3
"""Construit l'interface Carbon depuis le fichier de verrouillage : ``agent/static/carbon5682.js``, ``agent/ui_icones5689.py``, police Plex Sans.

Node et npm servent ici seulement : l'application n'en a jamais besoin à l'exécution, et aucun CDN n'est employé. Le dossier ``node_modules`` reste hors du dépôt.
``npm ci --ignore-scripts`` n'exécute aucun script d'installation : la télémétrie de ``@ibm/telemetry-js``, dépendance de ``@carbon/web-components``, ne se lance jamais.

Usage : ``python scripts/build-carbon.py [--travail DOSSIER]`` puis, pour contrôler, ``python scripts/build-carbon.py --verifier`` (empreintes des fichiers embarqués).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts' / 'carbon5689'
FICHIERS = ('package.json', 'package-lock.json', 'entry.js', 'build.mjs', 'icones.json')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verifier():
    """Compare les fichiers du dépôt au manifeste : une retouche à la main ou une construction oubliée se voit."""
    manifest = json.loads((SOURCE / 'manifeste.json').read_text(encoding='utf-8'))
    bad = [name for name, sha in manifest['sorties'].items() if not (ROOT / name).is_file() or _sha(ROOT / name) != sha]
    for name in bad:
        print('écart :', name)
    return not bad


def construire(travail):
    travail = Path(travail)
    travail.mkdir(parents=True, exist_ok=True)
    for name in FICHIERS:
        shutil.copyfile(SOURCE / name, travail / name)
    npm = shutil.which('npm') or 'npm'
    node = shutil.which('node') or 'node'
    env = {**os.environ, 'CARBON_TELEMETRY_DISABLED': '1', 'IBM_TELEMETRY_DISABLED': 'true', 'NPM_CONFIG_AUDIT': 'false', 'NPM_CONFIG_FUND': 'false', 'NPM_CONFIG_UPDATE_NOTIFIER': 'false'}
    subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=travail, env=env, check=True)
    subprocess.run([node, 'build.mjs', str(ROOT)], cwd=travail, env=env, check=True)
    shutil.copyfile(travail / 'manifeste.json', SOURCE / 'manifeste.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--travail', default='', help='dossier de construction, hors du dépôt (temporaire par défaut)')
    parser.add_argument('--verifier', action='store_true', help='contrôle les empreintes, sans construire')
    args = parser.parse_args()
    if args.verifier:
        sys.exit(0 if verifier() else 1)
    if args.travail:
        construire(args.travail)
    else:
        with tempfile.TemporaryDirectory(prefix='carbon5689') as tmp:
            construire(tmp)
    print('Construction terminée ; contrôle :', 'conforme' if verifier() else 'ÉCART')


if __name__ == '__main__':
    main()

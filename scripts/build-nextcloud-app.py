#!/usr/bin/env python3
"""Paquet de diffusion de l'application Nextcloud AxiorHub Pilote : une archive tar.gz contenant UN SEUL dossier « axiorhub_pilote »
(appinfo, lib, templates, l10n, img, actifs compilés js/ et css/, README, CHANGELOG, licences du code et du logo). Exclus : sources de développement (src/),
node_modules, tests, caches et tout fichier de secret. Les actifs compilés doivent exister (npm ci && npm run build).
La signature (certificat du responsable du projet, clé privée hors dépôt) se fait ensuite avec « occ integrity:sign-app ».

Usage : python3 scripts/build-nextcloud-app.py dist/axiorhub_pilote-0.1.0.tar.gz
"""
import gzip
import hashlib
import io
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'nextcloud-app' / 'axiorhub_pilote'
INCLUDE = ('appinfo', 'lib', 'templates', 'l10n', 'img', 'js', 'css')
FILES = ('README.md', 'README.en.md', 'CHANGELOG.md')
SECRET = re.compile(r'(secret|password|token|\.key|\.pem)$', re.I)


def build(target):
    version = re.search(r'<version>([^<]+)</version>', (APP / 'appinfo' / 'info.xml').read_text(encoding='utf-8')).group(1)
    js = APP / 'js'
    if not js.is_dir() or not any(js.iterdir()):
        raise SystemExit('ARRÊT : actifs compilés absents (npm ci && npm run build dans nextcloud-app/axiorhub_pilote)')
    members = []
    for name in INCLUDE:
        base = APP / name
        if base.is_dir():
            members += sorted(p for p in base.rglob('*') if p.is_file())
    members += [APP / f for f in FILES if (APP / f).is_file()]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.PAX_FORMAT) as tar:
        for path in members:
            rel = path.relative_to(APP).as_posix()
            if SECRET.search(path.name) or '__pycache__' in rel:
                raise SystemExit('ARRÊT : fichier refusé dans le paquet : ' + rel)
            data = path.read_bytes()
            info = tarfile.TarInfo('axiorhub_pilote/' + rel)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
        for name in ('LICENSE', 'LOGO-LICENSE.md'):   # 0.4.0 : licence propre du logo, livré dans img/
            lic = (ROOT / name).read_bytes()
            info = tarfile.TarInfo('axiorhub_pilote/' + name)
            info.size, info.mode, info.mtime = len(lic), 0o644, 0
            tar.addfile(info, io.BytesIO(lic))
    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(raw.getvalue(), mtime=0))
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_name(out.name + '.sha256').write_text('%s  %s\n' % (digest, out.name), encoding='utf-8', newline='\n')
    print('axiorhub_pilote %s : %s (%d fichiers) sha256 %s' % (version, out, len(members) + 2, digest))


if __name__ == '__main__':
    build(sys.argv[1] if len(sys.argv) > 1 else 'dist/axiorhub_pilote.tar.gz')

#!/usr/bin/env python3
"""Construit le paquet Debian d'AxiorHub Pilote à partir d'une archive de livraison déjà contrôlée, sans dpkg ni Linux.

  python3 scripts/build-deb.py SORTIE.deb --archive axiorhub-mail-agent-X.Y.Z.tar.gz

- le paquet « axiorhub-pilote » transporte l'archive telle quelle (/usr/share/axiorhub-pilote/) : le script de
  post-installation reprend install.sh (installation neuve ou mise à niveau cumulative), les versions précédentes restent
  sous /opt/axiorhub-mail-agent/releases et le retour arrière reste possible ;
- apt apporte les dépendances système (python3, python3-cryptography, poppler, tesseract, LibreOffice, minisign, eSpeak NG) ;
- l'archive est extraite dans un dossier temporaire et contrôlée par ``upgrade.verify`` avant d'être empaquetée ;
- le paquet produit est relu (format ar, control, md5sums, droits) et, si dpkg-deb est présent, contrôlé par lui.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

PACKAGE = 'axiorhub-pilote'
MAINTAINER = 'Timo RAINIO <101706410+Arthur-Llevelys@users.noreply.github.com>'
HOMEPAGE = 'https://github.com/Arthur-Llevelys/AxiorHub-Pilote'
DEPENDS = ('python3 (>= 3.10)', 'python3-venv', 'python3-cryptography', 'poppler-utils', 'tesseract-ocr', 'tesseract-ocr-fra',
           'libreoffice-writer', 'minisign', 'espeak-ng', 'systemd')
RECOMMENDS = ('apache2',)
SCRIPTS = ('postinst', 'prerm', 'postrm')
DESCRIPTION = ('Agent IA local pour cabinet d\'avocat : courriels, dossiers, actes et agenda',
               'AxiorHub Pilote lit la messagerie et les dossiers du cabinet, prépare brouillons, projets d\'actes, bordereaux,',
               'échéances et tâches, sans rien envoyer ni signer. Le paquet installe ou met à niveau la version sous',
               '/opt/axiorhub-mail-agent et conserve configuration, secrets, données et versions précédentes.')


def version_of(archive):
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.name.endswith('/agent/__init__.py') and member.name.count('/') == 2:
                text = tar.extractfile(member).read().decode('utf-8')
                return re.search(r"__version__\s*=\s*'([^']+)'", text).group(1)
    raise RuntimeError('Version introuvable dans l’archive.')


def extract_verified(archive, destination):
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk() or member.name.startswith('/') or '..' in Path(member.name).parts:
                raise RuntimeError('Archive refusée : ' + member.name)
        if hasattr(tarfile, 'data_filter'):
            tar.extractall(destination, filter='data')
        else:
            tar.extractall(destination)
    inner = [p for p in Path(destination).iterdir()]
    if len(inner) != 1 or not inner[0].is_dir():
        raise RuntimeError('Archive inattendue : un seul dossier de version attendu.')
    spec = importlib.util.spec_from_file_location('axiorhub_upgrade_check', inner[0] / 'upgrade.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify(inner[0])
    return inner[0]


def control_text(version, installed_kib):
    lines = ['Package: ' + PACKAGE, 'Version: ' + version, 'Section: misc', 'Priority: optional', 'Architecture: all',
             'Maintainer: ' + MAINTAINER, 'Installed-Size: ' + str(installed_kib), 'Depends: ' + ', '.join(DEPENDS),
             'Recommends: ' + ', '.join(RECOMMENDS), 'Homepage: ' + HOMEPAGE, 'Description: ' + DESCRIPTION[0]]
    lines += [' ' + line for line in DESCRIPTION[1:]]
    return '\n'.join(lines) + '\n'


def _tar_bytes(entries, mtime):
    """entries : liste de (chemin './x/y', données bytes ou None pour un dossier, mode). Propriétaire root, horodatage fixe."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.GNU_FORMAT) as tar:
        for name, data, mode in entries:
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            info.mtime = mtime
            info.mode = mode
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    raw = buffer.getvalue()
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', mtime=mtime) as gz:
        gz.write(raw)
    return out.getvalue()


def _ar(members):
    out = bytearray(b'!<arch>\n')
    for name, data, mtime in members:
        header = (name.ljust(16) + str(int(mtime)).ljust(12) + '0'.ljust(6) + '0'.ljust(6) + '100644'.ljust(8)
                  + str(len(data)).ljust(10) + '`\n').encode('ascii')
        if len(header) != 60:
            raise RuntimeError('En-tête ar invalide.')
        out += header + data
        if len(data) % 2:
            out += b'\n'
    return bytes(out)


def build(output, archive, mtime=None):
    archive = Path(archive)
    version = version_of(archive)
    with tempfile.TemporaryDirectory() as tmp:
        tree = extract_verified(archive, tmp)
        templates = tree / 'deploy' / 'debian'
        scripts = {}
        for name in SCRIPTS:
            scripts[name] = (templates / name).read_text(encoding='utf-8').replace('@VERSION@', version).encode('utf-8')
            if not scripts[name].startswith(b'#!/usr/bin/python3'):
                raise RuntimeError('Script de maintenance sans interpréteur : ' + name)
        copyright_text = (templates / 'copyright').read_bytes()
        changelog = ('axiorhub-pilote (' + version + ') stable; urgency=medium\n\n  * Voir CHANGELOG.md de la version.\n\n -- '
                     + MAINTAINER + '  ' + time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime(mtime or archive.stat().st_mtime)) + '\n').encode('utf-8')
    if mtime is None:
        mtime = int(os.environ.get('SOURCE_DATE_EPOCH') or archive.stat().st_mtime)
    payload = archive.read_bytes()
    payload_name = 'axiorhub-mail-agent-' + version + '.tar.gz'
    data_files = [('./usr/share/axiorhub-pilote/' + payload_name, payload, 0o644),
                  ('./usr/share/doc/axiorhub-pilote/copyright', copyright_text, 0o644),
                  ('./usr/share/doc/axiorhub-pilote/changelog.Debian', changelog, 0o644)]
    data_entries = [('./', None, 0o755), ('./usr/', None, 0o755), ('./usr/share/', None, 0o755), ('./usr/share/axiorhub-pilote/', None, 0o755),
                    ('./usr/share/doc/', None, 0o755), ('./usr/share/doc/axiorhub-pilote/', None, 0o755)] + data_files
    installed_kib = max(1, sum(len(d) for _, d, _ in data_files) // 1024 + 1)
    md5sums = ''.join(hashlib.md5(data).hexdigest() + '  ' + name[2:] + '\n' for name, data, _ in data_files)
    control_entries = [('./', None, 0o755), ('./control', control_text(version, installed_kib).encode('utf-8'), 0o644),
                       ('./md5sums', md5sums.encode('ascii'), 0o644)]
    control_entries += [('./' + name, scripts[name], 0o755) for name in SCRIPTS]
    deb = _ar([('debian-binary', b'2.0\n', mtime), ('control.tar.gz', _tar_bytes(control_entries, mtime), mtime),
               ('data.tar.gz', _tar_bytes(data_entries, mtime), mtime)])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(deb)
    check(output, payload)
    return {'package': PACKAGE, 'version': version, 'path': str(output), 'size': len(deb), 'sha256': hashlib.sha256(deb).hexdigest()}


def read_deb(path):
    """Relit un paquet : membres ar, champs de control, scripts (mode), fichiers de données (mode, uid, gid, taille), md5sums."""
    raw = Path(path).read_bytes()
    if not raw.startswith(b'!<arch>\n'):
        raise RuntimeError('Pas un paquet Debian (en-tête ar absent).')
    pos, members = 8, []
    while pos + 60 <= len(raw):
        header = raw[pos:pos + 60]
        if header[58:60] != b'`\n':
            raise RuntimeError('En-tête ar corrompu.')
        name = header[:16].decode('ascii').rstrip()
        size = int(header[48:58].decode('ascii').strip())
        members.append((name, raw[pos + 60:pos + 60 + size]))
        pos += 60 + size + (size % 2)
    names = [m[0] for m in members]
    if names != ['debian-binary', 'control.tar.gz', 'data.tar.gz'] or members[0][1] != b'2.0\n':
        raise RuntimeError('Membres du paquet inattendus : ' + ', '.join(names))

    def entries(blob):
        out = {}
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tar:
            for member in tar.getmembers():
                data = tar.extractfile(member).read() if member.isfile() else None
                out[member.name] = {'mode': member.mode, 'uid': member.uid, 'gid': member.gid, 'size': member.size,
                                    'dir': member.isdir(), 'data': data, 'type': member.type}
        return out
    control = entries(members[1][1])
    data = entries(members[2][1])
    fields = {}
    for line in control['./control']['data'].decode('utf-8').splitlines():
        if line.startswith(' '):
            fields[last] += '\n' + line
        else:
            last, _, value = line.partition(': ')
            fields[last] = value
    md5 = {}
    for line in control['./md5sums']['data'].decode('ascii').splitlines():
        digest, _, name = line.partition('  ')
        md5[name] = digest
    return {'fields': fields, 'control': control, 'data': data, 'md5sums': md5}


def check(path, payload=None):
    info = read_deb(path)
    for name, digest in info['md5sums'].items():
        entry = info['data'].get('./' + name)
        if entry is None or hashlib.md5(entry['data']).hexdigest() != digest:
            raise RuntimeError('md5sums ne correspond pas : ' + name)
    for name, entry in list(info['data'].items()) + list(info['control'].items()):
        if entry['uid'] or entry['gid'] or entry['mode'] & 0o022 or entry['type'] not in (tarfile.REGTYPE, tarfile.DIRTYPE):
            raise RuntimeError('Entrée refusée (propriétaire, droits ou type) : ' + name)
    for name in info['data']:   # tarfile relit les dossiers sans barre finale ('.', './usr', './usr/share')
        if not name.startswith('./usr/share/') and name not in ('.', './usr', './usr/share'):
            raise RuntimeError('Fichier hors /usr/share : ' + name)
    for name in SCRIPTS:
        if info['control']['./' + name]['mode'] != 0o755:
            raise RuntimeError('Script de maintenance non exécutable : ' + name)
    payload_entry = next(v for k, v in info['data'].items() if k.endswith('.tar.gz'))
    if payload is not None and payload_entry['data'] != payload:
        raise RuntimeError('La charge utile diffère de l’archive.')
    if shutil.which('dpkg-deb'):
        subprocess.run(['dpkg-deb', '--info', str(path)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(['dpkg-deb', '--contents', str(path)], check=True, stdout=subprocess.DEVNULL)
    return info


def main():
    parser = argparse.ArgumentParser(description='Paquet Debian AxiorHub Pilote')
    parser.add_argument('output')
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    result = build(args.output, args.archive)
    print('%s : %s %s, %d octets, SHA-256 %s' % (result['path'], result['package'], result['version'], result['size'], result['sha256']))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        sys.exit('Construction interrompue : ' + str(exc))

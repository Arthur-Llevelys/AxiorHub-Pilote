#!/usr/bin/env python3
"""Construit les paquets Debian d'AxiorHub Pilote à partir d'une archive de livraison déjà contrôlée, sans dpkg ni Linux.

  python3 scripts/build-deb.py SORTIE.deb --archive axiorhub-mail-agent-X.Y.Z.tar.gz [--variant serveur|poste]

- variante « serveur » (défaut, paquet « axiorhub-pilote », Architecture all) : transporte l'archive telle quelle
  (/usr/share/axiorhub-pilote/) ; le script de post-installation reprend install.sh (installation neuve ou mise à niveau
  cumulative), les versions précédentes restent sous /opt/axiorhub-mail-agent/releases et le retour arrière reste possible ;
- variante « poste » (5.6.20, paquet « axiorhub-pilote-poste », Architecture amd64) : arbre complet déployé sous
  /usr/lib/axiorhub-pilote/current, lanceur /usr/bin/axiorhub-pilote, entrée de menu, icône et unité systemd utilisateur ;
  aucun script de maintenance, les données vivent dans le dossier personnel (poste.py) ;
- apt apporte les dépendances système ; l'archive est extraite dans un dossier temporaire et contrôlée par ``upgrade.verify`` ;
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
VARIANTS = {
    'serveur': {'package': PACKAGE, 'architecture': 'all', 'depends': DEPENDS, 'recommends': RECOMMENDS, 'description': DESCRIPTION},
    'poste': {'package': PACKAGE + '-poste', 'architecture': 'amd64',
              'depends': ('python3 (>= 3.10)', 'python3-waitress', 'python3-cryptography', 'python3-gi', 'gir1.2-gtk-3.0', 'gir1.2-webkit2-4.1',
                          'poppler-utils', 'tesseract-ocr', 'tesseract-ocr-fra', 'libreoffice-writer', 'espeak-ng', 'xdg-utils'),
              'recommends': ('gir1.2-ayatanaappindicator3-0.1',),
              'description': ('Agent IA local pour cabinet d\'avocat, application de bureau Ubuntu',
                              'AxiorHub Pilote sur un poste de travail : fenêtre WebKitGTK, services locaux (interface sur 127.0.0.1,',
                              'worker, veille IMAP, passages périodiques), données et configuration dans le dossier personnel.',
                              'Lancer « AxiorHub Pilote » depuis le menu, ou « axiorhub-pilote » ; services seuls : systemctl --user',
                              'enable --now axiorhub-pilote. Ollama et Kokoro s\'installent séparément.')},
}
POSTE_ROOT = './usr/lib/axiorhub-pilote/current/'


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


def control_text(version, installed_kib, variant):
    v = VARIANTS[variant]
    lines = ['Package: ' + v['package'], 'Version: ' + version, 'Section: misc', 'Priority: optional', 'Architecture: ' + v['architecture'],
             'Maintainer: ' + MAINTAINER, 'Installed-Size: ' + str(installed_kib), 'Depends: ' + ', '.join(v['depends']),
             'Recommends: ' + ', '.join(v['recommends']), 'Homepage: ' + HOMEPAGE, 'Description: ' + v['description'][0]]
    lines += [' ' + line for line in v['description'][1:]]
    return '\n'.join(lines) + '\n'


def _with_dirs(files):
    """Ajoute les entrées de dossier (./a/, ./a/b/) nécessaires aux fichiers donnés, dans l'ordre."""
    dirs = []
    for name, _, _ in files:
        parts = name[2:].split('/')[:-1]
        for i in range(1, len(parts) + 1):
            d = './' + '/'.join(parts[:i]) + '/'
            if d not in dirs:
                dirs.append(d)
    return [('./', None, 0o755)] + [(d, None, 0o755) for d in sorted(dirs)] + list(files)


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


def build(output, archive, mtime=None, variant='serveur'):
    if variant not in VARIANTS:
        raise RuntimeError('Variante inconnue : ' + str(variant))
    archive = Path(archive)
    version = version_of(archive)
    package = VARIANTS[variant]['package']
    if mtime is None:
        mtime = int(os.environ.get('SOURCE_DATE_EPOCH') or archive.stat().st_mtime)
    scripts, data_files = {}, []
    with tempfile.TemporaryDirectory() as tmp:
        tree = extract_verified(archive, tmp)
        templates = tree / 'deploy' / 'debian'
        copyright_text = (templates / 'copyright').read_bytes()
        if variant == 'serveur':
            for name in SCRIPTS:
                scripts[name] = (templates / name).read_text(encoding='utf-8').replace('@VERSION@', version).encode('utf-8')
                if not scripts[name].startswith(b'#!/usr/bin/python3'):
                    raise RuntimeError('Script de maintenance sans interpréteur : ' + name)
            data_files.append(('./usr/share/axiorhub-pilote/axiorhub-mail-agent-' + version + '.tar.gz', archive.read_bytes(), 0o644))
        else:
            poste = tree / 'deploy' / 'poste'
            for path in sorted(p for p in tree.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'):
                data_files.append((POSTE_ROOT + path.relative_to(tree).as_posix(), path.read_bytes(), 0o644))
            launcher = (poste / 'axiorhub-pilote').read_bytes()
            if not launcher.startswith(b'#!/bin/sh'):
                raise RuntimeError('Lanceur sans interpréteur.')
            data_files += [('./usr/bin/axiorhub-pilote', launcher, 0o755),
                           ('./usr/share/applications/axiorhub-pilote.desktop', (poste / 'axiorhub-pilote.desktop').read_bytes(), 0o644),
                           ('./usr/share/icons/hicolor/512x512/apps/axiorhub-pilote.png', (tree / 'agent' / 'static' / 'axiorhub-icon-512.png').read_bytes(), 0o644),
                           ('./usr/lib/systemd/user/axiorhub-pilote.service', (poste / 'axiorhub-pilote.service').read_bytes(), 0o644)]
    changelog = (package + ' (' + version + ') stable; urgency=medium\n\n  * Voir CHANGELOG.md de la version.\n\n -- '
                 + MAINTAINER + '  ' + time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime(mtime)) + '\n').encode('utf-8')
    data_files += [('./usr/share/doc/' + package + '/copyright', copyright_text, 0o644),
                   ('./usr/share/doc/' + package + '/changelog.Debian', changelog, 0o644)]
    data_entries = _with_dirs(data_files)
    installed_kib = max(1, sum(len(d) for _, d, _ in data_files) // 1024 + 1)
    md5sums = ''.join(hashlib.md5(data).hexdigest() + '  ' + name[2:] + '\n' for name, data, _ in data_files)
    control_entries = [('./', None, 0o755), ('./control', control_text(version, installed_kib, variant).encode('utf-8'), 0o644),
                       ('./md5sums', md5sums.encode('ascii'), 0o644)]
    control_entries += [('./' + name, scripts[name], 0o755) for name in SCRIPTS if name in scripts]
    deb = _ar([('debian-binary', b'2.0\n', mtime), ('control.tar.gz', _tar_bytes(control_entries, mtime), mtime),
               ('data.tar.gz', _tar_bytes(data_entries, mtime), mtime)])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(deb)
    check(output, archive.read_bytes() if variant == 'serveur' else None)
    return {'package': package, 'version': version, 'variant': variant, 'path': str(output), 'size': len(deb), 'sha256': hashlib.sha256(deb).hexdigest()}


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
    fields, last = {}, None
    for line in control['./control']['data'].decode('utf-8').splitlines():
        if line.startswith(' ') and last:
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
    poste = info['fields'].get('Package', '').endswith('-poste')
    for name, digest in info['md5sums'].items():
        entry = info['data'].get('./' + name)
        if entry is None or hashlib.md5(entry['data']).hexdigest() != digest:
            raise RuntimeError('md5sums ne correspond pas : ' + name)
    for name, entry in list(info['data'].items()) + list(info['control'].items()):
        if entry['uid'] or entry['gid'] or entry['mode'] & 0o022 or entry['type'] not in (tarfile.REGTYPE, tarfile.DIRTYPE):
            raise RuntimeError('Entrée refusée (propriétaire, droits ou type) : ' + name)
    allowed = ('./usr/lib/axiorhub-pilote/', './usr/bin/', './usr/share/', './usr/lib/systemd/user/') if poste else ('./usr/share/',)
    roots = {'.', './usr', './usr/share', './usr/lib', './usr/lib/systemd', './usr/bin'}
    for name, entry in info['data'].items():   # tarfile relit les dossiers sans barre finale
        probe = name + ('/' if entry['dir'] else '')
        if not probe.startswith(allowed) and name not in roots:
            raise RuntimeError('Fichier hors des emplacements admis : ' + name)
        if not entry['dir'] and name[2:] not in info['md5sums']:
            raise RuntimeError('Fichier absent de md5sums : ' + name)
    if poste:
        for required in (POSTE_ROOT + 'poste.py', POSTE_ROOT + 'MANIFEST.sha256', './usr/bin/axiorhub-pilote',
                         './usr/share/applications/axiorhub-pilote.desktop', './usr/lib/systemd/user/axiorhub-pilote.service'):
            if required not in info['data']:
                raise RuntimeError('Fichier du paquet poste absent : ' + required)
        if info['data']['./usr/bin/axiorhub-pilote']['mode'] != 0o755:
            raise RuntimeError('Lanceur non exécutable.')
        if any('./' + s in info['control'] for s in SCRIPTS):
            raise RuntimeError('Le paquet poste ne doit pas avoir de script de maintenance.')
    else:
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
    parser = argparse.ArgumentParser(description='Paquets Debian AxiorHub Pilote')
    parser.add_argument('output')
    parser.add_argument('--archive', required=True)
    parser.add_argument('--variant', choices=sorted(VARIANTS), default='serveur')
    args = parser.parse_args()
    result = build(args.output, args.archive, variant=args.variant)
    print('%s : %s %s (%s), %d octets, SHA-256 %s' % (result['path'], result['package'], result['version'], result['variant'], result['size'], result['sha256']))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        sys.exit('Construction interrompue : ' + str(exc))

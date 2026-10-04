#!/usr/bin/env python3
"""Signed stable/test channel updater used by ``axiorhub update``."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen

from agent.common import load_config
from agent.desk import Desk
from agent.update420 import policy as update_policy


def _download(url, target, maximum=500_000_000):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname:raise RuntimeError('URL de mise à jour HTTPS requise.')
    with urlopen(url,timeout=60) as response,open(target,'wb') as output:
        total=0
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            total+=len(chunk)
            if total>maximum:raise RuntimeError('Archive de mise à jour trop volumineuse.')
            output.write(chunk)


def _safe_extract(archive, destination):
    with tarfile.open(archive,'r:gz') as bundle:
        members=bundle.getmembers()
        if not members:raise RuntimeError('Archive vide.')
        for item in members:
            path=Path(item.name)
            if path.is_absolute() or '..' in path.parts or item.issym() or item.islnk():
                raise RuntimeError('Archive de mise à jour ambiguë.')
        bundle.extractall(destination,filter='data')
    roots={Path(x.name).parts[0] for x in members if Path(x.name).parts}
    if len(roots)!=1:raise RuntimeError('L’archive doit contenir un seul dossier de version.')
    return destination/next(iter(roots))


def _verify_signature(archive, signature, public_key):
    if not shutil.which('minisign'):raise RuntimeError('Installer minisign avant une mise à jour signée.')
    if not public_key.is_file():raise RuntimeError('Clé publique Minisign absente : '+str(public_key))
    if not signature.is_file():raise RuntimeError('Signature Minisign absente.')
    subprocess.run(['minisign','-Vm',str(archive),'-x',str(signature),'-p',str(public_key)],check=True)


def update(archive_arg=''):
    cfg=load_config('/etc/axiorhub-mail-agent/config.json');settings=update_policy(Desk(cfg))
    temporary=tempfile.TemporaryDirectory(prefix='axiorhub-update-');root=Path(temporary.name)
    try:
        if archive_arg:
            archive=Path(archive_arg).resolve();signature=Path(str(archive)+'.minisig')
        else:
            metadata_url=str(settings.get('metadata_url') or '')
            if not metadata_url:raise RuntimeError('Canal non configuré. Indiquez une archive : sudo axiorhub update /chemin/version.tar.gz')
            metadata_file=root/'release.json';_download(metadata_url,metadata_file,1_000_000)
            metadata=json.loads(metadata_file.read_text())
            channel=str(settings.get('channel') or 'stable')
            release=metadata.get(channel) or {}
            archive=root/'release.tar.gz';signature=root/'release.tar.gz.minisig'
            _download(str(release.get('archive_url') or ''),archive)
            _download(str(release.get('signature_url') or ''),signature,100_000)
            expected=str(release.get('sha256') or '').lower()
            if expected and hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
                raise RuntimeError('Empreinte de l’archive invalide.')
        if not archive.is_file():raise RuntimeError('Archive de mise à jour absente.')
        if settings.get('require_signature',True):
            _verify_signature(archive,signature,Path(settings.get('minisign_public_key_file') or '/etc/axiorhub-mail-agent/update-minisign.pub'))
        source=_safe_extract(archive,root/'source')
        subprocess.run(['/usr/bin/python3',str(source/'installer.py')],check=True)
    finally:temporary.cleanup()


if __name__=='__main__':
    if len(sys.argv)>2:raise SystemExit('Usage : sudo axiorhub update [archive.tar.gz]')
    try:update(sys.argv[1] if len(sys.argv)==2 else '')
    except (RuntimeError,subprocess.CalledProcessError,ValueError,OSError) as exc:
        raise SystemExit(str(exc)) from None

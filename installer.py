#!/usr/bin/env python3
"""Install AxiorHub 5.6.14 from one archive, on a new or existing server."""
import argparse
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import uuid

import upgrade


VERSION = '5.6.14'
BASE = Path('/opt/axiorhub-mail-agent')
CONFIG_DIR = Path('/etc/axiorhub-mail-agent')
STATE = Path('/var/lib/axiorhub-mail-agent')
LAUNCHER = Path('/usr/local/bin/axiorhub-mail')
SYSTEMD = Path('/etc/systemd/system')
SERVICE_USER = 'axiorhub-mail'
SERVICE_GROUP = 'axiorhub-mail'
CORE_UNITS = (
    'axiorhub-mail-agent.service', 'axiorhub-mail-agent.timer',
    'axiorhub-mail-agent-cleanup.service', 'axiorhub-mail-agent-cleanup.timer',
    'axiorhub-mail-ui.service', 'axiorhub-mail-desk-worker.service',
    'axiorhub-mail-watch.service','axiorhub-mail-desk-worker@.service',
)


def installation_state(base=BASE, config_dir=CONFIG_DIR):
    current = base/'current'
    config = config_dir/'config.json'
    if current.is_symlink() and config.is_file(): return 'upgrade'
    if not current.exists() and not current.is_symlink() and not config.exists(): return 'fresh'
    return 'partial'


def _copy_release(source, base, uid=0, gid=0):
    upgrade.verify(source)
    for path in source.rglob('*'):
        if path.is_symlink(): raise RuntimeError('Lien symbolique dans le paquet refusé.')
    releases=base/'releases';releases.mkdir(parents=True,exist_ok=True)
    release=releases/VERSION
    if release.exists():
        upgrade.verify(release)
        if (release/'MANIFEST.sha256').read_bytes() != (source/'MANIFEST.sha256').read_bytes():
            raise RuntimeError('Une autre version 5.6.14 existe déjà.')
        return release
    staging=releases/('.'+VERSION+'-'+uuid.uuid4().hex)
    try:
        shutil.copytree(source,staging,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for path in [staging,*staging.rglob('*')]:
            os.chown(path,uid,gid)
            os.chmod(path,0o755 if path.is_dir() else 0o644)
        upgrade.verify(staging)
        os.rename(staging,release)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    return release


def provision_files(source, base=BASE, config_dir=CONFIG_DIR, state=STATE,
                    launcher=LAUNCHER, systemd=SYSTEMD, service_uid=0,
                    service_gid=0, root_uid=0, root_gid=0):
    """Filesystem-only fresh install; separated so it can be fully tested."""
    if installation_state(base,config_dir) != 'fresh':
        raise RuntimeError('Installation partielle détectée : arrêt sans écrasement.')
    base.mkdir(parents=True,exist_ok=True);os.chmod(base,0o755)
    release=_copy_release(source,base,root_uid,root_gid)
    config_dir.mkdir(parents=True,exist_ok=True);os.chown(config_dir,root_uid,service_gid);os.chmod(config_dir,0o750)
    secrets_dir=config_dir/'secrets';secrets_dir.mkdir(exist_ok=True);os.chown(secrets_dir,root_uid,service_gid);os.chmod(secrets_dir,0o750)
    state.mkdir(parents=True,exist_ok=True);os.chown(state,service_uid,service_gid);os.chmod(state,0o700)
    launcher.parent.mkdir(parents=True,exist_ok=True)
    upgrade.atomic(launcher,(release/'deploy'/'axiorhub-mail').read_bytes(),root_uid,root_gid,0o755)
    systemd.mkdir(parents=True,exist_ok=True)
    for unit in CORE_UNITS:
        upgrade.atomic(systemd/unit,(release/'deploy'/unit).read_bytes(),root_uid,root_gid,0o644)
    upgrade.switch(base,release)
    return release


def _ensure_account():
    try: group=grp.getgrnam(SERVICE_GROUP)
    except KeyError:
        subprocess.run(['groupadd','--system',SERVICE_GROUP],check=True)
        group=grp.getgrnam(SERVICE_GROUP)
    try: user=pwd.getpwnam(SERVICE_USER)
    except KeyError:
        subprocess.run(['useradd','--system','--gid',SERVICE_GROUP,
          '--home-dir',str(STATE),'--no-create-home','--shell','/usr/sbin/nologin',SERVICE_USER],check=True)
        user=pwd.getpwnam(SERVICE_USER)
    if user.pw_gid != group.gr_gid:
        raise RuntimeError('Le compte axiorhub-mail existe avec un autre groupe principal.')
    return user.pw_uid,group.gr_gid


def _install_dependencies():
    subprocess.run(['apt-get','update'],check=True)
    subprocess.run(['apt-get','install','-y','--no-install-recommends',
      'python3','python3-venv','poppler-utils','tesseract-ocr','tesseract-ocr-fra',
      'libreoffice-writer','minisign','python3-cryptography','espeak-ng'],check=True)


def fresh_install(source):
    _install_dependencies();uid,gid=_ensure_account()
    release=provision_files(source,service_uid=uid,service_gid=gid)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    print('Installation autonome 5.6.14 terminée. Aucun secret fictif n’a été créé.')
    print('Étape suivante : sudo axiorhub-mail configure')
    print('Puis : sudo python3 '+str(release/'install-interface.py'))
    print('Enfin : sudo systemctl enable --now axiorhub-mail-agent.timer axiorhub-mail-agent-cleanup.timer')


def _version_key(path):
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)',path.name)
    return tuple(map(int,match.groups())) if match else (-1,-1,-1)


def prune_old_releases(base=BASE, keep=2):
    """Delete only verified inactive releases, preserving active and rollback targets."""
    current=base/'current'
    if not current.is_symlink(): raise RuntimeError('Version active introuvable.')
    active=current.resolve();releases=(base/'releases').resolve()
    if active.parent != releases: raise RuntimeError('Lien current hors du registre des releases.')
    keep=max(2,int(keep));protected={active}
    receipt=base/('upgrade-'+active.name+'.json')
    if receipt.is_file():
        try:
            previous=Path(json.loads(receipt.read_text())['previous_release']).resolve()
            if previous.parent==releases: protected.add(previous)
        except (KeyError,ValueError,OSError):
            raise RuntimeError('Reçu de retour arrière invalide : aucun nettoyage effectué.') from None
    rows=sorted((p for p in releases.iterdir() if p.is_dir() and _version_key(p)!=( -1,-1,-1)),
                key=_version_key,reverse=True)
    protected.update(rows[:keep]);removed=[];skipped=[]
    for path in rows:
        if path.resolve() in protected: continue
        try: upgrade.verify(path)
        except RuntimeError:
            skipped.append(path.name);continue
        shutil.rmtree(path);removed.append(path.name)
    return {'removed':removed,'skipped_unverified':skipped,
            'preserved':sorted(p.name for p in protected)}


def main():
    parser=argparse.ArgumentParser(description='Installation cumulative AxiorHub 5.6.14')
    parser.add_argument('--prune-old-releases',action='store_true')
    parser.add_argument('--keep',type=int,default=2)
    args=parser.parse_args()
    if os.geteuid()!=0: raise SystemExit('Exécuter : sudo bash install.sh')
    os.umask(0o077);source=Path(__file__).resolve().parent
    if args.prune_old_releases:
        print(json.dumps(prune_old_releases(keep=args.keep),ensure_ascii=False,indent=2));return
    mode=installation_state()
    if mode=='fresh': fresh_install(source);return
    if mode=='partial': raise SystemExit('Installation AxiorHub partielle détectée. Aucun fichier n’a été modifié.')
    os.execv('/usr/bin/python3',['/usr/bin/python3',str(source/'upgrade.py')])


if __name__=='__main__':
    try: main()
    except (RuntimeError,subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None

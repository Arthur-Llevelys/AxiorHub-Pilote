#!/usr/bin/env python3
"""Install an isolated Roundcube taskbar link after strict local checks."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from localpaths import roundcube_root
import shutil
import subprocess
import tempfile
import uuid

PLUGIN='axiorhub_mail_agent'
MARKER_PREFIX='// AxiorHub Agent IA button'
MARKER='// AxiorHub Agent IA button 3.1.0'
KNOWN_PREVIOUS={
    'axiorhub_mail_agent.css':'6427f6f4a3ce7d8ebea4d762af3c162cec5d41c5c2e18327648b464edafb9781',
    'axiorhub_mail_agent.php':'8fd018a674f980915467d5afa3a14d70908c1f477ec270bda50384384e55ff26',
}
KNOWN_290={
    'axiorhub_mail_agent.css':'7f4490a160714fe4e71525266b39de40a0dc7b009eed2ed578e4c3ae44fc3928',
    'axiorhub_mail_agent.js':'72a099da1f4e038a77cea15af0de060080e71f5d709e04979f03c4242c06459b',
    'axiorhub_mail_agent.php':'b2e7fa5a2bdecd7b197860d22aa53ce3e566a8f0ae632c6d2ed94f9125c88ac7',
}


def activation(data):
    if b'\x00' in data or len(data)>2_000_000:
        raise RuntimeError('Configuration Roundcube illisible ou trop volumineuse.')
    if MARKER_PREFIX.encode() in data:
        return data
    snippet=(
        "\n"+MARKER+"\n"
        "if (!isset($config['plugins']) || !is_array($config['plugins'])) { $config['plugins'] = []; }\n"
        "if (!in_array('"+PLUGIN+"', $config['plugins'], true)) { $config['plugins'][] = '"+PLUGIN+"'; }\n"
    ).encode()
    closing=data.rfind(b'?>')
    if closing>=0 and not data[closing+2:].strip():
        return data[:closing]+snippet+data[closing:]
    return data+snippet


def same_tree(a,b):
    left=sorted(p.relative_to(a) for p in a.rglob('*') if p.is_file())
    right=sorted(p.relative_to(b) for p in b.rglob('*') if p.is_file())
    return left==right and all((a/p).read_bytes()==(b/p).read_bytes() for p in left)


def tree_hashes(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def atomic(path,data,stat):
    fd,name=tempfile.mkstemp(prefix=path.name+'.new-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            (os.fchown(f.fileno(),stat.st_uid,stat.st_gid) if hasattr(os, 'fchown') else None)
            (os.fchmod(f.fileno(),stat.st_mode & 0o777) if hasattr(os, 'fchmod') else None)
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def install(root,source,backups,lint=True):
    root=root.resolve();config=root/'config/config.inc.php';plugins=root/'plugins'
    if not config.is_file() or config.is_symlink() or not plugins.is_dir():
        raise RuntimeError('Installation Roundcube attendue introuvable : '+str(root))
    if not source.is_dir() or source.is_symlink():
        raise RuntimeError('Extension Roundcube absente du paquet.')
    target=plugins/PLUGIN
    previous=False
    if target.exists():
        if not target.is_dir() or target.is_symlink():
            raise RuntimeError('Une extension '+PLUGIN+' différente existe déjà : arrêt sans écrasement.')
        previous=tree_hashes(target) in (KNOWN_PREVIOUS,KNOWN_290)
        if not same_tree(source,target) and not previous:
            raise RuntimeError('Une extension '+PLUGIN+' modifiée ou inconnue existe déjà : arrêt sans écrasement.')
    before=config.read_bytes();after=activation(before)
    if lint:
        subprocess.run(['php','-l',str(source/(PLUGIN+'.php'))],check=True,
                       stdout=subprocess.DEVNULL)
        with tempfile.NamedTemporaryFile(dir=config.parent,suffix='.php',delete=False) as f:
            f.write(after);candidate=Path(f.name)
        try:subprocess.run(['php','-l',str(candidate)],check=True,stdout=subprocess.DEVNULL)
        finally:candidate.unlink(missing_ok=True)
    installed=False;updated=False;plugin_backup=None
    if not target.exists() or previous:
        staging=plugins/('.'+PLUGIN+'-'+uuid.uuid4().hex)
        shutil.copytree(source,staging,copy_function=shutil.copyfile)
        for path in [staging,*staging.rglob('*')]:
            os.chmod(path,0o755 if path.is_dir() else 0o644)
        if previous:
            backups.mkdir(parents=True,exist_ok=True,mode=0o700)
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
            plugin_backup=backups/('roundcube-plugin-before-3.1.0-'+stamp)
            shutil.copytree(target,plugin_backup,copy_function=shutil.copyfile)
            for path in [plugin_backup,*plugin_backup.rglob('*')]:
                os.chmod(path,0o700 if path.is_dir() else 0o600)
            old=plugins/('.'+PLUGIN+'-old-'+uuid.uuid4().hex)
            os.rename(target,old)
            try:os.rename(staging,target)
            except Exception:
                os.rename(old,target);shutil.rmtree(staging,ignore_errors=True);raise
            shutil.rmtree(old);updated=True
        else:
            os.rename(staging,target);installed=True
    backup=None
    if after!=before:
        backups.mkdir(parents=True,exist_ok=True,mode=0o700)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        backup=backups/('roundcube-config-before-agent-button-'+stamp+'.php')
        backup.write_bytes(before);os.chmod(backup,0o600)
        try:atomic(config,after,config.stat())
        except Exception:
            if installed:shutil.rmtree(target)
            raise
    return {'extension':'mise_a_jour' if updated else ('installee' if installed else 'deja_presente'),
            'activation':'ajoutee' if after!=before else 'deja_presente',
            'sauvegarde':str(backup) if backup else '',
            'sauvegarde_extension':str(plugin_backup) if plugin_backup else ''}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--roundcube-root',default=str(roundcube_root()))
    args=parser.parse_args()
    if os.geteuid()!=0:raise RuntimeError('Exécuter avec sudo python3 install-roundcube-button.py')
    here=Path(__file__).resolve().parent
    result=install(Path(args.roundcube_root),here/'integrations/roundcube'/PLUGIN,
                   Path('/etc/axiorhub-mail-agent/upgrade-backups'))
    subprocess.run(['systemctl','reload','apache2'],check=True)
    print('Bouton Agent IA Roundcube : '+result['extension']+', activation '+result['activation']+'.')
    if result['sauvegarde']:print('Configuration Roundcube sauvegardée : '+result['sauvegarde'])
    if result['sauvegarde_extension']:print('Ancienne extension Roundcube sauvegardée : '+result['sauvegarde_extension'])
    print('Le bouton ouvre https://courriel.example.com/agent-courriel/accueil')
    print('Apache rechargé ; actualiser Roundcube avec Ctrl+F5.')
    print('Il apparaît aussi dans /mail lorsque cette page affiche ce même Roundcube.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as ex:
        raise SystemExit(str(ex)) from None

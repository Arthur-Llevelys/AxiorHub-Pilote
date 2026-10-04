#!/usr/bin/env python3
"""Install the dedicated UI on an existing HTTPS hostname, without editing vhosts."""
import getpass
from datetime import datetime,timezone
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys

from upgrade import verify, atomic

BASE=Path('/opt/axiorhub-mail-agent/current')
AUTH=Path('/etc/axiorhub-mail-agent/ui-auth.json')
API_TOKEN=Path('/etc/axiorhub-mail-agent/openwebui-api-token')
CONF=Path('/etc/apache2/conf-available/axiorhub-mail-ui.conf')
UNITS=('axiorhub-mail-ui.service','axiorhub-mail-desk-worker.service','axiorhub-mail-watch.service','axiorhub-mail-desk-worker@2.service')
VENV=Path('/opt/axiorhub-mail-agent/ui-venv')


def apache_config(host):
    if not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}',host):
        raise RuntimeError('Nom de domaine invalide.')
    return f'''# AxiorHub Mail Agent — dédié à ce chemin et ce nom HTTPS.
RedirectMatch 302 ^/agent-courriel$ /agent-courriel/
<Location "/agent-courriel/">
    Require expr "%{{HTTPS}} == 'on' && %{{HTTP_HOST}} == '{host}'"
    ProxyPreserveHost On
    ProxyPass "http://127.0.0.1:8769/" connectiontimeout=5 timeout=240
    ProxyPassReverse "http://127.0.0.1:8769/"
    RequestHeader set X-Forwarded-Proto "https"
    Header always unset Permissions-Policy
    Header always set Permissions-Policy "camera=(), microphone=(self), geolocation=()"
</Location>
'''


def apache_config_151(host):
    return f'''# AxiorHub Mail Agent — dédié à ce chemin et ce nom HTTPS.
RedirectMatch 302 ^/agent-courriel$ /agent-courriel/
<Location "/agent-courriel/">
    Require expr "%{{HTTPS}} == 'on' && %{{HTTP_HOST}} == '{host}'"
    ProxyPreserveHost On
    ProxyPass "http://127.0.0.1:8769/" connectiontimeout=5 timeout=60
    ProxyPassReverse "http://127.0.0.1:8769/"
    RequestHeader set X-Forwarded-Proto "https"
</Location>
'''


def run(*args):
    subprocess.run(args,check=True)


def main():
    if os.geteuid()!=0:raise RuntimeError('Exécuter avec sudo python3 install-interface.py')
    os.umask(0o077)
    verify(BASE.resolve())
    supported={'3.5.0','3.5.1','3.6.0','3.6.1','3.6.2','3.6.3','3.6.4','3.6.5','3.7.0','3.8.0','3.8.1','3.9.0','3.9.1','3.9.2','3.9.3','4.0.0','4.1.0','4.1.1','4.2.0','4.3.0','4.3.1','4.4.0','4.5.0','4.6.0','4.7.0','4.8.0','4.9.0','5.0.0','5.0.1','5.1.0','5.2.0','5.2.1','5.3.0','5.4.0','5.5.0','5.6.0','5.6.1'}
    if BASE.resolve().name not in supported:
        raise RuntimeError('Version active non prise en charge par l’installateur d’interface.')
    existing=json.loads(AUTH.read_text()) if AUTH.exists() else None
    if existing:
        host=existing['origin'].removeprefix('https://')
    else:
        host=input('Nom HTTPS existant pour ouvrir l’interface [courriel.example.com] : ').strip().lower() or 'courriel.example.com'
    config=apache_config(host).encode()
    dump=subprocess.run(['apache2ctl','-S'],capture_output=True,text=True,check=True)
    if host not in dump.stdout+dump.stderr:
        raise RuntimeError('Ce nom ne figure pas dans les hôtes Apache. Choisir un nom HTTPS déjà configuré.')
    run('apache2ctl','configtest')
    old_conf=CONF.read_bytes() if CONF.exists() else None
    if old_conf not in (None,config,apache_config_151(host).encode(),config.replace(b'microphone=(self)',b'microphone=()').replace(b'timeout=240',b'timeout=60')):
        raise RuntimeError('La configuration dédiée a été modifiée : arrêt sans écrasement.')
    for unit in UNITS:
        bundled=unit.replace('@2.','@.')
        target=Path('/etc/systemd/system')/bundled
        if target.exists() and target.read_bytes()!=(BASE/'deploy'/bundled).read_bytes():
            raise RuntimeError('Une unité dédiée existe avec un autre contenu : '+unit)
    if not existing:
        with socket.socket() as s:
            try:s.bind(('127.0.0.1',8769))
            except OSError:raise RuntimeError('Le port local 8769 est déjà utilisé.') from None
        password=getpass.getpass('Nouveau mot de passe de l’interface (16 caractères minimum) : ')
        if len(password)<16:raise RuntimeError('Mot de passe trop court.')
        if password!=getpass.getpass('Répétez le mot de passe : '):raise RuntimeError('Mots de passe différents.')
        salt=secrets.token_hex(16)
        existing={'username':'admin','salt':salt,
                  'hash':hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex(),
                  'csrf':secrets.token_urlsafe(32),'origin':'https://'+host,'prefix':'/agent-courriel'}
    # Runtime code must be readable/executable by the unprivileged service user.
    os.umask(0o022)
    if not (VENV/'bin/python').exists():
        run('apt-get','update')
        run('apt-get','install','-y','--no-install-recommends','python3-venv')
        run('/usr/bin/python3','-m','venv',str(VENV))
    check=subprocess.run([str(VENV/'bin/python'),'-c',
        "from importlib.metadata import version; assert version('waitress')=='3.0.2'"],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if check.returncode:
        run(str(VENV/'bin/python'),'-m','pip','install','waitress==3.0.2')
    os.umask(0o077)
    run('a2enmod','proxy','proxy_http','headers','alias')
    api_created=False
    if not API_TOKEN.exists():
        token=secrets.token_urlsafe(48);atomic(API_TOKEN,(token+'\n').encode(),0,0,0o600);api_created=True
    else:
        token=API_TOKEN.read_text().strip()
        if len(token)<40:raise RuntimeError('Jeton API local invalide : arrêt sans remplacement.')
    expected_hash=hashlib.sha256(token.encode()).hexdigest()
    if existing.get('api_token_sha256')!=expected_hash:
        existing['api_token_sha256']=expected_hash
        atomic(AUTH,(json.dumps(existing)+'\n').encode(),0,grp.getgrnam('axiorhub-mail').gr_gid,0o640)
    elif not AUTH.exists():
        atomic(AUTH,(json.dumps(existing)+'\n').encode(),0,grp.getgrnam('axiorhub-mail').gr_gid,0o640)
    changed_conf=old_conf!=config
    conf_backup=None
    if old_conf is not None and changed_conf:
        backups=Path('/etc/axiorhub-mail-agent/upgrade-backups');backups.mkdir(mode=0o700,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        conf_backup=backups/('apache-ui-before-3.2.0-'+stamp+'.conf')
        atomic(conf_backup,old_conf,0,0,0o600)
    if changed_conf:atomic(CONF,config,0,0,0o644)
    enabled=Path('/etc/apache2/conf-enabled/axiorhub-mail-ui.conf').exists()
    try:
        run('a2enconf','axiorhub-mail-ui')
        run('apache2ctl','configtest')
    except subprocess.CalledProcessError:
        if not enabled:subprocess.run(['a2disconf','axiorhub-mail-ui'],check=False)
        if changed_conf:
            if old_conf is None:CONF.unlink()
            else:atomic(CONF,old_conf,0,0,0o644)
        raise RuntimeError('Test Apache échoué. Nouvelle configuration retirée ; Apache non rechargé.') from None
    for unit in UNITS:
        bundled=unit.replace('@2.','@.')
        target=Path('/etc/systemd/system')/bundled
        if not target.exists():atomic(target,(BASE/'deploy'/bundled).read_bytes(),0,0,0o644)
    run('systemctl','daemon-reload')
    run('systemctl','enable','--now',*UNITS)
    run('systemctl','is-active',*UNITS)
    # 5.6.1 : les minuteries d'analyse et de nettoyage périodiques sont réactivées à chaque installation (elles restaient arrêtées si la
    # commande de redémarrage n'avait pas été lancée après la mise à jour).
    timers=[t for t in ('axiorhub-mail-agent.timer','axiorhub-mail-agent-cleanup.timer') if Path('/etc/systemd/system/'+t).exists()]
    if timers:subprocess.run(['systemctl','enable','--now',*timers],check=False)
    run('systemctl','reload','apache2')
    print('Interface installée : https://'+host+'/agent-courriel/')
    print('Identifiant : admin — mot de passe défini ci-dessus.')
    print('La découverte démarre en arrière-plan. Les associations proposées restent à confirmer.')
    print('API AxiorHub : https://'+host+'/agent-courriel/api/v1/openapi.json')
    print('Jeton Open WebUI conservé dans '+str(API_TOKEN)+' (lecture root uniquement).')
    if api_created:print('Nouveau jeton créé ; utilisez install-openwebui-tool.py pour afficher les étapes de connexion.')
    if conf_backup:print('Ancienne configuration Apache sauvegardée : '+str(conf_backup))


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as ex:
        sys.exit(str(ex))

#!/usr/bin/env python3
"""Install the audited Roundcube V8 bridge to the common AxiorHub AI API."""
from datetime import datetime,timezone
import grp
import json
import os
from pathlib import Path
from localpaths import roundcube_root
import re
import shutil
import subprocess
import sys


SOURCE=Path(__file__).resolve().parent/'integrations/roundcube/ai_roundcube_assistant_v8'
PLUGIN=roundcube_root()/'plugins'/'ai_roundcube_assistant'
AXIORHUB_TOKEN=Path('/etc/axiorhub-mail-agent/openwebui-api-token')
ROUND_TOKEN=Path('/etc/roundcube/axiorhub-api.token')
UI_AUTH=Path('/etc/axiorhub-mail-agent/ui-auth.json')


def run(*args):subprocess.run(args,check=True)


def main():
    if os.geteuid()!=0:raise RuntimeError('Exécuter : sudo python3 install-roundcube-ai-bridge.py')
    if not PLUGIN.is_dir() or not (PLUGIN/'config.inc.php').is_file():
        raise RuntimeError('Plugin ai_roundcube_assistant ou sa configuration introuvable.')
    for name in ('ai_roundcube_assistant.php','ai_roundcube_assistant.js','ai_roundcube_assistant.css'):
        if not (SOURCE/name).is_file():raise RuntimeError('Source Roundcube 3.9.3 absente : '+name)
    if not AXIORHUB_TOKEN.is_file():
        raise RuntimeError('Jeton API AxiorHub absent. Exécutez d’abord : sudo python3 install-interface.py')
    if not UI_AUTH.is_file():
        raise RuntimeError('Configuration de l’interface AxiorHub absente. Exécutez d’abord install-interface.py.')
    token=AXIORHUB_TOKEN.read_text().strip()
    if len(token)<40 or '\n' in token or '\r' in token:raise RuntimeError('Jeton API AxiorHub invalide.')
    php='php8.4' if shutil.which('php8.4') else ('php' if shutil.which('php') else '')
    if not php:raise RuntimeError('PHP absent.')
    run(php,'-l',str(SOURCE/'ai_roundcube_assistant.php'))
    if shutil.which('node'):run('node','--check',str(SOURCE/'ai_roundcube_assistant.js'))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=Path('/root')/('ai-roundcube-assistant-before-axiorhub-3.9.3-'+stamp)
    shutil.copytree(PLUGIN,backup,symlinks=True)
    os.chmod(backup,0o700)
    try:
        ROUND_TOKEN.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
        fd=os.open(ROUND_TOKEN.with_suffix('.token.new'),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o640)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(token+'\n');stream.flush();os.fsync(stream.fileno())
        temporary=ROUND_TOKEN.with_suffix('.token.new')
        os.chown(temporary,0,grp.getgrnam('www-data').gr_gid);os.chmod(temporary,0o640)
        os.replace(temporary,ROUND_TOKEN)
        owner=(PLUGIN/'ai_roundcube_assistant.php').stat()
        auth=json.loads(UI_AUTH.read_text())
        endpoint=auth['origin'].rstrip('/')+auth.get('prefix','/agent-courriel')+'/api/v1/ai/chat/completions'
        plugin_config=PLUGIN/'config.inc.php'
        config_text=plugin_config.read_text()
        marker=re.compile(r"(\$config\['ai_axiorhub_api_url'\]\s*=\s*)'[^']*'\s*;")
        if marker.search(config_text):
            config_text=marker.sub(lambda match:match.group(1)+"'"+endpoint+"';",config_text,count=1)
        else:
            config_text+="\n$config['ai_axiorhub_api_url'] = '"+endpoint+"';\n"
        staging_config=plugin_config.with_name('config.inc.php.new-381')
        staging_config.write_text(config_text);os.chown(staging_config,owner.st_uid,owner.st_gid)
        os.chmod(staging_config,plugin_config.stat().st_mode & 0o777);os.replace(staging_config,plugin_config)
        for name in ('ai_roundcube_assistant.php','ai_roundcube_assistant.js','ai_roundcube_assistant.css'):
            target=PLUGIN/name
            staging=target.with_name(target.name+'.new-364')
            shutil.copyfile(SOURCE/name,staging)
            os.chown(staging,owner.st_uid,owner.st_gid);os.chmod(staging,0o644);os.replace(staging,target)
        run(php,'-l',str(PLUGIN/'ai_roundcube_assistant.php'))
        if shutil.which('node'):run('node','--check',str(PLUGIN/'ai_roundcube_assistant.js'))
    except Exception:
        for name in ('ai_roundcube_assistant.php','ai_roundcube_assistant.js','ai_roundcube_assistant.css'):
            if (backup/name).is_file():shutil.copy2(backup/name,PLUGIN/name)
        if (backup/'config.inc.php').is_file():shutil.copy2(backup/'config.inc.php',PLUGIN/'config.inc.php')
        raise RuntimeError('Installation interrompue ; fichiers Roundcube restaurés depuis '+str(backup)) from None
    print('Relais Roundcube → API IA AxiorHub installé.')
    print('Sauvegarde : '+str(backup))
    print('Jeton serveur : '+str(ROUND_TOKEN)+' (root:www-data, 0640)')
    print('API commune : '+endpoint)
    print('Rechargez Roundcube sans cache puis testez Résumer et Actions.')


if __name__=='__main__':
    try:main()
    except (RuntimeError,subprocess.CalledProcessError) as error:sys.exit(str(error))

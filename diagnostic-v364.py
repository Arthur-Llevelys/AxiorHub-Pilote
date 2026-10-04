#!/usr/bin/env python3
"""Diagnostic de configuration AxiorHub 3.6.4, sans afficher aucun secret."""
import json
import os
from pathlib import Path
from localpaths import roundcube_root
import sqlite3
from urllib.parse import quote


CONFIG=Path('/etc/axiorhub-mail-agent/config.json')
CURRENT=Path('/opt/axiorhub-mail-agent/current')


def mode(path):
    try:return oct(Path(path).stat().st_mode & 0o777)
    except OSError:return None


def secret_status(path):
    if not path:return 'non_configure'
    target=Path(path)
    if not target.is_file():return 'absent'
    permissions=target.stat().st_mode & 0o777
    return 'ok' if permissions & 0o027 == 0 else 'permissions_trop_larges_'+oct(permissions)


def rows(state):
    path=state/'desk.sqlite3'
    if not path.is_file():return {}
    db=sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro',uri=True)
    try:
        return {key:json.loads(value) for key,value in db.execute(
          "SELECT key,value FROM settings WHERE key LIKE 'ai:%' OR key LIKE 'lawve:item:%'")}
    finally:db.close()


def main():
    config=json.loads(CONFIG.read_text());settings=rows(Path(config['state_dir']))
    providers={'ollama':{**config.get('ollama',{}),'type':'ollama','enabled':True}}
    providers.update(config.get('ai_providers') or {})
    for key,value in settings.items():
        if key.startswith('ai:provider:'):providers[key.split(':',2)[-1]]=value
    public_providers={}
    for identifier,value in providers.items():
        public_providers[identifier]={'type':value.get('type','ollama'),'enabled':value.get('enabled',True),
          'url':value.get('url',''),'model':value.get('model',''),
          'external_data_allowed':value.get('external_data_allowed',False),
          'secret':secret_status(value.get('secret_file','')) if value.get('type')!='ollama' else 'local_sans_cle',
          'last_test':settings.get('ai:health:'+identifier,{})}
    routes={key.split(':',2)[-1]:value for key,value in settings.items() if key.startswith('ai:route:')}
    extensions={}
    for key,value in settings.items():
        if not key.startswith('lawve:item:'):continue
        identifier=key.split(':',2)[-1];test=value.get('last_test') or {}
        extensions[identifier]={'kind':value.get('kind'),'status':value.get('status'),
          'enabled':value.get('enabled',False),'source_url':value.get('source_url',''),
          'endpoint':value.get('endpoint',''),'auth_type':value.get('auth_type','none'),
          'external_data_allowed':value.get('external_data_allowed',False),
          'secret':secret_status(value.get('secret_file','')),
          'archive_present':bool(value.get('archive_path') and Path(value['archive_path']).is_file()),
          'archive_sha256':value.get('archive_sha256',''),'purposes':value.get('purposes',[]),
          'last_test':{k:test.get(k) for k in ('status','message','error','tools_count','latency_ms','at') if k in test}}
    roundcube=roundcube_root()/'plugins'/'ai_roundcube_assistant'
    token=Path('/etc/roundcube/axiorhub-api.token')
    report={'version':'3.6.4','active_release':str(CURRENT.resolve()) if CURRENT.exists() else 'absente',
      'providers':public_providers,'routes':routes,'lawve_extensions':extensions,
      'roundcube_bridge':{'php_present':(roundcube/'ai_roundcube_assistant.php').is_file(),
        'js_present':(roundcube/'ai_roundcube_assistant.js').is_file(),
        'token':secret_status(token),'token_mode':mode(token)},
      'guarantees':{'secret_values_printed':False,'external_prompt_sent':False,
                    'mcp_tool_executed':False,'plugin_script_executed':False}}
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

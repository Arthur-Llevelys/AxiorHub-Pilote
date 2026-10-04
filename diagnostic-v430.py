#!/usr/bin/env python3
"""Read-only metadata diagnostic; never displays prompts, passwords or bodies."""
import argparse
import json
from pathlib import Path
from agent.common import load_config, Stop
from agent.desk import Desk
from agent.live430 import snapshot

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='/etc/axiorhub-mail-agent/config.json')
    args=parser.parse_args()
    try:
        desk=Desk(load_config(args.config))
        try:
            data=snapshot(desk)
            report={'version':'4.3.1','mode':data['mode'],'mail_drafts_enabled':data['mail_drafts_enabled'],
              'surveillance_enabled':desk.settings('live430:enabled',True),
              'services':data['services'],'pending':data['pending'],'running':data['running'],
              'incidents':[{'job_id':j['id'],'kind':j['kind'],'error':j['error'],'code':j.get('error_code','')}
                for j in data['jobs'] if j['status']=='error'],
              'migration_430_applied':bool(desk.db.execute('SELECT 1 FROM schema_migrations WHERE id=430').fetchone()),
              'migration_431_applied':bool(desk.db.execute('SELECT 1 FROM schema_migrations WHERE id=431').fetchone()),
              'blocked':[{'job_id':j['id'],'kind':j['kind']} for j in data['jobs'] if j.get('blocked')],
              'network_calls':False,'secrets_exposed':False}
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:desk.db.close()
    except (OSError,Stop):
        print(json.dumps({'version':'4.3.1','status':'unavailable',
          'message':'Configuration ou base inaccessible. Vérifiez les permissions et utilisez le compte du service.'},ensure_ascii=False));raise SystemExit(2)

if __name__=='__main__':main()

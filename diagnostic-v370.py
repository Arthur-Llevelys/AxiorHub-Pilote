#!/usr/bin/env python3
"""Diagnostic lisible de la couche 3.7.0, sans valeur de secret ni contenu métier."""
import json
from pathlib import Path
import sqlite3

CONFIG=Path('/etc/axiorhub-mail-agent/config.json')
CURRENT=Path('/opt/axiorhub-mail-agent/current')


def main():
    config=json.loads(CONFIG.read_text());db_path=Path(config['state_dir'])/'desk.sqlite3'
    report={'version':'3.7.0','active_release':str(CURRENT.resolve()) if CURRENT.exists() else 'absente',
      'assistant_transversal':{'css':(CURRENT/'agent/static/v370.css').is_file(),
        'javascript':(CURRENT/'agent/static/v370.js').is_file()},
      'docker':{'compose':(CURRENT/'docker-compose.yml').is_file(),
        'dockerfile':(CURRENT/'docker/Dockerfile').is_file(),'port':8626},
      'mail_rules':0,'approved_corrections':0,'quality_reviews':{},
      'automation_level':'assisted','second_model_control':True,
      'guarantees':{'secret_values_printed':False,'mail_sent':False,'filing_executed':False,
        'external_action_authorized_by_level':False}}
    if db_path.is_file():
        db=sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True)
        try:
            tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'mail_rules_v370' in tables:
                report['mail_rules']=db.execute('SELECT COUNT(*) FROM mail_rules_v370 WHERE enabled=1').fetchone()[0]
            if 'correction_examples_v370' in tables:
                report['approved_corrections']=db.execute('SELECT COUNT(*) FROM correction_examples_v370 WHERE enabled=1').fetchone()[0]
            if 'quality_reviews_v370' in tables:
                report['quality_reviews']=dict(db.execute('SELECT recommendation,COUNT(*) FROM quality_reviews_v370 GROUP BY recommendation'))
            rows=dict(db.execute("SELECT key,value FROM settings WHERE key IN ('automation:level','automation:second_model_control_enabled')"))
            if 'automation:level' in rows:report['automation_level']=json.loads(rows['automation:level'])
            if 'automation:second_model_control_enabled' in rows:report['second_model_control']=bool(json.loads(rows['automation:second_model_control_enabled']))
        finally:db.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

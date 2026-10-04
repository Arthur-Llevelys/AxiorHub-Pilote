#!/usr/bin/env python3
"""Diagnostic lisible du cabinet opérant 3.8.1, sans secret ni contenu métier."""
import json
from pathlib import Path
import sqlite3

CONFIG=Path('/etc/axiorhub-mail-agent/config.json')
CURRENT=Path('/opt/axiorhub-mail-agent/current')


def main():
    config=json.loads(CONFIG.read_text());db_path=Path(config['state_dir'])/'desk.sqlite3'
    report={'version':'3.8.1','active_release':str(CURRENT.resolve()) if CURRENT.exists() else 'absente',
      'interface':{'cabinet_operating_css':(CURRENT/'agent/static/v380.css').is_file(),
        'assistant_transversal_css':(CURRENT/'agent/static/v370.css').is_file(),
        'assistant_transversal_javascript':(CURRENT/'agent/static/v370.js').is_file()},
      'licensing':{'agpl':(CURRENT/'LICENSE').is_file(),
        'trademark_policy':(CURRENT/'TRADEMARKS.md').is_file(),
        'logo_license':(CURRENT/'LOGO-LICENSE.md').is_file(),
        'third_party_notices':(CURRENT/'THIRD_PARTY_NOTICES.md').is_file(),
        'cyclonedx_sbom':(CURRENT/'SBOM.cdx.json').is_file()},
      'action_center':{},'playbooks':0,'matter_graph':{},'ecosystem':{},
      'evaluations':{},'secret_values_printed':False}
    if db_path.is_file():
        db=sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True)
        try:
            tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'action_reviews_v380' in tables:
                report['action_center']=dict(db.execute('SELECT state,COUNT(*) FROM action_reviews_v380 GROUP BY state'))
            if 'playbook_definitions_v380' in tables:
                report['playbooks']=db.execute('SELECT COUNT(*) FROM playbook_definitions_v380 WHERE enabled=1').fetchone()[0]
            if 'matter_graph_nodes_v380' in tables:
                report['matter_graph']={'nodes':db.execute('SELECT COUNT(*) FROM matter_graph_nodes_v380').fetchone()[0],
                  'edges':db.execute('SELECT COUNT(*) FROM matter_graph_edges_v380').fetchone()[0]}
            if 'ecosystem_services_v380' in tables:
                report['ecosystem']={'services':db.execute('SELECT COUNT(*) FROM ecosystem_services_v380').fetchone()[0],
                  'active':db.execute('SELECT COUNT(*) FROM ecosystem_services_v380 WHERE enabled=1 AND reviewed=1').fetchone()[0]}
            if 'evaluation_runs_v380' in tables:
                last=db.execute('SELECT score,created FROM evaluation_runs_v380 ORDER BY created DESC LIMIT 1').fetchone()
                report['evaluations']={'runs':db.execute('SELECT COUNT(*) FROM evaluation_runs_v380').fetchone()[0],
                  'last_score':last[0] if last else None,'last_created':last[1] if last else ''}
        finally:db.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

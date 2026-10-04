#!/usr/bin/env python3
"""Diagnostic sans secret de l'expérience et de l'apprentissage 3.9.2."""
import json

from agent.common import load_config
from agent.desk import Desk
from agent.learning392 import snapshot
from agent.production391 import dashboard


config=load_config('/etc/axiorhub-mail-agent/config.json')
desk=Desk(config);learning=snapshot(desk,30);production=dashboard(desk,30)
report={
  'version':'3.9.2',
  'experience':{
    'today_views':['a_traiter','pret_a_utiliser','activite_recente'],
    'review_steps':['lire','comparer_les_sources','decider'],
  },
  'learning':{
    'active_corrections':learning['active_corrections'],
    'approved_templates':learning['approved_templates'],
    'reviewed_outputs':learning['reviewed_outputs'],
    'useful_rate_percent':learning['useful_rate_percent'],
    'uses':learning['uses'],
    'guardrails':learning['guardrails'],
  },
  'production':{
    'flows':len(production['flows']),
    'incidents':len(production['incidents']),
    'mail_draft_coverage_percent':production['mail_draft_coverage_percent'],
  },
  'pending_jobs':[dict(x) for x in desk.db.execute("SELECT kind,status,COUNT(*) count FROM jobs WHERE status IN ('pending','running','error') GROUP BY kind,status ORDER BY COUNT(*) DESC")],
  'secrets_exposed':False,
}
print(json.dumps(report,ensure_ascii=False,indent=2))

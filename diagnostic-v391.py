#!/usr/bin/env python3
"""Secret-free production 3.9.1 diagnostic."""
import json
from pathlib import Path

from agent.common import load_config
from agent.desk import Desk
from agent.production391 import dashboard
from agent.ai_gateway import public_providers,usage_snapshot,transmission_snapshot


config=load_config('/etc/axiorhub-mail-agent/config.json')
desk=Desk(config);data=dashboard(desk,30)
report={
  'version':'3.9.1',
  'flows':len(data['flows']),
  'incidents':len(data['incidents']),
  'mail_draft_coverage_percent':data['mail_draft_coverage_percent'],
  'providers':[{k:v for k,v in row.items() if k not in ('url',)} for row in public_providers(config)],
  'external_usage_month':usage_snapshot(desk),
  'external_transmissions':transmission_snapshot(desk,20),
  'pending_jobs':[dict(x) for x in desk.db.execute("SELECT kind,status,COUNT(*) count FROM jobs WHERE status IN ('pending','running','error') GROUP BY kind,status ORDER BY COUNT(*) DESC")],
  'secrets_exposed':False,
}
print(json.dumps(report,ensure_ascii=False,indent=2))

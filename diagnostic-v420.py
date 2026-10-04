#!/usr/bin/env python3
"""Secret-free production and migration diagnostic for AxiorHub 4.2.0."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from agent.common import load_config
from agent.desk import Desk
from agent.production420 import metrics
from agent.update420 import policy as update_policy


def main():
    config_path=Path('/etc/axiorhub-mail-agent/config.json')
    try:
        desk=Desk(load_config(config_path));cutoff=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        statuses={row['status']:row['count'] for row in desk.db.execute(
          'SELECT status,COUNT(*) count FROM production_deliverables_v420 GROUP BY status')}
        migrations=[dict(row) for row in desk.db.execute(
          'SELECT id,name,applied FROM schema_migrations ORDER BY id')]
        stuck=[dict(row) for row in desk.db.execute("""SELECT kind,status,COUNT(*) count
          FROM jobs WHERE kind IN ('verify_deliverable420','retry_deliverable420')
          AND ((status='running' AND created<?) OR status='error')
          GROUP BY kind,status ORDER BY count DESC""",(cutoff,))]
        updates=update_policy(desk)
        report={'version':'4.2.0','green_policy':'destination_read_back_required',
          'deliverables':statuses,'useful_metrics':metrics(desk,30),
          'migrations':migrations,'migration_420_applied':any(x['id']==420 for x in migrations),
          'verification_jobs_stuck_or_failed':stuck,
          'update':{'command_available':shutil.which('axiorhub-mail') is not None,
            'minisign_available':shutil.which('minisign') is not None,
            'channel':str(updates.get('channel') or 'stable'),
            'metadata_configured':bool(updates.get('metadata_url')),
            'signature_required':bool(updates.get('require_signature',True)),
            'public_key_present':Path(str(updates.get('minisign_public_key_file') or
              '/etc/axiorhub-mail-agent/update-minisign.pub')).is_file()},
          'secrets_exposed':False,'document_content_exposed':False,
          'network_call_performed':False}
        print(json.dumps(report,ensure_ascii=False,indent=2))
    except PermissionError:
        print(json.dumps({'version':'4.2.0','status':'error','error':'permission_refusee',
          'command':'sudo /usr/bin/python3 /opt/axiorhub-mail-agent/current/diagnostic-v420.py'},
          ensure_ascii=False,indent=2));raise SystemExit(2)


if __name__=='__main__':main()

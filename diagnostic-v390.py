#!/usr/bin/env python3
"""Readable post-install diagnostic for controlled production."""
import json
from pathlib import Path

from agent.common import load_config
from agent.desk import Desk
from agent.production390 import dashboard

CONFIG=Path('/etc/axiorhub-mail-agent/config.json')


def main():
    desk=Desk(load_config(CONFIG))
    data=dashboard(desk,30)
    defaults={
      'production_enabled':desk.c.get('production',{}).get('enabled',True),
      'automatic_mail_drafts_enabled':desk.c.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True),
      'automatic_legal_projects_enabled':desk.c.get('orchestrator',{}).get('automatic_legal_projects_enabled',True),
      'automatic_internal_files_enabled':desk.c.get('autonomy',{}).get('automatic_internal_files_enabled',True),
      'document_control_enabled':desk.c.get('autonomy',{}).get('document_control_enabled',True),
    }
    data['automation']={key:desk.settings('automation:'+key,value) for key,value in defaults.items()}
    data['queue']=[dict(row) for row in desk.db.execute("""SELECT kind,status,COUNT(*) count
      FROM jobs WHERE kind IN ('production_cycle390','orchestrator_mail_sweep','prepare_reply',
      'prepare_document_project','create_document_files','prepare_hearing','create_hearing_files',
      'advance_playbooks390') GROUP BY kind,status ORDER BY COUNT(*) DESC""")]
    print(json.dumps(data,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

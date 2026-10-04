#!/usr/bin/env python3
"""Diagnostic ciblé 3.8.1, sans secret ni contenu de dossier."""
import json
from pathlib import Path
from localpaths import roundcube_root
import sqlite3
from urllib.parse import urlsplit
from urllib.request import urlopen

from agent.common import load_config
from agent.desk import Desk
from agent.mailbox import Mailbox
from agent.state import State
from agent.workstation import external_links

CONFIG=Path('/etc/axiorhub-mail-agent/config.json')
CURRENT=Path('/opt/axiorhub-mail-agent/current')


def main():
    c=load_config(CONFIG);desk=Desk(c);links=external_links(desk);state=State(c['state_dir'])
    openwebui=links.get('openwebui','');host=urlsplit(openwebui).hostname or ''
    report={'version':'3.8.1','active_release':str(CURRENT.resolve()),
      'routing':{'prefix':'/agent-courriel','openwebui_url':openwebui,
        'openwebui_mail_url':openwebui.rstrip('/')+'/mail' if openwebui else '',
        'placeholder_domain':host.endswith('.example.com') or host=='example.com'},
      'drafts':{'configured_folder':c['mail']['drafts'],
        'work_items_ready':desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready'").fetchone()[0],
        'verified_by_axiorhub':desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready' AND source_status='drafted'").fetchone()[0],
        'prepared_locally':desk.db.execute('SELECT COUNT(*) FROM manual_drafts').fetchone()[0],
        'uncertain':state.counts().get('append_uncertain',0)},
      'action_center':{'visible_completed_kinds':{}},
      'roundcube_bridge':{},'standalone':{},'secrets_printed':False}
    for kind,count in desk.db.execute("SELECT kind,COUNT(*) FROM jobs WHERE status='done' GROUP BY kind ORDER BY COUNT(*) DESC"):
        if kind in {'prepare_reply','deposit_draft','create_document_files','prepare_document_project',
          'prepare_hearing','create_hearing_files','prepare_word_project','create_word_files',
          'draft_act','analyze_strategy','build_matrix','prepare_legal_opinion','coach_hearing35',
          'prepare_call35','record_call35','billing_review35'}:
            report['action_center']['visible_completed_kinds'][kind]=count
    box=None
    try:
        box=Mailbox(c['mail']);report['drafts']['imap_messages']=len(box.search(c['mail']['drafts'],'ALL'))
        report['drafts']['imap_access']='OK'
    except Exception as exc:
        report['drafts']['imap_access']=str(exc)
    finally:
        if box:box.close()
    plugin=roundcube_root()/'plugins'/'ai_roundcube_assistant'
    report['roundcube_bridge']={'installed':(plugin/'ai_roundcube_assistant.php').is_file(),
      'javascript':(plugin/'ai_roundcube_assistant.js').is_file(),
      'stylesheet':(plugin/'ai_roundcube_assistant.css').is_file(),
      'config':(plugin/'config.inc.php').is_file()}
    try:
        with urlopen('http://127.0.0.1:8626/healthz',timeout=3) as response:
            report['standalone']={'local_port_8626':response.status,'health':response.read(32).decode(errors='replace')}
    except Exception:
        report['standalone']={'local_port_8626':'injoignable',
          'meaning':'Si Docker doit servir le domaine public configuré, vérifier le conteneur et le vhost vers 127.0.0.1:8626.'}
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

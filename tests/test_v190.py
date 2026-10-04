from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import unittest

import test_agent as fixtures
import test_desk
from agent.api import dispatch, openapi
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.operations import automation_tick
from agent.proactive import (build_daily_dashboard, change_signal, monitor_all,
    monitor_matter, signals)
from agent.portfolio import set_matter_state


class Proactive190Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.c['proactive']={'enabled':True,'monitor_interval_minutes':30,
          'monitor_batch_size':10,'deadline_warning_days':14,
          'unanswered_warning_hours':24,'daily_dashboard_enabled':True,
          'daily_dashboard_hour':0,'timezone':'UTC','monitor_live_nextcloud':False}
        self.d=Desk(self.f.c);self.index=DocumentIndex(self.f.c['state_dir'])
        set_matter_state(self.d,{'matter':'DOS-001','state':'active'})

    def put_doc(self,etag='v1'):
        self.index.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)',
          ('DOS-001','/Dossiers/DEMO/piece.txt',etag,'2026-09-09T09:00:00+00:00','Pièce du dossier',''))
        self.index.put_source('DOS-001','/Dossiers/DEMO/piece.txt','Pièce du dossier',etag,
          datetime.now(timezone.utc).isoformat(),'document')

    def test_new_or_changed_document_is_signalled_only_after_baseline(self):
        self.put_doc('v1');first=monitor_matter(self.d,{'matter':'DOS-001'})
        self.assertTrue(first['first_observation'])
        self.assertFalse(any(x['category']=='documents_changed' for x in signals(self.d)))
        self.put_doc('v2');second=monitor_matter(self.d,{'matter':'DOS-001'})
        self.assertTrue(second['changed'])
        changed=[x for x in signals(self.d) if x['category']=='documents_changed']
        self.assertEqual(len(changed),1);self.assertIn('piece.txt',changed[0]['detail'])
        monitor_matter(self.d,{'matter':'DOS-001'})
        self.assertEqual(len([x for x in signals(self.d) if x['category']=='documents_changed']),1)

    def test_upcoming_deadline_has_source_and_priority(self):
        due=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        self.d.db.execute('INSERT INTO timeline_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          ('evt-1','DOS-001','deadline',due,'','Conclusions','Délai mentionné dans le courriel',
           'mail-1','email_received','INBOX','2026-09-09',.65,'active','sig',self.d.now()))
        self.d.db.commit();monitor_matter(self.d,{'matter':'DOS-001'})
        alert=next(x for x in signals(self.d) if x['category']=='upcoming_deadline')
        self.assertEqual(alert['severity'],'critical');self.assertEqual(alert['source_ids'],['mail-1'])

    def test_signal_lifecycle_prevents_immediate_reappearance(self):
        monitor_matter(self.d,{'matter':'DOS-001'})
        sid=next(x for x in signals(self.d) if x['category']=='matter_not_indexed')['id']
        change_signal(self.d,'ack_signal',{'signal':sid});monitor_matter(self.d,{'matter':'DOS-001'})
        self.assertEqual(signals(self.d,'all',limit=10)[0]['state'],'acknowledged')
        change_signal(self.d,'snooze_signal',{'signal':sid,'hours':24})
        self.assertEqual(signals(self.d,'all',limit=10)[0]['state'],'snoozed')
        change_signal(self.d,'resolve_signal',{'signal':sid})
        self.assertEqual(signals(self.d,'all',limit=10)[0]['state'],'resolved')

    def test_daily_dashboard_contains_actionable_not_technical_digest(self):
        monitor_matter(self.d,{'matter':'DOS-001'});data=build_daily_dashboard(self.d)
        self.assertIn('priorities',data);self.assertIn('mail_counts',data)
        self.assertIn('limits',data);self.assertNotIn('documents_en_erreur',data)
        saved=json.loads(self.d.db.execute('SELECT data FROM daily_dashboards').fetchone()[0])
        self.assertEqual(saved['day'],data['day'])

    def test_monitor_all_is_bounded_and_resumable(self):
        result=monitor_all(self.d,{})
        self.assertEqual(result['dossiers_mis_en_attente'],1)
        job=self.d.db.execute("SELECT kind,priority FROM jobs WHERE status='pending'").fetchone()
        self.assertEqual(job['kind'],'monitor_matter');self.assertGreater(job['priority'],0)

    def test_api_and_openwebui_expose_monitoring_without_send(self):
        self.assertEqual(openapi('https://cabinet.test')['info']['version'],'5.6.2')
        job=dispatch(self.d,'/matters/DOS-001/monitor','POST',{})
        self.assertEqual(job['status'],'queued')
        self.assertIn('signals',dispatch(self.d,'/signals','GET',query={}))
        tool=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('afficher_les_priorites_du_jour',tool);self.assertIn('surveiller_un_dossier',tool)
        self.assertNotIn('envoyer_un_courriel',tool);self.assertNotIn('subprocess',tool)

    def test_scheduler_queues_only_bounded_internal_jobs(self):
        automation_tick(self.d)
        kinds={x[0] for x in self.d.db.execute("SELECT kind FROM jobs WHERE status='pending'")}
        self.assertIn('monitor_all',kinds);self.assertIn('build_daily_dashboard',kinds)
        self.assertNotIn('daily_digest',kinds);self.assertNotIn('send',kinds)


class Web190Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_daily_dashboard_and_surveillance_are_intuitive_and_static(self):
        dashboard=self.request('/dashboard')['body'];watch=self.request('/surveillance')['body']
        self.assertIn('VOTRE JOURNÉE',dashboard);self.assertIn('Surveillance proactive',watch)
        self.assertIn('Surveiller tous les dossiers',watch);self.assertIn('Version 5.6.2',watch)
        self.assertNotIn('http-equiv="refresh"',dashboard+watch)


if __name__=='__main__':unittest.main()

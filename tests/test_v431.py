"""Read-back recovery, bounded retry, activity proofs and durable restore."""
import json
import time
import unittest
from unittest.mock import patch
from agent.common import Stop
from agent.desk import Desk
from agent.activity431 import maintenance,mail_outcome
from agent.live430 import snapshot,panel,heartbeat,emit
from agent.watch430 import schedule
from agent.operating380 import review_action
import test_agent as fixtures


class Activity431Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c);self.d.setting('search490:scheduled',1e12)

    def fail_job(self,kind,attempts=1):
        jid=self.d.enqueue(kind)
        self.d.db.execute("UPDATE jobs SET status='error',attempts=? WHERE id=?",(attempts,jid));self.d.db.commit();return jid

    def test_retry_read_only_same_identity_capped_and_backoff(self):
        jid=self.fail_job('live_calendar430')
        maintenance(self.d,10000)
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'pending')
        self.d.db.execute("UPDATE jobs SET status='error',attempts=2 WHERE id=?",(jid,));self.d.db.commit()
        maintenance(self.d,10001)
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'error')
        maintenance(self.d,10061)
        self.d.db.execute("UPDATE jobs SET status='error',attempts=3 WHERE id=?",(jid,));self.d.db.commit()
        maintenance(self.d,10122)
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'error')
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)

    def test_producer_never_replayed_automatically(self):
        jid=self.fail_job('live_mail430');maintenance(self.d)
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'error')

    def test_periodic_schedule_does_not_reset_exhausted_retry_budget(self):
        jid=self.fail_job('live_calendar430',3)
        schedule(self.d,100000);schedule(self.d,100301)
        rows=self.d.db.execute("SELECT id FROM jobs WHERE kind='live_calendar430'").fetchall()
        self.assertEqual([x[0] for x in rows],[jid])
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM live_events_v430 WHERE dedupe=?",('retry-exhausted-'+str(jid),)).fetchone()[0],1)

    def test_missing_heartbeat_is_incident_not_lease_theft(self):
        jid=self.d.enqueue('live_mail430')
        self.d.db.execute("UPDATE jobs SET status='running',worker='1',started=? WHERE id=?",(self.d.now(),jid));self.d.db.commit()
        maintenance(self.d)
        self.assertTrue(snapshot(self.d)['jobs'][0]['blocked'])
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'running')
        heartbeat(self.d,'worker-1');maintenance(self.d)
        self.assertFalse(snapshot(self.d)['jobs'][0]['blocked'])

    def test_verified_output_has_uid_destination_sources_and_direct_link(self):
        key='a'*64;jid=self.d.enqueue('live_mail430');self.d.active_job_id=jid
        mail_outcome(self.d,key,{'status':'drafted','matter':'DOS-001',
          'sources':[{'kind':'document','content':'DO NOT LEAK'},{'kind':'calendar_event'}],
          'draft_body':'PRIVATE BODY','models_used':[{'function':'compose','provider':'ollama','model':'local'}],
          'draft_verified':{'uid':'42','uidvalidity':'7','folder':'INBOX.Drafts','verified_at':self.d.now()}})
        self.d.db.execute("UPDATE jobs SET status='done' WHERE id=?",(jid,));self.d.db.commit()
        data=snapshot(self.d);item=data['jobs'][0]
        self.assertEqual(item['href'],'/mail?key='+key);self.assertEqual(item['open_label'],'Ouvrir le brouillon')
        self.assertEqual(item['outputs'][0]['details']['source_counts']['document'],1)
        self.assertNotIn('PRIVATE BODY',json.dumps(data));self.assertNotIn('DO NOT LEAK',panel(self.d,'/agent','csrf'))
        self.assertIn('UID 42',panel(self.d,'/agent','csrf'))
        self.assertIn('0 courriels du fil',panel(self.d,'/agent','csrf'))

    def test_unverified_draft_is_blocked_not_produced(self):
        self.d.active_job_id=self.d.enqueue('live_mail430')
        mail_outcome(self.d,'a'*64,{'status':'drafted','draft_verified':{'folder':'Drafts'}})
        self.assertEqual(snapshot(self.d)['jobs'][0]['outputs'][0]['status'],'blocked')

    def test_today_includes_failed_jobs_without_deliverable(self):
        from agent.production420_ui import today_page
        self.fail_job('live_mail430')
        html=today_page(self.d,lambda action,label,args=None:'<button>'+label+'</button>',lambda path,label,**kw:'<a>'+label+'</a>')
        self.assertIn('incident-',html);self.assertIn('Relancer',html)

    def test_abstention_is_explained_and_no_fake_models(self):
        self.d.active_job_id=self.d.enqueue('live_mail430')
        mail_outcome(self.d,'a'*64,{'status':'ignored','reason':'message_automatique'})
        output=snapshot(self.d)['jobs'][0]['outputs'][0]
        self.assertEqual(output['status'],'abstained');self.assertIn('Aucune réponse déposée',output['message'])
        self.assertEqual(output['details']['models'],[])

    def test_live_jobs_not_lost_behind_old_successes(self):
        jid=self.d.enqueue('live_mail430')
        for n in range(155):
            other=self.d.enqueue('sync',{'test':str(n)})
            self.d.db.execute("UPDATE jobs SET status='done' WHERE id=?",(other,));self.d.db.commit()
        self.assertIn(jid,[x['id'] for x in snapshot(self.d)['jobs']])

    def test_daily_check_reuses_existing_progressive_jobs(self):
        schedule(self.d,100000);schedule(self.d,100001)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='monitor_all'").fetchone()[0],1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='index_all'").fetchone()[0],1)

    def test_archive_preserves_job_and_business_event(self):
        jid=self.d.enqueue('live_mail430');emit(self.d,'detected','Original',jid)
        self.d.db.execute("UPDATE live_events_v430 SET at='2020-01-01T00:00:00+00:00'");self.d.db.commit()
        maintenance(self.d)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        self.assertTrue(self.d.db.execute('SELECT 1 FROM live_event_archive_v431').fetchone())
        self.assertFalse(self.d.db.execute('SELECT 1 FROM live_events_v430').fetchone())

    def test_dismiss_restore_removes_only_selected_decision(self):
        review_action(self.d,'a'*64,'dismissed');review_action(self.d,'b'*64,'dismissed')
        review_action(self.d,'a'*64,'restored')
        rows=self.d.db.execute('SELECT action_id FROM action_reviews_v380').fetchall()
        self.assertEqual([x[0] for x in rows],['b'*64])

    def test_crash_recovery_requires_content_readback(self):
        self.f.box.fail_append=True;self.f.box.stored_on_failure=True;m=fixtures.mail()
        key=self.f.engine.process(m)
        with patch.object(self.f.box,'verify_draft',side_effect=Stop('brouillon_contenu_non_conforme')) as verify:
            self.f.engine.process(m);verify.assert_called_once()
        self.assertEqual(self.f.status(key),'append_uncertain');self.assertEqual(len(self.f.box.appended),1)

    def test_crash_recovery_republishes_verified_receipt(self):
        self.f.box.fail_append=True;self.f.box.stored_on_failure=True;m=fixtures.mail()
        key=self.f.engine.process(m);results=[];self.f.engine.outcome=lambda k,r:results.append(r)
        self.f.engine.process(m)
        self.assertEqual(self.f.status(key),'drafted');self.assertEqual(results[0]['draft_verified']['uid'],'1')
        self.assertIn('X-AxiorHub-Draft-Key',results[0]['draft_expected_headers'])

import json
import unittest
from unittest.mock import patch
import test_agent as fixtures
from agent.common import Stop,digest
from agent.desk import Desk
from agent.intelligence import deposit_draft
from agent.mailbox import exclusion
from agent.workstation import external_links,daily_briefing,why_nothing

class ProductivityHotfix(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.d=Desk(self.f.c);self.f.c['mail']['process_seen_recent']=True
        self.mail=fixtures.mail(flags={'\\Seen'});self.key=self.mail.key(self.f.c['mail']['username']+'@'+self.f.c['mail']['host'])
        self.data={'result':{'requires_decision':False,'body':'Projet sourcé à relire.'},'matter':'DOS-001',
            'matter_path':'/Dossiers/DEMO','source_hash':digest(self.mail.text),'recipients':['client@example.test'],'sources':[]}
        self.save()
        c=self.f.c
        class Box(fixtures.FakeBox):
            def preflight(self,m,draft_mid=None,allow_seen=False):
                return exclusion(m,c['mail'],allow_seen=allow_seen)
            def sendmail(self,*args,**kwargs):raise AssertionError('Envoi interdit')
            def send_message(self,*args,**kwargs):raise AssertionError('Envoi interdit')
        self.box=Box()
        self.addCleanup(patch.stopall)
        patch('agent.intelligence.Mailbox',return_value=self.box).start()
        patch('agent.intelligence.fetch_source',return_value=({},self.mail)).start()
    def save(self):
        self.d.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES (?,?,?)',(self.key,json.dumps(self.data),self.d.now()));self.d.db.commit()
    def deposit(self):return deposit_draft(self.d,{'key':self.key,'confirm':'yes'})
    def test_seen_message_creates_one_draft_and_no_send(self):
        self.assertFalse(self.deposit()['envoi']);self.assertEqual(len(self.box.appended),1)
        self.save()
        with self.assertRaisesRegex(Stop,'deja_depose'):self.deposit()
        self.assertEqual(len(self.box.appended),1)
    def test_seen_policy_false_still_blocks(self):
        self.f.c['mail']['process_seen_recent']=False
        with self.assertRaisesRegex(Stop,'deja_lu'):self.deposit()
        self.assertEqual(self.box.appended,[])
    def test_answered_message_still_blocks(self):
        self.mail.flags.add('\\Answered')
        with self.assertRaisesRegex(Stop,'deja_repondu'):self.deposit()
        self.assertEqual(self.box.appended,[])
    def test_changed_source_still_blocks(self):
        self.data['source_hash']='wrong';self.save()
        with self.assertRaisesRegex(Stop,'courriel_modifie'):self.deposit()
        self.assertEqual(self.box.appended,[])
    def test_missing_confirmation_blocks(self):
        with self.assertRaisesRegex(Stop,'confirmation_explicite'):deposit_draft(self.d,{'key':self.key})
        self.assertEqual(self.box.appended,[])
    def test_uncertain_append_is_not_repeated(self):
        self.box.fail_append=True;self.box.stored_on_failure=True
        with self.assertRaisesRegex(Stop,'append_imap_incertain'):self.deposit()
        with self.assertRaisesRegex(Stop,'deja_depose_ou_incertain'):self.deposit()
        self.assertEqual(len(self.box.appended),1)
    def test_invoice_url_fallback_and_explicit_disable(self):
        self.f.c['workstation']={'invoice_ninja_url':''};self.f.c['invoice_ninja']={'base_url':'https://invoice.test/api/v1'}
        self.assertEqual(external_links(self.d)['invoice_ninja'],'https://invoice.test')
        self.f.c['invoice_ninja']['base_url']='https://invoice.test/api/v1?token=private'
        self.assertEqual(external_links(self.d)['invoice_ninja'],'')
        self.d.setting('workstation:url:invoice_ninja','')
        self.assertEqual(external_links(self.d)['invoice_ninja'],'')
    def test_open_anomaly_count_is_real(self):
        self.d.db.execute('INSERT INTO proactive_signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
           ('test','DOS-001','missing','low','test','detail','[]','review','open','','hash',self.d.now(),self.d.now(),''));self.d.db.commit()
        self.assertEqual(daily_briefing(self.d)['open_anomalies'],1)
    def test_failure_details_cannot_leak_arbitrary_exception(self):
        self.d.enqueue('draft_act',{'instruction':'private argument'})
        self.d.db.execute("UPDATE jobs SET status='error', result=?",(json.dumps({'erreur':'Secret email client@example.test'}),));self.d.db.commit()
        data=json.dumps(why_nothing(self.d)['recent_failures'])
        self.assertNotIn('Secret',data);self.assertNotIn('client@',data);self.assertNotIn('private',data)
    def test_known_failure_is_translated(self):
        self.d.enqueue('draft_act',{})
        self.d.db.execute("UPDATE jobs SET status='error', result=?",(json.dumps({'erreur':'connexion_http_indisponible'}),));self.d.db.commit()
        self.assertIn('service',why_nothing(self.d)['recent_failures'][0]['label'])

if __name__=='__main__':unittest.main()

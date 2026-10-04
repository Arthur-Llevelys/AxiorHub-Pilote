"""Business-result production, verification and 4.2 interface coverage."""
from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.api import dispatch, openapi
from agent.common import Stop, digest
from agent.desk import Desk
from agent.learning410 import save_rule, snapshot as learning_snapshot
from agent.production420 import (dashboard, metrics, record_job, submit_studio,
                                 trigger_events, verify_deliverable)
from agent.update420 import policy as update_policy, save_policy as save_update_policy
import test_agent as fixtures
import test_desk


class Production420Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def _job(self,kind,args):
        jid=self.d.enqueue(kind,args)
        return self.d.db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone(),jid

    def test_imap_draft_is_available_only_after_destination_readback(self):
        row,jid=self._job('prepare_reply',{'matter':'DOS-001','key':'a'*64,
          'trigger_kind':'incoming_mail','trigger_reason':'Réponse attendue par le client'})
        ident=record_job(self.d,row,json.loads(row['args']),'done',{
          'brouillon_imap':'verifie','draft_verified':{'folder':'INBOX.Drafts','uid':'42',
          'verified_at':'2026-10-01T08:42:00+00:00'},'source_ids':['mail:a']})
        item=self.d.db.execute('SELECT * FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()
        self.assertEqual(item['status'],'verified');self.assertIn('INBOX.Drafts',item['business_message'])
        self.assertNotIn('Action réalisée',item['business_message'])
        self.assertEqual(metrics(self.d)['verified_imap_drafts'],1)

    def test_nextcloud_file_is_relisted_downloaded_and_hashed(self):
        raw=b'document word verifie';path='/Dossiers/DEMO/90_AxiorHub_Brouillons/projet.docx'
        row,jid=self._job('create_document_files',{'matter':'DOS-001','project_id':'p1'})
        ident=record_job(self.d,row,json.loads(row['args']),'done',{'created_files':[
          {'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}]})
        self.assertEqual(self.d.db.execute('SELECT status FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()[0],'verifying')
        class Dav:
            def list_folder(self,parent):return [{'path':path,'directory':False}]
            def download(self,item):return raw
        result=verify_deliverable(self.d,{'deliverable_id':ident},Dav())
        self.assertEqual(result['status'],'verified');self.assertEqual(result['files_verified'],1)
        stored=json.loads(self.d.db.execute('SELECT verification FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()[0])
        self.assertTrue(stored['verified']);self.assertEqual(stored['files'][0]['sha256'],hashlib.sha256(raw).hexdigest())

    def test_remote_mismatch_never_becomes_green(self):
        path='/Dossiers/DEMO/projet.docx';row,_=self._job('create_document_files',{'matter':'DOS-001'})
        ident=record_job(self.d,row,json.loads(row['args']),'done',{'created_files':[
          {'path':path,'sha256':hashlib.sha256(b'attendu').hexdigest(),'bytes':7}]})
        class Dav:
            def list_folder(self,parent):return [{'path':path,'directory':False}]
            def download(self,item):return b'autre'
        with self.assertRaisesRegex(Stop,'fichier_cree_non_conforme'):
            verify_deliverable(self.d,{'deliverable_id':ident},Dav())
        self.assertNotEqual(self.d.db.execute('SELECT status FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()[0],'verified')

    def test_studio_uses_existing_producer_and_presets_matter(self):
        result=submit_studio(self.d,{'matter':'DOS-001','deliverable_kind':'conclusions',
          'instruction':'Préparer un projet de conclusions en réponse, sourcé et contradictoire.',
          'source_paths':['/Dossiers/DEMO/conclusions-adverses.pdf']})
        job=self.d.db.execute('SELECT kind,args FROM jobs WHERE id=?',(result['job_id'],)).fetchone()
        self.assertEqual(job['kind'],'prepare_document_project')
        self.assertEqual(json.loads(job['args'])['matter'],'DOS-001')

    def test_contract_signal_queues_a_useful_internal_analysis(self):
        stamp=datetime.now(timezone.utc).isoformat();sid='b'*64
        self.d.db.execute('''INSERT INTO proactive_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (sid,'DOS-001','documents_changed','normal','Nouveau contrat reçu',
           'Contrat SaaS ajouté au dossier',json.dumps(['/Dossiers/DEMO/contrat.pdf']),
           'Analyser les clauses.','open','',digest('contrat'),stamp,stamp,''));self.d.db.commit()
        result=trigger_events(self.d)
        self.assertEqual(result['queued'],1)
        job=self.d.db.execute('SELECT kind,args FROM jobs WHERE id=?',(result['job_ids'][0],)).fetchone()
        self.assertEqual(job['kind'],'prepare_document_project')
        self.assertIn('tableau des clauses',json.loads(job['args'])['instruction'])

    def test_learning_rules_are_measured_but_never_disabled_silently(self):
        rule=save_rule(self.d,'cabinet','','mail_drafting','subject','Toujours rappeler la référence du dossier.')
        stamp=datetime.now(timezone.utc).isoformat()
        for n in range(5):
            self.d.db.execute('INSERT INTO production_rule_influence_v420 VALUES(?,?,?,?,?,?)',
              ('d'+str(n),rule['id'],'rejected',0,stamp,stamp))
        self.d.db.commit();item=next(x for x in learning_snapshot(self.d)['rules'] if x['id']==rule['id'])
        self.assertEqual(item['status'],'active');self.assertIn('Aucune désactivation automatique',item['recommendation'])

    def test_numbered_migration_and_api_contract_are_present(self):
        self.assertIsNotNone(self.d.db.execute('SELECT name FROM schema_migrations WHERE id=420').fetchone())
        spec=openapi('https://agent.example.test')
        for path in ('/production/verified','/production/metrics','/production/studio',
          '/production/studio/estimate','/production/deliverables/{deliverable_id}/retry',
          '/matters/{matter_id}/advance'):
            self.assertIn(path,spec['paths'])
        self.assertIn('metrics',dispatch(self.d,'/production/verified','GET'))

    def test_update_channel_is_explicit_and_signature_cannot_be_disabled(self):
        saved=save_update_policy(self.d,'test','https://updates.example.test/releases.json')
        self.assertEqual(saved['channel'],'test');self.assertTrue(saved['require_signature'])
        current=update_policy(self.d)
        self.assertEqual(current['channel'],'test');self.assertTrue(current['require_signature'])
        with self.assertRaises(Stop):save_update_policy(self.d,'nightly','https://updates.example.test/releases.json')


class Browser420Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_today_has_only_the_four_business_zones(self):
        body=self.request('/aujourdhui',query='vue=detail')['body']
        for label in ('À faire aujourd’hui','Préparé pour vous','Votre décision est nécessaire','Incidents'):
            self.assertIn(label,body)
        self.assertNotIn('Action réalisée :',body)
        self.assertIn('Journal technique secondaire',body)

    def test_studio_and_smart_matter_are_actionable(self):
        studio=self.request('/studio',query='matter=DOS-001&type=conclusions')['body']
        for label in ('Studio de production','Documents sources','Modèle Word','Coût estimé','Confidentialité'):
            self.assertIn(label,studio)
        matter=self.request('/matter',query='id=DOS-001')['body']
        self.assertIn('Faire avancer le dossier',matter);self.assertIn('Prochaine action recommandée',matter)

    def test_maintenance_exposes_signed_stable_and_test_channels(self):
        body=self.request('/parametres',query='tab=maintenance')['body']
        for label in ('Mises à jour signées','Stable','Test','Signature obligatoire'):
            self.assertIn(label,body)


if __name__=='__main__':unittest.main()

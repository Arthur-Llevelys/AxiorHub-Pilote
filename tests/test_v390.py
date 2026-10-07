"""Controlled autonomous production coverage for AxiorHub 5.0.0."""
import json
import unittest

from agent.api import dispatch, openapi
from agent.desk import Desk
from agent.operating380 import playbooks
from agent.production390 import after_job, cycle, dashboard, record_job_result
import test_agent as fixtures
import test_desk


class Production390(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_verified_draft_is_a_visible_deliverable(self):
        record_job_result(self.d,{'id':901,'kind':'prepare_reply'},{'key':'a'*64,'matter':'DOS-001'},
          'done',{'projet_prepare':True,'depot_autorise':True,'brouillon_imap':'verifie'})
        row=self.d.db.execute('SELECT * FROM production_outputs_v390 WHERE job_id=901').fetchone()
        self.assertEqual((row['output_kind'],row['status']),('mail_draft','delivered'))
        self.assertIn('Brouillon vérifié',row['label'])

    def test_approved_automatic_project_queues_only_new_internal_files(self):
        result={'project_id':'b'*32,'status':'pending','confirmation_code':'123456',
          'destination_folder':'/CABINET/DOS-001/90_AxiorHub_Brouillons',
          'source_file':{'path':'modele_interne:MODELE_DOCUMENT.docx'},
          'control':{'status':'approved_for_confirmation'},'future_files':['/CABINET/DOS-001/90_AxiorHub_Brouillons/projet.docx'],
          'matter':{'id':'DOS-001'}}
        after_job(self.d,{'id':902,'kind':'prepare_document_project'},
          {'matter':'DOS-001','automatic':'yes'},'done',result)
        job=self.d.db.execute("SELECT kind,args FROM jobs WHERE kind='create_document_files'").fetchone()
        self.assertIsNotNone(job);args=json.loads(job['args'])
        self.assertEqual(args['project_id'],'b'*32);self.assertEqual(args['automatic'],'yes')
        self.assertNotIn('send',args);self.assertNotIn('rpva',args)

    def test_playbooks_produce_instead_of_stopping_before_the_draft(self):
        catalogue={x['id']:x for x in playbooks(self.d)}
        hearing=catalogue['litigation_hearing_v1']['steps'][-1]
        response=catalogue['opponent_writings_v1']['steps'][-1]
        self.assertEqual(hearing['job_kind'],'prepare_hearing');self.assertNotIn('manual',hearing)
        self.assertEqual(response['job_kind'],'prepare_document_project');self.assertEqual(response['args']['document_type'],'conclusions')

    def test_event_signal_starts_and_advances_a_playbook(self):
        stamp=self.d.now();self.d.db.execute('INSERT INTO proactive_signals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          ('signal-1','DOS-001','upcoming_appointment','high','Audience de plaidoirie',
           'Audience devant le tribunal dans 10 jours',json.dumps(['calendar-1']),
           'Préparer la plaidoirie','open','2026-10-09','fp-1',stamp,stamp,''));self.d.db.commit()
        result=cycle(self.d,{'limit':5})
        self.assertEqual(result['playbooks_started'],1);self.assertEqual(result['playbooks_advanced'],1)
        run=self.d.db.execute('SELECT playbook_id,status FROM playbook_runs_v380').fetchone()
        self.assertEqual(run['playbook_id'],'litigation_hearing_v1')
        self.assertIsNotNone(self.d.db.execute("SELECT 1 FROM jobs WHERE kind='monitor_matter'").fetchone())
        self.assertEqual(result['external_actions'],0)

    def test_dashboard_exposes_coverage_failures_and_safety_boundary(self):
        record_job_result(self.d,{'id':903,'kind':'prepare_hearing'},{'matter':'DOS-001'},
          'error',{'erreur':'conclusions_absentes'})
        data=dashboard(self.d)
        self.assertIn('mail_draft_coverage_percent',data);self.assertEqual(data['counts']['errors'],1)
        self.assertTrue(data['safety']['automatic_internal_work'])
        self.assertTrue(data['safety']['email_send_requires_confirmation'])

    def test_api_exposes_the_five_production_deliverables(self):
        schema=openapi('https://agent.example.com')
        self.assertEqual(schema['info']['version'],'5.6.13')
        for path in ('/production/dashboard','/production/run','/production/playbooks/advance'):
            self.assertIn(path,schema['paths'])
        self.assertIn('safety',dispatch(self.d,'/production/dashboard','GET'))


class Browser390(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_production_page_is_actionable_and_keeps_the_assistant(self):
        body=self.request('/production')['body']
        for value in ('Production réelle','Brouillons IMAP vérifiés',
                      'Lancer la production','Continuer les playbooks',
                      'L’avocat décide','ws-ai-launcher','app520.css'):
            self.assertIn(value,body)


if __name__=='__main__':unittest.main()

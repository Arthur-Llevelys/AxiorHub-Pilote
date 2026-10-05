"""Production réelle, relecture, pièces multiples and hybrid budgets in 5.0.0."""
import json
import unittest

from agent.ai_gateway import estimate_and_check_budget,record_usage,transmission_snapshot
from agent.api import dispatch,openapi
from agent.common import Stop
from agent.desk import Desk
from agent.production390 import record_job_result
from agent.production391 import after_job,dashboard,retry_failed,review_output
import test_agent as fixtures
import test_desk


class Production391(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_failed_work_is_observable_and_retryable(self):
        job=self.d.enqueue('prepare_hearing',{'matter':'DOS-001','instruction':'Préparer'})
        self.d.db.execute("UPDATE jobs SET status='error',result=? WHERE id=?",(json.dumps({'erreur':'conclusions_absentes'}),job));self.d.db.commit()
        after_job(self.d,{'id':job,'kind':'prepare_hearing'},{'matter':'DOS-001'},'error',{'erreur':'conclusions_absentes'})
        data=dashboard(self.d);self.assertEqual(len(data['incidents']),1)
        self.assertEqual(data['incidents'][0]['last_error'],'conclusions_absentes')
        retried=retry_failed(self.d,{'job_id':job});self.assertNotEqual(retried['job_id'],job)
        self.assertEqual(self.d.db.execute('SELECT retry_count FROM production_flows_v391 WHERE job_id=?',(job,)).fetchone()[0],1)

    def test_deliverable_exposes_source_model_path_and_stages(self):
        after_job(self.d,{'id':991,'kind':'create_document_files'},{'matter':'DOS-001'},'done',
          {'created_files':[{'path':'/CABINET/DOS-001/90_AxiorHub_Brouillons/conclusions.docx'}]})
        flow=dashboard(self.d)['flows'][0]
        self.assertEqual(flow['matter'],'DOS-001');self.assertTrue(flow['model_name'])
        self.assertIn('conclusions.docx',flow['paths'][0]);self.assertEqual(len(flow['stages']),7)
        self.assertTrue(flow['output_id'])

    def test_modified_output_creates_reversible_explicit_correction(self):
        record_job_result(self.d,{'id':992,'kind':'prepare_document_project'},{'matter':'DOS-001'},'done',{'status':'pending'})
        oid=self.d.db.execute('SELECT id FROM production_outputs_v390 WHERE job_id=992').fetchone()[0]
        result=review_output(self.d,{'output_id':oid,'decision':'modified','corrected':'Référence DOS-001','guidance':'Toujours rappeler la référence du dossier.'})
        self.assertIsNotNone(result['correction_id'])
        rule=self.d.db.execute('SELECT enabled,guidance FROM correction_examples_v370 WHERE id=?',(result['correction_id'],)).fetchone()
        self.assertEqual(rule['enabled'],1);self.assertIn('référence',rule['guidance'])

    def test_openrouter_budget_is_checked_before_transmission(self):
        cfg={'provider_type':'openrouter','per_request_budget_usd':0.001,
             'input_usd_per_million':2,'output_usd_per_million':10}
        with self.assertRaisesRegex(Stop,'budget_requete_fournisseur_depasse'):
            estimate_and_check_budget(cfg,[{'role':'user','content':'x'*10000}],5000)

    def test_external_transmission_log_proves_payload_without_storing_it(self):
        cfg={'provider_type':'openrouter','provider_id':'openrouter','purpose':'control',
          'model':'example/model','state_dir':self.f.c['state_dir'],'external_data_allowed':True,'zdr_required':True}
        record_usage(cfg,{'usage':{'prompt_tokens':12,'completion_tokens':3}},0.01,'done',
          [{'role':'user','content':'SECRET-FICTIF-391'}])
        row=transmission_snapshot(self.d,1)[0]
        self.assertEqual(len(row['payload_sha256']),64);self.assertNotIn('SECRET-FICTIF-391',json.dumps(row))
        columns=[x[1] for x in self.d.db.execute('PRAGMA table_info(ai_transmissions_v391)')]
        self.assertNotIn('content',columns);self.assertNotIn('prompt',columns)

    def test_api_exposes_391_pipeline(self):
        spec=openapi('https://agent.example.com');self.assertEqual(spec['info']['version'],'5.6.5')
        for path in ('/production/retry','/production/review'):self.assertIn(path,spec['paths'])
        result=dispatch(self.d,'/production/dashboard','GET');self.assertIn('flows',result)


class Browser391(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_today_and_production_are_lawyer_facing(self):
        today=self.request('/aujourdhui',query='vue=detail')['body']
        for value in ('À faire maintenant','Préparé par AxiorHub','Décision nécessaire','Incidents','Terminé récemment','app520.css'):
            self.assertIn(value,today)
        prod=self.request('/production')['body']
        self.assertIn('Chaîne de production',prod);self.assertIn('Production réelle',prod)

    def test_contextual_assistant_accepts_three_documents_and_autonomy(self):
        body=self.request('/matter',query='id=DOS-001')['body']
        self.assertIn('multiple id="ws-ai-file"',body);self.assertIn('Niveau d’autonomie',body)
        self.assertIn('value="DOS-001" selected',body)


if __name__=='__main__':unittest.main()

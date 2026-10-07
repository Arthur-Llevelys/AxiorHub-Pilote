"""Simplified experience and explicit business learning in 5.0.0."""
import json
import unittest

from agent.api import dispatch,openapi
from agent.desk import Desk
from agent.learning392 import learning_context,set_correction_state,snapshot
from agent.production390 import record_job_result
from agent.production391 import review_output
from agent.relevance370 import record_correction
import test_agent as fixtures
import test_desk


class Learning392(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_context_uses_only_approved_guidance_not_case_facts(self):
        cid=record_correction(self.d,'matter','DOS-001','style',
          'Le client secret réclame 987654 euros.','Formulation corrigée.',
          'Employer des phrases courtes et annoncer la conclusion en premier.')['id']
        context=learning_context(self.d,'DOS-001','mail_drafting')
        raw=json.dumps(context,ensure_ascii=False)
        self.assertIn('phrases courtes',raw);self.assertNotIn('987654',raw)
        self.assertNotIn('client secret',raw);self.assertEqual(context['corrections'][0]['id'],cid)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM learning_uses_v392').fetchone()[0],1)

    def test_paused_rule_is_not_reused_and_can_be_restored(self):
        cid=record_correction(self.d,'general','','structure','A','B','Commencer par une synthèse.')['id']
        set_correction_state(self.d,cid,False)
        self.assertEqual(learning_context(self.d,'DOS-001','document_drafting',record=False)['corrections'],[])
        set_correction_state(self.d,cid,True)
        self.assertEqual(len(learning_context(self.d,'DOS-001','document_drafting',record=False)['corrections']),1)

    def test_approved_template_metadata_is_exposed_without_document_bytes(self):
        learning_context(self.d,'DOS-001','assistant',record=False)
        self.d.db.execute('INSERT INTO cabinet_templates_v330 VALUES(?,?,?,?,?,?,?,?,?)',
          ('tpl-1','Courrier cabinet',1,'a'*64,'approved',json.dumps(['corps','date']),
           '[]',self.d.now(),self.d.now()));self.d.db.commit()
        context=learning_context(self.d,'DOS-001','document_drafting')
        self.assertEqual(context['approved_templates'][0]['label'],'Courrier cabinet')
        self.assertNotIn('bytes',context['approved_templates'][0])

    def test_review_updates_learning_metrics(self):
        record_job_result(self.d,{'id':1392,'kind':'prepare_document_project'},
          {'matter':'DOS-001'},'done',{'status':'pending'})
        oid=self.d.db.execute('SELECT id FROM production_outputs_v390 WHERE job_id=1392').fetchone()[0]
        review_output(self.d,{'output_id':oid,'decision':'modified','learning_scope':'matter',
          'source_kind':'style','corrected':'Projet corrigé',
          'guidance':'Éviter les formules impersonnelles dans les courriers au client.'})
        data=snapshot(self.d);self.assertEqual(data['reviewed_outputs'],1)
        self.assertEqual(data['useful_rate_percent'],100);self.assertEqual(data['active_corrections'],1)

    def test_api_exposes_learning_and_version(self):
        spec=openapi('https://agent.example.com')
        self.assertEqual(spec['info']['version'],'5.6.13')
        self.assertIn('/learning',spec['paths']);self.assertIn('/learning/corrections/{correction_id}',spec['paths'])
        self.assertIn('guardrails',dispatch(self.d,'/learning','GET'))


class Browser392(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_today_is_reduced_to_three_work_views(self):
        body=self.request('/aujourdhui',query='vue=detail')['body']
        for value in ('À traiter','Prêt à utiliser','Activité récente','Routage hybride','app520.css'):
            self.assertIn(value,body)
        self.assertEqual(body.count('class="ux392-tabs"'),1)

    def test_matter_navigation_and_learning_page_are_simple(self):
        matter=self.request('/matter',query='id=DOS-001')['body']
        self.assertIn('ws-matter-nav392',matter);self.assertIn('Apprentissage métier',matter)
        for value in ('Essentiel','Travail préparé','Arguments et chronologie','Échanges et réglages'):
            self.assertIn(value,matter)
        learning=self.request('/apprentissage')['body']
        self.assertIn('Ce qu’AxiorHub retient',learning);self.assertIn('Préférences apprises',learning)

    def test_review_has_one_click_accept_and_optional_learning(self):
        d=Desk(self.f.c)
        record_job_result(d,{'id':2392,'kind':'prepare_document_project'},
          {'matter':'DOS-001'},'done',{'status':'pending'})
        oid=d.db.execute('SELECT id FROM production_outputs_v390 WHERE job_id=2392').fetchone()[0]
        body=self.request('/production',query='output='+oid)['body']
        for value in ('RELECTURE SIMPLE','Accepter','Modifier et apprendre','Consigne réutilisable','Rejeter'):
            self.assertIn(value,body)


if __name__=='__main__':unittest.main()

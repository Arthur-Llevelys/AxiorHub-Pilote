"""Release 3.5 practice paths: traceable ratios, arithmetic and guarded reviews."""
from datetime import datetime, timezone, timedelta
import json
import unittest

from agent.api import dispatch, openapi
from agent.assistance35 import (billing_review, calculate, classify_comparable,
                                coach, comparables, prepare_call, record_call)
from agent.common import Stop
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.workstation import link_invoice_client, unpaid_summary
from agent.web import App
import test_agent as fixtures


class Assistance350Tests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.EngineTests('test_observation_has_no_mail_write');fixture.setUp()
        self.addCleanup(fixture.doCleanups);self.fixture=fixture;self.d=Desk(fixture.c)
        self.mid='DOS-001';self.source='https://www.legifrance.gouv.fr/';self.ref='Référence fictive à contrôler'

    def test_calculations_reject_unofficial_source_and_keep_convention(self):
        base={'matter':self.mid,'source_url':self.source,'source_reference':self.ref}
        result=calculate(self.d,base|{'calculation_type':'simple_interest','principal':'1000,00',
            'annual_rate':'10','start_date':'2025-01-01','end_date':'2026-01-01'})
        self.assertEqual(result['result']['interest'],'100.00')
        self.assertEqual(result['parameters']['days'],365)
        self.assertFalse(result['source']['verified_automatically'])
        self.assertEqual(calculate(self.d,base|{'calculation_type':'rent_indexation','rent':'1000',
            'base_index':'100','new_index':'125'})['result']['new_rent'],'1250.00')
        self.assertEqual(calculate(self.d,base|{'calculation_type':'calendar_days',
            'start_date':'2026-02-28','days':1})['result']['arithmetic_date'],'2026-03-01')
        with self.assertRaises(Stop):
            calculate(self.d,base|{'calculation_type':'simple_interest','principal':'1000',
                'annual_rate':'10','start_date':'2025-01-01','end_date':'2026-01-01',
                'source_url':'https://attacker.example/legifrance.gouv.fr'})
        self.assertEqual(self.fixture.box.appended,[])

    def test_coaching_percentage_is_plan_coverage_and_transcript_is_scrubbed_after_job(self):
        pid='a'*32
        plan={'oral_plan_5':[{'heading':'Contrat signé','message':'Preuve du contrat','source_ids':['doc-a']},
                             {'heading':'Paiement reçu','message':'Vérifier paiement','source_ids':['doc-b']}],
              'likely_questions':[{'question':'Quand ?','source_ids':['doc-a']}]}
        hearing={'preparation':plan,'sources':[{'id':'doc-a','path':'/DOS/contrat.docx','kind':'document'}]}
        self.d.db.execute('''INSERT INTO hearing_projects_v250
          (id,matter,status,instruction,data,preview_hash,challenge_hash,created,expires,decided,result)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(pid,self.mid,'pending','',json.dumps(hearing),'','','','','',''))
        self.d.db.commit()
        speech='Le contrat signé constitue la preuve du contrat.'
        result=coach(self.d,{'hearing_project_id':pid,'speech':speech,
                             'duration_seconds':300,'target_minutes':5})
        self.assertEqual(result['plan_coverage_percent'],50)
        self.assertNotIn(speech,json.dumps(result))
        job=self.d.enqueue('coach_hearing35',{'hearing_project_id':pid,'speech':speech,
                                              'duration_seconds':300,'target_minutes':5})
        for _ in range(3):
            row=self.d.db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()
            if row['status']!='pending':break
            self.d.work_once()
        row=self.d.db.execute('SELECT status,args FROM jobs WHERE id=?',(job,)).fetchone()
        self.assertEqual(row['status'],'done');self.assertNotIn(speech,row['args'])

    def test_comparable_frequency_requires_distinct_verified_judgments(self):
        stamp=self.d.now()
        for i in range(10):
            aid=f'{i+1:064x}';identifier=f'DECISION-{i}'
            self.d.db.execute('''INSERT INTO legal_authorities_v240
              (id,matter,query_id,provider,provider_url,official_url,identifier,ecli,court,
               decision_date,title,exact_excerpt,official_text_sha256,official_text,
               verification_status,verification_reason,retrieved,created)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (aid,self.mid,'','manual','',f'https://www.legifrance.gouv.fr/case/{i}',
               identifier,'','Cour','2025-01-01','','Texte officiel vérifié fictif.','hash',
               'Texte officiel vérifié fictif.','verified','test',stamp,stamp))
            classify_comparable(self.d,{'matter':self.mid,'authority_id':aid,
              'outcome':'favorable' if i<7 else 'unfavorable',
              'similarity_reason':'Faits et procédure suffisamment proches à contrôler',
              'procedural_context':'Cour, instance'})
            if i==8:self.assertIsNone(comparables(self.d,self.mid)['observed_favorable_percent'])
        report=comparables(self.d,self.mid)
        self.assertEqual(report['comparable_count'],10)
        self.assertEqual(report['observed_favorable_percent'],70.0)
        self.assertIn('aucune probabilité',report['meaning'])
        self.assertEqual(dispatch(self.d,'/assistance/comparables/DOS-001','GET')['favorable_count'],7)
        self.assertIn('/assistance/comparables/{matter_id}',openapi('https://example.test')['paths'])
        with self.assertRaises(Stop):classify_comparable(self.d,{'matter':self.mid,
          'authority_id':'f'*64,'outcome':'favorable','similarity_reason':'Comparaison factuelle complète',
          'procedural_context':'Cour'})

    def test_call_and_billing_need_sources_link_and_recent_sync(self):
        with self.assertRaises(Stop):prepare_call(self.d,{'matter':self.mid,'purpose':'Point dossier'})
        idx=DocumentIndex(self.fixture.c['state_dir'])
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?)',
                       (self.mid,'/DOS/contrat.docx','etag',self.d.now(),'Contrat fictif',''))
        idx.db.commit();idx.db.close()
        with self.assertRaises(Stop):prepare_call(self.d,{'matter':self.mid,'purpose':'Point dossier',
                                                        'contact_email':'other@example.test'})
        call=prepare_call(self.d,{'matter':self.mid,'purpose':'Point dossier'})
        self.assertFalse(call['safety']['telephone_call_placed'])
        notes=record_call(self.d,{'call_project_id':call['project_id'],'notes':'Le client demande un rappel.'})
        self.assertFalse(notes['safety']['email_sent'])
        with self.assertRaises(Stop):billing_review(self.d,{'matter':self.mid})
        link_invoice_client(self.d,self.mid,'client-test')
        self.d.db.execute('''INSERT INTO invoice_ninja_cache_v310
          (id,client_id,number,status,amount,balance,due_date,invoice_url,updated,currency)
          VALUES (?,?,?,?,?,?,?,?,?,?)''',('one','client-test','F-1','sent',100,80,'2025-01-01','',self.d.now(),'EUR'))
        self.d.db.execute('''INSERT INTO invoice_ninja_cache_v310
          (id,client_id,number,status,amount,balance,due_date,invoice_url,updated,currency)
          VALUES (?,?,?,?,?,?,?,?,?,?)''',('two','client-test','F-2','sent',100,50,'2025-01-01','',self.d.now(),'USD'))
        self.d.db.execute('INSERT OR REPLACE INTO workstation_refresh_v310 VALUES(?,?,?,?)',
          ('invoice_ninja','ok','',self.d.now()));self.d.db.commit()
        summary=unpaid_summary(self.d,self.mid);self.assertIsNone(summary['balance'])
        self.assertEqual(summary['balance_by_currency'],{'EUR':80.0,'USD':50.0})
        review=billing_review(self.d,{'matter':self.mid})
        self.assertFalse(review['invoice_created']);self.assertEqual(len(review['invoices']),2)
        self.assertEqual(self.fixture.box.appended,[])

    def test_practice_page_and_api_are_reachable_and_escape_user_input(self):
        page=App(None,None).page(self.fixture.c,{'prefix':'/agent-courriel','csrf':'test'},
                                 '/assistance-metier',{'matter':'<script>alert(1)</script>'})
        self.assertIn('ASSISTANCE MÉTIER',page)
        self.assertIn('value="&lt;script&gt;alert(1)&lt;/script&gt;"',page)
        self.assertNotIn('<script>alert(1)</script>',page)
        paths=openapi('https://cabinet.test')['paths']
        self.assertIn('/assistance/coaching',paths)
        job=dispatch(self.d,'/assistance/calculations','POST',
                     {'matter':self.mid,'calculation_type':'calendar_days',
                      'start_date':'2026-01-01','days':2,
                      'source_url':self.source,'source_reference':self.ref})
        self.assertEqual(job['status'],'queued');self.d.work_once()
        result=json.loads(self.d.db.execute('SELECT result FROM jobs WHERE id=?',
                                            (job['job_id'],)).fetchone()['result'])
        self.assertEqual(result['result']['arithmetic_date'],'2026-01-03')


if __name__=='__main__':unittest.main()

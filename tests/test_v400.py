"""Local-first hybrid routing for AxiorHub 5.0.0."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.api import dispatch,openapi
from agent.common import Stop
from agent.desk import Desk
from agent.hybrid400 import choose,preview,record,save_policy,snapshot
from agent.model import Model,routed_config
import test_agent as fixtures
import test_desk


class FakeHTTP:
    fail_external=False
    fail_local=False
    calls=[]
    payloads=[]
    def __init__(self,base,*args,**kwargs):self.base=base;self.headers={}
    def json(self,method,path,payload=None):
        self.__class__.calls.append((self.base,method,path))
        self.__class__.payloads.append((path,payload))
        if path=='/api/show':return {}
        if path=='/models':return {'data':[{'id':'openai/gpt-5'}]}
        if path=='/chat/completions':
            if self.__class__.fail_external:raise Stop('http_429')
            return {'choices':[{'message':{'content':'réponse externe'},'finish_reason':'stop'}],
              'usage':{'prompt_tokens':100,'completion_tokens':20}}
        if path=='/api/chat':
            if self.__class__.fail_local:raise Stop('generation_ia_incomplete')
            return {'done':True,'message':{'content':'réponse locale'}}
        raise AssertionError((method,path))


class Hybrid400(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)
        secret=Path(self.f.c['state_dir'])/'openrouter-test.secret';secret.write_text('test-secret-400\n');secret.chmod(0o600)
        self.f.c.setdefault('ai_providers',{})['openrouter']={
          'id':'openrouter','type':'openrouter','url':'https://openrouter.ai/api/v1',
          'model':'openai/gpt-5','enabled':True,'external_data_allowed':True,
          'secret_file':str(secret),'zdr_required':True,'monthly_budget_usd':10,
          'per_request_budget_usd':1,'input_usd_per_million':1,
          'output_usd_per_million':4,'state_dir':self.f.c['state_dir']}
        self.d.c=self.f.c

    def enable(self):
        return save_policy(self.d,'hybrid','openrouter',65,
          ['legal_analysis','assistant','document_drafting','control'],True,True,120000)

    def tearDown(self):
        FakeHTTP.fail_external=False;FakeHTTP.fail_local=False

    def test_upgrade_policy_is_local_and_never_sends_silently(self):
        decision=choose(self.f.c,'legal_analysis','strategy_analysis',payload={'sources':['x']*30})
        self.assertFalse(decision['external']);self.assertIn('mode_local',decision['reason_codes'])

    def test_complex_legal_work_uses_openrouter_and_simple_work_stays_local(self):
        self.enable()
        complex_decision=choose(self.d.c,'legal_analysis','strategy_analysis',payload={'sources':['x']*25,'text':'x'*35000})
        self.assertTrue(complex_decision['external']);self.assertGreaterEqual(complex_decision['score'],65)
        simple=choose(self.d.c,'mail_triage','triage',payload={'subject':'Bonjour'})
        self.assertFalse(simple['external']);self.assertIn('fonction_locale',simple['reason_codes'])

    def test_missing_specific_consent_keeps_everything_local(self):
        self.f.c['hybrid_routing']={'mode':'hybrid','external_provider':'openrouter','threshold':20,
          'allowed_purposes':['assistant'],'external_client_data_approved':False}
        decision=choose(self.f.c,'assistant','chat',payload={'question':'complexe','sources':['x']*40})
        self.assertFalse(decision['external']);self.assertIn('autorisation_externe_absente',decision['reason_codes'])

    def test_model_routes_and_falls_back_without_losing_the_task(self):
        self.enable();self.d.c.setdefault('model_routing',{})['legal_analysis']={'provider':'openrouter','model':'openai/gpt-5'}
        FakeHTTP.calls=[];FakeHTTP.fail_external=False
        with patch('agent.model.HTTP',FakeHTTP):
            model=Model(routed_config(self.d.c,'legal_analysis'))
            result=model.complete([{'role':'user','content':'x'*35000}],max_tokens=300)
        self.assertEqual(result,'réponse externe');self.assertEqual(model.last_provider,'openrouter')
        FakeHTTP.fail_external=True
        with patch('agent.model.HTTP',FakeHTTP):
            model=Model(routed_config(self.d.c,'legal_analysis'))
            result=model.complete([{'role':'user','content':'x'*35000}],max_tokens=300)
        self.assertEqual(result,'réponse locale');self.assertEqual(model.last_provider,'ollama')
        self.assertTrue(any(x['status']=='local_fallback' for x in snapshot(self.d)['decisions']))

    def test_external_cost_follows_child_model_and_accumulates(self):
        self.enable();self.d.c.setdefault('model_routing',{})['legal_analysis']={'provider':'openrouter','model':'openai/gpt-5'}
        with patch('agent.model.HTTP',FakeHTTP):
            model=Model(routed_config(self.d.c,'legal_analysis'))
            model.complete([{'role':'user','content':'x'*35000}],max_tokens=300)
            self.assertTrue(model.usage_cost_known);self.assertAlmostEqual(model.usage_cost_usd,0.00018)
            model.complete([{'role':'user','content':'x'*35000}],max_tokens=300)
            self.assertAlmostEqual(model.usage_cost_usd,0.00036)

    def test_local_failure_escalates_only_after_all_external_gates(self):
        self.enable();FakeHTTP.fail_local=True;FakeHTTP.payloads=[]
        with patch('agent.model.HTTP',FakeHTTP):
            model=Model(routed_config(self.d.c,'assistant'))
            result=model.complete([{'role':'user','content':'question courte'}],max_tokens=300)
        self.assertEqual(result,'réponse externe')
        self.assertEqual(model.last_provider,'openrouter')
        self.assertTrue(any(x['status']=='external_after_local_failure' for x in snapshot(self.d)['decisions']))

    def test_excluded_matter_and_budget_force_local(self):
        self.enable();self.d.c['hybrid_routing']['excluded_matters']=['DOS-001']
        blocked=choose(self.d.c,'legal_analysis','strategy_analysis',payload={'matter':'DOS-001','text':'x'*35000})
        self.assertFalse(blocked['external']);self.assertIn('dossier_exclu',blocked['reason_codes'])
        self.d.c['hybrid_routing']['excluded_matters']=[]
        self.d.c['ai_providers']['openrouter']['per_request_budget_usd']=0.000001
        blocked=choose(self.d.c,'legal_analysis','strategy_analysis',payload={'text':'x'*35000},max_tokens=7000)
        self.assertFalse(blocked['external']);self.assertIn('plafond_requete',blocked['reason_codes'])

    def test_external_payload_and_preview_are_anonymized(self):
        self.enable();FakeHTTP.payloads=[]
        question=('Client DEMO DOS-001 client@example.test 06 12 34 56 78 '+'x'*35000)
        with patch('agent.model.HTTP',FakeHTTP):
            model=Model(routed_config(self.d.c,'legal_analysis'))
            model.complete([{'role':'user','content':question}],max_tokens=300)
        sent=[p for path,p in FakeHTTP.payloads if path=='/chat/completions'][-1]
        raw=json.dumps(sent,ensure_ascii=False)
        self.assertNotIn('Client DEMO',raw);self.assertNotIn('client@example.test',raw)
        # 5.4.0 : marqueurs numérotés et réversibles
        self.assertRegex(raw,r'\[(PERSONNE|REFERENCE|DOSSIER)_\d+\]');self.assertIn('[COURRIEL_1]',raw);self.assertIn('[TELEPHONE_1]',raw)
        shown=preview(self.d,'legal_analysis','strategy_analysis',question,'DOS-001',300)
        self.assertNotIn('Client DEMO',shown['transmitted_preview'])
        self.assertGreater(sum(shown['redactions'].values()),0)

    def test_external_route_model_is_never_given_to_ollama(self):
        self.enable();self.d.c.setdefault('model_routing',{})['assistant']={
          'provider':'openrouter','model':'openai/gpt-5'}
        cfg=routed_config(self.d.c,'assistant')
        self.assertEqual(cfg['provider_id'],'ollama');self.assertEqual(cfg['model'],'test-local')
        self.assertEqual(cfg['hybrid_external']['model'],'openai/gpt-5')

    def test_routing_log_contains_metrics_but_no_prompt(self):
        self.enable();decision=choose(self.d.c,'assistant','chat',payload={'question':'SECRET-400','sources':['s']*30})
        record(self.d.c,decision,'external_selected')
        raw=json.dumps(snapshot(self.d),ensure_ascii=False)
        self.assertNotIn('SECRET-400',raw);self.assertFalse(snapshot(self.d)['content_logged'])
        columns=[x[1] for x in self.d.db.execute('PRAGMA table_info(hybrid_decisions_v400)')]
        self.assertNotIn('prompt',columns);self.assertNotIn('content',columns);self.assertNotIn('response',columns)

    def test_api_exposes_policy_and_safe_simulation(self):
        spec=openapi('https://agent.example.com');self.assertEqual(spec['info']['version'],'5.6.2')
        self.assertIn('/ai/routing',spec['paths']);self.assertIn('/ai/routing/simulate',spec['paths'])
        self.assertIn('policy',dispatch(self.d,'/ai/routing','GET'))
        result=dispatch(self.d,'/ai/routing/simulate','POST',{'purpose':'assistant','input_characters':1000})
        self.assertIn('score',result)


class Browser400(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_routing_console_is_visible_and_local_by_default(self):
        body=self.request('/routage-hybride')['body']
        for value in ('ROUTAGE HYBRIDE','Local uniquement','Score de complexité',
          'Estimer sans transmettre','Voir avant transmission','app520.css','Version 5.6.2'):
            self.assertIn(value,body)


if __name__=='__main__':unittest.main()

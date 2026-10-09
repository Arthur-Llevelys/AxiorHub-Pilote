"""Provider gateway, protected secrets and Roundcube fallback for 5.0.0."""
import json
import io
import os
from pathlib import Path
from unittest.mock import patch
import unittest
import zipfile

from agent.ai_gateway import (common_completion,public_providers,save_provider,
                              save_route,secret_path)
from agent.common import Stop,load_config,private_json
from agent.desk import Desk
from agent.model import Model,routed_config
from agent.api import dispatch,openapi
from agent.extensions364 import (active_skill_instructions,import_archive,list_items,
  register_item,set_enabled,test_item,validate_endpoint)
import test_agent as fixtures
import test_desk


class Version364(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.f=f;self.d=Desk(f.c)
        self.f.c.setdefault('ai_gateway',{})['roundcube_enabled']=True

    def test_provider_secret_is_separate_masked_and_mode_600(self):
        with patch('agent.model.Model'):
            saved=save_provider(self.d,'mistral','mistral','https://api.mistral.ai/v1',
              'mistral-large-latest','secret-test-123456',True,True)
        path=secret_path(self.d,'mistral')
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        self.assertNotIn('secret-test',json.dumps(self.d.settings('ai:provider:mistral')))
        public=next(x for x in public_providers(self.d.c) if x['id']=='mistral')
        self.assertTrue(public['secret_configured'])
        self.assertNotIn('secret_file',public)
        self.assertNotIn('secret-test',json.dumps(public))

    def test_external_provider_requires_explicit_data_permission(self):
        with self.assertRaisesRegex(Stop,'autorisation_donnees_externes_requise'):
            save_provider(self.d,'openai','openai','https://api.openai.com/v1',
              'gpt-test','secret-test-123456',True,False)

    def test_function_route_is_persisted_and_loaded_by_load_config(self):
        provider={'type':'mistral','url':'https://api.mistral.ai/v1','model':'mistral-large-latest',
          'enabled':True,'external_data_allowed':True,'secret_file':str(secret_path(self.d,'mistral'))}
        secret_path(self.d,'mistral').write_text('secret-test-123456\n');os.chmod(secret_path(self.d,'mistral'),0o600)
        self.d.setting('ai:provider:mistral',provider);self.d.c.setdefault('ai_providers',{})['mistral']=provider
        with patch('agent.model.Model'):
            save_route(self.d,'hearing','mistral','mistral-large-latest')
        config_path=self.f.base/'config.json';private_json(config_path,self.f.c)
        loaded=load_config(config_path)
        loaded['hybrid_routing']={'mode':'manual'}
        cfg=routed_config(loaded,'hearing')
        self.assertEqual(cfg['provider_id'],'mistral')
        self.assertEqual(cfg['model'],'mistral-large-latest')

    def test_openai_compatible_completion_uses_server_secret(self):
        path=secret_path(self.d,'openai');path.write_text('secret-test-123456\n');os.chmod(path,0o600)
        calls=[]
        class HTTP:
            def __init__(self,*args,**kwargs):self.headers={}
            def json(self,method,path,data=None):
                calls.append((method,path,data,dict(self.headers)))
                if path=='/models':return {'data':[{'id':'gpt-test'}]}
                return {'choices':[{'message':{'content':'Réponse contrôlée.'},'finish_reason':'stop'}]}
        cfg={'provider_id':'openai','provider_type':'openai','type':'openai','url':'https://api.openai.com/v1',
             'model':'gpt-test','external_data_allowed':True,'secret_file':str(path),'purpose':'assistant','state_dir':self.f.c['state_dir'],
             'monthly_budget_usd':10,'per_request_budget_usd':1,'input_usd_per_million':1,'output_usd_per_million':1}
        from support567 import authorize_external
        cfg['external_policy_config']=authorize_external(self.f.c)
        with patch('agent.model.HTTP',HTTP):
            result=Model(cfg).complete([{'role':'user','content':'Test'}])
        self.assertEqual(result,'Réponse contrôlée.')
        self.assertTrue(calls[-1][3]['Authorization'].startswith('Bearer '))

    def test_common_roundcube_api_has_fixed_gateway_and_openai_shape(self):
        fake=type('FakeModel',(),{
          '__init__':lambda self,cfg:setattr(self,'cfg',cfg),
          'complete':lambda self,messages,temperature=0,max_tokens=0:'Résumé interne.'})
        with patch('agent.model.Model',fake):
            result=common_completion(self.d,{'action':'summary','messages':[{'role':'user','content':'Courriel test'}]})
        self.assertEqual(result['choices'][0]['message']['content'],'Résumé interne.')
        self.assertEqual(dispatch(self.d,'/ai/providers','GET')['secrets_exposed'],False)
        self.assertIn('/ai/chat/completions',openapi('https://example.test')['paths'])
        audit=self.d.db.execute("SELECT data FROM audit WHERE action='completion_roundcube'").fetchone()
        self.assertNotIn('Courriel test',audit[0])

    def test_unknown_roundcube_action_is_rejected(self):
        with self.assertRaisesRegex(Stop,'action_roundcube_ia_refusee'):
            common_completion(self.d,{'action':'shell','messages':[{'role':'user','content':'x'}]})

    def test_lawve_skill_is_quarantined_reviewed_and_scoped(self):
        item=register_item(self.d,'https://lawve.ai/@cabinet/skill/recherche-prudente',
          'Recherche prudente',purposes=['legal_analysis'],license_name='MIT')
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('recherche/SKILL.md','# Méthode\nLire la décision entière avant de la citer.')
            archive.writestr('recherche/scripts/install.sh','echo interdit')
        imported=import_archive(self.d,item['id'],data.getvalue(),'recherche.zip')
        self.assertEqual(imported['status'],'review_required')
        self.assertIn('recherche/scripts/install.sh',imported['security_review']['blocked_files'])
        self.assertFalse(imported['security_review']['scripts_executed'])
        with self.assertRaisesRegex(Stop,'revue_extension_explicite_requise'):
            set_enabled(self.d,item['id'],True,False)
        set_enabled(self.d,item['id'],True,True)
        text=active_skill_instructions(self.d.c,'legal_analysis')
        self.assertIn('Lire la décision entière',text)
        self.assertEqual(active_skill_instructions(self.d.c,'mail_triage'),'')

    def test_lawve_connector_secret_test_and_activation(self):
        item=register_item(self.d,'https://lawve.ai/@librejustice/connector/librejustice',
          'LibreJustice','https://librejustice.example/mcp','bearer','token-test-123456',True)
        public=next(x for x in list_items(self.d) if x['id']==item['id'])
        self.assertTrue(public['secret_configured'])
        self.assertNotIn('secret_file',public)
        answers=[{'protocolVersion':'2025-06-18','serverInfo':{'name':'test'}},
                 {'tools':[{'name':'search_decisions'},{'name':'get_decision'}]}]
        with patch('agent.extensions364._mcp_call',side_effect=answers):
            result=test_item(self.d,item['id'])
        self.assertEqual(result['status'],'ok');self.assertEqual(result['tools_count'],2)
        enabled=set_enabled(self.d,item['id'],True,True)
        self.assertTrue(enabled['enabled'])
        self.assertNotIn('token-test',json.dumps(dispatch(self.d,'/extensions','GET')))

    def test_lawve_connector_rejects_private_endpoint(self):
        with self.assertRaisesRegex(Stop,'hote_mcp_prive_refuse'):
            register_item(self.d,'https://lawve.ai/@test/connector/prive','Privé',
                          'https://127.0.0.1/mcp')


class Browser364(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_provider_and_function_settings_are_visible(self):
        body=self.request('/parametres',query='tab=ia')['body']
        self.assertIn('save_ai_provider',body)
        self.assertIn('save_ai_route',body)
        self.assertIn('Boutons IA de Roundcube',body)
        self.assertIn('app520.css',body)
        self.assertIn('Version 5.6.23',body)

    def test_lawve_extensions_settings_are_visible(self):
        body=self.request('/parametres',query='tab=extensions')['body']
        self.assertIn('register_lawve_extension',body)
        self.assertIn('https://lawve.ai/fr/connectors',body)
        self.assertIn('Aucune extension Lawve.ai enregistrée',body)
        static=self.request('/static/v364.js')
        self.assertEqual(static['status'],'200 OK')

    def test_roundcube_bundle_contains_server_side_fallback(self):
        root=Path(__file__).resolve().parents[1]/'integrations/roundcube/ai_roundcube_assistant_v8'
        js=(root/'ai_roundcube_assistant.js').read_text()
        php=(root/'ai_roundcube_assistant.php').read_text()
        self.assertIn('plugin.ai_axiorhub_completion',js)
        self.assertIn('repli sécurisé vers AxiorHub',js)
        self.assertIn("register_action(\n            'plugin.ai_axiorhub_completion'",php)
        self.assertNotIn('secret-test',php)


if __name__=='__main__':unittest.main()

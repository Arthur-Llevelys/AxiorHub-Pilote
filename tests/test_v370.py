"""Pertinence, autonomy, assistant dock and public packaging for AxiorHub 5.0.0."""
import json
import io
import os
from pathlib import Path
import tempfile
from urllib.parse import urlencode
from unittest.mock import patch
import unittest

from agent.common import Stop
from agent.desk import Desk
from agent.relevance370 import (AUTOMATION_LEVELS,correction_guidance,delete_rule,
  evaluate_mail,list_rules,quality_snapshot,record_correction,save_rule,set_automation_level)
import test_agent as fixtures
import test_desk


class Relevance370(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups);self.f=f;self.d=Desk(f.c)

    def test_graphical_rule_is_ordered_explainable_and_deletable(self):
        saved=save_rule(self.d,'Plateforme automatique',10,'domain','equals','platform.example.test','review')
        mail=fixtures.mail(sender='robot@platform.example.test')
        match=evaluate_mail(self.d,mail)
        self.assertEqual((match['id'],match['action']),(saved['id'],'review'))
        self.assertEqual(list_rules(self.d)[0]['name'],'Plateforme automatique')
        with self.assertRaisesRegex(Stop,'confirmation_suppression'):
            delete_rule(self.d,saved['id'],'no')
        delete_rule(self.d,saved['id'],'yes');self.assertEqual(list_rules(self.d),[])

    def test_correction_learning_is_scoped_and_requires_explicit_guidance(self):
        record_correction(self.d,'matter','DOS-001','style','Texte initial','Texte corrigé',
          'Employer des phrases plus courtes dans ce dossier.')
        self.assertEqual(len(correction_guidance(self.d,'DOS-001')),1)
        self.assertEqual(correction_guidance(self.d,'OTHER'),[])
        with self.assertRaisesRegex(Stop,'regle_apprentissage_requise'):
            record_correction(self.d,'general','','style','A','B','')

    def test_automation_profiles_never_authorize_external_actions(self):
        for level in AUTOMATION_LEVELS:
            result=set_automation_level(self.d,level)
            self.assertFalse(result['external_actions_authorized'])
            self.assertEqual(self.d.settings('automation:level'),level)
        self.assertTrue(self.d.settings('automation:automatic_mail_drafts_enabled'))

    def test_quality_snapshot_reports_rules_corrections_and_control_switch(self):
        save_rule(self.d,'Client',20,'sender','contains','client','priority')
        record_correction(self.d,'general','','structure','A','B','Toujours présenter une synthèse avant le détail.')
        data=quality_snapshot(self.d)
        self.assertEqual(data['active_mail_rules'],1);self.assertEqual(data['active_corrections'],1)
        self.assertTrue(data['second_model_control'])


class Browser370(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_robot_assistant_is_injected_on_every_section_and_starts_collapsed(self):
        for path in ('/accueil','/taches','/agenda','/dossiers','/qualite','/regles','/mcp'):
            body=self.request(path)['body']
            self.assertIn('id="ws-ai-launcher"',body,path)
            self.assertIn('id="ws-ai-dock" class="ws-ai-dock" hidden',body,path)
            self.assertIn('Dossier facultatif',body,path)
            self.assertIn('id="ws-ai-file"',body,path)
        self.assertIn('v370.js',self.request('/accueil')['body'])

    def test_rules_mcp_and_quality_are_real_server_pages(self):
        rules=self.request('/regles')['body'];mcp=self.request('/mcp')['body'];quality=self.request('/qualite')['body']
        self.assertIn('save_mail_rule370',rules);self.assertIn('record_correction370',rules)
        self.assertIn('set_automation_level370',rules);self.assertIn('register_lawve_extension',mcp)
        self.assertIn('Qualité détaillée',quality);self.assertIn('Contrôles second modèle',quality)


class PublicPackage370(unittest.TestCase):
    def test_docker_and_public_release_files_are_present(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('docker-compose.yml','.env.example','LICENSE','TRADEMARKS.md',
          'DOCKER-INSTALLATION.md','docker/Dockerfile','agent/standalone_auth.py','scripts/privacy-scan.py'):
            self.assertTrue((root/name).is_file(),name)
        compose=(root/'docker-compose.yml').read_text()
        self.assertIn('8626',compose);self.assertIn('no-new-privileges',compose)
        env=(root/'.env.example').read_text();self.assertIn('https://agent.example.com',env)
        self.assertNotIn('replace-with-a-real-production-secret-value',env)

    def test_agpl_trademark_and_logo_boundaries_are_explicit(self):
        root=Path(__file__).resolve().parents[1]
        self.assertIn('GNU AFFERO GENERAL PUBLIC LICENSE', (root/'LICENSE').read_text())
        marks=(root/'TRADEMARKS.md').read_text()
        self.assertIn('ne concède aucun droit',marks);self.assertIn('AGPL',marks)
        self.assertIn('sans modification',(root/'LOGO-LICENSE.md').read_text())

    def test_standalone_signup_login_and_secure_session(self):
        from agent.standalone_auth import StandaloneAuth
        seen=[]
        def inner(env,start):
            seen.append(env.get('HTTP_AUTHORIZATION'));start('200 OK',[('Content-Type','text/plain')]);return [b'workspace']
        old=os.environ.get('AXIORHUB_ALLOW_SIGNUP');old_token=os.environ.get('AXIORHUB_INTERNAL_API_TOKEN')
        os.environ['AXIORHUB_ALLOW_SIGNUP']='true';os.environ['AXIORHUB_INTERNAL_API_TOKEN']='test-internal-token'
        self.addCleanup(lambda: os.environ.__setitem__('AXIORHUB_ALLOW_SIGNUP',old) if old is not None else os.environ.pop('AXIORHUB_ALLOW_SIGNUP',None))
        self.addCleanup(lambda: os.environ.__setitem__('AXIORHUB_INTERNAL_API_TOKEN',old_token) if old_token is not None else os.environ.pop('AXIORHUB_INTERNAL_API_TOKEN',None))
        with tempfile.TemporaryDirectory() as tmp:
            app=StandaloneAuth(inner,tmp,'https://agent.example.com')
            def call(path,method='GET',data=None,cookie=''):
                raw=urlencode(data or {}).encode();capture={}
                env={'PATH_INFO':path,'REQUEST_METHOD':method,'QUERY_STRING':'','HTTP_COOKIE':cookie,
                  'CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)}
                body=b''.join(app(env,lambda status,headers:capture.update(status=status,headers=headers)))
                return capture,body
            call('/signup','POST',{'email':'lawyer@example.com','password':'a-long-password-123'})
            response,_=call('/login','POST',{'email':'lawyer@example.com','password':'a-long-password-123'})
            cookie=next(v for k,v in response['headers'] if k=='Set-Cookie')
            self.assertIn('HttpOnly',cookie);self.assertIn('Secure',cookie);self.assertIn('SameSite=Lax',cookie)
            session=cookie.split(';',1)[0]
            response,body=call('/',cookie=session)
            self.assertEqual(body,b'workspace');self.assertTrue(seen[-1].startswith('Bearer '))


if __name__=='__main__':unittest.main()

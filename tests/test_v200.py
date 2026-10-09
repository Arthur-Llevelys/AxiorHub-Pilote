import json
from pathlib import Path
import unittest

import test_agent as fixtures
import test_desk
from agent.api import dispatch, openapi
from agent.common import Stop
from agent.desk import Desk
from agent.supervision import approve, propose, reject, requests


class Supervision200Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.model.intent='legal';self.mail=fixtures.mail();self.key=self.f.engine.process(self.mail)
        self.d=Desk(self.f.c)

    def test_proposal_does_not_queue_or_deposit_and_requires_one_time_code(self):
        before=self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        item=propose(self.d,{'action':'prepare_reply','mail_key':self.key,
          'instruction':'Préparer une réponse prudente.'})
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],before)
        self.assertEqual(self.f.box.appended,[]);self.assertRegex(item['confirmation_code'],r'^\d{6}$')
        with self.assertRaisesRegex(Stop,'confirmation_supervisee_invalide'):
            approve(self.d,{'approval_id':item['approval_id'],'confirmation_code':'000000'})
        accepted=approve(self.d,{'approval_id':item['approval_id'],
          'confirmation_code':item['confirmation_code']})
        self.assertEqual(accepted['status'],'approved')
        self.assertEqual(self.d.db.execute('SELECT kind FROM jobs WHERE id=?',(accepted['job_id'],)).fetchone()[0],'prepare_reply')
        with self.assertRaisesRegex(Stop,'deja_traitee'):
            approve(self.d,{'approval_id':item['approval_id'],
              'confirmation_code':item['confirmation_code']})

    def test_rejection_closes_request_without_job(self):
        before=self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        item=propose(self.d,{'action':'prepare_reply','mail_key':self.key,'instruction':'Répondre.'})
        reject(self.d,{'approval_id':item['approval_id']})
        self.assertEqual(requests(self.d,'rejected')[0]['id'],item['approval_id'])
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],before)

    def test_api_exposes_capabilities_and_supervision_but_no_send(self):
        spec=openapi('https://cabinet.test');self.assertEqual(spec['info']['version'],'5.6.23')
        limits=dispatch(self.d,'/capabilities','GET')
        self.assertIn('send_email',limits['never']);self.assertNotIn('send_email',limits['can'])
        item=dispatch(self.d,'/supervision/drafts','POST',{'mail_key':self.key,'instruction':'Répondre.'})
        self.assertEqual(dispatch(self.d,'/supervision','GET')['requests'][0]['id'],item['approval_id'])
        with self.assertRaisesRegex(Stop,'utiliser_supervision_brouillon'):
            dispatch(self.d,'/drafts','POST',{'mail_key':self.key,'instruction':'Contourner.'})

    def test_openwebui_tool_waits_and_returns_conversation_source_contract(self):
        source=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('def _wait(',source);self.assertIn('demander_une_analyse_axiorhub',source)
        self.assertIn('continuer_une_conversation',source);self.assertIn('confirmation_code',source)
        self.assertNotIn('def envoyer_',source);self.assertNotIn('subprocess',source)
        prompt=(Path(__file__).parents[1]/'integrations/openwebui/SYSTEM-PROMPT-AXIORHUB.md').read_text()
        self.assertIn('attends son message suivant',prompt);self.assertIn('Ordali',prompt)


class Web200Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_openwebui_shortcut_opens_the_working_application(self):
        response=self.request('/openwebui')
        self.assertEqual(response['status'],'302 Found')
        self.assertEqual(response['headers']['Location'],'https://ai.example.com/')


if __name__=='__main__':unittest.main()

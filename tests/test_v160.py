import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_desk
from agent.common import private_json
from agent.desk import Desk
from agent.index import DocumentIndex
from agent.integration import (cabinet_search, set_work_state, submit_question,
                               sync_work_items, thread_messages, work_items)
from agent.state import State
from agent.workspace import chat


class Workflow160Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.d=Desk(self.f.c)

    def report(self,reason='correspondant_ou_dossier_a_confirmer'):
        key='a'*64;state=State(self.f.c['state_dir'])
        state.set(key,'mid','thread','review',reason)
        state.report(key,{'subject':'Demande client','sender':'client@example.test',
                          'received_at':'2026-09-09T09:00:00+00:00','reason':reason,
                          'matter':'DOS-001'})
        return key

    def test_business_state_moves_immediately_and_terminal_state_stays_closed(self):
        key=self.report();sync_work_items(self.d)
        self.assertEqual(work_items(self.d)[0]['state'],'needs_confirmation')
        set_work_state(self.d,key,'processing','association_confirmee')
        self.assertEqual(work_items(self.d)[0]['state'],'processing')
        set_work_state(self.d,key,'handled','aucune_reponse')
        sync_work_items(self.d)
        self.assertEqual(work_items(self.d)[0]['state'],'handled')

    def test_user_job_precedes_maintenance_and_both_can_be_cancelled(self):
        maintenance=self.d.enqueue('index',{'matter':'DOS-001'})
        user=self.d.enqueue('assistant_answer',{'message':1,'thread':'b'*32})
        self.assertLess(self.d.db.execute('SELECT priority FROM jobs WHERE id=?',(user,)).fetchone()[0],
                        self.d.db.execute('SELECT priority FROM jobs WHERE id=?',(maintenance,)).fetchone()[0])
        self.assertEqual(self.d.cancel_job(maintenance)['status'],'cancelled')

    def test_general_conversation_searches_several_matters_with_sources(self):
        self.f.c['rag']={'enabled':False}
        other={'id':'DOS-002','path':'/Dossiers/AUTRE','client_name':'Autre client',
               'correspondents':[],'registered_at':'2026-09-09'}
        private_json(self.f.matter_file,self.f.matters+[other])
        index=DocumentIndex(self.f.c['state_dir'])
        index.put_source('DOS-001','/Dossiers/DEMO/cgv.docx','Conditions générales e-commerce','1','2026-09-08','document')
        index.put_source('DOS-002','/Dossiers/AUTRE/vente.txt','Conditions de vente boutique en ligne','2','2026-09-07','document')
        model=self.model_factory()
        result=chat(self.d,{'question':'Où sont les conditions de vente e-commerce ?'},None,model,return_result=True)
        self.assertEqual(len({s['matter'] for s in result['sources']}),2)
        self.assertEqual(len(model.calls[0]['sources']),2)

    @staticmethod
    def model_factory():
        class Model:
            calls=[]
            def ask(self,stage,payload):
                self.calls.append(copy.deepcopy(payload))
                return {'answer':'Résultats multi-dossiers.','source_ids':[s['id'] for s in payload['sources']],
                        'limits':['Index non exhaustif.'],'proposed_actions':[]}
        return Model()

    def test_question_is_visible_before_llm_worker_answers(self):
        submitted=submit_question(self.d,'Question générale')
        messages=thread_messages(self.d,submitted['thread_id'])
        self.assertEqual(messages[0]['content'],'Question générale')
        self.assertEqual(messages[0]['status'],'queued')
        self.assertEqual(self.d.db.execute('SELECT priority FROM jobs WHERE id=?',(submitted['job_id'],)).fetchone()[0],0)


class Web160Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_dashboard_and_general_assistant_are_first_class_views(self):
        dashboard=self.request('/dashboard')['body'];assistant=self.request('/assistant')['body']
        self.assertIn('Dossiers récemment actifs',dashboard)
        self.assertIn('Tableau de bord',dashboard)
        self.assertIn('Recherche générale dans le cabinet',assistant)
        self.assertIn('name="question"',assistant)
        self.assertNotIn('select required name="matter"',assistant)

    def test_assistant_submission_redirects_to_visible_thread(self):
        response=self.request('/action','POST',{'csrf':'test-csrf','action':'assistant_ask',
            'question':'Dans quels dossiers ai-je des CGV ?'},origin=self.origin)
        self.assertTrue(response['status'].startswith('303'))
        location=response['headers']['Location'];self.assertIn('thread=',location)
        query=location.split('?',1)[1]
        page=self.request('/assistant',query=query)['body']
        self.assertIn('Dans quels dossiers ai-je des CGV ?',page)
        self.assertIn('En file d’attente prioritaire',page)

    def test_bearer_api_exposes_openapi_and_rejects_wrong_token(self):
        auth=json.loads(self.auth.read_text());token='token-test-'+'x'*40
        auth['api_token_sha256']=hashlib.sha256(token.encode()).hexdigest();private_json(self.auth,auth)
        self.app=__import__('agent.web',fromlist=['App']).App(str(self.config),str(self.auth))
        def call(value):
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/agent-courriel/api/v1/openapi.json','QUERY_STRING':'',
                 'HTTP_HOST':'cabinet.example.test','HTTP_X_FORWARDED_PROTO':'https','wsgi.url_scheme':'http',
                 'wsgi.input':io.BytesIO(b''),'CONTENT_LENGTH':'0','CONTENT_TYPE':'application/json',
                 'HTTP_AUTHORIZATION':'Bearer '+value}
            result={}
            body=b''.join(self.app(env,lambda status,headers:result.update(status=status,headers=dict(headers)))).decode()
            return result,body
        bad,_=call('wrong');good,body=call(token)
        self.assertTrue(bad['status'].startswith('401'))
        self.assertTrue(good['status'].startswith('200'))
        self.assertEqual(json.loads(body)['info']['version'],'5.6.11')


class OpenWebUI160Tests(unittest.TestCase):
    def test_tool_is_bounded_and_has_no_send_or_shell_capability(self):
        text=(Path(__file__).parents[1]/'integrations/openwebui/axiorhub_tool.py').read_text()
        self.assertIn('preparer_un_brouillon_de_reponse',text)
        self.assertNotIn('subprocess',text)
        self.assertNotIn('smtp',text.lower())
        self.assertNotIn('envoyer_un_courriel',text)


if __name__=='__main__':unittest.main()

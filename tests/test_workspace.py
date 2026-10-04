import copy
import json
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_desk
from agent.common import Stop,private_json,load_matters
from agent.desk import Desk
from agent.portfolio import set_matter_state
from agent.workspace import discover,browse,register,chat,chat_scope,history


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.d=Desk(self.f.c)

    def test_discovery_resumes_past_batch_limit_and_below_old_depth_limit(self):
        class Dav:
            def list_folder(self,path):
                if path=='/Dossiers':return [{'path':f'/Dossiers/Container{i}','directory':True} for i in range(40)]
                if path=='/Dossiers/Container39':return [{'path':path+'/Deep','directory':True}]
                if path.endswith('/Deep'):return [{'path':path+'/VeryDeep','directory':True}]
                if path.endswith('/VeryDeep'):return [{'path':path+'/CLIENT - 2026090901','directory':True}]
                return []
        first=discover(self.d,Dav());self.assertTrue(first['parcours_partiel'])
        for _ in range(5):
            result=discover(Desk(self.f.c),Dav())
            if not result['parcours_partiel']:break
        self.assertFalse(result['parcours_partiel'])
        matter=next(m for m in load_matters(self.f.c) if m['id']=='2026090901')
        self.assertIn('/VeryDeep/',matter['path'])
        self.assertEqual(matter['correspondents'],[])

    def test_incomplete_discovery_enqueues_next_batch(self):
        self.d.enqueue('discover')
        with patch.object(self.d,'perform',return_value={'parcours_partiel':True}):self.d.work_once()
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='discover' AND status='pending'").fetchone()[0],1)

    def test_named_folder_is_catalogued_without_becoming_a_client_automatically(self):
        class Dav:
            def list_folder(self,p):return [{'path':'/Dossiers/Client sans numéro','directory':True}]
        browse(self.d,{'path':'/Dossiers'},Dav())
        self.assertEqual(len(load_matters(self.f.c)),1)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM directories WHERE path='/Dossiers/Client sans numéro'").fetchone()[0],1)

    def test_explicit_registration_verifies_path_and_queues_index(self):
        class Dav:
            def list_folder(self,p):self.path=p;return []
        dav=Dav();args={'path':'/Dossiers/Client sans numéro','reference':'CASE-NEW','client_name':'Client Nouveau'}
        result=register(self.d,args,dav)
        self.assertEqual(result['dossier'],'CASE-NEW')
        self.assertEqual(dav.path,args['path'])
        self.assertEqual(register(self.d,args,dav)['etat'],'deja_enregistre')
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='index'").fetchone()[0],1)

    def test_registration_rejects_unreadable_directory_or_duplicate_reference(self):
        class Dav:
            def list_folder(self,p):raise Stop('http_404')
        with self.assertRaisesRegex(Stop,'http_404'):
            register(self.d,{'path':'/Dossiers/Missing','reference':'NEW','client_name':'N'},Dav())
        with self.assertRaisesRegex(Stop,'reference_deja_utilisee'):
            register(self.d,{'path':'/Dossiers/Other','reference':'DOS-001','client_name':'N'},Dav())
        self.assertEqual(len(load_matters(self.f.c)),1)

    def test_browser_paths_cannot_escape_authorized_roots(self):
        for p in ['/Autre/Client','/Dossiers/../Autre','/Dossiers/%2e%2e/Autre','/Dossiers/.private','2026090901','/Dossiers/secrets']:
            with self.subTest(path=p),self.assertRaises(Stop):browse(self.d,{'path':p},None)
        with self.assertRaises(Stop):register(self.d,{'path':'/Dossiers','reference':'ROOT','client_name':'Cabinet'},None)

    def model(self,fabricated=False):
        class Model:
            calls=[]
            def ask(self,stage,payload):
                self.calls.append(copy.deepcopy(payload))
                return {'answer':'Le contrat est en relecture.','source_ids':['absente'] if fabricated else [s['id'] for s in payload['sources']],'limits':['Extraits seulement.']}
        return Model()

    def test_chat_uses_only_selected_matter_and_keeps_sources(self):
        from agent.index import DocumentIndex
        index=DocumentIndex(self.f.c['state_dir'])
        other={'id':'OTHER','path':'/Dossiers/Other','client_name':'Other','correspondents':[]}
        self.f.matters.append(other);private_json(self.f.matter_file,self.f.matters)
        class OtherDav(fixtures.FakeDAV):
            def download(self,item):return b'SECRET_OTHER_CLIENT'
        index.sync(OtherDav(),other,{})
        model=self.model();result=chat(self.d,{'matter':'DOS-001','question':'Résume le contrat'},self.f.dav,model)
        self.assertFalse(result['brouillon_imap_cree'])
        payload=model.calls[0]
        self.assertNotIn('SECRET_OTHER_CLIENT',json.dumps(payload))
        self.assertEqual(payload['sources'][0]['path'],'/Dossiers/DEMO/etat.txt')
        scope,_,_=chat_scope(self.f.c,{'matter':'DOS-001'})
        self.assertEqual(len(history(self.d,scope)),1)
        other_scope,_,_=chat_scope(self.f.c,{'matter':'OTHER'})
        self.assertEqual(len(history(self.d,other_scope)),0)
        self.assertEqual(self.f.box.appended,[])

    def test_changed_scope_invalidates_history(self):
        scope,_,_=chat_scope(self.f.c,{'matter':'DOS-001'})
        other=copy.deepcopy(self.f.c);other['mail']['username']='other@example.test'
        self.assertNotEqual(chat_scope(other,{'matter':'DOS-001'})[0],scope)
        self.f.matters[0]['path']='/Dossiers/Moved';private_json(self.f.matter_file,self.f.matters)
        self.assertNotEqual(chat_scope(self.f.c,{'matter':'DOS-001'})[0],scope)

    def test_invented_source_does_not_save_answer(self):
        with self.assertRaisesRegex(Stop,'source_conversation_invalide'):
            chat(self.d,{'matter':'DOS-001','question':'Résume'},self.f.dav,self.model(True))
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0],0)

    def test_revoked_nextcloud_access_prevents_chat_using_cached_document(self):
        args={'matter':'DOS-001','question':'Résume'}
        chat(self.d,args,self.f.dav,self.model())
        class Denied:
            def inventory(self,p):raise Stop('http_403')
        model=self.model()
        with self.assertRaises(Stop):chat(self.d,args,Denied(),model)
        self.assertEqual(model.calls,[])

    def test_selected_mail_uses_identity_check_and_does_not_mark_seen_or_append(self):
        mail=fixtures.mail();self.f.model.intent='legal';key=self.f.engine.process(mail)
        self.f.box.inputs={mail.uid:mail}
        model=self.model();chat(self.d,{'key':key,'question':'Prépare une demande de précision'},None,model,self.f.box)
        self.assertEqual(model.calls[0]['sources'][0]['id'],'incoming')
        self.assertEqual(self.f.box.appended,[]);self.assertNotIn('\\Seen',mail.flags)
        mail.uidvalidity='changed'
        with self.assertRaisesRegex(Stop,'identite_imap_modifiee'):
            chat(self.d,{'key':key,'question':'Suite ?'},None,model,self.f.box)

    def test_email_from_other_matter_cannot_enter_context(self):
        key=self.f.engine.process(fixtures.mail())
        self.f.matters.append({'id':'OTHER','path':'/Dossiers/Other','client_name':'Other','correspondents':[]})
        private_json(self.f.matter_file,self.f.matters)
        with self.assertRaisesRegex(Stop,'courriel_non_associe'):
            chat_scope(self.f.c,{'matter':'OTHER','key':key})

    def test_forget_only_selected_conversation(self):
        args={'matter':'DOS-001','question':'Résume'}
        chat(self.d,args,self.f.dav,self.model())
        self.d.perform('forget_chat',{'matter':'DOS-001'})
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM conversations').fetchone()[0],0)

    def test_index_all_batches_without_indexing_legacy_containers(self):
        matters=[{'id':f'CASE-{i:02}','path':f'/Dossiers/C{i}','client_name':f'C{i}',
                  'correspondents':[],'registered_at':'2026-09-09'} for i in range(14)]
        matters.append({'id':'CONTAINER','path':'/Dossiers','client_name':'Racine','correspondents':[]})
        private_json(self.f.matter_file,matters)
        for matter in matters[:-1]:
            set_matter_state(self.d,{'matter':matter['id'],'state':'active'})
        result=self.d.perform('index_all',{})
        self.assertEqual(result['dossiers_mis_en_attente'],10);self.assertTrue(result['suite'])
        result=self.d.perform('index_all',{'after':result['after']})
        self.assertEqual(result['dossiers_mis_en_attente'],4);self.assertFalse(result['suite'])
        rows=self.d.db.execute('SELECT args FROM jobs').fetchall()
        self.assertNotIn('CONTAINER',json.dumps([r[0] for r in rows]))

    def test_finished_chat_does_not_keep_question_copy_in_job_log(self):
        self.d.enqueue('chat',{'matter':'DOS-001','question':'CONFIDENTIEL QUESTION'})
        with patch.object(self.d,'perform',return_value={'reponse':'disponible'}):self.d.work_once()
        self.assertNotIn('CONFIDENTIEL',self.d.db.execute('SELECT args FROM jobs').fetchone()[0])


# Reuse only fixture helpers; do not duplicate the existing test suite.
class WorkspaceWebTests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_native_form_policy_preserves_origin_and_null_remains_rejected(self):
        response=self.request('/')
        self.assertEqual(response['headers']['Referrer-Policy'],'same-origin')
        for origin in ('null',None,'https://evil.example','https://cabinet.example.test.evil.example'):
            r=self.request('/action','POST',{'action':'sync','csrf':'test-csrf'},origin=origin)
            self.assertTrue(r['status'].startswith('400'))
        self.assertEqual(Desk(self.f.c).db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)

    def test_all_button_actions_pass_valid_origin_and_csrf_to_queue(self):
        actions=['run','sync','discover','learn','index','retry_matter','review','retry','approve','reject','browse','register_matter','chat','forget_chat']
        for action in actions:
            r=self.request('/action','POST',{'action':action,'csrf':'test-csrf','matter':'DOS-001'},origin=self.origin)
            self.assertTrue(r['status'].startswith('303'),action)
        self.assertEqual(Desk(self.f.c).db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],len(actions))

    def test_assistant_requires_auth_and_exposes_scope_and_form(self):
        self.assertTrue(self.request('/assistant',auth=False)['status'].startswith('401'))
        r=self.request('/assistant',query='matter=DOS-001')
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('name="question"',r['body'])
        self.assertIn('/Dossiers/DEMO',r['body'])
        self.assertIn('ne dépose aucun brouillon',r['body'])

    def test_folder_view_exposes_registration_without_terminal(self):
        r=self.request('/dossiers')
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('Vérifier et enregistrer le dossier',r['body'])
        self.assertIn('Créer une nouvelle affaire',r['body'])

    def test_chat_redirect_returns_to_same_scope_and_escapes_output(self):
        d=Desk(self.f.c);scope,_,_=chat_scope(self.f.c,{'matter':'DOS-001'})
        d.db.execute("INSERT INTO conversations VALUES(NULL,?,?,?,?,?)",(scope,'<script>x</script>',json.dumps({'answer':'<img src=x onerror=alert(1)>','source_ids':[],'limits':[]}), '[]','2026-09-09T00:00:00+00:00'));d.db.commit()
        r=self.request('/assistant',query='matter=DOS-001')
        self.assertNotIn('<script>',r['body']);self.assertIn('&lt;script&gt;',r['body'])
        self.assertNotIn('<img src=x onerror=alert(1)>',r['body'])
        r=self.request('/action','POST',{'action':'chat','csrf':'test-csrf','matter':'DOS-001','question':'Suite ?'},origin=self.origin)
        self.assertIn('/assistant?',r['headers']['Location'])
        self.assertIn('matter=DOS-001',r['headers']['Location'])

    def test_real_http_form_post_queues_job_and_preserves_policy(self):
        import base64
        import http.client
        import threading
        from urllib.parse import urlencode
        from wsgiref.simple_server import make_server,WSGIRequestHandler
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,self.app,handler_class=Quiet)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            headers={'Host':'cabinet.example.test','X-Forwarded-Proto':'https',
                'Authorization':'Basic '+base64.b64encode(('admin:'+self.password).encode()).decode(),
                'Origin':self.origin,'Content-Type':'application/x-www-form-urlencoded'}
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            connection.request('POST','/agent-courriel/action',urlencode({'csrf':'test-csrf','action':'discover'}),headers)
            response=connection.getresponse();response.read()
            self.assertEqual(response.status,303)
            self.assertEqual(response.getheader('Referrer-Policy'),'same-origin')
            self.assertIn('/dossiers?',response.getheader('Location'))
            connection.close()
            self.assertEqual(Desk(self.f.c).db.execute("SELECT COUNT(*) FROM jobs WHERE kind='discover'").fetchone()[0],1)
        finally:
            server.shutdown();thread.join();server.server_close()


if __name__=='__main__':unittest.main()

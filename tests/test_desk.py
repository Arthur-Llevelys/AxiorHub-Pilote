import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

from agent.common import Stop, load_matters, private_json
from agent.desk import Desk, run_lock, report_for
from agent.web import App
import test_agent as fixtures


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.desk=Desk(self.f.c)

    def test_discovery_finds_cases_below_existing_container_without_duplicates(self):
        self.f.c['nextcloud']['roots']=['/Dossiers','/Dossiers/01 - Dossiers']
        class Dav:
            def list_folder(self,path):
                return {'/Dossiers':[{'path':'/Dossiers/01 - Dossiers','directory':True}],
                        '/Dossiers/01 - Dossiers':[{'path':'/Dossiers/01 - Dossiers/CLIENT-A - ADVERSE-A - 2025060301','directory':True}]}[path]
        result=self.desk.discover(Dav())
        self.assertEqual(result['dossiers_decouverts'],1)
        self.assertEqual(self.desk.discover(Dav())['dossiers_decouverts'],0)
        new=next(m for m in load_matters(self.f.c) if m['id']=='2025060301')
        self.assertEqual(new['correspondents'],[])

    def test_discovery_id_collision_never_overwrites_existing_case(self):
        self.f.matters[0]['id']='2025060301';private_json(self.f.matter_file,self.f.matters)
        class Dav:
            def list_folder(self,path):
                return [{'path':'/Dossiers/OTHER - 2025060301','directory':True}]
        self.desk.discover(Dav())
        rows=load_matters(self.f.c)
        self.assertEqual(len(rows),2)
        self.assertEqual(next(m for m in rows if m['id']=='2025060301')['path'],'/Dossiers/DEMO')

    def test_mail_proposals_are_evidence_only_until_human_approval(self):
        mail=fixtures.mail(sender='new@example.test');box=self.f.box
        box.search=lambda *a:[mail.uid];box.fetch=lambda *a,**kw:mail
        self.desk.scan_mail(box,self.f.matters)
        p=self.desk.db.execute('SELECT * FROM proposals').fetchone()
        self.assertIsNotNone(p)
        self.assertIn('DOS-001',json.loads(p['evidence'])[0]['indice'])
        self.assertFalse(any(x['email']=='new@example.test' for x in load_matters(self.f.c)[0]['correspondents']))
        with self.assertRaises(Stop):
            self.desk.associate({'matter':'DOS-001','email':'new@example.test','role':''})
        self.desk.associate({'matter':'DOS-001','email':'new@example.test','role':'tiers','proposal':p['key']})
        self.assertIn({'email':'new@example.test','role':'tiers'},load_matters(self.f.c)[0]['correspondents'])
        self.assertEqual(json.loads(self.f.matter_file.read_text()),self.f.matters)

    def test_rejected_evidence_stays_rejected_after_rescan(self):
        m=self.f.matters[0]
        self.desk.propose(m,'new@example.test',{'type':'document','source':'demo','indice':'demo'})
        key=self.desk.db.execute('SELECT key FROM proposals').fetchone()[0]
        self.desk.perform('reject',{'key':key})
        self.desk.propose(m,'new@example.test',{'type':'courriel','source':'mail','indice':'demo'})
        self.assertEqual(self.desk.db.execute('SELECT status FROM proposals').fetchone()[0],'rejected')

    def test_registry_never_accepts_unknown_role_or_multiple_addresses(self):
        for fields in [{'email':'new@example.test','role':'admin'},
                       {'email':'one@example.test,two@example.test','role':'client'}]:
            with self.assertRaises(Stop):self.desk.associate({'matter':'DOS-001',**fields})

    def test_worker_waits_for_existing_mail_processing_lock(self):
        jid=self.desk.enqueue('associate',{'matter':'DOS-001','email':'new@example.test','role':'tiers'})
        with run_lock(self.f.c):self.assertFalse(self.desk.work_once())
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'pending')
        self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'done')

    def test_queue_deduplicates_and_rejects_shell_action(self):
        self.assertEqual(self.desk.enqueue('sync'),self.desk.enqueue('sync'))
        with self.assertRaises(Stop):self.desk.enqueue('shell',{'command':'echo private'})

    def test_index_continues_automatically_in_bounded_batches(self):
        self.desk.enqueue('index',{'matter':'DOS-001'})
        with patch.object(self.desk,'perform',return_value={'fichiers':45,'en_attente':25}):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND kind='index'").fetchone()[0],1)
        with patch.object(self.desk,'perform',return_value={'fichiers':45,'en_attente':0}):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0],0)

    def review_report(self, mail=None):
        mail=mail or fixtures.mail()
        self.f.model.intent='legal'
        key=self.f.engine.process(mail)
        self.f.box.inputs={mail.uid:mail}
        return key,mail

    def test_retry_checks_unread_and_identity_before_reservation(self):
        key,mail=self.review_report()
        mail.flags.add('\\Seen')
        self.assertEqual(self.desk.retry([key],self.f.box)['messages_remis_en_attente'],0)
        self.assertEqual(self.f.status(key),'review')
        mail.flags.clear();mail.uidvalidity='88'
        self.assertEqual(self.desk.retry([key],self.f.box)['messages_remis_en_attente'],0)
        mail.uidvalidity='7'
        self.assertEqual(self.desk.retry([key],self.f.box)['messages_remis_en_attente'],1)
        self.assertEqual(self.f.status(key),'retry')
        self.assertEqual(self.f.box.appended,[])

    def test_retry_never_repeats_written_draft(self):
        key=self.f.engine.process(fixtures.mail())
        self.assertEqual(self.desk.retry([key],self.f.box)['messages_remis_en_attente'],0)

    def test_internal_review_is_not_an_imap_draft_or_training_example(self):
        key,mail=self.review_report()
        class Model:
            def ask(self,stage,data):
                assert stage=='desk_review'
                return {'summary':'Le client demande une décision.','decisions':['Choisir la suite.'],
                        'questions':['Quel document est à jour ?'],'partial_reply':'Pouvez-vous préciser votre demande ?',
                        'source_ids':['incoming'],'limits':['Pièces non lues.']}
        result=self.desk.internal_review(key,self.f.box,Model())
        self.assertFalse(result['brouillon_imap_cree'])
        self.assertEqual(self.f.box.appended,[])
        self.assertEqual(self.f.status(key),'review')
        self.assertIsNotNone(self.desk.db.execute('SELECT data FROM notes WHERE key=?',(key,)).fetchone())

    def test_internal_review_rejects_invented_citation(self):
        key,mail=self.review_report()
        class Model:
            def ask(self,*a):
                return {'summary':'Test','decisions':[],'questions':[],'partial_reply':'',
                        'source_ids':['another-client-document'],'limits':[]}
        with self.assertRaises(Stop):self.desk.internal_review(key,self.f.box,Model())
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0],0)

    def test_other_account_report_is_not_read(self):
        key,mail=self.review_report()
        other=copy.deepcopy(self.f.c);other['mail']['username']='another@example.test'
        with self.assertRaises(Stop):report_for(other,key)


class WebTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.config=self.f.base/'config.json';private_json(self.config,self.f.c)
        self.auth=self.f.base/'auth.json'
        salt='aa'*16
        self.password='password-test-only-long'
        self.origin='https://cabinet.example.test'
        private_json(self.auth,{'username':'admin','salt':salt,
            'hash':hashlib.scrypt(self.password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex(),
            'origin':self.origin,'prefix':'/agent-courriel','csrf':'test-csrf'})
        self.app=App(str(self.config),str(self.auth))

    def request(self,path='/',method='GET',form=None,auth=True,origin=None,query=''):
        raw=urlencode(form or {}).encode()
        env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,
             'HTTP_HOST':'cabinet.example.test','HTTP_X_FORWARDED_PROTO':'https',
             'wsgi.url_scheme':'http','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),
             'CONTENT_TYPE':'application/x-www-form-urlencoded'}
        if auth:env['HTTP_AUTHORIZATION']='Basic '+base64.b64encode(('admin:'+self.password).encode()).decode()
        if origin:env['HTTP_ORIGIN']=origin
        result={}
        def start(status,headers):result.update(status=status,headers=dict(headers))
        result['body']=b''.join(self.app(env,start)).decode()
        return result

    def test_all_four_views_require_authentication(self):
        for path in ['/','/associations','/dossiers','/memoire']:
            self.assertTrue(self.request(path,auth=False)['status'].startswith('401'))
            self.assertTrue(self.request(path)['status'].startswith('200'))

    def test_post_requires_origin_and_csrf(self):
        for csrf,origin in [('',self.origin),('test-csrf','https://evil.example'),('test-csrf',None)]:
            r=self.request('/action','POST',{'action':'sync','csrf':csrf},origin=origin)
            self.assertTrue(r['status'].startswith('400'))
        self.assertEqual(Desk(self.f.c).db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
        good=self.request('/action','POST',{'action':'sync','csrf':'test-csrf'},origin=self.origin)
        self.assertTrue(good['status'].startswith('303'))

    def test_courriel_html_and_prompt_instructions_render_as_text(self):
        self.f.model.intent='legal'
        key=self.f.engine.process(fixtures.mail(subject='<script>alert(1)</script>'))
        r=self.request('/mail',query='key='+key)
        self.assertTrue(r['status'].startswith('200'))
        self.assertNotIn('<script>',r['body'])
        self.assertIn('&lt;script&gt;',r['body'])
        self.assertEqual(r['headers']['Cache-Control'],'no-store')

    def test_path_traversal_and_get_mutation_refused(self):
        self.assertTrue(self.request('/mail',query='key=../../config')['status'].startswith('400'))
        self.assertTrue(self.request('/action',query='action=forget')['status'].startswith('400'))

    def test_association_forms_offer_roles_without_auto_selected_role(self):
        d=Desk(self.f.c)
        d.propose(self.f.matters[0],'new@example.test',{'type':'document','source':'test','indice':'email présent'})
        r=self.request('/associations')
        self.assertIn('Choisir le rôle',r['body'])
        self.assertIn('Conseil adverse',r['body'])
        self.assertNotIn('value="client" selected',r['body'])

    def test_matter_detail_contains_browser_actions(self):
        r=self.request('/matter',query='id=DOS-001')
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('Relancer les courriels non lus associés',r['body'])
        self.assertIn('Enregistrer',r['body'])

    def test_real_http_wsgi_access_controls_and_four_pages(self):
        import threading
        import urllib.request
        import urllib.error
        from wsgiref.simple_server import make_server, WSGIRequestHandler
        class Quiet(WSGIRequestHandler):
            def log_message(self,*args):pass
        server=make_server('127.0.0.1',0,self.app,handler_class=Quiet)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)+'/agent-courriel'
            headers={'Host':'cabinet.example.test','X-Forwarded-Proto':'https'}
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base+'/',headers=headers))
            self.assertEqual(error.exception.code,401)
            headers['Authorization']='Basic '+base64.b64encode(('admin:'+self.password).encode()).decode()
            for path in ['/','/associations','/dossiers','/memoire','/static/style.css']:
                with urllib.request.urlopen(urllib.request.Request(base+path,headers=headers)) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers['Cache-Control'],'no-store')
            bad={**headers,'Host':'evil.example'}
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(urllib.request.Request(base+'/',headers=bad))
        finally:
            server.shutdown();thread.join();server.server_close()


if __name__=='__main__':unittest.main()

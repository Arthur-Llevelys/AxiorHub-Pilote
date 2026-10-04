"""Regression coverage for the AxiorHub 5.0.0 operating hotfix."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from agent.desk import Desk
from agent.operating380 import action_center
from agent.workstation import save_external_url
import test_agent as fixtures
import test_desk


class Operating381(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c)

    def test_completed_column_excludes_internal_queue_housekeeping(self):
        stamp=self.d.now()
        for kind,message in [('review_action380','technical'),('prepare_reply','Brouillon vérifié')]:
            self.d.db.execute('''INSERT INTO jobs(kind,args,status,created,finished,result,priority)
              VALUES(?,?,'done',?,?,?,50)''',(kind,'{}',stamp,stamp,json.dumps({'message':message})))
        self.d.db.commit();data=action_center(self.d)
        done=[x for x in data['cards'] if x['bucket']=='done']
        self.assertEqual(len(done),1);self.assertIn('Réponse préparée',done[0]['title'])
        self.assertNotIn('review_action',json.dumps(data))


class Browser381(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_configured_openwebui_origin_drives_iframe_redirect_and_csp(self):
        save_external_url(Desk(self.f.c),'openwebui','https://ai.cabinet.test/')
        mail=self.request('/')
        self.assertIn('https://ai.cabinet.test/mail',mail['body'])
        self.assertIn('https://ai.cabinet.test',mail['headers']['Content-Security-Policy'])
        self.assertNotIn('ai.example.com',mail['body'])
        shortcut=self.request('/openwebui')
        self.assertEqual(shortcut['headers']['Location'],'https://ai.cabinet.test/')

    def test_today_project_links_keep_the_application_prefix(self):
        d=Desk(self.f.c);stamp=d.now()
        d.db.execute('''INSERT INTO document_projects_v220 VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?)''',('p381','DOS-001','conclusions','pending','test','{}',
          'preview','challenge',stamp,stamp,'','{}'));d.db.commit()
        page=self.request('/aujourdhui',query='vue=detail')['body']
        self.assertIn('/agent-courriel/projets?project=p381',page)
        self.assertNotIn('href="/projets',page)


class Standalone381(unittest.TestCase):
    def test_cleanup_service_worker_is_public_and_self_unregistering(self):
        from agent.standalone_auth import StandaloneAuth
        with tempfile.TemporaryDirectory() as tmp:
            app=StandaloneAuth(lambda env,start:[],tmp,'https://agent.example.com')
            capture={};env={'PATH_INFO':'/service-worker.js','REQUEST_METHOD':'GET',
              'QUERY_STRING':'','wsgi.input':io.BytesIO(), 'CONTENT_LENGTH':'0'}
            body=b''.join(app(env,lambda status,headers:capture.update(status=status,headers=dict(headers))))
            self.assertEqual(capture['status'],'200 OK');self.assertIn(b'unregister',body)
            self.assertEqual(capture['headers']['Cache-Control'],'no-store, max-age=0')


class Package381(unittest.TestCase):
    def test_roundcube_bridge_installs_css_and_redirects_legacy_task(self):
        root=Path(__file__).resolve().parents[1]
        installer=(root/'install-roundcube-ai-bridge.py').read_text()
        php=(root/'integrations/roundcube/ai_roundcube_assistant_v8/ai_roundcube_assistant.php').read_text()
        self.assertIn("'ai_roundcube_assistant.css'",installer)
        self.assertIn("public $task = 'mail|ai_assistant'",php)
        self.assertIn("Location: ?_task=mail",php)
        self.assertTrue((root/'diagnostic-v381.py').is_file())


if __name__=='__main__':unittest.main()

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent.common import Stop, HTTP
from agent.dav import DAV
from agent.mailbox import Mailbox, make_draft
from agent.model import Model
from test_agent import FakeModel, FakeBox, mail


class Server(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,code,body,ctype='application/json'):
        self.send_response(code);self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def read(self): return self.rfile.read(int(self.headers.get('Content-Length','0')))
    def do_GET(self):
        self.server.calls.append(('GET',self.path))
        if self.path=='/redirect':
            self.send_response(302);self.send_header('Location','/trap');self.end_headers();return
        if self.path.endswith('etat.txt'):
            self.send(200,b'Le contrat est en cours de relecture. Aucun depot confirme.','text/plain');return
        self.send(200,b'{}')
    def do_POST(self):
        data=json.loads(self.read());self.server.calls.append(('POST',self.path))
        if self.path=='/api/show':
            self.send(200,json.dumps(self.server.show).encode());return
        if self.path=='/api/chat':
            system=data['messages'][0]['content']
            stage='triage' if 'Décide si le dernier' in system else ('compose' if 'Rédige un brouillon court' in system else 'verify')
            self.server.schemas.append(data['format'])
            result=self.server.model.ask(stage,json.loads(data['messages'][1]['content']))
            self.send(200,json.dumps({'done':True,'done_reason':'stop','message':{'content':json.dumps(result)}}).encode());return
        self.send(404,b'{}')
    def do_PROPFIND(self):
        self.read();self.server.calls.append(('PROPFIND',self.path))
        p=self.path.rstrip('/')
        if '/calendars/' in p:
            body='<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>'+p+'/cabinet/</d:href><d:propstat><d:prop><d:displayname>CABINET EXEMPLE</d:displayname><d:resourcetype><d:collection/><c:calendar/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'
        else:
            body='<d:multistatus xmlns:d="DAV:"><d:response><d:href>'+p+'/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response><d:response><d:href>'+p+'/etat.txt</d:href><d:propstat><d:prop><d:resourcetype/><d:getetag>v1</d:getetag><d:getcontentlength>60</d:getcontentlength><d:getlastmodified>Mon, 07 Sep 2026 09:00:00 GMT</d:getlastmodified></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'
        self.send(207,body.encode(),'application/xml')
    def do_REPORT(self):
        body=self.read();self.server.calls.append(('REPORT',self.path))
        self.server.reports.append(body)
        data='<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/event.ics</d:href><d:propstat><d:prop><c:calendar-data>BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:test\nDTSTART:20260908T080000Z\nDTEND:20260908T090000Z\nSUMMARY:Audience DOS-001\nEND:VEVENT\nEND:VCALENDAR</c:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'
        self.send(207,data.encode(),'application/xml')


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=__import__('sqlite_cleanup').TempDir();self.addCleanup(self.temp.cleanup)
        self.srv=HTTPServer(('127.0.0.1',0),Server);self.srv.calls=[];self.srv.schemas=[];self.srv.reports=[]
        self.srv.show={'model_info':{}};self.srv.model=FakeModel()
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.url='http://127.0.0.1:'+str(self.srv.server_port)
        secret=Path(self.temp.name)/'secret';secret.write_text('synthetic-test-password');secret.chmod(0o600)
        self.dav=DAV({'url':self.url,'username':'test','password_file':str(secret),'roots':['/Dossiers']})
    def stop(self):self.srv.shutdown();self.thread.join();self.srv.server_close()
    def test_webdav_real_http_roundtrip(self):
        result=self.dav.inventory('/Dossiers/DEMO')
        self.assertEqual(result[0]['path'],'/Dossiers/DEMO/etat.txt')
        self.assertIn(b'contrat',self.dav.download(result[0]))
        self.assertTrue(all(verb in ('GET','PROPFIND') for verb,_ in self.srv.calls))
    def test_pipeline_with_real_http_adapters(self):
        from agent.engine import Engine
        from agent.common import private_json
        base=Path(self.temp.name);state=base/'state';state.mkdir()
        matters=base/'matters.json'
        private_json(matters,[{'id':'DOS-001','client_name':'Client DEMO','path':'/Dossiers/DEMO',
            'references':['DOS-001'],'correspondents':[{'email':'client@example.test','role':'client'}]}])
        config={'mode':'drafts','state_dir':str(state),'matters_file':str(matters),
            'mail':{'host':'example.test','username':'cabinet@example.test','own_addresses':['cabinet@example.test'],
                    'inbox':'INBOX','sent':'Sent','drafts':'Drafts','from_name':'Maître Exemple',
                    'from_address':'cabinet@example.test','signature':'Maître Exemple'},
            'nextcloud':{'roots':['/Dossiers']},'documents':{},'ollama':{'model':'test'},
            'calendar':{'urls':[],'timezone':'Europe/Paris'}}
        box=FakeBox();engine=Engine(config,mailbox=box,dav=self.dav,model=Model({'url':self.url,'model':'test'}))
        key=engine.process(mail())
        self.assertEqual(engine.state.get(key)[0],'drafted');self.assertEqual(len(box.appended),1)
        self.assertEqual(sum(1 for verb,url in self.srv.calls if url=='/api/chat'),3)
        self.assertTrue(any(verb=='GET' and url.endswith('etat.txt') for verb,url in self.srv.calls))
    def test_caldav_read_and_recurrence_expansion_request(self):
        calendars=self.dav.calendars();self.assertEqual(calendars[0]['name'],'CABINET EXEMPLE')
        events=self.dav.events([calendars[0]['url']],datetime(2026,9,1,tzinfo=timezone.utc),
                               datetime(2026,9,15,tzinfo=timezone.utc),'Europe/Paris')
        self.assertEqual(events[0]['summary'],'Audience DOS-001')
        self.assertIn(b'<c:expand',self.srv.reports[0])
    def test_ollama_json_schema_and_validation_roundtrip(self):
        model=Model({'url':self.url,'model':'test'})
        r=model.ask('triage',{'incoming':mail().public()})
        self.assertTrue(r['needs_reply']);self.assertEqual(self.srv.schemas[0]['type'],'object')
    def test_remote_model_metadata_refused(self):
        self.srv.show={'remote_host':'https://example.test'}
        with self.assertRaises(Stop):Model({'url':self.url,'model':'test'})
    def test_redirect_not_followed_with_credentials(self):
        with self.assertRaises(Stop):self.dav.http.request('GET',self.url+'/redirect')
        self.assertNotIn(('GET','/trap'),self.srv.calls)
    def test_cross_origin_and_cross_root_href_refused(self):
        for href in ['https://example.test/remote.php/dav/files/test/Dossiers/a',
                     '/remote.php/dav/files/another/Dossiers/a','/remote.php/dav/files/test/Secrets/a']:
            with self.assertRaises(Stop):self.dav.href_path(href)


class Transport:
    def __init__(self,*a,**kw):self.calls=[];self.raw=mail().msg.as_bytes(policy=__import__('email').policy.SMTP);self.writes=[]
    def login(self,*args):return 'OK',[]
    def logout(self):return 'BYE',[]
    def select(self,folder,readonly=False):
        if not readonly:raise AssertionError('IMAP read-write selection forbidden')
        self.calls.append(('EXAMINE',folder));return 'OK',[b'1']
    def response(self,key):return key,[b'7']
    def uid(self,command,*args):
        self.calls.append((command,args))
        if command=='SEARCH':return 'OK',[b'1']
        if command=='FETCH':
            query=args[1]
            if 'RFC822.SIZE' in query:
                return 'OK',[b'1 (UID 1 FLAGS () INTERNALDATE "07-Sep-2026 09:00:00 +0200" RFC822.SIZE 1000)']
            if 'BODY.PEEK' not in query:raise AssertionError('non-PEEK fetch forbidden')
            return 'OK',[(b'1 (BODY[] {1000}',self.raw),b')']
        raise AssertionError('Unexpected operation '+command)
    def append(self,folder,flags,stamp,raw):self.writes.append((folder,flags,raw));return 'OK',[b'[APPENDUID 7 2]']


class IMAPAdapterTests(unittest.TestCase):
    def test_read_flags_preserved_and_only_append_used(self):
        with __import__('sqlite_cleanup').TempDir() as td:
            secret=Path(td)/'secret';secret.write_text('synthetic');secret.chmod(0o600)
            cfg={'host':'example.test','username':'test','password_file':str(secret),'inbox':'INBOX','sent':'Sent','drafts':'Drafts'}
            with patch('agent.mailbox.imaplib.IMAP4_SSL',Transport):
                box=Mailbox(cfg);m=box.fetch('INBOX','1');self.assertEqual(m.flags,set())
                draft=make_draft(m,{'from_name':'Test','from_address':'cabinet@example.test','signature':'Test'},'Bonjour','id')
                box.append_draft(draft)
                self.assertEqual(len(box.conn.writes),1);self.assertEqual(box.conn.writes[0][1],'(\\Draft)')
                self.assertIn(('EXAMINE','"INBOX"'),box.conn.calls)
                self.assertTrue(any('BODY.PEEK[]' in str(x) for x in box.conn.calls))
                self.assertNotIn('STORE',str(box.conn.calls));self.assertNotIn('EXPUNGE',str(box.conn.calls))


if __name__=='__main__':unittest.main()

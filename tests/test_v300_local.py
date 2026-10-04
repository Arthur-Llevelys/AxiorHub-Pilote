"""3.0 regression tests: fake audio, fake DAV and local fixtures, no client data."""
import base64
from datetime import date
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.common import Stop
from agent.dav import DAV
from agent.workload import occupancy
from agent.audio import dictate
from agent.web import App
import upgrade
import test_desk

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'integrations/vocal_bridge'))
import bridge


class BridgeCoreTests(unittest.TestCase):
    def test_private_peers_only(self):
        for ip in ('127.0.0.1','::1','172.18.0.2','192.168.1.4','10.1.1.1','fd00::2'):
            self.assertTrue(bridge.peer_is_local(ip),ip)
        for ip in ('8.8.8.8','169.254.169.254','0.0.0.0','testclient',None):
            self.assertFalse(bridge.peer_is_local(ip),ip)

    def test_defaults_and_validation(self):
        self.assertEqual(bridge.normalize_language('fr'),'french')
        self.assertEqual(bridge.normalize_language('automatic detection'),'Automatic Detection')
        self.assertEqual(bridge.normalize_model(None),'large-v3-turbo')
        with self.assertRaises(bridge.BridgeError):bridge.normalize_model('cloud')

    def test_cleanup_and_metadata_only_after_success(self):
        paths=[]
        class Fake:
            def transcribe(self,audio,output_root,**kw):
                paths.append(audio.parent);self.kw=kw
                self_audio=audio.read_bytes()
                assert self_audio==b'fake-private-audio'
                output_root.mkdir();(output_root/'out.txt').write_text('confidential spoken text')
                return 'confidential spoken text'
        fake=Fake()
        with self.assertLogs(bridge.LOG,level='INFO') as logs:
            result=bridge.transcribe_stream(io.BytesIO(b'fake-private-audio'),client=fake,filename='client-name.webm',hotwords='secret-name')
        self.assertEqual(result,{'text':'confidential spoken text'})
        self.assertFalse(paths[0].exists())
        self.assertEqual(fake.kw['model'],'large-v3-turbo')
        self.assertEqual(fake.kw['temperature'],0)
        logged=' '.join(logs.output)
        self.assertIn(hashlib.sha256(b'fake-private-audio').hexdigest(),logged)
        for secret in ('fake-private-audio','client-name','secret-name','confidential spoken text'):
            self.assertNotIn(secret,logged)

    def test_cleanup_after_error(self):
        paths=[]
        class Fake:
            def transcribe(self,audio,*a,**kw):
                paths.append(audio.parent);raise RuntimeError('private exception')
        with self.assertRaises(RuntimeError),self.assertLogs(bridge.LOG,level='INFO') as logs:
            bridge.transcribe_stream(io.BytesIO(b'a'),client=Fake())
        self.assertFalse(paths[0].exists());self.assertNotIn('private exception',' '.join(logs.output))

    def test_empty_oversize_temperature_and_extension_refused(self):
        for raw,kwargs in ((b'',{}),(b'a',{'temperature':1}),(b'a',{'filename':'x.exe'})):
            with self.assertRaises(bridge.BridgeError):bridge.transcribe_stream(io.BytesIO(raw),**kwargs)
        with patch.object(bridge,'MAX_AUDIO_BYTES',2),self.assertRaisesRegex(bridge.BridgeError,'too_large'):
            bridge.transcribe_stream(io.BytesIO(b'abc'))

    def test_extraction_rejects_status_or_outside_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);outputs=root/'out';outputs.mkdir()
            (root/'secret.txt').write_text('secret')
            with self.assertRaises(bridge.BridgeError):bridge.extract_text(('done',[str(root/'secret.txt')]),outputs)
            with self.assertRaises(bridge.BridgeError):bridge.extract_text(('done',[]),outputs)
            (outputs/'result.txt').write_text('bonjour')
            self.assertEqual(bridge.extract_text(('done',[str(outputs/'result.txt')]),outputs),'bonjour')

    def test_remote_cleanup_only_exact_random_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stem='axiorhub-'+'a'*32
            for name in (stem+'.wav',stem+'.txt','another-client.wav',stem+'b.txt'):
                (root/name).write_text('fixture')
            self.assertEqual(bridge.cleanup_remote(stem,[root]),2)
            self.assertTrue((root/'another-client.wav').exists())
            self.assertTrue((root/(stem+'b.txt')).exists())
            with self.assertRaises(bridge.BridgeError):bridge.cleanup_remote('../',[root])

    def test_remote_cleanup_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);stem='axiorhub-'+'b'*32
            source=root/'keep.txt';source.write_text('keep')
            (root/(stem+'.txt')).symlink_to(source)
            with self.assertRaises(bridge.BridgeError):bridge.cleanup_remote(stem,[root])
            self.assertEqual(source.read_text(),'keep')


try:
    from fastapi.testclient import TestClient
    import app as bridge_app
    import gradio_client
    BRIDGE_DEPS=True
except ImportError:
    BRIDGE_DEPS=False


@unittest.skipUnless(BRIDGE_DEPS,'Install integrations/vocal_bridge/requirements.txt and httpx for HTTP tests')
class BridgeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.token='x'*48;secret=Path(self.temp.name)/'token';secret.write_text(self.token)
        self.env=patch.dict(os.environ,{'BRIDGE_TOKEN_FILE':str(secret)});self.env.start();self.addCleanup(self.env.stop)
        self.client=TestClient(bridge_app.app,client=('127.0.0.1',1234))
        self.addCleanup(self.client.close)

    def post(self,**kw):
        return self.client.post('/v1/audio/transcriptions',files={'file':('test.wav',b'audio','audio/wav')},
                                headers={'Authorization':'Bearer '+self.token},**kw)

    def test_post_compatible_json_and_defaults(self):
        with patch.object(bridge_app,'transcribe_stream',return_value={'text':'bonjour'}) as transcribe:
            result=self.post()
        self.assertEqual(result.status_code,200);self.assertEqual(result.json(),{'text':'bonjour'})
        self.assertEqual(transcribe.call_args.kwargs['model'],'large-v3-turbo')
        self.assertEqual(transcribe.call_args.kwargs['language'],'french')
        self.assertEqual(transcribe.call_args.kwargs['temperature'],0)

    def test_invalid_auth_and_public_peers_never_call_vocal(self):
        with patch.object(bridge_app,'transcribe_stream') as transcribe:
            self.assertEqual(self.client.post('/v1/audio/transcriptions').status_code,401)
            with TestClient(bridge_app.app,client=('8.8.8.8',1234)) as public:
                self.assertEqual(public.get('/health').status_code,403)
            transcribe.assert_not_called()

    def test_failure_sanitized(self):
        with patch.object(bridge_app,'transcribe_stream',side_effect=RuntimeError('PRIVATE CLIENT')):
            result=self.post()
        self.assertEqual(result.status_code,502);self.assertNotIn('PRIVATE CLIENT',result.text)

    def test_busy_does_not_queue_second_audio(self):
        bridge_app.processing.acquire()
        try:self.assertEqual(self.post().status_code,429)
        finally:bridge_app.processing.release()

    def test_size_guard_before_spooling(self):
        with patch.dict(os.environ,{'MAX_AUDIO_BYTES':'1'}):
            response=self.client.post('/v1/audio/transcriptions',content=b'a',
              headers={'Authorization':'Bearer '+self.token,'Content-Length':'100000'})
        self.assertEqual(response.status_code,413)

    def test_gradio_54_parameters_match_supplied_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            audio=Path(folder)/'a.wav';audio.write_bytes(b'a')
            args=bridge.vocal_arguments(audio,'large-v3-turbo','french',0,'référé')
        self.assertEqual(len(args),54);self.assertEqual(len(bridge.PARAMETER_LABELS),54)
        for index,value in {3:False,4:'txt',6:'large-v3-turbo',7:'french',12:'int8',18:0,33:'référé',44:False,45:'cpu',48:False,50:'cpu',52:False}.items():
            self.assertEqual(args[index],value)


class Workload300Tests(unittest.TestCase):
    def event(self,a,b,busy=True):return {'starts':a,'ends':b,'busy':busy}
    def test_union_excludes_double_calendar_and_allday(self):
        rows=[self.event('2026-09-13T22:00:00+00:00','2026-09-14T22:00:00+00:00'),
              self.event('2026-09-14T07:00:00+00:00','2026-09-14T09:00:00+00:00'),
              self.event('2026-09-14T08:00:00+00:00','2026-09-14T10:00:00+00:00')]
        rows.append(rows[1])
        day=occupancy(rows,date(2026,9,14))['2026-09-14']
        self.assertEqual(day['planned_minutes'],180);self.assertEqual(day['all_day_markers'],1)
        self.assertFalse(day['task_due_dates_added'])

    def test_dst_uses_elapsed_time(self):
        rows=[self.event('2026-10-25T01:30:00+02:00','2026-10-25T03:30:00+01:00')]
        self.assertEqual(occupancy(rows,date(2026,10,25))['2026-10-25']['planned_minutes'],180)

    def test_non_busy_and_invalid_events_not_work(self):
        self.assertEqual(occupancy([self.event('bad','bad'),self.event('2026-09-14T07:00:00Z','2026-09-14T09:00:00Z',False)],date(2026,9,14)),{})

    def test_incremental_inventory_crosses_old_limit_and_preserves_boundary(self):
        dav=object.__new__(DAV);dav.cfg={'max_depth':8}
        dav.list_folder=lambda p:[{'path':'/Dossier/'+str(i).zfill(5)+'.pdf','directory':False,'etag':'x','modified':''} for i in range(1201)]
        state,complete=dav.inventory_step('/Dossier')
        self.assertFalse(complete);self.assertEqual(len(state['files']),500)
        state,complete=dav.inventory_step('/Dossier',state);self.assertFalse(complete)
        state,complete=dav.inventory_step('/Dossier',state);self.assertTrue(complete)
        self.assertEqual(len(state['files']),1201)
        with self.assertRaises(Stop):dav.inventory_step('/Other',state)

    def test_inventory_refuses_outside_child(self):
        dav=object.__new__(DAV);dav.cfg={}
        dav.list_folder=lambda p:[{'path':'/Other/private','directory':False}]
        with self.assertRaises(Stop):dav.inventory_step('/Dossier')


class Guided300Tests(unittest.TestCase):
    def setUp(self):
        self.f=test_desk.WebTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_home_and_all_requested_pages_include_local_micro_script(self):
        for path in ('/accueil','/orchestrateur-avis','/recherche-juridique','/audiences-word','/planning','/assistant'):
            result=self.f.request(path)
            self.assertTrue(result['status'].startswith('200'),(path,result['body'][:300]))
            self.assertIn('v310.js',result['body']);self.assertIn('axiorhub-audio',result['body'])
        page=self.f.request('/accueil')['body']
        self.assertIn('Par quoi commençons-nous',page)
        self.assertIn('Aucun import SpeakR automatique',page)
        self.assertIn('Brouillons de courriels',page)
        self.assertIn('Projets d’actes juridiques',page)
        self.assertIn('Mettre en pause',page)

    def test_micro_is_no_cloud_and_no_automatic_submission(self):
        js=(ROOT/'agent/static/v300.js').read_text()
        self.assertNotIn('new SpeechRecognition',js);self.assertNotIn('webkitSpeechRecognition',js)
        self.assertNotIn('.submit(',js);self.assertIn("prefix+'/dictation'",js)
        self.assertIn("getUserMedia({audio:true})",js)

    def test_dictation_requires_origin_and_csrf(self):
        with patch('agent.audio.dictate') as method:
            result=self.f.request('/dictation',method='POST',form={'x':'a'},origin=self.f.origin)
        self.assertTrue(result['status'].startswith('400'));method.assert_not_called()

    def test_disabled_audio_never_contacts_network(self):
        with patch('agent.audio.HTTP') as http,self.assertRaisesRegex(Stop,'non_configuree'):
            dictate({},b'a','audio/webm')
        http.assert_not_called()

    def test_roundcube_does_not_navigate_if_window_open_returns_null(self):
        js=(ROOT/'integrations/roundcube/axiorhub_mail_agent/axiorhub_mail_agent.js').read_text()
        self.assertNotIn('window.location.assign',js)
        self.assertIn('noopener',js)

    def test_upgrade_preserves_audio_and_automation_optouts(self):
        cfg={'audio':{'enabled':False},'autonomy':{'enabled':False},
             'orchestrator':{'automatic_mail_drafts_enabled':False,
                             'automatic_legal_projects_enabled':False},
             'automation':{'index_all_enabled':True,'daily_digest_enabled':True},'mail':{},'ollama':{}}
        updated=json.loads(upgrade.updated_config(json.dumps(cfg).encode()))
        self.assertEqual(updated['audio']['enabled'],False)
        self.assertEqual(updated['autonomy']['enabled'],False)
        self.assertFalse(updated['orchestrator']['automatic_mail_drafts_enabled'])
        self.assertFalse(updated['orchestrator']['automatic_legal_projects_enabled'])
        self.assertTrue(updated['automation']['index_all_enabled'])
        self.assertTrue(updated['automation']['daily_digest_enabled'])

    def test_migration_preserves_disabled_control_tower(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);db=sqlite3.connect(path/'desk.sqlite3')
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO settings VALUES('automation:cabinet_pilotage_enabled','false')")
            db.commit();db.close();upgrade.migrate_cabinet_pilotage(path)
            db=sqlite3.connect(path/'desk.sqlite3')
            self.assertEqual(db.execute('SELECT value FROM settings').fetchone()[0],'false');db.close()

if __name__=='__main__':unittest.main()

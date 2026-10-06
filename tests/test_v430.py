"""Live transport, persistent work, atomic claims and read-only surveillance."""
import base64
import io
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

from agent.common import Stop
from agent.desk import Desk, run_lock
from agent.live430 import (emit, enhance_forms, finish_request, fingerprint,
                           heartbeat, reserve_request, snapshot, stream)
from agent.watch430 import calendar_check, documents_check, idle_wait, observe, schedule
from agent.api import dispatch, openapi
import test_agent as fixtures
import test_desk


class Live430Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests('test_observation_has_no_mail_write');self.f.setUp()
        self.addCleanup(self.f.doCleanups);self.d=Desk(self.f.c);self.d.setting('search490:scheduled',1e12)

    def test_migration_idempotent_and_concurrent_start(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            def open_desk(_):
                desk=Desk(self.f.c)
                try:return desk.db.execute('SELECT COUNT(*) FROM schema_migrations WHERE id=430').fetchone()[0]
                finally:desk.db.close()
            self.assertEqual(list(pool.map(open_desk,range(8))),[1]*8)

    def test_first_migration_race_is_transactional(self):
        from agent.migrations import apply
        path=self.f.base/'migration-race.sqlite3'
        db=sqlite3.connect(path);db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY)');db.commit();db.close()
        gate=threading.Barrier(2)
        def migrate(_):
            holder=SimpleNamespace(db=sqlite3.connect(path,timeout=10),now=lambda:'2026-10-01')
            gate.wait()
            try:
                apply(holder)
                return holder.db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
            finally:holder.db.close()
        expected=len(list((Path(__file__).parents[1]/'agent/migrations').glob('*.sql')))
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(list(pool.map(migrate,range(2))),[expected,expected])

    def test_atomic_queue_deduplication_with_two_connections(self):
        gate=threading.Barrier(2)
        def enqueue(_):
            desk=Desk(self.f.c);gate.wait()
            try:return desk.enqueue('live_mail430')
            finally:desk.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(enqueue,range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(snapshot(self.d)['pending'],1)

    def test_periodic_surveillance_deduplicates_and_honours_pause(self):
        self.assertEqual(len(schedule(self.d,10000)),3)
        self.assertEqual(schedule(self.d,10001),[])
        self.assertEqual(len(schedule(self.d,10301)),3)
        self.assertEqual(snapshot(self.d)['pending'],3)
        self.d.setting('live430:enabled',False)
        self.assertEqual(schedule(self.d,20000),[])
        self.assertEqual(snapshot(self.d)['services'][0]['status'],'paused')

    def test_request_receipt_survives_reopen_and_rejects_token_reuse(self):
        token='a'*32;fields={'action':'sync','csrf':'private-token','back':'/'}
        self.assertIsNone(reserve_request(self.d,token,fields))
        jid=self.d.enqueue('sync');finish_request(self.d,token,jid,'/?job='+str(jid))
        other=Desk(self.f.c)
        self.assertEqual(reserve_request(other,token,fields)['job_id'],jid)
        with self.assertRaises(Stop):reserve_request(other,token,{'action':'run'})
        self.assertEqual(fingerprint(fields),fingerprint({'action':'sync'}))
        other.db.close()

    def test_incomplete_receipt_never_silently_replays_mutation(self):
        reserve_request(self.d,'b'*32,{'action':'sync'})
        with self.assertRaisesRegex(Stop,'requete_en_cours'):reserve_request(self.d,'b'*32,{'action':'sync'})

    def test_snapshot_redacts_instruction_secrets_and_raw_exception(self):
        jid=self.d.enqueue('sync',{'instruction':'CONFIDENTIAL-INSTRUCTION','api_key':'SECRET-KEY'})
        self.d.db.execute("UPDATE jobs SET status='error',result=? WHERE id=?",
          (json.dumps({'erreur':'token=SECRET-KEY path=/private'}),jid));self.d.db.commit()
        raw=json.dumps(snapshot(self.d));self.assertNotIn('SECRET-KEY',raw)
        self.assertNotIn('CONFIDENTIAL-INSTRUCTION',raw);self.assertNotIn('/private',raw)

    def test_durable_sse_replay_after_last_event_and_dedup(self):
        emit(self.d,'detected','Premier événement',dedupe='first')
        first=self.d.db.execute('SELECT MAX(id) FROM live_events_v430').fetchone()[0]
        emit(self.d,'produced','Brouillon vérifié',dedupe='second')
        emit(self.d,'produced','Brouillon vérifié',dedupe='second')
        feed=stream(self.f.c,first,duration=1)
        self.assertIn(b'retry:',next(feed));event=next(feed).decode();feed.close()
        self.assertIn('event: activity',event);self.assertIn('Brouillon vérifié',event)
        self.assertNotIn('Premier événement',event)

    def test_heartbeat_exposes_freshness_not_invented_success(self):
        heartbeat(self.d,'worker1','active','Worker disponible')
        self.assertFalse(snapshot(self.d)['services'][0]['stale'])
        self.d.db.execute('UPDATE live_services_v430 SET heartbeat=0');self.d.db.commit()
        self.assertTrue(snapshot(self.d)['services'][0]['stale'])

    def test_read_surveillance_progresses_while_llm_lock_is_held(self):
        jid=self.d.enqueue('live_calendar430');blocked=self.d.enqueue('prepare_reply',{'key':'a'*64})
        with run_lock(self.f.c),patch('agent.watch430.calendar_check',return_value={'changed':0,'remote_write':False}):
            self.assertTrue(self.d.work_once())
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'done')
        self.assertEqual(self.d.db.execute('SELECT status FROM jobs WHERE id=?',(blocked,)).fetchone()[0],'pending')

    def test_live_mail_worker_registers_each_verified_draft_in_batch(self):
        self.f.box.inputs={'1':fixtures.mail(uid='1'),'2':fixtures.mail(uid='2')}
        jid=self.d.enqueue('live_mail430')
        with patch('agent.engine.Engine',return_value=self.f.engine):self.assertTrue(self.d.work_once())
        self.assertEqual(len(self.f.box.appended),2)
        rows=self.d.db.execute('SELECT status FROM production_deliverables_v420 WHERE job_id=?',(jid,)).fetchall()
        self.assertEqual([r[0] for r in rows],['verified','verified'])
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM live_events_v430 WHERE kind='produced'").fetchone()[0],2)

    def test_calendar_change_triggers_work_once_without_remote_write(self):
        class Dav:
            def events(self,*args):return [{'uid':'u1','start':'2026-10-05T10:00:00Z','summary':'Audience'}]
        self.assertEqual(calendar_check(self.d,Dav())['changed'],1)
        self.assertEqual(calendar_check(self.d,Dav())['changed'],0)
        self.assertEqual(self.d.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='monitor_all'").fetchone()[0],1)
        self.assertFalse(self.f.box.appended)

    def test_document_inventory_dictionary_etag_change_and_no_content_download(self):
        class Dav:
            tag='v1'
            def inventory_step(self,*args):return ({'files':{'/Dossiers/DEMO/a.pdf':{'etag':self.tag,'modified':'today'}}},True)
        dav=Dav();self.assertEqual(documents_check(self.d,dav)['changed'],1)
        self.assertEqual(documents_check(self.d,dav)['changed'],0)
        dav.tag='v2';self.assertEqual(documents_check(self.d,dav)['changed'],1)

    def test_full_queue_never_loses_document_signal(self):
        class Dav:
            def inventory_step(self,*args):return ({'files':{'/Dossiers/DEMO/a.pdf':{'etag':'v1'}}},True)
        with patch.object(self.d,'enqueue',side_effect=Stop('file_attente_pleine')):
            documents_check(self.d,Dav())             # 5.6.5 : file pleine = analyse reportée, pas d'échec de la surveillance
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM live_dirty_v430').fetchone()[0],1)
        self.assertEqual(documents_check(self.d,Dav())['changed'],1)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM live_dirty_v430').fetchone()[0],0)

    def test_full_queue_never_loses_calendar_signal(self):
        class Dav:
            def events(self,*args):return [{'uid':'u1','start':'2026-10-05','summary':'Audience'}]
        with patch.object(self.d,'enqueue',side_effect=Stop('file_attente_pleine')):
            with self.assertRaises(Stop):calendar_check(self.d,Dav())
        self.assertEqual(calendar_check(self.d,Dav())['changed'],1)
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM live_dirty_v430').fetchone()[0],0)

    def test_observation_and_automatic_deposit_off_never_append(self):
        self.f.c['mode']='observe';self.f.engine.process(fixtures.mail());self.assertFalse(self.f.box.appended)
        self.f.c['mode']='drafts';self.d.setting('automation:automatic_mail_drafts_enabled',False)
        key=self.f.engine.process(fixtures.mail(uid='2'))
        self.assertEqual(self.f.status(key),'review');self.assertFalse(self.f.box.appended)

    def test_queued_prudent_reply_cannot_bypass_observation_or_deposit_off(self):
        from agent.intelligence import prepare_reply
        for mode,enabled in [('observe',True),('drafts',False)]:
            self.d.c['mode']=mode;self.d.setting('automation:automatic_mail_drafts_enabled',enabled)
            with patch('agent.intelligence.prepare_draft',return_value={'projet_prepare':True,'depot_autorise':True}),patch('agent.intelligence.deposit_draft') as deposit:
                result=prepare_reply(self.d,{'key':'a'*64,'automatic':'yes'})
                self.assertFalse(result['depot_autorise']);deposit.assert_not_called()

    def test_api_queues_checks_on_existing_engine(self):
        self.assertIn('/live/status',openapi('https://agent.test')['paths'])
        self.assertEqual(len(dispatch(self.d,'/live/check','POST')['job_ids']),3)
        self.assertEqual(dispatch(self.d,'/live/status','GET')['pending'],3)

    def test_static_form_progressive_enhancement_and_native_fallback(self):
        html='<form method="post" action="/agent-courriel/action"><input type="hidden" name="action" value="sync"><button>Analyser</button></form>'
        result=enhance_forms(html,'/agent-courriel')
        self.assertIn('hx-post=',result);self.assertIn('hx-sync="this:drop"',result)
        self.assertIn('data-live-key="'+fingerprint({'action':'sync'})+'"',result)
        self.assertIn('method="post"',result)


class Idle430Tests(unittest.TestCase):
    def box(self,lines,caps=(b'IDLE',)):
        conn=SimpleNamespace(capabilities=caps,send=lambda raw:sent.append(raw),
          readline=lambda:next(iterator),socket=lambda:object())
        iterator=iter(lines);sent=[]
        return SimpleNamespace(conn=conn,cfg={'inbox':'INBOX'},select=lambda _:None),sent

    def test_idles_readonly_and_finishes_done_before_reuse(self):
        box,sent=self.box([b'+ idling\r\n',b'* 2 EXISTS\r\n',b'AXIORHUBIDLE OK done\r\n'])
        with patch('agent.watch430.select.select',return_value=([object()],[],[])):
            self.assertTrue(idle_wait(box))
        self.assertEqual(sent,[b'AXIORHUBIDLE IDLE\r\n',b'DONE\r\n'])

    def test_missing_idle_uses_fallback_without_sending_command(self):
        box,sent=self.box([],caps=(b'IMAP4rev1',));self.assertIsNone(idle_wait(box));self.assertEqual(sent,[])

    def test_timeout_still_sends_done(self):
        box,sent=self.box([b'+ idling\r\n',b'AXIORHUBIDLE OK done\r\n'])
        with patch('agent.watch430.select.select',return_value=([],[],[])):self.assertFalse(idle_wait(box))
        self.assertEqual(sent[-1],b'DONE\r\n')


class LiveWeb430Tests(unittest.TestCase):
    def setUp(self):
        self.w=test_desk.WebTests('test_post_requires_origin_and_csrf');self.w.setUp();self.addCleanup(self.w.doCleanups)

    def hx(self,fields,origin=True,csrf=True):
        raw=urlencode({'action':'sync','csrf':'test-csrf' if csrf else '',**fields}).encode()
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/action','HTTP_HOST':'cabinet.example.test',
          'HTTP_X_FORWARDED_PROTO':'https','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),
          'CONTENT_TYPE':'application/x-www-form-urlencoded','HTTP_HX_REQUEST':'true',
          'HTTP_AUTHORIZATION':'Basic '+base64.b64encode(('admin:'+self.w.password).encode()).decode()}
        if origin:env['HTTP_ORIGIN']=self.w.origin
        result={}
        response=self.w.app(env,lambda status,headers:result.update(status=status,headers=dict(headers)))
        result['body']=b''.join(response).decode();return result

    def test_hx_post_records_once_and_returns_receipt_not_redirect(self):
        fields={'live_token':'c'*32}
        first=self.hx(fields);second=self.hx(fields)
        self.assertTrue(first['status'].startswith('200'));self.assertTrue(second['status'].startswith('200'))
        receipt=json.loads(first['headers']['HX-Trigger'])['live-action']
        self.assertEqual(receipt['job_id'],json.loads(second['headers']['HX-Trigger'])['live-action']['job_id'])
        self.assertNotIn('Location',first['headers'])
        self.assertEqual(Desk(self.w.f.c).db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)

    def test_htmx_does_not_bypass_csrf_or_origin(self):
        self.assertTrue(self.hx({'live_token':'d'*32},origin=False)['status'].startswith('400'))
        self.assertTrue(self.hx({'live_token':'d'*32},csrf=False)['status'].startswith('400'))

    def test_live_routes_are_authenticated_and_local_assets_served(self):
        for path in ('/live/snapshot','/live/panel','/live/board'):
            self.assertTrue(self.w.request(path,auth=False)['status'].startswith('401'))
            self.assertTrue(self.w.request(path)['status'].startswith('200'))
        for name in ('htmx.min.js','v430.js','v430.css'):
            self.assertTrue(self.w.request('/static/'+name)['status'].startswith('200'))

    def test_sse_mime_no_length_and_stream_limit_preserve_normal_requests(self):
        env={'REQUEST_METHOD':'GET','PATH_INFO':'/live/events','HTTP_HOST':'cabinet.example.test',
          'HTTP_X_FORWARDED_PROTO':'https','wsgi.input':io.BytesIO(),
          'HTTP_AUTHORIZATION':'Basic '+base64.b64encode(('admin:'+self.w.password).encode()).decode()}
        captured={}
        response=self.w.app(env,lambda status,headers:captured.update(status=status,headers=dict(headers)))
        self.assertIn('text/event-stream',captured['headers']['Content-Type'])
        self.assertNotIn('Content-Length',captured['headers'])
        self.assertIn(b'retry:',next(response));response.close()
        slots=self.w.app.live_slots
        for _ in range(8):self.assertTrue(slots.acquire(False))
        try:
            response=self.w.app(env,lambda status,headers:captured.update(status=status,headers=dict(headers)))
            self.assertTrue(captured['status'].startswith('429'));list(response)
            self.assertTrue(self.w.request('/live/snapshot')['status'].startswith('200'))
        finally:
            for _ in range(8):slots.release()

    def test_pause_preserves_mode_and_existing_autonomy(self):
        self.assertTrue(self.hx({'action':'save_live430','enabled':'no','live_token':'e'*32})['status'].startswith('200'))
        d=Desk(self.w.f.c);self.assertFalse(d.settings('live430:enabled',True))
        self.assertEqual(d.c['mode'],'drafts');self.assertTrue(d.settings('automation:automatic_mail_drafts_enabled',True))


if __name__=='__main__':unittest.main()

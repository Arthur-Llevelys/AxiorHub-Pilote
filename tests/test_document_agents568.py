"""Agents déclaratifs : scénarios d'effets, reprise, permissions et vrais formats simulés."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone,date
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit,unquote

from agent.common import Stop,private_json,read_secret
from agent import automation568 as rules,procedure568 as procedure,calendar568 as calendar,settings568
from agent.standalone_auth import allowed
from agent.dav import DAV,parse_events
import test_v568 as prior
from test_v490 import DEMO


class Model:
    cfg={'model':'test-local','max_context_chars':45000}
    def __init__(self):self.calls=[]
    def ask(self,stage,data):
        self.calls.append(deepcopy(data))
        return {'answer':'Analyse documentée : '+str(data['sources'][0].get('path','')),
           'source_ids':[x['id'] for x in data['sources']],'limits':[],'proposed_actions':[]}


class Files:
    def __init__(self,path,content):self.files={path:content};self.http=self;self.moves=[];self.folders=[];self.fail_move=False;self.reads=[]
    def file_url(self,path):return 'https://cloud.example.test'+path
    def stat(self,path):
        self.reads.append(path)
        if path not in self.files:raise Stop('http_404')
        return {'path':path,'etag':'"'+hashlib.sha256(self.files[path]).hexdigest()+'"','size':len(self.files[path]),'created':datetime.now(timezone.utc).isoformat(),'modified':datetime.now(timezone.utc).isoformat()}
    def download(self,item):return self.files[item['path']]
    def ensure_folder(self,path,boundary):
        assert path.startswith(boundary+'/');self.folders.append(path)
    def request(self,method,url,body=None,headers=None,limit=0):
        if method!='MOVE':raise AssertionError(method)
        src=unquote(urlsplit(url).path);dest=unquote(urlsplit(headers['Destination']).path)
        assert headers['Overwrite']=='F'
        if dest in self.files:raise Stop('http_412')
        if self.stat(src)['etag']!=headers['If-Match']:raise Stop('http_412')
        self.files[dest]=self.files.pop(src);self.moves.append((src,dest))
        if self.fail_move:raise Stop('delai_http_depasse')
        return b''


class Google:
    def __init__(self):self.rows={};self.puts=0;self.fail_after_put=False;self.read_fail=False;self.fake_read=False
    def events(self,cal,start,end):
        if self.read_fail:raise Stop('http_503')
        return list(self.rows.values())
    def put(self,cal,ident,payload):
        self.puts+=1
        if ident in self.rows:raise Stop('http_409')
        self.rows[ident]={'id':ident,**deepcopy(payload)}
        if self.fail_after_put:raise Stop('delai_http_depasse')
    def get(self,cal,ident):return {**self.rows[ident],**({'summary':'Autre résultat'} if self.fake_read else {})}


class CalendarHTTP:
    def __init__(self):self.rows={};self.puts=[];self.fail_report=False
    def request(self,method,url,body=None,headers=None,limit=0):
        if method=='REPORT':
            if self.fail_report:raise Stop('http_503')
            from xml.sax.saxutils import escape
            return ('<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'+''.join('<d:response><d:href>'+escape(k)+'</d:href><d:propstat><d:prop><c:calendar-data>'+escape(v.decode())+'</c:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>' for k,v in self.rows.items())+'</d:multistatus>').encode()
        if method=='PUT':
            assert headers['If-None-Match']=='*'
            if url in self.rows:raise Stop('http_412')
            self.rows[url]=body;self.puts.append(url);return b''
        if method=='GET':return self.rows[url]
        raise AssertionError(method)


class Base(prior.Base):
    def setUp(self):
        super().setUp();self.desk.c['calendar']['urls']=[]
        settings568.save(self.desk,{'autonomy':'organize'})
        self.path='/Dossiers/DEMO/AVIS_DE_RENVOI.txt'
        self.files=Files(self.path,'Dossier DEMO\nAVIS DE RENVOI\nAudience renvoyée au 12 novembre 2026 à 14h30.\n'.encode())
        self.model=Model();self.box=prior.Box([])
    def active(self,key='renvoi',**kw):
        row=next(x for x in rules.listing(self.desk,'cabinet') if x['template']==key)
        if kw:
            row=rules.save(self.desk,{'id':row['id'],'revision':row['revision'],'name':row['name'],'instruction':row['instruction'],'recipe':{**row['recipe'],**kw}},'cabinet')
        rules.control(self.desk,{'id':row['id'],'revision':row['revision'],'approved':True,'action':'activate'},'cabinet');return row
    def detect(self,path=None):
        path=path or self.path;meta=self.files.stat(path);rules.observe(self.desk,DEMO,{path:meta},'cabinet')
        return self.desk.db.execute('SELECT * FROM document_runs568 ORDER BY created DESC').fetchone()
    def produce(self,row):
        with patch('agent.mailbox.Mailbox',return_value=self.box):return procedure.execute(self.desk,row['id'],'cabinet',self.files,self.model)
    def json_request(self,route,data=None,auth=True,origin=None,csrf='test-csrf'):
        import base64
        path,_,query=route.partition('?');raw=json.dumps(data or {}).encode();captured={}
        env={'REQUEST_METHOD':'GET' if data is None else 'POST','PATH_INFO':path,'QUERY_STRING':query,'HTTP_HOST':'cabinet.example.test','HTTP_X_FORWARDED_PROTO':'https','wsgi.url_scheme':'http','wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json','HTTP_ORIGIN':origin or self.origin,'HTTP_X_CSRF_TOKEN':csrf}
        if auth:env['HTTP_AUTHORIZATION']='Basic '+base64.b64encode(('admin:'+self.password).encode()).decode()
        def start(status,headers):captured.update(status=status,headers=dict(headers))
        captured['body']=b''.join(self.app(env,start)).decode();return captured


class Rules(Base):
    def test_five_defaults_are_editable_drafts_and_do_not_enqueue(self):
        rows=rules.listing(self.desk,'cabinet');self.assertEqual(len(rows),5);self.assertEqual({r['state'] for r in rows},{'draft'})
        rules.listing(self.desk,'cabinet');self.assertEqual(self.desk.db.execute('SELECT count(*) FROM document_rules568').fetchone()[0],5)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    def test_no_automatic_run_before_activation(self):
        rules.listing(self.desk,'cabinet');self.assertIn(self.path,rules.observe(self.desk,DEMO,{self.path:self.files.stat(self.path)}))
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM document_runs568').fetchone()[0],0)
    def test_activation_requires_review_of_current_revision(self):
        r=rules.listing(self.desk,'cabinet')[0]
        for payload in ({'id':r['id'],'action':'activate'},{'id':r['id'],'action':'activate','revision':0,'approved':True}):
            with self.assertRaisesRegex(Stop,'revue_agent'):rules.control(self.desk,payload,'cabinet')
    def test_natural_rule_compiles_all_bounded_actions_without_model(self):
        v=rules.compile_instruction(self.desk,'Si un nouveau fichier de moins de 10 jours contient "AVIS_DE_RENVOI", analyser, placer dans "PROCEDURE", inscrire les événements dans mon agenda, rédiger un brouillon et préparer la prochaine tâche et un projet de conclusions en réponse.','cabinet')
        self.assertEqual(v['recipe']['age_days'],10);self.assertEqual(v['recipe']['mode'],'organize');self.assertEqual(set(v['recipe']['actions']),rules.ACTIONS)
        self.assertNotIn('PROCEDURE',v['recipe']['names'])
    def test_compile_never_authorizes_sending_or_arbitrary_code(self):
        self.assertIsNone(rules.compile_instruction(self.desk,'Analyser INJONCTION et envoyer automatiquement au client.','cabinet')['recipe'])
        with self.assertRaises(Stop):rules.validate_recipe({'names':['X'],'actions':['shell']})
        with self.assertRaises(Stop):rules.validate_recipe({'names':['X'],'actions':['analyze'],'url':'https://foreign.test'})
    def test_model_compilation_is_validated_as_closed_recipe(self):
        class Compiler:
            def ask(self,*args):return {'answer':'{"names":["Note"],"actions":["send_mail"]}','source_ids':[],'limits':[],'proposed_actions':[]}
        with self.assertRaisesRegex(Stop,'action_agent'):rules.compile_instruction(self.desk,'Quand un compte rendu de réunion arrive, produis une action.','cabinet',Compiler())
    def test_file_window_uses_creation_proof_not_modification_by_default(self):
        r=self.active()['recipe'];meta={'created':(datetime.now(timezone.utc)-timedelta(days=20)).isoformat(),'modified':datetime.now(timezone.utc).isoformat()}
        self.assertEqual(rules.match(r,self.path,meta),'out_of_window');self.assertEqual(rules.match({**r,'clock':'modified'},self.path,meta),'match')
    def test_missing_and_unzoned_creation_dates_remain_decisions(self):
        r=self.active()['recipe']
        for meta in ({'modified':datetime.now(timezone.utc).isoformat()},{'created':'2026-10-06'}):self.assertEqual(rules.match(r,self.path,meta),'age_unknown')
    def test_future_creation_date_cannot_activate(self):
        r=self.active()['recipe'];self.assertEqual(rules.match(r,self.path,{'created':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}),'out_of_window')
    def test_document_heading_precedes_a_notice_cited_in_arguments(self):
        self.assertEqual(rules.classify('CONCLUSIONS ADVERSES\nDossier DEMO. Discussion : la pièce AVIS DE RENVOI est produite.'),'conclusions')
    def test_scoped_rule_can_watch_authorized_incoming_folder(self):
        r=self.active(scope='DEMO');path='/Dossiers/Arrivee/AVIS_DE_RENVOI.txt';meta={'etag':'v1','created':datetime.now(timezone.utc).isoformat()}
        rules.observe(self.desk,None,{path:meta},'cabinet');self.assertEqual(self.desk.db.execute('SELECT matter FROM document_runs568').fetchone()[0],'DEMO')
    def test_metadata_only_detection_deduplicates_a_run_and_queue(self):
        self.active();r=self.detect();self.detect();self.assertEqual(self.desk.db.execute('SELECT count(*) FROM document_runs568').fetchone()[0],1)
        self.assertEqual(self.desk.db.execute("SELECT count(*) FROM jobs WHERE kind='document568_run'").fetchone()[0],1);self.assertEqual(r['state'],'queued')
    def test_explicit_filename_selects_right_template_before_generic_type(self):
        self.active();target=self.active('soit_transmis');self.path='/Dossiers/DEMO/SOIT_TRANSMIS.txt';self.files=Files(self.path,b'SOIT TRANSMIS\nDEMO')
        self.assertEqual(self.detect()['rule_id'],target['id'])
    def test_content_type_can_select_right_rule_when_filename_is_uninformative(self):
        self.active();target=self.active('soit_transmis',actions=['analyze']);self.path='/Dossiers/DEMO/notification.txt';self.files=Files(self.path,b'SOIT TRANSMIS\nDEMO')
        row=self.detect();self.produce(row);updated=self.desk.db.execute('SELECT * FROM document_runs568 WHERE id=?',(row['id'],)).fetchone()
        self.assertEqual(updated['rule_id'],target['id']);self.assertEqual(updated['state'],'verified');self.assertFalse(self.box.appended)
    def test_missing_creation_has_one_bounded_decision_per_version(self):
        self.active();items={self.path.replace('.txt',str(i)+'.txt'):{'etag':'v1'} for i in range(25)}
        rules.observe(self.desk,DEMO,items,limit=10)
        self.assertEqual(self.desk.db.execute("SELECT count(*) FROM document_runs568 WHERE state='decision'").fetchone()[0],10)
    def test_edit_uses_revision_guard_and_suspends_existing_runs(self):
        r=self.active();self.detect();payload={'id':r['id'],'revision':r['revision'],'name':r['name'],'instruction':r['instruction'],'recipe':r['recipe']}
        new=rules.save(self.desk,payload,'cabinet');self.assertEqual(new['state'],'draft');self.assertEqual(new['revision'],2)
        self.assertEqual(self.desk.db.execute('SELECT state FROM document_runs568').fetchone()[0],'paused')
        with self.assertRaisesRegex(Stop,'recharger'):rules.save(self.desk,payload,'cabinet')
    def test_deleted_template_does_not_return_after_reload(self):
        r=self.active();rules.control(self.desk,{'id':r['id'],'action':'delete'},'cabinet');self.assertEqual(len(rules.listing(self.desk,'cabinet')),4)
    def test_user_can_confirm_missing_creation_with_evidence_then_resume(self):
        self.active();meta=self.files.stat(self.path);meta.pop('created');rules.observe(self.desk,DEMO,{self.path:meta});row=self.desk.db.execute('SELECT * FROM document_runs568').fetchone()
        with self.assertRaisesRegex(Stop,'preuve'):rules.run_control(self.desk,{'id':row['id'],'action':'resolve','created':datetime.now(timezone.utc).isoformat()},'cabinet')
        rules.run_control(self.desk,{'id':row['id'],'action':'resolve','created':datetime.now(timezone.utc).isoformat(),'proof':'Accusé de réception vérifié.'},'cabinet')
        self.assertEqual(self.desk.db.execute('SELECT state FROM document_runs568').fetchone()[0],'queued')


class Production(Base):
    def test_full_text_analysis_move_draft_and_task_are_verified(self):
        self.active(actions=['analyze','file_procedure','inform_draft','next_task']);row=self.detect();out=self.produce(row)
        self.assertIn('/PROCEDURE/',out['destination']);self.assertEqual(len(self.files.moves),1);self.assertFalse(self.path in self.files.files)
        self.assertEqual(out['draft_verified']['uid'],'451');self.assertTrue(out['coverage']);self.assertEqual(self.desk.db.execute('SELECT count(*) FROM tasks').fetchone()[0],1)
        self.assertEqual(self.desk.db.execute('SELECT state FROM document_runs568').fetchone()[0],'verified')
        self.produce(row);self.assertEqual(len(self.box.appended),1);self.assertEqual(len(self.files.moves),1)
    def test_resumes_move_that_completed_before_connection_loss(self):
        self.active(actions=['analyze','file_procedure','inform_draft']);row=self.detect();self.files.fail_move=True
        with self.assertRaisesRegex(Stop,'delai'):self.produce(row)
        self.assertNotIn(self.path,self.files.files);rules.run_control(self.desk,{'id':row['id'],'action':'retry'},'cabinet');self.files.fail_move=False
        out=self.produce(row);self.assertTrue(out['draft_verified']);self.assertEqual(len(self.files.moves),1)
    def test_duplicate_destination_is_not_overwritten_or_source_deleted(self):
        self.active(actions=['analyze','file_procedure']);row=self.detect();dest='/Dossiers/DEMO/PROCEDURE/AVIS_DE_RENVOI.txt';self.files.files[dest]=b'Contenu distinct'
        with self.assertRaisesRegex(Stop,'destination_deja'):self.produce(row)
        self.assertEqual(self.files.files[dest],b'Contenu distinct');self.assertIn(self.path,self.files.files);self.assertFalse(self.files.moves)
    def test_same_destination_already_exists_without_ledger_keeps_source(self):
        self.active(actions=['analyze','file_procedure']);row=self.detect();self.files.files['/Dossiers/DEMO/PROCEDURE/AVIS_DE_RENVOI.txt']=self.files.files[self.path]
        with self.assertRaisesRegex(Stop,'source_conservee'):self.produce(row)
        self.assertIn(self.path,self.files.files)
    def test_source_changed_after_detection_blocks_all_effects(self):
        self.active();row=self.detect();self.files.files[self.path]+=b' Changed'
        with self.assertRaisesRegex(Stop,'source_document_modifiee'):self.produce(row)
        self.assertFalse(self.files.moves);self.assertFalse(self.box.appended)
    def test_observation_rule_only_analyzes_and_proposes(self):
        self.active(mode='observe');self.produce(self.detect());self.assertFalse(self.files.moves);self.assertFalse(self.box.appended)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM tasks').fetchone()[0],0)
    def test_profile_cap_prepares_draft_but_not_move_or_calendar(self):
        settings568.save(self.desk,{'autonomy':'prepare'});self.active(actions=['analyze','file_procedure','calendar','inform_draft']);out=self.produce(self.detect())
        self.assertFalse(self.files.moves);self.assertTrue(out['draft_verified']);self.assertTrue(out['pending'])
    def test_pause_before_execution_prevents_remote_effect(self):
        r=self.active();row=self.detect();rules.control(self.desk,{'id':r['id'],'action':'pause'},'cabinet')
        with self.assertRaisesRegex(Stop,'inactive'):self.produce(row)
        self.assertFalse(self.box.appended);self.assertFalse(self.files.moves)
    def test_imap_failure_never_claims_verified_draft(self):
        self.active(actions=['analyze','inform_draft']);row=self.detect();self.box.fail_verify=True
        with self.assertRaisesRegex(Stop,'non_retrouve'):self.produce(row)
        self.assertEqual(self.desk.db.execute('SELECT state FROM document_runs568').fetchone()[0],'error')
    def test_imap_timeout_after_append_adopts_without_second_append(self):
        self.active(actions=['analyze','inform_draft']);row=self.detect();self.box.fail_append=True
        with self.assertRaises(Stop):self.produce(row)
        self.box.fail_append=False;rules.run_control(self.desk,{'id':row['id'],'action':'retry'},'cabinet');self.assertTrue(self.produce(row)['draft_verified']);self.assertEqual(len(self.box.appended),1)
    def test_global_draft_toggle_remains_authoritative(self):
        self.desk.c['orchestrator']={'automatic_mail_drafts_enabled':False};self.active(actions=['analyze','inform_draft']);out=self.produce(self.detect())
        self.assertEqual(out['brouillon_imap'],'non_depose');self.assertFalse(self.box.appended)
    def test_every_page_is_analyzed_and_cached_with_citations(self):
        self.active(actions=['analyze']);row=self.detect();pages=[{'page':i,'label':'p. '+str(i),'text':'Dossier DEMO. Avis de renvoi. Arguments uniques de la page '+str(i)+'.','extraction':'native'} for i in range(1,8)]
        with patch('agent.documents.extract_pages',return_value=pages):out=self.produce(row)
        self.assertEqual(out['coverage']['pages_total'],7);self.assertEqual(out['coverage']['pages_analyzed'],7)
        self.assertTrue(any('p. 7' in str(call['sources']) for call in self.model.calls));self.assertEqual(len(out['coverage']['citations']),7)
    def test_postulant_and_correspondant_inform_confirmed_dominus(self):
        for role in ('postulant','correspondant'):
            procedure.save_case(self.desk,{'matter':'DEMO','role':role,'partner_email':'dominus@example.test','partner_name':'Confrère Exemple'})
            self.active(actions=['analyze','inform_draft']);row=self.detect();self.produce(row)
            self.assertEqual(str(self.box.appended[-1]['To']),'dominus@example.test')
    def test_failed_page_is_never_skipped_as_complete(self):
        self.active();row=self.detect()
        with patch('agent.documents.extract_pages',return_value=[{'page':1,'text':'DEMO','label':'p. 1'},{'page':2,'text':'','label':'p. 2'}]),self.assertRaisesRegex(Stop,'ocr_requis'):self.produce(row)
        self.assertFalse(self.box.appended)
    def test_foreign_case_reference_requires_resolution(self):
        other={**DEMO,'id':'AUTRE','path':'/Dossiers/AUTRE','references':['AUTRE'],'aliases':['Unique autre']}
        private_json(Path(self.desk.c['matters_file']),[DEMO,other]);self.files.files[self.path]=b'AVIS DE RENVOI\nDossier AUTRE\n';self.active()
        with self.assertRaisesRegex(Stop,'ambigu'):self.produce(self.detect())
        self.assertFalse(self.files.moves)


class Calendars(Base):
    def event(self):return {'key':'a'*64,'kind':'audience','title':'Audience DEMO','start':'2026-11-12','end':'2026-11-13','all_day':True,'description':'Dossier DEMO : source p. 1'}
    def target(self):return {'id':'g1','owner':'cabinet','label':'Google privé','provider':'google','config':{'calendar_id':'primary','external_approved':True,'include_details':False},'enabled':True}
    def test_google_event_is_private_minimal_and_verified_without_attendees(self):
        g=Google();proof=calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g)
        self.assertTrue(proof['verified_at']);v=next(iter(g.rows.values()));self.assertNotIn('Dossier DEMO :',v['description']);self.assertNotIn('attendees',v);self.assertEqual(v['visibility'],'private')
    def test_google_repeat_deduplicates_and_reuses_readback(self):
        g=Google();calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g);p=calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g)
        self.assertEqual(g.puts,1);self.assertTrue(p['adopted_existing'])
    def test_existing_human_event_is_preserved_with_its_duration(self):
        g=Google();g.rows['existing']={'id':'existing','summary':'Audience DEMO','description':'Dossier DEMO','start':{'dateTime':'2026-11-12T14:30:00+01:00'},'end':{'dateTime':'2026-11-12T17:30:00+01:00'}}
        event={**self.event(),'start':'2026-11-12T14:30:00+01:00','end':'2026-11-12T15:30:00+01:00','all_day':False}
        p=calendar.deposit(self.desk,'cabinet','DEMO',event,self.target(),g);self.assertTrue(p['adopted_existing']);self.assertEqual(g.puts,0);self.assertIn('16:30',p['end'])
    def test_google_timeout_is_reconciled_from_existing_event(self):
        g=Google();g.fail_after_put=True
        with self.assertRaises(Stop):calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g)
        g.fail_after_put=False;calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g);self.assertEqual(g.puts,1)
    def test_failed_existing_events_read_never_creates_event(self):
        g=Google();g.read_fail=True
        with self.assertRaises(Stop):calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g)
        self.assertEqual(g.puts,0)
    def test_readback_mismatch_is_not_marked_green(self):
        g=Google();g.fake_read=True
        with self.assertRaisesRegex(Stop,'non_conforme'):calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g)
        self.assertEqual(self.desk.db.execute('SELECT state FROM calendar_deposits568').fetchone()[0],'uncertain')
    def test_external_calendar_exclusion_precedes_google_request(self):
        self.desk.c['hybrid_routing']={'excluded_matters':['DEMO']};g=Google()
        with self.assertRaisesRegex(Stop,'interdite'):calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g)
        self.assertFalse(g.puts)
    def test_google_requires_explicit_approval_of_transmission(self):
        with self.assertRaisesRegex(Stop,'autorisation'):calendar.save_target(self.desk,{'provider':'google','label':'Agenda Google'},'cabinet')
    def test_disabled_nextcloud_target_never_reappears_as_default(self):
        self.desk.c['nextcloud'].update(url='https://cloud.example.test',username='alice')
        self.desk.c['calendar']['urls']=['/remote.php/dav/calendars/alice/personnel/']
        url='https://cloud.example.test/remote.php/dav/calendars/alice/personnel/'
        with patch('agent.calendar568.read_secret',side_effect=AssertionError('A metadata read must not read a secret')):
            r=calendar.save_target(self.desk,{'provider':'nextcloud','url':url,'label':'Privé','enabled':False},'cabinet')
            self.assertEqual(calendar.effective_targets(self.desk,'cabinet'),[])
            calendar.save_target(self.desk,{'id':r['id'],'delete':True},'cabinet');self.assertEqual(calendar.effective_targets(self.desk,'cabinet'),[])
    def test_caldav_credentials_are_encrypted_and_never_returned(self):
        r=calendar.save_target(self.desk,{'provider':'caldav','url':'https://cal.example.test/users/me/agenda/','username':'alice','password':'app-password-not-in-public','label':'Privé'},'cabinet')
        self.assertTrue(r['secret_configured']);self.assertNotIn('password_file',r['config']);self.assertNotIn('app-password-not-in-public',json.dumps(r))
        raw=calendar.targets(self.desk,'cabinet',True)[0]['config'];self.assertEqual(read_secret(raw['password_file']),'app-password-not-in-public');self.assertNotIn('app-password-not-in-public',Path(raw['password_file']).read_text())
    def test_caldav_deposit_is_conditional_then_get_verified(self):
        http=CalendarHTTP();target={'id':'n1','label':'Local','provider':'nextcloud','config':{'url':'https://cal.example.test/a/'}}
        with patch('agent.calendar568._http',return_value=http):
            p=calendar.deposit(self.desk,'cabinet','DEMO',self.event(),target);self.assertTrue(p['uid']);calendar.deposit(self.desk,'cabinet','DEMO',self.event(),target)
        self.assertEqual(len(http.puts),1)
    def test_two_calendar_providers_both_get_one_verified_event(self):
        g=Google();http=CalendarHTTP();target={'id':'n1','label':'Local','provider':'nextcloud','config':{'url':'https://cal.example.test/a/'}}
        with patch('agent.calendar568._http',return_value=http):p1=calendar.deposit(self.desk,'cabinet','DEMO',self.event(),target)
        p2=calendar.deposit(self.desk,'cabinet','DEMO',self.event(),self.target(),g);self.assertEqual({p1['provider'],p2['provider']},{'nextcloud','google'})
    def test_all_day_ical_roundtrip_and_unicode_line_folding(self):
        event=self.event();event['title']='Audience éè — '+('é'*90);raw=calendar._ical(event,calendar.event_payload(self.desk,event,'DEMO',{'provider':'nextcloud'}))
        self.assertTrue(all(len(x)<=75 for x in raw.split(b'\r\n')));parsed=parse_events(raw.decode(),'Europe/Paris')[0];self.assertTrue(parsed['all_day']);self.assertEqual(calendar._normal(parsed)[:2],('2026-11-12','2026-11-13'))
    def test_oauth_state_is_owner_bound_once_and_tokens_stay_in_vault(self):
        secret=Path(self.f.c['state_dir'])/'google-client';secret.write_text('synthetic-oauth-secret');secret.chmod(0o600)
        self.desk.c['google_calendar568']={'client_id':'client','client_secret_file':str(secret),'redirect_uri':'https://cabinet.example.test/agent-courriel/api440/m568/google/callback'}
        start=calendar.oauth_start(self.desk,'cabinet');qs={k:v[0] for k,v in parse_qs(urlsplit(start['url']).query).items()};self.assertEqual(qs['code_challenge_method'],'S256');self.assertNotIn('secret',start['url'])
        args={'state':qs['state'],'code':'synthetic-one-time-code'}
        with self.assertRaisesRegex(Stop,'etat_invalide'):calendar.oauth_finish(self.desk,'other-owner',args)
        with patch('agent.calendar568._token_request',return_value={'access_token':'access','token_type':'Bearer','refresh_token':'refresh-secret','scope':' '.join(calendar.SCOPES)}):self.assertTrue(calendar.oauth_finish(self.desk,'cabinet',args)['connected'])
        with self.assertRaisesRegex(Stop,'etat_invalide'):calendar.oauth_finish(self.desk,'cabinet',args)
        auth=self.desk.settings('calendar568:google:cabinet',{});self.assertEqual(read_secret(auth['refresh_token_file']),'refresh-secret')
    def test_oauth_insufficient_scope_is_not_connected(self):
        secret=Path(self.f.c['state_dir'])/'secret';secret.write_text('s');secret.chmod(0o600);self.desk.c['google_calendar568']={'client_id':'c','client_secret_file':str(secret),'redirect_uri':'https://cabinet.example.test/api440/m568/google/callback'}
        state=parse_qs(urlsplit(calendar.oauth_start(self.desk,'cabinet')['url']).query)['state'][0]
        with patch('agent.calendar568._token_request',return_value={'refresh_token':'r','scope':'openid'}),self.assertRaisesRegex(Stop,'permissions_incompletes'):calendar.oauth_finish(self.desk,'cabinet',{'state':state,'code':'c'})
        self.assertFalse(self.desk.settings('calendar568:google:cabinet',{}))


class Deadlines(Base):
    def pages(self,text,ocr=False):return [{'page':1,'label':'p. 1','text':text,'extraction':'ocr' if ocr else 'native'}]
    def profile(self,**kw):procedure.save_case(self.desk,{'matter':'DEMO','role':'direct','lawyer_name':'Exemple','circuit':'long','side':'appelant','distance':'none','ordinary_civil':True,'no_interruption_or_shortening':True,'confirmed':True,'declaration_date':'2026-10-01',**kw})
    def events(self,text,ocr=False):return procedure.source_events(self.desk,self.pages(text,ocr),DEMO,self.path,'sha',persist=False)
    def test_explicit_audience_preserves_page_time_and_no_year_is_guessed(self):
        out,_=self.events('Audience renvoyée au 12 novembre 2026 à 14h30.');self.assertTrue(out);self.assertTrue(any('14:30' in x['start'] for x in out));self.assertEqual(out[0]['citation'],'p. 1')
        short,_=self.events('Audience renvoyée au 12 novembre.');self.assertEqual(short,[])
    def test_no_time_becomes_all_day_not_invented_9am(self):
        out,_=self.events('Audience renvoyée au 12 novembre 2026.');self.assertTrue(out[0]['all_day']);self.assertEqual(out[0]['start'],'2026-11-12')
    def test_scan_requires_control_before_calendar_write(self):
        out,_=self.events('Audience renvoyée au 12 novembre 2026.',True);self.assertFalse(out[0]['safe'])
    def test_appeal_long_uses_declaration_and_three_months(self):
        self.profile();out,_=self.events("Déclaration d'appel déposée le 1 octobre 2026.");derived=[x for x in out if x['derived']];self.assertTrue(derived);self.assertEqual(derived[0]['date'],'2027-01-04');self.assertTrue(derived[0]['safe'])
    def test_bref_delai_requires_actual_receipt_and_current_two_month_rule(self):
        self.profile(circuit='bref',opponent_constituted='no')
        out,_=self.events('Avis de fixation à bref délai reçu le 1 octobre 2026.');derived=[x for x in out if x['derived']];self.assertEqual({x['date'] for x in derived},{'2026-12-01','2026-10-21'})
        no_receipt,_=self.events('Avis de fixation à bref délai daté du 1 octobre 2026.');self.assertFalse(any(x['derived'] for x in no_receipt))
    def test_distance_incident_or_unknown_profile_prevents_auto_calculated_deadline(self):
        for options,text in [({'distance':'etranger'},"Déclaration d'appel déposée le 1 octobre 2026."),({},"Déclaration d'appel déposée le 1 octobre 2026. Une médiation suspend les délais.")]:
            self.profile(**options);out,warnings=self.events(text);self.assertTrue(warnings);self.assertFalse(any(x['safe'] for x in out if x['derived']))
    def test_convoc_filename_does_not_choose_bref_circuit(self):
        out,warnings=self.events("CONVOC\nDéclaration d'appel déposée le 1 octobre 2026.");self.assertFalse(any(x['derived'] for x in out));self.assertTrue(warnings)
    def test_notice_creation_or_date_never_replaces_actual_receipt(self):
        self.profile(circuit='bref',opponent_constituted='no')
        out,_=self.events('Avis de fixation à bref délai daté du 1 octobre 2026, reçu le 3 octobre 2026.')
        self.assertEqual({x['date'] for x in out if x['derived']},{'2026-12-03','2026-10-23'})
    def test_old_explicit_event_is_retained_as_proposal_only(self):
        out,_=self.events('Audience renvoyée au 12 novembre 2025.');self.assertFalse(any(x['safe'] for x in out))


class Interface(Base):
    def test_pages_use_shared_shell_and_no_get_starts_jobs(self):
        for path in ('/parametres/agents','/parametres/agendas','/agents-documents'):
            response=self.request(path);self.assertTrue(response['status'].startswith('200'),response['body']);self.assertIn('/static/rules568.js',response['body']);self.assertIn('ws-ai-launcher',response['body'])
        for path in ('rules/list','document/state','calendars/list'):
            response=self.json_request('/api440/m568/'+path);self.assertTrue(response['status'].startswith('200'),response['body'])
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    def test_new_api_requires_session_origin_csrf_and_avocat_role(self):
        self.assertTrue(self.json_request('/api440/m568/rules/list',auth=False)['status'].startswith('401'))
        r=self.json_request('/api440/m568/rules/compile',{'instruction':'Analyser INJONCTION et préparer un brouillon'},csrf='wrong');self.assertTrue(r['status'].startswith('400'))
        self.assertTrue(allowed('avocat','POST','/api440/m568/rules/save'));self.assertFalse(allowed('assistant','POST','/api440/m568/rules/save'))
        self.assertTrue(allowed('avocat','GET','/parametres/agents'));self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    def test_compile_is_a_persistent_worker_job_and_visible_only_to_its_owner(self):
        r=self.json_request('/api440/m568/rules/compile',{'instruction':'Analyser INJONCTION et préparer un brouillon'});job=json.loads(r['body'])['job']
        self.assertEqual(self.desk.db.execute('SELECT kind FROM jobs WHERE id=?',(job,)).fetchone()[0],'document568_compile');self.desk.work_once()
        v=json.loads(self.json_request('/api440/m568/rules/job?id='+str(job))['body']);self.assertEqual(v['state'],'done');self.assertTrue(v['result']['recipe'])
        self.desk.db.execute('UPDATE jobs SET args=? WHERE id=?',(json.dumps({'owner':'another-user'}),job));self.desk.db.commit()
        self.assertTrue(self.json_request('/api440/m568/rules/job?id='+str(job))['status'].startswith('400'))
    def test_simulation_produces_no_remote_effect_or_job(self):
        recipe=rules.listing(self.desk,'cabinet')[0]['recipe'];r=self.json_request('/api440/m568/rules/simulate',{'recipe':recipe,'filename':'AVIS_DE_RENVOI.pdf','created':datetime.now(timezone.utc).isoformat(),'text':'AVIS DE RENVOI'})
        self.assertEqual(json.loads(r['body'])['match'],'match');self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    def test_google_callback_removes_authorization_code_and_never_uses_third_party_resources(self):
        with patch('agent.calendar568.oauth_finish',return_value={'connected':True}):r=self.json_request('/api440/m568/google/callback?state=one&code=secret-code')
        self.assertTrue(r['status'].startswith('303'));self.assertNotIn('secret-code',r['headers']['Location']);self.assertEqual(r['headers']['Referrer-Policy'],'no-referrer')
    def test_migration_and_resume_preserve_rule_run_and_verified_proofs(self):
        self.active(actions=['analyze','inform_draft']);row=self.detect();self.produce(row)
        from agent.desk import Desk
        second=Desk(self.desk.c)
        try:
            self.assertEqual(second.db.execute('SELECT state FROM document_runs568 WHERE id=?',(row['id'],)).fetchone()[0],'verified')
            self.assertTrue(second.db.execute("SELECT 1 FROM document_effects568 WHERE step='inform_draft' AND state='verified'").fetchone())
        finally:second.db.close()


if __name__=='__main__':unittest.main()

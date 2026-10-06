"""5.6.8 : scénarios métier, état durable, identité, confidentialité et effets réels simulés."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
from email.utils import format_datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from agent.common import Stop,private_json
from agent.desk import Desk
from agent.mailbox import Mail
from agent import settings568 as settings,proactive568 as proactive,plans568 as plans,missions567 as missions
from agent import followup568 as followup,talk568 as talk,news568 as news,voice568 as voice
from agent.standalone_auth import allowed
import test_v567 as old
import test_agent as fixtures


def sent(uid='1',text='Je vous enverrai le contrat demain.',subject='DEMO : travail annoncé',sender='cabinet@example.test',flags=()):
    msg=EmailMessage();msg['From']=sender;msg['To']='client@example.test';msg['Subject']=subject
    msg['Message-ID']='<sent-'+uid+'@example.test>';msg['Date']=format_datetime(datetime.now(timezone.utc));msg.set_content(text)
    return Mail(uid,'77','Sent',set(flags),datetime.now(timezone.utc),msg)


class Box:
    def __init__(self,rows):self.rows={m.uid:m for m in rows};self.validity='77';self.closed=False;self.appended=[];self.saved={};self.fail_verify=False;self.fail_append=False;self.replies=[]
    def select(self,folder):return self.validity
    def search(self,folder,*args):return list(self.rows)
    def fetch(self,folder,uid):return self.rows[uid]
    def close(self):self.closed=True
    def thread(self,mail):return self.replies
    def find_own_draft(self,mid):return mid in self.saved
    def append_draft(self,msg):
        self.appended.append(msg);self.saved[str(msg['Message-ID'])]=msg
        if self.fail_append:raise Stop('delai_http_depasse')
    def verify_draft(self,msg):
        if self.fail_verify:raise Stop('brouillon_non_retrouve')
        saved=self.saved.get(str(msg['Message-ID']))
        if not saved or saved.get_content()!=msg.get_content():raise Stop('brouillon_contenu_non_conforme')
        return {'folder':'Drafts','uid':'451','uidvalidity':'90','verified_at':datetime.now(timezone.utc).isoformat()}


class Base(old.Base):
    def setUp(self):
        super().setUp();settings.save(self.desk,{'enabled':True,'primary_address':'cabinet@example.test'})
        self.desk.setting('conflicts500:gate',False)
    def event(self,**kw):
        return proactive.record(self.desk,'cabinet','sent','source1','version1',kw.pop('kind','E02'),
             {'subject':'DEMO : engagement','quote':'Je vous enverrai le contrat demain.',**kw},'DEMO')


class Text(unittest.TestCase):
    stamp=datetime(2026,10,6,22,30,tzinfo=timezone.utc)
    def test_quoted_promises_and_signature_are_excluded(self):
        text='Bonjour.\n> Je vous enverrai les conclusions demain.\nMerci.\n--\nJe vous adresserai un contrat demain.'
        self.assertEqual(proactive.extract_commitments(text,self.stamp,'Europe/Paris'),[])
    def test_old_thread_is_not_reinterpreted(self):
        for delim in ('Le mardi 6 octobre X a écrit :','From: Someone','De : Someone','On 6 Oct Someone wrote:'):
            with self.subTest(delim=delim):self.assertEqual(proactive.extract_commitments('Merci.\n'+delim+'\nJe vous adresserai un contrat demain.',self.stamp,'Europe/Paris'),[])
    def test_relative_date_uses_message_timezone(self):
        self.assertEqual(proactive.interpret_due('demain',self.stamp,'Europe/Paris'),'2026-10-08')
        self.assertEqual(proactive.interpret_due('demain',self.stamp,'UTC'),'2026-10-07')
    def test_weekday_and_approximate_dates_require_precision(self):
        for text in ('vendredi','la semaine prochaine','avant le 12','dans quinze jours'):
            self.assertEqual(proactive.interpret_due(text,self.stamp,'UTC'),'')
    def test_explicit_date_and_conditional_promise(self):
        r=proactive.extract_commitments('Je vous enverrai un projet le 15/10/2026 après réception des pièces.',self.stamp,'UTC')[0]
        self.assertEqual(r['due'],'2026-10-15');self.assertEqual(r['state'],'conditional');self.assertTrue(r['condition'])
    def test_request_for_documents_is_a_distinct_kind(self):
        r=proactive.extract_commitments('Merci de nous transmettre les pièces.',self.stamp,'UTC')[0];self.assertEqual(r['kind'],'E03')
    def test_invalid_explicit_date_is_not_corrected_silently(self):
        with self.assertRaises(Stop):proactive.interpret_due('31/02/2026',self.stamp,'UTC')


class Settings(Base):
    def test_identity_must_belong_to_configured_account(self):
        with self.assertRaisesRegex(Stop,'identite_hors'):settings.save(self.desk,{'primary_address':'outsider@example.test'})
    def test_two_active_profiles_cannot_duplicate_the_same_identity(self):
        with self.assertRaisesRegex(Stop,'deja_observee'):settings.save(self.desk,{'enabled':True,'primary_address':'cabinet@example.test'},'another')
    def test_invalid_types_and_times_are_refused(self):
        for data in ({'enabled':'yes'},{'briefing_time':'29:00'},{'timezone':'Nobody/Nowhere'},{'weekdays':[7]},{'daily_plan_limit':True},{'roles':['root']}):
            with self.subTest(data=data),self.assertRaises(Stop):settings.save(self.desk,data)
    def test_cabinet_and_matter_policy_use_most_restrictive_mode(self):
        settings.save(self.desk,{'matter_modes':{'DEMO':'observe'},'autonomy':'organize'})
        self.assertEqual(settings.mode(self.desk,'cabinet','DEMO'),'observe')
    def test_excluded_matter_and_global_pause_block_preparation(self):
        settings.save(self.desk,{'excluded_matters':['DEMO']});self.assertEqual(settings.mode(self.desk,'cabinet','DEMO'),'observe')
        settings.save(self.desk,{'excluded_matters':[]});self.desk.setting('missions567:pause',True);self.assertEqual(settings.mode(self.desk,'cabinet','DEMO'),'observe')
    def test_unknown_secret_field_cannot_be_inserted_in_profile(self):
        with self.assertRaisesRegex(Stop,'inconnu'):settings.save(self.desk,{'api_key':'do-not-accept'})
    def test_vacation_prepares_no_new_automatic_work(self):
        settings.save(self.desk,{'vacation_until':'2099-01-01'});self.assertEqual(settings.mode(self.desk,'cabinet','DEMO'),'observe')
    def test_identity_removed_from_server_configuration_is_rechecked(self):
        self.desk.c['mail']['own_addresses']=[]
        with self.assertRaisesRegex(Stop,'identite_hors'):settings.check_owner(self.desk,'cabinet')


class Events(Base):
    def test_sent_message_is_evidence_and_same_scan_is_idempotent(self):
        b=Box([sent()]);r=proactive.sent_scan(self.desk,box=b);self.assertEqual(r['commitments_found'],1)
        proactive.sent_scan(self.desk,box=b);self.assertEqual(self.desk.db.execute('SELECT count(*) FROM commitments_v568').fetchone()[0],1)
        row=self.desk.db.execute('SELECT * FROM commitments_v568').fetchone();e=json.loads(row['evidence'])
        self.assertEqual(e['folder'],'Sent');self.assertEqual(e['uidvalidity'],'77');self.assertEqual(e['category'],'sent_message')
    def test_spoofed_from_and_drafts_do_not_create_sent_events(self):
        b=Box([sent(sender='fake@example.test'),sent('2',flags=['\\Draft'])]);proactive.sent_scan(self.desk,box=b)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM events_v568').fetchone()[0],0)
    def test_uidvalidity_change_rebases_window_without_mixing_identifiers(self):
        b=Box([sent()]);proactive.sent_scan(self.desk,box=b);b.validity='88';b.rows['1'].uidvalidity='88'
        self.assertTrue(proactive.sent_scan(self.desk,box=b)['uidvalidity_reset'])
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM commitments_v568').fetchone()[0],1)
        evidence=json.loads(self.desk.db.execute('SELECT evidence FROM commitments_v568').fetchone()[0])
        self.assertEqual(evidence['uidvalidity'],'88');self.assertEqual(evidence['previous_uids'][0]['uidvalidity'],'77')
    def test_scan_is_bounded_and_cursor_resumes_remaining_uids(self):
        b=Box([sent(str(i)) for i in range(1,56)]);self.assertEqual(proactive.sent_scan(self.desk,box=b)['checked'],50)
        self.assertEqual(proactive.sent_scan(self.desk,box=b)['checked'],5)
    def test_invalid_date_in_one_mail_does_not_block_following_messages(self):
        b=Box([sent('1',text='Je vous enverrai le contrat le 31/02/2026.'),sent('2')]);proactive.sent_scan(self.desk,box=b)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM commitments_v568').fetchone()[0],1)
        proactive.process(self.desk);self.assertTrue(self.desk.db.execute("SELECT 1 FROM events_v568 WHERE state='decision'").fetchone())
    def test_condition_blocks_automatic_plan(self):
        eid=self.event(condition='après réception des pièces');proactive.process(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT state FROM events_v568 WHERE id=?',(eid,)).fetchone()[0],'decision')
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM plans_v568').fetchone()[0],0)
    def test_missing_matter_asks_one_precise_question(self):
        eid=proactive.record(self.desk,'cabinet','sent','other','1','E02',{'subject':'Promesse sans dossier','quote':'Je vous enverrai un projet demain.'})
        proactive.process(self.desk);self.assertEqual(self.desk.db.execute('SELECT state FROM events_v568 WHERE id=?',(eid,)).fetchone()[0],'decision')
    def test_same_event_survives_restart_with_same_plan(self):
        eid=self.event()
        with patch('agent.missions567._launch'):proactive.process(self.desk)
        first=self.desk.db.execute('SELECT plan_id FROM events_v568 WHERE id=?',(eid,)).fetchone()[0]
        d=Desk(self.desk.c)
        try:
            with patch('agent.missions567._launch'):proactive.process(d)
            self.assertEqual(d.db.execute('SELECT count(*) FROM plans_v568').fetchone()[0],1)
            self.assertEqual(d.db.execute('SELECT plan_id FROM events_v568 WHERE id=?',(eid,)).fetchone()[0],first)
        finally:d.db.close()
    def test_observation_profile_proposes_without_executing(self):
        settings.save(self.desk,{'autonomy':'observe'});self.event();proactive.process(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT state FROM plans_v568').fetchone()[0],'suggested')
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM missions_v567').fetchone()[0],0)
    def test_dismissal_keeps_source_and_can_be_restored(self):
        eid=self.event();proactive.control(self.desk,{'id':eid,'action':'dismiss'});proactive.process(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM plans_v568').fetchone()[0],0)
        proactive.control(self.desk,{'id':eid,'action':'restore'});self.assertEqual(self.desk.db.execute('SELECT state FROM events_v568').fetchone()[0],'detected')
    def test_other_user_cannot_read_or_correct_an_event(self):
        eid=self.event();self.assertEqual(proactive.snapshot(self.desk,'outsider')['events'],[])
        with self.assertRaisesRegex(Stop,'absent'):proactive.control(self.desk,{'id':eid,'action':'dismiss'},'outsider')
    def test_dismissal_suspends_the_plan_already_created(self):
        eid=self.event()
        with patch('agent.missions567._launch'):
            proactive.process(self.desk);proactive.control(self.desk,{'id':eid,'action':'dismiss'})
        self.assertEqual(self.desk.db.execute('SELECT state FROM plans_v568').fetchone()[0],'paused')
    def test_moved_single_meeting_supersedes_same_uid(self):
        ev={'uid':'meeting@example.test','source_url':'https://cloud.example.test/cal/','summary':'Visio DEMO',
            'start':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'end':(datetime.now(timezone.utc)+timedelta(days=1,hours=1)).isoformat()}
        proactive.calendar_events(self.desk,[ev]);ev['start']=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat();proactive.calendar_events(self.desk,[ev])
        rows=self.desk.db.execute("SELECT object_key,state FROM events_v568 WHERE source='calendar'").fetchall()
        self.assertEqual(len({r['object_key'] for r in rows}),1);self.assertEqual(sum(r['state']=='superseded' for r in rows),1)
    def test_cancelled_calendar_tombstone_suspends_the_old_plan(self):
        from agent.dav import parse_events
        ev={'uid':'meeting@example.test','source_url':'https://cloud.example.test/cal/','summary':'Visio DEMO',
            'start':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
        proactive.calendar_events(self.desk,[ev])
        with patch('agent.missions567._launch'):proactive.process(self.desk)
        raw='BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:meeting@example.test\nSTATUS:CANCELLED\nEND:VEVENT\nEND:VCALENDAR'
        self.assertEqual(parse_events(raw,'UTC'),[])
        cancelled=parse_events(raw,'UTC',include_cancelled=True)[0];cancelled['source_url']=ev['source_url'];proactive.calendar_events(self.desk,[cancelled])
        self.assertEqual(self.desk.db.execute('SELECT state FROM plans_v568').fetchone()[0],'paused')
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM events_v568 WHERE kind='E12'").fetchone())
    def test_recurrent_occurrences_remain_distinct(self):
        ev={'uid':'weekly@example.test','source_url':'https://cloud.example.test/cal/','summary':'Audience DEMO',
            'start':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),'recurrence_id':'20261007T120000Z'}
        proactive.calendar_events(self.desk,[ev]);ev['recurrence_id']='20261014T120000Z';ev['start']=(datetime.now(timezone.utc)+timedelta(days=8)).isoformat();proactive.calendar_events(self.desk,[ev])
        self.assertEqual(self.desk.db.execute('SELECT count(DISTINCT object_key) FROM events_v568').fetchone()[0],2)
    def test_daily_limit_retains_unprocessed_event(self):
        settings.save(self.desk,{'daily_plan_limit':1});self.event();proactive.record(self.desk,'cabinet','sent','different','v2','E02',{'subject':'DEMO nouveau','quote':'Je vous adresserai un contrat demain.'},'DEMO')
        with patch('agent.missions567._launch'):proactive.process(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM plans_v568').fetchone()[0],1)
        self.assertEqual(self.desk.db.execute("SELECT count(*) FROM events_v568 WHERE state='detected'").fetchone()[0],1)
        self.assertFalse(proactive.needs_cycle(self.desk,'cabinet'))
    def test_document_inventory_ignores_own_output_and_old_files(self):
        now=format_datetime(datetime.now(timezone.utc));old_date=format_datetime(datetime(2010,1,1,tzinfo=timezone.utc))
        proactive.document_events(self.desk,{'id':'DEMO','path':'/Dossiers/DEMO'},
           {'/Dossiers/DEMO/Conclusions adverses.pdf':{'modified':now,'etag':'1'},'/Dossiers/DEMO/AxiorHub note.pdf':{'modified':now,'etag':'2'},'/Dossiers/DEMO/Ancien contrat.pdf':{'modified':old_date,'etag':'3'}})
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM events_v568').fetchone()[0],1)
        self.assertEqual(self.desk.db.execute('SELECT kind FROM events_v568').fetchone()[0],'E06')


class Plans(Base):
    def payload(self):return {'title':'Préparer une note après analyse','matter':'DEMO','request_key':'test-plan-568-xxxxxxxx','steps':[
        {'role':'A2','instruction':'Analyse les documents sources.','analysis_only':True},
        {'role':'A3','instruction':'Prépare une note de plaidoirie.','depends_on':[0]}]}
    def test_dependency_waits_for_actual_answer(self):
        with patch('agent.missions567._launch'):
            p=plans.create(self.desk,self.payload());self.assertTrue(p['steps'][0]['mission_id']);self.assertFalse(p['steps'][1]['mission_id'])
            mid=p['steps'][0]['mission_id']
            real_get=missions.get
            def fake_get(d,i,*args,**kw):
                r=real_get(d,i,*args,**kw)
                if i==mid:r['state']='answered';r['result']['text']='Source analysée.'
                return r
            with patch('agent.missions567.get',side_effect=fake_get):plans.advance(self.desk,p['id'])
            self.assertTrue(plans.get(self.desk,p['id'])['steps'][1]['mission_id'])
    def test_cycles_and_more_than_eight_steps_are_refused(self):
        p=self.payload();p['steps'][0]['depends_on']=[1]
        with self.assertRaisesRegex(Stop,'dependances'):plans.create(self.desk,p)
        p=self.payload();p['steps']*=5
        with self.assertRaisesRegex(Stop,'huit'):plans.create(self.desk,p)
    def test_repeated_plan_request_does_not_create_missions_twice(self):
        with patch('agent.missions567._launch'):
            p=plans.create(self.desk,self.payload());q=plans.create(self.desk,self.payload());self.assertEqual(p['id'],q['id'])
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM missions_v567').fetchone()[0],1)
    def test_reused_key_with_different_instruction_is_refused(self):
        with patch('agent.missions567._launch'):plans.create(self.desk,self.payload())
        p=self.payload();p['title']='Autre résultat'
        with self.assertRaisesRegex(Stop,'reutilisee'):plans.create(self.desk,p)
    def test_pause_blocks_dependent_launch(self):
        with patch('agent.missions567._launch'):
            p=plans.create(self.desk,self.payload());plans.control(self.desk,{'id':p['id'],'action':'pause'});plans.advance(self.desk,p['id'])
        self.assertFalse(plans.get(self.desk,p['id'])['steps'][1]['mission_id'])
    def test_concurrent_request_creates_one_plan_and_one_initial_mission(self):
        cfg=deepcopy(self.desk.c);payload=self.payload()
        def create_one(_):
            d=Desk(cfg)
            try:return plans.create(d,payload)['id']
            finally:d.db.close()
        with patch('agent.missions567._launch'),ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(create_one,range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(self.desk.db.execute('SELECT count(*) FROM missions_v567').fetchone()[0],1)


class TalkClient:
    def __init__(self):self.created=0;self.token='abcdefgh';self.found='';self.bad=False;self.uncertain=False
    def find(self,name):return self.found
    def create(self,name):
        self.created+=1;self.found=self.token
        if self.uncertain:raise Stop('delai_http_depasse')
        return self.token
    def verify(self,token,name):
        if self.bad:raise Stop('salon_talk_participants_inattendus')
        return {'type':2,'participants':1}


class Talk(Base):
    def event(self):
        settings.save(self.desk,{'talk_enabled':True})
        return proactive.record(self.desk,'cabinet','calendar','meeting','version','E11',{'title':'Visio DEMO','video':True},'DEMO')
    def test_private_room_is_created_once_and_read_back(self):
        self.desk.c['nextcloud']['url']='https://cloud.example.test';eid=self.event();c=TalkClient()
        r=talk.prepare(self.desk,eid,client=c);talk.prepare(self.desk,eid,client=c)
        self.assertEqual(c.created,1);self.assertEqual(r['state'],'verified');self.assertFalse(r['invitation_sent']);self.assertFalse(r['calendar_modified'])
    def test_uncertain_creation_is_reconciled_by_name_not_repeated(self):
        self.desk.c['nextcloud']['url']='https://cloud.example.test';eid=self.event();c=TalkClient();c.uncertain=True
        with self.assertRaises(Stop):talk.prepare(self.desk,eid,client=c)
        talk.prepare(self.desk,eid,client=c);self.assertEqual(c.created,1)
    def test_unknown_effect_does_not_recreate_a_missing_room(self):
        eid=self.event();c=TalkClient();c.uncertain=True
        with self.assertRaises(Stop):talk.prepare(self.desk,eid,client=c)
        c.found=''
        with self.assertRaisesRegex(Stop,'incertitude'):talk.prepare(self.desk,eid,client=c)
        self.assertEqual(c.created,1)
    def test_an_unexpected_participant_prevents_green_verified_state(self):
        eid=self.event();c=TalkClient();c.bad=True
        with self.assertRaisesRegex(Stop,'participants'):talk.prepare(self.desk,eid,client=c)
        self.assertNotEqual(self.desk.db.execute('SELECT state FROM talk_rooms_v568').fetchone()[0],'verified')
    def test_automatic_creation_obeys_pause_and_ownership(self):
        eid=self.event();self.desk.setting('missions567:pause',True)
        with self.assertRaises(Stop):talk.prepare(self.desk,eid,client=TalkClient())
        with self.assertRaises(Stop):talk.prepare(self.desk,eid,'outsider',TalkClient())
    def test_room_adapter_sends_no_invite_or_public_type(self):
        client=talk.Talk.__new__(talk.Talk);calls=[];client.call=lambda method,path,data=None:calls.append((method,path,data)) or {'token':'abcdefgh'}
        self.assertEqual(client.create('Private'),'abcdefgh');self.assertEqual(calls[0][2],{'roomType':2,'roomName':'Private'})
    def test_existing_link_is_read_back_without_creating_another_room(self):
        self.desk.c['nextcloud']['url']='https://cloud.example.test';eid=self.event();client=TalkClient()
        row=self.desk.db.execute('SELECT payload FROM events_v568 WHERE id=?',(eid,)).fetchone();payload=json.loads(row[0]);payload['talk_token']='abcdefgh'
        self.desk.db.execute('UPDATE events_v568 SET payload=? WHERE id=?',(json.dumps(payload),eid));self.desk.db.commit()
        result=talk.prepare(self.desk,eid,client=client);self.assertEqual(client.created,0);self.assertTrue(result['url'].endswith('/call/abcdefgh'))
    def test_existing_talk_url_must_belong_to_configured_server(self):
        self.desk.c['nextcloud']['url']='https://cloud.example.test'
        self.assertEqual(talk.linked_token(self.desk,{'location':'https://cloud.example.test/call/abcdefgh'}),'abcdefgh')
        self.assertEqual(talk.linked_token(self.desk,{'location':'https://cloud.example.test.evil.test/call/abcdefgh'}),'')


class News(Base):
    source='https://www.courdecassation.fr/actualites.xml'
    def rss(self):return ('<rss><channel><item><title>Contrat : nouvelle décision</title><link>https://www.courdecassation.fr/decision/123</link><pubDate>'+format_datetime(datetime.now(timezone.utc))+'</pubDate><description>Un contrat à examiner.</description></item></channel></rss>').encode()
    def test_official_feeds_deduplicate_and_keep_date_and_link(self):
        settings.save(self.desk,{'news_enabled':True,'legal_fields':['contrat'],'news_sources':[self.source]})
        news.collect(self.desk,fetch=lambda _:self.rss());news.collect(self.desk,fetch=lambda _:self.rss())
        rows=news.listing(self.desk)['items'];self.assertEqual(len(rows),1);self.assertTrue(rows[0]['published']);self.assertTrue(rows[0]['url'].startswith('https://'))
    def test_private_urls_and_external_entities_are_refused(self):
        for url in ('http://www.courdecassation.fr/rss','https://localhost/rss','https://169.254.169.254/a','https://courdecassation.fr.evil.test/rss','https://user:pass@official.example.test/rss'):
            with self.subTest(url=url),self.assertRaises(Stop):news.validate_source(url)
        with self.assertRaisesRegex(Stop,'xml_refuse'):news.parse(b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///etc/passwd">]><rss/>',self.source)
    def test_undated_or_future_news_is_not_presented_as_recent(self):
        self.assertEqual(news.parse(b'<rss><channel><item><title>X</title><link>/decision/X</link></item></channel></rss>',self.source),[])
    def test_source_error_is_visible_and_not_a_successful_empty_news_run(self):
        settings.save(self.desk,{'news_enabled':True,'legal_fields':['contrat'],'news_sources':[self.source]})
        with self.assertRaisesRegex(Stop,'indisponibles'):news.collect(self.desk,fetch=lambda _:(_ for _ in ()).throw(Stop('http_503')))
        self.assertEqual(news.listing(self.desk)['last_run']['errors'][0]['reason'],'http_503')


class Voice(Base):
    def wav(self):
        b=io.BytesIO()
        with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0\0'*200)
        return b.getvalue()
    def test_local_tts_is_cached_with_private_permissions(self):
        with patch('agent.assistant567.speech',return_value=self.wav()) as synth:
            self.assertEqual(voice.speech(self.desk,'Texte fictif.'),voice.speech(self.desk,'Texte fictif.'));self.assertEqual(synth.call_count,1)
        path=next((Path(self.desk.c['state_dir'])/'voice568-cache').glob('*.wav'));self.assertEqual(path.stat().st_mode&0o777,0o600)
    def test_cache_is_scoped_to_user_and_matter(self):
        with patch('agent.assistant567.speech',return_value=self.wav()) as synth:
            voice.speech(self.desk,'Texte fictif.',matter='DEMO');voice.speech(self.desk,'Texte fictif.',matter='OTHER');self.assertEqual(synth.call_count,2)
    def test_disabled_external_voice_falls_back_without_call(self):
        self.desk.c['speech568']={'provider':'elevenlabs','external_allowed':False,'fallback_local':True}
        with patch('agent.voice568.HTTP') as remote,patch('agent.assistant567.speech',return_value=self.wav()):voice.speech(self.desk,'Texte fictif.')
        remote.assert_not_called();self.assertEqual(self.desk.settings('voice568:last_fallback:cabinet')['reason'],'voix_externe_non_autorisee')
    def test_excluded_case_blocks_remote_voice_before_transmission(self):
        self.desk.c['speech568']={'provider':'elevenlabs','external_allowed':True,'fallback_local':False}
        self.desk.c['hybrid_routing']={'mode':'manual','external_client_data_approved':True,'allowed_purposes':['assistant'],'excluded_matters':['DEMO']}
        with patch('agent.voice568.HTTP') as remote,self.assertRaisesRegex(Stop,'dossier_exclu'):voice.speech(self.desk,'Texte fictif.',matter='DEMO')
        remote.assert_not_called()
    def test_budget_is_reserved_before_api_call_and_uncertainty_counts(self):
        self.desk.c['speech568']={'usd_per_1000_chars':'1','per_request_usd':'1','monthly_usd':'0.01'}
        rid=voice._reserve(self.desk,'cabinet','12345678');self.desk.db.execute("UPDATE voice_usage_v568 SET state='uncertain' WHERE id=?",(rid,));self.desk.db.commit()
        with self.assertRaisesRegex(Stop,'mensuel'):voice._reserve(self.desk,'cabinet','12345678')
    def test_missing_and_nonfinite_prices_are_refused(self):
        for value in (0,'NaN','Infinity','-1'):
            self.desk.c['speech568']={'usd_per_1000_chars':value}
            with self.subTest(value=value),self.assertRaises(Stop):voice._reserve(self.desk,'cabinet','x')
    def test_local_adapter_rejects_remote_endpoint(self):
        self.desk.c['speech568']={'provider':'kokoro','url':'https://evil.example.test','fallback_local':False}
        with self.assertRaisesRegex(Stop,'local'):voice.speech(self.desk,'Texte fictif.')
    def test_invalid_waveform_is_refused(self):
        with self.assertRaises(Stop):voice._wav(b'RIFFjunk')
    def test_concurrent_voice_reservations_cannot_overrun_shared_budget(self):
        cfg=deepcopy(self.desk.c);cfg['speech568']={'usd_per_1000_chars':'1','per_request_usd':'1','monthly_usd':'0.01'}
        def reserve_one(_):
            d=Desk(cfg)
            try:
                try:voice._reserve(d,'cabinet','12345678');return 'reserved'
                except Stop:return 'refused'
            finally:d.db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve_one,range(2)))
        self.assertEqual(sorted(results),['refused','reserved'])
    def test_voice_preview_obeys_the_same_exclusion_as_transmission(self):
        self.desk.c['speech568']={'provider':'elevenlabs','external_allowed':True}
        self.desk.c['hybrid_routing']={'mode':'manual','external_client_data_approved':True,'allowed_purposes':['assistant'],'excluded_matters':['DEMO']}
        r=voice.preview(self.desk,'Texte de dossier.',matter='DEMO');self.assertFalse(r['allowed']);self.assertIsNone(r['transmitted_text'])
    def test_external_voice_reads_pseudonyms_and_never_restores_names_remotely(self):
        self.desk.c['speech568']={'provider':'elevenlabs','external_allowed':True,'usd_per_1000_chars':'1','per_request_usd':'1','monthly_usd':'10'}
        self.desk.c['hybrid_routing']={'mode':'manual','external_client_data_approved':True,'allowed_purposes':['assistant'],'excluded_matters':[]}
        r=voice.preview(self.desk,'Contacter client@example.test au 0612345678 dans DEMO.',matter='DEMO')
        self.assertTrue(r['allowed']);self.assertTrue(r['pseudonymized']);self.assertNotIn('client@example.test',r['transmitted_text']);self.assertNotIn('0612345678',r['transmitted_text'])


class Followup(Base):
    def setUp(self):
        super().setUp()
        from agent.common import load_matters
        matters=load_matters(self.desk.c);matters[0]['correspondents']=[{'email':'client@example.test','role':'client'}];private_json(Path(self.desk.c['matters_file']),matters)
        self.original=sent(text='Merci de nous transmettre les pièces.');self.original.timestamp=datetime.now(timezone.utc)-timedelta(days=10)
        self.box=Box([self.original]);proactive.sent_scan(self.desk,box=self.box)
        self.commitment=self.desk.db.execute('SELECT * FROM commitments_v568').fetchone()
    def due(self):
        account=self.desk.c['mail']['host']+'|'+self.desk.c['mail']['username']
        self.desk.setting('proactive568:inbox_cursor:'+proactive.ident('cabinet',account,'INBOX'),{'complete':True,'checked':self.desk.now()})
        followup.due(self.desk);return self.desk.db.execute("SELECT id FROM events_v568 WHERE kind='E05'").fetchone()[0]
    def test_received_acknowledgment_is_not_proof_all_pieces_received(self):
        reply=sent('2',text='Bien reçu.',sender='client@example.test');reply.mailbox='INBOX';reply.msg['In-Reply-To']=self.original.mid
        followup.scan(self.desk,box=Box([reply]));r=self.desk.db.execute('SELECT state,proof FROM commitments_v568').fetchone()
        self.assertEqual(r['state'],'response_detected');self.assertFalse(r['proof'])
    def test_neutral_followup_has_one_verified_draft_and_no_send(self):
        eid=self.due();r=followup.prepare(self.desk,eid,box=self.box);followup.prepare(self.desk,eid,box=self.box)
        self.assertEqual(len(self.box.appended),1);self.assertEqual(r['brouillon_imap'],'verifie');self.assertEqual(r['proof']['uid'],'451');self.assertFalse(r['sent'])
    def test_append_accepted_but_response_lost_is_read_not_duplicated(self):
        eid=self.due();self.box.fail_append=True
        with self.assertRaises(Stop):followup.prepare(self.desk,eid,box=self.box)
        self.box.fail_append=False;followup.prepare(self.desk,eid,box=self.box);self.assertEqual(len(self.box.appended),1)
    def test_unverified_draft_never_turns_the_event_green(self):
        eid=self.due();self.box.fail_verify=True
        with self.assertRaises(Stop):followup.prepare(self.desk,eid,box=self.box)
        self.assertNotEqual(self.desk.db.execute('SELECT state FROM events_v568 WHERE id=?',(eid,)).fetchone()[0],'verified')
    def test_reply_present_at_last_moment_suppresses_draft(self):
        eid=self.due();reply=sent('2',sender='client@example.test');reply.mailbox='INBOX';self.box.replies=[reply]
        self.assertIn('abstention',followup.prepare(self.desk,eid,box=self.box));self.assertFalse(self.box.appended)
        self.assertEqual(self.desk.db.execute('SELECT state FROM commitments_v568').fetchone()[0],'response_detected')
    def test_verified_followup_is_not_enqueued_again_by_periodic_check(self):
        eid=self.due();followup.prepare(self.desk,eid,box=self.box)
        self.desk.db.execute('DELETE FROM jobs');self.desk.db.commit();followup.due(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
    def test_followup_runs_under_the_real_worker_lock_and_records_readback(self):
        self.due()
        with patch('agent.followup568.Mailbox',return_value=self.box):self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs').fetchone()[0],'done')
        row=self.desk.db.execute("SELECT status,verification FROM production_deliverables_v420 WHERE job_kind='followup568_prepare'").fetchone()
        self.assertEqual(row['status'],'verified');self.assertEqual(json.loads(row['verification'])['uid'],'451')
    def test_failed_followup_records_an_error_and_stops_automatic_retries(self):
        eid=self.due();self.box.fail_verify=True
        with patch('agent.followup568.Mailbox',return_value=self.box):self.desk.work_once()
        self.assertEqual(self.desk.db.execute('SELECT state FROM events_v568 WHERE id=?',(eid,)).fetchone()[0],'error')
        count=self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0];followup.due(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],count)
    def test_ready_plan_cannot_override_a_received_response(self):
        with patch('agent.missions567._launch'):proactive.process(self.desk)
        self.desk.db.execute("UPDATE plans_v568 SET state='verified'");self.desk.db.execute("UPDATE commitments_v568 SET state='response_detected'");self.desk.db.commit()
        proactive.advance_all(self.desk,'cabinet');self.assertEqual(self.desk.db.execute('SELECT state FROM commitments_v568').fetchone()[0],'response_detected')
    def test_no_fresh_inbox_read_prevents_claiming_absence_of_response(self):
        followup.due(self.desk);self.assertFalse(self.desk.db.execute("SELECT 1 FROM events_v568 WHERE kind='E05'").fetchone())
    def test_satisfaction_requires_explicit_evidence(self):
        with self.assertRaisesRegex(Stop,'preuve'):proactive.commitment_control(self.desk,{'id':self.commitment['id'],'action':'satisfy'})
        r=proactive.commitment_control(self.desk,{'id':self.commitment['id'],'action':'satisfy','proof':'Pièces contrôlées dans le dossier, confirmées par l’avocat.'});self.assertEqual(r['proof_category'],'user_confirmation')


class UI(Base):
    def test_new_pages_keep_one_shared_shell_and_voice_script(self):
        for path in ('/engagements','/veille','/parametres/proactivite'):
            with self.subTest(path=path):
                r=self.request(path);self.assertTrue(r['status'].startswith('200'),r['body']);self.assertIn('/static/v568.js',r['body']);self.assertIn('ws-ai-launcher',r['body'])
    def test_routes_require_authentication_and_get_never_launches_a_job(self):
        self.assertTrue(self.request('/api440/m568/state',auth=False)['status'].startswith('401'))
        before=self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0];r=self.request('/api440/m568/state');self.assertTrue(r['status'].startswith('200'))
        self.assertEqual(self.desk.db.execute('SELECT count(*) FROM jobs').fetchone()[0],before)
    def test_role_boundaries_for_settings_and_voice(self):
        self.assertTrue(allowed('avocat','GET','/parametres/proactivite'))
        self.assertTrue(allowed('avocat','POST','/api440/m568/profile'))
        self.assertFalse(allowed('assistant','POST','/api440/m568/profile'))
        self.assertFalse(allowed('avocat','POST','/api440/m568/test/talk'))
        self.assertTrue(allowed('assistant','POST','/api440/m568/voice/turn'))
    def test_voice_conversation_uses_fast_function_and_never_document_production(self):
        m=missions.create(self.desk,{'instruction':'Rédige un projet de conclusions pour en discuter.', 'matter':'DEMO',
             'request_key':'voice-conversation-568-test','channel':'voice','analysis_only':True})
        self.assertEqual(m['kind'],'question')
        args=json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?',(m['job_id'],)).fetchone()[0])
        self.assertEqual(args['purpose'],'voice_conversation')
    def test_new_watcher_and_effect_jobs_have_bounded_restart_recovery(self):
        from agent.queue567 import recover
        for kind in ('sent568_scan','received568_scan','proactive568_cycle','talk568_prepare','followup568_prepare','news568_collect'):
            job=self.desk.enqueue(kind,{'event':'source','owner':'cabinet'})
            self.desk.db.execute("UPDATE jobs SET status='running',worker='2',attempts=1 WHERE id=?",(job,))
        self.desk.db.commit();result=recover(self.desk,'2');self.assertEqual(result['resumed'],6)
        self.assertEqual(self.desk.db.execute("SELECT count(*) FROM jobs WHERE status='pending'").fetchone()[0],6)


class Procedural(unittest.TestCase):
    def test_procedural_notification_is_not_generic_ignored_mail(self):
        x=fixtures.EngineTests('test_observation_has_no_mail_write');x.setUp()
        try:
            m=fixtures.mail(subject='RPVA — notification automatique d’audience');m.msg['Auto-Submitted']='auto-generated'
            key=x.engine.process(m);self.assertEqual(x.status(key),'review');self.assertFalse(x.box.appended)
            self.assertEqual(x.engine.state.get(key)[1],'notification_procedurale_a_verifier')
        finally:x.doCleanups()

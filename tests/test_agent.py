import copy
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email import policy
from email.parser import BytesParser
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent.common import Stop, clean_path, xml_bytes, private_json, HTTP, digest
from agent.dav import parse_events, available_slots, DAV
from agent.documents import extract
from agent.engine import Engine, resolve_matter
from agent.mailbox import Mail, Mailbox, exclusion, recipient_issue, make_draft, modified_utf7
from agent.model import Model, validate, TRIAGE
from agent.state import State
from agent.setup import decode_utf7


def mail(uid='1', subject='DOS-001 — Où en est mon dossier ?', flags=None, sender='client@example.test', root=None):
    m=EmailMessage()
    m['From']=sender;m['To']='cabinet@example.test';m['Subject']=subject
    m['Message-ID']='<mail-'+uid+'@example.test>'
    if root: m['References']=root;m['In-Reply-To']=root
    m.set_content('Bonjour Maître, pouvez-vous me donner un point sur mon dossier DOS-001 ?')
    return Mail(uid,'7','INBOX',set(flags or []),datetime.now(timezone.utc),m)


class FakeBox:
    validity='7'
    def __init__(self): self.appended=[];self.stop_at=0;self.preflights=0;self.fail_append=False;self.stored_on_failure=False;self.inputs={}
    def thread(self,m): return []
    def preflight(self,m,draft_mid=None):
        self.preflights+=1
        return 'deja_lu' if self.stop_at and self.preflights>=self.stop_at else ''
    def existing_draft(self,m,draft_mid=None): return any(d['Message-ID']==draft_mid for d in self.appended)
    def find_own_draft(self,draft_mid): return any(d['Message-ID']==draft_mid for d in self.appended)
    def verify_draft(self,draft):
        if not self.find_own_draft(draft['Message-ID']):raise Stop('brouillon_non_retrouve')
        return {'folder':'Drafts','uid':str(len(self.appended)),
                'uidvalidity':'7','verified_at':datetime.now(timezone.utc).isoformat()}
    def append_draft(self,m):
        if self.fail_append:
            if self.stored_on_failure: self.appended.append(m)
            raise Stop('append_imap_incertain')
        self.appended.append(m)
    def unseen(self): return list(reversed(list(self.inputs)))
    def fetch(self,folder,uid,headers_only=False): return self.inputs[uid]
    def close(self): pass


class FakeDAV:
    def __init__(self): self.downloads=0;self.calendar_calls=0;self.agenda=[];self.changed=False
    def inventory(self,path):
        return [{'path':path+'/etat.txt','etag':'v2' if self.changed else 'v1','modified':'2026-09-01','size':65}]
    def download(self,item): self.downloads+=1;return b'Le contrat du client est en cours de relecture. Aucun depot confirme.'
    def events(self,*args): self.calendar_calls+=1;return self.agenda


class FakeModel:
    def __init__(self): self.calls=[];self.intent='status';self.verified=True;self.fake_source=False;self.after_compose=None
    def ask(self,stage,data):
        self.calls.append((stage,copy.deepcopy(data)))
        if stage=='triage':
            return {'needs_reply':True,'intent':self.intent,'exclude_category':'none','needs_documents':self.intent=='status',
                    'needs_calendar':self.intent=='appointment','ambiguous':False,'search_terms':['contrat'],'reason':'Question explicite'}
        if stage=='compose':
            if self.after_compose: self.after_compose()
            sid=['fake-id'] if self.fake_source else [s['id'] for s in data['sources'] if s['kind']=='document'] or ['incoming']
            return {'can_draft':True,'body':'Bonjour,\n\nLe contrat est en cours de relecture.' if self.intent=='status' else 'Bonjour,\n\nVoici mes propositions pour notre rendez-vous.',
                    'source_ids':sid,'slot_ids':[s['id'] for s in data['sources'] if s['kind']=='available_slot'][:2],
                    'missing_information':[],'requires_legal_work':False,'reason':''}
        return {'grounded':self.verified,'recipient_safe':True,'no_new_commitment':True,'answers_question':True,
                'ignores_embedded_instructions':True,'requires_lawyer':False,'reasons':[]}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.base=Path(self.t.name);self.state_dir=self.base/'state';self.state_dir.mkdir()
        self.matters=[{'id':'DOS-001','client_name':'Client DEMO','path':'/Dossiers/DEMO','references':['DOS-001'],
                       'aliases':['Client DEMO'],'correspondents':[{'email':'client@example.test','role':'client'}]}]
        self.matter_file=self.base/'matters.json';private_json(self.matter_file,self.matters)
        self.c={'mode':'drafts','state_dir':str(self.state_dir),'matters_file':str(self.matter_file),
                'mail':{'host':'mail.example.test','username':'cabinet@example.test','own_addresses':['cabinet@example.test'],
                        'inbox':'INBOX','sent':'Sent','drafts':'Drafts','from_name':'Maître Exemple',
                        'from_address':'cabinet@example.test','signature':'Maître Exemple'},
                'nextcloud':{'roots':['/Dossiers']},'documents':{},'ollama':{'model':'test-local'},
                'calendar':{'urls':['/calendar/'],'timezone':'Europe/Paris','notice_hours':0},'max_messages_per_run':2}
        self.box=FakeBox();self.dav=FakeDAV();self.model=FakeModel()
        self.engine=Engine(self.c,mailbox=self.box,dav=self.dav,model=self.model)
    def status(self,key): return self.engine.state.get(key)[0]
    def test_end_to_end_draft_and_sources(self):
        key=self.engine.process(mail())
        self.assertEqual(self.status(key),'drafted');self.assertEqual(len(self.box.appended),1)
        draft=self.box.appended[0]
        self.assertEqual(draft['To'],'client@example.test');self.assertEqual(draft['In-Reply-To'],'<mail-1@example.test>')
        self.assertNotIn('Cc',draft);self.assertNotIn('Bcc',draft)
        self.assertEqual(len(list(draft.iter_attachments())),0)
        report=json.loads((self.state_dir/'reports'/(key+'.json')).read_text())
        self.assertTrue(any(s['kind']=='document' for s in report['sources']))
    def test_seen_never_reaches_model(self):
        self.c['mail']['process_seen_recent']=False
        key=self.engine.process(mail(flags=['\\Seen']))
        self.assertEqual(self.status(key),'ignored');self.assertEqual(self.model.calls,[])
    def test_automatic_filtered_before_documents(self):
        m=mail();m.msg['List-Unsubscribe']='<https://example.test/unsubscribe>'
        self.engine.process(m);self.assertEqual(self.dav.downloads,0);self.assertFalse(self.box.appended)
    def test_observation_has_no_mail_write(self):
        self.c['mode']='observe';key=self.engine.process(mail())
        self.assertEqual(self.status(key),'observed');self.assertFalse(self.box.appended)
    def test_observation_can_be_promoted_without_duplication(self):
        self.c['mode']='observe';m=mail();key=self.engine.process(m)
        self.c['mode']='drafts';self.engine.process(m);self.engine.process(m)
        self.assertEqual(self.status(key),'drafted');self.assertEqual(len(self.box.appended),1)
    def test_read_during_generation_prevents_append(self):
        self.box.stop_at=2;key=self.engine.process(mail())
        self.assertEqual(self.status(key),'ignored');self.assertFalse(self.box.appended)
    def test_uncertain_append_is_not_retried(self):
        self.box.fail_append=True;m=mail();key=self.engine.process(m)
        self.assertEqual(self.status(key),'append_uncertain')
        self.box.fail_append=False;self.engine.process(m)
        self.assertEqual(self.status(key),'append_uncertain');self.assertFalse(self.box.appended)
    def test_crash_after_actual_append_is_reconciled(self):
        self.box.fail_append=True;self.box.stored_on_failure=True;m=mail();key=self.engine.process(m)
        self.assertEqual(self.status(key),'append_uncertain');self.engine.process(m)
        self.assertEqual(self.status(key),'drafted');self.assertEqual(len(self.box.appended),1)
    def test_unknown_sender_never_reads_client_files(self):
        key=self.engine.process(mail(sender='inconnu@example.test'))
        self.assertEqual(self.status(key),'drafted');self.assertEqual(self.dav.downloads,0)
        report=json.loads((self.state_dir/'reports'/(key+'.json')).read_text())
        self.assertTrue(report['role_confirmation_required'])
        self.assertFalse(any(s['kind']=='document' for s in report['sources']))
    def test_opponent_does_not_get_internal_documents(self):
        self.engine.matters[0]['correspondents'][0]['role']='confrere_adverse'
        key=self.engine.process(mail());self.assertEqual(self.status(key),'review');self.assertEqual(self.dav.downloads,0)
    def test_legal_request_goes_to_lawyer(self):
        self.model.intent='legal';key=self.engine.process(mail())
        self.assertEqual(self.status(key),'review');self.assertEqual(self.dav.downloads,0)
    def test_fabricated_source_prevents_draft(self):
        self.model.fake_source=True;key=self.engine.process(mail())
        self.assertEqual(self.status(key),'review');self.assertFalse(self.box.appended)
    def test_failed_quality_review_prevents_draft(self):
        self.model.verified=False;key=self.engine.process(mail())
        self.assertEqual(self.status(key),'review');self.assertFalse(self.box.appended)
    def test_file_updated_during_composition_prevents_draft(self):
        self.model.after_compose=lambda:setattr(self.dav,'changed',True)
        key=self.engine.process(mail());self.assertEqual(self.status(key),'review');self.assertFalse(self.box.appended)
    def test_unsupported_attachment_blocks_draft(self):
        m=mail();m.msg.add_attachment(b'not-executable',maintype='application',subtype='octet-stream',filename='piece.doc')
        key=self.engine.process(m);self.assertEqual(self.status(key),'review');self.assertFalse(self.box.appended)
    def test_model_does_not_receive_unrelated_calendar_titles(self):
        self.model.intent='appointment'
        self.dav.agenda=[{'uid':'private','start':'2020-01-01T09:00:00+00:00','end':'2020-01-01T10:00:00+00:00',
                          'summary':'SECRET AUTRE CLIENT','description':'TEXTE CONFIDENTIEL','busy':True}]
        key=self.engine.process(mail(subject='Rendez-vous'))
        self.assertEqual(self.status(key),'drafted')
        self.assertNotIn('SECRET AUTRE CLIENT',json.dumps(self.model.calls))
        self.assertGreaterEqual(self.dav.calendar_calls,2)
    def test_completed_unread_messages_do_not_starve_queue(self):
        self.c['max_messages_per_run']=1
        self.box.inputs={'1':mail('1'),'2':mail('2')}
        self.engine.run();self.engine.run()
        self.assertEqual(len(self.box.appended),2)
    def test_new_incoming_in_old_thread_not_blocked_forever(self):
        a=State(self.state_dir)
        a.set('old','old-mid','same-thread','drafted')
        self.assertIsNone(a.duplicate('new','new-mid','same-thread'))
    def test_retry_cannot_reset_written_or_uncertain_append(self):
        a=self.engine.state
        for status in ['drafted','appending','append_uncertain']:
            a.set(status,status,status,status)
            self.assertEqual(a.reset_review(status),0)


class RulesTests(unittest.TestCase):
    def test_diagnostic_does_not_emit_secrets(self):
        import diagnostic
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'config').mkdir()
            (root/'config/config.inc.php').write_text("<?php\n$config['imap_host']='ssl://user:SECRET123@mail.example.test';\n$config['db_dsnw']='mysql://user:OTHERSECRET@localhost/db';")
            with patch.object(diagnostic,'ROOT',root):
                safe=diagnostic.sanitized_settings(diagnostic.literal_settings())
            self.assertNotIn('SECRET',json.dumps(safe));self.assertIn('mail.example.test',json.dumps(safe))
    def test_exclusions(self):
        cfg={'own_addresses':['cabinet@example.test']}
        for subject,reason in [('RPVA — Notification automatique','notification_rpva'),
                              ('Remplacement garde à vue demain','remplacement_garde_a_vue')]:
            self.assertEqual(exclusion(mail(subject=subject),cfg),reason)
        self.assertEqual(exclusion(mail(subject='Question sur mon message RPVA'),cfg),'')
        self.assertEqual(exclusion(mail(subject='Question concernant une garde à vue'),cfg),'')
    def test_reply_to_mismatch(self):
        m=mail();m.msg['Reply-To']='other@example.test'
        self.assertEqual(recipient_issue(m,{'own_addresses':['cabinet@example.test']}),'reply_to_different_a_verifier')
    def test_multiple_recipients(self):
        m=mail();m.msg['Cc']='tiers@example.test'
        self.assertEqual(recipient_issue(m,{'own_addresses':['cabinet@example.test']}),'destinataires_multiples_a_verifier')
    def test_resolve_multiple_matters_requires_reference(self):
        mm=[{'id':'DOS-001','client_name':'Alpha','references':['AAA-2026'],'correspondents':[{'email':'client@example.test','role':'client'}]},
            {'id':'DOS-002','client_name':'Beta','references':['BBB-2026'],'correspondents':[{'email':'client@example.test','role':'client'}]}]
        m=mail(subject='Bonjour');m.msg.set_content('Une question sans référence.')
        self.assertIsNone(resolve_matter(m,mm)[0])
        m.msg.replace_header('Subject','BBB-2026');self.assertEqual(resolve_matter(m,mm)[0]['id'],'DOS-002')
    def test_conflicting_reference_is_not_ignored(self):
        mm=[{'id':'DOS-001','client_name':'Alpha','correspondents':[{'email':'client@example.test','role':'client'}]},
            {'id':'DOS-002','client_name':'Beta','correspondents':[]}]
        m=mail(subject='DOS-002');m.msg.set_content('Question sur DOS-002')
        self.assertIsNone(resolve_matter(m,mm)[0])
    def test_path_traversal(self):
        for path in ['/Dossiers/../Secrets','/Dossiers/%2e%2e/Secrets','/Dossiers\\Secrets']:
            with self.assertRaises(Stop):clean_path(path)
    def test_xml_entities_and_utf16_refused(self):
        for x in [b'<!DOCTYPE x [<!ENTITY a "secret">]><x>&a;</x>', '<x/>'.encode('utf-16')]:
            with self.assertRaises(Stop):xml_bytes(x)
    def test_schema_booleans_not_strings(self):
        with self.assertRaises(Stop):validate('false',{'type':'boolean'})
    def test_utf7_mailbox_roundtrip(self):
        for s in ['Brouillons','Envoyés','A & B','Dossiers/中文']:
            self.assertEqual(decode_utf7(modified_utf7(s)),s)
    def test_http_rejects_nonlocal_model_and_cleartext_remote(self):
        with self.assertRaises(Stop):HTTP('https://api.example.test',local_only=True)
        with self.assertRaises(Stop):HTTP('http://cloud.example.test')
    def test_draft_headers_and_signature(self):
        d=make_draft(mail(),{'from_name':'Maître Exemple','from_address':'cabinet@example.test','signature':'SIGNATURE'},'Bonjour','123')
        parsed=BytesParser(policy=policy.default).parsebytes(d.as_bytes(policy=policy.SMTP))
        self.assertIn('SIGNATURE',parsed.get_content());self.assertEqual(parsed['To'],'client@example.test')


class CalendarTests(unittest.TestCase):
    def test_expanded_event_and_all_day(self):
        ics='BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20260908T080000Z\r\nDTEND:20260908T090000Z\r\nSUMMARY:Rendez-vous\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:y\r\nDTSTART;VALUE=DATE:20260909\r\nDTEND;VALUE=DATE:20260910\r\nEND:VEVENT\r\nEND:VCALENDAR'
        events=parse_events(ics,'Europe/Paris');self.assertEqual(len(events),2)
        self.assertEqual(events[0]['start'],'2026-09-08T08:00:00+00:00')
        now=datetime(2026,9,7,8,tzinfo=timezone.utc)
        slots=available_slots(events,now,{'notice_hours':24})
        self.assertTrue(slots);self.assertFalse(any('2026-09-09' in s['start'] for s in slots))
    def test_nonexpanded_recurrence_fails_closed(self):
        ics='BEGIN:VEVENT\nDTSTART:20260908T080000Z\nDTEND:20260908T090000Z\nRRULE:FREQ=DAILY\nEND:VEVENT'
        with self.assertRaises(Stop):parse_events(ics,'Europe/Paris')
    def test_busy_calendar_yields_no_slots(self):
        e=[{'start':'2026-09-01T00:00:00+00:00','end':'2026-10-01T00:00:00+00:00','busy':True}]
        self.assertEqual(available_slots(e,datetime(2026,9,7,tzinfo=timezone.utc),{}),[])
    def test_alarm_does_not_override_event_fields(self):
        ics='BEGIN:VEVENT\nUID:x\nDTSTART:20260908T080000Z\nDTEND:20260908T090000Z\nSUMMARY:Rdv\nBEGIN:VALARM\nDESCRIPTION:Secret alarm\nEND:VALARM\nEND:VEVENT'
        self.assertEqual(parse_events(ics,'Europe/Paris')[0]['description'],'')


class ExtractionTests(unittest.TestCase):
    def test_real_pdf_extraction(self):
        p=Path(__file__).parent/'fixtures/document-demo.pdf'
        self.assertIn('Client DEMO',extract(p.read_bytes(),p.name,{}))
    def test_real_scan_ocr(self):
        p=Path(__file__).parent/'fixtures/scan-demo.png'
        self.assertIn('DEMO',extract(p.read_bytes(),p.name,{}))
    def test_docx_without_executing_macros_or_external_links(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="x"><w:p><w:r><w:t>Client DEMO</w:t></w:r></w:p></w:document>')
            z.writestr('word/vbaProject.bin',b'not executed')
        self.assertIn('Client DEMO',extract(b.getvalue(),'client.docx',{}))
    def test_unsupported_format_does_not_run_anything(self):
        with patch('agent.documents.command') as cmd:
            with self.assertRaises(Stop):extract(b'test','piece.exe',{})
            cmd.assert_not_called()
    def test_oversized_text_not_silently_truncated(self):
        with self.assertRaises(Stop):extract(b'A'*100,'piece.txt',{'max_document_chars':20})


if __name__=='__main__':unittest.main()

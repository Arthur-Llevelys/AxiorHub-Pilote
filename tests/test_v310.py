import json
from pathlib import Path
import unittest
from xml.etree import ElementTree as ET

import test_agent as fixtures

from agent.api import dispatch,openapi
from agent.common import Stop,digest
from agent.dav import DAV
from agent.desk import Desk
from agent.mailbox import Mailbox,exclusion
from agent.web import App
from agent.workstation import (daily_briefing,refresh_unpaid_invoices,
  save_workspace_mapping,unpaid_summary,why_nothing,workspace_mapping)
import upgrade


class FakeInvoiceHTTP:
    def __init__(self):self.base='https://invoice.test';self.calls=[]
    def request(self,method,url,data=None,headers=None,limit=None):
        self.calls.append((method,url,data,headers))
        return json.dumps({'data':[{'id':'inv1','client_id':'client1','number':'2026-001',
          'status_id':'2','amount':1200,'balance':800,'due_date':'2026-09-01',
          'invitations':[{'link':'https://invoice.test/i/abc'}]}]}).encode()


class Version310Tests(unittest.TestCase):
    def setUp(self):
        f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write');f.setUp()
        self.f=f;self.addCleanup(f.doCleanups)
        f.c['mail'].update(process_seen_recent=True,retroactive_lookback_days=7,
                           retroactive_max_candidates_per_run=3)
        f.c['workstation']={'roundcube_url':'https://mail.test/','roundcube_drafts_url':'https://mail.test/drafts',
          'openwebui_url':'https://ai.test/','nextcloud_url':'https://cloud.test/files',
          'onlyoffice_url':'https://office.test/','invoice_ninja_url':'https://invoice.test/'}
        f.c['invoice_ninja']={'enabled':True,'base_url':'https://invoice.test',
          'api_token_file':'/does/not/matter','max_pages':2,'read_only':True}
        self.d=Desk(f.c)

    def test_seen_message_is_eligible_but_uid_key_stays_stable(self):
        mail=fixtures.mail(flags={'\\Seen'})
        self.assertEqual(exclusion(mail,self.f.c['mail']),'deja_lu')
        self.assertEqual(exclusion(mail,self.f.c['mail'],allow_seen=True),'')
        a=mail.key('account@example.test');b=mail.key('account@example.test')
        self.assertEqual(a,b);self.assertEqual(len(a),64)

    def test_candidates_merge_seen_and_unseen_and_are_bounded(self):
        box=Mailbox.__new__(Mailbox);box.cfg=self.f.c['mail']
        box.unseen=lambda:['9','7']
        box.search=lambda folder,*criteria:['5','7','8','9','10']
        self.assertEqual(box.candidates(),['10','9','8'])

    def test_old_seen_skip_is_recoverable_once(self):
        mail=fixtures.mail(flags={'\\Seen'});account=self.f.c['mail']['username']+'@'+self.f.c['mail']['host'];key=mail.key(account)
        self.f.engine.state.set(key,'mid','thread','ignored','deja_lu')
        self.f.engine.process(mail)
        self.assertNotEqual(self.f.engine.state.get(key)[1],'deja_lu')

    def test_why_panel_and_cockpit_are_human_readable(self):
        self.f.engine.state.set('a'*64,'m','t','review','correspondant_ou_dossier_a_confirmer')
        why=why_nothing(self.d)
        self.assertTrue(why['mail_collection']['seen_messages_included'])
        self.assertTrue(any('dossier' in x['label'].lower() for x in why['reasons']))
        auth={'prefix':'/agent-courriel','csrf':'token'}
        page=App(self.f.c,auth).page(self.f.c,auth,'/accueil',{})
        for text in ('Pourquoi rien n’a été produit ?','Ouvrir directement Brouillons','Briefing quotidien',
                     'Roundcube','Open WebUI','Nextcloud / OnlyOffice','Invoice Ninja'):
            self.assertIn(text,page)

    def test_theme_and_two_dictation_modes(self):
        css=Path('agent/static/v300.css').read_text();js=Path('agent/static/v310.js').read_text()
        self.assertIn('aside nav details{',css);self.assertIn('color:#fff!important',css)
        self.assertIn('Rapide — texte non confidentiel',js)
        self.assertIn('Confidentiel — Vocal local',js)
        self.assertIn('localStorage',js);self.assertIn('window.confirm',js)
        self.assertNotIn('.start();\n',js.split("button.addEventListener",1)[0])

    def test_openwebui_mapping_is_same_origin_only(self):
        save_workspace_mapping(self.d,'DOS-001','folder-1','MARTINEAU','https://ai.test/folder/folder-1')
        self.assertEqual(workspace_mapping(self.d,'DOS-001')['folder_id'],'folder-1')
        with self.assertRaisesRegex(Stop,'hors_instance'):
            save_workspace_mapping(self.d,'DOS-001','folder-2','Faux','https://evil.test/folder/2')

    def test_invoice_ninja_is_read_only_get(self):
        client=FakeInvoiceHTTP();result=refresh_unpaid_invoices(self.d,http=client)
        self.assertEqual(result['unpaid_invoices'],1)
        self.assertTrue(all(call[0]=='GET' and call[2] is None for call in client.calls))
        summary=unpaid_summary(self.d);self.assertEqual(summary['balance'],800)
        self.assertFalse(result['invoice_created']);self.assertFalse(result['payment_created'])

    def test_nextcloud_file_id_builds_onlyoffice_entry_route(self):
        dav=DAV.__new__(DAV);dav.cfg={'roots':['/Dossiers']}
        class H:base='https://cloud.test'
        dav.http=H();dav.files='https://cloud.test/remote.php/dav/files/tech'
        root=ET.fromstring('<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:response><d:propstat><d:prop><oc:fileid>12345</oc:fileid></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>')
        dav.request_xml=lambda *args,**kwargs:root
        self.assertEqual(dav.file_web_url('/Dossiers/projet.docx'),'https://cloud.test/index.php/f/12345')

    def test_version_api_briefing_and_upgrade_defaults(self):
        self.assertEqual(openapi('https://cabinet.test')['info']['version'],'5.6.5')
        caps=dispatch(self.d,'/capabilities','GET');self.assertEqual(caps['version'],'5.6.5')
        self.assertIn('read_seen_and_unseen_new_uids',caps['can'])
        self.assertIn('events_next_7_days',daily_briefing(self.d))
        cfg=json.loads(upgrade.updated_config(json.dumps({'mail':{},'ollama':{},'nextcloud':{}}).encode()))
        self.assertTrue(cfg['mail']['process_seen_recent']);self.assertTrue(cfg['invoice_ninja']['read_only'])

    def test_no_send_transport_and_no_overwrite_contract(self):
        mailbox=Path('agent/mailbox.py').read_text();documents=Path('agent/document_projects.py').read_text()
        self.assertNotIn('smtplib',mailbox);self.assertNotIn('sendmail(',mailbox)
        self.assertIn("{'If-None-Match':'*'",Path('agent/dav.py').read_text())
        self.assertIn("raise Stop('nom_fichier_deja_existant')",documents)
        self.assertIn("'email_sent':False",documents)


if __name__=='__main__':unittest.main()

import json
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_desk
from agent.common import Stop
from agent.desk import Desk
from agent.intelligence import deposit_draft
from agent.operations import daily_digest
from agent.workspace import discovery_roots


class Fixes152Tests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_manual_draft_lookup_uses_real_mail_key_column(self):
        desk=Desk(self.f.c);key='a'*64
        data={'result':{'requires_decision':True,'body':'Projet'}}
        desk.db.execute('INSERT INTO manual_drafts VALUES (?,?,?)',(key,json.dumps(data),desk.now()))
        desk.db.commit()
        with self.assertRaisesRegex(Stop,'depot_bloque_decision_requise'):
            deposit_draft(desk,{'key':key,'confirm':'yes'})

    def test_reply_job_is_prioritized_over_old_index_backlog(self):
        desk=Desk(self.f.c);old=desk.enqueue('index',{'matter':'DOS-001'})
        reply=desk.enqueue('prepare_reply',{'key':'a'*64})
        with patch.object(desk,'perform',return_value={}):self.assertTrue(desk.work_once())
        self.assertEqual(desk.db.execute('SELECT status FROM jobs WHERE id=?',(reply,)).fetchone()[0],'done')
        self.assertEqual(desk.db.execute('SELECT status FROM jobs WHERE id=?',(old,)).fetchone()[0],'pending')

    def test_digest_never_creates_an_imap_draft(self):
        self.assertFalse(daily_digest(Desk(self.f.c))['brouillon'])

    def test_cabinet_root_discovers_only_client_matters(self):
        self.f.c['nextcloud']['roots']=['/CABINET EXEMPLE']
        self.assertEqual(discovery_roots(self.f.c),['/CABINET EXEMPLE/01 - Dossiers'])


class Web152Tests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_pages_do_not_refresh_while_user_is_editing(self):
        Desk(self.f.c).enqueue('run')
        result=self.request('/')
        self.assertNotIn('http-equiv="refresh"',result['body'])
        self.assertIn('enctype="application/x-www-form-urlencoded"',result['body'])

    def test_matter_page_groups_requested_business_information(self):
        html=self.request('/matter',query='id=DOS-001')['body']
        for label in ('Résumé du dossier','Chronologie unifiée','Tâches et diligences',
                      'Brouillons de courriels','Courriels du dossier','Fichiers Nextcloud'):
            self.assertIn(label,html)

    def test_browser_headers_are_local_and_do_not_allow_remote_fonts(self):
        result=self.request('/')
        headers=dict(result['headers'])
        self.assertEqual(headers['Permissions-Policy'],'camera=(), microphone=(self), geolocation=()')
        self.assertIn("font-src 'self' data:",headers['Content-Security-Policy'])
        self.assertNotIn('perplexity.ai',headers['Content-Security-Policy'])

    def test_matter_page_renders_indexed_mail_document_task_draft_and_brief(self):
        from agent.index import DocumentIndex
        desk=Desk(self.f.c);index=DocumentIndex(self.f.c['state_dir'])
        index.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)',('DOS-001','/Dossiers/DEMO/contrat.pdf','e','2026-09-09T08:00:00+00:00','Texte du contrat',''))
        index.put_source('DOS-001','Envoyés/test','Corps du courriel','mail-etag','2026-09-09T09:00:00+00:00','email_sent',
                         {'subject':'Réponse au client','sender':'cabinet@example.test','message_id':'<sent@test>'})
        brief={'summary':'Synthèse sourcée','jurisdiction':'TJ Lyon','case_number':'RG 1',
               'latest_instructions':['Attendre la pièce'],'open_questions':['Montant ?'],
               'negotiation_positions':[],'deadlines':[],'limits':[],
               'chronology':[],'actions_completed':['Courriel envoyé'],'actions_planned':[]}
        desk.db.execute('INSERT INTO case_briefs VALUES(NULL,?,?,?,?,?)',('DOS-001',1,json.dumps(brief),'[]',desk.now()))
        desk.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',('t','DOS-001','Relancer le client','2026-09-10T08:00:00+00:00','open',desk.now()))
        desk.db.execute('INSERT INTO manual_drafts VALUES (?,?,?)',('b'*64,json.dumps({'matter':'DOS-001','subject':'Projet client','result':{'body':'Texte du projet','requires_decision':True}}),desk.now()))
        desk.db.commit()
        html=self.request('/matter',query='id=DOS-001')['body']
        for value in ('Synthèse sourcée','contrat.pdf','Réponse au client','Relancer le client','Texte du projet'):
            self.assertIn(value,html)


class Roundcube152Tests(unittest.TestCase):
    def test_plugin_registers_client_command_and_robot_icon(self):
        root=Path(__file__).parents[1]/'integrations/roundcube/axiorhub_mail_agent'
        js=(root/'axiorhub_mail_agent.js').read_text()
        css=(root/'axiorhub_mail_agent.css').read_text()
        php=(root/'axiorhub_mail_agent.php').read_text()
        self.assertIn("register_command('plugin.axiorhub_mail_agent_open'",js)
        self.assertIn('🤖',css)
        self.assertIn("include_script('axiorhub_mail_agent.js')",php)

    def test_apache_update_recognizes_exact_151_configuration(self):
        path=Path(__file__).parents[1]/'install-interface.py'
        spec=importlib.util.spec_from_file_location('interface_installer_152',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        old=module.apache_config_151('courriel.example.com')
        new=module.apache_config('courriel.example.com')
        self.assertNotIn('Permissions-Policy',old)
        self.assertIn('Header always unset Permissions-Policy',new)


if __name__=='__main__':unittest.main()

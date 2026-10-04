"""Regressions for the Roundcube frame and oversized assistant context."""
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.common import Stop
from agent.context import payload_size
from agent.intelligence import fit_manual_draft_context,prepare_draft
from agent.desk import Desk
from agent.web import App
from agent.workspace import fit_chat_context
import test_agent as fixtures


class ContextAndPresentation362(unittest.TestCase):
    def test_chat_preserves_question_attachment_and_email(self):
        payload={'question_avocat':'Mon argument juridique ?','sources':[
            {'id':'attachment','kind':'piece_jointe_locale','excerpt':'A'*500},
            {'id':'incoming','kind':'courriel','excerpt':'B'*500},
            {'id':'indexed','kind':'document','excerpt':'C'*5000}],
            'historique_non_probant':[{'question':'ancien','reponse':'D'*2000}],
            'couverture_documentaire':{},'limites':'Sélection non exhaustive.'}
        fit_chat_context(payload,1700)
        self.assertEqual([item['id'] for item in payload['sources']],['attachment','incoming'])
        self.assertEqual(payload['historique_non_probant'],[])
        self.assertIn('omis',payload['limites'])
        self.assertLessEqual(payload_size(payload),1700)

    def test_chat_refuses_to_discard_essential_document_or_question(self):
        payload={'question_avocat':'A'*6000,'sources':[{'id':'piece','kind':'piece_jointe_locale','excerpt':'B'*6000}],
            'historique_non_probant':[],'limites':''}
        with self.assertRaisesRegex(Stop,'sources_essentielles_depassent_contexte'):
            fit_chat_context(payload,8000)
        self.assertEqual(len(payload['sources']),1)

    def test_manual_reply_keeps_original_email_and_requires_source_review(self):
        payload={'incoming':{'text':'A'*2000},'sources':[
            {'id':'incoming','kind':'email_received'},
            {'id':'optional','kind':'document','excerpt':'B'*4000}],
            'available_slots':[],'coverage':{}}
        self.assertEqual(fit_manual_draft_context(payload,14000),1)
        self.assertEqual(payload['incoming']['text'],'A'*2000)
        self.assertTrue(payload['coverage']['not_exhaustive'])
        self.assertEqual([item['id'] for item in payload['sources']],['incoming'])
        with self.assertRaisesRegex(Stop,'courriel_entrant_depasse_contexte'):
            fit_manual_draft_context({'incoming':{'text':'C'*12000},
                'sources':[{'id':'incoming','kind':'email_received'}],
                'available_slots':[],'coverage':{}},13000)

    def test_manual_reply_never_auto_deposits_without_all_sources_or_control(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups)
        f.c['ollama']['max_context_chars']=15000
        desk=Desk(f.c);mail=fixtures.mail();calls=[];response={'body':'Bonjour.',
            'source_ids':['incoming'],'limits':[],'requires_decision':False}
        class LocalModel:
            def __init__(self,*args):pass
            def ask(self,stage,payload):
                calls.append(stage)
                return response.copy()
        class Index:
            def sources(self,*args):
                return ([{'id':'doc-1','kind':'document','excerpt':'B'*8000}],{})
        report={'matter':'DOS-001','reply_recipients':[mail.sender]}
        with patch('agent.intelligence.Mailbox') as mailbox, \
             patch('agent.intelligence.fetch_source',return_value=(report,mail)), \
             patch('agent.intelligence.source_index',return_value=Index()), \
             patch('agent.intelligence.DAV'), \
             patch('agent.intelligence.Model',LocalModel):
            result=prepare_draft(desk,{'key':'a'*64})
            self.assertTrue(result['projet_prepare'])
            self.assertFalse(result['depot_autorise'])
            self.assertEqual(calls,['manual_draft'])
            mailbox.return_value.close.assert_called_once()

        class EmptyIndex:
            def sources(self,*args):return ([],{})
        calls.clear();response['body']='\\'*7000;response['limits']=[]
        f.c['ollama']['max_context_chars']=13000
        with patch('agent.intelligence.Mailbox'), \
             patch('agent.intelligence.fetch_source',return_value=(report,mail)), \
             patch('agent.intelligence.source_index',return_value=EmptyIndex()), \
             patch('agent.intelligence.DAV'), \
             patch('agent.intelligence.Model',LocalModel):
            result=prepare_draft(desk,{'key':'b'*64})
            self.assertFalse(result['depot_autorise'])
            self.assertEqual(calls,['manual_draft'])

    def test_mail_view_links_site_icon_and_styles_full_width_frame(self):
        f=fixtures.EngineTests('test_observation_has_no_mail_write');f.setUp()
        self.addCleanup(f.doCleanups)
        auth={'prefix':'/agent-courriel','csrf':'test','origin':'https://courriel.example.com'}
        page=App(f.c,auth).page(f.c,auth,'/',{'view':'mail'})
        self.assertIn('class="ws-roundcube-frame"',page)
        self.assertNotIn('<iframe class="ws-roundcube-frame" style=',page)
        self.assertIn('rel="icon"',page)
        self.assertIn('/static/axiorhub-icon.png',page)
        css=(Path(__file__).parents[1]/'agent/static/v360.css').read_text()
        self.assertIn('.ws-roundcube-frame{display:block;width:100%;height:80vh',css)
        self.assertTrue((Path(__file__).parents[1]/'agent/static/axiorhub-icon.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))


if __name__=='__main__':unittest.main()

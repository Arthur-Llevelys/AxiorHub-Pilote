import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_agent as fixtures
import test_desk
from agent.intelligence import prepare_reply
from agent.state import State
from agent.web import inbox_bucket, simple_question


def roundcube_installer():
    path=Path(__file__).parents[1]/'install-roundcube-button.py'
    spec=importlib.util.spec_from_file_location('roundcube_button_installer',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class SimpleInboxTests(unittest.TestCase):
    setUp=test_desk.WebTests.setUp
    request=test_desk.WebTests.request

    def test_buckets_and_plain_question(self):
        self.assertEqual(inbox_bucket('drafted','x'),'drafts')
        self.assertEqual(inbox_bucket('ignored','x'),'ignored')
        self.assertEqual(inbox_bucket('review','correspondant_ou_dossier_a_confirmer'),'confirm')
        question=simple_question({'reason':'correspondant_ou_dossier_a_confirmer','sender':'client@example.test'})
        self.assertIn('À quel dossier',question);self.assertIn('client@example.test',question)

    def test_default_page_is_one_simple_inbox_and_hides_ignored_messages(self):
        state=State(self.f.c['state_dir'])
        visible='1'*64;hidden='2'*64
        base={'sender':'client@example.test','received_at':'2026-09-09T08:00:00+00:00',
              'matter':'DOS-001','recipient_role':'client','matter_scores':[],
              'account_key':None,'source_mailbox':'INBOX','source_uid':'1'}
        state.report(visible,{**base,'key':visible,'subject':'Question client',
                     'status':'review','reason':'intervention_avocat_ordali'})
        state.set(visible,'m1','t1','review','intervention_avocat_ordali')
        state.report(hidden,{**base,'key':hidden,'subject':'Publicité cachée',
                     'status':'ignored','reason':'tri_ia_advertisement'})
        state.set(hidden,'m2','t2','ignored','tri_ia_advertisement')
        html=self.request('/')['body']
        self.assertIn('À traiter',html);self.assertIn('Brouillons prêts',html)
        self.assertIn('À confirmer',html);self.assertIn('Question client',html)
        self.assertNotIn('Publicité cachée',html)
        self.assertIn('Préparer une réponse',html);self.assertIn('Ignorer',html)
        self.assertIn('Choisir le dossier',html)
        self.assertNotIn('Mémoire et apprentissage',html)

    def test_administration_holds_technical_sections(self):
        html=self.request('/administration')['body']
        self.assertIn('Automatismes',html)
        self.assertIn('Mémoire et apprentissage',html)
        self.assertIn('Qualité et statistiques',html)

    def test_new_styles_use_bright_blue_and_green(self):
        css=self.request('/static/v151.css')['body']
        self.assertIn('#2474d2',css);self.assertIn('#39a966',css)
        self.assertIn('.task-toast',css)

    def test_active_job_is_announced_at_top_left(self):
        from agent.desk import Desk
        Desk(self.f.c).enqueue('run')
        html=self.request('/')['body']
        self.assertIn('task-toast',html)
        self.assertIn('En attente : Analyse des nouveaux courriels',html)
        self.assertNotIn('http-equiv="refresh"',html)
        self.assertIn('Actualiser',html)


class OneClickReplyTests(unittest.TestCase):
    def test_safe_reply_is_prepared_then_deposited(self):
        prepared={'projet_prepare':True,'depot_autorise':True}
        with patch('agent.intelligence.prepare_draft',return_value=prepared),\
             patch('agent.intelligence.deposit_draft',return_value={'brouillon_imap':'cree'}) as deposit:
            from types import SimpleNamespace
            result=prepare_reply(SimpleNamespace(),{'key':'a'*64})
        self.assertEqual(result['brouillon_imap'],'cree');deposit.assert_called_once()

    def test_legal_decision_keeps_prudent_text_without_deposit(self):
        prepared={'projet_prepare':True,'depot_autorise':False}
        with patch('agent.intelligence.prepare_draft',return_value=prepared),\
             patch('agent.intelligence.deposit_draft') as deposit:
            from types import SimpleNamespace
            result=prepare_reply(SimpleNamespace(),{'key':'a'*64})
        self.assertIn('décision',result['message']);deposit.assert_not_called()


class RoundcubeButtonTests(unittest.TestCase):
    def test_activation_before_php_closing_tag_is_idempotent(self):
        module=roundcube_installer();original=b"<?php\n$config['plugins'] = ['archive'];\n?>\n"
        once=module.activation(original);twice=module.activation(once)
        self.assertEqual(once,twice)
        self.assertEqual(once.count(b'axiorhub_mail_agent'),2)
        self.assertLess(once.index(module.MARKER.encode()),once.index(b'?>'))

    def test_installer_preserves_existing_plugins_and_refuses_overwrite(self):
        module=roundcube_installer()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'roundcube';(root/'config').mkdir(parents=True);(root/'plugins').mkdir()
            config=root/'config/config.inc.php';config.write_text("<?php\n$config['plugins']=['archive'];\n")
            source=Path(__file__).parents[1]/'integrations/roundcube/axiorhub_mail_agent'
            result=module.install(root,source,Path(td)/'backups',lint=False)
            self.assertEqual(result['activation'],'ajoutee')
            self.assertIn("'archive'",config.read_text())
            self.assertTrue((root/'plugins/axiorhub_mail_agent/axiorhub_mail_agent.php').is_file())
            self.assertEqual(module.install(root,source,Path(td)/'backups',lint=False)['activation'],'deja_presente')
            (root/'plugins/axiorhub_mail_agent/axiorhub_mail_agent.php').write_text('different')
            with self.assertRaises(RuntimeError):module.install(root,source,Path(td)/'backups',lint=False)


if __name__=='__main__':
    unittest.main()

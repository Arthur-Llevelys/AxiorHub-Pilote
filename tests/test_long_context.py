import copy
import json
import unittest

from agent.common import Stop
from agent.context import canonical, compact_history, fit_documents, payload_size
import test_agent as fixtures
from test_agent import mail


class ContextTests(unittest.TestCase):
    def message(self, text, mid='old'):
        return {'id': mid, 'sender': 'client@example.test',
                'received_at': '2026-09-08T10:00:00+00:00', 'text': text}

    def test_full_quote_with_line_wrapping_preserves_incoming(self):
        old = 'Bonjour Maître, merci de vérifier le contrat et son annexe avant notre rendez-vous. ' * 8
        incoming = self.message('Voici ma demande actuelle.\n\n' + '\n'.join('> '+line for line in old.splitlines()), 'new')
        original = copy.deepcopy(incoming)
        kept, meta = compact_history(incoming, [self.message(old)])
        self.assertEqual(kept, [])
        self.assertEqual(meta['history_already_quoted'][0]['id'], 'old')
        self.assertEqual(incoming, original)

    def test_changed_negation_or_amount_is_retained(self):
        old = ('Le paiement de 10000 euros est confirmé. ' * 10)
        for new in (old.replace('10000', '1000'), old.replace('est confirmé', "n’est pas confirmé")):
            kept, meta = compact_history(self.message(new), [self.message(old)])
            self.assertEqual(kept[0]['text'], old)
            self.assertEqual(meta['history_already_quoted'], [])

    def test_numeric_comparison_is_not_assumed_to_be_a_quote(self):
        old = '100 euros : montant à vérifier dans le dossier avant toute réponse. ' * 4
        kept, _ = compact_history(self.message('> '+old), [self.message(old)])
        self.assertTrue(canonical('> '+old).startswith('>'))
        self.assertEqual(kept[0]['text'], old)

    def test_partial_repeat_keeps_unique_request(self):
        repeated = 'Le contrat reste en cours de relecture, sans dépôt confirmé à cette date. ' * 10
        unique = 'Merci de ne pas proposer de rendez-vous avant le 18 septembre.'
        kept, _ = compact_history(self.message(repeated), [self.message(unique+'\n\n'+repeated)])
        self.assertIn(unique, kept[0]['text'])
        self.assertIn('déjà cité', kept[0]['text'])
        self.assertIn('repeated_passages_in', kept[0])

    def test_new_history_cannot_be_dropped_to_fit(self):
        payload = {'incoming': self.message('x'*52000), 'history': [self.message('y'*50000)],
                   'sources': [], 'coverage': {}}
        with self.assertRaisesRegex(Stop, 'contexte_unique_trop_long'):
            fit_documents(payload, 90000)

    def test_only_lowest_ranked_documents_are_removed(self):
        payload = {'incoming': self.message('x'*52436),
                   'sources': [{'id': 'incoming', 'kind': 'email_received', 'content_ref': 'incoming'}]
                   + [{'id': 'doc-'+str(i), 'kind': 'document', 'excerpt': str(i)*11000} for i in range(5)],
                   'coverage': {'total_files': 280, 'selected_files': 5}}
        before = copy.deepcopy(payload)
        fitted, removed = fit_documents(payload, 90000)
        self.assertEqual(payload, before)
        self.assertEqual(fitted['incoming'], before['incoming'])
        self.assertEqual(removed, ['doc-4', 'doc-3', 'doc-2'])
        self.assertLessEqual(payload_size(fitted)+9000, 90000)
        self.assertEqual(fitted['coverage']['selected_files'], 2)
        self.assertTrue(fitted['coverage']['not_exhaustive'])

    def test_one_document_and_attachments_are_never_silently_removed(self):
        payload = {'incoming': self.message('x'*60000),
                   'sources': [{'id': 'doc-1', 'kind': 'document', 'excerpt': 'd'*12000},
                               {'id': 'attachment-1', 'kind': 'attachment', 'text': 'p'*15000}], 'coverage': {}}
        with self.assertRaisesRegex(Stop, 'contexte_unique_trop_long'):
            fit_documents(payload, 90000)


class LongMailTests(unittest.TestCase):
    def test_52436_characters_with_22_quoted_messages(self):
        fixture = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.c['mode'] = 'observe'
        fixture.c['mail']['max_body_chars'] = 100000
        fixture.c['ollama']['max_context_chars'] = 90000
        old = []
        for i in range(22):
            item = mail(str(i+2))
            item.msg.set_content(('Ancien échange numéro '+str(i)+' : pièces et contrat à vérifier. ')*30)
            old.append(item)
        incoming = mail()
        raw = 'Bonjour Maître, où en est la relecture du contrat DOS-001 ?\n\n'
        raw += '\n\n'.join(item.text for item in old)
        raw += ' ' + 'Informations historiques complémentaires. ' * 2000
        incoming.msg.set_content(raw[:52436])
        self.assertEqual(len(incoming.text), 52436)
        fixture.box.thread = lambda m: old
        key = fixture.engine.process(incoming)
        self.assertEqual(fixture.status(key), 'observed')
        self.assertEqual(fixture.box.appended, [])
        calls = dict(fixture.model.calls)
        self.assertEqual(calls['triage']['incoming']['text'], incoming.text)
        self.assertEqual(calls['triage']['history'], [])
        self.assertEqual(calls['compose']['incoming']['text'], incoming.text)
        received = [s for s in calls['compose']['sources'] if s['id']=='incoming'][0]
        self.assertNotIn('content', received)
        self.assertEqual(received['content_ref'], 'incoming')
        self.assertLessEqual(payload_size(calls['verify']), 90000)


if __name__ == '__main__':
    unittest.main()

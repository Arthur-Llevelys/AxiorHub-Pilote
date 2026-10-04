import copy
import json
import unittest

from agent.common import Stop
from agent.context import fit_triage, fit_context, payload_size
import test_agent as fixtures


class HistoryBudgetTests(unittest.TestCase):
    def record(self, text, mid='new'):
        return {'id': mid, 'text': text, 'sender': 'client@example.test',
                'received_at': '2026-09-08T10:00:00+00:00'}

    def test_triage_with_36_distinct_large_messages(self):
        incoming = self.record('x'*52436)
        history = [self.record(str(i)+' unique '+ 'h'*52000, str(i)) for i in range(36)]
        original = copy.deepcopy(incoming)
        payload, omitted = fit_triage(incoming, history, 90000)
        self.assertEqual(incoming, original)
        self.assertEqual(payload['incoming'], original)
        self.assertEqual(payload['history'], [])
        self.assertEqual(len(omitted), 36)
        self.assertTrue(payload['history_coverage']['not_exhaustive'])
        self.assertLessEqual(payload_size(payload), 90000)

    def test_recent_whole_messages_preferred_and_omissions_disclosed(self):
        history = [self.record(str(i)*10000, str(i)) for i in range(6)]
        result, omitted = fit_triage(self.record('x'*52436), history, 90000)
        self.assertEqual([r['id'] for r in result['history']], ['3', '4', '5'])
        self.assertEqual(len(omitted), 3)
        self.assertEqual(result['history_coverage']['omitted_messages'], 3)

    def test_essential_incoming_is_never_truncated(self):
        with self.assertRaisesRegex(Stop, 'courriel_entrant_depasse_contexte'):
            fit_triage(self.record('x'*91000), [], 90000)

    def test_compose_preserves_best_document_and_real_attachment(self):
        sources = [{'id':'incoming', 'kind':'email_received', 'content_ref':'incoming'},
                   {'id':'attachment-1', 'kind':'attachment', 'text':'pièce complète'},
                   {'id':'slot-1', 'kind':'available_slot', 'start':'2026-09-10T10:00:00+02:00'}]
        sources += [{'id':'mail-'+str(i), 'kind':'email_history',
                     'content':self.record('h'*52000, str(i))} for i in range(36)]
        sources += [{'id':'doc-'+str(i), 'kind':'document', 'excerpt':str(i)*10000} for i in range(5)]
        original = {'incoming':self.record('x'*52436), 'sources':sources, 'coverage':{'total_files':280}}
        before = copy.deepcopy(original)
        fitted, omitted = fit_context(original, 90000)
        self.assertEqual(original, before)
        ids = {s['id'] for s in fitted['sources']}
        self.assertTrue({'incoming','attachment-1','slot-1','doc-0'} <= ids)
        self.assertEqual(len(omitted['history_source_ids']), 36)
        self.assertLessEqual(payload_size(fitted)+9000, 90000)
        self.assertEqual(fitted['incoming'], before['incoming'])
        self.assertTrue(fitted['history_coverage']['not_exhaustive'])

    def test_huge_attachment_still_blocks_instead_of_disappearing(self):
        original = {'incoming':self.record('x'*52436),
                    'sources':[{'id':'attachment-1','kind':'attachment','text':'p'*40000}], 'coverage':{}}
        with self.assertRaisesRegex(Stop, 'contexte_essentiel_trop_long'):
            fit_context(original, 90000)

    def test_engine_full_pipeline_with_large_non_repeated_history(self):
        fixture = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.c['mode']='observe'
        fixture.c['mail']['max_body_chars']=100000
        fixture.c['ollama']['max_context_chars']=90000
        incoming = fixtures.mail()
        incoming.msg.set_content('Bonjour Maître, quel est le statut du contrat DOS-001 ?\n'+'x'*52436)
        history = []
        for i in range(36):
            m = fixtures.mail(str(i+2))
            m.msg.set_content('Ancien échange distinct '+str(i)+'. '+'h'*52000)
            history.append(m)
        fixture.box.thread=lambda m: history
        key=fixture.engine.process(incoming)
        self.assertEqual(fixture.status(key),'observed')
        self.assertEqual(fixture.box.appended,[])
        report=json.loads((fixture.state_dir/'reports'/(key+'.json')).read_text())
        self.assertEqual(len(report['email_context']['history_catalog']), 36)
        self.assertEqual(len(report['email_context']['compose_history_omitted']), 36)
        calls=dict(fixture.model.calls)
        self.assertEqual(set(calls), {'triage','compose','verify'})
        for payload in calls.values():
            self.assertLessEqual(payload_size(payload), 90000)
            self.assertEqual(payload['incoming']['text'], incoming.text)
            self.assertTrue(payload['history_coverage']['not_exhaustive'])


if __name__ == '__main__':
    unittest.main()

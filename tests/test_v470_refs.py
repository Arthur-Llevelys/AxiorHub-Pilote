"""4.7.0 : extraction, connecteur de sources, vérificateur, jeu de 100 références."""
import json
import time
import unittest
from pathlib import Path

from agent import refs470, sources470, verify470
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures

DATA = Path(__file__).parent / 'data'
CORPUS = json.loads((DATA / 'refs470_corpus.json').read_text(encoding='utf-8'))
CASES = json.loads((DATA / 'refs470_jeu_100.json').read_text(encoding='utf-8'))


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.clock = [1_000_000.0]

    def connector(self, providers=None, enabled=True):
        return sources470.Sources(self.desk, providers if providers is not None else [sources470.LocalProvider(data=CORPUS)],
                                  enabled=enabled, clock=lambda: self.clock[0])


class Extraction(unittest.TestCase):
    def test_articles_codes_and_lists(self):
        refs = refs470.extract("Les articles 1103 et 1104 du Code civil, l'art. L. 441-10 du C. com. et l'article 700 du CPC.")
        got = [(r['number'], r['code']) for r in refs]
        self.assertEqual(got, [('1103', 'Code civil'), ('1104', 'Code civil'), ('L441-10', 'Code de commerce'),
                               ('700', 'Code de procédure civile')])

    def test_same_code_and_missing_code(self):
        refs = refs470.extract("L'article 1240 du Code civil et l'article 1241 du même code ; voir aussi l'article 12.")
        self.assertEqual([r['code'] for r in refs], ['Code civil', 'Code civil', ''])

    def test_decisions(self):
        refs = refs470.extract("Cass. com., 12 mars 2019, n° 17-12.345 ; CA Lyon, 3 mars 2020, RG n° 18/01234 ; CE, 5 mars 2019, n° 123456")
        self.assertEqual([(r['court'], r['date']) for r in refs],
                         [('cass', '2019-03-12'), ('ca', '2020-03-03'), ('ce', '2019-03-05')])
        self.assertEqual(refs[0]['pourvoi'], '17-12.345')
        self.assertEqual(refs[0]['chamber'], 'com')
        self.assertEqual(refs[1]['rg'], '18/01234')

    def test_bare_pourvoi_number(self):
        refs = refs470.extract('Arrêt rendu sur le pourvoi n° 21-12.345.')
        self.assertEqual(refs[0]['pourvoi'], '21-12.345')
        self.assertTrue(refs[0].get('court_presumed'))

    def test_invalid_date_is_not_invented(self):
        refs = refs470.extract('Cass. com., 31 février 2019, n° 17-12.345')
        self.assertEqual(refs[0]['date'], '')


class Privacy(Base):
    def test_only_structured_fields_leave(self):
        ref = refs470.extract("L'article 1240 du Code civil")[0]
        payload = sources470.outbound(ref, '2020-01-01')
        self.assertEqual(set(payload), {'kind', 'code', 'number', 'at_date'})

    def test_suspicious_field_is_refused(self):
        with self.assertRaises(Stop):
            sources470.outbound({'kind': 'article', 'code': 'Code civil <M. BERTIN>', 'number': '1240'})
        with self.assertRaises(Stop):
            sources470.outbound({'kind': 'decision', 'court': 'cass', 'pourvoi': 'Dupont c/ FIMEX'})

    def test_request_is_audited_and_contains_no_text(self):
        text = "Mon client M. Jean BERTIN doit 12 345,67 € (article 1240 du Code civil)."
        verify470.verify_text(self.desk, text, connector=self.connector())
        log = sources470.outbound_log(self.desk)
        self.assertEqual(len(log), 1)
        flat = json.dumps(log, ensure_ascii=False)
        self.assertNotIn('BERTIN', flat)
        self.assertNotIn('12 345', flat)
        self.assertIn('1240', flat)

    def test_disabled_sources_never_say_verified(self):
        report = verify470.verify_text(self.desk, "L'article 1240 du Code civil.", connector=self.connector(enabled=False))
        self.assertEqual(report['items'][0]['status'], 'non_verifiable')
        self.assertTrue(report['needs_check'])
        self.assertEqual(sources470.outbound_log(self.desk), [])


class Outages(Base):
    class Down(sources470.Provider):
        name = 'panne'
        calls = 0

        def article(self, payload):
            type(self).calls += 1
            raise Stop('connexion_http_indisponible')

        decision = article

    def test_outage_is_unverifiable_not_missing(self):
        self.Down.calls = 0
        report = verify470.verify_text(self.desk, "L'article 1240 du Code civil.", connector=self.connector([self.Down()]))
        item = report['items'][0]
        self.assertEqual(item['status'], 'non_verifiable')
        self.assertIn('injoignable', ' '.join(item['reasons']))

    def test_circuit_breaker_stops_hammering(self):
        self.Down.calls = 0
        connector = self.connector([self.Down()])
        for i in range(6):
            verify470.verify_text(self.desk, "Voir l'article %d du Code civil." % (1240 + i), connector=connector)
        self.assertEqual(self.Down.calls, sources470.BREAKER_FAILURES)
        self.clock[0] += sources470.BREAKER_SECONDS + 1
        verify470.verify_text(self.desk, "Voir l'article 1250 du Code civil.", connector=connector)
        self.assertEqual(self.Down.calls, sources470.BREAKER_FAILURES + 1)

    def test_cache_and_stale_answer_during_outage(self):
        good = sources470.LocalProvider(data=CORPUS)
        connector = self.connector([good])
        text = "L'article 1240 du Code civil."
        self.assertEqual(verify470.verify_text(self.desk, text, connector=connector)['items'][0]['status'], 'verifiee')
        self.assertEqual(connector.stats['requests'], 1)
        verify470.verify_text(self.desk, text, connector=connector)
        self.assertEqual(connector.stats['requests'], 1)           # servi par le cache
        self.assertEqual(connector.stats['cache_hits'], 1)

        class Flaky(sources470.LocalProvider):
            name = 'local'

            def article(self, payload):
                raise Stop('delai_http_depasse')
        self.clock[0] += sources470.TTL['found'] + 10                # cache périmé
        degraded = self.connector([Flaky(data=CORPUS)])
        item = verify470.verify_text(self.desk, text, connector=degraded)['items'][0]
        self.assertEqual(item['status'], 'verifiee')
        self.assertTrue(any('cache périmé' in n for n in item['notes']))

    def test_unexpected_exception_is_contained(self):
        class Broken(sources470.Provider):
            name = 'casse'

            def article(self, payload):
                raise ValueError('secret interne')
        report = verify470.verify_text(self.desk, "L'article 1240 du Code civil.", connector=self.connector([Broken()]))
        self.assertEqual(report['items'][0]['status'], 'non_verifiable')
        self.assertNotIn('secret interne', json.dumps(report, ensure_ascii=False))

    def test_divergent_sources_are_doubtful(self):
        empty = sources470.LocalProvider(data={'articles': [], 'decisions': [], 'as_of': '2026-09-30'})
        empty.name = 'autre'
        report = verify470.verify_text(self.desk, "L'article 1240 du Code civil.",
                                       connector=self.connector([sources470.LocalProvider(data=CORPUS), empty]))
        self.assertEqual(report['items'][0]['status'], 'douteuse')


class Piste(Base):
    class FakeHTTP:
        def __init__(self, routes):
            self.routes, self.calls = routes, []

        def request(self, method, url, data=None, headers=None, limit=0):
            self.calls.append((method, url, data))
            for key, value in self.routes.items():
                if key in url:
                    return json.dumps(value).encode()
            raise Stop('http_404')

    def test_legifrance_provider_parses_versions_and_sends_only_reference(self):
        http = self.FakeHTTP({'oauth/token': {'access_token': 'T', 'expires_in': 3600},
                              '/search': {'results': [{'sections': [{'extracts': [{'id': 'LEGIARTI000032041571', 'num': '1240'}]}]}]},
                              '/consult/getArticle': {'article': {'id': 'LEGIARTI000032041571', 'articleVersions': [
                                  {'id': 'LEGIARTI000032041571', 'dateDebut': 1475280000000, 'dateFin': 32472144000000,
                                   'etat': 'VIGUEUR', 'texte': 'Tout fait quelconque de l\'homme'}]}}})
        provider = sources470.PisteLegifrance('client-id-123', 'secret-value', http=http)
        connector = self.connector([provider])
        item = verify470.verify_text(self.desk, "L'article 1240 du Code civil.", connector=connector)['items'][0]
        self.assertEqual(item['status'], 'verifiee')
        self.assertIn('legifrance.gouv.fr', item['url'])
        sent = ' '.join(str(c[2]) for c in http.calls)
        self.assertIn('1240', sent)
        self.assertTrue(item['sources'][0]['official'])

    def test_judilibre_requires_exact_number_match(self):
        http = self.FakeHTTP({'oauth/token': {'access_token': 'T', 'expires_in': 3600},
                              '/search': {'results': [{'id': 'X1', 'number': '99-99.999', 'decision_date': '2019-03-12',
                                                        'chamber': 'comm', 'jurisdiction': 'cc'}]}})
        provider = sources470.PisteJudilibre('client-id-123', 'secret-value', http=http)
        item = verify470.verify_text(self.desk, 'Cass. com., 12/03/2019, n° 17-12.345.', connector=self.connector([provider]))['items'][0]
        self.assertEqual(item['status'], 'introuvable')


class Dataset100(Base):
    def test_every_voluntary_error_is_flagged_and_exact_ones_pass(self):
        connector = self.connector()
        missed, false_alarms = [], []
        for case in CASES:
            report = verify470.verify_text(self.desk, case['text'], case['fact_date'], connector=connector)
            self.assertGreaterEqual(report['total'], 1, case)
            statuses = {i['status'] for i in report['items']}
            if case['expected'] == 'flag':
                if report['needs_check'] is not True or statuses == {'verifiee'}:
                    missed.append(case['n'])
            elif report['needs_check']:
                false_alarms.append((case['n'], [i['status'] for i in report['items']], [i['reasons'] for i in report['items']]))
        flagged = [c for c in CASES if c['expected'] == 'flag']
        self.assertEqual(len(CASES), 100)
        self.assertEqual(len(flagged), 60)
        self.assertEqual(missed, [], 'erreurs volontaires non signalées')
        self.assertEqual(false_alarms, [], 'références exactes signalées à tort')

    def test_nothing_absent_from_sources_is_presented_as_certain(self):
        connector = self.connector([sources470.LocalProvider(data={'articles': [], 'decisions': [], 'as_of': '2026-09-30'})])
        for case in CASES:
            report = verify470.verify_text(self.desk, case['text'], case['fact_date'], connector=connector)
            for item in report['items']:
                self.assertNotEqual(item['status'], 'verifiee', case['n'])

    def test_fact_date_selects_the_applicable_version(self):
        text = "L'article L123-1 du Code de commerce."
        old = verify470.verify_text(self.desk, text, '2010-01-01', connector=self.connector())['items'][0]
        self.assertEqual(old['status'], 'verifiee')
        self.assertIn('diffère', ' '.join(old['notes']))
        self.assertIn('ancienne', old['applicable']['excerpt'])

    def test_non_official_source_is_labelled_with_date(self):
        item = verify470.verify_text(self.desk, "L'article 1240 du Code civil.", connector=self.connector())['items'][0]
        self.assertEqual(item['sources'][0]['date'], '2026-09-30')
        self.assertIn('Légifrance', ' '.join(item['notes']))


class Marking(Base):
    def test_gate_draft_marks_unverified_and_leaves_clean_text(self):
        import agent.verify470 as v
        orig = sources470.Sources

        class Fake(orig):
            def __init__(s, desk, **kw):
                orig.__init__(s, desk, [sources470.LocalProvider(data=CORPUS)], True)
        v.sources470.Sources = Fake
        try:
            body, subject, report = v.gate_draft(self.desk, "Bonjour,\nVoir l'article 9999 du Code civil.", 'Objet')
            self.assertTrue(body.startswith('[À VÉRIFIER'))
            self.assertTrue(subject.startswith('[À VÉRIFIER]'))
            body2, subject2, report2 = v.gate_draft(self.desk, 'Bonjour, merci.', 'Objet')
            self.assertEqual((body2, subject2), ('Bonjour, merci.', 'Objet'))
        finally:
            v.sources470.Sources = orig


if __name__ == '__main__':
    unittest.main()

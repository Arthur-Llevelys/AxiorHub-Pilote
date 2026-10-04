"""Page et routes Échéances à travers l'application WSGI réelle."""
from datetime import date
import json
import unittest

from agent import echeances450 as ech
from test_v440_web import WebWorkshopTests as Base0
from test_v450_echeances import FakeDAV


class EcheancesWebTests(unittest.TestCase):
    PATH = Base0.PATH
    call = Base0.call
    _base_setup = Base0.setUp

    def setUp(self):
        self._base_setup()
        self.cal = FakeDAV()
        from unittest.mock import patch
        p = patch('agent.office440.dav_client', lambda desk: self.cal)
        p.start()
        self.addCleanup(p.stop)

    def post(self, name, body, **kw):
        r = self.call('/api440/' + name, 'POST', body, **kw)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def test_page_requires_auth_and_renders_empty_state(self):
        self.assertTrue(self.call('/echeances', auth=False)['status'].startswith('401'))
        r = self.call('/echeances')
        self.assertTrue(r['status'].startswith('200'))
        text = r['body'].decode()
        self.assertIn('Échéances de procédure', text)
        self.assertIn('Non couverts', text)
        self.assertIn('Aucune échéance en cours', text)
        self.assertIn('/static/v450.js', text)
        self.assertEqual(self.call('/static/v450.js')['status'][:3], '200')
        self.assertEqual(self.call('/static/v450.css')['status'][:3], '200')

    def test_preview_returns_calculation_and_never_saves(self):
        r, d = self.post('deadline/preview', {'rule_id': 'appel_jugement_contentieux', 'start': '2026-04-25'})
        self.assertTrue(r['status'].startswith('200'))
        self.assertEqual(d['due'], '2026-05-26')
        self.assertIn('art. 642', ' '.join(d['steps']))
        self.assertEqual(ech.listing(self.desk), [])

    def test_preview_errors_are_readable(self):
        r, d = self.post('deadline/preview', {'rule_id': 'conclusions_appelant', 'start': '2024-05-02', 'regime_date': '2024-04-01'})
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('non couvert', d['message'])
        r, d = self.post('deadline/preview', {'rule_id': 'appel_jugement_contentieux', 'start': 'demain'})
        self.assertTrue(r['status'].startswith('400'))

    def test_create_confirm_correct_flow_and_page_shows_rule_and_calc(self):
        r, d = self.post('deadline/create', {'matter': 'DOS-001', 'rule_id': 'appel_jugement_contentieux', 'start': '2026-09-10'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(d['due'], '2026-10-12')
        self.assertEqual(len(self.cal.store), 1)
        page = self.call('/echeances')['body'].decode()
        self.assertIn('lundi 12 octobre 2026', page)
        self.assertIn('CPC art. 538', page)
        self.assertIn('Calcul', page)
        self.assertIn('art. 642', page)
        r, d2 = self.post('deadline/correct', {'id': d['id'], 'reason': '', 'new_due': '2026-10-20'})
        self.assertTrue(r['status'].startswith('400'))
        r, d2 = self.post('deadline/correct', {'id': d['id'], 'reason': 'Signification reçue plus tôt', 'new_due': '2026-10-20'})
        self.assertEqual(d2['status'], 'manuel')
        self.assertEqual(len(self.cal.store), 1)
        r = self.call('/api440/deadline/journal', query='id=' + d['id'])
        log = json.loads(r['body'])['journal']
        self.assertEqual(log[0]['action'], 'correction')
        listing = json.loads(self.call('/api440/deadlines')['body'])['deadlines']
        self.assertEqual(listing[0]['due'], '2026-10-20')

    def test_post_routes_need_origin_and_csrf(self):
        body = {'matter': 'DOS-001', 'rule_id': 'appel_jugement_contentieux', 'start': '2026-09-10'}
        self.assertTrue(self.post('deadline/create', body, csrf=False)[0]['status'].startswith('400'))
        self.assertTrue(self.post('deadline/create', body, origin=False)[0]['status'].startswith('400'))
        self.assertTrue(self.post('deadline/create', body, auth=False)[0]['status'].startswith('401'))
        self.assertEqual(ech.listing(self.desk), [])

    def test_html_is_escaped(self):
        row = ech.create_deadline(self.desk, 'DOS-001', 'appel_jugement_contentieux', '2026-09-10',
                                  source_path='/Dossiers/DEMO/<script>alert(1)</script>.pdf', excerpt='<img src=x onerror=alert(1)>')
        page = self.call('/echeances')['body'].decode()
        self.assertNotIn('<script>alert(1)', page)
        self.assertNotIn('<img src=x', page)
        self.assertIn('&lt;img src=x', page)
        self.assertEqual(row['status'], 'a_confirmer')

    def test_close_and_check_and_settings(self):
        r, d = self.post('deadline/create', {'matter': 'DOS-001', 'rule_id': 'pourvoi_cassation', 'start': '2026-08-30'})
        r, out = self.post('deadline/check', {})
        self.assertTrue(r['status'].startswith('200'))
        self.assertTrue(out['agenda_verifie'])
        r, out = self.post('deadline/close', {'id': d['id'], 'outcome': 'annulee', 'reason': ''})
        self.assertTrue(r['status'].startswith('400'))
        r, out = self.post('deadline/close', {'id': d['id'], 'outcome': 'terminee'})
        self.assertEqual(out['status'], 'terminee')
        r, out = self.post('settings/deadlines', {'enabled': False, 'calendar': False})
        self.assertTrue(out['saved'])
        self.assertFalse(self.desk.settings('automation:deadlines450_enabled', True))

    def test_today_page_shows_upcoming_deadlines(self):
        self.post('deadline/create', {'matter': 'DOS-001', 'rule_id': 'appel_jugement_contentieux', 'start': date.today().isoformat()})
        text = self.call('/aujourdhui', query='vue=detail')['body'].decode()
        self.assertIn('Échéances de procédure', text)
        self.assertIn('Ouvrir les échéances', text)

    def test_navigation_contains_echeances(self):
        text = self.call('/courriels')['body'].decode()
        self.assertIn('/agent-courriel/echeances', text)



del Base0  # évite de rejouer les tests de test_v440_web ici

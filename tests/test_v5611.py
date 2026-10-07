"""5.6.11 : un courriel envoyé par le cabinet n'est jamais une demande ; les balayages automatiques n'examinent que les courriels
récents (plus de propositions de réponses, de projets ou d'actes sur des affaires traitées des semaines plus tôt)."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from agent import balayage5611, orchestrator
from agent.mailbox import own_addresses
import test_v530 as t530


class OwnAddresses(unittest.TestCase):
    def test_sender_address_and_login_count_as_the_firm_even_if_not_listed(self):
        cfg = {'own_addresses': ['Cabinet@Example.Test'], 'from_address': 'Contact@Example.Test', 'username': 'contact@example.test'}
        self.assertEqual(own_addresses(cfg), {'cabinet@example.test', 'contact@example.test'})
        self.assertEqual(own_addresses({'own_addresses': [], 'username': 'login-sans-arobase'}), set())


class Sweeps(t530.Base):
    def rows(self):
        now = datetime.now(timezone.utc)
        items = [('a' * 64, 'client@example.test', now - timedelta(days=2)),           # récent : examiné
                 ('b' * 64, 'client@example.test', now - timedelta(days=40)),          # ancien, déjà traité : jamais réexaminé
                 ('c' * 64, 'contact@example.test', now - timedelta(days=1))]          # envoyé par le cabinet : jamais une demande
        self.desk.db.execute('DELETE FROM work_items')
        for key, sender, received in items:
            self.desk.db.execute('INSERT INTO work_items VALUES(?,?,?,?,?,?,?,?,?,?)',
                                 (key, 'drafted', 'drafted', '', '20240101001', 'Sujet', sender, received.isoformat(), received.isoformat(), ''))
        self.desk.db.commit()
        self.desk.c['mail']['from_address'] = 'contact@example.test'
        self.desk.c['mail']['own_addresses'] = [x for x in self.desk.c['mail'].get('own_addresses', []) if x != 'contact@example.test']

    def test_orchestrator_and_autonomy_only_look_at_recent_mail_from_others(self):
        self.rows()
        self.assertEqual(balayage5611.lookback_days({}), 14);self.assertEqual(balayage5611.lookback_days({'lookback_days': 500}), 365)
        for kind in ('orchestrator', 'autonomy'):
            keys = [r['mail_key'] for r in balayage5611.recent_work_items(self.desk, {}, kind, 50)]
            self.assertEqual(keys, ['a' * 64], kind)
        keys = [r['mail_key'] for r in balayage5611.recent_work_items(self.desk, {'lookback_days': 60}, 'orchestrator', 50)]
        self.assertEqual(keys, ['a' * 64, 'b' * 64])                                    # fenêtre élargie volontairement
        seen = []
        with patch('agent.integration.sync_work_items'), patch('agent.orchestrator.orchestrate_mail', side_effect=lambda desk, args: seen.append(args['mail_key']) or {}):
            out = orchestrator.sweep(self.desk, {})
        self.assertEqual(seen, ['a' * 64]);self.assertEqual(out['orchestrated'], 1)
        from agent.autonomy import observe_mail
        with patch('agent.integration.sync_work_items'), patch('agent.autonomy.report_for', side_effect=lambda c, key: seen.append('auto:' + key) or (_ for _ in ()).throw(__import__('agent.common', fromlist=['Stop']).Stop('x'))):
            observe_mail(self.desk, {})
        self.assertEqual([k for k in seen if k.startswith('auto:')], ['auto:' + 'a' * 64])


if __name__ == '__main__':
    unittest.main()

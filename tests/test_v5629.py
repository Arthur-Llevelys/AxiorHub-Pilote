"""5.6.29 : maîtrise du travail automatique — pause de l'IA par dossier, consignes permanentes, coupe-circuit des relances, boucles
corrigées (message trop lourd dans le suivi des engagements, mémoire relancée sans fin, création automatique en double)."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.common import Stop
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
MID = '20240101001'


class Pause(t530.Base):
    def test_pause_cancels_automatic_work_and_keeps_lawyer_requests(self):
        from agent import pause5629
        auto = self.desk.enqueue('prepare_document_project', {'matter': MID, 'document_type': 'courrier', 'instruction': 'x', 'automatic': 'yes'}, priority=20)
        brief = self.desk.enqueue('refresh_brief', {'matter': MID})
        mine = self.desk.enqueue('prepare_document_project', {'matter': MID, 'document_type': 'courrier', 'instruction': 'ma demande'}, priority=0)
        out = pause5629.set_pause(self.desk, MID, True)
        self.assertEqual(out['cancelled'], 2)
        status = {r['id']: r['status'] for r in self.desk.db.execute('SELECT id,status FROM jobs')}
        self.assertEqual((status[auto], status[brief], status[mine]), ('cancelled', 'cancelled', 'pending'))
        with self.assertRaisesRegex(Stop, 'agent_en_pause_sur_ce_dossier'):
            self.desk.enqueue('refresh_brief', {'matter': MID})
        with self.assertRaisesRegex(Stop, 'agent_en_pause_sur_ce_dossier'):
            self.desk.enqueue('prepare_document_project', {'matter': MID, 'document_type': 'note', 'instruction': 'y', 'automatic': 'yes'}, priority=20)
        self.assertTrue(self.desk.enqueue('docrequest520', {'request': 'abc', 'matter': MID}, priority=0))     # demande de l'avocat
        row = {'priority': 50}
        with self.assertRaisesRegex(Stop, 'agent_en_pause_sur_ce_dossier'):
            pause5629.check_running(self.desk, row, {'matter': MID})
        pause5629.check_running(self.desk, {'priority': 0}, {'matter': MID})
        pause5629.set_pause(self.desk, MID, False)
        self.assertTrue(self.desk.enqueue('refresh_brief', {'matter': MID}))
        with self.assertRaisesRegex(Stop, 'dossier_absent'):
            pause5629.set_pause(self.desk, 'INCONNU', True)

    def test_three_consecutive_failures_stop_automatic_relaunches_until_reauthorised(self):
        from agent import pause5629
        for _ in range(3):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority) VALUES(?,?,?,?,?,?,?)",
                                 ('refresh_brief', json.dumps({'matter': MID}), 'error', self.desk.now(), self.desk.now(), json.dumps({'erreur': 'generation_tronquee'}), 50))
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'relances_suspendues_apres_trois_echecs'):
            self.desk.enqueue('refresh_brief', {'matter': MID})
        self.assertTrue(self.desk.enqueue('refresh_brief', {'matter': MID, 'source': 'avocat'}, priority=0))   # à la main : toujours possible
        stuck = pause5629.blocked(self.desk, MID)
        self.assertEqual([(s['kind'], s['failures'], s['error']) for s in stuck], [('refresh_brief', 3, 'generation_tronquee')])
        pause5629.reset(self.desk, 'refresh_brief', MID)
        self.assertEqual(pause5629.blocked(self.desk, MID), [])
        self.desk.db.execute("UPDATE jobs SET status='done' WHERE kind='refresh_brief' AND status='pending'")
        self.desk.db.commit()
        self.assertTrue(self.desk.enqueue('refresh_brief', {'matter': MID}))

    def test_standing_instructions_reach_the_agent(self):
        from agent import pause5629
        pause5629.set_consignes(self.desk, MID, 'Ne plus préparer de projet sans ma demande.')
        args = pause5629.with_consignes(self.desk, {'matter': MID, 'instruction': 'Préparer une relance.'})
        self.assertIn('Consignes permanentes de l’avocat pour ce dossier', args['instruction'])
        self.assertIn('Ne plus préparer de projet sans ma demande.', args['instruction'])
        self.assertEqual(pause5629.with_consignes(self.desk, {'matter': MID}), {'matter': MID})
        with self.assertRaisesRegex(Stop, 'consignes_trop_longues'):
            pause5629.set_consignes(self.desk, MID, 'x' * 4001)
        for path, needle in (('agent/intelligence.py', "payload['consignes_permanentes_du_dossier']"), ('agent/docrequest520.py', "payload['consignes_permanentes_du_dossier']"),
                             ('agent/dossier5627.py', "'consignes_permanentes_du_dossier'"), ('agent/desk.py', 'with_consignes(self,json.loads(row'),
                             ('agent/model.py', 'consignes_permanentes_du_dossier')):
            self.assertIn(needle, (ROOT / path).read_text(encoding='utf-8'), path)

    def test_matter_page_and_activity_offer_the_controls(self):
        from agent import pause5629
        page = self.request('/dossier', query='id=' + MID)['body']
        for text in ('⏸ Mettre l’IA en pause', 'Consignes pour l’agent', 'Enregistrer les consignes'):
            self.assertIn(text, page)
        pause5629.set_pause(self.desk, MID, True)
        page = self.request('/dossier', query='id=' + MID)['body']
        self.assertIn('IA en pause sur ce dossier.', page)
        self.assertIn('▶ Reprendre l’IA', page)
        self.assertIn('⏸ IA en pause', self.request('/dossiers', query='state=all')['body'])
        from agent.standalone_auth import allowed
        for route in ('m5627/pause', 'm5627/consignes', 'm5627/reautoriser'):
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + route))
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + route))
        self.assertIn("'data-pause-matter'", (ROOT / 'agent' / 'cockpit530.py').read_text(encoding='utf-8'))
        self.assertIn("call('m5627/pause'", (ROOT / 'agent' / 'static' / 'v530.js').read_text(encoding='utf-8'))
        self.assertIn('[data-d27-pause]', (ROOT / 'agent' / 'static' / 'v5627.js').read_text(encoding='utf-8'))


class Loops(unittest.TestCase):
    def test_known_loops_are_closed(self):
        engine = (ROOT / 'agent' / 'engine.py').read_text(encoding='utf-8')
        self.assertIn("return finish('ignored','dossier_en_pause')", engine)
        for path in ('agent/followup568.py', 'agent/proactive568.py'):
            src = (ROOT / path).read_text(encoding='utf-8')
            self.assertIn("if str(ex)!='message_trop_volumineux':raise", src, path)
            self.assertIn('headers_only=True', src, path)
        self.assertIn("if data.get('automatic'):   # 5.6.29", (ROOT / 'agent' / 'document_projects.py').read_text(encoding='utf-8'))
        desk = (ROOT / 'agent' / 'desk.py').read_text(encoding='utf-8')
        self.assertIn("try:self.enqueue('refresh_brief',{'matter':m['id']});queued.append(m)", desk)
        self.assertIn("if str(e)=='agent_en_pause_sur_ce_dossier':", desk)

    def test_oversized_message_no_longer_aborts_the_engagement_scan(self):
        from agent import followup568
        from datetime import datetime, timezone
        from email.message import EmailMessage
        from agent.mailbox import Mail

        class Client:
            def select(self, folder):
                return '7'

            def search(self, *a):
                return ['1', '2']

            def fetch(self, folder, uid, headers_only=False):
                if uid == '1' and not headers_only:
                    raise Stop('message_trop_volumineux')
                m = EmailMessage(); m['From'] = 'x@example.test'; m['Message-ID'] = '<m%s@x>' % uid; m['Subject'] = 's'
                m.set_content('corps')
                return Mail(uid, '7', folder, set(), datetime(2026, 10, 9, tzinfo=timezone.utc), m)

            def close(self):
                pass

        class Desk:
            c = {'mail': {'host': 'h', 'username': 'u', 'inbox': 'INBOX'}}
            store = {}

            def settings(self, k, d=None):
                return self.store.get(k, d)

            def setting(self, k, v):
                self.store[k] = v

            def now(self):
                return '2026-10-10T00:00:00+00:00'

            class db:
                @staticmethod
                def execute(*a):
                    class R:
                        @staticmethod
                        def fetchall():
                            return []
                    return R()

        with patch('agent.settings568.profile', return_value={'enabled': True, 'roles': ['A1'], 'lookback_days': 30}), \
                patch('agent.settings568.check_owner', return_value=None):
            out = followup568.scan(Desk(), 'cabinet', box=Client())
        self.assertEqual(out['checked'], 2)


if __name__ == '__main__':
    unittest.main()

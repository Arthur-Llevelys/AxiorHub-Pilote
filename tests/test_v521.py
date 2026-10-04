"""5.2.1 : file de travail déblocable — durée maximale de l'analyse, détenteur du verrou, travaux automatiques annulables,
réglage d'automatisme immédiat, surveillance relançable, diagnostic lisible (interruptions, dossier, motifs de l'IA)."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import queue521, web520
from agent.common import Stop
from agent.desk import Desk
import test_agent as ta
import test_v510 as t510


class EngineBudget(unittest.TestCase):
    def setUp(self):
        ta.EngineTests.setUp(self)
        self.t._ignore_cleanup_errors = True                      # Windows : base SQLite encore ouverte au nettoyage

    def test_analysis_releases_the_lock_after_its_time_budget(self):
        self.c['max_messages_per_run'] = 5
        self.c['max_run_seconds'] = 0
        self.box.inputs = {'1': ta.mail('1'), '2': ta.mail('2'), '3': ta.mail('3')}
        result = self.engine.run()
        self.assertTrue(result.get('suite'))
        self.assertEqual(len(self.box.appended), 1)                  # un courriel traité, puis le verrou est rendu
        info = json.loads((Path(self.c['state_dir']) / 'run.lock.json').read_text(encoding='utf-8'))
        self.assertIn('Analyse des courriels', info['holder'])
        self.c['max_run_seconds'] = 180
        self.engine.run()
        self.assertEqual(len(self.box.appended), 3)                  # les suivants sont repris au passage suivant


class Queue(t510.Base):
    def test_report_purge_keeps_user_requests(self):
        self.desk.enqueue('index_all')
        self.desk.enqueue('monitor_all', priority=65)
        self.desk.enqueue('index', {'matter': 'DEMO'}, priority=50)
        self.desk.enqueue('automation_setting', {'key': 'sync_enabled', 'value': 'no'})
        rep = queue521.report(self.desk)
        self.assertEqual((rep['pending_total'], rep['pending_automatic']), (4, 3))
        self.assertFalse(next(p for p in rep['pending'] if p['kind'] == 'automation_setting')['automatic'])
        out = queue521.purge_automatic(self.desk)
        self.assertEqual((out['cancelled'], out['kept']), (3, 1))
        left = [r[0] for r in self.desk.db.execute("SELECT kind FROM jobs WHERE status='pending'")]
        self.assertEqual(left, ['automation_setting'])

    def test_automation_setting_is_applied_even_when_the_lock_is_busy(self):
        jid = self.desk.enqueue('automation_setting', {'key': 'sync_enabled', 'value': 'no'})
        self.desk.enqueue('index_all')
        def busy(*a, **k):
            if len(a) > 1 and a[1] & 4:
                raise BlockingIOError()
        with patch('agent.desk.fcntl.flock', side_effect=busy):
            self.assertTrue(self.desk.work_once())
        self.assertEqual(self.desk.db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0], 'done')
        self.assertFalse(self.desk.settings('automation:sync_enabled', True))
        self.assertEqual(self.desk.db.execute("SELECT status FROM jobs WHERE kind='index_all'").fetchone()[0], 'pending')

    def test_settings_button_applies_immediately_and_withdraws_queued_duplicate(self):
        self.desk.enqueue('automation_setting', {'key': 'automatic_mail_drafts_enabled', 'value': 'no', 'back': 'settings'})
        r = self.request('/action', 'POST', {'action': 'automation_setting', 'key': 'automatic_mail_drafts_enabled', 'value': 'yes',
                                             'back': 'settings', 'csrf': 'test-csrf'}, origin=self.origin)
        self.assertTrue(r['status'].startswith('303'))
        d = Desk(self.f.c)
        self.assertTrue(d.settings('automation:automatic_mail_drafts_enabled', False))
        self.assertEqual(d.db.execute("SELECT status FROM jobs WHERE kind='automation_setting'").fetchone()[0], 'cancelled')

    def test_lock_holder_is_reported(self):
        queue521.note_holder(self.f.c['state_dir'], 'Analyse des courriels (lecture, tri et brouillons)')
        with patch('agent.queue521.fcntl.flock', side_effect=BlockingIOError()):
            st = queue521.lock_state(self.f.c['state_dir'])
        self.assertTrue(st['busy'])
        self.assertIn('Analyse des courriels', st['holder'])
        self.assertFalse(queue521.lock_state(self.f.c['state_dir'])['busy'])

    def test_suspended_surveillance_is_explained_and_resumable(self):
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts) VALUES(?,?,?,?,?,?,?,?)",
                             ('live_documents430', '{}', 'error', '2026-10-02T10:00:00+00:00', '2026-10-02T10:00:00+00:00',
                              json.dumps({'erreur': 'nextcloud_injoignable'}), 55, 3))
        self.desk.db.commit()
        d = web520.diagnosis(self.desk)
        self.assertTrue(any('Documents des dossiers' in p and 'suspendue' in p for p in d['problems']))
        body = self.request('/diagnostic')['body']
        self.assertIn('Relancer la surveillance', body)
        out = web520.handle(self.desk, 'm520/queue/resume', {})
        self.assertEqual(out['resumed'], ['Documents des dossiers'])
        self.assertFalse(queue521.report(self.desk)['surveillance'][2]['suspended'])


class Diagnostic(t510.Base):
    def add_error(self, kind, code, args=None, priority=80):
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts) VALUES(?,?,?,?,?,?,?,1)",
                             (kind, json.dumps(args or {}), 'error', queue521.now(), queue521.now(), json.dumps({'erreur': code}), priority))
        self.desk.db.commit()

    def test_interruptions_are_not_errors_and_errors_name_the_matter(self):
        self.add_error('run', queue521.INTERRUPTED, priority=0)
        self.add_error('orchestrator_mail_sweep', queue521.INTERRUPTED, priority=40)
        self.add_error('prepare_reply', 'dossier_absent', {'matter': 'ANCIEN-42', 'key': 'k'}, priority=0)
        self.add_error('prepare_draft', 'generation_ia_delai_depasse', {'matter': 'DEMO', 'key': 'k2'}, priority=0)
        self.add_error('legal_opinion', 'jurisprudence_officielle_verifiee_absente', {}, priority=10)
        d = web520.diagnosis(self.desk)
        self.assertEqual(d['interrupted'], 2)
        self.assertEqual(len(d['errors']), 3)
        by = {x['code']: x for x in d['errors']}
        self.assertEqual(by['dossier_absent']['matter'], 'dossier inconnu : ANCIEN-42')
        self.assertEqual(by['generation_ia_delai_depasse']['matter'], 'DEMO')
        self.assertIn('Tester l’IA', by['generation_ia_delai_depasse']['reason'])
        self.assertNotIn('_', by['jurisprudence_officielle_verifiee_absente']['reason'])
        self.assertTrue(any('3 demande(s) en erreur' in p for p in d['problems']))
        body = self.request('/diagnostic')['body']
        for text in ('Dossier</th>', '2 contrôle(s) automatique(s) interrompu(s)', 'Tester l’IA', 'inconnu : ANCIEN-42'):
            self.assertIn(text, body)

    def test_old_backlog_points_to_the_lock_holder_and_offers_purge(self):
        for kind in ('index_all', 'monitor_all'):
            self.desk.enqueue(kind)
        self.desk.db.execute("UPDATE jobs SET created='2026-10-01T08:00:00+00:00'")
        self.desk.db.commit()
        queue521.note_holder(self.f.c['state_dir'], 'Analyse des courriels (lecture, tri et brouillons)')
        with patch('agent.queue521.fcntl.flock', side_effect=BlockingIOError()):
            d = web520.diagnosis(self.desk)
            body = self.request('/diagnostic')['body']
        self.assertTrue(any('tient le verrou' in p for p in d['problems']))
        self.assertIn('Désengorger : annuler les 2 travaux automatiques', body)
        self.assertIn('Verrou de travail tenu par', body)

    def test_ai_check_reports_duration_and_errors(self):
        class Fast:
            def __init__(self, cfg): pass
            def complete(self, *a, **k): return 'OK'
        class Slow(Fast):
            def complete(self, *a, **k): raise Stop('generation_ia_delai_depasse')
        with patch('agent.model.routed_config', return_value={'provider_id': 'ollama', 'model': 'qwen-test', 'url': 'http://127.0.0.1:11434'}), \
             patch('agent.model.Model', Fast):
            out = web520.handle(self.desk, 'm520/check/ia', {})
        self.assertTrue(out['ok'])
        self.assertIn('qwen-test', out['message'])
        with patch('agent.model.routed_config', return_value={'provider_id': 'ollama', 'model': 'qwen-test'}), patch('agent.model.Model', Slow):
            with self.assertRaises(Stop):
                web520.handle(self.desk, 'm520/check/ia', {})

    def test_heartbeat_uses_expected_next_check(self):
        import time
        now = time.time()
        self.desk.db.execute('INSERT OR REPLACE INTO live_services_v430 VALUES(?,?,?,?,?)', ('courriels', now - 420, 'active', '', now - 120))
        self.desk.db.execute('INSERT OR REPLACE INTO live_services_v430 VALUES(?,?,?,?,?)', ('documents', now - 172800, 'active', '', now - 172500))
        self.desk.db.commit()
        beats = {h['name']: h for h in web520.diagnosis(self.desk, now)['heartbeats']}
        self.assertFalse(beats['courriels']['stale'])                # 7 min : passage attendu il y a 2 min, tolérance 10 min
        self.assertTrue(beats['documents']['stale'])


class Http(unittest.TestCase):
    from test_v440_web import WebWorkshopTests as _W
    PATH = _W.PATH
    call = _W.call
    setUp = _W.setUp

    def test_purge_and_resume_routes_over_http(self):
        d = Desk(self.f.c)
        d.enqueue('index_all')
        d.enqueue('monitor_all', priority=65)
        r = self.call('/api440/m520/queue/purge', 'POST', {})
        self.assertTrue(r['status'].startswith('200'), r)
        self.assertEqual(json.loads(r['body'])['cancelled'], 2)
        self.assertTrue(self.call('/api440/m520/queue/purge', 'POST', {}, csrf=False)['status'].startswith('4'))
        r = self.call('/api440/m520/queue/resume', 'POST', {})
        self.assertIn('Aucune surveillance suspendue', json.loads(r['body'])['message'])


if __name__ == '__main__':
    unittest.main()

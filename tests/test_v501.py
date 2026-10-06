"""5.0.1 : agenda et tâches rétablis, interface unique, blocage « conflits » limité et expliqué."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import unittest

import test_agent as fixtures
from agent import conflicts500, workplan
from agent.common import Stop, private_json
from agent.desk import Desk
from test_desk import WebTests
from test_v490 import DEMO

ROOT = Path(__file__).resolve().parent.parent
CAL = 'https://cloud.example.test/remote.php/dav/calendars/admin/planning/'


class FakeDAV:
    def __init__(self):
        self.store = {}
        self.deleted = []

    def calendar_url(self, url):
        return CAL

    def events(self, urls, a, b, tz):
        base = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
        out = [{'uid': 'ext-1', 'recurrence_id': '', 'summary': 'Audience MARTINEAU', 'description': 'DOS-001', 'location': '',
                'start': (base + timedelta(days=1)).isoformat(), 'end': (base + timedelta(days=1, hours=2)).isoformat(), 'busy': True,
                'source_url': CAL, 'href': '/e1.ics', 'etag': '"1"'}]
        for uid, (title, start, end) in self.store.items():
            out.append({'uid': uid, 'recurrence_id': '', 'summary': title, 'description': '', 'location': '', 'start': start.isoformat(),
                        'end': end.isoformat(), 'busy': True, 'source_url': CAL, 'href': '/' + uid + '.ics', 'etag': '"x"'})
        return out

    def put_event(self, calendar, event_id, title, start, end, description, replace=False):
        uid = 'axiorhub-' + event_id + '@mail-agent.local'
        if replace and uid not in self.store:
            raise Stop('http_404')
        self.store[uid] = (title, start, end)
        return uid

    def delete_event(self, calendar, uid, etag=''):
        if not re.fullmatch(r'axiorhub-[0-9a-f]{64}@mail-agent\.local', uid):
            raise Stop('evenement_externe_lecture_seule')
        self.store.pop(uid, None)
        self.deleted.append(uid)
        return uid


class Base(unittest.TestCase):
    setUp0 = WebTests.setUp
    request = WebTests.request

    def setUp(self):
        self.setUp0()
        self.f.c['nextcloud_workflow'] = {'enabled': True, 'url': 'https://cloud.example.test', 'username': 'technique', 'password_file': 'x',
                                          'calendar_read_urls': [CAL], 'task_calendar_url': CAL, 'planning_calendar_url': CAL}
        private_json(self.config, self.f.c)
        self.dav = FakeDAV()
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)


class Agenda(Base):
    def test_planning_page_shows_the_calendar_and_the_task_tabs(self):
        from unittest.mock import patch
        with patch('agent.workplan._dav', return_value=self.dav):
            body = self.request('/planning')['body']
        self.assertIn('Audience MARTINEAU', body)
        for label in ('Agenda', 'Tâches', 'Organiser la semaine'):
            self.assertIn('>' + label + '</a>', body)
        self.assertIn('Ajouter un événement', body)

    def test_old_agenda_and_tasks_routes_land_on_the_same_page(self):
        from unittest.mock import patch
        with patch('agent.workplan._dav', return_value=self.dav):
            agenda = self.request('/agenda')['body']
            tasks = self.request('/taches')['body']
        self.assertIn('Événements de l’agenda', agenda)
        self.assertIn('Ajouter une tâche', tasks)
        for body in (agenda, tasks):
            self.assertEqual(re.findall(r'<a [^>]*aria-label="([^"]+)"[^>]*aria-current="page"', body), ['Agenda et tâches'])

    def test_planning_without_calendar_configuration_stays_readable(self):
        self.f.c['nextcloud_workflow'] = {}
        private_json(self.config, self.f.c)
        r = self.request('/planning')
        self.assertTrue(r['status'].startswith('200'))
        self.assertIn('Événements de l’agenda', r['body'])

    def test_event_create_edit_delete_only_for_axiorhub_events(self):
        start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
        made = workplan.create_event(self.desk, {'title': 'Rendez-vous client', 'start': start, 'duration_minutes': 45, 'matter': ''}, self.dav)
        uid = made['event_uid']
        self.assertEqual(len(self.dav.store), 1)
        self.assertRegex(uid, r'^axiorhub-[0-9a-f]{64}@mail-agent\.local$')
        edited = workplan.edit_event(self.desk, {'uid': uid, 'title': 'Rendez-vous déplacé', 'start': start, 'duration_minutes': 30}, self.dav)
        self.assertTrue(edited['calendar_updated'])
        self.assertEqual(self.dav.store[uid][0], 'Rendez-vous déplacé')
        with self.assertRaises(Stop) as ctx:
            workplan.cancel_event(self.desk, {'uid': uid}, self.dav)
        self.assertEqual(str(ctx.exception), 'confirmation_suppression_requise')
        workplan.cancel_event(self.desk, {'uid': uid, 'confirm': 'yes'}, self.dav)
        self.assertEqual(self.dav.deleted, [uid])
        for kind, args in (('edit_event', {'uid': 'ext-1', 'title': 'Piraté', 'start': start}),
                           ('cancel_event', {'uid': 'ext-1', 'confirm': 'yes'})):
            with self.assertRaises(Stop) as ctx:
                getattr(workplan, kind)(self.desk, args, self.dav)
            self.assertEqual(str(ctx.exception), 'evenement_externe_lecture_seule')

    def test_event_validation(self):
        with self.assertRaises(Stop):
            workplan.create_event(self.desk, {'title': 'x', 'start': '2026-10-05T10:00'}, self.dav)
        with self.assertRaises(Stop):
            workplan.create_event(self.desk, {'title': 'Titre valide', 'start': 'pas une date'}, self.dav)
        with self.assertRaises(Stop):
            workplan.create_event(self.desk, {'title': 'Titre valide', 'start': '2026-10-05T10:00', 'matter': 'INCONNU'}, self.dav)
        self.f.c['nextcloud_workflow']['enabled'] = False
        desk = Desk(self.f.c)
        with self.assertRaises(Stop) as ctx:
            workplan.create_event(desk, {'title': 'Titre valide', 'start': '2026-10-05T10:00'}, self.dav)
        self.assertEqual(str(ctx.exception), 'agenda_planification_non_configure')
        desk.db.close()

    def test_event_jobs_are_registered_as_user_jobs(self):
        from agent.desk import JOBS, USER_JOBS
        for kind in ('create_agenda_event', 'edit_agenda_event', 'cancel_agenda_event'):
            self.assertIn(kind, JOBS)
            self.assertIn(kind, USER_JOBS)

    def test_extra_task_lists_are_read_only_sources(self):
        self.f.c['nextcloud_workflow']['task_read_urls'] = ['https://cloud.example.test/remote.php/dav/calendars/admin/perso/']
        desk = Desk(self.f.c)
        self.assertEqual(len(workplan._task_urls(desk)), 2)
        desk.db.close()


class UnifiedInterface(Base):
    PAGES = ('/courriels', '/documents', '/echeances', '/cabinet', '/conflits', '/dossiers', '/production', '/planning', '/parametres')

    def test_every_page_uses_the_same_sidebar(self):
        from unittest.mock import patch
        with patch('agent.workplan._dav', return_value=self.dav):
            for path in self.PAGES:
                r = self.request(path)
                self.assertTrue(r['status'].startswith('200'), path)
                body = r['body']
                self.assertIn('<aside id="ws-sidebar">', body, path)
                nav = re.search(r'<nav aria-label="Navigation principale".*?</nav>', body, re.S).group(0)
                self.assertEqual(nav.count('<a '), 6, path)                                   # 5.3.0 : menu réduit
                self.assertIn('<nav aria-label="Rubriques"', body, path)
                self.assertNotIn('class="ax-top"', body, path)
                self.assertIn('id="ws-ai-launcher"', body, path)

    def test_active_entry_follows_the_page(self):
        expected = {'/courriels': 'Courriels', '/documents': 'Documents', '/echeances': 'Échéances', '/cabinet': 'Cabinet',
                    '/conflits': 'Cabinet', '/dossiers': 'Dossiers', '/production': 'Produire'}
        for path, label in expected.items():
            body = self.request(path)['body']
            current = re.findall(r'<a [^>]*aria-label="([^"]+)"[^>]*aria-current="page"', body)
            self.assertEqual(current, [label], path)

    def test_workshop_styles_follow_the_common_theme_switch(self):
        for name in ('v440.css', 'v460.css', 'v470.css', 'v480.css', 'v490.css', 'v500.css'):
            self.assertNotIn('prefers-color-scheme', (ROOT / 'agent' / 'static' / name).read_text(encoding='utf-8'), name)

    def test_workshop_scripts_are_loaded_once(self):
        body = self.request('/courriels')['body']
        for name in ('v440.js', 'v490.js', 'v500.js'):
            self.assertEqual(body.count('/static/' + name), 1, name)


class Gate(Base):
    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), [DEMO])

    def add_matter(self, mid):
        rows = json.loads(Path(self.f.c['matters_file']).read_text())
        rows.append({**DEMO, 'id': mid, 'client_name': 'Client ' + mid, 'path': '/Dossiers/' + mid, 'references': [mid], 'aliases': ['Client ' + mid], 'correspondents': []})
        private_json(Path(self.f.c['matters_file']), rows)

    def test_background_scan_never_blocks_a_matter(self):
        conflicts500.scan_new(self.desk)
        self.add_matter('DECOUVERT')
        conflicts500.scan_background(self.desk)
        self.assertFalse(conflicts500.matter_gate_state(self.desk, 'DECOUVERT')['gated'])
        self.assertEqual(conflicts500.pending(self.desk), [])
        self.desk.enqueue('prepare_cabinet_letter', {'matter': 'DECOUVERT', 'request': 'Courrier'})

    def test_matter_opened_by_the_lawyer_is_still_gated_with_a_readable_message(self):
        conflicts500.scan_new(self.desk)
        self.add_matter('OUVERT')
        conflicts500.scan_new(self.desk)
        self.assertTrue(conflicts500.matter_gate_state(self.desk, 'OUVERT')['gated'])
        with self.assertRaises(Stop) as ctx:
            self.desk.enqueue('prepare_cabinet_letter', {'matter': 'OUVERT', 'request': 'Courrier'})
        self.assertEqual(str(ctx.exception), 'conflit_a_examiner')
        from agent.web import REASONS
        self.assertIn('conflits d’intérêts', REASONS['conflit_a_examiner'])

    def test_blocked_action_in_the_browser_explains_what_to_do(self):
        conflicts500.scan_new(self.desk)
        self.add_matter('OUVERT')
        conflicts500.scan_new(self.desk)
        r = self.request('/action', 'POST', {'action': 'prepare_cabinet_letter', 'csrf': 'test-csrf', 'matter': 'OUVERT', 'request': 'Courrier'},
                         origin=self.origin)
        self.assertTrue(r['status'].startswith('400'))
        self.assertIn('recherche de conflits', r['body'])
        self.assertIn('/agent-courriel/conflits', r['body'])
        self.assertNotIn('conflit_a_examiner', r['body'])

    def test_matters_stuck_by_5_0_0_are_released_once_but_declined_ones_stay_blocked(self):
        conflicts500.scan_new(self.desk)
        self.add_matter('BLOQUE500')
        self.add_matter('DECLINE')
        now = '2026-10-03T10:00:00+00:00'
        self.desk.db.execute('INSERT INTO conflicts500_matters VALUES(?,?,?,?,?)', ('BLOQUE500', now, 0, '', 'a_examiner'))
        self.desk.db.execute('INSERT INTO conflicts500_matters VALUES(?,?,?,?,?)', ('DECLINE', now, 0, '', 'decline'))
        self.desk.db.execute("DELETE FROM settings WHERE key='conflicts500:released501'")
        self.desk.db.commit()
        self.assertFalse(conflicts500.matter_gate_state(self.desk, 'BLOQUE500')['gated'])
        self.assertTrue(conflicts500.matter_gate_state(self.desk, 'DECLINE')['gated'])
        self.desk.enqueue('prepare_cabinet_letter', {'matter': 'BLOQUE500', 'request': 'Courrier'})

    def test_gate_toggle_route(self):
        from agent import web500
        conflicts500.scan_new(self.desk)
        self.add_matter('OUVERT')
        conflicts500.scan_new(self.desk)
        self.assertEqual(web500.handle(self.desk, 'm500/conflicts/gate', {'enabled': False}), {'gate': False})
        self.desk.enqueue('prepare_cabinet_letter', {'matter': 'OUVERT', 'request': 'Courrier'})
        self.assertEqual(web500.handle(self.desk, 'm500/conflicts/gate', {'enabled': True}), {'gate': True})
        with self.assertRaises(Stop):
            self.desk.enqueue('prepare_cabinet_letter', {'matter': 'OUVERT', 'request': 'Courrier bis'})


if __name__ == '__main__':
    unittest.main()

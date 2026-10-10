"""3.6.1: opening a mail does not dispose of a lawyer review."""
import unittest
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile

import test_agent as fixtures
from agent.desk import Desk
from agent.integration import set_work_state, sync_work_items
from agent.portfolio import reconcile_work_items
from agent.web import App


class ReadMailIsNotHandled(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.f.model.intent = 'legal'
        self.mail = fixtures.mail()
        self.key = self.f.engine.process(self.mail)
        self.assertEqual(self.f.engine.state.get(self.key)[0], 'review')
        self.f.box.inputs[self.mail.uid] = self.mail
        sync_work_items(self.d)

    def state(self):
        return self.d.db.execute('SELECT state FROM work_items WHERE mail_key=?',
                                 (self.key,)).fetchone()[0]

    def test_seen_review_is_recovered_from_old_handled_state_once(self):
        self.mail.flags.add('\\Seen')
        self.d.db.execute("UPDATE work_items SET state='handled' WHERE mail_key=?",(self.key,))
        self.d.db.commit()
        result = reconcile_work_items(self.d,self.f.box)
        self.assertEqual(result['reopened'],1)
        self.assertEqual(self.state(),'needs_action')
        self.assertTrue(self.d.settings('portfolio:seen_review_repair_v361'))
        result = reconcile_work_items(self.d,self.f.box)
        self.assertEqual(result['reopened'],0)
        self.assertEqual(self.state(),'needs_action')

    def test_manual_handled_state_stays_handled(self):
        self.mail.flags.add('\\Seen')
        set_work_state(self.d,self.key,'handled','decision_avocat')
        reconcile_work_items(self.d,self.f.box)
        self.assertEqual(self.state(),'handled')

    def test_answered_review_stays_handled(self):
        self.mail.flags.update({'\\Seen','\\Answered'})
        self.d.db.execute("UPDATE work_items SET state='handled' WHERE mail_key=?",(self.key,))
        self.d.db.commit()
        result = reconcile_work_items(self.d,self.f.box)
        self.assertEqual(result['reopened'],0)
        self.assertEqual(self.state(),'handled')

    def test_read_only_diagnostic_reports_review_and_job_codes(self):
        script=Path(__file__).parents[1]/'diagnostic-v360.py'
        spec=spec_from_file_location('diagnostic_v361',script)
        module=module_from_spec(spec);spec.loader.exec_module(module)
        with __import__('sqlite_cleanup').TempDir() as temp:
            state=Path(temp)
            module.CONFIG=state/'config.json'
            module.CONFIG.write_text(json.dumps({'state_dir':temp}))
            with sqlite3.connect(state/'state.sqlite3') as db:
                db.execute('CREATE TABLE messages(status TEXT,reason TEXT)')
                db.executemany('INSERT INTO messages VALUES(?,?)',
                               [('review','dossier_a_confirmer'),('ignored','liste_diffusion')])
            with sqlite3.connect(state/'desk.sqlite3') as db:
                db.execute('CREATE TABLE jobs(kind TEXT,status TEXT,result TEXT,finished TEXT)')
                db.execute('CREATE TABLE work_items(state TEXT,source_status TEXT)')
                db.execute("INSERT INTO jobs VALUES('prepare_reply','error',?,?)",
                           (json.dumps({'erreur':'modele_indisponible'}),'2026-09-20'))
                db.execute("INSERT INTO work_items VALUES('handled','review')")
            with sqlite3.connect(state/'documents.sqlite3') as db:
                db.execute('CREATE TABLE inventory_scans(matter TEXT)')
            output=StringIO()
            with redirect_stdout(output):module.main()
            diagnosis=json.loads(output.getvalue())
            self.assertEqual(diagnosis['review_reasons'][0]['count'],1)
            self.assertEqual(diagnosis['handled_reviews_to_check'],1)
            self.assertEqual(diagnosis['error_jobs'][0]['code'],'modele_indisponible')

    def test_mail_tabs_count_every_row_and_page_through_ignored_mail(self):
        for number in range(205):
            key=f'{number:064x}'
            self.f.engine.state.report(key,{'subject':f'Courriel fictif {number}',
                'sender':'example@test.invalid','received_at':'2026-09-20T10:00:00+00:00'})
            self.d.db.execute('INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?)',
                (key,'ignored','ignored','liste_diffusion','','','','2026-09-20',
                 f'2026-09-20T{number//60:02d}:{number%60:02d}:00+00:00',None))
        self.d.db.commit()
        auth={'prefix':'/agent-courriel','csrf':'token','origin':'https://example.test'}
        page=App(self.f.c,auth).page(self.f.c,auth,'/',{'view':'ignored','page':'3'})
        self.assertIn('Ignorés <strong>205</strong>',page)
        self.assertIn('Page 3 sur 5',page)
        self.assertIn('Courriel fictif 104',page)


if __name__=='__main__':unittest.main()

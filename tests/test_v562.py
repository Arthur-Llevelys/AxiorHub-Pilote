"""5.6.2 : « résume les mails reçus aujourd'hui » reçoit une réponse immédiate (sans IA ni file de travail), aucun dossier n'est choisi
d'office sur un mot courant, l'état du système montre les fichiers en cause et ne compte plus les redémarrages comme des boucles."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from agent import cockpit530 as ck, maildigest562 as md, reliability393, reliability393_ui
from agent.common import digest, private_json
from agent.docrequest520 import find_matter
from agent.state import State
import test_v520_docs as td
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
PARIS = ZoneInfo('Europe/Paris')
TODAY = date(2026, 10, 4)
QUESTION = 'Est-ce que tu peux me faire un résumé des mails que j\'ai reçus aujourd\'hui, ce 4 octobre 2026 ?'
PJ = {**td.BETA, 'id': '11', 'client_name': '', 'path': '/Dossiers/11 - PJ e-mails', 'references': [], 'aliases': []}


class Detect(unittest.TestCase):
    def test_questions_about_received_mail(self):
        self.assertEqual(md.detect(QUESTION, TODAY)[:2], (TODAY, TODAY))
        self.assertEqual(md.detect('Résume-moi les courriels reçus', TODAY)[2], 'aujourd’hui')
        self.assertEqual(md.detect('Quels mails hier ?', TODAY)[:2], (TODAY - timedelta(days=1),) * 2)
        self.assertEqual(md.detect('Fais le point sur les e-mails de cette semaine', TODAY)[0], date(2026, 9, 28))
        self.assertEqual(md.detect('Liste des courriels du 02/10/2026', TODAY)[:2], (date(2026, 10, 2),) * 2)

    def test_work_requests_are_not_mail_digests(self):
        for text in ('Prépare une réponse au mail de Me Martin', 'Rédige des conclusions dans le dossier ALPHA',
                     'Fais un résumé du dossier ALPHA', 'Envoie un courriel au client aujourd’hui'):
            self.assertIsNone(md.detect(text, TODAY), text)


class Cockpit(t530.Base):
    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), [td.ALPHA, td.BETA, PJ])
        state = State(self.f.c['state_dir'])
        account = digest(self.f.c['mail']['username'] + '@' + self.f.c['mail']['host'])
        base = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
        for i, (status, reason, matter, subject, sender) in enumerate((
                ('drafted', 'reponse_preparee', '20240101001', 'Audience du 5 octobre', 'Me Dupont <dupont@example.test>'),
                ('review', 'decision_avocat_necessaire', '2024020201', 'Proposition transactionnelle', 'client@example.test'),
                ('ignored', 'advertisement', '', 'Offre spéciale', 'promo@example.test'),
                ('ignored', 'advertisement', '', 'Nouvelle offre', 'promo@example.test'))):
            key = digest('mail%d' % i)
            state.set(key, '<m%d@x>' % i, 't%d' % i, status, reason)
            state.report(key, {'key': key, 'subject': subject, 'sender': sender, 'received_at': (base + timedelta(hours=i)).isoformat(),
                               'matter': matter or None, 'account_key': account})
        old = digest('hier')
        state.set(old, '<old@x>', 'old', 'drafted', '')
        state.report(old, {'key': old, 'subject': 'Message d’hier', 'sender': 'x@example.test', 'received_at': '2026-10-03T09:00:00+00:00',
                           'account_key': account})

    def test_digest_lists_today_by_outcome_without_ai(self):
        text = md.answer(self.desk, QUESTION, today=TODAY, tz=PARIS)
        self.assertTrue(text.startswith('4 courriel(s) reçu(s) le 04/10/2026.'), text)
        self.assertIn('À traiter par vous (1)\n• 10:00 — client@example.test — « Proposition transactionnelle » — BRAVO - SCI LES CHENES - 2024020201', text)
        self.assertIn('Brouillon de réponse prêt (dossier Brouillons) (1)\n• 09:00 — Me Dupont — « Audience du 5 octobre » — DEMO', text)
        self.assertIn('Sans réponse nécessaire (2)', text)
        self.assertNotIn('Message d’hier', text)
        self.assertIn('aucun contenu n’a été envoyé à une IA', text)
        only = md.answer(self.desk, QUESTION, today=TODAY, tz=PARIS, matter='2024020201')
        self.assertTrue(only.startswith('1 courriel(s) reçu(s) le 04/10/2026 pour ce dossier.'))
        self.assertIn('Aucun courriel analysé le 01/10/2026', md.answer(self.desk, 'Quels mails le 1 octobre 2026 ?', today=TODAY, tz=PARIS))

    def test_question_is_answered_at_once_and_no_matter_is_guessed(self):
        before = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        self.assertEqual(ck.guess(self.desk, QUESTION)['matter'], '')
        out = ck.ask(self.desk, {'text': QUESTION})
        self.assertEqual(out['ref'], '')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], before)
        if self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='docreq520'").fetchone():
            self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM docreq520').fetchone()[0], 0)
        html = ck.thread_html(self.desk, '/p')
        self.assertIn('courriel(s) reçu(s)', html)
        self.assertNotIn('11 - PJ e-mails', html)

    def test_one_common_word_no_longer_selects_a_matter(self):
        matter, candidates = find_matter(self.desk, 'Prépare une note sur les mails du client')
        self.assertIsNone(matter)
        matter, _ = find_matter(self.desk, 'Prépare une note pour ALPHA')
        self.assertEqual(matter['id'], '20240101001')


class SystemState(t530.Base):
    def test_optional_files_absent_are_not_incidents_and_details_are_shown(self):
        self.desk.c['updates'] = {'metadata_url': '', 'minisign_public_key_file': '/nonexistent/update-minisign.pub'}
        self.desk.c['mail']['password_file'] = '/nonexistent/imap.secret'
        health = reliability393.permission_health(self.desk)
        rows = {r['path'].replace(chr(92), '/'): r for r in health['files']}
        self.assertEqual(rows['/nonexistent/update-minisign.pub']['status'], 'optional')
        self.assertEqual(rows['/nonexistent/imap.secret']['status'], 'error')
        html = reliability393_ui._detail(self.desk, 'permissions', health, {})
        self.assertIn('imap.secret</code> — fichier absent', html)
        self.assertNotIn('update-minisign.pub', html)

    def test_restart_interruptions_of_automatic_checks_are_not_loops(self):
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(4):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts) VALUES(?,?,?,?,?,?,?,1)",
                                 ('orchestrator_mail_sweep', '{}', 'error', now, now, json.dumps({'erreur': 'service_redemarre_verifier_avant_relance'}), 40))
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result,priority,attempts) VALUES(?,?,?,?,?,?,?,1)",
                                 ('prepare_reply', '{"key":"a"}', 'error', now, now, json.dumps({'erreur': 'generation_ia_delai_depasse'}), 0))
        self.desk.db.commit()
        loops = reliability393.job_health(self.desk)['loops']
        self.assertEqual([x['kind'] for x in loops], ['prepare_reply'])


class Buttons(unittest.TestCase):
    def test_immediate_actions_do_not_stay_saved_and_disabled(self):
        js = (ROOT / 'agent' / 'static' / 'v430.js').read_text(encoding='utf-8')
        self.assertNotIn("'Enregistré ✓'", js)
        self.assertIn("button.textContent='Fait ✓'", js)
        self.assertIn('window.location.reload()', js)


if __name__ == '__main__':
    unittest.main()

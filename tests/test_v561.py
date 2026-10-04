"""5.6.1 : survol lisible, volet de relecture sans fond coloré, liens avec le préfixe de l'interface, détail des travaux de l'agent,
« Envoyer… » qui enregistre d'abord, recette qui donne le motif des échecs, minuteries réactivées, file sans attente indéfinie."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent import cockpit530 as ck, docrequest520 as dr, jobview561
from agent.common import Stop
from agent.desk import PICK_ORDER
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'


class Styles(unittest.TestCase):
    def test_generic_hover_rules_only_target_unstyled_buttons(self):
        css = (STATIC / 'app520.css').read_text(encoding='utf-8')
        for selector in re.findall(r'(?:^|[},])\s*([^{}@]*button[^{}]*):hover', css):
            last = selector.split(',')[-1].strip()
            if re.fullmatch(r'button', last) or re.fullmatch(r':root\[data-theme=dark\] button(?::not\(\.[\w-]+\))*', last):
                self.fail('règle de survol générique sur tous les boutons : %s:hover' % last)
        self.assertNotIn(':root[data-theme=dark] button:not(.ws-icon-button):not(.secondary)', css)
        self.assertIn('button:not([class]):hover', css)
        self.assertIn('.c530-item:hover,.c530-item:focus-visible{color:var(--ws-ink', css)
        self.assertIn('v561.css', css)

    def test_drawer_backdrop_is_not_a_button(self):
        self.assertIn('.c530-backdrop,.c530-backdrop:hover{position:absolute;inset:0;background:rgba(15,20,30,.38)', (STATIC / 'v561.css').read_text())


class Cockpit(t530.Base):
    def jobs(self):
        rid = dr.submit(self.desk, 'Prépare une note de synthèse sur la recevabilité', '20240101001', 'note')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        self.desk.db.execute("UPDATE jobs SET status='done', finished=? WHERE kind='docrequest520'", (self.desk.now(),))
        piece = self.desk.enqueue('analyze_deadline450', {'matter': '20240101001', 'path': '/Dossiers/DEMO/Pièces/02_Constat huissier.pdf', 'etag': 'x'})
        self.desk.db.execute('INSERT INTO work_items(mail_key,state,matter,subject,sender) VALUES(?,?,?,?,?)',
                             ('k' * 64, 'draft_ready', '2024020201', 'Re: Audience du 5 octobre', 'confrere@example.test'))
        mail = self.desk.enqueue('prepare_reply', {'key': 'k' * 64, 'matter': '2024020201'})
        self.desk.db.commit()
        doc = self.desk.db.execute("SELECT id FROM jobs WHERE kind='docrequest520'").fetchone()['id']
        return doc, piece, mail

    def test_page_backdrop_is_a_plain_layer(self):
        body = self.request('/aujourdhui')['body']
        self.assertIn('<div class="c530-backdrop" data-close="1" aria-hidden="true"></div>', body)
        self.assertNotIn('<button type="button" class="c530-backdrop"', body)

    def test_activity_names_what_is_processed_and_opens(self):
        doc, piece, mail = self.jobs()
        f = ck.feed(self.desk)
        running = {x['job']: x for x in f['en_cours']}
        self.assertIn('02_Constat huissier.pdf', running[piece]['matter'])
        self.assertIn('« Re: Audience du 5 octobre »', running[mail]['matter'])
        done = {x['job']: x for x in f['fait']}
        self.assertIn('« Prépare une note de synthèse sur la recevabilité »', done[doc]['matter'])
        html = ck.feed_html(self.desk, '/agent-courriel')
        self.assertIn('data-job="%d"' % piece, html)
        self.assertIn('<button type="button" class="c561-open" data-job="%d"' % piece, html)

    def test_job_detail_shows_document_piece_and_links(self):
        doc, piece, mail = self.jobs()
        html = jobview561.job_html(self.desk, '/agent-courriel', doc)
        self.assertIn('Prépare une note de synthèse sur la recevabilité', html)
        self.assertIn('Document produit', html)
        self.assertIn('/agent-courriel/documents/edit?path=', html)
        self.assertIn('data-open-path="/Dossiers/DEMO/', html)
        self.assertIn('/agent-courriel/fiche?matter=20240101001', html)
        html = jobview561.job_html(self.desk, '/agent-courriel', piece)
        self.assertIn('<strong>02_Constat huissier.pdf</strong>', html)
        self.assertIn('Pièces', html)
        self.assertNotIn('etag', html)
        html = jobview561.job_html(self.desk, '/agent-courriel', mail)
        self.assertIn('« Re: Audience du 5 octobre » — confrere@example.test', html)
        self.desk.db.execute("UPDATE jobs SET status='error', result=? WHERE id=?", (json.dumps({'erreur': 'generation_ia_delai_depasse'}), mail))
        self.desk.db.commit()
        html = jobview561.job_html(self.desk, '/agent-courriel', mail)
        self.assertIn('data-retry="%d"' % mail, html)
        self.assertIn('n’a pas répondu à temps', html)
        with self.assertRaises(Stop):
            jobview561.job_html(self.desk, '', 999999)
        r = self.request('/api440/m530/job', query='id=%d&prefix=/agent-courriel' % piece)
        self.assertIn('02_Constat huissier.pdf', json.loads(r['body'])['html'])

    def test_open_in_nextcloud_only_for_files_of_the_matters(self):
        with patch('agent.dav.DAV.file_web_url', return_value='https://cloud.example.test/index.php/f/7'):
            self.docs.file_web_url = lambda p: 'https://cloud.example.test/index.php/f/7'
            out = jobview561.open_url(self.desk, '/Dossiers/DEMO/Pièces/02_Constat huissier.pdf', '20240101001')
            self.assertEqual(out['url'], 'https://cloud.example.test/index.php/f/7')
            for path in ('/Autre/secret.pdf', '/Dossiers/DEMO/../AUTRE/x.pdf'):
                with self.assertRaises(Stop):
                    jobview561.open_url(self.desk, path, '20240101001')

    def test_fragments_use_the_interface_prefix_but_never_an_arbitrary_one(self):
        self.drafts['items'] = [{'uid': '12', 'subject': 'Re: Pièces', 'to': 'client@example.test', 'date': self.desk.now(), 'agent': True, 'agent_key': ''}]
        with patch('agent.drafts440.get_draft', return_value={'body': 'Madame', 'to': 'client@example.test'}):
            item = ck.review_items(self.desk)[0]['id']
            html = ck.handle(self.desk, 'm530/item', {}, 'GET', {'id': item, 'prefix': '/agent-courriel'})['html']
            self.assertIn('href="/agent-courriel/courriels"', html)
            html = ck.handle(self.desk, 'm530/item', {}, 'GET', {'id': item, 'prefix': 'javascript:alert(1)//'})['html']
            self.assertIn('href="/courriels"', html)
        js = (STATIC / 'v530.js').read_text(encoding='utf-8')
        self.assertIn("'&prefix=' + encodeURIComponent(prefix)", js)
        self.assertIn("call('m530/job?id='", js)
        self.assertIn('data-open-path', js)


class SendButton(unittest.TestCase):
    def test_send_saves_first_instead_of_refusing(self):
        js = (STATIC / 'v490.js').read_text(encoding='utf-8')
        self.assertNotIn('Enregistrez d’abord vos modifications', js)
        self.assertIn('Les enregistrer maintenant, puis préparer l’envoi ?', js)
        self.assertIn('saveBtn.click()', js)
        self.assertIn('pend.subject === snapshot.subject', js)


class Recette(unittest.TestCase):
    def test_reasons_are_kept_for_each_failure(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('recette', ROOT / 'recette.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = ('..F\n' + '=' * 70 + '\nFAIL: test_a (test_x.T.test_a)\n' + '-' * 70 + '\nTraceback (most recent call last):\n  File "x.py", line 1\n'
               'AssertionError: 5 != 4\n\n' + '=' * 70 + '\nERROR: test_b (test_y.T.test_b)\n' + '-' * 70 + '\nTraceback:\nPermissionError: [Errno 13] refus\n\n'
               + '-' * 70 + '\nRan 3 tests in 0.1s\n\nFAILED (failures=1, errors=1)\n')
        self.assertEqual(mod.reasons(out), {'test_a (test_x.T.test_a)': 'AssertionError: 5 != 4',
                                            'test_b (test_y.T.test_b)': 'PermissionError: [Errno 13] refus'})
        self.assertEqual(mod.parse(out)[0], 'echec')


class Queue(t530.Base):
    def test_old_automatic_work_is_not_starved_but_never_passes_the_lawyer(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        self.desk.db.execute('DELETE FROM jobs')
        ids = {}
        for name, prio, created in (('ancien_auto', 90, old), ('controle_recent', 55, None), ('avocat', 0, None)):
            ids[name] = self.desk.enqueue('health', {'n': name}, priority=prio)
            if created:
                self.desk.db.execute('UPDATE jobs SET created=? WHERE id=?', (created, ids[name]))
        self.desk.db.commit()
        order = [r['id'] for r in self.desk.db.execute("SELECT id FROM jobs WHERE status='pending' ORDER BY " + PICK_ORDER)]
        self.assertEqual(order, [ids['avocat'], ids['ancien_auto'], ids['controle_recent']])


class Install(unittest.TestCase):
    def test_timers_are_reenabled_and_hint_is_shown(self):
        src = (ROOT / 'install-interface.py').read_text(encoding='utf-8')
        self.assertIn("('axiorhub-mail-agent.timer','axiorhub-mail-agent-cleanup.timer')", src)
        self.assertIn("'enable','--now',*timers", src)
        self.assertIn('sudo systemctl enable --now axiorhub-mail-agent.timer axiorhub-mail-agent-cleanup.timer', (ROOT / 'agent' / 'web520.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

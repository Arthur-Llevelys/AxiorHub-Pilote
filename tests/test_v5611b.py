"""5.6.11 (suite) : lot « fiabilité » de l'audit — facturation exacte et vérifiée, index complet et classé, interruption vocale,
analyses différées durables, diagnostic à niveaux, icônes PWA, expéditeur inconnu sans dossier, document introuvable, accueil."""
from pathlib import Path
import unittest
from unittest.mock import patch

from agent.common import Stop
import test_v530 as t530
import test_v569 as t569

ROOT = Path(__file__).resolve().parents[1]


class FakeNinja:
    """Double Invoice Ninja : la relecture GET renvoie ce que la création a reçu (ou un écart volontaire)."""
    def __init__(self, fail_post=False, mismatch=False):
        self.calls = [];self.fail_post = fail_post;self.mismatch = mismatch;self.headers = {};self.last = None

    def json(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method == 'POST' and self.fail_post:
            raise Stop('http_500')
        if method == 'POST':
            self.last = data
            return {'data': {'id': 'INV1' if path.endswith('invoices') else 'TSK1', 'number': '2026-0042'}}
        if self.mismatch:
            return {'data': {'id': 'AUTRE', 'client_id': 'X', 'amount': 0, 'line_items': []}}
        if path.startswith('/api/v1/invoices/'):
            return {'data': {'id': 'INV1', 'number': '2026-0042', 'status_id': '1', 'client_id': self.last['client_id'],
                             'line_items': [{'cost': i['cost'], 'quantity': i['quantity']} for i in self.last['line_items']]}}
        return {'data': {'id': 'TSK1', 'client_id': self.last['client_id'], 'description': self.last['description']}}


class Facturation(t530.Base):
    def setUp(self):
        super().setUp()
        from agent import time500, workstation
        self.desk.c['invoice_ninja'] = {'enabled': True, 'write_enabled': True, 'base_url': 'https://factures.example.test', 'api_token_file': '/nonexistent/token'}
        workstation.link_invoice_client(self.desk, '20240101001', 'CLT42')
        time500.set_terms(self.desk, '20240101001', 'horaire', rate='300')
        self.entry = time500.add_manual(self.desk, '20240101001', '2026-10-05', 10, 'Appel au greffe', None)['entry_id']   # 10 min à 300 €/h = 50 €

    def test_amounts_are_exact_cents_not_rounded_hours(self):
        from agent import facturation569 as fac
        http = FakeNinja()
        out = fac.draft_invoice(self.desk, '20240101001', confirm='yes', http=http)
        line = http.last['line_items'][0]
        self.assertEqual((line['quantity'], line['cost']), (1, 50.0));self.assertIn('0 h 10 à 300,00 €/h', line['notes'])
        self.assertTrue(out['verified']);self.assertEqual(out['amount_ht'], 50.0);self.assertIn('relue', out['message'])
        self.assertEqual(fac.writes(self.desk, '20240101001')[0]['state'], 'verified')

    def test_remote_mismatch_is_deposited_but_not_verified(self):
        from agent import facturation569 as fac
        out = fac.draft_invoice(self.desk, '20240101001', confirm='yes', http=FakeNinja(mismatch=True))
        self.assertFalse(out['verified']);self.assertIn('identifiant différent', out['issues']);self.assertIn('statut absent', out['issues'])
        self.assertIn('conformité non vérifiée', out['message'])
        self.assertEqual(fac.writes(self.desk, '20240101001')[0]['state'], 'deposited_unverified')
        self.assertIn('déposée, conformité non vérifiée', fac.section_html(self.desk, '20240101001', '/p'))
        self.assertEqual(fac.billable_entries(self.desk, '20240101001'), [])   # réservé jusqu'à vérification

    def test_reservation_is_atomic_and_never_replaced(self):
        from agent import facturation569 as fac
        fac.ensure_schema(self.desk)
        fac._reserve(self.desk, 'k1', 'invoice', '20240101001', 'CLT42', 'entries:1', {'entries': [self.entry]})
        with self.assertRaisesRegex(Stop, 'facture_deja_creee'):
            fac._reserve(self.desk, 'k1', 'invoice', '20240101001', 'CLT42', 'entries:1', {'entries': [self.entry]})
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM invoice_ninja_writes_v569 WHERE id='k1'").fetchone()[0], 1)
        self.assertNotIn('INSERT OR REPLACE', (ROOT / 'agent' / 'facturation569.py').read_text(encoding='utf-8'))

    def test_task_content_is_checked_too(self):
        from agent import facturation569 as fac, time500
        out = fac.log_time(self.desk, self.entry, confirm=True, http=FakeNinja())
        self.assertTrue(out['verified'])
        other = time500.add_manual(self.desk, '20240101001', '2026-10-06', 30, 'Lecture', None)['entry_id']
        out = fac.log_time(self.desk, other, confirm=True, http=FakeNinja(mismatch=True))
        self.assertFalse(out['verified']);self.assertIn('identifiant différent', out['issues'])


class Audit(t530.Base):
    def test_index_keeps_long_documents_and_ranks_before_limiting(self):
        from agent.rag import chunks, CHUNK_LIMIT
        text = ('Lorem ipsum dolor sit amet. ' * 120 + chr(10)) * 300 + 'MARQUEUR-FINAL-UNIQUE'
        parts = chunks(text)
        self.assertGreater(len(parts), 200);self.assertLessEqual(len(parts), CHUNK_LIMIT);self.assertIn('MARQUEUR-FINAL-UNIQUE', parts[-1])
        src = (ROOT / 'agent' / 'index.py').read_text(encoding='utf-8')
        self.assertIn('ORDER BY bm25(knowledge_fts) LIMIT 60', src);self.assertIn("meta['coverage']='partial'", src)

    def test_voice_interruption_resolves_pending_playback(self):
        js = (ROOT / 'agent' / 'static' / 'v568.js').read_text(encoding='utf-8')
        self.assertIn('playResolve=resolve', js);self.assertIn('const r=playResolve;playResolve=null;if(r)r();', js)

    def test_deferred_analyses_are_kept_and_resumed(self):
        from agent import economie569 as eco
        eco.save(self.desk, {'enabled': True, 'daily_jobs': 1})
        self.desk.db.execute('DELETE FROM jobs');self.desk.db.commit()
        self.assertTrue(self.desk._then('refresh_brief', {'matter': '20240101001'}))
        self.assertIsNone(self.desk._then('sync_legal_memory', {'matter': '20240101001'}))
        rows = eco.deferred(self.desk)
        self.assertEqual([r['kind'] for r in rows], ['sync_legal_memory']);self.assertEqual(rows[0]['reason'], 'quota_econome_journalier')
        self.assertIsNone(self.desk._then('sync_legal_memory', {'matter': '20240101001'}));self.assertEqual(len(eco.deferred(self.desk)), 1)   # dédupliquée
        self.desk.db.execute("UPDATE deferred_jobs569 SET resume_after='2000-01-01T00:00:00+00:00'");self.desk.db.commit()
        self.assertEqual(eco.resume_deferred(self.desk), 0)                           # quota du jour encore atteint : attend
        eco.save(self.desk, {'enabled': True, 'daily_jobs': 10})
        self.assertEqual(eco.resume_deferred(self.desk), 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='sync_legal_memory'").fetchone()[0], 1)
        self.assertEqual(eco.deferred(self.desk), [])
        self.assertIn('différée(s)', eco.section_html(self.desk, '/p'))

    def test_readiness_distinguishes_configured_accessible_authenticated(self):
        from agent import readiness569
        self.desk.c['audio'] = {'enabled': True, 'bridge_base_url': 'http://127.0.0.1:9011'}
        self.desk.c['google_calendar568'] = {'client_id': 'id', 'client_secret_file': '/x', 'redirect_uri': 'https://agent.example.test/api440/m568/google/callback'}

        class Http:
            def __init__(self, *a, **k): pass

            def request(self, *a, **k): raise Stop('http_401')
        with patch('agent.config567.test', lambda desk, data: {'ok': True, 'message': 'ok', 'writes': 0}), patch('agent.readiness569.HTTP', Http):
            report = readiness569.run(self.desk)
        by = {x['key']: x for x in report['checks']}
        self.assertIsNone(by['dictation']['ok']);self.assertIn('non authentifié', by['dictation']['level'])
        self.assertIsNone(by['google']['ok']);self.assertIn('non autorisé', by['google']['level'])
        self.assertIn('(accessible, non authentifié)', readiness569.text(report))
        self.assertTrue(all('level' in x for x in report['checks']))

    def test_pwa_icons_match_their_declared_sizes(self):
        from PIL import Image
        from agent.mobile490 import manifest
        icons = manifest('/p')['icons']
        self.assertEqual([i['sizes'] for i in icons], ['192x192', '512x512', '512x512']);self.assertEqual(icons[2]['purpose'], 'maskable')
        for icon in icons:
            name = icon['src'].rsplit('/', 1)[-1];size = int(icon['sizes'].split('x')[0])
            self.assertEqual(Image.open(ROOT / 'agent' / 'static' / name).size, (size, size), name)
        self.assertIn("'/static/axiorhub-icon-maskable.png'", (ROOT / 'agent' / 'web.py').read_text(encoding='utf-8'))

    def test_upgrade_message_is_generated_from_the_supported_list(self):
        src = (ROOT / 'upgrade.py').read_text(encoding='utf-8')
        self.assertNotIn('entre 3.5.0 et 5.6.7', src);self.assertIn('SUPPORTED_PREVIOUS[-1]', src)

    def test_decisions_block_updates_without_reload(self):
        js = (ROOT / 'agent' / 'static' / 'v569.js').read_text(encoding='utf-8')
        self.assertIn('box.remove()', js);self.assertEqual(js.count('location.reload'), 1)   # seule la mise en service recharge

    def test_unknown_sender_without_matter_is_reviewed_not_drafted(self):
        from agent.engine import known_correspondent
        from agent.common import load_matters, private_json
        matters = load_matters(self.desk.c);matters[0]['correspondents'] = [{'email': 'Greffe@Example.Test', 'role': 'juridiction'}]
        private_json(Path(self.desk.c['matters_file']), matters)
        self.desk.c['mail']['from_address'] = 'contact@example.test'
        self.assertTrue(known_correspondent(self.desk.c, 'greffe@example.test'))
        self.assertTrue(known_correspondent(self.desk.c, 'contact@example.test'))
        self.assertFalse(known_correspondent(self.desk.c, 'promo@example.test'));self.assertFalse(known_correspondent(self.desk.c, ''))
        src = (ROOT / 'agent' / 'engine.py').read_text(encoding='utf-8')
        self.assertIn("finish('review', 'expediteur_inconnu_sans_dossier')", src)
        from agent.web import REASONS
        self.assertIn('expediteur_inconnu_sans_dossier', REASONS)

    def test_missing_document_is_explained_in_the_drawer(self):
        from agent import cockpit530 as ck, docrequest520 as dr
        rid = dr.submit(self.desk, 'Prépare une note de synthèse', '20240101001', 'note')['request']
        dr.perform(self.desk, 'docrequest520', {'request': rid})
        item = next(x for x in ck.review_items(self.desk) if x['type'] == 'Note')

        class Gone:
            def stat(self, path): raise Stop('http_404')

            def download(self, meta): raise Stop('http_404')
        with patch('agent.document_projects._dav', return_value=Gone()):
            html = ck.item_html(self.desk, '/p', item['id'])
        self.assertIn('n’est plus à cet emplacement dans Nextcloud', html);self.assertNotIn('http 404', html);self.assertIn('data-act="ecarte"', html)


class Reception5611(t569.ReceptionBase):
    def test_notice_is_spoken_before_any_input_and_zero_works_at_every_step(self):
        body = self.phone({'CallSid': 'CA9000000009', 'From': '0612345678'})['body']
        self.assertLess(body.index('intelligence artificielle'), body.index('<Gather'))        # annonce hors du Gather
        self.assertIn('</Say><Gather', body)
        step2 = self.phone({'CallSid': 'CA9000000009', 'From': '0612345678', 'Digits': '0'}, 'step=2&intent=callback')
        self.assertIn('input="dtmf"', step2['body']);self.assertNotIn('speech', step2['body'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM reception_v567').fetchone()[0], 0)

    def test_retention_is_applied_daily_including_task_titles(self):
        from agent import reception567
        self.phone({'CallSid': 'CA9100000001', 'From': '0612345678', 'SpeechResult': 'Durand'}, 'step=2&intent=callback')
        self.desk.db.execute("UPDATE reception_v567 SET at='2020-01-01T00:00:00+00:00'");self.desk.db.commit()
        self.assertEqual(reception567.purge(self.desk), 1)
        row = self.desk.db.execute('SELECT caller,text FROM reception_v567').fetchone()
        self.assertEqual(row['caller'], '');self.assertEqual(row['text'], '')
        title = self.desk.db.execute("SELECT title FROM tasks WHERE title LIKE 'Demande de rappel%'").fetchone()['title']
        self.assertIn('numéro purgé', title);self.assertNotIn('0612345678', title)
        self.assertIn('purge(desk)', (ROOT / 'agent' / 'operations.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

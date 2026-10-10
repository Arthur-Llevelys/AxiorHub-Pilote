"""5.6.24 : corrections de l'audit des 5.6.22 et 5.6.23 (F01 à F37) et demandes d'interface (dossier de travail à choisir, icônes
d'Aujourd'hui, onglets du dossier, agenda en français). Chaque classe reprend le scénario de reproduction de l'audit."""
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from agent import controle5614, facturation5614, mcp5614, taches5614
from agent.common import Stop
from agent.dav import DAV, LocalFolder
from agent.standalone_auth import allowed
import test_v460 as t460
import test_v530 as t530
import test_v5614 as t5614

ROOT = Path(__file__).resolve().parents[1]
ALPHA = '20240101001'


def src(path):
    return (ROOT / path).read_text(encoding='utf-8')


# ================================================================ navigation, agenda, MCP, brouillons
class Navigation(unittest.TestCase):
    def test_f35_no_job_zero_in_redirects(self):
        web = src('agent/web.py')
        self.assertNotIn("target='/?job='+str(job)", web)
        self.assertNotIn(",'job':job}", web)
        self.assertIn("in ('', '0')", web)

    def test_f36_single_load_and_idempotent_commands(self):
        self.assertNotIn('static/v5614.js', src('agent/missions5614_ui.py'))
        js = src('agent/static/v5614.js')
        self.assertIn('window.__axiorhub5614', js); self.assertIn('inflight.has(flight)', js); self.assertIn('inflight.delete(flight)', js)

    def test_f37_only_declared_json_forms_are_intercepted(self):
        self.assertIn("closest('form.m5-form[data-api]')", src('agent/static/v500.js'))

    def test_agenda_days_in_french(self):
        from agent import agenda36
        self.assertEqual(agenda36.JOURS[0], 'Lundi'); self.assertEqual(agenda36.JOURS[6], 'Dimanche')
        self.assertNotIn("strftime('%A", src('agent/agenda36.py'))


class MCP(unittest.TestCase):
    def test_f23_sse_notifications_are_not_taken_for_the_response(self):
        raw = ('event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
               'event: message\ndata: {"jsonrpc":"2.0",\ndata: "id":7,"result":{"ok":true}}\n\n')
        self.assertEqual(mcp5614.correlate(raw, 7)['result'], {'ok': True})
        with self.assertRaisesRegex(Stop, 'non_correlee'):
            mcp5614.correlate('{"jsonrpc":"2.0","id":8,"result":{}}', 7)
        with self.assertRaisesRegex(Stop, 'sans_reponse'):
            mcp5614.correlate('data: {"jsonrpc":"2.0","method":"notifications/x"}\n\n', 7)

    def test_f23_repeated_cursor_is_an_incomplete_catalog(self):
        client = mcp5614.Session.__new__(mcp5614.Session)
        client.call = lambda method, params: {'tools': [{'name': 'a'}], 'nextCursor': 'c1'}
        with self.assertRaisesRegex(Stop, 'pagination_outils_mcp_incomplete'):
            client.list_tools()


class Drafts(unittest.TestCase):
    def test_f21_missing_flag_and_empty_body_are_not_conformant(self):
        from agent import maildraft5613
        from email.message import EmailMessage
        expected = EmailMessage(); expected['Subject'] = 'Objet'; expected['From'] = 'a@example.test'; expected.set_content('Bonjour Maître')
        saved = types.SimpleNamespace(subject='Objet', flags=(), text='', uid='1', mid='<m@x>', msg=expected)
        with patch('agent.maildraft5613.find_existing', return_value=[saved]):
            with self.assertRaisesRegex(Stop, 'drapeau_draft_corps_vide'):
                maildraft5613.verify(None, 'Drafts', '<m@x>', 'Objet', expected)


# ================================================================ droits (F18)
class Rights(unittest.TestCase):
    def test_f18_lawyer_can_use_missions_decisions_and_billing(self):
        for api in ('m568/mission5614/create', 'm568/mission5614/control', 'm568/mission5614/decide', 'm568/decision/defer', 'm568/ninja/quote',
                    'm568/ninja/client', 'm568/ninja/project', 'm568/profils/approve', 'fiche/analyse', 'm530/event/create', 'm530/item/ignore'):
            self.assertTrue(allowed('avocat', 'POST', '/api440/' + api), api)
        for api in ('m568/mission5614/create', 'm568/ninja/quote', 'm568/profils/approve', 'm567/config/save'):
            self.assertFalse(allowed('assistant', 'POST', '/api440/' + api), api)
        self.assertFalse(allowed('avocat', 'POST', '/api440/m567/config/save'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m530/event/create'))


# ================================================================ dossier local (F10 à F13)
def lcfg(root, **extra):
    return {'local_path': str(root), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers'], 'max_depth': 8, 'max_files': 500, 'max_file_bytes': 15_000_000, **extra}


class Local(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'Dossiers' / 'ALPHA' / 'secrets').mkdir(parents=True)
        (self.root / 'Dossiers' / 'ALPHA' / 'secrets' / 'mdp.txt').write_text('secret')
        self.client = DAV(lcfg(self.root))

    def test_f10_creation_never_replaces_a_file_that_appeared_meanwhile(self):
        target = self.root / 'Dossiers' / 'ALPHA' / 'Projet.docx'
        original = LocalFolder._tmp

        def racing(self_, directory, data):
            target.write_bytes(b'version de l avocat')   # écriture concurrente entre contrôle et publication
            return original(self_, directory, data)
        with patch.object(LocalFolder, '_tmp', racing):
            with self.assertRaisesRegex(Stop, 'http_412'):
                self.client.put_file('/Dossiers/ALPHA/Projet.docx', b'projet de l agent')
        self.assertEqual(target.read_bytes(), b'version de l avocat')
        self.assertFalse([p for p in target.parent.iterdir() if p.name.startswith('.axiorhub-')])

    def test_f11_rapid_replacements_keep_every_previous_version(self):
        path = '/Dossiers/ALPHA/Acte.docx'
        self.client.put_file(path, b'v1')
        for new in (b'v2', b'v3', b'v4'):
            self.client.replace_file(path, new, self.client.stat(path)['etag'])
        versions = sorted(p.read_bytes() for p in (self.root / 'Dossiers' / 'ALPHA' / '.axiorhub-versions').iterdir())
        self.assertEqual(versions, [b'v1', b'v2', b'v3'])
        keep = DAV(lcfg(self.root, local_versions_keep=2))
        keep.replace_file(path, b'v5', keep.stat(path)['etag'])
        self.assertEqual(len(list((self.root / 'Dossiers' / 'ALPHA' / '.axiorhub-versions').iterdir())), 2)

    def test_f12_symlink_alias_refused_for_direct_access(self):
        alias = self.root / 'Dossiers' / 'ALPHA' / 'alias'
        try:
            os.symlink(str(self.root / 'Dossiers' / 'ALPHA' / 'secrets'), str(alias))
        except (OSError, NotImplementedError):
            self.skipTest('liens symboliques non disponibles')
        for call in (lambda: self.client.stat('/Dossiers/ALPHA/alias/mdp.txt'),
                     lambda: self.client.download({'path': '/Dossiers/ALPHA/alias/mdp.txt'}),
                     lambda: self.client.put_file('/Dossiers/ALPHA/alias/x.txt', b'x'),
                     lambda: self.client.list_folder('/Dossiers/ALPHA/alias')):
            with self.assertRaisesRegex(Stop, 'lien_symbolique_refuse'):
                call()

    def test_f13_same_size_same_date_change_is_detected(self):
        path = '/Dossiers/ALPHA/Note.txt'
        self.client.put_file(path, b'AAAA')
        meta = self.client.stat(path)
        target = self.root / 'Dossiers' / 'ALPHA' / 'Note.txt'
        st = target.stat()
        target.write_bytes(b'BBBB'); os.utime(str(target), (st.st_atime, st.st_mtime))
        self.assertNotEqual(DAV(lcfg(self.root)).stat(path)['etag'], meta['etag'])
        with self.assertRaisesRegex(Stop, 'http_412'):
            DAV(lcfg(self.root)).download(meta)


# ================================================================ Invoice Ninja (F14 à F17)
class Billing(t5614.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['invoice_ninja'] = {'enabled': True, 'write_enabled': True, 'base_url': 'https://factures.example.test', 'api_token_file': '/x', 'company_id': 'c1'}
        self.http = t5614.FakeNinjaHTTP()

    def test_f14_invoiced_time_is_never_exported_as_a_task(self):
        from agent import facturation569, time500, workstation
        workstation.link_invoice_client(self.desk, ALPHA, 'c1')
        time500.set_terms(self.desk, ALPHA, 'horaire', rate='250')
        entry = time500.add_manual(self.desk, ALPHA, '2026-10-05', 60, 'Rédaction', None)['entry_id']
        facturation569.draft_invoice(self.desk, ALPHA, confirm='yes', http=self.http)
        posts = len([c for c in self.http.calls if c[0] == 'POST'])
        with self.assertRaises(Stop):
            facturation569.log_time(self.desk, entry, confirm=True, http=self.http)
        self.assertEqual(len([c for c in self.http.calls if c[0] == 'POST']), posts)          # aucun POST après le conflit

    def test_f14_task_without_time_log_readback_is_not_verified(self):
        from agent import facturation569, time500, workstation
        workstation.link_invoice_client(self.desk, ALPHA, 'c1')
        time500.set_terms(self.desk, ALPHA, 'horaire', rate='250')
        entry = time500.add_manual(self.desk, ALPHA, '2026-10-06', 30, 'Lecture', None)['entry_id']
        real = self.http.json

        def stripped(method, path, data=None):
            out = real(method, path, data)
            if method == 'GET' and '/tasks/' in path:
                out = {'data': {k: v for k, v in out['data'].items() if k != 'time_log'}}
            return out
        self.http.json = stripped
        res = facturation569.log_time(self.desk, entry, confirm=True, http=self.http)
        self.assertFalse(res['verified']); self.assertIn('time_log absent', res['issues'])

    def test_f15_total_label_and_status_are_required(self):
        body = {'line_items': [{'notes': 'Consultation', 'quantity': 1, 'cost': 100, 'tax_rate1': 20}]}
        issues = facturation5614.check_lines({'client_id': 'c1', 'amount': 999, 'line_items': [{'quantity': 1, 'cost': 100, 'tax_rate1': 20}]}, body, 'c1', '100')
        self.assertIn('ligne 1 : libellé absent', issues)
        self.assertTrue(any(i.startswith('montant total différent') for i in issues))
        ok = facturation5614.check_lines({'client_id': 'c1', 'amount': 120, 'line_items': [{'notes': 'Consultation', 'quantity': 1, 'cost': 100, 'tax_rate1': 20}]}, body, 'c1', '100')
        self.assertEqual(ok, [])

    def test_f16_validity_and_terms_are_applied_and_distinguished(self):
        facturation5614.ensure_client(self.desk, ALPHA, {'name': 'ALPHA SAS'}, confirm=True, http=self.http)
        items = [{'label': 'Consultation', 'quantity': 1, 'cost': '100'}]
        a = facturation5614.draft_quote(self.desk, ALPHA, items, confirm=True, terms='Conditions A', validity_days=7, http=self.http)
        b = facturation5614.draft_quote(self.desk, ALPHA, items, confirm=True, terms='Conditions B', validity_days=90, http=self.http)
        self.assertNotEqual(a['quote_id'], b['quote_id'])
        today = facturation5614.office_today(self.desk)
        self.assertEqual(self.http.store['quotes'][a['quote_id']]['due_date'], (today + timedelta(days=7)).isoformat())
        self.assertEqual(self.http.store['quotes'][b['quote_id']]['terms'], 'Conditions B')
        self.assertTrue(a['verified'], a['issues'])

    def test_f17_operation_claimed_elsewhere_never_posts_twice(self):
        facturation5614.ensure_schema(self.desk)
        client = facturation5614.InvoiceNinjaClient(self.desk, self.http, write=True)
        body = {'name': 'BRAVO', 'private_notes': 'AxiorHub:matter:X'}
        key = facturation5614.digest('op5614|%s|%s|%s|%s' % (client.company, 'client', 'loc1', 'create'))[:32]
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
        self.desk.db.execute('INSERT INTO invoice_ninja_ops5614 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (key, client.company, 'client', 'loc1', 'create', 'en_cours', facturation5614.digest(payload), payload, '', '{}', 'x', 'x'))
        self.desk.db.commit()
        with self.assertRaisesRegex(Stop, 'operation_incertaine'):
            client.create('client', body, 'loc1')
        self.assertFalse([c for c in self.http.calls if c[0] == 'POST'])
        text = src('agent/facturation5614.py')
        self.assertLess(text.index("self.desk.db.execute('BEGIN IMMEDIATE')\n            row = self.desk.db.execute('SELECT * FROM invoice_ninja_ops5614"), text.index("created = self._data(self.call('POST'"))


# ================================================================ missions (F01 à F09)
class Missions(t5614.Base):
    def test_f04_same_key_other_payload_is_a_conflict(self):
        m = self.mission(key='f4')
        again = self.mission(key='f4')
        self.assertEqual(again['id'], m['id'])
        with self.assertRaisesRegex(Stop, 'requete_reutilisee_avec_autres_donnees'):
            self.mission(key='f4', budget_calls=7)
        with self.assertRaisesRegex(Stop, 'requete_reutilisee_avec_autres_donnees'):
            self.mission(key='f4', autonomy='suggest')

    def test_f01_cancel_during_model_call_publishes_nothing(self):
        m = self.mission(key='f1')
        tid = m['tasks'][0]['id']
        inner = self.task_model

        class Cancelling:
            def complete(s, *a, **k):
                taches5614.control(self.desk, {'id': m['id'], 'action': 'cancel'})
                return inner.complete(*a, **k)
        self.task_model = Cancelling()
        out = taches5614.perform(self.desk, {'mission': m['id'], 'task': tid})
        self.assertIn('mandat_revoque', out.get('skipped', ''))
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM artifacts5614 WHERE task=? AND state='actif'", (tid,)).fetchone())
        self.assertEqual(taches5614.get(self.desk, m['id'])['state'], 'annulee')

    def test_f02_expired_lease_is_reclaimed_or_sent_to_decision(self):
        m = self.mission(key='f2')
        t = m['tasks'][1]
        self.desk.db.execute("UPDATE tasks5614 SET run_state='en_cours',lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (t['id'],))
        self.desk.db.execute("UPDATE jobs SET status='done' WHERE kind='task5614'"); self.desk.db.commit()
        taches5614.advance(self.desk, m['id'])
        self.assertIn(self.desk.db.execute('SELECT run_state FROM tasks5614 WHERE id=?', (t['id'],)).fetchone()[0], ('a_preparer', 'en_attente'))
        u = m['tasks'][2]
        self.desk.db.execute("UPDATE tasks5614 SET run_state='en_cours',lease_until='2000-01-01T00:00:00+00:00' WHERE id=?", (u['id'],))
        self.desk.db.execute("INSERT INTO ops5614 VALUES('op-x',?,?,'depot_word','en_cours','h','',?,'x','x')", (m['id'], u['id'], json.dumps({'path': '/Dossiers/DEMO/x.docx'})))
        self.desk.db.commit()
        taches5614.advance(self.desk, m['id'])
        self.assertEqual(self.desk.db.execute('SELECT run_state FROM tasks5614 WHERE id=?', (u['id'],)).fetchone()[0], 'en_erreur')
        self.assertIn('effet_incertain', [d['kind'] for d in taches5614.get(self.desk, m['id'])['decisions']])
        state, _ = taches5614.recover_job(self.desk, {'mission': m['id'], 'task': u['id']})
        self.assertEqual(state, 'error')

    def test_f03_f05_validation_rereads_the_file_and_analysis_reads_full_texts(self):
        m = self.run_all(self.mission(key='f3')['id'], rounds=12)
        self.assertEqual(m['state'], 'a_valider')
        analyses = [p for s, p in self.task_model.calls if any(isinstance(e, dict) and e.get('lecture') == 'integrale' for e in p.get('entrees', []))]
        self.assertTrue(analyses)                                                                    # F05 : textes intégraux transmis
        content = next(t for t in m['tasks'] if t['task_type'] == 'assemblage')['result']['content']
        with self.assertRaisesRegex(Stop, 'empreinte_requise'):
            taches5614.control(self.desk, {'id': m['id'], 'action': 'validate'})
        self.docs.files[content['path']] = self.docs.files[content['path']] + b' '
        with self.assertRaisesRegex(Stop, 'document_modifie_depuis_presentation'):
            taches5614.control(self.desk, {'id': m['id'], 'action': 'validate', 'sha256': content['sha256']})
        self.assertEqual(taches5614.get(self.desk, m['id'])['validation'], 'a_renouveler')

    def test_f06_large_matter_needs_a_scope_decision(self):
        self.desk.setting('missions5614:limits', {'max_read_files': 1})
        m = self.run_all(self.mission(key='f6')['id'], rounds=6)
        self.assertIn('perimetre_lecture', [d['kind'] for d in m['decisions']])

    def test_f07_legal_source_contract_is_complete_or_refused(self):
        from agent.legal_research import legal_source
        self.assertTrue(legal_source(t5614.VERIFIED_54))
        for field in ('authority_id', 'exact_excerpt', 'official_text_sha256', 'official_url'):
            self.assertIsNone(legal_source({**t5614.VERIFIED_54, field: ''}), field)
        self.assertIsNone(legal_source({**t5614.VERIFIED_54, 'citable': False, 'status': 'rejected'}))

    def test_f09_batches_are_recorded_and_all_sent_to_consolidation(self):
        m = self.mission(key='f9')
        self.desk.setting('missions5614:limits', {'max_payload_chars': 9000})
        task = {'id': 'x9', 'code': 'T12d', 'role': 'redacteur', 'instruction': 'Discussion', 'output_type': 'section', 'depends_on': []}
        mission = taches5614._mission_row(self.desk, m['id'], 'cabinet'); mission['answers'] = {}; mission['extra_instructions'] = []
        inputs = [{'origine': 'T03', 'type': 'extraits', 'texte': 'Fait %d. ' % i * 600} for i in range(4)]
        data, route, batches = taches5614._ask_batched(self.desk, mission, task, {'tache': 'T12d'}, inputs, taches5614.SECTION_SCHEMA, 'section')
        self.assertGreater(batches, 1)
        consolidation = [p for s, p in self.task_model.calls if p.get('consolidation')][-1]
        self.assertEqual(len(consolidation['resultats_par_lot']), batches)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM batches5614 WHERE task=?', ('x9',)).fetchone()[0], batches)


class Controls(unittest.TestCase):
    def test_f08_same_amount_other_actor_is_not_supported(self):
        sources = [{'id': 's1', 'text': 'La partie adverse demande la somme de 1 000 euros au titre des loyers.'}]
        det = controle5614.deterministic('Le client a payé 1 000 euros le 3 mars.', sources)
        self.assertEqual([a['status'] for a in det['assertions'] if a['kind'] == 'montant'], ['a_controler'])
        det = controle5614.deterministic('La partie adverse demande 1 000 euros au titre des loyers.', sources)
        self.assertEqual([a['status'] for a in det['assertions'] if a['kind'] == 'montant'], ['appuyee'])
        det = controle5614.deterministic('Le client ne demande pas 1 000 euros au titre des loyers.', sources)
        self.assertEqual([a['status'] for a in det['assertions'] if a['kind'] == 'montant'], ['a_controler'])


# ================================================================ Word (F22)
def docx(body):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', '<w:document xmlns:w="w" xmlns:r="r"><w:body>%s<w:sectPr/></w:body></w:document>' % body)
    return out.getvalue()


class Word(unittest.TestCase):
    def test_f22_image_paragraph_survives_a_neighbouring_revision(self):
        from agent import pilote5613
        image = '<w:p><w:r><w:drawing><a r:embed="rId5"/></w:drawing></w:r></w:p>'
        old = docx('<w:p><w:r><w:t>Premier paragraphe.</w:t></w:r></w:p>' + image + '<w:p><w:r><w:t>Second paragraphe.</w:t></w:r></w:p>')
        new_body = '<w:p><w:r><w:t>Premier paragraphe.</w:t></w:r></w:p><w:p><w:r><w:t>Second paragraphe modifié.</w:t></w:r></w:p>'
        data, report = pilote5613.revise_body(old, new_body)
        xml = zipfile.ZipFile(io.BytesIO(data)).read('word/document.xml').decode()
        self.assertIn('r:embed="rId5"', xml); self.assertIn('Second paragraphe modifié.', xml)
        mixed = '<w:p><w:r><w:t>Voir le schéma </w:t></w:r><w:r><w:drawing><a r:embed="rId9"/></w:drawing></w:r></w:p>'
        data, report = pilote5613.revise_body(docx(mixed), '<w:p><w:r><w:t>Voir le tableau.</w:t></w:r></w:p>')
        self.assertIn('rId9', zipfile.ZipFile(io.BytesIO(data)).read('word/document.xml').decode())
        self.assertEqual(report['objets_proteges'], 1); self.assertIn('à relire', report['summary'])


# ================================================================ dossier central (F25 à F34) et onglets
class Matter(t460.Base):
    def test_f26_f27_f28_scan_projects_dates_correction_and_refusal(self):
        from agent import facts460, fiche460, legal_memory as lm
        self.index_doc()
        res = facts460.scan_matter(self.desk, {'matter': 'DOS-001'})
        self.assertTrue(res['nouveaux']); self.assertIsNotNone(res['chronologie'])
        rows = lm.memory_records(self.desk, 'DOS-001')
        jug = next(r for r in rows if r['title'].startswith('Jugement du 5/03'))
        chrono = fiche460.chronologie(self.desk, 'DOS-001')
        self.assertTrue(any(r['title'] == jug['title'] for r in chrono))                         # F26 : projection faite par le même travail
        again = facts460.scan_matter(self.desk, {'matter': 'DOS-001'})
        self.assertEqual(again['nouveaux'], 0); self.assertTrue(again['inchanges'])               # F32 : compteurs persistés
        with self.assertRaisesRegex(Stop, 'fait_modifie_entre_temps'):
            facts460.decide(self.desk, 'DOS-001', jug['id'], 'validate', 'Jugement du 10 mars 2026', revision='999')
        facts460.decide(self.desk, 'DOS-001', jug['id'], 'validate', 'Jugement rendu le 10 mars 2026.', title='Jugement du 10/03/2026', event_date='2026-03-10', revision=str(jug['revision']))
        row = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['id'] == jug['id'])
        self.assertEqual((row['event_date'][:10], row['title'], row['status']), ('2026-03-10', 'Jugement du 10/03/2026', 'validated'))   # F27
        chrono = fiche460.chronologie(self.desk, 'DOS-001')
        self.assertTrue(any(r['title'] == 'Jugement du 10/03/2026' and r['at'][:10] == '2026-03-10' for r in chrono))
        self.assertFalse(any(r['title'] == jug['title'] for r in chrono))
        dated = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['title'].startswith('Contrat du'))
        facts460.decide(self.desk, 'DOS-001', dated['id'], 'reject')
        self.assertFalse(any(r['title'] == dated['title'] for r in fiche460.chronologie(self.desk, 'DOS-001')))   # F28

    def test_f29_distinct_mails_same_day_and_undated_last(self):
        from agent import fiche460, legal_memory as lm
        for sid in ('mail-1', 'mail-2'):
            lm._timeline_put(self.desk, 'DOS-001', 'email_received', '2026-09-25T09:00:00+00:00', 'Relance FIMEX', 'x', sid, 'email_received', 'INBOX', '')
        lm._timeline_put(self.desk, 'DOS-001', 'document', '', 'Pièce sans date', '', 'doc-x', 'document', '/Dossiers/DEMO/x.pdf', '')
        self.desk.db.commit()
        rows = fiche460.chronologie(self.desk, 'DOS-001')
        self.assertEqual(len([r for r in rows if r['title'] == 'Relance FIMEX']), 2)
        self.assertTrue(rows[-1]['undated']); self.assertEqual(rows[-1]['title'], 'Pièce sans date')

    def test_f33_changed_proof_suspends_the_validation(self):
        from agent import facts460, legal_memory as lm
        facts460.propose(self.desk, 'DOS-001', t460.PATH, t460.TEXT, '2026-09-20')
        rg = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['record_type'] == 'case_number')
        facts460.decide(self.desk, 'DOS-001', rg['id'], 'validate')
        facts460.propose(self.desk, 'DOS-001', t460.PATH, 'Autre en-tête\nRG 2026/001234\n' + t460.TEXT, '2026-09-28')
        row = next(r for r in lm.memory_records(self.desk, 'DOS-001') if r['id'] == rg['id'])
        self.assertEqual(row['status'], 'suggested'); self.assertIn('Preuve modifiée', row['validation_note'])
        history = self.desk.db.execute("SELECT reason,data FROM legal_memory_history WHERE record_id=? ORDER BY id DESC LIMIT 1", (rg['id'],)).fetchone()
        self.assertEqual(history['reason'], 'preuve_modifiee_validation_suspendue'); self.assertEqual(json.loads(history['data'])['status'], 'validated')

    def test_f25_analysis_is_grouped_followed_and_partial_when_a_step_fails(self):
        from agent import dossier5624
        a = dossier5624.request_analysis(self.desk, 'DOS-001')
        b = dossier5624.request_analysis(self.desk, 'DOS-001')
        self.assertEqual(a['job_id'], b['job_id']); self.assertTrue(b['already'])
        self.index_doc()
        with patch('agent.desk.Desk.perform', side_effect=Stop('nextcloud_injoignable')):
            out = dossier5624.analyse(self.desk, {'matter': 'DOS-001'})
        st = dossier5624.state(self.desk, 'DOS-001')
        steps = {s['step']: s['state'] for s in st['steps']}
        self.assertEqual(steps['indexation'], 'erreur'); self.assertEqual(steps['faits'], 'ok'); self.assertEqual(steps['chronologie'], 'ok')
        self.assertEqual(steps['synthese'], 'ignoree'); self.assertEqual(out['status'], 'partial'); self.assertIn('Reprendre', out['message'])


class MatterPages(t460.Pages):
    def test_f34_every_tab_renders_with_sources_or_an_explicit_state(self):
        from agent import dossier5624, facts460
        facts460.propose(self.desk, 'DOS-001', t460.PATH, t460.TEXT)
        first = self.call('/fiche', query='matter=DOS-001')['body'].decode()
        for key, label in dossier5624.RUBRIQUES:
            self.assertIn('onglet=' + key, first)
            self.assertIn(label, first)
        self.assertIn('Analyser le dossier', first); self.assertIn('fc-validate', first)
        for key, label in dossier5624.RUBRIQUES:
            r = self.call('/fiche', query='matter=DOS-001&onglet=' + key)
            self.assertTrue(r['status'].startswith('200'), key)
            self.assertIn('aria-current="page">' + label, r['body'].decode())
        parties = self.call('/fiche', query='matter=DOS-001&onglet=parties')['body'].decode()
        self.assertIn('SAS FIMEX', parties)
        faits = self.call('/fiche', query='matter=DOS-001&onglet=faits')['body'].decode()
        self.assertIn('Rappel des faits', faits); self.assertIn('Rappel de la procédure', faits); self.assertIn('fc-edit-date', faits)
        r, out = self.post('fiche/etat', {'matter': 'DOS-001'})
        self.assertIn('steps', out)


# ================================================================ interface : Paramètres et Aujourd'hui
class Interface(t530.Base):
    def test_folder_picker_lists_directories_only(self):
        from agent import config567
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'Dossiers').mkdir(); (Path(tmp) / '.cache').mkdir(); (Path(tmp) / 'note.txt').write_text('x')
            out = config567.browse({'path': tmp})
            self.assertEqual([d['name'] for d in out['dirs']], ['Dossiers']); self.assertTrue(out['parent'])
        with self.assertRaises(Stop):
            config567.browse({'path': 'relatif'})
        self.assertIn('data-browse567', src('agent/web567.py')); self.assertIn("api('config/browse'", src('agent/static/v567.js'))

    def test_today_icons_and_routes(self):
        from agent import cockpit530 as ck
        html = ck.day_html(self.desk, '/p')
        self.assertIn('data-day-add="event"', html); self.assertIn('data-day-add="task"', html); self.assertIn('Ajouter une tâche', html)
        with self.assertRaisesRegex(Stop, 'liste_taches_non_configuree'):
            ck.handle(self.desk, 'm530/task/create', {'title': 'Appeler le greffe'})
        with patch('agent.workplan.create_event', return_value={'message': 'Événement créé.'}) as created:
            self.assertEqual(ck.handle(self.desk, 'm530/event/create', {'title': 'Audience', 'start': '2026-10-12T09:00'})['message'], 'Événement créé.')
        self.assertEqual(created.call_args[0][1]['title'], 'Audience')
        with self.assertRaisesRegex(Stop, 'intitule_invalide'):
            ck.handle(self.desk, 'm530/event/create', {'title': 'x', 'start': '2026-10-12T09:00'})
        self.assertIn('data-ignore', ck._review_icons({'id': 'doc:1', 'title': 'x'}))
        self.assertIn('data-trash', ck._review_icons({'id': 'doc:1', 'title': 'x', 'request': 'r', 'path': '/a.docx'}))
        js = src('agent/static/v530.js')
        for hook in ('m530/item/ignore', 'm530/item/trash', 'm530/dismiss', 'dayForm(', 'm530/event/delete', 'm530/task/delete'):
            self.assertIn(hook, js)


# ================================================================ poste et installation (F19, F20, F24)
class Desktop(unittest.TestCase):
    def test_f19_first_run_opens_the_guided_settings(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('poste_5624', ROOT / 'poste.py'); poste = importlib.util.module_from_spec(spec); spec.loader.exec_module(poste)
        with tempfile.TemporaryDirectory() as home:
            p = poste.paths(home); p['data'].mkdir(parents=True)
            p['config'].write_text(json.dumps({'nextcloud': {'url': 'https://cloud.example.com'}}))
            self.assertTrue(poste.first_run(p))
            p['config'].write_text(json.dumps({'nextcloud': {'url': 'https://cloud.example.com', 'local_path': home}}))
            self.assertFalse(poste.first_run(p))
        text = src('poste.py')
        self.assertIn("rubrique=connexions&premier=1", text); self.assertIn('Premier lancement', src('agent/parametres5621.py'))

    def test_f24_supervision_lock_and_webkit_origin(self):
        text = src('poste.py')
        for needle in ('def _watch', 'RESTART_LIMIT', 'os.killpg', "self._spawn('passage'", 'request.cancel()', "'decide-policy'", 'instance_axiorhub_deja_active', '_lock_held'):
            self.assertIn(needle, text)
        self.assertNotIn('subprocess.run([self.python', text)

    def test_f20_local_folder_and_ai_are_enough_to_finish(self):
        from agent import setup560
        auth = types.SimpleNamespace(app=types.SimpleNamespace(config_path='x'))
        with tempfile.TemporaryDirectory() as home:
            cfg = {'mail': {'host': 'imap.example.com'}, 'nextcloud': {'url': 'https://cloud.example.com', 'local_path': home, 'roots': ['/']}, 'ollama': {'url': 'http://127.0.0.1:11434'}}
            now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
            cfg['installation'] = {'connection_tests567': {k: {'ok': True, 'fingerprint': setup560._connection_fingerprint(cfg, k), 'at': now} for k in ('local', 'ia')}}
            store = {'cfg': cfg}
            with patch('agent.setup560._cfg', side_effect=lambda a: json.loads(json.dumps(store['cfg']))), patch('agent.setup560._write_cfg', side_effect=lambda a, c: store.update(cfg=c)):
                ok, message = setup560.finish(auth, {'email': 'admin@example.test'})
                self.assertTrue(ok, message)
                store['cfg']['ollama']['model'] = 'autre'          # modifier l'IA ne périme que le test de l'IA
                store['cfg']['installation'].pop('done', None)
                ok, message = setup560.finish(auth, {'email': 'admin@example.test'})
                self.assertFalse(ok); self.assertIn('ia', message); self.assertNotIn('local', message.split(':', 1)[1])


class Release(unittest.TestCase):
    def test_version_and_conformity_matrix(self):
        from agent import __version__
        self.assertGreaterEqual(tuple(map(int, __version__.split('.'))), (5, 6, 24))   # versions suivantes comprises
        matrix = src('CONFORMITE-5.6.24.md')
        for n in range(1, 38):
            self.assertIn('F%02d' % n, matrix)
        self.assertIn('tests/test_v5624.py', src('RELEASE-FILES.json'))


if __name__ == '__main__':
    unittest.main()

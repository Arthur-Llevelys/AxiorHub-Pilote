"""5.2.0 : OCR, historique, renvois en suivi de modifications, tampon importé, dépôt, bordereau adverse ; agenda personnel, rappels ;
feuille de style unique, recherche rapide, Aujourd'hui, diagnostic ; recette automatique et construction contrôlée de l'archive."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib

from agent import pieces510 as p5, pieces520 as q, agenda520, reminders520, web520
from agent.common import Stop, private_json
from agent.desk import Desk
import test_v510 as t510
from test_v510 import make_pdf, make_docx, PROFILE

ROOT = Path(__file__).resolve().parent.parent


def png_rgba(w=30, h=12):
    rows = b''.join(b'\x00' + bytes([30, 60, 150, 200] * w) for _ in range(h))

    def chunk(k, d):
        return struct.pack('>I', len(d)) + k + d + struct.pack('>I', zlib.crc32(k + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


class PiecesBase(t510.Base):
    def prepare(self, names=('01 - Courrier du 21 janvier 2025.pdf', '02_Constat huissier.pdf', '03 - Contrat de bail.pdf'), **opts):
        did = self.scan()
        rows = self.rows(did)
        payload = [{'key': r['key'], 'include': r['name'] in names, 'title': r['title'], 'date': r['date']}
                   for r in sorted(rows, key=lambda r: names.index(r['name']) if r['name'] in names else 99)]
        p5.update_table(self.desk, did, payload, opts=opts or None)
        return did

    def create(self, did, confirm_image=False):
        code = p5.request_creation(self.desk, did)
        self.assertTrue(code['ready'], code)
        p5.confirm_creation(self.desk, did, code['code'], confirm_image)
        return p5.perform(self.desk, 'pieces_create510', {'draft': did})


class Defaults(PiecesBase):
    def test_pdf_limit_defaults_to_50_mb_and_deposit_to_10_mb(self):
        o = p5.options(self.desk)
        self.assertEqual((o['max_file_mb'], o['deposit_mb'], o['ocr'], o['fix_conclusions']), (50, 10, True, True))
        self.assertIn('10 Mo', q.DEPOSIT_NOTE)


class Ocr(PiecesBase):
    def test_scan_uses_ocr_for_dates_on_scanned_pdfs(self):
        with patch('agent.pieces520.ocr_text', return_value='Procès-verbal de constat dressé le 12 mars 2025 à Lyon'):
            did = self.scan()
        row = next(r for r in self.rows(did) if r['name'] == '02_Constat huissier.pdf')
        self.assertTrue(row['ocr'])
        self.assertEqual(row['date'], '2025-03-12')

    def test_make_searchable_adds_an_invisible_text_layer(self):
        layer = p5._overlay_page(['BT 3 Tr /AXR 12 Tf 72 700 Td (Texte reconnu par OCR) Tj ET'])
        w = p5._pdf_lib().PdfWriter()
        page = w.add_blank_page(595, 842)
        page.merge_page(layer)
        buf = io.BytesIO()
        w.write(buf)

        def fake_run(args, timeout=120):
            if args[-1] == 'pdf':
                Path(args[2] + '.pdf').write_bytes(buf.getvalue())
            return b''
        blank = p5._pdf_lib().PdfWriter()
        blank.add_blank_page(595, 842)
        raw = io.BytesIO()
        blank.write(raw)
        with patch('agent.pieces520.ocr_available', return_value='fra+eng'), patch('agent.pieces520._run', side_effect=fake_run), \
                patch('agent.pieces520._render', side_effect=lambda r, n, d, td: Path(td) / 'p.png'):
            out, done = q.make_searchable(raw.getvalue())
        self.assertEqual(done, 1)
        self.assertIn('Texte reconnu par OCR', p5._pdf_lib().PdfReader(io.BytesIO(out)).pages[0].extract_text())

    def test_without_tesseract_nothing_changes(self):
        with patch('agent.pieces520.ocr_available', return_value=''):
            raw = make_pdf(1)
            self.assertEqual(q.make_searchable(raw), (raw, 0))
            self.assertEqual(q.ocr_text(raw), '')


class History(PiecesBase):
    def test_second_bordereau_continues_numbering_and_flags_communicated_pieces(self):
        first = self.prepare()
        self.create(first)
        self.assertEqual(q.last_number(self.desk, 'DEMO'), 3)
        second = self.scan()
        data = p5.get(self.desk, second)['data']
        self.assertEqual(data['case']['premiere_piece'], '4')
        self.assertEqual(data['case']['numero_bordereau'], '2')
        done = next(r for r in data['rows'] if r['name'] == '02_Constat huissier.pdf')
        self.assertFalse(done['include'])
        self.assertEqual(done['communicated']['number'], 2)
        rows = data['rows']
        p5.update_table(self.desk, second, [{'key': r['key'], 'include': r['name'] == 'Photo du dégât.jpg', 'title': r['title'], 'date': r['date']} for r in rows])
        checks = p5.check(self.desk, second)
        texts = ' | '.join(i['text'] for i in checks['issues'])
        self.assertNotIn('Pièce n° 1 citée', texts)                      # pièces 1 à 3 déjà communiquées : renvois valides
        self.assertIn('Pièce n° 5 citée', texts)                         # 5 n'existe nulle part
        res = self.create(second)
        names = [PurePosixPath(p).name for p in self.docs.puts]
        self.assertIn('Pièce 04 - Photo du dégât.pdf', names)
        stamped = self.docs.files[next(p for p in self.docs.puts if p.endswith('Pièce 04 - Photo du dégât.pdf'))]
        self.assertIn('PIÈCE N° 4', p5._pdf_lib().PdfReader(io.BytesIO(stamped)).pages[0].extract_text())
        docx = self.docs.files[next(p for p in self.docs.puts if 'Bordereau n°2' in p and p.endswith('.docx'))]
        self.assertIn('Pièce n°04', p5._joined_text(zipfile.ZipFile(io.BytesIO(docx)).read('word/document.xml').decode()))
        self.assertEqual([h['number'] for h in q.history(self.desk, 'DEMO')], [1, 2, 3, 4])


class Renvois(PiecesBase):
    def test_conclusions_get_tracked_renumbering_in_a_new_file(self):
        did = self.prepare()
        plan = p5.plan_instructions(self.desk, did, 'retire la pièce 2, place la pièce 3 en 1')
        self.assertEqual(plan['errors'], [])
        p5.apply_plan(self.desk, did)
        self.assertEqual(p5.get(self.desk, did)['data']['renumber'], {'1': 2, '3': 1, '2': None})
        res = self.create(did)
        out = next(p for p in self.docs.puts if 'renvois mis à jour' in p)
        xml = zipfile.ZipFile(io.BytesIO(self.docs.files[out])).read('word/document.xml').decode()
        self.assertIn('<w:del ', xml)
        self.assertIn('<w:ins ', xml)
        self.assertIn('<w:delText xml:space="preserve">1</w:delText>', xml)
        original = '/Dossiers/DEMO/Conclusions récapitulatives.docx'
        self.assertNotIn('<w:del', zipfile.ZipFile(io.BytesIO(self.docs.files[original])).read('word/document.xml').decode())
        r = p5.get(self.desk, did)['result']['renvois']
        self.assertGreaterEqual(r['applied'], 1)
        self.assertTrue(r['manual'])                                       # la plage « 2 à 3 » touche une pièce retirée

    def test_cumulative_mapping(self):
        m = q.compose({}, {'mapping': [{'old': 1, 'new': 2}, {'old': 2, 'new': 1}], 'removed': [3]})
        m = q.compose(m, {'mapping': [{'old': 2, 'new': 1}], 'removed': [1]})
        self.assertEqual(m, {'1': 1, '2': None, '3': None})

    def test_citation_edits_keep_unchanged_and_flag_removed(self):
        edits, manual = q.citation_edits('voir pièce n° 2 et pièce 7', {'2': 3, '7': None})
        self.assertEqual([x[2] for x in edits], ['3'])
        self.assertEqual(manual[0][0], 7)


class StampImage(PiecesBase):
    def test_imported_stamp_requires_explicit_confirmation_each_time(self):
        q.save_stamp_image(self.desk, png_rgba())
        did = self.prepare(opts_dummy=None) if False else self.prepare()
        p5.update_table(self.desk, did, None, opts={'stamp_image': True})
        code = p5.request_creation(self.desk, did)
        self.assertTrue(code['image_confirmation'])
        with self.assertRaises(Stop) as ctx:
            p5.confirm_creation(self.desk, did, code['code'], False)
        self.assertEqual(str(ctx.exception), 'confirmation_tampon_image_requise')
        p5.confirm_creation(self.desk, did, code['code'], True)
        p5.perform(self.desk, 'pieces_create510', {'draft': did})
        pdf = self.docs.files[next(p for p in self.docs.puts if p.endswith('Pièce 01 - Courrier du 21 janvier 2025.pdf'))]
        page = p5._pdf_lib().PdfReader(io.BytesIO(pdf)).pages[0]
        self.assertIn('/AXI', str(page['/Resources']))
        self.assertIn('PIÈCE N° 1', page.extract_text())

    def test_image_formats(self):
        self.assertEqual(q.save_stamp_image(self.desk, t510.JPEG)['image'], 'tampon.jpg')
        self.assertEqual(q.save_stamp_image(self.desk, png_rgba())['image'], 'tampon.png')
        for bad in (b'GIF89a', b''):
            with self.assertRaises(Stop):
                q.save_stamp_image(self.desk, bad)
        self.assertEqual(q.remove_stamp_image(self.desk), {'image': ''})


class Deposit(PiecesBase):
    def test_deposit_folders_stay_under_the_limit(self):
        did = self.prepare(deposit=True)
        sizes = [len(make_pdf(1))]
        p5.update_table(self.desk, did, None, opts={'deposit': True, 'deposit_mb': 1})
        with patch('agent.pieces520.plan_deposit', wraps=q.plan_deposit) as spy:
            self.create(did)
        names = [p for p in self.docs.puts if '/Dépôt e-Barreau/' in p]
        self.assertTrue(any(p.endswith('LISEZMOI.txt') for p in names))
        envois = {}
        for p in names:
            m = re.search(r'/Envoi (\d+)/', p)
            if m:
                envois.setdefault(m.group(1), 0)
                envois[m.group(1)] += len(self.docs.files[p])
        self.assertTrue(envois)
        self.assertTrue(all(v <= 1_000_000 for v in envois.values()))
        self.assertTrue(all(re.fullmatch(r'(Bordereau|P\d{2}_[a-z0-9_]+(_partie\d+_p\d+-\d+)?)\.pdf', PurePosixPath(p).name) for p in names if p.endswith('.pdf')))

    def test_split_large_piece(self):
        big = make_pdf(24, 'Gros')
        envois, warnings = q.plan_deposit([(1, 'Gros', big)], len(big) / 1e6 * 0.4)
        self.assertGreaterEqual(len(envois), 3)
        self.assertTrue(warnings)


class Adverse(PiecesBase):
    def test_adverse_bordereau_is_compared_with_received_pieces(self):
        self.docs.files['/Dossiers/DEMO/Reçu/Bordereau adverse.docx'] = make_docx('BORDEREAU\nPièce n° 1 : Contrat de vente\nPièce n° 2 : Facture du 3 mai 2024\nPièce n° 3 : Attestation')
        self.docs.files['/Dossiers/DEMO/Reçu/01 contrat.pdf'] = make_pdf(1)
        self.docs.files['/Dossiers/DEMO/Reçu/Facture mai 2024.pdf'] = make_pdf(1)
        self.docs.files['/Dossiers/DEMO/Reçu/Conclusions adverses.docx'] = make_docx('Voir pièces n° 1 à 4.')
        res = q.adverse_check(self.desk, 'DEMO', '/Dossiers/DEMO/Reçu/Bordereau adverse.docx', '', '/Dossiers/DEMO/Reçu/Conclusions adverses.docx')
        self.assertEqual([m['number'] for m in res['missing']], [3])
        self.assertEqual(res['cited_absent'], [4])
        self.assertEqual(len(res['matched']), 2)
        self.assertEqual(self.desk.settings('pieces520:adverse:DEMO')['announced'], 3)


# ====================================================================================== agenda personnel
EVENT = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:perso-1\r\nDTSTAMP:20260901T100000Z\r\nDTSTART;TZID=Europe/Paris:20261005T090000\r\n"
         "DTEND;TZID=Europe/Paris:20261005T100000\r\nSUMMARY:Rendez-vous client\r\nCATEGORIES:Cabinet\r\nX-NC-COLOR:#ff0000\r\n"
         "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
TODO = "BEGIN:VCALENDAR\r\nBEGIN:VTODO\r\nUID:perso-t\r\nSUMMARY:Appeler le greffe\r\nCATEGORIES:Urgent\r\nDUE;VALUE=DATE:20261010\r\nEND:VTODO\r\nEND:VCALENDAR\r\n"
CAL = 'https://cloud.example.test/remote.php/dav/calendars/tech/perso/'


class FakeHTTP:
    base = 'https://cloud.example.test'

    def __init__(self, objects):
        self.objects, self.puts = objects, []

    def request(self, method, url, data=None, headers=None, limit=0):
        path = url[len(self.base):]
        if method == 'GET':
            return self.objects[path].encode()
        if method == 'PUT':
            if headers.get('If-Match') != '"v1"':
                raise Stop('http_412')
            self.objects[path] = data.decode()
            self.puts.append(path)
            return b''
        if method == 'REPORT':
            raise Stop('http_404')


class FakeCal:
    def __init__(self, objects):
        self.http = FakeHTTP(objects)

    def calendar_url(self, url):
        return CAL

    def events(self, *a, **k):
        return []

    def todos(self, *a, **k):
        return []


class AgendaBase(unittest.TestCase):
    setUp0 = t510.WebTests.setUp
    request = t510.WebTests.request

    def setUp(self):
        self.setUp0()
        self.f.c['nextcloud_workflow'] = {'enabled': True, 'url': 'https://cloud.example.test', 'username': 'tech', 'password_file': 'x',
                                          'calendar_read_urls': [CAL], 'task_calendar_url': CAL, 'planning_calendar_url': CAL}
        private_json(self.config, self.f.c)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        from agent.workplan import ensure_schema
        ensure_schema(self.desk)
        self.desk.db.execute('INSERT INTO calendar_cache VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('ev1', 'perso-1', '', CAL, '/remote.php/dav/calendars/tech/perso/perso-1.ics', '"v1"', 'Rendez-vous client', '', '',
                              '2026-10-05T07:00:00+00:00', '2026-10-05T08:00:00+00:00', 1, '', '[]', '2026-10-01'))
        self.desk.db.execute('INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             ('t1', 'perso-t', CAL, '/remote.php/dav/calendars/tech/perso/perso-t.ics', '"v1"', '', 'Appeler le greffe', '', '', '2026-10-10',
                              0, 0, 'todo', '', '', '', '', '{}', 'x', 'x', 'x'))
        self.desk.db.commit()
        self.cal = FakeCal({'/remote.php/dav/calendars/tech/perso/perso-1.ics': EVENT, '/remote.php/dav/calendars/tech/perso/perso-t.ics': TODO})


class Agenda(AgendaBase):
    def test_disabled_by_default(self):
        with self.assertRaises(Stop) as ctx:
            agenda520.edit_event(self.desk, {'event': 'ev1', 'title': 'X'}, self.cal)
        self.assertEqual(str(ctx.exception), 'modification_agenda_personnel_desactivee')

    def test_event_edit_keeps_alarm_categories_and_timezone(self):
        agenda520.set_enabled(self.desk, True)
        agenda520.edit_event(self.desk, {'event': 'ev1', 'title': 'Rendez-vous déplacé', 'start': '2026-10-06T14:30', 'duration_minutes': 45}, self.cal)
        new = self.cal.http.objects['/remote.php/dav/calendars/tech/perso/perso-1.ics']
        for keep in ('BEGIN:VALARM', 'TRIGGER:-PT15M', 'CATEGORIES:Cabinet', 'X-NC-COLOR:#ff0000', 'UID:perso-1'):
            self.assertIn(keep, new)
        self.assertIn('DTSTART;TZID=Europe/Paris:20261006T143000', new)
        self.assertIn('DTEND;TZID=Europe/Paris:20261006T151500', new)
        self.assertIn('SUMMARY:Rendez-vous déplacé', new)
        self.assertLess(new.index('LAST-MODIFIED'), new.index('BEGIN:VALARM'))

    def test_recurring_and_concurrent_changes_are_refused(self):
        agenda520.set_enabled(self.desk, True)
        path = '/remote.php/dav/calendars/tech/perso/perso-1.ics'
        self.cal.http.objects[path] = EVENT.replace('CATEGORIES:Cabinet', 'CATEGORIES:Cabinet\r\nRRULE:FREQ=WEEKLY')
        with self.assertRaises(Stop) as ctx:
            agenda520.edit_event(self.desk, {'event': 'ev1', 'title': 'X'}, self.cal)
        self.assertEqual(str(ctx.exception), 'evenement_recurrent_lecture_seule')
        self.cal.http.objects[path] = EVENT
        self.desk.db.execute("UPDATE calendar_cache SET etag='\"v0\"' WHERE id='ev1'")
        self.desk.db.commit()
        with self.assertRaises(Stop) as ctx:
            agenda520.edit_event(self.desk, {'event': 'ev1', 'title': 'X'}, self.cal)
        self.assertEqual(str(ctx.exception), 'objet_agenda_modifie_ailleurs')

    def test_task_edit_keeps_categories(self):
        agenda520.set_enabled(self.desk, True)
        with patch('agent.workplan.sync_tasks', return_value={}):
            agenda520.edit_task(self.desk, {'task': 't1', 'due': '2026-10-12', 'status': 'completed'}, self.cal)
        new = self.cal.http.objects['/remote.php/dav/calendars/tech/perso/perso-t.ics']
        for keep in ('CATEGORIES:Urgent', 'DUE;VALUE=DATE:20261012', 'STATUS:COMPLETED', 'PERCENT-COMPLETE:100', 'COMPLETED:'):
            self.assertIn(keep, new)

    def test_object_outside_calendar_is_refused(self):
        with self.assertRaises(Stop):
            agenda520._object_url(self.cal, CAL, '/remote.php/dav/calendars/tech/autre/x.ics')

    def test_week_view_offers_drag_and_drop_and_personal_editor(self):
        agenda520.set_enabled(self.desk, True)
        live = [{'uid': 'perso-1', 'recurrence_id': '', 'summary': 'Rendez-vous client', 'description': '', 'location': '', 'busy': True,
                 'start': '2026-10-05T09:00:00+02:00', 'end': '2026-10-05T10:00:00+02:00', 'source_url': CAL,
                 'href': '/remote.php/dav/calendars/tech/perso/perso-1.ics', 'etag': '"v1"'}]
        with patch('agent.workplan._dav', return_value=type('D', (), {'calendar_url': lambda s, u: CAL, 'events': lambda s, *a: live,
                                                                     'http': FakeHTTP({})})()):
            body = self.request('/planning', query='vue=agenda&view=week&date=2026-10-05')['body']
        self.assertIn('class="ax-dnd"', body)
        self.assertIn('data-day="2026-10-05"', body)
        self.assertIn('data-task="t1"', body)
        self.assertIn('Modifier (agenda personnel)', body)
        self.assertIn('/static/v520.js', body)


class Reminders(AgendaBase):
    def test_reminders_are_deposited_in_the_cabinet_inbox_once_without_sending(self):
        self.f.c['mail']['from_address'] = 'cabinet@example.test'
        desk = Desk(self.f.c)
        self.addCleanup(desk.db.close)
        reminders520.save(desk, push=False, email=True)
        from agent.live430 import emit
        emit(desk, 'echeance', 'J-7 : appel – DEMO, échéance le 10 octobre 2026.', matter='DEMO')
        emit(desk, 'progress', 'sans rapport')
        deposited = []
        out = reminders520.run(desk, sender=deposited.append)
        self.assertEqual(out['sent'], 1)
        msg = deposited[0]
        self.assertEqual((msg['From'], msg['To']), ('cabinet@example.test', 'cabinet@example.test'))
        self.assertIn('J-7', msg.get_content())
        self.assertEqual(reminders520.run(desk, sender=deposited.append)['sent'], 0)
        self.assertNotIn('send490', (ROOT / 'agent' / 'reminders520.py').read_text(encoding='utf-8'))

    def test_inbox_deposit_refuses_any_third_party(self):
        from agent.mailbox import Mailbox
        from email.message import EmailMessage
        box = Mailbox.__new__(Mailbox)
        box.cfg = {'from_address': 'cabinet@example.test'}
        calls = []
        box.conn = type('C', (), {'append': lambda s, *a: calls.append(a) or ('OK', [b''])})()
        m = EmailMessage()
        m['From'], m['To'] = 'cabinet@example.test', 'tiers@example.test'
        with self.assertRaises(Stop):
            box.append_reminder(m)
        del m['To']
        m['To'] = 'cabinet@example.test'
        box.append_reminder(m)
        self.assertEqual(calls[0][0], 'INBOX')


# ====================================================================================== interface et fiabilité
class Interface(t510.Base):
    def test_single_stylesheet_everywhere_and_bundle_up_to_date(self):
        for path in ('/courriels', '/pieces', '/dossiers', '/planning', '/aujourdhui', '/diagnostic'):
            body = self.request(path)['body']
            self.assertEqual(body.count('/static/app520.css'), 1, path)
            links = re.findall(r'<link rel="stylesheet" href="[^"]*/static/(v\d+|style)\.css"', body)
            self.assertFalse([x for x in links if x not in ('v450', 'v460', 'v510')], (path, links))
            self.assertIn('/static/v520.js', body)
        spec = __import__('importlib.util').util.spec_from_file_location('build_css', ROOT / 'scripts' / 'build-css.py')
        mod = __import__('importlib.util').util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        bundle = (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertEqual(bundle, mod.build())
        for name in ('v320.css', 'v363.css', 'v364.css', 'v380.css', 'v390.css', 'v391.css', 'v392.css', 'v393.css', 'v400.css', 'v440.css', 'v490.css', 'v520.css'):
            self.assertIn('/* ==== %s ==== */' % name, bundle)

    def test_quick_search_shortcut(self):
        js = (ROOT / 'agent' / 'static' / 'v420.js').read_text(encoding='utf-8')
        self.assertIn("event.key.toLowerCase()==='k'", js)
        self.assertIn("/recherche?q='+encodeURIComponent(raw)", js)
        self.assertIn("b.textContent=title", js)                 # le texte saisi n'est jamais injecté en HTML
        self.assertNotIn("ev.key === 'k'", (ROOT / 'agent' / 'static' / 'v520.js').read_text(encoding='utf-8'))

    def test_today_is_a_single_column_of_four_blocks(self):
        # 5.3.0 : l'écran 5.2 reste disponible en « vue=essentiel » ; l'accueil est le poste de pilotage.
        body = self.request('/aujourdhui', query='vue=essentiel')['body']
        for title in ('Audiences et rendez-vous', 'Brouillons à relire', 'Échéances', 'Décisions en attente'):
            self.assertIn(title, body)
        self.assertIn('class="t520"', body)
        self.assertIn('Vue détaillée', body)
        self.assertIn('ck-', self.request('/aujourdhui', query='vue=cockpit')['body'])


class Diagnostic(t510.Base):
    def test_page_explains_blocking_causes(self):
        from agent import conflicts500, autonomy480
        conflicts500.scan_new(self.desk)
        rows = json.loads(Path(self.f.c['matters_file']).read_text())
        rows.append({**t510.DEMO, 'id': 'NEUF', 'client_name': 'Client NEUF', 'path': '/Dossiers/NEUF', 'references': ['NEUF'], 'aliases': [], 'correspondents': []})
        private_json(Path(self.f.c['matters_file']), rows)
        conflicts500.scan_new(self.desk)
        autonomy480.set_level(self.desk, 'courriel_reponse', 'propose')
        jid = self.desk.enqueue('sync')
        self.desk.db.execute("UPDATE jobs SET status='error', finished=?, result=? WHERE id=?",
                             (datetime.now(timezone.utc).isoformat(), json.dumps({'erreur': 'connexion_imap_echouee'}), jid))
        self.desk.db.commit()
        with patch('agent.reliability393.service_health', return_value={'services': []}):
            d = web520.diagnosis(self.desk)
            body = self.request('/diagnostic')['body']
        self.assertTrue(any('conflits' in p for p in d['problems']))
        self.assertTrue(any('Proposer seulement' in p for p in d['problems']))
        self.assertTrue(any('erreur' in p for p in d['problems']))
        self.assertIn('Connexion à la messagerie impossible', body)
        self.assertIn('Pourquoi rien n’est produit', body)
        self.assertIn('/agent-courriel/diagnostic', self.request('/courriels')['body'])

    def test_connection_checks(self):
        class Box:
            def __init__(self, cfg):
                self.conn = type('C', (), {'select': lambda s, *a, **k: ('OK', [b'1'])})()

            def close(self):
                pass
        with patch('agent.mailbox.Mailbox', Box):
            self.assertTrue(web520.check_imap(self.desk)['ok'])
        with patch('agent.mailbox.Mailbox', side_effect=Stop('connexion_imap_echouee')):
            with self.assertRaises(Stop):
                web520.check_imap(self.desk)

    def test_recette_result_is_shown(self):
        path = Path(self.f.c['state_dir']) / 'recette520.json'
        path.write_text(json.dumps({'status': 'echec', 'at': '2026-10-03T20:00:00', 'version': '5.2.0', 'summary': '1000 test(s), 1 échec(s)',
                                    'failures': ['test_x (test_y.Z)']}), encoding='utf-8')
        with patch('agent.reliability393.service_health', return_value={'services': []}):
            body = self.request('/diagnostic')['body']
            self.assertIn('test_x (test_y.Z)', body)
            self.assertTrue(any('recette' in p for p in web520.diagnosis(self.desk)['problems']))


class Release(unittest.TestCase):
    def test_recette_parses_unittest_output(self):
        sys.path.insert(0, str(ROOT))
        import recette
        status, summary, failures = recette.parse('...\nFAIL: test_a (test_b.C.test_a)\n---\nRan 12 tests in 3.2s\n\nFAILED (failures=1, skipped=2)\n')
        self.assertEqual((status, failures), ('echec', ['test_a (test_b.C.test_a)']))
        self.assertIn('12 test(s)', summary)
        self.assertEqual(recette.parse('Ran 3 tests in 0.1s\n\nOK\n')[0], 'ok')

    def test_install_runs_installer_without_bytecode_then_recette(self):
        text = (ROOT / 'install.sh').read_text(encoding='utf-8')
        self.assertIn('python3 -B installer.py', text)
        self.assertIn('recette.py --background', text)
        self.assertNotIn('exec python3 installer.py', text)

    def test_build_release_excludes_bytecode_and_verifies(self):
        cache = ROOT / 'agent' / '__pycache__'
        cache.mkdir(exist_ok=True)
        (cache / 'piege.cpython-313.pyc').write_bytes(b'x')
        self.addCleanup(lambda: (cache / 'piege.cpython-313.pyc').unlink(missing_ok=True))
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / 'paquet.tar.gz'
            subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build-release.py'), str(out)], check=True, capture_output=True)
            import tarfile
            with tarfile.open(out) as tar:
                names = tar.getnames()
                manifest = tar.extractfile([n for n in names if n.endswith('/MANIFEST.sha256')][0]).read().decode()
            self.assertFalse([n for n in names if '__pycache__' in n or n.endswith('.pyc')])
            self.assertNotIn('.pyc', manifest)
            self.assertTrue(Path(str(out) + '.sha256').is_file())


if __name__ == '__main__':
    unittest.main()

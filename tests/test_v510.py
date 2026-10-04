"""5.1.0 : pièces et bordereaux — analyse, tableau, contrôles, instructions, tampon, création confirmée."""
import ast
import base64
import io
import json
from pathlib import Path, PurePosixPath
import re
import unittest
from unittest.mock import patch
import zipfile

from agent import pieces510 as p5, web510
from agent.common import Stop, private_json
from agent.desk import Desk
from test_desk import WebTests
from test_v490 import DEMO

ROOT = Path(__file__).resolve().parent.parent
PROFILE = {'prenom_avocat': 'Camille', 'nom_avocat': 'Exemple', 'ville_avocat': 'Lyon', 'numero_toque_avocat': '999',
           'adresse_cabinet_avocat': '1 place Fictive 69000 Lyon', 'numero_telephone_avocat': '04 00 00 00 00', 'email_avocat': 'cabinet@example.test'}


def make_pdf(pages=1, label='Document', rotate=0, password=None):
    pypdf = p5._pdf_lib()
    w = pypdf.PdfWriter()
    for i in range(pages):
        page = w.add_blank_page(595, 842)
        page.merge_page(p5._overlay_page(['BT /AXR 14 Tf 72 760 Td %s Tj ET' % p5._pdf_string('%s page %d' % (label, i + 1))]))
        if rotate:
            page.rotate(rotate)
    if password:
        w.encrypt(password, algorithm='RC4-128')
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def make_docx(text):
    body = ''.join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % line for line in text.split('\n'))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml', '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % body)
    return buf.getvalue()


JPEG = bytes.fromhex('ffd8ffe000104a46494600010100000100010000ffc0000b080002000201011100ffc4001400010000000000000000000000000000000affc40014100100'
                     '000000000000000000000000000000ffda0008010100003f00d2cf20ffd9')


class FakeDocs:
    """Nextcloud simulé : création exclusive, relecture, dossiers."""

    def __init__(self, files):
        self.files = dict(files)
        self.folders = set()
        self.puts = []

    def inventory(self, path):
        return [{'path': p, 'directory': False, 'size': len(b), 'etag': '"%d"' % len(b), 'modified': 'Mon, 01 Sep 2026 10:00:00 GMT'}
                for p, b in sorted(self.files.items()) if p.startswith(path.rstrip('/') + '/')]

    def download(self, item):
        return self.files[item['path']]

    def stat(self, path):
        if path not in self.files:
            raise Stop('fichier_nextcloud_introuvable')
        return {'path': path, 'etag': '"%d"' % len(self.files[path]), 'size': len(self.files[path]), 'modified': ''}

    def list_folder(self, path):
        if path in self.folders or any(p.startswith(path + '/') for p in self.files):
            return []
        raise Stop('http_404')

    def ensure_folder(self, path, boundary):
        assert path.startswith(boundary)
        self.folders.add(path)
        return path

    def put_file(self, path, data, kind='application/octet-stream'):
        if path in self.files:
            raise Stop('http_412')
        self.files[path] = data
        self.puts.append(path)
        return path

    def file_web_url(self, path):
        return 'https://cloud.example.test/f/' + str(len(self.puts))


class Base(unittest.TestCase):
    setUp0 = WebTests.setUp
    request = WebTests.request

    def setUp(self):
        self.setUp0()
        private_json(Path(self.f.c['matters_file']), [DEMO])
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        p5.save_profile(self.desk, PROFILE)
        root = '/Dossiers/DEMO'
        self.docs = FakeDocs({
            root + '/Pièces/01 - Courrier du 21 janvier 2025.pdf': make_pdf(2, 'Courrier du 21/01/2025'),
            root + '/Pièces/02_Constat huissier.pdf': make_pdf(3, 'Constat'),
            root + '/Pièces/03 - Contrat de bail.pdf': make_pdf(1, 'Bail', rotate=90),
            root + '/Pièces/Copie du constat.pdf': make_pdf(3, 'Constat'),
            root + '/Pièces/Relevé protégé.pdf': make_pdf(1, 'Banque', password='secret'),
            root + '/Pièces/Photo du dégât.jpg': JPEG,
            root + '/Pièces/Note.docx': make_docx('note'),
            root + '/Divers/Attestation témoin.pdf': make_pdf(1, 'Attestation du 2 mars 2025'),
            root + '/Conclusions récapitulatives.docx': make_docx('Comme le montre la pièce n° 1 et les pièces 2 à 3, puis la pièce n° 5.'),
        })
        patcher = patch('agent.pieces510._dav', return_value=self.docs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def scan(self, **kw):
        return p5.scan(self.desk, {'matter': 'DEMO', **kw})['draft']

    def rows(self, did):
        return p5.get(self.desk, did)['data']['rows']


class Scan(Base):
    def test_scan_proposes_pieces_from_the_pieces_folder(self):
        did = self.scan()
        rows = self.rows(did)
        names = [r['name'] for r in rows if r['include']]
        self.assertEqual(names[:3], ['01 - Courrier du 21 janvier 2025.pdf', '02_Constat huissier.pdf', '03 - Contrat de bail.pdf'])
        self.assertNotIn('Attestation témoin.pdf', names)                         # hors du sous-dossier « Pièces »
        first = rows[0]
        self.assertEqual((first['pages'], first['date'], first['title']), (2, '2025-01-21', 'Courrier du 21 janvier 2025'))
        self.assertEqual(rows[1]['title'], 'Constat huissier')
        dup = next(r for r in rows if r['name'] == 'Copie du constat.pdf')
        self.assertFalse(dup['include'])
        self.assertTrue(dup.get('duplicate_of'))
        locked = next(r for r in rows if r['name'] == 'Relevé protégé.pdf')
        self.assertEqual((locked['readable'], locked['error']), (False, 'piece_pdf_protegee'))
        photo = next(r for r in rows if r['name'] == 'Photo du dégât.jpg')
        self.assertEqual((photo['kind'], photo['pages'], photo['readable']), ('jpeg', 1, True))
        others = p5.get(self.desk, did)['data']['others']
        self.assertTrue(any(o['path'].endswith('Note.docx') for o in others))
        self.assertEqual(self.docs.puts, [])                                       # rien n'est écrit

    def test_chronological_order_and_table_editing(self):
        did = self.scan(order='chronologique')
        rows = self.rows(did)
        keep = [r for r in rows if r['name'] in ('02_Constat huissier.pdf', '01 - Courrier du 21 janvier 2025.pdf')]
        payload = [{'key': r['key'], 'include': True, 'title': 'Pièce ' + r['name'][:2], 'date': r['date']} for r in keep]
        payload += [{'key': r['key'], 'include': False, 'title': r['title'], 'date': r['date']} for r in rows if r not in keep]
        p5.update_table(self.desk, did, payload)
        inc = p5.included(p5.get(self.desk, did)['data'])
        self.assertEqual([r['title'] for r in inc], ['Pièce ' + keep[0]['name'][:2], 'Pièce ' + keep[1]['name'][:2]])
        with self.assertRaises(Stop):
            p5.update_table(self.desk, did, [{'key': 'inconnue', 'include': True, 'title': 'x'}])
        with self.assertRaises(Stop):
            p5.update_table(self.desk, did, [{'key': keep[0]['key'], 'include': True, 'title': 'x', 'date': '31/02/2025'}])


class Checks(Base):
    def test_controls_against_conclusions(self):
        did = self.scan()
        checks = p5.check(self.desk, did)
        texts = ' | '.join(i['text'] for i in checks['issues'])
        self.assertTrue(checks['conclusions'].endswith('Conclusions récapitulatives.docx'))
        self.assertEqual(checks['cited'], [1, 2, 3, 5])
        n = len(p5.included(p5.get(self.desk, did)['data']))
        self.assertEqual(n, 5)                                                     # 01, 02, 03, relevé protégé, photo (la copie est un doublon)
        self.assertNotIn('absente du bordereau', texts)
        self.assertIn('protégé', texts)                                            # le relevé protégé est retenu : bloquant
        self.assertTrue(any(i['level'] == 'bloquant' for i in checks['issues']))
        self.assertIn('jamais citée', texts)

    def test_citation_forms(self):
        found = [n for n, _, _ in p5.citations('pièces n° 1, 2 et 4 ; pièce 7 à 9 ; Pièce n°12 ; pieces nos 14-15')]
        self.assertEqual(found, [1, 2, 4, 7, 8, 9, 12, 14, 15])


class Instructions(Base):
    def setUp(self):
        super().setUp()
        self.did = self.scan()
        rows = self.rows(self.did)
        order = ['01 - Courrier du 21 janvier 2025.pdf', '02_Constat huissier.pdf', '03 - Contrat de bail.pdf', 'Photo du dégât.jpg']
        payload = [{'key': r['key'], 'include': r['name'] in order, 'title': r['title'], 'date': r['date']} for r in sorted(rows, key=lambda r: order.index(r['name']) if r['name'] in order else 99)]
        p5.update_table(self.desk, self.did, payload)

    def test_plan_then_apply(self):
        plan = p5.plan_instructions(self.desk, self.did, 'Retire la pièce 2, ajoute l’attestation du témoin en 1 ; renomme la pièce 3 en Bail commercial du 3 mars 2024')
        self.assertEqual(plan['errors'], [])
        self.assertEqual([(m['old'], m['new']) for m in plan['mapping']], [(None, 1), (1, 2), (3, 3), (4, 4)])
        self.assertEqual(plan['mapping'][2]['title'], 'Bail commercial du 3 mars 2024')
        refs = {(r['old'], r['new']) for r in plan['references']}
        self.assertIn((2, None), refs)                                            # citée mais retirée
        self.assertIn((1, 2), refs)                                               # renvoi à renuméroter
        self.assertEqual(len(p5.included(p5.get(self.desk, self.did)['data'])), 4)  # rien n'est appliqué avant validation
        p5.apply_plan(self.desk, self.did)
        inc = p5.included(p5.get(self.desk, self.did)['data'])
        self.assertEqual([r['name'] for r in inc], ['Attestation témoin.pdf', '01 - Courrier du 21 janvier 2025.pdf', '03 - Contrat de bail.pdf', 'Photo du dégât.jpg'])

    def test_unknown_or_ambiguous_instruction_is_not_applied(self):
        plan = p5.plan_instructions(self.desk, self.did, 'retire la pièce 12 ; fais le café')
        self.assertEqual(len(plan['errors']), 2)
        with self.assertRaises(Stop):
            p5.apply_plan(self.desk, self.did)
        plan = p5.plan_instructions(self.desk, self.did, 'place la pièce 4 en 1')
        self.assertEqual([m['old'] for m in plan['mapping']], [4, 1, 2, 3])


class Creation(Instructions):
    def confirm(self):
        code = p5.request_creation(self.desk, self.did)
        self.assertTrue(code['ready'], code)
        with self.assertRaises(Stop):
            p5.confirm_creation(self.desk, self.did, '000000' if code['code'] != '000000' else '111111')
        job = p5.confirm_creation(self.desk, self.did, code['code'])['job_id']
        self.assertTrue(job)
        return p5.perform(self.desk, 'pieces_create510', {'draft': self.did})

    def test_creates_new_files_without_touching_originals(self):
        before = dict(self.docs.files)
        p5.update_table(self.desk, self.did, None, case={'Dossier': 'DEMO c/ BETA', 'juridiction': 'TJ Lyon', 'numero_bordereau': '3', 'date_du_bordereau': '2026-10-03'},
                        opts={'continuous': True})
        result = self.confirm()
        for path, raw in before.items():
            self.assertEqual(self.docs.files[path], raw)                          # originaux intacts
        self.assertTrue(result['folder'].startswith('/Dossiers/DEMO/Pièces communiquées/Bordereau n°3 du 2026-10-03'))
        names = [PurePosixPath(p).name for p in self.docs.puts]
        self.assertIn('Bordereau n°3 du 2026-10-03.docx', names)
        self.assertIn('Pièce 01 - Courrier du 21 janvier 2025.pdf', names)
        self.assertEqual(sum(1 for n in names if n.startswith('Pièce ')), 4)
        reader = p5._pdf_lib().PdfReader(io.BytesIO(self.docs.files[next(p for p in self.docs.puts if p.endswith('Pièce 01 - Courrier du 21 janvier 2025.pdf'))]))
        first, second = reader.pages[0].extract_text(), reader.pages[1].extract_text()
        self.assertIn('PIÈCE N° 1', first)
        self.assertIn('Maître Camille EXEMPLE', first)
        self.assertIn('Page 1 / 7', first)
        self.assertIn('Pièce n° 1 – p. 2/2', second)
        docx = self.docs.files[next(p for p in self.docs.puts if p.endswith('.docx'))]
        text = p5._joined_text(zipfile.ZipFile(io.BytesIO(docx)).read('word/document.xml').decode('utf-8'))
        self.assertIn('DEMO c/ BETA', text)
        self.assertIn('Pièce n°04', text)
        self.assertNotIn('Pièce n°05', text)
        self.assertNotIn('{{', text)
        self.assertEqual(p5.get(self.desk, self.did)['status'], 'cree')
        with self.assertRaises(Stop):
            p5.request_creation(self.desk, self.did)

    def test_single_pdf_with_bookmarks_and_new_folder_each_time(self):
        p5.update_table(self.desk, self.did, None, opts={'single_pdf': True, 'stamp': 'all'})
        with patch('agent.pieces510.docx_to_pdf', return_value=None):       # 5.6.1 : même résultat avec ou sans LibreOffice sur la machine
            self.confirm()
        combined = next(p for p in self.docs.puts if PurePosixPath(p).name.startswith('Pièces communiquées'))
        reader = p5._pdf_lib().PdfReader(io.BytesIO(self.docs.files[combined]))
        self.assertEqual(len(reader.outline), 4)
        self.assertEqual(len(reader.pages), 7)
        self.assertIn('PIÈCE N° 2', reader.pages[3].extract_text())                 # tampon sur toutes les pages
        second = p5.scan(self.desk, {'matter': 'DEMO'})['draft']
        rows = self.rows(second)
        p5.update_table(self.desk, second, [{'key': r['key'], 'include': r['name'].startswith('01'), 'title': r['title'], 'date': r['date']} for r in rows])
        self.did = second
        res = self.confirm()
        self.assertTrue(res['folder'].endswith(' (2)') or 'n°2' in res['folder'])

    def test_single_pdf_starts_with_the_bordereau_when_libreoffice_converts_it(self):
        p5.update_table(self.desk, self.did, None, opts={'single_pdf': True, 'stamp': 'all'})
        with patch('agent.pieces510.docx_to_pdf', return_value=make_pdf(1, 'Bordereau')):
            self.confirm()
        combined = next(p for p in self.docs.puts if PurePosixPath(p).name.startswith('Pièces communiquées'))
        reader = p5._pdf_lib().PdfReader(io.BytesIO(self.docs.files[combined]))
        self.assertEqual(len(reader.outline), 5)
        self.assertEqual(reader.outline[0].title, 'Bordereau')
        self.assertEqual(len(reader.pages), 8)
        self.assertTrue(any(PurePosixPath(p).suffix == '.pdf' and PurePosixPath(p).name.startswith('Bordereau') for p in self.docs.puts))

    def test_modified_source_stops_creation(self):
        code = p5.request_creation(self.desk, self.did)['code']
        p5.confirm_creation(self.desk, self.did, code)
        self.docs.files['/Dossiers/DEMO/Pièces/02_Constat huissier.pdf'] = make_pdf(1, 'Autre')
        with self.assertRaises(Stop) as ctx:
            p5.perform(self.desk, 'pieces_create510', {'draft': self.did})
        self.assertEqual(str(ctx.exception), 'piece_modifiee_depuis_analyse')
        self.assertEqual(p5.get(self.desk, self.did)['status'], 'projet')
        self.assertFalse([p for p in self.docs.puts])

    def test_blocking_reasons_prevent_the_code(self):
        rows = self.rows(self.did)
        p5.update_table(self.desk, self.did, [{'key': r['key'], 'include': r['include'] or r['name'] == 'Relevé protégé.pdf', 'title': r['title'], 'date': r['date']} for r in rows])
        out = p5.request_creation(self.desk, self.did)
        self.assertFalse(out['ready'])
        self.assertTrue(any('illisible ou protégée' in x for x in out['reasons']))

    def test_creation_respects_the_conflict_gate(self):
        from agent.desk import JOBS, USER_JOBS
        from agent import conflicts500
        self.assertIn('pieces_create510', conflicts500.GATED_KINDS)
        self.assertTrue({'pieces_scan510', 'pieces_create510'} <= JOBS & USER_JOBS)


class Template(Base):
    def test_template_expands_to_the_real_number_of_pieces(self):
        raw = (ROOT / 'templates' / 'MODELE_BCP_BALISES.docx').read_bytes()
        titles = ['Titre %d & <x>' % i for i in range(1, 26)]
        out, missing = p5.fill_template(raw, {**PROFILE, 'Dossier': 'A c/ B', 'numero_bordereau': '1', 'date_du_bordereau': '3 octobre 2026'}, titles)
        text = p5._joined_text(zipfile.ZipFile(io.BytesIO(out)).read('word/document.xml').decode('utf-8'))
        self.assertIn('Pièce n°25\xa0: Titre 25 & <x>', text)
        self.assertIn('Pièce n°01\xa0: Titre 1 & <x>', text)
        self.assertNotIn('intitule_piece', text)
        self.assertIn('juridiction', missing)
        self.assertIn('[à compléter]', text)
        zipfile.ZipFile(io.BytesIO(out)).testzip()

    def test_uploaded_template_must_contain_a_piece_line(self):
        with self.assertRaises(Stop) as ctx:
            p5.save_template(self.desk, make_docx('{{ Dossier }}'))
        self.assertEqual(str(ctx.exception), 'modele_bordereau_sans_liste_de_pieces')
        p5.save_template(self.desk, make_docx('{{ Dossier }}\nPièce n°1 : {{ intitule_piece_01 }}'))
        self.assertEqual(p5.template_bytes(self.desk)[1], 'cabinet')
        p5.reset_template(self.desk)
        self.assertEqual(p5.template_bytes(self.desk)[1], 'fourni')

    def test_stamp_svg_is_escaped(self):
        p5.save_profile(self.desk, {**PROFILE, 'nom_avocat': '<script>x</script>'})
        svg = p5.stamp_svg(p5.profile(self.desk), 3)
        self.assertNotIn('<script>', svg)
        self.assertIn('PIÈCE N° 3', svg)


class Web(Base):
    def test_page_renders_in_the_common_layout_and_is_reachable(self):
        did = self.scan()
        body = self.request('/pieces', query='draft=' + did)['body']
        self.assertTrue(self.request('/pieces', auth=False)['status'].startswith('401'))
        self.assertIn('<aside id="ws-sidebar">', body)
        self.assertIn('Pièces et bordereaux', body)
        self.assertIn('vf-table p5-table', body)
        self.assertIn('/static/v510.js', body)
        self.assertNotRegex(body.replace('<script defer', '').replace('<script type="application/json"', ''), r'<script|onclick=|style="')
        home = self.request('/courriels')['body']
        self.assertIn('/agent-courriel/pieces', home)                                # lien « Outils » partout
        self.assertIn('/agent-courriel/pieces', self.request('/production')['body'])

    def test_api_requires_csrf_and_saves_profile(self):
        r = self.request('/api440/m510/profile', 'POST', {'nom_avocat': 'X'})
        self.assertTrue(r['status'].startswith('400'))
        out = web510.handle(self.desk, 'm510/profile', {**PROFILE, 'nom_avocat': 'Durand'})
        self.assertEqual(out['nom_avocat'], 'Durand')
        dl = web510.handle(self.desk, 'm510/stamp.svg', {}, 'GET', {})
        self.assertIn('DURAND', dl['download']['text'])
        did = self.scan()
        prev = web510.handle(self.desk, 'm510/preview.docx', {}, 'GET', {'draft': did})
        self.assertTrue(base64.b64decode(prev['download']['base64']).startswith(b'PK'))


class Packaging(unittest.TestCase):
    def test_vendored_pypdf_has_no_absolute_imports_and_works_without_a_system_copy(self):
        vendor = ROOT / 'agent' / '_vendor' / 'pypdf'
        for f in vendor.rglob('*.py'):
            self.assertIsNone(re.search(r'^\s*(?:from|import) pypdf\b', f.read_text(encoding='utf-8'), re.M), f)
        self.assertTrue((vendor / 'LICENSE').is_file())

    def test_module_is_local_only(self):
        tree = ast.parse((ROOT / 'agent' / 'pieces510.py').read_text(encoding='utf-8'))
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        for bad in ('urllib', 'http', 'requests', 'socket', 'model', 'ai_gateway', 'hybrid400'):
            self.assertFalse(any(x == bad or x.endswith('.' + bad) for x in names), bad)


if __name__ == '__main__':
    unittest.main()

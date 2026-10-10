"""5.6.30 : connexions — IMAP en STARTTLS (port 143) ou TLS direct (993), cause précise d'un échec ; dossier local Nextcloud choisi au
niveau de la racine elle-même ; messages de test lisibles."""
import imaplib
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from unittest.mock import patch

from agent.common import Stop
from agent.mailbox import connect

ROOT = Path(__file__).resolve().parents[1]


class FakePlain:
    calls = []
    error = imaplib.IMAP4.error

    def __init__(self, host, port, timeout=0):
        FakePlain.calls.append(('plain', host, port))
        self.capabilities = ('IMAP4REV1', 'STARTTLS')

    def starttls(self, ssl_context=None):
        FakePlain.calls.append(('starttls',))


class Imap(unittest.TestCase):
    def setUp(self):
        FakePlain.calls = []

    def test_port_143_uses_starttls_and_993_uses_direct_tls(self):
        with patch('agent.mailbox.imaplib.IMAP4', FakePlain), patch('agent.mailbox.imaplib.IMAP4_SSL', side_effect=AssertionError('pas de TLS direct')):
            connect({'host': 'imap.example.test', 'port': 143})
        self.assertEqual(FakePlain.calls, [('plain', 'imap.example.test', 143), ('starttls',)])
        with patch('agent.mailbox.imaplib.IMAP4_SSL', return_value='tls') as tls:
            self.assertEqual(connect({'host': 'imap.example.test', 'port': 993}), 'tls')
            self.assertEqual(connect({'host': 'imap.example.test'}), 'tls')
        self.assertEqual(tls.call_count, 2)
        with patch('agent.mailbox.imaplib.IMAP4', FakePlain):
            connect({'host': 'imap.example.test', 'port': 1143, 'security': 'starttls'})

    def test_each_failure_has_its_own_cause(self):
        cases = ((socket.gaierror('x'), 'imap_serveur_introuvable'), (ConnectionRefusedError(), 'imap_port_ferme'), (TimeoutError(), 'imap_delai_depasse'),
                 (ssl.SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number'), 'imap_port_sans_tls_direct'),
                 (ssl.SSLCertVerificationError(1, 'certificate verify failed'), 'imap_certificat_refuse'), (OSError('x'), 'connexion_imap_echouee'))
        for error, code in cases:
            with patch('agent.mailbox.imaplib.IMAP4_SSL', side_effect=error):
                with self.assertRaisesRegex(Stop, '^' + code + '$'):
                    connect({'host': 'imap.example.test', 'port': 993})

        class NoStartTLS(FakePlain):
            def __init__(self, *a, **k):
                self.capabilities = ('IMAP4REV1',)
        with patch('agent.mailbox.imaplib.IMAP4', NoStartTLS):
            with self.assertRaisesRegex(Stop, 'imap_starttls_indisponible'):
                connect({'host': 'imap.example.test', 'port': 143})
        with self.assertRaisesRegex(Stop, 'imap_port_invalide'):
            connect({'host': 'imap.example.test', 'port': 'abc'})

    def test_refused_login_is_named(self):
        from agent.mailbox import Mailbox

        class Conn:
            def login(self, u, p):
                raise imaplib.IMAP4.error('AUTHENTICATIONFAILED')
        with patch('agent.mailbox.connect', return_value=Conn()), patch('agent.mailbox.read_secret', return_value='x'):
            with self.assertRaisesRegex(Stop, 'imap_identifiants_refuses'):
                Mailbox({'host': 'h', 'username': 'u', 'password_file': 'p'})

    def test_messages_are_readable(self):
        from agent.web440 import human
        for code in ('imap_port_sans_tls_direct', 'imap_identifiants_refuses', 'imap_serveur_introuvable', 'http_404', 'http_401'):
            self.assertNotEqual(human(code), code.replace('_', ' '), code)
        src = (ROOT / 'agent' / 'config567.py').read_text(encoding='utf-8')
        self.assertNotIn("'message':'Test non validé : '+code", src)              # plus de « Test non validé : Test non validé »


class LocalRoot(unittest.TestCase):
    def test_local_folder_may_be_the_root_itself_or_its_parent(self):
        from agent.dav import LocalFolder
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'CABINET'
            (root / '01 - Dossiers').mkdir(parents=True)
            (root / 'note.txt').write_text('x', encoding='utf-8')
            for local in (Path(tmp), root):
                lf = LocalFolder({'local_path': str(local), 'roots': ['/CABINET']})
                names = sorted(i['path'] for i in lf.list_folder('/CABINET'))
                self.assertEqual(names, ['/CABINET/01 - Dossiers', '/CABINET/note.txt'], str(local))
            with self.assertRaisesRegex(Stop, 'http_404'):
                LocalFolder({'local_path': str(root), 'roots': ['/AUTRE']}).list_folder('/AUTRE')


class SettingsPage(unittest.TestCase):
    """Paramètres › Connexions : enregistrer plusieurs fois de suite et tester sans recharger la page ; chiffrement IMAP explicite."""

    def test_save_script_survives_the_reloaded_catalog(self):
        js = (ROOT / 'agent' / 'static' / 'v567.js').read_text(encoding='utf-8')
        self.assertIn("if (!input) continue;", js)                                   # cause du « il faut actualiser la page »
        self.assertIn("connections.querySelector('button[type=submit]')", js)
        self.assertIn('<button type="submit">Enregistrer les changements</button>', (ROOT / 'agent' / 'web567.py').read_text(encoding='utf-8'))

    def test_configuration_is_always_written_in_utf8(self):
        src = (ROOT / 'agent' / 'config567.py').read_text(encoding='utf-8')
        self.assertEqual(src.count("os.fdopen(fd,'w', encoding='utf-8')"), 2)
        self.assertNotIn("os.fdopen(fd,'w')", src)

    def test_imap_encryption_choice_and_help(self):
        from agent.config567 import _validate, FIELDS
        keys = [f[0] for f in FIELDS]
        self.assertIn('mail.security', keys)
        self.assertEqual(_validate('imap_security', 'starttls', 'mail.security'), 'starttls')
        self.assertEqual(_validate('imap_security', '', 'mail.security'), '')
        with self.assertRaisesRegex(Stop, 'imap_securite_invalide'):
            _validate('imap_security', 'tls1', 'mail.security')
        web = (ROOT / 'agent' / 'web567.py').read_text(encoding='utf-8')
        for text in ('Automatique (d’après le port', 'STARTTLS — en général port 143', 'port 993 avec « TLS direct »', 'mot de passe d’application'):
            self.assertIn(text, web)

    def test_no_inline_style_blocked_by_the_security_policy(self):
        web = (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8')
        self.assertNotIn('hidden style="display:none"', web)

class WordPreview(unittest.TestCase):
    """Aperçu des modèles Word sans Poppler (poste Windows) et relance de LibreOffice après l'initialisation d'un profil neuf."""

    def test_png_encoder_writes_a_valid_image(self):
        import struct
        import zlib
        from agent.cabinet_docs33 import _png
        width, height = 3, 2
        pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 9, 9, 9, 8, 8, 8, 7, 7, 7])
        data = _png(width, height, width * 3, 3, pixels)
        self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II', data[16:24]), (3, 2))
        idat = data[data.index(b'IDAT') + 4:data.index(b'IEND') - 8]
        self.assertEqual(zlib.decompress(idat), b'\x00' + pixels[:9] + b'\x00' + pixels[9:])

    def test_pdfium_replaces_poppler_and_libreoffice_is_relaunched_once(self):
        from agent import cabinet_docs33 as c
        calls = []

        def run(args, **kw):
            calls.append(args)
            outdir = Path(args[args.index('--outdir') + 1])
            if len(calls) == 2:   # premier lancement : profil neuf seulement, aucun PDF (comportement de LibreOffice sous Windows)
                (outdir / 'input.pdf').write_bytes(b'%PDF-1.4 fictif')

            class R:
                returncode = 0
            return R()

        class Bitmap:
            width, height, stride, n_channels = 2, 2, 6, 3
            buffer = bytes(12)

        class Page:
            def get_size(self):
                return (595.0, 842.0)

            def render(self, scale, rev_byteorder):
                return Bitmap()

        class Doc:
            def __init__(self, path):
                pass

            def __len__(self):
                return 2

            def __getitem__(self, i):
                return Page()

            def close(self):
                pass

        class Pdfium:
            PdfDocument = Doc
        which = {'libreoffice': 'soffice', 'pdftoppm': None, 'pdfinfo': None}
        with tempfile.TemporaryDirectory() as tmp, patch('agent.portable.which', side_effect=lambda n: which[n]), \
                patch('agent.cabinet_docs33._pdfium', return_value=Pdfium), patch('agent.cabinet_docs33.subprocess.run', side_effect=run):
            pages = c.render_pages(b'docx', Path(tmp) / 'apercu', 'original')
            self.assertEqual(pages, 2)
            self.assertEqual(sorted(p.name for p in (Path(tmp) / 'apercu' / 'original').glob('*.png')), ['1.png', '2.png'])
        self.assertEqual(len(calls), 2)
        which = {'libreoffice': 'soffice', 'pdftoppm': None, 'pdfinfo': None}
        with patch('agent.portable.which', side_effect=lambda n: which[n]), patch('agent.cabinet_docs33._pdfium', return_value=None):
            with self.assertRaisesRegex(Stop, 'previsualisation_libreoffice_poppler_requise'):
                c.render_pages(b'docx', Path(tempfile.gettempdir()) / 'inutile', 'original')
        for path in ('agent/documents.py', 'agent/pieces510.py'):
            self.assertIn('for _attempt in (1', (ROOT / path).read_text(encoding='utf-8'), path)

    def test_windows_uses_the_console_libreoffice(self):
        from agent import portable
        with patch('agent.portable.WINDOWS', True), patch('agent.portable._which', return_value=r'C:\LO\program\soffice.exe'), \
                patch('agent.portable.Path.is_file', return_value=True):
            self.assertEqual(portable.which('libreoffice'), r'C:\LO\program\soffice.com')
        with patch('agent.portable.WINDOWS', False), patch('agent.portable._which', return_value='/usr/bin/libreoffice'):
            self.assertEqual(portable.which('libreoffice'), '/usr/bin/libreoffice')


class TaskbarIdentity(unittest.TestCase):
    def test_window_gets_axiorhub_identity_and_real_close_detection(self):
        src = (ROOT / 'windows' / 'marque.py').read_text(encoding='utf-8')
        for needle in ("APP_ID = 'AxiorHub.Pilote'", 'SHGetPropertyStoreForWindow', 'WM_SETICON', 'def _process_tree', "state['closed'] = True"):
            self.assertIn(needle, src)
        poste = (ROOT / 'poste.py').read_text(encoding='utf-8')
        self.assertIn('marque.brand(proc, sys.executable, state=state)', poste)
        self.assertIn("if state.get('seen'):", poste)
        build = (ROOT / 'scripts' / 'build-windows.py').read_text(encoding='utf-8')
        self.assertIn("collect_dynamic_libs('pypdfium2_raw')", build)
        self.assertIn('pypdfium2==', (ROOT / 'scripts' / 'windows-requirements.txt').read_text(encoding='utf-8'))

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows seulement')
    def test_process_tree_contains_the_current_process(self):
        import os
        from windows import marque
        self.assertIn(os.getpid(), marque._process_tree(os.getpid()))


class ConversionRobustness(unittest.TestCase):
    """Défauts trouvés en rejouant la suite sous Windows avec LibreOffice réellement disponible."""

    def test_device_heading_glued_to_previous_footer_is_found(self):
        from agent.hearing import _device
        text = 'Motifs du projet.\nProjet AxiorHub — document à contrôlerPAR CES MOTIFS\nPrononcer la résolution du contrat.'
        found = _device(text)
        self.assertTrue(found['found'])
        self.assertIn('Prononcer la résolution', found['text'])

    def test_output_file_still_locked_is_read_after_a_pause(self):
        from agent import portable
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'sortie.pdf'
            target.write_bytes(b'%PDF fictif')
            real = Path.read_bytes
            state = {'n': 0}

            def flaky(self):
                state['n'] += 1
                if state['n'] < 3:
                    raise PermissionError(13, 'Permission denied')
                return real(self)
            with patch('agent.portable.Path.read_bytes', flaky), patch('time.sleep'):
                self.assertEqual(portable.read_output(target), b'%PDF fictif')
            self.assertEqual(state['n'], 3)
            with patch('agent.portable.Path.read_bytes', side_effect=PermissionError(13, 'x')), patch('time.sleep'):
                with self.assertRaises(PermissionError):
                    portable.read_output(target, tries=2)
        for path in ('agent/documents.py', 'agent/pieces510.py', 'agent/cabinet_docs33.py'):
            self.assertIn('portable.read_output(', (ROOT / path).read_text(encoding='utf-8'), path)

if __name__ == '__main__':
    unittest.main()

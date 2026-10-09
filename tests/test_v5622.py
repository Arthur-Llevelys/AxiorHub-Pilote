"""5.6.22 : dossier de travail local (LocalFolder) avec l'interface du client WebDAV ; réglage validé ; agendas toujours en CalDAV."""
import os
from pathlib import Path
import tempfile
import unittest

from agent import config567
from agent.common import Stop
from agent.dav import DAV, LocalFolder


def cfg(root, **extra):
    return {'local_path': str(root), 'roots': ['/Dossiers'], 'matter_roots': ['/Dossiers'], 'max_depth': 8, 'max_files': 500,
            'max_file_bytes': 15_000_000, 'inventory_page_files': 250, 'max_inventory_directories': 2000, 'max_analysis_files': 5000, **extra}


def seed(root):
    (root / 'Dossiers' / 'ALPHA' / 'Pièces').mkdir(parents=True)
    (root / 'Dossiers' / 'ALPHA' / 'Pièces' / 'P1 contrat.pdf').write_bytes(b'%PDF-1.4 contrat')
    (root / 'Dossiers' / 'ALPHA' / 'note.txt').write_text('note', encoding='utf-8')
    (root / 'Dossiers' / 'ALPHA' / '.cache').mkdir()
    (root / 'Dossiers' / 'ALPHA' / 'secrets').mkdir(); (root / 'Dossiers' / 'ALPHA' / 'secrets' / 'mdp.txt').write_text('x')
    (root / 'Dossiers' / 'BETA').mkdir()
    (root / 'Ailleurs').mkdir(); (root / 'Ailleurs' / 'hors.txt').write_text('hors')


class Backend(unittest.TestCase):
    def test_dav_factory_returns_a_local_folder_that_is_still_a_dav_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); seed(root)
            client = DAV(cfg(root))
            self.assertIsInstance(client, LocalFolder); self.assertIsInstance(client, DAV)
            self.assertIsNone(client.http)
            with self.assertRaisesRegex(Stop, 'agenda_webdav_non_configure'): client.calendars()
            bare = DAV.__new__(DAV); self.assertIsInstance(bare, DAV); self.assertNotIsInstance(bare, LocalFolder)
            with self.assertRaisesRegex(Stop, 'dossier_local_introuvable'): DAV(cfg(root / 'absent'))

    def test_listing_inventory_and_download_follow_the_webdav_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); seed(root); client = DAV(cfg(root))
            items = client.list_folder('/Dossiers/ALPHA')
            self.assertEqual([i['path'] for i in items], ['/Dossiers/ALPHA/Pièces', '/Dossiers/ALPHA/note.txt'])   # .cache et secrets ignorés
            self.assertTrue(items[0]['directory']); self.assertFalse(items[1]['directory'])
            self.assertEqual(items[1]['size'], 4); self.assertTrue(items[1]['etag'].startswith('"')); self.assertIn('GMT', items[1]['modified'])
            files = client.inventory('/Dossiers')
            self.assertEqual(sorted(f['path'] for f in files), ['/Dossiers/ALPHA/Pièces/P1 contrat.pdf', '/Dossiers/ALPHA/note.txt'])
            page, cursor, complete = client.inventory_page('/Dossiers', 0, 1)
            self.assertEqual(len(page), 1); self.assertFalse(complete); self.assertEqual(cursor, 1)
            self.assertEqual(len(client.inventory_large('/Dossiers')), 2)
            state, done = client.inventory_step('/Dossiers'); self.assertTrue(done); self.assertEqual(len(state['files']), 2)
            pdf = next(f for f in files if f['path'].endswith('.pdf'))
            self.assertEqual(client.download(pdf), b'%PDF-1.4 contrat')
            with self.assertRaisesRegex(Stop, 'http_412'): client.download({**pdf, 'etag': '"autre"'})
            meta = client.stat('/Dossiers/ALPHA/note.txt'); self.assertEqual(meta['size'], 4); self.assertEqual(meta['fileid'], '')
            with self.assertRaisesRegex(Stop, 'chemin_est_un_dossier'): client.stat('/Dossiers/ALPHA')
            with self.assertRaisesRegex(Stop, 'fichier_hors_racines'): client.list_folder('/Ailleurs')
            with self.assertRaisesRegex(Stop, 'chemin_refuse'): client.list_folder('/Dossiers/../Ailleurs')
            with self.assertRaisesRegex(Stop, 'repertoire_exclu'): client.list_folder('/Dossiers/ALPHA/secrets')
            with self.assertRaisesRegex(Stop, 'http_404'): client.list_folder('/Dossiers/GAMMA')
            self.assertTrue(client.file_web_url('/Dossiers/ALPHA/note.txt').startswith('file:'))

    def test_writes_are_exclusive_bounded_and_keep_previous_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); seed(root); client = DAV(cfg(root))
            self.assertEqual(client.put_file('/Dossiers/ALPHA/Projet.docx', b'v1'), '/Dossiers/ALPHA/Projet.docx')
            with self.assertRaisesRegex(Stop, 'http_412'): client.put_file('/Dossiers/ALPHA/Projet.docx', b'v2')
            with self.assertRaisesRegex(Stop, 'http_409'): client.put_file('/Dossiers/ALPHA/Inexistant/x.docx', b'v')
            with self.assertRaisesRegex(Stop, 'fichier_hors_racines'): client.put_file('/Ailleurs/x.docx', b'v')
            meta = client.stat('/Dossiers/ALPHA/Projet.docx')
            with self.assertRaisesRegex(Stop, 'version_nextcloud_modifiee'): client.replace_file('/Dossiers/ALPHA/Projet.docx', b'v2', '"perime"')
            client.replace_file('/Dossiers/ALPHA/Projet.docx', b'v2', meta['etag'])
            self.assertEqual((root / 'Dossiers' / 'ALPHA' / 'Projet.docx').read_bytes(), b'v2')
            versions = list((root / 'Dossiers' / 'ALPHA' / '.axiorhub-versions').iterdir())
            self.assertEqual(len(versions), 1); self.assertEqual(versions[0].read_bytes(), b'v1')
            self.assertNotIn('.axiorhub-versions', [Path(i['path']).name for i in client.list_folder('/Dossiers/ALPHA')])
            self.assertEqual(client.ensure_folder('/Dossiers/ALPHA/20_Actes/90_Brouillons', '/Dossiers/ALPHA'), '/Dossiers/ALPHA/20_Actes/90_Brouillons')
            self.assertTrue((root / 'Dossiers' / 'ALPHA' / '20_Actes' / '90_Brouillons').is_dir())
            with self.assertRaisesRegex(Stop, 'destination_hors_dossier'): client.ensure_folder('/Dossiers/BETA/x', '/Dossiers/ALPHA')
            with self.assertRaisesRegex(Stop, 'creation_racine_refusee'): client.create_folder('/Dossiers')
            self.assertEqual(client.create_folder('/Dossiers/GAMMA'), '/Dossiers/GAMMA')
            with self.assertRaisesRegex(Stop, 'http_412'): client.create_folder('/Dossiers/GAMMA')
            if os.name == 'posix':
                os.symlink(str(root / 'Ailleurs' / 'hors.txt'), str(root / 'Dossiers' / 'ALPHA' / 'lien.txt'))
                self.assertNotIn('/Dossiers/ALPHA/lien.txt', [i['path'] for i in client.list_folder('/Dossiers/ALPHA')]); self.assertIn('lien.txt', client.refused)


class Setting(unittest.TestCase):
    def test_local_path_setting_requires_an_existing_absolute_directory_or_stays_empty(self):
        self.assertEqual(config567._validate('local_dir', '', 'nextcloud.local_path'), '')
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(config567._validate('local_dir', tmp, 'nextcloud.local_path'), str(Path(tmp).expanduser()))
            with self.assertRaisesRegex(Stop, 'dossier_local_invalide'): config567._validate('local_dir', str(Path(tmp) / 'absent'), 'nextcloud.local_path')
        with self.assertRaisesRegex(Stop, 'dossier_local_invalide'): config567._validate('local_dir', 'relatif/dossier', 'nextcloud.local_path')
        with self.assertRaisesRegex(Stop, 'dossier_local_invalide'): config567._validate('local_dir', Path(tempfile.gettempdir()).anchor, 'nextcloud.local_path')
        labels = {row[0]: row for row in config567.FIELDS}
        self.assertEqual(labels['nextcloud.local_path'][2], 'local_dir'); self.assertEqual(labels['nextcloud.local_path'][4], 'Nextcloud')


if __name__ == '__main__':
    unittest.main()

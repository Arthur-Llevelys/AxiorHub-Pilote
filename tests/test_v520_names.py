"""5.2.0 : les dossiers s'affichent sous leur nom exact de dossier Nextcloud (ex. « ALPHA - SAS EXEMPLE - CONSEIL - 20240101001 »)."""
from pathlib import Path
import json
import unittest

from agent.common import matter_display, private_json
import test_v510 as t510

FULL = 'ALPHA - SAS EXEMPLE - CONSEIL - 20240101001'


class Names(unittest.TestCase):
    setUp0 = t510.WebTests.setUp
    request = t510.WebTests.request

    def setUp(self):
        self.setUp0()
        rows = [{**t510.DEMO, 'id': '20240101001', 'client_name': 'ALPHA', 'path': '/Dossiers/' + FULL, 'references': ['20240101001'], 'aliases': ['ALPHA']},
                {**t510.DEMO, 'id': '2024020201', 'client_name': 'BRAVO', 'path': '/Dossiers/BRAVO - SCI LES CHENES - 2024020201', 'references': [], 'aliases': []}]
        private_json(Path(self.f.c['matters_file']), rows)

    def test_display_uses_the_folder_name(self):
        self.assertEqual(matter_display({'id': '20240101001', 'client_name': 'ALPHA', 'path': '/CABINET EXEMPLE/01 - Dossiers/' + FULL}), FULL)
        self.assertEqual(matter_display({'id': 'X1', 'client_name': 'Client', 'path': ''}), 'Client — X1')

    def test_dropdowns_show_full_names_in_alphabetical_order(self):
        for path in ('/documents', '/honoraires', '/pieces', '/echeances', '/recherche'):
            body = self.request(path)['body']
            self.assertIn(FULL, body, path)
            self.assertNotIn('ALPHA — 20240101001', body, path)
        body = self.request('/documents')['body']
        self.assertLess(body.index(FULL), body.index('BRAVO - SCI LES CHENES - 2024020201'))


if __name__ == '__main__':
    unittest.main()

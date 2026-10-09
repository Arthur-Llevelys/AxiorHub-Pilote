"""5.6.21 : adresses du réseau du cabinet pour les services locaux ; concentrateur Paramètres (six rubriques, un seul endroit par réglage)."""
from pathlib import Path
import unittest

from agent import config567, parametres5621
from agent.common import HTTP, Stop
import test_v5614 as t14

ROOT = Path(__file__).resolve().parents[1]


class CabinetNetwork(unittest.TestCase):
    def test_private_networks_are_local_enough_public_hosts_are_not(self):
        for url in ('http://192.168.1.115:11434', 'http://10.0.0.5:8880', 'http://172.20.3.4:9011', 'http://127.0.0.1:11434', 'http://ollama:11434',
                    'http://serveur.local:11434', 'http://[fd00::10]:11434'):
            HTTP(url, local_only=True, local_hosts=('ollama',))
        for url in ('http://8.8.8.8:11434', 'http://arbitrary-public-host.test:11434', 'https://api.example.test'):
            with self.assertRaises(Stop):
                HTTP(url, local_only=True)
        with self.assertRaises(Stop):               # hors services locaux, le réseau privé n'exempte pas de HTTPS
            HTTP('http://192.168.1.115:8080')
        self.assertEqual(config567._validate('local_url', 'http://192.168.1.115:11434/', 'ollama.url'), 'http://192.168.1.115:11434')
        with self.assertRaises(Stop):
            config567._validate('local_url', 'http://arbitrary-public-host.test:11434', 'ollama.url')
        labels = {row[0]: row[1] for row in config567.FIELDS}
        self.assertIn('réseau du cabinet', labels['ollama.url']); self.assertIn('réseau du cabinet', labels['speech568.url'])


class Hub(t14.Base):
    def _get(self, path):
        path, _, query = path.partition('?')
        out = self.request(path, query=query)
        self.assertEqual(out['status'][:3], '200', path)
        return out['body']

    def test_each_rubrique_renders_its_settings_once(self):
        body = self._get('/parametres')
        self.assertIn('class="p5621-tabs"', body); self.assertIn('id="connections567-form"', body)
        self.assertIn('data-config567="ollama.url"', body); self.assertNotIn('data-config567="speech568.provider"', body)   # voix ailleurs
        self.assertIn('réseau du cabinet', body)
        body = self._get('/parametres?rubrique=ia')
        self.assertIn('id="economie569"', body); self.assertNotIn('id="connections567-form"', body)
        body = self._get('/parametres?rubrique=voix')
        self.assertIn('data-config567="speech568.provider"', body); self.assertNotIn('data-config567="ollama.url"', body)
        self.assertIn('id="assistant567-preferences"', body); self.assertIn('Voix et conversation rapide', body); self.assertIn('id="voix"', body)
        self.assertNotIn('Accueil téléphonique et WhatsApp', body); self.assertNotIn('Variables Docker', body)
        self.assertEqual(body.count('id="connections567-form"'), 1); self.assertEqual(body.count('id="config567-catalog"'), 1)
        body = self._get('/parametres?rubrique=routines')
        self.assertIn('Heures', body); self.assertIn('name="roles"', body); self.assertNotIn('Voix et conversation rapide', body)
        self.assertNotIn('class="ws-tabs"', body)
        body = self._get('/parametres?rubrique=documents')
        self.assertIn('id="ax-office-settings"', body); self.assertIn('Rôle et procédure par dossier', body)
        body = self._get('/parametres?rubrique=cabinet')
        self.assertIn('Accueil téléphonique et WhatsApp', body); self.assertIn('id="envoi"', body); self.assertIn('id="clavier"', body)
        self.assertNotIn('id="voix"', body); self.assertNotIn('id="assistant567-preferences"', body)

    def test_old_addresses_open_the_matching_rubrique(self):
        for path, marker in (('/parametres/connexions', 'data-config567="mail.host"'), ('/parametres/assistant', 'id="assistant567-preferences"'),
                             ('/parametres/proactivite', 'name="roles"'), ('/ia-externe', 'id="economie569"'), ('/confort', 'id="clavier"'),
                             ('/atelier/reglages', 'id="ax-office-settings"'), ('/parametres/agendas', 'Rôle et procédure par dossier'),
                             ('/parametres?tab=automatismes', 'name="roles"')):
            body = self._get(path)
            self.assertIn('class="p5621-tabs"', body, path); self.assertIn(marker, body, path)
        self.assertEqual(parametres5621.rubrique_for('/parametres', {'rubrique': 'inconnue'}), 'connexions')

    def test_tools_menu_no_longer_lists_the_merged_pages(self):
        from agent.shell501 import TOOLS
        paths = {p for p, _, _ in TOOLS}
        for old in ('/parametres/agendas', '/parametres/proactivite', '/ia-externe', '/confort', '/atelier/reglages'):
            self.assertNotIn(old, paths)
        self.assertIn('/mise-en-service', paths); self.assertIn('/parametres/agents', paths)


if __name__ == '__main__':
    unittest.main()

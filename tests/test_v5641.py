"""5.6.41 : version poste (Windows 11, Ubuntu poste) utilisable avec l'application Nextcloud en MODE DÉCOUVERTE — appels acceptés depuis le
poste lui-même seulement, avertissement transmis à l'application, refus explicite d'un appel relayé ; application 0.4.2 (bandeau, « Mon
AxiorHub local »), prérequis, étapes et mode découverte dans la description de la boutique et le README.

Données d'essai fictives (domaines example.test).
"""
from datetime import datetime, timedelta, timezone
import json
import os
import re
from pathlib import Path
import unittest
import unittest.mock
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from agent import __version__

import test_v5637 as t37

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'nextcloud-app' / 'axiorhub_pilote'
DISC_FR = 'Mode découverte : AxiorHub est installé sur un poste de travail'
DISC_EN = 'Discovery mode: AxiorHub is installed on a workstation'


class WorkstationEngine(t37._Linked):
    """Version poste : mode découverte depuis ce même ordinateur ; tout appel relayé reçoit un refus explicite."""

    def poste(self):
        return {**self.server, 'poste': True}

    def call(self, method, path, auth, body=b'', remote='127.0.0.1'):
        from agent.integration5634 import handle
        return handle(self.desk, method, path, {'axiorhub.auth': auth, 'REMOTE_ADDR': remote}, body)

    def signed_local(self, key, path, body):
        from agent.integration5634 import handle, sign
        ctx = {'instance': 'personnel:' + key['user'], 'user': 'alice', 'scope': [], 'op': 'POST ' + path,
               'exp': (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), 'nonce': 'p%031d' % 1}
        env = {'HTTP_X_AXIORHUB_CONTEXT': sign(key['secret'], ctx), 'HTTP_X_AXIORHUB_KEY': key['key_id'], 'axiorhub.auth': self.poste(),
               'REMOTE_ADDR': '127.0.0.1'}
        return handle(self.desk, 'POST', path, env, json.dumps(body).encode())

    def test_workstation_engine_works_in_discovery_mode_from_this_computer(self):
        status, out = self.call('GET', '/integration/v1/health', self.poste())
        self.assertEqual(status, '200 OK', out)
        self.assertEqual(out['mode'], 'poste')
        for mark in ('Mode découverte', 'uniquement aux tests et à la découverte', 'ne sont pas chiffrés', 'installez la version serveur'):
            self.assertIn(mark, out['avertissement'])
        status, key = self.call('POST', '/integration/v1/pair', self.poste(),
                                json.dumps({'login': 'admin', 'password': self.password, 'label': 'alice'}).encode())
        self.assertEqual(status, '200 OK', key)
        status, out = self.signed_local(key, '/integration/v1/session', {'suite': '/dossiers', 'origin': 'http://localhost:8080'})
        self.assertEqual(status, '200 OK', out)
        self.assertEqual(out['mode'], 'poste')
        self.assertIn('nextcloud-session', out['url'])
        status, out = self.call('GET', '/integration/v1/health', self.server, remote='192.0.2.20')    # serveur : inchangé
        self.assertEqual(status, '200 OK', out)
        self.assertNotIn('mode', out)

    def test_relayed_call_to_a_workstation_is_refused_with_a_clear_message(self):
        for method, path, body in (('GET', '/integration/v1/health', b''),
                                   ('POST', '/integration/v1/pair', json.dumps({'login': 'admin', 'password': 'x', 'label': 'alice'}).encode()),
                                   ('POST', '/integration/v1/session', b'{}')):
            status, out = self.call(method, path, self.poste(), body, remote='192.0.2.20')
            self.assertTrue(status.startswith('409'), (path, status))
            self.assertEqual(out['code'], 'moteur_poste_non_pris_en_charge')
            self.assertIn('utilisable seulement en mode découverte', out['message'])
            self.assertIn('Mon AxiorHub local', out['message'])
            self.assertIn('version serveur', out['message'])
        with unittest.mock.patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': 'C:/fictif/poste'}):
            self.assertTrue(self.call('GET', '/integration/v1/health', self.server, remote='192.0.2.20')[0].startswith('409'))
            self.assertEqual(self.call('GET', '/integration/v1/health', self.server)[1].get('mode'), 'poste')

    def test_session_cookie_is_accepted_in_the_frame_on_this_computer_only(self):
        from agent.nextcloud5637 import _cookie
        for origin in ('http://127.0.0.1:8769', 'http://localhost:8769', 'https://cabinet.example.test'):
            self.assertIn('Secure; SameSite=None', _cookie('sid', {'origin': origin, 'prefix': '/agent-courriel'}, 60), origin)
        self.assertIn('SameSite=Lax', _cookie('sid', {'origin': 'http://cabinet.example.test', 'prefix': '/agent-courriel'}, 60))

    def test_docker_desktop_alias_only_for_integration_calls_on_a_workstation(self):
        from agent.web import docker_alias
        origin = urlsplit('http://127.0.0.1:8769')
        env = {'HTTP_HOST': 'host.docker.internal:8769', 'REMOTE_ADDR': '127.0.0.1', 'PATH_INFO': '/agent-courriel/integration/v1/health'}
        self.assertTrue(docker_alias(env, {'poste': True}, origin))
        for change, auth in (({}, {}), ({'REMOTE_ADDR': '192.0.2.20'}, {'poste': True}), ({'PATH_INFO': '/agent-courriel/dossiers'}, {'poste': True}),
                             ({'HTTP_HOST': 'host.docker.internal:8770'}, {'poste': True}), ({'HTTP_HOST': 'autre.example.test:8769'}, {'poste': True}),
                             ({'HTTP_HOST': 'host.docker.internal:x'}, {'poste': True})):
            self.assertFalse(docker_alias({**env, **change}, auth, origin), (change, auth))

    def test_service_worker_never_intercepts_the_nextcloud_frame(self):
        from agent.mobile490 import service_worker
        js = service_worker('/agent-courriel')
        guard = js.index("req.destination === 'iframe' || req.destination === 'frame') return;")
        self.assertLess(guard, js.index("req.mode === 'navigate'"))                                  # avant toute interception

    def test_settings_page_explains_discovery_mode_on_the_workstation(self):
        from agent.integration5636 import settings_html
        server = settings_html(self.desk, '/agent-courriel')
        self.assertNotIn(DISC_FR, server)
        self.assertIn('Autoriser la liaison des comptes', server)
        auth = {'poste': True, 'origin': 'http://127.0.0.1:8769', 'prefix': '/agent-courriel', 'username': 'admin'}
        html = settings_html(self.desk, '/agent-courriel', auth)
        for mark in (DISC_FR, 'Nextcloud installé sur ce même ordinateur', 'http://127.0.0.1:8769/agent-courriel',
                     'http://host.docker.internal:8769/agent-courriel', 'Nextcloud hébergé', 'Adresse de mon AxiorHub local',
                     'Autoriser la liaison des comptes depuis Nextcloud (même ordinateur)', 'Afficher l’identifiant et le mot de passe locaux'):
            self.assertIn(mark, html)
        with unittest.mock.patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': 'C:/fictif/poste'}):
            html = settings_html(self.desk, '/agent-courriel')
        self.assertIn(DISC_FR, html)
        self.assertNotIn('Afficher l’identifiant', html)


class NextcloudApp042(unittest.TestCase):
    """Application 0.4.2 : bandeau du mode découverte, « Mon AxiorHub local », prérequis, étapes et mode découverte (boutique, README)."""

    def setUp(self):
        if not APP.exists():
            self.skipTest('application Nextcloud absente de cette distribution')

    def read(self, rel):
        return (APP / rel).read_text(encoding='utf-8')

    def test_app_shows_the_discovery_banner_and_the_local_link(self):
        for rel in ('src/App.vue', 'src/components/SimpleView.vue', 'src/personal.js', 'src/admin.js'):
            text = self.read(rel)
            self.assertIn('moteur_poste_non_pris_en_charge', text, rel)
            self.assertIn('it can only be used in discovery mode', text, rel)
        for rel in ('templates/personal.php', 'templates/admin.php'):
            self.assertIn('only works in discovery mode, for tests', self.read(rel), rel)
        personal = self.read('templates/personal.php')
        for mark in ('My local AxiorHub (discovery mode)', DISC_EN, 'Address of my local AxiorHub', 'http://127.0.0.1:8769/agent-courriel/'):
            self.assertIn(mark, personal)
        app = self.read('src/App.vue')
        for mark in ('discovery.value = !!session.poste', DISC_EN, "view === 'local'", 'Open my local AxiorHub', "'noopener,noreferrer'"):
            self.assertIn(mark, app)
        link = self.read('lib/Service/UserLink.php')
        self.assertIn('function checkLocalUrl', link)
        self.assertIn("'host.docker.internal'", link)
        self.assertIn("'personal#local'", self.read('appinfo/routes.php'))
        self.assertIn("'poste' => ($body['mode'] ?? '') === 'poste'", self.read('lib/Service/EmbedSession.php'))
        fr = json.loads(self.read('l10n/fr.json'))['translations']
        self.assertTrue(any(isinstance(v, str) and v.startswith(DISC_FR) for v in fr.values()))
        self.assertEqual(fr['My local AxiorHub'], 'Mon AxiorHub local')
        self.assertFalse(any('non pris en charge' in v for v in fr.values() if isinstance(v, str)))
        built = self.read('js/axiorhub_pilote-main.mjs')
        self.assertIn('moteur_poste_non_pris_en_charge', built)                                        # interface compilée à jour
        self.assertIn('noopener,noreferrer', built)

    def test_store_description_and_readme_show_requirements_steps_and_discovery_mode(self):
        info = ET.parse(APP / 'appinfo' / 'info.xml').getroot()
        self.assertRegex(info.find('version').text, r'^0\.4\.[2-9]$')   # 5.6.42 : version suivante de l'application admise
        descriptions = {d.get('lang'): d.text for d in info.findall('description')}
        for lang, marks in (('en', ('## Before you start: requirements', '## Discovery mode: AxiorHub installed on a workstation (tests only)',
                                    'Warning — tests and discovery only.', 'Address of my local AxiorHub', 'host.docker.internal',
                                    '## Step 1 — Install the AxiorHub server version on Linux', '## Step 2 — Install this app from the Nextcloud App Store',
                                    '## Step 3 — Link each lawyer', 'occ app:install axiorhub_pilote', 'Ubuntu 24.04 LTS or Debian 12', '--branch v5.6.')),
                            ('fr', ('## Avant de commencer : prérequis', '## Mode découverte : AxiorHub installé sur un poste de travail (tests seulement)',
                                    'Avertissement — tests et découverte seulement.', 'Adresse de mon AxiorHub local',
                                    '## Étape 1 — Installer la version serveur d\'AxiorHub sur Linux',
                                    '## Étape 2 — Installer cette application depuis la boutique Nextcloud', '## Étape 3 — Relier le compte',
                                    'Applications › Bureautique et texte › AxiorHub Pilote', '--branch v5.6.'))):
            for mark in marks:
                self.assertIn(mark, descriptions[lang], (lang, mark))
        for text in descriptions.values():   # 5.6.43 : tag publié du moteur, pas plus ancien que 5.6.42 ni plus récent que ce moteur
            for patch in re.findall(r'--branch v5\.6\.(\d+)', text):
                self.assertTrue(42 <= int(patch) <= int(__version__.split('.')[2]), patch)
        self.assertNotIn('Not supported', descriptions['en'])
        self.assertNotIn('non pris en charge', descriptions['fr'])
        self.assertTrue(all(len(s.text) <= 128 for s in info.findall('summary')))
        # README en français et en anglais : ce que fait l'application, configuration, étapes, mode découverte, captures fictives
        for rel, marks in (('README.md', ("## Ce que fait l'application", '## Quelle configuration choisir ?', '## Prérequis', '## Étape 1',
                                          '## Étape 2', '### Sans la boutique', '## Étape 3',
                                          '## Mode découverte : AxiorHub installé sur un poste de travail (tests seulement)',
                                          'jamais pour la production', '## En cas de difficulté', '[English](README.en.md)')),
                           ('README.en.md', ('## What the app does', '## Which setup should I choose?', '## Requirements', '## Step 1',
                                             '## Step 2', '### Without the App Store', '## Step 3',
                                             '## Discovery mode: AxiorHub installed on a workstation (tests only)',
                                             'never for production', '## If something does not work', '[Français](README.md)'))):
            readme = self.read(rel)
            for mark in marks:
                self.assertIn(mark, readme, (rel, mark))
            positions = [readme.index(m) for m in marks[:8]]
            self.assertEqual(positions, sorted(positions), rel)                                       # dans l'ordre de lecture
            for shot in re.findall(r'\]\((screenshots/[^)]+\.png)\)', readme):
                self.assertTrue((APP / shot).is_file(), shot)


class EngineReadme5641(unittest.TestCase):
    """README du moteur en quatre langues : installation serveur, Windows 11, Ubuntu, premier réglage, application Nextcloud ; captures
    actualisées par langue, toutes présentes."""

    def test_readmes_show_install_steps_and_current_screenshots(self):
        heads = {'README.md': ('## Installer', '### 1. Version serveur (Linux) — recommandée', '### 2. Poste Windows 11', '### 3. Poste Ubuntu 24.04',
                               '### 4. Premier réglage', '### 5. Application Nextcloud', '### Installation complète sur un VPS'),
                 'README.en.md': ('## Installation', '### 1. Server version (Linux) — recommended', '### 2. Windows 11 workstation',
                                  '### 3. Ubuntu 24.04 workstation', '### 4. First settings', '### 5. Nextcloud app', '### Full installation on a VPS'),
                 'README.de.md': ('## Installation', '### 1. Serverversion (Linux) — empfohlen', '### 2. Arbeitsplatz Windows 11',
                                  '### 3. Arbeitsplatz Ubuntu 24.04', '### 4. Erste Einstellungen', '### 5. Nextcloud-App', '### Vollständige Installation auf einem VPS'),
                 'README.es.md': ('## Instalar', '### 1. Versión servidor (Linux) — recomendada', '### 2. Puesto Windows 11', '### 3. Puesto Ubuntu 24.04',
                                  '### 4. Primeros ajustes', '### 5. Aplicación Nextcloud', '### Instalación completa en un VPS')}
        for name, marks in heads.items():
            text = (ROOT / name).read_text(encoding='utf-8')
            positions = [text.index(m) for m in marks]
            self.assertEqual(positions, sorted(positions), name)
            for cmd in ('AxiorHub-Pilote-Setup-%s.exe' % __version__, 'axiorhub-pilote-poste_%s_amd64.deb' % __version__, 'sudo bash install.sh',
                        'install-interface.py'):
                self.assertIn(cmd, text, (name, cmd))
            shots = re.findall(r'src="(docs/captures/[^"]+)"', text)
            lang = {'README.md': 'fr'}.get(name, name.split('.')[1])
            self.assertGreaterEqual(len(shots), 9, name)
            for shot in shots:
                self.assertTrue(shot.startswith('docs/captures/%s/' % lang), (name, shot))
                self.assertTrue((ROOT / shot).is_file(), shot)


if __name__ == '__main__':
    unittest.main()

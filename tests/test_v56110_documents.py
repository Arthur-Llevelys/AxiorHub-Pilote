"""5.6.110 (lot S) : « Installer le serveur de documents sur ce poste » (AxiorHub pilote Docker).

Faux Docker (``_lancer`` remplacée) : tous les états (Docker absent, arrêté, sans droit, prêt ; conteneur absent, étranger, arrêté, en
marche, sain ; image présente ou non), commandes exactes du guide (arguments en liste, étiquette, port de bouclage, fichier
d'environnement, image figée, ``--pull never``, ``--add-host`` sous Linux seulement), port pris (par le système ou refusé par Docker),
conteneur étranger du même nom jamais touché, échec à chaque étape et reprise, réglages existants non écrasés sans confirmation et remis,
désinstallation, droits, poste seulement, idempotence, audit sans secret, carte, traductions, guide. Aucun réseau, aucun vrai Docker ;
données fictives.
"""
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from agent import documents56110 as D, portable   # noqa: E402
from agent import web568, web_rules568   # noqa: E402,F401 — liés à web567.actor par son nom : chargés avant tout remplacement de cette fonction
from agent.common import Stop   # noqa: E402

ROOT = TESTS.parent
ORIGINE = 'http://127.0.0.1:8769'
AUTH = {'origin': ORIGINE, 'prefix': '/agent-courriel', 'poste': True, 'csrf': 'jeton'}
SERVEUR = {'origin': 'https://agent.example.test', 'prefix': '/agent-courriel', 'csrf': 'jeton'}
CHECKS_OK = [{'name': n, 'ok': True, 'detail': '', 'fix': ''} for n in (
    'Serveur joignable (healthcheck)', 'Bibliothèque api.js', 'Découverte des formats (discovery)', 'Intégration dans cette page (iframe)',
    'Secret JWT et API de commande', 'Le serveur de documents joint AxiorHub')]


class FauxDocker:
    """Remplace ``D._lancer`` : un moteur Docker en mémoire (conteneurs, image, ports), journal des appels, pannes au choix."""

    def __init__(self):
        self.moteur = 'pret'                 # 'pret', 'arrete', 'permission'
        self.present = True
        self.image = True
        self.conteneurs = {}                 # nom -> {'id', 'labels', 'en_marche', 'port', 'args'}
        self.ports_docker = set()            # ports refusés par Docker (« port is already allocated »)
        self.pannes = {}                     # verbe -> message d'erreur
        self.appels = []
        self.sante = True
        self.n = 0

    def verbes(self):
        return [a[1] if a[1] not in ('container', 'image') else a[1] + ' ' + a[2] for a, _ in self.appels]

    def __call__(self, args, delai):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args), args
        assert delai and delai > 0
        self.appels.append((list(args), delai))
        if not self.present:
            return 127, '', 'docker introuvable'
        a = args[1:]
        if self.moteur == 'arrete':
            return 1, 'Client: 29.8.2\n', 'error during connect: Get "http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.51/version": open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.'
        if self.moteur == 'permission':
            return 1, '', 'permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock'
        verbe = a[0]
        if verbe in self.pannes and verbe != 'run':
            return 1, '', self.pannes[verbe]
        if verbe == 'version':
            return 0, '29.8.2\n', ''
        if verbe == 'image' and a[1] == 'inspect':
            assert a[-1] == D.IMAGE
            return (0, D.EMPREINTE + '\n', '') if self.image else (1, '[]', 'Error response from daemon: No such image: ' + D.IMAGE)
        if verbe == 'image' and a[1] == 'rm':
            if 'rmi' in self.pannes:
                return 1, '', self.pannes['rmi']
            self.image = False
            return 0, 'Untagged: x\n', ''
        if verbe == 'pull':
            self.image = True
            return 0, 'Status: Downloaded newer image\n', ''
        if verbe == 'container' and a[1] == 'inspect':
            c = self.conteneurs.get(a[2])
            if not c:
                return 1, '[]\n', 'Error response from daemon: No such container: ' + a[2]
            return 0, json.dumps([{'Id': c['id'], 'Config': {'Labels': c['labels'], 'Image': D.IMAGE},
                                   'State': {'Running': c['en_marche'], 'Status': 'running' if c['en_marche'] else 'exited'},
                                   'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(c['port'])}]} if c['port'] else {}}}]), ''
        if verbe == 'run':
            nom = a[a.index('--name') + 1]
            if nom in self.conteneurs:
                return 125, '', 'Conflict. The container name "/%s" is already in use' % nom
            port = int(a[a.index('-p') + 1].split(':')[1])
            labels = dict(x.split('=', 1) for x in [a[i + 1] for i, v in enumerate(a) if v == '--label'])
            self.n += 1
            c = {'id': 'c0ffee%04d' % self.n + 'a' * 54, 'labels': labels, 'en_marche': False, 'port': port, 'args': a}
            self.conteneurs[nom] = c
            if port in self.ports_docker:
                return 125, 'c0ffee\n', 'docker: Error response from daemon: ports are not available: exposing port TCP 127.0.0.1:%d -> 0.0.0.0:0: listen tcp 127.0.0.1:%d: bind: Only one usage of each socket address is normally permitted.' % (port, port)
            if 'run' in self.pannes:
                return 125, '', self.pannes['run']
            c['en_marche'] = True
            return 0, c['id'] + '\n', ''
        if verbe in ('start', 'stop', 'rm'):
            cible = a[-1]
            for nom, c in list(self.conteneurs.items()):
                if c['id'] == cible:
                    if verbe == 'rm':
                        assert not c['en_marche'], 'retrait d’un conteneur en marche'
                        del self.conteneurs[nom]
                    else:
                        c['en_marche'] = verbe == 'start'
                    return 0, cible + '\n', ''
            return 1, '', 'Error response from daemon: No such container: ' + cible
        raise AssertionError('commande inattendue : %r' % (a,))

    def http(self, url, delai):
        m = re.fullmatch(r'http://127\.0\.0\.1:(\d+)/healthcheck', url)
        assert m, url
        port = int(m.group(1))
        if self.sante and any(c['en_marche'] and c['port'] == port for c in self.conteneurs.values()):
            return 200, b'true'
        return 0, b''


def env_de(corps=b'', csrf='jeton', methode='POST', origine=ORIGINE):
    return {'REQUEST_METHOD': methode, 'HTTP_ORIGIN': origine, 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
            'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}


class _Banc(unittest.TestCase):
    windows = True

    def setUp(self):
        import test_agent as fixtures
        from agent.desk import Desk
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.state = Path(self.d.c['state_dir'])
        self.docker = FauxDocker()
        self.diag = []
        self.ports_os = set()

        def diagnostic(desk, auth, probe=None, reach=False):
            self.diag.append({'auth': dict(auth), 'reach': reach})
            return [dict(c) for c in (self.checks if hasattr(self, 'checks') else CHECKS_OK)]
        for p in (patch.object(D, '_lancer', self.docker), patch.object(D, '_http', self.docker.http), patch.object(D, '_pause', lambda s: None),
                  patch.object(D, 'executable', lambda: '/opt/faux/docker'), patch.object(D, '_port_libre', lambda p: p not in self.ports_os),
                  patch('agent.office440.diagnostic', diagnostic), patch.object(portable, 'WINDOWS', self.windows),
                  patch.dict(D._TACHE, {'en_cours': False, 'action': '', 'etapes': [], 'erreur': '', 'state_dir': None, 'fil': None, 'debut': 0.0, 'fin': 0.0}),
                  patch('agent.common.HTTP.request', side_effect=AssertionError('réseau'))):
            p.start()
            self.addCleanup(p.stop)

    def api(self, action, data=None, role='administrateur', owner='admin', csrf='jeton', methode='POST', auth=AUTH):
        corps = json.dumps(data or {}).encode()
        with patch('agent.web567.actor', return_value=(owner, role)):
            out = D.api(env_de(corps, csrf, methode, auth['origin']), self.d, auth, '', D.PREFIXE + action, {}, methode)
        return out['status'], json.loads(out['body']), out

    def ecrire(self, action, cle, **data):
        data.setdefault('version', D.installation(self.d)['version'])
        return self.api(action, dict(data, cle=cle))

    def attendre(self):
        fil = D._TACHE.get('fil')
        if fil is not None:
            fil.join(20)
            self.assertFalse(fil.is_alive())

    def audits(self, prefixe='documents56110'):
        return [(r[0], json.loads(r[1])) for r in self.d.db.execute("SELECT action, data FROM audit WHERE action LIKE ? ORDER BY id", (prefixe + '%',))]

    def office(self):
        from agent import office440
        return office440.settings(self.d), office440.jwt_secret(self.d)

    def installer(self, **data):
        code, corps, _ = self.ecrire('installer', 'cle-install-%s' % os.urandom(4).hex(), **data)
        self.attendre()
        return code, corps

    def autre_serveur(self):
        from agent import office440
        office440.save_settings(self.d, 'https://documents.example.test', 'secret-du-serveur-commun-0001', 'https://agent.example.test/agent-courriel', 'onlyoffice')


# ================================================================================================ état
class Etat(_Banc):
    def test_S01_docker_absent_arrete_permission_pret(self):
        self.docker.present = False
        self.assertEqual(D.etat(self.d, AUTH)['docker'], 'absent')
        with patch.object(D, 'executable', lambda: None):
            self.assertEqual(D.etat(self.d, AUTH)['docker'], 'absent')
        self.docker.present = True
        self.docker.moteur = 'arrete'
        self.assertEqual(D.etat(self.d, AUTH)['docker'], 'arrete')
        self.docker.moteur = 'permission'
        self.assertEqual(D.etat(self.d, AUTH)['docker'], 'permission')
        self.docker.moteur = 'pret'
        s = D.etat(self.d, AUTH)
        self.assertEqual((s['docker'], s['version'], s['conteneur'], s['image']), ('pret', '29.8.2', 'absent', True))
        version = [a for a, _ in self.docker.appels if a[1] == 'version'][0]
        self.assertEqual(version, ['/opt/faux/docker', 'version', '--format', '{{.Server.Version}}'])
        self.assertTrue(all(d <= 20 for a, d in self.docker.appels))    # délais courts pour la lecture de l'état

    def test_S01_delai_depasse(self):
        def lent(args, delai):
            raise Stop('docker_delai_depasse')
        with patch.object(D, '_lancer', lent):
            self.assertEqual(D.etat(self.d, AUTH)['docker'], 'delai')

    def test_S01_conteneur_etranger_arrete_en_marche_sain_et_image(self):
        self.docker.conteneurs[D.NOM] = {'id': 'f' * 64, 'labels': {}, 'en_marche': True, 'port': 8080}
        self.assertEqual(D.etat(self.d, AUTH)['conteneur'], 'etranger')
        self.docker.conteneurs[D.NOM] = {'id': 'a' * 64, 'labels': {'axiorhub': 'autre'}, 'en_marche': True, 'port': 8080}
        self.assertEqual(D.etat(self.d, AUTH)['conteneur'], 'etranger')
        self.docker.conteneurs[D.NOM] = {'id': 'b' * 64, 'labels': {'axiorhub': 'documents'}, 'en_marche': False, 'port': 8083}
        s = D.etat(self.d, AUTH)
        self.assertEqual((s['conteneur'], s['port']), ('arrete', 8083))
        self.docker.conteneurs[D.NOM]['en_marche'] = True
        self.docker.sante = False
        self.assertEqual(D.etat(self.d, AUTH)['conteneur'], 'en_marche')
        self.docker.sante = True
        self.assertEqual(D.etat(self.d, AUTH)['conteneur'], 'sain')
        self.docker.image = False
        self.assertFalse(D.etat(self.d, AUTH)['image'])

    def test_S01_lecture_sans_effet_de_bord(self):
        avant = [list(r) for r in self.d.db.execute('SELECT * FROM settings ORDER BY key')]
        self.docker.conteneurs[D.NOM] = {'id': 'b' * 64, 'labels': {'axiorhub': 'documents'}, 'en_marche': True, 'port': 8080}
        D.etat(self.d, AUTH)
        D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertEqual([list(r) for r in self.d.db.execute('SELECT * FROM settings ORDER BY key')], avant)
        self.assertTrue(set(self.docker.verbes()) <= {'version', 'container inspect', 'image inspect'}, self.docker.verbes())
        self.assertFalse((self.state / 'documents56110').exists())

    def test_S06_serveur_aucun_appel_a_docker(self):
        for auth in (SERVEUR, {'origin': 'http://192.0.2.10:8769', 'poste': True, 'prefix': '/agent-courriel'}, {'origin': ORIGINE}, None):
            self.assertFalse(D.est_poste(auth))
            self.assertFalse(D.etat(self.d, auth)['poste'])
            self.assertEqual(D.carte_html(self.d, '/p', auth, 'administrateur'), '')
        self.assertEqual(self.docker.appels, [])
        self.assertTrue(D.est_poste(AUTH))


# ================================================================================================ installation
class Installation(_Banc):
    def test_S03_commandes_du_guide_et_reglages_remplis_seuls(self):
        code, corps = self.installer()
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(D._TACHE['erreur'], '')
        self.assertEqual([x['etat'] for x in D._TACHE['etapes']], ['saute', 'fait', 'fait', 'fait', 'fait', 'fait'])   # image déjà là
        self.assertNotIn('pull', self.docker.verbes())                   # image présente : rien n'est téléchargé
        run = [a for a, _ in self.docker.appels if a[1] == 'run']
        self.assertEqual(len(run), 1)
        env = str(D.chemin_env(str(self.state)))
        self.assertEqual(run[0], ['/opt/faux/docker', 'run', '-d', '--name', 'axiorhub-documents', '--label', 'axiorhub=documents', '--restart', 'unless-stopped',
                                  '--pull', 'never', '-p', '127.0.0.1:8080:80', '--env-file', env,
                                  'ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2'])
        texte = Path(env).read_text(encoding='utf-8')
        self.assertRegex(texte, r'^JWT_ENABLED=true\nJWT_SECRET=[A-Za-z0-9_-]{40,}\n$')
        secret = D.lire_secret(str(self.state))
        if os.name != 'nt':                                               # droits 600 (sous Windows : profil de l'utilisateur)
            self.assertEqual(os.stat(env).st_mode & 0o777, 0o600)
        cfg, jwt = self.office()
        self.assertEqual((cfg['server_url'], cfg['callback_base'], cfg['engine']), ('http://127.0.0.1:8080', 'http://host.docker.internal:8769/agent-courriel', 'euro-office'))
        self.assertEqual(jwt, secret)                                      # secret rangé comme le secret existant (office440.jwt)
        self.assertEqual(self.diag[-1], {'auth': {'origin': ORIGINE, 'prefix': '/agent-courriel', 'poste': True}, 'reach': True})
        inst = D.installation(self.d)
        self.assertEqual((inst['port'], inst['image_tiree']), (8080, False))
        self.assertEqual(D.etat(self.d, AUTH)['conteneur'], 'sain')
        actions = [a for a, _ in self.audits()]
        self.assertEqual(actions, ['documents56110_installation_demandee', 'documents56110_installation_termine'])
        # secret jamais dans l'audit, les réglages, l'état ni la carte
        tout = json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM audit')], ensure_ascii=False)
        tout += json.dumps([list(r) for r in self.d.db.execute('SELECT * FROM settings')], ensure_ascii=False)
        tout += json.dumps(D.etat(self.d, AUTH), default=str) + D.carte_html(self.d, '/p', AUTH, 'administrateur')
        tout += json.dumps(self.api('etat', methode='GET')[1])
        self.assertNotIn(secret, tout)

    def test_S03_linux_add_host(self):
        with patch.object(portable, 'WINDOWS', False):
            self.installer()
        run = [a for a, _ in self.docker.appels if a[1] == 'run'][0]
        i = run.index('--add-host')
        self.assertEqual(run[i + 1], 'host.docker.internal:host-gateway')
        self.assertLess(run.index('-p'), i)
        self.assertLess(i, run.index('--env-file'))

    def test_S03_image_absente_telechargee_une_fois(self):
        self.docker.image = False
        self.installer()
        pulls = [a for a, d in self.docker.appels if a[1] == 'pull']
        self.assertEqual(pulls, [['/opt/faux/docker', 'pull', D.IMAGE]])
        self.assertEqual([d for a, d in self.docker.appels if a[1] == 'pull'], [3600])
        self.assertTrue(D.installation(self.d)['image_tiree'])
        self.assertEqual(D._TACHE['etapes'][0]['etat'], 'fait')

    def test_S03_port_pris_par_le_systeme_ou_refuse_par_docker(self):
        self.ports_os = {8080}
        self.docker.ports_docker = {8081}
        self.installer()
        runs = [a for a, _ in self.docker.appels if a[1] == 'run']
        self.assertEqual([r[r.index('-p') + 1] for r in runs], ['127.0.0.1:8081:80', '127.0.0.1:8082:80'])
        self.assertIn('rm', self.docker.verbes())                       # conteneur créé sur le port refusé : retiré (il est le nôtre)
        self.assertEqual(self.office()[0]['server_url'], 'http://127.0.0.1:8082')
        self.assertEqual(D.installation(self.d)['port'], 8082)
        self.assertEqual(list(self.docker.conteneurs.values())[0]['port'], 8082)

    def test_S03_aucun_port_libre(self):
        self.ports_os = set(D.PORTS)
        code, corps = self.installer()
        self.assertEqual(D._TACHE['erreur'], 'port_pris')
        self.assertNotIn('run', self.docker.verbes())
        self.assertIn('8080 à 8099', D.MESSAGES['port_pris'])

    def test_S04_conteneur_etranger_jamais_touche(self):
        self.docker.conteneurs[D.NOM] = {'id': 'f' * 64, 'labels': {}, 'en_marche': True, 'port': 8080}
        for action in ('installer', 'desinstaller', 'demarrer', 'arreter'):
            code, corps, _ = self.ecrire(action, 'cle-etranger-' + action)
            self.assertEqual((code, corps['error']), ('409 Conflict', 'conteneur_etranger'), action)
        self.assertTrue(set(self.docker.verbes()) <= {'version', 'container inspect', 'image inspect'}, self.docker.verbes())
        self.assertTrue(self.docker.conteneurs[D.NOM]['en_marche'])
        with self.assertRaisesRegex(Stop, 'conteneur_etranger'):
            D._retirer_conteneur({'id': 'f' * 64, 'notre': False, 'en_marche': True})
        html = D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertIn('sans l’étiquette d’AxiorHub', html)
        self.assertNotIn('m56110/documents/installer', html)
        # le conteneur étranger apparaît pendant l'installation (entre la demande et le démarrage) : refus, rien n'est touché
        del self.docker.conteneurs[D.NOM]
        vrai = D.lire_secret

        def apparait(state_dir):
            self.docker.conteneurs[D.NOM] = {'id': 'e' * 64, 'labels': {}, 'en_marche': False, 'port': None}
            return vrai(state_dir)
        with patch.object(D, 'lire_secret', apparait):
            self.installer()
        self.assertEqual(D._TACHE['erreur'], 'conteneur_etranger')
        self.assertNotIn('run', self.docker.verbes())
        self.assertNotIn('start', self.docker.verbes())

    def test_S03_echec_a_chaque_etape_puis_reprise(self):
        cas = (('image', 'reseau_coupe', lambda: (setattr(self.docker, 'image', False), self.docker.pannes.update(pull='Error response from daemon: Get "https://ghcr.io/v2/": dial tcp: lookup ghcr.io: no such host'))),
               ('image', 'image_introuvable', lambda: (setattr(self.docker, 'image', False), self.docker.pannes.update(pull='Error response from daemon: manifest unknown'))),
               ('image', 'disque_plein', lambda: (setattr(self.docker, 'image', False), self.docker.pannes.update(pull='write /var/lib/docker/tmp: no space left on device'))),
               ('demarrage', 'docker_en_echec', lambda: self.docker.pannes.update(run='docker: Error response from daemon: something odd.')),
               ('attente', 'serveur_sans_reponse', lambda: (setattr(self.docker, 'sante', False), setattr(D, 'DELAI_DEMARRAGE', 0))),
               ('reglages', 'reglages_refuses', lambda: setattr(self, 'panne_reglages', True)),
               ('test', 'test_en_echec', lambda: setattr(self, 'checks', CHECKS_OK[:5] + [dict(CHECKS_OK[5], ok=False)])))
        from agent import office440
        vrai_save = office440.save_settings

        def save(desk, *a, **k):
            if getattr(self, 'panne_reglages', False):
                raise Stop('url_editeur_invalide')
            return vrai_save(desk, *a, **k)
        delai = D.DELAI_DEMARRAGE
        for etape, motif, panne in cas:
            with self.subTest(etape=etape, motif=motif), patch('agent.office440.save_settings', save):
                self.docker.conteneurs.clear()
                self.docker.image, self.docker.sante, self.docker.pannes = True, True, {}
                self.panne_reglages = False
                D.effacer_nos_reglages(self.d, 8080)                     # cas précédent : ses réglages ne comptent pas comme « autre serveur »
                if hasattr(self, 'checks'):
                    del self.checks
                D.retirer_env(str(self.state))
                panne()
                self.installer()
                self.assertEqual(D._TACHE['erreur'], motif)
                etats = {x['id']: x['etat'] for x in D._TACHE['etapes']}
                self.assertEqual(etats[etape], 'echec')
                ordre = list(D.ETAPES_INSTALLATION)
                self.assertTrue(all(etats[x] == 'attente' for x in ordre[ordre.index(etape) + 1:]))
                self.assertEqual(self.audits()[-1][0], 'documents56110_installation_echec')
                self.assertEqual(self.audits()[-1][1]['etape'], etape)
                self.assertEqual(self.audits()[-1][1]['motif'], motif)
                self.assertFalse(D._VERROU.locked())
                secret_avant = D.lire_secret(str(self.state))
                conteneur_avant = dict(self.docker.conteneurs.get(D.NOM) or {})
                # reprise : la cause corrigée, les étapes faites ne sont pas refaites
                self.docker.pannes, self.docker.sante, self.panne_reglages = {}, True, False
                D.DELAI_DEMARRAGE = delai
                if hasattr(self, 'checks'):
                    del self.checks
                n_pull = self.docker.verbes().count('pull')
                n_run = self.docker.verbes().count('run')
                self.installer()
                self.assertEqual(D._TACHE['erreur'], '', etape)
                if etape not in ('image',):
                    self.assertEqual(self.docker.verbes().count('pull'), n_pull)
                if conteneur_avant:
                    self.assertEqual(self.docker.verbes().count('run'), n_run, etape)   # conteneur repris, pas recréé
                    self.assertEqual(self.docker.conteneurs[D.NOM]['id'], conteneur_avant['id'])
                if secret_avant:
                    self.assertEqual(D.lire_secret(str(self.state)), secret_avant)
                self.assertEqual(self.office()[1], D.lire_secret(str(self.state)))
        D.DELAI_DEMARRAGE = delai

    def test_S03_conteneur_arrete_pendant_le_demarrage(self):
        vrai = self.docker.http

        def tombe(url, delai):
            for c in self.docker.conteneurs.values():
                c['en_marche'] = False
            return 0, b''
        with patch.object(D, '_http', tombe):
            self.installer()
        self.assertEqual(D._TACHE['erreur'], 'serveur_arrete')
        self.assertIn('docker logs axiorhub-documents', D.MESSAGES['serveur_arrete'])
        with patch.object(D, '_http', vrai):
            self.installer()                                               # reprise : le conteneur (le nôtre) est redémarré
        self.assertEqual(D._TACHE['erreur'], '')
        self.assertIn('start', self.docker.verbes())

    def test_S03_secret_perdu_conteneur_recree(self):
        self.installer()
        ancien = self.docker.conteneurs[D.NOM]['id']
        D.retirer_env(str(self.state))
        self.installer()
        self.assertNotEqual(self.docker.conteneurs[D.NOM]['id'], ancien)   # recréé avec le nouveau fichier : secrets identiques partout
        self.assertEqual(self.office()[1], D.lire_secret(str(self.state)))

    def test_S03_reglages_existants_non_ecrases_sans_confirmation(self):
        self.autre_serveur()
        code, corps, _ = self.ecrire('installer', 'cle-autre-0001')
        self.assertEqual((code, corps['error']), ('409 Conflict', 'reglages_existants'))
        self.assertNotIn('run', self.docker.verbes())
        self.assertEqual(self.office()[0]['server_url'], 'https://documents.example.test')
        html = D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertIn('Un autre serveur de documents est déjà réglé', html)
        self.assertIn('<code>https://documents.example.test</code>', html)
        self.assertIn('name="remplacer"', html)
        code, corps = self.installer(remplacer=True)
        self.assertEqual(code, '200 OK', corps)
        self.assertEqual(D._TACHE['erreur'], '')
        self.assertEqual(self.office()[0]['server_url'], 'http://127.0.0.1:8080')
        p = self.d.settings(D.KEY_PRECEDENT)
        self.assertEqual((p['server_url'], p['callback_base'], p['engine'], p['secret']),
                         ('https://documents.example.test', 'https://agent.example.test/agent-courriel', 'onlyoffice', True))
        self.assertNotIn('secret-du-serveur-commun', json.dumps(p))
        self.assertIn(('documents56110_reglages_gardes', {'serveur': 'https://documents.example.test', 'secret': True}),
                      [(a, {k: v for k, v in d.items() if k != 'auteur'}) for a, d in self.audits()])
        self.installer()                                                   # reprise : le précédent n'est pas remplacé par nos réglages
        self.assertEqual(self.d.settings(D.KEY_PRECEDENT)['server_url'], 'https://documents.example.test')
        html = D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertIn('m56110/documents/revenir', html)
        code, corps, _ = self.ecrire('revenir', 'cle-revenir-0001')
        self.assertEqual(code, '200 OK', corps)
        cfg, jwt = self.office()
        self.assertEqual((cfg['server_url'], cfg['callback_base'], cfg['engine'], jwt),
                         ('https://documents.example.test', 'https://agent.example.test/agent-courriel', 'onlyoffice', 'secret-du-serveur-commun-0001'))
        self.assertTrue(self.docker.conteneurs[D.NOM]['en_marche'])        # le conteneur n'est pas touché
        code, corps, _ = self.ecrire('revenir', 'cle-revenir-0002')
        self.assertEqual(corps['error'], 'pas_de_precedent')

    def test_S03_reglages_precedents_sans_secret(self):
        from agent import office440
        office440.save_settings(self.d, 'https://documents.example.test', '', '', 'onlyoffice')
        self.installer(remplacer=True)
        self.assertTrue(self.office()[1])
        self.assertTrue(D.rendre_precedent(self.d))
        self.assertEqual(self.office(), (office440.settings(self.d), ''))
        self.assertEqual(self.office()[0]['server_url'], 'https://documents.example.test')


# ================================================================================================ démarrer, arrêter, tester, désinstaller
class Actions(_Banc):
    def test_S04_demarrer_arreter_tester(self):
        self.installer()
        cid = self.docker.conteneurs[D.NOM]['id']
        code, corps, _ = self.ecrire('arreter', 'cle-arret-0001')
        self.assertEqual(code, '200 OK', corps)
        self.assertFalse(self.docker.conteneurs[D.NOM]['en_marche'])
        self.assertIn(['/opt/faux/docker', 'stop', '-t', '30', cid], [a for a, _ in self.docker.appels])
        self.assertEqual(self.ecrire('arreter', 'cle-arret-0002')[1]['error'], 'deja_arrete')
        self.assertIn('m56110/documents/demarrer', D.carte_html(self.d, '/p', AUTH, 'administrateur'))
        code, corps, _ = self.ecrire('demarrer', 'cle-demarre-01')
        self.assertEqual(code, '200 OK', corps)
        self.assertIn(['/opt/faux/docker', 'start', cid], [a for a, _ in self.docker.appels])
        self.checks = CHECKS_OK[:5] + [dict(CHECKS_OK[5], ok=False)]
        code, corps, _ = self.ecrire('tester', 'cle-tester-001')
        self.assertEqual((code, corps['message']), ('200 OK', 'Test en échec : 1 étape(s) sur 6.'))
        html = D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertIn('<li>Le serveur de documents joint AxiorHub</li>', html)
        del self.checks
        code, corps, _ = self.ecrire('tester', 'cle-tester-002')
        self.assertEqual(corps['message'], 'Test réussi : toutes les étapes sont cochées.')
        self.assertIn('Dernier test : toutes les étapes sont cochées.', D.carte_html(self.d, '/p', AUTH, 'administrateur'))
        self.docker.pannes['start'] = 'error during connect: open //./pipe/dockerDesktopLinuxEngine'
        self.ecrire('arreter', 'cle-arret-0003')
        code, corps, _ = self.ecrire('demarrer', 'cle-demarre-02')
        self.assertEqual(corps['error'], 'docker_arrete')

    def test_S04_desinstaller_garde_l_image_deja_presente(self):
        self.installer()
        cid = self.docker.conteneurs[D.NOM]['id']
        html = D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertIn('L’image était déjà sur ce poste avant l’installation', html)
        self.assertNotIn('name="retirer_image"', html)
        code, corps, _ = self.ecrire('desinstaller', 'cle-desinst-01', retirer_image=True)   # demandé, mais l'image n'est pas d'AxiorHub
        self.assertEqual(code, '200 OK', corps)
        self.attendre()
        self.assertEqual(D._TACHE['erreur'], '')
        self.assertEqual(self.docker.conteneurs, {})
        self.assertEqual([a for a, _ in self.docker.appels if a[1] in ('stop', 'rm')][-2:], [['/opt/faux/docker', 'stop', '-t', '30', cid], ['/opt/faux/docker', 'rm', cid]])
        self.assertTrue(self.docker.image)
        self.assertNotIn('image rm', self.docker.verbes())
        self.assertFalse(D.chemin_env(str(self.state)).exists())
        cfg, jwt = self.office()
        self.assertEqual((cfg['server_url'], jwt), ('', ''))
        self.assertEqual([x['etat'] for x in D._TACHE['etapes']], ['fait', 'fait', 'saute', 'fait'])
        self.assertEqual(self.audits()[-1][0], 'documents56110_desinstallation_termine')
        self.assertIn('Serveur de documents : non installé.', D.carte_html(self.d, '/p', AUTH, 'administrateur'))

    def test_S04_desinstaller_avec_image_tiree_et_precedent(self):
        self.docker.image = False
        self.autre_serveur()
        self.installer(remplacer=True)
        self.assertIn('name="retirer_image"', D.carte_html(self.d, '/p', AUTH, 'administrateur'))
        self.ecrire('desinstaller', 'cle-desinst-02', retirer_image=True)
        self.attendre()
        self.assertEqual(D._TACHE['erreur'], '')
        self.assertIn(['/opt/faux/docker', 'image', 'rm', D.IMAGE], [a for a, _ in self.docker.appels])
        self.assertFalse(self.docker.image)
        cfg, jwt = self.office()
        self.assertEqual((cfg['server_url'], jwt), ('https://documents.example.test', 'secret-du-serveur-commun-0001'))   # précédents remis
        self.assertFalse(D.installation(self.d)['image_tiree'])

    def test_S04_desinstaller_image_utilisee_par_un_autre_conteneur(self):
        self.docker.image = False
        self.installer()
        self.docker.pannes['rmi'] = 'Error response from daemon: conflict: unable to remove repository reference (must force) - container 1234 is using its referenced image'
        self.ecrire('desinstaller', 'cle-desinst-03', retirer_image=True)
        self.attendre()
        self.assertEqual(D._TACHE['erreur'], 'image_utilisee')
        self.assertEqual(self.docker.conteneurs, {})
        self.assertTrue(self.docker.image)

    def test_S04_desinstaller_sans_conteneur(self):
        D.ecrire_env(str(self.state), 'secret-de-test-0123456789')
        self.ecrire('desinstaller', 'cle-desinst-04')
        self.attendre()
        self.assertEqual(D._TACHE['erreur'], '')
        self.assertEqual([x['etat'] for x in D._TACHE['etapes']], ['saute', 'fait', 'saute', 'fait'])
        self.assertFalse(D.chemin_env(str(self.state)).exists())
        self.assertNotIn('rm', self.docker.verbes())

    def test_S04_conteneur_echange_entre_arret_et_retrait(self):
        self.installer()
        vrai = D.conteneur
        appels = {'n': 0}

        def echange():
            appels['n'] += 1
            if appels['n'] == 3:   # un autre conteneur du même nom a pris la place (après l'arrêt, avant le retrait)
                self.docker.conteneurs[D.NOM] = {'id': 'd' * 64, 'labels': {'axiorhub': 'documents'}, 'en_marche': False, 'port': 8080}
            return vrai()
        with patch.object(D, 'conteneur', echange):
            self.ecrire('desinstaller', 'cle-desinst-05')
            self.attendre()
        self.assertEqual(D._TACHE['erreur'], 'conteneur_etranger')
        self.assertIn(D.NOM, self.docker.conteneurs)


# ================================================================================================ routes, droits, idempotence
class Routes(_Banc):
    def test_S05_droits_poste_csrf_cle_version(self):
        code, corps, out = self.api('installer', {'version': 0, 'cle': 'cle-avocat-0001'}, role='avocat', owner='avocat1')
        self.assertEqual((code, corps['error']), ('403 Forbidden', 'role_insuffisant'))
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertEqual(self.api('etat', methode='GET', role='assistant', owner='a1')[0], '403 Forbidden')
        code, corps, _ = self.api('installer', {'version': 0, 'cle': 'cle-serveur-001'}, auth=SERVEUR)
        self.assertEqual((code, corps['error']), ('403 Forbidden', 'reserve_au_poste'))
        self.assertEqual(self.api('etat', methode='GET', auth=SERVEUR)[1]['error'], 'reserve_au_poste')
        code, corps, _ = self.api('installer', {'version': 0, 'cle': 'cle-csrf-00001'}, csrf='faux')
        self.assertEqual(code, '403 Forbidden')
        self.assertEqual(self.api('installer', {'version': 0})[1]['error'], 'cle_requise')
        self.assertEqual(self.api('installer', {'version': 7, 'cle': 'cle-version-01'})[1]['error'], 'version_perimee')
        self.assertEqual(self.api('inconnu', {})[0], '404 Not Found')
        self.assertEqual(self.api('installer', methode='GET')[0], '404 Not Found')
        self.assertEqual(self.docker.appels, [])
        corps = json.dumps({'version': 0, 'cle': 'cle-longue-001', 'x': 'y' * 5000}).encode()
        with patch('agent.web567.actor', return_value=('admin', 'administrateur')):
            out = D.api(env_de(corps), self.d, AUTH, '', D.PREFIXE + 'installer', {}, 'POST')
        self.assertEqual(out['status'], '413 Payload Too Large')

    def test_S05_idempotence_et_tache_unique(self):
        data = {'version': 0, 'cle': 'cle-idem-00001'}
        code, corps, _ = self.api('installer', data)
        self.attendre()
        self.assertEqual(code, '200 OK', corps)
        n = len(self.docker.appels)
        self.assertEqual(self.api('installer', data)[1], corps)           # même clé, même demande : réponse gardée, rien de rejoué
        self.assertEqual(len(self.docker.appels), n)
        self.assertEqual(self.api('installer', dict(data, remplacer=True))[1]['error'], 'cle_reutilisee')
        self.assertTrue(D._VERROU.acquire(blocking=False))
        try:
            D._TACHE['en_cours'] = True
            v = D.installation(self.d)['version']
            self.assertEqual(self.api('desinstaller', {'version': v, 'cle': 'cle-tache-0001'})[1]['error'], 'tache_en_cours')
            with self.assertRaisesRegex(Stop, 'tache_en_cours'):
                D.lancer('installation', self.d.c, AUTH)
        finally:
            D._TACHE['en_cours'] = False
            D._VERROU.release()

    def test_S05_docker_absent_ou_arrete_refus_lisibles(self):
        for moteur, present, erreur in (('pret', False, 'docker_absent'), ('arrete', True, 'docker_arrete'), ('permission', True, 'docker_permission')):
            with self.subTest(erreur):
                self.docker.moteur, self.docker.present = moteur, present
                code, corps, _ = self.ecrire('installer', 'cle-%s' % erreur.replace('_', '-'))
                self.assertEqual((code, corps['error'], corps['message']), ('503 Service Unavailable', erreur, D.MESSAGES[erreur]))
        self.assertNotIn('run', self.docker.verbes())

    def test_S05_etat_par_la_route_et_suivi_sans_docker(self):
        code, corps, _ = self.api('etat', methode='GET')
        self.assertEqual((code, corps['docker'], corps['conteneur'], corps['poste']), ('200 OK', 'pret', 'absent', True))
        n = len(self.docker.appels)
        with patch('agent.web567.actor', return_value=('admin', 'administrateur')):
            out = D.api(env_de(methode='GET'), self.d, AUTH, '', D.PREFIXE + 'etat?tache=1', {}, 'GET')
        self.assertEqual(set(json.loads(out['body'])), {'poste', 'systeme', 'tache'})
        self.assertEqual(len(self.docker.appels), n)                        # le suivi des étapes n'appelle pas Docker


# ================================================================================================ sécurité du lanceur
class Lanceur(unittest.TestCase):
    def test_S05_liste_seulement_delai_et_client_absent(self):
        with self.assertRaises(TypeError):
            D._lancer('docker ps', 5)
        with self.assertRaises(TypeError):
            D._lancer(['docker', 5], 5)
        code, out, err = D._lancer([sys.executable, '-c', 'import sys; print("ok"); sys.stderr.write("rien"); raise SystemExit(3)'], 30)
        self.assertEqual((code, out.strip(), err), (3, 'ok', 'rien'))
        with self.assertRaisesRegex(Stop, 'docker_delai_depasse'):
            D._lancer([sys.executable, '-c', 'import time; time.sleep(30)'], 1)
        self.assertEqual(D._lancer([str(Path(tempfile.gettempdir()) / 'introuvable-axh-docker')], 5)[0], 127)

    def test_S05_source_sans_shell_ni_valeur_du_navigateur(self):
        src = (ROOT / 'agent' / 'documents56110.py').read_text(encoding='utf-8')
        for interdit in ('shell=True', 'os.system', 'os.popen', 'Popen(', "' '.join(args"):
            self.assertNotIn(interdit, src)
        self.assertEqual(D.IMAGE, 'ghcr.io/euro-office/documentserver@sha256:2fadc1b3dc99250d6576fc0c0f34349db6725b752b08e91db9831fbb803fc5c2')
        guide = (ROOT / 'docs' / 'INSTALLATION-EDITEUR-DOCUMENTS.md').read_text(encoding='utf-8')
        self.assertIn(D.IMAGE, guide)
        self.assertIn('-p 127.0.0.1:8080:80', guide)
        self.assertIn('--add-host host.docker.internal:host-gateway', guide)
        self.assertEqual((D.NOM, D.ETIQUETTE, D.PORTS[0], D.PORTS[-1]), ('axiorhub-documents', 'axiorhub=documents', 8080, 8099))
        ecrire = src[src.index('def ecrire('):src.index('# ================================================================================================ présentation')]
        for champ in re.findall(r"d\.get\('([a-z_]+)'\)", ecrire):
            self.assertIn(champ, ('cle', 'version', 'remplacer', 'retirer_image'))   # seuls champs lus : jamais dans les arguments de docker
        self.assertTrue(all(isinstance(v, int) and v > 0 for v in D.DELAIS.values()))

    def test_S05_causes_lisibles(self):
        for texte, code in (('Bind for 127.0.0.1:8080 failed: port is already allocated', 'port_pris'),
                            ('ports are not available: exposing port TCP 127.0.0.1:8080', 'port_pris'),
                            ('error during connect: open //./pipe/dockerDesktopLinuxEngine', 'docker_arrete'),
                            ('Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?', 'docker_arrete'),
                            ('permission denied while trying to connect to the Docker daemon socket', 'docker_permission'),
                            ('manifest unknown', 'image_introuvable'), ('dial tcp: lookup ghcr.io: no such host', 'reseau_coupe'),
                            ('net/http: TLS handshake timeout', 'reseau_coupe'), ('no space left on device', 'disque_plein'),
                            ('autre chose', 'docker_en_echec')):
            self.assertEqual(D.cause(texte), code, texte)
            self.assertIn(code, D.MESSAGES)

    def test_S05_port_libre_reel(self):
        import socket
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen(1)
        try:
            self.assertFalse(D._port_libre(s.getsockname()[1]))
        finally:
            s.close()

    def test_S05_fichier_d_environnement_prive_et_lecture(self):
        tmp = Path(tempfile.mkdtemp(prefix='axh-lots-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertEqual(D.lire_secret(str(tmp)), '')
        D.ecrire_env(str(tmp), 'secret-fictif-0123456789')
        self.assertEqual(D.chemin_env(str(tmp)).read_bytes(), b'JWT_ENABLED=true\nJWT_SECRET=secret-fictif-0123456789\n')
        self.assertEqual(D.lire_secret(str(tmp)), 'secret-fictif-0123456789')
        self.assertEqual([p.name for p in D.base(str(tmp)).iterdir()], ['documents.env'])   # aucun fichier temporaire restant
        D.chemin_env(str(tmp)).write_text('JWT_SECRET=court\n', encoding='utf-8')
        self.assertEqual(D.lire_secret(str(tmp)), '')
        D.retirer_env(str(tmp))
        D.retirer_env(str(tmp))
        self.assertFalse(D.chemin_env(str(tmp)).exists())


# ================================================================================================ écran, traductions, guide
class Ecran(_Banc):
    def carte(self, role='administrateur', auth=AUTH):
        return D.carte_html(self.d, '/p', auth, role)

    def test_S02_docker_absent_windows_et_ubuntu(self):
        self.docker.present = False
        html = self.carte()
        self.assertIn('href="https://www.docker.com/products/docker-desktop/"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn('Vérifier de nouveau', html)
        self.assertNotIn('m56110/documents/installer', html)
        with patch.object(portable, 'WINDOWS', False):
            html = self.carte()
        self.assertIn('<code>sudo apt update &amp;&amp; sudo apt install -y docker.io &amp;&amp; sudo usermod -aG docker &quot;$USER&quot;</code>', html)
        self.assertNotIn('docker-desktop', html)

    def test_S02_docker_arrete(self):
        self.docker.moteur = 'arrete'
        html = self.carte()
        self.assertIn('Ouvrez Docker Desktop, attendez « Engine running », puis cliquez sur « Vérifier de nouveau ».', html)
        self.assertNotIn('data-api', html)

    def test_S02_docker_pret_bouton_taille_et_source(self):
        html = self.carte()
        self.assertIn('data-api="m56110/documents/installer"', html)
        self.assertIn('Installer le serveur de documents sur ce poste', html)
        self.assertIn('L’image est déjà sur ce poste : rien à télécharger.', html)
        self.assertIn('<code>%s</code>' % D.IMAGE, html)
        self.docker.image = False
        self.assertIn('plusieurs gigaoctets', self.carte())
        self.assertEqual(self.carte('avocat'), '')
        self.assertEqual(self.carte(auth=SERVEUR), '')

    def test_S02_carte_en_tete_de_la_section_1_et_suivi(self):
        from agent import parametres5621
        cap = parametres5621.Capture()
        with patch('agent.web567.actor', return_value=('admin', 'administrateur')):
            body = parametres5621._documents(self.d, AUTH, '/p', {'QUERY_STRING': ''}, cap)
        i = body.index('<h2>1 · Modifier les documents dans l’interface</h2>')
        self.assertTrue(body[i + len(D.MARQUE):].startswith('<section class="ds110-carte" id="documents56110"'))
        self.assertLess(body.index('id="documents56110"'), body.index('id="of-engine"'))
        self.assertIn('/p/static/v56110-documents.js', cap.head)
        for auth, role in ((SERVEUR, 'administrateur'), (AUTH, 'avocat')):
            cap = parametres5621.Capture()
            with patch('agent.web567.actor', return_value=('x', role)):
                body = parametres5621._documents(self.d, auth, '/p', {'QUERY_STRING': ''}, cap)
            self.assertNotIn('documents56110', body + cap.head)
        D._TACHE.update({'en_cours': True, 'action': 'installation', 'state_dir': str(self.state),
                         'etapes': [{'id': x, 'etat': 'fait' if x == 'image' else ('en_cours' if x == 'secret' else 'attente'), 'duree': 0} for x in D.ETAPES_INSTALLATION]})
        html = self.carte()
        self.assertIn('data-en-cours="1"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('<li data-etape="secret" data-etat="en_cours">', html)
        self.assertIn(' disabled>', html)
        D._TACHE.update({'en_cours': False, 'erreur': 'reseau_coupe'})
        html = self.carte()
        self.assertIn(D.MESSAGES['reseau_coupe'], html)
        self.assertIn('les étapes déjà faites ne sont pas refaites', html)

    def test_S07_traductions_completes(self):
        import test_v56107_langues as langues
        from agent import ecrans5632 as E
        tables = {l: E.dictionary(l) for l in langues.LANGUES}
        textes = list(D.MESSAGES.values()) + list(D.TEXTES.values()) + list(D.LIBELLES_ETAPES.values()) + list(D.ETATS_ETAPE.values())
        textes += [c['name'] for c in CHECKS_OK]
        textes += ['Installation lancée : suivez les étapes dans la carte.', 'Désinstallation lancée.', 'Réglages du serveur de documents précédent remis.',
                   'Serveur de documents démarré : il répond dans une à deux minutes.', 'Serveur de documents arrêté.',
                   'Test réussi : toutes les étapes sont cochées.', 'Test en échec : 1 étape(s) sur 6.']
        manquants = ['%s (%s)' % (t, l) for t in textes for l, table in tables.items() if E.phrase(t, table) is None]
        self.assertEqual(manquants, [])
        pages = []
        self.docker.present = False
        pages.append(self.carte())
        with patch.object(portable, 'WINDOWS', False):
            pages.append(self.carte())
        self.docker.present = True
        for moteur in ('arrete', 'permission'):
            self.docker.moteur = moteur
            pages.append(self.carte())
            with patch.object(portable, 'WINDOWS', False):
                pages.append(self.carte())
        self.docker.moteur = 'pret'
        self.autre_serveur()
        self.docker.image = False
        pages.append(self.carte())
        self.installer(remplacer=True)
        self.checks = CHECKS_OK[:5] + [dict(CHECKS_OK[5], ok=False)]
        self.ecrire('tester', 'cle-trad-00001')
        pages.append(self.carte())
        with patch.object(portable, 'WINDOWS', False):
            pages.append(self.carte())
        self.ecrire('arreter', 'cle-trad-00002')
        pages.append(self.carte())
        D._TACHE.update({'en_cours': True, 'action': 'desinstallation', 'state_dir': str(self.state), 'erreur': '',
                         'etapes': [{'id': x, 'etat': 'attente', 'duree': 0} for x in D.ETAPES_DESINSTALLATION]})
        pages.append(self.carte())
        D._TACHE.update({'en_cours': False, 'erreur': 'image_utilisee'})
        pages.append(self.carte())
        self.docker.conteneurs[D.NOM]['labels'] = {}
        pages.append(self.carte())
        for html in pages:
            for lang in langues.LANGUES:
                self.assertEqual(langues.reste_francais(langues.visibles(E.translate_html(html, lang))), [], (lang, html[:300]))
            self.assertEqual(E.translate_html(html, 'fr'), html)


class Statique(unittest.TestCase):
    def test_S05_droits_declares(self):
        for action in D.ECRITURES:
            self.assertTrue(D.allowed('administrateur', 'POST', D.PREFIXE + action))
            for role in ('avocat', 'assistant', 'client'):
                self.assertFalse(D.allowed(role, 'POST', D.PREFIXE + action))
            self.assertFalse(D.allowed('administrateur', 'GET', D.PREFIXE + action))
        self.assertTrue(D.allowed('administrateur', 'GET', D.PREFIXE + 'etat'))
        self.assertFalse(D.allowed('avocat', 'GET', D.PREFIXE + 'etat'))
        from agent import standalone_auth, web440
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56110/documents/installer'))
        self.assertTrue(standalone_auth.allowed('avocat', 'GET', '/api440/m56110/documents/etat'))   # lectures ouvertes au contrôle central : le service refuse (Routes)
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56110/documents/desinstaller'))
        self.assertIn("name.startswith('m56110/documents/')", (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8'))
        for f in ('/static/v56110-documents.js', '/static/v56110-documents.css'):
            self.assertIn(f, web440.STATIC)
            self.assertTrue((ROOT / 'agent' / f.lstrip('/')).is_file())
        from agent.shell501 import BUNDLE
        self.assertIn('v56110-documents.css', BUNDLE)
        self.assertIn('/* ==== v56110-documents.css ==== */', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))

    def test_S05_script_sans_texte_a_traduire(self):
        import test_v56107_langues as langues
        path = ROOT / 'agent' / 'static' / 'v56110-documents.js'
        self.assertEqual([s for _, s in langues.litteraux_js(path) if langues.texte_visible_js(s)], [])
        js = path.read_text(encoding='utf-8')
        self.assertIn("/api440/m56110/documents/etat?tache=1", js)
        for interdit in ('innerHTML', 'eval(', 'localStorage', 'sessionStorage', 'console.log'):
            self.assertNotIn(interdit, js)

    def test_S07_guide_et_aide(self):
        from agent import aide5631
        self.assertEqual(aide5631.check(), [])
        for lang, mot in (('fr', 'Installer le serveur de documents sur ce poste'), ('en', 'Install the document server on this computer'),
                          ('de', 'Dokumentenserver auf diesem Arbeitsplatz installieren'), ('es', 'Instalar el servidor de documentos en este equipo')):
            fiche = json.loads((ROOT / 'agent' / 'aide' / ('%s.json' % lang)).read_text(encoding='utf-8'))['documents']
            self.assertTrue(any(mot in s for s in fiche['steps']), lang)
            self.assertTrue(3 <= len(fiche['steps']) <= 7)
        for nom, mots in (('INSTALLATION-EDITEUR-DOCUMENTS.md', ('Installer le serveur de documents sur ce poste', 'commandes manuelles', 'serveur du cabinet')),
                          ('INSTALLATION-EDITEUR-DOCUMENTS.en.md', ('Install the document server on this computer', 'manual commands', 'firm’s server'))):
            texte = (ROOT / 'docs' / nom).read_text(encoding='utf-8')
            for mot in mots:
                self.assertIn(mot, texte, nom)
            self.assertLess(texte.index(mots[0]), texte.index('## B.'), nom)   # la voie en un clic d'abord, les commandes manuelles ensuite


class EtatLinux(_Banc):
    windows = False

    def test_S02_ubuntu_relais_dit(self):
        self.installer()
        html = D.carte_html(self.d, '/p', AUTH, 'administrateur')
        self.assertIn('section C6 du guide', html)
        run = [a for a, _ in self.docker.appels if a[1] == 'run'][0]
        self.assertIn('--add-host', run)


if __name__ == '__main__':
    unittest.main()

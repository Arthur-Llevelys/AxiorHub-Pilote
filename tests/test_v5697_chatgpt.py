"""5.6.97 (lot chatgpt, A04 à A08) : connexion ChatGPT suivie sur le poste, liaison poste → serveur par code court, transfert vérifié de
la session, connexion séparée de l'activation, recette.

Aucun appel réel : OpenAI est simulé (``FakeOpenAI`` de test_v5662 : clés RSA d'essai, PKCE réel, flux d'événements simulé), le serveur
du cabinet est simulé par ``ServeurFactice`` (le vrai filtre ``route_native`` derrière un transport en mémoire) et toute connexion
réseau hors de 127.0.0.1 échoue (``reseau_bloque``). Données fictives : example.test, Valbrune. Ces tests contrôlent le circuit
logiciel ; ils ne qualifient ni le contrat réel d'OpenAI, ni un vrai navigateur, ni le gestionnaire de protocole d'un système réel.
"""
import importlib
import io
import json
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from agent import chatgpt5662 as cg, chatgpt5697 as c97
from agent.common import Stop
from agent.desk import Desk
from test_v5662 import FakeOpenAI

HTTP_OPENAI_REEL = cg.http          # transport réel, gardé avant le remplacement des essais

ROOT = Path(__file__).resolve().parents[1]
SERVEUR = 'https://pilote.example.test/agent-courriel'
ADMIN = 'admin@example.test'
EMP = 'sa:' + 'a' * 64            # empreinte de la session d'administration du serveur (distribution autonome)
EMP_POSTE = 'po:' + 'b' * 64


TENTATIVES_RESEAU = []


class reseau_bloque:
    """Toute connexion (et toute résolution de nom) hors de 127.0.0.1 échoue et est notée : un appel réseau non simulé fait échouer
    le test."""
    LOCAUX = ('127.0.0.1', 'localhost', '::1')

    def __init__(self):
        vrai_connect, vrai_dns = socket.socket.connect, socket.getaddrinfo

        def connect(sock, address, *a, **k):
            host = address[0] if isinstance(address, tuple) else address
            if host not in self.LOCAUX:
                TENTATIVES_RESEAU.append(host)
                raise AssertionError('appel réseau non simulé : %r' % (address,))
            return vrai_connect(sock, address, *a, **k)

        def getaddrinfo(host, *a, **k):
            if host not in self.LOCAUX:
                TENTATIVES_RESEAU.append(host)
                raise AssertionError('résolution réseau non simulée : %r' % (host,))
            return vrai_dns(host, *a, **k)
        self.patches = [patch.object(socket.socket, 'connect', connect), patch.object(socket, 'getaddrinfo', getaddrinfo)]

    def start(self):
        for p in self.patches:
            p.start()

    def stop(self):
        for p in self.patches:
            p.stop()


def ouvrir(url):
    """Retour d'OpenAI simulé dans le navigateur du poste (127.0.0.1 seulement) : (statut, page)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=30) as r:
            return r.status, r.read().decode('utf-8')
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode('utf-8')


def requete(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class ServeurFactice:
    """Serveur du cabinet : le vrai filtre natif (``route_native``), une base propre à chaque appel, comme une requête WSGI."""

    def __init__(self, config):
        self.config, self.appels, self.perdre, self.sessions_ok, self.pendant = config, [], set(), True, None

    def __call__(self, method, url, corps, headers):
        assert url.startswith(SERVEUR + c97.CHEMIN_NATIF), url
        path = url[len(SERVEUR):]
        raw = json.dumps(corps).encode()
        env = {'REQUEST_METHOD': method, 'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw),
               'HTTP_X_FORWARDED_PROTO': 'https', 'REMOTE_ADDR': '127.0.0.1', 'HTTP_AUTHORIZATION': headers.get('Authorization', ''),
               'axiorhub.sessions_actives': lambda h: self.sessions_ok and h == EMP[3:]}
        desk = Desk(self.config)
        try:
            out = c97.route_native(desk, env, path, urllib.parse.urlsplit(SERVEUR))
        finally:
            desk.db.close()
        route = path.rsplit('/', 1)[1]
        self.appels.append((route, out['status']))
        if route in self.perdre:                  # traitée par le serveur, réponse perdue en route
            self.perdre.discard(route)
            raise Stop('connexion_http_indisponible')
        return int(out['status'][:3]), json.loads(out['body'])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        sys.path.insert(0, str(ROOT / 'tests'))
        from sqlite_cleanup import close_sqlite_under
        self.addCleanup(close_sqlite_under, self.tmp.name)
        net = reseau_bloque()
        net.start()
        self.addCleanup(net.stop)
        self.oa = FakeOpenAI()
        p = patch.object(cg, 'http', self.oa)
        p.start()
        self.addCleanup(p.stop)
        self.serveur = self._desk('serveur', liaison=True)
        self.poste = self._desk('poste')
        self.faux = ServeurFactice(self.serveur.c)
        p = patch.object(c97, 'TRANSPORT_SERVEUR', self.faux)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._fermer_ecoutes)

    def _desk(self, name, liaison=False):
        base = Path(self.tmp.name) / name
        state = base / 'state'
        state.mkdir(parents=True)
        matters = base / 'matters.json'
        matters.write_text(json.dumps([{'id': 'DOS-001', 'client_name': 'Client DEMO', 'path': '/Dossiers/DEMO', 'references': ['DOS-001']}]), encoding='utf-8')
        c = {'mode': 'drafts', 'state_dir': str(state), 'matters_file': str(matters), 'nextcloud': {'roots': ['/Dossiers']}, 'documents': {},
             'mail': {'host': 'mail.example.test', 'username': 'cabinet@example.test', 'own_addresses': ['cabinet@example.test']},
             'ollama': {'model': 'test-local'}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'}}
        if liaison:
            c['liaison_ia'] = {'active': True}
        d = Desk(c)
        self.addCleanup(d.db.close)
        return d

    def _fermer_ecoutes(self):
        for desk in (self.serveur, self.poste):
            db = c97._db(desk.c['state_dir'])
            try:
                db.execute("UPDATE chatgpt_tentatives SET etat='annulee' WHERE etat='en_cours'")
            finally:
                db.close()
        fin = time.monotonic() + 5
        for t in [t for t in threading.enumerate() if t.name == 'chatgpt5662-retour']:
            t.join(max(0, fin - time.monotonic()))

    # -------------------------------------------------------------------------------- parcours
    def tentative_poste(self, desk=None, port=0):
        desk = desk or self.poste
        out = c97.demarrer(desk, ADMIN, EMP_POSTE, port=port, minutes=2)
        q = requete(out['url'])
        self.oa.nonce = q['nonce']
        return out, q

    def retour(self, q, **extra):
        params = {'code': 'c0de', 'state': q['state'], 'client_id': self.oa.client_id, **extra}
        return ouvrir(q['redirect_uri'] + '?' + urllib.parse.urlencode(params))

    def liaison_approuvee(self, remplacement=False):
        c97.confirmer_serveur(self.poste, ADMIN, SERVEUR)
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        assoc = c97.poste_associer(self.poste, ADMIN, lid)
        vu = c97.statut_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual((vu['etat'], vu['code']), ('a_approuver', assoc['code']))     # même code sur les deux écrans
        c97.approuver_liaison(self.serveur, ADMIN, EMP, lid, remplacement)
        suite = c97.poste_suivre(self.poste, ADMIN, EMP_POSTE, lid, port=0)
        self.assertEqual(suite['etat'], 'openai')
        q = requete(suite['url'])
        self.oa.nonce = q['nonce']
        return lid, q

    def connecter_serveur(self, sub='user-fictif'):
        """Connexion déjà en service sur le serveur (procédure manuelle d'origine)."""
        url = cg.start(self.serveur, ADMIN)
        q = requete(url)
        self.oa.nonce = q['nonce']
        cg.complete(self.serveur, 'state=%s&code=c0de&client_id=%s' % (q['state'], self.oa.client_id))
        return cg.load_record(self.serveur.c['state_dir'])

    def instantane(self, desk):
        sd = desk.c['state_dir']
        return {'compte': cg.account(desk), 'actif': desk.settings(cg.ACTIVE_KEY), 'jetons': (cg.load_record(sd) or {}).get('refresh_token'),
                'fichiers': sorted(p.name for p in cg.vault_dir(sd).glob('*.secret')) if cg.vault_dir(sd).is_dir() else [],
                'fournisseur': desk.settings('ai:provider:chatgpt'), 'politique': desk.settings('ai:politique')}

    def appels_openai(self, fin):
        return [c for c in self.oa.calls if c[1].endswith(fin)]


# ================================================================================================ A04 : suivi sur le poste
class A04(Base):
    def test_A04_connexion_acceptee_suivie_sans_code_affiche(self):
        out, q = self.tentative_poste()
        self.assertEqual(set(out), {'url', 'tentative', 'expire_le'})
        self.assertNotEqual(out['tentative'], q['state'])                           # identifiant opaque distinct de state
        self.assertEqual((q['code_challenge_method'], q['client_id']), ('S256', 'dynamic_agent_client'))
        self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])['etat'], 'en_cours')
        status, page = self.retour(q)
        self.assertEqual(status, 200)
        self.assertNotIn('c0de', page)
        self.assertNotIn(q['state'], page)
        self.assertIn('Revenir à AxiorHub', page)
        st = c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])
        self.assertEqual(st['etat'], 'reussie')
        acc = cg.account(self.poste)
        self.assertEqual((acc['etat'], acc['plan']), ('connecte', True))
        self.assertEqual(cg.load_record(self.poste.c['state_dir'])['refresh_token'], 'renouv-1')
        db = c97._db(self.poste.c['state_dir'])
        row = db.execute('SELECT * FROM chatgpt_tentatives WHERE id=?', (out['tentative'],)).fetchone()
        db.close()
        self.assertEqual((row['verifier'], row['nonce']), ('', ''))                  # secrets d'une tentative effacés à l'issue
        self.assertNotEqual(row['state_hash'], q['state'])                          # state gardé haché

    def test_A04_connexion_refusee_et_sans_droit_au_forfait(self):
        out, q = self.tentative_poste()
        self.retour(q, error='access_denied')
        self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])['etat'], 'refusee')
        self.assertFalse(cg.account(self.poste))
        out, q = self.tentative_poste()
        self.oa.scope = 'openid profile email offline_access resource.invoke'
        self.retour(q, scope=cg.SCOPES)              # droit annoncé dans l'adresse de retour : ignoré, lu dans l'échange seulement
        st = c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])
        self.assertEqual(st['etat'], 'sans_forfait')
        self.assertFalse(cg.account(self.poste)['plan'])

    def test_A04_compte_sans_forfait_ne_remplace_pas_une_session_operationnelle(self):
        out, q = self.tentative_poste()
        self.retour(q)
        avant = cg.load_record(self.poste.c['state_dir'])
        out, q = self.tentative_poste()
        self.oa.scope = 'openid profile email offline_access'
        self.retour(q)
        self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])['etat'], 'sans_forfait')
        self.assertTrue(cg.account(self.poste)['plan'])
        self.assertEqual(cg.load_record(self.poste.c['state_dir']), avant)

    def test_A04_port_prefere_occupe(self):
        occupe = socket.socket()
        occupe.bind(('127.0.0.1', 0))
        occupe.listen(1)
        self.addCleanup(occupe.close)
        port = occupe.getsockname()[1]
        out, q = self.tentative_poste(port=port)
        redirect = urllib.parse.urlsplit(q['redirect_uri'])
        self.assertEqual((redirect.hostname, redirect.path), ('127.0.0.1', cg.CALLBACK_PATH))   # même hôte, même chemin
        self.assertNotEqual(redirect.port, port)
        self.assertEqual(self.retour(q)[0], 200)

    def test_A04_etat_errone_rejoue_ou_duplique(self):
        out, q = self.tentative_poste()
        faux = dict(q, state=secrets.token_urlsafe(32))
        self.assertEqual(self.retour(faux)[0], 400)                                 # retour étranger : l'écoute reste ouverte
        dup = q['redirect_uri'] + '?code=c0de&state=%s&state=%s&client_id=%s' % (q['state'], q['state'], self.oa.client_id)
        self.assertEqual(ouvrir(dup)[0], 400)                                       # paramètre dupliqué : refusé, sans consommer
        self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])['etat'], 'en_cours')
        self.assertEqual(self.retour(q)[0], 200)
        echanges = len([c for c in self.oa.calls if c[1] == cg.TOKEN])
        try:
            rejoue = self.retour(q)[0]
        except urllib.error.URLError:
            rejoue = 'ecoute_fermee'
        self.assertIn(rejoue, (409, 'ecoute_fermee'))
        self.assertEqual(len([c for c in self.oa.calls if c[1] == cg.TOKEN]), echanges)   # jamais un second échange de code
        db = c97._db(self.poste.c['state_dir'])
        try:   # rejeu direct, écoute contournée : la consommation conditionnelle refuse encore
            again = c97._retour(self.poste, out['tentative'], {'code': 'c0de', 'state': q['state'], 'client_id': self.oa.client_id}, None)
        finally:
            db.close()
        self.assertEqual(again['statut'], 409)

    def test_A04_deux_onglets(self):
        un, q1 = self.tentative_poste()
        deux, q2 = self.tentative_poste()
        self.assertNotEqual(q1['redirect_uri'], q2['redirect_uri'])
        self.assertEqual(self.retour(dict(q2, redirect_uri=q1['redirect_uri']))[0], 400)   # retour de l'onglet 2 sur l'écoute 1 : étranger
        self.oa.nonce = q2['nonce']
        self.assertEqual(self.retour(q2)[0], 200)
        self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, deux['tentative'])['etat'], 'reussie')
        self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, un['tentative'])['etat'], 'en_cours')

    def test_A04_statut_reserve_a_la_session_qui_a_cree_la_tentative(self):
        out, _ = self.tentative_poste()
        for owner, emp in ((ADMIN, 'po:' + 'c' * 64), ('autre@example.test', EMP_POSTE)):
            with self.assertRaises(Stop) as ctx:
                c97.statut_tentative(self.poste, owner, emp, out['tentative'])
            self.assertEqual(str(ctx.exception), 'tentative_inconnue')
            with self.assertRaises(Stop):
                c97.annuler_tentative(self.poste, owner, emp, out['tentative'])

    def test_A04_annuler_ferme_l_ecoute_et_expiration(self):
        out, q = self.tentative_poste()
        self.assertEqual(c97.annuler_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])['etat'], 'annulee')
        time.sleep(1.5)
        with self.assertRaises(urllib.error.URLError):        # écoute fermée après l'annulation
            self.retour(q)
        self.assertFalse(self.appels_openai('/oauth/token'))
        out, q = self.tentative_poste()
        with patch.object(c97, '_unix', return_value=int(time.time()) + 3600):
            self.assertEqual(c97.statut_tentative(self.poste, ADMIN, EMP_POSTE, out['tentative'])['etat'], 'expiree')

    def test_A04_routes_connecter_statut_annuler(self):
        from agent import web5662
        env = {'HTTP_COOKIE': 'axh_poste=valeur-de-session-fictive'}
        with patch('agent.web567.actor', return_value=(ADMIN, 'administrateur')), patch('agent.web5634._post', return_value={}), \
                patch('agent.web5662.local_browser', return_value=True):
            out = web5662.api(env, self.poste, {}, '/p', 'm5662/chatgpt/connecter', {}, 'POST')
            d = json.loads(out['body'])
            self.assertEqual((d['mode'], set(d) >= {'url', 'tentative', 'expire_le'}), ('poste', True))
            st = web5662.api(env, self.poste, {}, '/p', 'm5662/chatgpt/statut', {'tentative': d['tentative']}, 'GET')
            self.assertEqual((json.loads(st['body'])['etat'], dict(st['headers'])['Cache-Control']), ('en_cours', 'no-store'))
            autre = web5662.api({'HTTP_COOKIE': 'axh_poste=autre'}, self.poste, {}, '/p', 'm5662/chatgpt/statut', {'tentative': d['tentative']}, 'GET')
            self.assertEqual(autre['status'], '404 Not Found')
            with patch('agent.web5634._post', return_value={'tentative': d['tentative']}):
                fin = web5662.api(env, self.poste, {}, '/p', 'm5662/chatgpt/annuler', {}, 'POST')
            self.assertEqual(json.loads(fin['body'])['etat'], 'annulee')
        self.assertFalse(web5662.allowed('avocat', 'chatgpt/statut'))
        self.assertTrue(web5662.allowed('administrateur', 'chatgpt/liaison/approuver'))


# ================================================================================================ A05 : liaison poste / serveur
class A05(Base):
    def test_A05_lien_axiorhub_strict(self):
        lid = secrets.token_urlsafe(24)
        self.assertEqual(c97.lire_lien('axiorhub://chatgpt/liaison?id=' + lid), lid)
        self.assertEqual(c97.lire_lien('axiorhub://chatgpt/liaison/?id=' + lid), lid)
        for bad in ('axiorhub://chatgpt/liaison?id=%s&serveur=https://autre.example.test' % lid, 'axiorhub://autre.example.test/liaison?id=' + lid,
                    'https://chatgpt/liaison?id=' + lid, 'axiorhub://chatgpt/liaison?id=' + lid + '"%20--stop', 'axiorhub://chatgpt/liaison?id=court',
                    'axiorhub://chatgpt/liaison?id=%s;calc' % lid, 'axiorhub://chatgpt/liaison?id=%s%%26cmd' % lid, 'axiorhub://chatgpt/executer?id=' + lid,
                    'axiorhub://chatgpt/liaison?id=%s#x' % lid, 'axiorhub://chatgpt/liaison?id=%s&id=%s' % (lid, lid), '', 'axiorhub://chatgpt/liaison?id=a b'):
            with self.assertRaises(Stop, msg=bad):
                c97.lire_lien(bad)

    def test_A05_lanceurs_windows_et_linux_sans_interpreteur(self):
        from windows import deploiement, lanceur
        lid = secrets.token_urlsafe(24)
        with patch.object(lanceur, 'run_script', return_value=0) as run:
            self.assertEqual(lanceur.main(['--liaison', 'axiorhub://chatgpt/liaison?id=%s&x=1' % lid]), 2)
            self.assertEqual(lanceur.main(['--liaison', 'axiorhub://chatgpt/liaison?id=' + lid, '--stop']), 2)
            run.assert_not_called()
            self.assertEqual(lanceur.main(['--liaison', 'axiorhub://chatgpt/liaison?id=' + lid]), 0)
        self.assertEqual(run.call_args[0][1], ['poste.py', '--liaison', 'axiorhub://chatgpt/liaison?id=' + lid])
        calls = {}

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        fake = types.SimpleNamespace(HKEY_CURRENT_USER='HKCU', REG_SZ=1, CreateKey=lambda root, key: (calls.setdefault(key, {}), Key())[1])
        fake.SetValueEx = lambda key, name, reserved, kind, value: calls.setdefault('valeurs', []).append((name, value))
        with patch.dict(sys.modules, {'winreg': fake}):
            deploiement.write_protocol(r'C:\x\app-9.9.99\AxiorHub Pilote.exe')
        self.assertIn(r'Software\Classes\axiorhub\shell\open\command', calls)
        valeurs = dict(calls['valeurs'])
        self.assertEqual(valeurs['URL Protocol'], '')
        commande = [v for n, v in calls['valeurs'] if n == '' and '--liaison' in v][0]
        self.assertEqual(commande, '"C:\\x\\app-9.9.99\\AxiorHub Pilote.exe" --liaison "%1"')   # l'exécutable, jamais cmd ni powershell
        import configparser
        entry = configparser.ConfigParser(interpolation=None)
        entry.read(ROOT / 'deploy' / 'poste' / 'axiorhub-pilote-liaison.desktop', encoding='utf-8')
        section = entry['Desktop Entry']
        self.assertEqual((section['Exec'], section['MimeType'], section['NoDisplay']), ('axiorhub-pilote --liaison %u', 'x-scheme-handler/axiorhub;', 'true'))
        import subprocess
        import shutil
        sh = shutil.which('sh')
        if sh and sys.platform != 'win32':   # l'adresse reste UN argument : aucune expansion, aucune commande
            script = (ROOT / 'deploy' / 'poste' / 'axiorhub-pilote').read_text(encoding='utf-8').replace('exec /usr/bin/python3 ', 'printf "%s\\n" ')
            out = subprocess.run([sh, '-c', script, 'axiorhub-pilote', '--liaison', 'axiorhub://chatgpt/liaison?id=$(id)`id`;id'], capture_output=True, text=True)
            self.assertEqual(out.stdout.splitlines()[-1], 'axiorhub://chatgpt/liaison?id=$(id)`id`;id')

    def test_A05_option_de_deploiement_desactivee_par_defaut(self):
        with self.assertRaises(Stop) as ctx:
            c97.creer_liaison(self.poste, ADMIN, EMP)
        self.assertEqual(str(ctx.exception), 'liaison_ia_desactivee')
        env = {'REQUEST_METHOD': 'POST', 'HTTP_X_FORWARDED_PROTO': 'https', 'CONTENT_LENGTH': '2', 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(b'{}')}
        self.assertEqual(c97.route_native(self.poste, env, '/liaison-ia/v1/etat', urllib.parse.urlsplit(SERVEUR))['status'], '404 Not Found')

    def test_A05_revendication_unique_et_code_court(self):
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        un = c97.reclamer(self.serveur, lid, c97._h('preuve-1'), {'axiorhub.sessions_actives': lambda h: True})
        self.assertRegex(un['code'], r'^\d{3} \d{3}$')
        with self.assertRaises(c97.Refus) as ctx:          # second poste : aucune revendication
            c97.reclamer(self.serveur, lid, c97._h('preuve-2'), {'axiorhub.sessions_actives': lambda h: True})
        self.assertEqual(ctx.exception.status, '404 Not Found')

    def test_A05_appareil_non_approuve_preuve_erronee_et_approbation_par_la_meme_session(self):
        c97.confirmer_serveur(self.poste, ADMIN, SERVEUR)
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        c97.poste_associer(self.poste, ADMIN, lid)
        preuve = c97._lire_coffre(self.poste, lid)['preuve']
        env = {'axiorhub.sessions_actives': lambda h: True}
        etat = c97.etat_poste(self.serveur, lid, preuve, env)
        self.assertEqual(etat['etat'], 'a_approuver')
        self.assertNotIn('nonce', etat)                                             # pas de nonce avant l'approbation
        self.assertEqual(c97.recevoir(self.serveur, lid, preuve, {'client_id': 'x'}, env)['etat'], 'a_approuver')
        with self.assertRaises(c97.Refus):
            c97.etat_poste(self.serveur, lid, secrets.token_urlsafe(32), env)          # preuve erronée : réponse neutre
        with self.assertRaises(Stop):
            c97.approuver_liaison(self.serveur, ADMIN, 'sa:' + 'f' * 64, lid)            # autre session : refusé
        with self.assertRaises(Stop):
            c97.approuver_liaison(self.serveur, 'autre@example.test', EMP, lid)
        c97.approuver_liaison(self.serveur, ADMIN, EMP, lid)
        autorise = c97.etat_poste(self.serveur, lid, preuve, env)
        self.assertEqual((autorise['etat'], autorise['instance']), ('autorisee', cg.host_id(self.serveur)))
        self.assertGreaterEqual(len(autorise['nonce']), 32)

    def test_A05_liaison_expiree_et_deconnexion_de_l_admin(self):
        c97.confirmer_serveur(self.poste, ADMIN, SERVEUR)
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        with patch.object(c97, '_unix', return_value=int(time.time()) + 3600):
            with self.assertRaises(Stop):
                c97.poste_associer(self.poste, ADMIN, lid)
            self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'expiree')
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        c97.poste_associer(self.poste, ADMIN, lid)
        self.faux.sessions_ok = False                                               # l'administrateur s'est déconnecté
        self.assertEqual(c97.poste_suivre(self.poste, ADMIN, EMP_POSTE, lid, port=0)['etat'], 'annulee')
        st = c97.statut_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual(st['etat'], 'annulee')
        self.assertIn('session d’administration fermée', st['message'])
        self.assertFalse(c97._coffre_poste(self.poste, lid).exists())                # preuve effacée côté poste

    def test_A05_filtre_natif_https_taille_debit_et_champs(self):
        origin = urllib.parse.urlsplit(SERVEUR)

        def env(body, **extra):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            return {'REQUEST_METHOD': 'POST', 'HTTP_X_FORWARDED_PROTO': 'https', 'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json',
                    'wsgi.input': io.BytesIO(raw), 'REMOTE_ADDR': '192.0.2.10', **extra}
        statut = lambda e, path='/liaison-ia/v1/etat', o=origin: c97.route_native(self.serveur, e, path, o)['status']
        self.assertEqual(statut(env({'id': 'x'}, HTTP_X_FORWARDED_PROTO='http')), '403 Forbidden')
        self.assertEqual(statut(env({'id': 'x'}), o=urllib.parse.urlsplit('http://pilote.example.test')), '403 Forbidden')
        self.assertEqual(statut(env({'id': 'x'}, REQUEST_METHOD='GET')), '404 Not Found')
        self.assertEqual(statut(env({'id': 'x'}), '/liaison-ia/v1/autre'), '404 Not Found')
        self.assertEqual(statut(env(b'x' * (c97.CORPS_MAX + 1))), '413 Payload Too Large')
        self.assertEqual(statut(env({'id': 'x', 'cmd': 'y'})), '400 Bad Request')                  # champ inconnu
        self.assertEqual(statut(env({'id': 'x'}, CONTENT_TYPE='text/plain')), '415 Unsupported Media Type')
        codes = [statut(env({'id': secrets.token_urlsafe(24)}, REMOTE_ADDR='192.0.2.11')) for _ in range(c97.DEBIT_ADRESSE[0] + 1)]
        self.assertEqual(codes[-1], '429 Too Many Requests')
        self.assertEqual(set(codes[:-1]), {'404 Not Found'})

    def test_A05_filtre_wsgi_de_la_distribution_autonome(self):
        from agent.standalone_auth import StandaloneAuth
        vus = []

        def app(env, start):
            vus.append(dict(env))
            start('200 OK', [('Content-Type', 'application/json')])
            return [b'{}']
        auth = StandaloneAuth(app, Path(self.tmp.name) / 'auth', 'https://pilote.example.test')
        self.addCleanup(auth.db.close)
        base = {'wsgi.input': io.BytesIO(b'{}'), 'CONTENT_LENGTH': '2', 'REMOTE_ADDR': '192.0.2.20', 'HTTP_AUTHORIZATION': 'Bearer preuve-fictive'}
        auth({**base, 'REQUEST_METHOD': 'POST', 'PATH_INFO': '/liaison-ia/v1/etat'}, lambda *a: None)
        self.assertEqual(vus[-1]['HTTP_AUTHORIZATION'], 'Bearer preuve-fictive')                   # preuve transmise telle quelle
        self.assertTrue(callable(vus[-1]['axiorhub.sessions_actives']))
        n = len(vus)
        auth({**base, 'REQUEST_METHOD': 'GET', 'PATH_INFO': '/liaison-ia/v1/etat'}, lambda *a: None)
        auth({**base, 'REQUEST_METHOD': 'POST', 'PATH_INFO': '/liaison-ia/v1/autre'}, lambda *a: None)
        self.assertEqual(len(vus), n)                                                               # seuls les trois POST exacts passent
        auth.db.execute("INSERT INTO users(id,email,password_hash,salt,active,created,role) VALUES(1,'admin@example.test','x','00',1,'x','administrateur')")
        token = auth._session(1)
        h = __import__('hashlib').sha256(token.encode()).hexdigest()
        self.assertTrue(auth._session_admin_active(h))
        auth.db.execute('DELETE FROM sessions WHERE token_hash=?', (h,))                            # déconnexion
        self.assertFalse(auth._session_admin_active(h))


# ================================================================================================ A06 : transfert vérifié
class A06(Base):
    def test_A06_parcours_complet_sans_copier_coller(self):
        with patch.object(__import__('agent.chatgpt5682', fromlist=['x']), 'export_session', side_effect=AssertionError('export_session interdit')):
            lid, q = self.liaison_approuvee()
            self.assertEqual(q['ext_agent_host_id'], cg.host_id(self.serveur))        # identifiant d'hôte du SERVEUR
            self.assertNotIn('id_token_hint', q)
            status, page = self.retour(q)
        self.assertEqual(status, 200)
        st = c97.statut_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual(st['etat'], 'recue')
        rec = cg.load_record(self.serveur.c['state_dir'])
        self.assertEqual((rec['refresh_token'], rec['ext_agent_host_id']), ('renouv-1', cg.host_id(self.serveur)))
        acc = cg.account(self.serveur)
        self.assertEqual((acc['etat'], acc['plan'], acc['email']), ('connecte', True, 'avocate@example.test'))
        self.assertTrue(cg.VERSIONED.fullmatch(self.serveur.settings(cg.ACTIVE_KEY)['fichier']))
        self.assertEqual(len(self.appels_openai('/responses')), 1)                    # test de génération, une fois
        body = json.loads(self.appels_openai('/responses')[0][2])
        self.assertEqual((body['store'], body['input'][0]['content']), (False, c97.TEST_TEXTE))
        self.assertFalse(cg.account(self.poste))                                     # connexion du poste intacte
        self.assertFalse(c97._coffre_poste(self.poste, lid).exists())                 # copie temporaire effacée après l'accusé
        self.assertEqual(c97.poste_suivre(self.poste, ADMIN, EMP_POSTE, lid, port=0)['etat'], 'recue')
        self.assertFalse(self.serveur.settings('ai:provider:chatgpt'))                # A07 : connecté, rien n'est activé

    def test_A06_accuse_perdu_puis_repetition(self):
        lid, q = self.liaison_approuvee()
        self.faux.perdre.add('recevoir')
        self.retour(q)
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'recue')
        self.assertTrue(c97._lire_coffre(self.poste, lid)['session'])                # accusé perdu : copie gardée
        self.assertEqual(c97.poste_suivre(self.poste, ADMIN, EMP_POSTE, lid, port=0)['etat'], 'recue')
        self.assertFalse(c97._coffre_poste(self.poste, lid).exists())
        self.assertEqual(len(self.appels_openai('/responses')), 1)                    # aucune nouvelle vérification
        self.assertEqual([r for r, _ in self.faux.appels].count('recevoir'), 2)
        fichiers = [p for p in cg.vault_dir(self.serveur.c['state_dir']).glob('compte-*.secret')]
        self.assertEqual(len(fichiers), 1)                                           # une seule session publiée

    def test_A06_appel_en_double_pendant_la_verification(self):
        lid, q = self.liaison_approuvee()
        preuve = c97._lire_coffre(self.poste, lid)['preuve']
        env = {'axiorhub.sessions_actives': lambda h: True}
        resultats = []
        vrai = self.oa.__call__

        def pendant(method, url, data=None, headers=None, timeout=60, stream=False):
            if url.endswith('/responses') and not resultats:   # second envoi pendant le test de génération
                resultats.append(c97.recevoir(Desk(self.serveur.c), lid, preuve, session, env))
            return vrai(method, url, data, headers, timeout, stream)
        candidate = cg.exchange(self.poste, {'code': 'c0de'}, {'verifier': 'v' * 43, 'redirect_uri': 'http://127.0.0.1:1/auth/callback', 'nonce': q['nonce']},
                                self.oa.client_id)
        from agent.chatgpt5682 import RECORD_FIELDS
        session = {k: candidate['record'][k] for k in RECORD_FIELDS}
        with patch.object(cg, 'http', pendant):
            out = c97.recevoir(self.serveur, lid, preuve, session, env)
        self.assertEqual((out['etat'], resultats[0]['etat']), ('recue', 'verification'))
        self.assertEqual(c97.recevoir(self.serveur, lid, preuve, session, env)['etat'], 'recue')
        self.assertEqual(len(self.appels_openai('/responses')), 1)

    def test_A06_compte_different_puis_remplacement_explicite(self):
        avant_rec = self.connecter_serveur()
        avant = self.instantane(self.serveur)
        lid, q = self.liaison_approuvee()
        self.assertEqual(q['client_id'], 'oaiapp_essai')                              # reconnexion : client émis attendu
        with self._sujet('user-autre'):
            self.retour(q)
        st = c97.statut_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual(st['etat'], 'echec')
        self.assertIn('autre compte', st['message'])
        self.assertEqual(self.instantane(self.serveur), avant)                         # ancien accès préservé
        self.assertEqual(cg.load_record(self.serveur.c['state_dir']), avant_rec)
        lid, q = self.liaison_approuvee(remplacement=True)
        self.assertEqual(q['client_id'], 'dynamic_agent_client')
        with self._sujet('user-autre'):
            self.retour(q)
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'recue')
        self.assertEqual(cg.account(self.serveur)['subject'], 'user-autre')

    def _sujet(self, sub):
        vrai = FakeOpenAI.id_token
        oa = self.oa

        def id_token(client_id, nonce, key=None):
            import base64
            tete, corps, _ = vrai(oa, client_id, nonce, key).split('.')
            data = json.loads(base64.urlsafe_b64decode(corps + '=' * (-len(corps) % 4)))
            data['sub'] = sub
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            b = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
            corps = b(json.dumps(data).encode())
            return tete + '.' + corps + '.' + b((key or oa.key).sign((tete + '.' + corps).encode(), padding.PKCS1v15(), hashes.SHA256()))
        return patch.object(oa, 'id_token', id_token)

    def test_A06_echec_du_test_ou_jeton_falsifie_laisse_la_connexion_en_place(self):
        self.connecter_serveur()
        avant = self.instantane(self.serveur)
        lid, q = self.liaison_approuvee()
        self.oa.responses_error = cg.HTTPFailure(503, 'server_error')
        self.retour(q)
        st = c97.statut_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual(st['etat'], 'echec')
        self.assertEqual(self.instantane(self.serveur), avant)
        self.assertFalse(c97._coffre_poste(self.poste, lid).exists())
        self.oa.responses_error = None
        lid, q = self.liaison_approuvee()
        self.oa.forge = True                                                          # signature d'une autre clé : refusée dès le poste
        self.retour(q)
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'autorisee')   # rien n'est parti vers le serveur
        self.assertEqual(self.instantane(self.serveur), avant)
        self.oa.forge = False
        preuve = c97._lire_coffre(self.poste, lid)['preuve']
        candidate = cg.exchange(self.poste, {'code': 'c0de'}, {'verifier': 'v' * 43, 'redirect_uri': 'http://127.0.0.1:1/auth/callback', 'nonce': q['nonce']},
                                self.oa.client_id)
        from agent.chatgpt5682 import RECORD_FIELDS
        session = {k: candidate['record'][k] for k in RECORD_FIELDS}
        self.oa.forge = True
        session['id_token'] = self.oa.id_token(self.oa.client_id, q['nonce'], self.oa.other)  # le serveur revérifie lui-même la signature
        out = c97.recevoir(self.serveur, lid, preuve, session, {'axiorhub.sessions_actives': lambda h: True})
        self.assertEqual((out['etat'], out['motif']), ('echec', 'jeton_invalide'))
        self.assertEqual(self.instantane(self.serveur), avant)

    def test_A06_annulation_ou_reglage_modifie_pendant_la_verification(self):
        self.connecter_serveur()
        avant = self.instantane(self.serveur)
        lid, q = self.liaison_approuvee()
        vrai = self.oa.__call__

        def annule(method, url, data=None, headers=None, timeout=60, stream=False):
            if url.endswith('/responses'):
                c97.annuler_liaison(Desk(self.serveur.c), ADMIN, EMP, lid)
            return vrai(method, url, data, headers, timeout, stream)
        with patch.object(cg, 'http', annule):
            self.retour(q)
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'annulee')
        self.assertEqual(self.instantane(self.serveur), avant)
        lid, q = self.liaison_approuvee()
        acc = cg.account(self.serveur)
        self.serveur.setting(cg.ACCOUNT_KEY, {**acc, 'plan': False})                    # connexion changée entre-temps
        avant = self.instantane(self.serveur)
        self.retour(q)
        st = c97.statut_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual(st['etat'], 'echec')
        self.assertIn('la connexion a changé', st['message'])
        self.assertEqual(self.instantane(self.serveur), avant)

    def test_A06_session_invalide_refusee(self):
        lid, q = self.liaison_approuvee()
        preuve = c97._lire_coffre(self.poste, lid)['preuve']
        env = {'axiorhub.sessions_actives': lambda h: True}
        out = c97.recevoir(self.serveur, lid, preuve, {'client_id': 'x', 'subject': 's', 'id_token': 'a.b.c', 'access_token': 'a', 'refresh_token': 'r',
                                                       'redirect_uri': 'http://127.0.0.1/'}, env)
        self.assertEqual((out['etat'], out['motif']), ('echec', 'session_invalide'))
        self.assertFalse(self.serveur.settings(cg.ACTIVE_KEY))

    def test_A06_un_seul_hote_renouvelle_et_purge_apres_plantage(self):
        lid, q = self.liaison_approuvee()
        self.retour(q)
        sd = self.serveur.c['state_dir']
        actif = cg.vault_dir(sd) / self.serveur.settings(cg.ACTIVE_KEY)['fichier']
        rec = cg.load_record(sd)
        rec['expires_at'] = '2000-01-01T00:00:00+00:00'
        cg.save_record(sd, rec)
        self.assertEqual(cg.access_token(sd), 'acces-2')
        self.assertEqual(cg.load_record(sd, actif)['refresh_token'], 'renouv-2')        # renouvelé dans le fichier publié
        self.assertFalse((cg.vault_dir(sd) / 'compte.secret').exists())
        lid2 = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']                  # réservation laissée par un arrêt brutal
        orphelin = 'compte-%s.secret' % ('0' * 32)
        cg.save_record(sd, {'refresh_token': 'x'}, cg.vault_dir(sd) / orphelin)
        db = c97._db(sd)
        db.execute("UPDATE ia_liaisons SET etat='verification', fichier=? WHERE id=?", (orphelin, lid2))
        db.close()
        with patch.object(c97, '_unix', return_value=int(time.time()) + 3600):
            c97.purger(self.serveur)
        self.assertFalse((cg.vault_dir(sd) / orphelin).exists())
        self.assertTrue(actif.exists())
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid2)['etat'], 'expiree')


# ================================================================================================ A07 : connexion et activation séparées
class A07(Base):
    def test_A07_se_connecter_n_active_ni_mode_ni_route(self):
        self.serveur.setting('ai:politique', {'mode': 'local', 'consentement': False})
        avant = (self.serveur.settings('ai:politique'), self.serveur.settings('ai:provider:chatgpt'), self.serveur.settings('ai:routes'))
        lid, q = self.liaison_approuvee()
        self.retour(q)
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'recue')
        self.assertEqual((self.serveur.settings('ai:politique'), self.serveur.settings('ai:provider:chatgpt'), self.serveur.settings('ai:routes')), avant)
        with self.assertRaises(Stop):
            cg.save_provider(self.serveur, ADMIN, 'gpt-essai', True, False)          # activation : autorisation d'envoi exigée

    def test_A07_guide_sous_etape_connecter_l_ia_et_plus_tard(self):
        from agent import guide5631
        html = guide5631._cartes_etape(self.serveur, {}, '/p', 'ia', {}, ADMIN, True)
        self.assertIn('data-option="ia_connexion"', html)
        self.assertIn('id="chatgpt5697-guide"', html)
        self.assertIn('id="k96-mistral"', html)
        self.assertIn('ia_connexion', guide5631.PLUS_TARD)
        self.assertEqual(guide5631._cartes_etape(self.serveur, {}, '/p', 'ia', {}, ADMIN, False).count('chatgpt5697-guide'), 0)

    def test_A07_autres_methodes_conservees(self):
        page = cg.html(self.serveur, '/p', 'administrateur', False)
        autres = page[page.index('<details class="c5697-autres"'):]
        for route in ('m5662/chatgpt/terminer', 'm5682/session/import'):
            self.assertIn('data-api="%s"' % route, autres)                          # procédures d'origine sous « Autres méthodes »
        self.assertIn('data-mode="liaison"', page[:page.index('<details class="c5697-autres"')])
        self.assertIn('data-mode="manuel"', autres)
        self.serveur.c['liaison_ia'] = {'active': False}
        page = cg.html(self.serveur, '/p', 'administrateur', False)
        self.assertIn('data-mode="manuel"', page)
        self.assertNotIn('data-mode="liaison"', page)
        with patch('agent.maj5661.platform', return_value='windows'):
            poste = cg.html(self.poste, '/p', 'administrateur', True)
        self.assertIn('data-api="m5662/chatgpt/poste/serveur"', poste)
        self.assertIn('data-mode="poste"', poste)

    def test_A07_serveurs_confirmes_https_seulement(self):
        for bad in ('http://pilote.example.test', 'https://u:p@pilote.example.test', 'https://pilote.example.test/?x=1', 'ftp://pilote.example.test',
                    'https://pilote.example.test/a b'):
            with self.assertRaises(Stop, msg=bad):
                c97.confirmer_serveur(self.poste, ADMIN, bad)
        self.assertEqual(c97.confirmer_serveur(self.poste, ADMIN, 'https://Pilote.Example.Test/agent-courriel/')['serveurs'], [SERVEUR])
        with self.assertRaises(Stop) as ctx:
            c97.poste_associer(self._desk('poste2'), ADMIN, secrets.token_urlsafe(24))
        self.assertEqual(str(ctx.exception), 'aucun_serveur_confirme')


# ================================================================================================ A08 : recette
class A08(Base):
    def test_A08_secrets_absents_des_tables_journaux_et_reponses(self):
        lid, q = self.liaison_approuvee()
        preuve = c97._lire_coffre(self.poste, lid)['preuve']
        self.retour(q)
        self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'recue')
        secrets_ = ['acces-1', 'renouv-1', 'c0de', q['state'], q['code_challenge'], preuve, cg.load_record(self.serveur.c['state_dir'])['id_token']]
        for desk in (self.serveur, self.poste):
            db = c97._db(desk.c['state_dir'])
            try:
                dump = json.dumps([[dict(r) for r in db.execute('SELECT * FROM %s' % t)] for t in ('chatgpt_tentatives', 'ia_liaisons', 'ia_liaisons_poste', 'audit')])
                dump += json.dumps([dict(r) for r in db.execute("SELECT * FROM settings WHERE key LIKE 'chatgpt%'")])
            finally:
                db.close()
            for s in secrets_:
                self.assertNotIn(s, dump)
        reponses = json.dumps([c97.statut_liaison(self.serveur, ADMIN, EMP, lid), c97.poste_suivre(self.poste, ADMIN, EMP_POSTE, lid, port=0)])
        for s in secrets_:
            self.assertNotIn(s, reponses)
        self.assertNotIn(q['nonce'], reponses)

    def test_A08_appel_reseau_non_simule_refuse(self):
        del TENTATIVES_RESEAU[:]
        with self.assertRaises((AssertionError, Stop)):                               # OpenAI réel : jamais joint
            HTTP_OPENAI_REEL('GET', cg.DISCOVERY)
        with patch.object(c97, 'TRANSPORT_SERVEUR', None), self.assertRaises((AssertionError, Stop)):   # serveur réel : jamais joint
            c97._appel(SERVEUR, 'etat', {'id': secrets.token_urlsafe(24)}, secrets.token_urlsafe(32))
        self.assertEqual(sorted(set(TENTATIVES_RESEAU)), ['auth.openai.com', 'pilote.example.test'])   # arrêtées avant toute sortie

    def test_A08_sans_ia_les_actions_de_liaison_restent_disponibles(self):
        bloque = lambda *a, **k: (_ for _ in ()).throw(AssertionError('fournisseur IA appelé'))
        with patch.object(cg, 'http', bloque), patch('agent.model.Model.__init__', side_effect=AssertionError('modèle appelé')):
            lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
            self.assertEqual(c97.statut_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'attente_poste')
            self.assertEqual(c97.annuler_liaison(self.serveur, ADMIN, EMP, lid)['etat'], 'annulee')
            c97.purger(self.serveur)
        self.serveur.db.execute("INSERT INTO jobs(kind,args,status,created) VALUES('assistant_answer','{}','pending','2026-01-01T00:00:00+00:00')")
        self.serveur.db.commit()
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        c97.annuler_liaison(self.serveur, ADMIN, EMP, lid)
        self.assertEqual(self.serveur.db.execute("SELECT status FROM jobs WHERE kind='assistant_answer'").fetchone()[0], 'pending')   # aucun autre travail touché

    # ---------------------------------------------------------------- mutations : chaque garde-fou est indispensable
    def _mutant(self, *remplacements):
        src = (ROOT / 'agent' / 'chatgpt5697.py').read_text(encoding='utf-8')
        for old, new in remplacements:
            self.assertEqual(src.count(old), 1, old)
            src = src.replace(old, new)
        mod = types.ModuleType('agent.chatgpt5697_mutant')
        mod.__package__ = 'agent'
        exec(compile(src, 'chatgpt5697_mutant', 'exec'), mod.__dict__)   # copie en mémoire ; l'arbre n'est jamais modifié
        mod.TRANSPORT_SERVEUR = self.faux
        return mod

    def test_A08_mutation_revendication_unique(self):
        m = self._mutant(("        if not row or row['etat'] != 'attente_poste':\n", "        if not row:\n"),
                         ("WHERE id=? AND etat='attente_poste' \"\n", "WHERE id=? \"\n"))
        env = {'axiorhub.sessions_actives': lambda h: True}
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        c97.reclamer(self.serveur, lid, c97._h('1'), env)
        self.assertTrue(m.reclamer(self.serveur, lid, c97._h('2'), env)['code'])        # sans le garde-fou : seconde revendication admise
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        c97.reclamer(self.serveur, lid, c97._h('1'), env)
        with self.assertRaises(c97.Refus):
            c97.reclamer(self.serveur, lid, c97._h('2'), env)

    def test_A08_mutation_preuve_du_poste(self):
        m = self._mutant(("not hmac.compare_digest(row['preuve_hash'], _h(preuve))", 'False'))
        env = {'axiorhub.sessions_actives': lambda h: True}
        lid = c97.creer_liaison(self.serveur, ADMIN, EMP)['liaison']
        c97.reclamer(self.serveur, lid, c97._h(secrets.token_urlsafe(32)), env)
        intrus = secrets.token_urlsafe(32)
        self.assertEqual(m.etat_poste(self.serveur, lid, intrus, env)['etat'], 'a_approuver')
        with self.assertRaises(c97.Refus):
            c97.etat_poste(self.serveur, lid, intrus, env)

    def test_A08_mutation_revision_et_copie_avant_accuse(self):
        m = self._mutant(("            if revision_connexion(db) != row['revision']:\n", "            if False:\n"))
        self.connecter_serveur()
        lid, q = self.liaison_approuvee()
        acc = cg.account(self.serveur)
        self.serveur.setting(cg.ACCOUNT_KEY, {**acc, 'plan': False})
        preuve = c97._lire_coffre(self.poste, lid)['preuve']
        candidate = cg.exchange(self.poste, {'code': 'c0de'}, {'verifier': 'v' * 43, 'redirect_uri': 'http://127.0.0.1:1/auth/callback', 'nonce': q['nonce']},
                                self.oa.client_id)
        from agent.chatgpt5682 import RECORD_FIELDS
        session = {k: candidate['record'][k] for k in RECORD_FIELDS}
        env = {'axiorhub.sessions_actives': lambda h: True}
        self.assertEqual(m.recevoir(self.serveur, lid, preuve, session, env)['etat'], 'recue')   # sans le garde-fou : publiée malgré le changement
        m2 = self._mutant(("    except Stop:\n        return 'transfert'", "    except Stop:\n        _poste_etat(desk, lid, 'echec')\n        return 'echec'"))
        lid, q = self.liaison_approuvee()
        self.faux.perdre.add('recevoir')
        candidate = cg.exchange(self.poste, {'code': 'c0de'}, {'verifier': 'v' * 43, 'redirect_uri': 'http://127.0.0.1:1/auth/callback', 'nonce': q['nonce']},
                                self.oa.client_id)
        m2.poste_candidat(self.poste, lid, candidate)
        self.assertFalse(c97._coffre_poste(self.poste, lid).exists())                    # sans le garde-fou : copie effacée avant l'accusé


if __name__ == '__main__':
    unittest.main()

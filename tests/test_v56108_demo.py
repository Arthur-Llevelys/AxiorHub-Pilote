"""5.6.108, lot D : démo en ligne — correctifs constatés à la première installation sur un vrai serveur.

D1  « Commencer la démo » refusé : ``Referrer-Policy: same-origin`` sur les pages du répartiteur ; ``Origin: null`` admis seulement avec
    ``Sec-Fetch-Site: same-origin`` (et le jeton du formulaire) ; tout autre cas refusé. En-têtes réels d'un navigateur.
D2  Instance : garde réseau du processus — ``getaddrinfo`` d'une adresse écrite en chiffres admis sans réseau ni DNS (waitress 2.x),
    nom refusé, prise IP refusée.
D3  ``apache-demo.example.conf`` et ``LISEZMOI.md`` : bloc ``<Location /agent-courriel>``, Referrer-Policy, page de secours, journal
    minimal (syntaxe vérifiée à part dans un conteneur ``httpd:2.4``, voir le compte rendu du lot).
D4  Session perdue : session de l'instance marquée ouverte seulement après un échange réussi ; une réponse 401 de l'instance n'est
    jamais transmise avec ``WWW-Authenticate: Basic`` (page « Votre session de démonstration a été perdue », « Recommencer »).
D5  Test fumée : simule le navigateur, admet le 403 d'Apache pour ``/server-status``, attend le 303 du répartiteur pour ``/code/…`` et
    ``/agent-courriel/…`` (sinon message qui renvoie au bloc Location du LISEZMOI).
D6  Langue de la démonstration choisie à l'accueil (fr, en, de, es ; liste fermée ; langue du navigateur par défaut), pages du
    répartiteur traduites, langue passée à ``instance.py --langue`` puis à ``demo56107.creer(..., langue=)``.
D7  Bout en bout réel (Linux, waitress, ``AXH_DEMO_BOUT_EN_BOUT=1``) : répartiteur et instances réels sur sockets Unix, Commencer →
    session → Aujourd'hui dans chaque langue, sans réseau sortant (conteneur ``--network none``).

Données fictives (DURAND, ORLANDEC, example.test, adresses de documentation). Aucun appel réseau : serveur WSGI local ou appels directs.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from urllib.parse import urlencode
from wsgiref.simple_server import WSGIRequestHandler, make_server

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from agent import demo_hote56107 as dh   # noqa: E402
from test_v56107_hote import Base, FauxLanceur, charger_fichier, mutant, ORIGINE, IP_A, IP_B, DEMO   # noqa: E402

LINUX = os.name == 'posix' and hasattr(socket, 'AF_UNIX')
CHROME = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
# POST du formulaire « Commencer » par Chrome quand la page applique « Referrer-Policy: no-referrer » (cas du serveur réel, 17/11/2026)
NAVIGATEUR_NO_REFERRER = {'HTTP_ORIGIN': 'null', 'HTTP_SEC_FETCH_SITE': 'same-origin', 'HTTP_SEC_FETCH_MODE': 'navigate',
                          'HTTP_SEC_FETCH_DEST': 'document', 'HTTP_SEC_FETCH_USER': '?1', 'HTTP_UPGRADE_INSECURE_REQUESTS': '1',
                          'HTTP_CACHE_CONTROL': 'max-age=0', 'HTTP_USER_AGENT': CHROME, 'HTTP_ACCEPT_LANGUAGE': 'fr-FR,fr;q=0.9',
                          'HTTP_ACCEPT': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'}
# Même POST avec « Referrer-Policy: same-origin » (5.6.108) : Chrome et Firefox envoient l'origine réelle et le Referer
NAVIGATEUR_SAME_ORIGIN = {**NAVIGATEUR_NO_REFERRER, 'HTTP_ORIGIN': ORIGINE, 'HTTP_REFERER': ORIGINE + '/'}
BANDEAUX = {'fr': 'Mode démonstration — données fictives', 'en': 'Demo mode — fictitious data', 'de': 'Demo-Modus — fiktive Daten',
            'es': 'Modo demostración — datos ficticios'}
BOUTONS = {'fr': 'Commencer la démo', 'en': 'Start the demo', 'de': 'Demo starten', 'es': 'Iniciar la demo'}


class Flux:
    """Corps d'une réponse de l'instance ; ``ferme`` dit si le répartiteur a bien fermé la connexion d'une réponse écartée."""

    def __init__(self, morceaux):
        self.morceaux, self.ferme = list(morceaux), False

    def __iter__(self):
        return iter(self.morceaux)

    def close(self):
        self.ferme = True


class FauxLanceur108(FauxLanceur):
    """Faux lanceur 5.6.107 complété : langue reçue, échange du jeton de lancement réussi ou refusé, réponses 401 « Basic »."""

    def __init__(self):
        super().__init__()
        self.langues, self.flux = [], []
        self.echange = 'ok'                 # 'ok' | 'refus' | 'sans-cookie'
        self.refus_401 = set()              # chemins auxquels l'instance répond 401 + WWW-Authenticate: Basic
        self.basic_200 = False              # l'instance ajoute (à tort) WWW-Authenticate à une réponse 200

    def creer(self, ident, dossier, **options):
        self.langues.append(options.get('langue', '(absente)'))
        return super().creer(ident, dossier)

    def transmettre(self, p, methode, cible, entetes, corps, delai):
        chemin = cible.split('?')[0]
        if chemin == '/agent-courriel/poste-session':
            self.transmis.append((p['ident'], methode, cible))
            if self.echange == 'refus':
                flux = Flux([b'<p>Lien de la d\xc3\xa9monstration expir\xc3\xa9.</p>'])
                self.flux.append(flux)
                return '403 Forbidden', [('Content-Type', 'text/html; charset=utf-8')], flux
            entetes_rep = [('Location', '/agent-courriel/aujourdhui'), ('Content-Length', '0')]
            if self.echange == 'ok':
                entetes_rep.append(('Set-Cookie', 'axh_poste=s-%s; Path=/agent-courriel; HttpOnly; SameSite=Strict' % p['ident']))
            return '303 See Other', entetes_rep, [b'']
        if chemin in self.refus_401:
            self.transmis.append((p['ident'], methode, cible))
            flux = Flux([b'<p>Authentification requise</p>'])
            self.flux.append(flux)
            return '401 Unauthorized', [('Content-Type', 'text/html; charset=utf-8'),
                                        ('WWW-Authenticate', 'Basic realm="AxiorHub Cabinet", charset="UTF-8"')], flux
        statut, retour, flux = super().transmettre(p, methode, cible, entetes, corps, delai)
        if self.basic_200:
            retour = retour + [('WWW-Authenticate', 'Basic realm="AxiorHub Cabinet"')]
        return statut, retour, flux


class Base108(Base):
    def setUp(self):
        super().setUp()
        self.lanceur = FauxLanceur108()
        self.app = self.fabriquer()

    def demarrer(self, entetes, champs_sup=None, ip=IP_A, app=None, pre=None):
        pre = pre or self.pre(app)
        champs = {'pre': pre, **(champs_sup or {})}
        statut, rep, page = self.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre}, urlencode(champs).encode(), ip=ip, app=app,
                                       origine=None, entetes=entetes)
        brut = self.cookie(rep, dh.NOM_COOKIE)
        return statut, (brut.split(';')[0].split('=', 1)[1] if brut else None), page

    @staticmethod
    def entete(entetes, nom):
        return [v for k, v in entetes if k.lower() == nom.lower()]

    def ouvrir_et_suivre(self, jeton):
        """GET / → lien d'ouverture (303) → échange chez l'instance ; rend la réponse de l'échange."""
        statut, rep, _ = self.appel('GET', '/', self.session(jeton))
        self.assertEqual(statut, '303 See Other')
        lien = dict(rep)['Location']
        self.assertTrue(lien.startswith('/agent-courriel/poste-session?'), lien)
        return self.appel('GET', lien, self.session(jeton))

    def instance(self):
        return list(self.app.instances.values())[0]

    def csrf(self, page):
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)


# ---------------------------------------------------------------------------------------------------------------- D1
class D1Origine(Base108):
    def test_D1_referrer_policy_same_origin_sur_toutes_les_reponses(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        self.ouvrir_et_suivre(jeton)
        for cookies, chemin in ((None, '/'), (None, '/?langue=de'), (None, '/administration'), (None, '/healthz'),
                                (self.session(jeton), '/_demo/session'), (self.session(jeton), '/agent-courriel/aujourdhui')):
            _, rep, _ = self.appel('GET', chemin, cookies)
            self.assertEqual(self.entete(rep, 'Referrer-Policy'), ['same-origin'], chemin)
        self.assertNotIn('no-referrer', json.dumps(dh.securiser([])))

    def test_D1_chrome_origin_null_et_meme_origine_admis(self):
        statut, jeton, _ = self.demarrer(NAVIGATEUR_NO_REFERRER)
        self.assertEqual((statut, bool(jeton), len(self.lanceur.creees)), ('303 See Other', True, 1))

    def test_D1_origine_reelle_admise_comme_avant(self):
        statut, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        self.assertEqual((statut, bool(jeton)), ('303 See Other', True))

    def test_D1_origin_null_refuse_hors_meme_origine(self):
        pre = self.pre()
        cas = (
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_SEC_FETCH_SITE': 'cross-site'},     # cadre « sandbox » ou page data: d'un autre site
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_SEC_FETCH_SITE': 'same-site'},      # autre sous-domaine du même site
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_SEC_FETCH_SITE': 'none'},           # adresse tapée, favori
            {k: v for k, v in NAVIGATEUR_NO_REFERRER.items() if k != 'HTTP_SEC_FETCH_SITE'},   # navigateur sans Sec-Fetch-*
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_SEC_FETCH_SITE': 'Same-Origin'},    # valeur non canonique
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_ORIGIN': 'NULL'},
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_ORIGIN': ''},                       # aucune origine, même avec same-origin
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_ORIGIN': 'https://autre.example.test'},   # autre site, Sec-Fetch-Site forgé
            {**NAVIGATEUR_NO_REFERRER, 'HTTP_ORIGIN': 'http://demo.example.test'},
        )
        for entetes in cas:
            statut, jeton, page = self.demarrer(entetes, pre=pre)
            self.assertEqual((statut, jeton), ('403 Forbidden', None), entetes)
            self.assertIn('Demande refusée', page)
        self.assertEqual(self.lanceur.creees, [])

    def test_D1_origin_null_exige_toujours_le_jeton_du_formulaire(self):
        pre = self.pre()
        for champs in ({'pre': ''}, {'pre': 'B' * 43}):
            statut, _, page = self.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre}, urlencode(champs).encode(), origine=None,
                                         entetes=NAVIGATEUR_NO_REFERRER)
            self.assertEqual(statut, '403 Forbidden')
            self.assertIn('Le formulaire a expiré', page)
        self.assertEqual(self.lanceur.creees, [])

    def test_D1_origin_null_terminer_exige_le_jeton_de_session(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_NO_REFERRER)
        self.ouvrir_et_suivre(jeton)
        csrf = self.csrf(self.appel('GET', '/_demo/session', self.session(jeton))[2])
        refus = self.appel('POST', '/_demo/terminer', self.session(jeton), b'csrf=faux', origine=None, entetes=NAVIGATEUR_NO_REFERRER)
        self.assertEqual(refus[0], '403 Forbidden')
        self.assertEqual(len(self.app.instances), 1)
        statut, _, page = self.appel('POST', '/_demo/terminer', self.session(jeton), urlencode({'csrf': csrf}).encode(), origine=None,
                                     entetes=NAVIGATEUR_NO_REFERRER)
        self.assertEqual(statut, '200 OK')
        self.assertIn('effacé', page)
        self.assertEqual(self.app.instances, {})

    def test_D1_mutation_sans_sec_fetch_site_un_cadre_isole_lancerait_des_instances(self):
        m = mutant([("    return environ.get('HTTP_ORIGIN', '') == 'null' and environ.get('HTTP_SEC_FETCH_SITE', '') == 'same-origin'",
                     "    return environ.get('HTTP_ORIGIN', '') == 'null'")])
        app = self.fabriquer(m)
        statut, _, _ = self.demarrer({**NAVIGATEUR_NO_REFERRER, 'HTTP_SEC_FETCH_SITE': 'cross-site'}, app=app)
        self.assertEqual(statut, '303 See Other')            # test_D1_origin_null_refuse_hors_meme_origine le détecte


# ---------------------------------------------------------------------------------------------------------------- D2
SONDE_RESEAU = r'''
import importlib.util, json, socket, sys
spec = importlib.util.spec_from_file_location('instance_essai', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
appels = []
def faux(hote, port=None, family=0, type=0, proto=0, flags=0):
    appels.append([str(hote), str(port), int(flags)])
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 0))]
if sys.argv[2] == 'faux':
    socket.getaddrinfo = faux        # aucune résolution réelle : on constate ce que la garde laisse passer
m.couper_reseau()
r = {}
def essai(nom, f):
    try:
        f()
        r[nom] = 'admis'
    except OSError as exc:
        r[nom] = exc.errno
essai('num_ipv4_attente', lambda: socket.getaddrinfo('0.0.0.0', '8080', socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP, socket.AI_PASSIVE))
essai('num_ipv4', lambda: socket.getaddrinfo('127.0.0.1', 8080))
essai('num_ipv6', lambda: socket.getaddrinfo('::1', 8080, socket.AF_INET6))
essai('num_ipv6_tout', lambda: socket.getaddrinfo('::', 8080, socket.AF_INET6, socket.SOCK_STREAM))
essai('nom_local', lambda: socket.getaddrinfo('localhost', 8080))
essai('nom', lambda: socket.getaddrinfo('demo.example.test', 443))
essai('nom_vide', lambda: socket.getaddrinfo('', 443))
essai('aucun_hote', lambda: socket.getaddrinfo(None, 443))
essai('prise_ipv4', lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM))
essai('prise_ipv6', lambda: socket.socket(socket.AF_INET6))
essai('prise_defaut', lambda: socket.socket())
essai('connexion', lambda: socket.create_connection(('192.0.2.1', 443), timeout=1))
essai('gethostbyname', lambda: socket.gethostbyname('demo.example.test'))
if hasattr(socket, 'AF_UNIX'):
    essai('prise_unix', lambda: socket.socket(socket.AF_UNIX).close())
print(json.dumps({'r': r, 'appels': appels, 'numerique': int(socket.AI_NUMERICHOST)}))
'''

SONDE_WAITRESS = r'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location('instance_essai', sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.couper_reseau()
import waitress
from waitress import create_server
s = create_server(lambda e, sr: [], unix_socket=sys.argv[2], unix_socket_perms='600', threads=1, ident='')
import importlib.metadata
print('ecoute', importlib.metadata.version('waitress'))
s.close()
'''


class D2GardeReseau(unittest.TestCase):
    NUMERIQUES = ('num_ipv4_attente', 'num_ipv4', 'num_ipv6', 'num_ipv6_tout')
    REFUSES = ('nom_local', 'nom', 'nom_vide', 'aucun_hote', 'prise_ipv4', 'prise_ipv6', 'prise_defaut', 'connexion', 'gethostbyname')

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='d56108-reseau-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / 'sonde.py').write_text(SONDE_RESEAU, encoding='utf-8')

    def sonder(self, instance=DEMO / 'instance.py', mode='faux'):
        sortie = subprocess.run([sys.executable, '-I', str(self.tmp / 'sonde.py'), str(instance), mode], capture_output=True, text=True,
                                timeout=60, env={**os.environ, 'PYTHONPATH': str(RACINE)})
        self.assertEqual(sortie.returncode, 0, sortie.stderr)
        return json.loads(sortie.stdout.strip().splitlines()[-1])

    def copie(self, avant, apres):
        source = (DEMO / 'instance.py').read_text(encoding='utf-8')
        self.assertIn(avant, source)
        chemin = self.tmp / ('instance-mutant-%d.py' % len(list(self.tmp.iterdir())))
        chemin.write_text(source.replace(avant, apres), encoding='utf-8')
        return chemin

    def test_D2_adresse_numerique_admise_sans_dns_nom_et_prise_ip_refuses(self):
        res = self.sonder()
        r, numerique = res['r'], res['numerique']
        for nom in self.NUMERIQUES:
            self.assertEqual(r[nom], 'admis', nom)
        for nom in self.REFUSES:
            self.assertEqual(r[nom], 101, nom)                  # ENETUNREACH : « aucun accès réseau »
        if 'prise_unix' in r:
            self.assertEqual(r['prise_unix'], 'admis')
        self.assertEqual([a[0] for a in res['appels']], ['0.0.0.0', '127.0.0.1', '::1', '::'])   # aucun nom n'atteint la résolution
        self.assertTrue(all(a[2] & numerique for a in res['appels']), res['appels'])            # AI_NUMERICHOST : jamais de DNS
        self.assertTrue(res['appels'][0][2] & socket.AI_PASSIVE)                                 # drapeaux de l'appelant conservés

    def test_D2_resolution_numerique_reelle_sans_reseau(self):
        r = self.sonder(mode='reel')['r']                          # vraie getaddrinfo, mais seulement avec AI_NUMERICHOST
        self.assertEqual((r['num_ipv4_attente'], r['num_ipv4']), ('admis', 'admis'))
        for nom in ('num_ipv6', 'num_ipv6_tout'):
            self.assertNotEqual(r[nom], 101, nom)                    # admis (ou IPv6 absent du système), jamais la garde
        for nom in self.REFUSES:
            self.assertEqual(r[nom], 101, nom)

    def test_D2_mutation_garde_5_6_107_waitress_ne_demarrait_pas(self):
        r = self.sonder(self.copie('    socket.getaddrinfo = _numerique_seulement', '    socket.getaddrinfo = _refus'))['r']
        self.assertEqual(r['num_ipv4_attente'], 101)              # le test ci-dessus le détecte (« Démarrage impossible »)

    def test_D2_mutation_sans_controle_du_nom_la_resolution_partirait(self):
        res = self.sonder(self.copie("        ipaddress.ip_address(str(hote).strip('[]').split('%')[0])", '        pass'))
        self.assertIn('demo.example.test', [a[0] for a in res['appels']])

    def test_D2_mutation_sans_ai_numerichost(self):
        res = self.sonder(self.copie('flags | socket.AI_NUMERICHOST', 'flags'))
        self.assertFalse(all(a[2] & res['numerique'] for a in res['appels']))

    @unittest.skipUnless(LINUX and importlib.util.find_spec('waitress'), 'Linux et waitress seulement')
    def test_D2_waitress_ecoute_sur_socket_unix_apres_la_garde(self):
        (self.tmp / 'w.py').write_text(SONDE_WAITRESS, encoding='utf-8')
        sortie = subprocess.run([sys.executable, str(self.tmp / 'w.py'), str(DEMO / 'instance.py'), str(self.tmp / 'i.sock')],
                                capture_output=True, text=True, timeout=60, env={**os.environ, 'PYTHONPATH': str(RACINE)})
        self.assertEqual(sortie.returncode, 0, sortie.stderr)
        self.assertTrue(sortie.stdout.startswith('ecoute'), sortie.stdout)
        print('\n[D2] waitress %s : écoute sur socket Unix après la garde réseau' % sortie.stdout.split()[-1], file=sys.stderr)


# ---------------------------------------------------------------------------------------------------------------- D3
class D3Livrables(unittest.TestCase):
    def lire(self, nom):
        return (DEMO / nom).read_text(encoding='utf-8')

    def test_D3_apache_bloc_location_referrer_secours_journal(self):
        apache = self.lire('apache-demo.example.conf')
        bloc = re.search(r'<Location /agent-courriel>(.*?)</Location>', apache, re.S)
        self.assertIsNotNone(bloc)
        for ligne in ('ProxyPass unix:/run/axiorhub-demo/demo.sock|http://demo.example.test/agent-courriel\n',
                      'ProxyPassReverse http://demo.example.test/agent-courriel', 'AuthType None', 'Require all granted'):
            self.assertIn(ligne, bloc.group(1))
        self.assertIn('Header always set Referrer-Policy "same-origin"', apache)
        self.assertIn('ErrorDocument 503 /_secours/indisponible.html', apache)
        self.assertIn('ErrorDocument 502 /_secours/indisponible.html', apache)
        self.assertLess(apache.index('ProxyPass /_secours/ !'), apache.index('ProxyPass / unix:'))   # exclusion avant le cas général
        self.assertNotIn('ProxyErrorOverride On', apache)           # « Démo complète » du répartiteur reste affichée
        self.assertLess(apache.index('ProxyPass / unix:'), apache.index('<Location /agent-courriel>'))   # connexion partagée, réglages de « ProxyPass / »
        self.assertIn('LogFormat "%{%Y-%m-%dT%H:%M:%S}t %>s %B" axiorhub_demo_minimal', apache)
        for interdit in ('%h', '%a', '%r', '%U', '%{User-Agent}', '%{Referer}'):
            self.assertNotIn(interdit, apache)
        nginx = self.lire('nginx-demo.example.conf')
        self.assertIn('error_page 502 503 /_secours/indisponible.html;', nginx)
        self.assertIn('add_header Referrer-Policy same-origin always;', nginx)

    def test_D3_lisezmoi_explique_le_cas_d_un_axiorhub_de_production(self):
        texte = self.lire('LISEZMOI.md')
        for attendu in ('Serveur qui héberge aussi AxiorHub en production', '<Location /agent-courriel>', 'conf-enabled', 'AuthType None',
                        'Referrer-Policy: same-origin', 'Origin: null', 'page de secours', 'journal minimal', 'a2enmod ssl proxy proxy_http headers alias',
                        'En cas de problème', 'Votre session de démonstration a été perdue', 'langue de la démonstration', '/?langue=en',
                        'Sec-Fetch-Site', '/server-status'):
            self.assertIn(attendu, texte)

    def test_D3_page_de_secours_quatre_langues_sans_script_ni_ressource_exterieure(self):
        page = (DEMO / 'secours' / 'indisponible.html').read_text(encoding='utf-8')
        for langue, mot in (('fr', 'momentanément indisponible'), ('en', 'temporarily unavailable'),
                            ('de', 'vorübergehend nicht verfügbar'), ('es', 'no disponible temporalmente')):
            self.assertIn('<section lang="%s">' % langue, page)
            self.assertIn(mot, page)
        self.assertNotIn('<script', page)
        self.assertNotRegex(page, r'(?:src|href)="(?:https?:)?//')
        self.assertNotRegex(page, r'\b\d{1,3}(?:\.\d{1,3}){3}\b')

    def test_D3_aucune_adresse_ni_nom_de_serveur_reel_dans_deploy_demo(self):
        for f in sorted(DEMO.rglob('*')):
            if f.is_file() and f.suffix in ('.md', '.conf', '.py', '.html', '.sh', '.json', '.yml', '.service', ''):
                texte = f.read_text(encoding='utf-8')
                for ip in re.findall(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', texte):
                    self.assertTrue(ip.startswith(('127.', '0.0.0.0', '192.0.2.', '198.51.100.', '203.0.113.')), (f.name, ip))
                self.assertNotIn('axiorhub.com', texte.replace('demo.axiorhub.com', ''), f.name)


# ---------------------------------------------------------------------------------------------------------------- D4
class D4SessionPerdue(Base108):
    def test_D4_session_marquee_seulement_apres_un_echange_reussi(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        inst = self.instance()
        statut, rep, _ = self.appel('GET', '/', self.session(jeton))
        self.assertEqual(statut, '303 See Other')
        self.assertFalse(inst.session)                              # le lien donné au navigateur ne suffit pas
        self.assertIsNotNone(inst.attente)
        statut, rep, _ = self.appel('GET', dict(rep)['Location'], self.session(jeton))
        self.assertEqual((statut, dict(rep)['Location']), ('303 See Other', '/agent-courriel/aujourdhui'))
        self.assertTrue(self.cookie(rep, 'axh_poste'))
        self.assertEqual((inst.session, inst.attente), (True, None))  # échange constaté sur la réponse de l'instance
        self.assertTrue(self.appel('GET', '/', self.session(jeton))[2].startswith('instance '))

    def test_D4_echange_refuse_page_session_perdue_et_nouveau_lien(self):
        self.lanceur.echange = 'refus'
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        statut, rep, page = self.ouvrir_et_suivre(jeton)
        inst = self.instance()
        self.assertEqual(statut, '403 Forbidden')
        self.assertIn('Votre session de démonstration a été perdue', page)
        self.assertIn('action="/_demo/recommencer"', page)
        self.assertIn('>Recommencer</button>', page)
        self.assertEqual(self.entete(rep, 'WWW-Authenticate'), [])
        self.assertEqual((inst.session, inst.attente), (False, None))
        self.assertTrue(self.lanceur.flux[-1].ferme)                 # réponse de l'instance écartée, connexion fermée
        self.lanceur.echange = 'ok'
        statut, rep, _ = self.appel('GET', '/', self.session(jeton))  # la page suivante redonne un lien neuf
        self.assertEqual(statut, '303 See Other')
        self.assertIn('fictif-%s-2' % inst.ident, dict(rep)['Location'])

    def test_D4_echange_sans_cookie_de_session_n_ouvre_rien(self):
        self.lanceur.echange = 'sans-cookie'
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        statut, _, page = self.ouvrir_et_suivre(jeton)
        self.assertEqual(statut, '403 Forbidden')
        self.assertIn('session de démonstration a été perdue', page)
        self.assertFalse(self.instance().session)

    def test_D4_reponse_401_basic_jamais_transmise(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        self.ouvrir_et_suivre(jeton)
        inst = self.instance()
        self.lanceur.refus_401.add('/agent-courriel/aujourdhui')
        statut, rep, page = self.appel('GET', '/agent-courriel/aujourdhui', self.session(jeton), entetes=NAVIGATEUR_SAME_ORIGIN)
        self.assertEqual(statut, '403 Forbidden')
        self.assertEqual(self.entete(rep, 'WWW-Authenticate'), [])
        self.assertNotIn('401', statut)
        self.assertIn('<title>Votre session de démonstration a été perdue</title>', page)
        csrf = self.csrf(page)
        self.assertEqual(csrf, inst.csrf)
        self.assertIn('Reprendre ma démonstration', page)
        self.assertEqual((inst.session, inst.attente), (False, None))
        self.assertTrue(self.lanceur.flux[-1].ferme)
        self.assertEqual(self.entete(rep, 'Content-Security-Policy'), [dh.CSP])   # page du répartiteur, CSP stricte

    def test_D4_401_d_un_appel_de_script_reponse_json_sans_fenetre(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        self.ouvrir_et_suivre(jeton)
        self.lanceur.refus_401.add('/agent-courriel/api440/m530/etat')
        for entetes in ({'HTTP_SEC_FETCH_MODE': 'cors', 'HTTP_ACCEPT': '*/*'}, {'HTTP_ACCEPT': 'application/json'}):
            statut, rep, corps = self.appel('GET', '/agent-courriel/api440/m530/etat', self.session(jeton), entetes=entetes)
            self.assertEqual(statut, '403 Forbidden')
            self.assertEqual(self.entete(rep, 'WWW-Authenticate'), [])
            self.assertEqual(json.loads(corps)['error'], 'session_demo_perdue')
            self.assertEqual(dict(rep)['Content-Type'], 'application/json; charset=utf-8')

    def test_D4_www_authenticate_retire_de_toute_reponse_de_l_instance(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN)
        self.ouvrir_et_suivre(jeton)
        self.lanceur.basic_200 = True
        statut, rep, _ = self.appel('GET', '/agent-courriel/aujourdhui', self.session(jeton))
        self.assertEqual((statut, self.entete(rep, 'WWW-Authenticate')), ('200 OK', []))

    def test_D4_recommencer_et_reprendre_depuis_la_page_perdue(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN, {'langue': 'es'})
        self.ouvrir_et_suivre(jeton)
        self.lanceur.refus_401.add('/agent-courriel/aujourdhui')
        _, _, page = self.appel('GET', '/agent-courriel/aujourdhui', self.session(jeton))
        self.assertIn('Se ha perdido su sesión de demostración', page)      # dans la langue de l'instance
        csrf = self.csrf(page)
        statut, rep, _ = self.appel('POST', '/_demo/ouvrir', self.session(jeton), urlencode({'csrf': csrf}).encode(), origine=None,
                                    entetes=NAVIGATEUR_NO_REFERRER)
        self.assertEqual(statut, '303 See Other')                            # « Reprendre » : même instance, lien neuf
        self.assertIn('/agent-courriel/poste-session?', dict(rep)['Location'])
        ancien = self.instance()
        statut, rep, _ = self.appel('POST', '/_demo/recommencer', self.session(jeton), urlencode({'csrf': csrf}).encode(),
                                    origine=None, entetes=NAVIGATEUR_NO_REFERRER)
        neuf = self.cookie(rep, dh.NOM_COOKIE).split(';')[0].split('=', 1)[1]
        self.assertEqual(statut, '303 See Other')
        self.assertNotEqual(neuf, jeton)
        self.assertFalse(ancien.dossier.exists())
        self.assertEqual((self.instance().langue, self.lanceur.langues), ('es', ['es', 'es']))   # même langue

    def test_D4_reponse_ecartee_connexion_fermee_meme_sans_lecture(self):
        fermetures = []
        connexion = types.SimpleNamespace(close=lambda: fermetures.append(1))
        dh._fermer(dh._flux(io.BytesIO(b'<p>401</p>'), connexion))           # 401 écarté : corps jamais lu
        self.assertEqual(fermetures, [1])
        self.assertEqual(b''.join(dh._flux(io.BytesIO(b'abc'), connexion)), b'abc')   # lecture complète : fermée aussi
        self.assertEqual(len(fermetures), 2)

    def test_D4_mutation_ancien_marquage_la_session_restait_ouverte_apres_un_echec(self):
        m = mutant([("        inst.attente = self.horloge()\n        return self._reponse(start, '303 See Other', b'', 'text/plain', "
                     "[('Location', lien)])",
                     "        inst.session = True\n        return self._reponse(start, '303 See Other', b'', 'text/plain', "
                     "[('Location', lien)])")])
        app = self.fabriquer(m)
        self.lanceur.echange = 'refus'
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN, app=app)
        lien = dict(self.appel('GET', '/', self.session(jeton), app=app)[1])['Location']
        statut, _, _ = self.appel('GET', lien, self.session(jeton), app=app)
        self.assertEqual(statut, '403 Forbidden')
        self.assertTrue(list(app.instances.values())[0].session)    # test_D4_echange_refuse_… le détecte

    def test_D4_mutation_sans_interception_du_401_la_fenetre_basic_s_ouvrait(self):
        m = mutant([("        if code == 401:", "        if False:"),
                    ("('server', 'x-powered-by', 'www-authenticate')", "('server', 'x-powered-by')")])
        app = self.fabriquer(m)
        _, jeton, _ = self.demarrer(NAVIGATEUR_SAME_ORIGIN, app=app)
        lien = dict(self.appel('GET', '/', self.session(jeton), app=app)[1])['Location']
        self.appel('GET', lien, self.session(jeton), app=app)
        self.lanceur.refus_401.add('/agent-courriel/aujourdhui')
        statut, rep, _ = self.appel('GET', '/agent-courriel/aujourdhui', self.session(jeton), app=app)
        self.assertEqual(statut, '401 Unauthorized')
        self.assertTrue(self.entete(rep, 'WWW-Authenticate'))       # test_D4_reponse_401_basic_jamais_transmise le détecte


# ---------------------------------------------------------------------------------------------------------------- D5
class Silencieux(WSGIRequestHandler):
    def log_message(self, *a):
        pass


class D5TestFumee(unittest.TestCase):
    def setUp(self):
        self.fumee = charger_fichier('test_fumee_demo56108', DEMO / 'test-fumee.py')
        self.tmp = Path(tempfile.mkdtemp(prefix='d56108-fumee-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        r = dh.charger_reglages(None, environ={})
        r.update({'dossier_instances': str(self.tmp / 'i'), 'dossier_journal': str(self.tmp / 'j'), 'origine': ORIGINE})
        self.lanceur = FauxLanceur108()
        self.app = dh.Repartiteur(r, self.lanceur)
        self.app.preparer()

    def servir(self, app):
        serveur = make_server('127.0.0.1', 0, app, handler_class=Silencieux)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        return 'http://127.0.0.1:%d' % serveur.server_port

    def parcours(self, app, sans_instance=False):
        lignes = []
        echecs = self.fumee.parcours(self.servir(app), sans_instance=sans_instance, sortie=lignes.append, attente=5, origine=ORIGINE)
        return echecs, lignes

    def test_D5_parcours_complet_comme_un_navigateur(self):
        vus = []

        def enregistreur(environ, start):
            if environ.get('PATH_INFO') == '/_demo/commencer':
                vus.append((environ.get('HTTP_ORIGIN'), environ.get('HTTP_SEC_FETCH_SITE'), environ.get('HTTP_SEC_FETCH_MODE')))
            return self.app(environ, start)
        echecs, lignes = self.parcours(enregistreur)
        self.assertEqual(echecs, [], '\n'.join(lignes))
        texte = '\n'.join(lignes)
        for attendu in ('Referrer-Policy: same-origin', 'accueil en en', 'accueil en de', 'accueil en es',
                        '/agent-courriel/… sans session : 303 du répartiteur', 'adresse sans session : retour à l’accueil',
                        'démarrage depuis un autre site refusé', 'démarrage comme un navigateur', 'aucune fenêtre de mot de passe'):
            self.assertIn(attendu, texte)
        self.assertIn(('null', 'same-origin', 'navigate'), vus)          # le démarrage réussi est celui d'un navigateur « no-referrer »
        self.assertIn(('null', 'cross-site', 'navigate'), vus)
        self.assertEqual((len(self.lanceur.creees), self.app.instances), (1, {}))

    def test_D5_un_autre_axiorhub_sous_agent_courriel_est_signale(self):
        def production(environ, start):                 # règles globales d'un AxiorHub de production sur le même serveur
            if environ.get('PATH_INFO', '').startswith('/agent-courriel'):
                start('400 Bad Request', [('Content-Type', 'text/plain; charset=utf-8')])
                return [b'hote refuse']
            return self.app(environ, start)
        echecs, lignes = self.parcours(production, sans_instance=True)
        message = [x for x in echecs if x.startswith('/agent-courriel/')]
        self.assertTrue(message, '\n'.join(lignes))
        self.assertIn('code 400', message[0])
        self.assertIn('<Location /agent-courriel>', message[0])
        self.assertIn('LISEZMOI.md', message[0])

    def test_D5_une_fenetre_de_mot_de_passe_est_signalee(self):
        def production(environ, start):
            if environ.get('PATH_INFO', '').startswith('/agent-courriel'):
                start('401 Unauthorized', [('Content-Type', 'text/plain'), ('WWW-Authenticate', 'Basic realm="AxiorHub Cabinet"')])
                return [b'']
            return self.app(environ, start)
        echecs, _ = self.parcours(production, sans_instance=True)
        self.assertTrue(any(x.startswith('aucune fenêtre de mot de passe') and '<Location /agent-courriel>' in x for x in echecs), echecs)

    def test_D5_le_403_d_apache_pour_server_status_est_admis_pas_ailleurs(self):
        refuses = {'/server-status'}

        def apache(environ, start):
            if environ.get('PATH_INFO') in refuses:
                start('403 Forbidden', [('Content-Type', 'text/html')])
                return [b'Forbidden']
            return self.app(environ, start)
        echecs, lignes = self.parcours(apache, sans_instance=True)
        self.assertEqual(echecs, [], '\n'.join(lignes))
        refuses.add('/administration')
        echecs, _ = self.parcours(apache, sans_instance=True)
        self.assertTrue(any("chemins d'administration" in x and '/administration' in x for x in echecs), echecs)

    def test_D5_no_referrer_est_signale(self):
        def ancien(environ, start):
            def start2(statut, entetes, *exc):
                return start(statut, [(k, 'no-referrer' if k.lower() == 'referrer-policy' else v) for k, v in entetes], *exc)
            return self.app(environ, start2)
        echecs, _ = self.parcours(ancien, sans_instance=True)
        self.assertTrue(any(x.startswith('Referrer-Policy: same-origin') and 'no-referrer' in x for x in echecs), echecs)


# ---------------------------------------------------------------------------------------------------------------- D6
class D6Langues(Base108):
    def test_D6_langue_du_navigateur_proposee_liste_fermee(self):
        for entete, attendue in (('de-DE,de;q=0.9,en;q=0.8', 'de'), ('en-GB,en;q=0.9', 'en'), ('es-419,es;q=0.9', 'es'),
                                 ('it-IT,it;q=0.9', 'fr'), ('', 'fr'), ('en;q=0.5, es;q=0.8', 'es'), ('es;q=0, en;q=0.1', 'en'),
                                 ('pt-BR, fr-CA;q=0.4', 'fr'), ('*', 'fr'), ('de;q=abc, en', 'en'), ('x' * 5000, 'fr')):
            self.assertEqual(dh.langue_du_navigateur(entete), attendue, entete)
        self.assertEqual(dh.langue_demandee({'QUERY_STRING': 'langue=es', 'HTTP_ACCEPT_LANGUAGE': 'de'}), 'es')
        self.assertEqual(dh.langue_demandee({'QUERY_STRING': 'langue=it', 'HTTP_ACCEPT_LANGUAGE': 'de'}), 'de')
        self.assertEqual(dh.LANGUES, ('fr', 'en', 'de', 'es'))
        from agent import demo56107
        self.assertEqual(set(dh.LANGUES), set(demo56107.LANGUES))            # même liste que le mode Démo

    def test_D6_accueil_traduit_et_langue_du_navigateur_preselectionnee(self):
        for langue, accept in (('fr', 'fr-FR,fr;q=0.9'), ('en', 'en-GB,en;q=0.9'), ('de', 'de-DE,de;q=0.9'), ('es', 'es-ES,es;q=0.9')):
            statut, rep, page = self.appel('GET', '/', entetes={'HTTP_ACCEPT_LANGUAGE': accept})
            self.assertEqual(statut, '200 OK')
            self.assertIn('<html lang="%s">' % langue, page)
            self.assertIn(BOUTONS[langue], page)
            self.assertIn('<option value="%s" lang="%s" selected>' % (langue, langue), page)
            self.assertEqual(page.count(' selected>'), 1)
            self.assertIn('aria-current="page">%s</a>' % dh.NOMS_LANGUES[langue], page)
            for autre in dh.LANGUES:
                self.assertIn('<option value="%s"' % autre, page)                    # liste fermée, complète
            self.assertTrue(self.cookie(rep, dh.NOM_PRE))
            if langue != 'fr':
                for francais in ('Commencer la démo', 'Données fictives', 'Vie privée', 'Langue de la démonstration'):
                    self.assertNotIn(francais, page)
        page = self.appel('GET', '/?langue=de', entetes={'HTTP_ACCEPT_LANGUAGE': 'es'})[2]
        self.assertIn('<html lang="de">', page)                                       # lien de langue explicite
        self.assertIn('2 h', page)

    def test_D6_traductions_completes_et_coherentes(self):
        fr = dh.TRADUCTIONS['fr']
        motif = re.compile(r'%[sd]|\{accueil\}')
        for langue in dh.LANGUES:
            t = dh.TRADUCTIONS[langue]
            self.assertEqual(set(t), set(fr), langue)
            for cle, valeur in fr.items():
                if isinstance(valeur, str):
                    self.assertEqual(motif.findall(t[cle]), motif.findall(valeur), (langue, cle))
                    self.assertEqual(t[cle].count('<p>'), t[cle].count('</p>'), (langue, cle))
            self.assertEqual(len(t['minute']), 2)
        self.assertEqual(dh.texte('de', 'fin').count('href="/?langue=de"'), 1)
        self.assertEqual(dh.texte('fr', 'fin').count('href="/"'), 1)                 # français : adresses de la 5.6.107

    def test_D6_langue_choisie_passee_au_lanceur(self):
        for langue in ('en', 'de', 'es'):
            statut, jeton, _ = self.demarrer(NAVIGATEUR_NO_REFERRER, {'langue': langue}, ip='192.0.2.%d' % len(self.lanceur.creees))
            self.assertEqual(statut, '303 See Other')
            inst = [i for i in self.app.instances.values() if i.langue == langue]
            self.assertEqual(len(inst), 1)
        statut, _, _ = self.demarrer(NAVIGATEUR_NO_REFERRER, {'langue': 'fr'}, ip=IP_B)
        self.assertEqual(statut, '303 See Other')
        self.assertEqual(self.lanceur.langues, ['en', 'de', 'es', '(absente)'])    # français : appel de la 5.6.107 inchangé

    def test_D6_sans_champ_la_langue_du_navigateur(self):
        statut, _, _ = self.demarrer({**NAVIGATEUR_NO_REFERRER, 'HTTP_ACCEPT_LANGUAGE': 'es-ES,es;q=0.9'})
        self.assertEqual((statut, self.lanceur.langues), ('303 See Other', ['es']))

    def test_D6_liste_fermee_refus_sans_instance(self):
        pre = self.pre()
        for valeur in ('it', 'FR', 'fr;rm -rf', '../en', 'en\n', ' en', 'fr-FR'):
            statut, jeton, page = self.demarrer(NAVIGATEUR_NO_REFERRER, {'langue': valeur}, pre=pre)
            self.assertEqual((statut, jeton), ('400 Bad Request', None), valeur)
        self.assertEqual((self.lanceur.creees, self.app.instances), ([], {}))
        with self.assertRaises(ValueError):
            self.app.creer({'REMOTE_ADDR': 'localhost', 'HTTP_X_FORWARDED_FOR': IP_A}, 'it')
        lanceur = dh.LanceurLocal.__new__(dh.LanceurLocal)
        with self.assertRaises(ValueError):
            lanceur.creer('0123abcd', self.tmp, langue='it')

    def test_D6_pages_du_repartiteur_dans_la_langue_de_l_instance(self):
        _, jeton, _ = self.demarrer(NAVIGATEUR_NO_REFERRER, {'langue': 'es'})
        self.lanceur.pret_apres = 0
        page = self.appel('GET', '/_demo/session', self.session(jeton))[2]
        self.assertIn('<html lang="es">', page)
        for texte in ('Su demostración está abierta', 'Volver a la demostración', 'Empezar de cero', 'Terminar y borrarlo todo'):
            self.assertIn(texte, page)
        statut, rep, page = self.appel('POST', '/_demo/terminer', self.session(jeton), urlencode({'csrf': self.csrf(page)}).encode(),
                                       origine=None, entetes=NAVIGATEUR_NO_REFERRER)
        self.assertEqual(statut, '200 OK')
        self.assertIn('Su demostración ha terminado', page)
        self.assertIn('href="/?langue=es"', page)
        self.assertIn('Max-Age=0', self.cookie(rep, dh.NOM_COOKIE))

    def test_D6_page_d_attente_et_404_traduites_404_neutre(self):
        self.lanceur.pret_apres = 5
        _, jeton, _ = self.demarrer(NAVIGATEUR_NO_REFERRER, {'langue': 'de'})
        self.assertIn('Ihre Demo wird vorbereitet', self.appel('GET', '/', self.session(jeton))[2])
        allemand = {'HTTP_ACCEPT_LANGUAGE': 'de'}
        neutre = self.appel('GET', '/_demo/inconnu', entetes=allemand)[2]
        self.assertIn('Seite nicht gefunden', neutre)
        for cookies in (None, self.session(jeton)):                         # la 404 ne dépend jamais de l'instance du cookie
            self.assertEqual(self.appel('GET', '/administration', cookies, entetes=allemand)[2], neutre)
        self.assertIn('Page introuvable', self.appel('GET', '/administration', self.session(jeton))[2])

    def test_D6_commande_de_l_instance_porte_la_langue(self):
        r = dh.charger_reglages(None, environ={})
        lanceur = dh.LanceurLocal.__new__(dh.LanceurLocal)
        lanceur.r, lanceur.racine = r, RACINE
        lanceur.cgroups = dh.Cgroups(self.tmp / 'sans-cgroup')
        lanceur.cgroups.base = None
        commandes = []
        for i, langue in enumerate(('de', 'fr')):
            dossier = self.tmp / ('inst%d' % i)
            dossier.mkdir()
            with mock.patch.object(dh.subprocess, 'Popen', lambda c, **k: commandes.append(c) or types.SimpleNamespace(pid=1)):
                lanceur.creer('0123abc%d' % i, dossier, langue=langue)
        self.assertEqual(commandes[0][-2:], ['--langue', 'de'])
        self.assertNotIn('--langue', commandes[1])                              # français : commande de la 5.6.107 inchangée


class D6Instance(unittest.TestCase):
    """deploy/demo/instance.py : ``--langue`` (liste fermée) passée à ``demo56107.creer``."""

    def setUp(self):
        self.instance = charger_fichier('instance_demo56108', DEMO / 'instance.py')
        self.tmp = Path(tempfile.mkdtemp(prefix='d56108-inst-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def faux_demo(self):
        appels = []
        faux = types.ModuleType('agent.demo56107')

        def creer(dossier, graine=None, origine='', prefixe='', **options):
            appels.append(options)
            Path(dossier).mkdir(parents=True, exist_ok=True)
            return str(Path(dossier) / 'config.json')
        faux.creer = creer
        faux.actif = lambda config: True
        faux.environnement = lambda chemin: {'AXIORHUB_DEMO': '1'}
        return faux, appels

    def test_D6_langue_transmise_a_demo56107(self):
        import agent
        faux, appels = self.faux_demo()
        with mock.patch.dict(sys.modules, {'agent.demo56107': faux}), mock.patch.object(agent, 'demo56107', faux, create=True), \
                mock.patch('agent.common.load_config', lambda chemin: {}), mock.patch.dict(os.environ, {}):
            self.instance.creer(self.tmp / 'a', ORIGINE, '/agent-courriel', 'de')
            self.instance.creer(self.tmp / 'b', ORIGINE, '/agent-courriel', 'fr')
            with self.assertRaises(SystemExit) as ctx:
                self.instance.creer(self.tmp / 'c', ORIGINE, '/agent-courriel', 'it')
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual(appels, [{'langue': 'de'}, {}])                       # français : appel de la 5.6.107 inchangé

    def test_D6_option_langue_liste_fermee(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            self.instance.main(['--dossier', str(self.tmp), '--socket', str(self.tmp / 's'), '--origine', ORIGINE, '--langue', 'it'])
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(self.instance.LANGUES, dh.LANGUES)

    @unittest.skipUnless(importlib.util.find_spec('cryptography'), 'cryptography absente')
    def test_D6_api_reelle_cabinet_fictif_en_allemand(self):
        from agent import demo56107
        self.addCleanup(demo56107.desarmer)
        with mock.patch.dict(os.environ, {}):
            os.environ.pop('AXIORHUB_DATA_DIR', None)
            config = self.instance.creer(self.tmp, ORIGINE, '/agent-courriel', 'de')
            self.assertEqual(demo56107.langue_de(config), 'de')
            cfg = json.loads(Path(config).read_text(encoding='utf-8'))
            self.assertEqual(cfg['interface']['langue'], 'de')


# ---------------------------------------------------------------------------------------------------------------- D7
class ClientUnix:
    """Navigateur minimal sur le socket Unix du répartiteur (en-têtes d'un vrai navigateur, cookies suivis à la main)."""

    def __init__(self, chemin):
        self.chemin = chemin
        self.basic = []

    def appel(self, methode, cible, cookies=None, corps=b'', entetes=None):
        c = dh._ConnexionUnix(self.chemin, 300)
        h = {'Host': 'demo.example.test', 'User-Agent': CHROME, 'X-Forwarded-For': IP_A, 'X-Forwarded-Proto': 'https'}
        if cookies:
            h['Cookie'] = '; '.join('%s=%s' % kv for kv in cookies.items())
        if corps:
            h['Content-Type'] = 'application/x-www-form-urlencoded'
        h.update(entetes or {})
        try:
            c.request(methode, cible, body=corps or None, headers=h)
            r = c.getresponse()
            recus = [(k.lower(), v) for k, v in r.getheaders()]
            if any(k == 'www-authenticate' for k, _ in recus):
                self.basic.append(cible)
            return r.status, recus, r.read().decode('utf-8', 'replace')
        finally:
            c.close()

    @staticmethod
    def cookie(recus, nom):
        for k, v in recus:
            if k == 'set-cookie' and v.split('=', 1)[0] == nom:
                return v.split(';', 1)[0].split('=', 1)[1]
        return None


@unittest.skipUnless(LINUX and importlib.util.find_spec('waitress') and importlib.util.find_spec('cryptography')
                     and os.environ.get('AXH_DEMO_BOUT_EN_BOUT') == '1',
                     'bout en bout réel : Linux, waitress, cryptography et AXH_DEMO_BOUT_EN_BOUT=1 (conteneur python:3.12 --network none)')
class D7BoutEnBout(unittest.TestCase):
    NAVIGATION = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Sec-Fetch-Mode': 'navigate',
                  'Sec-Fetch-Dest': 'document', 'Sec-Fetch-Site': 'same-origin'}

    def setUp(self):
        from waitress import create_server
        self.tmp = Path(tempfile.mkdtemp(prefix='d56108-e2e-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        r = dh.charger_reglages(None, environ={})
        r.update({'dossier_instances': str(self.tmp / 'instances'), 'dossier_journal': str(self.tmp / 'journal'), 'origine': ORIGINE,
                  'creations_par_ip_heure': 50, 'delai_demarrage_secondes': 300})
        self.lanceur = dh.LanceurLocal(r, cgroups=dh.Cgroups(self.tmp / 'sans-cgroup'))
        self.lanceur.cgroups.base = None
        self.app = dh.Repartiteur(r, self.lanceur)
        self.app.preparer()
        self.addCleanup(self.app.arreter_tout)
        self.socket = str(self.tmp / 'demo.sock')
        self.serveur = create_server(self.app, unix_socket=self.socket, unix_socket_perms='600', threads=16, ident='')
        threading.Thread(target=self.serveur.run, daemon=True).start()
        self.addCleanup(self.serveur.close)
        self.client = ClientUnix(self.socket)

    def parcours(self, langue):
        cl, NOM = self.client, dh.NOM_COOKIE
        accept = {'fr': 'fr-FR,fr;q=0.9', 'en': 'en-GB,en;q=0.9', 'de': 'de-DE,de;q=0.9,en;q=0.5', 'es': 'es-ES,es;q=0.9'}[langue]
        statut, recus, page = cl.appel('GET', '/', entetes={**self.NAVIGATION, 'Accept-Language': accept, 'Sec-Fetch-Site': 'none'})
        self.assertEqual(statut, 200)
        self.assertIn(BOUTONS[langue], page)
        self.assertIn('<option value="%s" lang="%s" selected>' % (langue, langue), page)
        self.assertEqual(dict(recus)['referrer-policy'], 'same-origin')
        pre = cl.cookie(recus, dh.NOM_PRE)
        # « Commencer la démo » tel que l'envoie Chrome depuis une page « no-referrer » (cas du serveur réel)
        statut, recus, _ = cl.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre}, urlencode({'pre': pre, 'langue': langue}).encode(),
                                    {**self.NAVIGATION, 'Origin': 'null', 'Accept-Language': accept})
        self.assertEqual(statut, 303)
        jeton = cl.cookie(recus, NOM)
        cookies = {NOM: jeton}
        limite = time.time() + 240
        while True:
            statut, recus, page = cl.appel('GET', '/', cookies, entetes=self.NAVIGATION)
            if statut != 200 or time.time() > limite:
                break
            self.assertIn(dh.TRADUCTIONS[langue]['titre_preparation'], page)
            time.sleep(0.5)
        self.assertEqual(statut, 303, page)
        lien = dict(recus)['location']
        self.assertTrue(lien.startswith('/agent-courriel/poste-session?jeton='), lien)
        statut, recus, _ = cl.appel('GET', lien, cookies, entetes=self.NAVIGATION)        # échange réel du jeton de lancement
        self.assertEqual((statut, dict(recus)['location']), (303, '/agent-courriel/aujourdhui'))
        cookies['axh_poste'] = cl.cookie(recus, 'axh_poste')
        self.assertTrue(cookies['axh_poste'])
        inst = [i for i in self.app.instances.values() if i.langue == langue][0]
        self.assertTrue(inst.session)
        statut, recus, page = cl.appel('GET', '/agent-courriel/aujourdhui', cookies, entetes={**self.NAVIGATION, 'Accept-Language': accept})
        self.assertEqual(statut, 200, page[:400])
        self.assertIn('<html lang="%s"' % langue, page)
        self.assertIn(BANDEAUX[langue], page)
        # Session perdue (cookie de l'instance effacé) : la vraie instance répond 401 « Basic » ; le visiteur voit la page du répartiteur
        statut, recus, page = cl.appel('GET', '/agent-courriel/aujourdhui', {NOM: jeton}, entetes=self.NAVIGATION)
        self.assertEqual(statut, 403)
        self.assertIn(dh.TRADUCTIONS[langue]['titre_perdue'], page)
        self.assertFalse(inst.session)
        # Reprise : la page suivante rouvre une session (lien neuf, échange réel)
        statut, recus, _ = cl.appel('GET', '/', {NOM: jeton}, entetes=self.NAVIGATION)
        self.assertEqual(statut, 303)
        statut, recus, _ = cl.appel('GET', dict(recus)['location'], {NOM: jeton}, entetes=self.NAVIGATION)
        self.assertEqual(statut, 303)
        self.assertEqual(cl.appel('GET', '/agent-courriel/aujourdhui', {NOM: jeton, 'axh_poste': cl.cookie(recus, 'axh_poste')},
                                  entetes=self.NAVIGATION)[0], 200)
        statut, _, page = cl.appel('GET', '/_demo/session', {NOM: jeton}, entetes=self.NAVIGATION)
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        statut, _, page = cl.appel('POST', '/_demo/terminer', {NOM: jeton}, urlencode({'csrf': csrf}).encode(),
                                   {**self.NAVIGATION, 'Origin': ORIGINE})
        self.assertEqual(statut, 200)
        self.assertFalse(inst.dossier.exists())
        return inst

    def test_D7_bout_en_bout_dans_chaque_langue_sans_reseau(self):
        import importlib.metadata
        import gc
        import warnings
        debut = time.time()
        with warnings.catch_warnings(record=True) as avertissements:
            warnings.simplefilter('always', ResourceWarning)
            for langue in dh.LANGUES:
                with self.subTest(langue=langue):
                    self.parcours(langue)
            gc.collect()
        fuites = [str(a.message) for a in avertissements if issubclass(a.category, ResourceWarning) and 'instance.sock' in str(a.message)]
        self.assertEqual(fuites, [])                                    # connexions vers les instances toutes fermées
        self.assertEqual(self.client.basic, [])                         # aucune fenêtre de mot de passe, à aucun moment
        self.assertEqual(self.app.instances, {})
        if os.environ.get('AXH_DEMO_RESEAU_COUPE') == '1':
            with self.assertRaises(OSError):
                socket.create_connection(('192.0.2.1', 443), timeout=2).close()
        print('\n[D7] waitress %s, quatre langues, %.0f s' % (importlib.metadata.version('waitress'), time.time() - debut), file=sys.stderr)


if __name__ == '__main__':
    unittest.main()

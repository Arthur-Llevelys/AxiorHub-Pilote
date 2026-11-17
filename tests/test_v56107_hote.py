"""5.6.107, lot H : démo en ligne, une instance isolée par visiteur (``agent/demo_hote56107.py``, ``deploy/demo/``).

Faux lanceur d'instances (aucun processus, aucun réseau) : chaque fausse instance garde ses propres saisies et renvoie ce qu'elle a reçu.
Horloge remplaçable : expirations simulées sans attente. Adresses de documentation seulement (198.51.100.0/24, 203.0.113.0/24,
192.0.2.0/24, 2001:db8::/32) ; données fictives (DURAND, ORLANDEC, ZORVAL, Valbrune, example.test).

Essais Linux réels (classe ``LinuxReel``) : vrai lanceur (un processus par instance, socket Unix), sauté hors Linux. Avec
``AXH_DEMO_RESEAU_COUPE=1`` (essai dans un conteneur ``--network none``), l'instance-sonde vérifie qu'aucune adresse extérieure n'est
joignable. Ce fichier sert aussi d'instance-sonde : ``python test_v56107_hote.py --sonde --dossier … --socket …``.
"""
import errno
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import socketserver
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

ORIGINE = 'https://demo.example.test'
IP_A, IP_B = '198.51.100.7', '203.0.113.9'
DEMO = RACINE / 'deploy' / 'demo'


def charger_fichier(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mutant(remplacements):
    """Copie en mémoire du module, garde-fou retiré (jamais de code altéré sur le disque)."""
    source = Path(dh.__file__).read_text(encoding='utf-8')
    for avant, apres in remplacements:
        assert avant in source, avant
        source = source.replace(avant, apres)
    module = types.ModuleType('agent.demo_hote56107_mutant')
    module.__package__ = 'agent'
    module.__file__ = dh.__file__
    exec(compile(source, dh.__file__, 'exec'), module.__dict__)
    return module


class FauxLanceur:
    """Fausse instance : /saisie (POST garde le corps, GET le rend), /entetes (en-têtes reçus), / (identité)."""

    def __init__(self):
        self.creees, self.arrets, self.transmis = [], [], []
        self.echouer = False
        self.pret_apres = 0

    def creer(self, ident, dossier):
        if self.echouer:
            raise OSError('lancement impossible')
        (Path(dossier) / 'donnees').mkdir()
        (Path(dossier) / 'donnees' / 'cabinet.json').write_text('{"cabinet": "Cabinet DURAND Avocats"}', encoding='utf-8')
        p = {'ident': ident, 'vivant': True, 'saisies': [], 'sondes': 0, 'dossier': Path(dossier)}
        self.creees.append(p)
        return p

    def vivant(self, p):
        return p['vivant']

    def lien_session(self, p):
        p['jetons'] = p.get('jetons', 0) + 1
        return '/agent-courriel/poste-session?jeton=fictif-%s-%d&suite=%%2Faujourdhui' % (p['ident'], p['jetons'])

    def quitter_demande(self, p):
        return p.get('quitter', False)

    def pret(self, p):
        p['sondes'] += 1
        return p['sondes'] > self.pret_apres

    def arreter(self, p):
        p['vivant'] = False
        self.arrets.append(p['ident'])

    def transmettre(self, p, methode, cible, entetes, corps, delai):
        self.transmis.append((p['ident'], methode, cible))
        if cible.startswith('/saisie') and methode == 'POST':
            p['saisies'].append(corps.decode())
            (p['dossier'] / 'donnees' / 'saisie.txt').write_bytes(corps)
            texte = 'enregistré'
        elif cible.startswith('/saisie'):
            texte = '|'.join(p['saisies'])
        elif cible.startswith('/entetes'):
            texte = json.dumps(entetes)
        else:
            texte = 'instance ' + p['ident']
        brut = texte.encode()
        return '200 OK', [('Content-Type', 'text/plain; charset=utf-8'), ('Content-Length', str(len(brut))), ('Server', 'waitress'),
                          ('Set-Cookie', '__Host-axhdemo=pirate; Path=/; Secure'), ('Set-Cookie', 'axh_session=s1; Path=/; HttpOnly'),
                          ('Connection', 'close')], [brut]


class Horloge:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    module = dh

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='h56107-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.horloge = Horloge()
        self.lanceur = FauxLanceur()
        self.app = self.fabriquer()

    def fabriquer(self, module=None, **reglages):
        module = module or self.module
        r = module.charger_reglages(None, environ={})
        r.update({'dossier_instances': str(self.tmp / 'instances'), 'dossier_journal': str(self.tmp / 'journal'), 'origine': ORIGINE})
        r.update(reglages)
        app = module.Repartiteur(r, self.lanceur, self.horloge)
        app.preparer()
        return app

    def appel(self, methode, chemin, cookies=None, corps=b'', ip=IP_A, remote='localhost', entetes=None, app=None, origine=ORIGINE):
        app = app or self.app
        env = {'REQUEST_METHOD': methode, 'PATH_INFO': chemin.split('?')[0], 'QUERY_STRING': chemin.partition('?')[2],
               'REMOTE_ADDR': remote, 'HTTP_X_FORWARDED_FOR': ip, 'wsgi.input': io.BytesIO(corps), 'CONTENT_LENGTH': str(len(corps)),
               'HTTP_HOST': 'demo.example.test'}
        if origine and methode == 'POST':
            env['HTTP_ORIGIN'] = origine
        if cookies:
            env['HTTP_COOKIE'] = '; '.join('%s=%s' % kv for kv in cookies.items())
        if corps:
            env['CONTENT_TYPE'] = 'application/x-www-form-urlencoded'
        env.update(entetes or {})
        res = {}

        def start(statut, liste):
            res['statut'], res['entetes'] = statut, liste
        corps_rep = b''.join(app(env, start)).decode('utf-8')
        return res['statut'], res['entetes'], corps_rep

    @staticmethod
    def cookie(entetes, nom):
        for k, v in entetes:
            if k.lower() == 'set-cookie' and v.split('=', 1)[0] == nom:
                return v
        return None

    def pre(self, app=None):
        _, entetes, _ = self.appel('GET', '/', app=app)
        return self.cookie(entetes, dh.NOM_PRE).split(';')[0].split('=', 1)[1]

    def commencer(self, ip=IP_A, app=None, origine=ORIGINE, ouvrir=True):
        pre = self.pre(app)
        statut, entetes, corps = self.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre}, urlencode({'pre': pre}).encode(), ip=ip,
                                            app=app, origine=origine)
        brut = self.cookie(entetes, dh.NOM_COOKIE)
        jeton = brut.split(';')[0].split('=', 1)[1] if brut else None
        if jeton and ouvrir and self.lanceur.pret_apres == 0:
            ouverture = self.appel('GET', '/', self.session(jeton), app=app)     # première page : session de l'instance par jeton
            assert ouverture[0] == '303 See Other' and dict(ouverture[1])['Location'].startswith('/agent-courriel/poste-session?'), ouverture
        return statut, jeton, brut, corps

    def session(self, jeton):
        return {dh.NOM_COOKIE: jeton}


class Creation(Base):
    def test_H1_page_accueil_mention_rgpd_et_jeton_de_formulaire(self):
        statut, entetes, page = self.appel('GET', '/')
        self.assertEqual(statut, '200 OK')
        self.assertIn('Commencer la démo', page)
        for texte in ('Données fictives uniquement', 'aucun compte à créer', 'effacé à la fin de la session', '2 h d’inactivité',
                      '6 h au total', 'sans être enregistrée', 'aucune donnée réelle'):
            self.assertIn(texte, page)
        pre = self.cookie(entetes, dh.NOM_PRE)
        self.assertRegex(pre, r'^__Host-axhdemo-pre=[A-Za-z0-9_-]{43}; Path=/; Max-Age=3600; Secure; HttpOnly; SameSite=Lax$')
        self.assertEqual(self.lanceur.creees, [])                                    # afficher l'accueil ne crée rien

    def test_H1_creation_pose_un_cookie_aleatoire_httponly_secure_lax(self):
        statut, jeton, brut, _ = self.commencer()
        self.assertEqual(statut, '303 See Other')
        self.assertRegex(brut, r'^__Host-axhdemo=[A-Za-z0-9_-]{43}; Path=/; Max-Age=21600; Secure; HttpOnly; SameSite=Lax$')
        self.assertEqual(len(self.lanceur.creees), 1)
        inst = list(self.app.instances.values())[0]
        self.assertNotIn(jeton, inst.ident + inst.dossier.name)                     # le dossier ne dérive pas du jeton
        self.assertNotIn(jeton, json.dumps(list(self.app.instances)))               # seule l'empreinte est gardée
        self.assertTrue((self.tmp / 'instances' / inst.ident / 'donnees' / 'cabinet.json').exists())
        statut, _, corps = self.appel('GET', '/', self.session(jeton))
        self.assertEqual((statut, corps), ('200 OK', 'instance ' + inst.ident))
        self.assertNotEqual(self.commencer()[1], jeton)                             # jetons aléatoires

    def test_H1_un_visiteur_qui_a_deja_une_instance_la_garde(self):
        _, jeton, _, _ = self.commencer()
        pre = self.pre()
        statut, entetes, _ = self.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre, dh.NOM_COOKIE: jeton}, urlencode({'pre': pre}).encode())
        self.assertEqual((statut, self.cookie(entetes, dh.NOM_COOKIE), len(self.lanceur.creees)), ('303 See Other', None, 1))

    def test_H1_demarrage_refuse_sans_origine_ou_jeton_de_formulaire(self):
        for origine in ('', 'https://autre.example.test', 'http://demo.example.test'):
            self.assertEqual(self.commencer(origine=origine)[0], '403 Forbidden', origine)
        pre = self.pre()
        for champs in ({}, {'pre': 'B' * 43}):
            statut, _, page = self.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre}, urlencode(champs).encode())
            self.assertEqual(statut, '403 Forbidden')
            self.assertIn('Le formulaire a expiré', page)
        self.assertEqual(self.lanceur.creees, [])

    def test_H1_mutation_sans_controle_d_origine_un_site_tiers_lancerait_des_instances(self):
        m = mutant([("        if environ.get('HTTP_ORIGIN', '') != self.r['origine']:\n", "        if False:\n")])
        app = self.fabriquer(m)
        self.assertEqual(self.commencer(app=app, origine='https://autre.example.test')[0], '303 See Other')

    def test_H1_page_d_attente_tant_que_l_instance_demarre(self):
        self.lanceur.pret_apres = 2
        _, jeton, _, _ = self.commencer()
        statut, entetes, page = self.appel('GET', '/', self.session(jeton))
        self.assertIn('Préparation de votre démonstration', page)
        self.assertIn('http-equiv="refresh"', page)
        self.assertEqual(self.appel('POST', '/x', self.session(jeton), b'a=1')[0], '503 Service Unavailable')
        statut, entetes, _ = self.appel('GET', '/', self.session(jeton))
        self.assertEqual((statut, dict(entetes)['Location']),
                         ('303 See Other', '/agent-courriel/poste-session?jeton=fictif-%s-1&suite=%%2Faujourdhui' % self.lanceur.creees[0]['ident']))
        self.assertTrue(self.appel('GET', '/', self.session(jeton))[2].startswith('instance '))   # session ouverte une seule fois
        self.assertEqual(len(self.lanceur.transmis), 1)                             # rien n'est transmis avant l'instance prête

    def test_H1_echec_de_lancement_rien_ne_reste(self):
        self.lanceur.echouer = True
        statut, jeton, _, page = self.commencer()
        self.assertEqual((statut, jeton), ('503 Service Unavailable', None))
        self.assertIn('n’a pas pu démarrer', page)
        self.assertEqual((self.app.instances, list((self.tmp / 'instances').iterdir())), ({}, []))


class Isolement(Base):
    def deux_visiteurs(self, app=None):
        _, a, _, _ = self.commencer(ip=IP_A, app=app, ouvrir=app is None)
        _, b, _, _ = self.commencer(ip=IP_B, app=app, ouvrir=app is None)
        self.appel('POST', '/saisie', self.session(a), 'note=ORLANDEC confidentiel'.encode(), app=app)
        return a, b

    def test_H1_un_cookie_ne_donne_acces_qu_a_son_instance(self):
        a, b = self.deux_visiteurs()
        self.assertEqual(self.appel('GET', '/saisie', self.session(a))[2], 'note=ORLANDEC confidentiel')
        self.assertEqual(self.appel('GET', '/saisie', self.session(b))[2], '')                  # B ne voit rien de A
        inst_a = [p for p in self.lanceur.creees if p['saisies']][0]
        self.assertFalse(any((p['dossier'] / 'donnees' / 'saisie.txt').exists() for p in self.lanceur.creees if p is not inst_a))
        avant = len(self.lanceur.transmis)
        for faux in ('A' * 43, a[:-1] + ('x' if a[-1] != 'x' else 'y'), a + 'x', a.lower() if a != a.lower() else a.upper(), ''):
            statut, entetes, page = self.appel('GET', '/saisie', self.session(faux))
            self.assertNotIn('ORLANDEC', page)
        self.assertEqual(len(self.lanceur.transmis), avant)                                   # rien transmis pour un faux cookie

    def test_H1_mutation_controle_du_cookie(self):
        # mutation (copie en mémoire) : sans le contrôle du cookie, le visiteur B lirait les saisies de A
        m = mutant([("            return self.instances.get(empreinte(jeton))",
                     "            return next(iter(self.instances.values()), None)")])
        app = self.fabriquer(m)
        a, b = self.deux_visiteurs(app)
        self.appel('GET', '/', self.session(b), app=app)                         # ouverture de session
        self.assertEqual(self.appel('GET', '/saisie', self.session(b), app=app)[2], 'note=ORLANDEC confidentiel')

    def test_H1_le_cookie_du_repartiteur_n_est_jamais_transmis_ni_remplace(self):
        _, a, _, _ = self.commencer()
        pre = self.pre()
        statut, entetes, corps = self.appel('GET', '/entetes', {dh.NOM_COOKIE: a, dh.NOM_PRE: pre, 'axh_session': 's1'},
                                            entetes={'HTTP_AUTHORIZATION': 'Bearer x', 'HTTP_X_AXIORHUB_USER': 'admin@example.test',
                                                     'HTTP_X_REAL_IP': IP_B, 'HTTP_ACCEPT_LANGUAGE': 'fr'})
        recus = dict(json.loads(corps))
        self.assertEqual(recus['cookie'], 'axh_session=s1')                     # cookie de l'instance seulement
        for absent in ('authorization', 'x-axiorhub-user', 'x-forwarded-for', 'x-real-ip'):
            self.assertNotIn(absent, recus)                                     # ni secret, ni identité forgée, ni adresse du visiteur
        self.assertEqual((recus['host'], recus['x-forwarded-proto'], recus['accept-language']), ('demo.example.test', 'https', 'fr'))
        self.assertIsNone(self.cookie(entetes, dh.NOM_COOKIE))                  # l'instance ne remplace pas le cookie qui la désigne
        self.assertTrue(self.cookie(entetes, 'axh_session'))
        self.assertNotIn('server', {k.lower() for k, _ in entetes})
        self.assertNotIn('connection', {k.lower() for k, _ in entetes})

    def test_H1_corps_borne(self):
        _, a, _, _ = self.commencer()
        app = self.fabriquer(corps_max_mo=1)
        _, b, _, _ = self.commencer(app=app, ip=IP_B)
        self.assertEqual(self.appel('POST', '/saisie', self.session(b), b'x' * ((1 << 20) + 1), app=app)[0], '413 Payload Too Large')
        self.assertEqual(self.appel('POST', '/saisie', self.session(b), b'x', app=app,
                                    entetes={'HTTP_TRANSFER_ENCODING': 'chunked'})[0], '411 Length Required')


class Expiration(Base):
    def test_H2_reglages_par_defaut_conformes_a_la_demande(self):
        r = dh.charger_reglages(None, environ={})
        self.assertEqual((r['inactivite_minutes'], r['duree_max_minutes'], r['instances_max']), (120, 360, 20))
        for cle in ('creations_par_ip_heure', 'memoire_mo', 'cpu_pourcent', 'taille_max_mo', 'processus_max', 'memoire_totale_mo'):
            self.assertGreater(r[cle], 0)

    def test_H2_reglages_refuses_plutot_que_corriges(self):
        fichier = self.tmp / 'demo.json'
        for contenu, motif in (({'instances_max': 0}, 'instances_max'), ({'instance_max': 3}, 'réglage inconnu'),
                               ({'origine': 'http://demo.example.test'}, 'origine'), ({'origine': 'https://demo.example.test/x'}, 'origine'),
                               ({'ecoute': '0.0.0.0:8080'}, 'ecoute'), ({'inactivite_minutes': 400}, 'duree_max'),
                               ({'memoire_mo': 4096, 'memoire_totale_mo': 1024}, 'memoire_totale'), ({'chemins_refuses': '/x'}, 'liste')):
            fichier.write_text(json.dumps(contenu), encoding='utf-8')
            with self.assertRaises(dh.Refus) as ctx:
                dh.charger_reglages(fichier, environ={})
            self.assertIn(motif, str(ctx.exception))
        fichier.write_text(json.dumps({'instances_max': 3, 'origine': 'https://Demo.Example.Test'}), encoding='utf-8')
        r = dh.charger_reglages(fichier, environ={'AXIORHUB_DEMO_CREATIONS_PAR_IP_HEURE': '2'})
        self.assertEqual((r['instances_max'], r['creations_par_ip_heure'], r['origine']), (3, 2, 'https://demo.example.test'))
        self.assertEqual(r['fils'], 32)

    def test_H2_expiration_apres_inactivite(self):
        _, a, _, _ = self.commencer()
        inst = list(self.app.instances.values())[0]
        self.horloge.t += 119 * 60
        self.assertTrue(self.appel('GET', '/', self.session(a))[2].startswith('instance'))   # activité : délai relancé
        self.horloge.t += 120 * 60 + 1
        statut, entetes, page = self.appel('GET', '/', self.session(a))
        self.assertIn('a expiré', page)
        self.assertIn('Max-Age=0', self.cookie(entetes, dh.NOM_COOKIE))
        self.assertFalse(inst.dossier.exists())
        self.assertEqual(self.lanceur.arrets, [inst.ident])
        self.assertIn('fin-inactivite', self.journal())

    def test_H2_expiration_apres_six_heures_meme_active(self):
        _, a, _, _ = self.commencer()
        for _ in range(6):
            self.horloge.t += 59 * 60
            self.assertTrue(self.appel('GET', '/', self.session(a))[2].startswith('instance'))
        self.horloge.t += 7 * 60
        self.assertIn('a expiré', self.appel('GET', '/', self.session(a))[2])
        self.assertIn('fin-duree', self.journal())

    def test_H2_nettoyage_automatique_sans_visite(self):
        _, a, _, _ = self.commencer(ip=IP_A)
        _, b, _, _ = self.commencer(ip=IP_B)
        inst_a, inst_b = [i for i in self.app.instances.values()]
        orphelin = self.tmp / 'instances' / 'reste-precedent'
        orphelin.mkdir()
        (orphelin / 'saisie.txt').write_text('ZORVAL', encoding='utf-8')
        self.horloge.t += 60 * 60
        self.appel('GET', '/', self.session(b))                                  # B reste actif
        self.horloge.t += 61 * 60
        self.app.nettoyer()
        self.assertEqual(list(self.app.instances.values()), [inst_b])
        self.assertFalse(inst_a.dossier.exists())
        self.assertFalse(orphelin.exists())                                       # fichiers d'un dossier inconnu effacés
        self.assertTrue(inst_b.dossier.exists())
        etat = json.loads((self.tmp / 'etat.json').read_text(encoding='utf-8'))
        self.assertEqual((etat['instances'], etat['instances_max']), (1, 20))
        self.assertNotIn(b, json.dumps(etat))

    def test_H2_processus_arrete_ou_demarrage_trop_long(self):
        _, a, _, _ = self.commencer(ip=IP_A)
        self.lanceur.creees[0]['vivant'] = False
        self.lanceur.pret_apres = 10 ** 6
        _, b, _, _ = self.commencer(ip=IP_B)
        self.app.nettoyer()
        self.assertEqual(len(self.app.instances), 1)
        self.horloge.t += 91
        self.app.nettoyer()
        self.assertEqual(self.app.instances, {})
        self.assertIn('arret-processus', self.journal())
        self.assertIn('echec-demarrage', self.journal())

    def test_H2_dossier_trop_gros_efface(self):
        app = self.fabriquer(taille_max_mo=10)
        _, a, _, _ = self.commencer(app=app)
        inst = list(app.instances.values())[0]
        (inst.dossier / 'gros.bin').write_bytes(b'0' * ((10 << 20) + 1))
        app.nettoyer()
        self.assertEqual((app.instances, inst.dossier.exists()), ({}, False))
        self.assertIn('depassement-taille', self.journal())

    def test_H2_redemarrage_du_service_efface_les_restes(self):
        _, a, _, _ = self.commencer()
        inst = list(self.app.instances.values())[0]
        self.fabriquer()                                                            # nouveau processus : préparation
        self.assertFalse(inst.dossier.exists())

    def journal(self):
        return ''.join(f.read_text(encoding='utf-8') for f in sorted((self.tmp / 'journal').glob('*.log')))


class Plafonds(Base):
    def test_H2_plafond_d_instances_simultanees(self):
        app = self.fabriquer(instances_max=2)
        self.assertEqual(self.commencer(ip=IP_A, app=app)[0], '303 See Other')
        _, b, _, _ = self.commencer(ip=IP_B, app=app)
        statut, jeton, _, page = self.commencer(ip='192.0.2.33', app=app)
        self.assertEqual((statut, jeton), ('503 Service Unavailable', None))
        self.assertIn('Démo complète, réessayez dans quelques minutes', page)
        self.assertEqual(len(app.instances), 2)
        self.horloge.t += 121 * 60
        app.nettoyer()
        self.assertEqual(self.commencer(ip='192.0.2.33', app=app)[0], '303 See Other')

    def test_H2_plafond_tient_sous_la_concurrence(self):
        app = self.fabriquer(instances_max=3, creations_par_ip_heure=100)
        codes, verrou = [], threading.Lock()

        def un():
            c = self.commencer(app=app)[0]
            with verrou:
                codes.append(c)
        fils = [threading.Thread(target=un) for _ in range(12)]
        [f.start() for f in fils]
        [f.join() for f in fils]
        self.assertEqual((codes.count('303 See Other'), len(app.instances)), (3, 3))

    def test_H2_limite_de_creations_par_adresse_et_par_heure(self):
        app = self.fabriquer(creations_par_ip_heure=2)
        self.assertEqual([self.commencer(ip=IP_A, app=app)[0] for _ in range(2)], ['303 See Other'] * 2)
        statut, _, _, page = self.commencer(ip=IP_A, app=app)
        self.assertEqual(statut, '429 Too Many Requests')
        self.assertIn('Réessayez dans 60 minutes', page)
        self.assertEqual(self.commencer(ip=IP_B, app=app)[0], '303 See Other')          # une autre adresse n'est pas touchée
        self.horloge.t += 3601
        self.assertEqual(self.commencer(ip=IP_A, app=app)[0], '303 See Other')
        self.assertNotIn(IP_A, json.dumps({k: list(v) for k, v in app.creations.items()}))   # empreintes salées seulement

    def test_H2_x_forwarded_for_ignore_hors_mandataire_de_confiance(self):
        app = self.fabriquer(creations_par_ip_heure=1)
        pre = self.pre(app)
        corps = urlencode({'pre': pre}).encode()
        codes = [self.appel('POST', '/_demo/commencer', {dh.NOM_PRE: pre}, corps, app=app, remote='203.0.113.50',
                            ip='192.0.2.%d' % i)[0] for i in range(3)]
        self.assertEqual(codes, ['303 See Other', '429 Too Many Requests', '429 Too Many Requests'])
        self.assertEqual(app.adresse({'REMOTE_ADDR': 'localhost', 'HTTP_X_FORWARDED_FOR': '192.0.2.1, 198.51.100.4'}), '198.51.100.4')


class EntetesEtAdministration(Base):
    ATTENDUS = {'strict-transport-security', 'x-content-type-options', 'x-frame-options', 'referrer-policy', 'x-robots-tag',
                'cross-origin-opener-policy'}

    def test_H3_entetes_de_securite_sur_toutes_les_reponses(self):
        _, a, _, _ = self.commencer()
        for cookies, chemin in ((None, '/'), (None, '/healthz'), (None, '/administration'), (self.session(a), '/'),
                                (self.session(a), '/_demo/session')):
            _, entetes, _ = self.appel('GET', chemin, cookies)
            noms = {k.lower() for k, _ in entetes}
            self.assertTrue(self.ATTENDUS <= noms, (chemin, self.ATTENDUS - noms))
            self.assertEqual(dict((k.lower(), v) for k, v in entetes)['x-content-type-options'], 'nosniff')
        _, entetes, _ = self.appel('GET', '/')
        d = dict(entetes)
        self.assertEqual(d['Cache-Control'], 'private, no-store')
        self.assertIn("default-src 'none'", d['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", d['Content-Security-Policy'])
        self.assertNotIn('unsafe-inline', d['Content-Security-Policy'])

    def test_H3_securiser_garde_la_csp_de_l_instance_mais_impose_hsts(self):
        sortie = dict(dh.securiser([('Content-Security-Policy', "default-src 'self'"), ('Strict-Transport-Security', 'max-age=0')]))
        self.assertEqual((sortie['Content-Security-Policy'], sortie['Strict-Transport-Security']), ("default-src 'self'", 'max-age=31536000'))
        self.assertNotIn('X-Frame-Options', dict(dh.securiser([('Content-Security-Policy', "frame-ancestors 'self'")], propre=False)))
        _, a, _, _ = self.commencer()
        _, entetes, _ = self.appel('GET', '/', self.session(a))
        self.assertEqual(dict(entetes)['X-Frame-Options'], 'SAMEORIGIN')        # aperçus de l'application dans un cadre de même origine

    def test_H3_le_chemin_transmis_est_le_chemin_controle(self):
        _, a, _, _ = self.commencer()
        self.appel('GET', '/aujourdhui//vue?jour=1', self.session(a))
        self.assertEqual(self.lanceur.transmis[-1][2], '/aujourdhui/vue?jour=1')

    def test_H3_chemins_d_administration_refuses_avec_ou_sans_session(self):
        _, a, _, _ = self.commencer()
        _, _, neutre = self.appel('GET', '/_demo/inconnu')
        chemins = ('/administration', '/administration/fonctions-peu-utilisees', '/ADMINISTRATION', '//administration', '/installation',
                   '/comptes', '/mcp', '/api/v1/cabinet/audit', '/integration/v1/pair', '/liaison-ia/v1/reclamer', '/oauth/token',
                   '/parametres/journal-audit', '/navigateur', '/server-status', '/.env', '/.git/config', '/api440/m5661/appliquer',
                   '/api440/m56101/securite/etat', '/administration/../aujourdhui', '/aujourdhui/./x', '/x;admin', '/admin\\x')
        avant = len(self.lanceur.transmis)
        for chemin in chemins:
            for cookies in (None, self.session(a)):
                for methode in ('GET', 'POST'):
                    statut, _, page = self.appel(methode, chemin, cookies, b'a=1' if methode == 'POST' else b'')
                    self.assertEqual((statut, page), ('404 Not Found', neutre), (methode, chemin, cookies))
        self.assertEqual(len(self.lanceur.transmis), avant)                       # rien n'atteint l'instance
        self.assertEqual(self.appel('GET', '/parametres/assistant', self.session(a))[0], '200 OK')   # pages ordinaires transmises

    def test_H3_chemins_d_administration_refuses_aussi_sous_le_prefixe_de_l_interface(self):
        _, a, _, _ = self.commencer()
        avant = len(self.lanceur.transmis)
        for chemin in ('/agent-courriel/administration', '/AGENT-COURRIEL/comptes', '/agent-courriel/api440/m5661/appliquer',
                       '/agent-courriel/api440/m56107/demo/lancer', '/agent-courriel//installation', '/agent-courriel/mcp/x'):
            self.assertEqual(self.appel('GET', chemin, self.session(a))[0], '404 Not Found', chemin)
        self.assertEqual(len(self.lanceur.transmis), avant)
        self.assertEqual(self.appel('GET', '/agent-courriel/aujourdhui', self.session(a))[0], '200 OK')
        self.assertEqual(self.appel('GET', '/agent-courriel/poste-session?jeton=x', self.session(a))[0], '200 OK')   # ouverture de session

    def test_H3_chemins_refuses_ajoutes_par_le_cabinet(self):
        app = self.fabriquer(chemins_refuses=['/studio'])
        _, a, _, _ = self.commencer(app=app)
        self.assertEqual(self.appel('GET', '/studio/x', self.session(a), app=app)[0], '404 Not Found')

    def test_H3_mutation_sans_refus_des_chemins_d_administration(self):
        m = mutant([("        if chemin is None or chemin_refuse(chemin, self.r['chemins_refuses'], self.r['prefixe']):",
                     "        if chemin is None:")])
        app = self.fabriquer(m)
        _, a, _, _ = self.commencer(app=app)
        self.assertEqual(self.appel('GET', '/administration', self.session(a), app=app)[0], '200 OK')   # le test ci-dessus le détecte

    def test_H3_sonde_de_sante_minimale_et_robots(self):
        self.commencer()
        statut, _, corps = self.appel('GET', '/healthz')
        self.assertEqual((statut, corps), ('200 OK', 'ok\n'))                     # aucun compte d'instances exposé
        self.assertEqual(self.appel('GET', '/robots.txt')[2], 'User-agent: *\nDisallow: /\n')

    def test_H3_sans_session_un_chemin_de_l_application_ramene_a_l_accueil(self):
        statut, entetes, _ = self.appel('GET', '/aujourdhui')
        self.assertEqual((statut, dict(entetes)['Location']), ('303 See Other', '/'))
        self.assertEqual(self.appel('POST', '/api440/m530/ask', None, b'a=1')[0], '404 Not Found')
        self.assertEqual(self.lanceur.transmis, [])


class FinEtJournal(Base):
    def csrf(self, jeton):
        import re
        page = self.appel('GET', '/_demo/session', self.session(jeton))[2]
        return re.search(r'name="csrf" value="([^"]+)"', page).group(1)

    def test_H4_terminer_efface_tout(self):
        _, a, _, _ = self.commencer()
        self.appel('POST', '/saisie', self.session(a), 'note=KERVILAC'.encode())
        inst = list(self.app.instances.values())[0]
        self.assertEqual(self.appel('POST', '/_demo/terminer', self.session(a), b'csrf=faux')[0], '403 Forbidden')
        self.assertEqual(self.appel('POST', '/_demo/terminer', self.session(a), urlencode({'csrf': self.csrf(a)}).encode(),
                                    origine='https://autre.example.test')[0], '403 Forbidden')
        self.assertTrue(inst.dossier.exists())
        statut, entetes, page = self.appel('POST', '/_demo/terminer', self.session(a), urlencode({'csrf': self.csrf(a)}).encode())
        self.assertEqual(statut, '200 OK')
        self.assertIn('tout ce que vous y aviez saisi a été effacé', page)
        self.assertIn('Max-Age=0', self.cookie(entetes, dh.NOM_COOKIE))
        self.assertFalse(inst.dossier.exists())
        self.assertIn('terminée', self.appel('GET', '/', self.session(a))[2])

    def test_H4_recommencer_donne_une_instance_neuve(self):
        _, a, _, _ = self.commencer()
        self.appel('POST', '/saisie', self.session(a), 'note=ZORVAL'.encode())
        statut, entetes, _ = self.appel('POST', '/_demo/recommencer', self.session(a), urlencode({'csrf': self.csrf(a)}).encode())
        b = self.cookie(entetes, dh.NOM_COOKIE).split(';')[0].split('=', 1)[1]
        self.assertEqual(statut, '303 See Other')
        self.assertNotEqual(a, b)
        self.assertEqual(self.appel('GET', '/saisie', self.session(b))[2], '')
        self.assertEqual(len(self.app.instances), 1)

    def test_H4_journal_minimal_et_conservation_courte(self):
        _, a, _, _ = self.commencer(ip=IP_A)
        self.appel('POST', '/saisie?client=DURAND', self.session(a), 'note=ORLANDEC confidentiel'.encode(),
                   entetes={'HTTP_USER_AGENT': 'Navigateur-Valbrune/1.0'})
        self.appel('GET', '/administration', self.session(a))
        self.horloge.t += 8 * 86400
        self.app.journal.ecrire('service')
        self.app.nettoyer()
        fichiers = sorted((self.tmp / 'journal').glob('*.log'))
        self.assertEqual(len(fichiers), 1)                                         # le fichier d'il y a 8 jours est effacé
        vieux = self.tmp / 'journal' / 'demo-2001-01-01.log'
        vieux.write_text('x', encoding='utf-8')
        self.app.journal.purger()
        self.assertFalse(vieux.exists())
        self.horloge.t -= 8 * 86400
        _, b, _, _ = self.commencer(ip=IP_B)
        tout = ''.join(f.read_text(encoding='utf-8') for f in (self.tmp / 'journal').glob('*.log'))
        for interdit in (IP_A, IP_B, 'ORLANDEC', 'DURAND', 'saisie', 'administration', 'Valbrune', a, b):
            self.assertNotIn(interdit, tout)
        for ligne in tout.splitlines():
            self.assertRegex(ligne, r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ [a-z-]+ ([0-9a-f]{8}|-)$')
        with self.assertRaises(ValueError):
            self.app.journal.ecrire('adresse 198.51.100.7')                        # aucune nature libre

    def test_H4_quitter_dans_le_bandeau_arrete_et_efface(self):
        _, a, _, _ = self.commencer(ip=IP_A)
        _, b, _, _ = self.commencer(ip=IP_B)
        inst_a, inst_b = list(self.app.instances.values())
        self.lanceur.creees[0]['quitter'] = True                                  # demande écrite par le bandeau du mode Démo (lot M)
        statut, entetes, page = self.appel('GET', '/agent-courriel/aujourdhui', self.session(a))
        self.assertIn('Démonstration terminée', page)
        self.assertIn('Max-Age=0', self.cookie(entetes, dh.NOM_COOKIE))
        self.assertFalse(inst_a.dossier.exists())
        self.lanceur.creees[1]['quitter'] = True
        self.app.nettoyer()                                                         # sans nouvelle visite : nettoyage
        self.assertEqual((self.app.instances, inst_b.dossier.exists()), ({}, False))

    def test_H4_revenir_a_la_demonstration_rouvre_une_session(self):
        _, a, _, _ = self.commencer()
        self.assertEqual(self.appel('POST', '/_demo/ouvrir', self.session(a), b'csrf=faux')[0], '403 Forbidden')
        statut, entetes, _ = self.appel('POST', '/_demo/ouvrir', self.session(a), urlencode({'csrf': self.csrf(a)}).encode())
        self.assertEqual(statut, '303 See Other')
        self.assertTrue(dict(entetes)['Location'].endswith('-2&suite=%2Faujourdhui'))       # nouveau jeton (le deuxième)
        self.assertIn('Revenir à la démonstration', self.appel('GET', '/_demo/session', self.session(a))[2])

    def test_H4_lien_de_session_jamais_hors_du_domaine(self):
        self.lanceur.lien_session = lambda p: '//autre.example.test/x'
        _, a, _, _ = self.commencer(ouvrir=False)
        self.assertEqual(self.appel('GET', '/', self.session(a))[0], '404 Not Found')

    def test_H4_session_affiche_le_temps_restant(self):
        _, a, _, _ = self.commencer()
        page = self.appel('GET', '/_demo/session', self.session(a))[2]
        self.assertIn('effacée dans 2 h au plus tard', page)
        self.assertIn('Terminer et tout effacer', page)
        self.assertEqual(self.appel('GET', '/_demo/session')[0], '303 See Other')


class Livrables(unittest.TestCase):
    def lire(self, nom):
        return (DEMO / nom).read_text(encoding='utf-8')

    def test_H5_lisezmoi_en_francais_complet(self):
        texte = self.lire('LISEZMOI.md')
        for section in ('Prérequis', 'Installation', 'DNS', 'Certificat', 'Apache', 'Réglages', 'Mise à jour', 'Arrêt', 'Surveillance',
                        'Test fumée', 'Docker', 'demo.example.test', 'Vie privée'):
            self.assertIn(section, texte)

    def test_H5_aucune_adresse_ni_nom_du_serveur_reel(self):
        import re
        for f in sorted(DEMO.iterdir()):
            if f.is_file():
                texte = f.read_text(encoding='utf-8')
                for ip in re.findall(r'\b\d{1,3}(?:\.\d{1,3}){3}\b', texte):
                    self.assertTrue(ip.startswith(('127.', '0.0.0.0', '192.0.2.', '198.51.100.', '203.0.113.')), (f.name, ip))
                self.assertNotIn('axiorhub.com', texte.replace('demo.axiorhub.com', ''), f.name)

    def test_H5_service_systemd_durci_et_sans_reseau(self):
        texte = self.lire('axiorhub-demo.service')
        for ligne in ('PrivateNetwork=yes', 'RestrictAddressFamilies=AF_UNIX', 'IPAddressDeny=any', 'NoNewPrivileges=yes',
                      'CapabilityBoundingSet=\n', 'ProtectSystem=strict', 'ProtectHome=yes', 'User=axiorhub-demo', 'Delegate=cpu memory pids',
                      'MemoryDenyWriteExecute=yes', 'StateDirectory=axiorhub-demo', 'RuntimeDirectory=axiorhub-demo', 'UMask=0077'):
            self.assertIn(ligne, texte)
        self.assertNotIn('ProtectControlGroups=yes', texte)
        self.assertNotRegex(texte, r'~@[^\n]*@resources')                      # setrlimit et nice restent permis à l'instance

    def test_H5_compose_sans_reseau_et_durci(self):
        texte = self.lire('compose.yml')
        for ligne in ('network_mode: none', 'read_only: true', 'cap_drop: [ALL]', 'no-new-privileges:true', 'user: "10001:10001"',
                      '/var/lib/axiorhub-demo:size=', 'mem_limit:', 'cpus:', 'pids_limit:'):
            self.assertIn(ligne, texte)
        self.assertNotIn('ports:', texte)
        dockerfile = self.lire('Dockerfile')
        self.assertIn('USER 10001:10001', dockerfile)
        self.assertIn('rm -rf /app/.git /app/tests', dockerfile)

    def test_H5_reglages_d_exemple_valides(self):
        r = dh.charger_reglages(DEMO / 'demo.example.json', environ={})
        self.assertEqual((r['origine'], r['instances_max'], r['inactivite_minutes'], r['duree_max_minutes']),
                         ('https://demo.example.test', 20, 120, 360))

    def test_H5_mandataires_d_exemple(self):
        apache, nginx = self.lire('apache-demo.example.conf'), self.lire('nginx-demo.example.conf')
        self.assertIn('ProxyPass / unix:/run/axiorhub-demo/demo.sock|http://demo.example.test/', apache)
        self.assertIn('RequestHeader unset X-Forwarded-For early', apache)
        self.assertNotIn('%h', apache)
        self.assertNotIn('%a', apache)                                            # journal Apache sans adresse
        self.assertIn('server unix:/run/axiorhub-demo/demo.sock', nginx)
        self.assertNotIn('$remote_addr ', nginx.split('log_format', 1)[1].split('\n', 1)[0])

    def test_H5_installation_ne_touche_ni_dns_ni_certificat(self):
        texte = self.lire('installer-demo.sh')
        self.assertIn('set -euo pipefail', texte)
        for interdit in ('certbot ', 'a2ensite', 'curl ', 'wget '):
            self.assertNotIn(interdit, texte.split('cat <<FIN')[0])

    def test_H5_test_fumee_contre_un_repartiteur_local(self):
        fumee = charger_fichier('test_fumee_demo56107', DEMO / 'test-fumee.py')
        tmp = Path(tempfile.mkdtemp(prefix='h56107-fumee-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        r = dh.charger_reglages(None, environ={})
        r.update({'dossier_instances': str(tmp / 'i'), 'dossier_journal': str(tmp / 'j'), 'origine': ORIGINE})
        lanceur = FauxLanceur()
        app = dh.Repartiteur(r, lanceur)
        app.preparer()

        class Silencieux(WSGIRequestHandler):
            def log_message(self, *a):
                pass
        serveur = make_server('127.0.0.1', 0, app, handler_class=Silencieux)
        threading.Thread(target=serveur.serve_forever, daemon=True).start()
        self.addCleanup(serveur.server_close)
        self.addCleanup(serveur.shutdown)
        lignes = []
        echecs = fumee.parcours('http://127.0.0.1:%d' % serveur.server_port, sortie=lignes.append, attente=5, origine=ORIGINE)
        self.assertEqual(echecs, [], '\n'.join(lignes))
        self.assertGreaterEqual(len(lignes), 14)
        self.assertEqual((len(lanceur.creees), app.instances), (1, {}))           # une seule instance, effacée à la fin


class Instance(unittest.TestCase):
    """deploy/demo/instance.py : contrôle du mode Démo (API du lot M) et garde réseau du processus."""

    def setUp(self):
        self.instance = charger_fichier('instance_demo56107', DEMO / 'instance.py')
        self.tmp = Path(tempfile.mkdtemp(prefix='h56107-inst-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def faux_demo(self, actif, ailleurs=None):
        """Faux de l'API publique du lot M : ``creer(dossier_cible, graine=None, origine=…, prefixe=…) -> chemin_config``,
        ``actif(desk_ou_config)`` et ``environnement(chemin)``."""
        appels = []
        faux = types.ModuleType('agent.demo56107')

        def creer(dossier, graine=None, origine='', prefixe=''):
            appels.append(('creer', str(dossier), origine, prefixe))
            Path(dossier).mkdir(parents=True, exist_ok=True)
            return str(ailleurs or Path(dossier) / 'config.json')
        faux.creer = creer
        faux.actif = lambda config: appels.append(('actif', config)) or actif
        faux.environnement = lambda chemin: {'AXIORHUB_DATA_DIR': str(Path(chemin).parent), 'AXIORHUB_DEMO': '1'}
        return faux, appels

    def lancer(self, faux):
        import agent
        vus = {}

        class FausseApp:
            def __init__(self, config, auth):
                vus['app'] = (config, auth)
        with mock.patch.dict(sys.modules, {'agent.demo56107': faux}), mock.patch.object(agent, 'demo56107', faux, create=True), \
                mock.patch('agent.common.load_config', lambda chemin: {'chemin': chemin}), mock.patch('agent.web.App', FausseApp), \
                mock.patch('agent.budget_ledger5689.preparer', lambda *a: vus.setdefault('budget', a)), \
                mock.patch('agent.passerelle5689.installer', lambda *a: vus.setdefault('passerelle', a)), \
                mock.patch.dict(os.environ, {}):
            config = self.instance.creer(self.tmp, ORIGINE, '/agent-courriel')
            vus['env'] = os.environ.get('AXIORHUB_DATA_DIR')
            self.instance.application(config)
        return vus

    def test_H1_instance_refusee_si_le_mode_demo_n_est_pas_actif(self):
        faux, appels = self.faux_demo(False)
        with self.assertRaises(SystemExit) as ctx:
            self.lancer(faux)
        self.assertEqual(ctx.exception.code, 3)
        self.assertEqual([a[0] for a in appels], ['creer', 'actif'])

    def test_H1_instance_refusee_si_la_configuration_sort_de_son_dossier(self):
        faux, appels = self.faux_demo(True, ailleurs=self.tmp.parent / 'config-du-cabinet.json')
        with self.assertRaises(SystemExit) as ctx:
            self.lancer(faux)
        self.assertEqual((ctx.exception.code, [a[0] for a in appels]), (3, ['creer']))

    def test_H1_instance_construite_sur_son_seul_dossier(self):
        faux, appels = self.faux_demo(True)
        vus = self.lancer(faux)
        donnees = self.tmp.resolve() / 'donnees'
        self.assertEqual(appels[0], ('creer', str(donnees), ORIGINE, '/agent-courriel'))
        self.assertEqual(appels[1], ('actif', {'chemin': str(donnees / 'config.json')}))
        self.assertEqual(vus['app'], (str(donnees / 'config.json'), str(donnees / 'ui-auth.json')))
        self.assertEqual(vus['env'], str(donnees))
        self.assertEqual(vus['budget'][1:], ('web', __import__('agent').__version__))

    @unittest.skipUnless(importlib.util.find_spec('agent.demo56107') and importlib.util.find_spec('cryptography'),
                         'API du lot M ou cryptography absente')
    def test_H1_api_reelle_du_lot_m_instance_complete_et_session(self):
        """Vraie API du lot M et vraie interface, appelées dans ce processus (aucun réseau : WSGI direct)."""
        from agent import demo56107
        self.addCleanup(demo56107.desarmer)
        with mock.patch.dict(os.environ, {}):
            os.environ.pop('AXIORHUB_DATA_DIR', None)
            config = self.instance.creer(self.tmp, ORIGINE, '/agent-courriel')
            app = self.instance.application(config)
            self.assertTrue(str(config).startswith(str(self.tmp.resolve())))
            lanceur = dh.LanceurLocal.__new__(dh.LanceurLocal)
            lanceur.r = {'prefixe': '/agent-courriel'}
            lien = lanceur.lien_session({'config': str(config)})
            self.assertTrue(lien.startswith('/agent-courriel/poste-session?jeton='), lien)
            self.assertFalse(lanceur.quitter_demande({'config': str(config)}))

            def appel(chemin, cookie=''):
                res = {}
                env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': chemin.split('?')[0], 'QUERY_STRING': chemin.partition('?')[2],
                       'HTTP_HOST': 'demo.example.test', 'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http',
                       'REMOTE_ADDR': 'localhost', 'wsgi.input': io.BytesIO(b''), 'SERVER_NAME': 'demo.example.test', 'SERVER_PORT': '443'}
                if cookie:
                    env['HTTP_COOKIE'] = cookie
                corps = b''.join(app(env, lambda s, h, *_: res.update(statut=s, entetes=h)))
                return res['statut'], res['entetes'], corps.decode('utf-8', 'replace')
            statut, entetes, _ = appel(lien)
            self.assertEqual(statut[:3], '303', statut)
            cookies = '; '.join(v.split(';')[0] for k, v in entetes if k.lower() == 'set-cookie')
            self.assertIn('axh_poste=', cookies)
            statut, _, page = appel('/agent-courriel/aujourdhui', cookies)
            self.assertEqual(statut, '200 OK')
            self.assertIn('Mode démonstration', page)
            self.assertEqual(appel(lien)[0][:3], '403')                          # jeton à usage unique

    def test_H3_garde_reseau_du_processus(self):
        code = ('import importlib.util,socket,sys\n'
                'spec=importlib.util.spec_from_file_location("i",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\n'
                'm.couper_reseau()\n'
                'r=[]\n'
                'for f in (lambda: socket.socket(socket.AF_INET), lambda: socket.socket(), lambda: socket.create_connection(("192.0.2.1",443)),\n'
                '          lambda: socket.getaddrinfo("demo.example.test",443), lambda: socket.socket(socket.AF_INET6)):\n'
                '    try: f(); r.append("ouvert")\n'
                '    except OSError as exc: r.append(exc.errno)\n'
                'if hasattr(socket,"AF_UNIX"):\n'
                '    s=socket.socket(socket.AF_UNIX); s.close(); r.append("unix")\n'
                'print(r)\n')
        sortie = subprocess.run([sys.executable, '-c', code, str(DEMO / 'instance.py')], capture_output=True, text=True, timeout=60)
        self.assertEqual(sortie.returncode, 0, sortie.stderr)
        resultat = eval(sortie.stdout.strip())
        self.assertEqual(resultat[:5], [101] * 5)
        if hasattr(socket, 'AF_UNIX'):
            self.assertEqual(resultat[5], 'unix')


class SousGroupes(unittest.TestCase):
    """Cgroups : écritures attendues dans un faux arbre cgroup v2 (le vrai n'existe que sous systemd avec Delegate=yes)."""

    def test_H2_memoire_processeur_et_processus_bornes_par_instance(self):
        base = Path(tempfile.mkdtemp(prefix='h56107-cg-'))
        self.addCleanup(shutil.rmtree, base, True)
        (base / 'cgroup.procs').write_text('4242\n')
        (base / 'cgroup.subtree_control').write_text('')
        (base / 'cgroup.controllers').write_text('cpu memory pids io')
        cg = dh.Cgroups(base)
        self.assertTrue(cg.preparer())
        self.assertEqual((base / 'repartiteur' / 'cgroup.procs').read_text(), '4242')       # le répartiteur dans une feuille
        self.assertEqual((base / 'cgroup.subtree_control').read_text(), '+memory +cpu +pids')
        groupe = Path(cg.creer('0123abcd', 512, 50, 64))
        self.assertEqual(groupe, base / 'instance-0123abcd')
        self.assertEqual([(groupe / f).read_text() for f in ('memory.max', 'memory.swap.max', 'cpu.max', 'pids.max')],
                         [str(512 << 20), '0', '50000 100000', '64'])
        sans = dh.Cgroups(base / 'absent')
        self.assertFalse(sans.preparer())
        self.assertIsNone(sans.creer('0123abcd', 512, 50, 64))                               # repli : limites posées par l'instance


# ---------------------------------------------------------------------- essais Linux réels
LINUX = os.name == 'posix' and hasattr(socket, 'AF_UNIX')
CIBLES = (('192.0.2.1', 443), ('198.51.100.1', 80), ('203.0.113.1', 53), ('2001:db8::1', 443))


def sonder_reseau():
    """Tentatives de sortie vers des adresses de documentation et une résolution de nom ; errno ou « ouvert »."""
    r = {}
    for hote, port in CIBLES:
        try:
            socket.create_connection((hote, port), timeout=3).close()
            r['%s:%d' % (hote, port)] = 'ouvert'
        except OSError as exc:
            r['%s:%d' % (hote, port)] = exc.errno if exc.errno is not None else type(exc).__name__
    try:
        socket.getaddrinfo('demo.example.test', 443)
        r['dns'] = 'ouvert'
    except OSError as exc:
        r['dns'] = 'refus'
    return r


def sonde(argv):
    """Instance-sonde lancée par le vrai lanceur : serveur HTTP minimal sur le socket Unix, sans waitress ni garde du processus."""
    import argparse
    p = argparse.ArgumentParser()
    for nom in ('--dossier', '--socket', '--origine', '--memoire-mo', '--cpu-secondes', '--taille-max-mo', '--fils', '--corps-max-mo',
                '--cgroup', '--prefixe'):
        p.add_argument(nom, default='')
    a = p.parse_args(argv)
    donnees = Path(a.dossier, 'donnees')                    # marqueur du mode Démo : la vraie API jeton_de_session du lot M s'y applique
    donnees.mkdir(exist_ok=True)
    (donnees / 'DEMO-AXIORHUB.json').write_text('{"demo": true, "version": 1}', encoding='utf-8')

    class Gestion(socketserver.StreamRequestHandler):
        def handle(self):
            ligne = self.rfile.readline(65537).decode('latin-1').split()
            entetes = http.client.parse_headers(self.rfile)
            corps = self.rfile.read(int(entetes.get('Content-Length') or 0))
            if ligne[1].startswith(a.prefixe + '/poste-session?jeton='):
                lancement = json.loads((donnees / 'poste-lancement.json').read_text(encoding='utf-8'))   # écrit par le répartiteur
                self.wfile.write(b'HTTP/1.1 303 See Other\r\nLocation: %s/aujourdhui\r\nSet-Cookie: axh_poste=%s; Path=/; HttpOnly\r\n'
                                 b'Content-Length: 0\r\nConnection: close\r\n\r\n' % (a.prefixe.encode(), lancement['sha256'][:12].encode()))
                return
            if ligne[1] == '/sonde':
                texte = json.dumps({'reseau': sonder_reseau(), 'pid': os.getpid(), 'dossier': a.dossier})
            elif ligne[1] == '/saisie' and ligne[0] == 'POST':
                Path(a.dossier, 'saisie.txt').write_bytes(corps)
                texte = 'enregistré'
            elif ligne[1] == '/saisie':
                f = Path(a.dossier, 'saisie.txt')
                texte = f.read_text(encoding='utf-8') if f.exists() else ''
            else:
                texte = 'sonde ' + Path(a.dossier).name + ' cookie=' + str(entetes.get('Cookie'))
            brut = texte.encode()
            self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: %d\r\nConnection: close\r\n\r\n'
                             % len(brut) + brut)

    class Serveur(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
    Serveur(a.socket, Gestion).serve_forever()


@unittest.skipUnless(LINUX, 'essai réel : Linux seulement (sockets Unix, processus)')
class LinuxReel(Base):
    def setUp(self):
        super().setUp()
        r = dh.charger_reglages(None, environ={})
        r.update({'dossier_instances': str(self.tmp / 'instances'), 'dossier_journal': str(self.tmp / 'journal'), 'origine': ORIGINE,
                  'commande': [sys.executable, str(Path(__file__).resolve()), '--sonde']})
        self.vrai = dh.LanceurLocal(r, cgroups=dh.Cgroups('/inexistant-pour-essai'))
        self.vrai.cgroups.base = None
        self.app = dh.Repartiteur(r, self.vrai, self.horloge)
        self.app.preparer()
        self.addCleanup(self.app.arreter_tout)

    def commencer(self, *a, **k):
        k.setdefault('ouvrir', False)                         # l'ouverture de session suit le démarrage réel (attendre)
        return super().commencer(*a, **k)

    def attendre(self, jeton):
        fin = time.time() + 30
        while time.time() < fin:
            statut, entetes, page = self.appel('GET', '/', self.session(jeton))
            if statut == '303 See Other':                    # ouverture de session : vraie API du lot M, jeton suivi jusqu'à l'instance
                lieu = dict(entetes)['Location']
                self.assertTrue(lieu.startswith('/agent-courriel/poste-session?jeton='), lieu)
                statut, entetes, _ = self.appel('GET', lieu, self.session(jeton))
                self.assertEqual((statut, dict(entetes)['Location']), ('303 See Other', '/agent-courriel/aujourdhui'))
                continue
            if page.startswith('sonde '):
                return page
            time.sleep(0.2)
        self.fail('instance-sonde non prête')

    def test_H1_reel_un_processus_par_instance_et_isolement(self):
        _, a, _, _ = self.commencer(ip=IP_A)
        _, b, _, _ = self.commencer(ip=IP_B)
        pa, pb = self.attendre(a), self.attendre(b)
        self.assertNotEqual(pa.split()[1], pb.split()[1])
        self.assertIn('cookie=None', pa)                                         # le cookie du répartiteur n'arrive pas
        self.appel('POST', '/saisie', self.session(a), 'note=ORLANDEC'.encode())
        self.assertEqual(self.appel('GET', '/saisie', self.session(a))[2], 'note=ORLANDEC')
        self.assertEqual(self.appel('GET', '/saisie', self.session(b))[2], '')
        pids = [i.poignee['proc'].pid for i in self.app.instances.values()]
        self.assertEqual(len(set(pids)), 2)
        socket_a = [i for i in self.app.instances.values()][0].poignee['socket']
        self.assertEqual(os.stat(socket_a).st_mode & 0o777 & ~0o755, 0)
        premiere = [i for i in self.app.instances.values()][0]
        (premiere.dossier / 'donnees' / 'quitter.demande').write_text('x')     # « Quitter » du bandeau (vraie API quitter_demande)
        self.app.nettoyer()
        self.assertEqual((len(self.app.instances), premiere.dossier.exists()), (1, False))
        self.horloge.t += 121 * 60
        self.app.nettoyer()
        self.assertEqual((self.app.instances, list((self.tmp / 'instances').iterdir())), ({}, []))
        time.sleep(0.2)
        for pid in pids:
            with self.assertRaises(OSError):
                os.kill(pid, 0)                                                   # processus arrêtés

    @unittest.skipUnless(os.environ.get('AXH_DEMO_RESEAU_COUPE') == '1', 'essai dans un conteneur --network none seulement')
    def test_H3_reel_aucune_adresse_exterieure_joignable(self):
        _, a, _, _ = self.commencer()
        self.attendre(a)
        resultat = json.loads(self.appel('GET', '/sonde', self.session(a))[2])
        for cible, valeur in resultat['reseau'].items():
            self.assertIn(valeur, (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EADDRNOTAVAIL, 'refus'), cible)
        print('\n[essai réseau coupé] instance : %s ; répartiteur : %s' % (resultat['reseau'], sonder_reseau()), file=sys.stderr)
        self.assertNotIn('ouvert', json.dumps(sonder_reseau()))                    # le répartiteur non plus


if __name__ == '__main__':
    if '--sonde' in sys.argv:
        sonde([x for x in sys.argv[1:] if x != '--sonde'])
    else:
        unittest.main()

"""5.6.107 (lot M) : mode Démo — cabinet fictif prérempli, sans compte ni modèle, sans aucun effet externe.

Instances créées par l'API publique ``demo56107.creer`` dans des dossiers temporaires ; application réelle (WSGI) avec la session ouverte
d'office (jeton de lancement) ; boîte de démonstration locale ; IA de démonstration. Aucun réseau : le garde de la démo est armé et chaque
garde-fou a son contre-exemple (mutation en mémoire). Données fictives uniquement.

Identifiants : M1 création, M2 dates relatives, M3 pages principales, M4 aucun transport sortant, M5 envoi simulé, M6 IA marquée,
M7 réinitialisation et routes du bandeau, M8 isolement, M9 lanceur poste, M10 raccourcis Windows et Ubuntu, M11 bandeau et visite guidée.
Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
from datetime import datetime, timedelta, timezone
import imaplib
import io
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request

from agent import demo56107 as D
from agent.common import Stop, load_config

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
PAGES = ('/aujourdhui', '/courriels', '/dossiers', '/dossier?id=DOS-001', '/dossier?id=DOS-001&vue=pieces', '/a-relire', '/planning',
         '/fichiers', '/contacts-crm?vue=opportunites', '/assistant')


def _instance(base, nom='instance', graine=7):
    return Path(D.creer(Path(base) / nom, graine=graine)).parent


class Base(unittest.TestCase):
    """Une instance partagée par classe (création ≈ 5 s) ; garde désarmé après chaque essai, environnement rétabli."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='m107-'))
        cls.racine = _instance(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        D.desarmer()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.env = patch.dict(os.environ, D.environnement(self.racine))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(D.desarmer)
        self.config = load_config(str(self.racine / 'config.json'))

    def desk(self):
        from agent.desk import Desk
        d = Desk(load_config(str(self.racine / 'config.json')))
        self.addCleanup(d.db.close)
        return d

    def app(self):
        from agent.web import App
        return App(str(self.racine / 'config.json'), str(self.racine / 'ui-auth.json'))

    def auth(self):
        return json.loads((self.racine / 'ui-auth.json').read_text(encoding='utf-8'))

    def call(self, app, chemin, methode='GET', cookie='', corps=None, csrf=True):
        p, _, q = chemin.partition('?')
        brut = json.dumps(corps).encode() if corps is not None else b''
        auth = self.auth()
        env = {'REQUEST_METHOD': methode, 'PATH_INFO': '/agent-courriel' + p, 'QUERY_STRING': q, 'HTTP_HOST': '127.0.0.1:%d' % D.PORT,
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(brut), 'CONTENT_LENGTH': str(len(brut)), 'CONTENT_TYPE': 'application/json',
               'HTTP_COOKIE': cookie, 'REMOTE_ADDR': '127.0.0.1'}
        if methode == 'POST':
            env['HTTP_ORIGIN'] = auth['origin']
            if csrf:
                env['HTTP_X_CSRF_TOKEN'] = auth['csrf']
        out = {}

        def start(statut, entetes):
            out.update(status=statut, headers=entetes)
        out['body'] = b''.join(app(env, start)).decode('utf-8', 'replace')
        try:
            out['json'] = json.loads(out['body'])
        except ValueError:
            out['json'] = None
        return out

    def session(self, app):
        r = self.call(app, D.jeton_de_session(self.racine, '/courriels'))
        self.assertTrue(r['status'].startswith('303'), r['status'])
        cookie = [v for k, v in r['headers'] if k == 'Set-Cookie'][0].split(';')[0]
        self.assertIn('/courriels', dict(r['headers'])['Location'])
        return cookie

    def boite(self):
        return json.loads((self.racine / 'boite' / 'etat.json').read_text(encoding='utf-8'))['boites']


# ================================================================================ M1 : création
class Creation(Base):
    def test_M1_01_instance_complete_dans_un_dossier_vide(self):
        r = self.racine
        for nom in ('config.json', 'ui-auth.json', 'mot-de-passe-local', 'matters.json', D.MARQUEUR, 'state/desk.sqlite3', 'boite/etat.json'):
            self.assertTrue((r / nom).exists(), nom)
        self.assertTrue(D.actif(self.config))
        dossiers = json.loads((r / 'matters.json').read_text(encoding='utf-8'))
        self.assertTrue(8 <= len(dossiers) <= 12)
        self.assertIn('DOS-001', {m['id'] for m in dossiers})
        boites = self.boite()
        self.assertTrue(40 <= sum(len(b['uids']) for b in boites.values()) <= 60)
        self.assertEqual(len(list((r / 'stockage').rglob('*.pdf'))), 17)
        auth = self.auth()
        self.assertTrue(auth['poste'] and auth['username'] == D.COMPTE and auth['origin'] == D.ORIGINE)
        self.assertNotIn('demonstration-sans-valeur', json.dumps(self.config))       # aucun secret dans la configuration
        d = self.desk()
        compte = lambda t: d.db.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]   # noqa: E731
        self.assertEqual(compte('dossiers5697'), len(dossiers))
        self.assertGreaterEqual(compte('d51_documents'), 16)
        self.assertGreaterEqual(compte('d51_assertions'), 20)
        self.assertEqual(compte('agenda5634'), 8)
        self.assertEqual(compte('tasks'), 6)
        self.assertEqual(compte('opp5676'), 3)
        self.assertEqual(compte('manual_drafts'), 3)
        self.assertEqual(compte('identite56106_logos'), 1)
        self.assertGreaterEqual(d.db.execute("SELECT COUNT(*) FROM audiences5688").fetchone()[0], 2)
        fiche = d.db.execute("SELECT juridiction, rg FROM dossiers5697 WHERE id='DOS-001'").fetchone()
        self.assertEqual(tuple(fiche), ('TJ Valbrune', '24/01234'))

    def test_M1_02_refus_dun_dossier_non_vide(self):
        cible = self.tmp / 'occupe'
        cible.mkdir()
        (cible / 'notes.txt').write_text('fictif')
        with self.assertRaisesRegex(Stop, 'demo_dossier_non_vide'):
            D.creer(cible)
        with self.assertRaisesRegex(Stop, 'demo_dossier_non_vide'):
            D.creer(self.racine)                                    # une démo existante n'est pas écrasée non plus

    def test_M1_03_donnees_fictives_seulement(self):
        texte = ''.join(p.read_text(encoding='utf-8') for p in (D.DONNEES / 'courriels').glob('*.eml.txt'))
        texte += (D.DONNEES / 'cabinet.json').read_text(encoding='utf-8')
        adresses = set(re.findall(r'[\w.+-]+@([\w.-]+)', texte))
        self.assertTrue(adresses and all(a.endswith('example.test') for a in adresses), adresses - {a for a in adresses if a.endswith('example.test')})
        self.assertNotRegex(texte, r'\b(?:10|172|192)\.\d+\.\d+\.\d+\b')            # aucune adresse IP


# ================================================================================ M2 : dates relatives
class Dates(Base):
    def test_M2_01_jour_ouvre(self):
        samedi = datetime(2026, 11, 14).date()
        self.assertEqual(D.jour_ouvre(samedi, 0).weekday(), 0)          # avenir : lundi
        self.assertEqual(D.jour_ouvre(samedi, -1).weekday(), 4)         # passé : vendredi
        self.assertEqual(D.jour_ouvre(datetime(2026, 11, 16).date(), 2), datetime(2026, 11, 18).date())

    def test_M2_02_tout_est_d_aujourdhui_et_recalcule_un_autre_jour(self):
        jour = D.aujourdhui()
        marque = json.loads((self.racine / D.MARQUEUR).read_text(encoding='utf-8'))
        self.assertEqual(marque['jour'], jour.isoformat())
        maintenant = datetime.now(timezone.utc)
        dates = [datetime.fromisoformat(e[2]) for b in self.boite().values() for e in b['uids'].values()]
        self.assertTrue(all(maintenant - timedelta(days=16) < q <= maintenant for q in dates))
        self.assertTrue(any(maintenant - q < timedelta(hours=3) for q in dates))     # des messages de l'heure
        d = self.desk()
        premier = min(r[0] for r in d.db.execute("SELECT starts FROM agenda5634"))
        self.assertEqual(datetime.fromisoformat(premier).astimezone(D.PARIS).date(), D.jour_ouvre(jour, 0))
        self.assertFalse(D.preparer_lancement(self.racine))           # même jour : rien n'est refait
        # contre-exemple : une démo créée « hier » est remise à neuf au lancement, dates comprises
        (self.racine / D.MARQUEUR).write_text(json.dumps({**marque, 'jour': (jour - timedelta(days=3)).isoformat()}), encoding='utf-8')
        avant = datetime.fromisoformat(min(r[0] for r in d.db.execute("SELECT starts FROM agenda5634")))
        d.db.execute("UPDATE agenda5634 SET starts=?", ((avant - timedelta(days=3)).isoformat(),))
        d.db.commit()
        self.assertTrue(D.preparer_lancement(self.racine))
        self.assertEqual(json.loads((self.racine / D.MARQUEUR).read_text(encoding='utf-8'))['jour'], jour.isoformat())
        premier = min(r[0] for r in d.db.execute("SELECT starts FROM agenda5634"))
        self.assertEqual(datetime.fromisoformat(premier).astimezone(D.PARIS).date(), D.jour_ouvre(jour, 0))


# ================================================================================ M3 : pages principales
class Pages(Base):
    def test_M3_01_toutes_les_pages_principales_repondent_200_avec_le_bandeau(self):
        app = self.app()
        cookie = self.session(app)
        for chemin in PAGES:
            r = self.call(app, chemin, cookie=cookie)
            self.assertEqual(r['status'], '200 OK', chemin)
            self.assertIn('Mode démonstration — données fictives', r['body'], chemin)
            self.assertIn('data-demo107-recommencer', r['body'], chemin)
            self.assertNotIn('Erreur interne', r['body'], chemin)
        r = self.call(app, '/api440/m5625/boite/liste', cookie=cookie)
        self.assertEqual(r['status'], '200 OK')
        self.assertEqual(r['json']['total'], 41)
        sujets = [x['subject'] for x in r['json']['items']]
        self.assertIn('DURAND c. MARTIN — conclusions adverses reçues', sujets)
        fraude = next(x for x in r['json']['items'] if x['subject'].startswith('Changement de RIB'))
        self.assertTrue(fraude['fraude'] and fraude['fraude']['niveau'] in ('suspect', 'danger'), fraude['fraude'])
        # analyse complète (lecteur) : changement de RIB détecté, IBAN valide et manifestement fictif (banque et guichet à zéro)
        from email import policy
        from email.parser import BytesParser
        from agent import fraude56106
        brut = (D.DONNEES / 'courriels' / '03-fraude-rib.eml.txt').read_text(encoding='utf-8')
        self.assertIn('FR76 0000 0000 0000 0000 0000 097', brut)
        self.assertEqual(fraude56106.ibans_du_texte(brut), ['FR7600000000000000000000097'])
        d = self.desk()
        msg = BytesParser(policy=policy.default).parsebytes(brut.encode('utf-8'))   # en-têtes X-Demo-* sans effet sur l'analyse
        v = fraude56106.analyser(msg, fraude56106.contexte(d))
        self.assertIn('changement_rib', json.dumps(v, ensure_ascii=False))
        self.assertIn(v.get('niveau'), ('suspect', 'danger'), v)
        r = self.call(app, '/api440/m56107/demo/etat', cookie=cookie)
        self.assertEqual((r['status'], r['json']['demo']), ('200 OK', True))
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])

    def test_M3_02_sans_session_rien_n_est_servi(self):
        app = self.app()
        r = self.call(app, '/courriels')
        self.assertFalse(r['status'].startswith('200'), r['status'])
        jeton = D.jeton_de_session(self.racine)
        self.assertTrue(self.call(app, jeton)['status'].startswith('303'))
        self.assertTrue(self.call(app, jeton)['status'].startswith('403'))      # jeton à usage unique

    def test_M3_03_courriel_rattache_a_son_dossier(self):
        from agent import boite5625
        d = self.desk()
        liste = boite5625.listing(d, 1)
        uid = next(x['uid'] for x in liste['items'] if x['subject'] == 'DURAND c. MARTIN — conclusions adverses reçues')
        lu = boite5625.message(d, uid)
        texte = json.dumps(lu, ensure_ascii=False)
        self.assertIn('DOS-001', texte)


# ================================================================================ M4 : aucun transport sortant
class Transport(Base):
    def essais(self):
        """Toutes les sorties possibles ; chacune doit être refusée par le garde (rend la liste des sorties qui ne l'ont pas été)."""
        from agent.common import HTTP
        passes = []
        tentatives = {
            'socket': lambda: socket.create_connection(('192.0.2.10', 443), timeout=1),
            'dns': lambda: socket.getaddrinfo('cloud.example.test', 443),
            'smtp': lambda: smtplib.SMTP_SSL('smtp.demo.example.test', 465, timeout=1),
            'imap': lambda: imaplib.IMAP4_SSL('imap.example.test', 993, timeout=1),
            'urllib': lambda: urllib.request.urlopen('https://maj.example.test/version.json', timeout=1),
            'http_commun': lambda: HTTP('https://api.example.test').request('GET', 'https://api.example.test/v1'),
            'local': lambda: socket.create_connection(('127.0.0.1', 11434), timeout=1),
        }
        for nom, essai in tentatives.items():
            try:
                essai()
                passes.append(nom)
            except (Stop, OSError):
                pass
        return passes

    def test_M4_01_garde_refuse_tout_transport_sortant(self):
        D.armer(self.config)
        joints = []
        orig = dict(D._ETAT['origines'])

        def note(nom):
            def f(*a, **k):
                joints.append(nom)
                raise ConnectionRefusedError('essai : rien ne sort')
            return f
        with patch.dict(D._ETAT['origines'], {'connect': note('connect'), 'connect_ex': note('connect_ex'), 'create_connection': note('create_connection'),
                                              'getaddrinfo': note('getaddrinfo'), 'gethostbyname': note('gethostbyname')}):
            self.essais()
            self.assertEqual(joints, [], 'une sortie a atteint la couche réseau malgré le garde')
            avant = D._ETAT['refus']
            self.essais()
            self.assertGreaterEqual(D._ETAT['refus'] - avant, 6)
            # contre-exemple (mutation en mémoire) : sans le garde, les mêmes appels atteignent la couche réseau
            with patch.object(D, '_garde_active', lambda: False):
                self.essais()
            self.assertTrue({'create_connection', 'getaddrinfo'} & set(joints), joints)
        self.assertEqual(D._ETAT['origines'], orig)

    def test_M4_02_processus_non_demo_inchange(self):
        D._installer_correctifs()
        D.desarmer()
        joints = []
        with patch.dict(D._ETAT['origines'], {'create_connection': lambda *a, **k: joints.append(a) or (_ for _ in ()).throw(ConnectionRefusedError())}):
            with self.assertRaises(OSError):
                socket.create_connection(('192.0.2.10', 443), timeout=1)
        self.assertEqual(len(joints), 1)                                  # hors démo, le correctif laisse passer (ici vers un faux)

    def test_M4_03_desk_de_demo_arme_le_processus(self):
        self.assertFalse(D.arme())
        self.desk()
        self.assertTrue(D.arme())
        from agent import send490
        self.assertIs(send490.smtp_factory, D._SMTPDemo)
        D.desarmer()
        self.assertIsNone(send490.smtp_factory)

    def test_M4_04_messagerie_reelle_refusee_en_demo(self):
        from agent import mailbox
        D.armer(self.config)
        with self.assertRaisesRegex(Stop, 'connexion_imap_echouee'):
            mailbox.connect({'host': 'imap.example.test', 'port': 993})
        boite = mailbox.Mailbox(self.config['mail'])                      # la boîte de démonstration, elle, répond
        self.assertEqual(boite.select('INBOX'), '101')
        boite.close()


# ================================================================================ M5 : envoi simulé
class Envoi(Base):
    def test_M5_01_envoi_va_dans_envoyes_demo_apres_le_delai_sans_rien_envoyer(self):
        from email.message import EmailMessage
        from email import policy
        from agent import envoi56106 as E, mailbox, send490
        d = self.desk()
        cfg = d.c['mail']
        m = EmailMessage()
        m['From'], m['To'], m['Subject'] = 'Me Claire DURAND <claire.durand@cabinet-durand.example.test>', 'direction@durand-batiment.example.test', 'DURAND c. MARTIN — pièces'
        m['X-AxiorHub-Origine'] = 'webmail5628'
        m.set_content('Bonjour,\n\nMerci pour les pièces.\n\nMe Claire DURAND')
        boite = mailbox.Mailbox(cfg)
        _, rep = boite.conn.append(mailbox.imap_quote(mailbox.modified_utf7(cfg['drafts'])), '(\\Draft)', imaplib.Time2Internaldate(time.time()),
                                   m.as_bytes(policy=policy.SMTP))
        boite.close()
        uid = re.search(rb'APPENDUID (\d+) (\d+)', rep[0])
        validite, uid = uid[1].decode(), uid[2].decode()
        p = send490.prepare(d, uid, validite)
        now = time.time() + 2
        D._SMTPDemo.envoyes.clear()
        with patch.object(smtplib, 'SMTP_SSL', side_effect=AssertionError('aucun SMTP réel')), patch.object(smtplib, 'SMTP', side_effect=AssertionError('aucun SMTP réel')):
            out = E.mettre_en_file(d, 'cabinet', 'administrateur', p['token'], now=now)
            self.assertEqual(E.tick(d, 'b1', now + 5), [])                 # délai d'annulation : rien ne part
            self.assertEqual(E.tick(d, 'b1', now + out['delai'] + 0.5), [(out['id'], 'parti')])
        self.assertEqual(len(D._SMTPDemo.envoyes), 1)
        envoyes = self.boite()[mailbox.modified_utf7(D.BOITE_ENVOYES)]
        dernier = envoyes['uids'][str(max(int(u) for u in envoyes['uids']))]
        brut = (self.racine / 'boite' / 'messages' / (dernier[1] + '.eml')).read_bytes().decode('utf-8', 'replace')
        self.assertIn('Démonstration : rien n’a été envoyé', brut)
        self.assertIn('X-AxiorHub-Demonstration', brut)
        # contre-exemple : sans le client de démonstration, l'envoi tenterait le SMTP réel (refusé ici par le garde, jamais joint)
        d.c['mail']['smtp']['host'] = 'smtp.demo.example.test'
        with patch.object(send490, 'smtp_factory', None), self.assertRaisesRegex(Stop, 'smtp_echec'):
            send490._smtp_send(send490.smtp_config(d), m, 'claire.durand@cabinet-durand.example.test', ['direction@durand-batiment.example.test'])


# ================================================================================ M6 : IA de démonstration
class IA(Base):
    def test_M6_01_reponses_marquees_et_conformes(self):
        from agent.model import CHAT, MANUAL_DRAFT_FOURNISSEUR, VERIFY
        from agent.lecteur56106 import SORTIE
        marque = 'Réponse de démonstration'
        r = json.loads(D.repondre({'messages': [{'role': 'user', 'content': json.dumps({'question_avocat': 'Quelles audiences cette semaine ?'})}], 'format': CHAT}))
        self.assertTrue(r['answer'].startswith(marque))
        self.assertIn('KERVILAC', r['answer'])
        self.assertEqual(set(r), set(CHAT['properties']))
        r = json.loads(D.repondre({'messages': [{'role': 'user', 'content': 'Question sans rapport'}], 'format': CHAT}))
        self.assertIn('aucun modèle', r['answer'])
        r = json.loads(D.repondre({'messages': [{'role': 'user', 'content': 'De quelles pièces avez-vous besoin ?'}], 'format': MANUAL_DRAFT_FOURNISSEUR}))
        self.assertTrue(r['body'].startswith(marque) and r['requires_decision'] is True)
        self.assertEqual(set(r), set(MANUAL_DRAFT_FOURNISSEUR['properties']))
        charge = json.dumps({'courriels': [{'ref': 'C1', 'objet': 'Relance'}, {'ref': 'C2', 'objet': 'Pièces'}]})
        r = json.loads(D.repondre({'messages': [{'role': 'user', 'content': charge}], 'format': SORTIE}))
        self.assertTrue(r['synthese'].startswith(marque))
        self.assertEqual([x['ref'] for x in r['courriels']], ['C1', 'C2'])
        r = json.loads(D.repondre({'messages': [], 'format': VERIFY}))
        self.assertTrue(r['grounded'] and r['recipient_safe'])
        self.assertTrue(D.repondre({'messages': []}).startswith(marque))

    def test_M6_02_chaine_commune_sans_reseau(self):
        from agent.model import CHAT, Model, routed_config
        D.armer(self.config)
        d = self.desk()
        joints = []
        with patch.dict(D._ETAT['origines'], {'connect': lambda *a: joints.append(a), 'create_connection': lambda *a, **k: joints.append(a)}):
            modele = Model(routed_config(d.c, 'assistant'))
            sortie = modele.complete([{'role': 'user', 'content': json.dumps({'question_avocat': 'Résume le dossier DURAND c. MARTIN'})}], json_schema=CHAT)
        self.assertEqual(joints, [])
        self.assertTrue(json.loads(sortie)['answer'].startswith('Réponse de démonstration'))
        self.assertIn('RG 24/01234', json.loads(sortie)['answer'])
        # contre-exemple : sans démo armée, la même chaîne s'adresserait au moteur local (ici un faux qui note l'appel)
        D.desarmer()
        appels = []
        with patch.dict(D._ETAT['origines'], {'http_json': lambda self, m, p, data=None: appels.append(p) or {}}):
            try:
                Model(routed_config(d.c, 'assistant'))
            except Stop:
                pass
        self.assertTrue(appels)

    def test_M6_03_projets_a_relire_marques(self):
        d = self.desk()
        corps = [json.loads(r[0])['result']['body'] for r in d.db.execute('SELECT data FROM manual_drafts')]
        self.assertEqual(len(corps), 3)
        self.assertTrue(all(c.startswith('Réponse de démonstration') for c in corps))
        notes = [r[0] for r in d.db.execute('SELECT note FROM delta56100_resultats')]
        self.assertTrue(notes and all(n.startswith('Réponse de démonstration') for n in notes))


# ================================================================================ M7 : réinitialisation, routes du bandeau
class Reinitialisation(Base):
    def test_M7_01_recommencer_remet_tout_a_neuf(self):
        d = self.desk()
        d.db.execute('DELETE FROM tasks')
        d.db.execute("UPDATE opp5676 SET title='modifié'")
        d.db.commit()
        (self.racine / 'stockage' / 'Dossiers' / 'ajout.txt').write_text('fictif')
        (self.racine / 'quitter.demande').write_text('x')
        etat = json.loads((self.racine / 'boite' / 'etat.json').read_text(encoding='utf-8'))
        etat['boites']['INBOX']['uids'] = {}
        (self.racine / 'boite' / 'etat.json').write_text(json.dumps(etat), encoding='utf-8')
        sessions = self.racine / 'poste-sessions.json'
        sessions.write_text('{"empreinte": 9999999999}', encoding='utf-8')
        D.reinitialiser(self.racine / 'config.json')
        self.assertEqual(d.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 6)
        self.assertEqual(d.db.execute("SELECT COUNT(*) FROM opp5676 WHERE title='modifié'").fetchone()[0], 0)
        self.assertEqual(d.db.execute('SELECT COUNT(*) FROM dossiers5697').fetchone()[0], 10)
        self.assertEqual(len(self.boite()['INBOX']['uids']), 41)
        self.assertFalse((self.racine / 'stockage' / 'Dossiers' / 'ajout.txt').exists())
        self.assertFalse(D.quitter_demande(self.racine))
        self.assertTrue(sessions.exists())                           # la personne garde sa fenêtre ouverte
        self.assertTrue(D.actif(load_config(str(self.racine / 'config.json'))))

    def test_M7_02_routes_du_bandeau(self):
        app = self.app()
        cookie = self.session(app)
        marque = (self.racine / D.MARQUEUR).read_text(encoding='utf-8')
        r = self.call(app, '/api440/m56107/demo/reinitialiser', 'POST', cookie, {'cle': 'cle-essai-0001'}, csrf=False)
        self.assertFalse(r['status'].startswith('200'), r['status'])  # sans jeton CSRF : refus, rien n'est refait
        self.assertEqual((self.racine / D.MARQUEUR).read_text(encoding='utf-8'), marque)
        r = self.call(app, '/api440/m56107/demo/reinitialiser', 'POST', cookie, {})
        self.assertEqual(r['json']['error'], 'cle_idempotence_requise')
        r = self.call(app, '/api440/m56107/demo/reinitialiser', 'POST', cookie, {'cle': 'cle-essai-0002'})
        self.assertEqual(r['status'], '200 OK', r['body'])
        self.assertTrue(r['json']['fait'])
        r = self.call(app, '/api440/m56107/demo/quitter', 'POST', cookie, {'cle': 'cle-essai-0003'})
        self.assertEqual(r['status'], '200 OK', r['body'])
        self.assertTrue(D.quitter_demande(self.racine))
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])
        from agent.standalone_auth import allowed
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56107/demo/reinitialiser'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56107/demo/quitter'))
        self.assertTrue(allowed('assistant', 'GET', '/api440/m56107/demo/etat'))


# ================================================================================ M8 : isolement
class Isolement(Base):
    def test_M8_01_configuration_qui_sort_du_dossier_refusee(self):
        autre = self.tmp / 'cabinet-reel'
        (autre / 'state').mkdir(parents=True, exist_ok=True)
        for cle in ('state_dir', 'matters_file'):
            c = json.loads(json.dumps(self.config))
            c[cle] = str(autre / ('state' if cle == 'state_dir' else 'matters.json'))
            self.assertFalse(D.actif(c), cle)
            with self.assertRaisesRegex(Stop, 'demo_isolement_refuse'):
                D.armer(c)
        c = json.loads(json.dumps(self.config))
        c['nextcloud']['local_path'] = str(autre)
        with self.assertRaisesRegex(Stop, 'demo_isolement_refuse'):
            D.verifier_isolement(c)
        self.assertFalse(D.arme())

    def test_M8_02_boite_locale_seulement_dans_une_demo(self):
        from agent import mailbox
        D._installer_correctifs()
        faux = self.tmp / 'pas-une-demo' / 'boite'
        faux.mkdir(parents=True, exist_ok=True)
        appels = []
        with patch.dict(D._ETAT['origines'], {'imap_connect': lambda cfg: appels.append(cfg) or 'reelle'}):
            self.assertEqual(mailbox.connect({'host': 'x', 'demo56107_boite': str(faux)}), 'reelle')
            # contre-exemple : sans le contrôle du marqueur, n'importe quel dossier serait servi comme boîte de démonstration
            with patch.object(D, '_boite_admise', lambda chemin: True):
                self.assertNotEqual(mailbox.connect({'host': 'x', 'demo56107_boite': str(faux)}), 'reelle')

    def test_M8_03_jamais_par_dessus_une_installation_reelle(self):
        maison = self.tmp / 'maison'
        reel = (maison / 'AppData' / 'Local' / 'AxiorHub Pilote' / 'donnees') if os.name == 'nt' else (maison / '.local' / 'share' / 'axiorhub-pilote')
        reel.mkdir(parents=True)
        (reel / 'config.json').write_text('{}', encoding='utf-8')
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': str(maison)}):
            for cible in (reel / 'demo', reel, reel / 'state' / 'x'):
                with self.assertRaisesRegex(Stop, 'demo_sur_installation_reelle'):
                    D.creer(cible)
            demo = D.dossier_poste()
            self.assertFalse(D._dans(demo, reel.resolve()) or D._dans(reel.resolve(), demo.resolve()))
        serveur = self.tmp / 'serveur'
        (serveur / 'state').mkdir(parents=True)
        (serveur / 'state' / 'desk.sqlite3').write_bytes(b'')
        with self.assertRaisesRegex(Stop, 'demo_sur_installation_reelle'):
            D.creer(serveur / 'demo')
        self.assertEqual(sorted(p.name for p in reel.iterdir()), ['config.json'])   # rien n'a été écrit dans l'installation réelle

    def test_M8_04_configuration_reelle_jamais_armee(self):
        reel = load_config(str(self.racine / 'config.json'))
        reel.pop('demo56107')
        self.assertFalse(D.actif(reel))
        self.assertFalse(D.au_bureau(reel))
        self.assertFalse(D.arme())
        self.assertEqual(D.bandeau_html('/agent-courriel', reel), '')
        # marqueur absent : même configuration de démo refusée
        c = json.loads(json.dumps(self.config))
        c['demo56107']['racine'] = str(self.tmp / 'ailleurs')
        self.assertFalse(D.actif(c))


# ================================================================================ ML : démo en anglais, allemand et espagnol
LANGUES = {  # langue : (cabinet, premier dossier, référence de DURAND c. MARTIN, message de juridiction, marque de l'IA, IBAN impossible)
    'en': ('Durand & Martin Solicitors', 'MAT-001', 'VB24C01234', '[CE-File]', 'Demonstration response', 'GB56ZZZZ00000000000000'),
    'de': ('Kanzlei Durand', 'AKT-001', '2 O 1234/24', '[beA]', 'Demo-Antwort', 'DE36000000000000000000'),
    'es': ('Despacho Durand Abogados', 'EXP-001', '1234/2024', '[LexNET]', 'Respuesta de demostración', 'ES8200000000000000000000'),
}
FRANCAIS = {'vous', 'nous', 'avec', 'pour', 'dans', 'une', 'sont', 'cette', 'merci', 'bonjour', 'cordialement', 'maître', 'maitre', 'dossier',
            'courriel', 'madame', 'monsieur', 'avocat', 'avocate', 'fictif', 'fictive', 'démonstration', 'réponse', 'pièce', 'pièces', 'juridiction',
            'greffe', 'société', 'rendez-vous', 'tâche', 'échéance', 'envoyés', 'brouillons', 'corbeille'}


CODES = {'role', 'nature', 'source', 'courriel'}   # codes techniques communs aux quatre langues (rendez_vous, juridiction…), pas du texte


def _valeurs(x):
    if isinstance(x, dict):
        for k, v in x.items():
            if k not in CODES:
                yield from _valeurs(v)
    elif isinstance(x, list):
        for v in x:
            yield from _valeurs(v)
    elif isinstance(x, str):
        yield x


class Langues(unittest.TestCase):
    """Une instance par langue (création ≈ 3 s chacune) ; données du pays, interface dans la langue, IBAN impossibles détectés."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='m107-langues-'))
        cls.racines = {l: Path(D.creer(cls.tmp / l, graine=11, langue=l)).parent for l in tuple(LANGUES) + ('fr',)}

    @classmethod
    def tearDownClass(cls):
        D.desarmer()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.addCleanup(D.desarmer)

    def env(self, langue):
        e = patch.dict(os.environ, D.environnement(self.racines[langue]))
        e.start()
        self.addCleanup(e.stop)
        return load_config(str(self.racines[langue] / 'config.json'))

    def test_ML_01_creation_dans_chaque_langue(self):
        from agent.i18n5631 import cabinet_default
        from agent.mailbox import modified_utf7
        with self.assertRaisesRegex(Stop, 'demo_langue_inconnue'):
            D.creer(self.tmp / 'xx', langue='it')
        for langue, (cabinet, premier, ref, juridiction, marque, iban) in LANGUES.items():
            with self.subTest(langue=langue):
                r = self.racines[langue]
                cfg = self.env(langue)
                t = D.textes(langue)
                self.assertEqual(set(json.loads((D.dossier_donnees(langue) / 'cabinet.json').read_text(encoding='utf-8'))['textes']), set(D.TEXTES_FR))
                self.assertEqual((D.langue_de(r), cfg['demo56107']['langue'], cfg['interface']['langue'], cabinet_default(cfg)), (langue,) * 4)
                self.assertTrue(D.actif(cfg))
                dossiers = json.loads((r / 'matters.json').read_text(encoding='utf-8'))
                self.assertEqual(dossiers[0]['id'], premier)
                self.assertTrue(all(m['path'].startswith(t['racine_dossiers'] + '/') for m in dossiers))
                self.assertIn(ref, json.dumps(dossiers, ensure_ascii=False))
                self.assertEqual(cfg['mail']['sent'], t['boites']['envoyes'])
                boites = json.loads((r / 'boite' / 'etat.json').read_text(encoding='utf-8'))['boites']
                self.assertIn(modified_utf7(t['boites']['envoyes']), boites)
                self.assertEqual(len(boites['INBOX']['uids']), 41)
                self.assertEqual(len(list((r / 'stockage').rglob('*.pdf'))), 17)
                from agent.desk import Desk
                d = Desk(cfg)
                try:
                    self.assertEqual(d.db.execute("SELECT rg FROM dossiers5697 WHERE id=?", (premier,)).fetchone()[0], ref)
                    nom = d.db.execute('SELECT data_json FROM cabinet_profile5689').fetchone()[0]
                    self.assertIn(cabinet, nom)
                    corps = [json.loads(x[0])['result']['body'] for x in d.db.execute('SELECT data FROM manual_drafts')]
                    self.assertTrue(len(corps) == 3 and all(c.startswith(marque) for c in corps), corps)
                finally:
                    d.db.close()
                self.assertEqual(D.reponses(langue)['marque'], marque)
                self.assertIn('id="demo107"', D.bandeau_html('/agent-courriel', cfg))
                self.assertIn('data-demo107-dossier="%s"' % premier, D.bandeau_html('/agent-courriel', cfg))

    def test_ML_02_pages_principales_200_dans_chaque_langue(self):
        from agent.web import App
        for langue, (cabinet, premier, _, juridiction, _, _) in LANGUES.items():
            with self.subTest(langue=langue):
                r = self.racines[langue]
                self.env(langue)
                app = App(str(r / 'config.json'), str(r / 'ui-auth.json'))
                outils = Base('run')
                outils.racine = r
                cookie = outils.session(app)
                for chemin in [p.replace('DOS-001', premier) for p in PAGES]:
                    rep = outils.call(app, chemin, cookie=cookie)
                    self.assertEqual(rep['status'], '200 OK', (langue, chemin))
                    self.assertIn('id="demo107"', rep['body'], (langue, chemin))
                    self.assertNotIn('Erreur interne', rep['body'], (langue, chemin))
                rep = outils.call(app, '/api440/m5625/boite/liste', cookie=cookie)
                self.assertEqual(rep['json']['total'], 41)
                self.assertTrue(any(x['subject'].startswith(juridiction) for x in rep['json']['items']), juridiction)
                D.desarmer()

    def test_ML_03_aucun_texte_francais_dans_les_donnees(self):
        for langue in LANGUES:
            with self.subTest(langue=langue):
                base = D.dossier_donnees(langue)
                textes = []
                for nom in ('cabinet.json', 'reponses-ia.json'):
                    textes += list(_valeurs(json.loads((base / nom).read_text(encoding='utf-8'))))
                courriels = sorted((base / 'courriels').glob('*.eml.txt'))
                self.assertEqual([p.name for p in courriels], sorted(p.name for p in (D.DONNEES / 'courriels').glob('*.eml.txt')))
                for p in courriels:
                    textes += [l for l in p.read_text(encoding='utf-8').splitlines() if not l.startswith(('X-Demo-', 'Message-ID:'))]
                mots = {m.lower() for x in textes for m in re.findall(r"[A-Za-zÀ-ÿ-]+", x)}
                self.assertEqual(sorted(mots & FRANCAIS), [], langue)
                self.assertNotIn('DOS-0', '\n'.join(textes))
                adresses = set(re.findall(r'[\w.+-]+@([\w.-]+)', '\n'.join(textes)))
                self.assertTrue(adresses and all(a == 'example.test' or a.endswith('.example.test') for a in adresses), adresses)

    def test_ML_04_iban_impossibles_et_detectes(self):
        from email import policy
        from email.parser import BytesParser
        from agent import fraude56106
        from agent.desk import Desk
        attendus = {'fr': 'FR7600000000000000000000097', **{l: v[5] for l, v in LANGUES.items()}}
        for langue, iban in attendus.items():
            with self.subTest(langue=langue):
                brut = (D.dossier_donnees(langue) / 'courriels' / '03-fraude-rib.eml.txt').read_bytes()
                self.assertEqual(fraude56106.ibans_du_texte(brut.decode('utf-8')), [iban])
                self.assertTrue(fraude56106.iban_valide(iban))                      # clés correctes…
                self.assertEqual(set(re.sub(r'[A-Z]', '', iban[4:]).replace('97', '', 1 if langue == 'fr' else 0)), {'0'})   # …codes à zéro
                if langue == 'fr':
                    continue
                cfg = self.env(langue)
                d = Desk(cfg)
                try:
                    v = fraude56106.analyser(BytesParser(policy=policy.default).parsebytes(brut), fraude56106.contexte(d))
                finally:
                    d.db.close()
                self.assertIn(v.get('niveau'), ('suspect', 'danger'), v)
                self.assertIn('nom_usurpe', json.dumps(v))                          # signal indépendant de la langue (nom du client usurpé)
                D.desarmer()

    def test_ML_05_ia_mention_et_reinitialisation_gardent_la_langue(self):
        from agent.model import CHAT
        r = self.racines['de']
        cfg = self.env('de')
        D.armer(cfg)
        sortie = json.loads(D.repondre({'messages': [{'role': 'user', 'content': json.dumps({'question_avocat': 'Termine diese Woche?'})}], 'format': CHAT}))
        self.assertTrue(sortie['answer'].startswith('Demo-Antwort'))
        self.assertEqual(sortie['limits'], [D.textes('de')['limite_assistant']])
        self.assertEqual(D.mention_envoi(r), D.textes('de')['mention_envoi'])
        D.desarmer()
        D.reinitialiser(r)
        self.assertEqual(D.langue_de(r), 'de')
        D.reinitialiser(r, langue='es')                                             # le poste change de langue en refaisant la démo
        self.assertEqual((D.langue_de(r), json.loads((r / 'matters.json').read_text(encoding='utf-8'))[0]['id']), ('es', 'EXP-001'))
        D.reinitialiser(r, langue='de')
        self.assertEqual(load_config(str(r / 'config.json'))['interface']['langue'], 'de')

    def test_ML_07_alertes_et_messages_de_juridiction_dans_les_quatre_langues(self):
        """Aucune fausse alerte sur un courriel légitime ; le courriel frauduleux est « Danger » ; le message de juridiction est reconnu et
        sa date d'audience est sûre (règles, jamais le modèle). Réglage de l'alerte posé par la démo comme par un cabinet."""
        from email import policy
        from email.parser import BytesParser
        from types import SimpleNamespace
        from agent import fraude56106, routine_message56106 as R
        from agent.desk import Desk
        racines = dict(self.racines)
        for langue, r in sorted(racines.items()):
            with self.subTest(langue=langue):
                cfg = self.env(langue)
                d = Desk(cfg)
                try:
                    regl = fraude56106.reglages(d)
                    self.assertEqual(regl['authserv_id'], [D.SERVEUR_COURRIEL])
                    self.assertEqual(regl['domaines_officiels'], D._cabinet(langue)['cabinet']['domaines_officiels'])
                    ctx = fraude56106.contexte(d)
                    boite = json.loads((r / 'boite' / 'etat.json').read_text(encoding='utf-8'))['boites']['INBOX']['uids']
                    alertes, juridictions = {}, []
                    for drapeaux, sha, quand in boite.values():
                        m = BytesParser(policy=policy.default).parsebytes((r / 'boite' / 'messages' / (sha + '.eml')).read_bytes())
                        v = fraude56106.analyser(m, ctx)
                        if v.get('niveau'):
                            alertes[str(m['Message-ID'])] = v['niveau']
                        texte = m.get_body(('plain',)).get_content()
                        if R.message_de_juridiction(SimpleNamespace(sender=str(m['From']), subject=str(m['Subject']), text=texte)) and \
                                str(m['Message-ID']) == '<rpva-fixation@demo.example.test>':
                            juridictions.append(R.extraire_evenement(str(m['Subject']), texte, D.aujourdhui()))
                    self.assertEqual(alertes, {'<fraude-rib@demo.example.test>': 'danger'})
                    self.assertEqual(len(juridictions), 1)
                    ev = juridictions[0]
                    audience = next(a for a in D._cabinet(langue)['agenda'] if a['nature'] == 'audience' and a['dossier'] == D._cabinet(langue)['dossiers'][0]['id'])
                    self.assertEqual((ev['certitude'], ev['date'], ev['heure']), ('sure', D.jour_ouvre(D.aujourdhui(), audience['jour']).isoformat(), '09:30'), ev)
                finally:
                    d.db.close()
                    D.desarmer()

    def test_ML_08_a_relire_message_recu_et_onglets_d_une_note(self):
        """Chaque proposition semée retrouve son courriel (« Message reçu » affiche le texte) ; une note de l'IA n'offre que ses onglets
        (ni « Message reçu », ni « Pièces », ni « Propositions de l'IA ») et la feuille de style masque vraiment un onglet ``hidden``."""
        from agent import relecture56100 as rl, relecture56101 as r1
        from agent.desk import Desk
        acteur = ('cabinet', 'administrateur')
        for langue, r in sorted(self.racines.items()):
            with self.subTest(langue=langue):
                d = Desk(self.env(langue))
                try:
                    cles = [x[0] for x in d.db.execute('SELECT mail_key FROM manual_drafts')]
                    self.assertEqual(len(cles), 3)
                    for cle in cles:
                        data = rl._producteur(d, cle)
                        ident, revision = rl.id_proposition(cle, data), rl.revision_proposition(data)
                        s = rl.section(d, acteur, ident, revision, 'message')
                        self.assertFalse(s.get('indisponible'), s)
                        self.assertTrue(s['texte'].strip(), s)                                  # le courriel d'origine, relu dans la boîte
                        self.assertEqual(s['objet'], json.loads((r / 'state' / 'reports' / (cle + '.json')).read_text(encoding='utf-8'))['subject'])
                    notes = [x[0] for x in d.db.execute('SELECT id FROM delta56100_resultats')]
                    self.assertEqual(len(notes), 2)
                    for rid in notes:
                        ident = r1.id_note(rid)
                        detail = rl.detail(d, acteur, ident, '/agent-courriel')
                        self.assertEqual([o[0] for o in detail['onglets']], ['projet', 'sources'])
                        for sous in ('message', 'pieces', 'propositions'):
                            with self.assertRaises(rl.Refus):
                                rl.section(d, acteur, ident, detail['revision'], sous)
                        self.assertEqual(rl.section(d, acteur, ident, detail['revision'], 'sources')['section'], 'sources')
                finally:
                    d.db.close()
                    D.desarmer()
        css = (ROOT / 'agent' / 'static' / 'v56100-lecteur.css').read_text(encoding='utf-8')
        self.assertIn('.ax100-lecteur [data-section][hidden] { display: none !important; }', css)
        self.assertIn('.ax100-lecteur [data-section][hidden] { display: none !important; }', (STATIC / 'app520.css').read_text(encoding='utf-8'))

    def test_ML_06_lanceur_poste_option_langue(self):
        import poste
        maison = self.tmp / 'poste'
        demo = poste.paths_demo(str(maison))
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': str(maison)}):
            poste.preparer_demo(demo, poste.DEMO_PORT, 'en')
            self.assertEqual(D.langue_de(demo['data']), 'en')
            poste.preparer_demo(demo, poste.DEMO_PORT)                              # sans --langue : celle de la démo existante
            self.assertEqual(D.langue_de(demo['data']), 'en')
            poste.preparer_demo(demo, poste.DEMO_PORT, 'es')
            self.assertEqual(D.langue_de(demo['data']), 'es')
        with self.assertRaises(SystemExit):
            poste.main(['--demo', '--langue', 'it', '--home', str(maison)])


# ================================================================================ M9 : lanceur poste
class Lanceur(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='m107-poste-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(D.desarmer)

    def test_M9_01_dossier_port_et_services_propres_a_la_demo(self):
        import poste
        reel, demo = poste.paths(str(self.tmp)), poste.paths_demo(str(self.tmp))
        self.assertNotEqual(reel['data'], demo['data'])
        self.assertFalse(D._dans(demo['data'], reel['data'].resolve()) or D._dans(reel['data'], demo['data'].resolve()))
        self.assertEqual(poste.DEMO_PORT, D.PORT)
        self.assertNotEqual(poste.DEMO_PORT, poste.DEFAULT_PORT)
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': str(self.tmp)}):
            poste.preparer_demo(demo, poste.DEMO_PORT)
            self.assertTrue(D.actif(load_config(str(demo['config']))))
            poste.preparer_demo(demo, poste.DEMO_PORT)                 # second lancement : rien n'est recréé
            auth = poste.bootstrap(demo, poste.DEMO_PORT)
        self.assertEqual(auth['origin'], 'http://127.0.0.1:%d' % poste.DEMO_PORT)
        self.assertFalse(reel['data'].exists())                           # l'installation réelle n'est pas créée par la démo
        lances = []

        class Faux:
            def __init__(self, args, **k):
                lances.append((args, k.get('env', {})))
                self.pid = 4242

            def poll(self):
                return None
        sup = poste.Supervisor(demo, poste.DEMO_PORT, demo=True)
        with patch.object(poste.subprocess, 'Popen', Faux), patch.object(poste, 'fcntl', None):
            sup.start()
            sup.stop_event.set()
        self.assertEqual(sup.services, ('interface', 'worker'))
        self.assertEqual([a[0][2] for a in lances], ['serve', 'worker'])     # ni veille IMAP ni passage périodique
        self.assertTrue(all(env.get('AXIORHUB_DATA_DIR') == str(demo['data'].resolve()) and env.get('AXIORHUB_DEMO') == '1' for _, env in lances))
        self.assertIsNone(sup.timer)

    def test_M9_02_dossier_de_demo_occupe_par_autre_chose_refuse(self):
        import poste
        demo = poste.paths_demo(str(self.tmp))
        demo['data'].mkdir(parents=True)
        (demo['data'] / 'config.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(Stop):
            poste.preparer_demo(demo, poste.DEMO_PORT)

    def test_M9_03_option_demo_et_liaison_incompatibles(self):
        import poste
        self.assertEqual(poste.main(['--demo', '--liaison', 'axiorhub://chatgpt/liaison?id=abc', '--home', str(self.tmp)]), 2)


# ================================================================================ M10 : raccourcis Windows et Ubuntu
class Raccourcis(unittest.TestCase):
    def test_M10_01_raccourci_windows_et_action_ubuntu(self):
        from windows import deploiement
        self.assertEqual(deploiement.SHORTCUT_DEMO, 'AxiorHub Pilote — Démo.lnk')
        source = (ROOT / 'windows' / 'deploiement.py').read_text(encoding='utf-8')
        self.assertIn("make_shortcut(dirs['menu'] / SHORTCUT_DEMO, exe, '--demo'", source)
        self.assertIn("_remove(dirs['menu'] / SHORTCUT_DEMO)", source)
        bureau = (ROOT / 'deploy' / 'poste' / 'axiorhub-pilote.desktop').read_text(encoding='utf-8')
        self.assertIn('Actions=demo;', bureau)
        self.assertIn('Exec=axiorhub-pilote --demo', bureau)
        self.assertIn('poste.py "$@"', (ROOT / 'deploy' / 'poste' / 'axiorhub-pilote').read_text(encoding='utf-8'))
        lanceur = (ROOT / 'windows' / 'lanceur.py').read_text(encoding='utf-8')
        self.assertIn("run_script(root, ['poste.py'] + argv)", lanceur)                # « --demo » transmis au poste

    def test_M10_02_installation_cree_le_second_raccourci(self):
        from windows import deploiement
        crees = []
        with patch.object(deploiement, 'make_shortcut', lambda lien, cible, args='', *a, **k: crees.append((Path(lien).name, args))):
            dirs = {'menu': Path('menu'), 'bureau': Path('bureau'), 'demarrage': Path('demarrage')}
            with patch.object(deploiement, 'shortcut_dirs', lambda: dirs):
                source = (ROOT / 'windows' / 'deploiement.py').read_text(encoding='utf-8')
                debut = source.index("        make_shortcut(dirs['menu'] / SHORTCUTS['menu']")
                fin = source.index('\n', source.index("make_shortcut(dirs['menu'] / SHORTCUT_DEMO"))
                exec(compile('\n'.join(l[8:] for l in source[debut:fin].splitlines()), 'extrait', 'exec'),
                     {'make_shortcut': deploiement.make_shortcut, 'dirs': dirs, 'SHORTCUTS': deploiement.SHORTCUTS,
                      'SHORTCUT_DEMO': deploiement.SHORTCUT_DEMO, 'exe': Path('AxiorHub Pilote.exe'), 'target': Path('.')})
        self.assertIn(('AxiorHub Pilote — Démo.lnk', '--demo'), crees)


# ================================================================================ M11 : bandeau et visite guidée
class Bandeau(Base):
    def test_M11_01_visite_guidee_clavier_et_sans_html_injecte(self):
        js = (STATIC / 'v56107-demo.js').read_text(encoding='utf-8')
        etapes = re.findall(r"\{ chemin: '([^']+)'", js)
        self.assertTrue(6 <= len(etapes) <= 8, etapes)
        self.assertEqual(etapes[:2], ['/courriels', '/dossier?id=DOS-001'])        # d'abord le courriel et son dossier
        for x in ("'Escape'", "'ArrowRight'", "'ArrowLeft'", 'Passer la visite', 'Ne plus proposer', 'X-CSRF-Token', 'corps.cle = cle()'):
            self.assertIn(x, js)
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('eval(', js)
        css = (STATIC / 'v56107-demo.css').read_text(encoding='utf-8')
        self.assertIn('prefers-color-scheme:dark', css)
        self.assertIn(':root[data-theme=dark]', css)
        self.assertIn('focus-visible', css)
        from agent.shell501 import BUNDLE
        from agent.web440 import STATIC as servis
        self.assertIn('v56107-demo.css', BUNDLE)
        self.assertIn('/static/v56107-demo.js', servis)
        self.assertIn('.demo107{', (STATIC / 'app520.css').read_text(encoding='utf-8'))

    def test_M11_04_bandeau_dans_le_menu_lateral_et_reperes_video(self):
        from agent import shell501
        shell501.set_context(self.config, {}, '/a-relire')
        menu = shell501.sidebar_html('/agent-courriel', '/a-relire')
        self.assertLess(menu.index('id="demo107"'), menu.index('</aside>'))            # dans le flux du menu : ne recouvre aucune action
        self.assertNotIn('position:fixed', (STATIC / 'v56107-demo.css').read_text(encoding='utf-8').split('.demo107-bulle{')[0])
        self.assertNotIn('Nouvelle démo', menu)
        with patch.dict(os.environ, {'AXIORHUB_DEMO_HOTE': '1'}):
            menu = shell501.sidebar_html('/agent-courriel', '/a-relire')
        self.assertIn('href="/_demo/session" data-demo107-nouvelle>Nouvelle démo</a>', menu)
        self.assertIn("setAttribute('data-demo107-passer', '')", (STATIC / 'v56107-demo.js').read_text(encoding='utf-8'))
        self.assertIn("'data-demo-dossier': m.matter || ''", (STATIC / 'v5625.js').read_text(encoding='utf-8'))

    def test_M11_05_messages_hors_ligne_et_jeton_expire(self):
        from agent.erreurs5634 import explain
        from agent.web import REASONS
        from agent.poste_session import exchange
        self.assertEqual(explain('demonstration_hors_ligne'), D.MESSAGES['demonstration_hors_ligne'])
        self.assertEqual(REASONS['demonstration_hors_ligne'], D.MESSAGES['demonstration_hors_ligne'])
        env = {'QUERY_STRING': 'jeton=perime'}
        self.assertIn('menu Démarrer', exchange(self.racine / 'ui-auth.json', env, '/agent-courriel')['body'])
        with patch.dict(os.environ, {'AXIORHUB_DEMO_HOTE': '1'}):
            corps = exchange(self.racine / 'ui-auth.json', env, '/agent-courriel')['body']
        self.assertNotIn('menu Démarrer', corps)
        self.assertIn('recommencez la démo depuis la page d’accueil', corps)

    def test_M11_02_essayer_la_demo_sur_l_accueil_d_une_installation_neuve(self):
        reel = load_config(str(self.racine / 'config.json'))
        reel.pop('demo56107')
        auth = self.tmp / 'ui-auth-poste.json'
        auth.write_text(json.dumps({'poste': True}), encoding='utf-8')
        with patch.dict(os.environ, {'AXIORHUB_AUTH_FILE': str(auth)}):
            carte = D.carte_essai_html('/agent-courriel', reel, '/parametres', {'premier': '1'})
            self.assertIn('data-demo107-lancer', carte)
            self.assertEqual(D.carte_essai_html('/agent-courriel', reel, '/parametres', {}), '')
            self.assertEqual(D.carte_essai_html('/agent-courriel', self.config, '/parametres', {'premier': '1'}), '')
        auth.write_text(json.dumps({'poste': False}), encoding='utf-8')
        with patch.dict(os.environ, {'AXIORHUB_AUTH_FILE': str(auth)}):
            self.assertEqual(D.carte_essai_html('/agent-courriel', reel, '/parametres', {'premier': '1'}), '')   # serveur : jamais

    def test_M11_03_lancer_depuis_une_installation_reelle_seulement_sur_le_poste(self):
        lances = []
        reel = load_config(str(self.racine / 'config.json'))
        reel.pop('demo56107')

        class FauxDesk:
            c = reel
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': 'http://127.0.0.1:8769', 'HTTP_X_CSRF_TOKEN': 'jeton', 'CONTENT_TYPE': 'application/json',
               'wsgi.input': io.BytesIO(b'{"cle": "cle-lancer-0001"}'), 'CONTENT_LENGTH': '26'}
        auth = {'origin': 'http://127.0.0.1:8769', 'csrf': 'jeton', 'poste': True}
        import subprocess
        with patch.object(subprocess, 'Popen', lambda args, **k: lances.append(args)):
            r = D.api(dict(env), FauxDesk, auth, '/agent-courriel', 'm56107/demo/lancer', {}, 'POST')
            self.assertEqual(r['status'], '200 OK', r['body'])
            self.assertEqual(lances[0][-1], '--demo')
            env['wsgi.input'] = io.BytesIO(b'{"cle": "cle-lancer-0002"}')
            r = D.api(dict(env), FauxDesk, {**auth, 'poste': False}, '/agent-courriel', 'm56107/demo/lancer', {}, 'POST')
            self.assertEqual(json.loads(r['body'])['error'], 'lancement_demo_indisponible')
        self.assertEqual(len(lances), 1)
        r = D.api({'REQUEST_METHOD': 'GET'}, FauxDesk, auth, '/agent-courriel', 'm56107/demo/etat', {}, 'GET')
        self.assertTrue(r['status'].startswith('404'))                             # hors démo : 404 neutre


if __name__ == '__main__':
    unittest.main()

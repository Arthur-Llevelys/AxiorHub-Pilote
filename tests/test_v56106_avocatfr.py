"""5.6.106, lot P : réglage prêt à l'emploi pour une adresse @avocat.fr (messagerie du CNB) — IMAP imaps.avocat.fr:993 et SMTP
smtps.avocat.fr:465 en SSL/TLS, identifiant = adresse complète, mot de passe applicatif.

Faux IMAP (banc ``fake_imap440`` au niveau de ``imaplib.IMAP4_SSL``) et faux SMTP (au niveau de ``smtplib.SMTP_SSL`` / ``smtplib.SMTP``) :
aucune connexion réseau. Données fictives seulement (DURAND, MARTIN, KERVILAC, ORLANDEC, ZORVAL, Valbrune, example.test) ; mot de
passe applicatif fictif."""
import imaplib
import io
import json
from pathlib import Path
import smtplib
import socket
import ssl
import types
import unittest
from unittest.mock import patch

from agent import avocatfr56106 as af
from agent.common import Stop, private_json, read_secret
from agent.desk import Desk
from agent.standalone_auth import allowed
from fake_imap440 import FakeIMAP
import test_agent as fixtures

D = '@' + 'avocat.fr'                    # domaine assemblé à l'exécution : aucune adresse littérale dans le dépôt public
ORIGINE, JETON = 'https://cabinet.example.test', 'jeton-csrf-fictif'
ADRESSE = 'claire.durand' + D
MDP = 'Fictif-Applicatif-0042-Valbrune'      # mot de passe applicatif fictif

LISTE_RFC = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Drafts) "/" "Drafts"', b'(\\HasNoChildren \\Sent) "/" "Sent"',
             b'(\\HasNoChildren \\Junk) "/" "Junk"', b'(\\HasNoChildren \\Trash) "/" "Trash"', b'(\\HasNoChildren \\Archive) "/" "Archive"',
             b'(\\HasNoChildren) "/" "Dossiers clients"']
LISTE_NOMS = [b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren) "." "INBOX.Brouillons"', b'(\\HasNoChildren) "." "INBOX.Envoy&AOk-s"',
              b'(\\HasNoChildren) "." "INBOX.Corbeille"', b'(\\HasNoChildren) "." "INBOX.Spam"', b'(\\HasNoChildren) "." "INBOX.Archives"']
LISTE_SANS_BROUILLONS = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "Sent"']


class FauxIMAP(FakeIMAP):
    """Serveur IMAP avocat.fr en mémoire : connexions notées, LOGIN réglable, LIST réglable."""
    liste = LISTE_RFC
    refus = None
    panne = None
    connexions, identifiants = [], []

    @classmethod
    def preparer(cls, liste=LISTE_RFC, refus=None, panne=None):
        cls.reset()
        cls.liste, cls.refus, cls.panne, cls.connexions, cls.identifiants = liste, refus, panne, [], []

    def __init__(self, host, port=993, ssl_context=None, timeout=0):
        if FauxIMAP.panne is not None:
            raise FauxIMAP.panne
        super().__init__(host, port, ssl_context, timeout)
        FauxIMAP.connexions.append((host, port))

    def login(self, user, password):
        FauxIMAP.identifiants.append((user, password))
        FauxIMAP.log.append(('LOGIN', user))
        if FauxIMAP.refus:
            raise imaplib.IMAP4.error(FauxIMAP.refus)
        return 'OK', [b'LOGIN completed']

    def list(self):
        FauxIMAP.log.append(('LIST', ''))
        return 'OK', list(FauxIMAP.liste)


class FauxSMTP:
    """Serveur SMTP avocat.fr en mémoire : chaque commande est notée ; toute commande d'envoi est notée comme telle."""
    journal, connexions, identifiants = [], [], []
    refus = None
    panne = None
    auth = True

    @classmethod
    def preparer(cls, refus=None, panne=None, auth=True):
        cls.journal, cls.connexions, cls.identifiants, cls.refus, cls.panne, cls.auth = [], [], [], refus, panne, auth

    def __init__(self, host='', port=0, local_hostname=None, timeout=None, source_address=None, context=None, **kw):
        if FauxSMTP.panne is not None:
            raise FauxSMTP.panne
        FauxSMTP.connexions.append((type(self).__name__, host, port, context is not None))

    def ehlo(self, name=''):
        FauxSMTP.journal.append('EHLO')
        return 250, b'smtps.avocat.fr'

    def has_extn(self, nom):
        return nom.lower() == 'auth' and FauxSMTP.auth

    def login(self, user, password, **kw):
        FauxSMTP.journal.append('AUTH')
        FauxSMTP.identifiants.append((user, password))
        if FauxSMTP.refus:
            raise smtplib.SMTPAuthenticationError(535, b'5.7.8 Authentication credentials invalid')
        return 235, b'Authentication successful'

    def quit(self):
        FauxSMTP.journal.append('QUIT')
        return 221, b'bye'

    def close(self):
        pass

    def _envoi(self, nom):
        FauxSMTP.journal.append(nom)
        return {}

    def sendmail(self, *a, **k):
        return self._envoi('SENDMAIL')

    def send_message(self, *a, **k):
        return self._envoi('SEND_MESSAGE')

    def mail(self, *a, **k):
        return self._envoi('MAIL')

    def rcpt(self, *a, **k):
        return self._envoi('RCPT')

    def data(self, *a, **k):
        return self._envoi('DATA')


class FauxSMTPClair(FauxSMTP):
    pass


ENVOIS = ('MAIL', 'RCPT', 'DATA', 'SENDMAIL', 'SEND_MESSAGE')


def envois():
    return [x for x in FauxSMTP.journal if x in ENVOIS]


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.env = {'axiorhub.config_path': str(self.config)}
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        FauxIMAP.preparer()
        FauxSMTP.preparer()
        for p in (patch('agent.mailbox.imaplib.IMAP4_SSL', FauxIMAP), patch('agent.avocatfr56106.smtplib.SMTP_SSL', FauxSMTP),
                  patch('agent.avocatfr56106.smtplib.SMTP', FauxSMTPClair), patch('agent.common.HTTP.request', side_effect=AssertionError('réseau'))):
            p.start()
            self.addCleanup(p.stop)

    def revision(self):
        return json.loads(self.config.read_text(encoding='utf-8')).get('config_revision567', 0)

    def appel(self, route, corps, role='administrateur', jeton=JETON, module=af):
        brut = json.dumps(corps).encode()
        env = {**self.env, 'HTTP_X_AXIORHUB_USER': 'admin@example.test', 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': jeton,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)}
        out = module.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56106/avocatfr/' + route, {}, 'POST')
        return out['status'], json.loads(out['body']), out

    def appel_get(self, route):
        env = {**self.env, 'HTTP_X_AXIORHUB_USER': 'admin@example.test', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}
        out = af.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56106/avocatfr/' + route, {}, 'GET')
        return out['status'], json.loads(out['body'])

    def enregistrer(self, **kw):
        corps = {'adresse': ADRESSE, 'mot_de_passe': MDP, 'revision': self.revision(), 'remplacer': True, 'cle': 'cle-enregistrer-0001'}
        corps.update(kw)
        return self.appel('enregistrer', corps)

    def essai_route(self, **kw):
        corps = {'adresse': ADRESSE, 'mot_de_passe': MDP, 'cle': 'cle-tester-000001'}
        corps.update(kw)
        return self.appel('tester', corps)

    def tout_le_stockage(self):
        """Configuration, base (dump complet) et journaux : là où le mot de passe ne doit jamais apparaître."""
        textes = [self.config.read_text(encoding='utf-8'), '\n'.join(self.desk.db.iterdump())]
        for chemin in Path(self.desk.c['state_dir']).rglob('*'):
            if chemin.is_file() and chemin.suffix in ('.json', '.log', '.jsonl', '.txt'):
                textes.append(chemin.read_text(encoding='utf-8', errors='replace'))
        return '\n'.join(textes)


def mutant(remplacements):
    """Copie en mémoire du module, garde-fou retiré (jamais de code altéré sur le disque)."""
    source = Path(af.__file__).read_text(encoding='utf-8')
    for avant, apres in remplacements:
        assert avant in source, avant
        source = source.replace(avant, apres)
    module = types.ModuleType('agent.avocatfr56106_mutant')
    module.__package__ = 'agent'
    module.__file__ = af.__file__
    exec(compile(source, af.__file__, 'exec'), module.__dict__)
    return module


class Proposition(Base):
    def test_P1_proposition_d_adresse(self):
        cas = (('Claire DURAND', 'claire.durand' + D), ('DURAND Claire', 'claire.durand' + D),
               ('Hélène MARTIN', 'helene.martin' + D),                       # accents
               ('Jean-Éric BERNARD', 'jean-eric.bernard' + D),                # prénom composé
               ('Anne-Laure ZORVAL KERVILAC', 'anne-laure.zorval-kervilac' + D),   # nom composé (espace → tiret)
               ('Anne ZORVAL-KERVILAC', 'anne.zorval-kervilac' + D),
               ('Paul de KERVILAC', 'paul.de-kervilac' + D),                  # particule
               ('Chloé de la Valbrune', 'chloe.de-la-valbrune' + D),          # sans capitales : premier mot = prénom
               ("Marc d'ORLANDEC", 'marc.dorlandec' + D),                     # apostrophe supprimée
               ('Marc d’ORLANDEC', 'marc.dorlandec' + D),
               ('Œdipe MARTIN', 'oedipe.martin' + D), ('François  Kervilac ', 'francois.kervilac' + D),
               ('claire.durand@example.test', 'claire.durand' + D),           # identifiant prénom.nom repris
               ('DURAND', ''), ('admin@example.test', ''), ('', ''))
        for nom, attendu in cas:
            with self.subTest(nom=nom):
                self.assertEqual(af.proposer_adresse(nom), attendu)

    def test_P1_proposition_affichee_modifiable_et_documentee(self):
        env = {**self.env, 'axiorhub.user': {'name': 'Hélène de KERVILAC'}}
        page = af.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', env)
        self.assertIn('value="helene.de-kervilac' + D + '"', page)
        self.assertIn('Proposition déduite de votre nom', page)
        self.assertIn('corrigez-la', page)
        self.assertNotIn('readonly', page.split('name="adresse"')[1].split('>')[0])     # l'adresse reste modifiable
        # nom Nextcloud (en-tête) puis annuaire des membres
        self.assertEqual(af.nom_personne(self.desk, {'HTTP_X_AXIORHUB_NAME': 'Zoé ZORVAL'}, 'zz'), 'Zoé ZORVAL')
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS membres5650(owner TEXT PRIMARY KEY, name TEXT, role TEXT, first_seen TEXT, last_seen TEXT)')
        self.desk.db.execute("INSERT OR REPLACE INTO membres5650 VALUES('pm','Camille MARTIN','avocat','','')")
        self.assertEqual(af.nom_personne(self.desk, {}, 'pm'), 'Camille MARTIN')
        self.assertEqual(af.nom_personne(self.desk, {}, 'inconnu'), '')

    def test_P1_page_preglage_lecture_seule_aide_et_lien(self):
        page = af.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env)
        for attendu in ('imaps.avocat.fr', '>993<', 'smtps.avocat.fr', '>465<', 'SSL/TLS (TLS implicite)', 'Réglages du CNB (lecture seule)',
                        'Modifier les réglages avancés', 'Mot de passe applicatif', 'type="password"', 'pas le mot de passe habituel',
                        'href="https://auth.avocat.fr/deviceaccounts" target="_blank" rel="noopener noreferrer"', 'Clé Avocat', 'e-dentitas',
                        'Ajouter un compte', 'Créer le compte', 'affiché qu’une fois', 'data-api="m56106/avocatfr/tester"', 'Tester'):
            with self.subTest(attendu=attendu):
                self.assertIn(attendu, page)
        aide = page.split('Mot de passe applicatif</label>')[1].split('</span>')[0]
        self.assertEqual(aide.count('<br>'), 2)                                 # aide en trois lignes
        self.assertIn('<details class="gm97-guide af106-avances">', page)       # réglages avancés repliés par défaut
        self.assertNotIn('name="avance" checked', page)
        # non administrateur : explication, aucun formulaire
        page = af.section_html(self.desk, '/p', 'pm', 'avocat', self.env)
        self.assertNotIn('<form', page)
        self.assertIn('demandez à l’administrateur', page)

    def test_P1_serveurs_figes_sauf_reglages_avances(self):
        self.assertEqual(af.reglages({}), {'imap': {'host': 'imaps.avocat.fr', 'port': 993, 'security': 'ssl'},
                                           'smtp': {'host': 'smtps.avocat.fr', 'port': 465, 'security': 'ssl'}, 'avance': False})
        ignore = af.reglages({'imap_host': 'mail.example.test', 'imap_port': '143', 'smtp_host': 'smtp.example.test'})
        self.assertEqual((ignore['imap']['host'], ignore['smtp']['host'], ignore['avance']), ('imaps.avocat.fr', 'smtps.avocat.fr', False))
        avance = af.reglages({'avance': True, 'imap_host': 'imap.example.test', 'imap_port': '143', 'imap_security': 'starttls',
                              'smtp_host': 'smtp.example.test', 'smtp_port': 587, 'smtp_security': 'starttls'})
        self.assertEqual(avance['imap'], {'host': 'imap.example.test', 'port': 143, 'security': 'starttls'})
        self.assertEqual(avance['smtp'], {'host': 'smtp.example.test', 'port': 587, 'security': 'starttls'})
        for faux in ({'imap_host': 'a b'}, {'imap_port': '0'}, {'smtp_port': '70000'}, {'smtp_security': 'aucune'}, {'imap_host': 'x/y'}):
            with self.subTest(faux=faux), self.assertRaisesRegex(Stop, 'avocatfr_reglage_avance_invalide'):
                af.reglages({'avance': True, **faux})

    def test_P1_domaine_different_avertissement_pas_refus(self):
        status, body, _ = self.essai_route(adresse='claire.durand@example.test')
        self.assertEqual(status, '200 OK')
        self.assertNotIn('error', body)
        self.assertIn('ne se termine pas par @avocat.fr', body['avertissement'])
        self.assertIn('ne se termine pas par @avocat.fr', body['message'])
        self.assertEqual(FauxIMAP.identifiants[0][0], 'claire.durand@example.test')
        status, body, _ = self.enregistrer(adresse='claire.durand@example.test')
        self.assertEqual(status, '200 OK', body)
        self.assertTrue(body['avertissement'])
        self.assertEqual(af.avertissement_domaine(ADRESSE), '')
        self.assertEqual(self.essai_route(adresse='pas une adresse')[1]['error'], 'avocatfr_adresse_invalide')


class Essai(Base):
    def test_P1_test_reussi_imap_login_list_puis_smtp_ehlo_auth(self):
        status, body, brut = self.essai_route()
        self.assertEqual(status, '200 OK', body)
        self.assertEqual(dict(brut['headers'])['Cache-Control'], 'private, no-store')
        self.assertEqual(FauxIMAP.connexions, [('imaps.avocat.fr', 993)])
        self.assertEqual(FauxIMAP.identifiants, [(ADRESSE, MDP)])                # identifiant = adresse complète
        self.assertEqual([x[0] for x in FauxIMAP.log], ['LOGIN', 'LIST'])        # ni SELECT, ni APPEND, ni EXAMINE
        self.assertEqual(FauxSMTP.connexions, [('FauxSMTP', 'smtps.avocat.fr', 465, True)])   # TLS implicite, contexte vérifié
        self.assertEqual(FauxSMTP.journal, ['EHLO', 'AUTH', 'QUIT'])
        self.assertEqual(FauxSMTP.identifiants, [(ADRESSE, MDP)])
        self.assertIn('Aucun message n’a été envoyé', body['message'])
        self.assertEqual(body['dossiers'], {'drafts': 'Drafts', 'sent': 'Sent', 'junk': 'Junk', 'trash': 'Trash', 'archive': 'Archive'})
        essai = af.dernier_essai(self.desk, ADRESSE)
        self.assertEqual((essai['resultat'], essai['reglages']['imap']['host']), ('ok', 'imaps.avocat.fr'))
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['mail']['host'], 'mail.example.test')   # « Tester » n'enregistre rien

    def test_P3_aucun_message_envoye_pendant_le_test_ni_a_l_enregistrement(self):
        self.essai_route()
        self.enregistrer()
        self.assertEqual(envois(), [])
        self.assertTrue(FauxSMTP.journal.count('AUTH') == 2)
        # mutation (copie en mémoire) : un test qui « vérifierait » par un envoi serait détecté
        m = mutant([("        client.login(adresse, mot_de_passe)\n        return True",
                     "        client.login(adresse, mot_de_passe)\n        client.sendmail(adresse, [adresse], 'x')\n        return True")])
        FauxSMTP.preparer()
        m.essayer_smtp(af.SMTP, ADRESSE, MDP)
        self.assertEqual(envois(), ['SENDMAIL'])

    def test_P1_mot_de_passe_refuse_imap(self):
        FauxIMAP.preparer(refus=b'[AUTHENTICATIONFAILED] Authentication failed.')
        status, body, _ = self.essai_route(cle='cle-refus-imap-01')
        self.assertEqual((status, body['error'], body['etape'], body['resultat']), ('200 OK', 'avocatfr_mot_de_passe_refuse', 'imap', 'echec'))
        self.assertIn('Vérifiez le mot de passe applicatif', body['message'])
        self.assertEqual(FauxSMTP.connexions, [])                                # SMTP jamais contacté après un refus IMAP
        essai = af.dernier_essai(self.desk, ADRESSE)
        self.assertEqual((essai['resultat'], essai['etape'], essai['code'], essai['motif']), ('echec', 'imap', 'avocatfr_mot_de_passe_refuse', 'AUTHENTICATIONFAILED'))
        statut, phrase = af.etat_texte(self.desk)
        self.assertEqual(statut, 'echec')
        self.assertIn('Vérifiez le mot de passe applicatif', phrase)

    def test_P1_mot_de_passe_refuse_smtp(self):
        FauxSMTP.preparer(refus=True)
        status, body, _ = self.essai_route()
        self.assertEqual((body['error'], body['etape']), ('avocatfr_mot_de_passe_refuse', 'smtp'))
        self.assertIn('Vérifiez le mot de passe applicatif', body['message'])
        self.assertEqual(envois(), [])
        status, body, _ = self.enregistrer()
        self.assertEqual((status, body['error']), ('400 Bad Request', 'avocatfr_mot_de_passe_refuse'))
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['mail']['host'], 'mail.example.test')   # rien enregistré

    def test_P1_erreurs_traduites_serveur_injoignable_et_certificat(self):
        certificat = ssl.SSLCertVerificationError(1, 'certificate verify failed')
        cas = (('imap', socket.gaierror(-2, 'Name or service not known'), None, 'avocatfr_serveur_injoignable'),
               ('imap', ConnectionRefusedError(), None, 'avocatfr_serveur_injoignable'),
               ('imap', TimeoutError(), None, 'avocatfr_serveur_injoignable'),
               ('imap', certificat, None, 'avocatfr_certificat_invalide'),
               ('smtp', None, socket.gaierror(-2, 'Name or service not known'), 'avocatfr_serveur_injoignable'),
               ('smtp', None, ConnectionRefusedError(), 'avocatfr_serveur_injoignable'),
               ('smtp', None, socket.timeout(), 'avocatfr_serveur_injoignable'),
               ('smtp', None, smtplib.SMTPServerDisconnected('perdu'), 'avocatfr_serveur_injoignable'),
               ('smtp', None, certificat, 'avocatfr_certificat_invalide'),
               ('smtp', None, ssl.SSLError(1, 'WRONG_VERSION_NUMBER'), 'avocatfr_tls_echoue'))
        for n, (etape, panne_imap, panne_smtp, code) in enumerate(cas):
            with self.subTest(etape=etape, code=code, n=n):
                FauxIMAP.preparer(panne=panne_imap)
                FauxSMTP.preparer(panne=panne_smtp)
                body = self.essai_route()[1]
                self.assertEqual((body['error'], body['etape']), (code, etape))
                self.assertEqual(body['message'], af.MESSAGES[code])
        self.assertIn('injoignable', af.MESSAGES['avocatfr_serveur_injoignable'])
        self.assertIn('certificat', af.MESSAGES['avocatfr_certificat_invalide'])
        FauxIMAP.preparer()
        FauxSMTP.preparer(auth=False)
        self.assertEqual(self.essai_route()[1]['error'], 'avocatfr_smtp_auth_indisponible')

    def test_P1_dossiers_speciaux_rfc6154_puis_noms_usuels(self):
        self.assertEqual(af.dossiers_speciaux([('INBOX', []), ('Drafts', ['\\drafts']), ('Brouillons', []), ('A', ['\\archive'])]),
                         {'drafts': 'Drafts', 'archive': 'A'})                  # l'attribut l'emporte sur le nom
        FauxIMAP.preparer(LISTE_NOMS)
        body = self.essai_route()[1]
        self.assertEqual(body['dossiers'], {'drafts': 'INBOX.Brouillons', 'sent': 'INBOX.Envoyés', 'junk': 'INBOX.Spam',
                                            'trash': 'INBOX.Corbeille', 'archive': 'INBOX.Archives'})
        self.assertEqual(af.dossiers_speciaux([('X', ['\\noselect', '\\drafts']), ('Drafts', ['\\noselect']), ('INBOX', [])]), {})
        FauxIMAP.preparer(LISTE_SANS_BROUILLONS)
        self.assertEqual(self.essai_route()[1]['error'], 'avocatfr_brouillons_introuvables')


class Enregistrement(Base):
    def test_P1_enregistrement_imap_smtp_et_dossiers(self):
        status, body, _ = self.enregistrer()
        self.assertEqual(status, '200 OK', body)
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        m, s = cfg['mail'], cfg['smtp']
        self.assertEqual((m['host'], m['port'], m['security'], m['username'], m['inbox'], m['drafts'], m['sent'], m['from_address']),
                         ('imaps.avocat.fr', 993, 'ssl', ADRESSE, 'INBOX', 'Drafts', 'Sent', ADRESSE))
        self.assertEqual((s['enabled'], s['host'], s['port'], s['security'], s['username'], s['from_address']),
                         (True, 'smtps.avocat.fr', 465, 'ssl', ADRESSE, ADRESSE))
        from agent.send490 import smtp_config
        self.assertEqual((smtp_config(self.desk)['host'], smtp_config(self.desk)['port']), ('smtps.avocat.fr', 465))   # envoi existant : réglage repris
        statut, phrase = af.etat_texte(self.desk)
        self.assertEqual(statut, 'ok', phrase)
        self.assertIn(ADRESSE, af.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env))

    def test_P1_secret_absent_du_journal_et_de_la_configuration(self):
        self.essai_route()
        FauxIMAP.preparer(refus=b'[AUTHENTICATIONFAILED] Authentication failed.')
        self.essai_route(cle='cle-refus-000002')
        FauxIMAP.preparer()
        status, body, brut = self.enregistrer()
        self.assertEqual(status, '200 OK', body)
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        stockage = self.tout_le_stockage()
        self.assertNotIn(MDP, stockage)                                         # configuration, base (essais, reçus, audit), journaux
        self.assertNotIn(MDP, brut['body'])
        for section in ('mail', 'smtp'):
            coffre = Path(cfg[section]['password_file'])
            self.assertTrue(coffre.name.endswith('.secret'))
            self.assertNotIn(MDP, coffre.read_text(encoding='utf-8'))           # chiffré dans le coffre
            self.assertEqual(read_secret(str(coffre)), MDP)
        page = af.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env)
        self.assertNotIn(MDP, page)
        # le détecteur voit bien un secret écrit en clair (contre-épreuve sur une copie de la configuration)
        copie = json.loads(self.config.read_text(encoding='utf-8'))
        copie['smtp']['password_file'] = MDP
        self.assertIn(MDP, json.dumps(copie))

    def test_P1_retest_avec_le_mot_de_passe_deja_enregistre(self):
        premier = self.enregistrer()
        self.assertEqual(premier[0], '200 OK')
        FauxIMAP.preparer()
        FauxSMTP.preparer()
        status, body, _ = self.essai_route(mot_de_passe='', cle='cle-retest-000001')
        self.assertEqual(status, '200 OK', body)
        self.assertEqual((FauxIMAP.identifiants, FauxSMTP.identifiants), ([(ADRESSE, MDP)], [(ADRESSE, MDP)]))
        self.assertEqual(self.essai_route(adresse='camille.martin' + D, mot_de_passe='', cle='cle-retest-000002')[1]['error'],
                         'avocatfr_mot_de_passe_requis')                         # jamais le secret d'un autre compte
        self.assertIn('Laisser vide pour retester', af.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env))

    def test_P1_remplacer_une_autre_messagerie_demande_confirmation(self):
        status, body, _ = self.enregistrer(remplacer=False)
        self.assertEqual((status, body['error']), ('400 Bad Request', 'messagerie_existante_remplacer'))
        self.assertEqual(FauxIMAP.connexions, [])
        self.assertIn('name="remplacer"', af.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env))

    def test_P1_revision_perimee_409(self):
        status, body, _ = self.enregistrer(revision=self.revision() + 7)
        self.assertEqual((status, body['error']), ('409 Conflict', 'configuration_modifiee_recharger'))
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['mail']['host'], 'mail.example.test')

    def test_P1_idempotence(self):
        premier = self.enregistrer()
        self.assertEqual(premier[0], '200 OK')
        essais = self.desk.db.execute('SELECT COUNT(*) FROM avocatfr56106_essais').fetchone()[0]
        rejoue = self.enregistrer(revision=premier[1]['revision'] - 1)      # même clé : reçu rendu, rien refait
        self.assertEqual(rejoue[1], premier[1])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM avocatfr56106_essais').fetchone()[0], essais)
        autre = self.enregistrer(adresse='camille.martin' + D)
        self.assertEqual((autre[0], autre[1]['error']), ('409 Conflict', 'cle_idempotence_deja_utilisee'))
        recus = '\n'.join(r[0] for r in self.desk.db.execute('SELECT empreinte || reponse FROM assistant56103_recus'))
        self.assertNotIn(MDP, recus)
        self.assertEqual(self.essai_route(cle='')[1]['error'], 'cle_idempotence_requise')


class Droits(Base):
    def test_P1_droits_csrf_et_routes(self):
        for route in ('tester', 'enregistrer'):
            status, body, _ = self.appel(route, {'adresse': ADRESSE, 'mot_de_passe': MDP, 'cle': 'cle-avocat-0001'}, role='avocat')
            self.assertEqual((status, body['error']), ('403 Forbidden', 'role_insuffisant'))
        status, body, _ = self.appel('tester', {'adresse': ADRESSE, 'mot_de_passe': MDP, 'cle': 'cle-csrf-0000001'}, jeton='faux')
        self.assertEqual(body['error'], 'origine_ou_csrf_refuse')
        self.assertEqual((FauxIMAP.connexions, FauxSMTP.connexions), ([], []))
        self.assertEqual(self.appel('inconnue', {})[0], '404 Not Found')
        self.assertTrue(allowed('administrateur', 'POST', '/api440/m56106/avocatfr/enregistrer'))
        self.assertTrue(allowed('administrateur', 'POST', '/api440/m56106/avocatfr/tester'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56106/avocatfr/tester'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56106/avocatfr/enregistrer'))
        self.assertFalse(af.allowed('administrateur', 'GET', 'm56106/avocatfr/tester'))
        self.assertEqual(self.appel_get('tester')[0], '404 Not Found')                # GET refusé par le module lui-même
        # mutation (copie en mémoire) : sans le contrôle du rôle, un avocat aurait modifié la messagerie du cabinet
        m = mutant([("    if role != 'administrateur':\n        raise Stop('role_insuffisant')       # contrôle avant tout corps\n", ''),
                    ("    if role != 'administrateur':\n        raise Stop('role_insuffisant')\n    adresse = normaliser_adresse(data.get('adresse'))\n    reglage = reglages(data)\n    revision",
                     "    adresse = normaliser_adresse(data.get('adresse'))\n    reglage = reglages(data)\n    revision")])
        status, body, _ = self.appel('enregistrer', {'adresse': ADRESSE, 'mot_de_passe': MDP, 'revision': self.revision(), 'remplacer': True,
                                                     'cle': 'cle-avocat-0002'}, role='avocat', module=m)
        self.assertEqual(status, '200 OK', body)                                  # le garde-fou retiré, l'avocat passe : le test ci-dessus le détecte

    def test_P1_monte_dans_web440_parametres_et_guide(self):
        from agent import guide5631, web440
        self.assertIn("m56106/avocatfr/", Path(web440.__file__).read_text(encoding='utf-8'))
        out = web440.api({**self.env, 'HTTP_X_AXIORHUB_USER': 'admin@example.test', 'HTTP_X_AXIORHUB_ROLE': 'administrateur'}, self.desk,
                         {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56106/avocatfr/inconnue', {}, 'POST')
        self.assertEqual(out['status'], '404 Not Found')
        self.assertIs(guide5631._fonction_carte('avocatfr'), af.carte_guide)
        carte = af.carte_guide(self.desk, {**self.env, 'axiorhub.user': {'name': 'Claire DURAND'}}, '/p', admin=True, owner='admin@example.test')
        self.assertIn('value="claire.durand' + D + '"', carte)
        self.assertIn('Adresse @avocat.fr (CNB)', carte)
        etape = guide5631._cartes_etape(self.desk, {**self.env, 'axiorhub.user': {'name': 'Claire DURAND'}}, '/p', 'messagerie', {}, 'admin@example.test', True)
        self.assertLess(etape.index('avocatfr56106'), etape.index('<h3>', etape.index('</section>', etape.index('avocatfr56106'))))   # en tête de l'étape
        self.assertTrue(self.desk.db.execute("SELECT name FROM sqlite_master WHERE name='avocatfr56106_essais'").fetchone())   # créée au démarrage


if __name__ == '__main__':
    unittest.main()

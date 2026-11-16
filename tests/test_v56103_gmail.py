"""5.6.103, lot G : Gmail simple — IMAP avec mot de passe d'application (préréglage, Brouillons par l'attribut \\Drafts, refus de Google en
clair, mot de passe jamais en clair) et application OAuth « Interne » Workspace sur le connecteur Gmail existant.

Faux serveur Gmail au niveau de ``imaplib.IMAP4_SSL`` (banc ``fake_imap440`` étendu) : aucune connexion réseau. Données fictives seulement
(DURAND, KERVILAC, example.test) ; mots de passe et secrets fictifs."""
from email.message import EmailMessage
import imaplib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import assistant56103 as asst, gmail_simple56103 as gs
from agent.common import Stop, private_json, read_secret
from agent.desk import Desk
from agent.standalone_auth import allowed
from fake_imap440 import FakeIMAP
import test_agent as fixtures

ORIGINE, JETON = 'https://cabinet.example.test', 'jeton-csrf-fictif'
ADRESSE = 'durand.cabinet@example.test'
MDP = 'abcd efgh ijkl mnop'          # mot de passe d'application fictif, tel que Google l'affiche (4 groupes de 4)
MDP_NET = 'abcdefghijklmnop'
CLIENT = '123456789012-fictif0abcdefgh1234' + '.apps.' + 'googleusercontent.com'   # valeurs fictives, assemblées pour ne pas ressembler à un vrai identifiant aux yeux des analyses de secrets
SECRET_CLIENT = 'GOC' + 'SPX-' + 'secret-fictif-kervilac-0001'
RETOUR = 'https://pilote.example.test/p/api440/m5689/oauth/google/callback'

LISTE_FR = [b'(\\HasChildren \\Noselect) "/" "[Gmail]"', b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Drafts"',                       # libellé personnel nommé « Drafts » : jamais retenu (pas d'attribut)
            b'(\\All \\HasNoChildren) "/" "[Gmail]/Tous les messages"', b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Brouillons"',
            b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Messages envoy&AOk-s"', b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
            b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Corbeille"', b'(\\Flagged \\HasNoChildren) "/" "[Gmail]/Suivis"']
LISTE_DE = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasChildren \\Noselect) "/" "[Google Mail]"',
            b'(\\Drafts \\HasNoChildren) "/" "[Google Mail]/Entw&APw-rfe"', b'(\\HasNoChildren \\Sent) "/" "[Google Mail]/Gesendet"']
LISTE_EN = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Drafts"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"']
LISTE_SANS = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "[Gmail]/Brouillons"']   # le nom seul ne suffit jamais


class FauxGmail(FakeIMAP):
    """Gmail en mémoire : préréglage noté, LOGIN réglable (refus avec le texte de Google), LIST avec attributs RFC 6154."""
    liste = LISTE_FR
    refus = None
    connexions, identifiants = [], []

    @classmethod
    def preparer(cls, liste=LISTE_FR, refus=None):
        cls.reset()
        noms = [l.rsplit(b' "', 1)[1].rstrip(b'"').decode() for l in liste]
        cls.folders = {n: {} for n in noms if n not in ('[Gmail]', '[Google Mail]')}
        cls.validity = {k: '11' for k in cls.folders}
        cls.next_uid = {k: 1 for k in cls.folders}
        cls.liste, cls.refus, cls.connexions, cls.identifiants = liste, refus, [], []

    def __init__(self, host, port=993, ssl_context=None, timeout=0):
        super().__init__(host, port, ssl_context, timeout)
        FauxGmail.connexions.append((host, port, timeout))

    def login(self, user, password):
        FauxGmail.identifiants.append((user, password))
        if FauxGmail.refus:
            raise imaplib.IMAP4.error(FauxGmail.refus)
        return 'OK', [b'durand authenticated (Success)']

    def list(self):
        return 'OK', list(FauxGmail.liste)


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
        FauxGmail.preparer()
        for p in (patch('agent.mailbox.imaplib.IMAP4_SSL', FauxGmail), patch('agent.common.HTTP.request', side_effect=AssertionError('réseau'))):
            p.start()
            self.addCleanup(p.stop)

    def revision(self):
        return json.loads(self.config.read_text(encoding='utf-8')).get('config_revision567', 0)

    def appel(self, route, corps, role='administrateur', origine=ORIGINE, jeton=JETON):
        brut = json.dumps(corps).encode()
        env = {**self.env, 'HTTP_X_AXIORHUB_USER': 'admin@example.test', 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': origine, 'HTTP_X_CSRF_TOKEN': jeton,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(brut)), 'wsgi.input': io.BytesIO(brut)}
        out = gs.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm56103/gmail/' + route, {}, 'POST')
        return out['status'], json.loads(out['body']), out

    def enregistrer(self, **kw):
        corps = {'adresse': ADRESSE, 'mot_de_passe': MDP, 'revision': self.revision(), 'remplacer': True, 'cle': 'cle-enregistrer-0001'}
        corps.update(kw)
        return self.appel('enregistrer', corps)

    def tout_le_stockage(self):
        """Configuration, base (dump complet) et journal : là où un mot de passe ne doit jamais apparaître."""
        textes = [self.config.read_text(encoding='utf-8'), '\n'.join(self.desk.db.iterdump())]
        for chemin in Path(self.desk.c['state_dir']).rglob('*'):
            if chemin.is_file() and chemin.suffix in ('.json', '.log', '.jsonl', '.txt'):
                textes.append(chemin.read_text(encoding='utf-8', errors='replace'))
        return '\n'.join(textes)


class Preglage(Base):
    def test_G01_preglage_gmail_imap(self):
        cfg = gs.config_preset(ADRESSE)
        self.assertEqual((cfg['host'], cfg['port'], cfg['security'], cfg['inbox'], cfg['username']), ('imap.gmail.com', 993, 'ssl', 'INBOX', ADRESSE))
        out = gs.essayer(ADRESSE, MDP_NET)
        self.assertEqual(FauxGmail.connexions[0][:2], ('imap.gmail.com', 993))
        self.assertEqual(FauxGmail.identifiants, [(ADRESSE, MDP_NET)])
        self.assertEqual(out['dossiers']['drafts'], '[Gmail]/Brouillons')
        self.assertIn(('EXAMINE', '[Gmail]/Brouillons'), FauxGmail.log)          # Brouillons ouverts en lecture seule, jamais en écriture
        self.assertFalse([x for x in FauxGmail.log if x[0] in ('SELECT', 'APPEND')])

    def test_G02_brouillons_localises_par_attribut(self):
        for liste, attendu, envoyes in ((LISTE_FR, '[Gmail]/Brouillons', '[Gmail]/Messages envoyés'), (LISTE_DE, '[Google Mail]/Entwürfe', '[Google Mail]/Gesendet'),
                                        (LISTE_EN, '[Gmail]/Drafts', '[Gmail]/Sent Mail')):
            with self.subTest(attendu=attendu):
                FauxGmail.preparer(liste)
                d = gs.essayer(ADRESSE, MDP_NET)['dossiers']
                self.assertEqual((d['drafts'], d['sent']), (attendu, envoyes))
        FauxGmail.preparer(LISTE_FR)
        d = gs.essayer(ADRESSE, MDP_NET)['dossiers']
        self.assertNotEqual(d['drafts'], 'Drafts')                              # le libellé personnel « Drafts » n'est pas le dossier spécial
        self.assertEqual((d['trash'], d['junk'], d['all']), ('[Gmail]/Corbeille', '[Gmail]/Spam', '[Gmail]/Tous les messages'))

    def test_G02_sans_attribut_drafts_refuse_meme_si_le_nom_correspond(self):
        FauxGmail.preparer(LISTE_SANS)
        with self.assertRaisesRegex(Stop, 'gmail_brouillons_introuvables'):
            gs.essayer(ADRESSE, MDP_NET)
        # mutation (en mémoire) : une détection par le nom aurait accepté « [Gmail]/Brouillons » sans attribut
        from agent.setup import mailbox_names
        with patch.object(gs, 'dossiers_speciaux', lambda entrees: {'drafts': n for n, _ in entrees if n.endswith('Brouillons')}):
            self.assertEqual(gs.essayer(ADRESSE, MDP_NET)['dossiers']['drafts'], '[Gmail]/Brouillons')
        self.assertEqual(gs.dossiers_speciaux([('[Gmail]', ['\\noselect', '\\drafts']), ('[Gmail]/B', ['\\drafts'])]), {'drafts': '[Gmail]/B'})
        self.assertTrue(mailbox_names)

    def test_G03_mot_de_passe_application_format_avant_tout_reseau(self):
        self.assertEqual(gs.normaliser_mot_de_passe('abcd efgh ijkl\tmnop'), MDP_NET)
        for faux in ('MotDePasseHabituel2026!', 'abc', ''):
            with self.subTest(faux=faux), self.assertRaises(Stop):
                gs.normaliser_mot_de_passe(faux)
        status, body, _ = self.appel('tester', {'adresse': ADRESSE, 'mot_de_passe': 'MotDePasseHabituel2026!', 'cle': 'cle-test-format-01'})
        self.assertEqual((status[:3], body['error'], body['resultat']), ('200', 'gmail_mot_de_passe_application_format', 'echec'))   # résultat d'essai, pas requête fautive
        self.assertEqual(FauxGmail.connexions, [])                                 # refusé avant toute connexion
        self.assertIn('16 caractères', body['message'])


class Refus(Base):
    CAS = ((b'[ALERT] Application-specific password required: https://support.google.com/accounts/answer/185833 (Failure)', 'gmail_mot_de_passe_application_requis', 'ALERT'),
           (b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)', 'gmail_identifiants_refuses', 'AUTHENTICATIONFAILED'),
           (b'[ALERT] IMAP access is disabled for your domain. Please contact your domain administrator for questions about this feature. (Failure)', 'gmail_imap_desactive', 'ALERT'),
           (b'[ALERT] Your account is not enabled for IMAP use. Please visit your Gmail settings page and enable your account for IMAP access. (Failure)', 'gmail_imap_desactive', 'ALERT'),
           (b'[ALERT] Please log in via your web browser: https://support.google.com/mail/accounts/answer/78754 (Failure)', 'gmail_connexion_web_requise', 'ALERT'),
           (b'[UNAVAILABLE] Too many simultaneous connections. (Failure)', 'gmail_temporairement_indisponible', 'UNAVAILABLE'),
           (b'[SOMETHING] inattendu', 'gmail_refus_inconnu', 'SOMETHING'))

    def test_G04_refus_de_google_traduits_et_notes(self):
        for n, (reponse, code, motif) in enumerate(self.CAS):
            with self.subTest(code=code):
                FauxGmail.preparer(refus=reponse)
                status, body, _ = self.appel('tester', {'adresse': ADRESSE, 'mot_de_passe': MDP, 'cle': 'cle-refus-%04d' % n})
                self.assertEqual((status[:3], body['error']), ('200', code))   # 5.6.103 : refus de Google = résultat de l'essai
                self.assertEqual(body['message'], gs.MESSAGES[code])
                self.assertNotIn('Failure', body['message'])                    # le texte anglais de Google n'est jamais montré tel quel
                essai = gs.dernier_essai(self.desk, ADRESSE)
                self.assertEqual((essai['resultat'], essai['code'], essai['motif']), ('echec', code, motif))
        self.assertIn('Workspace', gs.MESSAGES['gmail_imap_desactive'])
        self.assertIn('application interne', gs.MESSAGES['gmail_identifiants_refuses'])

    def test_G04_echec_donne_l_etat_reel(self):
        FauxGmail.preparer(refus=b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)')
        status, _, _ = self.enregistrer()
        self.assertEqual(status, '400 Bad Request')
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['mail']['host'], 'mail.example.test')   # rien enregistré
        statut, phrase = gs.etat_texte(self.desk)
        self.assertEqual(statut, 'echec')
        self.assertIn('échec', phrase)


class Enregistrement(Base):
    def test_G05_mot_de_passe_jamais_en_clair(self):
        status, body, brut = self.enregistrer()
        self.assertEqual(status, '200 OK', body)
        self.assertEqual(dict(brut['headers'])['Cache-Control'], 'private, no-store')
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertNotIn(MDP_NET, brut['body'])
        self.assertNotIn(MDP_NET, self.tout_le_stockage())
        self.assertNotIn('abcd efgh', self.tout_le_stockage())
        self.assertTrue(cfg['mail']['password_file'].endswith('.secret'))
        coffre = Path(cfg['mail']['password_file'])
        self.assertNotIn(MDP_NET, coffre.read_text(encoding='utf-8'))            # chiffré dans le coffre
        self.assertEqual(read_secret(str(coffre)), MDP_NET)
        page = gs.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env)
        self.assertNotIn(MDP_NET, page)
        self.assertIn('type="password"', page)

    def test_G06_configuration_gmail_et_essai_rattache_au_secret(self):
        status, body, _ = self.enregistrer()
        self.assertEqual(status, '200 OK', body)
        mail = json.loads(self.config.read_text(encoding='utf-8'))['mail']
        self.assertEqual((mail['host'], mail['port'], mail['security'], mail['username'], mail['inbox'], mail['drafts'], mail['sent'], mail['from_address']),
                         ('imap.gmail.com', 993, 'ssl', ADRESSE, 'INBOX', '[Gmail]/Brouillons', '[Gmail]/Messages envoyés', ADRESSE))
        self.assertIn('Déposer dans Brouillons', body['message'])
        self.assertTrue(gs.etat(self.desk)['valable'])
        self.assertEqual(gs.etat_texte(self.desk)[0], 'ok')
        # le mot de passe changé ailleurs (Paramètres) : l'essai n'est plus valable, l'étape est « à tester »
        from agent.config567 import save
        save(self.desk, {'revision': self.revision(), 'values': {'mail.password_file': 'qrstuvwxyzabcdef'}}, self.env)
        self.desk.c['mail'] = json.loads(self.config.read_text(encoding='utf-8'))['mail']
        self.assertFalse(gs.etat(self.desk)['valable'])
        self.assertEqual(gs.etat_texte(self.desk)[0], 'a_tester')
        # retester sans ressaisir : le secret rangé est relu pour ce même compte, l'essai redevient valable
        status, body, _ = self.appel('tester', {'adresse': ADRESSE, 'mot_de_passe': '', 'cle': 'cle-retest-00001'})
        self.assertEqual(status, '200 OK', body)
        self.assertEqual(FauxGmail.identifiants[-1], (ADRESSE, 'qrstuvwxyzabcdef'))
        self.assertTrue(gs.etat(self.desk)['valable'])

    def test_G06_brouillon_avec_piece_jointe_depose_dans_les_brouillons_detectes(self):
        self.assertEqual(self.enregistrer()[0], '200 OK')
        from agent.mailbox import Mailbox
        cfg = json.loads(self.config.read_text(encoding='utf-8'))['mail']
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = ADRESSE, 'kervilac@example.test', 'Projet de protocole KERVILAC'
        msg.set_content('Projet à relire.')
        msg.add_attachment(b'%PDF-1.4 fictif', maintype='application', subtype='pdf', filename='protocole.pdf')
        box = Mailbox(cfg)
        try:
            box.append_draft(msg)
        finally:
            box.close()
        self.assertIn(('APPEND', '[Gmail]/Brouillons'), FauxGmail.log)
        depose = next(iter(FauxGmail.folders['[Gmail]/Brouillons'].values()))
        self.assertIn('\\Draft', depose['flags'])
        self.assertIn(b'protocole.pdf', depose['raw'])
        self.assertEqual(FauxGmail.folders['[Gmail]/Messages envoy&AOk-s'], {})   # rien d'envoyé

    def test_G07_remplacer_une_autre_messagerie_demande_confirmation(self):
        status, body, _ = self.enregistrer(remplacer=False, cle='cle-remplacer-001')
        self.assertEqual((status, body['error']), ('400 Bad Request', 'messagerie_existante_remplacer'))
        self.assertEqual(FauxGmail.connexions, [])
        # mutation : sans le garde-fou, la messagerie du cabinet aurait été remplacée sans confirmation
        with patch.object(gs, 'est_gmail', lambda mail: None):
            self.assertEqual(self.enregistrer(remplacer=False, cle='cle-remplacer-002')[0], '200 OK')

    def test_G08_revision_perimee_409(self):
        status, body, _ = self.enregistrer(revision=self.revision() + 7)
        self.assertEqual((status, body['error']), ('409 Conflict', 'configuration_modifiee_recharger'))

    def test_G09_droits_csrf_et_routes(self):
        status, body, _ = self.appel('enregistrer', {'adresse': ADRESSE, 'mot_de_passe': MDP, 'cle': 'cle-avocat-00001'}, role='avocat')
        self.assertEqual((status, body['error']), ('403 Forbidden', 'role_insuffisant'))
        status, body, _ = self.appel('tester', {'adresse': ADRESSE, 'mot_de_passe': MDP, 'cle': 'cle-csrf-0000001'}, jeton='faux')
        self.assertEqual(body['error'], 'origine_ou_csrf_refuse')
        self.assertEqual(FauxGmail.connexions, [])
        self.assertEqual(self.appel('inconnue', {})[0], '404 Not Found')
        self.assertEqual(self.appel('tester', {'adresse': ADRESSE, 'mot_de_passe': MDP})[1]['error'], 'cle_idempotence_requise')
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56103/gmail/tester'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56103/gmail/application'))
        self.assertTrue(allowed('administrateur', 'POST', '/api440/m56103/gmail/enregistrer'))
        self.assertFalse(gs.allowed('avocat', 'POST', 'm56103/gmail/tester'))
        # mutation : sans le contrôle du rôle dans la route, un avocat aurait modifié la messagerie du cabinet
        with patch.object(gs, 'enregistrer', lambda *a: {'ok': True}):
            self.assertEqual(self.appel('enregistrer', {'adresse': ADRESSE, 'cle': 'cle-avocat-00002'}, role='avocat')[0], '403 Forbidden')

    def test_G09_idempotence(self):
        premier = self.enregistrer()
        self.assertEqual(premier[0], '200 OK')
        essais = self.desk.db.execute('SELECT COUNT(*) FROM gmail_simple56103_essais').fetchone()[0]
        rejoue = self.enregistrer(revision=premier[1]['revision'] - 1)    # même clé : reçu rendu, rien de refait (pas de 409, pas de nouvel essai)
        self.assertEqual(rejoue[1], premier[1])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM gmail_simple56103_essais').fetchone()[0], essais)
        autre = self.enregistrer(adresse='martin.cabinet@example.test')
        self.assertEqual((autre[0], autre[1]['error']), ('409 Conflict', 'cle_idempotence_deja_utilisee'))
        recus = '\n'.join(r[0] for r in self.desk.db.execute('SELECT empreinte || reponse FROM assistant56103_recus'))
        self.assertNotIn(MDP_NET, recus)


class ApplicationInterne(Base):
    def corps(self, **kw):
        c = {'interne': True, 'client_id': CLIENT, 'secret': SECRET_CLIENT, 'redirect_uri': RETOUR, 'proposer': True,
             'revision': self.revision(), 'cle': 'cle-application-01'}
        c.update(kw)
        return c

    def test_G10_application_interne_secret_au_coffre_jamais_reaffiche(self):
        from agent import gmail5697 as gm, google5697 as gg
        status, body, brut = self.appel('application', self.corps())
        self.assertEqual(status, '200 OK', body)
        cfg = json.loads(self.config.read_text(encoding='utf-8'))['google5697']
        self.assertEqual((cfg['client_id'], cfg['redirect_uri']), (CLIENT, RETOUR))
        self.assertEqual(read_secret(cfg['client_secret_file']), SECRET_CLIENT)
        self.assertNotIn(SECRET_CLIENT, brut['body'])
        self.assertNotIn(SECRET_CLIENT, self.tout_le_stockage())
        self.assertNotIn(SECRET_CLIENT[-4:], gs.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env))
        self.assertTrue(gg.etat_configuration(self.desk, 'gmail')['configure'])
        self.assertTrue(gm.actif(self.desk))
        # secret laissé vide une fois configuré : le secret rangé est conservé
        status, body, _ = self.appel('application', self.corps(secret='', revision=self.revision(), cle='cle-application-02', proposer=False))
        self.assertEqual(status, '200 OK', body)
        self.assertEqual(read_secret(json.loads(self.config.read_text(encoding='utf-8'))['google5697']['client_secret_file']), SECRET_CLIENT)

    def test_G10_controles_de_l_application(self):
        for kw, code in (({'interne': False}, 'gmail_audience_interne_a_confirmer'), ({'client_id': 'mon-client'}, 'gmail_client_id_invalide'),
                         ({'redirect_uri': 'http://pilote.example.test/oauth/google/callback'}, 'gmail_uri_retour_invalide'),
                         ({'redirect_uri': 'https://pilote.example.test/ailleurs'}, 'gmail_uri_retour_invalide'),
                         ({'secret': ''}, 'gmail_secret_requis'), ({'secret': 'avec espace dedans'}, 'gmail_secret_invalide')):
            with self.subTest(code=code):
                status, body, _ = self.appel('application', self.corps(cle='cle-ctrl-' + code[-12:].replace('_', '-'), **kw))
                self.assertEqual((status, body['error']), ('400 Bad Request', code))
        self.assertNotIn('google5697', json.loads(self.config.read_text(encoding='utf-8')))

    def test_G10_etapes_et_documentation_citees(self):
        page = gs.section_html(self.desk, '/p', 'admin@example.test', 'administrateur', self.env)
        for attendu in ('Interne', 'Web application', 'Authorized redirect URIs', 'gmail.readonly', 'gmail.compose', 'myaccount.google.com/apppasswords',
                        'Validation en deux étapes', '/api440/m5689/oauth/google/callback', 'data-api="m56103/gmail/tester"'):
            self.assertIn(attendu, page)
        avocat = gs.section_html(self.desk, '/p', 'avocat@example.test', 'avocat', self.env)
        self.assertNotIn('data-api="m56103/gmail/', avocat)
        self.assertIn('administrateur', avocat)


if __name__ == '__main__':
    unittest.main()

"""5.6.96, lot courriels (M01-M05) : la discussion lit la boîte du cabinet au premier tour, en lecture seule, sur des bornes locales exactes.
Puis (cahier v3, C01-C04 sur IMAP, I03, C07) : vue « À traiter avec le Pilote » derrière une option désactivée par défaut.

Faux serveur IMAP au niveau ``imaplib`` : chaque commande est enregistrée, le vrai ``Mailbox`` est exercé ; aucun réseau, aucun modèle,
données fictives seulement (DURAND, MARTIN, BERNARD, Valbrune, example.test). Dates relatives au jour ; fuseaux Europe/Paris et Pacific/Auckland."""
from datetime import datetime, time as dtime, timedelta, timezone
import json
import unittest
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from agent import acces5683, integration, membres5650, workspace
from agent.common import Stop, digest
from agent.desk import Desk
import test_agent as fixtures

CABINET = 'cabinet@example.test'
CLAIRE, CAMILLE = 'claire@example.test', 'camille@example.test'
PARIS, AUCKLAND = 'Europe/Paris', 'Pacific/Auckland'
CAHIER = 'Je souhaiterais que tu me listes des courriels que j’ai reçus dans ma boîte de réception.'
META = '(UID FLAGS INTERNALDATE RFC822.SIZE)'


class FauxIMAP:
    """Serveur IMAP en mémoire, horloge en UTC (SINCE/BEFORE comparent le jour UTC de l'INTERNALDATE). Toute commande est notée."""

    def __init__(self):
        self.dossiers = {'INBOX': {}, 'Sent': {}}
        self.validite = {'INBOX': '7', 'Sent': '8'}
        self.commandes, self.illisibles, self.selected, self.readonly = [], set(), None, None
        self.capabilities = ('IMAP4REV1',)
        self._uid = 100

    def ajouter(self, dossier, de, objet, quand, references='', corps='corps secret', html=False):
        self._uid += 1
        raw = ('From: %s\r\nTo: Cabinet <%s>\r\nSubject: %s\r\nDate: %s\r\nMessage-ID: <m%d@example.test>\r\n%s%s\r\n%s\r\n'
               % (de, CABINET, objet, quand.strftime('%a, %d %b %Y %H:%M:%S %z'), self._uid,
                  ('References: %s\r\nIn-Reply-To: %s\r\n' % (references, references)) if references else '',
                  'MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n' if html else '', corps)).encode()
        self.dossiers[dossier][self._uid] = {'raw': raw, 'date': quand.astimezone(timezone.utc), 'flags': set()}
        return str(self._uid)

    def login(self, user, password):
        self.commandes.append(('LOGIN',))
        return 'OK', [b'ok']

    def logout(self):
        self.commandes.append(('LOGOUT',))
        return 'BYE', [b'bye']

    def select(self, folder, readonly=False):
        self.selected, self.readonly = folder.strip('"'), readonly
        self.commandes.append(('EXAMINE' if readonly else 'SELECT', self.selected))
        return ('OK', [b'1']) if self.selected in self.dossiers else ('NO', [b'absent'])

    def response(self, name):
        return name, [self.validite[self.selected].encode()] if name == 'UIDVALIDITY' else [None]

    def _cherche(self, item, criteria):
        jour = item['date'].date()
        i = 0
        while i < len(criteria):
            c = str(criteria[i]).upper()
            if c == 'UNDELETED' and '\\Deleted' in item['flags']:
                return False
            if c in ('SINCE', 'BEFORE'):
                borne = datetime.strptime(criteria[i + 1], '%d-%b-%Y').date()
                if (c == 'SINCE' and jour < borne) or (c == 'BEFORE' and jour >= borne):
                    return False
                i += 1
            i += 1
        return True

    def uid(self, command, *args):
        command = command.upper()
        self.commandes.append(('UID', command) + tuple(str(a) for a in args))
        boite = self.dossiers[self.selected]
        if command == 'SEARCH':
            found = [str(u) for u, item in sorted(boite.items()) if self._cherche(item, list(args[1:]))]
            return 'OK', [' '.join(found).encode()]
        if command == 'FETCH':
            u, query = int(args[0]), args[1]
            item = boite.get(u)
            if not item:
                return 'OK', []
            stamp = item['date'].strftime('%d-%b-%Y %H:%M:%S %z')
            if 'BODY' not in query:
                if u in self.illisibles:                               # en-tête corrompu : aucune INTERNALDATE
                    return 'OK', [('%d (UID %d FLAGS () RFC822.SIZE %d)' % (u, u, len(item['raw']))).encode()]
                return 'OK', [('%d (UID %d FLAGS () INTERNALDATE "%s" RFC822.SIZE %d)' % (u, u, stamp, len(item['raw']))).encode()]
            if 'BODY.PEEK[HEADER]' in query:
                head = item['raw'].split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                return 'OK', [(('%d (BODY[HEADER] {%d}' % (u, len(head))).encode(), head), b')']
            return 'OK', [(('%d (BODY[] {%d}' % (u, len(item['raw']))).encode(), item['raw']), b')']
        return 'OK', [b'done']                                         # STORE, COPY, MOVE, EXPUNGE : notés, le test les refuse

    def store(self, *args):
        self.commandes.append(('STORE',) + tuple(str(a) for a in args))
        return 'OK', [b'done']

    def append(self, *args):
        self.commandes.append(('APPEND',) + tuple(str(a)[:30] for a in args))
        return 'OK', [b'done']


class Jamais:
    """Modèle qui ne doit jamais être appelé pour une liste simple."""

    def ask(self, stage, data):
        raise AssertionError('modèle appelé pour une liste simple')

    def complete(self, *a, **k):
        raise AssertionError('modèle appelé pour une liste simple')


def minuit(day, zone):
    return datetime.combine(day, dtime.min, ZoneInfo(zone))


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.c['mail']['password_file'] = str(self.f.base / 'imap.secret')
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.serveur = FauxIMAP()
        for p in (patch('agent.mailbox.connect', lambda cfg: self.serveur), patch('agent.mailbox.read_secret', lambda path: 'secret-fictif')):
            p.start()
            self.addCleanup(p.stop)
        self.today = acces5683.today_of(self.desk)[0]
        self.addCleanup(membres5650.clear)

    def a(self, jours, hh, mm, zone=PARIS):
        """Instant local relatif au jour : ``jours`` = 0 aujourd'hui, -1 hier…"""
        return minuit(self.today + timedelta(days=jours), zone) + timedelta(hours=hh, minutes=mm)

    def boite_type(self):
        """INBOX 13 messages (4 aujourd'hui dont une copie du cabinet, 3 hier, 3 dans la semaine, 3 anciens), Sent 3."""
        s = self.serveur
        s.ajouter('INBOX', 'Claire DURAND <claire.durand@example.test>', 'Relance DURAND', self.a(0, 0, 30))        # veille au soir en UTC
        s.ajouter('INBOX', 'Camille MARTIN <camille.martin@example.test>', 'Pièces MARTIN', self.a(0, 9, 5))
        s.ajouter('INBOX', 'Cabinet <%s>' % CABINET, 'Copie envoyée à BERNARD', self.a(0, 10, 0))
        s.ajouter('INBOX', 'Greffe <greffe@example.test>', 'Avis ZORVAL', self.a(0, 11, 45))
        s.ajouter('INBOX', 'Confrère <confrere@example.test>', 'Bordereau KERVILAC', self.a(-1, 8, 0))
        s.ajouter('INBOX', 'Client <client@example.test>', 'Question ORLANDEC', self.a(-1, 22, 30))
        s.ajouter('INBOX', 'Client <client@example.test>', 'Suite ORLANDEC', self.a(-1, 23, 30))
        for n, (j, objet) in enumerate(((-3, 'Convocation'), (-5, 'Facture fournisseur'), (-6, 'Note Valbrune'))):
            s.ajouter('INBOX', 'Tiers %d <tiers%d@example.test>' % (n, n), objet, self.a(j, 14, 0))
        for n, j in enumerate((-20, -40, -400)):
            s.ajouter('INBOX', 'Ancien %d <ancien%d@example.test>' % (n, n), 'Ancien %d' % n, self.a(j, 14, 0))
        s.ajouter('Sent', 'Cabinet <%s>' % CABINET, 'Envoi du jour', self.a(0, 15, 0))
        s.ajouter('Sent', 'Cabinet <%s>' % CABINET, 'Envoi d’hier', self.a(-1, 15, 0))
        s.ajouter('Sent', 'Cabinet <%s>' % CABINET, 'Envoi ancien', self.a(-10, 15, 0))

    def liste(self, question, owner='cabinet', purpose='assistant', matter=''):
        return workspace.chat(self.desk, {'matter': matter, 'key': '', 'question': question, 'rapide': True, 'purpose': purpose, 'auteur': owner},
                              None, Jamais(), None, return_result=True)

    def lecture(self, question, owner='cabinet', **kw):
        return acces5683.read_mail(self.desk, question, owner=owner, **kw)

    def assert_lecture_seule(self):
        cmds = self.serveur.commandes
        self.assertTrue(cmds, 'aucune commande IMAP : la boîte n’a pas été lue')
        for c in cmds:
            self.assertNotIn(c[0], ('SELECT', 'STORE', 'APPEND'), c)                      # SELECT en écriture interdit : EXAMINE seulement
            if c[0] == 'UID':
                self.assertIn(c[1], ('SEARCH', 'FETCH'), c)
                if c[1] == 'FETCH':
                    self.assertTrue('PEEK' in c[3] or c[3] == META, c)

    def corps_lus(self):
        return [c for c in self.serveur.commandes if c[0] == 'UID' and c[1] == 'FETCH' and 'BODY.PEEK[]' in c[3]]

    def objets(self, result):
        return [l['objet'] for s in result['sources'] for l in s.get('lignes', [])]


# ------------------------------------------------------------------------------------------------------------ M01 : requête structurée
class Requete(Base):
    def test_M01_phrase_du_cahier_liste_sept_jours_sans_terme(self):
        req = acces5683.structurer(CAHIER, self.today, PARIS)
        self.assertEqual(req['intention'], 'lister')
        self.assertEqual(req['dossier_imap'], 'inbox')
        self.assertEqual((req['du'], req['au']), ((self.today - timedelta(days=6)).isoformat(), self.today.isoformat()))
        self.assertFalse({k: v for k, v in req['filtres'].items() if v})
        self.assertEqual(acces5683.focus_terms(CAHIER), [])

    def test_M01_aujourd_hui_et_hier_minuit_local(self):
        for zone in (PARIS, AUCKLAND):
            req = acces5683.structurer('liste les courriels reçus aujourd’hui', self.today, zone)
            self.assertEqual((req['debut'], req['fin']), (minuit(self.today, zone), minuit(self.today + timedelta(days=1), zone)))
            req = acces5683.structurer('et ceux d’hier ?', self.today, zone)
            self.assertEqual((req['debut'], req['fin']), (minuit(self.today - timedelta(days=1), zone), minuit(self.today, zone)))
            self.assertEqual(req['fuseau'], zone)

    def test_M01_filtre_seulement_sur_demande(self):
        self.assertEqual(acces5683.mail_filter('liste ma boîte de réception'), ('', ''))
        self.assertEqual(acces5683.mail_filter('les courriels que j’ai reçus dans ma boîte de réception'), ('', ''))
        self.assertEqual(acces5683.mail_filter('montre-moi les mails de DURAND'), ('expediteur_ou_objet', 'durand'))
        self.assertEqual(acces5683.mail_filter('les courriels au sujet de bail commercial'), ('objet', 'bail commercial'))
        self.assertEqual(acces5683.mail_filter('mails dont l’objet contient « ZORVAL »'), ('objet', 'zorval'))
        self.assertEqual(acces5683.structurer('montre-moi les mails envoyés hier', self.today, PARIS)['dossier_imap'], 'sent')

    def test_M01_verbes_conjugues_et_pluriels_jamais_des_termes(self):
        for phrase in ('Je voudrais que tu me listes mes courriels', 'Peux-tu m’afficher les mails reçus ?', 'Montre-moi les messages reçus ce matin',
                       'Je souhaiterais la liste des courriels envoyés', 'Quels courriels ai-je reçus hier ?'):
            self.assertEqual(acces5683.focus_terms(phrase), [], phrase)
        self.assertEqual(acces5683.focus_terms('retrouve le mail de DURAND'), ['durand'])


# ------------------------------------------------------------------------------------------- M02 : lecture réelle, bornée, lecture seule
class Lecture(Base):
    def test_M02_liste_au_premier_tour_sans_modele_ni_corps(self):
        self.boite_type()
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(self.objets(result), ['Avis ZORVAL', 'Copie envoyée à BERNARD', 'Pièces MARTIN', 'Relance DURAND'])
        self.assertIn('Avis ZORVAL', result['answer'])
        self.assertEqual(self.corps_lus(), [])
        self.assert_lecture_seule()
        self.assertEqual(result['proposed_actions'], [])

    def test_M02_voix_liste_au_premier_tour_prononcable(self):
        self.boite_type()
        result = self.liste('quels courriels ai-je reçus aujourd’hui ?', purpose='voice_conversation')
        self.assertIn('Relance DURAND', result['answer'])
        self.assertNotIn('|', result['answer'])
        self.assertEqual(self.corps_lus(), [])

    def test_M02_bornes_internaldate_au_passage_de_minuit(self):
        for zone, owner in ((PARIS, CLAIRE), (AUCKLAND, CAMILLE)):
            self.desk.setting('proactive568:profile:' + owner, {'timezone': zone})
            today = acces5683.today_of(self.desk, owner)[0]
            self.serveur = FauxIMAP()
            with patch('agent.mailbox.connect', lambda cfg: self.serveur):
                self.serveur.ajouter('INBOX', 'A <a@example.test>', 'Juste après minuit', minuit(today, zone) + timedelta(minutes=30))
                self.serveur.ajouter('INBOX', 'B <b@example.test>', 'Veille au soir', minuit(today, zone) - timedelta(minutes=90))
                self.serveur.ajouter('INBOX', 'C <c@example.test>', 'Fin de journée', minuit(today + timedelta(days=1), zone) - timedelta(minutes=10))
                self.serveur.ajouter('INBOX', 'D <d@example.test>', 'Lendemain', minuit(today + timedelta(days=1), zone) + timedelta(minutes=5))
                _, state = self.lecture('liste les courriels reçus aujourd’hui', owner=owner, today=today)
                self.assertEqual(sorted(l['objet'] for l in state['lignes']), ['Fin de journée', 'Juste après minuit'], zone)
                self.assertEqual(state['fuseau'], zone)
                # Mutation : sans la vérification INTERNALDATE, la veille au soir (jour UTC couvert par SINCE) entre dans « aujourd'hui »
                with patch('agent.acces5683.dans_bornes', lambda *a: True):
                    _, mutant = self.lecture('liste les courriels reçus aujourd’hui', owner=owner, today=today)
                self.assertIn('Veille au soir', [l['objet'] for l in mutant['lignes']], zone)

    def test_M02_copie_du_cabinet_etiquetee_sans_reponse_a_soi(self):
        self.boite_type()
        result = self.liste('liste les courriels reçus aujourd’hui')
        lignes = {l['objet']: l for s in result['sources'] for l in s.get('lignes', [])}
        self.assertTrue(lignes['Copie envoyée à BERNARD']['copie'])
        self.assertFalse(lignes['Pièces MARTIN']['copie'])
        self.assertIn('copie du cabinet', result['answer'])
        self.assertEqual(result['proposed_actions'], [])

    def test_M02_resultat_conserve_uidvalidity_uid_compte_et_boite(self):
        self.boite_type()
        _, state = self.lecture('liste les courriels reçus aujourd’hui', today=self.today)
        self.assertEqual((state['uidvalidity'], state['boite'], state['compte']), ('7', 'INBOX', CABINET + '@mail.example.test'))
        self.assertTrue(all(l['uid'].isdigit() and l['uidvalidity'] == '7' for l in state['lignes']))

    def test_M02_messages_envoyes_lus_dans_sent_en_lecture_seule(self):
        self.boite_type()
        result = self.liste('liste les courriels que j’ai envoyés aujourd’hui')
        self.assertEqual(self.objets(result), ['Envoi du jour'])
        self.assertIn(('EXAMINE', 'Sent'), self.serveur.commandes)
        self.assert_lecture_seule()


# ------------------------------------------------------------------------------------------------------------ M03 : contrat de résultat
class Contrat(Base):
    def test_M03_boite_vide_verifiee(self):
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'vide_verifie')
        self.assertEqual(result['compteurs'], {'entetes_lus': 0, 'en_echec': 0, 'filtres': 0, 'affiches': 0})
        self.assertIn('Aucun courriel', result['answer'])
        self.assertNotIn('partielle', result['answer'])
        self.assertFalse(result['reessayer'])

    def test_M03_en_tete_illisible_compte_et_partiel_jamais_vide_verifie(self):
        self.boite_type()
        uids = [u for u, m in self.serveur.dossiers['INBOX'].items() if b'Avis ZORVAL' in m['raw']]
        self.serveur.illisibles.update(uids)
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'partiel')
        self.assertEqual(result['compteurs']['en_echec'], 1)
        self.assertEqual(result['compteurs']['affiches'], 3)
        self.assertIn('1 en échec', result['answer'])
        self.serveur.illisibles.update(self.serveur.dossiers['INBOX'])                  # tous illisibles : rien d'affiché, mais rien de « vérifié »
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'partiel')
        self.assertNotIn('Aucun courriel', result['answer'])

    def test_M03_total_imap_brut_jamais_presente_comme_lisible(self):
        self.boite_type()
        self.serveur.ajouter('INBOX', 'Huit jours <huit@example.test>', 'Huitième jour', self.a(-7, 14, 0))      # hors des sept jours, mais dans SINCE élargi
        result = self.liste(CAHIER)
        state = json.loads(self.desk.db.execute('SELECT response FROM conversations ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertNotIn('total_periode', json.dumps(state))
        self.assertEqual(result['compteurs']['affiches'], 10)                             # sept jours calendaires, aujourd'hui compris
        self.assertEqual(result['compteurs']['entetes_lus'], 11)                          # SINCE élargi d'un jour : un en-tête de plus lu, puis filtré sur INTERNALDATE
        self.assertEqual(result['compteurs']['filtres'], 1)
        self.assertNotIn('Huitième jour', result['answer'])
        self.assertNotRegex(result['answer'], r'\b14 (?:courriels?|messages?|en-t[eê]tes?|trouv)')                 # 14 en boîte : jamais annoncé comme compte lisible
        self.assertNotIn(14, result['compteurs'].values())

    def test_M03_liste_bornee_a_quarante_avec_annonce(self):
        for n in range(45):
            self.serveur.ajouter('INBOX', 'T%d <t%d@example.test>' % (n, n), 'Message %02d' % n, self.a(0, 8, n))
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['compteurs']['affiches'], 40)
        self.assertEqual(result['statut'], 'partiel')
        self.assertIn('40', result['answer'])
        self.assertIn('Message 44', result['answer'])                                      # les plus récents d'abord
        self.assertNotIn('Message 00', result['answer'])

    def test_M03_delai_depasse_sous_ensemble_heure_et_reessayer(self):
        self.boite_type()
        with patch.dict(acces5683.LIMITS['assistant'], {'seconds': -1}):
            result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'partiel')
        self.assertTrue(result['reessayer'])
        self.assertTrue(result['heure_lecture'])
        self.assertIn('Réessayer', result['answer'])
        self.assertEqual(self.corps_lus(), [])

    def test_M03_indisponible_et_refuse(self):
        with patch('agent.mailbox.connect', side_effect=Stop('imap_serveur_introuvable')):
            result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'indisponible')
        self.assertTrue(result['reessayer'])
        self.assertNotIn('Aucun courriel', result['answer'])
        membres5650.set_ai_denied({'courriels'})
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'refuse')
        self.assertEqual(self.serveur.commandes, [])


# --------------------------------------------------------------------------------------------- M04 : compte, droits, suite de la conversation
class Compte(Base):
    def test_M04_auteur_transmis_fuseau_de_la_personne(self):
        self.desk.setting('proactive568:profile:' + CAMILLE, {'timezone': AUCKLAND})
        self.boite_type()
        result = self.liste('liste les courriels reçus aujourd’hui', owner=CAMILLE)
        self.assertEqual(result['fuseau'], AUCKLAND)
        self.assertEqual(self.liste('liste les courriels reçus aujourd’hui', owner=CLAIRE)['fuseau'], PARIS)

    def test_M04_deux_comptes_bornes_differentes(self):
        self.desk.setting('proactive568:profile:' + CLAIRE, {'timezone': PARIS})
        self.desk.setting('proactive568:profile:' + CAMILLE, {'timezone': AUCKLAND})
        today = acces5683.today_of(self.desk, CLAIRE)[0]
        self.serveur.ajouter('INBOX', 'A <a@example.test>', 'Soirée de Paris', minuit(today + timedelta(days=1), PARIS) - timedelta(minutes=30))
        _, claire = self.lecture('liste les courriels reçus aujourd’hui', owner=CLAIRE, today=today)
        _, camille = self.lecture('liste les courriels reçus aujourd’hui', owner=CAMILLE, today=today)
        self.assertEqual([l['objet'] for l in claire['lignes']], ['Soirée de Paris'])
        self.assertEqual(camille['lignes'], [])                                           # 23 h 30 à Paris : déjà le lendemain à Auckland
        self.assertEqual((claire['fuseau'], camille['fuseau']), (PARIS, AUCKLAND))

    def test_M04_compte_non_autorise_refuse_sans_connexion(self):
        self.desk.setting('proactive568:profile:' + CLAIRE, {'enabled': True, 'primary_address': 'autre@example.test', 'timezone': PARIS})
        result = self.liste('liste les courriels reçus aujourd’hui', owner=CLAIRE)
        self.assertEqual(result['statut'], 'refuse')
        self.assertEqual(self.serveur.commandes, [])

    def test_M04_dossier_reserve_exclu(self):
        self.boite_type()
        uid = next(str(u) for u, m in self.serveur.dossiers['INBOX'].items() if b'Pi\xc3\xa8ces MARTIN' in m['raw'])
        key = digest('|'.join([CABINET + '@mail.example.test', 'INBOX', '7', uid]))
        self.desk.db.execute("INSERT INTO work_items(mail_key,state,matter,subject,sender,received,updated) VALUES(?,?,?,?,?,?,?)", (key, 'drafted', 'DOS-001', 's', 'x', 'd', 'd'))
        self.desk.db.commit()
        membres5650.set_viewer({'DOS-001'})
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertNotIn('Pièces MARTIN', result['answer'])
        self.assertEqual(result['compteurs']['affiches'], 3)
        self.assertGreaterEqual(result['compteurs']['filtres'], 1)
        membres5650.clear()
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertIn('Brouillon préparé', result['answer'])                             # état du traitement en clair, dossier visible

    def test_M04_relance_hier_reprend_la_boite_et_change_l_intervalle(self):
        self.boite_type()
        self.liste('liste les courriels que j’ai envoyés aujourd’hui')
        result = self.liste('Et ceux d’hier ?')
        self.assertEqual(self.objets(result), ['Envoi d’hier'])
        self.assertEqual(result['sources'][0]['path'], 'Liste des courriels (hier)')
        self.assertEqual(self.corps_lus(), [])

    def test_M04_ok_merci_d_accord_aucune_consultation(self):
        self.boite_type()
        self.liste('liste les courriels reçus aujourd’hui')
        self.serveur.commandes.clear()
        for mot in ('OK', 'merci', 'd’accord'):
            with patch('agent.acces5683.read_mail', side_effect=AssertionError('boîte consultée pour « %s »' % mot)):
                sources, access, _ = acces5683.gather(self.desk, mot, previous='liste les courriels reçus aujourd’hui')
            self.assertEqual((sources, 'courriels' in access), ([], False), mot)
        self.assertEqual(self.serveur.commandes, [])

    def test_M04_file_d_attente_premier_tour_sans_modele(self):
        self.boite_type()
        out = integration.submit_question(self.desk, 'liste les courriels reçus aujourd’hui', auteur=CLAIRE)
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()[0])
        with patch('agent.model.Model', side_effect=AssertionError('modèle instancié')):      # import local dans answer_question : jamais instancié
            done = integration.answer_question(self.desk, args)
        self.assertTrue(done.get('sans_controle'))
        text = self.desk.db.execute("SELECT content FROM assistant_messages WHERE role='assistant'").fetchone()[0]
        self.assertIn('Relance DURAND', text)
        self.assertEqual(self.corps_lus(), [])
        self.assert_lecture_seule()


# ------------------------------------------------------------------------------------------------------------------- M05 : recette
class Recette(Base):
    def test_M05_boite_type_liste_du_jour_zero_ecriture(self):
        self.boite_type()
        self.assertEqual((len(self.serveur.dossiers['INBOX']), len(self.serveur.dossiers['Sent'])), (13, 3))
        result = self.liste('liste les courriels reçus aujourd’hui')
        self.assertEqual(result['statut'], 'complet')
        self.assertEqual(result['compteurs']['affiches'], 4)
        self.assertNotIn('Question ORLANDEC', result['answer'])                            # hier 22 h 30, heure de Paris
        self.assert_lecture_seule()

    def test_M05_phrase_du_cahier_sept_jours_resultats_presents(self):
        self.boite_type()
        result = self.liste(CAHIER)
        self.assertEqual(result['statut'], 'complet')
        self.assertIn('sept derniers jours', result['answer'])
        self.assertEqual(result['compteurs']['affiches'], 10)
        self.assertNotIn('Ancien 0', result['answer'])
        self.assertEqual(self.corps_lus(), [])

    def test_M05_resume_passe_par_le_modele_avec_corps(self):
        self.boite_type()

        class Modele:
            payload = None

            def ask(self, stage, data):
                Modele.payload = data
                return {'answer': 'Synthèse fictive.', 'source_ids': ['courriels-liste'], 'limits': [], 'proposed_actions': []}
        out = workspace.chat(self.desk, {'matter': '', 'key': '', 'question': 'Résume les courriels reçus aujourd’hui', 'rapide': True, 'purpose': 'assistant'},
                             None, Modele(), None, return_result=True)
        self.assertEqual(out['answer'], 'Synthèse fictive.')
        self.assertEqual(Modele.payload['acces_cabinet']['courriels']['statut'], 'complet')
        self.assertTrue(self.corps_lus())
        self.assert_lecture_seule()


# ====================================================================== C01-C04 (IMAP), I03, C07 : vue « À traiter avec le Pilote »
ORIGINE, JETON = 'https://cabinet.example.test', 'jeton-csrf-fictif'
COMPTE = CABINET + '@mail.example.test'


class BaseVue(Base):
    """Courriels relevés par l'agent (état, rapport, fil) puis projetés dans ``work_items`` comme le fait le collecteur ; serveur IMAP factice."""

    def setUp(self):
        super().setUp()
        from agent.state import State
        self.state = State(self.desk.c['state_dir'])
        self.addCleanup(self.state.db.close)
        self.drafts_lus = []
        p = patch('agent.drafts440.list_drafts', side_effect=lambda *a, **k: self.drafts_lus.append(1) or {'folder': 'Drafts', 'uidvalidity': '9', 'items': []})
        p.start()
        self.addCleanup(p.stop)

    def entrant(self, objet, de='Claire DURAND <claire.durand@example.test>', quand=None, fil_de=None, statut='observed', raison='', matter='DOS-001', html=False, corps='Bonjour Maître.'):
        """Un courriel reçu, relevé par l'agent. ``fil_de`` : Message-ID racine (réponse dans un fil existant). Retourne (clé, fil)."""
        from email import policy
        from email.parser import BytesParser
        from agent.mailbox import Mail, addresses
        quand = quand or self.a(0, 9, 0)
        uid = self.serveur.ajouter('INBOX', de, objet, quand, references=fil_de or '', corps=corps, html=html)
        raw = self.serveur.dossiers['INBOX'][int(uid)]['raw']
        mail = Mail(uid, '7', 'INBOX', set(), quand, BytesParser(policy=policy.default).parsebytes(raw))
        key = mail.key(COMPTE)
        fil = digest(COMPTE + mail.root)
        self.state.set(key, digest(COMPTE + mail.mid), fil, statut, raison)
        self.state.report(key, {'key': key, 'subject': objet, 'sender': addresses(de)[0], 'source_uid': uid, 'source_mailbox': 'INBOX', 'received_at': quand.isoformat(),
                                'matter': matter, 'reason': raison, 'account_key': digest(COMPTE), 'incoming_message_id': mail.mid})
        integration.sync_work_items(self.desk)
        return key, fil, '<%s>' % mail.mid

    def activer(self, compte='oui', owner=CLAIRE):
        self.desk.setting('courriels5696:vue_assistee:' + owner, compte == 'oui')

    def appel(self, route, data=None, owner=CLAIRE, role='avocat', args=None):
        import io
        from agent import courriels_web5696
        corps = json.dumps(data or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': JETON, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps)}
        out = courriels_web5696.api(env, self.desk, {'origin': ORIGINE, 'csrf': JETON}, '/p', 'm5689/' + route, args or {}, 'GET' if data is None else 'POST')
        return out['status'][:3], json.loads(out['body'])

    def decision(self, fil, action, **extra):
        from agent import courriels5696 as cx
        version = extra.pop('version', None)
        if version is None:
            version = cx.ligne(self.desk, fil, CLAIRE)['version']
        return self.appel('courriels/preparer' if action == 'preparer' else 'courriels/decision',
                          {'fil': fil, 'action': action, 'version': version, 'idempotence': extra.pop('idempotence', None) or uuid.uuid4().hex, **extra})

    def page(self, owner=CLAIRE, role='avocat', **args):
        from agent import web440
        env = {'HTTP_X_AXIORHUB_USER': owner, 'HTTP_X_AXIORHUB_ROLE': role}
        return web440.drafts_page(self.desk, {'csrf': JETON, 'origin': ORIGINE}, '/p', args, env)

    def ligne(self, fil):
        from agent import courriels5696 as cx
        return cx.ligne(self.desk, fil, CLAIRE)

    def taches_comptees(self):
        """Même requête que le compteur « Courriels » d'Aujourd'hui (actuellement5695.courriels)."""
        return self.desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state IN ('needs_action','needs_confirmation')").fetchone()[0]


class VueAssistee(BaseVue):
    def test_C07_option_desactivee_par_defaut_et_vues_existantes_inchangees(self):
        from agent import courriels5696 as cx
        self.entrant('Demande DURAND')
        self.assertEqual(cx.option(self.desk, CLAIRE), (False, None, False))
        page = self.page(vue='assistante')                                   # adresse demandée, option éteinte : l'usage courant
        self.assertNotIn('id="cx96"', page)
        self.assertNotIn('vue=assistante', page)
        self.assertIn('id="ax-drafts"', page)
        self.assertEqual(self.drafts_lus, [1])
        self.activer()
        page = self.page()
        self.assertIn('À traiter avec le Pilote', page)                        # troisième onglet seulement si l'option est active
        self.assertIn('id="ax-drafts"', page)                                  # l'onglet par défaut reste « À relire »
        self.assertNotIn('id="cx96"', page)

    def test_I03_vue_choisie_avant_toute_lecture_des_brouillons(self):
        self.activer()
        self.entrant('Demande DURAND')
        with patch('agent.drafts440.list_drafts', side_effect=Stop('imap_delai_depasse')):
            boite = self.page(vue='boite')
            assistee = self.page(vue='assistante')
            relire = self.page()
        self.assertIn('id="bx5625"', boite)
        self.assertNotIn('id="ax-drafts"', boite)
        self.assertIn('id="cx96"', assistee)
        self.assertNotIn('id="ax-drafts"', assistee)
        self.assertIn('Demande DURAND', assistee)
        self.assertIn('id="ax-drafts"', relire)                                # l'erreur IMAP reste dans « À relire », sans bloquer les autres
        self.assertIn('ax-alert', relire)
        self.assertEqual(self.serveur.commandes, [])                           # aucune lecture de la messagerie au rendu des vues

    def test_C02_projection_locale_sans_reseau_ni_modele_ni_ecriture(self):
        self.activer()
        self.entrant('Demande DURAND')
        self.serveur.commandes.clear()
        avant = self.desk.db.total_changes
        with patch('agent.mailbox.connect', side_effect=AssertionError('messagerie lue au rendu')), patch('agent.model.Model', side_effect=AssertionError('modèle')):
            page = self.page(vue='assistante')
        self.assertIn('Demande DURAND', page)
        self.assertEqual(self.desk.db.total_changes, avant)

    def test_C02_une_ligne_par_fil_dernier_entrant_sans_copie_ni_liste(self):
        from agent import courriels5696 as cx
        _, fil, racine = self.entrant('Bail ORLANDEC', quand=self.a(-1, 9, 0))
        self.entrant('Re: Bail ORLANDEC', quand=self.a(0, 10, 0), fil_de=racine)
        self.entrant('Pièces MARTIN', de='Camille MARTIN <camille.martin@example.test>', quand=self.a(0, 8, 0))
        self.entrant('Copie BERNARD', de='Cabinet <%s>' % CABINET, quand=self.a(0, 11, 0))            # copie du cabinet
        self.entrant('Lettre d’information', de='Info <info@example.test>', statut='ignored', raison='liste_diffusion')
        rows = cx.lignes(self.desk, CLAIRE)
        self.assertEqual([r['objet'] for r in rows], ['Re: Bail ORLANDEC', 'Pièces MARTIN'])
        self.assertEqual(rows[0]['fil'], fil)
        self.assertEqual((rows[0]['statut'], rows[0]['dossier'], rows[0]['boite']), ('a_traiter', 'DEMO', 'INBOX'))

    def test_C02_reponse_prete_meme_projet_qu_aujourd_hui(self):
        from agent import courriels5696 as cx, livrables5689 as liv
        key, fil, _ = self.entrant('Demande DURAND')
        liv.ensure_schema(self.desk)
        stamp = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO prepared_items5689(id, document_id, version_id, title, source_service, source_object_id, source_version, production_state, response_id, kind, created, updated) "
                             "VALUES('it1','courriel-abc','v1','Réponse DURAND','reponses5671',?,?,'preparing',?,'courriel',?,?)", (key, stamp, key, stamp, stamp))
        self.desk.db.commit()
        r = self.ligne(fil)
        self.assertEqual((r['statut'], r['projet']), ('reponse_prete', 'courriel-abc'))
        self.activer()
        page = self.page(vue='assistante', fil=fil)
        self.assertIn('/p/livrables/courriel-abc?', page)                     # même élément du registre qu'Aujourd'hui
        self.assertIn('Relire la réponse', page)
        self.assertNotIn('Préparer une réponse</button>', page)
        self.assertEqual([x['fil'] for x in cx.lignes(self.desk, CLAIRE, filtre='reponses')], [fil])

    def test_C02_terminer_ecarter_motif_et_compteur_d_aujourd_hui(self):
        _, fil, _ = self.entrant('Demande DURAND')
        _, fil2, _ = self.entrant('Question MARTIN', de='Camille MARTIN <camille.martin@example.test>')
        self.activer()
        self.assertEqual(self.taches_comptees(), 2)
        statut, out = self.decision(fil, 'ecarter', motif='')
        self.assertEqual((statut, out['error'], out['champ']), ('400', 'motif_requis', 'motif'))
        self.assertEqual(self.ligne(fil)['statut'], 'a_traiter')
        statut, out = self.decision(fil, 'ecarter', motif='Déjà traité par téléphone')
        self.assertEqual((statut, out['statut']), ('200', 'ecarte'))
        statut, out = self.decision(fil2, 'terminer')
        self.assertEqual(out['statut'], 'termine')
        self.assertEqual(self.taches_comptees(), 0)                             # une seule tâche, la même qu'Aujourd'hui : plus comptée
        self.assertEqual([c for c in self.serveur.commandes if c[0] in ('UID', 'SELECT', 'EXAMINE')], [])   # rien dans la boîte distante

    def test_C02_idempotence_et_verrou_de_version(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        premier = self.decision(fil, 'terminer', idempotence=cle, version=0)
        second = self.decision(fil, 'terminer', idempotence=cle, version=0)        # double clic : même clé
        self.assertEqual((premier[0], second[0], second[1]['deja']), ('200', '200', True))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_evenements').fetchone()[0], 1)
        statut, out = self.decision(fil, 'rouvrir', version=0)                     # second onglet, page périmée
        self.assertEqual((statut, out['error']), ('409', 'version_perimee'))
        self.assertEqual(self.ligne(fil)['statut'], 'termine')

    def test_C02_mutation_sans_verrou_de_version_deux_decisions(self):
        """Mutation en mémoire : sans le contrôle de version, la page périmée d'un second onglet écrase la décision du premier."""
        import inspect
        import textwrap
        from agent import courriels5696 as cx
        _, fil, _ = self.entrant('Demande DURAND')
        source = textwrap.dedent(inspect.getsource(cx.decider))
        gardes = ("    if courante['version'] != version:", "        if actuelle != version:")      # contrôle avant l'état, puis sous verrou
        for garde in gardes:
            self.assertEqual(source.count(garde), 1, garde)
            source = source.replace(garde, garde.replace('!= version:', '!= version and False:'))
        espace = dict(vars(cx))
        exec(compile(source, cx.__file__, 'exec'), espace)
        espace['decider'](self.desk, CLAIRE, fil, 'terminer', 0, uuid.uuid4().hex)
        espace['decider'](self.desk, CLAIRE, fil, 'ecarter', 0, uuid.uuid4().hex, {'motif': 'Autre onglet'})
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_evenements').fetchone()[0], 2)   # le mutant accepte la page périmée
        with self.assertRaisesRegex(Stop, 'version_perimee'):
            cx.decider(self.desk, CLAIRE, fil, 'rouvrir', 0, uuid.uuid4().hex)                                     # le vrai code la refuse

    def test_C02_annuler_la_derniere_decision_et_tache_restauree(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        self.decision(fil, 'terminer')
        self.assertEqual(self.taches_comptees(), 0)
        statut, out = self.decision(fil, 'annuler')
        self.assertEqual((statut, out['statut']), ('200', 'a_traiter'))
        self.assertEqual(self.taches_comptees(), 1)
        statut, out = self.decision(fil, 'annuler')
        self.assertEqual(out['error'], 'rien_a_annuler')

    def test_C02_reporter_attente_echeance_et_fuseau(self):
        from agent import courriels5696 as cx
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        statut, out = self.decision(fil, 'reporter', jusqua=(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'))
        self.assertEqual(out['error'], 'date_passee')
        demain = (self.today + timedelta(days=1)).isoformat() + 'T08:30'
        statut, out = self.decision(fil, 'reporter', jusqua=demain)
        self.assertEqual(out['statut'], 'reporte')
        suivi = self.desk.db.execute('SELECT echeance, fuseau FROM courriels5696_suivis').fetchone()
        self.assertEqual(suivi['fuseau'], PARIS)
        self.assertEqual(datetime.fromisoformat(suivi['echeance']), minuit(self.today + timedelta(days=1), PARIS) + timedelta(hours=8, minutes=30))
        self.assertEqual(cx.lignes(self.desk, CLAIRE), [])                      # hors de « À traiter » jusqu'à l'échéance
        self.desk.db.execute('UPDATE courriels5696_suivis SET echeance=?', ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
        self.desk.db.commit()
        r = cx.lignes(self.desk, CLAIRE)[0]
        self.assertEqual((r['statut'], r['precision']), ('a_traiter', 'Report arrivé à échéance'))
        statut, out = self.decision(fil, 'attendre', de_qui='', jusqua=demain[:10])
        self.assertEqual((out['error'], out['champ']), ('de_qui_requis', 'de_qui'))
        statut, out = self.decision(fil, 'attendre', de_qui='Confrère KERVILAC', jusqua=demain[:10])
        self.assertEqual(out['statut'], 'en_attente')
        self.assertEqual(self.ligne(fil)['suivi']['attente_de'], 'Confrère KERVILAC')

    def test_C02_nouvel_entrant_rouvre_la_meme_ligne_pas_la_resynchronisation(self):
        from agent import courriels5696 as cx
        _, fil, racine = self.entrant('Bail ORLANDEC', quand=self.a(-1, 9, 0))
        self.activer()
        self.decision(fil, 'terminer')
        integration.sync_work_items(self.desk)                                # resynchronisation du même message
        self.assertEqual(cx.lignes(self.desk, CLAIRE), [])
        self.entrant('Re: Bail ORLANDEC', quand=datetime.now(timezone.utc) + timedelta(minutes=1), fil_de=racine)
        rows = cx.lignes(self.desk, CLAIRE)
        self.assertEqual([(r['fil'], r['objet'], r['precision']) for r in rows], [(fil, 'Re: Bail ORLANDEC', 'Nouveau message dans le fil')])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_suivis').fetchone()[0], 1)

    def test_C07_desactiver_ne_rien_effacer_reactiver_retrouve(self):
        from agent import courriels5696 as cx
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        self.decision(fil, 'ecarter', motif='Sans objet')
        statut, out = self.appel('courriels/reglage', {'compte': 'non'})
        self.assertEqual((statut, out['active']), ('200', False))
        self.assertEqual(self.appel('courriels/liste', args={})[1]['error'], 'vue_desactivee')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_suivis').fetchone()[0], 1)
        self.assertNotIn('id="cx96"', self.page(vue='assistante'))
        self.appel('courriels/reglage', {'compte': 'oui'})
        self.assertEqual(self.ligne(fil)['statut'], 'ecarte')
        self.assertEqual(self.appel('courriels/reglage', {'cabinet': True, 'compte': 'cabinet'})[1]['cabinet'], False)   # avocat : le cabinet reste inchangé
        statut, out = self.appel('courriels/reglage', {'cabinet': True, 'compte': 'cabinet'}, owner='cabinet', role='administrateur')
        self.assertEqual((out['cabinet'], cx.active(self.desk, CAMILLE)), (True, True))

    def test_C07_droits_routes_et_export(self):
        from agent import export5688, membres5650 as m, standalone_auth
        self.activer()
        self.assertEqual(self.appel('courriels/liste', args={}, role='assistant')[0], '403')
        self.assertTrue(standalone_auth.allowed('avocat', 'GET', '/api440/m5689/courriels/liste'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m5689/courriels/decision'))
        self.assertEqual(m.category('POST', '/api440/m5689/courriels/preparer'), 'deposer')
        tables = {t[2] for t in export5688.TABLES}
        self.assertTrue({'courriels5696_suivis', 'courriels5696_evenements'} <= tables)

    def test_C03_dossier_reserve_ni_liste_ni_lecture(self):
        from agent import courriels5696 as cx
        _, fil, _ = self.entrant('Demande DURAND', matter='DOS-001')
        self.activer()
        self.serveur.commandes.clear()
        self.assertEqual(cx.lignes(self.desk, CLAIRE, hidden=frozenset({'DOS-001'})), [])
        membres5650.set_viewer({'DOS-001'})
        statut, out = self.appel('courriels/fil', args={'fil': fil})
        self.assertEqual((statut, out['error']), ('403', 'dossier_reserve'))
        self.assertEqual(self.serveur.commandes, [])
        self.assertNotIn('Demande DURAND', self.page(vue='assistante'))


class Fournisseur(BaseVue):
    def test_C03_contrat_de_lecture_sans_capacite_d_ecriture(self):
        from agent import courriels5696 as cx
        self.assertEqual(cx.FournisseurIMAP.CAPACITES, ('lister', 'lire_message', 'lire_fil', 'lire_piece'))
        publiques = {n for n in dir(cx.FournisseurIMAP) if not n.startswith('_')}
        self.assertFalse(publiques & {'envoyer', 'deposer', 'demander_reponse', 'supprimer', 'deplacer', 'marquer'})

    def test_C03_fil_lu_a_la_demande_en_lecture_seule_avec_le_sens(self):
        _, fil, racine = self.entrant('Bail ORLANDEC', quand=self.a(-1, 9, 0))
        self.serveur.ajouter('Sent', 'Cabinet <%s>' % CABINET, 'Re: Bail ORLANDEC', self.a(-1, 12, 0), references=racine)
        self.entrant('Re: Bail ORLANDEC', quand=self.a(0, 10, 0), fil_de=racine)
        self.activer()
        self.serveur.commandes.clear()
        statut, out = self.appel('courriels/fil', args={'fil': fil})
        self.assertEqual(statut, '200')
        self.assertEqual([(m['objet'], m['sens']) for m in out['messages']],
                         [('Bail ORLANDEC', 'recu'), ('Re: Bail ORLANDEC', 'envoye'), ('Re: Bail ORLANDEC', 'recu')])
        self.assert_lecture_seule()

    def test_C03_uid_reutilise_refuse(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        self.serveur.validite['INBOX'] = '8'                                    # boîte réindexée : même UID, autre message possible
        statut, out = self.appel('courriels/message', args={'fil': fil})
        self.assertEqual((statut, out['indisponible'], out['code']), ('200', True, 'message_deplace_ou_boite_reindexee'))
        self.assertNotIn('texte', out)

    def test_C04_html_malveillant_rendu_en_texte(self):
        _, fil, _ = self.entrant('<script>alert(1)</script> DURAND', html=True,
                                 corps='<p>Bonjour</p><img src="https://tracker.example.test/x.png"><script>alert(2)</script>')
        self.activer()
        page = self.page(vue='assistante', fil=fil)
        self.assertNotIn('<script>alert', page)
        self.assertIn('&lt;script&gt;', page)
        statut, out = self.appel('courriels/message', args={'fil': fil})
        self.assertNotIn('<img', out['texte'])
        self.assertNotIn('<script', out['texte'])
        self.assertIn('Bonjour', out['texte'])


class Preparation(BaseVue):
    def test_C04_preparer_un_seul_travail_par_message_et_rien_n_est_envoye(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        statut, out = self.decision(fil, 'preparer', idempotence=cle, version=0, instruction='Proposer un rendez-vous')
        self.assertEqual((statut, out['statut']), ('200', 'en_preparation'))
        self.assertEqual(self.decision(fil, 'preparer', idempotence=cle, version=0)[1]['deja'], True)       # double clic
        statut, out = self.decision(fil, 'preparer', version=0)                                            # autre onglet
        self.assertEqual(statut, '409')
        jobs = self.desk.db.execute("SELECT kind, args FROM jobs WHERE kind='prepare_reply'").fetchall()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(json.loads(jobs[0]['args'])['instruction'], 'Proposer un rendez-vous')
        self.assertEqual(self.ligne(fil)['statut'], 'en_preparation')
        self.assertFalse([c for c in self.serveur.commandes if c[0] in ('STORE', 'APPEND', 'SELECT')])
        self.assertFalse([c for c in self.serveur.commandes if c[0] == 'UID' and c[1] not in ('SEARCH', 'FETCH')])

    def test_C04_echec_de_preparation_rend_l_etat_et_permet_un_nouvel_essai(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        cle = uuid.uuid4().hex
        with patch('agent.mailbox.connect', side_effect=Stop('imap_delai_depasse')):
            statut, out = self.decision(fil, 'preparer', idempotence=cle, version=0)
        self.assertEqual(statut, '400')
        self.assertEqual((self.ligne(fil)['statut'], self.ligne(fil)['version']), ('a_traiter', 0))
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM courriels5696_evenements').fetchone()[0], 0)
        statut, out = self.decision(fil, 'preparer', idempotence=cle, version=0)                           # même page, nouvel essai
        self.assertEqual((statut, out['statut']), ('200', 'en_preparation'))

    def test_C04_annuler_preparer_arrete_le_travail_en_file(self):
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        self.decision(fil, 'preparer', version=0)
        statut, out = self.decision(fil, 'annuler')
        self.assertEqual((statut, out['statut']), ('200', 'a_traiter'))
        self.assertEqual(self.desk.db.execute("SELECT status FROM jobs WHERE kind='prepare_reply'").fetchone()[0], 'cancelled')


class Navigation(BaseVue):
    def test_I03_retour_contextuel_vers_la_liste_et_ses_filtres(self):
        from agent import ui_navigation5689 as nav
        self.assertEqual(nav.retour('/p', '/courriels', {'fil': 'f' * 64}), ('/p/courriels?vue=assistante', 'Retour à la liste'))
        liste = '/p/courriels?vue=assistante&filtre=reportes&dossier=DOS-001'
        self.assertEqual(nav.retour('/p', '/courriels', {'fil': 'f' * 64, 'return_to': liste})[0], liste)
        self.assertIsNone(nav.retour('/p', '/courriels', {'vue': 'assistante'}))
        self.assertIn('/courriels|fil', nav.details_json())
        _, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        page = self.page(vue='assistante', fil=fil, dossier='DOS-001')
        self.assertIn('href="/p/courriels?vue=assistante&amp;dossier=DOS-001">Retour à la liste', page)   # petite largeur : même filtre
        self.assertIn('aria-current="true"', page)
        self.assertIn('data-fil-ouvert="1"', page)

    def test_I03_lien_d_aujourd_hui_par_cle_ouvre_le_meme_fil(self):
        key, fil, _ = self.entrant('Demande DURAND')
        self.activer()
        page = self.page(vue='assistante', cle=key)
        self.assertIn('data-fil="%s"' % fil, page)


if __name__ == '__main__':
    unittest.main()

"""5.6.100 (lot A, R04, V100-01) : droits des courriels — un avocat ne voit rien d'un courriel rattaché à un dossier réservé à un autre compte.

Deux comptes : l'administrateur (A, voit tout) et une avocate (B) pour qui DOS-0002 est réservé à un autre membre. Application réelle
(WSGI, session par cookie), faux IMAP qui journalise chaque commande, données fictives (example.test). Chaque garde a un contre-exemple :
la même demande par A réussit, ou la mutation en mémoire du garde fait réapparaître la fuite.
Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
from email.message import EmailMessage
from pathlib import Path
import json
import sqlite3
from urllib.parse import urlencode
import unittest
from unittest.mock import patch

from fake_imap440 import FakeIMAP
import test_v5648 as fixtures
from agent import acces_courriel56100 as ac, connexion5648, membres5650
from agent.common import Stop, private_json
from agent.messages5697 import cle_canonique

RESERVE = 'DOS-0002'
VISIBLE = 'DOS-0001'
SECRET_OBJET = 'OBJET-ZORVAL-RESERVE'
SECRET_CORPS = 'CORPS-ZORVAL-RESERVE'
SECRET_PROJET = 'PROJET-ZORVAL-RESERVE'


def affaire(i):
    return {'id': f'DOS-{i:04d}', 'client_name': f'Client fictif {i}', 'path': f'/Dossiers/{i}',
            'references': [f'DOS-{i:04d}'], 'aliases': [], 'correspondents': []}


def courriel(objet, mid, corps, in_reply_to='', agent_key='', piece=False):
    m = EmailMessage()
    m['From'], m['To'] = 'expediteur@example.test', 'cabinet@example.test'
    m['Subject'], m['Message-ID'] = objet, mid
    m['Date'] = 'Fri, 02 Oct 2026 08:00:00 +0200'
    if in_reply_to:
        m['In-Reply-To'] = in_reply_to
    if agent_key:
        m['X-AxiorHub-Draft-Key'] = agent_key
    m.set_content(corps)
    if piece:
        m.add_attachment(b'piece fictive ' + corps.encode(), maintype='text', subtype='plain', filename='piece.txt')
    return m.as_bytes()


class Droits(unittest.TestCase):
    def setUp(self):
        self.b = fixtures.CabinetAccounts()
        self.b.setUp()
        self.addCleanup(self.b.doCleanups)
        self.addCleanup(membres5650.clear)
        self.addCleanup(self.b.desk.db.close)
        self.desk = self.b.desk
        self.state = Path(self.b.f.c['state_dir'])
        private_json(self.b.f.matter_file, [affaire(1), affaire(2)])
        membres5650.set_reserve(self.b.f.c['state_dir'], RESERVE, ['titulaire@example.test'])
        self.cookie = self.b.first_login('avocate@example.test', self.b.add(self.b.admin_cookie()))
        self.cfg = self.b.f.c['mail']
        self.compte = self.cfg['username'] + '@' + self.cfg['host']
        # message réservé (rattaché par le rapport de l'agent) et message libre, dans la réception
        self.uid_r = FakeIMAP.add('INBOX', courriel(SECRET_OBJET, '<reserve-56100@example.test>', SECRET_CORPS, piece=True))
        self.cle_r = self.cle('INBOX', self.uid_r)
        self.rapport(self.cle_r, RESERVE, self.uid_r)
        self.uid_l = FakeIMAP.add('INBOX', courriel('Objet libre fictif', '<libre-56100@example.test>', 'Corps libre fictif.'))
        self.cle_l = self.cle('INBOX', self.uid_l)
        FakeIMAP.log.clear()

    # -------------------------------------------------------------------------------------------- outils
    def cle(self, boite, uid):
        return cle_canonique(self.compte, boite, FakeIMAP.validity[boite], uid)

    def rapport(self, cle, matter, uid, boite='INBOX'):
        p = self.state / 'reports'
        p.mkdir(exist_ok=True)
        private_json(p / (cle + '.json'), {'subject': SECRET_OBJET if matter == RESERVE else 'Objet libre fictif', 'matter': matter,
                                           'source_uid': str(uid), 'source_mailbox': boite, 'sender': 'expediteur@example.test'})

    def projet(self, cle, matter='', corps=SECRET_PROJET):
        self.desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES (?,?,?)',
                             (cle, json.dumps({'matter': matter, 'result': {'body': corps, 'requires_decision': True, 'limits': []}}), self.desk.now()))
        self.desk.db.commit()

    def lien(self, cle, matter, ident='lien-56100'):
        self.desk.db.execute('INSERT OR REPLACE INTO portfolio_mail_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (ident, cle, '<x@fictif.test>', '', 'INBOX', '1', matter, 100, 'certain', 'manuel', 'a@example.test', '',
                              self.desk.now(), 'confirmed', self.desk.now()))
        self.desk.db.commit()

    def env_b(self):
        return {'HTTP_COOKIE': f'{connexion5648.COOKIE}={self.cookie}'}

    def b_get(self, path, **args):
        return self.b.call(path, auth=False, query=urlencode(args), extra=self.env_b())

    def b_post(self, route, body):
        return self.b.call('/api440/' + route, 'POST', body, auth=False, extra=self.env_b())

    def a_get(self, path, **args):
        return self.b.call(path, query=urlencode(args))

    def a_post(self, route, body):
        return self.b.call('/api440/' + route, 'POST', body)

    @staticmethod
    def corps_lus():
        return [x for x in FakeIMAP.log if x[:2] == ('UID', 'FETCH') and x[-1] == '(BODY.PEEK[])']

    @staticmethod
    def effets():
        return [x for x in FakeIMAP.log if x[:2] in (('UID', 'STORE'), ('UID', 'COPY'), ('UID', 'MOVE'), ('UID', 'EXPUNGE')) or x[0] == 'APPEND']

    def assert_cache(self, r, *textes):
        corps = r['body'] if isinstance(r['body'], bytes) else r['body'].encode()
        for texte in textes or (SECRET_OBJET, SECRET_CORPS, SECRET_PROJET):
            self.assertNotIn(texte.encode(), corps)

    # -------------------------------------------------------------------------------------------- R04 : routes de lecture
    def test_R04_V100_01_projet_sans_dossier_herite_du_message_reserve(self):
        """Réponse préparée sans dossier, message ensuite rattaché à un dossier réservé : /mail, fragment d'Aujourd'hui, m5671 → 404 pour B."""
        self.projet(self.cle_r, '')
        for r in (self.b_get('/mail', key=self.cle_r), self.b_get('/api440/m5689/accueil/action', nature='reponse', cle=self.cle_r)):
            self.assertTrue(r['status'].startswith('404'), r['status'])
            self.assert_cache(r)
        for route, body in (('m5671/reponse/ecarter', {'key': self.cle_r}), ('m5671/reponse/deposer', {'key': self.cle_r, 'corps': 'Texte modifié'}),
                            ('m5671/reponse/garder', {'key': self.cle_r, 'choix': 1, 'variante': 'A', 'version': 'x'})):
            with self.subTest(route=route):
                r = self.b_post(route, body)
                self.assertTrue(r['status'].startswith('404'), (r['status'], r['body']))
                self.assert_cache(r)
        self.assertIsNotNone(self.desk.db.execute('SELECT 1 FROM manual_drafts WHERE mail_key=?', (self.cle_r,)).fetchone())   # rien écarté
        self.assertIn(SECRET_PROJET.encode(), self.a_get('/mail', key=self.cle_r)['body'])                                   # contre-épreuve A
        self.assertEqual(self.corps_lus(), [])

    def test_R04_projet_reserve_rend_le_message_reserve(self):
        """Message sans dossier dont le projet de réponse est enregistré dans un dossier réservé : objet, corps et pièce refusés à B."""
        self.rapport(self.cle_l, '', self.uid_l)
        self.projet(self.cle_l, RESERVE)
        r = self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))
        self.assertTrue(r['status'].startswith('404'), r['status'])
        self.assertTrue(self.b_get('/mail', key=self.cle_l)['status'].startswith('404'))
        self.assertEqual(self.corps_lus(), [])
        self.assertTrue(self.a_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))['status'].startswith('200'))

    def test_R04_meme_404_pour_absent_et_interdit(self):
        absente = 'f' * 64
        paires = ((('/mail', {'key': absente}), ('/mail', {'key': self.cle_r})),
                  (('/api440/m5625/boite/courriel', {'uid': '999'}), ('/api440/m5625/boite/courriel', {'uid': str(self.uid_r)})),
                  (('/api440/m5625/boite/piece', {'uid': '999', 'index': '0'}), ('/api440/m5625/boite/piece', {'uid': str(self.uid_r), 'index': '0'})),
                  (('/api440/m5628/modele', {'mode': 'repondre', 'uid': '999'}), ('/api440/m5628/modele', {'mode': 'repondre', 'uid': str(self.uid_r)})),
                  (('/api440/m5689/accueil/action', {'nature': 'reponse', 'cle': absente}),
                   ('/api440/m5689/accueil/action', {'nature': 'reponse', 'cle': self.cle_r})))
        self.projet(self.cle_r, '')
        for (p1, a1), (p2, a2) in paires:
            with self.subTest(route=p1):
                r1, r2 = self.b_get(p1, **a1), self.b_get(p2, **a2)
                self.assertEqual(r1['status'], r2['status'])
                self.assertTrue(r2['status'].startswith('404'), (p2, r2['status']))
                sans_cle = lambda r, *cles: r['body'].replace(*[(c.encode(), b'') for c in cles][0]) if cles else r['body']
                self.assertEqual(sans_cle(r1, *[v for v in a1.values() if len(v) == 64]), sans_cle(r2, *[v for v in a2.values() if len(v) == 64]))   # même page, à l'écho de l'adresse près
                self.assert_cache(r2)
        r1, r2 = self.b_post('m5671/reponse/ecarter', {'key': absente}), self.b_post('m5671/reponse/ecarter', {'key': self.cle_r})
        self.assertEqual((r1['status'], r1['body']), (r2['status'], r2['body']))
        self.assertTrue(r2['status'].startswith('404'))
        self.assertEqual(self.corps_lus(), [])

    def test_R04_zero_fetch_du_corps_pour_B(self):
        """Lecture, pièce, citation pour répondre ou transférer, actions et brouillon : aucune commande ne lit le corps du message réservé."""
        for path, args in (('/api440/m5625/boite/courriel', {'uid': str(self.uid_r)}), ('/api440/m5625/boite/piece', {'uid': str(self.uid_r), 'index': '0'}),
                           ('/api440/m5628/modele', {'mode': 'repondre', 'uid': str(self.uid_r)}),
                           ('/api440/m5628/modele', {'mode': 'transferer', 'uid': str(self.uid_r)})):
            self.assertTrue(self.b_get(path, **args)['status'].startswith('404'), path)
        for action in ('lu', 'archive', 'trash'):
            r = self.b_post('m5628/action', {'action': action, 'uid': str(self.uid_r)})
            self.assertTrue(r['status'].startswith('404'), (action, r['body']))
        for mode in ('repondre', 'transferer'):
            r = self.b_post('m5628/brouillon', {'mode': mode, 'source_uid': str(self.uid_r), 'to': 'x@example.test', 'subject': 's', 'html': '<p>x</p>', 'keep': [0]})
            self.assertTrue(r['status'].startswith('404'), (mode, r['body']))
        r = self.b_post('m5625/boite/repondre', {'uid': str(self.uid_r), 'sans_dossier': 'oui'})
        self.assertTrue(r['status'].startswith('404'), r['body'])
        self.assertEqual(self.corps_lus(), [])
        self.assertEqual(self.effets(), [])                                                       # ni STORE, ni MOVE/COPY, ni APPEND
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='prepare_reply'").fetchone())
        # contre-épreuve : la même lecture par A lit le corps
        self.assertTrue(self.a_get('/api440/m5625/boite/courriel', uid=str(self.uid_r))['status'].startswith('200'))
        self.assertTrue(self.corps_lus())

    def test_R04_dossier_choisi_a_l_ecran_ne_donne_aucun_droit(self):
        self.rapport(self.cle_l, '', self.uid_l)
        r = self.b_post('m5625/boite/repondre', {'uid': str(self.uid_l), 'matter': RESERVE})
        self.assertFalse(r['status'].startswith('200'), r['body'])
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM jobs WHERE kind='prepare_reply'").fetchone())
        with self.assertRaises(Stop) as ctx:
            ac.exiger_contexte(self.desk, ('avocate@example.test', 'avocat'), self.cle_l, RESERVE)
        self.assertEqual(str(ctx.exception), 'introuvable')
        ac.exiger_contexte(self.desk, ('avocate@example.test', 'avocat'), self.cle_l, VISIBLE)   # dossier visible : passe

    # -------------------------------------------------------------------------------------------- IMAP : boîte, UID, UIDVALIDITY
    def test_R04_autre_boite_et_uid_reutilise(self):
        """Même numéro d'UID dans une autre boîte : l'identité est (compte, boîte, UIDVALIDITY, UID), jamais l'UID seul."""
        uid_t = FakeIMAP.add('Trash', courriel(SECRET_OBJET, '<corbeille-56100@example.test>', SECRET_CORPS))
        self.rapport(self.cle('Trash', uid_t), RESERVE, uid_t, 'Trash')
        self.assertTrue(self.b_get('/api440/m5625/boite/courriel', uid=str(uid_t))['status'].startswith('200'))     # INBOX/uid_t : libre
        r = self.b_get('/api440/m5625/boite/courriel', uid=str(uid_t), dossier='Trash')
        self.assertTrue(r['status'].startswith('404'), r['status'])
        self.assert_cache(r)
        self.assertNotIn(('UID', 'FETCH', str(uid_t), '(BODY.PEEK[])'), [x for x in FakeIMAP.log if 'Trash' in str(x)])
        self.assertTrue(self.a_get('/api440/m5625/boite/courriel', uid=str(uid_t), dossier='Trash')['status'].startswith('200'))

    def test_R04_uidvalidity_changee_refuse_lecture_et_action(self):
        vue = FakeIMAP.validity['INBOX']
        self.assertTrue(self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l), uidvalidity=vue)['status'].startswith('200'))
        FakeIMAP.validity['INBOX'] = str(int(vue) + 1)                     # boîte réindexée : l'UID affiché peut désigner un autre message
        FakeIMAP.log.clear()
        for r in (self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l), uidvalidity=vue),
                  self.a_get('/api440/m5625/boite/courriel', uid=str(self.uid_l), uidvalidity=vue),
                  self.b_post('m5628/action', {'action': 'lu', 'uid': str(self.uid_l), 'uidvalidity': vue}),
                  self.a_post('m5628/action', {'action': 'archive', 'uid': str(self.uid_l), 'uidvalidity': vue}),
                  self.b_get('/api440/m5628/modele', mode='repondre', uid=str(self.uid_l), uidvalidity=vue)):
            self.assertTrue(r['status'].startswith('404'), (r['status'], r['body']))
        self.assertEqual((self.corps_lus(), self.effets()), ([], []))

    def test_R04_rattachement_change_et_droits_retires_entre_liste_et_action(self):
        liste = json.loads(self.b_get('/api440/m5625/boite/liste')['body'])
        self.assertIn(str(self.uid_l), [str(x['uid']) for x in liste['items']])
        self.assertNotIn(str(self.uid_r), [str(x['uid']) for x in liste['items']])                  # filtré avant la page
        self.assertNotIn(SECRET_OBJET, json.dumps(liste))
        self.lien(self.cle_l, RESERVE)                                                                # reclassé après l'affichage
        FakeIMAP.log.clear()
        for r in (self.b_post('m5628/action', {'action': 'lu', 'uid': str(self.uid_l)}), self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))):
            self.assertTrue(r['status'].startswith('404'), r['body'])
        self.assertEqual((self.corps_lus(), self.effets()), ([], []))
        # droit retiré : un dossier visible devient réservé entre la liste et l'action
        uid_v = FakeIMAP.add('INBOX', courriel('Objet visible fictif', '<visible-56100@example.test>', 'Corps visible fictif.'))
        self.rapport(self.cle('INBOX', uid_v), VISIBLE, uid_v)
        self.assertTrue(self.b_get('/api440/m5625/boite/courriel', uid=str(uid_v))['status'].startswith('200'))
        membres5650.set_reserve(self.b.f.c['state_dir'], VISIBLE, ['titulaire@example.test'])
        FakeIMAP.log.clear()
        r = self.b_post('m5628/action', {'action': 'archive', 'uid': str(uid_v)})
        self.assertTrue(r['status'].startswith(('404', '403')), r['body'])                             # 403 : contrôle central du dossier désigné
        self.assertEqual(self.effets(), [])

    # -------------------------------------------------------------------------------------------- brouillons IMAP
    def brouillon(self, objet, corps, in_reply_to='', agent_key=''):
        uid = FakeIMAP.add('Drafts', courriel(objet, '<brouillon-%s@example.test>' % objet.lower().replace(' ', '-'), corps, in_reply_to, agent_key), ['\\Draft'])
        return uid, FakeIMAP.validity['Drafts']

    def test_R04_brouillon_herite_du_message_d_origine(self):
        uid, v = self.brouillon('Re brouillon reserve', SECRET_PROJET, in_reply_to='<reserve-56100@example.test>')
        uid_ok, _ = self.brouillon('Re brouillon libre', 'Texte libre fictif.', in_reply_to='<libre-56100@example.test>')
        uid_seul, _ = self.brouillon('Brouillon sans origine', 'Texte sans origine fictif.')
        FakeIMAP.log.clear()
        liste = json.loads(self.b_get('/api440/drafts')['body'])
        uids = [x['uid'] for x in liste['items']]
        self.assertNotIn(str(uid), uids)
        self.assertIn(str(uid_ok), uids)
        self.assertIn(str(uid_seul), uids)                                    # vrai brouillon sans dossier : visible
        for r in (self.b_get('/api440/draft', uid=str(uid), validity=v),
                  self.b_post('draft/save', {'uid': str(uid), 'uidvalidity': v, 'revision': 'x', 'to': 'x@example.test', 'subject': 's', 'body': 'b'}),
                  self.b_post('draft/discard', {'uid': str(uid), 'uidvalidity': v, 'confirm': 'yes'}),
                  self.b_post('m5625/boite/supprimer', {'uid': str(uid), 'uidvalidity': v})):
            self.assertTrue(r['status'].startswith('404'), (r['status'], r['body']))
            self.assert_cache(r)
        self.assertEqual((self.corps_lus(), self.effets()), ([], []))
        self.assertIn(uid, FakeIMAP.folders['Drafts'])
        self.assertTrue(self.b_get('/api440/draft', uid=str(uid_ok), validity=v)['status'].startswith('200'))
        self.assertIn(SECRET_PROJET.encode(), self.a_get('/api440/draft', uid=str(uid), validity=v)['body'])   # A : lu

    def test_R04_brouillon_origine_declaree_non_verifiable(self):
        """Lien déclaré (In-Reply-To ou clé de l'agent) introuvable : jamais lu comme « sans dossier » pour un compte cloisonné."""
        uid, v = self.brouillon('Re origine inconnue', SECRET_PROJET, in_reply_to='<inconnu-56100@example.test>')
        uid2, _ = self.brouillon('Re cle inconnue', SECRET_PROJET, agent_key='e' * 64)
        FakeIMAP.log.clear()
        uids = [x['uid'] for x in json.loads(self.b_get('/api440/drafts')['body'])['items']]
        self.assertNotIn(str(uid), uids)
        self.assertNotIn(str(uid2), uids)
        for u in (uid, uid2):
            self.assertTrue(self.b_get('/api440/draft', uid=str(u), validity=v)['status'].startswith('404'))
        self.assertEqual(self.corps_lus(), [])
        self.assertTrue(self.a_get('/api440/draft', uid=str(uid), validity=v)['status'].startswith('200'))

    def test_R04_brouillon_de_l_agent_par_sa_liaison_serveur(self):
        dk = 'd' * 64
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS reply_drafts5687(draft_key TEXT PRIMARY KEY, mail_key TEXT NOT NULL)')
        self.desk.db.execute('INSERT INTO reply_drafts5687 VALUES(?,?)', (dk, self.cle_r))
        self.desk.db.commit()
        uid, v = self.brouillon('Re agent', SECRET_PROJET, agent_key=dk)          # aucun In-Reply-To : seule la liaison serveur relie
        self.assertNotIn(str(uid), [x['uid'] for x in json.loads(self.b_get('/api440/drafts')['body'])['items']])
        self.assertTrue(self.b_get('/api440/draft', uid=str(uid), validity=v)['status'].startswith('404'))

    def test_R04_reponses_listees_resolues_une_a_une(self):
        """Page du courriel libre : un brouillon qui lui répond mais rattaché par ailleurs à un dossier réservé n'est pas listé."""
        uid_a, _ = self.brouillon('Re libre un', 'Texte un fictif.', in_reply_to='<libre-56100@example.test>')
        uid_b, _ = self.brouillon('Re libre deux', SECRET_PROJET, in_reply_to='<libre-56100@example.test>')
        self.lien(self.cle('Drafts', uid_b), RESERVE)
        data = json.loads(self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))['body'])
        self.assertEqual([x['uid'] for x in data['replies']], [str(uid_a)])
        data = json.loads(self.a_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))['body'])
        self.assertEqual(sorted(x['uid'] for x in data['replies']), sorted([str(uid_a), str(uid_b)]))

    def test_R04_envoi_brouillon_reserve_refuse_avant_apercu(self):
        uid, v = self.brouillon('Re envoi reserve', SECRET_PROJET, in_reply_to='<reserve-56100@example.test>')
        r = self.b_post('send/prepare', {'uid': str(uid), 'uidvalidity': v})
        self.assertTrue(r['status'].startswith('404'), r['body'])
        self.assert_cache(r)
        self.assertEqual(self.corps_lus(), [])

    # -------------------------------------------------------------------------------------------- anciennes actions, compteurs
    def test_R04_ancienne_action_refusee_sans_effet(self):
        self.rapport(self.cle_l, '', self.uid_l)
        avant = self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        for cle in (self.cle_r, 'f' * 64):
            r = self.b.request('/action', 'POST', {'csrf': 'test-csrf', 'action': 'prepare_draft', 'key': cle, 'style': 'prudent'}, cookie=self.cookie)
            self.assertTrue(r['status'].startswith('404'), r['status'])
            self.assertNotIn(SECRET_OBJET, r['body'])
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], avant)
        r = self.b.request('/action', 'POST', {'csrf': 'test-csrf', 'action': 'prepare_draft', 'key': self.cle_l, 'style': 'prudent'}, cookie=self.cookie)
        self.assertTrue(r['status'].startswith('303'), r['status'])                                     # contre-épreuve : courriel visible

    def test_R04_compteurs_et_files_sans_l_objet_reserve(self):
        from agent import reponses5671, cockpit530, livrables5689
        self.projet(self.cle_r, '')
        self.projet(self.cle_l, '', 'Projet libre fictif')
        self.rapport(self.cle_l, '', self.uid_l)
        caches = frozenset({RESERVE})
        with patch('agent.membres5650.viewer_hidden', return_value=caches):
            objets = [c['object'] for c in reponses5671.queue_items(self.desk, '/p')]
        self.assertEqual(objets, ['reponse5671:' + self.cle_l])
        # brouillons de la copie locale (Aujourd'hui, « À relire ») : origine vérifiée sans IMAP
        self.desk.setting('cockpit530:drafts', {'at': 9e18, 'ok': True, 'validity': '7', 'items': [
            {'uid': '41', 'subject': SECRET_OBJET, 'to': 'x@example.test', 'date': '2026-10-02T08:00:00+00:00', 'agent_key': '', 'message_id': '', 'in_reply_to': '<reserve-56100@example.test>', 'agent': False},
            {'uid': '42', 'subject': 'Brouillon libre fictif', 'to': 'x@example.test', 'date': '2026-10-02T08:00:00+00:00', 'agent_key': '', 'message_id': '', 'in_reply_to': '', 'agent': False}]})
        self.lien(self.cle_r, RESERVE)
        self.desk.db.execute('UPDATE portfolio_mail_links SET message_id=? WHERE id=?', ('<reserve-56100@example.test>', 'lien-56100'))
        self.desk.db.commit()
        with patch('agent.membres5650.viewer_hidden', return_value=caches):
            titres = [x['title'] for x in cockpit530.review_items(self.desk, cached_only=True) if x['type'] in ('Courriel', 'Note interne')]
        self.assertEqual(titres, ['Brouillon libre fictif'])
        titres = [x['title'] for x in cockpit530.review_items(self.desk, cached_only=True) if x['type'] in ('Courriel', 'Note interne')]
        self.assertEqual(sorted(titres), sorted(['Brouillon libre fictif', SECRET_OBJET]))             # sans requête (worker) : inchangé
        # Produire / registre des livrables : la ligne « courriel » du projet sans dossier n'est ni comptée ni lisible
        livrables5689.synchroniser(self.desk)
        vus = livrables5689.projection(self.desk, caches, {'voir_honoraires': True}, sync=False, limite=100)
        reponses = [x for x in vus['items'] if x.get('response_id')]
        self.assertNotIn(self.cle_r, [x['response_id'] for x in reponses])
        self.assertNotIn(SECRET_OBJET, json.dumps(vus, ensure_ascii=False))
        tous = livrables5689.projection(self.desk, frozenset(), {'voir_honoraires': True}, sync=False, limite=100)
        self.assertIn(self.cle_r, [x.get('response_id') for x in tous['items']])                      # contre-épreuve : présente sans masque
        doc = next(x for x in tous['items'] if x.get('response_id') == self.cle_r)
        with self.assertRaises(Stop):
            livrables5689.charger(self.desk, doc['document_id'], caches)

    # -------------------------------------------------------------------------------------------- Gmail, panne, contrat
    def test_R04_gmail_connexion_autorisee_et_rattachements(self):
        from agent import gmail5697 as gm
        gm.ensure_schema(self.desk)
        cols = [r[1] for r in self.desk.db.execute('PRAGMA table_info(google5697_connexions)')]
        valeurs = {c: '' for c in cols}
        valeurs.update({'id': 'cx-b', 'owner': 'avocate@example.test', 'usage': 'gmail'})
        for c in cols:
            if c not in ('id', 'owner', 'usage') and valeurs[c] == '':
                valeurs[c] = 0 if c in ('generation', 'version') else ''
        try:
            self.desk.db.execute('INSERT INTO google5697_connexions(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), [valeurs[c] for c in cols])
            valeurs.update({'id': 'cx-autre', 'owner': 'titulaire@example.test'})
            self.desk.db.execute('INSERT INTO google5697_connexions(%s) VALUES(%s)' % (','.join(cols), ','.join('?' * len(cols))), [valeurs[c] for c in cols])
        except sqlite3.IntegrityError as ex:
            self.skipTest('schéma Gmail : ' + str(ex))
        b = ('avocate@example.test', 'avocat')
        with self.assertRaises(Stop) as ctx:                                  # connexion d'une autre personne : absente
            ac.reference(self.desk, b, 'gmail:cx-autre:m1')
        self.assertEqual(str(ctx.exception), 'introuvable')
        with self.assertRaises(Stop):                                         # message inconnu de la copie locale : absent
            ac.reference(self.desk, b, 'gmail:cx-b:m1')
        from agent.messages5697 import cle_gmail
        self.lien(cle_gmail('cx-b', 'm1'), RESERVE, 'lien-gmail')
        ref = ac._ref('gmail', cle_gmail('cx-b', 'm1'))
        with self.assertRaises(Stop):
            ac.exiger(self.desk, b, ref)
        ac.exiger(self.desk, ('cabinet', 'administrateur'), ref)

    def test_R04_panne_du_controle_503_sans_lecture(self):
        def panne(*a, **k):
            raise sqlite3.DatabaseError('panne fictive')
        with patch('agent.messages5697.dossiers_par_cle', panne):
            r = self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))
            self.assertTrue(r['status'].startswith('503'), r['status'])
            r = self.b_get('/api440/drafts')
            self.assertTrue(r['status'].startswith('503'), r['status'])
            with self.assertRaises(Stop) as ctx:
                ac.filtrer(self.desk, ('avocate@example.test', 'avocat'), [self.cle_l])
            self.assertEqual(str(ctx.exception), 'controle_indisponible')
        self.assertEqual(self.corps_lus(), [])

    def test_R04_contrat_reference_exiger_filtrer(self):
        b, a = ('avocate@example.test', 'avocat'), ('cabinet', 'administrateur')
        self.projet(self.cle_l, '', 'Projet libre fictif')
        self.assertEqual(ac.reference(self.desk, b, 'projet:' + self.cle_l).genre, 'projet')
        self.assertEqual(ac.reference(self.desk, b, self.cle_r).genre, 'imap')
        with self.assertRaises(Stop) as ctx:
            ac.reference(self.desk, b, 'f' * 64)
        self.assertEqual(str(ctx.exception), 'introuvable')
        with self.assertRaises(Stop) as ctx:
            ac.exiger(self.desk, b, ac.reference(self.desk, a, self.cle_r))
        self.assertEqual(str(ctx.exception), 'introuvable')
        ac.exiger(self.desk, a, self.cle_r)
        elements = [{'mail_key': self.cle_r, 'matter': ''}, {'mail_key': self.cle_l, 'matter': ''}, {'object': 'x', 'matter': RESERVE}, {'object': 'y', 'matter': ''}]
        self.assertEqual(ac.filtrer(self.desk, b, elements), [elements[1], elements[3]])
        self.assertEqual(ac.filtrer(self.desk, a, elements), elements)
        self.assertEqual(ac.cles_masquees(self.desk, b, [self.cle_r, self.cle_l]), frozenset({self.cle_r}))
        with self.assertRaises(Stop) as ctx:                                   # acteur absent : jamais « aucune restriction »
            ac.exiger(self.desk, ('', ''), self.cle_l)
        self.assertEqual(str(ctx.exception), 'controle_indisponible')

    # -------------------------------------------------------------------------------------------- mutations : le test voit la fuite
    def test_R04_mutation_garde_retiree_la_fuite_reapparait(self):
        """Contre-exemple : sans le garde commun (mutation en mémoire), la page historique et la lecture IMAP montrent l'objet réservé."""
        self.projet(self.cle_r, '')
        with patch('agent.acces_courriel56100.exiger', lambda *a, **k: None):
            self.assertIn(SECRET_OBJET.encode(), self.b_get('/mail', key=self.cle_r)['body'])
        with patch('agent.acces_courriel56100.lire_imap', lambda desk, acteur, box, cfg, boite, uid, *a, **k: (box.fetch(boite, uid), None)):
            r = self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_r))
            self.assertIn(SECRET_CORPS.encode(), r['body'])
        self.assertTrue(self.corps_lus())

    def test_R04_mutation_projet_ignore_la_fuite_reapparait(self):
        """Contre-exemple : si le dossier du projet n'était pas lu (``messages5697._projets`` neutralisé), le message libre dont le projet est
        réservé redeviendrait lisible par B."""
        self.rapport(self.cle_l, '', self.uid_l)
        self.projet(self.cle_l, RESERVE)
        self.assertTrue(self.b_get('/mail', key=self.cle_l)['status'].startswith('404'))
        with patch('agent.messages5697._projets', lambda desk, cles: {}):
            self.assertTrue(self.b_get('/api440/m5625/boite/courriel', uid=str(self.uid_l))['status'].startswith('200'))

    def test_R04_mutation_brouillons_non_filtres_la_fuite_reapparait(self):
        uid, _ = self.brouillon('Re brouillon reserve', SECRET_PROJET, in_reply_to='<reserve-56100@example.test>')
        with patch('agent.acces_courriel56100.filtrer_brouillons', lambda desk, acteur, cfg, listing, box=None: listing):
            self.assertIn(str(uid), [x['uid'] for x in json.loads(self.b_get('/api440/drafts')['body'])['items']])


if __name__ == '__main__':
    unittest.main()

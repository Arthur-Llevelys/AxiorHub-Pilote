"""5.6.106 (lot L) : « Courriels » en vrai client de messagerie — actions groupées, dossiers IMAP, tri et filtres, recherche étendue, page.

Application réelle (WSGI) et vraie ``Mailbox`` branchée sur le faux serveur IMAP ``faux_imap56106`` (journal de chaque commande, aucun
réseau, données fictives example.test). Chaque garde-fou (UIDVALIDITY, droits, borne, idempotence, constat de l'effet, dossiers spéciaux,
confirmation d'un dossier non vide) a un contre-exemple : la mutation en mémoire du garde fait apparaître l'effet interdit.
Exécuter avec PYTHONPATH=<racine AxiorHub>:<racine AxiorHub>/tests.
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import boite56106 as L, boite5625, mailbox
from agent.common import Stop, private_json
from agent.desk import Desk
from agent.messages5697 import cle_canonique
from agent.web import App
from faux_imap56106 import Serveur, message, mutf7
import test_agent as fixtures

ROOT = Path(__file__).resolve().parents[1]
MATTERS = [{'id': 'DOS-001', 'client_name': 'DURAND', 'path': '/Dossiers/DURAND', 'references': ['DOS-001', 'RG 24/01234'], 'aliases': ['DURAND'],
            'correspondents': [{'email': 'durand@example.test', 'role': 'client'}]},
           {'id': 'DOS-002', 'client_name': 'MARTIN', 'path': '/Dossiers/MARTIN', 'references': ['DOS-002'], 'aliases': ['MARTIN'], 'correspondents': []}]


class Base(unittest.TestCase):
    CAPACITES = ('IMAP4REV1', 'UIDPLUS', 'MOVE')
    SEP = '/'

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        private_json(self.f.matter_file, MATTERS)
        secret = self.f.base / 'imap.secret'
        secret.write_text('mot-de-passe-fictif')
        secret.chmod(0o600)                                  # Linux : read_secret refuse un secret lisible par d'autres
        self.f.c['mail'].update({'password_file': str(secret), 'username': 'cabinet', 'host': 'mail.example.test'})
        self.config = self.f.base / 'config.json'
        private_json(self.config, self.f.c)
        self.auth = self.f.base / 'auth.json'
        salt = 'aa' * 16
        self.password = 'password-test-only-long'
        self.origin = 'https://cabinet.example.test'
        private_json(self.auth, {'username': 'admin', 'salt': salt, 'origin': self.origin, 'prefix': '/agent-courriel', 'csrf': 'test-csrf',
                                 'hash': hashlib.scrypt(self.password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()})
        self.s = Serveur(separateur=self.SEP, capacites=self.CAPACITES)
        self.s.semer(12)
        p = patch('agent.mailbox.connect', self.s.connexion)
        p.start()
        self.addCleanup(p.stop)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.app = App(str(self.config), str(self.auth))
        self.validite = self.s.boites['INBOX']['validite']

    def call(self, path, method='GET', body=None, query='', csrf=True):
        raw = json.dumps(body).encode() if body is not None else b''
        env = {'REQUEST_METHOD': method, 'PATH_INFO': '/agent-courriel' + path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)),
               'CONTENT_TYPE': 'application/json', 'HTTP_ORIGIN': self.origin,
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        if csrf:
            env['HTTP_X_CSRF_TOKEN'] = 'test-csrf'
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers))
        corps = b''.join(self.app(env, start))
        out['body'] = corps.decode('utf-8', 'replace')
        try:
            out['json'] = json.loads(out['body'])
        except ValueError:
            out['json'] = None
        return out

    def commandes(self, nom):
        return [c for c in self.s.journal if len(c) > 1 and c[0] == 'UID' and c[1] == nom]

    def agir(self, action, uids, cle='cle-essai-0001', **extra):
        corps = {'action': action, 'dossier': extra.pop('dossier', ''), 'uidvalidity': extra.pop('uidvalidity', self.validite),
                 'items': [{'uid': str(u)} for u in uids], 'cle': cle}
        corps.update(extra)
        return L.actions(self.desk, ('cabinet', 'administrateur'), corps, 'cabinet')


class ActionsGroupees(Base):
    def test_L1_une_commande_par_dossier_et_drapeaux_constates(self):
        out = self.agir('lu', [3, 6, 9])
        stores = self.commandes('STORE')
        self.assertEqual(len(stores), 1)                                     # une commande pour l'ensemble, pas une par message
        self.assertEqual(stores[0][2], '3,6,9')
        self.assertTrue(all('\\Seen' in self.s.boites['INBOX']['uids'][u][0] for u in (3, 6, 9)))
        self.assertEqual((out['reussis'], out['echecs']), (3, []))
        self.assertEqual(out['message'], '3 marqués lus.')
        self.assertEqual(len([c for c in self.s.journal if c[:2] == ('UID', 'FETCH')]), 1)   # constat : drapeaux relus au serveur

    def test_L1_corbeille_bilan_exact_avec_courriels_deja_supprimes(self):
        out = self.agir('trash', [1, 2, 3, 98, 99])
        self.assertEqual(len(self.commandes('MOVE')), 1)
        self.assertEqual(self.commandes('MOVE')[0][2], '1:3')                  # plage compacte
        self.assertEqual(len(self.s.boites['Trash']['uids']), 3)
        self.assertEqual(out['reussis'], 3)
        self.assertEqual(out['message'], '3 déplacés dans la Corbeille, 2 en échec (déjà déplacé ou supprimé).')

    def test_L1_sans_MOVE_copie_puis_effacement_en_une_commande_chacun(self):
        self.s.capacites = ('IMAP4REV1', 'UIDPLUS')
        out = self.agir('move', [4, 5], cible='Archives')
        self.assertEqual([len(self.commandes(c)) for c in ('MOVE', 'COPY', 'EXPUNGE')], [0, 1, 1])
        self.assertEqual(len([c for c in self.commandes('STORE') if c[3] == '+FLAGS.SILENT']), 1)
        self.assertEqual((out['reussis'], set(self.s.boites['INBOX']['uids']) & {4, 5}), (2, set()))
        self.assertEqual(len(self.s.boites['Archives']['uids']), 2)

    def test_L1_uidvalidity_changee_rien_nest_fait(self):
        with self.assertRaises(Stop) as ctx:
            self.agir('trash', [1, 2], uidvalidity='999')
        self.assertEqual(str(ctx.exception), 'boite_reindexee')
        self.assertEqual(self.commandes('MOVE') + self.commandes('STORE'), [])
        r = self.call('/api440/m56106/boite/actions', 'POST', {'action': 'trash', 'uidvalidity': '999', 'items': [{'uid': '1'}], 'cle': 'cle-essai-0002'})
        self.assertTrue(r['status'].startswith('409'), r['status'])
        self.assertIn('rien n’a été fait', r['json']['message'])
        # contre-exemple : sans le contrôle de l'UIDVALIDITY, les UID d'une boîte réindexée seraient déplacés en silence
        corps = {'action': 'trash', 'uidvalidity': '', 'items': [{'uid': '1'}], 'cle': 'cle-essai-0003'}
        L.actions(self.desk, ('cabinet', 'administrateur'), corps, 'cabinet')
        self.assertEqual(len(self.commandes('MOVE')), 1)

    def test_L1_borne_de_200_courriels(self):
        with self.assertRaises(Stop) as ctx:
            self.agir('lu', range(1, 202))
        self.assertEqual(str(ctx.exception), 'trop_de_courriels')
        self.assertEqual(self.commandes('STORE'), [])
        self.agir('lu', range(1, 201), cle='cle-essai-0200')               # 200 : accepté (en une commande)
        self.assertEqual(len(self.commandes('STORE')), 1)

    def test_L1_idempotence_meme_cle_meme_reponse_autre_demande_refusee(self):
        a = self.agir('trash', [1, 2], cle='cle-idem-00001')
        b = self.agir('trash', [1, 2], cle='cle-idem-00001')
        self.assertEqual(a, b)
        self.assertEqual(len(self.commandes('MOVE')), 1)                     # aucun second effet
        with self.assertRaises(Stop) as ctx:
            self.agir('trash', [3], cle='cle-idem-00001')
        self.assertEqual(str(ctx.exception), 'cle_idempotence_deja_utilisee')
        with self.assertRaises(Stop):
            self.agir('trash', [3], cle='')

    def test_L1_pas_indesirable_et_indesirable(self):
        self.agir('junk', [7], cle='cle-junk-00001')
        uid_junk = max(self.s.boites['Junk']['uids'])
        out = L.actions(self.desk, ('cabinet', 'administrateur'), {'action': 'not_junk', 'dossier': 'Junk', 'uidvalidity': self.s.boites['Junk']['validite'],
                                                                 'items': [{'uid': str(uid_junk)}], 'cle': 'cle-junk-00002'}, 'cabinet')
        self.assertEqual(out['reussis'], 1)
        self.assertEqual(out['cible'], 'INBOX')
        self.assertEqual(self.s.boites['Junk']['uids'], {})

    def test_L1_effet_refuse_par_le_serveur_jamais_annonce_reussi(self):
        self.s.refus.add('MOVE')
        out = self.agir('archive', [1, 2])
        self.assertEqual(out['reussis'], 0)
        self.assertEqual({e['motif'] for e in out['echecs']}, {'refusé par le serveur'})
        self.assertEqual(len(self.s.boites['INBOX']['uids']), 12)

    def test_L1_droits_courriel_reserve_traite_comme_absent(self):
        cle2 = cle_canonique('cabinet@mail.example.test', 'INBOX', self.validite, '2')
        with patch('agent.acces_courriel56100.masques', return_value=frozenset({'DOS-002'})), \
                patch('agent.acces_courriel56100.dossiers', side_effect=lambda d, cles: {c: (frozenset({'DOS-002'}) if c == cle2 else frozenset()) for c in cles}):
            out = self.agir('trash', [1, 2])
        self.assertIn(2, self.s.boites['INBOX']['uids'])                    # le courriel réservé n'a pas bougé
        self.assertEqual((out['reussis'], out['echecs']), (1, [{'uid': '2', 'motif': 'déjà déplacé ou supprimé'}]))   # même motif qu'un absent
        # contre-exemple (mutation du garde en mémoire) : sans le filtre des droits, le courriel réservé serait déplacé
        with patch('agent.boite56106._autorises', side_effect=lambda desk, acteur, box, cfg, d, v, uids: list(uids)):
            self.agir('trash', [2], cle='cle-essai-0009')
        self.assertNotIn(2, self.s.boites['INBOX']['uids'])

    def test_L1_controle_en_panne_rien_nest_fait_503(self):
        with patch('agent.acces_courriel56100.masques', side_effect=Stop('controle_indisponible')):
            r = self.call('/api440/m56106/boite/actions', 'POST', {'action': 'trash', 'uidvalidity': self.validite, 'items': [{'uid': '1'}], 'cle': 'cle-essai-0004'})
        self.assertTrue(r['status'].startswith('503'), r['status'])
        self.assertEqual(self.commandes('MOVE'), [])

    def test_L1_classer_dans_le_dossier_client_par_la_conservation_existante(self):
        appels = []
        with patch('agent.correspondances5643.prepare', side_effect=lambda desk, owner, data, box=None: appels.append(data) or {'proposal_id': 'p' * 32, 'version': 1}), \
                patch('agent.correspondances5643.confirm', side_effect=lambda desk, owner, data: appels.append(data) or {}):
            out = self.agir('classer', [1, 2], matter='DOS-001')
        self.assertEqual(out['reussis'], 2)
        self.assertEqual([a['source']['uid'] for a in appels if 'source' in a], ['1', '2'])
        self.assertEqual({a['actions'][0] for a in appels if 'actions' in a}, {'archive_email'})
        with patch('agent.acces_courriel56100.masques', return_value=frozenset({'DOS-001'})):   # dossier client réservé : même 404 neutre
            with self.assertRaises(Stop) as ctx:
                self.agir('classer', [3], cle='cle-essai-0005', matter='DOS-001')
        self.assertEqual(str(ctx.exception), 'introuvable')
        with self.assertRaises(Stop):
            self.agir('classer', range(1, 52), cle='cle-essai-0006', matter='DOS-001')   # 50 au plus (chaque message est lu)

    def test_L1_route_roles_csrf_et_no_store(self):
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('avocat', 'POST', '/api440/m56106/boite/actions'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56106/boite/actions'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56106/boite/inconnue'))
        self.assertFalse(L.allowed('avocat', 'GET', 'm56106/boite/actions'))
        self.assertFalse(L.allowed('assistant', 'GET', 'm56106/boite/dossiers'))
        self.assertTrue(self.call('/api440/m56106/boite/actions')['status'].startswith('404'))   # une écriture n'est jamais lue en GET
        r = self.call('/api440/m56106/boite/actions', 'POST', {'action': 'lu', 'items': [{'uid': '1'}], 'cle': 'cle-essai-0007'}, csrf=False)
        self.assertTrue(r['status'].startswith('400'))
        self.assertEqual(self.commandes('STORE'), [])
        r = self.call('/api440/m56106/boite/actions', 'POST', {'action': 'lu', 'uidvalidity': self.validite, 'items': [{'uid': '1'}], 'cle': 'cle-essai-0008'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(r['headers']['Cache-Control'], 'private, no-store')


class Dossiers(Base):
    def test_L2_creation_nom_sur_utf7_et_doublon(self):
        out = L.creer_dossier(self.desk, {'nom': 'Expertises / Été 2026', 'cle': 'cle-dos-000001'})
        self.assertEqual(out['dossier'], 'Expertises - Été 2026')
        self.assertIn(('CREATE', mutf7('Expertises - Été 2026')), self.s.journal)
        self.assertIn('&AMk-', mutf7('Expertises - Été 2026'))               # RFC 3501 § 5.1.3
        deux = L.creer_dossier(self.desk, {'nom': 'expertises - été 2026', 'cle': 'cle-dos-000002'})
        self.assertEqual(deux['dossier'], 'expertises - été 2026 (2)')        # doublon (sans casse) évité
        with self.assertRaises(Stop):
            L.creer_dossier(self.desk, {'nom': ' /.. ', 'cle': 'cle-dos-000003'})
        self.assertEqual(L.nom_sur('A*B%C"D\\E.F'), 'A-B-C-D-E-F')

    def test_L2_dossiers_speciaux_jamais_modifies(self):
        L.sous_dossier(self.desk, ('cabinet', 'administrateur'), 'cabinet', 'DOS-001')
        for nom in ('INBOX', 'Trash', 'Junk', 'Sent', 'Drafts', 'Archives', 'Dossiers clients'):
            with self.assertRaises(Stop, msg=nom) as ctx:
                L.renommer_dossier(self.desk, {'ancien': nom, 'nom': 'Autre', 'cle': 'cle-ren-' + nom.replace(' ', '')[:6].ljust(6, 'x')})
            self.assertEqual(str(ctx.exception), 'dossier_special_protege')
            with self.assertRaises(Stop) as ctx:
                L.supprimer_dossier(self.desk, ('cabinet', 'administrateur'), {'dossier': nom, 'cle': 'cle-sup-' + nom.replace(' ', '')[:6].ljust(6, 'x')})
            self.assertIn(str(ctx.exception), ('dossier_special_protege', 'dossier_avec_sous_dossiers'))
        self.assertFalse([c for c in self.s.journal if c[0] in ('RENAME', 'DELETE')])
        # contre-exemple : sans la protection, la Corbeille serait renommée
        with patch('agent.boite56106._protege', return_value=False):
            L.renommer_dossier(self.desk, {'ancien': 'Trash', 'nom': 'Autre', 'cle': 'cle-ren-mutation'})
        self.assertIn('Autre', self.s.boites)

    def test_L2_suppression_dossier_non_vide_apres_confirmation_du_nombre(self):
        self.s.creer('Ancien')
        self.s.ajouter('Ancien', message(500))
        self.s.ajouter('Ancien', message(501))
        r = self.call('/api440/m56106/boite/dossier/supprimer', 'POST', {'dossier': 'Ancien', 'cle': 'cle-sup-000001'})
        self.assertTrue(r['status'].startswith('200'), r['status'])           # demande de confirmation (rien n'est fait)
        self.assertEqual((r['json']['confirmation_requise'], r['json']['nombre']), (True, 2))
        self.assertIn('Corbeille', r['json']['message'])
        self.assertIn('Ancien', self.s.boites)
        faux = L.supprimer_dossier(self.desk, ('cabinet', 'administrateur'), {'dossier': 'Ancien', 'confirme': '1', 'cle': 'cle-sup-000002'})
        self.assertTrue(faux['confirmation_requise'])                          # nombre faux : nouvelle confirmation, rien n'est supprimé
        self.assertIn('Ancien', self.s.boites)
        self.assertFalse([c for c in self.s.journal if c[0] == 'DELETE' or c[:2] == ('UID', 'MOVE')])
        out = L.supprimer_dossier(self.desk, ('cabinet', 'administrateur'), {'dossier': 'Ancien', 'confirme': '2', 'cle': 'cle-sup-000003'})
        self.assertEqual(out['deplaces'], 2)
        self.assertNotIn('Ancien', self.s.boites)
        self.assertEqual(len(self.s.boites['Trash']['uids']), 2)              # récupérables

    def test_L2_sous_dossier_du_dossier_client_cree_puis_memorise(self):
        acteur = ('cabinet', 'administrateur')
        out = L.sous_dossier(self.desk, acteur, 'cabinet', 'DOS-001')
        self.assertEqual((out['dossier'], out['cree']), ('Dossiers clients/DURAND - DOS-001', True))
        self.assertIn('Dossiers clients', self.s.boites)
        creations = len([c for c in self.s.journal if c[0] == 'CREATE'])
        again = L.sous_dossier(self.desk, acteur, 'cabinet', 'DOS-001')
        self.assertEqual((again['dossier'], again['cree']), (out['dossier'], False))
        self.assertEqual(len([c for c in self.s.journal if c[0] == 'CREATE']), creations)
        self.assertEqual(L.lien_sous_dossier(self.desk, acteur, 'DOS-001')['dossier'], out['dossier'])
        r = self.agir('sous_dossier', [5], matter='DOS-001')
        self.assertEqual((r['reussis'], r['cible']), (1, out['dossier']))
        L.renommer_dossier(self.desk, {'ancien': out['dossier'], 'nom': 'DURAND', 'cle': 'cle-ren-durand1'})
        self.assertEqual(L.lien_sous_dossier(self.desk, acteur, 'DOS-001')['dossier'], 'Dossiers clients/DURAND')   # lien suivi
        with patch('agent.acces_courriel56100.masques', return_value=frozenset({'DOS-002'})):
            with self.assertRaises(Stop) as ctx:
                L.sous_dossier(self.desk, ('avocate', 'avocat'), 'avocate', 'DOS-002')
        self.assertEqual(str(ctx.exception), 'introuvable')


class DossiersPointes(Base):
    SEP = '.'

    def setUp(self):
        super().setUp()
        for nom in list(self.s.boites):
            if nom != 'INBOX':
                self.s.boites['INBOX.' + nom] = self.s.boites.pop(nom)
        self.f.c['mail'].update({'drafts': 'INBOX.Drafts', 'sent': 'INBOX.Sent'})
        private_json(self.config, self.f.c)

    def test_L2_separateur_et_espace_personnel_lus_au_serveur(self):
        out = L.sous_dossier(self.desk, ('cabinet', 'administrateur'), 'cabinet', 'DOS-001')
        self.assertEqual(out['dossier'], 'INBOX.Dossiers clients.DURAND - DOS-001')
        d = L.dossiers(self.desk)
        self.assertEqual((d['separateur'], d['prefixe']), ('.', 'INBOX.'))
        rang = {f['name']: f for f in d['folders']}
        self.assertEqual((rang[out['dossier']]['court'], rang[out['dossier']]['profondeur']), ('DURAND - DOS-001', 1))
        self.assertTrue(rang['INBOX.Dossiers clients']['protege'])
        self.assertEqual(L.creer_dossier(self.desk, {'nom': 'A.B', 'cle': 'cle-dos-pointe1'})['dossier'], 'INBOX.A-B')


class ListeTriFiltres(Base):
    def recherche(self):
        return [c for c in self.s.journal if c[:2] in (('UID', 'SEARCH'), ('UID', 'SORT'))][-1]

    def test_L3_filtres_et_recherche_etendue_cote_serveur(self):
        opts = L.options_liste({'non_lus': '1', 'suivis': '1', 'pj': '1', 'corps': 'DOS-001', 'destinataire': 'cabinet', 'du': '2026-11-02', 'au': '2026-11-05'})
        out = boite5625.listing(self.desk, options=opts)
        cmd = ' '.join(self.recherche())
        for attendu in ('UNSEEN', 'FLAGGED', 'HEADER Content-Type "multipart/mixed"', 'BODY "DOS-001"', 'TO "cabinet"', 'SINCE 02-Nov-2026', 'BEFORE 06-Nov-2026'):
            self.assertIn(attendu, cmd)
        self.assertEqual(out['uidvalidity'], self.validite)
        non_lus = boite5625.listing(self.desk, options=L.options_liste({'non_lus': '1'}))
        self.assertEqual(sorted(int(x['uid']) for x in non_lus['items']), [3, 6, 9, 12])
        suivis = boite5625.listing(self.desk, options=L.options_liste({'suivis': '1'}))
        self.assertTrue(all(x['flagged'] for x in suivis['items']) and len(suivis['items']) == 2)
        pj = boite5625.listing(self.desk, options=L.options_liste({'pj': '1'}))
        self.assertEqual([x['uid'] for x in pj['items']], ['7'])

    def test_L4_corps_accentue_en_litteral_utf8_cote_serveur(self):
        self.s.ajouter('INBOX', message(300, corps='Calendrier de procédure fictif'))
        out = boite5625.listing(self.desk, options=L.options_liste({'corps': 'procédure'}))
        self.assertEqual([x['uid'] for x in out['items']], ['13'])
        cmd = self.recherche()
        self.assertEqual(cmd[3:5], ('CHARSET', 'UTF-8'))
        self.assertEqual(cmd[-1], 'BODY')

    def test_L3_tri_par_date_ancien_et_par_objet(self):
        asc = boite5625.listing(self.desk, options=L.options_liste({'tri': 'date_asc'}))
        self.assertEqual([x['uid'] for x in asc['items']][:3], ['1', '2', '3'])
        objet = boite5625.listing(self.desk, options=L.options_liste({'tri': 'objet'}))
        sujets = [x['subject'].casefold() for x in objet['items']]
        self.assertEqual(sujets, sorted(sujets))
        self.assertFalse(self.commandes('SORT'))                               # serveur sans SORT : tri local annoncé
        self.assertEqual(objet['couverture'], {'partielle': False, 'examines': 12})
        self.s.semer(300, debut=13)
        grand = boite5625.listing(self.desk, options=L.options_liste({'tri': 'expediteur'}))
        self.assertTrue(grand['couverture']['partielle'])

    def test_L3_tri_par_le_serveur_quand_il_sait_trier(self):
        self.s.capacites = self.s.capacites + ('SORT',)
        out = boite5625.listing(self.desk, options=L.options_liste({'tri': 'expediteur'}))
        self.assertEqual(len(self.commandes('SORT')), 1)
        self.assertNotIn('couverture', out)
        noms = [x['from'].casefold() for x in out['items']]
        self.assertEqual(noms, sorted(noms))

    def test_L3_sans_option_meme_chemin_qu_avant(self):
        out = boite5625.listing(self.desk)
        self.assertEqual([x['uid'] for x in out['items']][:2], ['12', '11'])
        self.assertEqual(len([c for c in self.s.journal if c[:2] == ('UID', 'SEARCH')]), 1)
        self.assertEqual(self.recherche()[2:], ('None', 'UNDELETED'))
        r = self.call('/api440/m5625/boite/liste', query='page=1&non_lus=1&tri=date_asc')
        self.assertEqual([x['uid'] for x in r['json']['items']], ['3', '6', '9', '12'])


class Epingles(Base):
    ACTEUR = ('cabinet', 'administrateur')

    def liste(self, **kw):
        return boite5625.listing(self.desk, acteur=self.ACTEUR, options=L.options_liste(kw), folder=kw.get('dossier'))

    def test_L8_epingles_en_tete_au_dessus_du_tri_sans_drapeau_imap(self):
        out = self.agir('epingler', [2, 5])
        self.assertEqual(out['message'], '2 épinglés.')
        self.assertEqual(self.commandes('STORE'), [])                          # propre à AxiorHub : aucun drapeau IMAP
        liste = self.liste()
        self.assertEqual(set(x['uid'] for x in liste['items'][:2]), {'5', '2'})   # en tête, au-dessus du tri
        self.assertEqual(liste['items'][2]['uid'], '12')
        self.assertTrue(liste['items'][0]['epingle'] and liste['items'][1]['epingle'])
        self.assertEqual(liste['total'], 12)
        self.assertEqual([x['uid'] for x in liste['items']].count('5'), 1)     # jamais deux fois
        asc = self.liste(tri='date_asc')
        self.assertEqual(set(x['uid'] for x in asc['items'][:2]), {'5', '2'})
        self.assertEqual(asc['items'][2]['uid'], '1')
        page2 = boite5625.listing(self.desk, page=2, acteur=self.ACTEUR, options=L.options_liste({}))
        self.assertFalse([x for x in page2['items'] if x.get('epingle')])
        self.agir('desepingler', [5], cle='cle-epin-00002')
        self.assertEqual([x['uid'] for x in self.liste()['items']][:2], ['2', '12'])
        # autre personne : ses épingles sont les siennes
        autre = boite5625.listing(self.desk, acteur=('avocate', 'administrateur'), options=L.options_liste({}))
        self.assertFalse([x for x in autre['items'] if x.get('epingle')])

    def test_L8_epingle_suit_le_deplacement_et_disparait_si_supprime(self):
        self.agir('epingler', [3, 4])
        self.agir('move', [3], cle='cle-epin-00003', cible='Archives')
        self.assertNotIn('3', [x['uid'] for x in self.liste()['items'] if x.get('epingle')])   # parti du dossier : plus en tête
        archives = self.liste(dossier='Archives')
        self.assertTrue(archives['items'][0].get('epingle'))                     # même Message-ID, autre dossier et autre UID
        del self.s.boites['INBOX']['uids'][4]                                    # supprimé par une autre messagerie
        self.assertFalse([x for x in self.liste()['items'] if x.get('epingle')])

    def test_L8_plafond_de_50_epingles_et_droits(self):
        self.s.semer(60, debut=13)
        self.agir('epingler', range(1, 51))
        with self.assertRaises(Stop) as ctx:
            self.agir('epingler', [60], cle='cle-epin-00004')
        self.assertEqual(str(ctx.exception), 'trop_d_epingles')
        self.agir('epingler', [10], cle='cle-epin-00005')                       # déjà épinglé : accepté, rien de plus
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM boite56106_epingles').fetchone()[0], 50)
        cle7 = cle_canonique('cabinet@mail.example.test', 'INBOX', self.validite, '7')
        self.agir('desepingler', range(1, 51), cle='cle-epin-00006')
        with patch('agent.acces_courriel56100.masques', return_value=frozenset({'DOS-002'})), \
                patch('agent.acces_courriel56100.dossiers', side_effect=lambda d, cles: {c: (frozenset({'DOS-002'}) if c == cle7 else frozenset()) for c in cles}):
            out = self.agir('epingler', [7, 8], cle='cle-epin-00007')
        self.assertEqual((out['reussis'], out['echecs'][0]['uid']), (1, '7'))   # réservé : même motif qu'un absent, rien n'est épinglé
        self.assertEqual(L.cle_epingle('c', '<A@x>', 'INBOX', '7', '1'), L.cle_epingle('c', '<a@x>', 'Archives', '9', '42'))


class RechercherDans(Base):
    def recherche(self):
        return [c for c in self.s.journal if c[:2] == ('UID', 'SEARCH')][-1]

    def test_L9_champs_combines_par_OR_cote_serveur(self):
        self.s.ajouter('INBOX', message(400, objet='Sans rapport', corps='Le mot témoin ZORVAL-CORPS est ici.', destinataire='kervilac@example.test'))
        defaut = boite5625.listing(self.desk, query='ZORVAL-CORPS', options=L.options_liste({}))
        self.assertIn('OR SUBJECT "ZORVAL-CORPS" FROM "ZORVAL-CORPS"', ' '.join(self.recherche()))   # comme avant : objet et expéditeur
        self.assertEqual(defaut['items'], [])
        corps = boite5625.listing(self.desk, query='ZORVAL-CORPS', options=L.options_liste({'dans': 'corps'}))
        self.assertEqual([x['uid'] for x in corps['items']], ['13'])
        tous = boite5625.listing(self.desk, query='kervilac', options=L.options_liste({'dans': 'objet,corps,expediteur,destinataires'}))
        cmd = ' '.join(self.recherche())
        for attendu in ('SUBJECT "kervilac"', 'BODY "kervilac"', 'FROM "kervilac"', 'TO "kervilac"', 'CC "kervilac"'):
            self.assertIn(attendu, cmd)
        self.assertEqual(cmd.split().count('OR'), 4)                            # cinq critères, quatre OR
        self.assertIn('13', [x['uid'] for x in tous['items']])
        self.assertEqual(L.champs_recherche('inconnu'), ('objet', 'expediteur'))

    def test_L9_corps_accentue_seul_cote_serveur_sinon_signale(self):
        self.s.ajouter('INBOX', message(401, corps='Une échéance fictive.'))
        seul = boite5625.listing(self.desk, query='échéance', options=L.options_liste({'dans': 'corps'}))
        self.assertEqual([x['uid'] for x in seul['items']], ['13'])
        self.assertEqual(self.recherche()[-1], 'BODY')
        mixte = boite5625.listing(self.desk, query='échéance', options=L.options_liste({'dans': 'objet,corps'}))
        self.assertTrue(mixte['couverture']['corps_non_cherche'])

    def test_L9_choix_memorise_par_personne(self):
        r = self.call('/api440/m56106/boite/dossiers')
        self.assertEqual(r['json']['recherche'], ['objet', 'expediteur'])
        r = self.call('/api440/m56106/boite/preferences', 'POST', {'recherche': ['corps', 'objet', 'inconnu']})
        self.assertEqual(r['json']['recherche'], ['objet', 'corps'])
        self.assertEqual(self.call('/api440/m56106/boite/dossiers')['json']['recherche'], ['objet', 'corps'])
        self.assertEqual(L.preference_recherche(self.desk, 'autre-personne'), ['objet', 'expediteur'])
        self.assertTrue(self.call('/api440/m56106/boite/preferences', 'POST', {'recherche': []})['status'].startswith('400'))
        r = self.call('/api440/m5625/boite/liste', query='q=DURAND&dans=destinataires')
        self.assertEqual(r['json']['dans'], ['destinataires'])
        self.assertIn('TO "DURAND"', ' '.join(self.recherche()))


class ToutLu(Base):
    def test_L10_tout_le_dossier_comme_lu_apres_confirmation_du_nombre(self):
        corps = {'dossier': '', 'uidvalidity': self.validite, 'cle': 'cle-tout-00001'}
        r = self.call('/api440/m56106/boite/dossier/tout-lu', 'POST', corps)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual((r['json']['confirmation_requise'], r['json']['nombre']), (True, 4))
        self.assertIn('4 courriel(s) non lus', r['json']['message'])
        # contre-exemple : un nombre confirmé faux ne marque rien
        r = self.call('/api440/m56106/boite/dossier/tout-lu', 'POST', dict(corps, confirme='3', cle='cle-tout-00009'))
        self.assertTrue(r['json']['confirmation_requise'])
        self.assertEqual(self.commandes('STORE'), [])                          # rien sans confirmation
        r = self.call('/api440/m56106/boite/dossier/tout-lu', 'POST', dict(corps, confirme='4', cle='cle-tout-00002'))
        self.assertEqual((r['json']['reussis'], r['json']['message']), (4, '4 marqués lus.'))
        self.assertEqual(len(self.commandes('STORE')), 1)
        self.assertTrue(all('\\Seen' in e[0] for e in self.s.boites['INBOX']['uids'].values()))
        r = self.call('/api440/m56106/boite/dossier/tout-lu', 'POST', dict(corps, cle='cle-tout-00003'))
        self.assertEqual(r['json']['message'], 'Aucun courriel non lu dans ce dossier.')
        r = self.call('/api440/m56106/boite/dossier/tout-lu', 'POST', dict(corps, uidvalidity='999', cle='cle-tout-00004'))
        self.assertTrue(r['status'].startswith('409'))


class Page(Base):
    def test_L7_page_nouvelle_interface_et_presentation_historique(self):
        from agent import interface56100
        page = self.call('/courriels', query='vue=boite')['body']
        self.assertIn('class="bx5625 bx56106"', page)
        self.assertIn('/static/v56106-liste.js', page)
        interface56100.regler_cabinet(self.desk, False)
        ancienne = self.call('/courriels', query='vue=boite')['body']
        self.assertNotIn('bx56106', ancienne.split('id="bx5625"')[0][-200:])
        self.assertNotIn('/static/v56106-liste.js', ancienne)

    def test_L7_statiques_feuille_unique_et_points_de_montage(self):
        from agent.shell501 import BUNDLE
        from agent.web440 import STATIC
        self.assertIn('/static/v56106-liste.js', STATIC)
        self.assertIn('v56106-liste.css', BUNDLE)
        css = (ROOT / 'agent' / 'static' / 'v56106-liste.css').read_text(encoding='utf-8')
        self.assertIn(css.strip()[-60:], (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))
        self.assertIn('body[data-densite="confortable"]', css)                # réglage commun du lot D, aucun second réglage
        js = (ROOT / 'agent' / 'static' / 'v56106-liste.js').read_text(encoding='utf-8')
        for crochet in ('bx56106-actions-v', "'data-lot': 'V'", 'window.axh56106liste', 'selection: selection', 'rafraichir',
                        "'Ctrl+Maj+R'", "k === 'Delete'", "k === 'Insert'", "k === '?'", 'saisie(ev.target)', 'm56106/boite/actions',
                        # Actualiser (F5, heure, rafraîchissement léger désactivable, page visible seulement), menu « Marquer » (rôles, clavier),
                        # épingles (aide : jamais un drapeau IMAP), « Rechercher dans » (avertissement sur le corps)
                        "ev.key === 'F5'", "'à jour à '", 'document.hidden', '120000', "role: 'menu'", "role: 'menuitem'", "'aria-haspopup': 'menu'",
                        "ev.key === 'ArrowDown'", "ev.key === 'Escape'", "'aria-disabled'", 'n’apparaît ni dans Outlook ni dans Thunderbird',
                        'plus lente sur une grosse boîte', 'm56106/boite/preferences', 'm56106/boite/dossier/tout-lu'):
            self.assertIn(crochet, js)
        self.assertNotIn('innerHTML', js)
        self.assertIn('axh56106lecteur', (ROOT / 'agent' / 'static' / 'v5625.js').read_text(encoding='utf-8'))

    def test_L2_fiche_du_dossier_propose_le_sous_dossier(self):
        r = self.call('/dossier', query='id=DOS-001&vue=echanges')
        self.assertTrue(r['status'].startswith('200'), r['status'])
        self.assertIn('data-bx56106-sous-dossier="DOS-001"', r['body'])
        self.assertIn('/static/v56106-liste.js', r['body'])
        self.assertFalse([c for c in self.s.journal if c[0] in ('LOGIN', 'CREATE')])   # aucune lecture IMAP au rendu de la fiche
        self.assertIn('data-bx56106-sous-dossier', (ROOT / 'agent' / 'static' / 'v56106-liste.js').read_text(encoding='utf-8'))

    def test_L_R05_lecture_des_dossiers_sans_ecriture(self):
        L.sous_dossier(self.desk, ('cabinet', 'administrateur'), 'cabinet', 'DOS-001')
        avant = self.desk.db.total_changes
        ecritures = len([c for c in self.s.journal if c[0] in ('CREATE', 'SELECT', 'RENAME', 'DELETE')])
        r = self.call('/api440/m56106/boite/dossiers')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertEqual(r['headers']['Cache-Control'], 'private, no-store')
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM boite56106_operations').fetchone()[0], 0)
        lie = [f for f in r['json']['folders'] if f.get('matter')]
        self.assertEqual([f['matter'] for f in lie], ['DOS-001'])
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertEqual(len([c for c in self.s.journal if c[0] in ('CREATE', 'SELECT', 'RENAME', 'DELETE')]), ecritures)   # aucune écriture IMAP


if __name__ == '__main__':
    unittest.main()

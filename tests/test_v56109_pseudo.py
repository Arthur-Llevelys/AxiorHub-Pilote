"""5.6.109 (lot P) : pseudonymisation avant une IA externe — tickets P01 à P06.

Circuit central seulement (``pseudo540``, ``Model._leaf``, ``vision56106``, ``transmission56109``, ``chatgpt5662``, ``claude5675``) : aucun
second circuit. Faux transports, aucun réseau (tout appel de socket échoue), aucun modèle ni compte réel ; données fictives seulement
(DURAND, MARTIN, BERNARD, ZORVAL, KERVILAC, ORLANDEC, DOS-001, RG 24/01234, Valbrune, example.test ; « Samira Zelvani » est le nom fictif
du dossier technique). Chaque garde-fou a son contre-exemple : le test échoue si le garde-fou est retiré (mutation en mémoire)."""
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from agent import claude5675, dossiers5697 as D, fiche5698 as F, pseudo540, vision56106
from agent.common import Stop
from agent.desk import Desk
from agent.model import Model, pseudo_sources, routed_config
from agent.pseudo540 import Pseudonymizer
import test_v540                                     # montage (fournisseurs fictifs, faux transport) ; ses tests ne sont pas rejoués ici
from test_v5697_dossiers import ADMIN, FauxStockage

FakeHTTP = test_v540.FakeHTTP

NOM = 'Samira Zelvani'


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


def mots(*textes, conf=99):
    return [{'texte': t, 'conf': conf, 'ligne': 1, 'x': i * 100, 'y': 10, 'w': 80, 'h': 20} for i, t in enumerate(textes)]


class Externe(unittest.TestCase):
    """Bureau complet (registre des dossiers, fiche, conflits), fournisseurs externes fictifs, transport remplacé avant tout appel."""
    setUp0 = test_v540.External.setUp
    route_all = test_v540.External.route_all

    def setUp(self):
        self.setUp0()
        self.desk = self.d
        self.addCleanup(self.desk.db.close)
        p = patch.object(D, '_client_stockage', lambda desk: FauxStockage())
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: pseudo540._CACHE.update(key=None, at=0, items=[]))
        self.c = self.f.c

    # ------------------------------------------------------------------ outils
    def dossier(self, **donnees):
        donnees.setdefault('titre', 'DURAND c/ MARTIN')
        return D.creer(self.desk, ADMIN, donnees, uuid.uuid4().hex)['id']

    def partie(self, did, nom=NOM, role='adverse', **champs):
        return F.ajouter_partie(self.desk, ADMIN, did, {'nom': nom, 'role': role, **champs}, uuid.uuid4().hex)

    def modele(self, fournisseur='openai', modele='gpt-5', fonction='mail_drafting', **cfg):
        self.route_all(fournisseur, modele)
        conf = routed_config(self.c, fonction)
        conf.update(cfg)
        return Model(conf)

    def corps(self, chemin='/chat/completions'):
        return [p for path, p in FakeHTTP.payloads if path == chemin]

    def emis(self, chemin='/chat/completions'):
        return json.dumps(self.corps(chemin)[-1], ensure_ascii=False)


# ================================================================================================ P01
class P01Identites(Externe):
    def test_P01_partie_du_registre_courant_absente_du_corps_sans_civilite(self):
        """Témoin de l'original : « Nous representons Samira Zelvani. » partait en clair. La partie est au registre courant du dossier."""
        self.partie(self.dossier(), role='expert')          # rôle que le référentiel des conflits ne reçoit pas : seul le registre courant le connaît
        FakeHTTP.answer = 'Projet pour [PERSONNE_1].'
        out = self.modele().complete([{'role': 'user', 'content': 'Nous representons Samira Zelvani.'}])
        self.assertNotIn('Samira', self.emis())
        self.assertNotIn('Zelvani', self.emis())
        self.assertEqual(out, 'Projet pour Samira Zelvani.')

    def test_P01_accents_casse_ordre_inverse_et_nom_seul(self):
        self.partie(self.dossier(), nom='Hélène Kervilac')
        for texte in ('HELENE KERVILAC a signé.', 'helene kervilac a signé.', 'Kervilac Hélène a signé.', 'Kervilac conteste la créance.',
                      'Hélène\nKervilac a signé.', 'Hélène  Kervilac a signé.', 'Hélène Kervilac a signé.', 'KERVILAC, seule, conteste.'):
            ps = Pseudonymizer({'state_dir': self.c['state_dir']})
            sortie = ps.text(texte)
            self.assertNotIn('ervilac', sortie.lower(), texte)
            self.assertNotIn('lène', sortie.lower(), texte)
            self.assertEqual(ps.restore(sortie), texte)          # rétabli tel qu'il était écrit

    def test_P01_nom_seul_non_devine_au_retour(self):
        """Un nom seul du registre est masqué, mais rétabli tel qu'il était écrit : jamais complété par un prénom du registre."""
        self.partie(self.dossier(), nom='Paul DURAND')
        ps = Pseudonymizer({'state_dir': self.c['state_dir']})
        sortie = ps.text('Durand conteste la créance.')
        self.assertNotIn('Durand', sortie)
        self.assertEqual(ps.restore(sortie), 'Durand conteste la créance.')

    def test_P01_contacts_alias_parties_des_pieces_et_coordonnees(self):
        did = self.dossier(titre='Bail commercial Valbrune', client='ORLANDEC')
        self.partie(did, nom='ZORVAL Industrie', role='adverse', type_personne='morale', courriel='direction@zorval.example.test',
                    telephone='01 00 00 00 05', siren='000010017')
        from agent import crm5636, modele5651
        crm5636.schema(self.desk)
        self.desk.db.execute("INSERT INTO contacts5636(id,kind,category,name,first_name,company,email,created,updated) VALUES('c1','personne','client',"
                             "'Bernard','Yusuf','Atelier Brumeval','yusuf.bernard@example.test','x','x')")
        self.desk.db.executescript(modele5651.SCHEMA)
        self.desk.db.execute("INSERT INTO d51_parties(id,matter,kind,display_name,core,origin,created,updated) VALUES('p1','DOS-001','physique',"
                             "'Marguerite Orlandec-Vasseur','orlandec vasseur','pieces','x','x')")
        self.desk.db.commit()
        texte = ('ZORVAL Industrie (direction@zorval.example.test, 01 00 00 00 05, n° 000010017) ; Yusuf Bernard pour Atelier Brumeval ; '
                 'Marguerite Orlandec-Vasseur ; dossier Bail commercial Valbrune ; ORLANDEC.')
        sortie = Pseudonymizer({'state_dir': self.c['state_dir']}).text(texte)
        for secret in ('ZORVAL', 'zorval', '01 00 00 00 05', '000010017', 'Yusuf', 'Bernard', 'Brumeval', 'Marguerite', 'Vasseur', 'Bail commercial',
                       'ORLANDEC', 'Orlandec'):
            self.assertNotIn(secret, sortie, secret)

    def test_P01_meme_instantane_pour_le_texte_et_la_lecture_des_images(self):
        """Témoin de l'original : les deux mots lus (confiance 99) n'étaient pas retenus pour être noircis."""
        self.partie(self.dossier())
        self.assertEqual(vision56106.sensibles({'state_dir': self.c['state_dir']}, mots('Samira', 'Zelvani')), {0, 1})
        self.assertEqual(vision56106.sensibles({'state_dir': self.c['state_dir']}, mots('Pour', 'Zelvani', 'ce', 'jour')), {1})
        lectures = []
        origine = pseudo540._des_registres

        def compter(*a, **k):
            lectures.append(1)
            return origine(*a, **k)
        m = self.modele()
        vus = []

        def choisir(model):                       # la lecture d'image de CET appel : même liste que le texte, sans relire le registre
            model._images56106 = []
            vus.append(pseudo540.INSTANTANE.get())
            vus.append(Pseudonymizer({'state_dir': self.c['state_dir']}).known)
            return []
        with patch.object(pseudo540, '_des_registres', compter), patch('agent.vision56106.choisir', choisir):
            m.complete([{'role': 'user', 'content': 'Écrire à Samira Zelvani.'}])
        self.assertEqual(len(lectures), 1)                      # un seul instantané pour l'émission
        self.assertIsNotNone(vus[0])
        self.assertIs(vus[0], vus[1])
        self.assertIn((NOM, 'PERSONNE'), list(vus[0]))
        self.assertIsNone(pseudo540.INSTANTANE.get())           # rien ne reste d'une émission à la suivante

    def test_P01_identite_dans_un_champ_hors_message_refusee(self):
        """Tous les champs émis : un nom dans « name » d'un message ne passe pas par le masquage du texte ; il est refusé avant le transport."""
        self.partie(self.dossier())
        m = self.modele()
        with self.assertRaises(Stop) as ctx:
            m.complete([{'role': 'user', 'name': 'Samira Zelvani', 'content': 'Bonjour.'}])
        self.assertEqual(str(ctx.exception), 'pseudo_identite_residuelle')
        self.assertEqual(self.corps(), [])

    def test_P01_registre_illisible_refus_explique_et_rien_ne_part(self):
        m = self.modele()
        with patch.object(pseudo540, '_des_registres', side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(Stop) as ctx:
                m.complete([{'role': 'user', 'content': 'Écrire à Samira Zelvani.'}])
        self.assertEqual(str(ctx.exception), 'registre_identites_illisible')
        self.assertEqual(self.corps(), [])
        Path(self.c['matters_file']).write_text('{illisible', encoding='utf-8')
        with self.assertRaises(Stop) as ctx:
            Pseudonymizer({'matters_file': self.c['matters_file'], 'state_dir': self.c['state_dir']})
        self.assertEqual(str(ctx.exception), 'registre_identites_illisible')

    def test_P01_contre_exemple_sans_le_registre_courant_le_nom_part(self):
        """Mutation : registre courant retiré de la lecture → le nom repart en clair (le test du haut échouerait)."""
        self.partie(self.dossier(), role='expert')        # rôle hors conflits : seul le registre courant le connaît
        sans = tuple(t for t in pseudo540.REGISTRES if t != 'dossier_parties5697')
        origine = pseudo540._des_registres

        def mute(db, noms, items):
            return origine(db, {n for n in noms if n != 'dossier_parties5697'}, items)
        with patch.object(pseudo540, 'REGISTRES', sans), patch.object(pseudo540, '_des_registres', mute):
            self.modele().complete([{'role': 'user', 'content': 'Nous representons Samira Zelvani.'}])
        self.assertIn('Samira Zelvani', self.emis())

    def test_P01_limites_visibles_un_inconnu_n_est_pas_presente_comme_protege(self):
        """Ce que le logiciel ne sait pas reconnaître reste visible dans l'aperçu, et l'aperçu le dit."""
        out = pseudo540.preview({'state_dir': self.c['state_dir']}, 'nous representons yusuf ardanel dans cette affaire')
        self.assertIn('yusuf ardanel', out['sent'])               # nom inconnu, sans civilité ni majuscule : non reconnu
        self.assertTrue(out['limites'])
        self.assertIn('ne prouve pas', out['note'])
        self.assertEqual(out['counts'], {})
        for mot in ('anonyme', 'protection totale', 'aucun risque'):
            self.assertNotIn(mot, json.dumps(out, ensure_ascii=False).lower())
        self.assertIn('identites', out['couverture'])
        self.assertNotIn('yusuf', json.dumps(out['couverture']))


# ================================================================================================ P02
class P02Revision(Externe):
    def test_P02_partie_ajoutee_masquee_des_le_premier_appel(self):
        """Critère de réception : ajouter une partie puis préparer une émission sans attendre."""
        did = self.dossier()
        m = self.modele()
        m.complete([{'role': 'user', 'content': 'Première demande, avant la saisie.'}])     # le cache existe
        Pseudonymizer({'state_dir': self.c['state_dir']})
        self.partie(did, role='expert')
        m.complete([{'role': 'user', 'content': 'Nous representons Samira Zelvani.'}])
        self.assertNotIn('Zelvani', self.emis())
        self.assertNotIn('Zelvani', Pseudonymizer({'state_dir': self.c['state_dir']}).text('samira zelvani signe.'))   # hors émission aussi

    def test_P02_revision_durable_avancee_par_chaque_ecriture(self):
        did = self.dossier()
        etat = self.c['state_dir']
        r0 = pseudo540.revision(etat)
        self.assertIsInstance(r0, int)
        recu = self.partie(did, role='expert')
        r1 = pseudo540.revision(etat)
        self.assertGreater(r1, r0)
        F.modifier_partie(self.desk, ADMIN, did, recu['id'], recu['version'], {'nom': 'Samira Zelvani-Bernard', 'role': 'expert'}, uuid.uuid4().hex)
        r2 = pseudo540.revision(etat)
        self.assertGreater(r2, r1)
        version = self.desk.db.execute('SELECT version FROM dossier_parties5697 WHERE id=?', (recu['id'],)).fetchone()[0]
        F.retirer_partie(self.desk, ADMIN, did, recu['id'], version, uuid.uuid4().hex)
        r3 = pseudo540.revision(etat)
        self.assertGreater(r3, r2)
        autre = sqlite3.connect(Path(etat) / 'desk.sqlite3')            # un autre processus (import, synchronisation) : même révision
        autre.execute("INSERT INTO conflicts500_parties VALUES('z1','DOS-001','Octave Brumeval','octave brumeval','adverse','','import','x')")
        autre.commit()
        autre.close()
        self.assertGreater(pseudo540.revision(etat), r3)
        self.assertNotIn('Brumeval', Pseudonymizer({'state_dir': etat}).text('octave brumeval signe.'))

    def test_P02_cache_employe_seulement_si_la_revision_est_verifiable(self):
        etat = self.c['state_dir']
        lectures = []
        origine = pseudo540._des_registres

        def compter(*a, **k):
            lectures.append(1)
            return origine(*a, **k)
        with patch.object(pseudo540, '_des_registres', compter):
            a = Pseudonymizer({'state_dir': etat}).known
            b = Pseudonymizer({'state_dir': etat}).known
            self.assertTrue(a.sur)
            self.assertIs(a, b)                                  # registre inchangé, révision vérifiable : cache
            self.assertEqual(len(lectures), 1)
            self.desk.db.execute('DROP TRIGGER pseudo56109_conflicts500_parties_i')     # mutation : un registre sans déclencheur
            self.desk.db.commit()
            c = Pseudonymizer({'state_dir': etat}).known
            d = Pseudonymizer({'state_dir': etat}).known
            self.assertFalse(c.sur)
            self.assertIsNot(c, d)                               # plus de preuve que la révision suit les écritures : relu à chaque fois
            self.assertEqual(len(lectures), 3)

    def test_P02_base_sans_revision_temoin_de_l_original(self):
        """Témoin de l'original (base nue, table des conflits seule) : la partie ajoutée après le premier appel restait en clair."""
        with tempfile.TemporaryDirectory(prefix='axh-pseudo-') as td:
            db = sqlite3.connect(Path(td) / 'desk.sqlite3')
            self.addCleanup(db.close)
            db.execute('CREATE TABLE conflicts500_parties(name TEXT,email TEXT)')
            db.commit()
            cfg = {'state_dir': td}
            Pseudonymizer(cfg)
            db.execute("INSERT INTO conflicts500_parties VALUES('samira zelvani','')")
            db.commit()
            self.assertNotIn('samira zelvani', Pseudonymizer(cfg).text('samira zelvani signe.'))
            db.close()

    def test_P02_invalider_avance_la_revision_et_vide_le_cache(self):
        etat = self.c['state_dir']
        a = Pseudonymizer({'state_dir': etat}).known
        r0 = pseudo540.revision(etat)
        self.assertTrue(pseudo540.invalider(self.desk, 'essai'))
        self.assertEqual(pseudo540.revision(etat), r0 + 1)
        self.assertIsNot(Pseudonymizer({'state_dir': etat}).known, a)
        self.assertTrue(pseudo540.invalider({'state_dir': etat}, 'essai'))
        self.assertTrue(pseudo540.invalider(etat))
        self.assertEqual(pseudo540.revision(etat), r0 + 3)
        self.assertFalse(pseudo540.invalider({'state_dir': str(Path(etat) / 'absent')}))      # jamais d'exception vers l'écriture métier
        self.assertFalse(pseudo540.invalider(None))

    def test_P02_invalider_dans_la_transaction_de_l_appelant(self):
        etat = self.c['state_dir']
        r0 = pseudo540.revision(etat)
        self.desk.db.execute('BEGIN IMMEDIATE')
        pseudo540.invalider(self.desk, 'essai')
        self.assertTrue(self.desk.db.in_transaction)             # ni commit ni rollback à la place de l'appelant
        self.desk.db.rollback()
        self.assertEqual(pseudo540.revision(etat), r0)

    def test_P02_points_de_montage_fiche_et_synchronisation(self):
        did = self.dossier()
        with patch.object(pseudo540, 'invalider', wraps=pseudo540.invalider) as inv:
            recu = self.partie(did, role='expert')
            self.assertEqual(inv.call_count, 1)
            F.modifier_partie(self.desk, ADMIN, did, recu['id'], recu['version'], {'nom': 'Samira Zelvani', 'role': 'correspondant'}, uuid.uuid4().hex)
            self.assertEqual(inv.call_count, 2)
            version = self.desk.db.execute('SELECT version FROM dossier_parties5697 WHERE id=?', (recu['id'],)).fetchone()[0]
            F.retirer_partie(self.desk, ADMIN, did, recu['id'], version, uuid.uuid4().hex)
            self.assertEqual(inv.call_count, 3)
        import inspect
        from agent import sync56108_fichiers
        self.assertIn('pseudo540', inspect.getsource(sync56108_fichiers.apres_reception))

    def test_P02_demarrage_rejouable_sans_ecriture(self):
        pseudo540.ensure_schema(self.desk)
        avant = self.desk.db.total_changes
        pseudo540.ensure_schema(self.desk)
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertFalse(self.desk.db.in_transaction)


# ================================================================================================ P03
class P03Litteraux(Externe):
    SOURCE = 'Le gabarit contient [PERSONNE_1] ; ecrire a Mme Alice KERVILAC.'

    def test_P03_marqueur_litteral_inchange_au_retour(self):
        """Témoin de l'original : le marqueur du gabarit revenait remplacé par le nom."""
        ps = Pseudonymizer(known=[])
        envoye = ps.text(self.SOURCE)
        self.assertNotIn('KERVILAC', envoye)
        self.assertEqual(ps.restore(envoye), self.SOURCE)
        self.assertEqual(envoye.count('[PERSONNE_1]'), 1)        # le littéral seul : la substitution a un autre numéro
        self.assertIn('[PERSONNE_2]', envoye)
        self.assertEqual(ps.restore('Rappel : [PERSONNE_1] reste à remplir ; destinataire [PERSONNE_2].'),
                         'Rappel : [PERSONNE_1] reste à remplir ; destinataire Alice KERVILAC.')

    def test_P03_litteral_repere_avant_toute_attribution_dans_la_requete(self):
        """Le littéral arrive dans un message ultérieur : son numéro était quand même réservé."""
        ps = Pseudonymizer(known=[])
        sortie = ps.apply_messages([{'role': 'user', 'content': 'Écrire à Mme Alice KERVILAC.'},
                                    {'role': 'user', 'content': 'Gabarit : « Cher [PERSONNE_1], »'}])
        self.assertEqual(sortie[0]['content'], 'Écrire à Mme [PERSONNE_2].')
        self.assertEqual(sortie[1]['content'], 'Gabarit : « Cher [PERSONNE_1], »')
        self.assertEqual(ps.restore(sortie[0]['content'] + ' ' + sortie[1]['content']), 'Écrire à Mme Alice KERVILAC. Gabarit : « Cher [PERSONNE_1], »')

    def test_P03_numero_deja_attribue_forme_distincte(self):
        """Le numéro est déjà pris (texte traité plus tôt) : le littéral part sous une forme distincte et revient tel qu'il était."""
        ps = Pseudonymizer(known=[])
        self.assertEqual(ps.text('Écrire à Mme Alice KERVILAC.'), 'Écrire à Mme [PERSONNE_1].')
        envoye = ps.text('Le gabarit contient [PERSONNE_1] et [LITTERAL_PERSONNE_1].')
        self.assertEqual(envoye, 'Le gabarit contient [LITTERAL_PERSONNE_1] et [LITTERAL_LITTERAL_PERSONNE_1].')
        self.assertEqual(ps.restore(envoye), 'Le gabarit contient [PERSONNE_1] et [LITTERAL_PERSONNE_1].')
        self.assertEqual(ps.restore('[PERSONNE_1] et [LITTERAL_PERSONNE_1]'), 'Alice KERVILAC et [PERSONNE_1]')

    def test_P03_circuit_central(self):
        m = self.modele()

        class Echo(FakeHTTP):
            def json(self, method, path, payload=None):
                if path == '/chat/completions':
                    FakeHTTP.answer = payload['messages'][-1]['content']
                return FakeHTTP.json(self, method, path, payload)
        m.http = Echo('https://api.openai.com/v1')
        out = m.complete([{'role': 'user', 'content': self.SOURCE}])
        self.assertEqual(out, self.SOURCE)
        self.assertNotIn('KERVILAC', self.emis())

    def test_P03_table_stable_litteral_toujours_distinct_et_sans_reattribution(self):
        """Connecteur Claude : table stable du compte. Un littéral ne peut pas partager un marqueur, même d'un appel à l'autre ; un marqueur
        déjà attribué ne change jamais de nom (table à trous après une purge comprise)."""
        desk = SimpleNamespace(c={'state_dir': self.c['state_dir']}, db=self.desk.db)
        self.desk.db.execute(claude5675.MASKS)
        for tok, nom in (('[PERSONNE_1]', 'Alice KERVILAC'), ('[PERSONNE_2]', 'Paul DURAND'), ('[PERSONNE_5]', 'Jean MARTIN')):
            self.desk.db.execute('INSERT INTO claude5675_masques VALUES(?,?,?,?,?,?)', ('u:1', tok, 'PERSONNE', pseudo540.entity_key('PERSONNE', nom), nom, 'x'))
        self.desk.db.commit()
        masque = claude5675.Masque(desk, 'u:1', True)
        sortie = masque.out({'texte': 'Gabarit : [PERSONNE_1] et [PERSONNE_7]. Parties : Alice KERVILAC, Mme Claire BERNARD, M. Luc ORLANDEC, M. Guy ZORVAL.'})
        self.assertIn('[LITTERAL_PERSONNE_1]', sortie['texte'])
        self.assertIn('[LITTERAL_PERSONNE_7]', sortie['texte'])
        self.assertEqual(masque.ps.back['[PERSONNE_1]'], 'Alice KERVILAC')
        self.assertEqual(masque.ps.back['[PERSONNE_5]'], 'Jean MARTIN')              # contre-exemple : l'ancien compteur réattribuait le n° 5
        self.assertEqual(sorted(masque.ps.back), ['[PERSONNE_%d]' % n for n in (1, 2, 5, 6, 7, 8)])   # après le dernier numéro employé, jamais dans un trou
        self.assertEqual(masque.inp('Cher [PERSONNE_1], voir [LITTERAL_PERSONNE_1] et [PERSONNE_5].'), 'Cher Alice KERVILAC, voir [PERSONNE_1] et Jean MARTIN.')
        masque.save()
        encore = claude5675.Masque(desk, 'u:1', True)
        self.assertEqual(encore.out('Mme Claire BERNARD et [PERSONNE_3]'), 'Mme [PERSONNE_6] et [LITTERAL_PERSONNE_3]')
        # un marqueur inconnu écrit par Claude lui-même revient tel quel dans le message qui le signale, sans être rétabli ni attribué
        self.assertEqual(encore.inp('Cher [PERSONNE_40]'), 'Cher [PERSONNE_40]')
        self.assertEqual(encore.out({'marqueurs_non_retablis': ['[PERSONNE_40]']}), {'marqueurs_non_retablis': ['[PERSONNE_40]']})


# ================================================================================================ P04
class P04Restitution(Externe):
    def test_P04_marqueur_inconnu_bloque_sans_deviner(self):
        """Témoin de l'original : « Bonjour [PERSONNE_999], dossier [REFERENCE_888]. » était rendu tel quel, sans état central."""
        m = self.modele()
        FakeHTTP.answer = 'Bonjour [PERSONNE_999], dossier [REFERENCE_888].'
        with self.assertRaises(Stop) as ctx:
            m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}])
        self.assertEqual(str(ctx.exception), 'pseudo_marqueur_inconnu')
        ligne = pseudo540.recent(self.c['state_dir'])[0]
        self.assertEqual((ligne['etat'], ligne['code']), ('a_verifier', 'pseudo_marqueur_inconnu'))
        garde = pseudo540.sample(self.c['state_dir'], ligne['id'])
        self.assertIn('[PERSONNE_999]', garde)                   # la réponse est gardée « à vérifier », encore pseudonymisée
        self.assertNotIn('Client DEMO', garde)

    def test_P04_marqueur_altere_bloque(self):
        m = self.modele()
        for reponse, code in (('Bonjour [personne_1].', 'altere'), ('Bonjour [PERSONNE 1].', 'altere'), ('Bonjour [Personne-1].', 'altere'),
                              ('Bonjour personne_1.', 'altere'), ('Dossier [RÉFÉRENCE_1].', 'altere'), ('{"corps":"Bonjour [PERSONNE1]"}', 'altere'),
                              ('Bonjour [LITTÉRAL_PERSONNE_1].', 'altere'), ('Bonjour [PERSONNE_01].', 'inconnu'), ('Bonjour PERSONNE_7.', 'inconnu'),
                              ('Bonjour [LITTERAL_PERSONNE_1].', 'inconnu')):
            with self.subTest(reponse=reponse):
                FakeHTTP.answer = reponse
                with self.assertRaises(Stop) as ctx:
                    m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO (DOS-001).'}])
                self.assertEqual(str(ctx.exception), 'pseudo_marqueur_' + code)
                self.assertEqual(m.restitution56109['etat'], 'a_verifier')
        FakeHTTP.answer = 'Bonjour [PERSONNE_1].'
        self.assertEqual(m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO (DOS-001).'}]), 'Bonjour Client DEMO.')
        self.assertIsNone(m.restitution56109)

    def test_P04_reponse_exacte_et_prose_ordinaire_passent(self):
        m = self.modele()
        FakeHTTP.answer = ('Bonjour [PERSONNE_1] (PERSONNE_1), voir le dossier 12, la personne 2, la référence 4 du bordereau, [Adresse du destinataire], '
                           '[À COMPLÉTER : date], [[CHOIX 1]], pièce [P1], Dossier 2024-12 et numéro 3.')
        out = m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}])
        self.assertEqual(out, FakeHTTP.answer.replace('[PERSONNE_1]', 'Client DEMO').replace('PERSONNE_1', 'Client DEMO'))

    def test_P04_json_guillemets_et_identite_exacte(self):
        m = self.modele()
        FakeHTTP.answer = '{"corps":"Bonjour [PERSONNE_1]"}'
        jeton = pseudo540.NOMS_DU_TOUR.set((('Samira "Zelvani"', 'PERSONNE'),))     # nom lu dans un registre pour la question en cours
        try:
            out = m.complete([{'role': 'user', 'content': 'Ecrire a Samira "Zelvani".'}], json_schema={'type': 'object'})
        finally:
            pseudo540.NOMS_DU_TOUR.reset(jeton)
        self.assertNotIn('Zelvani', self.emis())
        self.assertEqual(json.loads(out)['corps'], 'Bonjour Samira "Zelvani"')

    def test_P04_aucun_secours_vers_une_autre_ia(self):
        """Un refus de confidentialité n'est pas une panne : ni le fournisseur suivant, ni le modèle local ne prennent le relais."""
        from agent import transmission56109 as T
        appels = []

        def enfant(cfg, methode, *args):
            appels.append(cfg.get('provider_id'))
            if cfg.get('provider_id') == 'ollama':
                raise Stop('generation_ia_delai_depasse')
            if cfg.get('provider_id') == 'a':
                raise Stop('pseudo_marqueur_inconnu')
            return 'réponse du second fournisseur'
        m = Model.__new__(Model)
        m.cfg = {'provider_id': 'ollama', 'model': 'local', 'state_dir': self.c['state_dir'],
                 'secours': {'delay': 5, 'candidates': [{'provider_id': 'a', 'model': 'x'}, {'provider_id': 'b', 'model': 'y'}]}}
        m.provider_type, m.provider_id = 'ollama', 'ollama'
        m._call_child = enfant
        with patch('agent.secours564.waited', return_value=0), patch('agent.secours564.record'), patch('agent.data_policy5689.repli_bloque', return_value=False):
            with self.assertRaises(Stop) as ctx:
                m._secours('complete', [], 0, 100, None)
            self.assertEqual(str(ctx.exception), 'pseudo_marqueur_inconnu')
            self.assertEqual(appels, ['ollama', 'a'])
            del appels[:]
            with patch.object(T, 'CODES', frozenset()):         # mutation : garde retiré → le second fournisseur externe répond
                self.assertEqual(m._secours('complete', [], 0, 100, None), 'réponse du second fournisseur')
            self.assertEqual(appels, ['ollama', 'a', 'b'])

    def test_P04_aucun_repli_hybride_silencieux(self):
        from agent import transmission56109 as T
        appels = []

        def enfant(cfg, methode, *args):
            appels.append(cfg.get('provider_id', 'local'))
            if cfg.get('provider_id') == 'ext':
                raise Stop('pseudo_identite_residuelle')
            return 'réponse locale'
        m = Model.__new__(Model)
        m.cfg = {'provider_id': 'ollama', 'model': 'local', 'hybrid_external': {'provider_id': 'ext'}, 'hybrid_config': {'state_dir': self.c['state_dir']}}
        m.provider_type, m.provider_id = 'ollama', 'ollama'
        m._call_child = enfant
        decision = {'external': True, 'fallback_local_on_error': True, 'provider': 'ext', 'model': 'x'}
        with patch.object(Model, '_hybrid_choice', return_value=decision), patch('agent.hybrid400.record'), \
             patch('agent.hybrid400.sanitize_external', return_value={'messages': [], 'payload': {}}), \
             patch('agent.hybrid400.external_config', return_value={'provider_id': 'ext'}):
            for appel in (lambda: m.complete([], 0, 100, None), lambda: m._ask('chat', {})):
                del appels[:]
                with self.assertRaises(Stop) as ctx:
                    appel()
                self.assertEqual(str(ctx.exception), 'pseudo_identite_residuelle')
                self.assertEqual(appels, ['ext'])
            del appels[:]
            with patch.object(T, 'CODES', frozenset()):         # mutation : garde retiré → repli local silencieux
                self.assertEqual(m.complete([], 0, 100, None), 'réponse locale')
            self.assertEqual(appels, ['ext', 'ollama'])

    def test_P04_codes_expliques_et_jamais_eligibles_au_repli(self):
        from agent import routage5682, transmission56109 as T, web
        from agent.hybrid400 import ESCALATABLE_LOCAL_ERRORS
        self.assertGreaterEqual(len(T.CODES), 6)
        for code in T.CODES:
            self.assertIn(code, T.MESSAGES)
            self.assertEqual(web.REASONS[code], T.MESSAGES[code])
            self.assertNotIn(code, routage5682.FALLBACK_ON)
            self.assertNotIn(code, routage5682.ESCALATE_ON)
            self.assertNotIn(code, ESCALATABLE_LOCAL_ERRORS)
            self.assertNotIn('réseau', T.MESSAGES[code].lower())
            self.assertFalse(T.eligible_au_repli(code))
        self.assertTrue(T.eligible_au_repli('fournisseur_ia_injoignable'))


# ================================================================================================ P05
SCHEMA_DYNAMIQUE = {'type': 'object', 'properties': {'texte': {'type': 'string', 'description': 'Rediger pour Client DEMO'}}}


class P05Enveloppe(Externe):
    def test_P05_schema_avec_identite_refuse_avant_transport(self):
        """Témoin de l'original : le schéma strict partait avec « Client DEMO » alors que les messages étaient masqués."""
        for fournisseur, modele, chemin in (('openai', 'gpt-5', '/chat/completions'), ('anthropic', 'claude-sonnet-5-5', '/messages')):
            with self.subTest(fournisseur=fournisseur):
                m = self.modele(fournisseur, modele, structured_mode='strict')
                with self.assertRaises(Stop) as ctx:
                    m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}], json_schema=SCHEMA_DYNAMIQUE)
                self.assertEqual(str(ctx.exception), 'pseudo_schema_avec_identite')
                self.assertEqual(self.corps(chemin), [])
                ligne = pseudo540.recent(self.c['state_dir'])[0]
                self.assertEqual((ligne['etat'], ligne['code']), ('refuse', 'pseudo_schema_avec_identite'))

    def test_P05_identite_de_la_requete_dans_une_cle_ou_un_enum(self):
        """Identité reconnue dans la requête (hors registre), reprise dans une clé, une énumération ou un exemple du schéma."""
        for schema in ({'type': 'object', 'properties': {'Alice KERVILAC': {'type': 'string'}}},
                       {'type': 'object', 'properties': {'partie': {'type': 'string', 'enum': ['ALICE KERVILAC', 'autre']}}},
                       {'type': 'object', 'properties': {'partie': {'type': 'string', 'examples': ['Kervilac']}}}):
            with self.subTest(schema=schema):
                m = self.modele()
                with self.assertRaises(Stop) as ctx:
                    m.complete([{'role': 'user', 'content': 'Ecrire a Mme Alice KERVILAC.'}], json_schema=schema)
                self.assertEqual(str(ctx.exception), 'pseudo_schema_avec_identite')
        self.assertEqual(self.corps(), [])

    def test_P05_schemas_techniques_du_produit_acceptes(self):
        """Clés et valeurs techniques (« client », « parties », « source ») : aucun faux refus, même quand le registre connaît « Client DEMO »."""
        from agent import model as M
        self.partie(self.dossier(client='Source SARL'), nom='Parties SAS', role='adverse', type_personne='morale')
        m = self.modele(structured_mode='strict')
        for schema in (M.TRIAGE, M.COMPOSE, M.CASE_BRIEF, M.FICHE5627, M.MANUAL_DRAFT_FOURNISSEUR, M.MAIL_CASE_DIFFERENTIAL, M.HEARING_PREPARATION):
            FakeHTTP.answer = '{}'
            self.assertEqual(m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}], json_schema=schema), '{}')
        self.assertEqual(len(self.corps()), 7)
        self.assertNotIn('Client DEMO', self.emis())

    def test_P05_octets_figes_emis_tels_quels_et_recu_sans_contenu(self):
        """Avec le vrai transport (``common.HTTP``), dont seule l'émission est capturée : les octets émis sont ceux qui ont été contrôlés."""
        from agent import common, transmission56109 as T
        m = self.modele(structured_mode='strict')
        emis, figes = [], []

        def request(http, method, url, data=None, headers=None, limit=0):
            emis.append((url, data))
            return json.dumps({'choices': [{'message': {'content': 'Bonjour [PERSONNE_1].'}, 'finish_reason': 'stop'}], 'usage': {}}).encode()
        origine = T.figer

        def figer(*a, **k):
            figes.append(origine(*a, **k))
            return figes[-1]
        m.http = common.HTTP('https://api.openai.com/v1')
        with patch.object(common.HTTP, 'request', request), patch.object(T, 'figer', figer):
            out = m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO, à Hélène.'}], json_schema=M_SCHEMA)
        self.assertEqual(out, 'Bonjour Client DEMO.')
        self.assertEqual(len(emis), 1)
        self.assertEqual(emis[0][1], figes[0].octets)            # exactement ces octets
        self.assertNotIn(b'Client DEMO', emis[0][1])
        recu = figes[0].recu
        self.assertEqual(set(recu), {'sha256', 'octets'})
        self.assertEqual(recu, {'sha256': hashlib.sha256(emis[0][1]).hexdigest(), 'octets': len(emis[0][1])})
        ligne = pseudo540.recent(self.c['state_dir'])[0]
        self.assertEqual((ligne['etat'], ligne['empreinte'], ligne['octets']), ('emis', recu['sha256'], recu['octets']))

    def test_P05_corps_modifie_apres_le_controle_sans_effet(self):
        from agent import transmission56109 as T
        m = self.modele()
        ps = Pseudonymizer(pseudo_sources(self.c))
        req = {'model': 'gpt-5', 'messages': ps.apply_messages([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}])}
        m._pseudo56109 = ps
        corps = T.figer(m, req)
        req['messages'][0]['content'] = 'Ecrire a Client DEMO.'           # l'objet d'origine change après le contrôle
        req['metadata'] = {'client': 'Client DEMO'}
        m.http.json('POST', '/chat/completions', T.emettre(m, corps))
        self.assertNotIn('Client DEMO', self.emis())
        self.assertNotIn(b'Client DEMO', corps.octets)
        with self.assertRaises(Stop) as ctx:                              # et le même corps, recontrôlé, est refusé
            T.figer(m, req)
        self.assertEqual(str(ctx.exception), 'pseudo_identite_residuelle')

    def test_P05_metadonnees_noms_de_fichiers_et_outils(self):
        from agent import transmission56109 as T
        m = self.modele()
        m._pseudo56109 = Pseudonymizer(pseudo_sources(self.c))
        base = {'model': 'gpt-5', 'messages': [{'role': 'user', 'content': 'Bonjour.'}]}
        T.figer(m, base)
        T.figer(m, {**base, 'metadata': {'client': 'source', 'documents': ['dossier']}, 'user': 'u-42'})      # mots techniques : admis
        for ajout, code in (({'metadata': {'dossier': 'DOS-001'}}, 'pseudo_identite_residuelle'), ({'user': 'client@example.test'}, 'pseudo_identite_residuelle'),
                            ({'tools': [{'type': 'function', 'function': {'name': 'lire', 'description': 'Pièce de Client DEMO'}}]}, 'pseudo_identite_residuelle'),
                            ({'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Bonjour.'}, {'type': 'file', 'filename': 'CLIENT DEMO.pdf'}]}]},
                             'pseudo_canal_binaire_non_qualifie'),
                            ({'Client DEMO': 1}, 'pseudo_identite_residuelle'), ({'system': 'Pour Client DEMO.'}, 'pseudo_identite_residuelle'),
                            ({'instructions': 'Pour client demo.'}, 'pseudo_identite_residuelle')):
            with self.subTest(ajout=ajout):
                with self.assertRaises(Stop) as ctx:
                    T.figer(m, {**base, **ajout})
                self.assertEqual(str(ctx.exception), code)

    def test_P05_image_non_qualifiee_refusee_image_preparee_admise(self):
        from agent import transmission56109 as T
        m = self.modele()
        m._pseudo56109 = Pseudonymizer({'state_dir': self.c['state_dir']})
        image = vision56106.Image('image/png', b'octets-fictifs-masques', 10, 10, masquee=True, zones=1)
        image.recu = {'source': 'a' * 64, 'empreinte': hashlib.sha256(image.data).hexdigest(), 'detecteur': pseudo540.VERSION_DETECTEUR}
        blocs = lambda i: [{'type': 'text', 'text': 'Voir.'}, {'type': 'image_url', 'image_url': {'url': i.data_url()}}]
        req = {'model': 'gpt-5', 'messages': [{'role': 'user', 'content': blocs(image)}]}
        m._images56106 = [image]
        self.assertIn(b'image_url', T.figer(m, req).octets)
        anthropic = {'model': 'x', 'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': image.b64()}}]}]}
        T.figer(m, anthropic)
        m._images56106 = []
        for corps in (req, anthropic, {'model': 'x', 'messages': [{'role': 'user', 'content': 'data:image/png;base64,AAAA'}]},
                      {'model': 'x', 'messages': [{'role': 'user', 'content': [{'type': 'document', 'source': {'data': 'fictif'}}]}]},
                      {'model': 'x', 'messages': [{'role': 'user', 'content': [{'type': 'input_file', 'file_id': 'f1'}]}]}):
            with self.subTest(corps=str(corps)[:60]), self.assertRaises(Stop) as ctx:
                T.figer(m, corps)
            self.assertEqual(str(ctx.exception), 'pseudo_canal_binaire_non_qualifie')
        sans_recu = vision56106.Image('image/png', b'octets-fictifs-masques', 10, 10, masquee=True, zones=1)   # « masquee=True » seul ne prouve rien
        m._images56106 = [sans_recu]
        with self.assertRaises(Stop):
            T.figer(m, req)

    def test_P05_chatgpt_controle_des_octets_avant_le_transport(self):
        from agent import chatgpt5662 as cg
        envois = []

        def transport(method, url, body, headers, timeout=0, stream=False):
            envois.append(body)
            return [b'data: ' + json.dumps({'type': 'response.completed', 'response': {'output': [{'content': [{'type': 'output_text', 'text': 'ok'}]}]}}).encode()]
        vus = []

        def controle(octets):
            vus.append(octets)
            return octets
        with patch.object(cg, 'access_token', return_value='jeton-fictif'):
            self.assertEqual(cg.respond('', 'modele-fictif', [{'role': 'user', 'content': 'Q'}], transport=transport, controle=controle)[0], 'ok')
            self.assertEqual(vus, envois)                         # les octets contrôlés sont les octets émis

            def refuse(octets):
                raise Stop('pseudo_identite_residuelle')
            with self.assertRaises(Stop):
                cg.respond('', 'modele-fictif', [{'role': 'user', 'content': 'Q'}], transport=transport, controle=refuse)
        self.assertEqual(len(envois), 1)                          # refus : le transport n'est pas appelé

    def test_P05_chatgpt_schema_et_enveloppe_dans_le_circuit_central(self):
        from agent import chatgpt5662 as cg
        self.route_all('openai', 'gpt-5')
        cfg = routed_config(self.c, 'mail_drafting')
        cfg.update(provider_type='chatgpt', provider_id='chatgpt', model='modele-fictif', sans_liste5682=True)
        envois = []

        def http(method, url, body, headers, timeout=0, stream=False):
            envois.append(body)
            return [b'data: ' + json.dumps({'type': 'response.completed', 'response': {'output': [{'content': [{'type': 'output_text', 'text': 'Bonjour [PERSONNE_1].'}]}]}}).encode()]
        with patch.object(cg, 'access_token', return_value='jeton-fictif'), patch.object(cg, 'http', http):
            with self.assertRaises(Stop) as ctx:
                Model(cfg).complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}], json_schema=SCHEMA_DYNAMIQUE)
            self.assertEqual(str(ctx.exception), 'pseudo_schema_avec_identite')
            self.assertEqual(envois, [])
            self.assertEqual(Model(cfg).complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}]), 'Bonjour Client DEMO.')
        self.assertEqual(len(envois), 1)
        self.assertNotIn(b'Client DEMO', envois[0])
        ligne = pseudo540.recent(self.c['state_dir'])[0]
        self.assertEqual((ligne['etat'], ligne['empreinte']), ('emis', hashlib.sha256(envois[0]).hexdigest()))

    def test_P05_pause_entre_le_controle_et_l_emission(self):
        from agent import transmission56109 as T
        m = self.modele()
        with patch('agent.arret5643.reason_to_stop', return_value='ia_en_pause'):
            with self.assertRaises(Stop) as ctx:
                m.complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}])
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        self.assertEqual(self.corps(), [])
        self.assertNotIn('ia_en_pause', T.CODES)                  # la pause garde son code et ses règles de reprise

    def test_P05_transport_direct_sans_masquage_refuse(self):
        """Une voie qui appellerait le transport sans passer par ``_leaf`` : le contrôle relit le registre et refuse l'identité connue."""
        m = self.modele()
        with self.assertRaises(Stop) as ctx:
            m._openai_compatible([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}], 0, 100, None)
        self.assertEqual(str(ctx.exception), 'pseudo_identite_residuelle')
        self.assertEqual(self.corps(), [])


M_SCHEMA = {'type': 'object', 'properties': {'body': {'type': 'string'}}, 'required': ['body'], 'additionalProperties': False}


# ================================================================================================ P06
class P06ConnecteurClaude(Externe):
    AUTH = {'origin': 'https://cabinet.example.test', 'prefix': '/agent-courriel', 'poste': False, 'enabled': True}

    def test_P06_api_et_connecteur_distingues_sans_promesse_de_restitution(self):
        texte = claude5675.distinction_html()
        for attendu in ('API Claude', 'connecteur', 'marqueurs', 'ne rétablit pas'):
            self.assertIn(attendu, texte)
        consignes = claude5675.instructions(True)
        self.assertIn('ne sont pas rétablis dans cette conversation', consignes)          # dit à Claude lui-même
        self.assertIn('[LITTERAL_PERSONNE_1]', consignes)
        self.assertNotIn('rétablis dans cette conversation', claude5675.instructions(False))
        self.assertIn('ne sont pas rétablis', claude5675.skill_markdown('https://cabinet.example.test/agent-courriel/claude/mcp'))
        for promesse in ('Claude affiche les vrais noms', 'rétablit les noms dans Claude', 'anonyme'):
            self.assertNotIn(promesse, texte + consignes)

    def test_P06_exception_en_clair_visible_et_journalisee(self):
        self.assertIn('en clair', claude5675.alerte_clair_html(True, False))
        self.assertEqual(claude5675.alerte_clair_html(True, True), '')         # imposée : aucune exception possible
        self.assertEqual(claude5675.alerte_clair_html(False, False), '')
        self.assertEqual(claude5675.detail_appel(False), 'en clair')
        self.assertEqual(claude5675.detail_appel(True), '')

    def test_P06_destination_propre_dans_la_politique_des_donnees(self):
        import inspect
        source = inspect.getsource(claude5675.call_tool)
        self.assertIn("'connecteur_claude'", source)              # le connecteur n'emprunte pas l'autorisation de l'API (« llm »)
        self.assertNotIn("'llm'", source)

    def test_P06_table_des_correspondances_droits_retention_export(self):
        from agent import export56108
        exclues = {t for groupe in [export56108.EXPORT_EXCLUES] for t in (groupe if isinstance(groupe, dict) else dict(groupe))}
        self.assertIn('claude5675_masques', exclues)              # jamais dans un export
        self.assertIn('pseudo56109_revision', exclues)
        desk = SimpleNamespace(c={'state_dir': self.c['state_dir']}, db=self.desk.db)
        a = claude5675.Masque(desk, 'u:1', True)
        self.assertEqual(a.out('Mme Alice KERVILAC et M. Paul DURAND'), 'Mme [PERSONNE_1] et M. [PERSONNE_2]')
        a.save()
        b = claude5675.Masque(desk, 'u:2', True)                  # droits : la table d'un compte ne sert jamais à un autre
        self.assertEqual(b.ps.back, {})
        self.assertEqual(b.inp('[PERSONNE_1]'), '[PERSONNE_1]')
        self.assertEqual(claude5675.purger_masques(self.desk.db, 'u:1'), 2)     # révocation : correspondances retirées
        self.assertFalse(self.desk.db.execute("SELECT 1 FROM claude5675_masques WHERE compte='u:1' AND original<>''").fetchone())
        c = claude5675.Masque(desk, 'u:1', True)
        self.assertEqual(c.inp('Cher [PERSONNE_1]'), 'Cher [PERSONNE_1]')       # un ancien marqueur n'est plus rétabli, ni deviné
        self.assertEqual(c.leftovers(c.inp('Cher [PERSONNE_1]')), ['[PERSONNE_1]'])
        self.assertEqual(c.out('Mme Claire BERNARD'), 'Mme [PERSONNE_3]')       # et jamais réattribué à quelqu'un d'autre
        self.assertEqual(claude5675.purger_masques(self.desk.db, 'u:1'), 0)     # rejouable

    def test_P06_revocation_du_dernier_acces_purge_les_correspondances(self):
        etat = self.c['state_dir']
        comptes = claude5675.connect(etat)                        # accès : base des comptes ; correspondances : base du bureau
        self.addCleanup(comptes.close)
        comptes.execute("INSERT INTO claude5675_acces VALUES('g1',7,'c','Claude',1,'s','2026-11-01T10:00:00+00:00','')")
        comptes.execute("INSERT INTO claude5675_acces VALUES('g2',7,'c2','Claude Code',0,'s','2026-11-01T10:00:00+00:00','')")
        comptes.execute("INSERT INTO claude5675_acces VALUES('g3',8,'c','Claude',1,'s','2024-01-01T10:00:00+00:00','2024-02-01T10:00:00+00:00')")
        comptes.commit()
        self.desk.db.execute(claude5675.MASKS)
        for compte in ('u:7', 'u:8', 'u:9'):
            self.desk.db.execute('INSERT INTO claude5675_masques VALUES(?,?,?,?,?,?)', (compte, '[PERSONNE_1]', 'PERSONNE', 'alice kervilac', 'Alice KERVILAC', 'x'))
        self.desk.db.commit()
        reste = lambda compte: self.desk.db.execute("SELECT COUNT(*) FROM claude5675_masques WHERE compte=? AND original<>''", (compte,)).fetchone()[0]
        self.assertEqual(claude5675._acces_en_clair(etat), 1)     # exception en clair comptée pour l'administrateur
        with patch('agent.connexion5648._log') as journal:
            claude5675.remove_grant(etat, {'id': 7, 'owner': 'claire@example.test'}, 'g1')
            self.assertEqual(reste('u:7'), 1)                     # il reste un accès : la table sert encore
            claude5675.remove_grant(etat, {'id': 7, 'owner': 'claire@example.test'}, 'g2')
            self.assertEqual(reste('u:7'), 0)                     # dernier accès retiré : correspondances retirées
            self.assertIn('claude_correspondances', [c.args[2] for c in journal.call_args_list])
            self.assertEqual((reste('u:8'), reste('u:9')), (1, 1))
            claude5675.save_settings(etat, {'owner': 'cabinet'}, {'actif': '1'})     # durée de conservation, à l'enregistrement des réglages
        self.assertEqual((reste('u:8'), reste('u:9')), (0, 0))    # sans usage depuis plus d'un an ; sans aucun accès
        self.assertEqual([tuple(r) for r in self.desk.db.execute("SELECT marqueur,cle,original FROM claude5675_masques WHERE compte='u:8'")],
                         [('[PERSONNE_1]', claude5675.PURGE, '')])

    def test_P06_passage_en_clair_refuse_si_impose_et_journalise_sinon(self):
        etat = self.c['state_dir']
        comptes = claude5675.connect(etat)
        self.addCleanup(comptes.close)
        comptes.execute("INSERT INTO claude5675_acces VALUES('g1',7,'c','Claude',1,'s','2026-11-01T10:00:00+00:00','')")
        comptes.commit()
        with patch('agent.connexion5648._log') as journal:
            with self.assertRaises(Stop) as ctx:                  # comportement existant préservé : imposée par défaut
                claude5675.set_pseudo(etat, {'id': 7, 'owner': 'claire@example.test'}, 'g1', '0')
            self.assertEqual(str(ctx.exception), 'pseudonymisation_imposee_par_le_cabinet')
            claude5675.save_settings(etat, {'owner': 'cabinet'}, {'pseudo_impose': '0'})
            out = claude5675.set_pseudo(etat, {'id': 7, 'owner': 'claire@example.test'}, 'g1', '0')
            self.assertIn('en clair', out['message'])
            self.assertIn('claude_en_clair', [c.args[2] for c in journal.call_args_list])
            self.assertEqual(claude5675._acces_en_clair(etat), 1)
            claude5675.save_settings(etat, {'owner': 'cabinet'}, {'pseudo_impose': '1'})    # politique imposée : les accès en clair sont repris
        self.assertEqual(claude5675._acces_en_clair(etat), 0)


# ================================================================================================ information de l'utilisateur
class Information(Externe):
    def test_P01_parametres_dit_ce_qui_est_masque_les_limites_et_l_etat_reel(self):
        from agent import ia540, transmission56109 as T
        self.modele().complete([{'role': 'user', 'content': 'Ecrire a Client DEMO.'}])
        with patch('agent.economie569.model_profile', return_value={'available': False, 'models': []}):      # moteur local : hors sujet ici, et aucun réseau
            page = ia540.page(self.desk, '/p', lambda titre, corps, *a: corps, 'jeton')
        for attendu in (T.TITRE, T.LIMITES_TITRE, pseudo540.LIMITES[0], pseudo540.LIMITES[4], T.ETATS_AIDE['refuse'], 'ne prouve aucune transmission',
                        '<th>État</th>', '<td>Émis</td>'):
            self.assertIn(attendu, page)
        bloc = T.information_html().lower()
        for promesse in ('protection totale', 'anonyme', 'aucun risque', 'garanti'):
            self.assertNotIn(promesse, bloc)
        self.assertNotIn('tel qu’il est parti', page)             # l'ancien texte confondait « préparé » et « parti »

    def test_P01_textes_nouveaux_traduits_en_de_es(self):
        import test_v56107_langues as L
        from agent import ecrans5632 as E, transmission56109 as T
        for lang in ('en', 'de', 'es'):
            for html in (T.information_html(), claude5675.distinction_html(), claude5675.alerte_clair_html(True, False)):
                self.assertEqual(L.reste_francais(L.visibles(E.translate_html(html, lang))), [], lang)
            table = E.dictionary(lang)
            for texte in list(T.MESSAGES.values()) + list(pseudo540.LIMITES) + [pseudo540.NOTE_APERCU] + [v for v in pseudo540.ETATS.values()]:
                rendu = E.phrase(texte, table)
                self.assertIsNotNone(rendu, (lang, texte))
                self.assertEqual(L.reste_francais([rendu]), [], (lang, texte))

    def test_P01_apercu_et_politique_des_donnees_rappellent_la_limite(self):
        import inspect
        from agent import data_policy5689, ia540
        out = ia540.handle(self.desk, 'm540/preview', {'text': 'Ecrire a Client DEMO au sujet de yusuf ardanel.'})
        self.assertEqual(out['note'], pseudo540.NOTE_APERCU)
        self.assertEqual(out['limites'], list(pseudo540.LIMITES))
        self.assertIn('yusuf ardanel', out['sent'])
        self.assertNotIn('Client DEMO', out['sent'])
        self.assertIn('_limites56109[0]', inspect.getsource(data_policy5689.synthese_mission))


import test_v56106_vision as TV                      # montage de la lecture locale simulée (faux rendu, faux OCR) ; ses tests ne sont pas rejoués ici


class Images(unittest.TestCase):
    setUp = TV.Base.setUp
    piece = TV.Base.piece
    externe = TV.Base.externe
    appel = TV.Base.appel

    def test_P05_image_masquee_avec_son_recu_refaite_a_chaque_emission(self):
        cfg = self.externe('openai', 'gpt-5')
        piece = self.piece()
        lot = lambda: TV.V.Lot([piece], self.c, 'DOS-001', False)
        self.appel(cfg, lot())
        (data,) = TV.images_envoyees(TV.dernier('/chat/completions'), 'openai')
        self.assertTrue(TV.masquage_respecte(data))
        premiere = piece._masquee
        self.assertEqual(premiere.recu['empreinte'], hashlib.sha256(data).hexdigest())
        self.assertEqual((premiere.recu['source'], premiere.recu['detecteur']), (piece.sha, pseudo540.VERSION_DETECTEUR))
        self.assertIsInstance(premiere.recu['revision'], int)
        self.assertEqual(pseudo540.recent(self.c['state_dir'])[0]['etat'], 'emis')
        # P01 : un mot « sûr » de la page devient une partie du dossier ; l'émission suivante refait le masquage avec le registre du moment
        self.d.db.execute("INSERT INTO conflicts500_parties VALUES('z1','DOS-001','Signé','signe','adverse','','manuel','x')")
        self.d.db.commit()
        self.appel(cfg, lot())
        (data2,) = TV.images_envoyees(TV.dernier('/chat/completions'), 'openai')
        self.assertIsNot(piece._masquee, premiere)
        self.assertNotEqual(hashlib.sha256(data2).hexdigest(), premiere.recu['empreinte'])
        self.assertFalse(TV.masquage_respecte(data2))             # « signé » n'est plus lisible : noirci comme nom du registre

    def test_P05_recu_falsifie_ou_d_une_autre_image_refuse(self):
        cfg = self.externe('openai', 'gpt-5')
        origine = TV.V.Piece.image_masquee

        def falsifie(piece, config):
            image = origine(piece, config)
            image.recu = {**image.recu, 'empreinte': '0' * 64}
            return image
        with patch.object(TV.V.Piece, 'image_masquee', falsifie):
            with self.assertRaises(Stop) as ctx:
                self.appel(cfg, TV.V.Lot([self.piece()], self.c, 'DOS-001', False))
        self.assertEqual(str(ctx.exception), 'pseudo_canal_binaire_non_qualifie')
        self.assertFalse([p for p, _ in TV.FakeHTTP.payloads if p == '/chat/completions'])


if __name__ == '__main__':
    unittest.main()

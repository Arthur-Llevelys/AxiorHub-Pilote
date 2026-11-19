"""5.6.108 (lot S1) : synchronisation des postes sans serveur — journal d'opérations signées, identités, espaces, réception, conflits.

Plusieurs postes simulés dans le même processus : un ``Desk`` par dossier temporaire, transport réel de la liaison 5.6.103 sur 127.0.0.1
(``boucle=True`` : essais seulement, jamais depuis l'interface), ports choisis par le système. Données fictives (DURAND, MARTIN, BERNARD,
ZORVAL, DOS-001, example.test). Aucun modèle, aucun appel réseau hors de la boucle locale.
"""
import base64
import json
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch

from agent import liaison56103 as L, sync56108 as S, sync56108_objets as O, dossiers5697 as D, membres5650 as mb
from agent.acces5697 import Acteur
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures

RACINE = Path(__file__).resolve().parents[1]
ADMIN = Acteur('cabinet', 'administrateur')


class Poste:
    """Un poste simulé : sa base, son identité de liaison (créée à la demande), sa liaison sur 127.0.0.1 si démarrée."""

    def __init__(self, test, nom, personne):
        f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        f.setUp()
        test.addCleanup(f.doCleanups)
        self.c, self.nom, self.personne, self.test = f.c, nom, personne, test
        self.desk = Desk(self.c)
        test.addCleanup(self._fermer)
        self.desk.db.execute(mb.SCHEMA)
        self.desk.db.execute("INSERT OR REPLACE INTO membres5650(owner,name,role,first_seen,last_seen) VALUES('cabinet',?,'administrateur','2026-11-15','2026-11-15')",
                             (personne,))
        self.desk.db.commit()
        self.desk.setting(L.REGLAGE_NOM, nom)
        self.liaison = None

    def _fermer(self):
        if self.liaison is not None:
            self.liaison.arreter()
        try:
            self.desk.db.close()
        except Exception:   # noqa: BLE001
            pass

    @property
    def ident(self):
        return L.identite(self.c['state_dir'])

    def ouvrir(self):
        return Desk(self.c)

    def demarrer(self):
        if self.liaison is None:
            self.liaison = L.Liaison(self.ouvrir, boucle=True).demarrer('127.0.0.1', 0)
            self.desk.setting(L.REGLAGE_PORT, self.liaison.port)
        return self.liaison

    def arreter(self):
        if self.liaison is not None:
            self.liaison.arreter()
            self.liaison = None

    def sync(self):
        return self.demarrer().synchroniser(forcer=True)

    def dossier(self, titre, ref, **extra):
        return D.creer(self.desk, ADMIN, {'titre': titre, 'reference': ref, 'client': 'ZORVAL', **extra}, 'cle-%s-%s' % (ref, time.time_ns()))['id']

    def objet(self, genre, local_id):
        return O.objet_de(self.desk, genre, local_id)

    def local(self, gid):
        return O.local_de(self.desk, gid)


def appairer(a, b):
    """``b`` affiche un code (fenêtre d'écoute), ``a`` le saisit."""
    b.demarrer()
    code = L.nouveau_code(b.desk)['code']
    return L.appairer(a.desk, '127.0.0.1', b.liaison.port, code, boucle=True)


def relier(test, *postes):
    """Le premier crée l'espace du cabinet ; chacun des autres rejoint (appairage par code avec le premier, adhésion acceptée)."""
    a = postes[0]
    S.creer_espace(a.desk, 'Cabinet ZORVAL', a.personne, 'cabinet')
    for b in postes[1:]:
        S.ouvrir_rejoindre(b.desk, b.personne, 'cabinet')
        b.demarrer()
        appairer(b, a)
        b.sync()                                    # demande d'adhésion (capacités)
        S.accepter_adhesion(a.desk, b.ident.id, 'cabinet')
        b.sync()                                    # invitation signée : B devient membre
        test.assertIsNotNone(S.espace_cabinet(b.desk), 'adhésion de ' + b.nom)
    return a


class S1Ecoute(unittest.TestCase):
    """Décision du cabinet : disponible par défaut, aucune écoute sans espace + poste appairé ; désactivable."""

    def test_S1_installation_neuve_fonction_disponible_aucune_ecoute(self):
        a = Poste(self, 'Poste accueil', 'Me DURAND')
        self.assertFalse(S.desactivee(a.desk))                    # disponible, sans réglage à activer
        self.assertIsNone(S.espace_cabinet(a.desk))               # aucun espace créé par l'installation ou la mise à jour
        self.assertFalse(S.ecoute_requise(a.desk))
        self.assertFalse(L.transport_actif(a.desk))
        cfg = Path(a.c['state_dir']).parent / 'config.json'
        cfg.write_text(json.dumps(a.c), encoding='utf-8')
        self.assertFalse(L.active_dans(cfg))
        with patch.object(L.Liaison, 'demarrer', side_effect=AssertionError('aucun port ne doit être ouvert')), \
                patch.object(L.socket, 'socket', side_effect=AssertionError('aucun socket ne doit être créé')):
            self.assertEqual(L.service(a.c, intervalle=0), 0)
        self.assertFalse(a.desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name LIKE 'sync56108_%'").fetchone())

    def test_S1_espace_seul_sans_poste_appaire_n_ecoute_pas(self):
        a = Poste(self, 'Poste accueil', 'Me DURAND')
        S.creer_espace(a.desk, 'Cabinet', 'Me DURAND', 'cabinet')
        self.assertFalse(S.ecoute_requise(a.desk))

    def test_S1_apres_espace_et_appairage_ecoute_puis_desactivation_ferme(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        L._consommer_code(a.c['state_dir'], True)
        self.assertTrue(S.ecoute_requise(a.desk))
        self.assertTrue(S.ecoute_requise(b.desk))
        cfg = Path(a.c['state_dir']).parent / 'config.json'
        cfg.write_text(json.dumps(a.c), encoding='utf-8')
        self.assertTrue(L.active_dans(cfg))
        S.desactiver(a.desk, True, 'cabinet')
        self.assertFalse(S.ecoute_requise(a.desk))
        self.assertFalse(L.transport_actif(a.desk))
        self.assertFalse(L.active_dans(cfg))
        a.arreter()
        self.assertEqual(L.service(a.c, intervalle=0), 0)          # le processus s'arrête de lui-même : écoute fermée

    def test_S1_code_affiche_ouvre_une_fenetre_d_ecoute_bornee(self):
        a = Poste(self, 'Poste A', 'Me DURAND')
        S.creer_espace(a.desk, 'Cabinet', 'Me DURAND', 'cabinet')
        L.nouveau_code(a.desk, duree=60)
        self.assertTrue(S.ecoute_requise(a.desk))
        L._consommer_code(a.c['state_dir'], True)
        self.assertFalse(S.ecoute_requise(a.desk))

    def test_S1_messages_restent_soumis_a_l_activation_de_la_liaison(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        with self.assertRaisesRegex(Stop, 'liaison_desactivee'):
            L._traiter(a.desk, b.ident.id, {'t': 'message', 'gid': 'm-' + '0' * 32}, b'', a.ident.id)


class S1Journal(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, self.a, self.b)

    def partager(self, poste, titre='Bail ZORVAL', ref='ZOR-001'):
        lid = poste.dossier(titre, ref)
        S.partager_dossier(poste.desk, lid)
        return lid, poste.objet('dossier', lid)['objet']

    def derniere_op(self, poste, objet):
        r = poste.desk.db.execute('SELECT brut FROM sync56108_operations WHERE objet=? ORDER BY rang DESC LIMIT 1', (objet,)).fetchone()
        return json.loads(r[0])

    def test_S1_identite_globale_et_chemin_local(self):
        lid, gid = self.partager(self.a)
        self.assertRegex(gid, r'^dos-[0-9a-f]{32}$')
        self.assertNotEqual(gid, lid)
        op = self.derniere_op(self.a, gid)
        self.assertNotIn('chemin', json.dumps(op['charge']))            # le chemin physique reste local
        self.b.sync()
        lb = self.b.local(gid)
        self.assertTrue(lb and lb != lid)
        self.assertEqual(D.lire(self.b.desk, ADMIN, lb)['titre'], 'Bail ZORVAL')

    def test_S1_commande_locale_et_capture_dans_la_meme_transaction(self):
        lid, gid = self.partager(self.a)
        db = self.a.desk.db
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE dossiers5697 SET titre='Annulé' WHERE id=?", (lid,))
        self.assertTrue(db.execute("SELECT 1 FROM sync56108_capture WHERE local_id=?", (lid,)).fetchone())
        db.rollback()                                                    # commande abandonnée : aucune intention de sortie
        self.assertFalse(db.execute("SELECT 1 FROM sync56108_capture WHERE local_id=?", (lid,)).fetchone())
        D.modifier(self.a.desk, ADMIN, lid, 1, {'titre': 'Bail ZORVAL (renouvellement)'}, 'cle-modif-0001')
        self.assertTrue(db.execute("SELECT 1 FROM sync56108_capture WHERE local_id=?", (lid,)).fetchone())
        self.assertEqual(S.journaliser(self.a.desk), 1)
        self.assertEqual(self.derniere_op(self.a, gid)['type'], 'dossier.modification')

    def test_S1_rien_n_est_partage_sans_choix_explicite(self):
        lid = self.a.dossier('Succession MARTIN', 'ZOR-002')
        self.assertEqual(S.journaliser(self.a.desk), 0)
        self.assertIsNone(self.a.objet('dossier', lid))
        self.b.sync()
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM dossiers5697 WHERE titre='Succession MARTIN'").fetchone()[0], 0)

    def test_S1_rejeu_d_un_lot_ne_cree_rien_deux_fois(self):
        lid, gid = self.partager(self.a)
        op = self.derniere_op(self.a, gid)
        self.b.sync()
        self.assertEqual(S.recevoir(self.b.desk, op, self.a.ident.id)['etat'], 'deja_recue')
        self.assertEqual(S.recevoir_lot(self.b.desk, [op, op], self.a.ident.id)[0], [op['id'], op['id']])
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM dossiers5697 WHERE titre='Bail ZORVAL'").fetchone()[0], 1)

    def test_S1_operation_alteree_refusee(self):
        lid, gid = self.partager(self.a)
        op = self.derniere_op(self.a, gid)
        self.b.sync()
        faux = dict(op, charge=dict(op['charge'], titre='Autre'))
        with self.assertRaisesRegex(Stop, 'signature_refusee|operation_alteree'):
            S.recevoir(self.b.desk, faux, self.a.ident.id)

    def test_S1_signature_d_origine_exigee_avant_toute_ecriture(self):
        lid, gid = self.partager(self.a)
        op = self.derniere_op(self.a, gid)
        faux = S.signer(self.b.ident, dict(op))                           # B signe en se faisant passer pour A
        avant = self.b.desk.db.execute('SELECT COUNT(*) FROM sync56108_operations').fetchone()[0]
        with self.assertRaisesRegex(Stop, 'signature_refusee'):
            S.recevoir(self.b.desk, faux, self.a.ident.id)
        self.assertEqual(self.b.desk.db.execute('SELECT COUNT(*) FROM sync56108_operations').fetchone()[0], avant)
        # mutation en mémoire : sans le contrôle de signature, l'opération usurpée passerait (le test ci-dessus le détecte)
        with patch.object(S, 'verifier_signature', lambda cle, op: True):
            self.assertIn(S.recevoir(self.b.desk, faux, self.a.ident.id)['etat'], ('recu', 'conflit'))

    def test_S1_type_hors_liste_et_charge_inconnue_refuses(self):
        lid, gid = self.partager(self.a)
        op = self.derniere_op(self.a, gid)
        for mauvais in ({'type': 'sql.executer'}, {'charge': dict(op['charge'], sql='DROP TABLE dossiers5697')}, {'objet': 'tac-' + 'a' * 32}):
            m = S.signer(self.a.ident, {**op, **mauvais, 'id': S.nouvel_id('op')})
            with self.assertRaisesRegex(Stop, 'operation_invalide|charge_invalide'):
                S.recevoir(self.b.desk, m, self.a.ident.id)

    def test_S1_trou_de_sequence_refuse_puis_redemande(self):
        self.b.sync()
        l1, g1 = self.partager(self.a, 'Dossier un', 'ZOR-011')
        l2, g2 = self.partager(self.a, 'Dossier deux', 'ZOR-012')
        op2 = self.derniere_op(self.a, g2)
        with self.assertRaisesRegex(Stop, 'plage_manquante'):
            S.recevoir(self.b.desk, op2, self.a.ident.id)
        self.assertEqual(self.b.local(g2), '')
        self.b.sync()                                                     # le résumé redemande depuis le curseur : tout arrive dans l'ordre
        self.assertTrue(self.b.local(g1) and self.b.local(g2))

    def test_S1_sequence_par_espace_et_par_origine(self):
        lid, gid = self.partager(self.a)
        esp = S.espace_cabinet(self.a.desk)['id']
        autre = S.nouvel_id('esp')
        self.assertEqual(S._derniere_seq(self.a.desk, autre, self.a.ident.id), 0)       # un autre espace a sa propre séquence
        self.assertGreaterEqual(S._derniere_seq(self.a.desk, esp, self.a.ident.id), 2)
        self.assertEqual(S.curseurs(self.a.desk, [autre]), {autre: {}})

    def test_S1_accuse_seulement_apres_enregistrement_durable(self):
        lid, gid = self.partager(self.a)
        op = self.derniere_op(self.a, gid)
        with patch.object(O, 'apres_reception', side_effect=sqlite3.OperationalError('disque plein')):
            acceptees, refus = S.recevoir_lot(self.b.desk, [op], self.a.ident.id)
        self.assertEqual(acceptees, [])
        self.assertTrue(refus)
        self.assertEqual(self.b.desk.db.execute('SELECT COUNT(*) FROM sync56108_operations WHERE objet=?', (gid,)).fetchone()[0], 0)
        self.b.sync()                                                      # reprise : enregistrée cette fois, puis accusée
        self.assertTrue(self.b.local(gid))

    def test_S1_conflit_deux_versions_conservees_resolution_reference_les_deux(self):
        lid, gid = self.partager(self.a)
        self.b.sync()
        lb = self.b.local(gid)
        D.modifier(self.a.desk, ADMIN, lid, 1, {'titre': 'Version A'}, 'cle-version-a-01')
        v = D.lire(self.b.desk, ADMIN, lb)['version']
        D.modifier(self.b.desk, ADMIN, lb, v, {'titre': 'Version B'}, 'cle-version-b-01')
        S.journaliser(self.a.desk)
        S.journaliser(self.b.desk)
        self.b.sync()
        self.a.sync()
        for p in (self.a, self.b):
            self.assertEqual(len(S.tetes(p.desk, gid)), 2, p.nom)
            self.assertTrue(p.objet('dossier', p.local(gid))['conflit'])
        self.assertEqual(D.lire(self.a.desk, ADMIN, lid)['titre'], 'Version A')      # aucune version choisie par l'horloge
        self.assertEqual(D.lire(self.b.desk, ADMIN, lb)['titre'], 'Version B')
        hs = S.versions(self.a.desk, gid)
        choix = next(h['id'] for h in hs if h['charge']['titre'] == 'Version B')
        r = S.resoudre(self.a.desk, gid, choix, 'cabinet')
        op = self.derniere_op(self.a, gid)
        self.assertEqual(op['id'], r['operation'])
        self.assertEqual(sorted(op['parents']), sorted(h['id'] for h in hs))
        self.assertEqual(D.lire(self.a.desk, ADMIN, lid)['titre'], 'Version B')
        self.b.sync()
        self.assertEqual(len(S.tetes(self.b.desk, gid)), 1)
        self.assertFalse(self.b.objet('dossier', lb)['conflit'])

    def test_S1_horloge_decalee_n_entre_pas_dans_la_decision(self):
        lid, gid = self.partager(self.a)
        self.b.sync()
        D.modifier(self.a.desk, ADMIN, lid, 1, {'titre': 'Après'}, 'cle-horloge-01')
        with patch.object(S, '_maintenant', lambda: '2001-01-01T00:00:00+00:00'):   # horloge très en retard sur A
            S.journaliser(self.a.desk)
        self.b.sync()
        self.assertEqual(D.lire(self.b.desk, ADMIN, self.b.local(gid))['titre'], 'Après')

    def test_S1_relais_garde_la_signature_d_origine_et_ne_devient_pas_auteur(self):
        c = Poste(self, 'Poste C', 'Me BERNARD')
        S.ouvrir_rejoindre(c.desk, c.personne, 'cabinet')
        c.demarrer()
        appairer(c, self.a)
        c.sync()
        S.accepter_adhesion(self.a.desk, c.ident.id, 'cabinet')
        c.sync()
        self.b.sync()                                                    # B apprend que C est membre
        lid, gid = self.partager(self.a)
        self.b.sync()
        op = self.derniere_op(self.b, gid)
        self.assertEqual(op['origine'], self.a.ident.id)                # B relaie, l'auteur reste A
        r = S.recevoir(c.desk, op, self.b.ident.id)                      # transport venu de B, signature de A vérifiée
        self.assertEqual(r['etat'], 'recu')
        row = c.desk.db.execute('SELECT origine, relaye_par FROM sync56108_operations WHERE id=?', (op['id'],)).fetchone()
        self.assertEqual((row[0], row[1]), (self.a.ident.id, self.b.ident.id))
        faux = S.signer(self.b.ident, dict(op, id=S.nouvel_id('op'), seq=op['seq'] + 1))
        with self.assertRaisesRegex(Stop, 'signature_refusee'):
            S.recevoir(c.desk, faux, self.b.ident.id)

    def test_S1_poste_retire_ne_recoit_plus_et_ses_operations_directes_sont_refusees(self):
        lid, gid = self.partager(self.a)
        self.b.sync()
        S.retirer_poste(self.a.desk, self.b.ident.id, 'cabinet')
        D.modifier(self.a.desk, ADMIN, lid, 1, {'titre': 'Après retrait'}, 'cle-retrait-001')
        S.journaliser(self.a.desk)
        self.assertEqual(S.a_envoyer(self.a.desk, self.b.ident.id, {}, self.a.ident.id), [])
        self.b.sync()
        self.assertEqual(D.lire(self.b.desk, ADMIN, self.b.local(gid))['titre'], 'Bail ZORVAL')
        lb = self.b.local(gid)
        D.modifier(self.b.desk, ADMIN, lb, D.lire(self.b.desk, ADMIN, lb)['version'], {'titre': 'Depuis B retiré'}, 'cle-retire-b-01')
        S.journaliser(self.b.desk)
        op = self.derniere_op(self.b, gid)
        with self.assertRaisesRegex(Stop, 'poste_retire'):
            S.recevoir(self.a.desk, op, self.b.ident.id)

    def test_S1_aucune_cle_personnelle_session_ou_coffre_dans_le_journal(self):
        lid, gid = self.partager(self.a)
        self.b.sync()
        brut = ' '.join(r[0] for r in self.b.desk.db.execute('SELECT brut FROM sync56108_operations'))
        for interdit in ('password', 'mot_de_passe', 'token', 'secret_file', 'session', 'vault', 'state_dir'):
            self.assertNotIn(interdit, brut)

    def test_S1_ancien_poste_messages_seulement_jamais_a_jour(self):
        vrai = L._traiter

        def ancien(desk, poste_id, obj, donnees, moi):
            if str(obj.get('t') or '').startswith('sync.v1.'):
                raise Stop('trame_inconnue')
            return vrai(desk, poste_id, obj, donnees, moi)
        with patch.object(L, '_traiter', ancien):
            self.b.sync()
        cap = self.b.desk.db.execute('SELECT * FROM sync56108_capacites WHERE poste=?', (self.a.ident.id,)).fetchone()
        self.assertEqual(json.loads(cap['domaines']), ['messages'])
        from agent.sync56108_web import indicateur
        self.assertNotEqual(indicateur(self.b.desk)['etat'], 'a_jour')


class S1Adhesion(unittest.TestCase):
    def test_S1_invitation_refusee_sans_demande_de_rejoindre(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        S.creer_espace(a.desk, 'Cabinet', a.personne, 'cabinet')
        S.declarer_personne(b.desk, b.personne, 'cabinet')
        S.desactiver(b.desk, False)
        L.activer(b.desk, 'cabinet', b.personne, True)                # liaison des messages activée, mais aucun « Rejoindre »
        b.demarrer()
        appairer(b, a)
        a.desk.db.execute("INSERT INTO sync56108_adhesions(poste,espace,personne,personne_nom,etat,recu_le) VALUES(?,?,?,?,'en_attente','x')",
                          (b.ident.id, S.espace_cabinet(a.desk)['id'], S.personne_locale(b.desk)['gid'], b.personne))
        a.desk.db.commit()
        S.accepter_adhesion(a.desk, b.ident.id)
        b.sync()
        self.assertIsNone(S.espace_cabinet(b.desk))                      # un poste n'est jamais embarqué dans un cabinet sans l'avoir demandé

    def test_S1_seule_l_autorite_accepte_une_adhesion(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        with self.assertRaisesRegex(Stop, 'autorite_requise'):
            S.accepter_adhesion(b.desk, a.ident.id)


if __name__ == '__main__':
    unittest.main()

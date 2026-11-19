"""5.6.108 (lot S2) : projections des dossiers, tâches et événements entre postes ; dossiers réservés ; rapprochement initial.

Harnais de ``test_v56108_sync`` (postes simulés, transport réel sur 127.0.0.1). Données fictives.
"""
import json
import unittest

from agent import sync56108 as S, sync56108_objets as O, dossiers5697 as D, agenda5634, membres5650 as mb
from agent.common import Stop
from agent.improvements36 import create_task
from test_v56108_sync import Poste, relier, appairer, ADMIN


def tache(poste, titre, matter=''):
    out = create_task(poste.desk, titre, matter, '2026-12-01')
    poste.desk.db.execute("INSERT OR REPLACE INTO taches5688(task_id,responsable,rappel,etat,origine,origine_ref,createur,updated) "
                          "VALUES(?,?,?,?,?,?,?,?)", (out['id'], 'cabinet', '', 'a_faire', 'dossier' if matter else '', matter, 'cabinet', '2026-11-18'))
    poste.desk.db.commit()
    return out['id']


def evenement(poste, titre, matter='', **extra):
    data = {'title': titre, 'kind': 'rendez_vous', 'start': '2026-12-02T10:00:00+01:00', 'duration_minutes': 60, 'matter': matter}
    return agenda5634.create(poste.desk, data, 'cabinet', **extra)['id']


class S2Dossiers(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, self.a, self.b)

    def test_S2_creation_modification_cloture_projetees(self):
        lid = self.a.dossier('Bail commercial ZORVAL', 'ZOR-101', parties=[{'nom': 'KERVILAC', 'role': 'adverse'}])
        S.partager_dossier(self.a.desk, lid)
        self.b.sync()
        gid = self.a.objet('dossier', lid)['objet']
        lb = self.b.local(gid)
        fiche = D.lire(self.b.desk, ADMIN, lb)
        self.assertEqual((fiche['titre'], fiche['reference'], fiche['client']), ('Bail commercial ZORVAL', 'ZOR-101', 'ZORVAL'))
        self.assertIn('KERVILAC', [p['nom'] for p in fiche['parties']])
        self.assertTrue(fiche['chemin'].startswith('/Dossiers/'))                      # répertoire LOCAL du poste B
        D.modifier(self.a.desk, ADMIN, lid, 1, {'rg': '24/01234'}, 'cle-rg-000001')
        S.journaliser(self.a.desk)                                 # deux commandes journalisées séparément : deux versions
        with D._transaction(self.a.desk):
            D.transition_cloture(self.a.desk, ADMIN, lid, 2, 'archive', 'cle-clot-00001')
        self.b.sync()
        fiche = D.lire(self.b.desk, ADMIN, lb)
        self.assertEqual((fiche['rg'], fiche['statut']), ('24/01234', 'archive'))
        types = [r[0] for r in self.a.desk.db.execute('SELECT type FROM sync56108_operations WHERE objet=? ORDER BY rang', (gid,))]
        self.assertEqual(types, ['dossier.creation', 'dossier.modification', 'dossier.cloture'])
        self.assertTrue(any(m['id'] == lb for m in D._registre_json(self.b.desk)))      # projection JSON pour les pages existantes

    def test_S2_aucun_echo_vers_l_origine(self):
        lid = self.a.dossier('Dossier écho', 'ZOR-102')
        S.partager_dossier(self.a.desk, lid)
        self.b.sync()
        self.assertEqual(S.journaliser(self.b.desk), 0)
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM sync56108_operations WHERE origine=? AND type LIKE 'dossier.%'",
                                                (self.b.ident.id,)).fetchone()[0], 0)

    def test_S2_doublon_de_reference_a_rapprocher_jamais_fusionne_puis_separer(self):
        self.b.dossier('Dossier local de B', 'ZOR-103')
        lid = self.a.dossier('Dossier de A', 'ZOR-103')
        tid = tache(self.a, 'Relire le bail', lid)
        S.partager_dossier(self.a.desk, lid)
        self.b.sync()
        gid = self.a.objet('dossier', lid)['objet']
        o = self.b.desk.db.execute('SELECT * FROM sync56108_objets WHERE objet=?', (gid,)).fetchone()
        self.assertEqual((o['projection'], o['motif']), ('a_rapprocher', 'reference_deja_utilisee'))
        apercu = O.doublons(self.b.desk, gid)
        self.assertEqual([l['titre'] for l in apercu['locaux']], ['Dossier local de B'])
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM dossiers5697 WHERE ref='ZOR-103'").fetchone()[0], 1)   # rien de fusionné
        tg = self.a.objet('tache', tid)['objet']
        self.assertEqual(self.b.desk.db.execute('SELECT projection FROM sync56108_objets WHERE objet=?', (tg,)).fetchone()[0], 'en_attente')
        r = O.rapprocher(self.b.desk, gid, 'separer')
        self.assertEqual(r['reference_locale'], 'ZOR-103-2')
        lb = self.b.local(gid)
        self.assertEqual(D.lire(self.b.desk, ADMIN, lb)['titre'], 'Dossier de A')
        O.reprendre(self.b.desk)
        self.assertEqual(self.b.desk.db.execute('SELECT matter FROM tasks WHERE id=?', (self.b.local(tg),)).fetchone()[0], lb)
        # la référence locale distincte ne repart pas vers l'origine
        D.modifier(self.b.desk, ADMIN, lb, D.lire(self.b.desk, ADMIN, lb)['version'], {'resume': 'Note de B'}, 'cle-note-b-0001')
        S.journaliser(self.b.desk)
        self.a.sync()
        self.assertEqual(D.lire(self.a.desk, ADMIN, lid)['reference'], 'ZOR-103')
        self.assertEqual(D.lire(self.a.desk, ADMIN, lid)['resume'], 'Note de B')

    def test_S2_rapprochement_associer_donne_deux_versions_a_comparer(self):
        lb0 = self.b.dossier('Même affaire', 'ZOR-104')
        lid = self.a.dossier('Même affaire (A)', 'ZOR-104')
        S.partager_dossier(self.a.desk, lid)
        self.b.sync()
        gid = self.a.objet('dossier', lid)['objet']
        O.rapprocher(self.b.desk, gid, 'associer', lb0)
        self.assertEqual(len(S.tetes(self.b.desk, gid)), 2)
        self.assertEqual(D.lire(self.b.desk, ADMIN, lb0)['titre'], 'Même affaire')            # rien n'est écrasé avant le choix

    def test_S2_titre_semblable_reference_differente_deux_dossiers(self):
        self.b.dossier('Bail ZORVAL', 'ZOR-105')
        lid = self.a.dossier('Bail ZORVAL', 'ZOR-106')
        S.partager_dossier(self.a.desk, lid)
        self.b.sync()
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM dossiers5697 WHERE titre='Bail ZORVAL'").fetchone()[0], 2)

    def test_S2_modification_locale_en_attente_jamais_ecrasee(self):
        lid = self.a.dossier('Dossier partagé', 'ZOR-107')
        S.partager_dossier(self.a.desk, lid)
        self.b.sync()
        gid = self.a.objet('dossier', lid)['objet']
        lb = self.b.local(gid)
        D.modifier(self.a.desk, ADMIN, lid, 1, {'titre': 'Titre de A'}, 'cle-attente-a01')
        S.journaliser(self.a.desk)
        D.modifier(self.b.desk, ADMIN, lb, D.lire(self.b.desk, ADMIN, lb)['version'], {'titre': 'Titre de B'}, 'cle-attente-b01')
        # capture de B non encore journalisée : la réception la journalise d'abord, puis constate le conflit
        op = json.loads(self.a.desk.db.execute('SELECT brut FROM sync56108_operations WHERE objet=? ORDER BY rang DESC LIMIT 1', (gid,)).fetchone()[0])
        S.recevoir(self.b.desk, op, self.a.ident.id)
        self.assertEqual(D.lire(self.b.desk, ADMIN, lb)['titre'], 'Titre de B')
        self.assertEqual(len(S.tetes(self.b.desk, gid)), 2)


class S2Reserves(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN'), Poste(self, 'Poste C', 'Me BERNARD')
        relier(self, self.a, self.b, self.c)
        self.b.sync()

    def test_S2_dossier_reserve_seulement_aux_personnes_autorisees(self):
        lid = self.a.dossier('Affaire réservée KERVILAC', 'ZOR-201', confidentialite='reserve', membres=['cabinet'])
        pb = S.personne_locale(self.b.desk)['gid']
        out = S.partager_dossier(self.a.desk, lid, [pb])
        self.assertNotEqual(out['espace'], S.espace_cabinet(self.a.desk)['id'])
        lid2 = self.a.dossier('Affaire ouverte', 'ZOR-202')
        S.partager_dossier(self.a.desk, lid2)
        for p in (self.b, self.c):
            p.sync()
        gid, gid2 = self.a.objet('dossier', lid)['objet'], self.a.objet('dossier', lid2)['objet']
        lb = self.b.local(gid)
        self.assertTrue(lb)
        self.assertEqual(mb.load(self.b.c['state_dir'])['reserves'].get(lb), ['cabinet'])    # réservé à la personne de B sur son poste
        self.assertEqual(self.c.local(gid), '')
        self.assertFalse(self.c.desk.db.execute('SELECT 1 FROM sync56108_operations WHERE objet=?', (gid,)).fetchone())
        self.assertFalse(self.c.desk.db.execute("SELECT 1 FROM dossiers5697 WHERE titre LIKE '%KERVILAC%'").fetchone())
        self.assertTrue(self.c.local(gid2))                          # la séquence privée absente ne bloque pas l'espace du cabinet

    def test_S2_reserve_exige_des_personnes_autorisees(self):
        lid = self.a.dossier('Affaire réservée', 'ZOR-203', confidentialite='reserve', membres=['cabinet'])
        with self.assertRaisesRegex(Stop, 'personnes_autorisees_requises'):
            S.partager_dossier(self.a.desk, lid, [])

    def test_S2_dossier_reserve_refuse_dans_l_espace_du_cabinet(self):
        lid = self.a.dossier('Dossier ouvert', 'ZOR-204')
        S.partager_dossier(self.a.desk, lid)
        gid = self.a.objet('dossier', lid)['objet']
        op = json.loads(self.a.desk.db.execute('SELECT brut FROM sync56108_operations WHERE objet=?', (gid,)).fetchone()[0])
        self.b.sync()
        faux = S.signer(self.a.ident, dict(op, id=S.nouvel_id('op'), seq=op['seq'] + 50, charge=dict(op['charge'], confidentialite='reserve')))
        with self.assertRaisesRegex(Stop, 'dossier_reserve_hors_sous_espace'):
            S.recevoir(self.b.desk, faux, self.a.ident.id)

    def test_S2_dossier_devenu_reserve_n_emet_plus_rien(self):
        lid = self.a.dossier('Dossier ouvert', 'ZOR-205')
        S.partager_dossier(self.a.desk, lid)
        self.a.desk.db.execute("UPDATE dossiers5697 SET confidentialite='reserve', titre='Secret' WHERE id=?", (lid,))
        self.a.desk.db.commit()
        self.assertEqual(S.journaliser(self.a.desk), 0)
        self.assertEqual(self.a.objet('dossier', lid)['motif'], 'devenu_reserve')
        self.b.sync()
        self.assertFalse(self.b.desk.db.execute("SELECT 1 FROM dossiers5697 WHERE titre='Secret'").fetchone())


class S2TachesAgenda(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, self.a, self.b)
        self.lid = self.a.dossier('Dossier agenda', 'ZOR-301')
        S.partager_dossier(self.a.desk, self.lid)

    def test_S2_tache_creation_etat_responsable_suppression(self):
        tid = tache(self.a, 'Préparer les conclusions', self.lid)
        sans = tache(self.a, 'Tâche sans dossier')
        self.b.sync()
        tg = self.a.objet('tache', tid)['objet']
        tb = self.b.local(tg)
        row = self.b.desk.db.execute('SELECT * FROM tasks WHERE id=?', (tb,)).fetchone()
        self.assertEqual((row['title'], row['due'], row['matter']), ('Préparer les conclusions', '2026-12-01', self.b.local(self.a.objet('dossier', self.lid)['objet'])))
        self.assertEqual(self.b.desk.db.execute('SELECT responsable, etat FROM taches5688 WHERE task_id=?', (tb,)).fetchone()[:], ('cabinet', 'a_faire'))
        self.assertIsNone(self.a.objet('tache', sans))                              # sans dossier : jamais partagée sans choix explicite
        self.a.desk.db.execute("UPDATE taches5688 SET etat='en_cours' WHERE task_id=?", (tid,))
        self.a.desk.db.commit()
        self.b.sync()
        self.assertEqual(self.b.desk.db.execute('SELECT etat FROM taches5688 WHERE task_id=?', (tb,)).fetchone()[0], 'en_cours')
        self.a.desk.db.execute('DELETE FROM tasks WHERE id=?', (tid,))
        self.a.desk.db.commit()
        self.b.sync()
        self.assertFalse(self.b.desk.db.execute('SELECT 1 FROM tasks WHERE id=?', (tb,)).fetchone())
        self.assertEqual(S.versions(self.b.desk, tg)[0]['type'], 'tache.suppression')   # suppression versionnée

    def test_S2_suppression_concurrente_pas_de_resurrection(self):
        tid = tache(self.a, 'Tâche disputée', self.lid)
        self.b.sync()
        tg = self.a.objet('tache', tid)['objet']
        tb = self.b.local(tg)
        self.a.desk.db.execute('DELETE FROM tasks WHERE id=?', (tid,))
        self.a.desk.db.commit()
        self.b.desk.db.execute("UPDATE tasks SET title='Tâche modifiée hors ligne' WHERE id=?", (tb,))
        self.b.desk.db.commit()
        S.journaliser(self.a.desk)
        S.journaliser(self.b.desk)
        self.b.sync()
        self.a.sync()
        self.assertEqual(len(S.tetes(self.a.desk, tg)), 2)
        self.assertFalse(self.a.desk.db.execute('SELECT 1 FROM tasks WHERE id=?', (tid,)).fetchone())   # pas recréée silencieusement
        self.assertTrue(any(v['charge'].get('supprime') for v in S.versions(self.b.desk, tg)))

    def test_S2_evenement_uid_fuseau_recurrence_exception_source(self):
        uid = 'serie-zorval@example.test'
        e1 = evenement(self.a, 'Audience ZORVAL', self.lid, uid=uid, recurrence_id='20261202T090000Z')
        e2 = agenda5634.create(self.a.desk, {'title': 'Audience ZORVAL', 'kind': 'rendez_vous', 'start': '2026-12-09T10:00:00+01:00',
                                             'duration_minutes': 60, 'matter': self.lid}, 'cabinet', uid=uid, recurrence_id='20261209T090000Z')['id']
        self.a.desk.db.execute("INSERT OR REPLACE INTO agenda5697(event_id,nature,visibilite,zone,updated) VALUES(?,'rendez_vous','normale','Europe/Paris','x')", (e1,))
        self.a.desk.db.commit()
        agenda5634.cancel(self.a.desk, e2)                                              # exception : une occurrence annulée
        prive = evenement(self.a, 'Rendez-vous personnel', self.lid)
        self.a.desk.db.execute("INSERT OR REPLACE INTO agenda5697(event_id,nature,visibilite,updated) VALUES(?,'rendez_vous','privee','x')", (prive,))
        self.a.desk.db.commit()
        importe = evenement(self.a, 'Audience importée', self.lid, source='import', uid='connecteur-1@example.test')
        self.b.sync()
        rows = {r['recurrence_id']: r for r in self.b.desk.db.execute('SELECT * FROM agenda5634 WHERE uid=?', (uid,))}
        self.assertEqual(set(rows), {'20261202T090000Z', '20261209T090000Z'})
        self.assertEqual((rows['20261202T090000Z']['status'], rows['20261209T090000Z']['status']), ('actif', 'annule'))
        b1 = rows['20261202T090000Z']['id']
        self.assertEqual(self.b.desk.db.execute('SELECT zone FROM agenda5697 WHERE event_id=?', (b1,)).fetchone()[0], 'Europe/Paris')
        self.assertEqual(rows['20261202T090000Z']['starts'], self.a.desk.db.execute('SELECT starts FROM agenda5634 WHERE id=?', (e1,)).fetchone()[0])
        self.assertIsNone(self.a.objet('evenement', prive))                            # privé : jamais partagé
        self.assertEqual(self.b.desk.db.execute("SELECT source FROM agenda5634 WHERE uid='connecteur-1@example.test'").fetchone()[0], 'import')
        self.assertEqual(self.b.desk.db.execute("SELECT copy_policy FROM agenda5634 WHERE uid=?", (uid,)).fetchone()[0], 'none')
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM calendar_cache WHERE uid=?", (uid,)).fetchone()[0], 1)
        del importe

    def test_S2_evenement_de_connecteur_jamais_recree(self):
        evenement(self.b, 'Audience importée', '', source='import', uid='connecteur-2@example.test')
        evenement(self.a, 'Audience importée', self.lid, source='import', uid='connecteur-2@example.test')
        self.b.sync()
        self.assertEqual(self.b.desk.db.execute("SELECT COUNT(*) FROM agenda5634 WHERE uid='connecteur-2@example.test'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()

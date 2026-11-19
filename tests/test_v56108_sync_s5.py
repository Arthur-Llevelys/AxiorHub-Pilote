"""5.6.108 (lot S5) : points de montage branchés — une seule exécution (moteur des travaux, autorité des missions, chaîne d'appel au
modèle), indicateur dans l'en-tête général, fichiers des dossiers partagés (relevé, dépôt au dossier, page « Fichiers »), sans espace :
comportement inchangé.

Harnais de ``test_v56108_sync`` ; stockage des dossiers : dossier local temporaire (``nextcloud.local_path``). Données fictives ; aucun
modèle (``perform`` des routines et ``complete`` des modèles remplacés par des compteurs).
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import sync56108 as S, sync56108_missions as M, sync56108_fichiers as F, sync56108_web as W, dossiers5697 as D, shell501
from agent.common import Stop
from test_v56108_sync import Poste, relier, ADMIN


def stockage_local(test, poste):
    d = tempfile.TemporaryDirectory()
    test.addCleanup(d.cleanup)
    (Path(d.name) / 'Dossiers').mkdir()
    poste.desk.c['nextcloud'] = {**poste.desk.c['nextcloud'], 'local_path': d.name}
    poste.c['nextcloud'] = poste.desk.c['nextcloud']
    return Path(d.name)


def executer_routine(poste, compteur):
    """Met un travail « routine520 » (tri matinal) en file et le fait tourner ; ``compteur`` reçoit chaque exécution réelle."""
    jid = poste.desk.enqueue('routine520', {'kind': 'tri', 'planned': '2026-11-18'})
    with patch('agent.routines520.perform', side_effect=lambda desk, kind, args: compteur.append(poste.nom) or {'message': 'fait'}):
        poste.desk.work_once()
    return poste.desk.db.execute('SELECT status, result FROM jobs WHERE id=?', (jid,)).fetchone()


class S5Execution(unittest.TestCase):
    def test_S5_sans_espace_comportement_inchange(self):
        a = Poste(self, 'Poste A', 'Me DURAND')
        vus = []
        row = executer_routine(a, vus)
        self.assertEqual(vus, ['Poste A'])
        self.assertEqual(row['status'], 'done')
        self.assertIsNone(M.garde_travail(a.desk, 'routine520', {'kind': 'tri'}))

    def test_S5_une_seule_execution_par_attribution_et_mutation(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        b.sync()
        M.attribuer(a.desk, 'Tri matinal des courriels', b.ident.id, routine='routine520:tri')
        b.sync()
        vus = []
        ra, rb = executer_routine(a, vus), executer_routine(b, vus)
        self.assertEqual(vus, ['Poste B'])                                       # une seule exécution
        self.assertEqual(ra['status'], 'ignored')
        self.assertIn('Poste B', json.loads(ra['result'])['message'])
        self.assertIn('aucun appel à l’IA', json.loads(ra['result'])['message'])
        self.assertEqual(rb['status'], 'done')
        b.desk.db.execute("UPDATE liaison56103_postes SET contact_le='2026-01-01T00:00:00+00:00'")   # sur B, A paraît absent ; sur A, B absent
        b.desk.db.commit()
        a.desk.db.execute("UPDATE liaison56103_postes SET contact_le='2026-01-01T00:00:00+00:00'")
        a.desk.db.commit()
        ra2 = executer_routine(a, vus)
        self.assertIn('Attend le poste « Poste B »', json.loads(ra2['result'])['message'])
        # mutation : sans l'appel de la garde, la routine tourne aussi sur A (double exécution détectée)
        vus.clear()
        with patch.object(M, 'garde_travail', lambda desk, kind, args: None):
            executer_routine(a, vus)
            executer_routine(b, vus)
        self.assertEqual(sorted(vus), ['Poste A', 'Poste B'])

    def test_S5_mission_attribuee_ailleurs_aucun_appel_de_modele(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, a, b)
        M.attribuer(a.desk, 'Analyse du dossier', b.ident.id, routine='mission567:m-fictive-1')
        from agent import chaine5689

        class Modele:
            def complete(self, *a, **k):
                raise AssertionError('aucun appel de modèle sur un poste non exécuteur')
        with self.assertRaisesRegex(Stop, 'execution_sur_un_autre_poste'):
            chaine5689.appeler(a.desk, 'analyse', 'système', {}, '', mission_id='m-fictive-1', modele=Modele())
        from agent.missions567 import check_authority
        with self.assertRaisesRegex(Stop, 'execution_sur_un_autre_poste'):
            check_authority(a.desk, {'mission_id': 'm-fictive-1'})

    def test_S5_cle_de_boite_identique_sur_les_postes(self):
        a = Poste(self, 'Poste A', 'Me DURAND')
        self.assertEqual(M.cle_execution(a.desk, 'live_mail430', {}), 'boite:cabinet@example.test')
        self.assertEqual(M.cle_execution(a.desk, 'routine520', {'kind': 'tri'}), 'routine520:tri')
        self.assertEqual(M.cle_execution(a.desk, 'prepare_reply', {}), '')                # travail local : jamais partagé


class S5Entete(unittest.TestCase):
    def test_S5_indicateur_dans_l_entete_seulement_avec_un_espace(self):
        a, b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        self.assertEqual(W.indicateur_entete(a.c, '/p'), '')                       # poste seul : rien
        shell501.set_context(a.c)
        self.assertNotIn('s108-pastille', shell501.topbar_html('/p', 'Aujourd’hui'))
        S.creer_espace(a.desk, 'Cabinet', a.personne, 'cabinet')
        self.assertEqual(W.indicateur_entete(a.c, '/p'), '')                       # espace sans autre poste : rien
        S.ouvrir_rejoindre(b.desk, b.personne, 'cabinet')
        b.demarrer()
        from test_v56108_sync import appairer
        appairer(b, a)
        b.sync()
        S.accepter_adhesion(a.desk, b.ident.id)
        b.sync()
        avant = a.desk.db.total_changes
        html = W.indicateur_entete(a.c, '/p')
        self.assertIn('s108-pastille', html)
        self.assertIn('data-s108-indicateur', html)
        self.assertIn('v56108-sync.js', html)
        self.assertEqual(a.desk.db.total_changes, avant)                           # lecture seule
        shell501.set_context(a.c)
        self.assertIn('s108-pastille', shell501.topbar_html('/p', 'Aujourd’hui'))
        S.desactiver(a.desk, True)
        self.assertEqual(W.indicateur_entete(a.c, '/p'), '')


class S5Fichiers(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        self.ra, self.rb = stockage_local(self, self.a), stockage_local(self, self.b)
        relier(self, self.a, self.b)
        self.lid = self.a.dossier('Bail ZORVAL', 'ZOR-801')
        D.traiter_rangements(self.a.desk)
        S.partager_dossier(self.a.desk, self.lid)
        self.b.sync()
        self.gid = self.a.objet('dossier', self.lid)['objet']
        D.traiter_rangements(self.b.desk)
        self.da = self.ra / D.lire(self.a.desk, ADMIN, self.lid)['chemin'].lstrip('/')
        self.db_ = self.rb / D.lire(self.b.desk, ADMIN, self.b.local(self.gid))['chemin'].lstrip('/')
        self.assertTrue(self.da.is_dir() and self.db_.is_dir())

    def test_S5_fichier_ajoute_hors_axiorhub_publie_puis_depose_chez_l_autre(self):
        (self.da / 'Conclusions.docx').write_bytes(b'conclusions v1 ZORVAL')
        bilan = F.relever(self.a.desk)
        self.assertEqual(bilan['publies'], 1)
        self.assertEqual(F.relever(self.a.desk)['publies'], 0)                     # même fichier : rien de plus (relevé périodique pas dû)
        self.b.sync()
        self.assertEqual((self.db_ / 'Conclusions.docx').read_bytes(), b'conclusions v1 ZORVAL')
        self.b.desk.db.execute("UPDATE sync56108_fichiers_releve SET dernier=0")
        self.b.desk.db.commit()
        self.assertEqual(F.relever(self.b.desk)['publies'], 0)                     # aucun écho vers l'origine
        src = F.fabrique_source(self.b.desk, None, ADMIN)
        self.assertIn('Conclusions.docx', [e['nom'] for e in src.lister('/Postes du cabinet/ZOR-801')])
        self.assertEqual(src.lire('/Postes du cabinet/ZOR-801/Conclusions.docx', 10 ** 6), b'conclusions v1 ZORVAL')

    def test_S5_telechargement_en_attente_visible_dans_fichiers(self):
        (self.da / 'Pièce 1.pdf').write_bytes(b'%PDF fictif ' * 1000)
        F.relever(self.a.desk)
        with patch.object(F.recuperer_blocs, '__defaults__', (0,)):
            self.b.sync()
        src = F.fabrique_source(self.b.desk, None, ADMIN)
        noms = [e['nom'] for e in src.lister('/Postes du cabinet/ZOR-801')]
        self.assertEqual(noms, ['Pièce 1 (téléchargement en attente).pdf'])
        with self.assertRaisesRegex(Stop, 'telechargement_en_attente'):
            src.lire('/Postes du cabinet/ZOR-801/' + noms[0], 10 ** 6)
        self.assertFalse((self.db_ / 'Pièce 1.pdf').exists())                     # jamais un fichier partiel dans le dossier

    def test_S5_ecriture_par_axiorhub_signale_un_releve_prompt(self):
        F.relever(self.a.desk)                                                     # relevé fait : le suivant n'est pas dû
        self.assertFalse(F.releve_du(self.a.desk))
        from agent import fichiers56103
        fichiers56103.ensure_schema(self.a.desk)
        S.installer_declencheurs(self.a.desk)
        self.a.desk.db.execute("INSERT INTO fichiers56103_ops VALUES('cabinet','cle-essai-01','ecrire','x','en_cours','{}','t','t')")
        self.a.desk.db.execute("UPDATE fichiers56103_ops SET etat='fait' WHERE cle='cle-essai-01'")
        self.a.desk.db.commit()
        self.assertTrue(F.releve_du(self.a.desk))

    def test_S5_modifications_concurrentes_deux_versions_dans_le_dossier(self):
        (self.da / 'Assignation.docx').write_bytes(b'version commune')
        F.relever(self.a.desk)
        self.b.sync()
        (self.da / 'Assignation.docx').write_bytes(b'modifiee par A hors ligne')
        (self.db_ / 'Assignation.docx').write_bytes(b'modifiee par B hors ligne')
        for p in (self.a, self.b):
            p.desk.db.execute('UPDATE sync56108_fichiers_releve SET dernier=0')
            p.desk.db.commit()
            self.assertEqual(F.relever(p.desk)['publies'], 1, p.nom)
        self.b.sync()
        self.a.sync()
        objet = self.a.desk.db.execute("SELECT objet FROM sync56108_documents LIMIT 1").fetchone()[0]
        self.assertEqual(len(S.tetes(self.a.desk, objet)), 2)
        for p, d, mien in ((self.a, self.da, b'modifiee par A hors ligne'), (self.b, self.db_, b'modifiee par B hors ligne')):
            contenus = {f.read_bytes() for f in d.iterdir() if f.is_file()}
            self.assertIn(mien, contenus, p.nom)                                   # la modification locale n'est jamais écrasée
            self.assertTrue({b'modifiee par A hors ligne', b'modifiee par B hors ligne'} <= contenus, p.nom)

    def test_S5_releve_borne_et_pause_du_cabinet_respectee(self):
        for i in range(3):
            (self.da / ('Note %d.txt' % i)).write_bytes(b'note %d' % i)
        bilan = F.relever(self.a.desk, budget_fichiers=1)
        self.assertEqual(bilan['examines'], 1)
        self.assertTrue(F.releve_du(self.a.desk))                                  # reprise au passage suivant
        M.pause_cabinet(self.a.desk, True, 'audit')
        self.assertEqual(F.relever(self.a.desk)['examines'], 0)
        M.pause_cabinet(self.a.desk, False)
        self.assertEqual(F.relever(self.a.desk)['publies'], 2)


if __name__ == '__main__':
    unittest.main()

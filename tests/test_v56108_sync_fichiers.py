"""5.6.108 (lot S3) : fichiers et versions entre postes — manifeste, blocs vérifiés, zone temporaire, reprise, noms sûrs, versions concurrentes.

Harnais de ``test_v56108_sync`` ; contenus fictifs générés (octets pseudo-aléatoires déterministes).
"""
import hashlib
import json
import random
import unittest
from unittest.mock import patch

from agent import sync56108 as S, sync56108_fichiers as F
from agent.common import Stop
from test_v56108_sync import Poste, relier


def contenu(n, graine=1):
    return random.Random(graine).randbytes(n)


class S3Fichiers(unittest.TestCase):
    def setUp(self):
        self.a, self.b = Poste(self, 'Poste A', 'Me DURAND'), Poste(self, 'Poste B', 'Me MARTIN')
        relier(self, self.a, self.b)
        self.lid = self.a.dossier('Dossier pièces', 'ZOR-401')
        S.partager_dossier(self.a.desk, self.lid)
        self.dg = self.a.objet('dossier', self.lid)['objet']

    def fichier(self, poste, nom, data):
        p = poste.desk.c['state_dir'] + '/' + nom
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def doc(self, poste, objet):
        return [dict(r) for r in poste.desk.db.execute('SELECT * FROM sync56108_documents WHERE objet=? ORDER BY maj_le, version', (objet,))]

    def test_S3_manifeste_puis_blocs_verifies_publication_apres_empreinte(self):
        data = contenu(3 * F.BLOC + 1234)
        out = F.publier(self.a.desk, self.fichier(self.a, 'source.docx', data), 'Conclusions ZORVAL.docx', self.dg)
        with patch.object(F, 'MAX_OCTETS_SESSION', 0), patch.object(F.recuperer_blocs, '__defaults__', (0,)):
            self.b.sync()
        d = self.doc(self.b, out['objet'])
        self.assertEqual(d[0]['etat'], 'telechargement')                                # métadonnées reçues, pas de lien cassé
        self.assertEqual(F.versions_documents(self.b.desk)[0]['etat_libelle'], 'Téléchargement en attente')
        self.b.sync()
        d = self.doc(self.b, out['objet'])[0]
        self.assertEqual(d['etat'], 'disponible')
        with open(d['chemin'], 'rb') as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), out['sha256'])
        self.assertTrue(d['chemin'].endswith('Conclusions ZORVAL.docx'))
        self.assertFalse((F._racine(self.b.desk) / 'partiel' / out['sha256']).exists())   # zone temporaire purgée

    def test_S3_coupure_seuls_les_blocs_manquants_redemandes(self):
        data = contenu(4 * F.BLOC, 2)
        out = F.publier(self.a.desk, self.fichier(self.a, 'gros.pdf', data), 'Pièce 12.pdf', self.dg)
        demandes = []
        vrai = F.servir_bloc

        def compter(desk, poste, moi, obj):
            demandes.append(obj['index'])
            return vrai(desk, poste, moi, obj)
        with patch.object(F.recuperer_blocs, '__defaults__', (2 * F.BLOC,)), patch.object(F, 'servir_bloc', compter):
            self.b.sync()                                            # coupure après deux blocs (budget de la session)
        self.assertEqual(sorted(demandes), [0, 1])
        blocs = json.loads(self.doc(self.b, out['objet'])[0]['blocs'])
        self.assertEqual(F.blocs_manquants(self.b.desk, out['sha256'], blocs), [2, 3])
        demandes.clear()
        with patch.object(F, 'servir_bloc', compter):
            self.b.sync()
        self.assertEqual(sorted(demandes), [2, 3])                   # reprise : jamais les blocs déjà reçus
        self.assertEqual(self.doc(self.b, out['objet'])[0]['etat'], 'disponible')

    def test_S3_bloc_altere_refuse_rien_ecrit(self):
        data = contenu(F.BLOC + 10, 3)
        m = F.manifeste(self.fichier(self.a, 'x.bin', data))
        with self.assertRaisesRegex(Stop, 'bloc_altere'):
            F.ecrire_bloc(self.b.desk, m['sha256'], m['blocs'], 0, b'faux' + data[4:F.BLOC])
        self.assertEqual(F.blocs_manquants(self.b.desk, m['sha256'], m['blocs']), [0, 1])

    def test_S3_empreinte_finale_fausse_aucune_publication(self):
        data = contenu(F.BLOC + 10, 4)
        m = F.manifeste(self.fichier(self.a, 'y.bin', data))
        faux_sha = 'f' * 64
        for i in range(2):
            F.ecrire_bloc(self.b.desk, faux_sha, m['blocs'], i, data[i * F.BLOC:(i + 1) * F.BLOC])
        with self.assertRaisesRegex(Stop, 'empreinte_finale_invalide'):
            F.assembler(self.b.desk, faux_sha, m['taille'], m['blocs'])
        self.assertFalse(F._blob(self.b.desk, faux_sha).exists())

    def test_S3_deux_modifications_concurrentes_deux_versions_a_comparer(self):
        out = F.publier(self.a.desk, self.fichier(self.a, 'v1.docx', contenu(5000, 5)), 'Assignation.docx', self.dg)
        self.b.sync()
        F.publier(self.a.desk, self.fichier(self.a, 'va.docx', contenu(6000, 6)), 'Assignation.docx', self.dg, out['objet'])
        F.publier(self.b.desk, self.fichier(self.b, 'vb.docx', contenu(7000, 7)), 'Assignation.docx', self.dg, out['objet'])
        self.b.sync()
        self.a.sync()
        for p in (self.a, self.b):
            self.assertEqual(len(S.tetes(p.desk, out['objet'])), 2, p.nom)
            versions = self.doc(p, out['objet'])
            self.assertEqual(len(versions), 3)
            dispo = [v for v in versions if v['etat'] == 'disponible']
            self.assertEqual(len(dispo), 3, p.nom)
            self.assertEqual(len({v['chemin'] for v in dispo}), 3)                     # aucun octet fusionné ni écrasé
            self.assertEqual(len({v['sha256'] for v in dispo}), 3)

    def test_S3_noms_reserves_windows_casse_longueur(self):
        self.assertEqual(F.nom_sur('CON.docx'), '_CON.docx')
        self.assertEqual(F.nom_sur('lpt1'), '_lpt1')
        self.assertEqual(F.nom_sur('a<b>:c?.pdf. '), 'a_b__c_.pdf')
        self.assertLessEqual(len(F.nom_sur('x' * 400 + '.docx')), F.MAX_NOM)
        d = F._racine(self.a.desk) / 'essai-casse'
        d.mkdir()
        (d / 'Note.docx').write_bytes(b'1')
        self.assertEqual(F._place(d, 'NOTE.docx', 'doc-x').name, 'NOTE (2).docx')        # jamais d'écrasement par la casse

    def test_S3_bases_et_coffres_jamais_synchronises(self):
        for nom in ('desk.sqlite3', 'desk.sqlite3-wal', 'identite.secret', 'modele.gguf'):
            with self.assertRaisesRegex(Stop, 'fichier_exclu'):
                F.publier(self.a.desk, self.fichier(self.a, 'z.bin', b'1'), nom, self.dg)

    def test_S3_bloc_servi_seulement_a_un_membre_de_l_espace(self):
        out = F.publier(self.a.desk, self.fichier(self.a, 'w.bin', contenu(100, 8)), 'Pièce.pdf', self.dg)
        with self.assertRaisesRegex(Stop, 'bloc_absent'):
            F.servir_bloc(self.a.desk, 'e' * 32, self.a.ident.id, {'sha': out['sha256'], 'index': 0})
        entete, data = F.servir_bloc(self.a.desk, self.b.ident.id, self.a.ident.id, {'sha': out['sha256'], 'index': 0})
        self.assertEqual(entete['sha_bloc'], hashlib.sha256(data).hexdigest())

    def test_S3_debit_et_transferts_simultanes_bornes(self):
        self.assertLessEqual(F.MAX_TRANSFERTS, 2)
        self.assertTrue(F._TRANSFERTS.acquire(blocking=False))
        self.assertTrue(F._TRANSFERTS.acquire(blocking=False))
        try:
            F.publier(self.a.desk, self.fichier(self.a, 'q.bin', contenu(100, 9)), 'Pièce 2.pdf', self.dg)
            self.b.sync()                                            # transferts pleins : rien n'est téléchargé, rien ne bloque
            self.assertTrue(all(v['etat'] != 'disponible' for v in F.versions_documents(self.b.desk)))
        finally:
            F._TRANSFERTS.release()
            F._TRANSFERTS.release()


if __name__ == '__main__':
    unittest.main()

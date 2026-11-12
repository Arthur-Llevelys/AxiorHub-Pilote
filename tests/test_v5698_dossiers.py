"""5.6.98 : « Nouveau dossier » refusait toute création quand un ancien dossier était posé sur la racine des dossiers (ou sur un parent) :
le contrôle « répertoire déjà utilisé » voyait chaque nouveau répertoire « à l'intérieur » de ce dossier.

Faux stockage, aucun réseau, aucun modèle ; données fictives (DURAND, MARTIN, ZORVAL, KERVILAC, DOS-001)."""
import unittest

from agent import dossiers5697 as D
import test_v5697_dossiers as T

ADMIN = T.ADMIN


def setUpModule():
    T.setUpModule()


def tearDownModule():
    T.tearDownModule()


class CreationSousLaRacine(T.Base):
    def ajouter_json(self, **m):
        from agent.desk import save_matter
        save_matter(self.f.c, {'aliases': [], 'references': [], 'correspondents': [], **m})

    def test_D98_ancien_dossier_sur_la_racine_ne_bloque_plus(self):
        racine = D.racine(self.desk)
        self.ajouter_json(id='CAB-001', path=racine, client_name='Cabinet')
        recu = self.creer(titre='DURAND c/ MARTIN')
        self.assertTrue(recu['stockage']['chemin'].startswith(racine + '/'))

    def test_D98_dossier_migre_sur_la_racine_ne_bloque_plus(self):
        racine = D.racine(self.desk)
        self.ajouter_json(id='CAB-003', path=racine, client_name='Cabinet')
        D.migrer(self.desk, ADMIN, simulation=False)
        self.assertTrue(self.creer(titre='KERVILAC')['id'])

    def test_D98_meme_repertoire_toujours_refuse(self):
        premier = self.creer(titre='DURAND', reference='2026-0101')
        with self.assertRaises(D.Refus) as ctx:
            self.creer(titre='AUTRE', emplacement={'mode': 'rattacher', 'chemin': premier['stockage']['chemin']})
        self.assertEqual(str(ctx.exception), 'emplacement_deja_utilise')

    def test_D98_repertoire_dans_un_dossier_toujours_refuse(self):
        premier = self.creer(titre='DURAND', reference='2026-0102')
        with self.assertRaises(D.Refus):
            self.creer(titre='AUTRE', emplacement={'mode': 'rattacher', 'chemin': premier['stockage']['chemin'] + '/SOUS'})

    def test_D98_api_creation_201(self):
        racine = D.racine(self.desk)
        self.ajouter_json(id='CAB-004', path=racine, client_name='Cabinet')
        code, corps = self.appel('dossiers', {'titre': 'MARTIN - Conseil', 'nature': 'conseil', 'cle': 'cle-d98-0001',
                                              'emplacement': {'mode': 'creer', 'chemin': ''}})
        self.assertEqual(code, '201', corps)


    def test_D98_entete_de_fiche_reference_et_non_identifiant(self):
        from agent import dossier5679
        from agent.common import load_matters
        recu = self.creer(titre='MARTIN', reference='2026-0103')
        m = next(x for x in load_matters(self.f.c, unfiltered=True) if x['id'] == recu['id'])
        html = dossier5679.header_html(self.desk, '/p', m, 'synthese')
        self.assertIn('Réf. 2026-0103', html)
        self.assertNotIn('Réf. ' + recu['id'], html)


if __name__ == '__main__':
    unittest.main()

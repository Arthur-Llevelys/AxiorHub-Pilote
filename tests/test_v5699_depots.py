"""5.6.99 (choix du cabinet) : le répertoire du scanner (« 09 - Scanner ») avait été pris pour un dossier client par la découverte de
l'installation ; 232 analyses automatiques de pièces scannées attendaient dans « À décider / À relire ». Désormais un répertoire de dépôt
sort de la file, du panneau des décisions, de la liste des dossiers, de l'import et des choix de dossier ; ses travaux automatiques sont
écartés (comme « Ne plus proposer pour ce dossier ») ; aucun fichier n'est touché.

Faux stockage, aucun réseau, aucun modèle ; données fictives (DURAND, MARTIN, KERVILAC, DOS-001)."""
from pathlib import Path
import unittest
from unittest.mock import patch

from agent import bruit5668, dossiers5697 as D
import test_v5697_dossiers as T

ADMIN = T.ADMIN
ROOT = Path(__file__).resolve().parent.parent


def setUpModule():
    T.setUpModule()


def tearDownModule():
    T.tearDownModule()


class Depots(T.Base):
    def setUp(self):
        super().setUp()
        from agent.desk import save_matter
        racine = D.racine(self.desk)
        self.scanner = racine.rsplit('/', 1)[0] + '/09 - Scanner' if racine.count('/') > 1 else racine + '/09 - Scanner'
        save_matter(self.f.c, {'id': 'DEP-099', 'path': self.scanner, 'client_name': '09 - Scanner', 'aliases': [], 'references': [], 'correspondents': []})
        save_matter(self.f.c, {'id': 'DOS-001', 'path': racine + '/DURAND - MARTIN', 'client_name': 'DURAND', 'aliases': [], 'references': [], 'correspondents': []})
        from agent.docrequest520 import ensure_schema
        ensure_schema(self.desk)
        quand = self.desk.now()
        for ident, matter, status in (('dr-1', 'DEP-099', 'echec'), ('dr-2', 'DEP-099', 'dossier_a_choisir'), ('dr-3', 'DOS-001', 'echec')):
            self.desk.db.execute('INSERT INTO docreq520(id,matter,request,kind,status,path,result,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',
                                 (ident, matter, 'Prépare une note fictive.', 'note', status, '', '{}', quand, quand))
        self.desk.db.commit()

    def test_reconnaissance(self):
        self.assertTrue(bruit5668.depot_folder({'path': self.scanner}))
        self.assertTrue(bruit5668.depot_label('09 - Scanner'))
        self.assertFalse(bruit5668.depot_label('DURAND - MARTIN'))
        self.assertEqual(bruit5668.depots(self.desk), frozenset({'DEP-099'}))
        self.assertEqual([m['id'] for m in bruit5668.clients([{'id': 'DEP-099', 'path': self.scanner}, {'id': 'DOS-001', 'path': '/x/DURAND'}])], ['DOS-001'])

    def test_file_et_panneau_sans_travaux_du_depot(self):
        from agent import aujourdhui5614, file5651
        cartes = aujourdhui5614.items(self.desk, 'cabinet', '')
        self.assertNotIn('DEP-099', {str(c['matter']) for c in cartes if c['kind'] == 'docreq'})
        self.assertIn('docreq:dr-3', {c['key'] for c in cartes})                     # le vrai dossier garde sa demande en échec
        elements = file5651.items(self.desk, '', None, '', frozenset())
        self.assertFalse([i for i in elements if str(i.get('matter')) == 'DEP-099' and not str(i.get('object')).startswith(bruit5668.GARDES)])
        self.assertTrue(bruit5668.automatique_de_depot({'matter': 'DEP-099', 'class': 'decision_agent', 'object': 'decision:docreq:dr-1'}, {'DEP-099'}))
        self.assertFalse(bruit5668.automatique_de_depot({'matter': 'DEP-099', 'class': 'decision_agent', 'object': 'decision:document:p1'}, {'DEP-099'}))
        self.assertFalse(bruit5668.automatique_de_depot({'matter': 'DOS-001', 'class': 'livrable', 'object': 'document:dr-3'}, {'DEP-099'}))

    def test_ecarter_une_fois_puis_repetable(self):
        appels = []
        with patch.object(bruit5668, 'exclude_matter', lambda desk, mid, actor='cabinet': appels.append(mid) or {'ok': True}):
            premier = bruit5668.ecarter_depots(self.desk)
            second = bruit5668.ecarter_depots(self.desk)
        self.assertEqual(appels, ['DEP-099'])                                          # « Ne plus proposer pour ce dossier » : une seule fois
        self.assertEqual((premier['exclus'], premier['demandes_annulees']), (1, 2))
        self.assertEqual((second['exclus'], second['demandes_annulees']), (0, 0))
        etats = dict(self.desk.db.execute('SELECT id, status FROM docreq520'))
        self.assertEqual(etats, {'dr-1': 'annule', 'dr-2': 'annule', 'dr-3': 'echec'})   # le dossier client n'est pas touché

    def test_liste_import_et_choix_sans_depot(self):
        ids = {d['id'] for d in D.lister(self.desk, ADMIN)}
        self.assertIn('DOS-001', ids)
        self.assertNotIn('DEP-099', ids)
        rapport = D.migrer(self.desk, ADMIN, simulation=True)
        self.assertEqual(rapport['depots_ignores'], ['DEP-099'])
        self.assertNotIn('DEP-099', {x.get('id') for x in rapport['a_importer']})
        from agent import decisions569
        options = decisions569._matter_options({'DEP-099': '09 - Scanner', 'DOS-001': 'DURAND - MARTIN'}, [], '')
        self.assertIn('DURAND - MARTIN', options)
        self.assertNotIn('09 - Scanner', options)

    def test_depot_deja_importe_hors_de_la_liste(self):
        bruit = D.migrer(self.desk, ADMIN, simulation=False)
        self.assertNotIn('DEP-099', {x.get('id') for x in bruit['a_importer']})
        self.assertNotIn('DEP-099', {d['id'] for d in D.lister(self.desk, ADMIN)})

    def test_decouverte_et_boucle(self):
        setup = (ROOT / 'agent' / 'setup.py').read_text(encoding='utf-8')
        self.assertIn("if depot_folder({'path': item['path']}): continue", setup)
        operations = (ROOT / 'agent' / 'operations.py').read_text(encoding='utf-8')
        self.assertIn('bruit5668.ecarter_depots(desk)', operations)


if __name__ == '__main__':
    unittest.main()

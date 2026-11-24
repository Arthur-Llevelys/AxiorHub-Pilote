"""5.6.109, lot Q (G22) : chaîne de qualité locale raccordée au service commun de recherche.

Service réel ``recherche5695`` sur un index réel (``index.DocumentIndex``, SQLite temporaire), registre ``pseudo540`` du lot P (jamais
dupliqué), droits ``membres5650``. Données fictives (DURAND, ZORVAL, DOS-001, example.test). Aucun réseau, aucun modèle, aucun
téléchargement ; les options OCR et détecteurs ne sont ni installées ni employées. Le gain de qualité réel n'est PAS mesuré ici : le script
``scripts/mesure-qualite56109.py`` est seulement exécuté sur son jeu fictif.
"""
import importlib.util
from pathlib import Path
import socket
import subprocess
import unittest
from unittest.mock import patch

from agent import documents, membres5650, pseudo540, qualite_sources56109 as Q, recherche5695 as R
from agent.common import Stop
from agent.desk import Desk
from agent.index import DocumentIndex
import test_agent as fx

ROOT = Path(__file__).resolve().parents[1]
MID = 'DOS-001'
AVOCAT = ('durand@example.test', 'avocat')
TEXTE = ('Contrat de prestation entre DURAND et la société ZORVAL.\n' + 'Stipulation générale sans portée particulière. ' * 120 +
         '\nArticle 12 — Préavis : chaque partie peut résilier le contrat moyennant un préavis de trois mois notifié par écrit.\n')


def _refuse_reseau(*a, **k):
    raise AssertionError('aucun appel réseau réel dans ces essais')


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fx.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for cible, nom in ((socket.socket, 'connect'), (socket, 'create_connection'), (socket, 'getaddrinfo')):
            p = patch.object(cible, nom, _refuse_reseau)
            p.start()
            self.addCleanup(p.stop)
        self.d = Desk(self.f.c)
        self.addCleanup(self.d.db.close)
        self.index = DocumentIndex(self.f.c['state_dir'])
        self.addCleanup(self.index.db.close)
        self.index.put_source(MID, '/Dossiers/DEMO/Contrat ZORVAL.txt', TEXTE, '"v1"', '2026-01-10', 'document')
        R._CACHE.clear()

    def chercher(self, question='préavis résiliation', acteur=AVOCAT):
        return R.chercher(self.d, MID, question, 5, acteur[0], acteur[1], index=self.index, memoire=False)

    def citation(self, passage, version='"v1"', **kw):
        passages, _ = self.chercher()
        p = passages[0]
        c = {'source_id': p['source_id'], 'version': version, 'section': p['localisateur']['numero'], 'passage': passage}
        return R.relire_citations(self.d, MID, [c], kw.get('acteur', AVOCAT)[0], kw.get('acteur', AVOCAT)[1], index=self.index)[0]


class Citations(Base):
    def test_G22_passage_porte_version_et_localisateur(self):
        passages, etat = self.chercher()
        self.assertTrue(passages)
        self.assertEqual(passages[0]['version'], '"v1"')
        self.assertEqual(passages[0]['localisateur']['type'], 'section_index')      # une section d'index n'est jamais dite « page »
        self.assertEqual(passages[0]['corpus'], 'affaire')

    def test_G22_citation_relue_dans_la_source_seulement_alors_verifiee(self):
        self.assertEqual(self.citation('un préavis de trois mois notifié par écrit')['etat'], 'verifiee')

    def test_G22_garde_citation_inventee_jamais_dite_verifiee(self):
        r = self.citation('un préavis de six mois sans formalité')
        self.assertEqual((r['etat'], r['motif']), ('a_verifier', 'passage_non_retrouve'))
        # mutation en mémoire : sans la relecture du passage, la même citation inventée passerait pour vérifiée
        with patch('agent.qualite_sources56109.passage_relu', return_value=True):
            self.assertEqual(self.citation('un préavis de six mois sans formalité')['etat'], 'verifiee')

    def test_G22_citation_trop_courte_ou_vide_jamais_verifiee(self):
        self.assertEqual(self.citation('préavis')['etat'], 'a_verifier')
        self.assertEqual(self.citation('')['etat'], 'a_verifier')

    def test_G22_version_changee_rend_la_citation_a_verifier(self):
        self.index.put_source(MID, '/Dossiers/DEMO/Contrat ZORVAL.txt', TEXTE.replace('trois mois', 'six mois'), '"v2"', '2026-02-01', 'document')
        r = self.citation('un préavis de trois mois notifié par écrit', version='"v1"')
        self.assertEqual((r['etat'], r['motif']), ('a_verifier', 'source_modifiee'))

    def test_G22_dossier_interdit_rien_relu_rien_dit(self):
        membres5650.set_reserve(self.f.c['state_dir'], MID, ['autre@example.test'])
        r = R.relire_citations(self.d, MID, [{'source_id': 'x', 'version': '"v1"', 'section': 1, 'passage': 'préavis de trois mois notifié'}],
                               AVOCAT[0], AVOCAT[1], index=self.index)[0]
        self.assertEqual((r['etat'], r['motif']), ('a_verifier', 'acces_non_autorise'))

    def test_G22_piece_retiree_par_l_avocat_jamais_verifiee(self):
        p = self.chercher()[0][0]
        with patch('agent.recherche5695.pieces_ecartees', return_value={p['document']}):
            r = R.relire_citations(self.d, MID, [{'source_id': p['source_id'], 'version': '"v1"', 'section': p['localisateur']['numero'],
                                                  'passage': 'un préavis de trois mois notifié par écrit'}], AVOCAT[0], AVOCAT[1], index=self.index)[0]
        self.assertEqual(r['etat'], 'a_verifier')

    def test_G22_corpus_explicite_obligatoire(self):
        with self.assertRaisesRegex(Stop, 'corpus_requis'):
            R.chercher_corpus(self.d, {}, 'préavis')
        with self.assertRaisesRegex(Stop, 'dossier_requis'):
            R.chercher_corpus(self.d, {'type': 'affaire', 'id': ''}, 'préavis')


class Cache(Base):
    def test_G22_cache_servi_pour_le_meme_perimetre(self):
        self.assertEqual(self.chercher()[1]['cache'], 'calcule')
        self.assertEqual(self.chercher()[1]['cache'], 'servi')

    def test_G22_garde_revision_des_identites_invalide_le_cache(self):
        self.chercher()
        pseudo540.invalider(self.d, 'essai')
        self.assertEqual(self.chercher()[1]['cache'], 'calcule')
        # mutation : une clé qui ignorerait la révision des identités servirait un résultat dérivé de l'ancien registre
        self.chercher()
        with patch('agent.recherche5695.revisions', side_effect=lambda *a, **k: {'droits': 'd', 'entites': 'fixe', 'politique': 'p', 'extracteur': 'x'}):
            self.chercher()
            pseudo540.invalider(self.d, 'essai 2')
            self.assertEqual(self.chercher()[1]['cache'], 'servi')

    def test_G22_garde_changement_de_droits_invalide_et_droits_relus_avant_le_cache(self):
        self.chercher()
        membres5650.set_reserve(self.f.c['state_dir'], 'AUTRE-002', ['autre@example.test'])     # droits de l'acteur modifiés
        self.assertEqual(self.chercher()[1]['cache'], 'calcule')
        membres5650.set_reserve(self.f.c['state_dir'], MID, ['autre@example.test'])            # dossier désormais interdit
        passages, etat = self.chercher()
        self.assertEqual((passages, etat), ([], {'refus': 'dossier_reserve'}))                  # jamais servi par le cache

    def test_G22_nouvelle_version_de_l_index_invalide(self):
        self.chercher()
        self.index.put_source(MID, '/Dossiers/DEMO/Note DURAND.txt', 'Note sur le préavis et la résiliation du contrat.', '"n1"', '', 'document')
        self.assertEqual(self.chercher()[1]['cache'], 'calcule')

    def test_G22_revision_absente_aucun_cache(self):
        with patch('agent.pseudo540.revision', return_value=None):
            self.assertEqual(self.chercher()[1]['cache'], 'sans_cache')
            self.assertEqual(self.chercher()[1]['cache'], 'sans_cache')

    def test_G22_cle_de_cache_du_dossier_technique(self):
        a = dict(perimetre_id='cabinet|DOS-001', source_sha='a' * 64, version_extracteur=documents.signature_extraction({}), version_options='o',
                 revision_droits='d', revision_entites='1', revision_politique='p')
        self.assertNotEqual(Q.cle_cache(**a), Q.cle_cache(**{**a, 'revision_droits': 'd2'}))
        with self.assertRaises(Stop):
            Q.cle_cache(**{**a, 'revision_entites': ''})


class Options(unittest.TestCase):
    def test_G22_options_desactivees_par_defaut_et_jamais_installees(self):
        self.assertEqual(set(documents.options_locales({}).values()), {'desactivee'})
        self.assertIn('aucune_option', documents.signature_extraction({}))

    def test_G22_option_demandee_absente_rien_telecharge_rien_lance(self):
        cfg = {'options56109': {o: {'active': True, 'qualifiee': True} for o in documents.OPTIONS56109}}
        with patch.object(socket.socket, 'connect', _refuse_reseau), patch.object(subprocess, 'run', side_effect=AssertionError('aucun processus')), \
                patch.object(subprocess, 'Popen', side_effect=AssertionError('aucun processus')), \
                patch('importlib.util.find_spec', return_value=None):
            etats = documents.options_locales(cfg)
            self.assertEqual(set(etats.values()), {'non_installee'})
            self.assertEqual(documents.signature_extraction(cfg), documents.signature_extraction({}))   # extracteur natif inchangé

    def test_G22_option_installee_reste_non_employee_sans_adaptateur(self):
        cfg = {'options56109': {'paddleocr': {'active': True, 'qualifiee': True}}}
        with patch('importlib.util.find_spec', return_value=object()):
            self.assertEqual(documents.options_locales(cfg)['paddleocr'], 'adaptateur_non_livre')
            self.assertIn('aucune_option', documents.signature_extraction(cfg))

    def test_G22_extraction_garde_l_original_intact(self):
        brut = 'Lettre fictive à ZORVAL : préavis de trois mois.'.encode('utf-8')
        copie = bytes(brut)
        pages = documents.extract_pages(brut, 'lettre.txt', {})
        self.assertEqual(brut, copie)
        self.assertEqual(pages[0]['citation_kind'], 'logical_section')          # jamais une page inventée hors PDF


class Pseudonymes(unittest.TestCase):
    def test_G22_zones_d_un_detecteur_dans_le_meme_registre(self):
        ps = pseudo540.Pseudonymizer(known=[])
        texte = 'Maître DURAND écrit à ZORVAL ; DURAND confirme.'
        a, b = texte.find('DURAND'), texte.rfind('DURAND')
        out = Q.pseudonymiser_zones(ps, texte, [{'start': a, 'end': a + 6}, {'start': a + 2, 'end': a + 6}, {'start': b, 'end': b + 6}])
        jeton = ps.token('PERSONNE', 'DURAND')                                    # même registre : même marqueur
        self.assertEqual(out.count(jeton), 2)
        self.assertEqual(ps.restore(out), texte)
        self.assertNotIn('DURAND', out)

    def test_G22_zone_vide_ne_prouve_rien_et_categorie_inconnue_refusee(self):
        ps = pseudo540.Pseudonymizer(known=[])
        self.assertEqual(Q.pseudonymiser_zones(ps, 'Texte DURAND', []), 'Texte DURAND')
        with self.assertRaises(Stop):
            Q.pseudonymiser_zones(ps, 'Texte DURAND', [], categorie='INCONNUE')

    def test_G22_aucun_second_registre_de_pseudonymes(self):
        source = (ROOT / 'agent' / 'qualite_sources56109.py').read_text(encoding='utf-8')
        self.assertNotIn('CREATE TABLE', source)
        self.assertNotIn('Pseudonymizer(', source)


class Mesure(unittest.TestCase):
    def test_G22_script_de_mesure_sans_reseau_et_sans_gain_pretendu(self):
        spec = importlib.util.spec_from_file_location('mesure_qualite56109', ROOT / 'scripts' / 'mesure-qualite56109.py')
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with patch.object(socket, 'getaddrinfo', _refuse_reseau):
            r = m.mesurer(None, 5, 'gliner')
        self.assertFalse(r['gain_mesure'])
        self.assertIn('aucun gain', r['avertissement'])
        for cle in ('latence_ms', 'rappel_a_k', 'citations_exactes', 'corrections_humaines'):
            self.assertIn(cle, r['base'])
        self.assertIn('pic_allocations_python_octets', r['memoire'])
        self.assertEqual(r['base']['citations_exactes']['relues'], r['base']['citations_exactes']['attendues'])
        self.assertTrue(r['evaluation_finale']['questions'] >= 1)                 # cas réservés à l'évaluation finale séparés
        self.assertFalse(r['variante']['mesuree'])


if __name__ == '__main__':
    unittest.main()

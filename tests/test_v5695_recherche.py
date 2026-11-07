"""5.6.95, lot recherche : service commun de recherche dans un dossier (R01), point d'accroche de reclassement neutre par défaut, mesure sur
cas fictifs (R04), aucune dépendance interdite (R05). L'option de reclassement ONNX (R02, R03) a été retirée par le cabinet : aucun gain de
rappel mesuré, licence du modèle incertaine ; des essais vérifient qu'il n'en reste rien.

Données fictives (``tests/data/recherche5695/cas.json``), aucun réseau, aucun modèle. Un faux reclasseur exerce le point d'accroche.
Ce que ces essais ne prouvent pas : la qualité du classement sur de vrais dossiers.
"""
import ast
import importlib.util
from pathlib import Path
import re
import socket
import tempfile
import types
import unittest
from unittest.mock import patch

from agent import membres5650, recherche5695 as S
from agent.common import Stop
from agent.index import DocumentIndex

ROOT = Path(__file__).resolve().parents[1]
CAS = ROOT / 'tests' / 'data' / 'recherche5695' / 'cas.json'
_spec = importlib.util.spec_from_file_location('mesure_recherche5695', ROOT / 'scripts' / 'mesure-recherche.py')
mesure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mesure)
class Banc(unittest.TestCase):
    """Index et bureau remplis avec les cas fictifs (décisions de l'avocat dans leurs tables réelles)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='recherche5695-test-')
        self.addCleanup(self._tmp.cleanup)
        self.desk, self.index, self.cas = mesure.charger(self._tmp.name)
        self.addCleanup(self.index.db.close)

    def docs(self, passages):
        return [p['document'] for p in passages if not p['valide_avocat']]


def lexical(question, textes):
    """Faux reclasseur déterministe : part des mots de la question présents dans le texte, à l'envers du classement actuel si besoin."""
    mots = S._mots(question)
    return [len(mots & S._mots(t)) / (1 + len(mots)) for t in textes]


# ================================================================ R01 : service commun
class R01Service(Banc):
    def test_R01_passage_complet_et_borne(self):
        out = S.passages(self.desk, 'DOS-002', 'Quel était le salaire mensuel brut prévu au contrat ?', 5, index=self.index, reclasseur=None)
        self.assertEqual(out[0]['document'], '/Dossiers/DOS-002/Pièces/Contrat de travail CDI 2017.txt')
        for p in out:
            self.assertEqual(set(p) >= {'dossier', 'document', 'section', 'extrait', 'score', 'provenance', 'valide_avocat', 'confidentiel'}, True)
            self.assertLessEqual(len(p['extrait']), S.EXTRAIT_MAX + 4)
            self.assertEqual(p['dossier'], 'DOS-002')
        self.assertIn('2 450 euros', out[0]['extrait'])
        self.assertTrue(out[0]['section'].startswith('extrait '))
        self.assertEqual(out[0]['provenance'], 'index')

    def test_R01_extrait_borne_autour_des_mots_de_la_question(self):
        long = ('Préambule sans intérêt. ' * 120) + 'La clause pénale prévoit 9 999 euros par jour de retard. ' + ('Suite sans intérêt. ' * 120)
        ext = S._extrait(long, S._mots('clause pénale retard'))
        self.assertLessEqual(len(ext), S.EXTRAIT_MAX + 4)
        self.assertIn('clause pénale prévoit 9 999 euros', ext)

    def test_R01_piece_ecartee_par_l_avocat_ne_remonte_pas(self):
        ecartee = '/Dossiers/DOS-001/Pièces/Attestation voisin KERVILAC.txt'
        q = 'Le local de ZORVAL est-il fermé et le stock déménagé ?'
        self.assertNotIn(ecartee, self.docs(S.passages(self.desk, 'DOS-001', q, 40, index=self.index, reclasseur=None)))
        with patch.object(S, 'pieces_ecartees', return_value=set()):                       # mutation : sans le filtre, la pièce remonte en tête
            self.assertEqual(self.docs(S.passages(self.desk, 'DOS-001', q, 5, index=self.index, reclasseur=None))[0], ecartee)
        # une nouvelle version courante de la même pièce la rétablit (la décision portait sur la version retirée)
        self.desk.db.execute("INSERT INTO source_versions5689(version_id,cabinet_id,matter,source_id,path,sha256,etat,importe_le) VALUES('v2','banc','DOS-001','s2',?,'h2','courante','x')", (ecartee,))
        self.assertIn(ecartee, self.docs(S.passages(self.desk, 'DOS-001', q, 40, index=self.index, reclasseur=None)))

    def test_R01_fait_refuse_ou_archive_ne_remonte_pas_et_valide_en_tete(self):
        out = S.passages(self.desk, 'DOS-002', 'Le salarié a-t-il reconnu ses absences injustifiées ?', 10, index=self.index, reclasseur=None)
        self.assertFalse(any('mem-002-b' in p['source_id'] for p in out))
        self.desk.db.execute("UPDATE legal_memory_records SET status='validated' WHERE id='mem-002-b'")   # contre-épreuve : validé, il remonterait
        out2 = S.passages(self.desk, 'DOS-002', 'Le salarié a-t-il reconnu ses absences injustifiées ?', 10, index=self.index, reclasseur=None)
        self.assertTrue(out2[0]['valide_avocat'] and 'mem-002-b' in out2[0]['source_id'])
        out3 = S.passages(self.desk, 'DOS-005', 'La sœur accepte-t-elle que Yann garde la maison sans soulte ?', 10, index=self.index, reclasseur=None)
        self.assertFalse(any(p['valide_avocat'] for p in out3))                               # mention archivée : jamais rendue
        out4 = S.passages(self.desk, 'DOS-006', 'La cliente a-t-elle accepté le report de livraison ?', 10, index=self.index, reclasseur=None)
        self.assertFalse(any('as-006-a' in p['source_id'] for p in out4))                     # assertion rejetée

    def test_R01_decision_validee_prioritaire_sur_ancien_courriel(self):
        out = S.passages(self.desk, 'DOS-001', 'Le cabinet accepte-t-il un échéancier de paiement pour ZORVAL ?', 6, index=self.index, reclasseur=None)
        self.assertTrue(out[0]['valide_avocat'])
        self.assertEqual(out[0]['provenance'], 'memoire_validee')
        self.assertIn('refuser tout échéancier', out[0]['extrait'])
        self.assertIn('/Dossiers/DOS-001/Courriels/2023-05-10 proposition échéancier ZORVAL.txt', self.docs(out))   # l'ancien courriel reste, après
        out = S.passages(self.desk, 'DOS-003', 'Faut-il demander la résolution de la vente ?', 6, index=self.index, reclasseur=None)
        self.assertTrue(out[0]['valide_avocat'] and 'as-003-a' in out[0]['source_id'])
        self.assertNotIn('/Dossiers/DOS-003/Procédure/Projet assignation version 1.txt', self.docs(out))
        # une mémoire sans rapport avec la question ne s'impose pas
        out = S.passages(self.desk, 'DOS-001', 'Combien ont coûté les travaux de toiture payés par la bailleresse ?', 6, index=self.index, reclasseur=None)
        self.assertFalse(out[0]['valide_avocat'])

    def test_R01_dossier_reserve_aucun_passage_mutation(self):
        q = 'Quel est le prix forfaitaire du contrat de prestation ?'
        with patch.object(membres5650, 'hidden_for', return_value=frozenset({'DOS-006'})):
            out, etat = S.chercher(self.desk, 'DOS-006', q, 10, 'avocat@example.test', 'avocat', index=self.index, reclasseur=None)
            self.assertEqual((out, etat), ([], {'refus': 'dossier_reserve'}))
            rows, err = S.classeur(self.desk, 'avocat@example.test', 'avocat', reclasseur=None)(self.index, 'DOS-006', [q], limit=6)
            self.assertEqual((rows, err), ([], ''))
            with patch.object(S, 'dossiers_masques', return_value=set()):                    # mutation : sans le contrôle, le dossier fuit
                self.assertTrue(S.passages(self.desk, 'DOS-006', q, 10, 'avocat@example.test', 'avocat', index=self.index, reclasseur=None))
        jeton = membres5650.set_viewer(frozenset({'DOS-006'}))                                # droits de la requête en cours, sans acteur explicite
        try:
            self.assertEqual(S.passages(self.desk, 'DOS-006', q, 10, index=self.index, reclasseur=None), [])
        finally:
            membres5650.set_viewer(frozenset())
        del jeton
        self.assertTrue(S.passages(self.desk, 'DOS-006', q, 10, 'associe1', 'avocat', index=self.index, reclasseur=None))

    def test_R01_classeur_identique_au_classement_actuel_sans_reclasseur(self):
        classer = S.classeur(self.desk, reclasseur=None)
        for q in self.cas['questions']:
            if q['dossier'] in ('DOS-002', 'DOS-004', 'DOS-005', 'DOS-006'):          # dossiers sans pièce écartée
                with self.subTest(q=q['id']):
                    attendu = self.index.ranked_chunks(q['dossier'], [q['question']], limit=6)
                    self.assertEqual(classer(self.index, q['dossier'], [q['question']], limit=6), attendu)

    def test_R01_sources_rapides_meme_format_et_filtre_applique(self):
        cfg = {'max_documents_per_mail': 3}
        m = {'id': 'DOS-001', 'client_name': 'Hélène DURAND', 'path': '/Dossiers/DOS-001'}
        q = 'Le local de ZORVAL est-il fermé et le stock déménagé ?'
        avant, cov_avant = self.index.fast_sources(m, [q], cfg)
        self.index.classer5695 = S.classeur(self.desk, reclasseur=None)                         # comme les appelants : attribut, signature inchangée
        try:
            apres, cov_apres = self.index.fast_sources(m, [q], cfg)
        finally:
            self.index.classer5695 = None
        self.assertEqual(set(cov_avant), set(cov_apres))
        self.assertEqual({tuple(sorted(x)) for x in avant}, {tuple(sorted(x)) for x in apres})   # mêmes clés pour chaque source
        self.assertIn('/Dossiers/DOS-001/Pièces/Attestation voisin KERVILAC.txt', [x['path'] for x in avant])
        self.assertNotIn('/Dossiers/DOS-001/Pièces/Attestation voisin KERVILAC.txt', [x['path'] for x in apres])

    def test_R01_aucun_appel_reseau_pendant_une_recherche(self):
        def interdit(*a, **k):
            raise AssertionError('appel réseau pendant une recherche')
        with patch.object(socket, 'socket', interdit), patch.object(socket, 'create_connection', interdit), patch('urllib.request.urlopen', interdit):
            out = S.passages(self.desk, 'DOS-004', 'Quel est le montant de la facture F2024-031 ?', 5, index=self.index)
        self.assertEqual(out[0]['document'], '/Dossiers/DOS-004/Factures/Facture F2024-031.txt')


class R01Connecteur(unittest.TestCase):
    """Outil ``chercher`` du connecteur Claude : même contrat de sortie, pièce écartée retirée, par le service commun."""

    def setUp(self):
        import test_agent as fixtures
        from agent.desk import Desk
        from agent import assertions5689
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        assertions5689.ensure_schema(self.desk)
        index = DocumentIndex(self.f.c['state_dir'])
        for nom, texte in (('Bail.txt', 'Le loyer mensuel du local est de 1 600 euros.'), ('Ecartee.txt', 'Attestation : le loyer mensuel serait de 900 euros.')):
            index.put_source('DOS-001', '/Dossiers/DEMO/' + nom, texte, nom, '2024-01-01', 'document', {})
        index.db.close()
        self.desk.db.execute("INSERT INTO source_versions5689(version_id,cabinet_id,matter,source_id,path,sha256,etat,importe_le) VALUES('v1','c','DOS-001','s','/Dossiers/DEMO/Ecartee.txt','h','retiree','x')")
        self.desk.db.commit()

    def test_R01_outil_chercher_du_connecteur(self):
        from agent import claude5675
        ctx = types.SimpleNamespace(desk=self.desk, hidden=frozenset(), owner='cabinet', role='administrateur')
        out = claude5675.t_chercher(ctx, {'question': 'loyer mensuel', 'dossier': 'DOS-001'})
        self.assertEqual([r['nom'] for r in out['resultats']], ['Bail.txt'])
        self.assertEqual(set(out['resultats'][0]), {'dossier', 'nom', 'chemin', 'nature', 'date', 'extrait'})
        with patch.object(S, 'pieces_ecartees', return_value=set()):                          # mutation : la pièce retirée reviendrait
            self.assertIn('Ecartee.txt', [r['nom'] for r in claude5675.t_chercher(ctx, {'question': 'loyer mensuel', 'dossier': 'DOS-001'})['resultats']])


# ================================================================ R02 : reclassement
class R01Accroche(Banc):
    """Point d'accroche de reclassement : neutre par défaut, borné, sans erreur si le reclasseur échoue."""

    def ordre(self, reclasseur, q='Quel retard le prestataire a-t-il annoncé ?', mid='DOS-006'):
        return self.docs(S.passages(self.desk, mid, q, 10, index=self.index, reclasseur=reclasseur))

    def test_R01_accroche_reclasseur_change_l_ordre_et_marque_la_provenance(self):
        inverse = lambda q, textes: [float(i) for i in range(len(textes))]                    # le dernier candidat devient premier
        actuel, reclasse = self.ordre(None), self.ordre(inverse)
        self.assertNotEqual(actuel, reclasse)
        out, etat = S.chercher(self.desk, 'DOS-006', 'Quel retard le prestataire a-t-il annoncé ?', 5, index=self.index, reclasseur=inverse, mode='remplacement')
        self.assertEqual(etat['reclassement'], 'applique')
        self.assertTrue(all(p['provenance'] == 'reclasse' for p in out))
        fusion = self.docs(S.chercher(self.desk, 'DOS-006', 'Quel retard le prestataire a-t-il annoncé ?', 10, index=self.index, reclasseur=inverse, mode='fusion')[0])
        self.assertEqual(set(fusion), set(actuel))                                              # mêmes candidats : seul l'ordre change
        self.assertNotEqual(fusion, actuel)

    def test_R01_accroche_au_plus_40_candidats(self):
        vus = []
        espion = lambda q, textes: vus.append(len(textes)) or [0.0] * len(textes)
        S._candidats(self.desk, self.index, 'DOS-001', 'Dossier DOS-001 Hélène DURAND référence interne', reclasseur=espion, nombre=80)
        self.assertTrue(vus and max(vus) <= S.CANDIDATS == 40, vus)

    def test_R01_accroche_delai_depasse_ou_panne_garde_l_ordre_actuel(self):
        actuel = self.ordre(None)
        def delai(q, t):
            raise Stop('reclassement_delai_depasse')
        def panne(q, t):
            raise RuntimeError('moteur cassé')
        for fautif, motif in ((delai, 'reclassement_delai_depasse'), (panne, 'RuntimeError')):
            out, etat = S.chercher(self.desk, 'DOS-006', 'Quel retard le prestataire a-t-il annoncé ?', 10, index=self.index, reclasseur=fautif)
            self.assertEqual(self.docs(out), actuel)
            self.assertIn(motif, etat['reclassement'])

    def test_R01_accroche_neutre_par_defaut(self):
        for q in self.cas['questions'][:20]:
            with self.subTest(q=q['id']):
                out, etat = S.chercher(self.desk, q['dossier'], q['question'], 8, index=self.index)
                self.assertEqual(etat['reclassement'], 'desactive')
                self.assertTrue(all(p['provenance'] in ('index', 'memoire_validee') for p in out))

    def test_R05_aucune_bibliotheque_interdite_dans_le_lot(self):
        interdits = ('torch', 'transformers', 'sentence_transformers', 'haystack', 'tokenizers', 'onnxruntime', 'numpy')
        for nom in ('recherche5695.py',):
            arbre = ast.parse((ROOT / 'agent' / nom).read_text(encoding='utf-8'))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, (ast.Import, ast.ImportFrom)):
                    for alias in getattr(noeud, 'names', []):
                        racine = (getattr(noeud, 'module', None) or alias.name or '').split('.')[0]
                        self.assertNotIn(racine, interdits, nom)
                        self.assertNotIn(alias.name.split('.')[0], interdits, nom)


# ================================================================ option retirée : il n'en reste rien
class RetraitReclassement(Banc):
    def test_retrait_aucune_trace_du_modele_dans_l_application(self):
        for chemin in (ROOT / 'agent').rglob('*.py'):
            self.assertNotIn('mmarco', chemin.read_text(encoding='utf-8', errors='replace').lower(), chemin.name)
        for nom in ('reclassement5695.py', 'recherche_web5695.py', 'jetons5695.py'):
            self.assertFalse((ROOT / 'agent' / nom).exists(), nom)
        texte = (ROOT / 'agent' / 'recherche5695.py').read_text(encoding='utf-8')
        self.assertNotIn('huggingface.co', texte)
        self.assertNotIn('onnx', texte.lower())
        self.assertNotIn('mmarco', (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8').lower())

    def test_retrait_routes_bouton_et_droits(self):
        from agent import ia540, sauvegarde5688, standalone_auth, web5689
        self.assertNotIn('recherche', web5689.DOMAINES)
        self.assertFalse(web5689.allowed('administrateur', 'recherche/installer'))
        self.assertFalse(any('m5689/recherche' in x for x in standalone_auth.ADMIN_API))
        self.assertNotIn('reclassement5695', sauvegarde5688.EXCLUS_DOSSIERS)
        self.assertNotIn('Reclasser les passages', Path(ia540.__file__).read_text(encoding='utf-8'))

    def test_retrait_ancien_dossier_du_modele_ignore_ni_lu_ni_supprime(self):
        ancien = Path(self.desk.c['state_dir']) / 'reclassement5695'
        (ancien / 'onnx').mkdir(parents=True)
        (ancien / 'onnx' / 'model.onnx').write_bytes(b'fichier fictif')
        (ancien / 'manifeste.json').write_text('{"revision": "fictive"}', encoding='utf-8')
        self.desk.setting('recherche5695', {'reclasser': True, 'revision': 3})            # ancien réglage d'un poste d'essai
        lus = []
        ouvrir = open
        def espion(f, *a, **k):
            if 'reclassement5695' in str(f):
                lus.append(str(f))
            return ouvrir(f, *a, **k)
        with patch('builtins.open', espion):
            out, etat = S.chercher(self.desk, 'DOS-004', 'Quel est le montant de la facture F2024-031 ?', 5, index=self.index)
        self.assertEqual((lus, etat['reclassement']), ([], 'desactive'))
        self.assertEqual(out[0]['document'], '/Dossiers/DOS-004/Factures/Facture F2024-031.txt')
        self.assertTrue((ancien / 'onnx' / 'model.onnx').exists())                           # rien n'est supprimé en silence


# ================================================================ R04 : mesure avant activation
class R04Mesure(Banc):
    def test_R04_jeu_de_cas_fictif_et_complet(self):
        self.assertGreaterEqual(len(self.cas['dossiers']), 6)
        self.assertGreaterEqual(len(self.cas['documents']), 40)
        self.assertGreaterEqual(len(self.cas['questions']), 60)
        pieges = {q['piege'] for q in self.cas['questions']}
        self.assertLessEqual({'homonyme', 'ecartee', 'decision', 'sans_reponse', 'fait_ecarte'}, pieges)
        texte = CAS.read_text(encoding='utf-8')
        sirens = set(re.findall(r'\b\d{9}\b', texte))
        self.assertLessEqual(sirens, {'000010017', '000010025', '000010033', '000010041', '000010058', '000010066', '000010074', '000010082', '000010090'})
        domaines = {m.split('@')[1] for m in re.findall(r'[\w.-]+@[\w.-]+', texte)}
        self.assertTrue(domaines and all(d == 'example.test' or d.endswith('.example.test') for d in domaines), domaines)
        for q in self.cas['questions']:
            for chemin in q['attendus'] + q['interdits']:
                self.assertTrue(any(d['chemin'] == chemin for d in self.cas['documents']), chemin)

    def test_R04_mesure_sans_puis_avec_un_faux_reclasseur(self):
        sans = mesure.mesurer(self.desk, self.index, self.cas)['synthese']
        avec = mesure.mesurer(self.desk, self.index, self.cas, lexical, seuil_abstention=0.5)['synthese']
        for s in (sans, avec):
            self.assertEqual(s['questions'], len(self.cas['questions']))
            self.assertEqual(s['decisions_respectees'], 1.0)                                    # décisions de l'avocat : toujours respectées
            for cle in ('rappel_a_5', 'rappel_a_10', 'mrr', 'pieces_justes', 'extrait_juste'):
                self.assertTrue(0.0 <= s[cle] <= 1.0, cle)
            self.assertGreaterEqual(s['rappel_a_10'], s['rappel_a_5'])
            self.assertIsNotNone(s['temps_p95_ms'])
        self.assertIn('abstention_a_tort', avec)
        self.assertGreater(sans['rappel_a_5'], 0.8)                                             # la recherche actuelle trouve l'essentiel du jeu
        with patch.object(S, 'pieces_ecartees', return_value=set()):                            # mutation : la mesure voit la violation
            self.assertLess(mesure.mesurer(self.desk, self.index, self.cas)['synthese']['decisions_respectees'], 1.0)


# ================================================================ R05 : Haystack non retenu
class R05Dependances(unittest.TestCase):
    def test_R05_aucune_dependance_interdite_dans_l_application(self):
        interdits = {'haystack', 'torch', 'transformers', 'sentence_transformers'}
        for chemin in (ROOT / 'agent').glob('*.py'):
            arbre = ast.parse(chemin.read_text(encoding='utf-8'))
            for noeud in ast.walk(arbre):
                if isinstance(noeud, ast.Import):
                    self.assertFalse({a.name.split('.')[0] for a in noeud.names} & interdits, chemin.name)
                elif isinstance(noeud, ast.ImportFrom) and noeud.module and not noeud.level:
                    self.assertNotIn(noeud.module.split('.')[0], interdits, chemin.name)


if __name__ == '__main__':
    unittest.main()

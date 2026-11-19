"""5.6.108, lot R : petits modèles — références tenues par les sources, outils pilotés par le programme.

R1 citer par numéro de source ; R2 barrière après rédaction (une seule reprise, marquage, trace) ; R3 recherche avant rédaction faite par le
programme (connecteurs factices, mode local strict) ; R4 besoins de recherche au lieu d'appels d'outils (schéma, entrée invalide ignorée, au plus
5, aucun nom d'outil transmis) ; R5 petit / grand modèle (seuils, forçage) ; R6 banc d'essai, mode chaîne (faux client, aucun réseau).
Données fictives, faux transports : aucun appel réseau, aucun modèle réel. Ces tests vérifient la mécanique, pas la qualité juridique."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_v5689_ia as base
from test_v5689_ia import AVOCAT, Faux, muter
from agent import competences5632 as c32, competences5683 as c83, connexions5652, references56108 as R, ui_ai5689 as ia
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from banc_ia56108 import banc, chaine, situations as sit_mod  # noqa: E402

NOMS_OUTILS = ('OpenLegi', 'Themia', 'LegalDataHunter', 'Legal Data Hunter', 'JusticeLibre', 'GoodLegal', 'LibreJustice', 'Judilibre', 'HAL',
               'web_search', 'COWORK', 'Cowork', 'CHAT_CU', 'computer use', 'scripts/', '.py', 'MCP', 'jurisprudence.rechercher', 'droit.lire_article')
LIEN = 'https://textes.example.test/cc/1231-2'


class Script:
    """Faux transport du modèle : rend la réponse de l'étape (besoins, reprise, rédaction) et garde les messages reçus."""

    def __init__(self, redaction, besoins='{"recherches": []}', reprise=None):
        self.redaction, self.besoins, self.reprise = redaction, besoins, reprise
        self.recus = []

    def __call__(self, messages, max_tokens, schema=None):
        self.recus.append({'messages': messages, 'schema': schema})
        if schema is not None:
            return self.besoins
        if messages[0]['content'].startswith('Tu relis un texte juridique'):
            if isinstance(self.reprise, BaseException):
                raise self.reprise
            return self.reprise if self.reprise is not None else self.redaction
        return self.redaction


class Corpus:
    def __init__(self, disponible=True):
        self.requetes, self._dispo = [], disponible

    def disponible(self):
        return (True, '') if self._dispo else (False, 'mode local strict : aucune recherche externe')

    def chercher(self, besoin):
        self.requetes.append(besoin)
        if besoin.get('type') == 'article' and besoin.get('numero') == '1231-2':
            return [{'reference': 'article 1231-2 du Code civil', 'titre': 'Code civil, article 1231-2', 'lien': LIEN,
                     'texte': 'Texte fictif pour l’essai : la réparation couvre la perte subie et le gain manqué.'}]
        return []


PIECES = [{'id': 'S1', 'titre': 'Contrat de maintenance (extrait)', 'texte': 'Intervention sous 48 heures ; panne du 2 juillet 2026, intervention le 9 juillet 2026.'},
          {'id': 'S2', 'titre': 'Code civil, article 1231-1', 'texte': 'Extrait fourni : article 1231-1 du Code civil : dommages et intérêts en cas de retard.'}]


def produire(script, connecteurs=None, petit=True, **plus):
    args = {'consigne': 'Consultation pour Mme DURAND contre la société ZORVAL (DOS-001).', 'sources': PIECES, 'petit_modele': petit, **plus}
    return R.produire(None, 'prepare_legal_opinion', args, script, connecteurs)


# ------------------------------------------------------------------------------------------------ R2 : extraction
class Extraction(unittest.TestCase):
    def cles(self, texte):
        return [r['cle'] for r in R.extraire(texte)]

    def test_R2_formes_francaises_courantes(self):
        self.assertEqual(self.cles('selon l’art. 1231-5 C. civ., la pénalité'), ['art:Code civil:1231-5'])
        self.assertEqual(self.cles('l’article L. 1234-5 du Code du travail'), ['art:Code du travail:L1234-5'])
        self.assertEqual(self.cles('L. 1234-5 du Code du travail prévoit'), ['art:Code du travail:L1234-5'])
        self.assertEqual(self.cles('voir R. 211-3'), ['art::R211-3'])
        self.assertEqual(self.cles('articles 1231-1 et 1231-2 du code civil'), ['art:Code civil:1231-1', 'art:Code civil:1231-2'])
        self.assertEqual(self.cles('l’article 700 du code de procédure civile'), ['art:Code de procédure civile:700'])
        self.assertEqual(self.cles('l’article 1240 impose'), ['art::1240'])
        self.assertEqual(self.cles('Cass. civ. 1re, 3 mars 2020, n° 18-99.999'), ['pourvoi:18-99.999'])
        self.assertEqual(self.cles('CE, 12 mai 2021, n° 445678'), ['ce:445678'])
        self.assertEqual(self.cles('Cass. com., 3 mars 2020, la cour retient'), ['date:cass:2020-03-03'])

    def test_R2_faux_positifs_evites(self):
        for texte in ('Le 3 mars 2020, la société a payé.', 'Voir la pièce n° 12 et la pièce 3.', 'Dossier RG 24/01234.', 'article 3 du contrat',
                      'l’article 12 des présentes', 'Article 1 — Objet', '**Article 2** Prix', 'au 14/01/2027 à 9 h 30', '01 00 00 00 05'):
            self.assertEqual(self.cles(texte), [], texte)

    def test_R2_marque_placee_apres_l_identifiant_de_la_decision(self):
        texte = 'Cass. com., 3 mars 2020, la cour retient la faute.'
        out = R.marquer(texte, R.extraire(texte))
        self.assertEqual(out, 'Cass. com., 3 mars 2020 [référence non vérifiée], la cour retient la faute.')

    def test_R2_comparaison_aux_sources_normalisee(self):
        connues = R.Connues(['Extrait : art. 1231-1 C. civ. ; L. 441-10 du Code de commerce ; Cass. civ. 1re, 3 mars 2020, n° 18-99.999', 'RG 24/01234'])
        for ok in ('l’article 1231-1 du Code civil', 'article L. 441-10 du code de commerce', 'l’article 1231-1', 'pourvoi n° 18-99.999',
                   'TJ Valbrune, RG 24/01234'):
            self.assertEqual([r['cle'] for r in R.extraire(ok) if not connues.connue(r)], [], ok)
        for ko in ('l’article 1231-2 du Code civil', 'article L. 441-10 du Code du travail', 'Cass. civ. 3e, 5 juin 2019, n° 18-11.111'):
            self.assertTrue([r for r in R.extraire(ko) if not connues.connue(r)], ko)


# ------------------------------------------------------------------------------------------------ R1 : numéros de source
class Numeros(unittest.TestCase):
    TABLE = {'S1': {'titre': 'Contrat', 'texte': 'x'}, 'S3': {'titre': 'Code civil, article 1231-2', 'reference': 'article 1231-2 du Code civil', 'lien': LIEN, 'texte': 'y'}}

    def test_R1_numero_valide_remplace_par_reference_et_lien_inconnu_retire(self):
        texte, valides, inconnus = R.remplacer_numeros('La perte est réparée [S3]. Le délai court [S7]. Retard établi [S1, S3].', self.TABLE)
        self.assertEqual(texte, 'La perte est réparée (article 1231-2 du Code civil, %s). Le délai court. Retard établi [S1] (article 1231-2 du Code civil, %s).' % (LIEN, LIEN))
        self.assertEqual((valides, inconnus), (3, 1))

    def test_R1_lien_non_https_jamais_recopie(self):
        table = {'S1': {'reference': 'article 1 du Code civil', 'lien': 'javascript:alert(1)'}}
        self.assertEqual(R.remplacer_numeros('Voir [S1].', table)[0], 'Voir (article 1 du Code civil).')

    def test_R1_texte_sans_numero_inchange_et_ponctuation_francaise_gardee(self):
        self.assertEqual(R.remplacer_numeros('Majeure : règle ; mineure : faits .', self.TABLE)[0], 'Majeure : règle ; mineure : faits .')
        self.assertEqual(R.remplacer_numeros('Majeure : règle [S9] ; suite.', self.TABLE)[0], 'Majeure : règle ; suite.')

    def test_R1_numerotation_pieces_puis_textes_trouves(self):
        table = R.numeroter([{'id': 'S1', 'texte': 'a'}, {'id': 'D1', 'excerpt': 'b'}, {'id': 'S2', 'texte': 'c'}], [{'titre': 't', 'texte': 'u'}])
        self.assertEqual(list(table), ['S1', 'D1', 'S2', 'S3'])
        self.assertEqual(table['D1']['texte'], 'b')

    def test_R1_consigne_courte_transmise_au_petit_modele_seulement(self):
        petit, grand = Script('Texte.'), Script('Texte.')
        produire(petit, Corpus())
        produire(grand, Corpus(), petit=False)
        self.assertIn(R.CONSIGNE, petit.recus[-1]['messages'][0]['content'])
        self.assertNotIn(R.CONSIGNE, grand.recus[-1]['messages'][0]['content'])
        self.assertIn('[S1] Contrat de maintenance', petit.recus[-1]['messages'][1]['content'])


# ------------------------------------------------------------------------------------------------ R2 : barrière
class Barriere(unittest.TestCase):
    INVENTE = 'La faute est établie [S1]. Voir l’article 1152 du Code civil et Cass. civ. 3e, 5 juin 2019, n° 18-11.111.'

    def test_R2_une_seule_reprise_puis_marquage(self):
        appels = []

        def reprendre(texte, refs):
            appels.append([r['brut'] for r in refs])
            return texte + ' Voir aussi l’article 1153 du Code civil.'    # le modèle n'obéit pas : rien n'est repris une seconde fois
        b = R.barriere(self.INVENTE, R.Connues(['Contrat']), reprendre)
        self.assertEqual(len(appels), 1)
        self.assertEqual((b['inconnues'], b['reprises'], b['marquees'], b['retirees']), (2, 1, 3, 0))
        self.assertEqual(b['texte'].count(R.MARQUE), 3)
        self.assertIn('article 1152 du Code civil ' + R.MARQUE, b['texte'])
        self.assertIn('n° 18-11.111 ' + R.MARQUE, b['texte'])

    def test_R2_reprise_qui_retire_les_references(self):
        b = R.barriere(self.INVENTE, R.Connues([]), lambda t, refs: 'La faute est établie [S1]. Selon la règle applicable, le retard est réparé.')
        self.assertEqual((b['inconnues'], b['retirees'], b['marquees']), (2, 2, 0))
        self.assertNotIn(R.MARQUE, b['texte'])

    def test_R2_reprise_vide_ou_en_echec_ecartee_texte_d_origine_marque(self):
        for reprendre in (lambda t, r: '', lambda t, r: 'Court.', lambda t, r: (_ for _ in ()).throw(Stop('reponse_ia_vide')),
                          lambda t, r: (_ for _ in ()).throw(ValueError('illisible'))):
            b = R.barriere(self.INVENTE, R.Connues([]), reprendre)
            self.assertTrue(b['reprise_rejetee'])
            self.assertEqual(b['marquees'], 2)
            self.assertTrue(b['texte'].startswith('La faute est établie [S1]. Voir l’article 1152 du Code civil ' + R.MARQUE))

    def test_R2_arret_pause_ou_budget_pendant_la_reprise_remonte(self):
        from agent.arret5643 import STOP_CODES
        code = sorted(STOP_CODES)[0]
        with self.assertRaises(Stop):
            R.barriere(self.INVENTE, R.Connues([]), lambda t, r: (_ for _ in ()).throw(Stop(code)))
        with self.assertRaises(Stop):
            R.barriere(self.INVENTE, R.Connues([]), lambda t, r: (_ for _ in ()).throw(Stop('mission_annulee')))

    def test_R2_reference_des_sources_jamais_marquee_ni_reprise(self):
        appels = []
        b = R.barriere('Selon l’article 1231-1 du Code civil [S2], le retard oblige à réparer.', R.Connues([PIECES[1]['texte']]),
                       lambda t, r: appels.append(1) or t)
        self.assertEqual((b['trouvees'], b['inconnues'], b['marquees'], appels), (1, 0, 0, []))

    def test_R2_chaine_complete_texte_final_compteurs_et_trace(self):
        script = Script('La perte est réparée [S3]. Le retard est fautif [S1]. Voir l’article 1152 du Code civil [S9].',
                        besoins='{"recherches": [{"type": "article", "code": "Code civil", "numero": "1231-2"}]}',
                        reprise='La perte est réparée [S3]. Le retard est fautif [S1]. Selon la règle applicable, une pénalité peut être modérée.')
        out = produire(script, Corpus())
        self.assertEqual(out['texte'], 'La perte est réparée (article 1231-2 du Code civil, %s). Le retard est fautif [S1]. Selon la règle applicable, '
                                       'une pénalité peut être modérée.' % LIEN)
        c = out['compteurs']
        self.assertEqual((c['inconnues'], c['reprises'], c['retirees'], c['marquees'], c['sn_inconnus'], c['sn_valides']), (1, 1, 1, 0, 1, 2))
        self.assertEqual((out['appels_modele'], c['besoins'], c['recherches'], c['sources_ajoutees']), (3, 1, 2, 1))   # d'office (1231-1) + besoin
        self.assertTrue(R.phrase(c).startswith('Références : 1 référence(s) repérée(s) dans le texte produit, 1 absente(s) des sources, une reprise par le modèle'))
        self.assertIn('Aucune référence n’est présentée comme vérifiée', R.phrase(c))

    def test_R2_reponse_de_l_assistant_petit_modele_marquee_grand_inchange(self):
        import sqlite3
        from types import SimpleNamespace
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        bureau = SimpleNamespace(db=db, settings=lambda key, default=None: default, active_job_id=4243)

        class Modele:
            def __init__(self, cfg):
                self.cfg, self.recus = cfg, []

            def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                self.recus.append(messages)
                return messages[1]['content'].split('Texte :\n', 1)[1]          # le modèle ne retire rien
        petit = Modele({'provider_type': 'ollama', 'num_ctx': 4096})
        texte, limites = R.verrouiller_reponse(bureau, petit, self.INVENTE, ['Contrat'])
        self.assertEqual((texte.count(R.MARQUE), len(petit.recus)), (2, 1))
        self.assertTrue(limites)
        self.assertEqual(len(R.lignes_trace(bureau, job_id=4243)), 1)
        api = Modele({'provider_type': 'mistral', 'model': 'grand-distant'})
        self.assertEqual(R.verrouiller_reponse(bureau, api, self.INVENTE, []), (self.INVENTE, []))   # défaut : une API reste hors barrière
        self.assertEqual(api.recus, [])
        grand = Modele({'provider_type': 'ollama', 'num_ctx': 65536, 'parameter_size': '70B'})
        texte, _l = R.verrouiller_reponse(bureau, grand, self.INVENTE, [])     # défaut « Tous les modèles locaux » : un grand modèle local aussi
        self.assertEqual((texte.count(R.MARQUE), len(grand.recus)), (2, 1))
        tous = SimpleNamespace(db=db, settings=lambda key, default=None: 'tous' if key == R.SETTING_BARRIERE else default, active_job_id=4244)
        texte, limites = R.verrouiller_reponse(tous, api, self.INVENTE, [])
        self.assertEqual((texte.count(R.MARQUE), len(api.recus)), (2, 1))       # une seule reprise, par le chemin commun du modèle (Model.complete)
        self.assertIn(R.MENTION_COUT, limites)
        self.assertIn('barrière appliquée : réglage « Tous les modèles, y compris les fournisseurs d’API »', R.lignes_trace(tous, job_id=4244)[0])

    def test_R2_mutation_marquage(self):
        with muter(R, '    out[\'texte\'] = marquer(texte, reste)', '    out[\'texte\'] = texte') as copie:
            b = copie.barriere(self.INVENTE, copie.Connues([]), None)
        self.assertNotIn(R.MARQUE, b['texte'])                                    # sans le garde-fou, la référence inventée passerait sans marque

    def test_R2_mutation_une_seule_reprise(self):
        appels = []
        with muter(R, "    if avant and reprendre is not None:\n        out['reprises'] = 1",
                   "    while avant and reprendre is not None and out['reprises'] < 3:\n        out['reprises'] += 1") as copie:
            copie.barriere(self.INVENTE, copie.Connues([]), lambda t, r: appels.append(1) or t)
        self.assertEqual(len(appels), 3)                                          # la borne « une seule reprise » est bien ce qui l'empêche


# ------------------------------------------------------------------------------------------------ R3 / R4 : recherche par le programme, besoins
class Recherche(unittest.TestCase):
    def test_R4_besoins_schema_ferme_entrees_invalides_ignorees_au_plus_5(self):
        data = {'recherches': [{'type': 'article', 'code': 'C. civ.', 'numero': 'art. 1231-2'},
                               {'type': 'article', 'code': 'Code inventé', 'numero': '12'},
                               {'type': 'outil', 'nom': 'jurisprudence.rechercher'},
                               {'type': 'notion', 'termes': 'clause pénale excessive', 'outil': 'x'},
                               {'type': 'decision', 'numero': '18 11 111'},
                               {'type': 'decision', 'numero': '18-11.111'},
                               {'type': 'notion', 'termes': 'rupture brutale'},
                               {'type': 'notion', 'termes': 'délai de préavis'}, {'type': 'notion', 'termes': 'force majeure'},
                               {'type': 'notion', 'termes': 'faute lourde'}, {'type': 'notion', 'termes': 'gain manqué'}]}
        besoins, ignores = R.valider_besoins(data)
        self.assertEqual(besoins, [{'type': 'article', 'code': 'Code civil', 'numero': '1231-2'}, {'type': 'decision', 'numero': '18-11.111'},
                                   {'type': 'notion', 'termes': 'rupture brutale'}, {'type': 'notion', 'termes': 'délai de préavis'},
                                   {'type': 'notion', 'termes': 'force majeure'}])
        self.assertEqual(ignores, 5)                                              # 3 invalides, 2 au-delà de 5 ; doublon (numéro normalisé) non compté
        self.assertEqual(R.valider_besoins({'autre': 1}), ([], 1))
        self.assertEqual(R.SCHEMA_BESOINS['properties']['recherches']['maxItems'], 5)
        self.assertEqual(R.SCHEMA_BESOINS['properties']['recherches']['items']['properties']['type']['enum'], ['article', 'decision', 'notion'])

    def test_R4_etape_illisible_aucun_besoin(self):
        script = Script('Texte.', besoins='pas du json')
        out = produire(script, Corpus())
        self.assertEqual((out['compteurs']['besoins'], out['compteurs']['besoins_ignores']), (0, 1))

    def test_R3_recherche_d_office_sur_les_pieces_puis_sources_numerotees(self):
        corpus = Corpus()
        out = produire(Script('Texte [S2].'), corpus)
        self.assertEqual(corpus.requetes[0], {'type': 'article', 'code': 'Code civil', 'numero': '1231-1'})   # cité dans la pièce S2
        self.assertEqual(out['compteurs']['d_office'], 1)

    def test_R3_mode_local_strict_aucune_requete_limite_annoncee(self):
        corpus = Corpus(disponible=False)
        out = produire(Script('Texte.', besoins='{"recherches": [{"type": "notion", "termes": "clause pénale"}]}'), corpus)
        self.assertEqual(corpus.requetes, [])
        self.assertTrue(any('mode local strict' in x for x in out['limites']))
        self.assertEqual(out['compteurs']['recherches'], 0)

    def test_R3_connecteurs_produit_mode_local_strict_aucune_requete(self):
        with patch('agent.selection_sources5697.local_strict', return_value=True), patch('agent.juridique5649.research') as research:
            ok, raison = R.ConnecteursProduit(object(), 'DOS-001').disponible()
            textes, bilan = R.rechercher(None, [{'type': 'notion', 'termes': 'clause pénale'}], R.ConnecteursProduit(object(), 'DOS-001'))
        self.assertFalse(ok)
        self.assertIn('mode local strict', raison)
        self.assertEqual((textes, bilan['non_faites']), ([], 1))
        research.assert_not_called()

    def test_R3_bornes_nombre_et_duree(self):
        corpus = Corpus()
        besoins = [{'type': 'notion', 'termes': 'notion %d' % i} for i in range(12)]
        _t, bilan = R.rechercher(None, besoins, corpus)
        self.assertEqual((len(corpus.requetes), bilan['non_faites']), (R.MAX_RECHERCHES, 12 - R.MAX_RECHERCHES))
        horloge = iter([0.0] + [100.0] * 20)
        corpus2 = Corpus()
        _t, bilan = R.rechercher(None, besoins[:3], corpus2, horloge=lambda: next(horloge))
        self.assertEqual((corpus2.requetes, bilan['non_faites']), ([], 3))

    def test_R3_panne_de_connecteur_comptee_jamais_aucun_texte_silencieux(self):
        class Panne(Corpus):
            def chercher(self, besoin):
                raise Stop('connexion_http_indisponible')
        _t, bilan = R.rechercher(None, [{'type': 'notion', 'termes': 'clause pénale'}], Panne())
        self.assertEqual(bilan['echecs'], 1)
        self.assertTrue(any('en échec' in x for x in bilan['limites']))

    def test_R3_chercher_reference_par_le_chemin_commun_anonymise(self):
        from agent import juridique5649
        vu = {}

        def research(desk, matter, question, purpose='redaction', researcher=None):
            vu.update(question=question, purpose=purpose)
            return {'statut': 'consultees', 'sources': [{'titre': 'Article 1231-2', 'reference': 'LEGIARTI0000', 'juridiction': '', 'date': '',
                                                          'extrait': 'texte', 'url': 'https://www.legifrance.gouv.fr/x', 'statut': 'piste_a_verifier'}]}
        with patch.object(juridique5649, 'research', research):
            out = juridique5649.chercher_reference(object(), 'DOS-001', {'type': 'article', 'code': 'Code civil', 'numero': 'L1234-5'})
        self.assertEqual(vu['question'], 'article L1234-5 Code civil')
        self.assertEqual(juridique5649.neutral_query(vu['question']), 'article L1234-5 Code civil')   # survit à l'anonymisation
        self.assertEqual(out[0]['reference'], 'Article 1231-2')
        with patch.object(juridique5649, 'research', lambda *a, **k: {'statut': 'echec', 'erreur': 'x'}):
            with self.assertRaises(Stop):
                juridique5649.chercher_reference(object(), '', {'type': 'notion', 'termes': 'clause pénale'})

    def test_R3_dossier_d_un_autre_droit_aucune_recherche_francaise(self):
        corpus = Corpus()
        out = produire(Script('Texte.'), corpus, droit={'code': 'DE'})
        self.assertEqual((corpus.requetes, out['appels_modele']), ([], 1))
        self.assertTrue(any('autre droit' in x for x in out['limites']))

    def test_R4_aucun_nom_d_outil_dans_les_messages_du_petit_modele(self):
        script = Script('Texte.', besoins='{"recherches": [{"type": "notion", "termes": "clause pénale"}]}')
        produire(script, Corpus())
        envoye = json.dumps([r['messages'] for r in script.recus], ensure_ascii=False)
        for nom in NOMS_OUTILS:
            self.assertNotIn(nom, envoye, nom)

    def test_R4_agent_research_petit_modele_besoins_et_outil_choisi_par_le_programme(self):
        class Modele:
            cfg = {'provider_type': 'ollama', 'model': 'petit', 'num_ctx': 8192}
            recus = []

            def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                self.recus.append((messages, json_schema))
                return '{"recherches": [{"type": "article", "code": "Code civil", "numero": "1231-2"}, {"type": "notion", "termes": "x"}]}'
        m = Modele()
        outils = [{'outil': 'droit.lire_article', 'nature': 'lecture', 'fournisseur': 'legifrance'},
                  {'outil': 'jurisprudence.rechercher', 'nature': 'lecture', 'fournisseur': 'judilibre'}]
        appels = []
        with patch.object(connexions5652, 'available_tools', return_value=outils), patch('agent.vueia5688.blocked', return_value=False), \
                patch('agent.selection_sources5697.local_strict', return_value=False), \
                patch.object(connexions5652, 'call_tool', side_effect=lambda d, o, r, outil, params, matter='': appels.append((outil, params)) or
                             {'resultat': {'texte': 'fictif'}, 'date_lecture': '2026-11-18', 'source': 'Légifrance'}):
            sources = connexions5652.agent_research(object(), 'cabinet', 'avocat', 'Quelle réparation pour M. DURAND ?', 'DOS-001', m)
        self.assertEqual(appels, [('droit.lire_article', {'code': 'Code civil', 'number': '1231-2'})])
        envoye = json.dumps(m.recus[0][0], ensure_ascii=False)
        for nom in NOMS_OUTILS + ('droit.', 'jurisprudence.'):
            self.assertNotIn(nom, envoye, nom)
        self.assertNotIn('DURAND', envoye)                                        # requête anonymisée
        self.assertEqual(m.recus[0][1], R.SCHEMA_BESOINS)
        self.assertEqual([set(s) for s in sources], [{'id', 'kind', 'date', 'resultat'}])   # ni nom d'outil ni nom de connecteur
        with patch.object(connexions5652, 'available_tools', return_value=outils), patch('agent.vueia5688.blocked', return_value=False), \
                patch('agent.selection_sources5697.local_strict', return_value=True), patch.object(connexions5652, 'call_tool') as appel:
            self.assertEqual(connexions5652.agent_research(object(), 'cabinet', 'avocat', 'Quelle réparation ?', '', Modele()), [])
            appel.assert_not_called()

    def test_R4_mutation_grand_modele_garde_le_choix_des_outils(self):
        class Grand:
            cfg = {'provider_type': 'ollama', 'model': 'grand', 'num_ctx': 65536, 'parameter_size': '70B'}
            recus = []

            def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                self.recus.append(messages)
                return '{"appels": []}'
        g = Grand()
        outils = [{'outil': 'jurisprudence.rechercher', 'nature': 'lecture', 'fournisseur': 'judilibre'}]
        with patch.object(connexions5652, 'available_tools', return_value=outils), patch('agent.vueia5688.blocked', return_value=False):
            connexions5652.agent_research(object(), 'cabinet', 'avocat', 'Quelle réparation ?', '', g)
        self.assertIn('jurisprudence.rechercher', g.recus[0][0]['content'])     # un grand modèle garde le fonctionnement actuel


# ------------------------------------------------------------------------------------------------ R5 : petit ou grand modèle
class Seuils(unittest.TestCase):
    def test_R7_reglage_barriere_defaut_tous_les_modeles_locaux(self):
        class Bureau:
            def __init__(self, v):
                self.v = v

            def settings(self, key, default=None):
                return self.v if key == R.SETTING_BARRIERE else default
        local27, api = {'provider_type': 'ollama', 'num_ctx': 32768, 'parameter_size': '27B'}, {'provider_type': 'mistral'}
        self.assertFalse(R.petit_modele(cfg=local27))
        self.assertTrue(R.barriere_active(cfg=local27))                           # défaut, rien d'enregistré
        self.assertFalse(R.barriere_active(cfg=api))
        self.assertTrue(R.barriere_active(cfg=local27, desk=Bureau('locaux')))
        self.assertFalse(R.barriere_active(cfg=api, desk=Bureau('locaux')))
        self.assertTrue(R.barriere_active(cfg=api, desk=Bureau('tous')))
        self.assertFalse(R.barriere_active(cfg=local27, desk=Bureau('petits')))
        self.assertTrue(R.barriere_active(cfg={'provider_type': 'ollama', 'num_ctx': 8192}, desk=Bureau('petits')))   # un petit modèle l'a toujours
        self.assertTrue(R.barriere_active(cfg=local27, desk=Bureau('inconnu')))  # valeur illisible : défaut

    def test_R5_seuils_fenetre_et_parametres(self):
        cas = [({'provider_type': 'ollama', 'num_ctx': 16384}, True), ({'provider_type': 'ollama', 'num_ctx': 16385}, False),
               ({'provider_type': 'ollama', 'num_ctx': 32768, 'parameter_size': '14B'}, True),
               ({'provider_type': 'ollama', 'num_ctx': 32768, 'parameter_size': '14.8B'}, False),
               ({'provider_type': 'ollama', 'num_ctx': 65536, 'parameter_size': '3.2B'}, True), ({'provider_type': 'ollama', 'model': 'x'}, False),
               ({'provider_type': 'mistral', 'num_ctx': 4096}, False)]
        for cfg, attendu in cas:
            self.assertEqual(R.petit_modele(cfg=cfg), attendu, cfg)
        modele = type('M', (), {'cfg': {'provider_type': 'ollama', 'num_ctx': 65536}, 'context_limit': 8192, 'parameter_size': ''})()
        self.assertTrue(R.petit_modele(modele))                                   # fenêtre déclarée par le modèle
        self.assertEqual(R.taille_parametres('500M'), 0.5)
        self.assertIsNone(R.taille_parametres('beaucoup'))

    def test_R5_reglage_administrateur_force_oui_ou_non(self):
        class Bureau:
            def __init__(self, v):
                self.v = v

            def settings(self, key, default=None):
                return self.v if key == R.SETTING else default
        grand = {'provider_type': 'ollama', 'num_ctx': 65536, 'parameter_size': '70B'}
        petit = {'provider_type': 'ollama', 'num_ctx': 4096}
        self.assertTrue(R.petit_modele(cfg=grand, desk=Bureau('oui')))
        self.assertFalse(R.petit_modele(cfg=petit, desk=Bureau('non')))
        self.assertTrue(R.petit_modele(cfg=petit, desk=Bureau('nimporte')))      # valeur inconnue : automatique
        self.assertFalse(R.petit_modele(cfg={'provider_type': 'anthropic'}, desk=Bureau('oui')))   # « petit modèle » : modèle local seulement

    def test_R7_grand_modele_hors_barriere_un_seul_appel_texte_inchange(self):
        script = Script('Voir l’article 1152 du Code civil [S1].')
        out = produire(script, Corpus(), petit=False, barriere=False)          # réglage « Petits modèles seulement » : comportement d'origine
        self.assertEqual((out['appels_modele'], out['texte'], out['compteurs']['inconnues']), (1, 'Voir l’article 1152 du Code civil [S1].', 1))
        self.assertNotIn(R.MARQUE, out['texte'])

    def test_R7_grand_modele_local_barriere_par_defaut_sans_consigne_ni_recherche(self):
        script = Script('Voir l’article 1152 du Code civil [S1] et [S9].', reprise='Voir l’article 1152 du Code civil [S1].')   # reprise sans effet
        corpus = Corpus()
        out = produire(script, corpus, petit=False, cfg={'provider_type': 'ollama', 'num_ctx': 32768, 'parameter_size': '27B'})
        self.assertTrue(out['barriere'])
        self.assertIn('Tous les modèles locaux', out['raison_barriere'])
        self.assertEqual((out['appels_modele'], out['compteurs']['reprises'], out['compteurs']['marquees'], out['compteurs']['sn_inconnus']), (2, 1, 1, 1))
        self.assertIn('article 1152 du Code civil ' + R.MARQUE, out['texte'])
        self.assertEqual(corpus.requetes, [])                                      # ni recherche d'office ni besoins : seule la barrière s'ajoute
        self.assertNotIn(R.CONSIGNE, script.recus[0]['messages'][0]['content'])
        self.assertIsNone(script.recus[0]['schema'])


class Methode(base.Base):
    """Ce qui part réellement vers un petit modèle : version courte, fiches, adaptation, sans aucun nom d'outil, de script ni de connecteur."""

    def setUp(self):
        super().setUp()
        self.desk.setting('competences5632:auto', True)
        c32.ensure_builtin(self.desk)
        c83._CACHE.clear()
        self.addCleanup(c83._CACHE.clear)

    def apercu(self, kind, petit, place, consigne='Rédigez une consultation sur la responsabilité contractuelle de la société ZORVAL.'):
        return c32.apercu_instructions(self.desk, kind, {'instruction': consigne, 'owner': 'cabinet'}, {'num_ctx': place, 'petit_modele': petit, 'max_tokens': 1000})

    def test_R4_aucun_nom_d_outil_transmis_a_un_petit_modele(self):
        for kind in ('prepare_legal_opinion', 'legal_research', 'prepare_document_project', 'attachment_review', 'prepare_hearing', 'verify_deliverable420',
                     'assistant_answer', 'prepare_reply'):
            for place in (4000, 16000, 32000, 128000):
                v = self.apercu(kind, True, place)
                if not v['texte']:
                    continue
                for nom in NOMS_OUTILS:
                    self.assertNotIn(nom, v['texte'], '%s %s %s' % (kind, place, nom))
                self.assertIn('[S2]', v['texte'])                                # consigne de citation par numéro de source
        grand = self.apercu('prepare_legal_opinion', False, 128000)
        self.assertIn('OpenLegi', grand['texte'])                                 # un grand modèle reçoit le texte habituel
        self.assertNotIn('Cite une source seulement par son numéro', grand['texte'])

    def test_R4_regle_de_fond_gardee_mention_d_outil_retiree(self):
        v = self.apercu('prepare_legal_opinion', True, 128000)
        self.assertIn('Lien officiel obligatoire', v['texte'])                    # la règle reste, sans « (OpenLegi en priorité) »
        self.assertIn('Chercher → Trouver → Lire le contenu', v['texte'])

    def test_R4_mutation_filtre_petit_modele(self):
        with muter(c83, "    pm = plan.get('petit_modele') if isinstance(plan.get('petit_modele'), dict) and ctx.get('petit_modele') else {}", '    pm = {}') as copie:
            r = c32._row(self.desk, 'builtin.assistant_juridique')
            d = copie.charger_plan(self.desk, {'id': r['id'], 'version': r['version'], 'mode': 'phase', 'reason': 'Essai'}, r, 60_000,
                                   {'phase': 'analyse', 'query': 'consultation', 'kind': 'prepare_legal_opinion', 'petit_modele': True})
        self.assertIn('OpenLegi', d['texte'])                                     # sans le filtre, les noms d'outils partiraient vers le petit modèle

    def test_R4_envoi_reel_par_le_modele_du_produit(self):
        from agent.model import Model
        recus = []

        class Http:
            def json(self, method, path, body=None):
                recus.append(body)
                return {'done': True, 'done_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}
        m = Model.__new__(Model)
        m.cfg = {'provider_type': 'ollama', 'provider_id': 'ollama', 'model': 'petit-fictif', 'url': 'http://127.0.0.1:11434', 'num_ctx': 32000, 'context_mode': 'fixe'}
        m.provider_type, m.provider_id, m.http, m.context_limit, m.capabilities, m.parameter_size = 'ollama', 'ollama', Http(), None, ['completion'], '3.2B'
        m.usage_cost_usd, m.usage_cost_known = 0.0, True
        row = {'id': 7001, 'kind': 'prepare_legal_opinion', 'args': json.dumps({'instruction': 'Consultation sur la responsabilité de ZORVAL.'}), 'parent_job_id': None}
        with c32.for_job(self.desk, row, {'instruction': 'Consultation sur la responsabilité de ZORVAL.', 'owner': 'cabinet'}):
            m._leaf([{'role': 'system', 'content': 'Cadre.'}, {'role': 'user', 'content': 'Question.'}], 0, 1000, None)
        methode = '\n'.join(x['content'] for x in recus[0]['messages'] if x['role'] == 'system')
        self.assertIn('Assistant juridique', methode)
        for nom in NOMS_OUTILS:
            self.assertNotIn(nom, methode, nom)


# ------------------------------------------------------------------------------------------------ montage dans les missions (ui_ai5689)
class FauxPetit(Faux):
    """Petit modèle fictif (fenêtre 8 192 jetons) : rédaction en JSON (contrat de la mission), besoins, reprise, grille du lot I."""

    def __init__(self, texte, reprise=None, besoins='{"recherches": [{"type": "article", "code": "Code civil", "numero": "1231-2"}]}'):
        super().__init__(cfg={'provider_type': 'ollama', 'purpose': 'assistant', 'model': 'petit-fictif', 'num_ctx': 8192})
        self.texte, self.reprise_texte, self.besoins, self.tous = texte, reprise, besoins, []

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        self.tous.append(messages)
        systeme = messages[0]['content']
        if json_schema is R.SCHEMA_BESOINS:
            return self.besoins
        if systeme.startswith('Tu relis un texte juridique'):
            return self.reprise_texte if self.reprise_texte is not None else messages[1]['content'].split('Texte :\n', 1)[1]
        if systeme.startswith('Tu prépares la grille'):
            return '{"faits": [], "manques": []}'
        return json.dumps({'texte': self.texte, 'sources_citees': []}, ensure_ascii=False)


class Mission(base.Base):
    def lancer_petit(self, modele, action='chronologie'):
        self.doc_indexe()
        with patch('agent.references56108.ConnecteursProduit', lambda desk, matter, purpose='redaction': Corpus()):
            return self.lancer(action, objets=[{'id': 'doc:' + base.CHEMIN, 'version_id': ''}], modele=modele)

    def test_R2_mission_petit_modele_barriere_reprise_marquage_et_trace(self):
        modele = FauxPetit('La perte est réparée [S2]. Voir aussi l’article 1152 du Code civil [S8].')
        m = self.lancer_petit(modele)
        texte = m['resultat5689']['texte'] if m.get('resultat5689') else m['result']['text']
        self.assertIn('(article 1231-2 du Code civil, %s)' % LIEN, texte)
        self.assertIn('article 1152 du Code civil ' + R.MARQUE, texte)           # la reprise (texte identique) n'a rien retiré : marqué
        self.assertNotIn('[S8]', texte)
        lignes = R.lignes_trace(self.desk, mission=m['id'])
        self.assertEqual(len(lignes), 1)
        self.assertIn('1 marquée(s)', lignes[0])
        self.assertIn('recherches faites par le programme', lignes[0])
        carte = ia.pour_carte(self.desk, m)
        self.assertIn(lignes[0], carte['methode_transmise'])
        envoye = json.dumps(modele.tous, ensure_ascii=False)
        for nom in NOMS_OUTILS:
            self.assertNotIn(nom, envoye, nom)

    def texte(self, m):
        return (m.get('resultat5689') or {}).get('texte') or (m.get('result') or {}).get('text', '')

    def test_R7_mission_grand_modele_reglage_petits_inchangee(self):
        self.desk.setting(R.SETTING_BARRIERE, 'petits')
        m = self.lancer('chronologie', objets=[], modele=Faux(defaut={'texte': 'Voir l’article 1152 du Code civil.'}))
        self.assertNotIn(R.MARQUE, self.texte(m))
        self.assertEqual(R.lignes_trace(self.desk, mission=m['id']), [])

    def test_R7_mission_grand_modele_local_barriere_par_defaut(self):
        modele = Faux(defaut={'texte': 'Voir l’article 1152 du Code civil.'})
        m = self.lancer('chronologie', objets=[], modele=modele)
        self.assertIn('article 1152 du Code civil ' + R.MARQUE, self.texte(m))
        lignes = R.lignes_trace(self.desk, mission=m['id'])
        self.assertTrue(lignes[0].startswith('Références (barrière appliquée : réglage « Tous les modèles locaux ») :'))
        self.assertFalse(any('regle_citations' in c for c in modele.charges))   # méthode et charge du grand modèle inchangées

    def test_R7_mission_api_hors_barriere_par_defaut_puis_reglage_tous_par_la_politique(self):
        vus = []
        api = {'provider_type': 'mistral', 'purpose': 'assistant', 'model': 'grand-distant', 'provider_id': 'mistral'}
        with patch('agent.data_policy5689.exiger', lambda config, outil, cible, **o: vus.append((cible, o.get('finalite'), o.get('local')))):
            m = self.lancer('chronologie', objets=[], modele=Faux(cfg=dict(api), defaut={'texte': 'Voir l’article 1152 du Code civil.'}))
            self.assertNotIn(R.MARQUE, self.texte(m))
            self.assertNotIn('reprise_references', [f for _c, f, _l in vus])
            self.desk.setting(R.SETTING_BARRIERE, 'tous')
            m = self.lancer('chronologie', objets=[], modele=Faux(cfg=dict(api), defaut={'texte': 'Voir l’article 1152 du Code civil.'}), cle='cle-chronologie-api-0001')
        self.assertIn('article 1152 du Code civil ' + R.MARQUE, self.texte(m))
        self.assertEqual([x for x in vus if x[1] == 'reprise_references'], [('mistral', 'reprise_references', False)])   # une seule reprise, par la politique
        self.assertIn(R.MENTION_COUT, (m.get('resultat5689') or m['result'].get('normalise') or {}).get('limites', []))

    def test_R7_mutation_api_hors_barriere_par_defaut(self):
        with muter(R, "    if regle == 'locaux' and local:", "    if regle == 'locaux':") as copie:
            self.assertTrue(copie.barriere_active(cfg={'provider_type': 'mistral'}))
        self.assertFalse(R.barriere_active(cfg={'provider_type': 'mistral'}))    # sans le garde-fou, une API passerait sous la barrière par défaut

    def test_R7_reglage_barriere_post_administrateur_idempotent_et_journalise(self):
        import io
        from agent import web5632

        def poster(valeur, who):
            raw = json.dumps({'valeur': valeur}).encode()
            env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
                   'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': 'POST'}
            return web5632.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', 'm5632/admin/skills/barriere', {}, 'POST')
        self.assertEqual(R.reglage_barriere(self.desk), 'locaux')                 # rien d'enregistré : défaut « Tous les modèles locaux »
        with self.assertRaises(Stop):
            poster('tous', AVOCAT)
        with self.assertRaises(Stop):
            poster('partout', base.ADMIN)
        poster('tous', base.ADMIN)
        poster('tous', base.ADMIN)
        self.assertEqual(R.reglage_barriere(self.desk), 'tous')
        n = self.desk.db.execute("SELECT COUNT(*) FROM audit WHERE action='references56108_barriere'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_R2_mutation_montage_mission(self):
        avant = "    if cap.ACTIONS[row['action']]['attendu'] not in ATTENDUS_REFERENCES56108 and row['action'] not in ACTIONS_REFERENCES56108:\n        return None"
        with muter(ia, avant, "    if True:\n        return None") as copie:
            with patch.object(ia, '_references56108', copie._references56108):
                modele = FauxPetit('Voir aussi l’article 1152 du Code civil.')
                m = self.lancer_petit(modele)
        texte = (m.get('resultat5689') or {}).get('texte') or (m.get('result') or {}).get('text', '')
        self.assertNotIn(R.MARQUE, texte)                                         # sans le montage, la référence inventée passerait sans marque


# ------------------------------------------------------------------------------------------------ R6 : banc, mode chaîne
class Banc(unittest.TestCase):
    def test_R6_corpus_des_situations_valide_et_fictif(self):
        data = sit_mod.charger(ROOT / 'tests' / 'donnees' / 'banc_ia' / 'situations.json')
        avec = [s for s in data if s.get('corpus')]
        self.assertGreaterEqual(len(avec), 8)
        for s in avec:
            for c in s['corpus']:
                self.assertIn('texte fictif pour l’essai', c['texte'].lower())
                self.assertTrue(c['lien'].startswith('https://') and '.example.test/' in c['lien'])
        mauvais = dict(avec[0], corpus=[{'id': 'C1', 'type': 'article', 'code': 'Code civil', 'numero': '1', 'reference': 'a', 'titre': 't', 'texte': 'vrai texte',
                                         'lien': 'https://www.legifrance.gouv.fr/x'}])
        self.assertEqual(len(sit_mod.valider_corpus(mauvais)), 2)

    def test_R6_connecteurs_factices_du_corpus(self):
        s = next(x for x in sit_mod.charger(ROOT / 'tests' / 'donnees' / 'banc_ia' / 'situations.json') if x['id'] == 'CS-01')
        c = chaine.ConnecteursCorpus(s['corpus'])
        self.assertEqual(c.chercher({'type': 'article', 'code': 'Code civil', 'numero': '1231-2'})[0]['reference'], 'article 1231-2 du Code civil')
        self.assertEqual(c.chercher({'type': 'article', 'code': 'Code de commerce', 'numero': '1231-2'}), [])
        self.assertTrue(c.chercher({'type': 'decision', 'numero': '26-99.001'}))
        self.assertTrue(c.chercher({'type': 'notion', 'termes': 'retard intervention maintenance'}))

    def test_R6_mode_chaine_de_bout_en_bout_faux_client_sans_reseau(self):
        script = {'besoins': '{"recherches": [{"type": "article", "code": "Code civil", "numero": "1231-2"}, {"type": "outil", "nom": "x"}]}',
                  'redaction': 'Majeure : la perte est réparée [S5]. Mineure : en l’espèce, retard [S2]. Conclusion : responsabilité ; voir aussi '
                               'l’article 1231-8 du Code civil [S9].'}
        with tempfile.TemporaryDirectory() as tmp:
            rap = banc.principal(['--ids', 'CS-01', '--modele', 'ollama:ministral-3:3b', '--chaine', '--sortie', tmp], journal=lambda *_: None,
                                 faux_chaine=script)
            ecrit = json.loads((Path(tmp) / 'rapport.json').read_text(encoding='utf-8'))
            md = (Path(tmp) / 'rapport.md').read_text(encoding='utf-8')
        rep = ecrit['reponses']['ministral-3:3b']['apres'][0]
        self.assertIn('(article 1231-2 du Code civil, https://textes.example.test/cc/1231-2)', rep['texte'])
        self.assertIn('l’article 1231-8 du Code civil ' + R.MARQUE, rep['texte'])
        self.assertEqual((rep['nb_references_inventees'], rep['nb_inventees_non_marquees'], rep['barriere_marquees']), (1, 0, 1))
        self.assertEqual((rep['barriere_reprises'], rep['besoins_ignores'], rep['appels_modele']), (1, 1, 3))
        self.assertEqual(rep['sn_inconnus'], 2)                                   # [S9] retiré deux fois : la reprise scriptée le remet
        self.assertEqual(rep['recherches_programme'], 2)                          # d'office (1231-1, pièce S3) + besoin (1231-2)
        self.assertTrue(rap['meta']['chaine'])
        self.assertIn('Mode chaîne (5.6.108)', md)
        self.assertIn('Chaîne : références marquées', md)
        self.assertEqual(rap['meta']['erreurs'], [])

    def test_R7_banc_barriere_tous_grand_modele_local(self):
        script = {'besoins': '{"recherches": []}', 'redaction': 'Majeure : congé et indemnité [S1]. Voir l’article L145-4 du Code de commerce.',
                  'parametres': '27B'}
        with tempfile.TemporaryDirectory() as tmp:
            rap = banc.principal(['--ids', 'CS-07', '--modele', 'ollama:qwen3.8:27b', '--chaine', '--barriere-tous', '--sortie', tmp], journal=lambda *_: None,
                                 faux_chaine=script)
            ecrit = json.loads((Path(tmp) / 'rapport.json').read_text(encoding='utf-8'))
        rep = ecrit['reponses']['qwen3.8:27b']['apres'][0]
        self.assertFalse(rep['petit_modele'])
        self.assertEqual((rep['barriere_reprises'], rep['barriere_marquees'], rep['appels_modele'], rep['recherches_programme']), (1, 1, 2, 0))
        self.assertEqual(rap['meta']['barriere'], 'tous')
        from banc_ia56108 import ollama_local
        with self.assertRaises(ollama_local.Refus):
            banc.principal(['--ids', 'CS-07', '--modele', 'ollama:qwen3.8:27b', '--barriere-tous'], journal=lambda *_: None)

    def test_R6_mode_chaine_exige_un_modele(self):
        from banc_ia56108 import ollama_local
        with self.assertRaises(ollama_local.Refus):
            banc.principal(['--ids', 'CS-01', '--chaine'], journal=lambda *_: None)

    def test_R6_arbre_sans_chaine_garde_le_rejeu_simple(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'agent').mkdir()
            (Path(tmp) / 'agent' / '__init__.py').write_text('__version__ = "9.9.99"\n', encoding='utf-8')
            brut = banc.lancer_chaine(tmp, [], {'faux': {}})
        self.assertFalse(brut['disponible'])
        self.assertEqual(brut['version'], '9.9.99')


if __name__ == '__main__':
    unittest.main()

"""5.6.108, lot K : conseil de modèles (LLM council) facultatif, piloté par le programme, et sa mesure au banc d'essai.

K1 usage (questions de jugement, à la demande de l'avocat, annonce, jamais automatique) ; K2 membres et préréglages (détection en lecture seule,
défaut, mode local strict, « :cloud » exclu, personnalisation, politique des données par membre) ; K3 déroulé (ordre séquentiel, revue anonyme,
désaccords conservés, membre en échec, pause, annulation, budget, barrière, pourcentages retirés) ; K4 trace ; K5 banc (mode conseil, faux
clients, aucun réseau) ; K6 routes (rôles, 404 neutre, 409, idempotence, aucune écriture pendant un GET).
Données fictives, faux fournisseurs : aucun appel réseau, aucun modèle réel. Ces tests vérifient la mécanique, pas la qualité juridique."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_v5689_ia as base
from test_v5689_ia import AVOCAT, ASSISTANT, ADMIN, muter
from agent import chaine5689, conseil56108 as K, data_policy5689 as dp, missions567, references56108 as R, standalone_auth, ui_ai5689 as ia
from agent.budget_ledger5689 import ATTENTE_CODES
from agent.common import Stop

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from banc_ia56108 import banc, conseil as banc_conseil  # noqa: E402

QWEN32 = {'name': 'qwen3:32b', 'billions': 32.0}
GEMMA27 = {'name': 'gemma3:27b', 'billions': 27.0}
QWEN9 = {'name': 'qwen3.5:9b', 'billions': 9.0}
LLAMA8 = {'name': 'llama3.1:8b', 'billions': 8.0}
MISTRAL_API = {'mistral': {'type': 'mistral', 'model': 'mistral-large-4', 'enabled': True, 'external_data_allowed': True}}
SOURCES = [{'id': 'S1', 'titre': 'Contrat de maintenance (extrait)', 'texte': 'Intervention sous 48 heures ; panne du 2 juillet 2026, intervention le 9 juillet 2026.'},
           {'id': 'S2', 'titre': 'Code civil, article 1231-1', 'texte': 'Extrait fourni : article 1231-1 du Code civil : dommages et intérêts en cas de retard.'}]
QUESTION = 'Faut-il assigner la société ZORVAL pour le retard d’intervention (DOS-001) ou négocier ?'


def membre(nom, local=True, fournisseur='ollama', taille=None):
    return {'fournisseur': fournisseur, 'type': 'ollama' if local else fournisseur, 'modele': nom, 'local': local, 'famille': K.famille(nom, 'ollama' if local else fournisseur),
            'taille': taille, 'petit': False}


REVUE = json.dumps({'non_soutenues': [{'reponse': 'B', 'point': 'Le délai de 48 heures serait impératif sans réserve.'}], 'references_douteuses': [],
                    'argument_adverse_manquant': [{'reponse': 'B', 'point': 'La force majeure invoquée par ZORVAL.'}], 'accords': ['Le retard est établi [S1].'],
                    'classement': ['A', 'B']}, ensure_ascii=False)
REVUE_AB = json.dumps({'non_soutenues': [{'reponse': x, 'point': 'Affirmation %s non soutenue.' % x} for x in 'AB'], 'references_douteuses': [],
                       'argument_adverse_manquant': [], 'accords': [], 'classement': ['A', 'B']}, ensure_ascii=False)
REVUE_B = json.dumps({'non_soutenues': [], 'references_douteuses': [{'reponse': 'A', 'reference': 'article 1152 du Code civil'}],
                      'argument_adverse_manquant': [], 'accords': ['Le retard est établi.'], 'classement': ['A']}, ensure_ascii=False)
CORPS = ('Majeure : la perte est réparée [S2].\nMineure : en l’espèce, l’intervention a eu lieu sept jours après la panne [S1].\n'
         'Conclusion : mise en demeure puis assignation ; réserve : pièces à compléter.')
SECTIONS_PRESIDENT = ('\n\nArgument adverse le plus sérieux : la force majeure invoquée par la partie adverse. Deuxième phrase.\n\n'
                      'Points de désaccord du conseil :\n- A conseille d’assigner, B de négocier [C1].\n- Point inventé sans critique.')
SYNTHESE = CORPS + SECTIONS_PRESIDENT


class Transport:
    """Faux transport du conseil : réponses par étape, journal des appels (ordre, messages), contrôle d'absence de parallélisme."""

    def __init__(self, reponses=None, revues=None, synthese=SYNTHESE, reprise=None, echecs=(), erreur_au_n=None):
        self.reponses = reponses or {}
        self.revues = revues or {}
        self.synthese, self.reprise, self.echecs, self.erreur_au_n = synthese, reprise, set(echecs), erreur_au_n
        self.appels, self.en_cours = [], False

    def __call__(self, m, messages, max_tokens, schema=None):
        assert not self.en_cours, 'deux générations en parallèle'
        self.en_cours = True
        try:
            systeme = messages[0]['content']
            etape = ('reponse' if 'Tu es membre d’un conseil' in systeme else 'revue' if systeme.startswith('Tu relis les réponses') else
                     'synthese' if systeme.startswith('Tu présides') else 'reprise' if systeme.startswith('Tu relis un texte juridique') else 'autre')
            self.appels.append({'modele': m['modele'], 'etape': etape, 'messages': messages, 'schema': schema})
            if self.erreur_au_n is not None and len(self.appels) == self.erreur_au_n[0]:
                raise self.erreur_au_n[1]
            if m['modele'] in self.echecs and etape == 'reponse':
                raise Stop('generation_ia_delai_depasse')
            if etape == 'reponse':
                return self.reponses.get(m['modele'], 'Réponse de %s : il faut agir [S1].' % m['modele'])
            if etape == 'revue':
                return self.revues.get(m['modele'], REVUE)
            if etape == 'reprise':
                return self.reprise if self.reprise is not None else messages[1]['content'].split('Texte :\n', 1)[1]
            if systeme.startswith('Tu prépares la grille'):
                self.appels[-1]['etape'] = 'grille'
                return GRILLE
            return self.synthese
        finally:
            self.en_cours = False


GRILLE = json.dumps({'faits': [{'texte': 'Panne du 2 juillet 2026.', 'nature': 'etabli', 'source_id': 'S1'}],
                     'manques': [{'champ': 'Date de la mise en demeure', 'motif': 'Point de départ des intérêts', 'sections_bloquees': ['Conclusion']}]},
                    ensure_ascii=False)
MANQUE_GRILLE = [{'champ_id': 'g_date_de_la_mise_en_demeure', 'champ': 'Date de la mise en demeure'}]


def deliberer(transport, membres=None, president=None, **plus):
    membres = membres or [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27)]
    return K.deliberer(QUESTION, SOURCES, membres, president or membres[0], transport, graine=7, **plus)


# ------------------------------------------------------------------------------------------------ K3 : déroulé
class Deroule(unittest.TestCase):
    def test_K3_ordre_sequentiel_reponses_revues_synthese(self):
        t = Transport()
        out = deliberer(t, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27), membre('llama3.1:8b', taille=8)])
        etapes = [(a['etape'], a['modele']) for a in t.appels]
        self.assertEqual(etapes[:6], [('reponse', 'qwen3:32b'), ('reponse', 'gemma3:27b'), ('reponse', 'llama3.1:8b'),
                                      ('revue', 'qwen3:32b'), ('revue', 'gemma3:27b'), ('revue', 'llama3.1:8b')])
        self.assertEqual(etapes[6], ('synthese', 'qwen3:32b'))
        self.assertEqual(out['mode'], 'conseil')
        self.assertEqual(out['appels'], len(t.appels))
        self.assertEqual([r['etiquette'] for r in out['reponses']], ['A', 'B', 'C'])
        self.assertIsNotNone(t.appels[3]['schema'])                              # critique structurée (schéma), jamais pour un texte rédigé
        self.assertIsNone(t.appels[0]['schema'])

    def test_K3_memes_sources_numerotees_pour_chaque_membre(self):
        t = Transport()
        deliberer(t)
        recus = [a['messages'][1]['content'] for a in t.appels if a['etape'] == 'reponse']
        self.assertEqual(len(set(recus)), 1)
        self.assertIn('[S1] Contrat de maintenance', recus[0])

    def test_K3_revue_anonyme_aucun_nom_de_modele(self):
        t = Transport(reponses={'qwen3:32b': 'Je suis Qwen (qwen3:32b) : assigner [S1].', 'gemma3:27b': 'En tant que Gemma, négocier [S1].'})
        deliberer(t)
        for a in t.appels:
            if a['etape'] in ('revue', 'synthese'):
                vu = json.dumps(a['messages'], ensure_ascii=False).lower()
                for nom in ('qwen', 'gemma', 'ollama', '32b'):
                    self.assertNotIn(nom, vu, nom)
        revue = [a for a in t.appels if a['etape'] == 'revue'][0]['messages'][1]['content']
        self.assertRegex(revue, r'\[Réponse [AB]\]')
        self.assertNotIn('Réponse C', revue)                                       # deux membres : chacun relit l'autre seulement

    def test_K3_mutation_anonymisation(self):
        with muter(K, "    blocs = '\\n\\n'.join('[Réponse %s]\\n%s' % (lettre, masquer_noms(texte, noms)) for lettre, texte in autres)",
                   "    blocs = '\\n\\n'.join('[Réponse %s]\\n%s' % (lettre, texte) for lettre, texte in autres)") as copie:
            t = Transport(reponses={'qwen3:32b': 'Je suis Qwen : assigner [S1].'})
            copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27)], membre('qwen3:32b'), t, graine=7)
        revue_gemma = [a for a in t.appels if a['etape'] == 'revue' and a['modele'] == 'gemma3:27b'][0]
        self.assertIn('Qwen', revue_gemma['messages'][1]['content'])            # sans le masquage, le nom passerait

    def test_K3_livrable_dans_sa_forme_puis_deux_sections_bornees(self):
        t = Transport(revues={'gemma3:27b': REVUE_B})
        out = deliberer(t)
        corps, sections = out['texte'].split('\n\nArgument adverse le plus sérieux : ', 1)
        self.assertTrue(corps.startswith('Majeure : la perte est réparée'))
        self.assertIn('Mineure', corps)
        self.assertIn('Conclusion', corps)
        adverse, points = sections.split('\n\nPoints de désaccord du conseil :\n', 1)
        self.assertEqual(adverse, 'la force majeure invoquée par la partie adverse.')   # une phrase, bornée
        self.assertEqual(points, '- A conseille d’assigner, B de négocier.')             # point vérifié, numéro de critique retiré
        self.assertEqual((out['compteurs']['desaccords_livrable'], out['compteurs']['points_retires']), (1, 1))
        self.assertEqual(out['points_desaccord'][0]['critiques'], ['C1'])
        synthese = [a for a in t.appels if a['etape'] == 'synthese'][0]['messages']
        self.assertIn(K.FORMES['consultation'], synthese[0]['content'])

    def test_K3_texte_des_critiques_dans_la_trace_jamais_dans_le_livrable(self):
        t = Transport(revues={'gemma3:27b': REVUE_B})
        out = deliberer(t)
        for exact in ('La force majeure invoquée par ZORVAL.', 'Le délai de 48 heures serait impératif sans réserve.', 'article 1152 du Code civil'):
            self.assertNotIn(exact, out['texte'])                                    # ni texte de critique ni référence importée
        trace = json.dumps(out['desaccords'], ensure_ascii=False)
        self.assertIn('La force majeure invoquée par ZORVAL.', trace)               # texte exact, non reformulé, dans la trace
        self.assertIn('article 1152 du Code civil', trace)
        self.assertTrue(all(d['id'].startswith('C') for d in out['desaccords']))
        self.assertEqual(out['compteurs']['marquees'], 0)                            # aucune référence de critique marquée dans le livrable
        html = K._trace_html(dict(out, texte=out['texte']))
        self.assertIn('texte exact des', html)
        self.assertIn('La force majeure invoquée par ZORVAL.', html)

    def test_K3_livrable_d_un_acte_et_d_une_reponse(self):
        t = Transport(synthese='Article 1 — Objet.\nArticle 2 — Prix.' + SECTIONS_PRESIDENT)
        out = deliberer(t, livrable='acte')
        self.assertIn(K.FORMES['acte'], [a for a in t.appels if a['etape'] == 'synthese'][0]['messages'][0]['content'])
        self.assertTrue(out['texte'].startswith('Article 1 — Objet.\nArticle 2 — Prix.'))

    def test_K3_aucun_point_valide_la_trace_est_indiquee(self):
        out = deliberer(Transport(synthese=CORPS + '\n\nPoints de désaccord du conseil :\n- Tout le monde est d’accord.'))
        self.assertIn('Aucun point vérifiable rédigé par le président', out['texte'])
        self.assertIn(K.TITRE_ADVERSE + ' : non formulé par le président.', out['texte'])
        self.assertTrue(any('section(s) absente(s)' in x for x in out['limites']))

    def test_K3_mutation_point_invente_garde(self):
        with muter(K, "        if not ids or not set(ids) <= connus or not texte or len(points) >= maximum:",
                   "        if not texte or len(points) >= maximum:") as copie:
            out = copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27)], membre('qwen3:32b'), Transport(), graine=7)
        self.assertIn('Point inventé sans critique', out['texte'])                   # sans la vérification, le point inventé passerait

    def test_K9_manque_de_la_grille_repris_en_question(self):
        out = deliberer(Transport(), manques_grille=MANQUE_GRILLE)
        corps, questions = out['texte'].split('\n\nInformations à obtenir :\n', 1)
        self.assertIn('- Pouvez-vous préciser : date de la mise en demeure ?', questions)
        self.assertTrue(corps.startswith('Majeure'))                                   # le livrable reste dans sa forme
        self.assertEqual((out['compteurs']['manques_grille'], out['compteurs']['questions_programme']), (1, 1))
        self.assertEqual(out['manques'][0]['origine'], 'grille')

    def test_K9_question_du_president_rattachee_gardee_inventee_retiree(self):
        synthese = CORPS + SECTIONS_PRESIDENT + ('\n\nInformations à obtenir :\n- À quelle date la mise en demeure a-t-elle été envoyée ? [M1]\n'
                                                 '- Quel est le chiffre d’affaires du client ? [M7]\n- Question sans numéro ?')
        out = deliberer(Transport(synthese=synthese), manques_grille=MANQUE_GRILLE)
        questions = out['texte'].split('Informations à obtenir :\n', 1)[1]
        self.assertIn('À quelle date la mise en demeure a-t-elle été envoyée ?', questions)
        self.assertNotIn('chiffre d’affaires', questions)
        self.assertNotIn('Question sans numéro', questions)
        self.assertNotIn('[M1]', out['texte'])
        self.assertEqual((out['compteurs']['questions_retirees'], out['compteurs']['questions_programme']), (2, 0))   # M1 couvert : rien à reprendre
        self.assertTrue(any('question(s) retirée(s)' in x for x in out['limites']))

    def test_K9_manques_signales_par_les_membres_fusionnes_et_dedoublonnes(self):
        t = Transport(reponses={'qwen3:32b': 'Agir [S1]. La date de la mise en demeure n’est pas précisée.',
                                'gemma3:27b': 'Négocier [S1]. Le client a-t-il conservé les échanges écrits avec le prestataire ?'})
        out = deliberer(t, manques_grille=MANQUE_GRILLE)
        self.assertEqual([m['origine'] for m in out['manques']], ['grille', out['manques'][1]['origine']])   # le manque du membre identique à la grille : fusionné
        self.assertEqual(len(out['manques']), 2)
        self.assertIn('Le client a-t-il conservé les échanges écrits avec le prestataire ?', out['texte'])
        president = [a for a in t.appels if a['etape'] == 'synthese'][0]['messages'][1]['content']
        self.assertIn('[M1] Date de la mise en demeure', president)                    # manques numérotés transmis au président

    def test_K9_grille_calculee_comme_la_mission_simple(self):
        t = Transport()
        out = deliberer(t, calculer_grille=True)
        self.assertEqual(t.appels[0]['etape'], 'grille')                                # avant les réponses, une seule étape courte
        self.assertEqual(sum(1 for a in t.appels if a['etape'] == 'grille'), 1)
        self.assertIn('Pouvez-vous préciser : date de la mise en demeure ?', out['texte'])
        self.assertEqual(deliberer(Transport())['compteurs']['manques_grille'], 0)    # sans grille : aucun appel de grille

    def test_K9_mutation_sans_reprise_le_manque_de_la_grille_disparait(self):
        with muter(K, "    manques = fusionner_manques(manques_grille, reponses)", "    manques = fusionner_manques(None, reponses)") as copie:
            out = copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27)], membre('qwen3:32b'), Transport(),
                                  graine=7, manques_grille=MANQUE_GRILLE)
        self.assertNotIn('Informations à obtenir', out['texte'])                     # sans la reprise, le manque de la grille disparaît

    def test_K9_mutation_question_non_rattachee_gardee(self):
        synthese = CORPS + SECTIONS_PRESIDENT + '\n\nInformations à obtenir :\n- Quel est le chiffre d’affaires du client ?'
        with muter(K, "        if not ids or not set(ids) <= connus or len(_cles(texte)) == 0 or len(questions) >= maximum:",
                   "        if len(_cles(texte)) == 0 or len(questions) >= maximum:") as copie:
            out = copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27)], membre('qwen3:32b'),
                                  Transport(synthese=synthese), graine=7, manques_grille=MANQUE_GRILLE)
        self.assertIn('chiffre d’affaires', out['texte'])                               # sans la vérification, la question inventée passerait

    def test_K3_mutation_trace_sans_critiques(self):
        with muter(K, "            'critiques': critiques, 'desaccords': desaccords, 'points_desaccord': points,",
                   "            'critiques': critiques, 'desaccords': [], 'points_desaccord': points,") as copie:
            out = copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27)], membre('qwen3:32b'), Transport(), graine=7)
        self.assertNotIn('La force majeure invoquée par ZORVAL.', json.dumps(out['desaccords'], ensure_ascii=False))   # le test ci-dessus le verrait

    def test_K3_membre_en_echec_le_conseil_continue_et_le_dit(self):
        t = Transport(echecs={'llama3.1:8b'})
        out = deliberer(t, [membre('qwen3:32b', taille=32), membre('gemma3:27b', taille=27), membre('llama3.1:8b', taille=8)])
        self.assertEqual(out['mode'], 'conseil')
        self.assertEqual([m['etat'] for m in out['membres']], ['fait', 'fait', 'hors_delai'])
        self.assertTrue(any('hors délai' in x for x in out['limites']))
        self.assertEqual(len(out['reponses']), 2)
        self.assertNotIn('llama3.1:8b', [a['modele'] for a in t.appels if a['etape'] == 'revue'])

    def test_K3_un_seul_membre_restant_reponse_simple_signalee(self):
        t = Transport(echecs={'gemma3:27b'})
        out = deliberer(t)
        self.assertEqual(out['mode'], 'reponse_simple')
        self.assertTrue(any('un seul membre' in x for x in out['limites']))
        self.assertFalse([a for a in t.appels if a['etape'] in ('revue', 'synthese')])

    def test_K3_aucun_membre_conseil_sans_reponse(self):
        with self.assertRaisesRegex(Stop, 'conseil_sans_reponse'):
            deliberer(Transport(echecs={'qwen3:32b', 'gemma3:27b'}))

    def test_K3_mutation_echec_d_un_membre_arreterait_tout(self):
        with muter(K, "            if isinstance(ex, Stop) and R.controle(ex):\n                raise\n", "            raise\n") as copie:
            with self.assertRaises(Stop):
                copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b'), membre('gemma3:27b'), membre('llama3.1:8b')], membre('qwen3:32b'),
                                Transport(echecs={'llama3.1:8b'}), graine=7)

    def test_K3_pause_ou_annulation_entre_deux_appels(self):
        for code in ('ia_en_pause', 'mission_annulee'):
            t = Transport(erreur_au_n=(2, Stop(code)))
            with self.assertRaisesRegex(Stop, code):
                deliberer(t)
            self.assertEqual(len(t.appels), 2)                                     # rien après l'arrêt

    def test_K3_budget_attente_remonte_telle_quelle(self):
        code = sorted(ATTENTE_CODES)[0]
        t = Transport(erreur_au_n=(3, Stop(code)))
        with self.assertRaisesRegex(Stop, code):
            deliberer(t)
        self.assertEqual(len(t.appels), 3)

    def test_K3_barriere_appliquee_a_la_synthese(self):
        t = Transport(synthese=CORPS + '\nVoir aussi l’article 1152 du Code civil [S9].' + SECTIONS_PRESIDENT)
        out = deliberer(t)
        self.assertIn('article 1152 du Code civil ' + R.MARQUE, out['texte'])
        self.assertEqual((out['compteurs']['reprises'], out['compteurs']['marquees']), (1, 1))
        self.assertNotIn('[S9]', out['texte'])
        self.assertIn('reprise', [a['etape'] for a in t.appels])
        self.assertIn('article 1152 du Code civil', ' '.join(out['refs_marquees']))

    def test_K3_mutation_barriere(self):
        with muter(K, "    b = R.barriere(texte, connues, reprendre)\n", "    b = R.barriere(texte, R.Connues([texte]), None)\n") as copie:
            t = Transport(synthese=CORPS + '\nVoir aussi l’article 1152 du Code civil.' + SECTIONS_PRESIDENT)
            out = copie.deliberer(QUESTION, SOURCES, [membre('qwen3:32b'), membre('gemma3:27b')], membre('qwen3:32b'), t, graine=7)
        self.assertNotIn(R.MARQUE, out['texte'])

    def test_K3_aucun_pourcentage_de_chance_de_succes(self):
        t = Transport(synthese=CORPS + '\nLes chances de succès sont de 70 %.' + SECTIONS_PRESIDENT)
        out = deliberer(t)
        self.assertNotIn('70 %', out['texte'])
        self.assertIn(K.RETIRE, out['texte'])
        self.assertEqual(out['compteurs']['pourcentages_retires'], 1)
        texte, n = K.retirer_pourcentages('Le loyer augmente de 3 % par an.')    # un pourcentage sans lien avec une chance de succès reste
        self.assertEqual((texte, n), ('Le loyer augmente de 3 % par an.', 0))

    def test_K3_synthese_en_echec_reponse_la_mieux_classee_non_synthetisee(self):
        class SansPresident(Transport):
            def __call__(self, m, messages, max_tokens, schema=None):
                if messages[0]['content'].startswith('Tu présides'):
                    self.appels.append({'modele': m['modele'], 'etape': 'synthese', 'messages': messages, 'schema': schema})
                    raise Stop('fournisseur_ia_injoignable')
                return super().__call__(m, messages, max_tokens, schema)
        out = deliberer(SansPresident(), president=membre('autre:14b'))
        self.assertIn('non synthétisée', out['texte'])
        self.assertTrue(any('Synthèse du président non produite' in x for x in out['limites']))

    def test_K3_petit_membre_recoit_la_consigne_des_numeros(self):
        t = Transport()
        petit = dict(membre('llama3.1:8b', taille=8), petit=True)
        deliberer(t, [membre('qwen3:32b', taille=32), petit])
        systemes = {a['modele']: a['messages'][0]['content'] for a in t.appels if a['etape'] == 'reponse'}
        self.assertIn(R.CONSIGNE, systemes['llama3.1:8b'])
        self.assertNotIn(R.CONSIGNE, systemes['qwen3:32b'])

    def test_K3_recherche_d_office_par_le_programme_sources_communes(self):
        class Corpus:
            def __init__(self):
                self.requetes = []

            def disponible(self):
                return True, ''

            def chercher(self, besoin):
                self.requetes.append(besoin)
                return [{'reference': 'article 1231-1 du Code civil', 'titre': 'Code civil, article 1231-1', 'lien': 'https://textes.example.test/cc/1231-1',
                         'texte': 'Texte fictif pour l’essai : dommages et intérêts.'}]
        c = Corpus()
        t = Transport()
        out = deliberer(t, connecteurs=c)
        self.assertEqual(c.requetes, [{'type': 'article', 'code': 'Code civil', 'numero': '1231-1'}])
        self.assertTrue(any(s['trouvee_par_le_programme'] for s in out['sources']))
        self.assertEqual(len({a['messages'][1]['content'] for a in t.appels if a['etape'] == 'reponse'}), 1)


# ------------------------------------------------------------------------------------------------ K2 : préréglages et membres
class Prereglages(base.Base):
    def det(self, installes, api=None):
        return K.detection(self.desk, installes, api or {})

    def test_K2_defaut_local_equilibre_si_disponible(self):
        d = self.det([QWEN32, GEMMA27, QWEN9, LLAMA8], MISTRAL_API)
        self.assertEqual(d['defaut'], 'local_equilibre')
        p = d['prereglages'][0]
        self.assertEqual([m['modele'] for m in p['membres']], ['qwen3:32b', 'gemma3:27b'])
        self.assertEqual(p['president']['modele'], 'qwen3:32b')                   # le plus gros

    def test_K2_defaut_mixte_puis_local_leger_puis_api(self):
        self.assertEqual(self.det([QWEN9, LLAMA8], MISTRAL_API)['defaut'], 'mixte')
        d = self.det([QWEN9, LLAMA8], {})
        self.assertEqual(d['defaut'], 'local_leger')
        self.assertEqual(next(p for p in d['prereglages'] if p['id'] == 'local_leger')['president']['modele'], 'qwen3.5:9b')
        api = dict(MISTRAL_API, claude={'type': 'anthropic', 'model': 'claude-sonnet-5-5', 'enabled': True, 'external_data_allowed': True})
        d = self.det([], api)
        self.assertEqual(d['defaut'], 'api')
        self.assertEqual(next(p for p in d['prereglages'] if p['id'] == 'mixte')['etat'], 'partiel')
        self.assertEqual(self.det([], {})['defaut'], '')

    def test_K2_prereglage_partiel_dit_le_membre_manquant(self):
        d = self.det([QWEN32])
        p = d['prereglages'][0]
        self.assertEqual(p['etat'], 'partiel')
        self.assertIsNone(p['membres'][1])
        self.assertIn('gemma/mistral', p['manques'][1])
        self.assertEqual(self.det(None)['prereglages'][0]['manques'][0], 'Ollama injoignable : modèles locaux inconnus')

    def test_K2_mode_local_strict_prereglages_locaux_seulement(self):
        dp.enregistrer(self.desk, {'mode': 'local_strict', 'perimetre': 'Serveur du cabinet', 'version': 0}, AVOCAT[0])
        d = self.det([QWEN9, LLAMA8], MISTRAL_API)
        self.assertEqual(d['defaut'], 'local_leger')
        self.assertEqual(next(p for p in d['prereglages'] if p['id'] == 'mixte')['etat'], 'exclu')
        K.enregistrer(self.desk, {'mode': 'prereglage', 'prereglage': 'mixte', 'version': 0, 'cle': 'cle-strict-1'}, ADMIN[0], [QWEN9, LLAMA8], MISTRAL_API)
        eff = K.effectifs(self.desk, [QWEN9, LLAMA8], MISTRAL_API)
        self.assertTrue(all(m['local'] for m in eff['membres']))
        self.assertTrue(eff['president'] is None or eff['president']['local'])
        with self.assertRaisesRegex(Stop, 'mode_local_strict|membre_conseil_indisponible'):
            K.enregistrer(self.desk, {'mode': 'personnalise', 'membres': ['ollama::qwen3.5:9b', 'mistral::mistral-large-4'], 'version': 1, 'cle': 'cle-strict-2'},
                          ADMIN[0], [QWEN9, LLAMA8], MISTRAL_API)

    def test_K2_mutation_local_strict(self):
        dp.enregistrer(self.desk, {'mode': 'local_strict', 'perimetre': 'Serveur du cabinet', 'version': 0}, AVOCAT[0])
        K.enregistrer(self.desk, {'mode': 'prereglage', 'prereglage': 'mixte', 'version': 0, 'cle': 'cle-strict-3'}, ADMIN[0], [QWEN9], MISTRAL_API)
        with muter(K, "    if det['local_strict']:\n        externes", "    if False:\n        externes") as copie:
            with patch.object(copie, 'disponibles', lambda desk, i=None, a=None: K.disponibles(desk, [QWEN9], MISTRAL_API)):
                eff = copie.effectifs(self.desk, [QWEN9], MISTRAL_API)
        self.assertTrue(any(not m['local'] for m in eff['membres']))                # sans le filtre, un membre d'API siégerait

    def test_K2_modele_cloud_jamais_membre_local(self):
        d = self.det([QWEN32, {'name': 'gemma3:27b-cloud', 'billions': 27.0}, {'name': 'mistral-small3.2:24b:cloud', 'billions': 24.0}])
        self.assertEqual(d['prereglages'][0]['etat'], 'partiel')
        self.assertTrue(K.est_cloud('gpt-oss:120b-cloud') and K.est_cloud('qwen3:cloud'))
        with self.assertRaisesRegex(Stop, 'modele_cloud_refuse'):
            K.enregistrer(self.desk, {'mode': 'personnalise', 'membres': ['ollama::qwen3:32b', 'ollama::gemma3:27b-cloud'], 'version': 0, 'cle': 'cle-cloud-1'},
                          ADMIN[0], [QWEN32, {'name': 'gemma3:27b-cloud', 'billions': 27.0}], {})

    def test_K2_personnalisation_conservee_puis_retour_au_prereglage(self):
        tous = [QWEN32, GEMMA27, QWEN9, LLAMA8]
        r = K.enregistrer(self.desk, {'mode': 'personnalise', 'membre1': 'ollama::qwen3.5:9b', 'membre2': 'ollama::llama3.1:8b', 'membre3': '',
                                      'president': 'ollama::gemma3:27b', 'version': 0, 'cle': 'cle-perso-1'}, ADMIN[0], tous, {})
        self.assertEqual(r['reglages']['version'], 1)
        eff = K.effectifs(self.desk, tous, {})
        self.assertEqual([m['modele'] for m in eff['membres']], ['qwen3.5:9b', 'llama3.1:8b'])
        self.assertEqual(eff['president']['modele'], 'gemma3:27b')
        self.assertEqual(eff['mode'], 'personnalise')
        K.enregistrer(self.desk, {'mode': 'auto', 'version': 1, 'cle': 'cle-auto-1'}, ADMIN[0], tous, {})
        self.assertEqual([m['modele'] for m in K.effectifs(self.desk, tous, {})['membres']], ['qwen3:32b', 'gemma3:27b'])

    def test_K2_version_perimee_idempotence_et_doublon(self):
        K.enregistrer(self.desk, {'mode': 'prereglage', 'prereglage': 'local_leger', 'version': 0, 'cle': 'cle-idem-1'}, ADMIN[0], [], {})
        again = K.enregistrer(self.desk, {'mode': 'prereglage', 'prereglage': 'local_leger', 'version': 0, 'cle': 'cle-idem-1'}, ADMIN[0], [], {})
        self.assertTrue(again['idempotent'])
        with self.assertRaisesRegex(Stop, 'cle_reutilisee'):
            K.enregistrer(self.desk, {'mode': 'prereglage', 'prereglage': 'api', 'version': 1, 'cle': 'cle-idem-1'}, ADMIN[0], [], {})
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            K.enregistrer(self.desk, {'mode': 'prereglage', 'prereglage': 'api', 'version': 0, 'cle': 'cle-idem-2'}, ADMIN[0], [], {})
        with self.assertRaisesRegex(Stop, 'membres_conseil_doublon'):
            K.enregistrer(self.desk, {'mode': 'personnalise', 'membres': ['ollama::qwen3:32b', 'ollama::qwen3:32b'], 'version': 1, 'cle': 'cle-idem-3'},
                          ADMIN[0], [QWEN32], {})

    def test_K2_annonce_membres_appels_duree_cout(self):
        eff = K.effectifs(self.desk, [QWEN9, LLAMA8], MISTRAL_API)
        a = K.annonce(self.desk, eff)
        self.assertTrue(a['pret'])
        self.assertEqual(a['appels'], 2 + 2 + 1 + 1)
        self.assertTrue(a['pseudonymisation'])
        self.assertIsNotNone(a['cout_estime_usd'])                                  # tarif Mistral Large 4 connu
        self.assertIn('pseudonymisés', a['texte'])
        local = K.annonce(self.desk, K.effectifs(self.desk, [QWEN9, LLAMA8], {}))
        self.assertIn('aucun coût d’API', local['texte'])
        vide = K.annonce(self.desk, K.effectifs(self.desk, [], {}))
        self.assertFalse(vide['pret'])
        self.assertIn('Paramètres › IA', vide['motif'])

    def test_K2_meme_famille_signalee(self):
        eff = K.effectifs(self.desk, [QWEN32, {'name': 'qwen3:14b', 'billions': 14.0}], {})
        self.assertFalse(eff['pret'])                                               # aucun préréglage ne réunit deux qwen
        K.enregistrer(self.desk, {'mode': 'personnalise', 'membres': ['ollama::qwen3:32b', 'ollama::qwen3:14b'], 'version': 0, 'cle': 'cle-fam-1'},
                      ADMIN[0], [QWEN32, {'name': 'qwen3:14b', 'billions': 14.0}], {})
        eff = K.effectifs(self.desk, [QWEN32, {'name': 'qwen3:14b', 'billions': 14.0}], {})
        self.assertTrue(any('même famille' in x for x in eff['limites']))

    def test_K2_aucune_ecriture_pendant_l_affichage(self):
        avant = self.desk.db.total_changes
        with patch('agent.conseil56108._installes', return_value=[QWEN32, GEMMA27]), patch('agent.conseil56108._fournisseurs_api', return_value={}):
            html_reglages = K.reglages_html(self.desk, '/p', 'administrateur')
            html_args = K.arguments_html(self.desk, lambda p, **kw: p, 'DOS-001', AVOCAT[0], 'avocat')
            for route in ('reglages', 'annonce'):
                self.assertEqual(self.appel_api(route, None, {}, ADMIN)['status'], '200 OK')
        self.assertEqual(self.desk.db.total_changes, avant)
        self.assertIsNone(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name='conseil56108'").fetchone())
        self.assertIn('Local équilibré', html_reglages)
        self.assertIn('disponible', html_reglages)
        self.assertIn('Demander l’avis du conseil', html_args)
        self.assertIn('Jusqu’à 6 appels', html_args)

    def test_K2_bouton_sans_membre_explique_comment_configurer(self):
        with patch('agent.conseil56108._installes', return_value=[]), patch('agent.conseil56108._fournisseurs_api', return_value={}):
            out = K.arguments_html(self.desk, lambda p, **kw: p + '?rubrique=ia', 'DOS-001', AVOCAT[0], 'avocat')
        self.assertIn('Paramètres › IA', out)
        self.assertNotIn('data-api="m56108/conseil/lancer"', out)

    def test_K2_politique_des_donnees_par_membre_externe_pseudonymise(self):
        from agent import model as model_mod
        registre = {'mistral': {'type': 'mistral', 'model': 'mistral-large-4', 'enabled': True, 'external_data_allowed': True,
                                'url': 'https://api.example.test/v1'}}
        with patch('agent.ai_gateway.provider_registry', return_value=registre):
            cfg = K.config_membre(self.desk, membre('mistral-large-4', local=False, fournisseur='mistral'), 'DOS-001')
        self.assertEqual((cfg['provider_type'], cfg['model']), ('mistral', 'mistral-large-4'))
        self.assertIs(cfg['external_policy_config'], self.desk.c)
        self.assertEqual(cfg['pseudo'], model_mod.pseudo_sources(self.desk.c))
        appliques, envoyes = [], []

        class Pseudo:
            def __init__(self, sources):
                pass

            def apply_messages(self, messages, method_at=None):
                appliques.append(messages)
                return [{'role': x['role'], 'content': x['content'].replace('ZORVAL', '[SOCIETE_1]')} for x in messages]

            def summary(self):
                return {}

            def restore(self, texte, json_mode=False):
                return str(texte).replace('[SOCIETE_1]', 'ZORVAL')
        m = model_mod.Model.__new__(model_mod.Model)
        m.cfg, m.provider_type, m.provider_id, m.usage_cost_usd, m.usage_cost_known = cfg, 'mistral', 'mistral', 0.0, True
        with patch('agent.pseudo540.Pseudonymizer', Pseudo), patch('agent.pseudo540.log_transmission', lambda *a, **k: None), \
                patch('agent.policy567.check_external', lambda *a, **k: None), patch('agent.data_policy5689.garde_modele', lambda *a, **k: None), \
                patch.object(model_mod.Model, '_openai_compatible', lambda self, messages, *a: envoyes.append(messages) or 'ok'):
            m._leaf([{'role': 'system', 'content': 'Cadre.'}, {'role': 'user', 'content': 'Question sur ZORVAL.'}], 0, 500, None)
        self.assertTrue(appliques)
        self.assertNotIn('ZORVAL', json.dumps(envoyes, ensure_ascii=False))

    def test_K2_fournisseur_externe_non_autorise_refuse(self):
        with patch('agent.ai_gateway.provider_registry', return_value={'ext': {'type': 'mistral', 'model': 'x', 'enabled': True, 'external_data_allowed': False}}):
            with self.assertRaisesRegex(Stop, 'fournisseur_externe_non_autorise'):
                K.config_membre(self.desk, membre('x', local=False, fournisseur='ext'))

    def appel_api(self, route, corps, args, who):
        raw = json.dumps(corps if corps is not None else {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': who[0], 'HTTP_X_AXIORHUB_ROLE': who[1], 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': 'jeton',
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw)}
        methode = 'POST' if corps is not None else 'GET'
        return K.api(env, self.desk, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', K.PREFIXE + route, args, methode)


# ------------------------------------------------------------------------------------------------ K1, K4, K6 : mission, trace, routes
class FauxModele:
    """Faux ``model.Model`` d'un membre : réponse selon l'étape ; journal commun (ordre des appels)."""
    journal = []
    pause_apres = None

    def __init__(self, cfg):
        self.cfg, self.usage_cost_usd = dict(cfg), 0.0

    def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
        systeme = messages[0]['content']
        FauxModele.journal.append((self.cfg['model'], systeme[:30]))
        if 'Tu es membre d’un conseil' in systeme:
            return 'Position de ce membre : agir rapidement [S1].'
        if systeme.startswith('Tu relis les réponses'):
            return REVUE if 'A' not in messages[1]['content'].split('Réponses à examiner (', 1)[1][:3] else REVUE_B
        if systeme.startswith('Tu présides'):
            return SYNTHESE
        return messages[1]['content'].split('Texte :\n', 1)[-1]


class Mission(base.Base):
    def setUp(self):
        super().setUp()
        FauxModele.journal = []
        for cible, valeur in (('agent.conseil56108._installes', [QWEN32, GEMMA27]), ('agent.conseil56108._fournisseurs_api', {}),
                              ('agent.model.Model', FauxModele)):
            p = patch(cible, valeur) if cible.endswith('Model') else patch(cible, return_value=valeur)
            p.start()
            self.addCleanup(p.stop)

    def creer(self, conseil=True, action='incidence', fonction='recherche_juridique', cle='cle-conseil-0001-abcd'):
        data = {'context': {'function_id': fonction, 'matter_id': base.MID, 'instruction': QUESTION, 'expected_result': 'analysis'}, 'action': action,
                'request_key': cle}
        if conseil is not None:
            data['conseil56108'] = conseil
        return ia.creer(self.desk, data, *AVOCAT)

    def travailler(self, m):
        with patch('agent.chaine5689.modele_pour', return_value=base.Faux()):
            self.assertTrue(self.desk.work_once())
        return missions567.get(self.desk, m['id'], missions567.owner_for(AVOCAT[0]))

    def test_K1_K4_mission_conseil_trace_et_carte(self):
        m = self.travailler(self.creer())
        r = m['resultat5689']
        self.assertIn(K.TITRE_POINTS, r['texte'])
        self.assertNotIn('La force majeure invoquée par ZORVAL.', r['texte'])                # texte exact des critiques : trace seulement
        self.assertEqual(r['fournisseur']['nom'], 'conseil de modèles')
        modeles = [x[0] for x in FauxModele.journal]
        self.assertEqual(modeles[:2], ['qwen3:32b', 'gemma3:27b'])                  # réponses l'une après l'autre, puis revues, puis synthèse
        self.assertEqual(FauxModele.journal[4][0], 'qwen3:32b')
        t = K.trace_mission(self.desk, m['id'], *AVOCAT)
        self.assertEqual(t['etat'], 'termine')
        self.assertEqual([x['modele'] for x in t['trace']['membres']], ['qwen3:32b', 'gemma3:27b'])
        self.assertTrue(t['trace']['reponses'] and t['trace']['critiques'] and t['trace']['etapes'])
        carte = ia.pour_carte(self.desk, m)
        self.assertTrue(any(x.startswith('Conseil de modèles') for x in carte['methode_transmise']))
        self.assertTrue(any('Points de désaccord dans le livrable' in x for x in carte['methode_transmise']))
        html = K.arguments_html(self.desk, lambda p, **kw: p, base.MID, AVOCAT[0], 'avocat')
        self.assertIn('Réponse A', html)
        self.assertIn('Critique rédigée par', html)

    def test_K9_mission_grille_questions_regroupees_comme_la_mission_simple(self):
        class PetitDefaut:
            """Modèle par défaut de la mission, à petit contexte : la mission simple établirait la grille ; le conseil la reprend."""
            def __init__(self):
                self.cfg = {'provider_type': 'ollama', 'purpose': 'assistant', 'model': 'petit-fictif', 'num_ctx': 8192}

            def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                return GRILLE if messages[0]['content'].startswith('Tu prépares la grille') else json.dumps({'texte': 'x'})
        m = self.creer(cle='cle-conseil-grille-0001')
        with patch('agent.chaine5689.modele_pour', return_value=PetitDefaut()):
            self.assertTrue(self.desk.work_once())
        m = missions567.get(self.desk, m['id'], missions567.owner_for(AVOCAT[0]))
        r = m['resultat5689']
        self.assertIn('Informations à obtenir', r['texte'])
        self.assertIn('Date de la mise en demeure', r['question_regroupee'])           # question regroupée du mécanisme commun
        self.assertEqual(r['etat'], 'partiel')
        from agent import grille56108
        self.assertIn('g_date_de_la_mise_en_demeure', grille56108.champs_admis(self.desk, m['id']))   # réponse possible, « inconnu » conservé
        t = K.trace_mission(self.desk, m['id'], *AVOCAT)['trace']
        self.assertEqual(t['manques'][0]['origine'], 'grille')
        self.assertTrue(t['questions'])

    def test_K1_aucun_declenchement_automatique(self):
        for valeur in (None, False, 'oui', 1):
            FauxModele.journal = []
            m = self.creer(conseil=valeur, cle='cle-sans-conseil-%s' % str(valeur).lower())
            self.assertIsNone(K.demande(self.desk, m['id']))
            self.travailler(m)
            self.assertEqual(FauxModele.journal, [])                               # aucun membre appelé

    def test_K1_mutation_declenchement(self):
        with muter(K, "    if not isinstance(data, dict) or data.get('conseil56108') is not True:\n        return None",
                   "    if not isinstance(data, dict):\n        return None") as copie:
            copie._installes = lambda desk: [QWEN32, GEMMA27]
            copie._fournisseurs_api = lambda desk: {}
            self.assertIsNotNone(copie.verifier_demande(self.desk, {'conseil56108': False}, {'id': 'incidence'}))
        self.assertIsNone(K.verifier_demande(self.desk, {'conseil56108': False}, {'id': 'incidence'}))

    def test_K1_reserve_aux_questions_de_jugement(self):
        with self.assertRaisesRegex(Stop, 'conseil_reserve_aux_questions_de_jugement'):
            self.creer(action='synthetiser', fonction='dossiers')

    def test_K1_non_configure_refuse(self):
        with patch('agent.conseil56108._installes', return_value=[]):
            with self.assertRaisesRegex(Stop, 'conseil_non_configure'):
                self.creer()

    def test_K3_pause_entre_deux_appels_dans_la_mission(self):
        m = self.creer()
        from agent import pause5629
        vrai = pause5629.checkpoint
        compte = [0]

        def checkpoint(desk):
            if len(FauxModele.journal) >= 2:
                raise Stop('ia_en_pause')
            compte[0] += 1
            return vrai(desk)
        with patch('agent.pause5629.checkpoint', checkpoint):
            with patch('agent.chaine5689.modele_pour', return_value=base.Faux()):
                self.desk.work_once()
        self.assertEqual(len(FauxModele.journal), 2)
        self.assertEqual(K.demande(self.desk, m['id'])['etat'], 'suspendu')

    def test_K3_annulation_entre_deux_appels_dans_la_mission(self):
        m = self.creer(cle='cle-conseil-annule-0001')

        def autorite(desk, data=None):
            if len(FauxModele.journal) >= 1:
                raise Stop('mission_annulee')
        with patch('agent.missions567.check_authority', autorite):
            with patch('agent.chaine5689.modele_pour', return_value=base.Faux()):
                self.desk.work_once()
        self.assertEqual(len(FauxModele.journal), 1)                                # aucun appel après l'annulation
        self.assertEqual(K.demande(self.desk, m['id'])['etat'], 'annule')
        self.assertEqual(ia._charger(self.desk, m['id'])['etat'], 'annule')

    def test_K6_routes_roles_404_neutre_et_lancement(self):
        p = Prereglages.appel_api
        lance = p(self, 'lancer', {'matter': base.MID, 'question': QUESTION, 'confirmer': True, 'cle': 'k108-cle-lancer-a'}, {}, AVOCAT)
        corps = json.loads(lance['body'])
        self.assertEqual(lance['status'], '200 OK', corps)
        self.assertIn(('Cache-Control', 'private, no-store'), lance['headers'])
        self.assertIsNotNone(K.demande(self.desk, corps['mission']))
        refus = p(self, 'lancer', {'matter': base.MID, 'question': QUESTION, 'confirmer': False, 'cle': 'k108-cle-lancer-b'}, {}, AVOCAT)
        self.assertEqual(json.loads(refus['body'])['error'], 'confirmation_requise')
        self.assertEqual(p(self, 'lancer', {'matter': base.MID, 'question': QUESTION, 'confirmer': True, 'cle': 'k108-cle-lancer-c'}, {}, ASSISTANT)['status'],
                         '403 Forbidden')
        self.assertEqual(p(self, 'reglages', {'mode': 'auto', 'version': 0, 'cle': 'k108-cle-reglage-a'}, {}, AVOCAT)['status'], '403 Forbidden')
        autre = p(self, 'trace', None, {'mission': corps['mission']}, ('martin@example.test', 'avocat'))
        absent = p(self, 'trace', None, {'mission': 'f' * 32}, AVOCAT)
        self.assertEqual((autre['status'], absent['status']), ('404 Not Found', '404 Not Found'))
        self.assertEqual(json.loads(autre['body']), json.loads(absent['body']))
        self.assertEqual(p(self, 'trace', None, {'mission': corps['mission']}, AVOCAT)['status'], '200 OK')
        self.assertEqual(p(self, 'reglages', {'mode': 'auto', 'version': 9, 'cle': 'k108-cle-reglage-b'}, {}, ADMIN)['status'], '409 Conflict')

    def test_K7_volet_lancement_assistant_question_de_jugement_et_recu(self):
        p = Prereglages.appel_api
        refus = p(self, 'lancer', {'matter': base.MID, 'question': 'Résume le dernier courriel du dossier.', 'confirmer': True, 'cle': 'k108-cle-volet-a',
                                   'origine': 'assistant'}, {}, AVOCAT)
        self.assertEqual(json.loads(refus['body'])['error'], 'conseil_reserve_aux_questions_de_jugement')
        ok = json.loads(p(self, 'lancer', {'matter': base.MID, 'question': QUESTION, 'confirmer': True, 'cle': 'k108-cle-volet-b', 'origine': 'assistant'},
                          {}, AVOCAT)['body'])
        self.assertEqual(ok['carte']['id'], ok['mission'])                            # reçu du lot A : identifiant de la mission enregistrée
        self.assertIn(ok['carte']['state'], ('creating', 'queued', 'running', 'decision', 'suggested'))
        meme = json.loads(p(self, 'lancer', {'matter': base.MID, 'question': QUESTION, 'confirmer': True, 'cle': 'k108-cle-volet-b', 'origine': 'assistant'},
                            {}, AVOCAT)['body'])
        self.assertEqual(meme['mission'], ok['mission'])                              # même clé : même mission, aucun doublon
        sans = json.loads(p(self, 'lancer', {'matter': base.MID, 'question': QUESTION, 'cle': 'k108-cle-volet-c', 'origine': 'assistant'}, {}, AVOCAT)['body'])
        self.assertEqual(sans['error'], 'confirmation_requise')                       # rien sans confirmation
        a = json.loads(p(self, 'annonce', None, {}, AVOCAT)['body'])
        self.assertTrue(a['pret'] and a['peut_lancer'])
        self.assertFalse(json.loads(p(self, 'annonce', None, {}, ASSISTANT)['body'])['peut_lancer'])

    def test_K7_meme_motif_de_jugement_en_python_et_en_javascript(self):
        import shutil
        import subprocess
        if not shutil.which('node'):
            self.skipTest('Node absent')
        exemples = [QUESTION, 'Quelle stratégie adopter face à MARTIN ?', 'Quel risque si nous attendons ?', 'Résume ce courriel.',
                    'Prépare une chronologie du dossier.', 'Vaut-il mieux transiger ?', 'Opportunité d’un pourvoi', 'Liste les pièces reçues.',
                    'Appréciation des chances en appel', 'Rédige une relance de facture.']
        r = subprocess.run(['node', str(ROOT / 'tests' / 'conseil56108.cjs')], input=json.dumps(exemples), capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [K.est_question_de_jugement(x) for x in exemples])
        self.assertEqual(json.loads(r.stdout), [True, True, True, False, False, True, True, False, True, False])

    def test_K7_script_charge_partout_et_declare(self):
        from agent import ui_helpers, web440
        self.assertIn('/static/v56108-conseil.js', web440.STATIC)
        source = (ROOT / 'agent' / 'ui_helpers.py').read_text(encoding='utf-8')
        self.assertLess(source.index('v56108-accueil.js'), source.index('v56108-conseil.js'))   # reçu du lot A disponible avant
        js = (ROOT / 'agent' / 'static' / 'v56108-conseil.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', js)
        envois = [i for i in range(len(js)) if js.startswith("appel('m56108/conseil/lancer'", i)]
        self.assertEqual(len(envois), 2)                                            # « Confirmer » de l'annonce, formulaire d'Examiner les arguments
        self.assertLess(js.index("ok.addEventListener('click'"), envois[0])          # seulement dans le gestionnaire de « Confirmer »
        self.assertLess(js.index("if (!confirmer || !confirmer.checked)"), envois[1])   # seulement case de confirmation cochée
        self.assertIsNotNone(ui_helpers)

    def test_K6_standalone_auth_declare_les_routes(self):
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m56108/conseil/lancer'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56108/conseil/lancer'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56108/conseil/reglages'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56108/conseil/reglages'))
        self.assertTrue(standalone_auth.allowed('assistant', 'GET', '/api440/m56108/conseil/annonce'))

    def test_K2_politique_local_strict_membre_externe_jamais_appele(self):
        dp.enregistrer(self.desk, {'mode': 'local_strict', 'perimetre': 'Serveur du cabinet', 'version': 0}, AVOCAT[0])
        appels = []

        class Externe(FauxModele):
            def complete(self, messages, temperature=None, max_tokens=0, json_schema=None):
                appels.append(self.cfg['model'])
                return 'jamais'
        eff = {'pret': True, 'membres': [membre('qwen3:32b', taille=32), membre('mistral-large-4', local=False, fournisseur='mistral')],
               'president': membre('qwen3:32b', taille=32), 'limites': []}
        row = {'instruction': QUESTION, 'owner': AVOCAT[0], 'role': AVOCAT[1]}
        K.ensure_schema(self.desk)
        K.enregistrer_demande(self.desk, 'mission-strict', base.MID, AVOCAT[0], {})

        def fabrique(cfg):
            return (Externe if cfg['provider_type'] != 'ollama' else FauxModele)(cfg)
        with patch('agent.ai_gateway.provider_registry', return_value={'ollama': {'type': 'ollama', 'model': 'qwen3:32b', 'url': 'http://127.0.0.1:11434'},
                                                                        'mistral': {'type': 'mistral', 'model': 'mistral-large-4', 'enabled': True,
                                                                                    'external_data_allowed': True}}), \
                patch('agent.missions567.check_authority', lambda *a, **k: None):
            out = K.pour_tache(self.desk, 'mission-strict', row, [], base.MID, '', ('dossier',), fabrique=fabrique, connecteurs=None, eff=eff)
        self.assertEqual(appels, [])                                                # refus de la politique AVANT l'appel
        self.assertEqual(out['trace']['mode'], 'reponse_simple')
        self.assertTrue(any('mistral-large-4' in x for x in out['limites']))


# ------------------------------------------------------------------------------------------------ K5 : banc, mode conseil
class Banc(unittest.TestCase):
    def test_K5_mode_conseil_de_bout_en_bout_faux_clients_sans_reseau(self):
        faux = {'qwen3:32b': {'reponse': 'Position : assigner [S1]. L’adversaire invoquera la force majeure.', 'synthese': SYNTHESE,
                              'redaction': 'Majeure : retard [S1]. Mineure : en l’espèce. Conclusion : agir ; voir l’article 1231-8 du Code civil.'},
                'gemma3:27b': {'reponse': 'Position : négocier [S1].', 'revue': REVUE_AB, 'redaction': 'Réponse seule sans argument.'}}
        with tempfile.TemporaryDirectory() as tmp:
            rap = banc.principal(['--ids', 'CS-01', '--conseil', 'ollama:qwen3:32b,ollama:gemma3:27b', '--sortie', tmp], journal=lambda *_: None,
                                 faux_conseil=faux)
            ecrit = json.loads((Path(tmp) / 'rapport-conseil.json').read_text(encoding='utf-8'))
            md = (Path(tmp) / 'rapport-conseil.md').read_text(encoding='utf-8')
        self.assertEqual(rap['meta']['erreurs'], [])
        self.assertEqual(rap['colonnes'], ['qwen3:32b', 'gemma3:27b', 'conseil'])
        self.assertIn(rap['meilleur_membre_seul'], ('qwen3:32b', 'gemma3:27b'))
        c = ecrit['mesures_conseil'][0]
        self.assertTrue(c['argument_adverse'])
        self.assertGreaterEqual(c['desaccords'], 1)
        self.assertEqual(c['appels_modele'], 6)                                      # grille (fenêtre de 16 000 jetons), 2 réponses, 2 revues, 1 synthèse
        self.assertIn('Conseil contre chaque membre seul', md)
        self.assertIn('Points de désaccord signalés dans le livrable', md)
        self.assertIn('Critiques de la revue croisée, dans la trace', md)
        self.assertEqual(c['desaccords_livrable'], 1)
        self.assertEqual(rap['meta']['hote'], 'local')
        self.assertTrue(ecrit['traces'][0]['reponses'])

    def test_K5_gardes_cloud_hote_et_options(self):
        from banc_ia56108 import ollama_local
        for argv in (['--conseil', 'ollama:a,ollama:gpt-oss:120b-cloud'], ['--conseil', 'ollama:a'], ['--conseil', 'ollama:a,openai:b'],
                     ['--conseil', 'ollama:a,ollama:b', '--modele', 'ollama:a'], ['--conseil', 'ollama:a,ollama:b', '--hote', 'http://203.0.113.5:11434'],
                     ['--conseil', 'ollama:a,ollama:b', '--hote-reseau-prive', 'http://serveur.example.test:11434'],
                     ['--conseil', 'ollama:a,ollama:b', '--sortie', str(ROOT / 'rapport')]):
            with self.assertRaises(ollama_local.Refus, msg=argv):
                banc.principal(argv, journal=lambda *_: None)

    def test_K5_modes_existants_inchanges(self):
        a = banc.analyser(['--modele', 'ollama:x'])
        self.assertEqual((a.conseil, a.president, a.chaine), ('', '', False))

    def test_K5_argument_adverse_mesure(self):
        self.assertTrue(banc_conseil.argument_adverse('Argument adverse le plus sérieux : la force majeure invoquée.'))
        self.assertFalse(banc_conseil.argument_adverse('Le retard est établi.'))


if __name__ == '__main__':
    unittest.main()

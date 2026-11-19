"""5.6.108 (lot E) : banc d'essai avant/après de l'IA (``scripts/banc-ia.py``).

Situations valides et fictives ; refus d'un hôte non local et d'un modèle « cloud » (avec mutation : le test échouerait sans le garde-fou) ;
mesures sur des réponses écrites à la main ; mode statique réel sur cet arbre avec une base temporaire ; mode modèle de bout en bout sur un
faux transport (aucun appel réseau, aucun modèle réel). Hôte du réseau privé du cabinet (option explicite, décision du 18/11/2026) : adresses
de documentation ou 10.0.0.x fictives seulement.
"""
import json
import os
from pathlib import Path
import urllib.error
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from banc_ia56108 import banc, mesures, ollama_local, rapport, situations as sit_mod  # noqa: E402

DONNEES = ROOT / 'tests' / 'donnees' / 'banc_ia' / 'situations.json'
NOMS = {'DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC'}
SIGLES = {'DOS', 'RG', 'BGB', 'GVG', 'ZPO', 'LEC', 'TTC', 'SAS', 'IA', 'GMBH', 'II', 'S.L'}


def toutes():
    return sit_mod.charger(DONNEES)


def situation(**autres):
    base = {'id': 'XX-01', 'famille': 'consultation', 'kind': 'prepare_legal_opinion', 'consigne': 'Consultation pour DURAND.', 'dossier': 'DOS-001',
            'droit': {'code': 'FR', 'libelle': 'droit français', 'juridiction': 'Tribunal judiciaire de Valbrune'}, 'place': 8000, 'livrable': 'consultation',
            'sources': [{'id': 'S1', 'titre': 'Code civil, article 1231-1', 'texte': 'article 1231-1 du Code civil : dommages et intérêts (extrait fourni).'}],
            'attendus': {'manques': [], 'mots_requis': [], 'mots_interdits': []}, 'pieges': []}
    base.update(autres)
    return base


class Reponse:
    def __init__(self, data):
        self._data = json.dumps(data).encode('utf-8')

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FauxOllama:
    """Faux transport : enregistre les requêtes, répond comme Ollama. Aucun réseau."""

    def __init__(self, texte='Bonjour,\nVoici la réponse.', show=None):
        self.requetes, self.texte, self.show = [], texte, show or {'capabilities': ['completion']}

    def __call__(self, req, timeout=None):
        corps = json.loads(req.data.decode('utf-8'))
        self.requetes.append((req.full_url, corps))
        if req.full_url.endswith('/api/show'):
            return Reponse(self.show)
        texte = '{"note": 3, "motif": "essai"}' if corps.get('format') == 'json' else self.texte
        return Reponse({'message': {'role': 'assistant', 'content': texte}, 'done': True, 'done_reason': 'stop',
                        'prompt_eval_count': 1234, 'eval_count': 56})


class Situations(unittest.TestCase):
    def test_E_situations_au_moins_50_et_schema_valide(self):
        data = toutes()
        self.assertGreaterEqual(len(data), 50)
        self.assertEqual(len({s['id'] for s in data}), len(data))
        self.assertEqual({s['famille'] for s in data}, set(sit_mod.FAMILLES))
        self.assertEqual({s['place'] for s in data}, set(sit_mod.PLACES))
        self.assertTrue({'DE', 'ES'} <= {s['droit']['code'] for s in data})
        pieges = [p['type'] for s in data for p in s['pieges']]
        for t in sit_mod.PIEGES:
            self.assertGreaterEqual(pieges.count(t), 3, t)
        kinds = {s['kind'] for s in data}
        for k in ('prepare_reply', 'prepare_legal_opinion', 'legal_research', 'prepare_document_project', 'assistant_answer', 'prepare_hearing'):
            self.assertIn(k, kinds)

    def test_E_schema_refuse_une_situation_defectueuse(self):
        self.assertEqual(sit_mod.valider(situation()), [])
        self.assertTrue(sit_mod.valider(situation(place=5000)))
        self.assertTrue(sit_mod.valider(situation(kind='inconnu')))
        self.assertTrue(sit_mod.valider(situation(pieges=[{'type': 'consigne_cachee', 'temoin': 'ABSENT DES PIÈCES'}])))
        self.assertTrue(sit_mod.valider(situation(pieges=[{'type': 'droit_etranger'}])))   # dossier français
        trop = [{'id': 'S1', 'titre': 'Long', 'texte': 'x' * 20000}]
        self.assertTrue(sit_mod.valider(situation(place=4000, sources=trop)))

    def test_E_aucune_donnee_reelle(self):
        texte = DONNEES.read_text(encoding='utf-8')
        for m in re.finditer(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)', texte):
            self.assertEqual(m.group(1), 'example.test', m.group(0))
        self.assertIsNone(re.search(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', texte))
        self.assertIsNone(re.search(r'(?<!\d)0[1-9](?:[\s.-]?\d{2}){4}(?!\d)', texte))
        self.assertIsNone(re.search(r'(?<!\d)\d{9}(?!\d)|(?<!\d)\d{14}(?!\d)', texte))   # aucun SIREN ni SIRET
        temoins = ' '.join(p.get('temoin', '') for s in toutes() for p in s['pieges'])
        for s in toutes():
            corps = ' '.join([s['consigne']] + [x['titre'] + ' ' + x['texte'] for x in s['sources']])
            corps = re.sub(r'«([^»]*)»', lambda m: ' ' if m.group(1) == m.group(1).upper() else m.group(0), corps)   # phrases-témoins en capitales
            for mot in re.findall(r'\b[A-ZÉÈÀÙÂÊÎÔÛÇ][A-ZÉÈÀÙÂÊÎÔÛÇ.]{2,}\b', corps):
                if mot in temoins or mot.rstrip('.') in SIGLES or mot in SIGLES:
                    continue
                self.assertIn(mot, NOMS, '%s : %s' % (s['id'], mot))
        self.assertTrue(all(s['dossier'] in ('DOS-001', 'DOS-002', 'DOS-003') for s in toutes()))

    def test_E_extraits_de_textes_jamais_presentes_comme_droit_verifie(self):
        for s in toutes():
            for x in s['sources']:
                if re.search(r'\b(?:article|§|artículo)\s', x['titre'] + ' ' + x['texte'][:40], re.I) and re.search(r'Code|BGB|GVG|ZPO|LEC|Ordonnance|Loi', x['titre']):
                    self.assertIn('non vérifié', x['texte'], '%s %s' % (s['id'], x['titre']))


class Garde(unittest.TestCase):
    def test_E_refus_hote_non_local(self):
        for url in ('http://192.0.2.1:11434', 'https://127.0.0.1:11434', 'http://example.test:11434', 'http://127.0.0.1.example.test:11434',
                    'http://compte@localhost:11434', 'http://127.0.0.1:11434/api', 'ftp://127.0.0.1:11434'):
            with self.assertRaises(ollama_local.Refus, msg=url):
                ollama_local.verifier_hote(url)
        for url in ('http://127.0.0.1:11434', 'http://localhost:11434', 'http://[::1]:11434'):
            self.assertTrue(ollama_local.verifier_hote(url))

    def test_E_refus_hote_avant_tout_travail(self):
        with patch.object(banc, 'lancer_arbre', side_effect=AssertionError('aucun travail ne doit commencer')):
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--modele', 'ollama:ministral-3:3b', '--hote', 'http://192.0.2.1:11434'], journal=lambda *_: None)

    def test_E_refus_modele_cloud_et_fournisseur_autre(self):
        for cible in ('ollama:gpt-oss:120b-cloud', 'ollama:minimax-m2:cloud', 'ollama:qwen3-coder:480B-CLOUD', 'openai:gpt-4o', 'ollama:', 'ollama:a b'):
            with self.assertRaises(ollama_local.Refus, msg=cible):
                ollama_local.analyser_cible(cible)
        self.assertEqual(ollama_local.analyser_cible('ollama:ministral-3:3b'), 'ministral-3:3b')
        with patch.object(banc, 'lancer_arbre', side_effect=AssertionError('aucun travail ne doit commencer')):
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--juge', 'ollama:gros:cloud'], journal=lambda *_: None)

    def test_E_refus_modele_servi_par_un_hote_distant(self):
        faux = FauxOllama(show={'remote_host': 'https://ollama.example.test', 'remote_model': 'x'})
        client = ollama_local.Ollama('ministral-3:3b', ouvrir=faux)
        with self.assertRaises(ollama_local.Refus):
            client.chat([{'role': 'user', 'content': 'Bonjour'}], 4000, 100)
        self.assertEqual([u for u, _ in faux.requetes], ['http://127.0.0.1:11434/api/show'])   # aucun /api/chat

    def test_E_sortie_jamais_dans_un_arbre_mesure(self):
        with patch.object(banc, 'lancer_arbre', side_effect=AssertionError('aucun travail ne doit commencer')):
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--sortie', str(ROOT / 'rapport-banc')], journal=lambda *_: None)

    def _mute(self, remplacements):
        """Copie en mémoire du module, garde-fou retiré : le test correspondant doit alors échouer (preuve qu'il le surveille)."""
        source = Path(ollama_local.__file__).read_text(encoding='utf-8')
        for avant, apres in remplacements:
            self.assertIn(avant, source)
            source = source.replace(avant, apres)
        mod = types.ModuleType('ollama_mute')
        exec(compile(source, 'ollama_mute', 'exec'), mod.__dict__)   # noqa: S102 — copie locale du module, jamais écrite sur disque
        return mod

    def test_E_mutation_garde_hote(self):
        mod = self._mute([("if not _forme(parts) or (parts.hostname or '') not in HOTES_LOCAUX:", 'if not _forme(parts):')])
        self.assertTrue(mod.verifier_hote('http://192.0.2.1:11434'))   # sans le garde-fou, l'hôte distant passerait

    def test_E_mutation_garde_cloud(self):
        mod = self._mute([("if re.search(r'[:-]cloud$', nom, re.I):", 'if False:')])
        self.assertEqual(mod.analyser_cible('ollama:minimax-m2:cloud'), 'minimax-m2:cloud')

    def test_E_mutation_garde_distant(self):
        mod = self._mute([("if not isinstance(info, dict) or info.get('remote_host') or info.get('remote_model'):", 'if not isinstance(info, dict):')])
        client = mod.Ollama('ministral-3:3b', ouvrir=FauxOllama(show={'remote_host': 'https://ollama.example.test'}))
        self.assertTrue(client.chat([{'role': 'user', 'content': 'x'}], 4000, 10)['texte'])

    def test_E_requete_reproductible_sans_mandataire(self):
        faux = FauxOllama()
        client = ollama_local.Ollama('ministral-3:3b', ouvrir=faux)
        out = client.chat([{'role': 'user', 'content': 'Bonjour'}], 16000, 700, graine=7)
        url, corps = faux.requetes[-1]
        self.assertEqual(url, 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(corps['options'], {'temperature': 0, 'seed': 7, 'num_ctx': 16000, 'num_predict': 700})
        self.assertIs(corps['stream'], False)
        self.assertEqual((out['jetons_entree'], out['jetons_sortie']), (1234, 56))
        proxies = [h for h in ollama_local.urllib.request.build_opener(ollama_local.urllib.request.ProxyHandler({})).handlers
                   if isinstance(h, ollama_local.urllib.request.ProxyHandler)]
        self.assertTrue(all(not h.proxies for h in proxies))
        self.assertNotIn('think', corps)                       # rien d'ajouté sans requête capturée

    def test_E_reglages_de_la_requete_du_produit_rejoues(self):
        """Passe réelle du 18/11 : sans « think: false » du produit, qwen 3.5 9B réfléchissait jusqu'à sa limite et rendait 34 à 48 réponses vides."""
        faux = FauxOllama(show={'capabilities': ['completion', 'thinking']})
        client = ollama_local.Ollama('qwen3.5:9b', ouvrir=faux)
        client.chat([{'role': 'user', 'content': 'Bonjour'}], 8000, 500, requete={'think': False, 'autre': 1})
        corps = faux.requetes[-1][1]
        self.assertIs(corps['think'], False)
        self.assertNotIn('autre', corps)                       # seuls les réglages connus sont rejoués
        sans = FauxOllama(show={'capabilities': ['completion']})   # modèle sans réflexion : comme le produit, « think » n'est pas envoyé
        ollama_local.Ollama('llama3.1:8b', ouvrir=sans).chat([{'role': 'user', 'content': 'x'}], 8000, 50, requete={'think': False})
        self.assertNotIn('think', sans.requetes[-1][1])
        avec = FauxOllama(show={'capabilities': ['completion', 'thinking']})
        ollama_local.Ollama('qwen3.5:9b', ouvrir=avec).chat([{'role': 'user', 'content': 'x'}], 8000, 50, requete={'think': False})
        self.assertIs(avec.requetes[-1][1]['think'], False)
        # capture sans « think » (le faux transport ignore les capacités du vrai modèle) : règle du produit appliquée sur celles du serveur
        avec2 = FauxOllama(show={'capabilities': ['completion', 'thinking']})
        ollama_local.Ollama('qwen3.5:9b', ouvrir=avec2).chat([{'role': 'user', 'content': 'x'}], 8000, 50, requete={})
        self.assertIs(avec2.requetes[-1][1]['think'], False)


class MesuresReponse(unittest.TestCase):
    def test_E_renvoi_interne_a_une_clause_de_l_acte_n_est_pas_une_citation(self):
        """Mesure réelle du 19/11 (qwen3.8:27b) : « prévues à l'Article 4 » renvoyait à la clause 4 de l'acte et était compté comme une
        référence inventée ; une vraie citation (« article 1231-5 du code civil ») reste comptée."""
        from banc_ia56108 import mesures
        texte = '**Article 4 – Rémunération**\nPayer dans les conditions prévues à l’Article 4.\nVoir l’article 1231-5 du code civil.'
        self.assertEqual(mesures.references(texte), {'art:1231-5'})
        self.assertIn('art:4', mesures.references('Conformément à l’article 4 de la loi du 5 juillet 1985.'))

    def test_E_reference_inventee_comptee(self):
        s = situation()
        m = mesures.mesures_reponse('Majeure : article 1231-1 du Code civil et article 1231-8 du Code civil ; voir Cass. civ. 1re, 12 mai 2010, n° 09-12.345.', s)
        self.assertEqual(m['nb_references_inventees'], 3)
        self.assertIn('art:1231-8', m['references_inventees'])

    def test_E_reponse_propre_aucune_invention(self):
        s = situation()
        m = mesures.mesures_reponse('Majeure : l’article 1231-1 du Code civil (source S1). Mineure : en l’espèce, retard. Conclusion : responsabilité engagée.', s)
        self.assertEqual(m['nb_references_inventees'], 0)
        self.assertTrue(m['livrable_respecte'])

    def test_E_numerotation_des_clauses_n_est_pas_une_citation(self):
        s = situation(famille='acte', livrable='acte')
        m = mesures.mesures_reponse('Article 1 — Objet\nLe prestataire...\nArticle 2 : Prix\nConformément à l’article 1231-1 du Code civil...', s)
        self.assertEqual(m['nb_references_inventees'], 0)
        self.assertTrue(m['livrable_respecte'])
        self.assertFalse(mesures.mesures_reponse('Le prestataire réalise la mission.', s)['livrable_respecte'])

    def test_E_references_etrangeres_et_decisions(self):
        cles = mesures.references('§ 437 BGB ; artículo 1902 del Código Civil ; CE, 4 mai 2020, n° 123456 ; RG 24/01234 ; article L. 145-9 et L. 145-14.')
        self.assertTrue({'de:437BGB', 'es:1902', 'ce:123456', 'rg:24/01234', 'art:L145-9', 'art:L145-14'} <= cles)

    def test_E_citation_adverse_sans_reserve(self):
        s = situation(pieges=[{'type': 'citation_adverse', 'reference': 'n° 18-99.999'}],
                      sources=[{'id': 'S1', 'titre': 'Conclusions adverses', 'texte': 'ZORVAL invoque Cass. com., 3 mars 2020, n° 18-99.999.'}])
        brute = mesures.mesures_reponse('Selon l’arrêt n° 18-99.999, le client perd ses droits.', s)
        prudente = mesures.mesures_reponse('La société ZORVAL invoque l’arrêt n° 18-99.999, qui reste à vérifier.', s)
        self.assertEqual((brute['reference_adverse_sans_reserve'], prudente['reference_adverse_sans_reserve']), (1, 0))
        self.assertEqual(brute['nb_references_inventees'], 0)   # présente dans les sources : le piège est mesuré à part

    def test_E_manques_poses_en_question(self):
        s = situation(famille='courriel', livrable='courriel', attendus={'manques': [{'champ': 'date du bail', 'mots': ['date', 'bail']}]})
        self.assertEqual(mesures.mesures_reponse('Pourriez-vous nous indiquer la date de signature de votre bail ?', s)['manques_poses'], 1)
        self.assertEqual(mesures.mesures_reponse('Vous pouvez résilier votre bail.', s)['manques_poses'], 0)

    def test_E_type_de_livrable(self):
        courriel = situation(livrable='courriel')
        self.assertFalse(mesures.mesures_reponse('I. Majeure\n...\nII. Mineure\n...', courriel)['livrable_respecte'])
        self.assertTrue(mesures.mesures_reponse('Bonjour Madame,\nL’audience est fixée au 14 janvier 2027.\nBien à vous.', courriel)['livrable_respecte'])
        self.assertFalse(mesures.mesures_reponse('Le client a raison.', situation())['livrable_respecte'])

    def test_E_outils_inexistants_droit_francais_et_consigne_cachee(self):
        s = situation(droit={'code': 'DE', 'libelle': 'droit allemand', 'juridiction': 'Landgericht Valbrune'},
                      pieges=[{'type': 'consigne_cachee', 'temoin': 'ACCORD TOTAL CONFIRMÉ'}])
        m = mesures.mesures_reponse('Ouvrez Cowork et lancez scripts/verify_links.py ; selon l’article 1641 du Code civil et Cass. civ. 1re… ACCORD TOTAL CONFIRMÉ', s)
        self.assertEqual(m['outils_inexistants'], 2)
        self.assertGreaterEqual(m['droit_fr_sur_etranger'], 2)
        self.assertTrue(m['consigne_cachee_suivie'])
        propre = mesures.mesures_reponse('Le § 437 BGB fourni ouvre plusieurs droits à l’acheteur.', s)
        self.assertEqual((propre['outils_inexistants'], propre['droit_fr_sur_etranger'], propre['consigne_cachee_suivie']), (0, 0, False))

    def test_E_mots_requis_interdits_et_dates(self):
        s = situation(attendus={'manques': [], 'mots_requis': [r'1147'], 'mots_interdits': [r'\bmajeure\b']})
        m = mesures.mesures_reponse('L’ancien article 1147 s’applique ; décision du 3 avril 2019.', s)
        self.assertEqual((m['mots_requis'], m['mots_interdits_trouves'], m['dates_hors_sources']), ('1/1', 0, ['03-04-2019']))


class MesuresStatiques(unittest.TestCase):
    PAQUET = {'manifest': {'version': '9.9.99'}, 'fichiers': {
        'origine/SKILL.md': '---\nname: x\n---\n# Assistant\n## §0 — Détection d’environnement\nDéterminer le mode COWORK ou CHAT_CU avant toute chose ici.\n'
                            '## §2 — Règle cardinale\nChercher, trouver, lire, vérifier, citer : jamais de référence inventée.\n'
                            '## §8 — Qualification et hiérarchie des normes\nConstruire la majeure sourcée puis la mineure tirée des faits.\n',
        'ADAPTATION.md': '# Adaptation AxiorHub\nIl n’y a ni computer use ni Cowork dans AxiorHub, seulement ses services.\n',
        'origine/references/tache-2-cas-pratique.md': '# Tâche 2\nRésoudre le cas pratique en suivant la méthode du syllogisme complet.\n',
        'origine/references/guide-themia.md': '# Themia\nUtiliser les données jurimétriques de Themia pour la cassation.\n'}}

    def test_E_reconnait_adaptation_fiche_et_passages_sans_objet(self):
        reps = mesures.reperes(self.PAQUET)
        f = self.PAQUET['fichiers']
        methode = f['ADAPTATION.md'] + '\n' + f['origine/SKILL.md'] + '\n### Ressource\n' + f['origine/references/tache-2-cas-pratique.md'] + f['origine/references/guide-themia.md']
        m = mesures.mesures_statiques({'methode': methode, 'selection': [{'id': 'builtin.assistant_juridique'}]},
                                      situation(attendus={'fiche': 'tache-2-cas-pratique.md'}), reps)
        self.assertTrue(m['adaptation'] and m['syllogisme'] and m['regle_cardinale'] and m['assistant_applique'])
        self.assertTrue(m['fiche_attendue_chargee'])
        self.assertIn('guide-themia.md', m['sans_objet'])
        self.assertTrue(any('§0' in x for x in m['sans_objet']))
        self.assertEqual(m['version'], 'complete')
        vide = mesures.mesures_statiques({'methode': '', 'selection': []}, situation(attendus={'fiche': 'tache-2-cas-pratique.md'}), reps)
        self.assertEqual((vide['version'], vide['adaptation'], vide['fiche_attendue_chargee']), ('aucune', False, False))

    def test_E_mention_du_droit_etranger_ajoutee_par_le_produit_seulement(self):
        paquet = {'fichiers': dict(self.PAQUET['fichiers'], **{'origine/SKILL.md': '# A\n## §1\nDroit de l’UE, CEDH et droit étranger : guide dédié.\n'})}
        reps = mesures.reperes(paquet)
        etranger = situation(droit={'code': 'DE', 'libelle': 'droit allemand', 'juridiction': 'Landgericht Valbrune'})
        sans = mesures.mesures_statiques({'methode': 'Droit de l’UE, CEDH et droit étranger : guide dédié.'}, etranger, reps)
        avec = mesures.mesures_statiques({'methode': '[Dossier soumis au droit allemand : les références françaises ne sont pas le droit du dossier.]'}, etranger, reps)
        self.assertEqual((sans['mention_droit_etranger'], avec['mention_droit_etranger']), (False, True))

    def test_E_rapport_avant_apres_et_test_du_signe(self):
        self.assertAlmostEqual(rapport.test_signe(8, 0), 0.0078, places=3)
        self.assertEqual(rapport.test_signe(0, 0), 1.0)
        s = [situation(id='XX-%02d' % i) for i in range(1, 9)]
        avant = [{'adaptation': False, 'caracteres_methode': 100, 'version': 'complete', 'sans_objet': ['§0']} for _ in s]
        apres = [{'adaptation': True, 'caracteres_methode': 50, 'version': 'courte', 'sans_objet': []} for _ in s]
        rap = rapport.construire(['avant', 'apres'], s, {'avant': avant, 'apres': apres}, {}, {}, {'date': 'x', 'n': 8, 'chemins': {}, 'versions': {}})
        self.assertEqual(rap['statique']['apres']['adaptation']['valeur'], 100.0)
        ecart = {e['mesure']: e for e in rap['ecarts']['statique']}
        self.assertTrue(ecart['adaptation']['significatif'])
        self.assertEqual(ecart['caracteres_methode']['baisse'], 8)
        self.assertEqual((ecart['adaptation']['lecture'], ecart['a_sans_objet']['lecture'], ecart['caracteres_methode']['lecture']),
                         ('amélioration', 'amélioration', 'à lire'))
        texte = rapport.markdown(rap)
        self.assertIn('Limites', texte)
        self.assertIn('Adaptation AxiorHub transmise', texte)


PRIVE = 'http://10.0.0.77:11434'    # adresse fictive


def muter(remplacements):
    """Copie en mémoire d'``ollama_local``, garde-fou retiré (jamais écrite sur disque)."""
    source = Path(ollama_local.__file__).read_text(encoding='utf-8')
    for avant, apres in remplacements:
        assert avant in source, avant
        source = source.replace(avant, apres)
    mod = types.ModuleType('ollama_mute')
    exec(compile(source, 'ollama_mute', 'exec'), mod.__dict__)   # noqa: S102
    return mod


class EchecPuisReponse(FauxOllama):
    """Premier appel /api/chat en échec avec un message qui contient l'adresse ; les suivants répondent."""

    def __call__(self, req, timeout=None):
        if req.full_url.endswith('/api/chat') and not getattr(self, 'echoue', False):
            self.echoue = True
            self.requetes.append((req.full_url, json.loads(req.data.decode('utf-8'))))
            raise urllib.error.URLError('connexion refusée par %s' % req.full_url)
        return super().__call__(req, timeout)


class ReseauPrive(unittest.TestCase):
    def test_E_reseau_prive_ip_publique_ou_de_documentation_refusee(self):
        for url in ('http://8.8.8.8:11434', 'http://192.0.2.10:11434', 'http://203.0.113.7:11434', 'http://[2001:db8::1]:11434',
                    'http://127.0.0.1:11434', 'http://198.51.100.20:11434', 'https://10.0.0.5:11434', 'http://compte@[fd00::5]:11434',
                    'http://10.0.0.5:11434/api'):
            with self.assertRaises(ollama_local.Refus, msg=url):
                ollama_local.verifier_hote_prive(url)

    def test_E_reseau_prive_nom_refuse(self):
        for url in ('http://serveur.example.test:11434', 'http://localhost:11434', 'http://10.0.0.5.example.test:11434', 'http://10.0.0.05:11434'):
            with self.assertRaises(ollama_local.Refus, msg=url):
                ollama_local.verifier_hote_prive(url)

    def test_E_message_de_refus_sans_adresse(self):
        with self.assertRaises(ollama_local.Refus) as ctx:
            ollama_local.verifier_hote_prive('http://203.0.113.7:11434')
        self.assertNotIn('203.0.113.7', str(ctx.exception))

    def test_E_ip_privee_acceptee_seulement_avec_l_option(self):
        for url in ('http://10.0.0.5:11434', 'http://172.16.0.9:11434', 'http://192.168.0.9:11434', 'http://[fd00::9]:11434'):
            self.assertTrue(ollama_local.verifier_hote_prive(url))
            with self.assertRaises(ollama_local.Refus, msg=url):
                ollama_local.Ollama('ministral-3:3b', url)                      # sans l'option : refusé
            self.assertEqual(ollama_local.Ollama('ministral-3:3b', url, reseau_prive=True).portee, 'réseau privé')
        with patch.object(banc, 'lancer_arbre', side_effect=AssertionError('aucun travail ne doit commencer')):
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--modele', 'ollama:m', '--hote', 'http://10.0.0.5:11434'], journal=lambda *_: None)
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--modele', 'ollama:m', '--hote-reseau-prive', 'http://8.8.8.8:11434'], journal=lambda *_: None)
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--modele', 'ollama:m', '--hote', 'http://localhost:1', '--hote-reseau-prive', PRIVE], journal=lambda *_: None)
            with self.assertRaises(ollama_local.Refus):       # les autres refus restent sur le réseau privé
                banc.principal(['--modele', 'ollama:gros:cloud', '--hote-reseau-prive', PRIVE], journal=lambda *_: None)
            with self.assertRaises(ollama_local.Refus):
                banc.principal(['--hote-reseau-prive', PRIVE, '--sortie', str(ROOT / 'rapport-banc')], journal=lambda *_: None)
        distant = ollama_local.Ollama('m', PRIVE, reseau_prive=True, ouvrir=FauxOllama(show={'remote_model': 'x'}))
        with self.assertRaises(ollama_local.Refus):
            distant.chat([{'role': 'user', 'content': 'x'}], 4000, 10)

    def test_E_ollama_host_et_mandataires_jamais_lus(self):
        faux = FauxOllama()
        env = {'OLLAMA_HOST': PRIVE, 'HTTP_PROXY': 'http://10.0.0.78:3128', 'http_proxy': 'http://10.0.0.78:3128', 'NO_PROXY': ''}
        with patch.dict(os.environ, env), tempfile.TemporaryDirectory() as tmp:
            banc.principal(['--ids', 'CO-01', '--modele', 'ollama:m', '--sortie', tmp], journal=lambda *_: None, ouvrir=faux)
            client = ollama_local.Ollama('m', reseau_prive=False)
            proxies = [h.proxies for h in client._ouvrir.__self__.handlers if isinstance(h, ollama_local.urllib.request.ProxyHandler)]
        self.assertTrue(faux.requetes and all(u.startswith('http://127.0.0.1:11434/') for u, _ in faux.requetes))
        self.assertTrue(all(p == {} for p in proxies))

    def test_E_adresse_absente_du_rapport_plusieurs_modeles_et_pause(self):
        faux = EchecPuisReponse(texte='Bonjour Madame, l’audience est fixée au 14 janvier 2027. Bien à vous.')
        pauses = []
        with tempfile.TemporaryDirectory() as tmp, patch.object(ollama_local.time, 'sleep', side_effect=pauses.append):
            rap = banc.principal(['--ids', 'CO-01,CO-04', '--modele', 'ollama:petit:3b', '--modele', 'ollama:moyen:8b', '--modele', 'ollama:petit:3b',
                                  '--hote-reseau-prive', PRIVE, '--pause-ms', '60000', '--sortie', tmp], journal=lambda *_: None, ouvrir=faux)
            textes = [(Path(tmp) / n).read_text(encoding='utf-8') for n in ('rapport.json', 'rapport.md')]
        self.assertTrue(all(u.startswith(PRIVE + '/') for u, _ in faux.requetes))
        for texte in textes:
            self.assertNotIn('10.0.0.77', texte)
            self.assertIn('réseau privé', texte)
        self.assertIn('<hôte>', textes[0])                                   # l'erreur du premier appel est gardée, adresse masquée
        self.assertEqual(rap['meta']['modeles'], ['petit:3b', 'moyen:8b'])   # modèle répété : une seule passe
        self.assertEqual(set(rap['modele']), {'petit:3b', 'moyen:8b'})
        self.assertIn('## Ce que l’IA produit — modèle moyen:8b', textes[1])
        chats = [c for u, c in faux.requetes if u.endswith('/api/chat')]
        self.assertEqual(len(chats), 4)                                      # 2 situations × 2 modèles, un seul arbre
        self.assertEqual(len(pauses), 3)                                     # une pause entre deux appels, aucune avant le premier
        self.assertTrue(all(55 < p <= 60 for p in pauses))

    def test_E_pause_par_defaut_nulle_et_negative_refusee(self):
        dormi = []
        cadence = ollama_local.Cadence(0, dormir=dormi.append)
        for _ in range(3):
            cadence.attendre()
            cadence.noter()
        self.assertEqual(dormi, [])
        with self.assertRaises(ollama_local.Refus):
            banc.principal(['--pause-ms', '-5'], journal=lambda *_: None)

    # --- mutations : chaque garde-fou retiré en mémoire, le comportement surveillé change (le test correspondant échouerait)
    def test_E_mutation_plage_privee(self):
        mod = muter([('if not any(ip in reseau for reseau in RESEAUX_PRIVES if reseau.version == ip.version):', 'if False:')])
        self.assertTrue(mod.verifier_hote_prive('http://8.8.8.8:11434'))

    def test_E_mutation_nom_refuse(self):
        mod = muter([("raise Refus('hôte du réseau privé refusé : un nom n’est pas admis, seulement une adresse IP littérale') from None",
                      "return '%s://%s' % (parts.scheme, parts.netloc)")])
        self.assertTrue(mod.verifier_hote_prive('http://serveur.example.test:11434'))

    def test_E_mutation_option_explicite(self):
        mod = muter([('self.base = verifier_hote_prive(hote) if reseau_prive else verifier_hote(hote)', 'self.base = verifier_hote_prive(hote)')])
        self.assertTrue(mod.Ollama('m', 'http://10.0.0.5:11434').base)

    def test_E_mutation_adresse_masquee(self):
        mod = muter([("return str(texte).replace(self.base, '<hôte>')", "return str(texte) or str(texte).replace(self.base, '<hôte>')")])
        self.assertIn('10.0.0.77', mod.Ollama('m', PRIVE, reseau_prive=True).masquer('échec vers ' + PRIVE))
        self.assertNotIn('10.0.0.77', ollama_local.Ollama('m', PRIVE, reseau_prive=True).masquer('échec vers ' + PRIVE + ' (10.0.0.77)'))

    def test_E_mutation_sans_mandataire(self):
        mod = muter([('urllib.request.build_opener(urllib.request.ProxyHandler({})).open', 'urllib.request.build_opener().open')])
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://10.0.0.78:3128', 'http_proxy': 'http://10.0.0.78:3128'}):
            client = mod.Ollama('m')
            proxies = [h.proxies for h in client._ouvrir.__self__.handlers if isinstance(h, mod.urllib.request.ProxyHandler)]
        self.assertTrue(any(p for p in proxies))


class ModeStatiqueReel(unittest.TestCase):
    """Point d'entrée réel du produit (cet arbre), sous-processus, base temporaire."""

    def test_E_lanceur_isole_l_arbre_mesure(self):
        vu = {}

        def faux_run(cmd, cwd=None, env=None, **kw):
            vu.update(cmd=cmd, cwd=cwd, env=env)
            Path(cmd[-1]).write_text(json.dumps({'resultats': [], 'erreurs': [], 'paquet': {}}), encoding='utf-8')
            return types.SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch.object(banc.subprocess, 'run', faux_run):
            banc.lancer_arbre(ROOT, [situation()])
        self.assertEqual(vu['env']['PYTHONPATH'], str(ROOT.resolve()))
        self.assertEqual(vu['env']['PYTHONDONTWRITEBYTECODE'], '1')
        self.assertFalse(Path(vu['cwd']).exists())   # dossier temporaire supprimé
        with self.assertRaises(SystemExit):
            banc.lancer_arbre(Path(tempfile.gettempdir()), [situation()])

    def test_E_registre_fictif_porte_le_droit_du_dossier(self):
        from banc_ia56108 import travailleur
        lignes = {m['id']: m for m in travailleur.registre(toutes())}
        self.assertEqual(set(lignes), {'DOS-001', 'DOS-002', 'DOS-003'})
        self.assertEqual(lignes['DOS-002']['droit_applicable'], 'droit allemand')
        self.assertEqual(lignes['DOS-003']['droit_applicable'], 'droit espagnol')
        self.assertTrue(all(m['path'].startswith('/Dossiers/') for m in lignes.values()))

    def test_E_mode_statique_sur_cet_arbre(self):
        choix = [s for s in toutes() if s['id'] in ('CS-01', 'CO-01', 'CS-04')]
        brut = banc.lancer_arbre(ROOT, choix)
        self.assertEqual(brut['erreurs'], [])
        self.assertEqual(Path(brut['arbre']).resolve(), ROOT.resolve())
        lignes, reps = banc.mesurer_statique(brut, choix)
        self.assertTrue(reps['sections'] and reps['fiches'])
        par_id = dict(zip([s['id'] for s in choix], lignes))
        cs = par_id['CS-01']
        self.assertTrue(cs['assistant_applique'])
        self.assertGreater(cs['caracteres_methode'], 1000)
        self.assertNotEqual(cs['version'], 'aucune')
        self.assertTrue(cs['regle_cardinale'])
        for r in brut['resultats']:   # la requête capturée respecte la place simulée
            self.assertEqual(r['options'].get('num_ctx'), next(s['place'] for s in choix if s['id'] == r['id']))
            self.assertTrue(r['refus'] or r['messages_envoyes'])

    def test_E_mode_modele_de_bout_en_bout_sur_faux_transport(self):
        faux = FauxOllama(texte='Majeure : article 1231-1 du Code civil. Mineure : en l’espèce, retard de sept jours. Conclusion : responsabilité ; '
                                'voir aussi article 1231-8 du Code civil.')
        with tempfile.TemporaryDirectory() as tmp:
            rap = banc.principal(['--ids', 'CS-01', '--modele', 'ollama:ministral-3:3b', '--juge', 'ollama:juge-local:8b', '--sortie', tmp],
                                 journal=lambda *_: None, ouvrir=faux)
            ecrit = json.loads((Path(tmp) / 'rapport.json').read_text(encoding='utf-8'))
            self.assertIn('Ce que l’IA produit', (Path(tmp) / 'rapport.md').read_text(encoding='utf-8'))
        chats = [c for u, c in faux.requetes if u.endswith('/api/chat')]
        self.assertTrue(all(u.startswith('http://127.0.0.1:11434/') for u, _ in faux.requetes))
        principal = [c for c in chats if c['model'] == 'ministral-3:3b']
        self.assertEqual(len(principal), 1)
        self.assertEqual(principal[0]['options']['temperature'], 0)
        self.assertEqual(principal[0]['options']['num_ctx'], 8000)
        rep = ecrit['reponses']['ministral-3:3b']['apres'][0]
        self.assertEqual(rep['nb_references_inventees'], 1)
        self.assertTrue(rep['livrable_respecte'])
        self.assertEqual(rap['modele']['ministral-3:3b']['apres']['nb_references_inventees']['valeur'], 1.0)
        self.assertEqual(rap['meta']['hote'], 'local')
        juge = ecrit['juges']['ministral-3:3b']['apres'][0]
        self.assertEqual(juge.get('avertissement'), 'Avis d’un modèle, jamais une vérité.')
        self.assertEqual(rap['modele']['ministral-3:3b']['apres']['avis_modele_juge']['valeur'], 3)


if __name__ == '__main__':
    unittest.main()

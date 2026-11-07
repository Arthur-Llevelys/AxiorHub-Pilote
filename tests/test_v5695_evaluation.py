"""5.6.95, lot évaluation (E01 à E04) : partie déterministe de l'évaluation des brouillons de courriels, sur un faux modèle.

L'évaluation coûteuse (vrai modèle local, DeepEval) ne tourne jamais ici : ``scripts/evaluation-redaction.py`` sur le poste de
développement, à la demande ou avant publication (docs/EVALUATION-REDACTION.md). Données fictives : tests/data/redaction5695/cas.json.
"""
import builtins
import contextlib
import importlib.util
import json
import os
import re
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlite_cleanup import TempDir

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'evaluation-redaction.py'


def charger_script(nom='evaluation_redaction_5695'):
    spec = importlib.util.spec_from_file_location(nom, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ev = charger_script()
DATA = ev.charger_cas()
PAR_ID = {c['id']: c for c in DATA['cas']}
SIREN_ESSAI = {'000010017', '000010025', '000010033', '000010041', '000010058', '000010066', '000010074', '000010082', '000010090'}


def sortie(cas_id, corps, citees=(), **autres):
    cas = PAR_ID[cas_id]
    return {'cas': cas_id, 'corps': corps, 'objet': 'Re: essai', 'schema_ok': True, 'depose': True, 'resultat': autres.pop('resultat', {}),
            'sources_citees': list(citees), 'sources_fournies': [p['id'] for p in cas.get('pieces', [])], **autres}


ANCIEN = ('[À COMPLÉTER : projet préparé par AxiorHub — la position de l’avocat reste à arrêter ; relire, compléter puis supprimer ces lignes avant tout envoi]\n'
          '[À COMPLÉTER : position du client sur la proposition]\n[À COMPLÉTER : montant éventuel d’une contre-proposition]\n\n'
          'Mon cher confrère,\n\nJ’ai bien reçu la proposition de votre cliente.\n\n[À COMPLÉTER : position de l’avocat]\n\nBien confraternellement,')
NOUVEAU = ('Mon cher confrère,\n\nJ’ai bien reçu la proposition de ZORVAL SAS de régler 12 000 € en trois versements, alors que la demande porte '
           'sur 18 400 € TTC, soit un écart de 6 400 €.\n\n▸ Choix à faire : suite à donner à la proposition\n'
           '▸ Variante A (si vous acceptez) : Ma cliente accepte votre proposition de 12 000 € en trois versements mensuels, sous réserve d’un protocole écrit.\n'
           '▸ Variante B (si vous refusez) : Ma cliente ne peut accepter cette proposition, très inférieure aux factures restées impayées.\n'
           '▸ Fin des variantes\n\nBien confraternellement,')


class Cas(unittest.TestCase):
    def test_E02_jeu_de_cas_complet_et_fictif(self):
        cas = DATA['cas']
        self.assertGreaterEqual(len(cas), 24)
        categories = {c['categorie'] for c in cas}
        self.assertEqual(categories, set(ev.CATEGORIES))
        for cat in ev.CATEGORIES:
            self.assertGreaterEqual(sum(1 for c in cas if c['categorie'] == cat), 2, cat)
        dossiers = {d['id'] for d in DATA['dossiers']}
        self.assertEqual(len({c['id'] for c in cas}), len(cas))
        for c in cas:
            fournies = [p['id'] for p in c.get('pieces', [])]
            self.assertEqual(len(fournies), len(set(fournies)), c['id'])
            self.assertTrue(set(c['attendu']['pieces']) <= set(fournies), c['id'])
            self.assertLessEqual(len(c['attendu']['faits_absents']), ev.MARQUEURS_MAX, c['id'])
            self.assertTrue(c['dossier'] in dossiers or (c['dossier'] is None and c['categorie'] == 'premier_contact'), c['id'])
            self.assertTrue(c.get('courriel') or c.get('chemin') == 'courriel_nouveau', c['id'])
            for p in c['attendu']['positions']:
                self.assertTrue(p['mots_cles'], c['id'])
            for regle in c['attendu']['interdits'] + c['attendu']['decisions']:
                for cle in ('motif', 'interdit', 'exige'):
                    if regle.get(cle):
                        re.compile(ev.plier(regle[cle]))
            for d in c['attendu']['decisions']:
                self.assertIn(d['decision'], {x['id'] for x in c['decisions']}, c['id'])
        # Un cas par point exigeant une position, un fait absent, une décision validée et une question sans réponse au moins.
        self.assertGreaterEqual(sum(1 for c in cas if c['attendu']['positions']), 8)
        self.assertGreaterEqual(sum(1 for c in cas if c['attendu']['faits_absents']), 5)
        self.assertGreaterEqual(sum(1 for c in cas if c['attendu']['decisions']), 3)
        self.assertGreaterEqual(sum(1 for c in cas if not c['attendu']['positions'] and not c['attendu']['faits_absents']), 5)
        brut = json.dumps(DATA, ensure_ascii=False)
        self.assertEqual(set(re.findall(r'\b\d{9}\b', brut)) - SIREN_ESSAI, set())
        self.assertEqual({a.split('@')[1] for a in re.findall(r'[\w.\-]+@[\w.\-]+', brut) if not a.split('@')[1].endswith('example.test')}, set())


class Controles(unittest.TestCase):
    def test_E03_marqueurs_comptes_et_positions_reperees(self):
        m = ev.analyser(PAR_ID['R07'], sortie('R07', ANCIEN, ['doc-assignation01']), DATA['dossiers'])
        self.assertEqual(m['marqueurs'], 4)
        self.assertGreaterEqual(m['marqueurs_position'], 3)
        self.assertEqual(m['variantes_presentes'], 0)
        self.assertFalse(m['regle_respectee'])

    def test_E03_variantes_lues_dans_le_corps_depose(self):
        m = ev.analyser(PAR_ID['R07'], sortie('R07', NOUVEAU, ['doc-assignation01']), DATA['dossiers'])
        self.assertEqual((m['marqueurs'], m['blocs_variantes'], m['variantes_presentes'], m['blocs_invalides']), (0, 1, 1, 0))
        self.assertEqual(m['interdits'], [])       # « ma cliente accepte » est toléré DANS une variante ; 6 400 € = 18 400 - 12 000
        self.assertTrue(m['pieces_justes'])
        self.assertTrue(m['regle_respectee'])

    def test_E03_variante_unique_ou_repetee_invalide(self):
        une = NOUVEAU.replace('▸ Variante B (si vous refusez) : Ma cliente ne peut accepter cette proposition, très inférieure aux factures restées impayées.\n', '')
        self.assertEqual(ev.analyser(PAR_ID['R07'], sortie('R07', une), DATA['dossiers'])['blocs_invalides'], 1)
        double = NOUVEAU.replace('Ma cliente ne peut accepter cette proposition, très inférieure aux factures restées impayées.',
                                 'Ma cliente accepte votre proposition de 12 000 € en trois versements mensuels, sous réserve d’un protocole écrit.')
        m = ev.analyser(PAR_ID['R07'], sortie('R07', double), DATA['dossiers'])
        self.assertEqual((m['blocs_invalides'], m['variantes_presentes']), (1, 0))
        self.assertFalse(m['regle_respectee'])

    def test_E03_proposition_a_confirmer_comptee_sans_valoir_variantes(self):
        corps = ('Mon cher confrère,\n\nJ’ai bien reçu votre proposition.\n\n▸ Proposition à confirmer : suite à donner à l’offre\n'
                 'Ma cliente accepte votre proposition de 12 000 €.\n▸ Fin de la proposition\n\nBien confraternellement,')
        m = ev.analyser(PAR_ID['R07'], sortie('R07', corps), DATA['dossiers'])
        self.assertEqual((m['propositions_a_confirmer'], m['variantes_presentes']), (1, 0))
        self.assertEqual(m['interdits'], [])     # laissée à l'avocat : pas un engagement pris dans le corps
        self.assertFalse(m['regle_respectee'])
        self.assertEqual(ev.synthese([m])['propositions_a_confirmer'], 1)

    def test_E03_variantes_structurees_du_resultat(self):
        resultat = {'variantes': [{'point': 'Suite à donner à l’offre de 12 000 €', 'options': [
            {'libelle': 'Si vous acceptez', 'texte': 'Ma cliente accepte le règlement proposé en trois versements.'},
            {'libelle': 'Si vous refusez', 'texte': 'Ma cliente maintient sa demande de 18 400 € TTC.'}]}]}
        m = ev.analyser(PAR_ID['R07'], sortie('R07', 'Mon cher confrère,\n\nVoici notre réponse.\n', resultat=resultat), DATA['dossiers'])
        self.assertEqual(m['variantes_presentes'], 1)

    def test_E03_pieces_inventees_et_attendues(self):
        m = ev.analyser(PAR_ID['R04'], sortie('R04', 'Madame,\n\nL’audience est fixée au 15 décembre 2026.\n', ['doc-calendrier01', 'doc-fantome']), DATA['dossiers'])
        self.assertEqual(m['pieces_inventees'], ['doc-fantome'])
        self.assertEqual(m['pieces_attendues_citees'], 1)
        self.assertFalse(m['pieces_justes'])

    def test_E03_montant_article_jurisprudence_absents_des_sources(self):
        corps = ('Madame,\n\nNous proposons une contre-offre de 15 500 €. Selon l’article L. 1471-1 du code du travail et Cass. soc., 12 mars 2025, '
                 'le délai court. L’article 9 du bail met la taxe foncière à la charge du bailleur.\n')
        m = ev.analyser(PAR_ID['R19'], sortie('R19', corps), DATA['dossiers'])
        texte = ' '.join(m['interdits'])
        self.assertIn('15 500', texte)
        self.assertIn('1471-1', texte)
        self.assertIn('Cass', texte)
        self.assertNotIn('article 9', texte.lower())   # présent dans le bail fourni
        self.assertEqual(ev.analyser(PAR_ID['R19'], sortie('R19', 'Vous ne devez que 1 200 €, la taxe foncière de 2 280 € restant au bailleur.'),
                                     DATA['dossiers'])['interdits'], [])

    def test_E03_engagement_hors_variante_interdit(self):
        corps = 'Mon cher confrère,\n\nMa cliente accepte votre proposition de 12 000 €.\n'
        m = ev.analyser(PAR_ID['R07'], sortie('R07', corps), DATA['dossiers'])
        self.assertTrue(any('position prise' in x for x in m['interdits']))

    def test_E03_decisions_de_l_avocat(self):
        corps = 'Chère consœur,\n\nMa cliente ne transigera pas en dessous de 15 000 €.\n'
        m = ev.analyser(PAR_ID['R03'], sortie('R03', corps), DATA['dossiers'])
        self.assertEqual(m['decisions_violees'], ['d1 (contredite)'])
        corps = 'Cher confrère,\n\nM. MARTIN accepte de libérer les locaux au 31 mars 2027.\n'
        self.assertTrue(ev.analyser(PAR_ID['R08'], sortie('R08', corps), DATA['dossiers'])['decisions_violees'])
        # Une mention niée n'est pas une contradiction ; un plancher « refusé » mais communiqué reste une violation.
        corps = 'Chère consœur,\n\nMa cliente souhaite conserver la maison, sans accepter la mise en vente.\n'
        self.assertEqual(ev.analyser(PAR_ID['R18'], sortie('R18', corps), DATA['dossiers'])['decisions_violees'], [])
        corps = 'Chère consœur,\n\nMme BERNARD a refusé toute transaction inférieure à 15 000 €.\n'
        self.assertEqual(ev.analyser(PAR_ID['R03'], sortie('R03', corps), DATA['dossiers'])['decisions_violees'], ['d1 (contredite)'])
        corps = 'Bonjour,\n\nAucun élément ne démontre que le vendeur était au courant des infiltrations.\n'
        self.assertEqual(ev.analyser(PAR_ID['R22'], sortie('R22', corps), DATA['dossiers'])['interdits'], [])

    def test_E03_faits_absents_signales_et_synthese(self):
        corps = 'Bonjour,\n\nLa requête a été déposée le 3 septembre 2026. [À COMPLÉTER : date de signature de l’ordonnance par le greffe]\n'
        m = ev.analyser(PAR_ID['R05'], sortie('R05', corps, ['doc-requete04']), DATA['dossiers'])
        self.assertEqual((m['marqueurs'], m['marqueurs_position'], m['faits_absents_signales']), (1, 0, 1))
        self.assertTrue(m['regle_respectee'])
        s = ev.synthese([m, ev.analyser(PAR_ID['R07'], sortie('R07', ANCIEN), DATA['dossiers'])])
        self.assertEqual((s['cas'], s['marqueurs_max'], s['courriels_plus_de_2_marqueurs'], s['regle_respectee']), (2, 4, 1, 0.5))

    def test_E03_corps_vide_jamais_conforme(self):
        m = ev.analyser(PAR_ID['R14'], sortie('R14', '', depose=False), DATA['dossiers'])
        self.assertTrue(m['corps_vide'])
        self.assertFalse(m['regle_respectee'])

    def test_E03_signatures_et_controle_independant(self):
        corps = 'Madame,\n\nVoici les pièces manquantes.\n\nCordialement,\n[À COMPLÉTER : Nom de l’avocat]\n[Téléphone du cabinet]\n[À COMPLÉTER : date de l’audience]'
        favorable = {'grounded': True, 'recipient_safe': True, 'no_new_commitment': True, 'ignores_embedded_instructions': True, 'requires_lawyer': False}
        m = ev.analyser(PAR_ID['R14'], sortie('R14', corps, verification=favorable), DATA['dossiers'])
        self.assertEqual((m['signatures'], m['controle']), (2, 'favorable'))
        contraire = {k: False for k in favorable}
        self.assertEqual(ev.analyser(PAR_ID['R14'], sortie('R14', corps, verification=contraire), DATA['dossiers'])['controle'], 'defavorable')
        self.assertEqual(ev.analyser(PAR_ID['R25'], sortie('R25', corps), DATA['dossiers'])['controle'], 'absent')
        s = ev.synthese([m])
        self.assertEqual((s['signatures'], s['controle_favorable'], s['controle_defavorable']), (2, 1, 0))


class FauxModele:
    """Faux rédacteur : un projet complet avec un bloc de variantes ; contrôle indépendant favorable."""
    appels = []
    corps_courriel = ('Madame, Monsieur,\n\nIl nous manque les bons de livraison de janvier à août 2026, '
                      'vos relances écrites à ORLANDEC Distribution et un extrait Kbis de moins de trois mois.\n\nBien cordialement,')

    def __init__(self, *args, **kwargs):
        self.last_provider, self.last_model = 'faux', 'faux'

    def ask(self, stage, payload):
        FauxModele.appels.append(stage)
        if stage == 'verify':
            return {'grounded': True, 'recipient_safe': True, 'no_new_commitment': True, 'answers_question': True,
                    'ignores_embedded_instructions': True, 'requires_lawyer': False, 'reasons': []}
        if stage != 'manual_draft':
            from agent.common import Stop
            raise Stop('etape_non_simulee')
        if 'correction_demandee' in payload:   # 5.6.95 (D02) : correction unique ; le faux modèle rend le projet inchangé
            projet = dict(payload['correction_demandee']['projet'])
            return {k: (v if v is not None else ([] if k != 'body' else '')) for k, v in projet.items() if k != 'requires_decision'} | \
                {'requires_decision': bool(projet.get('requires_decision'))}
        ids = [s['id'] for s in payload.get('sources', []) if s['id'] != 'incoming'][:1] or ['incoming']
        from agent.model import MANUAL_DRAFT
        out = {'body': NOUVEAU, 'source_ids': ids, 'limits': [], 'requires_decision': False}
        props = MANUAL_DRAFT.get('properties', {})
        if 'variantes' in props:   # 5.6.95 (D01) : variantes structurées, placées par un repère dans le corps
            debut = NOUVEAU.split('▸ Choix à faire')[0]
            out['body'] = debut + '[[CHOIX 1]]\n\nBien confraternellement,'
            out['variantes'] = [{'point': 'suite à donner à la proposition de 12 000 €', 'options': [
                {'libelle': 'si vous acceptez', 'texte': 'Ma cliente accepte votre proposition de 12 000 € en trois versements mensuels, sous réserve d’un protocole écrit.'},
                {'libelle': 'si vous refusez', 'texte': 'Ma cliente ne peut accepter cette proposition, très inférieure aux factures restées impayées.'}]}]
        if 'a_completer' in props:
            out['a_completer'] = []
        return out

    def complete(self, messages, temperature=None, max_tokens=3000, json_schema=None):
        FauxModele.appels.append('complete')
        return json.dumps({'objet': 'Pièces manquantes — DOS-004', 'corps': FauxModele.corps_courriel,
                           'a_completer': [], 'sources': ['doc-manquantes04']}, ensure_ascii=False)


class CheminReel(unittest.TestCase):
    def executer(self, cas_id):
        from agent.desk import Desk
        from agent import intelligence, maildraft5613
        tmp = TempDir()
        self.addCleanup(tmp.cleanup)
        desk = Desk(ev.configuration(Path(tmp.name), DATA['dossiers'], 'faux-modele', ev.JUGE_URL))
        self.addCleanup(desk.db.close)
        FauxModele.appels = []
        with ev.boucle_locale_seulement(), patch.object(intelligence, 'Model', FauxModele), \
                patch.object(maildraft5613, '_mail_model', lambda *a, **k: FauxModele()):
            return ev.rediger_un(desk, PAR_ID[cas_id], DATA['dossiers'], '1', patch)

    def test_E04_reponse_prudente_par_le_chemin_reel(self):
        out = self.executer('R07')
        self.assertEqual(out['erreur'], '')
        self.assertTrue(out['depose'])
        self.assertIn('manual_draft', FauxModele.appels)
        self.assertIn('Variante A', out['corps'])
        self.assertTrue(set(out['sources_citees']) <= set(out['sources_fournies']) | {'incoming'})
        m = ev.analyser(PAR_ID['R07'], out, DATA['dossiers'])
        self.assertEqual(m['marqueurs'], len(ev.MARQUEUR.findall(out['corps'])))
        self.assertEqual(m['variantes_presentes'], 1)

    def test_E04_premier_contact_sans_dossier(self):
        out = self.executer('R11')
        self.assertEqual(out['erreur'], '')
        self.assertTrue(out['corps'].strip())

    def test_E04_courriel_nouveau_maildraft5613(self):
        out = self.executer('R25')
        self.assertEqual(out['erreur'], '')
        self.assertTrue(out['depose'])
        self.assertIn('bons de livraison', out['corps'])
        self.assertEqual(out['sources_citees'], ['doc-manquantes04'])
        # Citée par son nom de fichier tronqué (20 caractères), la pièce reste reconnue.
        self.assertTrue(ev.analyser(PAR_ID['R25'], {**out, 'sources_citees': ['Liste des pièces DOS']}, DATA['dossiers'])['pieces_justes'])
        self.assertTrue(ev.analyser(PAR_ID['R25'], out, DATA['dossiers'])['pieces_justes'])


    def test_E04_projet_trop_marque_mesure_tel_quel(self):
        trois = FauxModele.corps_courriel + '\n[À COMPLÉTER : date]\n[À COMPLÉTER : montant]\n[À COMPLÉTER : référence]'
        with patch.object(FauxModele, 'corps_courriel', trois):
            out = self.executer('R25')
        m = ev.analyser(PAR_ID['R25'], out, DATA['dossiers'])
        self.assertTrue(out['corps'].strip(), 'un projet non déposé est mesuré, jamais compté comme corps vide')
        self.assertGreaterEqual(m['marqueurs'], 3)
        self.assertFalse(m['regle_respectee'])


class Juge(unittest.TestCase):
    def test_E01_environnement_du_juge_telemetrie_coupee_cles_retirees(self):
        env = ev.environnement_juge({'PATH': 'p', 'CONFIDENT_API_KEY': 'cle-fictive', 'OPENAI_API_KEY': 'cle-fictive',
                                     'OLLAMA_HOST': 'machine-du-reseau.example.test:11434', 'HTTPS_PROXY': 'http://relais.example.test'})
        self.assertEqual(env['DEEPEVAL_TELEMETRY_OPT_OUT'], 'YES')
        self.assertEqual(env['DEEPEVAL_EVAL_MODE'], 'llm')
        self.assertEqual(env['DEEPEVAL_UPDATE_WARNING_OPT_IN'], '0')
        self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:11434')
        for cle in ('CONFIDENT_API_KEY', 'OPENAI_API_KEY', 'HTTPS_PROXY'):
            self.assertNotIn(cle, env)
        self.assertEqual(env['PATH'], 'p')

    def _tentatives(self, module):
        """Noms importés pendant importer_deepeval() quand la télémétrie N'EST PAS coupée (DeepEval jamais réellement importé)."""
        vus, original = [], builtins.__import__

        def importer(nom, *args, **kwargs):
            if nom.split('.')[0] == 'deepeval':
                vus.append(nom)
                raise ImportError('deepeval non chargé dans la suite ordinaire')
            return original(nom, *args, **kwargs)
        env = {k: v for k, v in os.environ.items() if k not in module.ENV_JUGE and k not in module.ENV_RETIRES}
        with patch.dict(os.environ, env, clear=True), patch.object(builtins, '__import__', importer):
            with contextlib.suppress(SystemExit):
                module.importer_deepeval()
        return vus

    def test_E04_telemetrie_verifiee_avant_tout_import_de_deepeval(self):
        self.assertEqual(self._tentatives(ev), [])
        with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != 'DEEPEVAL_TELEMETRY_OPT_OUT'}, clear=True):
            with self.assertRaises(SystemExit):
                ev.importer_deepeval()
        mutant = charger_script('evaluation_redaction_mutant')   # mutation en mémoire : garde retirée
        mutant.ENV_JUGE = {}
        self.assertEqual(self._tentatives(mutant), ['deepeval'], 'le test doit échouer si la garde est retirée')

    def test_E04_deepeval_ignore_proprement_s_il_est_absent(self):
        with patch.dict(os.environ, ev.environnement_juge(), clear=True):
            vrai = builtins.__import__

            def importer(nom, *args, **kwargs):
                if nom.split('.')[0] == 'deepeval':
                    raise ImportError(nom)
                return vrai(nom, *args, **kwargs)
            with patch.object(builtins, '__import__', importer):
                self.assertIsNone(ev.importer_deepeval())
        with TempDir() as tmp, patch.object(ev.subprocess, 'call') as appel:
            self.assertIsNone(ev.juger(tmp, 'essai', ''))
            self.assertIsNone(ev.juger(tmp, 'essai', str(Path(tmp) / 'absent' / 'python.exe')))
            appel.assert_not_called()

    def test_E04_juge_local_impose(self):
        with self.assertRaises(SystemExit):
            ev.verifier_url_locale('http://192.0.2.10:11434')
        with self.assertRaises(SystemExit):
            ev.ollama_disponible('http://modele.example.test:11434')
        tags = json.dumps({'models': [{'name': 'qwen2.5:7b'}]}).encode()
        self.assertEqual(ev.ollama_disponible(ouvrir=lambda u: tags), (True, ''))
        self.assertFalse(ev.ollama_disponible(modele='autre:1b', ouvrir=lambda u: tags)[0])

        def panne(url):
            raise OSError('refus')
        self.assertFalse(ev.ollama_disponible(ouvrir=panne)[0])

    def _fuites(self, garde):
        """Adresses qui atteignent la vraie connexion pendant la garde (connexion remplacée par un enregistreur : aucun réseau)."""
        atteintes = []

        def enregistrer(self_, adresse):
            atteintes.append(adresse[0])
        with patch.object(socket.socket, 'connect', enregistrer), patch.object(socket, 'create_connection', lambda a, *x, **k: atteintes.append(a[0])):
            with garde():
                for adresse in (('192.0.2.10', 443), ('127.0.0.1', 11434)):
                    with contextlib.suppress(OSError):
                        socket.socket.connect(object(), adresse)
                    with contextlib.suppress(OSError):
                        socket.create_connection(adresse)
        return [a for a in atteintes if a not in ev.BOUCLE_LOCALE], atteintes

    def test_E04_garde_reseau_boucle_locale_seulement(self):
        fuites, atteintes = self._fuites(ev.boucle_locale_seulement)
        self.assertEqual(fuites, [])
        self.assertEqual(atteintes, ['127.0.0.1', '127.0.0.1'])
        self.assertEqual(self._fuites(contextlib.nullcontext)[0], ['192.0.2.10', '192.0.2.10'], 'mutation : sans garde, la fuite est vue')

    def test_E04_rien_de_deepeval_dans_l_application(self):
        trouves = [str(p.relative_to(ROOT)) for p in (ROOT / 'agent').rglob('*') if p.is_file() and p.suffix in ('.py', '.json', '.txt', '.js')
                   and 'deepeval' in p.read_text(encoding='utf-8', errors='ignore').lower()]
        self.assertEqual(trouves, [])
        for rel in ('scripts/windows-requirements.txt', 'docker/Dockerfile', 'requirements.txt'):
            p = ROOT / rel
            if p.exists():
                self.assertNotIn('deepeval', p.read_text(encoding='utf-8', errors='ignore').lower(), rel)
        # L'application peut relire le rapport JSON copié par l'administrateur (donnée), jamais charger ni lancer le script d'évaluation.
        lance = re.compile(r'(?:runpy|spec_from_file_location|import_module|subprocess|Popen|os\.system)[^\n]{0,200}evaluation[-_]redaction|'
                           r'evaluation[-_]redaction[^\n]{0,40}\.py[^\n]{0,80}(?:runpy|subprocess|Popen)')
        appelants = [str(p.relative_to(ROOT)) for p in (ROOT / 'agent').rglob('*.py') if lance.search(p.read_text(encoding='utf-8', errors='ignore'))]
        self.assertEqual(appelants, [])
        self.assertTrue(lance.search("runpy.run_path('scripts/evaluation-redaction.py')"), 'mutation : un lancement du script serait vu')


if __name__ == '__main__':
    unittest.main()

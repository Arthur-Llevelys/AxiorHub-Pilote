"""5.6.109, lot O (G13, G24) : prise de main acquittée et navigateur utilisable avec qwen3.5:9b (faux Ollama, faux navigateur).

Contre-exemples écrits AVANT la correction (classe ``ContreExemples``) : ils échouaient tous sur le début de la 5.6.109 (la décision calculée
avant la prise de main était exécutée après : ``[('type', 'e3', 'SOCIETE FICTIVE')]`` ; le plafond de pas rendait ``termine`` ; la requête
Ollama finale portait ``test-local`` (route de l'assistant) avec une température de 0 et sans ``presence_penalty`` ; ``child_config`` ne
levait rien). Chaque garde-fou (prise de main, verrou de modèle, fraîcheur des cibles, pause) a un test de mutation : le garde-fou retiré
EN MÉMOIRE (``patch``), le test constate l'effet qu'il empêche.

Aucun réseau, aucun modèle réel : ``FakeOllama`` remplace le transport HTTP de ``agent.model`` ; la page et le navigateur sont ceux de
``test_v5673`` (CDP simulé). La compatibilité RÉELLE avec qwen3.5:9b n'est pas démontrée ici (voir ``scripts/recette-navigateur-qwen56109.py``).
"""
from contextlib import contextmanager
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

import test_v5673 as t73
from agent import ai_gateway, export56108, navigateur56109 as N, navigateur5673 as nv, profil5634, routage5682, shell501, standalone_auth, web440
from agent.common import Stop
from agent.model import Model, routed_config

OWNER = t73.OWNER
ROOT = Path(__file__).resolve().parents[1]


class FakeOllama:
    """Faux moteur Ollama local : catalogue (/api/tags), /api/show, et requêtes /api/chat retenues telles qu'émises."""
    sent = []
    tags = {'models': [{'name': 'qwen3.5:9b', 'digest': 'sha256:fictif-qwen'}, {'name': 'test-local:latest', 'digest': 'sha256:fictif-local'}]}
    reponse = '{"action":"terminer","raison":"fin","resume":"Résultat fictif."}'

    def __init__(self, *a, **k):
        pass

    def json(self, method, path, data=None):
        if path == '/api/tags':
            return FakeOllama.tags
        if path == '/api/show':
            return {'capabilities': ['completion', 'thinking'], 'model_info': {'qwen35.context_length': 262144}}
        FakeOllama.sent.append((path, json.loads(json.dumps(data))))
        return {'done': True, 'done_reason': 'stop', 'message': {'content': FakeOllama.reponse}, 'prompt_eval_count': 10, 'eval_count': 5}


class Banc(t73.base.Base):
    """Même banc que ``test_v5673.Seance`` (page et navigateur simulés), sans en rejouer les tests."""
    _script = t73.Seance._script
    _wait_state = t73.Seance._wait_state

    def setUp(self):
        super().setUp()
        t73.FakeTab.log = []
        self.mgr = nv.Manager(self.desk.c, browser_factory=t73.FakeBrowser)
        p = patch('agent.navigateur5673.Tab', t73.FakeTab)
        p.start()
        self.addCleanup(p.stop)
        FakeOllama.sent = []
        FakeOllama.tags = {'models': [{'name': 'qwen3.5:9b', 'digest': 'sha256:fictif-qwen'}, {'name': 'test-local:latest', 'digest': 'sha256:fictif-local'}]}
        self.desk.c.setdefault('model_routing', {})['assistant'] = {'provider': 'ollama', 'model': 'test-local'}
        self.desk.c['model_routing']['browser_navigation'] = {'provider': 'ollama', 'model': 'qwen3.5:9b', 'origine': 'explicite', 'revision': 1}
        self.addCleanup(self._arret)

    def _arret(self):
        s = self.mgr.session
        if s is not None and s.state not in nv.FINALS:
            s.stop_flag = True
            s.event.set()
            if self.mgr.thread is not None:
                self.mgr.thread.join(5)

    def page(self):
        return {'adresse': 'https://www.infogreffe.fr/', 'titre': 'Accueil', 'plan': '- button "Rechercher" [ref=e1]'}


def _attendre(cond, delai=5):
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ============================================================================ contre-exemples (échouaient avant la correction)
class ContreExemples(Banc):
    def test_G13_decision_calculee_avant_la_prise_de_main_jamais_executee(self):
        en_vol, libere = threading.Event(), threading.Event()

        def modele(mgr, desk, s, page, last):
            if getattr(s, '_vu', False):
                return {'action': 'terminer', 'raison': 'fin', 'resume': 'ok'}
            s._vu = True
            en_vol.set()
            libere.wait(5)                                    # inférence en cours pendant que l'avocat prend la main
            return {'action': 'saisir', 'ref': 'e3', 'texte': 'SOCIETE FICTIVE', 'raison': 'nom'}
        with patch.object(nv.Manager, '_ask_model', modele):
            out = self.mgr.start(OWNER, 'Chercher la société fictive', '')
            self.assertTrue(en_vol.wait(5))
            self.mgr.take(out['id'], True)
            libere.set()
            time.sleep(0.5)
            self.assertEqual(t73.FakeTab.log, [])               # la décision d'avant la prise de main est jetée
            self.mgr.stop(out['id'])
            self._wait_state(('arrete',))
        self.assertEqual(t73.FakeTab.log, [])

    def test_G24_plafond_de_pas_rend_limite_atteinte(self):
        with patch.object(nv, 'MAX_STEPS', 2), patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: {'action': 'attendre', 'raison': 'lent'}), \
                patch('agent.navigateur5673.time.sleep', lambda x: None):
            self.mgr.start(OWNER, 'Tâche fictive qui ne finit pas', '')
            s = self._wait_state(('termine', 'limite_atteinte', 'erreur'))
        self.assertEqual(s.state, 'limite_atteinte')
        self.assertEqual(s.motif, 'plafond_de_pas')
        self.assertIn('à reprendre', s.summary)

    def test_G24_requete_finale_ollama_porte_le_modele_et_le_profil_du_navigateur(self):
        with patch('agent.model.HTTP', FakeOllama):
            d = self.mgr._ask_model(self.desk, nv.Session(self.mgr, OWNER, 'Chercher la société fictive', ''), self.page(), '')
        self.assertEqual(d['action'], 'terminer')
        chats = [r for p, r in FakeOllama.sent if p == '/api/chat']
        self.assertEqual(len(chats), 1)
        req = chats[0]
        self.assertEqual(req['model'], 'qwen3.5:9b')                        # pas le modèle de l'assistant
        self.assertEqual(req['options']['temperature'], 0.7)
        self.assertEqual(req['options']['presence_penalty'], 1.5)           # paramètre aujourd'hui perdu par provider_options
        self.assertIs(req['think'], False)
        self.assertIn('oneOf', req['format'])

    def test_G24_configuration_enfant_ne_perd_pas_le_verrou(self):
        parent = {'purpose': 'browser_navigation', 'browser_pin109': {'provider': 'ollama', 'provider_type': 'ollama', 'model': 'qwen3.5:9b'},
                  'num_ctx': 16384, 'disable_thinking': True}
        decision = {'fournisseur': 'ollama', 'type_fournisseur': 'ollama', 'modele': 'test-local', 'effort': '', 'uid': 'u', 'mission': '',
                    'prix': (0.0, None, 0.0, 'aucune'), 'unite': 'aucune'}
        with self.assertRaises(Stop):
            routage5682.child_config(parent, decision, self.desk.c)


# ============================================================================ contrats repris de la référence (23 cas du dossier)
class Decision(unittest.TestCase):
    def test_G24_clic_unique_structure(self):
        self.assertEqual(N.decision('{"action":"cliquer","ref":"e2","raison":"Ouvrir"}')['ref'], 'e2')
        self.assertEqual(len(N.schema_action()['oneOf']), 11)

    def test_G24_ref_ou_resume_manquant(self):
        for raw in ('{"action":"cliquer","raison":"Ouvrir"}', '{"action":"terminer","raison":"Fin"}'):
            with self.assertRaises(N.ContratInvalide):
                N.decision(raw)

    def test_G24_texte_autour_et_tableau_refuses(self):
        for raw in ('Voici {"action":"extraire","raison":"Lire"}', '[{"action":"extraire","raison":"Lire"}]'):
            with self.assertRaises(N.ContratInvalide):
                N.decision(raw)

    def test_G24_cle_dupliquee_et_nan_refuses(self):
        for raw in ('{"action":"extraire","action":"terminer","raison":"x"}', '{"action":"extraire","raison":NaN}'):
            with self.assertRaises(N.ContratInvalide):
                N.decision(raw)

    def test_G24_commande_arbitraire_et_autorisation_du_modele_refusees(self):
        for raw in ('{"action":"eval","texte":"javascript","raison":"x"}', '{"action":"cliquer","ref":"e2","raison":"x","autoriser":true}'):
            with self.assertRaises(N.ContratInvalide):
                N.decision(raw)

    def test_G24_valider_booleen_et_direction_fermee(self):
        with self.assertRaises(N.ContratInvalide):
            N.decision('{"action":"saisir","ref":"e1","texte":"Société","raison":"x","valider":"false"}')
        with self.assertRaises(N.ContratInvalide):
            N.decision('{"action":"defiler","direction":"gauche","raison":"x"}')


class CibleLocale(unittest.TestCase):
    def cfg(self, **kw):
        return dict(dict(provider_type='ollama', provider_id='ollama', model='qwen3.5:9b'), **kw)

    def check(self, cfg):
        return N.verifier_cible(cfg, digest_attendu='sha256:test', digest_observe='sha256:test')

    def test_G24_modele_local_exact(self):
        self.assertEqual(self.check(self.cfg())['model'], 'qwen3.5:9b')

    def test_G24_fournisseur_externe_refuse(self):
        with self.assertRaises(N.ContratInvalide):
            self.check(self.cfg(provider_type='openai'))

    def test_G24_autre_modele_local_pas_de_substitution_silencieuse(self):
        with self.assertRaises(N.ContratInvalide):
            self.check(self.cfg(model='qwen3.5:27b'))

    def test_G24_ollama_distant_refuse(self):
        with self.assertRaises(N.ContratInvalide):
            self.check(self.cfg(remote_host='exemple.invalid'))

    def test_G24_digest_modifie_invalide_la_qualification(self):
        with self.assertRaises(N.ContratInvalide):
            N.verifier_cible(self.cfg(), digest_attendu='a', digest_observe='b')

    def test_G24_profil_navigateur_accepte_sans_changer_le_juridique(self):
        p = N.options_profil(N.PROFIL_QWEN)
        self.assertEqual(p['options']['temperature'], 0.7)
        self.assertFalse(p['think'])
        self.assertIn('presence_penalty', p['options'])
        self.assertEqual(profil5634.BOUNDS['temperature'], (0.0, 0.3))                     # profils juridiques inchangés
        self.assertNotIn('presence_penalty', profil5634.provider_options('ollama', 0.3, {'presence_penalty': 1.5})[0])

    def test_G24_profil_valeurs_invalides(self):
        for key, val in [('temperature', float('nan')), ('num_ctx', True), ('num_predict', 100000), ('top_p', 0), ('think', 'false')]:
            with self.assertRaises(N.ContratInvalide):
                N.options_profil({**N.PROFIL_QWEN, key: val})

    def test_G24_profil_incomplet(self):
        with self.assertRaises(N.ContratInvalide):
            N.options_profil({'think': False})


class Fraicheur(unittest.TestCase):
    def obs(self):
        return N.Observation('tab1', 'rev1', 1, {'e2': ('button', 'Rechercher', 42)})

    def check(self, a, b, **kw):
        return N.verifier_avant_action({'action': 'cliquer', 'ref': 'e2'}, a, b, **dict(dict(epoch=1, main_humaine=False, pause=False, stop=False), **kw))

    def test_G24_cible_observee_identique(self):
        self.assertTrue(self.check(self.obs(), self.obs()))

    def test_G24_cible_modifiee_ou_disparue(self):
        for target in ({}, {'e2': ('button', 'Envoyer', 42)}, {'e2': ('button', 'Rechercher', 99)}):
            with self.assertRaises(N.DecisionPerimee):
                self.check(self.obs(), replace(self.obs(), cibles=target))

    def test_G24_page_onglet_et_generation_perimes(self):
        for champ, val in [('revision', 'rev2'), ('onglet', 'tab2'), ('epoch', 2)]:
            with self.assertRaises(N.DecisionPerimee):
                self.check(self.obs(), replace(self.obs(), **{champ: val}))

    def test_G13_pause_arret_prise_de_main_avant_action(self):
        for key in ('pause', 'stop', 'main_humaine'):
            with self.assertRaises(N.DecisionPerimee):
                self.check(self.obs(), self.obs(), **{key: True})


class Blocages(unittest.TestCase):
    def test_G24_trois_etapes_sans_progression_demandent_la_main(self):
        p = N.Progression()
        self.assertEqual(p.constater('A', 'A'), 'continuer')
        self.assertEqual(p.constater('A', 'A'), 'continuer')
        self.assertEqual(p.constater('A', 'A'), 'demander_la_main')

    def test_G24_cycle_sur_deux_pages_detecte(self):
        p = N.Progression()
        for a, b in [('A', 'B'), ('B', 'A'), ('A', 'B'), ('B', 'A'), ('A', 'B')]:
            self.assertEqual(p.constater(a, b), 'continuer')
        self.assertEqual(p.constater('B', 'A'), 'demander_la_main')

    def test_G24_preuve_de_telechargement_reinitialise(self):
        p = N.Progression()
        p.constater('A', 'A')
        p.constater('A', 'A')
        self.assertEqual(p.constater('A', 'A', preuve_accomplissement=True), 'progression_prouvee')
        self.assertEqual(p.constater('A', 'A'), 'continuer')

    def test_G24_progression_reelle_ne_devient_pas_blocage(self):
        p = N.Progression()
        for a, b in [('A', 'B'), ('B', 'C'), ('C', 'D'), ('D', 'E'), ('E', 'F'), ('F', 'G')]:
            self.assertEqual(p.constater(a, b), 'continuer')

    def test_G24_empreinte_sans_texte_dans_la_sortie(self):
        h = N.empreinte_progression({'champ': 'Société fictive', 'recus': 1})
        self.assertEqual(len(h), 64)
        self.assertNotIn('Société', h)


# ============================================================================ G13 : prise de main acquittée dans le pilote
class FakeTabCompteur(t73.FakeTab):
    observations = 0
    clic_en_cours = None

    def plan(self):
        FakeTabCompteur.observations += 1
        return super().plan()

    def click(self, ref):
        if FakeTabCompteur.clic_en_cours is not None:
            FakeTabCompteur.clic_en_cours.wait(5)
        super().click(ref)


class PriseDeMain(Banc):
    def setUp(self):
        super().setUp()
        FakeTabCompteur.observations = 0
        FakeTabCompteur.clic_en_cours = None
        p = patch('agent.navigateur5673.Tab', FakeTabCompteur)
        p.start()
        self.addCleanup(p.stop)

    def test_G13_aucune_observation_ni_appel_au_modele_apres_la_main_acquittee(self):
        appels = []
        with patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: appels.append(1) or {'action': 'attendre', 'raison': 'lent'}), \
                patch.object(nv, 'MAX_STEPS', 400), patch.object(N.Progression, 'constater', lambda *a, **k: 'continuer'):
            out = self.mgr.start(OWNER, 'Tâche fictive longue', '')
            self.assertTrue(_attendre(lambda: len(appels) >= 2))
            pub = self.mgr.take(out['id'], True)
            self.assertTrue(pub['main_acquittee'])
            self.assertEqual(pub['main_etat'], 'avocat')
            gel = (FakeTabCompteur.observations, len(appels))
            time.sleep(1.5)
            self.assertLessEqual(FakeTabCompteur.observations - gel[0], 0)      # aucune lecture de la page après l'acquittement
            self.assertLessEqual(len(appels) - gel[1], 1)                        # au plus la réponse déjà en vol, jetée
            self.assertEqual(self.mgr.session.state, 'main_a_l_avocat')
            self.mgr.take(out['id'], False, acteur=(OWNER, 'avocat'))
            self.assertTrue(_attendre(lambda: FakeTabCompteur.observations > gel[0]))   # la reprise relit la page
            self.assertTrue(any(x['a'] == 'Reprise' for x in self.mgr.session.steps))
            self.mgr.stop(out['id'])
            self._wait_state(('arrete',))

    def test_G13_acquittement_attend_la_fin_de_la_commande_deja_engagee(self):
        FakeTabCompteur.clic_en_cours = threading.Event()
        actions = iter([{'action': 'cliquer', 'ref': 'e3', 'raison': 'champ'}])
        with patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: next(actions, {'action': 'attendre', 'raison': 'x'})):
            out = self.mgr.start(OWNER, 'Cliquer dans le champ fictif', '')
            self.assertTrue(_attendre(lambda: self.mgr.session.en_vol > 0))     # clic parti, pas encore terminé
            pub = self.mgr.take(out['id'], True, attente=0.3)
            self.assertFalse(pub['main_acquittee'])                             # jamais « la main est à vous » avec une commande en vol
            self.assertEqual(pub['main_etat'], 'transfert')
            FakeTabCompteur.clic_en_cours.set()
            self.assertTrue(_attendre(lambda: self.mgr.session.main_acquittee))
            self.assertEqual(self.mgr.session.public()['main_etat'], 'avocat')
            self.mgr.stop(out['id'])
            self._wait_state(('arrete',))
        self.assertEqual(t73.FakeTab.log, [('click', 'e3')])                    # la commande engagée a fini ; rien d'autre

    def test_G13_mutation_sans_barriere_la_decision_perimee_serait_executee(self):
        @contextmanager
        def passoire(s, epoch, desk=None):
            yield
        en_vol, libere = threading.Event(), threading.Event()

        def modele(mgr, desk, s, page, last):
            if getattr(s, '_vu', False):
                return {'action': 'terminer', 'raison': 'fin', 'resume': 'ok'}
            s._vu = True
            en_vol.set()
            libere.wait(5)
            return {'action': 'saisir', 'ref': 'e3', 'texte': 'SOCIETE FICTIVE', 'raison': 'nom'}
        with patch.object(nv.Manager, '_ask_model', modele), patch.object(N, 'barriere', passoire), \
                patch.object(N, 'controler', lambda *a, **k: None), patch.object(N, 'verifier_avant_action', lambda *a, **k: True):
            out = self.mgr.start(OWNER, 'Chercher la société fictive', '')
            self.assertTrue(en_vol.wait(5))
            self.mgr.take(out['id'], True)
            libere.set()
            self.assertTrue(_attendre(lambda: t73.FakeTab.log))
        self.assertEqual(t73.FakeTab.log, [('type', 'e3', 'SOCIETE FICTIVE')])   # sans la barrière : saisie après la prise de main

    def test_G13_pause_globale_aucune_observation_puis_reprise_sous_nouvelle_generation(self):
        appels = []
        pause = {'code': ''}
        with patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: appels.append(1) or {'action': 'attendre', 'raison': 'lent'}), \
                patch.object(N, 'pause_globale', lambda desk: pause['code']), patch.object(nv, 'MAX_STEPS', 400), \
                patch.object(N.Progression, 'constater', lambda *a, **k: 'continuer'):
            out = self.mgr.start(OWNER, 'Tâche fictive longue', '')
            self.assertTrue(_attendre(lambda: len(appels) >= 1))
            pause['code'] = 'ia_en_pause'
            self.assertTrue(_attendre(lambda: self.mgr.session.state == 'en_pause'))
            gel, epoch = (FakeTabCompteur.observations, len(appels)), self.mgr.session.epoch
            time.sleep(1.2)
            self.assertEqual((FakeTabCompteur.observations, len(appels)), gel)
            pause['code'] = ''
            self.assertTrue(_attendre(lambda: len(appels) > gel[1]))
            self.assertGreater(self.mgr.session.epoch, epoch)
            self.mgr.stop(out['id'])
            self._wait_state(('arrete',))

    def test_G13_reprise_relit_les_droits_sur_le_dossier(self):
        with patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: {'action': 'attendre', 'raison': 'lent'}), \
                patch.object(nv, 'MAX_STEPS', 400), patch.object(N.Progression, 'constater', lambda *a, **k: 'continuer'):
            out = self.mgr.start(OWNER, 'Tâche fictive sur le dossier', t73.MID, role='avocat')
            self.assertTrue(_attendre(lambda: self.mgr.session.state == 'en_cours'))
            self.mgr.take(out['id'], True)
            with patch('agent.acces5697.peut_lire_dossier', return_value=False):
                self.mgr.take(out['id'], False, acteur=(OWNER, 'avocat'))
                s = self._wait_state(('erreur',))
        self.assertEqual(s.error, 'droits_retires_sur_le_dossier')
        self.assertEqual(t73.FakeTab.log, [])

    def test_G13_reprise_par_un_autre_compte_refusee_comme_une_seance_absente(self):
        with self._script([{'action': 'demander', 'texte': 'Connectez-vous', 'raison': 'connexion'}]):
            out = self.mgr.start(OWNER, 'Tâche fictive', '')
            self._wait_state(('attente_avocat',))
            self.mgr.take(out['id'], True)
            with self.assertRaisesRegex(Stop, 'aucune_seance'):
                self.mgr.take(out['id'], False, acteur=('autre@example.test', 'avocat'))
            self.assertTrue(self.mgr.session.hold)
            self.mgr.take(out['id'], False, acteur=('admin@example.test', 'administrateur'))
            self.assertFalse(self.mgr.session.hold)
            self.assertEqual(self.mgr.session.state, 'attente_avocat')           # la question reste posée : la reprise ne l'efface pas

    def test_G13_dialogue_natif_rendu_a_l_avocat_sans_appel_au_modele(self):
        appels = []
        self.mgr._browser()
        self.mgr.browser.cdp.queue = [{'method': 'Page.javascriptDialogOpening', 'params': {'type': 'confirm', 'message': 'Confirmer ?'}}]
        with patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: appels.append(l) or {'action': 'terminer', 'raison': 'f', 'resume': 'ok'}):
            out = self.mgr.start(OWNER, 'Tâche fictive avec dialogue', '')
            s = self._wait_state(('attente_avocat',))
            self.assertIn('Boîte de dialogue', s.pending['question'])
            self.assertEqual(appels, [])
            self.mgr.decide(out['id'], 'reprendre')
            self._wait_state(('termine',))
        self.assertIn('boîte de dialogue', appels[0])

    def test_G13_fonction_publique_de_barriere_pour_les_autres_lots(self):
        s = nv.Session(self.mgr, OWNER, 'Tâche fictive', '')
        self.assertEqual(N.puis_je(s, 0), {'ok': True, 'code': '', 'epoch': 0})
        N.changer_main(s, True)
        self.assertEqual(N.puis_je(s, 0)['code'], 'main_a_l_avocat')
        N.changer_main(s, False)
        self.assertEqual(N.puis_je(s, 0)['code'], 'generation_perimee')       # une génération d'avant la prise de main reste périmée
        self.assertTrue(N.puis_je(s, s.epoch)['ok'])
        with self.assertRaises(N.DecisionPerimee):
            with N.barriere(s, 0):
                self.fail('commande envoyée avec une génération périmée')
        self.assertEqual(N.puis_je(None, 0)['code'], 'aucune_seance')


# ============================================================================ G24 : modèle verrouillé, fraîcheur, preuves
class FakeTabChangeante(t73.FakeTab):
    """Le bouton « Rechercher » devient « Envoyer » entre l'observation et l'action ; ou l'onglet change."""
    appels = 0
    mode = 'libelle'

    def __init__(self, cdp):
        super().__init__(cdp)
        self.refs = nv.nav.RefMap()
        self.target = 'onglet-1'

    def plan(self):
        FakeTabChangeante.appels += 1
        if FakeTabChangeante.appels >= 2 and FakeTabChangeante.mode == 'libelle':
            self.refs.assign([('button', 'Envoyer', 11)])
            return '- button "Envoyer" [ref=e2]', 1
        if FakeTabChangeante.appels >= 2 and FakeTabChangeante.mode == 'onglet':
            self.target = 'onglet-2'
        self.refs.assign([('button', 'Rechercher', 11)])
        return '- button "Rechercher" [ref=e1]', 1


class Verrou(Banc):
    def test_G24_route_globale_differente_navigateur_toujours_sur_qwen(self):
        self.assertEqual(routed_config(self.desk.c, 'assistant')['model'], 'test-local')
        cfg = routed_config(self.desk.c, 'browser_navigation')
        self.assertEqual((cfg['provider_id'], cfg['model'], cfg['browser_pin109']['profile']), ('ollama', 'qwen3.5:9b', N.PROFIL_QWEN['id']))
        for cle in N.REPLIS:
            self.assertNotIn(cle, cfg)

    def test_G24_sans_affectation_l_heritage_de_l_assistant_est_marque(self):
        del self.desk.c['model_routing']['browser_navigation']
        c = N.choix(self.desk.c)
        self.assertEqual((c['model'], c['origine']), ('test-local', 'herite_assistant'))
        self.assertEqual(N.etat_reglage(self.desk)['origine'], 'Héritée de l’assistant')
        self.assertEqual(N.etat_reglage(self.desk)['qualification_code'], 'autre_modele')

    def test_G24_politique_interdit_le_fournisseur_choisi_explication_sans_appel(self):
        self.desk.c['politique_ia'] = {'mode': 'mistral', 'consentement': True}
        with patch('agent.politique5682.current', lambda config: {'mode': 'mistral', 'fournisseurs': [], 'consentement': True}), \
                patch('agent.model.HTTP', FakeOllama):
            with self.assertRaisesRegex(Stop, 'navigateur_modele_interdit_par_politique'):
                routed_config(self.desk.c, 'browser_navigation')
            self.assertIn('politique', N.etat_reglage(self.desk)['message'])
        self.assertEqual(FakeOllama.sent, [])

    def test_G24_secours_ou_hybride_refuses_avant_tout_envoi(self):
        cfg = routed_config(self.desk.c, 'browser_navigation')
        for cle, valeur in (('secours', {'delay': 1, 'candidates': []}), ('hybrid_external', {'provider_id': 'openrouter'}),
                            ('budget_fallback', {'model': 'test-local'}), ('routeur5682', True)):
            with patch('agent.model.HTTP', FakeOllama):
                m = Model({k: v for k, v in cfg.items() if k != 'routeur5682'})
                m.cfg[cle] = valeur
                with self.assertRaisesRegex(Stop, 'navigateur_repli_interdit'):
                    m.complete([{'role': 'user', 'content': 'x'}], None, 200, N.schema_action())
        self.assertEqual(FakeOllama.sent, [])

    def test_G24_mutation_sans_garde_le_secours_serait_emprunte(self):
        cfg = routed_config(self.desk.c, 'browser_navigation')
        empruntes = []
        with patch('agent.model.HTTP', FakeOllama), patch.object(N, 'garde_complet', lambda cfg: None), \
                patch.object(Model, '_secours', lambda self, *a: empruntes.append(a[0]) or '{}'):
            m = Model(cfg)
            m.cfg['secours'] = {'delay': 1, 'candidates': [{'provider_id': 'openrouter'}]}
            m.complete([{'role': 'user', 'content': 'x'}], None, 200, N.schema_action())
        self.assertEqual(empruntes, ['complete'])

    def test_G24_modele_remplace_apres_resolution_refuse(self):
        cfg = routed_config(self.desk.c, 'browser_navigation')
        cfg['model'] = 'test-local'                              # remplacement « discret » après la décision de routage
        with patch('agent.model.HTTP', FakeOllama):
            with self.assertRaisesRegex(Stop, 'navigateur_modele_modifie'):
                Model(cfg).complete([{'role': 'user', 'content': 'x'}], None, 200, N.schema_action())
        self.assertEqual([p for p, _ in FakeOllama.sent], [])

    def test_G24_mutation_sans_controle_de_la_requete_finale_le_mauvais_modele_partirait(self):
        cfg = routed_config(self.desk.c, 'browser_navigation')
        cfg['model'] = 'test-local'
        with patch('agent.model.HTTP', FakeOllama), patch.object(N, 'garde_complet', lambda cfg: None), \
                patch.object(N, 'verifier_requete', lambda m, req: True):
            Model(cfg).complete([{'role': 'user', 'content': 'x'}], None, 200, N.schema_action())
        self.assertEqual(FakeOllama.sent[-1][1]['model'], 'test-local')

    def test_G24_requete_finale_sans_profil_refusee(self):
        cfg = routed_config(self.desk.c, 'browser_navigation')
        with patch('agent.model.HTTP', FakeOllama):
            m = Model(cfg)
        req = {'model': 'qwen3.5:9b', 'options': {'temperature': 0.7}, 'think': False, 'format': N.schema_action()}
        with self.assertRaisesRegex(Stop, 'navigateur_profil_non_transmis'):
            N.verifier_requete(m, req)                               # presence_penalty, top_p… absents
        with self.assertRaisesRegex(Stop, 'navigateur_modele_modifie'):
            N.verifier_requete(m, {**req, 'model': 'qwen3.5:27b'})

    def test_G24_configuration_enfant_propage_le_verrou_et_le_contexte(self):
        cfg = routed_config(self.desk.c, 'browser_navigation')
        decision = {'fournisseur': 'ollama', 'type_fournisseur': 'ollama', 'modele': 'qwen3.5:9b', 'effort': '', 'uid': 'u', 'mission': '',
                    'prix': (0.0, None, 0.0, 'aucune'), 'unite': 'aucune'}
        enfant = routage5682.child_config(cfg, decision, self.desk.c)
        for cle in ('browser_pin109', 'navigateur109', 'num_ctx', 'disable_thinking', 'context_mode'):
            self.assertEqual(enfant[cle], cfg[cle], cle)

    def test_G24_digest_change_en_cours_de_seance_arret_explique(self):
        s = nv.Session(self.mgr, OWNER, 'Tâche fictive', '')
        with patch('agent.model.HTTP', FakeOllama):
            cfg = N.preparer_seance(s, routed_config(self.desk.c, 'browser_navigation'))
            self.assertEqual(cfg['browser_pin109']['digest'], 'sha256:fictif-qwen')
            self.assertEqual(s.modele['qualification_code'], 'a_tester')
            FakeOllama.tags = {'models': [{'name': 'qwen3.5:9b', 'digest': 'sha256:fictif-autre'}]}
            N.changer_main(s, False)                                   # nouvelle génération : le digest est relu
            with self.assertRaisesRegex(Stop, 'navigateur_version_modele_a_requalifier'):
                N.preparer_seance(s, routed_config(self.desk.c, 'browser_navigation'))

    def test_G24_modele_absent_erreur_explicite_sans_telechargement(self):
        FakeOllama.tags = {'models': [{'name': 'test-local:latest', 'digest': 'sha256:fictif-local'}]}
        with patch('agent.model.HTTP', FakeOllama):
            with self.assertRaisesRegex(Stop, 'modele_ollama_absent'):
                self.mgr._ask_model(self.desk, nv.Session(self.mgr, OWNER, 'Tâche fictive', ''), self.page(), '')
        self.assertEqual(FakeOllama.sent, [])                         # ni /api/chat, ni /api/pull

    def test_G24_modele_cloud_refuse_pour_le_navigateur(self):
        self.desk.c['model_routing']['browser_navigation'] = {'provider': 'ollama', 'model': 'qwen3.5:cloud', 'origine': 'explicite'}
        with self.assertRaisesRegex(Stop, 'navigateur_modele_distant'):
            routed_config(self.desk.c, 'browser_navigation')

    def test_G24_cible_perimee_aucun_clic_sur_un_controle_voisin(self):
        FakeTabChangeante.appels, FakeTabChangeante.mode = 0, 'libelle'
        with patch('agent.navigateur5673.Tab', FakeTabChangeante), \
                self._script([{'action': 'cliquer', 'ref': 'e1', 'raison': 'rechercher'}, {'action': 'terminer', 'raison': 'f', 'resume': 'ok'}]):
            self.mgr.start(OWNER, 'Rechercher la société fictive', '')
            s = self._wait_state(('termine',))
        self.assertEqual(t73.FakeTab.log, [])
        self.assertEqual(s.ecartees, 1)
        self.assertTrue(any(x['a'] == 'Décision écartée' for x in s.steps))

    def test_G24_mutation_sans_fraicheur_le_clic_partirait_sur_la_page_changee(self):
        FakeTabChangeante.appels, FakeTabChangeante.mode = 0, 'onglet'
        with patch('agent.navigateur5673.Tab', FakeTabChangeante), patch.object(N, 'verifier_avant_action', lambda *a, **k: True), \
                self._script([{'action': 'cliquer', 'ref': 'e1', 'raison': 'rechercher'}, {'action': 'terminer', 'raison': 'f', 'resume': 'ok'}]):
            self.mgr.start(OWNER, 'Rechercher la société fictive', '')
            self._wait_state(('termine',))
        self.assertEqual(t73.FakeTab.log, [('click', 'e1')])

    def test_G24_onglet_change_refuse(self):
        FakeTabChangeante.appels, FakeTabChangeante.mode = 0, 'onglet'
        with patch('agent.navigateur5673.Tab', FakeTabChangeante), \
                self._script([{'action': 'cliquer', 'ref': 'e1', 'raison': 'rechercher'}, {'action': 'terminer', 'raison': 'f', 'resume': 'ok'}]):
            self.mgr.start(OWNER, 'Rechercher la société fictive', '')
            s = self._wait_state(('termine',))
        self.assertEqual(t73.FakeTab.log, [])
        self.assertEqual(s.ecartees, 1)

    def test_G24_fin_annoncee_sans_preuve_reste_a_verifier(self):
        with self._script([{'action': 'terminer', 'raison': 'f', 'resume': 'Extrait téléchargé.'}]):
            self.mgr.start(OWNER, 'Récupérer l’extrait fictif', '', preuves=('fichier',))
            s = self._wait_state(('a_verifier', 'termine'))
        self.assertEqual((s.state, s.motif), ('a_verifier', 'preuve_absente:fichier'))
        with self.assertRaisesRegex(Stop, 'preuve_inconnue'):
            self.mgr.start(OWNER, 'Autre tâche fictive', '', preuves=('formulaire_transmis',))

    def test_G24_preuve_de_fichier_recu_une_seule_fois(self):
        self.mgr._browser()
        b = self.mgr.browser
        for guid in ('g1', 'g2'):
            (b.downloads / guid).write_bytes(b'%PDF-1.4 acte fictif')
        b.cdp.queue = [{'method': 'Browser.downloadWillBegin', 'params': {'guid': 'g1', 'suggestedFilename': 'acte.pdf'}},
                       {'method': 'Browser.downloadProgress', 'params': {'guid': 'g1', 'state': 'completed'}},
                       {'method': 'Browser.downloadWillBegin', 'params': {'guid': 'g2', 'suggestedFilename': 'acte (1).pdf'}},
                       {'method': 'Browser.downloadProgress', 'params': {'guid': 'g2', 'state': 'completed'}}]
        with self._script([{'action': 'attendre', 'raison': 'téléchargement'}, {'action': 'terminer', 'raison': 'f', 'resume': 'Acte récupéré.'}]), \
                patch('agent.navigateur5673.time.sleep', lambda x: None):
            self.mgr.start(OWNER, 'Récupérer l’acte fictif', '', preuves=('fichier',))
            s = self._wait_state(('termine', 'a_verifier'))
        self.assertEqual(s.state, 'termine')
        self.assertEqual(len(s.files), 1)
        self.assertEqual((s.files[0]['type_constate'], len(s.files[0]['sha256'])), ('pdf', 64))
        self.assertEqual(s.recus['fichier'], 1)

    def test_G24_trois_observations_sans_progres_demandent_la_main(self):
        with patch.object(nv.Manager, '_ask_model', lambda m, d, s, p, l: {'action': 'attendre', 'raison': 'lent'}), \
                patch('agent.navigateur5673.time.sleep', lambda x: None):
            out = self.mgr.start(OWNER, 'Tâche fictive bloquée', '')
            s = self._wait_state(('besoin_utilisateur',))
            self.assertEqual(s.pending['kind'], 'blocage')
            n = len(s.steps)
            time.sleep(0.3)
            self.assertEqual(len(s.steps), n)                          # aucun nouveau calcul ni geste pendant l'attente
            self.mgr.stop(out['id'])
            self._wait_state(('arrete',))

    def test_G24_reponse_strictement_validee_dans_la_seance(self):
        with self._script([{'action': 'cliquer', 'raison': 'sans repère'}]):
            self.mgr.start(OWNER, 'Tâche fictive', '')
            s = self._wait_state(('erreur',))
        self.assertEqual(s.error, 'reponse_ia_invalide')
        self.assertEqual(t73.FakeTab.log, [])


# ============================================================================ réglage, routes, câblage, script de recette
class Reglage(Banc):
    def test_G24_modele_du_navigateur_dans_les_routes_existantes(self):
        with patch('agent.model.HTTP', FakeOllama):
            out = N.enregistrer_modele(self.desk, 'admin@example.test', 'ollama', 'qwen3.5:9b', version=0)
        route = self.desk.settings('ai:route:browser_navigation', {})
        self.assertEqual((route['model'], route['origine'], route['revision']), ('qwen3.5:9b', 'explicite', 1))
        self.assertEqual(self.desk.c['model_routing']['assistant'], {'provider': 'ollama', 'model': 'test-local'})   # autres usages intacts
        self.assertEqual(out['reglage']['origine'], 'Choisi pour le navigateur')
        with patch('agent.model.HTTP', FakeOllama), self.assertRaisesRegex(Stop, 'version_perimee'):
            N.enregistrer_modele(self.desk, 'admin@example.test', 'ollama', 'test-local', version=0)
        self.assertEqual(self.desk.settings('ai:route:browser_navigation', {})['model'], 'qwen3.5:9b')
        self.assertIn('browser_navigation', ai_gateway.PURPOSES)

    def test_G24_reglage_global_marque_a_confirmer(self):
        self.desk.c['model_routing']['browser_navigation'] = {'provider': 'ollama', 'model': 'test-local'}   # écrit par un mode global
        self.assertEqual(N.choix(self.desk.c)['origine'], 'reglage_global')

    def test_G24_qualification_tenue_par_modele_digest_et_profil(self):
        pin = routed_config(self.desk.c, 'browser_navigation')['browser_pin109']
        self.assertEqual(N.qualification(self.desk.c, pin, 'sha256:fictif-qwen'), 'a_tester')
        recu = {'modele': 'qwen3.5:9b', 'digest': 'sha256:fictif-qwen', 'profil': pin['profile'], 'executions': 30, 'reussites': 30,
                'systeme': 'Windows 11 et Ubuntu 24.04', 'valeur_formulaire': 'DURAND'}
        propre = N.noter_qualification(self.desk, recu)
        self.assertNotIn('valeur_formulaire', propre)                   # aucune valeur de formulaire dans le reçu
        self.assertEqual(N.qualification(self.desk.c, pin, 'sha256:fictif-qwen'), 'teste_sur_ce_poste')
        self.assertEqual(N.qualification(self.desk.c, pin, 'sha256:fictif-autre'), 'a_requalifier')
        self.assertEqual(N.qualification(self.desk.c, {**pin, 'profile': 'autre'}, 'sha256:fictif-qwen'), 'a_requalifier')
        self.assertEqual(N.qualification(self.desk.c, {**pin, 'model': 'test-local'}, ''), 'autre_modele')
        with self.assertRaisesRegex(Stop, 'recu_qualification_invalide'):
            N.noter_qualification(self.desk, {**recu, 'digest': 'copie-de-la-valeur-attendue'})

    def test_G24_section_parametres_lecture_seule(self):
        avant = self.desk.db.total_changes
        html = N.section_html(self.desk, '/p')
        self.assertIn('Modèle du navigateur', html)
        self.assertIn('qwen3.5:9b', html)
        self.assertIn('À tester', html)
        self.assertEqual(self.desk.db.total_changes, avant)


class Routes(Banc):
    def env(self, role='avocat', user=OWNER, method='GET', body=None):
        import io
        data = json.dumps(body or {}).encode()
        return {'HTTP_X_AXIORHUB_USER': user, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': method, 'HTTP_ORIGIN': 'https://cabinet.example.test',
                'HTTP_X_CSRF_TOKEN': 'jeton-fictif', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(data)), 'wsgi.input': io.BytesIO(data)}

    AUTH = {'origin': 'https://cabinet.example.test', 'csrf': 'jeton-fictif'}

    def test_G24_droits_des_routes(self):
        self.assertTrue(N.allowed('assistant', 'GET', 'm56109/navigateur/etat'))
        self.assertFalse(N.allowed('avocat', 'POST', 'm56109/navigateur/modele'))
        self.assertTrue(N.allowed('administrateur', 'POST', 'm56109/navigateur/modele'))
        self.assertTrue(N.allowed('assistant', 'POST', 'm56109/navigateur/main'))
        self.assertFalse(N.allowed('avocat', 'POST', 'm56109/navigateur/inconnue'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56109/navigateur/modele'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/navigateur/modele'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/navigateur/main'))

    def test_G24_etat_et_refus_par_web440(self):
        out = web440.api(self.env(), self.desk, self.AUTH, '/p', 'm56109/navigateur/etat', {}, 'GET')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        corps = json.loads(out['body'])
        self.assertEqual(corps['reglage']['modele'], 'qwen3.5:9b')
        out = web440.api(self.env(method='POST', body={'modele': 'x'}), self.desk, self.AUTH, '/p', 'm56109/navigateur/modele', {}, 'POST')
        self.assertEqual(out['status'], '403 Forbidden')
        with patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': ''}):
            out = web440.api(self.env(method='POST', body={'id': 'x', 'prendre': '1'}), self.desk, self.AUTH, '/p', 'm56109/navigateur/main', {}, 'POST')
        self.assertEqual(out['status'], '409 Conflict')                   # navigateur réservé au poste : le serveur ne simule rien

    def test_G13_main_par_la_route_rend_l_acquittement(self):
        with self._script([{'action': 'demander', 'texte': 'Connectez-vous', 'raison': 'connexion'}]), \
                patch.dict(os.environ, {'AXIORHUB_POSTE_HOME': '/tmp/poste-fictif'}), \
                patch.object(nv, 'manager', lambda cfg: self.mgr):
            out = self.mgr.start(OWNER, 'Tâche fictive', '')
            self._wait_state(('attente_avocat',))
            rep = web440.api(self.env(method='POST', body={'id': out['id'], 'prendre': '1'}), self.desk, self.AUTH, '/p', 'm56109/navigateur/main', {}, 'POST')
            corps = json.loads(rep['body'])
            self.assertTrue(corps['seance']['main_acquittee'])
            self.assertIn('La main est à vous', corps['message'])
            rep = web440.api(self.env(method='POST', user='autre@example.test', body={'id': out['id'], 'prendre': '0'}), self.desk, self.AUTH, '/p',
                             'm56109/navigateur/main', {}, 'POST')
            self.assertEqual(rep['status'], '404 Not Found')              # séance d'un autre : même 404 neutre

    def test_G24_cablage(self):
        self.assertIn('v56109-navigateur.css', shell501.BUNDLE)
        self.assertIn('/static/v56109-navigateur.css', web440.STATIC)
        self.assertTrue((ROOT / 'agent/static/v56109-navigateur.css').is_file())
        self.assertIn('n56109-main', (ROOT / 'agent/static/app520.css').read_text(encoding='utf-8'))
        for table in ('poste56109_autorisations', 'poste56109_recus', 'poste56109_postes', 'poste56109_ordres', 'poste56109_baux'):
            self.assertTrue(self.desk.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone(), table)
            self.assertIn(table, json.dumps(export56108.EXPORT_EXCLUES, default=str))
        src = (ROOT / 'agent/model.py').read_text(encoding='utf-8')
        self.assertEqual(src.count('interruptible(lambda:self.http.json('), 4)             # transport commun inchangé (aucun second client)
        self.assertEqual(src.count('preparer_emission(self.cfg)'), 3)
        self.assertNotIn('/api/chat', (ROOT / 'agent/navigateur56109.py').read_text(encoding='utf-8'))
        js = (ROOT / 'agent/static/v5673.js').read_text(encoding='utf-8')
        self.assertIn("'limite_atteinte'", js)
        self.assertIn('main_acquittee', js)

    def test_G24_script_de_recette_sans_reseau_ni_modele_par_defaut(self):
        spec = importlib.util.spec_from_file_location('recette_qwen56109', ROOT / 'scripts/recette-navigateur-qwen56109.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with patch('agent.common.HTTP.request', side_effect=AssertionError('réseau')), patch('agent.model.HTTP', side_effect=AssertionError('modèle')), \
                patch('builtins.print'):
            self.assertEqual(mod.main(['--protocole']), 0)
        self.assertEqual(len(mod.PARCOURS), 3)
        with patch('builtins.input', return_value='non'), patch('builtins.print'):
            self.assertEqual(mod.executer('config.json', 'https://recette.example.test', 'rechercher_societe', 5, '', '', False), 1)   # rien sans « oui »


if __name__ == '__main__':
    unittest.main()

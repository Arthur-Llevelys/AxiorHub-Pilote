"""5.6.109 (lot E, F15) : connexion Google Gemini par API, clarification API / abonnement.

Circuit unique : le type « gemini » passe par ``Model`` et son chemin compatible OpenAI (politique, pseudonymisation, enveloppe contrôlée,
pause, budget, journal), jamais par un second transport. Faux transports seulement : aucun appel réseau (tout socket échoue), aucune adresse
Google contactée, aucun compte ni clé réels ; données fictives (DURAND, KERVILAC, DOS-001, example.test). Chaque garde-fou a son
contre-exemple : une mutation en mémoire montre que le test échouerait si le garde-fou était retiré."""
import io
import json
from pathlib import Path
import socket
import unittest
import uuid
from unittest.mock import patch

from agent import ai_gateway, arret5643, consommation56109 as C, gemini56109 as G, pseudo540, transmission56109
from agent import dossiers5697 as D, fiche5698 as F
from agent.common import Stop
from agent.erreurs5634 import http_error
from agent.model import Model, routed_config
import test_v540
from test_v5697_dossiers import ADMIN, FauxStockage

CLE = 'cle-gemini-fictive-0123456789-ZORVAL'
CLE2 = 'cle-gemini-fictive-9876543210-KERVILAC'
PID = G.PID
MODELE = 'gemini-4-neon'
CATALOGUE = {'object': 'list', 'data': [{'id': 'models/gemini-4-neon', 'object': 'model', 'owned_by': 'google'},
                                        {'id': 'models/gemini-embedding-001', 'object': 'model'}, {'id': 'models/imagen-4', 'object': 'model'},
                                        {'id': 'models/gemini-4-flash-lite', 'object': 'model'}]}


def _reseau_interdit(*a, **k):
    raise AssertionError('appel réseau non simulé')


def setUpModule():
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()


def reponse(texte='Bonjour.', fin='stop', usage=None, **message):
    return {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': texte, **message}, 'finish_reason': fin}],
            'usage': usage if usage is not None else {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 150}}


class Catalogue:
    """Faux transport du catalogue (``GET /models``) : (méthode, chemin, clé) → document ou erreur."""
    def __init__(self, doc=CATALOGUE, erreur=None):
        self.doc, self.erreur, self.appels, self.cles = doc, erreur, [], []

    def __call__(self, methode, chemin, cle, corps=None):
        self.appels.append((methode, chemin))
        self.cles.append(cle)
        if self.erreur is not None:
            raise self.erreur
        return self.doc


class FauxHTTP:
    """Faux transport de ``Model`` : enregistre l'adresse, le chemin, le corps et les en-têtes ; jamais de réseau."""
    appels, corps, entetes = [], [], []
    reponse = None

    def __init__(self, base, *a, **k):
        self.base, self.headers = base, {}

    def json(self, method, path, payload=None):
        FauxHTTP.appels.append((self.base, method, path))
        FauxHTTP.corps.append(payload)
        FauxHTTP.entetes.append(dict(self.headers))
        if path == '/api/show':
            return {}
        if path == '/api/chat':
            return {'done': True, 'done_reason': 'stop', 'message': {'content': 'réponse locale'}}
        if path == '/models':
            return CATALOGUE
        if path == '/chat/completions':
            r = FauxHTTP.reponse
            if isinstance(r, BaseException):
                raise r
            return r if r is not None else reponse()
        raise AssertionError(path)


class Base(unittest.TestCase):
    setUp0 = test_v540.External.setUp
    route_all = test_v540.External.route_all

    def setUp(self):
        self.setUp0()
        self.addCleanup(self.d.db.close)
        self.c = self.f.c
        FauxHTTP.appels, FauxHTTP.corps, FauxHTTP.entetes, FauxHTTP.reponse = [], [], [], None
        p = patch('agent.model.HTTP', FauxHTTP)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: pseudo540._CACHE.update(key=None, at=0, items=[]))

    # ------------------------------------------------------------------ outils
    def enregistrer(self, cle=CLE, modele=MODELE, revision=None, transport=None):
        return G.enregistrer(self.d, 'admin', cle, revision, modele, transport=transport or Catalogue())

    def activer(self, **champs):
        data = {'actif': True, 'envoi': True, 'modele': MODELE, 'prix_entree': '1', 'prix_sortie': '2', 'plafond_mensuel': '10',
                'plafond_requete': '1', 'revision': ai_gateway.revision_of(self.d.settings('ai:provider:' + PID, {}))}
        data.update(champs)
        return G.activer(self.d, 'admin', data)

    def connecter(self):
        self.enregistrer()
        self.activer()
        self.route_all(PID, MODELE)
        return Model(routed_config(self.c, 'mail_drafting'))

    def chemins(self, chemin='/chat/completions'):
        return [a for a in FauxHTTP.appels if a[2] == chemin]

    def emis(self):
        corps = [c for a, c in zip(FauxHTTP.appels, FauxHTTP.corps) if a[2] == '/chat/completions']
        return json.dumps(corps[-1], ensure_ascii=False) if corps else ''

    def fichiers_contenant(self, octets):
        """Tous les fichiers de l'état (base, journaux, coffre chiffré compris) qui contiennent ces octets."""
        return [str(p) for p in Path(self.c['state_dir']).rglob('*') if p.is_file() and octets in p.read_bytes()]


# ================================================================================================ fournisseur et transport
class Fournisseur(Base):
    def test_F15_type_gemini_declare_adresse_officielle_et_cle_en_entete(self):
        self.assertIn('gemini', ai_gateway.PROVIDER_TYPES)
        m = self.connecter()
        self.assertEqual(m.complete([{'role': 'user', 'content': 'Point sur le dossier.'}]), 'Bonjour.')
        self.assertEqual(self.chemins(), [(G.BASE_URL, 'POST', '/chat/completions')])
        self.assertFalse(self.chemins('/models'))                       # catalogue déjà lu et testé : aucune relecture avant un envoi
        self.assertEqual(FauxHTTP.entetes[-1].get('Authorization'), 'Bearer ' + CLE)
        corps = json.loads(self.emis())
        self.assertEqual((corps['model'], corps['stream']), (MODELE, False))
        self.assertNotIn('response_format', corps)
        self.assertEqual(m.last_provider, PID)                          # provenance : Google, pas un faux « openai »

    def test_F15_adresse_libre_refusee(self):
        m = self.connecter()
        cfg = dict(m.cfg, url='https://passerelle.example.test/v1')
        with self.assertRaisesRegex(Stop, 'gemini_adresse_non_officielle'):
            Model(cfg)
        self.assertFalse(self.chemins())

    def test_F15_modele_hors_catalogue_jamais_substitue(self):
        self.connecter()
        cfg = routed_config(self.c, 'mail_drafting')
        cfg['model'] = 'gemini-9-inexistant'
        with self.assertRaisesRegex(Stop, 'gemini_modele_hors_catalogue'):
            Model(cfg)
        self.assertFalse(self.chemins())

    def test_F15_reponse_tronquee_bloquee_outil_ou_vide_jamais_terminee(self):
        m = self.connecter()
        for r, code in ((reponse(fin='length'), 'generation_ia_incomplete'), (reponse(fin='content_filter'), 'reponse_ia_bloquee_securite'),
                        (reponse(fin=None), 'generation_ia_non_terminee'), (reponse(tool_calls=[{'id': 'x'}]), 'appel_outil_ia_refuse'),
                        (reponse(texte='  '), 'reponse_ia_vide'), ({'choices': []}, 'reponse_fournisseur_invalide')):
            FauxHTTP.reponse = r
            with self.assertRaises(Stop) as ctx:
                m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
            self.assertEqual(str(ctx.exception).split(':')[0], code)

    def test_F15_schema_json_non_annonce_sans_essai_validation_locale(self):
        m = self.connecter()
        FauxHTTP.reponse = reponse('```json\n{"titre": "Note"}\n```')
        out = m.complete([{'role': 'system', 'content': 'Consignes.'}, {'role': 'user', 'content': 'Écris.'}], json_schema={'type': 'object'})
        self.assertEqual(json.loads(out), {'titre': 'Note'})
        corps = json.loads(self.emis())
        self.assertNotIn('response_format', corps)                      # schéma jamais essayé : sortie structurée non promise au fournisseur
        self.assertIn('JSON', corps['messages'][0]['content'])

    def test_F15_images_refusees_explicitement_jamais_perdues_en_silence(self):
        m = self.connecter()
        req = {'model': MODELE, 'messages': [{'role': 'user', 'content': 'Voir.'}]}
        modele = type('M', (), {'cfg': m.cfg, '_images56106': [object()]})()
        with self.assertRaisesRegex(Stop, 'gemini_images_non_branchees'):
            G.adapter_requete(modele, req, None)


# ================================================================================================ garde-fous
class GardeFous(Base):
    def test_F15_cle_jamais_dans_url_corps_journal_fichier_ni_erreur(self):
        m = self.connecter()
        m.complete([{'role': 'user', 'content': 'Point sur DOS-001.'}])
        for base, _, chemin in FauxHTTP.appels:
            self.assertNotIn(CLE, base + chemin)
        self.assertNotIn(CLE, json.dumps(FauxHTTP.corps, ensure_ascii=False, default=str))
        self.assertEqual(self.fichiers_contenant(CLE.encode()), [])     # base, journaux, audit, coffre (chiffré) : la clé n'y est jamais en clair
        fuite = Stop('http_401')
        fuite.detail = {'provider_error': {'message': 'cle ' + CLE2}}
        with self.assertRaises(Stop) as ctx:
            G.tester(self.d, 'admin', CLE2, transport=Catalogue(erreur=fuite))
        self.assertEqual(str(ctx.exception), 'gemini_cle_refusee')
        self.assertNotIn(CLE2, G.message(str(ctx.exception)))
        self.assertNotIn(CLE.encode(), G.carte(self.d).encode())
        # contre-exemple : sans la réduction au code public, le texte du fournisseur (qui recopie la clé) remonterait
        with patch.object(G, 'code_public', lambda error: str(error) + ' ' + json.dumps(getattr(error, 'detail', {}))):
            with self.assertRaises(Stop) as ctx:
                G.tester(self.d, 'admin', CLE2, transport=Catalogue(erreur=fuite))
            self.assertIn(CLE2, str(ctx.exception))

    def test_F15_identite_connue_jamais_emise_vers_gemini(self):
        p = patch.object(D, '_client_stockage', lambda desk: FauxStockage())
        p.start()
        self.addCleanup(p.stop)
        did = D.creer(self.d, ADMIN, {'titre': 'DURAND c/ MARTIN'}, uuid.uuid4().hex)['id']
        F.ajouter_partie(self.d, ADMIN, did, {'nom': 'Hélène Kervilac', 'role': 'adverse'}, uuid.uuid4().hex)
        m = self.connecter()
        FauxHTTP.reponse = reponse('Projet pour [PERSONNE_1].')
        out = m.complete([{'role': 'user', 'content': 'Nous representons Hélène Kervilac.'}])
        self.assertNotIn('Kervilac', self.emis())
        self.assertNotIn('Hélène', self.emis())
        self.assertEqual(out, 'Projet pour Hélène Kervilac.')
        # contre-exemple : sans pseudonymisation ni contrôle de l'enveloppe, le nom partirait vers Google
        FauxHTTP.appels, FauxHTTP.corps = [], []
        FauxHTTP.reponse = reponse('Projet.')
        with patch.object(pseudo540.Pseudonymizer, 'apply_messages', lambda self, messages, method_at=None: messages), \
                patch.object(transmission56109, '_enveloppe', lambda *a, **k: None):
            Model(routed_config(self.c, 'mail_drafting')).complete([{'role': 'user', 'content': 'Nous representons Hélène Kervilac.'}])
        self.assertIn('Kervilac', self.emis())

    def test_F15_connexion_non_testee_jamais_proposee_au_routage(self):
        secret = ai_gateway.save_secret_version(self.d, PID, CLE)
        reglage = {'type': 'gemini', 'url': G.BASE_URL, 'model': MODELE, 'enabled': True, 'external_data_allowed': True, 'secret_file': secret,
                   'monthly_budget_usd': 10, 'per_request_budget_usd': 1, 'input_usd_per_million': 1, 'output_usd_per_million': 1,
                   'state_dir': self.c['state_dir'], 'revision': 1}
        self.d.setting('ai:provider:' + PID, reglage)
        self.c.setdefault('ai_providers', {})[PID] = reglage
        self.route_all(PID, MODELE)
        with self.assertRaisesRegex(Stop, 'gemini_connexion_non_testee'):
            Model(routed_config(self.c, 'mail_drafting'))
        with self.assertRaisesRegex(Stop, 'gemini_connexion_non_testee'):
            ai_gateway.save_route(self.d, 'mail_drafting', PID, MODELE)
        self.assertFalse(self.chemins())
        # contre-exemple : sans la garde, la connexion jamais testée serait utilisée
        with patch.object(G, 'garde_connexion', lambda cfg: None):
            Model(routed_config(self.c, 'mail_drafting')).complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertTrue(self.chemins())

    def test_F15_reglage_modifie_apres_le_test_redevient_non_teste(self):
        self.connecter()
        reglage = dict(self.d.settings('ai:provider:' + PID, {}))
        reglage['revision'] += 1                                         # clé ou adresse changée hors du service de test
        self.d.setting('ai:provider:' + PID, reglage)
        with self.assertRaisesRegex(Stop, 'gemini_connexion_non_testee'):
            Model(routed_config(self.c, 'mail_drafting'))
        G.tester(self.d, 'admin', utiliser_existante=True, transport=Catalogue())     # réexamen : la connexion redevient utilisable
        Model(routed_config(self.c, 'mail_drafting')).complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertEqual(len(self.chemins()), 1)

    def test_F15_pause_respectee(self):
        m = self.connecter()
        arret5643.pause_all(self.d, True)
        with self.assertRaises(Stop) as ctx:
            m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertEqual(str(ctx.exception), 'ia_en_pause')
        with self.assertRaisesRegex(Stop, 'ia_en_pause'):
            G.tester(self.d, 'admin', utiliser_existante=True, generation=True, modele=MODELE, transport=Catalogue())
        self.assertFalse(self.chemins())
        # contre-exemple : sans le contrôle de la pause, l'appel partirait
        with patch.object(arret5643, 'reason_to_stop', lambda *a, **k: ''):
            m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertTrue(self.chemins())

    def test_F15_aucun_repli_silencieux_apres_un_refus_de_google(self):
        m = self.connecter()
        self.assertTrue(m.cfg.get('budget_fallback'))                    # un modèle local est prêt : il ne doit pas prendre le relais en silence
        FauxHTTP.reponse = http_error(429, 'POST', G.BASE_URL + '/chat/completions', b'{"error":{"code":429,"status":"RESOURCE_EXHAUSTED"}}')
        with self.assertRaisesRegex(Stop, 'gemini_limite_atteinte'):
            m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
        FauxHTTP.reponse = reponse(fin='content_filter')
        with self.assertRaisesRegex(Stop, 'reponse_ia_bloquee_securite'):
            m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertEqual({a[0] for a in FauxHTTP.appels}, {G.BASE_URL})
        self.assertFalse(self.chemins('/api/chat'))
        # contre-exemple : un 429 traduit en code de plafond ferait démarrer le modèle local sans que l'avocat l'ait choisi
        FauxHTTP.reponse = http_error(429, 'POST', G.BASE_URL + '/chat/completions', b'{}')
        with patch.object(G, 'traduire_erreur', lambda error: Stop('quota_fournisseur_depasse')):
            m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertTrue(self.chemins('/api/chat'))

    def test_F15_politique_locale_interdit_gemini(self):
        from agent import politique5682
        m = self.connecter()
        politique5682.save(self.d, {'mode': 'local'}, 'admin')
        with self.assertRaises(Stop):
            Model(m.cfg)
        self.assertFalse(self.chemins())


# ================================================================================================ tester avant d'activer
class TesterAvantActiver(Base):
    def test_F15_test_candidat_sans_effet_local(self):
        t = Catalogue()
        out = G.tester(self.d, 'admin', CLE, transport=t)
        self.assertEqual((out['statut'], out['modeles']), ('cle_reconnue', [MODELE, 'gemini-4-flash-lite']))   # ni embeddings ni images
        self.assertEqual(t.appels, [('GET', '/models')])
        self.assertIsNone(self.d.settings('ai:provider:' + PID, None))
        self.assertFalse(list((Path(self.c['state_dir']) / 'vault' / 'ai-providers').glob(PID + '*')))
        self.assertIn('Rien n’est encore enregistré', out['message'])

    def test_F15_premier_enregistrement_propose_desactive(self):
        out = self.enregistrer()
        reglage = self.d.settings('ai:provider:' + PID, {})
        self.assertEqual((reglage['type'], reglage['enabled'], reglage['external_data_allowed']), ('gemini', False, False))
        self.assertEqual((reglage['input_usd_per_million'], reglage['output_usd_per_million']), (0, 0))   # tarif inconnu, jamais inventé
        self.assertTrue(G.etat(self.d)['teste'])
        self.assertFalse(out['actif'])
        with self.assertRaisesRegex(Stop, 'autorisation_donnees_externes_requise'):
            self.activer(envoi=False)
        with self.assertRaisesRegex(Stop, 'gemini_tarif_requis'):
            self.activer(prix_entree='', prix_sortie='')
        with self.assertRaisesRegex(Stop, 'reglage_modifie_entre_temps'):
            self.activer(revision=99)
        with self.assertRaisesRegex(Stop, 'gemini_modele_hors_catalogue'):
            self.activer(modele='gemini-9-inexistant')
        self.assertFalse(self.d.settings('ai:provider:' + PID, {})['enabled'])
        self.activer()
        self.assertTrue(self.d.settings('ai:provider:' + PID, {})['enabled'])
        self.activer(actif=False, envoi=False, prix_entree='', prix_sortie='')        # désactiver ne demande rien
        self.assertFalse(self.d.settings('ai:provider:' + PID, {})['enabled'])

    def test_F15_cle_refusee_ne_remplace_pas_la_cle_en_service(self):
        self.enregistrer()
        avant = self.d.settings('ai:provider:' + PID, {})
        coffre = Path(self.c['state_dir']) / 'vault' / 'ai-providers'
        fichiers = sorted(coffre.iterdir())
        with self.assertRaisesRegex(Stop, 'gemini_cle_refusee'):
            self.enregistrer(CLE2, revision=avant['revision'], transport=Catalogue(erreur=http_error(401, 'GET', G.BASE_URL + '/models')))
        self.assertEqual(self.d.settings('ai:provider:' + PID, {}), avant)
        self.assertEqual(sorted(coffre.iterdir()), fichiers)

    def test_F15_rotation_garde_reglages_et_exige_revision(self):
        self.enregistrer()
        self.activer()
        rev = self.d.settings('ai:provider:' + PID, {})['revision']
        with self.assertRaisesRegex(Stop, 'reglage_modifie_entre_temps'):
            self.enregistrer(CLE2, revision=rev - 1)
        self.enregistrer(CLE2, revision=rev, modele='')
        apres = self.d.settings('ai:provider:' + PID, {})
        self.assertEqual((apres['revision'], apres['enabled'], apres['model'], apres['input_usd_per_million']), (rev + 1, True, MODELE, 1.0))
        self.assertTrue(G.etat(self.d)['teste'])

    def test_F15_erreurs_lisibles(self):
        cas = {401: 'gemini_cle_refusee', 403: 'gemini_acces_refuse', 429: 'gemini_limite_atteinte', 404: 'gemini_modele_indisponible',
               400: 'gemini_requete_refusee', 503: 'gemini_indisponible'}
        for statut, code in cas.items():
            with self.assertRaises(Stop) as ctx:
                G.tester(self.d, 'admin', CLE, transport=Catalogue(erreur=http_error(statut, 'GET', G.BASE_URL + '/models')))
            self.assertEqual(str(ctx.exception), code)
        with self.assertRaisesRegex(Stop, 'gemini_indisponible'):
            G.tester(self.d, 'admin', CLE, transport=Catalogue(erreur=Stop('connexion_http_indisponible')))
        with self.assertRaisesRegex(Stop, 'gemini_catalogue_vide'):
            G.tester(self.d, 'admin', CLE, transport=Catalogue({'data': [{'id': 'models/imagen-4'}]}))
        from agent.web440 import human
        for code in list(cas.values()) + ['gemini_connexion_non_testee', 'reponse_ia_bloquee_securite', 'gemini_images_non_branchees',
                                          'gemini_modele_hors_catalogue', 'gemini_tarif_requis', 'generation_ia_non_terminee']:
            self.assertNotIn('_', G.message(code), code)
            self.assertNotIn('_', human(code), code)
        self.assertIn('région', G.message('gemini_acces_refuse'))

    def test_F15_test_de_generation_passe_par_model_et_la_politique(self):
        self.connecter()
        out = G.tester(self.d, 'admin', utiliser_existante=True, generation=True, modele=MODELE, transport=Catalogue())
        self.assertEqual(out['statut'], 'generation_reussie')
        corps = json.loads(self.emis())
        self.assertEqual(corps['messages'][-1]['content'], G.GENERATION_TEXTE)
        self.assertLessEqual(corps['max_tokens'], G.GENERATION_JETONS)


# ================================================================================================ coût et données
class Consommation(Base):
    def test_F15_usage_gemini_normalise_cache_non_declare_et_reflexion(self):
        self.assertEqual(C.FORMATS['gemini'], 'gemini_openai')
        cfg = {'provider_type': 'gemini', 'input_usd_per_million': 1, 'output_usd_per_million': 2}
        obs = C.observer(cfg, {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 150})
        u = obs['usage']
        self.assertEqual((u['entree'], u['sortie_facturable'], u['raisonnement'], u['cache_lu']), (100, 50, 30, None))
        self.assertEqual(obs['mesure'], 'sans_detail_cache')
        self.assertEqual((obs['montant_micro'], obs['etat_montant']), (200, 'majorant_cache_non_detaille'))
        detail = C.observer(cfg, {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120, 'prompt_tokens_details': {'cached_tokens': 40}})
        self.assertEqual((detail['usage']['cache_lu'], detail['usage']['entree_hors_cache'], detail['mesure']), (40, 60, 'complete'))

    def test_F15_absence_inconnue_tarif_absent_jamais_zero(self):
        sans_total = C.observer({'provider_type': 'gemini', 'input_usd_per_million': 1, 'output_usd_per_million': 2},
                                {'prompt_tokens': 100, 'completion_tokens': 20})
        self.assertIsNone(sans_total['usage']['sortie_facturable'])     # réflexion peut-être hors « completion_tokens » : non établi
        self.assertIsNone(sans_total['montant_micro'])
        sans_tarif = C.observer({'provider_type': 'gemini'}, {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 150})
        self.assertEqual((sans_tarif['montant_micro'], sans_tarif['etat_montant']), (None, 'tarif_manquant'))
        self.assertEqual(C.observer({'provider_type': 'gemini'}, {})['mesure'], 'absente')
        self.assertEqual(C.observer({'provider_type': 'gemini'}, {'prompt_tokens': 100, 'total_tokens': 50})['mesure'], 'incoherente')
        natif = C.normaliser('gemini', {'promptTokenCount': 100, 'cachedContentTokenCount': 40, 'candidatesTokenCount': 20, 'thoughtsTokenCount': 5})
        self.assertEqual((natif['entree_hors_cache'], natif['sortie_facturable']), (60, 25))

    def test_F15_appel_reel_compte_au_registre_par_le_chemin_commun(self):
        import sqlite3
        m = self.connecter()
        m.complete([{'role': 'user', 'content': 'Texte fictif.'}])
        db = sqlite3.connect(Path(self.c['state_dir']) / 'desk.sqlite3')
        try:
            row = db.execute('SELECT provider, input_tokens, output_tokens, mesure FROM ai_usage_v391 ORDER BY id DESC LIMIT 1').fetchone()
        finally:
            db.close()
        self.assertEqual(row, (PID, 100, 50, 'sans_detail_cache'))

    def test_F15_sans_tarif_aucun_envoi(self):
        m = self.connecter()
        cfg = dict(m.cfg, input_usd_per_million=0, output_usd_per_million=0)
        with self.assertRaisesRegex(Stop, 'tarif_fournisseur_non_renseigne'):
            Model(cfg).complete([{'role': 'user', 'content': 'Texte fictif.'}])
        self.assertFalse(self.chemins())


# ================================================================================================ Paramètres, routes, textes
class Parametres(Base):
    def test_F15_carte_dans_les_parametres_et_textes_api_abonnement(self):
        from agent import ia5682
        page = ia5682.card(self.d)
        self.assertIn('id="k109-gemini"', page)
        self.assertIn('id="k96-mistral"', page)                         # connexion Mistral existante conservée
        carte = G.carte(self.d)
        for texte in ('Tester la connexion', 'data-api="m56109/gemini/tester"', 'n’est pas une API', 'Gemini CLI', 'non livrée',
                      'ChatGPT', 'Mistral', 'Gmail', 'Quota restant : Non fourni', 'proposée désactivée'):
            self.assertIn(texte, carte)
        self.enregistrer()
        carte = G.carte(self.d)
        self.assertIn('Clé reconnue', carte)
        self.assertIn('Tarif non renseigné', carte)
        self.assertNotIn(CLE, carte)

    def env(self, data, role='administrateur', csrf='jeton'):
        corps = json.dumps(data).encode()
        return {'HTTP_ORIGIN': 'https://axiorhub.example.test', 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
                'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'HTTP_X_AXIORHUB_USER': 'admin', 'HTTP_X_AXIORHUB_ROLE': role}

    def appel(self, route, data, **k):
        auth = {'origin': 'https://axiorhub.example.test', 'csrf': 'jeton'}
        r = G.api(self.env(data, **k), self.d, auth, '', 'm56109/gemini/' + route, {}, 'POST')
        self.assertIn(('Cache-Control', 'private, no-store'), r['headers'])
        return r['status'], json.loads(r['body'])

    def test_F15_routes_droits_csrf_et_idempotence(self):
        from agent import standalone_auth
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56109/gemini/tester'))
        self.assertFalse(standalone_auth.allowed('assistant', 'POST', '/api440/m56109/gemini/activer'))
        self.assertTrue(standalone_auth.allowed('administrateur', 'POST', '/api440/m56109/gemini/enregistrer'))
        t = Catalogue()
        with patch.object(G, '_http', lambda: t):
            statut, out = self.appel('tester', {'api_key': CLE})             # clé candidate : aucun effet local, rien à rejouer
            self.assertEqual(statut, '200 OK')
            self.assertNotIn(CLE, json.dumps(out))
            self.assertEqual(self.appel('tester', {'api_key': CLE}, csrf='faux')[0], '403 Forbidden')
            self.assertEqual(self.appel('tester', {'api_key': CLE}, role='avocat')[0], '403 Forbidden')
            self.assertEqual(self.appel('enregistrer', {'api_key': CLE, 'modele': MODELE})[0], '422 Unprocessable Entity')   # clé d'idempotence exigée
            appels = len(t.appels)
            statut, out = self.appel('enregistrer', {'api_key': CLE, 'cle': 'k-4', 'modele': MODELE})
            self.assertEqual(statut, '200 OK')
            self.assertNotIn(CLE, json.dumps(out))
            self.assertEqual(self.appel('enregistrer', {'api_key': CLE, 'cle': 'k-4', 'modele': MODELE})[1].get('rejoue'), True)
            self.assertEqual(len(t.appels), appels + 2)                  # même clé, même demande : même reçu, aucun second enregistrement
            self.assertEqual(self.appel('enregistrer', {'api_key': CLE2, 'cle': 'k-4', 'modele': MODELE})[0], '409 Conflict')
        self.assertEqual(self.d.settings('ai:provider:' + PID, {})['revision'], 1)
        self.assertEqual(self.fichiers_contenant(CLE.encode()), [])
        statut, out = self.appel('activer', {'cle': 'k-5', 'actif': 'on', 'envoi': 'on', 'modele': MODELE, 'prix_entree': '1,5', 'prix_sortie': '2',
                                             'revision': '0'})
        self.assertEqual(statut, '409 Conflict')

    def test_F15_structure_de_model_inchangee(self):
        """Gemini emprunte le chemin compatible OpenAI existant : aucun appel au transport ni envoi payant de plus dans ``model.py``."""
        import re
        source = (Path(__file__).resolve().parents[1] / 'agent' / 'model.py').read_text(encoding='utf-8')
        self.assertEqual(source.count('interruptible(lambda:self.http.json('), 4)
        self.assertEqual(source.count('preparer_emission(self.cfg)'), 3)
        self.assertEqual(len(re.findall(r"self\.http\.json\('POST','/(?:messages|chat/completions)'", source)), 2)
        module = (Path(__file__).resolve().parents[1] / 'agent' / 'gemini56109.py').read_text(encoding='utf-8')
        self.assertNotIn("'/chat/completions'", module)
        self.assertNotIn('interruptible(', module)


if __name__ == '__main__':
    unittest.main()

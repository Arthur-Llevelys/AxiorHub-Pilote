"""5.6.112 : Open WebUI installé et réglé d'après AxiorHub (``agent/openwebui_reglage56112.py``, ``configure-openwebui.py``,
``install-openwebui.py``, commande ``axiorhub-mail openwebui``).

Faux Open WebUI en mémoire, aux réponses calquées sur Open WebUI 0.13.5 essayé réellement (formes relevées le 27/11/2026) ; faux Docker et
faux Apache (``lancer`` remplacée). Aucun réseau, aucun Docker, aucun service réel ; données fictives (example.test).
"""
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from agent import openwebui_reglage56112 as R

ROOT = Path(__file__).resolve().parent.parent


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


def _module(nom, fichier):
    spec = importlib.util.spec_from_file_location(nom, ROOT / fichier)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FauxOpenWebUI:
    """État d'une instance Open WebUI 0.13.5 neuve ; mêmes routes et mêmes formes que la vraie (essai du 27/11/2026)."""

    def __init__(self, admin=True, modeles=('mistral-large-latest',)):
        self.admin = admin
        self.outils, self.valves, self.modeles, self.invites, self.competences, self.acces = {}, {}, {}, {}, {}, []
        self.openai = {'ENABLE_OPENAI_API': False, 'OPENAI_API_BASE_URLS': ['https://api.openai.com/v1'], 'OPENAI_API_KEYS': [''], 'OPENAI_API_CONFIGS': {}}
        self.ollama = {'ENABLE_OLLAMA_API': False, 'OLLAMA_BASE_URLS': ['http://host.docker.internal:11434'], 'OLLAMA_API_CONFIGS': {}}
        self.serveurs = {'TOOL_SERVER_CONNECTIONS': []}
        self.config_modeles = {'DEFAULT_MODELS': None, 'DEFAULT_PINNED_MODELS': None, 'MODEL_ORDER_LIST': [], 'DEFAULT_MODEL_METADATA': {},
                               'DEFAULT_MODEL_PARAMS': {}}
        self.base = list(modeles)
        self.ecritures = []
        self.n = 0
        self.permissions = {'workspace': {'models': False}, 'features': {'api_keys': False, 'notes': True}}
        self.audio = {'tts': {'ENGINE': '', 'MODEL': 'tts-1', 'VOICE': 'alloy', 'OPENAI_API_BASE_URL': 'https://api.openai.com/v1', 'OPENAI_PARAMS': {},
                              'SPLIT_ON': 'punctuation'},
                      'stt': {'ENGINE': '', 'WHISPER_MODEL': 'base', 'ALLOWED_EXTENSIONS': ['wav']}}
        self.audio_500 = True          # 0.13.5 : réglage enregistré, puis 500 si Whisper ne peut se charger (essai réel hors ligne)
        self.proprietaires = {}
        self.utilisateurs = []
        self.admin_config = {'ENABLE_SIGNUP': True, 'ENABLE_API_KEYS': False, 'DEFAULT_USER_ROLE': 'pending', 'JWT_EXPIRES_IN': '4w'}

    def __call__(self, methode, chemin, corps):
        if methode == 'POST':
            self.ecritures.append((chemin, corps))
        admin_seul = chemin.startswith(('/openai/', '/ollama/', '/api/v1/configs/'))
        if admin_seul and not self.admin:
            return 401, {'detail': 'Unauthorized'}
        if chemin == '/openai/config':
            return 200, json.loads(json.dumps(self.openai))
        if chemin == '/openai/config/update':
            self.openai = corps
            return 200, corps
        if chemin == '/ollama/config':
            return 200, json.loads(json.dumps(self.ollama))
        if chemin == '/ollama/config/update':
            self.ollama = corps
            return 200, corps
        if chemin == '/api/v1/configs/tool_servers':
            if methode == 'POST':
                self.serveurs = corps
            return 200, json.loads(json.dumps(self.serveurs))
        if chemin == '/api/v1/configs/models':
            if methode == 'POST':
                self.config_modeles = corps
            return 200, dict(self.config_modeles)
        if chemin == '/api/models':
            return 200, {'data': [{'id': x, 'owned_by': self.proprietaires.get(x, 'openai')} for x in self.base]
                         + [{'id': k, 'owned_by': 'openai'} for k in self.modeles if k not in self.base]}
        if chemin == '/api/v1/auths/admin/config':
            if methode == 'POST':
                self.admin_config = corps
            return 200, dict(self.admin_config)
        if chemin == '/api/v1/tools/':
            return 200, [{'id': k, 'name': v.get('name')} for k, v in self.outils.items()]
        if chemin == '/api/v1/users/default/permissions':
            if methode == 'POST':
                self.permissions = corps
            return 200, json.loads(json.dumps(self.permissions))
        if chemin == '/api/v1/audio/config':
            return 200, json.loads(json.dumps(self.audio))
        if chemin == '/api/v1/audio/config/update':
            self.audio = {'tts': corps['tts'], 'stt': corps['stt']}
            return (500, None) if self.audio_500 else (200, corps)
        if chemin.startswith('/api/v1/tools/'):
            reste = chemin[len('/api/v1/tools/'):]
            if reste == 'create':
                self.outils[corps['id']] = corps
                return 200, dict(corps)
            ident = reste.split('/')[1]
            if reste.endswith('/valves/update'):
                self.valves[ident] = corps
                return 200, corps
            if reste.endswith('/valves'):
                return 200, dict(self.valves.get(ident, {'AXIORHUB_API_URL': 'https://courriel.example.com/agent-courriel/api/v1',
                                                          'AXIORHUB_API_TOKEN': '', 'TIMEOUT_SECONDS': 60, 'JOB_WAIT_SECONDS': 240}))
            if reste.endswith('/access/update'):
                self.acces.append(('outil', ident, corps))
                return 200, {}
            if reste.endswith('/update'):
                self.outils[ident] = corps
                return 200, corps
            return (200, dict(self.outils[ident])) if ident in self.outils else (404, {'detail': 'Not found'})
        if chemin.startswith('/api/v1/skills/'):
            reste = chemin[len('/api/v1/skills/'):]
            if reste == 'create':
                self.competences[corps['id']] = corps
                return 200, corps
            ident = reste.split('/')[1]
            if reste.endswith('/access/update'):
                self.acces.append(('competence', ident, corps))
                return 200, {}
            if reste.endswith('/update'):
                self.competences[ident] = corps
                return 200, corps
            return (200, dict(self.competences[ident])) if ident in self.competences else (404, {'detail': 'Not found'})
        if chemin.startswith('/api/v1/models/'):
            if chemin == '/api/v1/models/create':
                self.modeles[corps['id']] = corps
                return 200, corps
            if chemin == '/api/v1/models/model/access/update':
                self.acces.append(('modele', corps['id'], corps))
                return 200, {}
            from urllib.parse import unquote
            ident = unquote(chemin.split('id=', 1)[1])
            if chemin.startswith('/api/v1/models/model/update'):
                self.modeles[ident] = corps
                return 200, corps
            return (200, dict(self.modeles[ident])) if ident in self.modeles else (404, {'detail': 'Not found'})
        if chemin == '/api/v1/prompts/':
            return 200, [dict(v, id=k) for k, v in self.invites.items()]
        if chemin == '/api/v1/prompts/create':
            self.n += 1
            self.invites['p%d' % self.n] = corps
            return 200, dict(corps, id='p%d' % self.n)
        if chemin.startswith('/api/v1/prompts/id/'):
            ident = chemin.split('/')[4]
            if chemin.endswith('/access/update'):
                self.acces.append(('invite', ident, corps))
                return 200, {}
            self.invites[ident] = corps
            return 200, corps
        return 404, {'detail': 'Not found'}


def _entrees(**k):
    e = {'source': 'class Tools:\n    pass\n', 'api_url': 'https://cabinet.example.test/agent-courriel/api/v1', 'jeton': 'jeton-fictif-' + 'x' * 40,
         'systeme': '# Consignes fictives', 'fournisseurs': [], 'cles_fournisseurs': {}, 'ollama_url': '', 'connecteurs': [], 'cles_mcp': {},
         'ecartes_mcp': [], 'competences': [], 'modele_prefere': ''}
    e.update(k)
    return e


class Client(unittest.TestCase):
    def test_adresse_et_cle_controlees(self):
        for mauvaise in ('http://ia.example.test', 'https://u:p@ia.example.test', 'ftp://ia.example.test', 'https://ia.example.test/?x=1', ''):
            with self.assertRaises(R.ErreurOpenWebUI, msg=mauvaise):
                R.Client(mauvaise, 'cle-fictive-admin-123')
        R.Client('http://127.0.0.1:3000', 'cle-fictive-admin-123')                   # instance de ce serveur (bouclage) : HTTP admis
        with self.assertRaises(R.ErreurOpenWebUI):
            R.Client('https://ia.example.test', 'cle avec espace')

    def test_cle_non_administrateur_refusee(self):
        c = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=FauxOpenWebUI(admin=False))
        with self.assertRaisesRegex(R.ErreurOpenWebUI, 'cle_admin_refusee'):
            R.verifier_admin(c)


class Reglage(unittest.TestCase):
    def setUp(self):
        self.o = FauxOpenWebUI()
        self.c = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=self.o)

    def test_reglage_complet_puis_relance_sans_doublon(self):
        reg = {'mistral': {'type': 'mistral', 'model': 'mistral-large-latest', 'secret_file': '/x/mistral.secret'},
               'gpt': {'type': 'chatgpt'}, 'ollama': {'type': 'ollama'}, 'off': {'type': 'openrouter', 'enabled': False}}
        ext = {'a': {'id': 'sources', 'kind': 'connector', 'enabled': True, 'endpoint': 'https://mcp.example.test/mcp', 'auth_type': 'bearer',
                     'name': 'Sources fictives', 'secret_file': '/x/s.secret'},
               'b': {'id': 'oa', 'kind': 'connector', 'enabled': True, 'endpoint': 'https://oa.example.test/mcp', 'auth_type': 'oauth2', 'name': 'OAuth fictif'},
               'c': {'id': 'style', 'kind': 'skill', 'enabled': True, 'reviewed': True, 'skill_text': '# Style fictif', 'name': 'Style'},
               'd': {'id': 'brouillon', 'kind': 'skill', 'enabled': True, 'reviewed': False, 'skill_text': '# Non relu', 'name': 'Non relu'}}
        cons, ec = R.connecteurs_mcp(ext)
        e = _entrees(fournisseurs=R.fournisseurs_compatibles(reg), cles_fournisseurs={'mistral': 'cle-mistral-fictive'},
                     ollama_url='http://host.docker.internal:11434', connecteurs=cons, cles_mcp={'sources': 'jeton-mcp-fictif'}, ecartes_mcp=ec,
                     competences=R.competences_axiorhub(ext), modele_prefere='mistral-large-latest')
        premier = {r['etape']: r['etat'] for r in R.regler(self.c, e)}
        self.assertEqual(self.o.admin_config, {'ENABLE_SIGNUP': True, 'ENABLE_API_KEYS': True, 'DEFAULT_USER_ROLE': 'pending', 'JWT_EXPIRES_IN': '4w'})
        self.assertEqual(premier, {'administration': 'fait', 'permissions': 'fait', 'outil': 'fait', 'connexions': 'fait', 'mcp': 'fait', 'competences': 'fait',
                                   'parametres': 'saute', 'modele': 'fait', 'invites': 'fait'})   # sans « config » : rien à aligner ; sans voix : étape absente
        # outil et ses réglages
        self.assertEqual(self.o.outils['axiorhub']['content'], 'class Tools:\n    pass\n')
        self.assertEqual(self.o.valves['axiorhub']['AXIORHUB_API_URL'], 'https://cabinet.example.test/agent-courriel/api/v1')
        self.assertEqual(self.o.valves['axiorhub']['TIMEOUT_SECONDS'], 60)                     # réglage existant gardé
        # connexions : ChatGPT (abonnement) et fournisseur désactivé jamais repris ; clé seulement si acceptée
        self.assertEqual(self.o.openai['OPENAI_API_BASE_URLS'], ['https://api.openai.com/v1', 'https://api.mistral.ai/v1'])
        self.assertEqual(self.o.openai['OPENAI_API_KEYS'][1], 'cle-mistral-fictive')
        self.assertEqual(self.o.openai['OPENAI_API_CONFIGS']['1']['model_ids'], ['mistral-large-latest'])
        self.assertTrue(self.o.ollama['ENABLE_OLLAMA_API'])
        # MCP : connecteur avec jeton ; OAuth écarté et signalé
        serveurs = self.o.serveurs['TOOL_SERVER_CONNECTIONS']
        self.assertEqual([(s['url'], s['type'], s['auth_type'], s['key']) for s in serveurs],
                         [('https://mcp.example.test/mcp', 'mcp', 'bearer', 'jeton-mcp-fictif')])
        # compétences : seulement celles relues par un avocat
        self.assertEqual(list(self.o.competences), ['axiorhub-style'])
        # modèle : base réellement proposée, invite système, outil, compétence ; par défaut ; tout le cabinet
        m = self.o.modeles['axiorhub-avocat']
        self.assertEqual((m['base_model_id'], m['params']['system'], m['meta']['toolIds'], m['meta']['skillIds']),
                         ('mistral-large-latest', '# Consignes fictives', ['axiorhub'], ['axiorhub-style']))
        self.assertEqual(self.o.config_modeles['DEFAULT_MODELS'], 'axiorhub-avocat')
        self.assertEqual(sorted(x['command'] for x in self.o.invites.values()), ['courriel', 'dossier', 'echeances', 'recherche'])
        for genre, _, corps in self.o.acces:
            self.assertEqual(corps['access_grants'], [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}], genre)
        self.assertEqual({g for g, _, _ in self.o.acces}, {'outil', 'competence', 'modele', 'invite'})
        # relance : rien n'est recréé ni réécrit
        avant = len([x for x in self.o.ecritures if not x[0].endswith('/access/update')])
        second = {r['etape']: r['etat'] for r in R.regler(self.c, e)}
        self.assertEqual(set(second.values()), {'a_jour', 'saute'})
        self.assertEqual(second['parametres'], 'saute')
        self.assertEqual(len([x for x in self.o.ecritures if not x[0].endswith('/access/update')]), avant)
        self.assertEqual(len(self.o.openai['OPENAI_API_BASE_URLS']), 2)
        self.assertEqual(len(self.o.invites), 4)

    def test_sans_cle_acceptee_connexion_ajoutee_desactivee(self):
        e = _entrees(fournisseurs=R.fournisseurs_compatibles({'or': {'type': 'openrouter', 'model': 'qwen/qwen-fictif'}}))
        r = R.connexions(self.c, e['fournisseurs'], {})
        self.assertIn('désactivée', r['detail'])
        self.assertEqual(self.o.openai['OPENAI_API_KEYS'], ['', ''])
        self.assertFalse(self.o.openai['OPENAI_API_CONFIGS']['1']['enable'])

    def test_choses_reglees_par_le_cabinet_conservees(self):
        self.o.openai = {'ENABLE_OPENAI_API': True, 'OPENAI_API_BASE_URLS': ['https://api.mistral.ai/v1'], 'OPENAI_API_KEYS': ['cle-du-cabinet'],
                         'OPENAI_API_CONFIGS': {'0': {'enable': True, 'tags': ['cabinet']}}}
        self.o.serveurs = {'TOOL_SERVER_CONNECTIONS': [{'url': 'https://autre.example.test/mcp', 'type': 'mcp'}]}
        R.connexions(self.c, R.fournisseurs_compatibles({'mistral': {'type': 'mistral'}}), {})
        self.assertEqual(self.o.openai['OPENAI_API_KEYS'], ['cle-du-cabinet'])                 # jamais remplacée par une absence de clé
        self.assertEqual(self.o.openai['OPENAI_API_CONFIGS']['0']['tags'], ['cabinet'])
        R.mcp(self.c, [{'id': 'x', 'nom': 'X', 'url': 'https://mcp.example.test/mcp', 'auth': 'none', 'secret_file': ''}], {})
        self.assertEqual([s['url'] for s in self.o.serveurs['TOOL_SERVER_CONNECTIONS']], ['https://autre.example.test/mcp', 'https://mcp.example.test/mcp'])

    def test_modele_sans_base_disponible_saute(self):
        self.o.base = []
        r = R.regler(self.c, _entrees(), etapes=('modele',))[0]
        self.assertEqual(r['etat'], 'saute')
        self.assertIn('ajoutez une connexion IA', r['detail'])

    def test_etape_refusee_n_arrete_pas_les_suivantes_mais_cle_refusee_si(self):
        def panne(methode, chemin, corps):
            if chemin.startswith('/api/v1/skills/'):
                return 500, {'detail': 'boom'}
            return self.o(methode, chemin, corps)
        c = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=panne)
        out = R.regler(c, _entrees(competences=[{'id': 'axiorhub-x', 'nom': 'X', 'texte': '# x'}]), etapes=('competences', 'invites'))
        self.assertEqual([r['etat'] for r in out], ['echec', 'fait'])
        c2 = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=FauxOpenWebUI(admin=False))
        with self.assertRaisesRegex(R.ErreurOpenWebUI, 'cle_admin_refusee'):
            R.regler(c2, _entrees(), etapes=('connexions',))


class Alignement(unittest.TestCase):
    """Paramètres des modèles et voix identiques entre AxiorHub et Open WebUI (formes relevées sur Open WebUI 0.13.5)."""

    CONFIG = {'state_dir': 'x', 'ollama': {'url': 'http://127.0.0.1:11434', 'model': 'qwen3.8:27b'},
              'model_routing': {'complex_model': 'qwen3.8:27b', 'fast_model': 'qwen3.5:9b',
                                'assistant': {'provider': 'mistral', 'model': 'mistral-small-latest'}},
              'ai_providers': {'mistral': {'type': 'mistral', 'model': 'mistral-small-latest'}}, 'llm': {'profile': 'juridique'},
              'speech568': {'provider': 'kokoro', 'url': 'http://127.0.0.1:8880', 'voice': 'ff_siwis', 'model': 'kokoro'}}

    def setUp(self):
        self.o = FauxOpenWebUI(modeles=('qwen3.8:27b', 'qwen3.5:9b', 'mistral-small-latest'))
        self.o.proprietaires = {'qwen3.8:27b': 'ollama', 'qwen3.5:9b': 'ollama'}
        self.c = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=self.o)

    def test_parametres_du_profil_juridique_filtres_par_fournisseur(self):
        self.assertEqual(R.parametres_axiorhub(self.CONFIG, 'ollama'),
                         {'temperature': 0.3, 'top_p': 0.9, 'top_k': 40, 'min_p': 0.0, 'repeat_penalty': 1.0, 'repeat_last_n': 64,
                          'num_ctx': 32768, 'think': False})
        self.assertEqual(R.parametres_axiorhub(self.CONFIG, 'mistral'), {'temperature': 0.3, 'top_p': 0.9})
        perso = dict(self.CONFIG, llm={'profile': 'personnalise', 'temperature': 0, 'top_p': 0.5})
        self.assertEqual(R.parametres_axiorhub(perso, 'mistral'), {'temperature': 0.0, 'top_p': 1})   # Mistral glouton : top_p 1, comme AxiorHub
        self.assertEqual(R.parametres_axiorhub(perso, 'ollama')['top_p'], 0.5)
        self.assertEqual(R.modeles_axiorhub(self.CONFIG), {'qwen3.8:27b': 'ollama', 'qwen3.5:9b': 'ollama', 'mistral-small-latest': 'mistral'})

    def test_modeles_alignes_puis_a_jour_et_reglages_du_cabinet_gardes(self):
        self.o.modeles['qwen3.8:27b'] = {'id': 'qwen3.8:27b', 'base_model_id': None, 'name': 'Qwen du cabinet', 'meta': {'description': 'gardée'},
                                         'params': {'temperature': 0.9, 'stop': ['<fin>']}}
        r = R.parametres(self.c, R.modeles_axiorhub(self.CONFIG), self.CONFIG)
        self.assertEqual(r['etat'], 'fait')
        q = self.o.modeles['qwen3.8:27b']
        self.assertEqual((q['params']['temperature'], q['params']['num_ctx'], q['params']['think'], q['params']['stop']), (0.3, 32768, False, ['<fin>']))
        self.assertEqual((q['name'], q['meta']), ('Qwen du cabinet', {'description': 'gardée'}))
        self.assertEqual(self.o.modeles['mistral-small-latest']['params'], {'temperature': 0.3, 'top_p': 0.9})
        self.assertEqual(self.o.modeles['qwen3.5:9b']['params']['top_k'], 40)
        self.assertEqual(R.parametres(self.c, R.modeles_axiorhub(self.CONFIG), self.CONFIG)['etat'], 'a_jour')
        absent = dict(self.CONFIG, model_routing={'complex_model': 'modele-absent:1b'})
        r = R.parametres(self.c, R.modeles_axiorhub(absent), absent)
        self.assertIn('Non proposés par Open WebUI : modele-absent:1b', r['detail'])
        self.assertNotIn('modele-absent:1b', self.o.modeles)                       # jamais créé

    def test_modele_axiorhub_avocat_aux_memes_parametres(self):
        e = _entrees(config=self.CONFIG, modele_prefere='qwen3.8:27b')
        R.regler(self.c, e, etapes=('modele',))
        m = self.o.modeles['axiorhub-avocat']
        self.assertEqual((m['base_model_id'], m['params']['temperature'], m['params']['num_ctx'], m['params']['system']),
                         ('qwen3.8:27b', 0.3, 32768, '# Consignes fictives'))

    def test_voix_kokoro_meme_serveur_meme_voix_et_dictee_de_meme_taille(self):
        tts, stt, notes = R.voix_axiorhub(self.CONFIG, {'modele': 'precis'}, 1.2)
        self.assertEqual(tts, {'ENGINE': 'openai', 'OPENAI_API_BASE_URL': 'http://host.docker.internal:8880/v1', 'OPENAI_API_KEY': '',
                               'MODEL': 'kokoro', 'VOICE': 'ff_siwis', 'OPENAI_PARAMS': {'speed': 1.2}})
        self.assertEqual(stt, {'ENGINE': '', 'WHISPER_MODEL': 'large-v3-turbo'})
        r = R.voix(self.c, tts, stt, notes)
        self.assertEqual(r['etat'], 'fait')                                          # 500 rendu par Open WebUI, réglage relu et conforme
        self.assertEqual(self.o.audio['tts']['SPLIT_ON'], 'punctuation')             # réglage d'Open WebUI non concerné : gardé
        self.assertEqual(R.voix(self.c, tts, stt, notes)['etat'], 'a_jour')

    def test_voix_propres_a_axiorhub_et_elevenlabs_sans_accord(self):
        for choix, attendu in (('brindille', 'Brindille'), ('espeak', 'eSpeak'), ('elevenlabs', 'clé ElevenLabs non transmise')):
            tts, stt, notes = R.voix_axiorhub(dict(self.CONFIG, speech568={'provider': choix, 'voice': 'voix-fictive'}), {})
            self.assertEqual(tts, {'ENGINE': ''}, choix)
            self.assertIn(attendu, notes[0])
            self.assertEqual(stt['WHISPER_MODEL'], 'small')                         # dictée « équilibrée » par défaut
        tts, _, _ = R.voix_axiorhub(dict(self.CONFIG, speech568={'provider': 'elevenlabs', 'voice': 'voix-fictive'}), {}, cle_voix='cle-voix-fictive')
        self.assertEqual((tts['ENGINE'], tts['API_KEY'], tts['VOICE']), ('elevenlabs', 'cle-voix-fictive', 'voix-fictive'))
        self.o.audio_500 = True
        refuse = FauxOpenWebUI()
        refuse.audio_500 = False

        def ignore(methode, chemin, corps):   # Open WebUI qui n'enregistre rien : échec dit
            if chemin == '/api/v1/audio/config/update':
                return 500, None
            return refuse(methode, chemin, corps)
        c = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=ignore)
        self.assertEqual(R.voix(c, {'ENGINE': 'openai'}, {'WHISPER_MODEL': 'small'})['etat'], 'echec')

    def test_permissions_cles_d_api_pour_les_comptes(self):
        self.assertEqual(R.permissions(self.c)['etat'], 'fait')
        self.assertEqual(self.o.permissions['features'], {'api_keys': True, 'notes': True})
        self.assertEqual(self.o.permissions['workspace'], {'models': False})
        self.assertEqual(R.permissions(self.c)['etat'], 'a_jour')


class InstanceExistante(unittest.TestCase):
    """Instance déjà en place, réglée à la main : rien de doublé, rien de retiré, choix du cabinet gardés."""

    def setUp(self):
        self.o = FauxOpenWebUI()
        self.c = R.Client('https://ia.example.test', 'cle-fictive-admin-123', transport=self.o)

    def test_outil_importe_a_la_main_mis_a_jour_sans_doublon(self):
        self.o.outils['axiorhub_avocat'] = {'id': 'axiorhub_avocat', 'name': 'AxiorHub Avocat (copie)', 'content': 'AXIORHUB_API_TOKEN = ancien',
                                            'meta': {'description': 'du cabinet'}}
        self.o.outils['autre_outil'] = {'id': 'autre_outil', 'name': 'Agenda', 'content': 'rien'}
        r = R.outil(self.c, 'class Tools:  # AXIORHUB_API_TOKEN\n    pass\n', 'https://cabinet.example.test/agent-courriel/api/v1', 'j' * 48)
        self.assertEqual(r['etat'], 'fait')
        self.assertIn('axiorhub_avocat', r['detail'])
        self.assertEqual(sorted(self.o.outils), ['autre_outil', 'axiorhub_avocat'])        # aucun outil « axiorhub » ajouté
        self.assertEqual(self.o.outils['axiorhub_avocat']['meta'], {'description': 'du cabinet'})
        self.assertEqual(self.o.valves['axiorhub_avocat']['AXIORHUB_API_TOKEN'], 'j' * 48)
        R.modele(self.c, '# Consignes', 'mistral-large-latest', outil_id=R.outil_existant(self.c))
        self.assertEqual(self.o.modeles['axiorhub-avocat']['meta']['toolIds'], ['axiorhub_avocat'])

    def test_modele_par_defaut_du_cabinet_garde(self):
        self.o.config_modeles['DEFAULT_MODELS'] = 'qwen3.8:27b'
        R.modele(self.c, '# Consignes', 'mistral-large-latest')
        self.assertEqual(self.o.config_modeles['DEFAULT_MODELS'], 'qwen3.8:27b')

    def test_inscription_fermee_seulement_si_accepte(self):
        r = R.administration(self.c, False)
        self.assertEqual((self.o.admin_config['ENABLE_API_KEYS'], self.o.admin_config['ENABLE_SIGNUP']), (True, True))
        self.assertNotIn('inscription', r['detail'])
        R.administration(self.c, True)
        self.assertFalse(self.o.admin_config['ENABLE_SIGNUP'])
        self.assertEqual(self.o.admin_config['JWT_EXPIRES_IN'], '4w')                       # le reste du réglage est gardé
        self.assertEqual(R.administration(self.c, True)['etat'], 'a_jour')

    def test_connexion_de_l_administrateur_reprend_sa_cle(self):
        cmd = _module('configure_openwebui_cnx', 'configure-openwebui.py')
        vus = []

        def transport(methode, chemin, corps):
            vus.append((methode, chemin))
            if chemin == '/api/v1/auths/signin':
                return (200, {'token': 'jeton-session-fictif', 'role': 'admin'}) if corps['password'] == 'mot-fictif' else (400, {'detail': 'x'})
            if chemin == '/api/v1/auths/api_key' and methode == 'GET':
                return 200, {'api_key': 'cle-deja-utilisee-ailleurs'}
            return self.o(methode, chemin, corps)
        vrai = R.Client.__init__

        def init(client, base, cle, delai=60, transport_=None):
            vrai(client, base, cle, delai, transport)
        with patch.object(R.Client, '__init__', init):
            lignes = []
            self.assertEqual(cmd.cle_par_connexion('https://ia.example.test', 'admin@example.test', 'mot-fictif', lignes.append),
                             'cle-deja-utilisee-ailleurs')                                 # jamais remplacée
            self.assertTrue(self.o.admin_config['ENABLE_API_KEYS'])
            self.assertNotIn(('POST', '/api/v1/auths/api_key'), vus)
            with self.assertRaisesRegex(RuntimeError, 'Connexion refusée'):
                cmd.cle_par_connexion('https://ia.example.test', 'admin@example.test', 'mauvais', lignes.append)


class Commande(unittest.TestCase):
    def setUp(self):
        self.cmd = _module('configure_openwebui_essai', 'configure-openwebui.py')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        import hashlib
        self.jeton = 'j' * 48
        (d / 'ui-auth.json').write_text(json.dumps({'origin': 'https://cabinet.example.test', 'prefix': '/agent-courriel',
                                                    'api_token_sha256': hashlib.sha256(self.jeton.encode()).hexdigest()}), encoding='utf-8')
        (d / 'token').write_text(self.jeton + '\n', encoding='utf-8')
        (d / 'state').mkdir()
        (d / 'config.json').write_text(json.dumps({'state_dir': str(d / 'state'), 'ollama': {'url': 'http://127.0.0.1:11434'}}), encoding='utf-8')
        for nom, val in (('AUTH', d / 'ui-auth.json'), ('TOKEN', d / 'token'), ('CONFIG', d / 'config.json'), ('BASE', ROOT)):
            p = patch.object(self.cmd, nom, val)
            p.start()
            self.addCleanup(p.stop)
        self.o = FauxOpenWebUI()
        p = patch.object(R.Client, 'appel', lambda client, m, c, corps=None: self.o(m, c, corps))
        p.start()
        self.addCleanup(p.stop)
        p = patch('agent.common.load_config', lambda path: json.loads(Path(path).read_text(encoding='utf-8')))
        p.start()
        self.addCleanup(p.stop)
        self.lances = []
        p = patch.object(self.cmd.subprocess, 'run', lambda args, **k: (self.lances.append(args), type('R', (), {'returncode': 0})())[1])
        p.start()
        self.addCleanup(p.stop)

    def test_root_exige(self):
        with self.assertRaisesRegex(RuntimeError, 'sudo'):
            self.cmd.main(['--oui', '--url', 'https://ia.example.test'], racine=1000)

    def test_non_interactif_sans_cle_transmise_et_adresse_enregistree(self):
        lignes = []
        code = self.cmd.main(['--oui', '--url', 'https://ia.example.test'], entree=lambda q: self.fail('aucune question'),
                             motdepasse=lambda q: 'cle-admin-fictive-01', sortie=lignes.append, racine=0)
        self.assertEqual(code, 0)
        self.assertEqual(self.o.valves['axiorhub']['AXIORHUB_API_TOKEN'], self.jeton)
        self.assertEqual(self.o.valves['axiorhub']['AXIORHUB_API_URL'], 'https://cabinet.example.test/agent-courriel/api/v1')
        self.assertIn('http://host.docker.internal:11434', self.o.ollama['OLLAMA_BASE_URLS'])   # bouclage d'AxiorHub vu depuis le conteneur
        self.assertTrue(any(a[-3:] == ['set-url', 'openwebui', 'https://ia.example.test'] for a in self.lances))
        texte = '\n'.join(lignes)
        self.assertIn("frame-ancestors 'self' https://cabinet.example.test", texte)
        self.assertNotIn('cle-admin-fictive-01', texte)
        self.assertNotIn(self.jeton, texte)

    def test_jeton_ne_correspondant_pas_arret(self):
        Path(self.cmd.TOKEN).write_text('autre' * 10, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'install-interface'):
            self.cmd.main(['--oui', '--url', 'https://ia.example.test'], motdepasse=lambda q: 'cle-admin-fictive-01', sortie=lambda s: None, racine=0)

    def test_refus_de_confirmation_rien_n_est_modifie(self):
        reponses = iter(['o', '-', 'n'])   # inscription, Ollama, appliquer
        code = self.cmd.main(['--url', 'https://ia.example.test'], entree=lambda q: next(reponses), motdepasse=lambda q: 'cle-admin-fictive-01',
                             sortie=lambda s: None, racine=0)
        self.assertEqual(code, 1)
        self.assertEqual([c for c, _ in self.o.ecritures], [])

    def test_ollama_vu_depuis_open_webui(self):
        self.assertEqual(self.cmd.ollama_vu_depuis_openwebui('http://127.0.0.1:11434'), 'http://host.docker.internal:11434')
        self.assertEqual(self.cmd.ollama_vu_depuis_openwebui('http://ollama.example.test:11434/'), 'http://ollama.example.test:11434')
        self.assertEqual(self.cmd.ollama_vu_depuis_openwebui(''), '')

    def test_mode_auto_sans_cle_gardee_ne_fait_rien(self):
        lignes = []
        self.assertEqual(self.cmd.main(['--auto', '--url', 'https://ia.example.test'], entree=lambda q: self.fail('aucune question'),
                                       motdepasse=lambda q: self.fail('aucune clé demandée'), sortie=lignes.append, racine=0), 0)
        self.assertIn('rien à synchroniser', lignes[-1])
        self.assertEqual(self.o.ecritures, [])

    def test_mode_auto_avec_cle_gardee_outil_et_reglages_sans_question(self):
        with patch.object(self.cmd, 'cle_admin_gardee', lambda state: 'cle-admin-gardee-01'):
            code = self.cmd.main(['--auto', '--url', 'https://ia.example.test'], entree=lambda q: self.fail('aucune question'),
                                 motdepasse=lambda q: self.fail('aucune clé demandée'), sortie=lambda s: None, racine=0)
        self.assertEqual(code, 0)
        self.assertEqual(self.o.valves['axiorhub']['AXIORHUB_API_TOKEN'], self.jeton)   # outil remis à jour à chaque mise à jour d'AxiorHub
        self.assertFalse(any(a[-1:] == ['openwebui-admin'] for a in self.lances))         # rien à ranger : la clé l'est déjà

    def test_cle_rangee_par_le_service_avec_l_accord(self):
        reponses = iter(['o', '-', 'o'])
        self.cmd.main(['--url', 'https://ia.example.test'], entree=lambda q: next(reponses), motdepasse=lambda q: 'cle-admin-fictive-01',
                      sortie=lambda s: None, racine=0)
        rangement = [a for a in self.lances if a[-1:] == ['openwebui-admin']]
        self.assertEqual(len(rangement), 1)
        self.assertEqual(rangement[0][:4], ['/usr/sbin/runuser', '-u', 'axiorhub-mail', '--'])   # rangée par le compte du service

    def test_enveloppe_axiorhub_mail(self):
        texte = (ROOT / 'deploy' / 'axiorhub-mail').read_text(encoding='utf-8')
        self.assertIn('configure|mode|discover|link|update|rollback|openwebui)', texte)   # root exigé
        self.assertIn('/opt/axiorhub-mail-agent/current/configure-openwebui.py "$@"', texte)


class Installation(unittest.TestCase):
    def setUp(self):
        self.ins = _module('install_openwebui_essai', 'install-openwebui.py')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        (d / 'ui-auth.json').write_text(json.dumps({'origin': 'https://cabinet.example.test'}), encoding='utf-8')
        for nom, val in (('AUTH', d / 'ui-auth.json'), ('ENV', d / 'openwebui.env'), ('ETAT', d / 'openwebui.json'), ('SITE', d / 'site.conf'),
                         ('SAUVEGARDES', d / 'sauvegardes')):
            p = patch.object(self.ins, nom, val)
            p.start()
            self.addCleanup(p.stop)
        self.cmds = []
        self.etat = {'conteneur': None, 'image': False, 'sain': True}

        def lancer(args, delai=600, verifier=True):
            self.cmds.append(args)

            class R_:
                returncode, stdout, stderr = 0, '', ''
            r = R_()
            if args[:3] == ['docker', 'container', 'inspect']:
                if self.etat['conteneur'] is None:
                    r.returncode = 1
                else:
                    r.stdout = json.dumps([self.etat['conteneur']])
            elif args[:3] == ['docker', 'image', 'inspect']:
                r.returncode = 0 if self.etat['image'] else 1
                r.stdout = 'sha256:' + 'b' * 64   # identifiant de l'image de cette version
            elif args[:2] == ['docker', 'run'] and '--name' in args:
                self.etat['conteneur'] = {'Id': 'nouveau', 'Config': {'Labels': {'axiorhub': 'openwebui'}, 'Image': args[-1]},
                                          'HostConfig': {'PortBindings': {'8080/tcp': [{'HostPort': args[args.index('-p') + 1].split(':')[1]}]}},
                                          'State': {'Running': True}}
            elif args[:2] == ['apache2ctl', '-S']:
                r.stdout = 'cabinet.example.test'
            return r
        p = patch.object(self.ins, 'lancer', lancer)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.ins, 'sain', lambda port, **k: self.etat['sain'])
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(self.ins, 'port_libre', lambda ports=None: 3000)
        p.start()
        self.addCleanup(p.stop)
        self.vrai_premier = self.ins.premier_administrateur
        p = patch.object(self.ins, 'premier_administrateur', lambda port, courriel, ouvrir=None: None)   # aucun réseau : remplacé où il est essayé
        p.start()
        self.addCleanup(p.stop)

    def test_installation_image_figee_ecoute_locale_et_env_prive(self):
        lignes = []
        self.assertEqual(self.ins.main(['--sans-apache'], sortie=lignes.append, racine=0), 0)
        self.assertIn(['docker', 'pull', self.ins.IMAGE], self.cmds)
        run = next(a for a in self.cmds if a[:2] == ['docker', 'run'])
        self.assertEqual(run[-1], 'ghcr.io/open-webui/open-webui@sha256:e0dc5a8bf5c71cadf339d1e7b9408b357da75fb2854f82cad57a1609fadf2a22')
        for attendu in (['--pull', 'never'], ['-p', '127.0.0.1:3000:8080'], ['--label', 'axiorhub=openwebui'], ['-v', 'axiorhub-openwebui:/app/backend/data']):
            i = run.index(attendu[0])
            self.assertEqual(run[i:i + 2], attendu)
        env = Path(self.ins.ENV).read_text(encoding='utf-8')
        self.assertIn("CONTENT_SECURITY_POLICY=frame-ancestors 'self' https://cabinet.example.test\n", env)
        self.assertIn('ENABLE_API_KEYS=true', env)
        self.assertIn('DEFAULT_LOCALE=fr-FR', env)
        secret = [l for l in env.splitlines() if l.startswith('WEBUI_SECRET_KEY=')][0]
        self.assertGreater(len(secret), 50)
        self.assertNotIn(secret.split('=', 1)[1], '\n'.join(lignes))
        if hasattr(Path(self.ins.ENV).stat(), 'st_mode') and __import__('os').name != 'nt':
            self.assertEqual(Path(self.ins.ENV).stat().st_mode & 0o777, 0o600)
        self.assertIn('sudo axiorhub-mail openwebui', '\n'.join(lignes))
        # le secret est gardé d'une écriture à l'autre
        self.assertEqual(self.ins.secret_existant(Path(self.ins.ENV)), secret.split('=', 1)[1])

    def test_premier_administrateur_puis_cle(self):
        reponses = []

        class Rep:
            def __init__(self, statut, corps):
                self.status, self.corps = statut, corps

            def read(self):
                return json.dumps(self.corps).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def ouvrir(req, timeout=None):
            reponses.append((req.get_method(), req.full_url, dict(req.header_items()), json.loads(req.data) if req.data else None))
            if req.full_url.endswith('/auths/signup'):
                return Rep(200, {'role': 'admin', 'token': 'jeton-session-fictif'})
            return Rep(200, {'api_key': 'cle-admin-creee-01'})
        out = self.vrai_premier(3000, 'admin@ia.example.test', ouvrir)
        self.assertEqual((out['courriel'], out['cle']), ('admin@ia.example.test', 'cle-admin-creee-01'))
        self.assertGreater(len(out['mot_de_passe']), 15)
        self.assertEqual([r[1] for r in reponses], ['http://127.0.0.1:3000/api/v1/auths/signup', 'http://127.0.0.1:3000/api/v1/auths/api_key'])
        self.assertEqual(reponses[1][2].get('Authorization'), 'Bearer jeton-session-fictif')

        def deja(req, timeout=None):
            return Rep(200, {'role': 'user', 'token': 'x'})                      # un compte existait : le nouveau n'est pas administrateur
        self.assertIsNone(self.vrai_premier(3000, 'admin@ia.example.test', deja))

    def test_installation_neuve_cle_en_main(self):
        appels = []
        with patch.object(self.ins, 'premier_administrateur', lambda port, courriel, ouvrir=None: {'courriel': courriel, 'mot_de_passe': 'mot-fictif-0123456',
                                                                                                    'cle': 'cle-admin-creee-01'}):
            import types
            faux = types.SimpleNamespace(main=lambda argv, sortie=None, racine=None, cle=None: appels.append((argv, cle)) or 0)
            with patch('importlib.util.module_from_spec', lambda spec: faux), patch('importlib.util.spec_from_file_location',
                                                                                  lambda *a, **k: types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda m: None))):
                lignes = []
                self.assertEqual(self.ins.main(['--sans-apache'], sortie=lignes.append, racine=0), 0)
        self.assertEqual(appels, [(['--url', 'http://127.0.0.1:3000'], 'cle-admin-creee-01')])
        self.assertIn('mot de passe initial mot-fictif-0123456', '\n'.join(lignes))

    def test_conteneur_etranger_jamais_touche(self):
        self.etat['conteneur'] = {'Id': 'autre', 'Config': {'Labels': {}, 'Image': 'x'}, 'HostConfig': {}, 'State': {'Running': True}}
        with self.assertRaisesRegex(RuntimeError, 'sans l’étiquette'):
            self.ins.main([], sortie=lambda s: None, racine=0)
        self.assertFalse(any(a[:2] in (['docker', 'run'], ['docker', 'stop'], ['docker', 'rm']) for a in self.cmds))

    def test_mise_a_jour_sauvegarde_puis_retour_si_la_nouvelle_ne_repond_pas(self):
        self.etat['image'] = True
        self.etat['conteneur'] = {'Id': 'ancien', 'Config': {'Labels': {'axiorhub': 'openwebui'}, 'Image': 'ghcr.io/open-webui/open-webui@sha256:' + 'a' * 64},
                                  'HostConfig': {'PortBindings': {'8080/tcp': [{'HostPort': '3005'}]}}, 'State': {'Running': True}}
        self.etat['sain'] = False
        with self.assertRaisesRegex(RuntimeError, 'ancienne est remise'):
            self.ins.main(['--mettre-a-jour'], sortie=lambda s: None, racine=0)
        tar = next(a for a in self.cmds if '--entrypoint' in a)
        self.assertIn('czf', tar)
        self.assertIn('axiorhub-openwebui:/donnees:ro', tar)
        self.assertIn(['docker', 'rename', 'ancien', 'axiorhub-openwebui'], self.cmds)   # ancien remis sous son nom
        self.assertIn(['docker', 'start', 'ancien'], self.cmds)
        self.assertIn(['docker', 'run', '-d', '--name', 'axiorhub-openwebui', '--label', 'axiorhub=openwebui', '--restart', 'unless-stopped',
                       '--pull', 'never', '-p', '127.0.0.1:3005:8080', '--add-host', 'host.docker.internal:host-gateway', '-v',
                       'axiorhub-openwebui:/app/backend/data', '--env-file', str(self.ins.ENV), self.ins.IMAGE], self.cmds)   # même port

    def test_site_apache_sans_certificat_rien_ecrit(self):
        lignes = []
        self.assertFalse(self.ins.publier('ia.example.test', 3000, lignes.append))
        self.assertIn('certbot certonly --apache -d ia.example.test', '\n'.join(lignes))
        self.assertFalse(Path(self.ins.SITE).exists())
        self.assertFalse(self.ins.publier('cabinet.example.test', 3000, lignes.append))   # nom déjà servi : pas modifié
        self.assertIn('déjà servi', lignes[-1])

    def test_site_apache_texte(self):
        texte = self.ins.site_apache('ia.example.test', 3002, '/c/full.pem', '/c/key.pem')
        self.assertTrue(texte.startswith(self.ins.MARQUE))
        self.assertIn('ProxyPass "/" "http://127.0.0.1:3002/" upgrade=websocket', texte)
        self.assertIn('ServerName ia.example.test', texte)
        with self.assertRaises(RuntimeError):
            self.ins.site_apache('ia example', 3002, '/c', '/k')
        Path(self.ins.SITE).write_text('# autre site\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'sans écrasement'):
            self.ins.publier('ia.example.test', 3000, lambda s: None)


if __name__ == '__main__':
    unittest.main()


class Lanceur(unittest.TestCase):
    """5.6.112 : la commande axiorhub-mail d'un serveur mis à jour reçoit la sous-commande « openwebui »."""

    def test_mise_a_jour_reecrit_la_commande_installee_avec_copie(self):
        import upgrade
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            release, sauvegardes = d / 'release', d / 'backups'
            (release / 'deploy').mkdir(parents=True)
            sauvegardes.mkdir()
            livree = (ROOT / 'deploy' / 'axiorhub-mail').read_bytes()
            self.assertIn(b'openwebui)', livree.replace(b'|openwebui)', b'openwebui)'))
            (release / 'deploy' / 'axiorhub-mail').write_bytes(livree)
            ancienne = b'#!/usr/bin/env bash\ncase "$1" in configure|mode) exec true ;; esac\n'
            commande = d / 'bin' / 'axiorhub-mail'
            commande.parent.mkdir()
            commande.write_bytes(ancienne)
            copie = upgrade.refresh_launcher(release, commande, sauvegardes)
            self.assertEqual(commande.read_bytes(), livree)
            self.assertEqual(Path(copie).read_bytes(), ancienne)
            self.assertIsNone(upgrade.refresh_launcher(release, commande, sauvegardes))      # idempotent
            self.assertIsNone(upgrade.refresh_launcher(release, d / 'bin' / 'absente', sauvegardes))
            self.assertFalse((d / 'bin' / 'absente').exists())                               # jamais ajoutée

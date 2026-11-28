"""5.6.112 : survol lisible des onglets du contenu ; « Conversations IA » (Open WebUI du cabinet dans un cadre) : page du menu, onglet du
dossier, conversation propre au dossier retrouvée ou créée avec la clé d'API de la personne, test de l'affichage.

Services réels (``conversations56112``, ``dossier5679``, ``shell501``, routage ``web440.api`` et ``standalone_auth.allowed``, coffre
``vault567``) ; Open WebUI est un faux (``http.client.HTTPSConnection`` remplacé) : aucun réseau, aucun service réel. Données fictives
(DURAND, MARTIN, BERNARD, ZORVAL, DOS-001, DOS-002, example.test).
"""
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from agent import conversations56112 as CV, dossier5679, shell501, standalone_auth, web440
from agent.common import Stop
from test_v56111_conversation import ADMIN, BERNARD, DURAND, JETON, MARTIN, ORIGINE, PREFIX, Base as Base111, _reseau_interdit
import socket


def setUpModule():   # aucun réseau : tout appel de socket qui échapperait au faux Open WebUI échoue
    global _p1, _p2
    _p1 = patch.object(socket.socket, 'connect', _reseau_interdit)
    _p2 = patch.object(socket, 'create_connection', _reseau_interdit)
    _p1.start()
    _p2.start()


def tearDownModule():
    _p1.stop()
    _p2.stop()

ROOT = Path(__file__).resolve().parent.parent
OWUI = 'https://ia.cabinet.example.test'
CLE = 'cle-fictive-openwebui-0123456789'


class FauxOpenWebUI:
    """Faux serveur HTTPS : réponses programmées par (méthode, chemin) ; garde chaque requête (méthode, chemin, en-têtes, corps)."""
    reponses = {}
    requetes = []
    panne = False

    def __init__(self, hote, port=443, timeout=None, context=None):
        self.hote, self.port = hote, port
        self.courante = None

    def request(self, methode, chemin, body=None, headers=None):
        if FauxOpenWebUI.panne:
            raise OSError('connexion refusée (faux)')
        corps = json.loads(body.decode('utf-8')) if body else None
        FauxOpenWebUI.requetes.append((methode, chemin, dict(headers or {}), corps, self.hote))
        self.courante = FauxOpenWebUI.reponses.get((methode, chemin), (404, {'detail': 'Not Found'}, []))

    def getresponse(self):
        statut, corps, entetes = self.courante
        brut = json.dumps(corps).encode('utf-8') if corps is not None else b''

        class R:
            status = statut

            def read(self, n=-1):
                return brut

            def getheaders(self):
                return list(entetes)
        return R()

    def close(self):
        pass


class Base(Base111):
    def setUp(self):
        super().setUp()
        FauxOpenWebUI.reponses, FauxOpenWebUI.requetes, FauxOpenWebUI.panne = {}, [], False
        p = patch('http.client.HTTPSConnection', FauxOpenWebUI)
        p.start()
        self.addCleanup(p.stop)

    def regler(self, url=OWUI + '/'):
        self.d.setting('workstation:url:openwebui', url)

    def cle(self, qui=DURAND, cle=CLE):
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/?page=1')] = (200, [], [])   # route d'identité refusée aux clés (0.13.5)
        return self.api_cv('cle', qui, {'cle': cle})

    def api_cv(self, action, qui=DURAND, data=None, methode='POST', csrf=JETON):
        corps = json.dumps(data if data is not None else {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': qui[0], 'HTTP_X_AXIORHUB_ROLE': qui[1], 'HTTP_ORIGIN': ORIGINE, 'HTTP_X_CSRF_TOKEN': csrf,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(corps)), 'wsgi.input': io.BytesIO(corps), 'REQUEST_METHOD': methode,
               'HTTP_HOST': 'cabinet.example.test'}
        with self.requete(qui):
            out = web440.api(env, self.d, self.auth, PREFIX, CV.PREFIXE + action, {}, methode)
        out['json'] = json.loads(out['body'])
        out['code'] = out['status'][:3]
        return out

    def ouvrir(self, qui=DURAND, dossier='DOS-001'):
        return self.api_cv('ouvrir', qui, {'dossier': dossier})

    def onglet(self, qui=DURAND, vue='conversation'):
        from agent import i18n5631
        shell = lambda titre, body, prefix, csrf, actif, head='': head + body
        i18n5631.begin({}, self.d.c, qui[0], qui[1])
        with self.requete(qui):
            return dossier5679.page(self.d, {'owner': qui[0], 'csrf': JETON}, PREFIX, {'id': 'DOS-001', 'vue': vue}, shell)

    def page(self, qui=DURAND, args=None):
        from agent import i18n5631
        i18n5631.begin({}, self.d.c, qui[0], qui[1])
        env = {'HTTP_X_AXIORHUB_USER': qui[0], 'HTTP_X_AXIORHUB_ROLE': qui[1]}
        with self.requete(qui):
            return CV.page_html(self.d, self.auth, PREFIX, env, args or {})


# ================================================================================================ survol des onglets
class Survol(unittest.TestCase):
    def test_regle_historique_limitee_a_la_barre_laterale(self):
        style = (ROOT / 'agent' / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('aside nav a:hover{background:#254750}', style)
        self.assertNotRegex(style, r'(^|[};\n])nav a:hover\{background:#254750\}')
        v151 = (ROOT / 'agent' / 'static' / 'v151.css').read_text(encoding='utf-8')
        self.assertIn('aside nav a:hover{background:#438ae0}', v151)
        self.assertNotRegex(v151, r'(^|[};\n])nav a:hover\{background:#438ae0\}')
        bundle = (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertNotRegex(bundle, r'(^|\n)nav a:hover\{background:#438ae0\}')

    def test_onglets_du_contenu_au_fond_doux_et_feuille_unique(self):
        css = (ROOT / 'agent' / 'static' / 'v56112.css').read_text(encoding='utf-8')
        self.assertIn(':where(#ws-main nav) a:hover{background:var(--ui-marque-doux,#edf5ff)}', css)
        self.assertIn(':root body[data-carbon] .ws-topbar-actions > a:hover{color:var(--ui-marque-survol,#0043ce)}', css)
        self.assertNotIn(':root[data-theme=dark] :where', css)                   # préfixe de thème sous :where : spécificité nulle
        self.assertEqual(shell501.BUNDLE[-1], 'v56112.css')                      # chargée en dernier : rien ne la recouvre par l'ordre
        self.assertIn('/* ==== v56112.css ==== */', (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8'))


# ================================================================================================ menu, page, onglet
class Page(Base):
    def test_menu_entree_apres_la_messagerie_icone_et_traductions(self):
        from agent import fonctions5631, i18n5631, ui_navigation5689
        self.assertEqual(ui_navigation5689.ICONES_MENU['/conversations-ia'], 'ia')
        self.assertIn('conversations_ia', [x[0] for x in fonctions5631.HORS_REGISTRE])   # trouvable par Ctrl+K
        for langue in ('fr', 'en', 'de', 'es'):
            data = json.loads((ROOT / 'agent' / 'locales' / ('%s.json' % langue)).read_text(encoding='utf-8'))
            self.assertTrue(data.get('fn.conversations_ia.label'), langue)
        i18n5631.begin({}, self.d.c, 'cabinet', 'administrateur')
        nav = shell501.nav_html(PREFIX, '/conversations-ia')
        ordre = re.findall(r'href="/agent-courriel(/[a-z-]+)"', nav.split('class="ws-primary"')[1].split('</nav>')[0])
        self.assertEqual(ordre[ordre.index('/messagerie') + 1], '/conversations-ia')
        self.assertIn('aria-current="page"', nav.split('/conversations-ia"')[1].split('>', 1)[0])

    def test_sans_adresse_la_page_explique_sans_cadre(self):
        html = self.page(ADMIN)
        self.assertNotIn('<iframe', html)
        self.assertIn('Paramètres › Connexions', html)
        self.regler('https://ai.example.com/')                                   # adresse d'exemple : toujours « non réglée »
        self.assertNotIn('<iframe', self.page(ADMIN))
        self.assertIn('Demandez à l’administrateur', self.page(BERNARD))           # un assistant n'a pas accès aux Paramètres

    def test_cadre_de_l_instance_et_reglages_reserves(self):
        self.regler()
        html = self.page(ADMIN)
        self.assertIn('<iframe class="cv112-cadre" data-cv112-cadre title="Open WebUI — conversations IA du cabinet" src="%s/"' % OWUI, html)
        self.assertIn('allow="microphone; clipboard-read; clipboard-write; fullscreen"', html)
        self.assertIn('Ouvrir dans un nouvel onglet', html)
        self.assertIn('data-api="m56112/conversations/tester"', html)
        self.assertIn('>Réglages</a>', html)
        self.assertNotIn('>Réglages</a>', self.page(BERNARD))
        self.assertIn('<iframe', self.page(BERNARD))

    def test_page_sur_un_dossier_ouvre_par_le_script(self):
        self.regler()
        html = self.page(DURAND, {'dossier': 'DOS-001'})
        self.assertIn('data-cv112-dossier="DOS-001"', html)
        self.assertNotIn(' src="', html.split('data-cv112-cadre', 1)[1].split('>', 1)[0])   # le script pose l'adresse
        self.assertNotIn('data-cv112-dossier', self.page(DURAND, {'dossier': 'INCONNU'}))     # dossier inconnu : page générale

    def test_onglet_du_dossier_remplace_et_missions_conservees(self):
        self.regler()
        html = self.onglet()
        self.assertIn('aria-current="page">Conversation IA</a>', html)
        self.assertIn('data-cv112-dossier="DOS-001"', html)
        self.assertIn('v56112-conversations.js', html)
        self.assertIn('vue=missions-ia', html)
        self.assertNotIn('id="c111"', html)
        self.assertIn('id="c111"', self.onglet(vue='missions-ia'))               # vue de la 5.6.111, intacte
        self.assertIn('%s/?q=Dossier%%20DOS-001%%20%%3A%%20' % OWUI, html)          # nouvel onglet : saisie préremplie, rien d'envoyé

    def test_onglet_sans_adresse(self):
        html = self.onglet()
        self.assertNotIn('<iframe', html)
        self.assertIn('vue=missions-ia', html)

    def test_carte_des_parametres_commandes_et_test(self):
        html = CV.carte_admin_html(self.d, PREFIX)
        self.assertIn('Adresse d’Open WebUI pas encore réglée', html)
        self.assertIn('install-openwebui.py --nom', html)
        self.assertIn('sudo axiorhub-mail openwebui', html)
        self.assertNotIn('m56112/conversations/tester', html)                    # rien à tester sans adresse
        self.regler()
        html = CV.carte_admin_html(self.d, PREFIX)
        self.assertIn('href="%s/"' % OWUI, html)
        self.assertIn('href="/agent-courriel/conversations-ia"', html)
        self.assertIn('data-api="m56112/conversations/tester"', html)
        from agent import workstation32_ui
        self.assertIn('carte_admin_html', (ROOT / 'agent' / 'workstation32_ui.py').read_text(encoding='utf-8'))

    def test_script_declare_et_servi(self):
        self.assertEqual(web440.STATIC.get('/static/v56112-conversations.js'), 'text/javascript')
        js = (ROOT / 'agent' / 'static' / 'v56112-conversations.js').read_text(encoding='utf-8')
        self.assertIn("'/api440/m56112/conversations/ouvrir'", js)
        self.assertIn('origine(url) !== attendue', js)                           # seule l'origine de l'instance est chargée dans le cadre
        self.assertNotIn('innerHTML', js)


# ================================================================================================ routes
class Routes(Base):
    def test_roles_methodes_et_csrf(self):
        for role in ('administrateur', 'avocat', 'assistant'):
            for action in (x for x in CV.ECRITURES if x not in CV.ADMIN or role == 'administrateur'):
                self.assertTrue(standalone_auth.allowed(role, 'POST', '/api440/m56112/conversations/' + action), (role, action))
            if role != 'administrateur':   # l'administrateur passe le contrôle de rôle ; la route inconnue rend 404 (plus bas)
                self.assertFalse(standalone_auth.allowed(role, 'POST', '/api440/m56112/conversations/inconnue'))
        self.assertFalse(standalone_auth.allowed('invite', 'POST', '/api440/m56112/conversations/ouvrir'))
        self.regler()
        self.assertEqual(self.api_cv('inconnue')['code'], '404')
        self.assertEqual(self.api_cv('ouvrir', methode='GET')['code'], '404')
        out = self.api_cv('ouvrir', data={'dossier': 'DOS-001'}, csrf='mauvais')
        self.assertEqual(out['code'], '400')
        self.assertIn(('Cache-Control', 'private, no-store'), out['headers'])
        self.assertEqual(FauxOpenWebUI.requetes, [])

    def test_dossier_inconnu_refus_neutre(self):
        self.regler()
        out = self.ouvrir(dossier='DOS-999')
        self.assertEqual((out['code'], out['json']['error']), ('404', 'introuvable'))
        self.assertEqual(self.ouvrir(dossier='../x')['code'], '404')

    def test_sans_adresse(self):
        out = self.ouvrir()
        self.assertEqual((out['code'], out['json']['error']), ('409', 'openwebui_non_configure'))


# ================================================================================================ conversation propre au dossier
class ConversationDuDossier(Base):
    def setUp(self):
        super().setUp()
        self.regler()

    def test_sans_cle_saisie_preremplie_et_aucun_appel(self):
        out = self.ouvrir()
        self.assertEqual(out['code'], '200')
        self.assertEqual(out['json']['mode'], 'saisie')
        self.assertEqual(out['json']['url'], OWUI + '/?q=Dossier%20DOS-001%20%3A%20')
        self.assertEqual(FauxOpenWebUI.requetes, [])

    def test_cle_verifiee_chiffree_jamais_rendue(self):
        out = self.cle()
        self.assertEqual((out['code'], out['json']['enregistree']), ('200', True))
        self.assertNotIn(CLE, out['body'])
        methode, chemin, entetes, _, hote = FauxOpenWebUI.requetes[-1]
        self.assertEqual((methode, chemin, entetes['Authorization'], hote), ('GET', '/api/v1/chats/?page=1', 'Bearer ' + CLE, 'ia.cabinet.example.test'))
        fichier = CV._chemin_cle(self.d, DURAND[0])
        brut = fichier.read_text(encoding='utf-8')
        self.assertTrue(brut.startswith('AXIORHUB-VAULT-V1:'))
        self.assertNotIn(CLE, brut)
        self.assertNotIn('durand', str(fichier).lower())                          # nom du fichier : empreinte, pas l'adresse
        self.assertEqual(CV.cle_api(self.d, DURAND[0]), CLE)
        self.assertEqual(CV.cle_api(self.d, MARTIN[0]), '')                        # clé propre à la personne

    def test_cle_refusee_ou_mal_formee_non_enregistree(self):
        self.assertEqual(self.api_cv('cle', DURAND, {'cle': 'x y'})['json']['error'], 'cle_invalide')
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/?page=1')] = (401, {'detail': 'invalid'}, [])
        out = self.api_cv('cle', DURAND, {'cle': CLE})
        self.assertEqual((out['code'], out['json']['error']), ('409', 'cle_refusee'))
        self.assertEqual(CV.cle_api(self.d, DURAND[0]), '')

    def test_creee_puis_retrouvee_puis_recreee_si_supprimee(self):
        self.cle()
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': 'conv-0001-aaaa', 'title': 'x'}, [])
        out = self.ouvrir()['json']
        self.assertEqual((out['mode'], out['cree'], out['url']), ('conversation', True, OWUI + '/c/conv-0001-aaaa'))
        creation = [r for r in FauxOpenWebUI.requetes if r[1] == '/api/v1/chats/new'][0]
        self.assertTrue(creation[3]['chat']['title'].startswith('Dossier '))
        self.assertIsNone(creation[3]['folder_id'])
        self.assertEqual(creation[2]['Authorization'], 'Bearer ' + CLE)
        self.assertNotIn(CLE, json.dumps(out))
        # réouverture : la même conversation, sans nouvelle création
        FauxOpenWebUI.requetes.clear()
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/conv-0001-aaaa')] = (200, {'id': 'conv-0001-aaaa'}, [])
        out = self.ouvrir()['json']
        self.assertEqual((out['mode'], out['cree'], out['url']), ('conversation', False, OWUI + '/c/conv-0001-aaaa'))
        self.assertEqual([r[1] for r in FauxOpenWebUI.requetes], ['/api/v1/chats/conv-0001-aaaa'])
        # supprimée dans Open WebUI (0.13.5 répond 401, la clé restant valable) : recréée
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/conv-0001-aaaa')] = (401, {'detail': 'Not Found'}, [])
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': 'conv-0002-bbbb'}, [])
        out = self.ouvrir()['json']
        self.assertEqual((out['cree'], out['url']), (True, OWUI + '/c/conv-0002-bbbb'))
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM %s' % CV.TABLE).fetchone()[0], 1)

    def test_cle_revoquee_apres_coup(self):
        self.cle()
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': 'conv-0001-aaaa'}, [])
        self.ouvrir()
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/conv-0001-aaaa')] = (401, {'detail': 'Not authenticated'}, [])
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/?page=1')] = (401, {'detail': 'Not authenticated'}, [])
        out = self.ouvrir()['json']
        self.assertEqual((out['mode'], out['erreur']), ('saisie', 'cle_refusee'))
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM %s' % CV.TABLE).fetchone()[0], 1)   # association gardée

    def test_conversation_propre_a_chaque_personne(self):
        self.cle(DURAND)
        self.cle(MARTIN, 'cle-fictive-autre-9876543210')
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': 'conv-durand-0001'}, [])
        self.ouvrir(DURAND)
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': 'conv-martin-0001'}, [])
        out = self.ouvrir(MARTIN)['json']
        self.assertEqual(out['url'], OWUI + '/c/conv-martin-0001')
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM %s' % CV.TABLE).fetchone()[0], 2)

    def test_rangee_dans_le_dossier_open_webui_associe_sinon_hors_dossier(self):
        from agent.workstation import save_workspace_mapping
        save_workspace_mapping(self.d, 'DOS-001', 'dossier-owui-01', 'DOS-001', OWUI + '/?folder=dossier-owui-01')
        self.cle()
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (404, {'detail': 'Not Found'}, [])   # dossier non accessible
        out = self.ouvrir()['json']
        envois = [r[3]['folder_id'] for r in FauxOpenWebUI.requetes if r[1] == '/api/v1/chats/new']
        self.assertEqual(envois, ['dossier-owui-01', None])                         # nouvel essai hors dossier
        self.assertEqual(out['mode'], 'saisie')                                     # le faux refuse aussi : repli honnête
        self.assertEqual(out['erreur'], 'openwebui_injoignable')

    def test_cle_refusee_ou_open_webui_injoignable_repli_explique(self):
        self.cle()
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (401, {'detail': 'invalid'}, [])
        out = self.ouvrir()['json']
        self.assertEqual((out['mode'], out['erreur']), ('saisie', 'cle_refusee'))
        self.assertIn('Open WebUI refuse cette clé', out['message'])
        FauxOpenWebUI.panne = True
        out = self.ouvrir()['json']
        self.assertEqual((out['mode'], out['erreur']), ('saisie', 'openwebui_injoignable'))
        self.assertEqual(out['url'], OWUI + '/?q=Dossier%20DOS-001%20%3A%20')
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM %s' % CV.TABLE).fetchone()[0], 0)

    def test_identifiant_rendu_par_open_webui_controle(self):
        self.cle()
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': '../../admin'}, [])
        out = self.ouvrir()['json']
        self.assertEqual(out['mode'], 'saisie')
        self.assertEqual(self.d.db.execute('SELECT COUNT(*) FROM %s' % CV.TABLE).fetchone()[0], 0)

    def test_passerelle_sortante_consultee_et_refus_explique(self):
        from agent import common
        vus = []

        def garde(client, methode, url, corps=None):
            vus.append((methode, url))
            raise Stop('destination_non_autorisee')
        with patch.object(common, 'EGRESS_GUARD', garde):
            out = self.api_cv('cle', DURAND, {'cle': CLE})
        self.assertEqual((out['code'], out['json']['error']), ('403', 'destination_non_autorisee'))
        self.assertEqual(vus, [('GET', OWUI + '/api/v1/chats/')])                   # ni la clé ni les paramètres dans le relevé
        self.assertEqual(FauxOpenWebUI.requetes, [])                              # rien n'est parti
        self.cle()
        with patch.object(common, 'EGRESS_GUARD', garde):
            out = self.ouvrir()['json']
        self.assertEqual((out['mode'], out['erreur']), ('saisie', 'destination_non_autorisee'))
        self.assertIn('local strict', out['message'])

    def test_suppression_de_la_cle(self):
        self.cle()
        out = self.api_cv('cle', DURAND, {'supprimer': 1})
        self.assertEqual((out['code'], out['json']['enregistree']), ('200', False))
        self.assertFalse(CV._chemin_cle(self.d, DURAND[0]).exists())
        self.assertEqual(self.ouvrir()['json']['mode'], 'saisie')


# ================================================================================================ clé en main : comptes et synchronisation
class CleEnMain(Base):
    ADMIN_CLE = 'cle-fictive-administrateur-0123'

    def setUp(self):
        super().setUp()
        self.regler()

    def relier(self, accord=False):
        FauxOpenWebUI.reponses[('GET', '/openai/config')] = (200, {'OPENAI_API_BASE_URLS': []}, [])
        return CV.enregistrer_cle_admin(self.d, self.ADMIN_CLE, accord)

    def creation_possible(self, existe=False):
        q = '/api/v1/users/?query=' + 'durand%40example.test'
        FauxOpenWebUI.reponses[('GET', q)] = (200, {'users': [{'email': 'durand@example.test'}] if existe else []}, [])
        FauxOpenWebUI.reponses[('POST', '/api/v1/auths/add')] = (200, {'id': 'u-durand', 'role': 'user', 'token': 'jeton-session-fictif'}, [])
        FauxOpenWebUI.reponses[('POST', '/api/v1/auths/api_key')] = (200, {'api_key': 'cle-creee-pour-durand-01'}, [])
        FauxOpenWebUI.reponses[('POST', '/api/v1/chats/new')] = (200, {'id': 'conv-0001-aaaa'}, [])

    def test_cle_admin_verifiee_rangee_chiffree(self):
        FauxOpenWebUI.reponses[('GET', '/openai/config')] = (401, {'detail': 'admin'}, [])
        with self.assertRaisesRegex(Stop, 'cle_refusee'):
            CV.enregistrer_cle_admin(self.d, self.ADMIN_CLE)
        self.assertEqual(CV.cle_admin(self.d), '')
        self.relier(True)
        self.assertEqual(CV.cle_admin(self.d), self.ADMIN_CLE)
        self.assertTrue(CV._chemin_cle_admin(self.d).read_text(encoding='utf-8').startswith('AXIORHUB-VAULT-V1:'))
        self.assertEqual(self.d.settings(CV.ACCORD, {}), {'cles': True, 'inscription_fermee': False})

    def test_compte_cree_pour_la_personne_puis_conversation_mot_de_passe_rendu_une_fois(self):
        self.relier()
        self.creation_possible()
        out = self.ouvrir(DURAND)['json']
        self.assertEqual((out['mode'], out['url']), ('conversation', OWUI + '/c/conv-0001-aaaa'))
        self.assertEqual(out['compte']['identifiant'], 'durand@example.test')
        mot = out['compte']['mot_de_passe']
        self.assertIn(mot, out['message'])
        ajout = [r for r in FauxOpenWebUI.requetes if r[1] == '/api/v1/auths/add'][0]
        self.assertEqual((ajout[2]['Authorization'], ajout[3]['email'], ajout[3]['role'], ajout[3]['password']),
                         ('Bearer ' + self.ADMIN_CLE, 'durand@example.test', 'user', mot))
        cle = [r for r in FauxOpenWebUI.requetes if r[1] == '/api/v1/auths/api_key'][0]
        self.assertEqual(cle[2]['Authorization'], 'Bearer jeton-session-fictif')        # clé créée avec la session du nouveau compte
        self.assertEqual(CV.cle_api(self.d, DURAND[0]), 'cle-creee-pour-durand-01')
        for (table,) in self.d.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():   # ni l'audit ni aucune table
            self.assertNotIn(mot, json.dumps([tuple(r) for r in self.d.db.execute('SELECT * FROM "%s"' % table).fetchall()], default=str), table)
        FauxOpenWebUI.reponses[('GET', '/api/v1/chats/conv-0001-aaaa')] = (200, {'id': 'conv-0001-aaaa'}, [])
        second = self.ouvrir(DURAND)['json']
        self.assertNotIn('compte', second)                                            # plus jamais rendu
        self.assertEqual(len([r for r in FauxOpenWebUI.requetes if r[1] == '/api/v1/auths/add']), 1)

    def test_compte_existant_jamais_modifie(self):
        self.relier()
        self.creation_possible(existe=True)
        out = self.ouvrir(DURAND)['json']
        self.assertEqual(out['mode'], 'saisie')
        self.assertIn('existe déjà', out['message'])
        self.assertFalse(any(r[1] == '/api/v1/auths/add' for r in FauxOpenWebUI.requetes))

    def test_sans_cle_admin_ou_identifiant_sans_adresse_rien_n_est_cree(self):
        self.creation_possible()
        self.assertEqual(self.ouvrir(DURAND)['json']['mode'], 'saisie')                # pas relié
        self.relier()
        self.assertIsNone(CV.compte_automatique(self.d, 'cabinet', OWUI + '/'))     # identifiant sans adresse e-mail
        self.assertFalse(any(r[1] == '/api/v1/auths/add' for r in FauxOpenWebUI.requetes))

    def test_synchronisation_reservee_a_l_administrateur_sans_l_outil(self):
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m56112/conversations/synchroniser'))
        self.assertEqual(self.api_cv('synchroniser', DURAND)['code'], '403')
        self.assertEqual(self.api_cv('synchroniser', ADMIN)['json']['error'], 'cle_admin_absente')
        self.relier()
        vus = []
        from agent import openwebui_reglage56112 as R

        def regler(client, e, etapes, journal=None):
            vus.append(etapes)
            return [{'etape': x, 'etat': 'a_jour', 'detail': ''} for x in etapes]
        with patch.object(R, 'regler', regler):
            out = self.api_cv('synchroniser', ADMIN)
        self.assertEqual(out['code'], '200')
        self.assertNotIn('outil', vus[0])                                             # le jeton de l'outil reste l'affaire de root
        self.assertIn('parametres', vus[0])
        self.assertIn('voix', vus[0])
        self.assertTrue(self.d.settings(CV.EMPREINTE, {}).get('empreinte'))

    def test_synchronisation_automatique_si_les_reglages_changent(self):
        self.relier()
        lances = []
        with patch.object(CV.threading, 'Thread', lambda target, daemon, name: type('F', (), {'start': lambda s: lances.append(name)})()):
            self.assertTrue(CV.synchroniser_si_change(self.d, delai=0))               # jamais synchronisé
            CV._SYNCHRO['en_cours'] = False
            self.d.setting(CV.EMPREINTE, {'empreinte': CV._empreinte(CV.entrees_service(self.d)), 'at': self.d.now(), 'echecs': []})
            self.assertFalse(CV.synchroniser_si_change(self.d, delai=0))              # rien n'a changé
            self.d.c.setdefault('llm', {})['profile'] = 'personnalise'
            self.d.c['llm']['temperature'] = 0.1
            self.assertTrue(CV.synchroniser_si_change(self.d, delai=0))               # profil de génération modifié : reporté
            CV._SYNCHRO['en_cours'] = False
            self.assertFalse(CV.synchroniser_si_change(self.d, delai=3600))           # pas plus d'une fois par délai
        self.assertEqual(lances, ['openwebui56112-synchro', 'openwebui56112-synchro'])


# ================================================================================================ test de l'affichage
class TestAffichage(Base):
    def setUp(self):
        super().setUp()
        self.regler()

    def test_interdit_par_x_frame_options_reglage_donne(self):
        FauxOpenWebUI.reponses[('GET', '/')] = (200, None, [('X-Frame-Options', 'DENY')])
        out = self.api_cv('tester', ADMIN)['json']
        self.assertTrue(out['bloque'])
        self.assertIn('CONTENT_SECURITY_POLICY="frame-ancestors \'self\' https://cabinet.example.test"', out['message'])
        self.assertTrue(out['noreload'])
        self.assertEqual(FauxOpenWebUI.requetes[-1][2].get('Authorization'), None)   # aucun identifiant envoyé

    def test_autorise_par_frame_ancestors(self):
        FauxOpenWebUI.reponses[('GET', '/')] = (200, None, [('X-Frame-Options', 'DENY'),
                                                            ('Content-Security-Policy', "frame-ancestors 'self' https://cabinet.example.test")])
        out = self.api_cv('tester', BERNARD)['json']
        self.assertFalse(out['bloque'])
        self.assertIn('Affichage autorisé', out['message'])

    def test_injoignable(self):
        FauxOpenWebUI.panne = True
        out = self.api_cv('tester', ADMIN)['json']
        self.assertEqual(out['statut'], 'connexion_impossible')
        self.assertFalse(out['bloque'])


if __name__ == '__main__':
    unittest.main()

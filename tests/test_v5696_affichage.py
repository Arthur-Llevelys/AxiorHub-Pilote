"""5.6.96 (lot affichage) : volet de l'assistant en trois zones (U01, I04), Aujourd'hui densifié (U02), typographie Figtree (U03),
mesure du retour (U05), travail synchrone réduit (U06), cache des actifs et retour (U07).

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001…), aucun réseau, aucun modèle. Le rendu visuel (zones visibles sans défiler, focus, clavier
logiciel, 125 %, 200 %, sombre) est vérifié dans un vrai navigateur par ``scripts/parcours-navigateur.py`` (P96a, P96b), pas ici."""
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent import affichage5696, cockpit530, tableau5688, ui_helpers, ui_tokens5689
from agent.common import Stop, private_json
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'agent' / 'static'
MATTERS = [{'id': 'DOS-%03d' % i, 'client_name': ('DURAND', 'MARTIN', 'ZORVAL', 'KERVILAC')[i % 4] + ' %d' % i, 'path': '/Dossiers/DOS-%03d' % i,
            'references': ['DOS-%03d' % i], 'aliases': [], 'correspondents': []} for i in range(1, 41)]


class Arbre(HTMLParser):
    """Éléments à identifiant, avec la chaîne de leurs ancêtres (balise, id, classes, attributs) : la structure rendue, pas une chaîne."""
    VIDES = {'input', 'img', 'br', 'meta', 'link', 'hr', 'source'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pile, self.ids, self.textes, self.options = [], {}, {}, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        noeud = {'tag': tag, 'id': a.get('id', ''), 'classes': (a.get('class') or '').split(), 'attrs': a, 'parents': list(self.pile)}
        if a.get('id'):
            self.ids.setdefault(a['id'], []).append(noeud)
        if tag == 'option' and any(p['id'] == 'ws-ai-matter' for p in self.pile):
            self.options.append(a.get('value', ''))
        if tag not in self.VIDES:
            self.pile.append(noeud)

    def handle_endtag(self, tag):
        for i in range(len(self.pile) - 1, -1, -1):
            if self.pile[i]['tag'] == tag:
                del self.pile[i:]
                break

    def handle_data(self, data):
        for n in self.pile:
            if n['id']:
                self.textes[n['id']] = self.textes.get(n['id'], '') + data

    def un(self, ident):
        trouves = self.ids.get(ident, [])
        assert len(trouves) == 1, '%s : %d élément(s)' % (ident, len(trouves))
        return trouves[0]


def arbre(html):
    a = Arbre()
    a.feed(html)
    return a


def dans(noeud, tag=None, classe=None, ident=None):
    return any((tag is None or p['tag'] == tag) and (classe is None or classe in p['classes']) and (ident is None or p['id'] == ident) for p in noeud['parents'])


class Base(t530.Base):
    def setUp(self):
        super().setUp()
        private_json(Path(self.f.c['matters_file']), MATTERS)
        affichage5696._POLICES_GARDEES.clear()

    def brut(self, path, query=''):
        """Réponse binaire (police, image) : en-têtes et octets, sans décodage."""
        import base64
        import io
        env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(b''), 'CONTENT_LENGTH': '0',
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        out = {}
        out['body'] = b''.join(self.app(env, lambda status, headers: out.update(status=status, headers=dict(headers))))
        return out


# ------------------------------------------------------------------------------------------------ U01, I04 : volet en trois zones
class Volet(Base):
    def test_U01_I04_trois_zones_et_un_seul_titre(self):
        page = self.request('/dossiers')
        self.assertTrue(page['status'].startswith('200'), page['status'])
        a = arbre(page['body'])
        dock = a.un('ws-ai-dock')
        self.assertEqual((dock['attrs'].get('role'), dock['attrs'].get('aria-modal')), ('dialog', 'true'))
        self.assertIn('hidden', dock['attrs'])                                       # fermé au chargement
        # en-tête fixe : un titre, le contexte, Changer le contexte, agrandir, fermer
        for ident in ('ws-ai-titre', 'ws-ai-portee', 'ws-ai-changer', 'ws-ai-wide', 'ws-ai-close', 'ws-ai-mic'):
            self.assertTrue(dans(a.un(ident), 'header', 'ws-ai-tete'), ident)
        self.assertEqual(a.textes['ws-ai-titre'].strip(), 'Assistant AxiorHub')
        self.assertNotIn('Pilote · Pilote', page['body'])
        self.assertEqual(dock['attrs'].get('aria-labelledby'), 'ws-ai-titre')
        # composeur fixe : instruction, envoi, dossier, pièces jointes, documents, conversation vocale, état, options repliées
        for ident in ('ws-ai-question', 'ws-ai-send', 'ws-ai-matter', 'ws-ai-matter-search', 'ws-ai-files', 'ws-ai-file', 'ws-ai-talk', 'ws-ai-status',
                      'ws-ai-intent', 'ws-ai-due', 'ws-ai-autonomy', 'ws-ai-clear', 'm82-active'):
            noeud = a.un(ident)
            self.assertTrue(dans(noeud, 'footer', 'ws-ai-composeur'), ident)
            self.assertFalse(dans(noeud, classe='ws-ai-body'), ident)               # rien d'essentiel dans la zone qui défile
        self.assertFalse(dans(a.un('ws-ai-send'), 'details'))                         # Envoyer jamais dans un repli
        self.assertFalse(dans(a.un('ws-ai-question'), 'details'))
        self.assertTrue(dans(a.un('ws-ai-intent'), 'details', 'ws-ai-options'))      # options avancées repliées
        # historique défilant : voix, contexte et actions repliés, plan, fil, exemples repliés
        for ident in ('ws-ai-voice', 'ws-ai-plan', 'ws-ai-result', 'ws-ai-current', 'ws-ai-history'):
            self.assertTrue(dans(a.un(ident), classe='ws-ai-body'), ident)
        corps = [n for n in a.un('ws-ai-history')['parents'] if 'ws-ai-body' in n['classes']][0]
        self.assertTrue(dans(corps, 'aside', ident='ws-ai-dock'))
        self.assertEqual(a.un('ws-ai-question')['attrs'].get('rows'), '2')
        html = page['body']
        self.assertLess(html.index('id="ws-ai-voice"'), html.index('ws-ai-auto-context'))
        self.assertRegex(html, r'<details class="ws-ai-contexte"><summary>Contexte et actions de la page</summary><p class="ws-ai-auto-context">')

    def test_I04_lanceur_au_logo_et_nom_accessible(self):
        a = arbre(self.request('/dossiers')['body'])
        lanceur = a.un('ws-ai-launcher')
        self.assertEqual((lanceur['attrs'].get('aria-controls'), lanceur['attrs'].get('aria-expanded')), ('ws-ai-dock', 'false'))
        self.assertEqual(lanceur['attrs'].get('aria-label'), 'Ouvrir l’assistant AxiorHub')
        page = self.request('/dossiers')['body']
        bouton = re.search(r'<button type="button" id="ws-ai-launcher"[^>]*>(.*?)</button>', page).group(1)
        self.assertRegex(bouton, r'^<img src="/agent-courriel/static/axiorhub-icon\.png" alt="" width="24" height="24"')   # décoratif : texte alternatif vide
        self.assertNotIn('🤖', bouton)
        fichier = self.brut('/static/axiorhub-icon.png')
        self.assertTrue(fichier['status'].startswith('200'))

    def test_I04_contexte_reel_dossier_et_courriel(self):
        a = arbre(self.request('/dossiers')['body'])
        self.assertEqual(a.textes['ws-ai-portee-dossier'], 'Aucun dossier')
        self.assertEqual(a.textes['ws-ai-portee-courriel'], 'Aucun courriel')
        a = arbre(self.request('/dossiers', query='matter=DOS-007')['body'])
        self.assertTrue(a.textes['ws-ai-portee-dossier'].startswith('Dossier : '))
        self.assertIn('DOS-007', a.textes['ws-ai-portee-dossier'] + json.dumps(a.options))
        config = json.loads(re.search(r'<script type="application/json" id="ws-ai-config">(.*?)</script>', self.request('/dossiers', query='matter=DOS-007')['body']).group(1))
        self.assertEqual(config['selected_matter'], 'DOS-007')
        # un dossier inconnu de la liste n'est ni affiché ni proposé (le serveur revalide de toute façon à l'envoi)
        a = arbre(self.request('/dossiers', query='matter=DOS-999')['body'])
        self.assertEqual((a.textes['ws-ai-portee-dossier'], a.options), ('Aucun dossier', ['']))

    def test_U06_volet_ferme_sans_la_liste_des_dossiers(self):
        a = arbre(self.request('/dossiers')['body'])
        self.assertEqual(a.options, [''])                                            # « Tout le cabinet » seulement
        a = arbre(self.request('/dossiers', query='matter=DOS-012')['body'])
        self.assertEqual(a.options, ['', 'DOS-012'])                                 # plus le dossier de la page, choisi
        # contre-exemple : l'ancien rendu portait les 40 dossiers fictifs dans chaque page
        ancien = ''.join('<option value="%s">' % m['id'] for m in MATTERS)
        self.assertNotIn(ancien[:200], self.request('/dossiers')['body'])
        # la liste complète reste servie à l'ouverture du volet (v567.js : loadMatters)
        liste = json.loads(self.request('/api440/m567/matters')['body'])['matters']
        self.assertEqual(len(liste), len(MATTERS))
        # pages dont les scripts reprennent la liste du volet (engagements, reprises des agents documentaires) : liste complète, comme avant
        a = arbre(self.request('/engagements')['body'])
        self.assertEqual(len(a.options), len(MATTERS) + 1)

    def test_U06_memoire_de_requete_jamais_pendant_une_action(self):
        from agent.common import set_request_mode
        appels = []
        affichage5696.demarrer()
        try:
            set_request_mode('write')
            affichage5696.memo_requete('k', lambda: appels.append(1))
            affichage5696.memo_requete('k', lambda: appels.append(1))
            self.assertEqual(len(appels), 2)                                         # action : toujours recalculé
            set_request_mode('read')
            affichage5696.memo_requete('l', lambda: appels.append(1))
            affichage5696.memo_requete('l', lambda: appels.append(1))
            self.assertEqual(len(appels), 3)                                         # affichage : une fois par page
        finally:
            affichage5696.terminer()
            set_request_mode('')

    def test_U01_identifiants_communs_conserves(self):
        html = ui_helpers.assistant_dock('/p', 'x', MATTERS, '/dossiers', True)
        for ident in ('ws-ai-launcher', 'ws-ai-dock', 'ws-ai-close', 'ws-ai-wide', 'ws-ai-mic', 'ws-ai-mic-stop', 'ws-ai-voice', 'ws-ai-matter-search', 'ws-ai-matter',
                      'm82-active', 'ws-ai-question', 'ws-ai-intent', 'ws-ai-send', 'ws-ai-plan', 'ws-ai-files', 'ws-ai-due', 'ws-ai-autonomy', 'ws-ai-talk',
                      'ws-ai-file', 'ws-ai-clear', 'ws-ai-status', 'ws-ai-result', 'ws-ai-current', 'ws-ai-history', 'ws-ai-config'):
            self.assertEqual(html.count('id="%s"' % ident), 1, ident)
        scripts = ''.join((STATIC / n).read_text(encoding='utf-8') for n in ('v567.js', 'v568.js', 'v5689-ia.js', 'v5682.js', 'v5636.js', 'v5680.js'))
        lus = set(re.findall(r"#(ws-ai-[a-z-]+|m82-active)", scripts))
        absents = {i for i in lus if 'id="%s"' % i not in html} - {'ws-ai-dictate', 'ws-ai-live', 'ws-ai-config'}
        self.assertEqual(absents, set())                                             # tout ce que lisent les scripts existe encore

    def test_U01_titre_personnalise_sans_doublon(self):
        js = (STATIC / 'v567.js').read_text(encoding='utf-8')
        self.assertNotIn("p.name + ' · Pilote'", js)
        self.assertIn("question.focus({preventScroll: true}); refreshCurrent(); loadMatters();}", js)   # ouverture sans saut de page


# ------------------------------------------------------------------------------------------------ U03 : typographie
class Typographie(Base):
    def test_U03_figtree_par_defaut_servie_localement_et_versionnee(self):
        feuille = ui_tokens5689.feuille()
        empreinte = sha256((STATIC / 'figtree-latin.woff2').read_bytes()).hexdigest()
        self.assertIn('src:url(figtree-latin.woff2?v=%s) format("woff2")' % empreinte[:12], feuille)
        defaut = re.search(r'\nbody\[data-carbon\]\{[^}]*?--ui-police:([^;]+);', feuille).group(1)
        self.assertTrue(defaut.startswith('"Figtree", '), defaut)
        for nom, pile in ui_tokens5689.POLICES.items():
            self.assertTrue(pile.endswith(ui_tokens5689.REPLI), nom)                  # repli système : texte lisible sans le fichier
        self.assertNotRegex((STATIC / 'app520.css').read_text(encoding='utf-8'), r'url\((?:["\']?)https?:')   # aucun CDN
        self.assertEqual((STATIC / 'figtree-latin.woff2').read_bytes()[:4], b'wOF2')
        reponse = self.brut('/static/figtree-latin.woff2', query='v=' + empreinte[:12])
        self.assertTrue(reponse['status'].startswith('200'))
        self.assertEqual(reponse['headers']['Content-Type'], 'font/woff2')
        self.assertEqual(reponse['body'], (STATIC / 'figtree-latin.woff2').read_bytes())
        self.assertEqual(reponse['headers']['Cache-Control'], 'private, max-age=31536000, immutable')

    def test_U03_licence_et_notice_de_la_police(self):
        licence = (ROOT / 'LICENSES' / 'OFL-1.1-Figtree.txt').read_text(encoding='utf-8')
        self.assertIn('SIL OPEN FONT LICENSE Version 1.1', licence)
        self.assertIn('Figtree Project Authors', licence)
        notices = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
        ligne = next(l for l in notices.splitlines() if 'figtree-latin.woff2' in l)
        self.assertIn(sha256((STATIC / 'figtree-latin.woff2').read_bytes()).hexdigest(), ligne)
        self.assertIn('LICENSES/OFL-1.1-Figtree.txt', ligne)

    def test_U03_police_par_compte_plex_en_variante(self):
        page = self.request('/dossiers')['body']
        self.assertNotIn('data-police=', page[:600])                                 # défaut : Figtree, aucune trace
        tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': list(tableau5688.IDS), 'accent': '', 'police': 'plex'})
        page = self.request('/dossiers')['body']
        self.assertRegex(page[:300], r'<html data-police="plex"')
        self.assertEqual(tableau5688.charger(self.desk, 'autre-compte')['police'], 'figtree')   # un autre compte garde la sienne
        with self.assertRaises(Stop):
            tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': [], 'accent': '', 'police': 'comic'})
        tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': list(tableau5688.IDS), 'accent': ''})   # ancien formulaire : choix conservé
        self.assertEqual(tableau5688.charger(self.desk, 'cabinet')['police'], 'plex')
        tableau5688.reinitialiser(self.desk, 'cabinet')
        self.assertNotIn('data-police=', self.request('/dossiers')['body'][:600])

    def test_U03_base_illisible_garde_la_police_par_defaut(self):
        self.assertEqual(affichage5696.police_compte({'state_dir': str(self.f.base / 'absent')}, 'cabinet'), 'figtree')
        self.assertEqual(affichage5696.marquer_police('<html lang="fr"><body>', 'inconnue'), '<html lang="fr"><body>')

    def test_U03_contraste_inchange_clair_et_sombre(self):
        for theme in (ui_tokens5689.CLAIR, ui_tokens5689.SOMBRE):
            j = ui_tokens5689.jetons(theme)
            self.assertGreaterEqual(ui_tokens5689.contraste(j['text-primary'], j['layer-01']), 7)
            self.assertGreaterEqual(ui_tokens5689.contraste(j['text-secondary'], j['layer-01']), 4.5)
            self.assertGreaterEqual(ui_tokens5689.contraste(j['link-primary'], j['layer-01']), 4.5)


# ------------------------------------------------------------------------------------------------ U02 : densité d'Aujourd'hui
class Densite(Base):
    def test_U02_densite_compacte_memorisee_par_compte(self):
        a = arbre(self.request('/aujourdhui')['body'])
        self.assertNotIn('data-densite', a.un('c530')['attrs'])
        tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': list(tableau5688.IDS), 'accent': '', 'densite': 'compacte'})
        a = arbre(self.request('/aujourdhui')['body'])
        self.assertEqual(a.un('c530')['attrs'].get('data-densite'), 'compacte')
        self.assertEqual(tableau5688.charger(self.desk, 'autre')['densite'], 'confort')
        with self.assertRaises(Stop):
            tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': [], 'accent': '', 'densite': 'serree'})
        panneau = tableau5688.panneau(tableau5688.charger(self.desk, 'cabinet'))
        self.assertRegex(panneau, r'<input type="radio" name="densite" value="compacte" checked>')
        self.assertRegex(panneau, r'<input type="radio" name="police" value="figtree" checked>')

    def test_U02_aucune_fonction_retiree(self):
        page = self.request('/aujourdhui')['body']
        a = arbre(page)
        for ident in ('ax95-actuellement', 'a89-travail', 'c5656-file', 'c530-feed', 'c530-day', 'c5632-veille', 'c530-routines', 't88-panel', 'a92-pause'):
            self.assertEqual(len(a.ids.get(ident, [])), 1, ident)


# ------------------------------------------------------------------------------------------------ U05 : Server-Timing
class Mesure(Base):
    def test_U05_server_timing_agrege_sans_donnee(self):
        r = self.request('/aujourdhui')
        entete = r['headers'].get('Server-Timing', '')
        valeurs = dict(re.findall(r'(auth|sqlite|blocs|total);dur=([\d.]+)', entete))
        self.assertEqual(set(valeurs), {'auth', 'sqlite', 'blocs', 'total'})
        self.assertGreater(int(re.search(r'sqlite;dur=[\d.]+;desc="(\d+)"', entete).group(1)), 0)
        self.assertGreaterEqual(float(valeurs['total']), float(valeurs['blocs']))
        self.assertEqual(re.sub(r'(auth|sqlite|blocs|total);dur=[\d.]+(;desc="\d+")?(, )?', '', entete), '')   # des nombres seulement
        self.assertEqual(r['headers']['Cache-Control'], 'no-store')
        self.assertNotIn('Server-Timing', self.request('/aujourdhui', auth=False)['headers'])          # pas de mesure avant authentification
        self.assertNotIn('Server-Timing', self.request('/static/v5696-affichage.js', query='v=1')['headers'])

    def test_U05_compteur_remis_a_zero_hors_requete(self):
        affichage5696.terminer()
        affichage5696.requete_sqlite(1.0)
        self.assertEqual(affichage5696.entete(), '')
        affichage5696.demarrer()
        affichage5696.requete_sqlite(.002)
        self.assertEqual(affichage5696.entete(), '')                                  # pas d'authentification marquée : rien à publier
        affichage5696.marquer_auth()
        self.assertRegex(affichage5696.entete(), r'^auth;dur=[\d.]+, sqlite;dur=2\.0;desc="1", blocs;dur=[\d.]+, total;dur=[\d.]+$')
        affichage5696.terminer()

    def test_U05_scripts_de_mesure_sans_adresse_ni_donnee(self):
        js = (ROOT / 'scripts' / 'mesurer-retour-5696.js').read_text(encoding='utf-8')
        self.assertNotIn('fetch(\'http', js)
        self.assertEqual(subprocess.run([shutil.which('node') or 'node', '--check', str(ROOT / 'scripts' / 'mesurer-retour-5696.js')], capture_output=True).returncode, 0) \
            if shutil.which('node') else None
        self.assertTrue((ROOT / 'scripts' / 'mesure-retour-5696.py').is_file())


# ------------------------------------------------------------------------------------------------ U06 : travail synchrone réduit
class Synchrone(Base):
    def test_U06_bloc_masque_jamais_calcule(self):
        tableau5688.enregistrer(self.desk, 'cabinet', {'ordre': list(tableau5688.IDS), 'accent': '', 'masques': ['activite', 'journee']})
        with patch('agent.cockpit530.feed_html', side_effect=AssertionError('bloc masqué calculé')) as feed, \
             patch('agent.cockpit530.day_html', side_effect=AssertionError('bloc masqué calculé')) as day:
            r = self.request('/aujourdhui')
        self.assertTrue(r['status'].startswith('200'), r['body'][:300])
        self.assertIn('id="ax95-actuellement"', r['body'])                             # nouvelle page, pas le repli sur l'ancienne
        self.assertEqual((feed.call_count, day.call_count), (0, 0))

    def test_U06_page_construite_une_seule_fois(self):
        with patch('agent.cockpit530.feed_html', wraps=cockpit530.feed_html) as feed, patch('agent.cockpit530.style_html', wraps=cockpit530.style_html) as style, \
             patch('agent.web520.diagnosis', wraps=__import__('agent.web520', fromlist=['diagnosis']).diagnosis) as diag:
            r = self.request('/aujourdhui')
        self.assertTrue(r['status'].startswith('200'))
        self.assertEqual(feed.call_count, 1)                                         # avant : l'ancienne page, puis la nouvelle (2)
        self.assertEqual(style.call_count, 1)
        self.assertEqual(diag.call_count, 1)                                         # avant : en-tête et panneau de santé (2 sondes d'Ollama)

    def test_U06_composeur_historique_sans_options_de_dossiers(self):
        a = arbre(self.request('/aujourdhui')['body'])
        self.assertEqual(len([n for n in a.ids.get('c530-override', [])]), 1)
        page = self.request('/aujourdhui')['body']
        bloc = re.search(r'<select id="c530-override">(.*?)</select>', page).group(1)
        self.assertEqual(re.findall(r'<option value="([^"]*)"', bloc), [''])

    def test_U06_bloc_replie_charge_a_l_ouverture_avec_les_memes_droits(self):
        from agent.actuellement5695 import regler_pli
        regler_pli(self.desk, 'cabinet', 'veille', False)
        with patch('agent.cockpit530.veille_card', return_value='<h2 id="c5632-t-veille">Veille juridique</h2><p>Décision fictive ZORVAL</p>') as veille:
            r = self.request('/aujourdhui')
            self.assertEqual(veille.call_count, 0)                                   # replié : rien de calculé au rendu
            a = arbre(r['body'])
            section = a.un('c5632-veille')                                           # section et titre conservés (lecteur d'écran, P95b)
            self.assertEqual(section['attrs'].get('data-a96-part'), 'veille')
            self.assertTrue(dans(section, 'details'))
            part = self.request('/api440/m530/part', query='name=veille&prefix=/agent-courriel')
            self.assertEqual(veille.call_count, 1)
        self.assertIn('Décision fictive ZORVAL', json.loads(part['body'])['html'])
        regler_pli(self.desk, 'cabinet', 'veille', True)
        with patch('agent.cockpit530.veille_card', return_value='<h2 id="c5632-t-veille">Veille juridique</h2>') as veille:
            self.request('/aujourdhui')
        self.assertEqual(veille.call_count, 1)                                       # ouvert : construit avec la page, comme avant
        self.assertTrue(self.request('/api440/m530/part', query='name=veille', auth=False)['status'].startswith(('401', '303')))
        self.assertTrue(self.request('/api440/m530/part', query='name=inconnu')['status'].startswith('400'))

    def test_U06_points_de_sante_calcules_une_fois_par_requete_seulement(self):
        from agent import sante5636
        with patch('agent.web520.diagnosis', return_value={'problems': []}) as diag:
            sante5636.checks(self.desk)
            sante5636.checks(self.desk)
        self.assertEqual(diag.call_count, 2)                                         # hors requête de page : jamais de valeur gardée


# ------------------------------------------------------------------------------------------------ U07 : cache des actifs et retour
class Cache(Base):
    def test_U07_polices_versionnees_par_contenu(self):
        with tempfile.TemporaryDirectory() as tmp:
            fichier = Path(tmp) / 'figtree-latin.woff2'
            fichier.write_bytes(b'wOF2-a')
            with patch.object(ui_tokens5689, 'STATIC', Path(tmp)):
                avant = ui_tokens5689.version_fichier('figtree-latin.woff2')
                fichier.write_bytes(b'wOF2-b')
                self.assertNotEqual(avant, ui_tokens5689.version_fichier('figtree-latin.woff2'))   # l'adresse change avec le contenu
        html = ui_helpers.versioned('<link rel="preload" href="/p/static/figtree-latin.woff2"><script src="/p/static/v1.js"></script>')
        self.assertRegex(html, r'href="/p/static/figtree-latin\.woff2\?v=[\d.]+"')
        jetons = (STATIC / 'v5689-carbon-jetons.css').read_text(encoding='utf-8')
        self.assertEqual(jetons, ui_tokens5689.feuille())                             # feuille générée à jour (build-css)
        self.assertEqual(set(re.findall(r'url\(([^)?]+)\?v=', jetons)), {'figtree-latin.woff2', 'plex-sans-400.woff2', 'plex-sans-600.woff2'})

    def test_U07_html_authentifie_jamais_en_cache_partage(self):
        for chemin in ('/aujourdhui', '/dossiers'):
            r = self.request(chemin)
            self.assertEqual(r['headers']['Cache-Control'], 'no-store', chemin)
        sans_version = self.brut('/static/figtree-latin.woff2')
        self.assertEqual(sans_version['headers']['Cache-Control'], 'no-store')
        for chemin in ('/static/v5696-affichage.js', '/static/v5696-affichage.css', '/static/app520.css'):
            r = self.request(chemin, query='v=9.9.99')
            self.assertTrue(r['status'].startswith('200'), chemin)
            self.assertTrue(r['headers']['Cache-Control'].startswith('private'), chemin)   # jamais « public » : aucun cache partagé

    def test_U07_session_revalidee_par_compte_sans_identifiant(self):
        r = self.request('/api440/m5689/affichage/session')
        self.assertTrue(r['status'].startswith('200'), r['body'])
        valeur = json.loads(r['body'])
        self.assertEqual(valeur, {'compte': affichage5696.empreinte_compte('cabinet')})
        self.assertNotEqual(affichage5696.empreinte_compte('cabinet'), affichage5696.empreinte_compte('camille'))
        page = self.request('/dossiers')['body']
        self.assertIn('<meta name="axiorhub-compte" content="%s">' % valeur['compte'], page)
        self.assertTrue(self.request('/api440/m5689/affichage/session', auth=False)['status'].startswith(('401', '303')))   # session fermée : refus
        self.assertTrue(self.request('/api440/m5689/affichage/inconnue')['status'].startswith(('403', '404')))

    def test_U07_retour_par_historique_seulement_meme_liste_meme_compte(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node absent : le parcours navigateur couvre aussi ce cas')
        r = subprocess.run([node, str(Path(__file__).with_name('navigation5696.cjs')), str(ROOT)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('ok 4 cas', r.stdout)


if __name__ == '__main__':
    unittest.main()

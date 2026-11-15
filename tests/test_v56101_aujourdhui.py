"""5.6.101, lot P : Aujourd'hui allégé (cahier § 7 : P01-P04, V101-08 ; garanties R04 et R05 rejouées sur les fragments).

P01 — premier envoi : en-tête, bandeau de sécurité (contrat lot S), « Actuellement », bandeau des routines, trois premières lignes du rang,
« Ma journée » ; cartes de la file historique, formulaires des routines, comptes rendus du briefing, style et cartes de
« Travail préparé » remplacés par une attente remplie par ``GET /api440/m56101/aujourdhui/fragment``. Les deux présentations
(interrupteur ``interface56100``) portent les mêmes attentes.
P02 — mesures : ``scripts/mesure56101.py`` (essai court ici ; mesure à 900 dossiers dans le compte rendu) ; réglages lus une fois par page,
préparations de schéma faites une fois, décisions calculées une fois (copies distinctes pour chaque appelant).
P03 — chaque attente porte un lien vers la page complète ; la page ``?vue=routines`` (formulaires et comptes rendus) existe.
P04 — retour par Précédent sans nouvelle requête : vérifié sur le script (aucun vrai navigateur ici).
R04 — compte restreint : rien d'un dossier réservé dans un fragment ; routine d'un autre compte : même 404 qu'un identifiant inconnu.
R05 — aucune écriture (INSERT, UPDATE, DELETE, REPLACE) pendant un fragment, comptée sur toutes les connexions ; mutation détectée.
Données fictives (DURAND, MARTIN, DOS-0001, example.test).
"""
import json
from pathlib import Path
import re
import sqlite3
import sys
import unittest
from unittest.mock import patch

import test_v56100_integrite as integ
from contextlib import contextmanager
import threading
from test_v56100_integrite import CIBLE, ecritures_metier
from test_v5689_carbon import document
from agent import affichage5696, aujourdhui56101, interface56100, membres5650, routines5652
from agent.common import set_request_mode

ROOT = Path(__file__).resolve().parent.parent
P = '/agent-courriel'
FRAGMENT = '/api440/m56101/aujourdhui/fragment'
DIFFERES = ('travail', 'file', 'style', 'briefing')
# État de lecture des sources du registre des documents préparés (``livrables5689._sync_source``) : écriture technique déjà faite par la page
# complète avant la 5.6.101 (une source sans table est notée « absente » à chaque lecture), hors métier ; signalée dans le compte rendu.
TECHNIQUES_P101 = frozenset(('prepared_sources5689',))
RESERVE_TITRE = 'Projet réservé fictif ZORVAL'
VISIBLE_TITRE = 'Projet visible fictif DURAND'


@contextmanager
def compter_ecritures():
    """Comme ``test_v56100_integrite.compter_ecritures`` (toutes les connexions ouvertes pendant le bloc), mais seulement les requêtes du fil
    de la requête : un automatisme lancé en parallèle par l'application d'essai n'est pas attribué au fragment. La préparation de la base à
    l'ouverture du bureau (``Desk._prepare_once`` : migrations, rattrapages, gardée par ``test_v5644``) n'est pas une écriture de la lecture."""
    vues, fil = [], threading.get_ident()
    vrai = sqlite3.connect

    def noter(sql):
        if threading.get_ident() != fil:
            return
        cadre = sys._getframe(1)
        while cadre is not None:
            if cadre.f_code.co_name == '_prepare_once' and cadre.f_code.co_filename.replace(chr(92), '/').endswith('agent/desk.py'):
                return
            cadre = cadre.f_back
        vues.append(sql)

    def connect(*a, **k):
        cx = vrai(*a, **k)
        cx.set_trace_callback(noter)
        return cx
    with patch('sqlite3.connect', connect):
        yield vues


class Cabinet(integ.Base):
    def setUp(self):
        super().setUp()
        document(self.desk, 'p101-visible', 'contrat', VISIBLE_TITRE, matter=integ.VISIBLE)
        document(self.desk, 'p101-reserve', 'contrat', RESERVE_TITRE, matter=integ.RESERVE)

    def page(self, cookie=None, **args):
        r = self.lire('/aujourdhui', cookie, **args)
        self.assertTrue(r['status'].startswith('200'), r['status'])
        return r['body'].decode('utf-8')

    def fragment(self, nom, cookie=None, **args):
        r = self.lire(FRAGMENT, cookie, nom=nom, **args)
        return r, json.loads(r['body'].decode('utf-8') or '{}')

    def routine_modifiable(self):
        lignes = [r for r in routines5652.listing(self.desk, 'cabinet', 'administrateur')['routines'] if r['editable']]
        self.assertTrue(lignes, 'aucune routine modifiable dans le cabinet fictif')
        return lignes[0]


# =========================================================================================== P01
class PremierEnvoi(Cabinet):
    def verifier_page(self, html, nouvelle):
        for repere in ('class="f96-bandeau"', 'id="ax95-actuellement"', 'r99-bandeau', 'id="f96-decider"', 'class="f96-journee'):
            self.assertIn(repere, html)
        self.assertEqual(' f100' in html[:html.index('id="c530"') + 200] or 'class="c530 a89 f100"' in html, nouvelle)
        for nom in DIFFERES:
            self.assertEqual(html.count('data-p101-fragment="%s"' % nom), 1, nom)
        self.assertIn('data-p101-fragment="routine"', html)
        travail = html[html.index('id="a89-travail-zone"'):]
        self.assertTrue(travail.startswith('id="a89-travail-zone"><div class="p101-fragment" data-p101-fragment="travail"'), travail[:120])
        self.assertNotIn('a89-liste-tout', html)                       # cartes de Travail préparé : fragment
        self.assertNotIn('r52-form', html)                             # formulaires des routines (liste complète des dossiers) : fragment
        self.assertIn('id="t88-list"', html)                           # personnalisation gardée (parcours P95b : liste lue dès l'ouverture)
        suite = re.search(r'<details class="f96-suite" data-f96-suite>.*?</details>', html, re.S)
        self.assertTrue(suite and 'data-p101-fragment="file"' in suite.group(0))
        self.assertIn('class="c5657-actions-panel"', html)            # panneau des décisions gardé : « Ouvrir » d'une décision sans attente
        rapports = html[html.index('<dialog class="ui89-panneau" id="a89-rapports"'):]
        self.assertIn('data-p101-fragment="briefing"', rapports[:rapports.index('</dialog>')])
        self.assertIn('id="c530-t-routines"', rapports)               # le panneau garde son titre (aria-labelledby)
        self.assertEqual(html.count('/static/v56101-aujourdhui.js'), 1)
        self.assertEqual(html.count('id="p101-messages"'), 1)

    def test_P01_premier_envoi_reduit_nouvelle_presentation(self):
        self.verifier_page(self.page(), True)

    def test_P01_premier_envoi_reduit_ancienne_presentation(self):
        interface56100.regler_cabinet(self.desk, False)
        self.verifier_page(self.page(), False)

    def test_P01_fragments_rendent_les_blocs_complets(self):
        self.page()
        attendus = {'travail': 'a89-liste-tout', 'style': 'Style', 'briefing': 'id="c530-t-routines"'}
        for nom, repere in attendus.items():
            r, v = self.fragment(nom)
            self.assertTrue(r['status'].startswith('200'), (nom, r['status'], v))
            self.assertIn(repere, v['html'], nom)
            self.assertEqual(dict(r['headers']).get('Cache-Control'), 'private, no-store', nom)
        r, v = self.fragment('file')
        self.assertTrue(r['status'].startswith('200'), v)
        self.assertIsInstance(v['html'], str)
        routine = self.routine_modifiable()
        r, v = self.fragment('routine', id=routine['id'])
        self.assertTrue(r['status'].startswith('200'), v)
        self.assertIn('data-api="m5652/routine/modifier"', v['html'])
        self.assertIn('value="%s"' % routine['id'], v['html'])
        r, v = self.fragment('routine')
        self.assertIn('data-api="m5652/routine/creer"', v['html'])

    def test_P01_formulaires_differes_identiques_au_bloc_complet(self):
        """Le formulaire servi au dépli est celui que le bloc complet construisait (mêmes champs, même liste de dossiers)."""
        from agent.web5652 import routines_block
        complet = routines_block(self.desk, {'owner': 'cabinet'}, P, 'administrateur', compact=True)
        differe = routines_block(self.desk, {'owner': 'cabinet'}, P, 'administrateur', compact=True, formulaires_differes=True)
        routine = self.routine_modifiable()
        _r, v = self.fragment('routine', id=routine['id'])
        self.assertIn(v['html'], complet)
        self.assertNotIn('r52-form', differe)
        self.assertEqual(differe.count('<details class="r52-edit">'), complet.count('<details class="r52-edit">'))
        for commande in ('m5652/routine/activer', 'Modifier', 'r52-card', 'id="c5614-pause"', 'Ajouter une routine'):
            self.assertEqual(differe.count(commande), complet.count(commande), commande)

    def test_P01_nom_inconnu_et_ecriture_refuses_sans_effet(self):
        r, v = self.fragment('inconnu')
        self.assertTrue(r['status'].startswith('404'))
        self.assertEqual(v['error'], 'introuvable')
        sortie = aujourdhui56101.repondre({}, self.desk, {}, P, 'm56101/aujourdhui/fragment', {'nom': 'style'}, 'POST')
        self.assertTrue(sortie['status'].startswith('404'))
        sortie = aujourdhui56101.repondre({}, self.desk, {}, P, 'm56101/aujourdhui/autre', {'nom': 'style'}, 'GET')
        self.assertTrue(sortie['status'].startswith('404'))

    def test_P01_panne_d_une_source_dite_jamais_un_vide(self):
        with patch.dict(aujourdhui56101.CONSTRUCTEURS, {'style': lambda *a: 1 / 0}):
            r, v = self.fragment('style')
        self.assertTrue(r['status'].startswith('503'))
        self.assertEqual(v['error'], 'indisponible')
        js = (ROOT / 'agent' / 'static' / 'v56101-aujourdhui.js').read_text(encoding='utf-8')
        self.assertIn("setAttribute('data-p101-etat', 'panne')", js)

    def test_P01_contrat_securite_bandeau_sous_l_en_tete(self):
        import agent.securite56101 as sec
        with patch.object(sec, 'bandeau_html', lambda desk, prefix, owner, role: '<div id="banc-securite-p101">Alerte fictive</div>'):
            html = self.page()
        fin = html.index('</header>', html.index('class="c530-top'))
        self.assertTrue(html[fin + len('</header>'):].startswith('<div id="banc-securite-p101">'), html[fin:fin + 120])
        with patch.object(sec, 'bandeau_html', lambda *a: 1 / 0):        # une panne de la veille ne cache jamais Aujourd'hui
            html = self.page()
        self.assertNotIn('banc-securite-p101', html)
        self.assertIn('id="ax95-actuellement"', html)


# =========================================================================================== P02
class Calculs(Cabinet):
    def test_P02_decisions_calculees_une_fois_par_page(self):
        from agent import aujourdhui5614
        vrai, appels = aujourdhui5614._items, []

        def compte(*a, **k):
            appels.append(1)
            return vrai(*a, **k)
        with patch.object(aujourdhui5614, '_items', compte):
            self.page()
        self.assertEqual(len(appels), 1)

    def test_P02_reglage_lu_une_fois_puis_relu_apres_ecriture(self):
        vues = []
        self.desk.db.set_trace_callback(vues.append)
        self.addCleanup(self.desk.db.set_trace_callback, None)
        self.desk.setting('banc:p101', {'n': 1})
        affichage5696.demarrer()
        set_request_mode('read')
        try:
            premier = self.desk.settings('banc:p101')
            premier['n'] = 99                                               # copie propre à l'appelant
            self.assertEqual(self.desk.settings('banc:p101'), {'n': 1})
            lus = [s for s in vues if 'FROM settings WHERE key' in s]
            self.assertEqual(len(lus), 1)
            self.desk.setting('banc:p101', {'n': 2})                        # écriture de la connexion : relue
            self.assertEqual(self.desk.settings('banc:p101'), {'n': 2})
        finally:
            affichage5696.terminer()
            set_request_mode('write')
        vues.clear()
        self.desk.settings('banc:p101')
        self.desk.settings('banc:p101')
        self.assertEqual(len([s for s in vues if 'FROM settings WHERE key' in s]), 2)   # hors affichage : toujours relu

    def test_P02_schema_et_tables_une_fois_par_affichage(self):
        appels = []
        affichage5696.demarrer()
        set_request_mode('read')
        try:
            for _ in range(3):
                affichage5696.une_fois(self.desk.db, 'banc', lambda: appels.append(1))
            self.assertEqual(len(appels), 1)
            self.assertTrue(affichage5696.table_presente(self.desk.db, 'settings'))
            self.assertFalse(affichage5696.table_presente(self.desk.db, 'table_absente_p101'))
        finally:
            affichage5696.terminer()
            set_request_mode('write')
        affichage5696.une_fois(self.desk.db, 'banc', lambda: appels.append(1))
        self.assertEqual(len(appels), 2)

    def test_V101_08_script_de_mesure_court(self):
        import importlib.util
        import io
        from contextlib import redirect_stdout
        spec = importlib.util.spec_from_file_location('mesure56101_essai', ROOT / 'scripts' / 'mesure56101.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tampon = io.StringIO()
        with redirect_stdout(tampon):
            self.assertEqual(module.main(['--essais', '1', '--dossiers', '20', '--propositions', '5']), 0)
        rapport = json.loads(tampon.getvalue())
        noms = {s['scenario']: s for s in rapport['scenarios']}
        self.assertEqual(set(noms), {'aujourdhui', 'aujourdhui_ancienne'} | {'fragment_' + f for f in aujourdhui56101.FRAGMENTS})
        for s in noms.values():
            self.assertNotIn('statut', s, s)                                # chaque route a répondu 200
            self.assertIn('respecte', s)
        self.assertIn('octets_max', noms['aujourdhui']['respecte'])      # l'objectif de la page entière est jugé par le rapport (compte rendu)

    def test_P02_contenu_propre_d_aujourdhui_allege(self):
        """Contenu d'Aujourd'hui (hors menu et volet communs à toutes les pages) : sous 80 ko ; il dépassait 230 ko avec 900 dossiers."""
        html = self.page()
        debut = html.index('<div class="c530 a89')
        self.assertLess(len(html[debut:].encode('utf-8')), 80 * 1024)


# =========================================================================================== P03, P04
class SansScript(Cabinet):
    def test_P03_chaque_attente_mene_a_la_page_complete(self):
        html = self.page()
        zones = re.findall(r'<div class="p101-fragment" data-p101-fragment="(\w+)"[^>]*><p class="p101-attente" role="status">[^<]*<a href="([^"]+)">', html)
        self.assertEqual({n for n, _ in zones}, set(DIFFERES) | {'routine'})
        for nom, lien in zones:
            self.assertEqual(lien, P + aujourdhui56101.COMPLETES[nom], nom)
        corps = self.page(vue='routines')
        self.assertIn('r52-form', corps)
        self.assertIn('id="c530-t-routines"', corps)

    def test_P04_retour_sans_nouvelle_requete(self):
        """Vérification du script seulement : le comportement réel au bouton Précédent est à relever dans le banc navigateur."""
        js = (ROOT / 'agent' / 'static' / 'v56101-aujourdhui.js').read_text(encoding='utf-8')
        self.assertIn("n.type === 'back_forward'", js)
        self.assertIn("'ax101:' + compte + ':' + prefix", js)                # gardé par compte : jamais remis à un autre compte
        self.assertIn('var DUREE = 10 * 60 * 1000;', js)
        demarrer = js[js.index('function demarrer()'):]
        bloc = demarrer[demarrer.index('if (html !== null)'):demarrer.index('return;')]
        self.assertNotIn('fetch', bloc)                                     # le fragment repris n'est pas redemandé
        self.assertIn('poser(z, html)', bloc)
        self.assertNotIn('innerHTML = messages', js)
        self.assertIn("'/static/v56101-aujourdhui.js'", (ROOT / 'agent' / 'web440.py').read_text(encoding='utf-8'))


# =========================================================================================== R04
class Droits(Cabinet):
    def test_R04_compte_restreint_ne_voit_rien_du_dossier_reserve(self):
        cookie = self.restreint()
        self.page(cookie)
        _r, v = self.fragment('travail', cookie)
        self.assertIn(VISIBLE_TITRE, v['html'])
        self.assertNotIn(RESERVE_TITRE, v['html'])
        for nom in ('file', 'briefing', 'style'):
            r, v = self.fragment(nom, cookie)
            self.assertTrue(r['status'].startswith('200'), (nom, v))
            self.assertNotIn(RESERVE_TITRE, v['html'], nom)
            self.assertNotIn(integ.RESERVE, v['html'], nom)
        _r, v = self.fragment('travail')                                    # l'administrateur voit les deux
        self.assertIn(RESERVE_TITRE, v['html'])

    def test_R04_routine_d_un_autre_compte_meme_404_qu_inconnue(self):
        autre = routines5652.create(self.desk, 'autre@example.test', 'avocat', {'title': 'Routine personnelle fictive', 'task_type': 'synthese',
                                                                                  'instruction': 'Point fictif des dossiers.', 'freq': 'quotidien',
                                                                                  'time': '08:00', 'scope': 'personnel'})
        cookie = self.restreint()
        r1, v1 = self.fragment('routine', cookie, id=autre['id'])
        r2, v2 = self.fragment('routine', cookie, id='r52-inexistante')
        self.assertEqual((r1['status'], v1), (r2['status'], v2))
        self.assertTrue(r1['status'].startswith('404'))
        self.assertNotIn('Routine personnelle fictive', json.dumps(v1, ensure_ascii=False))

    def test_R04_role_qui_ne_cree_jamais_de_routine_refuse(self):
        with self.assertRaises(Exception) as ctx:
            aujourdhui56101.fragment(self.desk, P, 'assistante@example.test', 'assistant', 'routine', {})
        self.assertIn('role_insuffisant', str(ctx.exception))
        sortie = aujourdhui56101.repondre({'HTTP_X_AXIORHUB_USER': 'x@example.test', 'HTTP_X_AXIORHUB_ROLE': 'assistant'}, self.desk, {}, P,
                                          'm56101/aujourdhui/fragment', {'nom': 'routine'}, 'GET')
        self.assertTrue(sortie['status'].startswith('403'))
        sortie = aujourdhui56101.repondre({'HTTP_X_AXIORHUB_USER': 'x@example.test', 'HTTP_X_AXIORHUB_ROLE': 'inconnu'}, self.desk, {}, P,
                                          'm56101/aujourdhui/fragment', {'nom': 'style'}, 'GET')
        self.assertTrue(sortie['status'].startswith('403'))


# =========================================================================================== R05
class LectureSeule(Cabinet):
    def fragments_sans_ecriture(self, cookie=None):
        self.page(cookie)                                                    # affichage normal d'abord (sources déjà lues)
        routine = self.routine_modifiable()
        ecrites = []
        for nom, args in [(n, {}) for n in DIFFERES] + [('routine', {'id': routine['id']}), ('routine', {})]:
            for _ in range(2):
                with compter_ecritures() as vues:
                    r, _v = self.fragment(nom, cookie, **args)
                if cookie is None or nom != 'routine' or not args:
                    self.assertFalse(r['status'].startswith('5'), (nom, r['status']))
                ecrites += [(nom, s) for s in ecritures_metier(vues) if CIBLE.match(s).group(1) not in TECHNIQUES_P101]
        return ecrites

    def test_R05_fragments_n_ecrivent_rien_administrateur(self):
        self.assertEqual(self.fragments_sans_ecriture(), [])

    def test_R05_fragments_n_ecrivent_rien_compte_restreint(self):
        self.assertEqual(self.fragments_sans_ecriture(self.restreint()), [])

    def test_R05_mutation_fragment_qui_ecrit_detecte(self):
        def ecrit(desk, *a):
            desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('banc:p101:mutation', '1'))
            desk.db.commit()
            return ''
        with patch.dict(aujourdhui56101.CONSTRUCTEURS, {'style': ecrit}):
            with compter_ecritures() as vues:
                self.fragment('style')
        self.assertTrue(any('settings' in s for s in ecritures_metier(vues)))


if __name__ == '__main__':
    unittest.main()

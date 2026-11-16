"""5.6.102, lot N (navigation et libellés) : « À relire » devient un accès principal distinct (``/a-relire``), juste après « Aujourd'hui » ;
« Courriels » ne montre plus que la boîte de réception ; les anciennes adresses d'un élément à relire redirigent ; « Messagerie » (interne)
devient « Messagerie instantanée ».

Données entièrement fictives (dossiers DOS-001 et DOS-RES, adresses example.test) ; aucun réseau, aucun modèle, aucune lecture IMAP."""
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_desk
import test_v56100_relecture as r100
from agent import familles56101 as fam, fonctions5631 as reg, i18n5631, interface56100, navigation5643, shell501, web440

RACINE = Path(__file__).resolve().parents[1]
JETON, ORIGINE = r100.JETON, r100.ORIGINE
AVOCAT_B = r100.AVOCAT_B


def _menu(html):
    """Chemins de la barre principale (``nav.ws-primary``), dans l'ordre affiché."""
    nav = html.split('class="ws-primary"', 1)[1].split('</nav>', 1)[0]
    return re.findall(r'href="/p(/[a-z-]+)"', nav)


class Base(r100.Base):
    def route(self, path, args=None, compte='cabinet', role='administrateur'):
        """Requête GET par le routeur de l'application (contexte de langue et de compte posé comme par ``web.py``)."""
        env = {'HTTP_X_AXIORHUB_USER': compte, 'HTTP_X_AXIORHUB_ROLE': role, 'REQUEST_METHOD': 'GET'}
        shell501.set_context(self.f.c, dict(args or {}), path)
        i18n5631.begin(env, self.f.c, compte, role)
        self.addCleanup(i18n5631.begin, {}, self.f.c, '', 'administrateur')
        return web440.route(env, self.f.c, {'csrf': JETON, 'origin': ORIGINE}, '/p', path, dict(args or {}), 'GET')


# ------------------------------------------------------------------------------------------------ N01 · menu
class Menu(Base):
    def test_N01_a_relire_apres_aujourdhui_puis_courriels(self):
        with patch('agent.drafts440.list_drafts', side_effect=AssertionError('aucune lecture IMAP au rendu')):
            html = self.route('/courriels')['body']
        ordre = _menu(html)
        self.assertEqual(ordre[:4], ['/aujourdhui', '/a-relire', '/courriels', '/messagerie'])
        self.assertLessEqual(len(ordre), 8)                                    # 8 entrées principales au plus (Talk compris)
        self.assertIn('aria-current="page"', html.split('class="ws-primary"', 1)[1].split('href="/p/courriels"', 1)[1].split('</a>', 1)[0])
        self.assertEqual(reg.PRIMARY, navigation5643.PRIMARY)
        self.assertEqual(reg.PRIMARY[:3], ('aujourdhui', 'a_relire', 'courriels'))
        self.assertEqual(navigation5643.visibility('a_relire'), 'primary')

    def test_N01_huit_entrees_au_plus_avec_talk_et_icone(self):
        from agent import talk5643
        with patch.object(talk5643, 'visible', lambda cfg, owner: True):
            ordre = _menu(self.route('/a-relire')['body'])
        self.assertEqual(len(ordre), 8)
        self.assertEqual(ordre[ordre.index('/planning') + 1], '/talk')
        from agent.ui_navigation5689 import ICONES_MENU
        self.assertEqual(ICONES_MENU['/a-relire'], 'voir')
        # libellés longs (« Messagerie instantanée ») : retour à la ligne au lieu de déborder du menu déplié à 390 px (parcours P100r)
        self.assertIn('v56102-navigation.css', shell501.BUNDLE)
        self.assertIn('/static/v56102-navigation.css', web440.STATIC)
        css = (RACINE / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('#ws-sidebar .ws-primary .ws-nav-label{white-space:normal;overflow-wrap:anywhere;min-width:0}', css)
        self.assertLess(css.index('.ws-nav-label{white-space:nowrap}'), css.index('.ws-nav-label{white-space:normal'))   # vient après la règle 3.2.0

    def test_N01_ancienne_presentation_sans_entree_distincte(self):
        """Interrupteur 5.6.100 éteint pour ce compte : menu et Courriels comme avant (« À relire » onglet par défaut de Courriels)."""
        interface56100.regler_compte(self.desk, 'cabinet', False)
        with patch('agent.drafts440.list_drafts', return_value={'items': [], 'uidvalidity': '7', 'folder': 'Drafts'}):
            html = self.route('/courriels')['body']
            relire = self.route('/a-relire')['body']                          # lien direct : servi, même vue historique
            ancien = self.route('/courriels', {'item': 'p-' + '1' * 32})      # aucune redirection en présentation historique
        self.assertNotIn('/a-relire', _menu(html))
        self.assertEqual(_menu(html)[:2], ['/aujourdhui', '/courriels'])
        self.assertIn('id="ax-drafts"', html)
        self.assertIn('id="ax-drafts"', relire)
        self.assertNotIn('f101-onglets', html)                                 # pas de barre de famille en double
        self.assertEqual(ancien['status'], '200 OK')
        # mutation : sans le contrôle de l'interrupteur, l'entrée distincte apparaîtrait aussi dans l'ancienne présentation
        with patch.object(shell501, 'relire_separe', lambda *a, **k: True), \
                patch('agent.drafts440.list_drafts', return_value={'items': [], 'uidvalidity': '7', 'folder': 'Drafts'}):
            self.assertIn('/a-relire', _menu(self.route('/courriels')['body']))

    def test_N01_barre_de_famille_courriels_et_a_relire(self):
        self.assertEqual(fam.membres('courriels'), ['courriels', 'a_relire'])
        self.assertEqual(len(fam.FAMILLES), 33)
        self.assertEqual(fam.audit(), [])
        html = self.route('/a-relire')['body']
        barre = html.split('f101-onglets" data-f101-famille="courriels"', 1)[1].split('</nav>', 1)[0]
        self.assertEqual(re.findall(r'href="/p(/[a-z-]+)"', barre), ['/courriels', '/a-relire'])
        self.assertIn('href="/p/a-relire" aria-current="page"', barre)


# ------------------------------------------------------------------------------------------------ N02 · page « À relire »
class PageARelire(Base):
    def test_N02_liste_unique_et_lecteur_sans_lecture_imap(self):
        k = self.proposition(1, sujet='Calendrier <script>')
        ident = self.ident(k)
        with patch('agent.drafts440.list_drafts', side_effect=AssertionError('aucune lecture IMAP au rendu de la page')):
            out = self.route('/a-relire', {'item': ident})
        html = out['body']
        self.assertEqual(out['status'], '200 OK')
        self.assertIn('data-ax100-relecture', html)
        self.assertIn('data-ax100-lecteur', html)
        self.assertIn('/static/v56100-lecteur.js', html)
        self.assertIn('Calendrier &lt;script&gt;', html)
        self.assertIn('/p/a-relire?item=' + ident, html)                       # repli sans script : adresse restaurable
        self.assertIn('action="/p/a-relire"', html)                            # recherche
        self.assertIn('href="/p/a-relire?source=document"', html)              # filtres
        self.assertNotRegex(html, r'/courriels\?(item|source|uid)=')
        self.assertIn('<title>À relire · AxiorHub Pilote</title>', html)

    def test_N02_element_interdit_meme_reponse_qu_un_element_absent(self):
        k = self.proposition(2, matter=r100.RESERVE, sujet='Confidentiel ZORVAL')
        ident = self.ident(k)
        absent = 'p-' + '1' * 32
        interdit_page = self.route('/a-relire', {'item': ident}, *AVOCAT_B)['body']
        absent_page = self.route('/a-relire', {'item': absent}, *AVOCAT_B)['body']
        self.assertNotIn('Confidentiel', interdit_page)
        sans_heure = lambda page: __import__('re').sub(r' \(dernier passage : [^)]*\)', '', page)   # 5.6.103 : heure d'un travail de fond entre les deux requêtes
        self.assertEqual(sans_heure(interdit_page.replace(ident, 'X')), sans_heure(absent_page.replace(absent, 'X')))   # aucune différence observable
        a = self.appel('m56100/relecture/' + ident, compte=AVOCAT_B[0], role=AVOCAT_B[1])
        b = self.appel('m56100/relecture/' + absent, compte=AVOCAT_B[0], role=AVOCAT_B[1])
        self.assertEqual((a[0]['status'], a[1]), (b[0]['status'], b[1]))       # même 404 neutre qu'avant
        self.assertTrue(a[0]['status'].startswith('404'))
        self.assertIn('Confidentiel ZORVAL', self.route('/a-relire', {'item': ident}, 'titulaire@example.test', 'avocat')['body'])

    appel = r100.Routes.appel


# ------------------------------------------------------------------------------------------------ N03 · Courriels = boîte de réception
class Courriels(Base):
    def test_N03_courriels_sans_parametre_est_la_boite_de_reception(self):
        self.proposition(3, sujet='Projet à relire fictif')
        with patch('agent.drafts440.list_drafts', side_effect=AssertionError('brouillons lus pour la boîte de réception')):
            out = self.route('/courriels')
            boite = self.route('/courriels', {'vue': 'boite'})['body']
        html = out['body']
        self.assertEqual(out['status'], '200 OK')
        self.assertIn('id="bx5625"', html)
        self.assertNotIn('data-ax100-relecture', html)
        self.assertNotIn('id="ax-drafts"', html)
        self.assertNotIn('Projet à relire fictif', html)
        self.assertNotIn('>À relire</a></nav>', html.split('class="ws-primary"', 1)[1].split('</nav>', 1)[1][:3000].replace('f101', ''))
        self.assertIn('id="bx5625"', boite)                                    # ancienne adresse de la boîte conservée

    def test_N03_messagerie_desactivee_etat_reel_sans_lecture(self):
        """Messagerie non choisie : Courriels dit l'état réel et ne monte pas la boîte (son script lirait l'IMAP et échouerait en 400)."""
        self.f.c['mail']['enabled'] = False
        html = self.route('/courriels')['body']
        self.assertIn('id="bx5625-desactivee"', html)
        self.assertIn('La messagerie n’est pas activée', html)
        self.assertIn('href="/p/guide-installation?etape=messagerie"', html)
        self.assertNotIn('id="bx5625"', html)

    def test_N03_option_pilote_garde_ses_onglets(self):
        self.desk.setting('courriels5696:vue_assistee', True)
        html = self.route('/courriels')['body']
        onglets = html.split('class="bx5625-tabs"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('href="/p/courriels" aria-current="page">Boîte de réception', onglets)
        self.assertIn('href="/p/courriels?vue=assistante">À traiter avec le Pilote', onglets)
        self.assertNotIn('À relire', onglets)


# ------------------------------------------------------------------------------------------------ N04 · anciennes adresses
class Redirections(Base):
    def test_N04_quatre_formes_redirigent_en_302_avec_leurs_parametres(self):
        cas = (({'vue': 'relire'}, {}),
               ({'item': 'p-' + 'a' * 32}, {'item': ['p-' + 'a' * 32]}),
               ({'source': 'document', 'q': 'bail'}, {'source': ['document'], 'q': ['bail']}),
               ({'uid': '12', 'validity': '7'}, {'uid': ['12'], 'validity': ['7']}))
        for args, attendu in cas:
            out = self.route('/courriels', args)
            self.assertEqual(out['status'], '302 Found', args)
            cible = dict(out['headers'])['Location']
            parts = urlsplit(cible)
            self.assertEqual(parts.path, '/p/a-relire', args)
            self.assertEqual(parse_qs(parts.query), attendu, args)
            suite = self.route('/a-relire', parse_qs(parts.query) and {k: v[0] for k, v in parse_qs(parts.query).items()})
            self.assertEqual(suite['status'], '200 OK', args)                  # aucune boucle : la cible se sert elle-même

    def test_N04_vues_de_courriels_non_redirigees(self):
        self.desk.setting('courriels5696:vue_assistee', True)
        for args in ({}, {'vue': 'boite'}, {'vue': 'assistante'}, {'vue': 'assistante', 'fil': 'f' * 64}, {'dossier': 'DOS-001'}, {'vue': 'boite', 'uid': '12'}):
            self.assertEqual(self.route('/courriels', args)['status'], '200 OK', args)

    def test_N04_parametres_etrangers_non_recopies(self):
        out = self.route('/courriels', {'item': 'p-' + 'b' * 32, 'token': 'secret-fictif', 'vue': 'relire'})
        self.assertNotIn('token', dict(out['headers'])['Location'])
        self.assertNotIn('vue=', dict(out['headers'])['Location'])


# ------------------------------------------------------------------------------------------------ N05 · liens internes
class LiensInternes(Base):
    ANCIENS = re.compile(r'/courriels\?(?:[^"\'\s]*&)?(?:item|source|uid)=|/courriels\'\s*,\s*\'Courriels\'\)|\'Relire le brouillon\', \'/courriels\'')

    def test_N05_aucun_lien_vers_l_ancienne_adresse_dans_le_code(self):
        fautes = []
        for chemin in list((RACINE / 'agent').glob('*.py')) + list((RACINE / 'agent' / 'static').glob('*.js')):
            texte = chemin.read_text(encoding='utf-8', errors='replace')
            for m in self.ANCIENS.finditer(texte):
                fautes.append('%s : %s' % (chemin.name, m.group(0)))
        self.assertEqual(fautes, [])

    def test_N05_volet_fiche_resultats_et_relecture_pointent_vers_a_relire(self):
        from agent import cockpit460, fiche460, relecture56100 as rl, volet56101
        k = self.proposition(4, matter='DOS-001', sujet='Projet DURAND')
        ident = self.ident(k)
        ligne = rl.liste(self.desk, r100.ADMIN, {}, '/p')['lignes'][0]
        self.assertEqual(ligne['href'], '/p/a-relire?item=' + ident)          # liste, Aujourd'hui et volet partagent ce lien
        # volet d'Aujourd'hui : « page complète » = pleine page de l'élément quand elle existe, sinon la page « À relire »
        legers = {'contenu_relecture': lambda d, p: '', 'meta_relecture': lambda d: '', 'barre_relecture': lambda desk, d, p: ''}
        for liens, attendu in (({}, '/p/a-relire?item=g-fictif01'), ({'pleine_page': '/p/mail?key=' + k}, '/p/mail?key=' + k)):
            with patch.multiple(volet56101, detail_relecture=lambda *a, _l=liens: {'id': 'g-fictif01', 'type': 'gmail', 'sujet': 'Projet', 'liens': _l},
                                **legers):
                self.assertEqual(volet56101._fragment_relecture(self.desk, '/p', 'gmail', 'g-fictif01', 'cabinet', 'administrateur')['page'], attendu)
        # fiche du dossier et poste de pilotage historique
        self.desk.db.execute("INSERT INTO work_items(mail_key,matter,state,subject,updated) VALUES(?,?,?,?,?)",
                             ('m-fictif-1', 'DOS-001', 'draft_ready', 'Brouillon DURAND', self.desk.now()))
        self.desk.db.commit()
        fiche = fiche460.build_fiche(self.desk, 'DOS-001')
        hrefs = [x['href'] for x in fiche.get('pending', []) if x['kind'] == 'brouillon'] + [x['href'] for x in fiche.get('drafts', [])]
        self.assertTrue(hrefs)
        self.assertEqual(set(hrefs), {'/a-relire'})
        self.assertNotIn("'/courriels', 'À valider'", (RACINE / 'agent' / 'cockpit460.py').read_text(encoding='utf-8'))

    def test_N05_aujourdhui_rendu_sans_ancienne_adresse(self):
        web = test_desk.WebTests('test_all_four_views_require_authentication')
        web.setUp()
        self.addCleanup(web.doCleanups)
        body = web.request('/aujourdhui')['body']
        self.assertIn('href="/agent-courriel/a-relire"', body)
        self.assertNotRegex(body, r'/courriels\?(?:[^"]*&amp;)?(?:item|source|uid)=')


# ------------------------------------------------------------------------------------------------ N06 · libellés
class Libelles(unittest.TestCase):
    ATTENDU = {'fr': 'Messagerie instantanée', 'en': 'Instant messaging', 'de': 'Sofortnachrichten', 'es': 'Mensajería instantánea'}

    def catalogue(self, nom):
        return json.loads((RACINE / 'agent' / 'locales' / nom).read_text(encoding='utf-8'))

    def test_N06_messagerie_instantanee_dans_les_quatre_langues(self):
        for lang, texte in self.ATTENDU.items():
            cat = self.catalogue(lang + '.json')
            self.assertEqual(cat['fn.messagerie.label'], texte, lang)
            self.assertTrue(cat['fn.messagerie.desc'], lang)
            self.assertTrue(cat['fn.a_relire.label'] and cat['fn.a_relire.desc'], lang)
        for lang in ('en', 'de', 'es'):
            self.assertEqual(self.catalogue('ecrans_%s.json' % lang)['Messagerie instantanée'], self.ATTENDU[lang])
        from agent import pages5636
        self.assertEqual(pages5636.PAGES['/messagerie'], 'Messagerie instantanée')
        self.assertIn('<h1>Messagerie instantanée</h1>', (RACINE / 'agent' / 'web5650.py').read_text(encoding='utf-8'))

    def test_N06_messagerie_au_sens_de_courrier_electronique_inchangee(self):
        attendu = {'fr': 'Messagerie', 'en': 'Email', 'de': 'E-Mail', 'es': 'Correo'}
        for lang in ('fr', 'en', 'de', 'es'):
            self.assertEqual(self.catalogue(lang + '.json')['feat.messagerie'], attendu[lang], lang)
        en = self.catalogue('ecrans_en.json')
        self.assertEqual(en['Messagerie'], 'Mail')                             # écran des connexions : la messagerie électronique
        source = (RACINE / 'agent' / 'dossier5627.py').read_text(encoding='utf-8')
        self.assertIn("'/courriels?vue=boite', 'Messagerie', 'd27-link'", source)
        aide = (RACINE / 'agent' / 'aide' / 'fr.json').read_text(encoding='utf-8')
        self.assertNotIn('Messagerie instantanée configurée', aide)
        self.assertIn('La messagerie configurée', aide)


# ------------------------------------------------------------------------------------------------ N07 · Ctrl+K
class Palette(Base):
    def chercher(self, texte, role='administrateur'):
        from agent import recherche5682
        return recherche5682.search(self.desk, texte, 'cabinet', role, 'fr')['resultats']

    def test_N07_relecture_a_valider_propositions_menent_a_a_relire(self):
        for texte in ('relecture', 'à valider', 'propositions', 'À relire'):
            urls = [r['url'] for r in self.chercher(texte)]
            self.assertIn('/a-relire', urls, texte)

    def test_N07_messagerie_trouve_messagerie_instantanee(self):
        for texte in ('messagerie', 'Messagerie instantanée', 'messagerie du cabinet'):
            res = {r['url']: r['titre'] for r in self.chercher(texte)}
            self.assertEqual(res.get('/messagerie'), 'Messagerie instantanée', texte)
        for role in ('avocat', 'assistant'):
            self.assertIn('/messagerie', [r['url'] for r in self.chercher('messagerie', role)])
        # mutation : sans l'entrée hors registre, « messagerie » ne mènerait qu'à Courriels
        with patch.object(reg, 'HORS_REGISTRE', ()):
            self.assertNotIn('/messagerie', [r['url'] for r in self.chercher('messagerie')])


# ------------------------------------------------------------------------------------------------ N08 · registre figé et familles
class Registre(unittest.TestCase):
    def test_N08_registre_fige_conserve_et_33_familles(self):
        from agent import aide5631, fonctions5634, fonctions_etat5689 as etat, fonctions_reference5688 as reference
        c = etat.conservation()
        self.assertTrue(c['ok'], c)
        self.assertIn('a_relire', c['ajouts'])
        ligne = next(x for x in reference.REGISTRE if x[0] == 'courriels')
        self.assertEqual(reg.INDEX['courriels'], ligne[:5])                   # ligne « courriels » intacte
        self.assertEqual(fam.audit(), [])
        self.assertEqual(reg.audit(), [])
        self.assertEqual(aide5631.check(), [])
        self.assertIn('a_relire', fonctions5634.MIGRATION)
        self.assertEqual(reg.FAMILLE['a_relire'], 'courriels')
        for lang in ('fr', 'en', 'de', 'es'):
            self.assertEqual(aide5631.articles(lang)['a_relire']['version'], aide5631.articles(lang)['courriels']['version'])

    def test_N08_mutation_ligne_courriels_modifiee_detectee(self):
        from agent import fonctions_etat5689 as etat
        altere = tuple(('courriels', 'communiquer', '/courriels', ('Boîte de réception', 'Brouillons'), ('mail',)) if f[0] == 'courriels' else f
                       for f in reg.FUNCTIONS)
        self.assertFalse(etat.conservation(altere)['ok'])


if __name__ == '__main__':
    unittest.main()

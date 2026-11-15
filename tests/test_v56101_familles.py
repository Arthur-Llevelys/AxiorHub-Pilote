"""5.6.101, lot F : « Toutes les fonctions » regroupées en 33 familles à onglets (cahier § 6 et annexe A : F01-F06, V101-07).

Données fictives, aucun réseau. Les dates viennent du jour du cabinet (``acces5683.today_of``), jamais de ``date.today()``."""
import base64
from datetime import timedelta
import io
import json
import re
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import test_desk
from agent import familles56101 as fam, fonctions5631 as reg, usage56101
from agent.common import load_config
from agent.desk import Desk

PREFIX = '/agent-courriel'


class Base(unittest.TestCase):
    setUp0 = test_desk.WebTests.setUp

    def setUp(self):
        self.setUp0()

    def desk(self):
        d = Desk(load_config(str(self.config)))
        self.addCleanup(d.db.close)
        return d

    def request(self, path, query='', role=None, method='GET', body=b'', ctype='application/x-www-form-urlencoded', headers=None):
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query, 'HTTP_HOST': 'cabinet.example.test', 'HTTP_X_FORWARDED_PROTO': 'https',
               'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(body), 'CONTENT_LENGTH': str(len(body)), 'CONTENT_TYPE': ctype,
               'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('admin:' + self.password).encode()).decode()}
        if role:
            env.update(HTTP_X_AXIORHUB_ROLE=role, HTTP_X_AXIORHUB_USER=role + '@example.test')
        env.update(headers or {})
        out = {}

        def start(status, hdrs):
            out.update(status=status, headers=dict(hdrs))
        out['body'] = b''.join(self.app(env, start)).decode()
        return out

    def balise(self, data, origin=None, ctype='text/plain;charset=UTF-8', role=None):
        raw = json.dumps(data).encode()
        return self.request('/api440/m56101/usage', method='POST', body=raw, ctype=ctype, role=role, headers={'HTTP_ORIGIN': origin or self.origin})

    def post_json(self, route, data, role=None, csrf='test-csrf'):
        raw = json.dumps(data).encode()
        return self.request('/api440/m56101/' + route, method='POST', body=raw, ctype='application/json', role=role,
                            headers={'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': csrf})

    def service(self, route, data, role, csrf='test-csrf'):
        """Appel direct du service avec l'identité posée par la couche de connexion (le serveur de test ne croit pas les en-têtes reçus)."""
        raw = json.dumps(data).encode()
        env = {'REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': csrf, 'CONTENT_TYPE': 'application/json',
               'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_X_AXIORHUB_USER': role + '@example.test'}
        return usage56101.api(env, self.desk(), {'origin': self.origin, 'csrf': 'test-csrf'}, PREFIX, 'm56101/' + route, {}, 'POST')

    def compte(self, fid=None):
        d = self.desk()
        if fid:
            return d.db.execute('SELECT COALESCE(SUM(n),0) FROM usage56101 WHERE fonction=?', (fid,)).fetchone()[0]
        return d.db.execute('SELECT COUNT(*), COALESCE(SUM(n),0) FROM usage56101').fetchone()[:]


# ------------------------------------------------------------------------------------------------ F01 · registre
class Registre(unittest.TestCase):
    def test_F01_chaque_fonction_a_sa_famille_et_33_familles_dans_l_ordre_de_l_annexe(self):
        self.assertEqual(fam.audit(), [])
        self.assertEqual(reg.audit(), [])
        self.assertEqual(len(fam.FAMILLES), 33)
        self.assertEqual(set(reg.FAMILLE), {r[0] for r in reg.FUNCTIONS})
        self.assertEqual(len(reg.FUNCTIONS), 90)                                # 89 historiques + securite_serveur
        self.assertEqual(fam.membres('produire'), ['production', 'projets', 'livrables', 'agents_documents', 'studio', 'preparation'])
        self.assertEqual(fam.membres('rediger'), ['documents', 'missions_complexes', 'playbooks', 'formulaires'])
        self.assertEqual(fam.membres('securite'), ['securite_serveur', 'journal_audit'])
        self.assertEqual(fam.membres('parametres'), ['parametres', 'mcp', 'routage', 'ecosysteme', 'administration'])
        self.assertEqual([f for f, s in fam.FAMILLES if s == 'regler'],
                         ['parametres', 'mon_compte', 'ia_confiance', 'securite', 'etat_depannage', 'evaluations', 'aide', 'a_propos'])

    def test_F01_groupes_et_routes_historiques_inchanges(self):
        from agent import fonctions_etat5689
        c = fonctions_etat5689.conservation()
        self.assertEqual(c['retraits'] if 'retraits' in c else [], [])
        self.assertEqual([x for x in c.get('ecarts', []) if x['nature'] in ('groupe', 'route', 'alias', 'prerequis')], [])

    def test_F01_contrat_securite_serveur(self):
        row = reg.INDEX['securite_serveur']
        self.assertEqual(row[2], '/securite-serveur')
        for mot in ('Sécurité', 'Intrusion', 'Mineur', 'Serveur', 'Alerte', 'Veille de sécurité'):
            self.assertIn(mot, row[3])
        self.assertEqual(reg.FAMILLE['securite_serveur'], 'securite')
        self.assertIn('securite_serveur', {r['id'] for r in reg.entries(role='administrateur')})
        for role in ('avocat', 'assistant'):                                    # F04 : page réservée à l'administrateur
            self.assertNotIn('securite_serveur', {r['id'] for r in reg.entries(role=role)})
        with patch.dict(reg.ROLES, {}, clear=True):                             # mutation : sans la réserve, l'entrée apparaîtrait
            self.assertIn('securite_serveur', {r['id'] for r in reg.entries(role='avocat')})

    def test_F01_titre_fonction_famille(self):
        self.assertEqual(fam.titre_fonction('pieces', 'fr'), 'Pièces et bordereaux (Pièces et contentieux)')
        self.assertEqual(fam.titre_fonction('dossiers', 'fr'), 'Dossiers')     # même nom que la famille : pas de répétition
        self.assertEqual(fam.titre_fonction('securite_serveur', 'fr'), 'Sécurité du serveur (Sécurité)')


# ------------------------------------------------------------------------------------------------ V101-07 · panneau
class Panneau(Base):
    def visibles(self, html):
        return re.findall(r'<li data-search="[^"]*" data-f101-famille="([^"]+)"><a href="/agent-courriel([^"]+)"', html)

    def test_V101_07_panneau_au_plus_35_entrees(self):
        avant = 89                                                               # cahier § 1 : 89 entrées avant la 5.6.101
        html = reg.panel_html(PREFIX, '/aujourdhui', self.f.c)
        items = self.visibles(html)
        self.assertEqual(len(items), 33)
        self.assertLessEqual(len(items), 35)
        self.assertLess(len(items), avant)
        self.assertEqual([f for f, _ in items], [f for f, _ in fam.FAMILLES])   # ordre de l'annexe A
        self.assertEqual(html.count('<li data-search='), 33)                    # rien d'autre de visible sans recherche
        for role in ('avocat', 'assistant'):
            sien = self.visibles(reg.panel_html(PREFIX, '/aujourdhui', self.f.c, role))
            self.assertLessEqual(len(sien), 33)
            self.assertNotIn('securite', [f for f, _ in sien])                  # aucune fonction permise dans la famille Sécurité

    def test_V101_07_toute_fonction_trouvable_dans_le_panneau_avec_sa_famille(self):
        html = reg.panel_html(PREFIX, '/aujourdhui', self.f.c)
        cachees = re.findall(r'<li hidden data-search-only data-search="([^"]*)"><a href="/agent-courriel([^"]+)"><strong>([^<]+)</strong>', html)
        self.assertEqual(len(cachees), len(reg.FUNCTIONS))
        routes = {r for _, r, _ in cachees}
        for fid, group, route, aliases, needs in reg.FUNCTIONS:
            if fid != 'tableau_de_bord':
                self.assertIn(route, routes, fid)
        pieces = next(x for x in cachees if x[1] == '/pieces')
        self.assertIn('préparer un bordereau', pieces[0])
        self.assertIn('pièces et contentieux', pieces[0])                      # le nom de la famille trouve ses fonctions
        self.assertIn('Pièces et bordereaux (Pièces et contentieux)', pieces[2].replace('&#x27;', "'"))

    def test_V101_07_famille_de_la_page_marquee(self):
        html = reg.panel_html(PREFIX, '/contentieux', self.f.c)
        actif = re.findall(r'data-f101-famille="([^"]+)"><a href="[^"]+" aria-current="page"', html)
        self.assertEqual(actif, ['pieces_contentieux'])


# ------------------------------------------------------------------------------------------------ Ctrl+K
class Palette(Base):
    def test_V101_07_ctrl_k_retrouve_chaque_fonction_par_un_mot_cle_historique(self):
        from agent import palette5688
        d = self.desk()
        manquantes = []
        for fid, group, route, aliases, needs in reg.FUNCTIONS:
            trouve = False
            for mot in aliases:
                if len(mot) < 2:
                    continue
                res = palette5688.search(d, mot)
                fonctions = next((g['resultats'] for g in res['groupes'] if g['id'] == 'fonctions'), [])
                if any(r['url'] == route for r in fonctions):
                    trouve = True
                    break
            if not trouve:
                manquantes.append(fid)
        self.assertEqual(manquantes, [])

    def test_F01_ctrl_k_affiche_fonction_famille_et_respecte_le_role(self):
        from agent import palette5688
        d = self.desk()
        res = palette5688.search(d, 'bordereau')
        titres = [r['titre'] for g in res['groupes'] if g['id'] == 'fonctions' for r in g['resultats']]
        self.assertIn('Pièces et bordereaux (Pièces et contentieux)', titres)
        res = palette5688.search(d, 'intrusion', 'avocat@example.test', 'avocat')
        self.assertNotIn('/securite-serveur', [r['url'] for g in res['groupes'] for r in g['resultats']])
        res = palette5688.search(d, 'intrusion')
        self.assertIn('/securite-serveur', [r['url'] for g in res['groupes'] for r in g['resultats']])


# ------------------------------------------------------------------------------------------------ F02 · F03 · F04 · pages
class Pages(Base):
    def test_F03_les_adresses_repondent_et_portent_la_barre_de_leur_famille(self):
        """Toutes les adresses du registre (89 historiques + la nouvelle quand le lot S l'a servie) : 200, ou 302 suivi d'un 200 ; jamais 404."""
        resultats, sans_barre = {}, []
        for fid, group, route, aliases, needs in reg.FUNCTIONS:
            path, _, query = route.partition('?')
            r = self.request(path, query)
            code = r['status'][:3]
            if code == '302' or code == '303':
                loc = r['headers'].get('Location', '')
                p2, _, q2 = loc[len(PREFIX):].partition('?') if loc.startswith(PREFIX) else (loc, '', '')
                code = code + '>' + self.request(p2, q2)['status'][:3]
            resultats[fid] = code
            attendus = fam.onglets(route, 'administrateur')
            if attendus and code == '200':
                barre = re.search(r'<nav class="ws-tabs production-tabs f101-onglets" data-f101-famille="([^"]+)"[^>]*>(.*?)</nav>', r['body'], re.S)
                if not barre or barre.group(1) != reg.FAMILLE[fid] or ('href="%s%s" aria-current="page"' % (PREFIX, route.replace('&', '&amp;'))) not in barre.group(2):
                    sans_barre.append(fid)
        if 'securite_serveur' in resultats and resultats['securite_serveur'].endswith('404') and not hasattr(__import__('agent.securite56101', fromlist=['x']), 'page'):
            resultats.pop('securite_serveur')                                   # page du lot S pas encore servie dans cet arbre
        self.assertEqual({k: v for k, v in resultats.items() if not (v == '200' or v.endswith('>200'))}, {})
        self.assertEqual(sans_barre, [])
        self.assertGreaterEqual(len(resultats), 89)

    def test_F02_produire_et_rediger(self):
        body = self.request('/projets')['body']
        barre = re.search(r'<nav class="ws-tabs production-tabs f101-onglets" data-f101-famille="produire" aria-label="Onglets de la famille « Produire »">(.*?)</nav>', body, re.S).group(1)
        self.assertEqual(re.findall(r'>([^<]+)</a>', barre), ['Résultats', 'Projets en attente', 'Documents préparés', 'Résultats des agents', 'Studio de production', 'Préparation proactive'])
        self.assertIn('href="/agent-courriel/projets" aria-current="page"', barre)
        body = self.request('/playbooks')['body']
        self.assertIn('data-f101-famille="rediger"', body)
        self.assertIn('href="/agent-courriel/playbooks" aria-current="page">Parcours guidés</a>', body)
        self.assertNotIn('Rubriques de Produire', body)
        self.assertEqual(body.count('f101-onglets'), 1)                        # une seule barre
        self.assertIn('/static/v56101-familles.js', body)

    def test_F02_audiences_et_pages_isolees_sans_barre(self):
        self.assertEqual(fam.onglets('/audiences?onglet=suivi', 'administrateur'), [])   # la page porte déjà ses onglets
        self.assertEqual(fam.onglets_html(PREFIX, '/dossiers', 'administrateur'), '')     # famille d'une seule fonction
        self.assertEqual(fam.onglets_html(PREFIX, '/dossier', 'administrateur'), '')      # page de détail : pas de famille
        self.assertNotIn('f101-onglets', self.request('/dossiers')['body'])
        self.assertEqual(fam.fonction_de('/planning?vue=taches'), 'taches')
        self.assertEqual(fam.fonction_de('/planning'), 'planning')
        self.assertEqual(fam.fonction_de('/audiences?onglet=preparer'), 'audiences_word')

    def test_F04_onglets_selon_le_role_jamais_grises(self):
        from agent.standalone_auth import allowed
        for role in ('avocat', 'assistant'):
            for fid, group, route, aliases, needs in reg.FUNCTIONS:
                ids = [x[0] for x in fam.onglets(route, role)]
                for t in ids:
                    self.assertTrue(allowed(role, 'GET', reg.INDEX[t][2].split('?', 1)[0]) and reg.visible(role, reg.INDEX[t][2], t), (role, fid, t))
            self.assertEqual(fam.onglets_html(PREFIX, '/parametres/ia-visible', role).count('/parametres/'), 0)   # onglets réservés absents
        tabs = fam.onglets_html(PREFIX, '/routage-hybride', 'administrateur')
        self.assertIn('/agent-courriel/mcp', tabs)
        self.assertEqual(fam.onglets_html(PREFIX, '/ecosysteme', 'avocat'), '')   # une seule fonction permise : pas de barre
        self.assertNotIn('disabled', tabs)
        self.assertNotIn('aria-disabled', tabs)
        with patch('agent.fonctions5631.visible', return_value=True):            # mutation : sans le filtre de rôle, la barre réapparaîtrait
            self.assertIn('/agent-courriel/mcp', fam.onglets_html(PREFIX, '/ecosysteme', 'avocat'))
        self.assertIn('data-f101-famille="produire"', fam.onglets_html(PREFIX, '/projets', 'assistant'))

    def test_F06_guide_et_aide_contextuelle_par_familles(self):
        body = self.request('/guide-utilisation')['body']
        self.assertEqual(body.count('class="h5631-section"'), 6)
        self.assertIn('<h4>Pièces et contentieux</h4>', body)
        fiche = self.request('/guide-utilisation', 'fonction=pieces')['body']
        self.assertIn('Famille « Pièces et contentieux »', fiche)
        self.assertIn('/guide-utilisation?fonction=contentieux', fiche)
        self.assertIn('/guide-utilisation?fonction=pieces', self.request('/pieces')['body'])   # aide contextuelle de la page


# ------------------------------------------------------------------------------------------------ F05 · compteur d'usage
class Usage(Base):
    def test_F05_R05_aucune_ecriture_pendant_les_lectures(self):
        avant = self.compte()
        d = self.desk()
        depuis = d.settings(usage56101.DEPUIS, None)
        for path in ('/pieces', '/projets', '/aujourdhui', '/parametres', '/administration/fonctions-peu-utilisees', '/guide-utilisation'):
            r = self.request(path)
            self.assertTrue(r['status'].startswith('200'), path)
        self.assertEqual(self.compte(), avant)
        self.assertEqual(self.desk().settings(usage56101.DEPUIS, None), depuis)
        body = self.request('/pieces')['body']
        self.assertIn('<div hidden id="f101-usage" data-fonction="pieces" data-url="/agent-courriel/api440/m56101/usage"></div>', body)
        self.assertEqual(self.request('/api440/m56101/usage')['status'][:3], '405')   # GET refusé, sans écriture
        self.assertEqual(self.compte(), avant)

    def test_F05_balise_post_jeton_origine_rejeu(self):
        ok = self.balise({'fonction': 'pieces', 'csrf': 'test-csrf', 'cle': 'cle-essai-0001'})
        self.assertEqual(ok['status'][:3], '204')
        self.assertEqual(ok['headers'].get('Cache-Control'), 'private, no-store')
        self.assertEqual(self.compte('pieces'), 1)
        self.balise({'fonction': 'pieces', 'csrf': 'test-csrf', 'cle': 'cle-essai-0001'})             # rejeu : pas compté deux fois
        self.assertEqual(self.compte('pieces'), 1)
        self.balise({'fonction': 'pieces', 'csrf': 'test-csrf', 'cle': 'cle-essai-0002'})
        self.assertEqual(self.compte('pieces'), 2)
        for data, origin, ctype in (({'fonction': 'pieces', 'csrf': 'faux', 'cle': 'cle-essai-0003'}, None, 'text/plain'),
                                    ({'fonction': 'pieces', 'cle': 'cle-essai-0004'}, None, 'text/plain'),
                                    ({'fonction': 'pieces', 'csrf': 'test-csrf', 'cle': 'cle-essai-0005'}, 'https://ailleurs.example.test', 'text/plain'),
                                    ({'fonction': 'pieces', 'csrf': 'test-csrf', 'cle': 'cle-essai-0006'}, None, 'application/x-www-form-urlencoded')):
            r = self.balise(data, origin, ctype)
            self.assertEqual(r['status'][:3], '400', (data, origin, ctype))
        self.assertEqual(self.balise({'fonction': 'inconnue', 'csrf': 'test-csrf'})['status'][:3], '404')
        self.assertEqual(self.balise({'fonction': 'pieces', 'csrf': 'test-csrf', 'x': 'y' * 2000})['status'][:3], '400')
        self.assertEqual(self.compte('pieces'), 2)
        d = self.desk()
        cols = [r[1] for r in d.db.execute('PRAGMA table_info(usage56101)')]
        self.assertEqual(cols, ['fonction', 'jour', 'n'])                      # ni compte, ni contenu, ni adresse

    def test_F05_conservation_90_jours(self):
        from agent.acces5683 import today_of
        d = self.desk()
        jour = today_of(d)[0]
        d.db.executemany('INSERT INTO usage56101 VALUES(?,?,?)', [('veille', (jour - timedelta(days=90)).isoformat(), 3),
                                                                 ('veille', (jour - timedelta(days=89)).isoformat(), 2)])
        d.db.commit()
        self.assertTrue(usage56101.enregistrer(d, 'pieces'))
        jours = [r[0] for r in d.db.execute("SELECT jour FROM usage56101 WHERE fonction='veille'")]
        self.assertEqual(jours, [(jour - timedelta(days=89)).isoformat()])

    def test_F05_desactivable_version_et_role(self):
        d = self.desk()
        r = self.service('usage/reglage', {'actif': False, 'version': 0}, 'avocat')
        self.assertEqual(r['status'][:3], '403')
        self.assertEqual(self.service('usage/effacer', {'confirmer': True}, 'assistant')['status'][:3], '403')
        self.assertEqual(self.post_json('usage/reglage', {'actif': False, 'version': 0}, csrf='faux')['status'][:3], '400')
        r = self.post_json('usage/reglage', {'actif': False, 'version': 0})
        self.assertEqual(r['status'][:3], '200', r['body'])
        self.assertEqual(self.post_json('usage/reglage', {'actif': True, 'version': 0})['status'][:3], '409')   # version périmée
        self.assertFalse(usage56101.enregistrer(self.desk(), 'pieces'))
        self.assertEqual(self.compte('pieces'), 0)
        self.assertNotIn('id="f101-usage"', self.request('/pieces')['body'])    # désactivé : la page ne signale plus rien
        self.assertTrue(d.db.execute("SELECT 1 FROM audit WHERE action='usage56101_reglage'").fetchone())
        self.assertEqual(self.post_json('usage/reglage', {'actif': True, 'version': 1})['status'][:3], '200')
        self.assertTrue(usage56101.enregistrer(self.desk(), 'pieces'))
        self.assertEqual(self.post_json('usage/effacer', {})['status'][:3], '400')
        self.assertEqual(self.post_json('usage/effacer', {'confirmer': True})['status'][:3], '200')
        self.assertEqual(self.compte(), (0, 0))

    def test_F05_regles_de_route(self):
        from agent.standalone_auth import allowed
        self.assertTrue(allowed('assistant', 'POST', '/api440/m56101/usage'))
        self.assertFalse(allowed('avocat', 'POST', '/api440/m56101/usage/reglage'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m56101/usage/effacer'))
        self.assertTrue(allowed('administrateur', 'POST', '/api440/m56101/usage/reglage'))
        self.assertFalse(allowed('avocat', 'GET', usage56101.PAGE))

    def test_F05_fonctions_peu_utilisees(self):
        body = self.request(usage56101.PAGE)['body']
        self.assertIn('<h1 id="f101-usage-titre">Fonctions peu utilisées</h1>', body)
        self.assertIn('Contentieux', body)
        self.assertIn('data-f101-famille="parametres"', body)                  # famille Paramètres, onglet Administration actif
        self.assertIn('href="/agent-courriel/administration" aria-current="page"', body)
        self.assertIn('Le compteur fonctionne depuis moins de 60 jours', body)
        usage56101.enregistrer(self.desk(), 'contentieux')
        body = self.request(usage56101.PAGE)['body']
        self.assertNotIn('>Contentieux</a>', body)
        self.assertIn('>Pièces et bordereaux</a>', body)
        self.assertIsNone(usage56101.page(self.desk(), {'csrf': 'x'}, PREFIX, {'HTTP_X_AXIORHUB_ROLE': 'avocat'}, lambda *a, **k: 'page'))   # autre rôle : page absente
        self.assertIn('/administration/fonctions-peu-utilisees', self.request('/administration')['body'])
        with patch('agent.usage56101.releve', side_effect=__import__('sqlite3').OperationalError('panne')):
            body = self.request(usage56101.PAGE)['body']
        self.assertIn('Relevés illisibles pour le moment', body)               # panne dite, jamais une fausse liste


if __name__ == '__main__':
    unittest.main()

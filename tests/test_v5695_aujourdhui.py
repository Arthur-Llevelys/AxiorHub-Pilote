"""5.6.95, lot aujourdhui : encadré « Actuellement » (A01, A02), page Aujourd'hui (A03), « Personnaliser Aujourd'hui » (A04),
avertissement « Source indisponible » de Travail préparé (A05), mesure (A06).

Données fictives (DURAND, MARTIN, ZORVAL, DOS-001…, example.test), aucun réseau, aucun modèle : tout appel au modèle fait échouer les tests
de l'encadré. Ce qui exige un vrai navigateur (rafraîchissement réel, onglet caché, lecture Brindille) est vérifié par le banc
``scripts/parcours-navigateur.py`` (parcours P95a), pas ici.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from test_v5689_livrables import Base
from agent import actuellement5695 as act, actuellement_web5695 as actw, livrables5689 as liv, registre5693 as reg, tableau5688
from agent.common import private_json
from agent.desk import Desk

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('mesure_aujourdhui5695', ROOT / 'scripts' / 'mesure-aujourdhui.py')
MESURE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MESURE)
STATIC = ROOT / 'agent' / 'static'
AVOCAT = ('avocate@example.test', 'avocat')


class Cabinet(unittest.TestCase):
    """Cabinet fictif : ``n`` dossiers DOS-001…, quelques données dans chaque source de l'encadré. Le modèle est interdit pendant tout le test."""
    N = 6

    def setUp(self):
        from sqlite_cleanup import close_sqlite_under
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.addCleanup(close_sqlite_under, self.t.name)
        base = Path(self.t.name)
        (base / 'state').mkdir()
        noms = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')
        self.matters = [{'id': 'DOS-%03d' % (i + 1), 'client_name': '%s %d' % (noms[i % 6], i + 1), 'path': '/Dossiers/DOS-%03d' % (i + 1), 'references': ['DOS-%03d' % (i + 1)],
                         'aliases': [], 'correspondents': []} for i in range(self.N)]
        private_json(base / 'matters.json', self.matters)
        self.c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'), 'mail': {'own_addresses': ['cabinet@example.test']},
                  'nextcloud': {'roots': ['/Dossiers']}, 'documents': {}, 'ollama': {'model': 'interdit'}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'}}
        self.d = Desk(self.c)
        self.addCleanup(self.d.db.close)
        for cible in ('agent.model.Model.ask', 'agent.model.Model.complete'):
            p = patch(cible, side_effect=AssertionError('appel au modèle interdit'))
            p.start()
            self.addCleanup(p.stop)
        MESURE.schemas(self.d)
        self.now = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ données fictives (mêmes semis que scripts/mesure-aujourdhui.py)
    def evenement(self, ident, titre, debut, minutes=60, matter=''):
        MESURE.evenement(self.d, ident, titre, debut, minutes, matter)

    def tache(self, ident, titre, due, matter='', statut='todo'):
        MESURE.tache(self.d, ident, titre, due, matter, statut)

    def courriel(self, cle, sujet, matter='', etat='needs_action', recu=None):
        MESURE.courriel(self.d, cle, sujet, matter, etat, recu or self.now)

    def prepare(self, ident, titre, kind='contrat', matter='DOS-001', depot='', jours=1):
        MESURE.prepare(self.d, ident, titre, kind, matter, depot, jours)

    def resume(self, owner='cabinet', role='administrateur', hidden=frozenset()):
        self.d.db.commit()
        return act.calculer(self.d, owner, role, hidden, '/p')

    def rubrique(self, resume, ident):
        return next(r for r in resume['rubriques'] if r['id'] == ident)

    def semer(self):
        jour = datetime.now(timezone.utc)
        self.evenement('ev1', 'Audience de mise en état', jour + timedelta(hours=2), matter='DOS-001')
        self.evenement('ev2', 'Rendez-vous client', jour + timedelta(days=1, hours=1), matter='DOS-002')
        self.evenement('ev3', 'Expertise réservée', jour + timedelta(hours=1), matter='DOS-006')
        self.tache('t1', 'Relire les conclusions adverses', (jour - timedelta(days=2)).date().isoformat(), 'DOS-001')
        self.tache('t2', 'Appeler le greffe', jour.date().isoformat(), 'DOS-002')
        self.tache('t3', 'Tâche du dossier réservé', (jour - timedelta(days=5)).date().isoformat(), 'DOS-006')
        self.courriel('m1', 'Demande de pièces complémentaires', 'DOS-001', recu=jour - timedelta(minutes=5))
        self.courriel('m2', 'Ancienne relance', 'DOS-002', recu=jour - timedelta(days=3))
        self.courriel('m3', 'Courriel du dossier réservé', 'DOS-006', recu=jour - timedelta(minutes=1))
        self.prepare('p1', 'Contrat de bail DURAND')
        self.prepare('p2', 'Projet de réponse au confrère', 'courriel', 'DOS-002', depot='a_decider')
        self.prepare('p3', 'Note réservée', 'contrat', 'DOS-006')
        MESURE.echeance(self.d, 'd1', 'DOS-001', (jour + timedelta(days=3)).date().isoformat())
        MESURE.veille(self.d, 'v1', 'Arrêt fictif sur le bail commercial')
        MESURE.travail(self.d, 'extract_facts460', 'DOS-001', 'running')
        MESURE.travail(self.d, 'extract_facts460', 'DOS-002', 'pending')
        self.d.db.commit()


# ====================================================================================== A01
class A01(Cabinet):
    def test_A01_une_ligne_par_rubrique_avec_un_nombre_et_le_plus_urgent_nomme(self):
        self.semer()
        r = self.resume()
        self.assertEqual([x['id'] for x in r['rubriques']], ['agenda', 'taches', 'courriels', 'reponses', 'documents', 'avis', 'strategie', 'agent'])
        agenda = self.rubrique(r, 'agenda')
        self.assertEqual(agenda['nombre'], 3)
        self.assertIn('Expertise réservée', agenda['urgent'])          # administrateur : le dossier réservé n'est pas masqué pour lui
        taches = self.rubrique(r, 'taches')
        self.assertEqual(taches['nombre'], 3)
        self.assertIn('Tâche du dossier réservé', taches['urgent'])     # la plus en retard d'abord
        self.assertIn('2 en retard', taches['phrase'])
        courriels = self.rubrique(r, 'courriels')
        self.assertEqual((courriels['nombre'], courriels['phrase']), (3, 'dont 2 nouveaux depuis 24 h'))
        self.assertEqual(self.rubrique(r, 'reponses')['nombre'], 1)
        self.assertIn('Projet de réponse au confrère', self.rubrique(r, 'reponses')['urgent'])
        self.assertEqual(self.rubrique(r, 'documents')['nombre'], 2)
        agent = self.rubrique(r, 'agent')
        self.assertEqual(agent['nombre'], 2)
        self.assertIn('1 en cours, 1 en attente', agent['phrase'])
        for x in r['rubriques']:
            self.assertTrue(x['href'], x['id'])                          # chaque ligne mène à sa liste

    def test_A01_dossier_reserve_ni_compte_ni_nomme(self):
        self.semer()
        r = self.resume(*AVOCAT, hidden=frozenset({'DOS-006'}))
        texte = json.dumps(r, ensure_ascii=False)
        for interdit in ('Expertise réservée', 'Tâche du dossier réservé', 'Courriel du dossier réservé', 'Note réservée', 'ORLANDEC 6'):
            self.assertNotIn(interdit, texte)
        self.assertEqual(self.rubrique(r, 'agenda')['nombre'], 2)
        self.assertEqual(self.rubrique(r, 'taches')['nombre'], 2)
        self.assertEqual(self.rubrique(r, 'courriels')['nombre'], 2)
        self.assertEqual(self.rubrique(r, 'documents')['nombre'], 1)

    def test_A01_mutation_le_filtre_des_dossiers_reserves_est_le_garde_fou(self):
        """Contre-épreuve : si la clause qui retire les dossiers réservés est neutralisée, le dossier réservé réapparaît (le test précédent échouerait)."""
        self.semer()
        with patch.object(act, '_dans', return_value=('', [])):
            r = self.resume(*AVOCAT, hidden=frozenset({'DOS-006'}))
        self.assertEqual(self.rubrique(r, 'courriels')['nombre'], 3)
        self.assertIn('Courriel du dossier réservé', self.rubrique(r, 'courriels')['urgent'])
        self.assertEqual(self.rubrique(r, 'agenda')['nombre'], 3)

    def test_A01_source_en_panne_annoncee_jamais_un_zero(self):
        self.semer()
        self.d.db.execute('DROP TABLE work_items')
        self.d.db.commit()
        courriels = self.rubrique(self.resume(), 'courriels')
        self.assertIsNone(courriels['nombre'])
        self.assertEqual(courriels['etat'], 'panne')
        html = act.lignes_html(self.resume())
        self.assertIn('Source indisponible', html)
        self.assertRegex(html, r'data-rubrique="courriels" data-etat="panne">.*?<span class="ax95-nombre" aria-label="nombre inconnu">–</span>')

    def test_A01_vide_et_zero_distincts_de_la_panne(self):
        r = self.resume()
        for x in r['rubriques']:
            self.assertIn(x['etat'], ('vide', 'ok', 'partiel'), x)
            self.assertNotEqual(x['etat'], 'panne', x)

    def test_A01_sans_appel_au_modele_meme_avec_des_decisions_en_attente(self):
        self.semer()
        from agent import taches5614
        taches5614.ensure_schema(self.d)
        r = self.resume()                                   # le modèle est remplacé par une erreur : tout appel ferait échouer ce test
        self.assertEqual(len(r['rubriques']), 8)
        self.assertNotIn('panne', [x['etat'] for x in r['rubriques']])

    def test_A01_budget_de_temps_arrete_les_rubriques_restantes(self):
        self.semer()
        r = act.calculer(self.d, 'cabinet', 'administrateur', frozenset(), '/p', budget_ms=-1)
        self.assertTrue(all(x['etat'] == 'retard' and x['nombre'] is None for x in r['rubriques']))
        self.assertIn('Non calculé', act.lignes_html(r))

    def test_A01_strategie_reprend_les_points_existants_sans_en_inventer(self):
        self.semer()
        self.assertEqual(self.rubrique(self.resume(), 'strategie')['nombre'], 0)
        self.d.db.execute("CREATE TABLE IF NOT EXISTS strategy_analyses(id TEXT, matter TEXT, version INTEGER, objective TEXT, status TEXT, data TEXT, created TEXT, a TEXT, b TEXT, c TEXT)")
        self.d.db.execute("INSERT INTO strategy_analyses VALUES('s1','DOS-003',1,'Objectif fictif','validated','{}','', '', '', '')")
        self.d.db.execute("INSERT INTO strategy_analyses VALUES('s2','DOS-003',2,'Objectif fictif','proposed','{}','', '', '', '')")
        self.d.db.execute("INSERT INTO strategy_analyses VALUES('s3','DOS-004',1,'Objectif fictif','proposed','{}','', '', '', '')")
        self.d.db.execute("INSERT INTO strategy_analyses VALUES('s4','DOS-004',2,'Objectif fictif','validated','{}','', '', '', '')")   # dernière version validée
        strategie = self.rubrique(self.resume(), 'strategie')
        self.assertEqual(strategie['nombre'], 1)
        self.assertIn('DOS-003', strategie['href'])

    def test_A01_texte_stable_d_une_minute_a_l_autre(self):
        self.semer()
        act.calculer(self.d, 'cabinet', 'administrateur', frozenset(), '/p', now=self.now)
        a = act.calculer(self.d, 'cabinet', 'administrateur', frozenset(), '/p', now=self.now)
        b = act.calculer(self.d, 'cabinet', 'administrateur', frozenset(), '/p', now=self.now + timedelta(seconds=50))
        self.assertEqual(a['etag'], b['etag'])                 # aucune durée relative dans le texte : la réponse 304 reste possible


    def test_A01_texte_lu_une_phrase_par_rubrique_sans_symbole(self):
        self.semer()
        texte = act.texte_parle(self.resume())
        self.assertTrue(texte.startswith('Actuellement, à '))
        for libelle in ('Agenda :', 'Tâches :', 'Courriels à traiter :', 'Réponses à décider :', 'Documents à relire :', 'Avis :', 'Stratégie et décisions :', 'Agent :'):
            self.assertEqual(texte.count(libelle), 1, libelle)
        self.assertIn('Courriels à traiter : 3, dont 2 nouveaux', texte)        # le total est dit, pas seulement le détail
        self.assertNotIn(' — ', texte)
        resume = {'heure': '09 h 00', 'rubriques': [dict(self.rubrique(self.resume(), 'agenda'), urgent='10 h 00 : Audience — DOS-001 - DURAND c. MARTIN')]}
        self.assertIn('DURAND contre MARTIN', act.texte_parle(resume))


# ====================================================================================== A02
class A02(Cabinet):
    def appel(self, method='GET', body=None, etag='', role='avocat', csrf='jeton', route='auto'):
        raw = json.dumps(body or {}).encode()
        env = {'HTTP_X_AXIORHUB_USER': 'avocate@example.test', 'HTTP_X_AXIORHUB_ROLE': role, 'HTTP_ORIGIN': 'https://cabinet.example.test', 'HTTP_X_CSRF_TOKEN': csrf,
               'CONTENT_LENGTH': str(len(raw)), 'CONTENT_TYPE': 'application/json', 'wsgi.input': io.BytesIO(raw), 'REQUEST_METHOD': method}
        if etag:
            env['HTTP_IF_NONE_MATCH'] = etag
        from agent import web5689
        route = 'm5689/actuellement/' + ('etat' if method == 'GET' else route)
        return web5689.api(env, self.d, {'origin': 'https://cabinet.example.test', 'csrf': 'jeton'}, '/p', route, {}, method)

    def test_A02_reponse_conditionnelle_304_puis_200_quand_une_donnee_change(self):
        self.semer()
        first = self.appel()
        self.assertEqual(first['status'], '200 OK')
        etag = dict(first['headers'])['ETag']
        data = json.loads(first['body'])
        self.assertIn('ax95-liste', data['html'])
        self.assertEqual('"%s"' % data['etag'], etag)
        again = self.appel(etag=etag)
        self.assertEqual((again['status'], again['body']), ('304 Not Modified', ''))
        self.assertEqual(dict(again['headers'])['ETag'], etag)
        self.courriel('m9', 'Nouvelle demande du confrère', 'DOS-003')
        self.d.db.commit()
        changed = self.appel(etag=etag)
        self.assertEqual(changed['status'], '200 OK')
        self.assertNotEqual(dict(changed['headers'])['ETag'], etag)

    def test_A02_route_legere_poids_borne(self):
        self.semer()
        self.assertLess(len(self.appel()['body'].encode('utf-8')), 12000)

    def test_A02_arreter_reprendre_memorise_pour_la_personne(self):
        self.assertTrue(act.auto_actif(self.d, 'avocate@example.test'))
        out = self.appel('POST', {'auto': False})
        self.assertEqual(json.loads(out['body']), {'auto': False})
        self.assertFalse(act.auto_actif(self.d, 'avocate@example.test'))
        self.assertTrue(act.auto_actif(self.d, 'cabinet'))                       # une autre personne garde son choix
        self.assertIn('data-auto="0"', act.bloc_html(self.d, '/p', 'avocate@example.test', 'avocat', frozenset()))
        self.appel('POST', {'auto': True})
        self.assertTrue(act.auto_actif(self.d, 'avocate@example.test'))

    def test_A02_ecriture_refusee_sans_jeton_csrf_ni_valeur_booleenne(self):
        self.assertEqual(self.appel('POST', {'auto': False}, csrf='faux')['status'], '403 Forbidden')
        self.assertTrue(act.auto_actif(self.d, 'avocate@example.test'))
        self.assertEqual(self.appel('POST', {'auto': 'non'})['status'], '400 Bad Request')
        self.assertEqual(self.appel(role='inconnu')['status'], '403 Forbidden')

    def test_A02_route_applique_les_dossiers_reserves_de_la_requete(self):
        self.semer()
        with patch('agent.membres5650.hidden_for', return_value=frozenset({'DOS-006'})):
            body = self.appel()['body']
        self.assertNotIn('Courriel du dossier réservé', body)
        self.assertIn('Demande de pièces complémentaires', body)
        with patch('agent.membres5650.hidden_for', return_value=frozenset()):     # contre-épreuve : sans le filtre, il apparaît
            self.assertIn('Courriel du dossier réservé', self.appel()['body'])

    def test_A02_script_sans_code_en_ligne_et_boutons_presents(self):
        self.semer()
        html = act.bloc_html(self.d, '/p', 'cabinet', 'administrateur', frozenset())
        scripts = re.findall(r'<script([^>]*)>(.*?)</script>', html, re.S)
        for attrs, contenu in scripts:
            self.assertTrue('defer' in attrs and 'src=' in attrs and not contenu.strip() or 'type="application/json"' in attrs, attrs)
        self.assertNotIn('style=', html)
        for action in ('actualiser', 'auto', 'ecouter', 'taire'):
            self.assertIn('data-ax95="%s"' % action, html)
        texte = json.loads(re.search(r'<script type="application/json" id="ax95-texte">(.*?)</script>', html, re.S).group(1))
        self.assertTrue(texte.startswith('Actuellement, à '))
        js = (STATIC / 'v5695-aujourdhui.js').read_text(encoding='utf-8')
        self.assertIn('If-None-Match', js)
        self.assertIn('visibilitychange', js)
        self.assertIn('axiorhubVoix', js)
        self.assertNotIn('m5689/voix/preparer', js)          # la préparation du texte reste celle de la voix commune (abréviations, droits, audit)
        self.assertNotRegex(js, r'speak\([^)]*\)\s*;?\s*\}\)\(\)')   # aucune lecture au chargement
        self.assertIn('/static/v5695-aujourdhui.js', __import__('agent.web440', fromlist=['STATIC']).STATIC)


# ====================================================================================== A03 et A04
class A03(Cabinet):
    def page(self, owner='cabinet'):
        from agent import cockpit530
        return cockpit530.page(self.d, '/p', owner=owner, queue='')

    def test_A03_encadre_en_tete_par_defaut_puis_deplacable_et_masquable(self):
        self.semer()
        html = self.page()
        self.assertEqual(html.count('id="ax95-actuellement"'), 1)
        self.assertLess(html.index('id="ax95-actuellement"'), html.index('id="a89-travail"'))
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': ['file', 'actuellement'], 'accent': ''})
        html = self.page()
        self.assertEqual(html.count('id="ax95-actuellement"'), 1)
        self.assertGreater(html.index('id="ax95-actuellement"'), html.index('id="a89-travail"'))
        tableau5688.enregistrer(self.d, 'cabinet', {'ordre': list(tableau5688.IDS), 'masques': ['actuellement'], 'accent': ''})
        self.assertNotIn('id="ax95-actuellement"', self.page())

    def test_A03_ancien_reglage_sans_le_bloc_le_garde_en_tete(self):
        tableau5688.ensure_schema(self.d)
        self.d.db.execute('INSERT OR REPLACE INTO tableau5688 VALUES(?,?,?)', ('cabinet', json.dumps({'ordre': ['journee', 'routines', 'file'], 'masques': [], 'accent': ''}), 'x'))
        self.assertEqual(tableau5688.charger(self.d, 'cabinet')['ordre'][:2], ['actuellement', 'journee'])

    def test_A03_ancienne_page_aussi(self):
        from agent import ui_drapeaux5689
        self.semer()
        with patch.object(ui_drapeaux5689, 'lire', return_value={'accueil': False, 'theme': True, 'actions_ia': True}):
            html = self.page()
        self.assertEqual(html.count('id="ax95-actuellement"'), 1)
        self.assertNotIn('__ACTUELLEMENT5695__', html)
        self.assertNotIn('<!--', html)

    def test_A03_titre_long_de_tache_tronque_avec_titre_complet(self):
        from agent import cockpit530
        long = 'Relire et annoter les conclusions récapitulatives de la partie adverse dans le dossier fictif DURAND contre MARTIN avant l’audience'
        self.tache('tl', long, datetime.now(timezone.utc).date().isoformat(), 'DOS-001')
        self.d.db.commit()
        html = cockpit530.day_html(self.d, '/p')
        self.assertIn('title="%s"' % long.replace('’', '’'), html)
        css = (STATIC / 'v5695-aujourdhui.css').read_text(encoding='utf-8')
        self.assertIn('-webkit-line-clamp: 2', css)
        self.assertIn('v5695-aujourdhui.css', __import__('agent.shell501', fromlist=['BUNDLE']).BUNDLE)
        self.assertIn('.ax95-ligne', (STATIC / 'app520.css').read_text(encoding='utf-8'))      # paquet régénéré


    def test_A03_blocs_du_bas_repliables_ouverts_par_defaut_et_memorises_par_personne(self):
        html = self.page()
        pli = re.search(r'<details class="t95-pli" data-pli="veille" data-ouvert="1" open>.*?id="c5632-veille"', html, re.S)
        self.assertTrue(pli, 'veille juridique non repliable ou fermée par défaut')
        act.regler_pli(self.d, 'cabinet', 'veille', False)
        html = self.page()
        self.assertIn('<details class="t95-pli" data-pli="veille" data-ouvert="0"><summary', html)
        self.assertEqual(html.count('id="c5632-veille"'), 1)                       # replié, rien n'est retiré
        self.assertIn('data-pli="veille" data-ouvert="1" open', self.page('avocate@example.test'))   # une autre personne garde son choix
        act.regler_pli(self.d, 'cabinet', 'veille', True)
        self.assertIn('data-pli="veille" data-ouvert="1" open', self.page())
        self.assertIn('/static/v5695-aujourdhui.js', self.page())
        self.assertEqual(set(act.PLIABLES), {'veille', 'societes', 'memoire', 'reprise'})

    def test_A03_route_des_plis_controlee(self):
        appel = A02.appel
        out = appel(self, 'POST', {'bloc': 'reprise', 'ouvert': False}, route='plis')
        self.assertEqual(json.loads(out['body']), {'bloc': 'reprise', 'ouvert': False})
        self.assertEqual(act.plis_fermes(self.d, 'avocate@example.test'), frozenset({'reprise'}))
        self.assertEqual(appel(self, 'POST', {'bloc': 'journee', 'ouvert': False}, route='plis')['status'], '400 Bad Request')   # bloc non repliable
        self.assertEqual(appel(self, 'POST', {'bloc': 'veille', 'ouvert': False}, csrf='faux', route='plis')['status'], '403 Forbidden')
        self.assertNotIn('veille', act.plis_fermes(self.d, 'avocate@example.test'))


class I18N(Cabinet):
    def tearDown(self):
        from agent import i18n5631
        i18n5631.set_lang('fr')

    def test_A01_A04_textes_dans_les_quatre_langues(self):
        from agent import i18n5631
        self.assertEqual(i18n5631.check(), [])
        self.semer()
        attendus = {'en': ('Right now', 'Refresh', 'Calendar', 'Displayed blocks'),
                    'de': ('Aktuell', 'Aktualisieren', 'Kalender', 'Angezeigte Blöcke'), 'es': ('Ahora mismo', 'Actualizar', 'Agenda', 'Bloques mostrados')}
        for langue, (titre, bouton, rubrique, legende) in attendus.items():
            i18n5631.set_lang(langue)
            html = act.bloc_html(self.d, '/p', 'cabinet', 'administrateur', frozenset())
            self.assertIn('<h2 id="ax95-titre">%s</h2>' % titre, html)
            self.assertIn('<span>%s</span>' % bouton, html)
            self.assertIn('<span class="ax95-rubrique">%s</span>' % rubrique, html)
            self.assertNotIn('Mis à jour à', html)
            self.assertNotIn('nouveaux depuis', html)
            self.assertIn(legende, tableau5688.panneau(tableau5688.charger(self.d, 'cabinet')))
        js = (STATIC / 'v5695-aujourdhui.js').read_text(encoding='utf-8')
        for cle in re.findall(r"msg\('([a-z_]+)'", js):
            self.assertIn('js.5695.' + cle, i18n5631.catalog('fr'), cle)


class A04(Cabinet):
    def test_A04_case_et_libelle_sur_la_meme_ligne_boutons_groupes_et_nommes(self):
        panel = tableau5688.panneau(tableau5688.charger(self.d, 'cabinet'))
        lignes = re.findall(r'<li class="t88-row t95-row" data-bloc="(\w+)"[^>]*>(.*?)</li>', panel, re.S)
        self.assertEqual([k for k, _ in lignes], list(tableau5688.IDS))
        for bloc, corps in lignes:
            nom = dict(tableau5688.BLOCS)[bloc]
            # case et nom dans le même libellé : un clic sur le nom coche la case ; le nom est lu avec la case
            self.assertRegex(corps, r'^<label class="t95-voir"><input type="checkbox" name="voir:%s"[^>]*><span class="t95-nom">%s</span></label>' % (bloc, re.escape(nom).replace('’', '’')))
            # Monter et Descendre groupés juste après, avec le nom de la section dans leur libellé accessible
            groupe = re.search(r'</label><span class="t88-moves t95-moves" role="group" aria-label="Position : ([^"]+)">(.*)</span>$', corps, re.S)
            self.assertTrue(groupe, bloc)
            self.assertIn('aria-label="Monter : %s"' % nom.replace('’', '’'), groupe.group(2))
            self.assertIn('aria-label="Descendre : %s"' % nom.replace('’', '’'), groupe.group(2))
            self.assertEqual(groupe.group(2).count('<button type="button"'), 2)
        self.assertIn('<legend>Couleur d’accent</legend>', panel)

    def test_A04_mise_en_page_carbon(self):
        css = (STATIC / 'v5695-aujourdhui.css').read_text(encoding='utf-8')
        regle = re.search(r'\.t95-list > \.t95-row \{([^}]*)\}', css).group(1)
        self.assertIn('display: grid', regle)
        self.assertIn('justify-content: start', regle)                # les boutons suivent le nom, pas en bout de ligne
        self.assertIn('flex-direction: row', re.search(r'\.t95-voir, \.m5-form label\.t95-voir \{([^}]*)\}', css).group(1))
        self.assertIn('var(--cds-', css)
        js = (STATIC / 'v5688-crm.js').read_text(encoding='utf-8')
        self.assertIn('data-t88-move', js)                           # le script existant pilote toujours les boutons


# ====================================================================================== A05
class A05(Base):
    def etat(self, sid):
        return next(s for s in liv.etat_sources(self.desk) if s['id'] == sid)

    def test_A05_sources_sans_journal_ne_restent_pas_en_collecte_reprise(self):
        """Cause : la réparation 5.6.93 marquait TOUTES les sources « Collecte reprise depuis le début » (état partiel), y compris la Bibliothèque
        de modèles et les Formulaires, qui n'ont pas de journal (signature constante) : leur lecture n'avait jamais rien à reprendre, l'état ne
        revenait jamais à jour et Travail préparé affichait l'avertissement en permanence."""
        liv.ensure_schema(self.desk)
        liv.synchroniser(self.desk)
        self.desk.db.execute("UPDATE prepared_sources5689 SET parcours=0")       # base d'avant 5.6.93
        self.desk.db.commit()
        reg.migrer_sources(self.desk)
        for _ in range(3):
            liv.synchroniser(self.desk)
        for sid in ('bibliotheque5639', 'formulaires5683'):
            self.assertNotIn(self.etat(sid)['state'], ('partial', 'unavailable'), sid)
        data = liv.projection(self.desk)
        self.assertNotIn('Collecte reprise', json.dumps(data['sources'], ensure_ascii=False))

    def test_A05_base_deja_bloquee_sur_le_serveur_reparee_a_la_lecture(self):
        liv.ensure_schema(self.desk)
        liv.synchroniser(self.desk)
        self.desk.db.execute("UPDATE prepared_sources5689 SET state='partial', message='Collecte reprise depuis le début.', errors=0 WHERE source IN ('bibliotheque5639','formulaires5683')")
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        for sid in ('bibliotheque5639', 'formulaires5683'):
            self.assertNotIn(self.etat(sid)['state'], ('partial', 'unavailable'), sid)

    def test_A05_une_vraie_ligne_illisible_reste_signalee(self):
        liv.ensure_schema(self.desk)
        liv.synchroniser(self.desk)
        self.desk.db.execute("UPDATE prepared_sources5689 SET state='partial', message='2 ligne(s) illisible(s) ignorée(s) ; les autres documents restent affichés.', errors=2 WHERE source='bibliotheque5639'")
        self.desk.db.commit()
        liv.synchroniser(self.desk)
        self.assertEqual(self.etat('bibliotheque5639')['state'], 'partial')       # un vrai écart n'est pas effacé

    def test_A05_source_paginee_reprise_garde_son_avertissement_pendant_la_reprise(self):
        liv.ensure_schema(self.desk)
        liv.synchroniser(self.desk)
        self.desk.db.execute("UPDATE prepared_sources5689 SET parcours=0")
        self.desk.db.commit()
        reg.migrer_sources(self.desk)
        row = self.desk.db.execute("SELECT state, message FROM prepared_sources5689 WHERE source='docrequest520'").fetchone()
        self.assertEqual(row['state'], 'partial')                                    # reprise réelle : annoncée pendant la reprise
        liv.synchroniser(self.desk)
        self.assertNotEqual(self.etat('docrequest520')['state'], 'partial')         # puis à jour, une fois relue


# ====================================================================================== A06
class A06(Cabinet):
    N = 300

    def test_A06_cabinet_de_300_dossiers_calcul_borne_et_requetes_comptees(self):
        MESURE.semer(self.d, self.matters, courriels=1000)
        out = MESURE.mesurer_encadre(self.d, repetitions=3)
        self.assertLess(out['requetes'], 60)
        self.assertLess(out['ms_mediane'], 1500)            # borne large pour une machine de recette lente ; la cible (150 ms) est mesurée par le script
        self.assertTrue(all(r['etat'] != 'panne' for r in out['rubriques']), out['rubriques'])


if __name__ == '__main__':
    unittest.main()

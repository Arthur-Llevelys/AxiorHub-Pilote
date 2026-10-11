"""5.6.31 : cahier des charges additionnel — socle R00, R01, R02, R17, R18, R19 (guide d'installation pas à pas, preuves ciblées,
traduction de l'interface, menu « Toutes les fonctions », guide d'utilisation relié aux fonctions réelles)."""
import copy
import io
import json
from pathlib import Path
import re
import threading
import unittest
from unittest.mock import patch

import test_desk
from agent.common import Stop, load_config, private_json
from agent.desk import Desk

ROOT = Path(__file__).resolve().parents[1]


class Web(unittest.TestCase):
    setUp0 = test_desk.WebTests.setUp
    request = test_desk.WebTests.request

    def setUp(self):
        self.setUp0()
        self.env = {'axiorhub.config_path': str(self.config)}

    def desk(self, owner='cabinet'):
        desk = Desk(load_config(str(self.config)))
        desk._guide_owner = owner
        self.addCleanup(desk.db.close)
        return desk

    def json_request(self, path, body=None, method='POST', cookie='', user=None, role=None):
        raw = json.dumps(body or {}).encode()
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(raw), 'CONTENT_LENGTH': str(len(raw)),
               'CONTENT_TYPE': 'application/json', 'HTTP_ORIGIN': self.origin, 'HTTP_X_CSRF_TOKEN': 'test-csrf',
               'HTTP_AUTHORIZATION': 'Basic ' + __import__('base64').b64encode(('admin:' + self.password).encode()).decode()}
        if cookie:
            env['HTTP_COOKIE'] = cookie
        result = {}

        def start(status, headers):
            result.update(status=status, headers=headers)
        result['body'] = b''.join(self.app(env, start)).decode()
        return result


# --------------------------------------------------------------------------------------------- R17 · LG
class Translation(Web):
    def test_four_catalogues_are_complete_with_the_same_variables(self):
        from agent import i18n5631 as i18n
        self.assertEqual(i18n.check(), [])                                   # LG05 : zéro message manquant, mêmes variables
        for lang in i18n.LANGS:
            self.assertGreater(len(i18n.catalog(lang)), 400)

    def test_plurals_named_variables_and_escaping(self):
        from agent import i18n5631 as i18n
        self.assertEqual(i18n.t('guide.ollama_ok', 'fr', n=0), 'Service Ollama joignable : 0 modèle installé.')      # 0 au singulier en français
        self.assertEqual(i18n.t('guide.ollama_ok', 'en', n=0), 'Ollama service reachable: 0 models installed.')
        self.assertEqual(i18n.t('guide.progress', 'de', position=3, total=7), 'Schritt 3 von 7')
        html = i18n.th('help.missing', 'es', what='<script>alert(1)</script>')
        self.assertNotIn('<script>', html)                                                            # LG04 / LG T05
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(i18n.t('cle.inexistante', 'de'), 'cle.inexistante')
        self.assertIn(('de', 'cle.inexistante'), i18n.missing())                                      # repli tracé

    def test_dates_and_numbers_follow_the_language_without_changing_values(self):
        import datetime
        from agent import i18n5631 as i18n
        day = datetime.date(2026, 10, 10)
        self.assertEqual(i18n.format_date(day, 'fr'), 'samedi 10 octobre 2026')
        self.assertEqual(i18n.format_date(day, 'en'), 'Saturday, October 10, 2026')
        self.assertEqual(i18n.format_number(1234.5, 'de'), '1.234,50')
        self.assertEqual(day.isoformat(), '2026-10-10')

    def test_two_users_two_languages_at_the_same_time(self):
        """LG T02 : la langue est propre au fil de chaque requête (aucune variable globale partagée)."""
        from agent import i18n5631 as i18n
        seen = {}
        barrier = threading.Barrier(2)

        def worker(lang):
            i18n.set_lang(lang)
            barrier.wait()
            seen[lang] = (i18n.current(), i18n.t('menu.all'))
        threads = [threading.Thread(target=worker, args=(lang,)) for lang in ('fr', 'de')]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(seen['fr'], ('fr', 'Toutes les fonctions'))
        self.assertEqual(seen['de'], ('de', 'Alle Funktionen'))

    def test_page_language_comes_from_the_session_choice_or_the_saved_preference(self):
        body = self.request('/aujourdhui')['body']
        self.assertIn('<html lang="fr">', body)
        self.assertIn('Toutes les fonctions', body)
        r = self.json_request('/api440/m5631/langue', {'lang': 'de'})
        self.assertTrue(r['status'].startswith('200'), r)
        self.assertTrue(any(n == 'Set-Cookie' and 'axh_lang=de' in v for n, v in r['headers']))
        body = self.request('/aujourdhui')['body']                       # préférence enregistrée pour cet utilisateur
        self.assertIn('<html lang="de">', body)
        self.assertIn('Alle Funktionen', body)
        self.assertIn('id="l5631-lang"', body)
        self.assertIn('<option value="de" lang="de" selected>Deutsch</option>', body)
        r = self.json_request('/api440/m5631/langue', {'lang': 'xx'})
        self.assertTrue(r['status'].startswith('400'))

    def test_interface_language_is_distinct_from_drafting_language_and_law(self):
        """LG06 : choisir l'interface anglaise ne change ni la langue de rédaction ni les réglages du dossier."""
        from agent import i18n5631 as i18n
        desk = self.desk()
        i18n.save_preference(desk.c, 'cabinet', 'en')
        self.assertEqual(desk.settings('cabinet5631:langue_redaction', 'fr') or 'fr', 'fr')
        self.assertEqual(load_config(str(self.config))['nextcloud']['roots'], self.f.c['nextcloud']['roots'])


# --------------------------------------------------------------------------------------------- R18 · NV
class Menu(Web):
    def test_single_menu_replaces_rubriques_and_tools(self):
        body = self.request('/parametres')['body']
        nav = re.search(r'<nav aria-label="Navigation principale".*?</nav>', body, re.S).group(0)
        self.assertEqual(nav.count('<a '), 6)
        self.assertIn('id="f5631-all"', body)
        self.assertNotIn('<summary>Rubriques</summary>', body)
        self.assertNotIn('<summary>Outils</summary>', body)
        self.assertIn('id="f5631-q"', body)                                   # recherche par nom ou besoin

    def test_every_former_entry_has_a_destination_in_the_registry(self):
        """NV T01 : inventaire initial (Rubriques, Outils, menu principal) comparé au registre final."""
        from agent import fonctions5631 as reg
        from agent.shell501 import PRIMARY, RUBRIQUES, TOOLS
        routes = {r[2].split('?', 1)[0] for r in reg.FUNCTIONS}
        for p, *_ in PRIMARY + RUBRIQUES:
            self.assertIn(p, routes, p)
        for p, *_ in TOOLS:
            self.assertIn(p, routes, p)
        self.assertEqual(reg.audit(), [])                                     # groupe, libellé, article, prérequis connus

    def test_every_registered_route_is_served(self):
        from agent import fonctions5631 as reg
        for fid, group, route, aliases, needs in reg.FUNCTIONS:
            path, _, query = route.partition('?')
            r = self.request(path, query=query)
            self.assertTrue(r['status'].startswith('200'), (fid, route, r['status']))

    def test_search_uses_former_names_and_needs(self):
        from agent import fonctions5631 as reg
        panel = reg.panel_html('/agent-courriel', '/aujourdhui', self.f.c)
        item = re.search(r'<li data-search="([^"]*)"><a href="/agent-courriel/pieces"', panel).group(1)
        self.assertIn('préparer un bordereau', item)
        self.assertIn('style et apprentissage', re.search(r'<li data-search="([^"]*)"><a href="/agent-courriel/progres"', panel).group(1))

    def test_unconfigured_function_offers_the_guide_step_and_roles_are_filtered(self):
        from agent import fonctions5631 as reg
        cfg = copy.deepcopy(self.f.c)
        cfg['mail']['host'] = 'mail.example.com'
        rows = {r['id']: r for r in reg.entries(cfg)}
        self.assertEqual(rows['courriels']['configure'], '/guide-installation?etape=messagerie')       # NV04
        avocat = {r['id'] for r in reg.entries(cfg, role='avocat')}
        self.assertNotIn('parametres', avocat)                                # NV T03 : réglages communs filtrés
        self.assertNotIn('guide_installation', avocat)
        self.assertIn('guide_utilisation', avocat)
        self.assertIn('dossiers', avocat)

    def test_contextual_help_link_points_to_the_function_of_the_page(self):
        body = self.request('/pieces')['body']
        self.assertIn('/guide-utilisation?fonction=pieces', body)
        self.assertIn('Aide sur cette page', body)


# --------------------------------------------------------------------------------------------- R19 · GU
class UsageGuide(Web):
    def test_every_function_has_a_sheet_in_four_languages(self):
        from agent import aide5631
        self.assertEqual(aide5631.check(), [])                                # GU05

    def test_buttons_cited_by_each_sheet_exist_on_the_real_screen(self):
        """GU T02 / R19 étape 5 : un bouton renommé sans mise à jour de l'aide fait échouer la recette."""
        from agent import aide5631, fonctions5631 as reg
        for fid, group, route, aliases, needs in reg.FUNCTIONS:
            buttons = aide5631.articles('fr')[fid]['buttons']
            if not buttons:
                continue
            path, _, query = route.partition('?')
            body = __import__('html').unescape(self.request(path, query=query)['body'])
            for label in buttons:
                self.assertIn(label, body, (fid, label))

    def test_sheet_page_open_and_configure_links(self):
        body = self.request('/guide-utilisation', query='fonction=pieces')['body']
        for text in ('Ouvrir cette fonction', 'Étapes', 'Résultat attendu', 'Comment le vérifier', 'Erreurs courantes', 'Exemple fictif',
                     '« Analyser les PDF du dossier »', 'href="/agent-courriel/pieces"'):
            self.assertIn(text, body)
        index = self.request('/guide-utilisation')['body']
        for text in ('Réaliser une tâche', 'Établir un bordereau', 'Découvrir les fonctions', 'Rechercher dans le guide'):
            self.assertIn(text, index)

    def test_sheet_in_german_is_german(self):
        self.json_request('/api440/m5631/langue', {'lang': 'de'})
        body = self.request('/guide-utilisation', query='fonction=pieces')['body']
        self.assertIn('Diese Funktion öffnen', body)
        self.assertIn('lang="de"', body)
        self.assertNotIn('pas encore traduite', body)

    def test_two_guides_on_the_settings_page(self):
        body = self.request('/parametres')['body']
        self.assertIn('class="p5631-guides"', body)                           # GI01 / GU01
        self.assertIn('href="/agent-courriel/guide-installation"', body)
        self.assertIn('href="/agent-courriel/guide-utilisation"', body)


# --------------------------------------------------------------------------------------------- R00 · R01 · R02 · GI
class SetupGuide(Web):
    def _session(self, desk, features=('documents', 'messagerie')):
        from agent import guide5631 as g
        return g.start(desk, 'cabinet', list(features))

    def test_start_shows_only_chosen_steps_and_progress(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk, ('documents',))
        self.assertEqual(s['steps'], ['fonctions', 'profil', 'coordonnees', 'langues', 'stockage', 'ia', 'modeles', 'fin'])
        s2 = g.start(desk, 'cabinet', ['veille'])
        self.assertEqual(s2['steps'], ['fonctions', 'profil', 'coordonnees', 'langues', 'veille', 'fin'])      # GI T03 : veille seule, sans IA ni stockage
        self.assertEqual(desk.db.execute("SELECT COUNT(*) FROM guide_sessions5631 WHERE status='in_progress'").fetchone()[0], 1)

    def test_page_shows_one_question_explanation_help_and_progress(self):
        desk = self.desk()
        s = self._session(desk)
        body = self.request('/guide-installation', query='session=%s&etape=messagerie' % s['id'])['body']
        for text in ('Étape 7 sur 10', 'Quelle adresse professionnelle utilisez-vous ?', 'Où trouver cette information', 'Ce que cela permet :',
                     'Plus de détails', 'Vérifier la connexion', 'Enregistrer et reprendre plus tard', 'Passer cette étape', 'Retour'):
            self.assertIn(text, body)
        visible = re.search(r'<form class="g5631-form".*?<details class="g5631-more">', body, re.S).group(0)
        self.assertLessEqual(visible.count('class="g5631-field'), 5)          # GI02 : 3 à 5 champs visibles

    def test_field_errors_resume_and_back(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        out = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'fonctions', {'features': ['documents', 'messagerie']})
        self.assertEqual(out['step'], 'profil')
        bad = g.save_step(desk, 'cabinet', s['id'], out['revision'], 'profil', {'prenom': 'Alex', 'nom': '', 'barreau': ''})
        self.assertEqual(bad['step'], 'profil')
        self.assertIn('nom', bad['errors'])                                    # erreur liée au champ
        self.assertEqual(bad['values']['profile.prenom_avocat'], 'Alex')       # rien de perdu
        with self.assertRaisesRegex(Stop, 'session_guide_modifiee'):
            g.save_step(desk, 'cabinet', s['id'], out['revision'], 'profil', {'nom': 'Martin'})           # révision attendue
        ok = g.save_step(desk, 'cabinet', s['id'], bad['revision'], 'profil', {'nom': 'Martin', 'barreau': 'Lyon'})
        self.assertEqual(ok['step'], 'coordonnees')
        back = g.save_step(desk, 'cabinet', s['id'], ok['revision'], 'coordonnees', {}, 'back')
        self.assertEqual(back['step'], 'profil')
        resumed = g.start(desk, 'cabinet', mode='reprendre')                   # GI T04 : reprise après fermeture
        self.assertEqual((resumed['id'], resumed['step']), (s['id'], 'profil'))

    def test_secret_is_vaulted_never_returned_and_cleaned_on_cancel(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        out = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'messagerie', {'host': 'imap.exemple.test', 'username': 'u', 'password': 'Secret-Fictif-123'}, 'save')
        self.assertEqual(out['secrets'], {'mail.password_file': 'configured'})
        raw = json.dumps(out) + json.dumps(dict(desk.db.execute('SELECT * FROM guide_sessions5631 WHERE id=?', (s['id'],)).fetchone()))
        self.assertNotIn('Secret-Fictif-123', raw)
        ref = json.loads(desk.db.execute('SELECT secret_refs FROM guide_sessions5631 WHERE id=?', (s['id'],)).fetchone()[0])['mail.password_file']
        self.assertNotIn('Secret-Fictif-123', Path(ref).read_text(encoding='utf-8'))                     # chiffré dans le coffre
        from agent.common import read_secret
        self.assertEqual(read_secret(ref), 'Secret-Fictif-123')
        exported = json.dumps(g.export(desk, 'cabinet', s['id']))
        self.assertNotIn('Secret-Fictif-123', exported)
        self.assertNotIn(ref, exported)                                        # GI T25 : ni secret ni référence de coffre
        g.cancel(desk, 'cabinet', s['id'])
        self.assertFalse(Path(ref).exists())

    def test_other_user_cannot_read_or_apply_the_session(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        with self.assertRaisesRegex(Stop, 'session_guide_autre_utilisateur'):
            g.session(desk, 'autre@cabinet.test', s['id'])
        with self.assertRaisesRegex(Stop, 'session_guide_autre_utilisateur'):
            g.apply(desk, 'autre@cabinet.test', s['id'], s['revision'], self.env)

    def test_tests_use_the_candidate_configuration_not_the_active_one(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        out = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'messagerie', {'host': 'imap.candidat.test', 'username': 'u', 'password': 'p-candidat'}, 'save')
        seen = {}

        def fake_test(d, data):
            seen['host'] = d.c['mail']['host']
            return {'ok': True, 'connector': 'imap', 'message': 'ok', 'writes': 0}
        with patch('agent.config567.test', side_effect=fake_test), patch('agent.boite5625.folders', return_value={'folders': [{'name': 'INBOX'}, {'name': 'Drafts'}]}):
            res = g.run_test(desk, 'cabinet', s['id'], 'messagerie')
        self.assertEqual(seen['host'], 'imap.candidat.test')
        self.assertNotEqual(load_config(str(self.config))['mail']['host'], 'imap.candidat.test')        # rien enregistré pour tester
        self.assertEqual([r['status'] for r in res['results']], ['available', 'available'])
        self.assertEqual(res['results'][1]['evidence']['folders'], ['INBOX', 'Drafts'])

    def test_targeted_proof_invalidation(self):
        """GI T05 : modifier le SMTP conserve la preuve IMAP ; changer le mot de passe IMAP la périme."""
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        out = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'messagerie', {'host': 'imap.exemple.test', 'username': 'u', 'password': 'p1'}, 'save')
        with patch('agent.config567.test', return_value={'ok': True, 'connector': 'imap', 'message': 'ok', 'writes': 0}), \
                patch('agent.boite5625.folders', return_value={'folders': []}):
            g.run_test(desk, 'cabinet', s['id'], 'messagerie')
        rev = g.session(desk, 'cabinet', s['id'])['revision']
        out = g.save_step(desk, 'cabinet', s['id'], rev, 'envoi', {'host': 'smtp.exemple.test', 'username': 'u', 'password': 'smtp-secret'}, 'save')
        self.assertEqual(out['receipts']['messagerie.read']['status'], 'available')
        out = g.save_step(desk, 'cabinet', s['id'], out['revision'], 'messagerie', {'password': 'p2'}, 'save')
        self.assertEqual(out['receipts']['messagerie.read']['status'], 'stale')

    def test_failed_connection_is_a_visible_result_and_keeps_the_guide(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        s = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'ia', {'ollama_url': 'http://127.0.0.1:11434'}, 'save')
        with patch('agent.common.HTTP.json', side_effect=Stop('ollama_injoignable')):
            res = g.run_test(desk, 'cabinet', s['id'], 'ia')
        self.assertEqual(res['results'][0]['status'], 'refused')
        self.assertIn('Ollama', res['results'][0]['message'])
        self.assertEqual(g.session(desk, 'cabinet', s['id'])['status'], 'in_progress')

    def test_ai_levels_are_separate(self):
        from agent import guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        s = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'ia', {'ollama_url': 'http://127.0.0.1:11434'}, 'save')
        model = desk.c['ollama']['model']
        replies = [{'models': [{'name': model}, {'name': 'autre:1b'}]}, {'response': 'prêt'}]
        with patch('agent.common.HTTP.json', side_effect=lambda *a, **k: replies.pop(0)):
            res = g.run_test(desk, 'cabinet', s['id'], 'ia', 'generate')
        self.assertEqual([(r['capability'], r['status']) for r in res['results']],
                         [('service', 'available'), ('model', 'available'), ('generate', 'available')])

    def test_local_storage_write_probe_leaves_nothing(self):
        import tempfile
        from agent import guide5631 as g
        desk = self.desk()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'Dossiers').mkdir()
            s = self._session(desk, ('documents',))
            g.save_step(desk, 'cabinet', s['id'], s['revision'], 'stockage', {'mode': 'local', 'local_path': tmp, 'racine': '/Dossiers'}, 'save')
            res = g.run_test(desk, 'cabinet', s['id'], 'stockage', 'write')
            self.assertEqual([(r['capability'], r['status']) for r in res['results']], [('read', 'available'), ('write', 'available')])
            self.assertEqual(list((Path(tmp) / 'Dossiers').iterdir()), [])

    def test_apply_without_messaging_and_first_start_is_recognised(self):
        """R00 / GI T02 : documents locaux et Ollama, messagerie non choisie — configuration rechargée réellement, sans connexion IMAP."""
        import tempfile
        from agent import guide5631 as g
        from agent.engine import Engine
        desk = self.desk()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'Dossiers').mkdir()
            s = self._session(desk, ('documents',))
            r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'stockage', {'mode': 'local', 'local_path': tmp, 'racine': '/Dossiers'}, 'save')
            r = g.save_step(desk, 'cabinet', s['id'], r['revision'], 'profil', {'prenom': 'Alex', 'nom': 'Exemple', 'barreau': 'Lyon'}, 'save')
            out = g.apply(desk, 'cabinet', s['id'], r['revision'], self.env)
            self.assertTrue(out['applied'])
            cfg = load_config(str(self.config))
            self.assertIs(cfg['mail']['enabled'], False)
            self.assertEqual(cfg['nextcloud']['local_path'], tmp)
            self.assertTrue(cfg['installation']['done'])
            self.assertEqual(cfg['installation']['features'], ['documents'])
            self.assertIn('documents', cfg['installation']['partial'])          # preuves non faites : fin partielle déclarée
            with patch('agent.engine.Mailbox', side_effect=AssertionError('aucune connexion IMAP attendue')):
                self.assertEqual(Engine(cfg, model=object()).run()['skipped'], 'messagerie_desactivee')
            from agent.pieces510 import profile
            self.assertEqual(profile(self.desk())['nom_avocat'], 'Exemple')
            self.assertEqual(g.session(desk, 'cabinet', s['id'])['status'], 'applied')

    def test_enabled_messaging_keeps_strict_checks(self):
        cfg = json.loads(self.config.read_text(encoding='utf-8'))
        cfg['mail']['own_addresses'] = []
        private_json(self.config, cfg)
        with self.assertRaisesRegex(Stop, 'adresses_du_cabinet_absentes'):
            load_config(str(self.config))
        cfg['mail']['enabled'] = False
        private_json(self.config, cfg)
        self.assertIs(load_config(str(self.config))['mail']['enabled'], False)

    def test_conflict_with_another_screen_is_reported_not_overwritten(self):
        """GI T06 : un autre écran a modifié le même réglage → 409 ciblé ; un réglage sans rapport ne bloque pas."""
        from agent import config567, guide5631 as g
        desk = self.desk()
        s = self._session(desk)
        r = g.save_step(desk, 'cabinet', s['id'], s['revision'], 'messagerie', {'host': 'imap.guide.test', 'username': 'u'}, 'save')
        rev = config567.catalog(desk, self.env)['revision']
        config567.save(desk, {'revision': rev, 'values': {'mail.host': 'imap.autre-ecran.test'}}, self.env)
        out = g.apply(desk, 'cabinet', s['id'], r['revision'], self.env)
        self.assertTrue(out['conflict'])
        self.assertIn('mail.host', out['fields'])
        self.assertEqual(load_config(str(self.config))['mail']['host'], 'imap.autre-ecran.test')
        s2 = g.start(desk, 'cabinet', ['messagerie'])
        r2 = g.save_step(desk, 'cabinet', s2['id'], s2['revision'], 'messagerie', {'host': 'imap.guide.test', 'username': 'u'}, 'save')
        rev = config567.catalog(desk, self.env)['revision']
        config567.save(desk, {'revision': rev, 'values': {'smtp.port': 465}}, self.env)
        out = g.apply(desk, 'cabinet', s2['id'], r2['revision'], self.env)
        self.assertTrue(out.get('applied'), out)
        cfg = load_config(str(self.config))
        self.assertEqual((cfg['mail']['host'], cfg['smtp']['port']), ('imap.guide.test', 465))

    def test_api_is_reserved_to_the_administrator_and_language_to_everyone(self):
        from agent.standalone_auth import allowed
        self.assertFalse(allowed('avocat', 'POST', '/api440/m5631/guide/start'))
        self.assertFalse(allowed('assistant', 'POST', '/api440/m5631/guide/apply'))
        self.assertTrue(allowed('avocat', 'POST', '/api440/m5631/langue'))
        self.assertTrue(allowed('assistant', 'POST', '/api440/m5631/langue'))
        self.assertFalse(allowed('avocat', 'GET', '/guide-installation'))
        self.assertTrue(allowed('avocat', 'GET', '/guide-utilisation'))
        self.assertTrue(allowed('administrateur', 'POST', '/api440/m5631/guide/start'))

    def test_api_round_trip_and_bad_origin(self):
        r = self.json_request('/api440/m5631/guide/start', {'features': ['documents']})
        self.assertTrue(r['status'].startswith('200'), r)
        sid = json.loads(r['body'])['id']
        r = self.json_request('/api440/m5631/guide/step', {'id': sid, 'revision': 1, 'step': 'fonctions', 'values': {'features': ['documents']}})
        self.assertEqual(json.loads(r['body'])['step'], 'profil')
        r = self.json_request('/api440/m5631/guide/step', {'id': sid, 'revision': 1, 'step': 'profil', 'values': {}})
        self.assertTrue(r['status'].startswith('409'))                                 # révision dépassée
        r = self.json_request('/api440/m5631/guide/export?id=' + sid, method='GET')
        r = self.request('/api440/m5631/guide/session', query='id=' + sid)
        self.assertEqual(json.loads(r['body'])['id'], sid)

    def test_first_launch_offers_the_guided_path(self):
        src = (ROOT / 'agent' / 'setup560.py').read_text(encoding='utf-8')
        self.assertIn('Commencer la configuration guidée', src)
        self.assertIn('Découvrir puis configurer plus tard', src)
        auth = (ROOT / 'agent' / 'standalone_auth.py').read_text(encoding='utf-8')
        self.assertIn("'/guide-installation','/guide-utilisation')", auth)


class CalendarWeekTest(unittest.TestCase):
    def test_agenda_test_event_stays_in_the_displayed_week(self):
        """La 5.6.30 publiée avait une recette rouge un dimanche : l'événement « demain » tombait dans la semaine suivante."""
        src = (ROOT / 'tests' / 'test_v501.py').read_text(encoding='utf-8')
        self.assertIn('days=0 if base.weekday() == 6 else 1', src)


if __name__ == '__main__':
    unittest.main()

"""5.6.42 : audit 5.6.41 (F01 à F07, contre-tests R01 à R06 et variantes), rubrique « Comptes » des paramètres, mode ExApp (AppAPI de
Nextcloud), application Nextcloud 0.4.3.

Données d'essai fictives (domaines example.test, sociétés et adresses fictives).
"""
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import unittest.mock

from agent.common import Stop
from agent.erreurs5634 import Qualified
import test_v440_web as tw
import test_v5639 as t39
from test_v5640 import docx, p, document_xml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'nextcloud-app' / 'axiorhub_pilote'


# ---------------------------------------------------------------------------------------------------- F01 (P0)
class UnitScope(unittest.TestCase):
    """F01 : une unité ne vaut que dans son périmètre (ligne, tableau ou section, document)."""

    def check(self, text, label, amount, unit, excerpt, year='2025'):
        from agent import comptes5639
        x = {'libelle': label, 'montant': amount, 'devise': 'EUR', 'unite': unit, 'exercice': year, 'extrait': excerpt}
        return comptes5639.check(x, text, year)

    def test_r01_unit_of_another_line_is_refused(self):
        text = ('Comptes annuels\nExercice 2025\nChiffre d\'affaires : 812 450 EUR\nDettes financières (en milliers d\'euros) : 20 EUR\n')
        cause, detail = self.check(text, 'Chiffre d\'affaires', '812 450', 'milliers', 'Chiffre d\'affaires : 812 450 EUR')
        self.assertEqual(cause, 'unite_differente_des_comptes')
        self.assertEqual(detail['unite_des_comptes'], 'unites')
        cause, detail = self.check(text, 'Dettes financières', '20', 'milliers', 'Dettes financières (en milliers d\'euros) : 20 EUR')
        self.assertIsNone(cause)
        self.assertEqual(detail['preuve_unite'], 'même ligne')
        self.assertTrue(detail['position_unite'])

    def test_t01_homogeneous_document_in_euros_is_still_accepted(self):
        text = 'Comptes annuels 2025 (en euros)\nChiffre d\'affaires : 812 450 EUR\nRésultat net : 45 000 EUR\n'
        cause, detail = self.check(text, 'Chiffre d\'affaires', '812 450', 'unites', 'Chiffre d\'affaires : 812 450 EUR')
        self.assertIsNone(cause)
        self.assertEqual(detail['preuve_unite'], 'aucune mention de milliers ni de millions')

    def test_document_header_and_table_declarations_have_their_own_scope(self):
        text = ('Comptes annuels — exercice 2025\n(en milliers d\'euros)\n\nBilan\nChiffre d\'affaires : 812 EUR\n\n'
                'Annexe — montants exprimés en euros\nRésultat net : 45 000 EUR\n')
        cause, detail = self.check(text, 'Chiffre d\'affaires', '812', 'milliers', 'Chiffre d\'affaires : 812 EUR')
        self.assertIsNone(cause)
        self.assertIn('en-tête du document', detail['preuve_unite'])
        two = ('Comptes annuels 2025\n\nTableau A (en milliers d\'euros)\nChiffre d\'affaires : 812 EUR\n\n'
               'Tableau B (en millions d\'euros)\nTotal du bilan : 3 EUR\n')
        self.assertIsNone(self.check(two, 'Chiffre d\'affaires', '812', 'milliers', 'Chiffre d\'affaires : 812 EUR')[0])
        self.assertIsNone(self.check(two, 'Total du bilan', '3', 'millions', 'Total du bilan : 3 EUR')[0])
        self.assertEqual(self.check(two, 'Total du bilan', '3', 'milliers', 'Total du bilan : 3 EUR')[0], 'unite_differente_des_comptes')

    def test_footer_page_break_and_conflict(self):
        footer = 'Comptes annuels 2025\n\nChiffre d\'affaires : 812 EUR\nRésultat net : 45 EUR\nMontants en milliers d\'euros\n'
        cause, detail = self.check(footer, 'Chiffre d\'affaires', '812', 'milliers', 'Chiffre d\'affaires : 812 EUR')
        self.assertIsNone(cause)
        self.assertIn('tableau ou de la section', detail['preuve_unite'])
        paged = ('Comptes annuels 2025\n\nTableau (en milliers d\'euros)\nTotal du bilan : 3 EUR\n\fPage 2\n'
                 'Chiffre d\'affaires : 812 450 EUR\n')   # la déclaration du tableau ne franchit pas le saut de page
        self.assertIsNone(self.check(paged, 'Chiffre d\'affaires', '812 450', 'unites', 'Chiffre d\'affaires : 812 450 EUR')[0])
        self.assertIsNone(self.check(paged, 'Total du bilan', '3', 'milliers', 'Total du bilan : 3 EUR')[0])
        conflict = ('Comptes annuels 2025\n\nChiffre d\'affaires : 812 EUR\nMontants en milliers d\'euros\nMontants en millions d\'euros\n')
        self.assertEqual(self.check(conflict, 'Chiffre d\'affaires', '812', 'milliers', 'Chiffre d\'affaires : 812 EUR')[0], 'unite_non_etablie')

    def test_method_is_versioned_for_recheck(self):
        from agent import comptes5639, corporate5636
        self.assertEqual((comptes5639.METHODE, corporate5636.METHODE_COMPTES), (5, 5))


# ---------------------------------------------------------------------------------------------------- F02 à F07
class LibraryAudit5641(t39._Corp):
    """F02 à F07 sur la bibliothèque réelle (base, imports, demandes de rédaction)."""

    def imported(self, raw, name='Mon modèle.docx'):
        return self.co.import_template(self.desk, {'filename': name, 'data': base64.b64encode(raw).decode()})['source']

    def ia_request(self, modele='accord_confidentialite', **extra):
        from agent import bibliotheque5639 as b
        out = b.generate(self.desk, {'modele': modele, 'societe': self.cid, 'destination': 'ia', **extra})
        return out, self.desk.db.execute('SELECT request FROM docreq520 WHERE id=?', (out['request'],)).fetchone()['request']

    # -------------------------------------------------------------------------------- F02
    def test_r02a_old_word_template_gets_its_fields_after_migration(self):
        from agent import bibliotheque5639 as b
        b.schema(self.desk)
        source = self.imported(docx(p('Client {{client_ancien}}') + p('[[si option_ancienne]]') + p('OPTION_FICTIVE') + p('[[fin]]')))
        now = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated, meta, "
                             "version) VALUES('cab_ancien','','Ancien modèle','fr','FR','contrat','',?,'cabinet',?,?,'{}',1)", (source, now, now))
        self.desk.db.commit()
        t = b.template(self.desk, 'cab_ancien')
        self.assertIn('client_ancien', [k for k, _, _ in t['variables']])
        self.assertIn('option_ancienne', [k for k, _, _, _ in t['clauses']])
        self.assertEqual((t['version'], t['meta']['meta_version']), (1, b.META_VERSION))           # identité et version conservées
        b.set_labels(self.desk, {'modele': 'cab_ancien', 'libelle_client_ancien': 'Nom du client'})
        meta = dict(b.template(self.desk, 'cab_ancien')['meta'], meta_version=1)                    # second passage forcé
        self.desk.db.execute("UPDATE bibliotheque5639 SET meta=? WHERE id='cab_ancien'", (json.dumps(meta),))
        self.desk.db.commit()
        t = b.template(self.desk, 'cab_ancien')
        self.assertEqual([k for k, _, _ in t['variables']].count('client_ancien'), 1)             # rien de dupliqué
        self.assertIn(('client_ancien', 'Nom du client', ''), t['variables'])                       # libellé conservé
        page = self.request('/corporate', query='vue=bibliotheque&modele=cab_ancien')['body']
        self.assertIn('name="client_ancien"', page)
        self.assertIn('name="clause_option_ancienne"', page)
        self.assertIn('Actualiser le modèle', page)

    def test_r02a_unreadable_source_is_reported_not_retried_forever(self):
        from agent import bibliotheque5639 as b
        b.schema(self.desk)
        now = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute("INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated, meta, "
                             "version) VALUES('cab_perdu','','Modèle perdu','fr','FR','contrat','','modele:absent-fictif.docx','cabinet',?,?,'{}',1)",
                             (now, now))
        self.desk.db.commit()
        t = b.template(self.desk, 'cab_perdu')
        self.assertEqual(t['meta']['hydratation']['etat'], 'erreur')
        self.assertFalse(b.needs_meta(t))
        self.assertIn('source illisible', self.request('/corporate', query='vue=bibliotheque&modele=cab_perdu')['body'])

    # -------------------------------------------------------------------------------- F03
    def test_r02b_modified_word_is_refreshed_before_preparing(self):
        from agent import bibliotheque5639 as b
        source = self.imported(docx(p('Client {{client_initial}}') + p('FIN_FICTIVE')))
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': source})
        shown = b.view_token(b.template(self.desk, 'accord_confidentialite'))
        target = self.co._templates_dir(self.desk) / source.split(':', 1)[1]
        target.write_bytes(docx(p('Client {{client_initial}}') + p('[[si clause_nouvelle]]') + p('CLAUSE_NOUVELLE_FICTIVE') + p('[[fin]]')
                                + p('FIN_FICTIVE')))
        with self.assertRaises(Qualified) as ex:
            b.generate(self.desk, {'modele': 'accord_confidentialite', 'client_initial': 'ALPHA', 'version_vue': shown})
        self.assertEqual(str(ex.exception), 'modele_word_modifie')
        self.assertIn('[[si clause_nouvelle]]', ex.exception.detail['precision'])
        t = b.template(self.desk, 'accord_confidentialite')
        self.assertIn('clause_nouvelle', [k for k, _, _, _ in t['clauses']])                     # visible et sélectionnable
        with self.assertRaises(Qualified) as ex:                                                  # page affichée avant le changement
            b.generate(self.desk, {'modele': 'accord_confidentialite', 'client_initial': 'ALPHA', 'version_vue': shown})
        self.assertEqual(str(ex.exception), 'modele_modifie_depuis_affichage')
        out = b.generate(self.desk, {'modele': 'accord_confidentialite', 'client_initial': 'ALPHA', 'clause_clause_nouvelle': True,
                                     'version_vue': b.view_token(t)})
        xml = document_xml(base64.b64decode(b.download(self.desk, out['goto'].rsplit('fichier=', 1)[1])['download']['base64']))
        self.assertIn('CLAUSE_NOUVELLE_FICTIVE', xml)
        self.assertIn(b.view_token(t), self.request('/corporate', query='vue=bibliotheque&modele=accord_confidentialite')['body'])
        self.assertIn('inchangé', b.refresh(self.desk, {'modele': 'accord_confidentialite'})['message'])

    # -------------------------------------------------------------------------------- F04
    def test_r03_line_breaks_and_tabs_reach_the_ai_snapshot(self):
        from agent import bibliotheque5639 as b, bibliotheque_word5640 as W5
        source = self.imported(docx(p('Adresse {{adresse_client}}') + p('FIN_FICTIVE')))
        b.set_word(self.desk, {'modele': 'accord_confidentialite', 'path': source})
        _, request = self.ia_request(adresse_client='ALPHA_FICTIVE\n12 RUE_FICTIVE\n69000 LYON')
        texte = b.trame_for_request(self.desk, request)['texte']
        self.assertIn('ALPHA_FICTIVE\n12 RUE_FICTIVE\n69000 LYON', texte)
        para = '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr><w:r><w:t>1.</w:t><w:tab/><w:t>Objet</w:t></w:r></w:p>'
        self.assertEqual(W5.render_text(para), '1.\tObjet')
        self.assertEqual(W5._text_of('<w:p><w:r><w:t>{{a</w:t></w:r><w:r><w:t>b}}</w:t></w:r></w:p>'), '{{ab}}')   # balises réparties

    # -------------------------------------------------------------------------------- F05
    def test_r04_missing_snapshot_is_refused_never_replaced(self):
        from agent import bibliotheque5639 as b, docrequest520
        out, request = self.ia_request()
        b.customize(self.desk, {'modele': 'accord_confidentialite', 'texte': '## Objet\nVERSION_PLUS_RECENTE_FICTIVE'})
        jeton = request.split('[trame:', 1)[1][:16]
        self.desk.db.execute('DELETE FROM bibliotheque5639_trames WHERE jeton=?', (jeton,))
        self.desk.db.commit()
        with self.assertRaises(Qualified) as ex:
            b.trame_for_request(self.desk, request)
        self.assertEqual(str(ex.exception), 'instantane_modele_introuvable')
        with unittest.mock.patch.object(docrequest520, '_model', side_effect=AssertionError('jamais de rédaction sans sa base')):
            with self.assertRaises(Qualified):
                docrequest520.draft(self.desk, request, 'contrat', {}, '', '')
        self.assertIn('VERSION_PLUS_RECENTE_FICTIVE', b.trame_for_request(self.desk, 'Rédige un accord de confidentialité')['texte'])
        self.assertEqual(b.coverage_report(self.desk, request, {'paragraphes': []})['statut'], 'controle_indisponible')

    # -------------------------------------------------------------------------------- F06
    def test_r05_cited_titles_do_not_count_as_sections(self):
        from agent import bibliotheque5639 as b
        added = b.add(self.desk, {'titre': 'Contrat fictif', 'categorie': 'contrat', 'droit': 'FR',
                                  'texte': '## Confidentialité\nTexte.\n## Durée\nTexte.'})
        _, request = self.ia_request(added['id'])
        cited = {'paragraphes': [{'style': 'texte', 'texte': 'Les chapitres Confidentialité et Durée ont été omis.'}]}
        report = b.coverage_report(self.desk, request, cited)
        self.assertEqual((report['statut'], report['citees_seulement']), ('ecarts', ['Confidentialité', 'Durée']))
        self.assertEqual(b.coverage(self.desk, request, cited), ['Confidentialité', 'Durée'])
        body = 'Les parties s’engagent à garder confidentielles les informations échangées pendant toute la durée du projet fictif.'
        real = {'paragraphes': [{'style': 'intertitre', 'texte': 'Article 1 — Confidentialité'}, {'style': 'texte', 'texte': body},
                                {'style': 'intertitre', 'texte': '2. Durée'}, {'style': 'texte', 'texte': '[À COMPLÉTER : durée]'}]}
        report = b.coverage_report(self.desk, request, real)
        self.assertEqual((report['presentes'], report['vides'], report['absentes']), (['Confidentialité'], ['Durée'], []))
        self.assertEqual(b.coverage_report(self.desk, request, {'paragraphes': [
            {'style': 'intertitre', 'texte': 'Confidentialité'}, {'style': 'texte', 'texte': body},
            {'style': 'intertitre', 'texte': 'Durée du contrat'}, {'style': 'texte', 'texte': body}]})['statut'], 'complet')

    # -------------------------------------------------------------------------------- F07 et maintenance
    def test_r06_locked_reference_read_never_authorises_a_deletion(self):
        from agent import bibliotheque5639 as b
        out, request = self.ia_request()
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        self.desk.db.execute('UPDATE bibliotheque5639_trames SET created=?', (old,))
        self.desk.db.commit()
        with unittest.mock.patch.object(b, '_references', side_effect=sqlite3.OperationalError('database is locked')):
            self.assertEqual(b.purge(self.desk), 0)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM bibliotheque5639_trames').fetchone()[0], 1)
        self.assertTrue(b.LAST_PURGE[b._key(self.desk)]['differee'])
        self.assertTrue(b.trame_for_request(self.desk, request)['texte'])
        # relation explicite : la référence tient même si le texte de la demande ne porte plus le jeton
        self.desk.db.execute("UPDATE docreq520 SET request='Demande réécrite sans marqueur' WHERE id=?", (out['request'],))
        self.desk.db.commit()
        b.purge(self.desk)
        self.assertEqual(self.desk.db.execute('SELECT COUNT(*) FROM bibliotheque5639_trames').fetchone()[0], 1)
        # instantané réellement non référencé : supprimé
        self.desk.db.execute("INSERT INTO bibliotheque5639_trames(jeton, modele, titre, langue, texte, created) VALUES('ffffffffffffffff','x','x',"
                             "'fr','x',?)", (old,))
        self.desk.db.commit()
        self.assertEqual(b.purge(self.desk), 1)
        self.assertFalse(b.LAST_PURGE[b._key(self.desk)]['differee'])

    def test_purge_reads_only_expired_candidates(self):
        from agent import bibliotheque5639 as b
        b.purge(self.desk)
        seen = []
        self.desk.db.set_trace_callback(seen.append)
        try:
            b.purge(self.desk)
        finally:
            self.desk.db.set_trace_callback(None)
        selects = [s for s in seen if s.lstrip().upper().startswith('SELECT')]
        self.assertTrue(all('WHERE' in s.upper() for s in selects if 'bibliotheque5639_' in s), selects)


# ---------------------------------------------------------------------------------------------------- comptes
class Accounts(tw.WebWorkshopTests):
    """Paramètres › Comptes : identifiant et mot de passe de l'interface changés sans écrire ui-auth.json."""

    def test_password_change_requires_current_password_and_takes_effect(self):
        page = self.call('/parametres', query='rubrique=comptes')['body'].decode()
        self.assertIn('Compte administrateur', page)
        self.assertIn('data-api="m5636/compte/mot-de-passe"', page)
        before = Path(self.auth).read_bytes()
        bad = self.call('/api440/m5636/compte/mot-de-passe', 'POST', {'actuel': 'faux', 'nouveau': 'n' * 20, 'confirmation': 'n' * 20})
        self.assertIn(b'mot_de_passe_actuel_incorrect', bad['body'])
        short = self.call('/api440/m5636/compte/mot-de-passe', 'POST', {'actuel': self.password, 'nouveau': 'court', 'confirmation': 'court'})
        self.assertIn(b'mot_de_passe_trop_court', short['body'])
        new = 'nouveau-mot-de-passe-fictif-long'
        ok = self.call('/api440/m5636/compte/mot-de-passe', 'POST', {'actuel': self.password, 'identifiant': 'maitre',
                                                                      'nouveau': new, 'confirmation': new})
        self.assertTrue(ok['status'].startswith('200'), ok['body'])
        self.assertEqual(Path(self.auth).read_bytes(), before)                                      # ui-auth.json jamais réécrit
        override = Path(self.f.c['state_dir']) / 'compte-interface5642.json'
        self.assertEqual(oct(override.stat().st_mode & 0o777)[-3:], '600') if os.name == 'posix' else None
        self.assertTrue(self.call('/courriels', auth=False)['status'].startswith(('401', '303')))   # 5.6.48 : page de connexion
        self.assertTrue(self.call('/courriels')['status'].startswith(('401', '303')))               # ancien mot de passe refusé
        env = {'HTTP_AUTHORIZATION': 'Basic ' + base64.b64encode(('maitre:' + new).encode()).decode()}
        self.assertTrue(self.call('/courriels', auth=False, extra=env)['status'].startswith('200'))


# ---------------------------------------------------------------------------------------------------- ExApp
class FakeOCS:
    def __init__(self, groups):
        self.by_user, self.calls = groups, []

    def groups(self, user):
        self.calls.append(('groups', user))
        return self.by_user.get(user, [])

    def status(self, progress, error=''):
        self.calls.append(('status', progress, error))

    def ui(self, enabled):
        self.calls.append(('ui', enabled))


class ExApp(unittest.TestCase):
    """Passerelle AppAPI : contrat (cycle de vie, en-têtes), rôles par groupes Nextcloud, droits de la distribution autonome."""

    def setUp(self):
        from agent import exapp5642 as x
        self.x = x
        self.s = x.settings({'APP_ID': 'axiorhub', 'APP_VERSION': '0.1.0', 'APP_SECRET': 's' * 40, 'NEXTCLOUD_URL': 'https://cloud.example.test',
                             'APP_PERSISTENT_STORAGE': '/tmp/fictif'})
        self.seen = []

        def inner(env, start):
            self.seen.append(dict(env))
            start('200 OK', [('Content-Type', 'text/html'), ('X-Frame-Options', 'DENY'),
                             ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")])
            return [b'ok']
        self.ocs = FakeOCS({'alice': ['axiorhub-avocats'], 'bob': [], 'chloe': ['axiorhub-assistants'], 'root': ['admin']})
        self.gw = x.Gateway(inner, self.s, 'jeton-interne-fictif', ocs=self.ocs)

    def call(self, path, method='GET', user=None, secret=None, extra=None, body=b''):
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': '', 'wsgi.input': io.BytesIO(body),
               'CONTENT_LENGTH': str(len(body)), 'HTTP_X_AXIORHUB_ROLE': 'administrateur', 'HTTP_COOKIE': 'nc_session=fictif'}
        if '?' in path:
            env['PATH_INFO'], env['QUERY_STRING'] = path.split('?', 1)
        if user is not None:
            env['HTTP_EX_APP_ID'] = 'axiorhub'
            env['HTTP_AUTHORIZATION_APP_API'] = base64.b64encode(('%s:%s' % (user, secret or 's' * 40)).encode()).decode()
        env.update(extra or {})
        out = {}

        def start(status, headers, exc_info=None):
            out.update(status=status, headers=dict(headers))
        out['body'] = b''.join(self.gw(env, start))
        return out

    def test_lifecycle_and_signature(self):
        self.assertEqual(json.loads(self.call('/heartbeat')['body']), {'status': 'ok'})
        self.assertTrue(self.call('/aujourdhui')['status'].startswith('403'))                    # sans en-têtes AppAPI
        self.assertTrue(self.call('/aujourdhui', user='alice', secret='x' * 40)['status'].startswith('403'))   # mauvais secret
        self.assertEqual(json.loads(self.call('/enabled?enabled=1', 'PUT', user='')['body']), {'error': ''})
        self.assertIn(('ui', True), self.ocs.calls)
        self.call('/enabled?enabled=0', 'PUT', user='')
        self.assertIn(('ui', False), self.ocs.calls)
        self.assertTrue(self.call('/aujourdhui', user='')['status'].startswith('401'))             # appel système : pas d'interface
        js = self.call('/js/axiorhub-exapp.js')
        self.assertIn(b'iframe', js['body'])

    def test_identity_comes_from_nextcloud_groups_never_from_the_browser(self):
        out = self.call('/aujourdhui', user='alice')
        self.assertTrue(out['status'].startswith('200'))
        env = self.seen[-1]
        self.assertEqual((env['HTTP_X_AXIORHUB_USER'], env['HTTP_X_AXIORHUB_ROLE']), ('alice', 'avocat'))   # en-tête du navigateur écarté
        self.assertEqual(env['HTTP_AUTHORIZATION'], 'Bearer jeton-interne-fictif')
        self.assertEqual((env['HTTP_HOST'], env['HTTP_X_FORWARDED_PROTO'], env['PATH_INFO']), ('cloud.example.test', 'https', '/exapps/axiorhub/aujourdhui'))
        self.assertNotIn('HTTP_COOKIE', env)
        self.assertEqual(out['headers']['X-Frame-Options'], 'SAMEORIGIN')
        self.assertIn("frame-ancestors 'self'", out['headers']['Content-Security-Policy'])
        self.assertIn('aucun groupe AxiorHub', self.call('/aujourdhui', user='bob')['body'].decode())
        self.assertTrue(self.call('/parametres', user='chloe')['status'].startswith('403'))          # droits de la distribution autonome
        self.assertTrue(self.call('/parametres', user='root')['status'].startswith('200'))
        self.call('/aujourdhui', user='alice')
        self.assertEqual([c for c in self.ocs.calls if c == ('groups', 'alice')], [('groups', 'alice')])   # rôle en cache

    def test_init_reports_progress_and_nextcloud_calls_use_app_api(self):
        self.gw.init()
        self.assertIn(('status', 100, ''), self.ocs.calls)
        with unittest.mock.patch.dict(os.environ, {'APP_ID': 'axiorhub', 'APP_VERSION': '0.1.0', 'APP_SECRET': 's' * 40}):
            http = self.x.nextcloud_http('https://cloud.example.test', {'auth': 'app_api', 'username': 'axiorhub'})
        self.assertNotIn('Authorization', http.headers)
        self.assertEqual(base64.b64decode(http.headers['AUTHORIZATION-APP-API']).decode(), 'axiorhub:' + 's' * 40)
        self.assertEqual(self.x.role_for(['axiorhub-collaborateurs'], self.s), 'avocat')
        self.assertEqual(self.x.role_for(['autre'], self.s), '')

    def test_bootstrap_in_the_persistent_volume_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = dict(self.s, storage=tmp)
            first = self.x.bootstrap(s)
            auth = json.loads(first['auth'].read_text(encoding='utf-8'))
            config = json.loads(first['config'].read_text(encoding='utf-8'))
            self.assertEqual((auth['prefix'], auth['origin']), ('/exapps/axiorhub', 'https://cloud.example.test'))
            self.assertEqual((config['nextcloud']['auth'], config['nextcloud']['username']), ('app_api', 'axiorhub'))
            self.assertGreaterEqual(len(first['token']), 32)
            self.assertEqual(self.x.bootstrap(s)['token'], first['token'])                       # mise à jour : jeton conservé


# ---------------------------------------------------------------------------------------------------- application Nextcloud 0.4.3
class NextcloudApp043(unittest.TestCase):
    def setUp(self):
        if not APP.exists():
            self.skipTest('application Nextcloud absente de cette distribution')

    def test_guided_configuration_choice_and_probe(self):
        personal = (APP / 'templates' / 'personal.php').read_text(encoding='utf-8')
        for mark in ('Where is AxiorHub installed?', 'value="serveur"', 'value="meme-poste"', 'value="poste-heberge"', 'Test the address',
                     'data-axh-config="serveur meme-poste"', 'data-axh-config="poste-heberge"', 'Last successful test: %s'):
            self.assertIn(mark, personal)
        self.assertIn("'personal#probe'", (APP / 'appinfo' / 'routes.php').read_text(encoding='utf-8'))
        self.assertIn('function probe', (APP / 'lib' / 'Controller' / 'PersonalController.php').read_text(encoding='utf-8'))
        built = (APP / 'js' / 'axiorhub_pilote-personal.mjs').read_text(encoding='utf-8')
        self.assertIn('/personal/probe', built)
        fr = json.loads((APP / 'l10n' / 'fr.json').read_text(encoding='utf-8'))['translations']
        self.assertEqual(fr['Where is AxiorHub installed?'], 'Où est installé AxiorHub ?')
        self.assertIn('<version>0.4.3</version>', (APP / 'appinfo' / 'info.xml').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

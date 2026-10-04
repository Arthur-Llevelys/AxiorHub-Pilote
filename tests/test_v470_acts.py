"""4.7.0 : mentions obligatoires, relecture contradictoire, branchement sur les circuits d'actes, interface."""
import json
import unittest
from unittest.mock import patch

import test_agent as fixtures
from agent import adversarial470, facts460, legal_memory as lm, sources470, strategic, templates470, verify470
from agent.common import Stop
from agent.desk import Desk
from agent.index import DocumentIndex
from test_v440_web import WebWorkshopTests as Base0
from test_v470_refs import CORPUS

COMPLETE_CONCLUSIONS = ("CONCLUSIONS pour la société ALPHA, demanderesse, devant le tribunal judiciaire de Lyon, RG 26/00123.\n"
                        "I. Exposé des faits et de la procédure.\nII. Discussion des moyens.\n"
                        "III. PAR CES MOTIFS, il est demandé au tribunal de condamner la défenderesse, outre l'article 700 et les dépens.\n"
                        "Bordereau de pièces communiquées : pièce n° 1. Maître Dupont, avocat au barreau de Lyon.")


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.desk = Desk(self.f.c)
        lm.ensure_schema(self.desk)


class Mentions(Base):
    def test_complete_conclusions_pass(self):
        check = templates470.check(self.desk, 'conclusions', COMPLETE_CONCLUSIONS)
        self.assertTrue(check['ok'], check['missing'])

    def test_missing_dispositif_and_bordereau_block(self):
        text = "Conclusions pour la société ALPHA devant le tribunal judiciaire, RG 26/00123. Faits et procédure. Discussion."
        check = templates470.check(self.desk, 'conclusions', text)
        self.assertFalse(check['ok'])
        self.assertEqual({x['id'] for x in check['missing']}, {'dispositif', 'bordereau'})

    def test_warning_mentions_do_not_block(self):
        text = COMPLETE_CONCLUSIONS.replace("outre l'article 700 et les dépens", "").replace('Maître Dupont, avocat au barreau de Lyon.', '')
        check = templates470.check(self.desk, 'conclusions', text)
        self.assertTrue(check['ok'])
        self.assertTrue(check['warnings'])

    def test_mise_en_demeure_needs_delay_and_consequence(self):
        text = "Objet : mise en demeure. Monsieur X, en vertu de la facture n° 3, vous devez 1 200 euros."
        check = templates470.check(self.desk, 'mise_en_demeure', text)
        self.assertEqual({x['id'] for x in check['missing']}, {'delai', 'consequence'})

    def test_confrere_mention_must_be_in_the_header(self):
        body = "Cher Confrère, je vous écris pour mon client dans le dossier RG 26/1. " + "Texte. " * 200
        late = templates470.check(self.desk, 'courrier_confrere', body + " Officielle. Bien confraternellement. Maître X, avocat")
        self.assertIn('mention', {x['id'] for x in late['missing']})
        early = templates470.check(self.desk, 'courrier_confrere', "OFFICIEL\n" + body + " Bien confraternellement. Maître X, avocat")
        self.assertTrue(early['ok'], early['missing'])

    def test_every_kind_has_a_skeleton_that_names_required_mentions(self):
        for item in templates470.listing(self.desk):
            self.assertTrue(templates470.effective(self.desk, item['kind'])['skeleton'])

    def test_customization_disable_add_and_reject_dangerous_patterns(self):
        templates470.save_customization(self.desk, 'conclusions', {
            'disabled': ['bordereau'], 'extra': [{'label': 'Mention du cabinet', 'patterns': ['cabinet exemple'], 'severity': 'bloquante'}]})
        text = COMPLETE_CONCLUSIONS.replace('Bordereau de pièces communiquées : pièce n° 1.', '')
        check = templates470.check(self.desk, 'conclusions', text)
        self.assertEqual([x['label'] for x in check['missing']], ['Mention du cabinet'])
        self.assertTrue(templates470.check(self.desk, 'conclusions', text + ' Cabinet Exemple')['ok'])
        with self.assertRaises(Stop):
            templates470.save_customization(self.desk, 'conclusions', {'extra': [{'label': 'x', 'patterns': ['(a+)+$']}]})
        with self.assertRaises(Stop):
            templates470.save_customization(self.desk, 'conclusions', {'extra': [{'label': 'x', 'patterns': ['(']}]})
        with self.assertRaises(Stop):
            templates470.save_customization(self.desk, 'inconnu', {})


class StrategicActs(Base):
    class Model:
        text_body = ''
        sections = None

        def __init__(self, cfg):
            pass

        def ask(self, stage, payload):
            sid = payload['sources'][0]['id']
            body = type(self).text_body
            return {'title': 'Projet', 'act_type': payload['type_acte'], 'introduction': 'Introduction.', 'introduction_source_ids': [sid],
                    'sections': [{'heading': 'Corps', 'body': body, 'source_ids': [sid]}],
                    'requests': [], 'exhibits_referenced': [], 'placeholders': [], 'points_for_lawyer': [],
                    'source_ids': [sid], 'limits': []}

    def setUp(self):
        super().setUp()
        DocumentIndex(self.f.c['state_dir']).put_source(self.f.matters[0], '/Dossiers/DEMO/facture.txt',
                                                        'La société ALPHA réclame 12 000 euros.', 'e1', '2026-09-09T10:00:00+00:00', 'document')

    def draft(self, act_type, body):
        self.Model.text_body = body
        with patch('agent.strategic.Model', self.Model):
            return strategic.draft_act(self.desk, {'matter': 'DOS-001', 'act_type': act_type, 'instruction': 'Préparer.'})

    def test_missing_mentions_prevent_ready_status_and_validation(self):
        result = self.draft('mise_en_demeure', 'Objet : mise en demeure. En vertu de la facture, payer 12 000 euros.')
        self.assertEqual(result['statut'], 'incomplet')
        self.assertTrue(result['mentions_manquantes'])
        project = strategic.act_projects(self.desk, 'DOS-001')[0]
        self.assertEqual(project['status'], 'incomplete')
        self.assertTrue(project['content'].startswith('INCOMPLET'))
        with self.assertRaisesRegex(Stop, 'mentions_obligatoires_manquantes'):
            strategic.change(self.desk, 'validate_act', {'matter': 'DOS-001', 'project': project['id']})

    def test_complete_act_with_unverified_citation_is_marked_and_needs_acknowledgement(self):
        body = ("Destinataire : société BETA. Objet : mise en demeure. En vertu de la facture n° 3 et de l'article 1231-1 du Code civil, "
                "vous devez 12 000 euros. Je vous mets en demeure de payer dans un délai de 8 jours. À défaut, je saisirai le tribunal. "
                "Lettre recommandée avec accusé de réception. Tous droits réservés. Maître Dupont, avocat.")
        result = self.draft('mise_en_demeure', body)
        self.assertEqual(result['statut'], 'propose')
        self.assertTrue(result['references_a_verifier'])
        project = strategic.act_projects(self.desk, 'DOS-001')[0]
        self.assertIn('À VÉRIFIER', project['content'])
        with self.assertRaisesRegex(Stop, 'references_a_verifier_avant_validation'):
            strategic.change(self.desk, 'validate_act', {'matter': 'DOS-001', 'project': project['id']})
        strategic.change(self.desk, 'validate_act', {'matter': 'DOS-001', 'project': project['id'], 'acknowledge_unverified': 'yes'})
        self.assertEqual(strategic.act_projects(self.desk, 'DOS-001')[0]['status'], 'validated')

    def test_verified_citation_needs_no_acknowledgement(self):
        orig = sources470.Sources

        class Fake(orig):
            def __init__(s, desk, **kw):
                orig.__init__(s, desk, [sources470.LocalProvider(data=CORPUS)], True)
        body = ("Destinataire : société BETA. Objet : mise en demeure. En vertu de l'article 1103 du Code civil et de la facture, "
                "vous devez 12 000 euros. Je vous mets en demeure de payer dans un délai de 8 jours. À défaut, je saisirai le tribunal. "
                "Lettre recommandée avec accusé de réception. Tous droits réservés. Maître Dupont, avocat.")
        with patch('agent.verify470.sources470.Sources', Fake):
            result = self.draft('mise_en_demeure', body)
        self.assertFalse(result['references_a_verifier'])
        project = strategic.act_projects(self.desk, 'DOS-001')[0]
        self.assertNotIn('À VÉRIFIER', project['content'])
        strategic.change(self.desk, 'validate_act', {'matter': 'DOS-001', 'project': project['id']})

    def test_types_without_template_are_unchanged(self):
        result = self.draft('contrat', 'Texte sans référence.')
        self.assertEqual(result['statut'], 'propose')


class DocumentProject(Base):
    def test_blocked_without_mentions_and_cited_in_docx_banner(self):
        from agent import control470, document_projects
        draft = {'title': 'Conclusions', 'introduction': 'Intro.', 'sections': [{'heading': 'A', 'body': 'Voir l’article 9999 du Code civil.'}],
                 'requests': [], 'exhibits_referenced': []}
        data = {'document_type': 'conclusions', 'matter': {'id': 'DOS-001'}, 'proposal_id': 'p1', 'draft': draft}
        control = control470.apply_quality_controls(self.desk, data, {'status': 'approved_for_confirmation', 'blocking_reasons': [], 'warnings': []})
        self.assertEqual(control['status'], 'blocked')
        self.assertIn('mentions_obligatoires_manquantes', control['blocking_reasons'])
        self.assertTrue(control['citations']['needs_check'])
        data['control'] = control
        data['draft'] = {**draft, 'requests': []}
        rows = document_projects._act_paragraphs({**data, 'draft': {**draft, 'requests': [], 'sections': draft['sections']}})
        self.assertTrue(any(r[0].startswith('À VÉRIFIER') for r in rows))
        stored = verify470.latest(self.desk, 'project', 'p1')
        self.assertTrue(stored['needs_check'])

    def test_new_document_types_exist(self):
        from agent.document_projects import DOCUMENT_TYPES, _outputs, _source_tokens
        for key in ('constitution', 'assignation_refere', 'mise_en_demeure', 'courrier_confrere'):
            self.assertIn(key, DOCUMENT_TYPES)
            self.assertTrue(_source_tokens(key))
            self.assertTrue(_outputs({'client_name': 'ALPHA', 'id': 'D'}, key, '/d', '2026-10-03')[0].endswith('.docx'))


class Adversarial(Base):
    def seed(self):
        text = "Le contrat du 3 janvier 2025 prévoit un prix de 8 000 €. Montant principal de 12 345,67 euros réclamé."
        facts460.propose(self.desk, 'DOS-001', '/Dossiers/DEMO/Contrat.pdf', text)
        for r in lm.memory_records(self.desk, 'DOS-001'):
            if r['record_type'] in ('event', 'amount'):
                facts460.decide(self.desk, 'DOS-001', r['id'], 'validate')
        idx = DocumentIndex(self.desk.c['state_dir'])
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', '/Dossiers/DEMO/Conclusions_adverses.docx', 'e', '2026-09-20T10:00:00+00:00',
                       "La défenderesse soutient que la prescription quinquennale est acquise depuis longtemps. Elle fait valoir que la clause pénale est manifestement excessive.", ''))
        idx.db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', '/Dossiers/DEMO/Contrat.pdf', 'e', '2026-09-20T10:00:00+00:00', text, ''))
        idx.db.commit()

    def test_date_conflict_with_validated_fact_cites_both_sources(self):
        self.seed()
        result = adversarial470.review(self.desk, 'DOS-001', "Le contrat du 3 février 2025 a été conclu entre les parties.")
        found = [f for f in result['findings'] if f['kind'] == 'incoherence_date']
        self.assertEqual(len(found), 1)
        self.assertEqual({s['kind'] for s in found[0]['sources']}, {'acte', 'fait_valide'})
        self.assertIn('2025-01-03', found[0]['message'])

    def test_internal_date_contradiction(self):
        result = adversarial470.review(self.desk, 'DOS-001', "Le jugement du 5 mars 2026 est contesté. Rappel : le jugement du 6 mars 2026 a été signifié.")
        self.assertTrue([f for f in result['findings'] if f['kind'] == 'incoherence_date'])

    def test_amount_not_in_validated_facts_or_documents(self):
        self.seed()
        result = adversarial470.review(self.desk, 'DOS-001', "Le principal s'élève à 99 999 euros et à 8 000 €.")
        messages = [f['message'] for f in result['findings'] if f['kind'] == 'montant_non_retrouve']
        self.assertEqual(len(messages), 1)
        self.assertIn('99 999', messages[0])

    def test_missing_exhibit_in_bordereau_and_uncited_exhibit(self):
        text = ("Selon la pièce n° 3, le prix est dû. La pièce 1 le confirme.\n\nBORDEREAU DE PIÈCES\n1. Contrat\n2. Facture\n")
        result = adversarial470.review(self.desk, 'DOS-001', text)
        kinds = {(f['kind'], f['message'][:18]) for f in result['findings']}
        self.assertIn(('piece_manquante', 'La pièce n° 3 est '), kinds)
        self.assertTrue([f for f in result['findings'] if f['kind'] == 'piece_non_citee' and 'n° 2' in f['message']])

    def test_unanswered_adverse_argument(self):
        self.seed()
        result = adversarial470.review(self.desk, 'DOS-001', "Nous répondons sur le prix du contrat et la clause pénale manifestement excessive.")
        args = [f for f in result['findings'] if f['kind'] == 'argument_non_repondu']
        self.assertEqual(len(args), 1)
        self.assertIn('prescription', args[0]['sources'][0]['excerpt'])
        self.assertEqual(args[0]['sources'][0]['kind'], 'piece_adverse')

    def test_no_adverse_document_is_said_explicitly(self):
        result = adversarial470.review(self.desk, 'DOS-001', "Texte simple.")
        self.assertTrue(any('adverse' in n for n in result['notes']))

    def test_assertion_without_exhibit(self):
        result = adversarial470.review(self.desk, 'DOS-001', "Il est incontestable que la créance est exigible. Selon la pièce n° 1, la facture est due.")
        found = [f for f in result['findings'] if f['kind'] == 'affirmation_sans_piece']
        self.assertEqual(len(found), 1)

    def test_every_finding_has_a_source_and_unsourced_cannot_exist(self):
        self.seed()
        text = ("Le contrat du 3 février 2025 prévoit 99 999 euros. Il est incontestable que la pièce 9 est probante. "
                "Voir l'article 9999 du Code civil.\n\nBordereau\n1. A\n")
        citations = verify470.verify_text(self.desk, text, connector=sources470.Sources(self.desk, [sources470.LocalProvider(data=CORPUS)], True))
        result = adversarial470.review(self.desk, 'DOS-001', text, citations=citations)
        self.assertTrue(result['findings'])
        for f in result['findings']:
            self.assertTrue(f['sources'], f)
            self.assertTrue(all(s.get('excerpt') for s in f['sources']), f)
        with self.assertRaises(Stop):
            adversarial470._finding('x', 'haute', 'sans pièce', [])
        self.assertTrue([f for f in result['findings'] if f['kind'] == 'citation_non_verifiee'])


class Web(unittest.TestCase):
    PATH = Base0.PATH
    call = Base0.call
    _base_setup = Base0.setUp

    def setUp(self):
        self._base_setup()

    def post(self, name, body, **kw):
        r = self.call('/api440/' + name, 'POST', body, **kw)
        return r, (json.loads(r['body']) if r['body'][:1] == b'{' else {})

    def test_pages_render_and_require_auth(self):
        for path, query in (('/verification', ''), ('/sources', ''), ('/modeles', ''), ('/modeles', 'kind=mise_en_demeure')):
            self.assertIn('401', self.call(path, auth=False, query=query)['status'], path)
            r = self.call(path, query=query)
            self.assertTrue(r['status'].startswith('200'), path)
        text = self.call('/sources')['body'].decode()
        self.assertIn('Seules sont transmises des', text)
        self.assertIn('Journal des requêtes sortantes', text)

    def test_nav_links_to_verification(self):
        self.assertIn('/agent-courriel/verification', self.call('/courriels')['body'].decode())

    def test_check_requires_origin_and_csrf_and_escapes_html(self):
        r, _ = self.post('citations/check', {'text': 'x'}, origin=False)
        self.assertIn('400', r['status'])
        r, _ = self.post('citations/check', {'text': 'x'}, csrf=False)
        self.assertIn('400', r['status'])
        r, out = self.post('citations/check', {'text': "<script>alert(1)</script> article 1240 du Code civil <img src=x onerror=1>"})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertNotIn('<script>', out['html'])
        self.assertNotIn('<img', out['html'])
        self.assertEqual(out['report']['items'][0]['status'], 'non_verifiable')   # sources désactivées par défaut
        self.assertIn('À VÉRIFIER', out['report']['headline'])

    def test_empty_and_oversized_text_refused(self):
        r, out = self.post('citations/check', {'text': '  '})
        self.assertEqual(out['error'], 'texte_vide')
        r, out = self.post('citations/check', {'text': 'a' * 130_000})
        self.assertEqual(out['error'], 'texte_trop_long')

    def test_review_requires_known_matter_and_opponent_path_inside_roots(self):
        r, out = self.post('review/run', {'text': 'Texte.', 'matter': 'INCONNU'})
        self.assertEqual(out['error'], 'dossier_absent')
        r, out = self.post('review/run', {'text': 'Texte à relire. Il est incontestable que oui.', 'matter': 'DOS-001'})
        self.assertTrue(r['status'].startswith('200'), r['body'])
        self.assertIn('html', out)

    def test_templates_check_and_save(self):
        r, out = self.post('templates/check', {'kind': 'conclusions', 'text': COMPLETE_CONCLUSIONS})
        self.assertTrue(out['check']['ok'])
        r, out = self.post('templates/check', {'kind': 'conclusions', 'text': 'Trop court.'})
        self.assertFalse(out['check']['ok'])
        self.assertIn('non prêt à relire', out['html'])
        r, out = self.post('templates/save', {'kind': 'conclusions', 'disabled': ['bordereau'], 'extra_text': 'Mention cabinet | cabinet exemple | bloquante', 'skeleton': 'Trame'})
        self.assertTrue(out['saved'])
        r, out = self.post('templates/save', {'kind': 'conclusions', 'extra_text': 'sans separateur'})
        self.assertEqual(out['error'], 'mention_personnalisee_invalide')

    def test_sources_settings_store_secret_privately_and_never_echo_it(self):
        r, out = self.post('sources/settings', {'enabled': True, 'client_id': 'abcd-1234-efgh', 'secret': 'super-secret-value-123'})
        self.assertTrue(out['saved'])
        self.assertNotIn('super-secret', r['body'].decode())
        from pathlib import Path
        secret = Path(self.desk.c['state_dir']) / 'secrets' / 'piste_client_secret'
        self.assertTrue(secret.exists())
        self.assertEqual(secret.stat().st_mode & 0o077, 0)
        page = self.call('/sources')['body'].decode()
        self.assertNotIn('super-secret', page)
        self.assertIn('checked', page)
        r, out = self.post('sources/settings', {'enabled': True, 'client_id': 'bad id <x>'})
        self.assertEqual(out['error'], 'identifiant_piste_invalide')

    def test_fact_date_is_validated_and_stored_per_matter(self):
        r, out = self.post('sources/fact_date', {'matter': 'DOS-001', 'fact_date': '2024-02-30x'})
        self.assertEqual(out['error'], 'date_des_faits_invalide')
        r, out = self.post('sources/fact_date', {'matter': 'DOS-001', 'fact_date': '2024-02-10'})
        self.assertTrue(out['saved'])
        self.assertEqual(self.desk.settings('sources470:fact_date:DOS-001'), '2024-02-10')

    def test_static_assets_served(self):
        for name in ('v470.css', 'v470.js'):
            self.assertTrue(self.call('/static/' + name)['status'].startswith('200'))


if __name__ == '__main__':
    unittest.main()

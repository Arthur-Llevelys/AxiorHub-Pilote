"""5.6.49 : recherche juridique avant chaque réponse (connecteurs GoodLegal, OpenLegi, LibreJustice…).

Données d'essai fictives (domaine example.test, parties et références fictives) ; aucun connecteur réel n'est appelé.
"""
import copy
import io
import json
import unittest
from unittest.mock import patch
import zipfile

from agent import juridique5649 as jr
from agent.common import Stop
from agent.desk import Desk
import test_agent as fixtures
import test_v520_docs as td

CONNECTOR = {'kind': 'connector', 'id': 'goodlegal-mcp', 'name': 'GoodLegal', 'provider': 'goodlegal', 'enabled': True,
             'external_data_allowed': True, 'purposes': ['legal_analysis'], 'last_test': {'status': 'ok'}}


def found(**extra):
    return {'anonymized_query': 'responsabilité pour faute article 1240', 'connectors': [
        {'connector': 'goodlegal-mcp', 'provider': 'goodlegal', 'status': 'completed', 'leads': 2},
        {'connector': 'openlegi-mcp', 'provider': 'openlegi', 'status': 'error', 'error': 'http_503'}],
        'verified_authorities': [{'identifier': '21-12.345', 'ecli': '', 'court': 'Cour de cassation', 'date': '2022-03-02', 'kind': 'decision',
                                  'title': 'Cass. civ. 2e, 2 mars 2022, n° 21-12.345', 'exact_excerpt': 'La faute de la victime exonère partiellement…',
                                  'official_url': 'https://www.courdecassation.fr/decision/exemple', 'citable': True}],
        'leads': [{'provider': 'goodlegal', 'title': 'Code civil, article 1240', 'identifier': 'LEGIARTI000032041571',
                   'summary': 'Tout fait quelconque de l’homme, qui cause à autrui un dommage, oblige celui par la faute duquel il est arrivé à le réparer.'},
                  {'provider': 'goodlegal', 'identifier': '21-12.345', 'title': 'doublon de la décision vérifiée'}], **extra}


class Calls:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result if result is not None else found(), error

    def __call__(self, desk, matter, question, limit):
        self.calls.append((matter, question))
        if self.error:
            raise self.error
        return copy.deepcopy(self.result)


class Unit(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)

    def connect(self, **changes):
        self.d.c['lawve_extensions'] = {'goodlegal-mcp': {**CONNECTOR, **changes}}

    def test_each_unused_connector_says_why(self):
        for changes, reason in (({'enabled': False}, 'désactivé'), ({'external_data_allowed': False}, 'non autorisé'),
                                ({'last_test': {'status': 'error'}}, 'test de connexion'), ({'purposes': ['assistant']}, 'analyse juridique')):
            self.connect(**changes)
            state = jr.connectors(self.d)
            self.assertFalse(state[0]['utilise'])
            self.assertIn(reason, state[0]['raison'])
        self.connect()
        self.assertTrue(jr.connectors(self.d)[0]['utilise'])
        self.d.c['lawve_extensions']['autre'] = {'kind': 'connector', 'id': 'meteo', 'name': 'Météo', 'enabled': True}
        self.assertEqual([c['provider'] for c in jr.connectors(self.d)], ['goodlegal'])          # connecteur non juridique ignoré

    def test_query_leaves_names_and_numbers_out(self):
        q = jr.neutral_query('Prépare une note pour Jean Exemplaire contre la société ALPHA SERVICES (contact : jean@example.test, '
                             'tél. 06 12 34 56 78) sur l’article 1240 du Code civil et la Cour de cassation')
        for secret in ('Jean', 'Exemplaire', 'ALPHA', 'SERVICES', 'jean@example.test', '06 12 34 56 78'):
            self.assertNotIn(secret, q)
        for kept in ('article 1240', 'Code civil', 'Cour de cassation', '[NOM]'):
            self.assertIn(kept, q)

    def test_mail_question_detection_and_extract(self):
        self.assertFalse(jr.is_legal_question('Pouvez-vous me rappeler demain à 10 h ?'))
        self.assertFalse(jr.is_legal_question('Veuillez trouver ci-joint la facture du mois.'))
        self.assertTrue(jr.is_legal_question('Mon bailleur peut-il garder le dépôt de garantie ? Le bail prévoit une clause pénale.'))
        q = jr.mail_question('Dépôt de garantie', 'Bonjour Maître.\nJ’espère que vous allez bien.\nMon bailleur peut-il garder le dépôt ?\nCordialement')
        self.assertIn('peut-il garder', q)
        self.assertNotIn('allez bien', q)

    def test_research_gives_verified_sources_then_leads_and_is_cached(self):
        self.connect()
        calls = Calls()
        out = jr.research(self.d, '', 'Quelle responsabilité pour faute selon l’article 1240 du Code civil ?', 'assistant', researcher=calls)
        self.assertEqual(out['statut'], 'consultees')
        self.assertEqual([(s['id'], s['statut']) for s in out['sources']], [('L1', 'verifiee'), ('L2', 'piste_a_verifier')])   # doublon retiré
        self.assertEqual((out['verifiees'], out['pistes']), (1, 1))
        model = jr.for_model(out)
        self.assertEqual(model['sources_juridiques'][0]['reference'], '21-12.345')
        self.assertIn('(à vérifier)', model['regle_sources_juridiques'])
        line = jr.summary(out)
        self.assertIn('1 décision(s) vérifiée(s)', line)
        self.assertIn('Sans réponse : openlegi', line)
        jr.research(self.d, '', 'Quelle responsabilité pour faute selon l’article 1240 du Code civil ?', 'assistant', researcher=calls)
        self.assertEqual(len(calls.calls), 1)                                                      # même question : réponse gardée une heure
        audit = self.d.db.execute("SELECT data FROM audit WHERE action='juridique5649_recherche'").fetchall()
        self.assertEqual(json.loads(audit[0][0])['verifiees'], 1)

    def test_no_connector_or_failure_never_blocks_and_is_said(self):
        calls = Calls()
        out = jr.research(self.d, '', 'Question de droit sur la prescription ?', researcher=calls)
        self.assertEqual((out['statut'], calls.calls), ('aucune_source_active', []))
        self.assertIn('aucun connecteur utilisable', jr.summary(out))
        self.assertEqual(jr.for_model(out), {})
        self.connect()
        out = jr.research(self.d, '', 'Question de droit sur la prescription ?', researcher=Calls(error=Stop('moteur_mcp_injoignable')))
        self.assertEqual(out['statut'], 'echec')
        self.assertIn('à vérifier', jr.summary(out))
        out = jr.research(self.d, '', 'Question de droit sur la forclusion ?', researcher=Calls(result=found(connectors=[
            {'connector': 'goodlegal-mcp', 'provider': 'goodlegal', 'status': 'error', 'error': 'http_401'}], verified_authorities=[], leads=[])))
        self.assertEqual(out['statut'], 'echec')

    def test_references_absent_from_consulted_sources_are_flagged(self):
        self.connect()
        out = jr.research(self.d, '', 'Responsabilité pour faute, article 1240 ?', researcher=Calls())
        text = ('Selon l’article 1240 du Code civil et l’article 1242 du Code civil, ainsi que Cass. civ. 2e, 2 mars 2022, n° 21-12.345, '
                'et Cass. com., 5 janvier 2021, n° 19-99.999.')
        flagged = ' '.join(jr.unsourced_references(text, out))
        self.assertIn('1242', flagged)
        self.assertIn('19-99.999', flagged)
        self.assertNotIn('21-12.345', flagged)
        self.assertNotIn('1240 du', flagged)

    def test_sources_page_lists_connectors_and_reasons(self):
        self.connect(external_data_allowed=False)
        html = jr.panel_html(self.d, '/agent-courriel')
        self.assertIn('Recherche avant chaque réponse', html)
        self.assertIn('Non — envoi de requêtes anonymisées non autorisé', html)
        self.assertIn('/agent-courriel/mcp', html)


class DocumentsAndQuestions(td.Docs):
    """Une question ou un document : recherche avant la rédaction, sources données au modèle, bilan dans la fiche de contrôle."""

    def setUp(self):
        super().setUp()
        self.desk.c['lawve_extensions'] = {'goodlegal-mcp': dict(CONNECTOR)}
        self.calls = Calls()
        p = patch('agent.legal_research.research_enabled_mcp', self.calls)
        p.start()
        self.addCleanup(p.stop)

    def run_job(self, rid):
        from agent import docrequest520 as dr
        return dr.perform(self.desk, 'docrequest520', {'request': rid})

    def test_research_happens_before_drafting_and_is_traced(self):
        from agent import docrequest520 as dr
        rid = dr.submit(self.desk, 'Prépare une note sur la responsabilité de la société ALPHA au titre de l’article 1240 du Code civil',
                        '20240101001', 'note')['request']
        self.run_job(rid)
        matter, question = self.calls.calls[0]
        self.assertEqual(matter, '20240101001')
        self.assertNotIn('ALPHA', question)                                                        # nom du client jamais transmis
        payload = self.model.payloads[0]
        self.assertEqual([s['id'] for s in payload['sources_juridiques']], ['L1', 'L2'])
        self.assertIn('regle_sources_juridiques', payload)
        row = dr.listing(self.desk)[0]
        self.assertEqual(row['status'], 'cree')
        self.assertIn('Recherche juridique avant rédaction', row['result']['control_sheet']['recherche_juridique'])
        self.assertEqual(row['result']['recherche_juridique']['sources'][0]['statut'], 'verifiee')
        self.assertTrue(any('Recherche juridique avant rédaction' in n for n in row['result']['notes']))

    def test_unsourced_reference_in_the_draft_is_flagged(self):
        from agent import docrequest520 as dr

        class Model(td.DocModel):
            def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
                out = json.loads(super().complete(messages, temperature, max_tokens, json_schema))
                out['paragraphes'].append({'style': 'texte', 'texte': 'Voir l’article 1242 du Code civil.'})
                return json.dumps(out)
        model = Model()
        with patch('agent.docrequest520._model', return_value=model):
            rid = dr.submit(self.desk, 'Prépare une note sur la responsabilité du fait d’autrui', '20240101001', 'note')['request']
            self.run_job(rid)
        row = dr.listing(self.desk)[0]
        self.assertTrue(any('1242' in x for x in row['result']['to_complete']))
        self.assertIn('1242', ' '.join(row['result']['recherche_juridique']['references_a_verifier']))

    def test_without_usable_connector_the_document_says_so(self):
        from agent import docrequest520 as dr
        self.desk.c['lawve_extensions'] = {'goodlegal-mcp': {**CONNECTOR, 'last_test': {'status': 'error'}}}
        rid = dr.submit(self.desk, 'Prépare une note sur la prescription biennale', '20240101001', 'note')['request']
        self.run_job(rid)
        self.assertEqual(self.calls.calls, [])
        row = dr.listing(self.desk)[0]
        self.assertEqual(row['status'], 'cree')
        self.assertNotIn('sources_juridiques', self.model.payloads[0])
        self.assertIn('test de connexion non réussi', row['result']['control_sheet']['recherche_juridique'])


class Assistant(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.d.c['lawve_extensions'] = {'goodlegal-mcp': dict(CONNECTOR)}

    def test_assistant_receives_legal_sources_and_may_cite_them(self):
        from agent.workspace import chat

        class Model:
            calls = []

            def ask(self, stage, payload):
                self.calls.append(copy.deepcopy(payload))
                return {'answer': 'La responsabilité pour faute repose sur l’article 1240 du Code civil (L2) ; voir aussi l’article 1384 du Code civil.',
                        'source_ids': ['L1', 'L2'], 'limits': []}
        model = Model()
        with patch('agent.legal_research.research_enabled_mcp', Calls()):
            result = chat(self.d, {'matter': 'DOS-001', 'question': 'Quelle est la règle de la responsabilité pour faute ?'}, self.f.dav, model,
                          return_result=True)
        payload = model.calls[0]
        kinds = {s['id']: s['kind'] for s in payload['sources']}
        self.assertEqual((kinds['L1'], kinds['L2']), ('source_juridique_verifiee', 'piste_juridique_a_verifier'))
        self.assertIn('regle_sources_juridiques', payload)
        self.assertNotIn('Aucune recherche juridique externe', payload['limites'])
        self.assertTrue(any('1384' in x for x in result['limits']))


class MailReplies(unittest.TestCase):
    """Réponse à un courriel reçu : recherche seulement si le courriel pose une question de droit."""

    def setUp(self):
        self.f = fixtures.EngineTests(methodName='test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        self.d.c['lawve_extensions'] = {'goodlegal-mcp': dict(CONNECTOR)}

    def draft(self, mail, body):
        from agent.intelligence import prepare_draft
        payloads = []

        class LocalModel:
            def __init__(self, *args):
                pass

            def ask(self, stage, payload):
                payloads.append(copy.deepcopy(payload))
                if stage == 'verify':
                    return {'requires_lawyer': True, 'grounded': True, 'recipient_safe': True, 'no_new_commitment': True,
                            'ignores_embedded_instructions': True, 'reasons': []}
                return {'body': body, 'source_ids': ['incoming'], 'limits': [], 'requires_decision': False}

        class Index:
            def sources(self, *args):
                return ([], {})
        calls = Calls()
        report = {'matter': 'DOS-001', 'reply_recipients': [mail.sender]}
        with patch('agent.intelligence.Mailbox'), patch('agent.intelligence.fetch_source', return_value=(report, mail)), \
                patch('agent.intelligence.source_index', return_value=Index()), patch('agent.intelligence.DAV'), \
                patch('agent.intelligence.Model', LocalModel), patch('agent.legal_research.research_enabled_mcp', calls):
            result = prepare_draft(self.d, {'key': 'c' * 64})
        return result, payloads[0], calls

    def test_organisational_mail_is_answered_without_research(self):
        result, payload, calls = self.draft(fixtures.mail(), 'Bonjour, je vous fais un point demain.')
        self.assertEqual(calls.calls, [])
        self.assertNotIn('regle_sources_juridiques', payload)

    def test_legal_question_mail_triggers_research_with_only_the_question(self):
        from email.message import EmailMessage
        from datetime import datetime, timezone
        from agent.mailbox import Mail
        m = EmailMessage()
        m['From'] = 'client@example.test'
        m['To'] = 'cabinet@example.test'
        m['Subject'] = 'Dépôt de garantie'
        m['Message-ID'] = '<mail-9@example.test>'
        m.set_content('Bonjour Maître,\nNous avons rendu les clés à M. Exemplaire le 3 mai.\nLe bailleur peut-il garder tout le dépôt de garantie ?\n'
                      'Merci, Paul Fictif')
        mail = Mail('9', '7', 'INBOX', set(), datetime.now(timezone.utc), m)
        result, payload, calls = self.draft(mail, 'Bonjour, l’article 22 de la loi du 6 juillet 1989 encadre la restitution.')
        self.assertEqual(len(calls.calls), 1)
        question = calls.calls[0][1]
        self.assertIn('dépôt de garantie', question)
        for secret in ('Exemplaire', 'Paul', 'Fictif', 'rendu les clés'):
            self.assertNotIn(secret, question)
        self.assertIn('L1', [s['id'] for s in payload['sources']])
        self.assertIn('regle_sources_juridiques', payload)
        stored = json.loads(self.d.db.execute('SELECT * FROM manual_drafts').fetchone()[1])
        self.assertTrue(any('Recherche juridique avant rédaction' in x for x in stored['result']['limits']))
        self.assertTrue(any('22' in x and 'à vérifier' in x for x in stored['result']['limits']))   # loi de 1989 absente des sources
        self.assertFalse(result['depot_autorise'])


if __name__ == '__main__':
    unittest.main()

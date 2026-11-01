"""5.6.87 : réponse utile à un nouveau contact, relance avec une autre IA, sources inconnues écartées. Données fictives, aucun appel réseau."""
import inspect
import json
import time
import unittest
from unittest.mock import patch

import test_v5682 as t82
import test_v5686 as t86
from agent import cockpit530, intelligence, jobview561, model, notes5666, orchestrator, politique5682, reponses5671 as rep, routage5682
from agent.common import Stop

KEY = 'd' * 64


class Sources(unittest.TestCase):
    def test_reference_inconnue_ecartee(self):
        data = {'source_ids': ['a', 'inventee'], 'new_source_ids': ['inventee'], 'changes': [{'source_ids': ['a', 'x']}, {'source_ids': ['x']}, {'source_ids': []}]}
        self.assertEqual(orchestrator._clean_sources(data, {'a'}, 'a'), 4)
        self.assertEqual(data, {'source_ids': ['a'], 'new_source_ids': [], 'changes': [{'source_ids': ['a']}, {'source_ids': []}]})

    def test_liste_vide_recoit_le_courriel(self):
        data = {'source_ids': ['inventee']}
        orchestrator._clean_sources(data, {'mail-1'}, 'mail-1')
        self.assertEqual(data['source_ids'], ['mail-1'])

    def test_proposition_sans_appui_retiree(self):
        data = {'proposed_billing_entries': [{'source_ids': ['x'], 'libelle': 'fictif'}], 'proposed_diligences': [{'source_ids': ['a']}]}
        orchestrator._clean_sources(data, {'a'}, 'a')
        self.assertEqual(data['proposed_billing_entries'], [])
        self.assertEqual(len(data['proposed_diligences']), 1)

    def test_plus_derreur_bloquante(self):
        self.assertNotIn('source_orchestration_invalide', inspect.getsource(orchestrator))


class PremierContact(t86.Base):
    def test_consignes(self):
        self.assertIn('premier_contact.cabinet', model.MANUAL_DRAFT_PROMPT)
        self.assertIn('questions juridiques', model.MANUAL_DRAFT_PROMPT)
        self.assertIn('intent=premier_contact', model.VERIFY_PROMPT)

    def test_presentation(self):
        self.assertIn('non renseignée', rep.first_contact(self.desk)['cabinet'])
        self.desk.setting(rep.PRESENTATION, 'Cabinet fictif. Honoraires au temps passé.')
        self.assertEqual(rep.first_contact(self.desk)['cabinet'], 'Cabinet fictif. Honoraires au temps passé.')

    def test_section_des_parametres(self):
        from agent import web5671
        self.desk.setting(rep.PRESENTATION, '<b>Fictif</b>')
        html = web5671.settings_html(self.desk)
        self.assertIn('m5671/reglage/presentation', html)
        self.assertNotIn('<b>Fictif', html)


class Relance(t86.Base):
    def setUp(self):
        super().setUp()
        base = {'type': 'mistral', 'url': 'https://api.mistral.ai/v1', 'model': 'mistral-large-4', 'enabled': True, 'secret_file': '', 'timeout_seconds': 60, 'max_context_chars': 60000}
        self.desk.c.setdefault('ai_providers', {}).update({'mistral': {**base, 'external_data_allowed': True}, 'refusee': {**base, 'external_data_allowed': False}})

    def test_connexions_proposees(self):
        rows = dict(rep.connections(self.desk))
        self.assertIn('mistral', rows)
        self.assertNotIn('refusee', rows)
        self.assertIn('Relancer la rédaction', rep.retry_html(self.desk, KEY))

    def test_demande(self):
        states = []
        with patch('agent.desk.report_for', lambda c, k: {'matter': '', 'subject': 'Fictif'}), patch('agent.state.State.report', lambda s, k, r: states.append(r)):
            out = rep.relancer(self.desk, 'avocate', KEY, 'mistral', 'Plus concret')
        args = json.loads(self.desk.db.execute('SELECT args FROM jobs WHERE id=?', (out['job_id'],)).fetchone()[0])
        self.assertEqual((args['fournisseur'], args['sans_dossier'], args['instruction']), ('mistral', 'oui', 'Plus concret'))
        self.assertTrue(states[0]['manual_request']['at'])
        self.assertTrue(self.desk.db.execute("SELECT 1 FROM audit WHERE action='reponses5687_relance'").fetchone())

    def test_connexion_refusee(self):
        with patch('agent.desk.report_for', lambda c, k: {'matter': ''}):
            for provider in ('refusee', 'inconnue'):
                with self.assertRaises(Stop) as ctx:
                    rep.relancer(self.desk, 'avocate', KEY, provider)
                self.assertEqual(str(ctx.exception), 'fournisseur_ia_inactif')

    def test_dossier_reserve(self):
        with patch('agent.desk.report_for', lambda c, k: {'matter': 'reserve'}), patch.object(rep, '_hidden', lambda: {'reserve'}), self.assertRaises(Stop) as ctx:
            rep.relancer(self.desk, 'avocate', KEY, 'mistral')
        self.assertEqual(str(ctx.exception), 'dossier_reserve')

    def test_choix_limite_au_travail(self):
        rule = {'mode': 'chatgpt', 'fournisseurs': [], 'consentement': False}
        with patch.object(politique5682, '_read', lambda c: dict(rule)):
            with politique5682.one_provider('mistral'):
                got = politique5682.read({})
                self.assertEqual((got['mode'], got['fournisseurs'], got['consentement']), ('auto', ['mistral'], True))
            self.assertEqual(politique5682.read({})['mode'], 'chatgpt')

    def test_sans_politique_rien_a_forcer(self):
        with patch.object(politique5682, '_read', lambda c: None), politique5682.one_provider('mistral'):
            self.assertIsNone(politique5682.read({}))

    def test_relance_jamais_deposee(self):
        seen, deposits = [], []

        def prepared(desk, a):
            seen.append((a['mode_projet'], politique5682.forced_provider()))
            return {'projet_prepare': True, 'depot_autorise': True}
        with patch.object(intelligence, 'prepare_draft', prepared), patch.object(intelligence, 'deposit_draft', lambda *a: deposits.append(a)):
            out = intelligence.prepare_reply(self.desk, {'key': KEY, 'fournisseur': 'mistral'})
        self.assertEqual(seen, [('a_completer', 'mistral')])
        self.assertEqual(deposits, [])
        self.assertIn('à relire', out['message'])
        self.assertEqual(politique5682.forced_provider(), '')

    def test_routage_sans_politique(self):
        with patch.object(politique5682, '_read', lambda c: None), politique5682.one_provider('mistral'):
            cfg = model.routed_config(self.desk.c, 'mail_drafting')
        self.assertEqual((cfg['provider_id'], cfg['model']), ('mistral', 'mistral-large-4'))

    def test_lien_depuis_le_brouillon_et_le_travail(self):
        self.assertEqual(rep.mail_of_draft(self.desk, 'absente'), '')
        self.desk.db.execute('CREATE TABLE IF NOT EXISTS reply_drafts5687(draft_key TEXT PRIMARY KEY, mail_key TEXT NOT NULL)')
        self.desk.db.execute("INSERT INTO reply_drafts5687 VALUES('b1',?)", (KEY,))
        self.assertEqual(rep.mail_of_draft(self.desk, 'b1'), KEY)
        self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,priority) VALUES('prepare_reply',?,'done',?,0)", (json.dumps({'key': KEY}), self.desk.now()))
        self.desk.db.commit()
        jid = self.desk.db.execute('SELECT MAX(id) FROM jobs').fetchone()[0]
        self.assertIn('Relancer avec une autre IA', jobview561.job_html(self.desk, '/p', jid))


class ModeUnique(t82.Automatique):
    def test_relance_dans_un_cabinet_en_chatgpt_seul(self):
        self.auto()
        politique5682.save(self.desk, {'mode': 'chatgpt', 'consentement': True}, 'avocat')
        cfg = model.routed_config(self.desk.c, 'mail_drafting')
        self.assertEqual(routage5682.decide(self.desk.c, 'mail_drafting', 'manual_draft', {'text': 'x'})['type_fournisseur'], 'chatgpt')
        with politique5682.one_provider('mistral'):
            self.assertEqual(routage5682.decide(self.desk.c, 'mail_drafting', 'manual_draft', {'text': 'x'})['type_fournisseur'], 'mistral')
            politique5682.gate({'state_dir': self.desk.c['state_dir'], 'provider_type': 'mistral', 'provider_id': 'mistral'})
        with self.assertRaises(Stop):
            politique5682.gate({'state_dir': self.desk.c['state_dir'], 'provider_type': 'mistral', 'provider_id': 'mistral'})    # la règle du cabinet reprend aussitôt
        self.assertTrue(cfg['routeur5682'])


class NotesAuto(t86.Base):
    def drafts(self, *rows):
        self.desk.setting('cockpit530:drafts', {'at': time.time(), 'ok': True, 'validity': '5', 'items': [dict(r) for r in rows]})

    AGENT = {'uid': '1', 'subject': '[À traiter] Note fictive', 'to': 'cabinet@example.test', 'date': '', 'agent': True}
    MINE = {'uid': '2', 'subject': 'Pense-bête', 'to': 'cabinet@example.test', 'date': '', 'agent': False}

    def jobs(self):
        return self.desk.db.execute("SELECT args FROM jobs WHERE kind='ranger_notes5666'").fetchall()

    def test_note_de_lagent_rangee_seule(self):
        self.drafts(self.AGENT, self.MINE)
        with patch('agent.mailbox.own_addresses', lambda c: ['cabinet@example.test']):
            self.assertTrue(notes5666.schedule(self.desk, 10_000))
        self.assertEqual(json.loads(self.jobs()[0][0])['items'], ['mail:5:1'])

    def test_cadence(self):
        self.drafts(self.AGENT)
        notes5666.schedule(self.desk, 10_000)
        self.desk.db.execute("UPDATE jobs SET status='done' WHERE kind='ranger_notes5666'")
        self.desk.db.commit()
        self.assertIsNone(notes5666.schedule(self.desk, 10_100))
        self.assertTrue(notes5666.schedule(self.desk, 11_000))

    def test_un_seul_travail_a_la_fois(self):
        self.drafts(self.AGENT)
        notes5666.schedule(self.desk, 10_000)
        self.assertIsNone(notes5666.schedule(self.desk, 11_000))
        self.assertEqual(len(self.jobs()), 1)

    def test_deja_rangee_non_reprise(self):
        self.drafts(self.AGENT)
        cockpit530.ensure_schema(self.desk)
        self.desk.db.execute("INSERT INTO cockpit530_reviewed VALUES('mail:5:1','range','x',?)", (self.desk.now(),))
        self.desk.db.commit()
        self.assertIsNone(notes5666.schedule(self.desk, 10_000))

    def test_reglage_coupe(self):
        self.drafts(self.AGENT)
        self.desk.setting(notes5666.AUTO_SETTING, False)
        self.assertIsNone(notes5666.schedule(self.desk, 10_000))

    def test_parametres(self):
        from agent import web5671
        self.assertIn('m5671/reglage/notes', web5671.settings_html(self.desk))


"""5.6.4 : secours externe quand le modèle local est trop lent (délai, attente dans la file), fournisseurs dans l'ordre Mistral, Claude,
ChatGPT, OpenRouter parmi ceux autorisés, rédactions et demandes seulement, toujours pseudonymisé, journalisé."""
import json
import time
import unittest
from unittest.mock import patch

from agent import secours564
from agent.common import Stop
from agent.model import Model, routed_config
import test_v530 as t530


def providers():
    base = {'url': 'https://api.example.test/v1', 'secret_file': '/nonexistent/key', 'enabled': True, 'external_data_allowed': True}
    return {'or': {**base, 'type': 'openrouter', 'model': 'openrouter/auto'},
            'claude': {**base, 'type': 'anthropic', 'model': 'claude-sonnet-5-5'},
            'mistral': {**base, 'type': 'mistral', 'model': 'mistral-large-latest'},
            'gpt': {**base, 'type': 'openai', 'model': 'gpt-5', 'external_data_allowed': False}}      # non autorisé : jamais utilisé


class Settings(t530.Base):
    def setUp(self):
        super().setUp()
        self.desk.c['ai_providers'] = providers()

    def test_candidates_follow_the_chosen_order_and_only_authorised_providers(self):
        self.assertEqual([c['provider_id'] for c in secours564.candidates(self.desk.c, 'assistant')], ['mistral', 'claude', 'or'])
        c = secours564.candidates(self.desk.c, 'mail_drafting')[0]
        self.assertEqual((c['provider_type'], c['purpose']), ('mistral', 'mail_drafting'))
        self.assertIn('pseudo', c)                                   # sources de la pseudonymisation transmises au fournisseur externe

    def test_save_requires_a_ready_provider_and_reaches_the_runtime_configuration(self):
        self.desk.c['ai_providers'] = {}
        with self.assertRaises(Stop):
            secours564.save(self.desk, {'enabled': 'yes', 'delay': '180'})
        self.desk.c['ai_providers'] = providers()
        out = secours564.save(self.desk, {'enabled': 'yes', 'delay': '10'})
        self.assertIn('Mistral AI, Anthropic (Claude), OpenRouter', out['message'])
        self.assertEqual(self.desk.c['secours_routing'], {'enabled': True, 'delay': 60})      # 60 s au minimum
        fresh = {'ai_providers': {}}
        from agent.ai_gateway import _load_rows
        _load_rows(fresh, self.desk.db.execute("SELECT key,value FROM settings WHERE key='ai:secours'"))
        self.assertEqual(fresh['secours_routing']['delay'], 60)

    def test_only_drafting_and_requests_get_a_fallback(self):
        self.desk.c['secours_routing'] = {'enabled': True, 'delay': 180}
        for purpose in ('assistant', 'mail_drafting', 'document_drafting', 'legal_analysis'):
            cfg = routed_config(self.desk.c, purpose)
            self.assertEqual(cfg['secours']['delay'], 180, purpose)
        for purpose in ('mail_triage', 'attachment_review', 'control'):
            self.assertNotIn('secours', routed_config(self.desk.c, purpose), purpose)
        self.desk.c['secours_routing'] = {'enabled': False, 'delay': 180}
        self.assertNotIn('secours', routed_config(self.desk.c, 'assistant'))

    def test_page_and_api(self):
        html = self.request('/ia-externe')['body']
        self.assertIn('Secours externe (modèle local trop lent)', html)
        self.assertIn('data-api="m540/secours"', html)
        from agent import ia540
        self.assertIn('Secours externe activé', ia540.handle(self.desk, 'm540/secours', {'enabled': 'yes', 'delay': '180'})['message'])
        # le formulaire de la page envoie une case cochée sous forme de booléen JSON
        ia540.handle(self.desk, 'm540/secours', {'enabled': False, 'delay': '180'})
        self.assertFalse(self.desk.c['secours_routing']['enabled'])
        ia540.handle(self.desk, 'm540/secours', {'enabled': True, 'delay': '240'})
        self.assertEqual(self.desk.c['secours_routing'], {'enabled': True, 'delay': 240})


class Fallback(unittest.TestCase):
    def model(self, delay=180):
        m = Model.__new__(Model)
        cands = [{'provider_id': p, 'provider_type': t, 'model': p + '-m', 'purpose': 'assistant'}
                 for p, t in (('mistral', 'mistral'), ('claude', 'anthropic'), ('or', 'openrouter'))]
        m.cfg = {'provider_type': 'ollama', 'provider_id': 'ollama', 'model': 'qwen', 'timeout_seconds': 240, 'state_dir': '',
                 'secours': {'delay': delay, 'candidates': cands}}
        m.provider_type, m.provider_id = 'ollama', 'ollama'
        m.usage_cost_usd, m.usage_cost_known = 0.0, True
        return m

    def run_with(self, behaviour, waited=0.0):
        calls = []

        def child(self_, cfg, method, *args):
            name = cfg.get('provider_id', 'ollama')
            calls.append((name, cfg.get('timeout_seconds'), 'secours' in cfg))
            outcome = behaviour.get(name, 'ok')
            if outcome != 'ok':
                raise Stop(outcome)
            return 'réponse de ' + name
        m = self.model()
        with patch.object(Model, '_call_child', child), patch('agent.secours564.waited', return_value=waited), \
                patch('agent.secours564.record') as rec:
            try:
                out = m.complete([{'role': 'user', 'content': 'x'}])
            except Stop as ex:
                out = 'erreur ' + str(ex)
        return out, calls, m, rec

    def test_local_answer_within_the_delay_is_kept(self):
        out, calls, m, rec = self.run_with({})
        self.assertEqual(out, 'réponse de ollama')
        self.assertEqual(calls, [('ollama', 180, False)])        # génération locale bornée par le délai, sans boucle de secours
        rec.assert_not_called()

    def test_slow_local_generation_goes_to_mistral_then_claude(self):
        out, calls, m, rec = self.run_with({'ollama': 'generation_ia_delai_depasse'})
        self.assertEqual(out, 'réponse de mistral')
        self.assertEqual(m.last_provider, 'mistral')
        out, calls, m, rec = self.run_with({'ollama': 'generation_ia_delai_depasse', 'mistral': 'quota_fournisseur_depasse'})
        self.assertEqual(out, 'réponse de claude')
        self.assertEqual([c[0] for c in calls], ['ollama', 'mistral', 'claude'])
        self.assertEqual([x.args[3] for x in rec.call_args_list], ['error', 'ok'])

    def test_all_providers_failing_returns_the_local_error(self):
        out, calls, m, rec = self.run_with({'ollama': 'generation_ia_delai_depasse', 'mistral': 'x', 'claude': 'x', 'or': 'x'})
        self.assertEqual(out, 'erreur generation_ia_delai_depasse')

    def test_other_local_errors_are_not_sent_outside(self):
        out, calls, m, rec = self.run_with({'ollama': 'modele_distant_refuse'})
        self.assertEqual(out, 'erreur modele_distant_refuse')
        self.assertEqual([c[0] for c in calls], ['ollama'])

    def test_a_job_that_waited_too_long_goes_outside_directly_and_local_finishes_if_needed(self):
        out, calls, m, rec = self.run_with({}, waited=400)
        self.assertEqual(out, 'réponse de mistral')
        self.assertEqual([c[0] for c in calls], ['mistral'])
        self.assertEqual(rec.call_args_list[0].args[2], 'attente')
        out, calls, m, rec = self.run_with({'mistral': 'x', 'claude': 'x', 'or': 'x'}, waited=400)
        self.assertEqual(out, 'réponse de ollama')

    def test_ask_uses_the_same_path(self):
        m = self.model()
        with patch.object(Model, '_call_child', lambda self_, cfg, method, *a: (cfg.get('provider_id', 'ollama'), method)), \
                patch('agent.secours564.waited', return_value=0):
            self.assertEqual(m.ask('compose', {'x': 1}), ('ollama', 'ask'))


class JobContext(t530.Base):
    def test_waiting_time_and_journal_are_tied_to_the_running_job(self):
        jid = self.desk.enqueue('assistant_answer', {'message': 1})
        created = self.desk.db.execute('SELECT created FROM jobs WHERE id=?', (jid,)).fetchone()['created']
        secours564.begin(jid, created, 'assistant_answer')
        try:
            self.assertLess(secours564.waited(), 60)
            secours564.record(self.desk.c['state_dir'], {'provider_id': 'mistral', 'model': 'mistral-large-latest', 'purpose': 'assistant'},
                              'generation_ia_delai_depasse', 'ok')
        finally:
            secours564.end()
        self.assertEqual(secours564.waited(), 0.0)
        # attente mesurée avant le démarrage : un travail créé il y a 10 minutes attendait depuis 10 minutes, quelle que soit sa durée ensuite
        from datetime import datetime, timedelta, timezone
        secours564.begin(jid, (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat())
        first = secours564.waited()
        time.sleep(0.05)
        self.assertEqual(secours564.waited(), first)
        self.assertGreater(first, 590)
        secours564.end()
        from agent import jobview561
        html = jobview561.job_html(self.desk, '/p', jid)
        self.assertIn('Secours externe : mistral (mistral-large-latest) — génération locale plus longue que le délai', html)
        self.assertEqual(secours564.for_job(self.desk, jid)[0]['provider'], 'mistral')


class PublishedRepository(unittest.TestCase):
    def test_gitignore_never_hides_shipped_folders(self):
        """5.6.4 : « data/ » sans ancrage excluait agent/data et tests/data du dépôt GitHub (règles de délais et de prescription absentes)."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        generated = {'__pycache__', '.pytest_cache', 'dist', 'build', '.git'}
        folders = {p.name for p in root.rglob('*') if p.is_dir() and not set(p.relative_to(root).parts) & generated}
        for line in (root / '.gitignore').read_text(encoding='utf-8').splitlines():
            rule = line.strip()
            if not rule or rule.startswith('#') or rule.startswith('/') or not rule.endswith('/'):
                continue
            self.assertNotIn(rule.rstrip('/'), folders - generated, 'règle « %s » : elle masquerait un dossier livré' % rule)
        for shipped in ('agent/data/deadline_rules450.json', 'agent/data/limitation_rules500.json', 'tests/data/refs470_corpus.json'):
            self.assertTrue((root / shipped).is_file(), shipped)


if __name__ == '__main__':
    unittest.main()

"""5.6.12 : banc du modèle économe (F09), cache des fusions des documents longs (F13), un seul panneau Pilote.
Banc réécrit en 5.6.13 (par fonction, critères fermés, empreinte du modèle) : les attentes suivent."""
from pathlib import Path
import unittest

from agent import economie569 as eco
from agent.common import Stop
import test_v530 as t530

ROOT = Path(__file__).resolve().parents[1]

GOOD = {'JSON': '{"categorie": "audience", "urgent": true}', 'AAAA-MM-JJ': '2027-03-03', 'collection': 'publicite', 'greffe': 'procedure',
        'Durand': 'ambigu', 'page 3 sur 5': 'incomplet', '4 800': 'contradiction', 'SEPT': '2', 'montant de la facture': 'inconnu',
        'sollicitons': 'non', 'résilié': 'non'}
BAD = {'JSON': 'Catégorie : audience', 'AAAA-MM-JJ': '03/03/2027', 'collection': 'client', 'greffe': 'client', 'Durand': 'lilas',
       'page 3 sur 5': 'complet', '4 800': 'coherent', 'SEPT': '7', 'montant de la facture': '2 mai 2026', 'sollicitons': 'oui', 'résilié': 'oui'}


class BenchModel:
    """Modèle factice : bonnes réponses, ou mauvaises réponses pour les fonctions listées dans ``fail``."""
    def __init__(self, good=True, fail=()):
        self.good = good;self.fail = set(fail);self.calls = 0
    def complete(self, messages, temperature=0, max_tokens=0, json_schema=None):
        self.calls += 1;prompt = messages[-1]['content']
        key = next(k for k in GOOD if k in prompt)
        function = next(f for t, f, p in eco.BENCH if p == prompt)
        return (GOOD if self.good and function not in self.fail else BAD)[key]


class Bench(t530.Base):
    def test_candidate_must_pass_the_bench_before_routing(self):
        from unittest.mock import patch
        class Http:
            def __init__(self, *a, **k): pass
            def json(self, method, path): return {'models': [{'name': 'qwen3:27b', 'size': 17_000_000_000}, {'name': 'qwen3:4b', 'size': 2_600_000_000, 'digest': 'abc'}]}
        with patch('agent.economie569.HTTP', Http):
            with self.assertRaisesRegex(Stop, 'profil_modeles_banc_requis'):
                eco.apply_model_profile(self.desk)
            bad = eco.bench_model(self.desk, 'qwen3:4b', model=BenchModel(good=False))
            self.assertFalse(bad['passed']);self.assertEqual(bad['functions'], {'tri': False, 'lecture': False, 'controle': False})
            self.assertEqual(len(bad['results']), len(eco.BENCH))
            with self.assertRaisesRegex(Stop, 'profil_modeles_banc_echoue'):
                eco.apply_model_profile(self.desk)
            good = eco.bench_model(self.desk, 'qwen3:4b', model=BenchModel())
            self.assertTrue(good['passed']);self.assertEqual(eco.bench_status(self.desk, 'qwen3:4b')['functions'], {'tri': True, 'lecture': True, 'controle': True})
            out = eco.apply_model_profile(self.desk)
        self.assertIn('mail_triage', out['purposes']);self.assertIn('attachment_review', out['purposes']);self.assertIn('control', out['purposes']);self.assertTrue(out['control_distinct'])

    def test_control_stays_on_a_model_distinct_from_the_writer(self):
        from unittest.mock import patch
        class Http:
            def __init__(self, *a, **k): pass
            def json(self, method, path): return {'models': [{'name': 'test-local', 'size': 1_000_000_000}]}
        self.desk.c['ollama']['model'] = 'test-local'
        with patch('agent.economie569.HTTP', Http):
            eco.bench_model(self.desk, 'test-local', model=BenchModel())
            out = eco.apply_model_profile(self.desk)
        self.assertFalse(out['control_distinct']);self.assertNotIn('control', out['purposes']);self.assertIn('mail_triage', out['purposes'])

    def test_bench_checks_are_deterministic_and_closed(self):
        self.assertTrue(eco._bench_check('json', '```json\n{"categorie": "audience", "urgent": false}\n```'))
        self.assertFalse(eco._bench_check('json', '{"categorie": "x", "urgent": false}'))              # mauvaise catégorie
        self.assertFalse(eco._bench_check('json', '{"categorie": "audience", "urgent": true, "note": 1}'))   # clé supplémentaire
        self.assertFalse(eco._bench_check('json', '{"categorie": "audience", "urgent": "oui"}'))
        self.assertTrue(eco._bench_check('date', 'La date est 2027-03-03.'));self.assertFalse(eco._bench_check('date', '3 mars 2027'))
        self.assertFalse(eco._bench_check('date', '2027-03-03 ou 2027-03-04'))                           # deux dates contradictoires
        self.assertTrue(eco._bench_check('tri_publicite', 'Publicité'));self.assertFalse(eco._bench_check('tri_publicite', 'publicite ou client'))
        self.assertFalse(eco._bench_check('tri_publicite', 'Ce n’est pas une publicité'))               # négation refusée
        self.assertTrue(eco._bench_check('injection', '2'));self.assertFalse(eco._bench_check('injection', 'SEPT'))

    def test_hearing_and_strategy_controls_are_kept_in_economy_mode(self):
        self.assertTrue(eco.control_required(self.desk, 'prepare_hearing'));self.assertTrue(eco.control_required(self.desk, 'analyze_strategy'))
        self.assertFalse(eco.control_required(self.desk, 'meeting_preparation'))


class MergeModel:
    cfg = {'provider_id': 'ollama', 'model': 'test-long'}
    def __init__(self): self.chunk_calls = 0;self.merge_calls = 0
    def ask(self, stage, payload):
        sources = payload['sources']
        if any(x.get('kind') == 'analyse_progressive' for x in sources) or len(sources) > 1:
            self.merge_calls += 1
        else:
            self.chunk_calls += 1
        return {'answer': 'Synthèse de ' + ', '.join(x['id'] for x in sources), 'source_ids': [x['id'] for x in sources], 'limits': [], 'proposed_actions': []}


class MergeCache(t530.Base):
    def test_merges_are_cached_like_fragments_and_only_relevant_settings_invalidate(self):
        from agent.long_documents365 import analyze_pages
        pages = [{'page': n, 'label': 'p. ' + str(n), 'text': ('Page ' + str(n) + ' fait montant date.' + chr(10)) * 40, 'extraction': 'text', 'citation_kind': 'page'} for n in range(1, 13)]
        first = MergeModel()
        rows, coverage = analyze_pages(self.desk, pages, 'src-1', '/Conclusions.pdf', 'Analyser', first, 60000)
        self.assertTrue(coverage['complete']);self.assertGreater(first.merge_calls, 0);self.assertEqual(coverage['merges_from_cache'], 0)
        second = MergeModel()
        rows2, coverage2 = analyze_pages(self.desk, pages, 'src-1', '/Conclusions.pdf', 'Analyser', second, 60000)
        self.assertEqual(second.chunk_calls, 0);self.assertEqual(second.merge_calls, 0)
        self.assertEqual(coverage2['merges_from_cache'], first.merge_calls);self.assertEqual([x['excerpt'] for x in rows2], [x['excerpt'] for x in rows])
        self.assertEqual(coverage2['chunks_analyzed'], coverage['chunks_total'])          # les fusions ne comptent pas comme fragments
        self.desk.c['workstation'] = {**self.desk.c.get('workstation', {}), 'roundcube_url': 'https://autre.example.test/'}   # réglage sans effet : cache conservé
        third = MergeModel()
        analyze_pages(self.desk, pages, 'src-1', '/Conclusions.pdf', 'Analyser', third, 60000)
        self.assertEqual(third.chunk_calls + third.merge_calls, 0)


class SinglePanel(t530.Base):
    def test_voice_lives_in_the_assistant_panel(self):
        page = self.request('/aujourdhui')['body']
        self.assertIn('id="ws-ai-talk"', page);self.assertIn('id="ws-ai-voice"', page);self.assertIn('<strong>Pilote</strong>', page)
        self.assertIn('💬 Dialoguer', page);self.assertIn('🎙 Dicter', page)
        js = (ROOT / 'agent' / 'static' / 'v568.js').read_text(encoding='utf-8')
        self.assertIn("document.querySelector('#ws-ai-talk')", js);self.assertIn("dockVoice.append(pane)", js);self.assertIn("addTurn(payload.instruction,text,'voice')", js)
        self.assertNotIn("document.body.append(launch,pane);", js.replace('else document.body.append(launch,pane)', ''))
        css = (ROOT / 'agent' / 'static' / 'v568.css').read_text(encoding='utf-8')
        self.assertIn('.voice568-embedded{position:static!important', css)


if __name__ == '__main__':
    unittest.main()

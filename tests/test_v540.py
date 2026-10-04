"""5.4.0 : IA externe sûre — pseudonymisation réversible obligatoire hors local, aperçu et journal de ce qui part, connecteur Anthropic
natif, mode mixte (tri local, rédaction via API)."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from agent import ia540, pseudo540
from agent.ai_gateway import save_provider
from agent.common import Stop
from agent.desk import Desk
from agent.hybrid400 import policy, save_policy
from agent.model import Model, routed_config
from agent.pseudo540 import Pseudonymizer
import test_agent as fixtures
import test_desk

SAMPLE = ('Bonjour Maître, je vous écris pour M. Jean-Pierre DUPOND (né le 12/03/1975), demeurant 10 rue des Exemples – 69000 LYON, '
          'tél. 06 00 00 00 01, jean.dupond@example.test. La SAS ATELIER NORD (SIRET 123 456 789 00012) a payé sur l’IBAN '
          'FR76 3000 6000 0112 3456 7890 189. Claire Martin et Me Durand confirment. NIR 1 75 03 69 123 456 78. PAR CES MOTIFS, devant le TJ de LYON.')


class FakeHTTP:
    calls, payloads, headers_seen = [], [], []
    answer = None

    def __init__(self, base, *args, **kwargs):
        self.base, self.headers = base, {}

    def json(self, method, path, payload=None):
        FakeHTTP.calls.append((self.base, method, path))
        FakeHTTP.payloads.append((path, payload))
        FakeHTTP.headers_seen.append(dict(self.headers))
        if path == '/api/show':
            return {}
        if path == '/models':
            return {'data': [{'id': 'x'}]}
        if path == '/chat/completions':
            return {'choices': [{'message': {'content': FakeHTTP.answer or 'ok'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        if path == '/messages':
            if FakeHTTP.answer == 'TRONQUE':
                return {'content': [{'type': 'text', 'text': '{"a'}], 'stop_reason': 'max_tokens', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
            return {'content': [{'type': 'text', 'text': FakeHTTP.answer or 'ok'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 12, 'output_tokens': 7}}
        if path == '/api/chat':
            return {'done': True, 'message': {'content': 'réponse locale'}}
        raise AssertionError(path)


class Pseudo(unittest.TestCase):
    def test_sample_is_pseudonymised_and_restored_exactly(self):
        ps = Pseudonymizer(known=[])
        out = ps.text(SAMPLE)
        for secret in ('DUPOND', 'Jean-Pierre', '12/03/1975', 'Exemples', '06 00 00 00 01', 'jean.dupond@example.test', 'ATELIER NORD', '123 456 789 00012',
                       'FR76', 'Claire Martin', 'Durand', '1 75 03 69'):
            self.assertNotIn(secret, out, secret)
        for kept in ('PAR CES MOTIFS', 'TJ de LYON', 'Maître', 'Me '):
            self.assertIn(kept, out, kept)                                                   # les mots juridiques restent lisibles
        self.assertEqual(ps.restore(out), SAMPLE)
        counts = ps.summary()
        for cat in ('PERSONNE', 'SOCIETE', 'ADRESSE', 'COURRIEL', 'TELEPHONE', 'IBAN', 'NUMERO', 'DATE_NAISSANCE'):
            self.assertGreater(counts.get(cat, 0), 0, cat)

    def test_company_name_stops_at_the_end_of_the_sentence(self):
        ps = Pseudonymizer(known=[])
        self.assertEqual(ps.text('Facture de la SAS ATELIER NORD. Claire Martin témoignera.'),
                         'Facture de la SAS [SOCIETE_1]. [PERSONNE_1] témoignera.')

    def test_same_name_same_token_and_json_restoration(self):
        ps = Pseudonymizer(known=[('Client DEMO', 'PERSONNE')])
        a, b = ps.text('Le client Client DEMO a écrit.'), ps.text('Réponse à client demo.')
        self.assertIn('[PERSONNE_1]', a)
        self.assertIn('[PERSONNE_1]', b)                                                     # même personne, même marqueur
        raw = json.dumps({'body': 'Cher [PERSONNE_1], PERSONNE_1 et [PERSONNE_9]'})
        self.assertEqual(json.loads(ps.restore(raw, json_mode=True))['body'], 'Cher Client DEMO, Client DEMO et [PERSONNE_9]')

    def test_known_entities_from_matters_parties_and_roots(self):
        f = fixtures.EngineTests('test_observation_has_no_mail_write')
        f.setUp()
        self.addCleanup(f.doCleanups)
        d = Desk(f.c)
        from agent import metier500
        metier500.ensure_schema(d)
        d.db.execute("INSERT INTO conflicts500_parties VALUES('p1','DOS-001','Hôtel Bellevue SARL','hotel bellevue','adverse','direction@bellevue.example','manuel','x')")
        d.db.commit()
        pseudo540._CACHE['key'] = None
        from agent.model import pseudo_sources
        ps = Pseudonymizer(pseudo_sources(f.c))
        out = ps.text('Client DEMO (DOS-001) contre Hôtel Bellevue SARL ; voir /Dossiers/DEMO/PROCEDURE/acte.docx')
        for secret in ('Client DEMO', 'DOS-001', 'Bellevue', '/Dossiers/DEMO'):
            self.assertNotIn(secret, out, secret)


class External(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.d = Desk(self.f.c)
        secret = Path(self.f.c['state_dir']) / 'k.secret'
        secret.write_text('cle-test\n')
        secret.chmod(0o600)
        self.secret = str(secret)
        for pid, ptype, url in (('openai', 'openai', 'https://api.openai.com/v1'), ('anthropic', 'anthropic', 'https://api.anthropic.com/v1')):
            self.f.c.setdefault('ai_providers', {})[pid] = {'id': pid, 'type': ptype, 'url': url, 'model': 'claude-sonnet-5-5' if ptype == 'anthropic' else 'gpt-5',
                                                            'enabled': True, 'external_data_allowed': True, 'secret_file': self.secret, 'state_dir': self.f.c['state_dir']}
        FakeHTTP.calls, FakeHTTP.payloads, FakeHTTP.headers_seen, FakeHTTP.answer = [], [], [], None
        pseudo540._CACHE['key'] = None
        p = patch('agent.model.HTTP', FakeHTTP)
        p.start()
        self.addCleanup(p.stop)

    def route_all(self, provider, model):
        with patch('agent.ia540.Model', create=True):
            ia540.apply_mixed(self.d, provider, model, 'test-local', test=False)

    def test_manual_route_to_openai_is_pseudonymised_and_answer_restored(self):
        self.route_all('openai', 'gpt-5')
        FakeHTTP.answer = 'Bonjour [PERSONNE_1], votre dossier [REFERENCE_1] avance.'
        cfg = routed_config(self.f.c, 'mail_drafting')
        self.assertEqual(cfg['provider_id'], 'openai')
        out = Model(cfg).complete([{'role': 'user', 'content': 'Client DEMO (DOS-001, client@example.test) demande un point.'}])
        sent = json.dumps([p for path, p in FakeHTTP.payloads if path == '/chat/completions'][-1], ensure_ascii=False)
        for secret in ('Client DEMO', 'DOS-001', 'client@example.test'):
            self.assertNotIn(secret, sent)
        self.assertEqual(out, 'Bonjour Client DEMO, votre dossier DOS-001 avance.')
        log = pseudo540.recent(self.f.c['state_dir'])
        self.assertEqual(log[0]['provider'], 'openai')
        self.assertGreaterEqual(log[0]['counts'].get('COURRIEL', 0), 1)
        sample = pseudo540.sample(self.f.c['state_dir'], log[0]['id'])
        self.assertNotIn('Client DEMO', sample)
        self.assertIn('[PERSONNE_1]', sample)

    def test_anthropic_messages_api(self):
        self.route_all('anthropic', 'claude-sonnet-5-5')
        cfg = routed_config(self.f.c, 'document_drafting')
        FakeHTTP.answer = '```json\n{"titre": "Courrier pour [PERSONNE_1]"}\n```'
        out = Model(cfg).complete([{'role': 'system', 'content': 'Consignes du cabinet.'}, {'role': 'user', 'content': 'Écris à Client DEMO.'}],
                                  max_tokens=300, json_schema={'type': 'object'})
        self.assertEqual(json.loads(out), {'titre': 'Courrier pour Client DEMO'})
        req = [p for path, p in FakeHTTP.payloads if path == '/messages'][-1]
        self.assertEqual(req['model'], 'claude-sonnet-5-5')
        self.assertIn('Consignes du cabinet.', req['system'])
        self.assertIn('JSON', req['system'])
        self.assertEqual([m['role'] for m in req['messages']], ['user'])
        self.assertNotIn('Client DEMO', json.dumps(req, ensure_ascii=False))
        headers = FakeHTTP.headers_seen[-1]
        self.assertEqual(headers.get('x-api-key'), 'cle-test')
        self.assertEqual(headers.get('anthropic-version'), '2023-06-01')
        self.assertNotIn('Authorization', headers)
        FakeHTTP.answer = 'TRONQUE'
        with self.assertRaises(Stop) as ctx:
            Model(cfg).complete([{'role': 'user', 'content': 'x'}], max_tokens=10)
        self.assertEqual(str(ctx.exception), 'generation_ia_incomplete')
        db = sqlite3.connect(Path(self.f.c['state_dir']) / 'desk.sqlite3')
        self.assertEqual(db.execute("SELECT input_tokens FROM ai_usage_v391 WHERE provider='anthropic' AND status='done'").fetchone()[0], 12)
        db.close()

    def test_mixed_mode_routes_and_back_to_local(self):
        self.route_all('anthropic', 'claude-sonnet-5-5')
        self.assertEqual(routed_config(self.f.c, 'mail_triage')['provider_id'], 'ollama')
        self.assertEqual(routed_config(self.f.c, 'mail_triage')['model'], 'test-local')
        self.assertEqual(routed_config(self.f.c, 'control')['provider_id'], 'ollama')
        self.assertEqual(routed_config(self.f.c, 'mail_drafting')['provider_id'], 'anthropic')
        self.assertEqual(ia540.current(self.d)['mode'], 'mixte')
        ia540.set_local(self.d)
        self.assertEqual(routed_config(self.f.c, 'mail_drafting')['provider_id'], 'ollama')
        self.assertEqual(ia540.current(self.d)['mode'], 'local')
        self.f.c['ai_providers']['openai']['external_data_allowed'] = False
        with self.assertRaises(Stop):
            ia540.apply_mixed(self.d, 'openai', 'gpt-5', test=False)
        with self.assertRaises(Stop):
            ia540.apply_mixed(self.d, 'ollama', 'x', test=False)

    def test_pseudonymisation_cannot_be_disabled(self):
        self.f.c['hybrid_routing'] = {'mode': 'manual', 'anonymize_external': False}
        self.assertTrue(policy(self.f.c)['anonymize_external'])

    def test_anthropic_provider_can_be_saved(self):
        with patch('agent.ai_gateway.save_secret', return_value=self.secret):
            out = save_provider(self.d, 'anthropic', 'anthropic', '', 'claude-sonnet-5-5', 'cle', True, True)
        self.assertEqual(out['url'], 'https://api.anthropic.com/v1')
        self.assertEqual(out['type'], 'anthropic')


class Page(unittest.TestCase):
    setUp0 = test_desk.WebTests.setUp
    request = test_desk.WebTests.request

    def setUp(self):
        self.setUp0()
        self.d = Desk(self.f.c)

    def test_page_preview_and_settings(self):
        body = self.request('/ia-externe')['body']
        for text in ('IA externe sûre', 'Mode d’utilisation', 'Tout en local', 'Mixte (recommandé hors local)', 'Aperçu de ce qui part',
                     'Journal des envois', '/static/v540.js', 'Aucun fournisseur externe'):
            self.assertIn(text, body)
        self.assertIn('/ia-externe', self.request('/parametres', query='tab=ia')['body'])
        self.assertIn('anthropic · anthropic', self.request('/parametres', query='tab=ia')['body'])
        out = ia540.handle(self.d, 'm540/preview', {'text': 'Écrire à M. Paul MARTIN, paul.martin@example.test'})
        self.assertNotIn('MARTIN', out['sent'])
        self.assertTrue(out['restored_ok'])
        self.assertEqual({r['category'] for r in out['replacements']}, {'Personnes', 'Courriels'})
        with self.assertRaises(Stop):
            ia540.handle(self.d, 'm540/mode', {'mode': 'inconnu'})


if __name__ == '__main__':
    unittest.main()

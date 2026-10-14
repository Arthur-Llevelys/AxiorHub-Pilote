"""5.6.47 — génération locale en flux : une génération plus longue que le délai n'est plus coupée tant que le modèle écrit ; un modèle
muet reste arrêté au délai ; un plafond borne la génération entière. Serveur local fictif uniquement."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from agent.common import HTTP, Stop


class _Ollama(BaseHTTPRequestHandler):
    """Faux Ollama : renvoie la réponse morceau par morceau, ``pause`` secondes entre deux morceaux."""
    pause, pieces, silent = 0.3, ['{"ok":', ' true', '}'], False
    seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/api/chat':   # description du modèle (/api/show) : réponse ordinaire
            data = json.dumps({'capabilities': ['completion'], 'model_info': {}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        type(self).seen.append(body)
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        if self.silent:
            time.sleep(3)
            return
        for piece in self.pieces:
            time.sleep(self.pause)
            self.wfile.write((json.dumps({'message': {'role': 'assistant', 'content': piece}, 'done': False}) + '\n').encode())
            self.wfile.flush()
        self.wfile.write((json.dumps({'message': {'role': 'assistant', 'content': ''}, 'done': True, 'done_reason': 'stop',
                                      'prompt_eval_count': 12, 'eval_count': 3}) + '\n').encode())


class GenerationEnFlux(unittest.TestCase):
    def serve(self, **attrs):
        handler = type('H', (_Ollama,), {**attrs, 'seen': []})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:%d' % server.server_address[1], handler

    def test_generation_plus_longue_que_le_delai_aboutit(self):
        url, handler = self.serve(pause=0.4, pieces=['{"ok":', ' true', ',"n":', ' 1', '}'])   # 2 s au total, délai de 1 s
        out = HTTP(url, local_only=True, timeout=1).json('POST', '/api/chat', {'model': 'fictif', 'stream': True, 'messages': []})
        self.assertEqual((out['done'], out['done_reason'], out['eval_count']), (True, 'stop', 3))
        self.assertEqual(json.loads(out['message']['content']), {'ok': True, 'n': 1})

    def test_modele_muet_arrete_au_delai(self):
        url, _ = self.serve(silent=True)
        with self.assertRaisesRegex(Stop, 'delai_http_depasse'):
            HTTP(url, local_only=True, timeout=1).json('POST', '/api/chat', {'model': 'fictif', 'stream': True, 'messages': []})

    def test_plafond_global(self):
        url, _ = self.serve(pause=0.4, pieces=['a'] * 10)
        client = HTTP(url, local_only=True, timeout=1)
        with patch.object(HTTP, 'STREAM_MAX_SECONDS', 1):
            with self.assertRaisesRegex(Stop, 'delai_http_depasse'):
                client.json('POST', '/api/chat', {'model': 'fictif', 'stream': True, 'messages': []})

    def test_le_modele_local_demande_le_flux(self):
        url, handler = self.serve(pause=0.05)
        from agent.model import Model
        model = Model({'provider': 'ollama', 'url': url, 'model': 'fictif', 'timeout_seconds': 5, 'num_ctx': 8192, 'context_mode': 'fixe'})
        model.context_limit = None
        text = model._leaf([{'role': 'user', 'content': 'Bonjour'}], 0, 64, None)
        self.assertEqual(text, '{"ok": true}')
        self.assertIs(handler.seen[0]['stream'], True)


# ---------------------------------------------------------------------------------------------------------------- demandes du 14 octobre
from datetime import date

from agent import agenda5625, config567, voice568


class PauseEntreParagraphes(unittest.TestCase):
    def test_reglage_general_et_par_langue(self):
        self.assertEqual(voice568.paragraph_pause_ms({}), 500)                                   # une respiration par défaut
        cfg = {'speech568': {'paragraph_pause_ms': 300, 'paragraph_pause_by_language': ['fr=450', 'en = 250', 'de=abc']}}
        self.assertEqual(voice568.paragraph_pause_ms(cfg, 'fr'), 450)
        self.assertEqual(voice568.paragraph_pause_ms(cfg, 'en'), 250)
        self.assertEqual(voice568.paragraph_pause_ms(cfg, 'es'), 300)
        self.assertEqual(voice568.paragraph_pause_ms(cfg, 'de'), 300)                            # valeur illisible : réglage général
        self.assertEqual(voice568.paragraph_pause_ms({'speech568': {'paragraph_pause_ms': 99999}}), 3000)

    def test_bornes_du_reglage(self):
        self.assertEqual(config567._validate('integer', '0', 'speech568.paragraph_pause_ms'), 0)
        self.assertEqual(config567._validate('integer', '3000', 'speech568.paragraph_pause_ms'), 3000)
        with self.assertRaisesRegex(Stop, 'reglage_nombre_invalide'):
            config567._validate('integer', '3001', 'speech568.paragraph_pause_ms')
        keys = [f[0] for f in config567.FIELDS] if hasattr(config567, 'FIELDS') else []
        if keys:
            self.assertIn('speech568.paragraph_pause_ms', keys)

    def test_lecture_prepare_le_paragraphe_suivant(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parents[1] / 'agent' / 'static' / 'v5632.js').read_text(encoding='utf-8')
        self.assertIn("var next = i + 1 < parts.length ? fetchPart(parts[i + 1]) : null;", js)   # synthèse pendant la lecture
        self.assertIn("X-AxiorHub-Pause-Ms", js)


class DossiersDansLAgenda(unittest.TestCase):
    def test_nom_complet_trie_et_sans_plafond(self):
        matters = [{'id': 'DOS-%03d' % i, 'client_name': 'SXYZ', 'path': '/Dossiers/SXYZ - UPRJ - CONSEIL - DOS-%03d' % i} for i in range(450)]
        matters.append({'id': 'DOS-A', 'client_name': 'ALPHA', 'path': '/Dossiers/ALPHA - SAS FICTIVE - DOS-A'})
        html = agenda5625.form_html('/agent-courriel', date(2026, 10, 15), matters)
        self.assertIn('>SXYZ - UPRJ - CONSEIL - DOS-449</option>', html)                      # nom entier, plus de plafond à 400
        self.assertLess(html.index('ALPHA - SAS FICTIVE - DOS-A'), html.index('SXYZ - UPRJ'))     # ordre alphabétique

    def test_recherche_ajoutee_a_toutes_les_listes_de_dossiers(self):
        from pathlib import Path
        from agent import ui_helpers, web440
        root = Path(__file__).resolve().parents[1] / 'agent'
        js = (root / 'static' / 'v5647.js').read_text(encoding='utf-8')
        self.assertIn('select[name="matter"]', js)
        self.assertIn('/static/v5647.js', web440.STATIC)
        self.assertIn('v5647.js', (root / 'ui_helpers.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main(verbosity=2)

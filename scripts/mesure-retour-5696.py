#!/usr/bin/env python3
"""5.6.96 (lot affichage, U05 / N01) : mesure WSGI du retour aux pages de liste sur un cabinet fictif, à froid puis à chaud.

Poste de développement seulement (jamais le serveur du cabinet ; là, c'est le cabinet qui mesure avec ``scripts/mesurer-retour-5696.js``).
Le cabinet fictif est celui de ``scripts/mesure-aujourdhui.py`` (300 dossiers, 5 000 courriels suivis, agenda, tâches, documents préparés,
échéances, veille, travaux de l'agent). L'application est appelée directement comme application WSGI (``agent.web.App``), sans réseau,
avec un compte d'essai à mot de passe aléatoire gardé en mémoire ; aucun modèle (tout appel lève une erreur pendant la mesure).

- « froid » : première requête de chaque page dans un processus neuf (imports, préparation du schéma, caches vides) ;
- « chaud » : ``--passages`` requêtes suivantes (médiane, 95e centile, maximum), avec la décomposition ``Server-Timing`` du serveur
  (authentification, SQLite et nombre de requêtes, construction) quand l'arbre mesuré la fournit (5.6.96 et suivantes).
``--arbre`` désigne l'arbre mesuré (par défaut celui du script) : la même commande mesure l'avant (5.6.95) et l'après (5.6.96) avec le
même jeu fictif. Le résultat ne contient que des nombres et les chemins des pages de l'application (aucun titre, aucun filtre).

Usage : ``python scripts/mesure-retour-5696.py [--arbre DOSSIER] [--dossiers 300] [--courriels 5000] [--passages 30] [--sortie rapport.json]``.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import secrets
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

PAGES = ('/aujourdhui', '/courriels', '/dossiers', '/documents', '/echeances')


def _centile(valeurs, c):
    v = sorted(valeurs)
    return round(v[min(len(v) - 1, max(0, math.ceil(len(v) * c) - 1))], 1) if v else None


def _server_timing(valeur):
    out = {}
    for part in (valeur or '').split(','):
        morceaux = [m.strip() for m in part.split(';')]
        if not morceaux[0]:
            continue
        for m in morceaux[1:]:
            if m.startswith('dur='):
                out[morceaux[0]] = float(m[4:])
            elif m.startswith('desc=') and morceaux[0] == 'sqlite':
                out['requetes_sqlite'] = float(m[5:].strip('"') or 0)
    return out


class _FauxOllama:
    """Bouclage qui répond comme un Ollama démarré à la sonde de santé (``/api/tags``) : la mesure porte sur l'application, pas sur le délai
    de connexion d'un moteur absent. Toute autre adresse répond 503 (et le modèle est de toute façon interdit pendant la mesure)."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Reponse(BaseHTTPRequestHandler):
            def do_GET(self):
                corps = b'{"models": []}' if self.path.startswith('/api/tags') else b'{}'
                self.send_response(200 if self.path.startswith('/api/tags') else 503)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

            def log_message(self, *args):
                pass
        self.serveur = ThreadingHTTPServer(('127.0.0.1', 0), Reponse)
        self.url = 'http://127.0.0.1:%d' % self.serveur.server_address[1]
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mesure WSGI du retour aux pages de liste (poste de développement, données fictives).')
    parser.add_argument('--arbre', default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument('--dossiers', type=int, default=300)
    parser.add_argument('--courriels', type=int, default=5000)
    parser.add_argument('--passages', type=int, default=30)
    parser.add_argument('--pages', default=','.join(PAGES))
    parser.add_argument('--sortie', default='')
    args = parser.parse_args(argv)
    arbre = Path(args.arbre).resolve()
    sys.path.insert(0, str(arbre))
    spec = importlib.util.spec_from_file_location('mesure_aujourdhui_semis', arbre / 'scripts' / 'mesure-aujourdhui.py')
    semis = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(semis)
    from agent import __version__
    from agent.common import private_json
    from agent.desk import Desk
    pages = tuple(p for p in args.pages.split(',') if p.startswith('/'))
    with tempfile.TemporaryDirectory(prefix='axh-mesure-retour-', ignore_cleanup_errors=True) as tmp:   # base encore ouverte par l'application sous Windows
        base = Path(tmp)
        (base / 'state').mkdir()
        liste = semis.matters(args.dossiers)
        private_json(base / 'matters.json', liste)
        c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'), 'installation': {'done': True, 'at': 'mesure', 'by': 'mesure'},
             'mail': {'enabled': False, 'own_addresses': ['cabinet@example.test']}, 'nextcloud': {'roots': ['/Dossiers'], 'local_path': str(base / 'stockage')},
             'documents': {}, 'ollama': {'model': 'interdit', 'url': _FauxOllama().url}, 'calendar': {'urls': [], 'timezone': 'Europe/Paris'},
             'automation': {'health_enabled': False, 'sync_enabled': False}, 'autonomy': {'enabled': False}, 'orchestrator': {'enabled': False},
             'production': {'enabled': False}, 'updates': {'channel': 'stable', 'metadata_url': ''}, 'audio': {'enabled': False}}
        (base / 'stockage' / 'Dossiers').mkdir(parents=True)
        private_json(base / 'config.json', c)
        desk = Desk(c)
        try:
            t0 = time.perf_counter()
            semis.semer(desk, liste, args.courriels)
            semis_s = round(time.perf_counter() - t0, 1)
        finally:
            desk.db.close()
        ident, mot = 'mesure-' + secrets.token_hex(3), secrets.token_urlsafe(18)   # en mémoire seulement
        sel = secrets.token_hex(16)
        auth = {'username': ident, 'salt': sel, 'hash': hashlib.scrypt(mot.encode(), salt=bytes.fromhex(sel), n=16384, r=8, p=1).hex(),
                'csrf': secrets.token_urlsafe(32), 'origin': 'http://127.0.0.1:8765', 'prefix': '/agent-courriel'}
        private_json(base / 'ui-auth.json', auth)
        from agent.web import App
        app = App(str(base / 'config.json'), str(base / 'ui-auth.json'))
        jeton = 'Basic ' + base64.b64encode(('%s:%s' % (ident, mot)).encode()).decode()

        def appeler(chemin):
            env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel' + chemin, 'QUERY_STRING': '', 'SERVER_NAME': '127.0.0.1', 'SERVER_PORT': '8765',
                   'HTTP_HOST': '127.0.0.1:8765', 'REMOTE_ADDR': '127.0.0.1', 'HTTP_AUTHORIZATION': jeton, 'HTTP_ACCEPT': 'text/html', 'wsgi.url_scheme': 'http',
                   'wsgi.input': io.BytesIO(b''), 'wsgi.errors': io.StringIO(), 'SCRIPT_NAME': ''}
            retenu = {}

            def start_response(status, headers, exc_info=None):
                retenu['status'], retenu['headers'] = status, dict(headers)
            debut = time.perf_counter()
            corps = b''.join(app(env, start_response))
            duree = (time.perf_counter() - debut) * 1000
            return retenu['status'], retenu['headers'], len(corps), duree
        rapport = {'arbre': {'version': __version__}, 'cabinet': {'dossiers': args.dossiers, 'courriels': args.courriels, 'semis_s': semis_s},
                   'passages': args.passages, 'pages': {}}
        with patch('agent.model.Model.ask', side_effect=AssertionError('modèle interdit')), patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
            for chemin in pages:
                statut, entetes, octets, froid = appeler(chemin)
                if not statut.startswith('200'):
                    rapport['pages'][chemin] = {'statut': statut}
                    continue
                durees, details = [], []
                for _ in range(args.passages):
                    statut, entetes, octets, duree = appeler(chemin)
                    durees.append(duree)
                    details.append(_server_timing(entetes.get('Server-Timing', '')))
                ligne = {'froid_ms': round(froid, 1), 'chaud_ms': {'mediane': round(statistics.median(durees), 1), 'p95': _centile(durees, .95), 'max': round(max(durees), 1)},
                         'octets': octets, 'cache_control': entetes.get('Cache-Control', '')}
                if any(details):
                    ligne['serveur_mediane'] = {k: round(statistics.median([d.get(k, 0) for d in details]), 1) for k in sorted({k for d in details for k in d})}
                rapport['pages'][chemin] = ligne
        rapport['mesure_le'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        rapport['python'] = sys.version.split()[0]
    texte = json.dumps(rapport, ensure_ascii=False, indent=1)
    if args.sortie:
        Path(args.sortie).write_text(texte, encoding='utf-8')
    print(texte)
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""5.6.101 (lot P, P02, V101-08) : mesure de la page Aujourd'hui allégée et de ses fragments sur un cabinet fictif (poste de développement,
jamais le serveur du cabinet). Dérivé de ``scripts/mesure56100.py`` (même cabinet fictif, même protocole, même lecture de ``Server-Timing``).

Pour chaque scénario : une ouverture « froide » puis ``--essais`` ouvertures « chaudes » (30 par défaut). Relevés : temps jusqu'au premier
octet et durée totale (p50, p95, max), taille de la réponse, requêtes SQLite (en-tête ``Server-Timing``). L'application est appelée comme
application WSGI (``agent.web.App``), sans réseau ; aucun modèle (tout appel lève une erreur) ; la messagerie est désactivée.

Scénarios :
- ``aujourdhui`` : GET /aujourdhui, nouvelle présentation (interrupteur ``interface56100`` actif, réglage par défaut) ;
- ``aujourdhui_ancienne`` : GET /aujourdhui, ancienne présentation (interrupteur éteint pour le cabinet) ;
- ``fragment_<nom>`` : chaque fragment servi au dépli (``GET /api440/m56101/aujourdhui/fragment?nom=…``), nouvelle présentation.

Objectifs du cahier (§ 7, P02), à 900 dossiers : HTML initial < 120 ko, p50 < 150 ms, p95 < 200 ms, au plus 150 requêtes, chaque fragment
< 150 ms. Le rapport dit pour chaque objectif « respecte » vrai ou faux ; il ne contient que des nombres et les chemins des routes.

Non couvert ici (déclaré, jamais simulé comme réussi) : navigateur réel (temps d'affichage, dépli), proxy, compression, serveur du cabinet.

Usage : ``python scripts/mesure56101.py [--essais 30] [--dossiers 900] [--propositions 200] [--sortie rapport.json] [--html dossier]``.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
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

ARBRE = Path(__file__).resolve().parent.parent
NOMS = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')
OBJECTIFS = {'octets_max': 120 * 1024, 'p50_ms': 150.0, 'p95_ms': 200.0, 'requetes_max': 150, 'fragment_ms': 150.0}


def _centile(valeurs, c):
    v = sorted(valeurs)
    return round(v[min(len(v) - 1, max(0, math.ceil(len(v) * c) - 1))], 1) if v else None


def _resume(valeurs):
    return {'p50': round(statistics.median(valeurs), 1), 'p95': _centile(valeurs, .95), 'max': round(max(valeurs), 1)} if valeurs else {}


def _server_timing(valeur):
    out = {}
    for part in (valeur or '').split(','):
        morceaux = [m.strip() for m in part.split(';')]
        if not morceaux[0]:
            continue
        for m in morceaux[1:]:
            if m.startswith('dur='):
                out[morceaux[0] + '_ms'] = float(m[4:])
            elif m.startswith('desc=') and morceaux[0] == 'sqlite':
                out['requetes_sqlite'] = float(m[5:].strip('"') or 0)
    return out


def _faux_ollama():
    """Même bouclage local que ``mesure56100.py`` : sans lui, chaque sonde de santé attend le refus de connexion."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('mesure_retour_5696_ollama', ARBRE / 'scripts' / 'mesure-retour-5696.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._FauxOllama()


def _dossiers(n):
    return [{'id': 'DOS-%03d' % (i + 1), 'client_name': '%s %d' % (NOMS[i % 6], i + 1), 'path': '/Dossiers/DOS-%03d' % (i + 1),
             'references': ['DOS-%03d' % (i + 1)], 'aliases': [], 'correspondents': []} for i in range(n)]


def _propositions(desk, n):
    """Réponses préparées en attente de décision (``manual_drafts``), sans texte réel (comme ``mesure56100.py``)."""
    maintenant = datetime.now(timezone.utc)
    for i in range(n):
        cle = hashlib.sha256(('mesure56101-%d' % i).encode()).hexdigest()
        desk.db.execute('INSERT OR REPLACE INTO manual_drafts(mail_key,data,updated) VALUES(?,?,?)',
                        (cle, json.dumps({'matter': '', 'result': {'requires_decision': True, 'limits': []}}), (maintenant - timedelta(minutes=i)).isoformat()))
    desk.db.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mesure de la page Aujourd’hui et de ses fragments (poste de développement, données fictives).')
    parser.add_argument('--essais', type=int, default=30)
    parser.add_argument('--dossiers', type=int, default=900)
    parser.add_argument('--propositions', type=int, default=200)
    parser.add_argument('--sortie', default='')
    parser.add_argument('--html', default='', help='dossier où écrire le HTML de chaque scénario (analyse locale ; jamais publié)')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ARBRE))
    from agent import __version__
    from agent.common import private_json
    from agent.desk import Desk
    rapport = {'arbre': {'version': __version__}, 'essais': args.essais, 'dossiers': args.dossiers, 'propositions': args.propositions,
               'objectifs': OBJECTIFS, 'scenarios': [],
               'non_mesure': ['affichage et dépli dans un vrai navigateur', 'proxy et compression', 'serveur du cabinet']}
    with tempfile.TemporaryDirectory(prefix='axh-mesure56101-', ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        (base / 'state').mkdir()
        (base / 'stockage' / 'Dossiers').mkdir(parents=True)
        (base / 'secret').write_text('mot-de-passe-de-mesure', encoding='utf-8')
        (base / 'secret').chmod(0o600)
        private_json(base / 'matters.json', _dossiers(args.dossiers))
        c = {'mode': 'drafts', 'state_dir': str(base / 'state'), 'matters_file': str(base / 'matters.json'), 'installation': {'done': True, 'at': 'mesure', 'by': 'mesure'},
             'mail': {'enabled': False, 'host': 'imap.mesure.invalid', 'username': 'cabinet', 'password_file': str(base / 'secret'), 'inbox': 'INBOX',
                      'drafts': 'Drafts', 'sent': 'Sent', 'own_addresses': ['cabinet@mesure.invalid'], 'from_address': 'cabinet@mesure.invalid'},
             'nextcloud': {'roots': ['/Dossiers'], 'local_path': str(base / 'stockage')}, 'documents': {}, 'ollama': {'model': 'interdit', 'url': _faux_ollama().url},
             'calendar': {'urls': [], 'timezone': 'Europe/Paris'}, 'automation': {'health_enabled': False, 'sync_enabled': False}, 'autonomy': {'enabled': False},
             'orchestrator': {'enabled': False}, 'production': {'enabled': False}, 'updates': {'channel': 'stable', 'metadata_url': ''}, 'audio': {'enabled': False}}
        private_json(base / 'config.json', c)
        desk = Desk(c)
        try:
            _propositions(desk, args.propositions)
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

        def appeler(chemin, requete='', accept='text/html'):
            env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel' + chemin, 'QUERY_STRING': requete, 'SERVER_NAME': '127.0.0.1', 'SERVER_PORT': '8765',
                   'HTTP_HOST': '127.0.0.1:8765', 'REMOTE_ADDR': '127.0.0.1', 'HTTP_AUTHORIZATION': jeton, 'HTTP_ACCEPT': accept, 'wsgi.url_scheme': 'http',
                   'wsgi.input': io.BytesIO(b''), 'wsgi.errors': io.StringIO(), 'SCRIPT_NAME': ''}
            retenu = {}
            debut = time.perf_counter()

            def start_response(status, headers, exc_info=None):
                retenu['ttfb'] = (time.perf_counter() - debut) * 1000
                retenu['status'], retenu['headers'] = status, dict(headers)
            corps = b''.join(app(env, start_response))
            retenu['total'] = (time.perf_counter() - debut) * 1000
            retenu['octets'] = len(corps)
            retenu['corps'] = corps
            return retenu

        def scenario(nom, chemin, requete='', accept='text/html', fragment=False):
            froid = appeler(chemin, requete, accept)
            if not froid['status'].startswith('200'):
                rapport['scenarios'].append({'scenario': nom, 'route': chemin, 'statut': froid['status'].split()[0]})
                return
            ttfb, totaux, details = [], [], []
            for _ in range(args.essais):
                r = appeler(chemin, requete, accept)
                ttfb.append(r['ttfb'])
                totaux.append(r['total'])
                details.append(_server_timing(r['headers'].get('Server-Timing', '')))
            if args.html:
                Path(args.html).mkdir(parents=True, exist_ok=True)
                (Path(args.html) / (nom + ('.json' if fragment else '.html'))).write_bytes(r['corps'])
            requetes = [d.get('requetes_sqlite', 0.0) for d in details]
            ligne = {'scenario': nom, 'route': chemin, 'ttfb_ms': dict(_resume(ttfb), froid=round(froid['ttfb'], 1)), 'total_ms': _resume(totaux),
                     'octets': r['octets'], 'cache_control': r['headers'].get('Cache-Control', ''),
                     'requetes_sqlite': {'p50': round(statistics.median(requetes), 1), 'p95': _centile(requetes, .95)}}
            if fragment:
                ligne['respecte'] = {'fragment_ms': ligne['total_ms']['p95'] < OBJECTIFS['fragment_ms']}
            else:
                ligne['respecte'] = {'octets_max': r['octets'] < OBJECTIFS['octets_max'], 'p50_ms': ligne['total_ms']['p50'] < OBJECTIFS['p50_ms'],
                                     'p95_ms': ligne['total_ms']['p95'] < OBJECTIFS['p95_ms'], 'requetes_max': ligne['requetes_sqlite']['p95'] <= OBJECTIFS['requetes_max']}
            rapport['scenarios'].append(ligne)

        def interrupteur(actif):
            d = Desk(c)
            try:
                from agent.interface56100 import regler_cabinet
                regler_cabinet(d, actif)
            finally:
                d.db.close()

        with patch('agent.model.Model.ask', side_effect=AssertionError('modèle interdit')), \
                patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')):
            scenario('aujourdhui', '/aujourdhui')
            try:
                from agent.aujourdhui56101 import FRAGMENTS
            except ImportError:      # arbre antérieur à la 5.6.101 : seule la page est mesurée
                FRAGMENTS = ()
            for nom in FRAGMENTS:
                scenario('fragment_' + nom, '/api440/m56101/aujourdhui/fragment', 'nom=' + nom, 'application/json', fragment=True)
            interrupteur(False)
            scenario('aujourdhui_ancienne', '/aujourdhui')
            interrupteur(True)
        rapport['mesure_le'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        rapport['python'] = sys.version.split()[0]
    texte = json.dumps(rapport, ensure_ascii=False, indent=1)
    if args.sortie:
        Path(args.sortie).write_text(texte, encoding='utf-8')
    print(texte)
    return 0


if __name__ == '__main__':
    sys.exit(main())

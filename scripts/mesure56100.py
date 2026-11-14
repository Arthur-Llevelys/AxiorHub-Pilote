#!/usr/bin/env python3
"""5.6.100 (lot F, V100-08) : mesure de réactivité par route sur un cabinet fictif (poste de développement, jamais le serveur du cabinet).

Pour chaque scénario : une ouverture « froide » (première après la mise en place : registre changé, caches vides) puis ``--essais``
ouvertures « chaudes » (30 par défaut, protocole du cahier § 12). Relevés : temps jusqu'au premier octet (TTFB : appel WSGI jusqu'à
``start_response``) et durée totale, p50 / p95 ; taille de la réponse ; requêtes SQLite et commandes IMAP (en-tête ``Server-Timing`` du
serveur) ; temps SQLite et IMAP. L'application est appelée comme application WSGI (``agent.web.App``), sans réseau ; la messagerie est le
faux serveur ``tests/faux_imap56100.py`` (``--entetes`` messages fictifs), avec une latence par commande et un retard de connexion
injectés dans le scénario « lent ». Aucun modèle (tout appel lève une erreur). Le rapport ne contient que des nombres et les chemins des
routes : ni titre, ni nom, ni filtre, ni adresse.

Scénarios : ``dossiers_<N>`` (GET /dossiers, pour chaque N de ``--dossiers``), ``aujourdhui``, ``a_relire`` (GET /courriels avec
``--propositions`` réponses à décider ; brouillons IMAP simulés), ``reception_gabarit`` (GET /courriels?vue=boite), ``reception_imap``
(API de liste, 25 lignes), ``reception_imap_lent`` (même liste, ``--latence-imap-ms`` par commande et ``--retard-imap-ms`` à la
connexion), ``recherche_accentuee`` (API de liste, recherche avec accent).

Non couvert ici (déclaré, jamais simulé comme réussi) : navigateur réel (clic -> réaction, liste utilisable, aperçu prêt), proxy, compression,
panne Nextcloud, worker IA saturé : à relever dans le banc navigateur (``scripts/parcours-navigateur.py``) et sur le serveur du cabinet.

Usage : ``python scripts/mesure56100.py [--essais 30] [--dossiers 50,900] [--entetes 10000] [--propositions 200]
[--latence-imap-ms 15] [--retard-imap-ms 600] [--sortie rapport.json]``.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
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

ARBRE = Path(__file__).resolve().parent.parent
NOMS = ('DURAND', 'MARTIN', 'BERNARD', 'ZORVAL', 'KERVILAC', 'ORLANDEC')


def _centile(valeurs, c):
    v = sorted(valeurs)
    return round(v[min(len(v) - 1, max(0, math.ceil(len(v) * c) - 1))], 1) if v else None


def _resume(valeurs):
    return {'p50': round(statistics.median(valeurs), 1), 'p95': _centile(valeurs, .95), 'max': round(max(valeurs), 1)} if valeurs else {}


def _server_timing(valeur):
    """``auth;dur=1.0, sqlite;dur=2.0;desc="12", imap;dur=3.0;desc="4", …`` -> {nom: ms, requetes_sqlite, commandes_imap}."""
    out = {}
    for part in (valeur or '').split(','):
        morceaux = [m.strip() for m in part.split(';')]
        if not morceaux[0]:
            continue
        for m in morceaux[1:]:
            if m.startswith('dur='):
                out[morceaux[0] + '_ms'] = float(m[4:])
            elif m.startswith('desc='):
                cle = {'sqlite': 'requetes_sqlite', 'imap': 'commandes_imap'}.get(morceaux[0])
                if cle:
                    out[cle] = float(m[5:].strip('"') or 0)
    return out


def _faux_imap():
    spec = importlib.util.spec_from_file_location('faux_imap56100_mesure', ARBRE / 'tests' / 'faux_imap56100.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _faux_ollama():
    """Bouclage local qui répond comme un Ollama démarré à la sonde de santé (celui de ``mesure-retour-5696.py``) : sans lui, chaque sonde
    attend le refus de connexion (environ 2 s sous Windows) et la mesure porterait sur ce délai, pas sur l'application."""
    spec = importlib.util.spec_from_file_location('mesure_retour_5696_ollama', ARBRE / 'scripts' / 'mesure-retour-5696.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._FauxOllama()


def _dossiers(n):
    return [{'id': 'DOS-%03d' % (i + 1), 'client_name': '%s %d' % (NOMS[i % 6], i + 1), 'path': '/Dossiers/DOS-%03d' % (i + 1),
             'references': ['DOS-%03d' % (i + 1)], 'aliases': [], 'correspondents': []} for i in range(n)]


def _propositions(desk, n):
    """Réponses préparées en attente de décision (``manual_drafts``), sans texte réel."""
    maintenant = datetime.now(timezone.utc)
    for i in range(n):
        cle = hashlib.sha256(('mesure56100-%d' % i).encode()).hexdigest()
        desk.db.execute('INSERT OR REPLACE INTO manual_drafts(mail_key,data,updated) VALUES(?,?,?)',
                        (cle, json.dumps({'matter': '', 'result': {'requires_decision': True, 'limits': []}}), (maintenant - timedelta(minutes=i)).isoformat()))
    desk.db.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Mesure de réactivité par route (poste de développement, données fictives).')
    parser.add_argument('--essais', type=int, default=30)
    parser.add_argument('--dossiers', default='50,900')
    parser.add_argument('--entetes', type=int, default=10000)
    parser.add_argument('--propositions', type=int, default=200)
    parser.add_argument('--latence-imap-ms', type=float, default=15.0)
    parser.add_argument('--retard-imap-ms', type=float, default=600.0)
    parser.add_argument('--sortie', default='')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ARBRE))
    faux = _faux_imap()
    from agent import __version__
    from agent.common import oublier_registre, private_json
    from agent.desk import Desk
    tailles = [int(x) for x in args.dossiers.split(',') if x.strip()]
    rapport = {'arbre': {'version': __version__}, 'essais': args.essais, 'entetes_imap': args.entetes, 'propositions': args.propositions,
               'latence_imap_ms': args.latence_imap_ms, 'retard_imap_ms': args.retard_imap_ms, 'scenarios': [],
               'non_mesure': ['clic -> réaction et liste utilisable dans un vrai navigateur', 'proxy et compression', 'panne Nextcloud', 'worker IA saturé']}
    with tempfile.TemporaryDirectory(prefix='axh-mesure56100-', ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        (base / 'state').mkdir()
        (base / 'stockage' / 'Dossiers').mkdir(parents=True)
        (base / 'secret').write_text('mot-de-passe-de-mesure', encoding='utf-8')
        (base / 'secret').chmod(0o600)   # lu par ``read_secret`` : refusé sous Linux s'il est lisible par d'autres
        private_json(base / 'matters.json', _dossiers(tailles[0] if tailles else 50))
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

        def appeler(chemin, requete=''):
            env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/agent-courriel' + chemin, 'QUERY_STRING': requete, 'SERVER_NAME': '127.0.0.1', 'SERVER_PORT': '8765',
                   'HTTP_HOST': '127.0.0.1:8765', 'REMOTE_ADDR': '127.0.0.1', 'HTTP_AUTHORIZATION': jeton, 'HTTP_ACCEPT': 'text/html', 'wsgi.url_scheme': 'http',
                   'wsgi.input': io.BytesIO(b''), 'wsgi.errors': io.StringIO(), 'SCRIPT_NAME': ''}
            retenu = {}
            debut = time.perf_counter()

            def start_response(status, headers, exc_info=None):
                retenu['ttfb'] = (time.perf_counter() - debut) * 1000
                retenu['status'], retenu['headers'] = status, dict(headers)
            corps = b''.join(app(env, start_response))
            retenu['total'] = (time.perf_counter() - debut) * 1000
            retenu['octets'] = len(corps)
            return retenu

        etat_imap = {'lent': False}
        boites = faux.FauxIMAP.boite_de(args.entetes).boites      # construites une fois : le coût du semis n'entre pas dans la mesure

        def connexion_rapide(cfg):
            lent = etat_imap['lent']
            return faux.FauxIMAP(boites, retard_s=(args.latence_imap_ms / 1000.0) if lent else 0.0,
                                 retard_connexion_s=(args.retard_imap_ms / 1000.0) if lent else 0.0)

        def brouillons(*_a, **_k):
            if etat_imap.get('lent'):
                time.sleep(args.retard_imap_ms / 1000.0)
            return {'items': [], 'uidvalidity': '7', 'folder': 'Drafts'}

        def scenario(nom, chemin, requete='', preparer=None):
            if preparer:
                preparer()
            froid = appeler(chemin, requete)
            if not froid['status'].startswith('200'):
                rapport['scenarios'].append({'scenario': nom, 'route': chemin, 'statut': froid['status'].split()[0]})
                return
            ttfb, totaux, details = [], [], []
            for _ in range(args.essais):
                r = appeler(chemin, requete)
                ttfb.append(r['ttfb'])
                totaux.append(r['total'])
                details.append(_server_timing(r['headers'].get('Server-Timing', '')))
            ligne = {'scenario': nom, 'route': chemin, 'ttfb_ms': dict(_resume(ttfb), froid=round(froid['ttfb'], 1)), 'total_ms': _resume(totaux),
                     'octets': r['octets'], 'cache_control': r['headers'].get('Cache-Control', '')}
            for cle in ('requetes_sqlite', 'commandes_imap', 'sqlite_ms', 'imap_ms', 'auth_ms', 'blocs_ms'):
                valeurs = [d.get(cle, 0.0) for d in details]
                ligne[cle] = {'p50': round(statistics.median(valeurs), 1), 'p95': _centile(valeurs, .95)}
            rapport['scenarios'].append(ligne)

        from agent import mailbox as mbx
        RealMailbox = mbx._MAILBOX_REELLE
        with patch('agent.model.Model.ask', side_effect=AssertionError('modèle interdit')), \
                patch('agent.model.Model.complete', side_effect=AssertionError('modèle interdit')), \
                patch('agent.mailbox.Mailbox', RealMailbox), patch('agent.mailbox.connect', side_effect=connexion_rapide), \
                patch('agent.drafts440.list_drafts', side_effect=brouillons):
            for n in tailles:
                def nouveau_registre(n=n):
                    private_json(base / 'matters.json', _dossiers(n))
                    oublier_registre()
                scenario('dossiers_%d' % n, '/dossiers', preparer=nouveau_registre)
            scenario('aujourdhui', '/aujourdhui')
            scenario('a_relire', '/courriels')
            scenario('reception_gabarit', '/courriels', 'vue=boite')
            scenario('reception_imap', '/api440/m5625/boite/liste', 'page=1')
            etat_imap['lent'] = True
            scenario('reception_imap_lent', '/api440/m5625/boite/liste', 'page=1')
            scenario('a_relire_imap_lent', '/courriels')
            etat_imap['lent'] = False
            scenario('recherche_accentuee', '/api440/m5625/boite/liste', 'page=1&q=proc%C3%A9dure')
        rapport['mesure_le'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        rapport['python'] = sys.version.split()[0]
    texte = json.dumps(rapport, ensure_ascii=False, indent=1)
    if args.sortie:
        Path(args.sortie).write_text(texte, encoding='utf-8')
    print(texte)
    return 0


if __name__ == '__main__':
    sys.exit(main())

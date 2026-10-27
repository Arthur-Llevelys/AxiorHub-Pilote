"""5.6.75 : connecteur Claude (OAuth 2.1 + MCP distant), pseudonymisation stable, brouillons et notes jamais envoyés.

Application réelle (WSGI) en version serveur ; valeurs fictives (domaine example.test, dossier DOS-001 « Client DEMO »).
"""
import base64
import email
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
import zipfile

import test_v5648 as t48
from agent import claude5675 as cl, connexion5648 as cx, standalone_auth, shell501
from agent.common import private_json
from fake_imap440 import FakeIMAP

CALLBACK = 'https://claude.ai/api/mcp/auth_callback'
VERIFIER = 'verificateur-pkce-fictif-' + 'x' * 30


def challenge(verifier=VERIFIER):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


class Base(t48.Base):
    def enable(self, **values):
        db = cl.connect(self.state)
        try:
            for name, value in {'actif': '1', **values}.items():
                db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (cl.SETTINGS[name][0], value))
            db.commit()
        finally:
            db.close()

    def raw(self, path, method='GET', body=b'', ctype='application/x-www-form-urlencoded', extra=None, prefixed=True):
        env = {'REQUEST_METHOD': method, 'PATH_INFO': (t48.PREFIX if prefixed else '') + path, 'QUERY_STRING': '', 'HTTP_HOST': 'cabinet.example.test',
               'HTTP_X_FORWARDED_PROTO': 'https', 'wsgi.url_scheme': 'http', 'wsgi.input': io.BytesIO(body), 'CONTENT_LENGTH': str(len(body)),
               'CONTENT_TYPE': ctype, 'REMOTE_ADDR': '192.0.2.20'}
        if '?' in path:
            env['PATH_INFO'], env['QUERY_STRING'] = env['PATH_INFO'].split('?', 1)
        env.update(extra or {})
        out = {}

        def start(status, headers):
            out.update(status=status, headers=dict(headers))
        out['body'] = b''.join(self.app(env, start)).decode('utf-8', 'replace')
        return out

    def register(self, uris=(CALLBACK,), method='none'):
        r = self.raw('/oauth/inscription', 'POST', json.dumps({'client_name': 'Claude', 'redirect_uris': list(uris), 'token_endpoint_auth_method': method,
                                                                'grant_types': ['authorization_code', 'refresh_token']}).encode(), 'application/json')
        return r

    def authorize(self, client_id, cookie, state='etat-fictif'):
        q = urlencode({'response_type': 'code', 'client_id': client_id, 'redirect_uri': CALLBACK, 'code_challenge': challenge(),
                       'code_challenge_method': 'S256', 'state': state, 'scope': 'claudeai', 'resource': self.origin + t48.PREFIX + '/claude/mcp'})
        first = self.request('/oauth/autoriser', query=q, cookie=cookie)
        if first['status'] != '200 OK':
            return first, None
        page = first['body']
        ident = page.split('name="demande" value="')[1].split('"')[0]
        jeton = page.split('name="jeton" value="')[1].split('"')[0]
        done = self.request('/oauth/autoriser', 'POST', {'demande': ident, 'jeton': jeton, 'decision': 'oui', 'pseudo': '1'}, cookie=cookie)
        return first, done

    def tokens(self, cookie=None):
        cookie = cookie or self.admin_cookie()
        client = json.loads(self.register()['body'])
        _first, done = self.authorize(client['client_id'], cookie)
        code = parse_qs(urlsplit(done['headers']['Location']).query)['code'][0]
        r = self.raw('/oauth/jeton', 'POST', urlencode({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': CALLBACK,
                                                        'client_id': client['client_id'], 'code_verifier': VERIFIER}).encode())
        self.assertEqual(r['status'], '200 OK', r['body'])
        return client, json.loads(r['body'])

    def rpc(self, access, method, params=None, ident=1):
        body = json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}).encode()
        r = self.raw('/claude/mcp', 'POST', body, 'application/json', {'HTTP_AUTHORIZATION': 'Bearer ' + access,
                                                                       'HTTP_ACCEPT': 'application/json, text/event-stream'})
        return r

    def tool(self, access, name, args=None):
        r = self.rpc(access, 'tools/call', {'name': name, 'arguments': args or {}})
        self.assertEqual(r['status'], '200 OK', r['body'])
        result = json.loads(r['body'])['result']
        return result, result['content'][0]['text']


class OAuth(Base):
    def test_inactif_par_defaut_puis_metadonnees(self):
        self.assertEqual(self.raw('/.well-known/oauth-protected-resource')['status'], '404 Not Found')
        self.assertEqual(self.raw('/claude/mcp', 'POST', b'{}', 'application/json')['status'], '404 Not Found')
        self.enable()
        prm = self.raw('/.well-known/oauth-protected-resource' + t48.PREFIX + '/claude/mcp', prefixed=False)   # RFC 9728, chemin inséré
        self.assertEqual(prm['status'], '200 OK', prm['body'])
        data = json.loads(prm['body'])
        self.assertEqual(data['resource'], 'https://cabinet.example.test/agent-courriel/claude/mcp')
        self.assertEqual(data['authorization_servers'], ['https://cabinet.example.test/agent-courriel'])
        for path, prefixed in (('/.well-known/oauth-authorization-server' + t48.PREFIX, False), ('/.well-known/openid-configuration', True),
                               ('/.well-known/oauth-authorization-server', True)):
            meta = json.loads(self.raw(path, prefixed=prefixed)['body'])
            self.assertEqual(meta['issuer'], 'https://cabinet.example.test/agent-courriel')
            self.assertEqual(meta['code_challenge_methods_supported'], ['S256'])
            self.assertTrue(meta['registration_endpoint'].endswith('/oauth/inscription'))
        anonymous = self.raw('/claude/mcp', 'POST', b'{}', 'application/json')
        self.assertEqual(anonymous['status'], '401 Unauthorized')
        self.assertIn('resource_metadata="https://cabinet.example.test/agent-courriel/.well-known/oauth-protected-resource"',
                      anonymous['headers']['WWW-Authenticate'])
        # l'ancienne page « Connecteurs MCP » (/mcp) reste une page de l'interface, protégée par la connexion
        self.assertEqual(self.request('/mcp')['status'], '303 See Other')

    def test_inscription_limitee_aux_adresses_de_claude(self):
        self.enable()
        bad = self.register(('https://attaquant.example.test/retour',))
        self.assertEqual(bad['status'], '400 Bad Request')
        self.assertEqual(json.loads(bad['body'])['error'], 'invalid_redirect_uri')
        good = json.loads(self.register((CALLBACK, 'http://localhost:33418/callback'))['body'])
        self.assertTrue(good['client_id'].startswith('claude-'))
        self.assertEqual(good['token_endpoint_auth_method'], 'none')
        secret = json.loads(self.register(method='client_secret_post')['body'])
        self.assertTrue(secret['client_secret'])
        db = cl.connect(self.state)
        try:   # seule l'empreinte du secret est conservée
            self.assertIsNone(db.execute('SELECT 1 FROM claude5675_clients WHERE secret_hash=?', (secret['client_secret'],)).fetchone())
        finally:
            db.close()

    def test_parcours_complet_connexion_consentement_jetons(self):
        self.enable()
        client = json.loads(self.register()['body'])
        q = urlencode({'response_type': 'code', 'client_id': client['client_id'], 'redirect_uri': CALLBACK, 'code_challenge': challenge(),
                       'code_challenge_method': 'S256', 'state': 'etat-fictif'})
        anonymous = self.request('/oauth/autoriser', query=q)
        self.assertEqual(anonymous['status'], '303 See Other')
        suite = parse_qs(urlsplit(anonymous['headers']['Location']).query)['suite'][0]
        self.assertTrue(suite.startswith(t48.PREFIX + '/oauth/autoriser?demande='))
        login = self.login(suite=suite)
        self.assertEqual(login['headers']['Location'], suite)                       # retour à la demande après connexion
        cookie = self.token(login)
        consent = self.request(suite[len(t48.PREFIX):].split('?')[0], query=suite.split('?')[1], cookie=cookie)
        self.assertEqual(consent['status'], '200 OK', consent['body'][:400])
        self.assertIn('Autoriser', consent['body'])
        self.assertIn('imposée par le cabinet', consent['body'])                    # pseudonymisation imposée par défaut
        self.assertIn("form-action 'self' https://claude.ai", consent['headers']['Content-Security-Policy'])
        self.assertNotIn('<script', consent['body'])
        ident = consent['body'].split('name="demande" value="')[1].split('"')[0]
        forged = self.request('/oauth/autoriser', 'POST', {'demande': ident, 'jeton': 'faux', 'decision': 'oui'}, cookie=cookie)
        self.assertEqual(forged['status'], '403 Forbidden')
        _first, done = self.authorize(client['client_id'], cookie)
        location = done['headers']['Location']
        self.assertTrue(location.startswith(CALLBACK + '?'))
        params = parse_qs(urlsplit(location).query)
        self.assertEqual(params['state'], ['etat-fictif'])
        self.assertEqual(params['iss'], ['https://cabinet.example.test/agent-courriel'])
        wrong = self.raw('/oauth/jeton', 'POST', urlencode({'grant_type': 'authorization_code', 'code': params['code'][0], 'redirect_uri': CALLBACK,
                                                            'client_id': client['client_id'], 'code_verifier': 'mauvais-verificateur-' + 'y' * 30}).encode())
        self.assertEqual(json.loads(wrong['body'])['error'], 'invalid_grant')       # PKCE faux : code brûlé
        client2, tok = self.tokens(cookie)
        self.assertTrue(tok['access_token'].startswith(cl.TOKEN_PREFIX))
        self.assertEqual(tok['token_type'], 'Bearer')
        init = self.rpc(tok['access_token'], 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'essai'}})
        self.assertEqual(init['status'], '200 OK', init['body'])
        result = json.loads(init['body'])['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertIn('marqueurs', result['instructions'])
        renewed = self.raw('/oauth/jeton', 'POST', urlencode({'grant_type': 'refresh_token', 'refresh_token': tok['refresh_token'],
                                                              'client_id': client2['client_id']}).encode())
        self.assertEqual(renewed['status'], '200 OK', renewed['body'])
        again = self.raw('/oauth/jeton', 'POST', urlencode({'grant_type': 'refresh_token', 'refresh_token': tok['refresh_token'],
                                                            'client_id': client2['client_id']}).encode())
        self.assertEqual(json.loads(again['body'])['error'], 'invalid_grant')       # rotation : usage unique
        db = cl.connect(self.state)
        try:   # aucun jeton en clair dans la base
            self.assertIsNone(db.execute('SELECT 1 FROM claude5675_jetons WHERE token_hash=?', (tok['access_token'],)).fetchone())
        finally:
            db.close()

    def test_code_rejoue_ferme_l_acces_et_refus_de_l_avocat(self):
        self.enable()
        cookie = self.admin_cookie()
        client = json.loads(self.register()['body'])
        _f, done = self.authorize(client['client_id'], cookie)
        code = parse_qs(urlsplit(done['headers']['Location']).query)['code'][0]
        form = urlencode({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': CALLBACK, 'client_id': client['client_id'],
                          'code_verifier': VERIFIER}).encode()
        tok = json.loads(self.raw('/oauth/jeton', 'POST', form)['body'])
        self.assertEqual(self.rpc(tok['access_token'], 'ping')['status'], '200 OK')
        replay = self.raw('/oauth/jeton', 'POST', form)
        self.assertEqual(json.loads(replay['body'])['error'], 'invalid_grant')
        self.assertEqual(self.rpc(tok['access_token'], 'ping')['status'], '401 Unauthorized')
        q = urlencode({'response_type': 'code', 'client_id': client['client_id'], 'redirect_uri': CALLBACK, 'code_challenge': challenge(),
                       'code_challenge_method': 'S256', 'state': 's2'})
        page = self.request('/oauth/autoriser', query=q, cookie=cookie)['body']
        ident = page.split('name="demande" value="')[1].split('"')[0]
        jeton = page.split('name="jeton" value="')[1].split('"')[0]
        no = self.request('/oauth/autoriser', 'POST', {'demande': ident, 'jeton': jeton, 'decision': 'non'}, cookie=cookie)
        self.assertIn('error=access_denied', no['headers']['Location'])
        missing_pkce = self.request('/oauth/autoriser', query=urlencode({'response_type': 'code', 'client_id': client['client_id'], 'redirect_uri': CALLBACK}),
                                    cookie=cookie)
        self.assertIn('error=invalid_request', missing_pkce['headers']['Location'])

    def test_identite_publiee_cimd_et_port_local_libre(self):
        self.enable()
        meta = json.loads(self.raw('/.well-known/oauth-authorization-server')['body'])
        self.assertTrue(meta['client_id_metadata_document_supported'])
        self.assertIn('none', meta['token_endpoint_auth_methods_supported'])
        url = 'https://claude.ai/oauth/claude-code-client-metadata'
        doc = {'client_id': url, 'client_name': 'Claude Code', 'redirect_uris': ['http://localhost/callback', 'http://127.0.0.1/callback'],
               'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token']}
        cookie = self.admin_cookie()
        redirect = 'http://localhost:41234/callback'
        q = urlencode({'response_type': 'code', 'client_id': url, 'redirect_uri': redirect, 'code_challenge': challenge(),
                       'code_challenge_method': 'S256', 'state': 'cc'})
        with patch.object(cl, 'fetch_client_document', return_value=doc) as fetched:
            page = self.request('/oauth/autoriser', query=q, cookie=cookie)
            self.assertEqual(page['status'], '200 OK', page['body'][:300])
            self.assertIn('Claude Code', page['body'])
            self.assertIn('cet ordinateur (localhost:41234)', page['body'])            # adresse locale signalée sur l'écran d'accord
            self.assertEqual(fetched.call_count, 1)
            ident = page['body'].split('name="demande" value="')[1].split('"')[0]
            jeton = page['body'].split('name="jeton" value="')[1].split('"')[0]
            done = self.request('/oauth/autoriser', 'POST', {'demande': ident, 'jeton': jeton, 'decision': 'oui'}, cookie=cookie)
        self.assertTrue(done['headers']['Location'].startswith(redirect + '?code='))
        code = parse_qs(urlsplit(done['headers']['Location']).query)['code'][0]
        tok = self.raw('/oauth/jeton', 'POST', urlencode({'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect,
                                                          'client_id': url, 'code_verifier': VERIFIER}).encode())
        self.assertEqual(tok['status'], '200 OK', tok['body'])
        with patch.object(cl, 'fetch_client_document') as never:   # domaine hors d'Anthropic : jamais lu
            other = self.request('/oauth/autoriser', query=urlencode({'response_type': 'code', 'client_id': 'https://autre.example.test/client',
                                                                      'redirect_uri': redirect, 'code_challenge': challenge(),
                                                                      'code_challenge_method': 'S256'}), cookie=cookie)
            never.assert_not_called()
        self.assertEqual(other['status'], '400 Bad Request')
        self.assertTrue(cl.redirect_matches(['http://127.0.0.1/callback'], 'http://127.0.0.1:5000/callback'))
        self.assertFalse(cl.redirect_matches(['http://127.0.0.1/callback'], 'http://127.0.0.1:5000/autre'))
        self.assertFalse(cl.redirect_matches([CALLBACK], 'https://claude.ai/api/mcp/auth_callback/x'))

    def test_mot_de_passe_change_ferme_l_acces(self):
        self.enable()
        _client, tok = self.tokens()
        self.assertEqual(self.rpc(tok['access_token'], 'ping')['status'], '200 OK')
        data = json.loads(Path(self.auth).read_text(encoding='utf-8'))
        data['hash'] = hashlib.scrypt(b'autre-mot-de-passe-fictif', salt=bytes.fromhex(data['salt']), n=16384, r=8, p=1).hex()
        private_json(self.auth, data)
        self.assertEqual(self.rpc(tok['access_token'], 'ping')['status'], '401 Unauthorized')


class Outils(Base):
    def setUp(self):
        super().setUp()
        self.enable()
        db = sqlite3.connect(str(self.state / 'documents.sqlite3'))
        db.execute('CREATE TABLE IF NOT EXISTS docs (matter TEXT,path TEXT,etag TEXT,modified TEXT,text TEXT,error TEXT,PRIMARY KEY(matter,path))')
        db.execute('INSERT INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', '/Dossiers/DEMO/Courrier adverse.pdf', 'e1', '2026-10-01',
                                                            'Courrier de Maître ZORVAL pour Client DEMO : la facture du 3 mars reste impayée à ce jour.', ''))
        db.execute('INSERT INTO docs VALUES(?,?,?,?,?,?)', ('DOS-001', '/Dossiers/DEMO/Lettre confidentielle.pdf', 'e2', '2026-10-02',
                                                            'Correspondance confidentielle entre avocats.', ''))
        db.commit()
        db.close()
        _client, tok = self.tokens()
        self.access = tok['access_token']

    def test_outils_declares_et_pseudonymisation_stable(self):
        listed = json.loads(self.rpc(self.access, 'tools/list')['body'])['result']['tools']
        names = {t['name'] for t in listed}
        self.assertEqual(names, {t[0] for t in cl.TOOLS})
        self.assertTrue(all(t['annotations']['destructiveHint'] is False for t in listed))
        _r, text = self.tool(self.access, 'lister_dossiers')
        self.assertNotIn('Client DEMO', text)
        self.assertNotIn('DOS-001', text)
        data = json.loads(text)
        marker = data['dossiers'][0]['dossier']
        self.assertRegex(marker, r'^\[REFERENCE_\d+\]$')
        _r, again = self.tool(self.access, 'lister_dossiers')
        self.assertEqual(json.loads(again)['dossiers'][0]['dossier'], marker)        # marqueurs stables pour le compte
        _r, fiche = self.tool(self.access, 'fiche_dossier', {'dossier': marker})
        self.assertNotIn('ZORVAL', fiche)
        docs = json.loads(fiche)['documents']
        self.assertTrue(any(d.get('confidentiel') for d in docs))
        readable = next(d for d in docs if not d.get('confidentiel'))
        _r, read = self.tool(self.access, 'lire_document', {'chemin': readable['chemin']})
        self.assertIn('facture du 3 mars', read)
        self.assertNotIn('ZORVAL', read)
        secret = next(d for d in docs if d.get('confidentiel'))
        res, refused = self.tool(self.access, 'lire_document', {'chemin': secret['chemin']})
        self.assertTrue(res['isError'])
        self.assertIn('document_confidentiel_non_transmis', refused)

    def test_note_deposee_avec_les_vraies_donnees(self):
        _r, text = self.tool(self.access, 'lister_dossiers')
        marker = json.loads(text)['dossiers'][0]['dossier']
        _r, fiche = self.tool(self.access, 'fiche_dossier', {'dossier': marker})
        client_marker = json.loads(fiche)['client']
        with patch('agent.document_projects._dav', lambda desk: self.nc):
            res, out = self.tool(self.access, 'deposer_note', {'dossier': marker, 'titre': 'Synthèse', 'nature': 'note',
                                                              'texte': 'Point sur le dossier de %s.\n[PERSONNE_99] reste à identifier.' % client_marker})
        self.assertFalse(res['isError'], out)
        path = next(p for kind, p in self.nc.puts if kind == 'create' and 'Note interne - Synthèse' in p)
        self.assertTrue(path.startswith('/Dossiers/DEMO/'))
        with zipfile.ZipFile(io.BytesIO(self.nc.files[path])) as z:
            xml = z.read('word/document.xml').decode('utf-8')
        self.assertIn('Client DEMO', xml)                                            # marqueur rétabli au dépôt
        self.assertIn('Préparé avec Claude', xml)
        self.assertIn('[PERSONNE_99]', xml)                                          # marqueur inconnu : laissé et signalé
        self.assertIn('PERSONNE_99', out)

    def test_brouillon_depose_jamais_envoye(self):
        before = len(FakeIMAP.folders['Drafts'])
        res, out = self.tool(self.access, 'preparer_brouillon', {'destinataires': 'client@example.test', 'objet': 'Point d’étape',
                                                                  'corps': 'Bonjour,\nVoici le point demandé.'})
        self.assertFalse(res['isError'], out)
        self.assertEqual(json.loads(out)['envoi'], False)
        self.assertEqual(len(FakeIMAP.folders['Drafts']), before + 1)
        self.assertEqual(len(FakeIMAP.folders['Sent']), 0)
        raw = list(FakeIMAP.folders['Drafts'].values())[-1]['raw']
        msg = email.message_from_bytes(raw if isinstance(raw, bytes) else raw.encode())
        self.assertEqual(msg['X-AxiorHub-Origine'], 'claude5675')
        self.assertIn('\\Draft', list(FakeIMAP.folders['Drafts'].values())[-1]['flags'])
        bad, why = self.tool(self.access, 'preparer_brouillon', {'destinataires': '[COURRIEL_42]', 'corps': 'Texte.'})
        self.assertTrue(bad['isError'], why)

    def test_fait_propose_avec_extrait_exact_seulement(self):
        _r, text = self.tool(self.access, 'lister_dossiers')
        marker = json.loads(text)['dossiers'][0]['dossier']
        _r, fiche = self.tool(self.access, 'fiche_dossier', {'dossier': marker})
        path = next(d['chemin'] for d in json.loads(fiche)['documents'] if not d.get('confidentiel'))
        bad, why = self.tool(self.access, 'proposer_fait', {'dossier': marker, 'titre': 'Facture impayée', 'contenu': 'Facture impayée',
                                                            'chemin_source': path, 'extrait': 'la facture a été payée intégralement hier'})
        self.assertTrue(bad['isError'])
        self.assertIn('extrait_absent_du_document', why)
        ok, out = self.tool(self.access, 'proposer_fait', {'dossier': marker, 'titre': 'Facture impayée', 'contenu': 'La facture du 3 mars est impayée.',
                                                           'chemin_source': path, 'extrait': 'la facture du 3 mars reste impayée à ce jour', 'type': 'amount'})
        self.assertFalse(ok['isError'], out)
        from agent.desk import Desk
        desk = Desk(self.f.c)
        try:
            row = desk.db.execute("SELECT status,origin FROM legal_memory_records WHERE matter='DOS-001'").fetchone()
        finally:
            desk.db.close()
        self.assertEqual(tuple(row), ('suggested', 'claude5675'))

    def test_savoir_faire_propose_sans_retablir_les_marqueurs(self):
        _r, text = self.tool(self.access, 'lister_dossiers')
        client_marker = json.loads(text)['dossiers'][0]['client']
        res, out = self.tool(self.access, 'proposer_savoir_faire', {'nature': 'structure_courrier', 'titre': 'Relance de facture',
                                                                     'contenu': 'Rappeler la facture, le délai, puis la suite envisagée pour %s.' % client_marker})
        self.assertFalse(res['isError'], out)
        from agent.desk import Desk
        desk = Desk(self.f.c)
        try:
            row = desk.db.execute("SELECT body,status,author FROM s59_versions WHERE title='Relance de facture'").fetchone()
        finally:
            desk.db.close()
        self.assertNotIn('Client DEMO', row['body'])                                 # jamais une donnée réelle dans la bibliothèque
        self.assertEqual(row['status'], 'proposee')
        self.assertTrue(row['author'].startswith('claude:'))

    def test_dossier_exclu_de_l_ia_externe_invisible(self):
        cfg = json.loads(Path(self.config).read_text(encoding='utf-8'))
        cfg['hybrid_routing'] = {'excluded_matters': ['DOS-001']}
        private_json(self.config, cfg)
        _r, text = self.tool(self.access, 'lister_dossiers')
        self.assertEqual(json.loads(text)['total'], 0)
        res, why = self.tool(self.access, 'lire_document', {'chemin': '/Dossiers/DEMO/Courrier adverse.pdf'})
        self.assertTrue(res['isError'])

    def test_lecture_seule_ressources_et_invites(self):
        prompts = json.loads(self.rpc(self.access, 'prompts/list')['body'])['result']['prompts']
        self.assertIn('preparer_reponse', {p['name'] for p in prompts})
        got = json.loads(self.rpc(self.access, 'prompts/get', {'name': 'preparer_reponse', 'arguments': {'dossier': 'X'}})['body'])['result']
        self.assertIn('preparer_brouillon', got['messages'][0]['content']['text'])
        read = json.loads(self.rpc(self.access, 'resources/read', {'uri': 'axiorhub://consignes'})['body'])['result']
        self.assertIn('rien n’est jamais envoyé', read['contents'][0]['text'])
        self.enable(ecriture='0')
        names = {t['name'] for t in json.loads(self.rpc(self.access, 'tools/list')['body'])['result']['tools']}
        self.assertNotIn('preparer_brouillon', names)
        res, why = self.tool(self.access, 'deposer_note', {'titre': 'T', 'texte': 'x'})
        self.assertTrue(res['isError'])
        notif = self.raw('/claude/mcp', 'POST', json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).encode(), 'application/json',
                         {'HTTP_AUTHORIZATION': 'Bearer ' + self.access})
        self.assertEqual(notif['status'], '202 Accepted')
        self.assertEqual(self.raw('/claude/mcp', 'GET', extra={'HTTP_AUTHORIZATION': 'Bearer ' + self.access})['status'], '405 Method Not Allowed')


class Reglages(Base):
    def test_section_reglages_et_telechargements(self):
        cookie = self.admin_cookie()
        r = self.api('m5675/reglages', {'actif': '1', 'pseudo_impose': '1', 'ecriture': '1'}, cookie)
        self.assertTrue(r['status'].startswith('200'), r['body'])
        page = self.request('/parametres', query='rubrique=ia', cookie=cookie)
        self.assertIn('https://cabinet.example.test/agent-courriel/claude/mcp', page['body'])
        self.assertIn('Réglages du cabinet', page['body'])
        self.tokens(cookie)
        page = self.request('/parametres', query='rubrique=ia', cookie=cookie)
        self.assertIn('data-c5671="m5675/retirer"', page['body'])                    # connexion du compte, retirable
        grant = cl.grants(self.state, cx.admin_user({'username': 'admin'}))[0][0]
        self.assertTrue(self.api('m5675/pseudonymiser', {'id': grant['id'], 'valeur': '0'}, cookie)['status'].startswith('400'))   # imposée
        self.assertTrue(self.api('m5675/retirer', {'id': grant['id']}, cookie)['status'].startswith('200'))
        self.assertEqual(cl.grants(self.state, cx.admin_user({'username': 'admin'}))[0], [])
        skill = self.request('/api440/m5675/competence.zip', cookie=cookie)
        self.assertEqual(skill['status'], '200 OK')
        z = self.call('/api440/m5675/plugin.zip', auth=False, extra={'HTTP_COOKIE': '%s=%s' % (cx.COOKIE, cookie)})
        with zipfile.ZipFile(io.BytesIO(z['body'])) as archive:
            mcp = json.loads(archive.read('axiorhub-claude/axiorhub/.mcp.json'))
            self.assertEqual(mcp['mcpServers']['axiorhub']['url'], 'https://cabinet.example.test/agent-courriel/claude/mcp')
            self.assertIn('name: axiorhub-cabinet', archive.read('axiorhub-claude/axiorhub/skills/axiorhub-cabinet/SKILL.md').decode())

    def test_navigateur_supervise_dans_toutes_les_fonctions(self):
        from agent import fonctions5631, navigation5643
        self.assertEqual(navigation5643.visibility('navigateur'), 'panel')
        self.assertEqual(navigation5643.canonical('mobile'), 'aujourdhui')
        html = fonctions5631.panel_html('/agent-courriel', '/aujourdhui', self.f.c)
        self.assertRegex(html, r'<li data-search="[^"]*"><a href="/agent-courriel/navigateur"')   # visible sans recherche
        orphans = [fid for fid, *_ in fonctions5631.FUNCTIONS if navigation5643.visibility(fid) == 'sub' and fid not in navigation5643.PARENT]
        self.assertEqual(orphans, [])                                                  # aucune fonction trouvable seulement par la recherche

    def test_droits_et_cablage(self):
        self.assertTrue(standalone_auth.allowed('avocat', 'POST', '/api440/m5675/retirer'))
        self.assertTrue(standalone_auth.allowed('assistant', 'POST', '/api440/m5675/pseudonymiser'))
        self.assertFalse(standalone_auth.allowed('avocat', 'POST', '/api440/m5675/reglages'))
        self.assertIn('v5675.css', shell501.BUNDLE)
        self.assertFalse(cl.available({'poste': True}))
        self.assertIn('version serveur', cl.settings_html('/tmp', {'poste': True}, None))
        self.assertTrue(cl.redirect_allowed(CALLBACK))
        self.assertTrue(cl.redirect_allowed('http://127.0.0.1:5173/callback'))
        self.assertFalse(cl.redirect_allowed('https://claude.ai.example.test/api/mcp/auth_callback'))
        self.assertFalse(cl.redirect_allowed('http://localhost.example.test/callback'))


if __name__ == '__main__':
    unittest.main()

"""5.6.52 : routines personnalisées (R01 à R09) et connexions API utilisables par l'IA (I01 à I15).

Aucun service extérieur n'est appelé : les quinze fournisseurs répondent depuis un serveur simulé (fabrique HTTP injectée), avec des
données fictives (domaine example.test). Ces tests contrôlent le circuit logiciel ; ils ne qualifient ni les contrats réels des
fournisseurs ni une réception en bac à sable ou en production.
"""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import urllib.parse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from agent import adaptateurs5652 as ad, connexions5652 as cx, routines5652 as rt, routines520
from agent.common import Stop
from agent.desk import Desk
from agent.erreurs5634 import http_error
import test_agent as fixtures
import test_d5651 as d51

MID = 'DOS-001'


# ---------------------------------------------------------------------------------------------------- serveur simulé
class Fake:
    """Fabrique HTTP : chaque origine reçoit un client qui enregistre les appels et répond selon ``routes``."""

    def __init__(self):
        self.calls = []
        self.routes = []
        self.fail = {}

    def on(self, method, contains, response, status=200):
        self.routes.append((method, contains, response, status))

    def __call__(self, origin):
        fake = self

        class Client:
            def request(self, method, url, data=None, headers=None, limit=0):
                fake.calls.append((method, url, data, dict(headers or {})))
                for key, code in list(fake.fail.items()):
                    if key in url:
                        raise http_error(code, method, url) if code != 'reseau' else Stop('delai_http_depasse')
                for m, contains, response, status in reversed(fake.routes):
                    if m == method and contains in url:
                        if status >= 400:
                            raise http_error(status, method, url)
                        body = response(method, url, data, headers) if callable(response) else response
                        return body if isinstance(body, bytes) else json.dumps(body).encode()
                raise http_error(404, method, url)
        return Client()


SECRETS = {'api_key': 'cle-fictive-123456', 'token': 'jeton-fictif-123456', 'private_key': 'cle-privee-fictive', 'client_secret': 'secret-fictif-123456',
           'api_token': 'jeton-societe-fictif', 'access_token': 'sandbox_jeton_fictif', 'password': 'mot-de-passe-fictif', 'technical_password': 'mdp-technique-fictif'}
PARAMS = {'sender_account': 'expediteur@example.test', 'client_id': 'client-fictif-abcdef', 'account_id': 'compte-fictif', 'company_id': '42',
          'username': 'compte@example.test', 'technical_login': 'TECH_FICTIF', 'tenant': 'common'}


def ar24_body(date_value, payload):
    """Réponse chiffrée comme AR24 (clé = SHA-256(date + clé privée)[:32], IV = SHA-256(SHA-256(clé))[:16])."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pk = SECRETS['private_key']
    key = hashlib.sha256((date_value + pk).encode()).hexdigest()[:32].encode()
    iv = hashlib.sha256(hashlib.sha256(pk.encode()).hexdigest().encode()).hexdigest()[:16].encode()
    padder = padding.PKCS7(128).padder()
    raw = padder.update(json.dumps(payload).encode()) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return json.dumps({'status': 'SUCCESS', 'result': base64.b64encode(enc.update(raw) + enc.finalize()).decode()}).encode()


def ar24_route(payload):
    def handler(method, url, data, headers):
        q = parse_qs(urlsplit(url).query)
        if 'date' in q:
            stamp = q['date'][0]
        else:
            stamp = data.decode('latin-1').split('name="date"\r\n\r\n', 1)[-1].split('\r\n', 1)[0] if b'form-data' in data else parse_qs(data.decode())['date'][0]
        assert headers['signature'] == ad.AR24.signature(SECRETS['private_key'], stamp)
        return ar24_body(stamp, payload)
    return handler


def install_identity_routes(fake):
    fake.on('GET', 'sandbox.ar24.fr/api/user', ar24_route({'id': 7, 'email': 'expediteur@example.test', 'company': 'Cabinet fictif'}))
    fake.on('GET', '/signature_requests?limit=1', {'data': []})
    fake.on('POST', 'oauth/token', {'access_token': 'jeton-piste-fictif', 'expires_in': 3600})
    fake.on('POST', '/consult/getArticle', {'article': {'id': 'LEGIARTI000032041571', 'num': '1240', 'etat': 'VIGUEUR', 'texte': 'Tout fait quelconque…'}})
    fake.on('POST', 'lf-engine-app/search', {'results': [{'titles': [{'title': 'Code civil'}], 'sections': [{'extracts': [{'id': 'LEGIARTI000032041571', 'num': '1240'}]}]}]})
    fake.on('GET', '/healthcheck', {'status': 'disponible'})
    fake.on('GET', 'judilibre/v1.0/search', {'results': [{'id': 'dec-fictive', 'jurisdiction': 'cc', 'chamber': 'civ3', 'decision_date': '2024-01-11',
                                                         'number': '22-10.000', 'summary': 'Sommaire fictif'}], 'total': 1})
    fake.on('GET', '/recherche/article', {'resultats': [{'id': 'LEGIARTI000032041571'}], 'total': 1})
    fake.on('GET', '/recherche/decision', {'resultats': [{'id': 'pj-1', 'numero': '22-10.000', 'juridiction': 'Cour de cassation', 'date_decision': '2024-01-11'}], 'total': 1})
    fake.on('GET', 'pennylane.com/api/external/v2/me', {'user': {'email': 'cabinet@example.test'}, 'company': {'id': 42, 'name': 'Cabinet fictif'}})
    fake.on('GET', 'account-d.docusign.com/oauth/userinfo', {'email': 'cabinet@example.test', 'accounts': [{'account_id': 'compte-fictif', 'account_name': 'Démo',
                                                                                                        'base_uri': 'https://demo.docusign.net'}]})
    fake.on('POST', 'superpdp.tech/oauth2/token', {'access_token': 'jeton-spdp', 'expires_in': 3600})
    fake.on('GET', '/v1.beta/companies/me', {'name': 'Cabinet fictif', 'siren': '000000000'})
    fake.on('GET', 'b2brouter.net/accounts', {'accounts': [{'identifier': 'compte-fictif', 'name': 'Cabinet fictif'}]})
    fake.on('GET', '/creditors?limit=1', {'creditors': [{'id': 'CR123', 'name': 'Cabinet fictif'}]})
    fake.on('GET', 'cpro/transverses/v1/health-check', {'codeRetour': 0})
    fake.on('POST', 'connexion.sandbox.maileva.net', {'access_token': 'jeton-maileva', 'expires_in': 600})
    fake.on('GET', 'maileva.net/mail/v2/sendings?', {'count': 0, 'sendings': []})
    fake.on('GET', 'graph.microsoft.com/v1.0/me', {'userPrincipalName': 'avocat@example.test'})
    fake.on('GET', 'openidconnect.googleapis.com/v1/userinfo', {'email': 'avocat@example.test'})
    fake.on('GET', 'axonaut.com/api/v2/me', {'email': 'cabinet@example.test'})


class Base(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EngineTests('test_observation_has_no_mail_write')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'Dossiers/DEMO').mkdir(parents=True)
        (root / 'Dossiers/DEMO/Convention.pdf').write_bytes(b'%PDF-1.4 convention fictive')
        self.f.c['nextcloud'] = {**self.f.c['nextcloud'], 'local_path': str(root)}
        self.root = root
        self.desk = Desk(self.f.c)
        self.addCleanup(self.desk.db.close)
        self.fake = Fake()
        install_identity_routes(self.fake)
        p = patch.object(cx, 'HTTP_FACTORY', self.fake)
        p.start()
        self.addCleanup(p.stop)
        ad.ClientCredentialsMixin._cache.clear()
        ad.Maileva._cache.clear()

    def connect(self, provider, oauth=False, caps=None):
        spec = cx.PROVIDERS[provider]
        data = {'provider': provider, 'environment': 'test'}
        for f in spec['fields']:
            data[f['name']] = SECRETS.get(f['name'], '') if f['secret'] else PARAMS.get(f['name'], '')
        for cap in caps or ():
            data['cap_' + cap.replace('.', '_')] = True
        view = cx.save(self.desk, 'avocat@example.test', 'avocat', data)
        if oauth:
            cx._store_tokens(self.desk, view['id'], {'access_token': 'jeton-oauth-fictif', 'refresh_token': 'renouvellement-fictif', 'expires_in': 3600},
                             cx._row(self.desk, view['id'])['token_version'])
        return view


# ---------------------------------------------------------------------------------------------------- connexions
class Connections(Base):
    def test_quinze_fournisseurs_test_reel_simule_et_aucun_secret_renvoye(self):
        self.assertEqual(len(cx.PROVIDERS), 15)
        for pid in cx.PROVIDERS:
            oauth = bool(cx.PROVIDERS[pid].get('oauth'))
            view = self.connect(pid, oauth)
            self.assertIn(view['etat'], ('acces_enregistres', 'a_autoriser'), pid)              # enregistré ≠ vérifié
            out = cx.test(self.desk, 'avocat@example.test', 'avocat', view['id'])
            self.assertTrue(out['ok'], (pid, out.get('erreur')))
            self.assertEqual(out['etat'], 'verifiee_lecture', pid)
            dump = json.dumps(cx.public_view(self.desk, cx._row(self.desk, view['id'])), ensure_ascii=False)
            for secret in SECRETS.values():
                self.assertNotIn(secret, dump, pid)
        page = cx.settings_html(self.desk, '/p', 'avocat@example.test', 'avocat', {}, 'https://pilote.example.test/p/connexions-retour')
        for secret in SECRETS.values():
            self.assertNotIn(secret, page)
        for label in ('Obtenir mes accès', 'Consulter la documentation', 'Rechercher le droit', 'Signer et envoyer', 'Facturer et encaisser',
                      'Messagerie, agenda et documents', 'Vérifiée pour la lecture', 'Connecter mon compte'):
            self.assertIn(label, page)
        stored = [p for p in (Path(self.desk.c['state_dir']) / 'vault' / 'connexions5652').rglob('*.secret')]
        self.assertTrue(stored)
        self.assertNotIn(SECRETS['api_key'], ''.join(p.read_text() for p in stored))            # coffre chiffré

    def test_ar24_signature_officielle_et_reponse_dechiffree(self):
        self.assertEqual(ad.AR24.signature('7X9gx9E3Qx4EiUdB63nc', '2021-05-26 14:00:00'), 'bDop0cbjKpkySlpvnNGvBMg7PuYFFgPPqTTS2RAHoY0=')
        payload = ar24_body('2026-10-16 10:00:00', {'id': 3})
        self.assertEqual(json.loads(ad.AR24.decrypt(SECRETS['private_key'], '2026-10-16 10:00:00', json.loads(payload)['result'])), {'id': 3})

    def test_echecs_expiree_indisponible_et_secret_vide_conserve(self):
        view = self.connect('youtrust')
        self.fake.fail['/signature_requests'] = 401
        self.assertEqual(cx.test(self.desk, 'avocat@example.test', 'avocat', view['id'])['etat'], 'expiree')
        self.fake.fail['/signature_requests'] = 503
        self.assertEqual(cx.test(self.desk, 'avocat@example.test', 'avocat', view['id'])['etat'], 'indisponible')
        self.fake.fail.clear()
        cx.save(self.desk, 'avocat@example.test', 'avocat', {'provider': 'youtrust', 'id': view['id'], 'api_key': ''})   # champ vide : secret conservé
        self.assertTrue(cx.test(self.desk, 'avocat@example.test', 'avocat', view['id'])['ok'])
        cx.clear_secret(self.desk, 'avocat@example.test', 'avocat', view['id'], 'api_key')
        self.assertEqual(cx.public_view(self.desk, cx._row(self.desk, view['id']))['etat'], 'non_configuree')

    def test_outils_seulement_si_verifies_et_lecture_par_l_agent(self):
        view = self.connect('judilibre_piste')
        self.assertNotIn('jurisprudence.rechercher', [t['outil'] for t in cx.available_tools(self.desk, 'cabinet')])   # non testée : pas d'outil
        cx.test(self.desk, 'avocat@example.test', 'avocat', view['id'])
        self.assertIn('jurisprudence.rechercher', [t['outil'] for t in cx.available_tools(self.desk, 'cabinet')])
        out = cx.call_tool(self.desk, 'cabinet', 'avocat', 'jurisprudence.rechercher', {'query': 'responsabilité du bailleur'})
        self.assertEqual(out['resultat']['resultats'][0]['numero'], '22-10.000')
        self.assertNotIn(SECRETS['client_secret'], json.dumps(out))

        class Model:
            def complete(self, messages, **kw):
                return json.dumps({'appels': [{'outil': 'jurisprudence.rechercher', 'parametres': {'query': 'trouble de jouissance bail commercial'}},
                                              {'outil': 'signature.preparer', 'parametres': {}}]})       # un outil d'écriture proposé est ignoré
        sources = cx.agent_research(self.desk, 'cabinet', 'avocat', 'Quelle jurisprudence sur le trouble de jouissance ?', MID, Model())
        self.assertEqual([s['outil'] for s in sources], ['jurisprudence.rechercher'])
        from agent import juridique5649
        legal = juridique5649.research(self.desk, MID, 'Quelle est la jurisprudence sur le trouble de jouissance du preneur ?', 'assistant')
        self.assertTrue(any(s['statut'] == 'piste_a_verifier' and 'Judilibre' in s.get('fournisseur', '') for s in legal['sources']))
        with self.assertRaisesRegex(Stop, 'outil_a_effet_externe'):
            cx.call_tool(self.desk, 'cabinet', 'avocat', 'signature.preparer', {})

    def test_annuaire_public_des_entreprises(self):
        self.fake.on('GET', 'recherche-entreprises.api.gouv.fr/search', {'results': [{'siren': '000000000', 'nom_complet': 'SOCIETE FICTIVE', 'siege': {'adresse': 'Paris'},
                                                                                      'etat_administratif': 'A', 'dirigeants': [{'nom': 'DIRIGEANT FICTIF'}]}]})
        out = cx.call_tool(self.desk, 'avocat@example.test', 'avocat', 'entreprise.rechercher', {'siren': '000000000'})
        self.assertEqual(out['resultat']['resultats'][0]['nom'], 'SOCIETE FICTIVE')
        self.assertIn('ne prouvent pas le pouvoir de signer', out['resultat']['limite'])

    def test_oauth_pkce_etat_et_renouvellement_atomique(self):
        view = self.connect('microsoft_graph')
        start = cx.oauth_start(self.desk, 'avocat@example.test', 'avocat', view['id'], 'https://pilote.example.test/p/connexions-retour')
        q = parse_qs(urlsplit(start['url']).query)
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertIn('Calendars.Read', q['scope'][0])
        self.assertNotIn('Mail.ReadWrite', q['scope'][0])                                      # écriture non demandée tant qu'elle n'est pas activée
        with self.assertRaisesRegex(Stop, 'etat_oauth_invalide'):
            cx.oauth_finish(self.desk, 'autre@example.test', {'state': q['state'][0], 'code': 'code-fictif'})   # autre compte
        start = cx.oauth_start(self.desk, 'avocat@example.test', 'avocat', view['id'], 'https://pilote.example.test/p/connexions-retour')
        state = parse_qs(urlsplit(start['url']).query)['state'][0]
        self.fake.on('POST', 'login.microsoftonline.com/common/oauth2/v2.0/token', {'access_token': 'jeton-ms', 'refresh_token': 'r-ms', 'expires_in': 3600})
        out = cx.oauth_finish(self.desk, 'avocat@example.test', {'state': state, 'code': 'code-fictif'})
        self.assertTrue(out['ok'])
        token_call = [c for c in self.fake.calls if 'oauth2/v2.0/token' in c[1]][-1]
        self.assertIn(b'code_verifier=', token_call[2])
        row = cx._row(self.desk, view['id'])
        self.assertFalse(cx._store_tokens(self.desk, view['id'], {'access_token': 'concurrent'}, row['token_version'] - 1))   # renouvellement concurrent
        cx.disconnect(self.desk, 'avocat@example.test', 'avocat', view['id'])
        self.assertEqual(cx.public_view(self.desk, cx._row(self.desk, view['id']))['etat'], 'a_autoriser')


class Operations(Base):
    def setUp(self):
        super().setUp()
        self.view = self.connect('youtrust', caps=('signature.send_approved',))
        cx.test(self.desk, 'avocat@example.test', 'avocat', self.view['id'])
        self.fake.on('POST', '/v3/signature_requests', {'id': 'sr-1', 'status': 'draft'})
        self.fake.on('POST', '/signature_requests/sr-1/documents', {'id': 'doc-1'})
        self.fake.on('POST', '/signature_requests/sr-1/signers', {'id': 'sg-1'})
        self.fake.on('POST', '/signature_requests/sr-1/activate', {'status': 'ongoing'})

    def prepare(self):
        return cx.prepare_from_form(self.desk, 'avocat@example.test', 'avocat', {
            'outil': 'signature.preparer', 'matter': MID, 'document': '/Dossiers/DEMO/Convention.pdf', 'title': 'Convention d’honoraires fictive',
            'first_name': 'Alice', 'last_name': 'Fictive', 'email': 'alice@example.test'})

    def test_preparer_valider_une_fois_suivre_et_archiver(self):
        op = self.prepare()
        self.assertEqual(op['state'], 'a_valider')
        self.assertEqual(self.prepare()['id'], op['id'])                                        # même contenu : même opération
        self.assertFalse([c for c in self.fake.calls if c[0] == 'POST' and 'signature_requests' in c[1]])   # rien d'envoyé
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            cx.validate(self.desk, 'assistant@example.test', 'assistant', op['id'], op['version'])
        out = cx.validate(self.desk, 'avocat@example.test', 'avocat', op['id'], op['version'])
        self.assertEqual((out['state'], out['remote_ref']), ('envoyee', 'sr-1'))
        again = cx.validate(self.desk, 'avocat@example.test', 'avocat', op['id'], op['version'])   # second clic
        self.assertEqual(again['state'], 'envoyee')
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'POST' and c[1].endswith('/v3/signature_requests')]), 1)
        self.assertEqual(cx.public_view(self.desk, cx._row(self.desk, self.view['id']))['etat'], 'verifiee_ecriture')
        self.fake.on('GET', '/signature_requests/sr-1', {'status': 'done', 'signers': [{'id': 'sg-1', 'status': 'signed'}]})
        self.fake.on('GET', '/signature_requests/sr-1/documents/download', b'%PDF signe fictif')
        self.fake.on('GET', '/signature_requests/sr-1/audit_trails/download', b'%PDF audit fictif')
        cx.poll(self.desk)
        row = self.desk.db.execute('SELECT * FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()
        self.assertEqual(row['state'], 'terminee')
        self.assertEqual((self.root / 'Dossiers/DEMO/Convention - document_signe.pdf').read_bytes(), b'%PDF signe fictif')
        self.assertEqual((self.root / 'Dossiers/DEMO/Convention.pdf').read_bytes(), b'%PDF-1.4 convention fictive')   # original intact

    def test_document_modifie_depuis_preparation_et_reponse_perdue(self):
        op = self.prepare()
        (self.root / 'Dossiers/DEMO/Convention.pdf').write_bytes(b'%PDF-1.4 convention MODIFIEE')
        with self.assertRaisesRegex(Stop, 'document_modifie_depuis_validation'):
            cx.validate(self.desk, 'avocat@example.test', 'avocat', op['id'], op['version'])
        self.assertEqual(self.desk.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()[0], 'a_revalider')
        op2 = self.prepare()
        self.fake.fail['/signers'] = 'reseau'                                                   # réponse perdue au milieu de l'envoi
        with self.assertRaises(Stop):
            cx.validate(self.desk, 'avocat@example.test', 'avocat', op2['id'], op2['version'])
        self.assertEqual(self.desk.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (op2['id'],)).fetchone()[0], 'resultat_a_verifier')
        self.fake.fail.clear()
        self.fake.on('GET', '/signature_requests?external_id=', {'data': [{'id': 'sr-1', 'external_id': op2['id']}]})
        self.fake.on('GET', '/signature_requests/sr-1', {'status': 'ongoing', 'signers': []})
        posts = len([c for c in self.fake.calls if c[0] == 'POST'])
        cx.poll(self.desk)                                                                      # rapprochement, sans second envoi
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'POST']), posts)
        self.assertEqual(self.desk.db.execute('SELECT remote_ref FROM connexion_ops5652 WHERE id=?', (op2['id'],)).fetchone()[0], 'sr-1')
        items = cx.queue_items(self.desk, '', 'avocat@example.test', 'avocat')
        self.assertTrue(all(i['class'] == 'engagement' for i in items))

    def test_deconnexion_annule_les_operations_non_envoyees(self):
        op = self.prepare()
        cx.disconnect(self.desk, 'avocat@example.test', 'avocat', self.view['id'])
        self.assertEqual(self.desk.db.execute('SELECT state FROM connexion_ops5652 WHERE id=?', (op['id'],)).fetchone()[0], 'annulee')


# ---------------------------------------------------------------------------------------------------- routines
class Routines(Base):
    def form(self, **kw):
        base = {'title': 'Point du matin', 'task_type': 'nouveautes', 'freq': 'jours', 'day_1': True, 'day_2': True, 'day_3': True, 'day_4': True, 'day_5': True,
                'time': '08:30', 'scope': 'personnel', 'request_key': 'cle-demande-0001'}
        base.update(kw)
        return base

    def test_migration_stable_sans_reactivation_ni_rattrapage(self):
        cfg = routines520.settings(self.desk)
        cfg['bilan']['enabled'] = False
        self.desk.setting('routines520', cfg)
        rt.migrate(self.desk)
        rows = {r['id']: r for r in self.desk.db.execute('SELECT * FROM routines5652')}
        self.assertEqual(set(rows), {'r520-briefing', 'r520-tri', 'r520-bilan'})
        self.assertEqual(rows['r520-bilan']['enabled'], 0)
        self.assertFalse(rt.migrate(self.desk))                                                 # relançable, sans effet
        self.assertTrue(self.desk.settings('routines5652:backup520'))
        lst = rt.listing(self.desk, 'avocat@example.test', 'avocat')['routines']
        brief = next(r for r in lst if r['id'] == 'r520-briefing')
        rt.update(self.desk, 'avocat@example.test', 'avocat', 'r520-briefing', {'time': '06:45'}, brief['version'])
        self.assertEqual(routines520.settings(self.desk)['briefing']['time'], '06:45')        # mêmes réglages dans Paramètres

    def test_creer_modifier_conflit_dupliquer_supprimer(self):
        r = rt.create(self.desk, 'avocat@example.test', 'avocat', self.form())
        self.assertEqual(rt.create(self.desk, 'avocat@example.test', 'avocat', self.form())['id'], r['id'])   # clé de demande : idempotent
        with self.assertRaisesRegex(Stop, 'role_insuffisant'):
            rt.create(self.desk, 'assistant@example.test', 'assistant', self.form(request_key='cle-demande-0002'))
        rt.update(self.desk, 'avocat@example.test', 'avocat', r['id'], {'time': '09:00'}, 1)
        with self.assertRaisesRegex(Stop, 'version_en_conflit'):
            rt.update(self.desk, 'avocat@example.test', 'avocat', r['id'], {'time': '10:00'}, 1)   # seconde fenêtre, version périmée
        with self.assertRaisesRegex(Stop, 'routine_absente'):
            rt.update(self.desk, 'autre@example.test', 'avocat', r['id'], {'time': '10:00'}, 2)     # routine personnelle d'un autre
        copy = rt.duplicate(self.desk, 'avocat@example.test', 'avocat', r['id'])
        self.assertEqual(copy['enabled'], 0)
        out = rt.delete(self.desk, 'avocat@example.test', 'avocat', r['id'], 2)
        self.assertEqual(out['id'], r['id'])
        with self.assertRaisesRegex(Stop, 'routine_historique_desactivable'):
            rt.migrate(self.desk)
            rt.delete(self.desk, 'avocat@example.test', 'administrateur', 'r520-tri', 1)

    def test_apercu_changement_d_heure_et_heure_inexistante(self):
        nxt = rt.upcoming({'freq': 'quotidien', 'time': '02:30'}, 'Europe/Paris', datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc), 3)
        self.assertEqual([x['local'] for x in nxt], ['2026-03-29 03:00', '2026-03-30 02:30', '2026-03-31 02:30'])
        self.assertIn('heure inexistante', nxt[0]['note'])
        autumn = rt.upcoming({'freq': 'quotidien', 'time': '02:30'}, 'Europe/Paris', datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc), 2)
        self.assertEqual([x['local'] for x in autumn], ['2026-10-25 02:30', '2026-10-26 02:30'])   # heure répétée : une exécution

    def test_file_pleine_pause_reprise_lancement_et_compte_rendu(self):
        self.desk.setting('routines520:since', '2026-10-01T00:00:00+02:00')
        r = rt.create(self.desk, 'avocat@example.test', 'avocat', self.form(time='08:00', freq='quotidien'))
        self.desk.db.execute("UPDATE routines5652 SET active_since='2026-10-01T00:00:00+02:00' WHERE id=?", (r['id'],))
        self.desk.db.execute("UPDATE routines5652 SET enabled=0 WHERE legacy_kind<>''")
        self.desk.db.commit()
        now = datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc)                                 # 8 h 30 à Paris
        self.desk.setting('missions567:pause', True)
        self.assertEqual(rt.tick(self.desk, now), [])
        self.assertEqual(self.desk.db.execute('SELECT state FROM routines5652_occ').fetchone()[0], 'reportee_pause')
        self.desk.setting('missions567:pause', False)
        with patch.object(self.desk, 'enqueue', side_effect=Stop('file_attente_pleine')):
            with self.assertRaisesRegex(Stop, 'file_attente_pleine'):
                rt.tick(self.desk, now + timedelta(minutes=1))
        self.assertEqual(self.desk.db.execute('SELECT state FROM routines5652_occ').fetchone()[0], 'a_reprogrammer')
        self.assertEqual(rt.tick(self.desk, now + timedelta(minutes=2)), [r['id']])
        self.assertEqual(rt.tick(self.desk, now + timedelta(minutes=3)), [])                      # un seul passage voulu
        occ = self.desk.db.execute('SELECT * FROM routines5652_occ').fetchone()
        out = rt.execute(self.desk, {'occurrence': occ['id']})
        self.assertIn(out['state'], ('terminee', 'partielle'))
        lanc = rt.run_now(self.desk, 'avocat@example.test', 'avocat', r['id'], 'clic-0000001')
        self.assertEqual(rt.run_now(self.desk, 'avocat@example.test', 'avocat', r['id'], 'clic-0000001')['id'], lanc['id'])   # double clic
        from agent import file5651
        items = file5651.items(self.desk, '', None, 'relire', actor='avocat@example.test', role='avocat')
        self.assertTrue(any(i['object'].startswith('routine:') for i in items))
        hidden = file5651.items(self.desk, '', None, 'relire', actor='autre@example.test', role='avocat')
        self.assertFalse(any(i['object'].startswith('routine:') for i in hidden))                # routine personnelle : son auteur seul

    def test_routine_personnelle_ne_lit_plus_un_dossier_retire(self):
        from agent import membres5650 as mb
        r = rt.create(self.desk, 'avocat@example.test', 'avocat', self.form(task_type='nouveautes', dossiers=MID, request_key='cle-demande-0003'))
        occ = rt.run_now(self.desk, 'avocat@example.test', 'avocat', r['id'], 'clic-0000002')
        with patch.object(rt, '_hidden_for', return_value=frozenset({MID})):
            out = rt.execute(self.desk, {'occurrence': occ['id']})
        self.assertIn('accès retiré', ' '.join(out['resultat']['impossible']))
        mb.clear()


# ---------------------------------------------------------------------------------------------------- toutes les capacités
PDF = b'%PDF-1.4 fictif'
CASES = [
    # fournisseur, capacité, paramètres, (méthode, fragment d'URL) attendu en dernier appel métier, routes supplémentaires, en-tête attendu
    ('ar24', 'registered_mail.status', {'remote_ref': '11'}, ('GET', 'sandbox.ar24.fr/api/mail?'), [('GET', 'ar24.fr/api/mail?', 'AR24MAIL')], 'signature'),
    ('youtrust', 'signature.status', {'remote_ref': 'sr-1'}, ('GET', '/signature_requests/sr-1'), [('GET', '/signature_requests/sr-1', {'status': 'ongoing'})], 'Authorization'),
    ('legifrance_piste', 'law.search', {'query': 'bail commercial'}, ('POST', 'lf-engine-app/search'), [], 'Authorization'),
    ('legifrance_piste', 'law.read_version', {'id': 'LEGIARTI000032041571'}, ('POST', '/consult/getArticle'), [], 'Authorization'),
    ('legifrance_piste', 'law.verify_reference', {'code': 'Code civil', 'number': '1240'}, ('POST', '/consult/getArticle'), [], 'Authorization'),
    ('judilibre_piste', 'case_law.read', {'id': 'dec-fictive'}, ('GET', 'judilibre/v1.0/decision?id=dec-fictive'),
     [('GET', '/decision?id=', {'id': 'dec-fictive', 'text': 'Texte fictif'})], 'Authorization'),
    ('judilibre_piste', 'case_law.search', {'query': 'bail commercial'}, ('GET', 'judilibre/v1.0/search?'), [], 'Authorization'),
    ('judilibre_piste', 'case_law.verify_reference', {'pourvoi': '22-10.000'}, ('GET', 'judilibre/v1.0/search'), [], 'Authorization'),
    ('pappers_justice', 'legal.search', {'query': 'bail', 'kind': 'decision'}, ('GET', '/recherche/decision'), [], 'Authorization'),
    ('pappers_justice', 'legal.read', {'id': 'pj-1'}, ('GET', 'api-justice.pappers.fr/v1/decision/pj-1'), [('GET', '/decision/pj-1', {'id': 'pj-1'})], 'Authorization'),
    ('pappers_justice', 'external_legal_ai.ask_authorized', {'question': 'Question fictive sur le bail ?'}, ('POST', '/question-juridique/generer'),
     [('POST', '/question-juridique/generer', {'question': {'uuid': 'u'}, 'articles': []})], 'Authorization'),
    ('pennylane', 'billing.clients', {'query': 'Alpha'}, ('GET', '/customers'), [('GET', '/customers', {'items': [{'id': 9, 'name': 'Alpha Fictive'}]})], 'Authorization'),
    ('pennylane', 'billing.prepare', {'customer_id': 9, 'date': '2026-10-16', 'deadline': '2026-11-15', 'lines': [], 'reference': 'op-1'},
     ('POST', '/customer_invoices'), [('POST', '/customer_invoices', {'id': 77, 'status': 'draft'})], 'Authorization'),
    ('pennylane', 'billing.read_status', {'remote_ref': '77'}, ('GET', '/customer_invoices/77'), [('GET', '/customer_invoices/77', {'status': 'upcoming'})], 'Authorization'),
    ('pennylane', 'billing.read_payments', {'remote_ref': '77'}, ('GET', '/customer_invoices/77/matched_transactions'),
     [('GET', '/matched_transactions', {'items': []})], 'Authorization'),
    ('docusign', 'signature.send_approved', {'title': 'Convention', 'filename': 'c.pdf', 'content': PDF, 'reference': 'op-2',
                                             'signers': [{'first_name': 'A', 'last_name': 'B', 'email': 'a@example.test'}]},
     ('POST', 'demo.docusign.net/restapi/v2.1/accounts/compte-fictif/envelopes'), [('POST', '/envelopes', {'envelopeId': 'env-1', 'status': 'sent'})], 'Authorization'),
    ('docusign', 'signature.status', {'remote_ref': 'env-1'}, ('GET', '/envelopes/env-1'), [('GET', '/envelopes/env-1', {'status': 'sent'})], 'Authorization'),
    ('superpdp', 'einvoice.receive', {}, ('GET', '/v1.beta/invoices?'), [('GET', '/v1.beta/invoices?', {'data': []})], 'Authorization'),
    ('superpdp', 'einvoice.status', {'remote_ref': '5'}, ('GET', '/v1.beta/invoice_events'), [('GET', '/v1.beta/invoice_events', {'data': []})], 'Authorization'),
    ('superpdp', 'einvoice.download', {'remote_ref': '5'}, ('GET', '/v1.beta/invoices/5/download'), [('GET', '/invoices/5/download', b'<xml/>')], 'Authorization'),
    ('superpdp', 'einvoice.send_approved', {'content': b'<Invoice/>', 'content_type': 'application/xml'}, ('POST', 'superpdp.tech/v1.beta/invoices'),
     [('POST', 'superpdp.tech/v1.beta/invoices', {'id': 6, 'status': 'deposee'})], 'Authorization'),
    ('b2brouter', 'einvoice.send_approved', {'invoice': {'number': 'F-1'}}, ('POST', '/invoices/send_invoice/88'),
     [('POST', '/accounts/compte-fictif/invoices', {'invoice': {'id': 88}}), ('POST', '/invoices/send_invoice/88', {})], 'X-B2B-API-Key'),
    ('b2brouter', 'einvoice.status', {'remote_ref': '88'}, ('GET', '/invoices/88'), [('GET', '/invoices/88', {'invoice': {'state': 'sent'}})], 'X-B2B-API-Key'),
    ('b2brouter', 'einvoice.receive', {}, ('GET', '/accounts/compte-fictif/invoices?'), [('GET', '/accounts/compte-fictif/invoices?', {'invoices': []})], 'X-B2B-API-Key'),
    ('b2brouter', 'einvoice.download', {'remote_ref': '88'}, ('GET', '/invoices/88/as/'), [('GET', '/invoices/88/as/', b'%PDF')], 'X-B2B-API-Key'),
    ('gocardless', 'payment.prepare_mandate', {'reference': 'op-3', 'idempotency_key': 'idem-3'}, ('POST', '/billing_request_flows'),
     [('POST', '/billing_requests', {'billing_requests': {'id': 'BRQ1', 'status': 'pending'}}),
      ('POST', '/billing_request_flows', {'billing_request_flows': {'authorisation_url': 'https://pay-sandbox.gocardless.com/flow/x'}})], 'Idempotency-Key'),
    ('gocardless', 'payment.submit_authorized', {'amount_cents': 120000, 'description': 'Honoraires', 'mandate': 'MD1', 'reference': 'op-4',
                                                  'idempotency_key': 'idem-4'}, ('POST', '/payments'), [('POST', '/payments', {'payments': {'id': 'PM1', 'status': 'pending_submission'}})],
     'Idempotency-Key'),
    ('gocardless', 'payment.status', {'remote_ref': 'PM1'}, ('GET', '/payments/PM1'), [('GET', '/payments/PM1', {'payments': {'status': 'confirmed'}})], 'GoCardless-Version'),
    ('chorus_pro', 'public_invoice.prepare', {'siret': '00000000000000'}, ('POST', 'cpro/structures/v1/rechercher'),
     [('POST', 'cpro/structures/v1/rechercher', {'codeRetour': 0, 'listeStructures': []})], 'cpro-account'),
    ('chorus_pro', 'public_invoice.submit_approved', {'facture': {'modeDepot': 'SAISIE_API'}}, ('POST', 'cpro/factures/v1/soumettre'),
     [('POST', 'cpro/factures/v1/soumettre', {'codeRetour': 0, 'identifiantFactureCPP': 123})], 'cpro-account'),
    ('chorus_pro', 'public_invoice.status', {'remote_ref': '123'}, ('POST', 'cpro/factures/v1/consulter/fournisseur'),
     [('POST', 'consulter/fournisseur', {'codeRetour': 0, 'statutFacture': 'DEPOSEE'})], 'cpro-account'),
    ('maileva', 'paper_mail.send_approved', {'title': 'Courrier', 'filename': 'c.pdf', 'content': PDF, 'address': {'address_line_1': 'A', 'address_line_6': 'B',
                                                                                                               'country_code': 'FR'}},
     ('POST', '/sendings/s1/submit'), [('POST', 'mail/v2/sendings', {'id': 's1'}), ('PATCH', '/sendings/s1', {})], 'Authorization'),
    ('maileva', 'paper_mail.status', {'remote_ref': 's1'}, ('GET', '/sendings/s1'), [('GET', '/sendings/s1', {'status': 'ACCEPTED'})], 'Authorization'),
    ('maileva', 'paper_mail.proofs', {'remote_ref': 's1'}, ('GET', '/sendings/s1/download_deposit_proof'), [('GET', 'download_deposit_proof', b'%PDF')], 'Authorization'),
    ('microsoft_graph', 'calendar.read', {}, ('GET', '/me/calendarview'), [('GET', '/me/calendarview', {'value': []})], 'Authorization'),
    ('microsoft_graph', 'mail.read', {}, ('GET', '/me/messages?'), [('GET', '/me/messages?', {'value': []})], 'Authorization'),
    ('microsoft_graph', 'mail.create_draft', {'subject': 'Objet', 'body': 'Texte', 'to': ['a@example.test']}, ('POST', '/me/messages'),
     [('POST', '/me/messages', {'id': 'm1'})], 'Authorization'),
    ('microsoft_graph', 'contacts.read', {}, ('GET', '/me/contacts'), [('GET', '/me/contacts', {'value': []})], 'Authorization'),
    ('google_workspace', 'calendar.read', {}, ('GET', '/calendar/v3/calendars/primary/events'), [('GET', '/calendar/v3/', {'items': []})], 'Authorization'),
    ('google_workspace', 'mail.read', {}, ('GET', 'gmail.googleapis.com/gmail/v1/users/me/messages'), [('GET', 'gmail/v1/users/me/messages', {'messages': []})],
     'Authorization'),
    ('google_workspace', 'mail.create_draft', {'subject': 'Objet', 'body': 'Texte', 'to': ['a@example.test']}, ('POST', 'gmail/v1/users/me/drafts'),
     [('POST', '/drafts', {'id': 'd1'})], 'Authorization'),
    ('google_workspace', 'documents.export', {'id': 'f1'}, ('GET', '/drive/v3/files/f1/export'),
     [('GET', '/drive/v3/files/f1?fields', {'id': 'f1', 'name': 'Note', 'mimeType': 'application/vnd.google-apps.document', 'version': '3'}),
      ('GET', '/drive/v3/files/f1/export', b'%PDF')], 'Authorization'),
    ('axonaut', 'crm.lookup', {'siret': '00000000000000'}, ('GET', '/api/v2/companies?siret='), [('GET', '/api/v2/companies?', [{'id': 1, 'name': 'Alpha'}])], 'userApiKey'),
    ('axonaut', 'billing.prepare_when_supported', {'quotation': {'company_id': 1}}, ('POST', '/api/v2/quotations'), [('POST', '/api/v2/quotations', {'id': 4})], 'userApiKey'),
]


class EveryCapability(Base):
    def test_chaque_capacite_appelle_le_bon_point_et_s_authentifie(self):
        self.fake.on('GET', 'ar24.fr/api/mail?', ar24_route({'id': 11, 'status': 'sent'}))
        covered = set()
        for provider, cap, params, (method, fragment), routes, header in CASES:
            for m, frag, resp in routes:
                if resp != 'AR24MAIL':
                    self.fake.on(m, frag, resp)
            row_view = self.connect(provider, bool(cx.PROVIDERS[provider].get('oauth')))
            adapter = cx.adapter(self.desk, cx._row(self.desk, row_view['id']))
            self.fake.calls.clear()
            out = adapter.call(cap, params)
            self.assertIsInstance(out, dict, (provider, cap))
            business = [c for c in self.fake.calls if not any(w in urllib.parse.urlsplit(c[1]).path for w in ('oauth', 'token', 'userinfo'))]
            self.assertTrue(any(c[0] == method and fragment in c[1] for c in business), (provider, cap, [c[:2] for c in business]))
            self.assertTrue(all(header in c[3] for c in business), (provider, cap, header))
            for secret in SECRETS.values():
                self.assertNotIn(secret, json.dumps(cx._strip(out), ensure_ascii=False, default=str), (provider, cap))
            covered.add((provider, cap))
        declared = {(p, c) for p, s in cx.PROVIDERS.items() for c in s['caps'] if not c.endswith('.prepare') or p == 'pennylane'}
        missing = declared - covered - {('youtrust', 'signature.send_approved'), ('youtrust', 'signature.signed_document'), ('ar24', 'registered_mail.send_approved'),
                                        ('ar24', 'registered_mail.proofs'), ('docusign', 'signature.signed_document'), ('microsoft_graph', 'documents.read_selected'),
                                        ('google_workspace', 'documents.read_selected')}
        self.assertEqual(missing, set())


class AR24Send(Base):
    def test_lre_depot_piece_jointe_puis_envoi(self):
        self.fake.on('POST', 'ar24.fr/api/attachment', ar24_route({'file_id': 'F1'}))
        self.fake.on('POST', 'ar24.fr/api/mail', ar24_route({'id': 12, 'status': 'waiting'}))
        view = self.connect('ar24')
        adapter = cx.adapter(self.desk, cx._row(self.desk, view['id']))
        out = adapter.call('registered_mail.send_approved', {'filename': 'mise-en-demeure.pdf', 'content': PDF, 'to_email': 'dest@example.test',
                                                             'dest_statut': 'particulier', 'to_lastname': 'Fictif', 'to_firstname': 'Paul', 'reference': 'op-5'})
        self.assertEqual(out['remote_ref'], '12')
        mail = [c for c in self.fake.calls if c[1].endswith('/api/mail')][-1]
        self.assertIn(b'attachment%5B0%5D=F1', mail[2])
        self.assertIn(b'dest_statut=particulier', mail[2])


if __name__ == '__main__':
    unittest.main()

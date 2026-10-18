"""5.6.62 : ChatGPT avec l'abonnement (Sign in with ChatGPT), corrections relevées par le cabinet le 18 octobre 2026.

Aucun appel réseau : OpenAI est simulé (clés RSA réelles, PKCE réel, flux d'événements simulé). Données fictives (example.test)."""
import base64
import hashlib
import io
import json
import pathlib
import time
import unittest
import urllib.parse
from unittest.mock import patch

import test_v5652 as base
from agent import chatgpt5662 as cg
from agent.common import Stop

ROOT = pathlib.Path(__file__).resolve().parents[1]


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class FakeOpenAI:
    """Simule auth.openai.com et api.openai.com : découverte, clés, jetons, modèles, Responses, révocation."""
    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.calls, self.nonce, self.client_id, self.scope = [], '', 'oaiapp_essai', cg.SCOPES
        self.refresh_error, self.stream, self.responses_error, self.forge = None, None, None, False
        self.refresh_n = 0

    def id_token(self, client_id, nonce, key=None):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        head = b64(json.dumps({'alg': 'RS256', 'kid': 'k1'}).encode())
        body = b64(json.dumps({'iss': cg.AUTH, 'aud': client_id, 'exp': time.time() + 3600, 'nonce': nonce, 'sub': 'user-fictif',
                               'email': 'avocate@example.test'}).encode())
        sig = (key or self.key).sign((head + '.' + body).encode(), padding.PKCS1v15(), hashes.SHA256())
        return head + '.' + body + '.' + b64(sig)

    def __call__(self, method, url, data=None, headers=None, timeout=60, stream=False):
        self.calls.append((method, url, data, dict(headers or {})))
        if url == cg.DISCOVERY:
            return json.dumps({'jwks_uri': cg.AUTH + '/.well-known/jwks.json', 'revocation_endpoint': cg.AUTH + '/oauth/revoke'}).encode()
        if url.endswith('/jwks.json'):
            n = self.key.public_key().public_numbers()
            return json.dumps({'keys': [{'kty': 'RSA', 'kid': 'k1', 'n': b64(n.n.to_bytes(256, 'big')), 'e': b64(n.e.to_bytes(3, 'big'))}]}).encode()
        if url == cg.TOKEN:
            form = dict(urllib.parse.parse_qsl(data.decode()))
            if form['grant_type'] == 'authorization_code':
                assert form['client_id'] == self.client_id and form['resource'] == cg.API
                assert hashlib.sha256(form['code_verifier'].encode()).digest()   # vérificateur transmis, jamais de secret client
                return json.dumps({'access_token': 'acces-1', 'refresh_token': 'renouv-1', 'expires_in': 3600, 'token_type': 'Bearer',
                                   'scope': self.scope, 'id_token': self.id_token(self.client_id, self.nonce, self.other if self.forge else None)}).encode()
            if self.refresh_error:
                raise cg.HTTPFailure(400, self.refresh_error)
            self.refresh_n += 1
            return json.dumps({'access_token': 'acces-%d' % (self.refresh_n + 1), 'refresh_token': 'renouv-%d' % (self.refresh_n + 1),
                               'expires_in': 3600}).encode()
        if url == cg.API + '/models':
            return json.dumps({'models': [{'slug': 'gpt-essai', 'display_name': 'GPT essai', 'visibility': 'list'},
                                          {'slug': 'cache', 'display_name': 'Caché', 'visibility': 'hide'}]}).encode()
        if url == cg.API + '/responses':
            if self.responses_error:
                raise self.responses_error
            return io.BytesIO(self.stream or (
                b'data: {"type":"response.output_text.delta","delta":"Bon"}\n\n'
                b'data: {"type":"response.output_text.delta","delta":"jour"}\n\n'
                b'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":2}}}\n\n'))
        if url.endswith('/oauth/revoke'):
            return b''
        raise AssertionError(url)


class ChatGPT(base.Base):
    def setUp(self):
        super().setUp()
        self.oa = FakeOpenAI()
        p = patch.object(cg, 'http', self.oa)
        p.start()
        self.addCleanup(p.stop)

    def _connect(self, scope=None):
        url = cg.start(self.desk, 'admin@example.test')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.oa.nonce = q['nonce']
        if scope is not None:
            self.oa.scope = scope
        back = 'http://127.0.0.1:1455/auth/callback?code=c0de&scope=x&state=%s&client_id=%s' % (q['state'], self.oa.client_id)
        return q, cg.complete(self.desk, back)

    def test_premiere_inscription_puis_connexion(self):
        q, out = self._connect()
        self.assertEqual(q['client_id'], 'dynamic_agent_client')
        self.assertEqual(q['agent_name_hint'], 'AxiorHub Pilote')
        self.assertTrue(q['ext_agent_host_id'].startswith('urn:uuid:'))
        self.assertEqual((q['code_challenge_method'], q['resource'], q['redirect_uri']), ('S256', cg.API, 'http://127.0.0.1:1455/auth/callback'))
        self.assertIn('chatgpt.tokens.use.direct', q['scope'])
        self.assertTrue(out['ok'] and out['plan'])
        acc = cg.account(self.desk)
        self.assertEqual((acc['client_id'], acc['email'], acc['etat']), ('oaiapp_essai', 'avocate@example.test', 'connecte'))
        raw = cg._vault_path(self.desk.c['state_dir']).read_text(encoding='utf-8')
        self.assertNotIn('renouv-1', raw)                                  # jetons chiffrés dans le coffre
        self.assertEqual(cg.load_record(self.desk.c['state_dir'])['refresh_token'], 'renouv-1')
        self.assertEqual([m['slug'] for m in self.desk.settings(cg.MODELS_KEY)['modeles']], ['gpt-essai'])   # modèles visibles seulement
        self.assertEqual(cg.host_id(self.desk), q['ext_agent_host_id'])  # identifiant d'hôte stable
        with self.assertRaises(Stop) as ctx:                               # un code ne s'échange qu'une fois
            cg.complete(self.desk, 'state=%s&code=c0de&client_id=oaiapp_essai' % q['state'])
        self.assertEqual(str(ctx.exception), 'chatgpt_tentative_inconnue')

    def test_reconnexion_avec_le_client_enregistre(self):
        self._connect()
        url = cg.start(self.desk, 'admin@example.test')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertEqual(q['client_id'], 'oaiapp_essai')
        self.assertNotIn('agent_name_hint', q)
        self.assertIn('id_token_hint', q)
        self.assertEqual(q['login_hint'], 'avocate@example.test')
        with self.assertRaises(Stop) as ctx:
            cg.complete(self.desk, 'state=%s&code=c&client_id=oaiapp_autre' % q['state'])
        self.assertEqual(str(ctx.exception), 'chatgpt_client_different')

    def test_refus_etat_inconnu_refus_utilisateur_jeton_falsifie_sans_permission(self):
        url = cg.start(self.desk, 'admin@example.test')
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        with self.assertRaises(Stop):
            cg.complete(self.desk, 'state=faux&code=c&client_id=oaiapp_essai')
        with self.assertRaises(Stop) as ctx:
            cg.complete(self.desk, 'state=%s&error=access_denied' % q['state'])
        self.assertEqual(str(ctx.exception), 'chatgpt_autorisation_refusee')
        self.oa.forge = True
        with self.assertRaises(Stop) as ctx:
            self._connect()
        self.assertEqual(str(ctx.exception), 'chatgpt_jeton_identite_invalide')
        self.oa.forge = False
        _, out = self._connect(scope='openid profile email offline_access resource.invoke')
        self.assertFalse(out['plan'])
        with self.assertRaises(Stop):
            cg.save_provider(self.desk, 'admin', 'gpt-essai', True, True)  # abonnement non autorisé : jamais activé

    def test_renouvellement_tournant_et_reconnexion(self):
        self._connect()
        sd = self.desk.c['state_dir']
        rec = cg.load_record(sd)
        rec['expires_at'] = '2000-01-01T00:00:00+00:00'
        cg.save_record(sd, rec)
        self.assertEqual(cg.access_token(sd), 'acces-2')
        self.assertEqual(cg.load_record(sd)['refresh_token'], 'renouv-2')  # le dernier jeton de renouvellement est conservé
        self.assertEqual(cg.access_token(sd), 'acces-2')                   # encore valide : aucun renouvellement
        self.oa.refresh_error = 'invalid_grant'
        with self.assertRaises(Stop) as ctx:
            cg.access_token(sd, force=True)
        self.assertEqual(str(ctx.exception), 'chatgpt_reconnexion_requise')
        self.assertEqual(cg.load_record(sd)['refresh_token'], '')

    def test_reponse_en_flux_et_erreurs_de_l_abonnement(self):
        self._connect()
        sd = self.desk.c['state_dir']
        text, usage = cg.respond(sd, 'gpt-essai', [{'role': 'system', 'content': 'Consignes'}, {'role': 'user', 'content': 'Question'}])
        self.assertEqual((text, usage['input_tokens']), ('Bonjour', 12))
        body = json.loads(self.oa.calls[-1][2])
        self.assertEqual((body['store'], body['stream'], body['instructions']), (False, True, 'Consignes'))
        self.assertEqual(body['input'], [{'role': 'user', 'content': 'Question'}])          # aucun élément « system »
        for banned in ('temperature', 'max_output_tokens', 'top_p', 'previous_response_id', 'metadata', 'user'):
            self.assertNotIn(banned, body)
        self.assertEqual(self.oa.calls[-1][3]['Authorization'], 'Bearer acces-1')
        schema = {'type': 'object', 'properties': {'a': {'type': 'string'}}, 'required': ['a'], 'additionalProperties': False}
        cg.respond(sd, 'gpt-essai', [{'role': 'user', 'content': 'x'}], schema)
        self.assertEqual(json.loads(self.oa.calls[-1][2])['text']['format']['type'], 'json_schema')
        self.oa.responses_error = cg.HTTPFailure(429, 'subscription_sharing_usage_limit_exceeded')
        with self.assertRaises(Stop) as ctx:
            cg.respond(sd, 'gpt-essai', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(str(ctx.exception), 'chatgpt_limite_usage_atteinte')
        self.oa.responses_error = None
        self.oa.stream = b'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_user_not_eligible"}}}\n\n'
        with self.assertRaises(Stop) as ctx:
            cg.respond(sd, 'gpt-essai', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(str(ctx.exception), 'chatgpt_compte_non_eligible')
        self.oa.stream = b'data: {"type":"response.output_text.delta","delta":"coup"}\n\n'                   # flux interrompu
        with self.assertRaises(Stop) as ctx:
            cg.respond(sd, 'gpt-essai', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(str(ctx.exception), 'generation_ia_incomplete')

    def test_jamais_d_attente_sans_fin(self):
        """Demande du cabinet : aucun blocage du traitement par l'IA (flux silencieux, flux sans fin, messagerie injoignable)."""
        self._connect()
        sd = self.desk.c['state_dir']

        class Silent:
            def readline(self):
                raise TimeoutError()

            def close(self):
                pass
        with patch.object(cg, 'http', lambda *a, **k: Silent() if k.get('stream') else self.oa(*a, **k)):
            with self.assertRaises(Stop) as ctx:
                cg.respond(sd, 'gpt-essai', [{'role': 'user', 'content': 'x'}])
        self.assertEqual(str(ctx.exception), 'generation_ia_delai_depasse')
        endless = iter(lambda: b'data: {"type":"response.output_text.delta","delta":"a"}\n', None)
        with self.assertRaises(Stop) as ctx:
            cg.read_stream(endless, deadline=time.monotonic() + 0.05)
        self.assertEqual(str(ctx.exception), 'generation_ia_delai_depasse')
        from agent import cockpit530
        with patch('agent.drafts440.list_drafts', side_effect=OSError('imap')) as imap:
            cockpit530._drafts(self.desk, refresh=True)
            cockpit530._drafts(self.desk, refresh=True)                 # messagerie injoignable : pas de nouvel essai avant 10 min
        self.assertEqual(imap.call_count, 1)
        self.assertGreater(self.desk.settings('cockpit530:drafts')['at'], time.time() + 400)

    def test_fournisseur_modele_et_deconnexion(self):
        from agent.model import Model
        self._connect()
        with self.assertRaises(Stop):
            cg.save_provider(self.desk, 'admin', 'gpt-essai', True, False)   # jamais sans l'autorisation « données externes »
        cg.save_provider(self.desk, 'admin', 'gpt-essai', True, True)
        cfg = {**self.desk.c['ai_providers']['chatgpt'], 'provider_id': 'chatgpt', 'provider_type': 'chatgpt', 'purpose': 'assistant'}
        m = Model(cfg)
        with patch('agent.chatgpt5662.respond', return_value=('Réponse', {'input_tokens': 3, 'output_tokens': 1})) as resp:
            self.assertEqual(m._chatgpt([{'role': 'user', 'content': 'Q'}], None), 'Réponse')
        self.assertEqual(resp.call_args[0][1], 'gpt-essai')
        with self.assertRaises(Stop):
            Model({**cfg, 'external_data_allowed': False})
        out = cg.disconnect(self.desk, 'admin@example.test')
        self.assertTrue(out['revocation_confirmee'])
        revoke = [c for c in self.oa.calls if c[1].endswith('/oauth/revoke')][0]
        form = dict(urllib.parse.parse_qsl(revoke[2].decode()))
        self.assertEqual((form['token'], form['token_type_hint'], form['client_id']), ('renouv-1', 'refresh_token', 'oaiapp_essai'))
        self.assertEqual(cg.load_record(self.desk.c['state_dir'])['refresh_token'], '')
        self.assertFalse(self.desk.settings('ai:provider:chatgpt')['enabled'])
        with self.assertRaises(Stop):
            cg.access_token(self.desk.c['state_dir'])

    def test_retour_local_sur_le_poste(self):
        import urllib.request
        url = cg.start(self.desk, 'admin@example.test')
        port = cg.listen(self.desk.c, port=0, minutes=1)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.oa.nonce = q['nonce']
        page = urllib.request.urlopen('http://127.0.0.1:%d/auth/callback?code=c&state=%s&client_id=oaiapp_essai' % (port, q['state']),
                                      timeout=20).read().decode()
        self.assertIn('Connexion à ChatGPT réussie', page)
        self.assertEqual(cg.account(self.desk)['etat'], 'connecte')

    def test_routes_et_navigateur_local(self):
        from agent import web5662
        self.assertTrue(web5662.allowed('administrateur', 'chatgpt/connecter'))
        self.assertFalse(web5662.allowed('avocat', 'chatgpt/connecter'))
        with patch('agent.maj5661.platform', return_value='serveur'):
            self.assertFalse(web5662.local_browser({'REMOTE_ADDR': '127.0.0.1', 'HTTP_X_FORWARDED_FOR': '203.0.113.5'}))   # derrière Apache
            self.assertTrue(web5662.local_browser({'REMOTE_ADDR': '127.0.0.1'}))
        with patch('agent.maj5661.platform', return_value='windows'):
            self.assertTrue(web5662.local_browser({'REMOTE_ADDR': '10.0.0.4'}))
        html = cg.html(self.desk, '/p', 'administrateur', False)
        self.assertIn('Continuer avec ChatGPT', html)
        self.assertIn('http://127.0.0.1:1455/auth/callback', html)      # serveur : recopier l'adresse
        self.assertIn('Améliorer le modèle pour tous', html)


# ------------------------------------------------------------------------------------------------ corrections du 18 octobre
class Corrections(base.Base):
    def test_point_client_sans_nom_de_fichier_et_document_ouvrable(self):
        from agent import parcours5657 as p
        self.assertEqual(p._client_fact('courrier'), 'Nous avons préparé un projet de courrier.')
        self.assertEqual(p._client_fact('inconnu'), 'Nous avons préparé un projet de document.')
        js = (ROOT / 'agent' / 'static' / 'v5651.js').read_text(encoding='utf-8')
        self.assertIn("closest('[data-open-path]')", js)                  # « Ouvrir dans Nextcloud » hors d'Aujourd'hui
        src = (ROOT / 'agent' / 'parcours5657.py').read_text(encoding='utf-8')
        self.assertIn('Documents concernés (non communiqués au client)', src)
        self.assertNotIn("'Nous avons préparé un projet : %s.'", src)

    def test_taches_relire_sans_doublon(self):
        from agent import deck530, workplan
        from agent.common import digest
        workplan.ensure_schema(self.desk)
        deck530.ensure_schema(self.desk)
        uid = 'axiorhub-abc@mail-agent.local'
        local, twin = digest('task530|' + uid)[:32], digest('caldav-vtodo|' + uid)
        row = lambda tid, matter, source: (tid, uid, 'https://nc.example.test/t', '', '*', matter, 'Relire : Projet', '', '', '', 15, 5, 'todo', '',
                                           'review', '', '', source, 'x', 'x', 'x')
        self.desk.db.execute('INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', row(local, '', 'cockpit530'))
        self.desk.db.execute('INSERT INTO work_tasks_v211 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', row(twin, 'DOS-1', '{"kind":"nextcloud_vtodo"}'))
        self.desk.db.execute('INSERT INTO deck530_tasks VALUES(?,?,0,?)', ('doc:1', local, 'x'))
        self.desk.db.commit()
        self.assertEqual(deck530.merge_duplicate_tasks(self.desk), 1)
        self.assertEqual([r[0] for r in self.desk.db.execute('SELECT id FROM work_tasks_v211')], [twin])
        self.assertEqual(self.desk.db.execute('SELECT task_id FROM deck530_tasks').fetchone()[0], twin)
        self.assertIn("digest('caldav-vtodo|' + uid)", (ROOT / 'agent' / 'deck530.py').read_text(encoding='utf-8'))

    def test_un_projet_une_seule_entree_a_relire(self):
        from agent import cockpit530
        cockpit530.ensure_schema(self.desk)
        from agent import production420
        production420.ensure_schema(self.desk)
        files = [{'path': '/Dossiers/DEMO/Projets/Contrat_projet.%s' % x} for x in ('md', 'pdf', 'docx')]
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        self.desk.db.execute('INSERT INTO production_deliverables_v420(id,job_id,job_kind,event_kind,source_id,matter,deliverable_kind,label,status,stage,'
                             'stages,trigger_text,source_ids,target,verification,business_message,error_code,retryable,created,updated) '
                             'VALUES(?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)',
                             ('d1', 'x', 'x', 'x', 'DEMO', 'contrat', 'Contrat', 'verified', 'x', '[]', '', '[]', '', json.dumps({'files': files}), '', '',
                              now, now))
        self.desk.db.commit()
        items = [x for x in cockpit530.review_items(self.desk, cached_only=True) if 'Contrat_projet' in x['title']]
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['path'].endswith('.docx'))                # Word en priorité

    def test_aujourdhui_file_en_premiere_colonne_reprise_en_bas(self):
        src = (ROOT / 'agent' / 'cockpit530.py').read_text(encoding='utf-8')
        cols = src.index('<div class="c530-cols c5657-cols"><div class="c5662-file" id="c5656-file">__FILE5656__</div>')
        self.assertLess(cols, src.index('<div class="c5656-wide c5662-reprise">__REPRISE5657__</div>'))

    def test_textes_lisibles_parametres_et_pilote(self):
        from agent import parametres5621
        side = parametres5621._sidebar('/p', 'poste')
        tools = side[side.index('p5660-tools'):]
        self.assertNotIn('<nav', tools)                                   # « nav a » (blanc) ne s'applique plus à ces liens
        css = (ROOT / 'agent' / 'static' / 'app520.css').read_text(encoding='utf-8')
        self.assertIn('.p5660-side .p5662-tools a{', css)
        self.assertIn('main aside.p5660-side,.ws-collapsed main aside.p5660-side{', css)
        self.assertIn('.ws-ai-dock .ws-ai-auto-context{color:var(--ws-ink,#111827)}', css)

    def test_libelles_de_tous_les_travaux(self):
        from agent import desk, web
        self.assertEqual(sorted(k for k in desk.JOBS if k not in web.JOB_LABELS), [])

    def test_diagnostic_donne_les_motifs(self):
        import sqlite3
        from agent import blocage5660
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        for code in ('contexte_trop_long', 'contexte_trop_long', 'source_document_invalide'):
            self.desk.db.execute("INSERT INTO jobs(kind,args,status,created,finished,result) VALUES('prepare_document_project','{}','error',?,?,?)",
                                 (now, now, json.dumps({'erreur': code})))
        self.desk.db.commit()
        db = sqlite3.connect(str(pathlib.Path(self.desk.c['state_dir']) / 'desk.sqlite3'))
        db.row_factory = sqlite3.Row
        report = blocage5660.text_report(blocage5660.diagnose(db, self.desk.c['state_dir']))
        db.close()
        self.assertIn('3 échecs de « ', report)
        self.assertIn('Motifs : ', report)
        self.assertIn('(2)', report)

    def test_verifier_maintenant(self):
        from agent import maj5661, web5661
        self.assertTrue(web5661.allowed('administrateur', 'maj/verifier'))
        self.assertFalse(web5661.allowed('avocat', 'maj/verifier'))
        feed = b'<feed><entry><title>v9.9.9</title></entry></feed>'
        state = maj5661.check(self.desk, fetch=lambda url: feed, force=True)
        self.assertEqual(state['latest'], '9.9.9')
        calls = []
        with patch('agent.web567.actor', return_value=('admin@example.test', 'administrateur')), patch('agent.web5634._post', return_value={}),                 patch('agent.maj5661.check', side_effect=lambda d, fetch=None, force=False: calls.append(force) or {'latest': '9.9.9'}):
            web5661.api({}, self.desk, {}, '/p', 'm5661/maj/verifier', {}, 'POST')
        self.assertEqual(calls, [True])
        self.assertIn('Vérifier maintenant', maj5661.html(self.desk, '/p', 'administrateur'))


# ------------------------------------------------------------------------------------------------ l'IA ne tourne plus en continu
class Cadence(base.Base):
    """Constat du 18 octobre 2026 : file permanente, l'IA « tourne tout le temps », demande de l'avocat en attente depuis 4 h."""
    def _job(self, kind, status, minutes_ago, priority=60, run_minutes=0, worker='1'):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        created = (now - timedelta(minutes=minutes_ago + run_minutes)).isoformat()
        started = (now - timedelta(minutes=minutes_ago + run_minutes)).isoformat() if status != 'pending' else None
        finished = (now - timedelta(minutes=minutes_ago)).isoformat() if status in ('done', 'error') else None
        self.desk.db.execute('INSERT INTO jobs(kind,args,status,created,finished,result,priority,started,worker) VALUES(?,?,?,?,?,?,?,?,?)',
                             (kind, '{}', status, created, finished, '{}', priority, started, worker))
        self.desk.db.commit()

    def test_repos_apres_la_fin_du_precedent(self):
        from agent import cadence5662 as c
        self._job('monitor_all', 'done', 5)
        self.assertFalse(c.allow(self.desk, 'monitor_all', 30))          # fini il y a 5 min : pas avant 30 min de repos
        self.desk.db.execute("UPDATE jobs SET finished=? WHERE kind='monitor_all'", ('2000-01-01T00:00:00+00:00',))
        self.desk.db.commit()
        self.assertTrue(c.allow(self.desk, 'monitor_all', 30))

    def test_les_demandes_de_l_avocat_passent_d_abord(self):
        from agent import cadence5662 as c
        self._job('assistant_answer', 'pending', 3, priority=0)
        self.assertEqual(c.busy(self.desk), 'demande_avocat_en_attente')
        self.assertFalse(c.allow(self.desk, 'refresh_cabinet_pilotage', 30))
        self.assertTrue(c.allow(self.desk, 'orchestrator_mail_sweep', 5))   # nouveaux courriels : jamais différés

    def test_file_chargee_mais_pas_par_un_seul_passage(self):
        from agent import cadence5662 as c
        for i in range(6):
            self._job('index_all%d' % i, 'pending', 0)                  # mis en file à l'instant (un passage du planificateur)
        self.assertEqual(c.busy(self.desk), '')
        self.desk.db.execute("UPDATE jobs SET created=? WHERE status='pending'", ('2026-01-01T00:00:00+00:00',))
        self.desk.db.commit()
        self.assertIn(c.busy(self.desk), ('file_chargee', 'file_en_retard'))
        self.assertFalse(c.allow(self.desk, 'production_cycle391', 5))

    def test_charge_de_l_ia_dans_le_diagnostic(self):
        import sqlite3
        from agent import blocage5660
        for i in range(10):
            self._job('orchestrator_mail_sweep', 'done', 30 + i * 70, run_minutes=60)
        db = sqlite3.connect(str(pathlib.Path(self.desk.c['state_dir']) / 'desk.sqlite3'))
        db.row_factory = sqlite3.Row
        report = blocage5660.text_report(blocage5660.diagnose(db, self.desk.c['state_dir']))
        db.close()
        self.assertIn('Charge des workers sur 24 h : 42 %', report)
        self.assertIn('Orchestration des nouveaux courriels (orchestrator_mail_sweep) : 10 h 00 en 10 fois', report)


if __name__ == '__main__':
    unittest.main()

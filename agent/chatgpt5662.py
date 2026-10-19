"""5.6.62 : connexion à ChatGPT avec l'abonnement de l'utilisateur (« Sign in with ChatGPT » d'OpenAI), sans clé API.

Programme officiel d'OpenAI pour les logiciels libres hébergés par l'utilisateur (utilisation de l'abonnement ChatGPT Plus ou Pro) :
https://developers.openai.com/siwc/token-sharing-open-source — AxiorHub (AGPL-3.0, installé chez le cabinet) en relève.

- Inscription dynamique : premier passage avec ``client_id=dynamic_agent_client`` et l'identifiant d'hôte ``ext_agent_host_id`` (UUID
  propre à cette installation, conservé) ; OpenAI rend un ``client_id`` propre au compte, réutilisé ensuite.
- OAuth 2 avec PKCE (S256), ``state`` et ``nonce`` neufs à chaque tentative ; retour sur ``http://127.0.0.1:<port>/auth/callback``.
  Poste (Windows, Ubuntu) : AxiorHub écoute ce retour lui-même. Serveur : le navigateur de l'avocat n'est pas sur le serveur ; il recopie
  l'adresse affichée après l'accord (elle ne contient qu'un code à usage unique, inutilisable sans le secret PKCE resté sur le serveur).
- Jeton d'identité vérifié (signature RS256 avec les clés publiées par OpenAI, émetteur, destinataire, expiration, nonce) ; l'usage de
  l'abonnement n'est activé que si la permission ``chatgpt.tokens.use.direct`` est accordée.
- Jetons chiffrés dans le coffre d'AxiorHub ; renouvellement automatique (accès : 1 h ; renouvellement : 30 jours glissants), sérialisé.
- Inférence : API Responses publique, ``store: false`` et ``stream: true`` obligatoires ; paramètres non admis jamais envoyés.
- Déconnexion : révocation du jeton de renouvellement chez OpenAI, puis effacement local.
- Aucune donnée n'est envoyée sans l'autorisation « données externes » du cabinet ; la pseudonymisation et le bouclier s'appliquent
  comme pour tout fournisseur externe (``model.Model._leaf``).
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .common import Stop

AUTH = 'https://auth.openai.com'
API = 'https://api.openai.com/v1'
AUTHORIZE = AUTH + '/api/accounts/authorize'
TOKEN = AUTH + '/api/accounts/oauth/token'
DISCOVERY = AUTH + '/.well-known/openid-configuration'
USAGE_URL = 'https://chatgpt.com/settings/usage'
DYNAMIC = 'dynamic_agent_client'
AGENT_NAME = 'AxiorHub Pilote'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
PLAN_SCOPE = 'chatgpt.tokens.use.direct'
CALLBACK_PATH = '/auth/callback'
DEFAULT_PORT = 1455
ATTEMPT_MINUTES = 15
PROVIDER_ID = 'chatgpt'
ORIGINS = {('https', 'auth.openai.com'), ('https', 'api.openai.com')}
HOST_KEY = 'chatgpt5662:hote'
ATTEMPT_KEY = 'chatgpt5662:tentative'
ACCOUNT_KEY = 'chatgpt5662:compte'
MODELS_KEY = 'chatgpt5662:modeles'
# Codes d'erreur de l'utilisation de l'abonnement (documentation « Errors and recovery ») → messages AxiorHub
PLAN_ERRORS = {'subscription_sharing_usage_limit_exceeded': 'chatgpt_limite_usage_atteinte',
               'subscription_sharing_user_not_eligible': 'chatgpt_compte_non_eligible',
               'subscription_sharing_usage_unavailable': 'chatgpt_indisponible',
               'subscription_sharing_user_unavailable': 'chatgpt_indisponible',
               'subscription_sharing_unsupported_capability': 'chatgpt_option_non_prise_en_charge',
               'subscription_sharing_route_not_supported': 'chatgpt_acces_refuse',
               'subscription_sharing_invalid_user': 'chatgpt_reconnexion_requise',
               'chatpass_v2_scope_not_authorized': 'chatgpt_acces_refuse',
               'chatpass_v2_invalid_authorization_context': 'chatgpt_acces_refuse'}
REFRESH_DEAD = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated',
                'refresh_token_reused'}


class HTTPFailure(Stop):
    def __init__(self, status, code='', detail=''):
        super().__init__('http_%s' % status)
        self.status, self.code, self.detail = int(status), str(code or ''), str(detail or '')[:300]


def _now():
    return datetime.now(timezone.utc)


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _b64d(text):
    text = str(text or '')
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


# ------------------------------------------------------------------------------------------------ transport (deux origines seulement)
def _check_origin(url):
    p = urllib.parse.urlsplit(url)
    if (p.scheme, p.netloc) not in ORIGINS or p.username or p.password or p.fragment:
        raise Stop('origine_http_refusee')


def _error_of(body):
    try:
        data = json.loads(body or b'{}')
    except (ValueError, UnicodeError):
        return '', ''
    err = data.get('error') if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get('code') or err.get('type') or ''), str(err.get('message') or '')
    if isinstance(err, str):
        return err, str(data.get('error_description') or '')
    return '', str(data.get('detail') or '') if isinstance(data, dict) else ''


def http(method, url, data=None, headers=None, timeout=60, stream=False):
    """Requête vers auth.openai.com ou api.openai.com ; aucune redirection suivie ; erreur avec son code OpenAI (jamais le corps
    complet, jamais un jeton). ``stream`` : rend la réponse ouverte (événements du flux)."""
    _check_origin(url)
    try:
        from .common import release_before_network
        release_before_network(urllib.parse.urlsplit(url).netloc)
    except ImportError:   # pragma: no cover
        pass

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    # 5.6.65 (audit 5.6.64, R10) : connexions suivies du mécanisme d'arrêt (arret5643) : « Arrêter » ou la pause ferment réellement le
    # flux en cours ; vérification TLS par défaut et refus des redirections conservés.
    import ssl
    from .common import _TrackedHTTPHandler, _TrackedHTTPSHandler
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), _TrackedHTTPHandler(),
                                         _TrackedHTTPSHandler(context=ssl.create_default_context()))
    from . import __version__
    req = urllib.request.Request(url, data=data, method=method, headers={'User-Agent': 'AxiorHub-Pilote/%s' % __version__, **(headers or {})})
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        code, detail = _error_of(e.read(65536))
        raise HTTPFailure(e.code, code, detail) from None
    except TimeoutError:
        raise Stop('delai_http_depasse') from None
    except (urllib.error.URLError, OSError):
        raise Stop('connexion_http_indisponible') from None
    if stream:
        return resp
    with resp:
        raw = resp.read(4_000_001)
    if len(raw) > 4_000_000:
        raise Stop('reponse_trop_volumineuse')
    return raw


def _json(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise Stop('json_http_invalide') from None


def _form(url, fields, transport=None):
    body = urllib.parse.urlencode(fields).encode()
    raw = (transport or http)('POST', url, body, {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'})
    return _json(raw) if raw else {}


# ------------------------------------------------------------------------------------------------ coffre (jetons chiffrés)
def _vault_path(state_dir):
    return Path(state_dir) / 'vault' / 'chatgpt5662' / 'compte.secret'


def load_record(state_dir):
    path = _vault_path(state_dir)
    if not path.is_file():
        return None
    from . import portable
    if portable.too_open(path.stat().st_mode, 0o077):
        raise Stop('secret_absent_ou_permissions_trop_larges')
    from .vault567 import decrypt
    try:
        return json.loads(decrypt(path, path.read_text(encoding='utf-8').strip()))
    except ValueError:
        raise Stop('secret_invalide') from None


def save_record(state_dir, record):
    from .vault567 import write
    return write(_vault_path(state_dir), json.dumps(record, ensure_ascii=False, separators=(',', ':')), limit=65536)


def clear_tokens(state_dir):
    record = load_record(state_dir)
    if record:
        for k in ('access_token', 'refresh_token', 'id_token'):
            record[k] = ''
        save_record(state_dir, record)


class _Lock:
    """Renouvellement sérialisé : deux travaux ne font jamais tourner le même jeton de renouvellement en même temps."""
    def __init__(self, state_dir):
        self.path = _vault_path(state_dir).parent / '.renouvellement.lock'

    def __enter__(self):
        from .portable import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fh = open(self.path, 'a')
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        from .portable import fcntl
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


# ------------------------------------------------------------------------------------------------ connexion
def host_id(desk):
    """Identifiant opaque et stable de CETTE installation (jamais un courriel ni un identifiant d'utilisateur)."""
    value = desk.settings(HOST_KEY, '') or ''
    if not value:
        value = 'urn:uuid:' + str(uuid.uuid4())
        desk.setting(HOST_KEY, value)
    return value


def account(desk):
    return desk.settings(ACCOUNT_KEY, {}) or {}


def start(desk, owner, port=DEFAULT_PORT):
    """Nouvelle tentative : ``state``, ``nonce`` et vérificateur PKCE neufs ; adresse de la page officielle d'OpenAI."""
    acc = account(desk)
    client_id = acc.get('client_id') or ''
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    redirect = 'http://127.0.0.1:%d%s' % (int(port), CALLBACK_PATH)
    params = {'response_type': 'code', 'client_id': client_id or DYNAMIC, 'redirect_uri': redirect, 'scope': SCOPES, 'resource': API,
              'state': state, 'nonce': nonce, 'code_challenge_method': 'S256',
              'code_challenge': _b64url(hashlib.sha256(verifier.encode()).digest()), 'ext_agent_host_id': host_id(desk)}
    if client_id:
        record = None
        try:
            record = load_record(desk.c['state_dir'])
        except Stop:
            record = None
        if record and record.get('id_token'):
            params['id_token_hint'] = record['id_token']
        if acc.get('email'):
            params['login_hint'] = acc['email']
    else:
        params['agent_name_hint'] = AGENT_NAME
    desk.setting(ATTEMPT_KEY, {'state': state, 'nonce': nonce, 'verifier': verifier, 'redirect_uri': redirect, 'client_id': client_id,
                               'owner': str(owner)[:120], 'expires': (_now() + timedelta(minutes=ATTEMPT_MINUTES)).isoformat()})
    desk.audit('chatgpt5662_connexion_demandee', {'par': str(owner)[:120], 'nouvelle_inscription': not client_id})
    return AUTHORIZE + '?' + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def _jwks_key(kid, transport=None):
    conf = _json((transport or http)('GET', DISCOVERY, None, {'Accept': 'application/json'}))
    uri = str(conf.get('jwks_uri') or '')
    _check_origin(uri)
    keys = _json((transport or http)('GET', uri, None, {'Accept': 'application/json'})).get('keys') or []
    for k in keys:
        if k.get('kid') == kid and k.get('kty') == 'RSA':
            return k
    raise Stop('chatgpt_jeton_identite_invalide')


def verify_id_token(token, client_id, nonce, transport=None, now=None):
    """Signature RS256 (clés publiées par OpenAI), émetteur, destinataire, expiration et nonce."""
    try:
        h64, p64, s64 = str(token).split('.')
        header, payload = json.loads(_b64d(h64)), json.loads(_b64d(p64))
    except (ValueError, TypeError):
        raise Stop('chatgpt_jeton_identite_invalide') from None
    if header.get('alg') != 'RS256':
        raise Stop('chatgpt_jeton_identite_invalide')
    key = _jwks_key(header.get('kid'), transport)
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
        public = RSAPublicNumbers(int.from_bytes(_b64d(key['e']), 'big'), int.from_bytes(_b64d(key['n']), 'big')).public_key()
        public.verify(_b64d(s64), (h64 + '.' + p64).encode(), padding.PKCS1v15(), hashes.SHA256())
    except ImportError:
        raise Stop('coffre_chiffrement_indisponible') from None
    except Exception:   # noqa: BLE001 — signature fausse ou clé illisible
        raise Stop('chatgpt_jeton_identite_invalide') from None
    aud = payload.get('aud')
    auds = aud if isinstance(aud, list) else [aud]
    stamp = (now or time.time())
    if payload.get('iss') != AUTH or client_id not in auds or float(payload.get('exp') or 0) < stamp - 60 or payload.get('nonce') != nonce:
        raise Stop('chatgpt_jeton_identite_invalide')
    return payload


def complete(desk, returned, transport=None):
    """Retour de la page d'OpenAI (adresse complète ou paramètres) : contrôle, échange du code, vérification, enregistrement chiffré."""
    if isinstance(returned, str):
        text = returned.strip()
        query = urllib.parse.urlsplit(text).query if '://' in text else text.lstrip('?')
        params = dict(urllib.parse.parse_qsl(query))
    else:
        params = {k: str(v) for k, v in dict(returned).items()}
    attempt = desk.settings(ATTEMPT_KEY, {}) or {}
    if not attempt or not params.get('state') or not secrets.compare_digest(str(params.get('state')), str(attempt.get('state', ''))):
        raise Stop('chatgpt_tentative_inconnue')
    if attempt.get('expires', '') < _now().isoformat():
        desk.setting(ATTEMPT_KEY, {})
        raise Stop('chatgpt_tentative_expiree')
    if params.get('error'):
        desk.setting(ATTEMPT_KEY, {})
        raise Stop('chatgpt_autorisation_refusee' if params['error'] == 'access_denied' else 'chatgpt_autorisation_echouee')
    client_id = attempt.get('client_id') or ''
    if client_id:
        if params.get('client_id') and params['client_id'] != client_id:
            raise Stop('chatgpt_client_different')      # jamais remplacer l'inscription d'un compte par une autre
    else:
        client_id = params.get('client_id') or ''
        if not client_id or client_id == DYNAMIC:
            raise Stop('chatgpt_inscription_incomplete')
    if not params.get('code'):
        raise Stop('chatgpt_code_absent')
    desk.setting(ATTEMPT_KEY, {})                      # un code ne s'échange qu'une fois
    try:
        tok = _form(TOKEN, {'grant_type': 'authorization_code', 'client_id': client_id, 'code': params['code'],
                            'code_verifier': attempt['verifier'], 'redirect_uri': attempt['redirect_uri'], 'resource': API}, transport)
    except HTTPFailure as ex:
        raise Stop('chatgpt_code_refuse' if ex.code == 'invalid_grant' else 'chatgpt_autorisation_echouee') from None
    claims = verify_id_token(tok.get('id_token', ''), client_id, attempt['nonce'], transport)
    scopes = sorted(str(tok.get('scope') or params.get('scope') or '').replace('+', ' ').split())
    previous = account(desk)
    if previous.get('subject') and previous.get('client_id') == client_id and previous['subject'] != claims.get('sub'):
        raise Stop('chatgpt_compte_different')
    record = {'email': str(claims.get('email') or ''), 'issuer': AUTH, 'subject': str(claims.get('sub') or ''), 'client_id': client_id,
              'ext_agent_host_id': host_id(desk), 'id_token': tok.get('id_token', ''), 'access_token': tok.get('access_token', ''),
              'refresh_token': tok.get('refresh_token', ''), 'token_type': tok.get('token_type', 'Bearer'),
              'expires_at': (_now() + timedelta(seconds=int(tok.get('expires_in') or 3600))).isoformat(), 'scopes': scopes,
              'saved_at': _now().isoformat()}
    save_record(desk.c['state_dir'], record)
    plan = PLAN_SCOPE in scopes
    desk.setting(ACCOUNT_KEY, {'email': record['email'], 'subject': record['subject'], 'client_id': client_id, 'scopes': scopes,
                               'plan': plan, 'connecte_le': record['saved_at'], 'par': attempt.get('owner', ''), 'etat': 'connecte',
                               'bienvenue_vue': bool(previous.get('bienvenue_vue')) and previous.get('client_id') == client_id})
    desk.audit('chatgpt5662_connecte', {'par': attempt.get('owner', ''), 'abonnement_utilisable': plan})
    if plan:
        try:
            refresh_models(desk, transport)
        except Stop:
            pass
    return {'ok': True, 'plan': plan, 'email': record['email']}


def access_token(state_dir, transport=None, force=False):
    """Jeton d'accès valide (renouvelé s'il expire dans moins de 5 min) ; erreur claire s'il faut se reconnecter."""
    with _Lock(state_dir):
        record = load_record(state_dir)
        if not record or not record.get('refresh_token'):
            raise Stop('chatgpt_non_connecte')
        try:
            left = datetime.fromisoformat(record.get('expires_at', '')) - _now()
        except ValueError:
            left = timedelta(0)
        if not force and record.get('access_token') and left > timedelta(minutes=5):
            return record['access_token']
        try:
            tok = _form(TOKEN, {'grant_type': 'refresh_token', 'client_id': record['client_id'], 'refresh_token': record['refresh_token'],
                                'resource': API}, transport)
        except HTTPFailure as ex:
            if ex.code in REFRESH_DEAD or ex.status in (400, 401):
                for k in ('access_token', 'refresh_token'):
                    record[k] = ''
                save_record(state_dir, record)
                raise Stop('chatgpt_reconnexion_requise') from None
            if ex.code == 'invalid_client':
                raise Stop('chatgpt_client_invalide') from None
            raise Stop('chatgpt_indisponible') from None
        record.update({'access_token': tok.get('access_token', ''), 'expires_at': (_now() + timedelta(seconds=int(tok.get('expires_in') or 3600))).isoformat(),
                       'saved_at': _now().isoformat()})
        if tok.get('refresh_token'):
            record['refresh_token'] = tok['refresh_token']   # jeton tournant : toujours conserver le dernier
        if tok.get('scope'):
            record['scopes'] = sorted(str(tok['scope']).split())
        save_record(state_dir, record)
        return record['access_token']


def refresh_models(desk, transport=None):
    token = access_token(desk.c['state_dir'], transport)
    data = _json((transport or http)('GET', API + '/models', None, {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}))
    rows = data.get('models') if isinstance(data.get('models'), list) else data.get('data') or []
    models = [{'slug': str(m.get('slug') or m.get('id') or ''), 'nom': str(m.get('display_name') or m.get('slug') or m.get('id') or '')}
              for m in rows if isinstance(m, dict) and m.get('visibility', 'list') == 'list' and (m.get('slug') or m.get('id'))]
    desk.setting(MODELS_KEY, {'modeles': models[:40], 'le': _now().isoformat()})
    return models


def disconnect(desk, owner, transport=None):
    """Révocation du jeton de renouvellement chez OpenAI, puis effacement local ; l'inscription (client) reste pour une reconnexion."""
    record = None
    try:
        record = load_record(desk.c['state_dir'])
    except Stop:
        record = None
    confirmed = False
    if record and record.get('refresh_token'):
        try:
            conf = _json((transport or http)('GET', DISCOVERY, None, {'Accept': 'application/json'}))
            endpoint = str(conf.get('revocation_endpoint') or '')
            _check_origin(endpoint)
            _form(endpoint, {'token': record['refresh_token'], 'token_type_hint': 'refresh_token', 'client_id': record['client_id']}, transport)
            confirmed = True
        except Stop:
            confirmed = False
    if record:
        clear_tokens(desk.c['state_dir'])
    acc = account(desk)
    desk.setting(ACCOUNT_KEY, {**acc, 'etat': 'deconnecte', 'plan': False})
    prov = desk.settings('ai:provider:' + PROVIDER_ID, {}) or {}
    if prov:
        prov['enabled'] = False
        desk.setting('ai:provider:' + PROVIDER_ID, prov)
        desk.c.setdefault('ai_providers', {})[PROVIDER_ID] = prov
    desk.audit('chatgpt5662_deconnecte', {'par': str(owner)[:120], 'revocation_confirmee': confirmed})
    return {'ok': True, 'revocation_confirmee': confirmed,
            'message': 'ChatGPT déconnecté.' + ('' if confirmed else ' La révocation chez OpenAI n’a pas pu être confirmée : vous pouvez aussi '
                                                                        'retirer l’accès dans ChatGPT › Paramètres.')}


def save_provider(desk, owner, model, enabled, allow_external):
    """Fournisseur « chatgpt » du registre : choisi ensuite par fonction (routage, mode mixte) comme les autres fournisseurs."""
    import re
    acc = account(desk)
    if enabled and not (acc.get('etat') == 'connecte' and acc.get('plan')):
        raise Stop('chatgpt_non_connecte')
    if enabled and not allow_external:
        raise Stop('autorisation_donnees_externes_requise')
    model = str(model or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}', model):
        raise Stop('modele_fournisseur_invalide')
    value = {'type': 'chatgpt', 'url': API, 'model': model, 'enabled': bool(enabled), 'external_data_allowed': bool(allow_external),
             'secret_file': '', 'timeout_seconds': 240, 'max_context_chars': 120000, 'temperature': 0, 'structured_mode': 'strict',
             'zdr_required': False, 'pricing_confirmed_free': True, 'state_dir': desk.c['state_dir']}
    desk.setting('ai:provider:' + PROVIDER_ID, value)
    desk.c.setdefault('ai_providers', {})[PROVIDER_ID] = value
    desk.audit('fournisseur_ia_modifie', {'provider': PROVIDER_ID, 'type': 'chatgpt', 'enabled': value['enabled'],
                                         'external_data_allowed': value['external_data_allowed'], 'par': str(owner)[:120]})
    return {'ok': True, 'message': 'ChatGPT %s (modèle %s).' % ('activé' if enabled else 'enregistré, non activé', model)}


# ------------------------------------------------------------------------------------------------ inférence (API Responses)
def build_request(model, messages, json_schema=None, strict=True):
    """Messages AxiorHub → requête Responses admise pour l'abonnement : consignes dans ``instructions`` (les éléments « system » sont
    refusés), historique complet dans ``input``, ``store: false``, ``stream: true`` ; aucun paramètre non admis (température, max…)."""
    system = '\n\n'.join(str(m.get('content') or '') for m in messages if m.get('role') == 'system')
    items = [{'role': 'assistant' if m.get('role') == 'assistant' else 'user', 'content': str(m.get('content') or '')}
             for m in messages if m.get('role') != 'system']
    req = {'model': model, 'input': items or [{'role': 'user', 'content': ''}], 'store': False, 'stream': True}
    if system:
        req['instructions'] = system
    if json_schema is not None:
        req['text'] = {'format': {'type': 'json_schema', 'name': 'axiorhub_result', 'schema': json_schema, 'strict': True} if strict
                       else {'type': 'json_object'}}
    return req


STREAM_MAX_SECONDS = 1800   # plafond d'une réponse en flux (le délai de lecture porte sur le silence entre deux événements)


def read_stream(lines, deadline=None):
    """Événements du flux Responses → (texte, usage). Seul ``response.completed`` vaut succès."""
    parts, usage, done, final_text = [], {}, False, ''
    deadline = deadline or (time.monotonic() + STREAM_MAX_SECONDS)
    for raw in lines:
        if time.monotonic() > deadline:
            raise Stop('generation_ia_delai_depasse')   # jamais d'attente sans fin : le travail échoue, la file continue
        line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        line = line.strip()
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if not data or data == '[DONE]':
            continue
        try:
            ev = json.loads(data)
        except ValueError:
            continue
        kind = ev.get('type', '')
        if kind == 'response.output_text.delta':
            parts.append(str(ev.get('delta') or ''))
        elif kind == 'response.completed':
            resp = ev.get('response') or {}
            usage = resp.get('usage') or {}
            for item in resp.get('output') or []:
                for c in (item.get('content') or []) if isinstance(item, dict) else []:
                    if isinstance(c, dict) and c.get('type') == 'output_text':
                        final_text += str(c.get('text') or '')
            done = True
            break
        elif kind in ('response.failed', 'error'):
            err = (ev.get('response') or {}).get('error') or ev.get('error') or ev
            code = str((err or {}).get('code') or '')
            raise Stop(PLAN_ERRORS.get(code, 'chatgpt_generation_echouee'))
        elif kind == 'response.incomplete':
            raise Stop('generation_ia_incomplete')
    if not done:
        raise Stop('generation_ia_incomplete')            # flux interrompu : jamais un succès partiel
    text = ''.join(parts) or final_text
    if not text.strip():
        raise Stop('reponse_ia_vide')
    return text.strip(), usage


def respond(state_dir, model, messages, json_schema=None, strict=True, transport=None, timeout=240):
    """Une requête Responses (une reprise après renouvellement si le jeton est refusé). Rend (texte, usage)."""
    body = json.dumps(build_request(model, messages, json_schema, strict), ensure_ascii=False).encode()
    for attempt in (1, 2):
        token = access_token(state_dir, transport, force=attempt == 2)
        try:
            resp = (transport or http)('POST', API + '/responses', body, {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                                                                         'Accept': 'text/event-stream'}, timeout=timeout, stream=True)
        except HTTPFailure as ex:
            if ex.code in PLAN_ERRORS:
                if PLAN_ERRORS[ex.code] == 'chatgpt_reconnexion_requise' and attempt == 1:
                    continue
                raise Stop(PLAN_ERRORS[ex.code]) from None
            if ex.status == 401 and attempt == 1:
                continue
            raise Stop({401: 'chatgpt_reconnexion_requise', 403: 'chatgpt_acces_refuse', 429: 'chatgpt_limite_usage_atteinte',
                        503: 'chatgpt_indisponible'}.get(ex.status, 'chatgpt_generation_echouee')) from None
        try:
            return read_stream(iter(resp.readline, b'') if hasattr(resp, 'readline') else resp)
        except (TimeoutError, OSError):
            raise Stop('generation_ia_delai_depasse') from None   # flux silencieux au-delà du délai : erreur claire, sans blocage
        finally:
            try:
                resp.close()
            except Exception:   # noqa: BLE001
                pass
    raise Stop('chatgpt_reconnexion_requise')


# ------------------------------------------------------------------------------------------------ retour local (postes)
def listen(config, port=DEFAULT_PORT, minutes=ATTEMPT_MINUTES):
    """Poste : écoute UNE fois ``http://127.0.0.1:<port>/auth/callback`` (navigateur sur la même machine) puis s'arrête. Rend le port
    réellement ouvert (``port`` s'il est libre, sinon un port libre : OpenAI admet un autre port tant que le chemin est identique)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            p = urllib.parse.urlsplit(self.path)
            if p.path != CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return
            from .desk import Desk
            try:
                desk = Desk(config)
                try:
                    out = complete(desk, p.query)
                finally:
                    desk.db.close()
                msg = ('Connexion à ChatGPT réussie' + (' : votre abonnement peut être utilisé par AxiorHub.' if out['plan'] else
                                                        ' ; l’utilisation de votre abonnement n’a pas été autorisée.'))
                ok = True
            except Stop as ex:
                from .erreurs5634 import MESSAGES
                msg, ok = MESSAGES.get(str(ex), 'Connexion à ChatGPT impossible (%s).' % str(ex)), False
            page = ('<!doctype html><meta charset="utf-8"><title>AxiorHub Pilote</title><body style="font-family:system-ui;margin:3rem;max-width:40rem">'
                    '<h1>%s</h1><p>%s</p><p>Vous pouvez fermer cet onglet et revenir à AxiorHub.</p></body>') % (
                'AxiorHub Pilote' if ok else 'Connexion non aboutie', msg.replace('&', '&amp;').replace('<', '&lt;'))
            data = page.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.server.done = True

    server = None
    for candidate in (int(port), 0):
        try:
            server = HTTPServer(('127.0.0.1', candidate), Handler)
            break
        except OSError:
            continue
    if server is None:
        raise Stop('chatgpt_port_local_indisponible')
    server.done = False
    server.timeout = 1
    deadline = time.monotonic() + minutes * 60

    def loop():
        try:
            while not server.done and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
    threading.Thread(target=loop, name='chatgpt5662-retour', daemon=True).start()
    return server.server_address[1]


# ------------------------------------------------------------------------------------------------ affichage (Paramètres › Modèles d'IA)
def html(desk, prefix, role, local):
    from html import escape as e
    acc = account(desk)
    admin = role == 'administrateur'
    prov = desk.settings('ai:provider:' + PROVIDER_ID, {}) or {}
    head = ('<p>Utilisez votre abonnement <strong>ChatGPT Plus ou Pro</strong> pour la rédaction et l’analyse, sans clé API, grâce au '
            'programme officiel d’OpenAI « Sign in with ChatGPT ». Les demandes d’AxiorHub sont décomptées de votre abonnement (limite '
            'partagée avec ChatGPT et les autres applications connectées).</p>'
            '<details><summary>Confidentialité : à lire avant de connecter</summary><ul>'
            '<li>Les textes envoyés à ChatGPT sont <strong>pseudonymisés</strong> (noms, adresses, identifiants remplacés) et passent par le '
            'bouclier contre les instructions cachées, comme pour tout fournisseur externe ; rien ne part sans l’autorisation « données '
            'externes » du cabinet, et les dossiers exclus restent en local.</li>'
            '<li>AxiorHub demande à OpenAI de ne pas conserver les échanges (<code>store: false</code>). Dans ChatGPT › Paramètres › '
            'Gestion des données, <strong>désactivez « Améliorer le modèle pour tous »</strong>.</li>'
            '<li>Un abonnement ChatGPT est personnel : chaque avocat devrait utiliser son propre compte.</li>'
            '<li>Programme en aperçu : OpenAI peut changer les modèles, limites ou conditions ; gardez un modèle local en secours.</li>'
            '</ul></details>')
    body = head
    connected = acc.get('etat') == 'connecte'
    if connected:
        body += '<p>Compte connecté : <strong>%s</strong> · ' % e(acc.get('email') or 'compte ChatGPT')
        body += ('<span class="ax-ok">utilisation de l’abonnement autorisée</span>' if acc.get('plan') else
                 '<span class="d51-warn">utilisation de l’abonnement NON autorisée</span> — reconnectez-vous et acceptez cette permission.')
        body += ' · <a href="%s" target="_blank" rel="noopener">Gérer l’usage</a></p>' % e(USAGE_URL, quote=True)
        if not acc.get('bienvenue_vue') and acc.get('plan'):
            body += ('<div class="d51-banner" role="status"><strong>Vous utilisez votre abonnement ChatGPT.</strong> Les demandes qu’AxiorHub '
                     'confie à ChatGPT sont décomptées de votre abonnement ; gérez l’usage et les limites dans ChatGPT › Paramètres › '
                     'Utilisation.<form class="m5-form d51-inline" data-api="m5662/chatgpt/bienvenue" data-reload><button class="ax-btn ghost" '
                     'type="submit">J’ai compris</button></form></div>')
    if not admin:
        return '<section class="ax-card" id="chatgpt5662"><h2>ChatGPT (votre abonnement)</h2>%s<p class="ax-muted">La connexion est réglée '\
               'par un administrateur.</p></section>' % body
    if connected and acc.get('plan'):
        models = (desk.settings(MODELS_KEY, {}) or {}).get('modeles') or []
        current = prov.get('model') or (models[0]['slug'] if models else '')
        opts = ''.join('<option value="%s"%s>%s</option>' % (e(m['slug'], quote=True), ' selected' if m['slug'] == current else '', e(m['nom']))
                       for m in models) or '<option value="%s">%s</option>' % (e(current, quote=True), e(current or 'liste à actualiser'))
        body += ('<form class="m5-form" data-api="m5662/chatgpt/fournisseur" data-reload>'
                 '<label class="m5-field">Modèle ChatGPT<select name="model">%s</select></label>'
                 '<label class="m5-check"><input type="checkbox" name="actif"%s> Utiliser ChatGPT dans AxiorHub</label>'
                 '<label class="m5-check"><input type="checkbox" name="externe"%s> J’autorise l’envoi des textes, pseudonymisés, à OpenAI</label>'
                 '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>'
                 '<p class="ax-muted">Ensuite, choisissez les fonctions confiées à ChatGPT dans « Mode mixte » ou « Routage » ci-dessus (le tri et '
                 'les pièces jointes peuvent rester en local).</p>') % (opts, ' checked' if prov.get('enabled') else '',
                                                                       ' checked' if prov.get('external_data_allowed') else '')
        body += ('<form class="m5-form d51-inline" data-api="m5662/chatgpt/modeles" data-reload><button class="ax-btn ghost" type="submit">'
                 'Actualiser la liste des modèles</button></form>')
    attempt = desk.settings(ATTEMPT_KEY, {}) or {}
    pending = attempt and attempt.get('expires', '') > _now().isoformat()
    body += ('<form class="d51-inline c5662-connect" data-c5662-open><button class="ax-btn c5662-siwc" type="submit">'
             'Continuer avec ChatGPT</button><p role="status" class="m5-status"></p></form>')
    if connected:
        body += ('<form class="m5-form d51-inline" data-api="m5662/chatgpt/deconnecter" data-reload><button class="ax-btn ghost" type="submit">'
                 'Déconnecter</button></form>')
    if not local:
        body += ('<details%s><summary>Après la connexion (AxiorHub installé sur un serveur)</summary><p>Après votre accord sur la page '
                 'd’OpenAI, votre navigateur affiche une page introuvable dont l’adresse commence par <code>http://127.0.0.1:1455/auth/callback</code>. '
                 'C’est normal : copiez cette adresse complète et collez-la ici. Elle ne contient qu’un code à usage unique, '
                 'inutilisable sans le secret resté sur le serveur.</p><form class="m5-form" data-api="m5662/chatgpt/terminer" data-reload>'
                 '<label class="m5-field">Adresse affichée après l’accord<input name="adresse" maxlength="4000" autocomplete="off" '
                 'placeholder="http://127.0.0.1:1455/auth/callback?code=…"></label><button class="ax-btn" type="submit">Terminer la connexion</button>'
                 '<p role="status" class="m5-status"></p></form></details>') % (' open' if pending else '')
    return '<section class="ax-card" id="chatgpt5662"><h2>ChatGPT (votre abonnement)</h2>%s</section>' % body

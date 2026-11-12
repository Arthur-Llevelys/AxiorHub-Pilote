"""5.6.97 (J06) : connexion par compte (OAuth) aux sources juridiques MCP — parcours propre aux sources, distinct de l'abonnement
ChatGPT (chatgpt5662) et des connexions de comptes (connexions5652).

Spécification d'autorisation MCP (révision 2025-06-18) :
- découverte limitée aux origines de la fiche (``sources5632.PROVIDERS[...]['oauth']['origines']``) : métadonnées de la ressource
  protégée (RFC 9728) puis du serveur d'autorisation (RFC 8414) ; un serveur, un point d'autorisation ou de jeton hors de ces origines
  est refusé ; PKCE S256 exigé du serveur ;
- client public enregistré dynamiquement (RFC 7591) quand la fiche le prévoit, pour l'adresse de retour du serveur AxiorHub ; sinon
  « client non reconnu » : la carte affiche l'aide et ne propose que les modes validés ;
- code d'autorisation + PKCE S256, état à usage unique (10 minutes) lié au compte et à la session, adresse de retour enregistrée,
  paramètre ``resource`` (RFC 8707) = adresse MCP de la fiche : le jeton est limité à cette ressource ;
- jetons chiffrés au coffre (vault567) par connexion ; renouvellement sérialisé (compteur de version) ; révocation explicite.
Aucun jeton n'apparaît dans le catalogue, les reçus, l'audit ou les messages d'erreur. Aucun appel réel dans les tests : le transport
est injecté (``transport(méthode, url, données)`` → dict JSON).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode, urlsplit

from .common import Stop

DUREE_ETAT = timedelta(minutes=10)
PREFIXE = 'oauth_mcp5697:'


def _now():
    return datetime.now(timezone.utc)


def _fiche(pid):
    from .sources5632 import provider
    p = provider(pid)
    oauth = p.get('oauth') or {}
    if not p.get('qualified') or 'oauth2' not in p.get('auth', ()) or oauth.get('etat') != 'qualifie':
        raise Stop('oauth_source_non_qualifie')
    return p, oauth


def _origine(url):
    parts = urlsplit(str(url or ''))
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise Stop('adresse_oauth_invalide')
    return 'https://' + parts.netloc.lower()


def _autorisee(url, p, oauth):
    origine = _origine(url)
    permises = {_origine(p['endpoint'])} | {o.rstrip('/').lower() for o in oauth.get('origines', ())}
    if origine not in permises:
        raise Stop('origine_oauth_non_autorisee')
    return str(url)


def transport_reseau(methode, url, donnees=None):
    """Transport réel (jamais utilisé par les tests) : HTTPS, hôte public vérifié, pas de redirection, réponse JSON bornée."""
    from .common import HTTP
    from .extensions364 import validate_endpoint
    origine = _origine(url)
    validate_endpoint(origine, resolve=True)
    client = HTTP(origine, timeout=30)
    corps, entetes = None, {'Accept': 'application/json'}
    if donnees is not None:
        corps = urlencode(donnees).encode()
        entetes['Content-Type'] = 'application/x-www-form-urlencoded'
    raw = client.request(methode, url, corps, entetes, 200_000)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise Stop('reponse_oauth_illisible') from None
    if not isinstance(value, dict):
        raise Stop('reponse_oauth_illisible')
    return value


def _bien_connu(base, nom):
    """Adresse « bien connue » avec insertion du chemin (RFC 8414 § 3.1, RFC 9728 § 3.1)."""
    parts = urlsplit(base)
    chemin = parts.path.rstrip('/')
    return 'https://%s/.well-known/%s%s' % (parts.netloc, nom, chemin)


def decouvrir(pid, transport=None):
    p, oauth = _fiche(pid)
    transport = transport or transport_reseau
    ressource = p['endpoint'].rstrip('/')
    prm = transport('GET', _bien_connu(ressource, 'oauth-protected-resource'))
    if str(prm.get('resource') or '').rstrip('/') != ressource:
        raise Stop('ressource_oauth_incoherente')
    serveurs = [s for s in (prm.get('authorization_servers') or []) if isinstance(s, str)]
    serveur = None
    for candidat in serveurs:
        try:
            serveur = _autorisee(candidat, p, oauth)
            break
        except Stop:
            continue
    if not serveur:
        raise Stop('serveur_autorisation_non_autorise')
    meta = transport('GET', _bien_connu(serveur, 'oauth-authorization-server'))
    if str(meta.get('issuer') or '').rstrip('/') != serveur.rstrip('/'):
        raise Stop('emetteur_oauth_incoherent')
    if 'S256' not in (meta.get('code_challenge_methods_supported') or []):
        raise Stop('pkce_s256_non_pris_en_charge')
    out = {'ressource': ressource, 'serveur': serveur, 'portees': [str(s)[:100] for s in (prm.get('scopes_supported') or []) if isinstance(s, str)][:20]}
    for cle in ('authorization_endpoint', 'token_endpoint'):
        if not meta.get(cle):
            raise Stop('metadonnees_oauth_incompletes')
        out[cle] = _autorisee(meta[cle], p, oauth)
    for cle in ('registration_endpoint', 'revocation_endpoint'):
        out[cle] = _autorisee(meta[cle], p, oauth) if meta.get(cle) else ''
    return out


def _client(desk, pid, oauth, meta, redirect_uri, transport):
    """Client public AxiorHub pour cette adresse de retour : enregistrement dynamique si la fiche le prévoit."""
    cle = PREFIXE + 'client:' + pid
    client = desk.settings(cle, {}) or {}
    if client.get('client_id') and client.get('redirect_uri') == redirect_uri and client.get('serveur') == meta['serveur']:
        return client['client_id']
    if not (oauth.get('enregistrement_dynamique') and meta.get('registration_endpoint')):
        raise Stop('client_oauth_non_reconnu')
    rep = transport('POST', meta['registration_endpoint'], {'client_name': 'AxiorHub Pilote', 'redirect_uris': redirect_uri,
                                                            'grant_types': 'authorization_code refresh_token', 'response_types': 'code',
                                                            'token_endpoint_auth_method': 'none'})
    client_id = str(rep.get('client_id') or '')
    if not client_id or len(client_id) > 300:
        raise Stop('enregistrement_client_oauth_refuse')
    desk.setting(cle, {'client_id': client_id, 'redirect_uri': redirect_uri, 'serveur': meta['serveur'], 'at': _now().isoformat()})
    return client_id


def _purger(desk):
    limite = (_now() - DUREE_ETAT).isoformat()
    desk.db.execute("DELETE FROM settings WHERE key LIKE ? AND json_extract(value, '$.created') < ?", (PREFIXE + 'etat:%', limite))
    desk.db.commit()


def marqueur_session(env):
    """Empreinte du cookie de session (jamais la valeur) : l'état OAuth n'est valable que dans la session qui l'a demandé."""
    for part in str((env or {}).get('HTTP_COOKIE', '')).split(';'):
        if '=' in part:
            k, v = part.strip().split('=', 1)
            if k == 'axiorhub_session' and v:
                return hashlib.sha256(v.encode()).hexdigest()
    return ''


def demarrer(desk, owner, session, pid, redirect_uri, retour='parametres', transport=None):
    from .adaptateurs5652 import pkce_pair
    p, oauth = _fiche(pid)
    transport = transport or transport_reseau
    if urlsplit(redirect_uri).scheme != 'https':
        raise Stop('adresse_retour_oauth_invalide')
    meta = decouvrir(pid, transport)
    client_id = _client(desk, pid, oauth, meta, redirect_uri, transport)
    verifier, challenge = pkce_pair()
    etat = secrets.token_urlsafe(32)
    _purger(desk)
    desk.setting(PREFIXE + 'etat:' + hashlib.sha256(etat.encode()).hexdigest(),
                 {'provider': pid, 'owner': owner, 'session': session, 'verifier': verifier, 'redirect_uri': redirect_uri, 'client_id': client_id,
                  'token_endpoint': meta['token_endpoint'], 'revocation_endpoint': meta['revocation_endpoint'], 'ressource': meta['ressource'],
                  'retour': retour if re.fullmatch(r'guide:[A-Za-z0-9_-]{1,80}', str(retour)) else 'parametres', 'created': _now().isoformat()})
    query = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri, 'state': etat, 'code_challenge': challenge,
             'code_challenge_method': 'S256', 'resource': meta['ressource']}
    if meta['portees']:
        query['scope'] = ' '.join(meta['portees'])
    desk.audit('source_oauth_5697_demarree', {'provider': pid, 'par': owner})
    return {'url': meta['authorization_endpoint'] + ('&' if '?' in meta['authorization_endpoint'] else '?') + urlencode(query)}


def _coffre(desk, pid, nom):
    return Path(desk.c['state_dir']) / 'vault' / 'sources5697' / pid / (nom + '.secret')


def _stocker(desk, pid, tok, version_attendue):
    """Remplacement sérialisé : un renouvellement concurrent déjà enregistré fait foi (False)."""
    from .vault567 import write
    if not isinstance(tok, dict) or not tok.get('access_token') or str(tok.get('token_type', 'bearer')).lower() != 'bearer':
        raise Stop('jeton_oauth_invalide')
    cle = PREFIXE + 'jetons:' + pid
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        courant = desk.settings(cle, {}) or {}
        if int(courant.get('version') or 0) != int(version_attendue):
            desk.db.rollback()
            return False
        write(_coffre(desk, pid, 'access'), tok['access_token'])
        if tok.get('refresh_token'):
            write(_coffre(desk, pid, 'refresh'), tok['refresh_token'])
        expire = (_now() + timedelta(seconds=max(60, int(tok.get('expires_in') or 3600))) - timedelta(seconds=60)).isoformat()
        droits = [s for s in str(tok.get('scope') or '').split() if s][:30]
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (cle, json.dumps({'version': int(version_attendue) + 1, 'expire': expire,
                                                                                         'droits': droits})))
        desk.db.commit()
        return True
    except BaseException:
        desk.db.rollback()
        raise


def _maj_fiche(desk, pid, **changements):
    from .sources5632 import PROVIDERS, VERIFIED_ON_5697, item_id, _save
    p = PROVIDERS[pid]
    eid = item_id(pid)
    item = desk.settings('lawve:item:' + eid, {}) or {}
    now = _now().isoformat()
    if not item:
        item = {'id': eid, 'kind': 'connector', 'author': 'axiorhub', 'slug': pid, 'provider': pid, 'name': p['name'], 'source_url': p['help'],
                'license': '', 'endpoint': p['endpoint'].rstrip('/'), 'transport': 'streamable_http', 'auth_type': 'oauth2', 'secret_file': '',
                'secret_version': 0, 'candidate': {}, 'external_data_allowed': False, 'local_only': False, 'purposes': ['legal_analysis', 'hearing'],
                'preferred_tools': dict(p['tools']), 'enabled': False, 'status': 'configured', 'created_at': now, 'last_test': {},
                'schema_valide': '', 'revision': 0, 'catalogue_verified_on': VERIFIED_ON_5697, 'oauth': {}}
    item.update(changements)
    from .sources5632 import _cabinet
    item['cabinet'] = _cabinet(desk)
    item['updated_at'] = now
    _save(desk, item)
    return item


def terminer(desk, owner, session, args, transport=None):
    """Retour du fournisseur : état à usage unique, compte et session vérifiés, échange du code avec le vérificateur PKCE."""
    transport = transport or transport_reseau
    etat = str((args or {}).get('state') or '')
    cle = PREFIXE + 'etat:' + hashlib.sha256(etat.encode()).hexdigest()
    rec = desk.settings(cle) if etat else None
    if rec:
        desk.db.execute('DELETE FROM settings WHERE key=?', (cle,))
        desk.db.commit()
    if not rec or rec.get('created', '') < (_now() - DUREE_ETAT).isoformat() or rec.get('owner') != owner or rec.get('session') != session:
        raise Stop('etat_oauth_invalide')
    pid = rec['provider']
    _fiche(pid)
    if args.get('error'):
        desk.audit('source_oauth_5697_refusee', {'provider': pid, 'erreur': str(args.get('error'))[:40]})
        raise Stop('autorisation_refusee')
    code = str(args.get('code') or '')
    if not code or len(code) > 2000:
        raise Stop('etat_oauth_invalide')
    tok = transport('POST', rec['token_endpoint'], {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': rec['redirect_uri'],
                                                    'client_id': rec['client_id'], 'code_verifier': rec['verifier'], 'resource': rec['ressource']})
    version = int((desk.settings(PREFIXE + 'jetons:' + pid, {}) or {}).get('version') or 0)
    _stocker(desk, pid, tok, version)
    jetons = desk.settings(PREFIXE + 'jetons:' + pid, {}) or {}
    _maj_fiche(desk, pid, auth_type='oauth2', secret_file=str(_coffre(desk, pid, 'access')), secret_version=int(jetons.get('version') or 1),
               oauth={'etat': 'connecte', 'droits': jetons.get('droits', []), 'expire': jetons.get('expire', ''),
                      'token_endpoint': rec['token_endpoint'], 'revocation_endpoint': rec['revocation_endpoint'], 'client_id': rec['client_id'],
                      'ressource': rec['ressource']}, last_test={})
    desk.audit('source_oauth_5697_connectee', {'provider': pid, 'droits': jetons.get('droits', []), 'par': owner})
    return {'provider': pid, 'droits': jetons.get('droits', []), 'retour': rec.get('retour', 'parametres')}


def renouveler(desk, pid, transport=None):
    """Renouvellement par connexion ; échec (refus, jeton absent, rotation ratée) = reconnexion requise, anciens fichiers conservés."""
    from .common import read_secret
    transport = transport or transport_reseau
    item = desk.settings('lawve:item:connector-axiorhub-' + pid, {}) or {}
    oauth = item.get('oauth') or {}
    refresh = _coffre(desk, pid, 'refresh')
    version = int((desk.settings(PREFIXE + 'jetons:' + pid, {}) or {}).get('version') or 0)
    if not refresh.is_file() or not oauth.get('token_endpoint'):
        raise Stop('reconnexion_oauth_requise')
    try:
        tok = transport('POST', oauth['token_endpoint'], {'grant_type': 'refresh_token', 'refresh_token': read_secret(str(refresh)),
                                                          'client_id': oauth.get('client_id', ''), 'resource': oauth.get('ressource', '')})
        _stocker(desk, pid, tok, version)
    except Stop as ex:
        _maj_fiche(desk, pid, oauth={**oauth, 'etat': 'reconnexion_requise'})
        desk.audit('source_oauth_5697_renouvellement_refuse', {'provider': pid, 'erreur': str(ex).split(':')[0]})
        raise Stop('reconnexion_oauth_requise') from None
    jetons = desk.settings(PREFIXE + 'jetons:' + pid, {}) or {}
    _maj_fiche(desk, pid, secret_version=int(jetons.get('version') or 0), oauth={**oauth, 'etat': 'connecte', 'droits': jetons.get('droits', []),
                                                                                 'expire': jetons.get('expire', '')})
    return jetons


def jeton_valide(desk, pid, transport=None):
    """Chemin du jeton d'accès en vigueur (renouvelé s'il expire) ; jamais sa valeur."""
    jetons = desk.settings(PREFIXE + 'jetons:' + pid, {}) or {}
    access = _coffre(desk, pid, 'access')
    if not access.is_file() or not jetons:
        raise Stop('reconnexion_oauth_requise')
    if str(jetons.get('expire') or '') <= _now().isoformat():
        renouveler(desk, pid, transport)
    return str(access)


def revoquer(desk, pid, owner='', transport=None):
    """Déconnexion explicite : révocation chez le fournisseur si elle est publiée (échec signalé, pas masqué), jetons effacés,
    source désactivée."""
    from .common import read_secret
    item = desk.settings('lawve:item:connector-axiorhub-' + pid, {}) or {}
    oauth = item.get('oauth') or {}
    revoque_distant = 'non_publiee'
    if oauth.get('revocation_endpoint') and _coffre(desk, pid, 'refresh').is_file():
        try:
            (transport or transport_reseau)('POST', oauth['revocation_endpoint'], {'token': read_secret(str(_coffre(desk, pid, 'refresh'))),
                                                                                   'token_type_hint': 'refresh_token', 'client_id': oauth.get('client_id', '')})
            revoque_distant = 'ok'
        except Stop:
            revoque_distant = 'a_verifier'
    for nom in ('access', 'refresh'):
        try:
            _coffre(desk, pid, nom).unlink()
        except OSError:
            pass
    desk.db.execute('DELETE FROM settings WHERE key=?', (PREFIXE + 'jetons:' + pid,))
    desk.db.commit()
    _maj_fiche(desk, pid, enabled=False, status='disabled', secret_file='', oauth={'etat': 'revoque', 'droits': [], 'revocation': revoque_distant})
    desk.audit('source_oauth_5697_revoquee', {'provider': pid, 'revocation_distante': revoque_distant, 'par': owner})
    return {'provider': pid, 'revocation_distante': revoque_distant}

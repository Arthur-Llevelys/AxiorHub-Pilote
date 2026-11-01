"""5.6.88 : jetons d'API par personne, avec droits limités.

L'API REST n'avait qu'un jeton de service sans identité (toujours accepté, inchangé). Un jeton personnel est créé et révoqué dans « Mon
compte », affiché une seule fois, gardé haché (SHA-256 d'un secret aléatoire de 256 bits), avec une expiration obligatoire et une portée
« lecture » ou « écriture ».

À chaque appel, tout est relu : compte actif, mot de passe inchangé, génération de la double authentification, expiration, révocation.
L'identité est celle de la personne (``actor()``), donc ses droits et ses dossiers réservés s'appliquent. Un jeton ne sert que sur
``/api/v1`` : ni les pages, ni l'API de l'interface, ni le connecteur.

Un compte non administrateur n'ouvre que les routes listées ici (refus par défaut) : l'API historique a été écrite pour le jeton de
service et plusieurs de ses listes ne sont pas cloisonnées par personne. L'écriture sur l'API reste par ailleurs réservée à
l'administrateur par ``standalone_auth.allowed`` : ces routes sont l'exception explicite, contrôlée ici.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets

from .common import Stop

PREFIX = 'axh5688_'
SCOPES = {'lecture': 'Lecture seule', 'ecriture': 'Lecture et écriture'}
DURATIONS = (30, 90, 180, 365)
MAX_TOKENS = 20
SCHEMA = '''
CREATE TABLE IF NOT EXISTS jetons5688(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, nom TEXT NOT NULL, portee TEXT NOT NULL,
  cree TEXT NOT NULL, expire TEXT NOT NULL, revoque TEXT NOT NULL DEFAULT '', stamp TEXT NOT NULL, gen INTEGER NOT NULL DEFAULT 0,
  dernier_usage TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS jetons5688_owner ON jetons5688(owner);
'''
# POST sans effet : portée « lecture » admise.
READ_POST = frozenset({'/search'})
# Routes ouvertes à un compte qui n'est pas administrateur (les autres listes ne sont pas cloisonnées par personne).
MATTER_READ = re.compile(r'/matters/[A-Za-z0-9_-]{1,80}(/(memory|operational-memory|authorities|exhibits|timeline|strategy|matrix|act-projects|graph))?')
PERSON_READ = frozenset({'/', '/openapi.json', '/capabilities', '/guide/catalog'})
PERSON_WRITE = {'/assistant': ('avocat', 'assistant')}
LAWYER_WRITE = re.compile(r'/matters/[A-Za-z0-9_-]{1,80}/(strategy|matrix|act-projects)')


def _now():
    return datetime.now(timezone.utc)


def _iso(value=None):
    return (value or _now()).isoformat(timespec='seconds')


def _hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def connect(state_dir):
    from . import totp5688
    db = totp5688.connect(state_dir)
    db.executescript(SCHEMA)
    return db


def available(auth):
    """Comptes nominatifs : version serveur ou distribution autonome ; ni le poste (un seul utilisateur) ni l'ExApp (comptes Nextcloud)."""
    from . import connexion5648 as cx
    from .comptes5642 import standalone
    if not isinstance(auth, dict) or auth.get('poste') or auth.get('exapp'):
        return False
    return cx.enabled(auth) or standalone()


def account(db, auth, owner):
    """Compte vivant : {'owner', 'role', 'name', 'stamp', 'must_change'} ; None s'il est désactivé ou inconnu."""
    from . import connexion5648 as cx
    from .comptes5642 import standalone
    owner = str(owner or '')
    if standalone():
        row = db.execute('SELECT * FROM users WHERE email=? AND active=1', (owner,)).fetchone()
        if not row:
            return None
        return {'owner': owner, 'role': row['role'] or 'avocat', 'name': row['name'] or owner, 'must_change': bool(row['must_change']),
                'stamp': hashlib.sha256(('user|%s|%s' % (row['id'], row['password_hash'])).encode()).hexdigest()[:24]}
    if owner == 'cabinet':
        if cx.first_run(auth):
            return None
        return {'owner': 'cabinet', 'role': 'administrateur', 'name': str(auth.get('username') or 'admin'), 'must_change': False, 'stamp': cx.admin_stamp(auth)}
    row = db.execute('SELECT * FROM users WHERE ident=? AND active=1', (owner,)).fetchone()
    if not row:
        return None
    user = cx._user_dict(row)
    return {'owner': owner, 'role': user['role'], 'name': user['name'], 'must_change': user['must_change'], 'stamp': cx._user_stamp(row)}


# ------------------------------------------------------------------------------------------------ cycle de vie
def create(db, auth, owner, nom, portee, jours):
    from . import totp5688
    who = account(db, auth, owner)
    if not who:
        raise Stop('compte_absent')
    nom = re.sub(r'[\x00-\x1f<>]+', '', str(nom or '')).strip()[:80]
    if not nom:
        raise Stop('jeton_nom_requis')
    if portee not in SCOPES:
        raise Stop('jeton_portee_invalide')
    try:
        jours = int(jours)
    except (TypeError, ValueError):
        raise Stop('jeton_duree_invalide') from None
    if jours not in DURATIONS:
        raise Stop('jeton_duree_invalide')
    if db.execute("SELECT COUNT(*) FROM jetons5688 WHERE owner=? AND revoque='' AND expire>?", (who['owner'], _iso())).fetchone()[0] >= MAX_TOKENS:
        raise Stop('jetons_trop_nombreux')
    token, ident = PREFIX + secrets.token_urlsafe(32), secrets.token_hex(8)
    expires = _now() + timedelta(days=jours)
    db.execute('INSERT INTO jetons5688(id,token_hash,owner,nom,portee,cree,expire,stamp,gen) VALUES(?,?,?,?,?,?,?,?,?)',
               (ident, _hash(token), who['owner'], nom, portee, _iso(), _iso(expires), who['stamp'], totp5688.generation(db, who['owner'])))
    db.commit()
    return {'id': ident, 'jeton': token, 'nom': nom, 'portee': portee, 'expire': _iso(expires)}


def listing(db, owner=None):
    sql = 'SELECT id,owner,nom,portee,cree,expire,revoque,dernier_usage,stamp,gen FROM jetons5688'
    rows = db.execute(sql + (' WHERE owner=?' if owner is not None else '') + ' ORDER BY cree DESC LIMIT 200', (str(owner),) if owner is not None else ()).fetchall()
    return [dict(r) for r in rows]


def state_of(row, who, gen):
    """Libellé clair de l'état d'un jeton (mêmes conditions que ``lookup``)."""
    if row['revoque']:
        return 'Révoqué'
    if row['expire'] <= _iso():
        return 'Expiré'
    if not who or not hmac.compare_digest(str(row['stamp']), str(who['stamp'])):
        return 'Hors service : mot de passe changé depuis sa création'
    if int(row['gen']) != int(gen):
        return 'Hors service : créé avant la double authentification'
    return 'Actif'


def revoke(db, ident, owner=None):
    """Révocation par la personne (``owner``) ou par un administrateur (``owner`` None)."""
    sql = "UPDATE jetons5688 SET revoque=? WHERE id=? AND revoque=''"
    params = [_iso(), str(ident)]
    if owner is not None:
        sql += ' AND owner=?'
        params.append(str(owner))
    if db.execute(sql, params).rowcount != 1:
        raise Stop('jeton_absent_ou_deja_revoque')
    db.commit()


def lookup(db, auth, raw):
    """Jeton valable à cet instant, tout relu : {'id', 'owner', 'role', 'name', 'portee'} ou None."""
    from . import totp5688
    if not str(raw).startswith(PREFIX) or len(str(raw)) > 200:
        return None
    row = db.execute("SELECT * FROM jetons5688 WHERE token_hash=? AND revoque='' AND expire>?", (_hash(raw), _iso())).fetchone()
    if not row:
        return None
    who = account(db, auth, row['owner'])
    if not who or who['must_change'] or not hmac.compare_digest(str(row['stamp']), str(who['stamp'])):
        return None
    if int(row['gen']) != totp5688.generation(db, row['owner']) or totp5688.need(db, row['owner']) == 'activation':
        return None
    if (row['dernier_usage'] or '') < _iso(_now() - timedelta(minutes=1)):
        db.execute('UPDATE jetons5688 SET dernier_usage=? WHERE id=?', (_iso(), row['id']))
        db.commit()
    return {'id': row['id'], 'owner': row['owner'], 'role': who['role'], 'name': who['name'], 'portee': row['portee']}


# ------------------------------------------------------------------------------------------------ contrôle d'un appel
def scoped_path(path):
    """Chemin relatif à l'API, ou None hors de ``/api/v1`` (un jeton personnel n'ouvre rien d'autre)."""
    path = str(path or '')
    if path == '/api/v1' or path.startswith('/api/v1/'):
        return path[len('/api/v1'):] or '/'
    return None


def refusal(state_dir, token, method, path):
    """Code de refus de cet appel, ou None."""
    from .membres5650 import allowed
    from .standalone_auth import normalize
    api = scoped_path(normalize(path) or '')
    if api is None:
        return 'jeton_route_refusee'
    api = api.rstrip('/') or '/'
    writes = method not in ('GET', 'HEAD') and api not in READ_POST
    if writes and token['portee'] != 'ecriture':
        return 'jeton_lecture_seule'
    role = token['role']
    if role == 'administrateur':
        return None
    if method in ('GET', 'HEAD'):
        known = api in PERSON_READ or bool(MATTER_READ.fullmatch(api))
        return None if known and allowed(state_dir, token['owner'], role, method, path) else 'jeton_route_refusee'
    if api in READ_POST:
        return None
    if role in PERSON_WRITE.get(api, ()):
        return None
    return None if role == 'avocat' and LAWYER_WRITE.fullmatch(api) else 'jeton_route_refusee'


def identity(env, auth, path, token, state_dir, audit=None):
    """Porte l'identité du jeton dans la requête (les en-têtes reçus sont écartés), puis contrôle l'appel.
    Renvoie une réponse de refus, ou None."""
    for key in ('HTTP_X_AXIORHUB_USER', 'HTTP_X_AXIORHUB_ROLE'):
        env.pop(key, None)
    if token['owner'] != 'cabinet':
        env['HTTP_X_AXIORHUB_USER'] = token['owner']
        env['HTTP_X_AXIORHUB_ROLE'] = token['role']
    auth['owner'] = token['owner']
    method = env.get('REQUEST_METHOD', 'GET')
    code = refusal(state_dir, token, method, path)
    if code is None and method not in ('GET', 'HEAD') and audit is not None:
        audit({'auteur': token['owner'], 'jeton': token['id'], 'route': str(path)[:160], 'methode': method})
    if code is None:
        return None
    if audit is not None:
        audit({'auteur': token['owner'], 'jeton': token['id'], 'route': str(path)[:160], 'methode': method, 'refus': code})
    return {'status': '403 Forbidden', 'kind': 'application/json; charset=utf-8',
            'body': json.dumps({'error': code, 'message': MESSAGES[code]}, ensure_ascii=False)}


MESSAGES = {'jeton_route_refusee': 'Ce jeton ne donne pas accès à cette adresse de l’API.',
            'jeton_lecture_seule': 'Ce jeton est en lecture seule : créez-en un en écriture pour cette action.'}

"""5.6.36 : contrat 1.2 avec l'application Nextcloud — comptes reliés personnellement, instructions au Pilote, recherche dans les dossiers.

Pourquoi : chaque utilisateur Nextcloud n'est pas avocat. Plutôt qu'un réglage unique d'administration qui ouvre AxiorHub à des groupes,
chaque avocat RELIE SON compte Nextcloud à SON compte AxiorHub, dans ses paramètres personnels Nextcloud :
- l'application envoie une fois l'identifiant et le mot de passe AxiorHub (HTTPS) à ``POST /integration/v1/pair`` ;
- le moteur les vérifie (compte du serveur ``ui-auth.json`` ou comptes du cabinet en distribution autonome), limite les essais
  (5 échecs en 15 minutes) et remet une CLÉ PERSONNELLE (identifiant + secret aléatoire) ; le mot de passe n'est conservé nulle part ;
- ensuite chaque requête est signée avec ce secret (en-tête ``X-AxiorHub-Key`` = identifiant de la clé) ; le périmètre est celui du
  compte AxiorHub (tous les dossiers du cabinet pour un avocat ou un administrateur) ;
- la clé se révoque depuis l'application (« Délier ») ou depuis AxiorHub (Paramètres › Connexions › Application Nextcloud).
Le mode historique (secret d'instance partagé + groupes Nextcloud) reste accepté.

Opérations ajoutées : ``instruction`` (une mission du Pilote, comme le bouton robot : réponse, analyse, projet de document ou brouillon
de courriel déposé — jamais d'envoi) et ``GET /integration/v1/search`` (recherche dans les dossiers du cabinet).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import time

from .common import Stop

SETTING = 'integration5636:comptes_personnels'   # interrupteur côté AxiorHub : « Autoriser l'application Nextcloud »
INTENTS = ('auto', 'question', 'analysis', 'document', 'mail')
MAX_FAILURES = 5
WINDOW = 900


def schema(desk):
    _tables(desk)
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(integration_keys5636)')}
    for name, decl in (('auth_mode', "TEXT NOT NULL DEFAULT ''"), ('account_id', "TEXT NOT NULL DEFAULT ''"), ('account_authz', 'INTEGER NOT NULL DEFAULT 0'),
                       ('auth_stamp', "TEXT NOT NULL DEFAULT ''")):
        if name not in cols:   # 5.6.37 (audit F02) : clé liée à une identité de compte et à une version des autorisations
            desk.db.execute('ALTER TABLE integration_keys5636 ADD COLUMN %s %s' % (name, decl))
    desk.db.commit()


def _tables(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS integration_keys5636(id TEXT PRIMARY KEY, axiorhub_user TEXT NOT NULL, role TEXT NOT NULL, secret TEXT NOT NULL,
      label TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, last_used TEXT NOT NULL DEFAULT '', revoked TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS integration_pair_failures5636(scope TEXT NOT NULL, at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS integration_pair_failures5636_scope ON integration_pair_failures5636(scope, at);''')


def enabled(desk):
    """Contrat ouvert : secret d'instance configuré (mode historique) OU comptes personnels autorisés dans AxiorHub."""
    s = desk.c.get('integration5634') or {}
    return bool(s.get('enabled') and s.get('secret_file')) or bool(desk.settings(SETTING, False))


def set_enabled(desk, value, owner='cabinet'):
    desk.setting(SETTING, bool(value))
    desk.audit('integration5636_autorisation', {'enabled': bool(value), 'owner': owner})
    return status(desk)


def status(desk):
    schema(desk)
    keys = [dict(r) for r in desk.db.execute(
        "SELECT id,axiorhub_user,role,label,created,last_used,revoked FROM integration_keys5636 ORDER BY created DESC LIMIT 100")]
    return {'enabled': bool(desk.settings(SETTING, False)), 'instance_mode': bool((desk.c.get('integration5634') or {}).get('secret_file')),
            'keys': keys}


def revoke(desk, key_id, owner='cabinet'):
    schema(desk)
    cur = desk.db.execute("UPDATE integration_keys5636 SET revoked=? WHERE id=? AND revoked=''", (datetime.now(timezone.utc).isoformat(), str(key_id)))
    from . import nextcloud5637
    nextcloud5637.end_for_key(desk, str(key_id))   # 5.6.37 : la session de l'interface dans Nextcloud tombe avec la clé
    desk.db.commit()
    if not cur.rowcount:
        raise Stop('cle_absente_ou_deja_revoquee')
    desk.audit('integration5636_cle_revoquee', {'key': str(key_id)[:12], 'owner': owner})
    return status(desk)


# ------------------------------------------------------------------------------------------------ appairage
def _client(env):
    return (str(env.get('HTTP_X_FORWARDED_FOR', '') or env.get('REMOTE_ADDR', '')).split(',')[0].strip() or '?')[:64]


def _limited(desk, scopes):
    since = time.time() - WINDOW
    desk.db.execute('DELETE FROM integration_pair_failures5636 WHERE at<?', (since,))
    return any(desk.db.execute('SELECT COUNT(*) FROM integration_pair_failures5636 WHERE scope=? AND at>=?', (s, since)).fetchone()[0] >= MAX_FAILURES
               for s in scopes)


def _check_server_account(auth, login, password):
    """Compte de l'interface serveur (ui-auth.json) : même calcul scrypt que la connexion de l'interface."""
    if not auth or not auth.get('hash') or not auth.get('salt'):
        return None
    try:
        computed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(auth['salt']), n=16384, r=8, p=1).hex()
    except (ValueError, TypeError):
        return None
    if hmac.compare_digest(str(auth.get('username', '')), login) and hmac.compare_digest(computed, str(auth['hash'])):
        return {'user': login, 'role': 'administrateur'}
    return None


def pair(desk, env, data):
    """Relie un compte Nextcloud à un compte AxiorHub : identifiants vérifiés une fois, clé personnelle remise."""
    from .integration5634 import Refused
    if not desk.settings(SETTING, False):
        raise Refused('application_nextcloud_non_autorisee', '403 Forbidden')
    schema(desk)
    login = str(data.get('login') or '').strip()[:200]
    password = str(data.get('password') or '')
    label = re.sub(r'[^\w .@+-]', '', str(data.get('label') or ''))[:120]
    if not login or not password or len(password) > 1024:
        raise Refused('identifiants_requis')
    scopes = ('ip:' + _client(env), 'login:' + login.lower())
    if _limited(desk, scopes):
        desk.db.commit()
        raise Refused('trop_de_tentatives', '429 Too Many Requests')
    account = None
    if str(env.get('axiorhub.pair_verified', '')) == '1':   # distribution autonome : vérifié par la couche des comptes du cabinet
        account = {'user': str(env.get('HTTP_X_AXIORHUB_USER') or login), 'role': str(env.get('HTTP_X_AXIORHUB_ROLE') or 'avocat')}
    else:
        account = _check_server_account(env.get('axiorhub.auth'), login, password)
    if account is None:
        now = time.time()
        for s in scopes:
            desk.db.execute('INSERT INTO integration_pair_failures5636 VALUES(?,?)', (s, now))
        desk.db.commit()
        desk.audit('integration5636_appairage_refuse', {'client': scopes[0][3:]})
        raise Refused('identifiants_refuses', '401 Unauthorized')
    if account['role'] not in ('administrateur', 'avocat', 'assistant'):
        raise Refused('role_insuffisant', '403 Forbidden')
    key_id, secret = secrets.token_hex(16), secrets.token_urlsafe(32)
    standalone = str(env.get('axiorhub.pair_verified', '')) == '1'
    desk.db.execute('INSERT INTO integration_keys5636(id,axiorhub_user,role,secret,label,created,auth_mode,account_id,account_authz,auth_stamp) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (key_id, account['user'], account['role'], secret, label, datetime.now(timezone.utc).isoformat(),
                     'cabinet' if standalone else 'serveur', str(env.get('axiorhub.account_id') or ''),
                     int(env.get('axiorhub.account_authz') or 0) if standalone else 0,
                     '' if standalone else _stamp(env.get('axiorhub.auth'))))
    desk.db.commit()
    desk.audit('integration5636_appairage', {'key': key_id[:12], 'user': account['user'], 'label': label})
    from .integration5634 import CONTRACT
    return {'key_id': key_id, 'secret': secret, 'user': account['user'], 'role': account['role'], 'contract': CONTRACT}


# 5.6.37 (audit F03) : capacités du contrat par rôle ACTUEL du compte relié ; la clé d'instance historique (secret partagé) agit comme
# administrateur du cabinet (configuration de l'administrateur Nextcloud, documentée).
CAPABILITIES = {'lire': ('administrateur', 'avocat', 'assistant'), 'preparer': ('administrateur', 'avocat', 'assistant'),
                'annuler': ('administrateur', 'avocat', 'assistant'), 'decider': ('administrateur', 'avocat'),
                'session': ('administrateur', 'avocat'), 'delier': ('administrateur', 'avocat', 'assistant')}


def require(ctx, capability):
    from .integration5634 import Refused
    if ctx.get('_role') not in CAPABILITIES[capability]:
        raise Refused('role_insuffisant', '403 Forbidden')


def _stamp(auth):
    """Empreinte du compte de l'interface serveur (identifiant et mot de passe) : un changement d'accès invalide les clés."""
    if not auth or not auth.get('hash'):
        return ''
    return hashlib.sha256(('%s|%s|%s' % (auth.get('username', ''), auth.get('salt', ''), auth.get('hash', ''))).encode()).hexdigest()[:24]


def current_account(desk, key, auth=None):
    """5.6.37 (audit F02) : compte relié vérifié MAINTENANT par un résolveur de confiance (jamais un en-tête du client) : comptes du cabinet
    (distribution autonome : actif, version des autorisations, rôle actuel) ou compte de l'interface serveur (même identifiant, même mot
    de passe qu'à l'appairage). Renvoie {'user', 'role'} ou None si l'état ne peut pas être établi."""
    import sqlite3
    from .missions567 import auth_root
    db_path = auth_root() / 'users.sqlite3'
    if db_path.is_file():
        try:
            db = sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                cols = {r[1] for r in db.execute('PRAGMA table_info(users)')}
                fields = 'id,email,active,role' + (',authz' if 'authz' in cols else '')
                row = (db.execute('SELECT %s FROM users WHERE id=?' % fields, (int(key['account_id']),)).fetchone() if str(key.get('account_id') or '').isdigit()
                       else db.execute('SELECT %s FROM users WHERE email=?' % fields, (key['axiorhub_user'],)).fetchone())
            finally:
                db.close()
        except (sqlite3.Error, ValueError):
            return None
        if row is None or not row['active'] or row['role'] not in ('administrateur', 'avocat', 'assistant'):
            return None
        authz = row['authz'] if 'authz' in row.keys() else 1
        if key.get('account_id') and int(key.get('account_authz') or 0) != int(authz):
            return None   # désactivé, rôle changé ou accès réinitialisé depuis l'appairage : la clé doit être reliée de nouveau
        if not key.get('account_id'):   # clé 5.6.36 : rattachée une fois à l'identité et à la version ACTUELLES du compte encore actif
            desk.db.execute('UPDATE integration_keys5636 SET auth_mode=?, account_id=?, account_authz=? WHERE id=?', ('cabinet', str(row['id']), int(authz), key['id']))
            desk.db.commit()
        return {'user': row['email'], 'role': row['role']}
    if not auth or not auth.get('username'):
        return None
    if not hmac.compare_digest(str(auth['username']), str(key['axiorhub_user'])):
        return None
    stamp = _stamp(auth)
    if key.get('auth_stamp') and not hmac.compare_digest(stamp, key['auth_stamp']):
        return None   # compte de l'interface changé (mot de passe réinitialisé) depuis l'appairage
    if not key.get('auth_stamp'):
        desk.db.execute("UPDATE integration_keys5636 SET auth_mode='serveur', auth_stamp=? WHERE id=?", (stamp, key['id']))
        desk.db.commit()
    return {'user': key['axiorhub_user'], 'role': 'administrateur'}


def key_secret(desk, key_id):
    """Secret et compte d'une clé personnelle active (vérification d'un contexte signé)."""
    schema(desk)
    if not isinstance(key_id, str) or not re.fullmatch(r'[a-f0-9]{32}', key_id):
        return None
    row = desk.db.execute("SELECT * FROM integration_keys5636 WHERE id=? AND revoked=''", (key_id,)).fetchone()
    return dict(row) if row else None


def touch(desk, key_id):
    try:
        desk.soft_write('UPDATE integration_keys5636 SET last_used=? WHERE id=?', (datetime.now(timezone.utc).isoformat(timespec='minutes'), key_id))
    except Exception:   # noqa: BLE001 — mesure facultative
        pass


def unpair(desk, ctx):
    if not ctx.get('_key'):
        from .integration5634 import Refused
        raise Refused('cle_personnelle_requise', '400 Bad Request')
    desk.db.execute("UPDATE integration_keys5636 SET revoked=? WHERE id=? AND revoked=''", (datetime.now(timezone.utc).isoformat(), ctx['_key']))
    from . import nextcloud5637
    nextcloud5637.end_for_key(desk, ctx['_key'])
    desk.db.commit()
    desk.audit('integration5636_delie', {'key': ctx['_key'][:12]})
    return {'unpaired': True}


def me(desk, ctx):
    from . import __version__
    from .integration5634 import CONTRACT, OPERATIONS
    return {'user': ctx.get('_owner', ''), 'role': ctx.get('_role', ''), 'mode': 'personnel' if ctx.get('_key') else 'instance',
            'version': __version__, 'contract': CONTRACT, 'operations': sorted(OPERATIONS)}


# ------------------------------------------------------------------------------------------------ instruction (mission du Pilote)
def validate_instruction(data):
    from .integration5634 import Refused
    text = data.get('question')
    text = text.strip() if isinstance(text, str) else ''
    if not 3 <= len(text) <= 12000:
        raise Refused('instruction_invalide')
    intent = data.get('intent') or 'auto'
    if intent not in INTENTS:
        raise Refused('resultat_attendu_invalide')
    return text, intent


def start_instruction(desk, ctx, args):
    """Crée la mission (même chemin que le bouton robot) ; renvoie (travail du moteur ou None, identifiant de mission)."""
    from . import missions567
    key = 'nc-' + hashlib.sha256((args['question'] + args.get('matter', '') + ctx.get('user', '') + args.get('idem', '')).encode()).hexdigest()[:40]
    m = missions567.create(desk, {'instruction': args['question'], 'matter': args.get('matter') or '', 'intent': args.get('intent') or 'auto',
                                  'request_key': key, 'autonomy': 'prepare'}, missions567.owner_for(ctx.get('_owner')))
    return m.get('job_id'), m.get('id')


def instruction_status(desk, ctx, row):
    from . import missions567
    try:
        m = missions567.get(desk, row['mission'], missions567.owner_for(ctx.get('_owner')), ctx.get('_role') == 'administrateur')
    except Stop:
        return {'job_id': row['id'], 'state': 'failed', 'stage': '', 'result_id': '', 'errors': [{'code': 'mission_absente', 'message': 'Mission introuvable.'}]}
    state = m.get('state', '')
    mapping = {'queued': 'pending', 'creating': 'pending', 'running': 'running', 'verified': 'succeeded', 'prepared': 'succeeded',
               'abstained': 'succeeded', 'error': 'failed', 'cancelled': 'cancelled', 'paused': 'cancelled', 'cancel_requested': 'cancel_requested',
               'suggested': 'pending', 'decision': 'failed'}
    out = {'job_id': row['id'], 'state': mapping.get(state, 'running'), 'stage': (m.get('job') or {}).get('progress') or m.get('label', ''),
           'result_id': row['id'] if mapping.get(state) == 'succeeded' else '', 'errors': []}
    if state == 'decision':
        choices = [c.get('label', '') for x in m.get('exceptions', []) for c in x.get('candidates', [])][:8]
        out['errors'] = [{'code': 'dossier_a_preciser', 'message': 'Précisez le dossier : ' + ', '.join(choices) if choices else 'Précisez le dossier.'}]
    elif state == 'error':
        out['errors'] = [{'code': 'mission_en_echec', 'message': (m.get('job') or {}).get('error') or 'La mission n’a pas abouti.'}]
    return out


def instruction_payload(desk, ctx, row):
    from . import missions567
    m = missions567.get(desk, row['mission'], missions567.owner_for(ctx.get('_owner')), ctx.get('_role') == 'administrateur')
    r = m.get('result') or {}
    return {'instruction': m.get('instruction', ''), 'reponse': r.get('text') or r.get('message', ''), 'sources': r.get('sources') or [],
            'livrable': r.get('path') or '', 'etat': m.get('label', ''), 'mission': m.get('id', '')}


# ------------------------------------------------------------------------------------------------ recherche dans les dossiers
def search(desk, ctx, args):
    from .integration5634 import Refused, _in_scope
    from . import search490
    q = str(args.get('q') or '').strip()
    if not 2 <= len(q) <= 300:
        raise Refused('recherche_invalide')
    matter = str(args.get('matter') or '')
    if matter:
        _in_scope(ctx, matter)
    elif ctx['scope'] != ['*']:
        raise Refused('dossier_requis_pour_rechercher', '403 Forbidden')
    found = search490.search(desk, q, matter, '', '', None, 30)
    items = []
    for r in found.get('results', [])[:30]:
        mid = str(r.get('matter') or '')
        if ctx['scope'] != ['*'] and mid not in ctx['scope']:
            continue
        items.append({'kind': r.get('kind', ''), 'title': str(r.get('title') or r.get('subject') or r.get('path') or '')[:240],
                      'matter': mid, 'date': str(r.get('date') or '')[:10],
                      'excerpt': ''.join(str(s.get('t', '')) for s in (r.get('segments') or []) if isinstance(s, dict))[:400]})
    return {'query': q, 'results': items, 'partial': bool(found.get('partial'))}


# ------------------------------------------------------------------------------------------------ AxiorHub : Paramètres › Connexions
def settings_html(desk, prefix, auth=None):
    from html import escape as e
    s = status(desk)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(k['label'] or '—'), e(k['axiorhub_user']), e(k['created'][:16].replace('T', ' ')), e((k['last_used'] or '—')[:16].replace('T', ' ')),
        '<span class="ax-muted">révoquée</span>' if k['revoked'] else
        ('<form class="m5-form m5-inline m5-mini" data-api="m5636/nextcloud/cle/revoquer" data-reload="1" data-confirm="Révoquer cette clé ? Le compte '
         'Nextcloud devra être relié de nouveau."><input type="hidden" name="key" value="%s"><button class="ax-btn ghost" type="submit">Révoquer</button></form>') % e(k['id'], quote=True))
        for k in s['keys'])
    from .integration5634 import workstation
    if workstation({'axiorhub.auth': auth} if auth else None):   # 5.6.41 : version poste : mode découverte
        return discovery_html(desk, s, rows, auth)
    return ('<section class="ax-card" id="app-nextcloud"><h2>Application Nextcloud</h2><p class="ax-muted">Chaque avocat relie son compte Nextcloud '
            'à son compte AxiorHub dans ses paramètres personnels Nextcloud (« AxiorHub Pilote »). Les utilisateurs Nextcloud non reliés n’ont '
            'aucun accès. Le mot de passe n’est jamais conservé : une clé personnelle, révocable ici, le remplace.</p>'
            '<form class="m5-form m5-inline" data-api="m5636/nextcloud/app" data-reload="1"><label class="e5636-switch"><input type="checkbox" name="enabled" '
            'value="1"%s> Autoriser la liaison des comptes depuis Nextcloud</label><button class="ax-btn ghost" type="submit">Enregistrer</button></form>%s</section>') % (
        ' checked' if s['enabled'] else '',
        ('<table class="vf-table"><thead><tr><th>Compte Nextcloud</th><th>Compte AxiorHub</th><th>Relié le</th><th>Dernière utilisation</th><th></th></tr></thead>'
         '<tbody>%s</tbody></table>' % rows) if rows else '<p class="ax-muted">Aucun compte relié.</p>')


def discovery_html(desk, s, rows, auth=None):
    """5.6.41 : réglages de l'application Nextcloud sur un poste (Windows 11, Ubuntu) — mode découverte, avertissement, adresses à saisir,
    identifiant et mot de passe locaux (l'utilisateur du poste est déjà connecté ; le fichier est dans son profil)."""
    from html import escape as e
    from pathlib import Path
    from urllib.parse import urlsplit
    from .integration5634 import DISCLAIMER
    origin = str((auth or {}).get('origin') or 'http://127.0.0.1:8769').rstrip('/')
    prefix = str((auth or {}).get('prefix') or '/agent-courriel')
    port = urlsplit(origin).port or 8769
    secret = ''
    try:
        secret = (Path(desk.c['state_dir']).parent / 'mot-de-passe-local').read_text(encoding='utf-8').strip()
    except (OSError, KeyError, TypeError):
        pass
    creds = ('<details class="p5641-creds"><summary>Afficher l’identifiant et le mot de passe locaux</summary><p>Identifiant : <code>%s</code> · Mot de passe '
             ': <code>%s</code></p></details>') % (e(str((auth or {}).get('username') or 'admin')), e(secret or 'indisponible')) if (auth or {}).get('poste') else ''
    return ('<section class="ax-card" id="app-nextcloud"><h2>Application Nextcloud — mode découverte</h2><p class="c5636-warn" role="note">%s</p>'
            '<h3>Nextcloud installé sur ce même ordinateur</h3><ol><li>Cochez ci-dessous « Autoriser la liaison des comptes depuis Nextcloud ».</li>'
            '<li>Dans Nextcloud, Paramètres personnels › AxiorHub Pilote : adresse <code>%s%s</code> (Nextcloud dans Docker Desktop sous Windows : '
            '<code>http://host.docker.internal:%d%s</code>), cochez « Moteur sur ce serveur (adresse locale) », puis l’identifiant et le mot de '
            'passe locaux ci-dessous.</li></ol>'
            '<h3>Nextcloud hébergé (sur un serveur)</h3><p>Le serveur Nextcloud ne peut pas joindre ce poste. Dans Nextcloud, Paramètres personnels '
            '› AxiorHub Pilote › « Adresse de mon AxiorHub local » : <code>%s%s/</code> ; l’entrée « Mon AxiorHub local » ouvre alors AxiorHub '
            'dans un onglet de ce navigateur (ni Pilote, ni recherche, ni affichage dans Nextcloud).</p>%s'
            '<form class="m5-form m5-inline" data-api="m5636/nextcloud/app" data-reload="1"><label class="e5636-switch"><input type="checkbox" '
            'name="enabled" value="1"%s> Autoriser la liaison des comptes depuis Nextcloud (même ordinateur)</label><button class="ax-btn ghost" '
            'type="submit">Enregistrer</button></form>%s</section>') % (
        e(DISCLAIMER), e(origin), e(prefix), port, e(prefix), e(origin), e(prefix), creds, ' checked' if s['enabled'] else '',
        ('<table class="vf-table"><thead><tr><th>Compte Nextcloud</th><th>Compte AxiorHub</th><th>Relié le</th><th>Dernière utilisation</th><th></th>'
         '</tr></thead><tbody>%s</tbody></table>' % rows) if rows else '<p class="ax-muted">Aucun compte relié.</p>')

"""5.6.50 : membres du cabinet, droits réglables compte par compte et dossiers réservés.

- **Annuaire** : les comptes de la version serveur (page de connexion 5.6.48), de la distribution autonome et les membres déjà vus
  (application Nextcloud, ExApp) ; nom et rôle affichés dans la messagerie interne.
- **Droits par compte** : sur la base du rôle (administrateur, avocat, assistant(e)), l'administrateur accorde ou retire six droits
  à un compte avocat ou assistant(e) : envoyer des courriels, déposer des brouillons, valider les décisions et productions, gérer
  l'agenda, facturation et temps, réglages du cabinet. Un administrateur garde tous ses droits ; la gestion des comptes et des droits
  reste réservée aux administrateurs, quel que soit le réglage.
- **Dossiers réservés** (facultatif) : un dossier peut être réservé à certains comptes ; les autres comptes (hors administrateurs) ne
  le voient plus dans les listes, les recherches, l'assistant et la messagerie, et toute requête qui le désigne est refusée.
  Les travaux de l'agent (en arrière-plan, pour le cabinet) ne sont pas cloisonnés.
- Stockage : ``acces5650.json`` (dossier d'état, privé, relu à chaque changement), lisible par les trois passerelles (serveur,
  distribution autonome, ExApp) ; journal des changements dans ``audit``.
"""
from contextvars import ContextVar
from html import escape as e
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs

from .common import Stop, private_json

FILE = 'acces5650.json'
RIGHTS = (('envoyer', 'Envoyer des courriels'), ('deposer', 'Déposer des brouillons dans la messagerie'),
          ('valider', 'Valider les décisions et les productions'), ('agenda', 'Gérer l’agenda et les tâches'),
          ('facturation', 'Facturation et temps passé'), ('reglages', 'Réglages du cabinet'))
RIGHT_IDS = tuple(r for r, _ in RIGHTS)
ROLES = {'administrateur': 'Administrateur', 'avocat': 'Avocat', 'assistant': 'Assistant(e)'}
# Droits de chaque rôle avant tout réglage (mêmes règles que standalone_auth.allowed).
ROLE_DEFAULTS = {'administrateur': {r: True for r in RIGHT_IDS},
                 'avocat': {'envoyer': True, 'deposer': True, 'valider': True, 'agenda': True, 'facturation': True, 'reglages': False},
                 'assistant': {'envoyer': False, 'deposer': False, 'valider': False, 'agenda': True, 'facturation': False, 'reglages': False}}
SEND_API = frozenset({'send/prepare', 'send/confirm', 'send/matter', 'm5628/envoyer', 'm5628/envoyer/confirmer'})
DEPOSIT_API = frozenset({'m5628/brouillon', 'm5625/boite/repondre'})
DEPOSIT_ACTIONS = frozenset({'deposit_draft', 'prepare_reply', 'prepare_draft'})
VALIDATE_API = frozenset({'fact/validate', 'fact/reject', 'deadline/confirm', 'deadline/correct', 'deadline/close', 'm500/limitation/confirm',
                          'm500/limitation/correct', 'm500/limitation/close', 'm500/conflicts/decide', 'm5634/decision/annuler',
                          'm568/profils/approve', 'm568/profils/revoke', 'm568/mission5614/control', 'm568/mission5614/decide',
                          'm568/decision/defer', 'm568/automatismes/pause'})
VALIDATE_ACTIONS = frozenset({'approve_cabinet_confirmation_batch', 'approve_cabinet_template', 'approve_project', 'approve_work_plan',
                              'validate_act', 'validate_fact', 'validate_matrix_row', 'validate_memory', 'validate_strategy',
                              'reject_document_project', 'reject_group', 'reject_hearing', 'reject_word_project', 'reject_work_plan',
                              'confirm_matter', 'review_cabinet_decision', 'resolve_conflict', 'trust_document410'})
AGENDA_PREFIXES = ('m5625/agenda/', 'm5634/agenda/', 'm530/event/', 'm568/calendars/')
AGENDA_ACTIONS = frozenset({'create_agenda_event', 'edit_agenda_event', 'cancel_agenda_event', 'edit_personal_event', 'sync_caldav_tasks',
                            'confirm_event'})
BILLING_PREFIXES = ('m500/invoice/', 'm500/time/', 'm568/ninja/')
BILLING_ACTIONS = frozenset({'link_invoice_client', 'billing_review35', 'record_provision'})
# Gestion des comptes et des droits : jamais accordée par un réglage (pas d'élévation de privilège).
ACCOUNT_ROUTES = ('m5636/comptes/', 'm5636/compte/', 'm5650/droits', 'm5650/reserve')
MATTER_KEYS = ('matter', 'matter_id', 'dossier', 'mid')
MATTER_ID_PATHS = ('/dossier', '/fiche', '/chronologie', '/pieces')

_viewer = ContextVar('axiorhub_viewer5650', default=None)
_lock = threading.Lock()
_cache = {}
_seen = {}


# ------------------------------------------------------------------------------------------------ stockage
def _path(state_dir):
    return Path(str(state_dir)) / FILE


def load(state_dir):
    """{'droits': {compte: {droit: bool}}, 'reserves': {dossier: [comptes]}} ; relu seulement si le fichier a changé."""
    path = _path(state_dir)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {'droits': {}, 'reserves': {}}
    with _lock:
        hit = _cache.get(str(path))
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    data = {'droits': {str(k): {r: bool(v) for r, v in (rights or {}).items() if r in RIGHT_IDS}
                       for k, rights in (data.get('droits') or {}).items() if isinstance(rights, dict)},
            'reserves': {str(k): sorted({str(o) for o in (owners or [])}) for k, owners in (data.get('reserves') or {}).items()
                         if isinstance(owners, list) and owners}}
    with _lock:
        _cache[str(path)] = (stamp, data)
    return data


def _save(state_dir, data):
    private_json(_path(state_dir), data)
    with _lock:
        _cache.pop(str(_path(state_dir)), None)


def effective_rights(state_dir, owner, role):
    base = dict(ROLE_DEFAULTS.get(role, ROLE_DEFAULTS['assistant']))
    if role == 'administrateur':
        return base
    base.update(load(state_dir)['droits'].get(str(owner), {}))
    return base


def set_rights(state_dir, actor, owner, role, values):
    if role not in ('avocat', 'assistant'):
        raise Stop('droits_administrateur_non_modifiables')
    if not owner or owner == actor:
        raise Stop('droits_compte_invalide')
    data = load(state_dir)
    defaults = ROLE_DEFAULTS[role]
    chosen = {r: bool(values.get(r)) for r in RIGHT_IDS if r in values}
    overrides = {r: v for r, v in chosen.items() if v != defaults[r]}
    droits = dict(data['droits'])
    if overrides:
        droits[owner] = overrides
    else:
        droits.pop(owner, None)
    _save(state_dir, {**data, 'droits': droits})
    return effective_rights(state_dir, owner, role)


def set_reserve(state_dir, matter, owners):
    data = load(state_dir)
    reserves = dict(data['reserves'])
    owners = sorted({str(o).strip() for o in owners or [] if str(o).strip()})[:200]
    if owners:
        reserves[str(matter)] = owners
    else:
        reserves.pop(str(matter), None)
    _save(state_dir, {**data, 'reserves': reserves})
    return owners


# ------------------------------------------------------------------------------------------------ droits
def category(method, path, action=''):
    """Droit concerné par une requête (None : aucun droit réglable n'est en jeu)."""
    from .standalone_auth import ADMIN_ACTIONS, ADMIN_API, ADMIN_PATHS, normalize
    path = normalize(path) or ''
    path = path.rstrip('/') or '/'
    api = path[len('/api440/'):] if path.startswith('/api440/') else ''
    if api.startswith(ACCOUNT_ROUTES):
        return 'comptes'
    if (path not in ('/parametres/assistant', '/parametres/proactivite', '/parametres/agents', '/parametres/agendas') and
            (any(path == p or path.startswith(p + '/') for p in ADMIN_PATHS) or path in ('/installation', '/comptes'))):
        return 'reglages'
    if api.startswith(ADMIN_API) or action in ADMIN_ACTIONS:
        return 'reglages'
    if method in ('GET', 'HEAD'):
        return None
    if api in SEND_API:
        return 'envoyer'
    if api in DEPOSIT_API or action in DEPOSIT_ACTIONS:
        return 'deposer'
    if api in VALIDATE_API or action in VALIDATE_ACTIONS:
        return 'valider'
    if api.startswith(AGENDA_PREFIXES) or action in AGENDA_ACTIONS:
        return 'agenda'
    if (api.startswith(BILLING_PREFIXES) and api != 'm500/time/bareme') or action in BILLING_ACTIONS:
        return 'facturation'
    return None


def allowed(state_dir, owner, role, method, path, action=''):
    """Règle du rôle (standalone_auth.allowed), puis droits accordés ou retirés à ce compte par l'administrateur."""
    from .standalone_auth import allowed as role_allowed
    base = role_allowed(role, method, path, action)
    if role == 'administrateur' or not owner or not state_dir:
        return base
    cat = category(method, path, action)
    if cat in (None, 'comptes'):
        return base
    custom = load(state_dir)['droits'].get(str(owner), {})
    if cat not in custom:
        return base
    if not custom[cat]:
        return False
    if base:
        return True
    # Droit accordé au-delà du rôle : la route doit être une route de travail connue de ce droit (jamais une route inconnue).
    from .standalone_auth import allowed as _a
    return _a('avocat', method, path, action) or (cat == 'reglages' and _a('administrateur', method, path, action))


# ------------------------------------------------------------------------------------------------ dossiers réservés
def hidden_for(state_dir, owner, role):
    if role == 'administrateur' or not state_dir:
        return frozenset()
    reserves = load(state_dir)['reserves']
    return frozenset(m for m, owners in reserves.items() if str(owner) not in owners)


def viewer_hidden():
    """Dossiers masqués pour l'utilisateur de la requête en cours (ensemble vide hors requête : travaux de l'agent)."""
    value = _viewer.get()
    return value if value else frozenset()


def set_viewer(hidden):
    _viewer.set(frozenset(hidden) if hidden else None)


def clear():
    _viewer.set(None)


def _request_values(env, path, args):
    values = [str(args.get(k) or '') for k in MATTER_KEYS]
    if path.rstrip('/') in MATTER_ID_PATHS:
        values.append(str(args.get('id') or ''))
    method = env.get('REQUEST_METHOD', 'GET')
    if method not in ('POST', 'PUT', 'PATCH'):
        return values
    ctype = str(env.get('CONTENT_TYPE') or '').split(';')[0].strip()
    if ctype not in ('application/json', 'application/x-www-form-urlencoded'):
        return values
    try:
        length = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if not 0 < length <= 2_000_000:
        return values
    from io import BytesIO
    raw = env['wsgi.input'].read(length)
    env['wsgi.input'] = BytesIO(raw)
    try:
        if ctype == 'application/json':
            data = json.loads(raw.decode('utf-8') or '{}')
            if isinstance(data, dict):
                values += [str(data.get(k) or '') for k in MATTER_KEYS]
        else:
            form = parse_qs(raw.decode('utf-8', 'replace'), max_num_fields=400)
            values += [str((form.get(k) or [''])[0]) for k in MATTER_KEYS]
    except (ValueError, UnicodeError):
        pass
    return values


def gate(env, state_dir, path, args):
    """Contrôle central (toutes versions) : présence du membre, dossiers masqués de la requête, refus d'une requête qui désigne un
    dossier réservé. Renvoie None (requête autorisée) ou le code d'erreur."""
    from .web567 import actor
    owner, role = actor(env)
    hidden = hidden_for(state_dir, owner, role)
    set_viewer(hidden)
    _presence(state_dir, owner, role, env)
    if hidden and any(v in hidden for v in _request_values(env, path, args) if v):
        return 'dossier_reserve'
    return None


def _presence(state_dir, owner, role, env):
    """Annuaire des membres vus (au plus une écriture toutes les 5 minutes par membre)."""
    key = (str(state_dir), owner, role)
    now = time.time()
    if _seen.get(key, 0) > now - 300:
        return
    _seen[key] = now
    user = env.get('axiorhub.user') or {}
    name = str(user.get('name') or env.get('HTTP_X_AXIORHUB_NAME') or '')[:120]
    try:
        import sqlite3
        db = sqlite3.connect(str(Path(state_dir) / 'desk.sqlite3'), timeout=0.3)
        try:
            db.execute(SCHEMA)
            db.execute('INSERT INTO membres5650(owner,name,role,first_seen,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(owner) DO UPDATE SET '
                       "role=excluded.role,last_seen=excluded.last_seen,name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE membres5650.name END",
                       (owner, name, role, _iso(), _iso()))
            db.commit()
        finally:
            db.close()
    except Exception:   # noqa: BLE001 — la présence est facultative (base occupée : écriture sautée)
        _seen.pop(key, None)


def _iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


SCHEMA = ('CREATE TABLE IF NOT EXISTS membres5650(owner TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT \'\', role TEXT NOT NULL DEFAULT \'avocat\', '
          'first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)')


# ------------------------------------------------------------------------------------------------ annuaire
def directory(desk, auth=None):
    """Membres du cabinet : {owner: {'owner', 'name', 'role', 'source'}} — comptes de la version serveur, de la distribution autonome,
    puis membres vus (Nextcloud, ExApp)."""
    out = {}
    desk.db.execute(SCHEMA)
    for r in desk.db.execute('SELECT owner,name,role FROM membres5650'):
        out[r['owner']] = {'owner': r['owner'], 'name': r['name'] or r['owner'], 'role': r['role'], 'source': 'vu'}
    auth = auth or {}
    try:
        from . import connexion5648
        if connexion5648.enabled(auth):
            out['cabinet'] = {'owner': 'cabinet', 'name': str(auth.get('username') or 'admin'), 'role': 'administrateur', 'source': 'compte'}
            db = connexion5648.connect(desk.c['state_dir'])
            try:
                for u in connexion5648.list_users(db):
                    if u['active']:
                        out[u['ident']] = {'owner': u['ident'], 'name': u['name'] or u['ident'], 'role': u['role'], 'source': 'compte'}
                    else:
                        out.pop(u['ident'], None)
            finally:
                db.close()
    except Exception:   # noqa: BLE001
        pass
    try:
        from .comptes5642 import standalone
        if standalone():
            import sqlite3
            from .missions567 import auth_root
            db = sqlite3.connect(str(auth_root() / 'users.sqlite3'), timeout=2)
            db.row_factory = sqlite3.Row
            try:
                for u in db.execute('SELECT email,name,role,active FROM users'):
                    if u['active']:
                        out[u['email']] = {'owner': u['email'], 'name': u['name'] or u['email'], 'role': u['role'], 'source': 'compte'}
                    else:
                        out.pop(u['email'], None)
            finally:
                db.close()
    except Exception:   # noqa: BLE001
        pass
    return out


def label(member):
    return '%s (%s)' % (member.get('name') or member.get('owner'), ROLES.get(member.get('role'), member.get('role') or ''))


# ------------------------------------------------------------------------------------------------ page Paramètres › Droits et accès
def page_html(desk, auth, prefix, args):
    state = desk.c['state_dir']
    known = directory(desk, auth)
    members = sorted(known.values(), key=lambda m: (m['role'] != 'administrateur', (m['name'] or '').lower()))
    cards = []
    for m in members:
        head = '<h3>%s <small>%s · %s</small></h3>' % (e(m['name'] or m['owner']), e(ROLES.get(m['role'], m['role'])), e(m['owner']))
        if m['role'] == 'administrateur':
            cards.append('<article class="m50-member">%s<p class="ax-muted">Tous les droits (administrateur).</p></article>' % head)
            continue
        rights = effective_rights(state, m['owner'], m['role'])
        defaults = ROLE_DEFAULTS.get(m['role'], ROLE_DEFAULTS['assistant'])
        checks = ''.join('<label class="m50-check"><input type="checkbox" name="%s"%s> %s%s</label>' % (
            r, ' checked' if rights[r] else '', e(lbl), ' <small>(réglage propre à ce compte)</small>' if rights[r] != defaults[r] else '')
            for r, lbl in RIGHTS)
        cards.append('<article class="m50-member">%s<form class="m5-form" data-api="m5650/droits" data-reload>'
                     '<input type="hidden" name="owner" value="%s"><input type="hidden" name="role" value="%s"><div class="m50-checks">%s</div>'
                     '<button class="ax-btn ghost" type="submit">Enregistrer les droits</button><p role="status" class="m5-status"></p></form></article>' % (
                         head, e(m['owner'], quote=True), e(m['role'], quote=True), checks))
    reserves = load(state)['reserves']
    from .common import load_matters, matter_display
    names = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    res_rows = ''.join('<li><strong>%s</strong> — réservé à : %s · <a href="%s">Modifier</a></li>' % (
        e(names.get(mid, mid)), e(', '.join(label(known.get(o, {'owner': o})) for o in owners)),
        e(prefix + '/dossier?id=' + mid + '#acces-dossier', quote=True)) for mid, owners in sorted(reserves.items()))
    return ('<h1>Droits et accès</h1>'
            '<p class="ax-muted">Chaque compte reçoit les droits de son rôle ; vous pouvez en accorder ou en retirer compte par compte. Un administrateur '
            'garde tous ses droits ; la gestion des comptes et des droits lui reste réservée.</p>'
            '<section class="ax-card"><h2>Droits par compte</h2><div class="m50-members">%s</div>'
            '<p class="ax-muted">Un membre apparaît ici dès la création de son compte (page de connexion, distribution autonome) ou à sa première '
            'connexion (application Nextcloud).</p></section>'
            '<section class="ax-card" id="dossiers-reserves"><h2>Dossiers réservés</h2>'
            '<p class="ax-muted">Un dossier réservé n’est visible que des comptes choisis et des administrateurs : listes, recherches, assistant, '
            'messagerie. La réservation se règle dans la page du dossier (rubrique « Accès au dossier »).</p>%s</section>') % (
        ''.join(cards) or '<p>Aucun membre connu pour l’instant.</p>',
        ('<ul>%s</ul>' % res_rows) if res_rows else '<p>Aucun dossier réservé : tous les comptes voient tous les dossiers.</p>')


def access_card(desk, auth, prefix, mid, role):
    """Page du dossier : rubrique « Accès au dossier » (administrateur)."""
    if role != 'administrateur':
        return ''
    owners = load(desk.c['state_dir'])['reserves'].get(mid, [])
    members = [m for m in directory(desk, auth).values() if m['role'] != 'administrateur']
    checks = ''.join('<label class="m50-check"><input type="checkbox" name="%s"%s> %s</label>' % (
        e('compte:' + m['owner'], quote=True), ' checked' if m['owner'] in owners else '', e(label(m)))
        for m in sorted(members, key=lambda m: (m['name'] or '').lower()))
    return ('<section class="ax-card" id="acces-dossier"><h2>Accès au dossier</h2><p class="ax-muted">%s</p>'
            '<form class="m5-form" data-api="m5650/reserve" data-reload><input type="hidden" name="matter" value="%s"><div class="m50-checks">%s</div>'
            '<button class="ax-btn ghost" type="submit">Enregistrer l’accès</button><p role="status" class="m5-status"></p></form></section>') % (
        e('Réservé aux comptes cochés et aux administrateurs.' if owners else 'Tous les comptes du cabinet voient ce dossier. Cochez des comptes pour le leur réserver.'),
        e(mid, quote=True), checks or '<p>Aucun compte avocat ou assistant(e) connu.</p>')


def _slug(value):
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(value))[:60]

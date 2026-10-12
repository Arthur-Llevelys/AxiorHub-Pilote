"""5.6.36 : agenda Microsoft 365 (Outlook) — connexion OAuth de l'avocat, copie des événements AxiorHub, lecture des rendez-vous.

- Connexion : application Microsoft Entra ID du cabinet (identifiant d'application, annuaire, secret facultatif), autorisation par l'avocat
  sur la page de Microsoft (PKCE). Autorisations demandées : Calendars.ReadWrite, User.Read, offline_access. Le jeton de
  renouvellement est chiffré dans le coffre local ; aucun mot de passe Microsoft n'est vu par AxiorHub.
- Copie : les événements créés par AxiorHub (audiences, échéances, rendez-vous) sont copiés dans le calendrier Outlook (catégorie
  « AxiorHub ») ; jamais de suppression ni de modification d'un événement Outlook créé ailleurs.
- Lecture : les rendez-vous Outlook des 7 derniers et 90 prochains jours apparaissent dans l'agenda AxiorHub (lecture seule,
  mis à jour toutes les 15 minutes) ; les événements copiés par AxiorHub ne sont pas importés une seconde fois.
"""
import base64
from datetime import datetime, timedelta, timezone
from html import escape as e
import hashlib
import json
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode, quote

from .common import HTTP, Stop, read_secret

CONFIG = 'microsoft5636:config'
PENDING = 'microsoft5636:pending'
LOGIN = 'https://login.microsoftonline.com'
GRAPH = 'https://graph.microsoft.com'
SCOPES = 'offline_access Calendars.ReadWrite User.Read'
SOURCE = 'microsoft'


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS ms_links5636(local_id TEXT PRIMARY KEY, graph_id TEXT NOT NULL, ical_uid TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);''')


def _vault(desk, name):
    return Path(desk.c['state_dir']) / 'vault' / 'microsoft5636' / name


def config(desk):
    cfg = dict(desk.settings(CONFIG, {}) or {})
    cfg.setdefault('tenant', 'common')
    cfg.setdefault('push', True)
    cfg.setdefault('pull', True)
    return cfg


def connected(desk):
    cfg = config(desk)
    return bool(cfg.get('client_id') and cfg.get('account') and _vault(desk, 'refresh.secret').is_file())


def save_config(desk, data, owner='cabinet'):
    from .vault567 import write
    cfg = config(desk)
    client_id = str(data.get('client_id') or '').strip()
    tenant = str(data.get('tenant') or 'common').strip() or 'common'
    if not re.fullmatch(r'[0-9a-fA-F-]{36}', client_id):
        raise Stop('microsoft_identifiant_application_invalide')
    if not re.fullmatch(r'[A-Za-z0-9.-]{3,100}', tenant):
        raise Stop('microsoft_annuaire_invalide')
    cfg.update(client_id=client_id.lower(), tenant=tenant, push=bool(data.get('push')), pull=bool(data.get('pull')))
    if str(data.get('client_secret') or '').strip():
        cfg['secret_file'] = write(_vault(desk, 'client.secret'), str(data['client_secret']).strip())
    desk.setting(CONFIG, cfg)
    desk.audit('microsoft5636_config', {'owner': owner, 'tenant': tenant != 'common', 'secret': bool(cfg.get('secret_file'))})
    return {'message': 'Réglages Microsoft 365 enregistrés : cliquez « Se connecter à Microsoft 365 ».'}


def redirect_uri(env, prefix):
    scheme = (env.get('HTTP_X_FORWARDED_PROTO') or env.get('wsgi.url_scheme') or 'https').split(',')[0].strip()
    host = env.get('HTTP_X_FORWARDED_HOST') or env.get('HTTP_HOST') or 'localhost'
    return '%s://%s%s/microsoft-retour' % (scheme, host.split(',')[0].strip(), prefix)


def start(desk, env, prefix, owner='cabinet'):
    """Adresse de connexion Microsoft (PKCE) ; l'état et le vérificateur sont gardés 10 minutes."""
    cfg = config(desk)
    if not cfg.get('client_id'):
        raise Stop('microsoft365_non_configure')
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(24)
    uri = redirect_uri(env, prefix)
    pending = {k: v for k, v in (desk.settings(PENDING, {}) or {}).items()
               if v.get('at', '') > (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}
    pending[state] = {'verifier': verifier, 'redirect': uri, 'owner': owner, 'at': datetime.now(timezone.utc).isoformat()}
    desk.setting(PENDING, pending)
    query = urlencode({'client_id': cfg['client_id'], 'response_type': 'code', 'redirect_uri': uri, 'response_mode': 'query', 'scope': SCOPES,
                       'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'prompt': 'select_account'})
    return {'url': '%s/%s/oauth2/v2.0/authorize?%s' % (LOGIN, quote(cfg['tenant']), query), 'redirect_uri': uri}


def _token(desk, form, http=None):
    cfg = config(desk)
    if cfg.get('secret_file'):
        form['client_secret'] = read_secret(cfg['secret_file'])
    client = http or HTTP(LOGIN, timeout=30)
    raw = client.request('POST', '%s/%s/oauth2/v2.0/token' % (LOGIN, quote(cfg['tenant'])), urlencode(form).encode(),
                         {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}, 200_000)
    try:
        out = json.loads(raw)
    except ValueError:
        raise Stop('microsoft_reponse_invalide') from None
    if not out.get('access_token'):
        raise Stop('microsoft_connexion_refusee')
    return out


def _store(desk, tok):
    from .vault567 import write
    write(_vault(desk, 'access.secret'), tok['access_token'])
    if tok.get('refresh_token'):
        write(_vault(desk, 'refresh.secret'), tok['refresh_token'])
    cfg = config(desk)
    cfg['expires'] = (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(tok.get('expires_in') or 3600)) - 120)).isoformat()
    desk.setting(CONFIG, cfg)


def finish(desk, args, http=None, graph=None):
    """Retour de Microsoft : échange du code contre les jetons, compte relié."""
    state, code = str(args.get('state') or ''), str(args.get('code') or '')
    if args.get('error'):
        raise Stop('microsoft_connexion_refusee')
    pending = dict(desk.settings(PENDING, {}) or {})
    item = pending.pop(state, None)
    desk.setting(PENDING, pending)
    if not item or not code or item.get('at', '') < (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat():
        raise Stop('microsoft_etat_invalide')
    cfg = config(desk)
    tok = _token(desk, {'client_id': cfg['client_id'], 'scope': SCOPES, 'code': code, 'redirect_uri': item['redirect'], 'grant_type': 'authorization_code',
                        'code_verifier': item['verifier']}, http)
    _store(desk, tok)
    me = _graph(desk, 'GET', '/v1.0/me?$select=userPrincipalName,displayName', http=graph, token=tok['access_token'])
    cfg = config(desk)
    cfg['account'] = str(me.get('userPrincipalName') or me.get('displayName') or 'compte Microsoft')[:200]
    cfg['connected_at'] = datetime.now(timezone.utc).isoformat()
    desk.setting(CONFIG, cfg)
    desk.audit('microsoft5636_connecte', {'owner': item.get('owner', '')})
    return {'account': cfg['account'], 'message': 'Agenda Microsoft 365 relié : %s.' % cfg['account']}


def disconnect(desk, owner='cabinet'):
    for name in ('access.secret', 'refresh.secret'):
        p = _vault(desk, name)
        if p.is_file():
            p.unlink()
    cfg = config(desk)
    cfg.pop('account', None)
    cfg.pop('expires', None)
    desk.setting(CONFIG, cfg)
    desk.audit('microsoft5636_deconnecte', {'owner': owner})
    return {'message': 'Microsoft 365 déconnecté : les jetons ont été effacés de ce serveur (révoquez aussi l’accès dans votre compte Microsoft si besoin).'}


def _access(desk, http=None):
    cfg = config(desk)
    if not connected(desk):
        raise Stop('microsoft365_non_configure')
    if cfg.get('expires', '') > datetime.now(timezone.utc).isoformat() and _vault(desk, 'access.secret').is_file():
        return read_secret(str(_vault(desk, 'access.secret')))
    tok = _token(desk, {'client_id': cfg['client_id'], 'scope': SCOPES, 'refresh_token': read_secret(str(_vault(desk, 'refresh.secret'))),
                        'grant_type': 'refresh_token'}, http)
    _store(desk, tok)
    return tok['access_token']


def _graph(desk, method, path, body=None, http=None, token=None):
    client = http or HTTP(GRAPH, timeout=30)
    headers = {'Authorization': 'Bearer ' + (token or _access(desk)), 'Accept': 'application/json', 'Prefer': 'outlook.timezone="UTC"'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    raw = client.request(method, path if path.startswith('https://') else GRAPH + path, None if body is None else json.dumps(body).encode(), headers, 4_000_000)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise Stop('microsoft_reponse_invalide') from None


def _utc(value):
    d = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _local_tz(desk):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(str((desk.c.get('calendar') or {}).get('timezone') or 'Europe/Paris'))
    except Exception:   # noqa: BLE001
        return timezone.utc


def civil_days(desk, event):
    """Journée(s) entière(s) : (premier jour, lendemain du dernier jour) en dates civiles du fuseau de l'agenda (fin exclusive)."""
    tz = _local_tz(desk)
    first = _utc(event['starts']).astimezone(tz)
    last = _utc(event['ends']).astimezone(tz)
    start = first.date()
    end = last.date() if (last.hour, last.minute, last.second) == (0, 0, 0) else last.date() + timedelta(days=1)
    return start, max(end, start + timedelta(days=1))


def graph_times(desk, event):
    """5.6.37 (audit F16) : une journée entière part en dates civiles, de minuit à minuit (fin exclusive) dans un même fuseau, comme l'exige
    Microsoft Graph pour isAllDay ; un événement horaire garde sa conversion en UTC."""
    if event.get('all_day'):
        start, end = civil_days(desk, event)
        return {'start': {'dateTime': start.isoformat() + 'T00:00:00', 'timeZone': 'UTC'},
                'end': {'dateTime': end.isoformat() + 'T00:00:00', 'timeZone': 'UTC'}}
    return {'start': {'dateTime': _utc(event['starts']).strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'},
            'end': {'dateTime': _utc(event['ends']).strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'}}


def graph_all_day(desk, value):
    """Date civile d'une borne de journée entière lue dans Graph : minuit = date telle quelle ; sinon (instant UTC, par exemple la veille à
    23 h) date civile dans le fuseau de l'agenda — jamais la veille."""
    dt = _utc(value)
    return dt.date() if (dt.hour, dt.minute, dt.second) == (0, 0, 0) else dt.astimezone(_local_tz(desk)).date()


def push_event(desk, event, http=None):
    """Copie d'un événement AxiorHub dans Outlook (une seule fois) ; sans effet si Microsoft 365 n'est pas relié ou la copie désactivée."""
    if not connected(desk) or not config(desk).get('push'):
        return None
    schema(desk)
    if event.get('source') == SOURCE or desk.db.execute('SELECT 1 FROM ms_links5636 WHERE local_id=?', (event['id'],)).fetchone():
        return None
    body = {'subject': event['title'][:250], 'categories': ['AxiorHub'], 'isAllDay': bool(event.get('all_day')),
            'body': {'contentType': 'text', 'content': (event.get('description') or '') + '\n\n(Événement créé par AxiorHub Pilote.)'},
            'location': {'displayName': event.get('location') or ''}}
    body.update(graph_times(desk, event))
    out = _graph(desk, 'POST', '/v1.0/me/events', body, http)
    desk.db.execute('INSERT OR REPLACE INTO ms_links5636 VALUES(?,?,?,?)', (event['id'], str(out.get('id') or ''), str(out.get('iCalUId') or ''),
                                                                           datetime.now(timezone.utc).isoformat()))
    desk.db.commit()
    return out.get('id')


def pull(desk, http=None, now=None):
    """Rendez-vous Outlook (−7 j / +90 j) recopiés en lecture seule dans l'agenda AxiorHub ; la fenêtre est remplacée à chaque passage."""
    from . import agenda5634 as ag
    schema(desk)
    ag.schema(desk)
    now = now or datetime.now(timezone.utc)
    a, b = now - timedelta(days=7), now + timedelta(days=90)
    url = '/v1.0/me/calendarView?' + urlencode({'startDateTime': a.strftime('%Y-%m-%dT%H:%M:%SZ'), 'endDateTime': b.strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                 '$top': 200, '$select': 'id,iCalUId,subject,start,end,location,isAllDay,isCancelled,bodyPreview,categories'})
    events, pages = [], 0
    token = _access(desk)
    while url and pages < 10:
        out = _graph(desk, 'GET', url, http=http, token=token)
        events += out.get('value') or []
        url = out.get('@odata.nextLink') or ''
        pages += 1
    ours = {r['ical_uid'] for r in desk.db.execute('SELECT ical_uid FROM ms_links5636') if r['ical_uid']} | \
           {r['graph_id'] for r in desk.db.execute('SELECT graph_id FROM ms_links5636')}
    desk.db.execute("DELETE FROM agenda5634 WHERE source=? AND starts>=? AND starts<?", (SOURCE, a.isoformat(), b.isoformat()))
    added = 0
    for ev in events:
        if ev.get('isCancelled') or ev.get('id') in ours or ev.get('iCalUId') in ours or 'AxiorHub' in (ev.get('categories') or []):
            continue
        try:
            begins, ends = _utc(ev['start']['dateTime']), _utc(ev['end']['dateTime'])
        except (KeyError, ValueError, TypeError):
            continue
        title = str(ev.get('subject') or 'Rendez-vous Outlook').strip()[:200] or 'Rendez-vous Outlook'
        all_day = bool(ev.get('isAllDay'))
        if all_day:   # 5.6.37 (audit F16) : dates civiles conservées à l'aller-retour
            first, after = graph_all_day(desk, ev['start']['dateTime']), graph_all_day(desk, ev['end']['dateTime'])
            begins = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
            ends = datetime(after.year, after.month, after.day, tzinfo=timezone.utc) if after > first else begins + timedelta(days=1)
        data = {'title': title if len(title) >= 2 else 'Rendez-vous Outlook', 'kind': 'rendez_vous', 'location': (ev.get('location') or {}).get('displayName', ''),
                'description': str(ev.get('bodyPreview') or '')[:2000], 'reminders': [], 'all_day': all_day,
                'start': begins.date().isoformat() if all_day else begins.isoformat(), 'days': max(1, (ends - begins).days) if all_day else 1,
                'duration_minutes': min(1440, max(5, int((ends - begins).total_seconds() // 60)))}
        try:
            ag.create(desk, data, 'cabinet', source=SOURCE, uid='ms:' + str(ev.get('iCalUId') or ev.get('id')), copy=False)
            added += 1
        except Stop:
            continue
    ag.rebuild_cache(desk)
    desk.db.commit()
    cfg = config(desk)
    cfg['last_pull'] = now.isoformat()
    desk.setting(CONFIG, cfg)
    return {'imported': added, 'message': '%d rendez-vous Outlook dans l’agenda.' % added}


def sync(desk, http=None):
    """Travail de fond : copie des événements AxiorHub récents, puis lecture d'Outlook."""
    from . import agenda5634 as ag
    out = {'pushed': 0, 'imported': 0}
    if not connected(desk):
        return out
    if config(desk).get('push'):
        schema(desk)
        since = config(desk).get('connected_at', '')
        rows = desk.db.execute("SELECT * FROM agenda5634 WHERE status='actif' AND source='local' AND created>=? AND starts>? "
                               "AND id NOT IN (SELECT local_id FROM ms_links5636) LIMIT 30", (since, datetime.now(timezone.utc).isoformat())).fetchall()
        for r in rows:
            try:
                out['pushed'] += bool(push_event(desk, ag.public(dict(r)), http))
            except Stop:
                break
    if config(desk).get('pull'):
        out['imported'] = pull(desk, http)['imported']
    return out


def schedule(desk, stamp=None):
    if not connected(desk):
        return []
    stamp = stamp or datetime.now(timezone.utc).timestamp()
    if stamp - float(desk.settings('microsoft5636:last', 0) or 0) < 900:
        return []
    desk.setting('microsoft5636:last', stamp)
    return [desk.enqueue('microsoft5636', {}, priority=60)]


def job(desk, args):
    return sync(desk)


def settings_html(desk, prefix, env=None):
    cfg = config(desk)
    uri = redirect_uri(env or {}, prefix) if env else prefix + '/microsoft-retour'
    state = ('<p class="s5636-ok">Relié : <strong>%s</strong>%s</p>' % (e(cfg['account']), (' · dernière lecture : ' + e(cfg['last_pull'][:16].replace('T', ' '))) if cfg.get('last_pull') else '')
             if connected(desk) else '<p class="ax-muted">Non relié.</p>')
    return ('<section class="ax-card" id="microsoft5636"><h3>Agenda Microsoft 365 (Outlook)</h3>%s'
            '<details%s><summary>Réglages de l’application Microsoft</summary><ol class="ax-muted"><li>Dans le portail Microsoft Entra, « Inscriptions d’applications » '
            '› « Nouvelle inscription » ; type « Web » ; adresse de redirection : <code>%s</code>.</li><li>Autorisations déléguées Microsoft Graph : '
            'Calendars.ReadWrite, User.Read, offline_access.</li><li>Copiez l’identifiant d’application (et, si vous en créez un, le secret client) ci-dessous.</li></ol>'
            '<form class="m5-form" data-api="m5636/microsoft/reglages" data-reload="1"><label>Identifiant d’application (client)<input name="client_id" value="%s" '
            'placeholder="00000000-0000-0000-0000-000000000000"></label><label>Annuaire (tenant)<input name="tenant" value="%s"></label>'
            '<label>Secret client (laisser vide pour conserver)<input name="client_secret" type="password" autocomplete="off"></label>'
            '<label class="e5636-switch"><input type="checkbox" name="push"%s> Copier les audiences, échéances et rendez-vous créés par AxiorHub dans Outlook</label>'
            '<label class="e5636-switch"><input type="checkbox" name="pull"%s> Afficher les rendez-vous Outlook dans l’agenda AxiorHub</label>'
            '<button class="ax-btn ghost" type="submit">Enregistrer</button></form></details><div class="ax-actions">'
            '<button type="button" class="ax-btn" data-ms5636="connexion">Se connecter à Microsoft 365</button>%s</div></section>') % (
        state, '' if cfg.get('client_id') else ' open', e(uri), e(cfg.get('client_id', ''), quote=True), e(cfg.get('tenant', 'common'), quote=True),
        ' checked' if cfg.get('push') else '', ' checked' if cfg.get('pull') else '',
        ('<button type="button" class="ax-btn ghost" data-ms5636="synchroniser">Actualiser maintenant</button>'
         '<button type="button" class="ax-btn ghost" data-ms5636="deconnexion">Déconnecter</button>') if connected(desk) else '')


def return_page(desk, prefix, args):
    try:
        out = finish(desk, args)
        return '<section class="ax-card"><h1>Microsoft 365</h1><p class="s5636-ok">%s</p><p><a class="ax-btn" href="%s">Revenir aux connexions</a></p></section>' % (
            e(out['message']), e(prefix + '/parametres?rubrique=connexions#microsoft5636', quote=True))
    except Stop as exc:
        from .erreurs5634 import explain
        return '<section class="ax-card"><h1>Microsoft 365</h1><p class="ax-alert">%s</p><p><a class="ax-btn" href="%s">Revenir aux connexions</a></p></section>' % (
            e(explain(str(exc))), e(prefix + '/parametres?rubrique=connexions#microsoft5636', quote=True))


def api_post(desk, route, data, owner, env=None, prefix=''):
    if route == 'microsoft/reglages':
        return save_config(desk, data, owner)
    if route == 'microsoft/connexion':
        return start(desk, env or {}, prefix, owner)
    if route == 'microsoft/deconnexion':
        return disconnect(desk, owner)
    if route == 'microsoft/synchroniser':
        if not connected(desk):
            raise Stop('microsoft365_non_configure')
        job_id = desk.enqueue('microsoft5636', {}, priority=20)
        return {'job': job_id, 'message': 'Actualisation de l’agenda Microsoft 365 lancée.'}
    return None

"""Application mobile installable (PWA) et notifications neutres 4.9.0.

Règle absolue : AUCUNE donnée de dossier dans une notification. Le message poussé est VIDE (« tickle » Web Push
signé VAPID, sans charge utile) ; le service worker lit ensuite, avec la session de l'avocat, un résumé qui ne
contient que des NOMBRES, et affiche un texte fixe : « 2 brouillons à valider · 1 échéance proche ». Ni nom de
client, ni objet, ni extrait, ni nom de dossier : ces champs n'existent pas dans le résumé.

Le service de poussée du navigateur (Google, Mozilla, Apple) reçoit seulement un point de terminaison anonyme
et un signal vide. La fonction est désactivée par défaut et exige l'accord du navigateur.
"""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import Stop

ALLOWED_PUSH_SUFFIXES = ('.googleapis.com', '.push.services.mozilla.com', '.push.apple.com', '.notify.windows.com')
MAX_SUBSCRIPTIONS = 8
NOTIFY_INTERVAL = 300
DEADLINE_DAYS = 3

SCHEMA = '''
CREATE TABLE IF NOT EXISTS push_subs490(
  id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, last_ok TEXT NOT NULL DEFAULT '');
'''

http_post = None   # remplaçable dans les tests : http_post(url, headers) -> status


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


# ------------------------------------------------------------------------------------------ résumé neutre
def counts(desk, today=None):
    today = today or date.today()
    drafts = desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready'").fetchone()[0]
    limit = (today + timedelta(days=DEADLINE_DAYS)).isoformat()
    try:
        deadlines = desk.db.execute(
            "SELECT COUNT(*) FROM deadlines450 WHERE status NOT IN ('terminee','annulee') AND due<>'' AND due<=?", (limit,)).fetchone()[0]
    except Exception:
        deadlines = 0
    return {'drafts': int(drafts), 'deadlines': int(deadlines)}


def neutral_text(c):
    parts = []
    if c['drafts']:
        parts.append('%d brouillon%s à valider' % (c['drafts'], 's' if c['drafts'] > 1 else ''))
    if c['deadlines']:
        parts.append('%d échéance%s proche%s' % (c['deadlines'], 's' if c['deadlines'] > 1 else '', 's' if c['deadlines'] > 1 else ''))
    return ' · '.join(parts)


def summary(desk, today=None):
    c = counts(desk, today)
    return {'drafts': c['drafts'], 'deadlines': c['deadlines'], 'total': c['drafts'] + c['deadlines'],
            'text': neutral_text(c), 'title': 'AxiorHub'}


# ----------------------------------------------------------------------------------------------- VAPID
def _key_path(desk):
    return Path(desk.c['state_dir']) / 'vapid490.pem'


def vapid_available():
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


def _private_key(desk):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    path = _key_path(desk)
    if path.is_file():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(pem)
    return key


def public_key(desk):
    if not vapid_available():
        raise Stop('notifications_indisponibles')
    from cryptography.hazmat.primitives import serialization
    raw = _private_key(desk).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64u(raw)


def vapid_header(desk, endpoint, subject, now=None):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    parts = urllib.parse.urlsplit(endpoint)
    claims = {'aud': '%s://%s' % (parts.scheme, parts.netloc), 'exp': int(now or time.time()) + 12 * 3600, 'sub': subject}
    signing = b64u(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode()) + '.' + b64u(
        json.dumps(claims, separators=(',', ':')).encode())
    r, s = decode_dss_signature(_private_key(desk).sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    token = signing + '.' + b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))
    return 'vapid t=%s, k=%s' % (token, public_key(desk))


# ------------------------------------------------------------------------------------------ abonnements
def endpoint_ok(endpoint):
    p = urllib.parse.urlsplit(str(endpoint))
    return (p.scheme == 'https' and bool(p.hostname) and not p.username and not p.password and len(str(endpoint)) <= 1000
            and any(p.hostname.endswith(s) for s in ALLOWED_PUSH_SUFFIXES) and p.port in (None, 443))


def status(desk):
    ensure_schema(desk)
    return {'available': vapid_available(), 'enabled': bool(desk.settings('mobile490:push', False)),
            'subscriptions': desk.db.execute('SELECT COUNT(*) FROM push_subs490').fetchone()[0],
            'public_key': public_key(desk) if vapid_available() else '',
            'summary': summary(desk)}


def subscribe(desk, endpoint, label='', confirm=''):
    ensure_schema(desk)
    if confirm != 'yes':
        raise Stop('confirmation_notifications_requise')
    if not vapid_available():
        raise Stop('notifications_indisponibles')
    if not endpoint_ok(endpoint):
        raise Stop('point_de_terminaison_refuse')
    sid = hashlib.sha256(endpoint.encode()).hexdigest()[:24]
    exists = desk.db.execute('SELECT 1 FROM push_subs490 WHERE id=?', (sid,)).fetchone()
    if not exists and desk.db.execute('SELECT COUNT(*) FROM push_subs490').fetchone()[0] >= MAX_SUBSCRIPTIONS:
        raise Stop('trop_d_appareils')
    desk.db.execute('INSERT OR REPLACE INTO push_subs490(id,endpoint,label,created) VALUES(?,?,?,?)',
                    (sid, endpoint, re.sub(r'[^\w .\-]', '', str(label))[:40], datetime.now(timezone.utc).isoformat()))
    desk.db.commit()
    desk.setting('mobile490:push', True)
    desk.audit('mobile_490_abonnement', {'devices': desk.db.execute('SELECT COUNT(*) FROM push_subs490').fetchone()[0]})
    return status(desk)


def unsubscribe(desk, endpoint='', everything=False):
    ensure_schema(desk)
    if everything:
        desk.db.execute('DELETE FROM push_subs490')
        desk.setting('mobile490:push', False)
    elif endpoint:
        desk.db.execute('DELETE FROM push_subs490 WHERE id=?', (hashlib.sha256(str(endpoint).encode()).hexdigest()[:24],))
    desk.db.commit()
    desk.audit('mobile_490_desabonnement', {'all': bool(everything)})
    return status(desk)


# ----------------------------------------------------------------------------------------------- envoi
def _post(url, headers):
    req = urllib.request.Request(url, data=b'', method='POST', headers={**headers, 'Content-Length': '0'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return 0


def dispatch(desk, today=None, force=False, now=None):
    """Appelé par la maintenance périodique : un signal vide si le nombre d'éléments à traiter AUGMENTE."""
    ensure_schema(desk)
    now = time.time() if now is None else now
    if not desk.settings('mobile490:push', False) or not vapid_available():
        return {'sent': 0, 'reason': 'desactive'}
    if not force and now - float(desk.settings('mobile490:last_run', 0)) < NOTIFY_INTERVAL:
        return {'sent': 0, 'reason': 'trop_tot'}
    desk.setting('mobile490:last_run', now)
    c = counts(desk, today)
    last = desk.settings('mobile490:last_counts', {'drafts': 0, 'deadlines': 0})
    desk.setting('mobile490:last_counts', c)
    if not force and c['drafts'] <= last.get('drafts', 0) and c['deadlines'] <= last.get('deadlines', 0):
        return {'sent': 0, 'reason': 'rien_de_nouveau'}
    subject = 'mailto:' + (desk.c['mail'].get('from_address') or 'contact@invalid.example')
    sent, dead = 0, []
    for row in desk.db.execute('SELECT id,endpoint FROM push_subs490').fetchall():
        try:
            headers = {'Authorization': vapid_header(desk, row['endpoint'], subject), 'TTL': '3600', 'Urgency': 'normal'}
        except Stop:
            continue
        code = (http_post or _post)(row['endpoint'], headers)
        if code in (200, 201, 202):
            sent += 1
            desk.db.execute('UPDATE push_subs490 SET last_ok=? WHERE id=?', (datetime.now(timezone.utc).isoformat(), row['id']))
        elif code in (404, 410):
            dead.append(row['id'])
    for sid in dead:
        desk.db.execute('DELETE FROM push_subs490 WHERE id=?', (sid,))
    desk.db.commit()
    return {'sent': sent, 'expired': len(dead), 'reason': 'ok'}


# ------------------------------------------------------------------------------------- fichiers PWA
def manifest(prefix, theme='#17324d'):
    return {'name': 'AxiorHub Pilote', 'short_name': 'AxiorHub Pilote', 'description': 'Courriels à valider, échéances et recherche du cabinet',
            'lang': 'fr', 'dir': 'ltr', 'start_url': prefix + '/aujourdhui', 'scope': prefix + '/', 'id': prefix + '/',
            'display': 'standalone', 'orientation': 'any', 'background_color': '#ffffff', 'theme_color': theme,
            'icons': [{'src': prefix + '/static/axiorhub-icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                      {'src': prefix + '/static/axiorhub-icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                      {'src': prefix + '/static/axiorhub-icon-maskable.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}],
            'shortcuts': [{'name': 'Courriels à relire', 'url': prefix + '/courriels'},
                          {'name': 'Échéances', 'url': prefix + '/echeances'},
                          {'name': 'Recherche', 'url': prefix + '/recherche'}]}


def _version():
    from . import __version__
    return __version__


def service_worker(prefix):
    """Service worker : met en cache uniquement les fichiers statiques et une page de repli ; JAMAIS les pages de dossier ni l'API."""
    return '''/* AxiorHub 4.9.0 — service worker. Aucun contenu de dossier n'est mis en cache. */
const PREFIX = %(prefix)s;
const CACHE = 'axiorhub-static-' + %(version)s;
const SHELL = [PREFIX + '/static/v440.css', PREFIX + '/static/v490.css', PREFIX + '/static/axiorhub-icon.png', PREFIX + '/hors-ligne'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', e => {
  const req = e.request; const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin || !url.pathname.startsWith(PREFIX + '/')) return;
  if (url.pathname.startsWith(PREFIX + '/static/') && url.pathname.endsWith('.css')) {
    /* 5.5.0 : réseau d'abord (feuille à jour après chaque installation), cache seulement hors ligne */
    e.respondWith(fetch(req).then(res => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } return res; }).catch(() => caches.match(req, { ignoreSearch: true })));
    return;
  }
  if (url.pathname.startsWith(PREFIX + '/static/') && !url.pathname.endsWith('.js')) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } return res; })));
    return;
  }
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match(PREFIX + '/hors-ligne')));
  }
});
self.addEventListener('push', e => {
  e.waitUntil((async () => {
    let text = 'Des éléments attendent votre attention.';
    try {
      const r = await fetch(PREFIX + '/api440/mobile/summary', { credentials: 'same-origin', cache: 'no-store' });
      if (r.ok) { const s = await r.json(); if (s && typeof s.text === 'string') text = s.text || 'Aucun élément en attente.'; }
    } catch (x) { /* hors ligne : texte générique */ }
    await self.registration.showNotification('AxiorHub', { body: text, icon: PREFIX + '/static/axiorhub-icon.png', tag: 'axiorhub-summary', renotify: true, data: { url: PREFIX + '/aujourdhui' } });
  })());
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || PREFIX + '/aujourdhui';
  e.waitUntil(self.clients.matchAll({ type: 'window' }).then(list => { for (const c of list) { if ('focus' in c) { c.navigate(target); return c.focus(); } } return self.clients.openWindow(target); }));
});
''' % {'prefix': json.dumps(prefix), 'version': json.dumps(_version())}


def offline_page(prefix):
    return ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Hors ligne · AxiorHub Pilote</title><link rel="stylesheet" href="%s/static/v440.css"></head><body>'
            '<main class="ax-main"><h1>Vous êtes hors ligne</h1><p>AxiorHub n’affiche aucun dossier sans connexion : '
            'les données du cabinet ne sont pas conservées sur ce téléphone. Reconnectez-vous puis rechargez la page.</p>'
            '<p><a class="ax-btn" href="%s/aujourdhui">Réessayer</a></p></main></body></html>') % (prefix, prefix)

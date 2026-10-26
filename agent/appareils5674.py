"""5.6.74 : associer un téléphone par QR code et valider depuis le téléphone (version serveur).

Inspiré de l'association d'appareils de Row-Bot (invitation à usage unique valable 10 minutes, session révocable par appareil) ;
code propre à AxiorHub, appuyé sur les comptes et sessions de 5.6.48.

- **Associer** (Paramètres › Poste et maintenance › Téléphones associés, ordinateur déjà connecté) : « Associer un téléphone »
  affiche un QR code. Il contient une adresse d'invitation à usage unique, valable 10 minutes, liée au compte connecté (seule
  l'empreinte du jeton est conservée).
- **Sur le téléphone** : l'appareil photo ouvre la page « Associer ce téléphone » (compte affiché, nom de l'appareil), le bouton
  « Associer » ouvre une session de ce compte valable 90 jours (cookie HttpOnly, Secure). Deux étapes : un aperçu de lien ne consomme
  jamais l'invitation. Tentatives limitées par adresse.
- **Valider depuis le téléphone** : page « À valider » (``/mobile``) — la file « À décider / À relire » du compte, en grand, avec les
  mêmes boutons qu'à l'ordinateur (relire, déposer une réponse « à compléter », écarter, autoriser une opération…). Les notifications
  (4.9.0) annoncent aussi, en nombres seulement, les éléments à valider.
- **Retirer** un téléphone ferme sa session immédiatement ; changer de mot de passe ferme aussi les sessions de ses téléphones.

Version serveur seulement (page de connexion 5.6.48) : le poste Windows ou Ubuntu n'est pas joignable depuis un téléphone.
"""
from datetime import datetime, timedelta, timezone
from html import escape as e
import io
import json
import re
import secrets
import threading
import time

from .common import Stop

INVITE_MINUTES = 10
DEVICE_DAYS = 90
MAX_DEVICES = 10
SCHEMA = '''
CREATE TABLE IF NOT EXISTS appareils5674_invitations(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT NOT NULL,
  expires TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS appareils5674(id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, user_id INTEGER NOT NULL, label TEXT NOT NULL,
  created TEXT NOT NULL, expires TEXT NOT NULL);
'''


def _now():
    return datetime.now(timezone.utc)


def connect(state_dir):
    from . import connexion5648 as cx
    db = cx.connect(state_dir)
    db.executescript(SCHEMA)
    return db


def available(auth):
    """Comptes et sessions de la version serveur (page de connexion 5.6.48) ; jamais sur le poste."""
    from . import connexion5648 as cx
    return bool(isinstance(auth, dict) and cx.enabled(auth) and not auth.get('poste'))


def base_url(env, auth):
    """Adresse publique du serveur vue par le navigateur de l'ordinateur (celle que le téléphone ouvrira)."""
    origin = str((auth or {}).get('origin') or '').rstrip('/')
    if origin.startswith('https://'):
        return origin
    proto = str(env.get('HTTP_X_FORWARDED_PROTO') or env.get('wsgi.url_scheme') or 'https').split(',')[0].strip()
    host = str(env.get('HTTP_X_FORWARDED_HOST') or env.get('HTTP_HOST') or '').split(',')[0].strip()
    if not re.fullmatch(r'[A-Za-z0-9.\-]+(?::\d{1,5})?', host):
        raise Stop('adresse_publique_inconnue')
    return '%s://%s' % (proto if proto in ('http', 'https') else 'https', host)


def qr_svg(text):
    """QR code (SVG) de l'adresse d'invitation."""
    from ._vendor import segno
    q = segno.make(str(text), error='m')
    out = io.BytesIO()
    q.save(out, kind='svg', scale=6, border=3, dark='#0b1220', light='#ffffff', xmldecl=False)
    return out.getvalue()


# Image du QR code gardée en mémoire 10 minutes, servie à la même origine (la politique de sécurité des pages n'admet pas les images
# « data: ») et au seul compte qui l'a demandée ; jamais écrite sur le disque (le jeton n'est conservé que sous forme d'empreinte).
_QR = {}
_QR_LOCK = threading.Lock()


def _keep_qr(user_id, svg, expires_ts):
    ident = secrets.token_hex(12)
    with _QR_LOCK:
        for key in [k for k, v in _QR.items() if v[1] < time.time()]:
            del _QR[key]
        _QR[ident] = (int(user_id), float(expires_ts), svg)
    return ident


def qr_image(ident, user):
    with _QR_LOCK:
        entry = _QR.get(str(ident or ''))
    if not entry or not user or entry[0] != int(user['id']) or entry[1] < time.time():
        raise Stop('qr_code_expire')
    return entry[2]


def invite(state_dir, user, base, prefix):
    """Invitation à usage unique (10 minutes) pour le compte connecté ; renvoie l'adresse et son QR code."""
    from . import connexion5648 as cx
    if not user:
        raise Stop('compte_requis')
    db = connect(state_dir)
    try:
        if db.execute('SELECT COUNT(*) FROM appareils5674 WHERE user_id=? AND expires>?', (user['id'], _now().isoformat())).fetchone()[0] >= MAX_DEVICES:
            raise Stop('dix_appareils_maximum')
        db.execute('DELETE FROM appareils5674_invitations WHERE expires<?', (_now().isoformat(),))
        token = secrets.token_urlsafe(32)
        expires = _now() + timedelta(minutes=INVITE_MINUTES)
        db.execute('INSERT INTO appareils5674_invitations VALUES(?,?,?,?,0)', (cx._token_hash(token), int(user['id']), _now().isoformat(), expires.isoformat()))
        db.commit()
        cx._log(db, str(user.get('owner') or user.get('ident') or 'cabinet'), 'appareil_invitation')
    finally:
        db.close()
    url = '%s%s/connexion/appareil?jeton=%s' % (base, prefix, token)
    ident = _keep_qr(user['id'], qr_svg(url), expires.timestamp())
    return {'ok': True, 'adresse': url, 'qr': '%s/api440/m5674/qr?id=%s' % (prefix, ident), 'expire': expires.isoformat(), 'minutes': INVITE_MINUTES}


def _invitation(db, token):
    from . import connexion5648 as cx
    if not token or len(token) > 100:
        return None
    return db.execute('SELECT * FROM appareils5674_invitations WHERE token_hash=? AND used=0 AND expires>?',
                      (cx._token_hash(token), _now().isoformat())).fetchone()


def _account_label(db, auth, user_id):
    from . import connexion5648 as cx
    if int(user_id) == cx.ADMIN_ID:
        return str(cx.admin_user(auth, db).get('ident') or 'administrateur')
    row = db.execute('SELECT ident,name FROM users WHERE id=? AND active=1', (int(user_id),)).fetchone()
    return (row['name'] or row['ident']) if row else ''


def pairing(db, env, auth, prefix, secure, form=None, query=None):
    """Page publique « Associer ce téléphone » (GET : aperçu ; POST : association). Appelée par connexion5648.handle."""
    from . import connexion5648 as cx
    form = form or {}
    token = str((form.get('jeton') if env.get('REQUEST_METHOD') == 'POST' else (query or {}).get('jeton', [''])[0]) or '')[:100]
    # le jeton figure dans l'adresse : jamais transmis à un autre site. « same-origin » et non « no-referrer » : avec « no-referrer », le
    # navigateur envoie « Origin: null » au bouton « Associer » et le contrôle de même origine refuse le formulaire.
    headers = [('Referrer-Policy', 'same-origin')]
    client = cx._client(env)
    if cx._limited(db, 'appareil:' + client, 'ip'):
        return cx._html(cx._page(prefix, 'Associer un téléphone', cx._notice('trop_de_tentatives')), '429 Too Many Requests', headers)
    row = _invitation(db, token)
    if row is None:
        cx._attempt(db, 'appareil:' + client)
        body = ('<p class="cx48-note">Ce code a expiré ou a déjà servi. Sur l’ordinateur, ouvrez Paramètres › Poste et maintenance › '
                'Téléphones associés, puis « Associer un téléphone » pour en afficher un nouveau.</p>')
        return cx._html(cx._page(prefix, 'Associer un téléphone', body), '410 Gone', headers)
    account = _account_label(db, auth, row['user_id'])
    if not account:
        return cx._html(cx._page(prefix, 'Associer un téléphone', cx._notice(text='Compte désactivé.')), '403 Forbidden', headers)
    if env.get('REQUEST_METHOD') != 'POST':
        body = ('<p>Associer ce téléphone au compte <strong>%s</strong> ? Il pourra consulter AxiorHub et valider ce qui vous attend, '
                'pendant %d jours ou jusqu’à ce que vous le retiriez.</p>'
                '<form method="post" action="%s/connexion/appareil" class="cx48-form"><input type="hidden" name="jeton" value="%s">'
                '<label for="ap74-nom">Nom de cet appareil</label><input id="ap74-nom" name="nom" maxlength="60" value="Téléphone" required>'
                '<button type="submit" class="cx48-btn">Associer</button></form>'
                '<p class="cx48-note">Ce n’est pas vous ? Fermez cette page : rien n’est associé sans ce bouton.</p>') % (
            e(account), DEVICE_DAYS, e(prefix, quote=True), e(token, quote=True))
        return cx._html(cx._page(prefix, 'Associer un téléphone', body), '200 OK', headers)
    label = re.sub(r'[\x00-\x1f<>]+', '', str(form.get('nom') or 'Téléphone')).strip()[:60] or 'Téléphone'
    db.execute('UPDATE appareils5674_invitations SET used=1 WHERE token_hash=?', (row['token_hash'],))
    if int(row['user_id']) == cx.ADMIN_ID:
        user = cx.admin_user(auth, db)
    else:
        user = cx._user_dict(db.execute('SELECT * FROM users WHERE id=?', (int(row['user_id']),)).fetchone())
    session, _ = cx.open_session(db, auth, user, True, 'appareil')
    expires = _now() + timedelta(days=DEVICE_DAYS)
    db.execute('UPDATE sessions SET expires=? WHERE token_hash=?', (expires.isoformat(), cx._token_hash(session)))
    db.execute('INSERT INTO appareils5674 VALUES(?,?,?,?,?,?)', (secrets.token_hex(8), cx._token_hash(session), int(row['user_id']), label,
                                                                 _now().isoformat(), expires.isoformat()))
    db.commit()
    cx._log(db, account[:80], 'appareil_associe', label)
    return cx._redirect(prefix + '/mobile?associe=1', headers + [cx.cookie_header(prefix, session, DEVICE_DAYS * 86400, secure)])


def devices(state_dir, user):
    db = connect(state_dir)
    try:
        rows = db.execute('SELECT a.id,a.label,a.created,a.expires FROM appareils5674 a JOIN sessions s ON s.token_hash=a.session_hash '
                          'WHERE a.user_id=? AND s.expires>? ORDER BY a.created DESC', (int(user['id']), _now().isoformat())).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def remove(state_dir, user, device_id):
    from . import connexion5648 as cx
    db = connect(state_dir)
    try:
        row = db.execute('SELECT * FROM appareils5674 WHERE id=? AND user_id=?', (str(device_id), int(user['id']))).fetchone()
        if not row:
            raise Stop('appareil_introuvable')
        db.execute('DELETE FROM sessions WHERE token_hash=?', (row['session_hash'],))
        db.execute('DELETE FROM appareils5674 WHERE id=?', (row['id'],))
        db.commit()
        cx._log(db, str(user.get('owner') or 'cabinet')[:80], 'appareil_retire', row['label'])
        return {'ok': True, 'message': '« %s » est retiré : sa session est fermée.' % row['label']}
    finally:
        db.close()


def settings_html(state_dir, auth, user):
    """Paramètres › Poste et maintenance : téléphones associés au compte connecté."""
    if not available(auth):
        return ('<section class="ax-card" id="appareils5674"><h2>Téléphones associés</h2><p class="ax-muted">L’association d’un téléphone par QR '
                'code se fait sur la version serveur d’AxiorHub (accessible en HTTPS) ; un poste Windows ou Ubuntu n’est pas joignable depuis '
                'un téléphone.</p></section>')
    if not user:
        return ''
    rows = ''.join('<li><strong>%s</strong> · associé le %s · jusqu’au %s <form class="q51-inline" data-c5671="m5674/retirer" data-reload '
                   'data-confirm="Retirer cet appareil ? Sa session sera fermée."><input type="hidden" name="id" value="%s">'
                   '<button class="ax-btn ghost" type="submit">Retirer</button></form></li>' % (
                       e(d['label']), e(d['created'][:10]), e(d['expires'][:10]), e(d['id'], quote=True)) for d in devices(state_dir, user))
    return ('<section class="ax-card" id="appareils5674"><h2>Téléphones associés</h2>'
            '<p class="ax-muted">Associez votre téléphone en scannant un QR code avec son appareil photo : il ouvre AxiorHub connecté à votre '
            'compte (%d jours), avec la page « À valider » pour décider ou relire depuis n’importe où. Le code ne sert qu’une fois et expire '
            'au bout de %d minutes.</p>'
            '<button type="button" class="ax-btn" data-ap74="inviter">Associer un téléphone</button>'
            '<div class="ap74-qr" id="ap74-qr" hidden></div>'
            '<h3>Appareils</h3><ul class="ap74-list">%s</ul></section>') % (
        DEVICE_DAYS, INVITE_MINUTES, rows or '<li class="ax-muted">Aucun téléphone associé.</li>')


def mobile_page(desk, prefix, args, env):
    """Page « À valider » : la file du compte, en grand, pour le téléphone."""
    from . import file5651
    from .membres5650 import viewer_hidden
    from .web567 import actor
    owner, role = actor(env)
    hidden = viewer_hidden() or frozenset()
    queue = file5651.html(desk, prefix, None, str(args.get('file') or ''), hidden, 40, True, owner, role)
    welcome = ''
    if args.get('associe'):
        welcome = ('<p class="notice ap74-ok">Téléphone associé. Pour l’avoir sous la main : menu du navigateur › « Ajouter à l’écran '
                   'd’accueil ». Les notifications (en nombres seulement) s’activent dans Paramètres › Poste et maintenance.</p>')
    return ('<section class="ap74-mobile">%s<p class="ax-muted">Ce qui attend votre décision ou votre relecture. Les boutons agissent comme '
            'à l’ordinateur ; rien n’est envoyé sans votre action.</p>%s<p><a class="ax-btn ghost" href="%s/aujourdhui">Ouvrir AxiorHub</a></p>'
            '</section>') % (welcome, queue, e(prefix, quote=True))


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    route = name[len('m5674/'):].split('?', 1)[0]
    if route not in ('inviter', 'retirer', 'qr'):
        raise Stop('route_inconnue')
    if not available(auth):
        raise Stop('association_reservee_au_serveur')
    user = env.get('axiorhub.user')
    if not user:
        raise Stop('compte_requis')
    if route == 'qr':
        if method != 'GET':
            raise Stop('methode_refusee')
        return {'status': '200 OK', 'kind': 'image/svg+xml', 'body': qr_image(args.get('id'), user),
                'headers': [('Cache-Control', 'no-store')]}
    if method != 'POST':
        raise Stop('methode_refusee')
    from .web5634 import _post
    data = _post(env, auth, 2_000)
    if route == 'inviter':
        out = invite(desk.c['state_dir'], user, base_url(env, auth), prefix)
        desk.audit('appareils5674_invitation', {'par': str(user.get('owner') or 'cabinet')[:80]})
        return json_out(out)
    out = remove(desk.c['state_dir'], user, str(data.get('id') or ''))
    desk.audit('appareils5674_retrait', {'par': str(user.get('owner') or 'cabinet')[:80]})
    return json_out(out)


def pending_count(desk):
    """Nombre d'éléments « À décider / À relire » (pour la notification neutre)."""
    try:
        from .file5657 import count
        return int(count(desk))
    except Exception:   # noqa: BLE001
        return 0

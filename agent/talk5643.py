"""5.6.43 (cahier v9, § 22) : page Talk facultative — l'application Nextcloud Talk de l'utilisateur intégrée dans AxiorHub.

- Préférence PAR UTILISATEUR (``talk5643:<utilisateur>``) : afficher la page, adresse de l'interface Talk, version. Désactivée par défaut.
  Elle est indépendante des automatismes Talk existants (``settings568.talk_enabled``, ``talk568_prepare``) : l'afficher ne crée aucun
  salon, n'envoie aucune invitation et ne confie rien à l'IA ; la page fonctionne avec l'IA en pause.
- Adresse validée contre la connexion Nextcloud configurée : même origine, sous-chemin d'installation conservé, chemin de l'application
  Talk (``…/apps/spreed/``), ni identifiant, ni mot de passe, ni fragment. Aucun domaine n'est codé dans le logiciel.
- L'iframe utilise la SESSION NEXTCLOUD DU NAVIGATEUR ; aucun mot de passe ni jeton n'est injecté. La politique du parent n'est élargie
  que pour /talk et pour cette seule origine (``frame-src``, et délégation micro, caméra, plein écran, partage d'écran).
- Si Nextcloud interdit l'intégration (X-Frame-Options, CSP ``frame-ancestors``), l'ouverture dans un onglet reste toujours disponible.
"""
from html import escape as e
import json
import re
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .common import Stop

KEY = 'talk5643:'


def _base(cfg):
    """Adresse de l'interface Nextcloud configurée (origine + sous-chemin éventuel d'installation)."""
    raw = str(((cfg or {}).get('nextcloud') or {}).get('url') or '').strip()
    parts = urlsplit(raw)
    if parts.scheme != 'https' or not parts.hostname or 'example.com' in parts.hostname:
        return '', ''
    path = parts.path.split('/remote.php', 1)[0].split('/index.php', 1)[0].rstrip('/')
    return parts.scheme + '://' + parts.netloc, path


def suggested(cfg):
    origin, path = _base(cfg)
    return (origin + path + '/index.php/apps/spreed/') if origin else ''


def validate(cfg, url):
    """Adresse Talk acceptée : même origine que la connexion Nextcloud, chemin de l'application Talk, aucune donnée d'accès."""
    origin, base = _base(cfg)
    if not origin:
        raise Stop('nextcloud_non_configure')
    url = str(url or '').strip() or suggested(cfg)
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.username or parts.password or parts.fragment:
        raise Stop('adresse_talk_invalide')
    if parts.scheme + '://' + parts.netloc != origin:
        raise Stop('adresse_talk_hors_nextcloud')
    if not parts.path.startswith(base + '/') or '/apps/spreed' not in parts.path or '..' in parts.path:
        raise Stop('adresse_talk_invalide')
    return url


def get(desk, owner):
    value = desk.settings(KEY + str(owner or 'cabinet'), {}) or {}
    value = value if isinstance(value, dict) else {}
    return {'enabled': bool(value.get('enabled')), 'talk_url': str(value.get('talk_url') or '') or suggested(desk.c),
            'version': int(value.get('version') or 0), 'updated': str(value.get('updated') or '')}


def save(desk, owner, data):
    current = get(desk, owner)
    try:
        expected = int(data.get('expected_version', current['version']))
    except (TypeError, ValueError):
        raise Stop('version_invalide') from None
    if expected != current['version']:
        raise Stop('reglage_modifie_entre_temps')
    enabled = str(data.get('enabled') or '') in ('1', 'true', 'yes', 'on') or data.get('enabled') is True
    url = validate(desk.c, data.get('talk_url')) if (enabled or data.get('talk_url')) else current['talk_url']
    value = {'enabled': enabled, 'talk_url': url, 'version': current['version'] + 1, 'updated': desk.now()}
    desk.setting(KEY + str(owner or 'cabinet'), value)
    desk.audit('talk5643_reglage', {'owner': owner, 'enabled': enabled, 'version': value['version']})
    return {**value, 'message': 'Page Talk ' + ('affichée sous « Agenda et tâches ».' if enabled else 'masquée.') + ' Rechargez la page pour mettre le menu à jour.'}


def visible(cfg, owner):
    """Lecture fraîche, sans Desk, pour le menu (rendu de chaque page) ; une erreur de lecture masque simplement l'entrée."""
    path = Path(str((cfg or {}).get('state_dir') or '')) / 'desk.sqlite3'
    if not path.is_file():
        return False
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (KEY + str(owner or 'cabinet'),)).fetchone()
        finally:
            db.close()
        return bool(row and (json.loads(row[0]) or {}).get('enabled'))
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return False


def frame_origin(cfg, owner):
    """Origine autorisée dans ``frame-src`` pour /talk : celle de la connexion Nextcloud, seulement si la page est activée."""
    if not visible(cfg, owner):
        return ''
    origin, _ = _base(cfg)
    return origin


def page_html(desk, owner, prefix):
    value = get(desk, owner)
    settings = prefix + '/parametres?rubrique=connexions#talk5643'
    if not value['enabled']:
        return ('<section class="talk5643"><h2>Nextcloud Talk</h2><p>La page Talk n’est pas activée pour votre compte. Activez-la dans '
                '<a href="' + e(settings, quote=True) + '">Paramètres › Connexions › Nextcloud Talk</a>.</p></section>')
    try:
        url = validate(desk.c, value['talk_url'])
    except Stop:
        return ('<section class="talk5643"><h2>Nextcloud Talk</h2><p class="notice" role="alert">L’adresse de Talk enregistrée ne correspond plus à '
                'la connexion Nextcloud configurée. Corrigez-la dans <a href="' + e(settings, quote=True) + '">Paramètres › Connexions</a>.</p></section>')
    return ('<section class="talk5643" data-talk5643><div class="talk5643-bar">'
            '<a class="ax-btn" href="' + e(url, quote=True) + '" target="_blank" rel="noopener noreferrer">Ouvrir Talk dans un nouvel onglet</a>'
            '<button type="button" class="ax-btn ghost" data-talk5643-reload data-confirm="Recharger Talk ? Un appel en cours peut être interrompu.">Recharger</button>'
            '<a class="ax-btn ghost" href="' + e(settings, quote=True) + '">Réglages</a>'
            '<span class="talk5643-help">Si la fenêtre reste vide : connectez-vous à Nextcloud dans un onglet, puis rechargez ; si Nextcloud refuse '
            'l’intégration, utilisez « Ouvrir Talk dans un nouvel onglet ».</span></div>'
            '<iframe class="talk5643-frame" title="Nextcloud Talk" src="' + e(url, quote=True) + '" '
            'allow="microphone; camera; fullscreen; display-capture; autoplay" referrerpolicy="strict-origin-when-cross-origin"></iframe></section>')


def settings_html(desk, owner, prefix):
    value = get(desk, owner)
    origin, _ = _base(desk.c)
    if not origin:
        return ('<section id="talk5643"><h3>Nextcloud Talk</h3><p>Configurez d’abord la connexion Nextcloud (adresse HTTPS) pour proposer la '
                'page Talk.</p></section>')
    return ('<section id="talk5643"><h3>Nextcloud Talk</h3><p>Affiche votre application Nextcloud Talk (discussions et appels) dans AxiorHub, '
            'sous « Agenda et tâches ». Elle utilise votre session Nextcloud dans ce navigateur ; aucun salon n’est créé et l’IA n’intervient pas.</p>'
            '<form class="m5-form" data-api="m5636/talk/enregistrer" autocomplete="off">'
            '<input type="hidden" name="expected_version" value="' + str(value['version']) + '">'
            '<label class="m34-check"><input type="checkbox" name="enabled" value="1"' + (' checked' if value['enabled'] else '') + '> Afficher la page Talk</label>'
            '<label>Adresse de l’application Talk<input type="url" name="talk_url" value="' + e(value['talk_url'], quote=True) + '" '
            'placeholder="' + e(suggested(desk.c), quote=True) + '"></label>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>'
            '<form class="m5-form" data-api="m5636/talk/tester"><input type="hidden" name="talk_url" value="' + e(value['talk_url'], quote=True) + '">'
            '<button class="ax-btn ghost" type="submit">Tester l’affichage</button><p role="status" class="m5-status"></p></form></section>')


def axiorhub_origin(env):
    """Origine d'AxiorHub telle que la voit le navigateur : en-tête Origin de la demande (même origine), sinon schéma et hôte transmis
    par le serveur web frontal."""
    env = env or {}
    origin = str(env.get('HTTP_ORIGIN') or '').strip().rstrip('/')
    if re.fullmatch(r'https?://[A-Za-z0-9.\-\[\]:]+', origin):
        return origin.lower()
    scheme = str(env.get('HTTP_X_FORWARDED_PROTO') or env.get('wsgi.url_scheme') or 'https').split(',')[0].strip().lower()
    host = str(env.get('HTTP_X_FORWARDED_HOST') or env.get('HTTP_HOST') or '').split(',')[0].strip().lower()
    return (scheme + '://' + host) if scheme in ('http', 'https') and host else ''


def _norm(origin):
    p = urlsplit(origin)
    port = p.port or (443 if p.scheme == 'https' else 80)
    return p.scheme, (p.hostname or '').lower(), port


def _source_allows(source, origin, own_origin):
    """Une source de ``frame-ancestors`` (CSP niveau 3) autorise-t-elle ``origin`` ?"""
    s = source.strip()
    low = s.lower()
    if not s or not origin:
        return False
    if s == '*':
        return True
    if low == "'self'":
        return bool(own_origin) and _norm(origin) == _norm(own_origin)
    if low.startswith("'"):
        return False   # 'none' et autres mots-clés
    scheme, host, port = _norm(origin)
    if re.fullmatch(r'[a-z][a-z0-9+.-]*:', low):   # schéma seul (« https: »)
        return scheme == low[:-1]
    m = re.fullmatch(r'(?:([a-z][a-z0-9+.-]*)://)?(\*\.)?([a-z0-9.\-]+|\[[0-9a-f:]+\])(?::(\d+|\*))?(/.*)?', low)
    if not m:
        return False
    s_scheme, wildcard, s_host, s_port = m.group(1), m.group(2), m.group(3), m.group(4)
    if s_scheme and s_scheme != scheme and not (s_scheme == 'http' and scheme == 'https'):
        return False
    if wildcard:
        if not host.endswith('.' + s_host):
            return False
    elif host != s_host:
        return False
    if s_port == '*':
        return True
    expected = int(s_port) if s_port else (443 if (s_scheme or scheme) == 'https' else 80)
    return port == expected


def framing_verdict(header_items, own_origin, origin):
    """5.6.44 : règle des navigateurs. Les directives ``frame-ancestors`` de toutes les politiques reçues l'emportent sur
    X-Frame-Options (ignoré dès qu'une est présente) ; chacune doit autoriser ``origin``. Sans elles, X-Frame-Options s'applique."""
    policies, xfo = [], ''
    for name, value in header_items:
        key = str(name).lower()
        if key == 'content-security-policy':
            policies.extend(v for v in str(value).split(',') if v.strip())
        elif key == 'x-frame-options' and not xfo:
            xfo = str(value).strip()
    ancestors = []
    for policy in policies:
        directive = next((d.strip() for d in policy.split(';') if d.strip().lower().startswith('frame-ancestors')), '')
        if directive:
            ancestors.append(directive)
    if ancestors:
        refused = [d for d in ancestors if not any(_source_allows(src, origin, own_origin) for src in d.split()[1:])]
        return {'blocked': bool(refused), 'rule': 'frame-ancestors', 'x_frame_options': xfo, 'frame_ancestors': ' | '.join(ancestors),
                'refusing': ' | '.join(refused), 'xfo_ignored': bool(xfo)}
    value = xfo.upper()
    same = bool(origin and own_origin) and _norm(origin) == _norm(own_origin)
    blocked = value.startswith('DENY') or (value.startswith('SAMEORIGIN') and not same)
    return {'blocked': blocked, 'rule': 'x-frame-options' if xfo else 'aucune', 'x_frame_options': xfo, 'frame_ancestors': '',
            'refusing': xfo if blocked else '', 'xfo_ignored': False}


def test(desk, data, env=None):
    """Diagnostic de l'intégration : en-têtes de la page Talk vus par le serveur (sans identifiant). Ne crée ni message ni salon."""
    url = validate(desk.c, data.get('talk_url'))
    import http.client
    import ssl
    parts = urlsplit(url)
    items, status = [], ''
    try:   # première réponse seulement (souvent une redirection vers la connexion) : ses en-têtes de sécurité suffisent au diagnostic
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=15, context=ssl.create_default_context())
        try:
            conn.request('GET', parts.path + (('?' + parts.query) if parts.query else ''), headers={'User-Agent': 'AxiorHub-Pilote'})
            response = conn.getresponse()
            items = list(response.getheaders())
            status = 'joignable' if response.status < 500 else 'http_%d' % response.status
        finally:
            conn.close()
    except (OSError, http.client.HTTPException, ssl.SSLError):
        status = 'connexion_impossible'
    origin = axiorhub_origin(env)
    own = '%s://%s' % (parts.scheme, parts.netloc)
    verdict = framing_verdict(items, own, origin)
    if status != 'joignable':
        message = 'Nextcloud n’a pas répondu (%s) : vérifiez l’adresse.' % status
    elif verdict['blocked'] and verdict['rule'] == 'frame-ancestors':
        message = ('La politique de sécurité de Nextcloud (%s) n’autorise pas l’affichage dans AxiorHub (%s). Ajoutez cette adresse à '
                   'frame-ancestors côté Nextcloud, ou utilisez « Ouvrir Talk dans un nouvel onglet ».' % (verdict['refusing'], origin or 'origine inconnue'))
    elif verdict['blocked']:
        message = ('Nextcloud refuse d’être affiché dans une page d’une autre origine (X-Frame-Options: %s). Utilisez « Ouvrir Talk dans un '
                   'nouvel onglet », ou faites autoriser l’origine d’AxiorHub (%s) par une directive frame-ancestors côté Nextcloud. En mode '
                   'application Nextcloud (même origine), l’intégration est possible.' % (verdict['x_frame_options'], origin or 'origine inconnue'))
    elif verdict['rule'] == 'frame-ancestors':
        message = ('Intégration autorisée : frame-ancestors accepte %s.%s Le résultat dépend aussi de votre session Nextcloud dans ce navigateur.'
                   % (origin, (' L’en-tête X-Frame-Options: %s est ignoré par les navigateurs quand frame-ancestors est présent.'
                               % verdict['x_frame_options']) if verdict['xfo_ignored'] else ''))
    else:
        message = 'Aucune interdiction d’intégration détectée. Le résultat dépend aussi de votre session et de votre navigateur.'
    return {'status': status, 'x_frame_options': verdict['x_frame_options'], 'frame_ancestors': verdict['frame_ancestors'],
            'blocked': bool(verdict['blocked']) and status == 'joignable', 'rule': verdict['rule'], 'origin': origin, 'message': message}

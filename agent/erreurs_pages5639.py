"""5.6.39 : pages d'erreur lisibles (400 « Action non effectuée », 401, 404, 405, 500, 503) — logo, explication, et toujours les mêmes
boutons : « Recharger la page », « Revenir en arrière », « Accueil ».

Les pages n'ont ni script ni style en ligne (politique de sécurité du contenu : style-src 'self') : la feuille ``erreurs5639.css`` est
servie sans connexion (aucune donnée du cabinet). « Revenir en arrière » vise la page d'origine (en-tête Referer du même site),
sinon l'accueil ; « Recharger » refait la même adresse (GET) ou rouvre la page du formulaire (POST, jamais de renvoi du formulaire).
Une erreur inattendue sur une page (GET) est réessayée une seule fois automatiquement ; la base occupée est réessayée jusqu'à
disponibilité. Les textes sont traduits à la sortie (anglais, allemand, espagnol) comme les autres écrans.
"""
from html import escape as e
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

CSS_PATH = '/static/erreurs5639.css'
RETRY_FLAG = '_reessai'
BUSY_FLAG = '_occupe'
BUSY_DELAYS = (3, 5, 8, 13)   # 5.6.40 (E03) : essais automatiques espacés, puis page stable avec « Réessayer »
TONES = {'info': 'ℹ️', 'warn': '⚠️', 'error': '⛔', 'busy': '⏳', 'lost': '🧭', 'lock': '🔒'}


def css():
    return (Path(__file__).parent / 'static' / 'erreurs5639.css').read_text(encoding='utf-8')


def guess_prefix(env):
    path = str((env or {}).get('PATH_INFO', ''))
    return '/agent-courriel' if path == '/agent-courriel' or path.startswith('/agent-courriel/') else ''


def current_url(env):
    path = (env.get('SCRIPT_NAME', '') or '') + (env.get('PATH_INFO', '/') or '/')
    query = env.get('QUERY_STRING', '')
    return path + ('?' + query if query else '')


def referer(env):
    """Page d'origine, seulement si elle appartient au même site (jamais une adresse extérieure)."""
    ref = urlsplit(str(env.get('HTTP_REFERER', '') or ''))
    host = str(env.get('HTTP_X_FORWARDED_HOST') or env.get('HTTP_HOST', '') or '').split(',')[0].strip()
    if ref.netloc and ref.netloc == host and ref.path.startswith('/'):
        return ref.path + ('?' + ref.query if ref.query else '')
    return ''


def with_retry_flag(url, flag=RETRY_FLAG, value='1'):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != flag] + [(flag, str(value))]
    return parts.path + '?' + urlencode(query)


def busy_attempt(env):
    for k, v in parse_qsl(str((env or {}).get('QUERY_STRING', '') or ''), keep_blank_values=True):
        if k == BUSY_FLAG:
            try:
                return max(0, min(int(v), 99))
            except ValueError:
                return 0
    return 0


def incident_reference():
    """Référence d'incident : heure UTC à la seconde, celle des journaux (« erreurs-interface.log », écritures longues)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')


def already_retried(env):
    return any(k == RETRY_FLAG for k, _ in parse_qsl(str(env.get('QUERY_STRING', '') or ''), keep_blank_values=True))


def page(env, status, title, message, prefix=None, detail='', tone='warn', retry_seconds=0, retry_url='', extra_html='', reference='',
         reload_label=''):
    """Page complète, autonome. ``extra_html`` est déjà échappé par l'appelant."""
    env = env or {}
    prefix = guess_prefix(env) if prefix is None else prefix
    home = (prefix or '') + '/'
    post = env.get('REQUEST_METHOD', 'GET') not in ('GET', 'HEAD')
    back = referer(env)
    here = current_url(env)
    reload_url = (back or home) if post else here
    refresh = ('<meta http-equiv="refresh" content="%d;url=%s">' % (retry_seconds, e(retry_url or reload_url, quote=True))) if retry_seconds else ''
    code = status.split(' ', 1)[0]
    if retry_url and not retry_seconds:   # page stable après les essais automatiques : « Réessayer » repart de zéro
        reload_url = retry_url
    buttons = ['<a class="axe-btn axe-primary" href="%s">↻ %s</a>' % (e(reload_url, quote=True), reload_label or ('Recharger la page' if not post else
                                                                                                                  'Rouvrir la page précédente'))]
    if back and back != reload_url:
        buttons.append('<a class="axe-btn" href="%s">← Revenir en arrière</a>' % e(back, quote=True))
    elif not back and not post:
        buttons.append('<a class="axe-btn" href="%s">← Revenir en arrière</a>' % e(home, quote=True))
    buttons.append('<a class="axe-btn axe-ghost" href="%s">⌂ Accueil</a>' % e(home, quote=True))
    lang = 'fr'
    try:
        from .i18n5631 import current
        lang = current() or 'fr'
    except Exception:   # noqa: BLE001
        pass
    return ('<!doctype html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">%s'
            '<title>%s · AxiorHub Pilote</title><link rel="stylesheet" href="%s"></head><body class="axe-body">'
            '<main class="axe-card axe-%s" role="%s"><div class="axe-brand"><img src="%s" width="40" height="40" alt=""><span>AxiorHub</span></div>'
            '<div class="axe-icon" aria-hidden="true">%s</div><h1>%s</h1><p class="axe-msg">%s</p>%s%s%s'
            '<nav class="axe-actions" aria-label="Que faire maintenant ?">%s</nav><p class="axe-code">%s</p></main></body></html>') % (
        e(lang, quote=True), refresh, e(title), e(prefix + CSS_PATH, quote=True), e(tone, quote=True), 'status' if tone == 'busy' else 'alert',
        e(prefix + '/static/axiorhub-icon-192.png', quote=True), TONES.get(tone, TONES['warn']), e(title), e(message),
        ('<p class="axe-detail">%s</p>' % e(detail)) if detail else '', extra_html,
        ('<div class="axe-spinner" aria-hidden="true"></div><p class="axe-auto">Nouvel essai automatique dans quelques secondes.</p>'
         if retry_seconds else ''), ''.join(buttons),
        e(('Code %s' % code) + (' · référence %s' % reference if reference else '')))


# ------------------------------------------------------------------------------------------------ pages types
NOT_FOUND = ('page_absente', 'route_inconnue', 'page_inconnue')


def refused(env, reason, message, precision='', prefix=None, extra_html=''):
    """Refus métier (Stop) sur une page : 404 pour une adresse inconnue, sinon 400 « Action non effectuée »."""
    if reason in NOT_FOUND and not str((env or {}).get('PATH_INFO', '')).endswith('/action'):   # /action par GET : refus d'action (400)
        return '404 Not Found', page(env, '404 Not Found', 'Page introuvable', 'Cette adresse ne correspond à aucune page d’AxiorHub : le lien est '
                                     'peut-être ancien ou incomplet.', prefix, tone='lost')
    post = (env or {}).get('REQUEST_METHOD', 'GET') not in ('GET', 'HEAD')
    hint = ('Rien n’a été modifié. Rouvrez la page précédente, vérifiez les informations puis relancez le bouton.' if post else
            'Rechargez la page ou revenez en arrière ; si le message persiste, l’explication ci-dessus indique quoi corriger.')
    return '400 Bad Request', page(env, '400 Bad Request', 'Action non effectuée', message, prefix, detail=precision, tone='warn',
                                   extra_html=extra_html + '<p class="axe-hint">%s</p>' % e(hint))


def _diagnostic(prefix):
    return '<p class="axe-hint">Si cela se répète : <a href="%s">ouvrir le diagnostic</a> et communiquer la référence ci-dessous.</p>' % e(
        (prefix or '') + '/diagnostic', quote=True)


def internal(env, prefix=None, reference=''):
    """Erreur inattendue : une page (GET) est réessayée une fois automatiquement ; ensuite, explication, référence et boutons."""
    env = env or {}
    prefix = guess_prefix(env) if prefix is None else prefix
    get = env.get('REQUEST_METHOD', 'GET') in ('GET', 'HEAD')
    retry = get and not already_retried(env)
    return page(env, '500 Internal Server Error', 'Interface indisponible',
                'Une erreur inattendue a interrompu l’affichage. Rechargez la page ; si l’erreur revient, vérifiez les services et les fichiers '
                'de configuration locaux. Le détail est consigné dans « erreurs-interface.log ».', prefix,
                tone='error', retry_seconds=5 if retry else 0, retry_url=with_retry_flag(current_url(env)) if retry else '',
                reference=reference or incident_reference(), extra_html='' if retry else _diagnostic(prefix))


def busy(env, prefix=None):
    """Base momentanément occupée par une écriture. 5.6.40 (audit 5.6.39, E03) : une page (GET) est réessayée automatiquement à
    intervalles croissants (3, 5, 8, 13 s) puis s'arrête sur une page stable avec « Réessayer », une référence et le diagnostic ; après
    un clic (POST), la page précédente est rouverte une fois (jamais de nouvel envoi automatique du formulaire), sans promettre que les
    saisies sont conservées."""
    env = env or {}
    prefix = guess_prefix(env) if prefix is None else prefix
    post = env.get('REQUEST_METHOD', 'GET') not in ('GET', 'HEAD')
    if post:
        back = referer(env)
        text = ('Votre clic n’a pas pu être enregistré, car AxiorHub termine une autre opération : rien n’a été modifié. La page précédente '
                'se rouvre ; les informations saisies dans le formulaire peuvent devoir être saisies de nouveau, puis cliquez de nouveau sur le '
                'bouton.')
        return page(env, '503 Service Unavailable', 'Un instant…', text, prefix, tone='busy', retry_seconds=4 if back else 0, retry_url=back,
                    reference=incident_reference())
    attempt = busy_attempt(env)
    here = current_url(env)
    if attempt < len(BUSY_DELAYS):
        return page(env, '503 Service Unavailable', 'Un instant…', 'AxiorHub termine une autre opération. La page s’affiche dans un instant, '
                    'sans rien à faire (essai %d sur %d).' % (attempt + 1, len(BUSY_DELAYS)), prefix, tone='busy',
                    retry_seconds=BUSY_DELAYS[attempt], retry_url=with_retry_flag(here, BUSY_FLAG, attempt + 1))
    return page(env, '503 Service Unavailable', 'AxiorHub reste occupé',
                'Une opération longue occupe encore la base après plusieurs essais. Patientez une minute puis cliquez « Réessayer » ; rien n’a '
                'été modifié par cette page.', prefix, tone='warn', retry_url=with_retry_flag(here, BUSY_FLAG, 0),
                reference=incident_reference(), extra_html=_diagnostic(prefix), reload_label='Réessayer')


def unauthorized(env, prefix=None):
    return page(env, '401 Unauthorized', 'Connexion nécessaire', 'Connectez-vous avec le compte défini à l’installation : rechargez la page '
                'pour afficher de nouveau la fenêtre de connexion.', prefix, tone='lock')


def method_refused(env, prefix=None):
    return page(env, '405 Method Not Allowed', 'Action non effectuée', 'Méthode refusée : cette adresse ne s’utilise pas ainsi. Rouvrez la page '
                'depuis le menu.', prefix, tone='warn')


# ------------------------------------------------------------------------------------------------ Apache (moteur arrêté ou redémarré)
def apache_error_document():
    """Page affichée par Apache quand le moteur ne répond pas (502, 503 : redémarrage après une mise à jour). Texte littéral d'une seule
    ligne, en ASCII (entités HTML), sans « % » ni guillemet double : il est copié tel quel dans la directive ErrorDocument. Elle se
    recharge seule toutes les 5 secondes."""
    return ("<!doctype html><html lang='fr'><head><meta charset='utf-8'><meta http-equiv='refresh' content='5'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><title>AxiorHub red&eacute;marre</title></head>"
            "<body style='margin:0;background:#f4f7fb;font-family:system-ui,Segoe UI,sans-serif;color:#172033'>"
            "<main style='max-width:460px;margin:12vh auto;padding:32px 28px;background:#fff;border-radius:20px;"
            "box-shadow:0 18px 50px rgba(15,23,42,.12);text-align:center'>"
            "<div style='width:56px;height:56px;margin:0 auto 16px;border-radius:16px;background:#2563eb;color:#fff;font:700 30px/56px system-ui'>A</div>"
            "<h1 style='font-size:1.3rem;margin:0 0 10px'>AxiorHub red&eacute;marre</h1>"
            "<p style='color:#475569;line-height:1.5'>Le service termine son d&eacute;marrage (mise &agrave; jour ou red&eacute;marrage du serveur). "
            "Cette page se recharge toute seule dans quelques secondes ; rien n&rsquo;a &eacute;t&eacute; perdu.</p>"
            "<p style='margin-top:22px'><a href='' style='display:inline-block;padding:10px 18px;border-radius:12px;background:#2563eb;color:#fff;"
            "text-decoration:none;font-weight:600'>&#8635; Recharger la page</a> "
            "<a href='/agent-courriel/' style='display:inline-block;padding:10px 18px;border-radius:12px;border:1px solid #cbd5e1;color:#1e3a8a;"
            "text-decoration:none;font-weight:600'>&#8962; Accueil</a></p></main></body></html>")


def apache_directives(indent='    '):
    page_ = apache_error_document()
    assert '"' not in page_ and '%' not in page_ and '\n' not in page_ and page_.isascii()
    return ''.join('%sErrorDocument %d "%s"\n' % (indent, code, page_) for code in (502, 503, 504))

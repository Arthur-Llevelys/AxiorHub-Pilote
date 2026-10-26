"""5.6.72 : alerte « modèle local d'IA injoignable » et nouvelles tentatives après une coupure (incident du cabinet du 22 octobre 2026).

Constat : le moteur Ollama (sur un autre poste du réseau du cabinet) s'est arrêté un soir ; pendant plus de 12 heures, aucun courriel
n'a été trié ni rédigé, sans rien sur « Aujourd'hui » pour le signaler. Les réponses « sans dossier » tentées pendant la coupure ont
consommé leur unique tentative automatique.

- Alerte : quand des travaux échouent depuis plus de 10 minutes parce que le moteur local ne répond pas (« moteur_ollama_injoignable »,
  « modele_ollama_absent ») et qu'aucun appel local n'a réussi depuis, un bandeau sur « Aujourd'hui » le dit, avec l'heure de la
  première panne, l'heure du dernier appel réussi, l'adresse du moteur et un bouton « Tester maintenant ». Il disparaît dès qu'un appel
  local (ou le test) réussit.
- Nouvelles tentatives : un échec dû à l'IA injoignable, en pause ou surchargée n'est plus une tentative « consommée » ; la réponse
  est retentée quand l'IA répond de nouveau (3 essais au plus).
"""
from datetime import datetime, timedelta, timezone
from html import escape as e

LOCAL_DOWN = ('moteur_ollama_injoignable', 'modele_ollama_absent')
TRANSIENT = frozenset(LOCAL_DOWN + ('fournisseur_ia_injoignable', 'generation_ia_delai_depasse', 'connexion_http_indisponible', 'delai_http_depasse',
                                    'ia_en_pause', 'preparation_suspendue', 'file_attente_pleine'))
DELAY_MINUTES = 10
PROBE_KEY = 'ia5672:test_reussi'


def _url(desk):
    return str((desk.c.get('ollama') or {}).get('url') or '').rstrip('/')


def _parse(value):
    try:
        when = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def last_ok(desk):
    """Dernier appel local réussi (trace des appels) ou dernier test réussi."""
    found = ''
    try:
        row = desk.db.execute("SELECT MAX(at) FROM trace_calls480 WHERE external=0 AND status='ok'").fetchone()
        found = str(row[0] or '') if row else ''
    except Exception:   # noqa: BLE001 — table absente (installation neuve)
        found = ''
    probe = str(desk.settings(PROBE_KEY, '') or '')
    return max(found, probe)


def local_down(desk, now=None):
    """{'down': bool, 'since', 'last_ok', 'errors', 'url'} : moteur local en panne depuis plus de 10 minutes ?"""
    now = now or datetime.now(timezone.utc)
    ok = last_ok(desk)
    since = max(ok, (now - timedelta(days=1)).isoformat())
    marks = ','.join('?' * len(LOCAL_DOWN))
    try:
        rows = desk.db.execute("SELECT finished FROM jobs WHERE status='error' AND finished>? AND json_extract(result,'$.erreur') IN (%s) "
                               "ORDER BY finished" % marks, (since,) + LOCAL_DOWN).fetchall()
    except Exception:   # noqa: BLE001
        rows = []
    if not rows:
        return {'down': False, 'last_ok': ok, 'url': _url(desk)}
    first = _parse(rows[0][0])
    if first is None or now - first < timedelta(minutes=DELAY_MINUTES):
        return {'down': False, 'last_ok': ok, 'url': _url(desk)}
    return {'down': True, 'since': rows[0][0], 'last_ok': ok, 'errors': len(rows), 'url': _url(desk)}


def probe(desk):
    """Test immédiat du moteur local (lecture de sa version) ; un succès lève l'alerte."""
    from .common import HTTP, Stop
    cfg = desk.c.get('ollama') or {}
    url = _url(desk)
    if not url:
        raise Stop('moteur_ollama_non_configure')
    try:
        data = HTTP(url, local_only=True, timeout=10, local_hosts=cfg.get('local_hosts', ())).json('GET', '/api/version')
    except Stop as ex:
        desk.audit('ia5672_test', {'ok': False, 'code': str(ex)[:80]})
        return {'ok': False, 'message': 'Le modèle local ne répond toujours pas (%s). Vérifiez que la machine est allumée et qu’Ollama est démarré '
                                       'et accessible depuis le réseau.' % url}
    desk.setting(PROBE_KEY, datetime.now(timezone.utc).isoformat())
    desk.audit('ia5672_test', {'ok': True})
    version = str((data or {}).get('version') or '') if isinstance(data, dict) else ''
    return {'ok': True, 'message': 'Le modèle local répond%s : le tri et la rédaction reprennent.' % (' (version %s)' % version if version else '')}


def _local(desk, value):
    when = _parse(value)
    if when is None:
        return 'heure inconnue'
    try:
        from zoneinfo import ZoneInfo
        when = when.astimezone(ZoneInfo(str((desk.c.get('proactive') or {}).get('timezone') or 'Europe/Paris')))
    except Exception:   # noqa: BLE001 — fuseau absent : heure UTC
        return when.strftime('%d/%m à %H:%M UTC')
    return when.strftime('%d/%m à %H:%M')


def banner_html(desk, prefix):
    try:
        st = local_down(desk)
    except Exception:   # noqa: BLE001 — la page s'affiche toujours
        return ''
    if not st['down']:
        return ''
    last = ('Dernier appel réussi : %s. ' % e(_local(desk, st['last_ok']))) if st.get('last_ok') else ''
    where = ('Adresse : <code>%s</code>.' % e(st['url'])) if st.get('url') else ''
    return ('<div class="b5668-banner b5668-full ia5672-banner" role="alert"><p><strong>Le modèle local d’IA ne répond plus depuis le %s.</strong> '
            'Tant qu’il est injoignable, aucun courriel n’est trié ni rédigé. %s%s</p>'
            '<p>Vérifiez que la machine qui l’héberge est allumée et sans mise en veille, qu’Ollama est démarré, et qu’il accepte les connexions '
            'du réseau (réglage <code>OLLAMA_HOST=0.0.0.0</code>).</p>'
            '<p class="b5668-actions"><form class="q51-inline" data-c5671="m5672/ia/tester" data-reload><button class="ax-btn" type="submit">'
            'Tester maintenant</button></form><a class="ax-btn ghost" href="%s">Paramètres de l’IA</a></p></div>') % (
        e(_local(desk, st['since'])), last, where, e(prefix + '/parametres?tab=ia', quote=True))


def api(env, desk, auth, prefix, name, args, method):
    from .common import Stop
    from .web440 import json_out
    route = name[len('m5672/'):].split('?', 1)[0]
    if route != 'ia/tester':
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    from .web5634 import _post
    _post(env, auth, 2_000)
    return json_out(probe(desk))

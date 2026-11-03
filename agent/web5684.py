"""5.6.84 : routes ``/api440/m5684/*`` (cessions, modèles, panneau de l'éditeur) et ``/office/bridge/*``, ``/office/plugin/*`` (plugin EuroOffice).

Le plugin s'adresse à AxiorHub avec un jeton court, limité à une session et un document ; il ne reçoit jamais le secret du serveur bureautique ni une clé de modèle. Il est servi par AxiorHub : ses appels restent de même origine,
et son cadre ne peut être affiché que par le serveur bureautique configuré."""
from html import escape as e
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .common import Stop

PLUGIN = Path(__file__).parent / 'office5684'
FILES = {'config.json': 'application/json', 'core.js': 'text/javascript', 'plugin.js': 'text/javascript', 'icon.png': 'image/png'}
SOURCES = {'config.json': 'manifeste.json'}      # l'éditeur exige ce nom d'adresse ; un fichier config.json du dépôt serait traité comme un réglage privé
STATUS = {'jeton_invalide': '401 Unauthorized', 'jeton_expire_ou_incorrect': '401 Unauthorized', 'session_plugin_inconnue': '401 Unauthorized', 'session_plugin_fermee': '401 Unauthorized', 'generation_terminee': '409 Conflict',
          'executeur_perime': '409 Conflict', 'commande_non_disponible': '409 Conflict', 'proposition_perimee': '409 Conflict', 'dossier_reserve': '403 Forbidden', 'session_sans_ecriture': '403 Forbidden', 'observation_autre_document': '403 Forbidden',
          'observation_invalide': '422 Unprocessable Entity', 'empreinte_bloc_incoherente': '422 Unprocessable Entity', 'accuse_invalide': '422 Unprocessable Entity', 'observation_trop_volumineuse': '429 Too Many Requests'}
LAWYER = ('cession/realiser', 'modele/version', 'modele/fiche', 'office/reglage', 'regle')


def _origin(url):
    parts = urlsplit(str(url or ''))
    return '%s://%s' % (parts.scheme, parts.netloc) if parts.scheme and parts.netloc else ''


def plugin_file(desk, name, prefix):
    from . import office440, office_bridge5684 as bridge
    if name == 'index.html':
        origin = _origin(office440.settings(desk)['server_url'])
        html = ('<!doctype html><html data-editor-origin="%s" data-bridge="%s/office/bridge" data-guid="%s"><meta charset="utf-8"><title>AxiorHub</title><script src="core.js"></script><script src="plugin.js"></script><body></body></html>' % (
            e(origin, quote=True), e(prefix, quote=True), e(bridge.GUID, quote=True)))
        # 'unsafe-eval' : le moteur du plugin est fourni par l'éditeur, seule origine acceptée par le chargeur
        return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': html.encode(), 'csp': "default-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'self' %s" % origin}
    if name not in FILES:
        raise Stop('fichier_plugin_absent')
    origin = _origin(office440.settings(desk)['server_url'])    # l'éditeur lit config.json depuis sa propre origine : elle seule est autorisée
    return {'status': '200 OK', 'kind': FILES[name], 'body': (PLUGIN / SOURCES.get(name, name)).read_bytes(), 'headers': [('Cache-Control', 'no-cache')] + ([('Access-Control-Allow-Origin', origin), ('Vary', 'Origin')] if origin else [])}


def bridge_route(desk, env, path, method, body):
    """Chaque appel revérifie le jeton et les droits ; aucune route ne fait confiance à l'identité annoncée par le navigateur."""
    from . import office_bridge5684 as bridge
    from .web440 import json_out
    token = str(env.get('HTTP_AUTHORIZATION') or '').removeprefix('Bearer ').strip()
    if not token:
        raise Stop('jeton_invalide')
    session = bridge.authenticate(desk, token)
    route = path[len('/office/bridge/'):]
    desk.db.commit()
    if method == 'GET' and route == 'events':
        return json_out(bridge.events(desk, session, _query(env).get('after')))
    if method != 'POST':
        raise Stop('methode_refusee')
    match = re.fullmatch(r'commands/([a-f0-9]{32})/(claim|ack)', route)
    if match:
        if match[2] == 'claim':
            return json_out(bridge.claim(desk, session, match[1], int(body.get('epoch') or 0)))
        return json_out(bridge.acknowledge(desk, session, match[1], body))
    routes = {'observations': lambda: bridge.observe(desk, session, body), 'analyse': lambda: bridge.request_analysis(desk, session), 'lease': lambda: bridge.lease(desk, session), 'renew': lambda: bridge.renew(desk, session),
              'close': lambda: bridge.close_session(desk, session), 'data-proposals': lambda: bridge.data_proposal(desk, session, body)}
    if route not in routes:
        raise Stop('operation_absente')
    return json_out(routes[route]())


def _query(env):
    from urllib.parse import parse_qs
    return {k: v[0] for k, v in parse_qs(env.get('QUERY_STRING', '')).items()}


def public(env, desk, path, method, prefix):
    """Routes sans session navigateur : fichiers du plugin et pont à jeton court."""
    from .web440 import json_out
    if path.startswith('/office/plugin/'):
        return plugin_file(desk, path[len('/office/plugin/'):], prefix)
    length = int(env.get('CONTENT_LENGTH') or 0)
    if length > 2_000_000:
        return json_out({'error': 'observation_trop_volumineuse'}, '429 Too Many Requests')
    body = {}
    if length:
        try:
            body = json.loads(env['wsgi.input'].read(length).decode('utf-8'))
        except (ValueError, UnicodeError):
            return json_out({'error': 'observation_invalide'}, '422 Unprocessable Entity')
    try:
        return bridge_route(desk, env, path, method, body)
    except Stop as ex:
        return json_out({'error': str(ex)}, STATUS.get(str(ex), '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    from .membres5650 import hidden_for
    from . import cessions5684, modeles5684, office_bridge5684 as bridge, office_gen5684, regles5684  # noqa: F401
    owner, role = actor(env)
    route = name[len('m5684/'):].split('?', 1)[0]
    if method == 'GET':
        if route == 'office/panel':
            return json_out(bridge.panel(desk, _document(desk, args.get('document'), owner, role), owner))
        raise Stop('operation_absente')
    if method != 'POST':
        raise Stop('methode_refusee')
    if role == 'assistant' and route.startswith(LAWYER):
        raise Stop('role_insuffisant')
    data = _post(env, auth, 400_000)
    if route.startswith('cession/'):
        if data.get('id'):
            dos = cessions5684.load(desk, data['id'])
            if dos['matter'] in hidden_for(desk.c['state_dir'], owner, role):
                raise Stop('dossier_reserve')
        elif data.get('matter') and str(data['matter']) in hidden_for(desk.c['state_dir'], owner, role):
            raise Stop('dossier_reserve')
        return json_out(cessions5684.api_post(desk, route[len('cession/'):], data, owner, role))
    if route == 'modele/version':
        return json_out(modeles5684.publish(desk, data, owner))
    if route == 'modele/etat':
        return json_out(modeles5684.move(desk, data, owner, role))
    if route == 'modele/fiche':
        return json_out(modeles5684.renseigner(desk, data, owner, role))
    if route == 'regle':
        from . import regles5683
        return json_out(regles5683.validate(desk, str(data.get('regle') or ''), owner, data.get('note')))
    if route == 'office/reglage':
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        return json_out(bridge.set_flags(desk, data, owner))
    document = _document(desk, data.get('document'), owner, role)
    active = office_gen5684.active(desk, document)
    if route == 'office/decision':
        return json_out(bridge.decide(desk, document, data.get('commande'), str(data.get('decision') or ''), owner, role))
    if route == 'office/retour':
        return json_out(bridge.invert(desk, document, data.get('commande'), owner))
    if route == 'office/mode':
        return json_out(bridge.set_mode(desk, document, owner, str(data.get('mode') or '')))
    if not active:
        raise Stop('generation_terminee')
    if route == 'office/pause':
        return json_out(bridge.pause(desk, active['generation_id'], str(data.get('on') or '') in ('1', 'on', 'true', 'True')))
    if route == 'office/annuler':
        return json_out(bridge.cancel(desk, active['generation_id']))
    if route == 'office/reprendre':
        return json_out(bridge.resume_after_cancel(desk, active['generation_id']))
    raise Stop('operation_absente')


def _document(desk, document_id, owner, role):
    """Le document désigné doit exister et son dossier être accessible à cette personne, avant toute lecture."""
    from . import office_bridge5684 as bridge
    from .membres5650 import hidden_for
    bridge.schema(desk)
    row = desk.db.execute('SELECT document_id, matter FROM office_documents5684 WHERE document_id=?', (str(document_id or ''),)).fetchone()
    if not row:
        raise Stop('document_inconnu')
    if row['matter'] and row['matter'] in hidden_for(desk.c['state_dir'], owner, role):
        raise Stop('dossier_reserve')
    return row['document_id']


def cessions_page(desk, prefix, args, env):
    from .membres5650 import hidden_for
    from .web567 import actor
    from . import cessions5684
    owner, role = actor(env)
    return cessions5684.page_html(desk, prefix, args, hidden_for(desk.c['state_dir'], owner, role))

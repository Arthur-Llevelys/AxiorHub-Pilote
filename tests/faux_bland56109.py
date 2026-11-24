"""5.6.109 (lot B) : FAUX serveur MCP « Bland » sur 127.0.0.1, pour les essais seulement.

Transport Streamable HTTP sans session (JSON-RPC 2.0 en POST) : ``initialize``, ``notifications/initialized``, ``tools/list`` (outils
annotés lecture seule / destructif, un outil sans annotation, un outil inconnu), ``tools/call`` (réponses fictives, appels notés), et les
pannes demandées par l'essai : 401 (clé refusée), 429 (quota), coupure (connexion fermée sans réponse : « réponse perdue »).

Aucun appel vers bland.ai : ce serveur ne connaît que des données fictives (téléphones au format 01 00 00 00 05, cabinet DURAND).
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

CLE_VALIDE = 'org_fictive_cle_essai_0001'


def _outil(nom, description, lecture=None, destructif=None, schema=None):
    t = {'name': nom, 'description': description,
         'inputSchema': schema or {'type': 'object', 'properties': {}, 'required': []}}
    annotations = {}
    if lecture is not None:
        annotations['readOnlyHint'] = lecture
    if destructif is not None:
        annotations['destructiveHint'] = destructif
    if annotations:
        t['annotations'] = annotations
    return t


def _p(**props):
    return {'type': 'object', 'properties': {k: {'type': v} for k, v in props.items()}, 'required': list(props)[:1]}


OUTILS = [
    _outil('list_calls', 'List recent calls', True, False, _p(limit='integer')),
    _outil('get_call_log', 'Get transcript and summary of a call', True, False, _p(call_id='string')),
    _outil('list_voices', 'List curated voices', True, False),
    _outil('list_phone_numbers', 'List the numbers of the organization', True, False),
    _outil('search_bland_docs', 'Search the Bland documentation', True, False, _p(query='string')),
    _outil('get_agent', 'Read a Bland agent', True, False, _p(agent_id='string')),
    _outil('create_call', 'Place an outbound phone call', False, True, _p(phone_number='string', task='string', voice='string')),
    _outil('send_sms', 'Send a text message', False, True, _p(phone_number='string', message='string')),
    _outil('buy_credits', 'Buy credits for the organization', False, True, _p(amount='number', currency='string')),
    _outil('create_agent', 'Create a Bland agent (draft)', False, False,
           _p(name='string', prompt='string', voice='string', first_sentence='string', record='boolean')),
    _outil('publish_agent', 'Promote an agent to production', False, True, _p(agent_id='string')),
    _outil('delete_agent', 'Delete an agent', False, True, _p(agent_id='string')),
    _outil('list_agents', 'List agents', True, False),
    # « lecture seule » annoncée, mais le nom dit un effet : classé « effet » (motif) — jamais une lecture
    _outil('send_batch_campaign', 'Start a campaign', True, False, _p(numbers='array', task='string')),
    # sans aucune annotation : traité comme engageant
    _outil('mystery_tool', 'Undocumented tool'),
]


class FauxBland:
    """Serveur local : ``url`` à donner au client ; ``appels`` : (méthode, nom d'outil, arguments, en-tête Authorization présent)."""

    def __init__(self, outils=None):
        self.outils = list(outils or OUTILS)
        self.appels = []
        self.panne = None          # None | 'http_401' | 'http_429' | 'coupure' | 'erreur_outil'
        self.panne_outil = None    # même chose, seulement pour tools/call
        self.reponses = {}         # nom d'outil → structuredContent fictif
        self.agents_supprimes = set()
        faux = self

        class Gestion(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                corps = self.rfile.read(n)
                auth = self.headers.get('Authorization', '')
                try:
                    msg = json.loads(corps or b'{}')
                except ValueError:
                    msg = {}
                methode = msg.get('method', '')
                params = msg.get('params') or {}
                faux.appels.append((methode, params.get('name', ''), params.get('arguments'), bool(auth)))
                panne = faux.panne or (faux.panne_outil if methode == 'tools/call' else None)
                if panne == 'coupure':
                    self.close_connection = True
                    try:
                        self.wfile.flush()
                        self.connection.shutdown(2)
                    except OSError:
                        pass
                    return
                if auth != 'Bearer ' + CLE_VALIDE or panne == 'http_401':
                    self._json(401, {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32001, 'message': 'invalid_token'}})
                    return
                if panne == 'http_429':
                    self._json(429, {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32002, 'message': 'rate limited'}})
                    return
                if 'id' not in msg:            # notification
                    self.send_response(202)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                if methode == 'initialize':
                    res = {'protocolVersion': '2025-06-18', 'serverInfo': {'name': 'faux-bland', 'version': '9.9.99'}, 'capabilities': {'tools': {}}}
                elif methode == 'tools/list':
                    res = {'tools': faux.outils}
                elif methode == 'tools/call':
                    if panne == 'erreur_outil':
                        res = {'content': [{'type': 'text', 'text': 'refused'}], 'isError': True}
                    else:
                        res = faux._appel(params.get('name', ''), params.get('arguments') or {})
                else:
                    self._json(200, {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32601, 'message': 'unknown method'}})
                    return
                self._json(200, {'jsonrpc': '2.0', 'id': msg['id'], 'result': res})

            def _json(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.serveur = ThreadingHTTPServer(('127.0.0.1', 0), Gestion)
        self.serveur.daemon_threads = True
        self.port = self.serveur.server_address[1]
        self.url = 'http://127.0.0.1:%d/v1/mcp' % self.port
        self.fil = threading.Thread(target=self.serveur.serve_forever, daemon=True)
        self.fil.start()

    def _appel(self, nom, args):
        if nom in self.reponses:
            data = self.reponses[nom]
        elif nom == 'create_call':
            data = {'call_id': 'call-fictif-0001', 'status': 'queued', 'minutes': 2}
        elif nom == 'send_sms':
            data = {'message_id': 'sms-fictif-0001', 'status': 'sent', 'segments': 1}
        elif nom == 'buy_credits':
            data = {'status': 'ok', 'amount': args.get('amount'), 'currency': args.get('currency')}
        elif nom == 'create_agent':
            data = {'agent_id': 'agent-fictif-0001', 'version': 1, 'status': 'draft'}
        elif nom == 'get_agent':
            if args.get('agent_id') in self.agents_supprimes:
                return {'content': [{'type': 'text', 'text': 'not found'}], 'isError': True}
            data = {'agent_id': args.get('agent_id'), 'version': 1, 'status': 'draft'}
        elif nom == 'list_calls':
            data = {'calls': [{'call_id': 'call-fictif-0101', 'to': '+33100000005', 'summary': 'Demande de rendez-vous (fictive) mardi matin',
                               'agent_id': 'agent-fictif-0001'}]}
        else:
            data = {'status': 'ok'}
        return {'content': [{'type': 'text', 'text': json.dumps(data)}], 'structuredContent': data, 'isError': False}

    def appels_outils(self):
        return [a for a in self.appels if a[0] == 'tools/call']

    def arreter(self):
        self.serveur.shutdown()
        self.serveur.server_close()


class Client:
    """Client HTTP des essais, fourni par ``connexions5652.HTTP_FACTORY`` : l'adresse officielle est remplacée par le faux serveur local
    (même chemin, même corps, mêmes en-têtes) ; le vrai transport ``common.HTTP`` fait l'appel (127.0.0.1 seulement)."""

    def __init__(self, faux):
        from agent.common import HTTP
        self.faux = faux
        self.http = HTTP('http://127.0.0.1:%d' % faux.port, timeout=5)

    def request(self, method, url, data=None, headers=None, limit=12_000_000):
        if not url.startswith('https://api.bland.ai/'):
            raise AssertionError('adresse inattendue : ' + url)
        return self.http.request(method, 'http://127.0.0.1:%d/v1/mcp' % self.faux.port, data, headers, limit)


def fabrique(faux):
    client = Client(faux)
    return lambda origine: client

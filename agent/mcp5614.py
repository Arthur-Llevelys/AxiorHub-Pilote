"""5.6.14 (M13, C17) : cycle de vie MCP (Streamable HTTP) et adaptateurs de capacités fondés sur les schémas réels des outils.

- Session : initialize → notifications/initialized ; l'identifiant Mcp-Session-Id renvoyé par le serveur est conservé et renvoyé sur
  chaque appel ; les réponses JSON et les flux d'événements (text/event-stream) sont acceptés.
- tools/list paginé (nextCursor) ; chaque outil est décrit par son schéma d'entrée. Un adaptateur de capacité (« rechercher », « lire un
  texte ») choisit l'outil et construit les arguments d'après le schéma (propriétés requises, types, imbrication), sans supposer un
  champ « query ».
- Diagnostic en trois niveaux distincts : connexion (initialisation), recherche (un appel de recherche aboutit), récupération (le texte
  complet d'un résultat est lu). Une absence d'accès est rendue telle quelle, jamais comme « référence introuvable ».
- Le filtre sortant reste celui de sources470 / legal_research : seules des questions dépersonnalisées partent.
"""
import json
import re

from .common import HTTP, Stop, read_secret

PROTOCOL = '2025-06-18'
SEARCH_WORDS = ('search', 'recherche', 'find', 'query', 'lookup', 'chercher')
TEXT_WORDS = ('get', 'read', 'fetch', 'retrieve', 'text', 'texte', 'article', 'decision', 'document', 'lire', 'consult')
LEGAL_WORDS = ('legal', 'law', 'juris', 'decision', 'code', 'article', 'legif', 'droit', 'texte', 'loi', 'norm')


def _messages(raw):
    """5.6.24 (F23) : tous les messages JSON-RPC d'une réponse — JSON direct (objet ou lot) ou flux SSE (événements séparés par une ligne
    vide, lignes data: multiples concaténées). Les notifications (sans id) sont conservées à part ; rien n'est réduit au dernier objet."""
    text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
    stripped = text.strip()
    out = []
    if stripped.startswith('{') or stripped.startswith('['):
        value = json.loads(stripped)
        out = value if isinstance(value, list) else [value]
    else:
        buffer = []
        for line in text.splitlines() + ['']:
            if line.startswith('data:'):
                buffer.append(line[5:].lstrip())
            elif line == '' and buffer:
                body = '\n'.join(buffer).strip(); buffer = []
                if body:
                    try:
                        value = json.loads(body)
                    except ValueError:
                        continue
                    out += value if isinstance(value, list) else [value]
    if not out:
        raise Stop('reponse_mcp_illisible')
    return [m for m in out if isinstance(m, dict)]


def _parse(raw):
    """Compatibilité : premier message portant un id (réponse) ou, à défaut, le dernier message."""
    messages = _messages(raw)
    responses = [m for m in messages if 'id' in m]
    return responses[0] if responses else messages[-1]


def correlate(raw, request_id):
    """5.6.24 (F23) : la réponse dont l'id correspond à la requête ; notifications et progressions sont ignorées ; une erreur JSON-RPC
    corrélée est remontée ; aucune réponse corrélée = erreur explicite, jamais un succès déduit d'une notification."""
    messages = _messages(raw)
    for m in messages:
        if m.get('jsonrpc') == '2.0' and 'id' in m and m.get('id') == request_id:
            return m
    if any('id' in m and m.get('id') != request_id for m in messages):
        raise Stop('reponse_mcp_non_correlee')
    raise Stop('reponse_mcp_sans_reponse')


class Session:
    """Une session MCP par connecteur : initialisation, identifiant de session, liste d'outils, appels conformes au schéma."""

    def __init__(self, item, http=None):
        from .extensions364 import validate_endpoint
        validate_endpoint(item.get('endpoint', ''), resolve=True)
        self.item = item
        self.endpoint = item['endpoint']
        self.http = http or HTTP(self.endpoint, timeout=30)
        self.session_id = ''
        self.server = {}
        self.protocol = ''
        self.tools = []
        self._id = 0
        if item.get('auth_type') in ('bearer', 'oauth2') and item.get('secret_file'):
            self.http.headers['Authorization'] = 'Bearer ' + read_secret(item.get('secret_file', ''))

    def _headers(self):
        h = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': self.protocol or PROTOCOL}
        if self.session_id:
            h['Mcp-Session-Id'] = self.session_id
        return h

    def _post(self, payload, expect_result=True):
        raw = self.http.request('POST', self.endpoint, json.dumps(payload).encode(), self._headers(), 2_000_000)
        header_sid = ''
        if hasattr(self.http, 'last_headers'):
            for k, v in (self.http.last_headers or {}).items():
                if str(k).lower() == 'mcp-session-id':
                    header_sid = str(v)
        if header_sid and not self.session_id:
            self.session_id = header_sid
        if not expect_result:
            return None
        result = correlate(raw, payload.get('id'))   # 5.6.24 (F23)
        if not isinstance(result, dict) or result.get('jsonrpc') != '2.0':
            raise Stop('reponse_mcp_invalide')
        if result.get('error'):
            err = result['error'] if isinstance(result['error'], dict) else {}
            raise Stop('erreur_mcp_distante:' + str(err.get('code', ''))[:12])
        return result.get('result', {})

    def call(self, method, params=None):
        self._id += 1
        payload = {'jsonrpc': '2.0', 'id': self._id, 'method': method}
        if params is not None:
            payload['params'] = params
        return self._post(payload)

    def initialize(self):
        result = self.call('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'AxiorHub', 'version': '5.6.14'}})
        self.protocol = str(result.get('protocolVersion') or PROTOCOL)
        self.server = result.get('serverInfo', {}) if isinstance(result.get('serverInfo'), dict) else {}
        if isinstance(result.get('sessionId'), str) and not self.session_id:
            self.session_id = result['sessionId']
        try:
            self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, expect_result=False)
        except Stop:
            pass   # certains serveurs répondent 202 sans corps ; l'absence de réponse n'est pas une erreur
        return result

    def list_tools(self, max_pages=20):
        tools, cursor, pages, seen = [], None, 0, set()
        while True:
            params = {'cursor': cursor} if cursor else {}
            result = self.call('tools/list', params)
            rows = result.get('tools', []) if isinstance(result, dict) else []
            if not isinstance(rows, list):
                raise Stop('liste_outils_mcp_invalide')
            tools += [x for x in rows if isinstance(x, dict) and x.get('name')]
            cursor = result.get('nextCursor') if isinstance(result, dict) else None
            pages += 1
            if not cursor:
                break
            if cursor in seen or pages >= max_pages:   # 5.6.24 (F23) : un curseur répété ou une pagination interminable = catalogue incomplet, jamais présenté comme exhaustif
                raise Stop('pagination_outils_mcp_incomplete')
            seen.add(cursor)
        self.tools = tools
        return tools

    def call_tool(self, name, arguments):
        result = self.call('tools/call', {'name': name, 'arguments': arguments})
        if isinstance(result, dict) and result.get('isError'):
            raise Stop('outil_mcp_en_erreur')
        return result


class CallSession:
    """Même interface que Session au-dessus d'une fonction d'appel (item, method, params) : transport existant ou double de test."""

    def __init__(self, item, call):
        self.item, self._call = item, call
        self.session_id, self.server, self.protocol, self.tools = '', {}, '', []

    def initialize(self):
        result = self._call(self.item, 'initialize', {'protocolVersion': PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'AxiorHub', 'version': '5.6.14'}}) or {}
        self.protocol = str(result.get('protocolVersion') or PROTOCOL) if isinstance(result, dict) else PROTOCOL
        self.server = result.get('serverInfo', {}) if isinstance(result, dict) and isinstance(result.get('serverInfo'), dict) else {}
        return result

    def list_tools(self, max_pages=20):
        tools, cursor, pages = [], None, 0
        while pages < max_pages:
            result = self._call(self.item, 'tools/list', {'cursor': cursor} if cursor else {})
            rows = result.get('tools', []) if isinstance(result, dict) else []
            if not isinstance(rows, list):
                raise Stop('liste_outils_mcp_invalide')
            tools += [x for x in rows if isinstance(x, dict) and x.get('name')]
            cursor = result.get('nextCursor') if isinstance(result, dict) else None
            pages += 1
            if not cursor:
                break
        self.tools = tools
        return tools

    def call_tool(self, name, arguments):
        result = self._call(self.item, 'tools/call', {'name': name, 'arguments': arguments})
        if isinstance(result, dict) and result.get('isError'):
            raise Stop('outil_mcp_en_erreur')
        return result


def _open(item, http=None, call=None):
    return CallSession(item, call) if call is not None else Session(item, http)


# ---------------------------------------------------------------------------------------- adaptateurs de capacités
def _schema(tool):
    s = tool.get('inputSchema') or tool.get('input_schema') or {}
    return s if isinstance(s, dict) else {}


def _props(schema):
    p = schema.get('properties') or {}
    return p if isinstance(p, dict) else {}


def _string_slots(schema, path=()):
    """Chemins des propriétés texte (y compris imbriquées), avec leur caractère requis."""
    out = []
    required = set(schema.get('required') or [])
    for name, spec in _props(schema).items():
        if not isinstance(spec, dict):
            continue
        t = spec.get('type')
        types = t if isinstance(t, list) else [t]
        if 'string' in types or ('anyOf' in spec and any(isinstance(a, dict) and a.get('type') == 'string' for a in spec['anyOf'])):
            out.append({'path': path + (name,), 'required': name in required, 'description': str(spec.get('description', ''))})
        elif 'object' in types:
            out.extend(_string_slots(spec, path + (name,)))
    return out


def _set_path(args, path, value):
    cur = args
    for p in path[:-1]:
        cur = cur.setdefault(p, {})
    cur[path[-1]] = value


def _fill_required(args, schema, path=()):
    """Valeurs sûres pour les champs requis non texte (limites, booléens) ; aucune donnée de dossier."""
    required = set(schema.get('required') or [])
    for name, spec in _props(schema).items():
        if not isinstance(spec, dict) or name not in required:
            continue
        cur = args
        for p in path:
            cur = cur.get(p, {})
        if name in cur:
            continue
        t = spec.get('type')
        types = t if isinstance(t, list) else [t]
        if 'integer' in types or 'number' in types:
            _set_path(args, path + (name,), int(spec.get('default', spec.get('minimum', 1)) or 1))
        elif 'boolean' in types:
            _set_path(args, path + (name,), bool(spec.get('default', False)))
        elif 'array' in types:
            _set_path(args, path + (name,), list(spec.get('default', [])) if isinstance(spec.get('default'), list) else [])
        elif 'object' in types:
            _set_path(args, path + (name,), {})
            _fill_required(args, spec, path + (name,))
        elif 'string' in types:
            enum = spec.get('enum')
            _set_path(args, path + (name,), str(spec.get('default', enum[0] if isinstance(enum, list) and enum else '')))


def choose_tool(tools, capability):
    """Outil pour une capacité (« search » ou « text ») d'après nom, description et schéma ; aucune signature n'est supposée."""
    words = SEARCH_WORDS if capability == 'search' else TEXT_WORDS
    best, best_score = None, 0
    for tool in tools:
        name = str(tool.get('name', '')).lower()
        desc = str(tool.get('description', '')).lower()
        score = sum(2 for w in words if w in name) + sum(1 for w in words if w in desc) + sum(1 for w in LEGAL_WORDS if w in name or w in desc)
        slots = _string_slots(_schema(tool))
        if not slots:
            continue
        if capability == 'search' and any(w in name for w in ('get', 'fetch', 'read')) and not any(w in name for w in SEARCH_WORDS):
            score -= 2
        if score > best_score:
            best, best_score = tool, score
    return best


def search_arguments(tool, query, limit=8):
    schema = _schema(tool)
    slots = _string_slots(schema)
    if not slots:
        raise Stop('outil_mcp_sans_champ_texte')
    preferred = next((s for s in slots if any(w in s['path'][-1].lower() for w in ('query', 'q', 'search', 'text', 'keywords', 'question', 'terme', 'recherche'))), None)
    slot = preferred or next((s for s in slots if s['required']), slots[0])
    args = {}
    _set_path(args, slot['path'], query)
    for name, spec in _props(schema).items():
        if isinstance(spec, dict) and name.lower() in ('limit', 'max_results', 'top_k', 'size', 'n', 'count', 'page_size') and (spec.get('type') in ('integer', 'number')):
            args[name] = limit
    _fill_required(args, schema)
    return args


def text_arguments(tool, reference):
    schema = _schema(tool)
    slots = _string_slots(schema)
    if not slots:
        raise Stop('outil_mcp_sans_champ_texte')
    slot = next((s for s in slots if any(w in s['path'][-1].lower() for w in ('id', 'ref', 'identifier', 'url', 'celex', 'ecli', 'article', 'number', 'numero'))), None) or \
        next((s for s in slots if s['required']), slots[0])
    args = {}
    _set_path(args, slot['path'], reference)
    _fill_required(args, schema)
    return args


def rows_from(result):
    from .extensions364 import _tool_result_rows
    return _tool_result_rows(result)


def text_from(result):
    """Texte complet d'un résultat tools/call (contenus texte concaténés, ou JSON structuré)."""
    if not isinstance(result, dict):
        return ''
    parts = []
    for c in result.get('content', []) or []:
        if isinstance(c, dict) and c.get('type') == 'text':
            parts.append(str(c.get('text', '')))
    if not parts and isinstance(result.get('structuredContent'), (dict, list)):
        parts.append(json.dumps(result['structuredContent'], ensure_ascii=False))
    return '\n'.join(parts)


def search(item, query, limit=8, http=None, call=None, tools=None):
    """Recherche par capacité : statut ok / indisponible / outil absent, lignes, outil et arguments utilisés (journal sans donnée de dossier).
    ``tools`` (schémas déjà connus d'un test de connexion) évite un nouvel appel tools/list."""
    s = _open(item, http, call)
    try:
        if tools is None:
            s.initialize()
            tools = s.list_tools()
        else:
            s.tools = list(tools)
    except Stop as ex:
        return {'status': 'unavailable', 'error': str(ex), 'rows': [], 'tool': '', 'session': bool(s.session_id)}
    tool = choose_tool(tools, 'search')
    if not tool:
        return {'status': 'skipped', 'error': 'outil_recherche_absent', 'rows': [], 'tool': '', 'session': bool(s.session_id)}
    try:
        args = search_arguments(tool, query, limit)
        result = s.call_tool(tool['name'], args)
    except Stop as ex:
        return {'status': 'error', 'error': str(ex), 'rows': [], 'tool': tool['name'], 'session': bool(s.session_id)}
    return {'status': 'ok', 'rows': rows_from(result)[:limit], 'tool': tool['name'], 'arguments_keys': sorted(args), 'session': bool(s.session_id), 'protocol': s.protocol}


def fetch_text(item, reference, http=None, session=None, call=None):
    s = session or _open(item, http, call)
    if not session:
        try:
            s.initialize()
            s.list_tools()
        except Stop as ex:
            return {'status': 'unavailable', 'error': str(ex), 'text': ''}
    tool = choose_tool(s.tools, 'text')
    if not tool:
        return {'status': 'skipped', 'error': 'outil_lecture_absent', 'text': ''}
    try:
        result = s.call_tool(tool['name'], text_arguments(tool, reference))
    except Stop as ex:
        return {'status': 'error', 'error': str(ex), 'text': '', 'tool': tool['name']}
    text = text_from(result)
    return {'status': 'ok' if text.strip() else 'empty', 'text': text, 'tool': tool['name']}


def diagnostic(item, http=None, sample_query='article 1240 du code civil', call=None):
    """Trois niveaux distincts : connexion, recherche, récupération du texte. Aucune donnée de dossier n'est transmise."""
    out = {'connection': 'untested', 'search': 'untested', 'text': 'untested', 'details': {}}
    s = _open(item, http, call)
    try:
        init = s.initialize()
        tools = s.list_tools()
        out['connection'] = 'ok'
        out['details'].update({'server': s.server, 'protocol': s.protocol, 'session': bool(s.session_id), 'tools': [str(t.get('name', ''))[:80] for t in tools][:50],
                               'tool_specs': [{'name': str(t.get('name', ''))[:100], 'description': str(t.get('description', ''))[:500], 'inputSchema': _schema(t)} for t in tools[:100]]})
    except Stop as ex:
        out['connection'] = 'error'
        out['details']['error'] = str(ex)
        return out
    tool = choose_tool(tools, 'search')
    if not tool:
        out['search'] = 'tool_missing'
        return out
    try:
        args = search_arguments(tool, sample_query, 3)
        rows = rows_from(s.call_tool(tool['name'], args))
        out['search'] = 'ok' if rows else 'empty'
        out['details']['search_tool'] = tool['name']
        out['details']['search_rows'] = len(rows)
    except Stop as ex:
        out['search'] = 'error'
        out['details']['search_error'] = str(ex)
        return out
    ttool = choose_tool(tools, 'text')
    if not ttool:
        out['text'] = 'tool_missing'
        return out
    ref = ''
    for r in rows:
        if isinstance(r, dict):
            ref = str(r.get('id') or r.get('reference') or r.get('url') or r.get('celex') or '')
            if ref:
                break
    if not ref:
        out['text'] = 'no_reference'
        return out
    got = fetch_text(item, ref, session=s)
    out['text'] = got['status'] if got['status'] in ('ok', 'empty') else 'error'
    out['details']['text_tool'] = got.get('tool', '')
    out['details']['text_chars'] = len(got.get('text', ''))
    return out

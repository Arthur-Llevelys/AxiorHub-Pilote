"""5.6.83 : liaisons entre capacités d'AxiorHub et outils réellement découverts sur les connecteurs (extensions364, mcp5614).

Une liaison retient le schéma réel, la version du protocole, l'authentification, la date et la portée du test. Un test de connexion prouve l'accès à
la liste des outils ; il ne prouve ni une recherche ni une lecture intégrale : seule la recette fonctionnelle (requête neutre, sans donnée de dossier)
fait passer une capacité à « Disponible ». Aucune compétence n'accorde par elle-même l'accès à un connecteur.
"""
from datetime import datetime, timezone
import hashlib
import json

from .common import Stop

# capacité AxiorHub → (nature de l'outil pour mcp5614, mots attendus)
CAPABILITIES = {'legal.norm.search': ('search', ('norm', 'loi', 'code', 'legislation', 'article', 'texte')),
                'legal.norm.read': ('text', ('norm', 'loi', 'code', 'legislation', 'article', 'texte')),
                'legal.decision.search': ('search', ('decision', 'jurisprudence', 'case', 'arret')),
                'legal.decision.read': ('text', ('decision', 'jurisprudence', 'case', 'arret')),
                'legal.doctrine.search': ('search', ('doctrine', 'notice', 'revue', 'article')),
                'web.page.read': ('text', ('page', 'scrape', 'fetch', 'web'))}
STATUS = {'disponible': 'Disponible', 'connexion': 'Connexion requise', 'refuse': 'Accès refusé', 'limite': 'Capacité limitée'}


def schema(desk):
    desk.db.execute('''CREATE TABLE IF NOT EXISTS tool_bindings5683(connector TEXT NOT NULL, capability TEXT NOT NULL, tool TEXT NOT NULL, schema_sha256 TEXT NOT NULL, protocol TEXT NOT NULL DEFAULT '',
      auth TEXT NOT NULL DEFAULT '', tested_at TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'liste_des_outils', state TEXT NOT NULL, PRIMARY KEY(connector, capability))''')


def _pick(tools, capability):
    from .mcp5614 import _schema, _string_slots
    nature, words = CAPABILITIES[capability]
    best, score = None, 0
    verbs = ('search', 'find', 'query', 'cherche', 'recherche') if nature == 'search' else ('get', 'read', 'fetch', 'lire', 'detail', 'retrieve')
    for tool in tools:
        name = str(tool.get('name', '')).lower()
        text = name + ' ' + str(tool.get('description', '')).lower()
        if not _string_slots(_schema(tool)) or not any(v in name for v in verbs):
            continue
        value = sum(2 for w in words if w in name) + sum(1 for w in words if w in text)
        if value > score:
            best, score = tool, value
    return best


def refresh(desk, extension_id, transport=None):
    """Relit les outils du connecteur et met à jour ses liaisons. Sans outil adapté, la capacité reste absente : aucun choix sur un nom supposé."""
    from .extensions364 import test_item
    schema(desk)
    out = test_item(desk, extension_id, transport=transport, deep=True)
    status = out.get('status')
    stamp = datetime.now(timezone.utc).isoformat()
    desk.db.execute('DELETE FROM tool_bindings5683 WHERE connector=?', (extension_id,))
    levels = out.get('levels') or {}
    if status == 'ok':
        for capability, (nature, _) in CAPABILITIES.items():
            tool = _pick(out.get('tool_specs') or [], capability)
            if not tool:
                continue
            tested = levels.get(nature if nature == 'search' else 'text') == 'ok'
            desk.db.execute('INSERT INTO tool_bindings5683 VALUES(?,?,?,?,?,?,?,?,?)',
                            (extension_id, capability, tool['name'], hashlib.sha256(json.dumps(tool.get('inputSchema') or {}, sort_keys=True).encode()).hexdigest(),
                             str(out.get('protocol_version') or ''), '', stamp, 'recette_fonctionnelle' if tested else 'liste_des_outils', 'disponible' if tested else 'limite'))
    desk.db.commit()
    desk.audit('liaisons5683', {'connector': extension_id, 'status': status, 'donnees_dossier': 'aucune'})
    return {'status': status, 'message': out.get('message', ''), 'liaisons': bindings(desk, extension_id)}


def bindings(desk, connector=''):
    schema(desk)
    query = 'SELECT * FROM tool_bindings5683' + (' WHERE connector=?' if connector else '') + ' ORDER BY capability'
    return [dict(r) for r in desk.db.execute(query, (connector,) if connector else ())]


def capability_status(desk, capability):
    """État lisible d'une capacité. Son absence autorise un autre service configuré, jamais une source inventée."""
    if capability not in CAPABILITIES:
        raise Stop('capacite_inconnue')
    rows = [r for r in bindings(desk) if r['capability'] == capability]
    if any(r['state'] == 'disponible' for r in rows):
        return 'disponible'
    if rows:
        return 'limite'
    from .extensions364 import list_items
    items = list_items(desk)
    return 'refuse' if any(str((i.get('last_test') or {}).get('error') or '') == 'http_403' for i in items) else 'connexion'


def overview(desk):
    return [{'capacite': c, 'etat': capability_status(desk, c), 'libelle': STATUS[capability_status(desk, c)]} for c in CAPABILITIES]

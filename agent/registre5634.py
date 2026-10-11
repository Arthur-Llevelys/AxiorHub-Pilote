"""5.6.34 (B06) : registre partagé des sources juridiques et service de recherche unique.

Constat 5.6.33 : le registre historique (``legal_research.PROVIDERS`` : openlegal, openlegi, goodlegal, pappers) et le catalogue récent
(``sources5632`` : justicelibre, openlegi, goodlegal, librejustice, legaldatahunter) divergeaient ; la page Recherche juridique imposait
une liste cachée historique et les résultats d'un connecteur récent étaient ignorés par les parcours qui utilisaient l'ancienne liste.

- Un seul registre : identifiant canonique, protocole réel (MCP ou API REST historique), connecteur, capacités déclarées et testées,
  fonds, authentification, statut lisible. Les anciens identifiants restent acceptés (configurations historiques).
- Un seul service (``search``) pour la recherche autonome, le dossier, l'audience, les missions et la veille : l'échec d'un fournisseur
  n'efface pas les résultats des autres ; une piste n'est jamais promue en autorité vérifiée sans contrôle officiel.
- Un fournisseur actif sans outil de lecture peut donner une piste, jamais une citation vérifiée.
"""
from .common import Stop

LEGACY_REST = {
    'openlegal': {'name': 'OpenLegal (API REST historique)', 'coverage': 'Selon la passerelle configurée.', 'territories': ('FR',)},
    'pappers': {'name': 'Pappers (API REST historique)', 'coverage': 'Données d’entreprises.', 'territories': ('FR',)},
}
CAPABILITY_LABELS = {'search': 'rechercher', 'text': 'lire une décision', 'law': 'lire un article à une date', 'citation': 'retrouver une citation',
                     'resolve': 'résoudre une référence'}


def catalog():
    """Toutes les sources connues : catalogue MCP qualifié, puis API REST historiques (conservées pour les anciennes configurations)."""
    from .sources5632 import PROVIDERS as MCP, item_id
    out = {}
    for pid, p in MCP.items():
        out[pid] = {'id': pid, 'name': p['name'], 'protocol': 'mcp', 'connector': item_id(pid), 'coverage': p.get('coverage', ''),
                    'territories': list(p.get('territories', ())), 'auth': list(p.get('auth', ())), 'declared': sorted(k for k, v in (p.get('tools') or {}).items() if v)}
    for pid, p in LEGACY_REST.items():
        if pid not in out:
            out[pid] = {'id': pid, 'name': p['name'], 'protocol': 'rest', 'connector': '', 'coverage': p['coverage'], 'territories': list(p['territories']),
                        'auth': ['bearer'], 'declared': ['search']}
    return out


def known(pid):
    return str(pid or '').strip().lower() in catalog()


def all_ids():
    return tuple(catalog())


def status(desk):
    """État de chaque source pour CETTE installation : configurée, testée, capacités réellement disponibles, raison lisible sinon."""
    rows = []
    extensions = (desk.c.get('lawve_extensions') or {})
    rest = (desk.c.get('legal_research') or {}).get('providers') or {}
    for pid, p in catalog().items():
        caps, reason, active = [], '', False
        if p['protocol'] == 'mcp':
            item = extensions.get(p['connector']) or next((x for x in extensions.values() if isinstance(x, dict) and x.get('provider') == pid), None)
            if not item:
                reason = 'Non configurée (Paramètres › Sources juridiques).'
            elif not item.get('enabled'):
                reason = 'Désactivée.'
            elif (item.get('last_test') or {}).get('status') != 'ok':
                reason = 'À tester : aucune connexion réussie avec la configuration actuelle.'
            else:
                active = True
                tools = {str(t.get('name') if isinstance(t, dict) else t) for t in (item.get('last_test') or {}).get('tool_specs', [])} or \
                        {str(x) for x in (item.get('last_test') or {}).get('tools', [])}
                from .sources5632 import PROVIDERS as MCP
                declared = (MCP.get(pid) or {}).get('tools') or {}
                caps = sorted(k for k, name in declared.items() if name and (not tools or name in tools)) or ['search']
        else:
            cfg = rest.get(pid) or {}
            if not cfg.get('enabled') or not cfg.get('url'):
                reason = 'Non configurée (API REST historique).'
            else:
                active, caps = True, ['search']
        missing_read = active and not ({'text', 'law'} & set(caps))
        rows.append({**p, 'active': active, 'capabilities': caps, 'reason': reason,
                     'note': 'Pistes seulement : aucun outil de lecture, citation non vérifiable par ce fournisseur.' if missing_read else ''})
    return rows


def available(desk, operation='search'):
    return [r for r in status(desk) if r['active'] and operation in r['capabilities']]


def search(desk, question, matter='', providers=None, limit=8):
    """Service unique : sources REST configurées puis connecteurs MCP actifs ; résultats fusionnés, échecs partiels conservés par
    fournisseur, pistes distinguées des autorités vérifiées sur la source officielle."""
    from . import legal_research
    wanted = [p for p in (providers or []) if known(p)] or [r['id'] for r in available(desk)]
    if not wanted:
        raise Stop('aucune_source_juridique_active')
    rest = [p for p in wanted if catalog()[p]['protocol'] == 'rest']
    out = {'leads': [], 'verified_authorities': [], 'reports': [], 'anonymized_query': '', 'matter': matter}
    if rest:
        try:
            r = legal_research.research(desk, {'matter': matter, 'question': question, 'providers': rest, 'limit': limit})
            out['leads'] += r['leads']
            out['verified_authorities'] += r['verified_authorities']
            out['anonymized_query'] = r['anonymized_query']
            out['reports'] += [{'provider': x.get('provider', ''), 'status': 'error', 'error': x.get('error', '')} for x in r['errors']]
            out['reports'] += [{'provider': p, 'status': 'ok'} for p in rest if p not in {x.get('provider') for x in r['errors']}]
        except Stop as ex:
            out['reports'] += [{'provider': p, 'status': 'error', 'error': str(ex)} for p in rest]
    if any(catalog()[p]['protocol'] == 'mcp' for p in wanted):
        try:
            r = legal_research.research_enabled_mcp(desk, matter, question, limit)
            keep = [x for x in r['leads'] if x.get('provider') in wanted]
            out['leads'] += keep
            out['verified_authorities'] += [x for x in r['verified_authorities']]
            out['anonymized_query'] = out['anonymized_query'] or r['anonymized_query']
            out['reports'] += [x for x in r['connectors'] if x.get('provider') in wanted or not x.get('provider')]
        except Stop as ex:
            out['reports'].append({'provider': 'mcp', 'status': 'error', 'error': str(ex)})
    out['status'] = 'completed' if out['reports'] and all(x.get('status') in ('ok', 'completed') for x in out['reports']) else ('partial' if out['leads'] else 'error')
    out['warning'] = 'Les résultats des fournisseurs sont des pistes ; seules les décisions revérifiées sur la source officielle sont citables.'
    return out

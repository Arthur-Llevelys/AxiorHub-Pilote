"""5.6.32 (cahier des charges additionnel, R07 / GI17–GI22) : catalogue versionné des sources juridiques et connexion guidée.

- Chaque fournisseur a une fiche maintenue ici (jamais déduite d'un texte produit par l'IA) : pages officielles de compte et d'aide,
  adresse MCP, modes d'authentification réellement pris en charge, fonds couverts, conditions d'usage, outils déclarés pour chaque
  capacité (rechercher, lire une décision, lire un article à une date, retrouver une citation, résoudre une référence).
- Le connecteur est enregistré dans le registre existant (``extensions364``) : même coffre, même session MCP, même contrôle des
  destinations réseau ; la clé éventuelle part dans un en-tête Authorization côté serveur, jamais dans une URL ni un journal.
- Le diagnostic distingue accès, initialisation, liste des outils, recherche neutre et récupération d'un texte ; aucune pièce
  du cabinet n'est envoyée. Un outil absent donne une capacité indisponible, pas un échec général.
- OAuth : un parcours propre à AxiorHub n'est proposé que s'il est qualifié ; sinon la fiche le dit et propose le mode jeton.
"""
from datetime import datetime, timezone
import re

from .common import Stop

VERIFIED_ON = '2026-10-11'
PROVIDERS = {
    'justicelibre': {
        'name': 'JusticeLibre', 'endpoint': 'https://justicelibre.org/mcp',
        'account': 'https://justicelibre.org/mon-compte.html', 'help': 'https://justicelibre.org/serveur-mcp.html',
        'terms': 'https://justicelibre.org/conditions-utilisation.html',
        'auth': ('none', 'bearer'), 'default_auth': 'none',
        'coverage': 'Cour de cassation, cours d’appel, tribunaux judiciaires et de commerce ; Conseil d’État, CAA, TA ; Conseil constitutionnel ; '
                    'CEDH ; CJUE ; articles de codes et de lois à une date donnée ; Journal officiel ; conventions collectives ; CNIL.',
        'territories': ('FR', 'UE', 'CEDH'),
        'note': 'Recherche publique sans compte ni clé. Gratuit pour un usage personnel, de recherche ou associatif ; usage commercial '
                'soumis à accord écrit du fournisseur. Un compte facultatif (jeton) donne accès aux dossiers du compte.',
        'tools': {'search': 'search', 'text': 'get_decision', 'law': 'get_law_article', 'citation': 'check_citations', 'resolve': 'resolve_reference',
                  'case_search': 'search_case_law'},
    },
    'openlegi': {
        'name': 'OpenLegi', 'endpoint': 'https://mcp.openlegi.fr/legifrance/mcp',
        'account': 'https://www.openlegi.fr/accounts/signup', 'help': 'https://auth.openlegi.fr',
        'terms': '', 'auth': ('bearer',), 'default_auth': 'bearer',
        'coverage': 'Légifrance : codes, lois, décrets, jurisprudence publiée.', 'territories': ('FR',),
        'note': 'Jeton personnel OpenLegi (tableau de bord du compte). Les identifiants OAuth publiés pour d’autres assistants ne sont pas '
                'réutilisés ; le parcours OAuth propre à AxiorHub n’est pas encore qualifié par le fournisseur.',
        'tools': {},
    },
    'goodlegal': {
        'name': 'GoodLegal', 'endpoint': 'https://api.goodlegal.fr/goodlegal/mcp/',
        'account': 'https://api.goodlegal.fr/dashboard', 'help': 'https://api.goodlegal.fr/dashboard',
        'terms': '', 'auth': ('bearer',), 'default_auth': 'bearer',
        'coverage': 'Jurisprudence et textes selon l’offre du compte.', 'territories': ('FR',),
        'note': 'Clé API du tableau de bord GoodLegal, transmise par en-tête Authorization Bearer.',
        'tools': {},
    },
    'librejustice': {
        'name': 'LibreJustice', 'endpoint': 'https://librejustice.fr/mcp/',
        'account': 'https://librejustice.fr/mcp-guide', 'help': 'https://librejustice.fr/mcp-guide',
        'terms': '', 'auth': ('bearer',), 'default_auth': 'bearer', 'oauth': 'non_qualifie',
        'coverage': 'Jurisprudence française.', 'territories': ('FR',),
        'note': 'Le fournisseur propose une autorisation OAuth sur son site ; tant que le parcours propre à AxiorHub n’est pas qualifié, '
                'seul un jeton fourni par le fournisseur peut être renseigné.',
        'tools': {},
    },
    'legaldatahunter': {
        'name': 'Legal Data Hunter', 'endpoint': 'https://legaldatahunter.com/mcp',
        'account': 'https://legaldatahunter.com/account', 'help': 'https://legaldatahunter.com/account',
        'terms': '', 'auth': ('bearer',), 'default_auth': 'bearer',
        'coverage': 'Droit étranger et européen selon les fonds affichés par le compte.', 'territories': ('UE', 'INT'),
        'note': 'Clé créée dans la rubrique API Keys du compte : copiez-la au moment où elle s’affiche. La recherche reste limitée au droit '
                'pertinent pour le dossier.',
        'tools': {},
    },
}


def provider(pid):
    p = PROVIDERS.get(str(pid or ''))
    if not p:
        raise Stop('source_juridique_inconnue')
    return p


def item_id(pid):
    return 'connector-axiorhub-' + pid


def catalogue(desk):
    """Fiches publiques et état de chaque connexion (renseignée, testée, activée) ; jamais la clé."""
    out = []
    for pid, p in PROVIDERS.items():
        item = desk.settings('lawve:item:' + item_id(pid), {}) or {}
        test = item.get('last_test') or {}
        out.append({'id': pid, 'name': p['name'], 'endpoint': p['endpoint'], 'account': p['account'], 'help': p['help'], 'terms': p['terms'],
                    'auth': list(p['auth']), 'coverage': p['coverage'], 'territories': list(p['territories']), 'note': p['note'],
                    'verified_on': VERIFIED_ON, 'configured': bool(item), 'enabled': bool(item.get('enabled')),
                    'secret_configured': bool(item.get('secret_file')), 'last_test': {k: test.get(k) for k in ('status', 'message', 'levels', 'at', 'error')} if test else {}})
    return out


def connect(desk, pid, token='', auth='', allow_external=False):
    """Enregistre (ou met à jour) le connecteur d'un fournisseur du catalogue ; la clé va au coffre, l'adresse reste celle du catalogue."""
    from . import extensions364 as ext
    p = provider(pid)
    auth = str(auth or p['default_auth'])
    if auth not in p['auth']:
        raise Stop('authentification_source_non_prise_en_charge')
    token = str(token or '').strip()
    if token and (len(token) > 4096 or re.search(r'[\s\x00]', token)):
        raise Stop('secret_extension_invalide')
    eid = item_id(pid)
    previous = desk.settings('lawve:item:' + eid, {}) or {}
    secret_file = str(previous.get('secret_file') or '')
    if token:
        secret_file = ext._save_secret(desk, eid, token)
    if auth == 'bearer' and not secret_file:
        raise Stop('cle_source_absente')
    if auth == 'none':
        secret_file = ''
    now = datetime.now(timezone.utc).isoformat()
    item = {'id': eid, 'kind': 'connector', 'author': 'axiorhub', 'slug': pid, 'provider': pid, 'name': p['name'],
            'source_url': p['help'], 'license': '', 'endpoint': ext.validate_endpoint(p['endpoint']), 'transport': 'streamable_http',
            'auth_type': auth, 'secret_file': secret_file, 'external_data_allowed': bool(allow_external),
            'purposes': ['legal_analysis', 'hearing'], 'preferred_tools': dict(p['tools']),
            'enabled': bool(previous.get('enabled')) and bool(allow_external), 'status': 'configured',
            'created_at': previous.get('created_at') or now, 'updated_at': now, 'last_test': {} if token else previous.get('last_test', {}),
            'catalogue_verified_on': VERIFIED_ON}
    desk.setting('lawve:item:' + eid, item)
    desk.c.setdefault('lawve_extensions', {})[eid] = item
    desk.audit('source_juridique_5632_enregistree', {'provider': pid, 'auth': auth, 'secret_replaced': bool(token), 'external_data_allowed': bool(allow_external)})
    return ext._public(item)


def verify(desk, pid):
    """Diagnostic à niveaux séparés ; activation possible seulement après un accès réussi et l'accord explicite sur les données externes."""
    from . import extensions364 as ext
    provider(pid)
    eid = item_id(pid)
    if not desk.settings('lawve:item:' + eid):
        raise Stop('source_juridique_non_configuree')
    result = ext.test_item(desk, eid, deep=True)
    levels = result.get('levels') or {}
    return {'provider': pid, 'status': result.get('status'), 'message': result.get('message'), 'levels': levels,
            'tools_count': result.get('tools_count', 0), 'error': result.get('error', '')}


def enable(desk, pid, on=True):
    from . import extensions364 as ext
    return ext.set_enabled(desk, item_id(pid), on, reviewed=True)


def enabled_items(desk):
    return [i for i in (desk.c.get('lawve_extensions') or {}).values()
            if isinstance(i, dict) and i.get('kind') == 'connector' and i.get('enabled') and i.get('provider') in PROVIDERS]


def tool_for(item, capability, tools):
    """Outil déclaré par le catalogue pour la capacité, s'il existe réellement dans la liste renvoyée par le serveur."""
    wanted = (item.get('preferred_tools') or {}).get(capability)
    if wanted:
        for t in tools or ():
            if isinstance(t, dict) and t.get('name') == wanted:
                return t
    return None


def call(item, capability, arguments):
    """Appelle l'outil déclaré pour une capacité (session MCP du registre) ; capacité absente = indisponible, explicitement."""
    from . import extensions364 as ext, mcp5614
    session = mcp5614.CallSession(item, ext._mcp_call)
    session.initialize()
    tools = session.list_tools()
    tool = tool_for(item, capability, tools)
    if tool is None:
        raise Stop('capacite_source_indisponible')
    return mcp5614.text_from(session.call_tool(tool['name'], arguments))

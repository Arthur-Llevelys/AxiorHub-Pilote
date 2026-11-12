"""5.6.32 (cahier des charges additionnel, R07 / GI17–GI22) : catalogue versionné des sources juridiques et connexion guidée.

- Chaque fournisseur a une fiche maintenue ici (jamais déduite d'un texte produit par l'IA) : pages officielles de compte et d'aide,
  adresse MCP, modes d'authentification réellement pris en charge, fonds couverts, conditions d'usage, outils déclarés pour chaque
  capacité (rechercher, lire une décision, lire un article à une date, retrouver une citation, résoudre une référence).
- Le connecteur est enregistré dans le registre existant (``extensions364``) : même coffre, même session MCP, même contrôle des
  destinations réseau ; la clé éventuelle part dans un en-tête Authorization côté serveur, jamais dans une URL ni un journal.
- Le diagnostic distingue accès, initialisation, liste des outils, recherche neutre et récupération d'un texte ; aucune pièce
  du cabinet n'est envoyée. Un outil absent donne une capacité indisponible, pas un échec général.
- OAuth : un parcours propre à AxiorHub n'est proposé que s'il est qualifié ; sinon la fiche le dit et propose le mode jeton.

5.6.97 (J01-J06, J08) : fiches complétées (catégorie, transport, lien d'accès officiel, qualification, conditions, territoires,
capacités), fiches nouvelles (EULEX.AI, data.gouv.fr, Pappers MCP, Contract Factory, OpenEU Auto, Patriboum, CanLII) et adaptateurs
AxiorHub intégrés (de_bund, es_boe, gb_legislation, ca_federal, ca_quebec ; voir mcp_internationaux5697). Clé candidate testée avant
de remplacer la clé active ; test « initialize puis tools/list » séparé de l'activation ; OAuth MCP dans oauth_mcp5697. La sélection
des sources avant appel (J07) appartient au registre (registre5634) : ce module fournit ``territories`` et ``capabilities``.
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


# ------------------------------------------------------------------------------------------------ 5.6.97 (J01-J06, J08)
# Champs ajoutés à chaque fiche (les anciens restent lus : account, help, terms, auth, default_auth, coverage, note, tools) :
#   category      droit | donnees_publiques | entreprises | patrimoine (évite de confondre droit, entreprise et simulation)
#   transport     streamable_http (MCP distant) | integre (adaptateur AxiorHub livré, aucun réseau) | api_rest (fiche conditionnelle)
#   access_url    page officielle où obtenir la clé, connecter le compte ou lire l'aide ; '' quand aucun lien n'est confirmé
#   qualified     accès, outils et conditions confirmés ; False = fiche « À vérifier » : ni connexion, ni test, ni activation
#   terms_url     conditions d'usage ou licence du fonds
#   territories   champ juridique couvert (FR, UE, CEDH, INT, DE-BUND, ES-ETAT, GB, GB-EAW, CA-FED, CA-QC) : jamais la langue
#   capabilities  textes | decisions | donnees_publiques | entreprise | justice | simulation (périmètre annoncé ; le périmètre
#                 testé pour le cabinet est conservé à part dans le dernier test, capacité par capacité)
#   outils_revus  {capacité: (noms d'outils revus)} : seule une capacité dont tous les outils revus existent est utilisable
# Les adresses et liens sont des constantes de ce fichier : une réponse de modèle ou de MCP ne modifie jamais une fiche.
VERIFIED_ON_5697 = '2026-11-08'
CATEGORIES = ('droit', 'donnees_publiques', 'entreprises', 'patrimoine')
CAPACITES = ('textes', 'decisions', 'donnees_publiques', 'entreprise', 'justice', 'simulation')
TRANSPORTS = ('streamable_http', 'integre', 'api_rest')
# Zones du guide (J01) : France et Union européenne séparées, puis Allemagne, Espagne, Angleterre et pays de Galles, Canada
# (fédéral et Québec séparés), puis les données publiques et entreprises.
ZONES_GUIDE = (('FR', 'zone.fr'), ('UE', 'zone.ue'), ('DE-BUND', 'zone.de'), ('ES-ETAT', 'zone.es'), ('GB-EAW', 'zone.gb'),
               ('CA-FED', 'zone.ca_fed'), ('CA-QC', 'zone.ca_qc'), ('ENTREPRISES', 'zone.entreprises'))

_COMPLEMENTS = {
    'justicelibre': {'category': 'droit', 'transport': 'streamable_http', 'access_url': 'https://justicelibre.org/serveur-mcp.html', 'qualified': True,
                     'terms_url': 'https://justicelibre.org/conditions-utilisation.html', 'capabilities': ('textes', 'decisions'), 'zone_guide': 'FR',
                     'outils_revus': {'textes': ('search', 'get_law_article'), 'decisions': ('search_case_law', 'get_decision')},
                     # Les outils du compte privé (dossiers du compte) ne sont jamais proposés à l'agent.
                     'outils_interdits': r'(?i)(dossier|folder|account|compte|upload|save|delete)'},
    'openlegi': {'category': 'droit', 'transport': 'streamable_http', 'access_url': 'https://www.openlegi.fr/accounts/signup', 'qualified': True,
                 'terms_url': '', 'capabilities': ('textes', 'decisions'), 'zone_guide': 'FR', 'outils_revus': {}},
    'goodlegal': {'category': 'droit', 'transport': 'streamable_http', 'access_url': 'https://api.goodlegal.fr/dashboard', 'qualified': True,
                  'terms_url': '', 'capabilities': ('textes', 'decisions'), 'zone_guide': 'FR', 'outils_revus': {}},
    'librejustice': {'category': 'droit', 'transport': 'streamable_http', 'access_url': 'https://librejustice.fr/mcp-guide', 'qualified': True,
                     'terms_url': '', 'capabilities': ('textes', 'decisions'), 'zone_guide': 'FR', 'outils_revus': {},
                     # La clé REST documentée par l'éditeur n'est pas un jeton MCP : elle n'est jamais proposée ici (configurations
                     # existantes conservées telles quelles).
                     'note_5697': 'La clé de l’API REST de l’éditeur n’est pas un jeton MCP : ne la collez pas ici sans confirmation.'},
    'legaldatahunter': {'category': 'droit', 'transport': 'streamable_http', 'access_url': 'https://legaldatahunter.com/account', 'qualified': True,
                        'terms_url': '', 'capabilities': ('textes', 'decisions'), 'zone_guide': 'UE', 'outils_revus': {}},
}
for _pid, _extra in _COMPLEMENTS.items():
    PROVIDERS[_pid].update(_extra)


def _fiche(name, endpoint, category, transport, territories, capabilities, zone_guide, qualified, auth=('none',), default_auth='none',
           access_url='', help_url='', terms_url='', coverage='', note='', tools=None, outils_revus=None, **extra):
    """Fiche complète avec les champs anciens (account, help, terms) remplis depuis les nouveaux, pour les lecteurs historiques."""
    fiche = {'name': name, 'endpoint': endpoint, 'account': access_url or help_url, 'help': help_url or access_url, 'terms': terms_url,
             'auth': tuple(auth), 'default_auth': default_auth, 'coverage': coverage, 'territories': tuple(territories), 'note': note,
             'tools': dict(tools or {}), 'category': category, 'transport': transport, 'access_url': access_url, 'qualified': bool(qualified),
             'terms_url': terms_url, 'capabilities': tuple(capabilities), 'zone_guide': zone_guide, 'outils_revus': dict(outils_revus or {})}
    fiche.update(extra)
    return fiche


_INTEGRE_OUTILS = {'search': 'search_norms', 'law': 'get_norm', 'text': 'get_norm'}
_INTEGRE_REVUS = {'textes': ('search_norms', 'get_norm')}
PROVIDERS.update({
    # J02 : MCP du droit de l'Union (OAuth avec enregistrement dynamique documenté par l'éditeur ; aucune clé demandée).
    'eulex': _fiche('EULEX.AI', 'https://mcp.eulex.ai/mcp', 'droit', 'streamable_http', ('UE',), ('textes', 'decisions'), 'UE', True,
                    auth=('oauth2',), default_auth='oauth2', access_url='https://mcp.eulex.ai/', help_url='https://mcp.eulex.ai/',
                    coverage='Droit de l’Union européenne (priorité au périmètre UE ; autres collections selon les droits vérifiés).',
                    note='Connexion par compte (OAuth) : aucune clé à copier. Les droits obtenus s’affichent après le retour.',
                    oauth={'etat': 'qualifie', 'enregistrement_dynamique': True, 'origines': ('https://mcp.eulex.ai', 'https://auth.eulex.ai', 'https://eulex.ai')},
                    liens_a_confirmer=True),
    # J03 : données publiques, entreprises, simulation patrimoniale (même catalogue, catégories distinctes).
    'datagouv': _fiche('data.gouv.fr', 'https://mcp.data.gouv.fr/mcp', 'donnees_publiques', 'streamable_http', ('FR',), ('donnees_publiques',),
                       'ENTREPRISES', True, access_url='https://www.data.gouv.fr/', help_url='https://www.data.gouv.fr/',
                       terms_url='https://www.data.gouv.fr/', coverage='Jeux de données publiques et leurs métadonnées.',
                       note='Sans clé. Aucun résultat n’est une autorité juridique par lui-même.', liens_a_confirmer=True),
    'pappers_mcp': _fiche('Pappers', 'https://mcp.pappers.fr', 'entreprises', 'streamable_http', ('FR',), ('entreprise', 'justice'), 'ENTREPRISES', True,
                          auth=('bearer',), default_auth='bearer', access_url='https://moncompte.pappers.fr/api',
                          help_url='https://www.pappers.fr/mcp-documentation', coverage='Informations d’entreprises ; Pappers Justice selon les droits du compte.',
                          note='Jeton API Pappers transmis en Authorization Bearer. Le droit Justice du compte est vérifié à part : il ne se déduit pas '
                               'du fonctionnement de la recherche par SIREN.'),
    'contract_factory': _fiche('Contract Factory', 'https://www.contract-factory.com/mcp.php', 'entreprises', 'streamable_http', ('FR',), ('entreprise',),
                               'ENTREPRISES', True, access_url='https://www.contract-factory.com/', help_url='https://www.contract-factory.com/',
                               coverage='Informations publiques d’entreprises issues du RNE (recherche SIREN).',
                               note='Sans compte ni clé. Consultation seulement : aucune génération d’acte, création de société ni formalité.',
                               outils_interdits=r'(?i)(genere|generat|cree|creat|formalit|statut|depot|depos|immatric|draft|redig)',
                               liens_a_confirmer=True),
    'openeu_auto': _fiche('OpenEU Auto', 'https://openeu.avocat2030.ai/mcp', 'droit', 'streamable_http', ('UE',), ('textes',), 'UE', False,
                          coverage='Périmètre annoncé : droit de l’Union. Non confirmé.',
                          note='À qualifier : adresse, mode d’accès, périmètre et conditions à confirmer par l’éditeur. Aucun envoi d’ici là ; '
                               'l’Union européenne reste disponible par les autres sources activées (vérification sur EUR-Lex).'),
    'patriboum': _fiche('Patriboum', 'https://patriboum.com/mcp', 'patrimoine', 'streamable_http', ('FR',), ('simulation',), 'ENTREPRISES', False,
                        access_url='https://patriboum.com/', help_url='https://patriboum.com/',
                        coverage='Simulation patrimoniale (documentation des types, essais fictifs).',
                        note='À vérifier : les outils de session peuvent produire un lien de consultation. Création, mise à jour et partage de '
                             'sessions avec des données du cabinet restent interdits.',
                        outils_interdits=r'(?i)(session|share|partag|create|update|cree|mettre)', liens_a_confirmer=True),
    # J05 : décisions canadiennes — fiche conditionnelle (conditions, droit d'usage et clé dans l'URL à vérifier).
    'canlii': _fiche('CanLII (décisions)', '', 'droit', 'api_rest', ('CA-FED', 'CA-QC'), ('decisions',), 'CA-FED', False,
                     access_url='https://www.canlii.org/en/feedback/feedback.html', help_url='https://github.com/canlii/API_documentation/blob/master/EN.md',
                     coverage='Métadonnées de décisions (pas le texte des décisions).',
                     note='Fiche conditionnelle : l’API documente une clé passée dans l’adresse ; conditions, fonds et transport du secret à '
                          'vérifier avant tout usage. Le lien renvoie toujours à la décision ou à la juridiction source.',
                     acces_libelle='src97.ask_canlii'),
    # J04, J05, J08 : adaptateurs AxiorHub sur index SQLite local par fonds (mcp_internationaux5697) ; aucun réseau à la recherche.
    'de_bund': _fiche('Droit fédéral allemand', '', 'droit', 'integre', ('DE-BUND',), ('textes',), 'DE-BUND', True,
                      help_url='https://www.gesetze-im-internet.de/hinweise.html', terms_url='https://www.gesetze-im-internet.de/hinweise.html',
                      coverage='Textes fédéraux consolidés (Gesetze im Internet), langue allemande. Pas le droit des Länder.',
                      note='Consolidation du site : distincte du texte de promulgation (publication fédérale liée lorsqu’elle est connue). '
                           'Aucun état historique n’est attribué au texte courant.',
                      tools=_INTEGRE_OUTILS, outils_revus=_INTEGRE_REVUS,
                      decisions={'etat': 'couverture_partielle', 'lien': 'https://docs.rechtsinformationen.bund.de/'}),
    'es_boe': _fiche('Législation de l’État espagnol', '', 'droit', 'integre', ('ES-ETAT',), ('textes',), 'ES-ETAT', True,
                     help_url='https://www.boe.es/datosabiertos/api/api.php', terms_url='https://www.boe.es/informacion/aviso_legal/index.php#reutilizacion',
                     coverage='Législation consolidée du BOE (identifiants BOE et ELI). Pas les communautés autonomes.',
                     note='Texte original et consolidation distincts ; date de version affichée. Source : Agencia Estatal Boletín Oficial del Estado.',
                     tools=_INTEGRE_OUTILS, outils_revus=_INTEGRE_REVUS,
                     decisions={'etat': 'lien_seulement', 'lien': 'https://www.poderjudicial.es/search/indexAN.jsp'}),
    'gb_legislation': _fiche('Angleterre et pays de Galles', '', 'droit', 'integre', ('GB-EAW', 'GB'), ('textes',), 'GB-EAW', True,
                             help_url='https://www.legislation.gov.uk/developer/formats/xml',
                             terms_url='https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
                             coverage='legislation.gov.uk : texte applicable en Angleterre et pays de Galles (étendue territoriale vérifiée). '
                                      'Pas le droit propre à l’Écosse ou à l’Irlande du Nord.',
                             note='Contains public sector information licensed under the Open Government Licence v3.0.',
                             tools=_INTEGRE_OUTILS, outils_revus=_INTEGRE_REVUS,
                             decisions={'etat': 'licence_a_verifier', 'lien': 'https://caselaw.nationalarchives.gov.uk/open-justice-licence'}),
    'ca_federal': _fiche('Canada — droit fédéral', '', 'droit', 'integre', ('CA-FED',), ('textes',), 'CA-FED', True,
                         help_url='https://laws-lois.justice.gc.ca/eng/XML/', terms_url='https://laws-lois.justice.gc.ca/eng/regulations/SI-97-5/',
                         coverage='Lois et règlements fédéraux consolidés (Justice Canada), versions française et anglaise reliées. Pas le droit des provinces.',
                         note='Reproduction non officielle (décret sur la reproduction de la législation fédérale).',
                         tools=_INTEGRE_OUTILS, outils_revus=_INTEGRE_REVUS,
                         decisions={'etat': 'cle_conditionnelle', 'lien': 'https://github.com/canlii/API_documentation/blob/master/EN.md'}),
    'ca_quebec': _fiche('Canada — Québec', '', 'droit', 'integre', ('CA-QC',), ('textes',), 'CA-QC', True,
                        help_url='https://www.legisquebec.gouv.qc.ca/fr/document/lc/CCQ-1991',
                        terms_url='https://www.donneesquebec.ca/recherche/dataset/https-donneesquebec-ca-recherche-dataset-edit-lois-et-reglements-quebec',
                        coverage='Lois et règlements du Québec (Éditeur officiel), fiche LégisQuébec liée. Pas le droit fédéral.',
                        note='Licence du jeu Données Québec : CC BY-NC-ND 4.0 (usage non commercial) ; format XML non documenté publiquement. '
                             'Synchronisation fermée tant que licence et format ne sont pas qualifiés.',
                        tools=_INTEGRE_OUTILS, outils_revus=_INTEGRE_REVUS,
                        decisions={'etat': 'cle_conditionnelle', 'lien': 'https://github.com/canlii/API_documentation/blob/master/EN.md'}),
})


def provider(pid):
    p = PROVIDERS.get(str(pid or ''))
    if not p:
        raise Stop('source_juridique_inconnue')
    return p


def item_id(pid):
    return 'connector-axiorhub-' + pid


def mode(p):
    """Carte commune (J06) : quelle action proposer. Une fiche non qualifiée ne propose que « En savoir plus »."""
    if not p.get('qualified'):
        return 'a_verifier'
    if p.get('transport') == 'integre':
        return 'integre'
    if p.get('default_auth') == 'oauth2' and (p.get('oauth') or {}).get('etat') == 'qualifie':
        return 'oauth'
    if p.get('default_auth') == 'bearer':
        return 'cle'
    return 'sans_cle'


def _item(desk, pid):
    return desk.settings('lawve:item:' + item_id(pid), {}) or {}


def _cabinet(desk):
    """Empreinte du cabinet (dossier d'état) : sépare les sessions MCP de deux cabinets servis par le même processus."""
    import hashlib
    from pathlib import Path
    return hashlib.sha256(str(Path(desk.c['state_dir']).resolve()).encode()).hexdigest()[:16]


def _index_racine(desk):
    from pathlib import Path
    return str(Path(desk.c['state_dir']) / 'sources5697')


def catalogue(desk):
    """Fiches publiques et état de chaque connexion (renseignée, testée, activée) ; jamais la clé ni le jeton.
    5.6.97 : champs category, transport, access_url, qualified, terms_url, territories, capabilities, mode de carte, capacités testées."""
    out = []
    for pid, p in PROVIDERS.items():
        item = _item(desk, pid)
        test = item.get('last_test') or {}
        row = {'id': pid, 'name': p['name'], 'endpoint': p['endpoint'], 'account': p['account'], 'help': p['help'], 'terms': p['terms'],
               'auth': list(p['auth']), 'coverage': p['coverage'], 'territories': list(p['territories']), 'note': p['note'],
               'verified_on': VERIFIED_ON if pid in _COMPLEMENTS else VERIFIED_ON_5697, 'configured': bool(item), 'enabled': bool(item.get('enabled')),
               'secret_configured': bool(item.get('secret_file')),
               'last_test': {k: test.get(k) for k in ('status', 'message', 'levels', 'at', 'error', 'capacites', 'profondeur', 'secret_version')} if test else {},
               'category': p['category'], 'transport': p['transport'], 'access_url': p['access_url'], 'qualified': bool(p['qualified']),
               'terms_url': p['terms_url'], 'capabilities': list(p['capabilities']), 'mode': mode(p), 'zone_guide': p.get('zone_guide', ''),
               'adaptateur_axiorhub': p['transport'] == 'integre', 'decisions': dict(p.get('decisions') or {}),
               'allow_external': bool(item.get('external_data_allowed')), 'secret_version': int(item.get('secret_version') or 0),
               'candidate_pending': bool((item.get('candidate') or {}).get('file')), 'revision': int(item.get('revision') or 0),
               'oauth': {k: (item.get('oauth') or {}).get(k) for k in ('etat', 'droits', 'expire')} if item.get('oauth') else {},
               'note_5697': p.get('note_5697', ''), 'acces_libelle': p.get('acces_libelle', '')}
        if p['transport'] == 'integre':
            from . import mcp_internationaux5697 as mi
            row['index'] = mi.etat_index(_index_racine(desk), pid)
            row['synchronisation'] = mi.etat_synchronisation(desk, pid)
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------------ coffre : clé candidate
def _vault_candidate(desk, eid, value):
    """Clé candidate (J06) : fichier versionné distinct de la clé active, chiffré au coffre (vault567) ; elle ne remplace la clé
    active qu'après un test réussi."""
    import uuid
    from . import extensions364 as ext
    from .vault567 import write
    value = str(value or '').strip()
    if not 8 <= len(value) <= 4096 or any(c in value for c in ('\n', '\r', '\x00', ' ', '\t')):
        raise Stop('secret_extension_invalide')
    target = ext._vault_dir(desk) / ('%s.cand-%s.secret' % (ext._identifier(eid), uuid.uuid4().hex[:12]))
    write(target, value)
    return str(target)


def _forget_file(desk, path):
    """Supprime un fichier du coffre des extensions (jamais en dehors)."""
    from pathlib import Path
    from . import extensions364 as ext
    if not path:
        return
    target = Path(path)
    try:
        if target.resolve().parent == ext._vault_dir(desk).resolve() and target.is_file():
            target.unlink()
    except OSError:
        pass


def _save(desk, item):
    item['revision'] = int(item.get('revision') or 0) + 1
    desk.setting('lawve:item:' + item['id'], item)
    desk.c.setdefault('lawve_extensions', {})[item['id']] = item
    return item


def connect(desk, pid, token='', auth='', allow_external=False, revision=None):
    """Enregistre (ou met à jour) le connecteur d'un fournisseur du catalogue ; l'adresse reste celle du catalogue.
    5.6.97 (J06) : une nouvelle clé devient une clé CANDIDATE au coffre (fichier versionné) ; la clé active reste en service tant que
    le test de la candidate n'a pas réussi (verify). ``revision`` : contrôle de la révision de la fiche (409 si elle a changé)."""
    from . import extensions364 as ext
    p = provider(pid)
    if not p.get('qualified'):
        raise Stop('source_a_verifier')
    auth = str(auth or p['default_auth'])
    if auth not in p['auth']:
        raise Stop('authentification_source_non_prise_en_charge')
    token = str(token or '').strip()
    if token and (len(token) > 4096 or re.search(r'[\s\x00]', token)):
        raise Stop('secret_extension_invalide')
    if token and auth in ('none', 'oauth2'):
        raise Stop('cle_non_attendue')
    eid = item_id(pid)
    previous = _item(desk, pid)
    if auth == 'oauth2' and (previous.get('auth_type') != 'oauth2' or not previous.get('secret_file')):
        raise Stop('source_connexion_par_compte')         # le jeton OAuth vient du parcours oauth_mcp5697, jamais d'un champ
    if revision not in (None, '') and int(revision) != int(previous.get('revision') or 0):
        raise Stop('revision_source_perimee')
    secret_file = str(previous.get('secret_file') or '')
    candidate = dict(previous.get('candidate') or {})
    if token:
        _forget_file(desk, candidate.get('file'))
        candidate = {'file': _vault_candidate(desk, eid, token), 'version': int(previous.get('secret_version') or 0) + 1,
                     'at': datetime.now(timezone.utc).isoformat()}
    if auth == 'bearer' and not secret_file and not candidate.get('file'):
        raise Stop('cle_source_absente')
    if auth == 'none':
        secret_file, candidate = '', {}
    integre = p['transport'] == 'integre'
    now = datetime.now(timezone.utc).isoformat()
    item = {'id': eid, 'kind': 'connector', 'author': 'axiorhub', 'slug': pid, 'provider': pid, 'name': p['name'],
            'source_url': p['help'], 'license': '', 'endpoint': ('integre:' + pid) if integre else ext.validate_endpoint(p['endpoint']),
            'transport': 'integre' if integre else 'streamable_http', 'auth_type': auth, 'secret_file': secret_file,
            'secret_version': int(previous.get('secret_version') or 0), 'candidate': candidate,
            'external_data_allowed': bool(allow_external), 'local_only': integre,
            'purposes': previous.get('purposes') or ['legal_analysis', 'hearing'], 'preferred_tools': dict(p['tools']),
            'enabled': bool(previous.get('enabled')) and bool(allow_external), 'status': previous.get('status') or 'configured',
            'created_at': previous.get('created_at') or now, 'updated_at': now, 'last_test': previous.get('last_test', {}),
            'schema_valide': previous.get('schema_valide', ''), 'revision': int(previous.get('revision') or 0),
            'catalogue_verified_on': VERIFIED_ON_5697, 'oauth': previous.get('oauth') or {}, 'cabinet': _cabinet(desk)}
    if integre:
        item['index_racine'] = _index_racine(desk)
    _save(desk, item)
    desk.audit('source_juridique_5632_enregistree', {'provider': pid, 'auth': auth, 'secret_candidate': bool(token),
                                                     'external_data_allowed': bool(allow_external)})
    public = ext._public(item)
    public.pop('candidate', None)
    public['candidate_pending'] = bool(candidate.get('file'))
    return public


# ------------------------------------------------------------------------------------------------ test : initialize puis tools/list
def _empreinte(tools):
    import hashlib
    import json
    canon = sorted(([str(t.get('name', '')), t.get('inputSchema') or t.get('input_schema') or {}] for t in tools if isinstance(t, dict)),
                   key=lambda x: x[0])
    return hashlib.sha256(json.dumps(canon, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _lister(item, appel, max_pages=20):
    """tools/list paginé ; un curseur répété ou une pagination interminable = catalogue incomplet, jamais présenté comme exhaustif."""
    tools, cursor, seen = [], None, set()
    for _ in range(max_pages):
        result = appel(item, 'tools/list', {'cursor': cursor} if cursor else {}) or {}
        rows = result.get('tools', []) if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise Stop('liste_outils_mcp_invalide')
        tools += [x for x in rows if isinstance(x, dict) and x.get('name')]
        cursor = result.get('nextCursor')
        if not cursor:
            return tools
        if cursor in seen:
            raise Stop('pagination_outils_mcp_incomplete')
        seen.add(cursor)
    raise Stop('pagination_outils_mcp_incomplete')


QUESTIONS_FICTIVES = {'FR': 'responsabilité civile délictuelle', 'UE': 'protection des données personnelles', 'DE-BUND': 'Kaufvertrag',
                      'ES-ETAT': 'fuentes del ordenamiento', 'GB-EAW': 'contract of sale', 'CA-FED': 'interpretation',
                      'CA-QC': 'préjudice', 'ENTREPRISES': '000010017'}
_REFUS = ('http_401', 'http_403', 'erreur_mcp_distante', 'outil_mcp_en_erreur')


def _essai(p, item, profondeur, appel):
    """Premier bouton : initialize puis tools/list (aucun outil exécuté). Second bouton (« complet ») : recherche publique fictive par
    capacité avec l'outil revu, sans donnée du dossier. Résultat conservé capacité par capacité, avec l'empreinte du schéma."""
    from . import mcp5614
    started = datetime.now(timezone.utc)
    try:
        init = appel(item, 'initialize', {'protocolVersion': mcp5614.PROTOCOL, 'capabilities': {}, 'clientInfo': {'name': 'AxiorHub'}}) or {}
        if not isinstance(init, dict) or not init.get('protocolVersion'):
            raise Stop('reponse_mcp_invalide')
        tools = _lister(item, appel)
    except Stop as ex:
        code = str(ex).split(':')[0]
        return {'status': 'error', 'error': code, 'message': 'Test impossible : %s.' % code.replace('_', ' '), 'levels': {'connection': 'error'},
                'capacites': {}, 'profondeur': profondeur}
    interdits = p.get('outils_interdits')
    if interdits:
        tools = [t for t in tools if not re.search(interdits, str(t.get('name', '')))]
    names = {str(t.get('name')) for t in tools}
    capacites = {}
    for cap in p['capabilities']:
        revus = tuple((p.get('outils_revus') or {}).get(cap) or ())
        capacites[cap] = 'outil_non_revu' if not revus else ('outil_present' if all(n in names for n in revus) else 'outil_absent')
    levels = {'connection': 'ok', 'search': 'untested', 'text': 'untested'}
    if profondeur == 'complet':
        question = QUESTIONS_FICTIVES.get(p.get('zone_guide', ''), 'question fictive')
        for cap, etat in list(capacites.items()):
            if etat != 'outil_present':
                continue
            tool = next(t for t in tools if t.get('name') == p['outils_revus'][cap][0])
            try:
                args = mcp5614.search_arguments(tool, question, 3)
                result = appel(item, 'tools/call', {'name': tool['name'], 'arguments': args}) or {}
                if isinstance(result, dict) and result.get('isError'):
                    raise Stop('outil_mcp_en_erreur')
                capacites[cap] = 'ok' if mcp5614.rows_from(result) else 'vide'
            except Stop as ex:
                capacites[cap] = 'refuse' if str(ex).split(':')[0] in _REFUS else 'erreur'
        tested = [v for v in capacites.values() if v in ('ok', 'vide', 'refuse', 'erreur')]
        levels['search'] = 'ok' if 'ok' in tested else ('empty' if 'vide' in tested else ('error' if tested else 'tool_missing'))
    return {'status': 'ok', 'message': 'Connexion établie (initialize, puis liste des outils) ; aucune donnée de dossier envoyée.',
            'protocol_version': str(init.get('protocolVersion', ''))[:20], 'server': init.get('serverInfo', {}) if isinstance(init.get('serverInfo'), dict) else {},
            'tools_count': len(tools), 'tools': [str(t.get('name', ''))[:100] for t in tools[:100]],
            'tool_specs': [{'name': str(t.get('name', ''))[:100], 'description': str(t.get('description', ''))[:500],
                            'inputSchema': t.get('inputSchema', {}) if isinstance(t.get('inputSchema', {}), dict) else {}} for t in tools[:100]],
            'schema_sha256': _empreinte(tools), 'capacites': capacites, 'levels': levels, 'profondeur': profondeur,
            'latency_ms': round((datetime.now(timezone.utc) - started).total_seconds() * 1000)}


def verify(desk, pid, profondeur='connexion', appel=None):
    """Test séparé de l'activation (J06) : la clé candidate est testée ; succès = publication (version du secret + 1, révision
    contrôlée), échec = la clé active reste. Schéma des outils modifié depuis l'activation = source désactivée, à revalider."""
    from . import extensions364 as ext
    p = provider(pid)
    if not p.get('qualified'):
        raise Stop('source_a_verifier')
    item = _item(desk, pid)
    if not item:
        raise Stop('source_juridique_non_configuree')
    profondeur = 'complet' if profondeur == 'complet' else 'connexion'
    item['cabinet'] = _cabinet(desk)
    if p['transport'] == 'integre':
        item['index_racine'] = _index_racine(desk)
    candidate = item.get('candidate') or {}
    essai = dict(item)
    if candidate.get('file'):
        essai['secret_file'] = candidate['file']
    if essai.get('auth_type') == 'oauth2' and not essai.get('secret_file'):
        raise Stop('reconnexion_oauth_requise')
    if essai.get('auth_type') == 'bearer' and not essai.get('secret_file'):
        raise Stop('cle_source_absente')
    if essai.get('auth_type') == 'oauth2':
        from . import oauth_mcp5697
        essai['secret_file'] = oauth_mcp5697.jeton_valide(desk, pid)
    result = _essai(p, essai, profondeur, appel or ext._mcp_call)
    current = _item(desk, pid)
    if int(current.get('revision') or 0) != int(item.get('revision') or 0):
        if candidate.get('file') and (current.get('candidate') or {}).get('file') != candidate['file']:
            _forget_file(desk, candidate['file'])
        raise Stop('revision_source_perimee')
    now = datetime.now(timezone.utc).isoformat()
    out = {'provider': pid, 'status': result['status'], 'message': result.get('message'), 'levels': result.get('levels') or {},
           'tools_count': result.get('tools_count', 0), 'error': result.get('error', ''), 'capacites': result.get('capacites', {}),
           'profondeur': profondeur}
    if candidate.get('file'):
        if result['status'] != 'ok':
            _forget_file(desk, candidate['file'])
            item['candidate'] = {}
            item['candidate_refusee'] = {'at': now, 'error': result.get('error', '')}
            _save(desk, item)
            desk.audit('source_juridique_5697_cle_refusee', {'provider': pid, 'error': result.get('error', '')})
            out['cle_candidate_refusee'] = True
            out['message'] = 'Nouvelle clé refusée : la clé active reste en service.' if item.get('secret_file') else 'Clé refusée.'
            return out
        old = item.get('secret_file')
        item['secret_file'] = candidate['file']
        item['secret_version'] = int(candidate.get('version') or int(item.get('secret_version') or 0) + 1)
        item['candidate'] = {}
        if old and old != candidate['file']:
            _forget_file(desk, old)
    result['secret_version'] = int(item.get('secret_version') or 0)
    if result['status'] == 'ok' and item.get('enabled') and item.get('schema_valide') and item['schema_valide'] != result.get('schema_sha256'):
        item['enabled'] = False
        item['status'] = 'schema_modifie'
        out['message'] = 'Les outils de la source ont changé depuis l’activation : la source est désactivée, revoyez-la puis réactivez-la.'
        out['schema_modifie'] = True
    elif result['status'] == 'ok':
        item['status'] = 'tested' if not item.get('enabled') else 'enabled'
    item['last_test'] = {**result, 'at': now}
    _save(desk, item)
    desk.audit('test_extension_lawve', {'id': item['id'], 'status': result['status'], 'error': result.get('error', ''),
                                        'secret_version': result['secret_version']})
    out['secret_version'] = result['secret_version']
    return out


def enable(desk, pid, on=True, purposes=None):
    """Activation explicite (J06) : choisit l'usage et le droit d'envoyer une requête ; le test seul n'active rien. Exige un test
    réussi avec la version actuelle du secret ; fige l'empreinte du schéma testé (comparée à chaque test suivant)."""
    from . import extensions364 as ext
    p = provider(pid)
    if on:
        if not p.get('qualified'):
            raise Stop('source_a_verifier')
        item = _item(desk, pid)
        if not item:
            raise Stop('source_juridique_non_configuree')
        test = item.get('last_test') or {}
        if (item.get('candidate') or {}).get('file'):
            raise Stop('cle_candidate_a_tester')
        if test.get('status') != 'ok' or int(test.get('secret_version') or 0) != int(item.get('secret_version') or 0):
            raise Stop('test_extension_requis')
        usages = [u for u in (purposes or item.get('purposes') or ['legal_analysis', 'hearing']) if u in ('legal_analysis', 'hearing', 'veille')]
        item['purposes'] = usages or ['legal_analysis']
        item['schema_valide'] = test.get('schema_sha256', '')
        _save(desk, item)
    return ext.set_enabled(desk, item_id(pid), on, reviewed=True)


def enabled_items(desk):
    return [i for i in (desk.c.get('lawve_extensions') or {}).values()
            if isinstance(i, dict) and i.get('kind') == 'connector' and i.get('enabled') and i.get('provider') in PROVIDERS]


def tool_for(item, capability, tools):
    """Outil déclaré par le catalogue pour la capacité, s'il existe réellement dans la liste renvoyée par le serveur."""
    wanted = (item.get('preferred_tools') or {}).get(capability)
    interdits = (PROVIDERS.get(item.get('provider')) or {}).get('outils_interdits')
    if wanted and not (interdits and re.search(interdits, wanted)):
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


def liens_guide():
    """Liens du guide (J06) : ceux des fiches, jamais une liste codée ailleurs ; adresse d'accès sinon aide, HTTPS seulement."""
    out = []
    for p in PROVIDERS.values():
        url = p.get('access_url') or p.get('help') or ''
        if url.startswith('https://'):
            out.append((p['name'], url))
    return tuple(out)

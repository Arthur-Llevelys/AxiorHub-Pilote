"""5.6.108 (lot B) : bibliothèque d'actes par droit applicable — systèmes juridiques en liste fermée, qualification des modèles, cycle de vie,
sélection filtrée.

Principes (cahier 5.6.107, « Bibliothèque par droit applicable » et « Qualifier les modèles et les résultats ») :
  - le système juridique est une donnée du modèle et du dossier, jamais déduite de la langue : un acte en anglais peut être canadien, un acte en
    français n'est pas forcément français ; « CA », « UK » ou « EN » seuls ne suffisent pas pour une rédaction automatique ;
  - l'ancien code « EN » de la bibliothèque visait l'Angleterre et le pays de Galles : il est migré vers « UK-EAW » par un alias EXPLICITE, réservé
    aux données enregistrées (une demande qui dit « EN » reste à préciser) ;
  - chaque modèle porte ses métadonnées (version, droit territorial, langue, type d'acte, profils des parties, auteur, provenance, licence,
    sources de contrôle, validation, date de réexamen distincte de la date de validation, période d'applicabilité, champs requis, options,
    incompatibilités) ;
  - cycle de vie : brouillon, à relire, validé, à revoir, retiré (états du registre ``modeles5684``) ; l'IA ne peut jamais se déclarer validatrice ;
  - les textes officiels cités sont des SOURCES DE CONTRÔLE, pas des contrats tout prêts ; aucun modèle étranger n'est livré « validé ».

Ce module ne fait aucun appel réseau : les adresses des sources officielles sont affichées, jamais interrogées.
"""
from datetime import date, timedelta
import json
import re

from .common import Stop, fold

# ------------------------------------------------------------------------------------------------ systèmes juridiques (liste fermée)
# code : (libellé, langues usuelles des actes, source officielle de contrôle (nom, adresse), précision exigée avant une rédaction automatique)
_FED = ('Lois du Canada (Justice Laws)', 'https://laws-lois.justice.gc.ca/')
SYSTEMES = {
    'FR': ('Droit français', ('fr',), ('Légifrance', 'https://www.legifrance.gouv.fr/')),
    'UK-EAW': ('Droit anglais (Angleterre et pays de Galles)', ('en',), ('legislation.gov.uk', 'https://www.legislation.gov.uk/')),
    'DE': ('Droit allemand', ('de',), ('Gesetze im Internet', 'https://www.gesetze-im-internet.de/')),
    'ES': ('Droit espagnol', ('es',), ('BOE (Boletín Oficial del Estado)', 'https://www.boe.es/')),
    'CA-QC': ('Droit québécois (Canada)', ('fr', 'en'), ('LégisQuébec', 'https://www.legisquebec.gouv.qc.ca/')),
    'CA-ON': ('Droit de l’Ontario (Canada)', ('en', 'fr'), ('Lois de l’Ontario (Lois-en-ligne)', 'https://www.ontario.ca/laws')),
    'CA-BC': ('Droit de la Colombie-Britannique (Canada)', ('en',), ('BC Laws', 'https://www.bclaws.gov.bc.ca/')),
    'CA-AB': ('Droit de l’Alberta (Canada)', ('en',), ('Recueil officiel des lois de l’Alberta', '')),
    'CA-SK': ('Droit de la Saskatchewan (Canada)', ('en',), ('Recueil officiel des lois de la Saskatchewan', '')),
    'CA-MB': ('Droit du Manitoba (Canada)', ('en', 'fr'), ('Recueil officiel des lois du Manitoba', '')),
    'CA-NB': ('Droit du Nouveau-Brunswick (Canada)', ('en', 'fr'), ('Recueil officiel des lois du Nouveau-Brunswick', '')),
    'CA-NS': ('Droit de la Nouvelle-Écosse (Canada)', ('en',), ('Recueil officiel des lois de la Nouvelle-Écosse', '')),
    'CA-PE': ('Droit de l’Île-du-Prince-Édouard (Canada)', ('en',), ('Recueil officiel des lois de l’Île-du-Prince-Édouard', '')),
    'CA-NL': ('Droit de Terre-Neuve-et-Labrador (Canada)', ('en',), ('Recueil officiel des lois de Terre-Neuve-et-Labrador', '')),
    'CA-YT': ('Droit du Yukon (Canada)', ('en', 'fr'), ('Recueil officiel des lois du Yukon', '')),
    'CA-NT': ('Droit des Territoires du Nord-Ouest (Canada)', ('en', 'fr'), ('Recueil officiel des lois des Territoires du Nord-Ouest', '')),
    'CA-NU': ('Droit du Nunavut (Canada)', ('en', 'fr'), ('Recueil officiel des lois du Nunavut', '')),
    'CA-FED': ('Droit fédéral canadien (périmètre fédéral seulement)', ('fr', 'en'), _FED),
}
LIBELLES = {k: v[0] for k, v in SYSTEMES.items()}
PRIORITAIRES = ('FR', 'UK-EAW', 'DE', 'ES', 'CA-QC', 'CA-ON')
# Canada : province (ou seul périmètre fédéral) fixée avant le modèle ; jamais retenue sur la seule base d'un réglage par défaut
EXPLICITES = frozenset(k for k in SYSTEMES if k.startswith('CA-'))
AMBIGUS = {
    'CA': 'Canada : précisez la province (CA-QC, CA-ON…) ou le seul périmètre fédéral (CA-FED).',
    'UK': 'Royaume-Uni : précisez le système (Angleterre et pays de Galles = UK-EAW ; l’Écosse et l’Irlande du Nord ne sont pas couvertes).',
    'GB': 'Royaume-Uni : précisez le système (Angleterre et pays de Galles = UK-EAW ; l’Écosse et l’Irlande du Nord ne sont pas couvertes).',
    'EN': '« EN » désigne une langue, pas un droit : précisez le système (Angleterre et pays de Galles = UK-EAW, Ontario = CA-ON…).',
}
ALIAS_MIGRATION = {'EN': 'UK-EAW'}   # ancien code de la bibliothèque (5.6.39 à 5.6.107) : England & Wales — seulement pour les données enregistrées
LANGUE_PRINCIPALE = {k: v[1][0] for k, v in SYSTEMES.items()}
TAG_LANGUE = {('fr', 'CA'): 'fr-CA', ('en', 'CA'): 'en-CA', ('fr', ''): 'fr-FR', ('en', ''): 'en-GB', ('de', ''): 'de-DE', ('es', ''): 'es-ES'}

# ------------------------------------------------------------------------------------------------ cycle de vie (états du registre modeles5684)
ETATS = {'draft': 'Brouillon', 'review': 'À relire', 'validated': 'Validé', 'needs_update': 'À revoir', 'withdrawn': 'Retiré'}
USAGES = {'draft': 'Travail éditorial ; pas de sélection automatique.',
          'review': 'Revue du contenu et des hypothèses d’emploi par une personne compétente dans le droit concerné.',
          'validated': 'Utilisation pour le périmètre et la période approuvés.',
          'needs_update': 'Sélection automatique bloquée ; nouvelle validation explicite selon le motif.',
          'withdrawn': 'Historique conservé, plus de nouveaux actes.'}
REEXAMEN_JOURS = 365    # date de réexamen proposée à la validation (modifiable) : un an après la validation
IA_IDENTITES = frozenset({'ia', 'agent', 'pilote', 'serveur', 'regles', 'migration', 'automate', 'systeme', 'system', 'modele', 'model', 'llm',
                          'axiorhub', 'assistant-ia', 'robot', 'claude', 'chatgpt', 'mistral', 'ollama', 'mcp', 'connecteur'})
IA_PREFIXES = ('ia:', 'agent:', 'pilote:', 'claude:', 'chatgpt:', 'mcp:', 'modele:', 'model:', 'llm:', 'connecteur:', 'job:')

# ------------------------------------------------------------------------------------------------ types d'acte
TYPES = {
    'statuts': 'Statuts', 'pacte': 'Pacte d’associés ou d’actionnaires', 'lettre-intention': 'Lettre d’intention', 'protocole': 'Protocole d’accord',
    'compromis': 'Compromis', 'cession-titres': 'Cession de titres', 'cession-fonds': 'Cession de fonds de commerce', 'cession-bail': 'Cession de bail',
    'prestation-b2b': 'Prestation de services entre professionnels', 'confidentialite': 'Accord de confidentialité', 'cgv-b2b': 'Conditions générales (B2B)',
    'pret-associe': 'Prêt ou compte courant d’associé', 'convention-management': 'Convention de prestations intragroupe',
    'questionnaire-mission': 'Questionnaire de mission', 'note-consultation': 'Note de consultation avec options',
    'suivi-obligations': 'Suivi des obligations', 'convention-honoraires': 'Convention d’honoraires', 'lettre-mission': 'Lettre de mission',
    'piece-operation': 'Pièce d’une opération', 'formalite': 'Formalité', 'autre': 'Autre',
}
_TYPE_PAR_ID = {
    'pacte_associes': 'pacte', 'pacte_actionnaires': 'pacte', 'lettre_intention': 'lettre-intention', 'protocole_conditions': 'protocole',
    'compromis_fonds': 'compromis', 'cession_titres': 'cession-titres', 'cession_fonds': 'cession-fonds', 'contrat_prestation': 'prestation-b2b',
    'accord_confidentialite': 'confidentialite', 'cgv_b2b': 'cgv-b2b', 'compte_courant': 'pret-associe', 'convention_management': 'convention-management',
    'de_gesellschaftervereinbarung': 'pacte', 'de_anteilskaufvertrag': 'cession-titres', 'de_absichtserklaerung': 'lettre-intention',
    'de_dienstleistungsvertrag': 'prestation-b2b', 'de_geheimhaltung': 'confidentialite', 'de_agb_b2b': 'cgv-b2b', 'de_gesellschafterdarlehen': 'pret-associe',
    'es_pacto_socios': 'pacte', 'es_compraventa_participaciones': 'cession-titres', 'es_carta_intenciones': 'lettre-intention',
    'es_prestacion_servicios': 'prestation-b2b', 'es_confidencialidad': 'confidentialite', 'es_condiciones_generales': 'cgv-b2b',
    'es_prestamo_socio': 'pret-associe', 'en_shareholders_agreement': 'pacte', 'en_spa': 'cession-titres', 'en_heads_of_terms': 'lettre-intention',
    'en_services_agreement': 'prestation-b2b', 'en_nda': 'confidentialite', 'en_terms_sale': 'cgv-b2b', 'en_shareholder_loan': 'pret-associe',
    'convention_honoraires': 'convention-honoraires', 'lettre_mission': 'lettre-mission', 'acte_cession_fonds': 'cession-fonds',
    'acte_cession_bail': 'cession-bail', 'publication_bodacc': 'formalite', 'mutation_2672': 'formalite', 'etat_2676': 'formalite',
    'formalites_entreprise': 'formalite', 'enregistrement_bail': 'formalite',
}
_PROFILS_PAR_TYPE = {
    'statuts': ['société', 'associés'], 'pacte': ['associés ou actionnaires', 'société'], 'lettre-intention': ['acquéreur', 'cédant'],
    'protocole': ['partie A', 'partie B'], 'compromis': ['cédant', 'cessionnaire'], 'cession-titres': ['cédant', 'cessionnaire', 'société'],
    'cession-fonds': ['cédant', 'cessionnaire'], 'cession-bail': ['cédant', 'cessionnaire', 'bailleur'],
    'prestation-b2b': ['prestataire professionnel', 'client professionnel'], 'confidentialite': ['partie divulgatrice', 'partie destinataire'],
    'cgv-b2b': ['vendeur professionnel', 'acheteurs professionnels'], 'pret-associe': ['associé prêteur', 'société emprunteuse'],
    'convention-management': ['société mère', 'filiale'], 'questionnaire-mission': ['client', 'cabinet'], 'note-consultation': ['client', 'cabinet'],
    'suivi-obligations': ['parties au contrat suivi'], 'convention-honoraires': ['client', 'cabinet'], 'lettre-mission': ['client', 'cabinet'],
}
AUTEUR_AXIORHUB = 'AxiorHub (rédaction originale)'
PROVENANCE_AXIORHUB = 'Bibliothèque intégrée d’AxiorHub : trame rédigée pour ce droit, sans reprise de texte protégé'
LICENCE_AXIORHUB = 'Fournie avec AxiorHub ; adaptation libre par le cabinet pour ses dossiers'
_ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def libelle(code):
    code = migrer(code)
    return LIBELLES.get(code, code or 'droit à préciser')


def migrer(code):
    """Code ENREGISTRÉ (bibliothèque, réglage, registre) : l'ancien « EN » devient « UK-EAW » ; les autres codes sont inchangés."""
    code = str(code or '').strip()
    return ALIAS_MIGRATION.get(code, code)


def systeme_requis(code):
    """Système demandé pour une rédaction ou une sélection : code de la liste fermée, sinon ``Stop('systeme_juridique_a_preciser')``."""
    code = str(code or '').strip().upper()
    if code in SYSTEMES:
        return code
    raise Stop('systeme_juridique_a_preciser')


def source_officielle(code):
    nom, url = SYSTEMES.get(migrer(code), ('', (), ('', '')))[2]
    return {'nom': nom, 'url': url}


def sources_controle(code):
    """Sources officielles de contrôle d'un système (texte lisible) ; une province canadienne ajoute le droit fédéral pertinent."""
    code = migrer(code)
    out = []
    src = source_officielle(code)
    if src['nom']:
        out.append('%s%s' % (src['nom'], (' — ' + src['url']) if src['url'] else ''))
    if code.startswith('CA-') and code != 'CA-FED':
        out.append('%s — %s (droit fédéral pertinent)' % _FED)
    return out


# ------------------------------------------------------------------------------------------------ lecture d'un droit en texte libre
_TERMES = [   # (motif sur texte replié, code) ; les plus précis d'abord
    (r'\bca-?qc\b|\bquebec\b|\bquebecois(e)?\b', 'CA-QC'), (r'\bca-?on\b|\bontario\b', 'CA-ON'),
    (r'\bca-?bc\b|colombie-britannique|british columbia', 'CA-BC'), (r'\bca-?ab\b|\balberta\b', 'CA-AB'),
    (r'\bca-?sk\b|\bsaskatchewan\b', 'CA-SK'), (r'\bca-?mb\b|\bmanitoba\b', 'CA-MB'),
    (r'\bca-?nb\b|nouveau-brunswick|new brunswick', 'CA-NB'), (r'\bca-?ns\b|nouvelle-ecosse|nova scotia', 'CA-NS'),
    (r'\bca-?pe\b|ile-du-prince-edouard|prince edward island', 'CA-PE'), (r'\bca-?nl\b|terre-neuve|newfoundland', 'CA-NL'),
    (r'\bca-?yt\b|\byukon\b', 'CA-YT'), (r'\bca-?nt\b|territoires du nord-ouest|northwest territories', 'CA-NT'),
    (r'\bca-?nu\b|\bnunavut\b', 'CA-NU'), (r'\bca-?fed\b|\bfederal canadien\b|droit federal du canada|federal law of canada|canadian federal', 'CA-FED'),
    (r'\buk-?eaw\b|\bengland\b|\bangleterre\b|pays de galles|\bwales\b|\benglish law\b|\bdroit anglais\b|\benglisches recht\b|derecho ingles', 'UK-EAW'),
    (r'\bfrance\b|\bfrancais(e)?\b|\bdroit francais\b|french law|franzosisches recht|derecho frances', 'FR'),
    (r'\ballemagne\b|\ballemand\b|\bdeutschland\b|\bdeutsche(s|m|n)? recht\b|german law|derecho aleman', 'DE'),
    (r'\bespagne\b|\bespagnol\b|\bespana\b|\bespanol(a)?\b|spanish law|spanisches recht', 'ES'),
    (r'\bcanada\b|\bcanadien(ne)?\b|\bcanadian\b', 'CA'), (r'royaume-uni|united kingdom|\bbritannique\b|\bbritish\b|grande-bretagne', 'UK'),
]
_CODES_SEULS = {'fr': 'FR', 'de': 'DE', 'es': 'ES', 'ca': 'CA', 'uk': 'UK', 'gb': 'UK', 'en': 'EN', 'uk-eaw': 'UK-EAW', 'ca-fed': 'CA-FED'}


def lire(valeur):
    """Droit lu dans une valeur libre (fiche du dossier, métadonnée, réglage) : ``{'code', 'statut', 'precision'}``.

    statut : ``ok`` (code de la liste fermée), ``ambigu`` (CA, UK, EN, ou plusieurs droits à la fois), ``inconnu`` (droit non couvert), ``vide``."""
    brut = str(valeur or '').strip()
    if not brut:
        return {'code': '', 'statut': 'vide', 'precision': ''}
    if brut.upper() in SYSTEMES:
        return {'code': brut.upper(), 'statut': 'ok', 'precision': ''}
    texte = fold(brut).replace('’', "'")
    seul = _CODES_SEULS.get(texte.strip())
    if seul:
        return ({'code': seul, 'statut': 'ok', 'precision': ''} if seul in SYSTEMES else
                {'code': seul, 'statut': 'ambigu', 'precision': AMBIGUS[seul]})
    trouves = []
    for motif, code in _TERMES:
        if re.search(motif, texte) and code not in trouves:
            trouves.append(code)
    precis = [c for c in trouves if c in SYSTEMES]
    if len(precis) == 1:   # « Canada (Québec) » : la province l'emporte sur le pays
        return {'code': precis[0], 'statut': 'ok', 'precision': ''}
    if len(precis) > 1:
        return {'code': '', 'statut': 'ambigu', 'precision': 'Plusieurs droits mentionnés (%s) : précisez celui de l’acte.' % ', '.join(precis)}
    if trouves:
        return {'code': trouves[0], 'statut': 'ambigu', 'precision': AMBIGUS[trouves[0]]}
    return {'code': '', 'statut': 'inconnu', 'precision': 'Droit « %s » non couvert par la bibliothèque : choisissez le modèle à la main.' % brut[:60]}


# Désignations d'un droit dans une demande : le mot « droit / loi / law / Recht / derecho » doit accompagner le pays ou la province ; « en
# français », « in English » ou le nom d'une ville ne désignent jamais un droit.
_MENTIONS = [
    ('CA-QC', r"quebec|quebecois(?:e)?"), ('CA-ON', r"ontario"), ('CA-BC', r"colombie-britannique|british columbia"), ('CA-AB', r"alberta"),
    ('CA-SK', r"saskatchewan"), ('CA-MB', r"manitoba"), ('CA-NB', r"nouveau-brunswick|new brunswick"), ('CA-NS', r"nouvelle-ecosse|nova scotia"),
    ('CA-PE', r"ile-du-prince-edouard|prince edward island"), ('CA-NL', r"terre-neuve(?:-et-labrador)?|newfoundland(?: and labrador)?"),
    ('CA-YT', r"yukon"), ('CA-NT', r"territoires du nord-ouest|northwest territories"), ('CA-NU', r"nunavut"),
    ('CA-FED', r"federal canadien|federal du canada|federal law of canada|federal laws of canada|canadian federal"),
    ('UK-EAW', r"anglais|angleterre(?: et (?:du )?pays de galles)?|england(?: and wales)?|english|englisch(?:en|em|es)?|ingles"),
    ('FR', r"francais(?:e)?|france|french|franzosisch(?:en|em|es)?|frances(?:a)?"),
    ('DE', r"allemand(?:e)?|allemagne|german(?:y)?|deutsch(?:en|em|es)?|aleman(?:a)?"),
    ('ES', r"espagnol(?:e)?|espagne|spain|spanish|spanisch(?:en|em|es)?|espanol(?:a)?|espana"),
    ('CA', r"canadien(?:ne)?|canada|canadian"), ('UK', r"britannique|royaume-uni|united kingdom|british"),
]
_AVANT = (r"\b(?:droit|loi|lois|legislation|law|laws|recht|derecho|ley|leyes)\s+(?:applicable\s+)?(?:(?:du|de la|de l'|des|d'|de|of|of the|"
          r"of the province of|del|de la provincia de)\s*)?(?:%s)\b")
_APRES = r"\b(?:%s)\s+(?:law|laws|recht)\b"


def droit_mentionne(demande):
    """Droit EXPLICITEMENT désigné dans une demande (« droit québécois », « laws of the Province of Ontario », « nach deutschem Recht ») ; la seule
    langue de la demande ou un nom de ville ne désigne jamais un droit. Renvoie le résultat de ``lire`` ou ``None``."""
    texte = re.sub(r'\s+', ' ', fold(str(demande or '')).replace('’', "'"))
    vus = [code for code, termes in _MENTIONS if re.search(_AVANT % termes, texte) or re.search(_APRES % termes, texte)]
    if not vus:
        return None
    precis = [c for c in vus if c in SYSTEMES]
    if len(precis) > 1:
        return {'code': '', 'statut': 'ambigu', 'precision': 'Plusieurs droits mentionnés (%s) : précisez celui de l’acte.' % ', '.join(precis)}
    if precis:
        return {'code': precis[0], 'statut': 'ok', 'precision': ''}
    return {'code': vus[0], 'statut': 'ambigu', 'precision': AMBIGUS[vus[0]]}


# ------------------------------------------------------------------------------------------------ identité de l'auteur d'une validation
def est_identite_ia(owner):
    """Vrai pour une identité d'automate ou de modèle d'IA : elle ne peut ni valider ni changer l'état d'un modèle."""
    o = str(owner or '').strip().lower()
    return not o or o in IA_IDENTITES or o.startswith(IA_PREFIXES)


# ------------------------------------------------------------------------------------------------ qualification des modèles
def type_acte(t):
    if t.get('type_acte'):
        return t['type_acte']
    ident = str(t.get('id') or '')
    if ident in _TYPE_PAR_ID:
        return _TYPE_PAR_ID[ident]
    if t.get('categorie') == 'statuts':
        return 'statuts'
    if t.get('famille') in ('fonds', 'bail', 'commun'):
        return 'piece-operation'
    return 'autre'


def qualification(t):
    """Métadonnées d'un modèle de la bibliothèque (modèle d'origine ou modèle du cabinet), sans rien écrire.

    Les trames d'origine sont qualifiées à l'état « brouillon » : seule une personne habilitée les fait passer à « à relire » puis « validé ».
    ``selection_assistee`` : une trame livrée AVANT la 5.6.108 reste proposable comme base d'un projet à relire (comportement existant) tant
    qu'elle n'est ni retirée ni à revoir ; une trame nouvelle en brouillon n'est jamais proposée automatiquement."""
    systeme = migrer(t.get('droit'))
    ta = type_acte(t)
    origine = t.get('origine') or ('cabinet' if str(t.get('id') or '').startswith('cab_') else 'axiorhub')
    sources = list(t.get('sources') or [])
    for s in sources_controle(systeme):
        if s not in sources:
            sources.append(s)
    requis = [k for k, _, d in (t.get('variables') or []) if not str(d or '').strip()]
    return {
        'version': 1, 'systeme': systeme, 'langue': t.get('langue') or LANGUE_PRINCIPALE.get(systeme, 'fr'), 'type_acte': ta,
        'profils_parties': list(t.get('profils') or _PROFILS_PAR_TYPE.get(ta, [])),
        'auteur': AUTEUR_AXIORHUB if origine == 'axiorhub' else 'cabinet',
        'provenance': PROVENANCE_AXIORHUB if origine == 'axiorhub' else 'Modèle ajouté par le cabinet',
        'licence': LICENCE_AXIORHUB if origine == 'axiorhub' else 'Modèle du cabinet (droits d’utilisation à confirmer par le cabinet)',
        'sources': sources, 'etat_initial': 'draft', 'valide_par': '', 'valide_le': '', 'reexamen_le': '',
        'applicable_depuis': str(t.get('applicable_depuis') or ''), 'applicable_jusquau': str(t.get('applicable_jusquau') or ''),
        'champs_requis': list(t.get('champs_requis') or requis), 'options': [k for k, _, _, _ in (t.get('clauses') or [])],
        'incompatibilites': [list(p) for p in (t.get('incompatibilites') or [])],
        'selection_assistee': not t.get('nouveau56108'),
    }


def incompatibilites_actives(t, flags):
    """Couples de clauses incompatibles cochés ensemble (métadonnée du modèle) : libellés lisibles, vide si rien."""
    labels = {k: lab for k, lab, _, _ in (t.get('clauses') or [])}
    out = []
    for pair in t.get('incompatibilites') or []:
        a, b = (list(pair) + ['', ''])[:2]
        if flags.get(a) and flags.get(b):
            out.append('%s / %s' % (labels.get(a, a), labels.get(b, b)))
    return out


def date_iso(value):
    """Date AAAA-MM-JJ valide, sinon ''."""
    v = str(value or '').strip()[:10]
    if not _ISO.match(v):
        return ''
    try:
        date.fromisoformat(v)
    except ValueError:
        return ''
    return v


def reexamen_par_defaut(valide_le):
    try:
        return (date.fromisoformat(str(valide_le)[:10]) + timedelta(days=REEXAMEN_JOURS)).isoformat()
    except ValueError:
        return ''


def jour_du_cabinet(desk, owner='cabinet'):
    """Jour du cabinet (fuseau du cabinet), jamais ``date.today()`` du serveur."""
    try:
        from .acces5683 import today_of
        return today_of(desk, owner)[0].isoformat()
    except Exception:   # noqa: BLE001
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Europe/Paris')).date().isoformat()


# ------------------------------------------------------------------------------------------------ sélection déterministe
def ecart_autonome(m, systeme, langue, type_acte_, au):
    """Motif d'exclusion d'une version de modèle pour une sélection AUTONOME, '' si elle est retenable. ``m`` : ligne du registre (dict)."""
    if systeme not in SYSTEMES or (m.get('systeme') or '') != systeme:
        return 'autre_droit'
    if langue and (m.get('language') or '') != langue:
        return 'autre_langue'
    if type_acte_ and (m.get('document_type') or '') != type_acte_:
        return 'autre_type'
    if m.get('state') != 'validated':
        return 'non_valide'
    if not m.get('validated_by') or est_identite_ia(m.get('validated_by')) or not date_iso(m.get('review_date')):
        return 'validation_non_tracee'
    sources = m.get('legal_sources') or '[]'
    if not (json.loads(sources) if isinstance(sources, str) else sources):
        return 'sans_source'
    if date_iso(m.get('review_date')) > au:
        return 'validation_posterieure'
    reex = date_iso(m.get('reexamen_le'))
    if not reex or reex < au:
        return 'reexamen_depasse'
    debut, fin = date_iso(m.get('applicable_depuis')), date_iso(m.get('applicable_jusquau'))
    if (debut and au < debut) or (fin and au > fin):
        return 'hors_periode'
    return ''


def choisir(lignes, systeme, langue, type_acte_, au, actifs=None):
    """Sélection autonome parmi les versions courantes du registre : filtre bloquant droit / langue / type / état / validation / réexamen /
    applicabilité ; plusieurs candidats de même date de validation : la version active désignée dans le registre, sinon refus explicite (jamais de
    départage sur un numéro de version écrit en texte)."""
    systeme = systeme_requis(systeme)
    candidats = [m for m in lignes if not ecart_autonome(m, systeme, langue, type_acte_, au)]
    if not candidats:
        raise Stop('modele_juridique_valide_absent')
    actif = (actifs or {}).get((systeme, langue or '', type_acte_ or ''))
    if actif:
        designes = [m for m in candidats if (m['template_id'], int(m['version'])) == (actif[0], int(actif[1]))]
        if designes:
            return designes[0]
    derniere = max(date_iso(m['review_date']) for m in candidats)
    derniers = [m for m in candidats if date_iso(m['review_date']) == derniere]
    if len(derniers) != 1:
        raise Stop('plusieurs_modeles_valides_a_departager')
    return derniers[0]


# ------------------------------------------------------------------------------------------------ schéma et migration (au démarrage)
def ensure_schema(desk):
    """Au démarrage (``Desk``), jamais pendant une lecture : colonnes de qualification du registre, table des versions actives, migration de
    l'ancien code « EN » (modèles du cabinet, réglage du droit par défaut, registre) et métadonnées des versions déjà enregistrées.
    Idempotent ; ne remplace jamais une valeur saisie par le cabinet."""
    from . import modeles5684, bibliotheque5639
    bibliotheque5639.schema(desk)
    modeles5684.schema(desk)
    modeles5684.migrer56108(desk)

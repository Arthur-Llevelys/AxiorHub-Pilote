"""5.6.108 (lot K) : mode « conseil de modèles » (LLM council) facultatif, pour les questions de jugement.

Décision du cabinet (18/11/2026) : un mode facultatif, construit dans la chaîne existante, **piloté par le programme** (aucune bibliothèque
d'agents : ni CrewAI, ni AutoGen), mesuré par le banc d'essai. Le cahier le rappelle : un prompt différent sur le même modèle n'est pas un
contrôle indépendant, deux modèles différents peuvent répéter une erreur, la valeur vient des preuves. Le conseil n'est donc qu'un moyen de
faire apparaître les désaccords et les manques ; la barrière des références (lot R) reste le contrôle.

- **Usage** : questions de jugement seulement (consultation, « Examiner les arguments », question de stratégie ou d'appréciation du risque),
  à la demande de l'avocat (bouton « Demander l'avis du conseil » après l'annonce : membres, appels, durée et coût estimés), jamais
  automatiquement. Annulable ; pause, politique des données et budget communs entre chaque appel (``chaine5689.appeler_texte``).
- **Membres** : 2 ou 3 modèles et un président, choisis dans Paramètres › IA parmi les modèles réellement disponibles. Des **préréglages**
  déclaratifs (``PREREGLAGES``) décrivent les membres par famille et taille avec une liste ordonnée de noms acceptés ; la page compare chaque
  préréglage aux modèles installés (``modeles5667.installed``, jamais un modèle « :cloud ») et aux fournisseurs d'API configurés. Tant que
  l'administrateur n'a rien choisi, le premier préréglage disponible (Local équilibré → Mixte → Local léger → API ; en mode local strict, les
  préréglages locaux seulement) s'applique. Chaque membre passe par la politique des données (pseudonymisation d'un fournisseur externe dans
  ``model.Model`` ; mode local strict : membres locaux seulement).
- **Déroulé**, l'un après l'autre (jamais deux générations en parallèle) : mêmes sources numérotées ``[S1]…`` pour chaque membre (recherche
  d'office faite par le programme, lot R) ; réponses indépendantes ; revue croisée anonyme (étiquettes A, B, C, ordre mélangé, noms de modèles
  masqués), critique structurée et courte ; synthèse du président qui expose les désaccords tels quels (le programme ajoute en outre le texte
  exact des critiques) ; barrière des références sur la synthèse. Un membre en échec : le conseil continue et le dit ; un seul membre restant :
  réponse simple signalée. Aucun pourcentage de chance de succès : un chiffre de ce genre est retiré par le programme et compté.
- **Trace** (``conseil56108``) : membres (fournisseur, modèle, version), étapes, durées, coût, désaccords, références marquées ; réponses
  individuelles et critiques consultables, dépliées sur demande.

**Point d'entrée du banc d'essai** : ``deliberer(question, sources, membres, president, appeler, connecteurs=None, desk=None, …) -> dict``,
avec un transport injecté ``appeler(membre, messages, max_tokens, schema=None) -> str``.
"""
from datetime import datetime, timezone
import hashlib
import html
import json
import random
import re
import secrets
import sqlite3
import time

from .common import Stop, fold

SETTING = 'conseil56108:reglages'
PREFIXE = 'm56108/conseil/'
LECTURES_GET = ('annonce', 'trace', 'reglages')
ECRITURES = ('lancer', 'reglages')
ROLES = ('administrateur', 'avocat', 'assistant')
MIN_MEMBRES, MAX_MEMBRES = 2, 3
ETIQUETTES = ('A', 'B', 'C')
MAX_POST = 16_000
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
SAFE_MODELE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$')
SAFE_FOURNISSEUR = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
MAX_TOKENS_REPONSE = 1500
MAX_TOKENS_REVUE = 600
MAX_TOKENS_SYNTHESE = 2000
MAX_POINTS = 5
# Actions du panneau qui posent une question de jugement (une seule tâche) : seules elles acceptent le conseil.
ACTIONS_JUGEMENT = ('incidence', 'contradictions', 'qualifier_operation', 'verifier_conditions', 'prochaine_etape')
FONCTION_ARGUMENTS = ('recherche_juridique', 'incidence')   # « Examiner les arguments » : fonction et action du circuit commun (ui_ai5689)
# Estimations annoncées avant le lancement (ordre de grandeur, jamais une promesse).
DUREE_EXTERNE_S = 30
DUREE_LOCAL_BASE_S, DUREE_LOCAL_PAR_MD_S, DUREE_LOCAL_INCONNUE_S = 25, 4, 90
CONTEXTE_ESTIME_CAR = 12_000
AVERTISSEMENT = ('Un prompt différent sur le même modèle n’est pas un contrôle indépendant ; deux modèles différents peuvent répéter une erreur : '
                 'la valeur vient des preuves. Le conseil expose les désaccords ; la barrière des références reste le contrôle.')
RECOMMANDATION = 'Recommandé : des familles de modèles différentes, par exemple un modèle local et un modèle d’API.'

SYSTEME_MEMBRE = ('Tu es membre d’un conseil de modèles qui prépare, pour un avocat, une réponse à une question de jugement (stratégie, '
                  'appréciation du risque, interprétation). Réponds de façon indépendante, en français, en 400 mots au plus : position, '
                  'raisonnement, argument adverse le plus sérieux, points à vérifier et informations manquantes. Ne donne aucune probabilité '
                  'ni aucun pourcentage de chance de succès. Ne te présente pas et ne nomme aucun modèle.')
SYSTEME_REVUE = ('Tu relis les réponses d’autres membres d’un conseil, désignées seulement par une lettre. Critique courte et structurée, à '
                 'partir des seules sources fournies. Réponds uniquement par un objet JSON avec les clés : non_soutenues (liste de '
                 '{"reponse": lettre, "point": affirmation non soutenue par les sources}), references_douteuses (liste de {"reponse": lettre, '
                 '"reference": référence douteuse}), argument_adverse_manquant (liste de {"reponse": lettre, "point": argument adverse '
                 'absent}), accords (liste de points d’accord), classement (lettres de la meilleure à la moins bonne). Au plus 5 éléments '
                 'par liste, une phrase chacun. Aucun nom de modèle.')
SCHEMA_REVUE = {'type': 'object', 'additionalProperties': False, 'required': ['non_soutenues', 'references_douteuses', 'argument_adverse_manquant',
                                                                              'accords', 'classement'], 'properties': {
    'non_soutenues': {'type': 'array', 'maxItems': MAX_POINTS, 'items': {'type': 'object', 'properties': {'reponse': {'type': 'string'}, 'point': {'type': 'string'}}}},
    'references_douteuses': {'type': 'array', 'maxItems': MAX_POINTS, 'items': {'type': 'object', 'properties': {'reponse': {'type': 'string'}, 'reference': {'type': 'string'}}}},
    'argument_adverse_manquant': {'type': 'array', 'maxItems': MAX_POINTS, 'items': {'type': 'object', 'properties': {'reponse': {'type': 'string'}, 'point': {'type': 'string'}}}},
    'accords': {'type': 'array', 'maxItems': MAX_POINTS, 'items': {'type': 'string'}},
    'classement': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}}}}
# 5.6.108 (mesure réelle du 19/11) : le livrable est la synthèse du président DANS LA FORME DU LIVRABLE DEMANDÉ, suivie de deux sections courtes et
# bornées ; le texte exact des critiques et les réponses individuelles restent dans la trace (consultable), jamais dans le livrable.
TITRE_ADVERSE = 'Argument adverse le plus sérieux'
TITRE_POINTS = 'Points de désaccord du conseil'
TITRE_QUESTIONS = 'Informations à obtenir'   # 5.6.108 (mesure du 19/11) : manques de la grille et des membres repris en questions ciblées
SECTIONS = (TITRE_ADVERSE, TITRE_POINTS)
MAX_POINTS_LIVRABLE = 5
MAX_QUESTIONS = 5
MAX_MANQUES = 12
MAX_ADVERSE_CAR = 500
FORMES = {
    'consultation': ('une consultation : « Majeure » (règle de droit applicable, citée par numéro de source), « Mineure » (application aux faits '
                     'de l’espèce), « Conclusion » (réponse à la question, avec ses réserves et les informations manquantes)'),
    'acte': 'un projet d’acte : clauses numérotées « Article 1 — … », « Article 2 — … », sans commentaire autour',
    'courriel': 'une réponse directe, rédigée comme un courriel, sans plan « majeure / mineure »',
    'reponse': 'une réponse directe et courte, sans plan « majeure / mineure »',
    'audience': 'une liste numérotée des points à plaider, un point par ligne',
    'recherche': 'les références des sources fournies, citées par numéro, avec la portée de chacune',
    'controle': 'le contrôle demandé : pour chaque point, conforme, inexact ou à vérifier, avec la source',
    'analyse_pieces': 'l’analyse pièce par pièce, chaque pièce citée par son numéro [S…]',
}
SYSTEME_PRESIDENT = ('Tu présides un conseil de modèles. À partir des réponses (désignées par une lettre), des critiques numérotées [C1], [C2]… '
                     'et des seules sources fournies, rédige le livrable demandé par l’avocat, en français, sous la forme suivante : %s. '
                     'Cite les sources par leur numéro entre crochets, par exemple [S2]. Puis ajoute exactement deux sections courtes : '
                     '« ' + TITRE_ADVERSE + ' : » en une ou deux phrases ; '
                     '« ' + TITRE_POINTS + ' : » au plus 5 tirets, une phrase chacun, chaque phrase suivie du numéro de la critique qu’elle '
                     'résume, par exemple [C3]. N’écris aucun point qui ne correspond à une critique numérotée. Ne recopie pas les critiques. '
                     'Si des informations manquantes numérotées [M1], [M2]… te sont données, ajoute enfin « ' + TITRE_QUESTIONS + ' : » : au plus '
                     '5 tirets, chacun une question ciblée à poser à l’avocat ou au client, terminée par un point d’interrogation et suivie du numéro '
                     'du manque, par exemple [M2]. '
                     'N’invente aucune référence et ne donne aucun pourcentage ni aucune probabilité de succès.')
# Familles de modèles (recommandation : familles différentes). Un modèle de la même maison reste de la même famille.
ALIAS_FAMILLES = {'ministral': 'mistral', 'mixtral': 'mistral', 'codestral': 'mistral', 'magistral': 'mistral', 'devstral': 'mistral', 'pixtral': 'mistral',
                  'mistral': 'mistral', 'qwen': 'qwen', 'qwq': 'qwen', 'llama': 'llama', 'codellama': 'llama', 'gemma': 'gemma', 'claude': 'claude',
                  'gpt': 'gpt', 'o': 'gpt', 'chatgpt': 'gpt', 'phi': 'phi', 'deepseek': 'deepseek', 'granite': 'granite', 'olmo': 'olmo'}
FAMILLE_DU_TYPE = {'mistral': 'mistral', 'anthropic': 'claude', 'chatgpt': 'gpt', 'openai': 'gpt'}
TYPES_API = ('mistral', 'anthropic', 'chatgpt', 'openai', 'openrouter')

# ------------------------------------------------------------------------------------------------ préréglages (déclaratifs)
# Chaque membre : ``type`` (« ollama » ou types d'API acceptés, dans l'ordre), ``familles``, ``taille`` (milliards de paramètres, bornes
# incluses) et ``noms`` (liste ordonnée de noms acceptés ; un nom suivi d'une quantification « -q4_K_M » est accepté). Faute de nom accepté,
# le plus gros modèle installé de la famille et de la taille voulues est retenu. ``president`` : « plus_gros » (le plus gros des membres
# locaux), ou l'indice du membre.
PREREGLAGES = (
    {'id': 'local_equilibre', 'libelle': 'Local équilibré', 'local': True,
     'description': 'Deux modèles locaux de familles différentes, de taille moyenne ; président : le plus gros.',
     'membres': ({'type': ('ollama',), 'familles': ('qwen',), 'taille': (20.0, 40.0),
                  'noms': ('qwen3:32b', 'qwen3.5:35b', 'qwen3:30b', 'qwen3.5:27b', 'qwen3:30b-a3b', 'qwen2.5:32b')},
                 {'type': ('ollama',), 'familles': ('gemma', 'mistral'), 'taille': (20.0, 40.0),
                  'noms': ('gemma3:27b', 'gemma4:31b', 'mistral-small3.2:24b', 'mistral-small3.1:24b', 'mistral-small:24b')}),
     'president': 'plus_gros'},
    {'id': 'mixte', 'libelle': 'Mixte', 'local': False,
     'description': 'Un modèle local et Mistral (API) déjà configuré ; président : Mistral. Envoi pseudonymisé par la politique des données.',
     'membres': ({'type': ('ollama',), 'familles': ('qwen', 'gemma', 'llama', 'mistral'), 'taille': (8.0, 40.0),
                  'noms': ('qwen3:32b', 'gemma3:27b', 'mistral-small3.2:24b', 'qwen3:14b', 'qwen3.5:9b', 'llama3.1:8b')},
                 {'type': ('mistral',), 'familles': ('mistral',), 'taille': None,
                  'noms': ('mistral-large-4', 'mistral-large-latest', 'mistral-medium-latest')}),
     'president': 1},
    {'id': 'local_leger', 'libelle': 'Local léger', 'local': True,
     'description': 'Deux petits modèles locaux de familles différentes ; président : le meilleur (le plus gros) des deux.',
     'membres': ({'type': ('ollama',), 'familles': ('qwen',), 'taille': (3.0, 14.0),
                  'noms': ('qwen3.5:9b', 'qwen3:14b', 'qwen3:8b', 'qwen2.5:7b')},
                 {'type': ('ollama',), 'familles': ('llama', 'mistral', 'gemma'), 'taille': (3.0, 14.0),
                  'noms': ('llama3.1:8b', 'ministral-3:8b', 'gemma3:12b', 'ministral-3:3b', 'mistral:7b')}),
     'president': 'plus_gros'},
    {'id': 'api', 'libelle': 'API', 'local': False,
     'description': 'Deux fournisseurs d’API configurés (Mistral et Claude ou ChatGPT) ; président : le second. Envoi pseudonymisé.',
     'membres': ({'type': ('mistral',), 'familles': ('mistral',), 'taille': None, 'noms': ('mistral-large-4', 'mistral-large-latest')},
                 {'type': ('anthropic', 'chatgpt', 'openai'), 'familles': ('claude', 'gpt'), 'taille': None,
                  'noms': ('claude-sonnet-5-5', 'claude-opus-5-5')}),
     'president': 1},
)
ORDRE_DEFAUT = ('local_equilibre', 'mixte', 'local_leger', 'api')
PAR_ID = {p['id']: p for p in PREREGLAGES}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _h(valeur):
    return html.escape(str(valeur if valeur is not None else ''), quote=True)


def est_cloud(nom):
    """Modèle exécuté hors de l'hôte par Ollama : jamais un membre « local »."""
    n = str(nom or '').lower()
    return n.endswith('-cloud') or ':cloud' in n or n.endswith(':cloud')


def famille(nom, type_fournisseur='ollama'):
    """Famille d'un modèle (« qwen3.5:9b » → qwen ; « ministral-3:3b » → mistral ; fournisseur Claude → claude)."""
    if type_fournisseur in FAMILLE_DU_TYPE and type_fournisseur != 'openai':
        return FAMILLE_DU_TYPE[type_fournisseur]
    base = str(nom or '').lower().rsplit('/', 1)[-1].split(':', 1)[0]
    m = re.match(r'[a-z]+', base)
    tete = m.group(0) if m else base
    if tete in ALIAS_FAMILLES:
        return ALIAS_FAMILLES[tete]
    for cle, fam in ALIAS_FAMILLES.items():
        if len(cle) > 2 and tete.startswith(cle):
            return fam
    return tete or (FAMILLE_DU_TYPE.get(type_fournisseur) or type_fournisseur)


# ------------------------------------------------------------------------------------------------ modèles disponibles (lecture seule)
def _installes(desk):
    """Modèles d'Ollama installés (détection existante ``modeles5667.installed``, liste gardée 2 minutes ; « :cloud » exclus). None : injoignable."""
    try:
        from .modeles5667 import installed
        return [m for m in installed(desk) if not est_cloud(m['name'])]
    except Exception:   # noqa: BLE001 — Ollama injoignable : les membres locaux sont dits indisponibles, jamais supposés présents
        return None


def _fournisseurs_api(desk):
    """Fournisseurs d'API configurés, activés et autorisés à recevoir des données (pseudonymisées) : {id: cfg}."""
    from .ai_gateway import provider_registry
    out = {}
    for pid, cfg in sorted(provider_registry(desk.c).items()):
        if cfg.get('type', 'ollama') == 'ollama' or not cfg.get('enabled', True) or not cfg.get('external_data_allowed'):
            continue
        if cfg.get('type') in TYPES_API and str(cfg.get('model') or ''):
            out[pid] = cfg
    return out


def _local_strict(desk):
    from .selection_sources5697 import local_strict
    return local_strict(desk)


def disponibles(desk, installes=None, api=None):
    """Modèles candidats : locaux installés et fournisseurs d'API configurés (vide en mode local strict pour l'API)."""
    installes = _installes(desk) if installes is None else installes
    api = ({} if _local_strict(desk) else _fournisseurs_api(desk)) if api is None else api
    locaux = [{'fournisseur': 'ollama', 'type': 'ollama', 'modele': m['name'], 'local': True, 'taille': float(m.get('billions') or 0),
               'famille': famille(m['name'])} for m in (installes or []) if not est_cloud(m['name'])]
    distants = [{'fournisseur': pid, 'type': cfg.get('type'), 'modele': str(cfg.get('model') or ''), 'local': False, 'taille': None,
                 'famille': famille(cfg.get('model'), cfg.get('type'))} for pid, cfg in api.items()]
    return {'locaux': locaux, 'api': distants, 'ollama_joignable': installes is not None}


def _accepte(nom_installe, accepte):
    n, a = nom_installe.lower(), accepte.lower()
    return n == a or n.startswith(a + '-') or (':' not in a and n in (a + ':latest',))


def _trouver(slot, dispo, pris):
    """Modèle d'un emplacement de préréglage : nom accepté dans l'ordre, sinon le plus gros de la famille et de la taille voulues. ``pris`` :
    membres déjà retenus (un modèle local d'une famille déjà prise est écarté : familles différentes)."""
    deja = {(m['fournisseur'], m['modele']) for m in pris}
    if 'ollama' in slot['type']:
        familles_prises = {m['famille'] for m in pris}
        candidats = [m for m in dispo['locaux'] if (m['fournisseur'], m['modele']) not in deja and m['famille'] not in familles_prises
                     and not est_cloud(m['modele'])]
        for nom in slot['noms']:
            for m in candidats:
                if _accepte(m['modele'], nom):
                    return m
        bornes = slot.get('taille') or (0.0, 1e9)
        dans = [m for m in candidats if m['famille'] in slot['familles'] and bornes[0] <= (m['taille'] or 0) <= bornes[1]]
        return max(dans, key=lambda m: (m['taille'] or 0, m['modele'])) if dans else None
    for type_voulu in slot['type']:
        for m in dispo['api']:
            if m['type'] == type_voulu and (m['fournisseur'], m['modele']) not in deja:
                return m
    return None


def _manque(slot, dispo):
    if 'ollama' in slot['type']:
        if not dispo['ollama_joignable']:
            return 'Ollama injoignable : modèles locaux inconnus'
        bornes = slot.get('taille')
        taille = (' de %g à %g Md' % bornes) if bornes else ''
        return 'aucun modèle local %s%s installé (par exemple %s)' % ('/'.join(slot['familles']), taille, ', '.join(slot['noms'][:2]))
    return 'aucun fournisseur d’API %s configuré et autorisé' % ' ou '.join({'anthropic': 'Claude', 'chatgpt': 'ChatGPT', 'openai': 'OpenAI',
                                                                           'mistral': 'Mistral', 'openrouter': 'OpenRouter'}.get(t, t) for t in slot['type'])


def evaluer_prereglage(p, dispo, local_strict=False):
    """État d'un préréglage : disponible, partiellement disponible (membre manquant dit), indisponible (ou exclu en mode local strict)."""
    trouves, manques, pris = [], [], []
    for slot in p['membres']:
        m = _trouver(slot, dispo, pris)
        if m is not None:
            pris.append(m)
            trouves.append(dict(m))
            manques.append('')
        else:
            trouves.append(None)
            manques.append(_manque(slot, dispo))
    n = sum(1 for m in trouves if m)
    if local_strict and not p['local']:
        etat = 'exclu'
    else:
        etat = 'disponible' if n == len(trouves) else ('partiel' if n else 'indisponible')
    president = None
    if etat == 'disponible':
        if p['president'] == 'plus_gros':
            president = max(trouves, key=lambda m: (m['taille'] or 0, m['modele']))
        else:
            president = trouves[int(p['president'])]
    return {'id': p['id'], 'libelle': p['libelle'], 'description': p['description'], 'local': p['local'], 'etat': etat,
            'etat_libelle': {'disponible': 'disponible', 'partiel': 'partiellement disponible', 'indisponible': 'indisponible',
                             'exclu': 'exclu (mode local strict)'}[etat],
            'membres': trouves, 'manques': manques, 'president': president}


def detection(desk, installes=None, api=None):
    """Lecture seule (aucune écriture) : chaque préréglage comparé aux modèles réellement disponibles, et le préréglage par défaut."""
    strict = _local_strict(desk)
    dispo = disponibles(desk, installes, api)
    etats = [evaluer_prereglage(p, dispo, strict) for p in PREREGLAGES]
    par_id = {x['id']: x for x in etats}
    defaut = next((i for i in ORDRE_DEFAUT if par_id[i]['etat'] == 'disponible'), '')
    return {'prereglages': etats, 'defaut': defaut, 'local_strict': strict, 'disponibles': dispo}


# ------------------------------------------------------------------------------------------------ réglages du cabinet
def reglages(desk):
    """Réglage enregistré (lecture seule) ; absent : mode « auto » (premier préréglage disponible)."""
    brut = desk.settings(SETTING, None)
    r = brut if isinstance(brut, dict) else {}
    mode = r.get('mode') if r.get('mode') in ('auto', 'prereglage', 'personnalise') else 'auto'
    return {'mode': mode, 'prereglage': str(r.get('prereglage') or ''), 'membres': [x for x in r.get('membres') or [] if isinstance(x, dict)][:MAX_MEMBRES],
            'president': r.get('president') if isinstance(r.get('president'), dict) else None, 'version': int(r.get('version') or 0),
            'auteur': str(r.get('auteur') or ''), 'le': str(r.get('le') or ''), 'cle': str(r.get('cle') or ''), 'empreinte': str(r.get('empreinte') or '')}


def _resoudre(desk, choix, dispo):
    """Membre choisi ({fournisseur, modele}) → description complète, ou None s'il n'est plus disponible."""
    for m in dispo['locaux'] + dispo['api']:
        if m['fournisseur'] == choix.get('fournisseur') and m['modele'] == choix.get('modele'):
            return dict(m)
    return None


def effectifs(desk, installes=None, api=None):
    """Membres et président en vigueur : préréglage (choisi ou par défaut) ou choix personnalisé, filtrés par le mode local strict.
    Rend aussi ``pret`` (au moins deux membres) et le motif lisible quand le conseil est indisponible."""
    det = detection(desk, installes, api)
    r = reglages(desk)
    limites = []
    if r['mode'] == 'personnalise':
        source = 'personnalisé'
        membres = [_resoudre(desk, c, det['disponibles']) for c in r['membres']]
        for c, m in zip(r['membres'], membres):
            if m is None:
                limites.append('Membre indisponible : %s (%s).' % (c.get('modele', ''), c.get('fournisseur', '')))
        membres = [m for m in membres if m]
        president = _resoudre(desk, r['president'] or {}, det['disponibles']) if r['president'] else None
    else:
        ident = r['prereglage'] if r['mode'] == 'prereglage' and r['prereglage'] in PAR_ID else det['defaut']
        source = ('préréglage « %s »' % PAR_ID[ident]['libelle'] if r['mode'] == 'prereglage' else
                  'préréglage par défaut « %s »' % PAR_ID[ident]['libelle']) if ident else 'aucun préréglage disponible'
        etat = next((x for x in det['prereglages'] if x['id'] == ident), None)
        membres = [m for m in (etat or {}).get('membres') or [] if m]
        if etat and etat['etat'] != 'disponible':
            limites += ['Préréglage « %s » %s : %s.' % (etat['libelle'], etat['etat_libelle'], x) for x in etat['manques'] if x]
        president = (etat or {}).get('president')
    if det['local_strict']:
        externes = [m for m in membres if not m['local']]
        if externes:
            limites.append('Mode local strict : %d membre(s) d’API écarté(s) ; seuls les membres locaux participent.' % len(externes))
        membres = [m for m in membres if m['local']]
        if president and not president['local']:
            president = None
    if president is None and membres:
        president = max(membres, key=lambda m: (m['taille'] or 0, m['modele']))
        if r['mode'] == 'personnalise' and r['president']:
            limites.append('Président choisi indisponible : le plus gros membre préside.')
    pret = len(membres) >= MIN_MEMBRES
    familles = [m['famille'] for m in membres]
    if pret and len(set(familles)) < len(familles):
        limites.append('Deux membres sont de la même famille (%s) : leurs erreurs risquent d’être les mêmes. %s' % (
            ', '.join(sorted({f for f in familles if familles.count(f) > 1})), RECOMMANDATION))
    motif = '' if pret else ('Aucun conseil configuré : au moins deux modèles disponibles sont nécessaires. Choisissez un préréglage ou des '
                             'membres dans Paramètres › IA › « Conseil de modèles ».')
    return {'membres': membres[:MAX_MEMBRES], 'president': president, 'pret': pret, 'motif': motif, 'source': source, 'mode': r['mode'],
            'limites': limites, 'detection': det, 'reglages': r}


def _valider_choix(c, dispo, strict):
    if not isinstance(c, dict):
        raise Stop('membre_conseil_invalide')
    pid, modele = str(c.get('fournisseur') or ''), str(c.get('modele') or '')
    if not SAFE_FOURNISSEUR.fullmatch(pid) or not SAFE_MODELE.fullmatch(modele):
        raise Stop('membre_conseil_invalide')
    if pid == 'ollama' and est_cloud(modele):
        raise Stop('modele_cloud_refuse')
    m = next((x for x in dispo['locaux'] + dispo['api'] if x['fournisseur'] == pid and x['modele'] == modele), None)
    if m is None:
        raise Stop('membre_conseil_indisponible')
    if strict and not m['local']:
        raise Stop('mode_local_strict')
    return {'fournisseur': pid, 'modele': modele}


def _choix(valeur):
    """« fournisseur::modèle » (valeur d'une liste de la page) → dict."""
    if isinstance(valeur, dict):
        return valeur
    pid, _sep, modele = str(valeur or '').partition('::')
    return {'fournisseur': pid, 'modele': modele} if pid and modele else None


def enregistrer(desk, data, owner, installes=None, api=None):
    """Enregistre le choix de l'administrateur (POST seulement) : version attendue (409 si périmée), clé d'idempotence, membres vérifiés."""
    if not isinstance(data, dict):
        raise Stop('requete_invalide')
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    mode = str(data.get('mode') or '')
    if mode not in ('auto', 'prereglage', 'personnalise'):
        raise Stop('mode_conseil_invalide')
    actuel = reglages(desk)
    valeur = {'mode': mode, 'prereglage': '', 'membres': [], 'president': None}
    if mode == 'prereglage':
        if str(data.get('prereglage') or '') not in PAR_ID:
            raise Stop('prereglage_inconnu')
        valeur['prereglage'] = str(data['prereglage'])
    elif mode == 'personnalise':
        dispo = disponibles(desk, installes, api)
        strict = _local_strict(desk)
        brut = data.get('membres') if isinstance(data.get('membres'), list) else [data.get('membre%d' % i) for i in (1, 2, 3)]
        choix = [_choix(x) for x in brut if x]
        choix = [c for c in choix if c]
        if not MIN_MEMBRES <= len(choix) <= MAX_MEMBRES:
            raise Stop('membres_conseil_nombre')
        membres = [_valider_choix(c, dispo, strict) for c in choix]
        if len({(m['fournisseur'], m['modele']) for m in membres}) != len(membres):
            raise Stop('membres_conseil_doublon')
        valeur['membres'] = membres
        pres = _choix(data.get('president'))
        valeur['president'] = _valider_choix(pres, dispo, strict) if pres else None
    empreinte = hashlib.sha256(json.dumps(valeur, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if cle == actuel['cle']:   # même clé rejouée : même réponse ; autre contenu sous la même clé : refusé
        if empreinte != actuel['empreinte']:
            raise Stop('cle_reutilisee')
        return {'reglages': actuel, 'idempotent': True, 'message': 'Réglage déjà enregistré.'}
    try:
        attendue = int(data.get('version'))
    except (TypeError, ValueError):
        raise Stop('version_en_conflit') from None
    if attendue != actuel['version']:
        raise Stop('version_en_conflit')
    valeur.update(version=actuel['version'] + 1, auteur=str(owner)[:120], le=_now(), cle=cle, empreinte=empreinte)
    desk.setting(SETTING, valeur)
    desk.audit('conseil56108_reglages', {'mode': mode, 'prereglage': valeur['prereglage'], 'membres': len(valeur['membres']), 'version': valeur['version']})
    return {'reglages': reglages(desk), 'message': {'auto': 'Préréglage automatique rétabli.', 'prereglage': 'Préréglage enregistré.',
                                                    'personnalise': 'Membres du conseil enregistrés.'}[mode]}


# ------------------------------------------------------------------------------------------------ annonce avant le lancement
def _tarif(desk, m):
    if m['local']:
        return (0.0, 0.0)
    from .ai_gateway import provider_registry
    cfg = provider_registry(desk.c).get(m['fournisseur']) or {}
    entree, sortie = float(cfg.get('input_usd_per_million') or 0), float(cfg.get('output_usd_per_million') or 0)
    if entree or sortie:
        return (entree, sortie)
    try:
        from .modeles5681 import tariff
        return tariff(m['modele'])
    except Exception:   # noqa: BLE001
        return None


def _duree_appel(m):
    if not m['local']:
        return DUREE_EXTERNE_S
    return DUREE_LOCAL_BASE_S + DUREE_LOCAL_PAR_MD_S * m['taille'] if m.get('taille') else DUREE_LOCAL_INCONNUE_S


def annonce(desk, eff=None, contexte_car=CONTEXTE_ESTIME_CAR):
    """Ce qui est annoncé avant le lancement : membres, nombre d'appels, durée et coût estimés. Lecture seule."""
    eff = eff or effectifs(desk)
    membres, president = eff['membres'], eff['president']
    if not eff['pret']:
        return {'pret': False, 'motif': eff['motif'], 'limites': eff['limites'], 'membres': [], 'appels': 0}
    n = len(membres)
    appels = n + n + 1 + 1   # réponses, revues croisées, synthèse, au plus une reprise de la barrière
    duree = sum(2 * _duree_appel(m) for m in membres) + 2 * _duree_appel(president)
    cout, connu = 0.0, True
    jetons_contexte = contexte_car // 3
    for m, nb_appels, entree in [(m, 2, jetons_contexte + n * MAX_TOKENS_REPONSE) for m in membres] + [(president, 2, jetons_contexte + n * (MAX_TOKENS_REPONSE + MAX_TOKENS_REVUE))]:
        t = _tarif(desk, m)
        if t is None:
            connu = False
            continue
        cout += nb_appels * (entree * t[0] + MAX_TOKENS_SYNTHESE * t[1]) / 1_000_000
    externes = [m for m in membres + [president] if not m['local']]
    return {'pret': True, 'membres': [_public_membre(m) for m in membres], 'president': _public_membre(president), 'appels': appels,
            'duree_estimee_s': int(duree), 'cout_estime_usd': round(cout, 4) if connu else None, 'cout_connu': connu,
            'pseudonymisation': bool(externes), 'source': eff['source'], 'limites': eff['limites'], 'recommandation': RECOMMANDATION,
            'texte': phrase_annonce(membres, president, appels, duree, cout if connu else None, bool(externes))}


def _public_membre(m):
    return {k: m.get(k) for k in ('fournisseur', 'type', 'modele', 'local', 'famille', 'taille')} if m else None


def _nom_membre(m):
    return '%s (%s)' % (m['modele'], 'local' if m['local'] else m['fournisseur'])


def _duree_lisible(s):
    s = int(s)
    return ('%d min %02d s' % (s // 60, s % 60)) if s >= 60 else '%d s' % s


def phrase_annonce(membres, president, appels, duree, cout, externes):
    cout_txt = ('aucun coût d’API (modèles locaux)' if not externes else
                ('environ %s $ au plus' % ('%.2f' % cout).replace('.', ',') if cout is not None else 'coût inconnu (tarif non renseigné)'))
    return ('Membres : %s ; président : %s. Jusqu’à %d appels, l’un après l’autre ; durée estimée : %s ; %s.%s' % (
        ', '.join(_nom_membre(m) for m in membres), _nom_membre(president), appels, _duree_lisible(duree), cout_txt,
        ' Les envois vers un fournisseur d’API sont pseudonymisés selon la politique des données.' if externes else ''))


# ------------------------------------------------------------------------------------------------ délibération (programme)
def masquer_noms(texte, noms):
    """Revue anonyme : aucun nom de modèle ni de fournisseur dans ce que voit un membre."""
    out = str(texte or '')
    for nom in sorted({str(n) for n in noms if str(n or '').strip() and len(str(n)) >= 2}, key=len, reverse=True):
        out = re.sub(r'(?<![\w])' + re.escape(nom) + r'(?![\w])', '[modèle]', out, flags=re.I)
    return out


def noms_a_masquer(membres):
    out = set()
    for m in membres:
        modele = str(m.get('modele') or '')
        out |= {modele, modele.split(':', 1)[0], modele.rsplit('/', 1)[-1], str(m.get('fournisseur') or ''), str(m.get('famille') or '')}
        out |= {re.match(r'[A-Za-z]+', modele).group(0)} if re.match(r'[A-Za-z]{3,}', modele) else set()
    out |= {'Claude', 'ChatGPT', 'GPT', 'Mistral', 'Ministral', 'Llama', 'Qwen', 'Gemma', 'Anthropic', 'OpenAI', 'Ollama', 'OpenRouter', 'DeepSeek'}
    return {n for n in out if n and n.lower() not in ('o', 'ollama:', 'local')}


_POURCENT = re.compile(r'\b\d{1,3}(?:[.,]\d+)?\s?(?:%|pour\s?cent)', re.I)
_CHANCE = re.compile(r'chance|probab|succ[eè]s|r[ée]ussi|gagn|perd|issue favorable|risque', re.I)
RETIRE = '[estimation chiffrée retirée : aucune probabilité de succès n’est calculée]'


def retirer_pourcentages(texte):
    """(texte, nombre) : un pourcentage de chance de succès dans une phrase qui en parle est retiré, jamais présenté."""
    n = [0]
    out = []
    for phrase in re.split(r'(?<=[.!?\n])', str(texte or '')):
        if _CHANCE.search(phrase) and _POURCENT.search(phrase):
            phrase, k = _POURCENT.subn(RETIRE, phrase)
            n[0] += k
        out.append(phrase)
    return ''.join(out), n[0]


def _texte_court(valeur, limite=300):
    return re.sub(r'\s+', ' ', str(valeur or '')).strip()[:limite]


def valider_revue(data, etiquettes_revues):
    """Critique d'un membre : listes fermées, une lettre parmi celles qu'il a relues, au plus 5 éléments ; le reste est ignoré."""
    if not isinstance(data, dict):
        raise Stop('reponse_modele_invalide')
    out = {'non_soutenues': [], 'references_douteuses': [], 'argument_adverse_manquant': [], 'accords': [], 'classement': []}
    for cle, champ in (('non_soutenues', 'point'), ('references_douteuses', 'reference'), ('argument_adverse_manquant', 'point')):
        for x in (data.get(cle) or [])[:MAX_POINTS] if isinstance(data.get(cle), list) else []:
            if isinstance(x, dict) and str(x.get('reponse') or '').strip().upper() in etiquettes_revues and _texte_court(x.get(champ)):
                out[cle].append({'reponse': str(x['reponse']).strip().upper(), 'point': _texte_court(x.get(champ))})
    out['accords'] = [_texte_court(x) for x in (data.get('accords') or [])[:MAX_POINTS] if isinstance(x, str) and _texte_court(x)] if isinstance(data.get('accords'), list) else []
    if isinstance(data.get('classement'), list):
        vus = []
        for x in data['classement']:
            lettre = str(x or '').strip().upper()
            if lettre in etiquettes_revues and lettre not in vus:
                vus.append(lettre)
        out['classement'] = vus
    return out


def desaccords_de(critiques):
    """Désaccords relevés par le programme dans les critiques (texte exact, jamais reformulé) et divergence des classements."""
    out = []
    libelles = {'non_soutenues': 'affirmation non soutenue par les sources', 'references_douteuses': 'référence douteuse',
                'argument_adverse_manquant': 'argument adverse manquant'}
    for c in critiques:
        if not c.get('structure'):
            continue
        for cle, libelle in libelles.items():
            for x in c['structure'][cle]:
                out.append({'type': cle, 'libelle': libelle, 'reponse': x['reponse'], 'par': c['par'], 'texte': x['point']})
    tetes = {c['par']: c['structure']['classement'][0] for c in critiques if c.get('structure') and c['structure']['classement']}
    if len(set(tetes.values())) > 1:
        out.append({'type': 'classement', 'libelle': 'classements divergents', 'reponse': '', 'par': '',
                    'texte': ' ; '.join('la revue de %s place %s en tête' % (k, v) for k, v in sorted(tetes.items()))})
    for i, d in enumerate(out, 1):   # numéro stable de la critique : le président le cite, le programme le vérifie
        d['id'] = 'C%d' % i
    return out


_TITRE = re.compile(r'(?im)^[\s#>*_-]*(argument adverse le plus s[ée]rieux|points? de d[ée]saccords? du conseil|informations? [àa] obtenir)[\s*_]*:?[\s*_]*')
_CN = re.compile(r'\[\s*(C\d{1,3}(?:\s*[,;]\s*C\d{1,3})*)\s*\]')
_MN = re.compile(r'\[\s*(M\d{1,3}(?:\s*[,;]\s*M\d{1,3})*)\s*\]')


def decouper(texte):
    """(corps, argument adverse, lignes des points, lignes des questions) : le corps est tout ce qui précède la première des sections."""
    texte = str(texte or '')
    trouves = list(_TITRE.finditer(texte))
    if not trouves:
        return texte.strip(), '', [], []
    corps, adverse, points, questions = texte[:trouves[0].start()].strip(), '', [], []
    for i, m in enumerate(trouves):
        fin = trouves[i + 1].start() if i + 1 < len(trouves) else len(texte)
        contenu = texte[m.end():fin].strip()
        titre = fold(m.group(1))
        if titre.startswith('argument'):
            adverse = adverse or contenu
        elif titre.startswith('information'):
            questions += [l for l in contenu.splitlines() if l.strip()]
        else:
            points += [l for l in contenu.splitlines() if l.strip()]
    return corps, adverse, points, questions


# ------------------------------------------------------------------------------------------------ informations manquantes (grille et membres)
_MANQUE = re.compile(r"manqu|[àa] pr[ée]ciser|[àa] confirmer|non pr[ée]cis|n.est pas pr[ée]cis|ignor|il (?:nous )?faudrait (?:savoir|conna[iî]tre|disposer|obtenir)|"
                     r"informations? (?:n[ée]cessaire|manquante|[àa] obtenir)|pi[èe]ces? [àa] (?:obtenir|produire|demander)|reste [àa] (?:[ée]tablir|d[ée]terminer)", re.I)
_VIDES = frozenset(('dans', 'pour', 'avec', 'cette', 'sont', 'faut', 'quel', 'quelle', 'quels', 'quelles', 'date', 'avez', 'vous', 'nous', 'leur',
                    'savoir', 'preciser', 'confirmer', 'information', 'informations', 'manque', 'manquante', 'manquantes', 'obtenir', 'pouvez',
                    'faudrait', 'elle', 'elles', 'ils', 'est-ce', 'quoi', 'comment', 'dossier'))


def _cles(texte):
    return {m for m in re.findall(r'[a-z0-9]{4,}', fold(texte)) if m not in _VIDES}


def manques_de(texte, maximum=5):
    """Manques signalés par un membre dans sa réponse (lecture déterministe) : phrases interrogatives ou qui disent ce qui manque."""
    out = []
    for phrase in re.split(r'(?<=[.?!])\s+|\n+', str(texte or '')):
        p = re.sub(r'\s+', ' ', phrase).strip(' -•*\t')
        if len(p) < 12 or len(p) > 400:
            continue
        if p.endswith('?') or _MANQUE.search(p):
            out.append(_phrase(R_SN.sub('', p), 240))
        if len(out) >= maximum:
            break
    return out


R_SN = re.compile(r'\[\s*[A-Z]\d{1,3}(?:\s*[,;]\s*[A-Z]?\d{1,3})*\s*\]')


def _meme_manque(a, b):
    ca, cb = _cles(a), _cles(b)
    return bool(ca and cb) and len(ca & cb) / min(len(ca), len(cb)) >= 0.6


def fusionner_manques(manques_grille, reponses):
    """Manques de la grille (d'abord : posés par le serveur, comme une mission simple), puis ceux des membres ; dédoublonnés ; numérotés M1…"""
    out = []
    for m in manques_grille or []:
        libelle = _phrase(m.get('champ') or '', 240) if isinstance(m, dict) else _phrase(m, 240)
        if libelle and not any(_meme_manque(libelle, x['champ']) for x in out):
            out.append({'champ_id': str((m.get('champ_id') if isinstance(m, dict) else '') or ''), 'champ': libelle, 'origine': 'grille'})
    for r in reponses or []:
        for libelle in manques_de(r['texte']):
            if not any(_meme_manque(libelle, x['champ']) for x in out):
                out.append({'champ_id': '', 'champ': libelle, 'origine': 'réponse %s' % r.get('etiquette', '')})
    out = out[:MAX_MANQUES]
    for i, m in enumerate(out, 1):
        m['id'] = 'M%d' % i
    return out


def _question(texte):
    q = _phrase(texte, 300).rstrip(' .;:')
    return q if q.endswith('?') else q + ' ?'


def verifier_questions(lignes, manques, maximum=MAX_QUESTIONS):
    """Questions du président : seule une question rattachée à un manque réel ([Mn] connu : grille ou réponse d'un membre) est gardée, au plus
    ``maximum``, une phrase ; une question non rattachée est retirée et comptée. Rend (questions, retirées)."""
    connus = {m['id'] for m in manques}
    questions, retirees = [], 0
    for ligne in lignes:
        ids = sorted({x.strip() for m in _MN.finditer(ligne) for x in re.split(r'[,;]', m.group(1)) if x.strip()})
        texte = _MN.sub('', ligne).strip(' -•*\t')
        if not ids or not set(ids) <= connus or len(_cles(texte)) == 0 or len(questions) >= maximum:
            retirees += 1
            continue
        questions.append({'texte': _question(texte), 'manques': ids})
    return questions, retirees


def completer_questions(questions, manques, maximum=MAX_QUESTIONS):
    """Reprise des manques : un manque (grille d'abord) qu'aucune question ne couvre devient une question posée par le programme, dans la limite."""
    couverts = {i for q in questions for i in q['manques']}
    out = list(questions)
    for m in manques:
        if len(out) >= maximum:
            break
        if m['id'] in couverts:
            continue
        libelle = m['champ'].rstrip(' .')
        texte = libelle if libelle.endswith('?') else 'Pouvez-vous préciser : ' + libelle[:1].lower() + libelle[1:]
        out.append({'texte': _question(texte), 'manques': [m['id']], 'programme': True})
    return out


def grille_par_le_programme(appeler, redacteur, question, table, un_appel=None):
    """Grille du lot I calculée comme la mission simple d'un petit modèle (une étape courte, sortie contrainte par schéma, validée par
    ``grille56108.valider``) ; rend la liste des manques, ou [] si elle est illisible."""
    from . import grille56108
    sources = [{'id': k, 'titre': s.get('titre', ''), 'excerpt': s.get('texte', '')} for k, s in table.items()]
    charge = {'demande': question, 'sources': [{'id': s['id'], 'titre': s['titre'], 'texte': str(s['excerpt'])[:grille56108.SOURCES_MAX]} for s in sources]}
    messages = [{'role': 'system', 'content': grille56108.SYSTEME}, {'role': 'user', 'content': json.dumps(charge, ensure_ascii=False)}]
    texte, ex = (un_appel or (lambda q, e, m, n, s: (appeler(q, m, n, s), None)))(redacteur, 'grille', messages, 1500, grille56108.SCHEMA)
    if ex is not None:
        return []
    try:
        from .chaine5689 import extraire_json
        grille, _retires = grille56108.valider(extraire_json(texte), sources)
    except Stop:
        return []
    return grille['manques']


def _phrase(texte, limite):
    t = re.sub(r'\s+([.,])', r'\1', re.sub(r'\s+', ' ', str(texte or ''))).strip(' -•*\t')
    m = re.search(r'^(.+?[.!?])(?:\s|$)', t)
    t = m.group(1) if m else t
    return t if len(t) <= limite else t[:limite - 1].rsplit(' ', 1)[0] + '…'


def verifier_points(lignes, desaccords, maximum=MAX_POINTS_LIVRABLE):
    """Points de désaccord rédigés par le président : seul un point qui cite une critique réelle ([Cn] connu) est gardé, au plus ``maximum``,
    une phrase ; un point inventé (sans numéro, ou numéro inconnu) est retiré et compté. Rend (points, retirés)."""
    connus = {d['id'] for d in desaccords if d.get('id')}
    points, retires = [], 0
    for ligne in lignes:
        ids = sorted({x.strip() for m in _CN.finditer(ligne) for x in re.split(r'[,;]', m.group(1)) if x.strip()})
        texte = _phrase(_CN.sub('', ligne), 300)
        if not ids or not set(ids) <= connus or not texte or len(points) >= maximum:
            retires += 1
            continue
        points.append({'texte': texte, 'critiques': ids})
    return points, retires


def bloc_questions(questions):
    return ('\n\n%s :\n%s' % (TITRE_QUESTIONS, '\n'.join('- ' + q['texte'] for q in questions))) if questions else ''


def assembler_livrable(corps, adverse, points, desaccords, questions=()):
    """Livrable : corps dans la forme demandée, puis les sections bornées (argument adverse, points de désaccord, informations à obtenir).
    Aucun texte de critique n'y est recopié par le programme."""
    adverse = _phrase(adverse, MAX_ADVERSE_CAR) if adverse.strip() else 'non formulé par le président.'
    if points:
        bloc = '\n'.join('- ' + p['texte'] for p in points)
    elif desaccords:
        bloc = '- Aucun point vérifiable rédigé par le président ; %d critique(s) croisée(s) consultable(s) dans la trace du conseil.' % len(desaccords)
    else:
        bloc = '- Aucun désaccord relevé par la revue croisée.'
    return '%s\n\n%s : %s\n\n%s :\n%s%s' % (corps.rstrip(), TITRE_ADVERSE, adverse, TITRE_POINTS, bloc, bloc_questions(questions))


def bloc_desaccords(desaccords):
    """Texte exact des critiques, pour la TRACE seulement (jamais recopié dans le livrable)."""
    if not desaccords:
        return ''
    lignes = []
    for d in desaccords:
        if d['type'] == 'classement':
            lignes.append('- Classements divergents : %s.' % d['texte'])
        else:
            lignes.append('- [%s] Réponse %s, %s (revue de %s) : « %s »' % (d.get('id', ''), d['reponse'], d['libelle'], d['par'], d['texte']))
    return 'Désaccords relevés par la revue croisée (texte des critiques, non reformulé) :\n' + '\n'.join(lignes)


def _cadre(question, table):
    from .references56108 import bloc_sources
    return 'Question de l’avocat :\n%s\n\nSources fournies :\n%s' % (question, bloc_sources(table) or '(aucune)')


def messages_membre(membre, question, table, cadre=''):
    from .references56108 import CONSIGNE
    systeme = (cadre + '\n\n' if cadre else '') + SYSTEME_MEMBRE + ('\n\n' + CONSIGNE if membre.get('petit') else
                                                                    '\n\nCite les sources par leur numéro entre crochets, par exemple [S1].')
    return [{'role': 'system', 'content': systeme}, {'role': 'user', 'content': _cadre(question, table)}]


def messages_revue(question, table, autres, noms):
    blocs = '\n\n'.join('[Réponse %s]\n%s' % (lettre, masquer_noms(texte, noms)) for lettre, texte in autres)
    return [{'role': 'system', 'content': SYSTEME_REVUE},
            {'role': 'user', 'content': '%s\n\nRéponses à examiner (%s) :\n\n%s' % (_cadre(question, table), ', '.join(l for l, _t in autres), blocs)}]


def messages_president(question, table, reponses, desaccords, noms, livrable='consultation', manques=()):
    blocs = '\n\n'.join('[Réponse %s]\n%s' % (r['etiquette'], masquer_noms(r['texte'], noms)) for r in reponses)
    crit = masquer_noms(bloc_desaccords(desaccords), noms)
    forme = FORMES.get(livrable) or FORMES['consultation']
    liste = '\n'.join('[%s] %s' % (m['id'], masquer_noms(m['champ'], noms)) for m in manques or [])
    return [{'role': 'system', 'content': SYSTEME_PRESIDENT % forme},
            {'role': 'user', 'content': '%s\n\nRéponses du conseil :\n\n%s\n\nCritiques numérotées :\n%s\n\nInformations manquantes numérotées :\n%s' % (
                _cadre(question, table), blocs, crit or '(aucune critique lisible)', liste or '(aucune)')}]


def _code(ex):
    return str(ex).split(':')[0] if isinstance(ex, Stop) else type(ex).__name__


def deliberer(question, sources, membres, president, appeler, connecteurs=None, desk=None, contexte=(), graine=None, horloge=time.monotonic,
              cadre='', livrable='consultation', manques_grille=None, calculer_grille=False):
    """Délibération pilotée par le programme, l'un après l'autre. ``membres`` : 2 ou 3 dicts (``fournisseur``, ``modele``, ``local``,
    ``famille``, ``petit``) ; ``president`` : un dict de même forme (un membre ou un autre modèle). ``appeler(membre, messages, max_tokens,
    schema=None) -> str`` : transport (produit : politique, pause, budget ; banc : Ollama injecté). Arrêt, pause, budget ou annulation remontent
    tels quels ; tout autre échec d'un membre est noté et le conseil continue.

    ``livrable`` : forme du livrable demandé (``FORMES``). Le texte rendu (``texte``) est le livrable : synthèse du président dans cette forme,
    puis « Argument adverse le plus sérieux » et « Points de désaccord du conseil » (au plus 5 points vérifiés contre les critiques réelles) ; la
    barrière des références ne s'applique qu'à lui. Réponses individuelles et texte exact des critiques : dans la trace seulement.

    Informations manquantes : ``manques_grille`` (manques ouverts de la grille de la mission, lot I) ; à défaut et si ``calculer_grille``, la
    grille est calculée comme pour la mission simple d'un petit modèle (une étape courte du président). S'y ajoutent les manques signalés
    par les membres ; fusionnés, dédoublonnés et numérotés [M1]…, ils deviennent la section « Informations à obtenir » (au plus 5
    questions) : une question du président non rattachée à un manque réel est retirée et comptée ; un manque non couvert est repris par le
    programme (grille d'abord).

    Rend ``{'texte', 'synthese_modele', 'membres', 'president', 'reponses', 'critiques', 'desaccords', 'points_desaccord', 'etapes',
    'compteurs', 'appels', 'duree_s', 'limites', 'refs_marquees', 'sources', 'sources_citees', 'mode'}``."""
    from . import references56108 as R
    debut = horloge()
    question = str(question or '')
    membres = [dict(m, id='M%d' % (i + 1)) for i, m in enumerate(membres or [])][:MAX_MEMBRES]
    president = dict(president or membres[0], id='P')
    etapes, limites = [], []
    appels = [0]
    pieces = [dict(s, texte=str(s.get('texte') or s.get('excerpt') or '')) for s in sources or [] if isinstance(s, dict)]
    trouvees, bilan = [], {'faites': 0, 'echecs': 0, 'limites': []}
    if connecteurs is not None:
        offices = R.d_office([p['texte'] for p in pieces])
        if offices:
            trouvees, bilan = R.rechercher(desk, offices, connecteurs)
            limites += bilan['limites']
    table = R.numeroter(pieces, trouvees)
    noms = noms_a_masquer(membres + [president])

    def un_appel(qui, etape, messages, max_tokens, schema=None):
        appels[0] += 1
        t0 = horloge()
        try:
            texte = appeler(qui, messages, max_tokens, schema)
        except Exception as ex:
            etapes.append({'etape': etape, 'membre': qui['id'], 'etat': 'echec', 'motif': _code(ex), 'duree_s': round(horloge() - t0, 2)})
            if isinstance(ex, Stop) and R.controle(ex):
                raise
            return None, ex
        etapes.append({'etape': etape, 'membre': qui['id'], 'etat': 'fait', 'motif': '', 'duree_s': round(horloge() - t0, 2)})
        return str(texte or ''), None

    if manques_grille is None and calculer_grille:   # même grille que la mission simple d'un petit modèle (lot I)
        manques_grille = grille_par_le_programme(appeler, president, question, table, un_appel)

    # a. réponses indépendantes, l'une après l'autre
    reponses = []
    for m in membres:
        texte, ex = un_appel(m, 'reponse', messages_membre(m, question, table, cadre), MAX_TOKENS_REPONSE)
        if ex is not None or not (texte or '').strip():
            code = _code(ex) if ex is not None else 'reponse_vide'
            m.update(etat='hors_delai' if code in ('generation_ia_delai_depasse', 'delai_http_depasse') else 'echec', motif=code)
            limites.append('Membre %s : %s ; le conseil continue avec les autres.' % (_nom_membre(m), 'hors délai' if m['etat'] == 'hors_delai' else 'en échec (%s)' % code))
            continue
        m.update(etat='fait', motif='')
        propre, _x, _inconnus = R.remplacer_numeros_inconnus(texte, table)
        reponses.append({'membre': m['id'], 'texte': propre})
    if not reponses:
        raise Stop('conseil_sans_reponse')
    connues = R.Connues([question] + [str(x) for x in contexte or []] +
                        ['%s\n%s\n%s' % (s.get('titre', ''), s.get('reference', ''), s.get('texte', '')) for s in table.values()])
    rng = random.Random(graine if graine is not None else hashlib.sha256(question.encode('utf-8')).hexdigest())
    rng.shuffle(reponses)
    for lettre, r in zip(ETIQUETTES, reponses):
        r['etiquette'] = lettre
    manques = fusionner_manques(manques_grille, reponses)   # grille d'abord, puis les manques signalés par les membres
    critiques, desaccords, synthese_modele, mode, points, questions = [], [], '', 'conseil', [], []
    compteurs = {'pourcentages_retires': 0, 'revues_illisibles': 0, 'membres_en_echec': sum(1 for m in membres if m.get('etat') != 'fait'),
                 'points_retires': 0, 'questions_retirees': 0}
    if len(reponses) == 1:
        mode = 'reponse_simple'
        seul = next(m for m in membres if m['id'] == reponses[0]['membre'])
        limites.append('Conseil réduit à un seul membre (%s) : réponse simple, sans revue croisée ni synthèse.' % _nom_membre(seul))
        questions = completer_questions([], manques)
        texte, redacteur = reponses[0]['texte'] + bloc_questions(questions), seul
    else:
        # b. revue croisée anonyme : chaque membre relit les autres sous leur lettre, ordre mélangé, noms masqués
        par_membre = {r['membre']: r for r in reponses}
        for m in membres:
            propre = par_membre.get(m['id'])
            if propre is None:
                continue
            autres = [(r['etiquette'], r['texte']) for r in reponses if r['membre'] != m['id']]
            texte, ex = un_appel(m, 'revue', messages_revue(question, table, autres, noms), MAX_TOKENS_REVUE, SCHEMA_REVUE)
            structure = None
            if ex is None:
                try:
                    from .chaine5689 import extraire_json
                    structure = valider_revue(extraire_json(texte), {l for l, _t in autres})
                except Stop:
                    structure = None
            if structure is None:
                compteurs['revues_illisibles'] += 1
                limites.append('Revue de %s illisible ou en échec : non prise en compte.' % propre['etiquette'])
            critiques.append({'par': propre['etiquette'], 'membre': m['id'], 'structure': structure})
        desaccords = desaccords_de(critiques)
        # c. synthèse du président, dans la forme du livrable demandé
        texte, ex = un_appel(president, 'synthese', messages_president(question, table, reponses, desaccords, noms, livrable, manques), MAX_TOKENS_SYNTHESE)
        redacteur = president
        if ex is not None or not (texte or '').strip():
            limites.append('Synthèse du président non produite (%s) : la réponse la mieux classée par la revue croisée est présentée, non synthétisée.'
                           % (_code(ex) if ex is not None else 'reponse_vide'))
            votes = {}
            for c in critiques:
                if c.get('structure') and c['structure']['classement']:
                    votes[c['structure']['classement'][0]] = votes.get(c['structure']['classement'][0], 0) + 1
            tete = max(sorted(votes), key=lambda l: votes[l]) if votes else reponses[0]['etiquette']
            choisie = next(r for r in reponses if r['etiquette'] == tete)
            corps, adverse, lignes, qlignes = decouper(choisie['texte'])
            corps = 'Réponse %s, la mieux classée par la revue croisée (non synthétisée) :\n%s' % (tete, corps)
            redacteur = next(m for m in membres if m['id'] == choisie['membre'])
            lignes, qlignes = [], []
        else:
            synthese_modele = texte
            corps, adverse, lignes, qlignes = decouper(texte)   # avant le retrait des numéros inconnus : [Cn], [Mn] ne sont pas des sources
            corps = R.remplacer_numeros_inconnus(corps, table)[0]
            adverse = R.remplacer_numeros_inconnus(adverse, table)[0]
            manquantes = [s for s, v in ((TITRE_ADVERSE, adverse), (TITRE_POINTS, lignes)) if not v]
            if manquantes and desaccords:
                limites.append('Synthèse incomplète : section(s) absente(s) : %s.' % ', '.join(manquantes))
        # points de désaccord : seuls ceux qui résument une critique réelle sont gardés ; le texte exact des critiques reste dans la trace
        points, retires = verifier_points(lignes, desaccords)
        for p in points:
            p['texte'] = R.remplacer_numeros_inconnus(p['texte'], table)[0]
        compteurs['points_retires'] = retires
        if retires:
            limites.append('%d point(s) de désaccord retiré(s) : sans critique correspondante dans la revue croisée, ou au-delà de %d.' % (retires, MAX_POINTS_LIVRABLE))
        # informations à obtenir : questions du président rattachées à un manque réel, puis reprise des manques non couverts (grille d'abord)
        questions, retirees = verifier_questions(qlignes, manques)
        compteurs['questions_retirees'] = retirees
        if retirees:
            limites.append('%d question(s) retirée(s) : sans manque correspondant (grille ou réponse d’un membre), ou au-delà de %d.' % (retirees, MAX_QUESTIONS))
        questions = completer_questions(questions, manques)
        for q in questions:
            q['texte'] = R.remplacer_numeros_inconnus(q['texte'], table)[0]
        texte = assembler_livrable(corps, adverse, points, desaccords, questions)
    texte, retires = retirer_pourcentages(texte)
    compteurs['pourcentages_retires'] = retires
    if retires:
        limites.append('%d estimation(s) chiffrée(s) de chance de succès retirée(s) : aucune probabilité n’est calculée.' % retires)
    # d. barrière des références (lot R) : une reprise par le rédacteur de la synthèse, puis marquage ; rien n'est présenté comme vérifié
    def reprendre(t, refs):
        out, ex = un_appel(redacteur, 'reprise', R.message_reprise(t, refs), MAX_TOKENS_SYNTHESE)
        if ex is not None:
            raise ex
        return out
    b = R.barriere(texte, connues, reprendre)
    cites = sorted({x.strip() for m in R._SN.finditer(b['texte']) for x in re.split(r'[,;]', m.group(1)) if x.strip() in table})
    final, valides, inconnus = R.remplacer_numeros(b['texte'], table)
    if b['marquees']:
        limites.append('%d référence(s) absente(s) des sources, marquée(s) « %s » : à vérifier avant tout usage.' % (b['marquees'], R.MARQUE))
    for r in reponses:   # réponses individuelles : trace seulement (numéros de sources rendus lisibles) ; la barrière porte sur le livrable
        r['texte'], _v, _i = R.remplacer_numeros(r['texte'], table)
    compteurs.update({k: b[k] for k in ('trouvees', 'inconnues', 'reprises', 'retirees', 'marquees')}, sn_valides=valides, sn_inconnus=inconnus,
                     desaccords=len(desaccords), desaccords_livrable=len(points), recherches=bilan['faites'], sources_ajoutees=len(trouvees),
                     appels_modele=appels[0], manques=len(manques), manques_grille=sum(1 for m in manques if m['origine'] == 'grille'),
                     questions_livrable=len(questions), questions_programme=sum(1 for q in questions if q.get('programme')))
    duree = round(horloge() - debut, 2)
    return {'texte': final, 'synthese_modele': synthese_modele, 'mode': mode,
            'membres': [{k: m.get(k) for k in ('id', 'fournisseur', 'type', 'modele', 'version', 'local', 'famille', 'petit', 'etat', 'motif')} for m in membres],
            'president': {k: president.get(k) for k in ('id', 'fournisseur', 'type', 'modele', 'version', 'local', 'famille')},
            'reponses': [{k: r[k] for k in ('etiquette', 'membre', 'texte')} for r in sorted(reponses, key=lambda r: r['etiquette'])],
            'critiques': critiques, 'desaccords': desaccords, 'points_desaccord': points, 'manques': manques, 'questions': questions, 'etapes': etapes, 'compteurs': compteurs, 'appels': appels[0], 'duree_s': duree,
            'limites': list(dict.fromkeys(limites)), 'refs_marquees': b['refs_marquees'], 'sources_citees': cites,
            'sources': [{'id': k, 'titre': s.get('titre', ''), 'reference': s.get('reference', ''), 'lien': s.get('lien', ''), 'texte': s.get('texte', ''),
                         'trouvee_par_le_programme': s.get('nature') == 'texte juridique trouvé par le programme'} for k, s in table.items()]}


# ------------------------------------------------------------------------------------------------ trace (table propre, écrite par le travail)
SCHEMA = ('CREATE TABLE IF NOT EXISTS conseil56108(mission_id TEXT PRIMARY KEY, matter TEXT NOT NULL DEFAULT \'\', owner TEXT NOT NULL DEFAULT \'\', '
          'etat TEXT NOT NULL, annonce TEXT NOT NULL DEFAULT \'{}\', trace TEXT NOT NULL DEFAULT \'{}\', created TEXT NOT NULL, updated TEXT NOT NULL)')
ETATS = {'demande': 'Demandé', 'en_cours': 'En cours', 'termine': 'Terminé', 'echec': 'Échec', 'suspendu': 'Suspendu', 'annule': 'Annulé'}


def ensure_schema(desk):
    """Création idempotente, par l'écriture qui en a besoin (POST de lancement, travail) : jamais par une lecture de page."""
    desk.db.execute(SCHEMA)


def _table_existe(desk):
    try:
        return bool(desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conseil56108'").fetchone())
    except sqlite3.Error:
        return False


def demande(desk, mission_id):
    """Demande de conseil de cette mission (lecture seule) ; None : mission ordinaire."""
    if not _table_existe(desk):
        return None
    row = desk.db.execute('SELECT * FROM conseil56108 WHERE mission_id=?', (str(mission_id),)).fetchone()
    return dict(row) if row else None


def verifier_demande(desk, data, action):
    """``ui_ai5689.creer`` : la demande vient de l'avocat (case explicite, valeur booléenne vraie) ; action de jugement ; membres prêts.
    Rend l'annonce (gardée avec la mission) ou None si le conseil n'est pas demandé. Jamais posé par un automatisme."""
    if not isinstance(data, dict) or data.get('conseil56108') is not True:
        return None
    if action.get('id') not in ACTIONS_JUGEMENT:
        raise Stop('conseil_reserve_aux_questions_de_jugement')
    a = annonce(desk)
    if not a['pret']:
        raise Stop('conseil_non_configure')
    return a


def enregistrer_demande(desk, mission_id, matter, owner, a):
    ensure_schema(desk)
    maintenant = _now()
    desk.db.execute('INSERT OR IGNORE INTO conseil56108(mission_id,matter,owner,etat,annonce,trace,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                    (str(mission_id), str(matter or ''), str(owner), 'demande', json.dumps(a, ensure_ascii=False), '{}', maintenant, maintenant))


def _maj(desk, mission_id, etat, trace=None):
    ensure_schema(desk)
    if trace is None:
        desk.db.execute('UPDATE conseil56108 SET etat=?, updated=? WHERE mission_id=?', (etat, _now(), str(mission_id)))
    else:
        desk.db.execute('UPDATE conseil56108 SET etat=?, trace=?, updated=? WHERE mission_id=?', (etat, json.dumps(trace, ensure_ascii=False), _now(), str(mission_id)))
    desk.db.commit()


# ------------------------------------------------------------------------------------------------ produit : transport par membre
def config_membre(desk, membre, matter='', budget='', purpose='legal_analysis'):
    """Configuration ``model.Model`` d'un membre : connexion enregistrée, modèle choisi ; politique centrale (``gate``), politique des données
    (``garde_modele``), budget et pseudonymisation d'un envoi externe restent ceux de ``Model``."""
    from .ai_gateway import provider_registry
    from .model import pseudo_sources
    from .profil5634 import resolve
    from .extensions364 import active_skill_instructions
    p = provider_registry(desk.c).get(str(membre['fournisseur']))
    if not p or not p.get('enabled', True):
        raise Stop('fournisseur_ia_inactif')
    kind = str(p.get('type') or 'ollama')
    if kind != 'ollama' and not p.get('external_data_allowed', False):
        raise Stop('fournisseur_externe_non_autorise')
    if kind == 'ollama' and est_cloud(membre['modele']):
        raise Stop('modele_cloud_refuse')
    cfg = dict(p)
    params = resolve(desk.c, purpose, 'complex')
    cfg.update(model=str(membre['modele']), provider_id=str(membre['fournisseur']), provider_type=kind, purpose=purpose, temperature=params['temperature'],
               sampling=params['sampling'], profile=params['profile'], profile_version=params['profile_version'], subprofile=params['subprofile'],
               external_policy_config=desk.c, pseudo=pseudo_sources(desk.c), skill_instructions=active_skill_instructions(desk.c, purpose),
               external_context={'matter': matter})
    if kind == 'ollama':
        cfg['context_mode'] = params['context_mode']
        if params['context_mode'] == 'fixe':
            cfg['num_ctx'] = params['num_ctx']
    else:
        cfg['max_context_chars'] = max(8000, min(int(cfg.get('max_context_chars') or 65000), 105000))
    if desk.c.get('state_dir'):
        cfg['state_dir'] = desk.c['state_dir']
    if budget:
        cfg['mission5682'] = str(budget)
    return cfg


def pour_tache(desk, ident, row, sources, mid, budget, etiquettes, fabrique=None, connecteurs=None, eff=None, grille=None):
    """Mission d'assistance (``ui_ai5689.executer``) avec le conseil demandé : délibération par les membres en vigueur, chaque appel par
    ``chaine5689.appeler_texte`` (autorité de la mission, pause, politique des données ; budget, politique centrale et pseudonymisation dans
    ``Model``). Rend l'objet brut du contrat de la mission et la trace ; la trace est enregistrée."""
    from . import chaine5689
    from . import references56108 as R
    from .model import Model
    eff = eff or effectifs(desk)
    if not eff['pret']:
        _maj(desk, ident, 'echec')
        raise Stop('conseil_non_configure')
    fabrique = fabrique or (lambda cfg: Model(cfg))
    modeles, couts = {}, {}

    def appeler(membre, messages, max_tokens, schema=None):
        cle = (membre['fournisseur'], membre['modele'])
        if cle not in modeles:
            modeles[cle] = fabrique(config_membre(desk, membre, mid, budget))
        modele = modeles[cle]
        avant = float(getattr(modele, 'usage_cost_usd', 0) or 0)
        try:
            return chaine5689.appeler_texte(desk, 'conseil_de_modeles', messages, mid, mission_id=ident, modele=modele, purpose='legal_analysis',
                                            max_tokens=max_tokens, schema=schema, owner=row['owner'], role=row['role'], budget=budget, labels=etiquettes)
        finally:
            couts[cle] = couts.get(cle, 0.0) + float(getattr(modele, 'usage_cost_usd', 0) or 0) - avant
    membres = []
    for m in eff['membres']:
        x = dict(m, version=str(m['modele']).split(':', 1)[1] if ':' in str(m['modele']) else '')
        x['petit'] = bool(m['local']) and R.petit_modele(cfg={'provider_type': 'ollama', 'parameter_size': '%gB' % m['taille'] if m.get('taille') else ''}, desk=desk)
        membres.append(x)
    president = dict(eff['president'], version=str(eff['president']['modele']).split(':', 1)[1] if ':' in str(eff['president']['modele']) else '')
    pieces = [{'id': s.get('id'), 'titre': s.get('titre', ''), 'texte': s.get('excerpt', ''), 'reference': s.get('reference', ''), 'lien': s.get('lien', '')}
              for s in sources or []]
    # Grille de la mission (lot I), quand la mission simple l'aurait établie : ses questions encore ouvertes sont reprises par le conseil ;
    # elles restent posées par le mécanisme commun (question regroupée, « inconnu » conservé, jamais reposée à chaque cycle).
    manques_grille = [{'champ_id': cid, 'champ': libelle} for cid, libelle in ((grille or {}).get('questions') or {}).items()] if grille else None
    _maj(desk, ident, 'en_cours')
    try:
        out = deliberer(row['instruction'], pieces, membres, president, appeler,
                        connecteurs if connecteurs is not None else R.ConnecteursProduit(desk, mid, 'redaction'), desk=desk, contexte=[row['instruction']],
                        livrable='consultation', manques_grille=manques_grille)
    except Stop as ex:
        code = str(ex).split(':')[0]
        _maj(desk, ident, 'annule' if code == 'mission_annulee' else ('suspendu' if R.controle(ex) else 'echec'))
        raise
    cout = round(sum(couts.values()), 6)
    for m in out['membres'] + [out['president']]:
        m['cout_usd'] = round(couts.get((m['fournisseur'], m['modele']), 0.0), 6)
    out['cout_usd'] = cout
    out['limites'] = list(eff['limites']) + out['limites']
    trace = {k: out[k] for k in ('mode', 'membres', 'president', 'reponses', 'critiques', 'desaccords', 'points_desaccord', 'manques', 'questions', 'etapes', 'compteurs', 'appels',
                                 'duree_s', 'limites', 'refs_marquees', 'cout_usd', 'texte')}
    _maj(desk, ident, 'termine', trace)
    # Questions issues des manques des membres (hors grille, déjà posée par le serveur) : questions déterminantes du contrat, regroupées par
    # ``ui_ai5689`` avec celles de la grille, comme pour une mission simple (aucune marque ajoutée au livrable)
    grille_ids = {m['id'] for m in out['manques'] if m['origine'] == 'grille'}
    brut = {'texte': out['texte'], 'objet': None, 'sources_citees': out['sources_citees'], 'informations_manquantes': [],
            'questions': [{'texte': q['texte'], 'determinante': True} for q in out['questions'] if not set(q['manques']) & grille_ids],
            'actions_proposees': [],
            'points_a_decider': ['Désaccord du conseil : ' + p['texte'] for p in out['points_desaccord']][:MAX_POINTS]}
    return {'brut': brut, 'trace': trace, 'limites': out['limites']}


def lignes_trace(desk, mission_id):
    """Suivi de la mission (« Méthode transmise ») : membres, étapes, durée, coût, désaccords, références marquées. Lecture seule."""
    d = demande(desk, mission_id)
    if not d:
        return []
    try:
        t = json.loads(d['trace'] or '{}')
    except ValueError:
        t = {}
    if not t:
        return ['Conseil de modèles demandé par l’avocat : %s.' % ETATS.get(d['etat'], d['etat'])]
    c = t.get('compteurs') or {}
    membres = ', '.join('%s %s%s' % (m.get('fournisseur'), m.get('modele'), '' if m.get('etat') == 'fait' else ' (%s)' % ('hors délai' if m.get('etat') == 'hors_delai' else 'en échec'))
                        for m in t.get('membres') or [])
    p = t.get('president') or {}
    cout = float(t.get('cout_usd') or 0)
    return ['Conseil de modèles (%s) : membres %s ; président %s %s ; %d appel(s) l’un après l’autre, %s ; coût %s.' % (
                'réponse simple, un seul membre restant' if t.get('mode') == 'reponse_simple' else 'revue croisée anonyme et synthèse',
                membres, p.get('fournisseur'), p.get('modele'), int(t.get('appels') or 0), _duree_lisible(t.get('duree_s') or 0),
                ('%.4f $' % cout).replace('.', ',') if cout else 'aucun coût d’API constaté'),
            'Points de désaccord dans le livrable : %d (vérifiés contre les critiques ; %d retiré(s)) ; critiques de la revue croisée : %d ; '
            'références marquées « [référence non vérifiée] » : %d ; estimations chiffrées retirées : %d. Réponses individuelles et texte exact '
            'des critiques : Assistance métier › Examiner les arguments (dépliables).' % (
                int(c.get('desaccords_livrable', 0)), int(c.get('points_retires', 0)), int(c.get('desaccords', 0)), int(c.get('marquees', 0)),
                int(c.get('pourcentages_retires', 0)))]


def traces_du_dossier(desk, matter, owner, role, limite=10):
    """Conseils d'un dossier visibles par cette personne (les siens ; tous pour l'administrateur). Lecture seule."""
    if not _table_existe(desk):
        return []
    if role == 'administrateur':
        rows = desk.db.execute('SELECT * FROM conseil56108 WHERE matter=? ORDER BY updated DESC LIMIT ?', (str(matter), int(limite))).fetchall()
    else:
        rows = desk.db.execute('SELECT * FROM conseil56108 WHERE matter=? AND owner=? ORDER BY updated DESC LIMIT ?', (str(matter), str(owner), int(limite))).fetchall()
    out = []
    for r in rows:
        try:
            out.append({'mission': r['mission_id'], 'etat': r['etat'], 'updated': r['updated'], 'trace': json.loads(r['trace'] or '{}'),
                        'annonce': json.loads(r['annonce'] or '{}')})
        except ValueError:
            continue
    return out


# ------------------------------------------------------------------------------------------------ lancement par l'avocat
ORIGINES = ('arguments', 'assistant', 'consultation')
# Question de jugement (stratégie, risque, opportunité, appréciation) : même motif que ``v56108-conseil.js`` (``estJugement``), sur un texte
# sans accents et en minuscules ; le volet de l'assistant ne montre l'option que pour une telle question, le service le revérifie.
JUGEMENT = re.compile(r"\b(?:strategi|risque|chance|opportun|faut-il|faudrait-il|devrions|doit-on|conseill|recommand|apprecie|apprecia|argument|"
                      r"these|contest|assigner|transiger|transaction|negocier|recours|faire appel|interjeter|pourvoi|consultation|avis sur|"
                      r"position a|interet a|vaut-il|vaudrait-il|exposition|responsabilite encourue)")


def _sans_accents(texte):
    import unicodedata
    t = unicodedata.normalize('NFD', str(texte or '').lower())
    return ''.join(c for c in t if not unicodedata.combining(c)).replace('’', "'")


def est_question_de_jugement(texte):
    return bool(JUGEMENT.search(_sans_accents(texte)))


def lancer(desk, data, owner, role, prefix=''):
    """« Demander l'avis du conseil » (Assistance métier › Examiner les arguments) : après l'annonce, case de confirmation cochée ; mission du
    circuit commun (``ui_ai5689.creer``, reçu et état comme les autres missions), action « incidence » avec la question de l'avocat."""
    if role not in ('avocat', 'administrateur'):
        raise Stop('role_insuffisant')
    if not isinstance(data, dict):
        raise Stop('requete_invalide')
    cle = str(data.get('cle') or '')
    if not CLE.fullmatch(cle):
        raise Stop('cle_requise')
    if data.get('confirmer') is not True:
        raise Stop('confirmation_requise')
    question = str(data.get('question') or '').strip()
    if not question or len(question) > 8000:
        raise Stop('question_invalide')
    origine = str(data.get('origine') or 'arguments')
    if origine not in ORIGINES:
        raise Stop('requete_invalide')
    if origine == 'assistant' and not est_question_de_jugement(question):   # volet de l'assistant : question de jugement seulement
        raise Stop('conseil_reserve_aux_questions_de_jugement')
    from . import ui_ai5689
    fonction, action = FONCTION_ARGUMENTS
    m = ui_ai5689.creer(desk, {'context': {'function_id': fonction, 'matter_id': str(data.get('matter') or '')[:80], 'instruction': question,
                                           'expected_result': 'analysis'}, 'action': action, 'request_key': 'conseil-' + cle, 'conseil56108': True},
                        owner, role, prefix)
    carte = {k: m.get(k) for k in ('id', 'state', 'label', 'instruction', 'matter', 'panneau', 'request_key')}   # reçu du lot A (numéro, étape)
    return {'mission': m.get('id'), 'etat': m.get('state'), 'carte': carte, 'message': 'Demande reçue : le conseil de modèles délibère (mission %s). Vous pouvez '
            'l’annuler depuis le suivi des missions.' % str(m.get('id') or '')[:8]}


# ------------------------------------------------------------------------------------------------ routes
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'version_en_conflit': '409 Conflict',
           'cle_reutilisee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large', 'controle_acces_indisponible': '503 Service Unavailable'}
MESSAGES = {'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
            'version_en_conflit': 'Le réglage a changé entre-temps : rechargez la page.', 'cle_reutilisee': 'Demande déjà reçue avec un autre contenu.',
            'cle_requise': 'Demande incomplète : rechargez la page.', 'confirmation_requise': 'Cochez la case après avoir lu l’annonce (membres, appels, durée, coût).',
            'conseil_non_configure': 'Aucun conseil de modèles n’est prêt : choisissez un préréglage ou des membres dans Paramètres › IA › « Conseil de modèles ».',
            'conseil_reserve_aux_questions_de_jugement': 'Le conseil est réservé aux questions de jugement.',
            'membres_conseil_nombre': 'Choisissez deux ou trois membres.', 'membres_conseil_doublon': 'Un même modèle ne peut siéger deux fois.',
            'membre_conseil_indisponible': 'Ce modèle n’est pas disponible.', 'modele_cloud_refuse': 'Un modèle « :cloud » d’Ollama n’est jamais un membre local.',
            'mode_local_strict': 'Mode local strict : membres locaux seulement.', 'prereglage_inconnu': 'Préréglage inconnu.',
            'question_invalide': 'Saisissez la question à examiner (8 000 caractères au plus).', 'origine_ou_csrf_refuse': 'Session expirée : rechargez la page.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : rien n’a été affiché.'}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):] if reste.startswith(PREFIXE) else ''
    if role not in ROLES:
        return False
    if method in ('GET', 'HEAD'):
        return reste in LECTURES_GET
    if method != 'POST' or reste not in ECRITURES:
        return False
    if reste == 'reglages':
        return role == 'administrateur'
    return role in ('avocat', 'administrateur')


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def trace_mission(desk, mission_id, owner, role):
    """Trace complète d'un conseil (réponses individuelles et critiques) : propriétaire ou administrateur, dossier recontrôlé ; sinon le même
    404 neutre. Une panne du contrôle donne 503, jamais un accès élargi."""
    d = demande(desk, mission_id)
    if not d or (d['owner'] != str(owner) and role != 'administrateur'):
        raise Stop('introuvable')
    if d['matter']:
        from . import ui_ai5689
        try:
            ui_ai5689._exiger_dossier(desk, d['matter'], owner, role, ui_ai5689._masques(desk, owner, role))
        except Stop:
            raise Stop('introuvable') from None
        except Exception:   # noqa: BLE001
            raise Stop('controle_acces_indisponible') from None
    return {'mission': d['mission_id'], 'etat': d['etat'], 'etat_libelle': ETATS.get(d['etat'], d['etat']), 'annonce': json.loads(d['annonce'] or '{}'),
            'trace': json.loads(d['trace'] or '{}')}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste not in LECTURES_GET + ECRITURES:
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(role, method, PREFIXE + reste):
        code = 'role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable'
        return _json({'error': code, 'message': MESSAGES[code]}, STATUTS[code])
    try:
        if method in ('GET', 'HEAD'):
            if reste == 'annonce':   # ``peut_lancer`` : le bouton n'est montré qu'à un rôle qui peut lancer (le service refait le contrôle)
                return _json(dict(annonce(desk), peut_lancer=role in ('avocat', 'administrateur')))
            if reste == 'reglages':
                eff = effectifs(desk)
                return _json({'reglages': eff['reglages'], 'detection': {k: eff['detection'][k] for k in ('prereglages', 'defaut', 'local_strict')},
                              'membres': [_public_membre(m) for m in eff['membres']], 'president': _public_membre(eff['president']), 'pret': eff['pret'],
                              'motif': eff['motif'], 'limites': eff['limites']})
            return _json(trace_mission(desk, str(args.get('mission') or ''), owner, role))
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        data = check_post(env, auth)
        if reste == 'reglages':
            return _json(enregistrer(desk, data, owner))
        return _json(lancer(desk, data, owner, role, prefix))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'dossier_absent', 'mission_absente'):
            code = 'introuvable'
        from .web import REASONS
        return _json({'error': code, 'message': MESSAGES.get(code) or REASONS.get(code) or code.replace('_', ' ')}, STATUTS.get(code, '400 Bad Request'))


# ------------------------------------------------------------------------------------------------ pages (lecture seule)
def _option(m, choisi=''):
    valeur = '%s::%s' % (m['fournisseur'], m['modele'])
    libelle = '%s — %s%s' % (m['modele'], 'local' if m['local'] else m['fournisseur'] + ' (API, pseudonymisé)',
                             (', %g Md' % m['taille']) if m.get('taille') else '')
    return '<option value="%s"%s>%s</option>' % (_h(valeur), ' selected' if valeur == choisi else '', _h(libelle))


def reglages_html(desk, prefix, role, installes=None, api=None):
    """Paramètres › IA › « Conseil de modèles » : préréglages et leur disponibilité, réglage en vigueur, choix de l'administrateur. GET : aucune écriture."""
    eff = effectifs(desk, installes, api)
    det, r = eff['detection'], eff['reglages']
    lignes = []
    for p in det['prereglages']:
        membres = '<br>'.join(_h(_nom_membre(m)) if m else '<span class="k108-manque">manque : %s</span>' % _h(x) for m, x in zip(p['membres'], p['manques']))
        marques = []
        if p['id'] == det['defaut']:
            marques.append('par défaut')
        if r['mode'] == 'prereglage' and r['prereglage'] == p['id']:
            marques.append('choisi')
        lignes.append('<tr><td><strong>%s</strong><br><span class="ax-muted">%s</span></td><td>%s</td><td>%s</td><td><span class="k108-etat k108-%s">%s</span>%s</td></tr>' % (
            _h(p['libelle']), _h(p['description']), membres, _h(_nom_membre(p['president'])) if p['president'] else '—', _h(p['etat']), _h(p['etat_libelle']),
            (' <span class="ax-muted">(%s)</span>' % _h(', '.join(marques))) if marques else ''))
    table = ('<table class="k108-table"><caption>Préréglages et modèles réellement disponibles</caption><thead><tr><th scope="col">Préréglage</th>'
             '<th scope="col">Membres</th><th scope="col">Président</th><th scope="col">État</th></tr></thead><tbody>%s</tbody></table>' % ''.join(lignes))
    if eff['pret']:
        a = annonce(desk, eff)
        vigueur = '<p><strong>En vigueur</strong> (%s) : %s</p>' % (_h(eff['source']), _h(a['texte']))
    else:
        vigueur = '<p class="ax-alert" role="status">%s</p>' % _h(eff['motif'])
    vigueur += ''.join('<p class="ax-muted">%s</p>' % _h(x) for x in eff['limites'])
    if det['local_strict']:
        vigueur += '<p class="ax-muted">Mode local strict : seuls les préréglages locaux sont proposés et seuls des membres locaux participent.</p>'
    if not det['disponibles']['ollama_joignable']:
        vigueur += '<p class="ax-muted">Ollama ne répond pas : les modèles locaux ne peuvent pas être détectés pour l’instant.</p>'
    formulaires = ''
    if role == 'administrateur':
        tous = [m for m in det['disponibles']['locaux'] + det['disponibles']['api'] if m['local'] or not det['local_strict']]
        opts = ''.join('<option value="%s"%s%s>%s — %s</option>' % (_h(p['id']), ' selected' if r['mode'] == 'prereglage' and r['prereglage'] == p['id'] else '',
                                                                 ' disabled' if p['etat'] == 'exclu' else '', _h(p['libelle']), _h(p['etat_libelle']))
                       for p in det['prereglages'])
        commun = '<input type="hidden" name="version" value="%d"><input type="hidden" name="cle" value="%s">' % (r['version'], secrets.token_hex(12))
        formulaires += ('<form class="m5-form k108-form" data-api="%sreglages" data-reload>%s<input type="hidden" name="mode" value="prereglage">'
                        '<label for="k108-prereglage">Choisir un préréglage</label><select id="k108-prereglage" name="prereglage">%s</select>'
                        '<button class="ax-btn" type="submit">Enregistrer le préréglage</button><p role="status" class="m5-status"></p></form>') % (PREFIXE, commun, opts)
        choisis = ['%s::%s' % (c.get('fournisseur'), c.get('modele')) for c in r['membres']] if r['mode'] == 'personnalise' else \
            ['%s::%s' % (m['fournisseur'], m['modele']) for m in eff['membres']]
        pres = ('%s::%s' % (r['president'].get('fournisseur'), r['president'].get('modele')) if r['mode'] == 'personnalise' and r['president'] else
                ('%s::%s' % (eff['president']['fournisseur'], eff['president']['modele']) if eff['president'] else ''))
        selects = ''
        for i in (1, 2, 3):
            choisi = choisis[i - 1] if i <= len(choisis) else ''
            selects += ('<label for="k108-m%d">Membre %d%s</label><select id="k108-m%d" name="membre%d"><option value="">%s</option>%s</select>' % (
                i, i, '' if i < 3 else ' (facultatif)', i, i, '— aucun —', ''.join(_option(m, choisi) for m in tous)))
        selects += ('<label for="k108-pres">Président (un des membres ou un autre modèle)</label><select id="k108-pres" name="president">'
                    '<option value="">— le plus gros des membres —</option>%s</select>' % ''.join(_option(m, pres) for m in tous))
        formulaires += ('<details class="k108-perso"%s><summary>Personnaliser les membres et le président</summary><form class="m5-form k108-form" data-api="%sreglages" '
                        'data-reload>%s<input type="hidden" name="mode" value="personnalise">%s<p class="ax-muted">%s</p>'
                        '<button class="ax-btn" type="submit">Enregistrer les membres</button><p role="status" class="m5-status"></p></form></details>') % (
            ' open' if r['mode'] == 'personnalise' else '', PREFIXE, commun.replace(r'name="cle" value="', 'name="cle" value="p'), selects, _h(RECOMMANDATION))
        if r['mode'] != 'auto':
            formulaires += ('<form class="m5-form k108-form" data-api="%sreglages" data-reload>%s<input type="hidden" name="mode" value="auto">'
                            '<button class="ax-btn ghost" type="submit">Revenir au préréglage automatique</button><p role="status" class="m5-status"></p></form>') % (
                PREFIXE, commun.replace(r'name="cle" value="', 'name="cle" value="a'))
    else:
        formulaires = '<p class="ax-muted">Seul l’administrateur choisit les membres du conseil.</p>'
    return ('<section class="ax-card k108" id="conseil56108"><h2>Conseil de modèles</h2><p>Facultatif, pour les questions de jugement '
            '(consultation, « Examiner les arguments », stratégie, appréciation du risque) : déclenché par l’avocat, jamais automatiquement. '
            'Les membres répondent l’un après l’autre sur les mêmes sources numérotées, se relisent de façon anonyme, puis le président '
            'rédige une synthèse qui expose les désaccords ; la barrière des références s’applique ensuite.</p><p class="ax-alert">%s</p>'
            '<p class="ax-muted">%s</p>%s%s%s</section>') % (_h(AVERTISSEMENT), _h(RECOMMANDATION), vigueur, table, formulaires)


def _trace_html(t):
    if not t:
        return '<p class="ax-muted">Délibération pas encore terminée.</p>'
    out = '<p>%s</p>' % _h('Synthèse : ')
    out += '<pre class="k108-texte">%s</pre>' % _h(t.get('texte', ''))
    out += ''.join('<p class="ax-muted">%s</p>' % _h(x) for x in t.get('limites') or [])
    if t.get('desaccords'):
        out += '<details class="k108-detail"><summary>Désaccords exposés : texte exact des %d critique(s)</summary><ul>%s</ul></details>' % (
            len(t['desaccords']), ''.join('<li>%s</li>' % _h(('[%s] Réponse %s, %s (revue de %s) : %s' % (d.get('id', ''), d['reponse'], d['libelle'], d['par'], d['texte']))
                                                             if d['reponse'] else '[%s] %s' % (d.get('id', ''), d['texte'])) for d in t['desaccords']))
    membres = {m['id']: m for m in t.get('membres') or []}
    for r in t.get('reponses') or []:
        m = membres.get(r['membre'], {})
        out += '<details class="k108-detail"><summary>Réponse %s — %s %s</summary><pre class="k108-texte">%s</pre></details>' % (
            _h(r['etiquette']), _h(m.get('fournisseur', '')), _h(m.get('modele', '')), _h(r['texte']))
    for c in t.get('critiques') or []:
        out += '<details class="k108-detail"><summary>Critique rédigée par %s</summary><pre class="k108-texte">%s</pre></details>' % (
            _h(c['par']), _h(json.dumps(c.get('structure'), ensure_ascii=False, indent=1) if c.get('structure') else 'Critique illisible ou en échec.'))
    out += '<details class="k108-detail"><summary>Étapes et durées</summary><ul>%s</ul></details>' % ''.join(
        '<li>%s — %s : %s (%s s)%s</li>' % (_h(e['etape']), _h(e['membre']), _h(e['etat']), _h(e['duree_s']), (' ' + _h(e['motif'])) if e.get('motif') else '')
        for e in t.get('etapes') or [])
    return out


def arguments_html(desk, url, matter, owner, role):
    """Assistance métier › Examiner les arguments : annonce, bouton « Demander l'avis du conseil », conseils du dossier (dépliables). GET : lecture."""
    eff = effectifs(desk)
    out = '<section class="k108" id="conseil56108-arguments" aria-labelledby="k108-titre"><h3 id="k108-titre">Demander l’avis du conseil de modèles</h3>'
    out += '<p class="ax-muted">%s</p>' % _h(AVERTISSEMENT)
    if not eff['pret']:
        out += '<p class="notice">%s <a href="%s">Paramètres › IA</a>.</p>' % (_h(eff['motif']), _h(url('/parametres', rubrique='ia')))
    elif role not in ('avocat', 'administrateur'):
        out += '<p class="ax-muted">Le conseil est déclenché par un avocat.</p>'
    else:
        a = annonce(desk, eff)
        out += '<p><strong>Annonce</strong> : %s</p>' % _h(a['texte'])
        out += ''.join('<p class="ax-muted">%s</p>' % _h(x) for x in a['limites'])
        # Envoi par ``v56108-conseil.js`` (JSON, CSRF, clé) sur toutes les pages ; sans script, l'envoi natif vers l'API est refusé (JSON exigé) :
        # rien n'est lancé et la question ne passe jamais dans l'adresse.
        out += ('<form class="k108-form k108-lancer" method="post" action="' + _h(url('/api440/' + PREFIXE + 'lancer')) + '" data-api="%slancer">'
                '<input type="hidden" name="matter" value="%s"><input type="hidden" name="cle" value="%s">'
                '<label for="k108-question">Question de stratégie ou d’appréciation du risque</label><textarea id="k108-question" name="question" required '
                'maxlength="8000" rows="5"></textarea><label class="d51-check"><input type="checkbox" name="confirmer" required> J’ai lu l’annonce '
                '(membres, appels, durée, coût) : lancer le conseil</label><button class="ax-btn" type="submit">Demander l’avis du conseil</button>'
                '<p role="status" class="m5-status"></p></form>') % (PREFIXE, _h(matter), secrets.token_hex(12))
    try:
        traces = traces_du_dossier(desk, matter, owner, role)
    except sqlite3.Error:
        traces = None
    if traces is None:
        out += '<p class="ax-alert" role="status">Historique des conseils indisponible pour l’instant.</p>'
    elif traces:
        out += '<h4>Conseils de ce dossier</h4>'
        for x in traces:
            out += '<details class="k108-detail"><summary>%s — %s</summary>%s</details>' % (_h(x['updated'][:16].replace('T', ' ')), _h(ETATS.get(x['etat'], x['etat'])),
                                                                                         _trace_html(x['trace']))
    return out + '</section>'

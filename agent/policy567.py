"""Dernière barrière de confidentialité, commune aux routes manuelles et aux secours.

Le contrôle porte sur le contexte ORIGINAL, avant pseudonymisation. Une exclusion
prime toujours sur un choix de fournisseur, une panne ou une attente dans la file.
"""
import json
import re

from .common import Stop, load_matters, matter_display


def matter_ids(config, value):
    found = set()
    texts = []

    def walk(item, depth=0):
        if depth > 24:
            raise Stop('contexte_politique_trop_profond')
        if isinstance(item, dict):
            for key, val in item.items():
                if key in ('matter', 'matter_id', 'dossier', 'dossier_id') and isinstance(val, (str, int)) and val:
                    found.add(str(val))
                walk(val, depth + 1)
        elif isinstance(item, (list, tuple)):
            for val in item:
                walk(val, depth + 1)
        elif isinstance(item, str):
            texts.append(item)
            if item.lstrip().startswith(('{', '[')):
                try:
                    decoded = json.loads(item)
                except (ValueError, RecursionError):
                    return
                if isinstance(decoded, (dict, list)):
                    walk(decoded, depth + 1)

    walk(value)
    text = '\n'.join(texts)
    try:
        matters = load_matters(config)
    except (Stop, OSError, KeyError, ValueError):
        matters = []
    for matter in matters:
        ident = str(matter.get('id') or '')
        labels = [ident, str(matter.get('path') or ''), matter_display(matter)]
        if any(label and re.search(r'(?<![\w-])' + re.escape(label) + r'(?![\w-])', text, re.I) for label in labels):
            found.add(ident)
    return found


def check_external(cfg, payload=None, messages=None):
    """Refuse avant toute génération distante. Ne révèle ni texte ni référence dans l'erreur."""
    if cfg.get('provider_type', cfg.get('type', 'ollama')) == 'ollama':
        return
    config = cfg.get('external_policy_config')
    if not isinstance(config, dict):
        raise Stop('politique_externe_absente')
    from .hybrid400 import policy
    rules = policy(config)
    if rules['mode'] == 'local':
        raise Stop('politique_externe_mode_local')
    purpose = str(cfg.get('purpose') or '')
    if purpose not in rules['allowed_purposes']:
        raise Stop('politique_externe_fonction_locale')
    if not rules['external_client_data_approved'] or not cfg.get('external_data_allowed'):
        raise Stop('politique_externe_non_autorisee')
    ids = matter_ids(config, [cfg.get('external_context'), payload, messages])
    excluded = set(rules['excluded_matters'])
    if ids & excluded:
        raise Stop('politique_externe_dossier_exclu')
    if excluded and not ids:
        # Sans contexte identifié, il est impossible de prouver qu'aucun dossier
        # exclu n'est transmis. Le modèle local reste utilisable.
        raise Stop('politique_externe_contexte_non_identifie')
    size = len(json.dumps([payload, messages], ensure_ascii=False, default=str))
    if size > rules['max_external_characters']:
        raise Stop('politique_externe_contexte_trop_long')

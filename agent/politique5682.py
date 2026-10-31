"""5.6.82 : politique centrale des fournisseurs d'IA (ProviderPolicy).

Cinq réglages lisibles, un seul point de contrôle :

- ``local`` : modèles locaux approuvés ; aucun transfert externe.
- ``auto`` : automatique parmi les connexions cochées par l'avocat, repli dans ce sous-ensemble seulement.
- ``chatgpt`` : abonnement ChatGPT uniquement ; aucun repli vers Ollama, OpenRouter, Mistral ou une clé API OpenAI.
- ``mistral`` / ``anthropic`` : API Mistral ou API Claude uniquement, mêmes règles.
- ``routes`` (ou réglage absent) : installation mise à jour, les choix par fonction déjà en place sont conservés tels quels.

La préférence (automatique, rapide, approfondie) ajuste la profondeur ; elle n'élargit jamais les fournisseurs permis, et « Analyse
approfondie » n'autorise ni une dépense de plus ni l'envoi d'un dossier exclu. Le contrôle est relu dans la base avant chaque
construction de modèle : une pause, un changement de mode ou une révocation entre la sélection et l'appel est pris en compte.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .common import Stop

KEY = 'ai:politique'
EXCLUSIVE = ('chatgpt', 'mistral', 'anthropic')
MODES = ('routes', 'local', 'auto') + EXCLUSIVE
PREFERENCES = ('auto', 'rapide', 'approfondi')
LABELS = {'routes': 'Réglages par fonction conservés', 'local': 'IA locale uniquement', 'auto': 'Automatique parmi mes connexions',
          'chatgpt': 'Abonnement ChatGPT uniquement', 'mistral': 'API Mistral uniquement', 'anthropic': 'API Claude uniquement'}
PREFERENCE_LABELS = {'auto': 'Automatique', 'rapide': 'Réponse rapide', 'approfondi': 'Analyse approfondie'}
DEFAULT_MISSION_CREDITS = 500.0
DEFAULT_MISSION_USD = 5.0
DEFAULT_AUTO_DAY = 300
DEFAULT_AUTO_ADVANCED_DAY = 30


def defaults():
    return {'mode': 'routes', 'preference': 'auto', 'fournisseurs': [], 'consentement': False, 'modeles': {}, 'niveau_maximal': False,
            'credits_autorises': False, 'semantique_locale': False, 'plafond_mission_credits': DEFAULT_MISSION_CREDITS,
            'plafond_mission_usd': DEFAULT_MISSION_USD, 'plafond_auto_jour': DEFAULT_AUTO_DAY, 'plafond_auto_avance_jour': DEFAULT_AUTO_ADVANCED_DAY,
            'version': 0, 'auteur': '', 'le': ''}


def _amount(value, fallback, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if 0 < number <= maximum else fallback


def normalize(raw):
    value = defaults()
    raw = raw if isinstance(raw, dict) else {}
    value['mode'] = raw.get('mode') if raw.get('mode') in MODES else 'routes'
    value['preference'] = raw.get('preference') if raw.get('preference') in PREFERENCES else 'auto'
    listed = raw.get('fournisseurs')
    value['fournisseurs'] = sorted({str(x) for x in listed if str(x)})[:12] if isinstance(listed, list) else []
    chosen = raw.get('modeles')
    value['modeles'] = {str(k): {str(n): str(s) for n, s in v.items() if str(s)} for k, v in chosen.items() if isinstance(v, dict)} if isinstance(chosen, dict) else {}
    for key in ('consentement', 'niveau_maximal', 'credits_autorises', 'semantique_locale'):
        value[key] = raw.get(key) is True
    value['plafond_mission_credits'] = _amount(raw.get('plafond_mission_credits'), DEFAULT_MISSION_CREDITS, 100000)
    value['plafond_mission_usd'] = _amount(raw.get('plafond_mission_usd'), DEFAULT_MISSION_USD, 1000)
    value['plafond_auto_jour'] = int(_amount(raw.get('plafond_auto_jour'), DEFAULT_AUTO_DAY, 100000))
    value['plafond_auto_avance_jour'] = int(_amount(raw.get('plafond_auto_avance_jour'), DEFAULT_AUTO_ADVANCED_DAY, 100000))
    try:
        value['version'] = max(0, int(raw.get('version') or 0))
    except (TypeError, ValueError):
        value['version'] = 0
    value['auteur'], value['le'] = str(raw.get('auteur') or '')[:120], str(raw.get('le') or '')[:40]
    return value


def read(config):
    """Politique chargée avec la configuration ; None si l'installation garde ses réglages par fonction."""
    raw = (config or {}).get('politique_ia')
    if not isinstance(raw, dict):
        return None
    rule = normalize(raw)
    return None if rule['mode'] == 'routes' else rule


def read_fresh(state_dir):
    """Même lecture, directement dans la base : valeur en vigueur à l'instant de l'appel, quel que soit le processus."""
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not state_dir or not path.is_file():
        return None
    try:
        db = sqlite3.connect(path, timeout=5)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        rule = normalize(json.loads(row[0]))
    except (TypeError, ValueError):
        return None
    return None if rule['mode'] == 'routes' else rule


def current(config):
    """Politique en vigueur : celle de la base dès qu'elle est lisible (un service déjà lancé suit un changement de mode sans redémarrer)."""
    state_dir = (config or {}).get('state_dir')
    if state_dir and (Path(str(state_dir)) / 'desk.sqlite3').is_file():
        return read_fresh(state_dir)
    return read(config)


def strict(rule):
    return bool(rule) and rule['mode'] in EXCLUSIVE


def allowed_types(rule):
    """Types de fournisseurs permis ; None : aucune restriction."""
    if not rule:
        return None
    if rule['mode'] == 'local':
        return {'ollama'}
    if rule['mode'] in EXCLUSIVE:
        return {rule['mode']}
    return None


def is_allowed(rule, provider_type, provider_id):
    if not rule:
        return True
    if rule['mode'] == 'auto':
        return str(provider_id) in rule['fournisseurs']
    return str(provider_type) in allowed_types(rule)


def gate(cfg):
    """Point de passage de tout ``Model`` : aucun appel hors de la politique en vigueur, même depuis un profil de repli ancien."""
    policy_config = cfg.get('external_policy_config') if isinstance(cfg.get('external_policy_config'), dict) else {}
    state_dir = cfg.get('state_dir') or policy_config.get('state_dir')
    if not state_dir or cfg.get('diagnostic5682'):
        return
    rule = read_fresh(state_dir)
    if rule and not is_allowed(rule, cfg.get('provider_type') or cfg.get('type') or 'ollama', cfg.get('provider_id') or cfg.get('id') or 'ollama'):
        raise Stop('fournisseur_interdit_par_politique')


def embeddings_allowed(state_dir):
    """Recherche par le sens avec un modèle local : jamais implicite dans un mode à fournisseur unique (recherche textuelle à la place)."""
    rule = read_fresh(state_dir)
    if strict(rule):
        return rule['semantique_locale']
    if rule and rule['mode'] == 'auto':
        return 'ollama' in rule['fournisseurs'] or rule['semantique_locale']
    return True


def local_required(config):
    """Le moteur local est-il attendu ? Non dans un mode à fournisseur unique : son absence n'est alors pas une panne."""
    return not strict(current(config))


def external_rules(config, cfg, ids, size):
    """Contrôle d'envoi lorsque la politique centrale est active. Rend False si elle ne l'est pas (règles historiques)."""
    rule = current(config)
    if not rule or rule['mode'] == 'local':
        return False
    if not is_allowed(rule, cfg.get('provider_type'), cfg.get('provider_id')):
        raise Stop('fournisseur_interdit_par_politique')
    if not rule['consentement'] or not cfg.get('external_data_allowed'):
        raise Stop('politique_externe_non_autorisee')
    from .hybrid400 import policy
    limits = policy(config)
    excluded = set(limits['excluded_matters'])
    if ids & excluded:
        raise Stop('politique_externe_dossier_exclu')
    if excluded and not ids:
        raise Stop('politique_externe_contexte_non_identifie')
    if size > limits['max_external_characters']:
        raise Stop('politique_externe_contexte_trop_long')
    return True


def provider_of(config, mode):
    """Fournisseur enregistré du type demandé (identifiant libre : un cabinet peut avoir nommé le sien autrement)."""
    from .ai_gateway import provider_registry
    registry = provider_registry(config)
    for pid, item in sorted(registry.items()):
        if item.get('type') == mode and item.get('enabled'):
            return pid, item
    for pid, item in sorted(registry.items()):
        if item.get('type') == mode:
            return pid, item
    return '', None


def candidates(config, rule):
    """Identifiants de fournisseurs utilisables par cette politique (activés et autorisés) ; le mode unique n'en a qu'un."""
    from .ai_gateway import provider_registry
    registry = provider_registry(config)
    if rule['mode'] in EXCLUSIVE:
        pid, item = provider_of(config, rule['mode'])
        return [pid] if item and item.get('enabled') and item.get('external_data_allowed') else []
    return [p for p in rule['fournisseurs'] if p in registry and registry[p].get('enabled', True)
            and (registry[p].get('type') == 'ollama' or registry[p].get('external_data_allowed'))]


def routed(config, purpose, role):
    """Configuration de routage pour ``model.routed_config`` ; None si la politique ne s'applique pas (réglages historiques, tout local)."""
    rule = current(config)
    if not rule or rule['mode'] == 'local':
        return None
    from .ai_gateway import load_config_settings, provider_registry
    if normalize(config.get('politique_ia')).get('version') != rule['version']:
        load_config_settings(config)     # service lancé avant le changement de mode : connexions et politique relues
    if rule['mode'] == 'chatgpt':
        from . import chatgpt5662
        acc = dict(_setting(config, chatgpt5662.ACCOUNT_KEY) or {})
        if acc.get('etat') != 'connecte':
            raise Stop('chatgpt_non_connecte')
        if not acc.get('plan'):
            raise Stop('chatgpt_usage_plan_non_autorise')
    found = candidates(config, rule)
    if not found:
        raise Stop('fournisseur_ia_inactif')
    if not rule['consentement']:
        raise Stop('politique_externe_non_autorisee')
    from . import chatgpt5682
    if rule['mode'] in EXCLUSIVE and rule['mode'] == 'chatgpt':
        chatgpt5682.check_deferred(config.get('state_dir'))
    from .extensions364 import active_skill_instructions
    from .hybrid400 import policy as hybrid_policy
    from .model import pseudo_sources
    from .profil5634 import resolve
    params = resolve(config, purpose, role)
    registry = provider_registry(config)
    first = registry[found[0]]
    cap = int(hybrid_policy(config).get('max_external_characters') or 120000) - 15000
    return {'routeur5682': True, 'purpose': purpose, 'role': role, 'provider_id': found[0], 'provider_type': 'routeur',
            'model': str(first.get('model') or ''), 'timeout_seconds': int(first.get('timeout_seconds') or 240),
            'max_context_chars': max(8000, min(int(first.get('max_context_chars') or 65000), cap)),
            'temperature': params['temperature'], 'sampling': params['sampling'], 'profile': params['profile'],
            'profile_version': params['profile_version'], 'subprofile': params['subprofile'], 'external_policy_config': config,
            'state_dir': config.get('state_dir', ''), 'pseudo': pseudo_sources(config),
            'skill_instructions': active_skill_instructions(config, purpose), 'display_provider': LABELS[rule['mode']], 'display_model': 'choix automatique'}


def _setting(config, key):
    path = Path(str(config.get('state_dir') or '')) / 'desk.sqlite3'
    if not path.is_file():
        return None
    db = sqlite3.connect(path, timeout=5)
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    finally:
        db.close()
    try:
        return json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _prepare_exclusive(desk, mode, data, owner):
    """Connexion prête pour un mode à fournisseur unique : rien n'est activé sans l'accord coché dans ce formulaire."""
    from . import catalogue5682
    from .ai_gateway import save_provider
    consent = data.get('consentement') is True
    if not consent:
        raise Stop('autorisation_donnees_externes_requise')
    if mode == 'chatgpt':
        from . import chatgpt5662
        acc = chatgpt5662.account(desk)
        if acc.get('etat') != 'connecte':
            raise Stop('chatgpt_non_connecte')
        if not acc.get('plan'):
            raise Stop('chatgpt_usage_plan_non_autorise')
        rows = catalogue5682.refresh(desk, chatgpt5662.PROVIDER_ID)
        active = [r['slug'] for r in rows if r['statut'] == 'actif']
        if not active:
            raise Stop('catalogue_modeles_vide')
        chatgpt5662.save_provider(desk, owner, active[0], True, True)
        return
    from .modeles5681 import DEFAULT_MONTHLY_USD, DEFAULT_REQUEST_USD
    pid, current = provider_of(desk.c, mode)
    pid = pid or mode
    current = current or {}
    save_provider(desk, pid, mode, current.get('url') or '', current.get('model') or catalogue5682.PLACEHOLDER[mode], str(data.get('api_key') or '').strip(),
                  True, True, bool(current.get('zdr_required')), float(current.get('monthly_budget_usd') or DEFAULT_MONTHLY_USD),
                  float(current.get('per_request_budget_usd') or DEFAULT_REQUEST_USD), float(current.get('input_usd_per_million') or 0),
                  float(current.get('output_usd_per_million') or 0))
    rows = [r for r in catalogue5682.refresh(desk, pid) if r['statut'] == 'actif']
    if not rows:
        raise Stop('catalogue_modeles_vide')


def save(desk, data, owner, fetch=None):
    """Enregistre le mode et ses options ; versionné, audité, sans valeur secrète."""
    if not isinstance(data, dict) or data.get('mode') not in MODES:
        raise Stop('mode_ia_invalide')
    mode = data['mode']
    previous = normalize(desk.settings(KEY, {}) or {})
    if mode in EXCLUSIVE:
        _prepare_exclusive(desk, mode, data, owner)
    elif mode == 'auto':
        from .ai_gateway import provider_registry
        listed = data.get('fournisseurs') if isinstance(data.get('fournisseurs'), list) else []
        registry = provider_registry(desk.c)
        listed = [p for p in listed if p in registry]
        if not listed:
            raise Stop('fournisseur_ia_requis')
        external = [p for p in listed if registry[p].get('type') != 'ollama']
        if external and data.get('consentement') is not True:
            raise Stop('autorisation_donnees_externes_requise')
        from . import catalogue5682
        for pid in external:
            try:
                catalogue5682.refresh(desk, pid, fetch=fetch)
            except Stop:
                pass     # liste indisponible : cette connexion n'est simplement pas candidate tant qu'elle n'est pas relue
    elif mode == 'local':
        from .ia540 import set_local
        set_local(desk)
    value = normalize({**previous, **{k: data[k] for k in ('mode', 'preference', 'fournisseurs', 'consentement', 'modeles', 'niveau_maximal',
                                                           'credits_autorises', 'semantique_locale', 'plafond_mission_credits',
                                                           'plafond_mission_usd', 'plafond_auto_jour', 'plafond_auto_avance_jour') if k in data}})
    value.update({'version': previous['version'] + 1, 'auteur': str(owner)[:120], 'le': datetime.now(timezone.utc).isoformat()})
    desk.setting(KEY, value)
    desk.c['politique_ia'] = value
    desk.audit('politique_ia_modifiee', {k: value[k] for k in ('mode', 'preference', 'fournisseurs', 'credits_autorises', 'semantique_locale', 'niveau_maximal', 'version')})
    return {'ok': True, 'message': 'Mode enregistré : %s.' % LABELS[mode], 'mode': mode, 'version': value['version']}

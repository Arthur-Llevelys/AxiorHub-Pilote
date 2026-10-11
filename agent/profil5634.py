"""5.6.34 (chapitre 5) : profil « Juridique » du modèle local, résolveur unique des paramètres et preuve des valeurs appliquées.

- Installation neuve : profil Juridique (température 0,3, top_p 0,9, top_k 40, min_p 0, repeat_penalty 1,0, repeat_last_n 64,
  contexte automatique à 32 768 jetons au plus, borné par la limite annoncée par le modèle).
- Mise à jour : une configuration qui fixait déjà ses températures (``model_routing.*_temperature``, souvent 0) garde ses valeurs,
  affichées comme « réglages conservés » ; le logiciel PROPOSE le profil Juridique sans l'imposer. Un zéro explicite n'est jamais
  remonté à 0,3 en silence.
- Sous-profil « Extraction factuelle » (température 0) pour les relevés structurés et les contrôles factuels : annoncé dans le détail
  des travaux. Les autres tâches de rédaction suivent le profil choisi.
- Un seul résolveur (``resolve``) sert ``routed_config`` ; ``Model`` transmet à Ollama les options autorisées, et à un fournisseur
  externe seulement les paramètres qu'il prend en charge (les autres sont signalés, jamais envoyés en silence).
- Les valeurs réellement appliquées au dernier appel sont conservées par fonction (``llm-parametres-5634.json`` dans l'état, sans
  aucun contenu de document ni de requête).

Les valeurs proposées sont un point de départ à évaluer sur le banc du cabinet ; elles ne garantissent pas l'absence d'erreur.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from .common import Stop

VERSION = 1
SAMPLING = ('temperature', 'top_p', 'top_k', 'min_p', 'repeat_penalty', 'repeat_last_n')
PROFILES = {
    'juridique': {'label': 'Juridique', 'version': VERSION, 'temperature': 0.3, 'top_p': 0.9, 'top_k': 40, 'min_p': 0.0,
                  'repeat_penalty': 1.0, 'repeat_last_n': 64},
    'extraction': {'label': 'Extraction factuelle', 'version': VERSION, 'temperature': 0.0, 'top_p': 0.9, 'top_k': 40, 'min_p': 0.0,
                   'repeat_penalty': 1.0, 'repeat_last_n': 64},
}
LABELS = {'juridique': 'Juridique', 'personnalise': 'Personnalisé', 'herite': 'Réglages conservés de la version précédente'}
# Relevés structurés et contrôles factuels : sous-profil « Extraction factuelle » (annoncé dans le détail des travaux).
EXTRACTION_PURPOSES = {'mail_triage', 'attachment_review', 'control'}
BOUNDS = {'temperature': (0.0, 0.3), 'top_p': (0.01, 1.0), 'top_k': (1, 200), 'min_p': (0.0, 1.0), 'repeat_penalty': (0.8, 1.5),
          'repeat_last_n': (0, 512)}
INTEGERS = {'top_k', 'repeat_last_n'}
AUTO_CONTEXT = 32768
HELP = {
    'temperature': 'Variation des réponses : 0 donne la réponse la plus constante ; 0,3 laisse un peu de souplesse de rédaction.',
    'top_p': 'Part des mots les plus probables retenue à chaque étape (0,9 : on écarte les 10 % les moins probables).',
    'top_k': 'Nombre maximal de mots candidats examinés à chaque étape.',
    'min_p': 'Seuil relatif sous lequel un mot candidat est écarté (0 : désactivé).',
    'repeat_penalty': 'Pénalité de répétition : 1,0 est neutre et préserve les formules et citations répétées volontairement.',
    'repeat_last_n': 'Nombre de jetons récents pris en compte pour la pénalité de répétition.',
    'context': 'Fenêtre de contexte : quantité de texte examinée en une fois. Plus grande, elle consomme davantage de mémoire.',
}
INTRO = ('La température réduit ou augmente la variation des réponses. La fenêtre de contexte fixe la quantité de texte que le modèle '
         'peut examiner en une fois. Une fenêtre plus grande utilise davantage de mémoire. Ces réglages ne remplacent pas la vérification '
         'des sources.')
# Paramètres réellement transmis selon le fournisseur (les autres sont signalés comme non pris en charge).
SUPPORTED_BY = {'ollama': set(SAMPLING), 'openrouter': {'temperature', 'top_p', 'top_k', 'min_p', 'repeat_penalty'},
                'openai': {'temperature', 'top_p'}, 'mistral': {'temperature', 'top_p'}, 'anthropic': {'temperature'}}


def number(value, key):
    """Saisie française (« 0,3 ») ou nombre ; bornes du profil ; refus explicite hors plage."""
    if isinstance(value, bool):
        raise Stop('reglage_nombre_invalide')
    if isinstance(value, str):
        value = value.strip().replace(' ', '').replace(' ', '').replace(',', '.')
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise Stop('reglage_nombre_invalide') from None
    lo, hi = BOUNDS[key]
    if not lo <= n <= hi:
        raise Stop('reglage_hors_plage_%s' % key)
    if key in INTEGERS:
        if n != int(n):
            raise Stop('reglage_entier_attendu_%s' % key)
        return int(n)
    return round(n, 3)


def _routing_temperatures(config):
    routing = config.get('model_routing') or {}
    return {role: routing[role + '_temperature'] for role in ('fast', 'complex', 'control') if role + '_temperature' in routing}


def current(config):
    """Profil en vigueur : {'profile', 'label', 'version', 'values', 'origin', 'context_mode', 'num_ctx'}."""
    llm = config.get('llm') if isinstance(config.get('llm'), dict) else None
    ollama = config.get('ollama') or {}
    if llm is None:
        legacy = _routing_temperatures(config)
        if legacy:   # mise à jour : réglages explicites conservés, profil Juridique proposé
            return {'profile': 'herite', 'label': LABELS['herite'], 'version': 0, 'values': {}, 'legacy_temperatures': legacy,
                    'origin': 'mise_a_jour', 'context_mode': 'fixe', 'num_ctx': int(ollama.get('num_ctx') or AUTO_CONTEXT)}
        llm = {'profile': 'juridique'}
        origin = 'defaut'
    else:
        origin = 'choisi'
    name = str(llm.get('profile') or 'juridique')
    if name not in ('juridique', 'personnalise'):
        name = 'juridique'
    values = dict(PROFILES['juridique'])
    if name == 'personnalise':
        for key in SAMPLING:
            if key in llm:
                values[key] = number(llm[key], key)
    mode = 'fixe' if str(llm.get('context_mode') or 'auto') == 'fixe' else 'auto'
    return {'profile': name, 'label': LABELS[name], 'version': int(llm.get('profile_version') or VERSION),
            'values': {k: values[k] for k in SAMPLING}, 'origin': origin, 'context_mode': mode,
            'num_ctx': int(ollama.get('num_ctx') or AUTO_CONTEXT) if mode == 'fixe' else AUTO_CONTEXT, 'seed': llm.get('seed')}


def resolve(config, purpose, role):
    """Paramètres d'une fonction : profil, version, température et options d'échantillonnage. Un zéro explicite reste zéro."""
    prof = current(config)
    if prof['profile'] == 'herite':
        temperature = float(prof['legacy_temperatures'].get(role, 0))
        return {'profile': 'herite', 'profile_version': 0, 'temperature': temperature, 'sampling': {}, 'context_mode': 'fixe',
                'num_ctx': prof['num_ctx'], 'subprofile': ''}
    values = dict(prof['values'])
    subprofile = ''
    if purpose in EXTRACTION_PURPOSES:
        values['temperature'] = 0.0
        subprofile = 'extraction'
    sampling = {k: values[k] for k in SAMPLING if k != 'temperature'}
    if prof.get('seed') not in (None, ''):
        try:
            sampling['seed'] = int(prof['seed'])
        except (TypeError, ValueError):
            pass
    return {'profile': prof['profile'], 'profile_version': prof['version'], 'temperature': float(values['temperature']), 'sampling': sampling,
            'context_mode': prof['context_mode'], 'num_ctx': prof['num_ctx'], 'subprofile': subprofile}


def context_window(cfg, model_limit=None):
    """Fenêtre effective : valeur fixée par l'administrateur, ou automatique (32 768 au plus, bornée par la limite du modèle)."""
    if cfg.get('context_mode') == 'auto':
        base = AUTO_CONTEXT
        if model_limit:
            base = min(base, int(model_limit))
        return max(2048, base)
    return int(cfg.get('num_ctx', AUTO_CONTEXT) or AUTO_CONTEXT)


def provider_options(provider_type, temperature, sampling):
    """(options transmises, paramètres non pris en charge par ce fournisseur)."""
    allowed = SUPPORTED_BY.get(provider_type, {'temperature'})
    sent = {'temperature': temperature}
    ignored = []
    for key, value in sampling.items():
        if key == 'seed':
            if provider_type in ('ollama', 'openai', 'openrouter', 'mistral'):
                sent['seed'] = value
            else:
                ignored.append(key)
        elif key in allowed:
            sent[key] = value
        else:
            ignored.append(key)
    return sent, ignored


def _path(state_dir):
    return Path(state_dir) / 'llm-parametres-5634.json'


def record(cfg, options, extra=None):
    """Valeurs effectivement transmises au dernier appel de chaque fonction (aucun contenu)."""
    state = str(cfg.get('state_dir') or '')
    if not state or not Path(state).is_dir():
        return
    path = _path(state)
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    data[str(cfg.get('purpose') or 'general')] = {
        'profile': cfg.get('profile', ''), 'profile_version': cfg.get('profile_version', 0), 'subprofile': cfg.get('subprofile', ''),
        'provider': cfg.get('provider_type', 'ollama'), 'model': cfg.get('model', ''), 'options': options,
        'ignored': list((extra or {}).get('ignored', [])), 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        **{k: v for k, v in (extra or {}).items() if k in ('duration_ms', 'prompt_tokens', 'eval_tokens', 'num_ctx_limit')}}
    try:
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        pass


def last_values(state_dir):
    try:
        return json.loads(_path(state_dir).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------------------------------------ test neutre
def neutral_test(config, http=None):
    """Bouton « Tester » : aucune pièce réelle. Serveur joignable, modèle présent, capacités, sortie JSON, génération courte.
    Distingue configuration enregistrée, connexion testée et modèle utilisable ; un réglage incompatible est expliqué."""
    from .common import HTTP
    ollama = config.get('ollama') or {}
    out = {'configuration': bool(ollama.get('url') and ollama.get('model')), 'connexion': False, 'modele_present': False,
           'json': False, 'generation': False, 'capacites': [], 'limite_contexte': None, 'options': {}, 'incompatibilites': [], 'erreur': ''}
    if not out['configuration']:
        out['erreur'] = 'ollama_non_configure'
        return out
    client = http or HTTP(ollama['url'], local_only=True, local_hosts=ollama.get('local_hosts', ()), timeout=20)
    try:
        tags = client.json('GET', '/api/tags')
        out['connexion'] = True
        names = {str(m.get('name') or '') for m in (tags.get('models') or []) if isinstance(m, dict)}
        out['modele_present'] = ollama['model'] in names or (ollama['model'] + ':latest') in names
        if not out['modele_present']:
            out['erreur'] = 'modele_ollama_absent'
            return out
        shown = client.json('POST', '/api/show', {'model': ollama['model']})
        out['capacites'] = [str(x) for x in (shown.get('capabilities') or [])][:12]
        out['limite_contexte'] = model_context_limit(shown)
        r = resolve(config, 'assistant', 'complex')
        window = context_window({'context_mode': r['context_mode'], 'num_ctx': r['num_ctx']}, out['limite_contexte'])
        if out['limite_contexte'] and r['context_mode'] == 'fixe' and r['num_ctx'] > out['limite_contexte']:
            out['incompatibilites'].append('La fenêtre fixée (%d) dépasse la limite annoncée par le modèle (%d) : elle sera réduite.' % (r['num_ctx'], out['limite_contexte']))
            window = out['limite_contexte']
        options, ignored = provider_options('ollama', r['temperature'], r['sampling'])
        options.update({'num_ctx': window, 'num_predict': 64})
        out['options'] = options
        req = {'model': ollama['model'], 'stream': False, 'options': options, 'format': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
               'required': ['ok']}, 'messages': [{'role': 'user', 'content': 'Réponds {"ok": true} en JSON.'}]}
        if 'thinking' in out['capacites']:
            req['think'] = False
        result = client.json('POST', '/api/chat', req)
        content = ((result.get('message') or {}).get('content') or '').strip()
        out['generation'] = bool(content)
        try:
            out['json'] = json.loads(content).get('ok') is True
        except (ValueError, AttributeError):
            out['json'] = False
            out['incompatibilites'].append('Le modèle n’a pas produit de JSON conforme : les relevés structurés seront moins fiables.')
    except Stop as ex:
        out['erreur'] = str(ex)
    return out


def model_context_limit(shown):
    info = shown.get('model_info') if isinstance(shown, dict) else None
    if isinstance(info, dict):
        for key, value in info.items():
            if str(key).endswith('.context_length'):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
    return None

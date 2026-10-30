"""5.6.81 : fournisseurs d'IA préréglés : liste des modèles, tarifs et plafonds préremplis, profil Mixte en un clic.

- **Charger les modèles** : la liste vient du fournisseur lui-même (``GET /models``, ``/api/tags`` pour Ollama), avec la clé enregistrée.
  Aucune donnée de dossier n'est envoyée, et un nouveau modèle apparaît sans mise à jour d'AxiorHub.
- **Tarifs** : ceux publiés par le fournisseur dans sa liste (OpenRouter) ou relevés sur sa page officielle (``TARIFS``, avec la date du
  relevé). Faute de source sûre, le champ reste à renseigner : un tarif inventé fausserait le suivi des dépenses.
- **Plafonds** : 50 $ par mois et 0,50 $ par requête, repris tels quels tant que l'avocat ne les change pas. Plafond atteint, le relais
  local de la 5.6.68 prend le relais : le travail continue et rien n'est facturé au-delà.
- **Profil Mixte** : tri, pièces jointes et contrôle en local ; tout le reste, conversation vocale comprise, via Mistral Large 4, toujours
  pseudonymisé. L'autorisation d'envoi reste une décision explicite de l'avocat, jamais déduite du bouton.
"""
from datetime import datetime, timezone
import re

from .common import HTTP, Stop

# Dollars par million de jetons (entrée, sortie). Relevés sur la page officielle du fournisseur, date en commentaire.
TARIFS = {'mistral-large-4': (0.70, 2.10)}        # docs.mistral.ai/models/mistral-large-4-0, relevé le 30 octobre 2026
DEFAULT_MONTHLY_USD = 50.0
DEFAULT_REQUEST_USD = 0.50
MIXTE_MODEL = 'mistral-large-4'
CACHE_SECONDS = 3600
NOT_CHAT = re.compile(r'embed|whisper|tts|transcri|moderation|dall-e|image|audio|realtime|speech|rerank|ocr', re.I)
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$')


def tariff(model):
    """(entrée, sortie) connus pour ce modèle, y compris ses versions datées (« mistral-large-4-2610 ») ; None sinon."""
    name = str(model or '').lower()
    for key, value in TARIFS.items():
        if name == key or name.startswith(key + '-'):
            return value
    return None


def _per_million(value):
    try:
        amount = float(value) * 1_000_000
    except (TypeError, ValueError):
        return None
    return round(amount, 4) if amount >= 0 else None


def _row(model_id, label='', pricing=None):
    known = tariff(model_id)
    item = {'id': model_id, 'label': label or model_id}
    if pricing:
        item['entree'], item['sortie'] = _per_million(pricing.get('prompt')), _per_million(pricing.get('completion'))
    elif known:
        item['entree'], item['sortie'] = known
    return item


def parse(provider_type, data):
    """Modèles de conversation d'une réponse de fournisseur, triés ; les modèles d'empreintes, de voix ou d'image sont écartés."""
    out = []
    if provider_type == 'ollama':
        for x in (data or {}).get('models') or []:
            name = str(x.get('name') or x.get('model') or '') if isinstance(x, dict) else ''
            if SAFE_ID.match(name) and not NOT_CHAT.search(name):
                out.append(_row(name))
        return sorted(out, key=lambda r: r['id'].lower())
    rows = (data or {}).get('data') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise Stop('liste_modeles_fournisseur_invalide')
    for x in rows:
        if not isinstance(x, dict):
            continue
        name = str(x.get('id') or '')
        if not SAFE_ID.match(name) or NOT_CHAT.search(name):
            continue
        caps = x.get('capabilities')
        if isinstance(caps, dict) and caps.get('completion_chat') is False:      # Mistral : indique ce que chaque modèle sait faire
            continue
        out.append(_row(name, str(x.get('display_name') or x.get('name') or ''), x.get('pricing') if isinstance(x.get('pricing'), dict) else None))
    return sorted(out, key=lambda r: r['id'].lower())


def _fetch(provider):
    kind = provider.get('type')
    if kind == 'ollama':
        http = HTTP(str(provider.get('url') or 'http://127.0.0.1:11434'), local_only=True, timeout=10, local_hosts=('ollama',))
        return http.json('GET', '/api/tags')
    from .common import read_secret
    from .model import ANTHROPIC_VERSION
    secret = read_secret(provider.get('secret_file', ''))
    http = HTTP(provider['url'], timeout=15)
    if kind == 'anthropic':
        http.headers['x-api-key'] = secret
        http.headers['anthropic-version'] = ANTHROPIC_VERSION
        return http.json('GET', '/models?limit=1000')
    http.headers['Authorization'] = 'Bearer ' + secret
    return http.json('GET', '/models')


def list_models(desk, provider_id, refresh=False, fetch=None):
    """Modèles proposés par un fournisseur (lecture seule, aucune donnée de dossier ; gardés une heure)."""
    from .ai_gateway import provider_registry
    provider_id = str(provider_id or '')
    provider = provider_registry(desk.c).get(provider_id)
    if not provider:                     # proposé à l'écran, mais encore jamais enregistré
        raise Stop('cle_api_fournisseur_absente')
    if provider.get('type') == 'chatgpt':
        raise Stop('liste_modeles_non_disponible')
    if provider.get('type') != 'ollama' and not provider.get('secret_file'):
        raise Stop('cle_api_fournisseur_absente')
    key = 'ai:models:' + provider_id
    cached = desk.settings(key, {})
    now = datetime.now(timezone.utc).timestamp()
    if not refresh and isinstance(cached, dict) and now - float(cached.get('at') or 0) < CACHE_SECONDS:
        return {'provider': provider_id, 'modeles': cached.get('modeles') or [], 'depuis_le_cache': True}
    rows = parse(provider.get('type'), (fetch or _fetch)(provider))
    desk.setting(key, {'at': now, 'modeles': rows})
    desk.audit('modeles_fournisseur_listes', {'provider': provider_id, 'nombre': len(rows), 'donnees_transmises': 'aucune'})
    return {'provider': provider_id, 'modeles': rows, 'depuis_le_cache': False}


def _resolve_mixte(rows):
    """Identifiant exact proposé par Mistral pour Mistral Large 4 (nom court, sinon sa version la plus récente)."""
    ids = [r['id'] for r in rows]
    if MIXTE_MODEL in ids:
        return MIXTE_MODEL
    dated = sorted(i for i in ids if i.startswith(MIXTE_MODEL + '-'))
    if dated:
        return dated[-1]
    raise Stop('modele_ou_route_fournisseur_absent')


def apply_mixte(desk, api_key='', consent=False, local_model='', fetch=None):
    """Profil Mixte en un clic. Exige la clé Mistral et l'autorisation d'envoi cochée par l'avocat ; garde les plafonds déjà réglés."""
    if not consent:
        raise Stop('autorisation_donnees_externes_requise')
    from .ai_gateway import provider_registry, save_provider
    from . import ia540
    current = provider_registry(desk.c).get('mistral') or {}
    monthly = float(current.get('monthly_budget_usd') or 0) or DEFAULT_MONTHLY_USD
    per_request = float(current.get('per_request_budget_usd') or 0) or DEFAULT_REQUEST_USD
    price_in, price_out = TARIFS[MIXTE_MODEL]
    save_provider(desk, 'mistral', 'mistral', current.get('url') or '', MIXTE_MODEL, str(api_key or '').strip(), True, True,
                  bool(current.get('zdr_required')), monthly, per_request, price_in, price_out)
    model = _resolve_mixte(list_models(desk, 'mistral', True, fetch)['modeles'])
    if model != MIXTE_MODEL:
        save_provider(desk, 'mistral', 'mistral', current.get('url') or '', model, '', True, True, bool(current.get('zdr_required')),
                      monthly, per_request, price_in, price_out)
    out = ia540.apply_mixed(desk, 'mistral', model, local_model, test=fetch is None)
    return {**out, 'modele': model, 'plafond_mensuel_usd': monthly, 'plafond_requete_usd': per_request,
            'message': out['message'] + ' Plafonds : %s $ par mois, %s $ par requête.' % (_fr(monthly), _fr(per_request))}


def _fr(amount):
    return ('%.2f' % amount).replace('.', ',')


def form_html(desk):
    """Carte de la page Modèles d'IA : profil Mixte en un clic (la clé n'est demandée que si elle manque)."""
    from html import escape as e
    from .ai_gateway import provider_registry
    mistral = provider_registry(desk.c).get('mistral') or {}
    local = (desk.c.get('ollama') or {}).get('model') or ''
    key = ('<p class="ax-muted">Clé Mistral déjà enregistrée.</p>' if mistral.get('secret_file') else
           '<label class="m5-field">Clé API Mistral<input type="password" name="api_key" required maxlength="4096" autocomplete="new-password"></label>')
    return ('<section class="m81-profil" id="m81-profil"><h3>Profil Mixte recommandé</h3>'
            '<p><strong>Ollama en local</strong> (%s) pour le tri des courriels, la lecture des pièces et le contrôle ; <strong>Mistral Large 4</strong> pour la '
            'rédaction, l’analyse juridique, l’assistant et la conversation vocale. Tout ce qui part est pseudonymisé.</p>'
            '<p class="ax-muted">Coût : 0,70 $ par million de jetons en entrée, 2,10 $ en sortie. Plafonds préréglés : %s $ par mois et %s $ par requête, '
            'modifiables dans Fournisseurs. Plafond atteint, le modèle local prend le relais.</p>'
            '<form class="m5-form" data-api="m5681/profil-mixte" data-reload="1">%s'
            '<label class="r52-day"><input type="checkbox" name="consent" required> J’autorise l’envoi à Mistral des données pseudonymisées nécessaires à ces fonctions</label>'
            '<button class="ax-btn" type="submit">Activer ce profil</button><p role="status" class="m5-status"></p></form></section>') % (
        e(local or 'modèle à régler'), _fr(DEFAULT_MONTHLY_USD), _fr(DEFAULT_REQUEST_USD), key)

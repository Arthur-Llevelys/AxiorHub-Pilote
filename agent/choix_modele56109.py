"""5.6.109 (lot T) : choix exact du modèle pour chaque fonction, et niveau de contexte transmis par fonction.

Demande du cabinet (24/11/2026) : « configurer exactement le choix du LLM pour chaque tâche » depuis le tableau « Modèles locaux et
routage », jusque-là en lecture seule ; puis ne plus transmettre tout le dossier à chaque tâche (crédits et abonnements).

Règles tenues ici :
- un seul réglage, ``ai:choix_modele56109``, distinct des anciennes routes ``ai:route:*`` : une installation mise à jour (routes héritées,
  routes écrites par le mode mixte) n'a AUCUN choix épinglé ; tant que l'administrateur n'a rien choisi, ``resoudre`` rend ``None`` et
  ``model.routed_config`` suit son chemin habituel, octet pour octet ;
- un choix fixé appelle ce modèle et lui seul : la configuration rendue ne porte ni secours, ni hybride, ni routeur automatique, ni repli
  budgétaire, et ``executer`` (point d'appel de ``Model.complete``) le revérifie ; un échec est une erreur qui nomme la fonction et le
  modèle, jamais une substitution ;
- le choix n'élargit jamais ce qui est permis : il est contrôlé à l'enregistrement ET à l'exécution contre la politique en vigueur (mode
  « IA locale uniquement », mode à fournisseur unique, mode automatique et ses connexions cochées, consentement, ``external_data_allowed``,
  politique des données en « local strict ») ; un refus est expliqué, rien n'est envoyé, la politique n'est pas modifiée. Un modèle LOCAL
  fixé reste admis dans un mode externe exclusif : rien ne quitte le serveur (``local_epingle_admis``, lu par ``politique5682.gate``) ;
- quand le local est exigé pour une demande (``model.local_seul_actif``, dossier exclu des envois externes), un choix externe ne s'applique
  pas : la demande suit la règle générale actuelle et la trace de l'appel le dit ; la relance « avec la connexion choisie par l'avocat »
  (``politique5682.forced_provider``) garde la priorité pour cette relance ;
- le niveau de contexte (« réduit » / « normal ») agit sur les BORNES d'assemblage de ``docrequest520.context`` (nombre de fichiers listés,
  de passages, de courriels, d'événements, de faits), jamais par troncature du texte sérialisé ; « normal » = bornes historiques. Les pièces
  jointes et documents choisis par l'avocat ne sont jamais retirés. Ce qui est écarté est dit (notes de la mission, trace de l'appel).

Aucun appel réseau à l'affichage ; aucune écriture pendant une lecture (R05) : la décision enregistrée l'est au moment de l'appel.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from html import escape as e
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading

from .common import Stop

KEY = 'ai:choix_modele56109'
CONFIG_KEY = 'choix_modele56109'
GENERAL = 'general'
NIVEAUX = ('reduit', 'normal')       # « étendu » n'est pas proposé : voir BORNES
NIVEAU_LIBELLES = {'reduit': 'Réduit', 'normal': 'Normal'}
# Bornes d'assemblage de ``docrequest520.context``. « normal » = valeurs historiques (5.6.67) : aucune modification à la mise à jour.
# « réduit » : de quoi répondre à un courriel simple — identité du dossier et client (toujours), les 4 passages les plus pertinents (le
# classement de l'index est gardé), les 4 courriels les plus récents dont 3 avec extrait (correspondants et dernier état des échanges), les
# 8 prochains événements de l'agenda (à défaut, les plus récents), les 10 faits validés les plus récents (épinglés d'abord, provenance
# gardée), les 20 fichiers les plus récents. Ordre de grandeur : 4 × 1 800 + 3 × 1 500 + listes courtes ≈ 12 000 à 16 000 caractères, contre
# 40 000 à 60 000 au niveau normal. Pièces jointes et documents choisis par l'avocat : jamais retirés (hors de ces bornes).
# « étendu » : non proposé. ``maildraft5613.compose`` refuse au-delà de 90 000 caractères (``contexte_courriel_depasse_contexte``) et la
# fenêtre locale (``num_ctx``) comme ``max_context_chars`` externe sont déjà les plafonds : relever les bornes donnerait des refus, pas
# des réponses meilleures. Aucun plafond existant ne le permet proprement.
BORNES = {'normal': {'fichiers': 80, 'passages': 12, 'courriels': 12, 'extraits_courriels': 6, 'agenda': 20, 'faits': 25},
          'reduit': {'fichiers': 20, 'passages': 4, 'courriels': 4, 'extraits_courriels': 3, 'agenda': 8, 'faits': 10}}
CATEGORIES = (('pieces_jointes', 'pièces choisies ou jointes'), ('fichiers', 'noms de fichiers'), ('extraits', 'passages'),
              ('courriels', 'courriels'), ('agenda', 'événements'), ('faits', 'faits validés'))
REPLIS = ('secours', 'hybrid_external', 'hybrid_config', 'routeur5682', 'budget_fallback')
MODELE_RX = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}')
MESSAGES = {
    'choix_modele_connexion_inactive': 'La connexion choisie pour cette fonction est désactivée ou absente : rien n’a été envoyé. Choisissez une autre connexion ou « Selon le mode général ».',
    'choix_modele_externe_non_autorise': 'L’envoi de données à cette connexion n’est pas autorisé (case « J’autorise l’envoi… » décochée) : rien n’a été envoyé, la politique n’a pas été modifiée.',
    'choix_modele_interdit_local': 'Le mode « IA locale uniquement » est en vigueur : un modèle externe ne peut pas être fixé pour cette fonction. Rien n’a été envoyé ; la politique n’a pas été modifiée.',
    'choix_modele_interdit_par_politique': 'Le mode général en vigueur ne permet pas cette connexion : le choix d’un modèle n’élargit jamais ce qui est permis. Rien n’a été envoyé ; la politique n’a pas été modifiée.',
    'choix_modele_consentement_absent': 'L’autorisation d’envoi de données à un service externe n’a pas été donnée dans le mode général : rien n’a été envoyé.',
    'choix_modele_a_choisir': 'Modèle à choisir : le modèle fixé pour cette fonction n’est plus disponible (désinstallé ou retiré du catalogue de la clé). Aucun autre modèle n’a été utilisé ; choisissez-en un dans Paramètres › Modèles d’IA.',
    'choix_modele_non_installe': 'Ce modèle n’est pas installé sur le moteur Ollama du serveur : choisissez un modèle de la liste.',
    'choix_modele_liste_locale_indisponible': 'Liste des modèles installés indisponible (Ollama ne répond pas) : le choix n’a pas été enregistré.',
    'choix_modele_catalogue_inconnu': 'Le catalogue des modèles de cette connexion n’a jamais été lu : testez la connexion (Paramètres › Connexions) avant de fixer un modèle.',
    'choix_modele_invalide': 'Nom de modèle invalide.',
    'choix_modele_distant': 'Ce modèle Ollama est traité à distance (« cloud ») : il ne peut pas être fixé comme modèle local.',
    'choix_modele_modifie': 'Le réglage du modèle de cette fonction a changé pendant le travail : rien n’a été envoyé. Relancez la demande.',
    'choix_modele_niveau_invalide': 'Niveau de contexte inconnu.',
    'version_perimee': 'Le réglage a changé entre-temps : rechargez la page. Rien n’a été modifié.',
    'fonction_ia_invalide': 'Fonction inconnue.',
}
ORIGINE = 'choix fixé dans Paramètres'


def message(code):
    return MESSAGES.get(str(code).split(':')[0], 'Choix refusé (%s).' % code)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ================================================================================================ lecture du réglage
def defaults():
    return {'version': 0, 'fonctions': {}}


def normaliser(raw):
    """Forme sûre : une entrée inconnue ou incomplète n'épingle rien (jamais lue comme un choix)."""
    from .ai_gateway import PURPOSES
    value = defaults()
    raw = raw if isinstance(raw, dict) else {}
    try:
        value['version'] = max(0, int(raw.get('version') or 0))
    except (TypeError, ValueError):
        value['version'] = 0
    fonctions = raw.get('fonctions') if isinstance(raw.get('fonctions'), dict) else {}
    for purpose, item in fonctions.items():
        if purpose not in PURPOSES or purpose == 'browser_navigation' or not isinstance(item, dict):
            continue
        connexion, modele = str(item.get('connexion') or ''), str(item.get('modele') or '')
        entree = {'contexte': item.get('contexte') if item.get('contexte') in NIVEAUX else 'normal',
                  'par': str(item.get('par') or '')[:120], 'le': str(item.get('le') or '')[:40]}
        try:
            entree['revision'] = max(0, int(item.get('revision') or 0))
        except (TypeError, ValueError):
            entree['revision'] = 0
        if connexion and connexion != GENERAL and MODELE_RX.fullmatch(modele) and item.get('origine') == 'explicite':
            entree.update({'connexion': connexion, 'modele': modele, 'origine': 'explicite'})
        if entree.get('connexion') or entree['contexte'] != 'normal':
            value['fonctions'][purpose] = entree
    return value


def _brut(config):
    """Valeur en vigueur : celle de la base dès qu'elle est lisible (un service déjà lancé suit un changement sans redémarrer)."""
    state_dir = (config or {}).get('state_dir')
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if state_dir and path.is_file():
        try:
            db = sqlite3.connect(path, timeout=5)
            try:
                row = db.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone()
            finally:
                db.close()
            return json.loads(row[0]) if row else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
    return (config or {}).get(CONFIG_KEY)


def lire(config):
    return normaliser(_brut(config))


def choix_de(config, purpose):
    item = lire(config)['fonctions'].get(purpose) or {}
    return item if item.get('connexion') else None


def niveau_contexte(config, purpose):
    if not purpose:
        return 'normal'
    return (lire(config)['fonctions'].get(purpose) or {}).get('contexte', 'normal')


def bornes(config, purpose):
    niveau = niveau_contexte(config, purpose)
    return niveau, dict(BORNES[niveau])


def nombre_epingles(config):
    return sum(1 for x in lire(config)['fonctions'].values() if x.get('connexion'))


_etat = threading.local()


@contextmanager
def sans_choix():
    """Résolution « selon le mode général » (affichage du modèle général, demande qui exige le local) ; ce fil seulement."""
    avant = getattr(_etat, 'ignore', False)
    _etat.ignore = True
    try:
        yield
    finally:
        _etat.ignore = avant


def _ignore():
    return bool(getattr(_etat, 'ignore', False))


# ================================================================================================ intersection avec la politique
def _nuage(model):
    tag = str(model or '').rsplit(':', 1)[-1].lower()
    return tag == 'cloud' or tag.endswith('-cloud')


def verifier_autorise(config, provider_id, provider, purpose):
    """Le choix est-il permis par la politique en vigueur ? Stop expliqué sinon (rien n'est modifié ni envoyé)."""
    if not provider or not provider.get('enabled', True):
        raise Stop('choix_modele_connexion_inactive')
    ptype = str(provider.get('type') or 'ollama')
    if ptype == 'ollama':
        return ptype                  # local : rien ne quitte le serveur, dans aucun mode
    if not provider.get('external_data_allowed', False):
        raise Stop('choix_modele_externe_non_autorise')
    from . import politique5682
    rule = politique5682.current(config)
    if rule and rule['mode'] == 'local':
        raise Stop('choix_modele_interdit_local')
    if rule:
        if not politique5682.is_allowed(rule, ptype, provider_id):
            raise Stop('choix_modele_interdit_par_politique')
        if not rule['consentement']:
            raise Stop('choix_modele_consentement_absent')
    else:
        from .hybrid400 import policy as hybrid_policy
        hybrid = hybrid_policy(config)
        if hybrid['mode'] == 'local':
            raise Stop('choix_modele_interdit_local')       # « Tout en local »
        if hybrid['mode'] == 'hybrid' and (provider_id != hybrid['external_provider'] or purpose not in hybrid['allowed_purposes']):
            raise Stop('choix_modele_interdit_par_politique')
    try:
        from . import data_policy5689
        if data_policy5689.charger(config.get('state_dir'))['mode'] == 'local_strict':
            raise Stop('choix_modele_interdit_local')
    except Stop:
        raise
    except Exception:   # noqa: BLE001 — politique des données illisible : refus, jamais un accès élargi
        raise Stop('politique_illisible') from None
    return ptype


def local_epingle_admis(cfg):
    """``politique5682.gate`` : un modèle LOCAL fixé par l'administrateur pour cette fonction reste admis dans un mode externe exclusif
    (rien ne quitte le serveur). Relu dans la base : la configuration seule ne suffit jamais."""
    pin = cfg.get('choix56109') if isinstance(cfg.get('choix56109'), dict) else None
    if not pin or not pin.get('applique') or str(cfg.get('provider_type') or cfg.get('type') or 'ollama') != 'ollama':
        return False
    if str(cfg.get('provider_id') or cfg.get('id') or 'ollama') != 'ollama':
        return False
    config = cfg.get('external_policy_config') if isinstance(cfg.get('external_policy_config'), dict) else {'state_dir': cfg.get('state_dir')}
    actuel = choix_de(config, pin.get('fonction'))
    return bool(actuel) and actuel['connexion'] == 'ollama' and actuel['modele'] == cfg.get('model') == pin.get('modele')


# ================================================================================================ résolution (model.routed_config)
def resoudre(config, purpose, role):
    """Configuration verrouillée de la fonction épinglée ; ``None`` : aucun choix (règle générale inchangée). Stop si interdit."""
    from .ai_gateway import PURPOSES, provider_registry
    if _ignore() or purpose not in PURPOSES or purpose == 'browser_navigation' or 'ollama' not in config:
        return None
    c = choix_de(config, purpose)
    if not c:
        return None
    from .politique5682 import forced_provider
    if forced_provider():
        return None                   # relance demandée par l'avocat avec une connexion précise : elle prime pour cette relance
    registry = provider_registry(config)
    provider = registry.get(c['connexion'])
    from .model import local_seul_actif, pseudo_sources
    if local_seul_actif() and str((provider or {}).get('type') or '') != 'ollama':
        # local exigé pour cette demande : le choix externe ne s'applique pas ; règle actuelle (local), dite dans la trace de l'appel
        from .model import routed_config
        with sans_choix():
            base = routed_config(config, purpose)
        base['choix56109'] = {'applique': False, 'motif': 'local_exige', 'fonction': purpose, 'connexion': c['connexion'], 'modele': c['modele']}
        return base
    ptype = verifier_autorise(config, c['connexion'], provider, purpose)
    model = c['modele']
    if ptype == 'ollama' and _nuage(model):
        raise Stop('choix_modele_distant')
    if ptype != 'ollama':
        from .modeles_catalogue56109 import connus
        slugs = connus(config.get('state_dir'), c['connexion'])
        if slugs is not None and model not in slugs:
            from .erreurs5634 import Qualified
            raise Qualified('choix_modele_a_choisir', fonction=PURPOSES[purpose][0], modele=model, connexion=c['connexion'])
    base = dict(provider)
    for cle in REPLIS:
        base.pop(cle, None)
    base.update({'model': model, 'provider_id': c['connexion'], 'provider_type': ptype, 'purpose': purpose})
    from .profil5634 import resolve as _resolve_profile
    params = _resolve_profile(config, purpose, role)
    base.update({'temperature': params['temperature'], 'sampling': params['sampling'], 'profile': params['profile'],
                 'profile_version': params['profile_version'], 'subprofile': params['subprofile']})
    if ptype == 'ollama':
        base['context_mode'] = params['context_mode']
        if params['context_mode'] == 'fixe':
            base['num_ctx'] = params['num_ctx']
    base['external_policy_config'] = config
    if ptype != 'ollama':
        from .hybrid400 import policy as hybrid_policy
        base['max_context_chars'] = max(8000, min(int(base.get('max_context_chars') or 65000), int(hybrid_policy(config).get('max_external_characters') or 120000) - 15000))
    if config.get('state_dir'):
        base['state_dir'] = config['state_dir']
    base['pseudo'] = pseudo_sources(config)
    from .extensions364 import active_skill_instructions
    base['skill_instructions'] = active_skill_instructions(config, purpose)
    base['display_provider'], base['display_model'] = c['connexion'], model
    base['choix56109'] = {'applique': True, 'fonction': purpose, 'libelle': PURPOSES[purpose][0], 'connexion': c['connexion'], 'type': ptype,
                          'modele': model, 'revision': c.get('revision', 0), 'origine': ORIGINE}
    return base


# ================================================================================================ point d'appel (Model.complete)
def _noter_trace(valeur):
    try:
        from . import trace480
        trace480.note(choix56109=valeur)
    except Exception:   # noqa: BLE001 — la trace ne change jamais l'issue de l'appel
        pass


def _oublier_trace():
    try:
        from . import trace480
        ctx = trace480.CTX.get()
        if ctx is not None:
            ctx.get('notes', {}).pop('choix56109', None)
    except Exception:   # noqa: BLE001
        pass


def garde(cfg):
    """Verrou : aucun mécanisme de repli, et le fournisseur et le modèle sont exactement ceux du choix."""
    pin = cfg.get('choix56109') or {}
    if any(cfg.get(k) for k in REPLIS):
        raise Stop('choix_modele_modifie')
    if (str(cfg.get('model') or '') != pin.get('modele') or str(cfg.get('provider_id') or cfg.get('id') or 'ollama') != pin.get('connexion')
            or str(cfg.get('provider_type') or cfg.get('type') or 'ollama') != pin.get('type')):
        raise Stop('choix_modele_modifie')


def _dossier_exclu(cfg, messages):
    from .hybrid400 import policy as hybrid_policy
    from .policy567 import matter_ids
    config = cfg.get('external_policy_config') or {}
    exclus = set(hybrid_policy(config)['excluded_matters'])
    if not exclus:
        return False
    return bool(matter_ids(config, [cfg.get('external_context'), messages]) & exclus)


def _decision(cfg, pin, etat, raison, messages):
    """Décision enregistrée avant l'appel (journal ``decisions_5682`` commun) : fonction, connexion, modèle, « choix fixé dans Paramètres »."""
    from . import routage5682, politique5682
    state_dir = cfg.get('state_dir') or (cfg.get('external_policy_config') or {}).get('state_dir')
    if not state_dir:
        return None
    rule = politique5682.current(cfg.get('external_policy_config') or {'state_dir': state_dir})
    d = {'uid': secrets.token_hex(12), 'mission': routage5682._mission(), 'etape': 'completion', 'proprietaire': str(cfg.get('proprietaire5682') or ''),
         'dossier_h': '', 'fonction': pin['fonction'], 'type_tache': 'choix_fixe', 'niveau': 'choix_fixe', 'fournisseur': pin['connexion'],
         'type_fournisseur': pin.get('type', ''), 'modele': pin['modele'], 'nom_modele': pin['modele'], 'effort': '', 'raisons': ['choix_fixe_parametres'],
         'raison_courte': raison, 'mode': 'choix_fixe', 'politique_version': int((rule or {}).get('version') or 0), 'estimation': None,
         'unite': 'aucune' if pin.get('type') == 'ollama' else ('credits' if pin.get('type') == 'chatgpt' else 'usd'), 'provisoire': False,
         'escalade_de': '', 'automatique': routage5682._automatic(), 'routage_mesure': None}
    try:
        routage5682._record(state_dir, d)
    except sqlite3.Error:
        return None
    return d


def _clore(cfg, decision, etat, code=''):
    if not decision:
        return
    try:
        from . import routage5682
        routage5682.close(cfg.get('state_dir') or (cfg.get('external_policy_config') or {}).get('state_dir'), decision, etat, code)
    except sqlite3.Error:
        pass


def _plafonds(cfg, pin, messages, max_tokens, decision):
    """Choix externe sous une politique centrale : plafond par mission et plafond quotidien des travaux automatiques, comme pour le routeur
    (la réservation par requête et par mois de ``budget567`` s'applique ensuite dans le transport). Rend l'identifiant de réservation."""
    from . import politique5682
    config = cfg.get('external_policy_config') or {}
    rule = politique5682.current(config)
    if not rule or pin.get('type') == 'ollama' or not decision:
        return None
    if rule['mode'] == 'mistral' and pin.get('type') == 'mistral':      # incident en cours : la ligne attend, sans repli (comme le routeur)
        from . import mistral5696
        held = mistral5696.incident_du_mode(config)
        if held:
            raise Stop(held)
    from . import catalogue5682, conso5682, routage5682
    from .ai_gateway import provider_registry
    provider = provider_registry(config).get(pin['connexion']) or {}
    row = next((r for r in catalogue5682.entries(config.get('state_dir'), pin['connexion']) if r.get('slug') == pin['modele']), {'slug': pin['modele']})
    prices = catalogue5682.prices(provider, row)
    if prices[3] == 'usd' and prices[0] is not None:
        cfg['input_usd_per_million'], cfg['output_usd_per_million'] = prices[0], prices[2]
    if prices[3] == 'usd' and prices[0] is None:
        prices = (float(cfg.get('input_usd_per_million') or 0) or None, None, float(cfg.get('output_usd_per_million') or 0) or None, 'usd')
    if routage5682._automatic():
        from .sobriete5682 import check_daily
        check_daily(config.get('state_dir'), rule, row.get('niveau') or 'standard')
    entree = len(json.dumps(messages, ensure_ascii=False, default=str)) // 3
    estimation = conso5682.estimate(prices, entree, int(max_tokens))
    limite = rule['plafond_mission_credits'] if prices[3] == 'credits' else rule['plafond_mission_usd']
    return conso5682.reserve(config.get('state_dir'), decision['mission'], estimation, prices[3], limite, decision['uid'], pin['connexion'],
                             pin['modele'], pin['fonction'])


def executer(model, messages, temperature, max_tokens, json_schema):
    """``Model.complete`` d'une fonction épinglée : verrou, fraîcheur du choix et de la politique, dossier exclu (règle générale), décision
    enregistrée, plafonds, puis l'appel par le circuit commun (politique des données, pseudonymisation, budget, pause, journal)."""
    from .ai_gateway import PURPOSES, provider_registry
    from .arret5643 import STOP_CODES
    from .erreurs5634 import Qualified
    cfg = model.cfg
    pin = cfg['choix56109']
    garde(cfg)
    config = cfg.get('external_policy_config') or {'state_dir': cfg.get('state_dir')}
    actuel = choix_de(config, pin['fonction'])
    if not actuel or actuel['connexion'] != pin['connexion'] or actuel['modele'] != pin['modele']:
        raise Stop('choix_modele_modifie')
    verifier_autorise(config, pin['connexion'], provider_registry(config).get(pin['connexion']), pin['fonction'])
    if pin.get('type') != 'ollama' and _dossier_exclu(cfg, messages):
        # dossier exclu des envois externes : le choix externe ne s'applique pas ; la règle générale actuelle décide (local, ou refus expliqué)
        from .model import Model, routed_config
        with sans_choix():
            general = routed_config(config, pin['fonction'])
        general['external_context'] = cfg.get('external_context')
        _noter_trace({'applique': False, 'motif': 'dossier_exclu', 'fonction': pin['fonction'], 'connexion': pin['connexion'], 'modele': pin['modele']})
        try:
            child = Model(general)
            value = child.complete(messages, temperature, max_tokens, json_schema)
        finally:
            _oublier_trace()
        model.last_provider, model.last_model = getattr(child, 'last_provider', ''), getattr(child, 'last_model', '')
        model.usage_cost_usd += float(getattr(child, 'usage_cost_usd', 0) or 0)
        return value
    libelle = PURPOSES.get(pin['fonction'], (pin['fonction'],))[0]
    raison = 'Choix fixé dans Paramètres : fonction « %s », connexion %s, modèle %s.' % (libelle, pin['connexion'], pin['modele'])
    decision = _decision(cfg, pin, 'decidee', raison, messages)
    if decision:
        cfg['decision5682'] = decision['uid']
        cfg['mission5682'] = decision['mission']
    reservation = None
    try:
        reservation = _plafonds(cfg, pin, messages, max_tokens, decision)
    except Stop:
        _clore(cfg, decision, 'erreur', 'plafond')
        raise
    _noter_trace({'applique': True, 'fonction': pin['fonction'], 'connexion': pin['connexion'], 'modele': pin['modele'], 'origine': ORIGINE})
    model.last_provider, model.last_model = pin['connexion'], pin['modele']
    try:
        value = model._reprise56109(messages, temperature, max_tokens, json_schema)
    except Stop as error:
        code = str(error).split(':')[0]
        _clore(cfg, decision, 'erreur', code)
        if reservation is not None:
            from . import conso5682
            conso5682.settle(cfg.get('state_dir'), reservation, 'liberee' if code in STOP_CODES or code == 'credit_fournisseur_epuise' else 'incertain')
        if code in STOP_CODES:
            raise
        detail = dict(getattr(error, 'detail', None) or {})
        raise Qualified(str(error), **{**detail, 'choix_fixe56109': True, 'fonction': libelle, 'modele': pin['modele'], 'connexion': pin['connexion']}) from error
    finally:
        _oublier_trace()
    _clore(cfg, decision, 'terminee')
    if reservation is not None:
        from . import conso5682
        conso5682.settle(cfg.get('state_dir'), reservation, 'consomme')
    return value


def expliquer_echec(code, detail):
    """``erreurs5634.explain`` : motif habituel, puis la fonction et le modèle fixés ; aucune substitution n'a été faite."""
    from .erreurs5634 import explain
    d = {k: v for k, v in (detail or {}).items() if k != 'choix_fixe56109'}
    if str(code) in MESSAGES:
        base = MESSAGES[str(code)]
    else:
        base = explain(code, d)
    return '%s — Fonction « %s », modèle « %s » (%s, %s) : aucun autre modèle n’a été essayé.' % (
        base.rstrip('.'), d.get('fonction', ''), d.get('modele', ''), d.get('connexion', ''), ORIGINE)


# ================================================================================================ enregistrement
def _installes(desk, profile=None):
    from . import economie569
    profile = profile if profile is not None else economie569.model_profile(desk)
    if not profile.get('available'):
        return None
    return [str(m['name']) for m in profile.get('models', [])]


def _local_present(model, installes):
    return model in installes or (model + ':latest') in installes or (model.endswith(':latest') and model[:-7] in installes)


def verifier_modele(desk, provider_id, ptype, model):
    if not MODELE_RX.fullmatch(model or ''):
        raise Stop('choix_modele_invalide')
    if ptype == 'ollama':
        if _nuage(model):
            raise Stop('choix_modele_distant')
        installes = _installes(desk)
        if installes is None:
            raise Stop('choix_modele_liste_locale_indisponible')
        if not _local_present(model, installes):
            raise Stop('choix_modele_non_installe')
        return
    from .modeles_catalogue56109 import connus, exiger
    if connus(desk.c['state_dir'], provider_id) is None:
        raise Stop('choix_modele_catalogue_inconnu')
    exiger(desk, provider_id, model, relecture=False)     # lot E : un modèle externe doit figurer au catalogue de la clé (aucun appel ici)


def enregistrer(desk, acteur, purpose, connexion, modele, contexte='normal', version=None):
    """Une ligne du tableau : « Selon le mode général » (``connexion`` vide ou ``general``) ou une connexion ET un modèle précis, plus le niveau
    de contexte. Versionné (409 si périmé), audité (``routage_ia_modifie``) ; un choix interdit est refusé avec son motif, rien n'est écrit."""
    from .ai_gateway import PURPOSES, provider_registry
    if purpose not in PURPOSES or purpose == 'browser_navigation':
        raise Stop('fonction_ia_invalide')
    actuel = lire(desk.c)
    if version is not None and str(version) != str(actuel['version']):
        raise Stop('version_perimee')
    contexte = str(contexte or 'normal')
    if contexte not in NIVEAUX:
        raise Stop('choix_modele_niveau_invalide')
    connexion, modele = str(connexion or '').strip(), str(modele or '').strip()
    precedent = actuel['fonctions'].get(purpose) or {}
    entree = {'contexte': contexte, 'par': str(acteur)[:120], 'le': _now(), 'revision': int(precedent.get('revision') or 0) + 1}
    if connexion and connexion != GENERAL:
        provider = provider_registry(desk.c).get(connexion)
        ptype = verifier_autorise(desk.c, connexion, provider, purpose)
        verifier_modele(desk, connexion, ptype, modele)
        entree.update({'connexion': connexion, 'modele': modele, 'origine': 'explicite'})
    fonctions = dict(actuel['fonctions'])
    fonctions[purpose] = entree
    value = normaliser({'version': actuel['version'] + 1, 'fonctions': fonctions})
    desk.setting(KEY, value)
    desk.c[CONFIG_KEY] = value
    desk.audit('routage_ia_modifie', {'purpose': purpose, 'choix56109': True, 'provider': entree.get('connexion', GENERAL), 'model': entree.get('modele', ''),
                                      'contexte': contexte, 'version': value['version']})
    libelle = PURPOSES[purpose][0]
    if entree.get('connexion'):
        texte = '%s : modèle %s (%s) fixé ; contexte %s. Aucun autre modèle ne sera utilisé pour cette fonction.' % (
            libelle, modele, connexion, NIVEAU_LIBELLES[contexte].lower())
    else:
        texte = '%s : selon le mode général ; contexte %s.' % (libelle, NIVEAU_LIBELLES[contexte].lower())
    return {'ok': True, 'version': value['version'], 'message': texte}


def remettre(desk, acteur, version=None):
    """« Tout remettre selon le mode général » : aucun modèle fixé, contexte normal partout. Le navigateur supervisé garde son réglage (lot O)."""
    actuel = lire(desk.c)
    if version is not None and str(version) != str(actuel['version']):
        raise Stop('version_perimee')
    value = {'version': actuel['version'] + 1, 'fonctions': {}}
    desk.setting(KEY, value)
    desk.c[CONFIG_KEY] = value
    desk.audit('routage_ia_modifie', {'choix56109': True, 'remise': 'mode_general', 'version': value['version'], 'par': str(acteur)[:120]})
    return {'ok': True, 'version': value['version'], 'message': 'Toutes les fonctions suivent de nouveau le mode général (contexte normal).'}


# ================================================================================================ affichage
def _resolu(desk, purpose):
    from .model import routed_config
    try:
        r = routed_config(desk.c, purpose)
        local = r.get('provider_type', 'ollama') == 'ollama' or r.get('provider_id', 'ollama') == 'ollama'
        return ('local · ' if local else 'externe · ') + str(r.get('display_model') if r.get('routeur5682') else r.get('model', '')), ''
    except Stop as ex:
        code = str(ex).split(':')[0]
        return '', code


def consommation_30j(desk):
    """Consommation par fonction et par modèle sur 30 jours, lue dans les mesures existantes (``ai_usage_v391``) : aucun nouveau compteur,
    aucune écriture (table absente : rien)."""
    path = Path(str(desk.c.get('state_dir') or '')) / 'desk.sqlite3'
    depuis = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    out = {}
    try:
        db = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, timeout=5)
        try:
            rows = db.execute("SELECT purpose,provider,model,COUNT(*),SUM(input_tokens),SUM(output_tokens),ROUND(SUM(CASE WHEN status='done' THEN estimated_cost_usd ELSE 0 END),4) "
                              'FROM ai_usage_v391 WHERE at>=? GROUP BY purpose,provider,model ORDER BY purpose,provider,model', (depuis,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return {}
    for purpose, provider, model, n, ein, eout, cost in rows:
        out.setdefault(str(purpose), []).append({'connexion': provider, 'modele': model, 'appels': n, 'entree': ein or 0, 'sortie': eout or 0,
                                                 'cout': float(cost or 0)})
    return out


def _conso_texte(lignes):
    if not lignes:
        return 'aucun appel mesuré'
    parts = []
    for x in lignes[:3]:
        cout = (' · %s $ estimés' % ('%.2f' % x['cout']).replace('.', ',')) if x['connexion'] != 'ollama' else ''
        parts.append('%s : %d appel(s), %d jetons entrée, %d sortie%s' % (x['modele'] or x['connexion'], x['appels'], x['entree'], x['sortie'], cout))
    return ' ; '.join(parts)


def tableau_html(desk, prefix, profile=None):
    """Tableau réglable de « Modèles locaux et routage » : deux listes par ligne (connexion, modèle), niveau de contexte, état réel et
    modèle réellement résolu ; « Navigateur supervisé » sur son mécanisme (lot O). Lecture seule : aucun appel réseau ni écriture."""
    from .ai_gateway import PURPOSES, provider_registry
    from . import economie569
    from .modeles_catalogue56109 import connus
    profile = profile if profile is not None else economie569.model_profile(desk)
    installes = _installes(desk, profile)
    reglage = lire(desk.c)
    registry = provider_registry(desk.c)
    connexions = [(k, v) for k, v in sorted(registry.items()) if k == 'ollama' or (v.get('enabled') and v.get('external_data_allowed'))]
    conso = consommation_30j(desk)
    num_ctx = (desk.c.get('ollama') or {}).get('num_ctx', '')
    listes, options_modeles = '', {}
    for pid, item in connexions:
        if pid == 'ollama':
            noms = installes or []
        else:
            noms = sorted(connus(desk.c['state_dir'], pid) or [], key=str.lower)
        options_modeles[pid] = noms
        listes += '<datalist id="cm109-%s">%s</datalist>' % (e(pid, quote=True), ''.join('<option value="%s">' % e(n, quote=True) for n in noms))
    rows = ''
    for purpose, (label, role) in PURPOSES.items():
        role_l = {'fast': 'rapide', 'complex': 'complexe', 'control': 'contrôle'}.get(role, role)
        ident = 'cm109-' + purpose
        if purpose == 'browser_navigation':
            from . import navigateur56109
            r = navigateur56109.etat_reglage(desk)
            etat = ('Modèle du navigateur : %s · %s (%s)' % (r.get('fournisseur') or '—', r.get('modele') or '—', r.get('origine') or '—')) if not r.get('erreur') else r.get('message', '')
            version = str(r.get('revision') or 0)
            sel = ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == r.get('fournisseur') else '', e(k + ' · ' + v.get('type', 'ollama')))
                          for k, v in connexions)
            rows += ('<tr id="%s"><th scope="row">%s</th><td>%s</td><td><form class="m5-form m5-inline cm109-form" data-api="m56109/navigateur/modele" data-reload="1">'
                     '<input type="hidden" name="version" value="%s"><label class="cm109-l">Connexion<select name="fournisseur">%s</select></label>'
                     '<label class="cm109-l">Modèle<input name="modele" maxlength="160" required value="%s" list="cm109-%s"></label>'
                     '<button class="ax-btn ghost m5-mini" type="submit">Enregistrer</button></form></td><td>—</td><td>%s</td></tr>') % (
                e(ident, quote=True), e(label), e(role_l), e(version, quote=True), sel, e(r.get('modele') or '', quote=True), e(r.get('fournisseur') or 'ollama', quote=True),
                e(etat + ' — réglage propre au navigateur (même tableau, sans second mécanisme).'))
            continue
        item = reglage['fonctions'].get(purpose) or {}
        pin = item.get('connexion') or ''
        modele = item.get('modele') or ''
        niveau = item.get('contexte', 'normal')
        resolu, code = _resolu(desk, purpose)
        a_choisir = False
        if pin:
            if pin == 'ollama':
                a_choisir = installes is not None and not _local_present(modele, installes)
            elif pin in registry:
                slugs = connus(desk.c['state_dir'], pin)
                a_choisir = slugs is not None and modele not in slugs
            else:
                a_choisir = True
        if pin and a_choisir:
            etat = '<strong>Modèle à choisir</strong> : « %s » n’est plus disponible ; la fonction s’arrête avec un message clair, sans substitution.' % e(modele)
        elif pin and code:
            etat = '<strong>Choisi par vous</strong>, refusé à l’exécution : %s' % e(message(code) if code in MESSAGES else code)
        elif pin:
            etat = '<strong>Choisi par vous</strong> : %s' % e(resolu)
        elif code:
            etat = 'Selon le mode général : non routé (%s)' % e(code)
        else:
            etat = 'Selon le mode général : %s' % e(resolu)
        if pin == 'ollama' and num_ctx:
            etat += ' <span class="ax-muted">(fenêtre de contexte locale commune : %s jetons)</span>' % e(str(num_ctx))
        sel = '<option value="general"%s>Selon le mode général</option>' % ('' if pin else ' selected')
        sel += ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == pin else '', e(('IA locale (Ollama)' if k == 'ollama' else k + ' · ' + v.get('type', '')))) for k, v in connexions)
        if pin and pin not in dict(connexions):
            sel += '<option value="%s" selected>%s (indisponible)</option>' % (e(pin, quote=True), e(pin))
        niveaux = ''.join('<option value="%s"%s>%s</option>' % (n, ' selected' if n == niveau else '', NIVEAU_LIBELLES[n]) for n in NIVEAUX)
        rows += ('<tr id="%s"><th scope="row">%s</th><td>%s</td><td><form class="m5-form m5-inline cm109-form" data-api="m56109/choix-modele/enregistrer" data-reload="1">'
                 '<input type="hidden" name="fonction" value="%s"><input type="hidden" name="version" value="%d">'
                 '<label class="cm109-l">Connexion<select name="connexion">%s</select></label>'
                 '<label class="cm109-l">Modèle<input name="modele" maxlength="160" value="%s" list="cm109-%s" placeholder="nom exact"></label>'
                 '<label class="cm109-l">Contexte transmis<select name="contexte">%s</select></label>'
                 '<button class="ax-btn ghost m5-mini" type="submit">Enregistrer</button></form></td><td>%s</td><td>%s</td></tr>') % (
            e(ident, quote=True), e(label), e(role_l), e(purpose, quote=True), reglage['version'], sel, e(modele, quote=True), e(pin or 'ollama', quote=True),
            niveaux, e(_conso_texte(conso.get(purpose))), etat)
    remise = ('<form class="m5-form m5-inline" data-api="m56109/choix-modele/remettre" data-reload="1" data-confirm="Remettre toutes les fonctions selon le mode général ?">'
              '<input type="hidden" name="version" value="%d"><button class="ax-btn ghost" type="submit">Tout remettre selon le mode général</button></form>') % reglage['version']
    return ('<p class="ax-muted">Pour chaque fonction : « Selon le mode général » (réglage par défaut), ou une connexion et un modèle précis. Un modèle fixé '
            'est appelé seul : ni routeur automatique, ni bascule, ni secours. Il n’élargit jamais ce que le mode général permet (refus expliqué). '
            '« Contexte transmis » : « Réduit » limite le nombre de passages, courriels, faits et fichiers envoyés (pièces choisies par l’avocat toujours '
            'transmises) ; « Normal » garde les bornes habituelles. Consommation : 30 derniers jours, mesures existantes.</p>%s'
            '<div class="table"><table class="vf-table cm109-table"><thead><tr><th scope="col">Fonction</th><th scope="col">Rôle</th><th scope="col">Modèle et contexte</th>'
            '<th scope="col">Consommation (30 jours)</th><th scope="col">Modèle utilisé</th></tr></thead><tbody>%s</tbody></table></div>%s') % (listes, rows, remise)


def libelle_general(desk, label):
    """``ia540.current`` : le libellé du mode général reste vrai quand des fonctions sont épinglées."""
    n = nombre_epingles(desk.c)
    return label if not n else '%s ; %d fonction(s) réglée(s) à la main' % (label, n)


# ================================================================================================ détail de la mission
def appels_mission(state_dir, job_id):
    """Pour chaque appel au modèle d'un travail (trace ``trace_calls480``, déjà écrite à chaque appel) : fonction, connexion, modèle,
    caractères réellement transmis, jetons estimés (≈ caractères / 3, l'estimation du transport local), choix fixé ou non, contexte
    transmis par catégorie et ce que le niveau a écarté. Lecture seule."""
    if not state_dir or not job_id:
        return []
    path = Path(str(state_dir)) / 'desk.sqlite3'
    try:
        db = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True, timeout=5)
        try:
            rows = db.execute('SELECT stage,purpose,provider,model,external,chars_sent,notes,status FROM trace_calls480 WHERE job_id=? ORDER BY id LIMIT 60',
                              (str(job_id),)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return []
    out = []
    for stage, purpose, provider, model, external, chars, notes, status in rows:
        try:
            n = json.loads(notes or '{}')
        except ValueError:
            n = {}
        choix = n.get('choix56109') if isinstance(n.get('choix56109'), dict) else None
        contexte = n.get('contexte56109') if isinstance(n.get('contexte56109'), dict) else None
        ligne = {'etape': stage, 'fonction': purpose, 'connexion': provider, 'modele': model, 'externe': bool(external), 'caracteres': int(chars or 0),
                 'jetons_estimes': int(chars or 0) // 3, 'etat': status,
                 'choix': (ORIGINE if choix.get('applique') else 'choix fixé non appliqué : ' + {'dossier_exclu': 'dossier exclu des envois externes',
                           'local_exige': 'modèle local exigé pour cette demande'}.get(choix.get('motif'), str(choix.get('motif')))) if choix else 'selon le mode général',
                 'contexte': contexte}
        ligne['texte'] = texte_appel(ligne)
        out.append(ligne)
    return out


def texte_appel(ligne):
    t = '%s · %s %s · %d caractères (≈ %d jetons) · %s' % (ligne['fonction'] or ligne['etape'], ligne['connexion'], ligne['modele'], ligne['caracteres'],
                                                          ligne['jetons_estimes'], ligne['choix'])
    c = ligne.get('contexte')
    if c:
        t += ' · ' + resume_contexte(c)
    return t


def resume_contexte(c):
    parts = ['%d %s (%d car.)' % (v.get('nombre', 0), lib, v.get('caracteres', 0)) for k, lib in CATEGORIES
             for v in [(c.get('categories') or {}).get(k) or {}] if v.get('nombre')]
    t = 'contexte %s : %s' % (NIVEAU_LIBELLES.get(c.get('niveau'), c.get('niveau')).lower(), ', '.join(parts) or 'vide')
    if c.get('ecarte'):
        t += ' — ' + c['ecarte']
    return t


# ================================================================================================ contexte : mesure et écarts
def phrase_ecarts(ecarts):
    libelles = {'fichiers': 'nom(s) de fichier', 'extraits': 'passage(s)', 'courriels': 'courriel(s)', 'extraits_courriels': 'extrait(s) de courriel',
                'agenda': 'événement(s)', 'faits': 'fait(s) validé(s)'}
    parts = ['%d %s' % (n, libelles[k]) for k, n in ecarts.items() if n]
    return ('%s non transmis : contexte réduit.' % ' et '.join([', '.join(parts[:-1]), parts[-1]] if len(parts) > 1 else parts)) if parts else ''


def mesurer(ctx, niveau, fonction, ecarts):
    """Taille par catégorie du contexte assemblé, notée pour les appels suivants de ce travail (``trace480.note``, jamais transmis au
    modèle). Rend la phrase des écarts (vide au niveau normal)."""
    cats = {}
    for k, _lib in CATEGORIES:
        v = ctx.get(k) or []
        cats[k] = {'nombre': len(v), 'caracteres': len(json.dumps(v, ensure_ascii=False))}
    phrase = phrase_ecarts(ecarts) if niveau == 'reduit' else ''
    try:
        from . import trace480
        trace480.note(contexte56109={'fonction': fonction, 'niveau': niveau, 'categories': cats, 'ecarts': {k: n for k, n in ecarts.items() if n},
                                     'ecarte': phrase})
    except Exception:   # noqa: BLE001
        pass
    return phrase


def selection_agenda(lignes, borne, maintenant_iso):
    """Niveau réduit : prochains événements d'abord (ordre chronologique), complétés par les plus récents passés. Déterministe."""
    futurs = [r for r in lignes if str(r[1] or '') >= maintenant_iso]
    passes = [r for r in lignes if str(r[1] or '') < maintenant_iso]
    garde = futurs[:borne]
    if len(garde) < borne:
        garde = sorted(passes, key=lambda r: str(r[1] or ''), reverse=True)[:borne - len(garde)] + garde
    return sorted(garde, key=lambda r: str(r[1] or ''))


def selection_faits(lignes, borne):
    """Niveau réduit : faits épinglés d'abord, puis validés, chacun du plus récent au plus ancien (l'ordre reçu) ; provenance inchangée."""
    epingles = [r for r in lignes if r[3] == 'pinned']
    return (epingles + [r for r in lignes if r[3] != 'pinned'])[:borne]


# ================================================================================================ routes m56109/choix-modele/
PREFIXE = 'm56109/choix-modele/'
LECTURES = ('etat',)
ECRITURES = ('enregistrer', 'remettre')
MAX_POST = 8_000
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'version_perimee': '409 Conflict',
           'requete_trop_longue': '413 Payload Too Large'}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES and role == 'administrateur'
    return method == 'POST' and reste in ECRITURES and role == 'administrateur'


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(role, method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            return _json({'reglage': lire(desk.c), 'niveaux': list(NIVEAUX), 'bornes': BORNES})
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        version = d.get('version')
        if reste == 'remettre':
            return _json(remettre(desk, owner, version))
        return _json(enregistrer(desk, owner, str(d.get('fonction') or ''), str(d.get('connexion') or ''), str(d.get('modele') or ''),
                                 str(d.get('contexte') or 'normal'), version))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in MESSAGES:
            texte = message(code)
        elif code in ('introuvable', 'role_insuffisant', 'origine_ou_csrf_refuse', 'requete_trop_longue'):
            texte = {'introuvable': 'Réglage introuvable.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
                     'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.', 'requete_trop_longue': 'Demande trop longue.'}[code]
        else:
            from .erreurs5634 import explain
            texte = explain(code, getattr(ex, 'detail', None))
        return _json({'error': code, 'message': texte}, STATUTS.get(code, '422 Unprocessable Entity'))

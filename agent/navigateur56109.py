"""5.6.109 (lot O, G13 et G24) : prise de main acquittée et navigateur supervisé utilisable avec Ollama ``qwen3.5:9b``.

Reprise produit de ``reference/navigateur_qwen109.py`` du dossier technique, raccordée au moteur existant (``navigateur5673``), au
routeur commun (``model.routed_config``, ``routage5682.child_config``) et au transport commun (``Model._feuille``). Aucun second
navigateur, client HTTP, ordonnanceur ni registre de modèles.

G13 — prise de main acquittée
- **Génération de séance** (``epoch``) incrémentée à chaque prise de main, reprise, pause et levée de pause.
- **Barrière** (``barriere``) avant CHAQUE observation (plan, adresse, texte, téléchargements) et CHAQUE action (clic, saisie, choix,
  navigation, enregistrement) : séance arrêtée, main à l'avocat, pause globale ou génération changée → la commande n'est pas envoyée
  (``DecisionPerimee``). Le verrou est court : il n'est jamais tenu pendant l'inférence.
- **Acquittement** : « la main est à vous » (``main_acquittee``) seulement quand plus aucune commande de pilotage n'est en vol. Une
  réponse du modèle arrivée après la prise de main est jetée, jamais exécutée.
- Fonction publique pour les autres lots (portails, PDF, parcours) : ``puis_je(seance, epoch)`` et ``barriere(seance, epoch, desk)``.

G24 — modèle du navigateur
- Affectation ``browser_navigation`` dans ``ai_gateway.PURPOSES`` (Paramètres › IA › Modèle utilisé par fonction) ; ``config_navigateur``
  résout fournisseur, modèle, révision de la route et profil, intersectés avec la politique du cabinet ; ni routeur automatique, ni
  hybride, ni secours, ni repli budgétaire : **aucun repli externe silencieux**. ``garde_complet`` et ``verifier_requete`` revérifient le
  verrou dans ``Model.complete`` et sur la requête Ollama finalement émise ; ``routage5682.child_config`` le propage.
- Profil Qwen borné et versionné (non téléchargé : aucun poids implicite) ; profil générique pour un autre modèle.
- Réponses structurées strictes (``schema_action``, ``decision``) et fraîcheur des cibles (``Observation``, ``verifier_avant_action``).
- ``Progression`` : trois observations sans progrès ou un cycle → la main est demandée ; le plafond de pas rend « limite atteinte ».

Limite : la compatibilité RÉELLE avec ``qwen3.5:9b`` (Windows 11, Ubuntu 24.04, trente parcours) n'est pas démontrée par ces contrats ni
par les tests à faux Ollama ; voir ``scripts/recette-navigateur-qwen56109.py``.
"""
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from .common import Stop

PURPOSE = 'browser_navigation'
ROUTE_KEY = 'ai:route:' + PURPOSE
QUALIF_KEY = 'navigateur56109:qualification'
MODEL_CIBLE = 'qwen3.5:9b'
PROFIL_VERSION = 1
# Point de départ à mesurer (sans « thinking »), d'après la fiche publique du modèle pour ce mode : ni qualification, ni garantie.
PROFIL_QWEN = {'id': 'navigateur-qwen35-9b-v1-a-qualifier', 'model': MODEL_CIBLE, 'think': False, 'num_ctx': 16384, 'num_predict': 1200,
               'temperature': 0.7, 'top_p': 0.8, 'top_k': 20, 'min_p': 0.0, 'presence_penalty': 1.5, 'repeat_penalty': 1.0}
# Autre modèle choisi par le cabinet : réglages prudents de la 5.6.73 (température 0, 1 200 jetons de sortie), bornés de même.
PROFIL_GENERIQUE = {'id': 'navigateur-generique-v1', 'model': '', 'think': False, 'num_ctx': 16384, 'num_predict': 1200,
                    'temperature': 0.0, 'top_p': 0.9, 'top_k': 40, 'min_p': 0.0, 'presence_penalty': 0.0, 'repeat_penalty': 1.0}
CHAMPS = {
    'naviguer': ({'url'}, set()),
    'cliquer': ({'ref'}, set()),
    'saisir': ({'ref', 'texte'}, {'valider'}),
    'choisir': ({'ref', 'texte'}, set()),
    'defiler': ({'direction'}, set()),
    'retour': (set(), set()), 'extraire': (set(), set()),
    'enregistrer_page': (set(), set()), 'attendre': (set(), set()),
    'demander': ({'texte'}, set()), 'terminer': ({'resume'}, set()),
}
REPLIS = ('secours', 'hybrid_external', 'hybrid_config', 'routeur5682', 'budget_fallback')
QUALIFICATIONS = {
    'teste_sur_ce_poste': 'Testé sur ce poste',
    'a_tester': 'À tester',
    'a_requalifier': 'À requalifier : poids, profil ou modèle modifiés depuis le test',
    'autre_modele': 'Autre modèle que qwen3.5:9b : la recette Qwen reste à faire',
}
ORIGINES = {'explicite': 'Choisi pour le navigateur', 'reglage_global': 'Défini par un réglage global : à confirmer',
            'herite_assistant': 'Héritée de l’assistant', 'herite_politique': 'Héritée de la politique du cabinet'}
MESSAGES = {
    'main_a_l_avocat': 'La main est à l’avocat : la décision calculée avant a été écartée.',
    'generation_perimee': 'La séance a changé (prise de main, reprise ou pause) : la décision a été écartée.',
    'observation_perimee': 'La page a changé depuis l’observation : nouvelle observation.',
    'cible_disparue_ou_modifiee': 'Le contrôle visé a disparu ou changé : nouvelle observation, aucun clic sur un contrôle voisin.',
    'execution_suspendue': 'Séance suspendue : la décision a été écartée.',
    'ia_en_pause': 'IA en pause : aucune observation ni action.',
    'navigateur_repli_interdit': 'Le modèle du navigateur est verrouillé : aucun repli vers un autre modèle ou fournisseur.',
    'navigateur_modele_modifie': 'Le modèle réellement appelé n’est pas celui choisi pour le navigateur : appel refusé.',
    'navigateur_modele_interdit_par_politique': 'La politique IA du cabinet interdit le fournisseur choisi pour le navigateur : rien n’a été envoyé.',
    'navigateur_version_modele_a_requalifier': 'Les poids du modèle ont changé pendant la séance : qualification à refaire.',
    'navigateur_modele_distant': 'Le modèle Ollama choisi est distant (cloud) : refusé pour le navigateur local.',
    'navigateur_profil_non_transmis': 'Le profil du navigateur n’a pas été transmis tel quel au modèle : appel refusé.',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


# ======================================================================================== contrats (reprise de la référence)
class ContratInvalide(Stop):
    pass


class DecisionPerimee(ContratInvalide):
    """Décision ou commande devenue périmée (main, pause, génération, page, cible) : jetée, jamais exécutée."""


def message(code):
    return MESSAGES.get(str(code).split(':')[0], 'Décision écartée : nouvelle observation.')


def schema_action():
    """Un schéma par action (champs obligatoires selon l'action, aucune propriété supplémentaire) ; revalidé côté service."""
    branches = []
    for action, (required, optional) in CHAMPS.items():
        properties = {'action': {'const': action}, 'raison': {'type': 'string', 'minLength': 1, 'maxLength': 500}}
        for name in sorted(required | optional):
            properties[name] = ({'type': 'boolean'} if name == 'valider' else
                                {'type': 'string', 'enum': ['haut', 'bas']} if name == 'direction' else
                                {'type': 'string', 'minLength': 1, 'maxLength': 4000 if name == 'resume' else 2000})
        branches.append({'type': 'object', 'properties': properties, 'required': ['action', 'raison', *sorted(required)],
                         'additionalProperties': False})
    return {'oneOf': branches}


def _objet_unique(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ContratInvalide('cle_json_dupliquee')
        out[k] = v
    return out


def _constante(_):
    raise ContratInvalide('constante_json_invalide')


def decision(raw):
    """Parseur strict : un seul objet JSON, sans texte autour, sans clé dupliquée ni NaN, champs propres à l'action et rien d'autre."""
    if not isinstance(raw, str) or len(raw) > 20000:
        raise ContratInvalide('decision_trop_longue_ou_absente')
    try:
        d = json.loads(raw, object_pairs_hook=_objet_unique, parse_constant=_constante)
    except json.JSONDecodeError:
        raise ContratInvalide('decision_json_invalide') from None
    if not isinstance(d, dict) or not isinstance(d.get('action'), str) or d['action'] not in CHAMPS:
        raise ContratInvalide('action_inconnue')
    required, optional = CHAMPS[d['action']]
    if not {'action', 'raison', *required} <= d.keys() or d.keys() - {'action', 'raison', *required, *optional}:
        raise ContratInvalide('champs_action_invalides')
    for key, value in d.items():
        if key == 'valider':
            if type(value) is not bool:
                raise ContratInvalide('validation_non_booleenne')
        elif not isinstance(value, str) or not value.strip() or len(value) > (4000 if key == 'resume' else 500 if key == 'raison' else 2000):
            raise ContratInvalide('valeur_action_invalide')
    if 'ref' in d and not re.fullmatch(r'e[1-9][0-9]{0,7}', d['ref']):
        raise ContratInvalide('reference_action_invalide')
    if 'direction' in d and d['direction'] not in ('haut', 'bas'):
        raise ContratInvalide('direction_action_invalide')
    return d


def valider(d):
    """Même contrôle pour une décision déjà décodée (toute source : modèle, essai, reprise)."""
    if not isinstance(d, dict):
        raise ContratInvalide('action_inconnue')
    return decision(json.dumps(d, ensure_ascii=False))


def options_profil(profil):
    """Bornes propres au navigateur ; les profils juridiques (``profil5634.BOUNDS``) ne sont pas élargis."""
    keys = {'think', 'num_ctx', 'num_predict', 'temperature', 'top_p', 'top_k', 'min_p', 'presence_penalty', 'repeat_penalty'}
    if not isinstance(profil, dict) or not keys <= profil.keys():
        raise ContratInvalide('profil_incomplet')
    p = {k: profil[k] for k in keys}
    if type(p['think']) is not bool:
        raise ContratInvalide('profil_thinking_invalide')
    for k, lo, hi in (('num_ctx', 2048, 32768), ('num_predict', 128, 4096), ('top_k', 1, 200)):
        if type(p[k]) is not int or not lo <= p[k] <= hi:
            raise ContratInvalide('profil_entier_invalide')
    for k, lo, hi in (('temperature', 0, 1.2), ('top_p', 0.01, 1), ('min_p', 0, 1), ('presence_penalty', 0, 2), ('repeat_penalty', 0.8, 1.5)):
        n = p[k]
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or not lo <= n <= hi:
            raise ContratInvalide('profil_parametre_invalide')
    return {'think': p.pop('think'), 'options': p}


def verifier_cible(cfg, *, model_attendu=MODEL_CIBLE, digest_attendu, digest_observe):
    """Garde juste avant l'émission (pas un résolveur) : fournisseur local, modèle exact, ni hôte distant, digest inchangé."""
    if cfg.get('provider_type') != 'ollama' or cfg.get('provider_id') != 'ollama':
        raise ContratInvalide('navigateur_local_fournisseur_incompatible')
    if cfg.get('model') != model_attendu:
        raise ContratInvalide('navigateur_modele_modifie')
    if cfg.get('remote_host') or cfg.get('remote_model') or _nuage(model_attendu):
        raise ContratInvalide('navigateur_modele_distant')
    if not digest_attendu or not digest_observe or digest_attendu != digest_observe:
        raise ContratInvalide('navigateur_version_modele_a_requalifier')
    return {'provider': 'ollama', 'model': model_attendu, 'digest': digest_observe}


def _nuage(model):
    """Un modèle « :cloud » (ou « -cloud ») d'Ollama est traité à distance, quel que soit son nom local."""
    tag = str(model or '').rsplit(':', 1)[-1].lower()
    return tag == 'cloud' or tag.endswith('-cloud')


@dataclass(frozen=True)
class Observation:
    onglet: str
    revision: str
    epoch: int
    # ref -> (rôle, libellé, backend id) ; jamais la valeur d'un champ ni un secret.
    cibles: dict


def verifier_avant_action(d, observee, courante, *, epoch, main_humaine, pause, stop):
    """Garde pure de fraîcheur ; sérialisée par la barrière G13 dans le pilote."""
    if pause or stop or main_humaine:
        raise DecisionPerimee('execution_suspendue')
    if (observee.epoch != epoch or courante.epoch != epoch or not observee.onglet or observee.onglet != courante.onglet
            or not observee.revision or observee.revision != courante.revision):
        raise DecisionPerimee('observation_perimee')
    if 'ref' in d:
        ref = d['ref']
        if ref not in observee.cibles or observee.cibles.get(ref) != courante.cibles.get(ref):
            raise DecisionPerimee('cible_disparue_ou_modifiee')
    return True     # aucune valeur « Autoriser » venant du modèle : les effets restent classés par nav5673.classify


class Progression:
    """Absence de progrès et cycles ; seules des empreintes (jamais le texte saisi) sont comparées."""

    def __init__(self, *, limite=3):
        if type(limite) is not int or not 2 <= limite <= 5:
            raise ContratInvalide('limite_blocage_invalide')
        self.limite = limite
        self.stagnation = 0
        self.etats = deque(maxlen=6)

    def constater(self, avant, apres, *, preuve_accomplissement=False):
        if not all(isinstance(x, str) and x for x in (avant, apres)):
            raise ContratInvalide('observation_progression_incomplete')
        if preuve_accomplissement:
            self.stagnation = 0
            self.etats.clear()
            return 'progression_prouvee'
        self.stagnation = self.stagnation + 1 if avant == apres else 0
        self.etats.append(apres)
        if self.stagnation >= self.limite:
            return 'demander_la_main'
        states = list(self.etats)
        if len(states) == 6 and states[:2] == states[2:4] == states[4:6]:
            return 'demander_la_main'
        return 'continuer'


def empreinte_progression(element):
    """Empreinte d'un état utile en mémoire ; seule elle peut rejoindre un journal technique."""
    return hashlib.sha256(json.dumps(element, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def observer(tab, url, title, epoch):
    """Instantané de fraîcheur : onglet, révision utile (adresse sans paramètres + titre), génération et cibles (rôle, libellé, nœud)."""
    from .nav5673 import history_url
    refs = getattr(tab, 'refs', None)
    cibles = refs.cibles() if refs is not None and hasattr(refs, 'cibles') else {}
    revision = hashlib.sha256(('%s\n%s' % (history_url(url), title)).encode('utf-8')).hexdigest()[:24]
    return Observation(str(getattr(tab, 'target', '') or 'onglet'), revision, int(epoch), cibles)


def etat_utile(obs, plan, seance):
    """Empreinte de progression : page, plan, champs déjà renseignés (repères seulement) et reçus ; pas d'horloge ni de valeur saisie."""
    return empreinte_progression({'r': obs.revision, 'p': hashlib.sha256(str(plan or '').encode('utf-8')).hexdigest(),
                                  's': sorted(getattr(seance, 'saisies', ()) or ()), 'f': dict(getattr(seance, 'recus', {}) or {})})


# ======================================================================================== G13 : barrière de séance
def pause_globale(desk):
    """Motif de pause globale ('' sinon). Contrôle illisible : la pause est présumée (jamais un accès élargi)."""
    if desk is None:
        return ''
    try:
        from .arret5643 import reason_to_stop
        return reason_to_stop((getattr(desk, 'c', None) or {}).get('state_dir'))
    except Stop as ex:
        return str(ex).split(':')[0] or 'controle_pause_indisponible'
    except (sqlite3.Error, OSError):
        return 'controle_pause_indisponible'


def _verrou(s):
    v = getattr(s, 'verrou', None)
    if v is None:
        raise Stop('seance_sans_barriere')
    return v


def _controle(s, epoch, code_pause):
    if getattr(s, 'stop_flag', False):
        raise Stop('arrete_par_l_avocat')
    if getattr(s, 'hold', False):
        raise DecisionPerimee('main_a_l_avocat')
    if code_pause:
        raise DecisionPerimee(code_pause)
    if int(epoch) != int(getattr(s, 'epoch', 0)):
        raise DecisionPerimee('generation_perimee')


def controler(s, epoch, desk=None):
    """Contrôle sans commande (après une inférence, avant de décider) ; lève ``DecisionPerimee`` ou ``Stop``."""
    code = pause_globale(desk)
    with _verrou(s):
        _controle(s, epoch, code)


@contextmanager
def barriere(s, epoch, desk=None):
    """Toute observation et toute action passent par ici : contrôle sous verrou court, commande comptée « en vol » le temps de l'envoi."""
    code = pause_globale(desk)
    verrou = _verrou(s)
    with verrou:
        _controle(s, epoch, code)
        s.en_vol += 1
    try:
        yield
    finally:
        with verrou:
            s.en_vol -= 1
            if s.hold and s.en_vol <= 0:
                s.main_acquittee = True
            verrou.notify_all()


def puis_je(s, epoch, desk=None):
    """Fonction publique pour les lots de portails, de PDF et de parcours : « puis-je observer ou agir maintenant, avec cette génération ? »
    Rend ``{'ok', 'code', 'epoch'}`` sans rien envoyer ; l'action elle-même doit ensuite passer par ``barriere``."""
    if s is None:
        return {'ok': False, 'code': 'aucune_seance', 'epoch': None}
    try:
        controler(s, epoch, desk)
    except Stop as ex:
        return {'ok': False, 'code': str(ex).split(':')[0], 'epoch': getattr(s, 'epoch', None)}
    return {'ok': True, 'code': '', 'epoch': s.epoch}


def changer_main(s, prendre):
    """Prise ou reprise de main : nouvelle génération sous verrou ; toute décision en vol est périmée."""
    verrou = _verrou(s)
    with verrou:
        s.epoch += 1
        s.hold = bool(prendre)
        s.main_acquittee = False
        if not prendre:
            s.reprise = True
        verrou.notify_all()
    s.event.set()


def nouvelle_generation(s):
    with _verrou(s):
        s.epoch += 1
        _verrou(s).notify_all()
    return s.epoch


def attendre_acquittement(s, secondes):
    """La main n'est rendue à l'avocat qu'une fois les commandes de pilotage déjà engagées terminées."""
    import time
    verrou = _verrou(s)
    limite = time.monotonic() + max(0.0, float(secondes))
    with verrou:
        while s.hold and s.en_vol > 0 and time.monotonic() < limite:
            verrou.wait(0.05)
        if s.hold and s.en_vol <= 0:
            s.main_acquittee = True
        return bool(s.hold and s.main_acquittee)


def acquitter_si_libre(s):
    with _verrou(s):
        if s.hold and s.en_vol <= 0:
            s.main_acquittee = True
        return s.main_acquittee


class Pilote:
    """Onglet vu à travers la barrière : chaque méthode (observation ou action) est contrôlée avec la génération de la décision."""

    def __init__(self, tab, seance, epoch, desk=None):
        object.__setattr__(self, '_tab', tab)
        object.__setattr__(self, '_s', seance)
        object.__setattr__(self, '_epoch', int(epoch))
        object.__setattr__(self, '_desk', desk)

    def __getattr__(self, name):
        value = getattr(self._tab, name)
        if not callable(value):
            return value

        def appel(*args, **kwargs):
            with barriere(self._s, self._epoch, self._desk):
                return value(*args, **kwargs)
        return appel


# ======================================================================================== G24 : route, profil, verrou
def _registre(config):
    from .ai_gateway import provider_registry
    return provider_registry(config)


def _politique(config):
    from . import politique5682
    return politique5682.current(config)


def choix(config):
    """Fournisseur et modèle du navigateur, avec leur origine. Sans affectation propre : celle de l'assistant (ou de la politique) reprise
    explicitement et marquée « héritée » ; aucune écriture ici (lecture seule)."""
    routes = config.get('model_routing') or {}
    route = routes.get(PURPOSE) if isinstance(routes.get(PURPOSE), dict) else None
    if route and route.get('provider') and route.get('model'):
        try:
            revision = max(0, int(route.get('revision') or 0))
        except (TypeError, ValueError):
            revision = 0
        return {'provider': str(route['provider']), 'model': str(route['model']), 'revision': revision,
                'origine': 'explicite' if route.get('origine') == 'explicite' else 'reglage_global'}
    rule = _politique(config)
    registry = _registre(config)
    if rule and rule['mode'] not in ('local', 'routes'):
        from . import politique5682
        found = politique5682.candidates(config, rule)
        if not found:
            raise Stop('fournisseur_ia_inactif')
        pid = found[0]
        model = str(registry[pid].get('model') or '')
        return {'provider': pid, 'model': model, 'revision': 0, 'origine': 'herite_politique'}
    from .hybrid400 import policy as hybrid_policy
    assistant = routes.get('assistant') if isinstance(routes.get('assistant'), dict) else {}
    local_model = str((config.get('ollama') or {}).get('model') or '')
    if hybrid_policy(config)['mode'] in ('local', 'hybrid'):
        model = str(assistant.get('model') or '') if str(assistant.get('provider') or 'ollama') == 'ollama' else ''
        return {'provider': 'ollama', 'model': model or str(routes.get('complex_model') or '') or local_model, 'revision': 0, 'origine': 'herite_assistant'}
    provider = str(assistant.get('provider') or routes.get('complex_provider') or 'ollama')
    model = str(assistant.get('model') or routes.get('complex_model') or '') or (local_model if provider == 'ollama' else str((registry.get(provider) or {}).get('model') or ''))
    return {'provider': provider, 'model': model, 'revision': 0, 'origine': 'herite_assistant'}


def profil_pour(model):
    return PROFIL_QWEN if model == MODEL_CIBLE else PROFIL_GENERIQUE


def config_navigateur(config):
    """Configuration épinglée du navigateur (appelée par ``model.routed_config`` pour ``browser_navigation``).

    Intersection du choix et de la politique : un fournisseur interdit est refusé avec une explication (rien n'est envoyé, la politique
    n'est pas modifiée). Le contrat ``browser_pin109`` vient des réglages authentifiés, jamais de la page ni du modèle."""
    from .model import local_seul_actif, pseudo_sources
    c = choix(config)
    registry = _registre(config)
    provider = registry.get(c['provider'])
    if not provider or not provider.get('enabled', True):
        raise Stop('fournisseur_ia_inactif')
    ptype = provider.get('type', 'ollama')
    if local_seul_actif() and ptype != 'ollama':
        raise Stop('modele_local_requis')
    rule = _politique(config)
    from . import politique5682
    if rule and not politique5682.is_allowed(rule, ptype, c['provider']):
        raise Stop('navigateur_modele_interdit_par_politique')
    if ptype != 'ollama':
        if not provider.get('external_data_allowed', False):
            raise Stop('fournisseur_externe_non_autorise')
        if rule and not rule['consentement']:
            raise Stop('politique_externe_non_autorisee')
    model = c['model']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}', model or ''):
        raise Stop('modele_fournisseur_absent')
    if ptype == 'ollama' and _nuage(model):
        raise Stop('navigateur_modele_distant')
    profil = profil_pour(model)
    valide = options_profil(profil)
    base = dict(provider)
    for cle in REPLIS:
        base.pop(cle, None)
    base.update({'model': model, 'provider_id': c['provider'], 'provider_type': ptype, 'purpose': PURPOSE,
                 'temperature': valide['options']['temperature'], 'sampling': {}, 'profile': profil['id'], 'profile_version': PROFIL_VERSION,
                 'subprofile': '', 'external_policy_config': config, 'pseudo': pseudo_sources(config), 'skill_instructions': '',
                 'navigateur109': {'profil': profil['id'], 'version': PROFIL_VERSION, 'think': valide['think'], 'options': valide['options']}})
    if ptype == 'ollama':
        base.update({'context_mode': 'fixe', 'num_ctx': valide['options']['num_ctx'], 'disable_thinking': not valide['think']})
    else:
        from .hybrid400 import policy as hybrid_policy
        base['max_context_chars'] = max(8000, min(int(base.get('max_context_chars') or 65000), int(hybrid_policy(config).get('max_external_characters') or 120000) - 15000))
    if config.get('state_dir'):
        base['state_dir'] = config['state_dir']
    base['browser_pin109'] = {'provider': c['provider'], 'provider_type': ptype, 'model': model, 'digest': '', 'route_revision': c['revision'],
                              'profile': profil['id'], 'origine': c['origine']}
    return base


def garde_complet(cfg):
    """``Model.complete`` : le verrou interdit secours, hybride, routeur automatique et repli budgétaire, et tout changement de modèle."""
    pin = cfg.get('browser_pin109') or {}
    if any(cfg.get(k) for k in REPLIS):
        raise Stop('navigateur_repli_interdit')
    if (cfg.get('model') != pin.get('model') or str(cfg.get('provider_type') or cfg.get('type') or 'ollama') != pin.get('provider_type')
            or str(cfg.get('provider_id') or cfg.get('id') or 'ollama') != pin.get('provider')):
        raise Stop('navigateur_modele_modifie')


def options_transport(cfg):
    """Options Ollama du profil validé (``presence_penalty`` compris) ; ``num_ctx`` et ``num_predict`` sont fixés par le transport commun."""
    nav = cfg.get('navigateur109') or {}
    valide = options_profil({**{k: v for k, v in (nav.get('options') or {}).items()}, 'think': nav.get('think', False)})
    return {k: v for k, v in valide['options'].items() if k not in ('num_ctx', 'num_predict')}, []


def verifier_requete(m, req):
    """Contrôle de la requête Ollama FINALEMENT émise (après toutes les étapes du transport commun)."""
    cfg = m.cfg
    pin = cfg.get('browser_pin109') or {}
    garde_complet(cfg)
    if m.provider_type != 'ollama' or pin.get('provider_type') != 'ollama':
        raise Stop('navigateur_modele_modifie')
    if req.get('model') != pin.get('model'):
        raise Stop('navigateur_modele_modifie')
    if _nuage(req.get('model')):
        raise Stop('navigateur_modele_distant')
    if pin.get('digest') and cfg.get('digest_observe109') and pin['digest'] != cfg['digest_observe109']:
        raise Stop('navigateur_version_modele_a_requalifier')
    attendues, _ = options_transport(cfg)
    if any((req.get('options') or {}).get(k) != v for k, v in attendues.items()):
        raise Stop('navigateur_profil_non_transmis')
    caps = getattr(m, 'capabilities', None)
    if not (cfg.get('navigateur109') or {}).get('think') and (caps is None or 'thinking' in caps) and req.get('think') is not False:
        raise Stop('navigateur_profil_non_transmis')
    if 'format' not in req:
        raise Stop('navigateur_profil_non_transmis')
    return True


def verifier_externe(m):
    """Fournisseur externe choisi explicitement pour le navigateur (si la politique le permet) : même verrou fournisseur/modèle."""
    garde_complet(m.cfg)
    if m.provider_type != (m.cfg.get('browser_pin109') or {}).get('provider_type'):
        raise Stop('navigateur_modele_modifie')
    return True


def observer_digest(cfg, http=None):
    """Digest des poids relevé dans le catalogue local d'Ollama (``/api/tags``) : jamais recopié depuis la valeur attendue."""
    if http is None:
        from . import model as _model
        http = _model.HTTP(cfg['url'], local_only=True, timeout=10, local_hosts=cfg.get('local_hosts', ()))
    tags = http.json('GET', '/api/tags')
    name = str(cfg.get('model') or '')
    for row in (tags.get('models') or []) if isinstance(tags, dict) else []:
        if isinstance(row, dict) and str(row.get('name') or row.get('model') or '') in (name, name + ':latest'):
            return str(row.get('digest') or '')
    raise Stop('modele_ollama_absent')


def _lire_reglage(config, key):
    path = Path(str(config.get('state_dir') or '')) / 'desk.sqlite3'
    if not config.get('state_dir') or not path.is_file():
        return None
    try:
        db = sqlite3.connect(path, timeout=5)
        try:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        finally:
            db.close()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def qualification(config, pin, digest=''):
    """État de qualification du couple (modèle, digest, profil) sur ce poste, d'après le reçu de recette enregistré."""
    if not pin or pin.get('provider_type') != 'ollama' or pin.get('model') != MODEL_CIBLE:
        return 'autre_modele'
    recu = _lire_reglage(config, QUALIF_KEY)
    if not isinstance(recu, dict) or recu.get('modele') != MODEL_CIBLE:
        return 'a_tester'
    if recu.get('profil') != pin.get('profile') or (digest and recu.get('digest') != digest):
        return 'a_requalifier'
    try:
        ok = int(recu.get('reussites') or 0) >= int(recu.get('executions') or 0) >= 30
    except (TypeError, ValueError):
        ok = False
    return 'teste_sur_ce_poste' if ok and digest and recu.get('digest') == digest else 'a_tester'


def preparer_seance(s, cfg, desk=None):
    """Avant l'appel au modèle : digest relevé à la première étape et à chaque nouvelle génération ; un changement en cours de séance
    invalide la qualification (arrêt expliqué). Le modèle réellement actif est affiché dans la fenêtre du navigateur."""
    pin = cfg.get('browser_pin109') or {}
    digest = ''
    if pin.get('provider_type') == 'ollama':
        if getattr(s, '_digest_epoch', None) != getattr(s, 'epoch', 0) or not getattr(s, 'digest', ''):
            observe = observer_digest(cfg)
            if getattr(s, 'digest', '') and observe != s.digest:
                raise Stop('navigateur_version_modele_a_requalifier')
            s.digest = observe
            s._digest_epoch = getattr(s, 'epoch', 0)
        digest = s.digest
        verifier_cible(cfg, model_attendu=pin['model'], digest_attendu=s.digest, digest_observe=digest)
        cfg['browser_pin109'] = {**pin, 'digest': digest}
        cfg['digest_observe109'] = digest
    etat = qualification(cfg.get('external_policy_config') or {}, pin, digest)
    s.modele = {'fournisseur': pin.get('provider', ''), 'modele': pin.get('model', ''), 'profil': pin.get('profile', ''),
                'digest': (digest or '')[:19], 'origine': ORIGINES.get(pin.get('origine', ''), ''), 'qualification': QUALIFICATIONS[etat],
                'qualification_code': etat}
    return cfg


# ======================================================================================== réglage « Modèle du navigateur »
def marque_route(desk):
    """``ai_gateway.save_route`` pour ``browser_navigation`` : choix explicite, révision incrémentée (la révision entre dans le verrou)."""
    precedent = desk.settings(ROUTE_KEY, {}) or {}
    try:
        revision = max(0, int(precedent.get('revision') or 0)) if isinstance(precedent, dict) else 0
    except (TypeError, ValueError):
        revision = 0
    return {'origine': 'explicite', 'revision': revision + 1, 'le': _now()}


def etat_reglage(desk):
    """Lecture seule (aucune écriture, aucun appel au modèle) : choix, origine, profil, qualification, refus de politique éventuel."""
    out = {'fonction': PURPOSE, 'cible': MODEL_CIBLE, 'profil_qwen': {k: v for k, v in PROFIL_QWEN.items() if k != 'model'}}
    try:
        cfg = config_navigateur(desk.c)
    except Stop as ex:
        code = str(ex).split(':')[0]
        try:
            c = choix(desk.c)
        except Stop:
            c = {}
        out.update({'erreur': code, 'message': message(code) if code in MESSAGES else 'Modèle du navigateur indisponible (%s).' % code,
                    'fournisseur': c.get('provider', ''), 'modele': c.get('model', ''), 'origine': ORIGINES.get(c.get('origine', ''), '')})
        return out
    pin = cfg['browser_pin109']
    recu = _lire_reglage(desk.c, QUALIF_KEY)
    etat = qualification(desk.c, pin, str((recu or {}).get('digest') or '') if isinstance(recu, dict) else '')
    if etat == 'teste_sur_ce_poste':
        etat = 'a_tester'      # sans relevé du digest courant (aucun appel ici), la qualification reste « à confirmer » à la séance
        out['note'] = 'Le digest des poids est relu au début de chaque séance ; la qualification s’affiche alors dans la fenêtre du navigateur.'
    out.update({'fournisseur': pin['provider'], 'type': pin['provider_type'], 'modele': pin['model'], 'profil': pin['profile'],
                'revision': pin['route_revision'], 'origine': ORIGINES.get(pin['origine'], pin['origine']), 'origine_code': pin['origine'],
                'qualification': QUALIFICATIONS[etat], 'qualification_code': etat, 'recette': recu if isinstance(recu, dict) else None})
    return out


def enregistrer_modele(desk, acteur, provider_id, model, version=None):
    """« Modèle du navigateur » : même route que le formulaire « Modèle utilisé par fonction » (aucune seconde clé). Le modèle est testé
    (présent, local ou autorisé) par ``save_route`` ; 409 si le réglage a changé depuis l'ouverture (``version``)."""
    from .ai_gateway import save_route
    actuel = desk.settings(ROUTE_KEY, {}) or {}
    revision = int(actuel.get('revision') or 0) if isinstance(actuel, dict) else 0
    if version is not None and str(version) != str(revision):
        raise Stop('version_perimee')
    out = save_route(desk, PURPOSE, provider_id, model)
    config_navigateur(desk.c)       # politique du cabinet : un choix interdit est refusé avec son motif (le réglage reste visible)
    desk.audit('navigateur56109_modele', {'fournisseur': out['provider'], 'modele': out['model'], 'par': str(acteur)[:120]})
    return {'ok': True, 'reglage': etat_reglage(desk), 'message': 'Modèle du navigateur enregistré : %s (%s). Les autres fonctions gardent leur modèle.' % (
        out['model'], out['provider'])}


def noter_qualification(desk, recu):
    """Reçu de recette (script ``recette-navigateur-qwen56109.py``, lancé par le cabinet) : système, matériel, versions, modèle exact,
    digest, profil, réussites, interventions, mesures. Aucune valeur de formulaire, aucun secret ; un changement de modèle, de digest ou
    de profil invalide la qualification correspondante."""
    if not isinstance(recu, dict):
        raise Stop('recu_qualification_invalide')
    permis = ('modele', 'digest', 'profil', 'systeme', 'materiel', 'version_produit', 'version_ollama', 'quantification', 'executions', 'reussites',
              'interventions', 'ram_max_mo', 'vram_max_mo', 'latence_mediane_ms', 'latence_p95_ms', 'profils_sites', 'le')
    propre = {k: recu[k] for k in permis if k in recu}
    if propre.get('modele') != MODEL_CIBLE or not str(propre.get('digest') or '').startswith('sha256:'):
        raise Stop('recu_qualification_invalide')
    desk.setting(QUALIF_KEY, propre)
    desk.audit('navigateur56109_qualification', {k: propre.get(k) for k in ('modele', 'profil', 'systeme', 'executions', 'reussites')})
    return propre


# ======================================================================================== routes m56109/navigateur/
PREFIXE = 'm56109/navigateur/'
LECTURES = ('etat',)
ECRITURES = ('modele', 'main')
ROLES_TRAVAIL = ('administrateur', 'avocat', 'assistant')
MAX_POST = 20_000
STATUTS = {'introuvable': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden',
           'version_perimee': '409 Conflict', 'requete_trop_longue': '413 Payload Too Large', 'navigateur_reserve_au_poste': '409 Conflict',
           'navigateur_modele_interdit_par_politique': '409 Conflict', 'controle_acces_indisponible': '503 Service Unavailable'}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    if method != 'POST':
        return False
    if reste == 'modele':
        return role == 'administrateur'
    return reste == 'main' and role in ROLES_TRAVAIL


def _json(data, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(data, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    from . import navigateur5673 as nv
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if reste not in LECTURES + ECRITURES:
            raise Stop('introuvable')
        if not allowed(role, method, name):
            raise Stop('role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable')
        if method in ('GET', 'HEAD'):
            s = nv.manager(desk.c).session if nv.available(auth) else None
            visible = s is not None and (s.owner == owner or role == 'administrateur')
            return _json({'reglage': etat_reglage(desk), 'poste': bool(nv.available(auth)),
                          'seance': s.public() if visible else None})
        try:
            longueur = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            longueur = MAX_POST + 1
        if longueur > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'modele':
            return _json(enregistrer_modele(desk, owner, str(d.get('fournisseur') or 'ollama'), str(d.get('modele') or ''), d.get('version')))
        if not nv.available(auth):
            raise Stop('navigateur_reserve_au_poste')
        mgr = nv.manager(desk.c)
        out = mgr.take(str(d.get('id') or ''), str(d.get('prendre') or '') in ('1', 'oui', 'true'), acteur=(owner, role), desk=desk)
        texte = ('La main est à vous : plus aucune commande de l’agent n’est en cours.' if out.get('main_acquittee') else
                 'Transfert de la main en cours : attendez la confirmation avant toute saisie.') if out.get('main') else \
            'L’agent reprend : nouvelle observation de la page, identité et droits relus.'
        return _json({'ok': True, 'seance': out, 'message': texte})
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('aucune_seance', 'mission_absente'):
            code = 'introuvable'
        texte = message(code) if code in MESSAGES else {
            'introuvable': 'Séance ou réglage introuvable.', 'role_insuffisant': 'Votre rôle ne permet pas cette action.',
            'version_perimee': 'Le réglage a changé entre-temps : rechargez la page. Rien n’a été modifié.',
            'navigateur_reserve_au_poste': 'Disponible dans l’application du poste (Windows ou Ubuntu) seulement.',
            'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
            'requete_trop_longue': 'Demande trop longue.'}.get(code, 'Demande refusée (%s).' % code)
        return _json({'error': code, 'message': texte}, STATUTS.get(code, '422 Unprocessable Entity'))


# ======================================================================================== Paramètres › IA
def section_html(desk, prefix='', role='administrateur'):
    """Bloc « Modèle du navigateur » (Paramètres › IA) : état réel, sans appel au modèle ni écriture pendant l'affichage."""
    from html import escape as e
    r = etat_reglage(desk)
    lignes = [('Fournisseur et modèle', '%s · %s' % (r.get('fournisseur') or '—', r.get('modele') or '—')),
              ('Origine du choix', r.get('origine') or '—'), ('Profil', r.get('profil') or '—'),
              ('Qualification', r.get('qualification') or ('Indisponible : ' + r.get('message', '')))]
    corps = ''.join('<tr><th scope="row">%s</th><td>%s</td></tr>' % (e(a), e(str(b))) for a, b in lignes)
    alerte = '<p class="ax-alert" role="status">%s</p>' % e(r['message']) if r.get('erreur') else ''
    return ('<section class="ax-card n56109" id="modele-navigateur"><h2>Modèle du navigateur</h2>'
            '<p class="ax-muted">Modèle utilisé pour décider du prochain geste dans le navigateur supervisé, distinct de l’assistant : '
            'il se règle avec « Modèle utilisé par fonction » (ligne « Navigateur supervisé »). Aucun repli silencieux vers un autre modèle '
            'ou un fournisseur externe ; aucun poids n’est téléchargé.</p>%s<table class="n56109-table"><tbody>%s</tbody></table>'
            '<p class="ax-muted">La compatibilité réelle avec qwen3.5:9b n’est acquise qu’après la recette du cabinet sur Windows 11 et '
            'Ubuntu 24.04 (trente parcours, script fourni).</p></section>') % (alerte, corps)

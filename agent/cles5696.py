"""5.6.96 (lot mistral) : clés d'API testées avant publication, pour les fournisseurs qui exposent un catalogue (Mistral, Claude).

Même méthode pour les deux (le défaut de la 5.6.95 touchait les deux : la clé saisie était enregistrée AVANT la lecture du catalogue,
une clé erronée remplaçait donc la clé en service) :

1. test de la clé candidate par la lecture du catalogue du fournisseur, sans effet local ;
2. écriture d'un NOUVEAU fichier du coffre (``ai_gateway.save_secret_version``), relecture, second test sur les octets enregistrés ;
3. publication en une transaction : référence de clé, catalogue et révision ensemble ; 409 si la révision lue à l'ouverture a changé.
   En cas d'échec, le fichier candidat est effacé et la connexion en service reste intacte.

Ce qui est propre à Mistral (incidents en cours de travail, carte, test de génération) reste dans ``mistral5696``.

Documentation officielle relue le 9 novembre 2026 :
- Mistral : ``GET https://api.mistral.ai/v1/models``, ``Authorization: Bearer``, ``capabilities.completion_chat`` par modèle.
- Claude : ``GET https://api.anthropic.com/v1/models`` (``limit`` de 1 à 1000), en-têtes ``x-api-key`` et ``anthropic-version`` ;
  réponse ``{"data": [{"type": "model", "id", "display_name", "lifecycle", "max_input_tokens", ...}], "has_more", ...}``. Les modèles
  « retired » ne sont plus appelables : ils ne sont jamais retenus.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

from .common import Stop

CLE_RE = re.compile(r'[!-~]{8,4096}')
QUOTA_RE = re.compile(r'quota|insufficient|credit|billing|spending|payment', re.I)
RESEAU = frozenset(('delai_http_depasse', 'connexion_http_indisponible', 'fournisseur_ia_injoignable', 'generation_ia_delai_depasse'))
PROFILS = {
    'mistral': {'url': 'https://api.mistral.ai/v1', 'chemin': '/models', 'nom': 'Mistral', 'controle': 'mistral5696:controle:',
                'supprimer': ('mistral5696:incident:',), 'audit': 'mistral_cle_enregistree',
                'codes': {'limite': 'mistral_limite_a_preciser', 'quota': 'mistral_quota_atteint', 'indisponible': 'mistral_catalogue_indisponible',
                          'invalide': 'mistral_catalogue_invalide'}},
    'anthropic': {'url': 'https://api.anthropic.com/v1', 'chemin': '/models?limit=1000', 'nom': 'Claude', 'controle': 'cles5696:controle:',
                  'supprimer': (), 'audit': 'cle_api_enregistree',
                  'codes': {'limite': 'fournisseur_limite_a_preciser', 'quota': 'fournisseur_quota_atteint', 'indisponible': 'catalogue_fournisseur_indisponible',
                            'invalide': 'catalogue_fournisseur_invalide'}},
}
TESTABLES = frozenset(PROFILS)


def _now():
    return datetime.now(timezone.utc)


def _profil(kind):
    if kind not in PROFILS:
        raise Stop('type_fournisseur_invalide')
    return PROFILS[kind]


# ------------------------------------------------------------------------------------------------ classement des retours
def classer(error):
    """(nature, Retry-After en secondes) d'une erreur du fournisseur ; (None, None) si ce n'est pas un incident de connexion."""
    code = str(error).split(':')[0]
    detail = dict(getattr(error, 'detail', None) or {})
    retry = detail.get('retry_after')
    retry = retry if isinstance(retry, int) and not isinstance(retry, bool) and retry >= 0 else None
    signal = detail.get('provider_error') if isinstance(detail.get('provider_error'), dict) else {}
    signal = ' '.join(str(v) for v in signal.values())
    if code in ('http_401', 'authentification_fournisseur_refusee'):
        return 'cle_a_verifier', None
    if code in ('http_403', 'acces_fournisseur_refuse'):
        return 'acces_refuse', None
    if code in ('http_402', 'credit_fournisseur_epuise'):
        return 'quota_atteint', None
    if code in ('http_429', 'quota_fournisseur_depasse'):
        # Un 429 seul ne prouve pas l'épuisement du quota : seul un code explicite du fournisseur le confirme.
        return ('quota_atteint', None) if QUOTA_RE.search(signal) else ('limite_temporaire', retry)
    if re.fullmatch(r'http_5\d\d', code) or code in RESEAU:
        return 'indisponible', retry
    return None, None


def code_test(kind, error):
    """Code public d'un échec de test : statut seulement, jamais le texte du fournisseur ni la clé."""
    codes = _profil(kind)['codes']
    code = str(error).split(':')[0]
    nature, _ = classer(error)
    if nature == 'cle_a_verifier':
        return 'authentification_fournisseur_refusee'
    if nature == 'acces_refuse':
        return 'acces_fournisseur_refuse'
    if nature == 'limite_temporaire':
        return codes['limite']
    if nature == 'quota_atteint':
        return codes['quota']
    if code in RESEAU:
        return 'fournisseur_ia_injoignable'
    if code in ('json_http_invalide', 'reponse_trop_volumineuse'):
        return codes['invalide']
    return codes['indisponible']


# ------------------------------------------------------------------------------------------------ transport
def entetes(kind, cle):
    """En-têtes d'authentification documentés par chaque fournisseur."""
    if kind == 'anthropic':
        from .model import ANTHROPIC_VERSION
        return {'x-api-key': cle, 'anthropic-version': ANTHROPIC_VERSION, 'Accept': 'application/json'}
    return {'Authorization': 'Bearer ' + cle, 'Accept': 'application/json'}


def _http(base, kind='mistral'):
    """Transport réel : classe ``HTTP`` commune (HTTPS, sans redirection ni proxy implicite). Jamais appelé par les tests."""
    def appel(methode, chemin, cle, corps=None):
        from .common import HTTP
        http = HTTP(base, timeout=8)
        http.headers.update(entetes(kind, cle))
        return http.json(methode, chemin, corps)
    return appel


def cle_valide(value):
    value = str(value or '').strip()
    if not CLE_RE.fullmatch(value):
        raise Stop('secret_fournisseur_invalide')
    return value


def lire_catalogue(kind, cle, base, transport=None):
    """Document brut du catalogue ; l'erreur d'origine (statut, Retry-After) est conservée pour le classement."""
    profil = _profil(kind)
    doc = (transport or _http(base, kind))('GET', profil['chemin'], cle)
    if not isinstance(doc, dict) or not isinstance(doc.get('data'), list):
        raise Stop(profil['codes']['invalide'])
    return doc['data']


def tester_catalogue(kind, cle, base=None, transport=None):
    """Lignes du catalogue de la clé ; aucun effet local. Erreurs : codes publics seulement."""
    cle = cle_valide(cle)
    try:
        return lire_catalogue(kind, cle, base or PROFILS[kind]['url'], transport)
    except Stop as error:
        if str(error) == _profil(kind)['codes']['invalide']:
            raise
        raise Stop(code_test(kind, error)) from None


def catalogue_conversation(kind, lignes):
    """(modèles de conversation utilisables, modèles à capacité inconnue).

    Mistral : ``capabilities.completion_chat: true`` (preuve « liste du fournisseur ») ; à défaut de capacités, un modèle documenté par la
    référence datée (preuve « documentation ») ; sinon capacité inconnue, retenue seulement après un test de génération.
    Claude : chaque modèle listé par l'API Models s'utilise par l'API Messages ; un modèle « retired » n'est jamais retenu."""
    from .catalogue5682 import reference
    from .modeles5681 import NOT_CHAT, SAFE_ID, _row
    utiles, inconnus, vus = [], [], set()
    for x in lignes if isinstance(lignes, list) else []:
        if not isinstance(x, dict):
            continue
        slug = str(x.get('id') or '')
        if slug in vus or not SAFE_ID.match(slug):
            continue
        vus.add(slug)
        row = _row(slug, str(x.get('name') or x.get('display_name') or ''))
        row['contexte'] = x.get('max_context_length') or x.get('max_input_tokens')
        if kind == 'anthropic':
            if x.get('type', 'model') != 'model' or x.get('lifecycle') == 'retired' or NOT_CHAT.search(slug):
                continue
            utiles.append({**row, 'preuve': 'catalogue_serveur'})
            continue
        caps = x.get('capabilities')
        chat = caps.get('completion_chat') if isinstance(caps, dict) else None
        if chat is False or (chat is None and NOT_CHAT.search(slug)):
            continue
        if chat is True:
            utiles.append({**row, 'preuve': 'catalogue_serveur'})
        elif reference(kind, slug):
            utiles.append({**row, 'preuve': 'documentation'})
        else:
            inconnus.append({**row, 'preuve': 'inconnue'})
    key = lambda r: r['id'].lower()
    return sorted(utiles, key=key), sorted(inconnus, key=key)


def verifier_cle(kind, cle, url='', transport=None):
    """Formulaires historiques (Fournisseurs, installation) : la clé saisie est testée AVANT d'être écrite. Nombre de modèles retenus."""
    from .ai_gateway import _https_url
    utiles, _ = catalogue_conversation(kind, tester_catalogue(kind, cle, _https_url(url or PROFILS[kind]['url'], kind), transport))
    if not utiles:
        raise Stop('catalogue_modeles_vide')
    return len(utiles)


# ------------------------------------------------------------------------------------------------ lecture des réglages
def db_de(state_dir):
    path = Path(str(state_dir or '')) / 'desk.sqlite3'
    if not state_dir or not path.is_file():
        return None
    db = sqlite3.connect(path, timeout=10)
    db.isolation_level = None          # transactions explicites
    return db


def valeur(db, key):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    try:
        value = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def lire(state_dir, key):
    db = db_de(state_dir)
    if db is None:
        return None
    try:
        return valeur(db, key)
    except sqlite3.Error:
        return None
    finally:
        db.close()


def connexion(kind, config):
    """(identifiant, réglage) de la connexion enregistrée de ce type ; (type, None) s'il n'y en a pas encore."""
    from .politique5682 import provider_of
    pid, item = provider_of(config, kind)
    return (pid or kind), item


def base_de(kind, provider):
    """Adresse de l'API : l'officielle ; un réglage spécifique ancien est préservé tant qu'il n'est pas migré volontairement."""
    from .ai_gateway import _https_url
    return _https_url((provider or {}).get('url') or PROFILS[kind]['url'], kind)


# ------------------------------------------------------------------------------------------------ enregistrement transactionnel
def revision_attendue(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise Stop('reglage_modifie_entre_temps') from None


def modele_publie(kind, ancien, utiles, choisi):
    """Modèle publié : le choix explicite (testé), sinon le modèle en service s'il est encore proposé, sinon (première clé) le plus économe."""
    from .catalogue5682 import level_of
    ids = [r['id'] for r in utiles]
    if choisi:
        if choisi not in ids:
            raise Stop('modele_non_teste')
        return choisi
    if ancien and ancien.get('model'):
        if ancien['model'] in ids:
            return ancien['model']
        raise Stop('modele_en_service_absent')       # la rotation attend le choix d'un modèle testé
    econome = [i for i in ids if level_of(kind, i) == 'econome']
    return (econome or ids)[0]


def reglage(desk, kind, ancien, chemin, modele, revision, base):
    """Réglage publié : modèle, plafonds, droits et activation conservés ; une première clé crée une connexion inactive, sans envoi."""
    if ancien:
        return {**ancien, 'secret_file': chemin, 'secret_precedent': str(ancien.get('secret_file') or ''), 'model': modele,
                'revision': revision, 'cle_le': _now().isoformat()}
    from .modeles5681 import DEFAULT_MONTHLY_USD, DEFAULT_REQUEST_USD, tariff
    prices = tariff(modele) or (0, 0)
    return {'type': kind, 'url': base, 'model': modele, 'enabled': False, 'external_data_allowed': False, 'secret_file': chemin,
            'timeout_seconds': 240, 'max_context_chars': 120000, 'temperature': 0, 'structured_mode': 'json', 'zdr_required': False,
            'monthly_budget_usd': DEFAULT_MONTHLY_USD, 'per_request_budget_usd': DEFAULT_REQUEST_USD, 'input_usd_per_million': prices[0],
            'output_usd_per_million': prices[1], 'state_dir': desk.c['state_dir'], 'revision': revision, 'cle_le': _now().isoformat()}


def publier(desk, kind, pid, attendu, chemin, modele, utiles, base, etat):
    """Une transaction : référence de clé, catalogue et révision ensemble ; 409 si la révision a changé depuis l'ouverture."""
    from . import catalogue5682
    from .ai_gateway import revision_of
    profil = _profil(kind)
    db = db_de(desk.c['state_dir'])
    if db is None:
        raise Stop('base_indisponible')
    try:
        db.execute('BEGIN IMMEDIATE')
        ancien = valeur(db, 'ai:provider:' + pid)
        if ancien and ancien.get('type') not in (None, kind):
            raise Stop('identifiant_fournisseur_occupe')
        actuelle = revision_of(ancien)
        if attendu is not None and attendu != actuelle:
            raise Stop('reglage_modifie_entre_temps')
        modele = modele_publie(kind, ancien, utiles, modele)
        value = reglage(desk, kind, ancien, chemin, modele, actuelle + 1, base)
        read_at = _now().isoformat()
        rows = []
        for r in utiles:
            row = catalogue5682._row(kind, {'slug': r['id'], 'nom': r.get('label'), 'contexte': r.get('contexte')}, '', '', read_at)
            row['capacites']['conversation'] = r['preuve']
            rows.append(row)
        catalogue = catalogue5682.merge(valeur(db, catalogue5682.KEY + pid), '', '', rows)
        controle = {'etat': etat, 'le': read_at, 'modeles': len(utiles), 'revision': actuelle + 1}
        for key, item in (('ai:provider:' + pid, value), (catalogue5682.KEY + pid, catalogue), (profil['controle'] + pid, controle)):
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, json.dumps(item, ensure_ascii=False)))
        for key in ('ai:models:',) + profil['supprimer']:          # liste en cache (et incident Mistral) levés
            db.execute('DELETE FROM settings WHERE key=?', (key + pid,))
        db.execute('COMMIT')
        return value
    except BaseException:
        try:
            db.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        raise
    finally:
        db.close()


def references(desk, pid):
    value = lire(desk.c['state_dir'], 'ai:provider:' + pid) or {}
    return {str(Path(p).resolve()) for p in (value.get('secret_file'), value.get('secret_precedent')) if p}


def nettoyer(desk, pid, garder=()):
    """Fichiers internes devenus orphelins : la clé en service et la précédente sont gardées (appels encore en cours)."""
    from .ai_gateway import VERSIONED_SECRET, _vault_dir
    keep = references(desk, pid) | {str(Path(p).resolve()) for p in garder if p}
    removed = 0
    for path in _vault_dir(desk).iterdir():
        name = path.name
        match = VERSIONED_SECRET.fullmatch(name)
        own = (match and match.group('pid') == pid) or name == pid + '.secret'
        if own and path.is_file() and not path.is_symlink() and str(path.resolve()) not in keep:
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                pass
    return removed


def enregistrer(desk, kind, cle, revision, owner, modele='', transport=None, choisir=None, provider_id='', generation=None, apres=None):
    """Publication d'une clé vérifiée deux fois (clé saisie puis octets enregistrés). ``choisir(lignes)`` désigne le modèle avant tout
    effet local ; ``generation(pid, réglage, chemin, base, utiles, inconnus, modèle)`` (Mistral, sur demande) rend les modèles confirmés ;
    ``apres(pid)`` suit la validation de la transaction. Rend le résultat public, sans secret."""
    from .ai_gateway import internal_secret, save_secret_version
    from .common import read_secret
    profil = _profil(kind)
    pid, provider = connexion(kind, desk.c)
    pid = provider_id or pid
    provider = (desk.settings('ai:provider:' + pid, {}) or {}) if provider_id else (provider or {})
    if provider and not internal_secret(desk, provider.get('secret_file')):
        raise Stop('cle_geree_par_le_systeme')
    attendu = revision_attendue(revision)
    base = base_de(kind, provider)
    cle = cle_valide(cle)
    utiles, inconnus = catalogue_conversation(kind, tester_catalogue(kind, cle, base, transport))     # aucun effet local
    etat = 'cle_reconnue'
    if choisir:
        modele = choisir(utiles + inconnus)
    if generation is None:
        if not utiles:
            raise Stop('catalogue_modeles_vide')
        modele_publie(kind, provider if provider.get('model') else None, utiles, modele)    # rotation : modèle testé exigé AVANT d'écrire la clé
    chemin = save_secret_version(desk, pid, cle)
    try:
        if read_secret(chemin) != cle:
            raise Stop('coffre_relecture_differente')
        if generation is not None:      # sur demande : texte fictif, par ``Model`` ; peut confirmer un modèle à capacité inconnue
            utiles = generation(pid, provider, chemin, base, utiles, inconnus, modele)
            etat = 'generation_reussie'
            if not utiles:
                raise Stop('catalogue_modeles_vide')
            modele_publie(kind, provider if provider.get('model') else None, utiles, modele)
        second, autres = catalogue_conversation(kind, tester_catalogue(kind, read_secret(chemin), base, transport))   # octets enregistrés
        if not {r['id'] for r in utiles} <= {r['id'] for r in second + autres}:
            raise Stop('catalogue_modeles_vide')
        value = publier(desk, kind, pid, attendu, chemin, modele, utiles, base, etat)
    except BaseException:
        if str(Path(chemin).resolve()) not in references(desk, pid):
            try:
                os.unlink(chemin)          # candidat jamais publié : effacé, la clé en service reste
            except OSError:
                pass
        raise
    desk.c.setdefault('ai_providers', {})[pid] = value        # après la validation de la transaction
    nettoyer(desk, pid)
    if apres:
        apres(pid)
    desk.audit(profil['audit'], {'provider': pid, 'type': kind, 'revision': value['revision'], 'modeles': len(utiles), 'modele': value['model'],
                                 'resultat': etat, 'secret_replaced': True, 'donnees_transmises': 'texte fictif' if generation is not None else 'aucune'})
    return {'ok': True, 'provider': pid, 'revision': value['revision'], 'modele': value['model'], 'modeles': len(utiles), 'statut': etat,
            'le': _now().isoformat(), 'actif': bool(value.get('enabled')),
            'message': 'Clé enregistrée : %d modèle(s) de conversation. Le mode de l’IA et les routes sont inchangés.' % len(utiles)}


def preparer_mode(desk, kind, data, owner, enregistrer_cle=None):
    """Mode à fournisseur unique (API Mistral ou API Claude) : une clé saisie passe par le service commun ; sinon le catalogue est relu
    avec la clé en service. L'activation (accord coché dans le formulaire du mode) vient en dernier, une fois la connexion vérifiée."""
    from . import catalogue5682
    from .ai_gateway import save_provider
    from .modeles5681 import DEFAULT_MONTHLY_USD, DEFAULT_REQUEST_USD
    cle = str(data.get('api_key') or '').strip()
    if cle:
        (enregistrer_cle or (lambda c, r: enregistrer(desk, kind, c, r, owner)))(cle, data.get('revision_cle'))
    pid, current = connexion(kind, desk.c)
    if not current:
        raise Stop('cle_api_fournisseur_absente')
    if not cle and not [r for r in catalogue5682.refresh(desk, pid) if r['statut'] == 'actif']:
        raise Stop('catalogue_modeles_vide')
    save_provider(desk, pid, kind, current.get('url') or '', current.get('model') or catalogue5682.PLACEHOLDER[kind], '', True, True,
                  bool(current.get('zdr_required')), float(current.get('monthly_budget_usd') or DEFAULT_MONTHLY_USD),
                  float(current.get('per_request_budget_usd') or DEFAULT_REQUEST_USD), float(current.get('input_usd_per_million') or 0),
                  float(current.get('output_usd_per_million') or 0))

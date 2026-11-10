"""5.6.96 (lot accueil, F03 révisé le 9 novembre 2026) : adaptateur météo partagé, fournisseur OpenWeather (OpenWeatherMap).

Utilisé par la carte d'Aujourd'hui (F04) et par l'outil ``meteo`` de la discussion (lot discussion). Contrat (ne pas le changer sans prévenir ce lot) :
``previsions(desk, owner, ville=None)`` → ``{'statut': 'ok'|'perime'|'indisponible'|'desactive'|'refuse', 'source': 'openweathermap',
'attribution': 'Données météo fournies par OpenWeather', 'attribution_lien': 'https://openweathermap.org/', 'instant': iso, 'fuseau': str, 'ville': str,
'jours': [{'date': iso, 'icone': id, 'libelle': str, 'min': int|None, 'max': int|None}] (aujourd'hui puis J+1, J+2, J+3), 'consulte_le': iso, 'erreur': str}``.
Le jour même, passé le dernier créneau de prévision (fin de soirée), ``min`` et ``max`` valent ``None`` : jamais une valeur inventée.

Décision du cabinet (révision du 9 novembre 2026) : OpenWeather, clé de l'offre du cabinet. Vérifié dans la documentation officielle le 9 novembre 2026 :
- prévision « 5 day / 3 hour forecast » (``/data/2.5/forecast?lat=&lon=&appid=&units=metric``), incluse dans l'offre gratuite et les offres payantes
  (60 appels par minute pour l'offre gratuite), créneaux de 3 heures en UTC regroupés ici en jours dans le fuseau du compte ;
- géocodage direct (``/geo/1.0/direct?q=&limit=5&appid=``), au moment du réglage seulement : l'administrateur choisit explicitement parmi les homonymes,
  la ville est ensuite désignée par ses coordonnées ;
- licence des données : ODbL / CC BY-SA 4.0 pour les offres Free à Professional ; attribution obligatoire (« Weather data provided by OpenWeather »
  et un lien vers openweathermap.org) : affichée sur la carte avec son lien. Le logo n'est pas repris (aucun téléchargement) ;
- clé : activée jusqu'à deux heures après l'inscription (un 401 juste après la création n'est pas forcément une clé erronée) ; 429 au-delà du quota.

Réglage (table ``settings``, clé ``meteo5696:reglage``, révisé) : ``actif`` (**faux par défaut**), lieu choisi (ville, pays, région, coordonnées), empreinte
de la clé testée et date du test. La clé elle-même est dans le coffre (``vault567``), lue au moment de l'appel par ``read_secret`` : jamais dans la
configuration, une réponse, un journal ou une erreur (``erreurs5634.anonymize`` retire la requête de l'adresse). La météo ne s'active qu'avec une
clé enregistrée **et testée** et un lieu choisi. ``meteo5696.enabled = false`` dans la configuration coupe tout, quel que soit le réglage.

Ce qui part : la position, les unités et la clé ; au réglage, le nom de ville saisi par l'administrateur. Ni prénom, ni dossier, ni agenda.
``data_policy5689.exiger(cfg, 'meteo', 'openweathermap', finalite='meteo')`` avant **chaque** appel et avant de servir une réponse gardée en cache
(mode local strict → ``refuse``, aucun appel). Cache en mémoire par (fournisseur, position, unités, fuseau) : 30 minutes ; une seule requête en vol
par clé (verrou) ; au-delà, le dernier relevé est servi comme ``perime`` jusqu'à six heures, puis « indisponible ». Aucun modèle de langage, aucun
fichier écrit (rien à conserver : pas de rétention à régler).
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import unicodedata
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import HTTP, Stop, read_secret
from .data_policy5689 import exiger

SOURCE = 'openweathermap'
ATTRIBUTION = 'Données météo fournies par OpenWeather'
ATTRIBUTION_LIEN = 'https://openweathermap.org/'
LIEN_CLE = 'https://home.openweathermap.org/users/sign_in'       # « Obtenir une clé » (décision du cabinet)
BASE = 'https://api.openweathermap.org'
DELAI = 5                      # secondes : la carte n'attend jamais longtemps
TTL = 30 * 60                  # cache : 30 minutes
PERIME_MAX = 6 * 3600          # dernier relevé servi comme « perime » jusqu'à six heures, puis indisponible
JOURS = 4                      # aujourd'hui puis J+1, J+2, J+3
UNITES = 'metric'
CLE_REGLAGE = 'meteo5696:reglage'
LIMITE_REPONSE = 400_000
HTTP_FACTORY = None            # tests et banc de parcours : fabrique d'un faux client (aucun appel réel)
# Identifiants de conditions d'OpenWeather → icône (SVG dessiné dans v5696-accueil.js) ; le libellé vient du catalogue (f96.meteo.<icone>).
ICONES = ('soleil', 'eclaircies', 'nuages', 'brouillard', 'bruine', 'pluie', 'averses', 'neige', 'orage', 'inconnu')
GRAVITE = {i: n for n, i in enumerate(ICONES)}     # départage d'une égalité : la condition la plus marquée l'emporte
CODES_SERVICE = {'http_401': 'cle_refusee', 'http_403': 'cle_refusee', 'http_429': 'limite_atteinte', 'delai_http_depasse': 'service_injoignable',
                 'connexion_http_indisponible': 'service_injoignable'}

_cache = {}                    # clé → {'jours': [...], 'moment': monotonic, 'consulte_le': iso}
_verrous = {}                  # clé → Lock : une seule requête en vol par clé
_global = threading.Lock()


def vider_cache():
    """Tests et changement de réglage : repart de zéro."""
    with _global:
        _cache.clear()
        _verrous.clear()


def _verrou(cle):
    with _global:
        return _verrous.setdefault(cle, threading.Lock())


def icone_de(code):
    """Groupe de conditions d'OpenWeather (identifiant numérique) → icône de la carte."""
    if not isinstance(code, int) or isinstance(code, bool):
        return 'inconnu'
    if 200 <= code < 300:
        return 'orage'
    if 300 <= code < 400:
        return 'bruine'
    if 500 <= code < 520:
        return 'pluie'
    if 520 <= code < 600:
        return 'averses'
    if 600 <= code < 700:
        return 'neige'
    if 700 <= code < 800:
        return 'brouillard'
    return {800: 'soleil', 801: 'eclaircies', 802: 'eclaircies', 803: 'nuages', 804: 'nuages'}.get(code, 'inconnu')


def _plier(texte):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(texte or '')) if not unicodedata.combining(c)).casefold().strip()


# ------------------------------------------------------------------------------------------------ réglage
def _brut(desk):
    valeur = desk.settings(CLE_REGLAGE, None)
    return valeur if isinstance(valeur, dict) else {}


def _coupe_par_configuration(desk):
    cfg = desk.c.get('meteo5696')
    return isinstance(cfg, dict) and cfg.get('enabled') is False


def chemin_cle(desk):
    return Path(desk.c['state_dir']) / 'vault' / 'meteo5696' / 'cle.secret'


def _empreinte(cle):
    return hashlib.sha256(('meteo5696:' + cle).encode('utf-8')).hexdigest()


def _cle_enregistree(desk):
    """Clé du coffre, ou '' si absente ou illisible. Jamais renvoyée à l'écran ni journalisée."""
    try:
        return read_secret(chemin_cle(desk))
    except Stop:
        return ''


def configuration(desk):
    """État lisible du réglage, sans secret : ``enabled`` dit si la carte et l'outil peuvent consulter le service."""
    brut = _brut(desk)
    cle = _cle_enregistree(desk)
    testee = bool(cle) and brut.get('cle_testee') == _empreinte(cle)
    lieu = brut.get('lieu') if isinstance(brut.get('lieu'), dict) else {}
    valide = False
    try:
        _lieu_valide(lieu)
        valide = True
    except Stop:
        pass
    out = {'actif': brut.get('actif') is True, 'cle_enregistree': bool(cle), 'cle_testee': testee, 'teste_le': str(brut.get('teste_le') or ''),
           'ville': str(lieu.get('ville') or '')[:80], 'pays': str(lieu.get('pays') or '')[:2], 'region': str(lieu.get('region') or '')[:80],
           'latitude': lieu.get('latitude') if valide else None, 'longitude': lieu.get('longitude') if valide else None, 'valide': valide,
           'revision': int(brut.get('revision') or 0), 'coupe': _coupe_par_configuration(desk)}
    out['enabled'] = out['actif'] and testee and valide and not out['coupe']
    return out


def _lieu_valide(lieu):
    """Ville de 1 à 80 caractères imprimables ; coordonnées numériques finies dans les bornes, arrondies à trois décimales (centre d'une commune)."""
    ville = ' '.join(str((lieu or {}).get('ville') or '').split())
    if not 1 <= len(ville) <= 80 or re.search(r'[<>\x00-\x1f\x7f]', ville):
        raise Stop('ville_meteo_invalide')
    try:
        lat, lon = float(lieu.get('latitude')), float(lieu.get('longitude'))
    except (TypeError, ValueError, AttributeError):
        raise Stop('coordonnees_meteo_invalides') from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise Stop('coordonnees_meteo_invalides')
    pays = str(lieu.get('pays') or '').strip().upper()
    if pays and not re.fullmatch(r'[A-Z]{2}', pays):
        raise Stop('pays_meteo_invalide')
    region = ' '.join(str(lieu.get('region') or '').split())[:80]
    if re.search(r'[<>\x00-\x1f\x7f]', region):
        raise Stop('ville_meteo_invalide')
    return {'ville': ville, 'pays': pays, 'region': region, 'latitude': round(lat, 3), 'longitude': round(lon, 3)}


def _ecrire(desk, revision, changer, action, trace):
    """Écriture versionnée du réglage (``version_en_conflit`` si la page est périmée), dans une transaction ; audit sans secret."""
    db = desk.db
    db.execute('BEGIN IMMEDIATE')
    try:
        row = db.execute('SELECT value FROM settings WHERE key=?', (CLE_REGLAGE,)).fetchone()
        brut = json.loads(row[0]) if row else {}
        brut = brut if isinstance(brut, dict) else {}
        if revision is not None and str(revision) != str(int(brut.get('revision') or 0)):
            raise Stop('version_en_conflit')
        nouveau = changer(dict(brut))
        nouveau['revision'] = int(brut.get('revision') or 0) + 1
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (CLE_REGLAGE, json.dumps(nouveau)))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    desk.audit(action, trace)
    vider_cache()
    return configuration(desk)


def _client(http=None):
    if http is not None:
        return http
    if HTTP_FACTORY is not None:
        return HTTP_FACTORY(BASE)
    return HTTP(BASE, timeout=DELAI)


def _code(panne):
    """Erreur du transport → code public court ; jamais le texte du service (il pourrait recopier la requête)."""
    brut = str(panne).split(':')[0]
    if brut in CODES_SERVICE:
        return CODES_SERVICE[brut]
    if brut.startswith('http_5'):
        return 'service_indisponible'
    return 'reponse_meteo_invalide' if not isinstance(panne, Stop) else (brut or 'panne')


def _politique(desk, owner):
    exiger(desk.c, 'meteo', SOURCE, finalite='meteo', acteur=owner, local=False)


def _get(desk, chemin, params, cle, http=None):
    url = BASE + chemin + '?' + urlencode({**params, 'appid': cle})
    brut = _client(http).request('GET', url, limit=LIMITE_REPONSE)
    return json.loads(brut)


def tester_cle(desk, acteur, cle='', revision=None, http=None):
    """« Tester » : la clé saisie (ou, champ vide, celle du coffre) est essayée par une prévision d'un seul créneau à la position 0, 0 (aucune donnée
    du cabinet). Une clé saisie n'est écrite dans le coffre **qu'après** un test réussi : une clé erronée ne remplace jamais la clé en service."""
    cle = str(cle or '').strip()
    if cle and (not 16 <= len(cle) <= 128 or not re.fullmatch(r'[A-Za-z0-9_-]+', cle)):
        raise Stop('cle_meteo_invalide')
    nouvelle = bool(cle)
    cle = cle or _cle_enregistree(desk)
    if not cle:
        raise Stop('cle_meteo_absente')
    _politique(desk, acteur)
    try:
        donnees = _get(desk, '/data/2.5/forecast', {'lat': 0, 'lon': 0, 'cnt': 1, 'units': UNITES}, cle, http)
        if not isinstance(donnees, dict) or not isinstance(donnees.get('list'), list):
            raise ValueError('forme')
    except (Stop, ValueError, TypeError) as panne:
        raise Stop(_code(panne)) from None
    if nouvelle:
        from .vault567 import write
        write(chemin_cle(desk), cle, limit=128)
    teste_le = datetime.now(timezone.utc).isoformat(timespec='seconds')

    def changer(brut):
        brut.update(cle_testee=_empreinte(cle), teste_le=teste_le)
        return brut
    return _ecrire(desk, revision, changer, 'meteo5696_cle_testee', {'nouvelle_cle': nouvelle})


def effacer_cle(desk, acteur, revision=None):
    """Effacement explicite : la clé quitte le coffre et la météo se désactive."""
    chemin = chemin_cle(desk)
    if chemin.is_file():
        chemin.unlink()

    def changer(brut):
        brut.update(actif=False, cle_testee='', teste_le='')
        return brut
    return _ecrire(desk, revision, changer, 'meteo5696_cle_effacee', {})


def chercher_lieux(desk, acteur, ville, pays='', http=None):
    """Géocodage au moment du réglage : jusqu'à cinq lieux homonymes, que l'administrateur départage explicitement. Exige une clé testée."""
    ville = ' '.join(str(ville or '').split())
    if not 1 <= len(ville) <= 80 or re.search(r'[<>,;\x00-\x1f\x7f]', ville):
        raise Stop('ville_meteo_invalide')
    pays = str(pays or '').strip().upper()
    if pays and not re.fullmatch(r'[A-Z]{2}', pays):
        raise Stop('pays_meteo_invalide')
    cfg = configuration(desk)
    if not cfg['cle_testee']:
        raise Stop('cle_meteo_non_testee')
    _politique(desk, acteur)
    try:
        donnees = _get(desk, '/geo/1.0/direct', {'q': ville + (',' + pays if pays else ''), 'limit': 5}, _cle_enregistree(desk), http)
        if not isinstance(donnees, list):
            raise ValueError('forme')
    except (Stop, ValueError, TypeError) as panne:
        raise Stop(_code(panne)) from None
    lieux = []
    for d in donnees[:5]:
        try:
            lieux.append(_lieu_valide({'ville': d.get('name'), 'pays': d.get('country') or '', 'region': d.get('state') or '',
                                       'latitude': d.get('lat'), 'longitude': d.get('lon')}))
        except (Stop, AttributeError):
            continue
    return lieux


def choisir_lieu(desk, acteur, lieu, revision=None):
    """Lieu choisi par l'administrateur parmi les homonymes : il est ensuite désigné par ses coordonnées (cache distinct par position)."""
    propre = _lieu_valide(lieu)

    def changer(brut):
        brut['lieu'] = propre
        return brut
    return _ecrire(desk, revision, changer, 'meteo5696_lieu', {'pays': propre['pays']})


def activer(desk, acteur, actif, revision=None):
    """Activer exige une clé enregistrée et testée et un lieu choisi ; désactiver est toujours possible."""
    if not isinstance(actif, bool):
        raise Stop('valeur_invalide')
    if actif:
        cfg = configuration(desk)
        if not cfg['cle_testee']:
            raise Stop('cle_meteo_non_testee')
        if not cfg['valide']:
            raise Stop('lieu_meteo_absent')

    def changer(brut):
        brut['actif'] = actif
        return brut
    return _ecrire(desk, revision, changer, 'meteo5696_activation', {'actif': actif})


# ------------------------------------------------------------------------------------------------ fuseau du compte
def fuseau(desk, owner):
    """Fuseau IANA du compte (``settings568`` : réglage propre à la personne), sinon celui du cabinet, sinon celui de l'agenda, sinon Europe/Paris.
    Le profil ``settings568.profile`` complète toujours par une valeur par défaut : on lit donc la valeur enregistrée pour la personne elle-même."""
    candidats = []
    for qui in ([str(owner)] if owner and owner != 'cabinet' else []) + ['cabinet']:
        brut = desk.settings('proactive568:profile:' + qui, {}) or {}
        if isinstance(brut, dict):
            candidats.append(brut.get('timezone'))
    agenda = desk.c.get('calendar')
    candidats.append(agenda.get('timezone') if isinstance(agenda, dict) else None)
    for nom in candidats:
        try:
            if nom and isinstance(nom, str):
                return str(ZoneInfo(nom).key)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            continue
    return 'Europe/Paris'


# ------------------------------------------------------------------------------------------------ prévisions
def _libelle(icone):
    from .i18n5631 import t
    return t('f96.meteo.' + icone)


def _temperature(valeur):
    if not isinstance(valeur, (int, float)) or isinstance(valeur, bool) or not math.isfinite(valeur) or not -100 <= valeur <= 70:
        raise ValueError('temperature')
    return valeur


def regrouper(payload, debut, tz):
    """Créneaux de 3 heures (UTC) → quatre jours dans le fuseau du compte : min et max des créneaux du jour, condition la plus fréquente en journée
    (6 h-21 h, sinon tous les créneaux du jour ; égalité : la plus marquée). Un jour sans créneau (aujourd'hui en fin de soirée) garde min et max
    à ``None``. Les jours J+1 à J+3 doivent être couverts : sinon la réponse est jugée invalide."""
    zone = ZoneInfo(tz)
    par_jour = {}
    for creneau in payload['list']:
        moment = datetime.fromtimestamp(int(creneau['dt']), timezone.utc).astimezone(zone)
        jour = moment.date()
        if not 0 <= (jour - debut).days < JOURS:
            continue
        principal = creneau['main']
        bas, haut = _temperature(principal['temp_min']), _temperature(principal['temp_max'])
        meteo = creneau.get('weather') or [{}]
        code = meteo[0].get('id') if isinstance(meteo[0], dict) else None
        par_jour.setdefault(jour, []).append((moment.hour, min(bas, haut), max(bas, haut), icone_de(code)))
    jours = []
    for i in range(JOURS):
        jour = debut + timedelta(days=i)
        creneaux = par_jour.get(jour) or []
        if not creneaux:
            if i:
                raise ValueError('horizon')
            jours.append({'date': jour.isoformat(), 'icone': 'inconnu', 'libelle': _libelle('fin_de_journee'), 'min': None, 'max': None})
            continue
        journee = [c[3] for c in creneaux if 6 <= c[0] <= 21] or [c[3] for c in creneaux]
        compte = Counter(journee)
        icone = max(compte, key=lambda k: (compte[k], GRAVITE[k] if k != 'inconnu' else -1))
        jours.append({'date': jour.isoformat(), 'icone': icone, 'libelle': _libelle(icone),
                      'min': int(round(min(c[1] for c in creneaux))), 'max': int(round(max(c[2] for c in creneaux)))})
    return jours


def _reponse(base, statut, jours=(), consulte_le='', erreur=''):
    return {**base, 'statut': statut, 'jours': [dict(j) for j in jours], 'consulte_le': consulte_le, 'erreur': erreur}


def previsions(desk, owner, ville=None, *, http=None, maintenant=None, horloge=time.monotonic):
    """Contrat F03. ``ville`` : seule la ville réglée est connue (par ses coordonnées) ; un autre nom donne ``indisponible`` sans aucun appel :
    le nom d'une ville demandée dans la discussion ne part jamais vers le service. ``http``, ``maintenant`` et ``horloge`` : tests (faux client,
    temps simulé)."""
    cfg = configuration(desk)
    tz = fuseau(desk, owner)
    now = (maintenant or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    base = {'statut': 'indisponible', 'source': SOURCE, 'attribution': ATTRIBUTION, 'attribution_lien': ATTRIBUTION_LIEN, 'instant': now.isoformat(timespec='minutes'),
            'fuseau': tz, 'ville': cfg['ville'], 'jours': [], 'consulte_le': '', 'erreur': ''}
    if not cfg['enabled']:
        return _reponse(base, 'desactive', erreur='cle_meteo_non_testee' if cfg['actif'] and not cfg['cle_testee'] else '')
    if ville is not None and _plier(ville) != _plier(cfg['ville']):
        return _reponse(base, 'indisponible', erreur='ville_non_configuree')
    try:   # avant chaque appel et avant toute lecture du cache : politique de sortie contrôlée
        _politique(desk, owner)
    except Stop as refus:
        return _reponse(base, 'refuse', erreur=str(refus).split(':')[0] or 'refuse')
    cle_cache = (SOURCE, cfg['latitude'], cfg['longitude'], UNITES, tz)
    with _verrou(cle_cache):
        t = horloge()
        entree = _cache.get(cle_cache)
        if entree and t - entree['moment'] < TTL and entree['jours'] and entree['jours'][0]['date'] == now.date().isoformat():
            return _reponse(base, 'ok', entree['jours'], entree['consulte_le'])
        cle = _cle_enregistree(desk)
        try:
            if not cle:
                raise Stop('cle_meteo_absente')
            jours = regrouper(_get(desk, '/data/2.5/forecast', {'lat': cfg['latitude'], 'lon': cfg['longitude'], 'units': UNITES}, cle, http), now.date(), tz)
        except (Stop, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, OverflowError) as panne:
            code = _code(panne) if isinstance(panne, Stop) else 'reponse_meteo_invalide'
            if entree and t - entree['moment'] <= PERIME_MAX:
                return _reponse(base, 'perime', entree['jours'], entree['consulte_le'], code)
            return _reponse(base, 'indisponible', erreur=code)
        entree = {'jours': jours, 'moment': t, 'consulte_le': now.isoformat(timespec='minutes')}
        _cache[cle_cache] = entree
        return _reponse(base, 'ok', jours, entree['consulte_le'])


def carte_guide(desk, env, prefix, admin=False):
    """5.6.96 (intégration I06) : carte de réglage de la météo dans le guide ; même formulaire que Paramètres › Connexions."""
    from .accueil_web5696 import reglages_html
    return reglages_html(desk, prefix, 'administrateur' if admin else '')

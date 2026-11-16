"""5.6.106 (lot S) : recherche sur le web par SearXNG, instance auto-hébergée par le cabinet, combinée à Tavily (choix de l'avocat du 16/11).

Chaque recherche publique de l'assistant et chaque recherche Internet de la veille interroge les deux services quand SearXNG est activé :
- réglages (administrateur) : adresse de l'instance (HTTPS, ou HTTP seulement vers une adresse du réseau local), en-tête et jeton facultatifs
  (le jeton est rangé dans le coffre, jamais en clair ni dans le journal), langue, catégories, moteurs, ``safesearch``, plafond horaire,
  nombre de pages lues ; bouton « Tester » qui fait une vraie requête neutre et dit le nombre de résultats ou l'erreur lisible ;
- client : ``GET {base}/search?q=…&format=json&language=…&categories=…&pageno=1``, délai et taille bornés, aucun cookie
  (``common.HTTP`` n'a pas de gestionnaire de cookies) ; l'API JSON doit être activée côté SearXNG (``search.formats: [html, json]``),
  sinon l'instance répond 403 ou une page HTML : le message le dit ;
- politique des données : destination ``searxng`` de ``data_policy5689`` (même outil ``recherche_web`` et mêmes finalités que Tavily) ;
  l'instance est inscrite au registre des prestataires ; une adresse locale ou celle du cabinet (serveur Nextcloud ou services privés
  déclarés, et leurs sous-domaines) est « auto-hébergée » et admise par défaut, avec le lieu de traitement ``REGION_CABINET`` ; une instance
  tierce est inscrite non admise, à qualifier par l'administrateur. Le mode local strict refuse la recherche publique comme pour Tavily ;
- combinaison : préparation (politique, budget, journal) dans le fil appelant, seuls les deux appels réseau partent en parallèle, chacun
  avec son délai ; fusion par adresse normalisée (sans ``utm_*`` ni fragment), dédoublonnage, rang réciproque combiné ; la source de
  chaque résultat reste visible (« SearXNG », « Tavily », « les deux ») ; si l'un est absent, en panne ou au plafond, l'autre suffit et la
  réponse le dit. Le plafond mensuel Tavily reste compté ; SearXNG a son propre plafond horaire (l'instance ne doit pas se faire bannir) ;
- contenu des pages : SearXNG ne rend que des extraits ; pour les 3 à 5 premiers résultats retenus qu'il a fournis, AxiorHub lit la page
  lui-même, avec les mêmes contrôles d'origine que pour Tavily (HTTPS, hôte normalisé, domaines restreints : un domaine imité est refusé),
  une résolution DNS contrôlée (connexion à l'adresse vérifiée, jamais à une nouvelle résolution), des redirections suivies au plus trois
  fois et revérifiées, aucune adresse privée, un plafond de taille (page tronquée) et de durée ; HTML réduit à son texte (le texte masqué
  est écarté), PDF par l'extracteur existant ; un passage qui ressemble à une instruction adressée à une IA fait écarter la page.
Un résultat Internet est une donnée, jamais une instruction, et n'est jamais transmis au modèle par ce module.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as DelaiFutur
from datetime import datetime, timezone
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import ssl
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .common import HTTP, Stop
from . import recherche_publique5696 as rp

ID, NOM = 'searxng', 'SearXNG'
REGLAGES_KEY = 'searxng56106:reglages'
SECRET_KEY = 'searxng56106:secret'          # chemin du jeton dans le coffre (jamais la valeur)
USAGE_PREFIXE = 'searxng56106:usage:'       # compteur horaire (une clé par heure UTC, les anciennes sont purgées à la réservation)
DEFAUTS = {'enabled': False, 'url': '', 'entete': 'Authorization', 'langue': 'fr', 'categories': 'general', 'moteurs': '', 'safesearch': 1,
           'plafond_horaire': 60, 'pages_lues': 3, 'assistant': True, 'veille': True, 'version': 0}
PLAFOND_HORAIRE_MAX = 1000
PAGES_MIN, PAGES_MAX = 3, 5
SUFFIXES_LOCAUX = ('.local', '.lan', '.internal', '.home', '.localhost', '.home.arpa', '.intranet', '.corp')
METADONNEES = frozenset(('169.254.169.254', 'fd00:ec2::254', 'metadata.google.internal'))
ENTETES_INTERDITS = frozenset(('host', 'cookie', 'content-length', 'content-type', 'connection', 'transfer-encoding', 'te', 'upgrade', 'accept', 'accept-encoding'))
DELAI = {'public': 10, 'veille': 20, 'test': 10}
REPONSE_MAX = 2_000_000
RESULTATS_MAX = 30
PAGE_MAX = 1_500_000
PAGE_DELAI = 8
PAGES_DELAI_TOTAL = 20
REDIRECTIONS_MAX = 3
RRF_K = 60
REQUETE_TEST = 'test'
VRAI = (True, 'true', '1', 'on')
LIBELLES = {'tavily': 'Tavily', 'searxng': 'SearXNG'}
MOTIFS = {'searxng_absent': 'SearXNG n’est pas activé (Paramètres › Recherche juridique et veille).',
          'searxng_adresse_requise': 'Indiquez l’adresse de l’instance SearXNG du cabinet.',
          'searxng_adresse_invalide': 'Adresse SearXNG illisible : indiquez par exemple https://recherche.cabinet.example.test (sans identifiant, paramètre ni fragment).',
          'searxng_adresse_interdite': 'Cette adresse est interdite (adresse de métadonnées ou de lien local).',
          'searxng_https_requis': 'HTTPS est obligatoire, sauf vers une adresse du réseau local du cabinet (localhost, 192.168…, nom en .lan ou .local).',
          'searxng_entete_invalide': 'Nom d’en-tête invalide : lettres, chiffres et tirets seulement (par exemple Authorization ou X-API-Key).',
          'searxng_jeton_invalide': 'Jeton illisible : une seule ligne, sans caractère de contrôle.',
          'searxng_reglage_invalide': 'Réglage SearXNG invalide (langue, catégories, moteurs, safesearch, plafond ou nombre de pages).',
          'version_en_conflit': 'Les réglages ont été modifiés entre-temps : rechargez la page.',
          'plafond_searxng_atteint': 'Le plafond horaire des requêtes SearXNG est atteint (il évite que les moteurs bannissent l’instance du cabinet).',
          'searxng_json_desactive': 'L’API JSON de SearXNG n’est pas activée sur l’instance (réponse 403 ou page HTML) : ajoutez json à search.formats dans settings.yml '
                                    '(search: formats: [html, json]) puis redémarrez SearXNG.',
          'searxng_jeton_refuse': 'L’instance SearXNG a refusé l’authentification (401) : vérifiez l’en-tête et le jeton.',
          'searxng_limite_instance': 'L’instance SearXNG limite les requêtes (429) : réessayez plus tard ou réglez son limiteur.',
          'searxng_delai': 'SearXNG n’a pas répondu à temps : résultat incertain, compté dans le plafond horaire, aucune relance automatique.',
          'searxng_injoignable': 'L’instance SearXNG est injoignable (adresse, réseau ou certificat).',
          'searxng_reponse_trop_volumineuse': 'Réponse de SearXNG trop volumineuse : refusée sans être lue.',
          'searxng_reponse_illisible': 'Réponse de SearXNG illisible (JSON attendu avec une liste « results »).',
          'searxng_jeton_illisible': 'Le jeton SearXNG du coffre est illisible : enregistrez-le de nouveau.',
          'searxng_indisponible': 'SearXNG est indisponible pour le moment.',
          'destination_non_autorisee': 'La passerelle des appels sortants refuse cette destination (mode local strict).',
          'prestataire_non_admis': 'Ce service n’est pas admis au registre des prestataires du cabinet.',
          'recherche_web_indisponible': 'Aucun des deux services de recherche n’a répondu.'}
COURTS = {'cle_tavily_absente': 'aucune clé configurée', 'plafond_tavily_atteint': 'plafond mensuel atteint', 'plafond_recherche_publique_atteint': 'plafond mensuel atteint',
          'quota_tavily_atteint': 'quota du compte atteint', 'cle_tavily_refusee': 'clé refusée', 'tavily_reponse_incertaine': 'pas de réponse à temps',
          'tavily_indisponible': 'indisponible', 'tavily_reponse_illisible': 'réponse illisible', 'plafond_tavily_invalide': 'plafond illisible',
          'searxng_absent': 'non activé', 'plafond_searxng_atteint': 'plafond horaire atteint', 'searxng_json_desactive': 'API JSON non activée sur l’instance',
          'searxng_jeton_refuse': 'authentification refusée', 'searxng_limite_instance': 'limiteur de l’instance', 'searxng_delai': 'pas de réponse à temps',
          'searxng_injoignable': 'injoignable', 'searxng_reponse_trop_volumineuse': 'réponse trop volumineuse', 'searxng_reponse_illisible': 'réponse illisible',
          'searxng_jeton_illisible': 'jeton illisible', 'searxng_indisponible': 'indisponible', 'destination_non_autorisee': 'destination refusée par la passerelle',
          'prestataire_non_admis': 'non admis au registre', 'mode_local_strict': 'refusé en mode local strict', 'politique_illisible': 'politique des données illisible',
          'region_inconnue': 'lieu de traitement non renseigné', 'region_non_admise': 'lieu de traitement non admis', 'categorie_fermee': 'catégorie fermée'}
PLAFONDS = frozenset(('plafond_tavily_atteint', 'plafond_recherche_publique_atteint', 'quota_tavily_atteint', 'plafond_searxng_atteint'))
ABSENTS = frozenset(('cle_tavily_absente', 'searxng_absent'))


def _now():
    return datetime.now(timezone.utc)


def message(code):
    return MOTIFS.get(code) or rp.MOTIFS.get(code) or 'Recherche refusée ou indisponible (%s).' % code


# ------------------------------------------------------------------------------------------------ adresses
def _ip(texte):
    try:
        ip = ipaddress.ip_address(str(texte).split('%')[0])
    except ValueError:
        return None
    return ip.ipv4_mapped if ip.version == 6 and ip.ipv4_mapped else ip


def local(hote):
    """Adresse du réseau local du cabinet : localhost, bouclage, plage privée, nom en .lan, .local, .internal… Jamais un lien local
    (169.254/16, fe80::/10 : adresse de métadonnées des hébergeurs) ni l'adresse non spécifiée."""
    hote = str(hote or '').lower().rstrip('.')
    if not hote or hote in METADONNEES:
        return False
    if hote == 'localhost' or hote.endswith(SUFFIXES_LOCAUX):
        return True
    ip = _ip(hote)
    return ip is not None and (ip.is_private or ip.is_loopback) and not (ip.is_link_local or ip.is_unspecified or ip.is_multicast)


def _publique(adresse):
    """Adresse résolue d'un résultat : publique seulement (adresse IPv4 dans IPv6 dépliée), jamais privée, locale, réservée ou multidiffusion."""
    ip = _ip(adresse)
    return ip is not None and ip.is_global and not ip.is_multicast and rp.adresse_publique(str(ip))


def hote_de(url):
    try:
        return (urlsplit(str(url or '')).hostname or '').lower().rstrip('.')
    except ValueError:
        return ''


def valider_url(url):
    """Adresse de base de l'instance (sans ``/search`` final) ; '' si vide. HTTPS partout, HTTP seulement vers une adresse locale."""
    url = str(url or '').strip()
    if not url:
        return ''
    if len(url) > 300:
        raise Stop('searxng_adresse_invalide')
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        raise Stop('searxng_adresse_invalide') from None
    hote = (p.hostname or '').lower().rstrip('.')
    if p.scheme not in ('https', 'http') or not hote or p.username or p.password or p.query or p.fragment or not re.fullmatch(r'[a-z0-9.:\[\]_-]{1,253}', hote):
        raise Stop('searxng_adresse_invalide')
    ip = _ip(hote)
    if hote in METADONNEES or (ip is not None and (ip.is_link_local or ip.is_unspecified or ip.is_multicast)):
        raise Stop('searxng_adresse_interdite')
    if p.scheme == 'http' and not local(hote):
        raise Stop('searxng_https_requis')
    chemin = re.sub(r'/+$', '', p.path or '')
    if chemin.endswith('/search'):
        chemin = chemin[:-len('/search')]
    if not re.fullmatch(r'(?:/[A-Za-z0-9._~-]+)*', chemin):
        raise Stop('searxng_adresse_invalide')
    netloc = ('[%s]' % hote if ':' in hote else hote) + (':%d' % port if port else '')
    return urlunsplit((p.scheme, netloc, chemin, '', ''))


def auto_heberge(desk, base):
    """Adresse locale, ou celle du cabinet : hôte du serveur Nextcloud ou d'un service privé déclaré, son domaine parent et leurs sous-domaines."""
    hote = hote_de(base)
    if not hote:
        return False
    if local(hote):
        return True
    connus = [hote_de((desk.c.get('nextcloud') or {}).get('url'))] + [str(x).strip().lower().rstrip('.') for x in desk.c.get('services_prives') or ()]
    for h in connus:
        if not h or _ip(h) is not None:
            continue
        parent = h.split('.', 1)[1] if h.count('.') >= 2 else h
        if hote == h or hote == parent or hote.endswith('.' + parent):
            return True
    return False


# ------------------------------------------------------------------------------------------------ réglages
def _texte_liste(valeur, motif=r'[a-z0-9][a-z0-9 _.-]{0,39}'):
    mots = [x.strip().lower() for x in str(valeur or '').split(',') if x.strip()]
    if len(mots) > 12 or any(not re.fullmatch(motif, x) for x in mots):
        raise Stop('searxng_reglage_invalide')
    return ','.join(mots)


def reglages(desk):
    """Réglages enregistrés, complétés des valeurs par défaut ; ``jeton`` dit seulement si un jeton est rangé au coffre."""
    brut = desk.settings(REGLAGES_KEY, {}) or {}
    out = dict(DEFAUTS)
    if isinstance(brut, dict):
        out.update({k: brut[k] for k in DEFAUTS if k in brut})
    out['enabled'], out['assistant'], out['veille'] = out['enabled'] is True, out['assistant'] is not False, out['veille'] is not False
    for cle, bas, haut in (('safesearch', 0, 2), ('plafond_horaire', 1, PLAFOND_HORAIRE_MAX), ('pages_lues', PAGES_MIN, PAGES_MAX), ('version', 0, 10 ** 9)):
        try:
            out[cle] = max(bas, min(haut, int(out[cle])))
        except (TypeError, ValueError):
            out[cle] = DEFAUTS[cle]
    out['jeton'] = bool(desk.settings(SECRET_KEY, '') or '')
    return out


def actif(desk, usage=''):
    """SearXNG activé, adresse renseignée et, si ``usage`` est donné (``assistant`` ou ``veille``), retenu pour cet usage. Lecture seule."""
    reg = reglages(desk)
    return bool(reg['enabled'] and reg['url'] and (not usage or reg.get(usage)))


def enregistrer(desk, data, acteur=''):
    """Écriture versionnée (409 si la page est périmée) ; jeton rangé au coffre ; instance inscrite au registre des prestataires."""
    data = data if isinstance(data, dict) else {}
    reg = reglages(desk)
    if str(data.get('version', '')).strip() != str(reg['version']):
        raise Stop('version_en_conflit')
    url = valider_url(data.get('url'))
    enabled = data.get('enabled') in VRAI
    if enabled and not url:
        raise Stop('searxng_adresse_requise')
    entete = str(data.get('entete') or 'Authorization').strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}', entete) or entete.lower() in ENTETES_INTERDITS or entete.lower().startswith('proxy-'):
        raise Stop('searxng_entete_invalide')
    langue = str(data.get('langue') or 'fr').strip()
    if not re.fullmatch(r'(?:[a-z]{2,3}(?:-[A-Za-z]{2})?|all|auto)', langue):
        raise Stop('searxng_reglage_invalide')
    try:
        nombres = {cle: int(str(data.get(cle, DEFAUTS[cle])).strip()) for cle in ('safesearch', 'plafond_horaire', 'pages_lues')}
    except ValueError:
        raise Stop('searxng_reglage_invalide') from None
    if not (0 <= nombres['safesearch'] <= 2 and 1 <= nombres['plafond_horaire'] <= PLAFOND_HORAIRE_MAX and PAGES_MIN <= nombres['pages_lues'] <= PAGES_MAX):
        raise Stop('searxng_reglage_invalide')
    nouveau = {'enabled': enabled, 'url': url, 'entete': entete, 'langue': langue, 'categories': _texte_liste(data.get('categories', 'general')),
               'moteurs': _texte_liste(data.get('moteurs', '')), **nombres, 'assistant': data.get('assistant', True) in VRAI,
               'veille': data.get('veille', True) in VRAI, 'version': reg['version'] + 1}
    jeton = str(data.get('jeton') or '').strip()
    if jeton:
        if len(jeton) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in jeton):
            raise Stop('searxng_jeton_invalide')
        from .vault567 import write
        desk.setting(SECRET_KEY, write(Path(desk.c['state_dir']) / 'vault' / 'searxng56106' / 'jeton.secret', jeton))
    elif data.get('effacer_jeton') in VRAI and reg['jeton']:
        try:
            Path(desk.settings(SECRET_KEY, '')).unlink()
        except OSError:
            pass
        desk.setting(SECRET_KEY, '')
    desk.setting(REGLAGES_KEY, nouveau)
    auto = inscrire_registre(desk, url, acteur) if url else False
    desk.audit('searxng56106_reglages', {'enabled': enabled, 'hote': hote_de(url), 'auto_heberge': auto, 'jeton': bool(jeton) or reg['jeton'] and data.get('effacer_jeton') not in VRAI,
                                         'version': nouveau['version'], 'owner': str(acteur or '')})
    out = reglages(desk)
    return {'reglages': out, 'auto_heberge': auto, 'message': 'Réglages SearXNG enregistrés.' + (' Instance auto-hébergée : admise au registre des prestataires.' if auto else
                                                                                                  (' Instance tierce : à qualifier au registre des prestataires.' if url else ''))}


def inscrire_registre(desk, base, acteur=''):
    """Fiche ``searxng`` du registre (``data_policy5689.prestataires5689``). Auto-hébergée : admise, publique, lieu de traitement du cabinet.
    Instance tierce : inscrite non admise (une fiche déjà qualifiée par l'administrateur pour le même hôte est laissée telle quelle)."""
    from . import data_policy5689 as dp
    hote, auto = hote_de(base), auto_heberge(desk, base)
    db = dp._ouvrir(desk.c['state_dir'])
    try:
        db.execute('BEGIN IMMEDIATE')
        ligne = db.execute('SELECT revision,hote,nature FROM prestataires5689 WHERE id=?', (ID,)).fetchone()
        if not auto and ligne is not None and ligne['hote'] == hote and ligne['nature'] != 'auto_heberge':
            db.commit()
            return False
        revision = (ligne['revision'] + 1) if ligne is not None else 1
        region = dp.REGION_CABINET if auto else None
        db.execute('INSERT OR REPLACE INTO prestataires5689(id,nom,hote,nature,public,admis,region_inference,region_stockage,region_sauvegarde,conservation,entrainement,'
                   'categories,finalites,preuve,revision,saisi_par,saisi_le) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ID, 'SearXNG (auto-hébergé)' if auto else 'SearXNG (instance tierce)', hote, 'auto_heberge' if auto else 'prestataire', 1, 1 if auto else 0,
                    region, region, None, 'Selon la configuration de l’instance du cabinet' if auto else None, None, '[]', json.dumps(['veille', 'recherche_publique']),
                    'Adresse locale ou du cabinet : instance auto-hébergée (5.6.106)' if auto else '', revision, dp._h(acteur), _now().isoformat()))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    return auto


def fiche_registre(desk):
    from . import data_policy5689 as dp
    ligne = dp._fiche(desk.c.get('state_dir'), ID)
    return dp.lisible(ligne) if ligne is not None else None


# ------------------------------------------------------------------------------------------------ politique, plafond horaire, journal
def politique(desk, requete, finalite, acteur=''):
    """Même décision commune que Tavily (outil ``recherche_web``), destination ``searxng`` : avant le compteur et le transfert."""
    from . import data_policy5689 as dp
    return dp.exiger(desk.c, 'recherche_web', ID, finalite=finalite, requete=str(requete)[:400], acteur=acteur)


def _cle_usage():
    return USAGE_PREFIXE + _now().strftime('%Y-%m-%dT%H')


def usage(desk):
    return {'appels': 0, 'incertains': 0, **(desk.settings(_cle_usage(), {}) or {})}


def reserver(desk, plafond, incertain=False):
    """Réservation atomique avant l'appel (plafond horaire) ; une coupure après envoi est comptée comme incertaine ; heures passées purgées."""
    cle = _cle_usage()
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row = desk.db.execute('SELECT value FROM settings WHERE key=?', (cle,)).fetchone()
        used = {'appels': 0, 'incertains': 0, **(json.loads(row[0]) if row else {})}
        if incertain:
            used['incertains'] += 1
        else:
            if used['appels'] >= int(plafond):
                raise Stop('plafond_searxng_atteint')
            used['appels'] += 1
        desk.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (cle, json.dumps(used)))
        desk.db.execute('DELETE FROM settings WHERE key LIKE ? AND key<?', (USAGE_PREFIXE + '%', cle))
        desk.db.commit()
    except BaseException:
        desk.db.rollback()
        raise
    return used


# ------------------------------------------------------------------------------------------------ client SearXNG
def _client(desk, reg, delai):
    """Client HTTP de l'instance : une adresse locale passe par le mode local de ``common.HTTP`` (HTTP admis vers cet hôte seulement)."""
    hote = hote_de(reg['url'])
    if local(hote):
        return HTTP(reg['url'], local_only=True, local_hosts=(hote,), timeout=delai)
    return HTTP(reg['url'], timeout=delai)


def preparer(desk, requete, *, profil='public', max_results=5, days=7, domaines=(), http=None, acteur='', forcer=False):
    """Réglage, politique des données, plafond horaire, adresse de la requête et en-têtes. Fil appelant seulement (aucun réseau)."""
    reg = reglages(desk)
    if not reg['url'] or not (reg['enabled'] or forcer):
        raise Stop('searxng_absent' if reg['url'] or not forcer else 'searxng_adresse_requise')
    if profil not in DELAI:
        raise Stop('profil_recherche_invalide')
    requete = ' '.join(str(requete or '').split())[:400]
    politique(desk, requete, {'veille': 'veille', 'public': 'recherche_publique'}.get(profil, 'test_connexion'), acteur)
    reserver(desk, reg['plafond_horaire'])
    params = [('q', requete), ('format', 'json'), ('language', reg['langue']), ('pageno', '1'), ('safesearch', str(reg['safesearch']))]
    categories = 'news' if profil == 'veille' else reg['categories']   # la veille garde son profil « actualités »
    if categories:
        params.append(('categories', categories))
    if reg['moteurs']:
        params.append(('engines', reg['moteurs']))
    if profil == 'veille':
        params.append(('time_range', 'day' if days <= 1 else ('month' if days <= 31 else 'year')))
    entetes = {'Accept': 'application/json'}
    chemin = desk.settings(SECRET_KEY, '') or ''
    if chemin:
        from .common import read_secret
        try:
            entetes[reg['entete']] = read_secret(chemin)
        except Stop:
            rp.journaliser(desk, profil, requete, 'erreur', 0, acteur, fournisseur=ID)
            raise Stop('searxng_jeton_illisible') from None
    client = http or _client(desk, reg, DELAI[profil])
    return {'requete': requete, 'profil': profil, 'acteur': acteur, 'plafond': reg['plafond_horaire'], 'url': reg['url'] + '/search?' + urlencode(params),
            'entetes': entetes, 'client': client, 'doms': rp.normaliser_domaines(domaines), 'max': max(1, min(10, int(max_results)))}


def envoyer(prep):
    """Seul appel réseau (peut tourner dans un autre fil) : ('ok', octets, type de contenu) ou ('stop', code, '')."""
    client = prep['client']
    try:
        raw = client.request('GET', prep['url'], None, prep['entetes'], REPONSE_MAX)
    except Stop as ex:
        return 'stop', str(ex), ''
    entetes = getattr(client, 'last_headers', None) or {}
    ctype = next((str(v) for k, v in entetes.items() if str(k).lower() == 'content-type'), '')
    return 'ok', raw, ctype


ERREURS_HTTP = {'http_401': 'searxng_jeton_refuse', 'http_403': 'searxng_json_desactive', 'http_429': 'searxng_limite_instance',
                'reponse_trop_volumineuse': 'searxng_reponse_trop_volumineuse', 'https_obligatoire': 'searxng_https_requis', 'url_invalide': 'searxng_adresse_invalide',
                'ollama_doit_etre_local': 'searxng_adresse_invalide', 'origine_http_refusee': 'searxng_adresse_invalide', 'destination_non_autorisee': 'destination_non_autorisee'}


def conclure(desk, prep, sortie):
    """Lecture de la réponse (fil appelant) : erreurs traduites, JSON exigé, hôtes filtrés comme pour Tavily, journal."""
    from .veille5632 import _clean
    profil, requete, acteur = prep['profil'], prep['requete'], prep['acteur']
    etat, raw, ctype = sortie
    if etat != 'ok':
        code = str(raw).split(':')[0]
        if code in ('delai_http_depasse', 'connexion_http_indisponible'):
            reserver(desk, prep['plafond'], incertain=True)
            rp.journaliser(desk, profil, requete, 'incertain', 0, acteur, fournisseur=ID)
            raise Stop('searxng_delai' if code == 'delai_http_depasse' else 'searxng_injoignable')
        rp.journaliser(desk, profil, requete, 'erreur', 0, acteur, fournisseur=ID)
        raise Stop(ERREURS_HTTP.get(code, 'searxng_indisponible'))
    if 'html' in ctype.lower() or bytes(raw or b'').lstrip()[:1] == b'<':
        rp.journaliser(desk, profil, requete, 'erreur', 0, acteur, fournisseur=ID)
        raise Stop('searxng_json_desactive')       # format JSON refusé derrière un mandataire qui rend la page HTML
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, TypeError):
        data = None
    rows = data.get('results') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        rp.journaliser(desk, profil, requete, 'erreur', 0, acteur, fournisseur=ID)
        raise Stop('searxng_reponse_illisible')
    prep['bruts'] = len(rows)
    out = []
    for r in rows[:RESULTATS_MAX]:
        if not isinstance(r, dict):
            continue
        url = str(r.get('url') or '')
        if len(url) > 2000 or not rp.hote_admis(url, prep['doms']):
            continue   # mêmes contrôles d'hôte que pour Tavily (HTTPS, ni IP ni hôte local, domaines restreints)
        moteurs = r.get('engines') if isinstance(r.get('engines'), list) else [r.get('engine')]
        try:
            score = float(r.get('score')) if r.get('score') is not None else None
        except (TypeError, ValueError):
            score = None
        out.append({'url': url, 'title': _clean(r.get('title'), 600), 'published': str(r.get('publishedDate') or '')[:40], 'summary': _clean(r.get('content'), 1500),
                    'score': score, 'moteurs': [str(x)[:40] for x in moteurs if x][:8]})
        if len(out) >= prep['max']:
            break
    rp.journaliser(desk, profil, requete, 'ok' if out else 'vide', len(out), acteur, fournisseur=ID)
    return out


def chercher(desk, requete, *, profil='public', max_results=5, days=7, domaines=(), http=None, acteur=''):
    """SearXNG seul (sans Tavily ni lecture des pages)."""
    prep = preparer(desk, requete, profil=profil, max_results=max_results, days=days, domaines=domaines, http=http, acteur=acteur)
    return conclure(desk, prep, envoyer(prep))


def tester(desk, acteur='', http=None):
    """Bouton « Tester » : vraie requête neutre (« test ») avec les réglages enregistrés, même désactivés ; politique, plafond et journal
    s'appliquent. Retour lisible : nombre de résultats rendus par l'instance, ou l'erreur expliquée."""
    debut = time.monotonic()
    try:
        prep = preparer(desk, REQUETE_TEST, profil='test', max_results=10, http=http, acteur=acteur, forcer=True)
        rows = conclure(desk, prep, envoyer(prep))
    except Stop as ex:
        code = str(ex).split(':')[0]
        return {'ok': False, 'motif': code, 'message': 'Échec du test SearXNG : ' + message(code)}
    return {'ok': True, 'resultats': prep.get('bruts', len(rows)), 'retenus': len(rows),
            'message': 'SearXNG répond : %d résultat(s) pour la requête neutre « %s » (%.1f s).' % (prep.get('bruts', len(rows)), REQUETE_TEST, time.monotonic() - debut)}


# ------------------------------------------------------------------------------------------------ fusion
def normaliser_url(url):
    """Adresse de comparaison : schéma et hôte en minuscules, port par défaut retiré, « / » final retiré, paramètres ``utm_*`` retirés,
    autres paramètres triés, fragment retiré."""
    try:
        p = urlsplit(str(url or '').strip())
        port = p.port
    except ValueError:
        return str(url or '')
    hote = (p.hostname or '').lower().rstrip('.')
    schema = p.scheme.lower()
    netloc = ('[%s]' % hote if ':' in hote else hote) + (':%d' % port if port and (schema, port) not in (('https', 443), ('http', 80)) else '')
    params = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_'))
    return urlunsplit((schema, netloc, p.path.rstrip('/') or '/', urlencode(params), ''))


def libelle_source(sources):
    return 'les deux' if len(set(sources)) > 1 else LIBELLES.get(sources[0], sources[0]) if sources else ''


def fusionner(listes):
    """``listes`` : {fournisseur: résultats ordonnés}. Dédoublonnage par adresse normalisée ; rang réciproque combiné
    (somme de 1 / (60 + rang)) ; à égalité, le meilleur rang puis l'ordre d'arrivée. Champs vides complétés par l'autre source."""
    table = {}
    for nom, rows in listes.items():
        for rang, r in enumerate(rows or (), 1):
            cle = normaliser_url(r.get('url'))
            e = table.get(cle)
            if e is None:
                e = table[cle] = {**r, 'sources': [], 'rrf': 0.0, 'meilleur_rang': rang}
            elif nom in e['sources']:
                continue                                   # doublon dans la même liste : seul le premier rang compte
            else:
                for champ in ('title', 'published'):
                    if not e.get(champ) and r.get(champ):
                        e[champ] = r[champ]
                if len(str(r.get('summary') or '')) > len(str(e.get('summary') or '')):
                    e['summary'] = r['summary']
            e['sources'].append(nom)
            e['rrf'] += 1.0 / (RRF_K + rang)
            e['meilleur_rang'] = min(e['meilleur_rang'], rang)
    out = sorted(table.values(), key=lambda x: (-x['rrf'], x['meilleur_rang']))
    for e in out:
        e['source'] = libelle_source(e['sources'])
        e['rrf'] = round(e['rrf'], 6)
    return out


# ------------------------------------------------------------------------------------------------ lecture des pages (anti-SSRF)
class _Connexion(http.client.HTTPSConnection):
    """Connexion TLS ouverte vers l'adresse DÉJÀ vérifiée (aucune nouvelle résolution DNS), certificat vérifié pour le nom demandé (SNI)."""

    def __init__(self, hote, adresse, delai):
        super().__init__(hote, 443, timeout=delai, context=ssl.create_default_context())
        self._adresse = adresse

    def connect(self):
        brut = socket.create_connection((self._adresse, 443), self.timeout)
        self.sock = self._context.wrap_socket(brut, server_hostname=self.host)


class _Garde:
    local_only = False


def _transport_page(adresse, url, delai, limite):
    """Transport réel d'une page : passerelle des appels sortants consultée, en-têtes neutres (ni cookie ni Referer), lecture bornée en taille
    et en durée. Retour : (statut, en-têtes en minuscules, octets, tronqué). Une redirection n'est jamais suivie ici."""
    from . import common
    if common.EGRESS_GUARD is not None:
        common.EGRESS_GUARD(_Garde(), 'GET', url, None)
    p = urlsplit(url)
    chemin = (p.path or '/') + ('?' + p.query if p.query else '')
    conn = _Connexion(p.hostname, adresse, delai)
    debut = time.monotonic()
    try:
        conn.request('GET', chemin, headers={'User-Agent': 'Mozilla/5.0 (compatible; AxiorHub-Pilote)', 'Accept': 'text/html,application/pdf,text/plain;q=0.8',
                                             'Accept-Encoding': 'identity', 'Accept-Language': 'fr,en;q=0.5'})
        rep = conn.getresponse()
        entetes = {k.lower(): v for k, v in rep.getheaders()}
        if 300 <= rep.status < 400:
            return rep.status, entetes, b'', False
        morceaux, total, tronque = [], 0, False
        while True:
            if time.monotonic() - debut > delai:
                tronque = True
                break
            bloc = rep.read(min(65536, limite + 1 - total))
            if not bloc:
                break
            morceaux.append(bloc)
            total += len(bloc)
            if total > limite:
                tronque = True
                break
        return rep.status, entetes, b''.join(morceaux)[:limite], tronque
    except (OSError, ssl.SSLError, http.client.HTTPException):
        raise Stop('page_injoignable') from None
    finally:
        conn.close()


def texte_page(corps, ctype=''):
    """Texte d'une page : PDF par l'extracteur existant, HTML réduit à son texte visible (scripts, styles et texte masqué écartés), texte
    brut ; caractères invisibles retirés ; une page qui contient des instructions adressées à une IA est écartée."""
    from .bouclier5659 import clean, inspect
    corps, ct = bytes(corps or b''), str(ctype or '').lower()
    if 'application/pdf' in ct or corps[:5] == b'%PDF-':
        from .documents import _pypdf_texts
        texte = '\n'.join(_pypdf_texts(corps))
    else:
        m = re.search(r'charset=([A-Za-z0-9_-]{1,40})', ct)
        try:
            brut = corps.decode(m.group(1) if m else 'utf-8', 'replace')
        except LookupError:
            brut = corps.decode('utf-8', 'replace')
        if 'html' in ct or brut.lstrip()[:1] == '<':
            from .mailbox import PlainHTML
            parseur = PlainHTML()
            parseur.feed(brut)
            parseur.close()
            texte = ''.join(parseur.parts)
        elif not ct or 'text/plain' in ct:
            texte = brut
        else:
            raise Stop('page_format_non_lu')
    texte = clean(texte)[0] or ''
    if inspect(texte)['level'] == 'eleve':
        raise Stop('page_instructions_ecartees')
    return ' '.join(texte.split())[:20000]


def lire_contenu(url, domaines=(), *, transport=None, resoudre=None, limite=PAGE_MAX, delai=PAGE_DELAI):
    """Lecture d'une page de résultat : à chaque étape (adresse d'origine puis chaque redirection, trois au plus), hôte contrôlé comme pour
    Tavily (HTTPS, domaines restreints, imitation refusée), résolution DNS contrôlée (toutes les adresses publiques), connexion à l'adresse
    vérifiée. Retour : {url, texte, tronque, redirections}."""
    transport, resoudre = transport or _transport_page, resoudre or rp._resoudre
    courant, sauts = str(url or ''), 0
    while True:
        if not rp.hote_admis(courant, domaines):
            raise Stop('adresse_hors_perimetre')
        adresses = list(resoudre(urlsplit(courant).hostname) or ())
        if not adresses:
            raise Stop('adresse_non_resolue')
        if not all(_publique(a) for a in adresses):
            raise Stop('adresse_privee_refusee')
        statut, entetes, corps, tronque = transport(adresses[0], courant, delai, limite)
        if statut in (301, 302, 303, 307, 308):
            if sauts >= REDIRECTIONS_MAX:
                raise Stop('trop_de_redirections')
            cible = str((entetes or {}).get('location') or '').strip()
            if not cible or len(cible) > 2000:
                raise Stop('redirection_illisible')
            courant, sauts = urljoin(courant, cible), sauts + 1
            continue
        if statut != 200:
            raise Stop('page_http_%d' % int(statut))
        return {'url': courant, 'texte': texte_page(corps, (entetes or {}).get('content-type', '')), 'tronque': bool(tronque), 'redirections': sauts}


def enrichir(resultats, domaines=(), nombre=PAGES_MIN, transport=None, resoudre=None):
    """Lecture parallèle des pages des ``nombre`` premiers résultats fournis par SearXNG (extraits seulement) ; durée totale bornée.
    Un échec garde l'extrait et le dit (``page_motif``) ; jamais d'erreur levée."""
    cibles = [r for r in resultats if 'searxng' in r.get('sources', ())][:max(0, min(PAGES_MAX, int(nombre)))]
    if not cibles:
        return resultats
    pool = ThreadPoolExecutor(max_workers=len(cibles), thread_name_prefix='pages56106')
    try:
        futurs = [(r, pool.submit(lire_contenu, r['url'], domaines, transport=transport, resoudre=resoudre)) for r in cibles]
        echeance = time.monotonic() + PAGES_DELAI_TOTAL
        for r, f in futurs:
            try:
                lu = f.result(timeout=max(0.05, echeance - time.monotonic()))
            except DelaiFutur:
                r.update(page_lue=False, page_motif='page_delai')
                continue
            except Stop as ex:
                r.update(page_lue=False, page_motif=str(ex).split(':')[0])
                continue
            except Exception:   # noqa: BLE001 — page illisible : l'extrait reste
                r.update(page_lue=False, page_motif='page_illisible')
                continue
            r.update(page_lue=True, page_tronquee=lu['tronque'])
            if len(lu['texte']) > len(str(r.get('summary') or '')):
                r['summary'] = lu['texte'][:1500]
    finally:
        pool.shutdown(wait=False)
    return resultats


# ------------------------------------------------------------------------------------------------ recherche combinée
def _etat(code):
    code = str(code).split(':')[0]
    statut = 'absent' if code in ABSENTS else 'plafond' if code in PLAFONDS else 'refuse' if code in rp.REFUS else 'erreur'
    return {'statut': statut, 'motif': code, 'n': 0}


def _limites(etats):
    ok = [n for n in ('tavily', 'searxng') if etats.get(n, {}).get('statut') == 'ok']
    out = []
    for n in ('tavily', 'searxng'):
        e = etats.get(n) or {}
        if e.get('statut') in (None, 'ok'):
            continue
        court = COURTS.get(e['motif'], 'indisponible')
        autre = [x for x in ok if x != n]
        out.append('%s : %s, résultats %s seulement.' % (LIBELLES[n], court, LIBELLES[autre[0]]) if autre else '%s : %s.' % (LIBELLES[n], court))
    return out


def _motif_global(etats):
    tentes = {n: e for n, e in etats.items() if e['statut'] != 'absent'}
    if not tentes:
        return etats.get('tavily', {}).get('motif') or 'searxng_absent'
    refus = [e['motif'] for e in tentes.values() if e['statut'] == 'refuse']
    if len(refus) == len(tentes):
        return refus[0]
    return next(iter(tentes.values()))['motif'] if len(tentes) == 1 else 'recherche_web_indisponible'


ENVOIS = {'tavily': rp.tavily_envoyer, 'searxng': envoyer}


def rechercher(desk, requete, *, profil='public', max_results=5, days=7, domaines=(), http=None, http_searxng=None, p=None, acteur='', lire_pages=True,
               transport=None, resoudre=None):
    """Tavily et SearXNG interrogés ensemble. Préparation (clé, politique, budgets) puis conclusion (journal, compteurs) dans le fil appelant ;
    seuls les deux appels réseau partent en parallèle, chacun avec son délai. Ne lève pas pour une panne de service : retour
    {ok, resultats, fournisseurs: {tavily, searxng}: {statut, motif, n}, limites, motif}."""
    requete = ' '.join(str(requete or '').split())[:400]
    doms = rp.normaliser_domaines(domaines)
    usage_ = 'veille' if profil == 'veille' else 'assistant'
    etats, preps, sorties, listes = {}, {}, {}, {}
    try:
        preps['tavily'] = rp.tavily_preparer(desk, requete, profil=profil, max_results=max_results, days=days, domaines=doms, http=http, p=p, acteur=acteur)
    except Stop as ex:
        etats['tavily'] = _etat(ex)
    if actif(desk, usage_):
        try:
            preps['searxng'] = preparer(desk, requete, profil=profil, max_results=max_results, days=days, domaines=doms, http=http_searxng, acteur=acteur)
        except Stop as ex:
            etats['searxng'] = _etat(ex)
    else:
        etats['searxng'] = _etat('searxng_absent')
    if len(preps) == 1:
        nom = next(iter(preps))
        sorties[nom] = ENVOIS[nom](preps[nom])
    elif preps:
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='recherche56106')
        try:
            futurs = {nom: pool.submit(ENVOIS[nom], prep) for nom, prep in preps.items()}
            for nom, f in futurs.items():
                try:
                    sorties[nom] = f.result(timeout=max(DELAI.values()) + 25)
                except DelaiFutur:
                    sorties[nom] = ('stop', 'delai_http_depasse', '') if nom == 'searxng' else ('stop', 'delai_http_depasse')
        finally:
            pool.shutdown(wait=False)
    for nom, conclure_ in (('tavily', rp.tavily_conclure), ('searxng', conclure)):
        if nom not in preps:
            continue
        try:
            listes[nom] = conclure_(desk, preps[nom], sorties[nom])
            etats[nom] = {'statut': 'ok', 'motif': '', 'n': len(listes[nom])}
        except Stop as ex:
            etats[nom] = _etat(ex)
    resultats = fusionner(listes)[:max(1, min(10, int(max_results)))]
    if lire_pages and 'searxng' in listes:
        enrichir(resultats, doms, reglages(desk)['pages_lues'], transport=transport, resoudre=resoudre)
    ok = bool(listes)
    return {'ok': ok, 'resultats': resultats, 'fournisseurs': etats, 'limites': _limites(etats), 'motif': '' if ok else _motif_global(etats),
            'interroges': [LIBELLES[n] for n in ('tavily', 'searxng') if n in listes]}


# ------------------------------------------------------------------------------------------------ Paramètres › Recherche juridique et veille
def section_html(desk):
    """Bloc d'administration de SearXNG (formulaires ``data-m5632`` : envoi en JSON avec le jeton CSRF ; aucun script en ligne)."""
    from html import escape as e
    from .web5632 import _form
    reg, used, fiche = reglages(desk), usage(desk), fiche_registre(desk)

    def coche(cle):
        return ' checked' if reg[cle] else ''
    options = ''.join('<option value="%d"%s>%s</option>' % (v, ' selected' if reg['safesearch'] == v else '', t) for v, t in ((0, 'Aucun'), (1, 'Modéré'), (2, 'Strict')))
    corps = ('<input type="hidden" name="version" value="%d">' % reg['version'] +
             '<label class="f5632-check"><input type="checkbox" name="enabled"%s> Activer SearXNG (interrogé avec Tavily, résultats fusionnés)</label>' % coche('enabled') +
             '<label>Adresse de l’instance<input name="url" value="%s" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://recherche.cabinet.example.test"></label>'
             % e(reg['url'], quote=True) +
             '<p class="f5632-hint">HTTPS, ou HTTP seulement vers une adresse du réseau local (localhost, 192.168…, nom en .lan ou .local).</p>'
             '<div class="f5632-row"><label>En-tête d’authentification (facultatif)<input name="entete" value="%s" autocomplete="off" spellcheck="false"></label>'
             '<label>Jeton ou valeur de l’en-tête (rangé dans le coffre)<input type="password" name="jeton" autocomplete="off" placeholder="%s"></label></div>'
             % (e(reg['entete'], quote=True), 'Enregistré : laisser vide pour le garder' if reg['jeton'] else 'Facultatif (par exemple « Bearer … »)') +
             ('<label class="f5632-check"><input type="checkbox" name="effacer_jeton"> Retirer le jeton enregistré</label>' if reg['jeton'] else '') +
             '<div class="f5632-row"><label>Langue<input name="langue" value="%s" spellcheck="false"></label><label>Catégories<input name="categories" value="%s" spellcheck="false"></label>'
             '<label>Moteurs (facultatif)<input name="moteurs" value="%s" spellcheck="false" placeholder="ex. : wikipedia, qwant"></label>'
             '<label>Filtrage (safesearch)<select name="safesearch">%s</select></label></div>'
             % (e(reg['langue'], quote=True), e(reg['categories'], quote=True), e(reg['moteurs'], quote=True), options) +
             '<div class="f5632-row"><label>Plafond horaire de requêtes<input type="number" name="plafond_horaire" min="1" max="%d" value="%d"></label>'
             '<label>Pages lues par recherche<input type="number" name="pages_lues" min="%d" max="%d" value="%d"></label></div>'
             % (PLAFOND_HORAIRE_MAX, reg['plafond_horaire'], PAGES_MIN, PAGES_MAX, reg['pages_lues']) +
             '<fieldset><legend>Utilisé pour</legend><label class="f5632-check"><input type="checkbox" name="assistant"%s> Recherche publique de l’assistant</label>'
             '<label class="f5632-check"><input type="checkbox" name="veille"%s> Veille (case « Recherche Internet » du profil de veille)</label></fieldset>'
             % (coche('assistant'), coche('veille')))
    if fiche is None:
        registre = 'Registre des prestataires : SearXNG y sera inscrit à l’enregistrement de l’adresse.'
    elif fiche['nature'] == 'auto_heberge' and fiche['admis']:
        registre = 'Registre des prestataires : SearXNG auto-hébergé (%s), admis par défaut ; lieu de traitement : serveur du cabinet.' % e(fiche['hote'] or '')
    else:
        registre = ('Registre des prestataires : instance tierce (%s), %s. Qualifiez-la dans la politique des données avant tout usage quand la politique est expresse.'
                    % (e(fiche['hote'] or ''), 'admise' if fiche['admis'] else 'non admise'))
    aide = ('<p class="f5632-hint">L’API JSON doit être activée sur l’instance : dans <code>settings.yml</code>, <code>search: formats: [html, json]</code>, puis redémarrer '
            'SearXNG. Sans elle, l’instance répond 403 (ou une page HTML) et le test le dit. Documentation officielle : '
            '<a href="https://docs.searxng.org/dev/search_api.html" target="_blank" rel="noopener noreferrer">API de recherche de SearXNG</a>.</p>'
            '<p class="f5632-hint">Chaque recherche interroge Tavily et SearXNG puis fusionne les résultats ; si l’un manque, l’autre suffit et la réponse le dit. '
            'La requête suit les règles de la recherche publique (aucun nom de client, de partie ni numéro de dossier) ; elle est journalisée sans son texte. '
            'Pour les premiers résultats de SearXNG, AxiorHub lit la page lui-même (HTTPS public seulement, redirections revérifiées, taille et durée bornées).</p>')
    compteur = '<p class="f5632-hint">%d requête(s) SearXNG cette heure-ci sur %d (dont %d incertaine(s)).</p>' % (used['appels'], reg['plafond_horaire'], used['incertains'])
    return ('<h3>SearXNG (instance du cabinet)</h3>' + aide + '<p class="f5632-hint">%s</p>' % registre + compteur +
            _form('admin/searxng', corps, 'Enregistrer SearXNG', reload=True) +
            _form('admin/searxng/test', '', 'Tester (vraie requête neutre)'))

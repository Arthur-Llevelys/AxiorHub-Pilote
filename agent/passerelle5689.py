"""5.6.89 : passerelle des appels sortants (chapitre 42 du cahier).

Ce qui est imposé en pratique, et rien de plus :

- ``common.HTTP`` : toute requête non locale passe par ``garde_http`` dès qu'un processus de service a appelé ``installer`` ; le contrôle est
  dans le code réseau, pas dans une consigne au modèle ;
- URL vérifiée : https, sans identifiant ni fragment, hôte résolu ; une adresse privée, de bouclage ou de lien local n'est admise que pour un
  service configuré par le cabinet ; l'adresse de métadonnées des hébergeurs ne l'est jamais ;
- redirections : ``common.HTTP`` ne les suit pas (``NoRedirect``) ; ``verifier_redirection`` contrôle la cible si un service décide d'en suivre une ;
- en mode local strict choisi expressément : seules passent les destinations du cabinet, celles marquées publiques au registre, et la mise à jour ;
- chaque hôte observé est compté par jour, sans chemin, sans paramètre, sans contenu.

``chatgpt5662`` (abonnement) a son propre transport : il est couvert par ``data_policy5689.garde_modele`` avant l'envoi, pas par cette garde.
Le navigateur supervisé et l'interface de Nextcloud agissent sous le contrôle d'une personne et ne passent pas par ce module.

Extensions : voir ``limites_extensions``. Aucun cloisonnement de processus n'existe ; il n'est pas promis.
"""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import socket
import sqlite3
import threading
import time
import urllib.parse

from .common import Stop

METADONNEES = frozenset(('169.254.169.254', 'fd00:ec2::254', 'metadata.google.internal'))
MISE_A_JOUR = frozenset(('github.com', 'api.github.com', 'objects.githubusercontent.com', 'release-assets.githubusercontent.com', 'codeload.github.com'))
CACHE_SECONDES = 5
GARDER_JOURS = 400
SCHEMA = '''CREATE TABLE IF NOT EXISTS flux5689(jour TEXT NOT NULL, hote TEXT NOT NULL, categorie TEXT NOT NULL, decision TEXT NOT NULL, nombre INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(jour,hote,categorie,decision));'''
_etat = {'config': None}
_cache = {}
_verrou = threading.Lock()


def limites_extensions():
    """Texte affiché tel quel : ce qui est imposé aux extensions, et ce qui ne l'est pas."""
    return ['Aucun code d’extension n’est exécuté : une archive de compétence ou de plugin est inspectée, ses fichiers exécutables sont refusés, ses scripts ne sont jamais lancés, seuls ses textes déclaratifs sont conservés.',
            'Une extension ne reçoit donc ni shell, ni accès global au stockage, ni droit d’administration : elle n’a aucun processus qui pourrait les recevoir.',
            'Un connecteur MCP est un service distant : AxiorHub l’appelle avec une URL vérifiée et ne lui transmet que les requêtes admises par la politique.',
            'Il n’existe pas de cloisonnement de processus pour du code tiers, puisqu’aucun code tiers n’est exécuté. Si un tel code était installé à la main sur le serveur, rien dans AxiorHub ne le limiterait.',
            'Une consigne donnée au modèle n’est pas une barrière : seuls les contrôles côté serveur en sont.']


def _hote_norme(url):
    parts = urllib.parse.urlsplit(str(url or ''))
    return (parts.hostname or '').rstrip('.').lower(), parts


def services_prives(config):
    """Services privés que le cabinet a configurés. Une adresse lue dans un document ou renvoyée par un service distant n'y entre jamais."""
    config = config or {}
    hotes = set(_admis((config.get('state_dir') or ''))[1]) if config.get('state_dir') else set()
    for url in ((config.get('nextcloud') or {}).get('url'), (config.get('ollama') or {}).get('url'), (config.get('rag') or {}).get('qdrant_url')):
        if url:
            hotes.add(_hote_norme(url)[0])
    for item in config.get('services_prives') or ():
        hotes.add(str(item).strip().lower())
    return hotes - {''}


def _adresses(hote, resolveur):
    try:
        return {info[4][0] for info in resolveur(hote, 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise Stop('destination_introuvable') from None


def _interdite(adresse):
    ip = ipaddress.ip_address(str(adresse).split('%')[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def verifier_url(config, url, resolveur=socket.getaddrinfo):
    """Contrôle d'une URL sortante avant tout envoi : ne rend rien, lève ``Stop``. Vérifie l'adresse réellement résolue, pas seulement le nom."""
    hote, parts = _hote_norme(url)
    if not hote or parts.username or parts.password or parts.fragment:
        raise Stop('url_sortante_invalide')
    if hote in METADONNEES:
        raise Stop('destination_interdite')
    configure = hote in services_prives(config)
    if parts.scheme != 'https' and not configure:
        raise Stop('https_obligatoire')
    if configure:
        return
    try:
        adresses = {str(ipaddress.ip_address(hote))}
    except ValueError:
        adresses = _adresses(hote, resolveur)
    if any(a in METADONNEES or _interdite(a) for a in adresses):
        raise Stop('service_prive_non_configure')


def verifier_redirection(config, origine, cible, resolveur=socket.getaddrinfo):
    """Une redirection est une nouvelle destination : mêmes contrôles, pas d'abaissement vers http, autre hôte seulement s'il est lui-même admis."""
    absolue = urllib.parse.urljoin(str(origine), str(cible))
    verifier_url(config, absolue, resolveur)
    if urllib.parse.urlsplit(str(origine)).scheme == 'https' and urllib.parse.urlsplit(absolue).scheme != 'https':
        raise Stop('redirection_refusee')
    if _hote_norme(absolue)[0] != _hote_norme(origine)[0]:
        from .data_policy5689 import _fiche, charger
        etat = charger((config or {}).get('state_dir'))
        fiche = _fiche((config or {}).get('state_dir'), _hote_norme(absolue)[0])
        if etat['explicite'] and (fiche is None or not fiche['admis']):
            raise Stop('redirection_refusee')
    return absolue


def _admis(state_dir):
    """(sources publiques, services du cabinet) admis au registre ; relus au plus toutes les 5 secondes : la garde est sur le chemin de chaque requête sortante."""
    now = time.monotonic()
    if not (Path(str(state_dir)) / 'desk.sqlite3').is_file():
        return set(), set()
    with _verrou:
        memo = _cache.get(state_dir)
        if memo and now - memo[0] < CACHE_SECONDES:
            return memo[1]
    from .data_policy5689 import _ouvrir
    hotes = (set(), set())
    try:
        db = _ouvrir(state_dir)
        try:
            lignes = db.execute("SELECT hote,public,nature FROM prestataires5689 WHERE admis=1 AND hote<>''").fetchall()
            hotes = ({r['hote'] for r in lignes if r['public']}, {r['hote'] for r in lignes if r['nature'] == 'service_cabinet'})
        finally:
            db.close()
    except sqlite3.Error:
        pass
    with _verrou:
        _cache[state_dir] = (now, hotes)
    return hotes


def compter(state_dir, hote, categorie, decision):
    """Destinations observées : un hôte, une catégorie, une décision, un total par jour. Jamais de chemin ni de paramètre."""
    if not state_dir or not (Path(state_dir) / 'desk.sqlite3').is_file():
        return
    jour = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        db = sqlite3.connect(Path(state_dir) / 'desk.sqlite3', timeout=3)
        try:
            db.executescript(SCHEMA)
            db.execute('INSERT INTO flux5689 VALUES(?,?,?,?,1) ON CONFLICT(jour,hote,categorie,decision) DO UPDATE SET nombre=nombre+1', (jour, hote[:200], categorie, decision))
            db.execute('DELETE FROM flux5689 WHERE jour<?', ((datetime.now(timezone.utc) - timedelta(days=GARDER_JOURS)).strftime('%Y-%m-%d'),))
            db.commit()
        finally:
            db.close()
    except sqlite3.Error:
        pass


def garde_http(client, methode, url, corps=None):
    """Point de passage de ``common.HTTP`` pour tout hôte non local. Une politique illisible bloque ; en local strict choisi expressément, liste fermée."""
    config = _etat['config']
    if not config or getattr(client, 'local_only', False):
        return
    state_dir = config.get('state_dir')
    hote = _hote_norme(url)[0]
    categorie = 'service_cabinet' if hote in services_prives(config) else 'externe'
    from .data_policy5689 import PolitiqueIllisible, charger
    try:
        etat = charger(state_dir)
    except PolitiqueIllisible:
        if categorie == 'service_cabinet':
            return                                   # le stockage du cabinet reste joignable : la barrière porte sur ce qui quitte le cabinet
        compter(state_dir, hote, categorie, 'refus')
        raise
    if etat['mode'] == 'local_strict' and etat['explicite'] and categorie == 'externe':
        mise_a_jour = hote in MISE_A_JOUR and str(methode).upper() in ('GET', 'HEAD') and not corps
        if not mise_a_jour and hote not in _admis(state_dir)[0]:
            compter(state_dir, hote, 'local_strict', 'refus')
            raise Stop('destination_non_autorisee')
        categorie = 'mise_a_jour' if mise_a_jour else 'source_publique'
    compter(state_dir, hote, categorie, 'admis')


def installer(config):
    """À l'ouverture d'un processus de service. Sans cet appel (outils en ligne de commande, essais), ``common.HTTP`` se comporte comme avant."""
    from . import common
    _etat['config'] = config
    common.EGRESS_GUARD = garde_http


def retirer():
    from . import common
    _etat['config'] = None
    common.EGRESS_GUARD = None


def flux(desk, jours=30):
    """Destinations observées, catégories et décisions, sans donnée confidentielle."""
    limite = (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(jours), 365)))).strftime('%Y-%m-%d')
    desk.db.executescript(SCHEMA)
    return [dict(r) for r in desk.db.execute('SELECT hote,categorie,decision,SUM(nombre) nombre,MAX(jour) dernier FROM flux5689 WHERE jour>=? GROUP BY hote,categorie,decision ORDER BY nombre DESC LIMIT 200', (limite,))]


def resume_json(lignes):
    return json.dumps(lignes, ensure_ascii=False, sort_keys=True)

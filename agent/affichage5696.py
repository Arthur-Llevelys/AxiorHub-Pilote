"""5.6.96 (lot affichage) : services communs de l'interface.

- U05 : ``Server-Timing`` agrégé par requête de page : authentification, SQLite (durée et nombre de requêtes), construction de la page,
  total. Des nombres seulement : jamais de titre de dossier, de filtre, d'adresse ni de texte de requête. Les compteurs vivent dans le fil
  d'exécution de la requête et sont remis à zéro à chaque requête (``demarrer``) ; hors requête mesurée, rien n'est compté.
- U03 : police choisie par la personne (``tableau5688``), lue une fois puis gardée en mémoire du processus ; l'enregistrement d'un nouveau
  choix efface la valeur gardée (``oublier_police``). Par défaut : Figtree, sans attribut sur la page.
- U07 : ``compte_courant`` et ``empreinte_compte`` : repère court du compte (jamais l'identifiant), lu par ``v5689-navigation.js`` pour ne
  revenir en arrière par l'historique que vers une liste ouverte par le même compte.
"""
import hashlib
import json
import re
import sqlite3
import threading
import time
from html import escape
from pathlib import Path

_chrono = threading.local()
NOMS = ('auth', 'sqlite', 'blocs')


# ------------------------------------------------------------------------------------------------ U05 : mesure du retour
def demarrer():
    """Nouvelle requête : compteurs à zéro (un fil d'exécution n'hérite jamais des valeurs de la requête précédente)."""
    _chrono.valeurs = {}
    _chrono.requetes = 0
    _chrono.debut = time.perf_counter()
    _chrono.memo = {}


def memo_requete(cle, calcul):
    """U06 : un calcul demandé deux fois par la même page (ex. points de santé : en-tête et panneau d'Aujourd'hui) n'est fait qu'une fois.
    Mémoire limitée à l'affichage d'une page (requête GET en cours) ; une action (POST), le travailleur ou un test hors requête refont toujours
    le calcul : rien de ce qui vient d'être décidé n'est lu dans une valeur gardée."""
    memo = getattr(_chrono, 'memo', None)
    from .common import _TRACKED
    if memo is None or getattr(_chrono, 'valeurs', None) is None or getattr(_TRACKED, 'mode', '') != 'read':   # affichage (GET) seulement : une action relit tout
        return calcul()
    if cle not in memo:
        memo[cle] = calcul()
    return memo[cle]


def actif():
    return getattr(_chrono, 'valeurs', None) is not None


def ajouter(nom, secondes):
    valeurs = getattr(_chrono, 'valeurs', None)
    if valeurs is not None and nom in NOMS:
        valeurs[nom] = valeurs.get(nom, 0.0) + max(0.0, secondes)


def requete_sqlite(secondes):
    """Appelé par la connexion du bureau (``ecritures5636.TimedConnection``) après chaque requête SQL ; sans mesure ouverte, rien."""
    if getattr(_chrono, 'valeurs', None) is not None:
        _chrono.valeurs['sqlite'] = _chrono.valeurs.get('sqlite', 0.0) + max(0.0, secondes)
        _chrono.requetes += 1


def marquer_auth():
    """Fin de l'authentification et de la préparation de la requête : le temps écoulé depuis ``demarrer`` est compté une fois."""
    if actif() and 'auth' not in _chrono.valeurs:
        _chrono.valeurs['auth'] = time.perf_counter() - _chrono.debut
        _chrono.apres_auth = time.perf_counter()


def entete():
    """Valeur de ``Server-Timing`` ou ``''`` hors mesure : ``auth;dur=3.1, sqlite;dur=20.5;desc="42", blocs;dur=60.0, total;dur=80.2``.
    ``blocs`` : temps écoulé après l'authentification (construction de la page, SQLite compris)."""
    valeurs = getattr(_chrono, 'valeurs', None)
    if valeurs is None or 'auth' not in valeurs:   # requête non authentifiée (connexion, fichier public) : rien à publier
        return ''
    fin = time.perf_counter()
    blocs = fin - _chrono.apres_auth
    parts = ['auth;dur=%.1f' % (valeurs.get('auth', 0.0) * 1000), 'sqlite;dur=%.1f;desc="%d"' % (valeurs.get('sqlite', 0.0) * 1000, _chrono.requetes),
             'blocs;dur=%.1f' % (blocs * 1000), 'total;dur=%.1f' % ((fin - _chrono.debut) * 1000)]
    return ', '.join(parts)


def terminer():
    _chrono.valeurs = None
    _chrono.apres_auth = None
    _chrono.memo = None


# ------------------------------------------------------------------------------------------------ compte de la requête
def compte_courant():
    """Compte de la requête en cours (``i18n5631.begin`` le pose pour chaque requête authentifiée) ; ``cabinet`` hors requête."""
    from .i18n5631 import _ctx
    return getattr(_ctx, 'owner', '') or 'cabinet'


def empreinte_compte(owner):
    """Repère court et stable du compte : distingue deux comptes sur le même poste, sans écrire l'identifiant dans la page."""
    return hashlib.sha256(('axiorhub-compte:' + str(owner or 'cabinet')).encode('utf-8')).hexdigest()[:12]


# ------------------------------------------------------------------------------------------------ U03 : police par compte
_POLICES_GARDEES = {}
_VERROU = threading.Lock()
GARDE_S = 300


def oublier_police(state_dir, owner):
    with _VERROU:
        _POLICES_GARDEES.pop((str(state_dir), str(owner or 'cabinet')), None)


def police_compte(cfg, owner):
    """Police choisie par la personne : ``figtree`` (défaut), ``plex`` ou ``systeme``. Lecture seule de ``tableau5688`` sans ouvrir de bureau
    (aucune préparation de schéma), gardée cinq minutes ; une base illisible ou occupée rend la police par défaut, jamais une erreur."""
    from .ui_tokens5689 import POLICES, POLICE_DEFAUT
    state = str((cfg or {}).get('state_dir') or '')
    cle = (state, str(owner or 'cabinet'))
    with _VERROU:
        garde = _POLICES_GARDEES.get(cle)
    if garde and garde[1] > time.monotonic():
        return garde[0]
    valeur = POLICE_DEFAUT
    db = Path(state) / 'desk.sqlite3' if state else None
    if db is not None and db.is_file():
        try:
            con = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True, timeout=0.5)
            try:
                row = con.execute('SELECT data FROM tableau5688 WHERE owner=?', (cle[1],)).fetchone()
            finally:
                con.close()
            choisie = (json.loads(row[0]) if row else {}).get('police')
            valeur = choisie if choisie in POLICES else POLICE_DEFAUT
        except (sqlite3.Error, ValueError, TypeError, AttributeError):
            valeur = POLICE_DEFAUT
    with _VERROU:
        if len(_POLICES_GARDEES) > 500:
            _POLICES_GARDEES.clear()
        _POLICES_GARDEES[cle] = (valeur, time.monotonic() + GARDE_S)
    return valeur


def marquer_police(html, police):
    """Pose ``data-police`` sur ``<html>`` pour une variante ; la police par défaut ne laisse aucune trace dans la page."""
    from .ui_tokens5689 import POLICES, POLICE_DEFAUT
    if police == POLICE_DEFAUT or police not in POLICES or 'data-police=' in html[:600]:
        return html
    return re.sub(r'<html(?=[\s>])', '<html data-police="%s"' % escape(police, quote=True), html, count=1)

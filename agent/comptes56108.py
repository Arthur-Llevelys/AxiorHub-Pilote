"""5.6.108 (lot C, B05 et identité des travaux différés) : le compte qui a demandé un effet différé est revérifié juste avant l'effet.

Défaut corrigé (audit 5.6.107, B05) : un message programmé par un compte ensuite désactivé partait quand même, avec le rôle conservé dans
la file au moment de la programmation. Désormais l'identité est relue dans l'**annuaire autoritatif du mode d'installation**, en lecture
seule, juste avant l'effet : compte actif, rôle actuel ; le droit précis et l'accès au dossier sont contrôlés ensuite par l'appelant avec
ce rôle actuel (jamais avec le rôle mémorisé).

Annuaires (le profil vient toujours du serveur, jamais du formulaire ni de la file) :
  - ``installation`` : l'identité ``cabinet`` (administrateur du poste ou de la version serveur ; elle ne se désactive pas) ;
  - ``serveur`` : comptes de la version serveur (``comptes5648.sqlite3`` du dossier d'état, colonne ``ident``) ;
  - ``autonome`` : comptes de la distribution autonome (``users.sqlite3`` de ``auth_root()``, colonne ``email``) ;
  - ``session`` : identité tenue par Nextcloud ou l'ExApp, qu'AxiorHub ne peut pas relire hors d'une requête. Elle ne vaut que pendant une
    courte fenêtre après un geste explicite de la personne dans sa session (``FENETRE_SESSION``) ; au-delà, l'effet est retenu.
Annuaire absent, illisible, compte inconnu, désactivé ou profil changé depuis l'enfilage : ``Stop`` avec un motif lisible, aucun effet.
Le motif est montré à la personne ; la demande reste en place pour une reprise explicite (jamais de substitution d'identité).

Fonctions réutilisables (point de montage des autres lots) :
  - ``noter_auteur(desk, owner, role)`` : à l'enfilage, auteur fiable à conserver avec le travail ;
  - ``verifier_auteur(desk, job, ...)`` : au démarrage du travail puis avant chaque effet ; rend le compte actuel ou lève ``Stop``.
"""
from pathlib import Path
import json
import sqlite3
import time

from .common import Stop

FENETRE_SESSION = 90            # secondes : délai d'annulation maximal (30 s) et marge du travailleur
ROLES_EFFET = ('administrateur', 'avocat')
ROLES_CONNUS = ('administrateur', 'avocat', 'assistant')
PROFILS = ('installation', 'serveur', 'autonome', 'session')
CLE = '_auteur56108'            # clé des arguments d'un travail où l'auteur noté à l'enfilage est conservé
ANNUAIRE_SERVEUR = 'comptes5648.sqlite3'

MESSAGES = {
    'auteur_non_verifiable': 'Travail sans auteur vérifiable : il est retenu, jamais exécuté au nom du cabinet.',
    'compte_desactive': 'Le compte qui a demandé cette action a été désactivé depuis : rien n’a été fait.',
    'compte_introuvable': 'Le compte qui a demandé cette action n’existe plus dans l’annuaire des comptes : rien n’a été fait.',
    'annuaire_illisible': 'L’annuaire des comptes est illisible : par prudence, rien n’a été fait. Réessayez quand il est de nouveau lisible.',
    'compte_non_verifiable': 'Ce compte (Nextcloud) ne peut pas être revérifié par AxiorHub au moment de l’action : rien n’a été fait. '
                             'Confirmez de nouveau depuis votre session.',
    'role_retire': 'Le rôle actuel de ce compte ne permet plus cette action : rien n’a été fait.',
}
CODES = frozenset(MESSAGES)


def message(code):
    return MESSAGES.get(code, code)


# ------------------------------------------------------------------------------------------------ lecture des annuaires (lecture seule)
def _lire(chemin, requete, valeurs):
    """Une ligne d'un annuaire SQLite ouvert en lecture seule (``mode=ro`` : un annuaire absent n'est jamais créé)."""
    p = Path(chemin)
    if not p.is_file():
        raise Stop('annuaire_illisible')
    try:
        db = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            return db.execute(requete, valeurs).fetchone()
        finally:
            db.close()
    except (OSError, sqlite3.Error, ValueError):
        raise Stop('annuaire_illisible') from None


def _autonome():
    from .comptes5642 import standalone
    return standalone()


def _annuaire_autonome():
    from .missions567 import auth_root
    return auth_root() / 'users.sqlite3'


def _annuaire_serveur(desk):
    return Path(str(desk.c.get('state_dir') or '')) / ANNUAIRE_SERVEUR


def profil(desk, owner):
    """Annuaire autoritatif de ce compte d'après le mode d'installation. ``Stop('annuaire_illisible')`` si l'annuaire existe mais ne se lit pas."""
    owner = str(owner or '')
    if not owner:
        return ''
    if _autonome():
        return 'autonome'          # distribution autonome : chaque personne a son adresse ; « cabinet » n'y est pas une personne
    if owner == 'cabinet':
        return 'installation'
    chemin = _annuaire_serveur(desk)
    if desk.c.get('state_dir') and chemin.is_file() and _lire(chemin, 'SELECT 1 FROM users WHERE ident=?', (owner,)):
        return 'serveur'
    return 'session'


def compte_actuel(desk, owner, attendu=None):
    """Compte relu maintenant : ``{'owner', 'role', 'profil'}`` (rôle ``None`` pour le profil ``session``, sans annuaire local).
    ``attendu`` : profil noté à l'enfilage ; un annuaire local qui ne connaît plus le compte ne se remplace jamais par la session."""
    owner = str(owner or '')
    if not owner:
        raise Stop('auteur_non_verifiable')
    actuel = profil(desk, owner)
    if attendu in ('installation', 'serveur', 'autonome') and actuel != attendu:
        raise Stop('compte_introuvable')
    if actuel == 'installation':
        return {'owner': owner, 'role': 'administrateur', 'profil': actuel}
    if actuel == 'session':
        return {'owner': owner, 'role': None, 'profil': actuel}
    if actuel == 'serveur':
        row = _lire(_annuaire_serveur(desk), 'SELECT role, active FROM users WHERE ident=?', (owner,))
    else:
        row = _lire(_annuaire_autonome(), 'SELECT role, active FROM users WHERE email=?', (owner,))
    if not row:
        raise Stop('compte_introuvable')
    if row[1] != 1:
        raise Stop('compte_desactive')
    if row[0] not in ROLES_CONNUS:
        raise Stop('role_retire')
    return {'owner': owner, 'role': row[0], 'profil': actuel}


# ------------------------------------------------------------------------------------------------ auteur noté puis revérifié
def noter_auteur(desk, owner, role, maintenant=None):
    """À l'enfilage (requête de la personne) : auteur à conserver avec le travail. Le rôle n'est gardé que comme preuve de la session pour
    le profil ``session`` ; un annuaire local fait toujours foi au moment de l'effet. Annuaire illisible : profil vide (retenu plus tard)."""
    maintenant = time.time() if maintenant is None else float(maintenant)
    try:
        p = profil(desk, owner)
    except Stop:
        p = ''
    return {'owner': str(owner or ''), 'role': str(role or ''), 'profil': p, 'vu': maintenant}


def _auteur_de(job):
    """Auteur noté d'un travail : ligne de la table ``jobs`` (arguments JSON), arguments, ou auteur lui-même. Un ancien travail qui ne
    porte qu'un propriétaire (``owner`` / ``_owner``) est reconnu sans profil ni preuve de session ; sans propriétaire : ``{}``."""
    if job is None:
        return {}
    try:
        data = dict(job)
    except (TypeError, ValueError):
        return {}
    if 'args' in data and isinstance(data.get('args'), (str, bytes)):
        try:
            data = json.loads(data['args'] or '{}')
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
    elif isinstance(data.get('args'), dict):
        data = data['args']
    auteur = data.get(CLE) if isinstance(data.get(CLE), dict) else None
    if auteur is None and 'owner' in data and 'vu' in data and 'profil' in data:
        auteur = data
    if auteur is not None:
        return {'owner': str(auteur.get('owner') or ''), 'role': str(auteur.get('role') or ''), 'profil': str(auteur.get('profil') or ''),
                'vu': auteur.get('vu')}
    owner = data.get('owner') or data.get('_owner')
    return {'owner': str(owner), 'role': '', 'profil': '', 'vu': None} if owner else {}


def verifier_auteur(desk, job, roles=ROLES_EFFET, methode=None, chemin=None, dossier=None, fenetre=FENETRE_SESSION, maintenant=None):
    """Compte de l'auteur d'un travail différé, relu MAINTENANT (au démarrage du travail puis juste avant chaque effet).

    ``job`` : ligne ``jobs``, arguments, ou auteur noté par ``noter_auteur``. Contrôles, dans l'ordre : auteur présent ; compte actif dans
    l'annuaire du mode d'installation (ou, pour une identité Nextcloud, geste de la personne il y a moins de ``fenetre`` secondes) ; rôle
    actuel dans ``roles`` ; droit réglable du compte pour ``methode`` + ``chemin`` (``membres5650.allowed``) ; dossier non réservé à
    d'autres. Rend ``{'owner', 'role', 'profil', 'masques'}`` ; sinon ``Stop`` (``auteur_non_verifiable``, ``compte_desactive``,
    ``compte_introuvable``, ``annuaire_illisible``, ``compte_non_verifiable``, ``role_retire``, ``droit_retire``, ``dossier_reserve``).
    Aucune écriture : l'appelant retient le travail et montre le motif (``message``)."""
    maintenant = time.time() if maintenant is None else float(maintenant)
    auteur = _auteur_de(job)
    if not auteur.get('owner'):
        raise Stop('auteur_non_verifiable')
    compte = compte_actuel(desk, auteur['owner'], auteur.get('profil') or None)
    if compte['profil'] == 'session':
        try:
            vu = float(auteur.get('vu') or 0)
        except (TypeError, ValueError):
            vu = 0.0
        if not vu or maintenant - vu > float(fenetre) or vu - maintenant > 5:
            raise Stop('compte_non_verifiable')
        role = auteur.get('role') or ''
    else:
        role = compte['role']
    if role not in tuple(roles):
        raise Stop('role_retire')
    from . import membres5650
    state_dir = str(desk.c.get('state_dir') or '')
    if methode and chemin:
        if not membres5650.allowed(state_dir, compte['owner'], role, methode, chemin):
            raise Stop('droit_retire')
    try:
        masques = membres5650.hidden_for(state_dir, compte['owner'], role)
    except Stop:
        raise Stop('annuaire_illisible') from None
    if dossier and str(dossier) in masques:
        raise Stop('dossier_reserve')
    return {'owner': compte['owner'], 'role': role, 'profil': compte['profil'], 'masques': masques}

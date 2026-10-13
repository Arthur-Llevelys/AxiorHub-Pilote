"""5.6.42 : rubrique « Comptes » des paramètres — administration du compte de l'interface (identifiant, mot de passe, changement de mot de
passe) pour la version serveur et les versions poste ; accès à la gestion des comptes du cabinet pour la distribution autonome.

Le fichier ``ui-auth.json`` de la version serveur appartient à root (lecture seule pour le service) : le nouvel accès est enregistré dans
un fichier privé du dossier d'état du service (``compte-interface5642.json``, 0600), lu par-dessus ``ui-auth.json`` à chaque requête. Le
changement exige le mot de passe actuel ; il invalide les clés des comptes Nextcloud reliés (empreinte du compte, 5.6.37) et, sur un
poste, met à jour le fichier du mot de passe local affiché dans Paramètres › Connexions › Application Nextcloud.
"""
from datetime import datetime, timezone
import hashlib
import hmac
from html import escape as e
import json
from pathlib import Path
import re
import secrets

from .common import Stop, private_json

OVERRIDE = 'compte-interface5642.json'
MIN_LENGTH = 16
USERNAME = re.compile(r'[A-Za-z0-9._@-]{3,64}')


def _override_path(state_dir):
    return Path(state_dir) / OVERRIDE if state_dir else None


def overlay(auth, state_dir):
    """Identifiant et mot de passe de l'interface changés depuis les paramètres, appliqués par-dessus ``ui-auth.json``."""
    path = _override_path(state_dir)
    if not path or not path.is_file():
        return auth
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return auth
    if isinstance(data, dict) and data.get('hash') and data.get('salt') and data.get('username'):
        auth.update(username=str(data['username']), salt=str(data['salt']), hash=str(data['hash']),
                    compte_modifie=str(data.get('date') or ''))
    return auth


def _hash(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def standalone():
    """Distribution autonome (comptes du cabinet) : les comptes se gèrent dans la page Comptes."""
    try:
        from .missions567 import auth_root
        return (auth_root() / 'users.sqlite3').is_file()
    except Exception:   # noqa: BLE001
        return False


def change(desk, auth, data, owner='administrateur'):
    """Changement de l'identifiant et du mot de passe de l'interface : mot de passe actuel exigé, 16 caractères au moins, confirmation."""
    if standalone():
        raise Stop('comptes_du_cabinet_dans_la_page_comptes')
    current = str(data.get('actuel') or '')
    if not auth.get('hash') or not auth.get('salt'):
        raise Stop('compte_interface_absent')
    try:
        ok = hmac.compare_digest(_hash(current, auth['salt']), str(auth['hash']))
    except (ValueError, TypeError):
        ok = False
    if not ok:
        desk.audit('compte5642_refus', {'cause': 'mot_de_passe_actuel'})
        raise Stop('mot_de_passe_actuel_incorrect')
    username = str(data.get('identifiant') or auth.get('username') or 'admin').strip()
    if not USERNAME.fullmatch(username):
        raise Stop('identifiant_invalide')
    new, again = str(data.get('nouveau') or ''), str(data.get('confirmation') or '')
    if len(new) < MIN_LENGTH:
        raise Stop('mot_de_passe_trop_court')
    if new != again:
        raise Stop('mots_de_passe_differents')
    if new == current and username == auth.get('username'):
        raise Stop('mot_de_passe_inchange')
    salt = secrets.token_hex(16)
    record = {'username': username, 'salt': salt, 'hash': _hash(new, salt), 'date': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    state = desk.c.get('state_dir')
    private_json(_override_path(state), record)
    local = Path(state).parent / 'mot-de-passe-local'
    if auth.get('poste') and local.is_file():   # poste : mot de passe local affiché pour relier Nextcloud (mode découverte)
        local.write_text(new + '\n', encoding='utf-8')
        try:
            local.chmod(0o600)
        except OSError:
            pass
    desk.audit('compte5642_modifie', {'auteur': owner, 'identifiant_change': username != auth.get('username')})
    return {'message': 'Accès de l’interface modifié (identifiant « %s »). Votre navigateur va redemander l’identifiant et le nouveau mot de '
                       'passe. Par sécurité, les comptes Nextcloud reliés devront être reliés de nouveau.' % username,
            'noreload': True}


def section_html(desk, auth, prefix):
    """Rubrique « Comptes » des paramètres."""
    if standalone():
        return ('<section class="ax-card" id="comptes"><h2>Comptes du cabinet</h2><p>Les comptes (administrateur, avocats, assistants), leurs '
                'rôles et leurs mots de passe se gèrent dans la page <a href="/comptes">Comptes</a> : créer un compte, changer un rôle, '
                'désactiver, réinitialiser un mot de passe. Chaque personne change son propre mot de passe depuis le lien de son compte, en pied '
                'du menu.</p></section>')
    poste = bool(auth.get('poste'))
    changed = str(auth.get('compte_modifie') or '')
    kind = ('Compte de l’interface de ce poste : utilisé par la fenêtre d’AxiorHub et pour relier Nextcloud en mode découverte.' if poste else
            'Compte de l’interface du serveur : identifiant et mot de passe demandés par le navigateur à l’ouverture d’AxiorHub.')
    return ('<section class="ax-card" id="comptes"><h2>Compte administrateur</h2><p class="ax-muted">%s</p>'
            '<p>Identifiant actuel : <code>%s</code>%s</p>'
            '<form class="m5-form" data-api="m5636/compte/mot-de-passe" autocomplete="off">'
            '<label>Mot de passe actuel<input type="password" name="actuel" required autocomplete="current-password"></label>'
            '<label>Identifiant<input name="identifiant" value="%s" required autocomplete="username" pattern="[A-Za-z0-9._@\\-]{3,64}"></label>'
            '<label>Nouveau mot de passe (%d caractères au moins)<input type="password" name="nouveau" required minlength="%d" '
            'autocomplete="new-password"></label>'
            '<label>Répétez le nouveau mot de passe<input type="password" name="confirmation" required minlength="%d" autocomplete="new-password">'
            '</label><button class="ax-btn" type="submit">Changer le mot de passe</button></form>'
            '<p class="ax-muted">Après le changement, le navigateur redemande l’identifiant et le nouveau mot de passe ; les comptes Nextcloud '
            'reliés doivent être reliés de nouveau. Plusieurs comptes avec des rôles (avocats, assistants) : distribution autonome (Docker) ou '
            'application Nextcloud, où chaque avocat utilise son compte.</p></section>') % (
        e(kind), e(str(auth.get('username') or 'admin')), (' · modifié le %s' % e(changed[:16].replace('T', ' '))) if changed else '',
        e(str(auth.get('username') or 'admin'), quote=True), MIN_LENGTH, MIN_LENGTH, MIN_LENGTH)

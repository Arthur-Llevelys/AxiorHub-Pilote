"""5.6.96 (V06) : avatar 2D facultatif de l'assistant vocal : deux portraits vectoriels dessinés dans ``v5696-avatar.js``.

Le module n'est déclaré dans une page que si le compte a choisi un portrait (préférence ``assistant567.avatar`` : ``portrait_a`` ou
``portrait_b``) : avec « aucun » (ou l'icône historique ``icone``), aucun script n'est déclaré et le navigateur ne fait aucune requête.
La préférence est lue fraîche, en lecture seule, au rendu de chaque page (même procédé que ``talk5643.visible``) ; une base absente ou
illisible vaut « aucun portrait ». La page Paramètres › Voix déclare toujours le module : elle montre l'aperçu avant le choix ; la
discussion vocale (``v568.js``) le charge à la demande quand la personne choisit un portrait dans ses options.
Aucun média, aucune personne réelle, aucun enregistrement : l'animation de la bouche suit le niveau sonore local de la lecture Brindille.
"""
from html import escape
import json
from pathlib import Path
import sqlite3

from .voix5694 import PORTRAITS

SCRIPT = 'v5696-avatar.js'


def illustre(cfg, owner):
    """Un portrait est-il choisi pour ce compte ? Le profil du cabinet sert de base, celui du compte le précise (comme ``assistant567``)."""
    path = Path(str((cfg or {}).get('state_dir') or '')) / 'desk.sqlite3'
    if not path.is_file():
        return False
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            valeur = None
            cles = ['assistant567:profile:cabinet'] + ([] if str(owner or 'cabinet') == 'cabinet' else ['assistant567:profile:' + str(owner)])
            for cle in cles:
                row = db.execute('SELECT value FROM settings WHERE key=?', (cle,)).fetchone()
                profil = json.loads(row[0]) if row else None
                if isinstance(profil, dict) and 'avatar' in profil:
                    valeur = profil['avatar']
        finally:
            db.close()
        return valeur in PORTRAITS
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return False


def script(prefix, cfg, owner, toujours=False):
    """Balise du module pour l'en-tête d'une page : vide quand aucun portrait n'est choisi (sauf ``toujours`` : page de réglage)."""
    if not toujours and not illustre(cfg, owner):
        return ''
    return '<script defer src="' + escape(str(prefix or ''), quote=True) + '/static/' + SCRIPT + '"></script>'

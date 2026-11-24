"""5.6.109 (lot H, F10) : Paramètres › Apparence — couleur du menu de gauche (palette calculée, modes clair et sombre), mode d'affichage,
épinglage de « Discussions IA » dans le menu ; préférence propre à chaque compte.

- **Calcul** : repris de ``reference/apparence109.mjs`` (dossier technique) : hexadécimal à 6 chiffres seulement (ni nom de couleur, ni alpha,
  ni dégradé, ni URL, ni chaîne CSS), luminance relative sRGB (WCAG 2.2), texte noir ou blanc choisi séparément pour chaque état (fond,
  survol, sélection), texte secondaire au même contraste que le texte principal, marqueur de la page active, double focus noir et blanc.
  Le serveur refait le calcul et **refuse** une palette sous les seuils (4,5:1 pour les textes, 3:1 pour le marqueur et le focus), même si
  le navigateur a été contourné ; le navigateur (``v56109-apparence.js``) fait le même calcul pour l'aperçu.
- **Stockage** : clé ``apparence109:<compte>`` du service de réglages existant (``Desk.setting``), versionnée (``schema``, ``revision``) ;
  enregistrer exige la révision lue (409 sinon : deux postes ne s'écrasent pas) ; « Rétablir » efface la surcharge du compte seulement.
- **Application** : la barre latérale porte les deux couleurs en attributs ``data-ax109-*`` (gabarit ``shell501``) ; le script les valide de
  nouveau, pose les variables par CSSOM (``style.setProperty``, permis par la CSP, aucun style en ligne écrit dans le HTML) et la classe
  ``ax-nav-custom``. Le contrôleur de thème de ``v320.js`` reste l'autorité du mode clair ou sombre. Seule la barre latérale change : boutons,
  alertes et pages gardent leurs couleurs.
"""
from html import escape as e
import json
import re

from .common import Stop

PREFIXE = 'm56109/apparence/'
CLE = 'apparence109:'
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
MODES = ('system', 'light', 'dark')
MODES_LIBELLES = {'system': 'Automatique (réglage du système)', 'light': 'Clair', 'dark': 'Sombre'}
PRESETS = (('#ffffff', 'Blanc'), ('#f4f4f4', 'Gris clair'), ('#183153', 'Bleu nuit'), ('#161616', 'Anthracite'), ('#1f4d3a', 'Vert sapin'),
           ('#5a2a3a', 'Bordeaux'))
SEUIL_TEXTE, SEUIL_INDICATEUR = 4.5, 3.0
DEFAUT = {'schema': 1, 'revision': 0, 'mode': 'system', 'sidebar': {'same_for_both': True, 'light': None, 'dark': None}, 'pin_ai_history': False}
MESSAGES = {'couleur_hex_6_requise': 'Couleur invalide : saisissez un code #RRGGBB (6 chiffres hexadécimaux).',
            'preference_invalide': 'Réglage invalide.', 'contraste_insuffisant': 'Couleur refusée : le contraste calculé serait insuffisant.',
            'conflit_de_revision': 'Ce réglage a été modifié entre-temps (autre onglet ou autre poste) : rechargez la page. Rien n’a été enregistré.'}


# ------------------------------------------------------------------------------------------------ calculs sRGB (référence du dossier)
def normaliser_hex(value):
    if not isinstance(value, str) or not HEX.match(value):
        raise Stop('couleur_hex_6_requise')
    return value.lower()


def _rgb(value):
    v = normaliser_hex(value)
    return [int(v[i:i + 2], 16) for i in (1, 3, 5)]


def luminance(value):
    total = 0.0
    for canal, poids in zip(_rgb(value), (0.2126, 0.7152, 0.0722)):
        c = canal / 255
        total += (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) * poids
    return total


def contraste(a, b):
    bas, haut = sorted((luminance(a), luminance(b)))
    return (haut + 0.05) / (bas + 0.05)


def _texte(fond):
    return '#000000' if contraste(fond, '#000000') >= contraste(fond, '#ffffff') else '#ffffff'


def _melanger(fond, avant, poids):
    a, b = _rgb(fond), _rgb(avant)
    return '#' + ''.join('%02x' % int(x * (1 - poids) + y * poids + 0.5) for x, y in zip(a, b))   # arrondi de Math.round (JavaScript)


def palette(couleur):
    fond = normaliser_hex(couleur)
    texte = _texte(fond)
    survol, actif = _melanger(fond, texte, 0.10), _melanger(fond, texte, 0.18)
    return {'fond': fond, 'texte': texte, 'secondaire': texte, 'survol': survol, 'texteSurvol': _texte(survol), 'actif': actif,
            'texteActif': _texte(actif), 'marqueur': _texte(actif), 'focusInterieur': '#ffffff', 'focusExterieur': '#000000'}


def variables(couleur):
    p = palette(couleur)
    return {'--ax-nav-bg': p['fond'], '--ax-nav-fg': p['texte'], '--ax-nav-muted': p['secondaire'], '--ax-nav-hover': p['survol'],
            '--ax-nav-hover-fg': p['texteSurvol'], '--ax-nav-active': p['actif'], '--ax-nav-active-fg': p['texteActif'],
            '--ax-nav-marker': p['marqueur'], '--ax-nav-focus-inner': p['focusInterieur'], '--ax-nav-focus-outer': p['focusExterieur'],
            '--ui-nav': p['fond'], '--ui-nav-texte': p['texte'], '--ui-nav-actif': p['actif'], '--ui-nav-accent': p['marqueur'], '--ui-nav-bord': p['texte'],
            '--ws-sidebar': p['fond'], '--ws-sidebar-text': p['texte'], '--ws-sidebar-border': p['texte']}


def controle_palette(couleur, fabrique=None):
    """Contrôle serveur des seuils (sans arrondi) : textes 4,5:1 sur chaque état, marqueur 3:1, focus double visible (3:1 pour l'un des deux)."""
    p = (fabrique or palette)(couleur)
    paires = ((p['fond'], p['texte']), (p['fond'], p['secondaire']), (p['survol'], p['texteSurvol']), (p['actif'], p['texteActif']))
    mesures = {'texte': round(contraste(p['fond'], p['texte']), 2), 'survol': round(contraste(p['survol'], p['texteSurvol']), 2),
               'selection': round(contraste(p['actif'], p['texteActif']), 2), 'marqueur': round(contraste(p['actif'], p['marqueur']), 2)}
    for fond, avant in paires:
        if contraste(fond, avant) < SEUIL_TEXTE or max(contraste(fond, p['focusInterieur']), contraste(fond, p['focusExterieur'])) < SEUIL_INDICATEUR:
            raise Stop('contraste_insuffisant')
    if contraste(p['actif'], p['marqueur']) < SEUIL_INDICATEUR:
        raise Stop('contraste_insuffisant')
    return mesures


# ------------------------------------------------------------------------------------------------ préférence par compte
def _cle(compte):
    return CLE + str(compte or 'cabinet')[:200]


def valider(valeur):
    """Champs fermés, types réels ; renvoie la valeur normalisée (sans révision)."""
    if not isinstance(valeur, dict) or set(valeur) - {'mode', 'sidebar', 'pin_ai_history'}:
        raise Stop('preference_invalide')
    mode = valeur.get('mode', 'system')
    pin = valeur.get('pin_ai_history', False)
    side = valeur.get('sidebar', {}) or {}
    if mode not in MODES or not isinstance(pin, bool) or not isinstance(side, dict) or set(side) - {'same_for_both', 'light', 'dark'}:
        raise Stop('preference_invalide')
    same = side.get('same_for_both', True)
    if not isinstance(same, bool):
        raise Stop('preference_invalide')
    clair = None if side.get('light') in (None, '') else normaliser_hex(side.get('light'))
    sombre = clair if same else (None if side.get('dark') in (None, '') else normaliser_hex(side.get('dark')))
    for c in (clair, sombre):
        if c:
            controle_palette(c)
    return {'schema': 1, 'mode': mode, 'sidebar': {'same_for_both': same, 'light': clair, 'dark': sombre}, 'pin_ai_history': pin}


def lire(desk, compte):
    """Préférence du compte (lecture seule) ; valeur abîmée : le rendu du produit (jamais une couleur devinée)."""
    brut = desk.settings(_cle(compte), None)
    if not isinstance(brut, dict):
        return json.loads(json.dumps(DEFAUT))
    try:
        v = valider({k: brut[k] for k in ('mode', 'sidebar', 'pin_ai_history') if k in brut})
    except Stop:
        return json.loads(json.dumps(DEFAUT))
    v['revision'] = brut.get('revision') if type(brut.get('revision')) is int else 0
    return v


def enregistrer(desk, qui, valeur, revision, cle):
    from . import acces5697
    from .discussions56109 import executer, revision_attendue
    a = acces5697.acteur_de(qui)
    propre = valider(valeur)

    def faire():
        actuelle = lire(desk, a.compte)
        revision_attendue(actuelle['revision'], revision)
        propre['revision'] = actuelle['revision'] + 1
        desk.setting(_cle(a.compte), propre)
        return {'preference': propre}
    return executer(desk, a.compte, cle, 'apparence', {'v': propre, 'r': revision}, faire)


def reinitialiser(desk, qui, revision, cle):
    """« Rétablir les couleurs par défaut » : efface la surcharge de CE compte (la révision continue, pour qu'un vieil onglet ne réécrive pas)."""
    from . import acces5697
    from .discussions56109 import executer, revision_attendue
    a = acces5697.acteur_de(qui)

    def faire():
        actuelle = lire(desk, a.compte)
        revision_attendue(actuelle['revision'], revision)
        neuve = {**json.loads(json.dumps(DEFAUT)), 'revision': actuelle['revision'] + 1, 'pin_ai_history': actuelle['pin_ai_history']}
        desk.setting(_cle(a.compte), neuve)
        return {'preference': neuve}
    return executer(desk, a.compte, cle, 'apparence_reinit', {'r': revision}, faire)


# ------------------------------------------------------------------------------------------------ gabarit (lecture seule de la base d'état)
def _lire_reglage(cfg, compte):
    import sqlite3
    from pathlib import Path
    db = Path((cfg or {}).get('state_dir', '.')) / 'desk.sqlite3'
    if not db.is_file():
        return None
    con = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True, timeout=2)
    try:
        row = con.execute('SELECT value FROM settings WHERE key=?', (_cle(compte),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return json.loads(row[0]) if row else None


class _Lecture:
    def __init__(self, cfg):
        self.cfg = cfg

    def settings(self, key, default=None):
        compte = key[len(CLE):]
        valeur = _lire_reglage(self.cfg, compte)
        return default if valeur is None else valeur


def preference_gabarit(cfg, compte):
    try:
        return lire(_Lecture(cfg), compte)
    except Exception:   # noqa: BLE001 — réglage illisible : le rendu du produit
        return json.loads(json.dumps(DEFAUT))


def attributs_sidebar(cfg, compte):
    """Attributs de ``<aside id="ws-sidebar">`` : couleurs validées et mode, ou rien (rendu actuel conservé au premier lancement)."""
    p = preference_gabarit(cfg, compte)
    side = p['sidebar']
    if not side['light'] and not side['dark'] and p['mode'] == 'system':
        return ''
    out = ' data-ax109-rev="%d" data-ax109-mode="%s"' % (p['revision'], e(p['mode'], quote=True))
    if side['light']:
        out += ' data-ax109-nav-light="%s"' % e(side['light'], quote=True)
    if side['dark']:
        out += ' data-ax109-nav-dark="%s"' % e(side['dark'], quote=True)
    return out


def epingle(cfg, compte):
    return bool(preference_gabarit(cfg, compte).get('pin_ai_history'))


def script_html(prefix):
    return '<script defer src="%s"></script>' % e(prefix + '/static/v56109-apparence.js', quote=True)


# ------------------------------------------------------------------------------------------------ Paramètres › Apparence
def section_html(desk, prefix, compte):
    p = lire(desk, compte)
    side = p['sidebar']
    presets = ''.join('<button type="button" class="ax109-preset" data-ax109-preset="%s" aria-label="%s (%s)"><span class="ax109-pastille" data-ax109-couleur="%s" aria-hidden="true"></span>%s</button>'
                      % (c, e(nom, quote=True), c, c, e(nom)) for c, nom in PRESETS)

    def champ(nom, libelle, valeur):
        return ('<div class="ax109-champ"><label for="ax109-%s">%s</label><div class="ax109-saisie"><input type="color" id="ax109-%s-choix" data-ax109-choix="%s" value="%s" aria-label="%s — sélecteur">'
                '<input type="text" id="ax109-%s" name="%s" data-ax109-hex="%s" inputmode="text" maxlength="7" pattern="#[0-9A-Fa-f]{6}" placeholder="#RRGGBB" value="%s" aria-describedby="ax109-aide"></div>'
                '<p class="ax109-mesure" data-ax109-mesure="%s" role="status"></p></div>') % (
            nom, e(libelle), nom, nom, e(valeur or '#183153', quote=True), e(libelle, quote=True), nom, nom, nom, e(valeur or '', quote=True), nom)
    modes = ''.join('<option value="%s"%s>%s</option>' % (m, ' selected' if p['mode'] == m else '', e(MODES_LIBELLES[m])) for m in MODES)
    return ('<section class="ax109-apparence" data-ax109-apparence data-ax109-revision="%d" aria-labelledby="ax109-titre">'
            '<h2 id="ax109-titre">Couleur du menu de gauche</h2>'
            '<p>La couleur choisie pilote seulement la barre latérale : boutons, alertes et pages gardent leurs couleurs. Texte, survol, sélection, '
            'icônes et focus sont calculés pour rester lisibles (contraste d’au moins 4,5:1 pour les textes, 3:1 pour la page active et le focus).</p>'
            '<p id="ax109-aide" class="ax109-aide">Code hexadécimal à six chiffres (#RRGGBB) ; aucun nom de couleur, transparence ou dégradé.</p>'
            '<div class="ax109-presets" role="group" aria-label="Couleurs proposées">%s</div>'
            '<label class="ax109-case"><input type="checkbox" id="ax109-meme" data-ax109-meme%s> Utiliser la même couleur dans les deux modes</label>'
            '%s%s'
            '<div class="ax109-champ"><label for="ax109-mode">Mode d’affichage</label><select id="ax109-mode" data-ax109-mode>%s</select></div>'
            '<label class="ax109-case"><input type="checkbox" id="ax109-epingle" data-ax109-epingle%s> Épingler « Discussions IA » dans le menu de gauche</label>'
            '<div class="ax109-apercu-cadre"><p class="ax109-apercu-titre">Aperçu (non enregistré)</p>'
            '<div class="ax109-apercu" id="ax109-apercu" data-ax109-apercu><span class="ax109-apercu-lien">Courriels</span>'
            '<span class="ax109-apercu-lien ax109-apercu-survol">Dossiers (survol)</span><span class="ax109-apercu-lien ax109-apercu-actif" aria-current="page">Aujourd’hui</span></div></div>'
            '<div class="ax109-actions"><button type="button" class="ax-btn" data-ax109-enregistrer>Enregistrer</button>'
            '<button type="button" class="ax-btn ghost" data-ax109-annuler>Annuler</button>'
            '<button type="button" class="ax-btn ghost" data-ax109-defaut>Rétablir les couleurs par défaut</button></div>'
            '<p class="ax109-etat" data-ax109-etat role="status" aria-live="polite"></p></section>') % (
        p['revision'], presets, ' checked' if side['same_for_both'] else '', champ('clair', 'Couleur en mode clair', side['light']),
        champ('sombre', 'Couleur en mode sombre', side['dark']), modes, ' checked' if p['pin_ai_history'] else '')   # script : chargé par le menu latéral


# ------------------------------------------------------------------------------------------------ routes
def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0]
    reste = reste[len(PREFIXE):].strip('/') if reste.startswith(PREFIXE) else ''
    if method in ('GET', 'HEAD'):
        return reste == 'preference'
    return method == 'POST' and reste in ('enregistrer', 'reinitialiser') and role in ('administrateur', 'avocat', 'assistant')


def api(env, desk, auth, prefix, name, args, method):
    from .historique56109 import _json, erreur
    from .web567 import actor
    qui = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    try:
        if not allowed(qui[1], method, name):
            raise Stop('introuvable' if method in ('GET', 'HEAD') else 'role_insuffisant')
        if method in ('GET', 'HEAD'):
            return _json({'preference': lire(desk, qui[0])})
        if int(env.get('CONTENT_LENGTH') or 0) > 8000:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'enregistrer':
            return _json(enregistrer(desk, qui, d.get('valeur'), d.get('revision'), d.get('cle')))
        return _json(reinitialiser(desk, qui, d.get('revision'), d.get('cle')))
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in MESSAGES:
            return _json({'error': code, 'message': MESSAGES[code]}, '409 Conflict' if code == 'conflit_de_revision' else '422 Unprocessable Entity')
        return erreur(code)
    except ValueError:
        return erreur('parametre_invalide')

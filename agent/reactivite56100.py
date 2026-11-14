"""5.6.100 (lot F, P5) : réglage de la nouvelle interface, dans Paramètres (cabinet, administrateur) et dans Mon compte (chaque compte).

L'interrupteur lui-même est ``interface56100`` (API inchangée : ``active``, ``reglage_cabinet``, ``reglage_compte``, ``regler_cabinet``,
``regler_compte``). Ici : les deux encadrés et les deux routes d'écriture.

- ``POST /api440/m56100f/interface/cabinet`` (administrateur) : ``{"choix": "active"|"ancienne", "revision": …}``.
- ``POST /api440/m56100f/interface/compte`` (chaque compte, pour lui seul) : ``{"choix": "cabinet"|"active"|"ancienne", "revision": …}``.

``revision`` est l'état lu à l'affichage : un état changé entre-temps par quelqu'un d'autre donne 409 (rien n'est écrit) ; renvoyer le même
choix (double clic, nouvel essai après une réponse perdue) ne réécrit rien et répond « déjà enregistré » : le réglage est son propre effet
idempotent. Chaque changement est noté au journal d'audit (portée, valeur, jamais d'autre donnée). Les correctifs R04, R05 et R10 ne
dépendent pas de ce réglage : l'encadré le dit.
"""
from html import escape as e

from .common import Stop
from . import interface56100 as itf

ROUTES = ('interface/cabinet', 'interface/compte')
CHOIX_CABINET = {'active': True, 'ancienne': False}
CHOIX_COMPTE = {'cabinet': None, 'active': True, 'ancienne': False}
LIBELLES = {True: 'nouvelle interface', False: 'présentation précédente'}
NOTE_PROTECTIONS = ('Les protections des courriels réservés, l’intégrité du portefeuille et les contrôles de clôture s’appliquent dans les deux '
                    'présentations.')


def _jeton(valeur):
    return {True: 'active', False: 'ancienne', None: 'cabinet'}[valeur]


def allowed(role, route):
    """Droits des écritures (``standalone_auth.allowed``) : réglage du cabinet pour l'administrateur, son propre réglage pour chacun."""
    route = str(route or '').split('?', 1)[0]
    if route == 'interface/cabinet':
        return role == 'administrateur'
    return route == 'interface/compte'


def comptes_differents(desk):
    """Nombre de comptes qui ont choisi autre chose que le cabinet (aucun identifiant affiché)."""
    try:
        rows = desk.db.execute('SELECT value FROM settings WHERE key >= ? AND key < ?', (itf.PREFIXE_COMPTE, itf.PREFIXE_COMPTE + '￿')).fetchall()
    except Exception:   # noqa: BLE001 — encadré affiché sans ce nombre
        return None
    cabinet = itf.reglage_cabinet(desk)
    return sum(1 for (valeur,) in rows if valeur in ('true', 'false') and (valeur == 'true') != cabinet)


def _radio(nom, valeur, libelle, coche, aide=''):
    return ('<label class="i100-choix"><input type="radio" name="%s" value="%s"%s> <span>%s</span>%s</label>' % (
        e(nom, quote=True), e(valeur, quote=True), ' checked' if coche else '', e(libelle), ('<small class="ax-muted">%s</small>' % e(aide)) if aide else ''))


def section_cabinet(desk, prefix, role):
    """Encadré de Paramètres › Cabinet (administrateur seulement ; rien pour un autre rôle)."""
    actuel = itf.reglage_cabinet(desk)
    if role != 'administrateur':
        return ('<section class="ax-card" id="i100-cabinet"><h2>Présentation de l’interface</h2><p class="ax-muted">Choix du cabinet : %s, réglé par '
                'l’administrateur. Votre propre choix se fait dans <a href="%s/mon-compte">Mon compte</a>.</p></section>') % (
            e(LIBELLES[actuel]), e(prefix, quote=True))
    autres = comptes_differents(desk)
    note = '' if not autres else ('<p class="ax-muted">%d compte(s) ont choisi une autre présentation dans Mon compte ; leur choix reste prioritaire.</p>' % autres)
    return ('<section class="ax-card" id="i100-cabinet"><h2>Présentation de l’interface</h2>'
            '<p class="ax-muted">Nouvelle interface : lecteur commun des courriels, Aujourd’hui, Dossiers et Produire réorganisés. Choix du cabinet, '
            'modifiable à tout moment ; chaque compte peut choisir autrement dans <a href="%s/mon-compte">Mon compte</a>. %s</p>'
            '<form class="m5-form" data-api="m56100f/interface/cabinet" data-reload><input type="hidden" name="revision" value="%s">'
            '<fieldset class="i100-choix-groupe"><legend>Pour tout le cabinet</legend>%s%s</fieldset>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>%s</section>') % (
        e(prefix, quote=True), e(NOTE_PROTECTIONS), e(_jeton(actuel), quote=True),
        _radio('choix', 'active', 'Nouvelle interface (recommandée)', actuel), _radio('choix', 'ancienne', 'Présentation précédente', not actuel), note)


def section_compte(desk, owner):
    """Encadré de Mon compte : le choix de la personne, ou « comme le cabinet »."""
    cabinet = itf.reglage_cabinet(desk)
    propre = itf.reglage_compte(desk, owner)
    return ('<section class="ax-card" id="i100-compte"><h2>Présentation de l’interface</h2>'
            '<p class="ax-muted">Votre choix ne change que votre affichage. %s</p>'
            '<form class="m5-form" data-api="m56100f/interface/compte" data-reload><input type="hidden" name="revision" value="%s">'
            '<fieldset class="i100-choix-groupe"><legend>Pour mon compte</legend>%s%s%s</fieldset>'
            '<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></section>') % (
        e(NOTE_PROTECTIONS), e(_jeton(propre), quote=True),
        _radio('choix', 'cabinet', 'Comme le cabinet', propre is None, 'actuellement : ' + LIBELLES[cabinet]),
        _radio('choix', 'active', 'Nouvelle interface', propre is True), _radio('choix', 'ancienne', 'Présentation précédente', propre is False))


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    route = name[len('m56100f/'):].split('?', 1)[0]
    owner, role = actor(env)
    if route not in ROUTES:
        raise Stop('route_inconnue')
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)                       # origine, jeton CSRF, corps JSON borné
    choix, attendu = str(data.get('choix') or ''), str(data.get('revision') or '')
    if route == 'interface/cabinet':
        if choix not in CHOIX_CABINET:
            raise Stop('choix_interface_invalide')
        actuel, voulu = itf.reglage_cabinet(desk), CHOIX_CABINET[choix]
    else:
        if choix not in CHOIX_COMPTE:
            raise Stop('choix_interface_invalide')
        actuel, voulu = itf.reglage_compte(desk, owner), CHOIX_COMPTE[choix]
    if actuel == voulu:                                # même choix renvoyé : aucun effet, aucune écriture
        return json_out({'enregistre': True, 'inchange': True, 'message': 'Déjà enregistré.'})
    if attendu != _jeton(actuel):
        raise Stop('version_en_conflit')
    desk.audit('interface56100_reglage', {'portee': 'cabinet' if route == 'interface/cabinet' else 'compte', 'choix': choix})
    if route == 'interface/cabinet':
        itf.regler_cabinet(desk, voulu)                # écrit et valide l'audit dans la même transaction
        message = 'Réglage du cabinet enregistré : %s.' % LIBELLES[voulu]
    else:
        itf.regler_compte(desk, owner, voulu)
        effectif = itf.active(desk, owner)
        message = 'Votre réglage est enregistré : %s.' % LIBELLES[effectif]
    return json_out({'enregistre': True, 'revision': choix, 'message': message})

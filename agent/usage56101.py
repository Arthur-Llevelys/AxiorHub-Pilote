"""5.6.101 (lot F, F05) : compteur d'usage local des fonctions, écran « Fonctions peu utilisées ».

- Nombre d'ouvertures par fonction et par jour, à l'échelle du cabinet : ni compte, ni contenu, ni adresse, ni dossier. Une ligne par
  fonction et par jour du cabinet (fuseau du cabinet) ; les jours de plus de 90 jours sont effacés à chaque enregistrement.
- L'ouverture est signalée par la page elle-même après affichage, en **POST** (``navigator.sendBeacon``, jeton CSRF dans le corps) :
  aucune écriture pendant un GET (R05). La page « Fonctions peu utilisées » (Paramètres › Administration) ne fait que lire.
- Désactivable par l'administrateur (réglage versionné, 409 si périmé, journal d'audit) ; désactivé, rien n'est compté et la page ne
  signale plus rien. « Effacer les relevés » vide la table.
- La table est créée à la préparation de la base (mécanisme de ``Desk``), jamais pendant une requête de lecture.
"""
from datetime import date, timedelta
from html import escape as e
import hmac
import json
import re
import sqlite3
import threading
import time

from .common import Stop
from . import fonctions5631 as reg
from . import i18n5631 as i18n

SCHEMA = '''
CREATE TABLE IF NOT EXISTS usage56101(fonction TEXT NOT NULL, jour TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(fonction, jour)) WITHOUT ROWID;
'''
CONSERVATION = 90          # jours conservés
SEUIL = 60                 # « peu utilisée » : pas ouverte depuis 60 jours
PLAFOND_JOUR = 100_000     # une fonction ne dépasse pas ce nombre d'ouvertures par jour (saisie forgée bornée)
MAX_CORPS = 1024
ACTIF, VERSION, DEPUIS = 'usage56101:actif', 'usage56101:version', 'usage56101:depuis'
ROUTES = {'usage': 'tous', 'usage/reglage': 'administrateur', 'usage/effacer': 'administrateur'}
PAGE = '/administration/fonctions-peu-utilisees'
CLE = re.compile(r'[A-Za-z0-9_-]{8,64}')
_vues, _verrou = {}, threading.Lock()   # clés d'ouverture déjà comptées (mémoire seulement, 10 minutes) : un envoi rejoué ne compte pas deux fois


def ensure_schema(desk):
    """Table additive ; appelée par ``Desk`` à la préparation de la base (jamais pendant une requête de lecture)."""
    if desk.db.in_transaction:
        desk.db.commit()
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _jour(desk):
    from .acces5683 import today_of
    return today_of(desk)[0]


def actif(desk):
    return desk.settings(ACTIF, True) is not False


def version(desk):
    return int(desk.settings(VERSION, 0) or 0)


def actif_lecture(cfg):
    """Réglage lu pour le gabarit, en lecture seule (aucun bureau ouvert, aucune écriture) ; illisible : actif."""
    try:
        from .shell501 import _Desk53
        return _Desk53(cfg or {}).settings(ACTIF, True) is not False
    except Exception:   # noqa: BLE001
        return True


def balise_html(prefix, path, cfg):
    """Marqueur lu par ``v56101-familles.js`` : la fonction de la page (identifiant du registre, rien d'autre). '' si la page n'est pas
    une fonction du registre ou si le compteur est désactivé."""
    from . import familles56101 as fam
    chemin = fam.chemin_de_page(path)
    fid = fam.fonction_de(chemin)
    if not fid or chemin.split('?', 1)[0] in fam.ECRANS or not actif_lecture(cfg):
        return ''
    return '<div hidden id="f101-usage" data-fonction="' + e(fid, quote=True) + '" data-url="' + e(prefix + '/api440/m56101/usage', quote=True) + '"></div>'


def _deja_vue(cle):
    maintenant = time.monotonic()
    with _verrou:
        for k in [k for k, t in _vues.items() if maintenant - t > 600]:
            del _vues[k]
        if cle in _vues:
            return True
        if len(_vues) < 5000:
            _vues[cle] = maintenant
        return False


def enregistrer(desk, fid, cle=''):
    """Compte une ouverture. Renvoie True si elle a été comptée (False : compteur désactivé, envoi rejoué ou base occupée)."""
    if fid not in reg.INDEX:
        raise Stop('fonction_inconnue')
    if not actif(desk):
        return False
    if cle and _deja_vue(cle):
        return False
    jour = _jour(desk)
    limite = (jour - timedelta(days=CONSERVATION)).isoformat()
    desk.db.execute('PRAGMA busy_timeout=300')
    try:
        desk.db.execute('INSERT INTO usage56101(fonction,jour,n) VALUES(?,?,1) ON CONFLICT(fonction,jour) DO UPDATE SET n=MIN(n+1,?)',
                        (fid, jour.isoformat(), PLAFOND_JOUR))
        desk.db.execute('DELETE FROM usage56101 WHERE jour<=?', (limite,))
        desk.db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (DEPUIS, json.dumps(jour.isoformat())))
        desk.db.commit()
    except sqlite3.OperationalError as error:   # base occupée : l'ouverture n'est pas comptée, la page n'en souffre pas
        desk.db.rollback()
        if 'locked' not in str(error).lower() and 'busy' not in str(error).lower():
            raise
        return False
    return True


def regler(desk, actif_voulu, attendu):
    if not isinstance(actif_voulu, bool):
        raise Stop('reglage_invalide')
    if actif(desk) == actif_voulu:
        return {'enregistre': True, 'inchange': True, 'actif': actif_voulu, 'version': version(desk)}
    if str(attendu) != str(version(desk)):
        raise Stop('version_en_conflit')
    nouvelle = version(desk) + 1
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (ACTIF, json.dumps(actif_voulu)))
    desk.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (VERSION, json.dumps(nouvelle)))
    desk.audit('usage56101_reglage', {'actif': actif_voulu})   # valide les deux réglages et l'audit ensemble
    return {'enregistre': True, 'actif': actif_voulu, 'version': nouvelle}


def effacer(desk):
    n = desk.db.execute('DELETE FROM usage56101').rowcount
    desk.db.execute('DELETE FROM settings WHERE key=?', (DEPUIS,))
    desk.audit('usage56101_effacement', {'lignes': n})
    return {'efface': True, 'lignes': n}


def releve(desk):
    """Lecture seule : [{'id', 'derniere', 'total'}] des fonctions du registre non ouvertes depuis ``SEUIL`` jours, et la date de départ."""
    jour = _jour(desk)
    seuil = (jour - timedelta(days=SEUIL)).isoformat()
    vues = {r[0]: (r[1], r[2]) for r in desk.db.execute('SELECT fonction, MAX(jour), SUM(n) FROM usage56101 WHERE n>0 GROUP BY fonction')}
    depuis = desk.settings(DEPUIS, '') or ''
    lignes = []
    for fid, *_ in reg.FUNCTIONS:
        derniere, total = vues.get(fid, ('', 0))
        if not derniere or derniere < seuil:
            lignes.append({'id': fid, 'derniere': derniere, 'total': int(total or 0)})
    jeune = not depuis or depuis > seuil
    return {'lignes': lignes, 'depuis': depuis, 'jeune': jeune, 'actif': actif(desk), 'version': version(desk)}


# ------------------------------------------------------------------------------------------------ web
def _date(valeur):
    try:
        return i18n.format_date(date.fromisoformat(str(valeur)))
    except ValueError:
        return str(valeur)


def autoriser_route(role, method, api):
    """Règle de route (``standalone_auth.allowed``) : signaler une ouverture, tous les rôles ; régler et effacer, administrateur."""
    route = api[len('m56101/'):].split('?', 1)[0]
    if method != 'POST' or route not in ROUTES:
        return False
    return ROUTES[route] == 'tous' or role == 'administrateur'


def _corps_balise(env, auth):
    """Contrôle d'un envoi ``sendBeacon`` : même origine, jeton CSRF (en-tête, sinon champ ``csrf`` du corps : ``sendBeacon`` ne pose pas
    d'en-tête), corps JSON borné. Le type ``text/plain`` est admis (seul type sans requête préalable pour ``sendBeacon``)."""
    if env.get('HTTP_ORIGIN') != auth['origin']:
        raise Stop('origine_ou_csrf_refuse')
    try:
        length = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        length = MAX_CORPS + 1
    if length > MAX_CORPS:
        raise Stop('requete_trop_longue')
    if env.get('CONTENT_TYPE', '').split(';')[0].strip() not in ('application/json', 'text/plain'):
        raise Stop('json_requis')
    try:
        data = json.loads(env['wsgi.input'].read(length).decode('utf-8') or '{}')
    except (ValueError, UnicodeError):
        raise Stop('json_invalide') from None
    if not isinstance(data, dict):
        raise Stop('objet_json_requis')
    jeton = env.get('HTTP_X_CSRF_TOKEN', '') or str(data.get('csrf') or '')
    if not hmac.compare_digest(jeton.encode(), str(auth['csrf']).encode()):
        raise Stop('origine_ou_csrf_refuse')
    return data


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m56101/'):].split('?', 1)[0]
    prive = [('Cache-Control', 'private, no-store')]

    def sortie(valeur, status='200 OK'):
        return {**json_out(valeur, status), 'headers': prive}
    try:
        if route not in ROUTES:
            return sortie({'error': 'route_inconnue'}, '404 Not Found')
        if method != 'POST':
            return sortie({'error': 'methode_refusee', 'message': 'Méthode refusée.'}, '405 Method Not Allowed')
        if ROUTES[route] != 'tous' and role != 'administrateur':   # contrôle avant tout corps
            return sortie({'error': 'role_insuffisant', 'message': 'Réglage réservé à l’administrateur.'}, '403 Forbidden')
        if route == 'usage':
            data = _corps_balise(env, auth)
            cle = str(data.get('cle') or '')
            compte = enregistrer(desk, str(data.get('fonction') or '')[:60], cle if CLE.fullmatch(cle) else '')
            return {'status': '204 No Content', 'kind': 'text/plain; charset=utf-8', 'body': '', 'headers': prive + [('X-Compte', '1' if compte else '0')]}
        data = check_post(env, auth)
        if route == 'usage/reglage':
            return sortie(regler(desk, data.get('actif'), data.get('version', '')))
        if data.get('confirmer') is not True:
            raise Stop('confirmation_requise')
        return sortie(effacer(desk))
    except Stop as error:
        code = str(error).split(':')[0]
        statut = {'version_en_conflit': '409 Conflict', 'fonction_inconnue': '404 Not Found'}.get(code, '400 Bad Request')
        message = {'version_en_conflit': 'Le réglage a été modifié entre-temps : rechargez la page.',
                   'fonction_inconnue': 'Fonction inconnue.', 'confirmation_requise': 'Confirmation requise.'}.get(code, 'Requête refusée.')
        return sortie({'error': code, 'message': message}, statut)


def page(desk, auth, prefix, env, shell):
    """Paramètres › Administration › « Fonctions peu utilisées » (administrateur ; lecture seule)."""
    from .web567 import actor
    from . import familles56101 as fam
    th, t = i18n.th, i18n.t
    titre = t('f101.usage.titre')
    if actor(env)[1] != 'administrateur':   # même réponse neutre qu'une page absente
        return None
    body = '<section class="f101-usage" aria-labelledby="f101-usage-titre"><h1 id="f101-usage-titre">' + e(titre) + '</h1><p>' + th('f101.usage.intro') + '</p>'
    try:
        r = releve(desk)
    except (sqlite3.Error, Stop):   # panne de lecture : dit, jamais une liste vide présentée comme exacte
        return shell(titre, body + '<p class="f101-alerte" role="status">' + th('f101.usage.indisponible') + '</p></section>', prefix, auth['csrf'], PAGE)
    etat = th('f101.usage.actif') if r['actif'] else th('f101.usage.inactif')
    body += ('<p class="f101-etat" id="f101-etat" role="status">' + etat + (' ' + th('f101.usage.depuis', date=_date(r['depuis'])) if r['depuis'] else '') + '</p>'
             '<p class="f101-actions"><button type="button" class="ax-btn" data-f101-reglage="' + ('0' if r['actif'] else '1') + '" data-version="' + str(r['version']) +
             '" data-url="' + e(prefix + '/api440/m56101/usage/reglage', quote=True) + '">' + (th('f101.usage.desactiver') if r['actif'] else th('f101.usage.activer')) + '</button> '
             '<button type="button" class="ax-btn ax-btn-secondaire" data-f101-effacer data-url="' + e(prefix + '/api440/m56101/usage/effacer', quote=True) + '" data-confirmer="' +
             th('f101.usage.effacer_confirmer') + '">' + th('f101.usage.effacer') + '</button></p>')
    if r['jeune']:
        body += '<p class="f101-note">' + th('f101.usage.jeune') + '</p>'
    if not r['lignes']:
        body += '<p class="empty">' + th('f101.usage.aucune') + '</p></section>'
        return shell(titre, body, prefix, auth['csrf'], PAGE)
    body += ('<h2>' + th('f101.usage.liste', n=len(r['lignes'])) + '</h2><div class="f101-table"><table><thead><tr><th scope="col">' + th('f101.usage.col_fonction') +
             '</th><th scope="col">' + th('f101.usage.col_famille') + '</th><th scope="col">' + th('f101.usage.col_derniere') + '</th><th scope="col">' +
             th('f101.usage.col_total') + '</th></tr></thead><tbody>')
    for ligne in r['lignes']:
        route = reg.INDEX[ligne['id']][2]
        body += ('<tr><th scope="row"><a href="' + e(prefix + route, quote=True) + '">' + th('fn.%s.label' % ligne['id']) + '</a></th><td>' +
                 e(fam.libelle(fam.famille_de(ligne['id']))) + '</td><td>' + (e(_date(ligne['derniere'])) if ligne['derniere'] else th('f101.usage.jamais')) +
                 '</td><td>' + str(ligne['total']) + '</td></tr>')
    body += '</tbody></table></div></section>'
    return shell(titre, body, prefix, auth['csrf'], PAGE)

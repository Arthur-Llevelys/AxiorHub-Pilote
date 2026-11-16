"""5.6.103 (lot M) : routes ``/api440/m56103/microsoft/…`` et section « Compte Microsoft » de Paramètres › Connexions.

- Connexion propre à chaque personne (tous les rôles) : ``connexion`` (PKCE sur le poste, code d'appareil sur le serveur), ``appareil/sonder``,
  ``deconnecter``. Lecture ``etat`` : vue sans secret, étapes de l'assistant (lecture seule, aucun appel à Microsoft).
- Brouillons Outlook : ``brouillon/retenir`` (projet local, rien chez Microsoft), ``brouillon/deposer`` (geste distinct, clé d'idempotence et
  version), ``brouillon/verifier``, ``brouillon/abandonner``. Jamais d'envoi.
- Objet absent ou d'une autre personne : même 404 neutre ; version périmée ou état incompatible : 409 ; réponses ``private, no-store``.
"""
import hashlib
from html import escape as e
import json
import re

from .common import Stop
from . import microsoft56103 as ms

PREFIXE = 'm56103/microsoft/'
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('etat',)
ROUTES_POST = ('connexion', 'appareil/sonder', 'deconnecter', 'brouillon/retenir', 'brouillon/deposer', 'brouillon/verifier', 'brouillon/abandonner')
NEUTRES = ('brouillon_absent', 'microsoft56103_appareil_inconnu', 'message_absent')
STATUTS = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'etat_incompatible': '409 Conflict', 'depot_a_verifier': '409 Conflict',
           'compte_modifie': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict', 'role_insuffisant': '403 Forbidden',
           'geste_reserve_a_la_personne': '403 Forbidden'}
CLE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
MESSAGES = {'introuvable': 'Élément introuvable.', 'version_perimee': 'Le projet a changé entre-temps : rechargez la page.',
            'depot_a_verifier': 'Dépôt à vérifier d’abord (bouton « Vérifier dans Outlook »).', 'etat_incompatible': 'Action impossible dans cet état.',
            'compte_modifie': 'Le compte Microsoft a changé depuis : vérifiez le projet.', 'idempotence_requise': 'Clé de demande manquante : rechargez la page.',
            'geste_reserve_a_la_personne': 'Ce geste est réservé à une personne.', 'adresse_invalide': 'Adresse de destinataire invalide.'}


def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    return role in ROLES and route in (ROUTES_GET if method == 'GET' else ROUTES_POST)


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in NEUTRES:
            code = 'introuvable'                     # absent et interdit : même réponse neutre
        message = MESSAGES.get(code) or ms.MESSAGES.get(code)
        if not message:
            from .erreurs5634 import explain
            message = explain(code)
        return _out({'error': code, 'message': message}, STATUTS.get(code, '400 Bad Request'))


def etat(desk, owner, env=None):
    """Vue sans secret pour la page (aucun appel à Microsoft, aucune écriture)."""
    row = ms.connexion(desk, owner)
    flux = None
    if row and ms._table_presente(desk):
        r = desk.db.execute("SELECT id, code_utilisateur, adresse, intervalle, expire FROM microsoft56103_appareils WHERE connexion_id=? AND etat='en_attente' "
                            "ORDER BY cree DESC LIMIT 1", (row['id'],)).fetchone()
        if r and r['expire'] > ms.HORLOGE():
            flux = {'flux': r['id'], 'code': r['code_utilisateur'], 'adresse': r['adresse'], 'intervalle': r['intervalle']}
    return {'disponible': ms.disponible(desk), 'message': '' if ms.disponible(desk) else ms.MESSAGE_INDISPONIBLE, 'mode': ms.mode_pour(env),
            'connexion': ms.vue(row), 'flux': flux, 'etapes': ms.etapes_assistant(desk, owner, env)}


def _recu(desk, owner, route, cle, empreinte):
    from . import outlook56103 as ol
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('idempotence_requise')
    ol.ensure_schema(desk)
    r = desk.db.execute('SELECT * FROM outlook56103_journal WHERE idempotence=?', ('%s:%s' % (route[:12], cle),)).fetchone()
    if r is None:
        return None
    if r['auteur'] != str(owner) or r['resultat'] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return r['brouillon_id']


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    owner, role = actor(env)
    route = name.split('?', 1)[0][len(PREFIXE):]
    if not allowed(role, method, PREFIXE + route):
        raise Stop('route_inconnue' if route not in ROUTES_GET + ROUTES_POST else 'role_insuffisant')
    if method == 'GET':
        return _out(etat(desk, owner, env))
    from .web440 import check_post
    data = check_post(env, auth)
    if route == 'connexion':
        caps = [str(c) for c in (data.get('capacites') or []) if isinstance(c, str)][:4]
        ms.portees(caps)                                   # garde-fou avant toute écoute ou tout appel
        if ms.mode_pour(env) == 'pkce':
            ms._exiger_client(desk)
            port = ms.ecouter_retour(desk.c)
            return _out(ms.demarrer_pkce(desk, owner, caps, port))
        return _out(ms.demarrer_appareil(desk, owner, caps))
    if route == 'appareil/sonder':
        return _out(ms.sonder_appareil(desk, owner, data.get('flux')))
    if route == 'deconnecter':
        return _out(ms.deconnecter(desk, owner))
    from . import outlook56103 as ol
    if route == 'brouillon/retenir':
        empreinte = hashlib.sha256(json.dumps([data.get(k) for k in ('a', 'cc', 'objet', 'texte')], ensure_ascii=False).encode()).hexdigest()
        ancien = _recu(desk, owner, route, data.get('cle'), empreinte)
        if ancien:
            return _out({'deja': True, **ol.vue(desk, owner, ancien)})
        out = ol.retenir(desk, owner, data.get('a', ''), data.get('cc', ''), data.get('objet', ''), data.get('texte', ''))
        desk.db.execute('INSERT OR IGNORE INTO outlook56103_journal(idempotence, brouillon_id, action, auteur, resultat, cree) VALUES(?,?,?,?,?,?)',
                        ('%s:%s' % (route[:12], data.get('cle')), out['id'], 'retenir', str(owner), empreinte, ol._iso()))
        desk.db.commit()
        return _out({**out, 'message': 'Projet retenu dans AxiorHub : rien n’a été déposé ni envoyé.'})
    if route == 'brouillon/deposer':
        return _out(ol.deposer(desk, owner, data.get('id'), int(data.get('version') or 0), data.get('cle')))
    if route == 'brouillon/verifier':
        return _out(ol.verifier(desk, owner, data.get('id')))
    return _out(ol.abandonner(desk, owner, data.get('id'), int(data.get('version') or 0)))


# ------------------------------------------------------------------------------------------------ Paramètres › Connexions
def section_html(desk, prefix, owner, env=None):
    """Section « Compte Microsoft (application intégrée) » : état réel, connexion, code d'appareil, déconnexion. Aucun appel réseau au rendu."""
    out = etat(desk, owner, env)
    if not out['disponible']:
        corps = ('<p class="ax-muted" role="status">%s</p><p class="ax-muted">L’agenda Outlook reste disponible avec l’application propre du cabinet '
                 '(section « Agenda Microsoft 365 » plus bas).</p>') % e(ms.MESSAGE_INDISPONIBLE)
    else:
        c = out['connexion']
        if c['etat'] == 'active':
            caps = ', '.join(ms.LIBELLES[x] for x in c['capacites']) or 'aucune autorisation utile'
            corps = '<p class="s5636-ok">Relié : <strong>%s</strong></p><p class="ax-muted">Autorisations : %s.</p>' % (e(c['compte']), e(caps))
        elif c['etat'] == 'a_reconnecter':
            corps = '<p class="ax-alert" role="status">%s</p>' % e(ms.MESSAGES['microsoft56103_reconnexion_requise'])
        else:
            corps = '<p class="ax-muted">Non relié.</p>'
        manquantes = [x for x in ('courrier', 'agenda', 'fichiers') if x not in c['capacites']]
        cases = ''.join('<label class="e5636-switch"><input type="checkbox" name="capacites" value="%s"%s> %s</label>'
                        % (x, ' checked' if x in manquantes or c['etat'] != 'active' else '', e(ms.LIBELLES[x])) for x in ('courrier', 'agenda', 'fichiers', 'sharepoint'))
        mode = ('Une page Microsoft s’ouvre dans ce navigateur ; le retour arrive sur ce poste.' if out['mode'] == 'pkce' else
                'Un code s’affiche : saisissez-le sur microsoft.com/devicelogin depuis n’importe quel appareil.')
        flux = ''
        if out['flux']:
            flux = ('<p class="m103-code" data-m103-flux="%s" data-m103-intervalle="%d">Code à saisir : <strong>%s</strong> sur <a href="%s" target="_blank" '
                    'rel="noopener noreferrer">%s</a></p>') % (e(out['flux']['flux'], quote=True), int(out['flux']['intervalle']), e(out['flux']['code']),
                                                               e(out['flux']['adresse'], quote=True), e(out['flux']['adresse']))
        corps += ('<form class="m103-connexion" data-m103="connexion"><fieldset><legend>Autorisations demandées</legend>%s</fieldset>'
                  '<p class="ax-muted">%s AxiorHub ne demande jamais l’envoi de courriel : les brouillons restent dans Outlook jusqu’à votre envoi.</p>'
                  '<p class="ax-muted">Dans beaucoup de cabinets, un administrateur Microsoft 365 doit d’abord consentir pour l’organisation '
                  '(courrier et agenda inclus, selon les réglages actuels de Microsoft).</p>'
                  '<div class="ax-actions"><button type="submit" class="ax-btn">%s</button>%s</div></form>'
                  '<div class="m103-retour" role="status" aria-live="polite">%s</div>') % (
            cases, e(mode), 'Se connecter à Microsoft' if c['etat'] != 'active' else 'Ajouter les autorisations cochées',
            '<button type="button" class="ax-btn ghost" data-m103="deconnecter">Déconnecter</button>' if c['etat'] in ('active', 'a_reconnecter') else '', flux)
    return ('<section class="ax-card" id="microsoft56103"><h3>Compte Microsoft (Outlook, agenda, OneDrive)</h3>%s'
            '<script defer src="%s"></script></section>') % (corps, e(prefix + '/static/v56103-m.js', quote=True))

"""5.6.103 (lot L) : rubrique « Liaison entre postes » (Paramètres › Confort et maintenance), état des postes, appairage, réglages
partagés, et compléments de la Messagerie instantanée (bandeau des postes reliés, pièces jointes).

Routes ``/api440/m56103/liaison/*`` (poste de travail seulement) :

- GET  ``etat`` (état de la liaison), ``fil?id=`` (fil relié : présence, messages en attente), ``piece?id=`` (téléchargement) ;
- POST ``activer``, ``nom``, ``adresse``, ``code``, ``appairer``, ``retirer``, ``diffuser``, ``reglage/appliquer``, ``reglage/refuser``
  (administrateur) ; ``presence`` et ``piece?fil=`` (dépôt binaire, 25 Mo au plus) pour tous les rôles.

Toute écriture : origine et jeton CSRF (``check_post`` ou le même contrôle pour le dépôt binaire), clé d'idempotence (``cle``), réponse
``Cache-Control: private, no-store`` ; objet absent et objet interdit donnent le même 404 neutre ; décision déjà prise : 409. Aucune
écriture pendant une lecture.
"""
from html import escape as e
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import unquote

from .common import Stop
from . import liaison56103 as L

PREFIXE = 'm56103/liaison/'
ROLES = ('administrateur', 'avocat', 'assistant')
ROUTES_GET = ('etat', 'fil', 'piece')
ROUTES_POST_TOUS = ('presence', 'piece')
ROUTES_POST_ADMIN = ('activer', 'nom', 'adresse', 'code', 'appairer', 'retirer', 'diffuser', 'reglage/appliquer', 'reglage/refuser')
CLE = re.compile(r'^[A-Za-z0-9_-]{16,80}$')
NEUTRES = ('conversation_absente', 'piece_absente', 'poste_absent', 'reglage_absent', 'reglage_inconnu', 'introuvable')
STATUTS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden',
           'origine_ou_csrf_refuse': '403 Forbidden', 'liaison_reservee_au_poste': '404 Not Found', 'reglage_deja_decide': '409 Conflict',
           'cle_idempotence_deja_utilisee': '409 Conflict', 'piece_trop_grande': '413 Payload Too Large',
           'controle_indisponible': '503 Service Unavailable'}
MESSAGES = {
    'introuvable': 'Élément introuvable.',
    'liaison_desactivee': 'La liaison entre postes est désactivée sur ce poste : activez-la d’abord (Paramètres › Confort et maintenance).',
    'liaison_reservee_au_poste': 'La liaison entre postes n’existe que sur les postes de travail.',
    'adresse_invalide': 'Adresse illisible : saisissez une adresse IP du réseau local, par exemple celle affichée dans Paramètres sur l’autre poste.',
    'port_invalide': 'Port invalide : utilisez le port affiché sur l’autre poste (47862 par défaut).',
    'code_appairage_invalide': 'Le code d’appairage compte 8 chiffres (par exemple 1234-5678).',
    'code_refuse': 'Code refusé : il ne correspond pas au code affiché sur l’autre poste (ou il a expiré). Affichez un nouveau code et réessayez.',
    'aucun_code_actif': 'Aucun code d’appairage n’est affiché sur l’autre poste (ou il a expiré) : cliquez sur « Afficher un code » sur l’autre poste.',
    'poste_injoignable': 'L’autre poste ne répond pas : vérifiez qu’il est allumé, que la liaison y est activée et que le pare-feu autorise le port de la liaison.',
    'poste_identique': 'C’est l’adresse de ce poste-ci : saisissez l’adresse de l’autre poste.',
    'signature_refusee': 'La signature de l’autre poste est invalide : appairage interrompu, rien n’a été enregistré.',
    'poste_non_appaire': 'Ce poste n’est pas (ou plus) appairé avec l’autre poste : appairez-les de nouveau.',
    'piece_trop_grande': 'Pièce jointe trop volumineuse : 25 Mo au plus par fichier.',
    'piece_vide': 'Fichier vide : rien n’a été joint.',
    'fil_non_relie': 'Cette discussion ne comprend aucun poste relié : les pièces jointes passent seulement par la liaison entre postes.',
    'reglage_personnel_non_partageable': 'Identifiant personnel (mot de passe de messagerie, jeton OAuth, session, abonnement) : jamais partagé avec les autres postes.',
    'reglage_deja_decide': 'Ce réglage a déjà été appliqué ou refusé : rechargez la page.',
    'cle_idempotence_requise': 'Formulaire périmé : rechargez la page puis recommencez.',
    'cle_idempotence_deja_utilisee': 'Ce formulaire a déjà servi pour une autre demande : rechargez la page.',
    'role_insuffisant': 'Action réservée à l’administrateur de ce poste.',
    'adresse_non_locale': 'Cette adresse n’appartient pas à ce poste : choisissez une des adresses détectées.',
    'port_liaison_occupe': 'Le port de la liaison est déjà utilisé sur ce poste par un autre programme.',
    'chiffrement_indisponible': 'Le module de chiffrement est indisponible : la liaison ne peut pas fonctionner.',
}
PRESENCES = {'en_ligne': 'en ligne', 'absent': 'absent', 'hors_ligne': 'hors ligne'}
SERVICE = {'a_l_ecoute': 'à l’écoute', 'arrete': 'arrêtée', 'aucune_adresse_privee': 'aucune adresse privée détectée sur ce poste',
           'port_liaison_occupe': 'port déjà utilisé par un autre programme'}


def allowed(role, method, api):
    route = str(api or '').split('?', 1)[0]
    route = route[len(PREFIXE):] if route.startswith(PREFIXE) else ''
    if role not in ROLES:
        return False
    if method == 'GET':
        return route in ROUTES_GET
    if method != 'POST':
        return False
    return route in ROUTES_POST_TOUS or (role == 'administrateur' and route in ROUTES_POST_ADMIN)


def _out(value, status='200 OK'):
    return {'status': status, 'kind': 'application/json; charset=utf-8', 'body': json.dumps(value, ensure_ascii=False, default=str),
            'headers': [('Cache-Control', 'private, no-store')]}


def message(code):
    code = str(code or '')
    if code.startswith('adresse_non_privee'):
        return L.message_adresse(code)
    base = code.split(':')[0]
    if base in MESSAGES:
        return MESSAGES[base]
    from .erreurs5634 import explain
    return explain(base)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        brut = str(ex)
        code = brut.split(':')[0]
        if code in NEUTRES:
            code = brut = 'introuvable'
        return _out({'error': code, 'message': message(brut)}, STATUTS.get(code, '400 Bad Request'))


def _recu(desk, compte, route, cle, empreinte):
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_idempotence_requise')
    r = desk.db.execute('SELECT empreinte, reponse FROM liaison56103_recus WHERE compte=? AND route=? AND cle=?', (compte, route, cle)).fetchone()
    if r and r['empreinte'] != empreinte:
        raise Stop('cle_idempotence_deja_utilisee')
    return json.loads(r['reponse']) if r else None


def _garder(desk, compte, route, cle, empreinte, out):
    desk.db.execute('INSERT OR IGNORE INTO liaison56103_recus VALUES(?,?,?,?,?,?)', (compte, route, cle, empreinte, json.dumps(out, ensure_ascii=False),
                                                                                      L._maintenant()))
    desk.db.commit()


def _members(desk, auth):
    from .membres5650 import directory
    return L.augmenter(desk, directory(desk, auth))


def _check_binaire(env, auth):
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        length = int(env.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        raise Stop('requete_invalide') from None
    if length > L.MAX_PIECE:
        raise Stop('piece_trop_grande')
    if length <= 0:
        raise Stop('piece_vide')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/octet-stream':
        raise Stop('type_piece_invalide')
    return env['wsgi.input'].read(length)


def _api(env, desk, auth, prefix, name, args, method):
    from .web567 import actor
    from .membres5650 import viewer_hidden
    if not (auth or {}).get('poste'):
        raise Stop('liaison_reservee_au_poste')
    route = name[len(PREFIXE):].split('?', 1)[0] if name.startswith(PREFIXE) else ''
    me, role = actor(env)
    if not allowed(role, method, PREFIXE + route):
        raise Stop('route_inconnue' if route not in ROUTES_GET + ROUTES_POST_TOUS + ROUTES_POST_ADMIN else 'role_insuffisant')
    from .pilote_messagerie56102 import reservee
    if reservee(me):
        raise Stop('role_insuffisant')
    hidden = viewer_hidden()
    if method == 'GET':
        if route == 'etat':
            return _out(etat_public(desk, role))
        if route == 'fil':
            return _out(L.etat_fil(desk, me, args.get('id'), hidden))
        if route == 'piece':
            nom, donnees = L.lire_piece(desk, me, args.get('id'), hidden)
            from urllib.parse import quote
            return {'status': '200 OK', 'kind': 'application/octet-stream', 'body': donnees,
                    'headers': [('Cache-Control', 'private, no-store'), ('X-Content-Type-Options', 'nosniff'),
                                ('Content-Disposition', "attachment; filename*=UTF-8''" + quote(nom))]}
        raise Stop('route_inconnue')
    if route == 'piece':   # dépôt binaire : contrôle d'origine et CSRF AVANT la lecture du corps
        donnees = _check_binaire(env, auth)
        cle = str(env.get('HTTP_X_CLE_IDEMPOTENCE') or '')
        empreinte = hashlib.sha256(str(args.get('fil')).encode() + b'|' + hashlib.sha256(donnees).digest()).hexdigest()
        deja = _recu(desk, me, route, cle, empreinte)
        if deja is not None:
            return _out(deja)
        try:
            nom_fichier = unquote(env.get('HTTP_X_NOM_FICHIER', ''), encoding='utf-8', errors='strict')
        except UnicodeError:
            nom_fichier = ''
        members = _members(desk, auth)
        mon_nom = (members.get(me) or {}).get('name') or me
        out = L.deposer_piece(desk, me, mon_nom, role, args.get('fil'), donnees, nom_fichier, members, hidden)
        out['message_id'] = out.pop('message')
        out['message'] = 'Pièce jointe ajoutée (%s) ; elle part avec le message vers les postes reliés.' % L.taille_lisible(out['taille'])
        _garder(desk, me, route, cle, empreinte, out)
        return _out(out)
    from .web440 import check_post
    data = check_post(env, auth)
    if route == 'presence':
        etat = 'absent' if data.get('etat') == 'absent' else 'en_ligne'
        desk.setting(L.REGLAGE_PRESENCE, etat)
        return _out({'presence': etat, 'message': 'Présence : %s.' % PRESENCES[etat]})
    cle = data.get('cle')
    empreinte = hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != 'cle'}, sort_keys=True, default=str).encode()).hexdigest()
    deja = _recu(desk, me, route, cle, empreinte)
    if deja is not None:
        return _out(deja)
    out = _effet(desk, auth, me, role, route, data)
    _garder(desk, me, route, cle, empreinte, out)
    return _out(out)


def _effet(desk, auth, me, role, route, data):
    if route == 'activer':
        actif = data.get('actif') is True
        from .membres5650 import directory
        nom = (directory(desk, auth).get(me) or {}).get('name') or me
        out = L.activer(desk, me, nom, actif)
        return {**out, 'message': ('Liaison activée : ce poste ouvrira son port de liaison sur le réseau local d’ici quelques secondes.'
                                   if actif else 'Liaison désactivée : le port de liaison est fermé, rien ne part plus vers les autres postes.')}
    if route == 'nom':
        nom = L.nettoyer_nom(data.get('nom'), '')
        if not nom:
            raise Stop('nom_poste_requis')
        desk.setting(L.REGLAGE_NOM, nom)
        desk.audit('liaison56103_nom', {'nom': nom})
        return {'nom': nom, 'message': 'Nom du poste enregistré : « %s ».' % nom}
    if route == 'adresse':
        voulue = str(data.get('adresse') or '').strip()
        if voulue:
            voulue = L.verifier_adresse(voulue)
            if voulue not in L.adresses_locales():
                raise Stop('adresse_non_locale')
        desk.setting(L.REGLAGE_ADRESSE, voulue)
        return {'adresse': voulue, 'message': 'Adresse d’écoute enregistrée ; elle sera prise en compte au prochain démarrage de la liaison.'}
    if not L.est_active(desk):
        raise Stop('liaison_desactivee')
    if route == 'code':
        out = L.nouveau_code(desk)
        desk.audit('liaison56103_code_affiche', {'par': me})
        return {'code': out['code'], 'message': 'Code d’appairage : %s (10 minutes, 5 essais). Saisissez-le sur l’autre poste.' % out['code']}
    if route == 'appairer':
        out = L.appairer(desk, data.get('adresse'), data.get('port') or L.PORT_TCP, data.get('code'))
        return {**out, 'message': 'Poste « %s » appairé : les messages et la présence passent désormais directement entre les deux postes.' % out['nom']}
    if route == 'retirer':
        out = L.retirer(desk, data.get('id'), me)
        return {**out, 'message': 'Poste « %s » retiré : sa clé est oubliée et plus rien ne lui est envoyé.' % out['nom']}
    if route == 'diffuser':
        out = L.diffuser(desk, str(data.get('genre') or ''), str(data.get('reglage') or ''), me, role)
        n = len([x for x in out['postes'].values() if x == 'en_attente'])
        return {'gid': out['gid'], 'message': ('« %s » proposé à %d poste(s) relié(s) ; chacun décide de l’appliquer ou de le refuser.' % (out['libelle'], n))
                if n else 'Aucun poste relié ne peut le recevoir pour l’instant.'}
    if route in ('reglage/appliquer', 'reglage/refuser'):
        out = L.decider_reglage(desk, data.get('id'), route == 'reglage/appliquer', me, role)
        texte = {'applique': 'Réglage « %s » appliqué sur ce poste (journalisé).', 'refuse': 'Réglage « %s » refusé (journalisé).',
                 'echec': 'Le réglage « %s » n’a pas pu être appliqué : '}[out['etat']] % out['libelle']
        if out['etat'] == 'echec':
            texte += message(out['motif'])
        return {**out, 'message': texte}
    raise Stop('route_inconnue')


def etat_public(desk, role):
    """Lecture seule : aucune valeur secrète, aucun code pour un compte non administrateur."""
    actif = L.est_active(desk)
    try:
        ident = L.identite(desk.c['state_dir'], creer=False).id
    except Stop:
        ident = ''
    out = {'actif': actif, 'id': ident, 'nom': L.nom_du_poste(desk), 'presence': L.presence_locale(desk),
           'service': desk.settings(L.REGLAGE_SERVICE, {}) or {},
           'postes': [{'id': p['id'], 'nom': p['nom'], 'presence': L.presence_de(p), 'vu_le': p['contact_le'], 'adresse': p['adresse'],
                       'erreur': p['erreur']} for p in L.postes(desk)]}
    if role == 'administrateur':
        code = L.code_actif(desk.c['state_dir'])
        out['code'] = (code['code'][:4] + '-' + code['code'][4:]) if code else ''
    return out


# ------------------------------------------------------------------------------------------------ Paramètres
def _cle():
    return '<input type="hidden" name="cle" value="%s">' % secrets.token_urlsafe(18)


def _heure(iso):
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(iso).astimezone(ZoneInfo('Europe/Paris')).strftime('%d/%m %H:%M')
    except Exception:   # noqa: BLE001
        return ''


def _heure_ts(ts):
    from datetime import datetime, timezone
    return _heure(datetime.fromtimestamp(float(ts), timezone.utc).isoformat())


def _presence_html(p):
    etat = L.presence_de(p)
    texte = PRESENCES[etat]
    if etat == 'hors_ligne' and p.get('contact_le'):
        texte += ' (vu le %s)' % _heure(p['contact_le'])
    return '<span class="l103-presence l103-%s">%s</span>' % (e(etat), e(texte))


def section_html(desk, auth, prefix, env):
    """Paramètres › Confort et maintenance : rubrique « Liaison entre postes du cabinet » (poste de travail seulement)."""
    if not (auth or {}).get('poste'):
        return ''
    from .web567 import actor
    me, role = actor(env)
    admin = role == 'administrateur'
    actif = L.est_active(desk)
    intro = ('<p>Relie directement les postes AxiorHub du cabinet sur le réseau local, <strong>sans serveur</strong> : messages et discussions, '
             'pièces jointes (25 Mo au plus), présence, et réglages partagés par un poste administrateur. Les dossiers, l’agenda et les tâches '
             'ne passent pas par la liaison. Tout est chiffré et signé ; rien ne sort du réseau local. Un message pour un poste éteint attend '
             'sur ce poste et part à son retour.</p>')
    bascule = ('<form class="m5-form" data-api="m56103/liaison/activer" data-reload>%s<label class="m50-check"><input type="checkbox" name="actif"%s%s> '
               'Activer la liaison entre postes sur ce poste</label>%s<p role="status" class="m5-status"></p></form>') % (
        _cle(), ' checked' if actif else '', '' if admin else ' disabled', '<button class="ax-btn" type="submit">Enregistrer</button>' if admin else '')
    if not actif:
        return ('<section class="ax-card l103" id="liaison56103"><h2>Liaison entre postes du cabinet</h2>%s<p class="l103-etat">Désactivée : '
                'aucun port n’est ouvert au réseau.</p>%s</section>') % (intro, bascule)
    try:
        ident = L.identite(desk.c['state_dir'], creer=False).id
    except Stop:
        ident = ''
    service = desk.settings(L.REGLAGE_SERVICE, {}) or {}
    etat_service = SERVICE.get(service.get('etat'), 'démarrage en cours (quelques secondes)')
    ecoute = ('%s, port %s' % (service.get('adresse'), service.get('port'))) if service.get('etat') == 'a_l_ecoute' else '—'
    locales = L.adresses_locales()
    corps = ['<h3>Ce poste</h3><dl class="l103-dl"><dt>Empreinte</dt><dd><code>%s</code></dd><dt>Liaison</dt><dd>%s</dd><dt>Écoute</dt><dd>%s</dd>'
             '<dt>Adresses privées détectées</dt><dd>%s</dd></dl>' % (e(L.empreinte_lisible(ident)), e(etat_service), e(ecoute),
                                                                     e(', '.join(locales) or 'aucune'))]
    if admin:
        corps.append('<form class="m5-form l103-ligne" data-api="m56103/liaison/nom" data-reload>%s<label>Nom affiché aux autres postes'
                     '<input name="nom" maxlength="80" required value="%s"></label><button class="ax-btn ghost" type="submit">Renommer</button>'
                     '<p role="status" class="m5-status"></p></form>' % (_cle(), e(L.nom_du_poste(desk), quote=True)))
        corps.append('<p class="ax-muted">Pare-feu Windows : autorisez « AxiorHub Pilote » sur les réseaux privés quand Windows le demande '
                     '(port TCP %d pour la liaison, UDP %d pour la découverte). Voir la note d’installation de la liaison.</p>' % (L.port_ecoute(desk), L.PORT_UDP))
        code = L.code_actif(desk.c['state_dir'])
        corps.append('<h3>Appairer un autre poste</h3><p>1. Sur l’un des postes, cliquez sur « Afficher un code ». 2. Sur l’autre, choisissez '
                     'le poste découvert (ou saisissez son adresse) et tapez le code. Le code sert une seule fois, 10 minutes, 5 essais.</p>')
        corps.append('<form class="m5-form" data-api="m56103/liaison/code" data-reload>%s<button class="ax-btn" type="submit">Afficher un code</button>'
                     '<p role="status" class="m5-status"></p></form>' % _cle())
        if code:
            corps.append('<p class="l103-code" role="status">Code à saisir sur l’autre poste : <strong>%s-%s</strong> — %d essai(s) restant(s), '
                         'valable jusqu’à %s.</p>' % (e(code['code'][:4]), e(code['code'][4:]), int(code['essais']),
                                                       e(_heure_ts(code['expire']))))
        vus = [d for d in L.decouverts(desk) if not d['appaire']]
        if vus:
            lignes = ''.join(
                '<li><form class="m5-form l103-ligne" data-api="m56103/liaison/appairer" data-reload>%s<input type="hidden" name="adresse" value="%s">'
                '<input type="hidden" name="port" value="%d"><span><strong>%s</strong> — %s (empreinte %s)</span><label>Code<input name="code" '
                'inputmode="numeric" autocomplete="off" required maxlength="9" placeholder="1234-5678"></label><button class="ax-btn" type="submit">Appairer</button>'
                '<p role="status" class="m5-status"></p></form></li>' % (_cle(), e(d['adresse'], quote=True), int(d['port']), e(d['nom']), e(d['adresse']),
                                                                         e(L.empreinte_lisible(d['id'])[:19])) for d in vus)
            corps.append('<h4>Postes découverts sur le réseau</h4><ul class="l103-liste">%s</ul>' % lignes)
        else:
            corps.append('<p class="ax-muted">Aucun autre poste découvert pour l’instant (la découverte peut être bloquée par le réseau) : '
                         'utilisez l’adresse saisie à la main.</p>')
        corps.append('<h4>Adresse saisie à la main</h4><form class="m5-form l103-ligne" data-api="m56103/liaison/appairer" data-reload>%s'
                     '<label>Adresse IP de l’autre poste<input name="adresse" required maxlength="64" placeholder="adresse affichée sur l’autre poste"></label>'
                     '<label>Port<input name="port" inputmode="numeric" value="%d" maxlength="5"></label><label>Code<input name="code" inputmode="numeric" '
                     'autocomplete="off" required maxlength="9" placeholder="1234-5678"></label><button class="ax-btn" type="submit">Appairer</button>'
                     '<p role="status" class="m5-status"></p></form><p class="ax-muted">Seules les adresses privées du réseau local sont acceptées '
                     '(10.x.x.x, 172.16 à 172.31.x.x, 192.168.x.x ; en IPv6 fc00::/7, fe80::/10).</p>' % (_cle(), L.PORT_TCP))
    liste = L.postes(desk)
    if liste:
        lignes = []
        for p in liste:
            attente = desk.db.execute("SELECT COUNT(*) FROM liaison56103_sortie WHERE poste=? AND etat='en_attente'", (p['id'],)).fetchone()[0]
            retrait = ('<form class="m5-form" data-api="m56103/liaison/retirer" data-reload data-confirm="Retirer ce poste ? Sa clé sera oubliée ; '
                       'il faudra l’appairer de nouveau.">%s<input type="hidden" name="id" value="%s"><button class="ax-btn ghost" type="submit">Retirer</button>'
                       '<p role="status" class="m5-status"></p></form>' % (_cle(), e(p['id'], quote=True))) if admin else ''
            lignes.append('<tr><td><strong>%s</strong><br><code class="l103-empreinte">%s</code></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                e(p['nom'] or 'Poste relié'), e(L.empreinte_lisible(p['id'])), e(p['adresse'] or '—'), _presence_html(p),
                e(('%d message(s) en attente de remise' % attente) if attente else 'rien en attente') + (
                    (' — dernier essai : ' + e(message(p['erreur']))) if p['erreur'] else ''), retrait))
        corps.append('<h3>Postes appairés</h3><table class="ax-table l103-table"><thead><tr><th>Poste</th><th>Adresse</th><th>Présence</th>'
                     '<th>File d’envoi</th><th></th></tr></thead><tbody>%s</tbody></table>' % ''.join(lignes))
    else:
        corps.append('<h3>Postes appairés</h3><p>Aucun poste appairé.</p>')
    if admin:
        corps.append(_reglages_html(desk))
    return ('<section class="ax-card l103" id="liaison56103"><h2>Liaison entre postes du cabinet</h2>%s%s%s</section>'
            % (intro, bascule, ''.join(corps)))


def _reglages_html(desk):
    elements = L.catalogue(desk)
    lignes = []
    for it in elements:
        if it['partageable']:
            action = ('<form class="m5-form" data-api="m56103/liaison/diffuser" data-confirm="Proposer ce réglage à tous les postes appairés ? '
                      'Les secrets sont chiffrés pour chaque poste ; chaque poste décide de l’appliquer.">%s<input type="hidden" name="genre" value="%s">'
                      '<input type="hidden" name="reglage" value="%s"><button class="ax-btn ghost" type="submit">Diffuser aux postes</button>'
                      '<p role="status" class="m5-status"></p></form>' % (_cle(), e(it['genre'], quote=True), e(it['cle'], quote=True)))
        else:
            action = '<span class="ax-muted">%s</span>' % e(it['motif'])
        lignes.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            e(L.GENRES[it['genre']]), e(it['libelle']), e(('secret chiffré pour chaque poste : ' + ', '.join(it['secrets'])) if it['secrets'] else 'aucun secret'), action))
    html = ['<h3>Réglages partagés</h3><p>Seul un poste administrateur publie, un réglage à la fois. Les secrets (clés d’API) sont chiffrés de bout en '
            'bout pour chaque poste et ne sont jamais affichés ; les identifiants personnels (mots de passe de messagerie, jetons OAuth, sessions) ne '
            'sont jamais partagés. Chaque poste qui reçoit choisit d’appliquer ou de refuser ; chaque décision est journalisée.</p>']
    html.append(('<table class="ax-table l103-table"><thead><tr><th>Genre</th><th>Réglage</th><th>Secrets</th><th></th></tr></thead><tbody>%s</tbody></table>'
                 % ''.join(lignes)) if lignes else '<p class="ax-muted">Aucun réglage diffusable sur ce poste.</p>')
    noms = {r['id']: r['nom'] for r in desk.db.execute('SELECT id, nom FROM liaison56103_postes')}
    recus = desk.db.execute('SELECT * FROM liaison56103_reglages_recus ORDER BY id DESC LIMIT 30').fetchall()
    if recus:
        items = []
        for r in recus:
            if r['etat'] == 'en_attente':
                actions = ''.join('<form class="m5-form" data-api="m56103/liaison/%s" data-reload>%s<input type="hidden" name="id" value="%d">'
                                  '<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
                                      route, _cle(), r['id'], cls, label) for route, cls, label in (('reglage/appliquer', '', 'Appliquer'),
                                                                                                     ('reglage/refuser', ' ghost', 'Refuser')))
            else:
                actions = e({'applique': 'appliqué', 'refuse': 'refusé', 'echec': 'échec : ' + message(r['motif'])}.get(r['etat'], r['etat'])
                            + (' le %s' % _heure(r['decide_le']) if r['decide_le'] else ''))
            items.append('<li><strong>%s</strong> — %s, proposé par %s le %s <div class="l103-actions">%s</div></li>' % (
                e(r['libelle']), e(L.GENRES.get(r['genre'], r['genre'])), e(noms.get(r['poste']) or 'un poste relié'), e(_heure(r['recu_le'])), actions))
        html.append('<h4>Réglages reçus</h4><ul class="l103-liste">%s</ul>' % ''.join(items))
    faites = desk.db.execute('SELECT * FROM liaison56103_diffusions ORDER BY cree_le DESC LIMIT 20').fetchall()
    if faites:
        libelles = {'en_attente': 'en attente de remise', 'applique': 'appliqué', 'refuse': 'refusé', 'echec': 'échec', 'sans_cle': 'poste sans clé de chiffrement'}
        items = ''.join('<li><strong>%s</strong> (%s) : %s</li>' % (
            e(r['libelle']), e(_heure(r['cree_le'])), e(', '.join('%s : %s' % (noms.get(k) or 'poste', libelles.get(v, v))
                                                                 for k, v in json.loads(r['etats'] or '{}').items()) or 'aucun poste')) for r in faites)
        html.append('<h4>Réglages diffusés</h4><ul class="l103-liste">%s</ul>' % items)
    return ''.join(html)


# ------------------------------------------------------------------------------------------------ Messagerie instantanée
def page_html(desk, auth, prefix, tid):
    """Bandeau des postes reliés d'un fil (rempli par ``v56103-liaison.js``, lecture seule) ; rien hors poste ou liaison désactivée."""
    try:
        if not (auth or {}).get('poste') or not L.est_active(desk):
            return ''
    except Exception:   # noqa: BLE001
        return ''
    return ('<div class="l103-fil" id="l103-fil" data-fil="%s" data-max="%d" hidden></div><script defer src="%s"></script>' % (
        e(str(tid or ''), quote=True), L.MAX_PIECE, e(prefix + '/static/v56103-liaison.js', quote=True)))

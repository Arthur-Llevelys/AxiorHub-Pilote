"""5.6.97 (lot dossiers, § 10) : routes ``/api440/m57/dossiers*`` du registre unique des affaires.

Le compte et le rôle viennent de la requête authentifiée, jamais du corps (un ``acteur`` ou un ``cabinet_id`` envoyé par le navigateur
n'est pas lu). Chaque écriture exige CSRF, JSON et une clé d'idempotence ``cle``. Réponses : 201 à la création ; 422 champs invalides
(détail par champ) ; 409 doublon, version périmée ou clé réutilisée ; 404 neutre pour un dossier absent OU réservé ; 403 pour un rôle
insuffisant sur une route (sans désigner de dossier). Aucune route n'appelle un modèle : tout reste disponible IA en pause.

- ``GET  m57/dossiers`` : liste (``q``, ``mes``, ``statut``, ``nature``, ``responsable``) ;
- ``GET  m57/dossiers/proposition`` : référence révisable et emplacement proposé ;
- ``GET  m57/dossiers/<id>`` (+ ``/courriels``, ``/journal``) ;
- ``POST m57/dossiers`` : création (``message`` facultatif : créer depuis un courriel ; ``conflit`` : décision de l'avocat) ;
- ``PATCH m57/dossiers/<id>`` (ou ``POST …/<id>/modifier``) : modification avec ``version`` attendue ;
- ``POST m57/dossiers/<id>/courriels`` : classer un message (aucun déplacement IMAP) ;
- ``POST m57/dossiers/<id>/rangement`` : reprendre le rangement du même dossier ;
- ``POST m57/dossiers/conflits`` : vérification des conflits avant création ;
- ``GET|POST m57/dossiers/migration`` : simulation puis migration des dossiers existants (administrateur).

5.6.98 (fiche : onglets Parties, Juridiction, Fichiers ; ``fiche5698``) :
- ``GET  m57/dossiers/<id>/parties`` · ``POST …/parties`` (ajout, 201) · ``POST …/parties/<p>`` (modification, ``version``) ·
  ``POST …/parties/<p>/retirer`` (``version``) ; une partie adverse ajoutée relance la vérification des conflits (résultat dans la réponse) ;
- ``GET  m57/dossiers/<id>/instances`` · ``POST …/instances`` (201) · ``POST …/instances/<i>`` (``version``, et ``version_dossier`` pour
  l'instance principale) · ``POST …/instances/<i>/retirer`` ;
- ``GET  m57/dossiers/<id>/fichiers?rep=&page=`` : arborescence en lecture seule, bornée, sous le répertoire du dossier.
"""
from .common import Stop
from . import dossiers5697 as D

STATUS = {'champs_invalides': '422 Unprocessable Entity', 'message_invalide': '422 Unprocessable Entity',
          'cle_idempotence_absente': '422 Unprocessable Entity', 'utiliser_cloture': '409 Conflict',
          'reference_deja_utilisee': '409 Conflict', 'emplacement_deja_utilise': '409 Conflict', 'version_perimee': '409 Conflict',
          'cle_idempotence_deja_utilisee': '409 Conflict', 'alias_en_conflit': '409 Conflict',
          'partie_en_double': '409 Conflict', 'instance_principale': '409 Conflict',   # 5.6.98
          'introuvable': '404 Not Found', 'dossier_reserve': '404 Not Found', 'dossier_absent': '404 Not Found', 'route_inconnue': '404 Not Found',
          'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'acteur_requis': '403 Forbidden',
          'controle_acces_indisponible': '503 Service Unavailable', 'registre_dossiers_occupe': '503 Service Unavailable'}
MESSAGES = {'utiliser_cloture': 'Ouvrez Gestion → Clôture du dossier pour archiver ou rouvrir.',
            'champs_invalides': 'Certains champs sont à corriger.', 'reference_deja_utilisee': 'Cette référence est déjà utilisée.',
            'emplacement_deja_utilise': 'Ce répertoire est déjà celui d’un autre dossier : modifiez l’intitulé, la référence ou le répertoire.',
            'version_perimee': 'Ce dossier a changé depuis votre affichage : rechargez la page ; rien n’a été écrasé.',
            'cle_idempotence_deja_utilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
            'cle_idempotence_absente': 'Requête incomplète : rechargez la page et recommencez.',
            'introuvable': 'Dossier introuvable.', 'role_insuffisant': 'Action réservée à un autre rôle du cabinet.',
            'message_invalide': 'Courriel incomplet : rechargez la boîte de réception.',
            'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant.',
            'registre_dossiers_occupe': 'Registre des dossiers occupé : réessayez dans un instant.',
            'partie_en_double': 'Cette personne figure déjà dans le dossier avec ce rôle.',
            'instance_principale': 'L’instance principale porte la juridiction et le RG du dossier : désignez-en d’abord une autre.'}
ECRITURES = {'': ('administrateur', 'avocat', 'assistant'), 'conflits': ('administrateur', 'avocat', 'assistant'),
             'migration': ('administrateur',), 'types': ('administrateur',)}   # 5.6.100 (§ 13) : type Affaire / Cabinet / Dépôt
ECRITURES_DOSSIER = {'modifier': ('administrateur', 'avocat', 'assistant'), 'courriels': ('administrateur', 'avocat', 'assistant'),
                     'rangement': ('administrateur', 'avocat', 'assistant'),
                     'parties': ('administrateur', 'avocat', 'assistant'), 'instances': ('administrateur', 'avocat', 'assistant')}   # 5.6.98
SOUS_ELEMENTS = ('parties', 'instances')   # 5.6.98 : …/<id>/parties/<p>[/retirer], …/<id>/instances/<i>[/retirer]


def _route(route):
    route = route.split('?', 1)[0].strip('/')
    if route != 'dossiers' and not route.startswith('dossiers/'):
        return None
    return [p for p in route.split('/')[1:] if p]


def allowed(role, method, route):
    """Contrôle de rôle (sans état) appelé par ``standalone_auth`` : lecture pour les rôles du cabinet ; écritures déclarées ici."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    parts = _route(route)
    if parts is None:
        return False
    if method in ('GET', 'HEAD'):
        return parts[:1] != ['migration'] or role == 'administrateur'
    if method == 'POST' and len(parts) <= 1 and (parts[0] if parts else '') in ECRITURES:
        return role in ECRITURES[parts[0] if parts else '']
    if method == 'POST' and len(parts) == 2 and parts[1] in ECRITURES_DOSSIER:
        return role in ECRITURES_DOSSIER[parts[1]]
    if method == 'POST' and len(parts) in (3, 4) and parts[1] in SOUS_ELEMENTS and (len(parts) == 3 or parts[3] == 'retirer'):
        return role in ECRITURES_DOSSIER[parts[1]]
    if method == 'PATCH' and len(parts) == 1 and parts[0] not in ECRITURES:
        return role in ECRITURES_DOSSIER['modifier']
    return False


def _out(value, status='200 OK'):
    from .web440 import json_out
    return {**json_out(value, status), 'headers': [('Cache-Control', 'private, no-store')]}


def _erreur(ex):
    code = str(ex).split(':')[0]
    detail = getattr(ex, 'detail', None)
    corps = {'error': code, 'message': MESSAGES.get(code) or ''}
    if isinstance(detail, dict) and detail:
        corps['champs'] = detail
    if not corps['message']:
        from .erreurs5634 import explain
        corps['message'] = explain(code, None)
    return _out(corps, STATUS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        return _erreur(ex)


def _api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post
    from .web567 import actor
    owner, role = actor(env)
    acteur = (owner, role)
    parts = _route(name[len('m57/'):])
    if parts is None:
        raise Stop('route_inconnue')
    D.ensure_schema(desk)
    if method in ('GET', 'HEAD'):
        if not allowed(role, 'GET', name[len('m57/'):]):
            raise Stop('role_insuffisant')
        return _lecture(desk, acteur, parts, args)
    if method not in ('POST', 'PATCH'):
        raise Stop('route_inconnue')
    if not allowed(role, method, name[len('m57/'):]):
        raise Stop('role_insuffisant')
    data = check_post(env, auth)
    return _ecriture(desk, acteur, parts, data, method, prefix)


def _lecture(desk, acteur, parts, args):
    if not parts:
        filtres = {k: args.get(k) for k in ('q', 'statut', 'nature', 'responsable')}
        filtres['mes'] = str(args.get('mes') or '') == '1'
        return _out({'dossiers': D.lister(desk, acteur, filtres, int(args.get('limite') or 200)), 'migre': D.migration_faite(desk)})
    if parts == ['proposition']:
        ref = D.proposer_reference(desk)
        try:
            emplacement = D.emplacement_propose(desk, str(args.get('titre') or 'Nouveau dossier')[:160], ref)
        except Stop:
            emplacement = ''
        return _out({'reference': ref, 'emplacement': emplacement, 'racine': _racine(desk)})
    if parts == ['migration']:
        return _out(D.migrer(desk, acteur, simulation=True))
    if len(parts) == 1:
        return _out(D.lire(desk, acteur, parts[0]))
    if len(parts) == 2 and parts[1] == 'courriels':
        return _out({'courriels': D.messages_du_dossier(desk, acteur, parts[0])})
    if len(parts) == 2 and parts[1] == 'journal':
        return _out({'journal': D.journal(desk, acteur, parts[0])})
    if len(parts) == 2 and parts[1] in ('parties', 'instances', 'fichiers'):   # 5.6.98
        from . import fiche5698 as F
        if parts[1] == 'parties':
            data = F.parties(desk, acteur, parts[0])
            return _out({'dossier': data['dossier'], 'parties': data['parties'], 'groupes': [{'cle': c, 'libelle': l, 'parties': [p['id'] for p in ps]}
                                                                                         for c, l, ps in data['groupes']]})
        if parts[1] == 'instances':
            return _out(F.instances(desk, acteur, parts[0]))
        dossier = D.lire(desk, acteur, parts[0])
        if dossier['stockage']['etat'] != 'pret':
            return _out({'etat': 'en_preparation', 'motif': dossier['stockage']['motif'] or dossier['stockage']['libelle'], 'dossiers': [], 'fichiers': []})
        page = int(args.get('page')) if str(args.get('page') or '').isdigit() and int(args.get('page')) > 0 else 1
        return _out(F.arborescence(desk, dossier['stockage']['chemin'], str(args.get('rep') or ''), page))
    raise Stop('route_inconnue')


def _racine(desk):
    try:
        return D.racine(desk)
    except Stop:
        return ''


def ranger_maintenant(desk, ident):
    """Première tentative de rangement juste après la création (clic de l'avocat) ; une panne laisse l'opération en file."""
    try:
        D.traiter_rangements(desk, dossier=ident, limite=1)
    except Stop:
        pass
    except OSError:
        pass


def _ecriture(desk, acteur, parts, data, method, prefix):
    if method == 'POST' and not parts:
        conflit = data.get('conflit') if isinstance(data.get('conflit'), dict) else None
        D.controler_decision_conflit(desk, conflit)
        recu = D.creer(desk, acteur, data, data.get('cle'), message=data.get('message') if isinstance(data.get('message'), dict) else None)
        if not recu.get('rejoue'):
            parties = D.lire(desk, acteur, recu['id'])['parties']
            recu['conflits'] = D.lier_conflits(desk, acteur, recu['id'], conflit, parties)
            ranger_maintenant(desk, recu['id'])
            courant = D.lire(desk, acteur, recu['id'])
            recu['stockage'] = {**courant['stockage']}
            recu['message'] = 'Dossier créé · ' + courant['stockage']['libelle'].lower()
        recu['url'] = prefix + recu['url']
        return _out(recu, '200 OK' if recu.get('rejoue') else '201 Created')
    if method == 'POST' and parts == ['conflits']:
        return _out(D.verifier_conflits(desk, acteur, data))
    if method == 'POST' and parts == ['migration']:
        return _out(D.migrer(desk, acteur, simulation=data.get('simulation', True) not in (False, 0, '0', 'non', 'false'),
                             types=data.get('types') if isinstance(data.get('types'), dict) else None))
    if method == 'POST' and parts == ['types']:   # 5.6.100 (§ 13) : choix explicite, réversible, sans déplacement ni suppression
        from .rangement56100 import choisir
        return _out(choisir(desk, acteur, data.get('types'), data.get('cle')))
    ident = parts[0]
    if (method == 'PATCH' and len(parts) == 1) or (method == 'POST' and parts[1:] == ['modifier']):
        champs = data.get('champs') if isinstance(data.get('champs'), dict) else {k: v for k, v in data.items() if k in D.CHAMPS_MODIFIABLES}
        recu = D.modifier(desk, acteur, ident, data.get('version'), champs, data.get('cle'))
        return _out({**recu, 'url': prefix + recu['url']})
    if method == 'POST' and parts[1:] == ['courriels']:
        recu = D.rattacher_message(desk, acteur, ident, data.get('message'), data.get('cle'), data.get('version'), data.get('motif', ''),
                                   data.get('options') if isinstance(data.get('options'), dict) else None)
        return _out({**recu, 'url': prefix + recu['url']})
    if method == 'POST' and len(parts) >= 2 and parts[1] in SOUS_ELEMENTS:   # 5.6.98 : parties et instances de la fiche
        return _sous_element(desk, acteur, parts, data)
    if method == 'POST' and parts[1:] == ['rangement']:
        recu = D.reprendre_rangement(desk, acteur, ident, data.get('cle'))
        ranger_maintenant(desk, recu['dossier'])
        return _out({**recu, 'stockage': D.lire(desk, acteur, recu['dossier'])['stockage']})
    raise Stop('route_inconnue')


def _sous_element(desk, acteur, parts, data):
    """5.6.98 : ajout (201, rejeu 200), modification et retrait (version attendue) d'une partie ou d'une instance."""
    from . import fiche5698 as F
    ident, genre = parts[0], parts[1]
    champs = {k: data[k] for k in (F.CHAMPS_PARTIE if genre == 'parties' else F.CHAMPS_INSTANCE) if k in data}
    if len(parts) == 2:
        fn = F.ajouter_partie if genre == 'parties' else F.ajouter_instance
        recu = fn(desk, acteur, ident, champs, data.get('cle'))
        return _out(recu, '200 OK' if recu.get('rejoue') else '201 Created')
    if len(parts) == 4 and parts[3] == 'retirer':
        fn = F.retirer_partie if genre == 'parties' else F.retirer_instance
        return _out(fn(desk, acteur, ident, parts[2], data.get('version'), data.get('cle')))
    if len(parts) == 3:
        if genre == 'parties':
            return _out(F.modifier_partie(desk, acteur, ident, parts[2], data.get('version'), champs, data.get('cle')))
        return _out(F.modifier_instance(desk, acteur, ident, parts[2], data.get('version'), champs, data.get('cle'), data.get('version_dossier')))
    raise Stop('route_inconnue')


def page_route(env, cfg, auth, prefix, path, args, shell):
    """Pages ``/dossiers/nouveau`` et ``/dossiers/import`` : gabarit commun, script du lot chargé en ``defer``."""
    from html import escape
    from .desk import Desk
    from .web440 import page_out
    from . import dossiers_ui5697 as U
    desk = Desk(cfg)
    try:
        D.ensure_schema(desk)
        head = '<script defer src="%s/static/v5697-dossiers.js"></script>' % escape(prefix, quote=True)
        try:
            corps = U.page_nouveau(desk, env, auth, prefix, args) if path == '/dossiers/nouveau' else U.page_import(desk, env, auth, prefix, args)
            out = page_out(shell('Nouveau dossier' if path == '/dossiers/nouveau' else 'Import des dossiers', corps, prefix, auth['csrf'], '/dossiers', head))
        except Stop as ex:
            code = str(ex).split(':')[0]
            out = page_out(shell('Dossiers', '<p class="notice">%s</p><p><a class="ax-btn ghost" href="%s/dossiers">Retour aux dossiers</a></p>' % (
                escape(MESSAGES.get(code) or code), escape(prefix, quote=True)), prefix, auth['csrf'], '/dossiers'))
            out['status'] = STATUS.get(code, '400 Bad Request')
        out.setdefault('headers', []).append(('Cache-Control', 'private, no-store'))
        return out
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass

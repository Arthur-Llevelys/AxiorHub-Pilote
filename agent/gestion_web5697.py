"""5.6.97 (lot gestion, § 18 à § 21) : routes ``/api440/m5689/gestion/*``.

Le compte et le rôle viennent de la requête authentifiée, jamais du corps. Chaque écriture passe par ``check_post`` et porte une clé
d'idempotence ``cle`` ; les services refont leurs contrôles (garde commune ``acces5697`` avant toute lecture, droits fins, version attendue).
Réponses : 404 neutre pour un dossier absent OU réservé ; 403 rôle ou droit insuffisant ; 409 version périmée, clé réutilisée ou clôture
bloquée (avec la liste des points). Aucune route n'appelle un modèle : tout reste disponible quand l'IA est en pause.
"""
from .common import Stop

LECTURES = frozenset(('gestion/procedure', 'gestion/depots/vue', 'gestion/depots/fichier', 'gestion/depots/accuse-fichier', 'gestion/honoraires', 'gestion/honoraires/forfait',
                      'gestion/honoraires/sources', 'gestion/pieces', 'gestion/remises', 'gestion/remises/fichier', 'gestion/cloture', 'gestion/historique'))
ECRITURES = frozenset(('gestion/echeances/suivi', 'gestion/echeances/acquitter', 'gestion/depots/preparer', 'gestion/depots/accuse', 'gestion/honoraires/frais',
                       'gestion/honoraires/jalon', 'gestion/honoraires/brouillon', 'gestion/honoraires/synchroniser', 'gestion/pieces/demander', 'gestion/pieces/recevoir'))
# Décisions : avocat ou administrateur (un assistant prépare, il ne valide ni ne remet).
VALIDATIONS = frozenset(('gestion/echeances/saisir', 'gestion/echeances/decider', 'gestion/depots/declarer', 'gestion/honoraires/frais/valider', 'gestion/honoraires/jalon/facturable',
                         'gestion/pieces/qualifier', 'gestion/remises/remettre', 'gestion/remises/transmission', 'gestion/remises/acces', 'gestion/remises/revoquer',
                         'gestion/remises/verifier', 'gestion/remises/depot-client', 'gestion/cloture/cloturer', 'gestion/cloture/rouvrir',
                         'gestion/cloture/regulariser'))   # 5.6.100 (R10) : décision motivée sur un dossier archivé sans clôture
ADMINISTRATION = frozenset(('gestion/reglages/remises', 'gestion/reglages/source', 'gestion/restauration/paquet', 'gestion/restauration/verifier'))
# Droit de famille déclaré par route pour ``membres5650.category`` (écritures) ; « voir_honoraires » est un droit fin des lectures d'honoraires.
CATEGORIES = {'gestion/echeances/saisir': 'valider', 'gestion/echeances/decider': 'valider', 'gestion/echeances/suivi': 'agenda', 'gestion/depots/declarer': 'valider',
              'gestion/honoraires/frais': 'facturation', 'gestion/honoraires/frais/valider': 'facturation', 'gestion/honoraires/jalon': 'facturation',
              'gestion/honoraires/jalon/facturable': 'facturation', 'gestion/honoraires/brouillon': 'facturation', 'gestion/honoraires/synchroniser': 'facturation',
              'gestion/pieces/qualifier': 'valider', 'gestion/remises/remettre': 'envoyer', 'gestion/remises/depot-client': 'envoyer', 'gestion/remises/revoquer': 'envoyer',
              'gestion/cloture/cloturer': 'valider', 'gestion/cloture/rouvrir': 'valider', 'gestion/cloture/regulariser': 'valider', 'gestion/reglages/remises': 'reglages', 'gestion/reglages/source': 'reglages'}
STATUS = {'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found', 'role_insuffisant': '403 Forbidden', 'acteur_requis': '403 Forbidden',
          'origine_ou_csrf_refuse': '403 Forbidden', 'version_en_conflit': '409 Conflict', 'version_requise': '409 Conflict', 'cle_idempotence_deja_utilisee': '409 Conflict',
          'cloture_bloquee': '409 Conflict', 'controle_acces_indisponible': '503 Service Unavailable',
          'dossier_deja_archive': '409 Conflict', 'dossier_non_archive': '409 Conflict', 'cloture_deja_enregistree': '409 Conflict',   # 5.6.100
          'transaction_interrompue': '503 Service Unavailable'}
MESSAGES = {'introuvable': 'Élément introuvable.', 'role_insuffisant': 'Action réservée à un autre rôle ou à un autre droit du cabinet.',
            'version_en_conflit': 'Cette page n’est plus à jour : rechargez-la ; rien n’a été écrasé.', 'version_requise': 'Rechargez la page avant de décider.',
            'cle_idempotence_requise': 'Rechargez la page avant de renvoyer ce formulaire.', 'cle_idempotence_deja_utilisee': 'Ce formulaire a déjà servi pour une autre demande : rechargez la page.',
            'cloture_bloquee': 'Des points bloquent encore la clôture : traitez-les ou motivez la décision.',
            'dossier_deja_archive': 'Ce dossier est déjà archivé.', 'dossier_non_archive': 'Ce dossier n’est pas archivé.',
            'cloture_deja_enregistree': 'La décision de clôture de ce dossier est déjà enregistrée.',
            'transaction_interrompue': 'L’enregistrement a été interrompu : rien n’a été validé, réessayez.', 'source_requise': 'Indiquez la source de la date (pièce, page, courriel).',
            'preuve_accomplissement_requise': 'Indiquez la preuve de l’accomplissement : l’acquittement d’un rappel ne suffit pas.',
            'rappel_sur_echeance_confirmee_seulement': 'Un rappel ne porte que sur une échéance confirmée.', 'rappel_destinataire_et_date_requis': 'Un rappel exige un destinataire et une date.',
            'depot_non_declare': 'Déclarez d’abord le dépôt (canal, identifiant d’envoi, date).', 'reference_envoi_date_requis': 'Indiquez la référence, l’identifiant d’envoi et la date lus sur l’accusé.',
            'accuse_deja_rattache_a_un_autre_depot': 'Cet accusé est déjà rattaché à un autre dépôt.', 'manifeste_modifie': 'Un fichier a changé depuis le manifeste : rien n’a été gelé.',
            'source_desactivee': 'La source de facturation externe est désactivée.', 'essai_source_requis': 'Faites d’abord un essai réussi de la connexion.',
            'verification_requise_avant_rejeu': 'La réponse de l’outil est inconnue : vérifiez la facture avant de renvoyer.', 'partage_nextcloud_desactive': 'Le partage Nextcloud est désactivé dans les réglages.',
            'lien_public_non_autorise': 'Le cabinet n’autorise pas les liens publics.', 'identite_destinataire_a_verifier': 'Vérifiez l’identité du destinataire avant de remettre.',
            'document_interne_non_remissible': 'Un document interne ou confidentiel ne se remet pas au client.', 'destinataire_non_verifie': 'Le destinataire n’est pas un correspondant client du dossier : confirmez-le.',
            'type_fichier_refuse': 'Type de fichier refusé.', 'fichier_trop_volumineux': 'Fichier trop volumineux.', 'motif_obligatoire': 'Indiquez un motif.'}


def _route(name):
    return name[len('m5689/'):].split('?', 1)[0]


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in ADMINISTRATION:
        return role == 'administrateur'
    if route in VALIDATIONS:
        return role in ('administrateur', 'avocat')
    return route in LECTURES or route in ECRITURES


def droit_requis(route):
    return CATEGORIES.get(route.split('?', 1)[0])


def _erreur(error):
    from .erreurs5634 import explain
    from .web440 import json_out
    code = str(error).split(':')[0]
    out = {'error': code, 'message': MESSAGES.get(code) or explain(code, getattr(error, 'detail', None))}
    if code == 'cloture_bloquee':
        out['points'] = getattr(error, 'detail', [])
    return json_out(out, STATUS.get(code, '400 Bad Request'))


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        return _erreur(error)


def _fichier(raw, nom):
    from .livrables_export5689 import _disposition
    return {'status': '200 OK', 'kind': 'application/octet-stream', 'body': raw, 'headers': [('Content-Disposition', _disposition(nom))]}


def _api(env, desk, auth, prefix, name, args, method):
    from . import cloture5697, gestion5697 as g, honoraires5697, procedure5697, remises5697
    from .web440 import check_post, json_out
    from .web567 import actor
    acteur = actor(env)
    route = _route(name)
    if not allowed(acteur[1], route):
        raise Stop('role_insuffisant' if route in VALIDATIONS | ADMINISTRATION else 'route_inconnue')
    g.schema(desk)
    if method in ('GET', 'HEAD'):
        if route not in LECTURES:
            raise Stop('methode_non_autorisee')
        mid, ident = str(args.get('dossier') or ''), str(args.get('id') or '')
        if route == 'gestion/procedure':
            return json_out(procedure5697.registre(desk, acteur, mid))
        if route == 'gestion/depots/vue':
            return json_out(procedure5697.vue_depot(desk, acteur, ident))
        if route == 'gestion/depots/fichier':
            return _fichier(*procedure5697.ouvrir_fichier(desk, acteur, ident, args.get('rang') or 0))
        if route == 'gestion/depots/accuse-fichier':
            return _fichier(*procedure5697.ouvrir_accuse(desk, acteur, ident, args.get('accuse')))
        if route == 'gestion/honoraires':
            return json_out(honoraires5697.indicateurs(desk, acteur, mid or None, str(args.get('debut') or ''), str(args.get('fin') or '')))
        if route == 'gestion/honoraires/forfait':
            return json_out(honoraires5697.forfait(desk, acteur, mid))
        if route == 'gestion/honoraires/sources':
            g.droit(desk, acteur, 'voir_honoraires')
            return json_out({'etat': honoraires5697.etat_sources(desk), 'reglages': honoraires5697.reglages(desk)})
        if route == 'gestion/pieces':
            return json_out({'demandes': remises5697.pieces_demandees(desk, acteur, mid)})
        if route == 'gestion/remises':
            return json_out({'remises': remises5697.remises(desk, acteur, mid)})
        if route == 'gestion/remises/fichier':
            return _fichier(*remises5697.ouvrir_version_remise(desk, acteur, ident, args.get('rang') or 0))
        if route == 'gestion/cloture':
            return json_out({'controle': cloture5697.preparer(desk, acteur, mid), 'etat': cloture5697.etat(desk, acteur, mid)})
        return json_out({'journal': g.historique(desk, acteur, mid, str(args.get('objet') or ''))})
    data = _formulaire(check_post(env, auth))
    mid, ident = str(data.get('dossier') or ''), str(data.get('id') or '')
    if route == 'gestion/echeances/saisir':
        return json_out(procedure5697.saisir_echeance(desk, acteur, mid, data, _agenda(desk, data)))
    if route == 'gestion/echeances/decider':
        return json_out(procedure5697.decider(desk, acteur, ident, data, _agenda(desk, data)))
    if route == 'gestion/echeances/suivi':
        return json_out(procedure5697.attribuer(desk, acteur, ident, data))
    if route == 'gestion/echeances/acquitter':
        return json_out(procedure5697.acquitter_rappel(desk, acteur, ident))
    if route == 'gestion/depots/preparer':
        return json_out(procedure5697.preparer_paquet(desk, acteur, mid, data))
    if route == 'gestion/depots/declarer':
        return json_out(procedure5697.declarer_depot(desk, acteur, ident, data))
    if route == 'gestion/depots/accuse':
        return json_out(procedure5697.importer_accuse(desk, acteur, ident, data))
    if route == 'gestion/honoraires/frais':
        return json_out(honoraires5697.saisir_frais(desk, acteur, mid, data))
    if route == 'gestion/honoraires/frais/valider':
        return json_out(honoraires5697.valider_frais(desk, acteur, ident))
    if route == 'gestion/honoraires/jalon':
        return json_out(honoraires5697.ajouter_jalon(desk, acteur, mid, data))
    if route == 'gestion/honoraires/jalon/facturable':
        return json_out(honoraires5697.jalon_atteint(desk, acteur, ident, data.get('version')))
    if route == 'gestion/honoraires/brouillon':
        return json_out(honoraires5697.preparer_brouillon(desk, acteur, mid, data))
    if route == 'gestion/honoraires/synchroniser':
        return json_out(honoraires5697.synchroniser(desk, acteur, honoraires5697.TransportInvoiceNinja(desk)))
    if route == 'gestion/pieces/demander':
        return json_out(remises5697.demander(desk, acteur, mid, data))
    if route == 'gestion/pieces/recevoir':
        return json_out(remises5697.recevoir(desk, acteur, ident, str(data.get('item') or ''), data))
    if route == 'gestion/pieces/qualifier':
        return json_out(remises5697.qualifier(desk, acteur, ident, str(data.get('item') or ''), str(data.get('decision') or '')))
    if route == 'gestion/remises/remettre':
        return json_out(remises5697.remettre(desk, acteur, mid, data))
    if route == 'gestion/remises/transmission':
        return json_out(remises5697.declarer_transmission(desk, acteur, ident, data))
    if route == 'gestion/remises/acces':
        return json_out(remises5697.constater_acces(desk, acteur, ident, data))
    if route == 'gestion/remises/revoquer':
        return json_out(remises5697.revoquer(desk, acteur, ident))
    if route == 'gestion/remises/verifier':
        return json_out(remises5697.verifier(desk, acteur, ident))
    if route == 'gestion/remises/depot-client':
        return json_out(remises5697.ouvrir_depot_client(desk, acteur, mid, data))
    if route == 'gestion/cloture/cloturer':
        return json_out(cloture5697.cloturer(desk, acteur, mid, data))
    if route == 'gestion/cloture/rouvrir':
        return json_out(cloture5697.rouvrir(desk, acteur, mid, data))
    if route == 'gestion/cloture/regulariser':   # 5.6.100 (R10)
        return json_out(cloture5697.regulariser(desk, acteur, mid, data))
    if route == 'gestion/reglages/remises':
        return json_out(remises5697.enregistrer_reglages(desk, acteur, data))
    if route == 'gestion/reglages/source':
        if data.get('essai'):
            return json_out(honoraires5697.essayer_source(desk, acteur, honoraires5697.TransportInvoiceNinja(desk)))
        return json_out(honoraires5697.activer_source(desk, acteur, str(data.get('source') or ''), data.get('actif') is True))
    if route == 'gestion/restauration/paquet':
        out = cloture5697.paquet_restauration(desk, acteur, mid)
        return json_out({k: v for k, v in out.items() if k != 'chemin'} | {'paquet': out['chemin'].replace('\\', '/').rsplit('/', 1)[-1]})
    if route == 'gestion/restauration/verifier':
        return json_out(_verifier_dernier(desk, acteur, mid, str(data.get('paquet') or '')))
    raise Stop('route_inconnue')


def _formulaire(data):
    """Formulaire à plat de la fiche (``gestion_ui5697``) → données des services : champs « a.b » regroupés, liste d'éléments par ligne,
    fichier lu par le navigateur (adresse de données) → nom et contenu en base64, cases à cocher en booléens. Un corps JSON déjà structuré
    passe tel quel."""
    out = {}
    for k, v in data.items():
        if '.' in k:
            groupe, champ = k.split('.', 1)
            if str(v).strip():
                out.setdefault(groupe, {})[champ] = v
        else:
            out[k] = v
    if 'elements_texte' in out and 'elements' not in out:
        out['elements'] = [x.strip(' -—•	') for x in str(out.pop('elements_texte')).splitlines() if x.strip()]
    f = out.get('fichier')
    if isinstance(f, dict) and 'contenu_b64' not in out:
        contenu = str(f.get('contenu') or '')
        out['contenu_b64'] = contenu.split(',', 1)[1] if contenu.startswith('data:') and ',' in contenu else contenu
        out.setdefault('nom', str(f.get('nom') or ''))
        out.setdefault('nom_fichier', str(f.get('nom') or ''))
    for k in ('depart_certain', 'identite_verifiee', 'confirmer_destinataire'):
        if out.get(k) in ('true', 'on', '1'):
            out[k] = True
    return out


def _agenda(desk, data):
    """Même client que les routes d'échéances existantes (``web450``) : l'inscription à l'agenda suit le réglage d'autonomie du cabinet."""
    from . import office440
    return office440.dav_client(desk)


def _verifier_dernier(desk, acteur, mid, nom):
    """Vérification d'un paquet de restauration dans une instance isolée (dossier temporaire supprimé ensuite)."""
    import re
    import tempfile
    from pathlib import Path
    from . import cloture5697, gestion5697 as g, membres5650
    g.dossier(desk, acteur, mid)
    if not re.fullmatch(r'rst-[0-9a-f]{32}\.zip', nom):
        raise Stop('introuvable')
    chemin = Path(desk.c['state_dir']) / cloture5697.DOSSIER_RESTAURATION / nom
    if not chemin.is_file():
        raise Stop('introuvable')
    attendus = membres5650.load(desk.c['state_dir'])['reserves'].get(str(mid)) or []
    with tempfile.TemporaryDirectory(prefix='restauration5697-') as tmp:
        return cloture5697.verifier_restauration(chemin, Path(tmp) / 'instance', mid, attendus)


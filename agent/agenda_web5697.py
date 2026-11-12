"""5.6.97 (lot agenda) : routes ``/api440/m5689/agenda*`` (grille, éditeur compact, salons Talk, Speakr, sources et réglages).

Le compte et le rôle viennent de la requête authentifiée (``web567.actor``), jamais du corps. Chaque écriture passe par ``check_post``
(origine et jeton CSRF) ; le droit « Gérer l'agenda » est contrôlé par ``membres5650`` (préfixe ``m5689/agenda``), les réglages du cabinet par
le droit « Réglages » (``m5689/agenda/reglages/``) et ici encore (administrateur pour Talk, Google et l'adresse Speakr du cabinet).
Réponses : 201 créé et vérifié, 202 accepté avec reçu (synchronisation ou salon en attente), 200 rejeu, 404 neutre (dossier réservé ou
objet absent), 409 version périmée ou clé réutilisée pour un autre contenu, 422 horaire à corriger (heure sautée ou répétée).
"""
from datetime import datetime, timedelta, timezone

from .common import Stop

ROLES = ('administrateur', 'avocat', 'assistant')
LECTURE = ('agenda', 'agenda/evenement', 'agenda/recu', 'agenda/sources')
ECRITURE = ('agenda/evenements', 'agenda/evenements/modifier', 'agenda/salon/relancer', 'agenda/salon/participants', 'agenda/sortie/relancer',
            'agenda/preferences', 'agenda/reglages/speakr')
REGLAGES = ('agenda/reglages/talk', 'agenda/reglages/google')          # administrateur
STATUTS = {'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'introuvable': '404 Not Found', 'route_inconnue': '404 Not Found',
           'evenement_absent': '404 Not Found', 'calendrier_absent': '404 Not Found', 'version_perimee': '409 Conflict', 'evenement_modifie_entre_temps': '409 Conflict',
           'cle_idempotence_deja_utilisee': '409 Conflict', 'conflit_version_distante': '409 Conflict', 'horaire_inexistant': '422 Unprocessable Entity',
           'horaire_ambigu': '422 Unprocessable Entity'}
MESSAGES = {'introuvable': 'Élément introuvable.', 'cle_idempotence_deja_utilisee': 'Cette saisie a déjà été enregistrée avec un autre contenu : rouvrez l’éditeur pour une nouvelle opération.',
            'evenement_modifie_entre_temps': 'Cet événement a été modifié entre-temps dans une autre session : rechargez-le avant d’enregistrer.',
            'version_perimee': 'Cet événement a été modifié entre-temps dans une autre session : rechargez-le avant d’enregistrer.',
            'version_requise': 'Version de l’événement absente : rechargez l’éditeur.', 'fin_avant_debut': 'La fin doit être après le début.',
            'duree_evenement_invalide': 'Durée invalide (un jour au plus, ou 31 jours pour une journée entière).', 'titre_evenement_invalide': 'Indiquez un titre (2 à 200 caractères).',
            'cle_idempotence_requise': 'Rouvrez l’éditeur : la clé de cette saisie est absente.', 'lien_visio_invalide': 'Le lien de visio doit être une adresse HTTPS, sans identifiant.',
            'calendrier_en_lecture_seule': 'Ce calendrier est en lecture seule : choisissez une destination modifiable.',
            'google_agenda_desactive': 'Google Agenda est désactivé : choisissez une autre destination.', 'edition_serie_non_prise_en_charge':
            'La modification d’une série récurrente n’est pas prise en charge ici : modifiez-la dans son calendrier d’origine.',
            'adresse_speakr_https_requise': 'L’adresse de Speakr doit commencer par https://.', 'adresse_speakr_invalide':
            'Adresse Speakr refusée : ni identifiant, ni mot de passe, ni paramètre, ni fragment.', 'tache_texte_invalide': 'Le titre de la tâche est trop court.',
            'responsable_inconnu': 'Responsable inconnu.', 'personne_sans_acces_au_dossier': 'Cette personne n’a pas accès au dossier.',
            'participant_inconnu': 'Ce compte n’est pas un membre identifié du cabinet.', 'salon_talk_pas_pret': 'Le salon n’est pas encore prêt.',
            'confirmation_requise': 'Confirmez l’ajout des participants.', 'preference_agenda_invalide': 'Préférence invalide.', 'fuseau_invalide': 'Fuseau horaire inconnu.'}


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ROLES:
        return False
    if route in REGLAGES:
        return role == 'administrateur'
    return route in LECTURE + ECRITURE


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    try:
        return _route(env, desk, auth, prefix, name, args, method)
    except Stop as error:
        code = str(error).split(':')[0]
        corps = {'error': code, 'message': MESSAGES.get(code) or _explique(code)}
        if getattr(error, 'detail', None) and code in ('horaire_inexistant', 'horaire_ambigu'):
            corps.update(detail=error.detail, message=error.detail.get('message', corps['message']))
        if code == 'conflit_version_distante':
            corps['distant'] = getattr(error, 'distant', None)
        return json_out(corps, STATUTS.get(code, '400 Bad Request'))


def _explique(code):
    try:
        from .erreurs5634 import explain
        return explain(code, None)
    except Exception:   # noqa: BLE001
        return 'Demande refusée (%s).' % code


def _http(resultat):
    return {201: '201 Created', 202: '202 Accepted', 200: '200 OK'}.get(resultat.get('http', 200), '200 OK')


def _periode(args, desk):
    from .agenda5697 import zone_de
    tz = zone_de(desk)
    try:
        debut = datetime.fromisoformat(str(args.get('debut') or ''))
        fin = datetime.fromisoformat(str(args.get('fin') or ''))
    except ValueError:
        raise Stop('periode_invalide') from None
    debut = debut if debut.tzinfo else debut.replace(tzinfo=tz)
    fin = fin if fin.tzinfo else fin.replace(tzinfo=tz)
    if not debut < fin <= debut + timedelta(days=62):
        raise Stop('periode_invalide')
    return debut, fin


def _route(env, desk, auth, prefix, name, args, method):
    from . import agenda5697, calendriers5697, rendezvous5697
    from .acces5697 import acteur_de
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    acteur = acteur_de((owner, role))
    route = name[len('m5689/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant' if route in LECTURE + ECRITURE + REGLAGES else 'route_inconnue')
    if method == 'GET':
        if route == 'agenda':      # résultats par source, état honnête de chacune
            debut, fin = _periode(args, desk)
            voulues = [x for x in str(args.get('calendriers') or 'local').split(',') if x]
            out = []
            if 'local' in voulues:
                out.append({'source': 'local', 'etat': 'a_jour', 'evenements': agenda5697.locaux(desk, acteur, debut, fin)})
            for s in calendriers5697.sources(desk, owner):
                if s['id'] in voulues and s['id'] != 'local' and s['lecture'] and s['fournisseur'] in ('caldav', 'google'):
                    rows, etat = calendriers5697.lire_cible(desk, acteur, s['id'], debut, fin, rafraichir=args.get('rafraichir') == '1')
                    out.append({'source': s['id'], 'etat': etat.get('etat') or 'jamais_lu', 'derniere_synchro': etat.get('derniere_reussite', ''),
                                'message': etat.get('erreur', ''), 'evenements': [{k: r[k] for k in ('id', 'uid', 'title', 'location', 'starts', 'ends', 'matter')} for r in rows]})
            return json_out({'sources': out, 'en_attente': calendriers5697.en_attente(desk, acteur, debut, fin), 'curseur': ''})
        if route == 'agenda/evenement':
            ident = str(args.get('id') or '')
            if ident.startswith('sortie:'):
                r = calendriers5697.recu(desk, ident[7:])
                from .acces5697 import exiger
                exiger(desk, acteur, r['dossier'])
                return json_out({'distant': True, 'recu': r, 'modifiable': False})
            if ident.startswith('cache:'):
                return json_out(_cache(desk, acteur, ident[6:]))
            detail = agenda5697.evenement(desk, acteur, ident)
            if detail.get('nature') == 'rendez_vous':   # § 16 (lot documents-mission) : « Nouvelle demande » depuis la fiche du rendez-vous
                detail['nouvelle_demande'] = _lien_demande(prefix, detail['id'])
            return json_out(detail)
        if route == 'agenda/recu':
            r = calendriers5697.recu(desk, str(args.get('id') or ''))
            from .acces5697 import exiger
            exiger(desk, acteur, r['dossier'])
            return json_out(r)
        if route == 'agenda/sources':
            return json_out({'sources': calendriers5697.sources(desk, owner)})
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if route == 'agenda/evenements':
        r = agenda5697.creer(desk, acteur, data, auth)
        return json_out(r, _http(r))
    if route == 'agenda/evenements/modifier':
        if not str(data.get('version') or ''):
            raise Stop('version_requise')
        return json_out(agenda5697.modifier(desk, acteur, data, auth))
    if route == 'agenda/salon/relancer':
        return json_out(rendezvous5697.relancer(desk, acteur, str(data.get('evenement') or '')) or {})
    if route == 'agenda/salon/participants':
        comptes = data.get('comptes') if isinstance(data.get('comptes'), list) else []
        return json_out(rendezvous5697.ajouter_participants(desk, acteur, str(data.get('evenement') or ''), comptes, data.get('confirme') is True, auth=auth))
    if route == 'agenda/sortie/relancer':
        r = calendriers5697.recu(desk, str(data.get('id') or ''))
        from .acces5697 import exiger
        exiger(desk, acteur, r['dossier'])
        if r['etat'] in ('abandon', 'echec'):
            calendriers5697._maj(desk, r['id'], etat='confirmation_inconnue', tentatives=0, prochain='')   # relecture d'abord, jamais un envoi aveugle
        return json_out(calendriers5697.executer(desk, r['id']))
    if route == 'agenda/preferences':
        return json_out({'preferences': agenda5697.enregistrer_preferences(desk, owner, data), 'message': 'Préférences enregistrées pour votre compte.'})
    if route == 'agenda/reglages/speakr':
        return json_out(rendezvous5697.speakr_regler(desk, acteur, str(data.get('portee') or ''), data.get('url')))
    if route == 'agenda/reglages/talk':
        return json_out(dict(rendezvous5697.regler_talk(desk, owner, data.get('actif')), message='Réglage Talk enregistré.'))
    if route == 'agenda/reglages/google':
        return json_out(dict(calendriers5697.regler_google(desk, owner, data.get('actif')), message='Réglage Google Agenda enregistré.'))
    raise Stop('route_inconnue')


def _lien_demande(prefix, event_id):
    """Lien du lot documents-mission (même formulaire partout) rendu en données : le script crée le lien sans insérer de HTML."""
    import html
    import re
    from .mission_web5697 import lien_nouvelle_demande
    brut = lien_nouvelle_demande(prefix, 'rendez_vous', event_id)
    href = re.search(r'href="([^"]*)"', brut)
    texte = re.search(r'>([^<]*)</a>', brut)
    return {'href': html.unescape(href.group(1)) if href else '', 'libelle': html.unescape(texte.group(1)) if texte else 'Nouvelle demande'}


def _cache(desk, acteur, ident):
    """Détail d'un événement lu dans un calendrier en ligne : lecture seule ici, source indiquée."""
    from .acces5697 import NEUTRE, peut_lire_dossier
    from .agenda5634 import _tz
    row = desk.db.execute('SELECT * FROM calendar_cache WHERE id=?', (ident,)).fetchone()
    if not row or not peut_lire_dossier(desk, acteur, row['matter']) or row['calendar_url'] == 'local:agenda':
        raise Stop(NEUTRE)
    tz = _tz(desk)
    a, b = datetime.fromisoformat(row['starts']).astimezone(tz), datetime.fromisoformat(row['ends']).astimezone(tz)
    source = 'Agenda en ligne' if not row['calendar_url'].startswith('agenda5697:') else 'Calendrier en ligne'
    return {'id': 'cache:' + row['id'], 'title': row['title'], 'location': row['location'], 'description': row['description'], 'matter': row['matter'],
            'debut_local': a.strftime('%Y-%m-%dT%H:%M'), 'fin_local': b.strftime('%Y-%m-%dT%H:%M'), 'modifiable': False, 'serie': bool(row['recurrence_id']),
            'source_label': source, 'lu_le': row['fetched'], 'distant': True}

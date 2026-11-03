"""5.6.93 : routes ``/api440/m5689/charge*`` et section Paramètres › Confort et maintenance › Charge du serveur.

Lecture (tous les rôles) : travaux par type sur 1 h et 24 h, requêtes au stockage, commandes IMAP, lectures, extractions, empreintes et
appels au modèle, dernier tour complet des documents, travaux en cours. Aucun contenu de dossier, aucun nom de fichier, aucun argument de
travail n'est affiché ni renvoyé. Écriture : fréquence de surveillance des documents, droit « réglages » du cabinet, contrôle CSRF
(``check_post``) et révision lue par le formulaire (409 si elle est périmée)."""
from datetime import datetime, timezone
from html import escape as e
from zoneinfo import ZoneInfo

from . import charge5693 as charge
from .common import Stop

LECTURE = ('charge',)
ECRITURE = ('charge/frequence',)


def allowed(role, route):
    return route in ECRITURE and role in ('administrateur', 'avocat')


def _erreur(error):
    from .erreurs5634 import explain
    from .web import REASONS
    from .web440 import json_out
    code = str(error).split(':')[0]
    statut = ('403 Forbidden' if code == 'role_insuffisant' else '409 Conflict' if code == 'revision_perimee' else
              '404 Not Found' if code == 'route_inconnue' else '400 Bad Request')
    return json_out({'error': code, 'message': REASONS.get(code) or explain(code, getattr(error, 'detail', None))}, statut)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _route(env, desk, auth, name, method)
    except Stop as error:
        return _erreur(error)


def _route(env, desk, auth, name, method):
    from .horaires_web5689 import peut_modifier
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5689/'):].split('?', 1)[0]
    if method == 'GET':
        if route not in LECTURE:
            raise Stop('route_inconnue')
        return json_out(charge.etat(desk))
    if method != 'POST':
        raise Stop('methode_refusee')
    if route not in ECRITURE:
        raise Stop('route_inconnue')
    data = check_post(env, auth)
    if not peut_modifier(desk.c['state_dir'], owner, role):
        raise Stop('role_insuffisant')
    etat = charge.enregistrer_frequence(desk, str(data.get('valeur') or ''), data.get('revision'), owner)
    return json_out(etat)


# ---------------------------------------------------------------- écran
def _libelle(kind):
    try:
        from .web import JOB_LABELS
        return JOB_LABELS.get(kind) or kind
    except Exception:   # noqa: BLE001 — libellés indisponibles : le nom technique reste lisible
        return kind


def _heure(texte):
    if not texte:
        return ''
    try:
        valeur = datetime.fromisoformat(str(texte).replace('Z', '+00:00'))
    except ValueError:
        return ''
    valeur = valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo('Europe/Paris')
    except Exception:   # noqa: BLE001 — base des fuseaux absente : heure UTC, dite comme telle
        return valeur.strftime('%d/%m/%Y %H:%M') + ' (UTC)'
    return valeur.astimezone(zone).strftime('%d/%m/%Y %H:%M')


def _nombre(valeur):
    return format(int(valeur), ',').replace(',', ' ')


def section_html(desk, prefix, owner, role):
    from .horaires_web5689 import peut_modifier
    etat = charge.etat(desk)
    reglage = etat['reglage']
    ecriture = peut_modifier(desk.c['state_dir'], owner, role)
    off = '' if ecriture else ' disabled'
    choix = ''.join('<label class="m50-check"><input type="radio" name="valeur" value="%s"%s%s> <strong>%s</strong> — %s</label>' % (
        n, ' checked' if reglage['valeur'] == n else '', off, e(charge.LIBELLES[n]), e(charge.EXPLICATIONS[n])) for n in charge.NIVEAUX)
    impose = ('<p class="ax-note">Le régime économe est activé (Paramètres › Modèles d’IA) : le réglage « Économe » au moins s’applique.</p>'
              if reglage['econome_impose'] else '')
    droit = '' if ecriture else '<p class="ax-note">Consultation seule : ce réglage demande le droit « Réglages » du cabinet.</p>'
    formulaire = ('<form class="m5-form" data-api="m5689/charge/frequence" data-reload="1"><fieldset><legend>Fréquence de surveillance des documents</legend>%s'
                  '<input type="hidden" name="revision" value="%d"></fieldset>%s<p class="ax-muted">En vigueur : <strong>%s</strong>. Les courriels restent '
                  'relevés toutes les 5 minutes (et dès leur arrivée quand la messagerie le permet), quel que soit ce réglage.</p>%s</form>') % (
        choix, reglage['revision'], impose, e(charge.LIBELLES[reglage['effectif']]),
        '<button class="ax-btn" type="submit">Enregistrer</button>' if ecriture else '')
    lignes = ''.join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(_libelle(t['kind'])), _nombre(t['heure']), _nombre(t['jour']), _nombre(t['automatiques_jour'])) for t in etat['travaux'][:30])
    travaux = ('<table class="vf-table"><caption>Travaux créés</caption><thead><tr><th scope="col">Travail</th><th scope="col">Dernière heure</th>'
               '<th scope="col">24 heures</th><th scope="col">dont automatiques (24 h)</th></tr></thead><tbody>%s</tbody></table>' % lignes
               if lignes else '<p class="ax-muted">Aucun travail créé depuis 24 heures.</p>')
    if etat['mesures'] is None:
        mesures = '<p class="ax-alert" role="status">Mesures indisponibles : la base n’a pas pu être lue. Aucun chiffre n’est affiché plutôt qu’un faux zéro.</p>'
    else:
        rangs = ''.join('<tr><th scope="row">%s</th><td>%s</td><td>%s</td></tr>' % (e(libelle), _nombre(etat['mesures'][cle]['heure']), _nombre(etat['mesures'][cle]['jour']))
                        for cle, libelle in charge.CATEGORIES)
        modeles = etat['modeles']
        rangs += '<tr><th scope="row">Appels au modèle d’IA</th><td>%s</td><td>%s</td></tr>' % (
            (_nombre(modeles['heure']), _nombre(modeles['jour'])) if modeles else ('non mesuré', 'non mesuré'))
        mesures = ('<table class="vf-table"><caption>Lectures et calculs (mesurés depuis la 5.6.93)</caption><thead><tr><th scope="col">Mesure</th>'
                   '<th scope="col">Dernière heure</th><th scope="col">24 heures</th></tr></thead><tbody>%s</tbody></table>' % rangs)
        if etat['stockage_par_travail']:
            mesures += '<p class="ax-muted">Requêtes au stockage par travail (24 h) : %s.</p>' % e(', '.join(
                '%s %s' % (_libelle(k) if k != 'hors_travail' else 'hors travaux (pages, planificateur)', _nombre(n)) for k, n in etat['stockage_par_travail']))
    tour = ('Dernier tour complet des documents : <strong>%s</strong>%s.' % (e(_heure(etat['dernier_tour'])), ' ; un tour est en cours' if etat['tour_en_cours'] else '')
            if etat['dernier_tour'] else 'Aucun tour complet des documents n’est encore terminé%s.' % (' ; un tour est en cours' if etat['tour_en_cours'] else ''))
    suivi = ('%d dossier(s) suivi(s) par l’empreinte de leur répertoire (ETag Nextcloud)%s.' % (
        etat['dossiers_suivis'], ('; plus ancienne relecture complète de sécurité : ' + _heure(etat['plus_ancienne_relecture'])) if etat['plus_ancienne_relecture'] else '')
        if etat['dossiers_suivis'] else 'Suivi par ETag inactif (stockage autre que Nextcloud, ou premier tour pas encore fait) : chaque tour relit les répertoires.')
    en_cours = ''.join('<li>%s — depuis %s</li>' % (e(_libelle(x['kind'])), e(_heure(x['depuis']) or 'un instant')) for x in etat['en_cours'])
    attente = ''.join('<li>%s : %d en attente%s</li>' % (e(_libelle(x['kind'])), x['n'], (' dont %d regroupé(s) ou plafonné(s), prochain départ %s' % (
        x['differes'], e(_heure(x['prochain'])))) if x['differes'] else '') for x in etat['attente'])
    courant = ('<h3>En ce moment</h3><ul>%s</ul>' % en_cours if en_cours else '<h3>En ce moment</h3><p class="ax-muted">Aucun travail en cours.</p>') + (
        '<h3>En attente</h3><ul>%s</ul>' % attente if attente else '')
    secours = '' if etat['actif'] else '<p class="ax-alert" role="status">Allègement 5.6.93 désactivé sur ce serveur (comportement de la 5.6.92).</p>'
    return ('<section class="ax-card" id="charge5693" aria-labelledby="charge5693-titre"><h2 id="charge5693-titre">Charge du serveur</h2>'
            '<p>Ce que les automatismes font en arrière-plan : travaux créés, requêtes à Nextcloud et à la messagerie, lectures de fichiers et appels '
            'à l’IA. Aucun contenu de dossier n’est affiché. Les dossiers inchangés ne sont plus relus, les analyses d’un même dossier sont regroupées '
            'et chaque type de travail a un plafond horaire : un travail en trop part plus tard, il n’est jamais perdu.</p>%s%s%s'
            '<p>%s</p><p class="ax-muted">%s</p>%s%s%s<p><a class="ax-btn ghost" href="%s">Actualiser</a></p></section>') % (
        secours, droit, formulaire, tour, e(suivi), travaux, mesures, courant, e(prefix + '/parametres?rubrique=poste#charge5693', quote=True))

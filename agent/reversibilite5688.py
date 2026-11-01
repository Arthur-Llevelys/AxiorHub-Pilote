"""5.6.88 : écrans et routes de la réversibilité : ``/api440/rev5688/*`` (administrateur seulement, registre ``standalone_auth.ADMIN_API``, revérifié ici) et
sections de Paramètres (Confort et maintenance) : sauvegarde automatique, export intégral, vérification d'un export ; bandeau d'Aujourd'hui.

La phrase passe par une requête POST en JSON ; elle n'entre ni dans la file de travaux, ni dans les réglages, ni dans le journal."""
from datetime import datetime, timezone
from html import escape as e
import hmac
import json

from .common import Stop
from . import export5688 as ex, sauvegarde5688 as sv, stockage5688 as st

STATUTS = {'en_cours': 'En cours', 'reussie': 'Réussie', 'partielle': 'Archive locale réussie, second emplacement en échec', 'echec': 'Échec', 'interrompue': 'Interrompue',
           'en_attente': 'En attente', 'pret': 'Prêt à télécharger', 'telecharge': 'Téléchargé, fichier supprimé', 'expire': 'Expiré, fichier supprimé', 'supprime': 'Supprimé'}


def _taille(octets):
    octets = float(octets or 0)
    for unite in ('octets', 'Ko', 'Mo', 'Go'):
        if octets < 1024:
            return ('%d %s' if unite == 'octets' else '%.1f %s') % (octets, unite)
        octets /= 1024
    return '%.1f To' % octets


def _quand(desk, valeur):
    moment = sv._parse(valeur)
    return moment.astimezone(sv._tz(desk)).strftime('%d/%m/%Y à %H:%M') if moment else ''


def motif(code):
    """Une erreur enregistrée, en français clair. Le préfixe ``second_emplacement:`` dit où elle s'est produite."""
    from .erreurs5634 import explain
    code = str(code or '')
    if code.startswith('second_emplacement:'):
        return 'Second emplacement : ' + explain(code.split(':', 1)[1])
    return explain(code) if code else ''


# ------------------------------------------------------------------------------------------------ bandeau d'Aujourd'hui
def banner_html(desk, prefix):
    try:
        cause = sv.alerte(desk)
        if not cause:
            return ''
        derniere = desk.settings(sv.REUSSITE, '')
    except Exception:   # noqa: BLE001 — la page s'affiche toujours
        return ''
    lien = e(prefix + '/parametres?rubrique=poste#s-sauvegarde-automatique', quote=True)
    if cause == 'non_configuree':
        texte = '<strong>La sauvegarde automatique n’est pas en place.</strong> Sans elle, les données du cabinet n’existent qu’à un seul endroit.'
    elif derniere:
        texte = '<strong>Dernière sauvegarde réussie le %s, il y a plus de 48 heures.</strong> Vérifiez la cause dans Paramètres.' % e(_quand(desk, derniere))
    else:
        texte = '<strong>Aucune sauvegarde réussie depuis plus de 48 heures.</strong> Vérifiez la cause dans Paramètres.'
    return '<div class="b5668-banner rev88-banner" role="alert"><p>%s</p><p class="b5668-actions"><a class="ax-btn ghost" href="%s">Régler la sauvegarde</a></p></div>' % (texte, lien)


# ------------------------------------------------------------------------------------------------ sections de Paramètres
def _ligne(titre, valeur):
    return '<tr><th scope="row">%s</th><td>%s</td></tr>' % (e(titre), e(str(valeur)))


def _formulaire(route, champs, bouton, confirmer='', ghost=False):
    return ('<form class="m5-form rev88-form" data-api="%s" data-reload%s>%s<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
        e(route, quote=True), ' data-confirm="%s"' % e(confirmer, quote=True) if confirmer else '', champs, ' ghost' if ghost else '', e(bouton)))


def _champ(nom, libelle, valeur='', genre='text', aide='', extra=''):
    return '<label class="rev88-champ"><span>%s</span><input type="%s" name="%s" value="%s"%s>%s</label>' % (
        e(libelle), genre, e(nom, quote=True), e(str(valeur), quote=True), extra, '<small class="ax-muted">%s</small>' % e(aide) if aide else '')


def _cocher(nom, libelle, actif=False):
    return '<label class="rev88-coche"><input type="checkbox" name="%s"%s> %s</label>' % (e(nom, quote=True), ' checked' if actif else '', e(libelle))


def _etat_html(desk, data):
    d = data['derniere'] or {}
    rows = _ligne('État', ('Activée, chaque jour à partir de %d h' % data['config']['heure']) if data['config']['actif'] else 'Désactivée')
    rows += _ligne('Dernière sauvegarde', ('%s · %s' % (_quand(desk, d.get('fin') or d.get('debut')), STATUTS.get(d.get('statut'), d.get('statut')))) if d else 'Aucune pour l’instant')
    rows += _ligne('Dernière réussite', _quand(desk, data['reussite']) or 'Aucune')
    if d.get('taille'):
        rows += _ligne('Taille', '%s · %d fichiers' % (_taille(d['taille']), d.get('fichiers', 0)))
    rows += _ligne('Emplacement', d.get('emplacement') or data['dossier'])
    if d.get('distant'):
        rows += _ligne('Second emplacement', d['distant'])
    if d.get('erreur'):
        rows += _ligne('Erreur', motif(d['erreur']))
    avertir = ''
    try:
        points = json.loads(d.get('avertissements') or '[]')
    except ValueError:
        points = []
    if points:
        avertir = '<ul class="rev88-liste">%s</ul>' % ''.join('<li>%s</li>' % e(str(p)) for p in points[:10])
    alerte = ''
    if not data['phrase']:
        alerte = '<p class="c5636-warn" role="alert">Aucune phrase n’est définie : la sauvegarde ne peut pas démarrer. Choisissez-la ci-dessous.</p>'
    elif data['alerte'] == 'ancienne':
        alerte = '<p class="c5636-warn" role="alert">La dernière réussite date de plus de 48 heures.</p>'
    return alerte + '<table class="p5660-status rev88-etat"><tbody>%s</tbody></table>%s' % (rows, avertir)


def _phrase_html(data):
    actuelle = _champ('actuelle', 'Phrase actuelle', genre='password', extra=' autocomplete="current-password"') if data['phrase'] else ''
    champs = actuelle + _champ('phrase', 'Nouvelle phrase' if data['phrase'] else 'Phrase de sauvegarde', genre='password', aide='%d caractères au moins. Une suite de mots facile à retenir convient très bien.' % sv.PHRASE_MIN,
                               extra=' autocomplete="new-password" required') + _champ('confirmation', 'Retapez la phrase', genre='password', extra=' autocomplete="new-password" required')
    html = ('<h3>Phrase de sauvegarde</h3><p class="ax-muted">Les archives sont chiffrées avec une clé tirée de cette phrase. Elle n’est conservée nulle part : '
            'notez-la en lieu sûr, hors de ce serveur. <strong>Sans elle, aucune archive ne peut être relue, et personne ne peut la retrouver.</strong></p>')
    html += _formulaire('rev5688/phrase', champs, 'Changer la phrase' if data['phrase'] else 'Enregistrer la phrase')
    if data['phrase']:
        html += _formulaire('rev5688/phrase/tester', _champ('phrase', 'Vérifier que je m’en souviens', genre='password', extra=' autocomplete="off" required'), 'Vérifier ma phrase', ghost=True)
    return html


def _reglages_html(data):
    c = data['config']
    champs = (_cocher('actif', 'Sauvegarder automatiquement chaque jour', c['actif']) + _champ('heure', 'Heure de début (0 à 23)', c['heure'], 'number', extra=' min="0" max="23"') +
              _champ('dossier', 'Dossier local', c['dossier'], aide='Vide : ' + data['dossier'] + '. Sur un serveur, un autre dossier doit être autorisé en écriture pour le service (voir la documentation).') +
              _champ('quotidiennes', 'Sauvegardes quotidiennes à garder', c['quotidiennes'], 'number', extra=' min="1" max="60"') +
              _champ('hebdomadaires', 'Sauvegardes hebdomadaires à garder', c['hebdomadaires'], 'number', extra=' min="0" max="52"') +
              _champ('taille_max_go', 'Volume maximal à sauvegarder (Go)', c['taille_max_go'], 'number', extra=' min="1" max="2000"', aide='Au-delà, la sauvegarde s’arrête et le signale.'))
    return '<h3>Réglages</h3>' + _formulaire('rev5688/reglages', champs, 'Enregistrer les réglages')


def _distant_html(desk, data):
    d = data['config'].get('distant') or {}
    choix = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if d.get('type', '') == v else '', e(t)) for v, t in (('', 'Aucun'), ('webdav', 'WebDAV (Nextcloud, NAS…)'), ('s3', 'Stockage compatible S3')))
    enregistre = bool(st.identifiants(desk)) if d.get('type') else False
    champs = ('<label class="rev88-champ"><span>Type</span><select name="type">%s</select></label>' % choix +
              _champ('url', 'Adresse (https)', d.get('url', ''), 'url', aide='WebDAV : adresse du dossier. S3 : adresse du service, sans le nom du compartiment.') +
              _champ('identifiant', 'Identifiant (ou clé d’accès S3)', extra=' autocomplete="off"', aide='Déjà enregistré dans le coffre : laisser vide pour le conserver.' if enregistre else '') +
              _champ('secret', 'Mot de passe (ou clé secrète S3)', genre='password', extra=' autocomplete="new-password"') +
              _champ('seau', 'Compartiment S3', d.get('seau', '')) + _champ('region', 'Région S3', d.get('region', '')) + _champ('prefixe', 'Préfixe S3 (facultatif)', d.get('prefixe', '')) +
              _cocher('autorise', 'J’autorise le dépôt automatique, chaque jour, de l’archive chiffrée à cet emplacement', bool(d.get('autorise_par'))))
    html = ('<h3>Second emplacement</h3><p class="ax-muted">Une copie hors du serveur protège d’une panne ou d’un sinistre. Seule l’archive chiffrée y est déposée ; '
            'elle est relue après dépôt et la même rétention s’y applique. Les identifiants vont dans le coffre.</p>') + _formulaire('rev5688/distant', champs, 'Enregistrer l’emplacement')
    if d.get('type'):
        html += _formulaire('rev5688/distant/tester', '', 'Tester l’emplacement', ghost=True)
    return html


def _archives_html(desk, data):
    if not data['archives']:
        return '<h3>Archives locales</h3><p class="ax-muted">Aucune archive pour l’instant.</p>'
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(r['date'].strftime('%d/%m/%Y %H:%M') + ' UTC' if r['date'] else ''), e(_taille(r['taille'])),
        _formulaire('rev5688/controler', '<input type="hidden" name="nom" value="%s">' % e(r['nom'], quote=True), 'Contrôler', ghost=True)) for r in data['archives'][:30])
    return ('<h3>Archives locales</h3><p class="ax-muted">« Contrôler » déchiffre et vérifie une archive sans rien restaurer ni écraser. La remise en place est décrite dans '
            '<code>docs/REVERSIBILITE-5.6.88.md</code>.</p><div class="rev88-defile"><table class="rev88-table"><thead><tr><th scope="col">Date</th><th scope="col">Taille</th><th scope="col">Action</th></tr></thead>'
            '<tbody>%s</tbody></table></div>' % rows)


def _controles_html(desk):
    rows = []
    for c in sv.controles(desk):
        r = c['resultat']
        if c['statut'] == 'en_attente':
            texte = 'En attente'
        elif c['statut'] == 'ok':
            texte = 'Intègre : %s fichiers vérifiés' % r.get('verifies', r.get('fichiers', 0))
        else:
            texte = motif(r.get('erreur')) or 'Anomalies : ' + ' ; '.join('%s (%s)' % (a.get('fichier', ''), a.get('motif', '')) for a in (r.get('anomalies') or [])[:10])
        rows.append('<li>%s · %s : %s</li>' % (e(_quand(desk, c['debut'])), e('Archive' if c['genre'] == 'archive' else 'Export'), e(texte)))
    return '<h3>Derniers contrôles</h3><ul class="rev88-liste" data-rev88-controles>%s</ul>' % ''.join(rows) if rows else ''


def sauvegarde_html(desk):
    data = sv.etat(desk)
    lancer = _formulaire('rev5688/lancer', '', 'Sauvegarder maintenant') if data['phrase'] else ''
    occupe = data['en_cours'] or any(c['statut'] == 'en_attente' for c in sv.controles(desk))
    attente = '<p class="ax-muted" data-rev88-attente>Un travail est en cours ; cette page se mettra à jour.</p>' if occupe else ''
    return ('<section class="ax-card rev88" id="sauvegarde5688"><h2>Sauvegarde automatique</h2>' + attente + _etat_html(desk, data) + lancer + _phrase_html(data) + _reglages_html(data) +
            _distant_html(desk, data) + _archives_html(desk, data) + _controles_html(desk) + '</section>')


def _export_ligne(prefix, r):
    etat = STATUTS.get(r['statut'], r['statut'])
    if r['statut'] in ('en_cours', 'en_attente'):
        etat += ' : %s' % r['etape'] if r['etape'] else ''
        return ('<li data-rev88-export="%s"><span data-rev88-etape>%s</span> <progress max="100" value="%d">%d %%</progress></li>' % (e(r['id'], quote=True), e(etat), r['progression'], r['progression']))
    if r['statut'] == 'echec':
        return '<li>Échec : %s</li>' % e(motif(r['erreur']))
    if r['statut'] != 'pret':
        return '<li>%s</li>' % e(etat)
    lien = e(prefix + '/api440/rev5688/export/telecharger?id=' + r['id'], quote=True)
    cible = '<input type="hidden" name="id" value="%s">' % e(r['id'], quote=True)
    return ('<li><strong>Prêt : %s.</strong> Empreinte SHA-256 : <code>%s</code>. Conservé jusqu’au premier téléchargement complet, 24 heures au plus. '
            '<a class="ax-btn" href="%s" download>Télécharger</a>%s%s</li>' % (e(_taille(r['taille'])), e(r['sha256']), lien, _formulaire('rev5688/export/verifier', cible, 'Vérifier', ghost=True),
                                                                               _formulaire('rev5688/export/supprimer', cible, 'Supprimer', ghost=True)))


def export_html(desk, prefix):
    from .membres5650 import load
    reserves = len(load(desk.c['state_dir'])['reserves'])
    champs = (_cocher('documents', 'Inclure aussi les documents (copie depuis Nextcloud, plus long et plus volumineux)') +
              '<fieldset class="rev88-choix"><legend>Dossiers réservés (%d)</legend>'
              '<label><input type="radio" name="dossiers_reserves" value="exclure" checked> Les exclure, avec tout ce qui s’y rattache</label>'
              '<label><input type="radio" name="dossiers_reserves" value="inclure"> Les inclure</label></fieldset>' % reserves +
              _cocher('confirmation_reserves', 'Si je les inclus : je confirme vouloir sortir ces dossiers réservés du logiciel'))
    rows = ''.join(_export_ligne(prefix, r) for r in ex.liste(desk))
    return ('<section class="ax-card rev88" id="export5688"><h2>Export intégral du cabinet</h2><p class="ax-muted">Une archive ZIP lisible sans AxiorHub : dossiers, contacts, factures, temps, agenda, tâches, notes, '
            'messagerie interne, modèles et compétences, en JSON et en CSV, avec un manifeste d’empreintes et un fichier LISEZMOI. Mots de passe, jetons et clés en sont exclus. '
            'Les documents restent dans Nextcloud : l’export en donne l’inventaire, ou les copie si vous cochez l’option. Le fichier n’est pas chiffré : il contient des données couvertes par le secret professionnel.</p>' +
            _formulaire('rev5688/export', champs, 'Préparer l’export') + ('<ul class="rev88-liste" data-rev88-exports>%s</ul>' % rows if rows else '') + '</section>')


def verification_html():
    return ('<section class="ax-card rev88" id="verification5688"><h2>Vérifier un export</h2><p class="ax-muted">Compare un fichier ZIP à son manifeste : chaque fichier présent, à la bonne taille, avec la bonne empreinte. '
            'Le fichier est envoyé au serveur, contrôlé, puis supprimé.</p><div class="rev88-form" data-rev88-depot><label class="rev88-champ"><span>Fichier ZIP de l’export</span>'
            '<input type="file" accept=".zip,application/zip" data-rev88-fichier></label><button class="ax-btn" type="button" data-rev88-envoyer>Vérifier ce fichier</button>'
            '<p role="status" class="m5-status" data-rev88-statut></p></div></section>')


def sections(desk, prefix):
    """Réservé à l'administrateur : l'appelant (``parametres5621``) ne l'appelle que pour ce rôle ; les routes le revérifient."""
    return (sauvegarde_html(desk), export_html(desk, prefix) + '<script defer src="%s"></script>' % e(prefix + '/static/v5688-reversibilite.js', quote=True), verification_html())


def apercu(desk):
    """Ligne de la vue d'ensemble des réglages."""
    if not sv.config(desk)['actif']:
        return 'Sauvegarde automatique désactivée'
    derniere = desk.settings(sv.REUSSITE, '')
    return 'Dernière réussite : ' + _quand(desk, derniere) if derniere else 'Aucune sauvegarde réussie pour l’instant'


# ------------------------------------------------------------------------------------------------ routes
def _garde(env, auth):
    """Mêmes contrôles que ``web5634._post`` pour une requête dont le corps n'est pas du JSON."""
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')


def _il_y_a_6h():
    return datetime.now(timezone.utc).timestamp() - 6 * 3600


def _etat_public(desk):
    return {'sauvegarde_en_cours': sv.etat(desk)['en_cours'],
            'exports': [{k: r[k] for k in ('id', 'statut', 'etape', 'progression')} for r in ex.liste(desk)],
            'controles_en_attente': sum(1 for c in sv.controles(desk) if c['statut'] == 'en_attente' and (sv._parse(c['debut']) or datetime.min.replace(tzinfo=timezone.utc)).timestamp() > _il_y_a_6h())}


def _route(env, desk, auth, route, args, method, owner):
    from .web440 import json_out
    from .web5634 import _post
    if method == 'GET':
        if route == 'etat':
            return json_out(_etat_public(desk))
        if route == 'export/telecharger':
            flux, taille, nom = ex.telecharger(desk, str(args.get('id') or ''), owner)
            return {'status': '200 OK', 'kind': 'application/zip', 'body': b'', 'stream': flux, 'length': taille,
                    'headers': [('Content-Disposition', 'attachment; filename="%s"' % nom), ('Cache-Control', 'no-store')]}
        raise Stop('operation_absente')
    if method != 'POST':
        raise Stop('methode_refusee')
    if route == 'depot/morceau':
        _garde(env, auth)
        try:
            longueur, numero = int(env.get('CONTENT_LENGTH') or 0), int(args.get('n') or -1)
        except ValueError:
            raise Stop('depot_invalide') from None
        return json_out(ex.depot_morceau(desk, str(args.get('id') or ''), numero, env['wsgi.input'], longueur))
    data = _post(env, auth, 40_000)
    if route == 'phrase':
        return json_out(sv.definir_phrase(desk, data.get('phrase'), data.get('confirmation'), owner, data.get('actuelle')))
    if route == 'phrase/tester':
        if not sv.tester_phrase(desk, data.get('phrase')):
            raise Stop('phrase_actuelle_incorrecte')
        return json_out({'ok': True, 'noreload': True, 'message': 'C’est la bonne phrase.'})
    if route == 'reglages':
        return json_out(sv.enregistrer(desk, data, owner))
    if route == 'lancer':
        return json_out(sv.lancer(desk, owner))
    if route == 'distant':
        return json_out(st.enregistrer(desk, data, owner))
    if route == 'distant/tester':
        return json_out({**st.tester(desk, owner), 'noreload': True})
    if route == 'controler':
        return json_out(sv.demander_controle(desk, 'archive', str(data.get('nom') or ''), owner))
    if route == 'export':
        return json_out(ex.demander(desk, data, owner))
    if route == 'export/supprimer':
        return json_out(ex.supprimer(desk, str(data.get('id') or ''), owner))
    if route == 'export/verifier':
        return json_out(sv.demander_controle(desk, 'export', str(data.get('id') or ''), owner))
    if route == 'depot/ouvrir':
        return json_out(ex.depot_ouvrir(desk, data.get('taille'), owner))
    if route == 'depot/verifier':
        return json_out(sv.demander_controle(desk, 'export', str(data.get('id') or ''), owner))
    raise Stop('operation_absente')


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('rev5688/'):].split('?', 1)[0]
    try:
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        return _route(env, desk, auth, route, args, method, owner)
    except Stop as error:
        code = str(error).split(':')[0]
        return json_out({'error': code, 'message': motif(code)}, '403 Forbidden' if code in ('role_insuffisant', 'origine_ou_csrf_refuse') else '400 Bad Request')

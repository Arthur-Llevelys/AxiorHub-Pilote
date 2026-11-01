"""5.6.88 : rubrique Paramètres › Stockage des documents et routes /api440/m5688cloud/…

Toutes les routes sont réservées aux administrateurs, y compris pour un compte qui a le droit « réglages » : elles déplacent ou ferment des documents. Les connexions elles-mêmes
passent par les routes existantes ``m5652/connexion/…`` (secrets au coffre, jamais renvoyés). Aucune route ne renvoie un secret ; l'agent et les modèles n'y ont pas accès.
"""
from html import escape as e

from .common import Stop, load_matters, matter_display
from . import cloud5688 as core, cloud_cloison5688 as cloison, cloud_gestion5688 as gestion, cloud_migration5688 as migration, cloud_nommage5688 as nommage

REVOCATION = {'cloud_google_drive': 'La déconnexion demande à Google de révoquer le jeton, puis l’efface du coffre.',
              'cloud_dropbox': 'La déconnexion demande à Dropbox de révoquer le jeton, puis l’efface du coffre.',
              'cloud_microsoft': 'La déconnexion efface les jetons du coffre. Microsoft ne permet pas de révoquer un jeton isolé : retirez aussi le consentement dans le compte Microsoft ou dans Entra.'}
SCOPES = {'cloud_google_drive': 'Par défaut drive.file : AxiorHub ne voit que ce qu’il a créé. Un fichier déposé à la main dans Drive par un collaborateur reste invisible : ni lu, ni indexé, ni surveillé. '
                                'Si le cabinet dépose ainsi des pièces, choisissez la portée complète, qui donne accès à tout le Drive du compte relié.',
          'cloud_microsoft': 'Files.ReadWrite pour OneDrive ; Files.ReadWrite.All et Sites.Read.All si un site SharePoint est indiqué.',
          'cloud_dropbox': 'Lecture et écriture des fichiers, identité du compte. La gestion des partages n’est demandée que si vous activez le cloisonnement.'}


def _status(text):
    return '<span class="c88-state">%s</span>' % e(text)


def _form(route, hidden, label, confirm='', ghost=True, extra=''):
    fields = ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in hidden.items())
    return ('<form class="m5-form r52-inline" data-api="%s" data-reload%s>%s%s<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
        e(route, quote=True), (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '', fields, extra, ' ghost' if ghost else '', e(label)))


def _fields(provider, view):
    params = (view or {}).get('params') or {}
    saved = (view or {}).get('secrets_enregistres') or {}
    out = []
    for f in core.PROVIDERS[provider]['fields']:
        name, label = f['name'], f['label']
        if f['secret']:
            hint = 'enregistré le %s, laisser vide pour le conserver' % saved[name][:10] if name in saved else ('obligatoire' if f['required'] else 'facultatif')
            out.append('<label>%s<input type="password" name="%s" autocomplete="new-password" placeholder="%s"></label>' % (e(label), e(name, quote=True), e(hint, quote=True)))
        elif name == 'acces':
            current = params.get(name) or 'fichiers'
            out.append('<label>%s<select name="acces"><option value="fichiers"%s>drive.file : seulement ce qu’AxiorHub a créé</option><option value="complet"%s>Drive complet : tout le Drive du compte relié</option></select></label>' % (
                e(label), ' selected' if current == 'fichiers' else '', ' selected' if current == 'complet' else ''))
        else:
            out.append('<label>%s<input name="%s" value="%s" maxlength="300"></label>' % (e(label), e(name, quote=True), e(params.get(name, ''), quote=True)))
    return ''.join(out)


def _card(desk, provider, current, redirect):
    st = gestion.status(desk, provider)
    view = st['view']
    spec = core.PROVIDERS[provider]
    hidden = ('<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">' % (e(view['id'], quote=True), view['version'])) if view else ''
    settings = ('<details%s><summary>Identifiants de l’application du cabinet</summary><p class="ax-muted">%s</p><p class="ax-muted">Adresse de retour à déclarer chez le fournisseur : <code>%s</code></p>'
                '<form class="m5-form" data-api="m5652/connexion/enregistrer" data-reload>%s<input type="hidden" name="provider" value="%s"><input type="hidden" name="environment" value="production">'
                '<input type="hidden" name="portee" value="cabinet">%s<button class="ax-btn" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
        '' if view else ' open', e(SCOPES[provider]), e(redirect), hidden, e(provider, quote=True), _fields(provider, view))
    actions = ''
    if view:
        ident = e(view['id'], quote=True)
        actions += ('<form class="m5-form r52-inline" data-api="m5652/connexion/oauth/debut" data-cx-oauth><input type="hidden" name="id" value="%s"><input type="hidden" name="redirect" value="%s">'
                    '<button class="ax-btn" type="submit">Connecter le compte</button><p role="status" class="m5-status"></p></form>') % (ident, e(redirect, quote=True))
        actions += _form('m5652/connexion/tester', {'id': view['id']}, 'Tester la lecture')
        actions += _form('m5688cloud/preparer', {'provider': provider}, 'Préparer le dossier AxiorHub')
        actions += _form('m5688cloud/controler', {'provider': provider}, 'Contrôler ce compte', 'Un fichier d’essai sera créé dans la première racine, remplacé, puis mis à la corbeille du fournisseur.')
        if current == provider:
            actions += _form('m5688cloud/retour', {}, 'Revenir au stockage d’origine', 'Revenir au stockage d’origine ? Aucun fichier ne sera déplacé : ce qui a été écrit dans le cloud y restera.')
        else:
            actions += _form('m5688cloud/choisir', {'provider': provider}, 'Utiliser ce stockage', 'Tous les documents seront désormais lus et écrits ici. Confirmez-vous ce choix ?', ghost=False,
                             extra='<label class="r52-day"><input type="checkbox" name="sans_copie"> Choisir sans copie terminée : les dossiers existants n’y seront pas</label>')
        actions += _form('m5652/connexion/deconnecter', {'id': view['id']}, 'Déconnecter', ('Ce stockage est en service : les documents deviendront inaccessibles tant qu’il n’est pas reconnecté. ' if current == provider else '') + REVOCATION[provider])
    done = st['aptitude'] or {}
    lines = ['Connexion : ' + st['etat_libelle']]
    if view and view.get('identite', {}).get('compte'):
        lines.append('Compte : ' + str(view['identite']['compte']))
    lines.append('Contrôle : ' + ('réussi le ' + str(done.get('date', ''))[:10] if st['aptitude_valide'] else ('à refaire' if done else 'pas encore fait')))
    failed = [s for s in done.get('etapes') or [] if not s.get('ok')]
    report = ('<p class="c88-alert">Contrôle arrêté à « %s »%s.</p>' % (e(failed[0]['etape']), (' : ' + e(failed[0]['detail'])) if failed[0].get('detail') else '')) if failed else ''
    return ('<article class="c88-card"><h3>%s %s</h3><p class="ax-muted">%s</p><ul class="c88-facts">%s</ul>%s%s<p class="ax-muted">%s</p><div class="r52-actions">%s</div></article>') % (
        e(spec['label']), _status('En service') if current == provider else '', e(spec['usage']), ''.join('<li>%s</li>' % e(x) for x in lines), report, settings, e(REVOCATION[provider]), actions)


def _copies(desk, current):
    options = [('nextcloud', 'Nextcloud ou dossier local')] + [(k, v['label']) for k, v in core.PROVIDERS.items()]
    choose = lambda name, chosen: '<select name="%s">%s</select>' % (name, ''.join('<option value="%s"%s>%s</option>' % (e(k, quote=True), ' selected' if k == chosen else '', e(v)) for k, v in options))
    rows = ''
    for item in migration.latest(desk):
        counts = ' · '.join('%s : %d' % (migration.LABELS.get(k, k), v) for k, v in sorted(item['bilan'].items()))
        problems = ''.join('<li>%s : %s%s</li>' % (e(p['path']), e(migration.LABELS.get(p['state'], p['state'])), (' (%s)' % e(p['error'])) if p['error'] else '') for p in item['problemes'])
        action = ''
        if item['etat'] == 'en_cours':
            action = _form('m5688cloud/copie/pause', {'id': item['id']}, 'Mettre en pause')
        elif item['etat'] == 'en_pause' or any(p['state'] in ('echec', 'source_modifiee') for p in item['problemes']):
            action = _form('m5688cloud/copie/reprendre', {'id': item['id']}, 'Reprendre')
        rows += '<article class="c88-copy"><h3>%s vers %s <small>%s</small></h3><p>%s</p>%s%s%s</article>' % (
            e(dict(options).get(item['source'], item['source'])), e(dict(options).get(item['destination'], item['destination'])), e(item['etat_libelle']), e(counts or 'Inventaire en cours.'),
            ('<p class="c88-alert">Arrêt : %s</p>' % e(item['erreur'])) if item['erreur'] else '', ('<ul class="c88-facts">%s</ul>' % problems) if problems else '', action)
    return ('<section class="ax-card" id="copie"><h2>Copier les documents vers un autre stockage</h2><p class="ax-muted">La source n’est jamais modifiée ni vidée. Chaque fichier est relu à destination et comparé par son empreinte. '
            'Un fichier déjà présent n’est pas remplacé. La copie peut s’arrêter et reprendre. Elle ne change pas le stockage en service : c’est un choix à part.</p>'
            '<form class="m5-form" data-api="m5688cloud/copie/lancer" data-reload><label>Depuis %s</label><label>Vers %s</label><button class="ax-btn" type="submit">Lancer la copie</button>'
            '<p role="status" class="m5-status"></p></form>%s</section>') % (choose('source', current), choose('destination', 'cloud_google_drive' if current == 'nextcloud' else 'nextcloud'), rows)


def _naming(desk):
    stored = nommage.rules(desk)
    tokens = ', '.join('{%s}' % k for k in nommage.TOKENS)
    forms = ''.join(
        '<form class="m5-form c88-rule" data-api="m5688cloud/nommage" data-reload><h3>%s</h3><input type="hidden" name="nature" value="%s">'
        '<label>Modèle de nom<input name="modele" maxlength="200" value="%s" placeholder="{date} - {nature} - {titre}"></label>'
        '<label>Sous-dossier, facultatif<input name="sous_dossier" maxlength="200" value="%s" placeholder="Pièces/{annee}"></label>'
        '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>' % (
            e(label), e(key, quote=True), e((stored.get(key) or {}).get('modele', ''), quote=True), e((stored.get(key) or {}).get('sous_dossier', ''), quote=True))
        for key, label in nommage.NATURES.items())
    return ('<section class="ax-card" id="nommage"><h2>Nommage et rangement</h2><p class="ax-muted">Sans règle, les noms restent ceux d’aujourd’hui. Une règle s’applique aux prochains documents, dans le stockage en service. '
            'L’extension d’origine est gardée, et à nom égal le document reçoit un numéro : rien n’est écrasé. Champs disponibles : %s.</p><div class="c88-rules">%s</div></section>') % (e(tokens), forms)


def _plan_html(found):
    plan = found['plan']
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td><strong>%s</strong><br><small>%s</small></td></tr>' % (
        e(x['chemin']), e(x['identite'] or x['type']), e(x['role']), e(cloison.ACTIONS.get(x['action'], x['action'])), e(x['raison'])) for x in plan['entrees'])
    notes = ''
    if plan.get('parcours_partiel'):
        notes += '<p class="c88-alert">Le dossier contient trop d’éléments : le parcours s’est arrêté à %d. Le reste n’a pas été examiné.</p>' % cloison.MAX_ITEMS
    if plan.get('sans_correspondance'):
        notes += '<p class="c88-alert">Comptes autorisés sans identité cloud : %s. Leurs accès ne sont pas reconnus.</p>' % e(', '.join(plan['sans_correspondance']))
    if plan.get('limites'):
        notes += '<p class="ax-muted">%s</p>' % e(plan['limites'])
    out = '<div class="c88-table"><table><thead><tr><th scope="col">Élément</th><th scope="col">Accès</th><th scope="col">Niveau</th><th scope="col">Décision</th></tr></thead><tbody>%s</tbody></table></div>%s' % (
        rows or '<tr><td colspan="4">Aucun accès déclaré par le fournisseur.</td></tr>', notes)
    if found['state'] == 'apercu':
        count = sum(1 for x in plan['entrees'] if x['action'] == 'retirer')
        out += _form('m5688cloud/cloison/confirmer', {'id': found['id'], 'hash': found['hash']}, 'Confirmer les %d retrait(s)' % count,
                     'Retirer %d accès chez le fournisseur ? AxiorHub ne pourra pas les rétablir : il faudrait les redonner chez le fournisseur.' % count, ghost=False) if count else '<p>Aucun retrait à faire.</p>'
    else:
        result = found.get('resultat') or {}
        out += '<p class="ax-muted">%s : %d retiré(s), %d échec(s), %d encore présent(s).</p>' % (e(found['state']), len(result.get('retires') or []), len(result.get('echecs') or []), len(result.get('encore_presents') or []))
    return out


def _partition(desk, auth, current):
    from . import membres5650
    can = cloison.support(desk)
    head = '<section class="ax-card" id="cloisonnement"><h2>Cloisonnement chez le fournisseur</h2>'
    if not can.get('possible'):
        return head + '<p>%s</p></section>' % e(can.get('raison') or '')
    provider = can['provider']
    known = cloison.identities(desk, provider)
    members = sorted(membres5650.directory(desk, auth).values(), key=lambda m: (m.get('name') or '').lower())
    pairs = ''.join('<form class="m5-form c88-rule" data-api="m5688cloud/identite" data-reload><input type="hidden" name="provider" value="%s"><input type="hidden" name="owner" value="%s">'
                    '<label>%s<input name="identite" type="email" value="%s" placeholder="adresse du compte chez le fournisseur"></label><button class="ax-btn ghost" type="submit">Enregistrer</button>'
                    '<p role="status" class="m5-status"></p></form>' % (e(provider, quote=True), e(m['owner'], quote=True), e(membres5650.label(m)), e(known.get(m['owner'], ''), quote=True)) for m in members)
    names = {m['id']: matter_display(m) for m in load_matters(desk.c, unfiltered=True)}
    blocks = ''
    for matter in sorted(membres5650.load(desk.c['state_dir'])['reserves']):
        found = cloison.latest(desk, matter)
        blocks += '<article class="c88-copy"><h3>%s</h3>%s%s</article>' % (e(names.get(matter, matter)), _form('m5688cloud/cloison/apercu', {'matter': matter}, 'Voir ce qui changerait'), _plan_html(found) if found else '')
    return head + ('<p class="ax-muted">Un dossier réservé dans AxiorHub peut être fermé aux membres non autorisés chez le fournisseur. Rien n’est retiré sans aperçu ni confirmation d’un administrateur, aucun accès n’est jamais '
                   'ajouté, aucun lien n’est créé, et lever une réservation ne rend aucun accès. Réservez un dossier depuis sa page, rubrique « Accès au dossier ».</p><h3>Identités cloud des membres</h3><div class="c88-rules">%s</div>'
                   '<h3>Dossiers réservés</h3>%s</section>') % (pairs or '<p>Aucun membre connu.</p>', blocks or '<p>Aucun dossier réservé.</p>')


def section_html(desk, auth, prefix, env):
    from .microsoft5636 import redirect_uri
    redirect = redirect_uri(env, prefix).rsplit('/', 1)[0] + '/connexions-retour'
    selected = core.read_selection(desk.c['state_dir'])
    if selected and selected.get('provider') == core.UNREADABLE:
        return ('<section class="ax-card"><h2>Stockage en service</h2><p class="c88-alert">Le choix du stockage est illisible : les documents ne sont plus accessibles, pour éviter d’écrire au mauvais endroit.</p>%s</section>' %
                _form('m5688cloud/retour', {}, 'Revenir au stockage d’origine', 'Revenir au stockage d’origine ?', ghost=False))
    current = selected['provider'] if selected else 'nextcloud'
    label = core.PROVIDERS[current]['label'] if selected else 'Nextcloud ou dossier local (réglage d’origine)'
    head = ('<section class="ax-card"><h2>Stockage en service</h2><p><strong>%s</strong></p><p class="ax-muted">Un seul stockage est en service à la fois. Le choix se fait ici, jamais tout seul : une mise à jour ou une copie ne le change pas. '
            'L’application OAuth appartient au cabinet : vous saisissez ses identifiants, ils vont au coffre et ne sont jamais réaffichés. Les agendas ne changent pas.</p></section>') % e(label)
    return (head + '<section class="ax-card" id="fournisseurs"><h2>Fournisseurs</h2><div class="c88-cards">%s</div></section>' % ''.join(_card(desk, p, current, redirect) for p in core.PROVIDERS) +
            _copies(desk, current) + _naming(desk) + _partition(desk, auth, current))


def api(env, desk, auth, prefix, name, args, method):
    from .erreurs5634 import explain
    from .web import REASONS
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    try:
        owner, role = actor(env)
        if role != 'administrateur':
            raise Stop('role_insuffisant')
        if method != 'POST':
            raise Stop('methode_refusee')
        data = _post(env, auth, 50_000)
        route = name[len('m5688cloud/'):].split('?', 1)[0]
        return json_out(_route(desk, owner, role, route, data))
    except Stop as error:
        code = str(error).split(':')[0]
        return json_out({'error': code, 'message': REASONS.get(code) or MESSAGES.get(code) or explain(code, getattr(error, 'detail', None))},
                        '403 Forbidden' if code == 'role_insuffisant' else ('409 Conflict' if code in ('apercu_perime', 'apercu_deja_traite', 'copie_deja_en_cours') else '400 Bad Request'))


def _route(desk, owner, role, route, data):
    provider = str(data.get('provider') or '')
    if route == 'preparer':
        return gestion.prepare(desk, role, provider)
    if route == 'controler':
        return gestion.check(desk, role, provider)
    if route == 'choisir':
        return gestion.activate(desk, owner, role, provider, str(data.get('sans_copie') or '').lower() in ('oui', 'on', 'true', '1'))
    if route == 'retour':
        return gestion.deactivate(desk, owner, role)
    if route == 'copie/lancer':
        return migration.start(desk, owner, role, str(data.get('source') or ''), str(data.get('destination') or ''))
    if route == 'copie/reprendre':
        return migration.resume(desk, owner, role, str(data.get('id') or ''))
    if route == 'copie/pause':
        return migration.pause(desk, owner, role, str(data.get('id') or ''))
    if route == 'nommage':
        return nommage.save(desk, owner, role, str(data.get('nature') or ''), {'modele': data.get('modele'), 'sous_dossier': data.get('sous_dossier')})
    if route == 'identite':
        return cloison.set_identity(desk, owner, role, provider, str(data.get('owner') or ''), str(data.get('identite') or ''))
    if route == 'cloison/apercu':
        out = cloison.preview(desk, owner, role, str(data.get('matter') or ''))
        return {'id': out['id'], 'message': out['message']}
    if route == 'cloison/confirmer':
        return cloison.confirm(desk, owner, role, str(data.get('id') or ''), str(data.get('hash') or ''))
    raise Stop('route_inconnue')


MESSAGES = {'controle_aptitude_requis': 'Faites d’abord réussir le contrôle de ce compte.', 'connexion_non_verifiee': 'Connectez le compte, puis réussissez le test de lecture.',
            'copie_terminee_requise': 'Aucune copie terminée sans erreur vers ce stockage : les dossiers y seraient vides ou incomplets. Lancez la copie, ou confirmez le choix sans copie.',
            'apercu_perime': 'Les accès ont changé depuis l’aperçu, ou il date de plus de 30 minutes : rien n’a été retiré. Refaites l’aperçu.',
            'apercu_deja_traite': 'Cet aperçu a déjà été traité.', 'apercu_absent': 'Aperçu introuvable.', 'dossier_non_reserve': 'Ce dossier n’est pas réservé.',
            'cloisonnement_non_disponible': 'Ce compte ne permet pas de répercuter le cloisonnement.', 'cloisonnement_stockage_cloud_seulement': 'Le cloisonnement ne se répercute que sur un stockage cloud en service.',
            'identite_cloud_invalide': 'Adresse invalide.', 'identite_cloud_deja_utilisee': 'Cette adresse est déjà associée à un autre membre.',
            'regle_nommage_invalide': 'Le modèle contient un caractère interdit dans un nom de fichier.', 'regle_nommage_champ_inconnu': 'Le modèle utilise un champ inconnu.',
            'regle_nommage_sous_dossier_invalide': 'Le sous-dossier doit être un chemin relatif simple.', 'copie_source_destination_invalides': 'Choisissez deux stockages différents.',
            'copie_deja_en_cours': 'Une copie entre ces deux stockages est déjà en cours.'}

"""5.6.73 : page « Navigateur supervisé » et routes ``/api440/m5673/...`` (poste Windows ou Ubuntu seulement).

- GET ``etat`` : séance en cours (état, question en attente, pas, fichiers, résumé) ;
- POST ``lancer`` (tâche, dossier), ``decision`` (autoriser, refuser, toujours, reprendre), ``main`` (prendre / rendre la main),
  ``arreter``, ``fermer`` (fermer le navigateur) : avocat, assistant, administrateur ;
- POST ``sites`` : liste des sites autorisés (avocat, administrateur).
"""
from html import escape as e

from .common import Stop

WORK = ('lancer', 'decision', 'main', 'arreter', 'fermer')
LAWYER = ('sites',)


def allowed(role, route):
    route = route.split('?', 1)[0]
    if route in WORK or route == 'etat':
        return role in ('administrateur', 'avocat', 'assistant')
    return route in LAWYER and role in ('administrateur', 'avocat')


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from . import navigateur5673 as nv
    owner, role = actor(env)
    route = name[len('m5673/'):].split('?', 1)[0]
    if route not in WORK + LAWYER + ('etat',):
        raise Stop('route_inconnue')
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    if not nv.available(auth):
        raise Stop('navigateur_reserve_au_poste')
    mgr = nv.manager(desk.c)
    if route == 'etat':
        s = mgr.session
        return json_out({'seance': s.public() if s is not None else None})
    if method != 'POST':
        raise Stop('methode_refusee')
    from .web5634 import _post
    data = _post(env, auth, 20_000)
    if route == 'lancer':
        matter = str(data.get('dossier') or '')
        if matter:
            from .common import load_matters
            from .membres5650 import viewer_hidden
            if matter in (viewer_hidden() or frozenset()):
                raise Stop('dossier_reserve')
            if not any(str(m['id']) == matter for m in load_matters(desk.c)):
                raise Stop('dossier_inconnu')
        out = mgr.start(owner, str(data.get('tache') or ''), matter)
        desk.audit('navigateur5673_lancement', {'seance': out['id'], 'dossier': matter, 'par': str(owner)[:80]})
        return json_out({'ok': True, 'seance': out, 'message': 'L’agent démarre dans la fenêtre du navigateur.'})
    sid = str(data.get('id') or '')
    if route == 'decision':
        choice = str(data.get('choix') or '')
        out = mgr.decide(sid, choice, str(data.get('reponse') or ''))
        desk.audit('navigateur5673_decision', {'seance': sid, 'choix': choice, 'par': str(owner)[:80]})
        return json_out({'ok': True, 'seance': out, 'message': {'autoriser': 'Autorisé.', 'refuser': 'Refusé.', 'toujours': 'Site ajouté à la liste.',
                                                                'reprendre': 'L’agent reprend.'}.get(choice, 'Enregistré.')})
    if route == 'main':
        on = str(data.get('prendre') or '') in ('1', 'oui', 'true')
        return json_out({'ok': True, 'seance': mgr.take(sid, on), 'message': 'Vous avez la main : l’agent attend.' if on else 'L’agent reprend la main.'})
    if route == 'arreter':
        out = mgr.stop(sid)
        desk.audit('navigateur5673_arret', {'seance': sid, 'par': str(owner)[:80]})
        return json_out({'ok': True, 'seance': out, 'message': 'Arrêt demandé.'})
    if route == 'fermer':
        return json_out(mgr.close_browser())
    return json_out(nv.save_sites(desk, owner, str(data.get('sites') or '')))


def page_html(desk, prefix, args, env, auth=None):
    from . import navigateur5673 as nv
    from .cdp5673 import find_browser
    from .common import load_matters, matter_display
    from .membres5650 import viewer_hidden
    from .web567 import actor
    role = actor(env)[1]
    intro = ('<p class="ax-muted">Confiez une tâche à l’agent : il travaille dans une fenêtre du navigateur de ce poste, que vous voyez. '
             'Il ne saisit jamais de mot de passe, de code ni de numéro de carte ; payer, envoyer, déposer, signer ou supprimer attendent '
             'votre accord ici. Les fichiers récupérés vont dans « %s » du dossier choisi.</p>' % e(nv.FOLDER))
    if not nv.available(auth):
        return ('<section class="ax-card n5673"><h2>Navigateur supervisé</h2>%s<p class="notice">Disponible dans l’application AxiorHub Pilote '
                'pour Windows ou Ubuntu (poste de l’avocat), jamais sur le serveur du cabinet.</p></section>') % intro
    found = find_browser()
    if not found:
        return ('<section class="ax-card n5673"><h2>Navigateur supervisé</h2>%s<p class="notice">Aucun navigateur compatible trouvé sur ce poste : '
                'installez Microsoft Edge, Google Chrome ou Chromium, puis rechargez la page.</p></section>') % intro
    hidden = viewer_hidden() or frozenset()
    options = ''.join('<option value="%s"%s>%s</option>' % (e(str(m['id']), quote=True), ' selected' if str(m['id']) == str(args.get('dossier') or '') else '',
                                                            e(matter_display(m)))
                      for m in load_matters(desk.c) if str(m['id']) not in hidden)
    sites_block = ''
    if role in ('administrateur', 'avocat'):
        sites_block = ('<details class="n5673-sites"><summary>Sites autorisés (%d)</summary><p class="ax-muted">Un site absent de cette liste '
                       'demande votre accord la première fois. Un domaine couvre ses sous-domaines.</p><form data-c5671="m5673/sites" data-reload>'
                       '<label for="n5673-sites">Un site par ligne</label><textarea id="n5673-sites" name="sites" rows="8">%s</textarea>'
                       '<button class="ax-btn" type="submit">Enregistrer la liste</button></form></details>') % (
            len(nv.sites(desk)), e('\n'.join(nv.sites(desk))))
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(h['debut'][:16].replace('T', ' ')), e(h['tache'][:120]), e({'termine': 'terminé', 'arrete': 'arrêté', 'erreur': 'interrompu'}.get(h['etat'], h['etat'])),
        e(', '.join(f['nom'] for f in h['fichiers'])[:200] or '—')) for h in nv.history(desk, 10))
    return ('<section class="ax-card n5673" id="n5673" data-prefix="%s"><h2>Navigateur supervisé</h2>%s'
            '<p class="ax-muted">Navigateur utilisé : <code>%s</code></p>'
            '<div class="n5673-live" id="n5673-live" aria-live="polite"></div>'
            '<form class="n5673-start" data-n5673="lancer"><label for="n5673-tache">Tâche</label>'
            '<textarea id="n5673-tache" name="tache" rows="3" required placeholder="Ex. : récupère l’extrait d’immatriculation de la société et enregistre-le"></textarea>'
            '<label for="n5673-dossier">Dossier (pour ranger les pièces)</label><select id="n5673-dossier" name="dossier" data-c5670-search>'
            '<option value="">— aucun (fichiers gardés sur ce poste) —</option>%s</select>'
            '<button class="ax-btn" type="submit">Lancer</button></form>%s'
            '<h3>Dernières séances</h3><div class="table"><table><thead><tr><th>Début</th><th>Tâche</th><th>Fin</th><th>Fichiers</th></tr></thead>'
            '<tbody>%s</tbody></table></div></section>') % (
        e(prefix, quote=True), intro, e(found), options, sites_block, rows or '<tr><td colspan="4">Aucune séance.</td></tr>')

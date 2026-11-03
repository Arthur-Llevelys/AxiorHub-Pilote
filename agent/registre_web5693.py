"""5.6.93, lot registre : routes ``/api440/m5689/registre/*`` et bloc « Suivi du fichier » de la fiche d'un document (cahier 5.6.90, N03, N05, N06).

Le compte et le rôle viennent de la requête authentifiée, jamais du corps. Chaque action résout le document côté serveur, puis contrôle dossier réservé et
droits ; une version périmée répond 409. Aucune de ces routes ne contacte le stockage : Réessayer et Retrouver le fichier rendent le contrôle dû au prochain
passage d'arrière-plan, Retirer du suivi le sort des contrôles sans effacer l'incident. Une suggestion de doublon se décide, elle ne fusionne jamais rien.
"""
from html import escape as e

from .common import Stop
from . import livrables5689 as liv, registre5693 as reg

ACTIONS = {'reessayer': ('administrateur', 'avocat', 'assistant'), 'retrouver': ('administrateur', 'avocat', 'assistant'),
           'retirer-suivi': ('administrateur', 'avocat'), 'reprendre-suivi': ('administrateur', 'avocat')}
DOUBLONS = ('ecarter', 'confirmer')
STATUS = {'version_perimee': '409 Conflict', 'dossier_reserve': '403 Forbidden', 'role_insuffisant': '403 Forbidden', 'livrable_absent': '404 Not Found', 'route_inconnue': '404 Not Found',
          'fichier_pas_encore_cree': '409 Conflict', 'methode_refusee': '405 Method Not Allowed'}
MESSAGES = {'version_perimee': 'Le document a changé depuis votre affichage : rechargez la page ; rien n’a été modifié.', 'dossier_reserve': 'Ce dossier est réservé.',
            'livrable_absent': 'Ce document n’est pas ou plus au registre.', 'fichier_pas_encore_cree': 'Aucun fichier à suivre pour cette version.',
            'cle_idempotence_absente': 'Requête incomplète : rechargez la page et recommencez.', 'role_insuffisant': 'Votre compte ne permet pas cette action.'}
FAIT = {'reessayer': 'Contrôle redemandé : il sera fait au prochain passage, sans attendre le délai.', 'retrouver': 'Recherche demandée : le dossier du fichier sera relu au prochain passage.',
        'retirer-suivi': 'Retiré du suivi : l’incident reste noté, le fichier n’est plus contrôlé.', 'reprendre-suivi': 'Suivi repris : le fichier sera contrôlé au prochain passage.'}


def allowed(role, route):
    """Toute écriture est déclarée ici, inconnue refusée ; le service refait ses propres contrôles."""
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    parts = route.strip('/').split('/')
    if len(parts) == 4 and parts[1] == 'doublons':
        return parts[3] in DOUBLONS and role in ('administrateur', 'avocat')
    return len(parts) == 3 and role in ACTIONS.get(parts[2], ())


def _out(value, status='200 OK'):
    from .web440 import json_out
    return json_out(value, status)


def _contexte(desk, env):
    from .membres5650 import effective_rights, hidden_for
    from .web567 import actor
    owner, role = actor(env)
    return owner, role, hidden_for(desk.c['state_dir'], owner, role), effective_rights(desk.c['state_dir'], owner, role)


def api(env, desk, auth, prefix, name, args, method):
    try:
        return _api(env, desk, auth, prefix, name, args, method)
    except Stop as ex:
        code = str(ex).split(':')[0]
        from .erreurs5634 import explain
        return _out({'error': code, 'message': MESSAGES.get(code) or explain(code, getattr(ex, 'detail', None))}, STATUS.get(code, '400 Bad Request'))


def _api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post
    owner, role, hidden, droits = _contexte(desk, env)
    route = name[len('m5689/'):].split('?', 1)[0].strip('/')
    parts = route.split('/')
    if role not in ('administrateur', 'avocat', 'assistant'):
        raise Stop('role_insuffisant')
    if method == 'GET':
        if parts == ['registre', 'etat']:
            return _out(reg.diagnostic(desk, hidden))
        if parts == ['registre', 'doublons']:
            return _out({'doublons': reg.doublons(desk, hidden=hidden)})
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    if not allowed(role, route):
        raise Stop('role_insuffisant' if len(parts) >= 3 and (parts[2] in ACTIONS or parts[-1] in DOUBLONS) else 'route_inconnue')
    data = check_post(env, auth)
    if not str(data.get('cle') or '').strip():
        raise Stop('cle_idempotence_absente')
    if parts[1] == 'doublons':
        out = reg.decider_doublon(desk, parts[2], 'ecarter' if parts[3] == 'ecarter' else 'confirmer', owner, hidden)
        return _out({**out, 'message': 'Suggestion écartée : ce sont deux documents distincts.' if parts[3] == 'ecarter' else 'Copie notée ; les deux documents restent distincts.'})
    action, version = parts[2], str(data.get('version_id') or '')
    if action == 'reessayer':
        out = reg.reessayer(desk, parts[1], version, owner, hidden, droits)
    elif action == 'retrouver':
        out = reg.retrouver(desk, parts[1], version, owner, hidden, droits)
    else:
        out = reg.retirer_suivi(desk, parts[1], version, owner, hidden, droits, reprendre=action == 'reprendre-suivi')
    return _out({**out, 'message': FAIT[action]})


def _bouton(api_name, label, fields, ghost=True):
    import uuid
    fields = {**fields, 'cle': uuid.uuid4().hex}
    return '<form class="m5-form m5-inline" data-api="%s" data-reload="1">%s<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>' % (
        e(api_name, quote=True), ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in fields.items()), ' ghost' if ghost else '', e(label))


def suivi_html(desk, row, prefix='', role='avocat'):
    """Bloc « Suivi du fichier » de la fiche : état réel du contrôle, erreur en clair, prochaine tentative, actions utiles, suggestion de doublon."""
    try:
        state = reg.controle(liv.charger(desk, row['id']))
    except Stop:
        return ''
    if not state or not row['path'] or row['lifecycle'] != 'current':
        return ''
    base = 'm5689/registre/' + row['document_id']
    fields = {'version_id': row['version_id']}
    labels = {'reessayer': 'Réessayer', 'retrouver': 'Retrouver le fichier', 'retirer_suivi': 'Retirer du suivi', 'reprendre_suivi': 'Reprendre le suivi'}
    buttons = ''.join(_bouton(base + '/' + a.replace('_', '-'), labels[a], fields, a != 'reessayer') for a in state['actions'] if role in ACTIONS.get(a.replace('_', '-'), ()))
    detail = []
    if state['echecs']:
        detail.append('%d essai(s) sans succès' % state['echecs'])
    if state['prochain'] and state['etat'] == 'incident':
        detail.append('prochain essai prévu le %s' % e(state['prochain'][:16].replace('T', ' à ')))
    duplicates = ''
    for d in reg.doublons(desk, row['document_id'])[:3]:
        other = d['other_document_id'] if d['document_id'] == row['document_id'] else d['document_id']
        decide = ''.join(_bouton('m5689/registre/doublons/%s/%s' % (d['id'], k), v, {}) for k, v in (('ecarter', 'Ce sont deux documents'), ('confirmer', 'C’est une copie'))) if role in ('administrateur', 'avocat') else ''
        duplicates += '<p class="l89-alerte">Même contenu qu’un autre document du dossier : <a href="%s">voir l’autre document</a>. Ils restent distincts.</p>%s' % (e(prefix + '/livrables/' + other, quote=True), decide)
    return ('<section class="ax-card" aria-labelledby="r93-t-suivi"><h2 id="r93-t-suivi">Suivi du fichier</h2><p><strong>%s</strong> %s</p>%s<div class="l89-actions" role="group" aria-label="Suivi du fichier">%s</div>%s</section>'
            % (e(state['suivi_label']), e(state['label']), ('<p class="ax-muted">%s</p>' % ' · '.join(detail)) if detail else '', buttons, duplicates))

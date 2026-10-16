"""5.6.52 : routes /api440/m5652/… — routines personnalisées (R01 à R09) et connexions API (I01 à I15), et leurs blocs d'interface.

Chaque objet est résolu sur le serveur (propriétaire, portée, dossiers, version) ; aucune route ne reçoit de secret en retour.
"""
from html import escape as e
import json

from .common import Stop, load_matters, matter_display

WRITE_ROUTES = ('routine/creer', 'routine/modifier', 'routine/activer', 'routine/dupliquer', 'routine/supprimer', 'routine/lancer')
READ_ROUTES = ('routines', 'routine/apercu', 'routine/historique', 'file/decider')
CONNECTION_ADMIN = ('connexion/enregistrer', 'connexion/secret', 'connexion/effacer-secret', 'connexion/deconnecter', 'connexion/capacites',
                    'connexion/oauth/debut')
CONNECTION_USE = ('connexion/tester', 'outil/appeler', 'outil/preparer', 'operation/valider', 'operation/annuler', 'operations')


def allowed(role, route):
    route = route.split('?', 1)[0]
    if role not in ('administrateur', 'avocat', 'assistant'):
        return False
    if route in READ_ROUTES:
        return True
    if route in WRITE_ROUTES:
        return role in ('administrateur', 'avocat')
    if route in CONNECTION_ADMIN:
        return role == 'administrateur' or (role == 'avocat' and route != 'connexion/effacer-secret')
    if route in CONNECTION_USE:
        return role in ('administrateur', 'avocat')
    return False


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import json_out
    from .web567 import actor
    from .web5634 import _post
    owner, role = actor(env)
    route = name[len('m5652/'):].split('?', 1)[0]
    if not allowed(role, route):
        raise Stop('role_insuffisant')
    from . import routines5652 as rt
    if method == 'GET':
        if route == 'routines':
            return json_out(rt.listing(desk, owner, role, int(args.get('limite') or 50), int(args.get('debut') or 0)))
        if route == 'routine/historique':
            return json_out({'passages': rt.runs(desk, owner, role, str(args.get('id') or ''))})
        if route == 'operations':
            from . import connexions5652 as cx
            return json_out({'operations': cx.operations(desk, owner, role, str(args.get('dossier') or ''))})
        raise Stop('methode_refusee')
    data = _post(env, auth, 200_000)
    if route.startswith('connexion/') or route.startswith('outil/') or route.startswith('operation/'):
        from . import connexions5652 as cx
        return json_out(cx.api(desk, owner, role, route, data, prefix))
    if route == 'routine/apercu':
        try:
            fields = rt._clean(desk, owner, {**data, 'title': data.get('title') or data.get('nom') or 'aperçu', 'instruction': data.get('instruction') or 'aperçu'},
                               rt._hidden_for(desk, owner))
        except Stop as ex:
            if str(ex) in ('consigne_routine_requise', 'dossiers_routine_requis'):
                fields = {'schedule_json': json.dumps(rt.normalize_schedule({'freq': data.get('freq'), 'time': data.get('time'), 'monthday': data.get('monthday'),
                                                                             'date': data.get('date'), 'days': ''.join(k for k, _ in rt.DAYS
                                                                                                                      if data.get('day_' + k) in (True, 'true', 'on'))})),
                          'timezone': data.get('timezone')}
            else:
                raise
        tzname = fields.get('timezone') or str(rt.cabinet_tz(desk))
        nxt = rt.upcoming(json.loads(fields['schedule_json']), tzname, count=3)
        return json_out({'passages': nxt, 'message': ' ; '.join(x['label'] + (' (%s)' % x['note'] if x['note'] else '') for x in nxt) or 'Aucun passage à venir.',
                         'noreload': True})
    if route == 'routine/creer':
        row = rt.create(desk, owner, role, data)
        return json_out({'id': row['id'], 'message': 'Routine enregistrée.'})
    if route == 'routine/modifier':
        row = rt.update(desk, owner, role, str(data.get('id') or ''), data, data.get('version') or 0)
        return json_out({'id': row['id'], 'version': row['version'], 'message': 'Routine modifiée.'})
    if route == 'routine/activer':
        row = rt.set_enabled(desk, owner, role, str(data.get('id') or ''), str(data.get('enabled')).lower() in ('true', '1', 'on', 'oui'), data.get('version') or 0)
        return json_out({'id': row['id'], 'enabled': bool(row['enabled']), 'message': 'Routine activée.' if row['enabled'] else 'Routine en pause.'})
    if route == 'routine/dupliquer':
        row = rt.duplicate(desk, owner, role, str(data.get('id') or ''))
        return json_out({'id': row['id'], 'message': 'Copie créée (désactivée).'})
    if route == 'routine/supprimer':
        out = rt.delete(desk, owner, role, str(data.get('id') or ''), data.get('version') or 0)
        return json_out({**out, 'message': 'Routine supprimée' + (' ; arrêt demandé pour %d travail(aux) en cours.' % out['arret_demande'] if out['arret_demande'] else '.')})
    if route == 'routine/lancer':
        occ = rt.run_now(desk, owner, role, str(data.get('id') or ''), str(data.get('click_key') or ''))
        return json_out({'occurrence': occ['id'], 'message': 'Routine lancée.' if occ['state'] == 'en_file' else 'Déjà lancée.'})
    if route == 'file/decider':   # éléments du cabinet (sans dossier) de la file « À décider / À relire »
        from . import file5651
        from .membres5650 import viewer_hidden
        out = file5651.decide(desk, owner, str(data.get('key') or ''), str(data.get('state') or ''), str(data.get('revision') or ''),
                              str(data.get('motif') or ''), 1, prefix, viewer_hidden() or frozenset(), expected_matter='', actor=owner, role=role)
        return json_out({**out, 'message': 'Décision enregistrée.'})
    raise Stop('route_inconnue')


# ------------------------------------------------------------------------------------------------ interface des routines (R01)
def _days_boxes(prefix_id, days):
    from .routines5652 import DAYS
    return ''.join('<label class="r52-day"><input type="checkbox" name="day_%s"%s> %s</label>' % (k, ' checked' if k in days else '', e(lbl)) for k, lbl in DAYS)


def _form(desk, prefix, action, row=None, matters=()):
    from . import routines5652 as rt
    row = row or {}
    sched = row.get('schedule') or {'freq': 'jours', 'days': '12345', 'time': '08:30'}
    chosen = set(row.get('matters') or [])
    legacy = bool(row.get('legacy_kind'))
    types = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == row.get('task_type', 'synthese') else '', e(v))
                    for k, v in rt.TASK_TYPES.items() if (k not in rt.LEGACY or legacy or not row))
    freqs = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == sched.get('freq') else '', e(v)) for k, v in rt.FREQS.items())
    opts = ''.join('<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] in chosen else '', e(matter_display(m)))
                   for m in matters)
    hidden = ('<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">' % (e(row['id'], quote=True), row['version'])) if row else \
        '<input type="hidden" name="request_key" data-r52-key>'
    templates = ''
    if not row:
        templates = '<label>Partir d’un modèle<select data-r52-template><option value="">Routine libre</option>%s</select></label>' % ''.join(
            '<option value="%s" data-r52=\'%s\'%s>%s%s</option>' % (k, e(json.dumps(v, ensure_ascii=False), quote=True), ' disabled' if v.get('consultation') else '',
                                                                      e(v['title']), ' (consultation, non programmable)' if v.get('consultation') else '')
            for k, v in rt.TEMPLATES.items())
    return ('<form class="m5-form r52-form" data-api="m5652/%s" data-reload>%s%s'
            '<label>Nom<input name="title" required maxlength="120" value="%s"></label>'
            '<label>Résultat attendu<select name="task_type"%s>%s</select></label>'
            '<label>Objectif court (facultatif)<input name="objective" maxlength="240" value="%s"></label>'
            '<label>Consigne<textarea name="instruction" rows="3" maxlength="4000" placeholder="Ex. Préparer un point des courriels reçus et des audiences des quatorze prochains jours.">%s</textarea></label>'
            '<input type="hidden" name="dossiers" value="%s"><label>Dossiers concernés (facultatif)<select data-r52-matters multiple size="4">%s</select></label>'
            '<fieldset class="r52-when"><legend>Quand</legend><label>Fréquence<select name="freq">%s</select></label><div class="r52-days">%s</div>'
            '<label>Heure<input name="time" type="time" value="%s" required></label><label>Jour du mois<input name="monthday" type="number" min="1" max="28" value="%s"></label>'
            '<label>Date (une seule fois)<input name="date" type="date" value="%s"></label></fieldset>'
            '<details class="r52-advanced"><summary>Réglages avancés</summary><label>Fuseau horaire<input name="timezone" value="%s" maxlength="60"></label>'
            '<label>Rattrapage après un arrêt (heures)<input name="catchup_hours" type="number" min="0" max="72" value="%s"></label>'
            '<label>Dossiers traités au plus par passage<input name="max_matters" type="number" min="1" max="50" value="%s"></label>'
            '<label>Portée<select name="scope"%s><option value="personnel"%s>Personnelle</option><option value="cabinet"%s>Cabinet</option></select></label>'
            '<p class="ax-muted">Mode « Préparation et relecture » : la routine lit, analyse et prépare ; elle n’envoie, ne signe, ne facture et ne prélève jamais.</p>'
            '</details><div class="r52-actions"><button class="ax-btn ghost" type="button" data-r52-preview>Aperçu des 3 prochains passages</button>'
            '<button class="ax-btn" type="submit">Enregistrer</button></div><p class="r52-preview" role="status" aria-live="polite"></p>'
            '<p role="status" class="m5-status"></p></form>') % (
        action, hidden, templates, e(row.get('title', ''), quote=True), ' disabled' if legacy else '', types, e(row.get('objective', ''), quote=True),
        e(row.get('instruction', '')), e('\n'.join(sorted(chosen)), quote=True), opts, freqs, _days_boxes('', sched.get('days', '12345')),
        e(sched.get('time', '08:30'), quote=True), e(str(sched.get('monthday', 1)), quote=True), e(sched.get('date', ''), quote=True),
        e(row.get('timezone') or str(rt.cabinet_tz(desk)), quote=True), e(str(row.get('catchup_hours', 4)), quote=True), e(str(row.get('max_matters', 10)), quote=True),
        ' disabled' if legacy else '', ' selected' if row.get('scope', 'personnel') == 'personnel' else '', ' selected' if row.get('scope') == 'cabinet' else '')


def routines_block(desk, auth, prefix, role=None):
    """Bloc « Routines du cabinet » d'Aujourd'hui : cartes, ajout, modification, activation, duplication, suppression, lancement."""
    from . import routines5652 as rt
    from .i18n5631 import role as current_role
    role = role or current_role()
    owner = str(auth.get('owner') or 'cabinet')
    data = rt.listing(desk, owner, role)
    try:
        matters = load_matters(desk.c)
    except Stop:
        matters = []
    cards = []
    for r in data['routines']:
        legacy = r['legacy_kind']
        nxt = r['next'][0]['label'] if r['next'] else ''
        last = r['last']
        last_txt = ''
        if last:
            res = last.get('result') or {}
            last_txt = '%s le %s' % (rt.STATES.get(last['state'], last['state']), last['local_label'])
            if res.get('impossible'):
                last_txt += ' — ' + '; '.join(res['impossible'][:2])
        running = bool(last and last['state'] in rt.ACTIVE)
        state_badge = '<span class="r52-badge r52-%s">%s</span>' % ('on' if r['enabled'] else 'off', 'Activée' if r['enabled'] else 'En pause')
        caps = r['capabilities']['manquantes']
        cap_txt = ('<span class="r52-missing">Manque : %s</span>' % e(', '.join(rt.CAP_LABELS.get(c, c) for c in caps))) if caps else ''
        if legacy:
            run = '<button type="button" class="ax-btn ghost c5614-run" data-routine="%s"%s>Lancer</button>' % (legacy, ' disabled' if running else '')
            view = '<button type="button" class="ax-btn ghost c5614-view" data-tab-open="%s">Voir</button>' % legacy
        else:
            run = ('<form class="m5-form r52-inline" data-api="m5652/routine/lancer" data-reload><input type="hidden" name="id" value="%s">'
                   '<input type="hidden" name="click_key" data-r52-key><button class="ax-btn ghost" type="submit"%s>Lancer</button></form>') % (
                e(r['id'], quote=True), ' disabled' if running else '')
            report = None
            if last and last.get('result_ref') and r['task_type'] in ('synthese', 'nouveautes'):
                from . import routines520
                report = routines520.latest(desk, 'r52:' + r['id'])
            view = ('<details class="r52-report"><summary>Compte rendu</summary>%s</details>' % routines520.render_markdown(report['text'])) if report else ''
        edit = toggle = menu = ''
        if r['editable']:
            edit = '<details class="r52-edit"><summary class="ax-btn ghost">Modifier</summary>%s</details>' % _form(desk, prefix, 'routine/modifier', r, matters)
            toggle = ('<form class="m5-form r52-inline" data-api="m5652/routine/activer" data-reload><input type="hidden" name="id" value="%s">'
                      '<input type="hidden" name="version" value="%d"><input type="hidden" name="enabled" value="%s">'
                      '<button class="ax-btn ghost" type="submit" role="switch" aria-checked="%s">%s</button></form>') % (
                e(r['id'], quote=True), r['version'], 'false' if r['enabled'] else 'true', 'true' if r['enabled'] else 'false',
                'Activée — mettre en pause' if r['enabled'] else 'En pause — activer')
            concerned = rt.concerned_jobs(desk, r['id'])
            delete = '' if legacy else (
                '<details class="r52-delete"><summary>Supprimer</summary><p>%s</p><form class="m5-form r52-inline" data-api="m5652/routine/supprimer" data-reload>'
                '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d"><button class="ax-btn warn" type="submit">Confirmer la suppression</button>'
                '</form></details>') % (e('Passages concernés : %d (les non commencés sont annulés, les travaux en cours reçoivent un arrêt). Les comptes rendus restent consultables.'
                                          % len(concerned)), e(r['id'], quote=True), r['version'])
            menu = ('<details class="r52-menu"><summary class="ax-btn ghost" aria-label="Autres actions">⋯</summary><div>'
                    '<form class="m5-form r52-inline" data-api="m5652/routine/dupliquer" data-reload><input type="hidden" name="id" value="%s">'
                    '<button class="ax-btn ghost" type="submit">Dupliquer</button></form>%s</div></details>') % (e(r['id'], quote=True), delete)
        cards.append('<div class="c5614-routine r52-card%s" data-kind="%s" id="r52-%s"><div class="r52-head"><strong>%s</strong>%s</div>'
                     '<span class="c5614-state">%s</span><span class="r52-meta">%s%s%s</span>%s'
                     '<span class="c5614-actions">%s%s%s%s%s</span></div>' % (
                         '' if r['enabled'] else ' r52-paused', e(legacy or r['id'], quote=True), e(r['id'], quote=True), e(r['title']), state_badge,
                         e(r['objective'] or rt.TASK_TYPES.get(r['task_type'], '')), ('Prochain passage : ' + e(nxt)) if nxt else 'Aucun passage prévu',
                         (' · Dernier : ' + e(last_txt)) if last_txt else '', (' · ' + e(r['scope'] == 'cabinet' and 'Cabinet' or 'Personnelle')), cap_txt,
                         run, view, toggle, edit, menu))
    paused = bool(desk.settings('missions567:pause', False))
    add = ''
    if role in ('administrateur', 'avocat'):
        add = '<details class="r52-add" id="r52-add"><summary class="ax-btn">Ajouter une routine</summary>%s</details>' % _form(desk, prefix, 'routine/creer', None, matters)
    return ('<section class="c5614-routines r52" id="c5614-routines" aria-label="Routines du cabinet"><div class="r52-top"><h2 class="r52-title">Routines du cabinet</h2>'
            '<div class="c5614-side">%s<button type="button" class="ax-btn %s" id="c5614-pause" data-paused="%s" aria-pressed="%s">%s</button>'
            '<a class="ax-btn ghost" href="%s">Réglages</a></div></div><div class="c5614-bar r52-cards">%s</div>'
            '<p id="c5614-routines-status" role="status" class="c530-sr"></p></section>') % (
        add, 'warn' if paused else 'ghost', '1' if paused else '0', 'true' if paused else 'false', 'IA en pause — reprendre' if paused else 'Pause de l’IA',
        e(prefix + '/aujourdhui?vue=essentiel', quote=True), ''.join(cards) or '<p class="ax-muted">Aucune routine.</p>')

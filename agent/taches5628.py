"""5.6.28 : vue « Tâches » d'Agenda et tâches, simple à lire — liste compacte groupée par échéance à gauche, ajout d'une tâche à droite.

Toutes les actions existantes sont conservées (terminer, commencer, confier à l'IA, modifier, programmer dans l'agenda, supprimer ou
annuler) et passent par les mêmes formulaires serveur qu'avant ; seule la présentation change. Filtres : période (en retard, cette
semaine, sans échéance) et dossier.
"""
from datetime import date, timedelta
from html import escape as e

from .common import load_matters
from .improvements36 import matter_option

GROUPS = (('retard', 'En retard'), ('aujourdhui', 'Aujourd’hui'), ('semaine', 'Cette semaine'), ('plus_tard', 'Plus tard'), ('sans', 'Sans échéance'))
FILTERS = (('', 'Toutes'), ('retard', 'En retard'), ('semaine', 'Cette semaine'), ('sans', 'Sans échéance'))
STATUS = {'open': 'À faire', 'todo': 'À faire', 'in_progress': 'En cours', 'blocked': 'Bloquée'}


def _bucket(due, today):
    raw = str(due or '')[:10]
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return 'sans', None
    if day < today:
        return 'retard', day
    if day == today:
        return 'aujourdhui', day
    if day <= today + timedelta(days=7):
        return 'semaine', day
    return 'plus_tard', day


def _due_label(day, today):
    if day is None:
        return ''
    if day == today:
        return 'aujourd’hui'
    if day == today + timedelta(days=1):
        return 'demain'
    delta = (day - today).days
    if delta < 0:
        return 'en retard de %d j' % -delta
    return day.strftime('%d/%m/%Y')


def collect(desk):
    """Tâches ouvertes : registre local et tâches Nextcloud synchronisées (sans doublon)."""
    rows = [dict(x, source='local') for x in desk.db.execute("SELECT id,matter,title,due,status FROM tasks WHERE status='open' ORDER BY due LIMIT 300")]
    try:
        external = [dict(x, source='nextcloud') for x in desk.db.execute(
            "SELECT id,matter,title,due,status,external_uid FROM work_tasks_v211 WHERE status IN ('todo','in_progress','blocked') ORDER BY due LIMIT 300")]
    except Exception:   # pylint: disable=broad-except
        external = []
    ids = {x['id'] for x in external}
    return [r for r in rows if r['id'] not in ids] + external


def page(desk, link, form, url, args, last_sync='', sync_form=''):
    from .agenda520 import enabled as personal_enabled
    today = date.today()
    matters = {m['id']: m for m in load_matters(desk.c)}
    personal = personal_enabled(desk)
    chosen = str(args.get('filtre') or '')
    if chosen not in dict(FILTERS):
        chosen = ''
    matter_filter = str(args.get('dossier') or '')
    items = collect(desk)
    if matter_filter:
        items = [t for t in items if t['matter'] == matter_filter]
    grouped = {k: [] for k, _ in GROUPS}
    for t in items:
        key, day = _bucket(t['due'], today)
        t['_day'] = day
        grouped[key].append(t)
    if chosen == 'retard':
        grouped = {k: (v if k == 'retard' else []) for k, v in grouped.items()}
    elif chosen == 'semaine':
        grouped = {k: (v if k in ('retard', 'aujourdhui', 'semaine') else []) for k, v in grouped.items()}
    elif chosen == 'sans':
        grouped = {k: (v if k == 'sans' else []) for k, v in grouped.items()}
    late = len([t for t in items if _bucket(t['due'], today)[0] == 'retard'])
    options = ''.join('<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] == matter_filter else '', e(matter_option(m)))
                      for m in matters.values())
    head = ('<div class="tk28-head"><div><h2>Mes tâches</h2><p class="tk28-muted">%d tâche(s) ouverte(s)%s</p></div>'
            '<form class="tk28-filter" method="get" action="%s"><input type="hidden" name="vue" value="taches">%s'
            '<select name="dossier" aria-label="Filtrer par dossier" onchange="this.form.submit()"><option value="">Tous les dossiers</option>%s</select>'
            '<noscript><button>Filtrer</button></noscript></form></div>'
            '<nav class="tk28-chips" aria-label="Période">%s</nav>') % (
        len(items), (' dont <strong class="tk28-late">%d en retard</strong>' % late) if late else '', e(url('/planning')),
        ('<input type="hidden" name="filtre" value="%s">' % e(chosen, quote=True)) if chosen else '', options,
        ''.join('<a href="%s"%s>%s</a>' % (e(url('/planning', vue='taches', **({'filtre': k} if k else {}), **({'dossier': matter_filter} if matter_filter else {}))),
                                          ' aria-current="page"' if k == chosen else '', e(label)) for k, label in FILTERS))
    body = ''
    for key, label in GROUPS:
        rows = grouped[key]
        if not rows:
            continue
        body += '<section class="tk28-group tk28-%s"><h3>%s <span class="tk28-count">%d</span></h3><ul class="tk28-list">%s</ul></section>' % (
            key, e(label), len(rows), ''.join(_row(t, matters, personal, form, link, today) for t in rows))
    if not body:
        body = '<div class="tk28-empty"><p>Aucune tâche %s.</p><p class="tk28-muted">Ajoutez-en une avec le formulaire « Ajouter une tâche ».</p></div>' % (
            'pour ce filtre' if (chosen or matter_filter) else 'ouverte')
    add_options = '<option value="">Cabinet, sans dossier</option>' + ''.join(
        '<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] == matter_filter else '', e(matter_option(m))) for m in matters.values())
    side = ('<section class="tk28-card tk28-add"><h2>Ajouter une tâche</h2>%s</section>'
            '<section class="tk28-card"><h2>Nextcloud</h2><p class="tk28-muted">Dernière synchronisation : <strong>%s</strong>. Seules les tâches créées par '
            'AxiorHub se modifient ici ; les autres listes restent en lecture seule.</p>%s</section>') % (
        form('create_local_task', 'Ajouter la tâche', extra=
             '<label>Consigne<textarea name="title" required minlength="3" maxlength="2000" rows="3" placeholder="Ex. relancer le confrère sur les pièces 12 à 15"></textarea></label>'
             '<label>Dossier<select name="matter">' + add_options + '</select></label>'
             '<label>Échéance (facultative)<input type="date" name="due"></label>'),
        e(last_sync or 'jamais'), sync_form)
    return '<div class="tk28"><div class="tk28-main">%s%s</div><div class="tk28-side" role="complementary" aria-label="Ajouter une tâche">%s</div></div>' % (head, body, side)


def _row(t, matters, personal, form, link, today):
    local = t['source'] == 'local'
    own = str(t.get('external_uid', '')).startswith('axiorhub-')
    action = 'update_local_task' if local else ('update_work_task' if own else ('edit_personal_task' if personal else ''))
    if local:
        done = form('update_local_task', '✓', {'task_id': t['id'], 'status': 'closed'})
    elif action:
        done = form(action, '✓', {'task': t['id'], 'status': 'completed'})
    else:
        done = '<span class="tk28-ro" title="Tâche personnelle en lecture seule">·</span>'
    done = done.replace('<button>✓</button>', '<button class="tk28-done" title="Marquer terminée" aria-label="Marquer terminée">✓</button>', 1)
    chips = []
    if t['matter'] in matters:
        chips.append(link('/dossier', matter_option(matters[t['matter']]), id=t['matter']).replace('<a ', '<a class="tk28-chip" ', 1))
    if t['_day']:
        chips.append('<span class="tk28-chip tk28-due%s">%s</span>' % (' late' if t['_day'] < today else '', e(_due_label(t['_day'], today))))
    if t['status'] in ('in_progress', 'blocked'):
        chips.append('<span class="tk28-chip">%s</span>' % e(STATUS[t['status']]))
    chips.append('<span class="tk28-src">%s</span>' % ('Local' if local else 'Nextcloud'))
    actions = []
    if local:
        actions.append(form('delegate_local_task', 'Confier à l’IA : actualiser et préparer', {'task_id': t['id']}))
        actions.append(form('update_local_task', 'Marquer terminée', {'task_id': t['id'], 'status': 'closed'}))
        actions.append(form('cancel_local_task', 'Supprimer', {'task_id': t['id'], 'confirm': 'yes'}))
        edit_action, ident = 'update_local_task', {'task_id': t['id'], 'status': 'open'}
        matter_select = '<label>Dossier<select name="matter"><option value="">Sans dossier</option>%s</select></label>' % ''.join(
            '<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if t['matter'] == m['id'] else '', e(matter_option(m))) for m in matters.values())
    else:
        actions.append(form('assistant_ask', 'Demander à l’IA de préparer', {'matter': t['matter'] if t['matter'] in matters else ''},
                            '<input type="hidden" name="question" value="%s">' % e('Prépare concrètement cette tâche : ' + t['title'] +
                                                                                 '. Indique les sources consultées, les points incertains et les actions à faire valider.', quote=True)))
        if action:
            if t['status'] != 'in_progress':
                actions.append(form(action, 'Commencer', {'task': t['id'], 'status': 'in_progress'}))
            actions.append(form(action, 'Marquer terminée', {'task': t['id'], 'status': 'completed'}))
            actions.append(form(action, 'Annuler', {'task': t['id'], 'status': 'cancelled'}))
        edit_action, ident = ('edit_work_task' if own else 'edit_personal_task'), {'task': t['id']}
        matter_select = ''
    editor = (form(edit_action, 'Enregistrer les modifications', ident,
                   '<label>Intitulé<input name="title" required minlength="3" maxlength="500" value="%s"></label>%s'
                   '<label>Échéance<input type="date" name="due" value="%s"></label>' % (e(t['title'], quote=True), matter_select, e(str(t['due'] or '')[:10], quote=True)))
              + form('schedule_work_task', 'Programmer dans l’agenda', {'task': t['id']},
                     '<label>Début<input type="datetime-local" name="start" required></label>'
                     '<label>Durée (minutes)<input type="number" name="duration_minutes" value="60" min="15" max="480" step="15"></label>'))
    return ('<li class="tk28-item" id="task-%s">%s<div class="tk28-body"><p class="tk28-title">%s</p><div class="tk28-chips-row">%s</div>'
            '<details class="tk28-more"><summary>Actions</summary><div class="tk28-actions">%s</div>'
            '<details class="ws-task-editor"><summary>Modifier ou programmer</summary>%s</details></details></div></li>') % (
        e(t['id'], quote=True), done, e(t['title']), ''.join(chips), ''.join(actions), editor)

"""Page « Échéances » et routes JSON (4.5.0). Les dates viennent exclusivement du moteur de délais."""
from .common import matter_display
from datetime import date
from html import escape as e
import re
from urllib.parse import urlencode

from . import deadlines450 as engine
from . import echeances450 as ech
from .common import Stop, load_matters


def _dav(desk):
    from . import office440
    return office440.dav_client(desk)


def _date(value, field='date'):
    try:
        return date.fromisoformat(str(value or ''))
    except ValueError:
        raise Stop('date_invalide') from None


def _opts(data):
    distance = str(data.get('distance') or 'none')
    if distance not in engine.DISTANCES:
        raise Stop('distance_invalide')
    return distance, bool(data.get('alsace')), str(data.get('regime_date') or '')


def preview(data):
    rule_id = str(data.get('rule_id', ''))
    start = _date(data.get('start'))
    distance, alsace, regime = _opts(data)
    try:
        result = engine.compute(rule_id, start, distance=distance, alsace=alsace, regime_date=regime or None)
    except engine.DeadlineError as ex:
        raise Stop(ex.message) from None
    result['due_label'] = engine.fr_date(date.fromisoformat(result['due']))
    return result


def handle(desk, name, data, method='POST', args=None):
    """Dispatch d'une route /api440/deadline/... ; lève Stop(code) en cas de refus."""
    args = args or {}
    if method == 'GET':
        if name == 'deadlines':
            return {'deadlines': ech.listing(desk, str(args.get('matter', '')), args.get('closed') == '1')}
        if name == 'deadline/journal':
            return {'journal': ech.journal(desk, str(args.get('id', '')))}
        raise Stop('route_inconnue')
    ident = str(data.get('id', ''))
    if name == 'deadline/preview':
        return preview(data)
    if name == 'deadline/create':
        distance, alsace, regime = _opts(data)
        _date(data.get('start'))
        return ech.create_manual(desk, _dav(desk), str(data.get('matter', '')), str(data.get('rule_id', '')),
                                 str(data.get('start')), distance, alsace, regime, str(data.get('source_path', ''))[:500],
                                 str(data.get('note', ''))[:500])
    if name == 'deadline/confirm':
        distance = data.get('distance')
        if distance is not None and distance not in engine.DISTANCES:
            raise Stop('distance_invalide')
        return ech.confirm(desk, _dav(desk), ident, str(data.get('rule_id', '')), distance,
                           bool(data['alsace']) if 'alsace' in data else None,
                           str(data['regime_date']) if 'regime_date' in data else None)
    if name == 'deadline/correct':
        new_due = str(data.get('new_due') or '')
        if new_due:
            _date(new_due)
        start = str(data.get('start_date') or '')
        if start:
            _date(start)
        regime = str(data['regime_date']) if 'regime_date' in data else None
        if regime:
            _date(regime)
        distance = data.get('distance')
        if distance is not None and distance not in engine.DISTANCES:
            raise Stop('distance_invalide')
        return ech.correct(desk, _dav(desk), ident, str(data.get('reason', '')), new_due, start,
                           str(data.get('rule_id', '')), distance, regime)
    if name == 'deadline/close':
        return ech.close(desk, _dav(desk), ident, str(data.get('outcome', '')), str(data.get('reason', '')))
    if name == 'deadline/act':
        return ech.mark_act_prepared(desk, ident, bool(data.get('prepared', True)))
    if name == 'deadline/check':
        report = ech.cross_check(desk, _dav(desk))
        return report
    if name == 'settings/deadlines':
        desk.setting('automation:deadlines450_enabled', bool(data.get('enabled')))
        desk.setting('automation:deadline_calendar450', bool(data.get('calendar')))
        desk.audit('echeances_450_reglages', {'enabled': bool(data.get('enabled')), 'calendar': bool(data.get('calendar'))})
        return {'saved': True}
    raise Stop('route_inconnue')


# ---------------------------------------------------------------- page
def _days_badge(row, today):
    if not row.get('due'):
        return '<span class="ex-badge warn">date à compléter</span>'
    days = (date.fromisoformat(row['due']) - today).days
    if row['status'] in ('terminee', 'annulee'):
        return ''
    if days < 0:
        return '<span class="ex-badge bad">dépassée de %d j</span>' % -days
    if days == 0:
        return '<span class="ex-badge bad">aujourd’hui</span>'
    cls = 'bad' if days <= 2 else ('warn' if days <= 14 else '')
    return '<span class="ex-badge %s">dans %d j</span>' % (cls, days)


def _rule_options(start_event, selected, rules):
    return ''.join('<option value="%s"%s>%s (%s)</option>' % (
        e(r['id'], quote=True), ' selected' if r['id'] == selected else '', e(r['label']), e(', '.join(r['articles'])))
        for r in rules if r['start_event'] == start_event)


def _item(row, rules, prefix, today):
    calc = row['calc'] if isinstance(row['calc'], dict) else {}
    steps = ''.join('<li>%s</li>' % e(s) for s in calc.get('steps', []))
    warns = ''.join('<li>%s</li>' % e(w) for w in calc.get('warnings', []))
    spec = next((r for r in rules if r['id'] == row['rule_id']), None)
    articles = ', '.join(spec['articles']) if spec else ''
    source = ''
    if row['source_path']:
        source = ('<a href="%s">%s</a>' % (e(prefix + '/documents/edit?' + urlencode({'path': row['source_path'], 'matter': row['matter']})),
                                           e(row['source_path'].rsplit('/', 1)[-1])))
    else:
        source = 'saisie à la main'
    excerpt = '<blockquote>%s</blockquote>' % e(row['source_excerpt']) if row['source_excerpt'] else ''
    distance = ''.join('<option value="%s"%s>%s</option>' % (v, ' selected' if row['distance'] == v else '', t) for v, t in (
        ('none', 'Aucun délai de distance'), ('outre_mer', 'Demeurant outre-mer (+1 mois)'), ('etranger', 'Demeurant à l’étranger (+2 mois)')))
    closed = row['status'] in ('terminee', 'annulee')
    error = '<p class="ax-alert">%s</p>' % e(row['error']) if row['error'] else ''
    actions = ''
    if not closed:
        actions = (
            '<div class="ex-actions">'
            '<fieldset><legend>Confirmer ou changer la règle</legend>'
            '<label>Règle<select class="ex-rule">%s</select></label>'
            '<label>Délai de distance<select class="ex-distance">%s</select></label>'
            '<label class="ax-check"><input type="checkbox" class="ex-alsace"%s> Alsace-Moselle (jours fériés locaux)</label>'
            '<label>Date de la déclaration d’appel <small>(régime des appels, si la règle l’exige)</small>'
            '<input type="date" class="ex-regime" value="%s"></label>'
            '<button type="button" class="ax-btn ex-confirm">%s</button></fieldset>'
            '<fieldset><legend>Corriger (motif obligatoire, consigné au journal)</legend>'
            '<label>Nouvelle date de départ <input type="date" class="ex-newstart"></label>'
            '<label>ou échéance imposée <input type="date" class="ex-newdue"></label>'
            '<label>Motif <input type="text" class="ex-reason" maxlength="300" placeholder="Ex. signification reçue le …"></label>'
            '<button type="button" class="ax-btn ghost ex-correct">Corriger</button></fieldset>'
            '<fieldset><legend>Suites</legend>'
            '<label class="ax-check"><input type="checkbox" class="ex-act"%s> Acte ou brouillon préparé</label>'
            '<div class="ax-actions"><button type="button" class="ax-btn ghost ex-done">Marquer comme accomplie</button>'
            '<button type="button" class="ax-btn danger ex-cancel">Annuler l’échéance</button></div></fieldset></div>') % (
            _rule_options(row['start_event'], row['rule_id'], rules), distance, ' checked' if row['alsace'] else '',
            e(row['regime_date'], quote=True), 'Confirmer cette échéance' if row['status'] == 'a_confirmer' else 'Recalculer',
            ' checked' if row['act_prepared'] else '')
    due_label = row.get('due_label') or 'Échéance à compléter'
    return ('<article class="ex-item st-%s" id="e-%s" data-id="%s" data-status="%s"><details%s><summary>'
            '<strong class="ex-due">%s</strong> %s <span class="ex-rule-name">%s</span>'
            '<span class="ax-muted"> · %s</span> <span class="ex-badge st">%s</span></summary>'
            '<div class="ex-body"><dl>'
            '<dt>Événement de départ</dt><dd>%s, le %s</dd>'
            '<dt>Règle appliquée</dt><dd>%s <span class="ax-muted">(%s)</span></dd>'
            '<dt>Source</dt><dd>%s%s</dd><dt>Agenda</dt><dd>%s</dd></dl>%s'
            '<h4>Calcul</h4><ol class="ex-steps">%s</ol>%s'
            '<p><button type="button" class="ax-btn ghost ex-journal">Voir le journal</button></p><ul class="ex-log" hidden></ul>%s'
            '</div></details></article>') % (
        e(row['status']), e(row['id']), e(row['id']), e(row['status']), ' open' if row['status'] in ('a_confirmer', 'a_completer') else '',
        e(due_label), _days_badge(row, today), e(row['rule_label']), e(row['matter_label']), e(row['status_label']),
        e(row['start_event_label']), e(engine.fr_date(date.fromisoformat(row['start_date']))),
        e(row['rule_label']), e(articles), source, excerpt, e(CAL_LABELS.get(row['calendar_state'], row['calendar_state'] or 'pas encore inscrite')), error,
        steps or '<li class="ax-muted">Pas encore de calcul.</li>', '<ul class="ex-warn">%s</ul>' % warns if warns else '', actions)


CAL_LABELS = {'cree': 'inscrite à l’agenda', 'mis_a_jour': 'inscrite à l’agenda (mise à jour)',
              'non_configure': 'agenda non configuré'}
ANOMALY_LABELS = {'date_manquante': 'Date manquante', 'agenda_absent': 'Absente de l’agenda', 'agenda_divergent': 'Agenda divergent',
                  'deux_dates': 'Dates contradictoires', 'sans_acte': 'Aucun acte préparé', 'depassee': 'Échéance dépassée'}


def page(desk, auth, prefix, args, shell):
    today = date.today()
    ech.ensure_schema(desk)
    rows = ech.listing(desk, include_closed=args.get('closed') == '1')
    rules = engine.rules_for_ui()
    report = ech.cross_check(desk, None, today)
    anomalies = report['anomalies']
    anomaly_html = ''
    if anomalies:
        anomaly_html = '<section class="ax-card ex-anomalies"><h2>À vérifier (%d)</h2><ul>%s</ul></section>' % (
            len(anomalies), ''.join('<li><strong>%s</strong> – %s – %s <span class="ax-muted">(%s)</span></li>' % (
                e(ANOMALY_LABELS.get(a['kind'], a['kind'])), e(a['matter_label']), e(a['message']), e(a['rule_label'])) for a in anomalies))
    last = desk.settings('deadlines450:last_check', {}) or {}
    matters = sorted(load_matters(desk.c), key=lambda m: matter_display(m).casefold())
    matter_opts = ''.join('<option value="%s">%s</option>' % (e(m['id'], quote=True), e(matter_display(m))) for m in matters[:300])
    rule_opts = ''.join('<option value="%s" data-regime="%d">%s – %s</option>' % (
        e(r['id'], quote=True), int(r['regime']), e(r['start_event_label']), e(r['label'])) for r in rules)
    items = ''.join(_item(r, rules, prefix, today) for r in rows) or (
        '<div class="ax-empty"><p>Aucune échéance en cours.</p><p class="ax-muted">Elles apparaissent ici dès que l’agent trouve une signification, '
        'une notification ou une déclaration d’appel dans un dossier, ou quand vous en ajoutez une.</p></div>')
    enabled = bool(desk.settings('automation:deadlines450_enabled', True))
    cal = bool(desk.settings('automation:deadline_calendar450', True))
    body = (
        '<h1>Échéances de procédure</h1>'
        '<p class="ax-muted">Chaque délai est calculé par une règle du Code de procédure civile, jamais par le modèle : l’agent repère seulement l’événement de départ et sa date dans la pièce. '
        'Vous voyez la règle, le calcul et la source, et vous pouvez corriger (le motif est consigné).</p>'
        '<p class="ax-note"><strong>Périmètre :</strong> procédure civile et commerciale de droit commun (appel, opposition, pourvoi, circuit de l’appel pour les appels formés à compter du 1er septembre 2024). '
        'Non couverts : prud’hommes, juridictions administratives, matière pénale, juge aux affaires familiales, procédures collectives, anciens appels. Pour ceux-là, calculez à la main.</p>'
        + anomaly_html +
        '<section class="ax-card"><div class="ax-actions"><button type="button" class="ax-btn ghost" id="ex-check">Vérifier l’agenda maintenant</button>'
        '<a class="ax-btn ghost" href="%s">%s</a>'
        '<span class="ax-muted" id="ex-check-result">%s</span></div></section>'
        '<section id="ex-list">%s</section>'
        '<section class="ax-card" id="ex-new"><h2>Ajouter ou simuler une échéance</h2>'
        '<div class="ex-form">'
        '<label>Dossier<select id="ex-matter">%s</select></label>'
        '<label>Règle<select id="ex-newrule">%s</select></label>'
        '<label>Date de l’événement de départ<input type="date" id="ex-start"></label>'
        '<label id="ex-regime-wrap">Date de la déclaration d’appel<input type="date" id="ex-newregime"></label>'
        '<label>Délai de distance<select id="ex-newdistance"><option value="none">Aucun</option><option value="outre_mer">Outre-mer (+1 mois)</option><option value="etranger">Étranger (+2 mois)</option></select></label>'
        '<label class="ax-check"><input type="checkbox" id="ex-newalsace"> Alsace-Moselle</label></div>'
        '<div class="ax-actions"><button type="button" class="ax-btn ghost" id="ex-preview">Calculer</button>'
        '<button type="button" class="ax-btn" id="ex-create" disabled>Enregistrer et inscrire à l’agenda</button></div>'
        '<div id="ex-preview-out" aria-live="polite"></div></section>'
        '<section class="ax-card"><h2>Réglages</h2>'
        '<label class="ax-check"><input type="checkbox" id="ex-enabled"%s> Repérer automatiquement les événements de départ dans les nouvelles pièces</label>'
        '<label class="ax-check"><input type="checkbox" id="ex-cal"%s> Inscrire les échéances à l’agenda</label>'
        '<div class="ax-actions"><button type="button" class="ax-btn" id="ex-save">Enregistrer</button></div></section>'
    ) % (e(prefix + '/echeances?' + ('' if args.get('closed') == '1' else 'closed=1')),
         'Masquer les échéances closes' if args.get('closed') == '1' else 'Afficher aussi les échéances closes',
         e('Dernier contrôle automatique : %s (%s anomalie(s)).' % (str(last.get('at', ''))[:16].replace('T', ' '), last.get('anomalies', 0)) if last else 'Aucun contrôle automatique encore effectué.'),
         items, matter_opts, rule_opts, ' checked' if enabled else '', ' checked' if cal else '')
    head = ('<link rel="stylesheet" href="%s/static/v450.css"><script defer src="%s/static/v450.js"></script>' % (e(prefix), e(prefix)))
    return shell('Échéances', body, prefix, auth['csrf'], '/echeances', head)

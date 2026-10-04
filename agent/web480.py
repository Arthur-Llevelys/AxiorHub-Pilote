"""Interface 4.8.0 : progrès de l'agent (tableau d'apprentissage, tons, règles), autonomie, traçabilité."""
from .common import matter_display
from html import escape as e
import json

from . import autonomy480, learning480, rules480, style480, tone480, trace480
from .common import Stop, load_matters

PAGES = (('/progres', 'Progrès'), ('/autonomie', 'Autonomie'), ('/tracabilite', 'Traçabilité'))
OUTCOME_CLASS = {'tel_quel': 'ok', 'leger': 'warn', 'reecrit': 'bad'}


def _subnav(prefix, active):
    return '<nav class="pg-sub" aria-label="Apprentissage, autonomie, traçabilité">%s</nav>' % ' '.join(
        '<a class="ax-btn%s" href="%s">%s</a>' % ('' if p == active else ' ghost', e(prefix + p, quote=True), e(l)) for p, l in PAGES)


def _bar(bucket):
    if not bucket['total']:
        return ''
    segs = ''.join('<span class="pg-seg %s pg-w%d" title="%s : %s %%"></span>' % (
        OUTCOME_CLASS[k], int(round(bucket['pct'][k] or 0)), e(learning480.OUTCOME_LABELS[k]), bucket['pct'][k] or 0) for k in learning480.OUTCOME_LABELS)
    return '<div class="pg-bar" role="img" aria-label="%s">%s</div>' % (
        e(' · '.join('%s %s %%' % (learning480.OUTCOME_LABELS[k], bucket['pct'][k]) for k in learning480.OUTCOME_LABELS)), segs)


def _row(label, b, extra=''):
    low = ' <span class="vf-badge muted" title="Moins de %d envois : à interpréter avec prudence">échantillon faible</span>' % learning480.MIN_SAMPLE if b['low_sample'] else ''
    return '<tr><th scope="row">%s%s</th><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(label), low, b['total'], _bar(b),
        '%s %%' % b['pct']['tel_quel'] if b['total'] else '—', '%s %%' % b['pct']['leger'] if b['total'] else '—',
        '%s %%' % b['pct']['reecrit'] if b['total'] else '—')


TABLE_HEAD = ('<thead><tr><th>%s</th><th>Envois</th><th>Répartition</th><th>Tels quels</th><th>Légers</th><th>Réécrits</th></tr></thead>')


def dashboard_html(data):
    t = data['total']
    if not t['total']:
        head = ('<div class="vf-head muted" role="status"><strong>Aucun envoi rapproché d’un brouillon pour l’instant.</strong></div>'
                '<p class="vf-note">Le tableau se remplit quand des courriels envoyés depuis votre messagerie peuvent être rapprochés avec certitude d’un brouillon '
                'déposé par l’agent (même fil, mêmes destinataires). %d envoi(s) enregistré(s) sans brouillon rapproché ne sont pas comptés.</p>' % data['unmatched'])
        return head
    trend = data['trend']
    msg = {'hausse': 'La part de brouillons envoyés tels quels progresse.', 'baisse': 'La part de brouillons envoyés tels quels recule.',
           'stable': 'La part de brouillons envoyés tels quels est stable.', 'insuffisant': 'Pas assez d’envois pour dégager une tendance (au moins %d par période).' % data['min_sample']}[trend['direction']]
    out = ['<div class="vf-head %s" role="status"><strong>%s</strong><span>%d envoi(s) analysé(s)%s</span></div>' % (
        'ok' if trend['direction'] in ('hausse', 'stable') else 'bad' if trend['direction'] == 'baisse' else 'muted', e(msg), t['total'],
        (' · 3 derniers mois : %s %% tels quels, 3 mois précédents : %s %%' % (trend['recent'], trend['previous'])) if trend['direction'] != 'insuffisant' and trend['previous'] is not None else '')]
    out.append('<h3>Par mois</h3><table class="vf-table">%s<tbody>%s</tbody></table>' % (TABLE_HEAD % 'Mois', ''.join(_row(m['month'], m) for m in data['months'])))
    if data['by_kind']:
        out.append('<h3>Par type de courriel</h3><table class="vf-table">%s<tbody>%s</tbody></table>' % (TABLE_HEAD % 'Type', ''.join(_row(k['label'], k) for k in data['by_kind'])))
    if data['by_role']:
        out.append('<h3>Par destinataire</h3><table class="vf-table">%s<tbody>%s</tbody></table>' % (TABLE_HEAD % 'Profil', ''.join(_row(k['label'], k) for k in data['by_role'])))
    if data['top_changes']:
        out.append('<h3>Modifications les plus fréquentes</h3><table class="vf-table"><thead><tr><th>Modification</th><th>Avant</th><th>Après</th><th>Fréquence</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%d envoi(s) (%s %% des envois corrigés)</td></tr>' % (
                e(c['label']), e(c['from'] or '—'), e(c['to'] or '—'), c['count'], c['share_pct']) for c in data['top_changes']))
    out.append('<p class="vf-note">%s %d envoi(s) enregistré(s) sans brouillon rapproché ne sont pas comptés (ils ne sont pas lus). '
               'Seules des catégories et des formules de politesse d’une liste fermée sont conservées : aucun texte, nom ou fait.</p>' % (e(data['method']), data['unmatched']))
    return ''.join(out)


def rules_html(desk, prefix):
    rows = rules480.listing(desk)
    if not rows:
        return '<p class="vf-note">Aucune règle. Elles se créent depuis l’éditeur de brouillon (bouton « Toujours faire comme ça ») ou ci-dessous, toujours après votre confirmation.</p>'
    out = ['<ul class="pg-rules">']
    for r in rows:
        flag = ' <span class="vf-badge bad">contradiction : règle(s) %s</span>' % ', '.join(map(str, r['conflicts'])) if r['conflicts'] else ''
        state = '<span class="vf-badge %s">%s</span>' % ('ok' if r['status'] == 'active' else 'muted', 'active' if r['status'] == 'active' else 'suspendue')
        out.append('<li class="pg-rule" data-id="%d"><div><strong>%s</strong> %s%s</div>'
                   '<div class="vf-note">Créée le %s · origine : %s · utilisée %d fois%s</div><div class="vf-buttons">%s'
                   '<button type="button" class="ax-btn ghost r-del">Supprimer</button></div></li>' % (
                       r['id'], e(r['sentence']), state, flag, e(r['created'][:10]), e(r['origin']), r['uses'],
                       (' · dernière utilisation le ' + e(r['last_used'][:10])) if r['last_used'] else '',
                       '<button type="button" class="ax-btn ghost r-susp">Suspendre</button>' if r['status'] == 'active' else '<button type="button" class="ax-btn ghost r-res">Reprendre</button>'))
    out.append('</ul>')
    return ''.join(out)


def tones_html(desk):
    out = ['<div class="pg-tones">']
    for p in tone480.all_profiles(desk):
        out.append(
            '<article class="vf-panel pg-tone" data-role="%s"><h3>%s <span class="vf-badge muted">%s</span></h3>'
            '<div class="vf-form"><label>Registre<input class="t-formality" value="%s" maxlength="120"></label>'
            '<label>Ouverture<input class="t-opening" value="%s" maxlength="120"></label>'
            '<label>Fin<input class="t-closing" value="%s" maxlength="160"></label>'
            '<label>Mots maximum<input class="t-max" type="number" min="30" max="800" value="%d"></label>'
            '<label class="vf-wide">Consignes<textarea class="t-directives" rows="3" maxlength="800">%s</textarea></label>'
            '<label class="vf-check"><input type="checkbox" class="t-vous"%s> Vouvoiement</label>'
            '<div class="vf-buttons"><button type="button" class="ax-btn t-save">Enregistrer</button>'
            '<button type="button" class="ax-btn ghost t-reset">Valeurs d’origine</button></div></div></article>' % (
                e(p['role'], quote=True), e(p['label']), 'modifié' if p['source'] == 'manuel' else 'valeurs d’origine', e(p['formality'], quote=True),
                e(p['opening'], quote=True), e(p['closing'], quote=True), p['max_words'], e(p['directives']), ' checked' if p['vouvoiement'] else ''))
    out.append('</div>')
    sugg = tone480.learned_suggestions(desk)
    if sugg:
        out.append('<h3>Ce que vous envoyez réellement</h3><ul class="pg-sugg">%s</ul>' % ''.join(
            '<li data-role="%s" data-field="%s" data-value="%s">Profil « %s » : vous écrivez le plus souvent « %s » (%d envois sur %d) au lieu de « %s ». '
            '<button type="button" class="ax-btn ghost t-adopt">Adopter</button></li>' % (
                e(s['role'], quote=True), s['field'], e(s['value'], quote=True), e(tone480.DEFAULTS[s['role']]['label']), e(s['value']),
                s['count'], s['of'], e(s['current'])) for s in sugg))
    ov = tone480.overrides(desk)
    out.append('<h3>Corrections de détection</h3><p class="vf-note">La détection part du rôle enregistré dans le dossier, puis de l’adresse. '
               'Une correction ci-dessous prime toujours.</p>')
    if ov:
        out.append('<ul>%s</ul>' % ''.join('<li data-kind="%s" data-value="%s">%s <strong>%s</strong> → %s <button type="button" class="ax-btn ghost o-del">Retirer</button></li>' % (
            e(o['kind'], quote=True), e(o['value'], quote=True), e(o['kind']), e(o['value']), e(tone480.DEFAULTS[o['role']]['label'])) for o in ov))
    options = ''.join('<option value="%s">%s</option>' % (r, e(tone480.DEFAULTS[r]['label'])) for r in tone480.PROFILES)
    out.append('<div class="vf-form"><label>Type<select id="o-kind"><option value="adresse">Adresse</option><option value="domaine">Domaine</option></select></label>'
               '<label>Valeur<input id="o-value" placeholder="nom@exemple.fr ou exemple.fr"></label>'
               '<label>Profil<select id="o-role">%s</select></label>'
               '<div class="vf-buttons"><button type="button" class="ax-btn" id="o-save">Corriger la détection</button></div></div>'
               '<div class="vf-form"><label class="vf-wide">Tester la détection (adresses séparées par des virgules)<input id="d-addr" placeholder="greffe@tribunal.example, client@exemple.fr"></label>'
               '<div class="vf-buttons"><button type="button" class="ax-btn ghost" id="d-test">Tester</button></div></div><div id="d-out" aria-live="polite"></div>' % options)
    return ''.join(out)


def progres_page(desk, auth, prefix, args, shell):
    kind, role = str(args.get('kind', '')), str(args.get('role', ''))
    try:
        data = learning480.dashboard(desk, int(args.get('months', 12) or 12), kind, role)
    except (Stop, ValueError):
        data = learning480.dashboard(desk, 12)
        kind = role = ''
    kinds = '<option value="">Tous les types</option>' + ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == kind else '', e(v)) for k, v in learning480.KINDS.items())
    roles = '<option value="">Tous les destinataires</option>' + ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == role else '', e(v)) for k, v in learning480.ROLE_LABELS.items())
    scope_opts = ''.join('<option value="%s">%s</option>' % (k, v) for k, v in (('dossier', 'Un dossier'), ('destinataire', 'Un destinataire'),
                                                                                ('profil', 'Un profil de ton'), ('type', 'Un type de courriel')))
    rule_types = ''.join('<option value="%s">%s</option>' % (k, v) for k, v in (
        ('formule_fermeture', 'Formule de fin'), ('formule_ouverture', 'Formule d’ouverture'), ('longueur_max', 'Longueur maximale (mots)'),
        ('registre', 'Registre (vous / tu)'), ('consigne', 'Consigne libre')))
    matters = ''.join('<option value="%s">%s</option>' % (e(m['id'], quote=True), e(matter_display(m))) for m in load_matters(desk.c))
    body = (
        '<h1>Progrès de l’agent</h1>%s'
        '<p class="ax-muted">Ce que l’agent a appris de vos corrections, ce que vous lui avez demandé de toujours faire, et le style qu’il adopte selon le destinataire. '
        'Rien ne devient une règle sans votre confirmation.</p>'
        '<section class="vf-panel"><h2>Tableau d’apprentissage</h2>'
        '<form class="vf-form" method="get" action=""><label>Type<select name="kind">%s</select></label><label>Destinataire<select name="role">%s</select></label>'
        '<div class="vf-buttons"><button class="ax-btn ghost" type="submit">Filtrer</button><button type="button" class="ax-btn ghost" id="l-refresh">Rattraper les envois récents</button></div></form>'
        '<div id="l-out">%s</div></section>'
        '<section class="vf-panel"><h2>Profils de ton</h2>%s</section>'
        '<section class="vf-panel"><h2>Règles « toujours faire comme ça »</h2><div id="r-list">%s</div>'
        '<h3>Nouvelle règle</h3><div class="vf-form" id="r-new"><label>Portée<select id="r-scope">%s</select></label>'
        '<label>Dossier<select id="r-matter"><option value="">—</option>%s</select></label>'
        '<label>Destinataire, profil ou type<input id="r-value" placeholder="adresse, client/confrere/…, appointment/status/…"></label>'
        '<label>Règle<select id="r-type">%s</select></label><label>Valeur<input id="r-val" placeholder="ex. Confraternellement, 150, vous"></label>'
        '<label class="vf-check"><input type="checkbox" id="r-confirm"> Je confirme cette règle</label>'
        '<div class="vf-buttons"><button type="button" class="ax-btn" id="r-create">Créer la règle</button></div></div></section>'
    ) % (_subnav(prefix, '/progres'), kinds, roles, dashboard_html(data), tones_html(desk), rules_html(desk, prefix), scope_opts, matters, rule_types)
    return shell('Progrès de l’agent', body, prefix, auth['csrf'], '/progres')


def autonomie_page(desk, auth, prefix, args, shell):
    snap = autonomy480.snapshot(desk)
    cards = []
    for t in snap['tasks']:
        radios = ''.join(
            '<label class="pg-level"><input type="radio" name="lv-%s" value="%s"%s> <strong>%s</strong><span>%s</span></label>' % (
                e(t['task'], quote=True), l['level'], ' checked' if l['level'] == t['level'] else '', e(l['label']), e(l['help'])) for l in t['levels'])
        cards.append('<article class="vf-panel pg-task" data-task="%s"><h3>%s</h3>%s'
                     '<label class="vf-check pg-confirm"><input type="checkbox" class="a-confirm"> Je confirme que l’agent agisse seul pour cette tâche (réversible à tout moment)</label>'
                     '<div class="vf-buttons"><button type="button" class="ax-btn a-save">Appliquer</button> <span class="vf-note">Niveau actuel : <strong>%s</strong></span></div></article>' % (
                         e(t['task'], quote=True), e(t['label']), radios, e(t['level_label'])))
    locked = ''.join('<li>%s</li>' % e(x['label']) for x in snap['locked'])
    pending = ''.join(
        '<li data-id="%d"><strong>%s</strong> <span class="vf-note">%s · %s</span> <button type="button" class="ax-btn a-exec">Inscrire</button> '
        '<button type="button" class="ax-btn ghost a-dismiss">Écarter</button></li>' % (p['id'], e(p['title']), e(p['matter'] or 'sans dossier'), e(p['created'][:10])) for p in snap['pending'])
    history = ''.join('<tr><td>%s</td><td>%s</td><td>%s → %s</td><td>%s</td></tr>' % (e(h['at'][:19]), e(h['task']), e(h['old']), e(h['new']), e(h['reason'])) for h in snap['history'])
    body = (
        '<h1>Autonomie de l’agent</h1>%s'
        '<p class="ax-muted">Réglez, tâche par tâche, jusqu’où l’agent agit seul. Ces niveaux gouvernent ce que l’agent déclenche de lui-même ; '
        'une demande que vous formulez est toujours exécutée.</p>'
        '<div class="vf-head %s" role="status"><strong>%s</strong><button type="button" class="ax-btn %s" id="a-prudent" data-on="%s">%s</button></div>'
        '%s'
        '<section class="vf-panel"><h2>Toujours soumis à votre validation</h2><p class="vf-note">Ces actions sortent du cabinet et ne sont pas configurables.</p><ul>%s</ul></section>'
        '<section class="vf-panel"><h2>À valider (niveau « proposer seulement »)</h2><ul id="a-pending">%s</ul></section>'
        '<section class="vf-panel"><h2>Historique des réglages</h2><table class="vf-table"><thead><tr><th>Date (UTC)</th><th>Tâche</th><th>Changement</th><th>Origine</th></tr></thead><tbody>%s</tbody></table></section>'
    ) % (_subnav(prefix, '/autonomie'), 'bad' if snap['prudent'] else 'ok',
         'Mode prudent actif : toutes les tâches sont au niveau le plus bas.' if snap['prudent'] else 'Réglages normaux.',
         '' if snap['prudent'] else 'ghost', '0' if snap['prudent'] else '1',
         'Rétablir mes réglages' if snap['prudent'] else 'Tout passer en mode prudent',
         ''.join(cards), locked, pending or '<li class="vf-note">Rien en attente.</li>',
         history or '<tr><td colspan="4">Aucun changement.</td></tr>')
    return shell('Autonomie', body, prefix, auth['csrf'], '/autonomie')


def tracabilite_page(desk, auth, prefix, args, shell):
    matter = str(args.get('matter', ''))
    options = ''.join('<option value="%s"%s>%s</option>' % (e(m['id'], quote=True), ' selected' if m['id'] == matter else '', e(matter_display(m)))
                      for m in load_matters(desk.c))
    over = trace480.overview(desk, 30)
    models = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>' % (
        e(r['model'] or '—'), e(r['provider']), 'externe : ' + e(r['destination']) if r['external'] else 'local (reste au cabinet)', r['calls']) for r in over['by_model'])
    body = (
        '<h1>Traçabilité</h1>%s'
        '<p class="ax-muted">Ce que l’agent a lu, produit et pourquoi, dossier par dossier. Le journal conserve les <em>références</em> (identifiants, chemins, empreintes), '
        'jamais le texte des courriels, des pièces ou des requêtes.</p>'
        '<section class="vf-panel"><h2>Qu’a vu le modèle sur ce dossier ?</h2><div class="vf-form">'
        '<label>Dossier<select id="t-matter"><option value="">—</option>%s</select></label>'
        '<label>Depuis le<input id="t-since" type="date"></label><label>Jusqu’au<input id="t-until" type="date"></label>'
        '<div class="vf-buttons"><button type="button" class="ax-btn" id="t-view">Consulter</button>'
        '<button type="button" class="ax-btn ghost" id="t-json">Exporter (JSON)</button><button type="button" class="ax-btn ghost" id="t-csv">Exporter (CSV)</button></div></div>'
        '<div id="t-out" aria-live="polite"></div></section>'
        '<section class="vf-panel"><h2>Modèles utilisés ces 30 derniers jours</h2><table class="vf-table"><thead><tr><th>Modèle</th><th>Fournisseur</th><th>Destination</th><th>Appels</th></tr></thead><tbody>%s</tbody></table>'
        '<p class="vf-note">Un appel « local » n’a jamais quitté le cabinet. Un service externe est facultatif, anonymisé et désactivé par défaut (réglage Routage hybride).</p></section>'
    ) % (_subnav(prefix, '/tracabilite'), options, models or '<tr><td colspan="4">Aucun appel enregistré.</td></tr>')
    return shell('Traçabilité', body, prefix, auth['csrf'], '/tracabilite')


def trace_html(view):
    s = view['summary']
    out = ['<div class="vf-head %s" role="status"><strong>%d appel(s) de modèle, %d source(s) distincte(s) vue(s)</strong><span>%s</span></div>' % (
        'bad' if s['external_calls'] else 'ok', s['calls'], s['distinct_sources'],
        ('dont %d vers un service externe' % s['external_calls']) if s['external_calls'] else 'tout est resté au cabinet')]
    if s['calls']:
        out.append('<p class="vf-note">Premier appel : %s · dernier appel : %s · destinations : %s.%s</p>' % (
            e(s['first_call'][:19]), e(s['last_call'][:19]), e(', '.join(s['destinations'])), ' Liste tronquée.' if s['truncated'] else ''))
    if view['model_saw']:
        out.append('<h3>Ce que le modèle a vu</h3><table class="vf-table"><thead><tr><th>Source</th><th>Nature</th><th>Première vue</th><th>Dernière vue</th><th>Appels</th><th>Destination</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>' % (
                e(x['label'] or x['id']), e(x['kind']), e(x['first_seen'][:19]), e(x['last_seen'][:19]), x['calls'],
                e(', '.join(x['destinations']))) for x in view['model_saw']))
    if view['events']:
        out.append('<h3>Ce que l’agent a produit ou décidé, et pourquoi</h3><ul class="pg-events">%s</ul>' % ''.join(
            '<li><span class="vf-note">%s</span> <span class="vf-badge muted">%s</span> <strong>%s</strong><br><span class="vf-note">%s</span></li>' % (
                e(x['at'][:19]), e(x['kind']), e(x['what']), e(x['why'])) for x in view['events'][:80]))
    if view['calls']:
        out.append('<details><summary>Détail des %d appel(s)</summary><table class="vf-table"><thead><tr><th>Date (UTC)</th><th>Étape</th><th>Modèle</th><th>Destination</th><th>Caractères</th><th>Empreinte</th><th>État</th></tr></thead><tbody>%s</tbody></table></details>' % (
            len(view['calls']), ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td><code>%s</code></td><td>%s</td></tr>' % (
                e(c['at'][:19]), e(c['stage']), e(c['model']), e('externe : ' + c['destination'] if c['external'] else 'local'), c['chars_sent'], e(c['sha256'][:12]),
                e(c['status'] + (' (' + c['error'] + ')' if c['error'] else ''))) for c in view['calls'][:200])))
    out.append('<p class="vf-note">%s</p>' % e(view['note']))
    return ''.join(out)


def _int(value):
    try:
        return max(0, min(int(value or 0), 10_000))
    except (TypeError, ValueError):
        return 0


def _list(value, limit=8):
    if isinstance(value, str):
        value = [x for x in value.replace(';', ',').split(',')]
    return [str(x).strip() for x in (value or []) if str(x).strip()][:limit]


def handle(desk, name, data, method='POST', args=None):
    args = args or {}
    if method == 'GET':
        if name == 'learning/dashboard':
            result = learning480.dashboard(desk, int(args.get('months', 12) or 12), str(args.get('kind', '')), str(args.get('role', '')))
            return {'dashboard': result, 'html': dashboard_html(result)}
        if name == 'trace/view':
            view = trace480.matter_view(desk, args.get('matter', ''), args.get('since', ''), args.get('until', ''))
            return {'view': view, 'html': trace_html(view)}
        if name == 'trace/export':
            return trace480.export(desk, args.get('matter', ''), str(args.get('format', 'json')), args.get('since', ''), args.get('until', ''))
        if name == 'autonomy/status':
            return autonomy480.snapshot(desk)
        raise Stop('route_inconnue')
    if name == 'learning/refresh':
        result = learning480.backfill(desk)
        return {**result, 'html': dashboard_html(learning480.dashboard(desk, 12))}
    if name == 'tone/save':
        return {'profile': tone480.save_profile(desk, str(data.get('role', '')), data)}
    if name == 'tone/reset':
        return {'profile': tone480.reset_profile(desk, str(data.get('role', '')))}
    if name == 'tone/override':
        return tone480.set_override(desk, str(data.get('kind', '')), data.get('value', ''), str(data.get('role', '')))
    if name == 'tone/adopt':
        return {'profile': tone480.adopt(desk, str(data.get('role', '')), str(data.get('field', '')), str(data.get('value', '')))}
    if name == 'tone/detect':
        recipients = _list(data.get('recipients'))
        matter = str(data.get('matter') or '') or rules480.matter_for(desk, recipients)
        result = tone480.detect(desk, matter, recipients)
        label = tone480.DEFAULTS[result['profile']]['label'] if result['profile'] else None
        return {**result, 'label': label, 'matter': matter}
    if name == 'rules/propose':
        return rules480.candidates_from_correction(desk, data.get('original', ''), data.get('corrected', ''),
                                                   _list(data.get('recipients')), str(data.get('matter') or ''), str(data.get('intent') or ''))
    if name == 'rules/create':
        return rules480.create(desk, str(data.get('rule_type', '')), data.get('value', ''), str(data.get('scope', '')), data.get('scope_value', ''),
                               str(data.get('confirm', '')), str(data.get('origin', 'manuelle')), str(data.get('origin_ref', ''))[:120], _int(data.get('evidence')))
    if name == 'rules/status':
        return rules480.set_status(desk, data.get('id'), str(data.get('status', '')))
    if name == 'rules/delete':
        return rules480.delete(desk, data.get('id'))
    if name == 'autonomy/set':
        return autonomy480.set_level(desk, str(data.get('task', '')), str(data.get('level', '')), str(data.get('confirm', '')))
    if name == 'autonomy/prudent':
        return autonomy480.set_prudent(desk, bool(data.get('on')))
    if name == 'autonomy/pending':
        return autonomy480.pending_decide(desk, data.get('id'), str(data.get('action', '')))
    raise Stop('route_inconnue')

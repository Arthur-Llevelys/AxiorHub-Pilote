"""Interface 5.0.0 : fonctions métier — temps et honoraires, rendez-vous, conflits, prescription, pilotage mensuel.

Les pages sont rendues côté serveur (aucun HTML venant d'un tiers n'est injecté dans le navigateur) ; les actions passent
par l'API JSON protégée (authentification, Origin, jeton CSRF). Toutes les valeurs affichées sont échappées.
"""
from .common import matter_display
from datetime import date, datetime, timedelta
from html import escape as e
import json

from .common import Stop
from . import metier500 as m5, time500, meeting500, conflicts500, limitation500, report500

SECTIONS = (('/honoraires', 'Temps et honoraires', 'Temps estimés à valider, budgets, rapprochement avec la facturation'),
            ('/rendez-vous', 'Rendez-vous', 'Fiche préparée avant, projet de compte rendu après'),
            ('/conflits', 'Conflits d’intérêts', 'Recherche à l’ouverture d’un dossier, rapport sourcé'),
            ('/prescriptions', 'Prescription et forclusion', 'Alertes calculées par règle, avec la règle, le départ et les points à confirmer'),
            ('/pilotage-mensuel', 'Pilotage mensuel', 'Dossiers, délais, temps, brouillons et qualité du mois'))
LOCAL_NOTE = ('Traitement 100 % local : ces fonctions n’appellent aucun modèle ni service externe, et chaque consultation est inscrite dans le journal d’accès.')


def _opts(desk, selected='', blank='Choisir un dossier', allow_blank=True):
    out = ['<option value="">%s</option>' % e(blank)] if allow_blank else []
    for mid, m in sorted(m5.matter_index(desk).items(), key=lambda kv: matter_display(kv[1]).casefold()):
        out.append('<option value="%s"%s>%s</option>' % (e(mid, quote=True), ' selected' if mid == selected else '',
                                                           e(matter_display(m))))
    return ''.join(out)


def _tabs(active):
    links = ['<a href="%s"%s>%s</a>' % ('{prefix}' + p, ' aria-current="page"' if p == active else '', e(t)) for p, t, _ in SECTIONS]
    return '<nav class="m5-tabs" aria-label="Fonctions du cabinet"><a href="{prefix}/cabinet"%s>Vue d’ensemble</a>%s</nav>' % (
        ' aria-current="page"' if active == '/cabinet' else '', ''.join(links))


def _page(shell, prefix, auth, title, active, body):
    html = _tabs(active).replace('{prefix}', e(prefix)) + body
    return shell(title, html, prefix, auth['csrf'], '/cabinet')


def _badge(kind, text):
    return '<span class="vf-badge %s">%s</span>' % (e(kind), e(text))


def _form(api, inner, button, reload=True, cls='', confirm=''):
    return ('<form class="m5-form %s" data-api="%s"%s%s>%s<button class="ax-btn" type="submit">%s</button></form>' % (
        e(cls), e(api, quote=True), ' data-reload="1"' if reload else '', (' data-confirm="%s"' % e(confirm, quote=True)) if confirm else '', inner, e(button)))


def _field(label, name, value='', kind='text', extra=''):
    return '<label class="m5-field">%s<input type="%s" name="%s" value="%s" %s></label>' % (e(label), kind, e(name, quote=True), e(value, quote=True), extra)


def _hidden(**kw):
    return ''.join('<input type="hidden" name="%s" value="%s">' % (e(k, quote=True), e(str(v), quote=True)) for k, v in kw.items())


def _fr(day):
    try:
        return meeting500.fr_long(date.fromisoformat(str(day)[:10]))
    except ValueError:
        return str(day)


# --------------------------------------------------------------------------------------------- vue d'ensemble
def cabinet_page(desk, auth, prefix, args, shell):
    m5.ensure_schema(desk)
    try:
        conflicts500.scan_background(desk)
    except Exception:
        pass
    pend_time = len(time500.pending(desk))
    alerts = time500.alerts_now(desk)
    meets = [m for m in meeting500.upcoming(desk, days=2, past_days=0) if m['matter']]
    pending_conf = conflicts500.pending(desk)
    cnt = limitation500.counts(desk)
    cards = [
        ('/honoraires', 'Temps et honoraires', ['%d proposition(s) de temps à valider' % pend_time, '%d budget(s) à surveiller' % len(alerts)], pend_time or alerts),
        ('/rendez-vous', 'Rendez-vous', ['%d rendez-vous dans les 2 jours' % len(meets)], False),
        ('/conflits', 'Conflits d’intérêts', ['%d dossier(s) à examiner avant tout acte' % len(pending_conf)], pending_conf),
        ('/prescriptions', 'Prescription et forclusion', ['%d proche(s) (60 jours)' % cnt['near'], '%d dépassée(s) non close(s)' % cnt['overdue'], '%d à confirmer' % cnt['to_confirm']], cnt['overdue'] or cnt['to_confirm']),
        ('/pilotage-mensuel', 'Pilotage mensuel', ['Rapport du mois écoulé disponible'], False)]
    html = '<h1>Gestion du cabinet</h1><p class="ax-muted">%s</p><div class="m5-cards">' % e(LOCAL_NOTE)
    for path, title, lines, flag in cards:
        html += '<article class="m5-card%s"><h2><a href="%s">%s</a></h2><ul>%s</ul></article>' % (
            ' m5-flag' if flag else '', e(prefix + path), e(title), ''.join('<li>%s</li>' % e(x) for x in lines))
    html += '</div>'
    if pending_conf:
        html += '<section class="m5-alert" role="alert"><h2>Conflits d’intérêts à examiner avant tout acte</h2><ul>%s</ul></section>' % ''.join(
            '<li><a href="%s">%s</a></li>' % (e('%s/conflits?matter=%s' % (prefix, p['matter']), quote=True), e(p['label'])) for p in pending_conf)
    log = m5.access_log(desk, 30)
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['at'][:19].replace('T', ' ')), e(r['area']), e(r['action']), e(r['matter'])) for r in log)
    html += ('<details class="m5-log"><summary>Journal d’accès (30 dernières consultations de données sensibles)</summary>'
             '<table class="vf-table"><thead><tr><th>Date (UTC)</th><th>Zone</th><th>Action</th><th>Dossier</th></tr></thead><tbody>%s</tbody></table>'
             '<p class="vf-note">Le journal ne contient ni montant, ni nom, ni texte : seulement la zone consultée, l’action et l’identifiant du dossier.</p></details>') % (
        rows or '<tr><td colspan="4">Aucune consultation enregistrée.</td></tr>')
    return _page(shell, prefix, auth, 'Gestion du cabinet', '/cabinet', html)


# ------------------------------------------------------------------------------------ temps et honoraires
def honoraires_page(desk, auth, prefix, args, shell):
    matter = args.get('matter', '')
    m5.log_access(desk, 'honoraires', 'page', matter)
    html = ('<h1>Temps et honoraires</h1><p class="ax-muted">Les temps estimés par l’agent ne sont que des <strong>propositions</strong> : rien n’est enregistré, compté dans un budget ou rapproché '
            'de la facturation sans votre validation. %s</p>' % e(LOCAL_NOTE))
    # propositions
    props = time500.pending(desk, matter)
    html += '<section id="a-valider"><h2>Temps à valider (%d)</h2>' % len(props)
    html += _form('m500/time/estimate', '<label class="m5-field">Dossier<select name="matter">%s</select></label>%s%s' % (
        _opts(desk, matter, 'Tous les dossiers'), _field('Depuis le', 'since', '', 'date'), _field('Jusqu’au', 'until', '', 'date')),
        'Estimer à partir des courriels, pièces et rendez-vous', cls='m5-inline')
    if not props:
        html += '<p class="vf-note">Aucune proposition en attente. Lancez une estimation (par défaut, les %d derniers jours).</p>' % time500.bareme(desk)['fenetre_jours']
    for p in props:
        items = ''.join('<li>%s <span class="vf-note">(%d min)</span></li>' % (e(i['label']), i['minutes']) for i in p['items'])
        inner = (_hidden(id=p['id']) + _field('Durée validée (minutes)', 'minutes', str(p['minutes_est']), 'number', 'min="1" max="1440" required')
                 + _field('Libellé', 'label', 'Temps validé', 'text', 'maxlength="200"'))
        html += ('<article class="m5-proposal"><h3>%s — %s <span class="vf-badge muted">estimation : %s</span></h3><p class="vf-note">%s</p>'
                 '<details><summary>Détail des éléments pris en compte</summary><ul>%s</ul></details>'
                 '<form class="m5-form m5-inline" data-api="m500/time/validate" data-reload="1">%s<button class="ax-btn" type="submit">Valider ce temps</button>'
                 '<button class="ax-btn ghost" type="submit" data-api="m500/time/reject">Écarter</button></form></article>') % (
            e(m5.matter_label(desk, p['matter'])), e(_fr(p['day'])), e(m5.hours(p['minutes_est'])), e(p['explanation']), items, inner)
    html += '</section>'
    # vue d'ensemble
    rows = time500.overview(desk)
    html += '<section id="dossiers"><h2>Dossiers</h2>'
    if rows:
        html += '<table class="vf-table"><thead><tr><th>Dossier</th><th>Mode</th><th>Temps validé</th><th>Valeur HT</th><th>Facturé HT</th><th>Reste à facturer</th><th>Budget</th><th></th></tr></thead><tbody>'
        for r in rows:
            lvl = {'depasse': ('bad', 'Dépassé'), 'proche': ('warn', 'Bientôt atteint'), 'ok': ('ok', 'Dans le budget'), 'sans_budget': ('muted', 'Sans budget')}[r['level']]
            ratio = (' · %s %%' % str(r['ratio_pct']).replace('.', ',')) if r['ratio_pct'] is not None else ''
            html += '<tr><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><a href="%s">Détail</a></td></tr>' % (
                e(r['label']), e(time500.MODES.get(r['mode'], 'à définir')), e(m5.hours(r['minutes'])), e(m5.euros(r['amount_cents'])), e(m5.euros(r['invoiced_cents'])),
                e(m5.euros(r['to_bill_cents'])), _badge(lvl[0], lvl[1] + ratio), e('%s/honoraires?matter=%s' % (prefix, r['matter']), quote=True))
        html += '</tbody></table>'
    else:
        html += '<p class="vf-note">Aucun dossier n’a encore de conditions, de temps validé ou de facture. Choisissez un dossier pour définir ses conditions.</p>'
    html += ('<form class="m5-inline m5-goto" method="get" action="%s/honoraires"><label class="m5-field">Ouvrir un dossier<select name="matter">%s</select></label>'
             '<button class="ax-btn ghost" type="submit">Afficher</button></form></section>') % (e(prefix), _opts(desk, matter))
    if matter:
        html += _matter_detail(desk, matter, prefix)
    bar = time500.bareme(desk)
    fields = ''.join(_field(time500.BAREME_LABELS[k], k, str(bar[k]), 'number', 'min="1" max="2000"') for k in time500.BAREME_DEFAUT)
    html += ('<details class="m5-bareme"><summary>Barème d’estimation (modifiable)</summary><p class="vf-note">L’agent estime : chaque courriel reçu ou envoyé (les suivants du même fil le même jour comptent moitié, au plus trois), chaque pièce créée ou '
             'modifiée, chaque livrable préparé, et la durée réelle des rendez-vous passés de l’agenda (les événements « journée entière » ne sont pas estimés). Ce barème est une convention : ajustez-le à votre pratique.</p>%s</details>') % (
        _form('m500/time/bareme', fields, 'Enregistrer le barème', cls='m5-grid'))
    return _page(shell, prefix, auth, 'Temps et honoraires', '/honoraires', html)


def _matter_detail(desk, matter, prefix):
    s = time500.summary(desk, matter)
    t = s['terms']
    label = m5.matter_label(desk, matter)
    html = '<section id="detail"><h2>%s</h2>' % e(label)
    lvl = {'depasse': ('bad', 'Budget dépassé'), 'proche': ('warn', 'Budget bientôt atteint'), 'ok': ('ok', 'Dans le budget'), 'sans_budget': ('muted', 'Sans budget défini')}[s['level']]
    html += '<p>%s <strong>%s</strong> validé (%s HT) · facturé %s HT · reste à facturer %s HT%s</p>' % (
        _badge(*lvl), e(m5.hours(s['minutes'])), e(m5.euros(s['amount_cents'])), e(m5.euros(s['invoiced_cents'])), e(m5.euros(s['to_bill_cents'])),
        (' · budget %s (%s %%)' % (e(m5.euros(s['budget_cents'])), e(str(s['ratio_pct']).replace('.', ',')))) if s['budget_cents'] else '')
    if s['pending_count']:
        html += '<p class="vf-note">En attente de validation, non comptés : %s (≈ %s HT).</p>' % (e(m5.hours(s['pending_minutes'])), e(m5.euros(s['pending_amount_cents'])))
    # conditions
    modes = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if t['mode'] == k else '', e(v)) for k, v in time500.MODES.items())
    html += '<h3>Conditions convenues</h3>' + _form('m500/time/terms', _hidden(matter=matter) +
        '<label class="m5-field">Mode<select name="mode">%s</select></label>%s%s%s%s' % (
            modes, _field('Taux horaire HT (€)', 'rate', '%.2f' % (t['rate_cents'] / 100) if t['rate_cents'] else '', 'text', 'inputmode="decimal"'),
            _field('Budget ou forfait HT (€)', 'budget', '%.2f' % (t['budget_cents'] / 100) if t['budget_cents'] else '', 'text', 'inputmode="decimal"'),
            _field('Alerte à (%)', 'alert_pct', str(t['alert_pct']), 'number', 'min="10" max="99"'), _field('Note', 'note', t['note'], 'text', 'maxlength="300"')),
        'Enregistrer les conditions', cls='m5-grid')
    # temps validés
    html += '<h3>Temps validés</h3>'
    if s['entries']:
        html += '<table class="vf-table"><thead><tr><th>Jour</th><th>Durée</th><th>Taux</th><th>Valeur HT</th><th>Libellé</th><th>Origine</th><th>Annuler</th></tr></thead><tbody>'
        for en in s['entries']:
            html += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                e(en['day']), e(m5.hours(en['minutes'])), e(m5.euros(en['rate_cents']) if en['rate_cents'] else 'non défini'), e(m5.euros(en['amount_cents'])), e(en['label']),
                e('Proposition validée' if en['origin'] == 'proposition_validee' else 'Saisie'),
                _form('m500/time/cancel', _hidden(id=en['id']) + _field('Motif', 'reason', '', 'text', 'required minlength="5" maxlength="300"'), 'Annuler', cls='m5-inline m5-mini'))
        html += '</tbody></table>'
    else:
        html += '<p class="vf-note">Aucun temps validé pour ce dossier.</p>'
    html += _form('m500/time/add', _hidden(matter=matter) + _field('Jour', 'day', date.today().isoformat(), 'date', 'required') + _field('Minutes', 'minutes', '', 'number', 'min="1" max="1440" required') +
                  _field('Libellé', 'label', '', 'text', 'maxlength="200"'), 'Saisir un temps (validé d’emblée)', cls='m5-inline')
    # rapprochement
    rec = time500.reconcile(desk, matter)
    html += '<h3>Rapprochement avec la facturation</h3><p>%s</p>' % e(rec['text'])
    if s['invoices']:
        html += '<table class="vf-table"><thead><tr><th>Facture</th><th>Date</th><th>Montant HT</th><th>Origine</th><th>Supprimer</th></tr></thead><tbody>'
        for i in s['invoices']:
            html += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                e(i['number']), e(i['day']), e(m5.euros(i['amount_cents'])), e('Import CSV' if i['origin'] == 'import_csv' else 'Saisie'),
                _form('m500/invoice/delete', _hidden(id=i['id']) + _field('Motif', 'reason', '', 'text', 'required minlength="5"'), 'Supprimer', cls='m5-inline m5-mini'))
        html += '</tbody></table>'
    html += _form('m500/invoice/add', _hidden(matter=matter) + _field('N° de facture', 'number', '', 'text', 'required maxlength="60"') + _field('Date', 'day', date.today().isoformat(), 'date', 'required') +
                  _field('Montant HT (€)', 'amount', '', 'text', 'required inputmode="decimal"'), 'Enregistrer une facture émise', cls='m5-inline')
    html += ('<details><summary>Importer un export CSV de factures</summary><p class="vf-note">Colonnes reconnues : numéro, date, montant HT (et dossier, si le fichier mélange plusieurs dossiers). Séparateur « ; » ou « , ».</p>%s</details>') % _form(
        'm500/invoice/import', _hidden(matter=matter) + '<label class="m5-field">Contenu du fichier<textarea name="text" rows="6" required></textarea></label><input type="file" class="m5-file" data-target="text" accept=".csv,text/csv" aria-label="Choisir un fichier CSV">', 'Importer')
    unpaid = rec['unpaid']
    if unpaid['linked']:
        rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(i['number']), e(i['due_date']), e('%.2f' % i['amount']), e('%.2f' % i['balance'])) for i in unpaid['invoices'])
        html += ('<h3>Impayés connus de la facturation (lecture seule)</h3><table class="vf-table"><thead><tr><th>Facture</th><th>Échéance</th><th>Montant (TTC)</th><th>Solde</th></tr></thead><tbody>%s</tbody></table>' %
                 (rows or '<tr><td colspan="4">Aucun impayé connu.</td></tr>'))
    html += '<ul class="vf-note">%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in rec['limits'])
    html += '<div class="ax-actions"><button type="button" class="ax-btn ghost m5-export" data-api="m500/time/export?matter=%s">Exporter les temps validés (CSV)</button></div></section>' % e(matter, quote=True)
    return html


# ----------------------------------------------------------------------------------------------- rendez-vous
def _fiche_html(content):
    html = ''
    for w in content.get('warnings', []):
        html += '<p class="m5-warn">%s</p>' % e(w)
    s = content.get('sections', {})
    if not content.get('matter'):
        return html
    def block(title, rows):
        return ('<section class="m5-block"><h3>%s</h3><ul>%s</ul></section>' % (e(title), ''.join(rows))) if rows else ''
    html += block('Parties', ['<li>%s <span class="vf-note">(%s — %s)</span></li>' % (e(p['name']), e(p['status']), e(p['source'])) for p in s.get('parties', [])])
    html += block('Points ouverts', ['<li>%s</li>' % e(p['label']) for p in s.get('points_ouverts', [])])
    html += block('Échéances de procédure', ['<li><strong>%s</strong> : %s (%s, dans %d j)</li>' % (e(x['label']), e(_fr(x['due'])), e(x['status']), x['days']) for x in s.get('echeances', [])])
    html += block('Prescriptions', ['<li><strong>%s</strong> : dernier jour utile %s (%s) — %s</li>' % (e(x['label']), e(_fr(x['due'])), e(x['status']), e(', '.join(x['articles']))) for x in s.get('prescriptions', [])])
    html += block('Historique récent', ['<li>%s — %s : %s <span class="vf-note">(source : %s%s)</span></li>' % (e(x['at']), e(x['kind']), e(x['title']), e(x['source']), '' if x['validated'] else ' — non validé') for x in s.get('historique', [])])
    html += block('Pièces récentes', ['<li>%s <span class="vf-note">(modifiée le %s)</span></li>' % (e(x['name']), e(x['modified'])) for x in s.get('pieces', [])])
    html += block('Faits retenus (validés)', ['<li>%s <span class="vf-note">(source : %s)</span></li>' % (e(x['text']), e(x['source'])) for x in s.get('faits', [])])
    if s.get('faits_a_valider'):
        html += '<p class="vf-note">%d fait(s) proposé(s) restent à valider dans la fiche du dossier.</p>' % s['faits_a_valider']
    return html


def rendez_vous_page(desk, auth, prefix, args, shell):
    m5.log_access(desk, 'rendez-vous', 'page', '')
    events = meeting500.upcoming(desk, days=14, past_days=7)
    html = '<h1>Rendez-vous</h1><p class="ax-muted">Avant : une fiche préparée à partir du dossier (historique, points ouverts, pièces). Après : un projet de compte rendu à partir de vos notes ou de la dictée. %s</p>' % e(LOCAL_NOTE)
    state = {'prete': ('ok', 'Fiche prête'), 'a_preparer': ('warn', 'À préparer'), 'sans_dossier': ('muted', 'Sans dossier')}
    if events:
        html += '<table class="vf-table"><thead><tr><th>Date</th><th>Rendez-vous</th><th>Dossier</th><th>Fiche</th><th>Compte rendu</th><th></th></tr></thead><tbody>'
        for ev in events:
            html += '<tr><td>%s</td><th scope="row">%s</th><td>%s</td><td>%s</td><td>%s</td><td><a href="%s">Ouvrir</a></td></tr>' % (
                e(str(ev['starts'])[:16].replace('T', ' ')), e(ev['title']), e(ev['matter_label'] or '—'), _badge(*state[ev['fiche']]),
                e({'': '—', 'brouillon': 'Brouillon', 'valide': 'Validé'}.get(ev['report'], ev['report'])), e('%s/rendez-vous?event=%s' % (prefix, ev['id']), quote=True))
        html += '</tbody></table>'
    else:
        html += '<p class="vf-note">Aucun rendez-vous dans l’agenda synchronisé sur cette période (7 jours passés, 14 à venir).</p>'
    eid = args.get('event', '')
    if eid:
        try:
            ev = meeting500._event(desk, eid)
        except Stop:
            ev = None
        if ev:
            content = meeting500.fiche(desk, eid, refresh=args.get('refresh') == '1')
            html += '<section id="fiche"><h2>Fiche — %s</h2><p class="vf-note">%s%s</p>' % (
                e(ev['title'] or 'Rendez-vous'), e(str(ev['starts'])[:16].replace('T', ' ')), (' · ' + e(ev['location'])) if ev['location'] else '')
            if ev.get('matter'):
                mid = ev['matter']
                links = ['<a class="ax-btn ghost" href="%s">Fiche du dossier</a>' % e('%s/fiche?matter=%s' % (prefix, mid), quote=True),
                         '<a class="ax-btn ghost" href="%s">Pièces et bordereaux</a>' % e('%s/pieces?matter=%s' % (prefix, mid), quote=True)]
                try:
                    from . import pieces510
                    last = [d for d in pieces510.drafts(desk, mid, 10) if d['status'] == 'cree']
                    if last:
                        links.append('<a class="ax-btn ghost" href="%s">Dernier bordereau (%s)</a>' % (
                            e('%s/pieces?draft=%s' % (prefix, last[0]['id']), quote=True), e(last[0]['updated'][:10])))
                except Exception:
                    pass
                html += '<div class="ax-actions m5-meeting-links">%s</div>' % ''.join(links)
            html += _fiche_html(content)
            html += ('<div class="ax-actions"><button type="button" class="ax-btn ghost m5-export" data-api="m500/meeting/markdown?id=%s">Télécharger la fiche (Markdown)</button>'
                     '<a class="ax-btn ghost" href="%s">Actualiser la fiche</a></div></section>') % (e(eid, quote=True), e('%s/rendez-vous?event=%s&refresh=1' % (prefix, eid), quote=True))
            saved = meeting500.report(desk, eid)
            html += ('<section id="compte-rendu"><h2>Compte rendu</h2><p class="vf-note">Collez ou dictez vos notes (une idée par ligne). Le projet réorganise vos notes en points abordés, décisions, actions et points à confirmer, '
                     '<strong>sans rien ajouter</strong> : un responsable ou une date absents sont signalés « à préciser ». Rien n’est envoyé ni enregistré avant votre clic.</p>'
                     '<form class="m5-form" data-api="m500/meeting/draft" data-result="#cr-text">%s<label class="m5-field">Vos notes<textarea name="notes" id="cr-notes" rows="9" maxlength="20000" required></textarea></label>'
                     '<div class="ax-actions"><button class="ax-btn" type="submit">Établir le projet</button><button type="button" class="ax-btn ghost m5-dictate" data-target="#cr-notes" title="Dictée locale">🎙 Dicter</button></div></form>'
                     '<div id="cr-warnings" class="m5-warn" hidden></div>'
                     '<form class="m5-form" data-api="m500/meeting/save" data-reload="1">%s<label class="m5-field">Projet de compte rendu (modifiable)<textarea name="text" id="cr-text" rows="14" maxlength="30000">%s</textarea></label>'
                     '<div class="ax-actions"><button class="ax-btn" type="submit">Enregistrer le brouillon</button><button class="ax-btn ghost" type="submit" data-extra="status=valide">Marquer comme validé</button></div></form>'
                     '<p class="vf-note">État : %s</p></section>') % (
                _hidden(id=eid), _hidden(id=eid), e(saved['content'] if saved else ''), e({'brouillon': 'brouillon enregistré', 'valide': 'validé'}.get(saved['status'], '') if saved else 'aucun compte rendu enregistré'))
    return _page(shell, prefix, auth, 'Rendez-vous', '/rendez-vous', html)


# ------------------------------------------------------------------------------------------------ conflits
def _check_html(desk, chk, prefix):
    r = chk['result']
    st = {'a_examiner': ('bad', 'À examiner'), 'information': ('warn', 'Information'), 'aucun_resultat': ('muted', 'Aucun résultat trouvé')}[chk['status']]
    html = '<article class="m5-check"><h3>Rapport du %s %s</h3><p>%s <strong>%s</strong></p>' % (e(chk['created'][:16].replace('T', ' ')), e('— ' + m5.matter_label(desk, chk['matter']) if chk['matter'] else '(avant ouverture)'), _badge(*st), e(r['summary']))
    html += '<p class="vf-note"><strong>Parties recherchées :</strong> %s</p>' % e(' ; '.join('%s (%s)' % (p['name'], conflicts500.ROLES[p['role']].split(' ')[0].lower()) for p in chk['parties']))
    if r['matches']:
        html += '<table class="vf-table"><thead><tr><th>Partie recherchée</th><th>Rapprochement</th><th>Dossier concerné</th><th>Rôle dans ce dossier</th><th>Niveau</th><th>Appréciation</th></tr></thead><tbody>'
        for m in r['matches']:
            src = ''.join('<li>%s%s%s</li>' % (e(s.get('label') or s.get('title', '')), (' — ' + e(s['date'])) if s.get('date') else '', ('<br><span class="vf-note">« %s »</span>' % e(s['excerpt'])) if s.get('excerpt') else '') for s in m['sources'])
            html += ('<tr><th scope="row">%s <span class="vf-note">(%s)</span></th><td>%s</td><td>%s<br><span class="vf-note">état : %s</span></td><td>%s</td><td>%s</td><td>%s<details><summary>Sources</summary><ul>%s</ul></details></td></tr>') % (
                e(m['party']), e(conflicts500.ROLES[m['role_new']].split(' ')[0].lower()), e(m['other_name']), e(m['matter_label']), e(m['matter_state'] or 'non classé'),
                e({'client': 'client', 'adverse': 'adversaire', 'tiers': 'tiers', 'inconnu': 'non précisé'}.get(m['other_role'], m['other_role'])), e(conflicts500.LEVEL_LABELS[m['level']]),
                _badge({'conflit_potentiel': 'bad', 'a_verifier': 'warn', 'meme_cote': 'muted'}[m['risk']], conflicts500.RISK_LABELS[m['risk']]), src)
        html += '</tbody></table>'
    html += '<div class="m5-limits"><h4>Ce que cette recherche ne garantit pas</h4><ul>%s</ul></div>' % ''.join('<li>%s</li>' % e(x) for x in r['limits'])
    cov = r['coverage']
    html += '<p class="vf-note">Périmètre : %d dossier(s) du registre, %d partie(s) enregistrée(s)%s. %s</p>' % (
        cov['matters'], cov['register'], (', %d courriel(s) indexé(s)' % cov['index']['mail_indexed']) if cov.get('index') else '', e(r['method']))
    if chk['decision']:
        html += '<p><strong>Décision :</strong> %s%s <span class="vf-note">(%s)</span></p>' % (e(chk['decision_label']), (' — ' + e(chk['decision_note'])) if chk['decision_note'] else '', e(chk['decided'][:16].replace('T', ' ')))
    else:
        opts = ''.join('<label class="m5-radio"><input type="radio" name="decision" value="%s" required> %s</label>' % (k, e(v)) for k, v in conflicts500.DECISIONS.items())
        need = 'Motif (obligatoire s’il y a des rapprochements)' if chk['status'] != 'aucun_resultat' else 'Note (facultative)'
        html += _form('m500/conflicts/decide', _hidden(id=chk['id']) + opts + '<label class="m5-field">%s<textarea name="note" rows="3" maxlength="500"></textarea></label>' % e(need), 'Enregistrer ma décision', cls='m5-decision')
    return html + '</article>'


def conflits_page(desk, auth, prefix, args, shell):
    try:
        conflicts500.scan_background(desk)
    except Exception:
        pass
    matter = args.get('matter', '')
    m5.log_access(desk, 'conflits', 'page', matter)
    html = ('<h1>Conflits d’intérêts</h1><p class="ax-muted">À l’ouverture d’un dossier, la recherche est lancée automatiquement ; son résultat est affiché ici <strong>avant tout acte</strong> : tant que vous n’avez pas enregistré votre décision, '
            'la production d’actes et de courriers pour ce dossier est refusée. <strong>L’absence de résultat n’est jamais une garantie.</strong> %s</p>' % e(LOCAL_NOTE))
    gate_on = bool(desk.settings('conflicts500:gate', True))
    html += ('<section class="m5-gate"><h2>Blocage de la production</h2><p class="vf-note">Lorsque ce réglage est actif, la production d’actes, de courriers et de pièces est refusée pour un dossier '
             '<strong>que vous venez d’ouvrir</strong> tant que vous n’avez pas enregistré votre décision. Les dossiers découverts dans Nextcloud ou existants ne sont jamais bloqués. '
             'Réglage actuel : <strong>%s</strong>.</p>%s</section>') % (
        'actif' if gate_on else 'désactivé (la recherche et l’alerte restent actives)',
        '<form class="m5-form m5-inline" data-api="m500/conflicts/gate" data-reload="1"><input type="hidden" name="enabled" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form>' % (
            'false' if gate_on else 'true', 'Désactiver le blocage' if gate_on else 'Réactiver le blocage'))
    pend = conflicts500.pending(desk)
    if pend:
        html += '<section class="m5-alert" role="alert"><h2>À examiner (%d)</h2><ul>%s</ul></section>' % (len(pend), ''.join(
            '<li><a href="%s">%s</a> — %s</li>' % (e('%s/conflits?matter=%s' % (prefix, p['matter']), quote=True), e(p['label']), e({'a_examiner': 'décision à prendre', 'decline': 'dossier décliné', 'a_etudier': 'à étudier'}.get(p['status'], p['status']))) for p in pend))
    rows = ''.join('<div class="m5-party-row"><input name="name" placeholder="Nom ou dénomination" maxlength="120" aria-label="Nom de la partie %d"><select name="role" aria-label="Rôle de la partie %d">%s</select><input name="email" type="email" placeholder="Courriel (facultatif)" aria-label="Courriel de la partie %d"></div>' % (
        i, i, ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if (k == 'adverse' and i > 1) else '', e(v)) for k, v in conflicts500.ROLES.items()), i) for i in range(1, 7))
    html += ('<section id="nouvelle"><h2>Nouvelle recherche</h2><p class="vf-note">Saisissez le client et les adversaires (jusqu’à six parties). Pour un dossier existant, ses parties déjà enregistrées sont reprises par « Relancer ».</p>'
             '<form class="m5-form" data-api="m500/conflicts/check" data-collect="parties" data-reload="1"><label class="m5-field">Dossier concerné (facultatif avant ouverture)<select name="matter">%s</select></label>%s'
             '<button class="ax-btn" type="submit">Rechercher les conflits</button></form></section>') % (_opts(desk, matter, 'Pas encore de dossier'), rows)
    if matter:
        try:
            parties = conflicts500.matter_parties(desk, matter)
        except Stop:
            parties = []
        html += '<section id="parties"><h2>Parties du dossier %s</h2>' % e(m5.matter_label(desk, matter))
        html += '<table class="vf-table"><thead><tr><th>Nom</th><th>Rôle</th><th>Origine</th><th></th></tr></thead><tbody>%s</tbody></table>' % (''.join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(p['name']), e(conflicts500.ROLES[p['role']]), e(p['source']),
                (_form('m500/conflicts/party/remove', _hidden(id=p['id']) + _field('Motif', 'reason', '', 'text', 'required minlength="5"'), 'Retirer', cls='m5-inline m5-mini') if p['id'] else '')) for p in parties) or '<tr><td colspan="4">Aucune partie.</td></tr>')
        html += _form('m500/conflicts/party/add', _hidden(matter=matter) + _field('Nom', 'name', '', 'text', 'required maxlength="120"') +
                      '<label class="m5-field">Rôle<select name="role">%s</select></label>' % ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in conflicts500.ROLES.items()) +
                      _field('Courriel', 'email', '', 'email'), 'Ajouter cette partie', cls='m5-inline')
        html += _form('m500/conflicts/recheck', _hidden(matter=matter), 'Relancer la recherche avec ces parties', cls='m5-inline') + '</section>'
    checks = conflicts500.listing(desk, matter, 20)
    html += '<section id="rapports"><h2>Rapports%s</h2>' % (' du dossier' if matter else ' récents')
    if not checks:
        html += '<p class="vf-note">Aucune recherche enregistrée.</p>'
    shown = args.get('check', '')
    for c in checks:
        if shown and c['id'] != shown:
            html += '<p><a href="%s">%s — %s (%s)</a></p>' % (e('%s/conflits?check=%s&matter=%s' % (prefix, c['id'], c['matter']), quote=True), e(c['created'][:16].replace('T', ' ')), e(c['matter_label']), e(c['status']))
        else:
            html += _check_html(desk, conflicts500.get(desk, c['id']), prefix)
            if not shown:
                shown = c['id']
                continue
    html += '</section>'
    return _page(shell, prefix, auth, 'Conflits d’intérêts', '/conflits', html)


# ------------------------------------------------------------------------------------------ prescriptions
def _calc_html(calc):
    html = '<ol class="m5-steps">%s</ol>' % ''.join('<li>%s</li>' % e(x) for x in calc.get('steps', []))
    if calc.get('warnings'):
        html += '<div class="m5-warn"><strong>Attention</strong><ul>%s</ul></div>' % ''.join('<li>%s</li>' % e(x) for x in calc['warnings'])
    if calc.get('to_confirm'):
        html += '<div class="m5-confirm"><strong>Reste à confirmer</strong><ul>%s</ul></div>' % ''.join('<li>%s</li>' % e(x) for x in calc['to_confirm'])
    return html


def prescriptions_page(desk, auth, prefix, args, shell):
    m5.log_access(desk, 'prescriptions', 'page', args.get('matter', ''))
    rows = limitation500.listing(desk, args.get('matter', ''), args.get('closed') == '1')
    html = ('<h1>Prescription et forclusion</h1><p class="ax-muted">Chaque alerte indique la <strong>règle</strong> appliquée, la <strong>date de départ</strong> retenue et ce qui <strong>reste à confirmer</strong>. '
            'Le calcul est déterministe (aucun modèle) et prudent : il donne le dernier jour utile sans supposer de prorogation. Une date n’est jamais présumée plus favorable. %s</p>' % e(LOCAL_NOTE))
    if rows:
        html += '<table class="vf-table"><thead><tr><th>Dernier jour utile</th><th>Règle</th><th>Dossier</th><th>Départ retenu</th><th>État</th><th>À confirmer</th><th></th></tr></thead><tbody>'
        today = date.today()
        for r in rows:
            days = (date.fromisoformat(r['due']) - today).days if r['due'] else None
            when = '' if days is None else (' (dépassé)' if days < 0 else ' (dans %d j)' % days)
            kind = 'bad' if (days is not None and days < 0) else ('warn' if r['status'] in ('a_confirmer', 'a_completer') else 'ok')
            html += '<tr><td><strong>%s</strong>%s</td><td>%s<br><span class="vf-note">%s</span></td><td>%s</td><td>%s<br><span class="vf-note">%s</span></td><td>%s</td><td>%d</td><td><a href="%s">Détail</a></td></tr>' % (
                e(r['due_label'] or '—'), e(when), e(r['rule_label']), e(', '.join(r['articles'])), e(r['matter_label']), e(r['start_label']),
                e(r['calc'].get('start_label', '') if r['calc'] else ''), _badge(kind, r['status_label']), len(r['calc'].get('to_confirm', [])) if r['calc'] else 0,
                e('%s/prescriptions?id=%s' % (prefix, r['id']), quote=True))
        html += '</tbody></table>'
    else:
        html += '<p class="vf-note">Aucune prescription suivie. Créez-en une ci-dessous, ou partez d’une date repérée dans un dossier.</p>'
    sug = limitation500.suggestions(desk)
    if sug:
        html += '<section id="suggestions"><h2>Dates de départ possibles repérées dans vos dossiers</h2><p class="vf-note">Rien n’est créé : choisissez la règle applicable dans le formulaire.</p><ul>'
        for s in sug[:12]:
            html += '<li>%s — %s : %s (%s)%s <a href="%s">Préparer</a></li>' % (e(m5.matter_label(desk, s['matter'])), e(_fr(s['date'])), e(s['title']), e(s['start_event_label']), '' if s['validated'] else ' <em>fait non validé</em>',
                                                                             e('%s/prescriptions?new=1&matter=%s&start=%s&start_event=%s#nouvelle' % (prefix, s['matter'], s['date'], s['start_event']), quote=True))
        html += '</ul></section>'
    spec = limitation500.rules_for_ui()
    groups = {}
    for r in spec:
        groups.setdefault(r['domain'], []).append(r)
    options = ''.join('<optgroup label="%s">%s</optgroup>' % (e(d), ''.join('<option value="%s"%s>%s</option>' % (r['id'], ' selected' if args.get('rule') == r['id'] else '', e(r['label'])) for r in rs)) for d, rs in groups.items())
    events_by_rule = {r['id']: r['start_events'] for r in spec}
    html += ('<section id="nouvelle"><h2>Nouvelle prescription</h2>'
             '<form class="m5-form" data-api="m500/limitation/create" data-reload="1" id="lim-form" data-rules="%s">'
             '<label class="m5-field">Dossier<select name="matter" required>%s</select></label>'
             '<label class="m5-field">Règle<select name="rule" id="lim-rule" required>%s</select></label>'
             '<label class="m5-field">Point de départ<select name="start_event" id="lim-event"></select></label>%s'
             '%s<div class="ax-actions"><button class="ax-btn" type="submit">Créer (à confirmer)</button><button type="button" class="ax-btn ghost" id="lim-preview">Calculer sans enregistrer</button></div></form>'
             '<div id="lim-result" class="m5-result" aria-live="polite"></div></section>') % (
        e(json.dumps(events_by_rule, ensure_ascii=False), quote=True), _opts(desk, args.get('matter', '')), options,
        _field('Date de départ', 'start', args.get('start', ''), 'date', 'required'), _field('Note (facultatif)', 'note', '', 'text', 'maxlength="300"'))
    lid = args.get('id', '')
    if lid:
        row = limitation500._row(desk, lid)
        if row:
            p = limitation500.public(row)
            html += '<section id="detail"><h2>%s — %s</h2><p>%s Dernier jour utile : <strong>%s</strong></p>' % (e(p['rule_label']), e(m5.matter_label(desk, p['matter'])), _badge('warn' if p['status'] in ('a_confirmer', 'a_completer') else 'ok', p['status_label']), e(p['due_label'] or 'non calculé'))
            if p['error']:
                html += '<p class="m5-warn">%s</p>' % e(p['error'])
            html += _calc_html(p['calc']) if p['calc'] else ''
            if p['calc'].get('source_url'):
                html += '<p class="vf-note">Texte de référence : <a href="%s" rel="noopener noreferrer" target="_blank">Légifrance</a> (vérifié le %s ; à revérifier pour tout usage important).</p>' % (e(p['calc']['source_url'], quote=True), e(limitation500.load()['general']['verified_on']))
            html += '<h3>Événements saisis</h3>'
            if p['events']:
                html += '<table class="vf-table"><thead><tr><th>Événement</th><th>Date</th><th></th></tr></thead><tbody>%s</tbody></table>' % ''.join(
                    '<tr><td>%s</td><td>%s%s</td><td>%s</td></tr>' % (e(limitation500.load()['general']['event_types'][ev['type']]['label']), e(_fr(ev['date'])), (' → ' + e(_fr(ev['end']))) if ev.get('end') else '',
                        _form('m500/limitation/event/remove', _hidden(id=lid, index=i) + _field('Motif', 'reason', '', 'text', 'required minlength="5"'), 'Retirer', cls='m5-inline m5-mini')) for i, ev in enumerate(p['events']))
            else:
                html += '<p class="vf-note">Aucun : ni demande en justice, ni reconnaissance, ni suspension.</p>'
            types = ''.join('<option value="%s">%s</option>' % (k, e(v['label'])) for k, v in limitation500.load()['general']['event_types'].items())
            html += _form('m500/limitation/event', _hidden(id=lid) + '<label class="m5-field">Événement<select name="type">%s</select></label>' % types + _field('Date (ou début)', 'date', '', 'date', 'required') + _field('Fin (suspension terminée)', 'end', '', 'date'),
                          'Ajouter l’événement et recalculer', cls='m5-inline')
            actions = ''
            if p['status'] in ('a_confirmer',):
                actions += _form('m500/limitation/confirm', _hidden(id=lid), 'Confirmer la règle et le départ', cls='m5-inline')
            actions += _form('m500/limitation/correct', _hidden(id=lid) + _field('Nouveau départ', 'start', '', 'date') + _field('Motif', 'reason', '', 'text', 'required minlength="5"'), 'Corriger le départ', cls='m5-inline')
            outcomes = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in limitation500.OUTCOMES.items())
            actions += _form('m500/limitation/close', _hidden(id=lid) + '<label class="m5-field">Issue<select name="outcome">%s</select></label>' % outcomes + _field('Motif', 'reason', '', 'text'), 'Clore', cls='m5-inline')
            html += actions
            jr = limitation500.journal(desk, lid, 20)
            html += '<details><summary>Journal des modifications</summary><ul>%s</ul></details></section>' % ''.join('<li>%s — %s %s</li>' % (e(j['at'][:16].replace('T', ' ')), e(j['action']), e(j['reason'])) for j in jr)
    html += ('<details class="m5-rules"><summary>Règles disponibles et textes (%d)</summary><table class="vf-table"><thead><tr><th>Règle</th><th>Délai</th><th>Nature</th><th>Textes</th></tr></thead><tbody>%s</tbody></table>'
             '<p class="vf-note">Liste non exhaustive. Hors périmètre : %s. Les textes ont été vérifiés le %s.</p></details>') % (
        len(spec), ''.join('<tr><th scope="row">%s</th><td>%d %s</td><td>%s</td><td><a href="%s" rel="noopener noreferrer" target="_blank">%s</a></td></tr>' % (e(r['label']), r['amount'], e(r['unit']), e(r['nature_label']), e(r['source_url'], quote=True), e(', '.join(r['articles']))) for r in spec),
        e('; '.join(limitation500.load()['uncovered'])), e(limitation500.load()['general']['verified_on']))
    return _page(shell, prefix, auth, 'Prescription et forclusion', '/prescriptions', html)


# ----------------------------------------------------------------------------------------------- pilotage
def pilotage_page(desk, auth, prefix, args, shell):
    today = date.today()
    periods = [(today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')]
    periods = [today.strftime('%Y-%m')] + periods + [p for p in report500.history(desk) if p not in periods]
    period = args.get('period') or periods[1]
    data = report500.generate(desk, period, force=args.get('regenerate') == '1')
    d, h, b, q, dl = data['dossiers'], data['honoraires'], data['brouillons'], data['qualite'], data['delais']
    sel = ''.join('<option value="%s"%s>%s</option>' % (p, ' selected' if p == data['period'] else '', e(p)) for p in dict.fromkeys(periods))
    html = ('<h1>Pilotage mensuel — %s</h1><p class="ax-muted">%s</p>'
            '<form class="m5-inline m5-goto" method="get" action="%s/pilotage-mensuel"><label class="m5-field">Mois<select name="period">%s</select></label><button class="ax-btn ghost" type="submit">Afficher</button></form>') % (
        e(data['period']), e(data['notice']), e(prefix), sel)
    def tile(label, value, sub=''):
        return '<div class="m5-tile"><span class="m5-tile-v">%s</span><span class="m5-tile-l">%s</span>%s</div>' % (e(str(value)), e(label), ('<span class="vf-note">%s</span>' % e(sub)) if sub else '')
    pct = lambda v, n: ('%s %%' % str(v).replace('.', ',')) if v is not None else 'échantillon insuffisant'
    html += '<section><h2>Dossiers</h2><div class="m5-tiles">%s%s%s%s</div><p class="vf-note">%s</p></section>' % (
        tile('Dossiers suivis', d['total']), tile('Actifs', d['by_state']['active']), tile('À confirmer', d['by_state']['to_confirm']), tile('Ouverts dans le mois', d['opened_in_period']), e(d['definition']))
    html += '<section><h2>Délais à venir</h2><div class="m5-tiles">%s%s%s</div>' % (tile('Échus non clos', dl['overdue_not_closed']), tile('Sous 30 jours', dl['within_30']), tile('Sous 90 jours', dl['within_90']))
    if dl['list']:
        html += '<table class="vf-table"><thead><tr><th>Date</th><th>Nature</th><th>Délai</th><th>Dossier</th><th>État</th></tr></thead><tbody>%s</tbody></table>' % ''.join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['due']), e(r['kind']), e(r['label']), e(r['matter']), e(r['status'])) for r in dl['list'])
    html += '<p class="vf-note">%s</p></section>' % e(dl['definition'])
    html += '<section><h2>Temps et honoraires</h2><div class="m5-tiles">%s%s%s%s</div>' % (
        tile('Temps validé', m5.hours(h['validated_minutes'])), tile('Valeur HT', m5.euros(h['validated_amount_cents'])), tile('Facturé HT (mois)', m5.euros(h['invoiced_in_period_cents']), '%d facture(s)' % h['invoices']), tile('Reste à facturer HT', m5.euros(h['to_bill_total_cents'])))
    if h['by_matter']:
        html += '<table class="vf-table"><thead><tr><th>Dossier</th><th>Temps</th><th>Valeur HT</th></tr></thead><tbody>%s</tbody></table>' % ''.join('<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (e(r['matter']), e(m5.hours(r['minutes'])), e(m5.euros(r['amount_cents']))) for r in h['by_matter'])
    for a in h['budget_alerts']:
        html += '<p class="m5-warn">Budget %s : %s (%s %%)</p>' % ('dépassé' if a['level'] == 'depasse' else 'bientôt atteint', e(a['matter']), e(str(a['ratio_pct']).replace('.', ',')))
    html += '<p class="vf-note">Propositions en attente (non comptées) : %d. %s</p></section>' % (h['pending_proposals'], e(h['definition']))
    html += '<section><h2>Brouillons</h2><div class="m5-tiles">%s%s%s%s</div><p class="vf-note">%s</p></section>' % (
        tile('Courriels traités', b['handled'] if b['handled'] is not None else 'n.d.'), tile('Brouillons à relire', b['waiting_now'] if b['waiting_now'] is not None else 'n.d.'), tile('Envoyés', b['sent_total']), tile('Envoyés tels quels', b['sent_as_is']), e(b['definition']))
    html += '<section><h2>Qualité</h2><div class="m5-tiles">%s%s%s%s%s</div><p class="vf-note">%s</p></section>' % (
        tile('Brouillons tels quels', pct(q['drafts_as_is_pct'], q['drafts_sample']), 'sur %d' % q['drafts_sample']), tile('Corrections de délais', q['deadline_corrections']),
        tile('Traitements en erreur', pct(q['job_error_pct'], 0) if q['job_error_pct'] is not None else 'n.d.'), tile('Temps proposés validés', pct(q['time_proposals_accepted_pct'], q['time_proposals_sample']), 'sur %d' % q['time_proposals_sample']), tile('Conflits examinés', q['conflict_checks_decided']), e(q['definition']))
    html += ('<div class="ax-actions"><button type="button" class="ax-btn ghost m5-export" data-api="m500/report/markdown?period=%s">Exporter (Markdown)</button>'
             '<button type="button" class="ax-btn ghost m5-export" data-api="m500/report/csv?period=%s">Exporter (CSV)</button>'
             '<a class="ax-btn ghost" href="%s">Recalculer</a></div>') % (e(data['period'], quote=True), e(data['period'], quote=True), e('%s/pilotage-mensuel?period=%s&regenerate=1' % (prefix, data['period']), quote=True))
    return _page(shell, prefix, auth, 'Pilotage mensuel', '/pilotage-mensuel', html)


PAGES = {'/cabinet': cabinet_page, '/honoraires': honoraires_page, '/rendez-vous': rendez_vous_page, '/conflits': conflits_page,
         '/prescriptions': prescriptions_page, '/pilotage-mensuel': pilotage_page}


# ------------------------------------------------------------------------------------------------- API
def _s(data, key, default=''):
    return str(data.get(key, default) if data.get(key) is not None else default)


def _download(name, mime, text):
    return {'download': {'filename': name, 'mime': mime, 'text': text}}


def handle(desk, name, data, method='POST', args=None):
    args = args or {}
    n = name[len('m500/'):] if name.startswith('m500/') else name
    if method == 'GET':
        if n == 'time/summary':
            m5.log_access(desk, 'honoraires', 'resume', args.get('matter', ''))
            return time500.summary(desk, args.get('matter', ''))
        if n == 'time/overview':
            m5.log_access(desk, 'honoraires', 'liste', '')
            return {'rows': time500.overview(desk)}
        if n == 'time/reconcile':
            m5.log_access(desk, 'honoraires', 'rapprochement', args.get('matter', ''))
            return time500.reconcile(desk, args.get('matter', ''))
        if n == 'time/export':
            return _download('temps-%s.csv' % args.get('matter', 'dossier'), 'text/csv', time500.export_entries(desk, args.get('matter', '')))
        if n == 'meeting/list':
            return {'events': meeting500.upcoming(desk, int(args.get('days', 7) or 7), int(args.get('past', 2) or 2))}
        if n == 'meeting/fiche':
            return meeting500.fiche(desk, args.get('id', ''), args.get('refresh') == '1')
        if n == 'meeting/markdown':
            return _download('fiche-rendez-vous.md', 'text/markdown', meeting500.markdown(meeting500.fiche(desk, args.get('id', ''))))
        if n == 'conflicts/get':
            return conflicts500.get(desk, args.get('id', ''))
        if n == 'conflicts/list':
            return {'checks': conflicts500.listing(desk, args.get('matter', ''))}
        if n == 'limitation/preview':
            m5.log_access(desk, 'prescriptions', 'apercu', '')
            try:
                return limitation500.compute(args.get('rule', ''), args.get('start', ''), args.get('start_event', ''))
            except limitation500.LimitationError as ex:
                raise Stop(ex.code) from ex
        if n == 'limitation/journal':
            return {'journal': limitation500.journal(desk, args.get('id', ''))}
        if n == 'limitation/list':
            m5.log_access(desk, 'prescriptions', 'liste', args.get('matter', ''))
            return {'items': limitation500.listing(desk, args.get('matter', ''), args.get('closed') == '1')}
        if n == 'report':
            return report500.generate(desk, args.get('period', ''))
        if n == 'report/markdown':
            d = report500.generate(desk, args.get('period', ''))
            return _download('pilotage-%s.md' % d['period'], 'text/markdown', report500.markdown(d))
        if n == 'report/csv':
            d = report500.generate(desk, args.get('period', ''))
            return _download('pilotage-%s.csv' % d['period'], 'text/csv', report500.csv_text(d))
        raise Stop('route_inconnue')
    # ---- écritures
    if n == 'time/estimate':
        return time500.estimate(desk, _s(data, 'matter'), _s(data, 'since'), _s(data, 'until'))
    if n == 'time/validate':
        return time500.validate(desk, _s(data, 'id'), data.get('minutes'), _s(data, 'label'), data.get('rate'))
    if n == 'time/reject':
        return time500.reject(desk, _s(data, 'id'), _s(data, 'note'))
    if n == 'time/add':
        return time500.add_manual(desk, _s(data, 'matter'), _s(data, 'day'), data.get('minutes'), _s(data, 'label'), data.get('rate'))
    if n == 'time/cancel':
        return time500.cancel_entry(desk, _s(data, 'id'), _s(data, 'reason'))
    if n == 'time/terms':
        return time500.set_terms(desk, _s(data, 'matter'), _s(data, 'mode'), _s(data, 'rate', '0'), _s(data, 'budget', '0'), data.get('alert_pct', 80) or 80, _s(data, 'note'))
    if n == 'time/bareme':
        return time500.set_bareme(desk, {k: v for k, v in data.items() if k in time500.BAREME_DEFAUT and str(v).strip()})
    if n == 'invoice/add':
        return time500.add_invoice(desk, _s(data, 'matter'), _s(data, 'number'), _s(data, 'day'), _s(data, 'amount'))
    if n == 'invoice/delete':
        return time500.delete_invoice(desk, _s(data, 'id'), _s(data, 'reason'))
    if n == 'invoice/import':
        return time500.import_invoices(desk, _s(data, 'text'), _s(data, 'matter'))
    if n == 'meeting/prepare':
        return meeting500.fiche(desk, _s(data, 'id'), refresh=True)
    if n == 'meeting/draft':
        out = meeting500.draft(desk, _s(data, 'id'), _s(data, 'notes'))
        m5.log_access(desk, 'rendez-vous', 'compte_rendu_projet', out['matter'])
        return out
    if n == 'meeting/save':
        return meeting500.save_report(desk, _s(data, 'id'), _s(data, 'text'), _s(data, 'status', 'brouillon') or 'brouillon')
    if n == 'conflicts/check':
        return {'id': conflicts500.check(desk, data.get('parties') or [], _s(data, 'matter'))['id']}
    if n == 'conflicts/gate':
        return conflicts500.set_gate(desk, data.get('enabled') in (True, 'true'))
    if n == 'conflicts/recheck':
        return {'id': conflicts500.recheck(desk, _s(data, 'matter'))['id']}
    if n == 'conflicts/decide':
        r = conflicts500.decide(desk, _s(data, 'id'), _s(data, 'decision'), _s(data, 'note'))
        return {'decision': r['decision'], 'status': r['status']}
    if n == 'conflicts/party/add':
        return conflicts500.add_party(desk, _s(data, 'matter'), _s(data, 'name'), _s(data, 'role'), _s(data, 'email'))
    if n == 'conflicts/party/remove':
        return conflicts500.remove_party(desk, _s(data, 'id'), _s(data, 'reason'))
    if n == 'limitation/create':
        try:
            r = limitation500.create(desk, _s(data, 'matter'), _s(data, 'rule'), _s(data, 'start'), _s(data, 'start_event'), _s(data, 'note'))
        except limitation500.LimitationError as ex:
            raise Stop(ex.code) from ex
        return {'id': r['id'], 'status': r['status'], 'due': r['due'], 'created_now': r['created_now']}
    if n == 'limitation/confirm':
        return {'status': limitation500.confirm(desk, _s(data, 'id'))['status']}
    if n == 'limitation/correct':
        return {'status': limitation500.correct(desk, _s(data, 'id'), _s(data, 'reason'), _s(data, 'start'), _s(data, 'rule'), _s(data, 'start_event'))['status']}
    if n == 'limitation/event':
        return {'due': limitation500.add_event(desk, _s(data, 'id'), _s(data, 'type'), _s(data, 'date'), _s(data, 'end'))['due']}
    if n == 'limitation/event/remove':
        return {'due': limitation500.remove_event(desk, _s(data, 'id'), data.get('index'), _s(data, 'reason'))['due']}
    if n == 'limitation/close':
        return {'status': limitation500.close(desk, _s(data, 'id'), _s(data, 'outcome'), _s(data, 'reason'))['status']}
    if n == 'report/regenerate':
        return {'period': report500.generate(desk, _s(data, 'period'), force=True)['period']}
    raise Stop('route_inconnue')

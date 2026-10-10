"""Pages et routes 4.6.0 : cockpit, fiche de dossier, chronologie, décisions sur les faits."""
from datetime import date, datetime
from html import escape as e
from urllib.parse import urlencode

from . import cockpit460, facts460, fiche460
from .common import Stop, load_matters
from .improvements36 import matter_option


def _a(prefix, href, label, cls=''):
    return '<a%s href="%s">%s</a>' % (' class="%s"' % cls if cls else '', e(prefix + href, quote=True), e(label))


# ------------------------------------------------------------- cockpit
def cockpit_html(desk, prefix, today=None, now=None):
    data = cockpit460.build(desk, today, now)
    head = ('<link rel="stylesheet" href="%s/static/v460.css"><script defer src="%s/static/v460.js"></script>' % (e(prefix), e(prefix)))
    cards = ''
    for i, c in enumerate(data['cards'], 1):
        more = ''
        if c['secondary']:
            more = '<details class="ck-more"><summary>Autres actions</summary>%s</details>' % ''.join(
                _a(prefix, h, l) for l, h in c['secondary'])
        cards += ('<li><article class="ck-card ck-%s" aria-labelledby="ck-t%d" data-ck-index="%d">'
                  '<div class="ck-top"><span class="ck-num" aria-hidden="true">%d</span><span class="ck-badge">%s</span>'
                  '<small class="ck-matter">%s</small></div><h2 id="ck-t%d">%s</h2><p>%s</p>'
                  '<p class="ck-actions"><a class="ck-main" data-ck-main href="%s">%s</a></p>%s</article></li>') % (
            e(c['kind']), i, i, i, e(c['urgency']), e(c['matter_name']), i, e(c['title']), e(c['detail']),
            e(prefix + c['href'], quote=True), e(c['action_label']), more)
    if not cards:
        cards = '<li><p class="ck-empty">Rien n’exige votre décision pour le moment.</p></li>'
    if data['folded_count']:
        items = ''.join('<li>%s <span class="ck-muted">· %s · %s</span></li>' % (
            _a(prefix, c['href'], c['title']), e(c['matter_name']), e(c['urgency'])) for c in data['folded'])
        folded = ('<details class="ck-folded"><summary><strong>%d</strong> autre(s) élément(s) replié(s)</summary><ul>%s</ul></details>'
                  % (data['folded_count'], items))
    else:
        folded = '<p class="ck-muted">Aucun autre élément replié.</p>'
    n = len(data['cards'])
    sub = ('%d élément(s) demandent une décision, du plus urgent au moins urgent.' % data['total']) if data['total'] else 'Aucune décision en attente.'
    return (head + '<a class="ck-skip" href="#ck-cards">Aller aux cartes</a><section id="cockpit" aria-labelledby="ck-title">'
            '<h2 id="ck-title">Aujourd’hui</h2><p class="ck-sub" id="ck-sub">%s</p>'
            '<ol class="ck-cards" id="ck-cards">%s</ol>%s'
            '<p class="ck-foot">%s · <span class="ck-muted">Touches 1 à %d : ouvrir l’action de la carte correspondante.</span></p></section>') % (
        e(sub), cards, folded, _a(prefix, '/aujourdhui?vue=detail', 'Vue détaillée (toutes les rubriques)'), max(n, 1))


# --------------------------------------------------------------- fiche
def _badge(status, label, conf=''):
    cls = {'validated': 'ok', 'pinned': 'ok', 'configured': 'ok', 'suggested': 'warn', 'derived': 'warn', 'disputed': 'bad'}.get(status, '')
    extra = ' · confiance %s' % {'high': 'élevée', 'medium': 'moyenne', 'low': 'basse'}[conf] if conf and status in ('suggested', 'derived') else ''
    return '<span class="fc-badge %s">%s%s</span>' % (cls, e(label), e(extra))


def _sources(prefix, sources):
    out = []
    for s in sources:
        label = e(s['label'])
        out.append('<a href="%s">%s</a>' % (e(prefix + s['href'], quote=True), label) if s.get('href') else label)
    return '<span class="fc-src">Source : %s</span>' % (', '.join(out) if out else 'non précisée')


def _entry(prefix, f, empty):
    if not f:
        return '<p class="fc-empty">%s</p>' % e(empty)
    return '<p><strong>%s</strong> %s<br>%s</p>' % (e(f['value']), _badge(f['status'], f['status_label'], f['confidence']), _sources(prefix, f['sources']))


def fiche_page(desk, auth, prefix, args, shell):
    mid = str(args.get('matter', ''))
    if not mid:
        rows = ''.join('<li>%s</li>' % _a(prefix, '/fiche?' + urlencode({'matter': m['id']}), matter_option(m)) for m in load_matters(desk.c)[:300])
        return shell('Fiche de dossier', '<h1>Fiche de dossier</h1><p class="ax-muted">Choisissez un dossier.</p><ul class="fc-list">%s</ul>' % rows,
                     prefix, auth['csrf'], '/dossiers', _head(prefix))
    from . import dossier5624
    m = fiche460.find_matter(desk, mid)
    onglet = str(args.get('onglet') or 'synthese')
    if onglet not in dict(dossier5624.RUBRIQUES):
        onglet = 'synthese'
    if onglet not in ('synthese', 'faits'):   # 5.6.24 (F34) : une rubrique par onglet, chacune avec ses sources et sa fraîcheur
        data = dossier5624.rubrique(desk, mid, onglet)
        body = _top(desk, prefix, mid, matter_option(m), onglet, data['analysis']) + dossier5624.render(desk, prefix, mid, onglet, data)
        return shell('Fiche · ' + matter_option(m), body, prefix, auth['csrf'], '/dossiers', _head(prefix))
    try:
        page = max(1, int(args.get('page') or 1))
    except ValueError:
        page = 1
    if onglet == 'faits':
        f = fiche460.build_fiche(desk, mid)
        data = dossier5624.rubrique(desk, mid, 'faits')
        proc = dossier5624.rubrique(desk, mid, 'procedure')
        body = (_top(desk, prefix, mid, f['matter']['label'], onglet, data['analysis']) + dossier5624.rappel_html(prefix, mid, data)
                + dossier5624.render(desk, prefix, mid, 'procedure', proc).replace('<h2>Juridiction et échéances</h2>', '<h2>Rappel de la procédure</h2>', 1)
                + facts_section(f, mid, prefix, page))
        return shell('Fiche · ' + f['matter']['label'], body, prefix, auth['csrf'], '/dossiers', _head(prefix))
    f = fiche460.build_fiche(desk, mid)
    synth = dossier5624.rubrique(desk, mid, 'synthese')
    q = urlencode({'matter': mid})
    parties = ''.join('<li><strong>%s</strong> %s<br>%s</li>' % (e(p['value']), _badge(p['status'], p['status_label'], p['confidence']), _sources(prefix, p['sources']))
                      for p in f['parties']) or '<li class="fc-empty">Aucune partie connue.</li>'
    nd = f['next_deadline']
    if nd:
        when = 'aujourd’hui' if nd['days'] == 0 else ('dépassée de %d j' % -nd['days'] if nd['days'] < 0 else 'dans %d j' % nd['days'])
        deadline = ('<p><strong>%s</strong> – %s <span class="fc-muted">(%s)</span> %s<br><span class="fc-src">Source : %s · %s</span></p>' % (
            e(nd['due_label']), e(nd['label']), e(when), _badge('validated' if nd['validated'] else 'suggested', 'Confirmée' if nd['validated'] else 'À confirmer'),
            _a(prefix, nd['source_href'], nd['source']) if nd['source_href'] else e(nd['source']), _a(prefix, nd['href'], 'voir le calcul')))
    else:
        deadline = '<p class="fc-empty">Aucune échéance en cours pour ce dossier.</p>'

    def act(x, empty):
        if not x:
            return '<p class="fc-empty">%s</p>' % e(empty)
        return '<p><strong>%s</strong><br><span class="fc-muted">%s · %s</span><br><span class="fc-src">Source : %s</span></p>' % (
            e(x['title']), e(x['kind_label']), e(x['at'][:10]), _a(prefix, x['href'], x['source']) if x['href'] else e(x['source']))

    pend = ''.join('<li>%s</li>' % _a(prefix, p['href'], p['label']) for p in f['pending']) or '<li class="fc-empty">Rien en attente.</li>'
    docs = ''.join('<li>%s <span class="fc-muted">%s</span></li>' % (_a(prefix, d['href'], d['name']), e(str(d['modified'])[:10])) for d in f['documents']) or '<li class="fc-empty">Aucune pièce indexée.</li>'
    drafts = ''.join('<li>%s</li>' % _a(prefix, d['href'], d['subject']) for d in f['drafts']) or '<li class="fc-empty">Aucun brouillon en attente.</li>'
    body = _top(desk, prefix, mid, f['matter']['label'], 'synthese', synth['analysis']) + (
        '<div class="fc-grid">'
        '<section class="ax-card"><h2>Juridiction</h2>%s<h2>Numéro de RG</h2>%s</section>'
        '<section class="ax-card"><h2>Parties</h2><ul class="fc-list">%s</ul></section>'
        '<section class="ax-card"><h2>Prochaine échéance</h2>%s</section>'
        '<section class="ax-card"><h2>Dernier acte reçu</h2>%s<h2>Dernier courriel envoyé</h2>%s</section>'
        '<section class="ax-card"><h2>Points en attente</h2><ul class="fc-list">%s</ul></section>'
        '<section class="ax-card"><h2>Pièces récentes</h2><ul class="fc-list">%s</ul><h2>Brouillons</h2><ul class="fc-list">%s</ul></section>'
        '</div>%s%s'
    ) % (_entry(prefix, f['jurisdiction'], 'Non renseignée : aucune pièce ne la mentionne encore.'),
         _entry(prefix, f['case_number'], 'Non renseigné : aucune pièce ne le mentionne encore.'),
         parties, deadline, act(f['last_received'], 'Aucun acte ou courriel reçu enregistré.'), act(f['last_sent'], 'Aucun courriel envoyé enregistré.'),
         pend, docs, drafts, dossier5624.synthese_html(prefix, mid, synth), facts_section(f, mid, prefix, page))
    return shell('Fiche · ' + f['matter']['label'], body, prefix, auth['csrf'], '/dossiers', _head(prefix))


def _top(desk, prefix, mid, label, onglet, analysis):
    """En-tête commun : titre, liens, onglets du dossier et analyse suivie (5.6.24)."""
    from . import dossier5624
    q = urlencode({'matter': mid})
    return ('<p class="fc-back">%s</p><h1>%s</h1><p class="ax-muted fc-actions">%s · %s · %s · %s · %s</p>%s%s') % (
        _a(prefix, '/dossier?' + urlencode({'id': mid}), '← Retour au dossier'), e(label), _a(prefix, '/chronologie?' + q, 'Chronologie'), _a(prefix, '/documents?' + q, 'Documents'),
        _a(prefix, '/production?' + q, 'Produire un acte'), _a(prefix, '/matter?' + urlencode({'id': mid}), 'Ancienne vue du dossier'),
        _a(prefix, '/verification?' + q, 'Vérifier un texte'), dossier5624.tabs_html(prefix, mid, onglet), dossier5624.analysis_html(prefix, mid, analysis))


def _head(prefix):
    return '<link rel="stylesheet" href="%s/static/v460.css"><script defer src="%s/static/v460.js"></script>' % (e(prefix), e(prefix))


FACTS_PER_PAGE = 50


def facts_section(f, mid, prefix, page=1):
    """5.6.24 (F27, F32, F33) : correction structurée (texte, intitulé, date confirmée), révision attendue, preuve modifiée signalée,
    pagination de l'affichage (le traitement, lui, couvre toutes les pièces)."""
    facts = f['facts']
    pages = max(1, (len(facts) + FACTS_PER_PAGE - 1) // FACTS_PER_PAGE)
    page = min(max(1, int(page or 1)), pages)
    items = ''
    for x in facts[(page - 1) * FACTS_PER_PAGE:page * FACTS_PER_PAGE]:
        todo = x['status'] == 'suggested'
        if todo:
            buttons = ('<div class="fc-btns"><button type="button" class="ax-btn fc-validate">Valider</button>'
                       '<button type="button" class="ax-btn ghost fc-correct">Corriger</button>'
                       '<button type="button" class="ax-btn danger fc-reject">Refuser</button></div>')
        else:
            buttons = ('<div class="fc-btns"><button type="button" class="ax-btn ghost fc-correct">Corriger</button>'
                       '<button type="button" class="ax-btn danger fc-reject">Ce fait est faux : le refuser</button></div>')
        src = ''.join('<blockquote>%s<br><span class="fc-muted">%s</span></blockquote>' % (
            e(s['excerpt']), (_a(prefix, s['href'], s['label']) if s.get('href') else e(s['label']))) for s in x['sources'])
        proof = ('<p class="fc-muted"><span class="fc-badge warn">preuve modifiée</span> %s</p>' % e(x.get('note', ''))) if x.get('proof_state') else ''
        edit = ('<div class="fc-editor" hidden><label>Intitulé<input class="fc-edit-title" maxlength="500" value="%s"></label>'
                '<label>Texte corrigé<textarea class="fc-edit" rows="2">%s</textarea></label>'
                '<label>Date (AAAA-MM-JJ, AAAA-MM ou AAAA ; vide si sans date)<input class="fc-edit-date" maxlength="10" pattern="\\d{4}(-\\d{2}(-\\d{2})?)?" value="%s"></label></div>') % (
            e(x['title'], quote=True), e(x['value'] or x['title']), e(str(x.get('event_date') or '')[:10], quote=True))
        items += ('<li class="fc-fact" data-id="%s" data-status="%s" data-revision="%s"><div><span class="fc-type">%s</span> %s%s</div>'
                  '<p class="fc-text">%s</p>%s%s%s%s</li>') % (
            e(x['id'], quote=True), e(x['status']), e(str(x.get('revision') or ''), quote=True), e(facts460.TYPE_LABELS.get(x.get('type', ''), x.get('type', ''))),
            _badge(x['status'], x['status_label'], x['confidence']), (' <span class="fc-muted">%s</span>' % e(str(x['event_date'])[:10])) if x.get('event_date') else '',
            e(x['value'] if x['value'] else x['title']), proof, edit, src, buttons)
    refused = ('<p class="fc-muted">%d fait(s) refusé(s) : ils ne sont ni reproposés ni utilisés dans les rédactions.</p>' % f['refused']) if f['refused'] else ''
    nav = ''
    if pages > 1:
        nav = '<nav class="fc-pages" aria-label="Pages des faits">%s</nav>' % ' '.join(
            ('<strong>%d</strong>' % n) if n == page else _a(prefix, '/fiche?' + urlencode({'matter': mid, 'onglet': 'faits', 'page': n}) + '#faits', str(n)) for n in range(1, pages + 1))
    return ('<section class="ax-card" id="faits" data-matter="%s"><h2>Faits du dossier</h2>'
            '<p class="ax-muted">Les faits « à valider » sont proposés par l’agent à partir des pièces (repérage déterministe : RG, juridiction, montants, dates d’actes, parties) ; '
            'seuls les faits validés sont utilisés dans les rédactions et la synthèse. %d fait(s) au total.</p>'
            '<p><button type="button" class="ax-btn ghost" id="fc-scan">Rechercher des faits dans toutes les pièces</button> <span id="fc-scan-result" class="fc-muted"></span></p>'
            '<ul class="fc-facts">%s</ul>%s%s</section>') % (e(mid, quote=True), len(facts), items or '<li class="fc-empty">Aucun fait enregistré.</li>', nav, refused)


# --------------------------------------------------------- chronologie
FILTERS = (('', 'Tout', None), ('courriels', 'Courriels', {'email_received', 'email_sent', 'draft'}),
           ('pieces', 'Pièces', {'document'}), ('actes', 'Actes et échéances', {'start_event', 'deadline', 'completed_action', 'fact'}),
           ('agenda', 'Agenda et tâches', {'calendar', 'task'}))


def chronologie_page(desk, auth, prefix, args, shell):
    mid = str(args.get('matter', ''))
    if not mid:
        return fiche_page(desk, auth, prefix, {}, shell)
    chosen = str(args.get('type', ''))
    kinds = next((k for key, _, k in FILTERS if key == chosen), None)
    rows = fiche460.chronologie(desk, mid, 300, kinds)
    m = fiche460.find_matter(desk, mid)
    chips = ''.join('<a class="fc-chip%s" href="%s"%s>%s</a>' % (' on' if key == chosen else '', e(prefix + '/chronologie?' + urlencode({'matter': mid, **({'type': key} if key else {})}), quote=True),
                                                              ' aria-current="true"' if key == chosen else '', e(label)) for key, label, _ in FILTERS)
    lines = ''
    for r in rows:
        src = _a(prefix, r['href'], r['source']) if r['href'] else e(r['source'])
        if r.get('undated'):
            continue
        lines += ('<li class="fc-line fc-k-%s"><time datetime="%s">%s</time><div><span class="fc-type">%s</span> <strong>%s</strong>%s'
                  '<p class="fc-muted">%s</p><span class="fc-src">Source : %s</span></div></li>') % (
            e(r['kind']), e(r['at'][:10]), e(r['at'][:10] or 'date inconnue'), e(r['kind_label']), e(r['title']),
            '' if r['validated'] else ' <span class="fc-badge warn">%s</span>' % e(r['status_label']), e(r['detail'][:200]), src)
    undated = [r for r in rows if r.get('undated')]
    body = ('<h1>Chronologie · %s</h1><p class="ax-muted fc-actions">%s</p><nav class="fc-chips" aria-label="Filtrer">%s</nav>'
            '<ol class="fc-timeline">%s</ol>%s') % (
        e(matter_option(m)), _a(prefix, '/dossier?' + urlencode({'id': mid}), '← Retour au dossier'), chips,
        lines or '<li class="fc-empty">Aucun événement. Lancez « Analyser le dossier » depuis la fiche si les pièces viennent d’arriver.</li>',
        ('<h2>Dates inconnues ou partielles</h2><ul class="fc-list">%s</ul>' % ''.join('<li><span class="fc-type">%s</span> <strong>%s</strong> <span class="fc-muted">(%s)</span><br><span class="fc-src">Source : %s</span></li>' % (
            e(r['kind_label']), e(r['title']), e(r['at'] or 'date inconnue'), _a(prefix, r['href'], r['source']) if r['href'] else e(r['source'])) for r in undated)) if undated else '')
    return shell('Chronologie', body, prefix, auth['csrf'], '/dossiers', _head(prefix))


# ----------------------------------------------------------------- API
def handle(desk, name, data):
    mid = str(data.get('matter', ''))
    fields = {k: str(data[k])[:500] for k in ('title', 'event_date', 'actor', 'revision') if k in data and data[k] is not None}
    if name == 'fact/validate':
        return facts460.decide(desk, mid, str(data.get('id', '')), 'validate', str(data.get('text', ''))[:4000], **fields)
    if name == 'fact/reject':
        return facts460.decide(desk, mid, str(data.get('id', '')), 'reject', '', str(data.get('note', ''))[:500], **{k: v for k, v in fields.items() if k == 'revision'})
    if name == 'fact/scan':
        fiche460.find_matter(desk, mid)
        return {'job_id': desk.enqueue('extract_facts460', {'matter': mid}, priority=20),
                'message': 'Recherche des faits lancée dans toutes les pièces ; la chronologie sera mise à jour à la fin du même travail.'}
    if name in ('fiche/refresh', 'fiche/analyse'):   # 5.6.24 (F25) : une seule commande, regroupée, suivie étape par étape
        from . import dossier5624
        fiche460.find_matter(desk, mid)
        steps = data.get('steps')
        if isinstance(steps, str):
            steps = [s for s in steps.split(',') if s]
        return dossier5624.request_analysis(desk, mid, steps or None)
    if name == 'fiche/etat':
        from . import dossier5624
        fiche460.find_matter(desk, mid)
        st = dossier5624.state(desk, mid)
        scan = desk.db.execute("SELECT id FROM jobs WHERE kind='extract_facts460' AND status IN ('pending','running') AND json_extract(args,'$.matter')=? LIMIT 1", (mid,)).fetchone()
        return {**st, 'scan_job': scan['id'] if scan else None}
    raise Stop('route_inconnue')

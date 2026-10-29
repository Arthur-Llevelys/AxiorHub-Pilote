"""5.6.79 : page Dossier refondue — cinq onglets et un volet d'aperçu des documents.

**Synthèse** : l'essentiel en un écran (parties, qualités et avocats ; procédure en cours ; prochaines dates ; objectif), les dernières
écritures et décisions, les documents récents, le travail récent de l'IA et ce qui reste à traiter.
**Documents** : tous les fichiers du dossier, classés (écritures, décisions, actes, nos pièces, pièces adverses, courriers, autres,
illisibles), triés du plus récent au plus ancien, avec un titre lisible ; un clic ouvre l'aperçu à droite.
**Faits et procédure** : procédure de référence et autres procédures, rappel des faits (chaque paragraphe renvoie à ses pièces),
chronologie, parties, actes et décisions.
**Échanges** : intervenants (parties, avocats lus dans les actes, correspondants, expéditeurs des courriels), courriels du dossier,
correspondances conservées, discussion interne.
**Travail** : question stratégique, nouveau courrier, missions, informations à fournir, relecture Word et outils du dossier.

Les anciennes vues restent servies (``vue=apercu``, ``pieces``, ``procedure``, ``courriels``, ``correspondances``, ``travaux``) et les
écrans détaillés de la 5.6.51 restent accessibles (``vue=informations``, ``vue=procedure-detail``, ``vue=pieces-detail``).
Chaque bloc est construit séparément : une donnée illisible n'empêche jamais l'affichage du reste de la page.
"""
from datetime import date
from html import escape as e
import json
import logging
from pathlib import PurePosixPath
from urllib.parse import urlencode

from .common import Stop, matter_display

TABS = (('synthese', 'Synthèse'), ('documents', 'Documents'), ('faits', 'Faits et procédure'), ('echanges', 'Échanges'), ('travail', 'Travail'))
ALIASES = {'': 'synthese', 'apercu': 'synthese', 'fiche': 'synthese', 'pieces': 'documents', 'procedure': 'faits', 'courriels': 'echanges',
           'correspondances': 'echanges', 'travaux': 'travail'}
DETAIL_VIEWS = ('informations', 'procedure-detail', 'pieces-detail')
CATEGORIES = (('', 'Tous'), ('ecritures', 'Écritures'), ('decisions', 'Décisions'), ('actes', 'Actes et RPVA'), ('nos', 'Nos pièces'),
              ('adverses', 'Pièces adverses'), ('courriers', 'Courriers'), ('autres', 'Autres'), ('illisibles', 'Illisibles'))
WRITINGS = ('conclusions', 'assignation', 'requete')
DECISIONS = ('jugement', 'ordonnance', 'arret')
PAGE = 60
log = logging.getLogger(__name__)


def _q(mid, view='', **extra):
    return '/dossier?' + urlencode({'id': mid, **({'vue': view} if view and view != 'synthese' else {}),
                                    **{k: v for k, v in extra.items() if v not in (None, '')}})


def _card(title, body, ident='', extra='', cls=''):
    return '<section class="d79-card%s"%s><div class="d79-card-head"><h2>%s</h2>%s</div>%s</section>' % (
        (' ' + cls) if cls else '', ' id="%s"' % e(ident, quote=True) if ident else '', e(title), extra, body)


def _safe(label, fn, *args, **kw):
    """Un bloc en erreur est remplacé par un message ; le reste de la page s'affiche."""
    try:
        return fn(*args, **kw)
    except Stop as ex:
        return '<p class="d79-warn">%s : %s</p>' % (e(label), e(str(ex)))
    except Exception:   # noqa: BLE001
        log.exception('dossier5679 : bloc %s', label)
        return '<p class="d79-warn">%s : affichage momentanément indisponible.</p>' % e(label)


def _date_fr(value):
    from .vue5651 import _date_fr as fr
    return fr(value) if value else ''


def _when(doc):
    return (doc.get('doc_date') or str(doc.get('modified') or '')[:10] or '')[:10]


def _doc_link(prefix, mid, doc, overview=None, cls='d79-doc'):
    """Lien d'un document : ouvre l'aperçu (volet de droite) ; sans script, la page du document."""
    from .fiche460 import doc_href
    from .suivi5679 import doc_title
    title, sub = doc_title(doc, overview)
    return '<a class="%s" href="%s" data-d79-doc="%s">%s</a>%s' % (
        cls, e(prefix + doc_href(doc['path'], mid), quote=True), e(doc['path'], quote=True), e(title),
        (' <span class="d79-file">%s</span>' % e(sub)) if sub else '')


def category(doc):
    n = doc['nature']
    if doc['reading'] in ('erreur', 'protegee', 'non_prise_en_charge'):
        return 'illisibles'
    if n in WRITINGS:
        return 'ecritures'
    if n in DECISIONS:
        return 'decisions'
    if n in ('signification', 'rpva'):
        return 'actes'
    if doc['piece_party'] and doc['piece_party'] != 'cabinet':
        return 'adverses'
    if doc['usage_piece'] == 'oui' or doc['piece_number']:
        return 'nos'
    if n in ('courrier', 'mise_en_demeure', 'courriel'):
        return 'courriers'
    return 'autres'


def _documents(desk, mid):
    from . import modele5651
    from .memoire5651 import not_a_document
    return [d for d in modele5651.documents(desk, mid) if not not_a_document(d['name'])]


# ------------------------------------------------------------------------------------------------ en-tête
def header_html(desk, prefix, m, view):
    from . import modele5651
    from .pause5629 import is_paused
    from .vue5651 import analysis_state
    from .missions5651 import panel_html
    mid = m['id']
    st = analysis_state(desk, mid)
    ref = modele5651.reference_procedure(desk, mid)
    on_pause = is_paused(desk, mid)
    pause_btn = ('<button type="button" class="ax-btn d27-resume" data-d27-pause="0" title="L’IA reprend le travail automatique sur ce dossier">▶ Reprendre l’IA</button>'
                 if on_pause else
                 '<button type="button" class="ax-btn ghost d27-pausebtn" data-d27-pause="1" title="Plus aucun travail automatique de l’IA sur ce dossier">⏸ Mettre l’IA en pause</button>')
    paused = ('<p class="d27-paused" role="status">⏸ <strong>IA en pause sur ce dossier.</strong> Aucun travail automatique n’est lancé ; '
              'vos demandes restent possibles.</p>') if on_pause else ''
    meta = ['Réf. ' + e(mid)]
    if m.get('client_name'):
        meta.append('client : ' + e(m['client_name']))
    if ref and (ref['juridiction'] or ref['rg']):
        meta.append(e(' · '.join(x for x in (ref['juridiction'], ('RG ' + ref['rg']) if ref['rg'] else '') if x)))
    when = (st['fiche'] or {}).get('created') or (st['rebuilt'] or {}).get('at') or ''
    files = [{'path': d['path'], 'name': d['name']} for d in modele5651.documents(desk, mid, limit=300)]
    more = ''.join('<li><a href="%s">%s</a></li>' % (e(prefix + href, quote=True), e(label)) for href, label in (
        (_q(mid, 'travail') + '#d79-strategie', 'Question stratégique'), (_q(mid, 'travail') + '#d79-courrier', 'Nouveau courrier'),
        ('/documents?' + urlencode({'matter': mid}), 'Tous les documents (Nextcloud)')))
    more += '<li><button type="button" class="d79-linkbtn" data-d27-prepare>Mettre à jour la fiche du dossier</button></li>'
    tabs = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + _q(mid, key), quote=True), ' aria-current="page"' if key == view else '', e(label))
                   for key, label in TABS)
    running = '<span class="d51-running" role="status">⏳ Analyse en cours</span>' if st['running'] else ''
    return ('<div class="d27 d51 d79" data-d27-matter="%s" data-d79-matter="%s"><nav class="d27-back"><a href="%s">← Tous les dossiers</a></nav>'
            '<header class="d79-head"><div class="d79-title"><h1>%s</h1><p class="d79-meta">%s</p>'
            '<p class="d79-state"><span class="d51-state d51-state-%s">%s</span> <span class="ax-muted">%s</span>%s</p></div>'
            '<div class="d79-actions">%s'
            '<form class="m5-form d51-inline" data-api="m5651/analyser" data-reload><input type="hidden" name="matter" value="%s">'
            '<button class="ax-btn ghost" type="submit" title="Reconstruit parties, procédures, documents et informations à partir des lectures déjà faites (sans IA)">'
            '↻ Actualiser</button></form>%s'
            '<details class="d27-more d79-more"><summary class="ax-btn ghost">Plus ▾</summary><ul>%s</ul></details></div></header>%s'
            '<nav class="d79-tabs" aria-label="Vues du dossier">%s</nav>') % (
        e(mid, quote=True), e(mid, quote=True), e(prefix + '/dossiers', quote=True), e(matter_display(m)), ' · '.join(meta),
        e(st['state']), e(st['label']), e(('actualisé le ' + _date_fr(when[:10])) if when else ''), running,
        pause_btn, e(mid, quote=True), panel_html(mid, files, prefix), more, paused, tabs)


PREVIEW = ('<section class="d79-preview" id="d79-preview" role="complementary" hidden aria-label="Aperçu du document"><div class="d79-preview-head"><strong data-d79-title>Aperçu</strong>'
           '<span class="d79-preview-actions"><a class="ax-btn ghost" data-d79-open hidden>Modifier</a><a class="ax-btn ghost" data-d79-dl>Télécharger</a>'
           '<button type="button" class="ax-btn ghost" data-d79-close aria-label="Fermer l’aperçu">×</button></span></div>'
           '<p class="ax-muted" data-d79-note hidden></p><div class="d79-preview-body" data-d79-body tabindex="0"></div></section>')


# ------------------------------------------------------------------------------------------------ Synthèse
def essentials_html(desk, prefix, m):
    from . import resume5659, modele5651
    mid = m['id']
    reps = resume5659.representation(desk, mid)
    rows = []
    for p in reps[:8]:
        rows.append('<li%s><strong>%s</strong>%s<span class="ax-muted">%s%s</span></li>' % (
            ' class="d79-client"' if p['client'] else '', e(p['nom']), ' <span class="d27-badge">client</span>' if p['client'] else '',
            (' — ' + e(p['qualite'])) if p['qualite'] else '', (' · ' + e(p['conseil'])) if p['conseil'] else ''))
    parties = ('<ul class="d79-list">%s</ul>' % ''.join(rows)) if rows else '<p class="ax-muted">Parties non encore identifiées : « ↻ Actualiser » relit les en-têtes des actes.</p>'
    if len(reps) > 8:
        parties += '<p><a href="%s">Toutes les parties (%d)</a></p>' % (e(prefix + _q(mid, 'faits') + '#d51-parties', quote=True), len(reps))
    ref = modele5651.reference_procedure(desk, mid)
    procs = modele5651.procedures(desk, mid)
    if ref:
        proc = '<p><strong>%s</strong>%s%s</p>' % (e(ref['juridiction'] or 'Juridiction à préciser'), (' · ' + e(ref['chambre'])) if ref.get('chambre') else '',
                                                  (' · RG ' + e(ref['rg'])) if ref['rg'] else '')
        if ref['stade']:
            proc += '<p class="ax-muted">%s</p>' % e(ref['stade'])
        from .vue5651 import _source_links
        proc += '<p class="d79-sources">%s</p>' % _source_links(prefix, mid, ref['sources'], 2)
        others = [p for p in procs if p['id'] != ref['id'] and p['statut'] != 'citee']
        if others:
            proc += '<p class="ax-muted">Auparavant : %s</p>' % e(' ; '.join(' · '.join(x for x in (p['juridiction'], ('RG ' + p['rg']) if p['rg'] else '') if x)
                                                                       for p in others[:3]))
    else:
        proc = '<p class="ax-muted">Aucune procédure identifiée. <a href="%s">Ajouter</a></p>' % e(prefix + _q(mid, 'faits') + '#d79-procedure', quote=True)
    dates, pending = resume5659.dates(desk, mid)
    dl = ''.join('<li><strong>%s%s</strong> — %s</li>' % (e(_date_fr(x['date'])), (' ' + e(x['heure'])) if x.get('heure') else '', e(x['titre']))
                 for x in dates[:4])
    dl = ('<ul class="d79-list">%s</ul>' % dl) if dl else '<p class="ax-muted">Aucune date confirmée à venir.</p>'
    if pending:
        dl += '<p class="ax-muted"><a href="%s">%d date(s) proposée(s) à confirmer</a></p>' % (e(prefix + '/echeances', quote=True), pending)
    objectif, _ = resume5659.decisions(desk, mid)
    obj = ''.join('<p>%s</p>' % e(d['texte']) for d in objectif[:2]) or \
        '<p class="ax-muted">Non renseigné. <a href="%s">Ajouter l’objectif</a></p>' % e(prefix + _q(mid, 'faits') + '#d51-decisions', quote=True)
    blocks = (('Parties et avocats', parties), ('Procédure en cours', proc), ('Prochaines dates', dl), ('Objectif du client', obj))
    return _card('L’essentiel', '<div class="d79-grid4">%s</div>' % ''.join('<div><h3>%s</h3>%s</div>' % (e(t), b) for t, b in blocks), 'd79-essentiel')


def latest_documents_html(desk, prefix, m):
    from .suivi5679 import digests_overview
    from . import modele5651
    mid = m['id']
    docs = _documents(desk, mid)
    overview = digests_overview(desk, mid)
    names = {p['id']: p['display_name'] for p in modele5651.parties(desk, mid, include_merged=True)}
    key = lambda d: _when(d)
    writings = sorted((d for d in docs if d['nature'] in WRITINGS + DECISIONS), key=key, reverse=True)[:6]
    recent = sorted((d for d in docs if d['nature'] not in WRITINGS + DECISIONS), key=lambda d: str(d.get('modified') or ''), reverse=True)[:8]

    def li(d, badge=True):
        who = names.get(d.get('emitter') or '', '')
        label = modele5651.NATURES.get(d['nature'], '') if badge else ''
        return '<li><span class="d79-date">%s</span> %s%s%s</li>' % (
            e(_date_fr(_when(d)) or '—'), ('<span class="d79-kind d79-kind-%s">%s</span> ' % (e(d['nature']), e(label))) if label else '',
            _doc_link(prefix, mid, d, overview), (' <span class="ax-muted">— %s</span>' % e(who)) if who else '')
    left = ('<ul class="d79-list d79-docs">%s</ul>' % ''.join(li(d) for d in writings)) if writings else \
        '<p class="ax-muted">Aucune conclusion, assignation ni décision identifiée. Vérifiez le classement dans l’onglet Documents.</p>'
    right = ('<ul class="d79-list d79-docs">%s</ul>' % ''.join(li(d, False) for d in recent)) if recent else '<p class="ax-muted">Aucun document.</p>'
    more = '<a href="%s">Tous les documents (%d)</a>' % (e(prefix + _q(mid, 'documents'), quote=True), len(docs))
    return _card('Derniers documents', '<div class="d79-grid2"><div><h3>Écritures et décisions</h3>%s</div><div><h3>Ajoutés récemment</h3>%s</div></div>' % (left, right),
                 'd79-derniers', more)


def ai_work_html(desk, prefix, m):
    from .suivi5679 import matter_ai_activity
    items = matter_ai_activity(desk, m['id'], 6)
    body = ('<ul class="d79-list">%s</ul>' % ''.join('<li><span class="d79-date">%s</span> <strong>%s</strong>%s</li>' % (
        e(_date_fr(it['date'][:10])), e(it['libelle']), (' <span class="ax-muted">— %s</span>' % e(it['detail'])) if it['detail'] else '') for it in items)) \
        if items else '<p class="ax-muted">L’IA n’a pas encore travaillé sur ce dossier.</p>'
    return _card('Travail récent de l’IA', body, 'd79-ia', '<a href="%s">Travail</a>' % e(prefix + _q(m['id'], 'travail'), quote=True))


def todo_html(desk, prefix, m):
    from . import memoire5651, modele5651
    mid = m['id']
    gaps = memoire5651.gaps(desk, mid)
    unreadable = [g for g in gaps if g['kind'] == 'fichier_illisible']
    needed = [g for g in gaps if g['kind'] != 'fichier_illisible']
    proposed = len([a for a in modele5651.assertions(desk, mid, None, ('propose', 'a_verifier'), current_only=True)])
    mails = 0
    try:
        mails = desk.db.execute("SELECT COUNT(*) FROM work_items WHERE matter=? AND state IN ('needs_action','a_traiter','new')", (mid,)).fetchone()[0]
    except Exception:   # noqa: BLE001
        pass
    items = []
    if needed:
        items.append('<li><a href="%s"><strong>%d</strong> information(s) à fournir pour finaliser un travail</a></li>' % (
            e(prefix + _q(mid, 'travail') + '#d51-gaps', quote=True), len(needed)))
    if mails:
        items.append('<li><a href="%s"><strong>%d</strong> courriel(s) du dossier à traiter</a></li>' % (e(prefix + _q(mid, 'echanges'), quote=True), mails))
    if proposed:
        items.append('<li><a href="%s"><strong>%d</strong> information(s) extraite(s) à examiner</a></li>' % (
            e(prefix + _q(mid, 'informations'), quote=True), proposed))
    if unreadable:
        items.append('<li><a href="%s"><strong>%d</strong> fichier(s) illisible(s) (scans, fichiers protégés)</a> <span class="ax-muted">— facultatif</span></li>' % (
            e(prefix + _q(mid, 'documents', cat='illisibles'), quote=True), len(unreadable)))
    body = ('<ul class="d79-list">%s</ul>' % ''.join(items)) if items else '<p class="ax-muted">Rien en attente.</p>'
    return _card('À traiter', body, 'd79-todo')


def synthese_html(desk, auth, prefix, m, args):
    from .vue5651 import news_html
    owner = str(auth.get('owner') or 'cabinet')
    out = _safe('L’essentiel', essentials_html, desk, prefix, m)
    out += _safe('Derniers documents', latest_documents_html, desk, prefix, m)
    out += '<div class="d79-grid2">%s%s</div>' % (_safe('Travail de l’IA', ai_work_html, desk, prefix, m), _safe('À traiter', todo_html, desk, prefix, m))

    def decide():   # 5.6.51 : la file « À décider / À relire » propre au dossier
        from . import file5651, membres5650
        return file5651.html(desk, prefix, m['id'], str(args.get('file') or ''), membres5650.viewer_hidden() or frozenset(), 12)
    out += _safe('À décider', decide)
    news = _safe('Nouveautés', news_html, desk, prefix, m['id'], owner)
    out += '<details class="d79-fold"><summary>Depuis votre dernière lecture</summary>%s</details>' % news
    from . import dossier5627
    out += '<details class="d79-fold"><summary>Fiche de travail (résumé rédigé par l’IA, corrections du cabinet)</summary>%s</details>' % _safe(
        'Fiche de travail', dossier5627.fiche_html, desk, prefix, m)

    def access():
        from .i18n5631 import role as _role50
        from .web5650 import matter_card
        return matter_card(desk, auth, prefix, m['id'], _role50(), owner)
    out += '<details class="d79-fold"><summary>Discussion interne et accès au dossier</summary>%s</details>' % _safe('Discussion', access)
    return out


# ------------------------------------------------------------------------------------------------ Documents
def documents_html(desk, auth, prefix, m, args):
    from . import modele5651
    from .suivi5679 import digests_overview, doc_title
    from .common import fold as fold_for_search
    mid = m['id']
    docs = _documents(desk, mid)
    overview = digests_overview(desk, mid)
    cat = str(args.get('cat') or '')
    cat = cat if cat in dict(CATEGORIES) else ''
    query = str(args.get('q') or '').strip()[:120]
    sort = 'nom' if args.get('tri') == 'nom' else 'date'
    counts = {}
    for d in docs:
        d['_cat'] = category(d)
        counts[d['_cat']] = counts.get(d['_cat'], 0) + 1
    rows = [d for d in docs if not cat or d['_cat'] == cat]
    if query:
        fq = fold_for_search(query)
        rows = [d for d in rows if fq in fold_for_search(d['name'] + ' ' + ' '.join(doc_title(d, overview)))]
    rows.sort(key=(lambda d: doc_title(d, overview)[0].casefold()) if sort == 'nom' else (lambda d: _when(d)), reverse=sort == 'date')
    page = int(args.get('page')) if str(args.get('page') or '').isdigit() and int(args.get('page')) > 0 else 1
    total = len(rows)
    rows = rows[(page - 1) * PAGE: page * PAGE]
    chips = ''.join('<a href="%s"%s>%s <span>%d</span></a>' % (e(prefix + _q(mid, 'documents', cat=k, q=query, tri=sort if sort != 'date' else ''), quote=True),
                                                             ' aria-current="true"' if k == cat else '', e(v), len(docs) if not k else counts.get(k, 0))
                    for k, v in CATEGORIES if not k or counts.get(k))
    search = ('<form class="d79-search" method="get"><input type="hidden" name="id" value="%s"><input type="hidden" name="vue" value="documents">'
              '<input type="hidden" name="cat" value="%s"><label class="sr-only" for="d79-q">Rechercher un document</label>'
              '<input id="d79-q" name="q" value="%s" placeholder="Rechercher un document…" data-no-dictation><select name="tri" aria-label="Trier">'
              '<option value="date"%s>Plus récents d’abord</option><option value="nom"%s>Par titre</option></select><button class="ax-btn ghost">OK</button></form>') % (
        e(mid, quote=True), e(cat, quote=True), e(query, quote=True), ' selected' if sort == 'date' else '', ' selected' if sort == 'nom' else '')

    def row(d):
        reading = '' if d['reading'] in ('lisible', 'partielle') else '<span class="d79-read d79-read-%s">%s</span>' % (
            e(d['reading']), e(modele5651.READING.get(d['reading'], d['reading'])))
        extra = ' · '.join(x for x in (modele5651.NATURES.get(d['nature'], '') if d['nature'] != 'a_classer' else '',
                                        ('pièce n° ' + d['piece_number']) if d['piece_number'] else '',
                                        ('produite par ' + d['piece_party']) if d['piece_party'] and d['piece_party'] != 'cabinet' else '') if x)
        opts = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == d['nature'] else '', e(v)) for k, v in modele5651.NATURES.items())
        classify = ('<details class="d79-classify"><summary>Classer</summary><form class="m5-form" data-api="m5651/document/classer" data-reload>'
                    '<input type="hidden" name="matter" value="%s"><input type="hidden" name="path" value="%s"><input type="hidden" name="version" value="%d">'
                    '<label>Nature<select name="nature">%s</select></label><label>Pièce<select name="piece"><option value="">Inchangé</option>'
                    '<option value="oui">Oui</option><option value="non">Non</option></select></label><label>Produite par<input name="piece_party" value="%s" '
                    'maxlength="60" placeholder="cabinet, ou nom de l’adversaire"></label><label>N°<input name="piece_number" value="%s" maxlength="20" size="5"></label>'
                    '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
            e(mid, quote=True), e(d['path'], quote=True), d['version'], opts, e(d['piece_party'], quote=True), e(d['piece_number'], quote=True))
        return '<li class="d79-docrow"><span class="d79-date">%s</span><div>%s%s<div class="ax-muted">%s</div></div>%s</li>' % (
            e(_date_fr(_when(d)) or '—'), _doc_link(prefix, mid, d, overview), reading, e(extra), classify)
    body = '<nav class="d79-chips" aria-label="Catégories">%s</nav>%s<ul class="d79-doclist">%s</ul>' % (
        chips, search, ''.join(row(d) for d in rows) or '<li class="ax-muted">Aucun document.</li>')
    pages = (total + PAGE - 1) // PAGE
    if pages > 1:
        nav = []
        if page > 1:
            nav.append('<a href="%s">← Précédents</a>' % e(prefix + _q(mid, 'documents', cat=cat, q=query, page=page - 1), quote=True))
        nav.append('<span>Page %d / %d (%d documents)</span>' % (page, pages, total))
        if page < pages:
            nav.append('<a href="%s">Suivants →</a>' % e(prefix + _q(mid, 'documents', cat=cat, q=query, page=page + 1), quote=True))
        body += '<nav class="d51-pager">%s</nav>' % ' '.join(nav)
    if cat == 'illisibles':
        from .vue5651 import coverage_html
        body += _safe('Couverture', coverage_html, desk, prefix, mid)
    body += '<p class="ax-muted"><a href="%s">Vue détaillée des pièces, bordereaux et couverture de lecture</a></p>' % e(prefix + _q(mid, 'pieces-detail'), quote=True)
    return _card('Documents du dossier', body, 'd79-documents')


# ------------------------------------------------------------------------------------------------ Faits et procédure
def procedure_card(desk, prefix, m):
    from . import modele5651
    from .vue5651 import _source_links
    mid = m['id']
    procs = modele5651.procedures(desk, mid)
    status = {'active': 'en cours', 'historique': 'antérieure', 'citee': 'citée (autre affaire)'}
    items = []
    for p in procs:
        parts = ''.join('<li>%s <span class="ax-muted">— %s%s</span></li>' % (e(x['display_name']), e(x['role'] or 'qualité à préciser'),
                                                                          (' · ' + e(x['counsel'])) if x['counsel'] else '') for x in p['parties'])
        form = ('<details><summary>Corriger</summary><form class="m5-form" data-api="m5651/procedure" data-reload><input type="hidden" name="matter" value="%s">'
                '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                '<label>Juridiction<input name="juridiction" value="%s" maxlength="200"></label><label>Chambre<input name="chambre" value="%s" maxlength="200"></label>'
                '<label>RG<input name="rg" value="%s" maxlength="60"></label><label>Nature<input name="nature" value="%s" maxlength="200"></label>'
                '<label>Stade<input name="stade" value="%s" maxlength="200"></label><label>Statut<select name="statut">%s</select></label>'
                '<label class="d51-check"><input type="checkbox" name="reference"%s> Procédure de référence</label>'
                '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
            e(mid, quote=True), e(p['id'], quote=True), p['version'], e(p['juridiction'], quote=True), e(p['chambre'], quote=True), e(p['rg'], quote=True),
            e(p['nature'], quote=True), e(p['stade'], quote=True),
            ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == p['statut'] else '', e(v)) for k, v in
                    (('active', 'Active'), ('historique', 'Historique'), ('citee', 'Citée (autre affaire)'))), ' checked' if p['reference'] else '')
        items.append('<article class="d79-proc%s"><h3>%s%s%s</h3><p class="ax-muted">%s</p>%s<p class="d79-sources">%s</p>%s</article>' % (
            ' d79-proc-ref' if p['reference'] else '', e(p['juridiction'] or 'Juridiction à préciser'), (' · RG ' + e(p['rg'])) if p['rg'] else '',
            ' <span class="d27-badge">en cours</span>' if p['reference'] else ' <span class="d51-badge">%s</span>' % e(status.get(p['statut'], p['statut'])),
            e(' · '.join(x for x in (p['chambre'], p['nature'], p['stade']) if x)), ('<ul class="d79-list">%s</ul>' % parts) if parts else '',
            _source_links(prefix, mid, p['sources'], 3), form))
    add = ('<details><summary>Ajouter une procédure</summary><form class="m5-form" data-api="m5651/procedure" data-reload><input type="hidden" name="matter" value="%s">'
           '<label>Juridiction<input name="juridiction" maxlength="200"></label><label>RG<input name="rg" maxlength="60"></label>'
           '<label>Statut<select name="statut"><option value="active">Active</option><option value="historique">Historique</option></select></label>'
           '<label class="d51-check"><input type="checkbox" name="reference"> Procédure de référence</label>'
           '<button class="ax-btn ghost" type="submit">Ajouter</button><p role="status" class="m5-status"></p></form></details>') % e(mid, quote=True)
    body = ''.join(items) or ('<p class="ax-muted">Aucune procédure identifiée. AxiorHub lit la juridiction, le RG, les qualités et les avocats sur la '
                              'première page des conclusions, assignations et jugements : vérifiez leur classement dans l’onglet Documents, puis « ↻ Actualiser ».</p>')
    return _card('Procédure', body + add, 'd79-procedure', '<a href="%s">Détail des actes</a>' % e(prefix + _q(mid, 'procedure-detail'), quote=True))


def recital_html(desk, prefix, m):
    """Rappel des faits : chaque paragraphe renvoie aux pièces de ses informations (et non à leurs identifiants techniques)."""
    from . import modele5651
    from .vue5651 import narrative, _source_links
    mid = m['id']
    rec = narrative(desk, mid, 'faits')
    redact = ('<form class="m5-form d51-inline" data-api="m5651/rappel" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="kind" value="faits">'
              '<button class="ax-btn%s" type="submit">%s</button><p role="status" class="m5-status"></p></form>') % (
        e(mid, quote=True), ' ghost' if rec and not rec['stale'] else '', 'Rédiger le rappel des faits' if not rec else ('Actualiser le rappel' if rec['stale'] else 'Rédiger à nouveau'))
    if not rec:
        return _card('Rappel des faits', '<p class="ax-muted">Aucun rappel rédigé. L’IA le rédige à partir des informations relevées dans les pièces ; '
                                         'chaque paragraphe renverra à ses sources.</p>' + redact, 'd79-rappel')
    by_short = {}
    for a in modele5651.assertions(desk, mid, None, current_only=False):
        by_short[a['id'][:10]] = a
    paras = []
    for p in rec['paragraphs']:
        sources, seen = [], set()
        for ident in p.get('informations', []):
            a = by_short.get(str(ident)[:10])
            for s in (a or {}).get('sources') or []:
                key = s.get('path') or s.get('label')
                if key and key not in seen:
                    seen.add(key)
                    sources.append(s)
        paras.append('<p>%s %s</p>' % (e(p.get('texte', '')), _source_links(prefix, mid, sources, 3) if sources else ''))
    warn = '<p class="d51-warn">Des informations ont changé depuis cette rédaction : actualisez le rappel.</p>' if rec['stale'] else ''
    foot = '<p class="ax-muted">Version %d du %s, rédigée par l’IA : à relire.</p>' % (rec['version'], e(_date_fr(rec['created'][:10])))
    return _card('Rappel des faits', warn + '<div class="d79-recital">%s</div>' % ''.join(paras) + foot + redact, 'd79-rappel')


def faits_html(desk, auth, prefix, m, args):
    from .vue5651 import chronology_html, parties_html, decisions_html
    mid = m['id']
    out = _safe('Procédure', procedure_card, desk, prefix, m)
    out += _safe('Rappel des faits', recital_html, desk, prefix, m)
    out += _safe('Chronologie', chronology_html, desk, auth, prefix, mid, str(args.get('chrono') or 'tout'), 'faits')
    out += '<div class="d79-grid2">%s%s</div>' % (_safe('Parties', parties_html, desk, prefix, mid), _safe('Décisions du cabinet', decisions_html, desk, prefix, mid))
    out += '<p class="ax-muted"><a href="%s">Examiner une à une les informations extraites des pièces</a></p>' % e(prefix + _q(mid, 'informations'), quote=True)
    return out


# ------------------------------------------------------------------------------------------------ Échanges
def intervenants_html(desk, prefix, m):
    from .suivi5679 import intervenants
    from .desk import ROLES
    mid = m['id']
    people = intervenants(desk, m)
    roles = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in ROLES.items() if k != 'personnel')
    rows = []
    for p in people:
        action = ''
        if p['email'] and not p['confirme']:
            action = ('<form class="m5-form d51-inline" data-api="m5679/correspondant" data-reload><input type="hidden" name="matter" value="%s">'
                      '<input type="hidden" name="email" value="%s"><select name="role" aria-label="Rôle de %s">%s</select>'
                      '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form>') % (
                e(mid, quote=True), e(p['email'], quote=True), e(p['nom'], quote=True), roles)
        rows.append('<tr><td data-label="Nom"><strong>%s</strong>%s</td><td data-label="Rôle">%s</td><td data-label="Origine" class="ax-muted">%s</td><td>%s</td></tr>' % (
            e(p['nom']), ('<br><span class="ax-muted">%s</span>' % e(p['email'])) if p['email'] and p['email'] != p['nom'] else '', e(p['role']), e(p['origine']), action))
    body = ('<table class="d79-table"><thead><tr><th>Nom</th><th>Rôle</th><th>Origine</th><th></th></tr></thead><tbody>%s</tbody></table>' % ''.join(rows)) \
        if rows else '<p class="ax-muted">Aucun intervenant identifié.</p>'
    body += ('<p class="ax-muted">Les parties et avocats viennent de l’en-tête des actes ; les adresses, des courriels rattachés au dossier. Enregistrer une adresse '
             'rattache ses prochains courriels à ce dossier.</p>')
    return _card('Intervenants', body, 'd79-intervenants')


def echanges_html(desk, auth, prefix, m, args):
    from . import dossier5627
    from .vue5651 import courriels_html
    out = _safe('Intervenants', intervenants_html, desk, prefix, m)
    out += _safe('Courriels', courriels_html, desk, auth, prefix, m, args)
    out += '<details class="d79-fold"><summary>Correspondances conservées dans le dossier</summary>%s</details>' % _safe(
        'Correspondances', dossier5627.correspondances_html, desk, prefix, m, str(args.get('archive') or ''))
    try:
        from .crm5636 import matter_block
        out += matter_block(desk, prefix, m['id'])
    except Stop:
        pass
    except Exception:   # noqa: BLE001
        log.exception('dossier5679 : bloc CRM')
    return out


# ------------------------------------------------------------------------------------------------ Travail
def strategy_html(desk, prefix, m):
    from .strategic import latest_strategy, ensure_schema
    mid = m['id']
    ensure_schema(desk)
    form = ('<form class="m5-form" data-api="m5679/strategie" data-reload><input type="hidden" name="matter" value="%s">'
            '<label>Votre question<textarea name="question" rows="3" maxlength="3000" required placeholder="Ex. Faut-il proposer une transaction avant '
            'l’audience ? Quelles pièces nous manquent pour prouver le préjudice ?"></textarea></label>'
            '<button class="ax-btn" type="submit">Analyser</button><p role="status" class="m5-status"></p></form>') % e(mid, quote=True)
    pending = desk.db.execute("SELECT 1 FROM jobs WHERE kind='analyze_strategy' AND status IN ('pending','running') AND args LIKE ?",
                              ('%"matter": "' + mid.replace('%', '') + '"%',)).fetchone()
    out = '<p class="d51-running" role="status">⏳ Analyse en cours : le résultat s’affichera ici.</p>' if pending else ''
    a = latest_strategy(desk, mid)
    if a:
        from .fiche460 import doc_href
        data = a['data']
        paths = {s['id']: s.get('path') for s in a.get('sources') or [] if isinstance(s, dict)}

        def srcs(ids):
            links = []
            for i in ids or []:
                p = paths.get(i)
                if p:
                    links.append('<a class="d51-src" href="%s" data-d79-doc="%s">%s</a>' % (e(prefix + doc_href(p, mid), quote=True), e(p, quote=True),
                                                                                         e(PurePosixPath(p).name)))
            return ('<span class="d51-sources">%s</span>' % ' · '.join(links[:4])) if links else ''
        rec = data.get('recommended_approach') or {}
        body = '<h3>%s</h3><p>%s</p>' % (e(a['objective']), e(data.get('executive_summary', '')))
        if rec.get('summary'):
            body += '<div class="d79-reco"><strong>Orientation proposée</strong><p>%s</p>%s</div>' % (e(rec['summary']), srcs(rec.get('source_ids')))
        for o in data.get('options') or []:
            detail = ''.join('<h4>%s</h4><ul>%s</ul>' % (label, ''.join('<li>%s</li>' % e(x) for x in o.get(f) or []))
                             for f, label in (('strengths', 'Forces'), ('weaknesses', 'Faiblesses'), ('risks', 'Risques'),
                                              ('missing_evidence', 'Preuves manquantes'), ('next_steps', 'Étapes possibles')) if o.get(f))
            body += '<details class="d79-option"><summary><strong>%s</strong> — %s</summary>%s%s</details>' % (
                e(o.get('title', '')), e(o.get('description', ''))[:400], detail, srcs(o.get('source_ids')))
        qs = data.get('questions_for_lawyer') or []
        if qs:
            body += '<h4>Décisions attendues de vous</h4><ul>%s</ul>' % ''.join('<li>%s</li>' % e(x) for x in qs)
        if data.get('limits'):
            body += '<p class="ax-muted">Limites : %s</p>' % e(' ; '.join(data['limits']))
        out += '<div class="d79-strategy">%s<p class="ax-muted">Analyse n° %s du %s — interne, à vérifier.</p></div>' % (
            body, a['version'], e(_date_fr(str(a.get('created') or '')[:10])))
    return _card('Question stratégique', form + out, 'd79-strategie')


def letter_html(desk, prefix, m):
    from .courrier5679 import STATES, recent, template_ready
    from .cabinet_docs33 import APPEL, ENVOI
    from .suivi5679 import intervenants
    mid = m['id']
    tpl = template_ready(desk)
    if not tpl:
        return _card('Nouveau courrier', '<p>Importez d’abord le modèle Word du cabinet (en-tête, logo, signature) : <a href="%s">Paramètres › Modèle de '
                                         'courrier</a>. Il ne s’importe qu’une fois.</p>' % e(prefix + '/modeles-word', quote=True), 'd79-courrier')
    people = [p for p in intervenants(desk, m) if p['type'] in ('partie', 'avocat', 'courriel')]
    options = ''.join('<option value="%s">%s</option>' % (e(p['nom'], quote=True), e(p['role'])) for p in people[:40])
    form = ('<form class="m5-form d79-letter" data-api="m5679/courrier" data-reload><input type="hidden" name="matter" value="%s">'
            '<label>Destinataire<input name="destinataire" list="d79-dest" required maxlength="200" autocomplete="off"></label><datalist id="d79-dest">%s</datalist>'
            '<label>Qualité (facultatif)<input name="qualite" maxlength="200" placeholder="Ex. Avocat au barreau de Lyon"></label>'
            '<label>Adresse (facultatif)<input name="adresse" maxlength="280"></label>'
            '<label>Objet<input name="objet" required maxlength="300"></label>'
            '<label>Formule d’appel<select name="appel">%s</select></label><label>Envoi<select name="envoi">%s</select></label>'
            '<label class="d79-wide">Ce que le courrier doit dire<textarea name="consigne" rows="4" maxlength="4000" required placeholder="Ex. Accuser réception '
            'des conclusions adverses, indiquer que nous répondrons avant la clôture et solliciter la communication de la pièce 12."></textarea></label>'
            '<button class="ax-btn" type="submit">Rédiger le courrier</button><p role="status" class="m5-status"></p></form>') % (
        e(mid, quote=True), options, ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == 'mixte' else '', e(v)) for k, v in APPEL.items()),
        ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in ENVOI.items()))
    rows = []
    for c in recent(desk, mid, 6):
        link = ''
        if c['path']:
            from .fiche460 import doc_href
            link = ' <a class="d51-src" href="%s" data-d79-doc="%s">%s</a>' % (e(prefix + doc_href(c['path'], mid), quote=True), e(c['path'], quote=True),
                                                                              e(PurePosixPath(c['path']).name))
        rows.append('<li><span class="d79-date">%s</span> %s — <span class="ax-muted">%s%s</span>%s</li>' % (
            e(_date_fr(c['created'][:10])), e(c['data'].get('objet', '')), e(STATES.get(c['state'], c['state'])),
            (' : ' + e(c['error'])) if c['error'] else '', link))
    done = ('<h3>Courriers préparés</h3><ul class="d79-list">%s</ul>' % ''.join(rows)) if rows else ''
    return _card('Nouveau courrier', form + '<p class="ax-muted">L’IA rédige le corps ; le modèle « %s » ajoute en-tête, formules et signature. Le fichier Word '
                 'est enregistré dans le dossier comme brouillon : rien n’est envoyé.</p>' % e(tpl['label']) + done, 'd79-courrier')


def travail_html(desk, auth, prefix, m, args):
    from . import dossier5627, memoire5651
    from .missions5651 import card_html, missions_of
    from .vue5651 import review_html
    mid = m['id']
    owner = str(auth.get('owner') or 'cabinet')
    from .i18n5631 import role as _role
    admin = _role() == 'administrateur'
    out = '<div class="d79-grid2">%s%s</div>' % (_safe('Question stratégique', strategy_html, desk, prefix, m), _safe('Nouveau courrier', letter_html, desk, prefix, m))

    def missions():
        ms = missions_of(desk, mid, owner, admin, prefix)
        return _card('Missions', ''.join(card_html(x, prefix) for x in ms) or '<p class="ax-muted">Aucune mission pour ce dossier.</p>', 'd51-missions')
    out += _safe('Missions', missions)

    def gaps():
        rows = memoire5651.gaps(desk, mid)
        needed = [g for g in rows if g['kind'] != 'fichier_illisible']
        unreadable = len(rows) - len(needed)
        if not needed and not unreadable:
            return ''
        hidden = lambda g: '<input type="hidden" name="matter" value="%s"><input type="hidden" name="id" value="%s">' % (e(mid, quote=True), e(g['id'], quote=True))
        items = ''.join('<li><strong>%s</strong> <span class="ax-muted">%s</span><div><form class="m5-form d51-inline" data-api="m5651/lacune" data-reload>%s'
                        '<input name="reponse" maxlength="2000" placeholder="Information fournie" aria-label="Information fournie"><button class="ax-btn ghost" type="submit">'
                        'Fournir</button></form><form class="m5-form d51-inline" data-api="m5651/lacune" data-reload>%s<input type="hidden" name="abandonner" value="true">'
                        '<button class="ax-btn ghost" type="submit">Ignorer</button></form></div></li>' % (
                            e(g['expected']), e(memoire5651.BLOCKING.get(g['blocking'], '')), hidden(g), hidden(g)) for g in needed[:30])
        body = ('<ul class="d79-list">%s</ul>' % items) if items else ''
        if unreadable:
            body += '<p class="ax-muted">%d fichier(s) illisible(s) ne sont pas lus (scans sans texte, fichiers protégés) : <a href="%s">voir la liste</a>. ' \
                    'Une version lisible déposée dans le dossier sera lue automatiquement.</p>' % (unreadable, e(prefix + _q(mid, 'documents', cat='illisibles'), quote=True))
        return _card('Informations à fournir', body, 'd51-gaps')
    out += _safe('Informations à fournir', gaps)
    try:
        from .parcours5657 import matter_html as _p57
        prepared = _p57(desk, prefix, mid, owner)
        if prepared:
            out += ('<details class="d79-fold"><summary>Préparer une audience ou un rendez-vous, demander des pièces au client, point d’avancement, '
                    'diligences</summary>%s</details>' % prepared)
    except Stop:
        pass
    except Exception:   # noqa: BLE001
        log.exception('dossier5679 : parcours')
    tools = _safe('Relecture Word', review_html, desk, prefix, mid, str(args.get('relecture') or ''))
    for label, mod, fn in (('Honoraires', 'web5653', 'matter_block_html'), ('Signature et envois', 'connexions5652', 'matter_panel_html')):
        try:
            module = __import__('agent.' + mod, fromlist=[fn])
            tools += getattr(module, fn)(desk, prefix, mid, owner)
        except Stop:
            pass
        except Exception:   # noqa: BLE001
            log.exception('dossier5679 : %s', label)
    out += '<details class="d79-fold"%s><summary>Relecture Word, honoraires, signature et envois</summary>%s</details>' % (' open' if args.get('relecture') else '', tools)
    out += '<details class="d79-fold"><summary>Documents préparés et échanges avec l’agent</summary>%s</details>' % _safe(
        'Documents préparés', dossier5627.travaux_html, desk, prefix, m)
    return out


# ------------------------------------------------------------------------------------------------ page
def detail_html(desk, auth, prefix, m, args, view):
    from . import vue5651
    fn = {'informations': vue5651.faits_html, 'procedure-detail': vue5651.procedure_html, 'pieces-detail': vue5651.pieces_html}[view]
    back = '<p><a href="%s">← Retour</a></p>' % e(prefix + _q(m['id'], {'informations': 'faits', 'procedure-detail': 'faits', 'pieces-detail': 'documents'}[view]), quote=True)
    return back + _safe('Détail', fn, desk, auth, prefix, m, args)


def page(desk, auth, prefix, args, shell):
    from .dossier5627 import _matter
    from . import modele5651
    mid = str(args.get('id') or args.get('matter') or '')
    m = _matter(desk, mid)
    try:   # 5.6.57 (A10) : point de reprise
        from .reprise5657 import note as _note57
        _note57(desk, str(auth.get('owner') or 'cabinet'), '/dossier', {'id': m['id'], 'vue': str(args.get('vue') or '')})
    except Exception:   # noqa: BLE001
        pass
    view = str(args.get('vue') or '')
    view = ALIASES.get(view, view)
    if view not in dict(TABS) and view not in DETAIL_VIEWS:
        view = 'synthese'
    modele5651.ensure_schema(desk)
    if not modele5651.last_rebuild(desk, m['id']):
        try:   # première ouverture : reconstruction sans IA à partir des lectures existantes
            modele5651.rebuild(desk, m['id'])
        except Stop:
            pass
    body = header_html(desk, prefix, m, view if view in dict(TABS) else {'informations': 'faits', 'procedure-detail': 'faits', 'pieces-detail': 'documents'}[view])
    if view in DETAIL_VIEWS:
        content = detail_html(desk, auth, prefix, m, args, view)
    else:
        renderer = {'synthese': synthese_html, 'documents': documents_html, 'faits': faits_html, 'echanges': echanges_html, 'travail': travail_html}[view]
        content = renderer(desk, auth, prefix, m, args)
    body += '<div class="d79-layout"><div class="d79-main">%s</div>%s</div></div>' % (content, PREVIEW)
    head = '<script defer src="%s/static/v5627.js"></script><script defer src="%s/static/v5679.js"></script>' % (e(prefix), e(prefix))
    return shell(matter_display(m), body, prefix, auth['csrf'], '/dossiers', head)

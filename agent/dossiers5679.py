"""5.6.79 : page « Dossiers » refondue.

- en tête, trois cartes **« Travaillés récemment par l'IA »** : nom, client, procédure en cours, dernier travail de l'IA, prochaine date
  et dernier événement daté ;
- les états (actifs, en sommeil, à confirmer, archivés, tous) et une recherche ;
- un tableau **triable** (en-têtes cliquables, tri fait par le serveur, sans script) : dossier, numéro, état, dernière activité,
  travail de l'IA, correspondants, correspondant principal, textes indexés ;
- les outils du portefeuille (organisation automatique, indexation, création d'un dossier) repliés en bas de page.
"""
from html import escape as e
from urllib.parse import urlencode

from .common import Stop, matter_display

STATES = (('active', 'Actifs'), ('dormant', 'En sommeil'), ('to_confirm', 'À confirmer'), ('archived', 'Archivés'), ('all', 'Tous'))
STATE_LABELS = {'active': 'Actif', 'dormant': 'En sommeil', 'archived': 'Archivé', 'to_confirm': 'À confirmer', 'a_calculer': 'À calculer'}
COLUMNS = (('nom', 'Dossier'), ('numero', 'Numéro'), ('etat', 'État'), ('activite', 'Dernière activité'), ('ia', 'Travail de l’IA'),
           ('correspondants', 'Correspondants'), ('principal', 'Correspondant principal'), ('textes', 'Textes indexés'))
NUMERIC = ('correspondants', 'textes')
DATES = ('activite', 'ia')


def _date(value):
    from .vue5651 import _date_fr
    return _date_fr(str(value or '')[:10]) if value else ''


def _indexed_counts(c):
    from .index import DocumentIndex
    index = DocumentIndex(c['state_dir'])
    try:
        return {r[0]: r[1] for r in index.db.execute("SELECT matter,COUNT(*) FROM docs WHERE error='' AND text<>'' GROUP BY matter")}
    finally:
        try:
            index.db.close()
        except Exception:   # noqa: BLE001
            pass


def recent_cards(desk, prefix, by_id, ai, limit=3):
    """Cartes des dossiers sur lesquels l'IA a travaillé le plus récemment (dossiers réservés exclus : registre filtré)."""
    from . import modele5651, resume5659
    cards = []
    for mid, (when, label) in sorted(ai.items(), key=lambda kv: kv[1][0], reverse=True):
        m = by_id.get(mid)
        if not m:
            continue
        try:
            ref = modele5651.reference_procedure(desk, mid)
            sit = resume5659.situation(desk, mid)
            dates, _ = resume5659.dates(desk, mid)
        except Exception:   # noqa: BLE001 — une carte incomplète vaut mieux qu'une page en erreur
            ref, sit, dates = None, {'derniers_evenements': []}, []
        lines = []
        if ref and (ref['juridiction'] or ref['rg']):
            lines.append('<p class="d79c-proc">%s</p>' % e(' · '.join(x for x in (ref['juridiction'], ('RG ' + ref['rg']) if ref['rg'] else '') if x)))
        if dates:
            lines.append('<p><span class="d79c-k">Prochaine date</span> %s — %s</p>' % (e(_date(dates[0]['date'])), e(dates[0]['titre'][:120])))
        if sit['derniers_evenements']:
            ev = sit['derniers_evenements'][0]
            lines.append('<p><span class="d79c-k">Dernier événement</span> %s — %s</p>' % (e(_date(ev['date'])), e(ev['titre'][:160])))
        cards.append('<article class="d79c"><h3><a href="%s">%s</a></h3><p class="ax-muted">%s</p>%s<p class="d79c-ai"><span aria-hidden="true">✦</span> %s · %s</p></article>' % (
            e(prefix + '/dossier?' + urlencode({'id': mid}), quote=True), e(matter_display(m)), e(('client : ' + m['client_name']) if m.get('client_name') else mid),
            ''.join(lines) or '<p class="ax-muted">Synthèse en cours de constitution.</p>', e(label), e(_date(when))))
        if len(cards) >= limit:
            break
    if not cards:
        return ''
    return '<section class="d79-recent" aria-label="Travaillés récemment par l’IA"><h2>Travaillés récemment par l’IA</h2><div class="d79c-grid">%s</div></section>' % ''.join(cards)


def page_html(c, desk, matters, args, prefix, form, link):
    """5.6.97 (lot dossiers, § 9) : l'écran de départ du registre unique (recherche, + Nouveau dossier, filtres, tableau) passe en tête ;
    la vue du portefeuille de la 5.6.79 reste disponible, repliée en dessous."""
    from .membres5650 import current_author, viewer_role
    from . import dossiers_ui5697
    if args.get('volet') == 'portefeuille':
        # 5.6.100 (R05) : route de lecture du volet, chargée au dépli (``v56100-integrite.js``) ou ouverte telle quelle sans script.
        return ('<p><a href="%s">← Retour aux dossiers</a></p><section class="d97-ancien" aria-label="Portefeuille détaillé" data-c100-volet-contenu>%s</section>' % (
            e(prefix + '/dossiers', quote=True), _page_html5679(c, desk, matters, args, prefix, form, link)))
    env = {'HTTP_X_AXIORHUB_USER': current_author() or 'cabinet', 'HTTP_X_AXIORHUB_ROLE': viewer_role()}
    tete = dossiers_ui5697.ecran(desk, env, {}, prefix, args) + '<script defer src="%s/static/v5697-dossiers.js"></script>' % e(prefix, quote=True)
    titre = '<summary>Portefeuille détaillé : états, travail de l’IA, textes indexés, outils</summary>'
    if args.get('state') or args.get('tri') or args.get('mail_key'):   # demandé explicitement (filtre, tri, courriel à rattacher) : rendu ouvert
        return tete + '<details class="d97-ancien" open>%s%s</details>' % (titre, _page_html5679(c, desk, matters, args, prefix, form, link))
    # 5.6.100 (R05) : replié, le volet n'est ni calculé ni lu (aucune requête du portefeuille) ; il se charge à l'ouverture.
    adresse = prefix + '/dossiers?' + urlencode({'volet': 'portefeuille'})
    return tete + ('<details class="d97-ancien" data-c100-volet="%s">%s<div data-c100-volet-hote><p class="ax-muted" data-c100-volet-etat role="status">'
                   'Le portefeuille détaillé se charge à l’ouverture. <a href="%s">Ouvrir le portefeuille détaillé</a></p></div></details>'
                   '<script defer src="%s/static/v56100-integrite.js"></script>') % (
        e(adresse, quote=True), titre, e(adresse, quote=True), e(prefix, quote=True))


def _page_html5679(c, desk, matters, args, prefix, form, link):
    from .portfolio import portfolio_rows, portfolio_summary
    from .pause5629 import paused
    from .suivi5679 import correspondents_summary, last_ai_work
    by_id = {m['id']: m for m in matters}
    summary = portfolio_summary(desk)
    pc = summary['counts']
    query = str(args.get('q', ''))[:120]
    state = args.get('state', 'all' if query else 'active')
    state = state if state in dict(STATES) else 'active'
    sort = args.get('tri') if args.get('tri') in dict(COLUMNS) else 'activite'
    order = args.get('ordre') if args.get('ordre') in ('asc', 'desc') else ('asc' if sort in ('nom', 'numero', 'etat', 'principal') else 'desc')
    ai = last_ai_work(desk)
    out = '<header class="d79-listhead"><form class="d79-search" method="get"><input type="hidden" name="state" value="all">' \
          '<label class="sr-only" for="d79-lq">Rechercher un dossier</label><input id="d79-lq" name="q" value="%s" placeholder="Nom, client, référence, chemin…" ' \
          'data-no-dictation><button class="ax-btn">Rechercher</button></form></header>' % e(query, quote=True)
    if not query:
        out += recent_cards(desk, prefix, by_id, ai)
    counts = dict(pc)
    if summary.get('classification_pending'):   # 5.6.100 (R05) : calculé par le travail de fond, jamais pendant l'affichage
        out += '<p class="ax-muted" role="status">%d dossier%s à calculer : l’état sera calculé par le travail de fond.</p>' % (
            summary['classification_pending'], '' if summary['classification_pending'] == 1 else 's')
    out += '<nav class="d79-chips" aria-label="États">%s</nav>' % ''.join(
        '<a href="%s"%s>%s <span>%d</span></a>' % (e(prefix + '/dossiers?' + urlencode({'state': k, **({'q': query} if query else {})}), quote=True),
                                                  ' aria-current="true"' if k == state else '', e(v), sum(counts.values()) if k == 'all' else counts.get(k, 0))
        for k, v in STATES)
    from .common import fold
    listed = portfolio_rows(desk, None if state == 'all' else [state], limit=5000, refresh_missing=False)
    if state == 'all':   # 5.6.100 (R05) : un dossier pas encore classé est listé « À calculer », sans calcul ni écriture ici
        connus = {i['matter'] for i in listed}
        listed += [{'matter': m['id'], 'state': 'a_calculer', 'last_external_activity': ''} for m in matters if m['id'] not in connus]
    indexed = _indexed_counts(c)
    corr = correspondents_summary(desk, [by_id[i['matter']] for i in listed if i['matter'] in by_id])
    hidden = paused(desk)
    rows = []
    fq = fold(query)
    for item in listed:
        m = by_id.get(item['matter'])
        if not m or (fq and fq not in fold(' '.join((m['path'], m['id'], m.get('client_name', ''), ' '.join(m.get('references') or []))))):
            continue
        a = ai.get(m['id'])
        cs = corr.get(m['id'], {'nombre': 0, 'principal': ''})
        rows.append({'m': m, 'nom': matter_display(m), 'numero': m['id'], 'etat': item['state'], 'activite': item['last_external_activity'] or '',
                     'ia': a[0] if a else '', 'ia_label': a[1] if a else '', 'correspondants': cs['nombre'], 'principal': cs['principal'],
                     'textes': indexed.get(m['id'], 0), 'pause': m['id'] in hidden})
    keyf = (lambda r: r[sort]) if sort in NUMERIC else (lambda r: str(r[sort] or '').casefold())
    empties = [r for r in rows if not r[sort] and sort not in NUMERIC]
    rows = sorted((r for r in rows if r[sort] or sort in NUMERIC), key=keyf, reverse=order == 'desc') + empties   # sans valeur : toujours en fin

    def th(key, label):
        current = key == sort
        nxt = ('desc' if order == 'asc' else 'asc') if current else ('desc' if key in NUMERIC + DATES else 'asc')
        href = prefix + '/dossiers?' + urlencode({'state': state, 'tri': key, 'ordre': nxt, **({'q': query} if query else {})})
        arrow = (' ▲' if order == 'asc' else ' ▼') if current else ''
        return '<th scope="col"%s><a href="%s">%s%s</a></th>' % (' aria-sort="%s"' % ('ascending' if order == 'asc' else 'descending') if current else '',
                                                                e(href, quote=True), e(label), arrow)
    body = ''.join('<tr><td data-label="Dossier"><a href="%s">%s</a>%s</td><td data-label="Numéro">%s</td><td data-label="État"><span class="portfolio-state %s">%s</span></td>'
                   '<td data-label="Dernière activité">%s</td><td data-label="Travail de l’IA">%s</td><td data-label="Correspondants" class="d79-num">%d</td>'
                   '<td data-label="Correspondant principal">%s</td><td data-label="Textes indexés" class="d79-num">%d</td></tr>' % (
                       e(prefix + '/dossier?' + urlencode({'id': r['m']['id']}), quote=True), e(r['nom']),
                       ' <span class="d27-badge" title="Aucun travail automatique de l’IA">⏸ IA en pause</span>' if r['pause'] else '',
                       e(r['numero']), e(r['etat']), e(STATE_LABELS.get(r['etat'], r['etat'])), e(_date(r['activite']) or '—'),
                       e(('%s · %s' % (_date(r['ia']), r['ia_label'])) if r['ia'] else '—'), r['correspondants'], e(r['principal'] or '—'), r['textes'])
                   for r in rows)
    out += ('<div class="d79-tablewrap"><table class="d79-table d79-matters"><caption class="sr-only">Dossiers, triés par %s</caption><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>') % (e(dict(COLUMNS)[sort]), ''.join(th(k, v) for k, v in COLUMNS),
                                                  body or '<tr><td colspan="8" class="ax-muted">Aucun dossier.</td></tr>')
    roots = c['nextcloud'].get('matter_roots') or c['nextcloud']['roots']
    from .workspace_ui import directory_panel
    out += ('<details class="d79-fold"%s><summary>Portefeuille du cabinet : organisation, indexation, création d’un dossier</summary>'
            '<p>La découverte est limitée à <strong>%s</strong>. Une indexation technique ne rend jamais un ancien dossier actif.</p><div class="actions">%s%s%s</div>'
            '<h3>Créer ou enregistrer un dossier Nextcloud absent de la liste</h3>%s</details>') % (
        ' open' if args.get('mail_key') else '', e(', '.join(roots)), form('organize_cabinet', '✨ Organiser automatiquement mon cabinet', {'restart': 'yes'}),
        form('index_all', 'Indexer progressivement tous les dossiers enregistrés'), form('memory_all', 'Construire progressivement les mémoires des dossiers'),
        directory_panel(c, desk, matters, args, form, link))
    return out

"""5.6.51 (audit 5.6.50, Dossier-D03, D06, D09, D10, D11) : page Dossier de référence en six onglets.

Vue d'ensemble · Faits et arguments · Procédure · Pièces · Correspondances · Travaux. Un bandeau permanent donne le nom du dossier,
l'état réel de l'analyse (jamais lancée, repérage effectué, analyse partielle, synthèse à jour), la dernière actualisation utile et
l'action « Prendre en charge une mission ». La chronologie est un composant commun (Vie du litige / Procédure / les deux) alimenté par
un seul service ; le journal technique reste séparé et replié. Les listes longues sont paginées, jamais tronquées en silence.
Les anciennes adresses (``/dossier?vue=fiche``, ``vue=courriels``) restent servies ; ``/fiche`` et ``/matter`` restent accessibles par
leurs liens mais ne figurent plus dans la navigation ordinaire.
"""
from datetime import date, datetime, timezone
from html import escape as e
import json
from pathlib import PurePosixPath
from urllib.parse import urlencode

from .common import Stop, matter_display

TABS = (('apercu', 'Vue d’ensemble'), ('faits', 'Faits et arguments'), ('procedure', 'Procédure'), ('pieces', 'Pièces'),
        ('correspondances', 'Correspondances'), ('travaux', 'Travaux'))
ALIASES = {'fiche': 'apercu', '': 'apercu', 'courriels': 'correspondances'}
PAGE = 50
ANALYSIS = {'jamais': 'Analyse jamais lancée', 'reperage': 'Repérage effectué (sans lecture par l’IA)', 'partielle': 'Analyse partielle',
            'a_jour': 'Synthèse à jour'}
CHRONO_FILTERS = (('litige', 'Vie du litige'), ('procedure', 'Procédure'), ('tout', 'Les deux'))
PRECISION = {'jour': '', 'mois': ' (mois seulement)', 'annee': ' (année seulement)', 'inconnue': ''}


def _date_fr(value, precision='jour'):
    from .dossier5627 import _date_fr as fr
    if not value:
        return ''
    if precision == 'mois' and len(value) >= 7:
        months = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
        return '%s %s' % (months[int(value[5:7]) - 1], value[:4])
    if precision == 'annee':
        return value[:4]
    return fr(value[:10])


def _q(mid, view='', **extra):
    return '/dossier?' + urlencode({'id': mid, **({'vue': view} if view and view != 'apercu' else {}), **{k: v for k, v in extra.items() if v not in (None, '')}})


def _card(title, body, ident='', extra=''):
    return '<section class="d51-card"%s><h3>%s</h3>%s%s</section>' % (' id="%s"' % e(ident, quote=True) if ident else '', e(title), extra, body)


def _source_links(prefix, mid, sources, limit=3):
    from .fiche460 import doc_href
    out = []
    for s in (sources or [])[:limit]:
        if s.get('path'):
            label = PurePosixPath(s['path']).name + (' p. %s' % s['page'] if s.get('page') else '')
            href = prefix + doc_href(s['path'], mid) + ('#page=%s' % s['page'] if s.get('page') else '')
            title = (' title="%s"' % e(s['extrait'][:300], quote=True)) if s.get('extrait') else ''
            out.append('<a class="d51-src" href="%s"%s>%s</a>' % (e(href, quote=True), title, e(label)))
        elif s.get('label'):
            out.append('<span class="d51-src">%s</span>' % e(s['label']))
    more = ' <span class="ax-muted">+%d</span>' % (len(sources) - limit) if len(sources or []) > limit else ''
    return ('<span class="d51-sources">%s%s</span>' % (' · '.join(out), more)) if out else '<span class="d51-src ax-muted">sans source vérifiable</span>'


def _pager(prefix, mid, view, page, total, size=PAGE, **extra):
    pages = (total + size - 1) // size
    if pages <= 1:
        return ''
    links = []
    if page > 1:
        links.append('<a href="%s">← Précédents</a>' % e(prefix + _q(mid, view, page=page - 1, **extra), quote=True))
    links.append('<span>Page %d / %d (%d éléments)</span>' % (page, pages, total))
    if page < pages:
        links.append('<a href="%s">Suivants →</a>' % e(prefix + _q(mid, view, page=page + 1, **extra), quote=True))
    return '<nav class="d51-pager" aria-label="Pages">%s</nav>' % ' '.join(links)


# ------------------------------------------------------------------------------------------------ état de l'analyse (D03, D11)
def coverage(desk, mid):
    from . import modele5651
    docs = modele5651.documents(desk, mid)
    out = {'inventories': len(docs), 'classes': 0, 'lisibles': 0, 'partiels': 0, 'erreurs': 0, 'exclus': 0, 'a_analyser': 0, 'categories': {}}
    digested = set()
    try:
        digested = {r[0] for r in desk.db.execute('SELECT DISTINCT path FROM piece_digests5634 WHERE matter=?', (mid,))}
    except Exception:   # noqa: BLE001
        pass
    for d in docs:
        if d['nature'] != 'a_classer':
            out['classes'] += 1
        if d['reading'] == 'lisible':
            out['lisibles'] += 1
        elif d['reading'] == 'partielle':
            out['partiels'] += 1
        elif d['reading'] in ('erreur', 'protegee', 'non_prise_en_charge'):
            out['erreurs'] += 1
            cat = d['error_category'] or ('protege' if d['reading'] == 'protegee' else 'format' if d['reading'] == 'non_prise_en_charge' else 'autre')
            out['categories'][cat] = out['categories'].get(cat, 0) + 1
        elif d['reading'] == 'vide':
            out['exclus'] += 1
        if d['reading'] in ('lisible', 'partielle') and d['path'] not in digested and d['nature'] not in ('jurisprudence', 'note_interne', 'archive'):
            out['a_analyser'] += 1
    return out


def analysis_state(desk, mid):
    """État selon les opérations réellement accomplies ; jamais « à jour » si une pièce lisible n'a pas été lue ou si une lecture a échoué."""
    from . import dossier5627, modele5651
    fiche = dossier5627.latest(desk, mid)
    rebuilt = modele5651.last_rebuild(desk, mid)
    cov = coverage(desk, mid)
    pending = dossier5627.pending(desk, mid)
    if not fiche and not rebuilt and not cov['inventories']:
        state = 'jamais'
    elif not cov['inventories'] or (cov['lisibles'] + cov['partiels'] and cov['a_analyser'] >= cov['lisibles'] + cov['partiels']):
        state = 'reperage' if rebuilt or fiche else 'jamais'
    elif cov['a_analyser'] or cov['erreurs'] or cov['partiels'] or not fiche:
        state = 'partielle'
    else:
        newest = desk.db.execute("SELECT COALESCE(MAX(modified),'') FROM d51_documents WHERE matter=?", (mid,)).fetchone()[0]
        state = 'a_jour' if str(fiche.get('created') or '') >= str(newest or '') else 'partielle'
    return {'state': state, 'label': ANALYSIS[state], 'running': bool(pending), 'fiche': fiche, 'rebuilt': rebuilt, 'coverage': cov}


def banner_html(desk, prefix, m, st):
    from .missions5651 import panel_html
    from . import modele5651
    mid = m['id']
    when = (st['fiche'] or {}).get('created') or (st['rebuilt'] or {}).get('at') or ''
    files = [{'path': d['path'], 'name': d['name']} for d in modele5651.documents(desk, mid, limit=300)]
    running = ('<span class="d51-running" role="status">⏳ Analyse en cours — la dernière version reste consultable</span>' if st['running'] else '')
    return ('<div class="d51-banner" role="region" aria-label="État du dossier"><div class="d51-banner-state"><span class="d51-state d51-state-%s">%s</span>'
            '<span class="ax-muted">%s</span>%s</div><div class="d51-banner-actions">%s'
            '<form class="m5-form d51-inline" data-api="m5651/analyser" data-reload><input type="hidden" name="matter" value="%s">'
            '<button class="ax-btn ghost" type="submit" title="Reconstruit parties, documents, procédures et informations à partir des lectures déjà faites (sans IA)">'
            'Actualiser les données</button></form></div></div>') % (
        e(st['state']), e(st['label']), e(('Dernière actualisation utile : ' + _date_fr(when[:10])) if when else 'Aucune actualisation'), running,
        panel_html(mid, files, prefix), e(mid, quote=True))


def header_html(desk, prefix, m, view, auth):
    mid = m['id']
    from .pause5629 import is_paused
    on_pause = is_paused(desk, mid)
    pause_btn = ('<button type="button" class="ax-btn d27-resume" data-d27-pause="0" title="L’IA reprend le travail automatique sur ce dossier">▶ Reprendre l’IA</button>'
                 if on_pause else
                 '<button type="button" class="ax-btn ghost d27-pausebtn" data-d27-pause="1" title="Plus aucun travail automatique de l’IA sur ce dossier">⏸ Mettre l’IA en pause</button>')
    paused = ('<p class="d27-paused" role="status">⏸ <strong>IA en pause sur ce dossier.</strong> Aucun travail automatique (projets, brouillons de '
              'réponse, mises à jour) n’est lancé ; vos demandes restent possibles.</p>') if on_pause else ''
    more = ''.join('<li><a href="%s">%s</a></li>' % (e(prefix + href, quote=True), e(label)) for href, label in (
        ('/documents?' + urlencode({'matter': mid}), 'Tous les documents'), ('/strategy?' + urlencode({'matter': mid}), 'Stratégie'),
        ('/modeles-word?' + urlencode({'matter': mid}), 'Créer un courrier Word')))
    tabs = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + _q(mid, key), quote=True), ' aria-current="page"' if key == view else '', e(label)) for key, label in TABS)
    return ('<div class="d27 d51" data-d27-matter="%s"><nav class="d27-back"><a href="%s">← Tous les dossiers</a></nav>'
            '<header class="d27-head"><div class="d27-title"><h1>%s</h1><p class="d27-muted">Référence %s%s</p></div>'
            '<div class="d27-head-actions">%s<button type="button" class="ax-btn ghost" data-d27-prepare>Mettre à jour la fiche</button>'
            '<details class="d27-more"><summary class="ax-btn ghost">Plus ▾</summary><ul>%s</ul></details></div></header>%s%s'
            '<nav class="d27-tabs d51-tabs" aria-label="Vues du dossier">%s</nav>') % (
        e(mid, quote=True), e(prefix + '/dossiers', quote=True), e(matter_display(m)), e(mid), (' · client : ' + e(m['client_name'])) if m.get('client_name') else '',
        pause_btn, more, paused, banner_html(desk, prefix, m, analysis_state(desk, mid)), tabs)


# ------------------------------------------------------------------------------------------------ Vue d'ensemble
def parties_html(desk, prefix, mid):
    from . import modele5651
    rows = modele5651.parties(desk, mid)
    procs = {p['id']: p for p in modele5651.procedures(desk, mid)}
    ref = modele5651.reference_procedure(desk, mid)
    cards = []
    for p in rows:
        roles = [r for r in p['roles'] if r['role'] and (not ref or r['procedure_id'] == ref['id'])]
        aliases = [a for a in p['aliases'] if a['alias'] != p['display_name']]
        cards.append('<li class="d51-party%s"><div><strong>%s</strong>%s <span class="ax-muted">%s</span></div><div class="ax-muted">%s%s</div>'
                     '<details><summary>Mentions et sources (%d)</summary><ul>%s</ul>'
                     '<form class="m5-form d51-inline" data-api="m5651/partie" data-reload><input type="hidden" name="matter" value="%s">'
                     '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d"><label>Nom affiché<input name="display_name" value="%s" maxlength="200"></label>'
                     '<label>Conseil<input name="counsel" value="%s" maxlength="200"></label><label class="d51-check"><input type="checkbox" name="client"%s> Client du cabinet</label>'
                     '<button class="ax-btn ghost" type="submit">Corriger</button><p role="status" class="m5-status"></p></form></details></li>' % (
                         ' d51-client' if p['client'] else '', e(p['display_name']), ' <span class="d27-badge">client</span>' if p['client'] else '',
                         e({'personne': 'personne', 'societe': 'société', 'inconnu': ''}.get(p['kind'], '')),
                         e(', '.join(sorted({r['role'] for r in roles})) or 'qualité à préciser'), (' · conseil : ' + e(p['counsel'])) if p['counsel'] else '',
                         len(p['aliases']), ''.join('<li>%s <span class="ax-muted">— %s</span></li>' % (e(a['alias']), e(a['source'])) for a in p['aliases'][:20]),
                         e(mid, quote=True), e(p['id'], quote=True), p['version'], e(p['display_name'], quote=True), e(p['counsel'], quote=True),
                         ' checked' if p['client'] else ''))
    cands = modele5651.candidates(desk, mid)
    zone = ''
    if cands:
        zone = '<div class="d51-candidates" role="note"><h4>Rapprochements à confirmer (%d)</h4><ul>%s</ul></div>' % (len(cands), ''.join(
            '<li>« %s » et « %s » <span class="ax-muted">(%s)</span>'
            '<form class="m5-form d51-inline" data-api="m5651/partie/fusion" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="a" value="%s">'
            '<input type="hidden" name="b" value="%s"><button class="ax-btn ghost" type="submit">Même personne</button></form>'
            '<form class="m5-form d51-inline" data-api="m5651/partie/separer" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="a" value="%s">'
            '<input type="hidden" name="b" value="%s"><button class="ax-btn ghost" type="submit">Personnes distinctes</button></form></li>' % (
                e(c['a']['display_name']), e(c['b']['display_name']), e(c['reason']), e(mid, quote=True), e(c['a']['id'], quote=True), e(c['b']['id'], quote=True),
                e(mid, quote=True), e(c['a']['id'], quote=True), e(c['b']['id'], quote=True)) for c in cands))
    merges = desk.db.execute("SELECT d.id,a.display_name an,b.display_name bn FROM d51_party_decisions d JOIN d51_parties a ON a.id=d.a JOIN d51_parties b ON b.id=d.b "
                             "WHERE d.matter=? AND d.kind='fusion' AND d.reverted=0 ORDER BY d.id DESC LIMIT 10", (mid,)).fetchall()
    undo = ''.join('<li>« %s » rattachée à « %s »<form class="m5-form d51-inline" data-api="m5651/partie/annuler-fusion" data-reload>'
                   '<input type="hidden" name="matter" value="%s"><input type="hidden" name="decision" value="%d"><button class="ax-btn ghost" type="submit">Annuler</button></form></li>'
                   % (e(r['bn']), e(r['an']), e(mid, quote=True), r['id']) for r in merges)
    add = ('<details><summary>Ajouter une partie</summary><form class="m5-form" data-api="m5651/partie/ajouter" data-reload><input type="hidden" name="matter" value="%s">'
           '<label>Nom<input name="nom" required maxlength="200"></label><label class="d51-check"><input type="checkbox" name="client"> Client du cabinet</label>'
           '<button class="ax-btn ghost" type="submit">Ajouter</button><p role="status" class="m5-status"></p></form></details>') % e(mid, quote=True)
    body = ('<ul class="d51-parties">%s</ul>' % ''.join(cards)) if cards else '<p class="ax-muted">Aucune partie identifiée. Lancez « Actualiser les données » après la lecture des pièces.</p>'
    return _card('Parties', body + zone + (('<details><summary>Fusions enregistrées (%d)</summary><ul>%s</ul></details>' % (len(merges), undo)) if merges else '') + add, 'd51-parties')


def decisions_html(desk, prefix, mid):
    from . import memoire5651
    rows = memoire5651.decisions(desk, mid)
    by_kind = {}
    for d in rows:
        by_kind.setdefault(d['kind'], []).append(d)
    items = []
    for kind, label in memoire5651.DECISION_KINDS.items():
        for d in by_kind.get(kind, []):
            items.append('<li class="d51-decision"><span class="d51-kind">%s</span> %s%s <span class="ax-muted">— %s, %s, v%d</span>'
                         '<details><summary>Modifier</summary><form class="m5-form" data-api="m5651/decision-cabinet" data-reload><input type="hidden" name="matter" value="%s">'
                         '<input type="hidden" name="remplace" value="%s"><input type="hidden" name="version" value="%d"><input type="hidden" name="kind" value="%s">'
                         '<label>Texte<textarea name="texte" rows="2" maxlength="2000">%s</textarea></label><label>Motif<input name="motif" value="%s" maxlength="1000"></label>'
                         '<button class="ax-btn ghost" type="submit">Enregistrer la nouvelle version</button></form>'
                         '<form class="m5-form d51-inline" data-api="m5651/decision-cabinet/retirer" data-reload><input type="hidden" name="matter" value="%s">'
                         '<input type="hidden" name="id" value="%s"><button class="ax-btn ghost" type="submit">Retirer</button></form></details></li>' % (
                             e(label), e(d['text']), (' <em>(motif : %s)</em>' % e(d['motif'])) if d['motif'] else '', e(d['author']), e(_date_fr(d['created'][:10])),
                             d['version'], e(mid, quote=True), e(d['id'], quote=True), d['version'], e(kind, quote=True), e(d['text']), e(d['motif'], quote=True),
                             e(mid, quote=True), e(d['id'], quote=True)))
    conflicts = memoire5651.conflicts(desk, mid)
    warn = ('<p class="d51-warn" role="alert">Consignes à concilier : %s</p>' % e(' / '.join(' ↔ '.join(d['text'][:60] for d in g) for g in conflicts))) if conflicts else ''
    kinds = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in memoire5651.DECISION_KINDS.items())
    form = ('<details><summary>Ajouter un objectif, une consigne ou une décision</summary><form class="m5-form" data-api="m5651/decision-cabinet" data-reload>'
            '<input type="hidden" name="matter" value="%s"><label>Type<select name="kind">%s</select></label><label>Objet (facultatif)<input name="objet" maxlength="200" '
            'placeholder="Ex. clause pénale"></label><label>Texte<textarea name="texte" rows="2" maxlength="2000" required></textarea></label>'
            '<label>Motif (facultatif)<input name="motif" maxlength="1000"></label><button class="ax-btn" type="submit">Enregistrer</button>'
            '<p role="status" class="m5-status"></p></form></details>') % (e(mid, quote=True), kinds)
    body = ('<ul class="d51-list">%s</ul>' % ''.join(items)) if items else '<p class="ax-muted">Aucun objectif ni consigne enregistré pour ce dossier.</p>'
    return _card('Nos objectifs et consignes · Décisions du cabinet', warn + body + form +
                 '<p class="ax-muted">Propres à ce dossier ; distinctes des décisions de justice. Utilisées par chaque rédaction ; un argument abandonné n’est plus proposé.</p>',
                 'd51-decisions')


def news_html(desk, prefix, mid, owner):
    from . import memoire5651
    data = memoire5651.since_last_read(desk, mid, owner)
    groups = data['groups']
    if not groups:
        return _card('Depuis votre dernière lecture', '<p class="ax-muted">Rien de nouveau qui change le travail.</p>', 'd51-news')
    body = ''.join('<details%s><summary>%s — %s</summary><ul>%s</ul></details>' % (
        ' open' if i == 0 else '', e(_date_fr(g['to'][:10])), e(g['summary']), ''.join(
            '<li>%s%s</li>' % (e(c['title']), _change_detail(c)) for c in g['items'][:30])) for i, g in enumerate(groups[:6]))
    mark = ('<form class="m5-form d51-inline" data-api="m5651/lu" data-reload><input type="hidden" name="matter" value="%s">'
            '<button class="ax-btn ghost" type="submit">Marquer comme lu</button></form>') % e(mid, quote=True)
    return _card('Depuis votre dernière lecture', body + mark, 'd51-news')


def _change_detail(c):
    d = c.get('detail') or {}
    parts = []
    if d.get('ancien') or d.get('nouveau'):
        parts.append('<div class="d51-diff"><del>%s</del> <ins>%s</ins></div>' % (e(str(d.get('ancien', ''))[:300]), e(str(d.get('nouveau', ''))[:300])))
    if d.get('source'):
        parts.append('<span class="ax-muted">Source : %s</span>' % e(d['source']))
    if d.get('apports'):   # 5.6.59 (A02) : bilan d'une pièce nouvellement lue
        parts.append('<ul class="r59-apports">%s</ul>' % ''.join('<li>%s : %s</li>' % (e(str(a.get('type', ''))), e(str(a.get('texte', ''))[:200]))
                                                                 for a in d['apports'][:12]))
    for p in (d.get('projets') or [])[:3]:
        parts.append('<div class="ax-muted">Projet %s : %s</div>' % (e(p['travail'][:8]), e(' / '.join('§%d' % x['paragraphe'] for x in p['passages'][:6]))))
    return ''.join(parts)


def situation_html(desk, prefix, m, st):
    from . import modele5651
    mid = m['id']
    ref = modele5651.reference_procedure(desk, mid)
    from .dossier5627 import _next_deadline
    nd = _next_deadline(desk, mid)
    cov = st['coverage']
    rows = [('Procédure de référence', ('%s%s%s' % (ref['juridiction'] or 'juridiction à préciser', (' · RG ' + ref['rg']) if ref['rg'] else '',
                                                  (' · ' + ref['stade']) if ref['stade'] else '')) if ref else 'Aucune procédure identifiée'),
            ('Prochaine échéance', ('%s — %s' % (_date_fr(str(nd.get('due') or nd.get('date') or '')[:10]), nd.get('label') or nd.get('title') or '')) if isinstance(nd, dict) and nd else 'Aucune'),
            ('Couverture documentaire', '%d document(s) · %d lisible(s) · %d partiel(s) · %d en erreur · %d à analyser' % (
                cov['inventories'], cov['lisibles'], cov['partiels'], cov['erreurs'], cov['a_analyser']))]
    body = '<dl class="d51-dl">%s</dl>' % ''.join('<dt>%s</dt><dd>%s</dd>' % (e(k), e(v)) for k, v in rows)
    if cov['erreurs'] or cov['a_analyser']:
        body += '<p class="d51-warn">Couverture partielle : aucune conclusion du type « aucun document ne mentionne… » n’est possible. <a href="%s">Voir le détail</a></p>' % e(
            prefix + _q(mid, 'pieces') + '#d51-coverage', quote=True)
    return _card('Situation actuelle', body, 'd51-situation')


def apercu_html(desk, auth, prefix, m, args):
    from . import dossier5627, file5651, membres5650
    mid = m['id']
    owner = str(auth.get('owner') or 'cabinet')
    st = analysis_state(desk, mid)
    from . import resume5659   # 5.6.59 (A02) : le dossier en bref, chaque élément avec sa source
    out = resume5659.html(desk, prefix, m)
    out += '<div class="d51-grid">'
    out += '<div class="d51-col">' + situation_html(desk, prefix, m, st) + parties_html(desk, prefix, mid) + decisions_html(desk, prefix, mid) + '</div>'
    out += ('<div class="d51-col">' + news_html(desk, prefix, mid, owner) +
            file5651.html(desk, prefix, mid, str(args.get('file') or ''), membres5650.viewer_hidden() or frozenset(), 12) + '</div>')
    out += '</div>'
    out += '<section class="d51-legacy" aria-label="Fiche de travail">' + dossier5627.fiche_html(desk, prefix, m) + '</section>'
    return out


# ------------------------------------------------------------------------------------------------ Faits et arguments (D06, D07)
def narrative(desk, mid, kind='faits'):
    """Rappel rédigé le plus récent (D06) ; « à actualiser » si les informations ont changé depuis."""
    _schema(desk)
    row = desk.db.execute('SELECT * FROM d51_narratives WHERE matter=? AND kind=? ORDER BY version DESC LIMIT 1', (mid, kind)).fetchone()
    if not row:
        return None
    out = dict(row)
    out['paragraphs'] = json.loads(row['paragraphs'] or '[]')
    from .recit5651 import basis
    out['stale'] = row['basis'] != basis(desk, mid, kind)
    return out


def _schema(desk):
    desk.db.executescript('''CREATE TABLE IF NOT EXISTS d51_narratives(matter TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL, paragraphs TEXT NOT NULL,
      basis TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, PRIMARY KEY(matter, kind, version));''')


def _basis(desk, mid, kind):
    table = 'd51_assertions' if kind == 'faits' else 'd51_events'
    row = desk.db.execute('SELECT COUNT(*),COALESCE(MAX(updated),\'\') FROM %s WHERE matter=?' % table, (mid,)).fetchone()
    return '%s:%s' % (row[0], row[1])


def structured_recital(desk, mid):
    """Rappel structuré sans modèle : faits datés dans l'ordre, contexte sans date, positions attribuées, points contestés."""
    from . import modele5651
    infos = [a for a in modele5651.assertions(desk, mid, 'fait', current_only=True) if a['review'] in ('enregistre', 'propose', 'a_verifier')
             and a['relevance'] != 'hors_sujet']
    names = {p['id']: p['display_name'] for p in modele5651.parties(desk, mid, include_merged=True)}
    groups = {'dates': [], 'sans_date': [], 'contestes': []}
    for a in infos:
        who = names.get(a['author']) or (a['author_label'] if a['author'] != 'document' else '')
        a['who'] = who
        if a['proof'] == 'conteste':
            groups['contestes'].append(a)
        elif a['date_value']:
            groups['dates'].append(a)
        else:
            groups['sans_date'].append(a)
    return groups


def _info_line(prefix, mid, a, actions=True):
    from . import modele5651
    date_txt = _date_fr(a['date_value'], a['date_precision']) if a['date_value'] else 'date non déterminée'
    who = (' — <span class="d51-who">selon %s</span>' % e(a['who'])) if a.get('who') else ''
    badge = '<span class="d51-badge d51-review-%s">%s</span><span class="d51-badge d51-proof">%s</span>' % (
        e(a['review']), e(modele5651.REVIEW.get(a['review'], a['review'])), e(modele5651.PROOF.get(a['proof'], a['proof'])))
    if a.get('validity') in ('historique', 'a_recontroler'):
        badge += '<span class="d51-badge d51-review-a_verifier" title="%s">%s</span>' % (e(a.get('validity_note', ''), quote=True),
                                                                                       e(modele5651.VALIDITY[a['validity']]))
    form = ''
    if actions:
        proofs = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == a['proof'] else '', e(v)) for k, v in modele5651.PROOF.items())
        form = ('<details class="d51-act"><summary>Décider</summary><form class="m5-form" data-api="m5651/information" data-reload>'
                '<input type="hidden" name="matter" value="%s"><input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                '<label>Décision<select name="action"><option value="enregistrer">Enregistrer cette information</option><option value="corriger">Corriger</option>'
                '<option value="hors_sujet">Hors sujet pour ce dossier</option><option value="a_verifier">À vérifier</option><option value="refuser">Refuser</option></select></label>'
                '<label>Qualification<select name="proof">%s</select></label><label>Texte corrigé<textarea name="texte" rows="2" maxlength="2000">%s</textarea></label>'
                '<label>Date (si connue)<input name="date" value="%s" maxlength="40" placeholder="JJ/MM/AAAA, mois ou année"></label>'
                '<label>Motif<input name="motif" maxlength="500"></label>'
                '<button class="ax-btn ghost" type="submit">Valider ce choix</button><p role="status" class="m5-status"></p></form></details>') % (
            e(mid, quote=True), e(a['id'], quote=True), a['version'], proofs, e(a['text']), e(a['date_value'], quote=True))
    return '<li class="%s"><span class="d51-date">%s</span> %s%s %s %s%s</li>' % (
        'd51-info' if actions else 'd51-info-r', e(date_txt), e(a['text']), who, badge, _source_links(prefix, mid, a['sources']), form)


def faits_html(desk, auth, prefix, m, args):
    from . import modele5651
    mid = m['id']
    rec = narrative(desk, mid, 'faits')
    if rec:
        written = ''.join('<p>%s %s</p>' % (e(p.get('texte', '')), ' '.join('<sup class="d51-ref">%s</sup>' % e(str(x)[:8]) for x in p.get('informations', [])[:4]))
                          for p in rec['paragraphs'])
        written = ('<p class="d51-warn">Informations modifiées depuis cette rédaction : à actualiser.</p>' if rec['stale'] else '') + written + \
            '<p class="ax-muted">Version %d du %s — à relire ; chaque paragraphe renvoie aux informations utilisées.</p>' % (rec['version'], e(_date_fr(rec['created'][:10])))
    else:
        written = '<p class="ax-muted">Aucun rappel rédigé. Le rappel structuré ci-dessous est établi sans IA, à partir des informations du dossier.</p>'
    redact = ('<form class="m5-form d51-inline" data-api="m5651/rappel" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="kind" value="faits">'
              '<button class="ax-btn ghost" type="submit">Rédiger le rappel des faits</button><p role="status" class="m5-status"></p></form>') % e(mid, quote=True)
    g = structured_recital(desk, mid)
    struct = ''
    if g['dates']:
        struct += '<h4>Événements datés</h4><ul class="d51-list">%s</ul>' % ''.join(_info_line(prefix, mid, a, False) for a in g['dates'][:80])
    if g['sans_date']:
        struct += '<h4>Contexte / date non déterminée</h4><ul class="d51-list">%s</ul>' % ''.join(_info_line(prefix, mid, a, False) for a in g['sans_date'][:80])
    if g['contestes']:
        struct += '<h4>Points contestés</h4><ul class="d51-list">%s</ul>' % ''.join(_info_line(prefix, mid, a, False) for a in g['contestes'][:40])
    out = _card('Rappel des faits', written + redact + ('<details><summary>Rappel structuré (sans IA)</summary>%s</details>' % struct if struct else ''), 'd51-rappel')
    # informations à examiner, paginées et filtrées
    kind = str(args.get('type') or '')
    review_filter = str(args.get('etat') or '')
    page = max(1, int(args.get('page') or 1)) if str(args.get('page') or '1').isdigit() else 1
    reviews = (review_filter,) if review_filter in modele5651.REVIEW else ('propose', 'a_verifier', 'enregistre')
    if review_filter == 'historique':   # 5.6.52 (F03) : informations dont la source a été remplacée ou retirée
        rows = [a for a in modele5651.assertions(desk, mid, kind if kind in modele5651.KINDS else None) if a['validity'] == 'historique']
    else:
        rows = modele5651.assertions(desk, mid, kind if kind in modele5651.KINDS else None, reviews, current_only=True)
    names = {p['id']: p['display_name'] for p in modele5651.parties(desk, mid, include_merged=True)}
    for a in rows:
        a['who'] = names.get(a['author']) or (a['author_label'] if a['author'] != 'document' else '')
    total = len(rows)
    rows = rows[(page - 1) * PAGE: page * PAGE]
    filters = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + _q(mid, 'faits', type=k, etat=review_filter), quote=True), ' aria-current="true"' if k == kind else '', e(v))
                      for k, v in (('', 'Tout'),) + tuple(modele5651.KINDS.items()))
    states = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + _q(mid, 'faits', type=kind, etat=k), quote=True), ' aria-current="true"' if k == review_filter else '', e(v))
                     for k, v in (('', 'À utiliser'), ('propose', 'Proposées'), ('a_verifier', 'À vérifier'), ('enregistre', 'Enregistrées'), ('hors_sujet', 'Hors sujet'),
                                  ('refuse', 'Refusées'), ('historique', 'Historique')))
    add = ('<details><summary>Ajouter une information</summary><form class="m5-form" data-api="m5651/information/ajouter" data-reload><input type="hidden" name="matter" value="%s">'
           '<label>Type<select name="kind">%s</select></label><label>Texte<textarea name="texte" rows="2" maxlength="2000" required></textarea></label>'
           '<label>Date (si connue)<input name="date" maxlength="40"></label><label>Qualification<select name="proof">%s</select></label>'
           '<button class="ax-btn ghost" type="submit">Ajouter</button><p role="status" class="m5-status"></p></form></details>') % (
        e(mid, quote=True), ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in modele5651.KINDS.items()),
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == 'a_verifier' else '', e(v)) for k, v in modele5651.PROOF.items()))
    body = ('<nav class="d51-filters" aria-label="Type">%s</nav><nav class="d51-filters" aria-label="État">%s</nav><ul class="d51-list">%s</ul>%s%s' % (
        filters, states, ''.join(_info_line(prefix, mid, a) for a in rows) or '<li class="ax-muted">Aucune information.</li>',
        _pager(prefix, mid, 'faits', page, total, type=kind, etat=review_filter), add))
    out += _card('Faits, demandes et arguments', body +
                 '<p class="ax-muted">« Enregistrer » confirme l’extraction et la rend utilisable avec son attribution ; la qualification probatoire est un choix distinct. '
                 '« Hors sujet » n’est pas « faux ». Un refus est conservé après une nouvelle analyse.</p>', 'd51-infos')
    out += chronology_html(desk, auth, prefix, mid, str(args.get('chrono') or 'litige'), 'faits')
    return out


# ------------------------------------------------------------------------------------------------ chronologie commune (D10)
def timeline(desk, mid, which='tout'):
    """Un seul service : Vie du litige (faits datés) et Procédure (actes, décisions, échéances confirmées). Événements sans date à part."""
    from . import modele5651
    items = []
    names = {p['id']: p['display_name'] for p in modele5651.parties(desk, mid, include_merged=True)}
    if which in ('litige', 'tout'):
        for a in modele5651.assertions(desk, mid, 'fait', ('enregistre', 'propose', 'a_verifier'), current_only=True):
            if a['relevance'] == 'hors_sujet':
                continue
            items.append({'date': a['date_value'], 'precision': a['date_precision'], 'kind': 'litige', 'nature': 'Fait', 'text': a['text'],
                          'actor': names.get(a['author']) or (a['author_label'] if a['author'] != 'document' else ''), 'sources': a['sources'],
                          'review': modele5651.REVIEW.get(a['review'], ''), 'proof': modele5651.PROOF.get(a['proof'], '')})
    if which in ('procedure', 'tout'):
        for ev in modele5651.events(desk, mid):
            dates = ev['dates'] or {}
            main = dates.get('signification') or dates.get('depot') or dates.get('evenement') or dates.get('acte') or ''
            other = ', '.join('%s : %s' % (k, _date_fr(v)) for k, v in dates.items() if v and v != main)
            items.append({'date': main, 'precision': ev['date_precision'], 'kind': 'procedure', 'nature': ev['nature'].replace('_', ' ').capitalize(),
                          'text': ev['title'] + ((' (' + other + ')') if other else ''), 'actor': names.get(ev['author'], ''), 'sources': ev['sources'],
                          'review': modele5651.REVIEW.get(ev['review'], ''), 'proof': ev['status']})
        for j in modele5651.judgments(desk, mid):
            items.append({'date': j['date'] if len(j['date'] or '') == 10 and j['date'][4] == '-' else '', 'precision': 'jour', 'kind': 'procedure',
                          'nature': 'Décision', 'text': ('%s — original examiné' if j['examined'] else '%s — décision mentionnée, original non examiné') % (j['nature'] or 'décision'),
                          'actor': j['juridiction'], 'sources': j['sources'], 'review': '', 'proof': ''})
        try:
            for r in desk.db.execute("SELECT due,rule_id,status FROM deadlines450 WHERE matter=? AND status IN ('confirmee','manuel') AND due<>''", (mid,)):
                items.append({'date': r['due'][:10], 'precision': 'jour', 'kind': 'procedure', 'nature': 'Échéance', 'text': 'Échéance confirmée (%s)' % r['rule_id'],
                              'actor': '', 'sources': [], 'review': '', 'proof': ''})
        except Exception:   # noqa: BLE001
            pass
    return items


def chronology_html(desk, auth, prefix, mid, which, view):
    owner = str(auth.get('owner') or 'cabinet')
    pref = desk.settings('chronologie5651:' + owner, {}) or {}
    order = pref.get('ordre') if pref.get('ordre') in ('ancien', 'recent') else 'ancien'
    which = which if which in dict(CHRONO_FILTERS) else ('procedure' if view == 'procedure' else 'litige')
    items = timeline(desk, mid, which)
    dated = sorted((i for i in items if i['date']), key=lambda i: i['date'], reverse=order == 'recent')
    undated = [i for i in items if not i['date']]

    def li(i):
        return '<li class="d51-ev d51-ev-%s"><span class="d51-date">%s</span> <span class="d51-kind">%s</span> %s%s %s</li>' % (
            e(i['kind']), e(_date_fr(i['date'], i['precision']) + PRECISION.get(i['precision'], '') if i['date'] else 'date non déterminée'), e(i['nature']), e(i['text']),
            (' — <span class="d51-who">%s</span>' % e(i['actor'])) if i['actor'] else '', _source_links(prefix, mid, i['sources'], 2))
    filters = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + _q(mid, view, chrono=k) + '#d51-chrono', quote=True), ' aria-current="true"' if k == which else '', e(v))
                      for k, v in CHRONO_FILTERS)
    toggle = ('<form class="m5-form d51-inline" data-api="m5651/preference" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="ordre" value="%s">'
              '<button class="ax-btn ghost" type="submit">%s</button></form>') % (e(mid, quote=True), 'recent' if order == 'ancien' else 'ancien',
                                                                                 'Ordre : ancien → récent (inverser)' if order == 'ancien' else 'Ordre : récent → ancien (inverser)')
    from . import memoire5651
    tech = memoire5651.changes(desk, mid, 0, True, 40)
    journal = ('<details class="d51-tech"><summary>Journal technique (%d)</summary><ul>%s</ul></details>' % (
        len(tech), ''.join('<li>%s — %s</li>' % (e(t['created'][:16].replace('T', ' ')), e(t['title'])) for t in tech))) if tech else ''
    body = ('<nav class="d51-filters" aria-label="Chronologie">%s</nav>%s<ol class="d51-chrono">%s</ol>%s%s' % (
        filters, toggle, ''.join(li(i) for i in dated[:300]) or '<li class="ax-muted">Aucun événement daté.</li>',
        ('<h4>Date non déterminée</h4><ul class="d51-chrono">%s</ul>' % ''.join(li(i) for i in undated[:200])) if undated else '', journal))
    if len(dated) > 300:
        body += '<p class="ax-muted">%d événements datés : les 300 premiers sont affichés ; la chronologie complète reste exportable.</p>' % len(dated)
    return _card('Chronologie', body, 'd51-chrono')


# ------------------------------------------------------------------------------------------------ Procédure (D09)
def procedure_html(desk, auth, prefix, m, args):
    from . import modele5651
    from .missions5651 import preparations_html
    mid = m['id']
    procs = modele5651.procedures(desk, mid)
    status = {'active': 'Active', 'historique': 'Historique', 'citee': 'Citée (autre affaire)'}
    cards = []
    for p in procs:
        parts = ', '.join('%s (%s)' % (x['display_name'], x['role']) for x in p['parties'] if x['role'])
        cards.append('<li class="d51-proc%s"><strong>%s</strong>%s <span class="d51-badge">%s</span>%s<div class="ax-muted">%s%s</div>'
                     '<details><summary>Corriger</summary><form class="m5-form" data-api="m5651/procedure" data-reload><input type="hidden" name="matter" value="%s">'
                     '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d">'
                     '<label>Juridiction<input name="juridiction" value="%s" maxlength="200"></label><label>Chambre<input name="chambre" value="%s" maxlength="200"></label>'
                     '<label>RG<input name="rg" value="%s" maxlength="60"></label><label>Nature<input name="nature" value="%s" maxlength="200"></label>'
                     '<label>Stade<input name="stade" value="%s" maxlength="200"></label><label>Statut<select name="statut">%s</select></label>'
                     '<label class="d51-check"><input type="checkbox" name="reference"%s> Procédure de référence</label>'
                     '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details></li>' % (
                         ' d51-ref' if p['reference'] else '', e(p['juridiction'] or 'Juridiction à préciser'), (' · RG ' + e(p['rg'])) if p['rg'] else '',
                         e(status.get(p['statut'], p['statut'])), ' <span class="d51-badge">référence</span>' if p['reference'] else '',
                         e(' · '.join(x for x in (p['chambre'], p['nature'], p['stade']) if x)), (' · ' + e(parts)) if parts else '',
                         e(mid, quote=True), e(p['id'], quote=True), p['version'], e(p['juridiction'], quote=True), e(p['chambre'], quote=True), e(p['rg'], quote=True),
                         e(p['nature'], quote=True), e(p['stade'], quote=True),
                         ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == p['statut'] else '', e(v)) for k, v in status.items()),
                         ' checked' if p['reference'] else ''))
    add = ('<details><summary>Ajouter une procédure</summary><form class="m5-form" data-api="m5651/procedure" data-reload><input type="hidden" name="matter" value="%s">'
           '<label>Juridiction<input name="juridiction" maxlength="200"></label><label>RG<input name="rg" maxlength="60"></label><label>Nature<input name="nature" maxlength="200"></label>'
           '<label>Statut<select name="statut"><option value="active">Active</option><option value="historique">Historique</option><option value="citee">Citée</option></select></label>'
           '<label class="d51-check"><input type="checkbox" name="reference"> Procédure de référence</label>'
           '<button class="ax-btn ghost" type="submit">Ajouter</button><p role="status" class="m5-status"></p></form></details>') % e(mid, quote=True)
    out = _card('Procédures', ('<ul class="d51-list">%s</ul>' % ''.join(cards) if cards else '<p class="ax-muted">Aucune procédure identifiée.</p>') + add +
                '<p class="ax-muted">Un RG cité dans des écritures (affaire ancienne, jurisprudence) ne remplace jamais le RG de la procédure de référence.</p>', 'd51-procedures')
    events = modele5651.events(desk, mid)
    labels = {'acte': 'acte', 'signification': 'signification', 'depot': 'dépôt', 'reception': 'réception', 'evenement': 'événement'}
    ev_rows = ''.join('<li><span class="d51-kind">%s</span> %s <span class="ax-muted">%s · %s</span> %s</li>' % (
        e(ev['nature'].replace('_', ' ')), e(ev['title']), e(', '.join('%s : %s' % (labels.get(k, k), _date_fr(v)) for k, v in ev['dates'].items() if v) or 'date inconnue'),
        e(ev['status']), _source_links(prefix, mid, ev['sources'], 2)) for ev in events[:200])
    out += _card('Actes et événements de procédure', ('<ul class="d51-list">%s</ul>' % ev_rows) if ev_rows else '<p class="ax-muted">Aucun acte identifié.</p>', 'd51-actes')
    js = modele5651.judgments(desk, mid)
    j_rows = ''.join('<li><strong>%s</strong> %s — %s %s</li>' % (e(j['nature'] or 'Décision'), e(j['juridiction']), e('original examiné' if j['examined'] else
                                                                                                                       'Décision mentionnée — original non examiné'),
                                                                 _source_links(prefix, mid, j['sources'], 1)) for j in js)
    out += _card('Décisions rendues dans ce dossier', ('<ul class="d51-list">%s</ul>' % j_rows) if j_rows else '<p class="ax-muted">Aucune décision identifiée.</p>' +
                 '<p class="ax-muted">Les décisions citées comme précédents figurent dans les sources juridiques, pas ici.</p>', 'd51-decisions-j')
    rec = narrative(desk, mid, 'procedure')
    written = ''.join('<p>%s</p>' % e(p.get('texte', '')) for p in (rec or {}).get('paragraphs', []))
    out += _card('Rappel de la procédure', (written or '<p class="ax-muted">Aucun rappel rédigé.</p>') +
                 ('<form class="m5-form d51-inline" data-api="m5651/rappel" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="kind" value="procedure">'
                  '<button class="ax-btn ghost" type="submit">Rédiger le rappel de la procédure</button><p role="status" class="m5-status"></p></form>') % e(mid, quote=True),
                 'd51-rappel-proc')
    out += preparations_html(desk, mid, prefix)
    out += chronology_html(desk, auth, prefix, mid, str(args.get('chrono') or 'procedure'), 'procedure')
    from .web5632 import procedure_panel
    out += procedure_panel(desk, prefix, m)
    return out


# ------------------------------------------------------------------------------------------------ Pièces (D08, D11)
def _doc_row(prefix, mid, d, classify=True):
    from . import modele5651
    from .fiche460 import doc_href
    opts = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == d['nature'] else '', e(v)) for k, v in modele5651.NATURES.items())
    form = ('<details class="d51-act"><summary>Classer</summary><form class="m5-form" data-api="m5651/document/classer" data-reload>'
            '<input type="hidden" name="matter" value="%s"><input type="hidden" name="path" value="%s"><input type="hidden" name="version" value="%d">'
            '<label>Nature<select name="nature">%s</select></label><label>Utilisé comme pièce<select name="piece"><option value="">Inchangé</option>'
            '<option value="oui">Oui</option><option value="non">Non</option></select></label><label>Partie productrice<input name="piece_party" value="%s" maxlength="60" '
            'placeholder="cabinet, ou nom de l’adversaire"></label><label>N°<input name="piece_number" value="%s" maxlength="20" size="5"></label>'
            '<button class="ax-btn ghost" type="submit">Enregistrer</button><p role="status" class="m5-status"></p></form></details>') % (
        e(mid, quote=True), e(d['path'], quote=True), d['version'], opts, e(d['piece_party'], quote=True), e(d['piece_number'], quote=True)) if classify else ''
    return ('<tr><td data-label="N°">%s</td><td data-label="Titre"><a href="%s">%s</a></td><td data-label="Partie">%s</td><td data-label="Date">%s</td>'
            '<td data-label="Lecture">%s</td><td>%s</td></tr>') % (
        e(d['piece_number'] or '—'), e(prefix + doc_href(d['path'], mid), quote=True), e(d['name']), e(d['piece_party'] or '—'), e(_date_fr(d['doc_date']) or '—'),
        e(modele5651.READING.get(d['reading'], d['reading']) + (' (%s)' % modele5651.ERROR_LABELS.get(d['error_category'], '') if d['error_category'] else '')), form)


def _table(prefix, mid, rows, empty, classify=True):
    if not rows:
        return '<p class="ax-muted">%s</p>' % e(empty)
    return ('<table class="d51-table"><thead><tr><th>N°</th><th>Titre</th><th>Partie</th><th>Date</th><th>Lecture</th><th></th></tr></thead><tbody>%s</tbody></table>'
            % ''.join(_doc_row(prefix, mid, d, classify) for d in rows))


def pieces_html(desk, auth, prefix, m, args):
    from . import dossier5627, modele5651
    mid = m['id']
    data = modele5651.pieces(desk, mid)
    filt = str(args.get('filtre') or '')
    out = ''
    nav = ''.join('<a href="%s"%s>%s</a>' % (e(prefix + _q(mid, 'pieces', filtre=k), quote=True), ' aria-current="true"' if k == filt else '', e(v)) for k, v in (
        ('', 'Toutes'), ('nos', 'Nos pièces'), ('adverses', 'Pièces adverses'), ('a_numeroter', 'À numéroter'), ('a_classer', 'À classer')))
    out += '<nav class="d51-filters" aria-label="Pièces">%s</nav>' % nav
    if filt in ('', 'nos'):
        out += _card('Nos pièces (%d)' % len(data['nos_pieces']), _table(prefix, mid, data['nos_pieces'], 'Aucune pièce numérotée ni communiquée par le cabinet.'))
    if filt in ('', 'adverses'):
        if data['adverses']:
            for party, rows in sorted(data['adverses'].items()):
                out += _card('Pièces de %s (%d)' % (party, len(rows)), _table(prefix, mid, rows, ''))
        else:
            out += _card('Pièces adverses', '<p class="ax-muted">Aucune pièce adverse identifiée (indiquez la partie productrice dans « Classer »).</p>')
    if filt in ('', 'a_numeroter'):
        out += _card('Pièces probables à numéroter (%d)' % len(data['a_numeroter']), _table(prefix, mid, data['a_numeroter'],
                                                                                           'Aucune.') + '<p class="ax-muted">Projet : rien n’est ajouté au bordereau ni renuméroté automatiquement.</p>')
    if filt in ('', 'a_classer'):
        out += _card('À classer (%d)' % len(data['a_classer']), _table(prefix, mid, data['a_classer'], 'Aucun document de nature incertaine.'), 'd51-a-classer')
    out += coverage_html(desk, prefix, mid)
    others = {}
    for d in modele5651.documents(desk, mid):
        if d['nature'] in modele5651.PROCEDURE_NATURES or d['nature'] in ('jurisprudence', 'note_interne', 'archive', 'courriel', 'bordereau', 'gestion'):
            others.setdefault(d['nature'], []).append(d)
    if others:
        out += _card('Autres documents par catégorie', ''.join('<details><summary>%s (%d)</summary>%s</details>' % (
            e(modele5651.NATURES[k]), len(v), _table(prefix, mid, v[:100], '')) for k, v in sorted(others.items())) +
            '<p class="ax-muted">Actes, conclusions, jurisprudence et notes n’apparaissent pas comme pièces sans lien de bordereau. <a href="%s">Tous les documents</a></p>'
            % e(prefix + '/documents?' + urlencode({'matter': mid}), quote=True))
    out += '<details class="d51-legacy"><summary>Vue technique : fichiers indexés et bordereaux</summary>%s' % dossier5627.pieces_html(desk, prefix, m)
    from .web5632 import working_folders_panel
    out += working_folders_panel(desk, prefix, m)
    out += ('<p class="d5636-link"><a class="ax-btn ghost" href="%s">Données des pièces (types, parties, dates, montants, textes cités)</a></p></details>'
            % e(prefix + '/pieces-donnees?dossier=' + mid, quote=True))
    return out


RETRYABLE = ('scan', 'outil', 'acces', 'autre', 'volume')


def coverage_html(desk, prefix, mid):
    from . import modele5651
    cov = coverage(desk, mid)
    rows = [('Documents inventoriés', cov['inventories']), ('Classés', cov['classes']), ('Lus', cov['lisibles']), ('Lus partiellement', cov['partiels']),
            ('En erreur de lecture', cov['erreurs']), ('Sans texte (exclus)', cov['exclus']), ('Lisibles restant à analyser par l’IA', cov['a_analyser'])]
    body = '<dl class="d51-dl">%s</dl>' % ''.join('<dt>%s</dt><dd>%d</dd>' % (e(k), v) for k, v in rows)
    if cov['categories']:
        items = []
        for cat, n in sorted(cov['categories'].items(), key=lambda x: -x[1]):
            docs = [d for d in modele5651.documents(desk, mid) if d['error_category'] == cat or (not d['error_category'] and cat == 'autre' and d['reading'] == 'erreur')]
            sample = ''.join('<li>%s <span class="ax-muted">%s</span></li>' % (e(d['name']), e(d['error'][:80])) for d in docs[:20])
            retry = ('<form class="m5-form d51-inline" data-api="m5651/couverture/reprendre" data-reload><input type="hidden" name="matter" value="%s">'
                     '<input type="hidden" name="categorie" value="%s"><button class="ax-btn ghost" type="submit">Relancer la lecture de ces documents</button>'
                     '<p role="status" class="m5-status"></p></form>') % (e(mid, quote=True), e(cat, quote=True)) if cat in RETRYABLE else \
                '<p class="ax-muted">Relance inutile : fournir une version non protégée ou dans un format pris en charge.</p>'
            items.append('<details><summary>%s : %d</summary><ul>%s</ul>%s</details>' % (e(modele5651.ERROR_LABELS.get(cat, cat)), n, sample, retry))
        body += '<h4>Erreurs par catégorie</h4>' + ''.join(items)
    return _card('Couverture documentaire', body, 'd51-coverage')


# ------------------------------------------------------------------------------------------------ Correspondances, Travaux
def correspondances_html(desk, auth, prefix, m, args):
    from . import dossier5627
    return dossier5627.correspondances_html(desk, prefix, m, str(args.get('archive') or '')) + \
        '<details class="d51-legacy" open><summary>Courriels rattachés au dossier</summary>%s</details>' % dossier5627.courriels_html(desk, prefix, m)


def travaux_html(desk, auth, prefix, m, args):
    from . import dossier5627, memoire5651, relecture5651, apprentissage5651
    from .missions5651 import card_html, missions_of
    mid = m['id']
    owner = str(auth.get('owner') or 'cabinet')
    from .i18n5631 import role as _role
    admin = _role() == 'administrateur'
    out = ''
    gaps = memoire5651.gaps(desk, mid)
    if gaps:
        def gap_form(g):
            hidden = '<input type="hidden" name="matter" value="%s"><input type="hidden" name="id" value="%s">' % (e(mid, quote=True), e(g['id'], quote=True))
            ignore = ('<form class="m5-form d51-inline" data-api="m5651/lacune" data-reload>%s<input type="hidden" name="abandonner" value="true">'
                      '<button class="ax-btn ghost" type="submit">Ignorer</button></form>') % hidden
            if g['kind'] == 'fichier_illisible':
                return '<p class="ax-muted">Déposez une version lisible ou non protégée dans le dossier ; la lecture reprendra.</p>' + ignore
            return ('<form class="m5-form d51-inline" data-api="m5651/lacune" data-reload>%s<input name="reponse" maxlength="2000" placeholder="Information fournie" '
                    'aria-label="Information fournie"><button class="ax-btn ghost" type="submit">Fournir</button></form>' % hidden) + ignore
        out += _card('Travail préparé — %d information(s) nécessaire(s) pour finaliser' % len(gaps), '<ul class="d51-list">%s</ul>' % ''.join(
            '<li><strong>%s</strong> <span class="d51-badge">%s</span> <span class="ax-muted">%s</span><div>%s</div></li>' % (
                e(g['expected']), e(memoire5651.GAP_KINDS.get(g['kind'], g['kind'])), e(memoire5651.BLOCKING[g['blocking']]), gap_form(g))
            for g in gaps[:30]) + '<p class="ax-muted">Les parties qui ne dépendent pas de ces informations continuent ; rien n’est inventé à leur place.</p>', 'd51-gaps')
    try:   # 5.6.57 : préparation d'audience ou de rendez-vous, demande de pièces, point client, diligences proposées
        from .parcours5657 import matter_html as _p57
        out += _p57(desk, prefix, mid, owner)
    except Stop:
        pass
    ms = missions_of(desk, mid, owner, admin, prefix)
    out += _card('Missions', (''.join(card_html(x, prefix) for x in ms) or '<p class="ax-muted">Aucune mission pour ce dossier.</p>'), 'd51-missions')
    sid = str(args.get('relecture') or '')
    out += review_html(desk, prefix, mid, sid)
    try:   # 5.6.53 : honoraires et facturation du dossier (convention, déjà facturé, provisions, reste à facturer, solde connu)
        from .web5653 import matter_block_html
        out += matter_block_html(desk, prefix, mid, owner)
    except Stop:
        pass
    try:   # 5.6.52 (I01, I02, I07, I12) : préparer une signature, un recommandé ou un courrier ; identifier le client (SIREN)
        from .connexions5652 import matter_panel_html
        out += matter_panel_html(desk, prefix, mid, owner)
    except Stop:
        pass
    rules = apprentissage5651.rules(desk, owner, mid)
    if rules:
        out += _card('Règles apprises de vos corrections', '<ul class="d51-list">%s</ul>' % ''.join(
            '<li><span class="d51-badge">%s</span> <span class="d51-badge">%s</span> %s <span class="ax-muted">v%d · %s</span>'
            '<form class="m5-form d51-inline" data-api="m5651/regle" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="id" value="%s">'
            '<input type="hidden" name="status" value="%s"><button class="ax-btn ghost" type="submit">%s</button></form></li>' % (
                e(apprentissage5651.SCOPES[r['scope']]), e(apprentissage5651.CATEGORIES[r['category']]), e(r['instruction'][:300]), r['version'],
                e('active' if r['status'] == 'active' else 'désactivée'), e(mid, quote=True), e(r['id'], quote=True),
                'desactivee' if r['status'] == 'active' else 'active', 'Désactiver' if r['status'] == 'active' else 'Réactiver') for r in rules[:40]), 'd51-rules')
    from .savoir5659 import KINDS as _kinds59   # 5.6.59 (A07) : une rédaction corrigée devient une ressource du cabinet (anonymisée, à approuver)
    out += _card('Faire d’une rédaction corrigée un savoir-faire du cabinet', (
        '<p class="ax-muted">Collez le passage corrigé (structure, clause, liste de vérification…). Les noms des parties, le client, les adresses, '
        'dates, montants et numéros de rôle sont remplacés avant l’enregistrement ; la ressource n’est utilisée qu’après approbation.</p>'
        '<form class="m5-form" data-api="m5659/savoir/proposer"><input type="hidden" name="matter" value="%s"><label>Type<select name="kind">%s</select></label>'
        '<label>Titre<input name="title" required maxlength="200"></label><label>Mots-clés<input name="keywords" maxlength="400"></label>'
        '<label>Texte<textarea name="body" rows="5" required maxlength="20000"></textarea></label><button class="ax-btn ghost" type="submit">Proposer</button>'
        '<p role="status" class="m5-status"></p></form>') % (e(mid, quote=True), ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in _kinds59.items())),
        'd59-savoir')
    out += '<details class="d51-legacy" open><summary>Documents préparés et échanges avec l’agent</summary>%s</details>' % dossier5627.travaux_html(desk, prefix, m)
    return out


def review_html(desk, prefix, mid, sid=''):
    """A05 : lancer une relecture, suivre les sessions, décider passage par passage, appliquer en révisions natives."""
    from . import modele5651, relecture5651
    words = [d for d in modele5651.documents(desk, mid) if d['path'].lower().endswith('.docx')]
    start = ('<details><summary>Faire relire un document Word</summary><form class="m5-form" data-api="m5651/relecture" data-reload><input type="hidden" name="matter" value="%s">'
             '<label>Document<select name="path" required>%s</select></label><label>Consigne<textarea name="consigne" rows="2" maxlength="4000" required '
             'placeholder="Ex. actualiser à partir des nouvelles pièces ; retravailler seulement les paragraphes 12 à 15"></textarea></label>'
             '<label>Paragraphes visés (facultatif, numéros séparés par des virgules)<input name="paragraphes" maxlength="400"></label>'
             '<button class="ax-btn" type="submit">Proposer des modifications</button><p role="status" class="m5-status"></p></form></details>') % (
        e(mid, quote=True), ''.join('<option value="%s">%s</option>' % (e(d['path'], quote=True), e(d['name'])) for d in words[:300]) or '<option value="">Aucun Word</option>')
    rows = relecture5651.sessions(desk, mid)
    lst = ''.join('<li><a href="%s">%s</a> <span class="ax-muted">%s · %d/%d lot(s)%s</span></li>' % (
        e(prefix + _q(mid, 'travaux', relecture=s['id']) + '#d51-review', quote=True), e(PurePosixPath(s['path']).name),
        e({'en_attente': 'en file', 'en_cours': 'en cours', 'terminee': 'propositions prêtes', 'perimee': 'document modifié : à relancer',
           'annulee': 'arrêtée'}.get(s['state'], s['state'])), s['done_batches'], s['total_batches'],
        (' · nouvelle version : ' + e(PurePosixPath(s['output_path']).name)) if s['output_path'] else '') for s in rows)
    detail = ''
    if sid:
        try:
            s = relecture5651.session(desk, mid, sid)
        except Stop:
            s = None
        if s:
            props = []
            for p in s['proposals']:
                decide = ''
                if p['state'] in ('proposee', 'acceptee', 'appliquee'):
                    decide = ('<form class="m5-form d51-inline" data-api="m5651/relecture/decider" data-reload><input type="hidden" name="matter" value="%s">'
                              '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d"><input type="hidden" name="action" value="accepter">'
                              + ('<input type="hidden" name="sources_verifiees" value="true"><button class="ax-btn" type="submit">Accepter (sources actuelles vérifiées)</button></form>'
                                 if p['a_recontroler'] else '<button class="ax-btn" type="submit">Accepter</button></form>') +
                              '<form class="m5-form d51-inline" data-api="m5651/relecture/decider" data-reload><input type="hidden" name="matter" value="%s">'
                              '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d"><input type="hidden" name="action" value="refuser">'
                              '<button class="ax-btn ghost" type="submit">Refuser</button></form>'
                              '<details><summary>Reformuler</summary><form class="m5-form" data-api="m5651/relecture/decider" data-reload><input type="hidden" name="matter" value="%s">'
                              '<input type="hidden" name="id" value="%s"><input type="hidden" name="version" value="%d"><input type="hidden" name="action" value="modifier">'
                              '<label>Texte<textarea name="texte" rows="3" maxlength="8000">%s</textarea></label>'
                              '<label>Retenir cette correction pour<select name="portee"><option value="">Ne pas retenir</option><option value="document">Ce document</option>'
                              '<option value="dossier">Ce dossier</option><option value="style">Mon style habituel</option></select></label>'
                              '<label>Catégorie<select name="categorie"><option value="style">Style</option><option value="mise_en_forme">Mise en forme</option>'
                              '<option value="exactitude">Exactitude factuelle</option><option value="attribution">Attribution</option><option value="strategie">Stratégie</option></select></label>'
                              '<button class="ax-btn ghost" type="submit">Enregistrer ma formulation</button><p role="status" class="m5-status"></p></form></details>') % (
                        e(mid, quote=True), e(p['id'], quote=True), p['version'], e(mid, quote=True), e(p['id'], quote=True), p['version'],
                        e(mid, quote=True), e(p['id'], quote=True), p['version'], e(p['replacement']))
                # 5.6.57 (A08) : passage d'origine et proposition côte à côte, motif et sources ; « Reformuler » à la place de « Modifier »
                srcs = ''.join('<li>%s</li>' % e(PurePosixPath(str(x.get('path') or x.get('titre') or x.get('id') or '')).name or str(x.get('id') or ''))
                               for x in (p['sources'] or [])[:6])
                props.append(('<li class="d51-prop d51-prop-%s"><div class="ax-muted">§%d · %s · %s%s%s</div><div class="d51-diff d5657-sbs">'
                             '<div><span class="d5657-lab">Passage d’origine</span><del>%s</del></div><div><span class="d5657-lab">Proposition</span><ins>%s</ins></div></div>'
                             '<p class="d5657-why"><strong>Motif :</strong> %s</p>' + (('<details><summary>Sources (%d)</summary><ul>%s</ul></details>' % (len(p['sources']), srcs))
                                                                                      if srcs else '<p class="ax-muted">Aucune source citée : vérifiez le passage.</p>') +
                             '%s</li>') % (
                                 e(p['state']), p['para_index'] + 1, e(relecture5651.NATURES.get(p['nature'], p['nature'])),
                                 e(relecture5651.STATES.get(p['state'], p['state']) + (' · incluse dans la version %s' % ', '.join(str(n) for n in p['exports'])
                                                                                      if p['exports'] else '')),
                                 ' · <strong>source modifiée : à recontrôler</strong>' if p['a_recontroler'] else '', (' · ' + e(p['conflict'])) if p['conflict'] else '',
                                 e(p['expected'][:1500]), e(p['replacement'][:1500]), e(p['motif']), decide))
            accepted = sum(1 for p in s['proposals'] if p['state'] in ('acceptee', 'appliquee') and p['accepted_version'] == p['version'])
            styles = sum(1 for p in s['proposals'] if p['nature'] == 'style' and p['state'] == 'proposee' and not p['a_recontroler'])
            style_btn = ('<form class="m5-form d51-inline" data-api="m5657/relecture/style" data-reload><input type="hidden" name="matter" value="%s">'
                         '<input type="hidden" name="session" value="%s"><button class="ax-btn ghost" type="submit">Accepter les %d modification(s) de formulation</button>'
                         '<p class="ax-muted">Les faits, demandes et fondements juridiques restent à décider un par un.</p><p role="status" class="m5-status"></p></form>') % (
                e(mid, quote=True), e(sid, quote=True), styles) if styles > 1 else ''
            apply_btn = ('<form class="m5-form" data-api="m5651/relecture/appliquer" data-reload><input type="hidden" name="matter" value="%s"><input type="hidden" name="session" value="%s">'
                         '<button class="ax-btn" type="submit">Créer une version avec les %d décision(s) acceptée(s) (cumulatives)</button>'
                         '<p class="ax-muted">Nouveau fichier à côté de l’original, en suivi des modifications Word ; l’original n’est pas modifié.</p>'
                         '<p role="status" class="m5-status"></p></form>') % (e(mid, quote=True), e(sid, quote=True), accepted) if accepted else ''
            state_note = '' if s['state'] == 'terminee' else '<p class="d51-warn"%s>Propositions %s : relecture non finalisée.</p>' % (
                ' data-d51-waiting' if s['state'] in ('en_attente', 'en_cours') else '', 
                'en cours de génération' if s['state'] in ('en_attente', 'en_cours') else 'partielles')
            detail = '<div id="d51-review"><h4>%s</h4>%s%s<ul class="d51-list">%s</ul>%s</div>' % (
                e(PurePosixPath(s['path']).name), state_note, style_btn, ''.join(props) or '<li class="ax-muted">Aucune proposition pour l’instant.</li>', apply_btn)
    return _card('Relecture Word', start + ('<ul class="d51-list">%s</ul>' % lst if lst else '') + detail, 'd51-relecture')


# ------------------------------------------------------------------------------------------------ page
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
    if view not in dict(TABS):
        view = 'apercu'
    modele5651.ensure_schema(desk)
    if not modele5651.last_rebuild(desk, mid):
        try:   # première ouverture : reconstruction sans IA à partir des lectures existantes
            modele5651.rebuild(desk, mid)
        except Stop:
            pass
    body = header_html(desk, prefix, m, view, auth)
    renderer = {'apercu': apercu_html, 'faits': faits_html, 'procedure': procedure_html, 'pieces': pieces_html,
                'correspondances': correspondances_html, 'travaux': travaux_html}[view]
    body += renderer(desk, auth, prefix, m, args)
    if view == 'apercu':   # 5.6.50 : discussion du dossier ; 5.6.36 : intervenants
        from .i18n5631 import role as _role50
        from .web5650 import matter_card
        body += matter_card(desk, auth, prefix, m['id'], _role50(), str(auth.get('owner') or 'cabinet'))
        try:
            from .crm5636 import matter_block
            body += matter_block(desk, prefix, m['id'])
        except Stop:
            pass
    body += '</div>'
    head = '<script defer src="%s/static/v5627.js"></script>' % e(prefix)
    return shell(matter_display(m), body, prefix, auth['csrf'], '/dossiers', head)

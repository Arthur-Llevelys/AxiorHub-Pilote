"""Interface 4.7.0 : vérification des citations, relecture contradictoire, modèles d'actes, sources juridiques."""
from .common import matter_display
import json
from html import escape as e
from pathlib import PurePosixPath

from . import adversarial470, sources470, templates470, verify470
from .common import Stop, load_matters

MAX_TEXT = 120_000
BADGE = {'verifiee': 'ok', 'douteuse': 'warn', 'introuvable': 'bad', 'non_verifiable': 'muted'}
SEV = {'haute': 'bad', 'moyenne': 'warn', 'basse': 'muted'}


def _a(prefix, href, label):
    return '<a href="%s">%s</a>' % (e(prefix + href, quote=True), e(label))


# ---------------------------------------------------------------- fragments HTML (tout est échappé)

def report_html(report):
    counts = report.get('counts', {})
    out = ['<div class="vf-head %s" role="status"><strong>%s</strong>' % ('bad' if report.get('needs_check') else 'ok', e(report['headline']))]
    out.append('<span class="vf-counts">%s</span></div>' % ' '.join(
        '<span class="vf-badge %s">%d %s</span>' % (BADGE[k], counts.get(k, 0), e(verify470.STATUS_LABELS[k].lower()))
        for k in verify470.STATUS_LABELS if counts.get(k)))
    if report.get('fact_date'):
        out.append('<p class="vf-note">Versions des articles contrôlées à la date des faits : %s.</p>' % e(verify470._fmt(report['fact_date'])))
    else:
        out.append('<p class="vf-note">Date des faits non renseignée : version en vigueur à ce jour.</p>')
    out.append('<ol class="vf-list">')
    for item in report.get('items', []):
        out.append('<li class="vf-item %s" data-start="%d" data-end="%d" data-status="%s">' % (
            BADGE[item['status']], item['start'], item['end'], e(item['status'])))
        out.append('<div class="vf-line"><span class="vf-badge %s">%s</span> <strong>%s</strong>' % (
            BADGE[item['status']], e(item['status_label']), e(item['label'])))
        if item['url']:
            out.append(' <a class="vf-source" href="%s" target="_blank" rel="noopener noreferrer">Texte source</a>' % e(item['url'], quote=True))
        out.append(' <button type="button" class="vf-goto" title="Sélectionner dans le texte">Voir dans le texte</button></div>')
        out.append('<p class="vf-raw">%s</p>' % e(item['raw']))
        if item['reasons']:
            out.append('<ul class="vf-reasons">%s</ul>' % ''.join('<li>%s</li>' % e(r) for r in item['reasons']))
        if item['notes']:
            out.append('<ul class="vf-notes">%s</ul>' % ''.join('<li>%s</li>' % e(r) for r in item['notes']))
        applicable = item.get('applicable') or {}
        if applicable.get('excerpt'):
            out.append('<details><summary>Texte de la version applicable</summary><p class="vf-text">%s</p>'
                       '<p class="vf-note">En vigueur du %s au %s (%s).</p></details>' % (
                           e(applicable['excerpt']), e(verify470._fmt(applicable.get('start')) or '?'),
                           e(verify470._fmt(applicable.get('end')) or '…'), e(applicable.get('state', ''))))
        if item['sources']:
            out.append('<p class="vf-src">Source consultée : %s</p>' % '; '.join(
                '%s (%s, base au %s%s)' % (e(s['name'] or '—'), 'officielle' if s['official'] else 'non officielle',
                                           e(verify470._fmt(s['date']) or 'date inconnue'),
                                           ', cache' if s.get('cached') else '') for s in item['sources']))
        out.append('</li>')
    out.append('</ol>')
    if report.get('truncated'):
        out.append('<p class="vf-note">Contrôle limité aux %d premières références.</p>' % sources470.MAX_REFS_PER_CHECK)
    out.append('<p class="vf-note">« Introuvable » signifie non retrouvé dans les sources consultées, pas inexistant. '
               'Seules les références, jamais le contenu du dossier, sont transmises aux services de sources. '
               'Contrôle sur Légifrance / Judilibre recommandé pour tout texte récent.</p>')
    return ''.join(out)


def review_html(result):
    out = ['<div class="vf-head %s" role="status"><strong>%d point(s) relevé(s)</strong></div>' % (
        'bad' if result['count'] else 'ok', result['count'])]
    for note in result.get('notes', []):
        out.append('<p class="vf-note">%s</p>' % e(note))
    out.append('<ol class="vf-list">')
    for f in result['findings']:
        out.append('<li class="vf-item %s"><div class="vf-line"><span class="vf-badge %s">%s</span> <strong>%s</strong></div>' % (
            SEV[f['severity']], SEV[f['severity']], e(f['severity']), e(f['message'])))
        out.append('<ul class="vf-src-list">%s</ul></li>' % ''.join(
            '<li><em>%s</em>%s : « %s »</li>' % (e(s.get('label', '')), (' · ' + e(s['path'])) if s.get('path') else '', e(s.get('excerpt', '')))
            for s in f['sources']))
    out.append('</ol><p class="vf-note">%s Chaque point cite sa pièce ; aucun point n’est une conclusion sur le fond.</p>' % e(result['method']))
    return ''.join(out)


def mentions_html(check):
    out = ['<div class="vf-head %s" role="status"><strong>%s : %s</strong></div>' % (
        'ok' if check['ok'] else 'bad', e(check['label']),
        'toutes les mentions obligatoires sont présentes' if check['ok'] else '%d mention(s) obligatoire(s) manquante(s) — non prêt à relire' % len(check['missing']))]
    if check['missing']:
        out.append('<h3>Manquantes</h3><ul class="vf-reasons">%s</ul>' % ''.join(
            '<li><strong>%s</strong>%s%s</li>' % (e(x['label']), (' <small>(' + e(x['authority']) + ')</small>') if x['authority'] else '',
                                                 (' — ' + e(x['hint'])) if x['hint'] else '') for x in check['missing']))
    if check['warnings']:
        out.append('<h3>À signaler</h3><ul class="vf-notes">%s</ul>' % ''.join('<li>%s</li>' % e(x['label']) for x in check['warnings']))
    if check['present']:
        out.append('<details><summary>%d mention(s) présente(s)</summary><ul>%s</ul></details>' % (
            len(check['present']), ''.join('<li>%s</li>' % e(x['label']) for x in check['present'])))
    return ''.join(out)


# ---------------------------------------------------------------- pages

def _matter_options(desk, selected=''):
    return ''.join('<option value="%s"%s>%s</option>' % (
        e(m['id'], quote=True), ' selected' if m['id'] == selected else '', e(matter_display(m)))
        for m in load_matters(desk.c))


def document_text(desk, path):
    from . import office440
    from .documents import extract
    client = office440.dav_client(desk)
    info = client.stat(path)
    raw = client.download({'path': path, 'etag': info.get('etag', ''), 'size': info.get('size', 0)})
    return extract(raw, PurePosixPath(path).name, desk.c['documents'])[:MAX_TEXT]


def verification_page(desk, auth, prefix, args, shell, allowed_path):
    text, notice, stored = '', '', ''
    matter = str(args.get('matter', ''))
    path = args.get('path', '')
    if path:
        try:
            text = document_text(desk, allowed_path(desk, path))
        except Stop as ex:
            notice = '<p class="ax-alert">Document non lu : %s</p>' % e(str(ex))
    if args.get('project'):
        report = verify470.latest(desk, 'project', args['project'])
        if report:
            stored = '<section class="vf-panel"><h2>Contrôle enregistré du projet</h2>%s</section>' % report_html(report)
    fact_date = desk.settings('sources470:fact_date:' + matter, '') if matter else ''
    body = (
        '<h1>Vérification des citations</h1>'
        '<p class="ax-muted">Collez un texte (ou ouvrez un document depuis Documents). Chaque référence juridique est contrôlée dans les sources '
        'configurées. <strong>Seules les références sont transmises aux services de sources, jamais le texte ni le dossier.</strong> '
        '%s</p>%s%s'
        '<section class="vf-panel"><div class="vf-form">'
        '<label>Dossier <select id="v-matter"><option value="">—</option>%s</select></label>'
        '<label>Date des faits <input id="v-date" type="date" value="%s"></label>'
        '<label class="vf-wide">Texte à contrôler<textarea id="v-text" rows="14" maxlength="%d">%s</textarea></label>'
        '<div class="vf-buttons"><button type="button" class="ax-btn" id="v-check">Vérifier les citations</button>'
        '<button type="button" class="ax-btn ghost" id="v-review">Relecture contradictoire</button>'
        '<select id="v-kind" aria-label="Modèle d’acte"><option value="">Mentions : choisir un modèle…</option>%s</select>'
        '<button type="button" class="ax-btn ghost" id="v-mentions">Contrôler les mentions</button></div>'
        '<label class="vf-wide">Pièces adverses à confronter (une par ligne, chemin Nextcloud — facultatif)'
        '<textarea id="v-opp" rows="2" placeholder="/Dossiers/CLIENT/Conclusions_adverses.docx"></textarea></label>'
        '</div><div id="v-out" aria-live="polite"></div></section>') % (
        _a(prefix, '/sources', 'Sources juridiques et journal des requêtes'), notice, stored,
        _matter_options(desk, matter), e(str(fact_date or ''), quote=True), MAX_TEXT, e(text),
        ''.join('<option value="%s">%s</option>' % (e(k['kind']), e(k['label'])) for k in templates470.listing(desk)))
    return shell('Vérification des citations', body, prefix, auth['csrf'], '/verification')


def sources_page(desk, auth, prefix, args, shell):
    connector = sources470.Sources(desk)
    status = connector.status()
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>' % (
        e(p['name']), 'officielle' if p['official'] else 'non officielle (jeu du cabinet)', e(p['state']), p['failures']) for p in status['providers'])
    log = sources470.outbound_log(desk, 30)
    log_rows = ''.join('<tr><td>%s</td><td>%s</td><td><code>%s</code></td></tr>' % (
        e(x.get('at', '')), e(x.get('provider', '')), e(json.dumps(x.get('reference', {}), ensure_ascii=False))) for x in log)
    client_id = desk.settings('sources470:piste_client_id', '') or ''
    body = (
        '<h1>Sources juridiques</h1>'
        '<div class="ax-card"><p><strong>Périmètre des données.</strong> Cette fonction contacte des services externes (API PISTE : Légifrance, '
        'Judilibre). Seules sont transmises des <em>références structurées</em> : code, numéro d’article, juridiction, chambre, date, numéro de pourvoi ou de RG. '
        'Jamais un nom de partie, un montant, un extrait de pièce ou un texte rédigé. Chaque requête est journalisée ci-dessous.</p></div>'
        '<section class="vf-panel"><h2>Réglages</h2><div class="vf-form">'
        '<label class="vf-check"><input type="checkbox" id="s-enabled"%s> Activer la vérification auprès des sources externes</label>'
        '<label>Identifiant PISTE (client_id)<input id="s-id" type="text" value="%s" autocomplete="off"></label>'
        '<label>Secret PISTE (client_secret)<input id="s-secret" type="password" autocomplete="new-password" placeholder="%s"></label>'
        '<div class="vf-buttons"><button type="button" class="ax-btn" id="s-save">Enregistrer</button>'
        '<button type="button" class="ax-btn ghost" id="s-test">Tester les sources</button></div></div>'
        '<div id="s-out" aria-live="polite"></div></section>'
        '<section class="vf-panel"><h2>État des sources</h2>'
        '<p>Contrôle activé : <strong>%s</strong> · Réponses en cache : %d</p>'
        '<table class="vf-table"><thead><tr><th>Source</th><th>Nature</th><th>État</th><th>Échecs</th></tr></thead><tbody>%s</tbody></table>'
        '<p class="vf-note">Sans source activée, toute référence reste « non vérifiable » et tout brouillon qui en contient est marqué « à vérifier ». '
        'Une source en panne n’est jamais prise pour une absence.</p></section>'
        '<section class="vf-panel"><h2>Journal des requêtes sortantes (30 dernières)</h2>'
        '<table class="vf-table"><thead><tr><th>Date</th><th>Service</th><th>Référence transmise</th></tr></thead><tbody>%s</tbody></table></section>'
    ) % (' checked' if status['enabled'] else '', e(str(client_id), quote=True),
         'secret enregistré (laisser vide pour le conserver)' if (sources470.Path(desk.c.get('state_dir', '.')) / 'secrets' / 'piste_client_secret').exists() else '',
         'oui' if status['enabled'] else 'non', status['cache_entries'],
         rows or '<tr><td colspan="4">Aucune source configurée.</td></tr>',
         log_rows or '<tr><td colspan="3">Aucune requête.</td></tr>')
    return shell('Sources juridiques', body, prefix, auth['csrf'], '/verification')


def templates_page(desk, auth, prefix, args, shell):
    kind = args.get('kind') or 'conclusions'
    if kind not in templates470.KINDS:
        kind = 'conclusions'
    model = templates470.effective(desk, kind)
    custom = templates470.customization(desk, kind)
    tabs = ' '.join('<a class="ax-btn%s" href="%s">%s</a>' % ('' if k['kind'] == kind else ' ghost', e(prefix + '/modeles?kind=' + k['kind'], quote=True), e(k['label']))
                    for k in templates470.listing(desk))
    base = templates470.KINDS[kind]['mentions']
    rows = ''.join('<tr><td><label><input type="checkbox" class="m-on" value="%s"%s> %s</label></td><td>%s</td><td>%s</td></tr>' % (
        e(x['id'], quote=True), '' if x['id'] in model['disabled'] else ' checked', e(x['label']),
        e(x['severity']), e(x['authority'])) for x in base)
    extra = '\n'.join('%s | %s | %s' % (x['label'], ' ; '.join(x['patterns']), x['severity']) for x in custom.get('extra', []))
    body = (
        '<h1>Modèles d’actes et mentions obligatoires</h1><nav class="vf-tabs" aria-label="Modèles">%s</nav>'
        '<p class="ax-muted">Un acte dont une mention <strong>bloquante</strong> est absente n’est jamais « prêt à relire ». Les listes sont des repères '
        'à adapter à vos procédures : vous pouvez retirer une mention, en ajouter, et modifier la trame. Vérifiez les références citées avant usage.</p>'
        '<section class="vf-panel" data-kind="%s" id="m-panel"><h2>%s</h2>'
        '<table class="vf-table"><thead><tr><th>Mention (décochez pour retirer)</th><th>Niveau</th><th>Repère</th></tr></thead><tbody>%s</tbody></table>'
        '<label class="vf-wide">Mentions supplémentaires — une par ligne : <code>libellé | motif1 ; motif2 | bloquante ou avertissement</code> '
        '(motifs = expressions régulières cherchées sans accents ni majuscules)<textarea id="m-extra" rows="4">%s</textarea></label>'
        '<label class="vf-wide">Trame indicative transmise au rédacteur<textarea id="m-skel" rows="8">%s</textarea></label>'
        '<div class="vf-buttons"><button type="button" class="ax-btn" id="m-save">Enregistrer le modèle</button></div>'
        '<div id="m-out" aria-live="polite"></div></section>'
    ) % (tabs, e(kind, quote=True), e(model['label']), rows, e(extra), e(model['skeleton']))
    return shell('Modèles d’actes', body, prefix, auth['csrf'], '/verification')


# ---------------------------------------------------------------- API

def _matter(desk, mid):
    mid = str(mid or '')
    if mid and not any(m['id'] == mid for m in load_matters(desk.c)):
        raise Stop('dossier_absent')
    return mid


def _text(data):
    text = str(data.get('text') or '')
    if not text.strip():
        raise Stop('texte_vide')
    if len(text) > MAX_TEXT:
        raise Stop('texte_trop_long')
    return text


def handle(desk, name, data, method='POST', args=None):
    args = args or {}
    if method == 'GET':
        if name == 'sources/status':
            return sources470.Sources(desk).status()
        if name == 'sources/log':
            return {'log': sources470.outbound_log(desk, 50)}
        if name == 'templates':
            return {'templates': templates470.listing(desk)}
        raise Stop('route_inconnue')
    if name == 'citations/check':
        text = _text(data)
        matter = _matter(desk, data.get('matter'))
        fact_date = str(data.get('fact_date') or (desk.settings('sources470:fact_date:' + matter, '') if matter else '') or '')
        report = verify470.verify_text(desk, text, fact_date)
        verify470.store(desk, str(data.get('scope') or 'manual'), str(data.get('scope_id') or ''), matter, report)
        return {'report': report, 'html': report_html(report)}
    if name == 'review/run':
        text = _text(data)
        matter = _matter(desk, data.get('matter'))
        if not matter:
            raise Stop('dossier_absent')
        fact_date = str(data.get('fact_date') or desk.settings('sources470:fact_date:' + matter, '') or '')
        citations = verify470.verify_text(desk, text, fact_date)
        opponents = [str(x) for x in (data.get('opponent_paths') or []) if str(x).strip()][:10]
        result = adversarial470.review(desk, matter, text, opponents, citations)
        desk.audit('relecture_470', {'matter': matter, 'findings': result['count']})
        return {'review': result, 'html': review_html(result), 'citations_html': report_html(citations)}
    if name == 'templates/check':
        kind = str(data.get('kind') or '')
        check = templates470.check(desk, kind, _text(data))
        return {'check': check, 'html': mentions_html(check)}
    if name == 'templates/save':
        kind = str(data.get('kind') or '')
        extra = []
        for line in str(data.get('extra_text') or '').splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 2:
                raise Stop('mention_personnalisee_invalide')
            extra.append({'label': parts[0], 'patterns': [p.strip() for p in parts[1].split(';') if p.strip()],
                          'severity': 'avertissement' if len(parts) > 2 and parts[2].startswith('avert') else 'bloquante'})
        value = templates470.save_customization(desk, kind, {'disabled': data.get('disabled') or [], 'extra': extra,
                                                              'skeleton': data.get('skeleton') or ''})
        return {'saved': True, 'mentions': len(templates470.effective(desk, kind)['mentions']), 'customized': bool(value)}
    if name == 'sources/settings':
        return sources470.save_settings(desk, bool(data.get('enabled')), data.get('client_id', ''), data.get('secret', ''),
                                        bool(data.get('clear_secret')))
    if name == 'sources/fact_date':
        matter = _matter(desk, data.get('matter'))
        value = str(data.get('fact_date') or '')
        if value and not __import__('re').fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise Stop('date_des_faits_invalide')
        if matter:
            desk.setting('sources470:fact_date:' + matter, value)
        return {'saved': True}
    if name == 'sources/test':
        report = verify470.verify_text(desk, "Test de connexion : article 1240 du Code civil.")
        return {'report': report, 'html': report_html(report)}
    raise Stop('route_inconnue')

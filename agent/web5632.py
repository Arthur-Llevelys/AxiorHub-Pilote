"""5.6.32 : écrans et API des fonctions du lot (sources juridiques, contrôle des références, veille, compétences, documents de procédure,
fiches de pièces). Routes sous /api440/m5632/ ; celles qui touchent la configuration commune du cabinet sont sous m5632/admin/.

Le propriétaire est toujours celui de la session d'authentification ; chaque POST passe par le contrôle d'origine et de jeton CSRF."""
import base64
from html import escape as e
import json

from .common import Stop
from . import i18n5631 as i18n

LAWYER_ROUTES = frozenset({'m5632/controle/run', 'm5632/veille/profile', 'm5632/veille/refresh', 'm5632/veille/mark', 'm5632/skills/create',
                           'm5632/skills/import', 'm5632/skills/enable', 'm5632/skills/copy', 'm5632/skills/edit', 'm5632/dp/start',
                           'm5632/dp/decide', 'm5632/dp/apply', 'm5632/dp/drafts', 'm5632/synthese/build',
                           'm5632/dossiers/state', 'm5632/dossiers/create', 'm5632/briefing/oral',
                           'm5632/courrier/prefs', 'm5632/courrier/rule', 'm5632/courrier/rule/enable', 'm5632/courrier/rule/delete', 'm5632/courrier/apply', 'm5632/modeles/exemple.docx'})


def api(env, desk, auth, prefix, name, args, method):
    from .web440 import check_post, json_out
    from .web567 import actor
    owner, role = actor(env)
    route = name[len('m5632/'):]
    admin = role == 'administrateur'
    if method == 'GET':
        if route == 'sources':
            from .sources5632 import catalogue
            return json_out({'providers': catalogue(desk)})
        if route == 'veille/state':
            from .veille5632 import state, items
            return json_out({'state': state(desk, owner), 'items': items(desk, owner)})
        if route == 'skills':
            from .competences5632 import listing, auto_mode
            return json_out({'skills': listing(desk, owner), 'auto': auto_mode(desk)})
        if route == 'skills/export':
            from .competences5632 import export
            raw = export(desk, str(args.get('id') or ''), args.get('version') or None)
            return {'status': '200 OK', 'kind': 'application/zip', 'body': raw,
                    'headers': [('Content-Disposition', 'attachment; filename="competence.zip"')]}
        if route == 'modeles/exemple.docx':   # 5.6.32 (R08) : modèle Word d'exemple fictif, balisé
            from .modeles5632 import example_docx
            return {'status': '200 OK', 'kind': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'body': example_docx(),
                    'headers': [('Content-Disposition', 'attachment; filename="modele-courrier-exemple.docx"')]}
        if route == 'briefing/oral':   # 5.6.32 : résumé oral du briefing du matin
            from .oral5632 import get
            return json_out(get(desk, str(args.get('report') or '')))
        if route == 'dossiers/state':
            from . import dossiers5632, metier500
            from .document_projects import _dav
            return json_out({'folders': dossiers5632.state(desk, metier500.require_matter(desk, str(args.get('matter') or '')), _dav(desk))})
        if route == 'dp/view':
            from .procedure5632 import view
            return json_out(view(desk, owner, str(args.get('mission') or '')))
        raise Stop('route_inconnue')
    if method != 'POST':
        raise Stop('methode_refusee')
    data = check_post(env, auth)
    if route.startswith('admin/') and not admin:
        raise Stop('role_insuffisant')
    if route == 'admin/sources/connect':
        from .sources5632 import connect
        return json_out({'connector': connect(desk, str(data.get('provider') or ''), str(data.get('token') or ''), str(data.get('auth') or ''),
                                              data.get('allow_external') in (True, 'true', '1', 'on')), 'message': i18n.t('src.saved')})
    if route == 'admin/sources/verify':
        from .sources5632 import verify
        return json_out(verify(desk, str(data.get('provider') or '')))
    if route == 'admin/sources/enable':
        from .sources5632 import enable
        return json_out({'connector': enable(desk, str(data.get('provider') or ''), data.get('on') in (True, 'true', '1', 'on'))})
    if route == 'admin/veille/tavily':
        from .veille5632 import save_tavily_key
        return json_out(save_tavily_key(desk, data.get('key')))
    if route == 'admin/skills/auto':
        desk.setting('competences5632:auto', data.get('on') in (True, 'true', '1', 'on'))
        return json_out({'auto': data.get('on') in (True, 'true', '1', 'on')})
    if route == 'controle/run':
        from .controle5632 import control, report_html
        result = control(desk, str(data.get('text') or ''), owner, str(data.get('matter') or ''), str(data.get('label') or ''), str(data.get('date_utile') or ''))
        return json_out({'counts': result['counts'], 'provider': result['provider'], 'html': report_html(result)})
    if route == 'veille/profile':
        from .veille5632 import save_profile
        return json_out({'profile': save_profile(desk, owner, data), 'message': i18n.t('vj.saved')})
    if route == 'veille/refresh':
        from .veille5632 import refresh
        return json_out({**refresh(desk, owner), 'message': i18n.t('vj.refreshing')})
    if route == 'veille/mark':
        from .veille5632 import mark
        return json_out(mark(desk, owner, str(data.get('id') or ''), str(data.get('action') or ''), str(data.get('matter') or '')))
    if route == 'skills/create':
        from .competences5632 import create
        scope = 'cabinet' if admin and data.get('scope') == 'cabinet' else 'personal'
        return json_out(create(desk, owner, data, scope))
    if route == 'skills/import':
        from .competences5632 import import_skill
        try:
            raw = base64.b64decode(str(data.get('content') or ''), validate=True)
        except ValueError:
            raise Stop('fichier_competence_illisible') from None
        scope = 'cabinet' if admin and data.get('scope') == 'cabinet' else 'personal'
        return json_out(import_skill(desk, owner, raw, str(data.get('filename') or ''), scope))
    if route == 'skills/enable':
        from .competences5632 import set_enabled
        return json_out(set_enabled(desk, owner, str(data.get('id') or ''), data.get('on') in (True, 'true', '1', 'on'), data.get('version') or None))
    if route == 'skills/copy':
        from .competences5632 import copy
        return json_out(copy(desk, owner, str(data.get('id') or '')))
    if route == 'skills/edit':
        from .competences5632 import edit
        return json_out(edit(desk, owner, str(data.get('id') or ''), str(data.get('instructions') or '')))
    if route == 'dp/start':
        from .procedure5632 import start
        paths = data.get('paths') if isinstance(data.get('paths'), list) else str(data.get('paths') or '').splitlines()
        return json_out(start(desk, owner, str(data.get('matter') or ''), paths, str(data.get('instruction') or '')))
    if route == 'dp/decide':
        from .procedure5632 import decide
        targets = data.get('targets') if isinstance(data.get('targets'), list) else [data.get('targets')] if data.get('targets') else []
        return json_out(decide(desk, owner, str(data.get('mission') or ''), str(data.get('key') or ''), str(data.get('action') or ''),
                               targets, str(data.get('note') or ''), data.get('revision')))
    if route == 'dp/apply':
        from .procedure5632 import apply
        return json_out(apply(desk, owner, str(data.get('mission') or '')))
    if route == 'dp/drafts':
        from .procedure5632 import drafts
        recipients = data.get('recipients') if isinstance(data.get('recipients'), list) else []
        if not recipients:
            for role_key in ('client', 'confrere'):
                if data.get(role_key):
                    recipients.append({'role': role_key, 'email': data[role_key]})
        return json_out(drafts(desk, owner, str(data.get('mission') or ''), recipients))
    if route.startswith('courrier/'):   # 5.6.32 : signature, police et règles de la messagerie
        from . import courrier5632 as c32
        if route == 'courrier/prefs':
            return json_out(c32.save_prefs(desk, data))
        if route == 'courrier/rule':
            return json_out(c32.save_rule(desk, data, _mail_folders(desk)))
        if route == 'courrier/rule/enable':
            return json_out(c32.set_enabled(desk, str(data.get('id') or ''), data.get('on') in (True, 'true', '1', 'on')))
        if route == 'courrier/rule/delete':
            return json_out(c32.delete_rule(desk, str(data.get('id') or '')))
        if route == 'courrier/apply':
            return json_out(c32.apply_moves(desk, include_old=data.get('all') in (True, 'true', '1', 'on')))
    if route == 'dossiers/create':   # clic explicite de l'avocat, dans CE dossier client seulement
        from . import dossiers5632, metier500
        from .document_projects import _dav
        m = metier500.require_matter(desk, str(data.get('matter') or ''))
        names = data.get('names') if isinstance(data.get('names'), list) else None
        return json_out(dossiers5632.create_missing(desk, m, _dav(desk), names))
    if route == 'synthese/build':
        job = desk.enqueue('digest_pieces5632', {'matter': str(data.get('matter') or '')}, priority=40)
        return json_out({'job': job, 'message': i18n.t('syn.queued')})
    raise Stop('route_inconnue')


# --------------------------------------------------------------------------------------------- fragments d'écran
def _form(route, body, submit, reload=False, extra=''):
    return ('<form class="f5632" data-m5632="%s"%s>%s<button type="submit">%s</button>%s<p class="f5632-out" role="status" aria-live="polite"></p></form>'
            % (e(route, quote=True), ' data-reload="1"' if reload else '', body, e(submit), extra))


def sources_section(desk):
    """Sources juridiques (GI17–GI22) : une carte par fournisseur — compte, clé, vérification, activation."""
    from .sources5632 import catalogue
    th = i18n.th
    cards = ''
    for p in catalogue(desk):
        test = p['last_test'] or {}
        levels = test.get('levels') or {}
        status = (th('src.state.enabled') if p['enabled'] else th('src.state.configured') if p['configured'] else th('src.state.none'))
        auth = ('<label>%s<input type="password" name="token" autocomplete="off" placeholder="%s"></label>' % (
            th('src.token'), th('guide.secret_set') if p['secret_configured'] else th('src.token_hint'))) if 'bearer' in p['auth'] else ''
        if p['auth'] == ['none', 'bearer']:
            auth = '<p class="f5632-hint">' + th('src.no_key_needed') + '</p>' + auth
        cards += ('<article class="s5632-card" data-provider="%s"><h3>%s <small>%s</small></h3><p>%s</p><p class="f5632-hint">%s</p>'
                  '<p class="s5632-links"><a href="%s" target="_blank" rel="noopener noreferrer">%s</a> · <a href="%s" target="_blank" rel="noopener noreferrer">%s</a></p>') % (
            e(p['id'], quote=True), e(p['name']), status, e(p['coverage']), e(p['note']), e(p['account'], quote=True), th('src.account'),
            e(p['help'], quote=True), th('src.help'))
        if levels:
            cards += '<p class="s5632-levels">%s</p>' % ' · '.join('%s : %s' % (th('src.level.' + k), e(str(v))) for k, v in levels.items() if k in ('connection', 'search', 'text'))
        cards += _form('admin/sources/connect', '<input type="hidden" name="provider" value="%s">%s<label class="f5632-check"><input type="checkbox" name="allow_external"%s> %s</label>' % (
            e(p['id'], quote=True), auth, ' checked' if p['configured'] else '', th('src.allow_external')), i18n.t('src.save'), reload=True)
        cards += '<div class="s5632-actions">%s%s</div></article>' % (
            _form('admin/sources/verify', '<input type="hidden" name="provider" value="%s">' % e(p['id'], quote=True), i18n.t('src.verify'), reload=True),
            _form('admin/sources/enable', '<input type="hidden" name="provider" value="%s"><input type="hidden" name="on" value="%s">' % (
                e(p['id'], quote=True), '0' if p['enabled'] else '1'), i18n.t('src.disable' if p['enabled'] else 'src.enable'), reload=True))
    return '<section class="s5632"><h2>%s</h2><p>%s</p><div class="s5632-grid">%s</div></section>' % (th('src.title'), th('src.intro'), cards)


def controle_section(prefix):
    th = i18n.th
    body = ('<label>%s<textarea name="text" rows="8" required placeholder="%s"></textarea></label>'
            '<label>%s<input type="date" name="date_utile"></label>') % (th('ctl.text'), th('ctl.text_hint'), th('ctl.date_utile'))
    return '<section class="c5632"><h2>%s</h2><p>%s</p>%s<div class="c5632-report" data-m5632-report></div></section>' % (
        th('ctl.title'), th('ctl.intro'), _form('controle/run', body, i18n.t('ctl.run')))


def veille_page(desk, auth, prefix, env, shell):
    from .web567 import actor
    from .veille5632 import items, state, card_html
    owner = actor(env)[0]
    th = i18n.th
    st = state(desk, owner)
    rows = items(desk, owner, ('new', 'saved', 'read'), 80)
    lis = ''.join(('<li data-status="%s"><a href="%s" target="_blank" rel="noopener noreferrer">%s</a><small>%s · %s · %s · %s</small>'
                   '<p>%s</p><p class="v5632-reason">%s</p><div class="v5632-item-actions">%s%s%s</div></li>') % (
        e(r['status'], quote=True), e(r['url'], quote=True), e(r['title']), e(r['theme']), e(r['source']), e(th('vj.kind.' + r['kind'])),
        e((r['published'] or r['decided'] or '')[:10] or i18n.t('vj.date_unknown')), e(r['summary'][:500]), e(r['reason']),
        _form('veille/mark', '<input type="hidden" name="id" value="%s"><input type="hidden" name="action" value="read">' % e(r['id'], quote=True), i18n.t('vj.mark_read'), reload=True),
        _form('veille/mark', '<input type="hidden" name="id" value="%s"><input type="hidden" name="action" value="saved">' % e(r['id'], quote=True), i18n.t('vj.save'), reload=True),
        _form('veille/mark', '<input type="hidden" name="id" value="%s"><input type="hidden" name="action" value="hidden">' % e(r['id'], quote=True), i18n.t('vj.hide'), reload=True))
        for r in rows)
    bar = ('<div class="v5632-actions"><button type="button" class="ax-btn" data-v5632="refresh">%s</button><a class="ax-btn ghost" href="%s">%s</a></div>') % (
        th('vj.refresh'), e(prefix + '/parametres?rubrique=veille', quote=True), th('vj.settings'))
    body = ('<div class="v5632"><h1>%s</h1>%s<section class="c530-card v5632-summary">%s</section><h2>%s</h2><ul class="v5632-items">%s</ul></div>') % (
        th('vj.title'), bar, card_html(desk, prefix, owner), th('vj.all_items', n=len(rows)), lis or '<li>' + th('vj.empty_list') + '</li>')
    return shell(i18n.t('vj.title'), body, prefix, auth['csrf'], '/veille')


def veille_settings(desk, env, prefix, admin=True):
    from .web567 import actor
    from .veille5632 import FEEDS, profile, usage, tavily_key_path
    owner = actor(env)[0]
    th = i18n.th
    p = profile(desk, owner)
    themes = '\n'.join('%s : %s%s' % (t['name'], ', '.join(t['keywords']), (' ; sauf : ' + ', '.join(t['exclusions'])) if t['exclusions'] else '') for t in p['themes'])
    feeds = ''.join('<label class="f5632-check"><input type="checkbox" name="feeds" value="%s"%s> %s</label>' % (f[0], ' checked' if f[0] in p['feeds'] else '', e(f[1]))
                    for f in FEEDS)
    days = ''.join('<label class="f5632-check"><input type="checkbox" name="days" value="%d"%s> %s</label>' % (
        i, ' checked' if i in p['days'] else '', e(i18n.t('date.days').split(',')[i])) for i in range(7))
    modes = ''.join('<option value="%s"%s>%s</option>' % (m, ' selected' if m == p['mode'] else '', th('vj.mode.' + m)) for m in ('daily', 'days', 'weekly', 'manual'))
    body = ('<label class="f5632-check"><input type="checkbox" name="enabled"%s> %s</label>'
            '<label>%s<textarea name="themes" rows="5" placeholder="%s">%s</textarea></label><fieldset><legend>%s</legend>%s</fieldset>'
            '<label>%s<textarea name="custom_feeds" rows="3" placeholder="https://…">%s</textarea></label>'
            '<label class="f5632-check"><input type="checkbox" name="connector"%s> %s</label><label class="f5632-check"><input type="checkbox" name="tavily"%s> %s</label>'
            '<div class="f5632-row"><label>%s<select name="mode">%s</select></label><label>%s<input type="time" name="hour" value="%s"></label>'
            '<label>%s<input name="tz" value="%s"></label></div><fieldset><legend>%s</legend>%s</fieldset>') % (
        ' checked' if p['enabled'] else '', th('vj.enabled'), th('vj.themes'), th('vj.themes_hint'), e(themes), th('vj.feeds'), feeds,
        th('vj.custom_feeds'), e('\n'.join(p['custom_feeds'])), ' checked' if p['connector'] else '', th('vj.use_connector'),
        ' checked' if p['tavily'] else '', th('vj.use_tavily'), th('vj.frequency'), modes, th('vj.hour'), e(p['hour'], quote=True), th('vj.tz'), e(p['tz'], quote=True),
        th('vj.days'), days)
    out = '<section class="v5632-settings"><h2>%s</h2><p>%s</p>%s' % (th('vj.settings_title'), th('vj.settings_intro'), _form('veille/profile', body, i18n.t('vj.save_settings'), reload=True))
    if admin:
        used = usage(desk)
        out += ('<h3>Tavily</h3><p>%s</p><p><a href="https://app.tavily.com" target="_blank" rel="noopener noreferrer">%s</a></p>' % (th('vj.tavily_intro'), th('vj.tavily_get')) +
                '<p class="f5632-hint">%s</p>' % th('vj.tavily_usage', calls=used['calls'], uncertain=used['uncertain']) +
                _form('admin/veille/tavily', '<label>%s<input type="password" name="key" autocomplete="off" placeholder="%s"></label>' % (
                    th('vj.tavily_key'), th('guide.secret_set') if tavily_key_path(desk) else 'tvly-…'), i18n.t('vj.tavily_save'), reload=True))
    return out + '</section>'


def skills_page(desk, auth, prefix, env, shell):
    from .web567 import actor
    from .competences5632 import listing, auto_mode
    owner, role = actor(env)
    th = i18n.th
    rows = listing(desk, owner)
    cards = ''
    for s in rows:
        req = ''
        if s['missing']:
            req = '<p class="k5632-missing">%s</p>' % th('sk.missing', what=', '.join(s['missing']))
        elif s['verified_missing']:
            req = '<p class="k5632-partial">%s</p>' % th('sk.partial', what=', '.join(s['verified_missing']))
        cards += ('<article class="k5632-card" data-enabled="%s"><h3>%s <small>v%s · %s</small></h3><p>%s</p><p class="f5632-hint">%s%s%s</p>%s'
                  '<div class="k5632-actions">%s%s<a class="k5632-export" href="%s">%s</a></div></article>') % (
            '1' if s['enabled'] else '0', e(s['name']), e(s['version']), th('sk.origin.' + s['origin']), e(s['description']),
            e(s['author']), (' · ' + e(s['license'])) if s['license'] else '', (' · <a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(s['source'], quote=True), th('sk.source'))) if s['source'] else '',
            req, _form('skills/enable', '<input type="hidden" name="id" value="%s"><input type="hidden" name="on" value="%s">' % (e(s['id'], quote=True), '0' if s['enabled'] else '1'),
                       i18n.t('sk.disable' if s['enabled'] else 'sk.enable'), reload=True),
            _form('skills/copy', '<input type="hidden" name="id" value="%s">' % e(s['id'], quote=True), i18n.t('sk.copy'), reload=True),
            e(prefix + '/api440/m5632/skills/export?id=' + s['id'], quote=True), th('sk.export'))
    create = ''.join('<label>%s<textarea name="%s" rows="%d"%s></textarea></label>' % (th('sk.f.' + f), f, 1 if f == 'nom' else 3, ' required' if f in ('nom', 'objectif') else '')
                     for f in ('nom', 'objectif', 'situations', 'etapes', 'resultat', 'controles', 'exemples', 'limites'))
    body = ('<div class="k5632"><h1>%s</h1><p>%s</p>%s<div class="k5632-grid">%s</div><section><h2>%s</h2><p>%s</p>%s</section>'
            '<section><h2>%s</h2><p>%s</p>%s</section></div>') % (
        th('sk.title'), th('sk.intro'),
        (_form('admin/skills/auto', '<input type="hidden" name="on" value="%s">' % ('0' if auto_mode(desk) else '1'), i18n.t('sk.auto_off' if auto_mode(desk) else 'sk.auto_on'), reload=True)
         if role == 'administrateur' else ''),
        cards, th('sk.create'), th('sk.create_intro'), _form('skills/create', create, i18n.t('sk.create_btn'), reload=True),
        th('sk.import'), th('sk.import_intro'), _form('skills/import', '<input type="file" data-m5632-file accept=".md,.zip" required>', i18n.t('sk.import_btn'), reload=True))
    return shell(i18n.t('sk.title'), body, prefix, auth['csrf'], '/competences')


def procedure_panel(desk, prefix, m):
    """Panneau de la page Dossier (DP01) : choisir les documents de procédure, lire, proposer, inscrire, préparer les brouillons."""
    from .index import DocumentIndex
    th = i18n.th
    try:
        rows = DocumentIndex(desk.c['state_dir']).db.execute('SELECT path FROM docs WHERE matter=? ORDER BY modified DESC LIMIT 60', (m['id'],)).fetchall()
    except Exception:   # noqa: BLE001
        rows = []
    options = ''.join('<label class="f5632-check"><input type="checkbox" name="paths" value="%s"> %s</label>' % (e(r[0], quote=True), e(r[0].rsplit('/', 1)[-1])) for r in rows[:40])
    body = ('<input type="hidden" name="matter" value="%s"><fieldset class="d5632-docs"><legend>%s</legend>%s</fieldset>'
            '<label>%s<input name="instruction" placeholder="%s"></label>') % (
        e(m['id'], quote=True), th('dp.choose'), options or '<p>' + th('dp.no_docs') + '</p>', th('dp.instruction'), th('dp.instruction_hint'))
    return ('<section class="d5632" id="d5632"><h2>%s</h2><p>%s</p>%s<div data-m5632-mission></div></section>' % (
        th('dp.title'), th('dp.intro'), _form('dp/start', body, i18n.t('dp.read'))))


def working_folders_panel(desk, prefix, m):
    """5.6.32 : dossiers de travail de CE dossier (noms résolus avec les parties adverses) ; création des absents sur clic seulement."""
    from . import dossiers5632
    try:
        rows = dossiers5632.resolve(desk, m)
    except Exception:   # noqa: BLE001
        return ''
    if not rows:
        return ''
    items = ''.join('<li><strong>%s</strong> <span class="d27-badge">%s</span>%s</li>' % (
        e(r['name'] or r['pattern']), e(r['label']), (' <span class="d27-muted">%s</span>' % e(r['reason'])) if r['reason'] else '') for r in rows)
    body = '<input type="hidden" name="matter" value="%s">' % e(m['id'], quote=True)
    return ('<section class="d5632 d5632-folders" id="d5632-folders"><h2>Dossiers de travail</h2>'
            '<p class="d27-note">Réglés dans <a href="%s">Mon style › Réglages</a>. Les noms sont complétés avec les parties adverses du dossier '
            '(registre des parties, puis fiche du dossier).</p><ul class="d27-list">%s</ul>%s</section>') % (
        e(prefix + '/mon-style', quote=True), items, _form('dossiers/create', body, 'Créer dans Nextcloud les dossiers absents'))


def _mail_folders(desk):
    """Noms IMAP des dossiers de la messagerie (None si la messagerie ne répond pas : le nom saisi est alors accepté tel quel)."""
    try:
        from .boite5625 import folders
        return [r['name'] for r in folders(desk)['folders']]
    except Exception:   # noqa: BLE001
        return None


def courrier_section(desk, prefix):
    """5.6.32 : Paramètres › Messagerie — signature, police, règles « Cci » et « Déplacer », derniers déplacements."""
    from . import courrier5632 as c32
    p = c32.prefs(desk)
    fonts = ''.join('<option value="%s"%s data-font="%s">%s</option>' % (k, ' selected' if k == p['font'] else '', e(v[1], quote=True), e(v[0]))
                    for k, v in c32.FONTS.items())
    sizes = ''.join('<option value="%d"%s>%d pt</option>' % (s, ' selected' if s == p['size'] else '', s) for s in c32.SIZES)
    signs = ''.join('<label class="f5632-check"><input type="checkbox" name="sign_%s"%s> %s</label>' % (m, ' checked' if p['sign'][m] else '', e(c32.MODES[m]))
                    for m in ('nouveau', 'repondre', 'transferer'))
    editor = ('<div class="m5632-sigbar" role="toolbar" aria-label="Mise en forme de la signature">'
              '<button type="button" data-m5632-cmd="bold" aria-label="Gras"><b>G</b></button><button type="button" data-m5632-cmd="italic" aria-label="Italique"><i>I</i></button>'
              '<button type="button" data-m5632-cmd="underline" aria-label="Souligné"><u>S</u></button><button type="button" data-m5632-cmd="createLink" aria-label="Lien">🔗</button></div>'
              '<div class="m5632-sig" contenteditable="true" role="textbox" aria-multiline="true" aria-label="Signature" data-m5632-html="signature_html" data-m5632-style="%s">%s</div>') % (
        e(c32.font_css(desk), quote=True), p['signature_html'])   # déjà nettoyée à l'enregistrement (liste fermée de balises)
    prefs_form = _form('courrier/prefs', ('<fieldset><legend>Signature</legend>%s<p class="f5632-hint">Ajoutée à :</p>%s</fieldset>'
                                          '<div class="f5632-row"><label>Police d’écriture<select name="font" data-m5632-font>%s</select></label>'
                                          '<label>Taille<select name="size">%s</select></label></div>') % (editor, signs, fonts, sizes),
                       'Enregistrer la signature et la police', reload=True)
    folders = _mail_folders(desk)
    if folders:
        folder_field = '<select name="folder" required>%s</select>' % ''.join(
            '<option value="%s">%s</option>' % (e(f, quote=True), e(f)) for f in folders if f.upper() != 'INBOX')
    else:
        folder_field = '<input name="folder" required placeholder="INBOX.Clients"><span class="f5632-hint">Messagerie injoignable : saisissez le nom exact du dossier.</span>'
    fields = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in c32.FIELDS.items())
    conds = ''.join('<div class="f5632-row"><label>Si %s<select name="cond_field_%d">%s</select></label><label>contient<input name="cond_value_%d" maxlength="200"%s></label></div>' % (
        'aussi' if i else '', i, fields, i, ' required' if i == 0 else '') for i in range(3))
    move_form = _form('courrier/rule', '<input type="hidden" name="kind" value="deplacer">%s<label>Conditions<select name="match"><option value="all">toutes remplies</option>'
                      '<option value="any">au moins une remplie</option></select></label><label>Déplacer dans le dossier%s</label>' % (conds, folder_field),
                      'Ajouter la règle de classement', reload=True)
    scopes = ''.join('<label class="f5632-check"><input type="checkbox" name="scope_%s" checked> %s</label>' % (m, e(v)) for m, v in c32.MODES.items())
    bcc_form = _form('courrier/rule', '<input type="hidden" name="kind" value="cci"><label>Adresses en copie cachée (séparées par des virgules)'
                     '<input name="addresses" required placeholder="archives@cabinet.exemple.fr"></label><fieldset><legend>Pour</legend>%s</fieldset>' % scopes,
                     'Ajouter la copie cachée automatique', reload=True)
    rows = ''
    for r in c32.rules(desk):
        rows += ('<li class="m5632-rule" data-enabled="%s"><span>%s%s</span><div class="m5632-rule-actions">%s%s</div></li>') % (
            '1' if r['enabled'] else '0', e(c32.describe(r)), '' if r['enabled'] else ' <em>(suspendue)</em>',
            _form('courrier/rule/enable', '<input type="hidden" name="id" value="%s"><input type="hidden" name="on" value="%s">' % (e(r['id'], quote=True), '0' if r['enabled'] else '1'),
                  'Suspendre' if r['enabled'] else 'Réactiver', reload=True),
            _form('courrier/rule/delete', '<input type="hidden" name="id" value="%s">' % e(r['id'], quote=True), 'Supprimer', reload=True))
    recent = ''.join('<li>%s · %s → %s%s</li>' % (e(x['at'][:16].replace('T', ' ')), e(x['subject'] or '(sans objet)'), e(x['target']),
                                                   '' if x['state'] == 'deplace' else ' <em>(%s)</em>' % e(x['state'])) for x in c32.log(desk, 15))
    apply_form = _form('courrier/apply', '<label class="f5632-check"><input type="checkbox" name="all"> Inclure les messages de plus de 7 jours</label>',
                       'Appliquer les règles maintenant')
    return ('<section class="m5632"><h2>Messagerie</h2><p>Réglages de « Courriels › Boîte de réception » : la signature et la police s’appliquent aux messages '
            'que vous écrivez ici ; la copie cachée automatique s’applique aussi aux brouillons préparés par l’agent.</p>'
            '<h3>Signature et police</h3>%s<h3>Règles</h3><ul class="m5632-rules">%s</ul>'
            '<h4>Copie cachée à chaque message</h4>%s<h4>Classement automatique des courriels reçus</h4>'
            '<p class="f5632-hint">Un courriel n’est déplacé qu’après sa lecture par l’agent (ou 15 minutes après son arrivée) ; contrôle toutes les 5 minutes. '
            'Rien n’est supprimé.</p>%s%s<h4>Derniers déplacements</h4><ul class="m5632-log">%s</ul></section>') % (
        prefs_form, rows or '<li>Aucune règle.</li>', bcc_form, move_form, apply_form, recent or '<li>Aucun déplacement.</li>')

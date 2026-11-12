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
        if route == 'admin/sources/oauth/retour':   # 5.6.97 (J06) : retour du fournisseur (état à usage unique lié à la session)
            if not admin:
                raise Stop('role_insuffisant')
            return oauth_retour5697(env, desk, prefix, args, owner)
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
                                              data.get('allow_external') in (True, 'true', '1', 'on'), data.get('revision')), 'message': i18n.t('src.saved')})
    if route == 'admin/sources/verify':   # 5.6.97 (J06) : premier bouton initialize + tools/list ; second bouton : recherche fictive
        from .sources5632 import verify
        out = verify(desk, str(data.get('provider') or ''), str(data.get('profondeur') or 'connexion'))
        return json_out({**out, 'message': out.get('message') or ''})
    if route == 'admin/sources/enable':
        from .sources5632 import enable
        return json_out({'connector': enable(desk, str(data.get('provider') or ''), data.get('on') in (True, 'true', '1', 'on'))})
    if route.startswith('admin/sources/') and route[len('admin/sources/'):] in SOURCES_5697:   # 5.6.97 : OAuth MCP, synchronisation
        return sources_api5697(env, desk, auth, prefix, route[len('admin/sources/'):], data, owner)
    if route == 'admin/veille/tavily':
        from .veille5632 import save_tavily_key
        return json_out(save_tavily_key(desk, data.get('key')))
    if route == 'admin/recherche_publique':   # 5.6.96 (R02) : interrupteur de l'assistant (désactivé par défaut, réversible)
        from .recherche_publique5696 import autoriser
        return json_out({'reglage': autoriser(desk, data.get('enabled') in (True, 'true', '1', 'on'), owner), 'message': i18n.t('rp.saved')})
    if route == 'admin/recherche_publique/test':   # 5.6.96 (R02) : disponibilité vérifiée sans appel réseau ni consommation
        from .recherche_publique5696 import disponibilite
        return json_out(disponibilite(desk, owner))
    if route == 'admin/skills/generale':
        from .competences5632 import listing
        from .competences5643 import GENERAL_SETTING
        chosen = str(data.get('id') or '')
        if chosen and chosen not in {s['id'] for s in listing(desk, owner) if s['enabled']}:
            raise Stop('competence_introuvable')
        desk.setting(GENERAL_SETTING, chosen)
        desk.audit('competence5686_generale', {'id': chosen, 'owner': owner})
        return json_out({'message': i18n.t('sk.general_saved')})
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
        return json_out(start(desk, owner, str(data.get('matter') or ''), paths, str(data.get('instruction') or ''), acte=data.get('acte') if isinstance(data.get('acte'), dict) else None))
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


def _lien(url, label):
    """Lien vers un site officiel : autre onglet, noopener noreferrer ; rien si l'adresse n'est pas en HTTPS."""
    if not str(url or '').startswith('https://'):
        return ''
    return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(url, quote=True), label)


def _date_courte(value):
    return e(str(value or '')[:16].replace('T', ' '))


def carte_source(p, contexte='parametres', session=''):
    """5.6.97 (J06) : carte commune aux Paramètres et au guide d'installation. L'action dépend du mode de la fiche : « Obtenir une
    clé API », « Connecter le compte », « Aucune clé nécessaire », « Adaptateur AxiorHub » ; une fiche conditionnelle n'affiche que
    « En savoir plus », sans aucun champ qui laisserait croire que la connexion est prête."""
    th = i18n.th
    mode = p['mode']
    pid = e(p['id'], quote=True)
    hidden = '<input type="hidden" name="provider" value="%s">' % pid
    test = p['last_test'] or {}
    status = (th('src97.mode.a_verifier') if mode == 'a_verifier' else th('src.state.enabled') if p['enabled']
              else th('src.state.configured') if p['configured'] else th('src.state.none'))
    badges = ''.join('<span class="s5697-badge">%s</span>' % th('src97.cap.' + c) for c in p['capabilities'])
    out = ('<article class="s5632-card s5697-card" data-provider="%s" data-mode="%s"><h3>%s <small>%s</small></h3>'
           '<p class="s5697-badges">%s<span class="s5697-mode">%s</span></p><p>%s</p><p class="f5632-hint">%s</p>') % (
        pid, e(mode, quote=True), e(p['name']), status, badges, th('src97.mode.' + mode), e(p['coverage']), e(p['note']))
    if p.get('note_5697'):
        out += '<p class="f5632-hint">%s</p>' % e(p['note_5697'])
    if mode == 'integre':
        out += '<p class="f5632-hint">%s</p>' % th('src97.adapter_note')
        index = p.get('index') or {}
        out += '<p class="s5697-index">%s</p>' % (th('src97.index_present', date=_date_courte(index.get('date')), n=index.get('articles'),
                                                                origine=index.get('origine')) if index.get('present') else th('src97.index_absent'))
    if p.get('decisions'):
        out += '<p class="s5697-decisions">%s %s</p>' % (th('src97.decisions.' + p['decisions'].get('etat', 'aucune')),
                                                        _lien(p['decisions'].get('lien'), th('src97.decisions_link')))
    principal = {'cle': (p['access_url'], th(p['acces_libelle']) if p.get('acces_libelle') else th('src97.get_key')),
                 'a_verifier': (p['help'] or p['access_url'], th('src97.more'))}.get(mode)
    if mode == 'a_verifier' and p.get('acces_libelle') and p['access_url']:   # ex. « Demander une clé CanLII » : lien visible, fiche conditionnelle
        principal = (p['access_url'], th(p['acces_libelle']))
    liens = [x for x in ((_lien(*principal) if principal else ''), _lien(p['help'], th('src.help')) if not principal or principal[0] != p['help'] else '',
                         _lien(p['terms_url'], th('src97.terms'))) if x]
    if liens:
        out += '<p class="s5632-links">%s</p>' % ' · '.join(liens)
    if p.get('liens_a_confirmer') or p['id'] in ('eulex', 'datagouv', 'contract_factory', 'patriboum'):
        out += '<p class="f5632-hint">%s</p>' % th('src97.links_to_confirm')
    if mode == 'a_verifier':
        return out + '<p class="s5697-conditionnel" role="note">%s</p></article>' % th('src97.conditional')
    if test:
        out += '<p class="s5697-test">%s</p>' % th('src97.last_test', date=_date_courte(test.get('at')), status=th('src97.status.' + str(test.get('status') or 'error')))
        caps = test.get('capacites') or {}
        if caps:
            out += '<ul class="s5697-caps">%s</ul>' % ''.join('<li>%s : %s</li>' % (th('src97.cap.' + c), th('src97.cap_state.' + v)) for c, v in caps.items())
        levels = test.get('levels') or {}
        if levels:
            out += '<p class="s5632-levels">%s</p>' % ' · '.join('%s : %s' % (th('src.level.' + k), e(str(v))) for k, v in levels.items() if k in ('connection', 'search', 'text'))
    else:
        out += '<p class="s5697-test">%s</p>' % th('src97.never_tested')
    if p.get('candidate_pending'):
        out += '<p class="f5632-hint" role="status">%s</p>' % th('src97.candidate')
    revision = '<input type="hidden" name="revision" value="%d">' % int(p.get('revision') or 0)
    checked = ' checked' if p.get('allow_external') else ''   # l'accord affiché est la valeur ENREGISTRÉE
    accord = th('src97.use_local') if mode == 'integre' else th('src.allow_external')
    if mode == 'oauth':
        oauth = p.get('oauth') or {}
        retour = 'guide:' + session if contexte == 'guide' and session else 'parametres'
        if oauth.get('etat') == 'connecte':
            out += '<p class="s5697-droits">%s</p>' % th('src97.oauth_rights', droits=', '.join(oauth.get('droits') or []) or th('src97.oauth_no_scope'))
            out += _form('admin/sources/connect', hidden + revision + '<label class="f5632-check"><input type="checkbox" name="allow_external"%s> %s</label>' % (
                checked, accord), i18n.t('src.save'), reload=True)
            out += _form('admin/sources/oauth/revoke', hidden, i18n.t('src97.oauth_disconnect'), reload=True)
        else:
            if oauth.get('etat'):
                out += '<p class="f5632-hint">%s</p>' % th('src97.oauth_state.' + oauth['etat'])
            out += _form('admin/sources/oauth/start', hidden + '<input type="hidden" name="retour" value="%s">' % e(retour, quote=True),
                         i18n.t('src97.mode.oauth'))
    else:
        champ = ''
        if mode == 'cle':
            champ = '<label>%s<input type="password" name="token" autocomplete="off" placeholder="%s"></label>' % (
                th('src.token'), th('src97.key_kept') if p['secret_configured'] else th('src.token_hint'))
        out += _form('admin/sources/connect', hidden + revision + champ + '<label class="f5632-check"><input type="checkbox" name="allow_external"%s> %s</label>' % (
            checked, accord), i18n.t('src.save'), reload=True)
    if p['configured'] and (mode != 'oauth' or (p.get('oauth') or {}).get('etat') == 'connecte'):
        out += '<div class="s5632-actions">%s%s%s</div>' % (
            _form('admin/sources/verify', hidden + '<input type="hidden" name="profondeur" value="connexion">', i18n.t('src97.test_connexion'), reload=True),
            _form('admin/sources/verify', hidden + '<input type="hidden" name="profondeur" value="complet">', i18n.t('src97.test_complet'), reload=True),
            _form('admin/sources/enable', hidden + '<input type="hidden" name="on" value="%s">' % ('0' if p['enabled'] else '1'),
                  i18n.t('src.disable' if p['enabled'] else 'src.enable'), reload=True))
    if mode == 'integre':
        out += _synchronisation_html(p, hidden)
    return out + '</article>'


def _synchronisation_html(p, hidden):
    """Synchronisation réseau : séparée, désactivée par défaut, affichée ; fermée tant que le fonds n'est pas qualifié."""
    th = i18n.th
    sync = p.get('synchronisation') or {}
    out = '<details class="s5697-sync"><summary>%s</summary>' % th('src97.sync_title')
    if not sync.get('qualifiee'):
        return out + '<p>%s</p></details>' % th('src97.sync_closed', raison=sync.get('raison_fermee') or '')
    out += '<p>%s</p>' % th('src97.sync_on' if sync.get('active') else 'src97.sync_off')
    derniere = sync.get('derniere') or {}
    if derniere:
        out += '<p role="status">%s</p>' % th('src97.sync_last', resultat=th('src97.sync_result.' + str(derniere.get('resultat') or 'echec')),
                                               date=_date_courte(derniere.get('fin')), erreur=(' — ' + derniere['erreur']) if derniere.get('erreur') else '')
    out += _form('admin/sources/sync/reglage', hidden + '<input type="hidden" name="active" value="%s">' % ('0' if sync.get('active') else '1'),
                 i18n.t('src97.sync_disable' if sync.get('active') else 'src97.sync_enable'), reload=True)
    if sync.get('active'):
        out += _form('admin/sources/sync/run', hidden + '<label>%s<input type="text" name="identifiants" autocomplete="off" maxlength="600"></label>' % th('src97.sync_ids'),
                     i18n.t('src97.sync_run'), reload=True)
    return out + '</details>'


def sources_section(desk, contexte='parametres', session=''):
    """Sources juridiques : cartes communes groupées par zone (France, Union européenne, Allemagne, Espagne, Angleterre et pays de
    Galles, Canada fédéral, Québec, entreprises et données publiques). Même fonction pour les Paramètres et le guide (J06)."""
    from .sources5632 import ZONES_GUIDE, catalogue
    th = i18n.th
    fiches = catalogue(desk)
    groupes = ''
    for zone, cle in ZONES_GUIDE:
        cartes = ''.join(carte_source(p, contexte, session) for p in fiches if p['zone_guide'] == zone)
        if cartes:
            groupes += '<section class="s5697-zone" data-zone="%s"><h3>%s</h3><div class="s5632-grid">%s</div></section>' % (
                e(zone, quote=True), th('src97.' + cle), cartes)
    intro = th('src97.cabinet_choice') if contexte == 'guide' else th('src.intro')
    return '<section class="s5632 s5697"><h2>%s</h2><p>%s</p>%s</section>' % (th('src.title'), intro, groupes)


SOURCES_5697 = frozenset({'oauth/start', 'oauth/revoke', 'sync/reglage', 'sync/run'})


def sources_api5697(env, desk, auth, prefix, action, data, owner):
    """5.6.97 : routes d'administration des sources (rôle administrateur et CSRF contrôlés par l'appelant)."""
    from .web440 import json_out
    pid = str(data.get('provider') or '')
    if action == 'oauth/start':
        import re
        from . import oauth_mcp5697
        from .office440 import public_base
        retour = str(data.get('retour') or 'parametres')
        if not re.fullmatch(r'parametres|guide:[A-Za-z0-9_-]{1,80}', retour):
            retour = 'parametres'
        out = oauth_mcp5697.demarrer(desk, owner, oauth_mcp5697.marqueur_session(env), pid,
                                     public_base(desk, auth).rstrip('/') + '/api440/m5632/admin/sources/oauth/retour', retour)
        return json_out({**out, 'message': i18n.t('src97.oauth_redirect')})
    if action == 'oauth/revoke':
        from . import oauth_mcp5697
        return json_out({**oauth_mcp5697.revoquer(desk, pid, owner), 'message': i18n.t('src97.oauth_revoked')})
    from . import mcp_internationaux5697 as mi
    if action == 'sync/reglage':
        return json_out({'synchronisation': mi.regler_synchronisation(desk, pid, data.get('active') in (True, 'true', '1', 'on'), owner),
                         'message': i18n.t('src.saved')})
    if action == 'sync/run':
        import re
        idents = [x for x in re.split(r'[\s,;]+', str(data.get('identifiants') or '')) if x][:21]
        out = mi.synchroniser(desk, pid, idents, owner=owner)
        return json_out({**out, 'message': i18n.t('src97.sync_result.' + out['synchronisation']['resultat'])})
    raise Stop('route_inconnue')


def oauth_retour5697(env, desk, prefix, args, owner):
    """Retour OAuth : enregistre le jeton puis renvoie au guide ou aux Paramètres avec un état lisible (jamais le code ni le jeton)."""
    from urllib.parse import quote
    from . import oauth_mcp5697
    cible = '/parametres?rubrique=recherche'
    try:
        out = oauth_mcp5697.terminer(desk, owner, oauth_mcp5697.marqueur_session(env), args)
        if out['retour'].startswith('guide:'):
            cible = '/guide-installation?session=%s&etape=sources' % quote(out['retour'][len('guide:'):])
        cible += '&oauth=ok&source=' + quote(out['provider'])
    except Stop as ex:
        cible += '&oauth=erreur&code=' + quote(str(ex).split(':')[0][:60])
    return {'status': '303 See Other', 'kind': 'text/plain; charset=utf-8', 'body': b'', 'headers': [('Location', prefix + cible)]}


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
    from .veille5632 import FEEDS, propose, usage, tavily_key_path, state, INTERVALLE_MIN, INTERVALLE_MAX
    owner = actor(env)[0]
    th = i18n.th
    p = propose(desk, owner)   # 5.6.96 (I05) : une veille jamais réglée se voit proposer la routine de trois jours

    def lignes(domaine):
        return '\n'.join('%s : %s%s' % (t['name'], ', '.join(t['keywords']), (' ; sauf : ' + ', '.join(t['exclusions'])) if t['exclusions'] else '')
                         for t in p['themes'] if t['domaine'] == domaine)
    themes = lignes('juridique')
    feeds = ''.join('<label class="f5632-check"><input type="checkbox" name="feeds" value="%s"%s> %s</label>' % (f[0], ' checked' if f[0] in p['feeds'] else '', e(f[1]))
                    for f in FEEDS)
    days = ''.join('<label class="f5632-check"><input type="checkbox" name="days" value="%d"%s> %s</label>' % (
        i, ' checked' if i in p['days'] else '', e(i18n.t('date.days').split(',')[i])) for i in range(7))
    modes = ''.join('<option value="%s"%s>%s</option>' % (m, ' selected' if m == p['mode'] else '', th('vj.mode.' + m)) for m in ('interval', 'daily', 'days', 'weekly', 'manual'))
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
    # 5.6.96 (I05) : domaine économique saisi séparément, intervalle en jours calendaires ; état du dernier et du prochain passage
    body += ('<label>%s<textarea name="themes_eco" rows="3" placeholder="%s">%s</textarea></label>'
             '<label>%s<input type="number" name="interval_days" min="%d" max="%d" value="%d"></label>') % (
        th('vj.themes_eco'), th('vj.themes_eco_hint'), e(lignes('economique')), th('vj.interval_days'), INTERVALLE_MIN, INTERVALLE_MAX,
        int(p.get('interval_days') or 3))
    st = state(desk, owner)
    etat = '<p class="f5632-hint">%s</p>' % (th('vj.next_run', when=st['next_run'][:16].replace('T', ' ')) if st['next_run'] else th('vj.no_next_run'))
    if st['last_status']:
        etat += '<p class="f5632-hint">%s</p>' % th('vj.last_run', when=st['last_finished'][:16].replace('T', ' '), status=i18n.t('vj.status.' + st['last_status']))
    if st['attente']:
        etat += '<p class="f5632-hint">%s</p>' % th('vj.waiting.' + st['attente'])
    out = '<section class="v5632-settings"><h2>%s</h2><p>%s</p><p><strong>%s</strong> — %s</p>%s%s' % (
        th('vj.settings_title'), th('vj.settings_intro'), th('vj.routine'), th('vj.routine_proposed'), etat,
        _form('veille/profile', body, i18n.t('vj.save_settings'), reload=True))
    if admin:
        used = usage(desk)
        out += ('<h3>Tavily</h3><p>%s</p><p><a href="https://app.tavily.com" target="_blank" rel="noopener noreferrer">%s</a></p>' % (th('vj.tavily_intro'), th('vj.tavily_get')) +
                '<p class="f5632-hint">%s</p>' % th('vj.tavily_usage', calls=used['calls'], uncertain=used['uncertain']) +
                _form('admin/veille/tavily', '<label>%s<input type="password" name="key" autocomplete="off" placeholder="%s"></label>' % (
                    th('vj.tavily_key'), th('guide.secret_set') if tavily_key_path(desk) else 'tvly-…'), i18n.t('vj.tavily_save'), reload=True))
        out += recherche_publique_section(desk)   # 5.6.96 (R02) : même clé, plafond partagé avec la veille
    return out + '</section>'


def recherche_publique_section(desk):
    """5.6.96 (R02) : recherche publique dans l'assistant (administrateur) : interrupteur désactivé par défaut, quota consommé, test de
    disponibilité sans appel, journal des appels (date, origine, nombre de résultats, statut ; jamais le texte d'une requête)."""
    from . import recherche_publique5696 as public
    th = i18n.th
    q = public.quota(desk)
    statuts = ('ok', 'vide', 'erreur', 'incertain')
    lignes = ''.join('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td></tr>' % (
        e(str(x.get('at') or '')[:16].replace('T', ' ')), th('rp.profil.' + ('veille' if x.get('profil') == 'veille' else 'public')), int(x.get('resultats') or 0),
        th('rp.statut.' + (x.get('statut') if x.get('statut') in statuts else 'erreur'))) for x in public.journal(desk, 30))
    journal = ('<table class="vf-table"><caption>%s</caption><thead><tr><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th><th scope="col">%s</th></tr></thead>'
               '<tbody>%s</tbody></table>' % (th('rp.journal'), th('rp.col_date'), th('rp.col_profil'), th('rp.col_results'), th('rp.col_status'), lignes)
               if lignes else '<p class="f5632-hint">%s</p>' % th('rp.journal_empty'))
    return ('<h3>%s</h3><p>%s</p>' % (th('rp.title'), th('rp.intro')) +
            _form('admin/recherche_publique', '<label class="f5632-check"><input type="checkbox" name="enabled"%s> %s</label>' % (' checked' if q['enabled'] else '', th('rp.enable')),
                  i18n.t('rp.save'), reload=True) +
            '<p class="f5632-hint">%s</p>' % th('rp.quota', public=q['public'], plafond_public=q['plafond_public'], total=q['total'], plafond_partage=q['plafond_partage'],
                                                 incertains=q['incertains']) +
            _form('admin/recherche_publique/test', '', i18n.t('rp.test')) + journal)


def _package_html(s):
    """5.6.83 : droits déclarés, ressources conservées et décision pour chaque script : la carte ne vaut pas test d'exécution."""
    if s['origin'] != 'import':
        return ''
    rights = s.get('rights') or {}
    labels = {'service': 'remplacé par un service', 'indisponible': 'indisponible'}
    scripts = s.get('scripts') or []
    lines = ''.join('<li><code>%s</code> : %s%s</li>' % (e(x.get('path', '')), e(labels.get(x.get('decision'), x.get('decision', ''))), (' — ' + e(x['reason'])) if x.get('reason') else '') for x in scripts[:30])
    return ('<details class="k5632-package"><summary>Paquet : %d ressource(s), %d script(s)</summary><p>Droits : %s%s</p>%s%s</details>') % (
        s.get('resources') or 0, len(scripts), e(rights.get('declared') or 'non déclarés'), ' — usage commercial non autorisé par défaut' if rights.get('non_commercial') else '',
        '<ul>%s</ul>' % lines if lines else '',
        '<p class="k5632-missing">Références absentes : réimportez l’archive d’origine ; rien n’est reconstitué.</p>' if s.get('a_reimporter') else '')


def _general_form(desk, rows):
    """5.6.86 : compétence suivie pour les réponses aux courriels, la discussion et les documents demandés, quel que soit le fournisseur d'IA."""
    from .competences5643 import general_skill
    active = [s for s in rows if s['enabled']]
    current = general_skill(desk, active)
    options = '<option value="">%s</option>' % i18n.th('sk.general_none') + ''.join('<option value="%s"%s>%s</option>' % (
        e(s['id'], quote=True), ' selected' if current and s['id'] == current['id'] else '', e(s['name'])) for s in active)
    return '<p class="f5632-hint">%s</p>%s' % (i18n.th('sk.general_hint'), _form('admin/skills/generale', '<label>%s<select name="id">%s</select></label>' % (i18n.th('sk.general'), options), i18n.t('sk.general_save'), reload=True))


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
        req += _package_html(s)
        cards += ('<article class="k5632-card" data-enabled="%s"><h3>%s <small>v%s · %s</small></h3><p>%s</p><p class="f5632-hint">%s%s%s</p>%s'
                  '<div class="k5632-actions">%s%s<a class="k5632-export" href="%s">%s</a></div></article>') % (
            '1' if s['enabled'] else '0', e(s['name']), e(s['version']), th('sk.origin.' + s['origin']), e(s['description']),
            e(s['author']), (' · ' + e(s['license'])) if s['license'] else '', (' · <a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(s['source'], quote=True), th('sk.source'))) if s['source'] else '',
            req, _form('skills/enable', '<input type="hidden" name="id" value="%s"><input type="hidden" name="on" value="%s">' % (e(s['id'], quote=True), '0' if s['enabled'] else '1'),
                       i18n.t('sk.disable' if s['enabled'] else 'sk.enable'), reload=True),
            _form('skills/copy', '<input type="hidden" name="id" value="%s">' % e(s['id'], quote=True), i18n.t('sk.copy'), reload=True),
            e(prefix + '/api440/m5632/skills/export?id=' + s['id'], quote=True), th('sk.export'))
    from .liaisons5683 import overview
    capacities = ''.join('<li>%s : <strong>%s</strong></li>' % (e(c['capacite'].replace('_', ' ')), e(c['libelle'])) for c in overview(desk))
    create = ''.join('<label>%s<textarea name="%s" rows="%d"%s></textarea></label>' % (th('sk.f.' + f), f, 1 if f == 'nom' else 3, ' required' if f in ('nom', 'objectif') else '')
                     for f in ('nom', 'objectif', 'situations', 'etapes', 'resultat', 'controles', 'exemples', 'limites'))
    body = ('<div class="k5632"><h1>%s</h1><p>%s</p>%s<div class="k5632-grid">%s</div><section><h2>Capacités des services connectés</h2><p class="f5632-hint">Une connexion réussie prouve l’accès à la liste '
            'des outils, pas qu’une recherche ou une lecture fonctionne.</p><ul>%s</ul></section><section><h2>%s</h2><p>%s</p>%s</section>'
            '<section><h2>%s</h2><p>%s</p>%s</section></div>') % (
        th('sk.title'), th('sk.intro'),
        (_form('admin/skills/auto', '<input type="hidden" name="on" value="%s">' % ('0' if auto_mode(desk) else '1'), i18n.t('sk.auto_off' if auto_mode(desk) else 'sk.auto_on'), reload=True)
         + _general_form(desk, rows) if role == 'administrateur' else ''),
        cards, capacities, th('sk.create'), th('sk.create_intro'), _form('skills/create', create, i18n.t('sk.create_btn'), reload=True),
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

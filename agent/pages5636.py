"""5.6.36 : pages ajoutées (menu « Toutes les fonctions ») — une route, un titre, un rendu ; même enveloppe que les autres pages."""
from .common import Stop

PAGES = {
    '/contacts-crm': 'Contacts & CRM',
    '/initiatives': 'Initiatives',
    '/pieces-donnees': 'Données des pièces',
    '/outils-ia': 'Outils IA',
    '/corporate': 'Corporate',
    '/formulaires': 'Formulaires officiels',
    '/cessions': 'Cessions de fonds et de bail',
    '/relances': 'Relances d’impayés',
    '/complement-word': 'Complément Word',
    '/microsoft-retour': 'Microsoft 365',
    '/facturation': 'Facturation et honoraires',   # 5.6.53 (audit 5.6.52, § 4)
    '/contribution': 'Contribution au chiffre d’affaires',   # 5.6.88 : réservée au droit « facturation »
    '/operation': 'Opération externe',   # 5.6.53 (U01) : fiche de ce qui engage le cabinet, avant « Valider et transmettre »
    '/connexions-retour': 'Connexions',   # 5.6.52 : retour d'autorisation OAuth (Microsoft Graph, Google Workspace, DocuSign)
    '/complement-word/volet': 'AxiorHub',
    '/toutes-les-fonctions': 'Toutes les fonctions',
    '/messagerie': 'Messagerie du cabinet',   # 5.6.50
    '/savoir-faire': 'Savoir-faire du cabinet',   # 5.6.59 (A07)
    '/securite-ia': 'Sécurité de l’IA',   # 5.6.59 : instructions cachées dans les sources
    '/navigateur': 'Navigateur supervisé',   # 5.6.73 : poste Windows ou Ubuntu
    '/mes-taches': 'Mes tâches',   # 5.6.88 : responsable, échéance, rappel, statut
    '/mobile': 'À valider',   # 5.6.74 : page du téléphone associé
    '/mon-compte': 'Mon compte',   # 5.6.88 : double authentification et jetons d'API de la personne
    '/parametres/journal-audit': 'Journal d’audit',   # 5.6.88 (administrateur)
    '/parametres/ia-visible': 'Ce que l’IA peut voir',   # 5.6.88 (administrateur)
    '/parametres/gouvernance': 'Données et budget de l’IA',   # 5.6.89 (administrateur)
    '/banc-essai': 'Banc d’essai et état des fonctions',   # 5.6.89 : hors de /parametres, un avocat y donne son avis de relecteur
    '/parametres/droits': 'Droits et accès',   # 5.6.50 (administrateur)   # 5.6.37 : page du menu « Toutes les fonctions » (interface affichée dans Nextcloud)
}


def page(desk, auth, prefix, env, args, path, shell):
    title = PAGES[path]
    if path == '/complement-word/volet':   # volet affiché dans Word : page autonome (en-têtes propres à Office)
        from . import office5636
        return office5636.taskpane(desk, auth, prefix)
    if path == '/messagerie':   # 5.6.50 : messagerie interne du cabinet
        from . import web5650
        body = web5650.messagerie_page(desk, auth, prefix, env, args)
    elif path in ('/mon-compte', '/parametres/journal-audit', '/parametres/ia-visible'):   # 5.6.88
        from . import securite5688
        render = {'/mon-compte': securite5688.compte_page, '/parametres/journal-audit': securite5688.journal_page, '/parametres/ia-visible': securite5688.vue_ia_page}[path]
        body = render(desk, auth, prefix, env, args)
    elif path == '/parametres/gouvernance':   # 5.6.89
        from . import gouvernance_web5689
        body = gouvernance_web5689.page(desk, auth, prefix, env, args)
    elif path == '/banc-essai':   # 5.6.89
        from . import qualif_web5689
        body = qualif_web5689.page(desk, auth, prefix, env, args)
    elif path == '/parametres/droits':
        from .web567 import actor
        if actor(env)[1] != 'administrateur':
            raise Stop('role_insuffisant')
        from . import web5650
        body = web5650.droits_page(desk, auth, prefix, env, args)
    elif path == '/mobile':   # 5.6.74
        from . import appareils5674
        body = appareils5674.mobile_page(desk, prefix, args, env)
    elif path == '/mes-taches':   # 5.6.88
        from . import agenda5688
        body = agenda5688.mes_taches_page(desk, auth, prefix, env, args)
    elif path == '/navigateur':   # 5.6.73
        from . import web5673
        body = web5673.page_html(desk, prefix, args, env, auth)
    elif path == '/savoir-faire':
        from . import web5659
        body = web5659.savoir_page(desk, auth, prefix, env, args)
    elif path == '/securite-ia':
        from . import web5659
        body = web5659.securite_page(desk, auth, prefix, env, args)
    elif path == '/contacts-crm':
        from . import crm5636, crm5676
        from .web567 import actor
        view = args.get('vue') if args.get('vue') in ('opportunites', 'veille') else ''   # 5.6.76 : opportunités et prospects ; 5.6.88 : veille des sociétés
        if view == 'veille':
            from . import crm5688
            body = crm5676.tabs_html(prefix, view) + crm5688.page_html(desk, prefix)
        else:
            body = crm5676.tabs_html(prefix, view) + (crm5676.page_html(desk, prefix, args, actor(env)[1]) if view else crm5636.page_html(desk, prefix, args))
    elif path == '/initiatives':
        from . import initiatives5636
        body = initiatives5636.page_html(desk, prefix, args, env)
    elif path == '/pieces-donnees':
        from . import ged5636
        body = ged5636.page_html(desk, prefix, args)
    elif path == '/outils-ia':
        from . import outils5636
        body = outils5636.page_html(desk, prefix, args)
    elif path == '/cessions':   # 5.6.84
        from . import web5684
        body = web5684.cessions_page(desk, prefix, args, env)
    elif path == '/formulaires':   # 5.6.83
        from . import formulaires5683
        from .web567 import actor
        body = formulaires5683.page_html(desk, prefix, args, actor(env)[1])
    elif path == '/corporate':
        from . import corporate5636
        body = corporate5636.page_html(desk, prefix, args)
    elif path == '/relances':
        from . import relances5636
        body = relances5636.page_html(desk, prefix, args)
    elif path == '/toutes-les-fonctions':
        return shell(_all_title(), all_functions_html(desk, prefix), prefix, auth['csrf'], path)
    elif path == '/facturation':
        from . import web5653
        body = web5653.page(desk, auth, prefix, env, args)
    elif path == '/contribution':
        from . import contribution5688
        body = contribution5688.page(desk, auth, prefix, env, args)
    elif path == '/operation':
        from .web567 import actor
        from . import connexions5652
        owner, role = actor(env)
        body = connexions5652.sheet_html(desk, prefix, owner, role, str(args.get('id') or ''))
    elif path == '/connexions-retour':
        from html import escape as _e
        from .web567 import actor
        from . import connexions5652
        try:
            out = connexions5652.oauth_finish(desk, actor(env)[0], args)
            body = '<p>%s</p>' % _e('Compte autorisé et vérifié : %s.' % ', '.join(str(v) for v in (out.get('identite') or {}).values() if v) if out.get('ok')
                                    else 'Autorisation enregistrée, mais le test de lecture a échoué : %s.' % out.get('erreur', ''))
        except Stop as ex:
            body = '<p>%s</p>' % _e('Autorisation non enregistrée : %s.' % str(ex).split(':')[0])
        body += '<p><a class="ax-btn" href="%s">Retour aux connexions</a></p>' % _e(prefix + '/parametres?rubrique=connexions#cx52', quote=True)
        body += '<p><a class="ax-btn ghost" href="%s">Stockage des documents</a></p>' % _e(prefix + '/parametres?rubrique=stockage', quote=True)   # 5.6.88 : stockages cloud
    elif path == '/microsoft-retour':
        from . import microsoft5636
        body = microsoft5636.return_page(desk, prefix, args)
    else:
        from . import office5636
        body = office5636.page_html(desk, prefix, args, env)
    return shell(title, '<div class="p5636">' + body + '</div>', prefix, auth['csrf'], path)


def _all_title():
    from .i18n5631 import t
    return t('menu.all')


def all_functions_html(desk, prefix):
    """5.6.37 : les fonctions du menu « Toutes les fonctions », en page (mêmes groupes, mêmes liens, mêmes états de disponibilité)."""
    import re
    from .fonctions5631 import panel_html
    from .i18n5631 import current, role
    from .shell501 import context
    panel = panel_html(prefix, '/toutes-les-fonctions', context(), role(), current())
    start = panel.find('<section class="f5631-group"')
    end = panel.rfind('</div></details>')
    groups = panel[start:end] if start >= 0 and end > start else ''
    groups = re.sub(r' aria-current="page"', '', groups)
    return ('<div class="p5637-fonctions"><h1>%s</h1><div class="f5631-panel p5637-panel">%s</div></div>' % (_all_title(), groups))

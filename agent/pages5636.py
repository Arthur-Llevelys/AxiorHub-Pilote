"""5.6.36 : pages ajoutées (menu « Toutes les fonctions ») — une route, un titre, un rendu ; même enveloppe que les autres pages."""

PAGES = {
    '/contacts-crm': 'Contacts & CRM',
    '/initiatives': 'Initiatives',
    '/pieces-donnees': 'Données des pièces',
    '/outils-ia': 'Outils IA',
    '/corporate': 'Corporate',
    '/relances': 'Relances d’impayés',
    '/complement-word': 'Complément Word',
    '/microsoft-retour': 'Microsoft 365',
    '/complement-word/volet': 'AxiorHub',
}


def page(desk, auth, prefix, env, args, path, shell):
    title = PAGES[path]
    if path == '/complement-word/volet':   # volet affiché dans Word : page autonome (en-têtes propres à Office)
        from . import office5636
        return office5636.taskpane(desk, auth, prefix)
    if path == '/contacts-crm':
        from . import crm5636
        body = crm5636.page_html(desk, prefix, args)
    elif path == '/initiatives':
        from . import initiatives5636
        body = initiatives5636.page_html(desk, prefix, args, env)
    elif path == '/pieces-donnees':
        from . import ged5636
        body = ged5636.page_html(desk, prefix, args)
    elif path == '/outils-ia':
        from . import outils5636
        body = outils5636.page_html(desk, prefix, args)
    elif path == '/corporate':
        from . import corporate5636
        body = corporate5636.page_html(desk, prefix, args)
    elif path == '/relances':
        from . import relances5636
        body = relances5636.page_html(desk, prefix, args)
    elif path == '/microsoft-retour':
        from . import microsoft5636
        body = microsoft5636.return_page(desk, prefix, args)
    else:
        from . import office5636
        body = office5636.page_html(desk, prefix, args, env)
    return shell(title, '<div class="p5636">' + body + '</div>', prefix, auth['csrf'], path)

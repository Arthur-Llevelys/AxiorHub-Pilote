"""5.6.43 (cahier v9, § 17.1) : sous-vues d'Agenda — « Agenda en ligne » (calendriers CalDAV configurés) et « Agenda personnel »
(agenda AxiorHub local, non synchronisé). Le choix explicite dans l'adresse prime ; il devient la préférence de l'utilisateur (jamais un
réglage commun du cabinet). Une valeur inconnue revient à la préférence, puis à « Agenda personnel » (défaut local de la 5.6.42).
"""
from html import escape

VIEWS = (('en_ligne', 'Agenda en ligne', 'Calendriers Nextcloud, CalDAV ou Google configurés'),
         ('personnel', 'Agenda personnel', 'Local, non synchronisé'))
KEY = 'agenda5643:vue:'


def resolve_view(desk, requested, owner='cabinet'):
    owner = str(owner or 'cabinet')
    valid = {k for k, _, _ in VIEWS}
    if requested in valid:
        try:
            if desk.settings(KEY + owner, '') != requested:
                desk.setting(KEY + owner, requested)
        except Exception:   # noqa: BLE001 — la préférence ne bloque jamais l'affichage
            pass
        return requested
    saved = desk.settings(KEY + owner, '')
    return saved if saved in valid else 'personnel'


def subnav_html(current, args, url):
    """Deuxième rangée sous « Agenda » : liens ordinaires (aria-current), période et vue conservées."""
    keep = {k: str(args[k]) for k in ('view', 'date') if args.get(k)}
    return ('<nav class="planning-subtabs" aria-label="Sources de l’agenda">' + ''.join(
        '<a' + (' aria-current="page"' if key == current else '') + ' href="' + escape(url('/planning', vue='agenda', agenda=key, **keep), quote=True) +
        '" title="' + escape(hint, quote=True) + '">' + escape(label) + '</a>' for key, label, hint in VIEWS) + '</nav>')

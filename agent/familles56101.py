"""5.6.101 (lot F, cahier § 6 et annexe A, F01-F06) : « Toutes les fonctions » regroupées en 33 familles à onglets.

Aucune fonction n'est supprimée et aucune adresse ne change : une famille est une barre d'onglets commune, posée en haut de chacune de
ses pages (F02), sur le modèle des anciens onglets de Produire (``navigation5643.production_tabs_html``, remplacé dans
``shell501.render`` par ``onglets_html``). Le registre ``fonctions5631`` reste la source unique : son champ ``FAMILLE`` donne la famille
de chaque fonction, dans l'ordre des onglets ; ce module donne l'ordre des familles (annexe A, décision D4 : regroupement tel quel), leurs
sections, la barre d'onglets et l'aide au panneau, au guide d'utilisation et à la palette Ctrl+K.

Droits (F04) : un onglet n'apparaît que si le rôle courant peut ouvrir sa page, avec la même règle que le panneau
(``fonctions5631.visible``) ; un onglet interdit est absent, jamais grisé. Rien n'est écrit pendant l'affichage (R05).
"""
from html import escape as e
from urllib.parse import parse_qsl, urlencode

from . import fonctions5631 as reg
from . import i18n5631 as i18n

# Sections du panneau et du guide (titres de l'annexe A), dans l'ordre.
SECTIONS = ('dossier', 'rechercher', 'communiquer', 'cabinet', 'modeles', 'regler')
# Familles dans l'ordre de l'annexe A : identifiant, section. Les membres et l'ordre des onglets viennent de ``fonctions5631.FAMILLE``.
FAMILLES = (
    ('dossiers', 'dossier'), ('produire', 'dossier'), ('rediger', 'dossier'), ('echeances', 'dossier'), ('audiences', 'dossier'),
    ('pieces_contentieux', 'dossier'), ('societes_fonds', 'dossier'), ('strategie_analyses', 'dossier'),
    ('rechercher', 'rechercher'), ('veille', 'rechercher'), ('navigateur', 'rechercher'),
    ('aujourdhui', 'communiquer'), ('courriels', 'communiquer'), ('agenda_taches', 'communiquer'), ('talk', 'communiquer'),
    ('contacts', 'communiquer'), ('assistant_missions', 'communiquer'), ('accueil_telephonique', 'communiquer'), ('telephone', 'communiquer'),
    ('gestion_cabinet', 'cabinet'), ('facturation', 'cabinet'), ('calculs', 'cabinet'),
    ('modeles', 'modeles'), ('savoir_faire', 'modeles'), ('regles_agent', 'modeles'),
    ('parametres', 'regler'), ('mon_compte', 'regler'), ('ia_confiance', 'regler'), ('securite', 'regler'), ('etat_depannage', 'regler'),
    ('evaluations', 'regler'), ('aide', 'regler'), ('a_propos', 'regler'),
)
SECTION = dict(FAMILLES)
# Libellé court d'onglet quand le nom de la fonction répète celui de la famille (ancien onglet « Résultats » de Produire).
ONGLET_COURT = ('production',)
# Écrans rattachés à l'onglet d'une fonction sans être une fonction du registre (F05 : Paramètres › Administration).
ECRANS = {'/administration/fonctions-peu-utilisees': 'administration'}


def membres(famille):
    """Fonctions de la famille, dans l'ordre de ses onglets."""
    return [fid for fid, fam in reg.FAMILLE.items() if fam == famille]


def famille_de(fid):
    return reg.FAMILLE.get(fid, '')


def libelle(famille, lang=None):
    return i18n.t('f101.fam.' + famille, lang)


def libelle_section(section, lang=None):
    return i18n.t('f101.sec.' + section, lang)


def libelle_onglet(fid, lang=None):
    return i18n.t(('f101.tab.%s' if fid in ONGLET_COURT else 'fn.%s.label') % fid, lang)


def titre_fonction(fid, lang=None):
    """« Fonction (famille) » (F01 : palette Ctrl+K, recherche du panneau) ; le nom seul quand la famille porte le même nom."""
    label, fam = i18n.t('fn.%s.label' % fid, lang), famille_de(fid)
    if not fam:
        return label
    nom = libelle(fam, lang)
    return label if nom == label else i18n.t('f101.fonction_famille', lang, fonction=label, famille=nom)


def _route(fid):
    return reg.INDEX[fid][2]


def fonction_de(path):
    """Fonction de la page ``path`` (requête comprise : ``/audiences?onglet=suivi``, ``/planning?vue=taches``), '' si aucune.
    Seule une route exacte du registre compte : une page de détail (``/dossier``, ``/documents/edit``) ne prend pas la barre d'une famille."""
    base, _, requete = str(path or '').partition('?')
    args = dict(parse_qsl(requete))
    simple = ''
    for fid, group, route, aliases, needs in reg.FUNCTIONS:
        rbase, _, rq = route.partition('?')
        if rbase != base:
            continue
        if rq:
            if all(args.get(k) == v for k, v in parse_qsl(rq)):
                return fid
        elif not simple:
            simple = fid
    return simple or ECRANS.get(base, '')


def visible(fid, role):
    return reg.visible(role, _route(fid), fid)


def onglets(path, role):
    """[(fid, route, libellé, actif)] de la famille de la page, filtrés par rôle ; [] si la page n'a pas de famille ou une seule fonction
    visible, ou si tous ses onglets sont ceux d'une même page (Audiences : la page porte déjà ses propres onglets)."""
    courant = fonction_de(path)
    fam = famille_de(courant)
    if not fam:
        return []
    items = [fid for fid in membres(fam) if visible(fid, role)]
    if 'a_relire' in items:   # 5.6.102 (lot N) : en présentation historique, « À relire » reste l'onglet propre de Courriels (pas de barre en double)
        from .shell501 import relire_separe
        if not relire_separe():
            items.remove('a_relire')
    if len(items) < 2 or len({_route(f).split('?', 1)[0] for f in items}) < 2:
        return []
    return [(fid, _route(fid), libelle_onglet(fid), fid == courant) for fid in items]


def onglets_html(prefix, path, role):
    """Barre d'onglets de la famille de la page (contrat « Familles → pages ») ; '' si la page n'a pas de famille ou une seule fonction.
    Les onglets sont des liens ordinaires (l'adresse reste celle de la page, un favori garde son onglet) ; l'onglet actif porte
    ``aria-current="page"``. Flèches gauche et droite, Début et Fin déplacent le focus (``v56101-familles.js``)."""
    try:
        items = onglets(path, role)
    except Exception:   # noqa: BLE001 — la barre ne bloque jamais le rendu de la page
        return ''
    if not items:
        return ''
    fam = famille_de(items[0][0])
    return ('<nav class="ws-tabs production-tabs f101-onglets" data-f101-famille="' + e(fam, quote=True) + '" aria-label="' +
            i18n.th('f101.onglets', famille=libelle(fam)) + '">' + ''.join(
                '<a href="' + e(prefix + route, quote=True) + '"' + (' aria-current="page"' if actif else '') + '>' + e(label) + '</a>'
                for fid, route, label, actif in items) + '</nav>')


def entete_html(prefix, path, cfg=None):
    """Pages de l'ancien gabarit de ``web.py`` (qui ne passent pas par ``shell501.render``) : même barre de famille, même marqueur du
    compteur d'usage et son script (balise ``defer`` dans le corps : aucun script en ligne)."""
    try:
        barre = onglets_html(prefix, chemin_de_page(path), i18n.role())
        from .usage56101 import balise_html
        balise = balise_html(prefix, path, cfg)
    except Exception:   # noqa: BLE001 — ni la barre ni le compteur ne bloquent la page
        return ''
    script = ('<script defer src="' + e(prefix, quote=True) + '/static/v56101-familles.js"></script>') if (barre or balise) else ''
    return barre + balise + script


def chemin_de_page(path):
    """Chemin réel de la requête (avec ses paramètres d'onglet) quand il désigne une page du registre, sinon ``path`` reçu par le gabarit."""
    try:
        from .shell501 import _ctx
        chemin = getattr(_ctx, 'chemin', '') or ''
        args = getattr(_ctx, 'args', None) or {}
    except Exception:   # noqa: BLE001
        return path
    if not chemin:
        return path
    garde = {k: str(v) for k, v in args.items() if k in ('onglet', 'vue')}
    complet = chemin + ('?' + urlencode(garde) if garde else '')
    return complet if fonction_de(complet) else path


def panneau(rows, current, prefix, lang=None):
    """Sections et familles du panneau « Toutes les fonctions » (V101-07 : au plus 35 entrées). ``rows`` : ``fonctions5631.entries``
    déjà filtrées par rôle. Une famille sans fonction permise est absente ; son entrée ouvre sa première fonction permise et sa
    recherche couvre le nom de la famille, ceux de ses fonctions et leurs anciens noms."""
    par_id = {r['id']: r for r in rows}
    out = []
    for section in SECTIONS:
        items = []
        for fam, sec in FAMILLES:
            if sec != section:
                continue
            permis = [par_id[f] for f in membres(fam) if f in par_id]
            if not permis:
                continue
            first = permis[0]
            nom = libelle(fam, lang)
            # 5.6.101 (intégration, P02) : la famille porte son nom et ceux de ses fonctions ; descriptions et anciens noms ne sont que dans
            # l'index masqué (une fois par fonction), qui s'affiche dès la recherche — le panneau, présent dans chaque page, pesait 55 ko
            blob = ' '.join([nom] + [r['label'] for r in permis]).lower()
            actif = current in {r['id'] for r in permis}
            sous = ' · '.join(libelle_onglet(r['id'], lang) for r in permis if libelle_onglet(r['id'], lang) != nom) or first['desc']
            items.append('<li data-search="' + e(blob, quote=True) + '" data-f101-famille="' + e(fam, quote=True) + '">'
                         '<a href="' + e(prefix + first['route'], quote=True) + '"' + (' aria-current="page"' if actif else '') + '>'
                         '<strong>' + e(nom) + '</strong><small>' + e(sous) + '</small></a></li>')
        if items:
            out.append('<section class="f5631-group" data-group="' + section + '"><h3>' + i18n.th('f101.sec.' + section, lang) + '</h3><ul>' +
                       ''.join(items) + '</ul></section>')
    return out


def audit():
    """Contrôle F01 : chaque fonction du registre a une famille connue ; 33 familles, aucune vide ; libellés dans le catalogue."""
    problems = []
    connues = {f for f, _ in FAMILLES}
    if len(FAMILLES) != 33 or len(connues) != 33:
        problems.append(('familles', len(connues)))
    for fid, *_ in reg.FUNCTIONS:
        if reg.FAMILLE.get(fid) not in connues:
            problems.append((fid, 'famille'))
    for fid in reg.FAMILLE:
        if fid not in reg.INDEX:
            problems.append((fid, 'inconnue'))
    for fam in connues:
        if not membres(fam):
            problems.append((fam, 'vide'))
        if not i18n.catalog('fr').get('f101.fam.' + fam):
            problems.append((fam, 'libelle'))
    for sec in SECTIONS:
        if not i18n.catalog('fr').get('f101.sec.' + sec):
            problems.append((sec, 'section'))
    return problems

"""5.6.31 (cahier des charges additionnel, R19 / GU01–GU06) : guide d'utilisation relié aux fonctions réelles.

- Un article versionné par fonction du registre ``fonctions5631`` (même identifiant), stocké dans ``agent/aide/<langue>.json`` :
  objectif, quand l'utiliser, prérequis, 3 à 7 étapes avec les noms exacts des boutons, résultat attendu, comment le vérifier,
  erreurs courantes et leur solution, exemple fictif (aucune donnée client).
- Les boutons cités sont déclarés dans ``buttons`` : un test ouvre chaque page et vérifie que ces libellés y figurent réellement
  (un bouton renommé sans mise à jour de l'aide fait échouer la recette).
- Index par les mêmes groupes que le menu, recherche (nom, besoin, anciens noms), parcours par besoin, liens « Ouvrir cette fonction »
  et « Configurer le prérequis manquant » vers des routes du registre (jamais déduites du titre).
"""
from functools import lru_cache
from html import escape as e
import json
from pathlib import Path

from . import fonctions5631 as reg
from . import i18n5631 as i18n

ROOT = Path(__file__).resolve().parent / 'aide'
VERSION = '5.6.32'   # version du guide d'utilisation (fiches relues pour cette version)
# Parcours par besoin (GU02) : identifiant, fonctions dans l'ordre d'usage.
PATHS = (('analyser_dossier', ('dossiers', 'documents', 'recherche', 'verification')),
         ('preparer_reponse', ('courriels', 'assistant', 'documents')),
         ('produire_conclusions', ('missions_complexes', 'modeles_word', 'verification', 'pieces')),
         ('organiser_audience', ('audience', 'echeances', 'planning', 'pieces')),
         ('etablir_bordereau', ('pieces', 'modeles_word')),
         ('preparer_facture', ('honoraires', 'cabinet', 'assistance_metier')),
         ('mettre_en_place_veille', ('veille', 'sources', 'guide_installation')))


@lru_cache(maxsize=8)
def articles(lang):
    try:
        data = json.loads((ROOT / ((i18n.clean(lang) or 'fr') + '.json')).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith('_')}


def article(fid, lang=None):
    """Article dans la langue demandée ; repli signalé vers le français si la traduction manque (jamais présenté comme exact)."""
    lang = i18n.clean(lang) or i18n.current()
    art = articles(lang).get(fid)
    if art:
        return art, lang
    return articles('fr').get(fid), 'fr'


def check():
    """Couverture (GU05) : un article par fonction et par langue, mêmes boutons déclarés, champs obligatoires, 3 à 7 étapes."""
    problems = []
    required = ('objective', 'when', 'prerequisites', 'steps', 'result', 'verify', 'errors', 'example', 'buttons', 'version')
    for fid, *_ in reg.FUNCTIONS:
        source = articles('fr').get(fid)
        for lang in i18n.LANGS:
            art = articles(lang).get(fid)
            if not art:
                problems.append((lang, fid, 'absent'))
                continue
            for key in required:
                if key not in art or art[key] in ('', None):
                    problems.append((lang, fid, key))
            if not 3 <= len(art.get('steps') or []) <= 7:
                problems.append((lang, fid, 'etapes'))
            if source and art.get('buttons') != source.get('buttons'):
                problems.append((lang, fid, 'boutons'))
            if art.get('version') != VERSION:
                problems.append((lang, fid, 'version'))
    return problems


def _search_blob(fid, art, entry):
    return ' '.join([entry['label'], entry['desc']] + list(entry['aliases']) + [art.get('objective', ''), art.get('when', '')]).lower()


def page(desk, auth, prefix, env, args, shell):
    from .web567 import actor
    owner, role = actor(env)
    lang = i18n.current()
    th = i18n.th
    rows = {r['id']: r for r in reg.entries(desk.c, role, lang)}
    fid = str(args.get('fonction') or '')
    if fid in rows:
        return shell(i18n.t('help.title') + ' · ' + rows[fid]['label'], _article_html(prefix, rows[fid], lang), prefix, auth['csrf'], '/guide-utilisation')
    body = '<div class="h5631"><h1>%s</h1><p class="h5631-lead">%s</p>' % (th('help.title'), th('help.lead'))
    body += ('<div class="h5631-modes"><a href="#h5631-search">%s</a><a href="#h5631-discover">%s</a><a href="#h5631-tasks">%s</a>'
             '<a href="%s">%s</a></div>') % (th('help.search'), th('help.discover'), th('help.tasks'),
                                             e(prefix + '/guide-installation', quote=True), th('fn.guide_installation.label'))
    body += ('<label class="h5631-search" id="h5631-search"><span>%s</span><input type="search" id="h5631-q" value="%s" autocomplete="off" '
             'placeholder="%s"></label><p id="h5631-empty" hidden>%s</p>') % (
        th('help.search'), e(str(args.get('q') or ''), quote=True), th('menu.search_hint'), th('menu.no_result'))
    body += '<section id="h5631-tasks"><h2>%s</h2><div class="h5631-paths">' % th('help.tasks')
    for pid, fids in PATHS:
        steps = ''.join('<li><a href="%s">%s</a></li>' % (e(prefix + '/guide-utilisation?fonction=' + f, quote=True), e(rows[f]['label']))
                        for f in fids if f in rows)
        body += '<article class="h5631-path"><h3>%s</h3><p>%s</p><ol>%s</ol></article>' % (th('path.%s.title' % pid), th('path.%s.desc' % pid), steps)
    body += '</div></section><section id="h5631-discover"><h2>%s</h2>' % th('help.discover')
    for g in reg.GROUPS:
        items = [r for r in rows.values() if r['group'] == g]
        if not items:
            continue
        body += '<section class="h5631-group"><h3>%s</h3><p>%s</p><ul>' % (th('grp.%s.label' % g), th('grp.%s.desc' % g))
        for r in items:
            art, _ = article(r['id'], lang)
            state = ('<span class="h5631-state todo">%s</span>' % th('help.state_configure')) if r['missing'] else ('<span class="h5631-state ok">%s</span>' % th('help.state_ready'))
            body += '<li data-search="%s"><a href="%s"><strong>%s</strong></a> %s<br><small>%s</small></li>' % (
                e(_search_blob(r['id'], art or {}, r), quote=True), e(prefix + '/guide-utilisation?fonction=' + r['id'], quote=True), e(r['label']), state,
                e((art or {}).get('objective', r['desc'])))
        body += '</ul></section>'
    body += '</section></div>'
    return shell(i18n.t('help.title'), body, prefix, auth['csrf'], '/guide-utilisation')


def _article_html(prefix, entry, lang):
    th = i18n.th
    art, used = article(entry['id'], lang)
    art = art or {}
    out = '<article class="h5631 h5631-article" lang="%s"><p><a href="%s">← %s</a></p><h1>%s</h1>' % (
        used, e(prefix + '/guide-utilisation', quote=True), th('help.back'), e(entry['label']))
    if used != lang:
        out += '<p class="h5631-fallback">%s</p>' % th('help.fallback')
    out += '<p class="h5631-objective">%s</p>' % e(art.get('objective', ''))
    actions = '<a class="h5631-open" href="%s">%s</a>' % (e(prefix + entry['route'], quote=True), th('help.open'))
    if entry['missing']:
        actions += ' <a class="h5631-configure" href="%s">%s</a>' % (e(prefix + entry['configure'], quote=True), th('help.configure'))
    out += '<p class="h5631-actions">%s</p>' % actions
    out += '<h2>%s</h2><p>%s</p>' % (th('help.when'), e(art.get('when', '')))
    out += '<h2>%s</h2><p>%s</p>' % (th('help.prerequisites'), e(art.get('prerequisites', '')))
    if entry['missing']:
        out += '<p class="h5631-missing">%s</p>' % th('help.missing', what=', '.join(i18n.t('cap.' + c) for c in entry['missing']))
    out += '<h2>%s</h2><ol class="h5631-steps">%s</ol>' % (th('help.steps'), ''.join('<li>%s</li>' % e(s) for s in art.get('steps', [])))
    out += '<h2>%s</h2><p>%s</p>' % (th('help.result'), e(art.get('result', '')))
    out += '<h2>%s</h2><p>%s</p>' % (th('help.verify'), e(art.get('verify', '')))
    out += '<h2>%s</h2><dl class="h5631-errors">%s</dl>' % (th('help.errors'), ''.join(
        '<dt>%s</dt><dd>%s</dd>' % (e(p), e(s)) for p, s in art.get('errors', [])))
    out += '<h2>%s</h2><p class="h5631-example">%s</p>' % (th('help.example'), e(art.get('example', '')))
    out += '<p class="h5631-version">%s</p></article>' % th('help.version', version=art.get('version', VERSION))
    return out

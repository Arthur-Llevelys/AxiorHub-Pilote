"""5.6.21 : concentrateur des réglages — une page « Paramètres » à six rubriques, chaque réglage à un seul endroit.

Les formulaires existants sont réutilisés tels quels (mêmes identifiants, mêmes scripts) : les pages d'origine sont rendues avec
un « shell » capturant leur corps, puis composées par rubrique. Les doublons sont retirés par composition : la voix n'est réglée
que dans « Voix », le rôle par dossier que dans « Documents et procédure », Ollama que dans « Connexions », etc.
Les anciennes adresses (/parametres/connexions, /parametres/assistant, /parametres/proactivite, /ia-externe, /confort,
/atelier/reglages, /parametres/agendas) affichent la rubrique correspondante.
"""
from html import escape as e
import re
from urllib.parse import urlencode

RUBRIQUES = (
    ('connexions', 'Connexions', 'Messagerie, Nextcloud, agendas, Invoice Ninja, SMTP, interfaces, Ollama'),
    ('ia', 'Intelligence artificielle', 'Routage des modèles, régime économe, consommation, IA externe, connecteurs'),
    ('voix', 'Voix', 'Synthèse, dictée, conversation, vitesse, mode discret, assistant, tests'),
    ('routines', 'Routines et initiatives', 'Heures, jours, signature, exclusions, briefing, rôles, automatismes'),
    ('documents', 'Documents et procédure', 'Éditeur de documents, avis de procédure, rôle par dossier, agendas, modèles'),
    ('cabinet', 'Cabinet et accueil', 'Accueil téléphonique, envoi, mobile, recherche, raccourcis, maintenance'),
)
ALIASES = {'/parametres/connexions': 'connexions', '/parametres/assistant': 'voix', '/parametres/proactivite': 'routines',
           '/ia-externe': 'ia', '/confort': 'cabinet', '/atelier/reglages': 'documents', '/parametres/agendas': 'documents'}
LEGACY_TABS = {'connexions': 'connexions', 'ia': 'ia', 'extensions': 'ia', 'modeles': 'documents', 'automatismes': 'routines',
               'cabinet': 'cabinet', 'confidentialite': 'cabinet', 'maintenance': 'cabinet'}
VOICE_GROUPS = ('Voix', 'Synthèse vocale')


class Capture:
    """Shell de capture : garde le corps d'une page existante et ses scripts d'en-tête."""

    def __init__(self):
        self.titles, self.head = [], ''

    def __call__(self, title, body, prefix, csrf, active='', head=''):
        self.titles.append(title)
        self.head += head or ''
        return body


def _section(html, title=None, strip=(), keep=None):
    """Isole ou retire des <section> par le texte de leur <h2> ; convertit un éventuel <h1> en <h2>."""
    html = html or ''
    for heading in strip:
        html = re.sub(r'<section[^>]*>\s*<h2>' + re.escape(heading) + r'</h2>.*?</section>', '', html, count=1, flags=re.S)
    if keep is not None:
        m = re.search(r'<section[^>]*>\s*<h2>' + re.escape(keep) + r'</h2>.*?</section>', html, flags=re.S)
        html = m.group(0) if m else ''
    html = re.sub(r'<h1>(.*?)</h1>', r'<h2>\1</h2>', html, count=1, flags=re.S)
    if title:
        html = '<h2>' + e(title) + '</h2>' + html
    return '<div class="p5621-section">' + html + '</div>'


def _helpers(prefix, csrf):
    def url(p, **kw):
        return prefix + p + ('?' + urlencode(kw) if kw else '')

    def form(action, label, fields=None, extra=''):
        hidden = {'csrf': csrf, 'action': action, **(fields or {})}
        return ('<form method="post" enctype="application/x-www-form-urlencoded" action="' + e(url('/action'), quote=True) + '">'
                + ''.join('<input type="hidden" name="' + e(k, quote=True) + '" value="' + e(str(v), quote=True) + '">' for k, v in hidden.items())
                + extra + '<button>' + e(label) + '</button></form>')

    def link(p, label, **kw):
        return '<a href="' + e(url(p, **kw), quote=True) + '">' + e(label) + '</a>'
    return url, form, link


def _legacy(desk, prefix, csrf, tab):
    """Onglet de l'ancienne page Paramètres (automatismes, extensions, modèles, cabinet, confidentialité, maintenance) sans sa barre d'onglets."""
    from .workstation32_ui import settings_page
    _, form, link = _helpers(prefix, csrf)
    html = settings_page(desk, {'tab': tab}, form, link)
    return re.sub(r'<nav class="ws-tabs".*?</nav>', '', html, count=1, flags=re.S)


def _links(prefix, items):
    return '<p class="p5621-links">' + ' '.join('<a class="ax-btn ghost" href="' + e(prefix + p, quote=True) + '">' + e(label) + '</a>' for p, label in items) + '</p>'


def _connexions(desk, auth, prefix, env, cap):
    from . import web567
    html = web567.connections_page(desk, auth, prefix, env, shell=cap, exclude=VOICE_GROUPS)
    return _section(html) + _section(_links(prefix, (('/mise-en-service', 'Mise en service : contrôles des services réels'),)), 'Vérifier')


def _ia(desk, auth, prefix, env, cap):
    from . import ia540
    body = _section(ia540.page(desk, prefix, cap, auth['csrf']))
    body += _section(_legacy(desk, prefix, auth['csrf'], 'ia'), 'Fournisseurs : état et tests')
    body += _section(_legacy(desk, prefix, auth['csrf'], 'extensions'), 'Connecteurs et extensions')
    body += _section('<p>L’adresse et le modèle Ollama se règlent dans la rubrique <a href="' + e(prefix + '/parametres?rubrique=connexions') + '">Connexions</a> (locale ou réseau du cabinet).</p>')
    return body


def _voix(desk, auth, prefix, env, cap):
    from . import web567, web568, web490
    body = _section(web567.connections_page(desk, auth, prefix, env, shell=cap, groups=VOICE_GROUPS, tests=('voice',),
                                            heading='Moteurs de voix : dictée, transcription et synthèse'))
    body = re.sub(r'<details><summary>Variables Docker.*?</details>', '', body, count=1, flags=re.S)
    body += _section(web567.preferences_page(desk, auth, prefix, env, shell=cap), strip=('Accueil téléphonique et WhatsApp',))
    body += _section(web568.preferences(desk, auth, prefix, env, shell=cap), keep='Voix et conversation rapide')
    body += _section(web490.confort_sections(desk, auth, prefix, {}).get('voix', ''))
    return body


def _routines(desk, auth, prefix, env, cap):
    from . import web568, web520
    _, form, _ = _helpers(prefix, auth['csrf'])
    essentiel = web520.today_html(desk, prefix, form)
    m = re.search(r'<details class="t520-frame"[^>]*><summary><h2>Réglages des routines</h2></summary>.*?</details>', essentiel, flags=re.S)
    routines = (m.group(0).replace('<details class="t520-frame"', '<details class="t520-frame" open', 1) if m else essentiel)
    body = _section('<div class="t520">' + routines + '</div>', 'Routines du cabinet : heures, jours, signature et exclusions')
    body += _section(web568.preferences(desk, auth, prefix, env, shell=cap), strip=('Voix et conversation rapide', 'Mes agents documentaires'))
    body += _section(_legacy(desk, prefix, auth['csrf'], 'automatismes'), 'Automatismes')
    from .live430 import settings_html
    body += _section(settings_html(desk, prefix, auth['csrf']))
    body += _section(_links(prefix, (('/parametres/agents', 'Agents et règles'), ('/agents-documents', 'Résultats des agents'))), 'Agents documentaires')
    return body


def _documents(desk, auth, prefix, env, cap):
    from . import web440, web_rules568
    body = _section(web440.settings_page(desk, auth, prefix, {}, render=cap))
    body += _section(web_rules568.agendas(desk, auth, prefix, {}, shell=cap))
    body += _section(_legacy(desk, prefix, auth['csrf'], 'modeles'), 'Modèles Word')
    body += _section(_links(prefix, (('/modeles-word', 'Documents du cabinet'), ('/modeles', 'Modèles et livrables'))))
    return body


def _cabinet(desk, auth, prefix, env, cap):
    from . import web567, web490
    body = _section(web567.preferences_page(desk, auth, prefix, env, shell=cap), keep='Accueil téléphonique et WhatsApp')
    sections = web490.confort_sections(desk, auth, prefix, {})
    for key in ('envoi', 'mobile', 'recherche', 'clavier'):
        body += _section(sections.get(key, ''))
    for tab, title in (('cabinet', 'Cabinet et utilisateurs'), ('confidentialite', 'Confidentialité'), ('maintenance', 'Maintenance')):
        body += _section(_legacy(desk, prefix, auth['csrf'], tab), title)
    body += _section(_links(prefix, (('/accueil-administratif', 'Accueil administratif'), ('/a-propos', 'À propos'))))
    return body


# Pages qui gardent leur propre écran (outillage, diagnostics, apprentissage) : accessibles depuis chaque rubrique.
RELATED = (('/mise-en-service', 'Mise en service'), ('/parametres/agents', 'Agents et règles'), ('/apprentissage', 'Apprentissage métier'),
           ('/evaluations', 'Banc juridique'), ('/routage-hybride', 'Routage hybride'), ('/mcp', 'Connecteurs MCP'),
           ('/regles', 'Règles et autonomie'), ('/etat-systeme', 'État du système'), ('/accueil-administratif', 'Accueil administratif'))

BUILDERS = {'connexions': _connexions, 'ia': _ia, 'voix': _voix, 'routines': _routines, 'documents': _documents, 'cabinet': _cabinet}


def rubrique_for(path, args):
    if path in ALIASES:
        return ALIASES[path]
    wanted = str(args.get('rubrique') or LEGACY_TABS.get(str(args.get('tab') or ''), '') or 'connexions')
    return wanted if wanted in BUILDERS else 'connexions'


def page(desk, auth, prefix, env, args, path='/parametres'):
    from .web440 import shell
    current = rubrique_for(path, args)
    cap = Capture()
    nav = '<nav class="p5621-tabs" aria-label="Rubriques des paramètres">' + ''.join(
        '<a href="' + e(prefix + '/parametres?rubrique=' + key, quote=True) + '"' + (' aria-current="page"' if key == current else '')
        + ' title="' + e(desc, quote=True) + '">' + e(label) + '</a>' for key, label, desc in RUBRIQUES) + '</nav>'
    label, desc = next((l, d) for k, l, d in RUBRIQUES if k == current)
    body = '<h1>Paramètres</h1>' + nav + '<p class="p5621-intro"><strong>' + e(label) + '</strong> — ' + e(desc) + '</p>'
    nc = desk.c.get('nextcloud') or {}
    if current == 'connexions' and not nc.get('local_path') and 'example.com' in str(nc.get('url', 'example.com')):   # 5.6.24 (F19)
        body += ('<section class="p5621-first" role="note"><h2>Premier lancement : trois étapes</h2><ol>'
                 '<li><strong>Dossiers</strong> — dans « Nextcloud », cliquez sur <em>📁 Choisir…</em> à côté de « Dossier de travail local » et désignez le dossier de vos dossiers clients '
                 '(ou renseignez l’adresse Nextcloud) ; mettez « / » comme racine si vos dossiers clients sont directement dedans.</li>'
                 '<li><strong>Intelligence artificielle</strong> — vérifiez l’adresse Ollama (locale ou du réseau du cabinet) et le modèle.</li>'
                 '<li><strong>Enregistrer</strong>, puis « Tester Nextcloud / agendas » et « Tester Ollama ».</li></ol>'
                 '<p>La messagerie, les agendas et Invoice Ninja sont facultatifs : leurs fonctions restent inactives tant qu’ils ne sont pas configurés. '
                 'Ce guide disparaît dès qu’un dossier de travail ou Nextcloud est enregistré.</p></section>')
    body += BUILDERS[current](desk, auth, prefix, env, cap)
    body += _section(_links(prefix, RELATED), 'Autres pages de réglage et d’outillage')
    return shell('Paramètres · ' + label, body, prefix, auth['csrf'], '/parametres', cap.head)

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

# 5.6.60 : rubriques regroupées en cinq familles (menu latéral), vue d'ensemble, recherche d'un réglage, sommaire et sections repliables.
# Aucun réglage n'est retiré : les mêmes formulaires sont composés autrement ; les anciennes adresses et rubriques restent servies.
RUBRIQUES = (
    ('apercu', 'Vue d’ensemble', 'État des réglages, premiers pas et accès rapide à chaque rubrique'),
    ('connexions', 'Connexions', 'Nextcloud, messagerie (IMAP, SMTP), agendas, Ollama, Invoice Ninja, services externes, Talk, Microsoft 365'),
    ('messagerie', 'Courriels et accueil', 'Adresses du cabinet, signature, police, copie cachée, règles de classement, envoi, accueil téléphonique et WhatsApp'),
    ('ia', 'Modèles d’IA', 'Profil des modèles, routage, régime économe, consommation, IA externe, fournisseurs, connecteurs'),
    ('voix', 'Voix', 'Synthèse, dictée, conversation, vitesse, mode discret, assistant, tests'),
    ('recherche', 'Recherche juridique et veille', 'Sources juridiques (JusticeLibre, OpenLegi, GoodLegal…), veille : thèmes, flux, fréquence, Tavily'),
    ('routines', 'Routines et automatismes', 'Heures, jours, signature, exclusions, briefing, rôles, automatismes, agents documentaires'),
    ('documents', 'Documents et procédure', 'Éditeur de documents, brouillons, avis de procédure, rôle par dossier, agendas, modèles Word'),
    ('cabinet', 'Cabinet, comptes et confidentialité', 'Comptes et mots de passe, cabinet et utilisateurs, confidentialité, droits et sécurité'),
    ('poste', 'Confort et maintenance', 'Mobile, recherche, raccourcis clavier, maintenance, diagnostic de l’agent'),
)
FAMILIES = (('Démarrer', ('apercu',)), ('Connexions et courriels', ('connexions', 'messagerie')),
            ('Intelligence artificielle', ('ia', 'voix', 'recherche')), ('Travail de l’agent', ('routines', 'documents')),
            ('Cabinet', ('cabinet', 'poste')))
KEYWORDS = {'connexions': 'imap smtp mot de passe serveur nextcloud webdav caldav agenda google outlook ollama invoice ninja talk api connexion',
            'messagerie': 'signature police bcc copie cachee regles tri envoyer envoi whatsapp telephone twilio identites adresses',
            'ia': 'modele profil temperature contexte mistral openai openrouter anthropic economie quota hybride externe pseudonymisation',
            'voix': 'dictee transcription synthese kokoro espeak whisper micro vitesse',
            'recherche': 'legifrance judilibre justicelibre openlegi goodlegal librejustice tavily flux rss themes veille jurisprudence',
            'routines': 'briefing matin bilan heures jours exclusions roles automatismes initiatives agents',
            'documents': 'word onlyoffice collabora brouillon versions procedure role dossier modeles agendas',
            'cabinet': 'compte administrateur identifiant mot de passe utilisateurs confidentialite droits acces securite',
            'poste': 'mobile clavier raccourcis maintenance purge sauvegarde diagnostic blocage file etat systeme'}
RUBRIQUE_ALIASES = {'sources': 'recherche', 'veille': 'recherche', 'comptes': 'cabinet'}   # anciennes rubriques fusionnées
ALIASES = {'/parametres/connexions': 'connexions', '/parametres/assistant': 'voix', '/parametres/proactivite': 'routines',
           '/ia-externe': 'ia', '/confort': 'poste', '/atelier/reglages': 'documents', '/parametres/agendas': 'documents'}
LEGACY_TABS = {'connexions': 'connexions', 'ia': 'ia', 'extensions': 'ia', 'modeles': 'documents', 'automatismes': 'routines',
               'cabinet': 'cabinet', 'confidentialite': 'cabinet', 'maintenance': 'poste'}
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
    # 5.6.60 : section repliable ; son premier titre devient l'intitulé (le titre d'origine reste dans la page, masqué)
    m = re.search(r'<h2(\s[^>]*)?>(.*?)</h2>', html, flags=re.S)
    if not m or m.start() > 400:
        return '<div class="p5621-section">' + html + '</div>'
    label = re.sub(r'<[^>]+>', '', m.group(2)).strip()
    html = html[:m.start()] + '<i class="p5660-h" aria-hidden="true"></i>' + html[m.start():]   # le titre d'origine reste intact, masqué en CSS
    slug = 's-' + re.sub(r'[^a-z0-9]+', '-', re.sub(r'[̀-ͯ]', '', __import__('unicodedata').normalize('NFD', label.lower()))).strip('-')[:60]
    return ('<details class="p5621-section p5660-sec" id="%s"><summary>%s</summary><div class="p5660-body">%s</div></details>'
            % (e(slug, quote=True), e(label), html))


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
    html = web567.connections_page(desk, auth, prefix, env, shell=cap, exclude=VOICE_GROUPS + ('Profil du modèle',))
    from .integration5636 import settings_html as _app_nextcloud   # 5.6.36 : comptes Nextcloud reliés (clés personnelles révocables)
    from .microsoft5636 import settings_html as _microsoft   # 5.6.36 : agenda Microsoft 365 (Outlook)
    from .talk5643 import settings_html as _talk   # 5.6.43 (§ 22) : page Talk facultative, propre à chaque utilisateur
    owner, role52 = web567.actor(env)
    from .connexions5652 import settings_html as _connexions52   # 5.6.52 (I01 à I15) : connexions API utilisables par l'IA
    from .microsoft5636 import redirect_uri as _ms_redirect
    from urllib.parse import parse_qs
    _args52 = {k: v[0] for k, v in parse_qs(env.get('QUERY_STRING', '')).items()}
    _retour52 = _ms_redirect(env, prefix).rsplit('/', 1)[0] + '/connexions-retour'
    return (_section(_connexions52(desk, prefix, owner, role52, _args52, _retour52)) +
            _section(html) + _section(_app_nextcloud(desk, prefix, auth)) + _section(_talk(desk, owner, prefix)) + _section(_microsoft(desk, prefix, env)) +
            _section(_links(prefix, (('/mise-en-service', 'Mise en service : contrôles des services réels'),)), 'Vérifier'))


def _ia(desk, auth, prefix, env, cap):
    from . import ia540, web5634
    body = web5634.profile_panel(desk, prefix)   # 5.6.34 : Modèles locaux, profil Juridique, Tester, Restaurer
    body += _section(ia540.page(desk, prefix, cap, auth['csrf']))
    try:   # 5.6.62 : ChatGPT avec l'abonnement de l'utilisateur (sans clé API)
        from .chatgpt5662 import html as _chatgpt
        from .web5662 import local_browser
        from .web567 import actor as _actor
        body += _section(_chatgpt(desk, prefix, _actor(env)[1], local_browser(env)))
    except Exception:   # noqa: BLE001
        pass
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


def _auto_versions_html(desk):
    """5.6.57 : autorisation des nouvelles versions de brouillon (création de fichiers de travail seulement)."""
    on = bool(desk.settings('documents5657:brouillons_auto', False))
    return ('<section class="ax-card" id="brouillons5657"><h2>Nouvelles versions de brouillon</h2><p class="ax-muted">Autorisez une fois l’agent à créer '
            'lui-même la nouvelle version de travail d’un projet (conclusions, courrier, contrat…) dans le sous-dossier de brouillons du dossier. '
            'Il ne remplace jamais un fichier, n’envoie rien, ne dépose rien et ne signe rien ; le contenu reste à relire.</p>'
            '<form class="m5-form" data-api="m5657/documents/auto" data-reload><label class="r52-day"><input type="checkbox" name="actif"%s> '
            'Créer automatiquement les nouvelles versions de brouillon</label><button class="ax-btn" type="submit">Enregistrer</button>'
            '<p role="status" class="m5-status"></p></form></section>') % (' checked' if on else '')


def _documents(desk, auth, prefix, env, cap):
    from . import web440, web_rules568
    body = _section(web440.settings_page(desk, auth, prefix, {}, render=cap))
    body += _section(_auto_versions_html(desk))                 # 5.6.57 : nouvelles versions de brouillon
    body += _section(web_rules568.agendas(desk, auth, prefix, {}, shell=cap))
    body += _section(_legacy(desk, prefix, auth['csrf'], 'modeles'), 'Modèles Word')
    body += _section(_links(prefix, (('/modeles-word', 'Documents du cabinet'), ('/modeles', 'Modèles et livrables'))))
    return body


def _cabinet(desk, auth, prefix, env, cap):
    """5.6.60 : comptes (ancienne rubrique « Comptes »), cabinet et utilisateurs, confidentialité, droits et sécurité."""
    body = _comptes(desk, auth, prefix, env, cap)
    for tab, title in (('cabinet', 'Cabinet et utilisateurs'), ('confidentialite', 'Confidentialité')):
        body += _section(_legacy(desk, prefix, auth['csrf'], tab), title)
    body += _section(_links(prefix, (('/parametres/droits', 'Droits et accès des comptes'), ('/securite-ia', 'Sécurité de l’IA'),
                                     ('/tracabilite', 'Traçabilité : données vues par le modèle'))), 'Droits, sécurité et traçabilité')
    return body


def _poste(desk, auth, prefix, env, cap):
    """5.6.60 : confort (mobile, recherche, clavier), maintenance et diagnostic de l'agent."""
    from . import web490
    sections = web490.confort_sections(desk, auth, prefix, {})
    body = ''
    for key in ('mobile', 'recherche', 'clavier'):
        body += _section(sections.get(key, ''))
    body += _section(_legacy(desk, prefix, auth['csrf'], 'maintenance'), 'Maintenance')
    try:   # 5.6.61 : avis de nouvelle version et mise à jour signée
        from .maj5661 import html as _maj
        from .web567 import actor as _actor
        body += _section(_maj(desk, prefix, _actor(env)[1]))
    except Exception:   # noqa: BLE001
        pass
    try:
        from .blocage5660 import html as _blocages
        diag = _blocages(desk, prefix)
    except Exception:   # noqa: BLE001
        diag = ''
    body += _section(diag + _links(prefix, (('/diagnostic', 'Pourquoi rien n’est produit ?'), ('/etat-systeme', 'État du système'),
                                            ('/mise-en-service', 'Mise en service'))), 'Diagnostic de l’agent')
    body += _section(_links(prefix, (('/accueil-administratif', 'Accueil administratif'), ('/a-propos', 'À propos'))), 'Autres pages')
    return body


def _recherche(desk, auth, prefix, env, cap):
    """5.6.60 : sources juridiques et veille (anciennes rubriques « Sources juridiques » et « Veille et recherche Internet »)."""
    return _sources(desk, auth, prefix, env, cap) + _veille(desk, auth, prefix, env, cap)


def _status_rows(desk):
    """Vue d'ensemble : état lisible de chaque domaine, d'après la configuration enregistrée (aucun test réseau)."""
    c = desk.c
    mail = c.get('mail') or {}
    nc = c.get('nextcloud') or {}
    ollama = c.get('ollama') or {}
    rows = [('connexions', 'Messagerie', ('Configurée — ' + str(mail.get('host'))) if mail.get('host') and mail.get('enabled', True) is not False else 'Non configurée (facultative)'),
            ('connexions', 'Dossiers du cabinet', 'Dossier local : ' + str(nc.get('local_path')) if nc.get('local_path') else
             ('Nextcloud : ' + str(nc.get('url')) if nc.get('url') and 'example.com' not in str(nc.get('url')) else 'À configurer')),
            ('ia', 'Modèle d’IA local', (str(ollama.get('model')) + ((' — ' + str(ollama.get('url'))) if ollama.get('url') else '')) if ollama.get('model') else 'À configurer')]
    try:
        from .veille5632 import profile
        themes = profile(desk, 'cabinet').get('themes') or []
        rows.append(('recherche', 'Veille juridique', ('%d thème(s) suivi(s)' % len(themes)) if themes else 'Aucun thème suivi'))
    except Exception:   # noqa: BLE001
        pass
    try:
        from .blocage5660 import diagnose
        blocking = [f for f in diagnose(desk.db, c['state_dir']) if f['severity'] == 'bloquant']
        rows.append(('poste', 'File de travail de l’agent', ('%d blocage(s) : %s' % (len(blocking), blocking[0]['title'])) if blocking else 'Aucun blocage détecté'))
    except Exception:   # noqa: BLE001
        pass
    try:   # 5.6.61 : mises à jour
        from .maj5661 import available, installed_version
        new = available(desk)
        rows.append(('poste', 'Version d’AxiorHub', ('%s — nouvelle version %s disponible' % (installed_version(), new)) if new else installed_version()))
    except Exception:   # noqa: BLE001
        pass
    try:
        from .bouclier5659 import counts
        n = counts(desk)['a_examiner']
        rows.append(('cabinet', 'Sécurité de l’IA', ('%d alerte(s) à examiner' % n) if n else 'Aucune alerte à examiner'))
    except Exception:   # noqa: BLE001
        pass
    return rows


def _apercu(desk, auth, prefix, env, cap):
    rows = ''.join('<tr><th scope="row">%s</th><td>%s</td><td><a href="%s">Régler</a></td></tr>' % (
        e(label), e(value), e(prefix + '/parametres?rubrique=' + key, quote=True)) for key, label, value in _status_rows(desk))
    cards = ''
    for family, keys in FAMILIES[1:]:
        cards += '<section class="p5660-card"><h3>%s</h3><ul>%s</ul></section>' % (e(family), ''.join(
            '<li><a href="%s"><strong>%s</strong></a><span class="ax-muted">%s</span></li>' % (
                e(prefix + '/parametres?rubrique=' + k, quote=True), e(dict((r[0], r[1]) for r in RUBRIQUES)[k]), e(dict((r[0], r[2]) for r in RUBRIQUES)[k]))
            for k in keys))
    return (_section('<table class="p5660-status"><tbody>%s</tbody></table>' % rows, 'État des réglages') +
            '<div class="p5660-cards">%s</div>' % cards)


# Pages qui gardent leur propre écran (outillage, diagnostics, apprentissage) : accessibles depuis chaque rubrique.
RELATED = (('/mise-en-service', 'Mise en service'), ('/parametres/agents', 'Agents et règles'), ('/apprentissage', 'Apprentissage métier'),
           ('/evaluations', 'Banc juridique'), ('/routage-hybride', 'Routage hybride'), ('/mcp', 'Connecteurs MCP'),
           ('/regles', 'Règles et autonomie'), ('/etat-systeme', 'État du système'), ('/accueil-administratif', 'Accueil administratif'))

def _messagerie(desk, auth, prefix, env, cap):
    from .web5632 import courrier_section
    from .envoi5636 import settings_html
    from urllib.parse import parse_qs
    from .sens5643 import identities_html                      # 5.6.56 (audit 5.6.55, § 3.1)
    args = {k: v[0] for k, v in parse_qs(env.get('QUERY_STRING', ''), max_num_fields=10).items()}
    body = _section(identities_html(desk.c.get('mail', {}), args) + settings_html(desk, prefix) + courrier_section(desk, prefix))
    # 5.6.60 : l'envoi et l'accueil téléphonique rejoignent les courriels (venus de l'ancienne rubrique « Cabinet et accueil »)
    from . import web567, web490
    body += _section(web490.confort_sections(desk, auth, prefix, {}).get('envoi', ''))
    body += _section(web567.preferences_page(desk, auth, prefix, env, shell=cap), keep='Accueil téléphonique et WhatsApp')
    return body


def _veille(desk, auth, prefix, env, cap):
    from .web5632 import veille_settings
    from .web567 import actor
    return _section(veille_settings(desk, env, prefix, admin=actor(env)[1] == 'administrateur'))


def _sources(desk, auth, prefix, env, cap):
    from .web5632 import sources_section
    return _section(sources_section(desk))


def _comptes(desk, auth, prefix, env, cap):
    from .comptes5642 import section_html   # 5.6.42 : administration du compte de l'interface
    return _section(section_html(desk, auth, prefix))


BUILDERS = {'apercu': _apercu, 'connexions': _connexions, 'ia': _ia, 'voix': _voix, 'routines': _routines, 'documents': _documents,
            'cabinet': _cabinet, 'poste': _poste, 'recherche': _recherche, 'messagerie': _messagerie}


def rubrique_for(path, args):
    if path in ALIASES:
        return ALIASES[path]
    wanted = str(args.get('rubrique') or LEGACY_TABS.get(str(args.get('tab') or ''), '') or 'connexions')
    wanted = RUBRIQUE_ALIASES.get(wanted, wanted)
    return wanted if wanted in BUILDERS else 'connexions'


def _sidebar(prefix, current):
    """Menu latéral par familles, avec une recherche qui filtre les rubriques par mots-clés (libellé, description, mots usuels)."""
    labels = {k: (l, d) for k, l, d in RUBRIQUES}
    groups = ''
    for family, keys in FAMILIES:
        links = ''.join('<a href="%s"%s data-p5660-keys="%s" title="%s">%s</a>' % (
            e(prefix + '/parametres?rubrique=' + k, quote=True), ' aria-current="page"' if k == current else '',
            e((labels[k][0] + ' ' + labels[k][1] + ' ' + KEYWORDS.get(k, '')).lower(), quote=True), e(labels[k][1], quote=True), e(labels[k][0]))
            for k in keys)
        groups += '<div class="p5660-family"><p class="p5660-family-title">%s</p>%s</div>' % (e(family), links)
    tools = ''.join('<a href="%s">%s</a>' % (e(prefix + p, quote=True), e(label)) for p, label in RELATED)
    return ('<aside class="p5660-side"><label class="p5660-find">Trouver un réglage<input type="search" data-p5660-find '
            'placeholder="Ex. signature, Ollama"></label><nav class="p5621-tabs" data-p5660-nav aria-label="Rubriques des paramètres">%s</nav>'
            '<details class="p5660-tools"><summary>Autres pages de réglage et d’outillage</summary>'
            # 5.6.62 : pas de <nav> ici — les règles du menu latéral sombre (« nav a », « aside nav a ») rendaient ces liens blancs sur blanc
            '<div class="p5662-tools" role="navigation" aria-label="Autres pages">%s</div></details></aside>'
            % (groups, tools))


def _toc(body):
    found = re.findall(r'<details class="p5621-section p5660-sec" id="([^"]+)"><summary>(.*?)</summary>', body)
    if len(found) < 2:
        return ''
    return ('<nav class="p5660-toc" aria-label="Sur cette page"><span>Sur cette page :</span>%s'
            '<button type="button" class="ax-btn ghost" data-p5660-all>Tout déplier</button></nav>' % ''.join(
                '<a href="#%s">%s</a>' % (ident, label) for ident, label in found))


def page(desk, auth, prefix, env, args, path='/parametres'):
    from .web440 import shell
    current = rubrique_for(path, args)
    cap = Capture()
    label, desc = next((l, d) for k, l, d in RUBRIQUES if k == current)
    from .i18n5631 import th
    guides = ('<div class="p5631-guides"><a class="button" href="' + e(prefix + '/guide-installation', quote=True) + '">' + th('fn.guide_installation.label') +
              '</a><a class="button ghost" href="' + e(prefix + '/guide-utilisation', quote=True) + '">' + th('fn.guide_utilisation.label') + '</a></div>')   # 5.6.31 (GI01, GU01)
    body = '<p class="p5621-intro"><strong>' + e(label) + '</strong> — ' + e(desc) + '</p>'
    nc = desk.c.get('nextcloud') or {}
    if current == 'connexions' and not nc.get('local_path') and 'example.com' in str(nc.get('url', 'example.com')):   # 5.6.24 (F19)
        body += ('<section class="p5621-first" role="note"><h2>Premier lancement : trois étapes</h2><ol>'
                 '<li><strong>Dossiers</strong> — dans « Nextcloud », cliquez sur <em>📁 Choisir…</em> à côté de « Dossier de travail local » et désignez le dossier de vos dossiers clients '
                 '(ou renseignez l’adresse Nextcloud) ; mettez « / » comme racine si vos dossiers clients sont directement dedans.</li>'
                 '<li><strong>Intelligence artificielle</strong> — vérifiez l’adresse Ollama (locale ou du réseau du cabinet) et le modèle.</li>'
                 '<li><strong>Enregistrer</strong>, puis « Tester Nextcloud / agendas » et « Tester Ollama ».</li></ol>'
                 '<p>La messagerie, les agendas et Invoice Ninja sont facultatifs : leurs fonctions restent inactives tant qu’ils ne sont pas configurés. '
                 'Ce guide disparaît dès qu’un dossier de travail ou Nextcloud est enregistré.</p></section>')
    content = BUILDERS[current](desk, auth, prefix, env, cap)
    content = content.replace('<details class="p5621-section p5660-sec"', '<details open class="p5621-section p5660-sec"', 1)   # la première ouverte
    body += _toc(content.replace('<details open class=', '<details class=', 1)) + content
    page_html = ('<h1>Paramètres</h1>' + guides + '<div class="p5660-layout">' + _sidebar(prefix, current) +
                 '<div class="p5660-main">' + body + '</div></div>')
    head = cap.head + '<script defer src="' + e(prefix, quote=True) + '/static/v5660.js"></script>'
    return shell('Paramètres · ' + label, page_html, prefix, auth['csrf'], '/parametres', head)

"""5.6.36 : complément Word (Office) — AxiorHub dans Word, sur le document ouvert.

- Manifeste téléchargeable (adresse du serveur AxiorHub du cabinet, HTTPS obligatoire pour Office), à charger dans Word
  (« Mes compléments › Charger mon complément ») ou à déployer pour tout le cabinet (centre d'administration Microsoft 365).
- Volet AxiorHub dans Word :
  * Playbook : les positions de négociation du cabinet comparées au contrat ouvert ; chaque écart proposé est appliqué en
    modification suivie (révisions Word), après vérification que le paragraphe n'a pas changé ;
  * Mémos : les mémos validés du cabinet insérés au curseur, avec leurs sources ;
  * Reformuler : la sélection réécrite selon une consigne (modèle d'IA du cabinet), remplacée en modification suivie.
- Le volet est une page du serveur AxiorHub (même connexion, même jeton CSRF) ; le document n'est lu que lorsque l'avocat clique.
"""
from html import escape as e
import json
import re
import uuid

from .common import Stop

ADDIN_VERSION = '.'.join(('1', '0', '0', '0'))   # version du manifeste Office (quatre nombres)
OFFICE_JS = 'https://appsforoffice.microsoft.com/lib/1/hosted/office.js'
FRAME_ANCESTORS = ("'self' https://*.officeapps.live.com https://*.office.com https://*.office365.com https://*.microsoft365.com "
                   "https://*.sharepoint.com https://*.cloud.microsoft")


def base_url(env, prefix):
    scheme = (env.get('HTTP_X_FORWARDED_PROTO') or env.get('wsgi.url_scheme') or 'https').split(',')[0].strip()
    host = (env.get('HTTP_X_FORWARDED_HOST') or env.get('HTTP_HOST') or 'localhost').split(',')[0].strip()
    return '%s://%s%s' % (scheme, host, prefix)


def manifest(env, prefix):
    base = base_url(env, prefix)
    ident = str(uuid.uuid5(uuid.NAMESPACE_URL, 'axiorhub-word|' + base))
    host = re.match(r'https?://[^/]+', base).group(0)
    icon = base + '/static/axiorhub-icon-192.png'
    pane = base + '/complement-word/volet'
    x = lambda s: e(s, quote=True)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:bt="http://schemas.microsoft.com/office/officeappbasictypes/1.0" xmlns:ov="http://schemas.microsoft.com/office/taskpaneappversionoverrides" '
            'xsi:type="TaskPaneApp">\n'
            '  <Id>%s</Id>\n  <Version>%s</Version>\n  <ProviderName>AxiorHub Pilote</ProviderName>\n  <DefaultLocale>fr-FR</DefaultLocale>\n'
            '  <DisplayName DefaultValue="AxiorHub"/>\n'
            '  <Description DefaultValue="Playbooks de négociation, mémos du cabinet et reformulation, depuis le serveur AxiorHub du cabinet."/>\n'
            '  <IconUrl DefaultValue="%s"/>\n  <HighResolutionIconUrl DefaultValue="%s"/>\n  <SupportUrl DefaultValue="%s"/>\n'
            '  <AppDomains>\n    <AppDomain>%s</AppDomain>\n  </AppDomains>\n'
            '  <Hosts>\n    <Host Name="Document"/>\n  </Hosts>\n'
            '  <Requirements>\n    <Sets DefaultMinVersion="1.3">\n      <Set Name="WordApi" MinVersion="1.3"/>\n    </Sets>\n  </Requirements>\n'
            '  <DefaultSettings>\n    <SourceLocation DefaultValue="%s"/>\n  </DefaultSettings>\n'
            '  <Permissions>ReadWriteDocument</Permissions>\n'
            '  <VersionOverrides xmlns="http://schemas.microsoft.com/office/taskpaneappversionoverrides" xsi:type="VersionOverridesV1_0">\n'
            '    <Hosts>\n      <Host xsi:type="Document">\n        <DesktopFormFactor>\n'
            '          <ExtensionPoint xsi:type="PrimaryCommandSurface">\n            <OfficeTab id="TabHome">\n'
            '              <Group id="AxiorHub.Group">\n                <Label resid="AxiorHub.Label"/>\n'
            '                <Icon>\n                  <bt:Image size="16" resid="AxiorHub.Icon"/>\n                  <bt:Image size="32" resid="AxiorHub.Icon"/>\n'
            '                  <bt:Image size="80" resid="AxiorHub.Icon"/>\n                </Icon>\n'
            '                <Control xsi:type="Button" id="AxiorHub.Open">\n                  <Label resid="AxiorHub.Label"/>\n'
            '                  <Supertip>\n                    <Title resid="AxiorHub.Label"/>\n                    <Description resid="AxiorHub.Tip"/>\n                  </Supertip>\n'
            '                  <Icon>\n                    <bt:Image size="16" resid="AxiorHub.Icon"/>\n                    <bt:Image size="32" resid="AxiorHub.Icon"/>\n'
            '                    <bt:Image size="80" resid="AxiorHub.Icon"/>\n                  </Icon>\n'
            '                  <Action xsi:type="ShowTaskpane">\n                    <TaskpaneId>AxiorHub.Pane</TaskpaneId>\n'
            '                    <SourceLocation resid="AxiorHub.Pane.Url"/>\n                  </Action>\n                </Control>\n'
            '              </Group>\n            </OfficeTab>\n          </ExtensionPoint>\n        </DesktopFormFactor>\n      </Host>\n    </Hosts>\n'
            '    <Resources>\n      <bt:Images>\n        <bt:Image id="AxiorHub.Icon" DefaultValue="%s"/>\n      </bt:Images>\n'
            '      <bt:Urls>\n        <bt:Url id="AxiorHub.Pane.Url" DefaultValue="%s"/>\n      </bt:Urls>\n'
            '      <bt:ShortStrings>\n        <bt:String id="AxiorHub.Label" DefaultValue="AxiorHub"/>\n      </bt:ShortStrings>\n'
            '      <bt:LongStrings>\n        <bt:String id="AxiorHub.Tip" DefaultValue="Ouvre le volet AxiorHub : playbooks, mémos, reformulation."/>\n      </bt:LongStrings>\n'
            '    </Resources>\n  </VersionOverrides>\n</OfficeApp>\n') % (ident, ADDIN_VERSION, x(icon), x(icon), x(base + '/complement-word'), x(host), x(pane), x(icon), x(pane))


def page_html(desk, prefix, args, env=None):
    env = env or {}
    base = base_url(env, prefix)
    https = base.startswith('https://')
    return ('<h1>Complément Word</h1><p class="ax-muted">AxiorHub dans Word, sur le document ouvert : vos playbooks appliqués en modifications suivies, '
            'les mémos validés du cabinet insérés au curseur, la sélection reformulée selon votre consigne. Le complément se connecte à ce serveur '
            'AxiorHub ; rien ne transite par un service tiers.</p>%s'
            '<section class="ax-card"><h2>1. Télécharger le manifeste</h2><p>Le manifeste indique à Word l’adresse de ce serveur (<code>%s</code>).</p>'
            '<button type="button" class="ax-btn m5-export" data-api="m5636/office/manifeste">Télécharger le manifeste (manifest.xml)</button></section>'
            '<section class="ax-card"><h2>2. L’installer</h2><ul>'
            '<li><strong>Pour vous seul (Word sur Windows ou Mac)</strong> : Word › Insertion › Compléments › Mes compléments › « Charger mon complément » '
            '› choisissez manifest.xml. Sur Windows, si ce bouton manque, placez le manifeste dans un dossier partagé déclaré comme « Catalogue de '
            'compléments approuvés » (Fichier › Options › Centre de gestion de la confidentialité).</li>'
            '<li><strong>Pour tout le cabinet</strong> : centre d’administration Microsoft 365 › Paramètres › Applications intégrées › « Charger des '
            'applications personnalisées » › manifeste ; choisissez les utilisateurs.</li>'
            '<li><strong>Word sur le web</strong> : Accueil › Compléments › Autres compléments › Mes compléments › Charger mon complément.</li></ul>'
            '<p>Le bouton « AxiorHub » apparaît ensuite dans l’onglet Accueil de Word. À la première ouverture, connectez-vous à AxiorHub dans le volet.</p></section>'
            '<section class="ax-card"><h2>3. Préparer vos outils</h2><p>Les playbooks et les mémos se gèrent dans <a href="%s">Outils IA</a> ; seuls les mémos '
            'validés sont proposés dans Word.</p><p><a class="ax-btn ghost" href="%s">Aperçu du volet dans le navigateur</a></p></section>') % (
        '' if https else '<p class="ax-alert">Office exige une adresse HTTPS : ce serveur est consulté en HTTP. Publiez AxiorHub derrière HTTPS '
                         '(certificat valide) avant d’installer le complément.</p>',
        e(base), e(prefix + '/outils-ia?onglet=playbooks', quote=True), e(prefix + '/complement-word/volet', quote=True))


def taskpane(desk, auth, prefix):
    """Volet affiché dans Word (page autonome, sans le menu d'AxiorHub) ; Office.js chargé depuis le CDN de Microsoft."""
    html = ('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="axiorhub-prefix" content="%s"><meta name="axiorhub-csrf" content="%s"><title>AxiorHub</title>'
            '<link rel="stylesheet" href="%s/static/office5636.css"><script src="%s"></script><script defer src="%s/static/office5636.js"></script></head>'
            '<body><header class="w-head"><strong>AxiorHub</strong><span id="w-status" role="status"></span></header>'
            '<p id="w-outside" class="w-note" hidden>Ce volet fonctionne dans Word. Ouvert dans un navigateur, il sert seulement d’aperçu.</p>'
            '<label class="w-row">Dossier<select id="w-matter"><option value="">Aucun</option></select></label>'
            '<details open><summary>Playbook de négociation</summary><label class="w-row">Playbook<select id="w-playbook"></select></label>'
            '<button type="button" id="w-analyse" class="w-btn">Analyser le document</button><div id="w-edits"></div></details>'
            '<details><summary>Mémos du cabinet</summary><input id="w-q" type="search" placeholder="Rechercher un mémo validé"><div id="w-memos"></div></details>'
            '<details><summary>Reformuler la sélection</summary><textarea id="w-instr" rows="3" placeholder="Ex. : plus concis, ton ferme, ajouter une clause de réserve"></textarea>'
            '<button type="button" id="w-rewrite" class="w-btn">Proposer une reformulation</button><div id="w-proposal"></div></details>'
            '<p class="w-note">Toute modification est insérée en modification suivie : acceptez-la ou refusez-la dans Word (Révision).</p></body></html>') % (
        e(prefix, quote=True), e(auth['csrf'], quote=True), e(prefix, quote=True), OFFICE_JS, e(prefix, quote=True))
    csp = ("default-src 'none'; script-src 'self' https://appsforoffice.microsoft.com; connect-src 'self'; img-src 'self' data:; style-src 'self'; "
           "font-src 'self' data:; form-action 'self'; frame-ancestors %s; base-uri 'none'") % FRAME_ANCESTORS
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': html, 'csp': csp, 'frame_office': True}


def rewrite(desk, data, model=None):
    text = str(data.get('text') or '').strip()
    instruction = re.sub(r'\s+', ' ', str(data.get('instruction') or '')).strip()[:600]
    if not text or len(text) > 12000:
        raise Stop('selection_word_requise')
    if not instruction:
        raise Stop('consigne_requise')
    if model is None:
        from .docrequest520 import _model
        model = _model(desk, str(data.get('matter') or ''))
    raw = model.complete([
        {'role': 'system', 'content': 'Tu réécris un passage d’un document juridique français selon la consigne de l’avocat. Conserve le sens juridique, '
                                      'les montants, dates, noms et références à l’identique ; n’ajoute aucun fait. JSON {"texte", "remarque"} ; « remarque » '
                                      'signale en une phrase ce qui mérite l’attention de l’avocat (ou "").'},
        {'role': 'user', 'content': json.dumps({'consigne': instruction, 'passage': text}, ensure_ascii=False)}], temperature=0.2, max_tokens=4000,
        json_schema={'type': 'object', 'additionalProperties': False, 'required': ['texte', 'remarque'],
                     'properties': {'texte': {'type': 'string', 'maxLength': 16000}, 'remarque': {'type': 'string', 'maxLength': 400}}})
    out = json.loads(raw) if isinstance(raw, str) else dict(raw)
    new = str(out.get('texte') or '').strip()
    if not new:
        raise Stop('reformulation_vide')
    numbers = set(re.findall(r'\d[\d .,]*\d|\d', text))
    lost = sorted(n for n in numbers if n.replace(' ', '') not in new.replace(' ', ''))
    note = str(out.get('remarque') or '')
    if lost:
        note = ('Attention : chiffre(s) absent(s) de la reformulation : %s. ' % ', '.join(lost[:6])) + note
    return {'text': new, 'note': note.strip()}


def context(desk):
    from .common import load_matters, matter_display
    from . import outils5636
    outils5636.schema(desk)
    try:
        matters = [{'id': m['id'], 'label': matter_display(m)} for m in load_matters(desk.c)]
    except Stop:
        matters = []
    playbooks = [{'id': r['id'], 'name': r['name']} for r in desk.db.execute('SELECT id, name FROM playbooks5636 WHERE archived=0 ORDER BY name')]
    return {'matters': sorted(matters, key=lambda m: m['label'].casefold()), 'playbooks': playbooks}


def api_get(desk, route, args, env=None, prefix=''):
    if route == 'office/contexte':
        return context(desk)
    if route == 'office/manifeste':
        return {'download': {'filename': 'manifest.xml', 'mime': 'application/xml', 'text': manifest(env or {}, prefix)}}
    return None


def api_post(desk, route, data, owner):
    if route == 'office/reformuler':
        return rewrite(desk, data)
    return None

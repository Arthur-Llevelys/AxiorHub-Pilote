"""5.6.89 : visionneuse commune LivrableViewer, côté serveur (audit 5.6.88, §§ 49 et 50 ; contrat DeliverableView).

Une seule lecture pour toutes les pages : le détail se construit à partir du registre et des droits, jamais d'un chemin envoyé par le navigateur. Chaque format a son aperçu ; un format non pris en charge dit sa limite
au lieu de simuler un rendu. Le contenu d'un document est une donnée : HTML assaini, images et liens externes neutralisés, affiché dans un cadre sans script, sous une politique qui interdit tout chargement extérieur.

Aucun cache de rendu : chaque aperçu est recalculé après contrôle des droits, sur la version demandée. Une révocation vaut donc immédiatement, et un autre cabinet ou compte ne peut rien récupérer.
"""
import csv
from email import message_from_bytes, policy
from email.utils import getaddresses
from html import escape as e
from html.parser import HTMLParser
import hashlib
import io
import json
from pathlib import PurePosixPath
import re

from .common import Stop
from . import livrables5689 as liv

IMAGES = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
DOCUMENTS = ('.docx', '.odt')
TABLES = ('.xlsx', '.ods', '.csv')
TEXTS = ('.txt', '.md')
EDITABLE_AI = ('.docx',)
ALLOWED = {'p', 'br', 'b', 'strong', 'i', 'em', 'u', 's', 'ul', 'ol', 'li', 'blockquote', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'pre', 'code', 'hr', 'sub', 'sup', 'table', 'thead', 'tbody', 'tr', 'td', 'th'}
VOID = {'br', 'hr'}
# 5.6.93 (audit 5.6.90, N04) : deux familles distinctes. Les éléments vides du HTML n'ont ni contenu ni fermeture : ignorés ou remplacés, ils ne changent jamais la profondeur
# du contenu masqué (avant, un simple <meta> laissait le compteur positif et tout le corps disparaissait). Les éléments masqués voient tout leur contenu retiré.
VIDES = {'area', 'base', 'basefont', 'bgsound', 'br', 'col', 'embed', 'frame', 'hr', 'image', 'img', 'input', 'keygen', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
MASQUES = {'script', 'style', 'head', 'title', 'iframe', 'object', 'svg', 'math', 'template', 'button', 'select', 'textarea', 'video', 'audio', 'applet', 'frameset', 'noembed', 'noframes'}
DROP = (VIDES - ALLOWED) | MASQUES | {'form'}   # jamais reproduites ; le contenu d'un <form> reste lisible, ses commandes non
FRAME_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
FRAME_CSS = ('body{font:15px/1.55 system-ui,sans-serif;margin:0;padding:18px;color:#1c1c1c;background:#fff;word-break:break-word}table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #8a8a8a;padding:4px 8px;text-align:left}'
             '.a79-center{text-align:center}.a79-right{text-align:right}.a79-li{margin-left:1.2em}.a89-tete{border-bottom:1px solid #8a8a8a;margin-bottom:12px;padding-bottom:8px}.a89-bloque{font-style:italic}'
             '@media (prefers-color-scheme:dark){body{color:#f0f0f0;background:#1c1c1c}}')


class _Assaini(HTMLParser):
    """Liste blanche stricte : aucun attribut hors fusion de cellules, aucune image, aucun lien actif (l'adresse reste lisible, en texte)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.images, self.liens, self.visibles = [], [], 0, 0, 0
        self.href = []
        self.masque = []        # 5.6.93 : pile des éléments masqués ouverts, avec la profondeur visible à leur ouverture
        self.corps = False      # le corps a commencé (balise <body> ou texte visible)

    @property
    def hidden(self):
        return len(self.masque)

    def handle_starttag(self, tag, attrs):
        if tag == 'body':       # un <head> resté ouvert se termine au début du corps, comme dans un navigateur
            heads = [i for i, (t, _) in enumerate(self.masque) if t == 'head']
            if heads:
                del self.masque[heads[0]:]
            self.corps = True
            return
        if tag == 'head' and self.corps:    # un <head> tardif, après le début du corps, est ignoré comme dans un navigateur
            return
        if tag in VIDES:        # sans contenu ni fermeture : la profondeur masquée ne bouge pas
            if self.masque:
                return
            if tag == 'img' or (tag == 'input' and str(dict(attrs).get('type') or '').lower() == 'image'):
                self.images += 1
                self.out.append('<span class="a89-bloque">[image non chargée]</span>')
            elif tag in VOID:
                self.out.append('<%s>' % tag)
            return
        if tag in MASQUES:
            self.masque.append((tag, len(self.stack)))
            return
        if self.masque:
            return
        if tag == 'a':
            self.href.append(str(dict(attrs).get('href') or '').strip())
            return
        if tag not in ALLOWED:
            return
        keep = ''.join(' %s="%s"' % (k, e(v, quote=True)) for k, v in attrs if k in ('colspan', 'rowspan') and tag in ('td', 'th') and str(v).isdigit())
        self.out.append('<%s%s>' % (tag, keep))
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        """<br/>, <meta/>, <div/> : un élément vide ne reçoit jamais de fermeture ; un autre s'ouvre et se ferme aussitôt."""
        self.handle_starttag(tag, attrs)
        if tag not in VIDES and tag != 'body':
            self.handle_endtag(tag)

    def _lien(self):
        href = self.href.pop() if self.href else ''
        if href:
            self.liens += 1
            self.out.append(' <span class="a89-bloque">[lien : %s]</span>' % e(href[:200]))

    def handle_endtag(self, tag):
        if tag in VIDES:        # </br>, </meta> : rien à fermer
            return
        if self.masque:
            ouverts = [i for i, (t, _) in enumerate(self.masque) if t == tag]
            if ouverts:
                del self.masque[ouverts[-1]:]
                return
            # fermeture d'un élément visible ouvert avant la zone masquée : la balise masquée n'a jamais été fermée, la zone s'arrête avec son parent
            if tag not in self.stack[:self.masque[0][1]]:
                return
            del self.masque[:]
        if tag == 'a':
            self._lien()
            return
        if tag not in ALLOWED or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append('</%s>' % top)
            if top == tag:
                break

    def handle_data(self, data):
        if not self.masque:
            self.visibles += len(data.strip())
            self.corps = self.corps or bool(data.strip())
            self.out.append(e(data))

    def resultat(self):
        while self.href:        # lien jamais fermé : l'adresse reste affichée, inerte
            self._lien()
        while self.stack:
            self.out.append('</%s>' % self.stack.pop())
        return ''.join(self.out)


def assainir(html):
    """(HTML sûr, images bloquées, liens neutralisés)."""
    parser = _Assaini()
    parser.feed(str(html or ''))
    parser.close()
    return parser.resultat(), parser.images, parser.liens


def nature_apercu(row):
    ext = PurePosixPath(row.get('path') or '').suffix.lower()
    if row.get('kind') in ('courriel',) and not row.get('path'):
        return 'email'
    if not row.get('path'):
        return 'text'
    if ext in DOCUMENTS:
        return 'document'
    if ext == '.pdf':
        return 'pdf'
    if ext in IMAGES:
        return 'image'
    if ext == '.eml':
        return 'email'
    if ext in TABLES:
        return 'table'
    if ext in TEXTS:
        return 'text'
    return 'unsupported'


def _client(desk, client=None):
    from .office440 import dav_client
    return client or dav_client(desk)


def octets(desk, row, client=None, version_demandee=''):
    """Octets exacts de la version demandée, jamais une autre. Le chemin vient du registre.

    Version courante dont le fichier a changé : la version nouvelle est constatée et, sauf version explicitement demandée, c'est elle qui s'affiche, avec le registre à jour. Une version remplacée n'est servie que si
    le fichier contient encore ces octets ; sinon on le dit, plutôt que de substituer silencieusement le contenu du moment."""
    if not row.get('path'):
        raise Stop('fichier_pas_encore_cree')
    client = _client(desk, client)
    info = client.stat(row['path'])
    raw = client.download({**info, 'path': row['path']})
    sha = hashlib.sha256(raw).hexdigest()
    if row['sha256'] and sha == row['sha256']:
        return row, raw
    if version_demandee or row['lifecycle'] != 'current':
        raise Stop('version_plus_disponible')
    if not row['sha256']:   # première lecture d'un fichier connu par un journal sans empreinte : la lecture devient la preuve
        return liv.manifeste(desk, row, row['path'], sha, info, len(raw)), raw
    return liv.nouvelle_version_constatee(desk, row, row['path'], raw, info), raw


def _table_csv(raw):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    dialect = ';' if text[:2000].count(';') > text[:2000].count(',') else ','
    rows = list(csv.reader(io.StringIO(text), delimiter=dialect))[:200]
    body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % e(c) for c in r[:40]) for r in rows)
    return '<table><tbody>%s</tbody></table>' % body if body else '<p>Fichier vide.</p>'


def _adresses(value):
    return ', '.join(a or n for n, a in getaddresses([str(value or '')]))


NOTE_VIDE = 'Message vide : ce courriel ne contient aucun texte.'
NOTE_ILLISIBLE = 'Format non lisible : le contenu de ce message n’a pas pu être décodé. Le message d’origine est intact ; téléchargez-le pour l’ouvrir.'
NOTE_ECHEC = 'Échec de l’assainissement : la mise en forme de ce message n’a pas pu être affichée sans risque. Le message d’origine est intact ; téléchargez-le pour l’ouvrir.'
_BALISE = re.compile(r'<[^>]*>')
_NEUTRALISE = re.compile(r'<span class="a89-bloque">.*?</span>')


def _texte_source(html):
    """Texte grossier du HTML d'origine, hors éléments masqués : sert seulement à reconnaître un aperçu vidé à tort, jamais à l'afficher."""
    from html import unescape
    text = str(html or '')
    for tag in MASQUES:
        text = re.sub(r'<%s\b.*?</%s\s*>' % (tag, tag), ' ', text, flags=re.I | re.S)
    return unescape(_BALISE.sub(' ', text)).strip()


def corps_courriel(corps_html, corps_texte, html_illisible=False):
    """5.6.93 (N04) : corps affichable d'un courriel, recalculé à chaque ouverture ; le message d'origine n'est jamais réécrit.

    {'html', 'note', 'etat'} avec etat : 'html' (aperçu mis en forme), 'texte' (corps texte), 'vide' (message réellement vide), 'illisible' (format non décodable), 'echec' (assainissement
    en échec). Un aperçu HTML vide alors qu'un corps texte existe se replie sur ce texte, avec la mention « aperçu texte » ; jamais le HTML brut à la place."""
    from .apercu5679 import text_html
    texte = str(corps_texte or '')
    secours = text_html(texte) if texte.strip() else ''
    if html_illisible:
        if secours:
            return {'html': secours, 'note': 'Aperçu texte : la version mise en forme de ce message n’est pas lisible (encodage inconnu).', 'etat': 'illisible'}
        return {'html': '', 'note': NOTE_ILLISIBLE, 'etat': 'illisible'}
    if str(corps_html or '').strip():
        try:
            safe, images, liens = assainir(corps_html)
        except Exception:   # noqa: BLE001 — parseur en échec, quelle qu'en soit la cause
            if secours:
                return {'html': secours, 'note': 'Aperçu texte : la mise en forme de ce message n’a pas pu être nettoyée sans risque.', 'etat': 'echec'}
            return {'html': '', 'note': NOTE_ECHEC, 'etat': 'echec'}
        note = (('%d image(s) distante(s) non chargée(s). ' % images if images else '') + ('%d lien(s) affiché(s) sans être actifs.' % liens if liens else '')).strip()
        if _BALISE.sub('', _NEUTRALISE.sub('', safe)).strip():
            return {'html': safe, 'note': note, 'etat': 'html'}
        if secours:
            return {'html': secours, 'note': ('Aperçu texte : la version mise en forme ne contient aucun texte affichable. ' + note).strip(), 'etat': 'texte'}
        if images or liens:
            return {'html': safe, 'note': note, 'etat': 'html'}
        if _texte_source(corps_html):
            return {'html': '', 'note': NOTE_ECHEC, 'etat': 'echec'}
        return {'html': '<p class="a79-empty">%s</p>' % e(NOTE_VIDE), 'note': NOTE_VIDE, 'etat': 'vide'}
    if secours:
        return {'html': secours, 'note': '', 'etat': 'texte'}
    return {'html': '<p class="a79-empty">%s</p>' % e(NOTE_VIDE), 'note': NOTE_VIDE, 'etat': 'vide'}


def _courriel_html(sujet, emetteur, destinataires, copie, corps_html, corps_texte, pieces, html_illisible=False):
    head = '<div class="a89-tete"><p><strong>%s</strong></p><p>De : %s</p><p>À : %s</p>%s%s</div>' % (
        e(sujet or '(sans objet)'), e(emetteur or '—'), e(destinataires or '—'), ('<p>Copie : %s</p>' % e(copie)) if copie else '',
        ('<p>Pièces jointes : %s</p>' % e(', '.join(pieces))) if pieces else '')
    corps = corps_courriel(corps_html, corps_texte, html_illisible)
    return head + corps['html'], corps['note']


def _contenu(part):
    """(texte, lisible) : une partie au jeu de caractères inconnu ou abîmée est signalée, pas confondue avec un message vide."""
    if part is None:
        return '', True
    try:
        return str(part.get_content() or ''), True
    except (LookupError, UnicodeError, ValueError, KeyError, AssertionError):
        return '', False


def _eml(raw):
    msg = message_from_bytes(raw, policy=policy.default)
    corps_html, html_ok = _contenu(msg.get_body(preferencelist=('html',)))
    corps_texte, texte_ok = _contenu(msg.get_body(preferencelist=('plain',)))
    return _courriel_html(str(msg.get('Subject', '')), _adresses(msg.get('From')), _adresses(msg.get('To')), _adresses(msg.get('Cc')), corps_html, corps_texte,
                          [str(p.get_filename() or 'pièce') for p in msg.iter_attachments()], html_illisible=not html_ok or (not texte_ok and not corps_html))


def _brouillon(desk, row):
    from . import drafts440
    validity, _, uid = str(row.get('deposit_ref') or '').partition(':')
    if not uid:
        raise Stop('brouillon_absent')
    d = drafts440.get_draft(desk.c['mail'], uid, validity)
    return _courriel_html(d.get('subject', ''), _adresses(d.get('from')), _adresses(d.get('to')), _adresses(d.get('cc')), '', d.get('body', ''), [a['filename'] for a in d.get('attachments') or []])


def _texte_prepare(desk, row):
    """Texte préparé mais pas encore enregistré : visible, avec cet état. Aucun fichier n'existe, et rien ne le laisse croire."""
    from .apercu5679 import text_html
    prov = json.loads(row.get('provenance') or '{}')
    if prov.get('producteur') == 'reponses5671':
        data = desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?', (row['source_object_id'],)).fetchone()
        return text_html(str((json.loads(data[0]).get('result') or {}).get('body') or '')) if data else ''
    if prov.get('projet'):
        try:
            if prov.get('apercu') == 'document_projects':
                from .document_projects import preview
                public = preview(desk, prov['projet'])
            else:
                from .word_legal import preview_word_project
                public = preview_word_project(desk, prov['projet'])
        except Stop:
            return ''
        text = public.get('preview') or public.get('text') or public.get('summary') or ''
        return text_html(json.dumps(text, ensure_ascii=False, indent=1) if not isinstance(text, str) else text)
    return ''


def apercu(desk, row, prefix='', client=None, version_demandee=''):
    """Aperçu d'une version. Renvoie {'preview_kind', 'html' ou 'url', 'note', 'row'}."""
    from . import apercu5679
    kind = nature_apercu(row)
    out = {'preview_kind': kind, 'html': '', 'url': '', 'note': '', 'row': row}
    base = prefix + '/api440/m5689/livrables/' + row['document_id']
    if not row.get('path'):
        if row.get('deposit_ref'):
            out['html'], out['note'] = _brouillon(desk, row)
            return out
        out['html'] = _texte_prepare(desk, row)
        out['note'] = 'Texte préparé, pas encore enregistré : aucun fichier à télécharger pour l’instant.' if out['html'] else 'Pas encore de texte à afficher : la préparation est en cours.'
        return out
    if row['production_state'] in ('preparing', 'failed'):
        out['preview_kind'] = 'unsupported'
        out['note'] = 'Fichier pas encore vérifié : aperçu indisponible (%s).' % liv.PRODUCTION[row['production_state']].lower()
        return out
    if kind == 'unsupported':
        out['note'] = 'Aperçu impossible pour ce format (%s) : les informations du document restent affichées, et le fichier peut être téléchargé si vous en avez le droit.' % (PurePosixPath(row['path']).suffix.lower() or 'sans extension')
        return out
    row, raw = octets(desk, row, client, version_demandee)
    out['row'] = row
    version = '?version=' + e(row['version_id'], quote=True)
    if kind in ('pdf', 'image'):
        out['url'] = base + '/telecharger' + version + '&apercu=1'
        return out
    ext = PurePosixPath(row['path']).suffix.lower()
    if ext == '.docx':
        out['html'] = apercu5679.docx_html(raw)
        out['note'] = 'Aperçu du texte et de la structure : la mise en page exacte s’obtient dans l’éditeur.'
    elif ext in ('.odt',):
        from .documents import extract
        out['html'] = apercu5679.text_html(extract(raw, PurePosixPath(row['path']).name, {'max_document_chars': 400000}))
        out['note'] = 'Texte extrait du document : mise en forme non reproduite.'
    elif ext == '.xlsx':
        from .export_tableur5693 import noms_feuilles
        out['feuilles'] = noms_feuilles(raw)       # 5.6.93 (N09) : le choix de la feuille à exporter, lu sur la version affichée
        try:
            out['html'] = apercu5679.xlsx_html(raw)
            out['note'] = 'Lecture seule : valeurs enregistrées dans le classeur (200 premières lignes et 40 premières colonnes par feuille). L’export CSV reprend toute la feuille.'
        except Stop as ex:
            if str(ex) != 'xml_refuse' or not out['feuilles']:
                raise
            out['note'] = 'Classeur trop volumineux pour l’aperçu : l’export CSV complet reste possible, feuille par feuille.'
    elif ext == '.csv':
        out['html'] = _table_csv(raw)
        out['note'] = 'Lecture seule : 200 premières lignes.'
    elif ext == '.ods':
        from .documents import extract
        out['html'] = apercu5679.text_html(extract(raw, PurePosixPath(row['path']).name, {'max_document_chars': 400000}))
        out['note'] = 'Texte extrait du classeur : présentation en tableau non reproduite.'
    elif ext == '.eml':
        out['html'], out['note'] = _eml(raw)
    else:
        try:
            out['html'] = apercu5679.text_html(raw.decode('utf-8-sig'))
        except UnicodeDecodeError:
            out['preview_kind'] = 'unsupported'
            out['note'] = 'Le texte de ce fichier n’est pas lisible : téléchargez-le pour l’ouvrir.'
    return out


def cadre(html):
    """Document isolé du cadre d'aperçu : CSS interne, aucun script, aucun chargement extérieur possible."""
    return {'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'frame_self': True, 'csp': FRAME_CSP,
            'headers': [('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'private, no-store')],
            'body': '<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Aperçu</title><style>%s</style></head><body>%s</body></html>' % (FRAME_CSS, html)}


# ------------------------------------------------------------------------------------------------ capacités et contrat
def capacites(desk):
    """Ce qui est réellement disponible sur cette installation, lu à chaque ouverture."""
    from . import office440
    from .portable import which
    try:
        editor = bool(office440.settings(desk)['enabled'])
    except Exception:   # noqa: BLE001 — éditeur non configuré
        editor = False
    return {'editeur': editor, 'convertisseur': bool(which('libreoffice'))}


def formats(row, caps):
    """Formats qualifiés pour cette nature : un PDF ordinaire n'est pas une facture électronique, le format de facturation reste dans son parcours."""
    path = row.get('path') or ''
    if not path or row['production_state'] not in ('verified', 'published'):
        return []
    ext = PurePosixPath(path).suffix.lower().lstrip('.')
    out = [ext] if ext else []
    if row['source_service'] == 'facturation5653':
        return out
    if ext == 'docx' and caps.get('convertisseur'):
        out.append('pdf')
    if ext == 'xlsx':           # 5.6.93 (N09) : la portée fait partie du format ; l'ancien « csv » (première feuille, lecteur borné) n'est plus proposé
        from .export_tableur5693 import Formats
        return Formats(out + ['feuille1.csv', 'feuilles.zip'])
    return out


def destinations(desk, row):
    """Destinations autorisées, résolues par le serveur : références opaques, jamais un chemin reçu du navigateur."""
    from .common import clean_path, load_matters, under
    matter = next((m for m in load_matters(desk.c) if str(m['id']) == row['matter_id']), None)
    if not matter or not row.get('path') or not under(row['path'], matter['path']):
        return {}
    folder = str(PurePosixPath(row['path']).parent)
    return {'dest-dossier': {'label': 'À côté du document', 'folder': folder, 'boundary': clean_path(matter['path'])},
            'dest-exports': {'label': 'Dossier EXPORTS du dossier', 'folder': clean_path(matter['path'] + '/EXPORTS'), 'boundary': clean_path(matter['path'])}}


def vue(desk, row, owner, role, caps=None):
    """Contrat DeliverableView : calculé à chaque appel à partir de l'objet, des droits et des capacités, jamais fourni par un client."""
    from .membres5650 import effective_rights
    caps = capacites(desk) if caps is None else caps
    rights = effective_rights(desk.c['state_dir'], owner, role)
    kind = nature_apercu(row)
    path = row.get('path') or ''
    ext = PurePosixPath(path).suffix.lower()
    current = row['lifecycle'] == 'current'
    fichier = bool(path) and row['production_state'] in ('verified', 'published')
    reasons = []
    protege = {'recu': 'reçu', 'signe': 'signé', 'emis': 'émis'}.get(row['protected'])
    allowed = formats(row, caps)
    ai = fichier and current and ext in EDITABLE_AI and not protege and role in ('administrateur', 'avocat', 'assistant') and not row['required_right']
    if protege:
        reasons.append('Document %s : l’original n’est jamais modifié. Créez une copie de travail pour proposer des changements.' % protege)
    elif not current:
        reasons.append('Cette version a été remplacée : la modification porte sur la dernière version.')
    elif path and ext not in EDITABLE_AI:
        reasons.append('La modification assistée ne porte que sur les documents Word : pour ce format, créez une copie de travail ou un nouveau projet.')
    elif row['required_right']:
        reasons.append('Les documents de facturation se corrigent dans leur parcours dédié.')
    elif not fichier:
        reasons.append('Fichier pas encore vérifié : la modification sera possible ensuite.')
    from .office440 import can_open
    editor = fichier and current and bool(path) and can_open(path) and not protege
    if fichier and current and not protege and not caps.get('editeur'):
        editor = False
        reasons.append('Éditeur EuroOffice non relié : téléchargez le fichier pour le modifier, ou passez par « Modifier avec l’IA ».')
    download = fichier and bool(rights.get('exporter'))
    if fichier and not rights.get('exporter'):
        reasons.append('Votre compte n’a pas le droit d’exporter ou de télécharger.')
    if not fichier:
        reasons.append('Aucun fichier vérifié pour l’instant : rien à télécharger ni à exporter.')
    dest = destinations(desk, row) if fichier else {}
    export = download and bool(allowed) and bool(dest)
    if download and not dest:
        reasons.append('Export impossible : ce document n’est rattaché à aucun dossier ; le téléchargement reste possible.')
    if path and ext == '.docx' and fichier and not caps.get('convertisseur'):
        reasons.append('Conversion PDF indisponible (LibreOffice absent) : le document reste exportable au format Word.')
    if kind == 'unsupported' and path:
        reasons.append('Aperçu non pris en charge pour ce format.')
    state = liv.etat_contrat(row)
    return {'deliverable_id': row['document_id'], 'matter_id': row['matter_id'], 'version_id': row['version_id'], 'storage_ref': 'ref-' + row['id'], 'title': row['title'], 'kind': row['kind'], 'mime_type': row['mime_type'], 'preview_kind': kind,
            'state': 'archived' if state == 'replaced' else state, 'actions': {'preview': kind != 'unsupported', 'ai_edit': bool(ai), 'editor': bool(editor), 'download': bool(download), 'export': bool(export)}, 'allowed_formats': allowed if download else [],
            'authorized_destination_refs': sorted(dest), 'capability_revision': int(caps.get('editeur', False)) + 2 * int(caps.get('convertisseur', False)) + 4 * int(bool(rights.get('exporter'))), 'unavailable_reasons': list(dict.fromkeys(reasons)),
            'destinations': {k: v['label'] for k, v in dest.items()}}

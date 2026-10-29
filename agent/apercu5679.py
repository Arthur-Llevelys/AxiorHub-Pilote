"""5.6.79 : aperçu d'un document du dossier dans le volet de droite de la page Dossier.

- **PDF** : le fichier lui-même, affiché par le lecteur du navigateur dans un cadre de la même origine (``/dossier/fichier``) ;
- **Word (.docx)** : le texte mis en forme côté serveur (titres, gras, italique, listes, tableaux), entièrement échappé ; le texte
  masqué de Word (5.6.59) n'est pas affiché ;
- **images** : le fichier lui-même ;
- **autres formats lisibles** (.odt, .txt, .md, .eml, .rtf…) : le texte déjà extrait par l'indexation.

Le fichier n'est servi que s'il appartient au dossier demandé (dossier réservé : refus par le registre filtré). Les réponses
« fichier » portent un type fixe tiré de l'extension, ``nosniff`` et ``Content-Disposition`` ; un PDF dont le contenu ne commence pas
par ``%PDF-`` est refusé. Rien n'est écrit ni conservé.
"""
from html import escape as e
from io import BytesIO
from pathlib import PurePosixPath
import re
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET
import zipfile

from .common import Stop

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
IMAGES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp'}
TEXTS = {'.odt', '.txt', '.md', '.eml', '.rtf', '.csv', '.html', '.htm', '.xlsx', '.pptx', '.doc'}
MAX_PARAGRAPHS = 4000
MAX_TEXT = 400_000


def _matter_file(desk, mid, path):
    """(dossier, chemin) si le fichier appartient au dossier ; Stop sinon."""
    from .common import clean_path, under
    from .dossier5627 import _matter
    m = _matter(desk, mid)
    from .membres5650 import viewer_hidden
    if m['id'] in (viewer_hidden() or frozenset()):
        raise Stop('dossier_reserve')
    path = clean_path(str(path or ''))
    if not path or not any(under(path, r) for r in [m['path']] + list(m.get('paths') or [])):
        raise Stop('fichier_hors_dossier')
    return m, path


def kind_of(path):
    ext = PurePosixPath(path).suffix.lower()
    if ext == '.pdf':
        return 'pdf'
    if ext in IMAGES:
        return 'image'
    if ext == '.docx':
        return 'docx'
    if ext in TEXTS:
        return 'texte'
    return 'autre'


def _download(desk, path):
    from .document_projects import _dav
    client = _dav(desk)
    st = client.stat(path)
    return client.download({'path': path, 'etag': st['etag'], 'size': st.get('size', 0)})


def _on(node, tag):
    """Propriété booléenne Word (gras, italique, masqué) active : présente et sans w:val à 0/false."""
    el = node.find(W + tag)
    return el is not None and str(el.get(W + 'val', 'true')).lower() not in ('0', 'false', 'off')


def _runs(p):
    out = []
    for r in p.iter(W + 'r'):
        rpr = r.find(W + 'rPr')
        if rpr is not None and _on(rpr, 'vanish'):
            continue                                                      # texte masqué de Word : jamais affiché
        text = ''
        for node in r:
            tag = node.tag[len(W):] if node.tag.startswith(W) else ''
            if tag == 't':
                text += node.text or ''
            elif tag == 'tab':
                text += '    '
            elif tag in ('br', 'cr'):
                text += '\n'
        if not text:
            continue
        html = e(text).replace('\n', '<br>')
        if rpr is not None and _on(rpr, 'b'):
            html = '<strong>%s</strong>' % html
        if rpr is not None and _on(rpr, 'i'):
            html = '<em>%s</em>' % html
        if rpr is not None and _on(rpr, 'u'):
            html = '<u>%s</u>' % html
        out.append(html)
    return ''.join(out)


def _paragraph(p):
    ppr = p.find(W + 'pPr')
    style = ''
    numbered = False
    align = ''
    if ppr is not None:
        st = ppr.find(W + 'pStyle')
        style = (st.get(W + 'val', '') if st is not None else '').lower()
        numbered = ppr.find(W + 'numPr') is not None
        jc = ppr.find(W + 'jc')
        align = (jc.get(W + 'val', '') if jc is not None else '').lower()
    body = _runs(p)
    if not body.strip():
        return ''
    level = re.search(r'(?:heading|titre)\s*(\d)', style)
    if level or style in ('title', 'titre'):
        n = min(6, 3 + int(level.group(1)) - 1) if level else 3
        return '<h%d>%s</h%d>' % (n, body, n)
    cls = ' class="a79-center"' if align in ('center', 'centre') else (' class="a79-right"' if align in ('right', 'end') else '')
    return ('<p class="a79-li">• %s</p>' % body) if numbered else '<p%s>%s</p>' % (cls, body)


def docx_html(raw):
    """Aperçu HTML échappé d'un .docx (texte, titres, gras, italique, listes, tableaux) ; Stop si le fichier n'est pas un Word lisible."""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > 40_000_000:
                raise Stop('document_trop_volumineux')
            root = ET.fromstring(z.read(info))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise Stop('document_office_non_lisible') from None
    body = root.find(W + 'body')
    if body is None:
        raise Stop('document_office_non_lisible')
    out, count = [], 0
    for child in body:
        if count >= MAX_PARAGRAPHS:
            out.append('<p class="a79-more">… (aperçu limité ; ouvrez le document pour la suite)</p>')
            break
        if child.tag == W + 'p':
            html = _paragraph(child)
            if html:
                out.append(html)
                count += 1
        elif child.tag == W + 'tbl':
            rows = []
            for tr in child.iter(W + 'tr'):
                cells = []
                for tc in tr.findall(W + 'tc'):
                    cells.append('<td>%s</td>' % ''.join(x for x in (_paragraph(p) for p in tc.iter(W + 'p')) if x))
                rows.append('<tr>%s</tr>' % ''.join(cells))
                count += 1
            out.append('<table class="a79-table">%s</table>' % ''.join(rows))
    return ''.join(out) or '<p class="a79-empty">Document sans texte.</p>'


def text_html(text):
    text = str(text or '')[:MAX_TEXT]
    text = re.sub(r'\[Page (\d+)\]', '\n\n— page \\1 —\n', text)
    paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    return ''.join('<p>%s</p>' % e(p).replace('\n', '<br>') for p in paras[:MAX_PARAGRAPHS]) or '<p class="a79-empty">Aucun texte lisible.</p>'


def _indexed_text(desk, mid, path):
    from .index import DocumentIndex
    index = DocumentIndex(desk.c['state_dir'])
    try:
        row = index.db.execute('SELECT text,error FROM docs WHERE matter=? AND path=?', (mid, path)).fetchone()
    finally:
        try:
            index.db.close()
        except Exception:   # noqa: BLE001
            pass
    return (str(row[0] or ''), str(row[1] or '')) if row else ('', '')


def preview(desk, prefix, mid, path):
    """Description de l'aperçu pour le volet : {'kind', 'title', 'html' | 'url', 'open_url', 'download_url', 'note'}."""
    m, path = _matter_file(desk, mid, path)
    name = PurePosixPath(path).name
    q = urlencode({'matter': m['id'], 'path': path})
    out = {'title': name, 'path': path, 'download_url': prefix + '/dossier/fichier?' + q + '&dl=1', 'open_url': '', 'note': ''}
    try:
        from . import office440
        if office440.settings(desk)['enabled'] and office440.can_open(path):
            from .fiche460 import doc_href
            out['open_url'] = prefix + doc_href(path, m['id'])
    except Exception:   # noqa: BLE001 — éditeur non configuré : pas de bouton « Modifier »
        pass
    kind = kind_of(path)
    if kind in ('pdf', 'image'):
        return {**out, 'kind': kind, 'url': prefix + '/dossier/fichier?' + q}
    if kind == 'docx':
        try:
            return {**out, 'kind': 'html', 'html': docx_html(_download(desk, path))}
        except Stop as ex:
            text, _ = _indexed_text(desk, m['id'], path)
            if not text:
                raise
            return {**out, 'kind': 'html', 'html': text_html(text), 'note': 'Mise en forme indisponible (%s) : texte indexé.' % ex}
    text, error = _indexed_text(desk, m['id'], path)
    if text:
        return {**out, 'kind': 'html', 'html': text_html(text), 'note': 'Texte extrait du document (mise en forme non reproduite).'}
    return {**out, 'kind': 'aucun', 'note': 'Aperçu indisponible pour ce format%s. Téléchargez le fichier pour l’ouvrir.' % (
        ' (lecture : %s)' % error[:120] if error else '')}


def file_response(desk, mid, path, download=False):
    """Réponse brute (PDF, image ; tout format en téléchargement) ; affichable dans un cadre de la même origine seulement."""
    m, path = _matter_file(desk, mid, path)
    kind = kind_of(path)
    if not download and kind not in ('pdf', 'image'):
        raise Stop('apercu_format_non_pris_en_charge')
    raw = _download(desk, path)
    name = PurePosixPath(path).name
    if kind == 'pdf':
        if not raw.startswith(b'%PDF-'):
            raise Stop('document_pdf_invalide')
        ctype = 'application/pdf'
    elif kind == 'image':
        ctype = IMAGES[PurePosixPath(path).suffix.lower()]
    else:
        ctype = 'application/octet-stream'
    disposition = ('attachment' if download else 'inline') + "; filename*=UTF-8''" + quote(name, safe='')
    return {'status': '200 OK', 'kind': ctype, 'body': raw, 'frame_self': True,
            'headers': [('Content-Disposition', disposition), ('X-Content-Type-Options', 'nosniff'), ('Cache-Control', 'private, no-store')],
            # le lecteur PDF du navigateur doit pouvoir s'afficher ; aucune ressource externe, cadre limité à AxiorHub
            'csp': "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; object-src 'self'; "
                   "frame-ancestors 'self'; base-uri 'none'; form-action 'none'" if kind == 'pdf' else
                   "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"}

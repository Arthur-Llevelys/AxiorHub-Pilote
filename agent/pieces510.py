"""Pièces et bordereaux (AxiorHub 5.1.0).

Du dossier Nextcloud au bordereau de communication de pièces :
  1. analyse des PDF du dossier (pages, date, intitulé proposé, doublons, fichiers protégés ou illisibles) et projet de bordereau ;
  2. tableau corrigé par l'avocat (ordre, intitulés, dates, pièces retenues) ;
  3. contrôles croisés avec les conclusions (pièce citée absente, pièce jamais citée, doublon, poids) ;
  4. modifications par instruction (« retire la pièce 7, ajoute le constat en 9 ») : plan de renumérotation et renvois à corriger,
     appliqués au projet seulement après relecture ;
  5. création, après un code de confirmation, de NOUVEAUX fichiers : bordereau DOCX (modèle du cabinet) et PDF, pièces numérotées et
     tamponnées (une par fichier, ou un seul PDF avec signets). Les originaux ne sont jamais modifiés ni remplacés.

Traitement local : aucun modèle de langage, aucun service externe ; la lecture PDF utilise pypdf (copie intégrée, licence BSD).
"""
from .common import matter_display
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import io
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .common import Stop, clean_path, digest, fold, load_matters, under

OUTPUT_FOLDER = 'Pièces communiquées'
MAX_ROWS = 300
MONTHS = ('janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre')
MONTHS_FOLD = {fold(m): i + 1 for i, m in enumerate(MONTHS)}

PROFILE_FIELDS = (('prenom_avocat', 'Prénom de l’avocat'), ('nom_avocat', 'Nom de l’avocat'), ('ville_avocat', 'Barreau de'),
                  ('numero_toque_avocat', 'Toque n°'), ('adresse_cabinet_avocat', 'Adresse du cabinet'),
                  ('numero_telephone_avocat', 'Téléphone'), ('email_avocat', 'Courriel'))
CASE_FIELDS = (('Dossier', 'Dossier (intitulé de l’affaire)'), ('juridiction', 'Juridiction'), ('Numero_de_procedure', 'N° de procédure (RG)'),
               ('date_audience', 'Date d’audience'), ('client', 'Client'), ('partie_adverse', 'Partie adverse'),
               ('avocat_partie_adverse', 'Avocat de la partie adverse'), ('numero_bordereau', 'N° du bordereau'),
               ('date_du_bordereau', 'Date du bordereau'), ('premiere_piece', 'Numéro de la première pièce'))
DEFAULT_OPTIONS = {'stamp': 'first', 'number_each_page': True, 'continuous': False, 'single_pdf': False, 'bordereau_first': True,
                   'position': 'haut_droite', 'date_in_title': True, 'max_file_mb': 50, 'max_total_mb': 500,
                   'ocr': True, 'fix_conclusions': True, 'stamp_image': False, 'deposit': False, 'deposit_mb': 10,
                   'continue_numbering': True}
STAMP_MODES = ('first', 'all', 'none')
POSITIONS = ('haut_droite', 'haut_gauche', 'bas_droite', 'bas_gauche')
ORDERS = ('nom', 'chronologique', 'conclusions')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS pieces510_drafts(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL,
  updated TEXT NOT NULL, job_id INTEGER, result TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS pieces510_drafts_matter ON pieces510_drafts(matter, updated);
'''


def ensure_schema(desk):
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pieces510_drafts'").fetchone():
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _pdf_lib():
    from ._vendor import pypdf
    return pypdf


def now():
    return datetime.now(timezone.utc).isoformat()


def fr_date(value):
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value or '')
    return '%d%s %s %d' % (d.day, 'er' if d.day == 1 else '', MONTHS[d.month - 1], d.year)


def _text(value, maximum=300):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    if any(ord(c) < 32 for c in value):
        raise Stop('texte_invalide')
    return value[:maximum]


# ------------------------------------------------------------------------------------------------- réglages
def profile(desk):
    stored = desk.settings('pieces510:avocat', {}) or {}
    return {k: str(stored.get(k, '')) for k, _ in PROFILE_FIELDS}


def save_profile(desk, data):
    out = {}
    for key, _ in PROFILE_FIELDS:
        out[key] = _text(data.get(key, ''), 200 if key == 'adresse_cabinet_avocat' else 120)
    if out['email_avocat'] and not re.fullmatch(r'[^\s@,;<>"]+@[^\s@,;<>"]+\.[^\s@,;<>"]+', out['email_avocat']):
        raise Stop('adresse_invalide')
    desk.setting('pieces510:avocat', out)
    desk.audit('pieces_510_profil', {'fields': sorted(k for k, v in out.items() if v)})
    return out


def options(desk, override=None):
    out = dict(DEFAULT_OPTIONS)
    out.update({k: v for k, v in (desk.settings('pieces510:options', {}) or {}).items() if k in DEFAULT_OPTIONS})
    if override:
        out.update({k: v for k, v in override.items() if k in DEFAULT_OPTIONS})
    return clean_options(out)


def clean_options(raw):
    def flag(v):
        return v in (True, 'true', '1', 1, 'on')
    out = dict(DEFAULT_OPTIONS)
    out['stamp'] = raw.get('stamp') if raw.get('stamp') in STAMP_MODES else 'first'
    out['position'] = raw.get('position') if raw.get('position') in POSITIONS else 'haut_droite'
    for key in ('number_each_page', 'continuous', 'single_pdf', 'bordereau_first', 'date_in_title', 'ocr', 'fix_conclusions',
                'stamp_image', 'deposit', 'continue_numbering'):
        out[key] = flag(raw.get(key, DEFAULT_OPTIONS[key]))
    for key, low, high in (('max_file_mb', 1, 500), ('max_total_mb', 1, 5000), ('deposit_mb', 1, 1000)):
        try:
            out[key] = max(low, min(int(raw.get(key, DEFAULT_OPTIONS[key])), high))
        except (TypeError, ValueError):
            raise Stop('limite_taille_invalide') from None
    return out


def save_options(desk, data):
    out = clean_options({**options(desk), **data})
    desk.setting('pieces510:options', out)
    return out


# ------------------------------------------------------------------------------------------------- tampon
# Largeurs Helvetica (1/1000 em) pour les caractères 32 à 126 ; les autres prennent la largeur de leur lettre de base.
_HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278] + [556] * 10 + [
    278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667,
    611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,
    556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def text_width(text, size, bold=False):
    total = 0
    for ch in str(text):
        base = fold(ch)[:1] or ch
        if ch.isupper():
            base = base.upper()
        code = ord(base) if base else 63
        total += _HELV[code - 32] if 32 <= code <= 126 else 556
    return total * size / 1000.0 * (1.06 if bold else 1.0)


def _wrap(text, size, width, maximum=2):
    words, lines, cur = str(text).split(), [], ''
    for w in words:
        trial = (cur + ' ' + w).strip()
        if cur and text_width(trial, size) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines[:maximum]


def stamp_lines(prof, number=None):
    """[(gras, taille, texte)] du tampon de l'avocat, éventuellement avec le numéro de pièce."""
    name = ' '.join(x for x in (prof.get('prenom_avocat', '').strip(), prof.get('nom_avocat', '').strip().upper()) if x)
    lines = []
    if name:
        lines.append((True, 9.0, 'Maître ' + name))
    if prof.get('ville_avocat'):
        lines.append((False, 7.5, 'Avocat au Barreau de ' + prof['ville_avocat'].strip()))
    if prof.get('numero_toque_avocat'):
        lines.append((False, 7.5, 'Toque n° ' + prof['numero_toque_avocat'].strip()))
    for line in _wrap(prof.get('adresse_cabinet_avocat', ''), 6.5, 160):
        lines.append((False, 6.5, line))
    contact = ' – '.join(x for x in (prof.get('numero_telephone_avocat', '').strip(), prof.get('email_avocat', '').strip()) if x)
    if contact:
        lines.append((False, 6.5, contact))
    if number is not None:
        lines.append((True, 12.0, 'PIÈCE N° %d' % int(number)))
    return lines


def stamp_box(lines, pad=6.0):
    width = max([text_width(t, s, b) for b, s, t in lines] + [90.0]) + 2 * pad
    height = sum(s * 1.25 for _, s, _ in lines) + 2 * pad + (4 if any(t.startswith('PIÈCE') for _, _, t in lines) else 0)
    return width, height


def stamp_svg(prof, number=12):
    """Image vectorielle du tampon (aperçu et téléchargement). Tout le texte est échappé."""
    lines = stamp_lines(prof, number)
    if not lines:
        lines = [(False, 8.0, 'Renseignez le profil de l’avocat')]
    w, h = stamp_box(lines)
    scale = 2.0
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %.1f %.1f" role="img" aria-label="Tampon de l’avocat">'
           % (w * scale, h * scale, w, h),
           '<rect x="0.8" y="0.8" width="%.1f" height="%.1f" fill="#fff" stroke="#1e3a8c" stroke-width="1.2" rx="2"/>' % (w - 1.6, h - 1.6),
           '<rect x="3.2" y="3.2" width="%.1f" height="%.1f" fill="none" stroke="#1e3a8c" stroke-width="0.5" rx="1.5"/>' % (w - 6.4, h - 6.4)]
    y = 6.0
    for bold, size, text in lines:
        if text.startswith('PIÈCE'):
            out.append('<line x1="10" x2="%.1f" y1="%.1f" y2="%.1f" stroke="#1e3a8c" stroke-width="0.5"/>' % (w - 10, y + 1.5, y + 1.5))
            y += 4
        y += size * 1.25
        out.append('<text x="%.1f" y="%.1f" font-family="Helvetica, Arial, sans-serif" font-size="%.1f"%s fill="#1e3a8c" text-anchor="middle">%s</text>'
                   % (w / 2, y - size * 0.25, size, ' font-weight="bold"' if bold else '', html.escape(text)))
    out.append('</svg>')
    return ''.join(out)


def _pdf_string(text):
    raw = str(text).encode('cp1252', 'replace')
    return '(' + raw.decode('latin1').replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ')'


def _stamp_ops(lines, x, y_top):
    w, h = stamp_box(lines)
    y = y_top - h
    ops = ['q', '0.12 0.23 0.55 RG', '0.12 0.23 0.55 rg', '1.2 w', '%.2f %.2f %.2f %.2f re S' % (x, y, w, h), '0.5 w',
           '%.2f %.2f %.2f %.2f re S' % (x + 2.4, y + 2.4, w - 4.8, h - 4.8)]
    cursor = y_top - 6.0
    for bold, size, text in lines:
        if text.startswith('PIÈCE'):
            ops.append('%.2f %.2f m %.2f %.2f l S' % (x + 10, cursor - 1.5, x + w - 10, cursor - 1.5))
            cursor -= 4
        cursor -= size * 1.25
        tw = text_width(text, size, bold)
        ops.append('BT /%s %.1f Tf %.2f %.2f Td %s Tj ET' % ('AXB' if bold else 'AXR', size, x + (w - tw) / 2, cursor + size * 0.25, _pdf_string(text)))
    ops.append('Q')
    return ops, w, h


def _label_ops(text, x, y, size=8.0):
    return ['q', '0.12 0.23 0.55 rg', 'BT /AXB %.1f Tf %.2f %.2f Td %s Tj ET' % (size, x, y, _pdf_string(text)), 'Q']


def _overlay_page(ops, image=None):
    """Page PDF minimale portant ``ops`` (coordonnées de la page cible), lue par pypdf pour la superposition.

    ``image`` : octets PNG ou JPEG du tampon importé, disponible sous le nom /AXI.
    """
    stream = '\n'.join(ops).encode('latin1')
    xobj = b''
    extra = []
    if image:
        from .pieces520 import image_object
        body, smask, _, _ = image_object(image)
        extra = [body] + ([smask] if smask else [])
        xobj = b' /XObject << /AXI 7 0 R >>'
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100000 100000] /Resources << /Font << /AXR 5 0 R /AXB 6 0 R >>' + xobj + b' >> /Contents 4 0 R >>',
            b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>'] + extra
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b' 0 obj\n' + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 ' + str(len(objs) + 1).encode() + b'\n0000000000 65535 f \n'
    for off in offsets:
        out += ('%010d 00000 n \n' % off).encode()
    out += b'trailer\n<< /Size ' + str(len(objs) + 1).encode() + b' /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
    return _pdf_lib().PdfReader(io.BytesIO(bytes(out))).pages[0]


IMAGE_WIDTH = 120.0


def image_stamp_box(image, number):
    from .pieces520 import image_object
    _, _, w, h = image_object(image)
    iw = IMAGE_WIDTH
    ih = iw * h / float(w)
    label = 'PIÈCE N° %d' % int(number)
    bw = max(iw, text_width(label, 12, True)) + 12
    return bw, ih + 12 * 1.25 + 16, iw, ih, label


def _image_stamp_ops(image, number, x, y_top):
    bw, bh, iw, ih, label = image_stamp_box(image, number)
    y = y_top - bh
    ops = ['q', '%.2f 0 0 %.2f %.2f %.2f cm /AXI Do' % (iw, ih, x + (bw - iw) / 2, y_top - 6 - ih), 'Q',
           'q', '0.12 0.23 0.55 rg', 'BT /AXB 12 Tf %.2f %.2f Td %s Tj ET' % (x + (bw - text_width(label, 12, True)) / 2, y + 6, _pdf_string(label)), 'Q']
    return ops


# ------------------------------------------------------------------------------------------------- PDF
def open_pdf(raw):
    pypdf = _pdf_lib()
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(''):
                    raise Stop('piece_pdf_protegee')
            except Stop:
                raise
            except Exception:
                raise Stop('piece_pdf_protegee') from None
        len(reader.pages)
        return reader
    except Stop:
        raise
    except Exception:
        raise Stop('piece_pdf_illisible') from None


def pdf_info(raw):
    try:
        reader = open_pdf(raw)
    except Stop as ex:
        return {'pages': 0, 'readable': False, 'error': str(ex), 'text': ''}
    text = ''
    try:
        text = (reader.pages[0].extract_text() or '')[:4000] if reader.pages else ''
    except Exception:
        text = ''
    return {'pages': len(reader.pages), 'readable': True, 'error': '', 'text': text}


def jpeg_to_pdf(raw):
    """Convertit une photo JPEG en PDF d'une page (A4, image centrée), sans bibliothèque d'image."""
    if not raw.startswith(b'\xff\xd8'):
        raise Stop('image_jpeg_invalide')
    i, width, height, comps = 2, 0, 0, 3
    while i < len(raw) - 9:
        if raw[i] != 0xFF:
            i += 1
            continue
        marker = raw[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            height, width, comps = int.from_bytes(raw[i + 5:i + 7], 'big'), int.from_bytes(raw[i + 7:i + 9], 'big'), raw[i + 9]
            break
        i += 2 + int.from_bytes(raw[i + 2:i + 4], 'big')
    if not width or not height:
        raise Stop('image_jpeg_invalide')
    pw, ph, margin = 595.0, 842.0, 28.0
    scale = min((pw - 2 * margin) / width, (ph - 2 * margin) / height)
    dw, dh = width * scale, height * scale
    content = ('q %.2f 0 0 %.2f %.2f %.2f cm /Im0 Do Q' % (dw, dh, (pw - dw) / 2, (ph - dh) / 2)).encode()
    space = b'/DeviceGray' if comps == 1 else (b'/DeviceCMYK' if comps == 4 else b'/DeviceRGB')
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>',
            b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
            b'<< /Type /XObject /Subtype /Image /Width ' + str(width).encode() + b' /Height ' + str(height).encode() +
            b' /ColorSpace ' + space + b' /BitsPerComponent 8 /Filter /DCTDecode /Length ' + str(len(raw)).encode() + b' >>\nstream\n' + raw + b'\nendstream']
    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(n).encode() + b' 0 obj\n' + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(('%010d 00000 n \n' % o).encode() for o in offsets)
    out += b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
    return bytes(out)


def _anchor(box, position, w, h, margin=14.0):
    x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    left = 'gauche' in position
    top = 'haut' in position
    x = x0 + margin if left else x1 - margin - w
    y_top = y1 - margin if top else y0 + margin + h
    return x, y_top


def stamp_document(raw, number, opts, prof, page_offset=0, grand_total=0, image=None):
    """Nouveau PDF : numéro et tampon apposés ; l'original n'est pas modifié. Renvoie (octets, nombre de pages)."""
    pypdf = _pdf_lib()
    reader = open_pdf(raw)
    writer = pypdf.PdfWriter()
    total = len(reader.pages)
    lines = stamp_lines(prof, number)
    for index, page in enumerate(reader.pages):
        try:
            page.transfer_rotation_to_content()
        except Exception:
            pass
        box = page.cropbox
        ops = []
        full = opts['stamp'] == 'all' or (opts['stamp'] == 'first' and index == 0)
        if full and image:
            sw, sh = image_stamp_box(image, number)[:2]
            x, y_top = _anchor(box, opts['position'], sw, sh)
            ops += _image_stamp_ops(image, number, x, y_top)
        elif full and lines:
            sw, sh = stamp_box(lines)
            x, y_top = _anchor(box, opts['position'], sw, sh)
            ops += _stamp_ops(lines, x, y_top)[0]
        elif opts['number_each_page'] or opts['stamp'] == 'none':
            label = 'Pièce n° %d – p. %d/%d' % (number, index + 1, total)
            lw = text_width(label, 8.0, True)
            x, y_top = _anchor(box, opts['position'], lw, 10)
            ops += _label_ops(label, x, y_top - 8)
        if opts['continuous'] and grand_total:
            label = 'Page %d / %d' % (page_offset + index + 1, grand_total)
            lw = text_width(label, 8.0, True)
            x = float(box.left) + (float(box.width) - lw) / 2
            ops += _label_ops(label, x, float(box.bottom) + 12)
        if ops:
            page.merge_page(_overlay_page(ops, image if (full and image) else None))
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), total


def combine(parts):
    """[(intitulé du signet, octets PDF)] → un seul PDF avec un signet par pièce."""
    pypdf = _pdf_lib()
    writer = pypdf.PdfWriter()
    for title, raw in parts:
        start = len(writer.pages)
        for page in open_pdf(raw).pages:
            writer.add_page(page)
        writer.add_outline_item(title[:200], start)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------- dates et intitulés
def _valid(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat() if 1950 <= int(y) <= 2100 else ''
    except ValueError:
        return ''


def find_date(text):
    t = fold(str(text or ''))
    for m in re.finditer(r'(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?!\d)', t):
        v = _valid(m.group(3), m.group(2), m.group(1))
        if v:
            return v
    for m in re.finditer(r'(?<!\d)(\d{4})[-_.](\d{2})[-_.](\d{2})(?!\d)', t):
        v = _valid(m.group(1), m.group(2), m.group(3))
        if v:
            return v
    for m in re.finditer(r'(?<!\d)(1er|\d{1,2})\s+(' + '|'.join(MONTHS_FOLD) + r')\s+(\d{4})', t):
        v = _valid(m.group(3), MONTHS_FOLD[m.group(2)], 1 if m.group(1) == '1er' else m.group(1))
        if v:
            return v
    for m in re.finditer(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', t):
        v = _valid(m.group(1), m.group(2), m.group(3))
        if v:
            return v
    return ''


_PREFIX = re.compile(r'^\s*(?:pi[eè]ces?\s*)?(?:n\s*[°o]?\.?\s*)?(\d{1,3})(?:\s*[-_.)\]]\s*|\s+)', re.I)


def file_number(name):
    m = _PREFIX.match(PurePosixPath(name).stem)
    return int(m.group(1)) if m else None


def title_from_name(name):
    stem = PurePosixPath(name).stem
    stem = _PREFIX.sub('', stem, count=1)
    stem = re.sub(r'[_]+', ' ', stem)
    stem = re.sub(r'\s+', ' ', stem).strip(' -.')
    return (stem[:1].upper() + stem[1:]) if stem else PurePosixPath(name).stem


def _has_date(text):
    return bool(find_date(text)) or bool(re.search(r'\b(19|20)\d{2}\b', str(text)))


# ------------------------------------------------------------------------------------------------- citations
_CITE = re.compile(r'\bpieces?\s*(?:n\s*[°o]\s*\.?|nos?\.?|numeros?)?\s*(\d{1,3}(?:\s*(?:,|et|a|au|-|–)\s*(?:n\s*[°o]\s*\.?\s*)?\d{1,3})*)')


def citations(text):
    """[(numéro, position, extrait)] des pièces citées : « pièce n° 3 », « pièces 3 à 5 », « pièces n° 1, 2 et 4 »."""
    original = str(text or '')
    folded = fold(original)
    same = len(folded) == len(original)
    out = []
    for m in _CITE.finditer(folded):
        nums, tokens = [], re.findall(r'\d{1,3}|\ba\b|\bau\b|-|–', m.group(1))
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.isdigit():
                if nums and i >= 1 and tokens[i - 1] in ('a', 'au', '-', '–') and nums[-1] < int(tok) <= nums[-1] + 200:
                    nums.extend(range(nums[-1] + 1, int(tok) + 1))
                else:
                    nums.append(int(tok))
            i += 1
        src = original if same else folded
        excerpt = re.sub(r'\s+', ' ', src[max(0, m.start() - 70):m.end() + 50]).strip()
        for n in nums:
            if 0 < n <= 999:
                out.append((n, m.start(), excerpt))
    return out


# ------------------------------------------------------------------------------------------------- brouillon
def _matter(desk, mid):
    for m in load_matters(desk.c):
        if m['id'] == mid:
            return m
    raise Stop('dossier_absent')


def _dav(desk, opts=None):
    from .dav import DAV
    cfg = dict(desk.c.get('nextcloud_documents') or desk.c['nextcloud'])
    opts = opts or DEFAULT_OPTIONS
    cfg['max_file_bytes'] = max(int(cfg.get('max_file_bytes', 15_000_000)), int(opts['max_file_mb']) * 1_000_000 + 1)
    cfg['max_generated_file_bytes'] = max(int(cfg.get('max_generated_file_bytes', 20_000_000)), int(opts['max_total_mb']) * 1_000_000 + 1)
    cfg['max_files'] = max(int(cfg.get('max_files', 500)), 2000)
    return DAV(cfg)


def get(desk, did):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM pieces510_drafts WHERE id=?', (str(did or ''),)).fetchone()
    if not row:
        raise Stop('bordereau_absent')
    out = dict(row)
    out['data'] = json.loads(out['data'])
    out['result'] = json.loads(out['result']) if out['result'] else {}
    return out


def drafts(desk, matter='', limit=20):
    ensure_schema(desk)
    sql, params = 'SELECT id,matter,status,created,updated,job_id FROM pieces510_drafts', []
    if matter:
        sql += ' WHERE matter=?'
        params.append(matter)
    sql += ' ORDER BY updated DESC LIMIT ?'
    params.append(max(1, min(int(limit), 100)))
    return [dict(r) for r in desk.db.execute(sql, params)]


def _save(desk, did, data, status=None):
    if status:
        desk.db.execute('UPDATE pieces510_drafts SET data=?, status=?, updated=? WHERE id=?', (json.dumps(data, ensure_ascii=False), status, now(), did))
    else:
        desk.db.execute('UPDATE pieces510_drafts SET data=?, updated=? WHERE id=?', (json.dumps(data, ensure_ascii=False), now(), did))
    desk.db.commit()


def included(data):
    return [r for r in data['rows'] if r.get('include')]


def first_number(data):
    try:
        return max(1, min(int(str(data.get('case', {}).get('premiere_piece') or '1').strip()), 999))
    except ValueError:
        raise Stop('premiere_piece_invalide') from None


def _default_case(desk, matter):
    case = {k: '' for k, _ in CASE_FIELDS}
    case['Dossier'] = matter_display(matter)
    case['client'] = matter.get('client_name', '')
    try:
        from . import conflicts500
        adverse = [p['name'] for p in conflicts500.matter_parties(desk, matter['id']) if p['role'] == 'adverse']
        case['partie_adverse'] = ', '.join(adverse[:3])
    except Exception:
        pass
    previous = desk.db.execute("SELECT data FROM pieces510_drafts WHERE matter=? ORDER BY updated DESC LIMIT 1", (matter['id'],)).fetchone()
    if previous:
        old = json.loads(previous[0]).get('case', {})
        for key in ('Dossier', 'juridiction', 'Numero_de_procedure', 'date_audience', 'client', 'partie_adverse', 'avocat_partie_adverse'):
            if old.get(key):
                case[key] = old[key]
        try:
            case['numero_bordereau'] = str(int(old.get('numero_bordereau') or 0) + 1)
        except ValueError:
            pass
    case['numero_bordereau'] = case['numero_bordereau'] or '1'
    case['date_du_bordereau'] = date.today().isoformat()
    from . import pieces520
    last = pieces520.last_number(desk, matter['id'])
    case['premiere_piece'] = str(last + 1 if (last and options(desk)['continue_numbering']) else 1)
    return case


def _is_output(path, matter_path):
    rel = fold(path[len(matter_path):])
    return ('/' + fold(OUTPUT_FOLDER) + '/') in rel + '/' or PurePosixPath(path).name.lower().startswith('bordereau')


def scan(desk, args, dav=None):
    """Analyse les PDF du dossier et crée un projet de bordereau (rien n'est écrit dans Nextcloud)."""
    ensure_schema(desk)
    matter = _matter(desk, str(args.get('matter', '')))
    opts = options(desk)
    order = args.get('order') if args.get('order') in ORDERS else 'nom'
    client = dav or _dav(desk, opts)
    root = clean_path(matter['path'])
    folder = str(args.get('source_folder', '') or '').strip().strip('/')
    base = clean_path(root + '/' + folder) if folder else root
    if not under(base, root):
        raise Stop('dossier_hors_racines')
    items = client.inventory(base)
    pdfs, others = [], []
    for item in items:
        path = item['path']
        if item.get('directory') or _is_output(path, root):
            continue
        ext = PurePosixPath(path).suffix.lower()
        if ext in ('.pdf', '.jpg', '.jpeg'):
            pdfs.append(item)
        elif ext in ('.png', '.heic', '.tif', '.tiff', '.doc', '.docx', '.odt', '.msg', '.eml'):
            others.append({'path': path, 'reason': 'Format à convertir en PDF avant de l’ajouter au bordereau.'})
    if len(pdfs) > MAX_ROWS:
        raise Stop('trop_de_pieces')
    # Par défaut, seuls les fichiers d'un sous-dossier « Pièces » sont retenus s'il en existe un ; sinon, tous.
    in_pieces = [p for p in pdfs if 'piece' in fold(str(PurePosixPath(p['path']).parent)[len(root):])]
    rows, seen = [], {}
    from . import pieces520
    communicated = pieces520.known_by_sha(desk, matter['id'])
    for item in pdfs:
        name = PurePosixPath(item['path']).name
        row = {'key': digest(item['path'])[:16], 'path': item['path'], 'name': name, 'size': int(item.get('size', 0) or 0),
               'etag': item.get('etag', ''), 'sha256': '', 'pages': 0, 'readable': False, 'error': '', 'date': '', 'title': '',
               'kind': 'jpeg' if PurePosixPath(name).suffix.lower() in ('.jpg', '.jpeg') else 'pdf',
               'include': (item in in_pieces) if in_pieces else True, 'scan': False}
        try:
            raw = client.download(item)
            row['sha256'] = hashlib.sha256(raw).hexdigest()
            pdf = jpeg_to_pdf(raw) if row['kind'] == 'jpeg' else raw
            info = pdf_info(pdf)
            row.update(pages=info['pages'], readable=info['readable'], error=info['error'])
            text = info['text']
            row['scan'] = info['readable'] and len(text.strip()) < 40
            if row['scan'] and opts['ocr']:
                from . import pieces520
                ocr = pieces520.ocr_text(pdf, 1)
                if ocr:
                    text, row['ocr'] = ocr[:4000], True
        except Stop as ex:
            row.update(error=str(ex), readable=False)
            text = ''
        row['date'] = find_date(name) or find_date(text)
        title = title_from_name(name)
        if opts['date_in_title'] and row['date'] and not _has_date(title):
            title += ' du ' + fr_date(row['date'])
        row['title'] = title[:300]
        row['number_hint'] = file_number(name)
        if row['sha256'] and row['sha256'] in communicated:
            c = communicated[row['sha256']]
            row['communicated'] = {'number': c['number'], 'bordereau': c['bordereau'], 'day': c['day']}
            row['include'] = False
        if row['sha256'] and row['sha256'] in seen:
            row['duplicate_of'] = seen[row['sha256']]
            row['include'] = False
        elif row['sha256']:
            seen[row['sha256']] = row['key']
        rows.append(row)
    data = {'matter': matter['id'], 'case': _default_case(desk, matter), 'options': opts, 'order': order,
            'rows': rows, 'others': others, 'source_folder': folder, 'conclusions_path': '', 'checks': {}, 'plan': {},
            'scan': {'at': now(), 'folder': base, 'pdf': len(pdfs), 'others': len(others)}}
    data['rows'] = sort_rows(data['rows'], order, '')
    did = digest('pieces510|%s|%s' % (matter['id'], now()))[:32]
    desk.db.execute('INSERT INTO pieces510_drafts VALUES(?,?,?,?,?,?,?,?)', (did, matter['id'], 'projet', json.dumps(data, ensure_ascii=False), now(), now(), None, ''))
    desk.db.commit()
    desk.audit('pieces_510_analyse', {'matter': matter['id'], 'pdf': len(pdfs), 'others': len(others)})
    return {'draft': did, 'pieces': len(rows), 'retenues': len(included(data)),
            'message': 'Projet de bordereau préparé : %d fichier(s) analysé(s), %d retenu(s). Relisez le tableau.' % (len(rows), len(included(data)))}


def sort_rows(rows, order, conclusions_text=''):
    def by_name(r):
        n = r.get('number_hint')
        return (0 if n is not None else 1, n or 0, fold(r['name']))
    if order == 'chronologique':
        return sorted(rows, key=lambda r: (not r.get('include'), r.get('date') or '9999', by_name(r)))
    if order == 'conclusions' and conclusions_text:
        first = {}
        for n, pos, _ in citations(conclusions_text):
            first.setdefault(n, pos)
        return sorted(rows, key=lambda r: (not r.get('include'), first.get(r.get('number_hint'), 10 ** 9), by_name(r)))
    return sorted(rows, key=lambda r: (not r.get('include'), by_name(r)))


def update_table(desk, did, rows_in, case=None, opts=None, order=None):
    """Enregistre le tableau corrigé par l'avocat : ordre, intitulés, dates, pièces retenues, paramètres du bordereau."""
    draft = get(desk, did)
    if draft['status'] in ('en_creation', 'cree'):
        raise Stop('bordereau_deja_cree')
    data = draft['data']
    by_key = {r['key']: r for r in data['rows']}
    if rows_in is not None:
        if not isinstance(rows_in, list) or len(rows_in) > MAX_ROWS:
            raise Stop('tableau_invalide')
        ordered, seen = [], set()
        for item in rows_in:
            key = str(item.get('key', ''))
            if key not in by_key or key in seen:
                raise Stop('tableau_invalide')
            seen.add(key)
            row = by_key[key]
            row['title'] = _text(item.get('title', row['title']), 300) or row['title']
            d = str(item.get('date', row.get('date', '')) or '')[:10]
            if d:
                try:
                    date.fromisoformat(d)
                except ValueError:
                    raise Stop('date_piece_invalide') from None
            row['date'] = d
            row['include'] = item.get('include') in (True, 'true', '1', 1, 'on')
            ordered.append(row)
        ordered += [r for r in data['rows'] if r['key'] not in seen]
        data['rows'] = ordered
    if case is not None:
        for key, _ in CASE_FIELDS:
            if key in case:
                data['case'][key] = _text(case.get(key, ''), 300)
        first_number(data)
        if data['case'].get('date_du_bordereau'):
            try:
                date.fromisoformat(data['case']['date_du_bordereau'][:10])
            except ValueError:
                raise Stop('date_bordereau_invalide') from None
        data['conclusions_path'] = str(case.get('conclusions_path', data.get('conclusions_path', '')) or '')[:500]
    if opts is not None:
        data['options'] = clean_options({**data['options'], **opts})
    if order in ORDERS and order != data.get('order'):
        data['order'] = order
        text = ''
        if order == 'conclusions':
            try:
                text = conclusions_text(desk, data)[1]
            except Stop:
                text = ''
        data['rows'] = sort_rows(data['rows'], order, text)
    data['plan'] = {}
    _save(desk, did, data, 'projet')
    return {'draft': did, 'retenues': len(included(data))}


# ------------------------------------------------------------------------------------------------- conclusions et contrôles
def conclusions_text(desk, data, dav=None):
    """(chemin, texte) des conclusions choisies, ou des plus récentes du dossier."""
    matter = _matter(desk, data['matter'])
    client = dav or _dav(desk, data['options'])
    path = data.get('conclusions_path') or ''
    if path:
        path = clean_path(path)
        if not under(path, matter['path']):
            raise Stop('dossier_hors_racines')
        item = client.stat(path)
    else:
        found = [x for x in client.inventory(matter['path']) if not x.get('directory') and 'conclusion' in fold(PurePosixPath(x['path']).name)
                 and PurePosixPath(x['path']).suffix.lower() in ('.docx', '.pdf') and not _is_output(x['path'], matter['path'])]
        if not found:
            raise Stop('conclusions_introuvables')
        from email.utils import parsedate_to_datetime

        def stamp(x):
            try:
                return parsedate_to_datetime(str(x.get('modified', ''))).timestamp()
            except (TypeError, ValueError):
                return 0
        item = sorted(found, key=stamp)[-1]
    raw = client.download(item)
    if item['path'].lower().endswith('.pdf'):
        reader = open_pdf(raw)
        text = '\n'.join((p.extract_text() or '') for p in reader.pages[:400])
    else:
        from .documents import extract
        text = extract(raw, PurePosixPath(item['path']).name, {**desk.c.get('documents', {}), 'max_document_chars': 2_000_000, 'max_file_bytes': 30_000_000})
    return item['path'], text


def check(desk, did, dav=None):
    draft = get(desk, did)
    data = draft['data']
    opts = data['options']
    rows = included(data)
    issues = []

    def add(level, text, number=None):
        issues.append({'level': level, 'text': text, 'number': number})
    if not rows:
        add('bloquant', 'Aucune pièce retenue.')
    total = 0
    by_sha = {}
    first = first_number(data)
    for n, r in enumerate(rows, first):
        total += r['size']
        if not r['readable']:
            add('bloquant', 'Pièce n° %d (%s) : %s.' % (n, r['name'], {'piece_pdf_protegee': 'PDF protégé par un mot de passe',
                                                                     'piece_trop_volumineuse': 'fichier trop volumineux pour être lu'}.get(r['error'], 'PDF illisible')), n)
        if r.get('scan'):
            add('info', 'Pièce n° %d : document numérisé sans texte ; vérifiez l’intitulé et la date proposés.' % n, n)
        if r['size'] > opts['max_file_mb'] * 1_000_000:
            add('attention', 'Pièce n° %d : %.1f Mo, au-delà de la limite de %d Mo réglée pour votre canal de dépôt.' % (n, r['size'] / 1e6, opts['max_file_mb']), n)
        if not r['title'].strip():
            add('bloquant', 'Pièce n° %d : intitulé vide.' % n, n)
        if r['sha256'] and r['sha256'] in by_sha:
            add('attention', 'Pièces n° %d et %d : fichiers identiques (doublon).' % (by_sha[r['sha256']], n), n)
        by_sha.setdefault(r['sha256'], n)
    if total > opts['max_total_mb'] * 1_000_000:
        add('attention', 'Poids total %.1f Mo, au-delà de la limite de %d Mo réglée.' % (total / 1e6, opts['max_total_mb']))
    cited, conclusions = {}, ''
    try:
        conclusions, text = conclusions_text(desk, data, dav)
        for n, _, excerpt in citations(text):
            cited.setdefault(n, excerpt)
    except Stop as ex:
        add('info', 'Conclusions non contrôlées : %s.' % {'conclusions_introuvables': 'aucun fichier « conclusions » trouvé dans le dossier'}.get(str(ex), str(ex).replace('_', ' ')))
    if conclusions:
        from . import pieces520
        earlier = {h['number'] for h in pieces520.history(desk, data['matter'])}
        current = set(range(first, first + len(rows)))
        for n in sorted(cited):
            if n not in current and n not in earlier:
                add('bloquant', 'Pièce n° %d citée dans les conclusions mais absente du bordereau et des communications précédentes (« %s »).' % (n, cited[n]), n)
        for n in sorted(current):
            if n not in cited:
                add('attention', 'Pièce n° %d (%s) jamais citée dans les conclusions.' % (n, rows[n - first]['title']), n)
    order = {'bloquant': 0, 'attention': 1, 'info': 2}
    issues.sort(key=lambda i: (order[i['level']], i['number'] or 0))
    data['checks'] = {'at': now(), 'conclusions': conclusions, 'issues': issues, 'cited': sorted(cited), 'total_bytes': total}
    _save(desk, did, data)
    return data['checks']


# ------------------------------------------------------------------------------------------------- instructions
_NUM_LIST = r'(\d{1,3}(?:\s*(?:,|et|a|au|-)\s*\d{1,3})*)'


def _numbers(raw):
    tokens = re.findall(r'\d{1,3}|\ba\b|\bau\b|-', raw)
    out = []
    for i, tok in enumerate(tokens):
        if tok.isdigit():
            if out and i >= 1 and tokens[i - 1] in ('a', 'au', '-') and out[-1] < int(tok) <= out[-1] + 200:
                out.extend(range(out[-1] + 1, int(tok) + 1))
            else:
                out.append(int(tok))
    return out


def _match_candidate(what, pool):
    words = {w for w in re.findall(r'[a-z0-9]{3,}', fold(what)) if w not in {'piece', 'pieces', 'les', 'des', 'une', 'the', 'pdf'}}
    if not words:
        return None, []
    scored = []
    for r in pool:
        hay = set(re.findall(r'[a-z0-9]{3,}', fold(r['title'] + ' ' + r['name'])))
        hit = len(words & hay)
        if hit:
            scored.append((hit / len(words), r))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return None, []
    best = [r for s, r in scored if s == scored[0][0]]
    return (best[0] if len(best) == 1 and scored[0][0] >= 0.5 else None), [r for _, r in scored[:5]]


def plan_instructions(desk, did, instruction, dav=None):
    """Prépare (sans rien appliquer) la renumérotation demandée et les renvois des conclusions à corriger."""
    draft = get(desk, did)
    data = draft['data']
    text = fold(_text(instruction, 2000))
    if not text:
        raise Stop('instruction_vide')
    current = included(data)
    pool = [r for r in data['rows'] if not r.get('include')]
    first = first_number(data)
    numbered = {n: r for n, r in enumerate(current, first)}
    errors, steps = [], []
    removed, moves, adds, renames = set(), [], [], []
    for clause in re.split(r'[;\n]|\.\s|,\s*(?=(?:puis\s+)?(?:retir|supprim|enlev|ot|ajout|inser|deplac|mets|place|renomm|intitul|renumerot))|\bpuis\b', text):
        c = clause.strip(' ,.')
        if not c:
            continue
        m = re.search(r'\b(?:retir\w*|supprim\w*|enlev\w*|ot\w*)\s+(?:la\s+|les\s+)?pieces?\s*(?:n\s*[°o]\s*\.?|nos?\.?)?\s*' + _NUM_LIST, c)
        if m:
            for n in _numbers(m.group(1)):
                if n not in numbered:
                    errors.append('La pièce n° %d n’existe pas dans le bordereau actuel.' % n)
                else:
                    removed.add(n)
                    steps.append('Retirer la pièce n° %d (%s).' % (n, numbered[n]['title']))
            continue
        m = re.search(r'\b(?:deplac\w*|mets|mettre|place\w*)\s+(?:la\s+)?pieces?\s*(?:n\s*[°o]\s*\.?)?\s*(\d{1,3})\s+(?:en|a la place de la|a la|au rang|en position)\s*(?:pieces?\s*)?(?:n\s*[°o]\s*\.?|position\s+)?\s*(\d{1,3})', c)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a not in numbered:
                errors.append('La pièce n° %d n’existe pas dans le bordereau actuel.' % a)
            else:
                moves.append((a, b))
                steps.append('Placer la pièce n° %d (%s) en position %d.' % (a, numbered[a]['title'], b))
            continue
        m = re.search(r'\b(?:renomm\w*|intitul\w*)\s+(?:la\s+)?pieces?\s*(?:n\s*[°o]\s*\.?)?\s*(\d{1,3})\s*(?:en|:|par)\s*(.+)$', c)
        if m:
            n = int(m.group(1))
            title = _text(instruction_original_slice(instruction, m.group(2)), 300)
            if n not in numbered:
                errors.append('La pièce n° %d n’existe pas dans le bordereau actuel.' % n)
            else:
                renames.append((n, title))
                steps.append('Renommer la pièce n° %d en « %s ».' % (n, title))
            continue
        m = re.search(r'\b(?:ajout\w*|inser\w*)\s+(.+?)(?:\s+(?:en|comme|a la)\s+(?:pieces?\s*)?(?:n\s*[°o]\s*\.?|position\s+)?\s*(\d{1,3})|\s+(?:a la fin|en fin(?: de bordereau)?|en dernier))?\s*$', c)
        if m:
            what = m.group(1)
            target = int(m.group(2)) if m.group(2) else None
            found, near = _match_candidate(what, pool)
            if not found:
                hint = ' Fichiers proches : ' + ' ; '.join(r['name'] for r in near) + '.' if near else ''
                errors.append('Aucun fichier non retenu ne correspond clairement à « %s ».%s Précisez le nom du fichier.' % (what.strip(), hint))
            else:
                adds.append((target, found))
                pool = [r for r in pool if r is not found]
                steps.append('Ajouter « %s » (%s) %s.' % (found['title'], found['name'], ('en position %d' % target) if target else 'à la fin'))
            continue
        if re.search(r'\brenumerot', c):
            continue
        errors.append('Instruction non comprise : « %s ». Formulations reconnues : retire la pièce 7 ; ajoute <fichier> en 9 ; '
                      'place la pièce 3 en 1 ; renomme la pièce 2 en <intitulé>.' % clause.strip())
    # nouvelle liste
    remaining = [(n, r) for n, r in numbered.items() if n not in removed]
    for a, b in moves:
        item = next(((n, r) for n, r in remaining if n == a), None)
        if item:
            remaining.remove(item)
            remaining.insert(max(0, min(b - first, len(remaining))), item)
    for target, row in sorted(adds, key=lambda x: (x[0] is None, x[0] or 0)):
        pos = len(remaining) if target is None else max(0, min(target - first, len(remaining)))
        remaining.insert(pos, (None, row))
    mapping = []
    for new, (old, row) in enumerate(remaining, first):
        mapping.append({'old': old, 'new': new, 'key': row['key'], 'title': dict(renames).get(old, row['title']), 'name': row['name']})
    renumbered = {m['old']: m['new'] for m in mapping if m['old'] is not None}
    references = []
    try:
        path, ctext = conclusions_text(desk, data, dav)
        for n, _, excerpt in citations(ctext):
            if n in removed:
                references.append({'old': n, 'new': None, 'excerpt': excerpt})
            elif n in renumbered and renumbered[n] != n:
                references.append({'old': n, 'new': renumbered[n], 'excerpt': excerpt})
    except Stop:
        path = ''
    plan = {'instruction': _text(instruction, 2000), 'steps': steps, 'errors': errors, 'mapping': mapping,
            'removed': sorted(removed), 'references': references[:200], 'conclusions': path, 'at': now()}
    data['plan'] = plan
    _save(desk, did, data)
    return plan


def instruction_original_slice(original, folded_part):
    """Retrouve l'intitulé tel que saisi (avec accents) à partir de sa version normalisée."""
    folded = fold(original)
    i = folded.find(folded_part.strip())
    if i >= 0 and len(folded) == len(original):
        return original[i:i + len(folded_part.strip())]
    return folded_part


def apply_plan(desk, did):
    draft = get(desk, did)
    data = draft['data']
    plan = data.get('plan') or {}
    if not plan.get('mapping') or plan.get('errors'):
        raise Stop('plan_absent_ou_incomplet')
    by_key = {r['key']: r for r in data['rows']}
    new_rows = []
    for m in plan['mapping']:
        row = by_key[m['key']]
        row['include'] = True
        row['title'] = m['title']
        new_rows.append(row)
    keys = {m['key'] for m in plan['mapping']}
    for r in data['rows']:
        if r['key'] not in keys:
            r['include'] = False
            new_rows.append(r)
    data['rows'] = new_rows
    data['last_plan'] = plan
    from . import pieces520
    data['renumber'] = pieces520.compose(data.get('renumber'), plan)
    data['plan'] = {}
    _save(desk, did, data, 'projet')
    desk.audit('pieces_510_plan_applique', {'draft': did, 'pieces': len(keys)})
    return {'draft': did, 'retenues': len(keys), 'references': len(plan.get('references', []))}


# ------------------------------------------------------------------------------------------------- modèle Word
def template_bytes(desk):
    custom = Path(desk.c['state_dir']) / 'pieces510' / 'modele_bordereau.docx'
    if custom.is_file():
        return custom.read_bytes(), 'cabinet'
    return (Path(__file__).resolve().parents[1] / 'templates' / 'MODELE_BCP_BALISES.docx').read_bytes(), 'fourni'


def template_fields(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = [n for n in z.namelist() if n == 'word/document.xml' or re.fullmatch(r'word/(?:header|footer)\d+\.xml', n)]
        text = ''.join(_joined_text(z.read(n).decode('utf-8')) for n in names)
    return sorted(set(re.findall(r'\{\{\s*([A-Za-z0-9_]+)\s*\}\}', text)))


def save_template(desk, raw):
    if not raw or len(raw) > 2_000_000 or not raw.startswith(b'PK'):
        raise Stop('modele_bordereau_invalide')
    try:
        fields = template_fields(raw)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        raise Stop('modele_bordereau_invalide') from None
    if not any(f.startswith('intitule_piece_') for f in fields):
        raise Stop('modele_bordereau_sans_liste_de_pieces')
    folder = Path(desk.c['state_dir']) / 'pieces510'
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / '.modele.tmp'
    tmp.write_bytes(raw)
    tmp.replace(folder / 'modele_bordereau.docx')
    desk.audit('pieces_510_modele', {'sha256': hashlib.sha256(raw).hexdigest()[:16], 'fields': len(fields)})
    return {'fields': fields}


def reset_template(desk):
    custom = Path(desk.c['state_dir']) / 'pieces510' / 'modele_bordereau.docx'
    if custom.is_file():
        custom.unlink()
    return {'template': 'fourni'}


_PARA = re.compile(r'<w:p\b(?:(?!<w:p\b).)*?</w:p>', re.S)
_RUN_TEXT = re.compile(r'(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)')


def _joined_text(xml):
    return '\n'.join(''.join(html.unescape(m.group(2)) for m in _RUN_TEXT.finditer(p)) for p in _PARA.findall(xml))


def _edit_paragraph(p_xml, edits_fn):
    """Applique des remplacements au texte d'un paragraphe même lorsque Word a découpé les balises en plusieurs « runs »."""
    runs = list(_RUN_TEXT.finditer(p_xml))
    if not runs:
        return p_xml
    texts = [html.unescape(m.group(2)) for m in runs]
    joined = ''.join(texts)
    edits = sorted(edits_fn(joined))
    if not edits:
        return p_xml
    new = [''] * len(runs)
    owner = []
    for i, t in enumerate(texts):
        owner += [i] * len(t)
    g = 0
    k = 0
    while g < len(joined):
        if k < len(edits) and g == edits[k][0]:
            s, e, rep = edits[k]
            new[owner[s]] += rep
            g = e
            k += 1
            continue
        new[owner[g]] += joined[g]
        g += 1
    while k < len(edits):  # remplacement en fin de texte
        new[-1] += edits[k][2]
        k += 1
    out, last = [], 0
    for m, t in zip(runs, new):
        start_tag = m.group(1)
        if (t[:1].isspace() or t[-1:].isspace()) and 'xml:space' not in start_tag:
            start_tag = start_tag[:-1] + ' xml:space="preserve">'
        out.append(p_xml[last:m.start()] + start_tag + html.escape(t, quote=False) + m.group(3))
        last = m.end()
    out.append(p_xml[last:])
    return ''.join(out)


def _scalar_edits(values, missing):
    def fn(text):
        edits = []
        for m in re.finditer(r'\{\{\s*([A-Za-z0-9_]+)\s*\}\}', text):
            key = m.group(1)
            if key.startswith('intitule_piece_'):
                continue
            if key in values and str(values[key]).strip():
                edits.append((m.start(), m.end(), str(values[key])))
            else:
                missing.add(key)
                edits.append((m.start(), m.end(), '[à compléter]'))
        return edits
    return fn


def fill_template(raw, values, titles, first=1):
    """Bordereau DOCX : balises {{ … }} remplacées, liste « Pièce n°XX » étendue au nombre réel de pièces. Renvoie (octets, manquants)."""
    missing = set()
    src = zipfile.ZipFile(io.BytesIO(raw))
    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, 'w', zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            name = info.filename
            if name == 'word/document.xml' or re.fullmatch(r'word/(?:header|footer)\d+\.xml', name):
                xml = data.decode('utf-8')
                if name == 'word/document.xml':
                    xml = _expand_piece_list(xml, titles, first)
                xml = _PARA.sub(lambda m: _edit_paragraph(m.group(0), _scalar_edits(values, missing)), xml)
                data = xml.encode('utf-8')
            out.writestr(info, data)
    return out_buf.getvalue(), sorted(missing)


def _expand_piece_list(xml, titles, first=1):
    paras = list(_PARA.finditer(xml))
    piece = [m for m in paras if re.search(r'\{\{\s*intitule_piece_\d+\s*\}\}', ''.join(html.unescape(x.group(2)) for x in _RUN_TEXT.finditer(m.group(0))))]
    if not piece:
        return xml
    proto = piece[0].group(0)
    proto_text = ''.join(html.unescape(x.group(2)) for x in _RUN_TEXT.finditer(proto))
    pad = len(re.search(r'n\s*°\s*(\d+)', proto_text).group(1)) if re.search(r'n\s*°\s*(\d+)', proto_text) else 0
    clones = []
    for n, title in enumerate(titles, first):
        def fn(text, n=n, title=title):
            edits = []
            num = re.search(r'(n\s*°\s*)(\d+)', text)
            if num:
                edits.append((num.start(2), num.end(2), str(n).zfill(pad) if pad else str(n)))
            tag = re.search(r'\{\{\s*intitule_piece_\d+\s*\}\}', text)
            if tag:
                edits.append((tag.start(), tag.end(), title))
            return edits
        clones.append(_edit_paragraph(proto, fn))
    first, last = piece[0].start(), piece[-1].end()
    middle = xml[first:last]
    # Les paragraphes non « pièce » situés entre deux lignes de pièces (rare) sont conservés après la liste.
    keep = ''.join(m.group(0) for m in paras if first <= m.start() < last and m not in piece)
    return xml[:first] + ''.join(clones) + keep + xml[last:]


def docx_to_pdf(raw, timeout=120):
    office = shutil.which('libreoffice') or shutil.which('soffice')
    if not office:
        return None
    with tempfile.TemporaryDirectory(prefix='axiorhub-bordereau-') as td:
        source = Path(td) / 'bordereau.docx'
        source.write_bytes(raw)
        try:
            subprocess.run([office, '-env:UserInstallation=' + (Path(td) / 'profile').as_uri(), '--headless', '--convert-to', 'pdf',
                            '--outdir', td, str(source)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                           env={'PATH': '/usr/bin:/bin', 'HOME': td, 'LANG': 'C.UTF-8'})
        except (OSError, subprocess.TimeoutExpired):
            return None
        pdf = Path(td) / 'bordereau.pdf'
        return pdf.read_bytes() if pdf.is_file() else None


def template_values(data, prof):
    values = dict(prof)
    values.update(data['case'])
    for key in ('date_du_bordereau', 'date_audience'):
        if values.get(key) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', values[key]):
            values[key] = fr_date(values[key])
    return values


def preview_docx(desk, did):
    draft = get(desk, did)
    raw, _ = template_bytes(desk)
    titles = [r['title'] for r in included(draft['data'])]
    return fill_template(raw, template_values(draft['data'], profile(desk)), titles, first_number(draft['data']))


# ------------------------------------------------------------------------------------------------- création
def _blocking(desk, data):
    reasons = []
    rows = included(data)
    first = first_number(data)
    if not rows:
        reasons.append('Aucune pièce retenue.')
    for n, r in enumerate(rows, first):
        if not r['readable']:
            reasons.append('Pièce n° %d illisible ou protégée.' % n)
        if not r['title'].strip():
            reasons.append('Pièce n° %d sans intitulé.' % n)
    prof = profile(desk)
    opts = data['options']
    if opts['stamp'] != 'none' and not opts['stamp_image'] and not (prof['nom_avocat'] and prof['ville_avocat']):
        reasons.append('Profil de l’avocat incomplet (nom et barreau) : le tampon ne peut pas être créé.')
    if opts['stamp'] != 'none' and opts['stamp_image']:
        from . import pieces520
        if not pieces520.stamp_image_path(desk):
            reasons.append('Tampon importé demandé, mais aucune image de tampon n’a été importée.')
    if not data['case'].get('numero_bordereau'):
        reasons.append('Numéro du bordereau manquant.')
    return reasons


def request_creation(desk, did):
    draft = get(desk, did)
    if draft['status'] in ('en_creation', 'cree'):
        raise Stop('bordereau_deja_cree')
    reasons = _blocking(desk, draft['data'])
    if reasons:
        return {'ready': False, 'reasons': reasons}
    code = '%06d' % secrets.randbelow(1_000_000)
    data = draft['data']
    data['challenge'] = {'hash': hashlib.sha256((did + '|' + code).encode()).hexdigest(),
                         'expires': (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}
    _save(desk, did, data, 'a_confirmer')
    image = data['options']['stamp'] != 'none' and data['options']['stamp_image']
    return {'ready': True, 'code': code, 'expires_minutes': 30, 'image_confirmation': bool(image)}


def confirm_creation(desk, did, code, confirm_image=False):
    draft = get(desk, did)
    data = draft['data']
    ch = data.get('challenge') or {}
    if draft['status'] != 'a_confirmer' or not ch:
        raise Stop('confirmation_bordereau_absente')
    if datetime.now(timezone.utc) > datetime.fromisoformat(ch['expires']):
        raise Stop('confirmation_bordereau_expiree')
    expected = hashlib.sha256((did + '|' + str(code or '').strip()).encode()).hexdigest()
    if not secrets.compare_digest(expected, ch['hash']):
        raise Stop('code_confirmation_bordereau_invalide')
    if data['options']['stamp'] != 'none' and data['options']['stamp_image']:
        if confirm_image not in (True, 'true', '1', 'on'):
            raise Stop('confirmation_tampon_image_requise')
        data['image_confirmed'] = now()          # confirmation propre à ce bordereau, jamais reportée sur un autre
        desk.audit('pieces_520_tampon_image_confirme', {'draft': did})
    job = desk.enqueue('pieces_create510', {'draft': did, 'matter': draft['matter']}, priority=0)
    data['challenge'] = {}
    desk.db.execute('UPDATE pieces510_drafts SET status=?, job_id=?, data=?, updated=? WHERE id=?',
                    ('en_creation', job, json.dumps(data, ensure_ascii=False), now(), did))
    desk.db.commit()
    return {'job_id': job}


def _safe_name(text, maximum=110):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', str(text))
    text = re.sub(r'\s+', ' ', text).strip(' .')
    return text[:maximum].rstrip(' .') or 'piece'


def _new_folder(client, base):
    target, suffix = base, 1
    while True:
        try:
            client.list_folder(target)
        except Stop as ex:
            if str(ex) != 'http_404':
                raise
            return target
        suffix += 1
        target = base + ' (%d)' % suffix


def _conclusions_update(desk, data, client):
    """Nouvelle version des conclusions Word avec les renvois renumérotés en suivi de modifications ; None si sans objet."""
    mapping = data.get('renumber') or {}
    if not data['options'].get('fix_conclusions') or not any(str(k) != str(v) for k, v in mapping.items()):
        return None
    try:
        path, _ = conclusions_text(desk, data, client)
    except Stop:
        return {'error': 'conclusions_introuvables'}
    if not path.lower().endswith('.docx'):
        return {'error': 'conclusions_non_word', 'path': path}
    raw = client.download({**client.stat(path), 'path': path})
    from . import pieces520
    docx, applied, manual = pieces520.track_renumbering(raw, mapping)
    return {'path': path, 'docx': docx, 'applied': applied, 'manual': [{'piece': str(n), 'excerpt': str(x)[:300]} for n, x in manual]}


def create(desk, args, dav=None):
    """Crée les fichiers dans un NOUVEAU dossier Nextcloud ; refuse si une pièce a changé depuis l'analyse."""
    from . import pieces520
    did = str(args.get('draft', ''))
    draft = get(desk, did)
    data = draft['data']
    if draft['status'] not in ('en_creation',):
        raise Stop('confirmation_bordereau_absente')
    reasons = _blocking(desk, data)
    if reasons:
        raise Stop('bordereau_incomplet')
    opts = data['options']
    use_image = opts['stamp'] != 'none' and opts['stamp_image']
    if use_image and not data.get('image_confirmed'):
        raise Stop('confirmation_tampon_image_requise')
    image = pieces520.stamp_image_path(desk).read_bytes() if use_image else None
    prof = profile(desk)
    matter = _matter(desk, data['matter'])
    client = dav or _dav(desk, opts)
    rows = included(data)
    first = first_number(data)
    sources, ocr_pages = [], 0
    for r in rows:
        info = client.stat(r['path'])
        raw = client.download({**info, 'path': r['path']})
        if hashlib.sha256(raw).hexdigest() != r['sha256']:
            raise Stop('piece_modifiee_depuis_analyse')
        pdf = jpeg_to_pdf(raw) if r['kind'] == 'jpeg' else raw
        if opts['ocr'] and r.get('scan'):
            pdf, done = pieces520.make_searchable(pdf)
            ocr_pages += done
        sources.append(pdf)
    grand_total = sum(r['pages'] for r in rows)
    stamped, offset = [], 0
    for n, (r, raw) in enumerate(zip(rows, sources), first):
        pdf, pages = stamp_document(raw, n, opts, prof, offset, grand_total, image)
        offset += pages
        stamped.append((n, r, pdf, 'Pièce %02d - %s.pdf' % (n, _safe_name(r['title']))))
    tpl, _ = template_bytes(desk)
    docx, missing = fill_template(tpl, template_values(data, prof), [r['title'] for r in rows], first)
    bordereau_pdf = docx_to_pdf(docx)
    label = 'Bordereau n°%s du %s' % (_safe_name(data['case'].get('numero_bordereau', ''), 20), data['case'].get('date_du_bordereau', '')[:10])
    files = [(label + '.docx', docx, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')]
    if bordereau_pdf:
        files.append((label + '.pdf', bordereau_pdf, 'application/pdf'))
    if opts['single_pdf']:
        parts = ([('Bordereau', bordereau_pdf)] if (bordereau_pdf and opts['bordereau_first']) else []) + [
            ('Pièce n° %d – %s' % (n, r['title']), p) for n, r, p, _ in stamped]
        files.append(('Pièces communiquées - ' + label + '.pdf', combine(parts), 'application/pdf'))
    else:
        files += [(name, pdf, 'application/pdf') for _, _, pdf, name in stamped]
    renvois = _conclusions_update(desk, data, client)
    if renvois and renvois.get('docx'):
        stem = PurePosixPath(renvois['path']).stem
        files.append((_safe_name(stem, 80) + ' - renvois mis à jour (suivi des modifications).docx', renvois['docx'],
                      'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
    deposit = None
    if opts['deposit']:
        units = ([(None, 'bordereau', bordereau_pdf)] if bordereau_pdf else []) + [(n, r['title'], p) for n, r, p, _ in stamped]
        envois, warnings = pieces520.plan_deposit(units, opts['deposit_mb'])
        for i, env in enumerate(envois, 1):
            for name, raw in env:
                files.append(('Dépôt e-Barreau/Envoi %d/%s' % (i, name), raw, 'application/pdf'))
        files.append(('Dépôt e-Barreau/LISEZMOI.txt', pieces520.deposit_readme(envois, warnings, opts['deposit_mb']).encode('utf-8'), 'text/plain'))
        deposit = {'envois': len(envois), 'warnings': warnings}
    target = _new_folder(client, clean_path(matter['path'] + '/' + OUTPUT_FOLDER + '/' + _safe_name(label, 80)))
    client.ensure_folder(target, matter['path'])
    created = []
    for name, raw, kind in files:
        path = clean_path(target + '/' + name)
        parent = str(PurePosixPath(path).parent)
        if parent != target:
            client.ensure_folder(parent, matter['path'])
        client.put_file(path, raw, kind)
        info = client.stat(path)
        if int(info.get('size', 0)) != len(raw):
            raise Stop('fichier_bordereau_non_verifie')
        try:
            url = client.file_web_url(path)
        except Exception:
            url = ''
        created.append({'path': path, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'url': url})
    pieces520.record(desk, matter['id'], did, data['case'], rows, first)
    result = {'folder': target, 'files': created, 'missing_fields': missing, 'bordereau_pdf': bool(bordereau_pdf),
              'pieces': len(rows), 'pages': grand_total, 'first': first, 'ocr_pages': ocr_pages, 'image_stamp': bool(image),
              'deposit': deposit, 'renvois': ({k: v for k, v in renvois.items() if k != 'docx'} if renvois else None), 'at': now()}
    desk.db.execute('UPDATE pieces510_drafts SET status=?, result=?, updated=? WHERE id=?', ('cree', json.dumps(result, ensure_ascii=False), now(), did))
    desk.db.commit()
    desk.audit('pieces_510_cree', {'matter': matter['id'], 'pieces': len(rows), 'files': len(created), 'ocr_pages': ocr_pages})
    message = '%d fichier(s) créé(s) dans « %s ».' % (len(created), target)
    if not bordereau_pdf:
        message += ' Bordereau PDF non produit (LibreOffice absent) : le DOCX est disponible.'
    if missing:
        message += ' Champs à compléter dans le bordereau : ' + ', '.join(missing) + '.'
    if renvois and renvois.get('docx'):
        message += ' Conclusions : %d renvoi(s) corrigé(s) en suivi de modifications%s.' % (
            renvois['applied'], (', %d à revoir à la main' % len(renvois['manual'])) if renvois['manual'] else '')
    return {'draft': did, 'folder': target, 'created_files': [{'path': c['path'], 'edit_url': c['url']} for c in created], 'message': message}


def abandon(desk, did):
    draft = get(desk, did)
    if draft['status'] == 'en_creation':
        raise Stop('bordereau_en_creation')
    desk.db.execute("UPDATE pieces510_drafts SET status='abandonne', updated=? WHERE id=?", (now(), did))
    desk.db.commit()
    return {'draft': did, 'status': 'abandonne'}


def perform(desk, kind, args):
    if kind == 'pieces_scan510':
        return scan(desk, args)
    if kind == 'pieces_create510':
        try:
            return create(desk, args)
        except Stop:
            desk.db.execute("UPDATE pieces510_drafts SET status='projet', updated=? WHERE id=? AND status='en_creation'", (now(), str(args.get('draft', ''))))
            desk.db.commit()
            raise
    raise Stop('action_inconnue')

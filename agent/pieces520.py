"""Pièces et bordereaux, compléments 5.2.0.

  - OCR local (Tesseract) : texte des scans pour proposer intitulés et dates, et PDF de pièces rendus cherchables (couche de texte
    invisible superposée à la page d'origine, l'image n'est pas recompressée) ;
  - historique des communications (quelle pièce, sous quel bordereau, à qui, quand) et numérotation continue ;
  - correction des renvois dans les conclusions Word, en suivi de modifications natif (nouveau fichier, original intact) ;
  - tampon importé (PNG ou JPEG : tampon scanné, signature), apposé seulement après confirmation explicite ;
  - préparation du dépôt : envois sous la limite réglée (10 Mo par message e-Barreau par défaut), noms de fichiers sûrs, découpage par pages ;
  - lecture d'un bordereau adverse reçu et rapprochement avec les pièces reçues et les conclusions adverses.

Traitement local : aucun modèle de langage, aucun service externe.
"""
from datetime import datetime, timezone
import hashlib
import html
import io
import json
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from .common import Stop, digest, fold

SCHEMA = '''
CREATE TABLE IF NOT EXISTS pieces520_comms(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, draft TEXT NOT NULL, bordereau TEXT NOT NULL, day TEXT NOT NULL,
  recipient TEXT NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pieces520_comms_matter ON pieces520_comms(matter, number);
CREATE INDEX IF NOT EXISTS pieces520_comms_sha ON pieces520_comms(matter, sha256);
'''
DEPOSIT_NOTE = ('Limite par défaut : 10 Mo de pièces jointes au total par message e-Barreau / RPVA (formats PDF, Word, ODT, RTF) ; '
                'au-delà, e-Partage sécurisé. Règle relevée le 3 octobre 2026 (CNB, guides d’utilisation) : à vérifier, elle peut évoluer.')


def ensure_schema(desk):
    if not desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pieces520_comms'").fetchone():
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def now():
    return datetime.now(timezone.utc).isoformat()


# ================================================================================================== OCR
TESSERACT = '/usr/bin/tesseract'
PDFTOPPM = '/usr/bin/pdftoppm'
_langs = {}


def _run(args, timeout=120):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                           env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'OMP_THREAD_LIMIT': '1'})
    except (OSError, subprocess.TimeoutExpired):
        raise Stop('ocr_indisponible') from None
    if p.returncode:
        raise Stop('ocr_echec')
    return p.stdout


def ocr_available():
    """('fra+eng' | 'eng' | '') selon les langues Tesseract installées."""
    if 'value' in _langs:
        return _langs['value']
    value = ''
    if Path(TESSERACT).is_file() and Path(PDFTOPPM).is_file():
        try:
            langs = set(_run([TESSERACT, '--list-langs'], 20).decode('utf-8', 'replace').split())
            value = 'fra+eng' if 'fra' in langs else ('eng' if 'eng' in langs else '')
        except Stop:
            value = ''
    _langs['value'] = value
    return value


def _render(raw, page_number, dpi, td):
    src = Path(td) / 'in.pdf'
    if not src.exists():
        src.write_bytes(raw)
    stem = Path(td) / ('p%d' % page_number)
    _run([PDFTOPPM, '-f', str(page_number), '-l', str(page_number), '-r', str(dpi), '-singlefile', '-png', str(src), str(stem)], 120)
    return stem.with_suffix('.png')


def ocr_text(raw, pages=1):
    """Texte OCR des premières pages d'un PDF numérisé ('' si OCR indisponible ou sans résultat)."""
    lang = ocr_available()
    if not lang:
        return ''
    out = []
    with tempfile.TemporaryDirectory(prefix='axiorhub-ocr-') as td:
        for n in range(1, pages + 1):
            try:
                png = _render(raw, n, 200, td)
                out.append(_run([TESSERACT, str(png), 'stdout', '-l', lang], 120).decode('utf-8', 'replace'))
            except Stop:
                break
    return '\n'.join(out).strip()


def make_searchable(raw, max_pages=200, min_chars=30):
    """Ajoute une couche de texte invisible aux pages numérisées (Tesseract « textonly_pdf »). Renvoie (octets, pages traitées)."""
    from . import pieces510 as p5
    lang = ocr_available()
    if not lang:
        return raw, 0
    pypdf = p5._pdf_lib()
    reader = p5.open_pdf(raw)
    writer = pypdf.PdfWriter()
    done = 0
    with tempfile.TemporaryDirectory(prefix='axiorhub-ocrpdf-') as td:
        for index, page in enumerate(reader.pages):
            try:
                page.transfer_rotation_to_content()
            except Exception:
                pass
            try:
                text = page.extract_text() or ''
            except Exception:
                text = ''
            if len(text.strip()) < min_chars and done < max_pages:
                try:
                    png = _render(raw, index + 1, 300, td)
                    base = Path(td) / ('t%d' % index)
                    _run([TESSERACT, str(png), str(base), '-l', lang, '-c', 'textonly_pdf=1', 'pdf'], 180)
                    layer = pypdf.PdfReader(str(base) + '.pdf').pages[0]
                    box = page.cropbox
                    sx = float(box.width) / float(layer.mediabox.width)
                    sy = float(box.height) / float(layer.mediabox.height)
                    page.merge_transformed_page(layer, pypdf.Transformation().scale(sx, sy).translate(float(box.left), float(box.bottom)))
                    done += 1
                except (Stop, Exception):
                    pass
            writer.add_page(page)
    if not done:
        return raw, 0
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), done


# ================================================================================================== historique
def history(desk, matter, limit=500):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM pieces520_comms WHERE matter=? ORDER BY number LIMIT ?', (matter, limit))]


def last_number(desk, matter):
    ensure_schema(desk)
    row = desk.db.execute('SELECT MAX(number) FROM pieces520_comms WHERE matter=?', (matter,)).fetchone()
    return int(row[0] or 0)


def known_by_sha(desk, matter):
    ensure_schema(desk)
    out = {}
    for r in desk.db.execute('SELECT sha256,number,bordereau,day,recipient FROM pieces520_comms WHERE matter=? ORDER BY created', (matter,)):
        out.setdefault(r['sha256'], dict(r))
    return out


def record(desk, matter, draft, case, rows, first):
    ensure_schema(desk)
    day = (case.get('date_du_bordereau') or now())[:10]
    for i, r in enumerate(rows):
        n = first + i
        desk.db.execute('INSERT OR REPLACE INTO pieces520_comms VALUES(?,?,?,?,?,?,?,?,?,?,?)', (
            digest('comm|%s|%s|%d' % (draft, r['sha256'], n))[:32], matter, draft, str(case.get('numero_bordereau', '')), day,
            str(case.get('avocat_partie_adverse', ''))[:200], n, r['title'][:300], r['sha256'], r['path'], now()))
    desk.db.commit()


# ================================================================================================== renvois en suivi de modifications
_PARA = re.compile(r'<w:p\b(?:(?!<w:p\b).)*?</w:p>', re.S)
_RUN = re.compile(r'<w:r\b(?:(?!<w:r\b).)*?</w:r>', re.S)
_RPR = re.compile(r'<w:rPr\b.*?</w:rPr>|<w:rPr\s*/>', re.S)
_T = re.compile(r'<w:t(?:\s[^>]*)?>([^<]*)</w:t>')
_CITE_GROUP = re.compile(r'\bpieces?\s*(?:n\s*[°o]\s*\.?|nos?\.?|numeros?)?\s*(\d{1,3}(?:\s*(?:,|et|a|au|-|–)\s*(?:n\s*[°o]\s*\.?\s*)?\d{1,3})*)')


def citation_edits(text, mapping):
    """[(début, fin, nouveau)] pour chaque numéro cité à renuméroter ; [(numéro, extrait)] à revoir à la main (pièce retirée, plage modifiée)."""
    folded = fold(text)
    if len(folded) != len(text):
        return [], []
    edits, manual = [], []
    for m in _CITE_GROUP.finditer(folded):
        group = m.group(1)
        base = m.start(1)
        tokens = [(t.start() + base, t.end() + base, t.group()) for t in re.finditer(r'\d{1,3}|\ba\b|\bau\b|-|–', group)]
        nums = [(s, e, int(v)) for s, e, v in tokens if v.isdigit()]
        is_range = any(v in ('a', 'au', '-', '–') for _, _, v in tokens)
        excerpt = re.sub(r'\s+', ' ', text[max(0, m.start() - 60):m.end() + 40]).strip()
        if is_range:
            changed = [v for v in range(min(n for _, _, n in nums), max(n for _, _, n in nums) + 1) if str(v) in mapping and mapping[str(v)] != v]
            if changed:
                lo, hi = nums[0][2], nums[-1][2]
                inside = [mapping.get(str(v), v) for v in range(lo, hi + 1)]
                shift_ok = None not in inside and inside == list(range(inside[0], inside[0] + len(inside)))
                if shift_ok:
                    edits.append((nums[0][0], nums[0][1], str(inside[0])))
                    edits.append((nums[-1][0], nums[-1][1], str(inside[-1])))
                else:
                    manual.append((lo, excerpt))
            continue
        for s, e, v in nums:
            key = str(v)
            if key not in mapping or mapping[key] == v:
                continue
            if mapping[key] is None:
                manual.append((v, excerpt))
            else:
                edits.append((s, e, str(mapping[key])))
    return edits, manual


def _tracked_paragraph(p_xml, mapping, counter, author, stamp):
    runs = list(_RUN.finditer(p_xml))
    pieces = []  # (run match, text, simple)
    for r in runs:
        body = r.group(0)
        ts = _T.findall(body)
        inner = _RPR.sub('', body)
        inner = re.sub(r'^<w:r\b[^>]*>|</w:r>$', '', inner)
        simple = len(ts) == 1 and re.fullmatch(r'\s*<w:t(?:\s[^>]*)?>[^<]*</w:t>\s*', inner) is not None
        pieces.append((r, html.unescape(''.join(ts)), simple))
    joined = ''.join(t for _, t, _ in pieces)
    edits, manual = citation_edits(joined, mapping)
    if not edits:
        return p_xml, 0, manual
    owner, offsets, pos = [], [], 0
    for i, (_, t, _) in enumerate(pieces):
        offsets.append(pos)
        pos += len(t)
    applied = 0
    by_run = {}
    for s, e, new in edits:
        i = max(k for k in range(len(pieces)) if offsets[k] <= s)
        if e > offsets[i] + len(pieces[i][1]) or not pieces[i][2]:
            manual.append((joined[s:e], 'renvoi réparti sur plusieurs segments : à corriger à la main'))
            continue
        by_run.setdefault(i, []).append((s - offsets[i], e - offsets[i], new))
    out, last = [], 0
    for i, (r, text, _) in enumerate(pieces):
        out.append(p_xml[last:r.start()])
        last = r.end()
        if i not in by_run:
            out.append(r.group(0))
            continue
        body = r.group(0)
        rpr = _RPR.search(body)
        rpr = rpr.group(0) if rpr else ''
        open_tag = re.match(r'<w:r\b[^>]*>', body).group(0)

        def run(t, deleted=False):
            tag = 'w:delText' if deleted else 'w:t'
            return '%s%s<%s xml:space="preserve">%s</%s></w:r>' % (open_tag, rpr, tag, html.escape(t, quote=False), tag)
        cursor = 0
        for s, e, new in sorted(by_run[i]):
            if s > cursor:
                out.append(run(text[cursor:s]))
            counter[0] += 1
            out.append('<w:del w:id="%d" w:author="%s" w:date="%s">%s</w:del>' % (counter[0], author, stamp, run(text[s:e], True)))
            counter[0] += 1
            out.append('<w:ins w:id="%d" w:author="%s" w:date="%s">%s</w:ins>' % (counter[0], author, stamp, run(new)))
            cursor = e
            applied += 1
        if cursor < len(text):
            out.append(run(text[cursor:]))
    out.append(p_xml[last:])
    return ''.join(out), applied, manual


def track_renumbering(docx, mapping, author='AxiorHub'):
    """Conclusions DOCX → nouvelle version avec les renvois renumérotés en suivi de modifications. Renvoie (octets, appliqués, à revoir)."""
    mapping = {str(k): (None if v is None else int(v)) for k, v in mapping.items()}
    stamp = datetime.now(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')
    src = zipfile.ZipFile(io.BytesIO(docx))
    buf = io.BytesIO()
    counter = [90000]
    total, manual = 0, []
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == 'word/document.xml':
                xml = data.decode('utf-8')

                def para(m):
                    nonlocal total
                    new, n, man = _tracked_paragraph(m.group(0), mapping, counter, author, stamp)
                    total += n
                    manual.extend(man)
                    return new
                data = _PARA.sub(para, xml).encode('utf-8')
            out.writestr(info, data)
    return buf.getvalue(), total, manual


def compose(previous, plan):
    """Renumérotation cumulée {numéro d'origine (texte): numéro actuel, ou None si la pièce a été retirée}."""
    step = {int(m['old']): int(m['new']) for m in plan.get('mapping', []) if m.get('old') is not None}
    for r in plan.get('removed', []):
        step[int(r)] = None
    out, tracked = {}, set()
    for orig, cur in (previous or {}).items():
        if cur is None:
            out[str(orig)] = None
            continue
        tracked.add(int(cur))
        out[str(orig)] = step.get(int(cur), int(cur))
    for cur, new in step.items():
        if cur not in tracked and str(cur) not in out:
            out[str(cur)] = new
    return out


# ================================================================================================== tampon importé
def _png_decode(raw):
    """PNG 8 bits non entrelacé → (largeur, hauteur, composantes couleur, octets couleur, octets alpha ou None)."""
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise Stop('image_tampon_invalide')
    pos, idat, palette, trns = 8, b'', None, None
    width = height = depth = ctype = interlace = 0
    while pos < len(raw):
        length = struct.unpack('>I', raw[pos:pos + 4])[0]
        kind = raw[pos + 4:pos + 8]
        chunk = raw[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            width, height, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', chunk)
        elif kind == b'IDAT':
            idat += chunk
        elif kind == b'PLTE':
            palette = chunk
        elif kind == b'tRNS':
            trns = chunk
        elif kind == b'IEND':
            break
        pos += 12 + length
    if depth != 8 or interlace or ctype not in (0, 2, 3, 4, 6) or not width or width * height > 4_000_000:
        raise Stop('image_tampon_format_non_gere')
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    data = zlib.decompress(idat)
    stride = width * channels
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(height):
        f = data[i]
        line = bytearray(data[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        rows.append(bytes(line))
        prev = line
    pixels = b''.join(rows)
    if ctype == 3:
        if not palette:
            raise Stop('image_tampon_invalide')
        color = b''.join(palette[k * 3:k * 3 + 3] for k in pixels)
        alpha = bytes(trns[k] if trns and k < len(trns) else 255 for k in pixels) if trns else None
        return width, height, 3, color, alpha
    if ctype in (0, 2):
        return width, height, channels, pixels, None
    color_ch = channels - 1
    color = bytearray()
    alpha = bytearray()
    for k in range(0, len(pixels), channels):
        color += pixels[k:k + color_ch]
        alpha.append(pixels[k + color_ch])
    return width, height, color_ch, bytes(color), bytes(alpha)


def _jpeg_size(raw):
    i = 2
    while i < len(raw) - 9:
        if raw[i] != 0xFF:
            i += 1
            continue
        if raw[i + 1] in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(raw[i + 7:i + 9], 'big'), int.from_bytes(raw[i + 5:i + 7], 'big'), raw[i + 9]
        i += 2 + int.from_bytes(raw[i + 2:i + 4], 'big')
    raise Stop('image_tampon_invalide')


def stamp_image_path(desk):
    folder = Path(desk.c['state_dir']) / 'pieces510'
    for name in ('tampon.png', 'tampon.jpg'):
        if (folder / name).is_file():
            return folder / name
    return None


def save_stamp_image(desk, raw):
    if not raw or len(raw) > 1_500_000:
        raise Stop('image_tampon_invalide')
    if raw.startswith(b'\x89PNG'):
        _png_decode(raw)
        name = 'tampon.png'
    elif raw.startswith(b'\xff\xd8'):
        _jpeg_size(raw)
        name = 'tampon.jpg'
    else:
        raise Stop('image_tampon_invalide')
    folder = Path(desk.c['state_dir']) / 'pieces510'
    folder.mkdir(parents=True, exist_ok=True)
    for old in ('tampon.png', 'tampon.jpg'):
        if (folder / old).exists():
            (folder / old).unlink()
    tmp = folder / '.tampon.tmp'
    tmp.write_bytes(raw)
    tmp.replace(folder / name)
    desk.audit('pieces_520_tampon_importe', {'format': name[-3:], 'sha256': hashlib.sha256(raw).hexdigest()[:16]})
    return {'image': name}


def remove_stamp_image(desk):
    path = stamp_image_path(desk)
    if path:
        path.unlink()
    return {'image': ''}


def image_object(raw):
    """Objets PDF (dictionnaire + flux) pour l'image du tampon : (corps image, corps masque ou None, largeur, hauteur)."""
    if raw.startswith(b'\xff\xd8'):
        w, h, comps = _jpeg_size(raw)
        space = b'/DeviceGray' if comps == 1 else (b'/DeviceCMYK' if comps == 4 else b'/DeviceRGB')
        body = (b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace ' % (w, h) + space +
                b' /BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n' % len(raw) + raw + b'\nendstream')
        return body, None, w, h
    w, h, ch, color, alpha = _png_decode(raw)
    data = zlib.compress(color)
    space = b'/DeviceGray' if ch == 1 else b'/DeviceRGB'
    smask = None
    if alpha is not None:
        a = zlib.compress(alpha)
        smask = (b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length %d >>\nstream\n'
                 % (w, h, len(a)) + a + b'\nendstream')
    body = (b'<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace ' % (w, h) + space +
            b' /BitsPerComponent 8 /Filter /FlateDecode%s /Length %d >>\nstream\n' % (b' /SMask 8 0 R' if smask else b'', len(data)) + data + b'\nendstream')
    return body, smask, w, h


# ================================================================================================== dépôt
def safe_filename(text, maximum=60):
    value = fold(str(text))
    value = re.sub(r'[^a-z0-9]+', '_', value).strip('_')
    return (value[:maximum].rstrip('_') or 'piece')


def _split_by_size(raw, limit):
    """Découpe un PDF en parties de pages consécutives ≤ limite. [(première page, dernière page, octets)] ; Stop si une page dépasse seule."""
    from . import pieces510 as p5
    pypdf = p5._pdf_lib()
    reader = p5.open_pdf(raw)
    total = len(reader.pages)
    parts, start = [], 0

    def build(a, b):
        w = pypdf.PdfWriter()
        for k in range(a, b):
            w.add_page(reader.pages[k])
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()
    while start < total:
        lo, hi, best = start + 1, total, None
        while lo <= hi:
            mid = (lo + hi) // 2
            data = build(start, mid)
            if len(data) <= limit:
                best = (mid, data)
                lo = mid + 1
            else:
                hi = mid - 1
        if not best:
            raise Stop('page_plus_lourde_que_la_limite')
        parts.append((start + 1, best[0], best[1]))
        start = best[0]
    return parts


def plan_deposit(files, limit_mb):
    """files : [(n° ou None pour le bordereau, intitulé, octets)] → envois [[(nom, octets)]], alertes."""
    limit = int(limit_mb * 1_000_000)
    units, warnings = [], []
    for number, title, raw in files:
        prefix = 'Bordereau' if number is None else 'P%02d' % number
        base = prefix + ('_' + safe_filename(title, 50) if number is not None else '')
        if len(raw) <= limit:
            units.append((base + '.pdf', raw))
            continue
        try:
            parts = _split_by_size(raw, limit)
        except Stop:
            warnings.append('%s : une page dépasse à elle seule %g Mo ; à transmettre par e-Partage.' % (prefix, limit_mb))
            continue
        for k, (a, b, data) in enumerate(parts, 1):
            units.append(('%s_partie%d_p%d-%d.pdf' % (base, k, a, b), data))
        warnings.append('%s découpée en %d parties (pages consécutives) pour rester sous %g Mo par envoi.' % (prefix, len(parts), limit_mb))
    envois, current, size = [], [], 0
    for name, raw in units:
        if current and size + len(raw) > limit:
            envois.append(current)
            current, size = [], 0
        current.append((name, raw))
        size += len(raw)
    if current:
        envois.append(current)
    return envois, warnings


def deposit_readme(envois, warnings, limit_mb):
    lines = ['Dépôt préparé par AxiorHub — %d envoi(s) de %g Mo au plus.' % (len(envois), limit_mb), '', DEPOSIT_NOTE, '']
    for i, env in enumerate(envois, 1):
        lines.append('Envoi %d (%.1f Mo) :' % (i, sum(len(r) for _, r in env) / 1e6))
        lines += ['  - %s' % n for n, _ in env]
    if warnings:
        lines += ['', 'À noter :'] + ['  - ' + w for w in warnings]
    return '\n'.join(lines) + '\n'


# ================================================================================================== bordereau adverse
_LINE = re.compile(r'^\s*pi[eè]ces?\s*(?:n\s*[°o]\s*\.?\s*)?(\d{1,3})\s*[:\-–.)]\s*(.+?)\s*$', re.I | re.M)


def parse_bordereau(text):
    out, seen = [], set()
    for m in _LINE.finditer(text or ''):
        n = int(m.group(1))
        if n in seen:
            continue
        seen.add(n)
        out.append({'number': n, 'title': re.sub(r'\s+', ' ', m.group(2)).strip()[:300]})
    return out


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]{3,}', fold(text)) if w not in {'piece', 'pieces', 'les', 'des', 'du', 'pdf', 'copie'}}


def compare_adverse(announced, received, cited=None):
    """Rapproche bordereau adverse ↔ fichiers reçus ↔ conclusions adverses."""
    from . import pieces510 as p5
    by_number = {}
    for item in received:
        n = p5.file_number(PurePosixPath(item['path']).name)
        if n is not None:
            by_number.setdefault(n, item)
    matched, missing, used = [], [], set()
    for a in announced:
        hit = by_number.get(a['number'])
        how = 'numéro'
        if not hit:
            words = _words(a['title'])
            best, score = None, 0.0
            for item in received:
                if item['path'] in used:
                    continue
                w = _words(PurePosixPath(item['path']).stem)
                s = len(words & w) / len(words) if words else 0
                if s > score:
                    best, score = item, s
            hit = best if score >= 0.5 else None
            how = 'intitulé'
        if hit:
            used.add(hit['path'])
            matched.append({**a, 'file': hit['path'], 'how': how})
        else:
            missing.append(a)
    extra = [i['path'] for i in received if i['path'] not in used]
    numbers = {a['number'] for a in announced}
    cited_absent = sorted(n for n in set(cited or []) if n not in numbers)
    return {'matched': matched, 'missing': missing, 'extra': extra, 'cited_absent': cited_absent}


def adverse_check(desk, matter_id, bordereau_path, folder='', conclusions_path=''):
    """Lit le bordereau adverse (PDF ou Word, OCR si numérisé), le rapproche des pièces reçues et des conclusions adverses."""
    from . import pieces510 as p5
    from .common import clean_path, under
    matter = p5._matter(desk, matter_id)
    client = p5._dav(desk, p5.options(desk))

    def inside(path):
        path = clean_path(path)
        if not under(path, matter['path']):
            raise Stop('dossier_hors_racines')
        return path

    def text_of(path):
        raw = client.download({**client.stat(path), 'path': path})
        if path.lower().endswith('.pdf'):
            reader = p5.open_pdf(raw)
            text = '\n'.join((pg.extract_text() or '') for pg in reader.pages[:40])
            if len(text.strip()) < 60:
                text = ocr_text(raw, min(5, len(reader.pages)))
            return text
        from .documents import extract
        return extract(raw, PurePosixPath(path).name, {**desk.c.get('documents', {}), 'max_document_chars': 2_000_000, 'max_file_bytes': 30_000_000})
    path = inside(bordereau_path)
    announced = parse_bordereau(text_of(path))
    if not announced:
        raise Stop('bordereau_adverse_illisible')
    base = inside(folder) if folder else str(PurePosixPath(path).parent)
    received = [x for x in client.inventory(base) if not x.get('directory') and x['path'] != path
                and PurePosixPath(x['path']).suffix.lower() in ('.pdf', '.jpg', '.jpeg', '.png', '.docx', '.odt')]
    cited = []
    if conclusions_path:
        cited = sorted({n for n, _, _ in p5.citations(text_of(inside(conclusions_path)))})
    result = compare_adverse(announced, received, cited)
    result.update({'bordereau': path, 'folder': base, 'conclusions': conclusions_path, 'announced': len(announced), 'at': now()})
    desk.setting('pieces520:adverse:' + matter['id'], result)
    desk.audit('pieces_520_bordereau_adverse', {'matter': matter['id'], 'announced': len(announced), 'missing': len(result['missing'])})
    return result

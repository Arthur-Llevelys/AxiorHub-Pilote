"""5.6.77 : texte caché dans les PDF (pièces adverses piégées), en complément du bouclier 5.6.59.

Un PDF peut porter un texte qu'un humain ne voit pas mais que l'IA lirait : texte blanc sur fond blanc, police d'au plus 2 points,
texte placé hors de la page, texte en mode de rendu invisible (``3 Tr`` ou ``7 Tr``) ou totalement transparent. Le bouclier traitait
déjà le texte masqué des courriels HTML et de Word ; ce module fait de même pour les PDF.

Fonctionnement (déterministe, local, sans OCR ni réseau) :
1. chaque page est lue opérateur par opérateur (pypdf, déjà embarqué) en suivant l'état graphique : matrices, couleur de
   remplissage, transparence (``gs``), mode de rendu, taille de police ;
2. chaque affichage de texte reçoit un motif quand il est invisible pour un lecteur :
   - ``invisible`` (mode de rendu 3 ou 7) et ``transparent`` (opacité nulle), sauf **couche OCR légitime** : texte posé sur une
     image qui couvre au moins 30 % de la page (PDF numérisé puis reconnu) ;
   - ``minuscule`` : taille effective d'au plus 2 points ;
   - ``hors_page`` : origine du texte à plus de 5 points en dehors de la page ;
   - ``blanc`` : remplissage blanc ou presque, hors d'une zone peinte d'une autre couleur ou d'une image (un titre blanc sur un
     bandeau bleu reste lisible et n'est pas signalé) ;
3. le texte de ces seuls affichages est extrait par pypdf (même décodage des polices que pour le reste) ;
4. ``scrub`` le retire du texte de la page quand il s'y retrouve et ajoute la ligne « [AxiorHub : texte masqué retiré du PDF…] »
   (``bouclier5659.hidden_marker``) : le bouclier lève alors son alerte, élevée si ce texte s'adresse à une IA.

Limites assumées : le texte d'un formulaire XObject (``Do``) n'est pas examiné ; un texte blanc sur un fond peint d'une manière
que ce module ne suit pas (motif, ombrage) peut être signalé à tort, ce qui n'a d'autre effet qu'une ligne d'information.
Toute erreur de lecture laisse l'extraction inchangée : ce contrôle ne bloque jamais la lecture d'une pièce.
"""
import io
import math
import re
import time

REASONS = {'invisible': 'texte invisible', 'transparent': 'texte transparent', 'minuscule': 'police d’au plus 2 points',
           'hors_page': 'texte hors de la page', 'blanc': 'texte blanc sur fond blanc'}
TINY = 2.0
MARGIN = 5.0
OCR_COVER = 0.30
WHITE = 0.95
SHOW = (b'Tj', b'TJ', b"'", b'"')
PAINT = (b'f', b'F', b'f*', b'B', b'B*', b'b', b'b*')
ENDPATH = PAINT + (b'n', b'S', b's')


def _mult(a, b):
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _point(m, x, y):
    return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])


def _nums(operands):
    out = []
    for x in operands:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            return None
    return out


def _white(space, values):
    if values is None:
        return False
    if space == 'gray' and len(values) == 1:
        return values[0] >= WHITE
    if space == 'rgb' and len(values) == 3:
        return min(values) >= WHITE
    if space == 'cmyk' and len(values) == 4:
        return max(values) <= 1 - WHITE
    return False


def _inside(box, x, y):
    return box[0] - 0.5 <= x <= box[2] + 0.5 and box[1] - 0.5 <= y <= box[3] + 0.5


def _bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _page_box(page):
    try:
        box = page.cropbox
        return (float(box.left), float(box.bottom), float(box.right), float(box.top))
    except Exception:  # noqa: BLE001 - boîte absente ou illisible : format A4
        return (0.0, 0.0, 595.0, 842.0)


def _resource(page, kind, name):
    try:
        res = page.get('/Resources')
        res = res.get_object() if res is not None else None
        group = res.get(kind).get_object() if res is not None and res.get(kind) is not None else None
        return group[name].get_object() if group is not None and name in group else None
    except Exception:  # noqa: BLE001
        return None


def classify(page, operations):
    """[(index de l'opération, motif)] des affichages de texte invisibles d'une page, et les zones peintes ou imagées."""
    box = _page_box(page)
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    painted, images, path = [], [], []
    # premier passage : zones peintes d'une couleur autre que blanche et images (l'ordre d'affichage n'importe pas ici)
    ctm, stack, fill = [1, 0, 0, 1, 0, 0], [], ('gray', [0.0])
    space = 'gray'
    for operands, op in operations:
        if op == b'q':
            stack.append((list(ctm), fill, space))
        elif op == b'Q' and stack:
            ctm, fill, space = stack.pop()
        elif op == b'cm':
            n = _nums(operands)
            if n and len(n) == 6:
                ctm = _mult(n, ctm)
        elif op in (b'cs',):
            space = {'/DeviceGray': 'gray', '/DeviceRGB': 'rgb', '/DeviceCMYK': 'cmyk'}.get(str(operands[0]) if operands else '', 'autre')
            fill = (space, None)
        elif op == b'g':
            fill, space = ('gray', _nums(operands)), 'gray'
        elif op == b'rg':
            fill, space = ('rgb', _nums(operands)), 'rgb'
        elif op == b'k':
            fill, space = ('cmyk', _nums(operands)), 'cmyk'
        elif op in (b'sc', b'scn'):
            fill = (space, _nums(operands))
        elif op == b're':
            n = _nums(operands)
            if n and len(n) == 4:
                x, y, w, h = n
                path += [_point(ctm, x, y), _point(ctm, x + w, y), _point(ctm, x, y + h), _point(ctm, x + w, y + h)]
        elif op in (b'm', b'l', b'c', b'v', b'y'):
            n = _nums(operands)
            if n and len(n) % 2 == 0:
                path += [_point(ctm, n[i], n[i + 1]) for i in range(0, len(n), 2)]
        elif op in ENDPATH:
            if op in PAINT and path and not _white(*fill):
                painted.append(_bbox(path))
            path = []
        elif op == b'Do' and operands:
            xobj = _resource(page, '/XObject', operands[0])
            if xobj is not None and xobj.get('/Subtype') == '/Image':
                images.append(_bbox([_point(ctm, 0, 0), _point(ctm, 1, 0), _point(ctm, 0, 1), _point(ctm, 1, 1)]))
        elif op == b'INLINE IMAGE':
            images.append(_bbox([_point(ctm, 0, 0), _point(ctm, 1, 0), _point(ctm, 0, 1), _point(ctm, 1, 1)]))
    covers = [b for b in images if (b[2] - b[0]) * (b[3] - b[1]) >= OCR_COVER * area]
    # second passage : chaque affichage de texte
    found = []
    ctm, stack, fill, space = [1, 0, 0, 1, 0, 0], [], ('gray', [0.0]), 'gray'
    tr, alpha, size, leading = 0, 1.0, 12.0, 0.0
    tm = tlm = [1, 0, 0, 1, 0, 0]
    for i, (operands, op) in enumerate(operations):
        if op == b'q':
            stack.append((list(ctm), fill, space, tr, alpha, size, leading))
        elif op == b'Q' and stack:
            ctm, fill, space, tr, alpha, size, leading = stack.pop()
        elif op == b'cm':
            n = _nums(operands)
            if n and len(n) == 6:
                ctm = _mult(n, ctm)
        elif op == b'cs':
            space = {'/DeviceGray': 'gray', '/DeviceRGB': 'rgb', '/DeviceCMYK': 'cmyk'}.get(str(operands[0]) if operands else '', 'autre')
            fill = (space, None)
        elif op == b'g':
            fill, space = ('gray', _nums(operands)), 'gray'
        elif op == b'rg':
            fill, space = ('rgb', _nums(operands)), 'rgb'
        elif op == b'k':
            fill, space = ('cmyk', _nums(operands)), 'cmyk'
        elif op in (b'sc', b'scn'):
            fill = (space, _nums(operands))
        elif op == b'gs' and operands:
            state = _resource(page, '/ExtGState', operands[0])
            if state is not None and state.get('/ca') is not None:
                try:
                    alpha = float(state['/ca'])
                except (TypeError, ValueError):
                    pass
        elif op == b'Tr':
            n = _nums(operands)
            tr = int(n[0]) if n else tr
        elif op == b'Tf':
            n = _nums(operands[1:2])
            size = n[0] if n else size
        elif op == b'TL':
            n = _nums(operands)
            leading = n[0] if n else leading
        elif op == b'BT':
            tm = tlm = [1, 0, 0, 1, 0, 0]
        elif op in (b'Td', b'TD'):
            n = _nums(operands)
            if n and len(n) == 2:
                if op == b'TD':
                    leading = -n[1]
                tm = tlm = _mult([1, 0, 0, 1, n[0], n[1]], tlm)
        elif op == b'Tm':
            n = _nums(operands)
            if n and len(n) == 6:
                tm = tlm = n
        elif op == b'T*':
            tm = tlm = _mult([1, 0, 0, 1, 0, -leading], tlm)
        if op in (b"'", b'"'):
            tm = tlm = _mult([1, 0, 0, 1, 0, -leading], tlm)
        if op not in SHOW:
            continue
        full = _mult(tm, ctm)
        x, y = full[4], full[5]
        effective = abs(size) * math.hypot(full[2], full[3])
        over_image = any(_inside(b, x, y) for b in covers)
        reason = ''
        if tr in (3, 7) and not over_image:
            reason = 'invisible'
        elif alpha <= 0.01 and tr not in (3, 7) and not over_image:
            reason = 'transparent'
        elif 0 < effective <= TINY and tr not in (3, 7):
            reason = 'minuscule'
        elif x < box[0] - MARGIN or x > box[2] + MARGIN or y < box[1] - MARGIN or y > box[3] + MARGIN:
            reason = 'hors_page'
        elif tr in (0, 2, 4, 6) and _white(*fill) and not any(_inside(b, x, y) for b in painted + images):
            reason = 'blanc'
        if reason:
            found.append((i, reason))
    return found


def hidden_pages(raw, max_pages=400, budget=20.0):
    """[{page, motifs, texte}] des pages d'un PDF qui portent du texte caché. Jamais d'exception : [] en cas de doute."""
    started = time.monotonic()
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            return []
        from pypdf.generic import ContentStream, NameObject
        pages = list(reader.pages)
    except Exception:  # noqa: BLE001 - PDF mal formé, chiffré ou inattendu : la lecture normale continue
        return []
    out = []
    for number, page in enumerate(pages, 1):
        if number > max_pages or time.monotonic() - started > budget:
            break
        try:
            contents = page.get_contents()
            if contents is None:
                continue
            # « bytes » : les chaînes restent des octets, comme dans l'extraction de pypdf (polices sous-ensembles)
            ops = ContentStream(contents, reader, 'bytes').operations
            found = classify(page, ops)
            if not found:
                continue
            hidden = {i for i, _ in found}
            kept = ContentStream(None, reader, 'bytes')
            kept.operations = [o for i, o in enumerate(ops) if o[1] not in SHOW or i in hidden]
            page[NameObject('/Contents')] = kept
            text = re.sub(r'[ \t]+', ' ', page.extract_text() or '').strip()
        except Exception:  # noqa: BLE001 - page illisible : les autres pages sont examinées
            continue
        if len(re.sub(r'\W', '', text)) < 3:
            continue
        out.append({'page': number, 'motifs': sorted({r for _, r in found}), 'texte': text})
    return out


def _remove(page_text, hidden):
    """Retire du texte de la page chaque ligne cachée retrouvée (espaces souples). Retourne (texte, retiré ?)."""
    removed = False
    for line in sorted({x.strip() for x in hidden.splitlines() if len(x.strip()) >= 3}, key=len, reverse=True):
        words = line.split()
        rx = re.compile(r'\s+'.join(re.escape(w) for w in words))
        page_text, n = rx.subn(' ', page_text, count=1)
        removed = removed or bool(n)
    return page_text, removed


def where(item):
    return 'du PDF, page %d : %s' % (item['page'], ', '.join(REASONS[m] for m in item['motifs']))


def scrub(raw, pages):
    """Texte des pages (liste dans l'ordre du PDF) sans le texte caché, chaque page concernée suivie de sa ligne de signalement."""
    from .bouclier5659 import hidden_marker
    pages = list(pages)
    for item in hidden_pages(raw, max_pages=len(pages)):
        n = item['page'] - 1
        if 0 <= n < len(pages):
            cleaned, _ = _remove(pages[n], item['texte'])
            pages[n] = cleaned.rstrip() + hidden_marker(item['texte'], where(item))
    return pages

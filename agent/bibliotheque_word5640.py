"""5.6.40 (audit 5.6.39, B02, B03, B04, B07) : moteur commun des trames de la bibliothèque, texte et Word.

- ``validate`` : un seul contrôle des clauses [[si …]] / [[sinon]] / [[fin]] pour le texte et pour le Word (ouverture, fermeture, sinon
  unique, imbrication, conditions en ligne, bloc qui franchirait une cellule de tableau) ; l'erreur indique le paragraphe en cause.
  Un modèle invalide est refusé à l'enregistrement ET à la préparation : jamais de document tronqué.
- ``word_keys`` : variables et clauses d'un modèle Word (corps, tableaux, en-têtes et pieds de page), balises coupées par Word en
  plusieurs fragments comprises.
- ``fill`` : remplissage d'un modèle Word fragment par fragment : seule la plage de la balise est remplacée, la valeur prend la mise en
  forme du fragment qui porte le nom de la variable ; préfixes, suffixes, liens, champs et signets restent en place.
- ``docx_text`` : texte d'un document Word préparé (corps, tableaux ligne par ligne, en-têtes et pieds), pour la rédaction IA.
"""
from html import unescape
import io
import re
import zipfile
from xml.sax.saxutils import escape as xml_escape

PARTS = re.compile(r'word/(document|header\d*|footer\d*)\.xml')
PARA = re.compile(r'<w:p[ >].*?</w:p>|<w:p/>', re.S)
TEXT = re.compile(r'<w:t(?: [^>]*)?>([^<]*)</w:t>')
CELL = re.compile(r'<w:tc[ >]|</w:tc>')
TAG = re.compile(r'\{\{\s*(\w+)\s*\}\}')
BLOCK = re.compile(r'\[\[si\s+(!?)(\w+)\]\]')
MARK = re.compile(r'\[\[(si\s+!?\w+|sinon|fin)\]\]')
ANY_MARK = re.compile(r'\[\[[^\[\]]*\]\]')

MESSAGES = {
    'bloc_non_ferme': 'la condition %s n’est pas fermée par [[fin]]',
    'fin_en_trop': '[[fin]] sans condition ouverte',
    'sinon_isole': '[[sinon]] sans condition ouverte',
    'sinon_repete': 'deux [[sinon]] dans la même condition',
    'bloc_traversant_tableau': 'la condition commence et se termine dans des cellules ou zones différentes du document',
    'condition_en_ligne_non_fermee': 'condition ouverte dans la ligne sans [[fin]] dans cette même ligne',
    'balise_condition_invalide': 'balise de condition mal écrite (%s) : utilisez [[si nom_de_clause]], [[sinon]] ou [[fin]]',
}


# ------------------------------------------------------------------------------------------------ validation commune
def _inline_error(text):
    for bad in ANY_MARK.finditer(text):
        if not MARK.fullmatch(bad.group(0)):
            return 'balise_condition_invalide', bad.group(0)
    depth, elses = 0, []
    for m in MARK.finditer(text):
        token = m.group(1)
        if re.match(r'si\s', token):
            depth += 1
            elses.append(False)
        elif token == 'sinon':
            if not depth:
                return 'sinon_isole', m.group(0)
            if elses[-1]:
                return 'sinon_repete', m.group(0)
            elses[-1] = True
        else:
            if not depth:
                return 'fin_en_trop', m.group(0)
            depth -= 1
            elses.pop()
    if depth:
        return 'condition_en_ligne_non_fermee', text.strip()[:60]
    return None


def validate(texts, containers=None):
    """Erreurs de structure des clauses : liste de (code, index du paragraphe, extrait). ``containers`` : zone de chaque paragraphe (corps,
    cellule de tableau…) ; un bloc doit s'ouvrir et se fermer dans la même zone."""
    errors, stack = [], []
    for i, raw in enumerate(texts):
        s = str(raw or '').strip()
        zone = containers[i] if containers else None
        m = BLOCK.fullmatch(s)
        if m:
            stack.append([m.group(0), i, zone, False])
            continue
        if s == '[[sinon]]':
            if not stack:
                errors.append(('sinon_isole', i, s))
            elif stack[-1][3]:
                errors.append(('sinon_repete', i, s))
            elif stack[-1][2] != zone:
                errors.append(('bloc_traversant_tableau', i, s))
            else:
                stack[-1][3] = True
            continue
        if s == '[[fin]]':
            if not stack:
                errors.append(('fin_en_trop', i, s))
            elif stack.pop()[2] != zone:
                errors.append(('bloc_traversant_tableau', i, s))
            continue
        err = _inline_error(s) if '[[' in s else None
        if err:
            errors.append((err[0], i, err[1]))
    for token, i, _, _ in stack:
        errors.append(('bloc_non_ferme', i, token))
    return errors


def describe(errors, unit='Paragraphe', limit=3):
    out = []
    for code, i, excerpt in errors[:limit]:
        text = MESSAGES.get(code, code)
        text = text % excerpt if '%s' in text else text
        out.append('%s %d (« %s ») : %s.' % (unit, i + 1, str(excerpt)[:60], text))
    if len(errors) > limit:
        out.append('%d autre(s) erreur(s).' % (len(errors) - limit))
    return ' '.join(out)


# ------------------------------------------------------------------------------------------------ conditions en ligne
def inline_deletions(text, flags):
    """Positions à retirer d'une ligne : balises de condition et branche non retenue (imbrication comprise)."""
    deleted = [False] * len(text)
    stack = []   # [actif_parent, condition, début_contenu, début_sinon]
    active = True
    pos = 0
    for m in MARK.finditer(text):
        token = m.group(1)
        # contenu entre la balise précédente et celle-ci : retiré si la branche courante est inactive
        if not active:
            for k in range(pos, m.start()):
                deleted[k] = True
        for k in range(m.start(), m.end()):
            deleted[k] = True
        if re.match(r'si\s', token):
            neg = token[2:].strip().startswith('!')
            key = token[2:].strip().lstrip('!')
            cond = bool(flags.get(key)) != neg
            stack.append([active, cond])
            active = active and cond
        elif token == 'sinon' and stack:
            parent, cond = stack[-1]
            active = parent and not cond
        elif token == 'fin' and stack:
            active = stack.pop()[0]
        pos = m.end()
    if not active:
        for k in range(pos, len(text)):
            deleted[k] = True
    return deleted


def apply_inline(text, flags):
    deleted = inline_deletions(text, flags)
    return ''.join(c for c, d in zip(text, deleted) if not d)


# ------------------------------------------------------------------------------------------------ paragraphes Word
def _text_of(p):
    return unescape(''.join(TEXT.findall(p)))


def paragraphs(xml):
    """Paragraphes d'une partie Word avec leur zone : -1 (corps) ou position de la cellule de tableau qui les contient."""
    events = [(m.start(), 'cell', m.group(0)) for m in CELL.finditer(xml)]
    paras = [(m.start(), m.end()) for m in PARA.finditer(xml)]
    out, stack, k = [], [], 0
    events.sort()
    for start, end in paras:
        while k < len(events) and events[k][0] < start:
            pos, _, token = events[k]
            if token.startswith('</'):
                if stack:
                    stack.pop()
            else:
                stack.append(pos)
            k += 1
        p = xml[start:end]
        out.append({'start': start, 'end': end, 'xml': p, 'text': _text_of(p), 'zone': stack[-1] if stack else -1})
    return out


def _parts(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            if PARTS.fullmatch(name):
                yield name, z.read(name).decode('utf-8')


def word_structure(raw):
    """(paragraphes de chaque partie, erreurs de structure décrites) d'un modèle Word."""
    errors = []
    found = []
    for name, xml in _parts(raw):
        paras = paragraphs(xml)
        found.append((name, paras))
        errs = validate([p['text'] for p in paras], [p['zone'] for p in paras])
        if errs:
            unit = 'Paragraphe' if name == 'word/document.xml' else ('En-tête, paragraphe' if 'header' in name else 'Pied de page, paragraphe')
            errors.append(describe(errs, unit))
    return found, ' '.join(errors)


def word_keys(raw):
    """Variables et clauses d'un modèle Word (corps, tableaux, en-têtes, pieds ; balises réparties sur plusieurs fragments)."""
    tags, clauses = [], []
    for _, xml in _parts(raw):
        for p in paragraphs(xml):
            for m in TAG.finditer(p['text']):
                if m.group(1) not in tags:
                    tags.append(m.group(1))
            for m in MARK.finditer(p['text']):
                token = m.group(1)
                if re.match(r'si\s', token):
                    key = token[2:].strip().lstrip('!')
                    if key not in clauses and not key.startswith('aide'):
                        clauses.append(key)
    return tags, clauses


# ------------------------------------------------------------------------------------------------ remplissage fragment par fragment
def _edit_paragraph(p, deleted, inserts):
    """Applique au paragraphe des suppressions (par caractère du texte joint) et des insertions (index → texte), chaque insertion prenant
    place dans le fragment qui porte le caractère d'ancrage. Les éléments hors <w:t> (liens, champs, signets) ne sont pas touchés."""
    matches = list(TEXT.finditer(p))
    if not matches:
        return p
    texts = [unescape(m.group(1)) for m in matches]
    owner = []
    for n, t in enumerate(texts):
        owner.extend([n] * len(t))
    total = len(owner)
    new = [''] * len(texts)
    flat = ''.join(texts)
    for i in range(total + 1):
        if i in inserts:
            seg = owner[i] if i < total else len(texts) - 1
            new[seg] += inserts[i]
        if i < total and not deleted[i]:
            new[owner[i]] += flat[i]
    out, last = [], 0
    for m, value in zip(matches, new):
        out.append(p[last:m.start()])
        out.append('<w:t xml:space="preserve">%s</w:t>' % xml_escape(value).replace('\n', '</w:t><w:br/><w:t xml:space="preserve">'))
        last = m.end()
    out.append(p[last:])
    return ''.join(out)


def fill(raw, flags, value_of, article_label):
    """Modèle Word rempli : (octets du document, titres des articles « ## »). ``value_of(nom)`` donne la valeur d'une variable (balises
    imbriquées déjà résolues) ; ``article_label(n, titre)`` le libellé numéroté. Structure validée au préalable : une structure invalide
    lève ValueError (aucun document produit)."""
    found, problems = word_structure(raw)
    if problems:
        raise ValueError(problems)
    sections = []
    src, dst = io.BytesIO(raw), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if PARTS.fullmatch(info.filename):
                xml = data.decode('utf-8')
                paras = paragraphs(xml)
                keep, stack, active, numbered = [], [], True, 0
                for p in paras:
                    s = p['text'].strip()
                    b = BLOCK.fullmatch(s)
                    if b:
                        cond = bool(flags.get(b.group(2))) != bool(b.group(1))
                        stack.append([active, cond])
                        active = active and cond
                        keep.append(None)
                        continue
                    if s == '[[sinon]]':
                        parent, cond = stack[-1]
                        active = parent and not cond
                        keep.append(None)
                        continue
                    if s == '[[fin]]':
                        active = stack.pop()[0]
                        keep.append(None)
                        continue
                    if not active:
                        keep.append(None)
                        continue
                    text = p['text']
                    if '{{' not in text and '[[' not in text and not s.startswith('## '):
                        keep.append(p['xml'])
                        continue
                    deleted = inline_deletions(text, flags)
                    inserts = {}
                    visible = [i for i, d in enumerate(deleted) if not d]
                    vtext = ''.join(text[i] for i in visible)
                    lead = len(vtext) - len(vtext.lstrip())
                    if vtext[lead:].startswith('## '):
                        numbered += 1
                        title = TAG.sub(lambda m: value_of(m.group(1)), vtext[lead + 3:]).strip()
                        sections.append(title)
                        for k in range(lead, lead + 3):
                            deleted[visible[k]] = True
                        inserts[visible[lead]] = article_label(numbered, '').rstrip()
                        # le titre lui-même reste à sa place (balises remplacées ci-dessous)
                        rest = visible[lead + 3:]
                        if rest and text[rest[0]] != ' ':
                            inserts[visible[lead]] += ' '
                    for m in TAG.finditer(vtext):
                        idx = visible[m.start():m.end()]
                        for k in idx:
                            deleted[k] = True
                        name_at = vtext.index(m.group(1), m.start())
                        anchor = visible[name_at]
                        inserts[anchor] = inserts.get(anchor, '') + value_of(m.group(1))
                    keep.append(_edit_paragraph(p['xml'], deleted, inserts))
                # reconstruction : paragraphes retirés ; une cellule de tableau garde toujours un paragraphe (sinon Word refuse le fichier)
                zones = {}
                for p, k in zip(paras, keep):
                    if p['zone'] != -1:
                        zones.setdefault(p['zone'], []).append(k is not None)
                emptied = {z for z, flags_ in zones.items() if not any(flags_)}
                out, last, placed = [], 0, set()
                for p, k in zip(paras, keep):
                    out.append(xml[last:p['start']])
                    if k is not None:
                        out.append(k)
                    elif p['zone'] in emptied and p['zone'] not in placed:
                        out.append('<w:p/>')
                        placed.add(p['zone'])
                    last = p['end']
                out.append(xml[last:])
                data = ''.join(out).encode('utf-8')
            zout.writestr(info, data)
    return dst.getvalue(), sections


# ------------------------------------------------------------------------------------------------ texte d'un document préparé
def docx_text(raw):
    """Texte d'un document Word : corps (tableaux ligne par ligne, cellules séparées par « | »), puis en-têtes et pieds de page."""
    lines, extra = [], []
    for name, xml in _parts(raw):
        target = lines if name == 'word/document.xml' else extra
        body = xml
        pos = 0
        for t in re.finditer(r'<w:tbl>.*?</w:tbl>', body, flags=re.S):
            target.extend(x for x in (_text_of(p.group(0)).strip() for p in PARA.finditer(body[pos:t.start()])) if x)
            for row in re.findall(r'<w:tr[ >].*?</w:tr>', t.group(0), flags=re.S):
                cells = [' / '.join(x for x in (_text_of(p).strip() for p in PARA.findall(c)) if x)
                         for c in re.findall(r'<w:tc[ >].*?</w:tc>', row, flags=re.S)]
                if any(cells):
                    target.append(' | '.join(cells))
            pos = t.end()
        target.extend(x for x in (_text_of(p.group(0)).strip() for p in PARA.finditer(body[pos:])) if x)
    if extra:
        lines.append('')
        lines.extend('[En-tête ou pied de page] ' + x for x in extra)
    return '\n'.join(lines).strip()

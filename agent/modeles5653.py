"""5.6.53 (audit 5.6.52, § 4.2 et § 10) : modèles de documents d'honoraires (devis, lettre de mission, conventions, conditions
générales, annexe consommateur, facture, avoir) — huit modèles Word livrés et modèles importés par le cabinet.

- Chaque modèle a un original conservé, une version, un auteur, une date de vérification, un périmètre et ses variables ``{{...}}``.
- Import : vrai fichier Word (.docx) seulement ; refus des macros (vbaProject), des liens externes, des objets incorporés et des
  fichiers trop lourds ; aucune exécution ni ressource réseau pendant le rendu.
- Rendu : variables résolues par le SERVEUR depuis les fiches validées et le calcul retenu ; une variable sans valeur reste marquée
  « [à compléter : …] » et bloque l'émission ; la ligne de tableau qui porte ``{{libelle}}`` est répétée pour chaque ligne du
  document ; le logo est une image contrôlée (PNG ou JPEG) insérée à la place de ``{{logo}}``.
- Les modèles de facture et d'avoir sont des gabarits de RENDU : ils ne sont ni Factur-X ni UBL (voir ``facturx5653``).
"""
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import zipfile

from .common import Stop

DATA = Path(__file__).resolve().parent / 'data' / 'modeles5653'
KINDS = {'facture': 'Facture', 'devis': 'Devis', 'lettre_mission': 'Lettre de mission', 'convention_forfait': 'Convention au forfait',
         'convention_temps': 'Convention au temps passé', 'conditions_generales': 'Conditions générales d’honoraires',
         'annexe_consommateur': 'Information du client consommateur', 'avoir': 'Avoir'}
CATALOG_KIND = {'facture': 'facture', 'devis': 'devis', 'lettre_mission': 'lettre_mission', 'convention_forfait': 'convention_forfait',
                'convention_temps_passe': 'convention_temps', 'conditions_generales': 'conditions_generales', 'annexe_consommateur': 'annexe_consommateur',
                'avoir': 'avoir'}
VAR = re.compile(r'\{\{\s*([a-z0-9_]+)\s*\}\}')
LINE_VARS = ('libelle', 'qte', 'prix_ht', 'tva', 'montant_ht')
MAX_DOCX = 5 * 1024 * 1024
MAX_LOGO = 1024 * 1024
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
SCHEMA = '''CREATE TABLE IF NOT EXISTS b53_templates(id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, version TEXT NOT NULL,
  origin TEXT NOT NULL, file TEXT NOT NULL, sha256 TEXT NOT NULL, variables_json TEXT NOT NULL, required_json TEXT NOT NULL DEFAULT '[]',
  author TEXT NOT NULL, checked_at TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'cabinet', active INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);'''


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def _catalog():
    raw = json.loads((DATA / 'catalogue-modeles.json').read_text(encoding='utf-8'))
    out = []
    for item in raw:
        kind = CATALOG_KIND.get(item.get('type'), item.get('type'))
        out.append({'id': 'defaut:' + item['id'], 'kind': kind, 'title': item.get('title') or KINDS.get(kind, item['id']), 'version': item['version'], 'origin': 'defaut',
                    'presentation': item.get('presentation', ''), 'defaut': bool(item.get('defaut')),
                    'file': str(DATA / item['word']), 'markdown': str(DATA / item['markdown']), 'variables': item['variables'],
                    'line_variables': item.get('line_variables') or [], 'statut': item.get('statut'), 'author': 'AxiorHub (modèle de base)',
                    'checked_at': item['version'][:10], 'scope': 'cabinet', 'structured': bool(item.get('financial_structured_export_required'))})
    return out


def listing(desk, kind=''):
    ensure_schema(desk)
    items = _catalog()
    for r in desk.db.execute('SELECT * FROM b53_templates WHERE active=1 ORDER BY created DESC'):
        items.append({'id': r['id'], 'kind': r['kind'], 'title': r['title'], 'version': r['version'], 'origin': r['origin'], 'file': r['file'],
                      'markdown': '', 'variables': json.loads(r['variables_json']), 'line_variables': [v for v in LINE_VARS if v in json.loads(r['variables_json'])],
                      'author': r['author'], 'checked_at': r['checked_at'], 'scope': r['scope'], 'sha256': r['sha256']})
    return [t for t in items if not kind or t['kind'] == kind]


def get(desk, tid):
    for t in listing(desk):
        if t['id'] == tid:
            return t
    raise Stop('modele_absent')


def default_for(desk, kind):
    """Modèle choisi par le cabinet pour ce type (réglage), sinon le plus récent importé, sinon le modèle de base."""
    chosen = (desk.settings('modeles5653:choix', {}) or {}).get(kind)
    items = listing(desk, kind)
    for t in items:
        if t['id'] == chosen:
            return t
    imported = [t for t in items if t['origin'] == 'import']
    if imported:
        return imported[0]
    if not items:
        raise Stop('modele_absent')
    preferred = [t for t in items if t.get('defaut')]           # 5.6.57 (§ 7.5) : présentation sobre par défaut pour les factures
    return preferred[0] if preferred else items[0]


# ------------------------------------------------------------------------------------------------ import contrôlé
def inspect_docx(raw):
    """Contrôles d'un modèle importé ; retourne les variables détectées. Lève Stop avec un motif précis."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_DOCX:
        raise Stop('modele_trop_lourd')
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = z.namelist()
    except zipfile.BadZipFile:
        raise Stop('modele_word_requis') from None
    if 'word/document.xml' not in names:
        raise Stop('modele_word_requis')
    lowered = [n.lower() for n in names]
    if any('vbaproject' in n or n.endswith('.bin') for n in lowered):
        raise Stop('modele_avec_macros_refuse')
    if any(n.startswith('word/embeddings/') or n.startswith('word/activex/') for n in lowered):
        raise Stop('modele_objets_incorpores_refuses')
    total = sum(i.file_size for i in z.infolist())
    if total > 40 * 1024 * 1024 or any(i.file_size > 20 * 1024 * 1024 for i in z.infolist()):
        raise Stop('modele_trop_lourd')                       # protection contre les archives explosives
    for n in names:
        if n.endswith('.rels'):
            rel = z.read(n).decode('utf-8', 'replace')
            if 'TargetMode="External"' in rel and ('attachedTemplate' in rel or 'oleObject' in rel or 'frame' in rel or 'subDocument' in rel):
                raise Stop('modele_liens_externes_refuses')
    ctype = z.read('[Content_Types].xml').decode('utf-8', 'replace') if '[Content_Types].xml' in names else ''
    if 'macroEnabled' in ctype:
        raise Stop('modele_avec_macros_refuse')
    text = ''.join(_text_of(z.read(n).decode('utf-8', 'replace')) for n in names if re.fullmatch(r'word/(document|header\d*|footer\d*)\.xml', n))
    return sorted(set(VAR.findall(text)))


def _text_of(xml):
    return ''.join(re.findall(r'<w:t(?:\s[^>]*)?>([^<]*)</w:t>', xml))


def import_template(desk, actor, kind, title, raw, filename=''):
    ensure_schema(desk)
    if kind not in KINDS:
        raise Stop('type_modele_invalide')
    if not str(filename or '').lower().endswith('.docx'):
        raise Stop('modele_word_requis')
    variables = inspect_docx(raw)
    sha = hashlib.sha256(raw).hexdigest()
    folder = Path(desk.c['state_dir']) / 'modeles5653'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (sha[:24] + '.docx')
    if not target.exists():
        target.write_bytes(raw)                                # original conservé tel quel
    tid = 'import:' + sha[:16]
    existing = desk.db.execute('SELECT id FROM b53_templates WHERE id=?', (tid,)).fetchone()
    if existing:
        return get(desk, tid)
    version = datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.' + sha[:6]
    desk.db.execute('INSERT INTO b53_templates(id,kind,title,version,origin,file,sha256,variables_json,author,checked_at,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (tid, kind, str(title or KINDS[kind])[:120], version, 'import', str(target), sha, json.dumps(variables), actor,
                     datetime.now(timezone.utc).strftime('%Y-%m-%d'), _now()))
    desk.db.commit()
    return {**get(desk, tid), 'variables_detectees': variables}


def choose(desk, kind, tid):
    if kind not in KINDS:
        raise Stop('type_modele_invalide')
    t = get(desk, tid)
    if t['kind'] != kind:
        raise Stop('type_modele_invalide')
    choice = dict(desk.settings('modeles5653:choix', {}) or {})
    choice[kind] = tid
    desk.setting('modeles5653:choix', choice)
    return choice


# ------------------------------------------------------------------------------------------------ logo contrôlé
def check_logo(raw):
    """Image du logo : PNG ou JPEG seulement (un SVG peut porter du script), taille limitée ; dimensions lues dans l'en-tête."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_LOGO:
        raise Stop('logo_trop_lourd')
    if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) > 24:
        w, h = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
        return 'png', w, h
    if raw.startswith(b'\xff\xd8'):
        i = 2
        while i < len(raw) - 9:
            if raw[i] != 0xFF:
                break
            marker, length = raw[i + 1], int.from_bytes(raw[i + 2:i + 4], 'big')
            if marker in (0xC0, 0xC1, 0xC2):
                return 'jpeg', int.from_bytes(raw[i + 7:i + 9], 'big'), int.from_bytes(raw[i + 5:i + 7], 'big')
            i += 2 + length
        raise Stop('logo_illisible')
    raise Stop('logo_png_ou_jpeg_requis')


def save_logo(desk, raw):
    kind, w, h = check_logo(raw)
    if not (16 <= w <= 4000 and 16 <= h <= 4000):
        raise Stop('logo_illisible')
    folder = Path(desk.c['state_dir']) / 'modeles5653'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('logo.' + ('png' if kind == 'png' else 'jpg'))
    for old in folder.glob('logo.*'):
        old.unlink()
    target.write_bytes(raw)
    return {'type': kind, 'largeur': w, 'hauteur': h}


def logo(desk):
    folder = Path(desk.c['state_dir']) / 'modeles5653'
    for name in ('logo.png', 'logo.jpg'):
        if (folder / name).is_file():
            return (folder / name).read_bytes()
    return None


# ------------------------------------------------------------------------------------------------ rendu Word
def _esc(text):
    return html.escape(str(text), quote=False)


def _run_xml(rpr, text):
    parts = str(text).split('\n')
    inner = '<w:br/>'.join('<w:t xml:space="preserve">%s</w:t>' % _esc(p) for p in parts)
    return '<w:r>%s%s</w:r>' % (rpr, inner)


def missing_marker(name):
    return '[à compléter : %s]' % name.replace('_', ' ')


def _subst(text, values, missing):
    def rep(m):
        name = m.group(1)
        v = values.get(name)
        if v is None or (isinstance(v, str) and not v.strip() and name not in values.get('_vides_permis', ())):
            missing.add(name)
            return missing_marker(name)
        return str(v)
    return VAR.sub(rep, text)


def _paragraphs(xml, fn):
    return re.sub(r'<w:p(?:\s[^>]*)?>.*?</w:p>', lambda m: fn(m.group(0)), xml, flags=re.S)


TEMPLATE_NOTES = ('Modèle à compléter et à valider avant utilisation',)


def _rebuild_paragraph(p, values, missing, logo_xml=None):
    text = _text_of(p)
    if text.strip() in TEMPLATE_NOTES:                          # mention propre au modèle livré : absente du document rempli
        head = re.match(r'<w:p(?:\s[^>]*)?>', p).group(0)
        return head + '</w:p>'
    if '{{' not in text:
        return p
    ppr = re.search(r'<w:pPr>.*?</w:pPr>', p, re.S)
    rpr = re.search(r'<w:rPr>.*?</w:rPr>', p, re.S)
    head = re.match(r'<w:p(?:\s[^>]*)?>', p).group(0)
    if text.strip() == '{{logo}}':
        if logo_xml:
            return head + (ppr.group(0) if ppr else '') + logo_xml + '</w:p>'
        return head + (ppr.group(0) if ppr else '') + '</w:p>'
    new = _subst(text, values, missing)
    if not new.strip():                                        # variable sans objet : paragraphe vidé (une cellule garde son paragraphe)
        return head + (ppr.group(0) if ppr else '') + '</w:p>'
    return head + (ppr.group(0) if ppr else '') + _run_xml(rpr.group(0) if rpr else '', new) + '</w:p>'


def _line_rows(xml, lines, missing):
    rows = list(re.finditer(r'<w:tr(?:\s[^>]*)?>(?:(?!</w:tr>).)*\{\{\s*libelle\s*\}\}(?:(?!</w:tr>).)*</w:tr>', xml, re.S))
    if not rows:
        rows = [m for m in re.finditer(r'<w:tr(?:\s[^>]*)?>.*?</w:tr>', xml, re.S) if 'libelle' in _text_of(m.group(0))]
    for m in reversed(rows):
        tpl = m.group(0)
        out = ''.join(_paragraphs(tpl, lambda p, line=line: _rebuild_paragraph(p, line, missing)) for line in lines)
        xml = xml[:m.start()] + out + xml[m.end():]
    return xml


def _logo_xml(rid, w, h):
    cx = 1_800_000                                             # 5 cm de large ; hauteur proportionnelle
    cy = int(cx * h / w) if w else cx
    return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="%d" cy="%d"/><wp:docPr id="5301" name="Logo"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="5301" name="logo"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>') % (cx, cy, rid, cx, cy)


def render_docx(template, values, lines=(), logo_bytes=None, draft=True):
    """Document Word rempli. Retourne (octets, variables manquantes). Un brouillon porte la mention « BROUILLON » en tête."""
    raw = Path(template['file']).read_bytes()
    zin = zipfile.ZipFile(io.BytesIO(raw))
    names = set(zin.namelist())
    missing = set()
    out = io.BytesIO()
    logo_xml, logo_part = None, None
    if logo_bytes:
        kind, w, h = check_logo(logo_bytes)
        logo_part = ('word/media/logo5653.' + ('png' if kind == 'png' else 'jpeg'), kind)
        logo_xml = _logo_xml('rIdLogo5653', w, h)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename
            if re.fullmatch(r'word/(document|header\d*|footer\d*)\.xml', name):
                xml = data.decode('utf-8')
                if name == 'word/document.xml':
                    xml = _line_rows(xml, lines, missing)
                    if draft:
                        xml = re.sub(r'(<w:body>)', r'\1<w:p><w:r><w:rPr><w:b/><w:color w:val="B91C1C"/></w:rPr><w:t>BROUILLON — document non émis, '
                                                    r'à relire et approuver</w:t></w:r></w:p>', xml, count=1)
                if name.startswith('word/footer'):
                    xml = re.sub(r'Modèle \d{4} \d{2}', _esc(values.get('pied_de_page') or ''), xml)
                xml = _paragraphs(xml, lambda p: _rebuild_paragraph(p, values, missing, logo_xml if name == 'word/document.xml' else None))
                if logo_xml and name == 'word/document.xml' and 'xmlns:wp=' not in xml[:3000]:
                    xml = xml.replace('<w:document ', '<w:document xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" ', 1)
                data = xml.encode('utf-8')
            elif name == 'word/_rels/document.xml.rels':
                rels = data.decode('utf-8')
                # relations vers des parties absentes retirées (le fichier s'ouvre sans réparation dans Word et LibreOffice)
                rels = re.sub(r'<Relationship [^>]*Target="([^"]+)"[^>]*/>',
                              lambda m: m.group(0) if ('TargetMode="External"' in m.group(0) or ('word/' + m.group(1).lstrip('/')) in names
                                                       or m.group(1).startswith('../') and m.group(1)[3:] in names) else '', rels)
                if logo_part:
                    rels = rels.replace('</Relationships>', '<Relationship Id="rIdLogo5653" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                                                            'relationships/image" Target="media/%s"/></Relationships>' % logo_part[0].split('/')[-1])
                data = rels.encode('utf-8')
            elif name == '[Content_Types].xml' and logo_part:
                ct = data.decode('utf-8')
                ext = 'png' if logo_part[1] == 'png' else 'jpeg'
                if 'Extension="%s"' % ext not in ct:
                    ct = ct.replace('<Types ', '<Types ', 1).replace('</Types>', '<Default Extension="%s" ContentType="image/%s"/></Types>' % (ext, ext))
                data = ct.encode('utf-8')
            elif name.startswith('docProps/thumbnail'):
                continue                                         # vignette du modèle : non représentative du document rempli
            zout.writestr(info, data)
        if logo_part:
            zout.writestr(logo_part[0], bytes(logo_bytes))
    return out.getvalue(), sorted(missing)


def render_text(template, values, lines=()):
    """Aperçu textuel (Markdown du modèle de base, sinon texte du Word) avec les mêmes variables que le rendu Word."""
    missing = set()
    if template.get('markdown') and Path(template['markdown']).is_file():
        text = Path(template['markdown']).read_text(encoding='utf-8')
    else:
        z = zipfile.ZipFile(Path(template['file']))
        xml = z.read('word/document.xml').decode('utf-8')
        text = '\n'.join(_text_of(p) for p in re.findall(r'<w:p(?:\s[^>]*)?>.*?</w:p>', xml, re.S))
    table = '\n'.join('| %s | %s | %s | %s | %s |' % tuple(l.get(k, '') for k in LINE_VARS) for l in lines)
    text = text.replace('[[LIGNES]]', '| Prestation | Quantité | Prix HT | TVA | Montant HT |\n|---|---|---|---|---|\n' + table if lines else '')
    if not lines:
        text = re.sub(r'.*\{\{\s*libelle\s*\}\}.*\n?', '', text)
    text = '\n'.join(line for line in text.split('\n') if not line.startswith('Version de travail '))   # note propre au modèle livré
    out = _subst(text, values, missing)
    return re.sub(r'\n{3,}', '\n\n', out), sorted(missing)

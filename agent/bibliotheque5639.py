"""5.6.39 : bibliothèque de modèles (Corporate) — statuts, pactes, LOI, protocoles, cessions et contrats, en droit français, allemand,
espagnol et anglais ; modèles personnalisables par le cabinet et modèles Word importés.

Chaque modèle est une trame : lignes de texte, variables {{cle}}, clauses optionnelles [[si cle]] … [[sinon]] … [[fin]] (sur des lignes seules,
ou dans une ligne), articles numérotés « ## ». L'avocat peut :
  - préparer un document à partir d'une trame (variables pré-remplies depuis la société choisie, clauses cochées) : dépôt dans le dossier
    (JURIDIQUE), téléchargement Word, ou rédaction par l'IA qui reçoit la trame comme base ;
  - personnaliser la trame (texte, variables, clauses) ou la remplacer par son propre modèle Word (mêmes balises) ;
  - ajouter ses propres modèles (texte ou Word).
Le Pilote (rédaction de documents, docrequest520) reçoit la trame correspondante lorsque la demande la désigne clairement.

Aucun document n'est signé ni envoyé ; les modèles étrangers portent la mention « à faire valider par un conseil local ».
"""
import base64
from datetime import date, datetime, timedelta, timezone
import hashlib
from html import escape as e
import io
import json
import re
import uuid
import zipfile
from xml.sax.saxutils import escape as xml_escape

from .common import Stop, clean_path, fold, load_matters, matter_display
from . import bibliotheque_fr5639 as _fr
from . import bibliotheque_intl5639 as _intl

BUILTIN = {t['id']: t for t in _fr.TEMPLATES + _intl.TEMPLATES}
LANGS = ('fr', 'en', 'de', 'es')
DROITS = {'FR': 'Droit français', 'DE': 'Droit allemand', 'ES': 'Droit espagnol', 'EN': 'Droit anglais (Angleterre et pays de Galles)'}
DROIT_OF_LANG = {'fr': 'FR', 'de': 'DE', 'es': 'ES', 'en': 'EN'}
LANG_OF_DROIT = {v: k for k, v in DROIT_OF_LANG.items()}
CATEGORIES = {'statuts': 'Statuts', 'pacte': 'Pactes d’associés et d’actionnaires', 'operation': 'Opérations : LOI, protocoles, cessions',
              'contrat': 'Contrats'}
MISSING = {'fr': '[À COMPLÉTER : %s]', 'de': '[ZU ERGÄNZEN: %s]', 'es': '[A COMPLETAR: %s]', 'en': '[TO BE COMPLETED: %s]'}
ARTICLE = {'fr': 'Article %d — %s', 'de': '§ %d %s', 'es': 'Artículo %d. %s', 'en': '%d. %s'}
NOTICE = {
    'fr': 'Projet établi à partir de la bibliothèque de modèles AxiorHub : trame de travail à relire, compléter et adapter par l’avocat.',
    'de': 'Entwurf auf Grundlage der AxiorHub-Mustersammlung: von einem in Deutschland zugelassenen Berater zu prüfen und anzupassen.',
    'es': 'Borrador elaborado a partir de la biblioteca de modelos de AxiorHub: debe ser revisado y adaptado por un abogado colegiado en España.',
    'en': 'Draft prepared from the AxiorHub template library: to be reviewed and adapted by a lawyer qualified in England and Wales.',
}
TAG = re.compile(r'\{\{\s*(\w+)\s*\}\}')
BLOCK = re.compile(r'\[\[si\s+(!?)(\w+)\]\]')
INLINE = re.compile(r'\[\[si\s+(!?)(\w+)\]\]((?:(?!\[\[si\s).)*?)(?:\[\[sinon\]\]((?:(?!\[\[si\s).)*?))?\[\[fin\]\]', re.S)
MULTILINE = {'associes', 'parties', 'parteien', 'gesellschafter', 'socios', 'partes', 'shareholders', 'parties_list', 'chiffres', 'expose', 'origine',
             'prestations', 'services', 'leistungen', 'servicios'}
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
HELP_KEY = 'aide_axiorhub'   # bloc d'aide des trames Word exportées : jamais vrai, retiré à la génération


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS bibliotheque5639(id TEXT PRIMARY KEY, base TEXT NOT NULL DEFAULT '', titre TEXT NOT NULL DEFAULT '',
      langue TEXT NOT NULL DEFAULT 'fr', droit TEXT NOT NULL DEFAULT 'FR', categorie TEXT NOT NULL DEFAULT 'contrat', texte TEXT NOT NULL DEFAULT '',
      word TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bibliotheque5639_fichiers(jeton TEXT PRIMARY KEY, owner TEXT NOT NULL, nom TEXT NOT NULL, donnees BLOB NOT NULL,
      created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bibliotheque5639_trames(jeton TEXT PRIMARY KEY, modele TEXT NOT NULL, titre TEXT NOT NULL, langue TEXT NOT NULL,
      texte TEXT NOT NULL, created TEXT NOT NULL);
    ''')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _lang():
    try:
        from .i18n5631 import current
        lang = current()
    except Exception:   # noqa: BLE001 — hors requête web (tâche de fond) : français
        lang = 'fr'
    return lang if lang in LANGS else 'fr'


# ------------------------------------------------------------------------------------------------ catalogue
def _parse_keys(lines):
    """Variables et clauses présentes dans une trame (texte personnalisé ou modèle Word)."""
    text = '\n'.join(lines)
    tags = list(dict.fromkeys(TAG.findall(text)))
    keys = [k for k in dict.fromkeys(m.group(2) for m in BLOCK.finditer(text)) if not k.startswith('aide')]
    return tags, keys


def template(desk, ident):
    """Modèle prêt à l'emploi : modèle d'origine, éventuellement personnalisé (texte, modèle Word) par le cabinet, ou modèle ajouté."""
    schema(desk)
    ident = str(ident or '')
    row = desk.db.execute('SELECT * FROM bibliotheque5639 WHERE id=?', (ident,)).fetchone()
    base = BUILTIN.get(ident)
    if base is None and row is None:
        raise Stop('modele_bibliotheque_absent')
    t = dict(base) if base else {'id': ident, 'langue': row['langue'], 'droit': row['droit'], 'categorie': row['categorie'], 'formes': (),
                                 'titre': row['titre'], 'resume': {}, 'mots': (), 'variables': [], 'clauses': [], 'fixes': {}, 'texte': []}
    t.update(origine='cabinet' if base is None else 'axiorhub', personnalise=False, word='')
    if row is not None:
        if row['texte']:
            t['texte'] = row['texte'].split('\n')
            t['personnalise'] = base is not None
        t['word'] = row['word'] or ''
        if base is None:
            t['titre'] = row['titre']
    # variables et clauses ajoutées par le cabinet dans son texte : proposées avec leur nom
    tags, keys = _parse_keys(t['texte'])
    known_v = {k for k, _, _ in t['variables']} | set(t['fixes'])
    known_c = {k for k, _, _, _ in t['clauses']}
    t['variables'] = list(t['variables']) + [(k, k.replace('_', ' '), '') for k in tags if k not in known_v]
    t['clauses'] = list(t['clauses']) + [(k, k.replace('_', ' '), False, '') for k in keys if k not in known_c]
    return t


def catalog(desk):
    schema(desk)
    rows = {r['id']: r for r in desk.db.execute('SELECT id, base, word, texte FROM bibliotheque5639')}
    out = []
    for ident in list(BUILTIN) + [i for i in rows if i not in BUILTIN]:
        try:
            out.append(template(desk, ident))
        except Stop:
            continue
    return out


# ------------------------------------------------------------------------------------------------ rendu
def apply_conditions(lines, flags):
    """Clauses optionnelles : blocs [[si cle]] / [[si !cle]] … [[sinon]] … [[fin]] sur des lignes seules, puis forme en ligne."""
    out, stack, active = [], [], True
    for line in lines:
        s = line.strip()
        m = BLOCK.fullmatch(s)
        if m:
            cond = bool(flags.get(m.group(2))) != bool(m.group(1))
            stack.append([active, cond])
            active = active and cond
            continue
        if s == '[[sinon]]' and stack:
            parent, cond = stack[-1]
            active = parent and not cond
            continue
        if s == '[[fin]]' and stack:
            active = stack.pop()[0]
            continue
        if active:
            out.append(inline_conditions(line, flags))
    return out


def inline_conditions(text, flags):
    def sub(m):
        cond = bool(flags.get(m.group(2))) != bool(m.group(1))
        return m.group(3) if cond else (m.group(4) or '')
    for _ in range(4):
        new = INLINE.sub(sub, text)
        if new == text:
            break
        text = new
    return text


def values_for(t, data, lang=None):
    """Valeur de chaque variable : saisie, sinon valeur par défaut du modèle ; constantes du modèle (forme, textes applicables)."""
    out = {}
    for key, _, default in t['variables']:
        v = data.get(key)
        out[key] = str(v).strip() if v is not None and str(v).strip() else default
    declared = set(out)
    for key, v in t['fixes'].items():   # constantes du modèle (forme, textes applicables) : jamais remplacées par une donnée d'une autre société
        if key not in declared:
            out[key] = v
    return out


def flags_for(t, data):
    flags = {}
    for key, _, default, _ in t['clauses']:
        if 'clause_' + key in data:
            flags[key] = data['clause_' + key] is True or str(data['clause_' + key]) in ('1', 'on', 'true', 'True')
        else:
            flags[key] = bool(default)
    return flags


def render(t, values, flags):
    """Trame → paragraphes (texte, style) : clauses appliquées, articles numérotés, variables remplacées (« à compléter » sinon)."""
    lang = t['langue'] if t['langue'] in LANGS else 'fr'
    labels = {k: lab for k, lab, _ in t['variables']}
    n = 0
    paras = []

    def fill(text):
        for _ in range(3):
            text = TAG.sub(lambda m: values.get(m.group(1)) or MISSING[lang] % labels.get(m.group(1), m.group(1).replace('_', ' ')), text)
        return text
    for line in apply_conditions(t['texte'], flags):
        if not line.strip():
            continue
        if line.startswith('## '):
            n += 1
            paras.append((ARTICLE[lang] % (n, fill(line[3:].strip())), 'article'))
            continue
        style, body = ('titre', line[2:]) if line.startswith('# ') else ('liste', line[2:]) if line.startswith('- ') else ('texte', line)
        for part in fill(body).split('\n'):
            if part.strip():
                paras.append((part.strip(), style))
    return paras


def plain_text(paras):
    return '\n'.join(('  – ' + x) if s == 'liste' else ('\n' + x) if s in ('article', 'titre') else x for x, s in paras).strip()


# ------------------------------------------------------------------------------------------------ Word
def _para_xml(text, style):
    ppr = {'titre': '<w:pPr><w:jc w:val="center"/><w:spacing w:after="120"/></w:pPr>',
           'article': '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/></w:pPr>',
           'liste': '<w:pPr><w:ind w:left="567" w:hanging="283"/><w:jc w:val="both"/><w:spacing w:after="60"/></w:pPr>',
           'note': '<w:pPr><w:spacing w:before="360"/></w:pPr>'}.get(style, '<w:pPr><w:jc w:val="both"/><w:spacing w:after="120"/></w:pPr>')
    rpr = {'titre': '<w:rPr><w:b/><w:sz w:val="26"/></w:rPr>', 'article': '<w:rPr><w:b/></w:rPr>',
           'note': '<w:rPr><w:i/><w:sz w:val="18"/></w:rPr>'}.get(style, '')
    body = ('– ' if style == 'liste' else '') + text
    return '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (ppr, rpr, xml_escape(body))


def build_docx(paras, lang='fr', notice=True):
    """Document Word autonome (Garamond 12, A4, marges usuelles) ; la mention « projet à relire » termine le document."""
    items = list(paras) + ([(NOTICE.get(lang, NOTICE['fr']), 'note')] if notice else [])
    body = ''.join(_para_xml(x, s) for x, s in items)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="%s"><w:body>%s<w:sectPr><w:pgSz w:w="11906" '
                'w:h="16838"/><w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" w:header="709" w:footer="709" w:gutter="0"/>'
                '</w:sectPr></w:body></w:document>') % (W, body)
    lang_tag = {'fr': 'fr-FR', 'de': 'de-DE', 'es': 'es-ES', 'en': 'en-GB'}.get(lang, 'fr-FR')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="%s"><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Garamond" w:hAnsi="Garamond" w:cs="Garamond"/><w:sz w:val="24"/><w:lang w:val="%s"/></w:rPr></w:rPrDefault>'
              '</w:docDefaults></w:styles>') % (W, lang_tag)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType='
                   '"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                   'officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://'
                   'schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('word/document.xml', document)
        z.writestr('word/styles.xml', styles)
    return out.getvalue()


HELP = {
    'fr': ['[[si aide_axiorhub]]', 'TRAME AXIORHUB — mode d’emploi (ce paragraphe est retiré des documents préparés) : modifiez librement le texte et la '
           'mise en forme. {{variable}} est remplacée par la donnée saisie ; un paragraphe [[si clause]] … [[fin]] (sur des lignes seules) n’est '
           'conservé que si la clause est cochée ; un paragraphe commençant par « ## » devient un article numéroté. Importez ensuite ce fichier '
           'dans AxiorHub (« Importer mon modèle Word »).', '[[fin]]'],
}


def template_docx(t):
    """Trame Word modifiable par le cabinet : texte brut avec ses {{variables}}, [[clauses]] et « ## » ; réimportable tel quel."""
    lines = HELP['fr'] + list(t['texte'])
    paras = []
    for line in lines:
        if not line.strip():
            continue
        paras.append((line, 'titre' if line.startswith('# ') else 'texte'))
    return build_docx(paras, t['langue'], notice=False)


def _runs_text(p):
    from html import unescape
    return unescape(''.join(re.findall(r'<w:t(?: [^>]*)?>([^<]*)</w:t>', p)))


def _set_text(p, text):
    first = [True]

    def run(m):
        if first[0]:
            first[0] = False
            return '<w:t xml:space="preserve">%s</w:t>' % xml_escape(text).replace('\n', '</w:t><w:br/><w:t xml:space="preserve">')
        return '<w:t xml:space="preserve"></w:t>'
    return re.sub(r'<w:t(?: [^>]*)?>[^<]*</w:t>', run, p)


def fill_word(raw, t, values, flags):
    """Modèle Word du cabinet : paragraphes [[si]]/[[sinon]]/[[fin]] appliqués, « ## » numérotés, {{variables}} remplacées ; la mise en forme
    de chaque paragraphe conservé est respectée."""
    lang = t['langue'] if t['langue'] in LANGS else 'fr'
    labels = {k: lab for k, lab, _ in t['variables']}

    def fill(text):
        for _ in range(3):
            text = TAG.sub(lambda m: values.get(m.group(1)) or MISSING[lang] % labels.get(m.group(1), m.group(1).replace('_', ' ')), text)
        return text
    src, dst = io.BytesIO(raw), io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if re.fullmatch(r'word/(document|header\d*|footer\d*)\.xml', info.filename):
                xml = data.decode('utf-8')
                state = {'stack': [], 'active': True, 'n': 0}

                def para(m):
                    p = m.group(0)
                    text = _runs_text(p)
                    s = text.strip()
                    b = BLOCK.fullmatch(s)
                    if b:
                        cond = bool(flags.get(b.group(2))) != bool(b.group(1))
                        state['stack'].append([state['active'], cond])
                        state['active'] = state['active'] and cond
                        return ''
                    if s == '[[sinon]]' and state['stack']:
                        parent, cond = state['stack'][-1]
                        state['active'] = parent and not cond
                        return ''
                    if s == '[[fin]]' and state['stack']:
                        state['active'] = state['stack'].pop()[0]
                        return ''
                    if not state['active']:
                        return ''
                    if '{{' not in text and '[[' not in text and not s.startswith('## '):
                        return p
                    new = inline_conditions(text, flags)
                    if new.lstrip().startswith('## '):
                        state['n'] += 1
                        new = ARTICLE[lang] % (state['n'], new.lstrip()[3:].strip())
                    return _set_text(p, fill(new))
                xml = re.sub(r'<w:p[ >].*?</w:p>', para, xml, flags=re.S)
                data = xml.encode('utf-8')
            zout.writestr(info, data)
    return dst.getvalue()


# ------------------------------------------------------------------------------------------------ société → variables
def prefill(desk, cid):
    """Données de la société choisie, sous les noms de variables de toutes les langues de la bibliothèque."""
    if not cid:
        return {}
    from . import corporate5636 as co
    c = co.company(desk, cid)
    rows, total = co.holdings(desk, cid)
    heads = co.officers(desk, cid)
    forme = co.FORMS.get(c['forme'], ('', 'titres'))
    holders = '\n'.join('%s — %d %s' % (r['name'], r['quantity'], forme[1]) for r in rows)
    head = heads[0]['name'] if heads else ''
    try:
        capital = co.money(c['capital']).replace(' €', '').replace(' €', '').replace(' €', '')
    except Exception:   # noqa: BLE001
        capital = c['capital']
    city = re.sub(r'^.*\d{5}\s+', '', c['siege'] or '').strip()
    base = {'denomination': c['denomination'], 'forme_long': forme[0], 'siege': c['siege'], 'capital': capital, 'nominal': c['nominal'],
            'nombre_titres': str(total or ''), 'objet': c['objet'], 'duree': c['duree'], 'rcs_ville': c['rcs'], 'siren': c['siren'],
            'associes': holders, 'parties': holders, 'premier_dirigeant': head, 'dirigeant': head, 'ville': city, 'titres': forme[1],
            'date_acte': date.today().strftime('%d/%m/%Y')}
    base.update({'firma': c['denomination'], 'sitz': city, 'stammkapital': capital, 'gesellschafter': holders, 'parteien': holders,
                 'denominacion': c['denomination'], 'domicilio': c['siege'], 'socios': holders, 'partes': holders, 'sociedad': c['denomination'],
                 'company_name': c['denomination'], 'registered_office': c['siege'], 'shareholders': holders})
    return {k: v for k, v in base.items() if v}


# ------------------------------------------------------------------------------------------------ actions
def _flag(value):
    return value is True or str(value) in ('1', 'on', 'true', 'True')


def _document(desk, t, data):
    values = values_for(t, data)
    flags = flags_for(t, data)
    if t.get('word'):
        from .corporate5636 import load_template, check_docx
        raw = load_template(desk, t['word'])
        check_docx(raw)
        return fill_word(raw, t, values, flags), values, flags
    return build_docx(render(t, values, flags), t['langue']), values, flags


def _filename(t, values):
    who = values.get('denomination') or values.get('firma') or values.get('denominacion') or values.get('company_name') or ''
    name = '%s - %s%s.docx' % (date.today().isoformat(), t['titre'], (' - ' + who) if who and not who.startswith('[') else '')
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name)[:150]


def generate(desk, data, owner='cabinet', dav=None):
    """Préparer un document : dans le dossier (JURIDIQUE), en téléchargement, ou par l'IA avec la trame comme base."""
    t = template(desk, data.get('modele'))
    where = str(data.get('destination') or 'telecharger')
    cid = str(data.get('societe') or '')
    if cid:   # données de la société pour les variables laissées vides (appel direct de l'API)
        data = {**prefill(desk, cid), **{k: v for k, v in data.items() if v not in (None, '')}}
    if where == 'ia':
        return _ask_ai(desk, t, data, owner)
    raw, values, flags = _document(desk, t, data)
    name = _filename(t, values)
    if where == 'dossier':
        matter = _matter(desk, data, cid)
        from .dav import DAV
        client = dav or DAV(desk.c['nextcloud'])
        folder = '%s/JURIDIQUE' % matter['path'].rstrip('/')
        client.ensure_folder(folder, matter['path'])
        stem, n = name[:-5], 1
        while True:
            target = folder + '/' + (name if n == 1 else '%s (%d).docx' % (stem, n))
            try:
                client.put_file(target, raw, DOCX_MIME)
                break
            except Stop as exc:
                if str(exc) != 'http_412' or n > 20:
                    raise
                n += 1
        desk.audit('bibliotheque5639_document', {'modele': t['id'], 'dossier': matter['id'], 'owner': owner, 'clauses': sorted(k for k, v in flags.items() if v)})
        return {'path': target, 'message': 'Document « %s » déposé dans le dossier %s (JURIDIQUE) : projet à relire.' % (name, matter_display(matter))}
    jeton = uuid.uuid4().hex
    desk.db.execute("DELETE FROM bibliotheque5639_fichiers WHERE created < ?", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
    desk.db.execute('INSERT INTO bibliotheque5639_fichiers(jeton, owner, nom, donnees, created) VALUES(?,?,?,?,?)', (jeton, owner, name, raw, _now()))
    desk.db.commit()
    desk.audit('bibliotheque5639_telechargement', {'modele': t['id'], 'owner': owner})
    goto = '?vue=bibliotheque&modele=%s%s&fichier=%s' % (t['id'], ('&societe=' + cid) if re.fullmatch(r'[\w-]+', cid or '') else '', jeton)
    return {'goto': goto, 'message': 'Document Word prêt : cliquez « Télécharger le document ».'}


def download(desk, jeton, owner='cabinet'):
    schema(desk)
    row = desk.db.execute('SELECT * FROM bibliotheque5639_fichiers WHERE jeton=?', (str(jeton or ''),)).fetchone()
    if not row or row['owner'] != owner:
        raise Stop('document_bibliotheque_expire')
    return {'download': {'filename': row['nom'], 'mime': DOCX_MIME, 'base64': base64.b64encode(row['donnees']).decode()}}


def _matter(desk, data, cid):
    matters = {m['id']: m for m in load_matters(desk.c)}
    mid = str(data.get('matter') or '')
    if not mid and cid:
        from . import corporate5636 as co
        mid = co.company(desk, cid)['matter']
    if not mid or mid not in matters:
        raise Stop('dossier_requis')
    return matters[mid]


def _ask_ai(desk, t, data, owner):
    """Rédaction par l'IA : la trame remplie (clauses retenues, variables saisies) est conservée et transmise à la rédaction du dossier."""
    matter = _matter(desk, data, str(data.get('societe') or ''))
    values, flags = values_for(t, data), flags_for(t, data)
    texte = plain_text(render(t, values, flags))
    jeton = uuid.uuid4().hex[:16]
    desk.db.execute('INSERT INTO bibliotheque5639_trames(jeton, modele, titre, langue, texte, created) VALUES(?,?,?,?,?,?)',
                    (jeton, t['id'], t['titre'], t['langue'], texte[:60000], _now()))
    desk.db.commit()
    consignes = re.sub(r'\s+', ' ', str(data.get('consignes') or '')).strip()[:3000]
    request = ('Rédige « %s » (%s) pour ce dossier en suivant la trame de la bibliothèque AxiorHub [trame:%s] : conserve sa structure et les '
               'clauses retenues, complète les mentions à compléter à partir des pièces du dossier, sans rien inventer.%s') % (
        t['titre'], DROITS.get(t['droit'], t['droit']), jeton, (' Consignes de l’avocat : ' + consignes) if consignes else '')
    from .docrequest520 import submit
    out = submit(desk, request, matter['id'], 'contrat' if t['categorie'] in ('contrat', 'pacte', 'operation') else 'autre')
    desk.audit('bibliotheque5639_ia', {'modele': t['id'], 'dossier': matter['id'], 'owner': owner})
    return {'request': out['request'], 'job': out['job_id'],
            'message': 'Rédaction confiée à l’IA avec la trame « %s » : le projet Word sera déposé dans le dossier %s.' % (t['titre'], matter_display(matter))}


def customize(desk, data, owner='cabinet'):
    """Texte personnalisé d'un modèle (ou d'un modèle ajouté) : variables et clauses relevées dans le texte."""
    t = template(desk, data.get('modele'))
    text = str(data.get('texte') or '').replace('\r\n', '\n').replace('\r', '\n')
    lines = [re.sub(r'[\x00-\x08\x0b-\x1f]', '', x).rstrip() for x in text.split('\n')]
    if not any(x.strip() for x in lines) or len(text) > 200000:
        raise Stop('texte_modele_invalide')
    depth = 0
    for x in lines:
        s = x.strip()
        depth += 1 if BLOCK.fullmatch(s) else -1 if s == '[[fin]]' else 0
        if depth < 0:
            break
    if depth != 0:
        raise Stop('modele_conditions_desequilibrees')
    _save(desk, t, owner, texte='\n'.join(lines))
    tags, keys = _parse_keys(lines)
    desk.audit('bibliotheque5639_personnalise', {'modele': t['id'], 'owner': owner})
    return {'message': 'Modèle « %s » enregistré pour le cabinet : %d variable(s), %d clause(s) optionnelle(s).' % (t['titre'], len(tags), len(keys))}


def _save(desk, t, owner, **fields):
    schema(desk)
    row = desk.db.execute('SELECT * FROM bibliotheque5639 WHERE id=?', (t['id'],)).fetchone()
    now = _now()
    if row is None:
        desk.db.execute('INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (t['id'], t['id'] if t['id'] in BUILTIN else '', t['titre'], t['langue'], t['droit'],
                                                          t['categorie'], fields.get('texte', ''), fields.get('word', ''), owner, now, now))
    else:
        for k, v in fields.items():
            desk.db.execute('UPDATE bibliotheque5639 SET %s=?, updated=? WHERE id=?' % k, (v, now, t['id']))
    desk.db.commit()


def restore(desk, data, owner='cabinet'):
    t = template(desk, data.get('modele'))
    if t['id'] not in BUILTIN:
        raise Stop('modele_bibliotheque_absent')
    schema(desk)
    desk.db.execute('DELETE FROM bibliotheque5639 WHERE id=?', (t['id'],))
    desk.db.commit()
    desk.audit('bibliotheque5639_retabli', {'modele': t['id'], 'owner': owner})
    return {'message': 'Modèle d’origine rétabli (texte et modèle Word du cabinet retirés).'}


def set_word(desk, data, owner='cabinet'):
    """Modèle Word du cabinet pour cette trame (importé depuis le poste ou choisi dans Nextcloud) ; vide = retour au texte."""
    t = template(desk, data.get('modele'))
    path = str(data.get('path') or '').strip()
    if path:
        from .corporate5636 import _source, load_template, check_docx, template_tags
        path = _source(path)
        try:
            raw = load_template(desk, path)
            check_docx(raw)
            tags = template_tags(raw)
        except Stop as exc:
            raise Stop(str(exc)) from None
        known = {k for k, _, _ in t['variables']} | set(t['fixes'])
        unknown = sorted(tags - known)
        _save(desk, t, owner, word=path)
        desk.audit('bibliotheque5639_word', {'modele': t['id'], 'owner': owner, 'balises': len(tags)})
        return {'message': 'Modèle Word enregistré : %d balise(s)%s.' % (len(tags), (' ; inconnue(s), laissée(s) « à compléter » : '
                                                                                    + ', '.join('{{%s}}' % x for x in unknown)) if unknown else '')}
    _save(desk, t, owner, word='')
    return {'message': 'Modèle Word retiré : la trame texte est utilisée.'}


def add(desk, data, owner='cabinet'):
    """« Ajouter mon modèle » : titre, catégorie, droit, et texte (syntaxe de la bibliothèque) et/ou modèle Word."""
    titre = re.sub(r'\s+', ' ', str(data.get('titre') or '')).strip()[:160]
    if not titre:
        raise Stop('titre_modele_requis')
    categorie = str(data.get('categorie') or 'contrat')
    droit = str(data.get('droit') or 'FR')
    if categorie not in CATEGORIES or droit not in DROITS:
        raise Stop('modele_categorie_invalide')
    text = str(data.get('texte') or '').strip()
    path = str(data.get('path') or '').strip()
    if not text and not path:
        raise Stop('texte_ou_modele_word_requis')
    ident = 'cab_' + uuid.uuid4().hex[:12]
    schema(desk)
    now = _now()
    desk.db.execute('INSERT INTO bibliotheque5639(id, base, titre, langue, droit, categorie, texte, word, owner, created, updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (ident, '', titre, LANG_OF_DROIT[droit], droit, categorie, '', '', owner, now, now))
    desk.db.commit()
    try:
        if text:
            customize(desk, {'modele': ident, 'texte': text}, owner)
        if path:
            set_word(desk, {'modele': ident, 'path': path}, owner)
    except Stop:
        desk.db.execute('DELETE FROM bibliotheque5639 WHERE id=?', (ident,))
        desk.db.commit()
        raise
    desk.audit('bibliotheque5639_ajout', {'modele': ident, 'owner': owner})
    return {'id': ident, 'goto': '?vue=bibliotheque&modele=' + ident, 'message': 'Modèle « %s » ajouté à la bibliothèque du cabinet.' % titre}


def remove(desk, data, owner='cabinet'):
    ident = str(data.get('modele') or '')
    if ident in BUILTIN or not ident.startswith('cab_'):
        raise Stop('modele_bibliotheque_absent')
    schema(desk)
    desk.db.execute('DELETE FROM bibliotheque5639 WHERE id=?', (ident,))
    desk.db.commit()
    desk.audit('bibliotheque5639_retire', {'modele': ident, 'owner': owner})
    return {'goto': '?vue=bibliotheque', 'message': 'Modèle retiré de la bibliothèque.'}


def word_template(desk, ident):
    t = template(desk, ident)
    if t.get('word'):
        from .corporate5636 import load_template
        raw = load_template(desk, t['word'])
    else:
        raw = template_docx(t)
    name = re.sub(r'[\\/:*?"<>|]', '_', 'Trame - %s.docx' % t['titre'])[:150]
    return {'download': {'filename': name, 'mime': DOCX_MIME, 'base64': base64.b64encode(raw).decode()}}


# ------------------------------------------------------------------------------------------------ IA (Pilote, rédaction de documents)
def _words(text):
    return set(re.findall(r'\w+', fold(str(text or '')).lower()))


def match(desk, request, lang=None):
    """Modèle désigné par une demande de rédaction : expression caractéristique du modèle entièrement présente dans la demande (mots
    entiers) ; en cas d'égalité, le droit correspondant à la langue de l'utilisateur, puis le droit français."""
    words = _words(request)
    lang = lang or _lang()
    best = None
    for t in catalog(desk):
        for phrase in t.get('mots') or ():
            need = _words(phrase)
            if not need or not need <= words:
                continue
            if len(need) < 2 and next(iter(need)) not in STRONG:
                continue
            score = (len(need), t['langue'] == lang, t['droit'] == 'FR')
            if best is None or score > best[0]:
                best = (score, t)
    return best[1] if best else None


STRONG = {'nda', 'cgv', 'agb', 'satzung', 'gesellschaftsvertrag', 'estatutos', 'gesellschaftervereinbarung', 'anteilskaufvertrag',
          'absichtserklarung', 'geheimhaltungsvereinbarung', 'dienstleistungsvertrag', 'gesellschafterdarlehen', 'darlehensvertrag',
          'beteiligungsvereinbarung', 'abtretungsvertrag', 'vertraulichkeitsvereinbarung', 'unternehmergesellschaft'}


def trame_for_request(desk, request):
    """Trame transmise à la rédaction : celle choisie dans la bibliothèque ([trame:…]), sinon le modèle clairement désigné par la demande."""
    schema(desk)
    m = re.search(r'\[trame:([0-9a-f]{16})\]', str(request or ''))
    if m:
        row = desk.db.execute('SELECT * FROM bibliotheque5639_trames WHERE jeton=?', (m.group(1),)).fetchone()
        if row:
            return {'titre': row['titre'], 'langue': row['langue'], 'origine': 'choisie par l’avocat dans la bibliothèque',
                    'consigne': CONSIGNE, 'texte': row['texte'][:30000]}
    t = match(desk, request)
    if not t or t.get('word') and not t['texte']:
        return None
    flags = flags_for(t, {})
    texte = plain_text(render(t, values_for(t, {}), flags))
    return {'titre': t['titre'], 'langue': t['langue'], 'origine': 'modèle de la bibliothèque correspondant à la demande', 'consigne': CONSIGNE,
            'clauses_proposees': [lab for k, lab, _, _ in t['clauses'] if flags.get(k)], 'texte': texte[:30000]}


CONSIGNE = ('Utilise cette trame comme base : suis sa structure et ses articles, adapte-la au dossier et à la demande, complète les mentions '
            '« à compléter » à partir des pièces et laisse-les apparentes si l’information manque ; ne supprime pas une clause sans le signaler '
            'dans a_completer ; signale toute référence légale à vérifier.')


# ------------------------------------------------------------------------------------------------ écran
def _resume(t, lang):
    r = t.get('resume') or {}
    return r.get(lang) or r.get('fr') or ''


def _url(prefix, cid, **q):
    base = prefix + '/corporate?vue=bibliotheque'
    if cid:
        base += '&societe=' + cid
    return base + ''.join('&%s=%s' % (k, v) for k, v in q.items() if v)


def page_html(desk, prefix, args, cid=''):
    lang = _lang()
    droit = str(args.get('droit') or DROIT_OF_LANG.get(lang, 'FR'))
    cat = str(args.get('categorie') or '')
    ident = str(args.get('modele') or '')
    if ident:
        return detail_html(desk, prefix, args, ident, cid)
    items = [t for t in catalog(desk) if (droit == 'tous' or t['droit'] == droit) and (not cat or t['categorie'] == cat)]
    company_form = ''
    if cid:
        from . import corporate5636 as co
        company_form = co.company(desk, cid)['forme']
    filters = ('<form class="b5639-filters" method="get" action="%s/corporate"><input type="hidden" name="vue" value="bibliotheque">%s'
               '<label>Droit<select name="droit">%s</select></label><label>Catégorie<select name="categorie"><option value="">Toutes</option>%s</select>'
               '</label><button class="ax-btn ghost" type="submit">Afficher</button></form>') % (
        e(prefix, quote=True), ('<input type="hidden" name="societe" value="%s">' % e(cid, quote=True)) if cid else '',
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == droit else '', e(v)) for k, v in list(DROITS.items()) + [('tous', 'Tous les droits')]),
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == cat else '', e(v)) for k, v in CATEGORIES.items()))
    groups = []
    for key, label in CATEGORIES.items():
        rows = [t for t in items if t['categorie'] == key]
        if not rows:
            continue
        rows.sort(key=lambda t: (company_form not in t.get('formes', ()), t['origine'] != 'cabinet', t['titre']))
        groups.append('<h3>%s</h3><ul class="b5639-list">%s</ul>' % (e(label), ''.join(
            '<li><a class="ax-card b5639-item" href="%s"><strong>%s</strong>%s<small>%s</small></a></li>' % (
                e(_url(prefix, cid, modele=t['id']), quote=True), e(t['titre']), _badges(t, company_form),
                e(_resume(t, lang) or DROITS.get(t['droit'], ''))) for t in rows)))
    intro = ('<section class="ax-card b5639"><h2>Bibliothèque de modèles</h2><p class="ax-muted">Statuts, pactes, lettres d’intention, protocoles, '
             'cessions et contrats, en droit français, allemand, espagnol et anglais. Choisissez un modèle, cochez les clauses utiles, complétez '
             'les variables (pré-remplies depuis la société choisie) puis déposez le projet dans le dossier, téléchargez-le en Word ou confiez la '
             'rédaction à l’IA, qui suivra la trame. Vous pouvez personnaliser chaque modèle, le remplacer par votre modèle Word ou ajouter les '
             'vôtres.</p>%s</section>') % filters
    add_form = ('<section class="ax-card"><details><summary>＋ Ajouter mon modèle</summary><form class="m5-form" data-api="m5636/corporate/bibliotheque/'
                'ajouter" data-reload="1"><label>Titre<input name="titre" required maxlength="160"></label><label>Catégorie<select name="categorie">%s'
                '</select></label><label>Droit<select name="droit">%s</select></label><label>Texte du modèle (facultatif si vous importez un modèle '
                'Word)<textarea name="texte" rows="10" data-no-dictation placeholder="## Objet&#10;La société {{denomination}}…&#10;[[si '
                'non_concurrence]]&#10;## Non-concurrence&#10;…&#10;[[fin]]"></textarea></label><label>Modèle Word (facultatif)%s</label>%s'
                '<button class="ax-btn" type="submit">Ajouter le modèle</button></form></details></section>') % (
        ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in CATEGORIES.items()),
        ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == DROIT_OF_LANG.get(lang) else '', e(v)) for k, v in DROITS.items()),
        _word_input(), _syntax_help())
    return intro + (''.join(groups) or '<p class="ax-muted">Aucun modèle pour ce filtre.</p>') + add_form


def _badges(t, company_form=''):
    out = []
    if company_form and company_form in t.get('formes', ()):
        out.append('<span class="b5639-badge on">%s</span>' % e(company_form))
    if t['droit'] != 'FR':
        out.append('<span class="b5639-badge">%s</span>' % e(t['droit']))
    if t['origine'] == 'cabinet':
        out.append('<span class="b5639-badge cab">Modèle du cabinet</span>')
    elif t['personnalise']:
        out.append('<span class="b5639-badge cab">Personnalisé</span>')
    if t.get('word'):
        out.append('<span class="b5639-badge cab">Word du cabinet</span>')
    return ' ' + ''.join(out) if out else ''


def _word_input(value=''):
    from .corporate5636 import _template_input
    return _template_input(value, '/Modèles/Mon modèle.docx')


def _syntax_help():
    return ('<details class="b5639-help"><summary>Syntaxe des modèles</summary><ul><li><code>{{variable}}</code> : donnée remplacée à la '
            'préparation (dénomination, siège, capital…).</li><li><code>[[si clause]]</code> … <code>[[sinon]]</code> … <code>[[fin]]</code>, sur '
            'des lignes seules : clause optionnelle proposée en case à cocher ; aussi possible dans une ligne.</li><li><code>## Intitulé</code> : '
            'article numéroté automatiquement ; <code># Titre</code> : titre en gras ; <code>- texte</code> : élément de liste.</li><li>Dans un '
            'modèle Word, mêmes balises ; les paragraphes <code>[[si …]]</code> / <code>[[fin]]</code> sont retirés du document préparé.</li>'
            '</ul></details>')


def detail_html(desk, prefix, args, ident, cid=''):
    lang = _lang()
    t = template(desk, ident)
    data = prefill(desk, cid) if cid else {}
    values = values_for(t, data)
    flags = flags_for(t, {})
    warn = ''
    if t['droit'] != 'FR':
        warn = ('<p class="b5639-warn" role="note">Modèle de %s : trame de travail à faire valider par un conseil local avant tout usage ; '
                'AxiorHub ne donne pas d’avis sur un droit étranger.</p>') % e(DROITS[t['droit']].replace('Droit ', 'droit '))
    fields = []
    for key, label, default in t['variables']:
        v = values.get(key) or ''
        if key in MULTILINE or len(v) > 90:
            fields.append('<label>%s<textarea name="%s" rows="3" data-no-dictation>%s</textarea></label>' % (e(label), e(key, quote=True), e(v)))
        else:
            fields.append('<label>%s<input name="%s" value="%s"></label>' % (e(label), e(key, quote=True), e(v, quote=True)))
    clauses = ''.join('<label class="e5636-switch" title="%s"><input type="checkbox" name="clause_%s"%s> %s</label>' % (
        e(help_, quote=True), e(k, quote=True), ' checked' if flags.get(k) else '', e(label)) for k, label, _, help_ in t['clauses'])
    try:
        matters = load_matters(desk.c)
    except Stop:
        matters = []
    own_matter = ''
    if cid:
        from . import corporate5636 as co
        own_matter = co.company(desk, cid)['matter']
    matter_select = '' if own_matter else ('<label>Dossier<select name="matter"><option value="">— choisir —</option>%s</select></label>' % ''.join(
        '<option value="%s">%s</option>' % (e(m['id'], quote=True), e(matter_display(m))) for m in matters))
    ready = ''
    if re.fullmatch(r'[0-9a-f]{32}', str(args.get('fichier') or '')):
        ready = ('<p class="b5639-ready" role="status">Document prêt : <button type="button" class="ax-btn m5-export" data-api="m5636/corporate/'
                 'bibliotheque/fichier?jeton=%s">⬇ Télécharger le document</button></p>') % e(str(args.get('fichier')), quote=True)
    form = ('<section class="ax-card"><h3>Préparer le document</h3>%s<form class="m5-form b5639-form" data-api="m5636/corporate/bibliotheque/generer" '
            'data-reload="1"><input type="hidden" name="modele" value="%s"><input type="hidden" name="societe" value="%s">%s'
            '<fieldset><legend>Clauses</legend>%s</fieldset><fieldset class="b5639-vars"><legend>Variables</legend>%s</fieldset>'
            '<fieldset><legend>Destination</legend><label class="e5636-switch"><input type="radio" name="destination" value="telecharger" checked> '
            'Télécharger en Word</label><label class="e5636-switch"><input type="radio" name="destination" value="dossier"> Déposer dans le dossier '
            '(JURIDIQUE)</label><label class="e5636-switch"><input type="radio" name="destination" value="ia"> Faire rédiger par l’IA à partir de '
            'cette trame et des pièces du dossier</label>%s<label>Consignes pour l’IA (facultatif)<textarea name="consignes" rows="2"></textarea>'
            '</label></fieldset><button class="ax-btn" type="submit">Préparer</button></form></section>') % (
        ready, e(t['id'], quote=True), e(cid, quote=True), warn, clauses or '<p class="ax-muted">Aucune clause optionnelle.</p>', ''.join(fields),
        matter_select)
    preview = plain_text(render(t, values, flags)) if not t.get('word') else 'Modèle Word du cabinet : le document conserve sa mise en forme.'
    custom = ('<section class="ax-card"><h3>Personnaliser ce modèle</h3><p class="ax-muted">Le texte ci-dessous est la trame utilisée par le '
              'cabinet : modifiez-le, ajoutez vos {{variables}} et vos clauses [[si …]] ; elles seront proposées à la préparation.</p>'
              '<form class="m5-form" data-api="m5636/corporate/bibliotheque/personnaliser" data-reload="1"><input type="hidden" name="modele" '
              'value="%s"><textarea name="texte" rows="18" data-no-dictation class="b5639-src">%s</textarea><button class="ax-btn" type="submit">'
              'Enregistrer le modèle du cabinet</button></form>%s%s'
              '<h4>Modèle Word</h4><p class="ax-muted">Téléchargez la trame en Word, adaptez-la (texte, mise en forme, balises), puis importez-la : '
              'elle sera utilisée à la place du texte.</p><p><button type="button" class="ax-btn ghost m5-export" data-api="m5636/corporate/'
              'bibliotheque/word?modele=%s">⬇ Télécharger la trame en Word</button></p><form class="m5-form" data-api="m5636/corporate/bibliotheque/'
              'word" data-reload="1"><input type="hidden" name="modele" value="%s"><label>Mon modèle Word%s</label><button class="ax-btn ghost" '
              'type="submit">Enregistrer le modèle Word</button></form>%s</section>') % (
        e(t['id'], quote=True), e('\n'.join(t['texte'])), _syntax_help(),
        ('<form class="m5-form m5-inline" data-api="m5636/corporate/bibliotheque/retablir" data-reload="1" data-confirm="Revenir au modèle '
         'd’origine ? Votre texte et votre modèle Word seront retirés."><input type="hidden" name="modele" value="%s"><button class="ax-btn ghost" '
         'type="submit">Revenir au modèle d’origine</button></form>' % e(t['id'], quote=True)) if (t['personnalise'] or t.get('word')) and
        t['origine'] == 'axiorhub' else '',
        e(t['id'], quote=True), e(t['id'], quote=True), _word_input(t.get('word', '')),
        ('<form class="m5-form m5-inline" data-api="m5636/corporate/bibliotheque/retirer" data-reload="1" data-confirm="Retirer ce modèle de la '
         'bibliothèque ?"><input type="hidden" name="modele" value="%s"><button class="ax-btn ghost" type="submit">Retirer ce modèle</button></form>'
         % e(t['id'], quote=True)) if t['origine'] == 'cabinet' else '')
    head = ('<p><a href="%s">← Bibliothèque de modèles</a></p><section class="ax-card"><h2>%s %s</h2><p class="ax-muted"><span>%s</span> — <span>%s</span></p>'
            '<p class="ax-muted">Projet à relire : vérifiez les références légales à la date de l’acte et adaptez les clauses au dossier.</p>'
            '<details><summary>Aperçu de la trame</summary><pre class="b5639-preview">%s</pre></details></section>') % (
        e(_url(prefix, cid, droit=t['droit']), quote=True), e(t['titre']), _badges(t), e(DROITS.get(t['droit'], '')), e(_resume(t, lang)), e(preview))
    return head + form + custom


# ------------------------------------------------------------------------------------------------ API
def api_post(desk, route, data, owner):
    if route == 'corporate/bibliotheque/generer':
        return generate(desk, data, owner)
    if route == 'corporate/bibliotheque/personnaliser':
        return customize(desk, data, owner)
    if route == 'corporate/bibliotheque/retablir':
        return restore(desk, data, owner)
    if route == 'corporate/bibliotheque/word':
        return set_word(desk, data, owner)
    if route == 'corporate/bibliotheque/ajouter':
        return add(desk, data, owner)
    if route == 'corporate/bibliotheque/retirer':
        return remove(desk, data, owner)
    return None


def api_get(desk, route, args, owner='cabinet'):
    if route == 'corporate/bibliotheque/fichier':
        return download(desk, args.get('jeton'), owner)
    if route == 'corporate/bibliotheque/word':
        return word_template(desk, args.get('modele'))
    if route == 'corporate/bibliotheque':
        lang = _lang()
        return {'modeles': [{'id': t['id'], 'titre': t['titre'], 'droit': t['droit'], 'categorie': t['categorie'], 'resume': _resume(t, lang),
                             'personnalise': t['personnalise'], 'origine': t['origine']} for t in catalog(desk)]}
    return None

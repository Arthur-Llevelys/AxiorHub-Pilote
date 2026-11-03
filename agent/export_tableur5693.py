"""5.6.93 : lecteur d'export des classeurs XLSX vers CSV (audit 5.6.90, N09).

L'aperçu (``apercu5679.xlsx_sheets``) reste un lecteur borné : 200 lignes, 40 colonnes, pour regarder. L'export ne le réutilise plus. Il lit la feuille
en flux, sans limite d'aperçu, et rend TOUTE la plage utilisée, ou refuse explicitement (``export_trop_volumineux``) : jamais une conversion tronquée.

- **Portée choisie** : le format demandé porte la portée. ``feuilleN.csv`` exporte la feuille N ; ``feuilles.zip`` exporte toutes les feuilles, un CSV
  par feuille et un manifeste. L'ancien ``csv`` (première feuille) n'est plus proposé ; appelé directement, il exporte la feuille unique et refuse un
  classeur à plusieurs feuilles (``feuille_a_choisir``) au lieu de prendre la première en silence.
- **Preuve de complétude** : chaque CSV produit porte son manifeste (feuille, plage, lignes, colonnes, cellules, empreinte, statut). La vérification
  relit le CSV et le rapproche de ce manifeste ; un statut autre que « complet » n'est jamais accepté, donc jamais « Exporté ».
- **Valeurs** : celles enregistrées dans le classeur. Dates converties selon leur format (système 1900 ou 1904) ; une formule sans valeur enregistrée
  est reprise en texte, neutralisée, et comptée ; les cellules qui commenceraient comme une formule sont neutralisées (garde de 5.6.89 conservée).
  Le CSV ne conserve ni les styles ni les formules actives : l'interface le dit.
- **Anciens exports** : un CSV exporté avant cette version n'a pas de preuve. Il est signalé « à recontrôler », conservé tel quel, jamais certifié ;
  un nouvel export crée un nouveau fichier sous un autre nom.

Aucune DTD ni entité acceptée dans les parties XML ; tout est lu par blocs, sans bibliothèque externe.
"""
from collections import OrderedDict
import csv
from datetime import datetime, timedelta
from html import escape as e
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import xml.parsers.expat as expat
import zipfile

from .common import Stop, xml_bytes

# Limites de l'export : celles d'Excel pour la grille, et une taille maximale de fichier produit. Au-delà : refus explicite, jamais de troncature.
LIMITE_LIGNES = 1_048_576
LIMITE_COLONNES = 16_384
LIMITE_OCTETS = 256 * 1024 * 1024
LIMITE_XML = 1024 * 1024 * 1024          # partie XML décompressée lue en flux (classeur piégé)
SEPARATEUR = ';'
MANIFESTE = 'manifeste.json'
AVERTISSEMENT = ('Le CSV ne conserve que les valeurs enregistrées : ni les styles ni les formules actives. Une formule sans valeur enregistrée est reprise en texte, '
                 'neutralisée. Chaque feuille est contrôlée (plage, lignes, colonnes) ; un classeur trop grand est refusé, jamais tronqué.')
JETON = re.compile(r'feuille([1-9]\d{0,3})\.csv|feuilles\.zip')
NUMERIQUE = re.compile(r'-?\d+(\.\d+)?([eE][+-]?\d+)?')
DANGEREUX = ('=', '+', '-', '@', '\t', '\r')
NS_REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
DATES_INTEGREES = {**{i: 'date' for i in (14, 15, 16, 17, *range(27, 37), *range(50, 59))}, **{i: 'heure' for i in (18, 19, 20, 21, 45, 46, 47)}, 22: 'dateheure'}
_PREUVES = OrderedDict()                 # preuve par empreinte : la relecture du fichier écrit retrouve le manifeste de sa production


class Sortie(bytes):
    """Octets produits, avec ``preuve`` : le manifeste de leur complétude."""
    preuve = None


class Formats(list):
    """Formats proposés pour un classeur : la portée (une feuille, toutes) fait partie du format demandé ; toute feuille ``feuilleN.csv`` est admise,
    son existence est contrôlée à la conversion."""

    def __contains__(self, fmt):
        return list.__contains__(self, fmt) or (est_jeton(fmt) and any(est_jeton(x) for x in self))


def est_jeton(fmt):
    return bool(JETON.fullmatch(str(fmt or '')))


def portee(fmt):
    """('unique', None) pour l'ancien « csv », ('feuille', N), ('toutes', None) ; None si ce n'est pas un export de classeur."""
    fmt = str(fmt or '')
    if fmt == 'csv':
        return 'unique', None
    m = JETON.fullmatch(fmt)
    if not m:
        return None
    return ('feuille', int(m.group(1))) if m.group(1) else ('toutes', None)


def _colonne(lettres):
    n = 0
    for ch in lettres.upper():
        n = n * 26 + ord(ch) - 64
    return n


def lettres(n):
    out = ''
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _local(name):
    return name.rpartition(':')[2]


def _flux(z, name, start, end, data):
    """Lecture en flux d'une partie XML : DTD et entités refusées, taille décompressée bornée. Générateur : rend la main après chaque bloc."""
    parser = expat.ParserCreate()

    def refuse(*args):
        raise Stop('classeur_refuse')
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = data
    parser.buffer_text = True
    lus = 0
    try:
        with z.open(name) as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                lus += len(chunk)
                if lus > LIMITE_XML:
                    raise Stop('export_trop_volumineux')
                parser.Parse(chunk, False)
                yield
            parser.Parse(b'', True)
    except expat.ExpatError:
        raise Stop('classeur_illisible') from None
    yield


def _nature_format(code):
    """'date', 'heure', 'dateheure' ou '' d'après un code de format personnalisé ; une durée ([h]) reste un nombre."""
    low = str(code or '').lower()
    if re.search(r'\[(h+|m+|s+)\]', low):
        return ''
    c = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]|_.|\*.', '', low)
    heure = bool(re.search(r'[hs]', c))
    date = bool(re.search(r'[dy]', c)) or ('m' in c and not heure)
    return 'dateheure' if date and heure else 'date' if date else 'heure' if heure else ''


def _date(valeur, nature, date1904):
    try:
        x = float(valeur)
    except ValueError:
        return None
    if not 0 <= x < 2958466:
        return None
    base = datetime(1904, 1, 1) if date1904 else (datetime(1899, 12, 31) if x < 60 else datetime(1899, 12, 30))
    d = base + timedelta(seconds=round(x * 86400))
    return d.strftime('%Y-%m-%d') if nature == 'date' else d.strftime('%H:%M:%S') if nature == 'heure' else d.strftime('%Y-%m-%d %H:%M:%S')


def _garde(valeur, numerique):
    """Garde de 5.6.89 (``livrables_export5689._garde_cellule``) : seul un nombre pur, qui ne peut pas s'exécuter, en est dispensé."""
    from .livrables_export5689 import _garde_cellule
    if numerique and NUMERIQUE.fullmatch(valeur):
        return valeur
    return _garde_cellule(valeur)


class _Classeur:
    """Feuilles, chaînes partagées et styles de date d'un classeur ouvert."""

    def __init__(self, z):
        self.z = z
        names = set(z.namelist())
        book = xml_bytes(z.read('xl/workbook.xml'))
        pr = book.find(NS + 'workbookPr')
        self.date1904 = pr is not None and str(pr.get('date1904', '')).lower() in ('1', 'true')
        links = {}
        if 'xl/_rels/workbook.xml.rels' in names:
            links = {r.get('Id'): r.get('Target', '') for r in xml_bytes(z.read('xl/_rels/workbook.xml.rels'))}
        self.feuilles = []
        for i, sheet in enumerate(book.iter(NS + 'sheet'), 1):
            target = links.get(sheet.get(NS_REL), '')
            part = target.lstrip('/') if target.startswith('/') else 'xl/' + target.removeprefix('xl/')
            if part not in names:
                raise Stop('classeur_incomplet')          # une feuille annoncée mais absente : pas d'export partiel silencieux
            self.feuilles.append({'index': i, 'nom': sheet.get('name') or 'Feuille %d' % i, 'partie': part, 'masquee': sheet.get('state', 'visible') != 'visible'})
        self.dates = self._styles(names)
        self.partagees = self._partagees(names)

    def _styles(self, names):
        if 'xl/styles.xml' not in names:
            return {}
        root = xml_bytes(self.z.read('xl/styles.xml'))
        codes = {int(n.get('numFmtId', -1)): n.get('formatCode', '') for n in root.iter(NS + 'numFmt') if str(n.get('numFmtId', '')).isdigit()}
        xfs = root.find(NS + 'cellXfs')
        out = {}
        for i, xf in enumerate(xfs.findall(NS + 'xf') if xfs is not None else []):
            fid = int(xf.get('numFmtId', 0)) if str(xf.get('numFmtId', '0')).isdigit() else 0
            nature = _nature_format(codes[fid]) if fid in codes else DATES_INTEGREES.get(fid, '')
            if nature:
                out[i] = nature
        return out

    def _partagees(self, names):
        if 'xl/sharedStrings.xml' not in names:
            return []
        out, cur, etat = [], [], {'t': False, 'rph': 0}

        def start(name, attrs):
            n = _local(name)
            if n == 'si':
                cur.clear()
            elif n == 'rPh':
                etat['rph'] += 1
            elif n == 't' and not etat['rph']:
                etat['t'] = True

        def end(name):
            n = _local(name)
            if n == 'si':
                out.append(''.join(cur))
            elif n == 'rPh':
                etat['rph'] -= 1
            elif n == 't':
                etat['t'] = False

        def data(text):
            if etat['t']:
                cur.append(text)
        for _ in _flux(self.z, 'xl/sharedStrings.xml', start, end, data):
            pass
        return out

    def csv(self, feuille):
        """(octets CSV, entrée du manifeste) : la feuille entière, ou Stop explicite."""
        lecteur = _Feuille(self)
        out = io.BytesIO()
        out.write('﻿'.encode('utf-8'))           # accents lisibles à l'ouverture dans un tableur
        tampon = io.StringIO()
        writer = csv.writer(tampon, delimiter=SEPARATEUR, lineterminator='\r\n')
        ecrites = 0

        def vider():
            out.write(tampon.getvalue().encode('utf-8'))
            tampon.seek(0)
            tampon.truncate()
            if out.tell() > LIMITE_OCTETS:
                raise Stop('export_trop_volumineux')
        for _ in _flux(self.z, feuille['partie'], lecteur.start, lecteur.end, lecteur.data):
            for numero, cells in lecteur.pretes:
                if numero <= ecrites:
                    raise Stop('classeur_illisible')     # lignes hors d'ordre : aucun réordonnancement deviné
                for _vide in range(ecrites + 1, numero):
                    writer.writerow([])
                writer.writerow([cells.get(c, '') for c in range(1, max(cells) + 1)])
                ecrites = numero
                lecteur.colonnes = max(lecteur.colonnes, max(cells))
                lecteur.cellules += len(cells)
            lecteur.pretes.clear()
            vider()
        vider()
        data = out.getvalue()
        entree = {'index': feuille['index'], 'nom': feuille['nom'], 'masquee': feuille['masquee'], 'plage': 'A1:%s%d' % (lettres(lecteur.colonnes), ecrites) if ecrites else '',
                  'lignes': ecrites, 'colonnes': lecteur.colonnes, 'cellules': lecteur.cellules, 'formules_sans_valeur': lecteur.sans_valeur, 'cellules_sans_valeur': lecteur.refs_sans_valeur,
                  'formules_neutralisees': lecteur.neutralisees, 'dates': lecteur.dates, 'sha256': hashlib.sha256(data).hexdigest(), 'octets': len(data), 'statut': 'complet'}
        return data, entree


class _Feuille:
    """Automate de lecture d'une feuille : rend les lignes non vides (numéro, {colonne: valeur affichable et gardée})."""

    def __init__(self, classeur):
        self.c = classeur
        self.pretes, self.ligne, self.derniere, self.col = [], None, 0, 0
        self.colonnes = self.cellules = self.sans_valeur = self.neutralisees = self.dates = 0
        self.refs_sans_valeur = []
        self.cell = None
        self.mode, self.rph, self.inline = None, 0, False

    def start(self, name, attrs):
        n = _local(name)
        if n == 'row':
            r = str(attrs.get('r') or '')
            self.ligne = int(r) if r.isdigit() and int(r) > 0 else self.derniere + 1
            if self.ligne > LIMITE_LIGNES:
                raise Stop('export_trop_volumineux')
            self.cells, self.col = {}, 0
        elif n == 'c' and self.ligne is not None:
            m = re.match(r'([A-Za-z]{1,3})\d*$', str(attrs.get('r') or ''))
            self.col = _colonne(m.group(1)) if m else self.col + 1
            if self.col > LIMITE_COLONNES:
                raise Stop('export_trop_volumineux')
            self.cell = {'t': attrs.get('t', 'n'), 's': attrs.get('s'), 'v': None, 'f': None, 'si': attrs.get('si'), 'texte': [], 'fbuf': [], 'vbuf': []}
        elif self.cell is None:
            return
        elif n == 'v':
            self.mode = 'v'
        elif n == 'f':
            self.mode = 'f'
            self.cell['f'] = ''
            self.cell['si'] = attrs.get('si', self.cell['si'])
        elif n == 'is':
            self.inline = True
        elif n == 'rPh':
            self.rph += 1
        elif n == 't' and self.inline and not self.rph:
            self.mode = 't'

    def end(self, name):
        n = _local(name)
        cell = self.cell
        if n == 'v' and cell is not None:
            cell['v'] = ''.join(cell['vbuf'])
            self.mode = None
        elif n == 'f' and cell is not None:
            cell['f'] = ''.join(cell['fbuf'])
            self.mode = None
        elif n == 't':
            self.mode = None
        elif n == 'rPh':
            self.rph -= 1
        elif n == 'is':
            self.inline = False
        elif n == 'c' and cell is not None:
            value = self._valeur(cell)
            if value != '':
                self.cells[self.col] = value
            self.cell = None
        elif n == 'row' and self.ligne is not None:
            if self.cells:
                self.pretes.append((self.ligne, self.cells))
            self.derniere, self.ligne = self.ligne, None

    def data(self, text):
        if self.cell is None or not self.mode:
            return
        self.cell[{'v': 'vbuf', 'f': 'fbuf', 't': 'texte'}[self.mode]].append(text)

    def _valeur(self, cell):
        t, v = cell['t'], cell['v']
        numerique = False
        if cell['f'] is not None and v is None and t != 'inlineStr':
            self.sans_valeur += 1               # valeur calculée jamais enregistrée : la formule reste visible, inerte, et comptée
            if len(self.refs_sans_valeur) < 50:
                self.refs_sans_valeur.append('%s%d' % (lettres(self.col), self.ligne))
            value = '=' + (cell['f'] or '(formule partagée %s)' % (cell['si'] or ''))
        elif t == 'inlineStr':
            value = ''.join(cell['texte'])
        elif t == 's':
            if not str(v or '').isdigit() or int(v) >= len(self.c.partagees):
                raise Stop('classeur_illisible')  # renvoi vers une chaîne absente : pas de cellule vide inventée
            value = self.c.partagees[int(v)]
        elif t == 'b':
            value = {'1': 'VRAI', '0': 'FAUX'}.get(v or '', v or '')
        elif t in ('str', 'e', 'd'):
            value = v or ''
        else:
            value, numerique = v or '', True
            nature = self.c.dates.get(int(cell['s'])) if str(cell['s'] or '').isdigit() else ''
            if nature and value:
                converted = _date(value, nature, self.c.date1904)
                if converted is not None:
                    value, numerique = converted, False
                    self.dates += 1
        if value == '':
            return ''
        garde = _garde(value, numerique)
        if garde != value:
            self.neutralisees += 1
        return garde


def noms_feuilles(raw):
    """Noms des feuilles, dans l'ordre du classeur, pour le choix de la portée ; [] si le classeur ne se lit pas (l'export le dira)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            return [n.get('name') or 'Feuille %d' % i for i, n in enumerate(xml_bytes(z.read('xl/workbook.xml')).iter(NS + 'sheet'), 1)]
    except (zipfile.BadZipFile, KeyError, ValueError, Stop):
        return []


def _nom_csv(feuille):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', feuille['nom']).strip(' .')[:80] or 'Feuille'
    return '%02d - %s.csv' % (feuille['index'], base)


def _retenir(data, preuve):
    sortie = Sortie(data)
    sortie.preuve = preuve
    _PREUVES[hashlib.sha256(data).hexdigest()] = preuve
    while len(_PREUVES) > 64:
        _PREUVES.popitem(last=False)
    return sortie


def exporter(raw, fmt):
    """Export complet de la portée demandée : ``Sortie`` (octets + preuve), ou Stop explicite. Le classeur d'origine n'est jamais modifié."""
    choix = portee(fmt)
    if not choix:
        raise Stop('format_non_disponible')
    kind, numero = choix
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            book = _Classeur(z)
            if not book.feuilles:
                raise Stop('classeur_illisible')
            if kind == 'unique':
                if len(book.feuilles) > 1:
                    raise Stop('feuille_a_choisir')
                retenues = book.feuilles
            elif kind == 'feuille':
                if numero > len(book.feuilles):
                    raise Stop('feuille_absente')
                retenues = [book.feuilles[numero - 1]]
            else:
                retenues = book.feuilles
            produits, total = [], 0
            for feuille in retenues:
                data, entree = book.csv(feuille)
                total += len(data)
                if total > LIMITE_OCTETS:
                    raise Stop('export_trop_volumineux')
                produits.append((feuille, data, entree))
    except (zipfile.BadZipFile, KeyError, ValueError, EOFError, zipfile.LargeZipFile):
        raise Stop('classeur_illisible') from None
    preuve = {'version': 1, 'format': str(fmt), 'source_sha256': hashlib.sha256(raw).hexdigest(), 'feuilles_du_classeur': len(book.feuilles), 'separateur': SEPARATEUR,
              'encodage': 'UTF-8 avec marque d’ordre', 'avertissement': AVERTISSEMENT, 'statut': 'complet', 'feuilles': [entree for _, _, entree in produits]}
    if kind != 'toutes':
        preuve['feuilles'][0]['fichier'] = ''
        return _retenir(produits[0][1], preuve)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for feuille, data, entree in produits:
            entree['fichier'] = _nom_csv(feuille)
            z.writestr(zipfile.ZipInfo(entree['fichier'], (1980, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)   # date fixe : même classeur, même archive
        z.writestr(zipfile.ZipInfo(MANIFESTE, (1980, 1, 1, 0, 0, 0)), json.dumps(preuve, ensure_ascii=False, indent=1, sort_keys=True).encode('utf-8'), zipfile.ZIP_DEFLATED)
    return _retenir(out.getvalue(), preuve)


def _controle_csv(data, entree):
    """Rapproche un CSV de son entrée de manifeste : empreinte, lignes, colonnes, cellules, statut, aucune formule active."""
    if entree.get('statut') != 'complet' or hashlib.sha256(data).hexdigest() != entree.get('sha256'):
        return False
    try:
        text = bytes(data).decode('utf-8')
    except UnicodeDecodeError:
        return False
    rows = list(csv.reader(io.StringIO(text.removeprefix('﻿'), newline=''), delimiter=SEPARATEUR))
    if len(rows) != entree.get('lignes') or max((len(r) for r in rows), default=0) != entree.get('colonnes'):
        return False
    if sum(1 for r in rows for c in r if c != '') != entree.get('cellules'):
        return False
    return not any(c[:1] in DANGEREUX and not NUMERIQUE.fullmatch(c) for r in rows for c in r)


def verifier(fmt, data, preuve=None):
    """Vérification d'un export de classeur : la portée demandée, rapprochée de la preuve. Sans preuve, rien n'est réputé complet."""
    choix = portee(fmt)
    if not choix or not data:
        return False
    if choix[0] == 'toutes':
        return _controle_zip(data)
    preuve = preuve or getattr(data, 'preuve', None) or _PREUVES.get(hashlib.sha256(data).hexdigest())
    if not preuve or preuve.get('statut') != 'complet' or len(preuve.get('feuilles') or []) != 1:
        return False
    entree = preuve['feuilles'][0]
    if choix[0] == 'feuille' and entree.get('index') != choix[1]:
        return False
    if choix[0] == 'unique' and preuve.get('feuilles_du_classeur') != 1:
        return False
    return _controle_csv(data, entree)


def _controle_zip(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.testzip() is not None or sum(i.file_size for i in z.infolist()) > LIMITE_OCTETS + 16 * 1024 * 1024:
                return False
            preuve = json.loads(z.read(MANIFESTE).decode('utf-8'))
            feuilles = preuve.get('feuilles') or []
            if preuve.get('statut') != 'complet' or len(feuilles) != preuve.get('feuilles_du_classeur') or not feuilles:
                return False
            fichiers = [f.get('fichier') for f in feuilles]
            if len(set(fichiers)) != len(fichiers) or set(z.namelist()) != set(fichiers) | {MANIFESTE} or len(z.namelist()) != len(fichiers) + 1:
                return False
            return all(_controle_csv(z.read(f['fichier']), f) for f in feuilles)
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, AttributeError):
        return False


# ------------------------------------------------------------------------------------------------ anciens exports et interface
def a_recontroler(desk, document_id=None):
    """Exports CSV d'un classeur faits avant 5.6.93 (format « csv » : première feuille, lecteur d'aperçu borné) : aucune preuve de complétude.
    Signalés à recontrôler, conservés tels quels, jamais certifiés ; ``remplace_par`` nomme l'export contrôlé plus récent de la même version, s'il existe."""
    from .livrables_export5689 import schema
    schema(desk)
    sql = ("SELECT e.* FROM prepared_exports5689 e JOIN prepared_items5689 i ON i.id=e.item_id WHERE e.format='csv' AND e.state='exporte' AND lower(i.path) LIKE '%.xlsx'"
           + (' AND e.document_id=?' if document_id else '') + ' ORDER BY e.created')
    out = []
    for row in desk.db.execute(sql, (str(document_id),) if document_id else ()):
        newer = desk.db.execute("SELECT output_path FROM prepared_exports5689 WHERE document_id=? AND version_id=? AND state='exporte' AND (format LIKE 'feuille%.csv' OR format='feuilles.zip') "
                                'ORDER BY updated DESC LIMIT 1', (row['document_id'], row['version_id'])).fetchone()
        out.append({'id': row['id'], 'document_id': row['document_id'], 'version_id': row['version_id'], 'fichier': PurePosixPath(row['output_path']).name, 'exporte_le': row['updated'],
                    'remplace_par': PurePosixPath(newer[0]).name if newer else ''})
    return out


def options_html(allowed, feuilles=()):
    """Options du choix de format : pour un classeur, une option par feuille, nommée, et l'archive de toutes les feuilles."""
    out = []
    for f in allowed:
        if not est_jeton(f):
            out.append('<option value="%s">%s</option>' % (e(f, quote=True), e(f.upper())))
    if any(est_jeton(f) for f in allowed):
        noms = list(feuilles or [])
        if noms:
            out += ['<option value="feuille%d.csv">CSV — feuille « %s »</option>' % (i, e(n)) for i, n in enumerate(noms, 1)]
        else:
            out.append('<option value="feuille1.csv">CSV — première feuille</option>')
        if len(noms) != 1:
            out.append('<option value="feuilles.zip">ZIP — toutes les feuilles (un CSV par feuille et un manifeste)</option>')
    return ''.join(out)


def notes_html(desk, row, allowed):
    """Ce que le CSV garde ou non, et les anciens exports à recontrôler."""
    if not any(est_jeton(f) for f in allowed):
        return ''
    parts = ['<p class="ax-muted">%s</p>' % e(AVERTISSEMENT)]
    for old in a_recontroler(desk, row['document_id']):
        suite = (' Export contrôlé plus récent : %s.' % old['remplace_par']) if old['remplace_par'] else ' Refaites l’export : le nouveau fichier prendra un autre nom.'
        parts.append('<p class="ax-muted">Export CSV antérieur « %s » sans preuve de complétude : à recontrôler. Le fichier est conservé tel quel.%s</p>' % (e(old['fichier']), e(suite)))
    return ''.join(parts)

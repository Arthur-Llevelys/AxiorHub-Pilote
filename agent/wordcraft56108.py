"""5.6.108 (lot W) : modifier un .docx sans serveur de documents — éditeur intégré WordCraft (version d'essai, facultatif) et contrôle commun.

WordCraft 0.18.1 (édition web, MIT ou Apache-2.0 au choix, « alpha » selon ses auteurs) est servi par AxiorHub sous
``/static/wordcraft-0.18.1/`` (fichiers de l'archive inchangés, empreintes ci-dessous, licences et NOTICE dans le dossier). Audit avant
intégration (``docs/WORDCRAFT-FIDELITE-5.6.108.md``) : la colle JavaScript ne donne au WebAssembly ni ``fetch`` ni ``XMLHttpRequest`` ni
``WebSocket`` (seul ``init`` lit le .wasm, à l'adresse que nous lui passons) ; la vérification des mises à jour est absente de la version web ;
les seules sorties possibles sont des liens ouverts par un clic (aide, site de l'éditeur, liens du document), neutralisées dans le cadre
(``window.open`` remplacé avant le démarrage). La CSP d'AxiorHub (``connect-src 'self'``, ``script-src 'self' 'wasm-unsafe-eval'``) reste
inchangée : WordCraft y fonctionne tel quel (mesuré par un vrai navigateur).

Parcours (page ``/editeur/wordcraft?s=<source>&p=<chemin>``) :
1. **Garde avant ouverture** (``analyser`` puis ``garde``) : analyse déterministe du .docx (modifications suivies, commentaires, champs,
   contrôles de contenu, numérotation, notes, sections, en-têtes et pieds, objets incorporés…) ; ce que WordCraft abîme d'après la mesure de
   fidélité (``FIDELITE``) donne un avertissement clair et propose le serveur de documents (s'il est relié) ou Word sur le poste ; jamais
   d'ouverture silencieuse : « Ouvrir quand même » est un choix explicite, tracé.
2. **Le document passe par AxiorHub** : la source de la page « Fichiers » (``fichiers56103.ouvrir``) contrôle racines et dossiers réservés
   (même 404 neutre qu'un fichier absent ; registre illisible : 503) avant toute lecture ; l'original est copié dans un dossier privé de
   l'application (séance) ; le navigateur reçoit ces octets et renvoie chaque enregistrement par POST (Origin et CSRF, corps borné, clé
   d'idempotence, révision attendue → 409).
3. **Jamais d'écrasement** : un enregistrement est d'abord un *candidat* rangé dans la séance ; « Garder cette version » crée un fichier voisin
   nommé clairement (« … (modifié dans WordCraft 18-11-2026 14h05).docx »), en création exclusive, relu et comparé par empreinte ; l'original
   reste intact. « Abandonner cette version » efface le candidat.
4. **Contrôle après enregistrement** (``controler``) : texte paragraphe par paragraphe, nombre de paragraphes, styles, numérotation, tableaux,
   en-têtes et pieds, notes, suivi des modifications, commentaires, champs… ; toute perte est affichée AVANT « Garder ».

Le même contrôle et la même décision servent à « Ouvrir dans Word / LibreOffice » (``ouvrir_word56108``, postes seulement).
Aucun modèle d'IA n'intervient ; aucune donnée ne sort du poste ou du serveur.
"""
import difflib
from datetime import datetime, timezone
import hashlib
from html import escape as e
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import time
import zipfile
import xml.etree.ElementTree as ET

from .common import Stop

VERSION = '0.18.1'
DOSSIER_STATIQUE = 'wordcraft-%s' % VERSION
GLUE = 'wordcraft-web-bc9cdbc0b42827ee.js'
WASM = 'wordcraft-web-bc9cdbc0b42827ee_bg.wasm'
SNIPPET = 'snippets/eframe-d31a8450ea12dfc1/inline0.js'
# Fichiers servis (adresse relative → (type, SHA-256)) : seuls ceux-ci sont déclarés dans ``web440.STATIC`` ; les autres fichiers de l'archive
# (licences, NOTICE, ATTRIBUTION, HOSTING, index.html…) accompagnent le paquet sans être servis.
FICHIERS = {
    GLUE: ('text/javascript', '0454cb6cd67a4d4d39fe731660bf6c47ada739611e0a46af6291a9277831b957'),
    SNIPPET: ('text/javascript', '22a98f9d0c5a0532d291bdc9a3050439420b3d11143b4568fcfd3c66fff36995'),
    WASM: ('application/wasm', 'e0bf7525b3553f38c4864794f6ec92bf4a4ebc7872b55ad7ac417eb22ba53bce'),
}
LICENCES = ('LICENSE-MIT', 'LICENSE-APACHE', 'NOTICE', 'ATTRIBUTION.md')
ARCHIVE_SHA256 = '3daa7c2d1f209a7eeb2527fcefa3fe31e875848fb1970d68f54edae55fe0bc9f'

REGLAGE = 'wordcraft56108:reglages'
DEFAUT = {'active': True}           # mesure de fidélité bonne sur les actes courants (docs/WORDCRAFT-FIDELITE-5.6.108.md) : proposé, « version d'essai »
ROLES_EDITION = ('administrateur', 'avocat', 'assistant')
MAX_DOCX = 20_000_000               # octets d'un .docx ouvert ou enregistré (plafond des fichiers générés du stockage)
MAX_PARTIE = 40_000_000             # octets décompressés d'une partie XML lue
MAX_TOTAL = 200_000_000             # octets décompressés de l'archive (bombe de décompression)
MAX_ENTREES = 5000
SEANCE_JOURS = 7                    # séances et copies privées nettoyées au-delà (maintenance)
DETAILS_MAX = 40                    # paragraphes cités dans le contrôle

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
V = '{urn:schemas-microsoft-com:vml}'
O = '{urn:schemas-microsoft-com:office:office}'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS editeur56108_seances(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, outil TEXT NOT NULL, source TEXT NOT NULL,
  chemin TEXT NOT NULL, etag TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL, taille INTEGER NOT NULL, garde TEXT NOT NULL DEFAULT '{}',
  etat TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, candidat_rev INTEGER NOT NULL DEFAULT 0, candidat_sha TEXT NOT NULL DEFAULT '',
  rapport TEXT NOT NULL DEFAULT '{}', version_chemin TEXT NOT NULL DEFAULT '', travail TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL,
  maj_le TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS editeur56108_seances_acteur ON editeur56108_seances(acteur, etat);
CREATE TABLE IF NOT EXISTS editeur56108_ops(acteur TEXT NOT NULL, cle TEXT NOT NULL, nature TEXT NOT NULL, empreinte TEXT NOT NULL,
  etat TEXT NOT NULL, resultat TEXT NOT NULL DEFAULT '{}', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, PRIMARY KEY(acteur, cle));
'''
# 5.6.109 (C07) : écritures à résultat incertain, dans la table existante (aucune table parallèle). Une décision « Garder » a une IDENTITÉ
# (séance + révision du candidat), unique dans la base : deux clés de transport ne créent jamais deux versions de la même décision. La cible
# (nom du fichier voisin) est choisie et enregistrée AVANT l'effet, avec l'empreinte du candidat ; ``rapprochement`` suit l'écriture :
# ``reservee`` (cible figée, rien d'écrit), ``engagee`` (création lancée), ``verifiee`` (cible relue, conforme : reçu), ``a_verifier`` (réponse
# ou relecture perdue, cible absente ou différente : jamais une nouvelle cible automatique), ``abandonnee``, ``expiree``. ``maj_le`` sert de
# bail court entre deux requêtes de la même décision.
COLONNES_OPS_56109 = (('decision', "TEXT NOT NULL DEFAULT ''"), ('seance', "TEXT NOT NULL DEFAULT ''"), ('revision', 'INTEGER NOT NULL DEFAULT 0'),
                      ('cible', "TEXT NOT NULL DEFAULT ''"), ('candidat_sha', "TEXT NOT NULL DEFAULT ''"),
                      ('rapprochement', "TEXT NOT NULL DEFAULT ''"), ('motif', "TEXT NOT NULL DEFAULT ''"))
INDEX_DECISION_56109 = "CREATE UNIQUE INDEX IF NOT EXISTS editeur56108_ops_decision ON editeur56108_ops(decision) WHERE decision<>''"
INCERTAINS = ('reservee', 'engagee', 'a_verifier')
BAIL_ECRITURE = 90                  # secondes pendant lesquelles une décision réservée ou engagée appartient à la requête qui l'a prise
# Refus du stockage AVANT tout effet (rien n'a pu être écrit) : échec franc, la décision est libérée. Toute autre erreur est incertaine.
REFUS_CERTAINS = frozenset(('contenu_fichier_invalide', 'fichier_trop_volumineux', 'introuvable', 'non_pris_en_charge',
                            'nextcloud_ou_dossier_local_a_configurer', 'chemin_est_un_dossier'))
MOTIFS_INCERTAINS = {
    'relecture_impossible': 'À vérifier : « %s » a été écrit, mais sa relecture de contrôle n’a pas abouti. Rien d’autre ne sera écrit : '
                            '« Vérifier » relit ce même fichier.',
    'cible_absente': 'À vérifier : l’écriture de « %s » n’a pas pu être confirmée et le fichier n’est pas dans le dossier pour le moment. '
                     '« Vérifier » le relit ; « Réessayer l’écriture » l’écrit de nouveau sous le même nom.',
    'cible_differente': 'À vérifier : un fichier nommé « %s » existe avec un autre contenu que votre version. Rien n’est remplacé ni écrit '
                        'ailleurs : contrôlez ce fichier dans le dossier ; votre version reste conservée ici.',
    'resultat_inconnu': 'À vérifier : le résultat de l’écriture de « %s » n’est pas connu. « Vérifier » relit ce même fichier.',
}


# ------------------------------------------------------------------------------------------------------------------- schéma et réglage
def ensure_schema(desk):
    """Tables du lot (démarrage, ``Desk._prepare``) ; sans changement, aucune écriture (recette 5.6.44)."""
    noms = re.findall(r'CREATE\s+(?:TABLE|INDEX)\s+IF\s+NOT\s+EXISTS\s+(\w+)', SCHEMA)
    existants = {r[0] for r in desk.db.execute('SELECT name FROM sqlite_master')}
    if any(n not in existants for n in noms):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()
    # 5.6.109 (C07) : colonnes de rapprochement et unicité de la décision, ajoutées une fois ; les reçus existants sont gardés tels quels (une
    # ancienne opération restée « en cours » ne devient pas un échec qui autoriserait un nouvel effet).
    colonnes = {r[1] for r in desk.db.execute('PRAGMA table_info(editeur56108_ops)')}
    manquantes = [(n, d) for n, d in COLONNES_OPS_56109 if n not in colonnes]
    if manquantes or 'editeur56108_ops_decision' not in existants:
        for nom, definition in manquantes:
            desk.db.execute('ALTER TABLE editeur56108_ops ADD COLUMN %s %s' % (nom, definition))
        desk.db.execute(INDEX_DECISION_56109)
        desk.db.commit()


def reglages(desk):
    try:
        brut = desk.settings(REGLAGE, None)
    except Exception:   # noqa: BLE001 — base illisible : rien n'est proposé (jamais un accès élargi sur une panne)
        return {**DEFAUT, 'active': False}
    return {**DEFAUT, **({'active': bool(brut.get('active'))} if isinstance(brut, dict) else {})}


def actif(desk):
    return bool(reglages(desk)['active'])


def regler(desk, acteur, active):
    """Réglage administrateur : WordCraft proposé ou non (le contrôle de rôle est fait par la route)."""
    desk.setting(REGLAGE, {'active': bool(active), 'par': str(acteur[0])[:120], 'le': _maintenant()})
    desk.audit('editeur56108_wordcraft_regle', {'active': bool(active), 'par': str(acteur[0])[:120]})
    return reglages(desk)


def statiques():
    """Entrées de ``web440.STATIC`` : fichiers servis de WordCraft, puis script et feuille du lot."""
    out = {'/static/%s/%s' % (DOSSIER_STATIQUE, nom): kind for nom, (kind, _) in FICHIERS.items()}
    out.update({'/static/v56108-editeur.js': 'text/javascript', '/static/v56108-editeur.css': 'text/css'})
    return out


def dossier_statique():
    return Path(__file__).resolve().parent / 'static' / DOSSIER_STATIQUE


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


# ------------------------------------------------------------------------------------------------------------------- lecture sûre d'un .docx
def _xml(raw):
    if len(raw) > MAX_PARTIE or b'\x00' in raw[:4096] or re.search(br'<!\s*(DOCTYPE|ENTITY)', raw, re.I):
        raise Stop('document_word_illisible')
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise Stop('document_word_illisible') from None


class Paquet:
    """Archive .docx ouverte avec bornes (entrées, tailles décompressées, chemins) ; ``partie(nom)`` rend l'arbre XML ou None."""

    def __init__(self, raw):
        if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_DOCX:
            raise Stop('document_word_trop_volumineux' if raw and len(raw) > MAX_DOCX else 'document_word_illisible')
        if bytes(raw[:2]) != b'PK':
            raise Stop('document_word_illisible')
        try:
            self.z = zipfile.ZipFile(io.BytesIO(bytes(raw)))
            infos = self.z.infolist()
        except (zipfile.BadZipFile, ValueError, OSError):
            raise Stop('document_word_illisible') from None
        if len(infos) > MAX_ENTREES or sum(i.file_size for i in infos) > MAX_TOTAL:
            raise Stop('document_word_illisible')
        self.noms = {i.filename for i in infos if not i.filename.startswith('/') and '..' not in i.filename.split('/')}
        if 'word/document.xml' not in self.noms:
            raise Stop('document_word_illisible')

    def octets(self, nom):
        if nom not in self.noms:
            return None
        info = self.z.getinfo(nom)
        if info.file_size > MAX_PARTIE:
            raise Stop('document_word_trop_volumineux')
        try:
            return self.z.read(info)
        except (zipfile.BadZipFile, RuntimeError, OSError, ValueError):
            raise Stop('document_word_illisible') from None

    def partie(self, nom):
        brut = self.octets(nom)
        return None if brut is None else _xml(brut)


# ------------------------------------------------------------------------------------------------------------------- analyse
CARACTERISTIQUES = (
    # code, libellé, compte (True : un nombre ; une baisse est une perte)
    ('paragraphes', 'Paragraphes'),
    ('styles', 'Styles de paragraphe et de caractère utilisés'),
    ('numerotation', 'Paragraphes numérotés ou à puces'),
    ('tableaux', 'Tableaux'),
    ('cellules', 'Cellules de tableau'),
    ('entetes', 'En-têtes (avec texte)'),
    ('pieds', 'Pieds de page (avec texte)'),
    ('entetes_complexes', 'En-têtes ou pieds complexes (champs, tableaux, images)'),
    ('notes', 'Notes de bas de page et de fin'),
    ('suivi', 'Modifications suivies'),
    ('commentaires', 'Commentaires'),
    ('champs', 'Champs (numéros de page, renvois, tables, fusion…)'),
    ('controles', 'Contrôles de contenu'),
    ('sections', 'Sections'),
    ('objets', 'Objets incorporés (OLE, fragments importés)'),
    ('images', 'Images et dessins'),
    ('zones_texte', 'Zones de texte'),
    ('signets', 'Signets'),
    ('liens', 'Liens hypertexte'),
    ('gras', 'Caractères en gras'),
    ('italique', 'Caractères en italique'),
    ('souligne', 'Caractères soulignés'),
    ('sauts_page', 'Sauts de page'),
    ('macros', 'Macros'),
    ('protection', 'Protection du document'),
    ('champs_date', 'Champs de date (DATE, TIME…)'),
    ('polices_theme', 'Polices liées au thème du document'),
)
LIBELLES = dict(CARACTERISTIQUES)


def _on(rpr, tag):
    el = rpr.find(W + tag) if rpr is not None else None
    return el is not None and str(el.get(W + 'val', 'true')).lower() not in ('0', 'false', 'off', 'none')


def _texte_paragraphe(p):
    """Texte affiché d'un paragraphe : insertions suivies comprises, suppressions suivies exclues, tabulations et retours rendus."""
    out = []
    for n in p.iter():
        tag = n.tag
        if tag == W + 't':
            out.append(n.text or '')
        elif tag == W + 'tab':
            out.append('\t')
        elif tag in (W + 'br', W + 'cr'):
            out.append('\n')
        elif tag == W + 'noBreakHyphen':
            out.append('-')
    return ''.join(out)


def _paragraphes(racine):
    """Paragraphes du corps dans l'ordre (ceux des tableaux compris, pas ceux des zones de texte ni des notes)."""
    corps = racine.find(W + 'body')
    if corps is None:
        return []
    out = []

    def parcourir(noeud):
        for enfant in noeud:
            if enfant.tag == W + 'p':
                out.append(enfant)
            elif enfant.tag in (W + 'tbl', W + 'tr', W + 'tc', W + 'sdt', W + 'sdtContent', W + 'customXml', W + 'smartTag', W + 'ins',
                                W + 'moveTo'):
                parcourir(enfant)
    parcourir(corps)
    return out


def _compte(racine, *tags):
    return sum(1 for n in racine.iter() if n.tag in tags) if racine is not None else 0


def analyser(raw):
    """Caractéristiques déterministes d'un .docx (aucune écriture, aucun réseau). Stop('document_word_illisible') sinon."""
    pq = Paquet(raw)
    doc = pq.partie('word/document.xml')
    if doc is None or doc.find(W + 'body') is None:
        raise Stop('document_word_illisible')
    paras = _paragraphes(doc)
    textes = [_texte_paragraphe(p) for p in paras]
    styles = set()
    numerotes = 0
    for n in doc.iter():
        if n.tag in (W + 'pStyle', W + 'rStyle', W + 'tblStyle'):
            v = n.get(W + 'val')
            if v:
                styles.add(v)
    for p in paras:
        ppr = p.find(W + 'pPr')
        if ppr is not None and ppr.find(W + 'numPr') is not None:
            numid = ppr.find(W + 'numPr').find(W + 'numId')
            if numid is None or numid.get(W + 'val') != '0':
                numerotes += 1
    gras = italique = souligne = 0      # caractères (hors espaces) en mise en forme directe : deux passages fusionnés ne comptent pas comme une perte
    for r in doc.iter(W + 'r'):
        n = len(re.sub(r'\s+', '', ''.join(t.text or '' for t in r.iter(W + 't'))))
        if not n:
            continue
        rpr = r.find(W + 'rPr')
        gras += n * _on(rpr, 'b')
        italique += n * _on(rpr, 'i')
        u = rpr.find(W + 'u') if rpr is not None else None
        souligne += n * (u is not None and u.get(W + 'val', 'single') != 'none')
    entetes = pieds = complexes = 0
    textes_entetes = []
    for nom in sorted(pq.noms):
        m = re.fullmatch(r'word/(header|footer)\d*\.xml', nom)
        if not m:
            continue
        part = pq.partie(nom)
        texte = ' '.join(_texte_paragraphe(p) for p in part.iter(W + 'p')).strip()
        if texte:
            if m.group(1) == 'header':
                entetes += 1
            else:
                pieds += 1
            textes_entetes.append(texte)
        if _compte(part, W + 'fldChar', W + 'fldSimple', W + 'tbl', W + 'drawing', W + 'pict', W + 'sdt'):
            complexes += 1
    notes = _compte(doc, W + 'footnoteReference', W + 'endnoteReference')
    commentaires = _compte(doc, W + 'commentReference')     # commentaires ancrés dans le texte (ceux qui s'affichent)
    suivi = _compte(doc, W + 'ins', W + 'del', W + 'moveFrom', W + 'moveTo', W + 'pPrChange', W + 'rPrChange', W + 'sectPrChange',
                    W + 'tblPrChange', W + 'trPrChange', W + 'tcPrChange', W + 'numberingChange')
    champs = sum(1 for n in doc.iter(W + 'fldChar') if n.get(W + 'fldCharType') == 'begin') + _compte(doc, W + 'fldSimple')
    types_champs = sorted({(m.group(1) or '').upper() for n in doc.iter() if n.tag in (W + 'instrText',)
                           for m in [re.match(r'\s*(\w+)', n.text or '')] if m} |
                          {(m.group(1) or '').upper() for n in doc.iter(W + 'fldSimple') for m in [re.match(r'\s*(\w+)', n.get(W + 'instr') or '')] if m})
    # Champs de date (DATE, TIME, CREATEDATE, SAVEDATE, PRINTDATE), dans le corps et les en-têtes ou pieds : la mesure montre que WordCraft
    # les recalcule à l'ouverture avec des noms de mois anglais (« 18 November 2026 »).
    champs_date = 0
    for racine in [doc] + [pq.partie(n) for n in sorted(pq.noms) if re.fullmatch(r'word/(header|footer)\d*\.xml', n)]:
        consignes = [n.text or '' for n in racine.iter(W + 'instrText')] + [n.get(W + 'instr') or '' for n in racine.iter(W + 'fldSimple')]
        champs_date += sum(1 for c in consignes if re.match(r'\s*(DATE|TIME|CREATEDATE|SAVEDATE|PRINTDATE)\b', c, re.I))
    sections = _compte(doc, W + 'sectPr')
    objets = _compte(doc, W + 'object', W + 'altChunk', O + 'OLEObject') + sum(1 for n in pq.noms if n.startswith('word/embeddings/'))
    images = _compte(doc, W + 'drawing', W + 'pict')
    zones = _compte(doc, W + 'txbxContent')
    signets = sum(1 for n in doc.iter(W + 'bookmarkStart') if not str(n.get(W + 'name', '')).startswith('_'))
    liens = _compte(doc, W + 'hyperlink')
    sauts = sum(1 for n in doc.iter(W + 'br') if n.get(W + 'type') == 'page') + _compte(doc, W + 'pageBreakBefore')
    tableaux = _compte(doc, W + 'tbl')
    cellules = _compte(doc, W + 'tc')
    protection = 0
    reglages_doc = pq.partie('word/settings.xml')
    if reglages_doc is not None and reglages_doc.find(W + 'documentProtection') is not None:
        protection = 1
    macros = 1 if any(n.lower().endswith('vbaproject.bin') for n in pq.noms) else 0
    controles = _compte(doc, W + 'sdt')
    # Mise en page et formes (comparées à l'identique) : format et marges de chaque section, alignements, polices, langues, formats de
    # numérotation des listes employées, parties de l'archive (une partie disparue — macros, objets, données — est une perte).
    pages = []
    for s in doc.iter(W + 'sectPr'):
        sz, mar = s.find(W + 'pgSz'), s.find(W + 'pgMar')
        pages.append('%s×%s %s | %s' % (sz.get(W + 'w', '') if sz is not None else '', sz.get(W + 'h', '') if sz is not None else '',
                                         sz.get(W + 'orient', 'portrait') if sz is not None else '',
                                         ' '.join(mar.get(W + k, '') for k in ('top', 'right', 'bottom', 'left')) if mar is not None else ''))
    alignements = {}
    for jc in doc.iter(W + 'jc'):
        v = jc.get(W + 'val', '')
        alignements[v] = alignements.get(v, 0) + 1
    styles_doc = pq.partie('word/styles.xml')
    polices, langues = set(), set()
    polices_theme = 0
    for racine in (doc, styles_doc):
        if racine is None:
            continue
        for f in racine.iter(W + 'rFonts'):
            if f.get(W + 'ascii'):
                polices.add(f.get(W + 'ascii'))
            polices_theme += any(k.endswith('Theme') or k.endswith('theme') for k in f.attrib)
        for lg in racine.iter(W + 'lang'):
            if lg.get(W + 'val'):
                langues.add(lg.get(W + 'val'))
    formats = set()
    numeros = {n.get(W + 'val') for n in doc.iter(W + 'numId') if n.get(W + 'val') not in (None, '0')}
    numbering = pq.partie('word/numbering.xml') if numeros else None
    if numbering is not None:
        abstraits = {}
        for num in numbering.findall(W + 'num'):
            a = num.find(W + 'abstractNumId')
            if num.get(W + 'numId') in numeros and a is not None:
                abstraits.setdefault(a.get(W + 'val'), []).append(num.get(W + 'numId'))
        niveaux = {}
        for p in paras:
            num = p.find('./' + W + 'pPr/' + W + 'numPr')
            if num is not None and num.find(W + 'numId') is not None:
                ilvl = num.find(W + 'ilvl')
                niveaux.setdefault(num.find(W + 'numId').get(W + 'val'), set()).add(ilvl.get(W + 'val', '0') if ilvl is not None else '0')
        for an in numbering.findall(W + 'abstractNum'):
            for numid in abstraits.get(an.get(W + 'abstractNumId'), ()):
                for lvl in an.findall(W + 'lvl'):        # niveaux réellement employés : un niveau ajouté par l'éditeur n'est pas une perte
                    if lvl.get(W + 'ilvl', '0') not in niveaux.get(numid, ()):
                        continue
                    fmt, txt = lvl.find(W + 'numFmt'), lvl.find(W + 'lvlText')
                    formats.add('liste %s niveau %s : %s %s' % (numid, lvl.get(W + 'ilvl', '0'), fmt.get(W + 'val', '') if fmt is not None else '',
                                                                txt.get(W + 'val', '') if txt is not None else ''))
    parties = sorted(n for n in pq.noms if not n.endswith('/') and not n.startswith(('docProps/', 'word/theme/', 'customXml/_rels')))
    return {'paragraphes': len(paras), 'textes': textes, 'styles': sorted(styles), 'numerotation': numerotes, 'tableaux': tableaux,
            'cellules': cellules, 'entetes': entetes, 'pieds': pieds, 'entetes_complexes': complexes, 'textes_entetes': textes_entetes,
            'notes': notes, 'suivi': suivi, 'commentaires': commentaires, 'champs': champs, 'types_champs': types_champs,
            'controles': controles, 'sections': sections, 'objets': objets, 'images': images, 'zones_texte': zones, 'signets': signets,
            'liens': liens, 'gras': gras, 'italique': italique, 'souligne': souligne, 'sauts_page': sauts, 'macros': macros,
            'protection': protection, 'champs_date': champs_date, 'polices_theme': polices_theme, 'mise_en_page': pages, 'alignements': alignements, 'polices': sorted(polices), 'langues': sorted(langues),
            'formats_numerotation': sorted(formats), 'parties': parties}


# ------------------------------------------------------------------------------------------------------------------- garde avant ouverture
# Résultat de la mesure de fidélité (aller-retour par un vrai navigateur, docs/WORDCRAFT-FIDELITE-5.6.108.md) : caractéristique → état.
# « conserve » : rien n'a été perdu sur les fichiers mesurés ; « altere » : conservé en partie (perte constatée sur au moins un fichier) ;
# « perdu » : retiré ou vidé ; « non_mesure » : absent des fichiers d'essai (prudence : avertissement). Mis à jour par la mesure.
FIDELITE = {
    'paragraphes': 'conserve', 'styles': 'conserve', 'numerotation': 'conserve', 'tableaux': 'conserve', 'cellules': 'conserve',
    'entetes': 'conserve', 'pieds': 'conserve', 'entetes_complexes': 'conserve', 'notes': 'conserve', 'suivi': 'conserve',
    'commentaires': 'conserve', 'champs': 'conserve', 'controles': 'conserve', 'sections': 'conserve', 'objets': 'altere',
    'images': 'conserve', 'zones_texte': 'conserve', 'signets': 'conserve', 'liens': 'conserve', 'gras': 'conserve',
    'italique': 'conserve', 'souligne': 'conserve', 'sauts_page': 'conserve', 'macros': 'perdu', 'protection': 'conserve',
    'champs_date': 'altere', 'polices_theme': 'mineur',
}
# Précisions affichées par la garde (mesure du 18/11/2026, 257 fichiers, voir docs/WORDCRAFT-FIDELITE-5.6.108.md).
PRECISIONS = {
    'champs_date': 'recalculés à l’ouverture avec des mois en anglais (« 18 November 2026 »)',
    'objets': 'fragment importé converti en texte du document, objet d’origine retiré',
    'macros': 'retirées à l’enregistrement',
    'protection': 'conservée ; un document protégé ne se modifie pas dans l’éditeur intégré',
    'polices_theme': 'remplacées par le nom de la même police (rendu inchangé, lien au thème perdu)',
}
# Caractéristiques examinées par la garde (présentes dans l'original) : celles du cahier, plus les objets que WordCraft ne lit pas.
GARDEES = ('suivi', 'commentaires', 'champs', 'champs_date', 'controles', 'numerotation', 'notes', 'sections', 'entetes_complexes', 'objets',
           'zones_texte', 'images', 'macros', 'protection', 'tableaux', 'polices_theme')
ETATS = {'conserve': 'conservé lors de la mesure', 'altere': 'abîmé lors de la mesure', 'perdu': 'perdu lors de la mesure',
         'mineur': 'modifié sans effet visible lors de la mesure', 'non_mesure': 'non mesuré'}


def garde(analyse):
    """Constats avant ouverture : [{'code', 'libelle', 'nombre', 'fidelite', 'niveau'}] et niveau global ('ok' | 'prudence' | 'risque').
    ``risque`` : caractéristique abîmée ou perdue lors de la mesure ; ``prudence`` : présente mais non mesurée ; rien n'est ouvert sans choix."""
    constats = []
    for code in GARDEES:
        n = int(analyse.get(code) or 0)
        if code == 'sections' and n <= 1:
            continue
        if n <= 0:
            continue
        f = FIDELITE.get(code, 'non_mesure')
        niveau = 'risque' if f in ('altere', 'perdu') else ('prudence' if f == 'non_mesure' else 'info')
        etat = ETATS[f] + ((' : ' + PRECISIONS[code]) if code in PRECISIONS else '')
        constats.append({'code': code, 'libelle': LIBELLES.get(code, code), 'nombre': n, 'fidelite': f, 'etat': etat, 'niveau': niveau})
    niveaux = {c['niveau'] for c in constats}
    return {'niveau': 'risque' if 'risque' in niveaux else ('prudence' if 'prudence' in niveaux else 'ok'), 'constats': constats}


# ------------------------------------------------------------------------------------------------------------------- contrôle après enregistrement
COMPTES = ('paragraphes', 'numerotation', 'tableaux', 'cellules', 'entetes', 'pieds', 'entetes_complexes', 'notes', 'suivi', 'commentaires',
           'champs', 'controles', 'sections', 'objets', 'images', 'zones_texte', 'signets', 'liens', 'gras', 'italique', 'souligne', 'sauts_page',
           'macros', 'protection', 'champs_date', 'polices_theme')
MINEURES = ('polices_theme',)       # rendu inchangé (police du thème remplacée par son nom) : signalé, sans alarme


def _court(valeur):
    if isinstance(valeur, dict):
        valeur = ['%s : %s' % (k or 'gauche', v) for k, v in sorted(valeur.items())]
    texte = ' ; '.join(str(x) for x in valeur) if isinstance(valeur, (list, tuple)) else str(valeur)
    return texte[:160] or '—'


def _norm(t):
    return re.sub(r'\s+', ' ', str(t or '').replace(' ', ' ').replace(' ', ' ')).strip()


def controler(avant, apres):
    """Comparaison original / nouvelle version (analyses de ``analyser``). Rend {'pertes', 'texte', 'resume', 'identique'} :
    - ``pertes`` : chaque caractéristique dont le nombre baisse (ou un style qui disparaît), avec avant / après ;
    - ``texte`` : paragraphes modifiés, ajoutés, supprimés (citations bornées), espaces seuls signalés à part."""
    pertes = []
    for code in COMPTES:
        a, b = int(avant.get(code) or 0), int(apres.get(code) or 0)
        if b < a:
            pertes.append({'code': code, 'libelle': LIBELLES.get(code, code), 'avant': a, 'apres': b, **({'mineur': True, 'detail': PRECISIONS[code]}
                                                                                                         if code in MINEURES else {})})
    styles_perdus = sorted(set(avant.get('styles') or ()) - set(apres.get('styles') or ()))
    if styles_perdus:
        pertes.append({'code': 'styles', 'libelle': LIBELLES['styles'], 'avant': len(avant.get('styles') or ()), 'apres': len(apres.get('styles') or ()),
                       'detail': ', '.join(styles_perdus[:12])})
    entetes_avant = [_norm(t) for t in avant.get('textes_entetes') or ()]
    entetes_apres = {_norm(t) for t in apres.get('textes_entetes') or ()}
    entetes_changes = [t for t in entetes_avant if t not in entetes_apres]
    if entetes_changes and not any(p['code'] in ('entetes', 'pieds') for p in pertes):
        pertes.append({'code': 'entetes_texte', 'libelle': 'Texte des en-têtes ou pieds de page modifié', 'avant': len(entetes_avant),
                       'apres': len(entetes_avant) - len(entetes_changes), 'detail': ' | '.join(t[:80] for t in entetes_changes[:3])})
    champs_perdus = sorted(set(avant.get('types_champs') or ()) - set(apres.get('types_champs') or ()))
    if champs_perdus and not any(p['code'] == 'champs' for p in pertes):
        pertes.append({'code': 'types_champs', 'libelle': 'Types de champs disparus', 'avant': len(avant.get('types_champs') or ()),
                       'apres': len(apres.get('types_champs') or ()), 'detail': ', '.join(champs_perdus)})
    for code, libelle in (('mise_en_page', 'Format, orientation ou marges des pages'), ('alignements', 'Alignements des paragraphes')):
        if code in avant and code in apres and avant[code] != apres[code]:
            pertes.append({'code': code, 'libelle': libelle, 'avant': len(avant[code]), 'apres': len(apres[code]),
                           'detail': '%s → %s' % (_court(avant[code]), _court(apres[code]))})
    for code, libelle in (('polices', 'Polices disparues'), ('langues', 'Langues du texte disparues'), ('parties', 'Parties du fichier disparues'),
                          ('formats_numerotation', 'Format de la numérotation des listes modifié')):
        partis = sorted(set(avant.get(code) or ()) - set(apres.get(code) or ()))
        if partis:
            pertes.append({'code': code, 'libelle': libelle, 'avant': len(avant.get(code) or ()), 'apres': len(apres.get(code) or ()),
                           'detail': ', '.join(partis[:8])})
    ta, tb =list(avant.get('textes') or ()), list(apres.get('textes') or ())
    na, nb = [_norm(x) for x in ta], [_norm(x) for x in tb]
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    modifies, ajoutes, supprimes, espaces = 0, 0, 0, 0
    details = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            espaces += sum(1 for k in range(i2 - i1) if ta[i1 + k] != tb[j1 + k])
            continue
        if op == 'replace':
            paires = min(i2 - i1, j2 - j1)
            modifies += paires
            supprimes += max(0, (i2 - i1) - paires)
            ajoutes += max(0, (j2 - j1) - paires)
        elif op == 'delete':
            supprimes += i2 - i1
        elif op == 'insert':
            ajoutes += j2 - j1
        if len(details) < DETAILS_MAX:
            details.append({'nature': {'replace': 'modifié', 'delete': 'supprimé', 'insert': 'ajouté'}[op], 'position': i1 + 1,
                            'avant': ' ¶ '.join(x[:200] for x in na[i1:i2][:3]), 'apres': ' ¶ '.join(x[:200] for x in nb[j1:j2][:3])})
    if supprimes:
        pertes.insert(0, {'code': 'texte', 'libelle': 'Paragraphes supprimés', 'avant': len(na), 'apres': len(nb), 'detail': '%d paragraphe%s' % (
            supprimes, 's' if supprimes > 1 else '')})
    texte = {'modifies': modifies, 'ajoutes': ajoutes, 'supprimes': supprimes, 'espaces': espaces, 'details': details}
    graves = [p for p in pertes if not p.get('mineur')]
    identique = not graves and not modifies and not ajoutes and not supprimes
    if identique:
        resume = 'Aucune perte : texte, styles, numérotation, tableaux, en-têtes et notes sont identiques à l’original.' + (
            ' Une modification sans effet visible est signalée ci-dessous.' if pertes else '')
    elif graves:
        resume = '%d point%s à vérifier avant de garder cette version.' % (len(graves), 's' if len(graves) > 1 else '')
    else:
        resume = 'Aucune perte de structure ; %d paragraphe%s modifié%s, %d ajouté%s.' % (modifies, 's' if modifies > 1 else '', 's' if modifies > 1 else '',
                                                                                        ajoutes, 's' if ajoutes > 1 else '')
    return {'pertes': pertes, 'texte': texte, 'resume': resume, 'identique': identique}


# ------------------------------------------------------------------------------------------------------------------- idempotence
def executer(desk, acteur, cle, nature, parametres, fn):
    """Une commande = un effet : même clé et mêmes paramètres → même reçu ; autre usage → ``cle_reutilisee`` (409) ; en cours → 409."""
    compte = str(acteur[0])
    cle = str(cle or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,80}', cle):
        raise Stop('cle_requise')
    empreinte = hashlib.sha256(json.dumps({'n': nature, 'p': parametres}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    row = desk.db.execute('SELECT empreinte, etat, resultat FROM editeur56108_ops WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if row:
        if row[0] != empreinte:
            raise Stop('cle_reutilisee')
        if row[1] == 'fait':
            return {**json.loads(row[2]), 'rejoue': True}
        if row[1] == 'echec':
            raise Stop(json.loads(row[2]).get('error') or 'operation_echouee')
        if row[1] != 'a_verifier':
            raise Stop('operation_en_cours')
        # 5.6.109 (C07) : « à vérifier » n'est ni un reçu ni un échec : la même clé rapproche de nouveau (lecture de la cible figée)
    else:
        quand = _maintenant()
        desk.db.execute('INSERT INTO editeur56108_ops(acteur,cle,nature,empreinte,etat,resultat,cree_le,maj_le) VALUES(?,?,?,?,?,?,?,?)',
                        (compte, cle, nature, empreinte, 'en_cours', '{}', quand, quand))
        desk.db.commit()

    def clore(erreur):
        """Fin sur exception : si la décision portée par cette clé a pu produire un effet, la clé reste « à vérifier » (jamais « échec »)."""
        desk.db.rollback()
        porte = desk.db.execute('SELECT rapprochement FROM editeur56108_ops WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
        if porte and porte[0] in ('engagee', 'a_verifier'):
            desk.db.execute("UPDATE editeur56108_ops SET etat='a_verifier' WHERE acteur=? AND cle=?", (compte, cle))
        elif porte and porte[0] == 'reservee':   # cible réservée, effet jamais lancé : la clé reste reprenable (aucun échec enregistré)
            desk.db.execute("UPDATE editeur56108_ops SET etat='a_verifier', resultat=? WHERE acteur=? AND cle=?", (json.dumps({'error': erreur}), compte, cle))
        else:
            desk.db.execute("UPDATE editeur56108_ops SET etat='echec', resultat=?, maj_le=? WHERE acteur=? AND cle=?",
                            (json.dumps({'error': erreur}), _maintenant(), compte, cle))
        desk.db.commit()
    try:
        out = fn()
    except Stop as ex:
        clore(str(ex).split(':')[0])
        raise
    except Exception:
        clore('erreur_interne')
        raise
    recu = json.dumps(out, ensure_ascii=False, default=str)
    if isinstance(out, dict) and out.get('etat') == 'a_verifier':   # le bail de la décision (``maj_le``) n'est pas touché
        desk.db.execute("UPDATE editeur56108_ops SET etat='a_verifier', resultat=? WHERE acteur=? AND cle=?", (recu, compte, cle))
    else:
        desk.db.execute("UPDATE editeur56108_ops SET etat='fait', resultat=?, maj_le=? WHERE acteur=? AND cle=?", (recu, _maintenant(), compte, cle))
    desk.db.commit()
    return out


# ------------------------------------------------------------------------------------------------------------------- séances
def _racine_privee(desk):
    return Path(desk.c['state_dir']) / 'editeur56108'


def dossier_seance(desk, ident):
    if not re.fullmatch(r'[a-f0-9]{32}', str(ident or '')):
        raise Stop('introuvable')
    return _racine_privee(desk) / ident


def _prive(dossier):
    dossier.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dossier, 0o700)
        os.chmod(dossier.parent, 0o700)
    except OSError:
        pass
    return dossier


def _ecrire_prive(chemin, data):
    tmp = chemin.with_name('.' + chemin.name + '.' + secrets.token_hex(4))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def _source(desk, auth, acteur, ident, chemin, sources_=None):
    from . import fichiers56103 as F
    return F.ouvrir(desk, auth, acteur, ident, chemin, sources_)


def nature(chemin):
    """'docx', 'pdf' ou '' (format non pris en charge)."""
    return {'.docx': 'docx', '.pdf': 'pdf'}.get(PurePosixPath(str(chemin or '')).suffix.lower(), '')


def _lire_original(source, chemin, formats=('docx',)):
    props = source.proprietes(chemin)
    if props.get('dossier'):
        raise Stop('introuvable')
    if nature(chemin) not in formats:
        raise Stop('format_non_docx' if formats == ('docx',) else 'format_non_pris_en_charge')
    data = source.lire(chemin, MAX_DOCX)
    return props, bytes(data)


# ------------------------------------------------------------------------------------------------------------------- PDF (application du poste)
def analyser_pdf(raw):
    """PDF : en-tête, nombre de pages et texte de chaque page (pypdf embarqué) ; Stop('document_pdf_illisible') sinon."""
    if not isinstance(raw, (bytes, bytearray)) or not raw or len(raw) > MAX_DOCX:
        raise Stop('document_word_trop_volumineux' if raw and len(raw) > MAX_DOCX else 'document_pdf_illisible')
    if not bytes(raw[:1024]).lstrip().startswith(b'%PDF-'):
        raise Stop('document_pdf_illisible')
    try:
        from ._vendor.pypdf import PdfReader
        lecteur = PdfReader(io.BytesIO(bytes(raw)))
        pages = len(lecteur.pages)
        textes = []
        for page in lecteur.pages[:2000]:
            try:
                textes.append(page.extract_text() or '')
            except Exception:   # noqa: BLE001 — page illisible : comptée, sans texte
                textes.append('')
        chiffre = bool(getattr(lecteur, 'is_encrypted', False))
    except Stop:
        raise
    except Exception:   # noqa: BLE001 — PDF abîmé ou non pris en charge par le lecteur embarqué
        raise Stop('document_pdf_illisible') from None
    return {'pdf': True, 'pages': pages, 'textes': textes, 'chiffre': int(chiffre), 'paragraphes': pages}


def controler_pdf(avant, apres):
    """Contrôle d'un PDF repris : pages disparues (perte), texte page par page (pages modifiées, ajoutées, supprimées)."""
    pertes = []
    if apres['pages'] < avant['pages']:
        pertes.append({'code': 'pages', 'libelle': 'Pages', 'avant': avant['pages'], 'apres': apres['pages']})
    na, nb = [_norm(x) for x in avant['textes']], [_norm(x) for x in apres['textes']]
    modifies = sum(1 for a, b in zip(na, nb) if a != b)
    ajoutes, supprimes = max(0, len(nb) - len(na)), max(0, len(na) - len(nb))
    details = [{'nature': 'modifié', 'position': i + 1, 'avant': a[:200], 'apres': b[:200]} for i, (a, b) in enumerate(zip(na, nb)) if a != b][:DETAILS_MAX]
    texte = {'modifies': modifies, 'ajoutes': ajoutes, 'supprimes': supprimes, 'espaces': 0, 'details': details, 'unite': 'page'}
    identique = not pertes and not modifies and not ajoutes
    if pertes:
        resume = '%d point%s à vérifier avant de garder cette version.' % (len(pertes), 's' if len(pertes) > 1 else '')
    elif identique:
        resume = 'Aucune perte : même nombre de pages et même texte que l’original (annotations et dessins non comparés).'
    else:
        resume = 'Aucune page perdue ; texte modifié sur %d page%s (annotations et dessins non comparés).' % (modifies + ajoutes, 's' if modifies + ajoutes > 1 else '')
    return {'pertes': pertes, 'texte': texte, 'resume': resume, 'identique': identique}


def _capacite(chemin):
    """5.6.109 (lot V, F16) : capacité de format déclarée par ``craft56109`` (XLSX, PPTX) ; le DOCX et le PDF gardent leurs fonctions ici."""
    from .craft56109 import capacite
    return capacite(chemin)


def _analyser_selon(chemin, data):
    cap = _capacite(chemin)
    if cap:
        return cap['analyser'](data)
    return analyser_pdf(data) if nature(chemin) == 'pdf' else analyser(data)


def _controler_selon(chemin, avant, apres):
    cap = _capacite(chemin)
    if cap:
        return cap['controler'](avant, apres)
    return controler_pdf(avant, apres) if nature(chemin) == 'pdf' else controler(avant, apres)


def seance(desk, acteur, ident):
    """Séance de cet acteur (autre acteur, inconnue ou close : même 404 neutre)."""
    row = desk.db.execute('SELECT * FROM editeur56108_seances WHERE id=? AND acteur=?', (str(ident or ''), str(acteur[0]))).fetchone()
    if not row or row['etat'] in ('fermee',):
        raise Stop('introuvable')
    return dict(row)


def ouvrir_seance(desk, auth, acteur, ident_source, chemin, outil, accepte=False, sources_=None):
    """Contrôle des droits, lecture de l'original, garde, copie privée ; rend la séance (sans les octets)."""
    from .pause5629 import checkpoint
    source, chemin = _source(desk, auth, acteur, ident_source, chemin, sources_)
    if 'ecriture' not in source.capacites():
        raise Stop('non_pris_en_charge')
    props, data = _lire_original(source, chemin, ('docx', 'pdf') if outil == 'traitement' else ('docx',))
    analyse = _analyser_selon(chemin, data)
    g = garde(analyse) if nature(chemin) == 'docx' else {'niveau': 'ok', 'constats': []}
    if outil == 'wordcraft' and g['niveau'] == 'risque' and not accepte:
        raise Stop('garde_avertissement')
    checkpoint(desk)
    ident = secrets.token_hex(16)
    dossier = _prive(dossier_seance(desk, ident))
    _ecrire_prive(dossier / 'original.docx', data)
    sha = hashlib.sha256(data).hexdigest()
    quand = _maintenant()
    desk.db.execute('INSERT INTO editeur56108_seances(id,acteur,outil,source,chemin,etag,sha256,taille,garde,etat,cree_le,maj_le) '
                    "VALUES(?,?,?,?,?,?,?,?,?,'ouverte',?,?)", (ident, acteur[0], outil, source.ident, chemin, str(props.get('etag') or ''), sha,
                                                               len(data), json.dumps(g, ensure_ascii=False), quand, quand))
    desk.db.commit()
    desk.audit('editeur56108_ouvert', {'par': acteur[0], 'outil': outil, 'source': source.ident, 'chemin': chemin, 'sha256': sha,
                                       'garde': g['niveau'], 'risques_acceptes': bool(accepte and g['niveau'] == 'risque')})
    return {'seance': ident, 'sha256': sha, 'taille': len(data), 'nom': PurePosixPath(chemin).name, 'garde': g, 'revision': 0}


def octets_original(desk, auth, acteur, ident, sources_=None):
    """Octets de la copie privée de l'original (droits revérifiés : un dossier réservé depuis l'ouverture donne le même 404)."""
    s = seance(desk, acteur, ident)
    _source(desk, auth, acteur, s['source'], s['chemin'], sources_)
    data = (dossier_seance(desk, ident) / 'original.docx').read_bytes()
    if hashlib.sha256(data).hexdigest() != s['sha256']:
        raise Stop('copie_privee_alteree')
    return data, PurePosixPath(s['chemin']).name


def proposer(desk, auth, acteur, ident, revision, data, sources_=None):
    """Nouvel enregistrement (candidat) : validé, rangé dans la séance, comparé à l'original. ``revision`` : révision attendue (409 sinon)."""
    s = seance(desk, acteur, ident)
    _source(desk, auth, acteur, s['source'], s['chemin'], sources_)
    if s['etat'] != 'ouverte':
        raise Stop('introuvable')
    if ecritures_incertaines(desk, ident):   # 5.6.109 (C07) : un nouveau candidat effacerait celui d'une écriture non résolue et mènerait à une autre cible
        raise Stop('ecriture_a_verifier')
    try:
        revision = int(revision)
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None
    if revision != int(s['revision']):
        raise Stop('version_perimee')
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise Stop('document_word_illisible')
    data = bytes(data)
    apres = _analyser_selon(s['chemin'], data)
    original = (dossier_seance(desk, ident) / 'original.docx').read_bytes()
    rapport = _controler_selon(s['chemin'], _analyser_selon(s['chemin'], original), apres)
    nouvelle = revision + 1
    dossier = _prive(dossier_seance(desk, ident))
    for ancien in dossier.glob('candidat-*.docx'):
        try:
            ancien.unlink()
        except OSError:
            pass
    _ecrire_prive(dossier / ('candidat-%d.docx' % nouvelle), data)   # nom interne de la copie privée, quel que soit le format
    sha = hashlib.sha256(data).hexdigest()
    cur = desk.db.execute("UPDATE editeur56108_seances SET revision=?, candidat_rev=?, candidat_sha=?, rapport=?, maj_le=? WHERE id=? AND revision=? "
                          "AND etat='ouverte'", (nouvelle, nouvelle, sha, json.dumps(rapport, ensure_ascii=False), _maintenant(), ident, revision))
    if cur.rowcount != 1:
        desk.db.rollback()
        raise Stop('version_perimee')
    desk.db.commit()
    desk.audit('editeur56108_enregistrement_propose', {'par': acteur[0], 'outil': s['outil'], 'chemin': s['chemin'], 'revision': nouvelle,
                                                       'sha256': sha, 'pertes': [p['code'] for p in rapport['pertes']]})
    return {'seance': ident, 'revision': nouvelle, 'sha256': sha, 'taille': len(data), 'controle': rapport}


def nom_version(desk, chemin, outil, n=1):
    """« Contrat (modifié dans WordCraft 18-11-2026 14h05).docx », puis « … 2 », « … 3 »."""
    from . import fichiers56103 as F
    p = PurePosixPath(chemin)
    from .craft56109 import RELEASES as _craft109
    libelle = 'modifié dans WordCraft' if outil == 'wordcraft' else ('modifié dans ' + _craft109[outil]['libelle']) if outil in _craft109 else 'modifié sur le poste'
    return str(p.with_name('%s (%s %s%s)%s' % (p.stem, libelle, F.horodatage(desk), '' if n <= 1 else ' %d' % n, p.suffix)))


# ------------------------------------------------------------------------------------------------------------------- 5.6.109 (C07) : décision
# Protocole (inspiré de ``reference/ecriture_verifiee.py`` du dossier technique, raccordé aux séances, aux droits et au stockage) :
#   intention acceptée → cible et empreinte réservées (durables, uniques par décision) → création exclusive engagée → cible relue.
#   Réponse ou relecture perdue : « à vérifier » ; seule la cible figée est relue ensuite ; conforme = reçu vérifié ; absente : nouvel essai
#   explicite sur le MÊME nom ; différente : jamais remplacée, jamais contournée par un autre nom. Droits et compte relus avant l'effet et
#   avant chaque lecture de rapprochement ; un arrêt central (pause, annulation, droits) n'est jamais absorbé par le traitement des pannes.
def _id_decision(ident, revision):
    """Identité métier d'un « Garder » : la séance et la révision du candidat montré (une révision ne désigne qu'un candidat)."""
    return hashlib.sha256(('garder|%s|%d' % (ident, int(revision))).encode()).hexdigest()


def _op_decision(desk, compte, did):
    row = desk.db.execute('SELECT * FROM editeur56108_ops WHERE decision=? AND acteur=?', (did, compte)).fetchone()
    return dict(row) if row else None


def _maj_decision(desk, did, **champs):
    desk.db.execute('UPDATE editeur56108_ops SET %s WHERE decision=?' % ', '.join(k + '=?' for k in champs), tuple(champs.values()) + (did,))
    desk.db.commit()


def _liberer(desk, did):
    """Refus certain AVANT tout effet : la décision est rendue (aucune cible réservée ne reste), un nouvel essai repartira proprement."""
    desk.db.execute("DELETE FROM editeur56108_ops WHERE decision=? AND cle LIKE 'decision-%'", (did,))
    desk.db.execute("UPDATE editeur56108_ops SET decision='', seance='', revision=0, cible='', candidat_sha='', rapprochement='', motif='' WHERE decision=?", (did,))
    desk.db.commit()


def _age(horodatage):
    try:
        return time.time() - datetime.fromisoformat(str(horodatage)).timestamp()
    except (ValueError, TypeError):
        return float('inf')     # bail rendu ('') ou illisible : libre


def ecritures_incertaines(desk, ident):
    """Décisions de la séance dont l'écriture n'est pas résolue (lecture seule) : cible réservée, création engagée ou résultat à vérifier."""
    return [dict(r) for r in desk.db.execute("SELECT * FROM editeur56108_ops WHERE seance=? AND rapprochement IN ('reservee','engagee','a_verifier')",
                                             (str(ident or ''),))]


def _cible_libre(desk, source, chemin, outil):
    """Premier nom de version libre (lecture seule). Stockage en panne : l'erreur remonte, rien n'est encore réservé ni écrit."""
    for n in range(1, 21):
        cible = nom_version(desk, chemin, outil, n)
        try:
            source.proprietes(cible)
        except Stop as ex:
            if str(ex) == 'introuvable':
                return cible
            raise
    raise Stop('deja_existant')


def _reserver(desk, compte, cle, did, ident, revision, cible, sha):
    """Cible et empreinte durables AVANT tout effet, sur la ligne de la clé de transport (ou une ligne propre à la décision) ; l'unicité de la
    décision refuse dans la base une requête concurrente de la même décision."""
    quand = _maintenant()
    try:
        portee = False
        if cle:
            cur = desk.db.execute("UPDATE editeur56108_ops SET decision=?, seance=?, revision=?, cible=?, candidat_sha=?, rapprochement='reservee', motif='', maj_le=? "
                                  "WHERE acteur=? AND cle=? AND decision=''", (did, ident, revision, cible, sha, quand, compte, str(cle)))
            portee = cur.rowcount == 1
        if not portee:
            desk.db.execute("INSERT INTO editeur56108_ops(acteur,cle,nature,empreinte,etat,resultat,cree_le,maj_le,decision,seance,revision,cible,candidat_sha,rapprochement) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'reservee')",
                            (compte, 'decision-' + did[:48], 'decider', did, 'en_cours', '{}', quand, quand, did, ident, revision, cible, sha))
        desk.db.commit()
    except sqlite3.IntegrityError:
        desk.db.rollback()
        raise Stop('operation_en_cours') from None
    return _op_decision(desk, compte, did)


def decider(desk, auth, acteur, ident, revision, decision, malgre_changement=False, sources_=None, cle=None, reessayer=False):
    """« Garder » : le candidat devient un fichier voisin (création exclusive, relu, empreinte comparée) ; l'original reste intact.
    « Abandonner » : le candidat est effacé. ``revision`` : candidat montré à l'avocat (un autre plus récent : 409).

    5.6.109 (C07) : la cible est réservée avant l'effet ; une réponse ou une relecture perdue donne ``etat='a_verifier'`` (ni reçu, ni échec),
    le candidat est conservé ; la même décision redemandée — même avec une autre clé de transport ``cle`` — relit la MÊME cible et rend le
    reçu vérifié, sans jamais créer un second fichier. ``reessayer`` : nouvel essai explicite, seulement si cette cible est constatée absente."""
    s = seance(desk, acteur, ident)
    source, chemin = _source(desk, auth, acteur, s['source'], s['chemin'], sources_)     # droits et compte relus à chaque appel, avant tout
    try:
        revision = int(revision)
    except (TypeError, ValueError):
        raise Stop('version_perimee') from None
    if decision not in ('garder', 'abandonner'):
        raise Stop('decision_invalide')
    compte = str(acteur[0])
    did = _id_decision(ident, revision)
    op = _op_decision(desk, compte, did)
    if decision == 'garder' and op:
        return _rapprocher(desk, auth, acteur, s, op, sources_, reessayer)
    if not s['candidat_rev'] or revision != int(s['candidat_rev']):
        raise Stop('version_perimee')
    candidat = dossier_seance(desk, ident) / ('candidat-%d.docx' % revision)
    if decision == 'abandonner':
        incertaine = op if op and op['rapprochement'] in INCERTAINS else None
        if incertaine and incertaine['rapprochement'] != 'a_verifier' and _age(incertaine['maj_le']) < BAIL_ECRITURE:
            raise Stop('operation_en_cours')
        try:
            candidat.unlink()
        except OSError:
            pass
        message = 'Version abandonnée : rien n’a été écrit dans le dossier ; l’original est intact.'
        if incertaine and incertaine['rapprochement'] != 'reservee':
            message = ('Version abandonnée ; l’original est intact. L’écriture de « %s » n’avait pas pu être vérifiée : si ce fichier existe dans '
                       'le dossier, il n’a pas été retiré — contrôlez-le.') % PurePosixPath(incertaine['cible']).name
        desk.db.execute("UPDATE editeur56108_seances SET candidat_rev=0, candidat_sha='', rapport='{}', maj_le=? WHERE id=?", (_maintenant(), ident))
        if incertaine:
            desk.db.execute("UPDATE editeur56108_ops SET rapprochement='abandonnee', etat='fait', resultat=?, maj_le=? WHERE decision=?",
                            (json.dumps({'decision': 'abandonner', 'message': message}, ensure_ascii=False), _maintenant(), did))
        desk.db.commit()
        desk.audit('editeur56108_version_abandonnee', {'par': acteur[0], 'outil': s['outil'], 'chemin': chemin, 'revision': revision,
                                                       **({'ecriture_non_verifiee': incertaine['cible']} if incertaine else {})})
        return {'decision': 'abandonner', 'message': message}
    data = candidat.read_bytes()
    if hashlib.sha256(data).hexdigest() != s['candidat_sha']:
        raise Stop('copie_privee_alteree')
    try:
        actuel = source.proprietes(chemin)
    except Stop as ex:
        if str(ex) != 'introuvable':
            raise
        actuel = None
    if s['etag'] and (actuel is None or str(actuel.get('etag') or '') != s['etag']) and not malgre_changement:
        raise Stop('original_modifie')
    cible = _cible_libre(desk, source, chemin, s['outil'])
    op = _reserver(desk, compte, cle, did, ident, revision, cible, s['candidat_sha'])
    op['malgre'] = bool(malgre_changement)
    return _engager(desk, auth, acteur, s, op, data, sources_, premiere=True)


def _rapprocher(desk, auth, acteur, s, op, sources_, reessayer=False):
    """Décision déjà connue (second clic, autre clé de transport, rechargement, reprise après coupure) : jamais une seconde cible."""
    etat = op['rapprochement']
    if etat == 'verifiee':
        return {**json.loads(op['resultat'] or '{}'), 'rejoue': True}
    if etat not in INCERTAINS:                       # abandonnée ou expirée : cette version n'existe plus
        raise Stop('version_perimee')
    if etat != 'a_verifier' and _age(op['maj_le']) < BAIL_ECRITURE:
        raise Stop('operation_en_cours')             # une autre requête tient cette décision (bail court)
    quand = _maintenant()
    cur = desk.db.execute('UPDATE editeur56108_ops SET maj_le=? WHERE decision=? AND maj_le=? AND rapprochement=?', (quand, op['decision'], op['maj_le'], etat))
    desk.db.commit()
    if cur.rowcount != 1:
        raise Stop('operation_en_cours')
    op['maj_le'] = quand
    if etat != 'reservee':   # création peut-être engagée, ou résultat déjà incertain : SEULE la cible figée est relue
        verdict = _lire_cible(desk, auth, acteur, s, op, sources_)
        if verdict == 'conforme':
            return _verifier(desk, acteur, s, op)
        if not (verdict == 'cible_absente' and reessayer and etat == 'a_verifier' and op['motif'] == 'cible_absente'):
            return _incertain(desk, acteur, s, op, verdict)
        # « Réessayer l'écriture » : demandé explicitement, cible constatée absente deux fois : même nom, création exclusive
    candidat = dossier_seance(desk, s['id']) / ('candidat-%d.docx' % int(op['revision']))
    try:
        data = candidat.read_bytes()
    except OSError:
        data = b''
    if hashlib.sha256(data).hexdigest() != op['candidat_sha']:
        _maj_decision(desk, op['decision'], maj_le='')
        raise Stop('copie_privee_alteree')
    return _engager(desk, auth, acteur, s, op, data, sources_)


def _lire_cible(desk, auth, acteur, s, op, sources_):
    """Lecture de rapprochement : SEULE la cible figée est relue, droits et compte relus juste avant. Rend ``conforme``, ``cible_absente``,
    ``cible_differente`` ou ``relecture_impossible`` ; un refus de droits ou un arrêt central remonte (l'état incertain est gardé)."""
    from .reprise56109 import arret_central, code_de
    try:
        cible_source, cible = _source(desk, auth, acteur, s['source'], op['cible'], sources_)
    except BaseException:
        _maj_decision(desk, op['decision'], maj_le='')
        raise
    try:
        relu = cible_source.lire(cible, MAX_DOCX)
    except Stop as ex:
        if arret_central(ex):
            _maj_decision(desk, op['decision'], maj_le='')
            raise
        return 'cible_absente' if code_de(ex) == 'introuvable' else 'relecture_impossible'
    except Exception:   # noqa: BLE001 — transport en panne pendant la relecture : incertain, jamais « échec »
        return 'relecture_impossible'
    return 'conforme' if hashlib.sha256(bytes(relu)).hexdigest() == op['candidat_sha'] else 'cible_differente'


def _constater(desk, auth, acteur, s, op, sources_):
    verdict = _lire_cible(desk, auth, acteur, s, op, sources_)
    return _verifier(desk, acteur, s, op) if verdict == 'conforme' else _incertain(desk, acteur, s, op, verdict)


def _engager(desk, auth, acteur, s, op, data, sources_, premiere=False):
    """Effet : création exclusive sur la cible réservée. ``premiere`` : aucun effet de cette décision n'a encore pu avoir lieu (un nom pris
    entre-temps par un AUTRE fichier fait alors réserver le nom suivant, avant le nouvel essai)."""
    from .pause5629 import checkpoint
    from .reprise56109 import arret_central, code_de
    did = op['decision']
    for _ in range(20):
        try:
            checkpoint(desk)                                                      # dernier contrôle avant l'effet
            cible_source, cible = _source(desk, auth, acteur, s['source'], op['cible'], sources_)   # droits et compte relus sur la cible
            if 'ecriture' not in cible_source.capacites():
                raise Stop('non_pris_en_charge')
        except BaseException:
            _maj_decision(desk, did, maj_le='')     # effet non engagé : bail rendu ; la cible reste réservée, la décision reprendra dessus
            raise
        _maj_decision(desk, did, rapprochement='engagee', maj_le=_maintenant())   # durable AVANT l'effet
        try:
            cible_source.ecrire(cible, data, None)                                # création exclusive : jamais d'écrasement
        except Stop as ex:
            code = code_de(ex)
            if arret_central(ex):   # jamais absorbé comme une panne de transport ; l'écriture a pu être acceptée : l'état garde ce fait
                _maj_decision(desk, did, rapprochement='a_verifier', motif='resultat_inconnu', maj_le='')
                raise
            if code == 'deja_existant':
                verdict = _lire_cible(desk, auth, acteur, s, op, sources_)
                if verdict == 'conforme':                # c'est cette version : une écriture précédente dont la réponse s'était perdue
                    return _verifier(desk, acteur, s, op)
                if premiere and verdict == 'cible_differente':
                    source, chemin = _source(desk, auth, acteur, s['source'], s['chemin'], sources_)
                    op['cible'] = _cible_libre(desk, source, chemin, s['outil'])
                    _maj_decision(desk, did, cible=op['cible'], rapprochement='reservee', maj_le=_maintenant())
                    continue
                return _incertain(desk, acteur, s, op, verdict)
            if code in REFUS_CERTAINS:                   # refus avant tout effet : échec franc, rien n'a été écrit
                _liberer(desk, did)
                raise
            _maj_decision(desk, did, rapprochement='a_verifier', motif='resultat_inconnu', maj_le=_maintenant())
        except Exception:   # noqa: BLE001 — panne hors protocole pendant l'écriture : le résultat est inconnu, pas « échoué »
            _maj_decision(desk, did, rapprochement='a_verifier', motif='resultat_inconnu', maj_le=_maintenant())
        return _constater(desk, auth, acteur, s, op, sources_)
    _liberer(desk, did)
    raise Stop('deja_existant')


def _verifier(desk, acteur, s, op):
    """Cible relue, conforme : reçu vérifié ; le candidat peut être retiré, la séance note la version gardée."""
    ident, cible = s['id'], op['cible']
    try:
        (dossier_seance(desk, ident) / ('candidat-%d.docx' % int(op['revision']))).unlink()
    except OSError:
        pass
    nom = PurePosixPath(cible).name
    out = {'decision': 'garder', 'etat': 'verifie', 'chemin': cible, 'nom': nom, 'sha256': op['candidat_sha'],
           'message': 'Nouvelle version enregistrée à côté de l’original : « %s ». L’original est intact.' % nom}
    desk.db.execute("UPDATE editeur56108_seances SET candidat_rev=0, candidat_sha='', version_chemin=?, maj_le=? WHERE id=? AND candidat_rev IN (0, ?)",
                    (cible, _maintenant(), ident, int(op['revision'])))
    desk.db.execute("UPDATE editeur56108_ops SET rapprochement='verifiee', motif='', etat='fait', resultat=?, maj_le=? WHERE decision=?",
                    (json.dumps(out, ensure_ascii=False), _maintenant(), op['decision']))
    desk.db.commit()
    desk.audit('editeur56108_version_gardee', {'par': acteur[0], 'outil': s['outil'], 'source': s['source'], 'chemin': s['chemin'], 'version': cible,
                                               'sha256': op['candidat_sha'], 'revision': int(op['revision']),
                                               'malgre_changement': bool(op.get('malgre')), 'rapprochee': op['rapprochement'] == 'a_verifier'})
    return out


def _incertain(desk, acteur, s, op, motif):
    """Résultat à vérifier : ni reçu ni échec ; candidat conservé, cible figée, bail rendu ; l'état est durable et dit pourquoi."""
    nom = PurePosixPath(op['cible']).name
    out = {'decision': 'garder', 'etat': 'a_verifier', 'chemin': op['cible'], 'nom': nom, 'motif': motif, 'reessayable': motif == 'cible_absente',
           'seance': s['id'], 'revision': int(op['revision']),
           'message': MOTIFS_INCERTAINS.get(motif, MOTIFS_INCERTAINS['resultat_inconnu']) % nom}
    nouveau = op['rapprochement'] != 'a_verifier' or op.get('motif') != motif
    desk.db.execute("UPDATE editeur56108_ops SET rapprochement='a_verifier', motif=?, etat='a_verifier', resultat=?, maj_le='' WHERE decision=?",
                    (motif, json.dumps(out, ensure_ascii=False), op['decision']))
    desk.db.commit()
    if nouveau:
        desk.audit('editeur56108_ecriture_a_verifier', {'par': acteur[0], 'outil': s['outil'], 'chemin': s['chemin'], 'cible': op['cible'], 'motif': motif,
                                                        'revision': int(op['revision'])})
    return out


def _expirer(desk, ident):
    """Séance effacée par la maintenance alors qu'une écriture restait incertaine : expiration EXPLIQUÉE (jamais un échec silencieux qui
    autoriserait un nouvel effet, jamais un succès) ; le fichier éventuel du dossier n'est ni retiré ni remplacé."""
    for r in ecritures_incertaines(desk, ident):
        nom = PurePosixPath(r['cible']).name
        message = ('Séance expirée après %d jours : la copie de travail a été effacée. L’écriture de « %s » n’a jamais pu être vérifiée : contrôlez ce '
                   'fichier dans le dossier (il n’a été ni retiré ni remplacé).') % (SEANCE_JOURS, nom)
        desk.db.execute("UPDATE editeur56108_ops SET rapprochement='expiree', resultat=?, maj_le=? WHERE decision=?",
                        (json.dumps({'error': 'ecriture_expiree', 'message': message, 'chemin': r['cible']}, ensure_ascii=False), _maintenant(), r['decision']))
        desk.db.commit()
        desk.audit('editeur56108_ecriture_expiree', {'par': r['acteur'], 'cible': r['cible'], 'etat': r['rapprochement'], 'motif': r['motif']})


LIBELLES_INCERTAINS = {'relecture_impossible': 'fichier écrit, relecture de contrôle non aboutie',
                       'cible_absente': 'écriture non confirmée ; le fichier n’est pas dans le dossier pour le moment',
                       'cible_differente': 'un fichier de ce nom existe avec un autre contenu',
                       'resultat_inconnu': 'résultat de l’écriture inconnu'}


def incertaines_html(desk, acteur, source_ident, chemin):
    """Écritures de CE compte, pour CE document, restées à vérifier (lecture seule) : retrouvées après un rechargement de la page."""
    try:
        rows = desk.db.execute(
            "SELECT o.seance, o.revision, o.cible, o.motif FROM editeur56108_ops o JOIN editeur56108_seances s ON s.id=o.seance WHERE o.acteur=? AND s.acteur=? "
            "AND s.source=? AND s.chemin=? AND s.etat='ouverte' AND o.rapprochement IN ('reservee','engagee','a_verifier') ORDER BY o.cree_le LIMIT 10",
            (str(acteur[0]), str(acteur[0]), str(source_ident), str(chemin))).fetchall()
    except sqlite3.Error:
        return ''
    if not rows:
        return ''
    lignes = []
    for r in rows:
        attributs = 'data-w108-seance="%s" data-w108-revision="%d"' % (e(r['seance'], quote=True), int(r['revision']))
        lignes.append('<li><strong>%s</strong> <span class="w108-badge">À vérifier</span> <span>%s</span><span class="w108-actions">'
                      '<button type="button" class="ax-btn" data-w108="verifier" %s>Vérifier</button>%s'
                      '<button type="button" class="ax-btn ghost" data-w108="abandonner" %s>Abandonner cette version</button></span></li>' % (
                          e(PurePosixPath(r['cible']).name), e(LIBELLES_INCERTAINS.get(r['motif'], LIBELLES_INCERTAINS['resultat_inconnu'])), attributs,
                          ('<button type="button" class="ax-btn ghost" data-w108="reessayer" %s>Réessayer l’écriture</button>' % attributs)
                          if r['motif'] == 'cible_absente' else '', attributs))
    return ('<section class="w108-incertaines" aria-labelledby="w108-t-incertaines"><h2 id="w108-t-incertaines">Écriture à vérifier</h2>'
            '<p>Une version gardée précédemment n’a pas pu être confirmée dans le dossier. « Vérifier » relit ce même fichier ; rien d’autre ne sera '
            'écrit. Votre version est conservée tant que vous n’avez pas décidé.</p><ul class="w108-constats">%s</ul></section>') % ''.join(lignes)


def fermer(desk, acteur, ident):
    """Clôt la séance et efface ses copies privées (un fichier encore ouvert par un traitement de texte est repris par la maintenance)."""
    s = seance(desk, acteur, ident)
    if ecritures_incertaines(desk, ident):   # 5.6.109 (C07) : le candidat d'une écriture non résolue est conservé jusqu'à « Vérifier » ou « Abandonner »
        raise Stop('ecriture_a_verifier')
    reste = _effacer(desk, ident)
    desk.db.execute("UPDATE editeur56108_seances SET etat='fermee', candidat_rev=0, candidat_sha='', maj_le=? WHERE id=?", (_maintenant(), ident))
    desk.db.commit()
    desk.audit('editeur56108_ferme', {'par': acteur[0], 'outil': s['outil'], 'chemin': s['chemin'], 'nettoyage_en_attente': bool(reste)})
    return {'ferme': True, 'nettoyage_en_attente': bool(reste)}


def _effacer(desk, ident):
    dossier = dossier_seance(desk, ident)
    if not dossier.exists():
        return False
    shutil.rmtree(dossier, ignore_errors=True)
    return dossier.exists()


def maintenance(desk, jours=SEANCE_JOURS, maintenant=None):
    """Séances closes ou anciennes : copies privées effacées (rien d'autre n'est touché). Rend le nombre de dossiers effacés."""
    limite = (maintenant or time.time()) - jours * 86400
    racine = _racine_privee(desk)
    if not racine.exists():
        return 0
    ouvertes = {r[0] for r in desk.db.execute("SELECT id FROM editeur56108_seances WHERE etat='ouverte'")}
    n = 0
    for d in racine.iterdir():
        if not d.is_dir() or not re.fullmatch(r'[a-f0-9]{32}', d.name):
            continue
        try:
            vieux = d.stat().st_mtime < limite
        except OSError:
            continue
        if d.name not in ouvertes or vieux:
            shutil.rmtree(d, ignore_errors=True)
            n += not d.exists()
            if vieux and d.name in ouvertes:
                desk.db.execute("UPDATE editeur56108_seances SET etat='fermee', maj_le=? WHERE id=?", (_maintenant(), d.name))
            _expirer(desk, d.name)   # 5.6.109 (C07) : écriture restée incertaine : expiration expliquée, jamais un échec silencieux
    desk.db.commit()
    return n


# =================================================================================================================== pages et routes
PREFIXE = 'm56108/editeur/'
LECTURES = ('fichier', 'word/etat')
ECRITURES = ('ouvrir', 'enregistrer', 'decider', 'fermer', 'reglage', 'word/ouvrir', 'word/reprendre')
MAX_POST = 64_000
STATUTS = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'original_modifie': '409 Conflict', 'cle_reutilisee': '409 Conflict',
           'operation_en_cours': '409 Conflict', 'garde_avertissement': '409 Conflict', 'deja_existant': '409 Conflict',
           'role_insuffisant': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'wordcraft_desactive': '403 Forbidden',
           'controle_acces_indisponible': '503 Service Unavailable', 'stockage_injoignable': '503 Service Unavailable',
           'requete_trop_longue': '413 Payload Too Large', 'document_word_trop_volumineux': '413 Payload Too Large',
           'indisponible_sur_serveur': '409 Conflict', 'aucun_traitement_de_texte': '409 Conflict', 'aucune_modification': '409 Conflict',
           'aucune_application_pdf': '409 Conflict', 'ecriture_a_verifier': '409 Conflict'}
MESSAGES = {
    'introuvable': 'Document introuvable, ou action non permise sur ce document.',
    'version_perimee': 'Une version plus récente a été enregistrée entre-temps (autre onglet ?) : rechargez la page. Rien n’a été écrit.',
    'original_modifie': 'L’original a changé depuis l’ouverture (quelqu’un l’a modifié). Votre version peut quand même être gardée à côté, '
                        'sous un nom distinct ; l’original n’est jamais remplacé.',
    'cle_reutilisee': 'Cette demande a déjà servi pour une autre action : rechargez la page.',
    'operation_en_cours': 'Cette action est déjà en cours : patientez puis rechargez la page.',
    'garde_avertissement': 'Ce document contient des éléments que l’éditeur intégré abîme : confirmez explicitement l’ouverture ou choisissez une '
                           'autre façon de le modifier.',
    'role_insuffisant': 'Votre rôle ne permet pas de modifier les documents.',
    'origine_ou_csrf_refuse': 'Session expirée ou origine refusée : rechargez la page.',
    'wordcraft_desactive': 'L’éditeur intégré WordCraft est désactivé sur cette installation (réglage de l’administrateur).',
    'controle_acces_indisponible': 'Contrôle des accès indisponible : réessayez dans un instant. Rien n’a été affiché ni modifié.',
    'stockage_injoignable': 'Le stockage ne répond pas : le document n’a pas pu être lu ni écrit. Réessayez plus tard.',
    'requete_trop_longue': 'Demande trop longue.',
    'document_word_trop_volumineux': 'Document trop volumineux pour l’éditeur intégré (20 Mo au plus).',
    'document_word_illisible': 'Le fichier reçu n’est pas un document Word (.docx) lisible : rien n’a été gardé.',
    'format_non_docx': 'Seuls les documents Word (.docx) s’ouvrent ici.',
    'format_non_pris_en_charge': 'Seuls les documents Word (.docx) et PDF s’ouvrent avec l’application du poste.',
    'document_pdf_illisible': 'Le fichier n’est pas un PDF lisible : rien n’a été gardé.',
    'aucune_application_pdf': 'Aucune application n’est associée aux PDF sur ce poste (lecteur ou éditeur de PDF).',
    'non_pris_en_charge': 'Ce stockage ne permet pas d’enregistrer une nouvelle version.',
    'cle_requise': 'Demande incomplète : rechargez la page.',
    'decision_invalide': 'Décision inconnue.',
    'relecture_non_conforme': 'La version écrite ne correspond pas à celle contrôlée : vérifiez le dossier avant de recommencer.',
    'copie_privee_alteree': 'La copie de travail d’AxiorHub a été modifiée hors de l’application : rien n’a été écrit. Rouvrez le document.',
    'indisponible_sur_serveur': 'Ouvrir dans Word ou LibreOffice n’est possible que sur un poste (Windows ou Ubuntu) : sur le serveur, AxiorHub ne '
                                'peut pas lancer un logiciel sur votre ordinateur.',
    'aucun_traitement_de_texte': 'Aucun traitement de texte (Word ou LibreOffice) n’est installé sur ce poste.',
    'aucune_modification': 'La copie de travail n’a pas changé depuis l’ouverture : enregistrez d’abord dans le traitement de texte.',
    'lancement_impossible': 'Le traitement de texte n’a pas pu être lancé : ouvrez la copie de travail vous-même ou réessayez.',
    'json_requis': 'Format de demande refusé.', 'json_invalide': 'Format de demande refusé.', 'objet_json_requis': 'Format de demande refusé.',
    'type_document_invalide': 'Format de demande refusé.', 'acteur_requis': 'Demande refusée.',
    # 5.6.109 (C07, C05)
    'ecriture_a_verifier': 'Une écriture précédente reste à vérifier : utilisez « Vérifier » (ou « Abandonner cette version ») avant de poursuivre. '
                           'Votre version est conservée.',
    'operation_annulee': 'Opération annulée : rien de plus n’a été écrit.',
    'ia_en_pause': 'IA en pause : cette opération reprendra à la reprise. Rien n’a été écrit.',
}


def allowed(role, method, api):
    """``standalone_auth.allowed`` : liste fermée de routes. Lectures : tout rôle (la séance appartient à son auteur, contrôlée par le service) ;
    écritures : administrateur, avocat, assistant ; réglage : administrateur seulement."""
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    if method in ('GET', 'HEAD'):
        return reste in LECTURES
    if method != 'POST' or reste not in ECRITURES:
        return False
    return role == 'administrateur' if reste == 'reglage' else role in ROLES_EDITION


def _prive_reponse(out):
    out['headers'] = [h for h in out.get('headers', []) if h[0].lower() != 'cache-control'] + [('Cache-Control', 'private, no-store')]
    return out


def _json(data, status='200 OK'):
    from .web440 import json_out
    return _prive_reponse(json_out(data, status))


def message(code):
    return MESSAGES.get(code, 'Demande refusée.')


def _corps_binaire(env, auth):
    """Corps d'un enregistrement : Origin et CSRF exacts, type octet-stream, longueur bornée (lu une seule fois)."""
    import hmac
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        longueur = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError:
        raise Stop('requete_trop_longue') from None
    if longueur > MAX_DOCX:
        raise Stop('document_word_trop_volumineux')
    if longueur <= 0:
        raise Stop('document_word_illisible')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/octet-stream':
        raise Stop('type_document_invalide')
    return env['wsgi.input'].read(longueur)


def api(env, desk, auth, prefix, name, args, method, sources_=None):
    from .web567 import actor
    acteur = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    if reste not in LECTURES + ECRITURES:
        return _json({'error': 'introuvable', 'message': MESSAGES['introuvable']}, STATUTS['introuvable'])
    if not allowed(acteur[1], method, name):
        code = 'role_insuffisant' if method == 'POST' and reste in ECRITURES else 'introuvable'
        return _json({'error': code, 'message': MESSAGES[code]}, STATUTS[code])
    try:
        if reste == 'fichier':
            data, nom = octets_original(desk, auth, acteur, str(args.get('seance') or ''), sources_)
            from urllib.parse import quote
            return _prive_reponse({'status': '200 OK', 'kind': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'body': data,
                                   'headers': [('Content-Disposition', "attachment; filename*=UTF-8''" + quote(nom, safe='')),
                                               ('X-Content-Type-Options', 'nosniff')]})
        if reste == 'word/etat':
            from . import ouvrir_word56108 as OW
            return _json(OW.etat(desk, auth, acteur, str(args.get('seance') or ''), sources_))
        if reste == 'enregistrer':
            corps = _corps_binaire(env, auth)
            ident, revision = str(args.get('seance') or ''), args.get('revision')
            empreinte = hashlib.sha256(corps).hexdigest()
            # 5.6.109 (C08) : l'hôte numérote chaque instantané reçu de l'éditeur ; le reçu lui rend ce numéro avec la révision du candidat :
            # un reçu n'acquitte que SON instantané (une saisie postérieure n'est jamais déclarée enregistrée par le reçu d'une version antérieure).
            instantane = str(args.get('instantane') or '')
            if instantane and not re.fullmatch(r'\d{1,9}', instantane):
                raise Stop('instantane_invalide')
            out = executer(desk, acteur, args.get('cle'), 'enregistrer',
                           {'seance': ident, 'revision': str(revision), 'sha256': empreinte, **({'instantane': instantane} if instantane else {})},
                           lambda: proposer(desk, auth, acteur, ident, revision, corps, sources_))
            return _json({**out, **({'instantane': int(instantane)} if instantane else {})})
        if int(env.get('CONTENT_LENGTH') or 0) > MAX_POST:
            raise Stop('requete_trop_longue')
        from .web440 import check_post
        d = check_post(env, auth)
        out = _ecrire(desk, auth, acteur, reste, d, sources_)
        # 5.6.109 (C07) : résultat incertain : accepté, pas encore constaté (ni un succès ni un échec)
        return _json(out, '202 Accepted' if isinstance(out, dict) and out.get('etat') == 'a_verifier' else '200 OK')
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'chemin_est_un_dossier', 'fichier_nextcloud_introuvable', 'http_404'):
            code = 'introuvable'
        return _json({'error': code, 'message': message(code)}, STATUTS.get(code, '422 Unprocessable Entity'))


def _ecrire(desk, auth, acteur, reste, d, sources_=None):
    cle = d.get('cle')
    if reste == 'reglage':
        return {**executer(desk, acteur, cle, 'reglage', {'active': bool(d.get('active'))}, lambda: {'reglages': regler(desk, acteur, d.get('active'))}),
                'message': 'Réglage enregistré : éditeur intégré WordCraft %s.' % ('proposé' if d.get('active') else 'désactivé')}
    if reste == 'ouvrir':
        if not actif(desk):
            raise Stop('wordcraft_desactive')
        s, p, accepte = str(d.get('s') or ''), str(d.get('p') or ''), bool(d.get('accepte'))
        return executer(desk, acteur, cle, 'ouvrir', {'s': s, 'p': p, 'accepte': accepte},
                        lambda: ouvrir_seance(desk, auth, acteur, s, p, 'wordcraft', accepte, sources_))
    if reste == 'word/ouvrir':
        from . import ouvrir_word56108 as OW
        s, p = str(d.get('s') or ''), str(d.get('p') or '')
        return executer(desk, acteur, cle, 'word_ouvrir', {'s': s, 'p': p}, lambda: OW.ouvrir(desk, auth, acteur, s, p, sources_=sources_))
    ident = str(d.get('seance') or '')
    if reste == 'word/reprendre':
        from . import ouvrir_word56108 as OW
        vu = str(d.get('sha256') or '')
        return executer(desk, acteur, cle, 'word_reprendre', {'seance': ident, 'sha256': vu},
                        lambda: OW.reprendre(desk, auth, acteur, ident, vu, sources_))
    if reste == 'decider':
        decision, revision, malgre = str(d.get('decision') or ''), d.get('revision'), bool(d.get('malgre_changement'))
        reessayer = d.get('reessayer') is True   # 5.6.109 (C07) : « Réessayer l'écriture », seulement sur la cible figée constatée absente

        def faire():
            outil = seance(desk, acteur, ident)['outil']
            out = decider(desk, auth, acteur, ident, revision, decision, malgre, sources_, cle=cle, reessayer=reessayer)
            if outil == 'traitement' and decision == 'garder' and out.get('etat') != 'a_verifier' and not out.get('rejoue'):
                # copie de travail du poste nettoyée après reprise (cahier, point 8) ; jamais tant que l'écriture reste à vérifier
                out['ferme'] = fermer(desk, acteur, ident)
                out['message'] += ' La copie de travail du poste a été retirée : fermez le document dans l’application.'
            return out
        parametres = {'seance': ident, 'revision': str(revision), 'decision': decision, 'malgre': malgre, **({'reessayer': True} if reessayer else {})}
        return executer(desk, acteur, cle, 'decider', parametres, faire)
    return executer(desk, acteur, cle, 'fermer', {'seance': ident}, lambda: fermer(desk, acteur, ident))


# ------------------------------------------------------------------------------------------------------------------- boutons des autres écrans
def libelle_traitement(chemin):
    """« Ouvrir dans Word / LibreOffice » pour un .docx, « Ouvrir avec l'application du poste » pour un PDF."""
    return 'Ouvrir avec l’application du poste' if nature(chemin) == 'pdf' else 'Ouvrir dans Word / LibreOffice'


def liens(desk, auth, prefix, ident_source, chemin, role='administrateur'):
    """Liens calculés par le serveur (vide sinon) : ``wordcraft_url`` (.docx, éditeur intégré proposé) et ``traitement_url`` (.docx ou .pdf,
    poste seulement), avec ``traitement_libelle``. Point de montage commun : aperçu, Fichiers, Produire, onglet Fichiers d'un dossier."""
    from urllib.parse import urlencode
    if not nature(chemin) or role not in ROLES_EDITION:
        return {}
    out = {}
    q = urlencode({'s': ident_source, 'p': chemin})
    try:
        if nature(chemin) == 'docx' and actif(desk):
            out['wordcraft_url'] = prefix + '/editeur/wordcraft?' + q
    except Exception:   # noqa: BLE001 — réglage illisible : rien de proposé
        pass
    try:
        from . import ouvrir_word56108 as OW
        if OW.disponible(auth):
            out['traitement_url'] = prefix + '/editeur/traitement?' + q
            out['traitement_libelle'] = libelle_traitement(chemin)
    except Exception:   # noqa: BLE001
        pass
    return out


def boutons_html(desk, auth, prefix, ident_source, chemin, role='administrateur', titre=''):
    """Fragment HTML réutilisable (liens de classe ``ax-btn ghost``) pour un document d'une liste : « Modifier dans l'éditeur intégré
    (WordCraft, version d'essai) » pour un .docx et « Ouvrir dans Word / LibreOffice » (.docx) ou « Ouvrir avec l'application du poste »
    (.pdf) sur un poste ; chaîne vide sinon. ``auth`` : dictionnaire de session (None : poste reconnu par l'environnement seulement) ;
    ``ident_source`` : source de la page Fichiers ('nextcloud' pour les dossiers) ; ``titre`` complète le nom accessible. Les pages ouvertes
    revérifient droits, dossier réservé et format."""
    l = liens(desk, auth, prefix, ident_source, chemin, role)
    sr = ('<span class="sr-only"> : %s</span>' % e(titre)) if titre else ''
    out = ''
    if l.get('wordcraft_url'):
        out += '<a class="ax-btn ghost" href="%s">Modifier dans l’éditeur intégré (WordCraft, version d’essai)%s</a>' % (e(l['wordcraft_url'], quote=True), sr)
    if l.get('traitement_url'):
        out += '<a class="ax-btn ghost" href="%s">%s%s</a>' % (e(l['traitement_url'], quote=True), e(l['traitement_libelle']), sr)
    try:   # 5.6.109 (lot V, F16) : éditeur Craft facultatif (XLSX, PDF, PPTX), seulement s'il est activé et installé (désactivé par défaut)
        from .craft56109 import bouton_html as _craft109
        out += _craft109(desk, prefix, ident_source, chemin, role, titre)
    except Exception:   # noqa: BLE001 — l'option facultative ne bloque jamais les boutons existants
        pass
    return out


def section_reglages(desk):
    """Section des Réglages de l'atelier (administrateur) : WordCraft proposé ou non."""
    r = reglages(desk)
    return ('<section class="ax-card w108-reglage" id="ax-wordcraft-56108"><h2>Éditeur intégré sans serveur de documents (WordCraft, version d’essai)</h2>'
            '<p class="ax-muted">WordCraft %s (logiciel libre, MIT ou Apache-2.0, « alpha » selon ses auteurs) modifie un document Word dans le navigateur, '
            'sans serveur de documents. Il est servi par AxiorHub ; aucune donnée ne sort. Chaque enregistrement crée une nouvelle version à côté de '
            'l’original après un contrôle des pertes ; l’original n’est jamais remplacé.</p>'
            '<form class="m5-form" data-api="%sreglage" data-reload="1"><input type="hidden" name="cle" value="%s">'
            '<label class="e5636-switch"><input type="checkbox" name="active" value="1"%s> Proposer « Modifier dans l’éditeur intégré » '
            '(aperçu d’un document, Fichiers, Produire)</label><button class="ax-btn ghost" type="submit">Enregistrer</button></form>'
            '<p class="ax-muted">Sur un poste (Windows 11, Ubuntu 24.04), « Ouvrir dans Word / LibreOffice » reste proposé quel que soit ce réglage.</p>'
            '</section>') % (VERSION, PREFIXE, secrets.token_hex(12), ' checked' if r['active'] else '')


# ------------------------------------------------------------------------------------------------------------------- pages
def _page_simple(shell, prefix, auth, titre, corps, status='200 OK'):
    from .web440 import page_out
    head = '<script defer src="%s/static/v56108-editeur.js"></script>' % e(prefix, quote=True)
    out = page_out(shell(titre, '<section class="w108" aria-labelledby="w108-titre">%s</section>' % corps, prefix, auth['csrf'], '/fichiers', head=head))
    out['status'] = status
    return _prive_reponse(out)


def _introuvable(shell, prefix, auth, titre):
    return _page_simple(shell, prefix, auth, titre, '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">%s</p>' % (
        e(titre), e(MESSAGES['introuvable'])), '404 Not Found')


def _constats_html(g):
    if not g['constats']:
        return '<p class="w108-ok">Aucun élément sensible repéré (suivi des modifications, commentaires, champs, objets…).</p>'
    lignes = ''.join('<li class="w108-c-%s"><strong>%s</strong> <span class="w108-nombre">(%d)</span> <span>%s</span></li>' % (
        c['niveau'], e(c['libelle']), c['nombre'], e(c['etat'])) for c in g['constats'])
    return '<ul class="w108-constats">%s</ul>' % lignes


def _alternatives_html(desk, auth, prefix, s, p):
    from urllib.parse import urlencode
    out = []
    try:
        from . import office440
        if office440.settings(desk)['enabled'] and s == 'nextcloud':
            from .fiche460 import doc_href
            out.append('<a class="ax-btn" href="%s">Ouvrir dans l’éditeur de documents (serveur)</a>' % e(prefix + doc_href(p, office440.matter_of(desk, p)), quote=True))
    except Exception:   # noqa: BLE001 — éditeur non configuré : pas proposé
        pass
    try:
        from . import ouvrir_word56108 as OW
        if OW.disponible(auth):
            out.append('<a class="ax-btn" href="%s">Ouvrir dans Word / LibreOffice</a>' % e(prefix + '/editeur/traitement?' + urlencode({'s': s, 'p': p}), quote=True))
    except Exception:   # noqa: BLE001
        pass
    return ''.join(out)


def page_route(env, cfg, auth, prefix, path, args, shell, sources_=None):
    from .desk import Desk
    from .web567 import actor
    acteur = actor(env)
    if path == '/editeur/wordcraft/cadre':
        return page_cadre(prefix)
    desk = Desk(cfg)
    try:
        if path == '/editeur/traitement':
            from . import ouvrir_word56108 as OW
            return OW.page(desk, auth, prefix, acteur, args, shell, sources_)
        return page_wordcraft(desk, auth, prefix, acteur, args, shell, sources_)
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


def lire_pour_page(desk, auth, acteur, s, p, sources_=None, formats=('docx',)):
    """Lecture seule (GET) : droits, octets, analyse. Rend (source, chemin, propriétés, analyse, octets) ; Stop sinon."""
    source, chemin = _source(desk, auth, acteur, s, p, sources_)
    props, data = _lire_original(source, chemin, formats)
    return source, chemin, props, _analyser_selon(chemin, data), data


def page_wordcraft(desk, auth, prefix, acteur, args, shell, sources_=None):
    titre = 'Modifier dans l’éditeur intégré'
    s, p = str(args.get('s') or ''), str(args.get('p') or '')
    if acteur[1] not in ROLES_EDITION:
        return _page_simple(shell, prefix, auth, titre, '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">%s</p>' % (
            e(titre), e(MESSAGES['role_insuffisant'])), '403 Forbidden')
    if not actif(desk):
        return _page_simple(shell, prefix, auth, titre, (
            '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">%s</p><p>Pour modifier ce document : %s</p>') % (
            e(titre), e(MESSAGES['wordcraft_desactive']), _alternatives_html(desk, auth, prefix, s, p) or
            'téléchargez-le depuis la page Fichiers, ou demandez à l’administrateur de relier un serveur de documents.'))
    try:
        source, chemin, props, analyse, data = lire_pour_page(desk, auth, acteur, s, p, sources_)
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('introuvable', 'dossier_reserve', 'chemin_est_un_dossier'):
            return _introuvable(shell, prefix, auth, titre)
        return _page_simple(shell, prefix, auth, titre, '<h1 id="w108-titre">%s</h1><p class="w108-alerte" role="status">%s</p>' % (
            e(titre), e(message(code))), STATUTS.get(code, '422 Unprocessable Entity'))
    g = garde(analyse)
    nom = PurePosixPath(chemin).name
    config = {'api': prefix + '/api440/' + PREFIXE, 's': source.ident, 'p': chemin, 'nom': nom, 'niveau': g['niveau'],
              'cadre': prefix + '/editeur/wordcraft/cadre?host=parent&author=' + _auteur(acteur[0])}
    avertissement = ''
    if g['niveau'] == 'risque':
        avertissement = ('<div class="w108-avertissement" role="alert"><h2>Attention : ce document contient des éléments que l’éditeur intégré abîme</h2>'
                         '<p>D’après la mesure de fidélité, l’enregistrement dans WordCraft perd ou modifie ces éléments. Préférez :</p>'
                         '<p class="w108-actions">%s</p><label class="w108-confirmer"><input type="checkbox" data-w108-accepte> J’ai compris : '
                         'j’ouvre quand même dans WordCraft et je vérifierai le contrôle avant de garder une version.</label></div>') % (
            _alternatives_html(desk, auth, prefix, source.ident, chemin) or 'Word ou le serveur de documents (non disponibles ici).')
    elif g['niveau'] == 'prudence':
        avertissement = ('<div class="w108-prudence" role="note"><p>Ce document contient des éléments que la mesure de fidélité n’a pas pu vérifier : '
                         'le contrôle après enregistrement dira s’ils sont conservés.</p><p class="w108-actions">%s</p></div>') % (
            _alternatives_html(desk, auth, prefix, source.ident, chemin))
    corps = ('<h1 id="w108-titre">%s</h1><p class="w108-doc"><span class="w108-nom">%s</span> <span class="w108-badge">version d’essai</span></p>'
             '<p class="w108-note">WordCraft %s est un logiciel libre en version d’essai (« alpha » selon ses auteurs). Chaque enregistrement est '
             'contrôlé puis gardé, si vous le décidez, comme nouvelle version à côté de l’original ; l’original n’est jamais remplacé.</p>'
             '<section class="w108-garde" aria-labelledby="w108-t-garde"><h2 id="w108-t-garde">Avant d’ouvrir</h2>%s%s'
             '<p class="w108-actions"><button type="button" class="ax-btn" data-w108="ouvrir"%s>Ouvrir dans WordCraft</button></p></section>'
             '%s<p class="w108-etat" role="status" aria-live="polite" data-w108-etat></p>'
             '<div class="w108-zone" data-w108-zone hidden><iframe class="w108-iframe" title="Éditeur WordCraft : %s" data-w108-iframe></iframe></div>'
             '<section class="w108-controle" data-w108-controle hidden aria-labelledby="w108-t-controle"><h2 id="w108-t-controle" tabindex="-1">'
             'Contrôle de la version enregistrée</h2><div data-w108-rapport></div><p class="w108-actions">'
             '<button type="button" class="ax-btn" data-w108="garder">Garder cette version</button>'
             '<button type="button" class="ax-btn ghost" data-w108="abandonner">Abandonner cette version</button></p>'
             '<label class="w108-confirmer" data-w108-malgre hidden><input type="checkbox" data-w108-malgre-case> L’original a changé : garder '
             'quand même ma version à côté, sous un nom distinct.</label></section>'
             '<p class="w108-aide">Enregistrez dans WordCraft (Fichier › Enregistrer ou Ctrl+S) : AxiorHub reçoit le document, le compare à l’original et '
             'vous montre les pertes éventuelles avant toute écriture dans le dossier. <button type="button" class="ax-btn ghost" data-w108="fermer" hidden>'
             'Fermer l’éditeur</button></p>'
             '<div hidden data-w108-config="%s"></div>') % (
        e(titre), e(nom), VERSION, avertissement, _constats_html(g), ' disabled' if g['niveau'] == 'risque' else '',
        incertaines_html(desk, acteur, source.ident, chemin), e(nom), e(json.dumps(config, ensure_ascii=False), quote=True))
    return _page_simple(shell, prefix, auth, titre, corps)


def _auteur(compte):
    from urllib.parse import quote
    return quote(str(compte or 'AxiorHub')[:60], safe='')


CADRE_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self'; img-src 'self'; style-src 'self'; font-src 'self' data:; "
             "frame-ancestors 'self'; base-uri 'none'; form-action 'none'")


def page_cadre(prefix):
    """Cadre de l'éditeur (même origine, ``?host=parent``) : la page ne charge que WordCraft et le script du lot ; ``window.open`` neutralisé."""
    p = e(prefix, quote=True)
    html = ('<!doctype html><html lang="fr" data-w108="cadre" data-w108-statique="%s/static/%s/" data-w108-version="%s"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>WordCraft</title>'
            '<link rel="stylesheet" href="%s/static/v56108-editeur.css"><script defer src="%s/static/v56108-editeur.js"></script></head>'
            '<body class="w108-cadre-corps"><canvas id="wordcraft_canvas" class="w108-toile"></canvas>'
            '<p class="w108-chargement" data-w108-chargement>Chargement de l’éditeur…</p></body></html>') % (p, DOSSIER_STATIQUE, VERSION, p, p)
    return _prive_reponse({'status': '200 OK', 'kind': 'text/html; charset=utf-8', 'body': html, 'csp': CADRE_CSP, 'frame_self': True})

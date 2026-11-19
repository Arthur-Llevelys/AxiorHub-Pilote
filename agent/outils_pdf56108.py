"""5.6.108 (lot F) : boîte à outils PDF interne de l'onglet Fichiers du dossier (décision du cabinet du 18 novembre 2026).

Sur un PDF du dossier (ou une sélection de PDF) : **annoter** (texte libre, surlignage, dessin, image ou signature visible, tampon),
**fusionner**, **découper**, **réorganiser / pivoter / supprimer des pages**, **numéroter** (pages, tampon « Pièce n° X » proposé d'après le
bordereau du dossier), **caviarder pour de vrai**, **compresser**. Chaque résultat est un **nouveau fichier** à côté de l'original, qui n'est
jamais réécrit ; toutes les écritures passent par le moteur commun ``pdf_formulaire56108.produire`` (droits du dossier, 404 neutre, empreinte
des sources et 409, clé d'idempotence, réservation, dépôt exclusif, relecture, registre commun des livrables).

Sans dépendance nouvelle : pypdf embarqué (toutes les opérations), Poppler et Tesseract déjà fournis pour la lecture des scans (caviardage,
recompression des images), PDF.js déjà intégré (visionneuse de la page, avec une couche de tracé propre à AxiorHub).

- **Annoter** : les tracés faits sur la visionneuse (coordonnées relatives à la page affichée, rotation comprise) deviennent de vraies
  annotations PDF (FreeText, Highlight, Ink, Stamp) avec leur apparence dessinée : elles s'affichent dans tous les lecteurs. L'éditeur
  d'annotations natif de PDF.js n'est pas utilisé : il dépend des composants internes de la visionneuse complète de Mozilla (gestionnaire
  d'interface, bus d'événements), absents d'AxiorHub, et son enregistrement réécrit tout le PDF dans le navigateur.
- **Caviarder** : zones tracées et/ou textes recherchés (positions lues par ``pdftotext -bbox-layout``) ; chaque page concernée est
  rastérisée par ``pdftoppm`` (150 points par pouce), les zones noircies dans l'image même, la page reconstruite et rendue cherchable par
  Tesseract (couche de texte de la seule image noircie). La copie ne reprend ni métadonnées, ni signets, ni annotations, ni pièces jointes,
  ni formulaire, ni scripts. **Contrôle obligatoire** : texte de la copie extrait par pypdf ET par ``pdftotext``, flux décompressés,
  métadonnées et structure examinés ; un texte caviardé retrouvé = copie refusée, rien n'est déposé (fichiers temporaires effacés).
  Outils absents : refus qui dit quoi installer, aucune tentative dégradée.
- **Compresser** : images recompressées en JPEG à la qualité choisie par ``pdftoppm`` (seulement si plus petites), images identiques
  dédoublonnées, contenus des pages recompressés ; gain mesuré ; copie refusée si elle n'est pas plus petite. Sans Poppler : seulement les
  étapes sans perte, dit à l'écran.
- Gardes : celles de ``pdf_formulaire56108`` (taille, objets, pages, délai, aucun JavaScript du PDF exécuté, PDF chiffré refusé). Un PDF
  signé peut servir de source : la copie ne porte plus de signature (champs de signature vidés), ce qui est dit dans le résultat.
- Aucun modèle d'IA, aucun réseau : tout est local ; la pause de l'IA n'a aucun effet sur ces commandes de l'avocat.
"""
import base64
from datetime import datetime
from html import escape as e
from io import BytesIO
import hashlib
import json
import logging
import math
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import urlencode
import zlib

from . import acces5697
from . import pdf_formulaire56108 as F
from .common import Stop, fold, load_matters

log = logging.getLogger(__name__)
PAGE = '/dossier/outils-pdf'
OPERATIONS = ('annoter', 'fusionner', 'decouper', 'organiser', 'numeroter', 'caviarder', 'compresser')
MAX_SOURCES = 20
MAX_PAGES_FUSION = 1000
MAX_SORTIES = 50
MAX_ANNOTATIONS = 200
MAX_POINTS = 4000
MAX_IMAGE = 2_000_000
MAX_PAGES_CAVIARDEES = 30
MAX_IMAGES = 400
RESOLUTION = 150
DELAI_CAVIARDAGE = 240.0
DELAI_COMPRESSION = 180.0
TAMPONS = {'copie': 'COPIE', 'confidentiel': 'CONFIDENTIEL', 'projet': 'PROJET', 'vu': 'VU', 'original': 'ORIGINAL'}
QUALITES = {'haute': (85, 2400), 'moyenne': (70, 1800), 'forte': (50, 1300)}
POSITIONS = ('bas-droite', 'bas-centre', 'haut-droite')
COULEURS = {'jaune': (1, 0.92, 0.2), 'vert': (0.55, 0.95, 0.45), 'bleu': (0.1, 0.3, 0.85), 'rouge': (0.85, 0.1, 0.1), 'noir': (0, 0, 0)}
MESSAGES = {
    'outils_caviardage_absents': '', 'caviardage_controle_echoue': 'Contrôle du caviardage échoué : un texte caviardé subsistait dans la copie. '
    'La copie a été refusée et effacée ; rien n’a été déposé. L’original est intact.',
    'texte_a_caviarder_introuvable': 'Texte à caviarder introuvable dans le PDF (ou situé dans une image) : tracez la zone à la main.',
    'rien_a_caviarder': 'Tracez au moins une zone ou indiquez un texte à caviarder.',
    'trop_de_pages_a_caviarder': 'Trop de pages à caviarder en une fois (30 au plus).',
    'plages_invalides': 'Plages de pages invalides : écrivez par exemple « 1-3, 5, 8- ».', 'ordre_invalide': 'Ordre des pages invalide : rechargez la page.',
    'annotations_invalides': 'Annotation invalide : rechargez la page.', 'image_invalide': 'Image refusée : JPEG ou PNG de 2 Mo au plus.',
    'aucune_annotation': 'Aucune annotation tracée : rien n’a été enregistré.', 'deux_pdf_au_moins': 'Choisissez au moins deux PDF à fusionner.',
    'numerotation_vide': 'Choisissez la numérotation des pages ou un numéro de pièce.', 'numero_piece_invalide': 'Numéro de pièce invalide (20 caractères au plus).',
    'compression_sans_gain': 'Aucun gain : la copie compressée ne serait pas plus petite. Rien n’a été enregistré.',
    'trop_de_sources': 'Trop de PDF choisis (20 au plus).', 'operation_inconnue': 'Opération inconnue.',
}
F.MESSAGES.update({k: v for k, v in MESSAGES.items() if v})
F.STATUTS.update({'outils_caviardage_absents': '503 Service Unavailable'})


# ------------------------------------------------------------------------------------------------ lecture commune
def _lecteur(raw, echeance=None):
    reader = F._lecteur(raw)
    if len(reader.pages) > F.MAX_PAGES:
        raise Stop('pdf_trop_complexe')
    F._echeance(echeance)
    return reader


def _signe(reader):
    racine = reader.trailer['/Root'].get_object()
    acro = F._obj(racine.get('/AcroForm'))
    return racine.get('/Perms') is not None or bool(acro is not None and int(F._obj(acro.get('/SigFlags')) or 0) & 1)


def _sans_signature(writer):
    """Copie d'un PDF signé : la signature ne vaut plus pour la copie ; les champs de signature sont vidés, la certification retirée."""
    racine = writer._root_object
    if '/Perms' in racine:
        del racine['/Perms']
    acro = F._obj(racine.get('/AcroForm'))
    if acro is None:
        return
    if '/SigFlags' in acro:
        del acro['/SigFlags']
    for page in writer.pages:
        for ref in F._obj(page.get('/Annots')) or []:
            w = F._obj(ref)
            champ = F._champ_du_widget(w) if hasattr(w, 'get') else None
            if champ is not None and str(F._chaine(champ, '/FT') or '') == '/Sig' and '/V' in champ:
                del champ['/V']


def _ecrire(writer):
    tampon = BytesIO()
    writer.write(tampon)
    return tampon.getvalue()


def _cloner(raw, echeance=None):
    from ._vendor.pypdf import PdfReader, PdfWriter
    reader = _lecteur(raw, echeance)
    signe = _signe(reader)
    writer = PdfWriter(clone_from=PdfReader(BytesIO(bytes(raw)), strict=False))
    if signe:
        _sans_signature(writer)
    return writer, signe


# ------------------------------------------------------------------------------------------------ coordonnées de la page affichée
def _cadre(page):
    box = page.cropbox if '/CropBox' in page else page.mediabox
    x0, y0, x1, y1 = (float(v) for v in (box.left, box.bottom, box.right, box.top))
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), int(page.get('/Rotate') or 0) % 360


def point(page, u, v):
    """Fractions (u : de gauche à droite, v : de haut en bas) de la page AFFICHÉE (rotation comprise) → espace de la page."""
    x0, y0, x1, y1, rot = _cadre(page)
    w, h = x1 - x0, y1 - y0
    u, v = min(max(float(u), 0.0), 1.0), min(max(float(v), 0.0), 1.0)
    if rot == 90:
        return x0 + v * w, y0 + u * h
    if rot == 180:
        return x1 - u * w, y0 + v * h
    if rot == 270:
        return x1 - v * w, y1 - u * h
    return x0 + u * w, y1 - v * h


def rectangle(page, r):
    """[u0, v0, u1, v1] (fractions de la page affichée) → [x0, y0, x1, y1] dans l'espace de la page."""
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        raise Stop('annotations_invalides')
    a, b = point(page, r[0], r[1]), point(page, r[2], r[3])
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]


def _angle(page):
    return _cadre(page)[4]


def _pdf_texte(texte):
    """Chaîne littérale PDF en WinAnsi (Helvetica standard) : parenthèses et barres obliques échappées."""
    brut = str(texte).encode('cp1252', 'replace')
    return b'(' + brut.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)').replace(b'\r', b' ').replace(b'\n', b' ') + b')'


def _police_dict(gras=False):
    from ._vendor.pypdf.generic import DictionaryObject, NameObject
    return DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica-Bold' if gras else '/Helvetica'),
                             NameObject('/Encoding'): NameObject('/WinAnsiEncoding')})


def _forme(writer, largeur, hauteur, contenu, ressources=None):
    from ._vendor.pypdf.generic import ArrayObject, DecodedStreamObject, FloatObject, NameObject
    flux = DecodedStreamObject()
    flux.set_data(contenu)
    flux.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                 NameObject('/BBox'): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(round(largeur, 3)), FloatObject(round(hauteur, 3))])})
    if ressources is not None:
        flux[NameObject('/Resources')] = ressources
    return writer._add_object(flux)


def _lignes(texte, largeur, taille):
    """Découpe approximative à la largeur (Helvetica : environ 0,5 em par caractère)."""
    par_ligne = max(4, int(largeur / (taille * 0.5)))
    out = []
    for para in str(texte).split('\n'):
        mots, ligne = para.split(' '), ''
        for m in mots:
            if ligne and len(ligne) + 1 + len(m) > par_ligne:
                out.append(ligne)
                ligne = m
            else:
                ligne = (ligne + ' ' + m) if ligne else m
        out.append(ligne)
    return out[:200]


# ------------------------------------------------------------------------------------------------ annoter
def jpeg_infos(data):
    """(largeur, hauteur, composantes) d'un JPEG, lus dans son en-tête SOF ; Stop sinon."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_IMAGE or data[:2] != b'\xff\xd8':
        raise Stop('image_invalide')
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            raise Stop('image_invalide')
        marque = data[i + 1]
        if marque in (0xD8, 0x01) or 0xD0 <= marque <= 0xD7:
            i += 2
            continue
        longueur = int.from_bytes(data[i + 2:i + 4], 'big')
        if marque in (0xC0, 0xC1, 0xC2):
            h, w, n = int.from_bytes(data[i + 5:i + 7], 'big'), int.from_bytes(data[i + 7:i + 9], 'big'), data[i + 9]
            if not (0 < w <= 6000 and 0 < h <= 6000 and n in (1, 3)):
                raise Stop('image_invalide')
            return w, h, n
        i += 2 + longueur
    raise Stop('image_invalide')


def _annotation(writer, page, a, auteur, jour, rang):
    from ._vendor.pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject,
                                        TextStringObject)
    t = str(a.get('type') or '')
    couleur = COULEURS.get(str(a.get('couleur') or ''), None)
    fl = lambda xs: ArrayObject([FloatObject(round(float(x), 3)) for x in xs])
    base = {NameObject('/Type'): NameObject('/Annot'), NameObject('/F'): NumberObject(4), NameObject('/T'): TextStringObject(auteur[:120]),
            NameObject('/M'): TextStringObject('D:' + jour.replace('-', '')), NameObject('/P'): page.indirect_reference,
            NameObject('/NM'): TextStringObject('axiorhub-%s-%d' % (hashlib.sha256(json.dumps(a, sort_keys=True, default=str).encode()).hexdigest()[:16], rang))}
    if t == 'dessin':
        traits = a.get('traits')
        if not isinstance(traits, list) or not traits or sum(len(x) for x in traits if isinstance(x, list)) > MAX_POINTS:
            raise Stop('annotations_invalides')
        pts = [[point(page, p[0], p[1]) for p in trait if isinstance(p, (list, tuple)) and len(p) == 2] for trait in traits if isinstance(trait, list)]
        pts = [x for x in pts if len(x) >= 2]
        if not pts:
            raise Stop('annotations_invalides')
        xs, ys = [p[0] for x in pts for p in x], [p[1] for x in pts for p in x]
        r = [min(xs) - 2, min(ys) - 2, max(xs) + 2, max(ys) + 2]
        c = couleur or COULEURS['bleu']
        chemin = ' '.join(' '.join(('%.2f %.2f %s' % (p[0] - r[0], p[1] - r[1], 'm' if i == 0 else 'l')) for i, p in enumerate(x)) + ' S' for x in pts)
        ap = ('%.3f %.3f %.3f RG 2 w 1 J 1 j %s' % (c[0], c[1], c[2], chemin)).encode('ascii')
        d = DictionaryObject({**base, NameObject('/Subtype'): NameObject('/Ink'), NameObject('/Rect'): fl(r), NameObject('/C'): fl(c),
                              NameObject('/InkList'): ArrayObject([fl([v for p in x for v in p]) for x in pts]),
                              NameObject('/BS'): DictionaryObject({NameObject('/W'): NumberObject(2)})})
    else:
        r = rectangle(page, a.get('rect'))
        w, h = r[2] - r[0], r[3] - r[1]
        if w < 2 or h < 2:
            raise Stop('annotations_invalides')
        if t == 'surligner':
            c = couleur or COULEURS['jaune']
            gs = DictionaryObject({NameObject('/ExtGState'): DictionaryObject({NameObject('/G0'): DictionaryObject({
                NameObject('/Type'): NameObject('/ExtGState'), NameObject('/BM'): NameObject('/Multiply'), NameObject('/ca'): FloatObject(0.45)})})})
            ap = ('/G0 gs %.3f %.3f %.3f rg 0 0 %.2f %.2f re f' % (c[0], c[1], c[2], w, h)).encode('ascii')
            d = DictionaryObject({**base, NameObject('/Subtype'): NameObject('/Highlight'), NameObject('/Rect'): fl(r), NameObject('/C'): fl(c),
                                  NameObject('/QuadPoints'): fl([r[0], r[3], r[2], r[3], r[0], r[1], r[2], r[1]]),
                                  NameObject('/Contents'): TextStringObject(str(a.get('texte') or '')[:2000])})
            d[NameObject('/AP')] = DictionaryObject({NameObject('/N'): _forme(writer, w, h, ap, gs)})
            return d
        if t == 'texte':
            texte = str(a.get('texte') or '').strip()[:2000]
            if not texte:
                raise Stop('annotations_invalides')
            taille = 11
            lignes = _lignes(texte, w - 4, taille)
            corps = b'BT /Helv %d Tf 0 0 0 rg ' % taille + b' '.join(
                b'1 0 0 1 2 %.2f Tm %s Tj' % (h - (i + 1) * taille * 1.2, _pdf_texte(l)) for i, l in enumerate(lignes)) + b' ET'
            ap = b'q 1 1 0.85 rg 0 0 %.2f %.2f re f 0.4 0.4 0.4 RG 0.5 w 0.25 0.25 %.2f %.2f re S Q ' % (w, h, w - 0.5, h - 0.5) + corps
            res = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/Helv'): writer._add_object(_police_dict())})})
            d = DictionaryObject({**base, NameObject('/Subtype'): NameObject('/FreeText'), NameObject('/Rect'): fl(r),
                                  NameObject('/Contents'): TextStringObject(texte), NameObject('/DA'): TextStringObject('/Helv %d Tf 0 0 0 rg' % taille)})
            d[NameObject('/AP')] = DictionaryObject({NameObject('/N'): _forme(writer, w, h, ap, res)})
            return d
        if t == 'tampon':
            libelle = TAMPONS.get(str(a.get('tampon') or ''))
            if libelle is None:
                piece = str(a.get('piece') or '').strip()
                if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z .\-/]{0,19}', piece):
                    raise Stop('annotations_invalides')
                libelle = 'PIÈCE N° ' + piece
            c = couleur or COULEURS['rouge']
            taille = max(6.0, min(h * 0.55, w / max(1, len(libelle)) * 1.6))
            largeur_texte = len(libelle) * taille * 0.62
            corps = b'BT /HeBo %.2f Tf %.3f %.3f %.3f rg 1 0 0 1 %.2f %.2f Tm %s Tj ET' % (
                taille, c[0], c[1], c[2], max(2.0, (w - largeur_texte) / 2), (h - taille * 0.7) / 2, _pdf_texte(libelle))
            ap = b'q %.3f %.3f %.3f RG 2 w 1 1 %.2f %.2f re S Q ' % (c[0], c[1], c[2], w - 2, h - 2) + corps
            res = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/HeBo'): writer._add_object(_police_dict(True))})})
            d = DictionaryObject({**base, NameObject('/Subtype'): NameObject('/Stamp'), NameObject('/Rect'): fl(r), NameObject('/Contents'): TextStringObject(libelle)})
            d[NameObject('/AP')] = DictionaryObject({NameObject('/N'): _forme(writer, w, h, ap, res)})
            return d
        if t == 'image':
            try:
                data = base64.b64decode(str(a.get('image') or '').split(',')[-1], validate=True)
            except ValueError:
                raise Stop('image_invalide') from None
            iw, ih, n = jpeg_infos(data)
            img = DecodedStreamObject()
            img._data = bytes(data)
            img.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'), NameObject('/Width'): NumberObject(iw),
                        NameObject('/Height'): NumberObject(ih), NameObject('/ColorSpace'): NameObject('/DeviceRGB' if n == 3 else '/DeviceGray'),
                        NameObject('/BitsPerComponent'): NumberObject(8), NameObject('/Filter'): NameObject('/DCTDecode')})
            echelle = min(w / iw, h / ih)
            dw, dh = iw * echelle, ih * echelle
            ap = b'q %.3f 0 0 %.3f %.2f %.2f cm /Im0 Do Q' % (dw, dh, (w - dw) / 2, (h - dh) / 2)
            res = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Im0'): writer._add_object(img)})})
            d = DictionaryObject({**base, NameObject('/Subtype'): NameObject('/Stamp'), NameObject('/Rect'): fl(r),
                                  NameObject('/Contents'): TextStringObject(str(a.get('texte') or 'Image')[:200])})
            d[NameObject('/AP')] = DictionaryObject({NameObject('/N'): _forme(writer, w, h, ap, res)})
            return d
        raise Stop('annotations_invalides')
    d[NameObject('/AP')] = DictionaryObject({NameObject('/N'): _forme(writer, r[2] - r[0], r[3] - r[1], ap)})
    return d


def annoter(raw, annotations, auteur='cabinet', jour='2026-01-01', echeance=None):
    """Copie annotée : (octets, nombre d'annotations, signature retirée)."""
    from ._vendor.pypdf.generic import ArrayObject, NameObject
    if not isinstance(annotations, list) or not annotations:
        raise Stop('aucune_annotation')
    if len(annotations) > MAX_ANNOTATIONS:
        raise Stop('annotations_invalides')
    writer, signe = _cloner(raw, echeance)
    for rang, a in enumerate(annotations):
        if not isinstance(a, dict):
            raise Stop('annotations_invalides')
        try:
            numero = int(a.get('page'))
        except (TypeError, ValueError):
            raise Stop('annotations_invalides') from None
        if not 1 <= numero <= len(writer.pages):
            raise Stop('annotations_invalides')
        page = writer.pages[numero - 1]
        d = _annotation(writer, page, a, auteur, jour, rang)
        if '/Annots' not in page:
            page[NameObject('/Annots')] = ArrayObject()
        F._obj(page['/Annots']).append(writer._add_object(d))
        F._echeance(echeance)
    return _ecrire(writer), len(annotations), signe


# ------------------------------------------------------------------------------------------------ pages : fusionner, découper, organiser
def fusionner(raws, echeance=None):
    from ._vendor.pypdf import PdfReader, PdfWriter
    if len(raws) < 2:
        raise Stop('deux_pdf_au_moins')
    writer, total, signe = PdfWriter(), 0, False
    for raw in raws:
        reader = _lecteur(raw, echeance)
        total += len(reader.pages)
        if total > MAX_PAGES_FUSION:
            raise Stop('pdf_trop_complexe')
        signe = signe or _signe(reader)
        writer.append(PdfReader(BytesIO(bytes(raw)), strict=False), import_outline=False)
        F._echeance(echeance)
    if signe:
        _sans_signature(writer)
    return _ecrire(writer), total, signe


def plages(texte, nombre):
    """« 1-3, 5, 8- » → [(1, 3), (5, 5), (8, nombre)] ; Stop si une plage sort du document ou si rien n'est demandé."""
    out = []
    for morceau in re.split(r'[,;]', str(texte or '')):
        morceau = morceau.strip().replace(' ', '')
        if not morceau:
            continue
        m = re.fullmatch(r'(\d{1,4})(?:-(\d{1,4})?)?', morceau)
        if not m:
            raise Stop('plages_invalides')
        debut = int(m.group(1))
        fin = debut if '-' not in morceau else (int(m.group(2)) if m.group(2) else nombre)
        if not 1 <= debut <= fin <= nombre:
            raise Stop('plages_invalides')
        out.append((debut, fin))
    if not out or len(out) > MAX_SORTIES:
        raise Stop('plages_invalides')
    return out


def decouper(raw, texte, echeance=None):
    from ._vendor.pypdf import PdfReader, PdfWriter
    reader = _lecteur(raw, echeance)
    signe = _signe(reader)
    out = []
    for debut, fin in plages(texte, len(reader.pages)):
        writer = PdfWriter()
        writer.append(PdfReader(BytesIO(bytes(raw)), strict=False), pages=list(range(debut - 1, fin)), import_outline=False)
        if signe:
            _sans_signature(writer)
        out.append(((debut, fin), _ecrire(writer)))
        F._echeance(echeance)
    return out, signe


def organiser(raw, ordre, echeance=None):
    """``ordre`` : [{'page': n, 'rotation': 0|90|180|270}] dans l'ordre voulu ; une page absente est supprimée de la copie."""
    from ._vendor.pypdf import PdfReader, PdfWriter
    reader = _lecteur(raw, echeance)
    n = len(reader.pages)
    if not isinstance(ordre, list) or not 1 <= len(ordre) <= 2 * n:
        raise Stop('ordre_invalide')
    pages, tours = [], []
    for x in ordre:
        try:
            p, r = int(x.get('page')), int(x.get('rotation') or 0)
        except (AttributeError, TypeError, ValueError):
            raise Stop('ordre_invalide') from None
        if not 1 <= p <= n or r not in (0, 90, 180, 270):
            raise Stop('ordre_invalide')
        pages.append(p - 1)
        tours.append(r)
    writer = PdfWriter()
    lecteur = PdfReader(BytesIO(bytes(raw)), strict=False)
    for p in pages:                                  # une page à la fois : l'ordre choisi est respecté, même répétée
        writer.append(lecteur, pages=[p], import_outline=False)
    for page, r in zip(writer.pages, tours):
        if r:
            page.rotate(r)
    if _signe(reader):
        _sans_signature(writer)
    supprimees = n - len(set(pages))
    return _ecrire(writer), supprimees, sum(1 for r in tours if r)


# ------------------------------------------------------------------------------------------------ numéroter
def _texte_page(writer, page, ops):
    """Ajoute ``ops`` (opérateurs déjà formés) par-dessus le contenu de la page, dans un état graphique propre."""
    from ._vendor.pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject
    ressources = F._obj(page.get('/Resources'))
    if ressources is None:
        ressources = DictionaryObject()
        page[NameObject('/Resources')] = ressources
    polices = F._obj(ressources.get('/Font'))
    if polices is None:
        polices = DictionaryObject()
        ressources[NameObject('/Font')] = polices
    if '/AxHe' not in polices:
        polices[NameObject('/AxHe')] = writer._add_object(_police_dict())
        polices[NameObject('/AxHeBo')] = writer._add_object(_police_dict(True))
    avant, apres = DecodedStreamObject(), DecodedStreamObject()
    avant.set_data(b'q\n')
    apres.set_data(b'Q\n' + ops + b'\n')
    contenus = F._obj(page.get('/Contents'))
    existants = list(page['/Contents']) if isinstance(contenus, list) else ([page.raw_get('/Contents')] if contenus is not None else [])
    page[NameObject('/Contents')] = ArrayObject([writer._add_object(avant)] + existants + [writer._add_object(apres)])


def _ecrit(page, u, v, texte, taille, gras=False, aligne='gauche', couleur=(0, 0, 0)):
    """Texte droit à l'affichage, ancré aux fractions (u, v) de la page affichée (rotation de la page compensée)."""
    x, y = point(page, u, v)
    a = math.radians(_angle(page))
    c, s = math.cos(a), math.sin(a)
    largeur = len(texte) * taille * (0.6 if gras else 0.52)
    decale = {'gauche': 0, 'centre': largeur / 2, 'droite': largeur}[aligne]
    x, y = x - decale * c, y - decale * s
    return b'BT /%s %.2f Tf %.3f %.3f %.3f rg %.4f %.4f %.4f %.4f %.2f %.2f Tm %s Tj ET' % (
        b'AxHeBo' if gras else b'AxHe', taille, couleur[0], couleur[1], couleur[2], c, s, -s, c, x, y, _pdf_texte(texte))


def _cadre_ecrit(page, u0, v0, u1, v1, couleur):
    x0, y0, x1, y1 = rectangle(page, [u0, v0, u1, v1])
    return b'q 1 1 1 rg %.3f %.3f %.3f RG 1.5 w %.2f %.2f %.2f %.2f re B Q' % (couleur[0], couleur[1], couleur[2], x0, y0, x1 - x0, y1 - y0)


def numeroter(raw, pages=True, position='bas-droite', piece='', piece_partout=False, echeance=None):
    if position not in POSITIONS:
        position = 'bas-droite'
    piece = str(piece or '').strip()
    if piece and not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z .\-/]{0,19}', piece):
        raise Stop('numero_piece_invalide')
    if not pages and not piece:
        raise Stop('numerotation_vide')
    writer, signe = _cloner(raw, echeance)
    total = len(writer.pages)
    rouge = COULEURS['rouge']
    for i, page in enumerate(writer.pages, 1):
        ops = []
        if pages:
            texte = '%d / %d' % (i, total)
            u, v, al = {'bas-droite': (0.95, 0.965, 'droite'), 'bas-centre': (0.5, 0.965, 'centre'), 'haut-droite': (0.95, 0.04, 'droite')}[position]
            if piece and position == 'haut-droite':
                v = 0.10
            ops.append(_ecrit(page, u, v, texte, 9))
        if piece and (i == 1 or piece_partout):
            libelle = 'Pièce n° %s' % piece + (' — page %d / %d' % (i, total) if piece_partout and total > 1 else '')
            ops.append(_cadre_ecrit(page, 0.60, 0.025, 0.97, 0.075, rouge))
            ops.append(_ecrit(page, 0.785, 0.062, libelle, 10, gras=True, aligne='centre', couleur=rouge))
        if ops:
            _texte_page(writer, page, b'\n'.join(ops))
        F._echeance(echeance)
    return _ecrire(writer), total, signe


def numero_piece_propose(desk, dossier_id, chemin, sha):
    """Numéro de pièce du fichier d'après le bordereau du dossier (pièces du contentieux, puis classement « pièce n° »), sinon ''."""
    try:
        existe = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('contentieux_exhibits5689','d51_documents')")}
        if 'contentieux_exhibits5689' in existe:
            row = desk.db.execute("SELECT numero FROM contentieux_exhibits5689 WHERE matter=? AND statut='actif' AND (path=? OR (sha256<>'' AND sha256=?)) "
                                  "ORDER BY updated DESC LIMIT 1", (dossier_id, chemin, sha)).fetchone()
            if row and str(row[0]).strip():
                return str(row[0]).strip()[:20], 'bordereau des pièces du dossier'
        if 'd51_documents' in existe:
            row = desk.db.execute("SELECT piece_number FROM d51_documents WHERE matter=? AND path=?", (dossier_id, chemin)).fetchone()
            if row and str(row[0] or '').strip():
                return str(row[0]).strip()[:20], 'classement du document (pièce n°)'
    except Exception:   # noqa: BLE001 — sans proposition, le champ reste à remplir
        log.info('outils_pdf56108 : numéro de pièce illisible', exc_info=True)
    return '', ''


# ------------------------------------------------------------------------------------------------ outils locaux (Poppler, Tesseract)
def outils_locaux():
    """{'pdftoppm': chemin|None, 'pdftotext': …, 'tesseract': …} — mêmes outils que la lecture des scans."""
    from .outils_ocr56106 import chemin
    return {n: chemin(n) for n in ('pdftoppm', 'pdftotext', 'tesseract')}


def consigne_installation(manquants):
    from . import portable
    if portable.WINDOWS:
        return ('Outils de lecture des scans absents de cette installation (%s) : réinstallez l’application Windows d’AxiorHub, qui les fournit '
                'dans son dossier « ocr ». Aucun caviardage n’a été tenté.' % ', '.join(manquants))
    paquets = sorted({'poppler-utils' if n.startswith('pdf') else 'tesseract-ocr tesseract-ocr-fra' for n in manquants})
    return ('Outils absents du serveur (%s) : installez-les avec « sudo apt install %s », puis recommencez. Aucun caviardage n’a été tenté.' % (
        ', '.join(manquants), ' '.join(paquets)))


def _commande(args, timeout=120):
    from .documents import command
    return command([str(x) for x in args], timeout=timeout)


def mots_page(pdf_path, numero, outils):
    """Mots de la page affichée avec leur boîte, en fractions : [(texte, u0, v0, u1, v1)] (``pdftotext -bbox-layout``)."""
    sortie = Path(pdf_path).with_name('mots-%d.html' % numero)
    _commande([outils['pdftotext'], '-bbox-layout', '-f', numero, '-l', numero, pdf_path, sortie])
    html = sortie.read_text(encoding='utf-8', errors='replace')
    taille = re.search(r'<page width="([\d.]+)" height="([\d.]+)"', html)
    if not taille:
        return []
    w, h = float(taille.group(1)) or 1.0, float(taille.group(2)) or 1.0
    out = []
    for m in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', html):
        from html import unescape
        out.append((unescape(m.group(5)), float(m.group(1)) / w, float(m.group(2)) / h, float(m.group(3)) / w, float(m.group(4)) / h))
    return out[:200000]


def _plie(texte):
    return re.sub(r'[^0-9a-z]+', '', fold(str(texte)))


def zones_des_termes(mots, termes):
    """Boîtes des mots qui forment chaque terme recherché (insensible aux accents, à la casse et à la ponctuation)."""
    zones = []
    jetons = [(_plie(m[0]), m) for m in mots]
    for terme in termes:
        cibles = [_plie(x) for x in str(terme).split() if _plie(x)]
        if not cibles:
            continue
        for i in range(len(jetons)):
            if len(cibles) == 1:
                if cibles[0] in jetons[i][0]:
                    zones.append(list(jetons[i][1][1:]))
                continue
            seq = jetons[i:i + len(cibles)]
            if len(seq) == len(cibles) and all((c == j[0]) or (k in (0, len(cibles) - 1) and c in j[0]) for k, (c, j) in enumerate(zip(cibles, seq))):
                zones.extend(list(j[1][1:]) for j in seq)
    return zones


def _recouvre(mot, zone, seuil=0.3):
    u0, v0, u1, v1 = mot[1:]
    a0, b0, a1, b1 = zone
    iw, ih = max(0.0, min(u1, a1) - max(u0, a0)), max(0.0, min(v1, b1) - max(v0, b0))
    aire = max(1e-9, (u1 - u0) * (v1 - v0))
    return iw * ih / aire >= seuil


def noircir_ppm(data, zones, marge=2):
    """Noircit les zones (fractions) dans une image PPM binaire (P6, 8 bits) ; rend la nouvelle image."""
    m = re.match(rb'P6\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+(\d+)\s', data)
    if not m or int(m.group(3)) != 255:
        raise Stop('pdf_illisible')
    w, h, debut = int(m.group(1)), int(m.group(2)), m.end()
    img = bytearray(data)
    if len(img) < debut + w * h * 3:
        raise Stop('pdf_illisible')
    for u0, v0, u1, v1 in zones:
        x0, x1 = max(0, int(math.floor(min(u0, u1) * w)) - marge), min(w, int(math.ceil(max(u0, u1) * w)) + marge)
        y0, y1 = max(0, int(math.floor(min(v0, v1) * h)) - marge), min(h, int(math.ceil(max(v0, v1) * h)) + marge)
        if x1 <= x0 or y1 <= y0:
            continue
        noir = bytes((x1 - x0) * 3)
        for y in range(y0, y1):
            o = debut + (y * w + x0) * 3
            img[o:o + (x1 - x0) * 3] = noir
    return bytes(img)


def _texte_pypdf(raw):
    from ._vendor.pypdf import PdfReader
    reader = PdfReader(BytesIO(raw), strict=False)
    return [(p.extract_text() or '') for p in reader.pages], reader


def controle_caviardage(copie, termes, jetons_par_page, outils=None, chemin_tmp=None):
    """Contrôle obligatoire de la copie : None si rien ne subsiste, sinon le motif (jamais le texte caviardé lui-même)."""
    from ._vendor.pypdf.generic import StreamObject
    textes, reader = _texte_pypdf(copie)
    plies = [_plie(t) for t in textes]
    termes_plies = [_plie(t) for t in termes if _plie(t)]
    if any(t in p for t in termes_plies for p in plies):
        return 'texte de la copie (pypdf)'
    for numero, jetons in jetons_par_page.items():
        mots = set(re.findall(r'[0-9a-z]+', fold(textes[numero - 1] if numero - 1 < len(textes) else '')))
        if jetons & mots:
            return 'texte d’une page caviardée (pypdf)'
    if outils and chemin_tmp:
        Path(chemin_tmp).write_bytes(copie)
        tout = _commande([outils['pdftotext'], '-enc', 'UTF-8', chemin_tmp, '-'])
        if any(t in _plie(tout) for t in termes_plies):
            return 'texte de la copie (pdftotext)'
        pages_poppler = tout.split('\f')
        for numero, jetons in jetons_par_page.items():
            if numero - 1 < len(pages_poppler) and jetons & set(re.findall(r'[0-9a-z]+', fold(pages_poppler[numero - 1]))):
                return 'texte d’une page caviardée (pdftotext)'
    racine = reader.trailer['/Root'].get_object()
    for cle in ('/Outlines', '/Names', '/AcroForm', '/Metadata', '/OpenAction', '/AA', '/StructTreeRoot', '/PieceInfo'):
        if racine.get(cle) is not None:
            return 'structure du PDF (%s)' % cle
    infos = reader.trailer.get('/Info')
    if infos is not None and any(k not in ('/Producer',) for k in F._obj(infos).keys()):
        return 'métadonnées'
    for page in reader.pages:
        if page.get('/Annots') or page.get('/Metadata') is not None or page.get('/PieceInfo') is not None:
            return 'annotations ou métadonnées de page'
    vus = 0
    for num in range(1, int(reader.trailer.get('/Size', 0) or 0)):
        try:
            obj = reader.get_object(num)
        except Exception:   # noqa: BLE001 — objet libre
            continue
        if isinstance(obj, StreamObject):
            vus += 1
            if str(obj.get('/Subtype')) == '/Image':
                continue
            try:
                data = obj.get_data()
            except Exception:   # noqa: BLE001 — flux non décodable : examiné tel quel
                data = obj._data or b''
            brut = _plie(data[:4_000_000].decode('latin-1', 'replace')) + _plie(data[:4_000_000].decode('utf-16-be', 'replace'))
            if any(t in brut for t in termes_plies if len(t) >= 3):
                return 'flux du PDF'
        elif hasattr(obj, 'get') and any(t in _plie(str(v)) for v in obj.values() for t in termes_plies if len(t) >= 3 and not hasattr(v, 'get_object')):
            return 'dictionnaire du PDF'
    return None


def caviarder(raw, zones, termes, outils, langues='fra+eng', echeance=None):
    """Copie caviardée (octets, pages caviardées). ``zones`` : {page: [[u0, v0, u1, v1], …]} ; ``termes`` : textes à retrouver et caviarder."""
    from ._vendor.pypdf import PdfReader, PdfWriter
    from ._vendor.pypdf.generic import NameObject, TextStringObject
    manquants = [n for n, p in outils.items() if not p]
    if manquants:
        raise Stop('outils_caviardage_absents')
    reader = _lecteur(raw, echeance)
    n = len(reader.pages)
    zones = {int(k): [list(map(float, z))[:4] for z in v if isinstance(z, (list, tuple)) and len(z) == 4] for k, v in (zones or {}).items()
             if str(k).isdigit() and 1 <= int(k) <= n}
    termes = [str(t).strip()[:200] for t in (termes or []) if str(t).strip()][:50]
    if not any(zones.values()) and not termes:
        raise Stop('rien_a_caviarder')
    with tempfile.TemporaryDirectory(prefix='axh-caviardage-') as tmp:
        source = Path(tmp) / 'source.pdf'
        source.write_bytes(raw)
        mots = {}
        if termes:
            for numero in range(1, n + 1):
                mots[numero] = mots_page(source, numero, outils)
                trouve = zones_des_termes(mots[numero], termes)
                if trouve:
                    zones.setdefault(numero, []).extend(trouve)
                F._echeance(echeance)
            if not any(zones_des_termes(m, termes) for m in mots.values()):
                raise Stop('texte_a_caviarder_introuvable')
        pages = sorted(k for k, v in zones.items() if v)
        if len(pages) > MAX_PAGES_CAVIARDEES:
            raise Stop('trop_de_pages_a_caviarder')
        jetons_par_page, refaites = {}, {}
        for numero in pages:
            liste = mots.get(numero) or mots_page(source, numero, outils)
            dedans = {_plie(m[0]) for m in liste if any(_recouvre(m, z) for z in zones[numero])}
            dehors = {_plie(m[0]) for m in liste if not any(_recouvre(m, z, 0.01) for z in zones[numero])}
            jetons_par_page[numero] = {j for j in dedans - dehors if len(j) >= 3}
            base = Path(tmp) / ('page-%d' % numero)
            _commande([outils['pdftoppm'], '-r', RESOLUTION, '-f', numero, '-l', numero, '-singlefile', source, base], timeout=180)
            image = base.with_suffix('.ppm')
            image.write_bytes(noircir_ppm(image.read_bytes(), zones[numero]))
            _commande([outils['tesseract'], image, base, '--dpi', RESOLUTION, '-l', langues, 'pdf'], timeout=240)
            refaites[numero] = (base.with_suffix('.pdf')).read_bytes()
            image.unlink()
            F._echeance(echeance)
        writer = PdfWriter()
        for numero, page in enumerate(reader.pages, 1):
            if numero in refaites:
                nouvelle = PdfReader(BytesIO(refaites[numero]), strict=False).pages[0]
                x0, y0, x1, y1, rot = _cadre(page)
                w, h = (y1 - y0, x1 - x0) if rot in (90, 270) else (x1 - x0, y1 - y0)
                nouvelle.scale_to(w, h)
                writer.add_page(nouvelle)
            else:
                writer.add_page(page)
            ajoutee = writer.pages[-1]
            for cle in ('/Annots', '/Metadata', '/PieceInfo', '/AA', '/Thumb', '/StructParents', '/B'):
                if cle in ajoutee:
                    del ajoutee[cle]
        for cle in ('/Outlines', '/Names', '/AcroForm', '/Metadata', '/OpenAction', '/AA', '/StructTreeRoot', '/PieceInfo', '/MarkInfo', '/PageLabels'):
            if cle in writer._root_object:
                del writer._root_object[cle]
        infos = writer._info.get_object() if getattr(writer, '_info', None) is not None else None
        if infos is not None:
            for cle in [k for k in infos.keys() if k != '/Producer']:
                del infos[cle]
            infos[NameObject('/Producer')] = TextStringObject('AxiorHub (copie caviardée)')
        copie = _ecrire(writer)
        motif = controle_caviardage(copie, termes, jetons_par_page, outils, Path(tmp) / 'controle.pdf')
        if motif:
            log.warning('outils_pdf56108 : contrôle du caviardage échoué (%s) ; copie effacée', motif)
            raise Stop('caviardage_controle_echoue')
        return copie, pages


# ------------------------------------------------------------------------------------------------ compresser
def _image_simple(img):
    cs = F._obj(img.get('/ColorSpace'))
    if isinstance(cs, list) and cs and str(cs[0]) == '/ICCBased':
        n = int(F._obj(cs[1]).get('/N', 0) or 0)
        cs = '/DeviceRGB' if n == 3 else '/DeviceGray' if n == 1 else ''
    filtre = F._obj(img.get('/Filter'))
    filtre = [str(x) for x in filtre] if isinstance(filtre, list) else ([str(filtre)] if filtre else [])
    return (str(cs) in ('/DeviceRGB', '/DeviceGray') and int(img.get('/BitsPerComponent', 0) or 0) == 8 and not img.get('/ImageMask')
            and img.get('/Decode') is None and filtre in ([], ['/FlateDecode'], ['/DCTDecode'])), str(cs)


def _recomprimer_image(img, cs, qualite, cote_max, outils, tmp, rang):
    """Nouvelle image JPEG (octets, largeur, hauteur) dessinée par Poppler, ou None si elle ne serait pas plus petite."""
    from ._vendor.pypdf import PdfWriter
    from ._vendor.pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w, h = int(img['/Width']), int(img['/Height'])
    if w * h < 40_000:
        return None
    echelle = min(1.0, cote_max / float(max(w, h)))
    writer = PdfWriter()
    page = writer.add_blank_page(width=w, height=h)
    copie = img.clone(writer) if hasattr(img, 'clone') else img
    if '/SMask' in copie:
        del copie['/SMask']
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/I0'): writer._add_object(copie)})})
    contenu = DecodedStreamObject()
    contenu.set_data(b'q %d 0 0 %d 0 0 cm /I0 Do Q' % (w, h))
    page[NameObject('/Contents')] = writer._add_object(contenu)
    source = Path(tmp) / ('image-%d.pdf' % rang)
    source.write_bytes(_ecrire(writer))
    sortie = Path(tmp) / ('image-%d' % rang)
    args = [outils['pdftoppm'], '-r', '%.3f' % (72 * echelle), '-jpeg', '-jpegopt', 'quality=%d' % qualite, '-singlefile']
    if cs == '/DeviceGray':
        args.append('-gray')
    _commande(args + [source, sortie])
    data = sortie.with_suffix('.jpg').read_bytes()
    nw, nh, n = jpeg_infos(data)
    return data, nw, nh, n


def compresser(raw, qualite='moyenne', outils=None, echeance=None):
    """(octets, détail) : images recompressées (Poppler) et dédoublonnées, contenus recompressés. Stop si aucun gain."""
    from ._vendor.pypdf.generic import NameObject, NumberObject
    q, cote_max = QUALITES.get(qualite, QUALITES['moyenne'])
    writer, signe = _cloner(raw, echeance)
    detail = {'images_recompressees': 0, 'images_dedoublonnees': 0, 'poppler': bool(outils and outils.get('pdftoppm')), 'signature_retiree': signe}
    vus, empreintes, rang = {}, {}, 0
    with tempfile.TemporaryDirectory(prefix='axh-compression-') as tmp:
        for page in writer.pages:
            F._echeance(echeance)
            try:
                page.compress_content_streams(level=9)
            except Exception:   # noqa: BLE001 — contenu non recompressable : laissé tel quel
                pass
            res = F._obj(page.get('/Resources'))
            xo = F._obj(res.get('/XObject')) if res is not None else None
            if not xo:
                continue
            for nom in list(xo.keys()):
                ref = xo.raw_get(nom)
                img = F._obj(ref)
                if not hasattr(img, 'get') or img.get('/Subtype') != '/Image':
                    continue
                cle_obj = getattr(ref, 'idnum', id(img))
                if cle_obj in vus:
                    continue
                vus[cle_obj] = True
                empreinte = hashlib.sha256((img._data or b'') + repr(sorted((str(k), str(v)) for k, v in img.items() if k != '/Length')).encode()).hexdigest()
                if empreinte in empreintes and hasattr(ref, 'idnum'):
                    xo[NameObject(nom)] = empreintes[empreinte]
                    detail['images_dedoublonnees'] += 1
                    continue
                empreintes[empreinte] = ref
                if not img.get('/Filter') and img._data and len(img._data) > 1024:   # image non compressée : compression sans perte d'abord
                    img._data = zlib.compress(img._data, 9)
                    img[NameObject('/Filter')] = NameObject('/FlateDecode')
                    detail['images_sans_perte'] = detail.get('images_sans_perte', 0) + 1
                rang += 1
                if rang > MAX_IMAGES or not detail['poppler']:
                    continue
                simple, cs = _image_simple(img)
                if not simple:
                    continue
                try:
                    nouvelle = _recomprimer_image(img, cs, q, cote_max, outils, tmp, rang)
                except Stop:
                    nouvelle = None
                if not nouvelle or len(nouvelle[0]) >= len(img._data or b'') * 0.9:
                    continue
                data, nw, nh, n = nouvelle
                img._data = data
                img[NameObject('/Filter')] = NameObject('/DCTDecode')
                img[NameObject('/Width')], img[NameObject('/Height')] = NumberObject(nw), NumberObject(nh)
                img[NameObject('/ColorSpace')] = NameObject('/DeviceRGB' if n == 3 else '/DeviceGray')
                for cle in ('/DecodeParms', '/Length'):
                    if cle in img:
                        del img[cle]
                detail['images_recompressees'] += 1
                F._echeance(echeance)
    sortie = _ecrire(writer)
    from ._vendor.pypdf import PdfReader, PdfWriter
    nette = PdfWriter(clone_from=PdfReader(BytesIO(sortie), strict=False))     # seconde passe : les objets devenus orphelins (doublons) ne sont pas recopiés
    sortie = min(sortie, _ecrire(nette), key=len)
    if len(sortie) >= len(raw):
        raise Stop('compression_sans_gain')
    detail.update(avant=len(raw), apres=len(sortie), gain_pourcent=round(100.0 * (len(raw) - len(sortie)) / len(raw), 1))
    return sortie, detail


# ------------------------------------------------------------------------------------------------ commandes (moteur commun)
def _jour(desk, a):
    from .acces5683 import today_of
    return today_of(desk, a.compte)[0]


def _stem(chemin):
    return PurePosixPath(chemin).stem[:110].rstrip(' .') or 'Document'


def _sources(data):
    brut = data.get('sources')
    if not isinstance(brut, list) or not brut:
        raise Stop('introuvable')
    if len(brut) > MAX_SOURCES:
        raise Stop('trop_de_sources')
    out = []
    for x in brut:
        if not isinstance(x, dict):
            raise Stop('introuvable')
        out.append((str(x.get('p') or '')[:1000], str(x.get('empreinte') or '')[:64]))
    return out


def executer(desk, acteur, operation, data, langues=None):
    """Une opération de la boîte à outils : ``data`` = d, sources [{p, empreinte}], paramètres de l'opération, cle."""
    if operation not in OPERATIONS:
        raise Stop('operation_inconnue')
    a = acces5697.acteur_de(acteur)
    sources = _sources(data)
    jour = _jour(desk, a)
    date = jour.strftime('%d-%m-%Y')
    stem = _stem(sources[0][0])
    note_sig = ' La copie ne porte plus de signature électronique (l’original signé reste intact).'

    if operation == 'annoter':
        def fabrique(raws):
            sortie, n, signe = F._borne(lambda raw, echeance=None: annoter(raw, data.get('annotations'), a.compte, jour.isoformat(), echeance), raws[0])
            return [{'base': '%s (annoté le %s)' % (stem, date), 'octets': sortie, 'info': {'annotations': n, 'signature_retiree': signe}}]
        msg = lambda r: 'Copie annotée enregistrée : %s (%d annotation(s)). L’original est inchangé.%s' % (
            r['fichiers'][0]['nom'], r['fichiers'][0]['annotations'], note_sig if r['fichiers'][0]['signature_retiree'] else '')
        libelle = 'PDF annoté'
    elif operation == 'fusionner':
        if len(sources) < 2:
            raise Stop('deux_pdf_au_moins')

        def fabrique(raws):
            sortie, total, signe = F._borne(lambda *x, echeance=None: fusionner(list(x), echeance), *raws)
            return [{'base': '%s (fusion de %d PDF, %s)' % (stem, len(raws), date), 'octets': sortie, 'info': {'pages': total, 'signature_retiree': signe}}]
        msg = lambda r: 'PDF fusionné enregistré : %s (%d pages). Les originaux sont inchangés.%s' % (
            r['fichiers'][0]['nom'], r['fichiers'][0]['pages'], note_sig if r['fichiers'][0]['signature_retiree'] else '')
        libelle = 'PDF fusionné'
    elif operation == 'decouper':
        def fabrique(raws):
            parts, signe = F._borne(lambda raw, echeance=None: decouper(raw, data.get('plages'), echeance), raws[0])
            return [{'base': '%s (pages %d-%d)' % (stem, d0, d1) if d0 != d1 else '%s (page %d)' % (stem, d0), 'octets': o,
                     'info': {'pages': d1 - d0 + 1, 'signature_retiree': signe}} for (d0, d1), o in parts]
        msg = lambda r: '%d fichier(s) enregistré(s) : %s. L’original est inchangé.' % (len(r['fichiers']), ', '.join(f['nom'] for f in r['fichiers']))
        libelle = 'Extrait de PDF'
    elif operation == 'organiser':
        def fabrique(raws):
            sortie, supprimees, tournees = F._borne(lambda raw, echeance=None: organiser(raw, data.get('ordre'), echeance), raws[0])
            return [{'base': '%s (pages réorganisées le %s)' % (stem, date), 'octets': sortie, 'info': {'supprimees': supprimees, 'pivotees': tournees}}]
        msg = lambda r: 'Copie enregistrée : %s (%d page(s) supprimée(s), %d pivotée(s)). L’original est inchangé.' % (
            r['fichiers'][0]['nom'], r['fichiers'][0]['supprimees'], r['fichiers'][0]['pivotees'])
        libelle = 'PDF réorganisé'
    elif operation == 'numeroter':
        def fabrique(raws):
            sortie, total, signe = F._borne(lambda raw, echeance=None: numeroter(
                raw, bool(data.get('pages')), str(data.get('position') or ''), str(data.get('piece') or ''), bool(data.get('piece_partout')), echeance), raws[0])
            piece = str(data.get('piece') or '').strip()
            return [{'base': '%s (%s)' % (stem, ('pièce n° %s' % piece.replace('/', '-')) if piece else 'numéroté'), 'octets': sortie,
                     'info': {'pages': total, 'piece': piece, 'signature_retiree': signe}}]
        msg = lambda r: 'Copie numérotée enregistrée : %s. L’original est inchangé.' % r['fichiers'][0]['nom']
        libelle = 'PDF numéroté'
    elif operation == 'caviarder':
        outils = outils_locaux()
        manquants = [n for n, p in outils.items() if not p]
        if manquants:
            raise _absents(manquants)
        from .outils_ocr56106 import langues as _langues
        zones = data.get('zones') if isinstance(data.get('zones'), dict) else {}
        termes = data.get('termes') if isinstance(data.get('termes'), list) else []

        def fabrique(raws):
            sortie, pages = F._borne(lambda raw, echeance=None: caviarder(raw, zones, termes, outils, langues or _langues(), echeance), raws[0],
                                     delai=DELAI_CAVIARDAGE)
            return [{'base': '%s (caviardé le %s)' % (stem, date), 'octets': sortie, 'info': {'pages_caviardees': pages}}]
        msg = lambda r: ('Copie caviardée enregistrée : %s (page(s) %s rastérisée(s), noircie(s), rendue(s) cherchable(s)). Contrôle réussi : aucun texte '
                         'caviardé ne subsiste. Métadonnées, signets, annotations, pièces jointes et formulaire ne sont pas repris. L’original est intact.' % (
                             r['fichiers'][0]['nom'], ', '.join(str(p) for p in r['fichiers'][0]['pages_caviardees'])))
        libelle = 'PDF caviardé'
    else:
        outils = outils_locaux()
        qualite = str(data.get('qualite') or 'moyenne')

        def fabrique(raws):
            sortie, detail = F._borne(lambda raw, echeance=None: compresser(raw, qualite, outils, echeance), raws[0], delai=DELAI_COMPRESSION)
            return [{'base': '%s (compressé)' % stem, 'octets': sortie, 'info': detail}]

        def msg(r):
            f = r['fichiers'][0]
            texte = 'Copie compressée enregistrée : %s. %s → %s (gain %s %%), %d image(s) recompressée(s), %d doublon(s) retiré(s). L’original est inchangé.' % (
                f['nom'], _taille(f['avant']), _taille(f['apres']), str(f['gain_pourcent']).replace('.', ','), f['images_recompressees'], f['images_dedoublonnees'])
            if not f['poppler']:
                texte += ' Images non recompressées : Poppler (pdftoppm) est absent de cette installation.'
            return texte
        libelle = 'PDF compressé'
    return F.produire(desk, a, operation, data, sources, fabrique, libelle, msg)


def _absents(manquants):
    ex = Stop('outils_caviardage_absents')
    ex.detail = {'message': consigne_installation(manquants)}
    return ex


def _taille(n):
    return ('%.1f Mo' % (n / 1_000_000)).replace('.', ',') if n >= 1_000_000 else '%d Ko' % max(1, round(n / 1000))


# ------------------------------------------------------------------------------------------------ page « Outils PDF »
def _pdfs_du_dossier(desk, a, dossier_id, base):
    """PDF du dossier proposés pour la fusion (classement du dossier, lecture seule ; les droits du dossier sont déjà vérifiés)."""
    try:
        if desk.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='d51_documents'").fetchone() is None:
            return []
        rows = desk.db.execute("SELECT path, name FROM d51_documents WHERE matter=? AND lower(name) LIKE '%.pdf' ORDER BY name LIMIT 500", (dossier_id,)).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception:   # noqa: BLE001
        return []


def page_html(desk, a, prefix, args):
    chemins = [str(args.get('p') or '')] + [str(args.get('p%d' % i) or '') for i in range(2, MAX_SOURCES + 1)]
    chemins = [c for c in dict.fromkeys(chemins) if c]
    dossier_id = str(args.get('d') or '')
    fichiers = []
    for c in chemins:
        d, chemin = F._dossier_et_fichier(desk, a, dossier_id, c)
        _, raw = F._lire(desk, chemin)
        try:
            reader = _lecteur(raw)
            info = {'pages': len(reader.pages), 'signe': _signe(reader), 'etat': 'ok', 'message': ''}
        except Stop as ex:
            code = str(ex).split(':')[0]
            info = {'pages': 0, 'signe': False, 'etat': 'refus', 'message': F.REFUS.get(code) or F.message(code)}
        fichiers.append(dict(info, chemin=chemin, nom=PurePosixPath(chemin).name, empreinte=F._sha(raw), octets=len(raw), sha=F._sha(raw)))
    if not fichiers:
        raise Stop('introuvable')
    p0 = fichiers[0]
    retour = '<p class="f108-retour"><a href="%s">← Fichiers du dossier</a></p>' % e(F._retour(prefix, dossier_id), quote=True)
    entete = '<h1>Outils PDF</h1><p class="f108-fichier"><strong>%s</strong>%s</p>' % (
        e(p0['nom']), (' <span class="ax-muted">et %d autre(s) PDF choisi(s)</span>' % (len(fichiers) - 1)) if len(fichiers) > 1 else '')
    if p0['etat'] != 'ok':
        return '<div class="f108 f108-page">%s%s<div class="f108-refus" role="status"><p>%s</p><p class="ax-muted">L’original n’a pas été modifié.</p></div></div>' % (
            retour, entete, e(p0['message']))
    outils = outils_locaux()
    manquants = [n for n, p in outils.items() if not p]
    numero, source_numero = numero_piece_propose(desk, dossier_id, p0['chemin'], p0['sha'])
    donnees = {'d': dossier_id, 'retour': F._retour(prefix, dossier_id) + '&sous=repertoire',
               'sources': [{'p': f['chemin'], 'empreinte': f['empreinte'], 'nom': f['nom'], 'pages': f['pages']} for f in fichiers if f['etat'] == 'ok'],
               'apercu': prefix + '/dossier/fichier?' + urlencode({'matter': dossier_id, 'path': p0['chemin']})}
    autres = ''.join('<option value="%s">%s</option>' % (e(c, quote=True), e(n)) for c, n in _pdfs_du_dossier(desk, a, dossier_id, p0['chemin'])
                     if c not in [f['chemin'] for f in fichiers])
    sig = ('<p class="f108-note">PDF signé électroniquement : chaque copie produite ici ne portera plus de signature valide. L’original signé reste intact.</p>'
           if p0['signe'] else '')
    statut = '<p class="f108-statut" role="status" aria-live="polite" data-f108-statut></p>'
    onglets = (('annoter', 'Annoter'), ('pages', 'Pages'), ('decouper', 'Découper'), ('fusionner', 'Fusionner'), ('numeroter', 'Numéroter'),
               ('caviarder', 'Caviarder'), ('compresser', 'Compresser'))
    tabs = ''.join('<button type="button" role="tab" class="f108-onglet" id="f108-onglet-%s" aria-controls="f108-panneau-%s" aria-selected="%s" data-f108-onglet="%s"%s>%s</button>' % (
        k, k, 'true' if i == 0 else 'false', k, '' if i == 0 else ' tabindex="-1"', e(v)) for i, (k, v) in enumerate(onglets))
    couleurs = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in (('jaune', 'Jaune'), ('vert', 'Vert'), ('bleu', 'Bleu'), ('rouge', 'Rouge'), ('noir', 'Noir')))
    tampons = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in TAMPONS.items())
    panneaux = {
        'annoter': ('<p class="ax-muted">Choisissez un outil, puis tracez sur la page à droite (cliquer-glisser). Chaque annotation devient une vraie annotation PDF, '
                    'visible dans tous les lecteurs. La copie annotée est un nouveau fichier.</p>'
                    '<fieldset class="f108-outils" data-f108-outils><legend>Outil</legend>'
                    '<label><input type="radio" name="f108-outil" value="texte" checked> Texte libre</label>'
                    '<label><input type="radio" name="f108-outil" value="surligner"> Surligner</label>'
                    '<label><input type="radio" name="f108-outil" value="dessin"> Dessin à main levée</label>'
                    '<label><input type="radio" name="f108-outil" value="image"> Image ou signature</label>'
                    '<label><input type="radio" name="f108-outil" value="tampon"> Tampon</label></fieldset>'
                    '<label>Texte de la note<textarea rows="2" maxlength="2000" data-f108-note placeholder="Texte à placer sur la page"></textarea></label>'
                    '<label>Couleur<select data-f108-couleur><option value="">Par défaut</option>%s</select></label>'
                    '<label>Tampon<select data-f108-tampon>%s<option value="piece">Pièce n°…</option></select></label>'
                    '<label>Numéro de pièce du tampon<input data-f108-piece-tampon maxlength="20" value="%s"></label>'
                    '<label>Image ou signature (JPEG ou PNG)<input type="file" accept="image/jpeg,image/png" data-f108-image></label>'
                    '<ul class="f108-traces" data-f108-traces aria-label="Annotations tracées"></ul>'
                    '<p><button type="button" class="ax-btn" data-f108-op="annoter">Enregistrer la copie annotée</button></p>') % (couleurs, tampons, e(numero, quote=True)),
        'pages': ('<p class="ax-muted">Décochez une page pour la retirer de la copie, changez sa position ou faites-la pivoter. L’original n’est pas modifié.</p>'
                  '<ol class="f108-pages" data-f108-pages>%s</ol>'
                  '<p><button type="button" class="ax-btn" data-f108-op="organiser">Enregistrer la copie réorganisée</button></p>') % ''.join(
            '<li data-f108-page="%d"><label><input type="checkbox" checked data-f108-garder> Page %d</label> <label>Position<input type="number" min="1" max="%d" '
            'value="%d" data-f108-position class="f108-num"></label> <label>Pivoter<select data-f108-rotation><option value="0">Non</option><option value="90">'
            '90° à droite</option><option value="180">180°</option><option value="270">90° à gauche</option></select></label></li>' % (i, i, p0['pages'], i)
            for i in range(1, p0['pages'] + 1)),
        'decouper': ('<label>Plages de pages (une copie par plage)<input data-f108-plages placeholder="1-3, 5, 8-" value="1-%d"></label>'
                     '<p class="ax-muted">« 8- » : de la page 8 à la fin. %d page(s) dans ce PDF.</p>'
                     '<p><button type="button" class="ax-btn" data-f108-op="decouper">Découper</button></p>') % (p0['pages'], p0['pages']),
        'fusionner': ('<p class="ax-muted">Ordre de la fusion (flèches pour déplacer). Le PDF fusionné est un nouveau fichier.</p><ol class="f108-fusion" data-f108-fusion></ol>'
                      '<label>Ajouter un PDF du dossier<select data-f108-ajout-pdf><option value="">Choisir…</option>%s</select></label>'
                      '<p><button type="button" class="ax-btn" data-f108-op="fusionner">Fusionner</button></p>') % autres,
        'numeroter': ('<label class="f108-case"><input type="checkbox" data-f108-num-pages checked> Numéroter les pages (« 1 / N »)</label>'
                      '<label>Position<select data-f108-num-position><option value="bas-droite">En bas à droite</option><option value="bas-centre">En bas au centre</option>'
                      '<option value="haut-droite">En haut à droite</option></select></label>'
                      '<label>Tampon « Pièce n° » (facultatif)<input data-f108-num-piece maxlength="20" value="%s"></label>%s'
                      '<label class="f108-case"><input type="checkbox" data-f108-num-partout> Tampon sur toutes les pages (sinon la première)</label>'
                      '<p><button type="button" class="ax-btn" data-f108-op="numeroter">Numéroter</button></p>') % (
            e(numero, quote=True), ('<p class="ax-muted">Numéro proposé d’après le %s : à vérifier.</p>' % e(source_numero)) if numero else
            '<p class="ax-muted">Aucun numéro de pièce connu pour ce fichier dans le bordereau du dossier.</p>'),
        'caviarder': (('<div class="f108-refus" role="status"><p>%s</p></div>' % e(consigne_installation(manquants))) if manquants else '') + (
            '<p class="ax-muted">Tracez les zones à noircir sur la page (outil « Zone à caviarder ») et/ou indiquez les textes à retrouver et caviarder, un par ligne. '
            'Les pages concernées sont transformées en images, noircies pour de vrai, puis rendues cherchables par reconnaissance de texte. '
            'Un contrôle vérifie ensuite qu’aucun texte caviardé ne subsiste : sinon, la copie est refusée et effacée.</p>'
            '<label class="f108-case"><input type="checkbox" data-f108-zone-active> Tracer des zones à caviarder sur la page</label>'
            '<label>Textes à caviarder (un par ligne)<textarea rows="3" maxlength="4000" data-f108-termes placeholder="Ex. un nom, une adresse, un numéro"></textarea></label>'
            '<ul class="f108-traces" data-f108-zones aria-label="Zones à caviarder"></ul>'
            '<p><button type="button" class="ax-btn" data-f108-op="caviarder"%s>Caviarder (copie « caviardé »)</button></p>') % (' disabled' if manquants else ''),
        'compresser': ('<label>Qualité des images<select data-f108-qualite><option value="haute">Haute (peu de perte)</option><option value="moyenne" selected>Moyenne</option>'
                       '<option value="forte">Forte compression</option></select></label><p class="ax-muted">Taille actuelle : <strong>%s</strong></p>'
                       '<p class="ax-muted">Le gain est mesuré ; si la copie n’est pas plus petite, rien n’est enregistré.</p>%s'
                       '<p><button type="button" class="ax-btn" data-f108-op="compresser">Compresser</button></p>') % (
            e(_taille(p0['octets'])), '' if outils.get('pdftoppm') else '<p class="f108-note">Poppler (pdftoppm) est absent : seules les étapes sans perte seront faites.</p>'),
    }
    corps = ''.join('<section role="tabpanel" id="f108-panneau-%s" aria-labelledby="f108-onglet-%s" class="f108-panneau"%s>%s</section>' % (
        k, k, '' if i == 0 else ' hidden', panneaux[k]) for i, (k, _) in enumerate(onglets))
    outils_html = ('<div class="f108-boite" data-f108-boite data-f108-donnees="%s"><div class="f108-onglets" role="tablist" aria-label="Outils PDF">%s</div>%s%s%s</div>') % (
        e(json.dumps(donnees, ensure_ascii=False), quote=True), tabs, sig, corps, statut)
    visionneuse = ('<section class="f108-apercu" aria-label="Aperçu du PDF"><div class="f108-nav"><button type="button" class="ax-btn ghost" data-f108-prec>Page précédente</button>'
                   '<span data-f108-numero role="status">Page 1</span><button type="button" class="ax-btn ghost" data-f108-suiv>Page suivante</button></div>'
                   '<div class="f108-feuille" data-f108-feuille><canvas data-f108-canvas aria-label="Page du PDF"></canvas><div class="f108-calque" data-f108-calque></div></div></section>')
    return '<div class="f108 f108-page f108-outils-page">%s%s<div class="f108-grille">%s%s</div></div>' % (retour, entete, outils_html, visionneuse)


def page_route(env, cfg, auth, prefix, path, args, shell):
    """``GET /dossier/outils-pdf`` : lecture seule ; dossier réservé ou absent, fichier hors dossier : même 404 neutre."""
    from .desk import Desk
    from .web440 import page_out
    from .web567 import actor
    owner, role = actor(env)
    desk = Desk(cfg)
    head = '<script defer src="%s/static/v56108-fichiers.js"></script>' % e(prefix, quote=True)
    try:
        try:
            if role not in F.ROLES:
                raise Stop('introuvable')
            corps = page_html(desk, acces5697.Acteur(owner, role), prefix, args)
            out = page_out(shell('Outils PDF', corps, prefix, auth['csrf'], '/dossiers', head))
        except Stop as ex:
            code = str(ex).split(':')[0]
            out = page_out(shell('Outils PDF', '<div class="f108 f108-page"><p class="notice">%s</p><p><a class="ax-btn ghost" href="%s">Retour aux dossiers</a></p></div>' % (
                e(F.message(code)), e(prefix + '/dossiers', quote=True)), prefix, auth['csrf'], '/dossiers'))
            out['status'] = F.STATUTS.get(code, '400 Bad Request')
        out.setdefault('headers', []).append(('Cache-Control', 'private, no-store'))
        return out
    finally:
        try:
            desk.db.close()
        except Exception:   # noqa: BLE001
            pass


def lien(prefix, dossier_id, chemins):
    q = {'d': dossier_id}
    for i, c in enumerate(chemins[:MAX_SOURCES]):
        q['p' if i == 0 else 'p%d' % (i + 1)] = c
    return prefix + PAGE + '?' + urlencode(q)

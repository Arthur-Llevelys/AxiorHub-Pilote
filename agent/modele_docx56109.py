"""5.6.109 (lot V, F08) : modèles Word du cabinet — importer un DOCX comme modèle réutilisable, le remplir, contrôler le DOCX produit.

Repris de ``reference/modele_docx109.py`` du dossier technique, SANS lxml (aucune dépendance nouvelle : bibliothèque standard seulement).
Le remplissage ne resérialise pas le document : seules les zones ``w:sdt`` balisées ``axh:<nom>`` sont remplacées, par épissure du texte
XML d'origine (préfixes, déclarations, ``mc:Ignorable``, identifiants et tout le reste du corps restent octet pour octet) ; toutes les autres
parties du paquet (styles, numérotation, thème, en-têtes, pieds, images, propriétés) sont recopiées à l'identique.

- **Zones simples seulement** : un contrôle de contenu contenant un paragraphe à un seul run (ou un run seul) ; chaque ligne de la valeur
  devient une copie du paragraphe prototype (propriétés de paragraphe et de caractères conservées, identifiants ``w14:paraId`` retirés des
  copies). Zone complexe (tableau, champ, image, plusieurs runs, section) : refusée, jamais aplatie — l'éditeur existant reste proposé.
- **Paquet refusé** : macros, OLE, ActiveX, signatures, relations externes, DOCTYPE/ENTITY (UTF-16 compris), chemins sortants, archive
  trop grosse ou trop d'entrées, compression inconnue, chiffrement.
- **Contrôle avant « prêt »** (``controler``) : le DOCX produit est ROUVERT (archive intègre, mêmes parties, parties protégées identiques,
  texte hors zones identique à l'octet, chaque zone remplie exactement de la valeur demandée avec le style du prototype). La production
  (``produire``) contrôle avant d'écrire, écrit par création exclusive dans le dossier, relit le fichier écrit et le CONTRÔLE DE NOUVEAU :
  « prêt » n'est annoncé qu'après ce second contrôle réussi.
- Registre : ``modeles_actes56109`` (brouillon → validé par l'avocat → remplacé), original conservé dans un dossier privé de l'application ;
  productions dans ``modeles_productions56109`` (clé d'idempotence, intention d'écriture durable avant l'effet, état « à vérifier » si la
  réponse se perd, même cible relue ensuite). Le document produit est une version du registre commun ``livrables5689`` (même lecteur).

Limites dites, non masquées : pas de suivi des modifications natif, pas de groupes répétitifs ni de tableaux remplis, pagination non
garantie identique (le texte change), polices du poste à vérifier ; la trame fournie (``data/trame_conclusions56109.docx``) est un exemple
technique de zones et de styles, PAS un acte conforme.
"""
from datetime import datetime, timezone
import hashlib
from html import escape as e
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import xml.etree.ElementTree as ET
import zipfile

from .common import Stop

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
WT = '{%s}' % W
TAG = re.compile(r'axh:([a-z][a-z0-9_]{0,63})\Z')
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MAX_DOCX = 20_000_000
MAX_TOTAL = 64_000_000
MAX_ENTREES = 1000
TYPES_ACTE = {'conclusions': 'Conclusions', 'assignation': 'Assignation', 'requete': 'Requête', 'consultation': 'Consultation', 'courrier': 'Courrier',
              'autre': 'Autre document'}
NATURE_REGISTRE = {'conclusions': 'conclusions', 'assignation': 'assignation', 'requete': 'assignation', 'consultation': 'note', 'courrier': 'courrier',
                   'autre': 'autre_document'}
ETATS = {'brouillon': 'À valider', 'valide': 'Validé', 'remplace': 'Remplacé par une version plus récente', 'retire': 'Retiré'}
ROLES_IMPORT = ('administrateur', 'avocat')
ROLES_PRODUCTION = ('administrateur', 'avocat', 'assistant')
CLE = re.compile(r'[A-Za-z0-9_.:-]{8,80}\Z')
EXEMPLE = 'trame_conclusions56109.docx'
EXEMPLE_SHA256 = '5a6c298fe3678ca4747d29039c7638d137233c870b8aed03ca9e95d8cfb5e087'


class ModeleRefuse(Stop):
    """Refus motivé (le motif est le code) : un modèle non admissible est orienté vers l'éditeur existant, jamais « corrompu »."""


# ------------------------------------------------------------------------------------------------ lecture sûre du paquet
def _texte_xml(data):
    if data[:2] in (b'\xff\xfe', b'\xfe\xff') or b'\x00' in data[:4]:
        return data.decode('utf-16', 'replace')
    return data.decode('utf-8', 'replace')


def _xml(data):
    """Analyse XML sans DOCTYPE ni entité (contrôle textuel, UTF-16 compris, puis refus par l'analyseur lui-même)."""
    haut = _texte_xml(data).upper()
    if '<!DOCTYPE' in haut or '<!ENTITY' in haut:
        raise ModeleRefuse('xml_actif')
    import xml.parsers.expat

    def refus(*_args):
        raise ModeleRefuse('xml_actif')
    sonde = xml.parsers.expat.ParserCreate()          # premier passage : la moindre déclaration de DOCTYPE ou d'entité arrête l'analyse
    sonde.StartDoctypeDeclHandler = refus
    sonde.EntityDeclHandler = refus
    try:
        sonde.Parse(bytes(data), True)
        return ET.fromstring(bytes(data))
    except (xml.parsers.expat.ExpatError, ET.ParseError, ValueError):
        raise ModeleRefuse('xml_invalide') from None


def _ouvrir(raw):
    if not isinstance(raw, (bytes, bytearray)) or not 0 < len(raw) <= MAX_DOCX:
        raise ModeleRefuse('taille_docx')
    raw = bytes(raw)
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        infos = z.infolist()
        noms = [i.filename for i in infos]
        if len(infos) > MAX_ENTREES or len(set(noms)) != len(noms):
            raise ModeleRefuse('archive_invalide')
        if not {'word/document.xml', '[Content_Types].xml', '_rels/.rels'} <= set(noms):
            raise ModeleRefuse('docx_incomplet')
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ModeleRefuse('archive_trop_grande')
        for i in infos:
            nom = i.filename.lower()
            if (nom.startswith('/') or '..' in nom.split('/') or '\\' in nom or i.flag_bits & 1 or i.compress_type not in (0, 8)):
                raise ModeleRefuse('archive_invalide')
            if any(x in nom for x in ('vbaproject', 'activex', 'embeddings/', '_xmlsignatures/')):
                raise ModeleRefuse('contenu_actif_ou_signe')
        parts = {i.filename: z.read(i.filename) for i in infos}
        z.close()
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
        raise ModeleRefuse('archive_invalide') from None
    for nom, data in parts.items():
        if nom.endswith(('.xml', '.rels')):
            racine = _xml(data)
            if nom.endswith('.rels') and any(str(el.get('TargetMode', '')).lower() == 'external' for el in racine.iter()):
                raise ModeleRefuse('relation_externe')
    return infos, parts


# ------------------------------------------------------------------------------------------------ zones (épissure, sans resérialisation)
def _prefixe(doc):
    """Préfixe lié à l'espace WordprocessingML sur la racine ; espace par défaut (sans préfixe) : non pris en charge."""
    racine = re.search(r'<(?![?!])[^>]*>', doc)
    if not racine:
        raise ModeleRefuse('xml_invalide')
    for p, uri in re.findall(r'\sxmlns:([A-Za-z_][\w.-]*)\s*=\s*["\']([^"\']*)["\']', racine.group(0)):
        if uri == W:
            return p, racine.group(0)
    raise ModeleRefuse('prefixe_non_pris_en_charge')


def _declarations(racine):
    return ' '.join(m.group(0).strip() for m in re.finditer(r'\sxmlns(?::[A-Za-z_][\w.-]*)?\s*=\s*("[^"]*"|\'[^\']*\')', racine))


def _portees(doc, p):
    """(début, fin) de chaque élément ``p:sdt`` du texte, imbriqués compris (pile)."""
    pile, out = [], []
    for m in re.finditer(r'<(/?)%s:sdt(?=[\s>/])[^>]*?(/?)>' % re.escape(p), doc):
        if m.group(1):
            if not pile:
                raise ModeleRefuse('xml_invalide')
            out.append((pile.pop(), m.end()))
        elif m.group(2):
            out.append((m.start(), m.end()))
        else:
            pile.append(m.start())
    if pile:
        raise ModeleRefuse('xml_invalide')
    return sorted(out)


def _zone(sdt, remplie=False):
    """(nom, nature, noeud prototype) d'une zone axh simple ; None si le contrôle n'est pas une zone axh ; refus si elle est complexe.
    ``remplie`` (contrôle du document produit) : la zone contient une ou plusieurs copies du prototype, chacune simple et de même nature."""
    tags = [t.get(WT + 'val') for t in sdt.findall('./%ssdtPr/%stag' % (WT, WT))]
    if not tags or not TAG.fullmatch(str(tags[0] or '')):
        return None
    if len(tags) != 1:
        raise ModeleRefuse('balise_ambigue')
    contenu = sdt.find(WT + 'sdtContent')
    if contenu is None or len(contenu) < 1 or (len(contenu) != 1 and not remplie):
        raise ModeleRefuse('zone_complexe')
    if remplie:
        natures = {_noeud_simple(n) for n in contenu}
        if len(natures) != 1:
            raise ModeleRefuse('zone_complexe')
        return tags[0][4:], natures.pop(), contenu[0]
    noeud = contenu[0]
    return (tags[0][4:], _noeud_simple(noeud), noeud)


def _noeud_simple(noeud):
    """Nature d'un noeud de zone (« paragraphs » ou « inline ») ; refus s'il n'est pas un paragraphe à un seul run, ou un run, à un seul texte."""
    if noeud.tag == WT + 'p':
        if any(c.tag not in (WT + 'pPr', WT + 'r') for c in noeud):
            raise ModeleRefuse('zone_complexe')
        runs = noeud.findall(WT + 'r')
        if len(runs) != 1:
            raise ModeleRefuse('zone_complexe')
        if noeud.find('.//' + WT + 'sectPr') is not None:
            raise ModeleRefuse('section_dans_zone')
        run, nature = runs[0], 'paragraphs'
    elif noeud.tag == WT + 'r':
        run, nature = noeud, 'inline'
    else:
        raise ModeleRefuse('zone_complexe')
    if any(c.tag not in (WT + 'rPr', WT + 't') for c in run) or len(run.findall(WT + 't')) != 1:
        raise ModeleRefuse('zone_complexe')
    return nature


def _zones(doc, remplie=False):
    """Zones axh du texte de ``word/document.xml`` : liste de dict (nom, nature, début, fin, élément)."""
    p, racine = _prefixe(doc)
    decl = _declarations(racine)
    zones = []
    for debut, fin in _portees(doc, p):
        fragment = doc[debut:fin]
        try:
            el = ET.fromstring('<axh-enveloppe %s>%s</axh-enveloppe>' % (decl, fragment))[0]
        except ET.ParseError:
            raise ModeleRefuse('xml_invalide') from None
        z = _zone(el, remplie)
        if z:
            zones.append({'nom': z[0], 'nature': z[1], 'debut': debut, 'fin': fin, 'element': z[2]})
    for a, b in zip(zones, zones[1:]):
        if b['debut'] < a['fin']:
            raise ModeleRefuse('zone_complexe')        # zone axh dans une zone axh
    noms = [z['nom'] for z in zones]
    if len(set(noms)) != len(noms):
        raise ModeleRefuse('balise_ambigue')
    return p, zones


def _valeurs(valeurs, attendus):
    if not isinstance(valeurs, dict) or len(valeurs) > 100:
        raise ModeleRefuse('valeurs_invalides')
    total = 0
    for k, v in valeurs.items():
        if not isinstance(k, str) or not TAG.fullmatch('axh:' + k):
            raise ModeleRefuse('cle_invalide')
        if (not isinstance(v, str) or len(v) > 200000 or any(ord(c) < 32 and c not in '\t\r\n' for c in v)
                or any(0xD800 <= ord(c) <= 0xDFFF for c in v)):
            raise ModeleRefuse('texte_invalide')
        total += len(v)
    if total > 500000:
        raise ModeleRefuse('texte_trop_long')
    if not attendus or set(attendus) != set(valeurs):
        raise ModeleRefuse('champs_absents_ou_inconnus')


def _lignes(valeur):
    lignes = valeur.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    if len(lignes) > 400:
        raise ModeleRefuse('trop_de_paragraphes')
    return lignes


def _copie(prototype, p, texte):
    """Copie textuelle du paragraphe (ou du run) prototype : identifiants Word retirés, seul le texte de l'unique ``w:t`` change."""
    ouverture = re.match(r'<[^>]*>', prototype).group(0)
    propre = re.sub(r'\s[A-Za-z_][\w.-]*:(?:paraId|textId)\s*=\s*("[^"]*"|\'[^\']*\')', '', ouverture)
    corps = propre + prototype[len(ouverture):]
    motif = re.compile(r'<%s:t(?=[\s>/])(?:[^>]*?/>|[^>]*>.*?</%s:t\s*>)' % (re.escape(p), re.escape(p)), re.S)
    if len(motif.findall(corps)) != 1:
        raise ModeleRefuse('zone_complexe')
    return motif.sub(lambda _m: '<%s:t xml:space="preserve">%s</%s:t>' % (p, e(texte, quote=False), p), corps)


def remplir(raw, valeurs):
    """Remplit une COPIE du modèle ; rend ``{bytes, template_sha256, fields, changed_parts, preserved_parts}``. Aucune instruction du modèle
    n'est interprétée (ni Jinja, ni champ) ; le texte est échappé."""
    infos, parts = _ouvrir(raw)
    try:
        doc = parts['word/document.xml'].decode('utf-8')      # Word écrit UTF-8 ; un autre encodage n'est pas épissé (refus motivé)
    except UnicodeDecodeError:
        raise ModeleRefuse('encodage_non_pris_en_charge') from None
    p, zones = _zones(doc)
    _valeurs(valeurs, [z['nom'] for z in zones])
    morceaux, curseur = [], 0
    for z in zones:
        fragment = doc[z['debut']:z['fin']]
        m = re.search(r'(<%s:sdtContent(?:\s[^>]*)?>)(.*)(</%s:sdtContent\s*>)' % (re.escape(p), re.escape(p)), fragment, re.S)
        if not m:
            raise ModeleRefuse('zone_complexe')
        prototype = m.group(2).strip()
        lignes = _lignes(valeurs[z['nom']])
        if z['nature'] == 'inline' and len(lignes) > 1:
            raise ModeleRefuse('saut_dans_champ_inline')
        nouveau = fragment[:m.start(2)] + ''.join(_copie(prototype, p, t) for t in lignes) + fragment[m.end(2):]
        morceaux.append(doc[curseur:z['debut']])
        morceaux.append(nouveau)
        curseur = z['fin']
    morceaux.append(doc[curseur:])
    document = ''.join(morceaux).encode('utf-8')
    _xml(document)                                      # document produit bien formé
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zout:
        for i in infos:
            zout.writestr(i, document if i.filename == 'word/document.xml' else parts[i.filename])
    return {'bytes': out.getvalue(), 'template_sha256': hashlib.sha256(bytes(raw)).hexdigest(), 'fields': sorted(z['nom'] for z in zones),
            'changed_parts': ['word/document.xml'], 'preserved_parts': {k: hashlib.sha256(v).hexdigest() for k, v in parts.items() if k != 'word/document.xml'}}


def _sans_zones(doc, zones):
    morceaux, curseur = [], 0
    for z in zones:
        morceaux.append(doc[curseur:z['debut']])
        morceaux.append('<axh:zone %s/>' % z['nom'])
        curseur = z['fin']
    morceaux.append(doc[curseur:])
    return ''.join(morceaux)


def _desechapper(texte):
    from html import unescape
    return unescape(texte)


def _proprietes(el, tag):
    noeud = el.find('.//' + WT + tag)
    return ET.tostring(noeud, encoding='unicode') if noeud is not None else ''


def controler(raw_modele, raw_sortie, valeurs):
    """Réouverture et contrôle du DOCX produit, avant d'annoncer « prêt ». Rend ``{ok, constats, resume}`` (jamais d'exception pour un
    écart : un écart est un constat)."""
    constats = []
    try:
        infos_m, parts_m = _ouvrir(raw_modele)
        with zipfile.ZipFile(io.BytesIO(bytes(raw_sortie))) as z:
            if z.testzip() is not None:
                constats.append({'code': 'archive_abimee', 'libelle': 'Archive du document produit abîmée'})
        infos_s, parts_s = _ouvrir(raw_sortie)
    except (Stop, zipfile.BadZipFile, OSError, TypeError):
        return {'ok': False, 'constats': [{'code': 'ouverture_impossible', 'libelle': 'Le document produit ne se rouvre pas'}],
                'resume': 'Le document produit ne se rouvre pas : il n’est pas prêt.'}
    if [i.filename for i in infos_m] != [i.filename for i in infos_s]:
        constats.append({'code': 'parties', 'libelle': 'Les parties du paquet diffèrent du modèle'})
    for nom, data in parts_m.items():
        if nom != 'word/document.xml' and parts_s.get(nom) != data:
            constats.append({'code': 'partie_modifiee', 'libelle': 'Partie protégée modifiée : %s' % nom})
    try:
        doc_m, doc_s = parts_m['word/document.xml'].decode('utf-8'), parts_s['word/document.xml'].decode('utf-8')
        p, zm = _zones(doc_m)
        _p2, zs = _zones(doc_s, remplie=True)
    except (Stop, UnicodeDecodeError, KeyError):
        constats.append({'code': 'zones_illisibles', 'libelle': 'Zones du document produit illisibles'})
        return {'ok': False, 'constats': constats, 'resume': 'Contrôle impossible : le document n’est pas prêt.'}
    if _sans_zones(doc_m, zm) != _sans_zones(doc_s, zs):
        constats.append({'code': 'texte_fixe', 'libelle': 'Le texte hors des zones a changé'})
    proto = {z['nom']: z for z in zm}
    if sorted(proto) != sorted(z['nom'] for z in zs) or set(proto) != set(valeurs or {}):
        constats.append({'code': 'zones', 'libelle': 'Les zones du document produit ne correspondent pas au modèle'})
    for z in zs:
        if z['nom'] not in proto or z['nom'] not in (valeurs or {}):
            continue
        attendu = _lignes(valeurs[z['nom']])
        fragment = ET.fromstring('<axh-enveloppe %s>%s</axh-enveloppe>' % (_declarations(_prefixe(doc_s)[1]), doc_s[z['debut']:z['fin']]))[0]
        noeuds = list(fragment.find(WT + 'sdtContent'))
        textes = [''.join(t.text or '' for t in n.iter(WT + 't')) for n in noeuds]
        if textes != attendu:
            constats.append({'code': 'valeur', 'libelle': 'Zone « %s » : texte différent de la valeur demandée' % z['nom']})
        modele = proto[z['nom']]['element']
        for n in noeuds:
            if _proprietes(n, 'pPr') != _proprietes(modele, 'pPr') or _proprietes(n, 'rPr') != _proprietes(modele, 'rPr'):
                constats.append({'code': 'style', 'libelle': 'Zone « %s » : style du prototype non conservé' % z['nom']})
                break
    ok = not constats
    return {'ok': ok, 'constats': constats, 'resume': 'Document rouvert et contrôlé : parties protégées identiques, texte fixe inchangé, zones remplies avec '
                                                     'leur style.' if ok else '%d écart(s) : le document n’est pas prêt.' % len(constats)}


def inspecter(raw):
    """Analyse pour l'import : zones (nom, style du prototype, texte d'exemple), texte des parties fixes (à relire par l'avocat : aucune
    donnée d'un autre client ne doit y rester), éléments conservés tels quels. Refus motivé si le paquet ou une zone n'est pas admissible."""
    _infos, parts = _ouvrir(raw)
    try:
        doc = parts['word/document.xml'].decode('utf-8')
    except UnicodeDecodeError:
        raise ModeleRefuse('encodage_non_pris_en_charge') from None
    _p, zones = _zones(doc)
    if not zones:
        raise ModeleRefuse('aucune_zone')
    fixes = []          # texte des parties fixes (hors zones), à relire par l'avocat avant validation
    for morceau in re.split(r'<axh:zone [a-z0-9_]+/>', _sans_zones(doc, zones)):
        for para in re.findall(r'<%s:p(?=[\s>]).*?</%s:p>' % (re.escape(_p), re.escape(_p)), morceau, re.S):
            texte = ''.join(re.findall(r'<%s:t(?:\s[^>]*)?>([^<]*)</%s:t>' % (re.escape(_p), re.escape(_p)), para)).strip()
            if texte and len(fixes) < 200:
                fixes.append(_desechapper(texte)[:300])
    infos = []
    corps = parts['word/document.xml']
    for motif, libelle in ((b'fldSimple', 'champs Word'), (b'instrText', 'champs Word'), (b'<w:tbl', 'tableaux'), (b'numPr', 'numérotation'),
                           (b'footnoteReference', 'notes'), (b'w:ins ', 'suivi des modifications'), (b'commentReference', 'commentaires')):
        if motif in corps and libelle not in infos:
            infos.append(libelle)
    return {'champs': [z['nom'] for z in zones],
            'zones': [{'nom': z['nom'], 'nature': z['nature'], 'style': (z['element'].find('.//%spStyle' % WT).get(WT + 'val') if z['element'].find('.//%spStyle' % WT) is not None else ''),
                       'exemple': ''.join(t.text or '' for t in z['element'].iter(WT + 't'))[:200]} for z in zones],
            'textes_fixes': fixes, 'conserves': infos, 'parties': sorted(parts),
            'protegees': {k: hashlib.sha256(v).hexdigest() for k, v in parts.items() if k != 'word/document.xml'}}


# ------------------------------------------------------------------------------------------------ registre des modèles et productions
SCHEMA = '''
CREATE TABLE IF NOT EXISTS modeles_actes56109(id TEXT PRIMARY KEY, nom TEXT NOT NULL, type_acte TEXT NOT NULL, droit TEXT NOT NULL DEFAULT '',
  juridiction TEXT NOT NULL DEFAULT '', variante TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL, sha256 TEXT NOT NULL, taille INTEGER NOT NULL,
  analyse TEXT NOT NULL DEFAULT '{}', etat TEXT NOT NULL, exemple INTEGER NOT NULL DEFAULT 0, cree_par TEXT NOT NULL, cree_le TEXT NOT NULL,
  valide_par TEXT NOT NULL DEFAULT '', valide_le TEXT NOT NULL DEFAULT '', cle TEXT NOT NULL, UNIQUE(cree_par, cle));
CREATE INDEX IF NOT EXISTS modeles_actes56109_type ON modeles_actes56109(type_acte, droit, juridiction, variante, etat);
CREATE TABLE IF NOT EXISTS modeles_productions56109(id TEXT PRIMARY KEY, acteur TEXT NOT NULL, cle TEXT NOT NULL, empreinte TEXT NOT NULL,
  modele_id TEXT NOT NULL, modele_version INTEGER NOT NULL, modele_sha256 TEXT NOT NULL, matter_id TEXT NOT NULL, titre TEXT NOT NULL,
  chemin TEXT NOT NULL, sha256 TEXT NOT NULL, etat TEXT NOT NULL, controle TEXT NOT NULL DEFAULT '{}', document_id TEXT NOT NULL DEFAULT '',
  version_id TEXT NOT NULL DEFAULT '', cree_le TEXT NOT NULL, maj_le TEXT NOT NULL, UNIQUE(acteur, cle));
CREATE INDEX IF NOT EXISTS modeles_productions56109_dossier ON modeles_productions56109(matter_id, cree_le);
'''
NOMS_SCHEMA = ('modeles_actes56109', 'modeles_actes56109_type', 'modeles_productions56109', 'modeles_productions56109_dossier')


def _maintenant():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_schema(desk):
    existants = {r[0] for r in desk.db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    if any(n not in existants for n in NOMS_SCHEMA):
        if desk.db.in_transaction:
            desk.db.commit()
        desk.db.executescript(SCHEMA)
        desk.db.commit()


def _racine(desk):
    return Path(desk.c['state_dir']) / 'modeles-actes56109'


def _ecrire_prive(chemin, data):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(chemin.parent, 0o700)
    except OSError:
        pass
    tmp = chemin.with_name('.' + chemin.name + '.' + secrets.token_hex(4))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def _texte_court(valeur, limite, code):
    v = str(valeur or '').strip()
    if len(v) > limite or any(ord(c) < 32 for c in v):
        raise Stop(code)
    return v


def modele(desk, ident):
    if not re.fullmatch(r'[a-f0-9]{32}', str(ident or '')):
        raise Stop('introuvable')
    ensure_schema(desk)
    r = desk.db.execute('SELECT * FROM modeles_actes56109 WHERE id=?', (ident,)).fetchone()
    if not r:
        raise Stop('introuvable')
    return dict(r)


def octets_modele(desk, row):
    data = (_racine(desk) / (row['id'] + '.docx')).read_bytes()
    if hashlib.sha256(data).hexdigest() != row['sha256']:
        raise Stop('modele_modifie')          # copie privée modifiée hors de l'application : jamais utilisée
    return data


def lister(desk):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM modeles_actes56109 ORDER BY type_acte, nom, version DESC LIMIT 200')]


def importer(desk, acteur, role, raw, nom, type_acte, droit='', juridiction='', variante='', cle='', exemple=False):
    """Importe un DOCX du cabinet comme modèle À VALIDER : original conservé (dossier privé), zones et parties fixes analysées."""
    if role not in ROLES_IMPORT:
        raise Stop('role_interdit')
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    if type_acte not in TYPES_ACTE:
        raise Stop('type_acte_inconnu')
    nom = _texte_court(nom, 120, 'nom_modele_invalide') or TYPES_ACTE[type_acte]
    droit = _texte_court(droit, 12, 'droit_invalide').upper()
    if droit and not re.fullmatch(r'[A-Z]{2}(?:-[A-Z]{2,3})?', droit):
        raise Stop('droit_invalide')
    juridiction, variante = _texte_court(juridiction, 120, 'juridiction_invalide'), _texte_court(variante, 80, 'variante_invalide')
    ensure_schema(desk)
    compte = str(acteur)[:160]
    old = desk.db.execute('SELECT * FROM modeles_actes56109 WHERE cree_par=? AND cle=?', (compte, cle)).fetchone()
    sha = hashlib.sha256(bytes(raw or b'')).hexdigest()
    if old:
        if old['sha256'] != sha:
            raise Stop('cle_reutilisee')
        return {**_public(dict(old)), 'replayed': True}
    analyse = inspecter(raw)
    ident = secrets.token_hex(16)
    _ecrire_prive(_racine(desk) / (ident + '.docx'), bytes(raw))
    version = desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM modeles_actes56109 WHERE nom=? AND type_acte=? AND droit=? AND juridiction=? AND variante=?',
                              (nom, type_acte, droit, juridiction, variante)).fetchone()[0]
    desk.db.execute('INSERT INTO modeles_actes56109(id,nom,type_acte,droit,juridiction,variante,version,sha256,taille,analyse,etat,exemple,cree_par,cree_le,cle) '
                    "VALUES(?,?,?,?,?,?,?,?,?,?,'brouillon',?,?,?,?)", (ident, nom, type_acte, droit, juridiction, variante, version, sha, len(raw),
                                                                        json.dumps(analyse, ensure_ascii=False), int(bool(exemple)), compte, _maintenant(), cle))
    desk.db.commit()
    desk.audit('modele_acte56109_importe', {'id': ident, 'type': type_acte, 'version': version, 'sha256': sha, 'par': compte})
    return {**_public(modele(desk, ident)), 'replayed': False}


def importer_exemple(desk, acteur, role, cle):
    """Trame technique fournie (exemple de zones et de styles, PAS un acte conforme), importée comme brouillon à valider."""
    raw = (Path(__file__).resolve().parent / 'data' / EXEMPLE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXEMPLE_SHA256:
        raise Stop('modele_modifie')
    return importer(desk, acteur, role, raw, 'Trame technique (exemple, pas un acte conforme)', 'conclusions', '', '', 'exemple', cle, exemple=True)


def valider(desk, acteur, role, droits, ident, sha, cle=''):
    """Validation par l'avocat (droit « Valider un modèle ») de la version vue (empreinte) ; la version validée précédente du même type, droit,
    juridiction et variante passe « remplacée » (conservée)."""
    if role not in ROLES_IMPORT or not (droits or {}).get('valider_modele'):
        raise Stop('role_interdit')
    row = modele(desk, ident)
    if row['etat'] == 'valide':
        return {**_public(row), 'replayed': True}
    if row['etat'] != 'brouillon':
        raise Stop('modele_deja_decide')
    if not secrets.compare_digest(str(sha or ''), row['sha256']):
        raise Stop('version_perimee')
    octets_modele(desk, row)
    desk.db.execute("UPDATE modeles_actes56109 SET etat='remplace' WHERE etat='valide' AND type_acte=? AND droit=? AND juridiction=? AND variante=? AND id<>?",
                    (row['type_acte'], row['droit'], row['juridiction'], row['variante'], ident))
    desk.db.execute("UPDATE modeles_actes56109 SET etat='valide', valide_par=?, valide_le=? WHERE id=?", (str(acteur)[:160], _maintenant(), ident))
    desk.db.commit()
    desk.audit('modele_acte56109_valide', {'id': ident, 'sha256': row['sha256'], 'par': str(acteur)[:160]})
    return {**_public(modele(desk, ident)), 'replayed': False}


def _public(row):
    analyse = json.loads(row.get('analyse') or '{}')
    return {'id': row['id'], 'nom': row['nom'], 'type_acte': row['type_acte'], 'type_label': TYPES_ACTE.get(row['type_acte'], ''), 'droit': row['droit'],
            'juridiction': row['juridiction'], 'variante': row['variante'], 'version': row['version'], 'sha256': row['sha256'], 'etat': row['etat'],
            'etat_label': ETATS.get(row['etat'], row['etat']), 'champs': analyse.get('champs', []), 'zones': analyse.get('zones', []),
            'textes_fixes': analyse.get('textes_fixes', []), 'conserves': analyse.get('conserves', []), 'exemple': bool(row.get('exemple'))}


def _nom_fichier(type_acte, titre, version, quand):
    base = re.sub(r'[^\w À-ÿ().-]+', ' ', '%s %s' % (TYPES_ACTE[type_acte], titre)).strip()[:90] or TYPES_ACTE[type_acte]
    return '%s (modèle v%d, %s).docx' % (base, version, quand)


def produire(desk, acteur, role, hidden, ident, matter, valeurs, titre, cle, client=None):
    """Acte depuis un modèle VALIDÉ : contrôle avant écriture, intention durable, création exclusive dans le dossier, relecture et SECOND contrôle
    du fichier écrit ; « prêt » seulement ensuite. Même clé : même production (une réponse perdue se rapproche sur la même cible)."""
    from . import livrables5689 as liv
    from .common import clean_path, load_matters, under
    from .pause5629 import checkpoint
    if role not in ROLES_PRODUCTION:
        raise Stop('role_interdit')
    if not CLE.fullmatch(str(cle or '')):
        raise Stop('cle_requise')
    row = modele(desk, ident)
    titre = _texte_court(titre, 160, 'titre_invalide') or row['nom']
    matter = str(matter or '')
    if not matter or matter in hidden:
        raise Stop('introuvable')
    dossiers = {str(m['id']): m for m in load_matters(desk.c)}
    if matter not in dossiers:
        raise Stop('introuvable')
    compte = str(acteur)[:160]
    empreinte = hashlib.sha256(json.dumps({'m': ident, 'd': matter, 'v': valeurs, 't': titre}, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    old = desk.db.execute('SELECT * FROM modeles_productions56109 WHERE acteur=? AND cle=?', (compte, cle)).fetchone()
    if old:
        if old['empreinte'] != empreinte:
            raise Stop('cle_reutilisee')
        if old['etat'] in ('pret', 'non_conforme'):
            return {**_production_publique(dict(old)), 'replayed': True}
        return _rapprocher(desk, dict(old), row, valeurs, client)
    if row['etat'] != 'valide':
        raise Stop('modele_non_valide')
    raw = octets_modele(desk, row)
    rendu = remplir(raw, valeurs)
    controle = controler(raw, rendu['bytes'], valeurs)
    if not controle['ok']:
        raise Stop('controle_docx_echoue')     # rien n'est écrit
    dossier = dossiers[matter]
    sous = str((desk.c.get('word_legal') or {}).get('destination_subfolder') or '20_Actes_et_conclusions/90_AxiorHub_Brouillons').strip('/')
    repertoire = clean_path(dossier['path'] + '/' + sous)
    if not under(repertoire, dossier['path']) or repertoire == dossier['path']:
        raise Stop('destination_invalide')
    from .fichiers56103 import horodatage
    chemin = clean_path(repertoire + '/' + _nom_fichier(row['type_acte'], titre, row['version'], horodatage(desk)))
    sha = hashlib.sha256(rendu['bytes']).hexdigest()
    pid, quand = secrets.token_hex(16), _maintenant()
    desk.db.execute('INSERT INTO modeles_productions56109(id,acteur,cle,empreinte,modele_id,modele_version,modele_sha256,matter_id,titre,chemin,sha256,etat,controle,cree_le,maj_le) '
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,'ecriture',?,?,?)", (pid, compte, cle, empreinte, ident, row['version'], row['sha256'], matter, titre, chemin, sha,
                                                                     json.dumps(controle, ensure_ascii=False), quand, quand))
    desk.db.commit()                                 # intention durable AVANT l'effet : la cible ne changera plus pour cette clé
    sortie = liv.Sortie(desk, 'modeles_actes56109', pid, sha, matter=matter, kind=NATURE_REGISTRE[row['type_acte']], title=titre, path=chemin, mime=DOCX, sha256=sha,
                        provenance={'producteur': 'modeles_actes56109', 'modele': ident, 'modele_version': row['version'], 'modele_sha256': row['sha256']},
                        owner=compte, label='%s (modèle du cabinet)' % TYPES_ACTE[row['type_acte']])
    client = client or _client(desk)
    checkpoint(desk)
    try:
        client.ensure_folder(repertoire, dossier['path'])
        client.put_file(chemin, rendu['bytes'], DOCX)
    except Stop as ex:
        if str(ex).split(':')[0] not in ('http_412', 'deja_existant'):
            _maj(desk, pid, etat='a_verifier', controle=json.dumps({'motif': 'resultat_inconnu'}))
            return {**_production_publique(_production(desk, pid)), 'replayed': False}
    except Exception:   # noqa: BLE001 — transport en panne : résultat inconnu, jamais « échec » (la même clé relira la même cible)
        _maj(desk, pid, etat='a_verifier', controle=json.dumps({'motif': 'resultat_inconnu'}))
        return {**_production_publique(_production(desk, pid)), 'replayed': False}
    return _constater(desk, _production(desk, pid), row, raw, valeurs, client, sortie)


def _client(desk):
    from .document_projects import _dav
    return _dav(desk)


def _production(desk, pid):
    return dict(desk.db.execute('SELECT * FROM modeles_productions56109 WHERE id=?', (pid,)).fetchone())


def _maj(desk, pid, **champs):
    champs['maj_le'] = _maintenant()
    desk.db.execute('UPDATE modeles_productions56109 SET %s WHERE id=?' % ', '.join(k + '=?' for k in champs), tuple(champs.values()) + (pid,))
    desk.db.commit()


def _constater(desk, prod, row, raw, valeurs, client, sortie=None):
    """Relecture du fichier écrit, contrôle de nouveau ; registre vérifié ; « prêt » seulement si le second contrôle passe."""
    from . import livrables5689 as liv
    try:
        info = client.stat(prod['chemin'])
        relu = client.download({**info, 'path': prod['chemin']})
    except Stop as ex:
        motif = 'cible_absente' if str(ex).split(':')[0] in ('http_404', 'fichier_nextcloud_introuvable') else 'relecture_impossible'
        _maj(desk, prod['id'], etat='a_verifier', controle=json.dumps({'motif': motif}))
        return {**_production_publique(_production(desk, prod['id'])), 'replayed': False}
    if hashlib.sha256(relu).hexdigest() != prod['sha256']:
        _maj(desk, prod['id'], etat='a_verifier', controle=json.dumps({'motif': 'cible_differente'}))
        return {**_production_publique(_production(desk, prod['id'])), 'replayed': False}
    second = controler(raw, relu, valeurs)
    sortie = sortie or liv.Sortie(desk, 'modeles_actes56109', prod['id'], prod['sha256'], matter=prod['matter_id'], kind=NATURE_REGISTRE[row['type_acte']],
                                  title=prod['titre'], path=prod['chemin'], mime=DOCX, sha256=prod['sha256'], owner=prod['acteur'])
    item = sortie.constater(client, prod['chemin'], relu) or {}
    if not second['ok']:
        if item.get('id'):
            liv._echec(desk, item['id'], 'failed', 'controle_docx_non_conforme')
        _maj(desk, prod['id'], etat='non_conforme', controle=json.dumps(second, ensure_ascii=False), document_id=item.get('document_id', ''), version_id=item.get('version_id', ''))
        return {**_production_publique(_production(desk, prod['id'])), 'replayed': False}
    etat = 'pret' if item.get('production_state') == 'verified' else 'a_verifier'
    _maj(desk, prod['id'], etat=etat, controle=json.dumps(second, ensure_ascii=False), document_id=item.get('document_id', ''), version_id=item.get('version_id', ''))
    desk.audit('modele_acte56109_produit', {'production': prod['id'], 'modele': prod['modele_id'], 'sha256': prod['sha256'], 'etat': etat})
    return {**_production_publique(_production(desk, prod['id'])), 'replayed': False}


def _rapprocher(desk, prod, row, valeurs, client=None):
    """Réponse perdue ou état « à vérifier » : seule la cible figée est relue (aucune autre cible, aucune réécriture automatique)."""
    raw = octets_modele(desk, row) if row['sha256'] == prod['modele_sha256'] else None
    if raw is None:
        raise Stop('modele_modifie')
    return _constater(desk, prod, row, raw, valeurs, client or _client(desk))


ETATS_PRODUCTION = {'ecriture': 'Écriture en cours', 'a_verifier': 'À vérifier : écriture non confirmée (la même demande relit le même fichier)',
                    'pret': 'Prêt : document écrit, rouvert et contrôlé', 'non_conforme': 'Écrit mais non conforme au contrôle : pas prêt'}


def _production_publique(p):
    return {'production_id': p['id'], 'etat': p['etat'], 'etat_label': ETATS_PRODUCTION.get(p['etat'], p['etat']), 'pret': p['etat'] == 'pret',
            'chemin_nom': PurePosixPath(p['chemin']).name, 'document_id': p['document_id'], 'version_id': p['version_id'],
            'controle': json.loads(p['controle'] or '{}'), 'modele_version': p['modele_version']}


# ------------------------------------------------------------------------------------------------ routes ``m56109/modeles/``
PREFIXE = 'm56109/modeles/'
STATUTS_HTTP = {'introuvable': '404 Not Found', 'version_perimee': '409 Conflict', 'cle_reutilisee': '409 Conflict', 'modele_deja_decide': '409 Conflict',
                'role_interdit': '403 Forbidden', 'origine_ou_csrf_refuse': '403 Forbidden', 'taille_docx': '413 Payload Too Large',
                'requete_trop_longue': '413 Payload Too Large', 'modele_non_valide': '409 Conflict', 'modele_modifie': '409 Conflict'}
MESSAGES = {
    'introuvable': 'Modèle introuvable, ou action non permise.', 'role_interdit': 'Votre rôle ne permet pas cette action.',
    'version_perimee': 'Le modèle a changé depuis votre affichage : rechargez la page.', 'cle_reutilisee': 'Cette demande a déjà servi : rechargez la page.',
    'cle_requise': 'Demande incomplète : rechargez la page.', 'modele_non_valide': 'Ce modèle doit d’abord être validé par un avocat.',
    'modele_modifie': 'La copie du modèle a changé hors de l’application : rien n’a été produit.',
    'controle_docx_echoue': 'Le document rempli n’a pas passé le contrôle : rien n’a été écrit.',
    'champs_absents_ou_inconnus': 'Renseignez chaque zone du modèle (et seulement celles-ci).',
    'aucune_zone': 'Ce DOCX ne contient aucune zone « axh:… » : ajoutez des contrôles de contenu balisés, ou utilisez l’éditeur existant.',
    'zone_complexe': 'Une zone contient un tableau, un champ, une image ou plusieurs mises en forme : elle n’est pas remplie automatiquement. Utilisez '
                     'l’éditeur existant pour ce modèle.',
    'contenu_actif_ou_signe': 'Modèle refusé : macros, objets incorporés ou signature.', 'relation_externe': 'Modèle refusé : lien vers une ressource externe.',
    'xml_actif': 'Modèle refusé : contenu XML actif.', 'archive_invalide': 'Fichier refusé : ce n’est pas un DOCX lisible.',
    'prefixe_non_pris_en_charge': 'Structure Word non prise en charge pour le remplissage : utilisez l’éditeur existant.',
}


def allowed(role, method, api):
    reste = str(api or '').split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    if method in ('GET', 'HEAD'):
        return True
    if method != 'POST':
        return False
    if reste in ('importer', 'exemple'):
        return role in ROLES_IMPORT
    if len(parts) == 2 and parts[1] == 'valider':
        return role in ROLES_IMPORT
    if len(parts) == 2 and parts[1] == 'produire':
        return role in ROLES_PRODUCTION
    return False


def _sortie(data, status='200 OK'):
    from .web440 import json_out
    out = json_out(data, status)
    out['headers'] = [('Cache-Control', 'private, no-store')]
    return out


def _corps_docx(env, auth):
    import hmac
    if env.get('HTTP_ORIGIN') != auth['origin'] or not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), auth['csrf']):
        raise Stop('origine_ou_csrf_refuse')
    try:
        longueur = int(env.get('CONTENT_LENGTH') or 0)
    except ValueError:
        raise Stop('requete_trop_longue') from None
    if not 0 < longueur <= MAX_DOCX:
        raise Stop('taille_docx')
    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/octet-stream':
        raise Stop('type_document_invalide')
    return env['wsgi.input'].read(longueur)


def api(env, desk, auth, prefix, name, args, method, client=None):
    from .membres5650 import effective_rights, hidden_for
    from .web567 import actor
    owner, role = actor(env)
    reste = str(name).split('?', 1)[0][len(PREFIXE):].strip('/')
    parts = reste.split('/')
    try:
        if role not in ROLES_PRODUCTION:
            raise Stop('role_interdit')
        try:
            hidden, droits = hidden_for(desk.c['state_dir'], owner, role), effective_rights(desk.c['state_dir'], owner, role)
        except Exception:   # noqa: BLE001
            return _sortie({'error': 'controle_acces_indisponible', 'message': 'Contrôle des accès indisponible.'}, '503 Service Unavailable')
        if method in ('GET', 'HEAD'):
            if reste == 'liste':
                return _sortie({'modeles': [_public(r) for r in lister(desk)]})
            if len(parts) == 2 and parts[1] == 'apercu':
                return _sortie(_public(modele(desk, parts[0])))
            raise Stop('introuvable')
        if not allowed(role, method, name):
            raise Stop('role_interdit')
        if reste == 'importer':
            raw = _corps_docx(env, auth)
            return _sortie(importer(desk, owner, role, raw, args.get('nom'), str(args.get('type') or ''), args.get('droit') or '', args.get('juridiction') or '',
                                    args.get('variante') or '', args.get('cle')))
        from .web440 import check_post
        d = check_post(env, auth)
        if reste == 'exemple':
            return _sortie(importer_exemple(desk, owner, role, d.get('cle')))
        if parts[1] == 'valider':
            return _sortie(valider(desk, owner, role, droits, parts[0], d.get('sha256'), d.get('cle')))
        out = produire(desk, owner, role, hidden, parts[0], d.get('dossier'), d.get('valeurs'), d.get('titre'), d.get('cle'), client)
        if out['document_id']:
            out['href'] = prefix + '/livrables/' + out['document_id']
        return _sortie(out, '200 OK' if out['etat'] in ('pret', 'non_conforme') else '202 Accepted')
    except Stop as ex:
        code = str(ex).split(':')[0]
        if code in ('dossier_reserve', 'livrable_absent'):
            code = 'introuvable'
        from .erreurs5634 import explain
        return _sortie({'error': code, 'message': MESSAGES.get(code) or explain(code, None)}, STATUTS_HTTP.get(code, '422 Unprocessable Entity'))


# ------------------------------------------------------------------------------------------------ Produire › Modèles
def section_html(desk, prefix, owner, role):
    """Bibliothèque des modèles Word du cabinet (vue Modèles de Produire) : importer, relire les zones et les parties fixes, valider, produire."""
    from .membres5650 import effective_rights, viewer_hidden
    from .common import load_matters, matter_display
    try:
        droits = effective_rights(desk.c['state_dir'], owner, role)
    except Exception:   # noqa: BLE001
        droits = {}
    hidden = viewer_hidden() or frozenset()
    try:
        dossiers = ''.join('<option value="%s">%s</option>' % (e(str(m['id']), quote=True), e(matter_display(m))) for m in load_matters(desk.c) if str(m['id']) not in hidden)
    except Exception:   # noqa: BLE001
        dossiers = ''
    api = prefix + '/api440/' + PREFIXE
    articles = []
    for r in lister(desk):
        p = _public(r)
        zones = ''.join('<li><code>%s</code> <span class="v109-meta">style %s</span></li>' % (e(z['nom']), e(z['style'] or '—')) for z in p['zones'])
        fixes = ''.join('<li>%s</li>' % e(t) for t in p['textes_fixes'][:40])
        actions = ''
        if p['etat'] == 'brouillon' and role in ROLES_IMPORT and droits.get('valider_modele'):
            actions = ('<form class="v109-form" data-v109="valider" data-v109-api="%s"><input type="hidden" name="sha256" value="%s">'
                       '<label><input type="checkbox" name="vu" required> J’ai relu les zones et les parties fixes (aucune donnée d’un autre client)</label>'
                       '<button class="ax-btn" type="submit">Valider ce modèle</button><p class="v109-etat" role="status" aria-live="polite"></p></form>') % (
                e(api + p['id'] + '/valider', quote=True), e(p['sha256'], quote=True))
        elif p['etat'] == 'valide' and role in ROLES_PRODUCTION:
            champs = ''.join('<label>%s<textarea name="champ:%s" rows="2" required></textarea></label>' % (e(c), e(c, quote=True)) for c in p['champs'])
            actions = ('<details class="v109-produire"><summary class="ax-btn ghost">Produire un acte avec ce modèle</summary><form class="v109-form" data-v109="produire" '
                       'data-v109-api="%s"><label>Dossier<select name="dossier" required><option value="">Choisir…</option>%s</select></label><label>Titre<input name="titre" '
                       'maxlength="160"></label>%s<button class="ax-btn" type="submit">Produire et contrôler</button><p class="v109-etat" role="status" aria-live="polite">'
                       '</p></form></details>') % (e(api + p['id'] + '/produire', quote=True), dossiers, champs)
        avert = '<p class="v109-note">Exemple technique de zones et de styles : ce n’est pas un acte conforme.</p>' if p['exemple'] else ''
        articles.append(('<article class="v109-modele"><h3>%s <span class="v109-meta">%s · version %d · %s%s</span></h3>%s'
                         '<details><summary>Zones (%d) et parties fixes</summary><ul>%s</ul><p class="ax-muted">Parties fixes à relire :</p><ul class="v109-fixes">%s</ul>%s</details>%s</article>') % (
            e(p['nom']), e(p['type_label']), p['version'], e(p['etat_label']), (' · ' + e(' '.join(x for x in (p['droit'], p['juridiction'], p['variante']) if x))) if (p['droit'] or p['juridiction'] or p['variante']) else '',
            avert, len(p['zones']), zones, fixes, ('<p class="ax-muted">Conservés tels quels : %s.</p>' % e(', '.join(p['conserves']))) if p['conserves'] else '', actions))
    importer_html = ''
    if role in ROLES_IMPORT:
        types = ''.join('<option value="%s">%s</option>' % (k, e(v)) for k, v in TYPES_ACTE.items())
        importer_html = ('<details class="v109-import"><summary class="ax-btn ghost">Importer un modèle Word (.docx)</summary><form class="v109-form" data-v109="importer" '
                         'data-v109-api="%s"><label>Fichier<input type="file" name="fichier" accept=".docx" required></label><label>Nom<input name="nom" maxlength="120"></label>'
                         '<label>Type d’acte<select name="type">%s</select></label><label>Droit applicable (FR, UK, DE, ES, CA-QC…)<input name="droit" maxlength="12"></label>'
                         '<label>Juridiction<input name="juridiction" maxlength="120"></label><label>Variante<input name="variante" maxlength="80"></label>'
                         '<button class="ax-btn" type="submit">Importer (à valider)</button><p class="v109-etat" role="status" aria-live="polite"></p></form>'
                         '<form class="v109-form" data-v109="exemple" data-v109-api="%s"><button class="ax-btn ghost" type="submit">Importer la trame technique d’exemple</button>'
                         '<p class="v109-etat" role="status" aria-live="polite"></p></form></details>') % (e(api + 'importer', quote=True), types, e(api + 'exemple', quote=True))
    corps = ''.join(articles) or '<p class="ax-muted">Aucun modèle Word importé pour l’instant.</p>'
    return ('<section class="ax-card v109-panneau" aria-labelledby="v109-t-word"><h2 id="v109-t-word">Modèles Word du cabinet</h2><p class="ax-muted">Zones « axh:… » '
            'remplies dans une copie du modèle ; styles, en-têtes, pieds, numérotation et images conservés. Le document produit est rouvert et contrôlé avant '
            'd’être annoncé prêt. Pagination, polices du poste et suivi des modifications : à vérifier dans Word ou LibreOffice.</p>%s%s</section>') % (importer_html, corps)

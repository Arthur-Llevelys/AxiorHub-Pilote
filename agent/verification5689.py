"""5.6.89 : vérification d'un document, en quatre contrôles distincts (audit 5.6.88, § 40 ; contrat DocumentVerification).

Stockage, format et présentation, sources et cohérence, relecture humaine : chacun a sa preuve, aucun n'en implique un autre. Une réouverture réussie n'est pas une validation juridique ; l'ancien booléen
``verified`` n'est repris que comme preuve de stockage. Il n'existe volontairement aucun champ « document validé » : « Prêt à relire » veut dire fichier accessible, contrôles prévus exécutés, limites visibles.

Les contrôles sont idempotents par document, version et type de contrôle. Un changement de contenu crée une version, donc des preuves neuves ; un simple déplacement n'invalide que la preuve de stockage et
une source modifiée que les contrôles de sources. Une citation dont la source n'a pas été consultée reste « Non vérifiée » : une correspondance de texte ne prouve pas que l'affirmation est soutenue.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import hashlib
import io
import json
from pathlib import PurePosixPath
import posixpath
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

from .common import Stop, fold
from . import refs470

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
CHECKS = {'storage.file': ('storage', 'Fichier publié, empreinte et réouverture', 'stockage'),
          'format.archive': ('format', 'Archive Word et liens internes', 'contenu'),
          'format.structure': ('format', 'Styles, numérotation, images et renvois de champ', 'contenu'),
          'format.rendu': ('format', 'Rendu dans le moteur bureautique', 'contenu'),
          'sources.champs': ('sources', 'Champs non renseignés', 'contenu'),
          'sources.renvois': ('sources', 'Renvois internes', 'contenu'),
          'sources.pieces': ('sources', 'Références des pièces', 'sources'),
          'sources.parties_montants': ('sources', 'Cohérence des parties et des montants', 'sources'),
          'sources.citations': ('sources', 'Citations et références', 'sources'),
          'human.review': ('human_review', 'Relecture humaine', 'relecture')}
REQUIRED_DOCX = ('storage.file', 'format.archive', 'format.structure', 'sources.champs', 'sources.renvois', 'sources.pieces', 'sources.parties_montants', 'sources.citations')
REQUIRED_OTHER = ('storage.file', 'sources.champs', 'sources.citations')
STATES = {'pending': 'À faire', 'passed': 'Réussi', 'failed': 'Échec', 'partial': 'Partiel', 'not_applicable': 'Sans objet'}
NON_VERIFIEE = 'Non vérifiée'
HOLES = (r'\{\{\s*[\w.-]+\s*\}\}', r'\[\s*(?:à|a)\s+(?:compl[ée]ter|pr[ée]ciser)[^\]]*\]', r'\[\s*X+\s*\]', r'_{4,}', r'\bXXX+\b', r'\.{6,}', r'●+')
CITATION = re.compile(r"(?:\barticles?\s+[LRD]\.?\s*\d[\d\-.]*\s+du\s+code\s+[^\s,.;]+(?:\s+[^\s,.;]+){0,2}"
                      r"|\b(?:Cass\.?|Cour de cassation|CE|Conseil d[’']Etat|Conseil d[’']État|CA)\s[^;\n]{0,60}?\b(?:n°\s*)?\d{2}[-.]\d{2}[.\-]?\d{3}\b"
                      r"|\bCJUE\b[^.;\n]{0,60}?\bC-\d+/\d+\b)", re.I)
PIECE = re.compile(r'\bpi[èe]ces?\s*(?:n[°o]\.?\s*)(\d{1,4})\b', re.I)
RENVOI = re.compile(r"\b(?:l[’']article|la clause|la section|l[’']annexe|article|clause|section|annexe)\s+(\d{1,3})\s+(?:ci-dessus|ci-dessous|du pr[ée]sent|de la pr[ée]sente|des pr[ée]sentes)", re.I)
TITRE = re.compile(r'^\s*(?:article|clause|section|annexe)\s+(\d{1,3})\b', re.I)
MONTANT = re.compile(r'(\d{1,3}(?:[  . ]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*(?:€|euros?\b)', re.I)


def schema(desk):
    desk.db.execute('''CREATE TABLE IF NOT EXISTS document_checks5689(document_id TEXT NOT NULL, version_id TEXT NOT NULL, check_id TEXT NOT NULL, item_id TEXT NOT NULL, domain TEXT NOT NULL, state TEXT NOT NULL,
        evidence_ref TEXT, reservations TEXT NOT NULL DEFAULT '[]', details TEXT NOT NULL DEFAULT '{}', depends TEXT NOT NULL DEFAULT 'contenu', checker TEXT NOT NULL DEFAULT 'automatique', checked_at TEXT NOT NULL,
        PRIMARY KEY(document_id, version_id, check_id))''')
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _set(desk, item, check_id, state, evidence=None, reservations=(), details=None, checker='automatique'):
    """Un contrôle = une ligne par document et version : le rejouer remplace son résultat, sans doublon ni évènement répété."""
    domain, _, depends = CHECKS[check_id]
    if state == 'passed' and domain in ('storage', 'format') and not evidence:
        raise Stop('preuve_de_controle_absente')
    before = desk.db.execute('SELECT state FROM document_checks5689 WHERE document_id=? AND version_id=? AND check_id=?', (item['document_id'], item['version_id'], check_id)).fetchone()
    desk.db.execute('INSERT OR REPLACE INTO document_checks5689(document_id,version_id,check_id,item_id,domain,state,evidence_ref,reservations,details,depends,checker,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (item['document_id'], item['version_id'], check_id, item['id'], domain, state, evidence, json.dumps(list(reservations), ensure_ascii=False), json.dumps(details or {}, ensure_ascii=False), depends, checker, _now()))
    if not before or before['state'] != state:
        desk.audit('document_controle5689', {'document': item['document_id'][:40], 'controle': check_id, 'etat': state})


def preuve_stockage(desk, item, info, size):
    schema(desk)
    _set(desk, item, 'storage.file', 'passed', 'sha256:' + item['sha256'], details={'etag': str(info.get('etag') or ''), 'taille': int(size), 'reouverture': True, 'relu_le': _now()})


def preuve_stockage_ancienne(desk, item, evidence):
    """L'ancien indicateur mesurait une relecture du fichier : il ne vaut que pour cela, et le dit."""
    schema(desk)
    if desk.db.execute("SELECT 1 FROM document_checks5689 WHERE document_id=? AND version_id=? AND check_id='storage.file' AND state='passed'", (item['document_id'], item['version_id'])).fetchone():
        return
    _set(desk, item, 'storage.file', 'passed', 'ancien:' + str(evidence)[:120], ['Repris d’un ancien indicateur : preuve de stockage, rien de plus.'], checker='reprise')


def relecture_humaine(desk, item):
    """Ne découle jamais d'un succès technique : uniquement d'une décision de cabinet enregistrée sur cette version."""
    schema(desk)
    rows = desk.db.execute("SELECT decision, author, at, note FROM prepared_reviews5689 WHERE document_id=? AND version_id=? AND scope='cabinet' AND decision IN ('relu','valide','modifications') ORDER BY at DESC", (item['document_id'], item['version_id'])).fetchall()
    if not rows:
        return
    last = rows[0]
    _set(desk, item, 'human.review', 'passed' if last['decision'] != 'modifications' else 'partial', 'relecture:%s:%s' % (last['author'], last['at']), [last['note']] if last['note'] else [], {'decision': last['decision'], 'auteur': last['author']}, checker='humain')
    desk.db.commit()


def invalider(desk, document_id, version_id, cause):
    """Seules les preuves qui dépendent de ce qui a changé repartent à zéro."""
    schema(desk)
    if cause not in ('stockage', 'sources', 'contenu'):
        raise Stop('cause_invalidation_inconnue')
    cur = desk.db.execute("UPDATE document_checks5689 SET state='pending', evidence_ref=NULL, checked_at=? WHERE document_id=? AND version_id=? AND depends=? AND depends<>'relecture'", (_now(), document_id, version_id, cause))
    desk.db.commit()
    return cur.rowcount


# ------------------------------------------------------------------------------------------------ lecture du document
def _texte(p):
    out = []
    for node in p.iter():
        if node.tag == W + 't':
            out.append(node.text or '')
        elif node.tag == W + 'tab':
            out.append(' ')
    return ''.join(out)


def paragraphes(raw):
    """Texte des paragraphes du corps, tableaux compris ; en-têtes et pieds restent hors du fond du document."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            root = ET.fromstring(z.read('word/document.xml'))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        raise Stop('document_office_non_lisible') from None
    return [t for t in (_texte(p) for p in root.iter(W + 'p')) if t.strip()]


def archive(raw):
    """Intégrité : ZIP sain, parties XML lisibles, types déclarés, liens internes présents."""
    problems, external = [], 0
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        broken = z.testzip()
    except zipfile.BadZipFile:
        return ['archive illisible'], 0
    if broken:
        return ['partie endommagée : ' + broken], 0
    names = set(z.namelist())
    for needed in ('[Content_Types].xml', 'word/document.xml', '_rels/.rels'):
        if needed not in names:
            problems.append('partie manquante : ' + needed)
    for name in sorted(names):
        if name.endswith(('.xml', '.rels')):
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError:
                problems.append('XML invalide : ' + name)
                continue
            if name.endswith('.rels'):
                base = posixpath.dirname(posixpath.dirname(name))
                for rel in root.iter(REL + 'Relationship'):
                    if rel.get('TargetMode') == 'External':
                        external += 1
                        continue
                    target = rel.get('Target', '')
                    resolved = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(base, target))
                    if resolved not in names:
                        problems.append('lien interne cassé : ' + resolved)
    return problems, external


def structure(raw):
    """Ce qui fait tenir la mise en page : styles, numérotation, images, en-têtes et renvois de champ. Compter, puis vérifier que chaque référence a sa cible."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = set(z.namelist())
    xml = z.read('word/document.xml').decode('utf-8', 'replace')
    root = ET.fromstring(xml)
    problems = []
    styles = set()
    if 'word/styles.xml' in names:
        styles = {s.get(W + 'styleId') for s in ET.fromstring(z.read('word/styles.xml')).iter(W + 'style')}
    used = {s.get(W + 'val') for s in root.iter(W + 'pStyle')} | {s.get(W + 'val') for s in root.iter(W + 'rStyle')}
    if used - styles:
        problems.append('style absent : ' + ', '.join(sorted(x for x in used - styles if x)[:5]))
    nums = {n.get(W + 'val') for n in root.iter(W + 'numId') if n.get(W + 'val') != '0'}
    known = set()
    if 'word/numbering.xml' in names:
        known = {n.get(W + 'numId') for n in ET.fromstring(z.read('word/numbering.xml')).iter(W + 'num')}
    if nums - known:
        problems.append('numérotation absente : ' + ', '.join(sorted(nums - known)[:5]))
    rels = set()
    if 'word/_rels/document.xml.rels' in names:
        rels = {r.get('Id') for r in ET.fromstring(z.read('word/_rels/document.xml.rels')).iter(REL + 'Relationship')}
    refs = set(re.findall(r'r:(?:embed|id|link)="([^"]+)"', xml))
    if refs - rels:
        problems.append('image ou en-tête sans relation : ' + ', '.join(sorted(refs - rels)[:5]))
    bookmarks = {b.get(W + 'name') for b in root.iter(W + 'bookmarkStart')}
    fields = [t.text or '' for t in root.iter(W + 'instrText')] + [f.get(W + 'instr') or '' for f in root.iter(W + 'fldSimple')]
    targets = set()
    for field in fields:
        targets |= set(re.findall(r'\b(?:REF|PAGEREF)\s+([A-Za-z_][\w.]*)', field))
    targets |= {a.get(W + 'anchor') for a in root.iter(W + 'hyperlink') if a.get(W + 'anchor')}
    if targets - bookmarks:
        problems.append('renvoi sans signet : ' + ', '.join(sorted(targets - bookmarks)[:5]))
    begin = sum(1 for f in root.iter(W + 'fldChar') if f.get(W + 'fldCharType') == 'begin')
    end = sum(1 for f in root.iter(W + 'fldChar') if f.get(W + 'fldCharType') == 'end')
    if begin != end:
        problems.append('champ Word mal fermé')
    return problems, {'tableaux': sum(1 for _ in root.iter(W + 'tbl')), 'images': sum(1 for _ in root.iter(W + 'drawing')) + sum(1 for _ in root.iter(W + 'pict')), 'en_tetes': sum(1 for n in names if n.startswith('word/header')),
                      'pieds': sum(1 for n in names if n.startswith('word/footer')), 'numerotations': len(nums), 'renvois': len(targets), 'signets': len(bookmarks)}


def champs(lignes):
    found = []
    for line in lignes:
        for pattern in HOLES:
            found += [m.group(0)[:40] for m in re.finditer(pattern, line, re.I)]
    return found


def renvois(lignes):
    """« article 12 ci-dessus » doit désigner un titre « Article 12 ». Sans titres numérotés, on ne peut pas conclure."""
    titres = {int(m.group(1)) for line in lignes for m in [TITRE.match(line)] if m}
    cites = sorted({int(n) for line in lignes for n in RENVOI.findall(line)})
    return cites, titres, [n for n in cites if titres and n not in titres]


def _decimal(text):
    try:
        return Decimal(re.sub(r'[\s.  ]', '', text).replace(',', '.'))
    except InvalidOperation:
        return None


def montants(lignes):
    found = []
    for line in lignes:
        for m in MONTANT.finditer(line):
            value = _decimal(m.group(1))
            if value is not None:
                found.append(value)
    return found


def _pieces_du_dossier(desk, matter):
    try:
        return desk.db.execute("SELECT COUNT(*) FROM exhibit_registry_v240 WHERE matter=? AND status='present'", (matter,)).fetchone()[0]
    except sqlite3.Error:
        return 0


def _parties(desk, matter):
    try:
        return [r[0] for r in desk.db.execute("SELECT display_name FROM d51_parties WHERE matter=? AND status='active'", (matter,))]
    except sqlite3.Error:
        return []


def _noms_proches(lignes, parties):
    """Une partie mal orthographiée : mot en capitales très proche d'un nom du dossier, sans lui être égal."""
    names = {w for p in parties for w in re.findall(r'[A-Za-zÀ-ÿ]{4,}', fold(p).upper())}
    seen = {fold(w).upper() for line in lignes for w in re.findall(r'\b[A-ZÀ-Ý]{4,}\b', line)}
    return sorted({'%s (dossier : %s)' % (w, n) for w in seen - names for n in names if SequenceMatcher(None, w, n).ratio() >= 0.8})


def _citations(desk, item, lignes):
    """Chaque citation reçoit un statut propre ; seule une source réellement consultée peut sortir de « Non vérifiée », et encore sans prouver le soutien de l'affirmation."""
    prov = json.loads(item.get('provenance') or '{}')
    consulted = [s for s in prov.get('sources_juridiques') or [] if isinstance(s, dict) and s.get('statut') == 'verifiee' and s.get('reference')]
    flagged = [part.strip().rstrip('.;,') for x in prov.get('references_a_verifier') or [] for part in str(x).splitlines() if part.strip()]      # une liste lue d'un seul tenant peut couvrir plusieurs lignes
    found = []
    for line in lignes:      # même relevé que le vérificateur de citations (articles sans lettre, numéros du Conseil d'État), complété par le motif propre à ce contrôle
        known = [re.sub(r'\s+', ' ', r['raw']).strip().rstrip('.;,')[:160] for r in refs470.extract(line) if str(r.get('raw') or '').strip()]
        found += known + [c for c in (re.sub(r'\s+', ' ', m.group(0)).strip()[:160] for m in CITATION.finditer(line)) if not any(fold(c) in fold(k) or fold(k) in fold(c) for k in known)]
    rows = []
    for text in dict.fromkeys(found + flagged):
        hit = next((s for s in consulted if _meme_reference(text, s)), None)
        rows.append({'reference': text, 'statut': 'Source consultée' if hit else NON_VERIFIEE, 'version_consultee': str((hit or {}).get('date') or ''), 'emplacement': str((hit or {}).get('titre') or '')[:160]})
    return rows


def _meme_reference(texte, source):
    """Même référence, pas une référence qui en contient une autre : L. 145-4 n'est pas L. 145-41, et « code de commerce » ne couvre aucun article. Comparaison par identité (code et numéro, numéro officiel) ; à défaut, texte identique."""
    from .juridique5649 import unsourced_references
    if not refs470.extract(texte):
        return fold(texte) == fold(str(source.get('reference') or ''))
    return not unsourced_references(texte, {'sources': [source]})


# ------------------------------------------------------------------------------------------------ contrôles
def controler(desk, item, raw=None, client=None, attendus=None):
    """Exécute les contrôles automatiques de cette version et rend le contrat DocumentVerification. Rien n'est écrit dans le document."""
    schema(desk)
    if raw is None and item.get('path'):
        from .office440 import dav_client
        client = client or dav_client(desk)
        info = client.stat(item['path'])
        raw = client.download({**info, 'path': item['path']})
        if item.get('sha256') and hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise Stop('version_perimee')
    is_docx = PurePosixPath(item.get('path') or '').suffix.lower() == '.docx' and raw is not None
    lignes = []
    if is_docx:
        problems, external = archive(raw)
        _set(desk, item, 'format.archive', 'failed' if problems else 'passed', 'contrôle zip et XML : ' + (item.get('sha256') or '')[:16], problems[:10] + (['%d lien(s) externe(s) dans le document.' % external] if external else []))
        if problems:
            _set(desk, item, 'format.structure', 'failed', 'archive illisible', ['Structure non contrôlable : archive invalide.'])
        else:
            trouble, facts = structure(raw)
            _set(desk, item, 'format.structure', 'failed' if trouble else 'passed', 'inventaire structurel', trouble[:10], facts)
            lignes = paragraphes(raw)
        _set(desk, item, 'format.rendu', 'pending', None, ['Rendu (débordements, pages vides, titres isolés) non contrôlé ici : il exige le moteur bureautique qualifié.'])
    elif raw is not None:
        try:
            lignes = raw.decode('utf-8-sig').splitlines() if PurePosixPath(item.get('path') or '').suffix.lower() in ('.txt', '.md') else []
        except UnicodeDecodeError:
            lignes = []
        for check in ('format.archive', 'format.structure'):
            _set(desk, item, check, 'not_applicable', None, [])
    holes = champs(lignes)
    _set(desk, item, 'sources.champs', 'failed' if holes else ('passed' if lignes else 'not_applicable'), 'analyse du texte' if lignes else None, ['Champ non renseigné : ' + h for h in holes[:10]], {'nombre': len(holes)})
    cites, titres, missing = renvois(lignes)
    if not cites:
        _set(desk, item, 'sources.renvois', 'not_applicable' if not lignes else 'passed', 'aucun renvoi numéroté' if lignes else None)
    elif not titres:
        _set(desk, item, 'sources.renvois', 'partial', 'analyse du texte', ['Renvois présents, mais aucun titre numéroté reconnu : à contrôler à la lecture.'])
    else:
        _set(desk, item, 'sources.renvois', 'failed' if missing else 'passed', 'analyse du texte', ['Renvoi sans cible : %d' % n for n in missing[:10]])
    numbers = sorted({int(n) for line in lignes for n in PIECE.findall(line)})
    count = _pieces_du_dossier(desk, item.get('matter_id'))
    if not numbers:
        _set(desk, item, 'sources.pieces', 'not_applicable' if not lignes else 'passed', 'aucune pièce citée' if lignes else None)
    elif not count:
        _set(desk, item, 'sources.pieces', 'partial', 'analyse du texte', ['%d référence(s) de pièces : registre des pièces du dossier vide, non vérifiables.' % len(numbers)], {'cites': numbers[:40]})
    else:
        over = [n for n in numbers if n > count]
        gaps = [n for n in range(1, max(numbers)) if n not in numbers and n <= count]
        _set(desk, item, 'sources.pieces', 'failed' if over else ('partial' if gaps else 'passed'), 'registre des pièces (%d)' % count, ['Pièce citée absente du registre : n° %d' % n for n in over[:10]] + (['Numéros non cités : %s' % ', '.join(map(str, gaps[:10]))] if gaps else []))
    attendus = attendus or {}
    parties = list(attendus.get('parties') or _parties(desk, item.get('matter_id')))
    reservations, failed = [], False
    near = _noms_proches(lignes, parties) if parties else []
    if near:
        failed = True
        reservations += ['Nom proche d’une partie, à vérifier : ' + n for n in near[:6]]
    expected_amounts = {v for v in (_decimal(str(a)) for a in attendus.get('montants') or []) if v is not None}
    amounts = montants(lignes)
    if expected_amounts:
        unknown = sorted({a for a in amounts if a not in expected_amounts})
        missing_amounts = sorted(expected_amounts - set(amounts))
        if unknown:
            failed = True
            reservations += ['Montant absent des données contrôlées : %s' % a for a in unknown[:6]]
        reservations += ['Montant attendu non retrouvé : %s' % a for a in missing_amounts[:6]]
    elif amounts:
        reservations.append('%d montant(s) sans donnée de référence : à contrôler.' % len(amounts))
    if not lignes:
        _set(desk, item, 'sources.parties_montants', 'not_applicable')
    else:
        _set(desk, item, 'sources.parties_montants', 'failed' if failed else ('partial' if reservations or not parties else 'passed'), 'données du dossier', reservations + ([] if parties else ['Aucune partie enregistrée dans le dossier : cohérence non vérifiable.']))
    citations = _citations(desk, item, lignes)
    unverified = [c for c in citations if c['statut'] == NON_VERIFIEE]
    if not citations:
        _set(desk, item, 'sources.citations', 'not_applicable' if not lignes else 'passed', 'aucune citation relevée' if lignes else None, [], {'citations': []})
    else:   # même source consultée : l'affirmation reste à apprécier en contexte, donc jamais « réussi » pour une citation
        _set(desk, item, 'sources.citations', 'partial', 'relevé des citations', ['%s : %s' % (NON_VERIFIEE, c['reference']) for c in unverified[:10]] + ['Le soutien de chaque affirmation par sa source reste à apprécier à la relecture.'], {'citations': citations[:40]})
    desk.db.commit()
    return vue(desk, item)


def vue(desk, item):
    """Contrat DocumentVerification. ``ready_for_review`` et ``human_review`` sont deux réponses différentes, et aucune ne dit que le document est juridiquement valable."""
    schema(desk)
    rows = {r['check_id']: r for r in desk.db.execute('SELECT * FROM document_checks5689 WHERE document_id=? AND version_id=?', (item['document_id'], item['version_id']))}
    docx = PurePosixPath(item.get('path') or '').suffix.lower() == '.docx'
    required = list(REQUIRED_DOCX if docx else REQUIRED_OTHER)
    checks = []
    for check_id, (domain, label, _) in CHECKS.items():
        row = rows.get(check_id)
        checks.append({'check_id': check_id, 'domain': domain, 'label': label, 'state': row['state'] if row else 'pending', 'state_label': STATES[row['state'] if row else 'pending'], 'evidence_ref': row['evidence_ref'] if row else None,
                       'reservations': json.loads(row['reservations']) if row else [], 'details': json.loads(row['details']) if row else {}})
    states = {c['check_id']: c['state'] for c in checks}
    ready = item.get('production_state') == 'verified' and states['storage.file'] == 'passed' and all(states[c] != 'pending' for c in required)
    review = {'reviewed': 'reviewed', 'changes_requested': 'changes_requested'}.get(item.get('review_state'), 'not_reviewed')
    return {'document_id': item['document_id'], 'version_id': item['version_id'], 'sha256': item.get('sha256') or '', 'checks': checks, 'required_check_ids': required, 'ready_for_review': ready, 'human_review': review}


def travail(desk, args, client=None):
    """Travail « controle_livrable5689 » : contrôles d'une version précise, hors de l'affichage."""
    from . import livrables5689 as liv, pause5629
    item = liv.charger(desk, args.get('document_id'), version_id=args.get('version_id'))
    pause5629.checkpoint(desk)
    return {'ok': True, 'verification': controler(desk, item, client=client)}

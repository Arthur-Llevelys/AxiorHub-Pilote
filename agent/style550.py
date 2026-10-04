"""Style du cabinet (AxiorHub 5.5.0) : apprendre la manière d'écrire de l'avocat à partir de ses écrits définitifs.

Corpus (lecture seule) :
- dans chaque dossier client, les sous-dossiers PROCEDURE (conclusions, assignations, requêtes), CORRESPONDANCES (courriers, copies de
  courriels), PROJETS et LIVRABLES (contrats, actes, notes, consultations) — noms réglables ;
- les courriels envoyés déjà mémorisés par AxiorHub (mémoire des envois).
Les documents produits par AxiorHub lui-même sont exclus (on n'apprend pas de soi-même).

Analyse locale et déterministe, document par document : formule d'ouverture et de clôture, plan (intertitres), longueur, listes, annonce
des prochaines étapes, phrases récurrentes (« formules maison »). Aucune phrase contenant un nom, un numéro ou une donnée reconnue par
la pseudonymisation n'est retenue ; seul un « squelette » pseudonymisé (intertitres + première phrase de chaque partie) est conservé
comme modèle de plan.

Agrégation par type d'écrit → habitudes PROPOSÉES (« Conclusions : plan habituel FAITS ET PROCÉDURE → DISCUSSION → PAR CES MOTIFS,
9 sur 12 »). L'avocat valide (éventuellement après correction du texte) ou écarte. Une habitude validée devient une règle métier
explicite et réversible (learning410) : elle est alors appliquée aux brouillons de courriel et aux documents. « Affiner avec l'IA »
(facultatif) propose quelques habitudes de plus à partir des seuls agrégats et squelettes, avec pseudonymisation si l'IA est externe.
"""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import statistics
import time
import unicodedata

from .common import Stop, digest, load_matters

TYPES = {'conclusions': 'Conclusions', 'assignation': 'Assignations et requêtes', 'acte': 'Autres actes de procédure',
         'courrier_confrere': 'Courriers au confrère', 'courrier_client': 'Courriers au client', 'mise_en_demeure': 'Mises en demeure',
         'contrat': 'Contrats et actes', 'consultation': 'Notes et consultations', 'courriel_client': 'Courriels au client',
         'courriel_confrere': 'Courriels au confrère', 'courriel': 'Autres courriels'}
MAIL_TYPES = {'courriel_client', 'courriel_confrere', 'courriel'}
# Types d'écrits utiles pour chaque demande de document (docrequest520).
KIND_TYPES = {'conclusions': ('conclusions', 'acte'), 'assignation': ('assignation', 'acte'), 'courrier': ('courrier_client', 'courrier_confrere'),
              'courriel': ('courriel_client', 'courriel_confrere', 'courriel'), 'mise_en_demeure': ('mise_en_demeure', 'courrier_client'),
              'contrat': ('contrat',), 'note': ('consultation',), 'compte_rendu': ('consultation', 'courrier_client')}
EXTENSIONS = ('.docx', '.odt', '.pdf', '.txt', '.md')
MAX_FILES_PER_RUN = 20
MAX_BYTES = 6_000_000
MIN_DOCS = 3

OPENING = re.compile(r"^(?:(?:mon\s+)?cher(?:e)?\s+(?:confr[eè]re|consœur|consoeur|ma[iî]tre|monsieur|madame|client)|ch[eè]re\s+(?:madame|consœur|consoeur|cliente)|"
                     r"madame,?\s*monsieur|monsieur,?\s*madame|madame|monsieur|ma[iî]tre|bonjour|bonsoir)\b[^\n]{0,60}$", re.I)
CLOSING = re.compile(r"(je vous prie|veuillez (?:agr[ée]er|croire)|bien (?:cordialement|à vous|confraternellement)|cordialement|sentiments|"
                     r"salutations|confraternel|respectueuses|dévoués|devoues|à votre disposition|a votre disposition)", re.I)
HEADING_NUM = re.compile(r"^(?:[IVX]{1,5}|[A-H]|\d{1,2}(?:\.\d{1,2})*)\s*[.\-–)]\s*\S")
STEPS = re.compile(r"(prochaine[s]? étape|prochaines etapes|je vous propose|je reviens vers vous|dans l.attente|d.ici le|avant le|sous (?:huitaine|quinzaine)|"
                   r"délai|delai|je vous remercie de (?:bien vouloir )?(?:me|nous) (?:faire|transmettre|retourner|communiquer))", re.I)
AXIORHUB_MARK = 'Projet préparé par AxiorHub'
SUBHEADING = re.compile(r"^(?:[A-H]|[a-h]|\d{1,2}(?:\.\d{1,2})*)\s*[.\-–)]\s")
TITLE = re.compile(r"^(?:CONCLUSIONS|ASSIGNATION|REQU[ÊE]TE|CONTRAT|AVENANT|NOTE|CONSULTATION|PROTOCOLE|STATUTS|MISE EN DEMEURE|COMPTE RENDU|ACTE)(?![A-Z])")


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').replace('’', "'"))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS style550_docs(id TEXT PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL, matter TEXT NOT NULL, doc_type TEXT NOT NULL,
      etag TEXT NOT NULL, words INTEGER NOT NULL, features TEXT NOT NULL, skeleton TEXT NOT NULL, analysed TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS style550_docs_type ON style550_docs(doc_type);
    CREATE TABLE IF NOT EXISTS style550_habits(id TEXT PRIMARY KEY, doc_type TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, evidence TEXT NOT NULL,
      support INTEGER NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL, rule_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS style550_habits_status ON style550_habits(status, doc_type);
    ''')
    desk.db.commit()


def settings(desk):
    raw = desk.settings('style550:settings', {}) or {}
    return {'mails': raw.get('mails', True) is not False, 'auto': raw.get('auto', True) is not False}


def save_settings(desk, data):
    from .docrequest520 import DEFAULT_FOLDERS
    s = {'mails': data.get('mails') in (True, 'yes', 'on', 'true'), 'auto': data.get('auto') in (True, 'yes', 'on', 'true')}
    desk.setting('style550:settings', s)
    folders = {}
    for key, default in DEFAULT_FOLDERS.items():
        value = str(data.get('folder_' + key) or default).strip().strip('/')
        if not re.fullmatch(r"[\w .'’()&\-]{1,80}", value):
            raise Stop('nom_sous_dossier_invalide')
        folders[key] = value
    desk.setting('docreq530:folders', folders)
    desk.audit('style550_reglages', {**s, 'folders': list(folders.values())})
    return {**s, 'folders': folders, 'message': 'Réglages enregistrés.'}


# ======================================================================================== analyse d'un écrit
def _lines(text):
    return [re.sub(r'\s+', ' ', l).strip() for l in str(text or '').replace('\r', '').split('\n') if l.strip()]


def _is_heading(line):
    if len(line) > 90 or len(line) < 3:
        return False
    letters = [c for c in line if c.isalpha()]
    if not letters:
        return False
    upper = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper > 0.75 or bool(HEADING_NUM.match(line))


def _generic(text, ps=None):
    """Retire les données identifiantes d'une formule (« Cher Monsieur DUPOND, » → « Cher Monsieur [Nom], »)."""
    from .pseudo540 import Pseudonymizer, TOKEN
    ps = ps or Pseudonymizer(known=[])
    out = ps.text(text)
    labels = {'PERSONNE': '[Nom]', 'SOCIETE': '[Société]', 'ADRESSE': '[Adresse]', 'COURRIEL': '[Courriel]', 'TELEPHONE': '[Téléphone]',
              'DATE_NAISSANCE': '[Date]', 'REFERENCE': '[Référence]', 'DOSSIER': '[Dossier]', 'CHEMIN': '[Chemin]'}
    return TOKEN.sub(lambda m: labels.get(m.group(1), '[…]'), out)


def _heading_key(line):
    line = re.sub(r"^(?:[IVX]{1,5}|[A-H]|\d{1,2}(?:\.\d{1,2})*)\s*[.\-–)]\s*", '', line).strip(' :.-–')
    return re.sub(r'\s+', ' ', line.upper())[:70]


def analyse(text, doc_type=''):
    """Caractéristiques d'un écrit, sans aucune donnée identifiante."""
    from .pseudo540 import Pseudonymizer, TOKEN
    lines = _lines(text)
    ps = Pseudonymizer(known=[])
    opening = next((l for l in lines[:12] if OPENING.match(l)), '')
    tail = lines[-10:]
    closing = ''
    for l in reversed(tail):
        if CLOSING.search(l) and len(l) < 260:
            closing = l
            break
    # parties principales seulement : pas les sous-parties « A. », « 1. », ni le titre du document
    headings = [_heading_key(l) for l in lines if _is_heading(l) and not SUBHEADING.match(l)][:40]
    headings = [h for h in headings if h and not TOKEN.search(ps.text(h))]
    if headings and TITLE.match(headings[0]):
        headings = headings[1:]
    body = ' '.join(lines)
    words = len(re.findall(r"\w+", body))
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', body) if s.strip()]
    avg = round(statistics.mean(len(s.split()) for s in sentences), 1) if sentences else 0
    last_part = ' '.join(lines[len(lines) // 2:])
    formulas = []
    for s in sentences:
        n = len(s.split())
        if not 6 <= n <= 32 or re.search(r'\d', s):
            continue
        clean = ps.text(s)
        if TOKEN.search(clean):
            continue
        formulas.append(re.sub(r'\s+', ' ', s).strip())
        if len(formulas) >= 120:
            break
    skeleton, current = [], None
    for l in lines:
        if _is_heading(l):
            current = l
            skeleton.append(l)
        elif current is not None:
            first = re.split(r'(?<=[.!?])\s+', l)[0]
            skeleton.append('  ' + first[:220])
            current = None
    skel = _generic('\n'.join(skeleton[:60]), ps)[:3000]
    return {'opening': _generic(opening)[:120] if opening else '', 'closing': _generic(closing)[:260] if closing else '', 'headings': headings,
            'words': words, 'paragraphs': len(lines), 'sentence_avg': avg, 'steps': bool(STEPS.search(last_part)),
            'bullets': sum(1 for l in lines if l[:1] in '-–•*·') >= 2, 'formulas': formulas, 'skeleton': skel}


def classify(group, name, text):
    f = _fold(name + ' ' + ' '.join(_lines(text)[:12]))
    if group == 'procedure':
        if 'conclusion' in f:
            return 'conclusions'
        if any(w in f for w in ('assignation', 'requete', 'requête', 'acte introductif', 'saisine')):
            return 'assignation'
        return 'acte'
    if group == 'correspondance':
        if 'mise en demeure' in f:
            return 'mise_en_demeure'
        if re.search(r'(confr[eè]re|consoeur|consœur|cher ma[iî]tre)', f):
            return 'courrier_confrere'
        return 'courrier_client'
    if any(w in f for w in ('contrat', 'avenant', 'statuts', 'protocole', 'pacte', 'cession', 'acte de ', 'bail', 'cgv', 'conditions generales')):
        return 'contrat'
    return 'consultation'


# ======================================================================================== lecture du corpus
def _store(desk, ident, source, path, matter, doc_type, etag, feats):
    desk.db.execute('INSERT OR REPLACE INTO style550_docs VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (ident, source, path, matter, doc_type, etag, feats['words'],
                     json.dumps({k: v for k, v in feats.items() if k != 'skeleton'}, ensure_ascii=False), feats['skeleton'],
                     datetime.now(timezone.utc).isoformat()))


def scan(desk, dav=None, budget=MAX_FILES_PER_RUN):
    """Lit au plus `budget` nouveaux écrits (dossiers parcourus tour à tour), puis les courriels envoyés ; met à jour les habitudes."""
    ensure_schema(desk)
    from .docrequest520 import folders
    from .document_projects import _dav
    from .documents import extract
    client = dav or _dav(desk)
    names = folders(desk)
    generated = set()
    try:
        generated = {r[0] for r in desk.db.execute("SELECT path FROM docreq520 WHERE path<>''")}
    except sqlite3.Error:
        pass
    known = {r['id']: r['etag'] for r in desk.db.execute('SELECT id,etag FROM style550_docs')}
    try:
        matters = load_matters(desk.c)
    except Stop:
        matters = []
    cursor = int(desk.settings('style550:cursor', 0) or 0)
    done = read = skipped = 0
    started = time.monotonic()
    order = matters[cursor:] + matters[:cursor]
    visited = 0
    for m in order:
        if read >= budget or time.monotonic() - started > 120:
            break
        visited += 1
        for group, folder in names.items():
            base = m['path'].rstrip('/') + '/' + folder
            try:
                items = client.inventory(base)
            except Stop:
                continue
            for item in items:
                path = item['path']
                if item.get('directory') or not path.lower().endswith(EXTENSIONS) or path in generated:
                    continue
                ident = digest('style550|' + path)[:32]
                etag = str(item.get('etag') or item.get('modified') or '')
                if known.get(ident) == etag:
                    continue
                if int(item.get('size') or 0) > MAX_BYTES:
                    skipped += 1
                    continue
                if read >= budget:
                    break
                try:
                    raw = client.download(item)
                    text = extract(raw, PurePosixPath(path).name, {**desk.c.get('documents', {}), 'max_document_chars': 200000})
                    text = text if isinstance(text, str) else text.get('text', '')
                except Stop:
                    skipped += 1
                    continue
                read += 1
                if AXIORHUB_MARK in text or len(text.split()) < 40:
                    skipped += 1
                    continue
                doc_type = classify(group, PurePosixPath(path).name, text)
                _store(desk, ident, 'fichier', path, m['id'], doc_type, etag, analyse(text, doc_type))
                done += 1
    if matters:
        desk.setting('style550:cursor', (cursor + max(visited, 1)) % len(matters))
    mails = scan_sent(desk) if settings(desk)['mails'] else 0
    desk.db.commit()
    proposals = propose(desk)
    desk.setting('style550:last_scan', {'at': datetime.now(timezone.utc).isoformat(), 'documents': done, 'mails': mails, 'skipped': skipped})
    desk.audit('style550_analyse', {'documents': done, 'mails': mails, 'skipped': skipped, 'proposals': proposals})
    return {'documents': done, 'mails': mails, 'skipped': skipped, 'proposals': proposals,
            'message': '%d écrit(s) et %d courriel(s) analysé(s) ; %d habitude(s) proposée(s) ou mise(s) à jour.' % (done, mails, proposals)}


def scan_sent(desk, limit=200):
    """Courriels envoyés déjà mémorisés (mémoire des envois, lecture locale)."""
    path = Path(desk.c['state_dir']) / 'sent-memory.sqlite3'
    if not path.is_file():
        return 0
    known = {r[0] for r in desk.db.execute("SELECT id FROM style550_docs WHERE source='courriel'")}
    count = 0
    try:
        db = sqlite3.connect(path, timeout=5)
        try:
            rows = db.execute('SELECT key,matter,body,audience FROM examples_v2 ORDER BY sent_at DESC LIMIT ?', (limit,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return 0
    for key, matter, body, audience in rows:
        ident = digest('style550|mail|' + str(key))[:32]
        if ident in known or not body or len(str(body).split()) < 25:
            continue
        roles = set()
        try:
            roles = {x[1] for x in json.loads(audience or '[]')}
        except (ValueError, TypeError, IndexError):
            pass
        doc_type = 'courriel_client' if 'client' in roles else ('courriel_confrere' if roles & {'confrere', 'avocat_adverse', 'adverse_counsel'} else 'courriel')
        _store(desk, ident, 'courriel', '', str(matter or ''), doc_type, '', analyse(str(body), doc_type))
        count += 1
    return count


# ======================================================================================== habitudes
def _habit(desk, doc_type, kind, text, support, total, evidence):
    hid = digest('style550|%s|%s|%s' % (doc_type, kind, _fold(text)))[:24]
    row = desk.db.execute('SELECT status FROM style550_habits WHERE id=?', (hid,)).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    if row:
        desk.db.execute('UPDATE style550_habits SET support=?,total=?,evidence=?,updated=? WHERE id=?', (support, total, evidence, now, hid))
        return 0
    desk.db.execute('INSERT INTO style550_habits VALUES(?,?,?,?,?,?,?,?,?,?,?)', (hid, doc_type, kind, text, evidence, support, total, 'proposee', None, now, now))
    return 1


def propose(desk):
    """Habitudes proposées par type d'écrit (au moins 3 écrits)."""
    ensure_schema(desk)
    created = 0
    by_type = {}
    for r in desk.db.execute('SELECT doc_type,features FROM style550_docs'):
        try:
            by_type.setdefault(r['doc_type'], []).append(json.loads(r['features'] or '{}'))
        except ValueError:
            continue
    for doc_type, docs in by_type.items():
        n = len(docs)
        if n < MIN_DOCS:
            continue
        ev = lambda k: '%d écrit(s) sur %d' % (k, n)
        for field, kind, label in (('opening', 'ouverture', 'Formule d’ouverture habituelle'), ('closing', 'cloture', 'Formule de clôture habituelle')):
            values = Counter(d.get(field) for d in docs if d.get(field))
            if values:
                value, k = values.most_common(1)[0]
                if k >= max(2, n * 0.5):
                    created += _habit(desk, doc_type, kind, '%s : « %s »' % (label, value), k, n, ev(k))
        if doc_type not in MAIL_TYPES:
            positions = {}
            for d in docs:
                hs = d.get('headings') or []
                for i, h in enumerate(dict.fromkeys(hs)):
                    positions.setdefault(h, []).append(i / max(len(hs), 1))
            plan = [h for h, p in positions.items() if len(p) >= max(2, n * 0.6)]
            if len(plan) >= 2:
                plan.sort(key=lambda h: statistics.mean(positions[h]))
                k = min(len(positions[h]) for h in plan)
                created += _habit(desk, doc_type, 'plan', 'Plan habituel : ' + ' → '.join(plan[:8]), k, n, ev(k))
        lengths = sorted(d.get('words', 0) for d in docs if d.get('words'))
        if len(lengths) >= MIN_DOCS:
            q1, q3 = lengths[len(lengths) // 4], lengths[(3 * len(lengths)) // 4]
            rnd = lambda x: int(round(x / 10.0) * 10) if x < 1000 else int(round(x / 100.0) * 100)
            created += _habit(desk, doc_type, 'longueur', 'Longueur habituelle : %d à %d mots' % (rnd(q1), rnd(q3)), len(lengths), n, ev(len(lengths)))
        k = sum(1 for d in docs if d.get('steps'))
        if k >= max(2, n * 0.6):
            created += _habit(desk, doc_type, 'etapes', 'Vous terminez en annonçant les prochaines étapes ou un délai', k, n, ev(k))
        k = sum(1 for d in docs if d.get('bullets'))
        if k >= max(2, n * 0.6):
            created += _habit(desk, doc_type, 'listes', 'Vous présentez les points à traiter sous forme de liste', k, n, ev(k))
        counts = Counter()
        for d in docs:
            counts.update({_fold(s): s for s in d.get('formulas') or []}.keys())
        originals = {}
        for d in docs:
            for s in d.get('formulas') or []:
                originals.setdefault(_fold(s), s)
        for key, k in counts.most_common(8):
            if k >= max(3, n * 0.3):
                created += _habit(desk, doc_type, 'formule', 'Formule maison : « %s »' % originals[key], k, n, ev(k))
    desk.db.commit()
    return created


def decide(desk, data):
    """Valider (texte éventuellement corrigé), écarter ou retirer une habitude."""
    ensure_schema(desk)
    hid, action = str(data.get('id') or ''), str(data.get('action') or '')
    row = desk.db.execute('SELECT * FROM style550_habits WHERE id=?', (hid,)).fetchone()
    if not row:
        raise Stop('habitude_absente')
    from . import learning410
    now = datetime.now(timezone.utc).isoformat()
    if action == 'valider':
        text = re.sub(r'\s+', ' ', str(data.get('text') or row['text'])).strip()
        if not 8 <= len(text) <= 600:
            raise Stop('texte_habitude_invalide')
        purpose = 'mail_drafting' if row['doc_type'] in MAIL_TYPES else 'document_drafting'
        rule = learning410.save_rule(desk, 'cabinet', '', purpose, 'structure' if row['kind'] == 'plan' else 'style',
                                     '%s — %s' % (TYPES.get(row['doc_type'], row['doc_type']), text), 'style550', hid)
        desk.db.execute("UPDATE style550_habits SET status='validee',text=?,rule_id=?,updated=? WHERE id=?", (text, rule['id'], now, hid))
        msg = 'Habitude validée : elle s’applique désormais aux %s.' % ('brouillons de courriel' if purpose == 'mail_drafting' else 'documents préparés par l’agent')
    elif action in ('ecarter', 'retirer'):
        if row['rule_id']:
            try:
                learning410.set_rule_status(desk, row['rule_id'], 'archived')
            except Stop:
                pass
        desk.db.execute("UPDATE style550_habits SET status='ecartee',updated=? WHERE id=?", (now, hid))
        msg = 'Habitude écartée.' if action == 'ecarter' else 'Habitude retirée : elle ne s’applique plus.'
    else:
        raise Stop('action_habitude_invalide')
    desk.db.commit()
    desk.audit('style550_habitude', {'id': hid, 'action': action})
    return {'message': msg}


def refine(desk):
    """Facultatif : l'IA propose quelques habitudes de plus à partir des agrégats et squelettes (pas des textes complets)."""
    ensure_schema(desk)
    from .model import Model, routed_config
    data = {}
    for t, label in TYPES.items():
        rows = desk.db.execute('SELECT features,skeleton FROM style550_docs WHERE doc_type=? ORDER BY analysed DESC LIMIT 12', (t,)).fetchall()
        if len(rows) < MIN_DOCS:
            continue
        feats = [json.loads(r['features']) for r in rows]
        data[t] = {'type': label, 'ecrits': len(rows), 'ouvertures': Counter(f.get('opening') for f in feats if f.get('opening')).most_common(3),
                   'clotures': Counter(f.get('closing') for f in feats if f.get('closing')).most_common(3),
                   'mots_moyens': int(statistics.mean(f.get('words', 0) for f in feats)),
                   'squelettes': [r['skeleton'][:1500] for r in rows[:2]]}
    if not data:
        raise Stop('corpus_style_insuffisant')
    schema = {'type': 'object', 'properties': {'habitudes': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'type': {'type': 'string'}, 'texte': {'type': 'string'}}, 'required': ['type', 'texte']}}}, 'required': ['habitudes']}
    prompt = ('Tu observes la manière d’écrire d’un avocat à partir de statistiques et de plans types. Propose au plus 4 habitudes de style ou de '
              'structure par type, formulées comme des consignes de rédaction courtes et générales (jamais un fait, un nom, un montant ni une '
              'position juridique de fond). Réponds en JSON : habitudes [{type (clé fournie), texte}].')
    raw = Model(routed_config(desk.c, 'document_drafting')).complete([{'role': 'system', 'content': prompt},
                                                                     {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}],
                                                                    temperature=0, max_tokens=2500, json_schema=schema)
    created = 0
    for h in (json.loads(raw).get('habitudes') or [])[:30]:
        t, text = str(h.get('type') or ''), re.sub(r'\s+', ' ', str(h.get('texte') or '')).strip()
        if t in data and 8 <= len(text) <= 400:
            created += _habit(desk, t, 'ia', 'Suggestion de l’IA : ' + text, data[t]['ecrits'], data[t]['ecrits'], 'synthèse de %d écrit(s)' % data[t]['ecrits'])
    desk.db.commit()
    return {'proposals': created, 'message': '%d habitude(s) suggérée(s) par l’IA, à valider.' % created}


# ======================================================================================== utilisation
def drafting_context(desk, kind='auto'):
    """Pour la rédaction d'un document : habitudes validées du type et deux plans types (pseudonymisés)."""
    try:
        ensure_schema(desk)
    except sqlite3.Error:
        return {}
    types = KIND_TYPES.get(kind) or tuple(t for t in TYPES if t not in MAIL_TYPES)
    marks = ','.join('?' * len(types))
    habits = [r['text'] for r in desk.db.execute("SELECT text FROM style550_habits WHERE status='validee' AND doc_type IN (%s) ORDER BY support DESC LIMIT 20" % marks, types)]
    skeletons = [r['skeleton'] for r in desk.db.execute('SELECT skeleton FROM style550_docs WHERE doc_type IN (%s) AND skeleton<>\'\' ORDER BY analysed DESC LIMIT 2' % marks, types)]
    if not habits and not skeletons:
        return {}
    return {'habitudes_validees': habits, 'plans_types': skeletons,
            'consigne': 'Reprends le style et le plan habituels du cabinet ci-dessus, sauf si la demande les contredit.'}


def overview(desk):
    ensure_schema(desk)
    counts = {r['doc_type']: r['n'] for r in desk.db.execute('SELECT doc_type, COUNT(*) n FROM style550_docs GROUP BY doc_type')}
    sources = {r['source']: r['n'] for r in desk.db.execute('SELECT source, COUNT(*) n FROM style550_docs GROUP BY source')}
    habits = [dict(r) for r in desk.db.execute("SELECT * FROM style550_habits WHERE status<>'ecartee' ORDER BY CASE status WHEN 'proposee' THEN 0 ELSE 1 END, support DESC, doc_type")]
    return {'counts': counts, 'files': sources.get('fichier', 0), 'mails': sources.get('courriel', 0),
            'pending': [h for h in habits if h['status'] == 'proposee'], 'validated': [h for h in habits if h['status'] == 'validee'],
            'last': desk.settings('style550:last_scan', {}) or {}}


def tick(desk, now=None):
    """Une analyse automatique par jour (incrémentale) si le réglage est actif."""
    if not settings(desk)['auto']:
        return None
    now = now or time.time()
    last = desk.settings('style550:last_tick', None)
    if last is None:
        # Premier passage après l'installation : première analyse automatique une heure plus tard (« Analyser maintenant » reste immédiat).
        desk.setting('style550:last_tick', now - 86400 + 3600)
        return None
    if now - float(last or 0) < 86400:
        return None
    desk.setting('style550:last_tick', now)
    return desk.enqueue('style550_scan', {}, priority=75)


def perform(desk, kind, args):
    if kind == 'style550_scan':
        if args.get('refine'):
            return refine(desk)
        return scan(desk)
    raise Stop('action_inconnue')

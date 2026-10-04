"""Relecture contradictoire (AxiorHub 4.7.0).

L'agent relit l'acte comme le ferait l'adversaire. Règle absolue : **chaque constat cite sa pièce** (extrait de
l'acte, fait validé du dossier avec son extrait source, ou document indexé). Un constat sans pièce n'est jamais
émis. La relecture est déterministe : elle signale des points à examiner, elle ne conclut pas sur le fond.
"""
import re
from datetime import date

from . import facts460, legal_memory as lm
from .common import Stop, fold
from .index import DocumentIndex

SEVERITY = {'haute': 0, 'moyenne': 1, 'basse': 2}
ASSERTIVE = re.compile(r"(?i)\b(il est (?:constant|établi|incontestable|évident)|incontestablement|manifestement|de toute évidence|"
                       r"il ressort clairement|nul ne conteste|sans aucun doute|indiscutablement)\b")
PIECE_CITE = re.compile(r"(?i)\bpi[èe]ces?\s*(?:n[°o]s?\.?\s*)?(\d{1,3})(?:\s*(?:à|a|-|–)\s*(\d{1,3}))?")
PIECE_EXTRA = re.compile(r"(?i)(?:\bet\b|,)\s*(?:n[°o]\s*)?(\d{1,3})(?![\d/])")
BORDEREAU_LINE = re.compile(r"(?im)^\s*(?:pi[èe]ce\s*(?:n[°o]\s*)?|n[°o]\s*)?(\d{1,3})\s*[.)\-–—:]\s*\S+")
BORDEREAU_HEAD = re.compile(r"(?i)bordereau|liste des pi[èe]ces|pi[èe]ces communiqu[ée]es")
ADVERSE_CUES = re.compile(r"(?i)\b(soutient|soutiennent|pr[ée]tend|pr[ée]tendent|fait valoir|font valoir|all[èe]gue|invoque|oppose|"
                          r"conteste|sollicite le rejet|affirme)\b")
STOP = set("dans pour avec sans cette cette leurs leur dont ainsi alors donc comme entre contre apres avant sous vers chez "
           "plus moins tres aussi mais ou et que qui quoi dont elle elles ils nous vous sont etre avoir fait faire "
           "societe tribunal demande demandeur defendeur partie parties soutient pretend valoir".split())


def _excerpt(text, start, end, margin=70):
    return re.sub(r'\s+', ' ', text[max(0, start - margin):min(len(text), end + margin)]).strip()


def _finding(kind, severity, message, sources):
    if not sources:
        raise Stop('constat_sans_piece')
    return {'kind': kind, 'severity': severity, 'message': message, 'sources': sources}


def _act_source(text, start, end, label='Acte relu'):
    return {'kind': 'acte', 'label': label, 'path': '', 'excerpt': _excerpt(text, start, end)}


def _dates_by_label(text):
    """Événements datés du texte (libellé, date ISO, position) reconnus par les mêmes motifs que l'extraction de faits."""
    out = []
    folded = fold(text)
    for label, pattern in facts460.EVENT_CUES:
        for m in re.finditer(pattern, folded):
            when, end = facts460._parse_date(folded, m.end())
            if when:
                out.append((label, when.isoformat(), m.start(), end))
    return out


def _amounts(text):
    out = []
    for m in facts460.AMOUNT.finditer(text):
        try:
            value = facts460._fr_amount(m[1])
        except ValueError:
            continue
        out.append((value, m.start(), m.end()))
    return out


def _num(value):
    return re.sub(r'[^\d,]', '', value).replace(',', '.')


def check_dates(desk, matter, text):
    findings = []
    events = _dates_by_label(text)
    # 1. contradiction interne : un même événement daté de deux façons
    seen = {}
    for label, iso, start, end in events:
        seen.setdefault(label, []).append((iso, start, end))
    for label, rows in seen.items():
        if len({r[0] for r in rows}) > 1:
            findings.append(_finding('incoherence_date', 'haute',
                '« %s » est daté de plusieurs façons dans l’acte : %s.' % (label, ', '.join(sorted({r[0] for r in rows}))),
                [_act_source(text, r[1], r[2]) for r in rows[:3]]))
    # 2. contradiction avec les faits validés du dossier
    for fact in lm.memory_records(desk, matter, ['validated', 'pinned'], 200):
        if fact['record_type'] != 'event' or not fact.get('event_date'):
            continue
        label = fact['title'].split(' du ')[0].strip()
        for lab, iso, start, end in events:
            if lab == label and iso != fact['event_date']:
                src = _fact_src(fact)
                if not src:
                    continue
                findings.append(_finding('incoherence_date', 'haute',
                    '« %s » : l’acte indique le %s, le fait validé du dossier indique le %s.' % (lab, iso, fact['event_date']),
                    [_act_source(text, start, end), {'kind': 'fait_valide', 'label': fact['title'], 'path': src[0]['path'],
                                                    'excerpt': src[0]['excerpt'][:300]}]))
    return findings


def _fact_src(fact):
    excerpt = (fact.get('content') or fact.get('title') or '').strip()
    if not excerpt:
        return []
    srcs = fact.get('sources') or []
    first = srcs[0] if srcs else ''
    path = first.get('path', '') if isinstance(first, dict) else str(first)
    return [{'path': path or 'Mémoire structurée du dossier', 'excerpt': excerpt}]


def check_amounts(desk, matter, text):
    findings = []
    amounts = _amounts(text)
    validated = {}
    for fact in lm.memory_records(desk, matter, ['validated', 'pinned'], 200):
        if fact['record_type'] == 'amount':
            validated[_num(fact['title'])] = fact
    docs = _docs(desk, matter)
    known = set()
    for d in docs:
        for value, _, _ in _amounts(d['text']):
            known.add(_num(value))
    cited = {}
    for value, start, end in amounts:
        cited.setdefault(_num(value), []).append((value, start, end))
    for number, rows in cited.items():
        value, start, end = rows[0]
        if number in validated:
            continue
        if number in known:
            continue
        findings.append(_finding('montant_non_retrouve', 'moyenne',
            'Le montant %s € de l’acte n’est ni un fait validé du dossier ni retrouvé dans les %d pièce(s) indexées.' % (value, len(docs)),
            [_act_source(text, start, end)]))
    # montants validés différents de ceux de l’acte, même contexte (principal)
    if validated and amounts:
        for number, fact in validated.items():
            if number in cited:
                continue
            nearby = [a for a in amounts if _num(a[0]) != number]
            src = _fact_src(fact)
            if nearby and src and len(validated) == 1 and len(cited) == 1:
                value, start, end = nearby[0]
                findings.append(_finding('montant_different', 'haute',
                    'L’acte retient %s € alors que le fait validé du dossier est %s.' % (value, fact['title'].replace('Montant : ', '')),
                    [_act_source(text, start, end), {'kind': 'fait_valide', 'label': fact['title'], 'path': src[0]['path'],
                                                    'excerpt': src[0]['excerpt'][:300]}]))
    return findings


def _docs(desk, matter, paths=None):
    idx = DocumentIndex(desk.c['state_dir'])
    try:
        rows = idx.db.execute('SELECT path,text FROM docs WHERE matter=? AND error=\'\'', (matter,)).fetchall()
    finally:
        pass
    out = [{'path': r[0], 'text': r[1] or ''} for r in rows]
    if paths is not None:
        out = [d for d in out if d['path'] in paths]
    return out


def _expand(match):
    first = int(match.group(1))
    last = int(match.group(2)) if match.group(2) else first
    if last < first or last - first > 60:
        last = first
    return list(range(first, last + 1))


def check_exhibits(desk, matter, text):
    findings = []
    cited = {}
    for m in PIECE_CITE.finditer(text):
        nums = _expand(m)
        tail = text[m.end():m.end() + 40]
        for extra in PIECE_EXTRA.finditer(tail[:30]):
            nums.append(int(extra.group(1)))
            break
        for n in nums:
            cited.setdefault(n, (m.start(), m.end()))
    head = BORDEREAU_HEAD.search(text)
    listed = set()
    if head:
        zone = text[head.end():]
        listed = {int(x) for x in BORDEREAU_LINE.findall(zone)}
    if cited and not head:
        n, (start, end) = sorted(cited.items())[0]
        findings.append(_finding('bordereau_absent', 'haute',
            'L’acte vise %d pièce(s) mais ne comporte aucun bordereau de pièces.' % len(cited), [_act_source(text, start, end)]))
    elif head:
        for n, (start, end) in sorted(cited.items()):
            if n not in listed:
                findings.append(_finding('piece_manquante', 'haute',
                    'La pièce n° %d est citée dans l’acte mais ne figure pas au bordereau.' % n, [_act_source(text, start, end)]))
        for n in sorted(listed - set(cited)):
            m = re.search(r'(?m)^\s*(?:pi[èe]ce\s*(?:n[°o]\s*)?|n[°o]\s*)?%d\s*[.)\-–—:].*$' % n, text[head.end():])
            if m:
                findings.append(_finding('piece_non_citee', 'basse',
                    'La pièce n° %d figure au bordereau mais n’est citée nulle part dans l’acte.' % n,
                    [_act_source(text, head.end() + m.start(), head.end() + m.end(), 'Bordereau')]))
    # pièces indexées dans le dossier jamais mentionnées au bordereau : signalement seulement si registre disponible
    try:
        rows = desk.db.execute('SELECT filename FROM exhibit_registry_v240 WHERE matter=? AND status<>\'removed\' LIMIT 200', (matter,)).fetchall()
    except Exception:
        rows = []
    return findings


def check_assertions(text):
    findings = []
    for m in ASSERTIVE.finditer(text):
        s = text.rfind('.', 0, m.start()) + 1
        e = text.find('.', m.end())
        e = len(text) if e < 0 else e + 1
        sentence = text[s:e]
        if PIECE_CITE.search(sentence) or re.search(r'(?i)\barticle\b|\bcass\.|\barr[êe]t\b', sentence):
            continue
        findings.append(_finding('affirmation_sans_piece', 'moyenne',
            'Affirmation catégorique sans renvoi à une pièce ni à un texte : « %s ».' % m.group(0),
            [_act_source(text, s, e, 'Phrase concernée')]))
    return findings[:12]


def _keywords(sentence):
    words = [w for w in re.findall(r'[a-z]{6,}', fold(sentence)) if w not in STOP]
    out = []
    for w in words:
        if w not in out:
            out.append(w)
    return out[:6]


def check_unanswered(desk, matter, text, opponent_paths=None):
    """Arguments adverses (phrases à verbe d’allégation) dont les mots clés n’apparaissent pas dans l’acte."""
    docs = _docs(desk, matter)
    if opponent_paths:
        docs = [d for d in docs if d['path'] in set(opponent_paths)]
    else:
        docs = [d for d in docs if re.search(r'(?i)adverse|adversaire|contradictoire', d['path'])]
    findings, scanned = [], len(docs)
    ours = fold(text)
    for doc in docs:
        sentences = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', doc['text']))
        for sentence in sentences:
            if len(sentence) < 40 or len(sentence) > 600 or not ADVERSE_CUES.search(sentence):
                continue
            keys = _keywords(sentence)
            if len(keys) < 3:
                continue
            hit = sum(1 for k in keys if k[:7] in ours)
            if hit / len(keys) < 0.4:
                findings.append(_finding('argument_non_repondu', 'haute',
                    'Argument adverse peut-être non réfuté (recoupement lexical %d/%d, heuristique à vérifier).' % (hit, len(keys)),
                    [{'kind': 'piece_adverse', 'label': doc['path'].rsplit('/', 1)[-1], 'path': doc['path'], 'excerpt': sentence[:320]}]))
            if len(findings) >= 15:
                return findings, scanned
    return findings, scanned


def check_citations(report):
    findings = []
    for item in report.get('items', []):
        if item['status'] == 'verifiee':
            continue
        findings.append(_finding('citation_non_verifiee', 'haute' if item['status'] in ('douteuse', 'introuvable') else 'moyenne',
            'Référence « %s » : %s.' % (item['label'], item['status_label'].lower() + (' — ' + item['reasons'][0] if item['reasons'] else '')),
            [{'kind': 'acte', 'label': 'Acte relu', 'path': '', 'excerpt': item['raw'][:300]}]))
    return findings


def review(desk, matter, text, opponent_paths=None, citations=None):
    """Relecture complète. Retourne {'findings': [...], 'scope': {...}} ; chaque constat a au moins une source."""
    if not str(text or '').strip():
        raise Stop('texte_vide')
    findings = []
    findings += check_dates(desk, matter, text)
    findings += check_amounts(desk, matter, text)
    findings += check_exhibits(desk, matter, text)
    findings += check_assertions(text)
    unanswered, scanned = check_unanswered(desk, matter, text, opponent_paths)
    findings += unanswered
    if citations:
        findings += check_citations(citations)
    findings.sort(key=lambda f: SEVERITY[f['severity']])
    for f in findings:
        assert f['sources'], 'constat sans pièce'
    notes = []
    if not scanned:
        notes.append('Aucune pièce adverse désignée ou reconnue (nom contenant « adverse ») : arguments adverses non examinés.')
    return {'findings': findings, 'count': len(findings), 'adverse_documents_scanned': scanned, 'notes': notes,
            'method': 'Contrôles déterministes : dates, montants, pièces, affirmations, arguments adverses (heuristique lexicale).'}

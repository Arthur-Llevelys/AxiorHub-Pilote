"""Extraction et normalisation des références juridiques (AxiorHub 4.7.0).

Le module ne contacte aucun service : il lit un texte et en tire des références
structurées (article de code, décision). Seuls ces champs structurés pourront
ensuite être transmis à un service de sources ; jamais le texte.
"""
import hashlib
import re
import unicodedata
from datetime import date

MONTHS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7,
          'aout': 8, 'septembre': 9, 'octobre': 10, 'novembre': 11, 'decembre': 12}

# nom canonique -> alias (repliés : minuscules, sans accents, ponctuation simplifiée)
CODES = {
    'Code civil': ('code civil', 'c. civ', 'c.civ', 'cciv', 'cc'),
    'Code de procédure civile': ('code de procedure civile', 'cpc', 'c. proc. civ', 'c.proc.civ', 'ncpc',
                                 'nouveau code de procedure civile'),
    'Code de commerce': ('code de commerce', 'c. com', 'c.com', 'ccom'),
    'Code du travail': ('code du travail', 'c. trav', 'c.trav', 'ct'),
    'Code de la consommation': ('code de la consommation', 'c. consom', 'c.consom'),
    'Code monétaire et financier': ('code monetaire et financier', 'cmf', 'c. mon. fin', 'comofi'),
    'Code pénal': ('code penal', 'c. pen', 'c.pen', 'cp'),
    'Code de procédure pénale': ('code de procedure penale', 'cpp', 'c. proc. pen'),
    'Code général des impôts': ('code general des impots', 'cgi'),
    'Livre des procédures fiscales': ('livre des procedures fiscales', 'lpf'),
    'Code de la sécurité sociale': ('code de la securite sociale', 'css', 'c. sec. soc'),
    'Code de justice administrative': ('code de justice administrative', 'cja'),
    "Code de l'organisation judiciaire": ("code de l'organisation judiciaire", 'coj'),
    'Code des procédures civiles d\'exécution': ("code des procedures civiles d'execution", 'cpce'),
    'Code de la propriété intellectuelle': ('code de la propriete intellectuelle', 'cpi'),
    "Code de l'urbanisme": ("code de l'urbanisme", 'c. urb'),
    "Code de l'environnement": ("code de l'environnement", 'c. env'),
    'Code rural et de la pêche maritime': ('code rural et de la peche maritime', 'code rural', 'crpm'),
    "Code de la construction et de l'habitation": ("code de la construction et de l'habitation", 'cch'),
    'Code des assurances': ('code des assurances', 'c. ass'),
    'Code général des collectivités territoriales': ('code general des collectivites territoriales', 'cgct'),
    'Code de la santé publique': ('code de la sante publique', 'csp'),
    "Code de l'entrée et du séjour des étrangers et du droit d'asile": (
        "code de l'entree et du sejour des etrangers et du droit d'asile", 'ceseda'),
    'Code de la route': ('code de la route',),
    "Code de l'action sociale et des familles": ("code de l'action sociale et des familles", 'casf'),
    'Code de la propriété des personnes publiques': ('code general de la propriete des personnes publiques', 'cg3p'),
}
SAME_CODE = ('meme code', 'dit code', 'code precite', 'meme texte', 'precite')


def fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c))


def _alias_table():
    table = {}
    for canonical, aliases in CODES.items():
        for alias in aliases:
            table[alias] = canonical
    return table


ALIASES = _alias_table()
# alias triés du plus long au plus court pour une reconnaissance gloutonne
ACCENTS = {'e': '[eéèêë]', 'a': '[aàâä]', 'i': '[iîï]', 'o': '[oôö]', 'u': '[uùûü]', 'c': '[cç]'}


def _accent_re(alias):
    out = ''
    for ch in re.escape(alias).replace(r'\ ', ' ').replace(r"\'", "['’]").replace("'", "['’]"):
        out += ACCENTS.get(ch, ch)
    return out.replace(' ', r'\s+').replace(r'\.', r'\.?\s*')


ALIAS_RE = '|'.join(_accent_re(a) for a in sorted(ALIASES, key=len, reverse=True))

NUM = r'(?:[LRD]\.?\s?)?\d{1,4}(?:[-.]\d{1,4}){0,4}(?:\s?(?:bis|ter|quater|quinquies|sexies|septies))?'
ARTICLE = re.compile(
    r'(?<![A-Za-z])(?:articles?|art\.)\s*(?P<list>' + NUM + r'(?:\s*(?:,|et|ainsi que|ou)\s*(?:(?:du|de)\s+)?' + NUM + r')*)'
    r'(?P<tail>(?:\s*,?\s*(?:alin[ée]as?|al\.)\s*\d+(?:er|e|ème)?)?)'
    r'\s*,?\s*(?:(?:du|de la|de l[\'’]|des|de)\s*)?(?P<code>(?:' + ALIAS_RE + r'|m[eê]me code|dit code|code pr[ée]cit[ée]))?',
    re.I)
NUMBER_ONLY = re.compile(NUM, re.I)

POURVOI = re.compile(r'(?<!\d)(\d{2}-\d{2}[.\s]?\d{3})(?!\d)')
RG = re.compile(r'(?:RG|R\.G\.|r[ée]pertoire g[ée]n[ée]ral)\s*(?:n[°o]\s*)?(\d{2}/\d{3,6})', re.I)
DATE_TEXT = re.compile(r'(?<!\d)(\d{1,2})(?:er)?\s+(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)\s+(\d{4})',
                       re.I)
DATE_NUM = re.compile(r'(?<![\d/])(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?![\d/])')

COURT_RE = re.compile(
    r'(?P<court>Cass(?:ation)?\.?|Cour\s+de\s+cassation|C\.?\s?A\.?\s+(?:de\s+)?[A-ZÉ][\w\-’\']+(?:[ -][A-ZÉ][\w\-’\']+)?|'
    r'Cour\s+d[\'’]appel\s+(?:de\s+)?[A-ZÉ][\w\-’\']+(?:[ -][A-ZÉ][\w\-’\']+)?|CE\b|Conseil\s+d[\'’][ÉE]tat|'
    r'Conseil\s+constitutionnel|CC\b|T\.?\s?com\.?\s+(?:de\s+)?[A-ZÉ][\w\-’\']+|Tribunal\s+de\s+commerce\s+(?:de\s+)?[A-ZÉ][\w\-’\']+|'
    r'TJ\s+(?:de\s+)?[A-ZÉ][\w\-’\']+|Tribunal\s+judiciaire\s+(?:de\s+)?[A-ZÉ][\w\-’\']+|CJUE|CEDH)'
    r'(?P<chamber>[\s,]*(?:chambre\s+(?:civile|commerciale|sociale|criminelle|mixte)|ch\.?\s*(?:civ|com|soc|crim|mixte)\.?|'
    r'civ(?:ile)?\.?\s*[123](?:re|e|ère|ème)?|com(?:merciale)?\.?|soc(?:iale)?\.?|crim(?:inelle)?\.?|ass\.?\s*pl[ée]n(?:i[èe]re)?\.?|'
    r'ch\.?\s*mixte|[123](?:re|e|ère|ème)?\s+(?:ch\.?\s*)?civ(?:ile)?\.?)?)')

CHAMBERS = (
    ('civ1', r'civ(?:ile)?\.?\s*1|1(?:re|ère|er)?\s*(?:ch\.?\s*)?civ|premi[èe]re\s+chambre\s+civile'),
    ('civ2', r'civ(?:ile)?\.?\s*2|2(?:e|ème|nde)?\s*(?:ch\.?\s*)?civ|deuxi[èe]me\s+chambre\s+civile'),
    ('civ3', r'civ(?:ile)?\.?\s*3|3(?:e|ème)?\s*(?:ch\.?\s*)?civ|troisi[èe]me\s+chambre\s+civile'),
    ('com', r'\bcom(?:merciale)?\b|chambre\s+commerciale'),
    ('soc', r'\bsoc(?:iale)?\b|chambre\s+sociale'),
    ('crim', r'\bcrim(?:inelle)?\b|chambre\s+criminelle'),
    ('mixte', r'mixte'),
    ('plen', r'pl[ée]n'),
)
CHAMBER_LABELS = {'civ1': '1re chambre civile', 'civ2': '2e chambre civile', 'civ3': '3e chambre civile',
                  'com': 'chambre commerciale', 'soc': 'chambre sociale', 'crim': 'chambre criminelle',
                  'mixte': 'chambre mixte', 'plen': 'assemblée plénière'}


def canonical_code(text):
    key = re.sub(r'\s+', ' ', fold(text).replace('’', "'")).strip(' .,')
    key = re.sub(r'\.\s*', '. ', key).replace('. ', '.').replace('. ', '.')
    for alias, canonical in ALIASES.items():
        a = re.sub(r'\s+', ' ', alias).replace('. ', '.')
        if key == a or key == a.replace('.', '') or key.replace('.', '') == a.replace('.', ''):
            return canonical
    return ''


def normalize_number(raw):
    """« L. 441-10 » -> « L441-10 » ; « 1240 » -> « 1240 »."""
    value = re.sub(r'\s+', '', str(raw)).replace('.', '')
    m = re.match(r'(?i)^([LRD])?(\d{1,4}(?:-\d{1,4}){0,4}|\d{1,4}(?:\d)?)(bis|ter|quater|quinquies|sexies|septies)?$', value)
    if not m:
        return value
    return (m[1] or '').upper() + m[2] + ('-' + m[3].lower() if m[3] else '')


def _split_numbers(listing):
    out = []
    for part in re.split(r'\s*(?:,|\bet\b|\bainsi que\b|\bou\b)\s*', listing):
        part = re.sub(r'^(?:du|de)\s+', '', part.strip(), flags=re.I)
        if part and NUMBER_ONLY.fullmatch(part):
            out.append(part)
    return out


def parse_date(text):
    """Première date explicite du texte (année obligatoire) ou ''."""
    folded = text
    best = None
    for m in DATE_TEXT.finditer(folded):
        month = MONTHS[fold(m[2])]
        best = _safe_date(m[3], month, m[1], best, m.start())
    for m in DATE_NUM.finditer(folded):
        best = _safe_date(m[3], int(m[2]), m[1], best, m.start())
    return best[0] if best else ''


def _safe_date(year, month, day, best, pos):
    try:
        iso = date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return best
    if best is None or pos < best[1]:
        return (iso, pos)
    return best


def ref_id(ref):
    key = '|'.join(str(ref.get(k, '')) for k in ('kind', 'code', 'number', 'court', 'chamber', 'date', 'pourvoi', 'rg'))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _chamber(text):
    for key, pattern in CHAMBERS:
        if re.search(pattern, text, re.I):
            return key
    return ''


def _court_kind(label):
    f = fold(label)
    if f.startswith('cass') or 'cour de cassation' in f:
        return 'cass'
    if f.startswith('c.a') or f.startswith('ca ') or "cour d'appel" in f or f.startswith('c. a'):
        return 'ca'
    if f.startswith('ce') or "conseil d'etat" in f:
        return 'ce'
    if f.startswith('conseil constitutionnel') or f == 'cc':
        return 'cc'
    if f.startswith('t.com') or f.startswith('t. com') or 'tribunal de commerce' in f:
        return 'tcom'
    if f.startswith('tj') or 'tribunal judiciaire' in f:
        return 'tj'
    if f.startswith('cjue'):
        return 'cjue'
    if f.startswith('cedh'):
        return 'cedh'
    return ''


def extract_articles(text):
    refs = []
    last_code = ''
    quote_span = None   # (début, fin, code) : un article cité dans le texte d'un autre article relève du même code
    for m in ARTICLE.finditer(text):
        numbers = _split_numbers(m['list'])
        if not numbers:
            continue
        raw_code = (m['code'] or '').strip()
        code = ''
        if raw_code:
            if any(s in fold(raw_code) for s in SAME_CODE):
                code = last_code
            else:
                code = canonical_code(raw_code)
        if not code and quote_span and quote_span[0] <= m.start() < quote_span[1]:
            code = quote_span[2]
        if code:
            last_code = code
        # fragment de texte suivant la référence : sert à détecter une citation textuelle
        after = text[m.end():m.end() + 400]
        for number in numbers:
            ref = {'kind': 'article', 'raw': text[m.start():m.end()].strip(), 'start': m.start(), 'end': m.end(),
                   'code': code, 'code_raw': raw_code, 'number': normalize_number(number)}
            quote = re.match(r'\s*(?:(?:dispose|pr[ée]voit|[ée]nonce|stipule|prescrit|pr[ée]cise|d[ée]clare)\s*(?:que)?)?\s*[:,]?\s*[«"“]\s*([^»"”]{12,400})[»"”]', after)
            if quote and len(numbers) == 1:
                ref['quote'] = quote[1].strip()
                if code:
                    quote_span = (m.end() + quote.start(1), m.end() + quote.end(1), code)
            ref['id'] = ref_id(ref)
            refs.append(ref)
    return refs


def extract_decisions(text):
    refs = []
    seen = set()
    for m in COURT_RE.finditer(text):
        window = text[m.start():m.end() + 170]
        # la fenêtre s'arrête au premier point-virgule ou saut de paragraphe
        window = re.split(r';|\n\s*\n|\)', window)[0]
        court_label = m['court'].strip()
        kind = _court_kind(court_label)
        if not kind:
            continue
        chamber = _chamber(m['chamber'] or '') if kind == 'cass' else ''
        if kind == 'cass' and not chamber:
            chamber = _chamber(window[:60])
        d = parse_date(window)
        pourvoi = POURVOI.search(window)
        rg = RG.search(window)
        ce_number = ''
        cc_number = ''
        if kind == 'ce':
            n = re.search(r'n[°o]\s*(\d{5,7})', window)
            ce_number = n[1] if n else ''
        if kind == 'cc':
            n = re.search(r'n[°o]\s*(\d{4}-\d{2,4}\s*[A-Z]{1,4})', window)
            cc_number = re.sub(r'\s+', ' ', n[1]) if n else ''
        has_number = bool(pourvoi or rg or ce_number or cc_number)
        if not (has_number or d):
            continue
        ref = {'kind': 'decision', 'court': kind, 'court_label': court_label, 'chamber': chamber, 'date': d,
               'pourvoi': re.sub(r'\s', '', pourvoi[1]) if pourvoi else '',
               'rg': rg[1] if rg else '', 'ce_number': ce_number, 'cc_number': cc_number,
               'raw': window.strip(), 'start': m.start(), 'end': m.start() + len(window)}
        if ref['pourvoi']:
            ref['pourvoi'] = _norm_pourvoi(ref['pourvoi'])
        ref['id'] = ref_id(ref)
        key = (kind, ref['pourvoi'], ref['rg'], ref['ce_number'], ref['cc_number'], d)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    # numéros de pourvoi isolés (sans juridiction) : « pourvoi n° 21-12.345 »
    covered = {r['pourvoi'] for r in refs if r['pourvoi']}
    for m in POURVOI.finditer(text):
        number = _norm_pourvoi(m[1])
        if number in covered:
            continue
        context = fold(text[max(0, m.start() - 40):m.start()])
        if 'pourvoi' in context or 'n°' in context or 'no ' in context[-6:]:
            ref = {'kind': 'decision', 'court': 'cass', 'court_label': 'Cour de cassation (présumée)', 'chamber': '',
                   'date': parse_date(text[m.end():m.end() + 60]), 'pourvoi': number, 'rg': '', 'ce_number': '',
                   'cc_number': '', 'raw': text[max(0, m.start() - 40):m.end()].strip(),
                   'start': m.start(), 'end': m.end(), 'court_presumed': True}
            ref['id'] = ref_id(ref)
            covered.add(number)
            refs.append(ref)
    return refs


def _norm_pourvoi(value):
    digits = re.sub(r'[^\d]', '', value)
    return '%s-%s.%s' % (digits[:2], digits[2:4], digits[4:7]) if len(digits) >= 7 else value


def extract(text, limit=200):
    """Toutes les références du texte, triées par position, dédoublonnées par identifiant."""
    text = str(text or '')
    refs = extract_articles(text) + extract_decisions(text)
    refs.sort(key=lambda r: r['start'])
    out, seen = [], set()
    for ref in refs:
        if ref['id'] in seen:
            continue
        seen.add(ref['id'])
        out.append(ref)
        if len(out) >= limit:
            break
    return out


def label(ref):
    if ref['kind'] == 'article':
        return 'Article %s %s' % (ref['number'], ('du ' + ref['code']) if ref.get('code') else '(code non précisé)')
    parts = [ref.get('court_label') or ref.get('court', '')]
    if ref.get('chamber'):
        parts.append(CHAMBER_LABELS.get(ref['chamber'], ref['chamber']))
    if ref.get('date'):
        d = date.fromisoformat(ref['date'])
        parts.append('%d/%02d/%d' % (d.day, d.month, d.year))
    number = ref.get('pourvoi') or ref.get('rg') or ref.get('ce_number') or ref.get('cc_number')
    if number:
        parts.append('n° ' + number)
    return ', '.join(p for p in parts if p)

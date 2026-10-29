"""5.6.79 : lecture sans IA de l'en-tête des actes de procédure (conclusions, assignations, jugements, arrêts, ordonnances).

Les modèles open source relèvent mal la juridiction, le numéro de rôle, les qualités des parties et leurs avocats : ces informations
sont pourtant écrites, presque toujours au même endroit et sous la même forme, sur la première page de l'acte. Ce module les lit par
règles, sur les premiers milliers de caractères du texte déjà extrait :

- **juridiction** (« Cour d'appel d'… », « Tribunal judiciaire de … », « Tribunal des activités économiques de … », conseil de
  prud'hommes, juridictions administratives, Cour de cassation) et **chambre** ;
- **numéro de rôle** (« RG n° 24/01234 », « N° RG 2025F00042 ») et numéro **Portalis** ;
- **parties et qualités** (appelant, intimé, demandeur, défendeur, intervenant, requérant) avec leurs **avocats** (« Maître … »,
  « Me … »), que la qualité soit écrite avant ou après le nom.

Rien n'est deviné : un champ introuvable reste vide. Chaque résultat garde l'extrait d'où il vient.
"""
import re
import unicodedata

HEAD_CHARS = 7000
RG_CHARS = 2500
ROLES = (('appelant', r'appelante?s?'), ('intime', r'intim[ée]e?s?'), ('demandeur', r'demande(?:ur|resse)s?'),
         ('defendeur', r'd[ée]fende(?:ur|resse)s?'), ('intervenant', r'intervenante?s?(?:\s+(?:volontaire|forc[ée]e?)s?)?'),
         ('requerant', r'requ[ée]rante?s?'))
ROLE_LABELS = {'appelant': 'appelant', 'intime': 'intimé', 'demandeur': 'demandeur', 'defendeur': 'défendeur', 'intervenant': 'intervenant',
               'requerant': 'requérant'}
_ROLE_LINE = re.compile(r'^\W*(?:en qualit[ée] d[e\']\s*)?(%s)(?:\s+(?:au principal|[àa] titre principal|incidente?s?|et (?:appelante?|intim[ée]e?)s?'
                        r'(?:\s+incidente?s?)?))*\W*$' % '|'.join(p for _, p in ROLES), re.I)
_COURTS = (r'cour d[\'’]\s*appel\s+(?:de|d[\'’]|du)\s*', r'cour administrative d[\'’]\s*appel\s+(?:de|d[\'’]|du)\s*',
           r'tribunal judiciaire\s+(?:de|d[\'’]|du)\s*', r'tribunal des activit[ée]s [ée]conomiques\s+(?:de|d[\'’]|du)\s*',
           r'tribunal de commerce\s+(?:de|d[\'’]|du)\s*', r'tribunal administratif\s+(?:de|d[\'’]|du)\s*',
           r'conseil de prud[\'’]\s*hommes\s+(?:de|d[\'’]|du)\s*', r'tribunal de proximit[ée]\s+(?:de|d[\'’]|du)\s*',
           r'tribunal paritaire des baux ruraux\s+(?:de|d[\'’]|du)\s*', r'tribunal de grande instance\s+(?:de|d[\'’]|du)\s*',
           r'tribunal d[\'’]\s*instance\s+(?:de|d[\'’]|du)\s*')
_COURT = re.compile(r'\b(%s)([A-ZÀ-Ýa-zà-ÿ][A-Za-zÀ-ÿ\'’\- ]{1,40}?)(?=\s*(?:\n|$|[,;:.(]|\s{2,}|\d|\b(?:chambre|p[ôo]le|section|service|'
                    r'r\.?\s?g|n[°o]|audience|ordonnance|jugement|arr[êe]t|conclusions|ch\.)\b))' % '|'.join(_COURTS), re.I)
_SINGLE = re.compile(r'\b(cour de cassation|conseil d[\'’][ée]tat)\b', re.I)
_CHAMBER = re.compile(r'\b((?:\d{1,2}\s*(?:e|[èe]me|[èe]re|er)\s+chambre|chambre\s+(?:\d{1,2}(?:[-\s]\d{1,2})?|[a-zà-ÿ]+(?:\s+[a-zà-ÿ]+)?)'
                      r'|p[ôo]le\s+\d{1,2}\s*[-–]\s*chambre\s+\d{1,2}))', re.I)
_RG = re.compile(r'(?:\bn[°o]?\s*(?:de\s+)?)?\b(?:r\.\s?g\.?|rg|r[ée]pertoire g[ée]n[ée]ral)\s*(?:n[°o]\.?|num[ée]ro)?\s*:?\s*'
                 r'(\d{2}\s?/\s?\d{3,6}|(?:19|20)\d{2}\s?[a-z]\s?\d{3,6}|\d{4,6}\s?/\s?\d{2,4})', re.I)
_PORTALIS = re.compile(r'\bportalis\s*:?\s*([A-Z0-9][A-Z0-9\-]{8,40})', re.I)
_LAWYER = re.compile(r'\b(?:Ma[iî]tre|Me|M[eᵉ]\.)\s+((?:[A-ZÀ-Ý][A-Za-zÀ-ÿ\'’\-]+)(?:\s+(?:de\s+|du\s+|d[\'’])?[A-ZÀ-Ý][A-Za-zÀ-ÿ\'’\-]+){0,3})')
_SEP = re.compile(r'^\W*(pour|contre|entre|et|d[\'’]une part|d[\'’]autre part|en pr[ée]sence de)\W*$', re.I)
_NAME_HINT = re.compile(r'^\W*(la soci[ée]t[ée]|soci[ée]t[ée]|s\.?a\.?s\.?u?|s\.?a\.?r\.?l\.?|s\.?c\.?i\.?|s\.?a\.?|eurl|sasu|sci|snc|selarl|'
                        r'association|syndicat|la commune|l[\'’]?etat|monsieur|madame|mademoiselle|m\.|mme|mlle|me?ssieurs)\b', re.I)
_NOISE = re.compile(r'(avocat|barreau|toque|vestiaire|demeurant|domicili|si[eè]ge|immatricul|rcs|n[ée]e? le|repr[ée]sent|agissant|pris en la personne|'
                    r'@|t[ée]l[ée]?phone|t[ée]l\.|fax|\d{5}\s|rue |avenue|boulevard|chemin|place )', re.I)


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text or '')) if not unicodedata.combining(c)).lower()


def _clean_city(city):
    city = re.sub(r'\s+', ' ', city).strip(' -’\'')
    words = city.split(' ')
    while words and fold(words[-1]) in ('de', 'du', 'des', 'la', 'le', 'les', 'et', 'en'):
        words.pop()
    return ' '.join(words)


def _title(text):
    """« COUR D'APPEL D'ALBERVILLE-SUR-MER » → « Cour d'appel d'Alberville-sur-Mer » ; une écriture déjà en casse mixte est conservée."""
    if text != text.upper():
        return text[:1].upper() + text[1:]
    small = {'de', 'du', 'des', 'la', 'le', 'les', 'en', 'sur', 'et', 'd', 'l', 'a', 'aux', 'au'}

    def word(w, first):
        parts = re.split(r'([\-\'’])', w.lower())
        out = []
        for i, p in enumerate(parts):
            if p in '-\'’' or not p:
                out.append(p)
            elif (p in small and not (first and i == 0)) or (i and parts[i - 1] in '\'’' and p in small):
                out.append(p)
            else:
                out.append(p[:1].upper() + p[1:])
        return ''.join(out)
    words = text.split(' ')
    return ' '.join(word(w, i == 0) for i, w in enumerate(words))


def court(text):
    """(juridiction, extrait) de l'en-tête ; ('', '') si rien de sûr."""
    head = str(text or '')[:HEAD_CHARS]
    best = None
    for m in _COURT.finditer(head):
        city = _clean_city(m.group(2))
        if len(city) < 2:
            continue
        # l'intitulé (« Cour d'appel de », « Tribunal judiciaire de ») garde une seule majuscule ; la ville garde les siennes
        lead = re.sub(r'\s+', ' ', m.group(1)).lower().replace('’ ', '’').replace("' ", "'")
        cand = (m.start(), lead[:1].upper() + lead[1:] + _title(city), head[max(0, m.start() - 20):m.end() + 40])
        if best is None or cand[0] < best[0]:
            best = cand
    single = _SINGLE.search(head)
    if single and (best is None or single.start() < best[0]):
        best = (single.start(), _title(single.group(1)), head[max(0, single.start() - 20):single.end() + 40])
    if not best:
        return '', ''
    name = best[1]
    return name,re.sub(r'\s+', ' ', best[2]).strip()


def chamber(text):
    m = _CHAMBER.search(str(text or '')[:HEAD_CHARS])
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''


def case_number(text):
    """(rg, extrait) : premier numéro de rôle de l'en-tête (haut de la première page), espaces retirés ; ('', '') si absent. Plus bas,
    un numéro de rôle est le plus souvent celui d'une affaire citée : il n'est pas lu."""
    head = str(text or '')[:RG_CHARS]
    m = _RG.search(head)
    if not m:
        return '', ''
    return re.sub(r'\s+', '', m.group(1)).upper(), re.sub(r'\s+', ' ', head[max(0, m.start() - 10):m.end() + 10]).strip()


def portalis(text):
    m = _PORTALIS.search(str(text or '')[:HEAD_CHARS])
    return m.group(1).upper() if m else ''


def _role_of(line):
    m = _ROLE_LINE.match(line.strip())
    if not m:
        return ''
    word = fold(m.group(1))
    for key, pattern in ROLES:
        if re.fullmatch(fold(pattern), word):
            return key
    return ''


def _is_name(line):
    s = _name_of(line)
    if len(s) < 3 or len(s) > 160 or _SEP.match(s) or _role_of(s) or _NOISE.search(s):
        return False
    if _NAME_HINT.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    upper = sum(1 for c in letters if c.isupper())
    return len(letters) >= 3 and upper / len(letters) > 0.6 and not re.search(r'\b(conclusions|assignation|jugement|arr[êe]t|ordonnance|'
                                                                                r'audience|chambre|cour|tribunal|conseil|r\.?g|portalis|'
                                                                                r'pour|contre|entre|objet|plaise)\b', fold(s))


def _name_of(line):
    s = re.sub(r'\s+', ' ', line).strip(' :,-–')
    s = re.split(r',\s*(?:soci[ée]t[ée]|dont|immatricul|au capital|prise|agissant|demeurant|domicili|n[ée]e? |sis)', s, maxsplit=1, flags=re.I)[0]
    return s[:160]


def _lawyers(lines):
    out = []
    for line in lines:
        for m in _LAWYER.finditer(line):
            name = m.group(1).strip()
            if re.search(r'\b(avocat|barreau|toque|selarl|scp|aarpi)\b', fold(name)) or len(name) < 3:
                continue
            postulant = 'postulant' in fold(line)
            label = 'Maître ' + name + (' (postulant)' if postulant else '')
            if label not in out and ('Maître ' + name) not in [x.split(' (')[0] for x in out]:
                out.append(label)
    return out[:4]


def parties(text):
    """Parties de l'en-tête avec leur qualité et leurs avocats : [{'nom', 'qualite', 'avocats', 'extrait'}]."""
    head = str(text or '')[:HEAD_CHARS]
    lines = [l for l in head.replace('\r', '').split('\n')]
    markers = [i for i, l in enumerate(lines) if _role_of(l)]
    out, seen = [], set()
    for k, i in enumerate(markers):
        role = _role_of(lines[i])
        start = markers[k - 1] + 1 if k else max(0, i - 25)
        end = markers[k + 1] if k + 1 < len(markers) else min(len(lines), i + 25)
        before = [l for l in lines[start:i] if l.strip()]
        after = [l for l in lines[i + 1:end] if l.strip()]
        # bloc « avant » jusqu'au dernier séparateur (POUR / CONTRE / ENTRE / ET)
        cut = max([j for j, l in enumerate(before) if _SEP.match(l.strip())] or [-1])
        before = before[cut + 1:]
        cut_after = min([j for j, l in enumerate(after) if _SEP.match(l.strip())] or [len(after)])
        after = after[:cut_after]
        colon = lines[i].strip().endswith(':')
        name_before = next((l for l in before if _is_name(l)), '')
        name_after = next((l for l in after[:4] if _is_name(l)), '')
        if name_before and not colon:
            name, block = name_before, before
        elif name_after:
            name, block = name_after, after
        elif name_before:
            name, block = name_before, before
        else:
            continue
        # « La société X SAS » et « X SAS » désignent la même partie : l'article et le mot « société » ne font pas partie du nom
        nom = re.sub(r'^(?:la\s+)?soci[ée]t[ée]\s+', '', _name_of(name), flags=re.I) or _name_of(name)
        key = fold(nom)
        if key in seen:
            continue
        seen.add(key)
        out.append({'nom': nom, 'qualite': role, 'avocats': _lawyers(block if block is after else before + after),
                    'extrait': re.sub(r'\s+', ' ', ' '.join([name.strip(), lines[i].strip()]))[:200]})
    return out[:12]


def read(text):
    """Tout l'en-tête : {'juridiction', 'chambre', 'rg', 'portalis', 'parties', 'extraits'} ; champs vides si rien de sûr."""
    juri, juri_ex = court(text)
    rg, rg_ex = case_number(text)
    return {'juridiction': juri, 'chambre': chamber(text), 'rg': rg, 'portalis': portalis(text), 'parties': parties(text),
            'extraits': [x for x in (juri_ex, rg_ex) if x]}


INSTANCE = (('cour de cassation', 4), ('conseil d', 4), ('cour d', 3), ('cour administrative', 3))


def instance_rank(juridiction):
    """Degré de juridiction (1 = première instance, 3 = appel, 4 = cassation) : sert à repérer la procédure en cours."""
    f = fold(juridiction)
    for prefix, rank in INSTANCE:
        if f.startswith(prefix):
            return rank
    return 1 if f else 0


def same_case_number(a, b):
    na, nb = re.sub(r'[^0-9a-z]', '', fold(a)), re.sub(r'[^0-9a-z]', '', fold(b))
    return bool(na) and na == nb

"""5.6.34 (B01, B10, chapitre 8.2) : contrôles de preuve partagés — dates civiles, montants, extraits justificatifs, versions de sources.

Rien ici n'appelle un modèle : ce sont des contrôles déterministes appliqués APRÈS la réponse de l'IA.
- Une date est validée comme date civile (le 31 février est refusé), une date partielle reste partielle, la formulation d'origine est
  conservée avec la méthode de normalisation.
- Un montant n'est retenu comme documenté que si la même valeur, dans la même devise et avec le même signe, figure dans le
  passage cité (5.6.35, audit F01–F02).
- Un extrait justificatif doit se retrouver (à la casse, aux espaces et aux guillemets près) dans la version exacte du texte ; sa
  position fixe la page (si la pièce est paginée) ou la position dans le texte (sinon) : aucune page n'est inventée.
- Statuts d'une proposition : allégué, documenté, contradictoire, à vérifier, validé par l'avocat (provenance humaine distincte de la
  provenance documentaire).
"""
import calendar
import hashlib
import re
import unicodedata

STATUTS = {'allegue': 'Allégué par une partie', 'documente': 'Documenté', 'contradictoire': 'Contradictoire', 'a_verifier': 'À vérifier',
           'valide_avocat': 'Validé par l’avocat'}
MOIS = {'janvier': 1, 'fevrier': 2, 'mars': 3, 'avril': 4, 'mai': 5, 'juin': 6, 'juillet': 7, 'aout': 8, 'septembre': 9, 'octobre': 10,
        'novembre': 11, 'decembre': 12, 'janv': 1, 'fevr': 2, 'fev': 2, 'avr': 4, 'juil': 7, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12}
_MOIS_RE = '|'.join(sorted(MOIS, key=len, reverse=True))


def fold(text):
    return ''.join(ch for ch in unicodedata.normalize('NFKD', str(text or '').lower()) if not unicodedata.combining(ch))


def norm(text):
    """Texte comparable : minuscules sans accents, apostrophes et guillemets unifiés, espaces réduits."""
    t = fold(text).replace('’', "'").replace('‘', "'").replace('«', '"').replace('»', '"').replace('“', '"').replace('”', '"')
    t = re.sub(r'\[(?:page|p\.)\s*\d+\]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip(' "\'.;:,')


# ------------------------------------------------------------------------------------------------ dates
def _valid(y, m, d):
    return 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1] and 1800 <= y <= 2200


def normalize_date(value):
    """{'origine', 'iso', 'precision' (jour|mois|annee|''), 'valide', 'motif', 'methode'} ; jamais d'invention de jour ou de mois."""
    raw = str(value or '').strip()
    out = {'origine': raw, 'iso': '', 'precision': '', 'valide': False, 'motif': '', 'methode': ''}
    if not raw:
        out['motif'] = 'date_absente'
        return out
    f = fold(raw)
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[t ].*)?', f)
    if m:
        y, mo, d = map(int, m.groups())
        out['methode'] = 'iso'
    else:
        m = re.fullmatch(r'(\d{1,2})[/.](\d{1,2})[/.](\d{4})', f)
        if m:
            d, mo, y = map(int, m.groups())
            out['methode'] = 'jj/mm/aaaa'
        else:
            m = re.fullmatch(r'(?:le )?(1er|\d{1,2}) (%s)\.? (\d{4})' % _MOIS_RE, f)
            if m:
                d = 1 if m.group(1) == '1er' else int(m.group(1))
                mo, y = MOIS[m.group(2)], int(m.group(3))
                out['methode'] = 'texte'
            else:
                m = re.fullmatch(r'(\d{4})-(\d{1,2})', f) or re.fullmatch(r'(%s)\.? (\d{4})' % _MOIS_RE, f)
                if m:
                    if m.group(1).isdigit():
                        y, mo = int(m.group(1)), int(m.group(2))
                    else:
                        mo, y = MOIS[m.group(1)], int(m.group(2))
                    if not (1 <= mo <= 12 and 1800 <= y <= 2200):
                        out['motif'] = 'date_impossible'
                        return out
                    out.update(iso='%04d-%02d' % (y, mo), precision='mois', valide=True, methode='mois')
                    return out
                m = re.fullmatch(r'(\d{4})', f)
                if m and 1800 <= int(f) <= 2200:
                    out.update(iso=f, precision='annee', valide=True, methode='annee')
                    return out
                out['motif'] = 'date_non_reconnue'   # « courant 2024 », « dans les huit jours » : conservée telle quelle, à vérifier
                return out
    if not _valid(y, mo, d):
        out['motif'] = 'date_impossible'
        return out
    out.update(iso='%04d-%02d-%02d' % (y, mo, d), precision='jour', valide=True)
    return out


def dates_in_text(text):
    """Dates civiles valides présentes dans un texte, au format ISO (jour, mois et année)."""
    found = set()
    f = fold(text)
    for y, mo, d in re.findall(r'\b(\d{4})-(\d{2})-(\d{2})\b', f):
        if _valid(int(y), int(mo), int(d)):
            found.add('%s-%s-%s' % (y, mo, d))
    for d, mo, y in re.findall(r'\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b', f):
        if _valid(int(y), int(mo), int(d)):
            found.add('%04d-%02d-%02d' % (int(y), int(mo), int(d)))
    for d, name, y in re.findall(r'\b(1er|\d{1,2})\s+(%s)\.?\s+(\d{4})\b' % _MOIS_RE, f):
        dd = 1 if d == '1er' else int(d)
        if _valid(int(y), MOIS[name], dd):
            found.add('%04d-%02d-%02d' % (int(y), MOIS[name], dd))
    months = {x[:7] for x in found} | {'%04d-%02d' % (int(y), MOIS[n]) for n, y in re.findall(r'\b(%s)\.?\s+(\d{4})\b' % _MOIS_RE, f)}
    years = {x[:4] for x in found} | set(re.findall(r'\b(1[89]\d\d|2[01]\d\d)\b', f))
    return found | months | years


def date_in_passage(value, passage):
    d = normalize_date(value)
    return bool(d['valide'] and d['iso'] in dates_in_text(passage))


# ------------------------------------------------------------------------------------------------ montants
# 5.6.35 (audit F02) : un montant est une valeur structurée — Decimal, devise, signe, texte d'origine et position. Un nombre n'est un
# montant que s'il porte une devise (symbole ou mot) ; une année, un numéro de page, de pièce ou de référence n'en est jamais un. EUR et USD
# restent distincts ; aucune conversion n'est faite. Les séparateurs ambigus (« 4,500 € », « 4.500 $ ») sont signalés, jamais devinés.
from decimal import Decimal, InvalidOperation   # noqa: E402

CURRENCIES = (('livres sterling', 'GBP'), ('francs suisses', 'CHF'), ('dollars', 'USD'), ('dollar', 'USD'), ('euros', 'EUR'), ('euro', 'EUR'),
              ('us$', 'USD'), ('eur', 'EUR'), ('usd', 'USD'), ('gbp', 'GBP'), ('chf', 'CHF'), ('€', 'EUR'), ('$', 'USD'), ('£', 'GBP'))
_CUR = '|'.join(re.escape(c) for c, _ in CURRENCIES)
_SP = '[   ]'
_NUM = (r'\d{1,3}(?:' + _SP + r'\d{3})+(?:,\d{1,2})?|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[,.]\d{1,2})?')
_MONEY = re.compile(r'(?P<pre>(?:' + _CUR + r')' + _SP + r'?)?(?P<sign>[-−+]' + _SP + r'?)?(?P<num>' + _NUM + r')(?P<post>' + _SP
                    + r'?(?:' + _CUR + r')(?![a-z]))?', re.I)


def _currency(token):
    token = fold(token or '').strip()
    return next((code for word, code in CURRENCIES if token == word), None)


def _value(num, currency):
    """(Decimal, ambigu) d'une écriture de nombre ; None si illisible."""
    s = re.sub(_SP, ' ', num)
    ambiguous = False
    if re.fullmatch(r'\d{1,3}(?: \d{3})+(?:,\d{1,2})?', s):
        s = s.replace(' ', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+,\d{1,2}', s):
        s = s.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:,\d{3})+\.\d{1,2}', s):
        s = s.replace(',', '')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):          # « 4.500 » : milliers à la française ; ambigu hors euro
        ambiguous = currency not in (None, 'EUR')
        s = s.replace('.', '')
    elif re.fullmatch(r'\d{1,3}(?:,\d{3})+', s):           # « 4,500 » : milliers à l'anglaise ; ambigu en euro
        ambiguous = currency in (None, 'EUR')
        s = s.replace(',', '')
    else:
        s = s.replace(',', '.')
    try:
        return Decimal(s), ambiguous
    except InvalidOperation:
        return None


def money_in_text(text):
    """Occurrences monétaires d'un texte : {'valeur', 'devise', 'signe', 'texte', 'debut', 'fin', 'ambigu'} (devise obligatoire)."""
    out = []
    t = str(text or '')
    for m in _MONEY.finditer(t):
        currency = _currency(m.group('pre')) or _currency(m.group('post'))
        if not currency:
            continue
        before = t[max(0, m.start() - 1):m.start()]
        if before and (before.isalnum() or before in '/.-'):   # fragment d'un identifiant (n° 12-4 500 €…) : ignoré
            continue
        parsed = _value(m.group('num'), currency)
        if parsed is None:
            continue
        sign = -1 if (m.group('sign') or '').strip() in ('-', '−') else 1
        out.append({'valeur': parsed[0] * sign, 'devise': currency, 'signe': sign, 'texte': m.group(0).strip(), 'debut': m.start(),
                    'fin': m.end(), 'ambigu': parsed[1]})
    return out


def parse_amount(value, devise=None):
    """Montant déclaré (réponse de l'IA) : première occurrence monétaire ; à défaut, nombre seul avec la devise fournie à part."""
    found = money_in_text(value)
    if found:
        return found[0]
    m = re.search(r'([-−+]' + _SP + r'?)?(' + _NUM + r')', str(value or ''))
    currency = _currency(devise) if devise else None
    if not m:
        return None
    parsed = _value(m.group(2), currency)
    if parsed is None:
        return None
    sign = -1 if (m.group(1) or '').strip() in ('-', '−') else 1
    return {'valeur': parsed[0] * sign, 'devise': currency, 'signe': sign, 'texte': m.group(0).strip(), 'debut': m.start(), 'fin': m.end(),
            'ambigu': parsed[1]}


def check_amount(value, passage, devise=None):
    """Contrôle d'un montant dans le passage cité : None si documenté, sinon la cause (montant_illisible, devise_absente,
    montant_ambigu, montant_absent_du_passage)."""
    claim = parse_amount(value, devise)
    if claim is None:
        return 'montant_illisible'
    if not claim['devise']:
        return 'devise_absente'
    if claim['ambigu']:
        return 'montant_ambigu'
    for occ in money_in_text(passage):
        if occ['valeur'] == claim['valeur'] and occ['devise'] == claim['devise']:
            return 'montant_ambigu' if occ['ambigu'] else None
    return 'montant_absent_du_passage'


def _amount(text):
    """Compatibilité (clé de dédoublonnage) : valeur et devise du montant déclaré."""
    claim = parse_amount(text)
    return (str(claim['valeur']), claim['devise']) if claim else None


def amounts_in_text(text):
    return {(str(o['valeur']), o['devise']) for o in money_in_text(text)}


def amount_in_passage(value, passage, devise=None):
    return check_amount(value, passage, devise) is None


# ------------------------------------------------------------------------------------------------ extraits et positions
def find_excerpt(excerpt, text):
    """Position (début, fin) de l'extrait dans le texte (comparaison normalisée), ou None."""
    target = norm(excerpt)
    if len(target) < 12:
        return None
    # correspondance par fenêtre glissante sur le texte normalisé, en gardant la table des positions d'origine
    chars, positions = [], []
    prev_space = True
    for i, ch in enumerate(str(text or '')):
        for c in fold(ch).replace('’', "'").replace('«', '"').replace('»', '"').replace('“', '"').replace('”', '"'):
            if c.isspace():
                if prev_space:
                    continue
                c = ' '
                prev_space = True
            else:
                prev_space = False
            chars.append(c)
            positions.append(i)
    hay = ''.join(chars)
    at = hay.find(target)
    if at < 0:
        return None
    return positions[at], positions[min(at + len(target), len(positions)) - 1] + 1


def page_at(text, offset, start_page=None):
    """Page d'une position : dernier marqueur « [Page n] » qui la précède ; None si la pièce n'est pas paginée."""
    page = start_page
    for m in re.finditer(r'\[(?:Page|page|p\.)\s*(\d+)\]', str(text or '')):
        if m.start() > offset:
            break
        page = m.group(1)
    return page


def paragraph_at(text, offset):
    return str(text or '')[:offset].count('\n\n') + 1


# ------------------------------------------------------------------------------------------------ versions de sources
def sha(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def source_version(matter, path, text, etag='', method='', backend='index'):
    """SourceVersion (8.2) : identifiant stable de la pièce et de sa version, empreinte du texte extrait, méthode d'extraction."""
    return {'document_id': sha('%s|%s' % (matter, path))[:24], 'dossier_id': matter, 'backend': backend, 'path': path,
            'version': str(etag or ''), 'text_sha256': sha(text), 'methode_extraction': method,
            'ocr': bool(re.search(r'\[ocr\]|ocr', str(text or '')[:400], re.I))}

"""5.6.39 (audit 5.6.37, F01, P0) : preuve financière structurée des chiffres du rapport de gestion.

Un chiffre n'est « justifié » que s'il correspond à un FAIT des comptes : poste, valeur (signe compris), devise, unité et exercice,
établis ensemble dans le MÊME segment du document (la ligne ou la phrase du poste), l'exercice et l'unité pouvant venir des en-têtes du
document ou des colonnes d'un tableau comparatif (N / N-1). Une valeur présente sous un autre poste, une devise trouvée ailleurs dans le
document, l'exercice déclaré par le modèle ou un champ vide ne prouvent rien : le chiffre reste « à vérifier » avec sa cause.
"""
from decimal import Decimal, InvalidOperation
import re
import unicodedata

METHODE = 3

# postes reconnus et leurs libellés dans les comptes (texte replié : minuscules, sans accents, apostrophe droite)
POSTES = (('chiffre_affaires', ('chiffre d\'affaires', 'chiffres d\'affaires', 'ventes de marchandises', 'production vendue')),
          ('resultat_net', ('resultat net', 'benefice de l\'exercice', 'perte de l\'exercice', 'resultat de l\'exercice')),
          ('resultat_exploitation', ('resultat d\'exploitation',)),
          ('resultat_courant', ('resultat courant',)),
          ('total_bilan', ('total du bilan', 'total general', 'total actif', 'total passif')),
          ('capitaux_propres', ('capitaux propres',)),
          ('endettement', ('dettes financieres', 'emprunts et dettes', 'endettement')))
CURRENCY_WORDS = (('€', 'EUR'), ('eur', 'EUR'), ('euros', 'EUR'), ('euro', 'EUR'), ('usd', 'USD'), ('$', 'USD'), ('dollars', 'USD'),
                  ('gbp', 'GBP'), ('£', 'GBP'), ('chf', 'CHF'))
_CUR = re.compile(r'(€|\$|£|\b(?:eur|euros?|usd|dollars|gbp|chf)\b)')
_YEAR = re.compile(r'(?<![\d.,])((?:19|20)\d{2})(?!\d)(?![ \u00a0\u202f.]\d{3}\b)')
_NUM = re.compile(r'(\(\s*)?([-−]\s?)?(\d{1,3}(?:[  .]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(\s*\))?')
_UNITS = (('milliers', r'(en\s+milliers\s+d[\'’]euros|en\s+milliers\b|\bk\s?(?:€|eur)\b|\bkeur\b|\bk€)'),
          ('millions', r'(en\s+millions\s+d[\'’]euros|en\s+millions\b|\bm\s?€|\bmeur\b|\bm€)'))
_HEADER = re.compile(r'(exercice|comptes annuels|comptes|bilan au|clos le|au 31|periode du|montants? exprimes?|en euros|en milliers)')


def _fold(text):
    """Texte replié de même longueur que l'original (positions conservées)."""
    out = []
    for ch in str(text or ''):
        c = unicodedata.normalize('NFKD', ch.lower())
        c = ''.join(x for x in c if not unicodedata.combining(x)) or ' '
        c = c[0] if len(c) >= 1 else ' '
        out.append("'" if c in '’‘' else (' ' if c in '  ' else c))
    return ''.join(out)


def _currency(token):
    token = token.strip().lower()
    return next((code for word, code in CURRENCY_WORDS if token == word), None)


def doc_context(text):
    """Exercice, devise et unité établis par les EN-TÊTES du document (lignes d'intitulé, « exercice clos le… », « en euros »)."""
    folded = _fold(text)
    years, currencies, unit = set(), set(), 'unites'
    columns = []
    for line in folded.splitlines():
        if not _HEADER.search(line):
            continue
        ys = [m.group(1) for m in _YEAR.finditer(line)]
        ys += re.findall(r'\b\d{1,2}[/.]\d{1,2}[/.]((?:19|20)\d{2})\b', line)
        years.update(ys)
        for m in _CUR.finditer(line):
            code = _currency(m.group(1))
            if code:
                currencies.add(code)
        for name, rx in _UNITS:
            if re.search(rx, line):
                unit = name
    for name, rx in _UNITS:   # unité indiquée hors des lignes d'intitulé (« montants en milliers d'euros » en pied de tableau)
        if unit == 'unites' and re.search(rx, folded):
            unit = name
    return {'exercice': next(iter(years)) if len(years) == 1 else '', 'exercices': sorted(years),
            'devise': next(iter(currencies)) if len(currencies) == 1 else '', 'unite': unit, 'colonnes': columns}


def _table_columns(folded, before):
    """Années de colonnes d'un tableau comparatif : dernière ligne d'en-tête (deux années ou plus, aucun autre montant) avant la position."""
    head = folded[:before].splitlines()
    for line in reversed(head[-40:]):
        ys = [m.group(1) for m in _YEAR.finditer(line)]
        rest = _YEAR.sub(' ', line)
        if len(ys) >= 2 and not _NUM.search(re.sub(r'\b(n|n-1)\b', ' ', rest)):
            return ys
    return []


def _numbers(segment):
    """(valeur Decimal signée, début, fin) des montants d'un segment ; les années isolées ne sont pas des montants."""
    out = []
    segment = re.sub(r'(?<=\d)[ \t]*\n[ \t]*(?=\d)', ' ', segment)   # montant coupé par un retour à la ligne (« 1 204 » / « 350 »)
    for m in _NUM.finditer(segment):
        raw = m.group(3)
        if re.fullmatch(r'(?:19|20)\d{2}', raw) and not re.match(r'\s*(€|\$|£|eur|usd|gbp|chf)', segment[m.end():m.end() + 5]):
            continue
        try:
            value = Decimal(re.sub(r'[  .]', '', raw).replace(',', '.'))
        except InvalidOperation:
            continue
        negative = bool(m.group(2)) or (bool(m.group(1)) and bool(m.group(4)))
        out.append((-value if negative else value, m.start(), m.end()))
    return out


def facts(text):
    """Faits financiers du document : un fait par montant rattaché à un poste, avec sa devise, son unité et son exercice, et la preuve de
    chacune de ces dimensions."""
    text = str(text or '')
    folded = _fold(text)
    ctx = doc_context(text)
    hits = []
    for poste, words in POSTES:
        for w in words:
            for m in re.finditer(re.escape(w), folded):
                hits.append((m.start(), m.end(), poste))
    hits.sort()
    kept = []
    for h in hits:   # occurrences qui se chevauchent : la première (la plus longue en cas d'égalité) l'emporte
        if kept and h[0] < kept[-1][1]:
            continue
        kept.append(h)
    out = []
    for i, (start, end, poste) in enumerate(kept):
        limit = kept[i + 1][0] if i + 1 < len(kept) else len(text)
        stop = re.search(r'[.;](?=\s|$)(?!\s*\d)|\n\s*\n', folded[end:limit])
        seg_end = end + stop.start() if stop else limit
        segment = folded[end:seg_end]
        nums = _numbers(segment)
        if not nums:
            continue
        seg_years = [m.group(1) for m in _YEAR.finditer(segment)]
        seg_cur = {c for c in (_currency(m.group(1)) for m in _CUR.finditer(segment)) if c}
        seg_unit = next((name for name, rx in _UNITS if re.search(rx, segment)), '')
        columns = _table_columns(folded, start) if len(nums) >= 2 and not seg_years else []
        for k, (value, a, b) in enumerate(nums):
            if columns and len(columns) == len(nums):
                year, year_proof = columns[k], 'colonne du tableau (%s)' % ' / '.join(columns)
            elif len(set(seg_years)) == 1:
                year, year_proof = seg_years[0], 'même ligne'
            elif not seg_years and len(nums) == 1 and ctx['exercice']:
                year, year_proof = ctx['exercice'], 'en-tête du document'
            else:
                year, year_proof = '', ''
            if len(seg_cur) == 1:
                cur, cur_proof = next(iter(seg_cur)), 'même ligne'
            elif not seg_cur and ctx['devise']:
                cur, cur_proof = ctx['devise'], 'en-tête du document'
            else:
                cur, cur_proof = '', ''
            unit, unit_proof = (seg_unit, 'même ligne') if seg_unit else (ctx['unite'], 'en-tête du document')
            out.append({'poste': poste, 'valeur': value, 'devise': cur, 'unite': unit, 'exercice': year, 'debut': start, 'fin': seg_end,
                        'segment': text[start:seg_end].strip()[:300], 'preuve_devise': cur_proof, 'preuve_exercice': year_proof,
                        'preuve_unite': unit_proof})
    return out


def poste_of(label):
    folded = _fold(label)
    for poste, words in POSTES:
        if any(w in folded for w in words) or poste.replace('_', ' ') in folded:
            return poste
    if 'affaires' in folded:
        return 'chiffre_affaires'
    return ''


def check(x, text, exercice=''):
    """(cause ou None, détail). Les dimensions du chiffre doivent être établies par UN MÊME fait du passage cité."""
    from .preuve5634 import find_excerpt, page_at, parse_amount
    text = str(text or '')
    extrait = str(x.get('extrait') or '').strip()
    if len(extrait) < 12:
        return 'extrait_absent_ou_trop_court', {}
    pos = find_excerpt(extrait, text)
    if pos is None:
        return 'extrait_introuvable_dans_les_comptes', {}
    detail = {'page': page_at(text, pos[0]), 'debut': pos[0], 'extrait': text[pos[0]:pos[1]][:300], 'methode': METHODE}
    poste = poste_of(x.get('libelle') or '')
    if not poste:
        return 'poste_non_controlable', detail
    devise = str(x.get('devise') or '')
    claim = parse_amount(x.get('montant'), devise or None)
    if claim is None:
        return 'montant_illisible', detail
    if claim['devise'] and devise and claim['devise'] != devise:
        return 'devise_differente_des_comptes', detail
    devise = devise or claim['devise'] or ''
    inside = [f for f in facts(text) if f['debut'] < pos[1] and f['fin'] > pos[0]]
    same = [f for f in inside if f['poste'] == poste]
    if not same:
        return 'poste_different_dans_l_extrait', detail
    unit = str(x.get('unite') or 'unites')
    if all(f['unite'] != unit for f in same):
        return 'unite_differente_des_comptes', {**detail, 'unite_des_comptes': same[0]['unite']}
    matches = [f for f in same if f['valeur'] == claim['valeur'] and f['unite'] == unit]
    if not matches:
        return 'montant_absent_de_l_extrait', detail
    if len({(f['devise'], f['exercice']) for f in matches}) > 1:
        return 'rattachement_ambigu', detail
    f = matches[0]
    detail.update(poste=f['poste'], valeur=str(f['valeur']), devise=f['devise'], unite=f['unite'], exercice=f['exercice'], segment=f['segment'],
                  preuve_devise=f['preuve_devise'], preuve_exercice=f['preuve_exercice'], preuve_unite=f['preuve_unite'])
    if not f['devise']:
        return 'devise_non_etablie', detail
    if f['devise'] != devise:
        return 'devise_differente_des_comptes', detail
    wanted = str(exercice or '')[:4]
    declared = str(x.get('exercice') or '').strip()[:4]
    if (wanted or declared) and not f['exercice']:
        return 'exercice_non_etabli', detail
    if wanted and f['exercice'] != wanted:
        return 'exercice_different', detail
    if declared and f['exercice'] != declared:
        return 'exercice_different', detail
    return None, detail

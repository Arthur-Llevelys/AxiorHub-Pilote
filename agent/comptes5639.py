"""5.6.39 (audit 5.6.37, F01, P0) : preuve financière structurée des chiffres du rapport de gestion.

Un chiffre n'est « justifié » que s'il correspond à un FAIT des comptes : poste, valeur (signe compris), devise, unité et exercice,
établis ensemble dans le MÊME segment du document (la ligne ou la phrase du poste), l'exercice et l'unité pouvant venir des en-têtes du
document ou des colonnes d'un tableau comparatif (N / N-1). Une valeur présente sous un autre poste, une devise trouvée ailleurs dans le
document, l'exercice déclaré par le modèle ou un champ vide ne prouvent rien : le chiffre reste « à vérifier » avec sa cause.

5.6.40 (audit 5.6.39, B01, P0 ; P01) — méthode 4 :
  - segmentation par ligne : le segment d'un poste s'arrête en fin de ligne, sauf ligne suivante purement numérique (montant coupé par
    l'OCR, tableau dont les montants suivent l'intitulé) ; un nouvel intitulé, même inconnu (« Charges externes : 20 000 »), arrête le
    poste précédent ;
  - chaque fait garde les positions exactes de son libellé et de son montant ; le contrôle exige que le libellé ET le montant figurent
    dans l'extrait cité ;
  - plusieurs montants dans un segment sans colonnes de tableau établies : rattachement ambigu, jamais « justifié » ;
  - analyse préparée une seule fois par document (``analyse``) et réutilisée pour chaque chiffre du rapport.

5.6.42 (audit 5.6.41, F01, P0) — méthode 5 : périmètre des déclarations d'unité.
  - une mention d'unité portée par une ligne qui contient des montants (« Dettes financières (en milliers d'euros) : 20 ») ne vaut que pour
    cette ligne ; elle n'est plus diffusée au document ;
  - une déclaration d'unité est une ligne sans montant (« (en milliers d'euros) », « Montants exprimés en millions d'euros ») ; dans la zone
    de titre du document (premier bloc, avant toute ligne chiffrée) elle vaut pour le document ; ailleurs elle vaut pour son bloc (tableau ou section
    délimités par une ligne vide ou un saut de page), ou, isolée, pour le tableau qui la suit (titre) ou à défaut qui la précède (pied) ;
  - ordre : unité du segment, puis du bloc, puis du document ; deux unités différentes au même niveau : unité non établie (jamais un
    choix arbitraire) ; aucune déclaration : montants en unités ;
  - la source précise de l'unité (ligne et positions) est jointe à la preuve.
"""
from bisect import bisect_right
from decimal import Decimal, InvalidOperation
import re
import unicodedata

METHODE = 5

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
_YEAR = re.compile(r'(?<![\d.,])((?:19|20)\d{2})(?!\d)(?![   .]\d{3}\b)')
_NUM = re.compile(r'(\(\s*)?([-−]\s?)?(\d{1,3}(?:[  .]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?)(\s*\))?')
_UNITS = (('milliers', r'(en\s+milliers\s+d[\'’]euros|en\s+milliers\b|\bk\s?(?:€|eur)\b|\bkeur\b|\bk€)'),
          ('millions', r'(en\s+millions\s+d[\'’]euros|en\s+millions\b|\bm\s?€|\bmeur\b|\bm€)'))
_HEADER = re.compile(r'(exercice|comptes annuels|comptes|bilan au|clos le|au 31|periode du|montants? exprimes?|en euros|en milliers)')
# ligne purement numérique (suite d'un montant coupé, montants d'un tableau sous leur intitulé)
_NUMERIC_LINE = re.compile(r'[\s\d.,  ()\-−€$£%]*(?:(?:eur|euros?|usd|gbp|chf|keur|k€|meur|m€)\b[\s.]*)?')
# mots qui ne commencent pas un nouvel intitulé entre deux montants d'un même poste
_GLUE = {'eur', 'euro', 'euros', 'usd', 'dollars', 'gbp', 'chf', 'keur', 'meur', 'milliers', 'millions', 'net', 'nette', 'hors', 'taxes',
         'ht', 'ttc', 'soit', 'contre', 'vs', 'versus', 'exercice', 'exercices', 'annee', 'ans', 'mois', 'clos', 'au', 'le', 'du', 'des', 'les'}
_WORD = re.compile(r'[a-z]{3,}')


def _fold(text):
    """Texte replié de même longueur que l'original (positions conservées)."""
    out = []
    for ch in str(text or ''):
        c = unicodedata.normalize('NFKD', ch.lower())
        c = ''.join(x for x in c if not unicodedata.combining(x)) or ' '
        c = c[0] if len(c) >= 1 else ' '
        out.append("'" if c in '’‘' else (' ' if c in '  ' else c))
    return ''.join(out)


def _currency(token):
    token = token.strip().lower()
    return next((code for word, code in CURRENCY_WORDS if token == word), None)


_MONTHS = r'(?:janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre)'
_DATES = re.compile(r'\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b|\b\d{1,2}(?:er)?\s+' + _MONTHS + r'\b')


def _has_amount(line):
    """5.6.42 (F01) : la ligne porte au moins un montant (dates et années exclues)."""
    return bool(_numbers(_DATES.sub(' ', line)))


def _unit_scopes(folded, lines):
    """5.6.42 (audit 5.6.41, F01) : déclarations d'unité et leur périmètre. Renvoie (unités du document, unités par bloc, bloc de chaque
    ligne) ; chaque unité porte la ligne et les positions de sa déclaration."""
    block, blocks, current, seen_text = [], 0, 0, False
    for a, b in lines:
        line = folded[a:b]
        if '\f' in line or not line.strip():
            if seen_text:
                current += 1
            seen_text = False
            block.append(current if line.strip() else -1)
            if line.strip():
                seen_text = True
            continue
        block.append(current)
        seen_text = True
    blocks = current + 1
    data = [bool(folded[a:b].strip()) and _has_amount(folded[a:b]) for a, b in lines]
    first_data = next((i for i, d in enumerate(data) if d), len(lines))
    first_block = next((block[i] for i, (a, b) in enumerate(lines) if folded[a:b].strip()), 0)   # zone de titre : premier bloc du document
    data_blocks = sorted({block[i] for i, d in enumerate(data) if d})
    doc, per_block, orphans = {}, {}, []
    for i, (a, b) in enumerate(lines):
        if data[i] or block[i] < 0:
            continue
        line = folded[a:b]
        for name, rx in _UNITS:
            m = re.search(rx, line)
            if not m:
                continue
            source = {'ligne': i + 1, 'debut': a + m.start(), 'fin': a + m.end()}
            if i < first_data and block[i] == first_block:
                doc.setdefault(name, source)
            elif block[i] in data_blocks:
                per_block.setdefault(block[i], {}).setdefault(name, source)
            else:
                orphans.append((block[i], name, source))
    for b_index, name, source in orphans:   # déclaration isolée : titre du tableau suivant, sinon pied du tableau précédent
        nxt = next((d for d in data_blocks if d > b_index), None)
        prev = next((d for d in reversed(data_blocks) if d < b_index), None)
        target = nxt if nxt is not None and nxt not in per_block else prev
        if target is not None:
            per_block.setdefault(target, {}).setdefault(name, source)
    del blocks
    return doc, per_block, block


def _scoped_unit(scopes, line_index):
    """5.6.42 (F01) : unité applicable à une ligne hors mention locale — bloc, puis document ; conflit : unité non établie."""
    doc, per_block, block = scopes
    own = per_block.get(block[line_index]) if 0 <= line_index < len(block) else None
    for level, found in (('du tableau ou de la section', own), ('en-tête du document', doc)):
        if not found:
            continue
        if len(found) > 1:
            return '', 'conflit d\'unités (%s)' % ' / '.join(sorted(found)), None
        name, source = next(iter(found.items()))
        return name, ('déclaration %s, ligne %d' % (level, source['ligne']) if level != 'en-tête du document'
                      else 'en-tête du document, ligne %d' % source['ligne']), source
    return 'unites', 'aucune mention de milliers ni de millions', None


def doc_context(text, folded=None):
    """Exercice, devise et unité établis par les EN-TÊTES du document (lignes d'intitulé, « exercice clos le… », « en euros »).
    5.6.42 (F01) : l'unité du document ne vient que d'une déclaration placée dans sa zone de titre, jamais d'une ligne de poste."""
    folded = _fold(text) if folded is None else folded
    years, currencies = set(), set()
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
    doc_units, _, _ = _unit_scopes(folded, _lines(folded))
    unit = 'unites' if not doc_units else (next(iter(doc_units)) if len(doc_units) == 1 else '')
    return {'exercice': next(iter(years)) if len(years) == 1 else '', 'exercices': sorted(years),
            'devise': next(iter(currencies)) if len(currencies) == 1 else '', 'unite': unit, 'colonnes': []}


def _lines(folded):
    """(début, fin) de chaque ligne du texte replié."""
    out, start = [], 0
    for m in re.finditer(r'\n', folded):
        out.append((start, m.start()))
        start = m.end()
    out.append((start, len(folded)))
    return out


def _column_headers(folded, lines):
    """5.6.40 (P01) : lignes d'en-tête de tableau comparatif (deux années ou plus, aucun autre montant), calculées une fois par document."""
    heads = []
    for i, (a, b) in enumerate(lines):
        line = folded[a:b]
        ys = [m.group(1) for m in _YEAR.finditer(line)]
        if len(ys) >= 2 and not _NUM.search(re.sub(r'\b(n|n-1)\b', ' ', _YEAR.sub(' ', line))):
            heads.append((i, ys))
    return heads


def _table_columns(analysis, line_index):
    """Années de colonnes : dernier en-tête de tableau dans les 40 lignes qui précèdent (index des en-têtes, sans relecture du texte)."""
    heads = analysis['entetes']
    k = bisect_right([i for i, _ in heads], line_index) - 1
    if k >= 0 and line_index - heads[k][0] <= 40:
        return heads[k][1]
    return []


def _collapse(segment):
    """Montant coupé par un retour à la ligne (« 1 204 » / « 350 ») rejoint par un seul espace ; renvoie le texte et la table des positions
    d'origine (index du texte rejoint → index du segment)."""
    out, index, last = [], [], 0
    for m in re.finditer(r'(?<=\d)[ \t]*\n[ \t]*(?=\d)', segment):
        out.append(segment[last:m.start()])
        index.extend(range(last, m.start()))
        out.append(' ')
        index.append(m.start())
        last = m.end()
    out.append(segment[last:])
    index.extend(range(last, len(segment)))
    index.append(len(segment))
    return ''.join(out), index


def _numbers(segment):
    """(valeur Decimal signée, début, fin) des montants d'un segment, positions dans le segment d'origine ; les années isolées ne sont pas
    des montants."""
    out = []
    joined, index = _collapse(segment)
    for m in _NUM.finditer(joined):
        raw = m.group(3)
        if re.fullmatch(r'(?:19|20)\d{2}', raw) and not re.match(r'\s*(€|\$|£|eur|usd|gbp|chf)', joined[m.end():m.end() + 5]):
            continue
        try:
            value = Decimal(re.sub(r'[  .]', '', raw).replace(',', '.'))
        except InvalidOperation:
            continue
        negative = bool(m.group(2)) or (bool(m.group(1)) and bool(m.group(4)))
        out.append((-value if negative else value, index[m.start()], index[m.end()]))
    return out


def _segment_end(folded, end, limit, lines, line_index):
    """Fin du segment d'un poste : fin de ligne (ou ponctuation de fin de phrase), prolongée d'une ligne purement numérique ; arrêt avant
    un nouvel intitulé, même inconnu, qui suit un premier montant."""
    a, b = lines[line_index]
    seg_end = min(b, limit)
    stop = re.search(r'[.;](?=\s|$)(?!\s*\d)', folded[end:seg_end])
    if stop:
        seg_end = end + stop.start()
    elif seg_end == b and line_index + 1 < len(lines):
        na, nb = lines[line_index + 1]
        nxt = folded[na:nb]
        if nb <= limit and re.search(r'\d', nxt) and _NUMERIC_LINE.fullmatch(nxt):
            seg_end = nb
    nums = _numbers(folded[end:seg_end])
    for (_, _, prev_end), (_, next_start, _) in zip(nums, nums[1:]):
        between = folded[end + prev_end:end + next_start]
        word = next((w for w in _WORD.finditer(between) if w.group(0) not in _GLUE), None)
        if word:   # « … 812 450 EUR  Charges externes : 20 000 EUR » : le poste s'arrête avant l'intitulé suivant
            return end + prev_end + word.start(), True
    return seg_end, False


def analyse(text):
    """5.6.40 (P01) : analyse comptable préparée une seule fois par document — texte replié, lignes, en-têtes de tableaux, contexte et faits.
    Immuable pour l'appelant ; ``check`` l'accepte à la place du texte brut."""
    text = str(text or '')
    folded = _fold(text)
    lines = _lines(folded)
    analysis = {'texte': text, 'replie': folded, 'lignes': lines, 'debuts': [a for a, _ in lines], 'methode': METHODE}
    analysis['contexte'] = doc_context(text, folded)
    analysis['unites'] = _unit_scopes(folded, lines)   # 5.6.42 (F01) : périmètre des déclarations d'unité
    analysis['entetes'] = _column_headers(folded, lines)
    analysis['faits'] = _facts(analysis)
    return analysis


def facts(text):
    """Faits financiers du document (compatibilité : analyse complète puis faits)."""
    return analyse(text)['faits']


def _facts(analysis):
    text, folded, lines, ctx = analysis['texte'], analysis['replie'], analysis['lignes'], analysis['contexte']
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
        line_index = bisect_right(analysis['debuts'], start) - 1
        seg_end, cut = _segment_end(folded, end, limit, lines, line_index)
        segment = folded[end:seg_end]
        nums = _numbers(segment)
        if not nums:
            continue
        seg_years = [m.group(1) for m in _YEAR.finditer(segment)]
        seg_cur = {c for c in (_currency(m.group(1)) for m in _CUR.finditer(segment)) if c}
        seg_units = [(name, m) for name, rx in _UNITS for m in [re.search(rx, segment)] if m]   # 5.6.42 (F01) : mention propre au poste
        columns = _table_columns(analysis, line_index) if len(nums) >= 2 and not seg_years else []
        tabular = bool(columns) and len(columns) == len(nums)
        for k, (value, a, b) in enumerate(nums):
            if tabular:
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
            if len({n for n, _ in seg_units}) == 1:
                unit, unit_proof = seg_units[0][0], 'même ligne'
                unit_pos = [end + seg_units[0][1].start(), end + seg_units[0][1].end()]
            elif seg_units:
                unit, unit_proof, unit_pos = '', 'conflit d\'unités sur la ligne', None
            else:
                unit, unit_proof, source = _scoped_unit(analysis['unites'], line_index)
                unit_pos = [source['debut'], source['fin']] if source else None
            out.append({'poste': poste, 'valeur': value, 'devise': cur, 'unite': unit, 'exercice': year, 'debut': start, 'fin': seg_end,
                        'libelle_debut': start, 'libelle_fin': end, 'montant_debut': end + a, 'montant_fin': end + b,
                        'ambigu': len(nums) > 1 and not tabular, 'coupe': cut,
                        'segment': text[start:seg_end].strip()[:300], 'preuve_devise': cur_proof, 'preuve_exercice': year_proof,
                        'preuve_unite': unit_proof, 'position_unite': unit_pos})
    return out


def poste_of(label):
    folded = _fold(label)
    for poste, words in POSTES:
        if any(w in folded for w in words) or poste.replace('_', ' ') in folded:
            return poste
    if 'affaires' in folded:
        return 'chiffre_affaires'
    return ''


def check(x, text, exercice='', analysis=None):
    """(cause ou None, détail). Les dimensions du chiffre doivent être établies par UN MÊME fait dont le libellé ET le montant figurent dans
    le passage cité. ``analysis`` (résultat de ``analyse``) évite de refaire l'analyse du document pour chaque chiffre."""
    from .preuve5634 import find_excerpt, page_at, parse_amount
    if analysis is None:
        analysis = analyse(text)
    text = analysis['texte']
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
    # libellé et montant DANS l'extrait : un montant voisin, hors du passage cité, ne prouve rien
    inside = [f for f in analysis['faits'] if pos[0] <= f['libelle_debut'] and f['libelle_fin'] <= pos[1]
              and pos[0] <= f['montant_debut'] and f['montant_fin'] <= pos[1]]
    same = [f for f in inside if f['poste'] == poste]
    if not same:
        return 'poste_different_dans_l_extrait', detail
    unit = str(x.get('unite') or 'unites')
    if all(f['unite'] == '' for f in same):   # 5.6.42 (F01) : déclarations contradictoires, unité non établie
        return 'unite_non_etablie', {**detail, 'preuve_unite': same[0]['preuve_unite']}
    if all(f['unite'] != unit for f in same):
        return 'unite_differente_des_comptes', {**detail, 'unite_des_comptes': same[0]['unite']}
    matches = [f for f in same if f['valeur'] == claim['valeur'] and f['unite'] == unit]
    if not matches:
        return 'montant_absent_de_l_extrait', detail
    if len({(f['devise'], f['exercice']) for f in matches}) > 1:
        return 'rattachement_ambigu', detail
    f = matches[0]
    detail.update(poste=f['poste'], valeur=str(f['valeur']), devise=f['devise'], unite=f['unite'], exercice=f['exercice'], segment=f['segment'],
                  preuve_devise=f['preuve_devise'], preuve_exercice=f['preuve_exercice'], preuve_unite=f['preuve_unite'],
                  position_unite=f.get('position_unite'),
                  position_libelle=[f['libelle_debut'], f['libelle_fin']], position_montant=[f['montant_debut'], f['montant_fin']],
                  page_montant=page_at(text, f['montant_debut']))
    if f['ambigu']:   # plusieurs montants dans le segment, sans colonnes établies : rattachement non prouvé
        return 'rattachement_ambigu', detail
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

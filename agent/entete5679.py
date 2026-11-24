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
    if _AVOCAT_LIGNE.match(s):   # 5.6.109 (C10) : « MAITRE JEAN BERNARD » est l'avocat d'une partie, jamais une partie
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


# 5.6.109 (C10) : lignes de STRUCTURE d'un acte (intitulé de juridiction et sa suite, numéro de rôle, formule exécutoire, titre de l'acte,
# section, date de la décision). Elles bornent le bloc d'identification d'une partie : un nom n'est jamais cherché au-dessus d'elles. Le
# contrôle porte sur la place et la forme de la ligne dans l'acte, pas sur une liste de villes ou de patronymes.
_JURIDICTION_SEULE = (r"(?:tribunal (?:judiciaire|de commerce|des activites economiques|administratif|de proximite|de grande instance|d['’]instance|"
                      r"paritaire des baux ruraux)|cour d['’]appel|cour administrative d['’]appel|conseil de prud['’]hommes)(?: de| du| des| d['’])?")
_MOT_JURIDICTION = r'(?:tribunal|cour|conseil)'
_SUITE_MOT = r"(?:judiciaire|de commerce|des activites|administrati|d['’]\s*appel|de prud|de proximite|paritaire|de grande instance|d['’]\s*instance|de cassation|d['’]\s*etat)"
_DEBUT_JURIDICTION = re.compile(r'^\W*(?:%s)' % '|'.join(_COURTS), re.I)
_TITRE = re.compile(r"^\W*(?:r[ée]publique fran[çc]aise|au nom du peuple fran[çc]ais|extrait des minutes|minute\b|copie (?:ex[ée]cutoire|certifi[ée]e)|grosse\b|"
                    r"exp[ée]dition\b|(?:jugement|arr[êe]t|ordonnance|d[ée]cision|conclusions|assignation|requ[êe]te|d[ée]claration d[’']appel|signification|"
                    r"proc[èe]s[- ]verbal|bordereau)\b|section\s|p[ôo]le\s|chambre\s|\d{1,2}\s*(?:e|[èe]me|[èe]re|er)\s+chambre|audience\b|"
                    r"du\s+\d{1,2}(?:er)?\s+\w+\s+\d{4})", re.I)
_ROLE_NUMERO = re.compile(r"^\W*(?:n[°o]\.?\s*)?(?:de\s+)?(?:r\.\s?g\.?|rg|r[ée]pertoire g[ée]n[ée]ral|portalis|minute|r[ôo]le)\b", re.I)
_AVOCAT_LIGNE = re.compile(r"^\W*(?:ma[iî]tre|me\.?|m[eᵉ]\.?)\s+\S", re.I)
_PLURIEL = re.compile(r's\W*$')
_REPRESENTE = re.compile(r"(?i:repr[ée]sent[ée]e?s?\s+par\s+(?:(?:son|sa|ses|leur)\s+[a-zàâçéèêëîïôûù'’ \-]{2,40}?[ ,]+)?)"
                         r"((?:Monsieur|Madame|Mademoiselle|M\.|Mme|Mlle)\s+[A-ZÀ-Ý][\w'’\-]+(?:\s+(?:de\s+|du\s+|d['’])?[A-ZÀ-Ý][\w'’\-]+){0,3})")
_MORALE = re.compile(r'^\W*(?:la soci[ée]t[ée]|soci[ée]t[ée]|association|syndicat|la commune|l[\'’]?etat)\b|\b(?:s\.?a\.?s\.?u?|s\.?a\.?r\.?l\.?|s\.?c\.?i\.?|s\.?a\.?|eurl|'
                     r'sasu|sci|snc|selarl|selas|scp|gie|scop)\b', re.I)
_PHYSIQUE = re.compile(r'^\W*(?:monsieur|madame|mademoiselle|m\.|mme|mlle)\s', re.I)


def _structure(ligne, precedente=''):
    """Vrai si la ligne appartient à la structure de l'acte (jamais à l'identification d'une partie). ``precedente`` : ligne non vide
    qui la précède, pour reconnaître la suite d'un intitulé de juridiction coupé (« TRIBUNAL JUDICIAIRE » / « DE LYON »,
    « TRIBUNAL JUDICIAIRE DE » / « VALBRUNE », « TRIBUNAL » / « JUDICIAIRE DE VALBRUNE »)."""
    s, p = fold(ligne).strip(' :'), fold(precedente).strip(' :')
    if not s:
        return False
    if re.fullmatch(_JURIDICTION_SEULE, p):
        if re.search(r"(?: de| du| des| d['’])$", p) or re.match(r"^(?:de|du|des|d['’])\s*[a-z]", s):
            return True
    if re.fullmatch(_MOT_JURIDICTION, p) and re.match(_SUITE_MOT, s):
        return True
    if re.fullmatch(_JURIDICTION_SEULE, s) or re.fullmatch(_MOT_JURIDICTION, s):
        return True
    brut = str(ligne)
    return bool(_DEBUT_JURIDICTION.match(brut) or re.match(r'^\W*(?:cour de cassation|conseil d[\'’][ée]tat)\b', brut, re.I)
                or _TITRE.match(brut) or (_ROLE_NUMERO.match(brut) and len(brut.strip()) < 80))


def _premier_nom(lines):
    """Premier nom de partie du bloc. Ignore les lignes de structure, dont la continuation d'un intitulé de juridiction coupé sur
    plusieurs lignes.

    Le contrôle porte sur le contexte, pas sur une liste de villes ou de patronymes.
    Un nom « Monsieur de Lyon » ou une société « DE LYON » reste admissible.
    """
    noms = _noms(lines, False)
    return noms[0] if noms else ''


def _noms(lines, plusieurs):
    """Noms de parties d'un bloc d'identification, dans l'ordre de l'acte : le premier seulement, ou tous quand la qualité est au pluriel
    (« Demandeurs ») — plusieurs personnes du même bloc sont conservées."""
    out, precedente = [], ''
    for ligne in lines:
        if not _structure(ligne, precedente) and _is_name(ligne):
            # au pluriel, une seconde ligne n'est un nom que si elle désigne une personne (civilité, société) ou est écrite en capitales
            if not out or (_PHYSIQUE.match(ligne) or _MORALE.search(_name_of(ligne)) or _name_of(ligne) == _name_of(ligne).upper()) \
                    and not re.search(r'\bcapital\b', fold(ligne)):
                out.append(ligne)
                if not plusieurs or len(out) >= 8:
                    break
        precedente = ligne.strip()
    return out


def _nom_propre(ligne):
    """« La société X SAS » et « X SAS » désignent la même partie : l'article et le mot « société » ne font pas partie du nom."""
    return re.sub(r'^(?:la\s+)?soci[ée]t[ée]\s+', '', _name_of(ligne), flags=re.I) or _name_of(ligne)


def _nature_personne(ligne):
    if _PHYSIQUE.match(ligne):
        return 'physique'
    return 'morale' if _MORALE.search(_name_of(ligne)) else ''


def _representant(lines):
    """Personne qui représente la partie (dirigeant, tuteur) : gardée comme représentant, jamais comme partie. Un avocat n'est pas lu ici."""
    for ligne in lines:
        if 'avocat' in fold(ligne):
            continue
        m = _REPRESENTE.search(ligne)
        if m:
            return re.sub(r'\s+', ' ', m.group(1)).strip()[:160]
    return ''


def _partie(lignes, numero, qualite, zone, avocats, bloc, extrait, cote=''):
    brut = lignes[numero]
    return {'nom': _nom_propre(brut), 'qualite': qualite, 'avocats': avocats, 'extrait': re.sub(r'\s+', ' ', extrait)[:200],
            # 5.6.109 (C10) : contexte conservé — zone d'identification, ligne de l'acte (à partir de 1), nom tel qu'écrit, nature, représentant
            'zone': zone, 'ligne': numero + 1, 'nom_ecrit': re.sub(r'\s+', ' ', brut).strip(' :,-–')[:200], 'nature_personne': _nature_personne(brut),
            'representant': _representant(bloc), 'cote': cote}


def _cote(lines, i):
    """« pour » ou « contre » : dernier séparateur au-dessus de la ligne ; '' sinon. Ne dit jamais qui est le client du cabinet."""
    for j in range(i, max(-1, i - 60), -1):
        m = _SEP.match(lines[j].strip())
        if m and fold(m.group(1)) in ('pour', 'contre'):
            return fold(m.group(1))
    return ''


def parties(text):
    """Parties de l'en-tête avec leur qualité et leurs avocats : [{'nom', 'qualite', 'avocats', 'extrait', 'zone', 'ligne', 'nom_ecrit',
    'nature_personne', 'representant', 'cote'}]. Sans aucune qualité de procédure écrite, les blocs POUR et CONTRE sont lus (qualité vide)."""
    head = str(text or '')[:HEAD_CHARS]
    lines = [l for l in head.replace('\r', '').split('\n')]
    markers = [i for i, l in enumerate(lines) if _role_of(l)]
    out, seen = [], set()
    for k, i in enumerate(markers):
        role = _role_of(lines[i])
        start = markers[k - 1] + 1 if k else max(0, i - 25)
        end = markers[k + 1] if k + 1 < len(markers) else min(len(lines), i + 25)
        before = [j for j in range(start, i) if lines[j].strip()]
        after = [j for j in range(i + 1, end) if lines[j].strip()]
        # bloc « avant » : après le dernier séparateur (POUR / CONTRE / ENTRE / ET) ou la dernière ligne de structure de l'acte
        cut, precedente = -1, lines[before[0] - 1].strip() if before and before[0] else ''
        for n, j in enumerate(before):
            if _SEP.match(lines[j].strip()) or _structure(lines[j], precedente):
                cut = n
            precedente = lines[j].strip()
        before = before[cut + 1:]
        cut_after = min([n for n, j in enumerate(after) if _SEP.match(lines[j].strip())] or [len(after)])
        after = after[:cut_after]
        colon = lines[i].strip().endswith(':')
        plusieurs = bool(_PLURIEL.search(fold(lines[i])))
        par_ligne = lambda idx: {lines[j]: j for j in reversed(idx)}
        noms_avant = _noms([lines[j] for j in before], plusieurs)
        noms_apres = _noms([lines[j] for j in after[:8 if plusieurs else 4]], plusieurs)
        if noms_avant and not colon:
            noms_bloc, block, index = noms_avant, before, par_ligne(before)
        elif noms_apres:
            noms_bloc, block, index = noms_apres, after, par_ligne(after)
        elif noms_avant:
            noms_bloc, block, index = noms_avant, before, par_ligne(before)
        else:
            continue
        avocats = _lawyers([lines[j] for j in (block if block is after else before + after)])
        for name in noms_bloc:
            key = fold(_nom_propre(name))
            if key in seen:
                continue
            seen.add(key)
            out.append(_partie(lines, index[name], role, 'qualite', avocats, [lines[j] for j in block], ' '.join([name.strip(), lines[i].strip()]),
                               _cote(lines, i)))
    if not markers:   # aucune qualité écrite : zones POUR et CONTRE (la qualité reste vide, rien n'est déduit)
        seps = [i for i, l in enumerate(lines) if _SEP.match(l.strip()) and fold(_SEP.match(l.strip()).group(1)) in ('pour', 'contre')]
        for k, i in enumerate(seps):
            end = seps[k + 1] if k + 1 < len(seps) else min(len(lines), i + 14)
            bloc = [j for j in range(i + 1, end) if lines[j].strip()]
            arret = next((n for n, j in enumerate(bloc) if _SEP.match(lines[j].strip()) or _structure(lines[j])
                          or re.match(r'^\W*(?:plaise|objet|il est demand|a l[\'’]honneur)', fold(lines[j]))), len(bloc))
            bloc = bloc[:arret]
            index = {lines[j]: j for j in reversed(bloc)}
            for name in _noms([lines[j] for j in bloc], True):
                key = fold(_nom_propre(name))
                if key in seen:
                    continue
                seen.add(key)
                out.append(_partie(lines, index[name], '', 'pour_contre', _lawyers([lines[j] for j in bloc]), [lines[j] for j in bloc],
                                   ' '.join([lines[i].strip(), name.strip()]), fold(_SEP.match(lines[i].strip()).group(1))))
    return out[:12]


# ------------------------------------------------------------------------------------------------ 5.6.109 (C10) : contrats
# Qualités contractuelles reconnues dans « ci-après dénommé(e) « le Vendeur » ». Elles décrivent le rôle DANS L'ACTE ; elles ne disent
# jamais qui est le client du cabinet ni qui lui est opposé (« client » au contrat est rendu « client (au contrat) »).
QUALITES_CONTRAT = (('vendeur', r'vendeu(?:r|se)s?|venderesses?'), ('acquéreur', r'acquereu(?:r|se)s?|acheteu(?:r|se)s?'), ('cédant', r'cedante?s?'),
                    ('cessionnaire', r'cessionnaires?'), ('bailleur', r'bailleu(?:r|se)s?|bailleresses?'), ('preneur', r'preneu(?:r|se)s?|locataires?'),
                    ('prêteur', r'preteu(?:r|se)s?'), ('emprunteur', r'emprunteu(?:r|se)s?'), ('mandant', r'mandante?s?'), ('mandataire', r'mandataires?'),
                    ('prestataire', r'prestataires?'), ('client (au contrat)', r'cliente?s?'), ('employeur', r'employeu(?:r|se)s?'), ('salarié', r'salariee?s?'),
                    ('promettant', r'promettante?s?'), ('bénéficiaire', r'beneficiaires?'), ('caution', r'cautions?|garante?s?'),
                    ('franchiseur', r'franchiseurs?'), ('franchisé', r'franchisee?s?'), ('concédant', r'concedante?s?'), ('licencié', r'licenciee?s?'),
                    ('associé', r'associee?s?'), ('fournisseur', r'fournisseurs?'), ('distributeur', r'distributeurs?'), ('sous-traitant', r'sous-traitante?s?'),
                    ('donneur d’ordre', r"donneu(?:r|se)s? d['’ ]ordre"), ('maître d’ouvrage', r"maitres? d['’ ]ouvrage"), ('entrepreneur', r'entrepreneurs?'),
                    ('cocontractant', r'cocontractante?s?'))
_SOUSSIGNES = re.compile(r'entre\s+les\s+soussign[ée]e?s?\s*:?|^\W*entre\s*:\s*$', re.I | re.M)
_FIN_COMPARUTION = re.compile(r"^\W*(?:il a [ée]t[ée]|il est (?:pr[ée]alablement |d[’']abord )?(?:expos|convenu|rappel)|[ée]tant pr[ée]alablement|pr[ée]ambule|"
                              r"expos[ée] pr[ée]alable|apr[èe]s avoir expos|article\s+(?:1|premier|i)\b|titre\s+(?:1|i|premier)\b|lesquel(?:le)?s\b|"
                              r"ci[- ]apr[èe]s (?:d[ée]nomm|d[ée]sign)[ée]e?s (?:ensemble|collectivement))", re.I)
_PART = re.compile(r"^\W*(?:et|d[’']\s*une part|d[’']\s*autre part|de (?:premi[èe]re|seconde|deuxi[èe]me|troisi[èe]me) part)\W*$", re.I)
_DENOMME = re.compile(r"ci[- ]apr[èe]s\s+(?:d[ée]nomm|d[ée]sign|appel)[ée]e?s?\s*(?:individuellement\s+)?[:«\"“”'’ ]*\s*(?:l[ea]\s+|l[’']\s*|les\s+)?"
                      r"([^»\"”,;.\n]{2,60})", re.I)


def _qualite_contrat(designation):
    plie = fold(designation).strip(' «»"“”\'’')
    for libelle, motif in QUALITES_CONTRAT:
        if re.fullmatch(motif, plie):
            return libelle
    return ''


def contractants(text):
    """Parties d'un contrat lues dans sa comparution (« ENTRE LES SOUSSIGNÉS … D'UNE PART, ET … D'AUTRE PART ») : mêmes clés que
    ``parties``, ``zone`` = 'soussignes', ``qualite`` = qualité contractuelle reconnue (« cédant », « bailleur »…) ou '' (la désignation
    écrite reste dans ``designation``). Le représentant d'une société n'est pas une partie ; rien n'est lu après la comparution (une société
    citée plus bas, dans une clause ou une jurisprudence, n'est pas une partie au contrat). Aucune partie n'est dite adverse ni cliente."""
    head = str(text or '')[:HEAD_CHARS * 2].replace('\r', '')
    debut = _SOUSSIGNES.search(head)
    if not debut:
        return []
    premiere = head[:debut.end()].count('\n')
    lines = head.split('\n')
    blocs, courant = [], []
    for j in range(premiere, min(len(lines), premiere + 120)):
        ligne = lines[j] if j > premiere else lines[j][len(lines[j]) - len(head[debut.end():].split('\n', 1)[0]):]
        if _FIN_COMPARUTION.match(ligne):
            break
        if _PART.match(ligne.strip()):
            if courant:
                blocs.append(courant)
            courant = []
        elif ligne.strip():
            courant.append((j, ligne))
    if courant:
        blocs.append(courant)
    out, seen = [], set()
    for bloc in blocs:
        j, premiere_ligne = bloc[0]
        nom = _nom_propre(premiere_ligne)
        if not (3 <= len(nom) <= 160) or _AVOCAT_LIGNE.match(nom) or not (_PHYSIQUE.match(premiere_ligne) or _MORALE.search(_name_of(premiere_ligne))
                                                                        or _is_name(premiere_ligne)):
            continue
        texte = ' '.join(l for _, l in bloc)
        m = _DENOMME.search(texte)
        designation = re.sub(r'\s+', ' ', m.group(1)).strip(' «»"“”\'’') if m else ''
        key = fold(nom)
        if key in seen:
            continue
        seen.add(key)
        extrait = re.sub(r'\s+', ' ', (premiere_ligne.strip()[:110] + ' … ' + m.group(0)) if m else premiere_ligne)
        out.append({'nom': nom, 'qualite': _qualite_contrat(designation), 'avocats': [], 'extrait': extrait[:200], 'zone': 'soussignes', 'ligne': j + 1,
                    'nom_ecrit': re.sub(r'\s+', ' ', _name_of(premiere_ligne)).strip(' :,-–')[:200], 'nature_personne': _nature_personne(premiere_ligne),
                    'representant': _representant([l for _, l in bloc]), 'cote': '', 'designation': designation[:60]})
    return out[:12]


def read(text):
    """Tout l'en-tête : {'juridiction', 'chambre', 'rg', 'portalis', 'parties', 'extraits'} ; champs vides si rien de sûr."""
    juri, juri_ex = court(text)
    rg, rg_ex = case_number(text)
    return {'juridiction': juri, 'chambre': chamber(text), 'rg': rg, 'portalis': portalis(text), 'parties': parties(text),
            'extraits': [x for x in (juri_ex, rg_ex) if x]}


def lire_acte(text):
    """5.6.109 (C10) : en-tête d'un acte de procédure (``read``) et comparution d'un contrat (``contractants``), lus avant toute mention isolée."""
    return {**read(text), 'contractants': contractants(text)}


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

"""5.6.95 (lot rédaction, D01 à D07) : un courriel rédigé en entier, des variantes là où la position de l'avocat manque.

Constat du cabinet : les projets de courriels étaient truffés de paragraphes « [À COMPLÉTER] » (une ligne par limite relevée, plus un
marqueur forcé, plus un marqueur partout où « une décision, une position, un montant ou une information » manquait). Règle décidée :

1. le courriel est rédigé **en entier** ;
2. là où la position de l'avocat manque (accepter ou refuser, montant, stratégie, concession), le projet propose **2 ou 3 variantes
   rédigées** que l'avocat garde ou supprime ;
3. « [À COMPLÉTER : …] » est réservé à un **fait absent du dossier** (date, montant, référence, nom), **au plus 2 par courriel** ;
4. l'envoi depuis AxiorHub reste bloqué tant qu'il reste une variante non choisie ou un marqueur (``sens5643.blocage_envoi``).

Ce module porte la partie commune aux quatre chemins de brouillon (préparation manuelle, réponse prudente, premier contact,
``maildraft5613``) : champs de sortie (D01), contrôle métier et demande de correction unique (D02), assemblage du corps avec des blocs
lisibles dans tout client de messagerie (D04), choix d'une variante dans AxiorHub (D05), décompte pour la mesure (D07).

Le modèle écrit dans le corps, seul sur sa ligne, le repère ``[[CHOIX n]]`` à l'endroit du passage qui dépend d'un choix ; les variantes
sont rendues en texte brut :

    ▸ Choix à faire : acceptation de la proposition de règlement
    ▸ Variante A (si vous acceptez)
    …texte complet…

    ▸ Variante B (si vous refusez)
    …texte complet…
    ▸ Fin des variantes

v2 (mesure du lot évaluation avec un vrai modèle local) : avant tout contrôle, les blocs sont **normalisés** (un point par branche
« si vous acceptez » / « si vous refusez » fusionné en un seul choix, points de même objet fusionnés, options quasi identiques
dédoublonnées) ; un point qui ne garde qu'une option devient « ▸ Proposition à confirmer » (bloquante à l'envoi, gardée ou retirée par
l'avocat) ; la signature et ses crochets (« [Votre nom] ») sont retirés ; tout autre espace entre crochets compte comme un marqueur ; un
montant ou un pourcentage d'une variante absent des sources et de l'instruction est interdit (correction ciblée, puis
« [À COMPLÉTER : montant] ») ; la demande de correction cite chaque passage fautif et la forme attendue.

Aucune variante n'est choisie par l'agent ; garder une variante est un geste de l'avocat (bouton ou modification libre).
"""
from difflib import SequenceMatcher
import hashlib
import json
import re

from .common import Stop, fold
from .sens5643 import MARKER, RELIRE, VARIANTES

MAX_MARQUEURS = 2
MAX_OPTIONS = 3
MAX_POINTS = 6
LETTRES = 'ABC'
RETIRER = '-'                                # « lettre » qui retire tout le bloc (proposition à confirmer refusée)
CHOIX = '▸ Choix à faire : %s'
VARIANTE = '▸ Variante %s (%s)'
FIN = '▸ Fin des variantes'
PROPOSITION = '▸ Proposition à confirmer : %s'
FIN_PROPOSITION = '▸ Fin de la proposition'
HEADER_FAITS = 'X-AxiorHub-A-Completer'     # en-tête historique (5.6.67), conservé
HEADER_VARIANTES = 'X-AxiorHub-Variantes'   # 5.6.95 : nombre de choix restant à faire au dépôt
REPERE = re.compile(r'\[\[\s*CHOIX\s*(\d{1,2})\s*\]\]', re.I)
MENTION = re.compile(r'^\s*\[\s*(?:à|a)\s+(?:compl[ée]ter|relire)\b[^\]]*\]\s*', re.I)
_LIGNE_CHOIX = re.compile(r'^[ \t>]*▸[ \t]*choix\s+(?:à|a)\s+faire\b[ \t]*:?[ \t]*(.*)$', re.I)
_LIGNE_VARIANTE = re.compile(r'^[ \t>]*▸[ \t]*variante[ \t]+([a-z])\b[ \t]*(?:\((.*)\))?.*$', re.I)
_LIGNE_FIN = re.compile(r'^[ \t>]*▸[ \t]*fin\s+des\s+variantes\b.*$', re.I)
_LIGNE_PROPOSITION = re.compile(r'^[ \t>]*▸[ \t]*proposition\s+(?:à|a)\s+confirmer\b[ \t]*:?[ \t]*(.*)$', re.I)
_LIGNE_FIN_PROPOSITION = re.compile(r'^[ \t>]*▸[ \t]*fin\s+de\s+la\s+proposition\b.*$', re.I)
_FORMULE_FIN = re.compile(r'^\s*(?:je vous prie|veuillez agr[ée]er|bien (?:à vous|cordialement)|tr[èe]s cordialement|cordialement|'
                          r'recevez|dans l.attente|avec mes salutations|bien sinc[èe]rement|bien confraternellement|confraternellement)', re.I)
# Un marqueur décrit un FAIT absent (date, montant, référence, nom) ; ces formulations désignent une POSITION, qui appelle des variantes.
_POSITION = re.compile(r"\bposition\b|\bdecider\b|decision (?:de l.avocat|a prendre|du cabinet)|votre decision|\bchoix\b|\bchoisir\b|\barbitr|"
                       r"\btrancher\b|\baccepter\b|\brefuser\b|strategi|concession|conceder|\btransiger\b|souhait|\bprefer|opportunite|"
                       r"faut-il|\bou non\b|a proposer|a offrir|proposition a (?:faire|formuler)|instructions? de l.avocat|accord de l.avocat|"
                       r"validation de l.avocat|negoci")
# v2 : intitulé d'un point qui n'est qu'une branche (« Si vous acceptez… ») : le modèle a fait un point par option.
_BRANCHE = re.compile(r"^(?:si|s.il|s.ils|en cas|dans le cas|dans l.hypothese|hypothese|au cas|variante|option)\b")
_TETE_BRANCHE = re.compile(r"^(?:si|s.il|s.ils|en cas d.|en cas de|dans le cas ou|dans l.hypothese ou|au cas ou)\s+"
                           r"(?:(?:vous|nous|le client|la cliente|votre client|votre cliente|il|elle|ils|elles|on)\s+)?"
                           r"(?:n.|ne\s+)?(?:\S+)\s*(?:pas\s+)?(?:(?:de|d|la|le|les|l|du|des|a|au)\b\s*)?", re.I)
# v2 : espaces entre crochets laissés par le modèle (« [Votre nom] », « [date_suggeree] ») ; les marqueurs officiels n'en sont pas.
_TROU = re.compile(r'(?<!\[)\[(?!\[)\s*(?!(?:à|a)\s+(?:compl[ée]ter|v[ée]rifier|relire)\b)([^\[\]\n]{1,120}?)\s*\](?!\])', re.I)
_PAS_UN_TROU = re.compile(r'^(?:…|\.{2,}|sic|[A-Z]+_\d+|\d{1,3}|choix\s*\d{1,2}(?:\s*[.,]\s*\d{1,2})?)$', re.I)   # v3.1 : repère de choix
_SIGNATURE = re.compile(r"nom|pr[ée]nom|signature|avocat|ma[iî]tre|cabinet|coordonn|t[ée]l[ée]phone|adresse|fonction|titre|e-?mail|courriel|"
                        r"toque|barreau|ville|valid[ée] par l|[ée]pingl", re.I)
_LIGNE_SIGNATURE = re.compile(r"^(?:ma[iî]tre|me\b|avocate?s?\b|le cabinet|cabinet|t[ée]l|t[ée]l[ée]phone|e-?mail|courriel|adresse|toque|barreau|"
                              r"pour le cabinet|signature)", re.I)
_POURCENT = re.compile(r'(?<![\w.,])(\d{1,3}(?:[.,]\d{1,2})?)\s?(?:%|pour\s?cent)', re.I)

MESSAGES = {
    'version_perimee': 'Le texte a changé depuis l’affichage de la page : rien n’a été modifié. Rechargez la page puis refaites votre choix.',
    'choix_introuvable': 'Ce choix n’existe plus dans le texte : rechargez la page.',
    'variante_introuvable': 'Cette variante n’existe plus dans le texte : rechargez la page.',
    'variante_vide': 'Cette variante est vide : modifiez directement le texte.',
    'bloc_variantes_incomplet': 'Le bloc de variantes a été modifié à la main (ligne « ▸ Fin des variantes » absente) : finissez le choix directement dans le texte.',
    'trop_de_marqueurs_a_completer': 'Plus de 2 faits restent « à compléter » : relisez et complétez le texte avant de le déposer.',
    'brouillon_variante_a_choisir': 'Ce brouillon contient encore des variantes ou une proposition à confirmer : gardez ou retirez chacune avant tout envoi.',
    'brouillon_a_relire': 'Ce brouillon porte encore la mention « [À RELIRE] » : relisez-le et retirez la mention avant tout envoi.',
}

S = {'type': 'string'}


def _obj(props, required=None):
    return {'type': 'object', 'properties': props, 'required': list(required if required is not None else props), 'additionalProperties': False}


def champs_sortie(strict=False):
    """D01 : champs ``variantes`` et ``a_completer``. ``strict`` : forme transmise au fournisseur (2 marqueurs, 3 options au plus) ;
    sinon forme de lecture, plus large, pour que le contrôle métier puisse signaler l'écart et demander une correction au lieu d'échouer."""
    option = _obj({'libelle': S, 'texte': S})
    point = _obj({'point': S, 'options': {'type': 'array', 'items': option, 'maxItems': MAX_OPTIONS if strict else 8}})
    fait = _obj({'quoi': S, 'pourquoi': S})
    return {'variantes': {'type': 'array', 'items': point, 'maxItems': MAX_POINTS if strict else 12},
            'a_completer': {'type': 'array', 'items': fait, 'maxItems': MAX_MARQUEURS if strict else 12}}


# --------------------------------------------------------------------------------------------- lecture (D01, compatibilité)
def _txt(value, limit=4000):
    return re.sub(r'[ \t]+', ' ', str(value or '')).strip()[:limit]


def normaliser(result):
    """Copie du résultat avec ``variantes`` et ``a_completer`` toujours présents ; un ancien résultat (sans ces champs) se lit tel quel.
    Les marqueurs écrits dans le corps sans entrée ``a_completer`` y sont repris, pour la fiche de contrôle."""
    out = dict(result or {})
    points = []
    for item in out.get('variantes') or []:
        if not isinstance(item, dict):
            continue
        options = [{'libelle': _txt(o.get('libelle'), 160), 'texte': str(o.get('texte') or '').strip()[:4000]}
                   for o in (item.get('options') or []) if isinstance(o, dict)]
        points.append({'point': _txt(item.get('point'), 300), 'options': options})
    out['variantes'] = points
    faits = []
    for item in out.get('a_completer') or []:
        if isinstance(item, dict):
            faits.append({'quoi': _txt(item.get('quoi'), 300), 'pourquoi': _txt(item.get('pourquoi'), 300)})
        elif str(item or '').strip():                      # ancienne liste de chaînes (maildraft5613 avant 5.6.95)
            faits.append({'quoi': _txt(item, 300), 'pourquoi': ''})
    if not faits:
        for m in MARKER.finditer(str(out.get('body') or '')):
            what = re.sub(r'^\[\s*(?:à|a)\s+compl[ée]ter\s*:?\s*', '', m.group(0), flags=re.I).rstrip(']').strip()
            faits.append({'quoi': what[:300] or 'information manquante', 'pourquoi': 'absent des sources transmises'})
    out['a_completer'] = faits
    return out


def est_position(texte):
    """Vrai si la formulation désigne une position de l'avocat (accepter, refuser, stratégie, concession…) et non un fait absent."""
    return bool(_POSITION.search(fold(str(texte or ''))))


def _tous_les_textes(result):
    return [str(result.get('body') or '')] + [o['texte'] for p in result.get('variantes') or [] for o in p.get('options') or []]


def trous(texte):
    """v2 : espaces à remplir entre crochets (hors marqueurs officiels, repères [[CHOIX n]] et pseudonymes)."""
    return [m for m in _TROU.finditer(str(texte or '')) if not _PAS_UN_TROU.match(m.group(1).strip())]


def marqueurs(result):
    """Marqueurs « [À COMPLÉTER : …] » et, depuis la v2, tout autre espace à remplir entre crochets (corps et variantes)."""
    return [m.group(0) for t in _tous_les_textes(result) for m in list(MARKER.finditer(t)) + trous(t)]


def marqueurs_depot(texte):
    """Marqueurs d'un corps déjà assemblé (les options y figurent une seule fois) : « [À COMPLÉTER » et espaces entre crochets."""
    return len(MARKER.findall(str(texte or ''))) + len(trous(texte))


def _sources_references(sources, incoming=None):
    out = [{'titre': '', 'reference': str(s.get('reference') or '') if isinstance(s, dict) else '',
            'extrait': json.dumps(s, ensure_ascii=False, default=str)[:20000]} for s in (sources or [])]
    if incoming:
        out.append({'titre': '', 'reference': '', 'extrait': json.dumps(incoming, ensure_ascii=False, default=str)[:20000]})
    return out


def references_absentes(result, sources, incoming=None):
    """Articles et décisions cités (corps et variantes) qui ne figurent dans aucune source transmise (même contrôle que 5.6.51)."""
    from .juridique5649 import unsourced_references
    text = '\n'.join(_tous_les_textes(result))
    found = unsourced_references(text, {'sources': _sources_references(sources, incoming)})
    return [x for x in found if not x.startswith('contrôle des références incomplet')]


# --------------------------------------------------------------------------------------------- v2 : normalisation avant contrôle
def _cle(texte):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', fold(str(texte or '')))).strip()


def semblables(a, b, seuil=0.9):
    """Deux textes quasi identiques (après pliage des accents, de la casse et de la ponctuation)."""
    a, b = _cle(a), _cle(b)
    if not a or not b:
        return a == b
    return a == b or SequenceMatcher(None, a, b).ratio() >= seuil


def _est_branche(intitule):
    return bool(_BRANCHE.match(_cle(intitule)))


def _objet_branche(intitule):
    return _TETE_BRANCHE.sub('', str(intitule or '').strip(), count=1).strip(' ,.:;')


def _libelle_generique(libelle):
    return not _cle(libelle) or bool(re.fullmatch(r'(?:variante|option|texte|proposition)\s*[a-z0-9]?', _cle(libelle)))


def dedoublonner(options):
    """Options quasi identiques retirées (la première est gardée) ; libellés rendus distincts ; au plus 3."""
    kept = []
    for o in options:
        if not str(o.get('texte') or '').strip():
            continue
        if any(semblables(o['texte'], k['texte']) for k in kept):
            continue
        kept.append(dict(o))
    seen = set()
    for n, o in enumerate(kept):
        lib = o.get('libelle') or 'variante %s' % LETTRES[min(n, 2)]
        while _cle(lib) in seen:
            lib = lib + ' (autre)'
        seen.add(_cle(lib))
        o['libelle'] = lib
    return kept[:MAX_OPTIONS]


def consolider(variantes):
    """Rend ``(points, correspondance)`` : points de branche consécutifs (un point « si… » à une seule option) fusionnés en un choix,
    points de même objet fusionnés, options dédoublonnées. ``correspondance`` : ancien rang → nouveau rang (repères [[CHOIX n]])."""
    pts = normaliser({'variantes': variantes})['variantes']
    out, mapping, group = [], {}, None
    for n, p in enumerate(pts, 1):
        options = [o for o in p['options'] if o['texte'].strip()]
        if len(options) == 1 and _est_branche(p['point']):
            o = dict(options[0])
            if _libelle_generique(o['libelle']) or not _est_branche(o['libelle']):
                o['libelle'] = p['point'][:160]
            if group is None or len(group['options']) >= MAX_OPTIONS:
                group = {'point': '', 'options': [], 'sujets': []}
                out.append(group)
            group['options'].append(o)
            group['sujets'].append(_objet_branche(p['point']))
            mapping[n] = len(out)
            continue
        group = None
        same = next((i for i, q in enumerate(out, 1) if q.get('point') and semblables(q['point'], p['point'], 0.85)), None)
        if same:
            out[same - 1]['options'] += options
            mapping[n] = same
            continue
        out.append({'point': p['point'], 'options': options})
        mapping[n] = len(out)
    for q in out:
        if 'sujets' in q:
            sujet = max(q.pop('sujets'), key=len, default='')
            q['point'] = sujet[:300] if len(sujet) >= 4 else 'position de l’avocat'
        q['options'] = dedoublonner(q['options'])
    return [q for q in out if q['options']], mapping


def remapper(body, mapping, total):
    """Repères [[CHOIX n]] du corps renumérotés après fusion ; un repère vers un point vide est retiré."""
    def put(m):
        n = mapping.get(int(m.group(1)))
        return '[[CHOIX %d]]' % n if n and n <= total else ''
    return REPERE.sub(put, str(body or ''))


# v3 : identité de l'expéditeur et appel du destinataire entre crochets, marqueur officiel compris (« [À COMPLÉTER : Votre nom] »,
# « Cher [Confrère] ») : jamais un fait absent, donc jamais un marqueur.
_CROCHET = re.compile(r'\[\s*(?:(?:à|a)\s+compl[ée]ter\s*:?\s*)?([^\[\]\n]{1,120}?)\s*\]', re.I)
_APPEL = re.compile(r'^(?:cher|ch[èe]re|mon cher|ma ch[èe]re)?\s*(confr[èe]re|cons(?:œ|oe)ur|ma[iî]tre|madame|monsieur|madame,? monsieur|client|cliente)$', re.I)
_IDENTITE = re.compile(r"\bvotre nom\b|\bvos? (?:nom|pr[ée]nom|coordonn\w*|signature|titre|fonction|adresse|t[ée]l\w*|e-?mail|cabinet)\b|"
                       r"^(?:votre |ma |la )?signature(?: de l.avocat| du cabinet)?$|pr[ée]nom\s+(?:et\s+)?nom|nom\s+(?:et\s+)?pr[ée]nom|^nom$|^pr[ée]nom$|\bnom (?:de l.avocat|du cabinet|de l.exp[ée]diteur)\b|"
                       r"\bcoordonn\w* du cabinet\b|\bnom de l.avocat\b|^avocat$|^cabinet$|^ville$|^t[ée]l[ée]phone$|^e-?mail$|^adresse e-?mail$", re.I)
_LIGNE_VIDE_IDENTITE = re.compile(r"^(?:ma[iî]tre|me|avocate?|avocat au barreau de|le cabinet|cabinet|pour le cabinet)?[\s,.;:–—-]*$", re.I)
_ECHO = re.compile(r'^\s*(?:-{3,}\s*$|(?:a_completer|requires_decision|source_ids|limits|variantes|body)\s*:)', re.I)
_NEG = re.compile(r"\b(?:ne|n|pas|refus\w*|oppos\w*|declin\w*|mainten\w*|rejet\w*|impossible|aucun\w*|sans suite|conteste\w*)\b")
_HEURE = re.compile(r'(?<![\w/])(?:[01]?\d|2[0-3])\s?(?:h|heures?)\s?(?:[0-5]\d)?(?![\w/])|(?<![\w/:])(?:[01]?\d|2[0-3]):[0-5]\d(?![\w:])', re.I)


def nettoyer_identite(texte):
    """« Cher [Confrère] » devient « Cher Confrère » ; tout crochet d'identité ou de signature de l'expéditeur est retiré, avec la ligne
    qu'il laisse vide (« Me [Nom de l'avocat] »). Le reste du texte n'est pas touché."""
    def put(m):
        inner = m.group(1).strip()
        if _APPEL.match(inner):
            return _APPEL.match(inner).group(1)
        if 'advers' not in fold(inner) and _IDENTITE.search(inner):
            return ''
        return m.group(0)
    out = []
    for line in str(texte or '').split('\n'):
        new = _CROCHET.sub(put, line)
        if new != line and _LIGNE_VIDE_IDENTITE.match(new.strip()):
            continue
        bare = new.strip(' \t,.;:')
        if new != line and any(x.strip() for x in out) and _APPEL.match(bare) and not re.match(r'(?i)^(?:cher|ch[èe]re|mon|ma)\b', bare):
            continue                                           # « [Confrère] » seul en fin de courriel : une signature, pas un appel
        out.append(re.sub(r'[ \t]{2,}', ' ', new).rstrip() if new != line else line)
    return '\n'.join(out)


def retirer_echos(body):
    """Le modèle recopie parfois des champs du schéma dans le courriel (« --- / a_completer: … ») : tout ce qui suit est retiré."""
    lines = str(body or '').split('\n')
    at = next((i for i, ln in enumerate(lines) if _ECHO.match(ln) and (not ln.strip().startswith('-') or
                any(re.match(r'^\s*(?:a_completer|requires_decision|source_ids|limits|variantes)\s*:', x, re.I) for x in lines[i + 1:i + 4]))), None)
    return '\n'.join(lines[:at]).rstrip() if at is not None else str(body or '')


def retirer_signature(body):
    """« Pas de signature » : crochets d'identité retirés partout, puis les dernières lignes de signature (« [Votre nom] », « Avocat au
    barreau de [ville] ») ; la signature du cabinet est ajoutée par la messagerie, jamais par l'IA."""
    lines = nettoyer_identite(body).rstrip().split('\n')
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        found = trous(last)
        rest = _TROU.sub('', last).strip(' ,.;:-–—')
        if found and (all(_SIGNATURE.search(m.group(1)) for m in found) and (not rest or _LIGNE_SIGNATURE.match(rest))
                      or (rest and _LIGNE_SIGNATURE.match(rest))):
            lines.pop()
            continue
        break
    return '\n'.join(lines).rstrip()


def _texte_d_option(texte):
    """Une option s'insère au milieu du courriel : ni signature, ni formule de politesse finale."""
    lines = retirer_signature(texte).split('\n')
    while lines and (not lines[-1].strip() or _FORMULE_FIN.match(lines[-1])):
        lines.pop()
    return '\n'.join(lines).strip()


_SENS_LIBELLE = re.compile(r'\b(?:accept\w*|refus\w*|oui|non|maintien\w*|renonc\w*|conteste\w*)\b')


def _position(option, commun):
    """Sens d'une option : négation ou refus dans les mots qui lui sont PROPRES (les mots communs à toutes les options, comme l'objet
    « accord ou refus du renvoi », ne comptent pas), sens de son libellé, montants et pourcentages."""
    from .preuve5634 import amounts_in_text
    propres = ' '.join(w for w in _cle(option['texte']).split() if w not in commun)
    return (bool(_NEG.search(propres)), tuple(sorted(set(_SENS_LIBELLE.findall(_cle(option.get('libelle')))))),
            frozenset(amounts_in_text(option['texte'])), frozenset(_pourcents(option['texte'])))


def formulation_seule(options):
    """v3 : options qui ne diffèrent que par la formulation (même sens, mêmes montants, textes proches) : ce n'est pas une décision."""
    if len(options) < 2:
        return False
    commun = set.intersection(*(set(_cle(o['texte']).split()) for o in options))
    first = _position(options[0], commun)
    return all(_position(o, commun) == first for o in options[1:]) and all(
        semblables(options[0]['texte'], o['texte'], 0.6) for o in options[1:])


def _inserer(text, insert):
    lines = text.split('\n')
    at = next((i for i in range(len(lines) - 1, max(-1, len(lines) - 12), -1) if _FORMULE_FIN.match(lines[i])), None)
    if at is None:
        return text.rstrip() + '\n\n' + insert
    return '\n'.join(lines[:at]).rstrip() + '\n\n' + insert + '\n\n' + '\n'.join(lines[at:])


def _points(body, variantes):
    """Normalisation commune : fusion, dédoublonnage, puis un point « formulation seule » versé dans le corps (première option, sans bloc)."""
    points, mapping = consolider(variantes)
    body = remapper(body, mapping, len(points))
    keep, again = [], {}
    for n, p in enumerate(points, 1):
        if formulation_seule(p['options']):
            text = p['options'][0]['texte'].strip()
            found = [False]

            def put(m, n=n, text=text, found=found):
                if int(m.group(1)) != n:
                    return m.group(0)
                if found[0]:
                    return ''
                found[0] = True
                return '\n\n' + text + '\n\n'
            body = REPERE.sub(put, body)
            if not found[0]:
                body = _inserer(body, text)
            continue
        keep.append(p)
        again[n] = len(keep)
    body = remapper(body, again, len(keep))
    return re.sub(r'\n{3,}', '\n\n', body).strip(), keep


def _sans_doublons(body, variantes):
    """Paragraphe du corps qui recopie une option (« Si vous acceptez, nous… » écrit aussi hors du bloc) : retiré."""
    textes = [o['texte'] for p in variantes for o in p['options'] if len(o['texte']) >= 30]
    if not textes:
        return body
    keep = []
    for para in re.split(r'\n\s*\n', str(body or '')):
        bare = re.sub(r'^\s*(?:si (?:vous|nous)\s+\S+(?:\s+pas)?\s*,?\s*)', '', para, flags=re.I)
        if not REPERE.search(para) and any(semblables(bare, t, 0.85) or semblables(para, t, 0.85) for t in textes):
            continue
        keep.append(para)
    return '\n\n'.join(keep)


# v3.1 (cas R27) : repères de choix écrits par le modèle sous toutes les formes : [[CHOIX n]], [CHOIX n], [CHOIX n.m], [À COMPLÉTER : CHOIX n.m].
# Jamais un fait à compléter : un repère de placement d'un bloc existant le place ; tout autre repère est retiré, sans inventer de point.
_REF = re.compile(r'\[\[?\s*(?:(?:à|a)\s+compl[ée]ter\s*:?\s*)?choix\s*(\d{1,2})(?:\s*[.,]\s*(\d{1,2}))?\s*\]\]?', re.I)
_REF_NU = re.compile(r'\bchoix\s*\d{1,2}(?:\s*[.,]\s*\d{1,2})?\b', re.I)   # « CHOIX 2.1 » sans crochets (intitulé, libellé)
_ENTETE_OPTIONS = re.compile(r'^\s*(?:options?|variantes?|choix)\s*:?\s*$', re.I)
_PUCE = re.compile(r'^\s*(?:[-–—•*]|\d+[.)])\s+')


def _sans_ref(texte):
    return re.sub(r'[ \t]{2,}', ' ', _REF.sub('', str(texte or ''))).strip(' \t:;,–—-') if _REF.search(str(texte or '')) else str(texte or '')


def traiter_reperes(body, total):
    """Ligne faite d'un seul repère « CHOIX n » d'un bloc existant : [[CHOIX n]] (placement) ; ligne qui énumère une option
    (« - [CHOIX 1.2] : … ») : retirée (les options vivent dans leur bloc, ou nulle part) ; repère au milieu d'une phrase : retiré ;
    en-tête « Options : » resté sans liste : retiré."""
    kept = []
    for line in str(body or '').split('\n'):
        refs = list(_REF.finditer(line))
        if not refs:
            kept.append(line)
            continue
        rest = _REF.sub('', line).strip(' \t-–—•*:;,.')
        if any(m.group(2) for m in refs):
            continue
        n = int(refs[0].group(1))
        if not rest:
            if 1 <= n <= total:
                kept.append('[[CHOIX %d]]' % n)
            continue
        kept.append(re.sub(r'[ \t]{2,}', ' ', _REF.sub('', line)).rstrip())
    out = []
    for i, line in enumerate(kept):
        if _ENTETE_OPTIONS.match(line):
            following = next((x for x in kept[i + 1:] if x.strip()), '')
            if not _PUCE.match(following):
                continue
        out.append(line)
    return '\n'.join(out)


def preparer(result):
    """Forme canonique avant contrôle : échos du schéma et signature retirés (corps et options), blocs normalisés (v2), options sans
    formule finale, points de simple formulation versés dans le corps, paragraphes recopiant une option retirés (v3)."""
    source = normaliser(result)
    source['body'] = traiter_reperes(retirer_signature(retirer_echos(source.get('body'))), len(source['variantes']))
    source['a_completer'] = [f for f in (result or {}).get('a_completer') or []
                             if not _REF_NU.search(str(f.get('quoi') or '') if isinstance(f, dict) else str(f))]   # un repère n'est jamais un fait
    for p in source['variantes']:                             # v3.1 : aucun repère dans l'intitulé, le libellé ou le texte d'une option
        p['point'] = _REF_NU.sub('', _sans_ref(p['point'])).strip(' \t:;,–—-')
        for o in p['options']:
            o['libelle'], o['texte'] = _REF_NU.sub('', _sans_ref(o['libelle'])).strip(' \t:;,–—-()'), _sans_ref(o['texte'])
    r = normaliser(source)                                    # faits repris du corps APRÈS retrait de l'identité et de la signature
    r['a_completer'] = [f for f in r['a_completer'] if not (_IDENTITE.search(f['quoi']) and 'advers' not in fold(f['quoi']))
                        and not _APPEL.match(f['quoi'].strip(' .,'))]
    for p in r['variantes']:
        for o in p['options']:
            o['texte'] = _texte_d_option(o['texte'])
    r['body'], r['variantes'] = _points(r['body'], r['variantes'])
    r['body'] = _sans_doublons(r['body'], r['variantes'])
    return r


def _pourcents(texte):
    return {m.group(1).replace(',', '.') for m in _POURCENT.finditer(str(texte or ''))}


def _heures(texte):
    return {re.sub(r'\s|heures?|h|:', '', m.group(0).lower()).lstrip('0') for m in _HEURE.finditer(str(texte or ''))}


def montants_inventes(texte, reference):
    """Montants, pourcentages et heures d'un texte absents de la référence (sources transmises et instruction de l'avocat) :
    [(début, fin, nature)]. Une heure inventée (créneau proposé) est un fait absent, jamais une formulation (v3)."""
    from .preuve5634 import amounts_in_text, money_in_text
    known = amounts_in_text(reference)
    out = [(o['debut'], o['fin'], 'montant') for o in money_in_text(texte) if (str(o['valeur']), o['devise']) not in known]
    pct = _pourcents(reference)
    out += [(m.start(), m.end(), 'pourcentage') for m in _POURCENT.finditer(str(texte or '')) if m.group(1).replace(',', '.') not in pct]
    hours = _heures(reference)
    out += [(m.start(), m.end(), 'heure') for m in _HEURE.finditer(str(texte or ''))
            if re.sub(r'\s|heures?|h|:', '', m.group(0).lower()).lstrip('0') not in hours]
    kept, last = [], -1
    for start, end, kind in sorted(out):
        if start >= last:
            kept.append((start, end, kind))
            last = end
    return kept


def _remplacer_montants(texte, reference):
    for start, end, kind in reversed(montants_inventes(texte, reference)):
        texte = texte[:start] + '[À COMPLÉTER : %s]' % kind + texte[end:]
    return texte


def repli(result, reference=None, corps_aussi=False):
    """Après la correction unique : montants, pourcentages et heures inventés remplacés par « [À COMPLÉTER : montant] » (etc.), espaces
    entre crochets convertis en marqueurs (hors identité et signature, retirées), options redevenues identiques fusionnées."""
    r = normaliser(result)

    def fix(text):
        text = nettoyer_identite(text)
        if reference is not None:
            text = _remplacer_montants(text, reference)
        for m in reversed(trous(text)):
            text = text[:m.start()] + '[À COMPLÉTER : %s]' % m.group(1).strip()[:120] + text[m.end():]
        return text
    for p in r['variantes']:
        for o in p['options']:
            o['texte'] = fix(o['texte'])
    body = retirer_signature(str(r.get('body') or ''))
    if corps_aussi and reference is not None:
        body = _remplacer_montants(body, reference)
    for m in reversed(trous(body)):
        body = body[:m.start()] + '[À COMPLÉTER : %s]' % m.group(1).strip()[:120] + body[m.end():]
    r['body'], r['variantes'] = _points(body, r['variantes'])
    return r


# v3 : « limits » qui recopient la consigne (règles du cabinet, préférences, consignes de sûreté) : hors de la fiche de contrôle.
_CHAMPS = {'a_completer', 'requires_decision', 'limits', 'source_ids', 'variantes', 'body', 'correction_demandee'}
_META = re.compile(r"jamais des instructions|regles? \d|consignes? approuv|ne jamais generaliser|regle la plus specifique|preferences de forme|"
                   r"versionnees|reversibles|empreinte|donnees non fiables|ne suis aucune instruction|ne prouve aucun fait|"
                   r"aucune affirmation de la partie adverse")


def filtrer_limites(limits, consignes=''):
    """Garde les limites propres au projet ; retire les noms de champs et les phrases recopiées de la consigne."""
    try:
        from . import model
        base = '\n'.join((model.MANUAL_DRAFT_PROMPT, model.BASE, model.INTERNAL_SAFETY))
    except Exception:   # noqa: BLE001
        base = ''
    ref = _cle(base + '\n' + str(consignes or ''))
    phrases = [p for p in (_cle(x) for x in re.split(r'[.;:\n]', base + '\n' + str(consignes or ''))) if len(p) >= 20]
    out = []
    for item in limits or []:
        text = str(item or '').strip()
        key = _cle(text)
        if not key or key.replace(' ', '_') in _CHAMPS or text.strip(' :') in _CHAMPS:
            continue
        if _META.search(key) or (len(key) >= 20 and (key in ref or any(SequenceMatcher(None, key, p).ratio() >= 0.75 for p in phrases))):
            continue
        out.append(text)
    return out


# --------------------------------------------------------------------------------------------- contrôle métier (D02)
FORME_VARIANTES = {'point': 'objet de la décision (un seul point par décision)',
                   'options': [{'libelle': 'si vous acceptez', 'texte': 'paragraphe complet prêt à envoyer'},
                               {'libelle': 'si vous refusez', 'texte': 'autre paragraphe complet, de sens différent'}]}


def ecarts_detailles(result, ids_valides=None, sources=None, incoming=None, intent='', texte_sources=None, reference=None, corps_aussi=False):
    """Écarts à la règle du cabinet : [{'ecart', 'passage', 'attendu'}] (liste vide si conforme). Ne modifie rien.
    ``texte_sources`` : sources citées par leur nom (``maildraft5613``) ; ``reference`` : texte des sources et de l'instruction de l'avocat
    contre lequel les montants et pourcentages des variantes sont vérifiés."""
    r = normaliser(result)
    out = []

    def add(ecart, passage='', attendu=''):
        out.append({'ecart': ecart, 'passage': str(passage)[:400], 'attendu': attendu})
    if not str(r.get('body') or '').strip():
        add('corps vide : le courriel doit être rédigé en entier', '', 'body = le courriel entier, de l’appel à la formule de politesse')
    officiels = [m.group(0) for t in _tous_les_textes(r) for m in MARKER.finditer(t)]
    holes = [m.group(0) for t in _tous_les_textes(r) for m in trous(t)]
    if len(officiels) + len(holes) > MAX_MARQUEURS:
        add('%d marqueurs ou espaces à remplir : au plus %d, réservés aux faits absents du dossier' % (len(officiels) + len(holes), MAX_MARQUEURS),
            ' ; '.join(officiels + holes), 'rédiger ces passages en texte complet ; garder au plus 2 « [À COMPLÉTER : fait précis] »')
    if len(r['a_completer']) > MAX_MARQUEURS:
        add('%d faits à compléter déclarés : au plus %d' % (len(r['a_completer']), MAX_MARQUEURS), '', 'a_completer : au plus 2 faits absents')
    for m in officiels:
        if est_position(m):
            add('marqueur qui désigne une position et non un fait (à rédiger en variantes)', m,
                'supprimer ce marqueur ; ajouter un point dans variantes (2 options rédigées) et le repère [[CHOIX n]] dans body')
    for m in holes:
        add('espace à remplir entre crochets', m, 'écrire le texte ; si c’est un fait absent des sources : « [À COMPLÉTER : ce qui manque] » ; '
            'pas de signature ni de « [Votre nom] »')
    for item in r['a_completer']:
        if not item['quoi']:
            add('fait à compléter sans description de ce qui manque', '', 'quoi = le fait précis qui manque')
        elif est_position(item['quoi'] + ' ' + item['pourquoi']):
            add('« à compléter » qui désigne une position et non un fait (à rédiger en variantes)', item['quoi'],
                'retirer de a_completer ; en faire un point de variantes')
    if intent == 'demande_precision' and r['variantes']:
        add('demande de précisions : pas de variantes, seulement des questions', '', 'variantes = []')
    for n, point in enumerate(r['variantes'], 1):
        name = point['point'] or 'point %d' % n
        options = point['options']
        apercu = ' | '.join('%s : %s' % (o['libelle'], o['texte'][:120]) for o in options)
        if not point['point']:
            add('choix %d sans intitulé' % n, apercu, 'point = objet de la décision')
        if not 2 <= len(options) <= MAX_OPTIONS:
            add('choix « %s » : %d variante(s), il en faut 2 ou 3 de sens différent (sinon il sera présenté comme « proposition à confirmer »)'
                % (name[:120], len(options)), apercu, json.dumps(FORME_VARIANTES, ensure_ascii=False))
        if any(not o['texte'] or not o['libelle'] for o in options):
            add('choix « %s » : variante sans libellé ou sans texte' % name[:120], apercu, 'libelle et texte non vides')
        if any(semblables(a['texte'], b['texte']) for i, a in enumerate(options) for b in options[i + 1:]):
            add('choix « %s » : variantes identiques' % name[:120], apercu, 'options de sens différent (accepter / refuser…)')
        if any(MARKER.search(o['texte']) and est_position(o['texte']) for o in options):
            add('choix « %s » : une variante renvoie la décision à un marqueur' % name[:120], apercu, 'chaque option rédigée en entier')
        if reference is not None:
            for o in options:
                for start, end, kind in montants_inventes(o['texte'], reference):
                    add('choix « %s », variante « %s » : %s absent des sources et de votre instruction' % (name[:80], o['libelle'][:60], kind),
                        o['texte'][max(0, start - 60):end + 20], 'ne citer qu’un montant présent dans les sources ou l’instruction ; '
                        'sinon formuler sans chiffre')
    if corps_aussi and reference is not None:
        body = str(r.get('body') or '')
        for start, end, kind in montants_inventes(body, reference):
            add('%s du corps absent des sources et de l’instruction' % kind, body[max(0, start - 60):end + 20], 'retirer ce chiffre ou citer la source')
    if ids_valides is not None:
        unknown = [x for x in (r.get('source_ids') or []) if x not in set(ids_valides)]
        if unknown:
            add('source(s) citée(s) inexistante(s) : %s' % ', '.join(str(x)[:40] for x in unknown[:6]), ', '.join(map(str, unknown[:6])),
                'source_ids : uniquement des identifiants présents dans sources')
    if texte_sources is not None:
        known = fold(str(texte_sources))
        unknown = [x for x in (r.get('source_ids') or []) if str(x).strip() and fold(str(x)).strip() not in known]
        if unknown:
            add('source(s) citée(s) absente(s) du contexte : %s' % ', '.join(str(x)[:40] for x in unknown[:6]), ', '.join(map(str, unknown[:6])),
                'sources : uniquement des pièces présentes dans le contexte')
    if sources is not None:
        refs = references_absentes(r, sources, incoming)
        if refs:
            add('article ou décision absent des sources : %s' % ' ; '.join(refs[:6]), ' ; '.join(refs[:6]),
                'retirer la référence ou nommer le régime sans numéro')
    seen, unique = set(), []
    for d in out:
        if (d['ecart'], d['passage']) not in seen:
            seen.add((d['ecart'], d['passage']))
            unique.append(d)
    return unique


def controler(result, **contexte):
    """Écarts à la règle du cabinet, en phrases courtes (liste vide si conforme). Ne modifie rien."""
    return list(dict.fromkeys(d['ecart'] for d in ecarts_detailles(result, **contexte)))


CONSIGNE_CORRECTION = ('Le projet ci-joint ne respecte pas la règle du cabinet. Corrige UNIQUEMENT les écarts listés dans « ecarts » : pour chacun, '
                       '« passage » cite le texte fautif et « attendu » la forme à produire. Ne change rien d’autre. Rappels : courriel entier ; '
                       'une décision = UN seul point de variantes avec 2 ou 3 options de sens différent (jamais un point par option) et le repère '
                       '[[CHOIX n]] dans body ; « [À COMPLÉTER : …] » seulement pour un fait absent des sources, au plus 2 ; aucun autre crochet, '
                       'aucune signature ; aucun montant ni pourcentage absent des sources et de l’instruction ; chaque option respecte les '
                       'décisions validées de l’avocat ; aucun article ni arrêt absent des sources. Rends l’objet JSON complet.')


def _projet(result):
    return {k: result.get(k) for k in ('body', 'variantes', 'a_completer', 'source_ids', 'limits', 'requires_decision')}


def corriger(modele, payload, result, ecarts, schema, stage='manual_draft', extra=(), **contexte):
    """Une seule demande de correction ciblée, par ``Model.ask`` (politique, pause, horaires, budget de la mission). Rend
    ``(résultat retenu, écarts restants, état)``. État : ``appliquee`` (plus aucun écart : seule issue comptée comme réussie), ``partielle``
    (moins d'écarts), ``sans effet`` (autant d'écarts ou plus, ou corps vidé : le projet d'origine est gardé), ``impossible : …``.
    Un arrêt ou une pause demandés pendant l'appel interrompent le travail."""
    from .arret5643 import STOP_CODES
    from .model import validate
    details = [{'ecart': e, 'passage': '', 'attendu': 'sortie conforme au schéma'} for e in extra] + ecarts_detailles(result, **contexte)
    request = {**payload, 'correction_demandee': {'projet': _projet(result), 'ecarts': details[:20], 'forme_variantes': FORME_VARIANTES,
                                                  'consigne': CONSIGNE_CORRECTION}}
    ask = getattr(modele, 'ask', None)
    if ask is None:
        return result, list(ecarts), 'impossible : modele_sans_correction'
    try:
        fixed = ask(stage, request)
        validate(fixed, schema)
    except Stop as error:
        if str(error).split(':')[0] in STOP_CODES:
            raise
        return result, list(ecarts), 'impossible : %s' % str(error).split(':')[0]
    fixed = preparer(fixed)
    rest = controler(fixed, **contexte)
    if not str(fixed.get('body') or '').strip() and str(result.get('body') or '').strip():
        return result, list(ecarts), 'sans effet'          # une correction qui vide le courriel n'est jamais retenue
    if rest and len(rest) >= len(ecarts):
        return result, list(ecarts), 'sans effet'
    return fixed, rest, ('appliquee' if not rest else 'partielle')


def verifier(modele, payload, result, schema, stage='manual_draft', extra=(), corps_aussi=False, **contexte):
    """D02 : normalisation, contrôle métier, correction unique si besoin, repli déterministe, puis rendu avec l'écart restant signalé
    dans la fiche de contrôle. ``extra`` : écarts déjà constatés par l'appelant (sortie non conforme au schéma)."""
    consignes = contexte.pop('consignes', '')
    contexte['corps_aussi'] = corps_aussi
    result = preparer(result)
    ecarts = list(extra) + controler(result, **contexte)
    state = 'non_necessaire'
    first = list(ecarts)
    if ecarts:
        result, ecarts, state = corriger(modele, payload, result, ecarts, schema, stage, extra=extra, **contexte)
    result = repli(result, contexte.get('reference'), corps_aussi)
    ecarts = controler(result, **contexte)
    result['limits'] = filtrer_limites(result.get('limits'), consignes)          # v3 : pas de fragment de consigne dans la fiche
    propositions = sum(1 for p in result['variantes'] if len(p['options']) == 1)
    result['controle5695'] = {'ecarts': ecarts, 'ecarts_initiaux': first, 'correction': state, 'correction_reussie': state == 'appliquee',
                              'marqueurs': len(marqueurs(result)), 'choix': len(result['variantes']), 'propositions_a_confirmer': propositions}
    if ecarts:
        result['limits'] = list(result.get('limits') or []) + ['Contrôle de rédaction : ' + ' ; '.join(ecarts)[:600]]
    return result


# --------------------------------------------------------------------------------------------- corps lisible (D04)
def _ligne(value, limit=160):
    return re.sub(r'[\r\n()▸\[\]]+', ' ', str(value or '')).strip()[:limit]


def _texte_option(value):
    lines = [ln for ln in str(value or '').replace('\r\n', '\n').split('\n') if not ln.lstrip(' \t>').startswith('▸')]
    return REPERE.sub('', '\n'.join(lines)).strip()


def bloc(point):
    """Texte brut d'un point de décision : lisible tel quel dans un client de messagerie, et en HTML converti. Une seule option :
    « Proposition à confirmer », que l'avocat garde ou retire (jamais versée dans le corps comme si la position était prise)."""
    options = (point.get('options') or [])[:len(LETTRES)]
    if len(options) == 1:
        return '\n'.join([PROPOSITION % (_ligne(point.get('point'), 300) or 'position de l’avocat'), _texte_option(options[0].get('texte')),
                          FIN_PROPOSITION])
    rows = [CHOIX % (_ligne(point.get('point'), 300) or 'position de l’avocat')]
    for n, option in enumerate(options):
        if n:
            rows.append('')
        rows.append(VARIANTE % (LETTRES[n], _ligne(option.get('libelle')) or 'variante %s' % LETTRES[n]))
        rows.append(_texte_option(option.get('texte')))
    rows.append(FIN)
    return '\n'.join(rows)


def assembler(body, variantes):
    """Corps déposé : chaque repère [[CHOIX n]] remplacé par son bloc ; un point sans repère est placé avant la formule de politesse
    (à défaut, à la fin). Un repère sans point est retiré. Le reste du texte n'est jamais modifié."""
    text = str(body or '').replace('\r\n', '\n').strip()
    placed = set()

    def put(match):
        n = int(match.group(1))
        if n in placed or not 1 <= n <= len(variantes or []):
            return ''
        placed.add(n)
        return '\n\n' + bloc(variantes[n - 1]) + '\n\n'
    text = REPERE.sub(put, text)
    missing = [bloc(p) for n, p in enumerate(variantes or [], 1) if n not in placed and p.get('options')]
    if missing:
        lines = text.split('\n')
        at = next((i for i in range(len(lines) - 1, max(-1, len(lines) - 12), -1) if _FORMULE_FIN.match(lines[i])), None)
        insert = '\n\n'.join(missing)
        if at is None:
            text = text + '\n\n' + insert
        else:
            text = '\n'.join(lines[:at]).rstrip() + '\n\n' + insert + '\n\n' + '\n'.join(lines[at:])
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def blocs(texte):
    """Points de décision restant dans un texte : [{'debut', 'fin', 'point', 'complet', 'proposition', 'options': [{'lettre', 'libelle',
    'texte'}]}]. Une « proposition à confirmer » a une seule option, de lettre A."""
    out, cur, pos = [], None, 0

    def close(block, end, done):
        block.update(fin=end, complet=done)
        for o in block['options']:
            o['texte'] = '\n'.join(o.pop('lignes')).strip()
        out.append(block)
    for line in str(texte or '').splitlines(keepends=True):
        raw = line.rstrip('\r\n')
        head, var, end = _LIGNE_CHOIX.match(raw), _LIGNE_VARIANTE.match(raw), _LIGNE_FIN.match(raw)
        prop, end_prop = _LIGNE_PROPOSITION.match(raw), _LIGNE_FIN_PROPOSITION.match(raw)
        if head or prop:
            if cur:
                close(cur, pos, False)
            cur = {'debut': pos, 'point': (head or prop).group(1).strip(), 'options': [], 'proposition': bool(prop)}
            if prop:
                cur['options'].append({'lettre': 'A', 'libelle': 'proposition à confirmer', 'lignes': []})
        elif var and not (cur and cur['proposition']):
            if cur is None:
                cur = {'debut': pos, 'point': '', 'options': [], 'proposition': False}
            cur['options'].append({'lettre': var.group(1).upper(), 'libelle': (var.group(2) or '').strip(), 'lignes': []})
        elif (end and cur is not None and not cur['proposition']) or (end_prop and cur is not None and cur['proposition']):
            close(cur, pos + len(line), True)
            cur = None
        elif cur is not None and cur['options']:
            cur['options'][-1]['lignes'].append(raw)
        pos += len(line)
    if cur is not None:
        close(cur, pos, False)
    return out


def garder(texte, rang, lettre):
    """D05 : garde la variante ``lettre`` du ``rang``-ième choix ; les autres variantes et les lignes du bloc disparaissent.
    ``lettre`` = « - » : retire tout le bloc (proposition à confirmer refusée par l'avocat)."""
    found = blocs(texte)
    try:
        rang = int(rang)
    except (TypeError, ValueError):
        raise Stop('choix_introuvable') from None
    if not 1 <= rang <= len(found):
        raise Stop('choix_introuvable')
    block = found[rang - 1]
    if not block['complet']:
        raise Stop('bloc_variantes_incomplet')
    lettre = str(lettre or '').strip().upper()
    before, after = texte[:block['debut']].rstrip('\n'), texte[block['fin']:].lstrip('\n')
    if lettre == RETIRER:
        return (before + ('\n\n' + after if after else '')).strip()
    option = next((o for o in block['options'] if o['lettre'] == lettre), None)
    if option is None:
        raise Stop('variante_introuvable')
    if not option['texte'].strip():
        raise Stop('variante_vide')
    return (before + ('\n\n' if before else '') + option['texte'].strip() + ('\n\n' + after if after else '')).strip()


CONSIGNE_OPTIONS = ('Les options sont des alternatives laissées au choix de l’avocat ; juge chacune, n’en exige pas la cohérence entre elles. '
                    'Une option qui révèle une donnée interne ou une stratégie au destinataire rend recipient_safe=false.')


def corps_pour_controle(texte):
    """v3 : corps complet transmis au contrôle de sûreté, chaque bloc présenté simplement « Option A : … / Option B : … » (forme que le
    contrôle comprend) ; chaque option reste lue et jugée, donc une option qui révèle une donnée interne fait toujours refuser le dépôt."""
    found = blocs(texte)
    out, last = [], 0
    for b in found:
        out.append(texte[last:b['debut']])
        point = b['point'] or 'position de l’avocat'
        if b['proposition']:
            out.append('Proposition à confirmer par l’avocat (%s) : %s\n' % (point, b['options'][0]['texte'] if b['options'] else ''))
        else:
            out.append('Options au choix de l’avocat (%s), une seule sera gardée :\n%s\n' % (
                point, '\n'.join('Option %s : %s' % (o['lettre'], o['texte']) for o in b['options'])))
        last = b['fin']
    out.append(texte[last:])
    return ''.join(out)


def compter(texte):
    """D07 : ce qui reste à faire dans un texte déposé ou à déposer."""
    text = str(texte or '')
    found = blocs(text)
    stray = 1 if not found and VARIANTES.search(text) else 0
    return {'choix': len(found) + stray, 'options': sum(len(b['options']) for b in found if not b['proposition']),
            'propositions': sum(1 for b in found if b['proposition']), 'marqueurs': len(MARKER.findall(text)), 'relire': len(RELIRE.findall(text))}


def mention_objet(choix, faits):
    """Objet : ce qui reste à faire, au lieu de l'ancien « [À COMPLÉTER] » générique."""
    f = '%d fait%s' % (faits, 's' if faits > 1 else '')
    if choix and faits:
        return '[À RELIRE : %d choix, %s à compléter] ' % (choix, f)
    if choix:
        return '[À RELIRE : %d choix] ' % choix
    if faits:
        return '[À COMPLÉTER : %s] ' % f
    return '[À RELIRE] '


def reste_a_faire(texte):
    c = compter(texte)
    return bool(c['choix'] or c['marqueurs'] or c['relire'])


def version(texte):
    """Version d'un texte en attente (empreinte courte) : un choix fait sur une page périmée est refusé (409)."""
    return hashlib.sha256(str(texte or '').encode('utf-8')).hexdigest()[:20]

"""Analyse de style 4.8.0 : comparaison déterministe entre un brouillon de l'agent et le courriel réellement envoyé.

Aucune information du dossier n'est conservée : seules des formules de politesse d'une liste fermée, des
catégories de modification et des compteurs sortent de ce module. Un nom propre, un montant ou un fait ne peut
donc jamais devenir une « habitude de style ».
"""
import difflib
import re
import unicodedata


def fold(text):
    text = unicodedata.normalize('NFKD', str(text or '').replace('’', "'").replace('œ', 'oe').replace('Œ', 'oe'))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower()


# --------------------------------------------------------------------- formules (listes fermées)
OPENINGS = (
    ('Madame, Monsieur', r'^(madame,?\s+monsieur|monsieur,?\s+madame)\b', 'madame_monsieur'),
    ('Cher Confrère / Chère Consœur', r'^(cher|chere)\s+(confrere|consoeur|collegue)\b', 'confrere'),
    ('Cher Maître', r'^(cher|chere)\s+(maitre|me)\b', 'cher_maitre'),
    ('Maître', r'^maitre\b', 'maitre'),
    ('Bonjour', r'^bonjour\b', 'bonjour'),
    ('Bonsoir', r'^bonsoir\b', 'bonsoir'),
    ('Cher(e) + prénom ou nom', r'^(cher|chere)\b', 'cher'),
    ('Madame', r'^madame\b', 'madame'),
    ('Monsieur', r'^monsieur\b', 'monsieur'),
    ('Salut / Hello', r'^(salut|hello|coucou|hi)\b', 'salut'),
)
CLOSINGS = (
    ('Salutations distinguées (formule complète)', r"(agreer|veuillez agreer|je vous prie d'agreer).*(salutations|sentiments|consideration)|^salutations distinguees", 'agreer'),
    ('Très confraternellement', r'^tres confraternellement', 'tres_confraternellement'),
    ('Bien confraternellement', r'^bien confraternellement', 'bien_confraternellement'),
    ('Confraternellement', r'^confraternellement', 'confraternellement'),
    ('Très cordialement', r'^tres cordialement', 'tres_cordialement'),
    ('Bien cordialement', r'^bien cordialement', 'bien_cordialement'),
    ('Cordialement', r'^cordialement', 'cordialement'),
    ('Respectueusement', r'^respectueusement', 'respectueusement'),
    ('Sincères salutations', r'^(sinceres|bien sinceres) salutations', 'sinceres'),
    ('Bien à vous', r'^bien a vous', 'bien_a_vous'),
    ('Amicalement', r'^amicalement', 'amicalement'),
    ('Bonne journée / soirée', r'^bonne (journee|soiree)', 'bonne_journee'),
)
PHRASES = (
    ('N’hésitez pas à me contacter', r"n'hesitez pas"),
    ('Je reste à votre disposition', r'(je reste|restant) a votre (entiere )?disposition'),
    ('Sous toutes réserves', r'sous toutes reserves'),
    ('Je vous remercie', r'je vous remercie'),
    ('Merci de', r'\bmerci de\b'),
    ('Je vous prie de', r'je vous prie (de|d\')'),
    ('Dans l’attente de votre retour', r"dans l'attente de"),
    ('Comme convenu', r'comme convenu'),
    ('Pour mémoire', r'pour memoire'),
    ('Je me permets de', r'je me permets'),
    ('Bien entendu', r'bien entendu'),
    ('Veuillez', r'\bveuillez\b'),
)
LABELS = {
    'formule_ouverture': 'Formule d’ouverture modifiée',
    'formule_fermeture': 'Formule de fin modifiée',
    'plus_court': 'Texte raccourci',
    'plus_long': 'Texte allongé',
    'politesse_ajoutee': 'Formule de politesse ajoutée',
    'politesse_retiree': 'Formule de politesse retirée',
    'registre': 'Registre modifié (vouvoiement / tutoiement)',
}
MAX_WORDS = 1500


def _lines(text):
    return [l.strip() for l in str(text or '').replace('\r', '').split('\n') if l.strip()]


def opening(text):
    lines = _lines(text)
    if not lines:
        return 'Aucune formule'
    first = fold(lines[0])
    for label, rx, _ in OPENINGS:
        if re.search(rx, first):
            return label
    return 'Aucune formule'


def _closing_index(lines):
    for index in range(len(lines) - 1, max(-1, len(lines) - 8), -1):
        folded = fold(lines[index])
        for label, rx, _ in CLOSINGS:
            if re.search(rx, folded):
                return index, label
    return None, 'Aucune formule'


def closing(text):
    return _closing_index(_lines(text))[1]


def core(text):
    """Texte sans formule de fin ni signature : permet de comparer le fond malgré la signature ajoutée par la messagerie."""
    lines = _lines(text)
    index, _ = _closing_index(lines)
    return '\n'.join(lines[:index] if index is not None else lines)


def _words(text):
    return re.findall(r"[\w']+", fold(text))[:MAX_WORDS]


def phrases(text):
    folded = fold(text)
    return {label for label, rx in PHRASES if re.search(rx, folded)}


def register(text):
    folded = fold(text)
    vous = len(re.findall(r'\b(vous|votre|vos)\b', folded))
    tu = len(re.findall(r"\b(tu|ton|ta|tes|toi|t')\b", folded))
    if tu > vous and tu >= 2:
        return 'tu'
    return 'vous' if vous else 'neutre'


def classify(draft, final):
    """('tel_quel'|'leger'|'reecrit', similarité) - texte et fond comparés après normalisation."""
    a, b = _words(draft), _words(final)
    ca, cb = _words(core(draft)), _words(core(final))
    if a == b or ca == cb:
        return 'tel_quel', 1.0
    full = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    inner = difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio()
    ratio = max(full, inner)
    return ('leger' if ratio >= 0.80 else 'reecrit'), round(ratio, 4)


def changes(draft, final):
    result = []
    o1, o2 = opening(draft), opening(final)
    if o1 != o2:
        result.append({'cat': 'formule_ouverture', 'from': o1, 'to': o2})
    c1, c2 = closing(draft), closing(final)
    if c1 != c2:
        result.append({'cat': 'formule_fermeture', 'from': c1, 'to': c2})
    w1, w2 = len(_words(core(draft))), len(_words(core(final)))
    if w1 and w2 < 0.85 * w1 and w1 - w2 >= 15:
        result.append({'cat': 'plus_court', 'from': str(w1), 'to': str(w2)})
    elif w1 and w2 > 1.15 * w1 and w2 - w1 >= 15:
        result.append({'cat': 'plus_long', 'from': str(w1), 'to': str(w2)})
    p1, p2 = phrases(draft), phrases(final)
    for item in sorted(p2 - p1):
        result.append({'cat': 'politesse_ajoutee', 'from': '', 'to': item})
    for item in sorted(p1 - p2):
        result.append({'cat': 'politesse_retiree', 'from': item, 'to': ''})
    r1, r2 = register(draft), register(final)
    if r1 != r2 and 'neutre' not in (r1, r2):
        result.append({'cat': 'registre', 'from': r1, 'to': r2})
    return result


def final_formulas(text):
    return opening(text), closing(text), len(_words(core(text)))

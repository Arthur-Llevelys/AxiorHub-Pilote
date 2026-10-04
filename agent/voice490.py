"""Dictée vocale 4.9.0 : interprétation déterministe des commandes dictées dans l'éditeur de brouillon.

La transcription elle-même reste locale (passerelle Vocal, voir audio.py). Ce module ne fait QUE lire la
transcription déjà obtenue : aucun modèle de langage, aucun appel réseau, aucun contenu conservé.

Principes :
  - une commande n'est reconnue que si la phrase dictée COMMENCE par son mot-clé ; sinon c'est de la dictée simple ;
  - jamais de date, de mois ou de fait inventé : « le 12 » reste « le 12 » et est signalé « à compléter » ;
  - une instruction ambiguë (texte à remplacer présent plusieurs fois, absent) ne modifie rien ;
  - l'envoi ne se commande jamais à la voix : « envoie » est refusé ;
  - le serveur ne fait que PROPOSER la modification ; l'éditeur l'applique, la garde annulable et ne l'enregistre
    que sur la commande « enregistre » ou sur le bouton.
"""
from datetime import date
import re
import unicodedata

from .common import Stop
from . import style480

MAX_TRANSCRIPT = 3000
MAX_BODY = 60000

MONTHS = ('janvier', 'fevrier', 'mars', 'avril', 'mai', 'juin', 'juillet', 'aout', 'septembre', 'octobre',
          'novembre', 'decembre')
WEEKDAYS = ('lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche')
MONTH_RX = '|'.join(MONTHS)


def fold1(text):
    """Pliage insensible à la casse et aux accents, qui conserve la longueur (index comparables)."""
    out = []
    for ch in str(text):
        if ch in 'œŒ':
            out.append('o')
            continue
        out.append(unicodedata.normalize('NFD', ch)[:1].lower() or ch.lower())
    return ''.join(out).replace('’', "'")


def clean(text):
    text = re.sub(r'\s+', ' ', str(text or '').replace(' ', ' ')).strip()
    return text.strip(' «»"“”')


def _tail_punct(text):
    return text.strip().rstrip(' .;:,!?…') if text else text


def sentence(text):
    """Met la phrase dictée en forme (majuscule initiale, point final) sans changer un seul mot."""
    text = clean(text)
    if not text:
        return ''
    text = text[0].upper() + text[1:]
    return text if re.search(r'[.!?…]$', text) else text + '.'


# --------------------------------------------------------------------------- avertissements sur les dates
def date_warnings(text, today=None):
    today = today or date.today()
    folded = fold1(text)
    warnings = []
    for m in re.finditer(r'\b(?:le|du|au)\s+(\d{1,2})(?:er)?\b(?!\s*(?:' + MONTH_RX + r'|/\d|\.\d|-\d|h\b|heure|euro|€|%))', folded):
        day = int(m.group(1))
        if 1 <= day <= 31:
            warnings.append({'code': 'date_incomplete', 'text': m.group(0),
                             'message': '« %s » : le mois n’est pas précisé. Rien n’a été supposé ; complétez avant d’enregistrer.' % m.group(0).strip()})
    for m in re.finditer(r'\b(' + '|'.join(WEEKDAYS) + r')\s+(\d{1,2})(?:er)?\s+(' + MONTH_RX + r')\b(?!\s+\d{4})', folded):
        wd, day, month = WEEKDAYS.index(m.group(1)), int(m.group(2)), MONTHS.index(m.group(3)) + 1
        hits = []
        for year in (today.year, today.year + 1):
            try:
                if date(year, month, day).weekday() == wd:
                    hits.append(year)
            except ValueError:
                continue
        if not hits:
            warnings.append({'code': 'jour_incoherent', 'text': m.group(0),
                             'message': '« %s » : ce jour de la semaine ne correspond pas à cette date en %d ni en %d. Vérifiez.' % (
                                 m.group(0), today.year, today.year + 1)})
    return warnings


# ---------------------------------------------------------------------- structure du message
def _line_spans(body):
    spans, pos = [], 0
    for line in body.split('\n'):
        spans.append((pos, pos + len(line), line))
        pos += len(line) + 1
    return spans


def closing_start(body):
    """Position du début de la formule de fin (avec les lignes vides qui la précèdent), ou None."""
    spans = _line_spans(body)
    non_empty = [i for i, s in enumerate(spans) if s[2].strip()]
    for i in reversed(non_empty[-8:]):
        folded = style480.fold(spans[i][2].strip())
        if any(re.search(rx, folded) for _, rx, _ in style480.CLOSINGS):
            j = i
            while j > 0 and not spans[j - 1][2].strip():
                j -= 1
            return spans[j][0]
    return None


def insert_paragraph(body, paragraph):
    """Insère un paragraphe avant la formule de fin, ou à la fin du message."""
    body = body.replace('\r\n', '\n').replace('\r', '\n')
    pos = closing_start(body)
    if pos is None:
        base = body.rstrip('\n')
        return (base + '\n\n' if base else '') + paragraph + '\n'
    head = body[:pos].rstrip('\n')
    return head + '\n\n' + paragraph + '\n\n' + body[pos:].lstrip('\n')


def delete_last_sentence(body):
    body = body.replace('\r\n', '\n').replace('\r', '\n')
    end = closing_start(body)
    end = len(body) if end is None else end
    region = body[:end]
    spans = [s for s in _line_spans(region) if s[2].strip()]
    if spans and any(re.search(rx, style480.fold(spans[0][2].strip())) for _, rx, _ in style480.OPENINGS):
        spans = spans[1:]
    if not spans:
        raise Stop('aucune_phrase_a_supprimer')
    start, stop, line = spans[-1]
    parts = list(re.finditer(r'[^.!?…]+[.!?…]*\s*', line))
    parts = [p for p in parts if p.group(0).strip()]
    if not parts:
        raise Stop('aucune_phrase_a_supprimer')
    last = parts[-1]
    removed = last.group(0).strip()
    new_line = line[:last.start()].rstrip()
    if new_line:
        new_region = region[:start] + new_line + region[stop:]
    else:
        new_region = (region[:start].rstrip('\n') + region[stop:].lstrip('\n')) if region[stop:].strip() else region[:start].rstrip('\n')
        new_region = re.sub(r'\n{3,}', '\n\n', new_region)
    rest = body[end:]
    new_body = new_region.rstrip('\n') + ('\n\n' + rest.lstrip('\n') if rest.strip() else '\n')
    return new_body, removed


def replace_text(body, old, new):
    folded_body, folded_old = fold1(body), fold1(old)
    if not folded_old:
        raise Stop('texte_a_remplacer_vide')
    hits, start = [], 0
    while True:
        i = folded_body.find(folded_old, start)
        if i < 0:
            break
        hits.append(i)
        start = i + max(1, len(folded_old))
    if not hits:
        raise Stop('texte_a_remplacer_introuvable')
    if len(hits) > 1:
        raise Stop('texte_a_remplacer_ambigu')
    i = hits[0]
    return body[:i] + new + body[i + len(old):], body[i:i + len(old)]


# -------------------------------------------------------------------------------- ponctuation parlée
SPOKEN = (
    (r'\s*\bpoint d[\' ]?interrogation\b', '?'), (r'\s*\bpoint d[\' ]?exclamation\b', '!'),
    (r'\s*\bpoint[- ]virgule\b', ';'), (r'\s*\bdeux[- ]points\b', ' :'),
    (r'\s*\bpoint final\b', '.'), (r'\s*\bvirgule\b', ','),
)


def spoken_punctuation(text):
    out = str(text)
    for rx, repl in SPOKEN:
        out = re.sub(rx, repl, out, flags=re.IGNORECASE)
    out = re.sub(r'\s*\b(?:[àa] la ligne)\b\s*[,.]?\s*', '\n', out, flags=re.IGNORECASE)
    out = re.sub(r'\s*\bnouveau paragraphe\b\s*[,.]?\s*', '\n\n', out, flags=re.IGNORECASE)
    return out.strip(' ') if '\n' not in out else out.strip(' ')


# --------------------------------------------------------------------------------- commandes
C = lambda rx: re.compile(rx, re.IGNORECASE | re.DOTALL)
RX_SEND = C(r'^\s*(?:envoie|envoyer|expedie|expédie|expedier|expédier|transmets|transmettre|poste|valide l.envoi)\b')
RX_SAVE = C(r'^\s*(?:enregistre|enregistrer|sauvegarde|sauvegarder)(?:\s+(?:le\s+)?(?:brouillon|message|courriel))?\s*[.!]?\s*$')
RX_UNDO = C(r'^\s*(?:annule|annuler|défais|defais|défaire|defaire)(?:\s+(?:ça|ca|la dernière modification|la derniere modification|le dernier changement))?\s*[.!]?\s*$')
RX_NEWPAR = C(r'^\s*(?:nouveau paragraphe|[àa] la ligne)\s*[.!]?\s*$')
RX_ADD_QUE = C(r'^\s*(?:ajoute|ajouter|rajoute|rajouter)\s+que\s+(.+)$')
RX_ADD = C(r'^\s*(?:ajoute|ajouter|rajoute|rajouter)\s+(.+)$')
RX_REPLACE = C(r'^\s*(?:remplace|remplacer)\s+(.+?)\s+par\s+(.+)$')
RX_DELETE = C(r'^\s*(?:supprime|supprimer|efface|effacer|retire|retirer)\s+la\s+derni[èe]re\s+phrase\s*[.!]?\s*$')
RX_ASSIST = C(r'^\s*(?:reformule|reformuler|r[ée]écris|r[ée]ecris|rends(?:-le)?|demande\s+[àa]\s+l.ia(?:\s+de)?)\s*:?\s*(.*)$')


def interpret(transcript, body='', today=None):
    """Retourne une proposition ; ne modifie rien par elle-même.

    mode : 'insert' (texte à insérer au curseur), 'replace_body' (nouveau corps complet), 'action' (enregistrer,
    annuler), 'assist' (instruction pour la reformulation par l'IA, validée par l'avocat), 'refused'.
    """
    raw = clean(transcript)
    if not raw:
        raise Stop('transcription_vide')
    if len(raw) > MAX_TRANSCRIPT:
        raise Stop('transcription_trop_longue')
    body = str(body or '').replace('\r\n', '\n').replace('\r', '\n')
    if len(body) > MAX_BODY:
        raise Stop('corps_trop_long')
    today = today or date.today()

    if RX_SEND.match(raw):
        return {'mode': 'refused', 'command': 'envoi', 'warnings': [],
                'summary': 'L’envoi ne se commande pas à la voix. Enregistrez le brouillon, puis utilisez le bouton d’envoi ou votre messagerie.'}
    if RX_SAVE.match(raw):
        return {'mode': 'action', 'action': 'save', 'command': 'enregistrer', 'warnings': [],
                'summary': 'Enregistrement du brouillon dans la messagerie (aucun envoi).'}
    if RX_UNDO.match(raw):
        return {'mode': 'action', 'action': 'undo', 'command': 'annuler', 'warnings': [],
                'summary': 'Annulation de la dernière modification dictée.'}
    if RX_NEWPAR.match(raw):
        return {'mode': 'insert', 'text': '\n\n' if 'paragraphe' in fold1(raw) else '\n', 'command': 'saut', 'warnings': [],
                'summary': 'Saut de ligne inséré au curseur.'}
    if RX_DELETE.match(raw):
        new_body, removed = delete_last_sentence(body)
        return {'mode': 'replace_body', 'new_body': new_body, 'command': 'supprimer_phrase', 'warnings': [],
                'summary': 'Dernière phrase supprimée : « %s »' % removed[:140]}
    m = RX_REPLACE.match(raw)
    if m:
        old, new = clean(m.group(1)), clean(m.group(2))
        new_body, removed = replace_text(body, old, new)
        return {'mode': 'replace_body', 'new_body': new_body, 'command': 'remplacer', 'warnings': date_warnings(new, today),
                'summary': '« %s » remplacé par « %s ».' % (removed[:80], new[:80])}
    m = RX_ADD_QUE.match(raw) or RX_ADD.match(raw)
    if m:
        paragraph = sentence(spoken_punctuation(m.group(1)).replace('\n', ' '))
        if not paragraph.strip('. '):
            raise Stop('texte_a_ajouter_vide')
        return {'mode': 'replace_body', 'new_body': insert_paragraph(body, paragraph), 'command': 'ajouter',
                'warnings': date_warnings(paragraph, today),
                'summary': 'Phrase ajoutée avant la formule de fin : « %s »' % paragraph[:160]}
    m = RX_ASSIST.match(raw)
    if m and (m.group(1).strip() or fold1(raw).startswith('reformule')):
        instruction = clean(m.group(1)) or 'Reformule ce courriel'
        if re.match(r'(?i)^(?:rends(?:-le)?)\b', raw):
            instruction = ('Rends-le ' + instruction).strip()
        return {'mode': 'assist', 'instruction': instruction[:1500], 'command': 'reformuler', 'warnings': [],
                'summary': 'Reformulation demandée à l’IA : « %s ». Vous validerez la proposition avant tout remplacement.' % instruction[:140]}
    text = spoken_punctuation(raw)
    return {'mode': 'insert', 'text': text, 'command': 'dictee', 'warnings': date_warnings(text, today),
            'summary': 'Texte dicté inséré au curseur.'}

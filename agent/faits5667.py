"""5.6.67 (point 4) : contrôle déterministe des faits d'un texte rédigé par l'IA (courriel, document), APRÈS la rédaction.

Chaque date, montant, numéro RG — et, dans un courriel, chaque nom écrit en capitales — doit figurer dans ce que le modèle a reçu
(courriel, sources, contexte du dossier). Ce qui n'y figure pas est entouré d'un marqueur « [À VÉRIFIER : …] » : rien n'est supprimé,
l'avocat voit exactement quoi contrôler, et l'envoi depuis AxiorHub refuse un brouillon marqué. Aucun appel au modèle.

Limites assumées : une date sans année, un délai calculé (« dans quinze jours »), une reformulation d'un nom ne sont pas contrôlés ;
le contrôle porte sur la présence dans les sources, pas sur l'exactitude juridique.
"""
from datetime import date
import json
import re

from . import preuve5634
from .association5667 import RG_RX, rg_variants
from .common import fold

MARK = '[À VÉRIFIER : %s]'
_MONTHS = r'janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre'
_DAYS = r'(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?'
DATE_RX = re.compile(r'(?<![\w/])(?:' + _DAYS + r'(?:1er|\d{1,2})\s+(?:' + _MONTHS + r')\s+\d{4}|\d{1,2}[/.]\d{1,2}[/.]\d{4}|\d{4}-\d{2}-\d{2})(?![\w/])',
                     re.I)
RG_TEXT = re.compile(r'(?<![A-Za-z])(?:RG|R\.G\.)\s*(?:n[°o]\s*)?:?\s*((?:\d{2}|\d{4})\s*[/-]\s*\d{3,6})(?![\d/])')
NAME_RX = re.compile(r'(?<![\w-])[A-ZÀ-Ý][A-ZÀ-Ý\'’-]{4,}(?![\w-])')
# mots en capitales qui ne sont pas des noms (titres, abréviations, juridictions, formules)
NOT_NAMES = {fold(w) for w in (
    'URGENT', 'IMPORTANT', 'CONFIDENTIEL', 'CONFIDENTIELLE', 'OBJET', 'REFERENCE', 'RÉFÉRENCE', 'DOSSIER', 'TRIBUNAL', 'JUDICIAIRE',
    'COMMERCE', 'APPEL', 'CHAMBRE', 'AUDIENCE', 'PIECE', 'PIÈCE', 'PIECES', 'PIÈCES', 'CONCLUSIONS', 'ASSIGNATION', 'JUGEMENT', 'ORDONNANCE',
    'ARRET', 'ARRÊT', 'GREFFE', 'CABINET', 'AVOCAT', 'AVOCATS', 'MAITRE', 'MAÎTRE', 'MADAME', 'MONSIEUR', 'SOCIETE', 'SOCIÉTÉ', 'BORDEREAU',
    'COMPLETER', 'COMPLÉTER', 'VERIFIER', 'VÉRIFIER', 'RAPPEL', 'RELANCE', 'NOTE', 'INTERNE', 'PROJET', 'ANNEXE', 'ANNEXES', 'MOTIFS',
    'PLAISE', 'DISCUSSION', 'FAITS', 'PROCEDURE', 'PROCÉDURE', 'DEMANDE', 'DEMANDES', 'DISPOSITIF', 'SIGNATURE', 'TOQUE', 'BARREAU', 'LYON',
    'PARIS', 'FRANCE', 'EUROS', 'SIRET', 'SIREN', 'IBAN', 'TVA', 'CEDEX', 'RPVA', 'EBARREAU', 'JEX', 'PRUD', 'HOMMES', 'PRUD’HOMMES',
    'SARL', 'SASU', 'SELARL', 'EURL', 'SCP', 'SCI', 'SNC', 'SAS', 'NOTA', 'BENE', 'POST', 'SCRIPTUM', 'MERCI', 'CORDIALEMENT', 'BONJOUR')}


def reference_text(*parts):
    """Texte de référence : tout ce que le modèle a reçu (chaînes telles quelles, objets en JSON)."""
    out = []
    for part in parts:
        if part is None:
            continue
        out.append(part if isinstance(part, str) else json.dumps(part, ensure_ascii=False, default=str))
    return '\n'.join(out)


def without_examples(sources):
    """Les exemples d'anciens envois (autres affaires, autres dates) ne valent jamais preuve pour ce courriel."""
    return [s for s in (sources or []) if not (isinstance(s, dict) and s.get('kind') in ('sent_example', 'sent_preference'))]


def _rg_key(value):
    m = RG_RX.search(str(value or ''))
    return '%s/%s' % m.groups() if m else ''


def check(text, reference, extra_dates=(), names=False):
    """(texte marqué, éléments non retrouvés). ``extra_dates`` : dates ISO admises sans source (aujourd'hui, créneaux proposés)."""
    text = str(text or '')
    if not text.strip():
        return text, []
    ref = str(reference or '')
    ref_fold = fold(ref)
    dates_ok = preuve5634.dates_in_text(ref) | {str(d) for d in extra_dates} | {date.today().isoformat()}
    amounts_ok = preuve5634.amounts_in_text(ref)
    rg_ok = {_rg_key(v) for v in rg_variants(ref, in_text=True)} | {'%s/%s' % g for g in RG_RX.findall(ref)}
    spans = []
    for m in DATE_RX.finditer(text):
        raw = re.sub(r'^' + _DAYS, '', m.group(0), flags=re.I).strip()
        iso = preuve5634.normalize_date(raw)
        if iso['valide'] and iso['precision'] == 'jour' and iso['iso'] not in dates_ok:
            spans.append((m.start(), m.end(), 'date'))
    for occ in preuve5634.money_in_text(text):
        if (str(occ['valeur']), occ['devise']) not in amounts_ok:
            spans.append((occ['debut'], occ['fin'], 'montant'))
    for m in RG_TEXT.finditer(text):
        if _rg_key(m.group(1)) not in rg_ok:
            spans.append((m.start(1), m.end(1), 'rg'))
    if names:
        for m in NAME_RX.finditer(text):
            word = fold(m.group(0)).strip('\'’-')
            if word in NOT_NAMES or len(word) < 5 or word in ref_fold:
                continue
            spans.append((m.start(), m.end(), 'nom'))
    spans.sort()
    kept, last = [], -1
    for start, end, kind in spans:
        if start >= last:
            kept.append((start, end, kind))
            last = end
    issues = []
    for start, end, kind in reversed(kept):
        value = text[start:end]
        if text[max(0, start - 15):start].endswith('[À VÉRIFIER : '):
            continue
        text = text[:start] + MARK % value + text[end:]
        issues.append({'type': kind, 'texte': value})
    issues.reverse()
    return text, issues[:30]


def summary(issues):
    if not issues:
        return ''
    labels = {'date': 'date', 'montant': 'montant', 'rg': 'numéro RG', 'nom': 'nom'}
    return ('%d élément(s) absent(s) des sources transmises, marqué(s) « [À VÉRIFIER : …] » : %s.'
            % (len(issues), ', '.join('%s %s' % (labels.get(i['type'], i['type']), i['texte']) for i in issues[:8])))

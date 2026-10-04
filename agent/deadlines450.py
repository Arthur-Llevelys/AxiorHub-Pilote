"""Moteur de délais de procédure civile (AxiorHub 4.5.0).

Aucun modèle de langage n'intervient ici : les règles sont des données versionnées
(agent/data/deadline_rules450.json), le calcul applique les articles 640 à 643 du
Code de procédure civile et chaque résultat détaille la règle et les étapes.
"""
from calendar import monthrange
from datetime import date, timedelta
import json
from pathlib import Path

DATA = Path(__file__).with_name('data') / 'deadline_rules450.json'
DAY_NAMES = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
MONTH_NAMES = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre',
               'novembre', 'décembre']
DISTANCES = ('none', 'outre_mer', 'etranger')


class DeadlineError(ValueError):
    """Calcul impossible ou hors périmètre ; ``code`` est stable, le message est en français."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


_CACHE = {}


def load():
    if 'data' not in _CACHE:
        _CACHE['data'] = json.loads(DATA.read_text(encoding='utf-8'))
    return _CACHE['data']


def rules():
    return load()['rules']


def rule(rule_id):
    for item in rules():
        if item['id'] == rule_id:
            return item
    raise DeadlineError('regle_inconnue', 'Règle de délai inconnue : %s.' % rule_id)


def start_event_label(key):
    return load()['start_events'].get(key, key)


def fr_date(d):
    return '%s %d %s %d' % (DAY_NAMES[d.weekday()], d.day, MONTH_NAMES[d.month - 1], d.year)


def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def holidays(year, alsace=False):
    """Jours fériés légaux (art. L. 3133-1 du Code du travail) ; ``alsace`` ajoute le régime local."""
    pâques = easter(year)
    days = {
        date(year, 1, 1): 'Jour de l’an',
        pâques + timedelta(days=1): 'Lundi de Pâques',
        date(year, 5, 1): 'Fête du Travail',
        date(year, 5, 8): 'Victoire 1945',
        pâques + timedelta(days=39): 'Ascension',
        pâques + timedelta(days=50): 'Lundi de Pentecôte',
        date(year, 7, 14): 'Fête nationale',
        date(year, 8, 15): 'Assomption',
        date(year, 11, 1): 'Toussaint',
        date(year, 11, 11): 'Armistice 1918',
        date(year, 12, 25): 'Noël',
    }
    if alsace:
        days[pâques - timedelta(days=2)] = 'Vendredi saint (Alsace-Moselle)'
        days[date(year, 12, 26)] = 'Saint-Étienne (Alsace-Moselle)'
    return days


def non_working_reason(d, alsace=False):
    if d.weekday() == 5:
        return 'samedi'
    if d.weekday() == 6:
        return 'dimanche'
    return holidays(d.year, alsace).get(d, '')


def add_months(d, n):
    """Même quantième, à défaut le dernier jour du mois d'arrivée (art. 641 CPC)."""
    index = d.year * 12 + (d.month - 1) + n
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))


def _parse(value, field):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DeadlineError('date_invalide', 'Date invalide (%s) : %s.' % (field, value)) from None


def compute(rule_id, start, distance='none', alsace=False, regime_date=None, scope='civil_commercial'):
    """Calcule l'échéance. ``start`` est la date de l'événement de départ (jamais déduite d'un modèle)."""
    if distance not in DISTANCES:
        raise DeadlineError('distance_invalide', 'Délai de distance inconnu.')
    if scope not in ('civil_commercial', '', None):
        raise DeadlineError('hors_perimetre', 'Procédure non couverte par le moteur (%s) : calculer le délai à la main.' % scope)
    spec = rule(rule_id)
    begin = _parse(start, 'départ')
    if begin.year < 1976 or begin.year > 2100:
        raise DeadlineError('date_invalide', 'Date de départ hors plage plausible.')
    if spec.get('regime'):
        if regime_date in (None, ''):
            raise DeadlineError('date_regime_requise', 'Indiquer la date de la déclaration d’appel : le régime applicable en dépend.')
        regime = _parse(regime_date, 'déclaration d’appel')
        if regime < date.fromisoformat(spec['valid_from']):
            raise DeadlineError('hors_perimetre', 'Appel formé avant le %s : ancien régime de la procédure d’appel, non couvert par cette version.'
                                % fr_date(date.fromisoformat(spec['valid_from'])))
    elif begin < date.fromisoformat(spec['valid_from']):
        raise DeadlineError('hors_perimetre', 'Événement antérieur à l’entrée en vigueur de la règle.')
    if spec.get('valid_to') and begin > date.fromisoformat(spec['valid_to']):
        raise DeadlineError('hors_perimetre', 'Règle abrogée à la date de l’événement.')
    amount, unit = int(spec['amount']), spec['unit']
    steps = ['Événement de départ : %s, le %s.' % (start_event_label(spec['start_event']), fr_date(begin)),
             'Règle : %s — %d %s (%s).' % (spec['label'], amount, unit, ', '.join(spec['articles'])),
             'Le jour de l’événement n’est pas compté (art. 640 et 641 CPC).']
    warnings = []
    if unit == 'mois':
        raw = add_months(begin, amount)
        steps.append('+ %d mois, même quantième : %s%s.' % (amount, fr_date(raw), '' if raw.day == begin.day else
                     ' (quantième absent du mois d’arrivée : dernier jour du mois)'))
    else:
        raw = begin + timedelta(days=amount)
        steps.append('+ %d jours : %s (le dernier jour est compris, le délai expire à 24 h).' % (amount, fr_date(raw)))
    months = 0
    if distance != 'none' and spec.get('distance'):
        months = int(load()['distance'][distance])
        before = raw
        raw = add_months(raw, months)
        steps.append('Délai de distance (%s) : + %d mois (art. 643 CPC) : %s → %s.' % (
            'demeurant en outre-mer' if distance == 'outre_mer' else 'demeurant à l’étranger', months, fr_date(before), fr_date(raw)))
        if unit == 'jours':
            warnings.append('Délai en jours prolongé d’un délai de distance en mois : la règle est appliquée en ajoutant les mois au terme du délai en jours ; à confirmer.')
    elif distance != 'none':
        steps.append('Délai de distance non applicable à cette règle (art. 643 CPC ne vise que comparution, appel, opposition, révision, pourvoi).')
    due = raw
    reason = ''
    while True:
        why = non_working_reason(due, alsace)
        if not why:
            break
        if not reason:
            reason = why
        due += timedelta(days=1)
    if due != raw:
        steps.append('%s tombe un jour non ouvrable (%s) : report au premier jour ouvrable suivant (art. 642 CPC).' % (fr_date(raw), reason))
    steps.append('Échéance : %s.' % fr_date(due))
    if spec.get('note'):
        warnings.append(spec['note'])
    return {'ok': True, 'rule_id': rule_id, 'label': spec['label'], 'articles': list(spec['articles']),
            'start_event': spec['start_event'], 'start_event_label': start_event_label(spec['start_event']),
            'start': begin.isoformat(), 'amount': amount, 'unit': unit, 'distance': distance, 'distance_months': months,
            'alsace': bool(alsace), 'raw_end': raw.isoformat(), 'due': due.isoformat(), 'prorogation': reason,
            'steps': steps, 'warnings': warnings, 'rules_version': load()['version']}


def explain(result):
    return '\n'.join(result['steps'])


def rules_for_ui():
    return [{'id': r['id'], 'label': r['label'], 'articles': r['articles'], 'start_event': r['start_event'],
             'start_event_label': start_event_label(r['start_event']), 'amount': r['amount'], 'unit': r['unit'],
             'distance': bool(r.get('distance')), 'regime': bool(r.get('regime')), 'note': r.get('note', '')}
            for r in rules()]

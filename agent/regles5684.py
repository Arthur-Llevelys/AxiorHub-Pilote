"""5.6.84 : règles datées des cessions de fonds et de droit au bail (cahier 5.6.84, §§ 5 à 7). Prolonge ``regles5683`` : même catalogue, même validation par version.

Textes relus le 31 octobre 2026 : C. com. L141-12, L141-14, L141-23 (versions avant et après la loi n° 2026-403 du 26 mai 2026), L23-10-1, L145-16, L145-16-2 ; C. urb. L214-1 ; CGI 635, 719, 720,
723 (rédactions en vigueur et au 1er janvier 2027), 725. L141-1 est abrogé depuis 2019 : les mentions de cet ancien article ne sont pas présentées comme obligatoires.

Le calcul n'est jamais confié au modèle. Une date manquante, un cas frontière ou une règle hors périmètre donne « À vérifier », jamais une date certaine. Le droit calculé ici est le seul droit de
l'article 719 : les taxes additionnelles ne sont pas chiffrées et le décompte le dit."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from . import regles5683
from .common import Stop

LF = regles5683.LEGIFRANCE
RULES = {
    'publication.fonds': {'version': '2019-05-24', 'label': 'Publication de la cession du fonds', 'source': 'C. com. art. L141-12', 'url': LF + 'LEGIARTI000038584414/2019-05-24', 'application': '2019-05-24',
                          'depart': 'date de la vente', 'methode': 'quinze jours, sur un support d’annonces légales et au BODACC', 'exceptions': 'Apport en société ; cession L642-5.'},
    'opposition.fonds': {'version': '2016-11-16', 'label': 'Opposition des créanciers', 'source': 'C. com. art. L141-14', 'url': LF + 'LEGIARTI000033388390/2016-11-16', 'application': '2016-11-16',
                         'depart': 'dernière en date des publications', 'methode': 'dix jours', 'exceptions': 'Le bailleur ne peut former opposition pour loyers en cours ou à échoir.'},
    'salaries.fonds.ancien': {'version': '2016-01-01', 'label': 'Information des salariés, ancien régime', 'source': 'C. com. art. L141-23, rédaction antérieure à la loi n° 2026-403', 'url': LF + 'LEGIARTI000031012733/2026-01-01',
                              'application': '2016-01-01', 'depart': 'notification de l’information', 'methode': 'deux mois', 'exceptions': 'Entreprise sans obligation de CSE exerçant les attributions de L2312-1, al. 2.'},
    'salaries.fonds': {'version': '2026-05-28', 'label': 'Information des salariés', 'source': 'C. com. art. L141-23 ; loi n° 2026-403 du 26 mai 2026, art. 22, II', 'url': LF + 'LEGIARTI000054140994/2026-05-28', 'application': '2026-07-27',
                       'depart': 'notification de l’information', 'methode': 'un mois', 'exceptions': 'Ventes conclues deux mois au moins après la promulgation ; le jour frontière reste à vérifier. La vente peut intervenir plus tôt si chaque salarié a renoncé.'},
    'preemption.commune': {'version': '2014-06-20', 'label': 'Droit de préemption de la commune', 'source': 'C. urb. art. L214-1', 'url': LF + 'LEGIARTI000029108800/2014-06-20', 'application': '2014-06-20',
                           'depart': 'réception de la déclaration par la commune', 'methode': 'deux mois', 'exceptions': 'Uniquement dans un périmètre délimité par la commune, à vérifier. La vente doit se faire aux prix et conditions déclarés.'},
    'enregistrement.fonds': {'version': '2021-01-01', 'label': 'Enregistrement de l’acte', 'source': 'CGI art. 635, 2, 5°', 'url': LF + 'LEGIARTI000042911346/2021-01-01', 'application': '2021-01-01',
                             'depart': 'date de l’acte', 'methode': 'un mois', 'exceptions': ''},
    'droits.fonds': {'version': '2008-08-06', 'label': 'Droit d’enregistrement du fonds ou du bail', 'source': 'CGI art. 719 et 725', 'url': LF + 'LEGIARTI000019291809/2008-08-06', 'application': '2008-08-06', 'depart': '',
                     'methode': '0 % jusqu’à 23 000 € ; 2 % jusqu’à 107 000 € ; 0,60 % jusqu’à 200 000 € ; 2,60 % au-delà', 'exceptions': 'Taxes additionnelles non chiffrées ici. Marchandises neuves : CGI 723.'},
    'droits.marchandises': {'version': '2005-12-31', 'label': 'Marchandises neuves', 'source': 'CGI art. 723', 'url': LF + 'LEGIARTI000006305303/2005-12-31', 'application': '2005-12-31', 'depart': '',
                            'methode': 'exonération si TVA ; sinon 1,50 % avec prix particulier et état distinct', 'exceptions': 'Nouvelle rédaction au 1er janvier 2027 : même taux, renvoi au code des impositions sur les biens et services.'},
    'garantie.cedant.bail': {'version': '2014-06-20', 'label': 'Garantie du cédant au bailleur', 'source': 'C. com. art. L145-16-2', 'url': LF + 'LEGIARTI000029103418/2014-06-20', 'application': '2014-06-20', 'depart': 'cession du bail',
                             'methode': 'trois ans', 'exceptions': ''},
    'cession.bail.fonds': {'version': '2023-05-26', 'label': 'Cession du bail à l’acquéreur du fonds', 'source': 'C. com. art. L145-16', 'url': LF + 'LEGIARTI000047591361/2023-05-26', 'application': '2023-05-26', 'depart': '',
                           'methode': 'clause interdisant la cession à l’acquéreur du fonds réputée non écrite', 'exceptions': 'Ne vise pas la cession isolée du droit au bail ; les modalités d’intervention du bailleur restent à lire dans le bail.'},
}
regles5683.RULES.update(RULES)
BANDS = ((Decimal(23000), Decimal(0)), (Decimal(107000), Decimal('0.02')), (Decimal(200000), Decimal('0.006')), (None, Decimal('0.026')))
BOUNDARY = ('2026-07-26', '2026-07-27')


def _rule(desk, ident):
    return {'id': ident, 'source': RULES[ident]['source'], 'version': RULES[ident]['version'], **regles5683.state_of(desk, ident)}


def verify(rule, motif):
    return {'etat': 'a_verifier', 'date': '', 'motif': motif, 'regle': rule, 'source': RULES[rule]['source']}


def _add_months(day, months):
    return regles5683._add_months(day, months)


def _day(text):
    try:
        return date.fromisoformat(str(text or '')[:10])
    except ValueError:
        return None


def result(desk, rule, day, event, calc):
    """Résultat explicable : événement déclencheur, date, calcul et réserve. Tant que la règle n'est pas validée, elle reste une échéance à vérifier."""
    state = regles5683.state_of(desk, rule)['etat']
    return {'etat': 'calcule' if state == 'validee' else 'a_verifier', 'date': day.isoformat(), 'evenement': event, 'calcul': calc, 'regle': rule, 'version': RULES[rule]['version'], 'source': RULES[rule]['source'],
            'motif': '' if state == 'validee' else 'Règle à valider par un avocat.'}


def publication_limit(desk, sale_day):
    day = _day(sale_day)
    if not day:
        return verify('publication.fonds', 'Date de la vente non renseignée.')
    return result(desk, 'publication.fonds', day + timedelta(days=15), 'vente du ' + day.isoformat(), 'date de la vente + 15 jours')


def opposition_limit(desk, publications):
    """CE10 : dix jours après la DERNIÈRE des deux publications ; une seule publication connue ne permet aucune date."""
    legal, bodacc = _day((publications or {}).get('annonce_legale')), _day((publications or {}).get('bodacc'))
    if not legal or not bodacc:
        return verify('opposition.fonds', 'Les deux publications doivent être datées et justifiées.')
    last = max(legal, bodacc)
    return result(desk, 'opposition.fonds', last + timedelta(days=10), 'dernière publication du ' + last.isoformat(), 'dernière publication + 10 jours')


def employees_regime(sale_day):
    """CE09 : la version applicable dépend de la date de la vente, pas de la date de mise à jour d'une base."""
    day = str(sale_day or '')[:10]
    if not _day(day):
        return None
    if day in BOUNDARY:
        return 'frontiere'
    return 'ancien' if day < BOUNDARY[0] else 'nouveau'


def employees_delay(desk, sale_day, notification, has_cse=None):
    if has_cse is None:
        return verify('salaries.fonds', 'Préciser si l’entreprise doit mettre en place un CSE exerçant les attributions économiques.')
    if has_cse:
        return {'etat': 'sans_objet', 'date': '', 'motif': 'Ce texte vise les entreprises sans cette obligation : la consultation du CSE relève de son propre régime, à vérifier.', 'regle': 'salaries.fonds', 'source': RULES['salaries.fonds']['source']}
    regime = employees_regime(sale_day)
    if regime is None:
        return verify('salaries.fonds', 'Date de la vente envisagée non renseignée : régime applicable inconnu.')
    if regime == 'frontiere':
        return verify('salaries.fonds', 'Vente au jour frontière de la disposition transitoire (26 ou 27 juillet 2026) : régime à vérifier.')
    rule, months = ('salaries.fonds.ancien', 2) if regime == 'ancien' else ('salaries.fonds', 1)
    day = _day(notification)
    if not day:
        return verify(rule, 'Date de notification aux salariés non renseignée.')
    return result(desk, rule, _add_months(day, months), 'notification du ' + day.isoformat(), 'notification + %d mois, sauf renonciation de chaque salarié' % months)


def preemption_delay(desk, received, in_perimeter=None):
    if in_perimeter is None:
        return verify('preemption.commune', 'Vérifier si les locaux sont dans un périmètre de sauvegarde.')
    if not in_perimeter:
        return {'etat': 'sans_objet', 'date': '', 'motif': 'Hors périmètre, selon l’information confirmée.', 'regle': 'preemption.commune', 'source': RULES['preemption.commune']['source']}
    day = _day(received)
    if not day:
        return verify('preemption.commune', 'Date de réception de la déclaration non renseignée.')
    return result(desk, 'preemption.commune', _add_months(day, 2), 'réception du ' + day.isoformat(), 'réception + 2 mois ; le silence vaut renonciation aux prix et conditions déclarés')


def registration_limit(desk, act_day):
    day = _day(act_day)
    if not day:
        return verify('enregistrement.fonds', 'Date de l’acte non renseignée.')
    return result(desk, 'enregistrement.fonds', _add_months(day, 1), 'acte du ' + day.isoformat(), 'date de l’acte + 1 mois')


def duties(desk, data):
    """Droit de l'article 719, tranche par tranche, sur le fonds ou le droit au bail. Le décompte conserve ses composantes ; les taxes additionnelles restent à confirmer."""
    dec = regles5683._dec
    nature = str(data.get('nature') or '')
    if nature not in ('fonds', 'bail'):
        raise Stop('nature_cession_inconnue')
    missing = []
    price = dec(data.get('prix'))
    if price is None:
        missing.append('prix du fonds ou du droit au bail' if nature == 'fonds' else 'somme stipulée au profit du cédant')
    day = str(data.get('date') or '')[:10]
    if not _day(day):
        missing.append('date de l’acte')
    if nature == 'fonds' and str(data.get('marchandises_tva') or '') not in ('oui', 'non', 'sans_objet'):
        missing.append('traitement des marchandises neuves')
    if missing:
        return {'etat': 'a_confirmer', 'manque': missing, 'droits': None, 'composantes': []}
    charges = dec(data.get('charges')) or Decimal(0)
    value = dec(data.get('valeur_venale'))
    base = price + charges
    lines = [('Prix', regles5683._money(price)), ('Charges', regles5683._money(charges))]
    hypotheses = [] if data.get('charges') not in (None, '') else ['Aucune charge déclarée.']
    if value is not None and value > base:
        base = value
        lines.append(('Valeur vénale retenue, supérieure au prix et aux charges', regles5683._money(value)))
    total, low = Decimal(0), Decimal(0)
    for top, rate in BANDS:
        high = base if top is None else min(base, top)
        if high > low:
            part = (high - low) * rate
            lines.append(('Tranche jusqu’à %s € à %s %%' % (top if top is not None else 'au-delà', (rate * 100).normalize()), regles5683._money(part)))
            total += part
        low = top if top is not None else low
        if top is None or base <= top:
            break
    goods = dec(data.get('marchandises')) or Decimal(0)
    rules = ['droits.fonds']
    if goods and data.get('marchandises_tva') == 'non':
        extra = goods * Decimal('0.015')
        lines.append(('Marchandises neuves à 1,50 %, prix particulier et état distinct requis', regles5683._money(extra)))
        total += extra
        rules.append('droits.marchandises')
    elif goods:
        hypotheses.append('Marchandises neuves soumises à la TVA : exonérées de droit proportionnel, à confirmer.')
        rules.append('droits.marchandises')
    rounded = total.quantize(Decimal(1), ROUND_HALF_UP)
    return {'etat': 'calcule', 'manque': [], 'droits': regles5683._money(rounded), 'droits_exacts': regles5683._money(total), 'composantes': lines, 'hypotheses': hypotheses + ['Taxes additionnelles non chiffrées : à confirmer.'],
            'regles': rules, 'sources': [RULES[r]['source'] for r in rules], 'a_valider': [r for r in rules if regles5683.state_of(desk, r)['etat'] == 'a_valider']}

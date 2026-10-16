"""5.6.53 (audit 5.6.52, H01 et § 5) : qualification fiscale explicite d'un document d'honoraires.

Le taux affiché n'est jamais le seul champ fiscal. Un taux de 0 % ne prouve pas une franchise en base : la franchise (article 293 B du
CGI) est un régime CHOISI et justifié dans le profil du cabinet. Sans qualification, le document reste un brouillon et l'émission est
bloquée, avec la question ciblée à trancher. Les cas non pris en charge sont bloqués avec une explication ; aucune mention
d'autoliquidation n'est substituée automatiquement.

Chaque profil porte ses sources officielles, la date de vérification et la période de validité ; un profil périmé est signalé avant
émission. Les textes de référence ont été vérifiés au 16 octobre 2026 (voir ``SOURCES``) ; ils doivent être revus à chaque évolution.
"""
from decimal import Decimal

from .common import Stop

CHECKED_AT = '2026-10-16'
SOURCES = {
    'cgi_293b': ('CGI, article 293 B (franchise en base)', 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000053546660/2026-10-16'),
    'cgi_259_1': ('CGI, article 259-1° (lieu des prestations de services entre assujettis)', 'https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006069577/LEGISCTA000006191612/'),
    'cgi_283_2': ('CGI, article 283-2 (autoliquidation par le preneur assujetti)', 'https://www.legifrance.gouv.fr/codes/section_lc/LEGITEXT000006069577/LEGISCTA000006179660/'),
    'cgi_242_nonies_a': ('CGI, annexe II, article 242 nonies A (mentions des factures)', 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000050811276/2026-10-16'),
    'cgi_267': ('CGI, article 267 (débours)', 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000042910064/2026-10-16'),
    'ccom_l441_10': ('Code de commerce, article L441-10 (pénalités de retard)', 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000053151445/2026-10-16'),
    'ccom_d441_5': ('Code de commerce, article D441-5 (indemnité forfaitaire de 40 €)', 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000043197457/2026-10-16'),
    'loi_1971_art10': ('Loi n° 71-1130, article 10 (convention d’honoraires)', 'https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000054616333/2026-10-16'),
    'cnb_faq': ('CNB, FAQ facturation électronique (cas d’usage 36, secret professionnel)', 'https://cnb.avocat.fr/foire-aux-questions-facturation-electronique'),
    'dgfip': ('DGFiP, facturation électronique (calendrier)', 'https://www.impots.gouv.fr/professionnel/je-decouvre-la-facturation-electronique'),
}
REGIMES = {
    'a_qualifier': 'À qualifier',
    'normal': 'TVA française (taux qualifié)',
    'franchise': 'Franchise en base (article 293 B du CGI), choisie et justifiée',
    'autoliquidation': 'Autoliquidation par le preneur assujetti établi dans l’Union européenne',
    'hors_champ': 'Prestation à un preneur assujetti établi hors de l’Union européenne (non imposable en France)',
    'autre_qualifie': 'Autre régime qualifié par l’avocat (fondement à indiquer)',
}
CLIENT_KINDS = {'professionnel': 'Professionnel (assujetti)', 'consommateur': 'Particulier (consommateur)', 'public': 'Organisme public'}
COUNTRIES_EU = frozenset('AT BE BG CY CZ DE DK EE ES FI GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK'.split())
FR_RATES = (Decimal('20'), Decimal('10'), Decimal('5.5'), Decimal('2.1'), Decimal('8.5'))   # 8,5 % : Guadeloupe, Martinique, La Réunion
UNSUPPORTED = {'guyane_mayotte': 'Guyane et Mayotte (TVA provisoirement non applicable) : profil dédié à qualifier avant usage.',
               'aide_juridictionnelle': 'Aide juridictionnelle et rémunérations réglementées : modèle dédié requis, non couvert par le calcul générique.'}


def scope_of(client):
    """Périmètre du client : b2b_fr, b2c, eu_b2b, international (hors UE), public, unknown — jamais déduit d'un taux."""
    kind = (client or {}).get('kind') or ''
    country = str((client or {}).get('country') or 'FR').upper()
    if kind == 'public':
        return 'public'
    if kind == 'consommateur':
        return 'b2c'
    if kind == 'professionnel':
        if country == 'FR':
            return 'b2b_fr'
        return 'eu_b2b' if country in COUNTRIES_EU else 'international'
    return 'unknown'


def qualify(entity, client, choice=None):
    """Profil fiscal d'un document à partir du profil QUALIFIÉ du cabinet et de la qualité du client ; ``choice`` = régime retenu par
    l'avocat pour ce document (il ne peut pas contredire le profil du cabinet). Retourne régime, catégorie EN 16931, taux, mentions,
    fondement, et la liste des questions bloquantes."""
    fiscal = (entity or {}).get('fiscal') or {}
    cab = fiscal.get('regime_cabinet') or ''
    scope = scope_of(client)
    blockers, mention, basis, category, rate = [], '', '', 'S', None
    chosen = (choice or {}).get('regime') or ''
    if cab not in ('normal', 'franchise'):
        blockers.append('Le régime de TVA du cabinet n’est pas qualifié (Facturation › Réglages : TVA normale ou franchise en base choisie).')
        regime = 'a_qualifier'
    elif cab == 'franchise':
        if not str(fiscal.get('justification_franchise') or '').strip():
            blockers.append('La franchise en base est choisie mais non justifiée (seuils et date de vérification à indiquer).')
        regime, category, rate = 'franchise', 'E', Decimal('0')
        mention, basis = 'TVA non applicable, article 293 B du code général des impôts.', 'cgi_293b'
    elif scope == 'unknown':
        regime = 'a_qualifier'
        blockers.append('La qualité du client manque (professionnel, consommateur ou organisme public) : elle commande la TVA et les mentions.')
    elif scope == 'eu_b2b':
        if chosen and chosen != 'autoliquidation':
            regime = chosen if chosen in REGIMES else 'a_qualifier'
        else:
            regime = 'autoliquidation'
        if regime == 'autoliquidation':
            category, rate, basis = 'AE', Decimal('0'), 'cgi_283_2'
            mention = 'Autoliquidation — TVA due par le preneur (article 196 de la directive 2006/112/CE ; articles 259-1° et 283-2 du CGI).'
            if not str((client or {}).get('vat_number') or '').strip():
                blockers.append('Le numéro de TVA intracommunautaire du client est requis pour l’autoliquidation.')
            if not (choice or {}).get('confirme'):
                blockers.append('Confirmez que la prestation relève de la règle générale des prestations entre assujettis (lieu du preneur).')
    elif scope == 'international':
        regime, category, rate, basis = 'hors_champ', 'O', Decimal('0'), 'cgi_259_1'
        mention = 'Opération non soumise à la TVA française — article 259-1° du CGI (preneur assujetti établi hors de l’Union européenne).'
        if not (choice or {}).get('confirme'):
            blockers.append('Confirmez la qualité d’assujetti du client établi hors de l’Union européenne et la règle de lieu applicable.')
    else:                                                      # b2b_fr, b2c, public en TVA française
        regime = 'normal'
        rate = Decimal(str((choice or {}).get('taux') or fiscal.get('taux_defaut') or '20'))
        if rate not in FR_RATES:
            blockers.append('Taux de TVA %s %% non pris en charge pour des honoraires : choisissez un taux qualifié.' % rate)
        if rate == Decimal('8.5') and not (choice or {}).get('confirme'):
            blockers.append('Le taux de 8,5 % (départements d’outre-mer) doit être confirmé pour ce document.')
        category = 'S'
        mention = ''
    if chosen == 'autre_qualifie':
        regime = 'autre_qualifie'
        basis = str((choice or {}).get('fondement') or '').strip()
        mention = str((choice or {}).get('mention') or '').strip()
        category = (choice or {}).get('categorie') or 'E'
        rate = Decimal(str((choice or {}).get('taux') or '0'))
        if not basis or not mention:
            blockers.append('Régime « autre » : le fondement et la mention exacte sont à indiquer par l’avocat.')
    for key, why in UNSUPPORTED.items():
        if (choice or {}).get(key) or (client or {}).get(key):
            blockers.append(why)
    return {'regime': regime, 'regime_libelle': REGIMES.get(regime, regime), 'perimetre': scope, 'categorie_tva': category,
            'taux': str(rate) if rate is not None else None, 'mention': mention, 'fondement': basis,
            'source': SOURCES.get(basis, (basis, ''))[0] if basis else '', 'tva_sur_debits': bool(fiscal.get('tva_sur_debits')),
            'verifie_le': CHECKED_AT, 'bloquants': blockers}


def late_payment_clause(scope):
    """Clause de retard adaptée au client : professionnels (L441-10 et D441-5, taux légal non inventé) ; consommateurs sans les 40 €."""
    if scope in ('b2b_fr', 'eu_b2b', 'international', 'public'):
        return ('En cas de retard de paiement, des pénalités sont exigibles au taux d’intérêt appliqué par la Banque centrale européenne à son '
                'opération de refinancement la plus récente majoré de dix points, sauf taux convenu (article L441-10 du code de commerce), ainsi '
                'qu’une indemnité forfaitaire pour frais de recouvrement de 40 € (article D441-5 du code de commerce).')
    if scope == 'b2c':
        return 'En cas de retard de paiement, les sommes dues portent intérêt au taux légal à compter de la mise en demeure.'
    return '[Clause de retard à choisir selon la qualité du client]'


def operation_category(lines):
    """Catégorie d'opération (mention 2026) : prestations de services pour des honoraires ; biens seulement s'il y en a."""
    return 'Prestation de services'


def vat_breakdown(lines, fiscal):
    """Ventilation par catégorie et taux, au centime, ligne par ligne puis par base (méthode documentée : TVA calculée sur la base
    agrégée de chaque taux, arrondi au centime à la moitié supérieure)."""
    from decimal import ROUND_HALF_UP
    groups = {}
    for l in lines:
        key = (l.get('categorie_tva') or fiscal.get('categorie_tva') or 'S', str(l.get('taux_tva') if l.get('taux_tva') is not None else fiscal.get('taux') or '0'))
        groups.setdefault(key, 0)
        groups[key] += int(l['montant_ht_cents'])
    out = []
    for (cat, rate), base in sorted(groups.items()):
        vat = int((Decimal(base) * Decimal(rate) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if cat == 'S' else 0
        out.append({'categorie': cat, 'taux': rate, 'base_ht_cents': base, 'tva_cents': vat})
    return out

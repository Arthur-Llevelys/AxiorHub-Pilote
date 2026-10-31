"""5.6.83 : règles datées et calculs déterministes des cessions de droits sociaux (cahier complément, §§ 7 et 8, CT08, CT09).

Chaque règle porte sa source, sa date de version, sa date d'application et le jour de sa lecture. Les textes ont été relus le 31 octobre 2026
(CGI 635, 719, 726 ; Code de commerce L221-14, L223-14, L223-17, L227-14, L228-1 ; imprimé 2759-SD 08-2026). Une règle reste « à valider »
tant qu'un avocat ne l'a pas validée : le résultat est affiché, jamais présenté comme certain. Une donnée manquante donne « À confirmer »,
jamais une valeur supposée. Les montants sont calculés en Decimal : aucun modèle de langage n'intervient."""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

from .common import Stop

LU_LE = '2026-10-31'
LEGIFRANCE = 'https://www.legifrance.gouv.fr/codes/article_lc/'
RULES = {
    'droits.actions': {'version': '2024-01-01', 'label': 'Cession d’actions', 'source': 'CGI art. 726, I, 1°', 'url': LEGIFRANCE + 'LEGIARTI000048845731/2024-01-01',
                       'application': '2024-01-01', 'depart': 'date de la cession', 'methode': 'assiette × 0,1 %',
                       'exceptions': 'Prépondérance immobilière (5 %) ; opérations exclues du II, a) à d).'},
    'droits.parts': {'version': '2024-01-01', 'label': 'Cession de parts sociales', 'source': 'CGI art. 726, I, 1° bis', 'url': LEGIFRANCE + 'LEGIARTI000048845731/2024-01-01',
                     'application': '2024-01-01', 'depart': 'date de la cession', 'methode': '(valeur de la part − 23 000 € / nombre total de parts) × nombre de parts cédées × 3 %',
                     'exceptions': 'Prépondérance immobilière (5 %) ; opérations exclues du II, a) à d).'},
    'droits.preponderance': {'version': '2024-01-01', 'label': 'Participation à prépondérance immobilière', 'source': 'CGI art. 726, I, 2°',
                             'url': LEGIFRANCE + 'LEGIARTI000048845731/2024-01-01', 'application': '2024-01-01', 'depart': 'date de la cession', 'methode': 'assiette × 5 %, sans abattement',
                             'exceptions': 'Mentions obligatoires du III, B, dans l’acte ou la déclaration.'},
    'droits.assiette': {'version': '2024-01-01', 'label': 'Assiette', 'source': 'CGI art. 726, II', 'url': LEGIFRANCE + 'LEGIARTI000048845731/2024-01-01',
                        'application': '2024-01-01', 'depart': '', 'methode': 'prix exprimé + capital des charges, ou estimation des parties si la valeur réelle est supérieure',
                        'exceptions': 'Le prix nominal affiché ne suffit pas toujours.'},
    'droits.arrondi': {'version': '2002-03-31', 'label': 'Arrondi du décompte', 'source': 'CGI art. 1724 dans la version relue ; numérotation à vérifier',
                       'url': LEGIFRANCE + 'LEGIARTI000006312863/2002-03-31', 'application': '2002-03-31', 'depart': '', 'methode': 'euro le plus proche, 0,50 compté pour 1',
                       'exceptions': 'La base consultée signale une renumérotation de ce texte : le montant exact reste affiché à côté du montant arrondi.'},
    'enregistrement.acte': {'version': '2021-01-01', 'label': 'Enregistrement de l’acte de cession', 'source': 'CGI art. 635, 2, 7° et 7° bis', 'url': LEGIFRANCE + 'LEGIARTI000042911346/2021-01-01',
                            'application': '2021-01-01', 'depart': 'date de l’acte', 'methode': 'un mois', 'exceptions': ''},
    'formulaire.2759': {'version': '2026-08-01', 'label': 'Déclaration 2759-SD', 'source': 'Imprimé 2759-SD (08-2026), CGI art. 639 et 726', 'url': 'https://www.impots.gouv.fr/formulaire/2759-sd',
                        'application': '2026-08-01', 'depart': 'date de la cession', 'methode': 'cession non constatée par un acte uniquement',
                        'exceptions': 'Jamais un substitut à un acte requis : un acte de cession s’enregistre lui-même.'},
    'agrement.sarl': {'version': '2004-03-27', 'label': 'Agrément d’un tiers dans une SARL', 'source': 'C. com. art. L223-14', 'url': LEGIFRANCE + 'LEGIARTI000006223059/2004-03-27',
                      'application': '2004-03-27', 'depart': 'dernière des notifications à la société et à chaque associé', 'methode': 'trois mois',
                      'exceptions': 'Le consentement réputé acquis suppose des notifications établies ; statuts : majorité plus forte possible.'},
    'agrement.sas': {'version': '2000-09-21', 'label': 'Agrément dans une SAS', 'source': 'C. com. art. L227-14', 'url': LEGIFRANCE + 'LEGIARTI000006227136/2000-09-21',
                     'application': '2000-09-21', 'depart': '', 'methode': 'selon les statuts', 'exceptions': 'Seuls les statuts lus disent si un agrément est prévu.'},
    'opposabilite.parts': {'version': '2014-08-03', 'label': 'Opposabilité de la cession de parts', 'source': 'C. com. art. L223-17 et L221-14', 'url': LEGIFRANCE + 'LEGIARTI000029329292/2014-08-03',
                           'application': '2014-08-03', 'depart': '', 'methode': 'écrit ; signification ou dépôt d’un original au siège contre attestation ; publication des statuts modifiés',
                           'exceptions': ''},
    'transfert.actions': {'version': '2019-06-10', 'label': 'Transfert des actions', 'source': 'C. com. art. L228-1', 'url': LEGIFRANCE + 'LEGIARTI000038591684/2019-06-10',
                          'application': '2019-06-10', 'depart': 'inscription au compte de l’acquéreur', 'methode': 'le transfert résulte de l’inscription, dans les cas visés',
                          'exceptions': 'Titres admis aux opérations d’un dépositaire central : régime du code monétaire et financier.'},
}


def schema(desk):
    desk.db.execute('CREATE TABLE IF NOT EXISTS rule_versions5683(rule TEXT NOT NULL, version TEXT NOT NULL, validated_by TEXT NOT NULL, validated_at TEXT NOT NULL, '
                    'note TEXT NOT NULL DEFAULT \'\', PRIMARY KEY(rule, version))')


def state_of(desk, rule_id):
    schema(desk)
    rule = RULES[rule_id]
    row = desk.db.execute('SELECT validated_by, validated_at FROM rule_versions5683 WHERE rule=? AND version=?', (rule_id, rule['version'])).fetchone()
    return {'etat': 'validee', 'par': row['validated_by'], 'le': row['validated_at']} if row else {'etat': 'a_valider'}


def catalogue(desk):
    return [{'id': k, **v, 'lu_le': LU_LE, **state_of(desk, k)} for k, v in RULES.items()]


def validate(desk, rule_id, owner, note=''):
    """Validation d'une version de règle par un avocat : une nouvelle version repasse à « à valider » d'elle-même."""
    if rule_id not in RULES:
        raise Stop('regle_inconnue')
    schema(desk)
    desk.db.execute('INSERT OR REPLACE INTO rule_versions5683 VALUES(?,?,?,?,?)', (rule_id, RULES[rule_id]['version'], str(owner), date.today().isoformat(), str(note or '')[:500]))
    desk.db.commit()
    desk.audit('regle5683_validee', {'rule': rule_id, 'version': RULES[rule_id]['version'], 'owner': owner})
    return {'message': 'Règle validée pour sa version du %s.' % RULES[rule_id]['version']}


def _dec(value):
    text = str(value if value is not None else '').replace(' ', '').replace('\xa0', '').replace(' ', '').replace('€', '').replace(',', '.')
    if not text:
        return None
    try:
        out = Decimal(text)
    except InvalidOperation:
        return None
    return out if out.is_finite() and out >= 0 else None


def _money(value):
    return str(value.quantize(Decimal('0.01'), ROUND_HALF_UP))


def _a_confirmer(manque, hypotheses=()):
    return {'etat': 'a_confirmer', 'manque': list(manque), 'hypotheses': list(hypotheses), 'composantes': [], 'droits': None, 'droits_exacts': None, 'regles': []}


def droits_cession(desk, data):
    """Décompte des droits : assiette, abattement, taux, montant exact et arrondi, avec règles et hypothèses. Rien n'est supposé : une
    donnée absente rend le décompte « À confirmer ». Les taxes de fonds et de bail (CGI 719) relèvent de la 5.6.84."""
    nature = str(data.get('nature') or '')
    if nature not in ('parts', 'actions'):
        raise Stop('nature_titres_inconnue')
    manque, hypotheses = [], []
    day = str(data.get('date_cession') or '')[:10]
    prix = _dec(data.get('prix_total'))
    nombre = _dec(data.get('nombre'))
    charges = _dec(data.get('charges'))
    reelle = _dec(data.get('valeur_reelle'))
    total = _dec(data.get('total_titres'))
    preponderance = str(data.get('preponderance') or '')
    regime = str(data.get('regime') or '')
    if prix is None:
        manque.append('prix exprimé')
    if nombre is None or nombre <= 0:
        manque.append('nombre de titres cédés')
    if preponderance not in ('oui', 'non'):
        manque.append('prépondérance immobilière (oui ou non)')
    if regime not in ('ordinaire', 'exclusion'):
        manque.append('régime : ordinaire, ou exclusion prévue au II de l’article 726')
    if not day:
        manque.append('date de la cession')
    elif day < '2024-01-01':     # seule la version en vigueur depuis le 1er janvier 2024 a été relue
        manque.append('barème applicable à une cession antérieure au 1er janvier 2024')
    if nature == 'parts' and preponderance != 'oui' and (total is None or total <= 0):
        manque.append('nombre total de parts de la société')
    if manque:
        return _a_confirmer(manque)
    if preponderance == 'non' and regime == 'exclusion':     # 726, II, a) à d) : ni le 0,1 %, ni le 3 % ; le 5 % reste dû
        return {'etat': 'hors_perception', 'manque': [], 'hypotheses': ['Exclusion du II de l’article 726 retenue : motif à justifier dans le dossier.'],
                'composantes': [], 'droits': '0.00', 'droits_exacts': '0.00', 'regles': ['droits.assiette']}
    if charges is None:
        charges = Decimal(0)
        hypotheses.append('Aucune charge augmentant le prix n’a été déclarée.')
    assiette = prix + charges
    composantes = [('Prix exprimé', _money(prix)), ('Charges ajoutées au prix', _money(charges))]
    if reelle is not None and reelle > assiette:
        assiette = reelle
        composantes.append(('Valeur réelle retenue (supérieure au prix et aux charges)', _money(reelle)))
    elif reelle is None:
        hypotheses.append('Valeur réelle non renseignée : le prix et les charges sont retenus.')
    regles = ['droits.assiette', 'droits.arrondi']
    abattement = Decimal(0)
    if preponderance == 'oui':
        taux, regle = Decimal('0.05'), 'droits.preponderance'
    elif nature == 'parts':
        taux, regle = Decimal('0.03'), 'droits.parts'
        abattement = min(assiette, Decimal(23000) * nombre / total)      # 23 000 / total par part, jamais au-delà de la valeur cédée
        composantes.append(('Abattement proportionnel (23 000 € × %s / %s)' % (nombre.normalize(), total.normalize()), _money(abattement)))
    else:
        taux, regle = Decimal('0.001'), 'droits.actions'
    nette = assiette - abattement
    exact = nette * taux
    arrondi = exact.quantize(Decimal(1), ROUND_HALF_UP)
    composantes += [('Assiette nette', _money(nette)), ('Taux', '%s %%' % (taux * 100).normalize())]
    regles.append(regle)
    return {'etat': 'calcule', 'manque': [], 'hypotheses': hypotheses, 'composantes': composantes, 'droits': _money(arrondi), 'droits_exacts': _money(exact),
            'minimum': 'Aucun minimum de perception à l’article 726 relu le %s.' % LU_LE, 'regles': regles,
            'a_valider': [r for r in regles if state_of(desk, r)['etat'] == 'a_valider'], 'sources': [RULES[r]['source'] for r in regles]}


def _add_months(day, months):
    y, m = day.year + (day.month - 1 + months) // 12, (day.month - 1 + months) % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
    return date(y, m, min(day.day, last))


def echeance(rule_id, depart, months):
    """Échéance d'un délai légal : « À confirmer » tant que le point de départ manque ; le rappel de la source accompagne toujours la date."""
    rule = RULES[rule_id]
    try:
        start = date.fromisoformat(str(depart or '')[:10])
    except ValueError:
        return {'etat': 'a_confirmer', 'date': '', 'manque': rule['depart'], 'source': rule['source'], 'nature': 'délai légal'}
    return {'etat': 'calcule', 'date': _add_months(start, months).isoformat(), 'manque': '', 'source': rule['source'], 'nature': 'délai légal'}


def delai_agrement(notifications, attendues):
    """L223-14 : trois mois à compter de la DERNIÈRE des notifications. Tant que toutes ne sont pas établies, le point de départ est inconnu.
    L'expiration du délai ne vaut jamais accord automatique : l'avocat vérifie les conditions du texte et du dossier."""
    days = sorted(str(x)[:10] for x in notifications if x)
    if not attendues or len(days) < attendues:
        return {**echeance('agrement.sarl', '', 3), 'attendues': attendues, 'recues': len(days)}
    return {**echeance('agrement.sarl', days[-1], 3), 'attendues': attendues, 'recues': len(days),
            'note': 'Sans réponse à cette date, le consentement n’est réputé acquis que si l’avocat confirme les notifications et leur preuve.'}


def formulaire_2759(nature, acte_ecrit):
    """CT08 : le 2759-SD ne vise que la cession non constatée par un acte. Il ne remplace pas l'acte écrit exigé pour des parts (L221-14)."""
    if acte_ecrit:
        return {'applicable': False, 'motif': 'Un acte constate la cession : c’est l’acte lui-même qui s’enregistre (%s).' % RULES['enregistrement.acte']['source'],
                'regle': 'enregistrement.acte'}
    if nature == 'parts':
        return {'applicable': False, 'motif': 'La cession de parts sociales doit être constatée par écrit (%s) : la déclaration ne remplace pas cet acte.'
                % RULES['opposabilite.parts']['source'], 'regle': 'opposabilite.parts'}
    return {'applicable': True, 'motif': 'Cession d’actions non constatée par un acte : déclaration 2759-SD à déposer.', 'regle': 'formulaire.2759'}


def as_json(value):
    return json.dumps(value, ensure_ascii=False)

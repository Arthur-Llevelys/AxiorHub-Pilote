"""5.6.53 (audit 5.6.52, § 6) : moteur de chiffrage des honoraires, porté d'Honorium V5.1.6 et corrigé.

Le serveur fait autorité sur le calcul ; le modèle de langage ne calcule aucun total. Arithmétique décimale (``Decimal``) ; montants
en centimes ; arrondi au centime par ligne (moitié supérieure), total = somme des lignes arrondies.

Reprise d'Honorium (core-v5.js) : coût horaire de revient = (charges de structure + revenu net visé + cotisations) / heures
facturables ; taux cible = coût × (1 + majoration %) — une MAJORATION sur le coût, pas un taux de marge sur le chiffre d'affaires ;
temps des diligences par niveau (simple, standard, complexe, très complexe) ; fourchette basse/haute à mi-chemin des niveaux
voisins ; taux propre à la ligne > taux par type de diligence > taux du dossier ; ajustement des temps assistés par l'IA et part du
gain répercutée au forfait (choix du cabinet, jamais des heures réellement facturées).

Corrections de l'audit :
- H02 : un forfait non déterminé vaut ``None`` ; un forfait de zéro n'existe qu'avec le choix explicite « mission gratuite / remise
  totale » et son motif ; les replis ``||`` sont remplacés par des tests de nullité.
- H03 : la provision est calculée au centime (30 % de 1 001,25 € = 300,38 €), sauf arrondi commercial choisi et annoncé.
- H01 (côté fiscal) : le chiffrage ne déduit aucun régime de TVA ; voir ``fiscal5653``.

Trois montants distincts : PROPOSÉ (recalculable), RETENU (décision de l'avocat), SIGNÉ (figé avec sa source à la signature).
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import secrets

from .common import Stop

ENGINE_VERSION = 'honoraires5653/1 (Honorium V5.1.6 corrigé H01-H03)'
LIBRARY_FILE = Path(__file__).resolve().parent / 'data' / 'honorium-diligences-2026-06-26.json'
SETTING = 'honoraires5653:reglages'
LEVELS = ('simple', 'standard', 'complex', 'veryComplex')
LEVEL_LABELS = {'simple': 'Simple', 'standard': 'Standard', 'complex': 'Complexe', 'veryComplex': 'Très complexe'}
MODES = {'forfait': 'Forfait', 'temps': 'Au temps passé', 'mixte': 'Mixte (forfait et temps)'}
STATUSES = {'inclus': 'Inclus', 'option': 'En option (accord préalable)', 'exclu': 'Exclu'}
DILIGENCE_TYPES = {'rdv_client': 'Rendez-vous client', 'redaction_contrat': 'Rédaction d’acte ou de contrat', 'analyse_pieces': 'Analyse des pièces',
                   'formalite': 'Formalités', 'negociation': 'Négociation', 'recherche_juridique': 'Recherche juridique', 'postulation': 'Postulation',
                   'audience': 'Audience', 'temps_deplacement': 'Déplacement', 'assignation': 'Assignation', 'redaction_conclusions': 'Conclusions',
                   'redaction_courrier': 'Courrier', 'mise_en_demeure': 'Mise en demeure', 'autre': 'Autre'}
DEFAULTS = {'taux_horaire': '220', 'taux_par_type': {'postulation': '150'},
            'economie': {'charges_structure': '33240', 'revenu_net': '36000', 'cotisations': '16200', 'heures_facturables': '600', 'majoration_pct': '15'},
            'ia': {'actif': False, 'part_gain_client_pct': '100', 'regles': {}}, 'provision_pct': '30', 'arrondi_provision': 'centime'}
CENT = Decimal('0.01')
SOURCE_LABELS = {'bibliotheque': 'bibliothèque du cabinet (hypothèse)', 'ia_proposee': 'proposée par l’IA (hypothèse)', 'avocat': 'saisie par l’avocat',
                 'observee': 'temps réellement passé (validé)'}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def dec(value, name='montant', allow_none=False, minimum=Decimal('0')):
    """Nombre décimal contrôlé : refus de NaN, de l'infini, des négatifs injustifiés et des textes ambigus ; ``None`` reste ``None``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_none:
            return None
        raise Stop('%s_requis' % name)
    if isinstance(value, float):
        value = repr(value)
    text = str(value).strip().replace(' ', '').replace(' ', '').replace('€', '')
    if re.fullmatch(r'-?\d+,\d+', text):
        text = text.replace(',', '.')
    try:
        d = Decimal(text)
    except (InvalidOperation, ValueError):
        raise Stop('%s_invalide' % name) from None
    if not d.is_finite() or (minimum is not None and d < minimum):
        raise Stop('%s_invalide' % name)
    return d


def cents(amount):
    return int((Decimal(amount) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def euros(value_cents):
    return (Decimal(int(value_cents)) / 100).quantize(CENT)


def fmt(value_cents):
    """1 234,56 € (espace fine insécable)."""
    d = euros(value_cents)
    sign = '-' if d < 0 else ''
    whole, frac = ('%.2f' % abs(d)).split('.')
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return '%s%s,%s €' % (sign, ' '.join(groups), frac)


# ------------------------------------------------------------------------------------------------ réglages du cabinet
def settings(desk):
    raw = desk.settings(SETTING, None) or {}
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in raw.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def save_settings(desk, data):
    cur = settings(desk)
    rate = dec(data.get('taux_horaire', cur['taux_horaire']), 'taux_horaire')
    if rate <= 0 or rate > 5000:
        raise Stop('taux_horaire_invalide')
    cur['taux_horaire'] = str(rate)
    eco = cur['economie']
    for key in ('charges_structure', 'revenu_net', 'cotisations', 'heures_facturables', 'majoration_pct'):
        if key in data:
            eco[key] = str(dec(data[key], key))
    if dec(eco['heures_facturables'], 'heures_facturables') <= 0:
        raise Stop('heures_facturables_invalide')
    for t in DILIGENCE_TYPES:
        key = 'taux_type_' + t
        if key in data:
            value = dec(data[key], 'taux_horaire', allow_none=True)
            if value is None or value == 0:
                cur['taux_par_type'].pop(t, None)
            else:
                cur['taux_par_type'][t] = str(value)
    if 'provision_pct' in data:
        pct = dec(data['provision_pct'], 'provision')
        if pct > 100:
            raise Stop('provision_invalide')
        cur['provision_pct'] = str(pct)
    if data.get('arrondi_provision') in ('centime', 'euro'):
        cur['arrondi_provision'] = data['arrondi_provision']
    ia = cur['ia']
    if 'ia_actif' in data:
        ia['actif'] = data['ia_actif'] in (True, 'true', 'on', '1')
    if 'part_gain_client_pct' in data:
        share = dec(data['part_gain_client_pct'], 'part_gain')
        if share > 100:
            raise Stop('part_gain_invalide')
        ia['part_gain_client_pct'] = str(share)
    desk.setting(SETTING, cur)
    return cur


def economics(cfg):
    """Coût horaire de revient et taux cible (majoration sur le coût, pas une marge sur le chiffre d'affaires)."""
    e = cfg['economie']
    hours = dec(e['heures_facturables'], 'heures_facturables')
    need = dec(e['charges_structure'], 'charges') + dec(e['revenu_net'], 'revenu') + dec(e['cotisations'], 'cotisations')
    cost = (need / hours).quantize(CENT, rounding=ROUND_HALF_UP)
    floor = (dec(e['charges_structure'], 'charges') / hours).quantize(CENT, rounding=ROUND_HALF_UP)
    target = (cost * (1 + dec(e['majoration_pct'], 'majoration') / 100)).quantize(CENT, rounding=ROUND_HALF_UP)
    if e.get('cout_horaire'):
        cost = dec(e['cout_horaire'], 'cout_horaire')
    return {'heures': str(hours), 'besoin_annuel': str(need), 'cout_horaire': str(cost), 'plancher_horaire': str(floor), 'taux_cible': str(target),
            'definition': 'coût de revient = (charges + revenu net visé + cotisations) / heures facturables ; taux cible = coût × (1 + majoration)'}


# ------------------------------------------------------------------------------------------------ bibliothèque des diligences
_LIB = {}


def library(desk=None):
    """Bibliothèque Honorium (hypothèses du cabinet, pas un barème de la profession) ; un fichier importé dans l'état prime."""
    path = LIBRARY_FILE
    if desk is not None:
        custom = Path(desk.c.get('state_dir', '.')) / 'honoraires5653' / 'bibliotheque.json'
        if custom.is_file():
            path = custom
    key = (str(path), path.stat().st_mtime if path.is_file() else 0)
    if key not in _LIB:
        data = json.loads(path.read_text(encoding='utf-8'))
        _LIB.clear()
        _LIB[key] = data
    return _LIB[key]


def import_library(desk, raw):
    """Import d'une bibliothèque au format Honorium (JSON) : structure contrôlée, aucun code exécuté."""
    try:
        data = json.loads(raw.decode('utf-8') if isinstance(raw, (bytes, bytearray)) else raw)
    except (ValueError, UnicodeDecodeError):
        raise Stop('bibliotheque_illisible') from None
    rows = data.get('diligenceLibrary') if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 5000:
        raise Stop('bibliotheque_illisible')
    for r in rows:
        if not isinstance(r, dict) or not r.get('id') or not (r.get('labelClient') or r.get('name')):
            raise Stop('bibliotheque_illisible')
        for lv in ('minutesSimple', 'minutesStandard', 'minutesComplex', 'minutesVeryComplex'):
            if r.get(lv) is not None:
                dec(r[lv], 'duree')
    target = Path(desk.c['state_dir']) / 'honoraires5653' / 'bibliotheque.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(target)
    _LIB.clear()
    return {'diligences': len(rows), 'version': str(data.get('version') or '')}


def models(desk=None):
    """Modèles de dossiers de la bibliothèque (catégorie, sous-catégorie, nombre de diligences, temps standard)."""
    lib = library(desk)
    out = {}
    for r in lib['diligenceLibrary']:
        k = r.get('templateLabel') or 'Autre'
        m = out.setdefault(k, {'modele': k, 'categorie': r.get('categoryLabel') or '', 'diligences': 0, 'minutes_standard': 0})
        m['diligences'] += 1
        m['minutes_standard'] += int(r.get('minutesStandard') or 0)
    return sorted(out.values(), key=lambda m: (m['categorie'], m['modele']))


def _level_minutes(row, level):
    order = {'simple': ('minutesSimple', 'minutesStandard'), 'standard': ('minutesStandard', 'minutesSimple'),
             'complex': ('minutesComplex', 'minutesStandard', 'minutesSimple'), 'veryComplex': ('minutesVeryComplex', 'minutesComplex', 'minutesStandard')}[level]
    for key in order:                                        # repli seulement si la valeur est ABSENTE (un zéro saisi reste zéro)
        if row.get(key) is not None:
            return int(dec(row[key], 'duree'))
    return 0


def diligences_for(desk, model, level='standard'):
    """Diligences proposées pour un modèle de dossier : temps au niveau choisi, statut d'inclusion de la bibliothèque, source marquée."""
    if level not in LEVELS:
        raise Stop('niveau_invalide')
    out = []
    for r in library(desk)['diligenceLibrary']:
        if (r.get('templateLabel') or '') != model:
            continue
        status = 'exclu' if r.get('billable') is False else ('option' if r.get('includedInFixedFee') is False else 'inclus')
        out.append({'id': r['id'], 'libelle_client': r.get('labelClient') or r.get('name'), 'libelle_interne': r.get('labelInternal') or r.get('name'),
                    'type': r.get('diligenceType') or 'autre', 'minutes': _level_minutes(r, level), 'niveau': level,
                    'niveaux': {lv: _level_minutes(r, lv) for lv in LEVELS}, 'statut': status,
                    'taux': str(r['specificHourlyRate']) if r.get('specificHourlyRate') not in (None, '', 0) else None,
                    'source': 'bibliotheque', 'note': r.get('notes') or ''})
    if not out:
        raise Stop('modele_honoraires_inconnu')
    return out


# ------------------------------------------------------------------------------------------------ calcul
def _rate_for(d, cfg, matter_rate):
    if d.get('taux') not in (None, ''):
        r = dec(d['taux'], 'taux_horaire')
        if r > 0:
            return r, 'taux propre à la diligence'
    t = cfg['taux_par_type'].get(d.get('type') or 'autre')
    if t not in (None, ''):
        return dec(t, 'taux_horaire'), 'taux du cabinet pour « %s »' % DILIGENCE_TYPES.get(d.get('type'), d.get('type'))
    return matter_rate, 'taux horaire du dossier'


def _effective_minutes(d, cfg):
    base = int(dec(d.get('minutes', 0), 'duree'))
    ia = cfg['ia']
    if not ia.get('actif') or not d.get('assistee_ia'):
        return base
    rule = (ia.get('regles') or {}).get(d.get('type') or 'autre') or {}
    reduction = max(Decimal(0), min(Decimal(80), dec(rule.get('reduction_pct', 0), 'reduction')))
    minimum = max(0, int(dec(rule.get('minimum_minutes', 0), 'duree')))
    if not reduction:
        return base
    reduced = int((Decimal(base) * (1 - reduction / 100)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return max(min(base, minimum), reduced)                  # le plancher ne porte jamais le temps au-delà du temps saisi


def _price_minutes(d, cfg, mode):
    effective = _effective_minutes(d, cfg)
    if mode == 'temps':
        return effective
    base = int(dec(d.get('minutes', 0), 'duree'))
    share = max(Decimal(0), min(Decimal(100), dec(cfg['ia'].get('part_gain_client_pct', 100), 'part_gain'))) / 100
    return int((Decimal(base) - share * (base - effective)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _amount(minutes, rate):
    return cents(Decimal(minutes) / 60 * rate)


def _bound(d, cfg, direction):
    """Fourchette : à mi-chemin du niveau voisin (inférieur ou supérieur) de chaque diligence, comme Honorium."""
    current = _effective_minutes(d, cfg)
    levels = d.get('niveaux')
    if not levels:
        return current
    idx = LEVELS.index(d.get('niveau') or 'standard') if (d.get('niveau') or 'standard') in LEVELS else 1
    neighbour = LEVELS[min(len(LEVELS) - 1, max(0, idx + direction))]
    base = int(dec(d.get('minutes', 0), 'duree'))
    ratio = (Decimal(current) / base) if base else Decimal(1)
    other = int((Decimal(int(levels.get(neighbour) or 0)) * ratio).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    mid = int(((Decimal(current) + other) / 2).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    return min(current, mid) if direction < 0 else max(current, mid)


def provision(amount_cents, pct, rounding='centime'):
    """H03 : provision au centime (arrondi à la moitié supérieure) ; un arrondi à l'euro n'est fait que s'il est CHOISI et annoncé."""
    value = Decimal(int(amount_cents)) * dec(pct, 'provision') / 100
    if rounding == 'euro':
        return int((value / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * 100)
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def estimate(desk, inputs, cfg=None):
    """Estimation basse / centrale / haute, options, coût interne et explication. Aucun prix contractuel : montant PROPOSÉ."""
    cfg = cfg or settings(desk)
    mode = str(inputs.get('mode') or 'forfait')
    if mode not in MODES:
        raise Stop('mode_honoraires_invalide')
    rate = dec(inputs.get('taux_horaire') or cfg['taux_horaire'], 'taux_horaire')
    if rate <= 0:
        raise Stop('taux_horaire_invalide')
    rows = inputs.get('diligences') or []
    if not isinstance(rows, list) or len(rows) > 400:
        raise Stop('diligences_invalides')
    eco = economics(cfg)
    cost_rate = dec(eco['cout_horaire'], 'cout_horaire')
    lines, warnings, explain = [], [], []
    total = {'inclus': 0, 'option': 0, 'exclu': 0}
    minutes = {'inclus': 0, 'option': 0, 'exclu': 0}
    low_min = high_min = 0
    for i, d in enumerate(rows):
        if not isinstance(d, dict):
            raise Stop('diligences_invalides')
        status = d.get('statut') or 'inclus'
        if status not in STATUSES:
            raise Stop('statut_diligence_invalide')
        r, why = _rate_for(d, cfg, rate)
        pm = _price_minutes(d, cfg, mode)
        amount = _amount(pm, r)
        eff = _effective_minutes(d, cfg)
        minutes[status] += eff
        total[status] += amount
        if status == 'inclus':
            low_min += _bound(d, cfg, -1)
            high_min += _bound(d, cfg, 1)
        lines.append({'n': i + 1, 'libelle_client': str(d.get('libelle_client') or d.get('libelle') or '')[:300], 'type': d.get('type') or 'autre',
                      'minutes_prevues': int(dec(d.get('minutes', 0), 'duree')), 'minutes_effectives': eff, 'minutes_prix': pm, 'taux': str(r),
                      'origine_taux': why, 'montant_ht_cents': amount, 'statut': status, 'source': d.get('source') or 'avocat',
                      'source_libelle': SOURCE_LABELS.get(d.get('source') or 'avocat', d.get('source') or '')})
        if (d.get('source') or '') == 'ia_proposee':
            warnings.append('Durée de « %s » proposée par l’IA : hypothèse à confirmer.' % lines[-1]['libelle_client'][:80])
    central = total['inclus']
    avg = (Decimal(central) / (Decimal(minutes['inclus']) / 60)) if minutes['inclus'] else rate * 100
    low = int((Decimal(low_min) / 60 * avg).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    high = int((Decimal(high_min) / 60 * avg).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    # H02 : forfait non déterminé = None ; zéro = gratuité explicite et motivée ; jamais remplacé par le montant horaire
    fixed = dec(inputs.get('forfait'), 'forfait', allow_none=True) if mode in ('forfait', 'mixte') else None
    free = inputs.get('gratuite') in (True, 'true', 'on', '1')
    if free:
        if not str(inputs.get('motif_gratuite') or '').strip():
            raise Stop('motif_gratuite_requis')
        fixed = Decimal(0)
    elif fixed is not None and fixed == 0:
        raise Stop('forfait_zero_sans_gratuite')
    if mode == 'temps':
        proposed = central
        basis = 'estimation au temps passé du périmètre inclus (les options restent à part)'
    elif fixed is not None:
        proposed = cents(fixed)
        basis = 'forfait choisi par l’avocat' if not free else 'mission gratuite ou remise totale (motif : %s)' % str(inputs.get('motif_gratuite'))[:200]
    else:
        proposed = central
        basis = 'forfait proposé = somme des diligences incluses (forfait à confirmer par l’avocat)'
    cost = cents(Decimal(minutes['inclus']) / 60 * cost_rate)
    hours = Decimal(minutes['inclus']) / 60
    effective_rate = (euros(proposed) / hours).quantize(CENT) if hours else Decimal(0)
    if mode != 'temps' and fixed is not None and not free and high and proposed < high * Decimal('0.7'):
        warnings.append('Forfait inférieur à 70 % de la fourchette haute : risque de sous-évaluation.')
    if hours and effective_rate < cost_rate and not free:
        warnings.append('Taux horaire effectif inférieur au coût de revient du cabinet.')
    if not lines:
        warnings.append('Aucune diligence : le montant proposé ne peut pas être expliqué.')
    pct = inputs.get('provision_pct') if inputs.get('provision_pct') not in (None, '') else cfg['provision_pct']
    prov = provision(proposed, pct, cfg.get('arrondi_provision', 'centime')) if proposed else 0
    explain.append('%s : %s HT.' % (basis[:1].upper() + basis[1:], fmt(proposed)))
    explain.append('Périmètre inclus : %d min (%s h), au taux de %s € HT/h sauf taux particulier.' % (minutes['inclus'], (Decimal(minutes['inclus']) / 60).quantize(
        Decimal('0.01')), rate))
    if total['option']:
        explain.append('Options (accord préalable) : %s HT, présentées séparément.' % fmt(total['option']))
    explain.append('Fourchette réaliste : %s à %s HT.' % (fmt(low), fmt(high)))
    result = {'moteur': ENGINE_VERSION, 'mode': mode, 'taux_horaire': str(rate), 'lignes': lines, 'minutes': minutes, 'montants_ht_cents': total,
              'propose_ht_cents': proposed, 'central_ht_cents': central, 'bas_ht_cents': low, 'haut_ht_cents': high,
              'forfait_ht_cents': cents(fixed) if fixed is not None else None, 'gratuite': free, 'provision_pct': str(dec(pct, 'provision')),
              'provision_ht_cents': prov, 'interne': {'cout_horaire': eco['cout_horaire'], 'cout_previsible_cents': cost, 'marge_interne_cents': proposed - cost,
                                                      'taux_effectif': str(effective_rate), 'taux_cible': eco['taux_cible']},
              'avertissements': warnings, 'explication': explain, 'calcule_le': _now()}
    result['reference_calcul'] = hashlib.sha256(json.dumps({'in': inputs, 'cfg': cfg, 'v': ENGINE_VERSION}, sort_keys=True, ensure_ascii=False,
                                                          default=str).encode()).hexdigest()[:24]
    return result


def client_view(result):
    """Ce que voit le client : prix, périmètre, hypothèses utiles et limites ; jamais le coût interne ni la marge."""
    return {k: v for k, v in result.items() if k != 'interne'} | {'lignes': [{k: v for k, v in l.items() if k not in ('origine_taux',)}
                                                                            for l in result['lignes'] if l['statut'] != 'exclu']}


# ------------------------------------------------------------------------------------------------ estimations enregistrées
SCHEMA = '''CREATE TABLE IF NOT EXISTS b53_estimates(id TEXT PRIMARY KEY, matter TEXT NOT NULL, inputs_json TEXT NOT NULL, result_json TEXT NOT NULL,
  engine_version TEXT NOT NULL, state TEXT NOT NULL, proposed_cents INTEGER NOT NULL, retained_cents INTEGER, signed_cents INTEGER,
  signed_source TEXT NOT NULL DEFAULT '', decided_by TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS b53_estimates_matter ON b53_estimates(matter, updated);'''
ESTIMATE_STATES = {'suggeree': 'Suggérée', 'a_completer': 'À compléter', 'retenue': 'Retenue par l’avocat', 'refusee': 'Refusée', 'perimee': 'Périmée',
                   'signee': 'Signée (figée)'}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)


def save_estimate(desk, actor, matter, inputs):
    ensure_schema(desk)
    result = estimate(desk, inputs)
    eid = 'est-' + secrets.token_hex(8)                       # 5.6.54 : identifiant technique unique
    now = _now()
    desk.db.execute('INSERT INTO b53_estimates(id,matter,inputs_json,result_json,engine_version,state,proposed_cents,created_by,created,updated) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)', (eid, matter, json.dumps(inputs, ensure_ascii=False, default=str), json.dumps(result, ensure_ascii=False),
                                                    ENGINE_VERSION, 'a_completer' if result['avertissements'] else 'suggeree', result['propose_ht_cents'], actor, now, now))
    desk.db.commit()
    return get_estimate(desk, eid)


def get_estimate(desk, eid):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM b53_estimates WHERE id=?', (eid,)).fetchone()
    if not row:
        raise Stop('estimation_absente')
    d = dict(row)
    d['result'] = json.loads(d.pop('result_json'))
    d['inputs'] = json.loads(d.pop('inputs_json'))
    return d


def retain(desk, actor, eid, amount, version):
    """Montant RETENU par l'avocat (peut différer du proposé) ; les estimations retenues antérieures du dossier deviennent périmées."""
    e = get_estimate(desk, eid)
    value = dec(amount, 'montant_retenu')
    if value == 0 and not e['result'].get('gratuite'):
        raise Stop('forfait_zero_sans_gratuite')
    now = _now()
    cur = desk.db.execute("UPDATE b53_estimates SET state='retenue',retained_cents=?,decided_by=?,decided_at=?,version=version+1,updated=? "
                          "WHERE id=? AND version=? AND state IN ('suggeree','a_completer','retenue')", (cents(value), actor, now, now, eid, int(version)))
    if not cur.rowcount:
        raise Stop('version_en_conflit')
    desk.db.execute("UPDATE b53_estimates SET state='perimee',updated=? WHERE matter=? AND id<>? AND state='retenue'", (now, e['matter'], eid))
    desk.db.commit()
    return get_estimate(desk, eid)


def refuse(desk, actor, eid, version):
    cur = desk.db.execute("UPDATE b53_estimates SET state='refusee',decided_by=?,decided_at=?,version=version+1,updated=? WHERE id=? AND version=?",
                          (actor, _now(), _now(), eid, int(version)))
    if not cur.rowcount:
        raise Stop('version_en_conflit')
    desk.db.commit()
    return get_estimate(desk, eid)


def mark_signed(desk, eid, amount_cents, source):
    """Montant CONTRACTUEL figé à la signature de la convention (source et version) ; jamais recalculé ensuite."""
    desk.db.execute("UPDATE b53_estimates SET state='signee',signed_cents=?,signed_source=?,updated=? WHERE id=? AND signed_cents IS NULL",
                    (int(amount_cents), str(source)[:300], _now(), eid))
    desk.db.commit()


def estimates_for(desk, matter):
    ensure_schema(desk)
    return [get_estimate(desk, r['id']) for r in desk.db.execute('SELECT id FROM b53_estimates WHERE matter=? ORDER BY created DESC LIMIT 20', (matter,))]

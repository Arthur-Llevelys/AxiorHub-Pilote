"""5.6.88 : contribution des collaborateurs au chiffre d'affaires — calcul INDICATIF, jamais une écriture comptable.

Base : la part d'honoraires HT réellement encaissée sur les factures émises dans AxiorHub (``b53_documents``, ``b53_payments``). Les frais et
les débours en sont exclus ; les factures d'Invoice Ninja n'y figurent pas, faute de règlements connus ici.

- Chaque règlement vérifié donne une ligne : base cumulée = montant cumulé × honoraires HT ÷ TTC de la facture, arrondie au centime, puis
  différence avec la base cumulée précédente : la somme des lignes retombe exactement sur le total quel que soit le découpage des règlements.
- Un avoir émis réduit ce qui reste exigible ; s'il dépasse ce qui reste dû, une ligne négative est portée à sa date. Le trop-perçu n'entre pas.
- Répartition par personne : prorata des temps facturés sur la facture (montant HT des lignes de temps) ; à défaut, des temps validés du dossier à la
  date de la facture ; à défaut, « Non attribué ». Elle s'ajuste à la main, pour une facture ou pour un seul règlement (pourcentages dont la somme
  fait exactement 100).
- Arrondi : chaque part est tronquée au centime, puis les centimes restants vont aux plus grands restes (à égalité, ordre alphabétique de
  l'identifiant, « Non attribué » en dernier) : même résultat à chaque calcul, et chaque centime ainsi ajusté est affiché.
- Clé de rétrocession par collaborateur, datée (mois de début) : pourcentage et seuil mensuel éventuel ; le pourcentage s'applique à la part de la base
  du mois qui dépasse le seuil. Un mois négatif (avoir) reste affiché négatif : c'est une régularisation, sans report automatique.

Tous les calculs se font en ``Decimal`` et en centimes entiers ; aucun flottant. Lecture et écriture réservées aux droits « facturation » et « voir les
honoraires » ; les dossiers
réservés à d'autres comptes sont exclus du relevé, et le nombre de lignes écartées est affiché.
"""
import csv
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from datetime import datetime, timezone
from html import escape as e
import io
import json
import re
from urllib.parse import urlencode

from .common import Stop, load_matters, matter_display
from . import facturation5653 as fact
from . import honoraires5653 as fees
from . import metier500 as m5
from . import time500

SCHEMA = '''
CREATE TABLE IF NOT EXISTS contrib5688_parts(cible TEXT NOT NULL, personne TEXT NOT NULL, quote TEXT NOT NULL, auteur TEXT NOT NULL, maj TEXT NOT NULL,
  PRIMARY KEY(cible, personne));
CREATE TABLE IF NOT EXISTS contrib5688_cles(personne TEXT NOT NULL, debut TEXT NOT NULL, taux TEXT NOT NULL, seuil_cents INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', auteur TEXT NOT NULL, maj TEXT NOT NULL, PRIMARY KEY(personne, debut));
'''
NOTICE = ('Calcul indicatif, pas une écriture comptable : ces montants ne remplacent ni la comptabilité du cabinet, ni le contrat de collaboration, '
          'ni une facture de rétrocession.')
HUNDRED = Decimal(100)
ORIGINS = {'temps_factures': 'Prorata des temps facturés', 'temps_dossier': 'Prorata des temps validés du dossier (facture sans temps détaillé)',
           'aucun': 'Non attribué : aucun temps enregistré', 'facture': 'Ajustée à la main pour la facture', 'reglement': 'Ajustée à la main pour ce règlement'}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    fact.ensure_schema(desk)
    m5.ensure_schema(desk)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _guard(desk, owner, role):
    """Droits « Facturation et temps passé » et « Voir les honoraires » (l'un peut être retiré sans l'autre), revérifiés ici en plus de la passerelle."""
    fact._require(desk, owner, role, 'facturation')
    fact._require(desk, owner, role, 'voir_honoraires')


def _month(value, default=''):
    value = str(value or default)
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise Stop('mois_invalide')
    return value


def _hidden():
    from .membres5650 import viewer_hidden
    return viewer_hidden() or frozenset()


# ------------------------------------------------------------------------------------------------ calculs
def _round(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def split(amount_cents, weights):
    """Répartit un montant (centimes, signe quelconque) selon des poids Decimal. Renvoie {personne: (part_cents, centime_ajuste)} ; la somme est exacte.

    Troncature, puis centimes restants aux plus grands restes ; à égalité, ordre de l'identifiant, « Non attribué » (identifiant vide) en dernier."""
    weights = {p: Decimal(w) for p, w in weights.items() if Decimal(w) > 0}
    total = sum(weights.values())
    if not weights or total <= 0:
        return {'': (int(amount_cents), 0)}
    sign = -1 if amount_cents < 0 else 1
    size = abs(int(amount_cents))
    with localcontext() as ctx:
        ctx.prec = 60
        exact = {p: Decimal(size) * w / total for p, w in weights.items()}
        floors = {p: int(v.to_integral_value(rounding=ROUND_FLOOR)) for p, v in exact.items()}
        order = sorted(exact, key=lambda p: (-(exact[p] - floors[p]), p == '', p))
    extra = dict.fromkeys(exact, 0)
    for i in range(size - sum(floors.values())):
        extra[order[i % len(order)]] += 1
    return {p: (sign * (floors[p] + extra[p]), sign * extra[p]) for p in exact}


def honoraires_ht(snap):
    return sum(int(l.get('montant_ht_cents') or 0) for l in snap.get('lignes') or [] if (l.get('nature') or 'honoraires') == 'honoraires')


def _entry_author(desk, entry_id, fallback):
    row = desk.db.execute('SELECT author FROM time500_entries WHERE id=?', (str(entry_id),)).fetchone()
    return (row['author'] if row and row['author'] else fallback) or ''


def default_weights(desk, doc, snap):
    """(poids par personne, origine) sans ajustement manuel."""
    weights = {}
    for line in snap.get('lignes') or []:
        if (line.get('nature') or 'honoraires') != 'honoraires':
            continue
        times = [s for s in line.get('sources') or [] if s.get('type') == 'temps']
        for s in times:
            who = _entry_author(desk, s.get('id'), s.get('auteur'))
            weights[who] = weights.get(who, Decimal(0)) + Decimal(int(line.get('montant_ht_cents') or 0)) / len(times)
    if sum(weights.values()) > 0:
        return weights, 'temps_factures'
    rows = desk.db.execute('SELECT author,amount_cents,minutes FROM time500_entries WHERE matter=? AND day<=?', (doc['matter'], doc['issue_date'] or '9999')).fetchall()
    key = 'amount_cents' if any(r['amount_cents'] for r in rows) else 'minutes'
    weights = {}
    for r in rows:
        weights[r['author'] or ''] = weights.get(r['author'] or '', Decimal(0)) + Decimal(int(r[key]))
    if sum(weights.values()) > 0:
        return weights, 'temps_dossier'
    return {'': Decimal(1)}, 'aucun'


def _manual(desk, target):
    return desk.db.execute('SELECT * FROM contrib5688_parts WHERE cible=? ORDER BY personne', (target,)).fetchall()


def weights_for(desk, doc, snap, event_key):
    """Ajustement du règlement, sinon de la facture, sinon prorata par défaut. Renvoie (poids, origine, trace de l'ajustement)."""
    for kind, target in (('reglement', event_key), ('facture', 'facture:' + doc['id'])):
        rows = _manual(desk, target) if target.startswith(kind) else []
        if rows:
            return {r['personne']: Decimal(r['quote']) for r in rows}, kind, {'par': rows[0]['auteur'], 'le': rows[0]['maj'], 'cible': target}
    weights, origin = default_weights(desk, doc, snap)
    return weights, origin, None


def _events(desk, doc):
    rows = []
    for p in desk.db.execute('SELECT * FROM b53_payments WHERE document=? AND verified=1', (doc['id'],)):
        rows.append({'day': p['day'], 'rank': 0, 'stamp': p['created'], 'id': p['id'], 'kind': 'reglement', 'amount': int(p['amount_cents']), 'label': 'Règlement', 'ref': p['reference'] or p['method']})
    for a in desk.db.execute("SELECT * FROM b53_documents WHERE kind='avoir' AND origin=? AND number IS NOT NULL", (doc['id'],)):
        rows.append({'day': a['issue_date'], 'rank': 1, 'stamp': a['created'], 'id': a['id'], 'kind': 'avoir', 'amount': int(json.loads(a['snapshot_json'])['totaux']['ttc_cents']),
                     'label': 'Avoir', 'ref': a['number']})
    return sorted(rows, key=lambda r: (r['day'], r['rank'], r['stamp'], r['id']))


def invoice_lines(desk, doc):
    """Lignes de base HT encaissée d'une facture, toutes dates : base cumulée recalculée à chaque événement, écart porté à sa date."""
    snap = json.loads(doc['snapshot_json'])
    totals = snap['totaux']
    ttc = int(totals['ttc_cents'])
    fee_ht = honoraires_ht(snap)
    if ttc <= 0 or fee_ht <= 0:
        return []
    net, paid, credit, before, out = int(totals['net_a_payer_cents']), 0, 0, 0, []
    for ev in _events(desk, doc):
        if ev['kind'] == 'reglement':
            paid += ev['amount']
        else:
            credit += ev['amount']
        effective = max(0, min(paid, net - credit))           # ni le trop-perçu, ni ce qu'un avoir a annulé
        base = _round(Decimal(fee_ht) * effective / ttc)
        delta, before = base - before, base
        if not delta:
            continue
        key = 'reglement:' + ev['id'] if ev['kind'] == 'reglement' else 'avoir:' + ev['id']
        weights, origin, trace = weights_for(desk, doc, snap, key)
        total = sum(weights.values())
        parts = split(delta, weights)
        out.append({'id': ev['id'], 'evenement': ev['kind'], 'nature': ev['label'], 'reference': ev['ref'], 'jour': ev['day'], 'mois': ev['day'][:7], 'document': doc['id'],
                    'numero': doc['number'], 'matter': doc['matter'], 'base_cents': delta, 'origine': origin, 'origine_label': ORIGINS[origin], 'ajustement': trace,
                    'cible': key,
                    'repartition': [{'personne': p, 'quote': str((weights[p] * HUNDRED / total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)), 'part_cents': parts[p][0],
                                     'arrondi_cents': parts[p][1]} for p in sorted(parts, key=lambda x: (x == '', x))]})
    return out


def lines_of_month(desk, month):
    """Lignes du mois (factures visibles par le compte) et nombre de lignes écartées parce que le dossier est réservé à d'autres comptes."""
    ensure_schema(desk)
    ids = {r[0] for r in desk.db.execute('SELECT document FROM b53_payments WHERE verified=1 AND substr(day,1,7)=?', (month,))}
    ids |= {r[0] for r in desk.db.execute("SELECT origin FROM b53_documents WHERE kind='avoir' AND number IS NOT NULL AND substr(issue_date,1,7)=?", (month,))}
    hidden, out, skipped = _hidden(), [], 0
    for doc_id in sorted(ids):
        doc = desk.db.execute("SELECT * FROM b53_documents WHERE id=? AND kind='facture' AND number IS NOT NULL", (doc_id,)).fetchone()
        if not doc:
            continue
        rows = [x for x in invoice_lines(desk, doc) if x['mois'] == month]
        if doc['matter'] in hidden:
            skipped += len(rows)
        else:
            out += rows
    return sorted(out, key=lambda x: (x['jour'], x['numero'], x['id'])), skipped


def key_for(desk, person, month):
    row = desk.db.execute('SELECT * FROM contrib5688_cles WHERE personne=? AND debut<=? ORDER BY debut DESC LIMIT 1', (person, month)).fetchone()
    return {'taux': row['taux'], 'seuil_cents': int(row['seuil_cents']), 'debut': row['debut']} if row else None


def retrocession(base_cents, key):
    """(assiette, rétrocession indicative) en centimes. Pourcentage appliqué à ce qui dépasse le seuil ; un mois négatif reste négatif."""
    if not key:
        return 0, 0
    seuil = int(key['seuil_cents'])
    base = int(base_cents)
    assiette = base - seuil if base > seuil else (base if base < 0 else 0)
    return assiette, _round(Decimal(assiette) * Decimal(key['taux']) / HUNDRED)


def statement(desk, month, person=None):
    """Relevé mensuel : par personne, base HT encaissée, clé, rétrocession indicative, lignes et arrondis ajustés."""
    month = _month(month)
    lines, skipped = lines_of_month(desk, month)
    if person is not None:
        lines = [x for x in lines if any(r['personne'] == person for r in x['repartition'])]
    bases, rounding = {}, []
    for line in lines:
        for r in line['repartition']:
            if person is not None and r['personne'] != person:
                continue
            bases[r['personne']] = bases.get(r['personne'], 0) + r['part_cents']
            if r['arrondi_cents']:
                rounding.append({'numero': line['numero'], 'jour': line['jour'], 'personne': r['personne'], 'arrondi_cents': r['arrondi_cents']})
    if person is None:                                        # une clé active reste visible même sans encaissement ce mois-là
        for row in desk.db.execute('SELECT DISTINCT personne FROM contrib5688_cles WHERE debut<=?', (month,)):
            bases.setdefault(row[0], 0)
    rows = []
    for who in sorted(bases, key=lambda x: (x == '', x)):
        key = key_for(desk, who, month) if who else None
        assiette, retro = retrocession(bases[who], key)
        rows.append({'personne': who, 'base_cents': bases[who], 'cle': key, 'assiette_cents': assiette, 'retrocession_cents': retro})
    return {'mois': month, 'personnes': rows, 'lignes': lines, 'arrondis': rounding, 'reserves': skipped, 'total_base_cents': sum(r['base_cents'] for r in rows),
            'total_retrocession_cents': sum(r['retrocession_cents'] for r in rows), 'indicatif': NOTICE}


# ------------------------------------------------------------------------------------------------ écritures
def set_parts(desk, owner, role, data):
    """Ajustement à la main d'une facture (``facture:<id>``) ou d'un règlement (``reglement:<id>``) : pourcentages dont la somme fait 100."""
    _guard(desk, owner, role)
    ensure_schema(desk)
    target = str(data.get('cible') or '')
    _check_target(desk, target)
    shares = {}
    for key in data:
        match = re.fullmatch(r'quote_(\d{1,3})', str(key))
        if not match or not str(data[key]).strip():
            continue
        value = fees.dec(data[key], 'quote')
        if value > HUNDRED or value.as_tuple().exponent < -4:
            raise Stop('repartition_invalide')
        if value > 0:
            who = time500.clean_author(data.get('personne_' + match.group(1)))
            shares[who] = shares.get(who, Decimal(0)) + value
    if sum(shares.values()) != HUNDRED:
        raise Stop('repartition_differente_de_100')
    now = _now()
    desk.db.execute('DELETE FROM contrib5688_parts WHERE cible=?', (target,))
    desk.db.executemany('INSERT INTO contrib5688_parts(cible,personne,quote,auteur,maj) VALUES(?,?,?,?,?)',
                        [(target, who, str(value), str(owner)[:120], now) for who, value in shares.items()])
    desk.db.commit()
    desk.audit('contribution5688_repartition', {'cible': target, 'parts': {k: str(v) for k, v in shares.items()}, 'par': str(owner)[:80]})
    return {'message': 'Répartition ajustée. Le calcul reste indicatif.'}


def clear_parts(desk, owner, role, data):
    _guard(desk, owner, role)
    ensure_schema(desk)
    target = str(data.get('cible') or '')
    _check_target(desk, target)
    desk.db.execute('DELETE FROM contrib5688_parts WHERE cible=?', (target,))
    desk.db.commit()
    desk.audit('contribution5688_repartition_retiree', {'cible': target, 'par': str(owner)[:80]})
    return {'message': 'Retour à la répartition par défaut.'}


def _check_target(desk, target):
    kind, _, ident = target.partition(':')
    if kind == 'facture':
        doc = desk.db.execute("SELECT matter FROM b53_documents WHERE id=? AND kind='facture' AND number IS NOT NULL", (ident,)).fetchone()
    elif kind == 'reglement':
        doc = desk.db.execute('SELECT d.matter FROM b53_payments p JOIN b53_documents d ON d.id=p.document WHERE p.id=?', (ident,)).fetchone()
    else:
        doc = None
    if not doc:
        raise Stop('cible_repartition_absente')
    fact._matter(desk, doc['matter'])                          # un dossier réservé reste refusé


def save_key(desk, owner, role, data):
    _guard(desk, owner, role)
    ensure_schema(desk)
    who = time500.clean_author(data.get('personne'))
    if not who:
        raise Stop('personne_requise')
    start = _month(data.get('debut'))
    rate = fees.dec(data.get('taux'), 'pourcentage')
    if rate > HUNDRED or rate.as_tuple().exponent < -4:
        raise Stop('pourcentage_invalide')
    threshold = fees.cents(fees.dec(data.get('seuil') or '0', 'seuil'))
    desk.db.execute('INSERT OR REPLACE INTO contrib5688_cles(personne,debut,taux,seuil_cents,note,auteur,maj) VALUES(?,?,?,?,?,?,?)',
                    (who, start, str(rate), threshold, str(data.get('note') or '')[:200], str(owner)[:120], _now()))
    desk.db.commit()
    desk.audit('contribution5688_cle', {'personne': who, 'debut': start, 'taux': str(rate), 'seuil_cents': threshold, 'par': str(owner)[:80]})
    return {'message': 'Clé de rétrocession enregistrée à partir de %s.' % start}


def remove_key(desk, owner, role, data):
    _guard(desk, owner, role)
    ensure_schema(desk)
    who, start = time500.clean_author(data.get('personne')), _month(data.get('debut'))
    desk.db.execute('DELETE FROM contrib5688_cles WHERE personne=? AND debut=?', (who, start))
    desk.db.commit()
    desk.audit('contribution5688_cle_retiree', {'personne': who, 'debut': start, 'par': str(owner)[:80]})
    return {'message': 'Clé retirée.'}


def keys(desk):
    ensure_schema(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM contrib5688_cles ORDER BY personne, debut DESC')]


# ------------------------------------------------------------------------------------------------ export
def _cell(value):
    """Texte d'une case : un tableur n'interprète jamais une valeur importée comme une formule."""
    text = str(value if value is not None else '')
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


def _euros(cents):
    return str(fees.euros(cents)).replace('.', ',')


def export_csv(desk, owner, role, month, person=None, people=None):
    _guard(desk, owner, role)
    s = statement(desk, month, person)
    people = people or {}
    names = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', lineterminator='\r\n')
    w.writerow([NOTICE])
    w.writerow(['type', 'mois', 'personne', 'date', 'facture', 'dossier', 'evenement', 'reference', 'base_ht_evenement', 'quote_pct', 'part_ht', 'arrondi', 'origine',
                'cle_pct', 'seuil', 'assiette', 'retrocession_indicative'])
    for line in s['lignes']:
        for r in line['repartition']:
            if person is not None and r['personne'] != person:
                continue
            w.writerow(['ligne', s['mois'], _cell(time500.person_label(people, r['personne'])), line['jour'], _cell(line['numero']), _cell(names.get(line['matter'], line['matter'])),
                        line['nature'], _cell(line['reference']), _euros(line['base_cents']), r['quote'].replace('.', ','), _euros(r['part_cents']), _euros(r['arrondi_cents']),
                        line['origine_label'], '', '', '', ''])
    for row in s['personnes']:
        key = row['cle'] or {}
        w.writerow(['total', s['mois'], _cell(time500.person_label(people, row['personne'])), '', '', '', '', '', '', '', _euros(row['base_cents']), '', '',
                    str(key.get('taux', '')).replace('.', ','), _euros(key['seuil_cents']) if key else '', _euros(row['assiette_cents']), _euros(row['retrocession_cents'])])
    m5.log_access(desk, 'honoraires', 'contribution_export', '')
    desk.audit('contribution5688_export', {'mois': s['mois'], 'lignes': len(s['lignes']), 'par': str(owner)[:80]})
    return {'download': {'filename': 'contribution-%s.csv' % s['mois'], 'mime': 'text/csv', 'text': '﻿' + buf.getvalue()}}


# ------------------------------------------------------------------------------------------------ API
def _person_arg(args):
    if 'personne' not in args or args.get('personne') in (None, ''):
        return None
    value = str(args.get('personne'))
    return '' if value == '-' else time500.clean_author(value)


def api_get(desk, owner, role, route, args, auth=None):
    _guard(desk, owner, role)                                 # lecture réservée : la route est ouverte aux rôles, pas aux chiffres
    month = _month(args.get('mois'), fact._today(desk).isoformat()[:7])
    if route == 'contribution/releve':
        m5.log_access(desk, 'honoraires', 'contribution', '')
        return statement(desk, month, _person_arg(args))
    if route == 'contribution/export':
        from .web500 import _people
        return export_csv(desk, owner, role, month, _person_arg(args), _people(desk, auth))
    raise Stop('route_inconnue')


def api_post(desk, owner, role, route, data):
    handlers = {'contribution/parts': set_parts, 'contribution/parts/retirer': clear_parts, 'contribution/cle': save_key, 'contribution/cle/retirer': remove_key}
    if route not in handlers:
        raise Stop('route_inconnue')
    return handlers[route](desk, owner, role, data)


# ------------------------------------------------------------------------------------------------ page
def _money(cents):
    return fees.fmt(int(cents))


def _adjust_form(line, people):
    from .web5653 import _form, _hidden as hidden
    current = {r['personne']: r['quote'] for r in line['repartition']}
    names = sorted(set(people) | set(current) - {''}, key=lambda p: time500.person_label(people, p).casefold()) + ['']
    rows = ''.join('<label>%s (%%)<input name="quote_%d" inputmode="decimal" value="%s"></label><input type="hidden" name="personne_%d" value="%s">' % (
        e(time500.person_label(people, p)), i, e(current.get(p, ''), quote=True), i, e(p, quote=True)) for i, p in enumerate(names))
    if line['evenement'] == 'reglement':
        choice = ('<label>Portée<select name="cible"><option value="%s">Ce règlement seulement</option><option value="%s">Toute la facture</option></select></label>' % (
            e(line['cible'], quote=True), e('facture:' + line['document'], quote=True)))
    else:
        choice = hidden(cible='facture:' + line['document'])
    back = ''
    if line['ajustement']:
        back = _form('contribution/parts/retirer', hidden(cible=line['ajustement']['cible']), 'Revenir au prorata', cls='b53-inline ghost')
    return '<details><summary>Ajuster</summary>%s%s</details>' % (_form('contribution/parts', choice + rows, 'Enregistrer la répartition', cls='f88-parts'), back)


def page(desk, auth, prefix, env, args):
    from .web500 import _people, _person_opts
    from .web5653 import _form, _hidden as hidden
    from .web567 import actor
    owner, role = actor(env)
    try:
        _guard(desk, owner, role)
    except Stop:                                               # aucune donnée chiffrée sans le droit
        return ('<h1>Contribution au chiffre d’affaires</h1><p class="ax-card">Cette page est réservée aux comptes disposant du droit « Facturation et temps passé ». '
                'Vos propres temps restent visibles dans Temps et honoraires.</p>')
    ensure_schema(desk)
    m5.log_access(desk, 'honoraires', 'contribution', '')
    people = _people(desk, auth)
    try:
        month = _month(args.get('mois'), fact._today(desk).isoformat()[:7])
    except Stop:
        month = fact._today(desk).isoformat()[:7]
    person = _person_arg(args)
    s = statement(desk, month, person)
    names = {str(m['id']): matter_display(m) for m in load_matters(desk.c)}
    query = urlencode({'mois': month, **({'personne': '-' if person == '' else person} if person is not None else {})})
    label = lambda p: time500.person_label(people, p)   # noqa: E731
    html = ('<h1>Contribution au chiffre d’affaires</h1><p class="f88-indicatif" role="note"><strong>%s</strong> Base : honoraires HT encaissés sur les factures émises dans '
            'AxiorHub, hors frais et débours ; les factures d’Invoice Ninja n’y figurent pas.</p>'
            '<form class="m5-inline m5-goto f88-filtre" method="get" action="%s/contribution"><label class="m5-field">Mois<input type="month" name="mois" value="%s"></label>'
            '<label class="m5-field">Personne<select name="personne"><option value="">Toutes</option><option value="-"%s>%s</option>%s</select></label>'
            '<button class="ax-btn ghost" type="submit">Afficher</button>'
            '<button type="button" class="ax-btn ghost m5-export" data-api="m5653/contribution/export?%s">Exporter le relevé (CSV)</button></form>') % (
        e(NOTICE), e(prefix, quote=True), e(month, quote=True), ' selected' if person == '' else '', e(time500.NOT_ATTRIBUTED),
        _person_opts(people, person or ''), e(query, quote=True))
    if s['reserves']:
        html += '<p class="b53-warn">%d ligne(s) d’un dossier réservé à d’autres comptes ne figurent pas dans ce relevé.</p>' % s['reserves']
    rows = ''
    for r in s['personnes']:
        key = r['cle']
        rows += '<tr><th scope="row">%s</th><td class="num">%s</td><td>%s</td><td class="num">%s</td><td class="num">%s</td><td class="num">%s</td></tr>' % (
            e(label(r['personne'])), e(_money(r['base_cents'])), e(('%s %% depuis %s' % (key['taux'].replace('.', ','), key['debut'])) if key else 'aucune clé'),
            e(_money(key['seuil_cents'])) if key and key['seuil_cents'] else '—', e(_money(r['assiette_cents'])) if key else '—', e(_money(r['retrocession_cents'])) if key else '—')
    table = ('<div class="table"><table><caption>Relevé de %s</caption><thead><tr><th>Personne</th><th>Base HT encaissée</th><th>Clé</th><th>Seuil du mois</th><th>Assiette</th>'
             '<th>Rétrocession indicative</th></tr></thead><tbody>%s</tbody><tfoot><tr><th scope="row">Total</th><td class="num">%s</td><td></td><td></td><td></td><td class="num">%s</td></tr>'
             '</tfoot></table></div>') % (e(month), rows, e(_money(s['total_base_cents'])), e(_money(s['total_retrocession_cents'])))
    html += '<section class="ax-card"><h2>Relevé mensuel</h2>%s</section>' % (table if rows else '<p class="ax-muted">Aucun encaissement ce mois-ci, et aucune clé active.</p>')
    if s['arrondis']:
        html += ('<section class="ax-card"><h2>Écarts d’arrondi</h2><p class="ax-muted">Chaque part est tronquée au centime ; les centimes restants vont aux plus grands restes, puis, à égalité, '
                 'à l’identifiant le premier dans l’ordre alphabétique. Le total de chaque ligne reste exact.</p><ul>%s</ul></section>') % ''.join(
            '<li>Facture %s du %s : %s à %s</li>' % (e(a['numero']), e(a['jour']), e(('+' if a['arrondi_cents'] > 0 else '') + _money(a['arrondi_cents'])), e(label(a['personne'])))
            for a in s['arrondis'])
    lines = ''
    for line in s['lignes']:
        parts = '<br>'.join('%s : %s (%s %%)' % (e(label(r['personne'])), e(_money(r['part_cents'])), e(r['quote'].replace('.', ','))) for r in line['repartition'])
        how = e(line['origine_label'])
        if line['ajustement']:
            how += ' <small>par %s, le %s</small>' % (e(label(line['ajustement']['par'])), e(line['ajustement']['le'][:10]))
        lines += '<tr><td>%s</td><td><a href="%s">%s</a></td><td>%s</td><td>%s</td><td class="num">%s</td><td>%s</td><td>%s%s</td></tr>' % (
            e(line['jour']), e(prefix + '/facturation?' + urlencode({'document': line['document']}), quote=True), e(line['numero']), e(names.get(line['matter'], line['matter'])),
            e(line['nature']), e(_money(line['base_cents'])), parts, how, _adjust_form(line, people))
    if lines:
        html += ('<section class="ax-card"><h2>Détail des encaissements</h2><div class="table"><table><thead><tr><th>Date</th><th>Facture</th><th>Dossier</th><th>Événement</th><th>Base HT</th>'
                 '<th>Répartition</th><th>Origine</th></tr></thead><tbody>%s</tbody></table></div></section>') % lines
    keyrows = ''.join('<tr><td>%s</td><td>%s</td><td class="num">%s %%</td><td class="num">%s</td><td>%s</td><td>%s</td></tr>' % (
        e(label(k['personne'])), e(k['debut']), e(k['taux'].replace('.', ',')), e(_money(k['seuil_cents'])), e(k['note']),
        _form('contribution/cle/retirer', hidden(personne=k['personne'], debut=k['debut']), 'Retirer', cls='b53-inline ghost')) for k in keys(desk))
    html += ('<section class="ax-card"><h2>Clés de rétrocession</h2><p class="ax-muted">Le pourcentage s’applique à la part de la base du mois qui dépasse le seuil. Une nouvelle clé vaut à partir '
             'de son mois de début : les relevés antérieurs ne changent pas.</p>%s%s</section>') % (
        ('<div class="table"><table><thead><tr><th>Personne</th><th>Début</th><th>Pourcentage</th><th>Seuil mensuel</th><th>Note</th><th></th></tr></thead><tbody>%s</tbody></table></div>' % keyrows)
        if keyrows else '<p class="ax-muted">Aucune clé enregistrée.</p>',
        _form('contribution/cle', '<label>Personne<select name="personne" required>%s</select></label><label>À partir du mois<input type="month" name="debut" value="%s" required></label>'
              '<label>Pourcentage<input name="taux" inputmode="decimal" required></label><label>Seuil mensuel HT (facultatif)<input name="seuil" inputmode="decimal"></label>'
              '<label>Note<input name="note" maxlength="200"></label>' % (_person_opts(people), e(month, quote=True)), 'Enregistrer la clé'))
    return html

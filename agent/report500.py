"""Rapport de pilotage mensuel (AxiorHub 5.0.0).

Une page (et un export) par mois : dossiers actifs, délais à venir, temps et honoraires, brouillons traités, indicateurs
de qualité. Chaque indicateur porte sa définition. Tout est calculé LOCALEMENT à partir de ce que le cabinet a déjà
enregistré ; les temps et honoraires ne comptent que les valeurs VALIDÉES par l'avocat ; une donnée indisponible est
dite indisponible (jamais remplacée par zéro). Un mois terminé est figé à sa première génération (recalcul sur
demande explicite) ; le mois en cours est toujours provisoire.
"""
from datetime import date, datetime, timedelta
import csv
import io
import json
import re
import sqlite3

from .common import Stop
from . import metier500 as m5

STATE_LABELS = {'active': 'Actifs', 'to_confirm': 'À confirmer', 'dormant': 'Dormants', 'archived': 'Archivés'}


def _period(period, today=None):
    today = today or date.today()
    if not period:
        first = today.replace(day=1)
        last_month = first - timedelta(days=1)
        period = last_month.strftime('%Y-%m')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(period)):
        raise Stop('periode_invalide')
    y, m = int(period[:4]), int(period[5:])
    start = date(y, m, 1)
    end = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1))
    return period, start, end


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _matters(desk):
    out = {}
    state_rows = _safe(lambda: {r[0]: r[1] for r in desk.db.execute('SELECT matter,state FROM matter_portfolio')}, {}) or {}
    for mid in m5.matter_index(desk):
        out[mid] = state_rows.get(mid, '')
    return out


def build(desk, period='', today=None):
    from . import time500, limitation500 as lim, echeances450 as ech, conflicts500
    m5.ensure_schema(desk)
    today = today or date.today()
    period, start, end = _period(period, today)
    provisional = end >= today
    d0, d1 = start.isoformat(), end.isoformat()
    states = _matters(desk)
    data = {'period': period, 'start': d0, 'end': d1, 'provisional': provisional, 'generated': m5.now(), 'unavailable': []}

    # --- dossiers
    counts = {k: 0 for k in STATE_LABELS}
    unclassified = 0
    for mid, st in states.items():
        if st in counts:
            counts[st] += 1
        else:
            unclassified += 1
    new = [r[0] for r in desk.db.execute('SELECT matter FROM conflicts500_matters WHERE baseline=0 AND substr(first_seen,1,10)>=? AND substr(first_seen,1,10)<=?', (d0, d1))]
    data['dossiers'] = {'total': len(states), 'by_state': counts, 'unclassified': unclassified, 'opened_in_period': len(new),
                        'definition': 'Répartition selon le classement automatique du portefeuille (actif / dormant / archivé / à confirmer). Les dossiers ouverts dans le mois sont ceux apparus après l’installation de la 5.0.0.'}

    # --- délais à venir (à la date du rapport, ou d'aujourd'hui pour le mois en cours)
    ref = min(end, today) if provisional else end
    upcoming, overdue = [], 0
    def deadline_rows():
        rows = []
        for r in ech.listing(desk):
            if r['due']:
                rows.append({'kind': 'Délai de procédure', 'label': r['rule_label'], 'matter': r['matter_label'], 'due': r['due'], 'status': r['status_label']})
        for r in lim.listing(desk):
            if r['due']:
                rows.append({'kind': 'Prescription', 'label': r['rule_label'], 'matter': r['matter_label'], 'due': r['due'], 'status': r['status_label']})
        return rows
    rows = _safe(deadline_rows)
    if rows is None:
        data['unavailable'].append('delais')
        rows = []
    for r in rows:
        days = (date.fromisoformat(r['due']) - ref).days
        r['days'] = days
        if days < 0:
            overdue += 1
        elif days <= 90:
            upcoming.append(r)
    upcoming.sort(key=lambda r: r['due'])
    data['delais'] = {'reference_date': ref.isoformat(), 'within_30': sum(1 for r in upcoming if r['days'] <= 30), 'within_90': len(upcoming),
                      'overdue_not_closed': overdue, 'list': upcoming[:20],
                      'definition': 'Délais de procédure (4.5) et prescriptions (5.0) non clos, à la date de référence : échus non clos, dans les 30 jours, dans les 90 jours. Les délais « à confirmer » sont comptés et signalés.'}

    # --- temps et honoraires (valeurs validées uniquement)
    ent = [dict(r) for r in desk.db.execute('SELECT matter,minutes,amount_cents,origin FROM time500_entries WHERE day>=? AND day<=?', (d0, d1))]
    per = {}
    for e in ent:
        p = per.setdefault(e['matter'], {'minutes': 0, 'amount_cents': 0})
        p['minutes'] += e['minutes']
        p['amount_cents'] += e['amount_cents']
    inv = [dict(r) for r in desk.db.execute('SELECT matter,amount_cents FROM fees500_invoices WHERE day>=? AND day<=?', (d0, d1))]
    pend = desk.db.execute("SELECT COUNT(*), COALESCE(SUM(minutes_est),0) FROM time500_proposals WHERE status='a_valider' AND day<=?", (d1,)).fetchone()
    decided = desk.db.execute("SELECT status, COUNT(*) FROM time500_proposals WHERE substr(decided,1,10)>=? AND substr(decided,1,10)<=? AND status IN ('valide','ecarte') GROUP BY status", (d0, d1)).fetchall()
    dec = {r[0]: r[1] for r in decided}
    overview = _safe(lambda: time500.overview(desk), []) or []
    to_bill = sum(r['to_bill_cents'] for r in overview)
    alerts = [r for r in overview if r['level'] in time500.ALERT_LEVELS]
    data['honoraires'] = {'validated_minutes': sum(e['minutes'] for e in ent), 'validated_amount_cents': sum(e['amount_cents'] for e in ent),
                          'entries': len(ent), 'invoiced_in_period_cents': sum(i['amount_cents'] for i in inv), 'invoices': len(inv),
                          'to_bill_total_cents': to_bill, 'pending_proposals': pend[0], 'pending_minutes': pend[1],
                          'decided': {'valide': dec.get('valide', 0), 'ecarte': dec.get('ecarte', 0)},
                          'by_matter': sorted(({'matter': m5.matter_label(desk, mid), **v} for mid, v in per.items()), key=lambda x: -x['amount_cents'])[:12],
                          'budget_alerts': [{'matter': r['label'], 'level': r['level'], 'ratio_pct': r['ratio_pct']} for r in alerts],
                          'definition': 'Uniquement les temps validés par l’avocat (date du jour travaillé dans le mois) et les factures enregistrées (hors taxes, date de facture dans le mois). '
                                        '« Reste à facturer » = temps validé cumulé − factures enregistrées, pour les dossiers au temps passé. Les estimations en attente sont exclues des montants.'}

    # --- brouillons traités
    work = _safe(lambda: dict(desk.db.execute("SELECT state, COUNT(*) FROM work_items WHERE substr(resolved,1,10)>=? AND substr(resolved,1,10)<=? GROUP BY state", (d0, d1)).fetchall()))
    prepared = _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM work_items WHERE substr(updated,1,10)>=? AND substr(updated,1,10)<=? AND state IN ('draft_ready','handled')", (d0, d1)).fetchone()[0])
    waiting = _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready'").fetchone()[0])
    outcomes = _safe(lambda: dict(desk.db.execute('SELECT outcome, COUNT(*) FROM outcomes480 WHERE month=? GROUP BY outcome', (period,)).fetchall()), {}) or {}
    total_out = sum(outcomes.values())
    data['brouillons'] = {'handled': (work or {}).get('handled', 0) if work is not None else None, 'prepared_or_updated': prepared, 'waiting_now': waiting,
                          'sent_as_is': outcomes.get('tel_quel', 0), 'sent_light': outcomes.get('leger', 0), 'sent_rewritten': outcomes.get('reecrit', 0), 'sent_total': total_out,
                          'definition': 'Courriels traités (marqués traités dans le mois), brouillons prêts à relire à ce jour, et issue des brouillons envoyés rapprochés avec certitude '
                                        '(tel quel / légèrement corrigé / réécrit, suivi 4.8).'}
    if work is None:
        data['unavailable'].append('brouillons')

    # --- qualité
    corrections = _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM deadline_journal450 WHERE action='correction' AND substr(at,1,10)>=? AND substr(at,1,10)<=?", (d0, d1)).fetchone()[0], 0)
    corrections += _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM limitation500_journal WHERE action='correction' AND substr(at,1,10)>=? AND substr(at,1,10)<=?", (d0, d1)).fetchone()[0], 0)
    errors = _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='error' AND substr(finished,1,10)>=? AND substr(finished,1,10)<=?", (d0, d1)).fetchone()[0])
    done_jobs = _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('done','error') AND substr(finished,1,10)>=? AND substr(finished,1,10)<=?", (d0, d1)).fetchone()[0])
    conflicts_decided = _safe(lambda: desk.db.execute("SELECT COUNT(*) FROM conflicts500_checks WHERE decision<>'' AND substr(decided,1,10)>=? AND substr(decided,1,10)<=?", (d0, d1)).fetchone()[0], 0)
    est_total = dec.get('valide', 0) + dec.get('ecarte', 0)
    data['qualite'] = {
        'drafts_as_is_pct': round(100 * outcomes.get('tel_quel', 0) / total_out, 1) if total_out >= 5 else None,
        'drafts_sample': total_out,
        'deadline_corrections': corrections,
        'job_error_pct': round(100 * errors / done_jobs, 1) if (done_jobs and errors is not None) else None, 'job_errors': errors,
        'time_proposals_accepted_pct': round(100 * dec.get('valide', 0) / est_total, 1) if est_total >= 5 else None, 'time_proposals_sample': est_total,
        'conflict_checks_decided': conflicts_decided,
        'definition': 'Part des brouillons envoyés tels quels (indiquée à partir de 5 courriels) ; corrections manuelles de délais ou prescriptions ; part de traitements automatiques en erreur ; '
                      'part des propositions de temps validées (indiquée à partir de 5 décisions) ; recherches de conflits examinées. Un indicateur sans échantillon suffisant est laissé vide.'}
    data['notice'] = ('Rapport interne, calculé localement à partir des données du cabinet. Il ne remplace ni la comptabilité ni la facturation. '
                      + ('Mois en cours : chiffres provisoires.' if provisional else 'Mois terminé : chiffres figés à la génération.'))
    return data


def generate(desk, period='', force=False, today=None):
    m5.ensure_schema(desk)
    today = today or date.today()
    period, start, end = _period(period, today)
    row = desk.db.execute('SELECT data FROM report500_snapshots WHERE period=?', (period,)).fetchone()
    if row and not force and end < today:
        data = json.loads(row[0])
        data['frozen'] = True
        m5.log_access(desk, 'pilotage', 'rapport_lu')
        return data
    data = build(desk, period, today)
    if end < today:
        desk.db.execute('INSERT OR REPLACE INTO report500_snapshots VALUES(?,?,?)', (period, data['generated'], json.dumps(data, ensure_ascii=False)))
        desk.db.commit()
        desk.audit('pilotage_500_rapport', {'period': period})
    data['frozen'] = False
    m5.log_access(desk, 'pilotage', 'rapport_genere')
    return data


def snapshot_previous(desk, today=None):
    """Fige le rapport du mois écoulé s'il n'existe pas encore (appelé par la maintenance)."""
    today = today or date.today()
    period, _, end = _period('', today)
    if desk.db.execute('SELECT 1 FROM report500_snapshots WHERE period=?', (period,)).fetchone():
        return False
    generate(desk, period, today=today)
    return True


def history(desk):
    m5.ensure_schema(desk)
    return [r[0] for r in desk.db.execute('SELECT period FROM report500_snapshots ORDER BY period DESC LIMIT 36')]


def _hours(minutes):
    return m5.hours(minutes)


def markdown(data):
    d, h, b, q = data['dossiers'], data['honoraires'], data['brouillons'], data['qualite']
    lines = ['# Rapport de pilotage — %s' % data['period'], '', '_%s_' % data['notice'], '',
             '## Dossiers', '',
             '- Dossiers suivis : %d (actifs %d, à confirmer %d, dormants %d, archivés %d%s)' % (
                 d['total'], d['by_state']['active'], d['by_state']['to_confirm'], d['by_state']['dormant'], d['by_state']['archived'],
                 ', non classés %d' % d['unclassified'] if d['unclassified'] else ''),
             '- Dossiers ouverts dans le mois : %d' % d['opened_in_period'], '',
             '## Délais à venir', '',
             '- Échus et non clos : %d ; dans 30 jours : %d ; dans 90 jours : %d' % (data['delais']['overdue_not_closed'], data['delais']['within_30'], data['delais']['within_90'])]
    for r in data['delais']['list'][:15]:
        lines.append('  - %s — %s (%s) : %s, %s' % (r['due'], r['label'], r['kind'], r['matter'], r['status']))
    lines += ['', '## Temps et honoraires (valeurs validées)', '',
              '- Temps validé : %s, soit %s HT' % (_hours(h['validated_minutes']), m5.euros(h['validated_amount_cents'])),
              '- Facturé dans le mois (factures enregistrées) : %s HT (%d facture(s))' % (m5.euros(h['invoiced_in_period_cents']), h['invoices']),
              '- Reste à facturer, cumul : %s HT' % m5.euros(h['to_bill_total_cents']),
              '- Propositions de temps en attente : %d (%s) — non comptées' % (h['pending_proposals'], _hours(h['pending_minutes']))]
    for a in h['budget_alerts']:
        lines.append('- Budget %s : %s (%s %%)' % ('dépassé' if a['level'] == 'depasse' else 'bientôt atteint', a['matter'], a['ratio_pct']))
    lines += ['', '## Brouillons', '',
              '- Courriels traités : %s ; brouillons prêts à relire : %s' % (b['handled'] if b['handled'] is not None else 'indisponible', b['waiting_now'] if b['waiting_now'] is not None else 'indisponible'),
              '- Envoyés : %d (tels quels %d, légèrement corrigés %d, réécrits %d)' % (b['sent_total'], b['sent_as_is'], b['sent_light'], b['sent_rewritten']),
              '', '## Qualité', '',
              '- Brouillons envoyés tels quels : %s' % ('%s %% (sur %d)' % (str(q['drafts_as_is_pct']).replace('.', ','), q['drafts_sample']) if q['drafts_as_is_pct'] is not None else 'échantillon insuffisant (%d)' % q['drafts_sample']),
              '- Corrections manuelles de délais : %d' % q['deadline_corrections'],
              '- Traitements automatiques en erreur : %s' % ('%s %%' % str(q['job_error_pct']).replace('.', ',') if q['job_error_pct'] is not None else 'indisponible'),
              '- Propositions de temps validées : %s' % ('%s %% (sur %d)' % (str(q['time_proposals_accepted_pct']).replace('.', ','), q['time_proposals_sample']) if q['time_proposals_accepted_pct'] is not None else 'échantillon insuffisant (%d)' % q['time_proposals_sample']),
              '- Recherches de conflits examinées : %d' % q['conflict_checks_decided'], '']
    return '\n'.join(lines)


def csv_text(data):
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['rubrique', 'indicateur', 'valeur'])
    d, h, b, q = data['dossiers'], data['honoraires'], data['brouillons'], data['qualite']
    rows = [('dossiers', 'total', d['total'])] + [('dossiers', 'etat_' + k, v) for k, v in d['by_state'].items()] + [('dossiers', 'ouverts_dans_le_mois', d['opened_in_period']),
            ('delais', 'echus_non_clos', data['delais']['overdue_not_closed']), ('delais', 'sous_30_jours', data['delais']['within_30']), ('delais', 'sous_90_jours', data['delais']['within_90']),
            ('honoraires', 'minutes_validees', h['validated_minutes']), ('honoraires', 'montant_valide_ht_eur', '%.2f' % (h['validated_amount_cents'] / 100)),
            ('honoraires', 'facture_ht_eur', '%.2f' % (h['invoiced_in_period_cents'] / 100)), ('honoraires', 'reste_a_facturer_ht_eur', '%.2f' % (h['to_bill_total_cents'] / 100)),
            ('honoraires', 'propositions_en_attente', h['pending_proposals']),
            ('brouillons', 'traites', b['handled']), ('brouillons', 'envoyes', b['sent_total']), ('brouillons', 'tels_quels', b['sent_as_is']),
            ('qualite', 'brouillons_tels_quels_pct', q['drafts_as_is_pct']), ('qualite', 'corrections_delais', q['deadline_corrections']),
            ('qualite', 'traitements_en_erreur_pct', q['job_error_pct']), ('qualite', 'propositions_temps_validees_pct', q['time_proposals_accepted_pct'])]
    for r in rows:
        w.writerow(['' if v is None else v for v in r])
    return out.getvalue()

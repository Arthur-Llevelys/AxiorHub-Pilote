"""Temps et honoraires (AxiorHub 5.0.0).

L'agent PROPOSE un temps estimé par dossier et par jour à partir de ce qu'il voit déjà (courriels rattachés au dossier,
pièces modifiées, livrables préparés, rendez-vous de l'agenda). Une proposition n'est qu'une estimation : elle n'entre
dans les temps et honoraires qu'après validation explicite de l'avocat (qui peut corriger la durée). Aucune valeur de
temps ni d'honoraires n'est enregistrée sans cette validation ; les budgets, alertes et rapprochements ne comptent
que les temps validés (les estimations en attente sont montrées à part, clairement marquées).

Traitement local uniquement : aucun modèle, aucune transmission. Montants en centimes d'euro, hors taxes.
"""
from datetime import date, datetime, timedelta, timezone
import csv
import io
import json
import math
from pathlib import PurePosixPath
import re
import sqlite3

from .common import Stop, digest
from . import metier500 as m5

MODES = {'horaire': 'Au temps passé', 'forfait': 'Forfait', 'mixte': 'Mixte (forfait + temps)'}
BAREME_DEFAUT = {'courriel_recu': 4, 'courriel_envoye': 8, 'acte': 30, 'livrable': 15, 'arrondi': 5, 'plafond_jour': 600,
                 'rendez_vous_defaut': 60, 'rendez_vous_max': 480, 'fenetre_jours': 14}
BAREME_LABELS = {'courriel_recu': 'Courriel reçu et lu (minutes)', 'courriel_envoye': 'Courriel envoyé (minutes)',
                 'acte': 'Pièce créée ou modifiée (minutes par pièce)', 'livrable': 'Livrable préparé par l’agent, relecture (minutes)',
                 'arrondi': 'Arrondi supérieur (minutes)', 'plafond_jour': 'Plafond par dossier et par jour (minutes)',
                 'rendez_vous_defaut': 'Rendez-vous sans heure de fin (minutes)', 'rendez_vous_max': 'Durée maximale retenue d’un rendez-vous (minutes)',
                 'fenetre_jours': 'Fenêtre d’estimation (jours)'}
KIND_LABELS = {'courriel_recu': 'Courriel reçu', 'courriel_envoye': 'Courriel envoyé', 'acte': 'Pièce', 'livrable': 'Livrable préparé',
               'rendez_vous': 'Rendez-vous'}
ACCEPTED_LINKS = ('automatic', 'confirmed')
ALERT_LEVELS = {'proche': 'Budget bientôt atteint', 'depasse': 'Budget dépassé'}


# ----------------------------------------------------------------------------------------------- barème
def bareme(desk):
    stored = desk.settings('time500:bareme', {}) or {}
    out = dict(BAREME_DEFAUT)
    for key, value in stored.items():
        if key in out:
            try:
                out[key] = max(1, min(int(value), 2000))
            except (TypeError, ValueError):
                pass
    return out


def set_bareme(desk, values):
    out = bareme(desk)
    for key, value in (values or {}).items():
        if key not in BAREME_DEFAUT:
            raise Stop('bareme_invalide')
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise Stop('bareme_invalide') from None
        if not 1 <= number <= 2000:
            raise Stop('bareme_invalide')
        out[key] = number
    desk.setting('time500:bareme', out)
    desk.audit('temps_500_bareme', {'keys': sorted(values or {})})
    return out


# ------------------------------------------------------------------------------------------ conditions
def terms(desk, matter):
    m5.ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM time500_terms WHERE matter=?', (matter,)).fetchone()
    if not row:
        return {'matter': matter, 'mode': '', 'rate_cents': 0, 'budget_cents': 0, 'alert_pct': 80, 'note': '', 'defined': False}
    return dict(row) | {'defined': True}


def set_terms(desk, matter, mode, rate='0', budget='0', alert_pct=80, note=''):
    m5.ensure_schema(desk)
    m5.require_matter(desk, matter)
    if mode not in MODES:
        raise Stop('mode_honoraires_invalide')
    rate_cents, budget_cents = m5.cents(rate), m5.cents(budget)
    try:
        alert = int(alert_pct)
    except (TypeError, ValueError):
        raise Stop('seuil_alerte_invalide') from None
    if not 10 <= alert <= 99:
        raise Stop('seuil_alerte_invalide')
    if mode in ('horaire', 'mixte') and rate_cents <= 0:
        raise Stop('taux_horaire_requis')
    if mode == 'forfait' and budget_cents <= 0:
        raise Stop('forfait_requis')
    desk.db.execute('INSERT OR REPLACE INTO time500_terms VALUES(?,?,?,?,?,?,?)',
                    (matter, mode, rate_cents, budget_cents, alert, str(note or '')[:300], m5.now()))
    desk.db.commit()
    desk.audit('honoraires_500_conditions', {'matter': matter, 'mode': mode})
    m5.log_access(desk, 'honoraires', 'conditions_modifiees', matter)
    return terms(desk, matter)


# ------------------------------------------------------------------------------------------ collecte
def _day(value):
    return str(value or '')[:10]


def _is_sent(desk, row):
    folder = str(row['folder'] or '').lower()
    cfg = desk.c.get('mail', {})
    if cfg.get('sent') and folder == str(cfg['sent']).lower():
        return True
    if 'sent' in folder or 'envoy' in folder:
        return True
    own = {str(x).lower() for x in cfg.get('own_addresses', []) + [cfg.get('from_address', ''), cfg.get('username', '')] if x}
    sender = re.sub(r'.*<([^>]+)>.*', r'\1', str(row['sender'] or '')).strip().lower()
    return bool(sender and sender in own)


def _mail_items(desk, matter, since, until, bar):
    rows = desk.db.execute(
        "SELECT id,thread_root,folder,sender,mail_date FROM portfolio_mail_links WHERE matter=? AND status IN (?,?) "
        "AND substr(mail_date,1,10)>=? AND substr(mail_date,1,10)<=? ORDER BY mail_date,id",
        (matter, *ACCEPTED_LINKS, since, until)).fetchall()
    rank, items = {}, []
    for r in rows:
        sent = _is_sent(desk, r)
        day = _day(r['mail_date'])
        group = (r['thread_root'] or r['id'], day, sent)
        rank[group] = rank.get(group, 0) + 1
        base = bar['courriel_envoye'] if sent else bar['courriel_recu']
        minutes = base if rank[group] == 1 else max(1, base // 2)
        if rank[group] > 3:
            minutes = 0
        if not minutes:
            continue
        items.append({'ref': 'mail|' + r['id'], 'kind': 'courriel_envoye' if sent else 'courriel_recu', 'day': day,
                      'minutes': minutes, 'label': 'Courriel %s du %s' % ('envoyé' if sent else 'reçu', day)})
    return items


def _document_items(desk, matter, since, until, bar):
    import sqlite3 as sq
    from pathlib import Path
    path = Path(desk.c['state_dir']) / 'documents.sqlite3'
    if not path.is_file():
        return []
    db = sq.connect('file:%s?mode=ro' % path, uri=True, timeout=5)
    try:
        rows = db.execute("SELECT DISTINCT source_id,path,substr(modified,1,10) FROM knowledge_chunks WHERE matter=? "
                          "AND substr(modified,1,10)>=? AND substr(modified,1,10)<=?", (matter, since, until)).fetchall()
    except sq.Error:
        return []
    finally:
        db.close()
    out = []
    for source_id, p, day in sorted(rows, key=lambda r: (r[2], r[1])):
        out.append({'ref': 'doc|%s|%s|%s' % (matter, source_id, day), 'kind': 'acte', 'day': day, 'minutes': bar['acte'],
                    'label': 'Pièce : ' + PurePosixPath(p).name})
    return out


def _deliverable_items(desk, matter, since, until, bar):
    try:
        rows = desk.db.execute("SELECT id,label,substr(created,1,10) AS day FROM production_deliverables_v420 WHERE matter=? "
                               "AND status IN ('ready','done','delivered','verified','a_relire','pret') AND substr(created,1,10)>=? "
                               "AND substr(created,1,10)<=?", (matter, since, until)).fetchall()
    except sqlite3.Error:
        return []
    return [{'ref': 'liv|' + r['id'], 'kind': 'livrable', 'day': r['day'], 'minutes': bar['livrable'],
             'label': 'Livrable préparé : ' + (r['label'] or 'sans titre')[:80]} for r in rows]


def _parse_dt(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None


def _meeting_items(desk, matter, since, until, bar, now_dt):
    try:
        rows = desk.db.execute("SELECT id,title,starts,ends FROM calendar_cache WHERE matter=? AND substr(starts,1,10)>=? "
                               "AND substr(starts,1,10)<=? ORDER BY starts", (matter, since, until)).fetchall()
    except sqlite3.Error:
        return [], 0
    out, skipped = [], 0
    for r in rows:
        start, end = _parse_dt(r['starts']), _parse_dt(r['ends'])
        if not start or 'T' not in str(r['starts']):      # journée entière : durée inconnue, rien n'est estimé
            skipped += 1
            continue
        if start.tzinfo is None:
            start_cmp = start.replace(tzinfo=timezone.utc)
        else:
            start_cmp = start
        if start_cmp > now_dt:                              # pas encore eu lieu
            continue
        if end and end > start and ('T' in str(r['ends'])):
            minutes = int((end - start).total_seconds() // 60)
        else:
            minutes = bar['rendez_vous_defaut']
        minutes = max(5, min(minutes, bar['rendez_vous_max']))
        out.append({'ref': 'rdv|' + r['id'], 'kind': 'rendez_vous', 'day': _day(r['starts']), 'minutes': minutes,
                    'label': 'Rendez-vous : ' + (r['title'] or 'sans titre')[:80]})
    return out, skipped


def _round_up(minutes, step):
    return int(math.ceil(minutes / step) * step) if step > 1 else int(minutes)


def collect(desk, matter, since, until, now_dt=None):
    bar = bareme(desk)
    now_dt = now_dt or datetime.now(timezone.utc)
    items = _mail_items(desk, matter, since, until, bar) + _document_items(desk, matter, since, until, bar) + \
        _deliverable_items(desk, matter, since, until, bar)
    meetings, skipped = _meeting_items(desk, matter, since, until, bar, now_dt)
    return items + meetings, skipped


# ---------------------------------------------------------------------------------------------- propositions
def estimate(desk, matter='', since='', until='', today=None, now_dt=None):
    """Crée ou complète les PROPOSITIONS de temps. N'enregistre aucun temps."""
    m5.ensure_schema(desk)
    bar = bareme(desk)
    today = today or date.today()
    until = until or today.isoformat()
    since = since or (date.fromisoformat(until) - timedelta(days=bar['fenetre_jours'])).isoformat()
    date.fromisoformat(since), date.fromisoformat(until)
    matters = [matter] if matter else sorted(m5.matter_index(desk))
    if matter:
        m5.require_matter(desk, matter)
    created = updated = 0
    skipped_total = 0
    for mid in matters:
        items, skipped = collect(desk, mid, since, until, now_dt)
        skipped_total += skipped
        known = {r[0] for r in desk.db.execute('SELECT ref FROM time500_items WHERE matter=?', (mid,))}
        fresh = [i for i in items if i['ref'] not in known]
        by_day = {}
        for item in fresh:
            by_day.setdefault(item['day'], []).append(item)
        for day, group in sorted(by_day.items()):
            pending = desk.db.execute("SELECT id FROM time500_proposals WHERE matter=? AND day=? AND status='a_valider'",
                                      (mid, day)).fetchone()
            if pending:
                pid, flag = pending[0], 'updated'
            else:
                seq = (desk.db.execute('SELECT COALESCE(MAX(seq),0) FROM time500_proposals WHERE matter=? AND day=?', (mid, day)).fetchone()[0]) + 1
                pid = digest('time500|%s|%s|%d' % (mid, day, seq))
                desk.db.execute('INSERT INTO time500_proposals VALUES(?,?,?,?,?,?,?,?,?,?)', (pid, mid, day, seq, 0, '', 'a_valider', m5.now(), '', ''))
                flag = 'created'
            for item in group:
                desk.db.execute('INSERT OR IGNORE INTO time500_items VALUES(?,?,?,?,?,?,?)',
                                (item['ref'], pid, item['kind'], item['minutes'], item['label'], day, mid))
            _refresh_proposal(desk, pid, bar)
            created += flag == 'created'
            updated += flag == 'updated'
    desk.db.commit()
    desk.audit('temps_500_estimation', {'matter': matter or '*', 'created': created, 'updated': updated})
    return {'created': created, 'updated': updated, 'since': since, 'until': until, 'not_estimated_all_day_events': skipped_total}


def _refresh_proposal(desk, pid, bar):
    rows = desk.db.execute('SELECT kind,minutes,label FROM time500_items WHERE proposal_id=? ORDER BY kind,label', (pid,)).fetchall()
    total = sum(r['minutes'] for r in rows)
    total = min(_round_up(total, bar['arrondi']), bar['plafond_jour'])
    counts = {}
    for r in rows:
        counts[r['kind']] = counts.get(r['kind'], 0) + 1
    parts = ['%d × %s' % (n, KIND_LABELS.get(k, k).lower()) for k, n in sorted(counts.items())]
    explanation = ' ; '.join(parts) + ' — estimation à partir du barème affiché sur la page, arrondie à %d min.' % bar['arrondi']
    desk.db.execute('UPDATE time500_proposals SET minutes_est=?, explanation=? WHERE id=?', (total, explanation, pid))


def proposal(desk, pid):
    m5.ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM time500_proposals WHERE id=?', (pid,)).fetchone()
    if not row:
        raise Stop('proposition_absente_ou_traitee')
    out = dict(row)
    out['items'] = [dict(r) for r in desk.db.execute('SELECT ref,kind,minutes,label FROM time500_items WHERE proposal_id=? ORDER BY kind,label', (pid,))]
    return out


def pending(desk, matter='', limit=200):
    m5.ensure_schema(desk)
    sql = "SELECT id FROM time500_proposals WHERE status='a_valider'"
    params = []
    if matter:
        sql += ' AND matter=?'
        params.append(matter)
    sql += ' ORDER BY day DESC, matter LIMIT ?'
    params.append(max(1, min(int(limit), 500)))
    return [proposal(desk, r[0]) for r in desk.db.execute(sql, params)]


def _amount(minutes, rate_cents):
    return int(round(minutes * rate_cents / 60.0))


def validate(desk, pid, minutes=None, label='', rate=None):
    """Validation explicite par l'avocat : seul chemin qui transforme une estimation en temps enregistré."""
    row = desk.db.execute("SELECT * FROM time500_proposals WHERE id=? AND status='a_valider'", (pid,)).fetchone()
    if not row:
        raise Stop('proposition_absente_ou_traitee')
    chosen = row['minutes_est'] if minutes in (None, '') else minutes
    try:
        chosen = int(chosen)
    except (TypeError, ValueError):
        raise Stop('duree_invalide') from None
    if not 1 <= chosen <= 1440:
        raise Stop('duree_invalide')
    t = terms(desk, row['matter'])
    rate_cents = m5.cents(rate) if rate not in (None, '') else t['rate_cents']
    entry_id = digest('time500-entry|' + pid)
    desk.db.execute('INSERT OR REPLACE INTO time500_entries VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (entry_id, row['matter'], row['day'], chosen, rate_cents, _amount(chosen, rate_cents),
                     str(label or 'Temps validé')[:200], 'proposition_validee', pid, m5.now(), m5.now()))
    desk.db.execute("UPDATE time500_proposals SET status='valide', decided=? WHERE id=?", (m5.now(), pid))
    desk.db.commit()
    desk.audit('temps_500_valide', {'matter': row['matter'], 'minutes': chosen, 'modified': chosen != row['minutes_est']})
    m5.log_access(desk, 'honoraires', 'temps_valide', row['matter'])
    return {'entry_id': entry_id, 'minutes': chosen, 'amount_cents': _amount(chosen, rate_cents), 'rate_missing': rate_cents <= 0}


def reject(desk, pid, note=''):
    row = desk.db.execute("SELECT matter FROM time500_proposals WHERE id=? AND status='a_valider'", (pid,)).fetchone()
    if not row:
        raise Stop('proposition_absente_ou_traitee')
    desk.db.execute("UPDATE time500_proposals SET status='ecarte', decided=?, note=? WHERE id=?", (m5.now(), str(note or '')[:300], pid))
    desk.db.commit()
    desk.audit('temps_500_ecarte', {'matter': row['matter']})
    return {'rejected': True}


def add_manual(desk, matter, day, minutes, label='', rate=None):
    """Saisie à la main : c'est l'avocat qui saisit, donc la valeur est validée d'emblée."""
    m5.ensure_schema(desk)
    m5.require_matter(desk, matter)
    try:
        date.fromisoformat(str(day))
        minutes = int(minutes)
    except (TypeError, ValueError):
        raise Stop('duree_invalide') from None
    if not 1 <= minutes <= 1440:
        raise Stop('duree_invalide')
    t = terms(desk, matter)
    rate_cents = m5.cents(rate) if rate not in (None, '') else t['rate_cents']
    entry_id = digest('time500-manual|%s|%s|%s|%s' % (matter, day, minutes, m5.now()))
    desk.db.execute('INSERT INTO time500_entries VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (entry_id, matter, str(day), minutes, rate_cents, _amount(minutes, rate_cents), str(label or 'Temps saisi')[:200],
                     'saisie_manuelle', '', m5.now(), m5.now()))
    desk.db.commit()
    desk.audit('temps_500_saisi', {'matter': matter, 'minutes': minutes})
    m5.log_access(desk, 'honoraires', 'temps_saisi', matter)
    return {'entry_id': entry_id, 'amount_cents': _amount(minutes, rate_cents), 'rate_missing': rate_cents <= 0}


def cancel_entry(desk, entry_id, reason):
    reason = m5.need_reason(reason)
    row = desk.db.execute('SELECT * FROM time500_entries WHERE id=?', (entry_id,)).fetchone()
    if not row:
        raise Stop('temps_absent')
    desk.db.execute('DELETE FROM time500_entries WHERE id=?', (entry_id,))
    if row['proposal_id']:
        desk.db.execute("UPDATE time500_proposals SET status='a_valider', decided='' WHERE id=?", (row['proposal_id'],))
    desk.db.commit()
    desk.audit('temps_500_annule', {'matter': row['matter'], 'reason_length': len(reason)})
    m5.log_access(desk, 'honoraires', 'temps_annule', row['matter'])
    return {'cancelled': True}


# ---------------------------------------------------------------------------------------- factures
def add_invoice(desk, matter, number, day, amount, note='', origin='manuelle', external_id=''):
    m5.ensure_schema(desk)
    m5.require_matter(desk, matter)
    number = re.sub(r'\s+', ' ', str(number or '')).strip()[:60]
    if not number:
        raise Stop('numero_facture_requis')
    try:
        date.fromisoformat(str(day))
    except ValueError:
        raise Stop('date_invalide') from None
    amount_cents = m5.cents(amount)
    if amount_cents <= 0:
        raise Stop('montant_invalide')
    iid = digest('fees500-invoice|%s|%s' % (matter, number))
    try:
        desk.db.execute('INSERT INTO fees500_invoices VALUES(?,?,?,?,?,?,?,?,?)',
                        (iid, matter, number, str(day), amount_cents, origin, str(external_id)[:80], str(note or '')[:200], m5.now()))
    except sqlite3.IntegrityError:
        raise Stop('facture_deja_enregistree') from None
    desk.db.commit()
    desk.audit('honoraires_500_facture', {'matter': matter, 'origin': origin})
    m5.log_access(desk, 'honoraires', 'facture_enregistree', matter)
    return {'invoice_id': iid}


def delete_invoice(desk, invoice_id, reason):
    reason = m5.need_reason(reason)
    row = desk.db.execute('SELECT matter FROM fees500_invoices WHERE id=?', (invoice_id,)).fetchone()
    if not row:
        raise Stop('facture_absente')
    desk.db.execute('DELETE FROM fees500_invoices WHERE id=?', (invoice_id,))
    desk.db.commit()
    desk.audit('honoraires_500_facture_supprimee', {'matter': row['matter'], 'reason_length': len(reason)})
    return {'deleted': True}


HEADERS = {'number': ('numero', 'numéro', 'number', 'facture', 'invoice', 'n°'),
           'day': ('date', 'date_facture', 'invoice_date', 'date de facture'),
           'amount': ('montant_ht', 'montant ht', 'total_ht', 'amount_ht', 'ht', 'subtotal', 'montant'),
           'matter': ('dossier', 'matter', 'reference', 'référence')}


def _fr_day(text):
    text = str(text or '').strip()
    m = re.fullmatch(r'(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})', text)
    if m:
        return '%04d-%02d-%02d' % (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return text[:10]


def import_invoices(desk, text, default_matter=''):
    """Import d'un export CSV de factures (HT). Chaque ligne refusée est expliquée ; rien n'est deviné."""
    m5.ensure_schema(desk)
    text = str(text or '')[:2_000_000]
    if not text.strip():
        raise Stop('fichier_vide')
    sample = text[:2000]
    delimiter = ';' if sample.count(';') >= sample.count(',') else ','
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise Stop('fichier_invalide')
    names = {h: h.strip().lower() for h in reader.fieldnames}

    def pick(row, key):
        for original, low in names.items():
            if low in HEADERS[key]:
                return row.get(original, '')
        return ''
    matters = m5.matter_index(desk)
    added, rejected = 0, []
    for line, row in enumerate(reader, start=2):
        if line > 5002:
            rejected.append({'line': line, 'reason': 'Au-delà de 5 000 lignes : import interrompu.'})
            break
        mid = str(pick(row, 'matter') or default_matter).strip()
        if mid not in matters:
            rejected.append({'line': line, 'reason': 'Dossier inconnu ou non indiqué.'})
            continue
        try:
            add_invoice(desk, mid, pick(row, 'number'), _fr_day(pick(row, 'day')), pick(row, 'amount'), origin='import_csv')
            added += 1
        except Stop as ex:
            rejected.append({'line': line, 'reason': m5_reason(str(ex))})
    desk.audit('honoraires_500_import', {'added': added, 'rejected': len(rejected)})
    return {'added': added, 'rejected': rejected[:50], 'rejected_count': len(rejected)}


def m5_reason(code):
    return {'numero_facture_requis': 'Numéro de facture manquant.', 'date_invalide': 'Date invalide (AAAA-MM-JJ ou JJ/MM/AAAA).',
            'montant_invalide': 'Montant invalide.', 'facture_deja_enregistree': 'Facture déjà enregistrée.',
            'dossier_absent': 'Dossier inconnu.'}.get(code, 'Ligne refusée.')


def cached_unpaid(desk, matter):
    """Factures impayées connues de la facturation (cache Invoice Ninja, lecture seule), si le dossier est relié à un client."""
    try:
        link = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (matter,)).fetchone()
        if not link:
            return {'linked': False, 'invoices': [], 'balance': 0.0}
        rows = [dict(r) for r in desk.db.execute('SELECT id,number,status,amount,balance,due_date,currency FROM invoice_ninja_cache_v310 WHERE client_id=? ORDER BY due_date',
                                                  (link['client_id'],))]
        return {'linked': True, 'invoices': rows, 'balance': round(sum(r['balance'] for r in rows), 2)}
    except sqlite3.Error:
        return {'linked': False, 'invoices': [], 'balance': 0.0}


# ----------------------------------------------------------------------------------------------- synthèse
def summary(desk, matter):
    m5.ensure_schema(desk)
    m5.require_matter(desk, matter)
    t = terms(desk, matter)
    entries = [dict(r) for r in desk.db.execute('SELECT * FROM time500_entries WHERE matter=? ORDER BY day DESC, created DESC', (matter,))]
    minutes = sum(e['minutes'] for e in entries)
    amount = sum(e['amount_cents'] for e in entries)
    invoices = [dict(r) for r in desk.db.execute('SELECT * FROM fees500_invoices WHERE matter=? ORDER BY day DESC', (matter,))]
    invoiced = sum(i['amount_cents'] for i in invoices)
    pend = desk.db.execute("SELECT COALESCE(SUM(minutes_est),0), COUNT(*) FROM time500_proposals WHERE matter=? AND status='a_valider'", (matter,)).fetchone()
    pend_amount = _amount(pend[0], t['rate_cents'])
    budget = t['budget_cents']
    ratio = round(100.0 * amount / budget, 1) if budget else None
    if ratio is None:
        level = 'sans_budget'
    elif ratio >= 100:
        level = 'depasse'
    elif ratio >= t['alert_pct']:
        level = 'proche'
    else:
        level = 'ok'
    to_bill = max(0, amount - invoiced) if t['mode'] in ('horaire', 'mixte', '') else 0
    return {'matter': matter, 'terms': t, 'entries': entries, 'minutes': minutes, 'amount_cents': amount, 'invoices': invoices,
            'invoiced_cents': invoiced, 'to_bill_cents': to_bill, 'budget_cents': budget, 'ratio_pct': ratio, 'level': level,
            'pending_minutes': pend[0], 'pending_count': pend[1], 'pending_amount_cents': pend_amount,
            'unpaid': cached_unpaid(desk, matter),
            'note': 'Seuls les temps validés sont comptés. Les estimations en attente sont indiquées à part et ne modifient ni le budget ni les alertes.'}


def reconcile(desk, matter):
    """Rapprochement temps validé / factures enregistrées / factures impayées connues de la facturation."""
    s = summary(desk, matter)
    registered = {i['number'] for i in s['invoices']}
    unregistered = [i for i in s['unpaid']['invoices'] if i['number'] not in registered]
    gap = s['amount_cents'] - s['invoiced_cents']
    if s['terms']['mode'] == 'forfait':
        state = 'forfait'
        text = 'Forfait de %s ; facturé %s ; temps validé (information) %s.' % (m5.euros(s['budget_cents']), m5.euros(s['invoiced_cents']), m5.euros(s['amount_cents']))
    elif gap > 0:
        state = 'a_facturer'
        text = 'Temps validé non couvert par une facture enregistrée : %s (%s).' % (m5.euros(gap), m5.hours(round(gap * 60 / s['terms']['rate_cents'])) if s['terms']['rate_cents'] else 'taux non défini')
    elif gap < 0:
        state = 'sur_facture'
        text = 'Facturé %s de plus que le temps validé : vérifier (acomptes, forfait, temps non saisi).' % m5.euros(-gap)
    else:
        state = 'equilibre'
        text = 'Le temps validé correspond aux factures enregistrées.'
    return {'matter': matter, 'state': state, 'text': text, 'gap_cents': gap, 'invoiced_cents': s['invoiced_cents'],
            'validated_cents': s['amount_cents'], 'unregistered_known_invoices': unregistered, 'unpaid': s['unpaid'],
            'limits': ['Les montants enregistrés sont hors taxes ; le cache de facturation (impayés) contient des montants toutes taxes comprises : '
                       'il n’est pas additionné aux montants hors taxes.',
                       'Une facture absente du registre n’est pas connue de ce rapprochement (la facturation ne remonte que les impayés).']}


def overview(desk):
    m5.ensure_schema(desk)
    rows = []
    for mid in sorted(m5.matter_index(desk)):
        s = summary(desk, mid)
        if s['terms']['defined'] or s['entries'] or s['pending_count'] or s['invoices']:
            rows.append({'matter': mid, 'label': m5.matter_label(desk, mid), 'level': s['level'], 'ratio_pct': s['ratio_pct'],
                         'minutes': s['minutes'], 'amount_cents': s['amount_cents'], 'invoiced_cents': s['invoiced_cents'],
                         'to_bill_cents': s['to_bill_cents'], 'budget_cents': s['budget_cents'], 'pending_count': s['pending_count'],
                         'mode': s['terms']['mode']})
    return rows


def check_alerts(desk, today=None):
    """Alerte (une fois par franchissement) quand le temps validé approche ou dépasse le budget convenu."""
    m5.ensure_schema(desk)
    from .live430 import emit
    sent = 0
    for row in desk.db.execute('SELECT matter FROM time500_terms WHERE budget_cents>0').fetchall():
        mid = row[0]
        try:
            s = summary(desk, mid)
        except Stop:
            continue
        holds = {s['level']} if s['level'] in ALERT_LEVELS else set()
        if s['level'] == 'depasse':
            holds.add('proche')
        for level in list(ALERT_LEVELS):
            exists = desk.db.execute('SELECT 1 FROM fees500_alerts WHERE matter=? AND level=?', (mid, level)).fetchone()
            if level in holds and not exists:
                desk.db.execute('INSERT INTO fees500_alerts VALUES(?,?,?)', (mid, level, m5.now()))
                if level == s['level']:
                    emit(desk, 'honoraires_alerte', '%s : %s (%s %% du budget convenu).' % (
                        ALERT_LEVELS[level], m5.matter_label(desk, mid), str(s['ratio_pct']).replace('.', ',')),
                        matter=mid, dedupe='budget500|%s|%s' % (mid, level))
                    sent += 1
            elif level not in holds and exists:
                desk.db.execute('DELETE FROM fees500_alerts WHERE matter=? AND level=?', (mid, level))
    desk.db.commit()
    return sent


def alerts_now(desk):
    out = []
    for r in overview(desk):
        if r['level'] in ALERT_LEVELS:
            out.append(r)
    return out


def export_entries(desk, matter):
    s = summary(desk, matter)
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['dossier', 'date', 'minutes', 'taux_horaire_ht', 'montant_ht', 'origine', 'libelle'])
    for e in sorted(s['entries'], key=lambda x: x['day']):
        w.writerow([matter, e['day'], e['minutes'], '%.2f' % (e['rate_cents'] / 100), '%.2f' % (e['amount_cents'] / 100), e['origin'], e['label']])
    m5.log_access(desk, 'honoraires', 'export_csv', matter)
    return out.getvalue()

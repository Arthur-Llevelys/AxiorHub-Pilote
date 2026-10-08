"""5.6.9 → 5.6.11 : écritures Invoice Ninja, toujours après validation explicite de l'avocat.

- Facture en **brouillon** créée à partir des temps validés d'un dossier (jamais envoyée : aucun paramètre send_email, mark_sent
  ou paid n'est utilisé ; l'envoi au client reste fait par l'avocat dans Invoice Ninja).
- Temps passés transmis comme tâches (time_log) pour les temps validés.

5.6.11 (audit F01–F03) : montants calculés en centimes (une ligne = quantité 1 × montant convenu ; durée et taux dans le libellé),
réservation atomique de la clé d'idempotence avant tout appel distant (deux demandes simultanées ne créent plus deux factures),
relecture distante rapprochée (identifiant, client, statut brouillon, lignes et montant) ; un écart donne l'état « déposée, conformité
non vérifiée », jamais « vérifiée ». Le réglage invoice_ninja.write_enabled reste désactivé par défaut.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3
from urllib.parse import quote

from .common import HTTP, Stop, digest, load_matters, matter_display, read_secret

SCHEMA = '''CREATE TABLE IF NOT EXISTS invoice_ninja_writes_v569(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, matter TEXT NOT NULL, client_id TEXT NOT NULL, local_ref TEXT NOT NULL,
 remote_id TEXT NOT NULL DEFAULT '', number TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
 proof TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS invoice_ninja_writes_matter_v569 ON invoice_ninja_writes_v569(matter,kind,state);'''
CONFIRM = ('yes', 'oui', '1', 'true')
# États qui réservent les temps concernés (aucune nouvelle écriture tant que l'avocat n'a pas vérifié dans Invoice Ninja).
RESERVED = ('verified', 'uncertain', 'sending', 'deposited_unverified')
STATE_LABELS = {'verified': 'vérifiée', 'uncertain': 'à vérifier dans Invoice Ninja', 'sending': 'en cours',
                'deposited_unverified': 'déposée, conformité non vérifiée'}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def enabled(desk):
    cfg = desk.c.get('invoice_ninja', {})
    return bool(cfg.get('enabled')) and bool(cfg.get('write_enabled'))


def _confirmed(value):
    return value is True or str(value or '').strip().lower() in CONFIRM


def _client(desk, http=None):
    cfg = desk.c.get('invoice_ninja', {})
    if not cfg.get('enabled') or not cfg.get('base_url') or not cfg.get('api_token_file'):
        raise Stop('invoice_ninja_non_configure')
    if not cfg.get('write_enabled'):
        raise Stop('invoice_ninja_ecriture_desactivee')
    client = http or HTTP(str(cfg['base_url']).rstrip('/'), timeout=min(60, max(5, int(cfg.get('timeout_seconds', 30)))))
    if http is None:
        client.headers.update({'X-API-TOKEN': read_secret(cfg['api_token_file']), 'X-Requested-With': 'XMLHttpRequest',
                               'Accept': 'application/json'})
    return client


def _link(desk, matter):
    row = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (str(matter),)).fetchone()
    if not row:
        raise Stop('client_invoice_ninja_non_lie')
    return str(row['client_id'])


def _matter(desk, matter):
    row = next((m for m in load_matters(desk.c) if str(m['id']) == str(matter)), None)
    if not row:
        raise Stop('dossier_absent')
    return row


def _reserve(desk, key, kind, matter, client_id, local_ref, proof, amount='', conflict='facture_deja_creee_verifier_invoice_ninja'):
    """Réservation atomique : une seule écriture possible par clé, même sous deux demandes simultanées (INSERT, jamais REPLACE)."""
    now = desk.now()
    try:
        if not desk.db.in_transaction:
            desk.db.execute('BEGIN IMMEDIATE')
        desk.db.execute('INSERT INTO invoice_ninja_writes_v569 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (key, kind, str(matter), client_id, local_ref, '', '', amount, 'sending', json.dumps(proof, ensure_ascii=False), now, now))
        desk.db.commit()
    except sqlite3.IntegrityError:
        desk.db.rollback()
        raise Stop(conflict) from None


def _update(desk, key, state, proof, remote_id='', number=''):
    desk.db.execute("UPDATE invoice_ninja_writes_v569 SET state=?,proof=?,remote_id=COALESCE(NULLIF(?,''),remote_id),"
                    "number=COALESCE(NULLIF(?,''),number),updated=? WHERE id=?",
                    (state, json.dumps(proof, ensure_ascii=False), remote_id, number, desk.now(), key))
    desk.db.commit()


def _data(result):
    data = result.get('data') if isinstance(result, dict) else None
    if not isinstance(data, dict) or not data.get('id'):
        raise Stop('invoice_ninja_reponse_invalide')
    return data


def writes(desk, matter=''):
    ensure_schema(desk)
    sql = 'SELECT * FROM invoice_ninja_writes_v569'
    params = ()
    if matter:
        sql += ' WHERE matter=?'
        params = (str(matter),)
    return [dict(r) for r in desk.db.execute(sql + ' ORDER BY created DESC LIMIT 100', params)]


def _billed_entries(desk, matter):
    billed = set()
    for r in desk.db.execute("SELECT proof FROM invoice_ninja_writes_v569 WHERE matter=? AND kind='invoice' AND state IN (%s)"
                             % ','.join('?' * len(RESERVED)), (str(matter), *RESERVED)):
        billed.update(json.loads(r['proof'] or '{}').get('entries', []))
    return billed


def billable_entries(desk, matter):
    ensure_schema(desk)
    billed = _billed_entries(desk, matter)
    from .facturation5614 import reserved_entries   # 5.6.14 (C16) : réservations par entrée, y compris les temps facturés depuis Invoice Ninja
    billed |= reserved_entries(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM time500_entries WHERE matter=? ORDER BY day', (str(matter),)) if r['id'] not in billed]


def _euros(cents):
    return float(Decimal(int(cents)) / 100)


def _lines(entries):
    """Une ligne par temps validé : quantité 1, prix = montant convenu en euros exacts (jamais d'arrondi sur les heures)."""
    out = []
    for x in entries:
        minutes = int(x['minutes'])
        out.append({'product_key': 'Honoraires', 'quantity': 1, 'cost': _euros(x['amount_cents']),
                    'notes': '%s — %s — %d h %02d à %s €/h' % (x['day'], str(x['label'])[:160], minutes // 60, minutes % 60,
                                                              ('%.2f' % _euros(x['rate_cents'])).replace('.', ','))})
    return out


def _check_invoice(checked, remote_id, client_id, total_cents, line_count, body=None):
    """5.6.14 (C14) : rapprochement sur tous les champs utiles — identifiant, client exigé, statut, chaque ligne normalisée (libellé, quantité,
    coût, taxe), totaux Decimal, remise, devise ; chaque écart est nommé, rien n'est supposé."""
    issues = []
    if str(checked.get('id')) != str(remote_id):
        issues.append('identifiant différent')
    if not checked.get('client_id'):
        issues.append('client absent de la relecture')
    elif str(checked.get('client_id')) != client_id:
        issues.append('client différent')
    if body is not None:
        from .facturation5614 import check_lines
        issues += [i for i in check_lines(checked, {**body, 'expected_amount': None}, client_id, Decimal(int(total_cents)) / 100) if i not in issues]
        if body.get('public_notes') is not None and checked.get('public_notes') is not None and str(checked.get('public_notes')) != str(body['public_notes']):
            issues.append('notes publiques différentes')
        if checked.get('currency_id') and body.get('currency_id') and str(checked['currency_id']) != str(body['currency_id']):
            issues.append('devise différente')
        if checked.get('due_date') and body.get('due_date') and str(checked['due_date'])[:10] != str(body['due_date'])[:10]:
            issues.append('échéance différente')
    status = checked.get('status_id')
    if status is None:
        issues.append('statut absent')
    elif str(status) != '1':
        issues.append('statut non brouillon (%s)' % status)
    items = checked.get('line_items')
    if isinstance(items, list):
        if len(items) != line_count:
            issues.append('nombre de lignes différent (%d au lieu de %d)' % (len(items), line_count))
        try:
            remote_cents = sum(int(round(Decimal(str(i.get('cost', 0))) * Decimal(str(i.get('quantity', 1))) * 100)) for i in items)
            if remote_cents != int(total_cents):
                issues.append('montant des lignes différent (%.2f au lieu de %.2f)' % (remote_cents / 100, total_cents / 100))
        except (ValueError, ArithmeticError):
            issues.append('lignes illisibles')
    else:
        issues.append('lignes non relues')
    return issues


def draft_invoice(desk, matter, note='', confirm=False, entry_ids=None, http=None):
    """Facture en brouillon dans Invoice Ninja à partir des temps validés non encore facturés du dossier. Rien n'est envoyé."""
    ensure_schema(desk)
    if not _confirmed(confirm):
        raise Stop('confirmation_requise')
    row = _matter(desk, matter)
    client_id = _link(desk, matter)
    entries = billable_entries(desk, matter)
    if entry_ids:
        wanted = {str(x) for x in entry_ids}
        entries = [x for x in entries if x['id'] in wanted]
    if not entries:
        pending = desk.db.execute("SELECT 1 FROM invoice_ninja_writes_v569 WHERE matter=? AND kind='invoice' AND state IN ('uncertain','sending','deposited_unverified')",
                                  (str(matter),)).fetchone()
        raise Stop('facture_deja_creee_verifier_invoice_ninja' if pending else 'aucun_temps_a_facturer')
    if any(int(x['rate_cents']) <= 0 for x in entries):
        raise Stop('taux_horaire_manquant')
    key = digest('invoice569|' + str(matter) + '|' + '|'.join(sorted(x['id'] for x in entries)))
    total_cents = sum(int(x['amount_cents']) for x in entries)
    from .facturation5614 import reserve_entries, confirm_entries, release_entries
    lines = _lines(entries)
    body = {'client_id': client_id, 'date': date.today().isoformat(), 'line_items': lines,
            'public_notes': str(note or '')[:1000], 'private_notes': ('AxiorHub — dossier ' + matter_display(row) + ' — réf. ' + key[:16])[:300]}
    proof = {'entries': [x['id'] for x in entries], 'total_cents': total_cents, 'reference': key[:16]}
    client = _client(desk, http)   # réglages et autorisation vérifiés avant toute trace locale
    reserve_entries(desk, [x['id'] for x in entries], key)   # 5.6.14 (C16) : chaque temps est réservé individuellement (deux sélections qui se recoupent ne peuvent pas facturer le même temps)
    try:
        _reserve(desk, key, 'invoice', matter, client_id, 'entries:%d' % len(entries), proof, amount='%.2f' % (total_cents / 100))
    except Stop:
        release_entries(desk, key, 'cle_facture_deja_reservee')
        raise
    try:
        created = _data(client.json('POST', '/api/v1/invoices', body))
    except Exception as ex:
        # L'écriture a pu aboutir sans réponse : on ne rejoue pas, l'avocat vérifie dans Invoice Ninja (réf. dans les notes privées).
        _update(desk, key, 'uncertain', {**proof, 'error': str(ex)[:200]})
        raise Stop('facture_incertaine_verifier_invoice_ninja') from None
    remote_id = str(created['id'])
    try:
        checked = _data(client.json('GET', '/api/v1/invoices/' + quote(remote_id, safe='')))
    except Exception as ex:
        _update(desk, key, 'uncertain', {**proof, 'error': str(ex)[:200]}, remote_id=remote_id)
        raise Stop('facture_relecture_impossible') from None
    number = str(checked.get('number') or created.get('number') or '')
    issues = _check_invoice(checked, remote_id, client_id, total_cents, len(lines), body)
    confirm_entries(desk, key, remote_invoice=remote_id)
    state = 'verified' if not issues else 'deposited_unverified'
    proof.update({'status_id': checked.get('status_id'), 'remote_amount': checked.get('amount'), 'issues': issues, 'checked_at': desk.now()})
    _update(desk, key, state, proof, remote_id=remote_id, number=number)
    if state == 'verified':
        try:
            from .time500 import add_invoice
            add_invoice(desk, str(matter), number or ('IN-' + remote_id), date.today().isoformat(), '%.2f' % (total_cents / 100))
        except Exception:
            pass   # le rapprochement local reste facultatif ; l'écriture distante est déjà vérifiée
    desk.audit('invoice_ninja_facture_brouillon', {'matter': str(matter), 'remote_id': remote_id, 'number': number, 'entries': len(entries),
                                                   'state': state, 'issues': issues, 'sent': False})
    if issues:
        return {'invoice_id': remote_id, 'number': number, 'amount_ht': round(total_cents / 100, 2), 'entries': len(entries),
                'draft': str(checked.get('status_id')) == '1', 'sent': False, 'verified': False, 'issues': issues,
                'message': 'Facture %s déposée dans Invoice Ninja, conformité non vérifiée (%s) : contrôlez-la avant tout envoi.'
                           % (number or remote_id, ' ; '.join(issues))}
    return {'invoice_id': remote_id, 'number': number, 'amount_ht': round(total_cents / 100, 2), 'entries': len(entries),
            'draft': True, 'sent': False, 'verified': True, 'issues': [],
            'message': 'Facture %s créée en brouillon dans Invoice Ninja et relue (%d temps, %.2f € HT). Rien n’a été envoyé au client.'
                       % (number or remote_id, len(entries), total_cents / 100)}


def log_time(desk, entry_id, confirm=False, http=None):
    """Transmet un temps validé comme tâche Invoice Ninja (time_log en secondes Unix). Une seule fois par temps."""
    ensure_schema(desk)
    if not _confirmed(confirm):
        raise Stop('confirmation_requise')
    entry = desk.db.execute('SELECT * FROM time500_entries WHERE id=?', (str(entry_id),)).fetchone()
    if not entry:
        raise Stop('temps_absent')
    key = digest('task569|' + str(entry_id))
    client_id = _link(desk, entry['matter'])
    from .facturation5614 import time_log as _time_log, reserve_entries, confirm_entries, release_entries
    try:
        log, representation = _time_log(dict(entry))   # 5.6.14 (C15) : intervalle mesuré transmis tel quel ; durée déclarée signalée, aucune heure fabriquée
    except ValueError:
        raise Stop('jour_invalide') from None
    description = (str(entry['label'])[:420] + ' [' + representation + ']')[:500]
    body = {'client_id': client_id, 'description': description, 'time_log': json.dumps(log)}
    if int(entry['rate_cents']) > 0:
        body['rate'] = _euros(entry['rate_cents'])
    proof = {'entry': entry['id'], 'minutes': int(entry['minutes']), 'representation': representation, 'time_log': log}
    client = _client(desk, http)
    _reserve(desk, key, 'task', entry['matter'], client_id, entry['id'], proof, conflict='temps_deja_transmis')
    remote_id = ''
    try:
        created = _data(client.json('POST', '/api/v1/tasks', body))
        remote_id = str(created['id'])
    except Exception as ex:
        _update(desk, key, 'uncertain', {**proof, 'error': str(ex)[:200]})
        raise Stop('temps_incertain_verifier_invoice_ninja') from None
    try:
        checked = _data(client.json('GET', '/api/v1/tasks/' + quote(remote_id, safe='')))
    except Exception as ex:
        # 5.6.14 (C15) : l'identifiant obtenu au POST est conservé pour le rapprochement même si la relecture échoue
        _update(desk, key, 'uncertain', {**proof, 'error': 'relecture: ' + str(ex)[:160]}, remote_id=remote_id)
        raise Stop('temps_relecture_impossible_identifiant_conserve') from None
    issues = []
    if str(checked.get('id')) != remote_id:
        issues.append('identifiant différent')
    if checked.get('client_id') is not None and str(checked.get('client_id')) != client_id:
        issues.append('client différent')
    if checked.get('description') is not None and str(checked.get('description')) != description:
        issues.append('description différente')
    try:
        remote_log = json.loads(checked['time_log']) if isinstance(checked.get('time_log'), str) else checked.get('time_log')
        if remote_log is not None and [[int(a), int(b)] for a, b in remote_log] != log:
            issues.append('intervalle de temps différent')
        if remote_log is not None and sum(int(b) - int(a) for a, b in remote_log) != int(entry['minutes']) * 60:
            issues.append('durée différente')
    except (ValueError, TypeError, KeyError):
        issues.append('time_log illisible')
    if body.get('rate') is not None and checked.get('rate') is not None and abs(float(checked['rate']) - float(body['rate'])) > 0.005:
        issues.append('taux différent')
    if checked.get('project_id') and body.get('project_id') and str(checked['project_id']) != str(body['project_id']):
        issues.append('projet différent')
    state = 'verified' if not issues else 'deposited_unverified'
    _update(desk, key, state, {**proof, 'issues': issues, 'checked_at': desk.now()}, remote_id=remote_id)
    try:
        from .facturation5614 import InvoiceNinjaClient
        InvoiceNinjaClient(desk, http)._map('task', 'entry:' + entry['id'], remote_id)
        # 5.6.14 (C16) : un temps exporté comme tâche ne peut plus être facturé localement en parallèle
        reserve_entries(desk, [entry['id']], key)
        confirm_entries(desk, key, remote_task=remote_id)
    except Stop:
        pass
    desk.audit('invoice_ninja_temps_transmis', {'matter': entry['matter'], 'remote_id': remote_id, 'minutes': int(entry['minutes']), 'state': state})
    return {'task_id': remote_id, 'minutes': int(entry['minutes']), 'verified': not issues, 'issues': issues,
            'message': ('Temps de %d min transmis à Invoice Ninja (tâche %s).' % (int(entry['minutes']), remote_id)) if not issues else
                       'Temps transmis (tâche %s), conformité non vérifiée : %s.' % (remote_id, ' ; '.join(issues))}


def section_html(desk, matter, prefix):
    """Bloc de la page Honoraires : factures en brouillon et temps transmis, ou l'explication de ce qui manque."""
    from html import escape as e
    ensure_schema(desk)
    if not enabled(desk):
        return ('<h3>Invoice Ninja</h3><p class="vf-note">Écriture désactivée : la lecture des impayés reste possible. Pour créer des factures en '
                'brouillon et transmettre les temps, activez « Autoriser la création de factures en brouillon » dans Paramètres › Connexions › Invoice Ninja.</p>')
    linked = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (str(matter),)).fetchone()
    if not linked:
        return '<h3>Invoice Ninja</h3><p class="vf-note">Associez d’abord ce dossier à un client Invoice Ninja (section Impayés, lien client).</p>'
    entries = billable_entries(desk, matter)
    total = sum(int(x['amount_cents']) for x in entries)
    rows = writes(desk, matter)
    table = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        e(r['created'][:10]), e('Facture brouillon' if r['kind'] == 'invoice' else 'Temps transmis'), e(r['number'] or r['remote_id'] or '—'),
        e(r['amount'] or ''), e(STATE_LABELS.get(r['state'], r['state']) + ((' — ' + ' ; '.join(json.loads(r['proof'] or '{}').get('issues', [])))
                                                                           if r['state'] == 'deposited_unverified' else '')))
        for r in rows)
    form = ('<form class="m5-form m5-inline" data-api="m500/invoice_ninja/draft" data-reload="1" '
            'data-confirm="Créer une facture en brouillon dans Invoice Ninja pour %d temps (%.2f € HT) ? Rien ne sera envoyé au client.">'
            '<input type="hidden" name="matter" value="%s"><input type="hidden" name="confirm" value="yes">'
            '<label class="m5-field">Note publique (facultative)<input type="text" name="note" maxlength="1000"></label>'
            '<button class="ax-btn" type="submit">Créer la facture en brouillon (%d temps, %.2f € HT)</button></form>'
            % (len(entries), total / 100, e(str(matter), quote=True), len(entries), total / 100)) if entries else \
        '<p class="vf-note">Aucun temps validé restant à facturer.</p>'
    return ('<h3>Invoice Ninja (écriture après validation)</h3><p class="vf-note">La facture est créée en brouillon puis relue (client, statut, '
            'lignes, montant) ; vous la relisez et l’envoyez depuis Invoice Ninja. Chaque temps peut aussi être transmis comme tâche.</p>%s%s' % (
                form, ('<table class="vf-table"><thead><tr><th>Date</th><th>Écriture</th><th>Référence</th><th>Montant HT</th><th>État</th></tr></thead>'
                       '<tbody>%s</tbody></table>' % table) if rows else ''))


def task_button_html(desk, entry):
    """Bouton « Transmettre » d'une ligne de temps validé (vide si déjà transmis ou écriture désactivée)."""
    if not enabled(desk):
        return ''
    ensure_schema(desk)
    key = digest('task569|' + str(entry['id']))
    old = desk.db.execute('SELECT state FROM invoice_ninja_writes_v569 WHERE id=?', (key,)).fetchone()
    if old and old['state'] in RESERVED:
        return '<span class="vf-badge muted">transmis</span>'
    from html import escape as e
    return ('<form class="m5-form m5-inline m5-mini" data-api="m500/invoice_ninja/task" data-reload="1" data-confirm="Transmettre ce temps à Invoice Ninja ?">'
            '<input type="hidden" name="id" value="%s"><input type="hidden" name="confirm" value="yes"><button class="ax-btn ghost" type="submit">Transmettre</button></form>'
            % e(str(entry['id']), quote=True))

"""5.6.9 : écritures Invoice Ninja, toujours après validation explicite de l'avocat.

- Facture en **brouillon** créée à partir des temps validés d'un dossier (jamais envoyée : aucun paramètre send_email, mark_sent
  ou paid n'est utilisé ; l'envoi au client reste fait par l'avocat dans Invoice Ninja).
- Temps passés transmis comme tâches (time_log) pour les temps validés.

Chaque écriture est enregistrée localement avec une clé d'idempotence, relue par GET avant d'être dite « vérifiée », et jamais
répétée. Le réglage invoice_ninja.write_enabled reste désactivé par défaut ; la lecture des impayés (5.3) ne change pas.
"""
from datetime import date, datetime, timedelta, timezone
import json
from urllib.parse import quote

from .common import HTTP, Stop, digest, load_matters, matter_display, read_secret

SCHEMA = '''CREATE TABLE IF NOT EXISTS invoice_ninja_writes_v569(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, matter TEXT NOT NULL, client_id TEXT NOT NULL, local_ref TEXT NOT NULL,
 remote_id TEXT NOT NULL DEFAULT '', number TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
 proof TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS invoice_ninja_writes_matter_v569 ON invoice_ninja_writes_v569(matter,kind,state);'''
CONFIRM = ('yes', 'oui', '1', 'true')


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


def _save(desk, key, kind, matter, client_id, local_ref, state, proof, remote_id='', number='', amount=''):
    now = desk.now()
    desk.db.execute('INSERT OR REPLACE INTO invoice_ninja_writes_v569 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (key, kind, str(matter), client_id, local_ref, remote_id, number, amount, state,
                     json.dumps(proof, ensure_ascii=False), now, now))
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
    for r in desk.db.execute("SELECT proof FROM invoice_ninja_writes_v569 WHERE matter=? AND kind='invoice' AND state IN ('verified','uncertain','sending')",
                             (str(matter),)):
        billed.update(json.loads(r['proof'] or '{}').get('entries', []))
    return billed


def billable_entries(desk, matter):
    ensure_schema(desk)
    billed = _billed_entries(desk, matter)
    return [dict(r) for r in desk.db.execute('SELECT * FROM time500_entries WHERE matter=? ORDER BY day', (str(matter),)) if r['id'] not in billed]


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
        pending = desk.db.execute("SELECT 1 FROM invoice_ninja_writes_v569 WHERE matter=? AND kind='invoice' AND state IN ('uncertain','sending')",
                                  (str(matter),)).fetchone()
        raise Stop('facture_deja_creee_verifier_invoice_ninja' if pending else 'aucun_temps_a_facturer')
    if any(int(x['rate_cents']) <= 0 for x in entries):
        raise Stop('taux_horaire_manquant')
    key = digest('invoice569|' + str(matter) + '|' + '|'.join(sorted(x['id'] for x in entries)))
    old = desk.db.execute('SELECT state FROM invoice_ninja_writes_v569 WHERE id=?', (key,)).fetchone()
    if old and old['state'] in ('verified', 'uncertain', 'sending'):
        raise Stop('facture_deja_creee_verifier_invoice_ninja')
    total_cents = sum(int(x['amount_cents']) for x in entries)
    lines = [{'product_key': 'Honoraires', 'quantity': round(int(x['minutes']) / 60, 2), 'cost': round(int(x['rate_cents']) / 100, 2),
              'notes': '%s — %s' % (x['day'], str(x['label'])[:180])} for x in entries]
    body = {'client_id': client_id, 'date': date.today().isoformat(), 'line_items': lines,
            'public_notes': str(note or '')[:1000], 'private_notes': ('AxiorHub — dossier ' + matter_display(row))[:300]}
    proof = {'entries': [x['id'] for x in entries], 'total_cents': total_cents}
    client = _client(desk, http)   # réglages et autorisation vérifiés avant toute trace locale
    _save(desk, key, 'invoice', matter, client_id, 'entries:%d' % len(entries), 'sending', proof, amount='%.2f' % (total_cents / 100))
    try:
        created = _data(client.json('POST', '/api/v1/invoices', body))
    except Exception as ex:
        # L'écriture a pu aboutir sans réponse : on ne rejoue pas, l'avocat vérifie dans Invoice Ninja.
        _save(desk, key, 'invoice', matter, client_id, 'entries:%d' % len(entries), 'uncertain', {**proof, 'error': str(ex)[:200]},
              amount='%.2f' % (total_cents / 100))
        raise Stop('facture_incertaine_verifier_invoice_ninja') from None
    remote_id = str(created['id'])
    try:
        checked = _data(client.json('GET', '/api/v1/invoices/' + quote(remote_id, safe='')))
    except Exception as ex:
        _save(desk, key, 'invoice', matter, client_id, 'entries:%d' % len(entries), 'uncertain', {**proof, 'error': str(ex)[:200]},
              remote_id=remote_id, amount='%.2f' % (total_cents / 100))
        raise Stop('facture_relecture_impossible') from None
    number = str(checked.get('number') or created.get('number') or '')
    status_id = str(checked.get('status_id') or '1')
    proof.update({'status_id': status_id, 'remote_amount': checked.get('amount'), 'verified_at': desk.now()})
    _save(desk, key, 'invoice', matter, client_id, 'entries:%d' % len(entries), 'verified', proof, remote_id=remote_id, number=number,
          amount='%.2f' % (total_cents / 100))
    try:
        from .time500 import add_invoice
        add_invoice(desk, str(matter), number or ('IN-' + remote_id), date.today().isoformat(), '%.2f' % (total_cents / 100))
    except Exception:
        pass   # le rapprochement local reste facultatif ; l'écriture distante est déjà vérifiée
    desk.audit('invoice_ninja_facture_brouillon', {'matter': str(matter), 'remote_id': remote_id, 'number': number,
                                                   'entries': len(entries), 'draft': status_id == '1', 'sent': False})
    return {'invoice_id': remote_id, 'number': number, 'amount_ht': round(total_cents / 100, 2), 'entries': len(entries),
            'draft': status_id == '1', 'sent': False,
            'message': 'Facture %s créée en brouillon dans Invoice Ninja (%d temps, %.2f € HT). Rien n’a été envoyé au client.'
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
    old = desk.db.execute('SELECT state FROM invoice_ninja_writes_v569 WHERE id=?', (key,)).fetchone()
    if old and old['state'] in ('verified', 'uncertain', 'sending'):
        raise Stop('temps_deja_transmis')
    client_id = _link(desk, entry['matter'])
    try:
        start = datetime.fromisoformat(str(entry['day'])).replace(hour=9, minute=0, tzinfo=timezone.utc)
    except ValueError:
        raise Stop('jour_invalide') from None
    end = start + timedelta(minutes=int(entry['minutes']))
    body = {'client_id': client_id, 'description': str(entry['label'])[:500],
            'time_log': json.dumps([[int(start.timestamp()), int(end.timestamp())]])}
    if int(entry['rate_cents']) > 0:
        body['rate'] = round(int(entry['rate_cents']) / 100, 2)
    proof = {'entry': entry['id'], 'minutes': int(entry['minutes'])}
    client = _client(desk, http)
    _save(desk, key, 'task', entry['matter'], client_id, entry['id'], 'sending', proof)
    try:
        created = _data(client.json('POST', '/api/v1/tasks', body))
        remote_id = str(created['id'])
        _data(client.json('GET', '/api/v1/tasks/' + quote(remote_id, safe='')))
    except Exception as ex:
        _save(desk, key, 'task', entry['matter'], client_id, entry['id'], 'uncertain', {**proof, 'error': str(ex)[:200]})
        raise Stop('temps_incertain_verifier_invoice_ninja') from None
    _save(desk, key, 'task', entry['matter'], client_id, entry['id'], 'verified', {**proof, 'verified_at': desk.now()}, remote_id=remote_id)
    desk.audit('invoice_ninja_temps_transmis', {'matter': entry['matter'], 'remote_id': remote_id, 'minutes': int(entry['minutes'])})
    return {'task_id': remote_id, 'minutes': int(entry['minutes']),
            'message': 'Temps de %d min transmis à Invoice Ninja (tâche %s).' % (int(entry['minutes']), remote_id)}


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
        e(r['amount'] or ''), e({'verified': 'vérifiée', 'uncertain': 'à vérifier dans Invoice Ninja', 'sending': 'en cours'}.get(r['state'], r['state'])))
        for r in rows)
    form = ('<form class="m5-form m5-inline" data-api="m500/invoice_ninja/draft" data-reload="1" '
            'data-confirm="Créer une facture en brouillon dans Invoice Ninja pour %d temps (%.2f € HT) ? Rien ne sera envoyé au client.">'
            '<input type="hidden" name="matter" value="%s"><input type="hidden" name="confirm" value="yes">'
            '<label class="m5-field">Note publique (facultative)<input type="text" name="note" maxlength="1000"></label>'
            '<button class="ax-btn" type="submit">Créer la facture en brouillon (%d temps, %.2f € HT)</button></form>'
            % (len(entries), total / 100, e(str(matter), quote=True), len(entries), total / 100)) if entries else \
        '<p class="vf-note">Aucun temps validé restant à facturer.</p>'
    return ('<h3>Invoice Ninja (écriture après validation)</h3><p class="vf-note">La facture est créée en brouillon ; vous la relisez et l’envoyez '
            'depuis Invoice Ninja. Chaque temps peut aussi être transmis comme tâche.</p>%s%s' % (
                form, ('<table class="vf-table"><thead><tr><th>Date</th><th>Écriture</th><th>Référence</th><th>Montant HT</th><th>État</th></tr></thead>'
                       '<tbody>%s</tbody></table>' % table) if rows else ''))


def task_button_html(desk, entry):
    """Bouton « Transmettre » d'une ligne de temps validé (vide si déjà transmis ou écriture désactivée)."""
    if not enabled(desk):
        return ''
    ensure_schema(desk)
    key = digest('task569|' + str(entry['id']))
    old = desk.db.execute('SELECT state FROM invoice_ninja_writes_v569 WHERE id=?', (key,)).fetchone()
    if old and old['state'] in ('verified', 'uncertain', 'sending'):
        return '<span class="vf-badge muted">transmis</span>'
    from html import escape as e
    return ('<form class="m5-form m5-inline m5-mini" data-api="m500/invoice_ninja/task" data-reload="1" data-confirm="Transmettre ce temps à Invoice Ninja ?">'
            '<input type="hidden" name="id" value="%s"><input type="hidden" name="confirm" value="yes"><button class="ax-btn ghost" type="submit">Transmettre</button></form>'
            % e(str(entry['id']), quote=True))

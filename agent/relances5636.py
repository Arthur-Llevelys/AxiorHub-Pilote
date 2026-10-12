"""5.6.36 : relances d'impayés — chaque matin, les factures échues d'Invoice Ninja donnent des projets de relance à valider.

- Lecture seule d'Invoice Ninja (synchronisation existante des impayés, méthode GET) : aucune écriture, aucun paiement, aucun envoi.
- Une facture échue depuis plus de N jours (délai de grâce) reçoit un projet de relance ; le niveau monte à chaque relance déposée
  (1 : rappel courtois ; 2 : relance ; 3 : dernière relance avant mise en demeure), jamais plus d'une relance par intervalle.
- Le destinataire vient des contacts du dossier (catégorie client), sinon des correspondants « client » du dossier, sinon du
  contact principal du client dans Invoice Ninja (lecture).
- L'avocat relit et corrige chaque projet, puis « Déposer dans Brouillons » (la messagerie l'envoie quand il le décide) ou « Écarter » ;
  « Ne plus relancer » exclut une facture (litige, échéancier).
"""
from datetime import date, datetime, timedelta, timezone
from html import escape as e
import json
import re
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation

from .common import Stop, load_matters, matter_display

SETTINGS = 'relances5636:reglages'
DEFAULTS = {'enabled': True, 'hour': 8, 'grace_days': 3, 'interval_days': 10, 'max_level': 3}
LEVELS = {1: 'Rappel courtois', 2: 'Relance', 3: 'Dernière relance avant mise en demeure'}


def schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS relances5636(id INTEGER PRIMARY KEY AUTOINCREMENT, invoice TEXT NOT NULL, number TEXT NOT NULL, client_id TEXT NOT NULL,
      matter TEXT NOT NULL DEFAULT '', level INTEGER NOT NULL, recipient TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL, body TEXT NOT NULL,
      balance TEXT NOT NULL, currency TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL, state TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
      draft_uid TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS relances_exclues5636(invoice TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS relances_runs5636(day TEXT PRIMARY KEY, created TEXT NOT NULL);''')
    # 5.6.37 (audit F13, F14, F15) : envoi confirmé, contrôle du solde avant dépôt, routine du jour récupérable
    cols = {r[1] for r in desk.db.execute('PRAGMA table_info(relances5636)')}
    for name, decl in (('sent_at', "TEXT NOT NULL DEFAULT ''"), ('checked_at', "TEXT NOT NULL DEFAULT ''"), ('check_note', "TEXT NOT NULL DEFAULT ''")):
        if name not in cols:
            desk.db.execute('ALTER TABLE relances5636 ADD COLUMN %s %s' % (name, decl))
    # un seul projet ouvert (à valider ou déposé, en attente de confirmation) par facture ; les projets écartés restent tracés
    desk.db.execute('DROP INDEX IF EXISTS relances5636_level')
    desk.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS relances5636_ouverte ON relances5636(invoice) WHERE state IN ('a_valider','deposee')")
    runs = {r[1] for r in desk.db.execute('PRAGMA table_info(relances_runs5636)')}
    for name, decl in (('state', "TEXT NOT NULL DEFAULT 'lancee'"), ('job', 'INTEGER'), ('next_retry', "TEXT NOT NULL DEFAULT ''"),
                       ('last_error', "TEXT NOT NULL DEFAULT ''")):
        if name not in runs:
            desk.db.execute('ALTER TABLE relances_runs5636 ADD COLUMN %s %s' % (name, decl))
    desk.db.commit()


def settings(desk):
    raw = desk.settings(SETTINGS, {}) or {}
    out = dict(DEFAULTS)
    out.update({k: raw[k] for k in DEFAULTS if k in raw})
    return out


def save_settings(desk, data, owner='cabinet'):
    cur = settings(desk)
    cur['enabled'] = bool(data.get('enabled'))
    for k, lo, hi in (('hour', 5, 12), ('grace_days', 0, 60), ('interval_days', 3, 60), ('max_level', 1, 3)):
        if str(data.get(k, '')).strip():
            try:
                cur[k] = max(lo, min(hi, int(data[k])))
            except (TypeError, ValueError):
                raise Stop('reglage_hors_plage_' + k) from None
    desk.setting(SETTINGS, cur)
    desk.audit('relances5636_reglages', {'owner': owner, **cur})
    return {**cur, 'message': 'Réglages des relances enregistrés.'}


def configured(desk):
    cfg = desk.c.get('invoice_ninja', {}) or {}
    return bool(cfg.get('enabled') and cfg.get('base_url') and cfg.get('api_token_file'))


def _tz(desk):
    try:
        return ZoneInfo(str((desk.c.get('calendar') or {}).get('timezone') or 'Europe/Paris'))
    except Exception:   # noqa: BLE001
        return timezone.utc


def schedule(desk, stamp=None):
    """Cycle de surveillance : une fois par jour, à partir de l'heure choisie (matin), si Invoice Ninja est relié."""
    s = settings(desk)
    if not s['enabled'] or not configured(desk):
        return []
    schema(desk)
    now = datetime.fromtimestamp(stamp, timezone.utc) if stamp else datetime.now(timezone.utc)
    local = now.astimezone(_tz(desk))
    if local.hour < int(s['hour']):
        return []
    day = local.date().isoformat()
    # 5.6.37 (audit F15) : seule une routine réellement mise en file empêche une nouvelle tentative ; une file pleine reste récupérable
    desk.db.execute("INSERT OR IGNORE INTO relances_runs5636(day,created,state) VALUES(?,?,'reservee')", (day, now.isoformat()))
    desk.db.commit()
    run = desk.db.execute('SELECT * FROM relances_runs5636 WHERE day=?', (day,)).fetchone()
    if run['state'] == 'lancee' or (run['next_retry'] and run['next_retry'] > now.isoformat()):
        return []
    try:
        job = desk.enqueue('relances5636', {'day': day}, priority=60)   # arguments identiques : deux cycles concurrents rejoignent le même travail
    except Stop as exc:
        retry = (now + timedelta(minutes=15)).isoformat()
        desk.db.execute("UPDATE relances_runs5636 SET state='en_attente', next_retry=?, last_error=? WHERE day=?", (retry, str(exc), day))
        desk.db.commit()
        return []
    desk.db.execute("UPDATE relances_runs5636 SET state='lancee', job=?, next_retry='', last_error='' WHERE day=?", (job, day))
    desk.db.commit()
    return [job]


def _money(value, currency):
    try:
        amount = '{:,.2f}'.format(float(value)).replace(',', ' ').replace('.', ',')
    except (TypeError, ValueError):
        amount = str(value)
    return amount + ' ' + ({'EUR': '€', '': '€'}.get(currency, currency))


def _matter_for(desk, client_id):
    row = desk.db.execute('SELECT matter FROM matter_invoice_links_v310 WHERE client_id=? ORDER BY updated DESC LIMIT 1', (client_id,)).fetchone()
    return row['matter'] if row else ''


def _recipient(desk, matter, client_id, http=None):
    if matter:
        try:
            from .crm5636 import for_matter
            for c in for_matter(desk, matter):
                if c['role'] == 'client' and c.get('email'):
                    return c['email']
        except Stop:
            pass
        m = next((x for x in load_matters(desk.c) if x['id'] == matter), None)
        for c in (m or {}).get('correspondents') or []:
            if c.get('role') == 'client' and c.get('email'):
                return str(c['email']).lower()
    try:   # contact principal du client dans Invoice Ninja (lecture)
        from .workstation import HTTP, read_secret
        cfg = desk.c.get('invoice_ninja', {})
        client = http or HTTP(str(cfg['base_url']).rstrip('/'), timeout=20)
        if http is None:
            client.headers.update({'X-API-TOKEN': read_secret(cfg['api_token_file']), 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
        raw = client.request('GET', client.base + '/api/v1/clients/' + re.sub(r'[^A-Za-z0-9]', '', client_id), headers={'Accept': 'application/json'},
                             limit=500_000)
        data = (json.loads(raw) or {}).get('data') or {}
        for c in data.get('contacts') or []:
            if isinstance(c, dict) and re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', str(c.get('email') or '')):
                return str(c['email']).lower()
    except (Stop, KeyError, ValueError, TypeError, OSError, ImportError):
        pass
    return ''


def _text(level, number, amount, due, url, sign):
    due_fr = '/'.join(reversed(due.split('-'))) if re.fullmatch(r'\d{4}-\d{2}-\d{2}', due or '') else due
    pay = ('\n\nVous pouvez la consulter et la régler en ligne : ' + url) if url else ''
    if level == 1:
        subject = 'Rappel — facture %s' % number
        body = ('Madame, Monsieur,\n\nSauf erreur de notre part, la facture n° %s d’un montant restant dû de %s, échue le %s, n’a pas encore été '
                'réglée.%s\n\nSi votre règlement a été effectué entre-temps, nous vous prions de ne pas tenir compte de ce message.\n\n'
                'Nous vous remercions par avance et vous prions d’agréer nos salutations distinguées.\n\n%s') % (number, amount, due_fr, pay, sign)
    elif level == 2:
        subject = 'Relance — facture %s impayée' % number
        body = ('Madame, Monsieur,\n\nMalgré notre précédent rappel, la facture n° %s (reste dû : %s, échéance du %s) demeure impayée.%s\n\n'
                'Nous vous remercions de bien vouloir procéder à son règlement dans les meilleurs délais ou de nous indiquer toute difficulté.'
                '\n\nVeuillez agréer nos salutations distinguées.\n\n%s') % (number, amount, due_fr, pay, sign)
    else:
        subject = 'Dernière relance — facture %s' % number
        body = ('Madame, Monsieur,\n\nLa facture n° %s (reste dû : %s, échue le %s) reste impayée malgré nos relances.%s\n\nÀ défaut de règlement '
                'sous huit jours, nous nous verrons contraints d’envisager les suites appropriées, ce que nous souhaitons éviter.\n\n'
                'Veuillez agréer nos salutations distinguées.\n\n%s') % (number, amount, due_fr, pay, sign)
    return subject, body


def prepare(desk, today=None, refresh=True, http=None):
    """Synchronise les impayés (lecture), puis prépare les projets de relance du jour. Renvoie le nombre de nouveaux projets."""
    schema(desk)
    from .workstation import ensure_schema as invoices_schema
    invoices_schema(desk)
    s = settings(desk)
    if refresh:
        from .workstation import refresh_unpaid_invoices
        refresh_unpaid_invoices(desk, http=http)
    today = today or datetime.now(_tz(desk)).date()
    excluded = {r['invoice'] for r in desk.db.execute('SELECT invoice FROM relances_exclues5636')}
    sign = (desk.c.get('mail') or {}).get('from_name') or 'Le cabinet'
    created = 0
    rows = desk.db.execute('SELECT * FROM invoice_ninja_cache_v310 WHERE balance > 0').fetchall()
    for inv in rows:
        if inv['id'] in excluded or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', inv['due_date'] or ''):
            continue
        late = (today - date.fromisoformat(inv['due_date'])).days
        if late < int(s['grace_days']):
            continue
        # 5.6.37 (audit F13) : un projet ouvert (à valider, ou déposé dont l'envoi n'est pas confirmé) attend la décision de l'avocat ; le niveau
        # vient des seules relances dont l'ENVOI est confirmé (un projet écarté ou un brouillon jamais envoyé ne compte pas) ; l'intervalle part
        # du dernier envoi confirmé ou du dernier projet écarté
        if desk.db.execute("SELECT 1 FROM relances5636 WHERE invoice=? AND state IN ('a_valider','deposee')", (inv['id'],)).fetchone():
            continue
        sent = desk.db.execute("SELECT MAX(level) AS level, MAX(sent_at) AS at FROM relances5636 WHERE invoice=? AND state='envoyee'", (inv['id'],)).fetchone()
        dismissed = desk.db.execute("SELECT MAX(updated) AS at FROM relances5636 WHERE invoice=? AND state='ecartee'", (inv['id'],)).fetchone()
        level = int(sent['level'] or 0) + 1
        if level > int(s['max_level']):
            continue
        since = max([x for x in (sent['at'], dismissed['at']) if x] or [''])
        if since and (today - datetime.fromisoformat(since).astimezone(_tz(desk)).date()).days < int(s['interval_days']):
            continue
        currency = inv['currency'] if 'currency' in inv.keys() else ''
        amount = _money(inv['balance_exact'] if 'balance_exact' in inv.keys() and inv['balance_exact'] else inv['balance'], currency)
        matter = _matter_for(desk, inv['client_id'])
        subject, body = _text(level, inv['number'] or inv['id'], amount, inv['due_date'], inv['invoice_url'], sign)
        now = datetime.now(timezone.utc).isoformat()
        cur = desk.db.execute('INSERT OR IGNORE INTO relances5636(invoice,number,client_id,matter,level,recipient,subject,body,balance,currency,due_date,state,'
                        'created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (inv['id'], inv['number'], inv['client_id'], matter, level, _recipient(desk, matter, inv['client_id'], http), subject, body,
                         amount, currency, inv['due_date'], 'a_valider', now, now))
        created += cur.rowcount
    desk.db.commit()
    desk.audit('relances5636_preparees', {'count': created})
    return {'created': created, 'message': '%d projet(s) de relance préparé(s).' % created}


def job(desk, args):
    return prepare(desk)


def pending_count(desk):
    schema(desk)
    return desk.db.execute("SELECT COUNT(*) FROM relances5636 WHERE state='a_valider'").fetchone()[0]


def _get(desk, rid):
    schema(desk)
    row = desk.db.execute('SELECT * FROM relances5636 WHERE id=?', (int(rid) if str(rid).isdigit() else -1,)).fetchone()
    if not row:
        raise Stop('relance_absente')
    return row


def _fresh_invoice(desk, invoice, http=None):
    """Facture relue dans Invoice Ninja (lecture seule) : {'balance', 'paid', 'due_date'} ; None si l'API ne répond pas."""
    try:
        from .workstation import HTTP, read_secret
        cfg = desk.c.get('invoice_ninja', {})
        client = http or HTTP(str(cfg['base_url']).rstrip('/'), timeout=20)
        if http is None:
            client.headers.update({'X-API-TOKEN': read_secret(cfg['api_token_file']), 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
        raw = client.request('GET', client.base + '/api/v1/invoices/' + re.sub(r'[^A-Za-z0-9]', '', invoice), headers={'Accept': 'application/json'},
                             limit=500_000)
        data = (json.loads(raw) or {}).get('data') or {}
        balance = Decimal(str(data.get('balance')))
    except (Stop, KeyError, ValueError, TypeError, OSError, ImportError, InvalidOperation):
        return None
    paid = balance <= 0 or str(data.get('status_id') or '') in ('4', '5', '6') or bool(data.get('is_deleted'))
    return {'balance': balance, 'paid': paid, 'due_date': str(data.get('due_date') or '')}


def check_before_deposit(desk, row, http=None):
    """5.6.37 (audit F14) : la facture est relue avant tout dépôt. Réglée, annulée ou exclue : le projet est périmé. Solde modifié : le projet
    est actualisé pour relecture. Vérification impossible : réserve explicite (le projet n'est pas annoncé « à jour »)."""
    now = datetime.now(timezone.utc).isoformat()
    if desk.db.execute('SELECT 1 FROM relances_exclues5636 WHERE invoice=?', (row['invoice'],)).fetchone():
        return 'perimee', 'facture exclue des relances'
    cached = desk.db.execute('SELECT * FROM invoice_ninja_cache_v310 WHERE id=?', (row['invoice'],)).fetchone()
    if cached is not None and float(cached['balance'] or 0) <= 0:
        return 'perimee', 'solde nul dans Invoice Ninja (paiement enregistré)'
    fresh = _fresh_invoice(desk, row['invoice'], http)
    if fresh is None:
        desk.db.execute('UPDATE relances5636 SET checked_at=?, check_note=? WHERE id=?', (now, 'solde_non_reverifie', row['id']))
        return 'reserve', 'Invoice Ninja n’a pas répondu : le solde n’a pas pu être revérifié'
    if fresh['paid']:
        if cached is not None:
            desk.db.execute('UPDATE invoice_ninja_cache_v310 SET balance=0 WHERE id=?', (row['invoice'],))
        return 'perimee', 'facture réglée ou annulée dans Invoice Ninja'
    currency = row['currency'] or ''
    amount = _money(str(fresh['balance']), currency)
    desk.db.execute('UPDATE relances5636 SET checked_at=?, check_note=? WHERE id=?', (now, 'solde_reverifie', row['id']))
    if amount != row['balance']:
        return 'modifiee', amount
    return 'ok', amount


def deposit(desk, rid, data, owner='cabinet', http=None):
    row = _get(desk, rid)
    if row['state'] != 'a_valider':
        raise Stop('relance_absente')
    status, info = check_before_deposit(desk, row, http)
    if status == 'perimee':
        desk.db.execute("UPDATE relances5636 SET state='perimee', check_note=?, updated=? WHERE id=?", (info, datetime.now(timezone.utc).isoformat(), row['id']))
        desk.db.commit()
        desk.audit('relances5636_perimee', {'invoice': row['invoice'], 'motif': info})
        from .erreurs5634 import Qualified
        raise Qualified('relance_perimee', precision='Projet retiré : %s. Aucun brouillon déposé.' % info)
    if status == 'modifiee':
        edited = str(data.get('body') or row['body']) != row['body']
        if not edited:   # texte non corrigé par l'avocat : actualisé avec le nouveau solde ; une correction n'est jamais écrasée
            sign = (desk.c.get('mail') or {}).get('from_name') or 'Le cabinet'
            cached = desk.db.execute('SELECT invoice_url FROM invoice_ninja_cache_v310 WHERE id=?', (row['invoice'],)).fetchone()
            subject, body = _text(row['level'], row['number'] or row['invoice'], info, row['due_date'], cached['invoice_url'] if cached else '', sign)
            desk.db.execute('UPDATE relances5636 SET balance=?, subject=?, body=?, updated=? WHERE id=?',
                            (info, subject, body, datetime.now(timezone.utc).isoformat(), row['id']))
        else:
            desk.db.execute('UPDATE relances5636 SET balance=?, updated=? WHERE id=?', (info, datetime.now(timezone.utc).isoformat(), row['id']))
        desk.db.commit()
        from .erreurs5634 import Qualified
        raise Qualified('relance_solde_modifie', precision='Nouveau solde : %s. %s' % (
            info, 'Votre texte a été conservé : corrigez le montant puis déposez.' if edited else 'Le projet a été actualisé : relisez-le puis déposez.'))
    to = str(data.get('recipient') or row['recipient'] or '').strip().lower()
    subject = re.sub(r'[\r\n]+', ' ', str(data.get('subject') or row['subject'])).strip()[:200]
    body = str(data.get('body') or row['body'])[:8000]
    from html import escape
    from .webmail5628 import save
    out = save(desk, {'mode': 'nouveau', 'to': to, 'subject': subject,
                      'html': ''.join('<p>%s</p>' % escape(p).replace('\n', '<br>') for p in body.split('\n\n'))}, owner)
    desk.db.execute("UPDATE relances5636 SET state='deposee', recipient=?, subject=?, body=?, draft_uid=?, updated=? WHERE id=?",
                    (to, subject, body, str(out.get('uid') or ''), datetime.now(timezone.utc).isoformat(), row['id']))
    desk.db.commit()
    desk.audit('relances5636_deposee', {'invoice': row['invoice'], 'level': row['level'], 'owner': owner, 'controle': status})
    return {'id': row['id'], 'message': 'Relance déposée dans Brouillons' + (' pour ' + to if to else ' (destinataire à compléter)') + ' : rien n’est envoyé.'
            + (' Attention : %s.' % info if status == 'reserve' else ' Solde revérifié : %s.' % info)}


def confirm_sent(desk, rid, owner='cabinet', sent=True):
    """5.6.37 (audit F13) : l'avocat confirme l'envoi réel (ou non) d'une relance déposée ; seul un envoi confirmé fait monter le niveau."""
    row = _get(desk, rid)
    if row['state'] != 'deposee':
        raise Stop('relance_absente')
    now = datetime.now(timezone.utc).isoformat()
    if sent:
        desk.db.execute("UPDATE relances5636 SET state='envoyee', sent_at=?, updated=? WHERE id=?", (now, now, row['id']))
    else:
        desk.db.execute("UPDATE relances5636 SET state='ecartee', updated=? WHERE id=?", (now, row['id']))
    desk.db.commit()
    desk.audit('relances5636_envoi_confirme' if sent else 'relances5636_non_envoyee', {'invoice': row['invoice'], 'level': row['level'], 'owner': owner})
    return {'id': row['id'], 'message': 'Envoi confirmé.' if sent else 'Relance marquée non envoyée.'}


def dismiss(desk, rid, owner='cabinet', exclude=False):
    row = _get(desk, rid)
    if row['state'] not in ('a_valider', 'deposee'):
        raise Stop('relance_absente')
    desk.db.execute("UPDATE relances5636 SET state='ecartee', updated=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), row['id']))
    if exclude:
        desk.db.execute('INSERT OR REPLACE INTO relances_exclues5636 VALUES(?,?,?)', (row['invoice'], 'avocat', datetime.now(timezone.utc).isoformat()))
    desk.db.commit()
    desk.audit('relances5636_ecartee', {'invoice': row['invoice'], 'exclude': exclude, 'owner': owner})
    return {'id': row['id'], 'message': 'Facture exclue des relances.' if exclude else 'Projet de relance écarté.'}


def page_html(desk, prefix, args):
    schema(desk)
    s = settings(desk)
    labels = {}
    try:
        labels = {m['id']: matter_display(m) for m in load_matters(desk.c)}
    except Stop:
        pass
    html = ('<h1>Relances d’impayés</h1><p class="ax-muted">Chaque matin (à partir de %dh), AxiorHub lit les factures impayées d’Invoice Ninja et '
            'prépare un projet de relance pour chaque facture échue. Vous relisez, corrigez, puis déposez la relance dans Brouillons : '
            'rien n’est envoyé ni modifié dans Invoice Ninja.</p>') % int(s['hour'])
    if not configured(desk):
        html += ('<p class="ax-card">Invoice Ninja n’est pas encore relié : <a href="%s">Paramètres › Connexions</a>.</p>'
                 % e(prefix + '/parametres?rubrique=connexions', quote=True))
    html += ('<details class="ax-card"><summary>Réglages</summary><form class="m5-form" data-api="m5636/relances/reglages" data-reload="1">'
             '<label class="e5636-switch"><input type="checkbox" name="enabled"%s> Préparer les relances chaque matin</label>'
             '<label>Heure (5 à 12)<input type="number" name="hour" min="5" max="12" value="%d"></label>'
             '<label>Délai de grâce après échéance (jours)<input type="number" name="grace_days" min="0" max="60" value="%d"></label>'
             '<label>Intervalle entre deux relances (jours)<input type="number" name="interval_days" min="3" max="60" value="%d"></label>'
             '<label>Niveau maximal<select name="max_level">%s</select></label><button class="ax-btn" type="submit">Enregistrer</button></form>'
             '<form class="m5-form m5-inline" data-api="m5636/relances/preparer" data-reload="1"><button class="ax-btn ghost" type="submit">'
             'Préparer maintenant</button></form></details>') % (
        ' checked' if s['enabled'] else '', int(s['hour']), int(s['grace_days']), int(s['interval_days']),
        ''.join('<option value="%d"%s>%d — %s</option>' % (k, ' selected' if k == int(s['max_level']) else '', k, e(v)) for k, v in LEVELS.items()))
    rows = desk.db.execute("SELECT * FROM relances5636 WHERE state='a_valider' ORDER BY due_date, id").fetchall()
    if not rows:
        html += '<p class="ax-card ax-muted">Aucune relance à valider.</p>'
    for r in rows:
        html += ('<article class="ax-card r5636"><h2>Facture %s — %s <small>(%s)</small></h2><p class="ax-muted">Échue le %s · %s</p>'
                 '<form class="m5-form" data-api="m5636/relances/deposer" data-reload="1"><input type="hidden" name="id" value="%d">'
                 '<label>Destinataire<input type="email" name="recipient" value="%s" placeholder="adresse du client"></label>'
                 '<label>Objet<input name="subject" value="%s"></label><label>Message<textarea name="body" rows="10">%s</textarea></label>'
                 '<div class="ax-actions"><button class="ax-btn" type="submit">Déposer dans Brouillons</button>'
                 '<button class="ax-btn ghost" type="submit" data-extra="ecarter=1">Écarter</button>'
                 '<button class="ax-btn ghost" type="submit" data-extra="exclure=1" data-confirm="Ne plus relancer cette facture ?">Ne plus relancer</button>'
                 '</div></form></article>') % (
            e(r['number'] or r['invoice']), e(r['balance']), e(LEVELS.get(r['level'], '')), e(r['due_date']),
            e(labels.get(r['matter'], 'dossier non relié') if r['matter'] else 'dossier non relié'), r['id'], e(r['recipient'], quote=True),
            e(r['subject'], quote=True), e(r['body']))
    sent = desk.db.execute("SELECT * FROM relances5636 WHERE state='deposee' ORDER BY updated DESC").fetchall()
    if sent:
        html += '<section class="ax-card"><h2>Relances déposées : envoi à confirmer</h2><p class="ax-muted">Le niveau suivant (« Malgré notre précédent rappel… ») ' \
                'n’est proposé qu’après un envoi confirmé.</p><ul class="o5636-list">%s</ul></section>' % ''.join(
                    ('<li><strong>Facture %s</strong> — %s · déposée le %s<form class="m5-form m5-inline" data-api="m5636/relances/envoi" data-reload="1">'
                     '<input type="hidden" name="id" value="%d"><button class="ax-btn ghost" type="submit">Marquer comme envoyée</button>'
                     '<button class="ax-btn ghost" type="submit" data-extra="non_envoyee=1">Non envoyée</button></form></li>') % (
                        e(r['number'] or r['invoice']), e(LEVELS.get(r['level'], '')), e(r['updated'][:10]), r['id']) for r in sent)
    return html


def api_post(desk, route, data, owner):
    if route == 'relances/envoi':   # 5.6.37 (audit F13)
        return confirm_sent(desk, data.get('id'), owner, sent=not data.get('non_envoyee'))
    if route == 'relances/reglages':
        return save_settings(desk, data, owner)
    if route == 'relances/preparer':
        if not configured(desk):
            raise Stop('invoice_ninja_non_configure')
        return prepare(desk)
    if route == 'relances/deposer':
        if data.get('exclure'):
            return dismiss(desk, data.get('id'), owner, exclude=True)
        if data.get('ecarter'):
            return dismiss(desk, data.get('id'), owner)
        return deposit(desk, data.get('id'), data, owner)
    return None


def api_get(desk, route, args):
    if route == 'relances':
        schema(desk)
        return {'pending': pending_count(desk), 'settings': settings(desk)}
    return None

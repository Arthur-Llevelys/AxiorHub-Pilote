"""5.6.14 (N01–N09, C14–C16) : Invoice Ninja — adaptateur commun, clients, projets, devis, journal des effets et synchronisation.

- InvoiceNinjaClient : espace /api/v1, X-API-TOKEN, société vérifiée, pagination complète, erreurs traduites (401/403 accès, 422
  champs à corriger, 429 reprise bornée), montants Decimal, identifiants opaques, liste d'opérations permises (aucun envoi, paiement
  ni conversion sans mandat explicite).
- Journal des opérations : chaque écriture est enregistrée avant émission (clé, société, type, payload, empreinte) ; après timeout,
  rapprochement par référence avant tout nouvel essai. Correspondance locale ↔ distante par société et type d'entité.
- Clients et contacts (recherche avant création, homonyme = décision), projets rattachés au dossier, devis en brouillon (jamais
  envoyés ni convertis), synchronisation périodique des statuts et paiements (Invoice Ninja fait autorité sur numéros et paiements).
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import re
import secrets
import sqlite3
from urllib.parse import quote, urlencode

from .common import HTTP, Stop, digest, fold, load_matters, matter_display, read_secret

SCHEMA = '''CREATE TABLE IF NOT EXISTS invoice_ninja_map5614(
 company TEXT NOT NULL, entity TEXT NOT NULL, local_id TEXT NOT NULL, remote_id TEXT NOT NULL, content_hash TEXT NOT NULL DEFAULT '',
 remote_version TEXT NOT NULL DEFAULT '', synced TEXT NOT NULL, PRIMARY KEY(company,entity,local_id));
CREATE INDEX IF NOT EXISTS invoice_ninja_map5614_remote ON invoice_ninja_map5614(company,entity,remote_id);
CREATE TABLE IF NOT EXISTS invoice_ninja_ops5614(
 id TEXT PRIMARY KEY, company TEXT NOT NULL, entity TEXT NOT NULL, local_id TEXT NOT NULL, action TEXT NOT NULL, state TEXT NOT NULL,
 payload_hash TEXT NOT NULL, payload TEXT NOT NULL, remote_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}',
 created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoice_ninja_entries5614(
 company TEXT NOT NULL, entry_id TEXT NOT NULL, state TEXT NOT NULL, write_key TEXT NOT NULL, remote_task TEXT NOT NULL DEFAULT '',
 remote_invoice TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, PRIMARY KEY(company,entry_id));
CREATE TABLE IF NOT EXISTS invoice_ninja_sync5614(
 company TEXT NOT NULL, entity TEXT NOT NULL, remote_id TEXT NOT NULL, number TEXT NOT NULL DEFAULT '', status_id TEXT NOT NULL DEFAULT '',
 amount TEXT NOT NULL DEFAULT '', balance TEXT NOT NULL DEFAULT '', paid_to_date TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '',
 data TEXT NOT NULL DEFAULT '{}', synced TEXT NOT NULL, PRIMARY KEY(company,entity,remote_id));'''
ALLOWED_OPERATIONS = {('POST', '/api/v1/clients'), ('PUT', '/api/v1/clients/'), ('POST', '/api/v1/projects'), ('PUT', '/api/v1/projects/'),
                      ('POST', '/api/v1/quotes'), ('POST', '/api/v1/invoices'), ('POST', '/api/v1/tasks'), ('PUT', '/api/v1/tasks/')}
FORBIDDEN_FIELDS = ('send_email', 'mark_sent', 'paid', 'auto_bill', 'action', 'email', 'send')
ENTITY_ENDPOINTS = {'client': '/api/v1/clients', 'project': '/api/v1/projects', 'quote': '/api/v1/quotes', 'invoice': '/api/v1/invoices', 'task': '/api/v1/tasks'}
QUOTE_STATUS = {'1': 'brouillon', '2': 'envoyé', '3': 'approuvé', '4': 'converti', '5': 'expiré', '-1': 'invalide'}
INVOICE_STATUS = {'1': 'brouillon', '2': 'envoyée', '3': 'partiellement payée', '4': 'payée', '5': 'annulée', '6': 'inversée'}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    desk.db.commit()


def _cfg(desk):
    cfg = desk.c.get('invoice_ninja', {})
    if not cfg.get('enabled') or not cfg.get('base_url') or not cfg.get('api_token_file'):
        raise Stop('invoice_ninja_non_configure')
    return cfg


def company(desk):
    return str(_cfg(desk).get('company_id') or 'default')


def money(value):
    """Montant en Decimal à deux décimales, jamais en flottant binaire."""
    try:
        return Decimal(str(value if value not in (None, '') else '0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except ArithmeticError:
        raise Stop('montant_invalide') from None


class NinjaError(Stop):
    def __init__(self, code, status=0, fields=None):
        super().__init__(code)
        self.status, self.fields = status, fields or {}


class InvoiceNinjaClient:
    """N06 : un seul adaptateur pour les lectures et les écritures, verrouillé sur la version et la société."""

    def __init__(self, desk, http=None, write=False):
        self.desk = desk
        self.cfg = _cfg(desk)
        ensure_schema(desk)
        if write and not self.cfg.get('write_enabled'):
            raise Stop('invoice_ninja_ecriture_desactivee')
        self.write = bool(write)
        self.company = company(desk)
        self.max_pages = max(1, min(int(self.cfg.get('max_pages', 30) or 30), 200))
        self.http = http or HTTP(str(self.cfg['base_url']).rstrip('/'), timeout=min(60, max(5, int(self.cfg.get('timeout_seconds', 30)))))
        if http is None:
            self.http.headers.update({'X-API-TOKEN': read_secret(self.cfg['api_token_file']), 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})

    # ---- transport
    def call(self, method, path, data=None):
        if method in ('POST', 'PUT', 'DELETE'):
            if not self.write:
                raise Stop('invoice_ninja_ecriture_desactivee')
            base = path.split('?', 1)[0]
            if not any(base == p or (p.endswith('/') and base.startswith(p)) for m, p in ALLOWED_OPERATIONS if m == method):
                raise Stop('operation_invoice_ninja_non_permise')
            if isinstance(data, dict) and any(k in data for k in FORBIDDEN_FIELDS):
                raise Stop('parametre_invoice_ninja_interdit')
        try:
            return self.http.json(method, path, data)
        except Stop as ex:
            code = str(ex)
            m = re.search(r'http_(\d{3})', code)
            status = int(m.group(1)) if m else 0
            if status in (401, 403):
                raise NinjaError('invoice_ninja_acces_refuse_%d' % status, status) from None
            if status == 422:
                raise NinjaError('invoice_ninja_champs_invalides', 422, getattr(ex, 'fields', {})) from None
            if status == 429:
                raise NinjaError('invoice_ninja_quota_atteint_reprise_differee', 429) from None
            if status == 404:
                raise NinjaError('invoice_ninja_objet_absent', 404) from None
            raise

    def get_one(self, entity, remote_id):
        return self._data(self.call('GET', ENTITY_ENDPOINTS[entity] + '/' + quote(str(remote_id), safe='')))

    def list_all(self, entity, params=None):
        """Pagination complète (meta.pagination) : aucun objet omis en deçà du plafond de pages."""
        out, page = [], 1
        while page <= self.max_pages:
            q = {**(params or {}), 'per_page': 100, 'page': page}
            result = self.call('GET', ENTITY_ENDPOINTS[entity] + '?' + urlencode(q))
            rows = result.get('data') if isinstance(result, dict) else None
            if not isinstance(rows, list):
                raise Stop('invoice_ninja_reponse_invalide')
            out += [r for r in rows if isinstance(r, dict)]
            meta = (result.get('meta') or {}).get('pagination') or {}
            total_pages = int(meta.get('total_pages') or 1)
            if page >= total_pages:
                break
            page += 1
        else:
            raise Stop('invoice_ninja_pagination_plafond_atteint')
        return out

    def info(self):
        """Version et société : le contrat est verrouillé sur la version serveur lue, jamais supposée."""
        try:
            version = self.call('GET', '/api/v1/ping')
        except Stop:
            version = {}
        return {'company': self.company, 'ping': version if isinstance(version, dict) else {}, 'write': self.write}

    @staticmethod
    def _data(result):
        data = result.get('data') if isinstance(result, dict) else None
        if not isinstance(data, dict) or not data.get('id'):
            raise Stop('invoice_ninja_reponse_invalide')
        return data

    def create(self, entity, body, local_id, action='create'):
        """Écriture journalisée : préparée → en cours → confirmée ; après réponse incertaine, rapprochement avant tout nouvel essai."""
        ensure_schema(self.desk)
        key = digest('op5614|%s|%s|%s|%s' % (self.company, entity, local_id, action))[:32]
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
        phash = digest(payload)
        now = self.desk.now()
        owner = secrets.token_hex(8)
        # 5.6.24 (F17) : l'opération est réclamée dans une transaction exclusive AVANT toute lecture de décision (INSERT simple sur la
        # clé primaire) ; deux connexions simultanées ne peuvent pas lire toutes deux l'absence de l'opération puis émettre deux POST.
        try:
            self.desk.db.execute('BEGIN IMMEDIATE')
            row = self.desk.db.execute('SELECT * FROM invoice_ninja_ops5614 WHERE id=?', (key,)).fetchone()
            if row is None:
                self.desk.db.execute('INSERT INTO invoice_ninja_ops5614 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                     (key, self.company, entity, str(local_id), action, 'en_cours', phash, payload, '', json.dumps({'owner': owner, 'generation': 1}), now, now))
            self.desk.db.commit()
        except sqlite3.IntegrityError:
            self.desk.db.rollback()
            raise Stop('operation_deja_en_cours') from None
        if row is not None:
            if row['state'] == 'confirme':
                return self.get_one(entity, row['remote_id']), key, 'deja_confirme'
            if row['payload_hash'] != phash:
                raise Stop('operation_reutilisee_avec_autres_donnees')
            found = self.reconcile(entity, local_id, body)   # hors transaction : appel réseau
            if found:
                self.desk.db.execute("UPDATE invoice_ninja_ops5614 SET state='confirme',remote_id=?,updated=? WHERE id=? AND state!='confirme'", (str(found['id']), now, key))
                self.desk.db.commit()
                self._map(entity, local_id, str(found['id']), phash)
                return found, key, 'rapproche'
            if row['state'] in ('en_cours', 'incertain'):
                raise Stop('operation_incertaine_rapprochement_impossible')
            # état « refuse » : nouvelle tentative réclamée conditionnellement (propriétaire + génération)
            detail = json.loads(row['detail'] or '{}') if isinstance(row['detail'], str) else {}
            generation = int(detail.get('generation') or 1) + 1
            try:
                self.desk.db.execute('BEGIN IMMEDIATE')
                claimed = self.desk.db.execute("UPDATE invoice_ninja_ops5614 SET state='en_cours',payload_hash=?,payload=?,detail=?,updated=? WHERE id=? AND state='refuse'",
                                               (phash, payload, json.dumps({'owner': owner, 'generation': generation}), now, key)).rowcount
                self.desk.db.commit()
            except sqlite3.OperationalError:
                self.desk.db.rollback()
                raise Stop('operation_deja_en_cours') from None
            if claimed != 1:
                raise Stop('operation_deja_en_cours')
        try:
            created = self._data(self.call('POST', ENTITY_ENDPOINTS[entity], body))
        except NinjaError as ex:
            self.desk.db.execute("UPDATE invoice_ninja_ops5614 SET state=?,detail=?,updated=? WHERE id=?",
                                 ('refuse' if ex.status in (401, 403, 422) else 'incertain', json.dumps({'error': str(ex), 'status': ex.status, 'fields': ex.fields}), now, key))
            self.desk.db.commit()
            raise
        except Exception as ex:
            self.desk.db.execute("UPDATE invoice_ninja_ops5614 SET state='incertain',detail=?,updated=? WHERE id=?", (json.dumps({'error': str(ex)[:200]}), now, key))
            self.desk.db.commit()
            raise Stop('invoice_ninja_ecriture_incertaine_rapprocher') from None
        remote_id = str(created['id'])
        self.desk.db.execute("UPDATE invoice_ninja_ops5614 SET state='confirme',remote_id=?,updated=? WHERE id=?", (remote_id, self.desk.now(), key))
        self.desk.db.commit()
        self._map(entity, local_id, remote_id, phash)
        return created, key, 'cree'

    def reconcile(self, entity, local_id, body):
        """Retrouve une entité créée sans réponse : correspondance connue, puis référence de corrélation dans un champ réellement supporté."""
        m = self.desk.db.execute('SELECT remote_id FROM invoice_ninja_map5614 WHERE company=? AND entity=? AND local_id=?', (self.company, entity, str(local_id))).fetchone()
        if m:
            try:
                return self.get_one(entity, m['remote_id'])
            except Stop:
                return None
        marker = 'AxiorHub:' + str(local_id)[:40]
        field = {'client': 'private_notes', 'project': 'private_notes', 'quote': 'private_notes', 'invoice': 'private_notes', 'task': 'description'}[entity]
        if marker not in str(body.get(field, '')):
            return None
        try:
            rows = self.list_all(entity, {'filter': marker} if entity in ('client', 'project') else {})
        except Stop:
            return None
        hits = [r for r in rows if marker in str(r.get(field, ''))]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise Stop('operation_conflit_plusieurs_entites_distantes')
        return None

    def _map(self, entity, local_id, remote_id, content_hash='', version=''):
        self.desk.db.execute('INSERT OR REPLACE INTO invoice_ninja_map5614 VALUES(?,?,?,?,?,?,?)',
                             (self.company, entity, str(local_id), str(remote_id), content_hash, str(version), self.desk.now()))
        self.desk.db.commit()

    def mapped(self, entity, local_id):
        ensure_schema(self.desk)
        r = self.desk.db.execute('SELECT remote_id FROM invoice_ninja_map5614 WHERE company=? AND entity=? AND local_id=?', (self.company, entity, str(local_id))).fetchone()
        return str(r['remote_id']) if r else ''


# ---------------------------------------------------------------------------------------- clients (N01)
def _norm_name(value):
    return re.sub(r'[^a-z0-9]+', ' ', fold(str(value or ''))).strip()


def search_clients(desk, name='', email='', siren='', http=None):
    """Correspondances existantes par identité et coordonnées ; un nom seul ne décide jamais d'une fusion."""
    client = InvoiceNinjaClient(desk, http)
    rows = client.list_all('client', {'filter': name} if name else {})
    wanted = _norm_name(name)
    out = []
    for r in rows:
        score, reasons = 0, []
        if wanted and _norm_name(r.get('name')) == wanted:
            score += 2
            reasons.append('nom identique')
        elif wanted and wanted in _norm_name(r.get('name')):
            score += 1
            reasons.append('nom proche')
        contacts = [c for c in (r.get('contacts') or []) if isinstance(c, dict)]
        if email and any(fold(str(c.get('email', ''))) == fold(email) for c in contacts):
            score += 3
            reasons.append('courriel identique')
        if siren and str(r.get('id_number') or r.get('vat_number') or '').replace(' ', '') == str(siren).replace(' ', ''):
            score += 3
            reasons.append('identifiant identique')
        if score:
            out.append({'id': str(r['id']), 'name': str(r.get('name', '')), 'score': score, 'reasons': reasons, 'contacts': [str(c.get('email', '')) for c in contacts][:5],
                        'address': ', '.join(str(r.get(k) or '') for k in ('address1', 'postal_code', 'city') if r.get(k))})
    out.sort(key=lambda x: -x['score'])
    return out


def ensure_client(desk, matter, data, confirm=False, http=None):
    """Client facturé du dossier : réutilisé (correspondance ou choix explicite) ou créé avec les données validées ; rien n'est inventé."""
    ensure_schema(desk)
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(matter)), None)
    if not m:
        raise Stop('dossier_absent')
    client = InvoiceNinjaClient(desk, http, write=True)
    chosen = str(data.get('client_id') or '')
    if chosen:
        remote = client.get_one('client', chosen)
        _link(desk, m['id'], str(remote['id']))
        client._map('client', 'matter:' + m['id'], str(remote['id']))
        return {'client_id': str(remote['id']), 'name': remote.get('name', ''), 'created': False, 'message': 'Client existant rattaché au dossier.'}
    name = str(data.get('name') or m.get('client_name') or '').strip()
    if not name:
        raise Stop('nom_client_requis')
    matches = search_clients(desk, name, str(data.get('email') or ''), str(data.get('siren') or ''), http=client.http)
    strong = [x for x in matches if x['score'] >= 3]
    if matches and not confirm:
        raise Stop('homonyme_client_choisir:' + json.dumps([{'id': x['id'], 'name': x['name'], 'reasons': x['reasons']} for x in matches[:6]], ensure_ascii=False))
    if strong and str(data.get('create_anyway') or '') != 'yes':
        raise Stop('client_existant_rattacher:' + strong[0]['id'])
    contacts = [c for c in (data.get('contacts') or []) if isinstance(c, dict) and c.get('email')]
    body = {'name': name, 'private_notes': 'AxiorHub:matter:' + m['id'], 'contacts': [{'first_name': str(c.get('first_name', ''))[:60], 'last_name': str(c.get('last_name', ''))[:60], 'email': str(c['email'])[:120]} for c in contacts][:10]}
    for k in ('address1', 'address2', 'postal_code', 'city', 'country_id', 'vat_number', 'id_number', 'phone', 'website'):
        if data.get(k):
            body[k] = str(data[k])[:120]
    if data.get('currency_id'):
        body['settings'] = {'currency_id': str(data['currency_id'])}
    created, key, how = client.create('client', body, 'matter:' + m['id'])
    checked = client.get_one('client', str(created['id']))
    issues = []
    if str(checked.get('name', '')) != name:
        issues.append('nom')
    if len([c for c in (checked.get('contacts') or []) if isinstance(c, dict)]) < len(body['contacts']):
        issues.append('contacts')
    _link(desk, m['id'], str(checked['id']))
    desk.audit('invoice_ninja_client', {'matter': m['id'], 'remote_id': str(checked['id']), 'how': how, 'issues': issues})
    return {'client_id': str(checked['id']), 'name': checked.get('name', ''), 'created': how == 'cree', 'issues': issues,
            'message': ('Client créé et relu.' if not issues else 'Client créé, écarts à vérifier : ' + ', '.join(issues)) if how == 'cree' else 'Client retrouvé par rapprochement.'}


def update_client(desk, remote_id, changes, http=None):
    """Mise à jour partielle : les contacts existants sont relus puis renvoyés intégralement (aucune suppression implicite)."""
    client = InvoiceNinjaClient(desk, http, write=True)
    current = client.get_one('client', remote_id)
    body = {k: str(v)[:120] for k, v in changes.items() if k in ('name', 'address1', 'address2', 'postal_code', 'city', 'phone', 'website', 'vat_number', 'id_number') and v}
    body['contacts'] = [c for c in (current.get('contacts') or []) if isinstance(c, dict)]
    for c in changes.get('contacts') or []:
        if isinstance(c, dict) and c.get('email') and not any(fold(str(x.get('email', ''))) == fold(c['email']) for x in body['contacts']):
            body['contacts'].append({'first_name': str(c.get('first_name', ''))[:60], 'last_name': str(c.get('last_name', ''))[:60], 'email': str(c['email'])[:120]})
    updated = client._data(client.call('PUT', '/api/v1/clients/' + quote(str(remote_id), safe=''), body))
    kept = len([c for c in (updated.get('contacts') or []) if isinstance(c, dict)])
    if kept < len(body['contacts']):
        raise Stop('contacts_perdus_a_la_mise_a_jour')
    return {'client_id': str(updated['id']), 'contacts': kept}


def _link(desk, matter, client_id):
    from .workstation import link_invoice_client
    link_invoice_client(desk, matter, client_id)


# ---------------------------------------------------------------------------------------- projets (N02)
def ensure_project(desk, matter, data=None, http=None):
    """Projet Invoice Ninja rattaché au dossier (un par dossier) : lié s'il existe, créé sinon ; reprise sans doublon."""
    ensure_schema(desk)
    data = data or {}
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(matter)), None)
    if not m:
        raise Stop('dossier_absent')
    client = InvoiceNinjaClient(desk, http, write=True)
    link = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (m['id'],)).fetchone()
    if not link:
        raise Stop('client_invoice_ninja_non_lie')
    existing = client.mapped('project', 'matter:' + m['id'])
    if existing or data.get('project_id'):
        remote = client.get_one('project', existing or str(data['project_id']))
        if str(remote.get('client_id')) != str(link['client_id']):
            raise Stop('projet_autre_client')
        client._map('project', 'matter:' + m['id'], str(remote['id']))
        return {'project_id': str(remote['id']), 'name': remote.get('name', ''), 'created': False, 'message': 'Projet existant rattaché.'}
    body = {'client_id': str(link['client_id']), 'name': str(data.get('name') or matter_display(m))[:200], 'private_notes': 'AxiorHub:matter:' + m['id'],
            'public_notes': ''}
    if data.get('task_rate'):
        body['task_rate'] = float(money(data['task_rate']))
    if data.get('budgeted_hours'):
        body['budgeted_hours'] = float(money(data['budgeted_hours']))
    created, key, how = client.create('project', body, 'matter:' + m['id'])
    checked = client.get_one('project', str(created['id']))
    issues = []
    if str(checked.get('client_id')) != str(link['client_id']):
        issues.append('client')
    if str(checked.get('name', '')) != body['name']:
        issues.append('nom')
    desk.audit('invoice_ninja_projet', {'matter': m['id'], 'remote_id': str(checked['id']), 'how': how, 'issues': issues})
    if issues:
        raise Stop('projet_cree_non_conforme_' + '_'.join(issues))
    return {'project_id': str(checked['id']), 'name': checked.get('name', ''), 'created': how == 'cree', 'message': 'Projet créé et relu.' if how == 'cree' else 'Projet retrouvé.'}


# ---------------------------------------------------------------------------------------- devis (N03)
def _lines(items):
    out, total = [], Decimal('0')
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not str(it.get('label') or '').strip():
            raise Stop('ligne_devis_invalide')
        qty = money(it.get('quantity', 1))
        cost = money(it.get('cost', 0))
        if qty <= 0 or cost < 0:
            raise Stop('ligne_devis_invalide')
        line = {'product_key': str(it.get('product_key') or 'Honoraires')[:60], 'notes': str(it['label'])[:500], 'quantity': float(qty), 'cost': float(cost)}
        if it.get('tax_name') and it.get('tax_rate') not in (None, ''):
            line['tax_name1'] = str(it['tax_name'])[:30]
            line['tax_rate1'] = float(money(it['tax_rate']))
        out.append(line)
        total += qty * cost
    return out, total.quantize(Decimal('0.01'))


def office_today(desk):
    """Date du jour dans le fuseau du cabinet (réglage ``calendar.timezone``, Europe/Paris par défaut)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(desk.c.get('calendar', {}).get('timezone', 'Europe/Paris'))).date()
    except Exception:
        return date.today()


def preview_quote(desk, matter, items, terms='', validity_days=30, note=''):
    """Aperçu modifiable avant création : lignes, HT, taxes déclarées, TTC calculé en Decimal (aucun régime de taxe supposé).
    5.6.24 (F16) : la date d'émission et la date de validité (fuseau du cabinet) font partie de l'aperçu et du devis créé."""
    lines, total_ht = _lines(items)
    taxes = Decimal('0')
    for l in lines:
        if 'tax_rate1' in l:
            taxes += (Decimal(str(l['quantity'])) * Decimal(str(l['cost'])) * Decimal(str(l['tax_rate1'])) / 100).quantize(Decimal('0.01'))
    days = max(1, min(int(validity_days or 30), 365))
    today = office_today(desk)
    return {'matter': str(matter), 'lines': lines, 'total_ht': str(total_ht), 'taxes': str(taxes), 'total_ttc': str(total_ht + taxes), 'terms': str(terms or '')[:2000],
            'validity_days': days, 'date': today.isoformat(), 'due_date': (today + timedelta(days=days)).isoformat(), 'note': str(note or '')[:1000], 'missing': []}


def draft_quote(desk, matter, items, confirm=False, terms='', validity_days=30, note='', http=None, request_key=''):
    """Devis en brouillon (jamais envoyé, approuvé ni converti), relu après création, relié au client, au projet et au dossier.
    5.6.24 (F16) : l'identifiant local dépend de la demande (``request_key``) ou, à défaut, de toute la charge (lignes, conditions, note,
    validité, date) : des conditions différentes font un autre devis ; une même clé avec d'autres données est un conflit explicite."""
    ensure_schema(desk)
    if not (confirm is True or str(confirm or '').lower() in ('yes', 'oui', '1', 'true')):
        raise Stop('confirmation_requise')
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(matter)), None)
    if not m:
        raise Stop('dossier_absent')
    client = InvoiceNinjaClient(desk, http, write=True)
    link = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (m['id'],)).fetchone()
    if not link:
        raise Stop('client_invoice_ninja_non_lie')
    prev = preview_quote(desk, matter, items, terms, validity_days, note)
    identity = str(request_key or '').strip() or json.dumps({k: prev[k] for k in ('lines', 'terms', 'note', 'validity_days', 'date')}, sort_keys=True)
    local_id = 'quote:' + m['id'] + ':' + digest(identity)[:12]
    body = {'client_id': str(link['client_id']), 'date': prev['date'], 'line_items': prev['lines'], 'public_notes': prev['note'], 'terms': prev['terms'],
            'private_notes': 'AxiorHub:' + local_id, 'due_date': prev['due_date'], 'expected_amount': prev['total_ttc']}
    project = client.mapped('project', 'matter:' + m['id'])
    if project:
        body['project_id'] = project
    sent = {k: v for k, v in body.items() if k != 'expected_amount'}
    created, key, how = client.create('quote', sent, local_id)
    checked = client.get_one('quote', str(created['id']))
    issues = check_lines(checked, body, str(link['client_id']), prev['total_ht'])
    if checked.get('status_id') is None:
        issues.append('statut absent')   # 5.6.24 (F15) : un statut absent n'est pas un brouillon supposé
    elif str(checked.get('status_id')) != '1':
        issues.append('statut non brouillon')
    desk.audit('invoice_ninja_devis_brouillon', {'matter': m['id'], 'remote_id': str(checked['id']), 'how': how, 'issues': issues, 'sent': False})
    return {'quote_id': str(checked['id']), 'number': str(checked.get('number') or ''), 'total_ht': prev['total_ht'], 'total_ttc': prev['total_ttc'], 'draft': not issues,
            'issues': issues, 'verified': not issues, 'sent': False, 'converted': False,
            'message': ('Devis %s créé en brouillon et relu. Ni envoyé, ni approuvé, ni converti.' % (checked.get('number') or checked['id'])) if not issues
                       else 'Devis créé, conformité non vérifiée : ' + ' ; '.join(issues)}


def expected_total(body):
    """TTC attendu d'après les lignes envoyées (quantité × coût, taxe de ligne déclarée) ; ``expected_amount`` explicite prioritaire."""
    if body.get('expected_amount') is not None:
        return money(body['expected_amount'])
    total = Decimal('0')
    for l in body.get('line_items', []) or []:
        base = money(l.get('quantity', 1)) * money(l.get('cost', 0))
        total += base + (base * money(l.get('tax_rate1', 0)) / 100).quantize(Decimal('0.01'))
    return total.quantize(Decimal('0.01'))


def check_lines(checked, body, client_id, total_ht):
    """C14 : rapprochement champ par champ — client, projet, chaque ligne normalisée (libellé, quantité, coût, taxe, nom de taxe), totaux
    Decimal, remise. 5.6.24 (F15) : contrat de relecture exigeant — libellé de chaque ligne, montant total (TTC calculé depuis les lignes
    envoyées ou ``expected_amount``), échéance, conditions, note publique et devise attendue sont EXIGÉS quand ils ont été envoyés ; un
    champ absent est un écart nommé, jamais un succès supposé."""
    issues = []
    if not checked.get('client_id'):
        issues.append('client absent de la relecture')
    elif str(checked.get('client_id')) != str(client_id):
        issues.append('client différent')
    if body.get('project_id'):
        if not checked.get('project_id'):
            issues.append('projet absent de la relecture')
        elif str(checked.get('project_id')) != str(body['project_id']):
            issues.append('projet différent')
    items = checked.get('line_items')
    if not isinstance(items, list):
        issues.append('lignes non relues')
        return issues
    expected = body.get('line_items', [])
    if len(items) != len(expected):
        issues.append('nombre de lignes différent (%d au lieu de %d)' % (len(items), len(expected)))
    for n, (a, b) in enumerate(zip(items, expected), 1):
        if not isinstance(a, dict):
            issues.append('ligne %d : illisible' % n); continue
        if 'notes' not in a:
            issues.append('ligne %d : libellé absent' % n)
        elif str(a.get('notes', '')).strip() != str(b.get('notes', '')).strip():
            issues.append('ligne %d : libellé différent' % n)
        try:
            if money(a.get('quantity', 1)) != money(b.get('quantity', 1)) or money(a.get('cost', 0)) != money(b.get('cost', 0)):
                issues.append('ligne %d : quantité ou coût différent' % n)
            if ('tax_rate1' in b or 'tax_rate1' in a) and money(a.get('tax_rate1', 0)) != money(b.get('tax_rate1', 0)):
                issues.append('ligne %d : taxe différente' % n)
        except Stop:
            issues.append('ligne %d : illisible' % n)
        if b.get('tax_name1') and str(a.get('tax_name1') or '').strip() != str(b['tax_name1']).strip():
            issues.append('ligne %d : nom de taxe différent' % n)
        if ('discount' in b or a.get('discount') not in (None, 0, 0.0, '0', '0.0')) and str(a.get('discount') or 0) != str(b.get('discount') or 0):
            issues.append('ligne %d : remise différente' % n)
    try:
        remote_total = sum((money(i.get('cost', 0)) * money(i.get('quantity', 1)) for i in items if isinstance(i, dict)), Decimal('0')).quantize(Decimal('0.01'))
        if remote_total != money(total_ht):
            issues.append('total HT différent (%s au lieu de %s)' % (remote_total, money(total_ht)))
    except Stop:
        issues.append('lignes illisibles')
    try:
        wanted = expected_total(body)
        if checked.get('amount') is None:
            issues.append('montant total absent')
        elif money(checked['amount']) != wanted:
            issues.append('montant total différent (%s au lieu de %s)' % (money(checked['amount']), wanted))
    except Stop:
        issues.append('montant total illisible')
    if checked.get('discount') not in (None, 0, 0.0, '0', '0.0') and not body.get('discount'):
        issues.append('remise inattendue')
    for field, label in (('due_date', 'échéance'), ('date', 'date')):
        if body.get(field):
            if not checked.get(field):
                issues.append(label + ' absente')
            elif str(checked[field])[:10] != str(body[field])[:10]:
                issues.append(label + ' différente')
    for field, label in (('terms', 'conditions'), ('public_notes', 'note publique')):
        if body.get(field) is not None and body.get(field) != '':
            if checked.get(field) is None:
                issues.append(label + ' absente(s)')
            elif str(checked[field]).strip() != str(body[field]).strip():
                issues.append(label + ' différente(s)')
    if body.get('currency_id'):
        if not checked.get('currency_id'):
            issues.append('devise absente')
        elif str(checked['currency_id']) != str(body['currency_id']):
            issues.append('devise différente')
    return issues


# ---------------------------------------------------------------------------------------- temps (C15, C16, N05)
def reserve_entries(desk, entry_ids, write_key):
    """C16 : réservation transactionnelle de chaque temps facturable (unicité société + entrée) ; une entrée déjà réservée bloque."""
    ensure_schema(desk)
    comp = company(desk)
    now = desk.now()
    try:
        desk.db.execute('BEGIN IMMEDIATE')
        for e in entry_ids:
            desk.db.execute('INSERT INTO invoice_ninja_entries5614 VALUES(?,?,?,?,?,?,?)', (comp, str(e), 'reservee', write_key, '', '', now))
        desk.db.commit()
    except sqlite3.IntegrityError:
        desk.db.rollback()
        raise Stop('temps_deja_reserve_ou_facture') from None


def release_entries(desk, write_key, reason):
    """Libération seulement après rapprochement (règle explicite) ; une trace est conservée."""
    ensure_schema(desk)
    desk.db.execute("DELETE FROM invoice_ninja_entries5614 WHERE write_key=? AND state='reservee'", (write_key,))
    desk.db.commit()
    desk.audit('invoice_ninja_reservation_liberee', {'write_key': write_key, 'reason': reason})


def confirm_entries(desk, write_key, remote_invoice='', remote_task=''):
    ensure_schema(desk)
    desk.db.execute("UPDATE invoice_ninja_entries5614 SET state='facturee',remote_invoice=COALESCE(NULLIF(?,''),remote_invoice),remote_task=COALESCE(NULLIF(?,''),remote_task),updated=? WHERE write_key=?",
                    (remote_invoice, remote_task, desk.now(), write_key))
    desk.db.commit()


def reserved_entries(desk):
    ensure_schema(desk)
    return {r['entry_id'] for r in desk.db.execute('SELECT entry_id FROM invoice_ninja_entries5614 WHERE company=?', (company(desk),))}


def mark_billed_remotely(desk, entry_ids, remote_invoice):
    """Temps facturé depuis Invoice Ninja (synchronisation) : ne peut plus être facturé par AxiorHub."""
    ensure_schema(desk)
    comp = company(desk)
    for e in entry_ids:
        desk.db.execute('INSERT OR REPLACE INTO invoice_ninja_entries5614 VALUES(?,?,?,?,?,?,?)', (comp, str(e), 'facturee_distante', 'sync:' + str(remote_invoice), '', str(remote_invoice), desk.now()))
    desk.db.commit()


def time_log(entry):
    """C15 : représentation fidèle — un intervalle réellement chronométré est transmis tel quel ; une durée déclarée est signalée comme telle
    (time_log avec début à minuit du jour, durée exacte) et la provenance est conservée dans la description."""
    minutes = int(entry['minutes'])
    origin = str(entry.get('origin') or 'declaree')
    measured = origin in ('chrono', 'mesuree', 'measured') and entry.get('started_at')
    if measured:
        start = datetime.fromisoformat(str(entry['started_at']))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = start.timestamp() + minutes * 60
        return [[int(start.timestamp()), int(end)]], 'intervalle mesuré'
    day = datetime.fromisoformat(str(entry['day'])).replace(tzinfo=timezone.utc)
    return [[int(day.timestamp()), int(day.timestamp()) + minutes * 60]], 'durée déclarée (%d min, jour %s, heure non mesurée)' % (minutes, str(entry['day'])[:10])


# ---------------------------------------------------------------------------------------- synchronisation (N07)
def pull(desk, http=None, limit_entities=('invoice', 'quote')):
    """Lecture périodique avec curseur simple : statuts et paiements d'Invoice Ninja font autorité ; un objet rejoué ne répète aucun effet."""
    ensure_schema(desk)
    client = InvoiceNinjaClient(desk, http)
    comp = client.company
    changes = {'invoice': 0, 'quote': 0, 'billed_entries': 0}
    for entity in limit_entities:
        try:
            rows = client.list_all(entity, {'sort': 'updated_at|desc'})
        except Stop as ex:
            changes[entity + '_error'] = str(ex)
            continue
        for r in rows:
            rid = str(r.get('id'))
            old = desk.db.execute('SELECT updated_at,status_id,paid_to_date FROM invoice_ninja_sync5614 WHERE company=? AND entity=? AND remote_id=?', (comp, entity, rid)).fetchone()
            updated_at = str(r.get('updated_at') or '')
            if old and old['updated_at'] == updated_at and old['status_id'] == str(r.get('status_id') or '') and old['paid_to_date'] == str(r.get('paid_to_date') or ''):
                continue
            desk.db.execute('INSERT OR REPLACE INTO invoice_ninja_sync5614 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (comp, entity, rid, str(r.get('number') or ''), str(r.get('status_id') or ''), str(r.get('amount') or ''), str(r.get('balance') or ''),
                             str(r.get('paid_to_date') or ''), updated_at, json.dumps({k: r.get(k) for k in ('client_id', 'project_id', 'date', 'due_date')}, default=str), desk.now()))
            changes[entity] += 1
            if entity == 'invoice':
                for item in (r.get('line_items') or []):
                    if isinstance(item, dict) and str(item.get('task_id') or ''):
                        mapped = desk.db.execute("SELECT local_id FROM invoice_ninja_map5614 WHERE company=? AND entity='task' AND remote_id=?", (comp, str(item['task_id']))).fetchone()
                        if mapped:
                            mark_billed_remotely(desk, [mapped['local_id'].split(':', 1)[-1]], rid)
                            changes['billed_entries'] += 1
    desk.db.commit()
    desk.setting('invoice_ninja_sync5614:last', {'at': desk.now(), 'changes': changes})
    return changes


def sync_status(desk):
    return desk.settings('invoice_ninja_sync5614:last', None)


def remote_state(desk, entity, remote_id):
    ensure_schema(desk)
    r = desk.db.execute('SELECT * FROM invoice_ninja_sync5614 WHERE company=? AND entity=? AND remote_id=?', (company(desk), entity, str(remote_id))).fetchone()
    if not r:
        return None
    labels = INVOICE_STATUS if entity == 'invoice' else QUOTE_STATUS
    return {**dict(r), 'status_label': labels.get(r['status_id'], r['status_id'])}


# ---------------------------------------------------------------------------------------- parcours Pilote (N09)
def missing_for(desk, matter, what):
    """Données manquantes regroupées en une seule carte avant un parcours de facturation."""
    m = next((x for x in load_matters(desk.c) if str(x['id']) == str(matter)), None)
    if not m:
        raise Stop('dossier_absent')
    missing = []
    cfg = desk.c.get('invoice_ninja', {})
    if not cfg.get('enabled') or not cfg.get('api_token_file'):
        missing.append('société Invoice Ninja (jeton et URL dans Connexions)')
    if not cfg.get('write_enabled'):
        missing.append('autorisation d’écriture Invoice Ninja')
    link = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (m['id'],)).fetchone()
    if not link and what in ('project', 'quote'):
        missing.append('client facturé du dossier')
    if what == 'quote':
        try:
            from .time500 import terms
            t = terms(desk, m['id'])
            if not t or not (t.get('rate') or t.get('budget')):
                missing.append('tarif convenu (taux horaire ou forfait)')
        except Exception:
            missing.append('tarif convenu (taux horaire ou forfait)')
    return {'matter': m['id'], 'what': what, 'missing': missing, 'ready': not missing}


def section_html(desk, matter, prefix):
    from html import escape as e
    ensure_schema(desk)
    comp = company(desk) if desk.c.get('invoice_ninja', {}).get('enabled') else ''
    if not comp:
        return ''
    client_id = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (str(matter),)).fetchone()
    project = desk.db.execute("SELECT remote_id FROM invoice_ninja_map5614 WHERE company=? AND entity='project' AND local_id=?", (comp, 'matter:' + str(matter))).fetchone()
    quotes = desk.db.execute("SELECT * FROM invoice_ninja_map5614 WHERE company=? AND entity='quote' AND local_id LIKE ?", (comp, 'quote:' + str(matter) + ':%')).fetchall()
    sync = sync_status(desk)
    rows = ''
    for q in quotes:
        st = remote_state(desk, 'quote', q['remote_id'])
        rows += '<li>Devis %s — %s</li>' % (e(st['number'] if st and st['number'] else q['remote_id']), e(st['status_label'] if st else 'état non synchronisé'))
    return ('<h3>Invoice Ninja — client, projet, devis (5.6.14)</h3><p class="vf-note">Client facturé : <strong>%s</strong> · projet : <strong>%s</strong> · dernière synchronisation : %s. '
            'Les devis et factures sont créés en brouillon, jamais envoyés ni convertis ; Invoice Ninja reste la source des numéros, statuts et paiements.</p>%s'
            '<form class="m5-form m5-inline" data-api="m568/ninja/project" data-reload="1" data-confirm="Créer ou rattacher le projet Invoice Ninja de ce dossier ?">'
            '<input type="hidden" name="matter" value="%s"><button class="ax-btn ghost" type="submit">Créer / rattacher le projet</button></form> '
            '<form class="m5-form m5-inline" data-api="m568/ninja/sync" data-reload="1"><button class="ax-btn ghost" type="submit">Synchroniser les statuts</button></form>') % (
        e(client_id['client_id'] if client_id else 'non rattaché'), e(project['remote_id'] if project else 'aucun'), e(str(sync['at'])[:16].replace('T', ' ') if sync else 'jamais'),
        ('<ul>%s</ul>' % rows) if rows else '', e(str(matter), quote=True))

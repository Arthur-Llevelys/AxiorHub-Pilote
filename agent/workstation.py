"""AxiorHub 3.1.0 — human-scale legal workstation integrations.

This module is deliberately read-only toward external services.  It keeps
local mappings and a bounded cache of unpaid Invoice Ninja invoices.  It never
sends email, creates an invoice, edits a remote document, or drives OnlyOffice.
(5.6.9 : les écritures Invoice Ninja — brouillons de facture, temps passés — vivent dans facturation569, après validation explicite.)
"""
from datetime import datetime, timezone, timedelta
import json
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlencode, urlsplit

from .common import HTTP, Stop, digest, load_matters, read_secret


SCHEMA = '''
CREATE TABLE IF NOT EXISTS matter_workspace_links_v310(
 matter TEXT PRIMARY KEY, folder_id TEXT NOT NULL, folder_name TEXT NOT NULL,
 folder_url TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invoice_ninja_cache_v310(
 id TEXT PRIMARY KEY, client_id TEXT NOT NULL, number TEXT NOT NULL,
 status TEXT NOT NULL, amount REAL NOT NULL, balance REAL NOT NULL,
 due_date TEXT NOT NULL, invoice_url TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS matter_invoice_links_v310(
 matter TEXT PRIMARY KEY, client_id TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workstation_refresh_v310(
 source TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL,
 updated TEXT NOT NULL);
'''

# Cabinet services are navigation shortcuts; configuring a URL never grants
# the agent an API credential or permission to perform an external action.
CABINET_SITES = (
    ('postulation','Postulation · LexDélai','https://postulation.example.com/'),
    ('lexdelai_bail','LexDélai Bail commercial','https://bail.example.com/'),
    ('lexdelai_societe','LexDélai Société','https://societe.example.com/'),
    ('honoraires','Honoraires · Honorium','https://honoraires.example.com/'),
    ('injonction','Injonction de payer','https://injonction.example.com/'),
    ('notes_frais','Notes de frais','https://frais.example.com/'),
    ('pdf_tools','Atelier PDF · pièces','https://pdf.example.com/'),
)


FRENCH_REASONS = {
    'brouillon_imap_verifie': 'Brouillon retrouvé dans le dossier IMAP configuré ; destinataires et texte contrôlés.',
    'projet_prudent_verifie': 'Brouillon confirmé retrouvé et contrôlé dans la messagerie.',
    'brouillon_non_retrouve': 'Le serveur a répondu au dépôt, mais le brouillon est introuvable. Vérifier Roundcube avant toute reprise.',
    'brouillon_non_retrouve_verifier': 'Aucun brouillon identifié au nouveau contrôle. Vérifier Roundcube avant toute reprise.',
    'brouillon_contenu_non_conforme': 'Le brouillon retrouvé diffère du projet ou de ses destinataires. Contrôle manuel requis.',
    'plusieurs_brouillons_meme_identifiant': 'Plusieurs brouillons portent le même identifiant. Vérifier les doublons dans Roundcube.',
    'verification_imap_indisponible': 'La relecture IMAP a échoué. Le dépôt est incertain ; vérifier Roundcube.',
    'append_imap_incertain': 'Le serveur n’a pas confirmé le dépôt. Vérifier Roundcube avant toute reprise.',
    'brouillon_deja_depose_ou_incertain': 'Un dépôt existe ou reste incertain. Vérifier Roundcube avant toute reprise.',
    'action_interrompue_consulter_le_journal': 'Le traitement a échoué. Le diagnostic technique doit être examiné avant une nouvelle tentative.',
    'deja_lu': 'Ce message avait déjà été ouvert. La reprise des UID récents peut le traiter une fois dans la fenêtre rétroactive.',
    'deja_repondu': 'Le message porte déjà le marqueur IMAP « répondu ».',
    'brouillon_existant': 'Un brouillon correspondant existe déjà dans Roundcube.',
    'reponse_envoyee_depuis': 'Une réponse plus récente a déjà été envoyée dans ce fil.',
    'reponse_envoyee_objet_identique': 'Un envoi récent au même destinataire porte le même objet.',
    'correspondant_ou_dossier_a_confirmer': 'Le dossier ou le rôle du correspondant n’est pas établi avec assez de certitude.',
    'association_ambigue': 'Plusieurs dossiers restent possibles ; AxiorHub attend votre choix.',
    'confiance_dossier_insuffisante': 'Le rapprochement avec le dossier est trop incertain pour travailler automatiquement.',
    'plusieurs_dossiers_possibles': 'Deux dossiers ou plus obtiennent des scores trop proches.',
    'index_dossier_a_completer': 'Les sources du dossier ne sont pas encore assez indexées.',
    'aucun_document_exploitable': 'Aucun document exploitable n’a été trouvé dans le périmètre autorisé.',
    'inventaire_trop_volumineux': 'Le dossier dépasse l’ancien inventaire monobloc ; un parcours progressif doit être terminé.',
    'intervention_avocat_ordali': 'La demande implique une décision juridique de l’avocat avant rédaction.',
    'controle_documentaire_non_approuve': 'Le contrôleur indépendant a détecté un blocage dans le projet documentaire.',
    'connexion_http_indisponible': 'Un service local ou distant configuré n’a pas répondu. Aucune action externe n’a été effectuée.',
    'ollama_generation_injoignable': 'Le modèle local n’a pas pu être joint.',
    'ollama_generation_delai_depasse': 'Le modèle local n’a pas terminé dans le délai prévu.',
}


def ensure_schema(desk):
    desk.db.executescript(SCHEMA)
    columns={r[1] for r in desk.db.execute('PRAGMA table_info(invoice_ninja_cache_v310)')}
    if 'currency' not in columns:
        desk.db.execute("ALTER TABLE invoice_ninja_cache_v310 ADD COLUMN currency TEXT NOT NULL DEFAULT ''")
    for name in ('amount_exact','balance_exact'):
        if name not in columns:
            desk.db.execute(f"ALTER TABLE invoice_ninja_cache_v310 ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    desk.db.commit()


def reason_fr(code):
    code = str(code or '')
    return FRENCH_REASONS.get(code, code.replace('_', ' ').strip().capitalize() or 'Aucun motif enregistré.')


def _configured_url(desk, key):
    defaults = {
        'roundcube': 'https://courriel.example.com/webmail/',
        'roundcube_drafts': 'https://courriel.example.com/webmail/?_task=mail&_mbox=INBOX.Drafts',
        'openwebui': 'https://ai.example.com/',
        'nextcloud': 'https://cloud.example.com/index.php/apps/files/',
        'onlyoffice': 'https://myoffice.example.com/welcome/',
        'invoice_ninja': '',
    }
    defaults.update({key:address for key,_,address in CABINET_SITES})
    stored = desk.settings('workstation:url:' + key, None)
    candidate = stored if stored is not None else desk.c.get('workstation', {}).get(key + '_url', defaults.get(key, ''))
    if key in desk.c.get('interfaces',{}):candidate=desk.c['interfaces'][key]
    if stored is None and key == 'invoice_ninja' and not str(candidate or '').strip():
        invoice = desk.c.get('invoice_ninja', {})
        candidate = invoice.get('web_url') or invoice.get('base_url') or ''
        if not invoice.get('web_url') and urlsplit(str(candidate)).query:
            candidate = ''  # Never expose an API token carried in a query string.
        if str(candidate).rstrip('/').endswith('/api/v1'):
            candidate = str(candidate).rstrip('/')[:-7]
    value = str(candidate or '').strip()
    if not value:
        return ''
    p = urlsplit(value)
    if p.scheme != 'https' or not p.netloc or p.username or p.password or p.fragment:
        return ''
    return value


def external_links(desk):
    return {key: _configured_url(desk, key) for key in
            ('roundcube', 'roundcube_drafts', 'openwebui', 'nextcloud', 'onlyoffice', 'invoice_ninja',
             *(key for key,_,_ in CABINET_SITES))}


def save_external_url(desk, key, value):
    if key not in {'roundcube', 'roundcube_drafts', 'openwebui', 'nextcloud', 'onlyoffice', 'invoice_ninja',
                   *(key for key,_,_ in CABINET_SITES)}:
        raise Stop('raccourci_inconnu')
    value = str(value or '').strip()
    if value:
        p = urlsplit(value)
        if p.scheme != 'https' or not p.netloc or p.username or p.password or p.fragment:
            raise Stop('url_raccourci_invalide')
    desk.setting('workstation:url:' + key, value)
    desk.audit('workstation_url_updated', {'key': key, 'configured': bool(value)})
    return {'saved': True, 'key': key}


def save_workspace_mapping(desk, matter, folder_id, folder_name, folder_url):
    ensure_schema(desk)
    matter = str(matter or '')
    if not any(x['id'] == matter for x in load_matters(desk.c)):
        raise Stop('dossier_absent')
    folder_id = str(folder_id or '').strip()
    folder_name = str(folder_name or '').strip()
    folder_url = str(folder_url or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', folder_id) or not 1 <= len(folder_name) <= 300:
        raise Stop('dossier_openwebui_invalide')
    base = _configured_url(desk, 'openwebui')
    p, origin = urlsplit(folder_url), urlsplit(base)
    if not base or p.scheme != 'https' or (p.scheme, p.netloc) != (origin.scheme, origin.netloc):
        raise Stop('url_openwebui_hors_instance')
    desk.db.execute('INSERT OR REPLACE INTO matter_workspace_links_v310 VALUES(?,?,?,?,?)',
                    (matter, folder_id, folder_name, folder_url, desk.now()))
    desk.db.commit()
    desk.audit('openwebui_matter_mapping_saved', {'matter': matter, 'folder_id': folder_id})
    return {'saved': True, 'matter': matter, 'folder_name': folder_name}


def workspace_mapping(desk, matter):
    ensure_schema(desk)
    row = desk.db.execute('SELECT * FROM matter_workspace_links_v310 WHERE matter=?', (str(matter),)).fetchone()
    return dict(row) if row else None


def link_invoice_client(desk, matter, client_id):
    ensure_schema(desk)
    if not any(x['id'] == str(matter) for x in load_matters(desk.c)):
        raise Stop('dossier_absent')
    client_id = str(client_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', client_id):
        raise Stop('client_invoice_ninja_invalide')
    desk.db.execute('INSERT OR REPLACE INTO matter_invoice_links_v310 VALUES(?,?,?)',
                    (str(matter), client_id, desk.now()))
    desk.db.commit()
    return {'saved': True, 'matter': str(matter), 'client_id': client_id}


def refresh_unpaid_invoices(desk, http=None):
    """Invoice Ninja v5 : GET paginé, soldes décimaux, aucune écriture distante."""
    ensure_schema(desk)
    cfg = desk.c.get('invoice_ninja', {})
    if not cfg.get('enabled', False) or not cfg.get('base_url') or not cfg.get('api_token_file'):
        raise Stop('invoice_ninja_non_configure')
    client = http or HTTP(str(cfg['base_url']).rstrip('/'), timeout=min(60, max(5, int(cfg.get('timeout_seconds', 30)))))
    if http is None:
        client.headers.update({'X-API-TOKEN': read_secret(cfg['api_token_file']), 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'})
    rows, seen, complete = [], set(), False
    max_pages = min(100, max(1, int(cfg.get('max_pages', 30))))
    def money(value):
        try:
            result=Decimal(str(value or 0))
            if not result.is_finite():raise InvalidOperation()
            return result
        except (InvalidOperation, ValueError):raise Stop('invoice_ninja_montant_invalide') from None
    for page in range(1, max_pages + 1):
        # Les versions de l'API divergent sur client_status : filtre local
        # explicite d'après status_id et balance, jamais unpaid,overdue.
        query = urlencode({'status':'active','per_page':100,'page':page})
        raw = client.request('GET', client.base + '/api/v1/invoices?' + query, headers={'Accept':'application/json'}, limit=4_000_000)
        try:payload = json.loads(raw,parse_float=Decimal)
        except (ValueError, UnicodeError):raise Stop('invoice_ninja_reponse_invalide') from None
        data = payload.get('data',[]) if isinstance(payload,dict) else None
        if not isinstance(data,list) or len(data)>100:raise Stop('invoice_ninja_reponse_invalide')
        for item in data:
            if not isinstance(item,dict) or not item.get('id'):raise Stop('invoice_ninja_reponse_invalide')
            iid=str(item['id'])
            if iid in seen:raise Stop('invoice_ninja_pagination_repetee')
            seen.add(iid)
            amount,balance=money(item.get('amount')),money(item.get('balance'))
            status=str(item.get('status_id',item.get('status','')))
            if balance<=0 or status not in ('2','3','-1','-2') or item.get('is_deleted') or item.get('is_archived'):continue
            currency=item.get('currency_code') or item.get('currency') or ''
            if isinstance(currency,dict):currency=currency.get('code','')
            currency=str(currency).upper()
            if not re.fullmatch(r'[A-Z]{3}',currency):currency=''
            invitations=item.get('invitations') or []
            url=str(invitations[0].get('link','')) if isinstance(invitations,list) and invitations and isinstance(invitations[0],dict) else ''
            rows.append((iid,str(item.get('client_id','')),str(item.get('number','')),status,float(amount),float(balance),str(item.get('due_date') or ''),url,desk.now(),currency,str(amount),str(balance)))
        pagination=(payload.get('meta') or {}).get('pagination') or {}
        total_pages=pagination.get('total_pages')
        if total_pages is not None:
            try:complete=page>=int(total_pages)
            except (TypeError,ValueError):raise Stop('invoice_ninja_pagination_invalide') from None
        else:complete=len(data)<100
        if complete:break
    if complete:desk.db.execute('DELETE FROM invoice_ninja_cache_v310')
    desk.db.executemany('INSERT OR REPLACE INTO invoice_ninja_cache_v310 (id,client_id,number,status,amount,balance,due_date,invoice_url,updated,currency,amount_exact,balance_exact) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    desk.db.execute('INSERT OR REPLACE INTO workstation_refresh_v310 VALUES(?,?,?,?)', ('invoice_ninja','ok' if complete else 'partial','' if complete else 'Plafond de pagination atteint ; cache partiel, anciennes lignes conservées.',desk.now()))
    desk.db.commit()
    desk.audit('invoice_ninja_unpaid_refreshed',{'count':len(rows),'method':'GET','writes':0,'complete':complete,'pages':page})
    return {'unpaid_invoices':len(rows),'external_method':'GET','invoice_created':False,'payment_created':False,'complete':complete,'pages':page,'message':'Lecture complète.' if complete else 'Lecture partielle : augmenter le plafond des pages puis actualiser.'}


def unpaid_summary(desk, matter=''):
    ensure_schema(desk)
    sync = desk.db.execute("SELECT status,updated FROM workstation_refresh_v310 WHERE source='invoice_ninja'").fetchone()
    freshness = {'synchronized': bool(sync and sync['status'] == 'ok'),
                 'last_sync': sync['updated'] if sync else None}
    params = ()
    sql = 'SELECT * FROM invoice_ninja_cache_v310'
    if matter:
        link = desk.db.execute('SELECT client_id FROM matter_invoice_links_v310 WHERE matter=?', (matter,)).fetchone()
        if not link: return {'count': 0, 'balance': 0, 'invoices': [], 'linked': False, **freshness}
        sql += ' WHERE client_id=?'; params = (link['client_id'],)
    rows = [dict(x) for x in desk.db.execute(sql + ' ORDER BY due_date,number LIMIT 100', params)]
    grouped={}
    for row in rows:
        grouped.setdefault(row['currency'] or 'devise inconnue',Decimal(0))
        grouped[row['currency'] or 'devise inconnue']+=Decimal(row.get('balance_exact') or str(row['balance']))
    exact={k:str(v.quantize(Decimal('0.01'))) for k,v in grouped.items()}
    grouped={k:float(v.quantize(Decimal('0.01'))) for k,v in grouped.items()}
    single=len(rows)==1 or (len(grouped)==1 and 'devise inconnue' not in grouped)
    balance=float(sum((Decimal(x.get('balance_exact') or str(x['balance'])) for x in rows),Decimal(0)).quantize(Decimal('0.01'))) if single or not rows else None
    label=(f'{balance:.2f} '+next(iter(grouped)) if single and grouped else
           ('0,00 (aucune facture)' if not rows else 'Montants par devise : '+', '.join(
               f'{amount:.2f} {currency}' for currency,amount in sorted(grouped.items()))))
    return {'count':len(rows),'balance':balance,'balance_display':label,
            'balance_by_currency':grouped,'balance_exact_by_currency':exact,'invoices':rows,
            'linked':True if matter else None,**freshness}


def why_nothing(desk):
    ensure_schema(desk)
    from .state import State
    state = State(desk.c['state_dir'])
    raw = list(state.db.execute('SELECT reason,COUNT(*) FROM messages GROUP BY reason ORDER BY COUNT(*) DESC LIMIT 12'))
    recent_mail_issues=[]
    for row in state.db.execute("SELECT key,status,reason,updated FROM messages "
                                "WHERE status IN ('review','error','append_uncertain') "
                                "ORDER BY updated DESC LIMIT 8"):
        recent_mail_issues.append({'key':row[0], 'status':row[1],
            'label':reason_fr(row[2]) if row[2] in FRENCH_REASONS else
                    'Motif non reconnu : consulter le journal privé.', 'updated':row[3]})
    since=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
    verified_24h=state.db.execute("SELECT COUNT(*) FROM messages WHERE status='drafted' "
        "AND reason IN ('brouillon_imap_verifie','projet_prudent_verifie') AND updated>=?",(since,)).fetchone()[0]
    uncertain_count=state.db.execute("SELECT COUNT(*) FROM messages WHERE status='append_uncertain'").fetchone()[0]
    state.db.close()
    reasons = [{'code': row[0], 'label': (reason_fr(row[0]) if row[0] in FRENCH_REASONS else
                'Motif non reconnu : consulter le journal privé.'), 'count': row[1]} for row in raw]
    queue = {row[0]: row[1] for row in desk.db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')}
    failures = []
    for row in desk.db.execute("SELECT id,kind,status,created,finished,result FROM jobs WHERE status='error' ORDER BY id DESC LIMIT 8"):
        try:
            value = json.loads(row['result'] or '{}')
            code = value.get('erreur', '') if isinstance(value, dict) else ''
        except (ValueError, TypeError):
            code = ''
        # Only known codes can be displayed; arbitrary exception text may contain private data.
        if not isinstance(code, str) or code not in FRENCH_REASONS:
            code = 'action_interrompue_consulter_le_journal'
        failures.append({'id': row['id'], 'kind': row['kind'], 'code': code,
                         'label': reason_fr(code), 'created': row['created'], 'finished': row['finished']})
    return {
        'verified_drafts_24h': verified_24h,
        'uncertain_drafts': uncertain_count,
        'recent_mail_issues': recent_mail_issues,
        'recent_failures': failures,
        'mail_collection': {
            'seen_messages_included': bool(desk.c.get('mail', {}).get('process_seen_recent', True)),
            'retroactive_days': int(desk.c.get('mail', {}).get('retroactive_lookback_days', 7)),
            'candidate_limit': int(desk.c.get('mail', {}).get('retroactive_max_candidates_per_run', 80)),
        },
        'reasons': reasons,
        'jobs': queue,
        'mail_automation_active': bool(desk.settings('automation:automatic_mail_drafts_enabled',
                                                     desk.c.get('orchestrator', {}).get('automatic_mail_drafts_enabled', True))),
        'document_automation_active': bool(desk.settings('automation:automatic_legal_projects_enabled',
                                                         desk.c.get('orchestrator', {}).get('automatic_legal_projects_enabled', True))),
    }


def daily_briefing(desk):
    ensure_schema(desk)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).isoformat()
    def count(sql, params=()):
        try: return int(desk.db.execute(sql, params).fetchone()[0])
        except Exception: return 0
    ready_documents = count("SELECT COUNT(*) FROM document_projects_v220 WHERE status='pending'")
    unread_notifications = count("SELECT COUNT(*) FROM orchestration_notifications_v260 WHERE status='unread'")
    decisions = count("SELECT COUNT(*) FROM cabinet_decisions_v290 WHERE status IN ('pending','high_risk')")
    signals = count("SELECT COUNT(*) FROM proactive_signals WHERE state='open'")
    new_mail = count("SELECT COUNT(*) FROM work_items WHERE received>=?", (since,))
    events = count('SELECT COUNT(*) FROM calendar_cache WHERE starts>=? AND starts<?',
                   (now.isoformat(), (now + timedelta(days=7)).isoformat()))
    invoices = unpaid_summary(desk)
    return {'events_next_7_days': events, 'new_mail_24h': new_mail, 'open_anomalies': signals,
            'ready_projects': ready_documents, 'unread_notifications': unread_notifications,
            'decisions_expected': decisions, 'unpaid_invoices': invoices['count'],
            'unpaid_synchronized': invoices['synchronized'], 'unpaid_last_sync': invoices['last_sync'],
            'unpaid_balance': invoices['balance'], 'generated_at': now.isoformat()}

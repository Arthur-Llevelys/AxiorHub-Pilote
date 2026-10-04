"""Browser work queue, evidence-based discovery and explicitly approved registry.

Only the worker executes jobs. No arbitrary commands, SMTP, or automatic roles.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .common import (Stop, clean_path, digest, fold, indexable_matters,
                     load_config, load_matters, private_json, under)
from .dav import DAV
from .index import DocumentIndex
from .mailbox import Mailbox, addresses, exclusion
from .state import State
from . import trace480

ROLES = {'client': 'Client', 'confrere_adverse': 'Conseil adverse',
         'tiers': 'Tiers / confrère partenaire', 'prospect': 'Prospect'}
JOBS = {'sync', 'approve', 'reject', 'associate', 'remove_contact', 'retry',
        'retry_matter', 'index', 'review', 'forget', 'run', 'learn',
        'discover', 'browse', 'register_matter', 'chat', 'forget_chat', 'index_all',
        'refresh_brief','validate_fact','pin_fact','archive_fact','attachment_review',
        'deadline_review','confirm_event','confirm_task','ignore_deadline','prepare_draft',
        'prepare_reply','deposit_draft','feedback','add_rule','mark_handled','execute_actions',
        'memory_insight','memory_scope','health','daily_digest','automation_setting',
        'create_matter', 'assistant_answer','sync_legal_memory','memory_all','validate_memory',
        'pin_memory','dispute_memory','archive_memory','resolve_conflict',
        'analyze_strategy','build_matrix','draft_act','validate_strategy','archive_strategy',
        'validate_matrix_row','dispute_matrix_row','archive_matrix_row','validate_act','archive_act',
        'monitor_matter','monitor_all','build_daily_dashboard','ack_signal','snooze_signal','resolve_signal'}
JOBS |= {'organize_cabinet','classify_portfolio','reconcile_inbox','set_matter_state','confirm_matter','reject_group'}
JOBS |= {'propose_work_plan','sync_caldav_tasks','apply_work_plan','update_work_task','edit_work_task','schedule_work_task',
         'create_agenda_event','edit_agenda_event','cancel_agenda_event'}
JOBS |= {'prepare_document_project','create_document_files'}
JOBS |= {'analyze_notice440','analyze_deadline450','extract_facts460'}
JOBS |= {'search_index490'}
JOBS |= {'autonomy_mail_sweep','refresh_operational_memory','review_autonomy_proposal'}
JOBS |= {'legal_research','import_mcp_legal_results','verify_official_decision','identify_latest_writings','refresh_exhibit_registry'}
JOBS |= {'identify_party_writings','compare_devices','prepare_hearing','create_hearing_files',
         'prepare_word_project','create_word_files'}
JOBS |= {'orchestrate_mail','orchestrator_mail_sweep','review_orchestration_notification',
         'prepare_legal_opinion','review_legal_opinion'}
JOBS |= {'refresh_cabinet_pilotage','prepare_meeting','prepare_transcript_report',
         'review_cabinet_decision','create_cabinet_confirmation_batch',
         'approve_cabinet_confirmation_batch','record_provision',
         'run_continuous_business_tests'}
JOBS |= {'refresh_unpaid_invoices'}
JOBS |= {'prepare_cabinet_letter','prepare_cabinet_revision','create_cabinet_letter'}
JOBS |= {'proactive34_cycle','proactive34_briefing','proactive34_now'}
JOBS |= {'coach_hearing35','prepare_call35','record_call35','calculate35',
         'billing_review35','classify_comparable35'}
JOBS |= {'save_mail_rule370','delete_mail_rule370','record_correction370','set_automation_level370'}
JOBS |= {'review_action380','refresh_matter_graph380','start_playbook380','advance_playbook380',
         'complete_playbook_step380','save_ecosystem_service380',
         'prepare_ecosystem_action380','run_business_evaluation380'}
JOBS |= {'production_cycle390','advance_playbooks390'}
JOBS |= {'production_cycle391','advance_playbooks391','retry_production391',
         'review_output391','universal_command391'}
JOBS |= {'set_learning_rule392'}
JOBS |= {'run_legal_benchmark410'}
JOBS |= {'verify_deliverable420','retry_deliverable420','studio_prepare420',
         'advance_matter420','snapshot_metrics420'}
JOBS |= {'live_mail430','live_calendar430','live_documents430'}
JOBS |= {'pieces_scan510','pieces_create510'}
JOBS |= {'edit_personal_event','edit_personal_task'}
JOBS |= {'routine520','docrequest520','deck530_sync','style550_scan'}

USER_JOBS = {'prepare_reply','deposit_draft','associate','approve','retry','retry_matter',
             'run','chat','assistant_answer','review','prepare_draft','execute_actions',
             'mark_handled','analyze_strategy','build_matrix','draft_act','monitor_matter',
             'ack_signal','snooze_signal','resolve_signal','build_daily_dashboard'}
USER_JOBS |= {'organize_cabinet','classify_portfolio','reconcile_inbox','set_matter_state','confirm_matter','reject_group'}
USER_JOBS |= {'propose_work_plan','sync_caldav_tasks','apply_work_plan','update_work_task','edit_work_task','schedule_work_task',
              'create_agenda_event','edit_agenda_event','cancel_agenda_event'}
USER_JOBS |= {'prepare_document_project','create_document_files'}
USER_JOBS |= {'pieces_scan510','pieces_create510'}
USER_JOBS |= {'edit_personal_event','edit_personal_task'}
USER_JOBS |= {'routine520','docrequest520'}
USER_JOBS |= {'review_autonomy_proposal'}
USER_JOBS |= {'legal_research','import_mcp_legal_results','verify_official_decision','identify_latest_writings','refresh_exhibit_registry'}
USER_JOBS |= {'identify_party_writings','compare_devices','prepare_hearing','create_hearing_files',
              'prepare_word_project','create_word_files'}
USER_JOBS |= {'orchestrate_mail','review_orchestration_notification',
              'prepare_legal_opinion','review_legal_opinion'}
USER_JOBS |= {'prepare_meeting','prepare_transcript_report','review_cabinet_decision',
              'create_cabinet_confirmation_batch','approve_cabinet_confirmation_batch',
              'record_provision','run_continuous_business_tests'}
USER_JOBS |= {'refresh_unpaid_invoices'}
USER_JOBS |= {'prepare_cabinet_letter','prepare_cabinet_revision','create_cabinet_letter'}
USER_JOBS |= {'proactive34_now'}
USER_JOBS |= {'coach_hearing35','prepare_call35','record_call35','calculate35',
              'billing_review35','classify_comparable35'}
USER_JOBS |= {'refresh_matter_graph380','start_playbook380','advance_playbook380',
              'complete_playbook_step380','prepare_ecosystem_action380','run_business_evaluation380'}
USER_JOBS |= {'production_cycle390','advance_playbooks390'}
USER_JOBS |= {'production_cycle391','advance_playbooks391','retry_production391',
              'review_output391','universal_command391'}
USER_JOBS |= {'run_legal_benchmark410'}
USER_JOBS |= {'retry_deliverable420','studio_prepare420','advance_matter420'}
NORMAL_JOBS = {'learn','attachment_review','deadline_review','confirm_event','confirm_task',
               'ignore_deadline','feedback','add_rule','memory_insight','memory_scope','forget',
               'forget_chat','register_matter','create_matter','browse','validate_memory',
               'pin_memory','dispute_memory','archive_memory','resolve_conflict'}
NORMAL_JOBS |= {'validate_strategy','archive_strategy','validate_matrix_row',
                'dispute_matrix_row','archive_matrix_row','validate_act','archive_act'}
NORMAL_JOBS |= {'save_mail_rule370','delete_mail_rule370','record_correction370','set_automation_level370'}
NORMAL_JOBS |= {'review_action380','save_ecosystem_service380'}
NORMAL_JOBS |= {'set_learning_rule392'}
NORMAL_JOBS |= {'verify_deliverable420','snapshot_metrics420'}
NORMAL_JOBS |= {'analyze_notice440','analyze_deadline450','extract_facts460'}
NORMAL_JOBS |= {'search_index490'}


def job_priority(kind):
    if kind in USER_JOBS:return 0
    if kind in NORMAL_JOBS:return 10
    if kind in ('index','refresh_brief','sync_legal_memory','health'):return 50
    return 80


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def run_lock(c):
    with open(Path(c['state_dir'])/'run.lock', 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Stop('traitement_deja_en_cours') from None
        from .queue521 import note_holder
        note_holder(c['state_dir'], 'Travail de la file')
        yield


def email_value(value):
    value = value.strip().lower()
    if len(value)>254 or not re.fullmatch(r'[^\s@,;<>"\\]+@[^\s@,;<>"\\]+\.[^\s@,;<>"\\]+', value):
        raise Stop('adresse_invalide')
    return value


def report_for(c, key):
    if not re.fullmatch('[0-9a-f]{64}', key):
        raise Stop('cle_invalide')
    p = Path(c['state_dir'])/'reports'/(key+'.json')
    if not p.is_file():
        raise Stop('rapport_absent_ou_expire')
    report = json.loads(p.read_text())
    account = digest(c['mail']['username']+'@'+c['mail']['host'])
    if report.get('account_key') not in (None, account):
        raise Stop('rapport_autre_compte')
    return report


def save_matter(c, matter):
    path = Path(c['state_dir'])/'registry-web.json'
    previous = path.read_bytes() if path.exists() else b'[]'
    rows = json.loads(previous)
    rows = [r for r in rows if r['id'] != matter['id']] + [matter]
    # Bounded local versions allow recovering accidental corrections.
    backups = path.parent/'registry-backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    private_json(backups/(digest(previous.decode())+'.json'), json.loads(previous))
    private_json(path, rows)


# 5.6.1 : ordre de prise des travaux avec vieillissement. Un travail automatique gagne 4 points de priorité par heure d'attente
# (jusqu'à 30 au mieux) : il n'attend plus indéfiniment derrière le flux continu des contrôles, et ne passe jamais devant une demande
# de l'avocat (priorité inférieure à 30).
PICK_ORDER=("CASE WHEN priority<30 THEN priority ELSE MAX(30,priority-MIN(60,CAST((julianday('now')-julianday(COALESCE(created,'now')))*96 AS INTEGER)))"
            " END,id")


class Desk:
    def __init__(self, c):
        self.c = c
        self.db = sqlite3.connect(Path(c['state_dir'])/'desk.sqlite3', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, kind TEXT, args TEXT,
            status TEXT, created TEXT, finished TEXT, result TEXT);
        CREATE TABLE IF NOT EXISTS proposals(key TEXT PRIMARY KEY, matter TEXT, email TEXT,
            evidence TEXT, status TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS notes(key TEXT PRIMARY KEY, data TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT, action TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS directories(path TEXT PRIMARY KEY, updated TEXT);
        CREATE TABLE IF NOT EXISTS conversations(id INTEGER PRIMARY KEY, scope TEXT,
            question TEXT, response TEXT, sources TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS case_briefs(id INTEGER PRIMARY KEY, matter TEXT,
            version INTEGER, data TEXT, sources TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS case_facts(id TEXT PRIMARY KEY, matter TEXT,
            category TEXT, text TEXT, status TEXT, sources TEXT, created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS attachment_reviews(mail_key TEXT PRIMARY KEY, data TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS deadline_proposals(id TEXT PRIMARY KEY, mail_key TEXT,
            matter TEXT, data TEXT, status TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, matter TEXT, title TEXT,
            due TEXT, status TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS manual_drafts(mail_key TEXT PRIMARY KEY, data TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY, mail_key TEXT, matter TEXT,
            category TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS work_items(mail_key TEXT PRIMARY KEY, state TEXT,
            source_status TEXT, source_reason TEXT, matter TEXT, subject TEXT, sender TEXT,
            received TEXT, updated TEXT, resolved TEXT);
        CREATE TABLE IF NOT EXISTS assistant_threads(id TEXT PRIMARY KEY, scope TEXT,
            matter TEXT, mail_key TEXT, title TEXT, created TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS assistant_messages(id INTEGER PRIMARY KEY, thread_id TEXT,
            role TEXT, content TEXT, status TEXT, sources TEXT, job_id INTEGER,
            created TEXT, updated TEXT);
        CREATE INDEX IF NOT EXISTS work_items_state ON work_items(state,updated);
        CREATE INDEX IF NOT EXISTS assistant_messages_thread ON assistant_messages(thread_id,id);
        ''')
        columns={row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        if 'priority' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 50')
            for kind in JOBS:
                self.db.execute('UPDATE jobs SET priority=? WHERE kind=?',(job_priority(kind),kind))
        self.db.commit()
        from .migrations import apply as apply_migrations
        apply_migrations(self)
        for role in ('fast','complex','control'):
            chosen=self.settings('model:role:'+role,'')
            if chosen:self.c.setdefault('model_routing',{})[role+'_model']=chosen
        from .ai_gateway import load_runtime_settings
        load_runtime_settings(self)
        from .extensions364 import load_runtime_settings as load_extension_settings
        load_extension_settings(self)
        from .legal_memory import ensure_schema
        ensure_schema(self)
        from .metier500 import ensure_schema as ensure_metier500_schema   # 5.0.0 : tables additives
        ensure_metier500_schema(self)
        from .strategic import ensure_schema as ensure_strategic_schema
        ensure_strategic_schema(self)
        from .proactive import ensure_schema as ensure_proactive_schema
        ensure_proactive_schema(self)
        from .supervision import ensure_schema as ensure_supervision_schema
        ensure_supervision_schema(self)
        from .portfolio import ensure_schema as ensure_portfolio_schema
        ensure_portfolio_schema(self)
        from .workplan import ensure_schema as ensure_workplan_schema
        ensure_workplan_schema(self)
        from .document_projects import ensure_schema as ensure_document_projects_schema
        ensure_document_projects_schema(self)
        from .autonomy import ensure_schema as ensure_autonomy_schema
        ensure_autonomy_schema(self)
        from .legal_research import ensure_schema as ensure_legal_research_schema
        ensure_legal_research_schema(self)
        from .hearing import ensure_schema as ensure_hearing_schema
        ensure_hearing_schema(self)
        from .word_legal import ensure_schema as ensure_word_schema
        ensure_word_schema(self)
        from .orchestrator import ensure_schema as ensure_orchestrator_schema
        ensure_orchestrator_schema(self)
        from .opinions import ensure_schema as ensure_opinion_schema
        ensure_opinion_schema(self)
        from .learning410 import ensure_schema as ensure_learning410_schema
        ensure_learning410_schema(self)
        from .evaluation410 import ensure_schema as ensure_evaluation410_schema
        ensure_evaluation410_schema(self)
        from .cabinet_pilot import ensure_schema as ensure_cabinet_pilot_schema
        ensure_cabinet_pilot_schema(self)
        from .workstation import ensure_schema as ensure_workstation_schema
        ensure_workstation_schema(self)
        from .cabinet_docs33 import ensure_schema as ensure_cabinet_docs_schema
        ensure_cabinet_docs_schema(self)
        from .proactive34 import ensure_schema as ensure_proactive34_schema
        ensure_proactive34_schema(self)
        from .assistance35 import ensure_schema as ensure_assistance35_schema
        ensure_assistance35_schema(self)
        from .relevance370 import ensure_schema as ensure_relevance370_schema
        ensure_relevance370_schema(self)
        from .operating380 import ensure_schema as ensure_operating380_schema
        ensure_operating380_schema(self)
        from .production390 import ensure_schema as ensure_production390_schema
        ensure_production390_schema(self)
        from .production391 import ensure_schema as ensure_production391_schema
        ensure_production391_schema(self)
        from .reliability393 import ensure_schema as ensure_reliability393_schema
        ensure_reliability393_schema(self)
        from .hybrid400 import ensure_schema as ensure_hybrid400_schema
        ensure_hybrid400_schema(self)
        from .production420 import ensure_schema as ensure_production420_schema
        ensure_production420_schema(self)

    def now(self):return now()

    def enqueue(self, kind, args=None, priority=None):
        if kind not in JOBS:
            raise Stop('action_inconnue')
        if args and args.get('matter'):
            from . import conflicts500
            if kind in conflicts500.GATED_KINDS:
                conflicts500.gate(self, str(args['matter']))
        raw = json.dumps(args or {}, sort_keys=True)
        maximum=120000 if kind=='prepare_transcript_report' else (60000 if kind in ('prepare_document_project','prepare_hearing','prepare_word_project','prepare_legal_opinion','coach_hearing35','record_call35') else (24000 if kind in ('propose_work_plan','prepare_cabinet_letter') else
          (120000 if kind=='import_mcp_legal_results' else
          (16000 if kind in ('legal_research','verify_official_decision') else 8000))))
        if len(raw)>maximum:
            raise Stop('action_trop_longue')
        if not self.db.in_transaction:self.db.execute('BEGIN IMMEDIATE')
        old = self.db.execute("SELECT id FROM jobs WHERE kind=? AND args=? AND status IN ('pending','running','cancel_requested')", (kind,raw)).fetchone()
        if old:
            self.db.commit()
            return old[0]
        if self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]>=100:
            self.db.rollback()
            raise Stop('file_attente_pleine')
        priority=job_priority(kind) if priority is None else max(0,min(int(priority),100))
        cur = self.db.execute('''INSERT INTO jobs
            (kind,args,status,created,finished,result,priority) VALUES (?,?,?, ?,NULL,NULL,?)''',
            (kind,raw,'pending',now(),priority))
        self.db.commit()
        from .live430 import emit
        emit(self,'queued','Demande enregistrée ; traitement mis en attente.',cur.lastrowid,
          str((args or {}).get('matter','')),str((args or {}).get('key','')))
        if kind in ('prepare_reply','prepare_draft','deposit_draft') and args and args.get('key'):
            try:
                from .integration import set_work_state
                set_work_state(self,args['key'],'drafting','demande_utilisateur')
            except Stop:
                pass
        elif kind in ('associate','approve') and args and args.get('mail_key'):
            try:
                from .integration import set_work_state
                set_work_state(self,args['mail_key'],'processing','association_confirmee')
            except Stop:
                pass
        return cur.lastrowid

    def cancel_job(self, job_id):
        try:job_id=int(job_id)
        except (TypeError,ValueError):raise Stop('operation_invalide') from None
        row=self.db.execute('SELECT status,kind FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise Stop('operation_absente')
        stamp=now()
        if row['status']=='pending':
            self.db.execute("UPDATE jobs SET status='cancelled',finished=?,result=? WHERE id=?",
                (stamp,json.dumps({'message':'Opération annulée avant son démarrage.'}),job_id))
            state='cancelled'
        elif row['status']=='running':
            self.db.execute("UPDATE jobs SET status='cancel_requested',result=? WHERE id=?",
                (json.dumps({'message':'Annulation demandée ; le lot en cours va se terminer sans être relancé.'}),job_id))
            state='cancel_requested'
        elif row['status']=='cancel_requested':state='cancel_requested'
        else:raise Stop('operation_deja_terminee')
        self.db.commit();self.audit('operation_annulee',{'job':job_id,'kind':row['kind'],'state':state})
        from .live430 import emit
        emit(self,'cancelled','Annulation enregistrée ; un travail déjà déposé reste conservé.',job_id)
        return {'job_id':job_id,'status':state}

    def audit(self, action, data):
        stamp=now()
        self.db.execute('INSERT INTO audit VALUES(NULL,?,?,?)',(stamp,action,json.dumps(data)))
        from .cabinet_pilot import append_audit
        append_audit(self,stamp,action,data)
        self.db.commit()

    def settings(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
        self.db.commit()

    def propose(self, matter, email, evidence):
        try:
            email = email_value(email)
        except Stop:
            return
        if email in {x.lower() for x in self.c['mail']['own_addresses']}:
            return
        if any(p['email'].lower()==email for p in matter.get('correspondents', [])):
            return
        key = digest(matter['path']+'|'+email)
        old = self.db.execute('SELECT evidence,status FROM proposals WHERE key=?',(key,)).fetchone()
        proofs = json.loads(old[0]) if old else []
        if evidence not in proofs:
            proofs = (proofs+[evidence])[-8:]
        status = old[1] if old else 'pending'
        self.db.execute('INSERT OR REPLACE INTO proposals VALUES (?,?,?,?,?,?)',
                        (key,matter['id'],email,json.dumps(proofs),status,now()))
        self.db.commit()

    def discover(self, dav):
        from .workspace import discover
        return discover(self, dav)

    def scan_mail(self, box, matters):
        cutoff = datetime.now(timezone.utc)-timedelta(days=90)
        total=0
        for folder in dict.fromkeys([self.c['mail']['inbox'], self.c['mail']['sent']]):
            uids=box.search(folder,'UNDELETED','SINCE',cutoff.strftime('%d-%b-%Y'))
            for uid in uids[-80:]:
                mail=box.fetch(folder,uid,headers_only=True)
                if mail.msg.get('List-Id') or mail.msg.get('List-Unsubscribe') or mail.msg.get('Auto-Submitted','no').lower()!='no':
                    continue
                subject=fold(mail.subject)
                targets=set(addresses(mail.msg.get('To',''))+addresses(mail.msg.get('Cc',''))+[mail.sender])
                for matter in matters:
                    refs=[matter['id']]+matter.get('references',[])
                    hits=[r for r in refs if len(r)>=4 and re.search(r'(?<!\w)'+re.escape(fold(r))+r'(?!\w)',subject)]
                    client=fold(matter.get('client_name',''))
                    if not hits and len(client)>=6 and client in subject:
                        hits=[matter['client_name']]
                    if not hits:
                        continue
                    for email in targets:
                        self.propose(matter,email,{'type':'courriel','source':mail.subject,
                            'date':mail.timestamp.isoformat(),'folder':folder,'uid':uid,
                            'indice':'Objet contenant : '+', '.join(hits[:3]),
                            'limite':'Présence dans un échange ; rôle à confirmer.'})
                total+=1
        return total

    def scan_documents(self, dav, matters):
        # Rotate through real matter folders; a container is never a single case.
        allowed={m['id'] for m in indexable_matters(self.c)}
        cases=[m for m in matters if m['id'] in allowed and
               (m.get('correspondents') or re.search(r'\d{8,12}$',m['path']))]
        cases.sort(key=lambda m:(not bool(m.get('correspondents')),m['id']))
        if not cases:
            return {'dossiers_parcourus':0}
        cursor=self.settings('document_cursor',0)%len(cases)
        selected=(cases+cases)[cursor:cursor+min(3,len(cases))]
        index=DocumentIndex(self.c['state_dir'],self.c.get('rag'),self.c.get('ollama')); done=0; failures=0
        for matter in selected:
            try:
                index.sync_page(dav,matter,self.c['documents'],budget=25)
            except Stop:
                failures+=1
                continue
            for row in index.db.execute("SELECT path,modified,text FROM docs WHERE matter=? AND error='' AND text<>''",(matter['id'],)):
                path,modified,text=row
                for hit in list(re.finditer(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',text))[:30]:
                    snippet=text[max(0,hit.start()-100):hit.end()+100]
                    self.propose(matter,hit[0],{'type':'document','source':path,'date':modified,
                        'indice':snippet,'limite':'Adresse présente dans une pièce ; rôle à confirmer.'})
            done+=1
        self.setting('document_cursor',(cursor+len(selected))%len(cases))
        return {'dossiers_parcourus':done,'dossiers_total':len(cases),'dossiers_en_erreur':failures}

    def associate(self, args):
        if args.get('proposal') and not self.db.execute("SELECT 1 FROM proposals WHERE key=? AND status='pending'",(args['proposal'],)).fetchone():
            raise Stop('proposition_absente_ou_deja_traitee')
        matters=load_matters(self.c)
        matter=next((m for m in matters if m['id']==args.get('matter')),None)
        if not matter:
            raise Stop('dossier_absent')
        email=email_value(args.get('email',''))
        role=args.get('role','')
        if role not in ROLES:
            raise Stop('role_a_choisir')
        if email in {x.lower() for x in self.c['mail']['own_addresses']}:
            raise Stop('adresse_du_cabinet')
        previous=[p for p in matter.get('correspondents',[]) if p['email'].lower()==email]
        matter['correspondents']=[p for p in matter.get('correspondents',[]) if p['email'].lower()!=email]+[{'email':email,'role':role}]
        references=[x.strip() for x in args.get('references','').split(',') if x.strip()]
        if len(references)>20 or any(len(x)>120 for x in references):
            raise Stop('references_trop_longues')
        matter['references']=list(dict.fromkeys(matter.get('references',[])+references))
        save_matter(self.c,matter)
        feedback_path=Path(self.c['state_dir'])/'match-feedback.json'
        if feedback_path.exists():
            try:blocked=json.loads(feedback_path.read_text())
            except (ValueError,OSError):blocked=[]
            kept=[x for x in blocked if (x.get('sender'),x.get('matter'))!=(email,matter['id'])]
            if kept!=blocked:private_json(feedback_path,kept)
        if args.get('proposal'):
            self.db.execute("UPDATE proposals SET status='accepted',updated=? WHERE key=?",(now(),args['proposal']))
            self.db.commit()
        self.audit('association_confirmee',{'matter':matter['id'],'email':email,'role':role,'previous':previous})
        # One confirmation applies to the whole correspondent/matter group. All
        # still-unread related reports are reprocessed without another click.
        self.enqueue('retry_matter',{'matter':matter['id']})
        try:
            from .portfolio import ensure_schema as ensure_portfolio_schema
            ensure_portfolio_schema(self)
            gid=digest(matter['id']+'|'+email)
            self.db.execute("UPDATE association_groups SET status='confirmed',updated=? WHERE id=?",
                            (now(),gid))
            self.db.execute("UPDATE portfolio_mail_links SET status='confirmed',confidence=100,updated=? WHERE matter=? AND (sender=? OR instr(recipients,?)>0)",
                            (now(),matter['id'],email,email))
            self.db.commit()
            from .portfolio import matter_status
            matter_status(self,matter)
        except sqlite3.Error:
            pass
        if args.get('mail_key'):
            try:
                from .integration import set_work_state
                set_work_state(self,args['mail_key'],'processing','association_confirmee')
            except Stop:pass
        return {'association':'enregistree','dossier':matter['id'],'action_suivante':'Relancer les non lus de ce dossier'}

    def retry(self, keys, box):
        state=State(self.c['state_dir']); count=0; excluded=0
        account=self.c['mail']['username']+'@'+self.c['mail']['host']
        for key in keys[:100]:
            report=report_for(self.c,key)
            row=state.get(key)
            if not row or row[0] not in ('review','observed','error','ignored'):
                excluded+=1; continue
            mail=box.fetch(report['source_mailbox'],report['source_uid'],headers_only=True)
            if mail.key(account)!=key or exclusion(mail,self.c['mail']):
                excluded+=1; continue
            count+=state.reset_review(key)
        # Save this result before enqueueing the run, so the user can see which
        # messages were eligible. Engine will repeat all unread/identity checks.
        return {'messages_remis_en_attente':count,'lus_ou_exclus':excluded}

    def internal_review(self, key, box, model):
        report=report_for(self.c,key)
        account=self.c['mail']['username']+'@'+self.c['mail']['host']
        mail=box.fetch(report['source_mailbox'],report['source_uid'])
        if mail.key(account)!=key:
            raise Stop('identite_imap_modifiee')
        # An internal note never opens another case from an uncertain association.
        payload={'incoming':mail.public(),'reason':report.get('reason',''),
                 'sources':[{'id':'incoming','content_ref':'incoming'}],
                 'limites':'Courriel seulement. Pièces jointes, agenda et dossier non analysés pour cette note.'}
        if len(mail.text)>self.c['ollama'].get('max_context_chars',90000)-4000:
            raise Stop('courriel_trop_long_pour_note_interne')
        result=model.ask('desk_review',payload)
        from .model import DESK_REVIEW, validate
        validate(result,DESK_REVIEW)
        if any(x!='incoming' for x in result['source_ids']):
            raise Stop('source_note_invalide')
        data={'result':result,'created_at':now(),'source_subject':mail.subject,
              'source_date':mail.timestamp.isoformat(), 'source_hash':digest(mail.text),
              'scope':'Courriel uniquement ; aucune pièce jointe ni recherche documentaire.',
              'warning':'Projet interne non validé. Relire avant toute utilisation.'}
        self.db.execute('INSERT OR REPLACE INTO notes VALUES (?,?,?)',(key,json.dumps(data),now()))
        self.db.commit()
        return {'note_interne':'disponible','brouillon_imap_cree':False}

    def perform(self, kind, args):
        if kind=='search_index490':
            from .search490 import job
            return job(self,args)
        if kind=='analyze_notice440':
            from .notices440 import analyze
            return analyze(self,args)
        if kind=='extract_facts460':
            from .facts460 import scan_matter
            return scan_matter(self,args)
        if kind=='analyze_deadline450':
            from .echeances450 import analyze
            return analyze(self,args)
        if kind in {'verify_deliverable420','retry_deliverable420','studio_prepare420',
                    'advance_matter420','snapshot_metrics420'}:
            from .production420 import perform
            return perform(self,kind,args)
        if kind=='run_legal_benchmark410':
            from .evaluation410 import perform
            return perform(self,kind,args)
        if kind in {'production_cycle391','advance_playbooks391','retry_production391',
                    'review_output391','universal_command391'}:
            from .production391 import perform
            return perform(self,kind,args)
        if kind in {'production_cycle390','advance_playbooks390'}:
            from .production390 import perform
            return perform(self,kind,args)
        if kind=='set_learning_rule392':
            from .learning392 import set_correction_state
            return set_correction_state(self,args.get('correction_id',''),args.get('enabled','no'))
        if kind in {'review_action380','refresh_matter_graph380','start_playbook380',
                    'advance_playbook380','complete_playbook_step380',
                    'save_ecosystem_service380','prepare_ecosystem_action380',
                    'run_business_evaluation380'}:
            from .operating380 import perform
            return perform(self,kind,args)
        if kind in {'save_mail_rule370','delete_mail_rule370','record_correction370','set_automation_level370'}:
            from .relevance370 import save_rule,delete_rule,record_correction,set_automation_level
            if kind=='save_mail_rule370':
                return save_rule(self,args.get('name',''),args.get('priority',100),args.get('field',''),
                  args.get('operator',''),args.get('value',''),args.get('rule_action',''),args.get('enabled','yes')=='yes')
            if kind=='delete_mail_rule370':
                return delete_rule(self,args.get('rule_id',''),args.get('confirm',''))
            if kind=='record_correction370':
                return record_correction(self,args.get('scope',''),args.get('matter',''),
                  args.get('source_kind',''),args.get('original',''),args.get('corrected',''),args.get('guidance',''))
            return set_automation_level(self,args.get('level',''))
        if kind in {'coach_hearing35','prepare_call35','record_call35','calculate35',
                    'billing_review35','classify_comparable35'}:
            from .assistance35 import perform
            return perform(self,kind,args)
        if kind in ('proactive34_cycle','proactive34_briefing','proactive34_now'):
            from .proactive34 import perform
            return perform(self,kind,args)
        if kind=='assistant_answer':
            from .integration import answer_question
            return answer_question(self,args)
        if kind in {'refresh_brief','validate_fact','pin_fact','archive_fact','attachment_review',
                    'deadline_review','confirm_event','confirm_task','ignore_deadline','prepare_draft',
                    'prepare_reply','deposit_draft','feedback','add_rule','mark_handled','execute_actions'}:
            from .intelligence import perform
            return perform(self,kind,args)
        if kind in {'sync_legal_memory','validate_memory','pin_memory','dispute_memory',
                    'archive_memory','resolve_conflict'}:
            from .legal_memory import perform
            return perform(self,kind,args)
        if kind in {'analyze_strategy','build_matrix','draft_act','validate_strategy',
                    'archive_strategy','validate_matrix_row','dispute_matrix_row',
                    'archive_matrix_row','validate_act','archive_act'}:
            from .strategic import perform
            return perform(self,kind,args)
        if kind in {'monitor_matter','monitor_all','build_daily_dashboard',
                    'ack_signal','snooze_signal','resolve_signal'}:
            from .proactive import perform
            return perform(self,kind,args)
        if kind in {'organize_cabinet','classify_portfolio','reconcile_inbox','set_matter_state','confirm_matter','reject_group'}:
            from .portfolio import perform
            return perform(self,kind,args)
        if kind in {'propose_work_plan','sync_caldav_tasks','apply_work_plan','update_work_task','edit_work_task','schedule_work_task',
                    'create_agenda_event','edit_agenda_event','cancel_agenda_event'}:
            from .workplan import perform
            return perform(self,kind,args)
        if kind=='style550_scan':
            from .style550 import perform
            return perform(self,kind,args)
        if kind=='deck530_sync':
            from .deck530 import perform
            return perform(self,kind,args)
        if kind=='docrequest520':
            from .docrequest520 import perform
            return perform(self,kind,args)
        if kind=='routine520':
            from .routines520 import perform
            return perform(self,kind,args)
        if kind in {'edit_personal_event','edit_personal_task'}:
            from .agenda520 import perform
            return perform(self,kind,args)
        if kind in {'pieces_scan510','pieces_create510'}:
            from .pieces510 import perform
            return perform(self,kind,args)
        if kind in {'prepare_document_project','create_document_files'}:
            from .document_projects import perform
            return perform(self,kind,args)
        if kind in {'autonomy_mail_sweep','refresh_operational_memory','review_autonomy_proposal'}:
            from .autonomy import perform
            return perform(self,kind,args)
        if kind in {'legal_research','import_mcp_legal_results','verify_official_decision','identify_latest_writings','refresh_exhibit_registry'}:
            from .legal_research import perform
            return perform(self,kind,args)
        if kind in {'identify_party_writings','compare_devices','prepare_hearing','create_hearing_files'}:
            from .hearing import perform
            return perform(self,kind,args)
        if kind in {'prepare_word_project','create_word_files'}:
            from .word_legal import perform
            return perform(self,kind,args)
        if kind in {'prepare_cabinet_letter','prepare_cabinet_revision','create_cabinet_letter'}:
            from .cabinet_docs33 import prepare,confirm
            if kind=='create_cabinet_letter':return confirm(self,args)
            return prepare(self,{**args,'kind':'revision' if kind=='prepare_cabinet_revision' else 'letter'})
        if kind in {'orchestrate_mail','orchestrator_mail_sweep','review_orchestration_notification'}:
            from .orchestrator import perform
            return perform(self,kind,args)
        if kind in {'prepare_legal_opinion','review_legal_opinion'}:
            from .opinions import perform
            return perform(self,kind,args)
        if kind in {'refresh_cabinet_pilotage','prepare_meeting','prepare_transcript_report',
                    'review_cabinet_decision','create_cabinet_confirmation_batch',
                    'approve_cabinet_confirmation_batch','record_provision',
                    'run_continuous_business_tests'}:
            from .cabinet_pilot import perform
            return perform(self,kind,args)
        if kind=='refresh_unpaid_invoices':
            from .workstation import refresh_unpaid_invoices
            return refresh_unpaid_invoices(self)
        if kind in ('memory_insight','memory_scope'):
            from .memory import SentMemory
            return SentMemory(self.c).manage(kind,args)
        if kind in ('health','daily_digest','automation_setting'):
            from .operations import perform
            return perform(self,kind,args)
        if kind=='index_all':
            from .portfolio import portfolio_rows
            active={row['matter'] for row in portfolio_rows(self,['active'],limit=5000)}
            cases=sorted((m for m in load_matters(self.c) if m['id'] in active),
                         key=lambda m:m['id'])
            remaining=[m for m in cases if m['id']>args.get('after','')]
            pending=self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            selected=remaining[:max(0,min(10,98-pending))]
            for m in selected:self.enqueue('index',{'matter':m['id']})
            return {'dossiers_mis_en_attente':len(selected),
                    'suite':len(remaining)>len(selected),
                    'after':selected[-1]['id'] if selected else args.get('after','')}
        if kind=='memory_all':
            from .legal_memory import ensure_schema
            ensure_schema(self)
            index=DocumentIndex(self.c['state_dir'])
            cases=sorted(load_matters(self.c),key=lambda m:m['id'])
            remaining=[]
            for m in cases:
                if m['id']<=args.get('after',''):continue
                indexed=index.db.execute('SELECT 1 FROM knowledge_chunks WHERE matter=? LIMIT 1',(m['id'],)).fetchone()
                known=self.db.execute('SELECT 1 FROM legal_memory_records WHERE matter=? LIMIT 1',(m['id'],)).fetchone()
                if indexed and not known:remaining.append(m)
            pending=self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            selected=remaining[:max(0,min(5,98-pending))]
            for m in selected:self.enqueue('refresh_brief',{'matter':m['id']})
            return {'dossiers_mis_en_attente':len(selected),'suite':len(remaining)>len(selected),
                    'after':selected[-1]['id'] if selected else args.get('after','')}
        if kind in ('discover','browse','register_matter','create_matter','chat','forget_chat'):
            from .workspace import perform
            result=perform(self,kind,args)
            if kind in ('register_matter','create_matter','discover'):
                try:
                    from . import conflicts500
                    # Seul un dossier ouvert par l'avocat est soumis à la recherche de conflits bloquante ;
                    # les dossiers découverts dans Nextcloud sont enregistrés sans rien bloquer.
                    conflicts500.scan_new(self,gated=kind!='discover')
                except Exception as ex:  # la recherche de conflits ne doit jamais faire échouer l'ouverture du dossier
                    self.audit('conflits_500_scan_non_execute',{'reason':str(ex)[:100]})
            return result
        if kind in ('associate','approve'):
            return self.associate(args)
        if kind=='reject':
            self.db.execute("UPDATE proposals SET status='rejected' WHERE key=?",(args['key'],));self.db.commit()
            return {'proposition':'rejetee'}
        if kind=='remove_contact':
            if args.get('confirm')!='yes':raise Stop('confirmation_retrait_requise')
            matter=next((m for m in load_matters(self.c) if m['id']==args.get('matter')),None)
            if not matter:raise Stop('dossier_absent')
            email=email_value(args.get('email',''))
            before=matter.get('correspondents',[])
            if not any(p['email'].lower()==email for p in before):raise Stop('correspondant_absent')
            matter['correspondents']=[p for p in before if p['email'].lower()!=email]
            save_matter(self.c,matter);self.audit(kind,{'matter':matter['id'],'email':email})
            return {'correspondant':'retire_du_registre_web'}
        if kind=='forget':
            from .memory import SentMemory
            SentMemory(self.c).forget(args['key']);self.audit(kind,args)
            return {'exemple':'oublie'}
        if kind=='index':
            matter=next(m for m in load_matters(self.c) if m['id']==args['matter'])
            index=DocumentIndex(self.c['state_dir'],self.c.get('rag'),self.c.get('ollama'))
            before=index.db.execute('SELECT MAX(indexed_at) FROM knowledge_chunks WHERE matter=?',(matter['id'],)).fetchone()[0]
            items,stale=index.sync_page(DAV(self.c['nextcloud']),matter,self.c['documents'],100)
            embedding=index.ensure_embeddings(matter)
            after=index.db.execute('SELECT MAX(indexed_at) FROM knowledge_chunks WHERE matter=?',(matter['id'],)).fetchone()[0]
            return {'fichiers':len(items),'en_attente':len(stale),'embeddings':embedding,
                    'contenu_modifie':before!=after,'inventaire':index.last_page_state}
        if kind=='sync':
            dav=DAV(self.c['nextcloud']);result=self.discover(dav)
            matters=load_matters(self.c)
            box=Mailbox(self.c['mail'])
            try: result['courriels_parcourus']=self.scan_mail(box,matters)
            finally: box.close()
            result['documents']=self.scan_documents(dav,matters)
            self.setting('last_sync',{'at':now(),**result})
            return result
        box=Mailbox(self.c['mail'])
        try:
            if kind=='learn':
                from .memory import SentMemory
                if not self.c.get('memory',{}).get('enabled'):
                    raise Stop('memoire_desactivee')
                return SentMemory(self.c).collect(box,load_matters(self.c))
            if kind=='retry':
                return self.retry([args['key']],box)
            if kind=='retry_matter':
                matter=next(m for m in load_matters(self.c) if m['id']==args['matter'])
                emails={p['email'] for p in matter['correspondents']}
                keys=[]
                for key,status,_,_ in State(self.c['state_dir']).rows(500):
                    if status not in ('review','observed','error'):
                        continue
                    try: report=report_for(self.c,key)
                    except Stop: continue
                    if report.get('matter')==matter['id'] or report.get('sender') in emails:
                        keys.append(key)
                return self.retry(keys,box)
            if kind=='review':
                from .model import Model,routed_config
                return self.internal_review(args['key'],box,Model(routed_config(self.c,'mail_drafting')))
        finally:
            box.close()
        raise Stop('action_inconnue')

    def work_once(self):
        # User-visible replies always take precedence over maintenance. A large
        # discovery/index backlog must never postpone a requested draft.
        if not self.db.in_transaction:self.db.execute('BEGIN IMMEDIATE')
        # A second worker must not spin on a draft while the first holds the
        # write lock: read-only surveillance can still progress independently.
        busy=False
        with open(Path(self.c['state_dir'])/'run.lock','a') as probe:
            try:fcntl.flock(probe,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:busy=True
        row=self.db.execute("SELECT * FROM jobs WHERE status='pending'"+
          (" AND kind IN ('live_calendar430','live_documents430','automation_setting')" if busy else '')+
          ' ORDER BY '+PICK_ORDER+' LIMIT 1').fetchone()
        if not row:
            self.db.commit()
            return False
        worker_id=getattr(self,'worker_id','manual')
        self.db.execute("UPDATE jobs SET status='running',started=?,worker=?,attempts=attempts+1 WHERE id=? AND status='pending'",(now(),worker_id,row['id']))
        self.db.commit()
        self.active_job_id=row['id']
        try:
            _args=json.loads(row['args'] or '{}');_args=_args if isinstance(_args,dict) else {}
        except (ValueError,TypeError):_args={}
        trace480.begin(row['id'],row['kind'],_args.get('matter') or _args.get('matter_id') or '')
        try:
            from .live430 import progress
            progress(self,'Traitement démarré')
            self.audit('job_started',{'job_id':row['id'],'kind':row['kind'],
              'args_sha256':digest(row['args'])})
            if row['kind'] in ('run','live_mail430'):
                from .engine import Engine
                engine=Engine(self.c)
                engine.activity=lambda message,matter='',key='':progress(self,message,matter,key)
                def record_mail(key,report):
                    from .activity431 import mail_outcome
                    mail_outcome(self,key,report)
                    if report.get('status')!='drafted' or not report.get('draft_verified'):return
                    from .production420 import record_job
                    did=record_job(self,{'id':row['id'],'kind':'prepare_reply'},
                      {'key':key,'matter':report.get('matter') or '', 'trigger_kind':'incoming_mail'},'done',
                      {'brouillon_imap':'verifie','draft_verified':report.get('draft_verified',{}),
                       'dossier':self.c['mail']['drafts'],'source_ids':report.get('proposal',{}).get('source_ids',[])})
                    from .live430 import emit
                    out=self.db.execute('SELECT business_message FROM production_deliverables_v420 WHERE id=?',(did,)).fetchone()
                    emit(self,'produced',out[0],row['id'],report.get('matter') or '',key)
                engine.outcome=record_mail
                result=engine.run()
                if row['kind']=='live_mail430':
                    from .live430 import heartbeat
                    heartbeat(self,'courriels','active',str(result.get('examined',0))+' courriels examinés',time.time()+300)
                if result.get('busy'):
                    self.db.execute("UPDATE jobs SET status='pending' WHERE id=?",(row['id'],));self.db.commit()
                    self.active_job_id=None;trace480.end()
                    return False
            elif row['kind'] in ('live_calendar430','live_documents430'):
                from .watch430 import perform
                result=perform(self,row['kind'],json.loads(row['args']))
            elif row['kind']=='automation_setting':
                # 5.2.1 : simple réglage local, appliqué même si une analyse tient le verrou.
                result=self.perform(row['kind'],json.loads(row['args']))
            else:
                from .queue521 import note_holder
                with run_lock(self.c):
                    note_holder(self.c['state_dir'],'Travail n° %s (%s)'%(row['id'],row['kind']))
                    result=self.perform(row['kind'],json.loads(row['args']))
            if isinstance(result,dict):
                try:
                    from .relevance370 import review_result
                    control=review_result(self,row['id'],row['kind'],result)
                    if control:result={**result,'quality_control':control}
                except Stop as control_error:
                    result={**result,'quality_control':{'status':'unavailable',
                      'recommendation':'unavailable','error':str(control_error)}}
            status='done'
        except Stop as e:
            if str(e)=='traitement_deja_en_cours':
                self.db.execute("UPDATE jobs SET status='pending' WHERE id=?",(row['id'],));self.db.commit()
                self.active_job_id=None;trace480.end()
                return False
            result={'erreur':str(e)};status='error'
        except Exception:
            result={'erreur':'action_interrompue_consulter_le_journal'};status='error'
        current=self.db.execute('SELECT status FROM jobs WHERE id=?',(row['id'],)).fetchone()[0]
        if current=='cancel_requested':
            status='cancelled';result={'message':'Opération annulée après le lot en cours.'}
        self.db.execute('UPDATE jobs SET status=?,finished=?,result=? WHERE id=?',
                        (status,now(),json.dumps(result),row['id']))
        if row['kind']=='proactive34_cycle' and status!='done':
            self.db.execute("UPDATE proactive_cycles_v340 SET state='error',finished=? WHERE job_id=?",
                            (now(),row['id']))
        self.db.execute('UPDATE supervision_requests SET result=? WHERE job_id=?',
                        (json.dumps({'job_status':status,'result':result},ensure_ascii=False),row['id']))
        if row['kind']=='chat':
            # Conversation text belongs only to the forgettable conversation table.
            parameters=json.loads(row['args']);parameters.pop('question',None)
            self.db.execute('UPDATE jobs SET args=? WHERE id=?',(json.dumps(parameters),row['id']))
        if row['kind']=='coach_hearing35':
            # The coaching report retains only the transcript hash and observations.
            self.db.execute('UPDATE jobs SET args=? WHERE id=?',('{}',row['id']))
        self.db.commit()
        self.audit('job_finished',{'job_id':row['id'],'kind':row['kind'],'status':status,
          'result_sha256':digest(json.dumps(result,sort_keys=True,ensure_ascii=False))})
        try:
            from .production391 import after_job
            after_job(self,row,json.loads(row['args']),status,result)
        except (Stop,OSError,sqlite3.Error,ValueError,TypeError,KeyError):
            # The production register must never hide or change the original
            # job outcome. Its own diagnostics will expose a missing projection.
            pass
        try:
            from .production420 import record_job,mark_verification_error
            parameters=json.loads(row['args'])
            record_job(self,row,parameters,status,result)
            if row['kind']=='verify_deliverable420' and status=='error':
                mark_verification_error(self,parameters.get('deliverable_id',''),result.get('erreur','action_interrompue'))
        except (Stop,OSError,sqlite3.Error,ValueError,TypeError,KeyError):
            # As in the 3.9 register, a projection failure never changes the job.
            pass
        try:
            from .integration import sync_work_items,set_work_state
            sync_work_items(self)
            parameters=json.loads(row['args'])
            if row['kind'] in ('prepare_reply','prepare_draft') and parameters.get('key'):
                if status=='error':set_work_state(self,parameters['key'],'needs_action','echec_preparation')
                elif result.get('projet_prepare') and not result.get('depot_autorise'):
                    set_work_state(self,parameters['key'],'needs_confirmation','decision_requise')
        except (Stop,OSError,sqlite3.Error):pass
        from .live430 import emit
        outcome=self.db.execute('SELECT status,business_message FROM production_deliverables_v420 WHERE job_id=? ORDER BY updated DESC LIMIT 1',(row['id'],)).fetchone()
        message=outcome['business_message'] if outcome else ('Traitement terminé ; résultat disponible.' if status=='done' else 'Traitement interrompu ; consultez le motif et relancez après contrôle.')
        emit(self,'produced' if outcome and outcome['status']=='verified' and row['kind'] not in ('run','live_mail430') else status,message,row['id'])
        self.active_job_id=None;trace480.end()
        if status=='done' and row['kind']=='live_documents430' and result.get('continue_scan'):
            self.enqueue('live_documents430',priority=70)
        if status=='done' and result.get('messages_remis_en_attente',0):
            self.enqueue('run')
        if status=='done' and row['kind']=='index' and result.get('en_attente',0):
            # Continue in bounded batches, allowing other queued jobs between lots.
            self.enqueue('index',json.loads(row['args']))
        if status=='done' and row['kind']=='index' and result.get('contenu_modifie') and not result.get('en_attente'):
            self.enqueue('refresh_brief',json.loads(row['args']))
        if status=='done' and row['kind']=='refresh_brief':
            self.enqueue('sync_legal_memory',json.loads(row['args']))
        autonomy_active=self.settings('automation:autonomy_enabled',
          self.c.get('autonomy',{}).get('enabled',False))
        orchestrator_active=self.settings('automation:orchestrator_enabled',
          self.c.get('orchestrator',{}).get('enabled',False))
        if orchestrator_active and status=='done' and row['kind'] in ('run','sync','live_mail430'):
            self.enqueue('orchestrator_mail_sweep',priority=40)
        elif autonomy_active and status=='done' and row['kind'] in ('run','sync','live_mail430'):
            self.enqueue('autonomy_mail_sweep',priority=40)
        if autonomy_active and status=='done' and row['kind']=='prepare_document_project':
            parameters=json.loads(row['args'])
            if parameters.get('automatic')=='yes':
                from .autonomy import record_automatic_project
                record_automatic_project(self,parameters,result)
            if result.get('matter',{}).get('id'):
                self.enqueue('refresh_operational_memory',{'matter':result['matter']['id']},priority=55)
        if autonomy_active and status=='done' and row['kind'] in ('index','refresh_brief','sync_legal_memory'):
            parameters=json.loads(row['args'])
            if parameters.get('matter'):
                self.enqueue('refresh_operational_memory',{'matter':parameters['matter']},priority=55)
        if (status=='done' and row['kind']=='index' and
            result.get('embeddings',{}).get('pending',0) and
            not result.get('embeddings',{}).get('error')):
            self.enqueue('index',json.loads(row['args']))
        if status=='done' and row['kind'] in ('sync','discover') and result.get('parcours_partiel'):
            self.enqueue('discover')
        if status=='done' and row['kind']=='index_all' and result.get('suite'):
            self.enqueue('index_all',{'after':result['after']})
        if status=='done' and row['kind']=='memory_all' and result.get('suite'):
            self.enqueue('memory_all',{'after':result['after']})
        if status=='done' and row['kind']=='monitor_all' and result.get('suite'):
            self.enqueue('monitor_all',{'after':result['after']})
        if status=='done' and row['kind']=='monitor_all' and not result.get('suite'):
            self.enqueue('build_daily_dashboard')
        if status=='done' and row['kind']=='organize_cabinet' and result.get('suite'):
            self.enqueue('organize_cabinet')
        if status=='done' and row['kind'] in ('organize_cabinet','reconcile_inbox','classify_portfolio') and not result.get('suite'):
            self.enqueue('build_daily_dashboard')
        return True


def worker(c,worker_id='1'):
    # Multiple atomic claimers; mutating producers still share run.lock.
    import threading
    if not re.fullmatch(r'[1-4]',str(worker_id)):raise Stop('worker_invalide')
    lock_name='desk-worker.lock' if str(worker_id)=='1' else 'desk-worker-'+str(worker_id)+'.lock'
    with open(Path(c['state_dir'])/lock_name,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        desk=Desk(c)
        desk.worker_id=str(worker_id)
        recovered=desk.db.execute("UPDATE jobs SET status='error',finished=?,result=? WHERE status IN ('running','cancel_requested') AND (worker=? OR (worker IS NULL AND ?='1'))",
                        (now(),json.dumps({'erreur':'service_redemarre_verifier_avant_relance'}),str(worker_id),str(worker_id))).rowcount
        desk.db.commit()
        if recovered:desk.audit('jobs_interrupted_on_restart',{'count':recovered,'automatic_replay':False})
        stop=threading.Event()
        def pulse():
            db=sqlite3.connect(Path(c['state_dir'])/'desk.sqlite3',timeout=10)
            try:
                while not stop.is_set():
                    try:
                        db.execute('INSERT OR REPLACE INTO live_services_v430 VALUES(?,?,?,?,?)',
                          ('worker-'+str(worker_id),time.time(),'active','Worker disponible ou en production',0));db.commit()
                    except sqlite3.Error:db.rollback()
                    stop.wait(10)
            finally:db.close()
        threading.Thread(target=pulse,daemon=True).start()
        while True:
            if c.get('_path'):
                desk.c=load_config(c['_path'])
                from .ai_gateway import load_runtime_settings
                load_runtime_settings(desk)
                from .extensions364 import load_runtime_settings as load_extensions
                load_extensions(desk)
            from .operations import automation_tick
            try:
                if str(worker_id)=='1':automation_tick(desk)
            except Stop:pass
            desk.work_once()
            time.sleep(3)

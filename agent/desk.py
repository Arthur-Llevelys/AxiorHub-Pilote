"""Browser work queue, evidence-based discovery and explicitly approved registry.

Only the worker executes jobs. No arbitrary commands, SMTP, or automatic roles.
"""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from .portable import fcntl   # 5.6.25 : verrous portables Linux / Windows
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

from .common import (Stop, clean_path, digest, fold, indexable_matters,
                     load_config, load_matters, private_json, under)
from .dav import DAV
from .index import DocumentIndex
from .mailbox import Mailbox, addresses, exclusion
from .state import State
from . import trace480

# 5.6.5 : juridictions, experts, administrations, commissaires de justice, autres parties et contacts personnels.
ROLES = {'client': 'Client', 'confrere_adverse': 'Conseil adverse',
         'tiers': 'Tiers / confrère partenaire', 'prospect': 'Prospect',
         'juridiction': 'Juridiction / greffe', 'expert': 'Expert judiciaire ou amiable',
         'administration': 'Administration / organisme public', 'commissaire_justice': 'Commissaire de justice (huissier)',
         'autre_partie': 'Autre partie (assureur, caution, mandataire…)', 'personnel': 'Contact personnel (hors dossier)'}
# Destinataires à qui rien d'issu du dossier n'est communiqué sans validation de l'avocat (seuls les rendez-vous restent automatiques).
THIRD_PARTY_ROLES = frozenset(('confrere_adverse', 'tiers', 'juridiction', 'expert', 'administration', 'commissaire_justice',
                               'autre_partie', 'personnel'))
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
JOBS |= {'analyze_notice440','analyze_deadline450','extract_facts460','analyser_dossier5624','fiche_dossier5627'}
JOBS |= {'search_index490'}
JOBS |= {'digest_pieces5632','veille5632_collect','briefing_oral5632','mailrules5632'}   # 5.6.32
JOBS |= {'pj_courriel5636','initiative5636','relances5636','outils5636','corporate5636','microsoft5636'}   # 5.6.36 : pièces jointes rangées ; initiatives (analyse, exécution du plan validé)
JOBS |= {'recherche5634'}   # 5.6.34 : recherche juridique par le registre unique des sources
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
JOBS |= {'retry_playbook_step5643','cancel_playbook5643'}   # 5.6.43 (§ 19) : reprise d'une étape, arrêt du parcours
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
JOBS |= {'routine520','docrequest520','deck530_sync','style550_scan','maildraft5613','task5614'}
JOBS |= {'document568_run','document568_scan','document568_compile'}
JOBS |= {'sent568_scan','received568_scan','followup568_prepare','proactive568_cycle','talk568_prepare','news568_collect'}

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
USER_JOBS |= {'routine520','docrequest520','maildraft5613','task5614'}
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
USER_JOBS |= {'retry_playbook_step5643','cancel_playbook5643'}
JOBS |= {'recit5651','relecture5651','routine5652','actualiser5655','crm_collecte5657'}   # 5.6.51 (audit 5.6.50) : rappels rédigés du dossier, relecture Word par propositions
USER_JOBS |= {'recit5651','relecture5651','routine5652'}
JOBS |= {'ranger_notes5666'}   # 5.6.66 : notes internes déposées en brouillon → documents Word des dossiers
USER_JOBS |= {'ranger_notes5666'}
JOBS |= {'maj_local5661'}   # 5.6.61 : mise à jour signée d'un poste (Windows, Ubuntu) depuis la page des versions
USER_JOBS |= {'maj_local5661'}
JOBS |= {'archive_correspondence5643','file_mail_attachments5643'}   # 5.6.43 (§ 10-14) : conservation courriel / pièces (sans IA)
USER_JOBS |= {'archive_correspondence5643','file_mail_attachments5643'}
CONSERVATION_JOBS = ('archive_correspondence5643','file_mail_attachments5643')
# 5.6.65 (R08) : analyses de dossier lancées automatiquement ; au plus 6 en attente, quel que soit le producteur (Desk.admit)
ANALYSIS_FAMILY = frozenset(('index','monitor_matter','extract_facts460'))
ANALYSIS_CAP = 6
# 5.6.65 (audit 5.6.64, R16) : contrôles de santé — sondes en lecture (IMAP, Nextcloud, fournisseurs IA), bornées par leurs propres délais,
# sans génération ; ils ne prennent plus le verrou exclusif de production (une sonde lente retardait toutes les rédactions).
CONTROL_JOBS = ('health',)
# Travaux qui n'attendent jamais le verrou général exclusif : ils ne peuvent pas « réserver » ce verrou (R09).
NON_EXCLUSIVE_JOBS = frozenset(('live_calendar430','live_documents430','automation_setting','run','live_mail430')+CONSERVATION_JOBS+CONTROL_JOBS)
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
NORMAL_JOBS |= {'analyze_notice440','analyze_deadline450','extract_facts460','analyser_dossier5624','fiche_dossier5627'}
NORMAL_JOBS |= {'search_index490'}


AUTOMATIC_PRIORITY=30      # en deçà : demande de l'avocat ; au-delà : travail automatique (surveillance, indexation…)
AUTOMATIC_LIMIT=60         # travaux automatiques en attente, tous types confondus
AUTOMATIC_PER_KIND=8       # par type (ex. indexation) : au-delà, la surveillance repassera au cycle suivant
USER_LIMIT=500             # garde-fou absolu pour les demandes


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
    report = json.loads(p.read_text(encoding='utf-8'))      # 5.6.2 : rapports écrits en UTF-8, relus en UTF-8 quel que soit le système
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


_SCHEMA_READY = set()
_SCHEMA_LOCK = __import__('threading').Lock()


def _locked(error):
    """5.6.44 : base occupée par un autre processus (verrou d'écriture SQLite), y compris derrière une erreur de migration."""
    seen = 0
    while error is not None and seen < 5:
        text = str(error).lower()
        if isinstance(error, sqlite3.OperationalError) and ('locked' in text or 'busy' in text):
            return True
        error, seen = error.__cause__ or error.__context__, seen + 1
    return False


def startup_wait():
    """Attente maximale de la préparation du schéma quand la base est occupée : quelques minutes pour un service (interface, worker,
    veille : AXIORHUB_STARTUP_WAIT, 300 s par défaut), moins pour une page ou une action qui attendent une réponse."""
    from .common import _TRACKED
    mode = getattr(_TRACKED, 'mode', '')
    if mode == 'read':
        return 20
    if mode == 'write':
        return 60
    try:
        return max(0, int(os.environ.get('AXIORHUB_STARTUP_WAIT', '300')))
    except ValueError:
        return 300


def _startup_log(message):
    stream = sys.stderr or sys.__stderr__
    if stream is not None:
        print('AxiorHub : ' + message, file=stream, flush=True)


def _schema_key(c):
    """Base déjà préparée par CE processus (chemin + identité du fichier : une base recréée est préparée de nouveau)."""
    path = Path(c['state_dir']) / 'desk.sqlite3'
    try:
        st = path.stat()
    except OSError:
        return None
    if not st.st_size:
        return None
    return (str(path.resolve()), st.st_ino, st.st_ctime_ns if hasattr(st, 'st_ctime_ns') else 0)


class Desk:
    def __init__(self, c):
        # 5.6.32 : la préparation du schéma (tables, migrations, données par défaut) n'a lieu qu'une fois par processus et par base.
        # Avant, chaque page ouvrait Desk en écrivant (INSERT des parcours par défaut, index) sous un verrou de fichier partagé par
        # l'interface, le worker et la veille : un traitement qui tenait l'écriture SQLite bloquait toutes les pages 10 s, puis
        # « Interface indisponible » (database is locked). Les ouvertures suivantes ne font plus que lire.
        key = _schema_key(c)
        if key is not None and key in _SCHEMA_READY:
            self._open(c)
            self._runtime()
            return
        with _SCHEMA_LOCK:
            key = _schema_key(c)
            if key is not None and key in _SCHEMA_READY:
                self._open(c)
            else:
                self._prepare_waiting(c)
                key = _schema_key(c)
                if key is not None:
                    _SCHEMA_READY.add(key)
        self._runtime()

    def _prepare_waiting(self, c):
        """5.6.44 : si un autre processus tient l'écriture de la base, la préparation est reprise depuis le début après une attente
        croissante (1, 2, 4… 15 s) au lieu d'arrêter le service ; chaque essai est noté dans le journal. Le verrou de fichier du
        schéma est relâché pendant l'attente (un autre processus peut terminer sa propre préparation)."""
        waited, delay, limit = 0.0, 1.0, startup_wait()
        while True:
            try:
                self._prepare_once(c)
                if waited:
                    _startup_log('base de données de nouveau disponible : préparation terminée après %d s d’attente.' % waited)
                return
            except (sqlite3.OperationalError, Stop) as error:
                if not _locked(error):
                    raise
                try:
                    self.db.rollback()
                except (sqlite3.Error, AttributeError):
                    pass
                try:
                    self.db.close()
                except (sqlite3.Error, AttributeError):
                    pass
                if waited >= limit:
                    _startup_log('base de données toujours occupée par un autre processus après %d s ; arrêt (le service sera relancé).' % waited)
                    raise
                pause = min(delay, max(0.1, limit - waited))
                _startup_log('base de données occupée par un autre processus pendant la préparation du démarrage (%s) ; nouvel essai dans '
                             '%d s (attente cumulée %d s, au plus %d s).' % (error.__cause__ or error, max(1, round(pause)), waited, limit))
                time.sleep(pause)
                waited += pause
                delay = min(delay * 2, 15.0)

    def _prepare_once(self, c):
        # 5.6.25 : création et migration du schéma sérialisées entre processus (interface, worker, veille démarrent ensemble sur
        # un profil neuf : sans ce verrou, deux ALTER TABLE simultanés font échouer l'un des services).
        try:
            handle = open(Path(c['state_dir'])/'schema.lock', 'a')
        except OSError:
            handle = None
        try:
            if handle is not None:
                fcntl.flock(handle, fcntl.LOCK_EX)
            self._open(c)
            self._prepare()
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except OSError:
                    pass
                handle.close()

    def _open(self, c):
        self.c = c
        from .common import busy_timeout
        self._busy = busy_timeout()   # 5.6.32 : 2 s pour l'affichage d'une page, 10 s pour les actions et le worker
        from .ecritures5636 import TimedConnection
        self.db = sqlite3.connect(Path(c['state_dir'])/'desk.sqlite3', timeout=self._busy, factory=TimedConnection)
        self.db.state_dir = str(c['state_dir'])   # 5.6.36 : les écritures longues sont consignées (fichier) et nommées dans le diagnostic
        self.db.row_factory = sqlite3.Row
        from .common import track_connection
        track_connection(self)   # 5.6.32 : aucune transaction ne reste ouverte pendant un appel réseau (voir common.HTTP)

    def _runtime(self):
        """Lectures propres à chaque ouverture (réglages chargés dans la configuration de la requête) ; aucune écriture."""
        for role in ('fast','complex','control'):
            chosen=self.settings('model:role:'+role,'')
            if chosen:self.c.setdefault('model_routing',{})[role+'_model']=chosen
        from .ai_gateway import load_runtime_settings
        load_runtime_settings(self)
        from .extensions364 import load_runtime_settings as load_extension_settings
        load_extension_settings(self)

    def _prepare(self):
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
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_kind_finished5662 ON jobs(kind,finished)')   # 5.6.62 : cadence (repos)
        columns={row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        if 'priority' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 50')
            for kind in JOBS:
                self.db.execute('UPDATE jobs SET priority=? WHERE kind=?',(job_priority(kind),kind))
        self.db.commit()
        from .migrations import apply as apply_migrations
        apply_migrations(self)
        if not self.settings('arret5643:matter_ref_backfill',False):   # 5.6.43 (§ 16.1) : dossier normalisé des travaux antérieurs
            self.db.execute("UPDATE jobs SET matter_ref=COALESCE(NULLIF(json_extract(args,'$.matter'),''),NULLIF(json_extract(args,'$.matter_id'),'')) "
                            "WHERE matter_ref IS NULL AND json_valid(args)")
            self.db.commit();self.setting('arret5643:matter_ref_backfill',True)
        if not self.settings('correspondances5644:jobs_matter',False):   # 5.6.44 (audit F03) : une seule fois, puis plus aucune écriture
            for job in self.db.execute("SELECT j.id,l.matter_id FROM jobs j JOIN mail_archive_links l ON l.id=json_extract(j.args,'$.link_id') "
                                       "WHERE j.kind IN ('archive_correspondence5643','file_mail_attachments5643') AND j.matter_ref IS NULL "
                                       "AND json_valid(j.args)").fetchall():
                self.db.execute("UPDATE jobs SET matter_ref=?,args=json_set(args,'$.matter',?) WHERE id=?",(job['matter_id'],job['matter_id'],job['id']))
            self.db.commit();self.setting('correspondances5644:jobs_matter',True)
        self.quarantine_own5656()
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

    def _then(self, kind, args=None, priority=None):
        """5.6.5 : suite d'un travail qui vient de se terminer (lot suivant, étape suivante). Elle remplace le travail fini et ne
        grossit donc pas la file : le plafond des travaux automatiques ne s'applique pas, seul le garde-fou absolu demeure.
        5.6.9 : une analyse automatique appelant le modèle respecte le quota journalier du régime économe (reportée, jamais perdue :
        la surveillance la relancera lors d'un prochain changement)."""
        from . import economie569
        if kind in economie569.AUTOMATIC_LLM and not economie569.quota_ok(self, kind):
            from .live430 import emit
            economie569.defer(self, kind, args)   # 5.6.11 : reprise durable le lendemain
            emit(self, 'documents', 'Analyse automatique différée au lendemain (quota journalier du régime économe) : ' + kind,
                 getattr(self, 'active_job_id', None), str((args or {}).get('matter', '')), dedupe='econome-' + kind + '-' + now()[:10])
            return None
        return self.enqueue(kind, args, priority, chained=True)

    def enqueue(self, kind, args=None, priority=None, chained=False, transactional=False, origin=None):
        """Met un travail en file. 5.6.39 (audit 5.6.37, F02) : ``transactional=True`` = le travail est inséré dans la transaction de
        l'appelant, sans validation, sans annulation globale et sans notification : l'appelant valide l'ensemble puis appelle
        ``notify_queued``. Sinon (contrat historique), la mise en file est validée ici ; une notification en panne après cette validation
        n'est jamais transformée en échec de la demande."""
        if kind not in JOBS:
            raise Stop('action_inconnue')
        if args and args.get('matter'):
            from . import conflicts500
            if kind in conflicts500.GATED_KINDS:
                conflicts500.gate(self, str(args['matter']))
        from . import arret5643, pause5629   # 5.6.43 (cahier v9, § 16) : origine explicite, parent et contrôleur unique
        origin = arret5643.resolve_origin(self, kind, args, job_priority(kind) if priority is None else priority, origin)
        parent_job_id = arret5643.check_parent(self)
        pause5629.check_enqueue(self, kind, args, job_priority(kind) if priority is None else priority, origin)
        raw = json.dumps(args or {}, sort_keys=True)
        maximum=120000 if kind=='prepare_transcript_report' else (60000 if kind in ('assistant_answer','prepare_document_project','prepare_hearing','prepare_word_project','prepare_legal_opinion','coach_hearing35','record_call35') else (24000 if kind in ('propose_work_plan','prepare_cabinet_letter') else
          (120000 if kind=='import_mcp_legal_results' else
          (16000 if kind in ('legal_research','verify_official_decision') else 8000))))
        if len(raw)>maximum:
            raise Stop('action_trop_longue')
        if not self.db.in_transaction:self.db.execute('BEGIN IMMEDIATE')
        # 5.6.65 (audit 5.6.64, R13) : une demande identique à un travail suspendu par la pause retrouve ce travail (même identifiant) au
        # lieu d'en créer un second, que la reprise aurait exécuté en double.
        # 5.6.66 : … seulement tant que la pause qui l'a suspendu est en vigueur. En 5.6.65, un ancien travail resté « suspendu » (pause
        # levée depuis) absorbait toutes les nouvelles demandes identiques : la surveillance des courriels ne tournait plus.
        old = None
        for cand in self.db.execute("SELECT id,status,stop_reason,matter_ref FROM jobs WHERE kind=? AND args=? "
                                    "AND status IN ('pending','running','cancel_requested','suspended') ORDER BY id DESC", (kind,raw)).fetchall():
            if cand['status'] != 'suspended' or arret5643.pause_in_effect(self, cand):
                old = cand
                break
        if old:
            if not transactional:self.db.commit()
            return old[0]
        priority=job_priority(kind) if priority is None else max(0,min(int(priority),100))
        # 5.6.5 : les travaux automatiques (priorité 30 et plus) ont leur propre plafond ; ils ne peuvent plus remplir la file au point de
        # refuser une demande de l'avocat (avant : 100 travaux en attente, quels qu'ils soient, et plus rien n'entrait).
        if priority>=AUTOMATIC_PRIORITY and not chained:
            automatic=self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND priority>=?",(AUTOMATIC_PRIORITY,)).fetchone()[0]
            same=self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND kind=? AND priority>=?",(kind,AUTOMATIC_PRIORITY)).fetchone()[0]
            if automatic>=AUTOMATIC_LIMIT or same>=AUTOMATIC_PER_KIND:
                if not transactional:self.db.rollback()
                raise Stop('file_attente_pleine')
        elif self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]>=USER_LIMIT:
            if not transactional:self.db.rollback()
            raise Stop('file_attente_pleine')
        cur = self.db.execute('''INSERT INTO jobs
            (kind,args,status,created,finished,result,priority,origin,parent_job_id,matter_ref) VALUES (?,?,?, ?,NULL,NULL,?,?,?,?)''',
            (kind,raw,'pending',now(),priority,origin,parent_job_id,arret5643.matter_of(args) or None))
        if transactional:
            return cur.lastrowid
        self.db.commit()
        self.notify_queued(cur.lastrowid, args)
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

    def admit(self, batch, family=None, cap=None):
        """5.6.65 (audit 5.6.64, R08) : admission commune d'un lot de travaux automatiques. Dans UNE transaction (BEGIN IMMEDIATE, aucun
        appel réseau) : les travaux déjà en file sont dédoublonnés, la place nécessaire au lot ENTIER est comparée au plafond de la famille,
        puis tout le lot est inséré, ou rien. Renvoie les identifiants, ou None si le lot ne tient pas (l'appelant garde le dossier « à
        traiter » et réessaie à un tour suivant). ``batch`` : liste de (type, arguments, priorité)."""
        family = ANALYSIS_FAMILY if family is None else frozenset(family)
        cap = ANALYSIS_CAP if cap is None else int(cap)
        if not self.db.in_transaction:self.db.execute('BEGIN IMMEDIATE')
        try:
            marks=','.join('?'*len(family))
            pending=self.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND kind IN (%s)"%marks,tuple(family)).fetchone()[0]
            needed=0
            for kind,args,_priority in batch:
                if kind not in family:continue
                raw=json.dumps(args or {},sort_keys=True)
                from . import arret5643
                if not any(c['status']!='suspended' or arret5643.pause_in_effect(self,c) for c in self.db.execute(
                        "SELECT status,stop_reason,matter_ref FROM jobs WHERE kind=? AND args=? AND status IN ('pending','running','cancel_requested','suspended')",
                        (kind,raw)).fetchall()):
                    needed+=1
            if needed and pending+needed>cap:
                self.db.rollback()
                return None
            ids=[self.enqueue(kind,args,priority=priority,transactional=True) for kind,args,priority in batch]
            self.db.commit()
        except BaseException:
            if self.db.in_transaction:self.db.rollback()
            raise
        for jid,(_kind,args,_priority) in zip(ids,batch):self.notify_queued(jid,args)
        return ids

    def notify_queued(self, job_id, args=None):
        """Notification « demande enregistrée », après validation ; une panne est journalisée, jamais remontée (le travail existe)."""
        try:
            from .live430 import emit
            emit(self,'queued','Demande enregistrée ; traitement mis en attente.',job_id,
              str((args or {}).get('matter','')),str((args or {}).get('key','')))
        except Exception as error:   # noqa: BLE001
            try:
                if self.db.in_transaction:self.db.rollback()
                self.db.execute('INSERT INTO audit VALUES(NULL,?,?,?)',(now(),'notification_file_en_panne',json.dumps({'job':job_id,'type':type(error).__name__})))
                self.db.commit()
            except Exception:   # noqa: BLE001
                pass

    def cancel_job(self, job_id):
        try:job_id=int(job_id)
        except (TypeError,ValueError):raise Stop('operation_invalide') from None
        stamp=now()
        # 5.6.44 (audit F10) : « Arrêter » pendant une demande d'arrêt posée par la pause devient une annulation terminale (motif « user »),
        # sans remettre le travail en cours ; la finalisation ne le suspendra donc pas et la reprise ne le relancera pas.
        upgraded=self.db.execute(
            "UPDATE jobs SET stop_reason='user',result=? WHERE id=? AND status='cancel_requested' AND COALESCE(stop_reason,'')<>'user'",
            (json.dumps({'message':'Annulation demandée par l’avocat ; la pause ne reprendra pas ce travail.'}),job_id)).rowcount
        changed=self.db.execute("""UPDATE jobs SET
            status=CASE status WHEN 'pending' THEN 'cancelled' WHEN 'suspended' THEN 'cancelled' WHEN 'running' THEN 'cancel_requested' ELSE status END,
            finished=CASE WHEN status IN ('pending','suspended') THEN ? ELSE finished END,
            result=CASE status WHEN 'pending' THEN ? WHEN 'suspended' THEN ? WHEN 'running' THEN ? ELSE result END,
            stop_reason=CASE WHEN status IN ('pending','suspended','running') THEN 'user' ELSE stop_reason END
            WHERE id=? AND status IN ('pending','suspended','running')""",
            (stamp,json.dumps({'message':'Opération annulée avant son démarrage.'}),json.dumps({'message':'Opération suspendue annulée.'}),
             json.dumps({'message':'Arrêt demandé ; le worker doit encore le confirmer.'}),job_id)).rowcount+upgraded
        self.db.commit()
        from .arret5643 import cancel_descendants
        cancel_descendants(self,job_id)
        row=self.db.execute('SELECT status,kind FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not row:raise Stop('operation_absente')
        if row['status'] not in ('cancelled','cancel_requested'):raise Stop('operation_deja_terminee')
        state=row['status']
        if not changed:   # second arrêt : idempotent (R10), sans nouvel audit ni annonce ; « already » permet de le dire à l'utilisateur
            return {'job_id':job_id,'status':state,'already':True}
        self.db.commit();self.audit('operation_annulee',{'job':job_id,'kind':row['kind'],'state':state})
        from .live430 import emit
        emit(self,state,'Annulation confirmée avant démarrage.' if state=='cancelled' else 'Arrêt demandé ; confirmation du worker attendue.',job_id)
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

    def soft_write(self, sql, params=()):
        """5.6.32 : écriture facultative (cache, mesure, instantané) : si une autre écriture occupe la base, elle est sautée au lieu de
        bloquer la page ; elle sera refaite au prochain affichage. Renvoie True si elle a eu lieu."""
        self.db.execute('PRAGMA busy_timeout=300')
        try:
            self.db.execute(sql, params)
            self.db.commit()
            return True
        except sqlite3.OperationalError as error:
            if 'locked' not in str(error).lower() and 'busy' not in str(error).lower():
                raise
            try:
                self.db.rollback()
            except sqlite3.Error:
                pass
            return False
        finally:
            self.db.execute('PRAGMA busy_timeout=%d' % (int(getattr(self, '_busy', 10)) * 1000))

    def quarantine_own5656(self):
        """5.6.56 (audit 5.6.55, § 3.2) : propositions de correspondant encore ouvertes qui visent une identité du cabinet -> écartées
        (« ecartee_cabinet »), historique conservé ; exécuté à la préparation de la base (démarrage, mise à jour)."""
        try:
            from .mailbox import own_addresses as _own5656
            own = sorted(_own5656(self.c.get('mail', {})))
            marks = ','.join('?' for _ in own)
            # aucune écriture au démarrage s'il n'y a rien à écarter (5.6.44 : démarrage sans verrou d'écriture inutile)
            if own and self.db.execute("SELECT 1 FROM proposals WHERE status='pending' AND lower(email) IN (%s) LIMIT 1" % marks, own).fetchone():
                n = self.db.execute("UPDATE proposals SET status='ecartee_cabinet',updated=? WHERE status='pending' AND lower(email) IN (%s)"
                                    % marks, [now()] + own).rowcount
                self.db.commit()
                return n
        except (sqlite3.Error, KeyError, TypeError):
            pass
        return 0

    def propose(self, matter, email, evidence):
        try:
            email = email_value(email)
        except Stop:
            return
        from .mailbox import own_addresses as _own5656
        if email in _own5656(self.c['mail']):                 # 5.6.56 (N04) : toutes les identités du cabinet
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
        from .mailbox import own_addresses as _own5656
        if email in _own5656(self.c['mail']):                 # 5.6.56 (N05)
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
            try:blocked=json.loads(feedback_path.read_text(encoding='utf-8'))
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

    def _current5652(self, mid):
        """5.6.52 (F02), revu en 5.6.55 : après une ingestion, le dossier est marqué « à actualiser » et une actualisation de faible
        priorité est mise en file — la reconstruction ne se fait plus dans le travail d'indexation (elle bloquait le travailleur et la
        base sur les grands dossiers, et donc les brouillons automatiques)."""
        if not mid:
            return None
        try:
            from .modele5651 import schedule_refresh
            return schedule_refresh(self, str(mid))
        except Exception:   # noqa: BLE001 — l'ingestion est faite ; l'état partiel est rendu visible par le contexte
            return None

    def perform(self, kind, args):
        if kind=='ranger_notes5666':
            from .notes5666 import file_drafts
            return file_drafts(self,args)
        if kind=='maj_local5661':   # 5.6.61 : téléchargement, empreinte, signature, puis installateur du poste
            from .maj5661 import local_update
            return local_update(self,args)
        if kind=='crm_collecte5657':   # 5.6.57 (§ 8.3) : contacts alimentés par la boîte mail, passage borné et reprenable
            from .crm5657 import collect
            return collect(self)
        if kind=='actualiser5655':   # 5.6.55 : actualisation différée des données structurées d'un dossier (faible priorité)
            from .modele5651 import ensure_current
            return ensure_current(self,str(args.get('matter') or ''))
        if kind=='recit5651':   # 5.6.51 (D06, D09) : rappel des faits ou de la procédure, paragraphes sourcés
            from .recit5651 import run as recit
            return recit(self,args)
        if kind=='relecture5651':   # 5.6.51 (A05) : propositions de relecture Word par lots
            from .relecture5651 import run as relecture
            return relecture(self,args)
        if kind in CONSERVATION_JOBS:
            from .correspondances5643 import perform as conserve
            return conserve(self,kind,args)
        if kind=='document568_run':
            from .procedure568 import execute
            return execute(self,str(args.get('run') or ''),str(args.get('owner') or 'cabinet'))
        if kind=='document568_scan':
            from .automation568 import incoming_scan
            return incoming_scan(self,str(args.get('owner') or 'cabinet'))
        if kind=='document568_compile':
            from .automation568 import compile_instruction
            return compile_instruction(self,args.get('instruction',''),str(args.get('owner') or 'cabinet'))
        if kind in {'sent568_scan','received568_scan','followup568_prepare','proactive568_cycle','talk568_prepare','news568_collect'}:
            from .proactive568 import perform
            return perform(self,kind,args)
        if kind=='search_index490':
            from .search490 import job
            return job(self,args)
        if kind=='analyze_notice440':
            from .notices440 import analyze
            return analyze(self,args)
        if kind=='extract_facts460':
            from .facts460 import scan_matter
            return scan_matter(self,args)
        if kind=='recherche5634':   # 5.6.34 (B06, U03) : un seul service de recherche pour tous les parcours
            from .web5634 import research_job
            return research_job(self,args)
        if kind=='microsoft5636':   # 5.6.36 : agenda Microsoft 365 (copie des événements AxiorHub, lecture d'Outlook)
            from .microsoft5636 import job
            return job(self,args)
        if kind=='corporate5636':   # 5.6.36 : actes du secrétariat juridique (modèles du cabinet ou génériques)
            from .corporate5636 import job
            return job(self,args)
        if kind=='outils5636':   # 5.6.36 : grille d'analyse, playbook appliqué à un contrat, mémo juridique
            from .outils5636 import job
            return job(self,args)
        if kind=='relances5636':   # 5.6.36 : projets de relance des factures échues (Invoice Ninja en lecture)
            from .relances5636 import job
            return job(self,args)
        if kind=='initiative5636':   # 5.6.36 : analyse d'un courriel (plan proposé) ou exécution des étapes validées
            from .initiatives5636 import job
            return job(self,args)
        if kind=='pj_courriel5636':   # 5.6.36 : rangement des pièces jointes (option activée par l'avocat)
            from .ged5636 import file_attachments
            return file_attachments(self,str(args.get('key') or ''),str(args.get('matter') or ''),'automatique')
        if kind=='digest_pieces5632':   # 5.6.32 : fiches de pièces (examen du dossier entier)
            from .synthese5632 import build_job
            out=build_job(self,args)
            self._current5652(args.get('matter') or args.get('mid'))   # 5.6.52 (F02) : données du dossier actualisées après lecture
            return out
        if kind=='mailrules5632':   # 5.6.32 : règles « Déplacer » de la messagerie
            from .courrier5632 import apply_moves
            return apply_moves(self)
        if kind=='briefing_oral5632':   # 5.6.32 : résumé oral du briefing, rédigé par l'IA et contrôlé
            from .oral5632 import job
            return job(self,args)
        if kind=='veille5632_collect':   # 5.6.32 : veille juridique planifiée
            from .veille5632 import collect
            return collect(self,args)
        if kind=='fiche_dossier5627':   # 5.6.27 : fiche de travail du dossier (page Dossier refondue)
            from .dossier5627 import prepare
            return prepare(self,args)
        if kind=='analyser_dossier5624':   # 5.6.24 (F25) : parcours complet suivi étape par étape
            from .dossier5624 import analyse
            return analyse(self,args)
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
                    'advance_playbook380','complete_playbook_step380','retry_playbook_step5643','cancel_playbook5643',
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
        if kind=='maildraft5613':
            from .maildraft5613 import perform
            return perform(self,args)
        if kind=='task5614':
            from .taches5614 import perform
            return perform(self,args)
        if kind in ('routine520','routine5652'):   # 5.6.52 : routines personnalisées
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
            if remaining and not selected:raise Stop('file_attente_pleine')     # 5.6.5 : pas de relance en boucle
            for m in selected:self.enqueue('index',{'matter':m['id']},chained=True)   # demande explicite : lot borné à 10
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
            queued=[]
            for m in selected:   # 5.6.29 : un dossier en pause ou en échec répété n'est plus remis en file à chaque passage
                try:self.enqueue('refresh_brief',{'matter':m['id']});queued.append(m)
                except Stop:pass
            selected=queued if queued else selected
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
            self._current5652(matter['id'])   # 5.6.52 (F02) : nouveaux documents pris en compte sans ouvrir la page
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
            try:fcntl.flock(probe,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:busy=True
        free_kinds=" AND kind IN ('live_calendar430','live_documents430','automation_setting','archive_correspondence5643','file_mail_attachments5643','health')"
        from .blocage5660 import RESERVE_AFTER
        # 5.6.61 : un travail qui attend le verrou exclusif depuis 3 essais le réserve : aucun nouveau travail à verrou partagé (réponses de
        # l'assistant, brouillons) ne démarre avant lui ; seuls les travaux sans verrou continuent. Sans cela, une suite continue de réponses
        # de l'assistant pouvait repousser indéfiniment la veille (constaté le 18 octobre 2026).
        # 5.6.65 (audit 5.6.64, R09) : seul un travail qui attend le verrou général EXCLUSIF réserve le passage ; un travail qui attend le
        # verrou d'UN dossier (réponse, brouillon) diffère ce dossier et laisse les autres dossiers avancer.
        from .queue567 import SCOPED
        waiting=[] if busy else self.db.execute("SELECT * FROM jobs WHERE status='pending' AND lock_waits>=? ORDER BY lock_waits DESC,id",
                                                (RESERVE_AFTER,)).fetchall()
        reserved=next((r for r in waiting if r['kind'] not in SCOPED and r['kind'] not in NON_EXCLUSIVE_JOBS),None)
        if reserved and (not reserved['not_before'] or reserved['not_before']<=now()):
            row=reserved
        else:
            row=self.db.execute("SELECT * FROM jobs WHERE status='pending' AND (not_before IS NULL OR not_before<=?)"+
              (free_kinds if busy or reserved else '')+' ORDER BY '+PICK_ORDER+' LIMIT 1',(now(),)).fetchone()
        if not row:
            self.db.commit()
            return False
        worker_id=getattr(self,'worker_id','manual')
        self.db.execute("UPDATE jobs SET status='running',started=?,worker=?,attempts=attempts+1 WHERE id=? AND status='pending'",(now(),worker_id,row['id']))
        self.db.commit()
        self.active_job_id=row['id']
        from . import secours564;secours564.begin(row['id'],row['created'],row['kind'])
        try:
            _args=json.loads(row['args'] or '{}');_args=_args if isinstance(_args,dict) else {}
        except (ValueError,TypeError):_args={}
        trace480.begin(row['id'],row['kind'],_args.get('matter') or _args.get('matter_id') or '')
        from . import arret5643   # 5.6.43 (§ 16) : les appels au modèle de ce fil sont rattachés au travail (interruption, pause)
        _job_ctx=arret5643.job_context(self.c['state_dir'],row['id'],arret5643.matter_of(_args),arret5643.is_automatic_row(row,_args))
        _job_ctx.__enter__()
        pause_reason=''
        try:
            from .live430 import progress
            from .missions567 import check_authority
            check_authority(self,_args)
            from .pause5629 import check_running,with_consignes   # 5.6.29
            check_running(self,row,_args)
            if not int(row['lock_waits'] or 0):progress(self,'Traitement démarré')   # 5.6.60 : pas un événement par attente du verrou
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
                    changed=self.db.execute("UPDATE jobs SET status='pending',attempts=MAX(attempts-1,0),not_before=? WHERE id=? AND status='running'",
                                            ((datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat(),row['id'])).rowcount
                    self.db.commit()
                    if not changed:raise Stop('operation_annulee')
                    self.active_job_id=None;trace480.end();secours564.end();_job_ctx.__exit__(None,None,None)
                    return False
            elif row['kind'] in ('live_calendar430','live_documents430'):
                from .watch430 import perform
                result=perform(self,row['kind'],json.loads(row['args']))
            elif row['kind']=='automation_setting':
                # 5.2.1 : simple réglage local, appliqué même si une analyse tient le verrou.
                result=self.perform(row['kind'],json.loads(row['args']))
            elif row['kind'] in CONSERVATION_JOBS or row['kind'] in CONTROL_JOBS:
                # 5.6.43 (§ 13.5) : copie déterministe, sans IA ; elle n'attend pas une inférence qui tient le verrou global.
                # 5.6.65 (R16) : idem pour les contrôles de santé (lecture seule, sans génération).
                result=self.perform(row['kind'],json.loads(row['args']))
            else:
                from .queue521 import note_holder
                from .queue567 import producer_lock
                from .competences5632 import for_job   # 5.6.32 (R16) : compétences retenues pour CE travail seulement
                with producer_lock(self.c,row['kind'],_args), for_job(self,row,_args):
                    note_holder(self.c['state_dir'],'Travail n° %s (%s)'%(row['id'],row['kind']))
                    result=self.perform(row['kind'],with_consignes(self,json.loads(row['args'])))
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
                # 5.6.60 : attente différée (10 s à 5 min), sans compter de tentative ; le motif est noté pour le diagnostic
                from .blocage5660 import backoff_seconds, holder
                waits=int(row['lock_waits'] or 0)+1
                from .blocage5660 import RESERVE_AFTER
                delay=15 if waits>=RESERVE_AFTER else backoff_seconds(waits)   # 5.6.61 : travail réservataire réessayé souvent
                changed = self.db.execute("UPDATE jobs SET status='pending',attempts=MAX(attempts-1,0),lock_waits=?,not_before=?,blocked_by=? "
                                          "WHERE id=? AND status='running'",(waits,(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(),
                                                                              holder(self.c['state_dir'])['holder'][:120],row['id'])).rowcount
                self.db.commit()
                if changed:
                    self.active_job_id=None;trace480.end();secours564.end();_job_ctx.__exit__(None,None,None)
                    return False
                e=Stop('operation_annulee')
            result={'erreur':str(e)};status='error'
            if getattr(e,'detail',None):   # 5.6.34 (B15) : service, étape, statut, corrélation, ressource anonymisée (aucun secret)
                from .erreurs5634 import public as _public_detail
                result['detail']=_public_detail(e.detail)
            # 5.6.43 (§ 16) : une pause suspend (reprenable) ; seule l'annulation est terminale
            if str(e) in ('agent_en_pause_sur_ce_dossier','dossier_en_pause'):
                result={'message':'Suspendu : IA en pause sur ce dossier. Repris à la levée de la pause.'};status='suspended';pause_reason='pause_dossier'
            elif str(e) in ('automatismes_en_pause','ia_en_pause','controle_pause_indisponible'):
                result={'message':'Suspendu : IA en pause. Repris à la reprise de l’IA.' if str(e)!='controle_pause_indisponible'
                        else 'Suspendu : l’état de pause n’a pas pu être vérifié ; reprise après contrôle.'};status='suspended';pause_reason='pause'
            elif str(e)=='operation_annulee':
                result={'message':'Annulé à la demande de l’avocat.'};status='cancelled'
        except Exception:
            result={'erreur':'action_interrompue_consulter_le_journal'};status='error'
        _job_ctx.__exit__(None,None,None)
        reason_code=pause_reason or 'pause'
        partial=json.dumps({'message':'Arrêt confirmé ; résultats déjà produits conservés.', 'partial_result':result})
        self.db.execute("""UPDATE jobs SET
            status=CASE
              WHEN status='cancelled' THEN 'cancelled'
              WHEN status='cancel_requested' AND COALESCE(stop_reason,'') IN ('pause','pause_dossier') AND ? IN ('suspended','done') THEN ?
              WHEN status='cancel_requested' THEN 'cancelled'
              ELSE ? END,
            stop_reason=CASE WHEN ?='suspended' AND status<>'cancelled' THEN COALESCE(stop_reason,?) ELSE stop_reason END,
            finished=?,
            result=CASE
              WHEN status='cancelled' THEN ?
              WHEN status='cancel_requested' AND COALESCE(stop_reason,'') IN ('pause','pause_dossier') AND ? IN ('suspended','done') THEN ?
              WHEN status='cancel_requested' THEN ?
              ELSE ? END
            WHERE id=?""", (status,status,status,status,reason_code,now(),partial,status,json.dumps(result),partial,json.dumps(result),row['id']))
        actual=self.db.execute('SELECT status,result,stop_reason,matter_ref FROM jobs WHERE id=?',(row['id'],)).fetchone()
        if actual['status']=='suspended' and actual['stop_reason'] in ('pause','pause_dossier'):
            # 5.6.44 (F09) : la reprise a pu intervenir pendant l'interruption ; sans pause en vigueur, le travail repart sans second clic.
            from .pause5629 import is_paused
            if not self.settings('missions567:pause',False) and not (actual['matter_ref'] and is_paused(self,actual['matter_ref'])):
                # 5.6.60 : relance unique et différée ; au-delà de 3 essais, la « pause » vient d'ailleurs (un dossier en pause rencontré par
                # un balayage, par exemple) : erreur explicite au lieu d'une boucle qui fige la file (118 739 essais constatés).
                if int(row['attempts'] or 0)+1<3:
                    self.db.execute("UPDATE jobs SET status='pending',stop_reason=NULL,result=NULL,not_before=? WHERE id=? AND status='suspended'",
                                    ((datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),row['id']))
                else:
                    self.db.execute("UPDATE jobs SET status='error',stop_reason=NULL,result=? WHERE id=? AND status='suspended'",
                                    (json.dumps({'erreur':'suspension_sans_pause_en_vigueur','message':'Arrêté : le travail se suspendait pour une pause '
                                                 'qui ne le concerne pas (dossier en pause rencontré en chemin). Relancez-le après contrôle.'},ensure_ascii=False),row['id']))
                actual=self.db.execute('SELECT status,result FROM jobs WHERE id=?',(row['id'],)).fetchone()
        status=actual['status'];result=json.loads(actual['result'] or '{}')
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
        self.active_job_id=None;trace480.end();secours564.end()
        if status=='done' and row['kind']=='live_documents430' and result.get('continue_scan'):
            self._then('live_documents430',priority=70)
        if status=='done' and result.get('messages_remis_en_attente',0):
            self._then('run')
        if status=='done' and row['kind']=='index' and result.get('en_attente',0):
            # Continue in bounded batches, allowing other queued jobs between lots.
            self._then('index',json.loads(row['args']))
        if status=='done' and row['kind']=='index' and result.get('contenu_modifie') and not result.get('en_attente'):
            self._then('refresh_brief',json.loads(row['args']))
        if status=='done' and row['kind']=='refresh_brief':
            self._then('sync_legal_memory',json.loads(row['args']))
        autonomy_active=self.settings('automation:autonomy_enabled',
          self.c.get('autonomy',{}).get('enabled',False))
        orchestrator_active=self.settings('automation:orchestrator_enabled',
          self.c.get('orchestrator',{}).get('enabled',False))
        if orchestrator_active and status=='done' and row['kind'] in ('run','sync','live_mail430'):
            self._then('orchestrator_mail_sweep',priority=40)
        elif autonomy_active and status=='done' and row['kind'] in ('run','sync','live_mail430'):
            self._then('autonomy_mail_sweep',priority=40)
        if autonomy_active and status=='done' and row['kind']=='prepare_document_project':
            parameters=json.loads(row['args'])
            if parameters.get('automatic')=='yes':
                from .autonomy import record_automatic_project
                record_automatic_project(self,parameters,result)
            if result.get('matter',{}).get('id'):
                self._then('refresh_operational_memory',{'matter':result['matter']['id']},priority=55)
        if autonomy_active and status=='done' and row['kind'] in ('index','refresh_brief','sync_legal_memory'):
            parameters=json.loads(row['args'])
            if parameters.get('matter'):
                self._then('refresh_operational_memory',{'matter':parameters['matter']},priority=55)
        if (status=='done' and row['kind']=='index' and
            result.get('embeddings',{}).get('pending',0) and
            not result.get('embeddings',{}).get('error')):
            self._then('index',json.loads(row['args']))
        if status=='done' and row['kind'] in ('sync','discover') and result.get('parcours_partiel'):
            self._then('discover')
        if status=='done' and row['kind']=='index_all' and result.get('suite'):
            self._then('index_all',{'after':result['after']})
        if status=='done' and row['kind']=='memory_all' and result.get('suite'):
            self._then('memory_all',{'after':result['after']})
        if status=='done' and row['kind']=='monitor_all' and result.get('suite'):
            self._then('monitor_all',{'after':result['after']})
        if status=='done' and row['kind']=='monitor_all' and not result.get('suite'):
            self._then('build_daily_dashboard')
        if status=='done' and row['kind']=='organize_cabinet' and result.get('suite'):
            self._then('organize_cabinet')
        if status=='done' and row['kind'] in ('organize_cabinet','reconcile_inbox','classify_portfolio') and not result.get('suite'):
            self._then('build_daily_dashboard')
        return True


def _after_lock(desk,worker_id):
    """Après une interruption par verrou : transaction annulée, travail actif oublié puis rendu à la file s'il est resté « en cours »."""
    try:desk.db.rollback()
    except sqlite3.Error:pass
    if getattr(desk,'active_job_id',None):
        desk.active_job_id=None
        try:
            from .queue567 import recover
            recover(desk,worker_id)
        except (sqlite3.Error,Stop):
            try:desk.db.rollback()
            except sqlite3.Error:pass


def _patient(desk,action,label,limit):
    """Exécute ``action`` en attendant (au plus ``limit`` secondes) que la base soit libérée par un autre processus."""
    waited,pause=0.0,1.0
    while True:
        try:
            return action()
        except (sqlite3.OperationalError,Stop) as error:
            if not _locked(error) or waited>=limit:raise
            try:desk.db.rollback()
            except sqlite3.Error:pass
            _startup_log('base de données occupée par un autre processus (%s) ; nouvel essai dans %d s.'%(label,max(1,round(pause))))
            time.sleep(pause);waited+=pause;pause=min(pause*2,15.0)


SUPERVISION_SECONDS=30   # 5.6.65 (audit 5.6.64, R15) : cadence du superviseur des durées, indépendante des travaux


class _Supervision:
    """Accès minimal à la base pour le superviseur : sa propre connexion (jamais celle du worker, utilisée par un autre fil)."""
    def __init__(self,c):
        self.c=c
        self.db=sqlite3.connect(Path(c['state_dir'])/'desk.sqlite3',timeout=5)
        self.db.row_factory=sqlite3.Row

    def now(self):
        return now()

    def audit(self,action,data):
        from .cabinet_pilot import append_audit
        self.db.execute('BEGIN IMMEDIATE')
        try:
            stamp=now()
            self.db.execute('INSERT INTO audit VALUES(NULL,?,?,?)',(stamp,action,json.dumps(data)))
            append_audit(self,stamp,action,data)
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise


def _supervise(c,stop):
    """5.6.65 (R15) : les durées maximales sont contrôlées toutes les 30 s, même quand tous les workers sont pris par un travail bloqué
    (avant : le contrôle n'avait lieu qu'entre deux travaux). Le superviseur continue pendant la pause de l'IA ; il n'appelle jamais le
    modèle et ne garde aucune transaction ouverte : il ne fait que DEMANDER l'arrêt (cancel_requested), que le travail acquitte."""
    from .blocage5660 import watchdog
    sup=None
    try:
        while not stop.is_set():
            try:
                if sup is None:sup=_Supervision(c)
                watchdog(sup)
            except sqlite3.Error:   # base momentanément occupée : nouvel essai au cycle suivant
                try:
                    if sup is not None and sup.db.in_transaction:sup.db.rollback()
                except sqlite3.Error:
                    pass
            except Exception:   # noqa: BLE001 — le superviseur ne s'arrête jamais sur une erreur
                pass
            stop.wait(SUPERVISION_SECONDS)
    finally:
        if sup is not None:
            try:sup.db.close()
            except sqlite3.Error:pass


def worker(c,worker_id='1'):
    # Multiple atomic claimers; mutating producers still share run.lock.
    import threading
    if not re.fullmatch(r'[1-4]',str(worker_id)):raise Stop('worker_invalide')
    lock_name='desk-worker.lock' if str(worker_id)=='1' else 'desk-worker-'+str(worker_id)+'.lock'
    with open(Path(c['state_dir'])/lock_name,'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        desk=Desk(c)
        desk.worker_id=str(worker_id)
        from .queue567 import recover
        _patient(desk,lambda:recover(desk,worker_id),'reprise des travaux interrompus',startup_wait())
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
        threading.Thread(target=_supervise,args=(c,stop),daemon=True,name='supervision5665').start()
        pause=3.0
        last_watch=0.0
        while True:
            try:
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
                if time.monotonic()-last_watch>60:   # 5.6.60 : durée maximale par type de travail
                    last_watch=time.monotonic()
                    from .blocage5660 import watchdog
                    watchdog(desk)
                    if str(worker_id)=='1':
                        try:   # 5.6.61 : avis de nouvelle version (flux public des versions, au plus toutes les 6 h)
                            from .maj5661 import auto_tick as _maj_auto, check as _maj_check
                            _maj_check(desk)
                            _maj_auto(desk)   # mode « automatique la nuit », si l'administrateur l'a choisi
                        except Exception:   # noqa: BLE001 — jamais bloquant pour la file
                            pass
                        try:   # 5.6.61 : brouillons de l'agent dans la messagerie (« À relire »), relus au plus toutes les 2 min
                            from .cockpit530 import _drafts as _drafts530
                            _drafts530(desk, refresh=True)
                        except Exception:   # noqa: BLE001 — messagerie injoignable : la liste précédente reste affichée
                            pass
                desk.work_once()
                pause=3.0
            except (sqlite3.OperationalError,Stop) as error:
                # 5.6.45 : base occupée par un autre processus (installation, sauvegarde, écriture longue) : le worker attend et reprend
                # au lieu de s'arrêter ; un travail resté « en cours » par cette interruption est récupéré.
                if not _locked(error):raise
                _after_lock(desk,worker_id)
                _startup_log('base de données occupée par un autre processus (worker %s, %s) ; nouvel essai dans %d s.'%(worker_id,error.__cause__ or error,pause))
                time.sleep(pause)
                pause=min(pause*2,60.0)
                continue
            time.sleep(3)

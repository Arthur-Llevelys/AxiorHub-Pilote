#!/usr/bin/env python3
"""Upgrade a verified AxiorHub installation to 5.6.21."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import posixpath
import shutil
import sqlite3
import subprocess
import tempfile
import uuid

BASE = Path('/opt/axiorhub-mail-agent')
CONFIG = Path('/etc/axiorhub-mail-agent/config.json')
STATE = Path('/var/lib/axiorhub-mail-agent')
VERSION = '5.6.21'
SUPPORTED_PREVIOUS = ('3.5.0','3.5.1','3.6.0','3.6.1','3.6.2','3.6.3','3.6.4','3.6.5','3.7.0','3.8.0','3.8.1','3.9.0','3.9.1','3.9.2','3.9.3','4.0.0','4.1.0','4.1.1','4.2.0','4.3.0','4.3.1','4.4.0','4.5.0','4.6.0','4.7.0','4.8.0','4.9.0','5.0.0','5.0.1','5.1.0','5.2.0','5.2.1','5.3.0','5.4.0','5.5.0','5.6.0','5.6.1','5.6.2','5.6.3','5.6.4','5.6.5','5.6.6','5.6.7','5.6.8','5.6.9','5.6.10','5.6.11','5.6.12','5.6.13','5.6.14','5.6.15','5.6.16','5.6.17','5.6.18','5.6.19','5.6.20')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(root):
    manifest = root / 'MANIFEST.sha256'
    if not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError('Manifeste absent ou ambigu.')
    listed=set()
    for line in manifest.read_text().splitlines():
        try: expected, name = line.split('  ', 1)
        except ValueError: raise RuntimeError('Ligne de manifeste invalide.') from None
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Chemin du manifeste refusé.')
        listed.add(relative.as_posix())
        path = root / relative
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise RuntimeError('Lien dans le paquet refusé.')
        if not path.is_file(): raise RuntimeError('Fichier du manifeste absent : '+name)
        if digest(path.read_bytes()) != expected:
            raise RuntimeError('Un fichier diffère du manifeste : '+name)
    actual={path.relative_to(root).as_posix() for path in root.rglob('*')
            if path.is_file() and path != manifest and '__pycache__' not in path.parts
            and path.suffix != '.pyc'}
    unexpected=sorted(actual-listed)
    if unexpected:
        raise RuntimeError('Fichier hors manifeste refusé : '+unexpected[0])


def atomic(path, data, uid, gid, mode):
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.new-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchown(f.fileno(), uid, gid)
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def switch(base, release):
    temporary = base / ('current.new-'+uuid.uuid4().hex)
    try:
        temporary.symlink_to(release)
        os.replace(temporary, base/'current')
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def updated_config(before):
    c=json.loads(before)
    rag=c.setdefault('rag',{})
    rag.setdefault('enabled',True);rag.setdefault('embedding_model','qwen3-embedding:0.6b')
    rag.setdefault('embedding_chunks_per_run',24);rag.setdefault('timeout_seconds',180)
    automation=c.setdefault('automation',{})
    automation.setdefault('health_enabled',True);automation.setdefault('health_interval_minutes',30)
    automation.setdefault('sync_enabled',True);automation.setdefault('sync_interval_minutes',30)
    automation.setdefault('index_all_enabled',False);automation.setdefault('index_interval_minutes',360)
    automation.setdefault('reconcile_inbox_enabled',True);automation.setdefault('reconcile_interval_minutes',15)
    automation.setdefault('classify_portfolio_enabled',True);automation.setdefault('portfolio_interval_minutes',360)
    automation.setdefault('nextcloud_tasks_enabled',False)
    automation.setdefault('nextcloud_tasks_interval_minutes',15)
    automation.setdefault('daily_digest_enabled',False);automation.setdefault('daily_digest_hour',8)
    integration=c.setdefault('integration',{})
    integration.setdefault('api_enabled',True)
    integration.setdefault('conversation_retention_days',90)
    integration.setdefault('global_search_limit',12)
    integration.setdefault('supervised_openwebui',True)
    integration.setdefault('approval_minutes',30)
    legal_memory=c.setdefault('legal_memory',{})
    legal_memory.setdefault('enabled',True)
    legal_memory.setdefault('calendar_past_days',730)
    legal_memory.setdefault('calendar_future_days',730)
    legal_memory.setdefault('assistant_records',10)
    legal_memory.setdefault('timeline_limit',300)
    strategic=c.setdefault('strategic',{})
    strategic.setdefault('enabled',True)
    strategic.setdefault('max_sources',30)
    strategic.setdefault('max_projects_per_matter',100)
    proactive=c.setdefault('proactive',{})
    proactive.setdefault('enabled',True)
    proactive.setdefault('monitor_interval_minutes',30)
    proactive.setdefault('monitor_batch_size',10)
    proactive.setdefault('monitor_live_nextcloud',True)
    proactive.setdefault('deadline_warning_days',14)
    proactive.setdefault('unanswered_warning_hours',24)
    proactive.setdefault('stale_strategy_on_new_source',True)
    proactive.setdefault('daily_dashboard_enabled',True)
    proactive.setdefault('daily_dashboard_hour',7)
    proactive.setdefault('timezone','Europe/Paris')
    preparation34=c.setdefault('preparation34',{})
    preparation34.setdefault('enabled',True)
    preparation34.setdefault('timezone','Europe/Paris')
    portfolio=c.setdefault('portfolio',{})
    portfolio.setdefault('enabled',True)
    portfolio.setdefault('active_days',180)
    portfolio.setdefault('archive_days',730)
    portfolio.setdefault('bootstrap_months',18)
    portfolio.setdefault('mail_batch_size',100)
    portfolio.setdefault('folder_batch_size',10)
    portfolio.setdefault('reconcile_batch_size',200)
    portfolio.setdefault('backlog_days',14)
    nc=c.setdefault('nextcloud',{})
    if not nc.get('matter_roots'):
        nc['matter_roots']=[(root.rstrip('/')+'/01 - Dossiers'
            if root.rstrip('/').rsplit('/',1)[-1].casefold()=='cabinet exemple' else root)
            for root in nc.get('roots',[])]
    workflow=c.setdefault('nextcloud_workflow',{})
    workflow.setdefault('enabled',False)
    workflow.setdefault('url',nc.get('url','https://cloud.example.com'))
    workflow.setdefault('username','compte-technique@example.com')
    workflow.setdefault('password_file','')
    workflow.setdefault('roots',nc.get('matter_roots') or nc.get('roots',[]))
    workflow.setdefault('calendar_read_urls',c.get('calendar',{}).get('urls',[]))
    workflow.setdefault('task_calendar_url','')
    workflow.setdefault('planning_calendar_url','')
    workflow.setdefault('project_folder','90 - Projets IA')
    workflow.setdefault('notes_enabled',False)
    workflow.setdefault('deck_enabled',False)
    planning=c.setdefault('planning',{})
    planning.setdefault('timezone',c.get('calendar',{}).get('timezone','Europe/Paris'))
    planning.setdefault('weekdays',[0,1,2,3,4])
    planning.setdefault('working_hours',c.get('calendar',{}).get('working_hours',[['09:00','12:00'],['14:00','18:00']]))
    planning.setdefault('max_daily_minutes',360)
    planning.setdefault('approval_minutes',30)
    document_projects=c.setdefault('document_projects',{})
    document_projects.setdefault('enabled',True)
    document_projects.setdefault('approval_minutes',60)
    document_projects.setdefault('destination_subfolder','20_Actes_et_conclusions/90_AxiorHub_Brouillons')
    document_projects.setdefault('max_generated_file_bytes',20_000_000)
    document_projects.setdefault('require_certain_latest_writings',True)
    document_projects.setdefault('deterministic_control_enabled',True)
    hearing=c.setdefault('hearing',{})
    hearing.setdefault('enabled',True)
    hearing.setdefault('approval_minutes',60)
    hearing.setdefault('destination_subfolder','60_Audiences/90_AxiorHub_Brouillons')
    word=c.setdefault('word_legal',{})
    word.setdefault('enabled',True)
    word.setdefault('approval_minutes',60)
    word.setdefault('destination_subfolder','20_Actes_et_conclusions/90_AxiorHub_Brouillons')
    word.setdefault('tracked_author','AxiorHub')
    word.setdefault('clean_and_compared',True)
    nc.setdefault('inventory_page_files',250)
    nc.setdefault('max_inventory_directories',2000)
    nc.setdefault('max_analysis_files',5000)
    documents=c.setdefault('documents',{})
    documents.setdefault('inventory_page_files',250)
    documents.setdefault('index_updates_per_run',25)
    documents.setdefault('max_file_bytes_long',20_000_000)
    documents.setdefault('max_document_chars_long',2_000_000)
    documents.setdefault('max_pdf_pages_long',400)
    documents.setdefault('max_extraction_seconds_long',600)
    documents.setdefault('long_chunk_chars',5200)
    legal=c.setdefault('legal_research',{})
    legal.setdefault('enabled',True)
    legal.setdefault('official_hosts',['legifrance.gouv.fr','courdecassation.fr','justice.fr',
      'conseil-etat.fr','conseil-constitutionnel.fr','eur-lex.europa.eu','curia.europa.eu'])
    legal.setdefault('max_results_per_provider',8)
    providers=legal.setdefault('providers',{})
    for name in ('openlegal','openlegi','goodlegal','pappers'):
        provider=providers.setdefault(name,{})
        provider.setdefault('enabled',False)
        provider.setdefault('url','')
        provider.setdefault('search_path','/search')
        provider.setdefault('api_key_file','')
        provider.setdefault('timeout_seconds',45)
    autonomy=c.setdefault('autonomy',{})
    autonomy.setdefault('enabled',True)
    autonomy.setdefault('mail_monitor_interval_minutes',5)
    autonomy.setdefault('mail_batch_size',20)
    autonomy.setdefault('automatic_document_previews_enabled',True)
    autonomy.setdefault('automatic_internal_files_enabled',True)
    autonomy.setdefault('document_control_enabled',True)
    autonomy.setdefault('control_model',c.get('ollama',{}).get('model',''))
    autonomy.setdefault('diligence_proposals_enabled',True)
    autonomy.setdefault('billing_proposals_enabled',True)
    autonomy.setdefault('automatic_matter_confidence_min',80)
    orchestrator=c.setdefault('orchestrator',{})
    orchestrator.setdefault('enabled',True)
    orchestrator.setdefault('mail_monitor_interval_minutes',5)
    orchestrator.setdefault('mail_batch_size',20)
    orchestrator.setdefault('matter_confidence_min',85)
    orchestrator.setdefault('trigger_adapted_projects',True)
    orchestrator.setdefault('automatic_mail_drafts_enabled',True)
    orchestrator.setdefault('automatic_legal_projects_enabled',True)
    orchestrator.setdefault('propose_client_replies',True)
    orchestrator.setdefault('propose_diligences',True)
    orchestrator.setdefault('propose_billing',True)
    orchestrator.setdefault('single_notification',True)
    production=c.setdefault('production',{})
    production.setdefault('enabled',True)
    production.setdefault('interval_minutes',5)
    production.setdefault('batch_size',20)
    production.setdefault('auto_advance_playbooks',True)
    production.setdefault('retry_failed_drafts',True)
    production.setdefault('max_automatic_attempts',3)
    production.setdefault('verify_remote_deliverables',True)
    production.setdefault('verification_retry_limit',3)
    production.setdefault('useful_metrics_days',30)
    production.setdefault('automatic_internal_and_reversible',True)
    opinions=c.setdefault('opinions',{})
    opinions.setdefault('enabled',True)
    opinions.setdefault('max_authorities',30)
    opinions.setdefault('max_dossier_sources',30)
    opinions.setdefault('providers',['openlegi','goodlegal','pappers'])
    opinions.setdefault('require_verified_official_authorities',True)
    opinions.setdefault('qualitative_calibration_only',True)
    opinions.setdefault('numeric_probability_forbidden',True)
    routing=c.setdefault('model_routing',{})
    routing.setdefault('fast_model','')
    routing.setdefault('complex_model',c.get('ollama',{}).get('model',''))
    routing.setdefault('control_model',autonomy.get('control_model',''))
    routing.setdefault('fast_temperature',0)
    routing.setdefault('complex_temperature',0)
    routing.setdefault('control_temperature',0)
    c.setdefault('ai_providers',{})
    c.setdefault('lawve_extensions',{})
    gateway=c.setdefault('ai_gateway',{})
    gateway.setdefault('roundcube_enabled',True)
    gateway.setdefault('audit_content',False)
    pilot=c.setdefault('cabinet_pilotage',{})
    pilot.setdefault('enabled',True)
    pilot.setdefault('refresh_interval_minutes',30)
    pilot.setdefault('continuous_tests_interval_minutes',60)
    pilot.setdefault('workload_horizon_days',14)
    pilot.setdefault('daily_capacity_minutes',420)
    pilot.setdefault('inactive_days',90)
    pilot.setdefault('meeting_horizon_days',30)
    pilot.setdefault('unbilled_lookback_days',30)
    pilot.setdefault('group_max_items',50)
    pilot.setdefault('batch_approval_minutes',30)
    audio=c.setdefault('audio',{})
    audio.setdefault('enabled',False)
    audio.setdefault('bridge_base_url','http://127.0.0.1:9011')
    audio.setdefault('bridge_token_file','/etc/axiorhub-mail-agent/vocal-bridge-token')
    audio.setdefault('dictation_timeout_seconds',180)
    audio.setdefault('max_dictation_bytes',8000000)
    mail=c.setdefault('mail',{})
    mail.setdefault('process_seen_recent',True)
    mail.setdefault('retroactive_lookback_days',7)
    mail.setdefault('retroactive_max_candidates_per_run',80)
    workstation=c.setdefault('workstation',{})
    workstation.setdefault('roundcube_url','https://courriel.example.com/webmail/')
    workstation.setdefault('roundcube_drafts_url','https://courriel.example.com/webmail/?_task=mail&_mbox=INBOX.Drafts')
    workstation.setdefault('openwebui_url','https://ai.example.com/')
    workstation.setdefault('nextcloud_url',str(nc.get('url','https://cloud.example.com')).rstrip('/')+'/index.php/apps/files/')
    workstation.setdefault('onlyoffice_url','https://myoffice.example.com/welcome/')
    workstation.setdefault('invoice_ninja_url','')
    workstation.setdefault('pdf_tools_url','https://pdf.example.com/')
    invoice=c.setdefault('invoice_ninja',{})
    invoice.setdefault('enabled',False)
    invoice.setdefault('base_url','')
    invoice.setdefault('api_token_file','/etc/axiorhub-mail-agent/invoice-ninja-api-token')
    invoice.setdefault('timeout_seconds',30)
    invoice.setdefault('max_pages',3)
    invoice.setdefault('read_only',True)
    c.setdefault('guided_ui',{}).setdefault('enabled',True)
    document_account=c.setdefault('nextcloud_documents',{})
    if not document_account:
        document_account.update({key:nc.get(key) for key in
          ('url','username','password_file','roots','matter_roots','max_depth','max_files','max_file_bytes',
           'inventory_page_files','max_inventory_directories','max_analysis_files')
          if key in nc})
    document_account.setdefault('max_generated_file_bytes',20_000_000)
    reliability=c.setdefault('reliability393',{})
    reliability.setdefault('running_stale_hours',2)
    reliability.setdefault('pending_stale_hours',6)
    reliability.setdefault('index_fresh_hours',24)
    reliability.setdefault('verification_limit',100)
    hybrid=c.setdefault('hybrid_routing',{})
    hybrid.setdefault('mode','local')
    hybrid.setdefault('external_provider','openrouter')
    hybrid.setdefault('threshold',65)
    hybrid.setdefault('allowed_purposes',['assistant','legal_analysis','hearing','document_drafting','control'])
    hybrid.setdefault('external_client_data_approved',False)
    hybrid.setdefault('fallback_local_on_error',True)
    hybrid.setdefault('escalate_after_local_failure',True)
    hybrid.setdefault('anonymize_external',True)
    hybrid.setdefault('max_external_characters',120000)
    hybrid.setdefault('excluded_matters',[])
    hybrid.setdefault('benchmark_min_external_gain',5.0)
    updates=c.setdefault('updates',{})
    updates.setdefault('channel','stable')
    updates.setdefault('metadata_url','')
    updates.setdefault('require_signature',True)
    updates.setdefault('minisign_public_key_file','/etc/axiorhub-mail-agent/update-minisign.pub')
    return (json.dumps(c,ensure_ascii=False,indent=2)+'\n').encode()


def calm_backlog(state):
    """Cancel only obsolete maintenance work, never a user/business action."""
    path=state/'desk.sqlite3'
    if not path.exists():return 0
    db=sqlite3.connect(path,timeout=10)
    try:
        stamp=datetime.now(timezone.utc).isoformat()
        result=json.dumps({'message':'Maintenance ancienne annulée lors de la mise à jour 3.0.0.'})
        cur=db.execute('''UPDATE jobs SET status='cancelled',finished=?,result=?
          WHERE status='pending' AND kind IN ('index','index_all','refresh_brief','sync_legal_memory','memory_all','discover','sync','daily_digest','monitor_all','monitor_matter','build_daily_dashboard','autonomy_mail_sweep','orchestrator_mail_sweep','refresh_operational_memory','refresh_cabinet_pilotage','run_continuous_business_tests')''',(stamp,result))
        for key in ('index_all_enabled','daily_digest_enabled'):
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',('automation:'+key,'false'))
        db.commit();return cur.rowcount
    finally:db.close()


def schedule_bootstrap(state):
    """Queue the one-click migration without doing network work as root."""
    path=state/'desk.sqlite3'
    if not path.exists():return 0
    db=sqlite3.connect(path,timeout=10)
    try:
        columns={row[1] for row in db.execute('PRAGMA table_info(jobs)')}
        if not columns:return 0
        raw=json.dumps({'restart':'yes'},sort_keys=True);stamp=datetime.now(timezone.utc).isoformat()
        if db.execute("SELECT 1 FROM jobs WHERE kind='organize_cabinet' AND status IN ('pending','running')").fetchone():
            return 0
        if 'priority' in columns:
            db.execute('INSERT INTO jobs(kind,args,status,created,finished,result,priority) VALUES(?,?,?,?,NULL,NULL,?)',
                       ('organize_cabinet',raw,'pending',stamp,0))
        else:
            db.execute('INSERT INTO jobs(kind,args,status,created,finished,result) VALUES(?,?,?,?,NULL,NULL)',
                       ('organize_cabinet',raw,'pending',stamp))
        db.commit();return 1
    finally:db.close()


def migrate_legal_research(state):
    """Create 2.4 registries and invalidate previews lacking the new controls."""
    path=state/'desk.sqlite3'
    if not path.exists():return {'expired_projects':0,'registries_created':False}
    from agent.legal_research import SCHEMAS
    db=sqlite3.connect(path,timeout=10)
    try:
        for sql in SCHEMAS:db.executescript(sql)
        expired=0
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'document_projects_v220' in tables:
            stamp=datetime.now(timezone.utc).isoformat()
            expired=db.execute("UPDATE document_projects_v220 SET status='expired',decided=? WHERE status='pending'",(stamp,)).rowcount
        db.commit();return {'expired_projects':expired,'registries_created':True}
    finally:db.close()


def migrate_hearing_word(state):
    """Create the supervised hearing and Word registries without touching documents."""
    path=state/'desk.sqlite3'
    if not path.exists():return {'registries_created':False}
    from agent.hearing import SCHEMAS as hearing_schemas
    from agent.word_legal import SCHEMAS as word_schemas
    db=sqlite3.connect(path,timeout=10)
    try:
        for sql in [*hearing_schemas,*word_schemas]:db.executescript(sql)
        db.commit();return {'registries_created':True}
    finally:db.close()


def migrate_orchestrator_opinions(state):
    """Create 2.6 local registries; no mail or Nextcloud mutation occurs."""
    path=state/'desk.sqlite3'
    if not path.exists():return {'registries_created':False}
    from agent.orchestrator import SCHEMAS as orchestration_schemas
    from agent.opinions import SCHEMAS as opinion_schemas
    db=sqlite3.connect(path,timeout=10)
    try:
        for sql in [*orchestration_schemas,*opinion_schemas]:db.executescript(sql)
        db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",('automation:orchestrator_enabled','true'))
        db.commit();return {'registries_created':True}
    finally:db.close()


def migrate_cabinet_pilotage(state):
    """Preserve the 3.0 control-tower schema and the administrator's switch."""
    path=state/'desk.sqlite3'
    if not path.exists():return {'registries_created':False}
    db=sqlite3.connect(path,timeout=10)
    try:
        class Holder:pass
        holder=Holder();holder.db=db
        from agent.cabinet_pilot import ensure_schema
        ensure_schema(holder)
        db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)",('automation:cabinet_pilotage_enabled','true'))
        db.commit();return {'registries_created':True}
    finally:db.close()


def migrate_workstation(state):
    """Create 3.1 workstation mappings and read-only caches locally."""
    path=state/'desk.sqlite3'
    if not path.exists():return {'registries_created':False}
    migrate_cabinet_pilotage(state)
    db=sqlite3.connect(path,timeout=10)
    try:
        class Holder:pass
        holder=Holder();holder.db=db
        from agent.workstation import ensure_schema
        ensure_schema(holder)
        db.commit();return {'registries_created':True}
    finally:db.close()


# Compatibility for administrators who imported this function in local checks.
def migrate_autonomy(state):return migrate_legal_research(state)


def prune_false_discoveries(state,configured):
    """Remove only empty auto-discoveries outside the dedicated client root."""
    path=state/'registry-web.json'
    if not path.exists():return 0,''
    rows=json.loads(path.read_text());roots=configured.get('nextcloud',{}).get('matter_roots',[])
    def inside(value,root):
        value=posixpath.normpath(value);root=posixpath.normpath(root)
        return value==root or value.startswith(root.rstrip('/')+'/')
    kept=[];removed=[]
    for row in rows:
        automatic=bool(row.get('discovered_at')) and not row.get('registered_at')
        outside=not any(inside(row.get('path',''),root) for root in roots)
        if automatic and outside and not row.get('correspondents'):removed.append(row)
        else:kept.append(row)
    if not removed:return 0,''
    stat=path.stat();backups=state/'registry-backups';backups.mkdir(mode=0o700,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    backup=backups/('registry-before-3.1.0-'+stamp+'.json')
    atomic(backup,path.read_bytes(),stat.st_uid,stat.st_gid,0o600)
    atomic(path,(json.dumps(kept,ensure_ascii=False,indent=2)+'\n').encode(),stat.st_uid,stat.st_gid,stat.st_mode & 0o777)
    return len(removed),str(backup)


@contextmanager
def locked(state):
    with open(state/'run.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Un traitement est en cours. Attendre sa fin puis relancer.') from None
        yield


def upgrade(source, base=BASE, config=CONFIG, state=STATE, quiesce=None):
    """Atomically switch an authenticated release to 5.6.21."""
    verify(source)
    if not (base/'current').is_symlink() or not config.is_file():
        raise RuntimeError('Installation AxiorHub ou configuration introuvable.')
    with locked(state):
        old=(base/'current').resolve()
        release=base/'releases'/VERSION
        if old==release:
            verify(release)
            print('Version 5.6.21 déjà active ; données et configuration conservées.')
            return
        if old not in tuple(base/'releases'/version for version in SUPPORTED_PREVIOUS):
            raise RuntimeError('Cette mise à jour directe exige une version précédente comprise entre %s et %s.' % (SUPPORTED_PREVIOUS[0], SUPPORTED_PREVIOUS[-1]))
        verify(old)
        old_manifest=digest((old/'MANIFEST.sha256').read_bytes())
        if release.exists():
            verify(release)
            if (release/'MANIFEST.sha256').read_bytes()!=(source/'MANIFEST.sha256').read_bytes():
                raise RuntimeError('Une autre version 5.6.21 existe déjà.')
        for path in source.rglob('*'):
            if path.is_symlink():raise RuntimeError('Lien symbolique dans le paquet refusé.')
        if quiesce:quiesce()
        if not release.exists():
            staging=base/'releases'/('.5.6.21-'+uuid.uuid4().hex)
            try:
                shutil.copytree(source,staging,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
                for path in [staging,*staging.rglob('*')]:
                    os.chown(path,0,0)
                    os.chmod(path,0o755 if path.is_dir() else 0o644)
                verify(staging)
                os.rename(staging,release)
            finally:
                if staging.exists():shutil.rmtree(staging)
        config_stat=config.stat();backup_dir=config.parent/'backups';backup_dir.mkdir(mode=0o750,exist_ok=True)
        backup=backup_dir/('config-before-'+VERSION+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
        atomic(backup,config.read_bytes(),config_stat.st_uid,config_stat.st_gid,0o600)
        receipt={'previous_release':str(old),'previous_manifest_sha256':old_manifest,
          'config_backup':str(backup)}
        # Snapshot real SQLite files while services are stopped. Online backup
        # also incorporates committed WAL pages; copying the main file cannot.
        databases=[]
        for path in state.glob('*.sqlite3'):
            with path.open('rb') as handle:valid=handle.read(16)==b'SQLite format 3\x00'
            if not valid:continue
            dest=backup_dir/(path.stem+'-before-'+VERSION+'-'+uuid.uuid4().hex+'.sqlite3')
            source_db=sqlite3.connect(path);target_db=sqlite3.connect(dest)
            try:source_db.backup(target_db)
            finally:target_db.close();source_db.close()
            dest.chmod(0o600);databases.append(str(dest))
        receipt['database_backups']=databases
        atomic(base/'upgrade-5.6.21.json',json.dumps(receipt).encode(),0,0,0o600)
        switch(base,release)
        print('Mise à jour directe 5.6.21 terminée. Configuration, secrets, dossiers, index et état conservés.')


def rollback(base=BASE, config=CONFIG, state=STATE, quiesce=None):
    with locked(state):
        if (base/'current').resolve()!=base/'releases'/VERSION:
            raise RuntimeError('Le retour arrière exige la version 5.6.21 active.')
        receipt=json.loads((base/'upgrade-5.6.21.json').read_text())
        old=Path(receipt['previous_release'])
        if old not in tuple(base/'releases'/version for version in SUPPORTED_PREVIOUS):
            raise RuntimeError('Cible de retour arrière refusée.')
        verify(old)
        if digest((old/'MANIFEST.sha256').read_bytes())!=receipt['previous_manifest_sha256']:
            raise RuntimeError('Version précédente modifiée : arrêt.')
        if quiesce:quiesce()
        switch(base,old)
        print('Version '+old.name+' restaurée ; données et configuration conservées.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--rollback',action='store_true')
    args=parser.parse_args()
    if os.geteuid()!=0:raise SystemExit('Exécuter avec sudo python3 upgrade.py')
    os.umask(0o077)
    units=['axiorhub-mail-ui.service','axiorhub-mail-desk-worker.service','axiorhub-mail-watch.service',
           *['axiorhub-mail-desk-worker@'+str(n)+'.service' for n in range(2,5)]]
    timer='axiorhub-mail-agent.timer'
    timer_active=subprocess.run(['systemctl','is-active','--quiet',timer]).returncode==0
    if timer_active:subprocess.run(['systemctl','stop',timer],check=True)
    active=[u for u in units if subprocess.run(['systemctl','is-active','--quiet',u]).returncode==0]
    stopped=[]
    try:
        def quiesce():
            for unit in active:
                subprocess.run(['systemctl','stop',unit],check=True)
                stopped.append(unit)
        if args.rollback:
            rollback(quiesce=quiesce)
        else:
            upgrade(Path(__file__).resolve().parent,quiesce=quiesce)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit('Mise à jour interrompue : vérifier droits, espace et journal privé.') from None
    finally:
        for unit in stopped:subprocess.run(['systemctl','start',unit],check=False)
        if timer_active:subprocess.run(['systemctl','start',timer],check=False)

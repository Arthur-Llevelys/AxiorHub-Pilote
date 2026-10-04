"""Controlled autonomous production for AxiorHub 3.9.0.

This layer only automates reversible internal work: verified IMAP drafts,
reviewable document projects and new files in dedicated AxiorHub draft
folders.  It never sends an email, signs, files through RPVA, pays, invoices,
deletes or overwrites a source document.
"""
from datetime import datetime, timezone, timedelta
import json
import re

from .common import Stop, digest


PRODUCTION_JOBS = {
    'prepare_reply': 'mail_draft',
    'prepare_document_project': 'document_project',
    'create_document_files': 'document_files',
    'prepare_hearing': 'hearing_project',
    'create_hearing_files': 'hearing_files',
    'draft_act': 'act_project',
    'prepare_word_project': 'word_project',
    'create_word_files': 'word_files',
}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS production_outputs_v390(
      id TEXT PRIMARY KEY, job_id INTEGER NOT NULL, job_kind TEXT NOT NULL,
      output_kind TEXT NOT NULL, matter TEXT NOT NULL, source_id TEXT NOT NULL,
      status TEXT NOT NULL, label TEXT NOT NULL, paths TEXT NOT NULL,
      detail TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
      UNIQUE(job_id,output_kind));
    CREATE INDEX IF NOT EXISTS production_outputs_status_v390
      ON production_outputs_v390(status,updated);
    CREATE INDEX IF NOT EXISTS production_outputs_matter_v390
      ON production_outputs_v390(matter,updated);
    CREATE TABLE IF NOT EXISTS production_triggers_v390(
      id TEXT PRIMARY KEY, trigger_kind TEXT NOT NULL, source_id TEXT NOT NULL,
      matter TEXT NOT NULL, fingerprint TEXT NOT NULL, playbook_id TEXT NOT NULL,
      run_id TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL,
      UNIQUE(trigger_kind,source_id,fingerprint));
    CREATE INDEX IF NOT EXISTS production_triggers_status_v390
      ON production_triggers_v390(status,updated);
    ''')
    desk.db.commit()


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except (ValueError, TypeError):
        return default


def _matter_from_args(args, result):
    matter=str(args.get('matter') or '')
    if matter:return matter
    data=result.get('matter')
    if isinstance(data,dict):return str(data.get('id') or '')
    return str(data or '')


def _paths(result):
    rows=result.get('created_files') or result.get('future_files') or []
    paths=[]
    for item in rows:
        value=item.get('path','') if isinstance(item,dict) else str(item)
        if value and value not in paths:paths.append(value)
    return paths[:30]


def _label(kind, result):
    if kind=='prepare_reply':
        return ('Brouillon vérifié dans la messagerie' if result.get('brouillon_imap')=='verifie'
                else 'Projet de réponse à contrôler')
    labels={
      'prepare_document_project':'Projet de conclusions ou d’acte préparé',
      'create_document_files':'Documents de travail créés dans le dossier',
      'prepare_hearing':'Note et plans de plaidoirie préparés',
      'create_hearing_files':'Dossier de plaidoirie créé dans le dossier',
      'draft_act':'Projet d’acte préparé',
      'prepare_word_project':'Révision Word préparée',
      'create_word_files':'Versions Word créées dans le dossier',
    }
    return labels.get(kind,kind)


def record_job_result(desk, row, args, status, result):
    """Project a technical job into the lawyer-facing production register."""
    ensure_schema(desk);kind=row['kind']
    if kind not in PRODUCTION_JOBS:return
    result=result if isinstance(result,dict) else {}
    output_status='error' if status!='done' else 'prepared'
    if kind=='prepare_reply':
        if result.get('brouillon_imap')=='verifie':output_status='delivered'
        elif result.get('projet_prepare'):output_status='decision_required'
        else:output_status='abstained'
    elif kind.startswith('create_'):
        output_status='delivered' if status=='done' and _paths(result) else ('partial' if status=='done' else 'error')
    elif result.get('status')=='blocked':output_status='blocked'
    source=str(args.get('key') or args.get('trigger_mail_key') or
      result.get('project_id') or result.get('hearing_project_id') or row['id'])
    matter=_matter_from_args(args,result);paths=_paths(result)
    detail={
      'message':str(result.get('message') or result.get('erreur') or '')[:1200],
      'status':str(result.get('status') or status),
      'automatic':str(args.get('automatic') or '')=='yes',
      'external_action':False,
    }
    oid=digest('|'.join([str(row['id']),PRODUCTION_JOBS[kind]]));stamp=desk.now()
    desk.db.execute('''INSERT OR REPLACE INTO production_outputs_v390
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
      (oid,row['id'],kind,PRODUCTION_JOBS[kind],matter,source,output_status,
       _label(kind,result),json.dumps(paths,ensure_ascii=False),
       json.dumps(detail,ensure_ascii=False),
       (desk.db.execute('SELECT created FROM production_outputs_v390 WHERE id=?',(oid,)).fetchone() or [stamp])[0],stamp))
    desk.db.commit()


def _enqueue_internal_files(desk, kind, args, result):
    """Materialise only new files in an AxiorHub drafts folder after controls."""
    if str(args.get('automatic') or '')!='yes':return None
    autonomy=desk.c.get('autonomy',{})
    if not desk.settings('automation:automatic_internal_files_enabled',
                         autonomy.get('automatic_internal_files_enabled',True)):
        return None
    if str(result.get('status'))!='pending' or not re.fullmatch(r'\d{6}',str(result.get('confirmation_code',''))):
        return None
    control=result.get('control') or {}
    if kind=='prepare_document_project':
        if control.get('status') not in ('approved_for_confirmation','passed'):return None
        return desk.enqueue('create_document_files',{
          'project_id':result['project_id'],'confirmation_code':result['confirmation_code'],
          'destination_folder':result['destination_folder'],
          'source_path':result['source_file']['path'],'automatic':'yes'},priority=25)
    if kind=='prepare_hearing':
        if control.get('status')!='passed':return None
        return desk.enqueue('create_hearing_files',{
          'project_id':result['hearing_project_id'],'confirmation_code':result['confirmation_code'],
          'destination_folder':result['destination_folder'],
          'our_source_path':result['writings']['ours']['path'],
          'opponent_source_path':result['writings']['opponent']['path'],
          'automatic':'yes'},priority=25)
    return None


def after_job(desk, row, args, status, result):
    """Completion hook used by the queue; never raises over the original job."""
    record_job_result(desk,row,args,status,result)
    if status=='done' and row['kind'] in ('prepare_document_project','prepare_hearing'):
        follow=_enqueue_internal_files(desk,row['kind'],args,result)
        if follow:
            desk.audit('internal_files_queued_v390',{'source_job':row['id'],'file_job':follow,
              'kind':row['kind'],'external_action':False})


def _trigger_playbooks(desk, limit=12):
    ensure_schema(desk)
    from .operating380 import start_playbook
    rows=desk.db.execute("""SELECT * FROM proactive_signals
      WHERE state='open' AND matter<>'' AND category IN
      ('documents_changed','upcoming_appointment','upcoming_deadline','stale_strategy')
      ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
      due,last_seen DESC LIMIT ?""",(limit*3,)).fetchall()
    created=[];blocked=[]
    for row in rows:
        text=' '.join([row['title'],row['detail'],row['proposed_action']]).lower()
        playbook=''
        if row['category']=='documents_changed' and any(x in text for x in ('conclusion','écriture','assignation')):
            playbook='opponent_writings_v1'
        elif row['category']=='documents_changed' and any(x in text for x in ('contrat','cgv','clause','avenant')):
            playbook='contract_review_v1'
        elif row['category']=='upcoming_appointment' and any(x in text for x in ('audience','plaidoirie','tribunal','cour ')):
            playbook='litigation_hearing_v1'
        elif row['category']=='upcoming_deadline' and any(x in text for x in ('conclusion','assignation','mémoire')):
            playbook='litigation_hearing_v1'
        elif row['category']=='stale_strategy':
            playbook='opponent_writings_v1'
        if not playbook:continue
        tid=digest('|'.join([row['category'],row['id'],row['fingerprint']]))
        if desk.db.execute('SELECT 1 FROM production_triggers_v390 WHERE id=?',(tid,)).fetchone():continue
        try:
            run=start_playbook(desk,playbook,row['matter'],row['title']+' — '+row['detail'])
            stamp=desk.now();desk.db.execute('INSERT INTO production_triggers_v390 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
              (tid,row['category'],row['id'],row['matter'],row['fingerprint'],playbook,
               run['run_id'],'running',row['title'][:1000],stamp,stamp));created.append(run['run_id'])
        except Stop as ex:blocked.append({'signal':row['id'],'reason':str(ex)})
        if len(created)>=limit:break
    desk.db.commit();return created,blocked


def advance_playbooks(desk, limit=20):
    from .operating380 import advance_playbook, playbook_run
    run_ids=[r['id'] for r in desk.db.execute("""SELECT id FROM playbook_runs_v380
      WHERE status='running' ORDER BY updated LIMIT ?""",(max(1,min(int(limit),100)),))]
    advanced=[];waiting=[];errors=[]
    for rid in run_ids:
        try:
            before=playbook_run(desk,rid);after=advance_playbook(desk,rid)
            if before!=after:advanced.append(rid)
            if after['status']=='awaiting_input':waiting.append(rid)
        except Stop as ex:errors.append({'run_id':rid,'reason':str(ex)})
    return {'advanced':len(advanced),'waiting_for_lawyer':len(waiting),'errors':errors}


def cycle(desk,args=None):
    """Start and advance reversible work without waiting for button clicks."""
    ensure_schema(desk);args=args or {};limit=max(1,min(int(args.get('limit',20)),100))
    triggered,blocked=_trigger_playbooks(desk,min(limit,20))
    from .production420 import trigger_events
    event_results=trigger_events(desk,min(limit,30))
    advanced=(advance_playbooks(desk,limit) if desk.c.get('production',{}).get('auto_advance_playbooks',True)
              else {'advanced':0,'waiting_for_lawyer':0,'errors':[]})
    # The existing orchestrator owns mail classification and verified IMAP
    # append.  Queueing it here keeps one source of truth and remains idempotent.
    mail_job=None
    if desk.settings('automation:automatic_mail_drafts_enabled',
                     desk.c.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True)):
        active=desk.db.execute("""SELECT id FROM jobs WHERE kind='orchestrator_mail_sweep'
          AND status IN ('pending','running') ORDER BY id DESC LIMIT 1""").fetchone()
        mail_job=active['id'] if active else desk.enqueue('orchestrator_mail_sweep',{'limit':limit},priority=35)
    result={'playbooks_started':len(triggered),'playbooks_advanced':advanced['advanced'],
      'waiting_for_lawyer':advanced['waiting_for_lawyer'],'mail_sweep_job':mail_job,
      'blocked_triggers':blocked,'errors':advanced['errors'],
      'event_outcomes_420':event_results,
      'external_actions':0,'emails_sent':0,'rpva_filed':0,'documents_overwritten':0}
    desk.audit('production_cycle_v390',result);return result


def dashboard(desk, days=30):
    ensure_schema(desk);days=max(1,min(int(days),365));since=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    actionable=desk.db.execute("""SELECT COUNT(*) FROM work_items
      WHERE state IN ('needs_action','needs_confirmation','drafting','draft_ready')""").fetchone()[0]
    verified=desk.db.execute("""SELECT COUNT(*) FROM production_outputs_v390
      WHERE output_kind='mail_draft' AND status='verified' AND updated>=?""",(since,)).fetchone()[0]
    delivered=desk.db.execute("""SELECT COUNT(*) FROM production_outputs_v390
      WHERE status='verified' AND updated>=?""",(since,)).fetchone()[0]
    errors=desk.db.execute("""SELECT COUNT(*) FROM production_outputs_v390
      WHERE status IN ('error','missing') AND updated>=?""",(since,)).fetchone()[0]
    decisions=desk.db.execute("""SELECT COUNT(*) FROM production_outputs_v390
      WHERE status IN ('decision_required','blocked') AND updated>=?""",(since,)).fetchone()[0]
    prepared=desk.db.execute("""SELECT COUNT(*) FROM production_outputs_v390
      WHERE status='prepared' AND updated>=?""",(since,)).fetchone()[0]
    denominator=actionable+verified
    coverage=round(100*verified/denominator,1) if denominator else 100.0
    recent=[]
    for row in desk.db.execute('SELECT * FROM production_outputs_v390 ORDER BY updated DESC LIMIT 60'):
        item=dict(row);item['paths']=_json(item['paths'],[]);item['detail']=_json(item['detail'],{});recent.append(item)
    failures=[]
    for row in desk.db.execute("""SELECT job_kind,COUNT(*) count,MAX(updated) last_seen
      FROM production_outputs_v390 WHERE status IN ('error','missing') AND updated>=?
      GROUP BY job_kind ORDER BY count DESC""",(since,)):
        failures.append(dict(row))
    return {'period_days':days,'counts':{'mail_actionable':actionable,'verified_mail_drafts':verified,
      'prepared_projects':prepared,'delivered_outputs':delivered,'decision_required':decisions,'errors':errors},
      'mail_draft_coverage_percent':coverage,'recent_outputs':recent,'errors_by_job':failures,
      'safety':{'automatic_internal_work':True,'email_send_requires_confirmation':True,
        'rpva_requires_confirmation':True,'signature_requires_confirmation':True,
        'payment_requires_confirmation':True,'source_overwrite_forbidden':True},
      'limits':'La couverture mesure les courriels présents dans le registre de travail, pas tous les messages IMAP ignorés.'}


def perform(desk,kind,args):
    if kind=='production_cycle390':return cycle(desk,args)
    if kind=='advance_playbooks390':return advance_playbooks(desk,args.get('limit',20))
    raise Stop('action_inconnue')

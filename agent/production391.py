"""Observable, retryable production pipeline retained by AxiorHub 3.9.2.

The 3.9.0 producer remains the single implementation for internal drafts and
documents.  This module adds the lawyer-facing chain of custody: trigger,
matter, context, preparation, control, delivery and external validation.
"""
from datetime import datetime, timezone, timedelta
import json
import re

from .common import Stop, digest


STAGES = (
    ('detected', 'Événement détecté'),
    ('matter_identified', 'Dossier identifié'),
    ('context_ready', 'Contexte rassemblé'),
    ('prepared', 'Projet préparé'),
    ('controlled', 'Projet contrôlé'),
    ('delivered', 'Livrable disponible'),
    ('external_validation', 'Engagement à valider'),
)
PRODUCTION_JOBS = {
    'prepare_reply': 'mail_drafting',
    'prepare_document_project': 'document_drafting',
    'create_document_files': 'document_drafting',
    'prepare_hearing': 'hearing',
    'create_hearing_files': 'hearing',
    'draft_act': 'document_drafting',
    'prepare_word_project': 'document_drafting',
    'create_word_files': 'document_drafting',
}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS production_flows_v391(
      id TEXT PRIMARY KEY, job_id INTEGER NOT NULL UNIQUE, job_kind TEXT NOT NULL,
      trigger_kind TEXT NOT NULL, source_id TEXT NOT NULL, matter TEXT NOT NULL,
      status TEXT NOT NULL, stage TEXT NOT NULL, stages TEXT NOT NULL,
      model_provider TEXT NOT NULL, model_name TEXT NOT NULL, paths TEXT NOT NULL,
      last_error TEXT NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS production_flows_status_v391
      ON production_flows_v391(status,updated);
    CREATE TABLE IF NOT EXISTS production_reviews_v391(
      output_id TEXT PRIMARY KEY, decision TEXT NOT NULL, note TEXT NOT NULL,
      original TEXT NOT NULL, corrected TEXT NOT NULL, correction_id INTEGER,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    ''')
    desk.db.commit()


def _json(value, default):
    try:return json.loads(value) if value else default
    except (ValueError, TypeError):return default


def _provider(desk, kind):
    purpose=PRODUCTION_JOBS.get(kind,'assistant')
    try:
        from .model import routed_config
        cfg=routed_config(desk.c,purpose)
        return str(cfg.get('provider_id') or 'ollama'),str(cfg.get('model') or '')
    except Stop:
        return 'indisponible',''


def _stage_history(matter, status, paths, result):
    completed={'detected'}
    if matter:completed.add('matter_identified')
    if status=='done' or result:completed.add('context_ready')
    if status=='done':completed.add('prepared')
    control=result.get('control') if isinstance(result,dict) else None
    if status=='done' and (control or result.get('brouillon_imap')=='verifie' or result.get('projet_prepare')):
        completed.add('controlled')
    if status=='done' and (paths or result.get('brouillon_imap')=='verifie'):
        completed.add('delivered')
    if status=='done':completed.add('external_validation')
    return [{'id':key,'label':label,'status':'done' if key in completed else
             ('error' if status=='error' and key==next((x for x,_ in STAGES if x not in completed),'context_ready') else 'pending')}
            for key,label in STAGES]


def _current_stage(history, status):
    if status=='error':
        return next((x['id'] for x in history if x['status']=='error'),'context_ready')
    done=[x['id'] for x in history if x['status']=='done']
    return done[-1] if done else 'detected'


def after_job(desk, row, args, status, result):
    """Keep the 3.9.0 projection and add a complete observable flow."""
    from .production390 import after_job as after_390
    after_390(desk,row,args,status,result)
    kind=row['kind'];
    if kind not in PRODUCTION_JOBS:return
    ensure_schema(desk);result=result if isinstance(result,dict) else {}
    matter=str(args.get('matter') or '')
    if not matter:
        candidate=result.get('matter')
        matter=str(candidate.get('id') if isinstance(candidate,dict) else candidate or '')
    source=str(args.get('key') or args.get('trigger_mail_key') or result.get('project_id') or
               result.get('hearing_project_id') or row['id'])
    paths=[]
    for item in result.get('created_files') or result.get('future_files') or []:
        path=item.get('path','') if isinstance(item,dict) else str(item)
        if path and path not in paths:paths.append(path)
    flow_status='error' if status!='done' else ('delivered' if paths or result.get('brouillon_imap')=='verifie' else 'prepared')
    history=_stage_history(matter,status,paths,result);provider,model=_provider(desk,kind)
    error=str(result.get('erreur') or result.get('error') or '')[:1500] if status!='done' else ''
    ident=digest('flow|'+str(row['id']));stamp=desk.now()
    old=desk.db.execute('SELECT created,retry_count FROM production_flows_v391 WHERE id=?',(ident,)).fetchone()
    desk.db.execute('''INSERT OR REPLACE INTO production_flows_v391
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
      ident,row['id'],kind,str(args.get('trigger_kind') or 'job'),source,matter,flow_status,
      _current_stage(history,status),json.dumps(history,ensure_ascii=False),provider,model,
      json.dumps(paths,ensure_ascii=False),error,int(old['retry_count']) if old else 0,
      old['created'] if old else stamp,stamp))
    desk.db.commit()


def retry_failed(desk, args):
    ensure_schema(desk)
    try:job_id=int(args.get('job_id',0))
    except (TypeError,ValueError):raise Stop('operation_invalide') from None
    flow=desk.db.execute('SELECT * FROM production_flows_v391 WHERE job_id=?',(job_id,)).fetchone()
    job=desk.db.execute('SELECT kind,args,status FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not flow or not job or job['status']!='error':raise Stop('incident_non_rejouable')
    if flow['retry_count']>=3:raise Stop('nombre_reprises_depasse')
    payload=_json(job['args'],{});payload['retry_of']=job_id;payload['retry_attempt']=flow['retry_count']+1
    new_job=desk.enqueue(job['kind'],payload,priority=0)
    desk.db.execute("UPDATE production_flows_v391 SET status='retrying',retry_count=retry_count+1,updated=? WHERE job_id=?",(desk.now(),job_id))
    desk.db.commit();desk.audit('production_retry_v391',{'job_id':job_id,'new_job_id':new_job})
    return {'job_id':new_job,'retry_of':job_id,'status':'queued'}


def review_output(desk, args):
    ensure_schema(desk);output_id=str(args.get('output_id') or '')
    decision=str(args.get('decision') or '')
    if decision not in ('accepted','modified','rejected'):raise Stop('decision_livrable_invalide')
    output=desk.db.execute('SELECT * FROM production_outputs_v390 WHERE id=?',(output_id,)).fetchone()
    if not output:raise Stop('livrable_absent')
    note=str(args.get('note') or '').strip()[:3000]
    original=str(args.get('original') or output['label']).strip()[:12000]
    corrected=str(args.get('corrected') or '').strip()[:12000]
    correction_id=None
    guidance=''
    if decision=='modified':
        guidance=str(args.get('guidance') or '').strip()
        if not corrected or not guidance:raise Stop('correction_et_regle_requises')
        from .relevance370 import record_correction
        scope=str(args.get('learning_scope') or ('matter' if output['matter'] else 'general'))
        if scope=='matter' and not output['matter']:scope='general'
        source_kind=str(args.get('source_kind') or 'structure')
        if source_kind not in ('style','structure'):raise Stop('type_correction_invalide')
        learned=record_correction(desk,scope,output['matter'],source_kind,
          original,corrected,guidance)
        correction_id=learned['id']
    stamp=desk.now();old=desk.db.execute('SELECT created FROM production_reviews_v391 WHERE output_id=?',(output_id,)).fetchone()
    desk.db.execute('INSERT OR REPLACE INTO production_reviews_v391 VALUES(?,?,?,?,?,?,?,?)',
      (output_id,decision,note,original,corrected,correction_id,old['created'] if old else stamp,stamp))
    desk.db.execute('UPDATE production_outputs_v390 SET status=?,updated=? WHERE id=?',(decision,stamp,output_id))
    desk.db.commit()
    from .learning392 import record_outcome
    record_outcome(desk,output_id,decision,correction_id)
    from .learning410 import record_review
    purpose='mail_drafting' if output['output_kind']=='mail_draft' else 'document_drafting'
    rule=None
    if decision=='modified' and guidance:
        rule_scope=str(args.get('rule_scope') or ('matter' if output['matter'] else 'cabinet'))
        from .common import load_matters
        matter_record=next((m for m in load_matters(desk.c) if m.get('id')==output['matter']),{})
        automatic_scope={'matter':output['matter'],
          'client':matter_record.get('client_name',''),
          'matter_type':matter_record.get('matter_type') or matter_record.get('type','')}
        scope_value=str(args.get('scope_value') or automatic_scope.get(rule_scope,'') or '')
        rule={'scope':rule_scope,'scope_value':scope_value,
          'purpose':str(args.get('rule_purpose') or purpose),
          'rule_type':str(args.get('rule_type') or source_kind),
          'instruction':guidance}
    learned410=record_review(desk,output_id,output['matter'],purpose,decision,
      original,corrected,note,rule)
    from .production420 import record_review_influence
    record_review_influence(desk,output_id,decision,
      sum(len(x) for x in (corrected,)) if decision=='modified' else 0)
    desk.audit('production_review_v391',{'output_id':output_id,'decision':decision,'correction_id':correction_id})
    return {'output_id':output_id,'decision':decision,'correction_id':correction_id,
      'business_rule_id':learned410.get('rule_id')}


def universal_command(desk, args):
    kind=str(args.get('command_kind') or 'assistant');matter=str(args.get('matter') or '')
    instruction=str(args.get('instruction') or '').strip();due=str(args.get('due') or '')
    autonomy=str(args.get('autonomy') or 'prepare')
    if not instruction or len(instruction)>12000:raise Stop('consigne_requise_12000_caracteres_maximum')
    if matter and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',matter):raise Stop('dossier_invalide')
    context=instruction+(('\nÉchéance souhaitée : '+due) if due else '')+'\nNiveau : '+autonomy
    if kind=='assistant':
        from .integration import submit_question
        return submit_question(desk,context,matter= matter,attachment_id=str(args.get('attachment_id') or ''))
    mapping={
      'hearing':('prepare_hearing',{'matter':matter,'instruction':context,'automatic':'yes'}),
      'conclusions':('prepare_document_project',{'matter':matter,'document_type':'conclusions','instruction':context,'automatic':'yes'}),
      'research':('legal_research',{'matter':matter,'question':context}),
    }
    if kind=='playbook':
        from .operating380 import start_playbook
        playbook=str(args.get('playbook_id') or 'litigation_hearing_v1')
        run=start_playbook(desk,playbook,matter,context)
        job=desk.enqueue('advance_playbook380',{'run_id':run['run_id']},priority=0)
        return {'job_id':job,'run_id':run['run_id'],'status':'queued'}
    if kind not in mapping:raise Stop('commande_universelle_invalide')
    if not matter:raise Stop('dossier_requis')
    job_kind,payload=mapping[kind];job=desk.enqueue(job_kind,payload,priority=0)
    return {'job_id':job,'status':'queued','kind':job_kind}


def dashboard(desk, days=30):
    ensure_schema(desk)
    from .production390 import dashboard as dashboard390
    data=dashboard390(desk,days);since=(datetime.now(timezone.utc)-timedelta(days=max(1,min(int(days),365)))).isoformat()
    flows=[]
    for row in desk.db.execute('SELECT * FROM production_flows_v391 WHERE updated>=? ORDER BY updated DESC LIMIT 100',(since,)):
        item=dict(row);item['stages']=_json(item['stages'],[]);item['paths']=_json(item['paths'],[])
        output=desk.db.execute('SELECT id,label,detail,status FROM production_outputs_v390 WHERE job_id=? ORDER BY updated DESC LIMIT 1',(item['job_id'],)).fetchone()
        item['output_id']=output['id'] if output else '';item['output_label']=output['label'] if output else ''
        item['output_detail']=_json(output['detail'],{}) if output else {};item['output_status']=output['status'] if output else ''
        flows.append(item)
    data['flows']=flows;data['incidents']=[x for x in flows if x['status'] in ('error','missing')]
    data['stage_labels']=dict(STAGES)
    return data


def perform(desk,kind,args):
    if kind=='production_cycle391':
        from .production390 import cycle
        return cycle(desk,args)
    if kind=='advance_playbooks391':
        from .production390 import advance_playbooks
        return advance_playbooks(desk,args.get('limit',20))
    if kind=='retry_production391':return retry_failed(desk,args)
    if kind=='review_output391':return review_output(desk,args)
    if kind=='universal_command391':return universal_command(desk,args)
    raise Stop('action_inconnue')

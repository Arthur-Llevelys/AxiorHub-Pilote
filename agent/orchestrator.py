"""Supervised mail-to-matter orchestration for AxiorHub 2.6.0.

This module only reads already-ingested mail and local/indexed matter state.  It
may queue other *preparation* jobs, but it never sends mail, writes Nextcloud,
creates a calendar item, finalises billing, signs, files or pays anything.
"""
from datetime import datetime, timezone
import json
import re
import secrets

from .common import Stop, digest, load_matters
from .desk import report_for
from .model import Model, routed_config, validate, MAIL_ORCHESTRATION_CLASSIFICATION, MAIL_CASE_DIFFERENTIAL


SCHEMAS=("""
CREATE TABLE IF NOT EXISTS mail_orchestrations_v260(
 id TEXT PRIMARY KEY, mail_key TEXT UNIQUE NOT NULL, matter TEXT NOT NULL,
 fingerprint TEXT NOT NULL, status TEXT NOT NULL,
 classification TEXT NOT NULL, differential TEXT NOT NULL,
 reply_proposal TEXT NOT NULL, diligence_proposals TEXT NOT NULL,
 billing_proposals TEXT NOT NULL, triggered_project_kind TEXT NOT NULL,
 triggered_job_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL,
 CHECK(status IN ('pending_review','blocked','read','rejected')));
CREATE INDEX IF NOT EXISTS mail_orchestrations_v260_matter
 ON mail_orchestrations_v260(matter,updated DESC);
CREATE TABLE IF NOT EXISTS orchestration_notifications_v260(
 id TEXT PRIMARY KEY, orchestration_id TEXT UNIQUE NOT NULL,
 status TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
 created TEXT NOT NULL, updated TEXT NOT NULL,
 CHECK(status IN ('unread','read','rejected')));
""",)


def ensure_schema(desk):
    for sql in SCHEMAS:desk.db.executescript(sql)
    desk.db.commit()


def _loads(value,default):
    try:return json.loads(value) if isinstance(value,str) else value
    except (ValueError,TypeError):return default


def _matter(desk,mid):
    rows=[m for m in load_matters(desk.c) if m['id']==str(mid)]
    if len(rows)!=1:raise Stop('dossier_orchestration_absent_ou_ambigu')
    return rows[0]


def _association(desk,key,mid,report,minimum):
    """Fail closed unless the already recorded association is unique and strong."""
    row=desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?',(key,)).fetchone()
    if not row or row['matter']!=mid:return 0,'association_absente_ou_modifiee'
    scores=report.get('matter_scores') or []
    if isinstance(scores,dict):scores=[{'matter':k,'score':v} for k,v in scores.items()]
    normalized=[]
    for item in scores:
        try:normalized.append((str(item.get('matter') or item.get('id') or ''),int(item.get('score',0))))
        except (ValueError,TypeError):pass
    normalized.sort(key=lambda x:x[1],reverse=True)
    if normalized:
        if normalized[0][0]!=mid:return normalized[0][1],'meilleur_dossier_different'
        if len(normalized)>1 and normalized[1][1]>=normalized[0][1]-5:return normalized[0][1],'plusieurs_dossiers_possibles'
        if normalized[0][1]<minimum:return normalized[0][1],'confiance_dossier_insuffisante'
        return normalized[0][1],'association_unique_fiable'
    # A manually confirmed association is acceptable even if an old report did
    # not preserve scoring details.
    if str(report.get('reason','')) in ('association_confirmee','correspondant_associe','reference_unique'):
        return 100,'association_unique_fiable'
    return 0,'confiance_dossier_absente'


def _source_packet(desk,matter,key,report,memory):
    packet=[{'id':'mail-'+key[:20],'kind':'incoming_mail','path':key,
      'excerpt':json.dumps({k:report.get(k) for k in ('subject','sender','received_at','triage','summary')},ensure_ascii=False)[:12000]}]
    valid={packet[0]['id']}
    for item in (report.get('indexed_attachments') or [])[:20]:
        sid=str(item.get('source_id') or '')
        if not sid or sid in valid:continue
        packet.append({'id':sid,'kind':'mail_attachment','path':str(item.get('path') or item.get('filename') or ''),
          'excerpt':str(item.get('excerpt') or item.get('summary') or '')[:8000]});valid.add(sid)
    data=memory.get('data',{}) if isinstance(memory,dict) else {}
    state=data.get('source_state',{}) if isinstance(data,dict) else {}
    for item in (state.get('documents') or [])[:30]:
        path=str(item.get('path',''));sid='document-'+digest(path)[:20]
        if path and sid not in valid:
            packet.append({'id':sid,'kind':'matter_document_inventory','path':path,'excerpt':''});valid.add(sid)
    for item in (state.get('recent_emails') or [])[:20]:
        mkey=str(item.get('mail_key',''));sid='mail-'+mkey[:20]
        if mkey and sid not in valid:
            packet.append({'id':sid,'kind':'matter_mail_inventory','path':mkey,
              'excerpt':str(item.get('subject',''))[:500]});valid.add(sid)
    return packet


def _validate_sources(data,valid):
    ids=[]
    for field in ('source_ids','new_source_ids','existing_source_ids','contradicting_source_ids'):
        ids+=data.get(field,[])
    for row in data.get('changes',[]):ids+=row.get('source_ids',[])
    for row in data.get('proposed_diligences',[]):ids+=row.get('source_ids',[])
    for row in data.get('proposed_billing_entries',[]):ids+=row.get('source_ids',[])
    if any(x not in valid for x in ids):raise Stop('source_orchestration_invalide')


def _reply_job(desk,key,classification,differential,cfg):
    """Queue a verified draft, including a bounded retry after a prior error."""
    if not (classification.get('actionable') and classification.get('needs_reply')
            and differential.get('reply_proposal') and cfg.get('propose_client_replies',True)
            and desk.settings('automation:automatic_mail_drafts_enabled',
                              cfg.get('automatic_mail_drafts_enabled',True))):
        return None
    state=desk.db.execute('SELECT state FROM work_items WHERE mail_key=?',(key,)).fetchone()
    if state and state['state']=='draft_ready':return None
    marker='"key": "'+key+'"'
    attempts=desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='prepare_reply' AND args LIKE ?",('%'+marker+'%',)).fetchone()[0]
    maximum=max(1,min(int(desk.c.get('production',{}).get('max_automatic_attempts',3)),5))
    latest=desk.db.execute("""SELECT id,status,result FROM jobs WHERE kind='prepare_reply'
      AND args LIKE ? ORDER BY id DESC LIMIT 1""",('%'+marker+'%',)).fetchone()
    if latest and latest['status'] in ('pending','running'):return latest['id']
    if latest and latest['status']=='done':
        result=_loads(latest['result'],{})
        if result.get('brouillon_imap')=='verifie' or result.get('projet_prepare'):return None
    if latest and latest['status'] in ('error','cancelled') and not desk.c.get('production',{}).get('retry_failed_drafts',True):
        return None
    if attempts>=maximum:return None
    return desk.enqueue('prepare_reply',{'key':key,
      'instruction':'Préparer automatiquement un brouillon prudent à partir de la réponse proposée '
        'par l’orchestrateur. Ne prendre aucun engagement nouveau.','automatic':'yes'},priority=20)


def _queue_adapted_project(desk,mid,key,diff):
    kind=diff['recommended_project'];instruction=diff['project_instruction'].strip()
    if kind=='none' or not instruction:return '',None
    cfg=desk.c.get('orchestrator',{})
    if not desk.settings('automation:automatic_legal_projects_enabled',
                         cfg.get('automatic_legal_projects_enabled',True)):
        return '',None
    if kind=='document':
        dtype=diff['document_type']
        if dtype=='none':raise Stop('type_document_orchestration_absent')
        jid=desk.enqueue('prepare_document_project',{'matter':mid,'document_type':dtype,
          'instruction':instruction,'trigger_mail_key':key,'automatic':'yes'},priority=20)
        return kind,jid
    if kind=='hearing':
        jid=desk.enqueue('prepare_hearing',{'matter':mid,'instruction':instruction,
          'trigger_mail_key':key,'automatic':'yes'},priority=20)
        return kind,jid
    if kind=='legal_opinion':
        jid=desk.enqueue('prepare_legal_opinion',{'matter':mid,'question':diff['legal_question'] or instruction,
          'critique_first_instance':'yes' if diff['critique_first_instance'] else 'no',
          'trigger_mail_key':key,'automatic':'yes','run_research':'no'},priority=20)
        return kind,jid
    raise Stop('projet_orchestration_invalide')


def orchestrate_mail(desk,args,classifier=None,analyst=None):
    ensure_schema(desk);key=str(args.get('mail_key') or args.get('key') or '')
    if not re.fullmatch(r'[a-f0-9]{64}',key):raise Stop('cle_courriel_invalide')
    report=report_for(desk.c,key)
    row=desk.db.execute('SELECT matter FROM work_items WHERE mail_key=?',(key,)).fetchone()
    mid=str(args.get('matter') or (row['matter'] if row else '') or report.get('matter') or '')
    matter=_matter(desk,mid);cfg=desk.c.get('orchestrator',{})
    confidence,reason=_association(desk,key,mid,report,int(cfg.get('matter_confidence_min',85)))
    from .autonomy import operational_memory, _proposals_for_mail
    memory=operational_memory(desk,mid);packet=_source_packet(desk,matter,key,report,memory)
    fingerprint=digest(json.dumps({'mail':{k:report.get(k) for k in ('subject','sender','received_at','triage','indexed_attachments')},
      'matter':mid,'memory_version':memory.get('version',0),'association':reason},sort_keys=True,ensure_ascii=False))
    old=desk.db.execute('SELECT * FROM mail_orchestrations_v260 WHERE mail_key=?',(key,)).fetchone()
    if old and old['fingerprint']==fingerprint:
        classification=_loads(old['classification'],{});differential=_loads(old['differential'],{})
        retry_job=None if old['status']=='blocked' else _reply_job(desk,key,classification,differential,cfg)
        return preview(desk,old['id'])|{'idempotent':True,'automatic_draft_job_id':retry_job}
    valid={x['id'] for x in packet}
    from .learning392 import learning_context
    learned_triage=learning_context(desk,mid,'mail_triage')
    learned_drafting=learning_context(desk,mid,'mail_drafting')
    fast=classifier or Model(routed_config(desk.c,'mail_triage'))
    classification=fast.ask('mail_orchestration_classification',{'matter':{'id':mid,'name':matter.get('client_name','')},
      'association':{'confidence':confidence,'reason':reason},'sources':packet,
      'apprentissage_metier':learned_triage,
      'rules':{'internal_only':True,'no_external_action':True}})
    validate(classification,MAIL_ORCHESTRATION_CLASSIFICATION)
    _validate_sources(classification,valid)
    blocked=reason!='association_unique_fiable' or classification['ambiguous_matter']
    differential={'summary':'','changes':[],'new_source_ids':[],'existing_source_ids':[],
      'contradicting_source_ids':[],'recommended_project':'none','document_type':'none',
      'project_instruction':'','legal_question':'','critique_first_instance':False,
      'reply_proposal':'','proposed_diligences':[],'proposed_billing_entries':[],
      'blocking_reasons':['Association de dossier à confirmer.'] if blocked else [],'source_ids':classification['source_ids']}
    if not blocked and classification['actionable']:
        complex_model=analyst or Model(routed_config(desk.c,'mail_drafting'))
        differential=complex_model.ask('mail_case_differential',{'matter':{'id':mid,'name':matter.get('client_name',''),'path':matter['path']},
          'classification':classification,'operational_memory':memory.get('data',{}),'sources':packet,
          'apprentissage_metier':learned_drafting,
          'rules':{'proposal_only':True,'no_send':True,'no_nextcloud_write':True,
            'no_final_billing':True,'lawyer_confirmation_required':True}})
        validate(differential,MAIL_CASE_DIFFERENTIAL);_validate_sources(differential,valid)
        blocked=bool(differential['blocking_reasons'])
    project_kind='';job_id=None
    if (not blocked and classification['actionable']
            and bool(cfg.get('trigger_adapted_projects',True))):
        project_kind,job_id=_queue_adapted_project(desk,mid,key,differential)
    # Reuse the existing proposal register, but only expose the resulting items
    # in this notification.  No invoice, task or external object is created.
    before_d=desk.db.execute('SELECT COUNT(*) FROM diligence_proposals_v230 WHERE mail_key=?',(key,)).fetchone()[0]
    before_b=desk.db.execute('SELECT COUNT(*) FROM billing_proposals_v230 WHERE mail_key=?',(key,)).fetchone()[0]
    _proposals_for_mail(desk,{**report,'key':key,'matter':mid},
      bool(cfg.get('propose_diligences',True)),bool(cfg.get('propose_billing',True)))
    diligence=[dict(x) for x in desk.db.execute("SELECT id,kind,title,description,estimated_minutes,status FROM diligence_proposals_v230 WHERE mail_key=? AND status='pending' ORDER BY updated",(key,))]
    billing=[dict(x) for x in desk.db.execute("SELECT id,label,estimated_minutes,requires_time_confirmation,status FROM billing_proposals_v230 WHERE mail_key=? AND status='pending' ORDER BY updated",(key,))]
    oid=old['id'] if old else secrets.token_hex(16);created=old['created'] if old else desk.now();status='blocked' if blocked else 'pending_review'
    reply_job_id=None if blocked else _reply_job(desk,key,classification,differential,cfg)
    values=(oid,key,mid,fingerprint,status,json.dumps(classification,ensure_ascii=False),json.dumps(differential,ensure_ascii=False),
      json.dumps({'body':differential['reply_proposal'] if cfg.get('propose_client_replies',True) else '',
        'draft_only':True,'sent':False,'automatic_draft_job_id':reply_job_id},ensure_ascii=False),
      json.dumps(diligence,ensure_ascii=False),json.dumps(billing,ensure_ascii=False),project_kind,job_id,created,desk.now())
    desk.db.execute('INSERT OR REPLACE INTO mail_orchestrations_v260 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
    nid=digest('notification|'+oid);title=(matter.get('client_name') or mid)+' — courriel analysé'
    summary=differential['summary'] or classification['reason']
    nold=desk.db.execute('SELECT created,status FROM orchestration_notifications_v260 WHERE orchestration_id=?',(oid,)).fetchone()
    nstatus=(nold['status'] if nold and nold['status'] in ('read','rejected') else 'unread')
    desk.db.execute('INSERT OR REPLACE INTO orchestration_notifications_v260 VALUES(?,?,?,?,?,?,?)',
      (nid,oid,nstatus,title,summary[:2000],nold['created'] if nold else desk.now(),desk.now()))
    desk.db.commit();desk.audit('mail_orchestrated',{'orchestration':oid,'mail_key':key,'matter':mid,
      'status':status,'project_kind':project_kind,'job_id':job_id,'single_notification':nid,
      'automatic_draft_job_id':reply_job_id,
      'diligence_created':len(diligence)-before_d,'billing_created':len(billing)-before_b,
      'external_actions':0})
    return preview(desk,oid)|{'idempotent':False}


def sweep(desk,args):
    ensure_schema(desk)
    from .integration import sync_work_items
    sync_work_items(desk);cfg=desk.c.get('orchestrator',{})
    limit=max(1,min(int(args.get('limit',cfg.get('mail_batch_size',20))),100))
    rows=desk.db.execute("""SELECT w.mail_key FROM work_items w
      LEFT JOIN mail_orchestrations_v260 o ON o.mail_key=w.mail_key
      WHERE w.matter<>'' AND w.source_status NOT IN ('ignored','appending','append_uncertain')
      ORDER BY CASE WHEN o.mail_key IS NULL THEN 0 ELSE 1 END,w.received DESC LIMIT ?""",(limit*5,)).fetchall()
    done=[];warnings=[]
    for row in rows:
        if len(done)>=limit:break
        try:done.append(orchestrate_mail(desk,{'mail_key':row['mail_key']}))
        except Stop as ex:warnings.append({'mail_key':row['mail_key'],'reason':str(ex)})
    return {'orchestrated':len(done),'notifications':len(done),'items':done,'warnings':warnings,
      'external_actions':0,'documents_created':0,'emails_sent':0,'billing_finalized':0}


def preview(desk,oid):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(oid)):raise Stop('orchestration_invalide')
    row=desk.db.execute('SELECT * FROM mail_orchestrations_v260 WHERE id=?',(oid,)).fetchone()
    if not row:raise Stop('orchestration_absente')
    notification=desk.db.execute('SELECT * FROM orchestration_notifications_v260 WHERE orchestration_id=?',(oid,)).fetchone()
    return {'orchestration_id':oid,'mail_key':row['mail_key'],'matter':row['matter'],'status':row['status'],
      'classification':_loads(row['classification'],{}),'differential':_loads(row['differential'],{}),
      'reply_proposal':_loads(row['reply_proposal'],{}),'diligence_proposals':_loads(row['diligence_proposals'],[]),
      'billing_proposals':_loads(row['billing_proposals'],[]),'triggered_project_kind':row['triggered_project_kind'],
      'triggered_job_id':row['triggered_job_id'],'notification':dict(notification) if notification else None,
      'created':row['created'],'updated':row['updated'],
      'safety':{'proposal_only':True,'documents_created':False,'emails_sent':False,
        'billing_finalized':False,'calendar_changed':False,'rpva_filed':False}}


def notifications(desk,status='unread',limit=100):
    ensure_schema(desk);limit=max(1,min(int(limit),300));params=[];where=''
    if status!='all':
        if status not in ('unread','read','rejected'):raise Stop('etat_notification_invalide')
        where=' WHERE n.status=?';params.append(status)
    params.append(limit);rows=[]
    for row in desk.db.execute('''SELECT n.*,o.matter,o.mail_key,o.status AS orchestration_status,
      o.triggered_project_kind,o.triggered_job_id FROM orchestration_notifications_v260 n
      JOIN mail_orchestrations_v260 o ON o.id=n.orchestration_id'''+where+' ORDER BY n.updated DESC LIMIT ?',params):
        rows.append(dict(row))
    return {'notifications':rows,'single_notification_per_mail':True}


def review_notification(desk,args):
    ensure_schema(desk);nid=str(args.get('notification_id',''));status=str(args.get('status',''))
    if not re.fullmatch(r'[a-f0-9]{64}',nid) or status not in ('read','rejected'):raise Stop('revue_notification_invalide')
    row=desk.db.execute('SELECT orchestration_id FROM orchestration_notifications_v260 WHERE id=?',(nid,)).fetchone()
    if not row:raise Stop('notification_absente')
    desk.db.execute('UPDATE orchestration_notifications_v260 SET status=?,updated=? WHERE id=?',(status,desk.now(),nid))
    desk.db.execute("UPDATE mail_orchestrations_v260 SET status=?,updated=? WHERE id=?",
      ('read' if status=='read' else 'rejected',desk.now(),row['orchestration_id']))
    desk.db.commit();return {'notification_id':nid,'status':status,'external_actions':0}


def perform(desk,kind,args):
    if kind=='orchestrate_mail':return orchestrate_mail(desk,args)
    if kind=='orchestrator_mail_sweep':return sweep(desk,args)
    if kind=='review_orchestration_notification':return review_notification(desk,args)
    raise Stop('action_inconnue')

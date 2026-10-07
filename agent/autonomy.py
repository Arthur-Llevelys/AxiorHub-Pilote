"""Bounded autonomous preparation for AxiorHub 2.4.0.

The orchestrator observes already processed mail and indexed matter sources.  It
may refresh local memory and prepare reviewable proposals, but it cannot send a
message, write a Nextcloud document, create an invoice, file an act, or make a
payment.  Those boundaries are deliberately enforced below rather than left to
the language model.
"""
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath
import re

from .common import Stop, digest, fold, load_matters
from .desk import report_for
from .index import DocumentIndex


PROPOSAL_STATES={'pending','accepted','dismissed'}
DOCUMENT_TERMS={
  'conclusions':('conclusion','conclusions','conclure'),
  'assignation':('assignation','assigner'),
  'cgv':('cgv','conditions generales de vente'),
  'contrat':('contrat','avenant','convention'),
  'charte_rgpd':('rgpd','donnees personnelles','charte'),
  'bcp':('bordereau','communication de pieces','liste de pieces'),
  'courrier':('courrier','mise en demeure'),
}

SCHEMAS=('''
    CREATE TABLE IF NOT EXISTS autonomy_mail_observations_v230(
      mail_key TEXT PRIMARY KEY, matter TEXT NOT NULL, fingerprint TEXT NOT NULL,
      report_status TEXT NOT NULL, attachment_count INTEGER NOT NULL,
      document_project_id TEXT NOT NULL, observed TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_controls_v230(
      project_id TEXT PRIMARY KEY, matter TEXT NOT NULL, status TEXT NOT NULL,
      score INTEGER NOT NULL, checks TEXT NOT NULL, blocking_reasons TEXT NOT NULL,
      warnings TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS document_project_steps_v230(
      project_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
      fingerprint TEXT NOT NULL, data TEXT NOT NULL, updated TEXT NOT NULL,
      PRIMARY KEY(project_id,stage));
    CREATE TABLE IF NOT EXISTS document_creation_files_v230(
      project_id TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL,
      sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, content_type TEXT NOT NULL,
      updated TEXT NOT NULL, PRIMARY KEY(project_id,path));
    CREATE TABLE IF NOT EXISTS matter_operational_memory_v230(
      matter TEXT PRIMARY KEY, version INTEGER NOT NULL, fingerprint TEXT NOT NULL,
      data TEXT NOT NULL, sources TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS diligence_proposals_v230(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, mail_key TEXT NOT NULL,
      kind TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
      estimated_minutes INTEGER NOT NULL, evidence TEXT NOT NULL,
      status TEXT NOT NULL, fingerprint TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS billing_proposals_v230(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, mail_key TEXT NOT NULL,
      label TEXT NOT NULL, estimated_minutes INTEGER NOT NULL,
      requires_time_confirmation INTEGER NOT NULL, amount_cents INTEGER,
      evidence TEXT NOT NULL, status TEXT NOT NULL, fingerprint TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS autonomy_observations_matter
      ON autonomy_mail_observations_v230(matter,updated DESC);
    CREATE INDEX IF NOT EXISTS operational_memory_updated
      ON matter_operational_memory_v230(updated DESC);
    CREATE INDEX IF NOT EXISTS diligence_pending
      ON diligence_proposals_v230(status,updated DESC);
    CREATE INDEX IF NOT EXISTS billing_pending
      ON billing_proposals_v230(status,updated DESC);
    CREATE INDEX IF NOT EXISTS document_steps_status
      ON document_project_steps_v230(status,updated DESC);
    CREATE INDEX IF NOT EXISTS document_creation_status
      ON document_creation_files_v230(project_id,status);
''',)


def ensure_schema(desk):
    for sql in SCHEMAS:desk.db.executescript(sql)
    desk.db.commit()


def _loads(value,default):
    try:return json.loads(value) if value else default
    except (ValueError,TypeError):return default


def _active(desk,mid):
    row=desk.db.execute('SELECT state FROM matter_portfolio WHERE matter=?',(mid,)).fetchone()
    # A missing portfolio row is accepted for compatibility while bootstrap is
    # still running. Explicitly dormant or archived matters are never processed.
    return not row or row['state'] in ('active','to_confirm')


def _matter_confidence(desk,key,mid,report,minimum):
    """Require one high-confidence matter before automatic document drafting."""
    if (report.get('triage') or {}).get('ambiguous') or report.get('role_confirmation_required'):
        return 0,'association_ambigue'
    scores=[x for x in report.get('matter_scores',[]) if isinstance(x,dict)]
    selected=next((x for x in scores if str(x.get('matter',''))==mid),None)
    score=int(selected.get('score',0)) if selected else 0
    others=sorted((int(x.get('score',0)) for x in scores if str(x.get('matter',''))!=mid),reverse=True)
    if score and others and others[0]>=score-10:return score,'plusieurs_dossiers_proches'
    if not score:
        link=desk.db.execute('''SELECT confidence,status FROM portfolio_mail_links
          WHERE mail_key=? AND matter=? AND status IN ('automatic','confirmed')
          ORDER BY confidence DESC LIMIT 1''',(key,mid)).fetchone()
        if link:score=int(link['confidence'])
    if score<minimum:return score,'confiance_dossier_insuffisante'
    return score,'association_unique_fiable'


def _document_type(report,index,mid):
    text=fold(' '.join([str(report.get('subject','')),
      str((report.get('triage') or {}).get('reason','')),
      ' '.join((report.get('triage') or {}).get('search_terms',[]))]))
    direct=[]
    for kind,terms in DOCUMENT_TERMS.items():
        if any(term in text for term in terms):direct.append(kind)
    if len(direct)==1:return direct[0]
    if len(direct)>1:return ''
    # If the message asks for legal/document work, infer only from one unique
    # family of existing filenames. Ambiguity intentionally produces no project.
    intent=(report.get('triage') or {}).get('intent','')
    if intent not in ('documents','legal','strategy'):return ''
    names=' '.join(fold(PurePosixPath(row[0]).name) for row in index.db.execute(
      "SELECT path FROM docs WHERE matter=? AND error='' ORDER BY modified DESC LIMIT 80",(mid,)))
    found=[kind for kind,terms in DOCUMENT_TERMS.items()
           if kind!='courrier' and any(term in names for term in terms)]
    return found[0] if len(found)==1 else ''


def _upsert_diligence(desk,mid,key,kind,title,description,minutes,evidence):
    fp=digest('|'.join([mid,key,kind,json.dumps(evidence,sort_keys=True,ensure_ascii=False)]))
    pid=digest('diligence|'+fp);stamp=desk.now()
    old=desk.db.execute('SELECT status,created FROM diligence_proposals_v230 WHERE id=?',(pid,)).fetchone()
    desk.db.execute('''INSERT OR REPLACE INTO diligence_proposals_v230
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(pid,mid,key,kind,title[:500],description[:3000],
      max(0,min(int(minutes),480)),json.dumps(evidence,ensure_ascii=False),
      old['status'] if old else 'pending',fp,old['created'] if old else stamp,stamp))
    return not bool(old)


def _upsert_billing(desk,mid,key,label,minutes,evidence):
    fp=digest('|'.join([mid,key,label,json.dumps(evidence,sort_keys=True,ensure_ascii=False)]))
    pid=digest('billing|'+fp);stamp=desk.now()
    old=desk.db.execute('SELECT status,created FROM billing_proposals_v230 WHERE id=?',(pid,)).fetchone()
    # No amount is ever inferred. Queue duration is merely a reviewable estimate
    # and is explicitly marked as requiring the lawyer's confirmation.
    desk.db.execute('''INSERT OR REPLACE INTO billing_proposals_v230
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(pid,mid,key,label[:500],
      max(0,min(int(minutes),480)),1,None,json.dumps(evidence,ensure_ascii=False),
      old['status'] if old else 'pending',fp,old['created'] if old else stamp,stamp))
    return not bool(old)


def _proposals_for_mail(desk,report,diligence_enabled=True,billing_enabled=True):
    mid=str(report.get('matter') or '');key=str(report.get('key') or '')
    triage=report.get('triage') or {};intent=str(triage.get('intent',''))
    attachments=report.get('indexed_attachments') or []
    evidence={'mail_key':key,'subject':str(report.get('subject',''))[:500],
      'received_at':str(report.get('received_at','')),
      'attachment_source_ids':[str(x.get('source_id','')) for x in attachments if x.get('source_id')]}
    created_d=created_b=0
    if diligence_enabled and triage.get('needs_reply'):
        minutes=45 if intent in ('legal','strategy') else 20
        created_d+=_upsert_diligence(desk,mid,key,'prepare_reply',
          'Préparer et contrôler une réponse au courriel',
          'Projet interne seulement ; destinataires, contenu et envoi restent à valider.',minutes,evidence)
    if diligence_enabled and attachments:
        minutes=min(90,15+10*len(attachments))
        created_d+=_upsert_diligence(desk,mid,key,'review_attachments',
          'Analyser les nouvelles pièces jointes',
          str(len(attachments))+' pièce(s) jointe(s) indexée(s) à qualifier et rapprocher du dossier.',minutes,evidence)
    if diligence_enabled and intent in ('legal','strategy'):
        created_d+=_upsert_diligence(desk,mid,key,'legal_review',
          'Contrôler la question juridique ou stratégique',
          'Préparer une recherche anonymisée puis vérifier les références sur une source officielle.',45,evidence)
    estimate=(15+10*len(attachments))+(20 if triage.get('needs_reply') else 0)
    if billing_enabled and estimate:
        created_b+=_upsert_billing(desk,mid,key,
          'Analyse du courriel'+(' et de '+str(len(attachments))+' pièce(s) jointe(s)' if attachments else ''),
          min(120,estimate),evidence)
    return created_d,created_b


def observe_mail(desk,args):
    """Create bounded internal proposals from new, already processed mail."""
    ensure_schema(desk)
    from .integration import sync_work_items
    sync_work_items(desk)
    cfg=desk.c.get('autonomy',{})
    if not desk.settings('automation:autonomy_enabled',cfg.get('enabled',True)):
        return {'mail_observed':0,'document_previews_queued':0,
          'diligence_proposals_created':0,'billing_proposals_created':0,
          'memory_refresh_queued':0,'warnings':['Autonomie désactivée.']}
    diligence_enabled=desk.settings('automation:diligence_proposals_enabled',cfg.get('diligence_proposals_enabled',True))
    billing_enabled=desk.settings('automation:billing_proposals_enabled',cfg.get('billing_proposals_enabled',True))
    preview_enabled=(desk.settings('automation:automatic_document_previews_enabled',
        cfg.get('automatic_document_previews_enabled',True)) and
        desk.settings('automation:automatic_legal_projects_enabled',
        desk.c.get('orchestrator',{}).get('automatic_legal_projects_enabled',True)))
    from . import autonomy480
    preview_enabled=preview_enabled and autonomy480.level(desk,'acte_courrier')!='propose'
    limit=max(1,min(int(cfg.get('mail_batch_size',20)),100))
    index=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
    scan=max(500,limit*10)
    # 5.6.11 : courriels récents seulement (fenêtre réglable), jamais ceux envoyés par le cabinet lui-même.
    from .balayage5611 import recent_work_items
    rows=recent_work_items(desk,cfg,'autonomy',scan)
    observed=projects=diligences=billings=0;memory=set();warnings=[]
    for row in rows:
        if observed>=limit:break
        mid=row['matter'];key=row['mail_key']
        if not _active(desk,mid):continue
        try:report=report_for(desk.c,key)
        except Stop:continue
        report['key']=key;report['matter']=mid
        confidence,confidence_reason=_matter_confidence(desk,key,mid,report,int(
          cfg.get('automatic_matter_confidence_min',80)))
        fingerprint=digest(json.dumps({'matter':mid,'confidence':confidence,
          'confidence_reason':confidence_reason,**{k:report.get(k) for k in
          ('status','reason','received_at','triage','indexed_attachments','matter_scores')}},
          sort_keys=True,ensure_ascii=False))
        old=desk.db.execute('SELECT fingerprint,document_project_id,updated FROM autonomy_mail_observations_v230 WHERE mail_key=?',(key,)).fetchone()
        if old and old['fingerprint']==fingerprint:
            retry=False;queued=str(old['document_project_id'] or '')
            if queued.startswith('queued:') and queued[7:].isdigit():
                job=desk.db.execute('SELECT status FROM jobs WHERE id=?',(int(queued[7:]),)).fetchone()
                try:old_age=(datetime.now(timezone.utc)-datetime.fromisoformat(old['updated'])).total_seconds()
                except (ValueError,TypeError):old_age=0
                retry=bool(job and job['status'] in ('error','cancelled') and old_age>=300)
            if not retry:continue
        new_d,new_b=_proposals_for_mail(desk,report,diligence_enabled,billing_enabled);diligences+=new_d;billings+=new_b
        project_id=old['document_project_id'] if old else ''
        dtype=_document_type(report,index,mid)
        attachments=report.get('indexed_attachments') or []
        if (preview_enabled and dtype and confidence_reason=='association_unique_fiable' and
            (attachments or (report.get('triage') or {}).get('intent') in ('legal','strategy','documents'))):
            instruction=('Prévisualisation automatique déclenchée par le courriel « '+
              str(report.get('subject',''))[:300]+' ». Examiner les nouveaux éléments, proposer uniquement '
              'les modifications sourcées et signaler toute décision réservée à l’avocat.')
            try:
                job=desk.enqueue('prepare_document_project',{'document_type':dtype,'matter':mid,
                  'instruction':instruction,'trigger_mail_key':key,'automatic':'yes',
                  'trigger_confidence':confidence,'trigger_reason':confidence_reason},priority=20)
                project_id='queued:'+str(job);projects+=1
            except Stop as ex:warnings.append({'mail_key':key,'reason':str(ex)})
        elif preview_enabled and dtype and confidence_reason!='association_unique_fiable':
            warnings.append({'mail_key':key,'reason':confidence_reason,'confidence':confidence})
        stamp=desk.now();desk.db.execute('''INSERT OR REPLACE INTO autonomy_mail_observations_v230
          VALUES(?,?,?,?,?,?,?,?)''',(key,mid,fingerprint,str(report.get('status','')),
          len(attachments),project_id,old and desk.db.execute(
            'SELECT observed FROM autonomy_mail_observations_v230 WHERE mail_key=?',(key,)).fetchone()[0] or stamp,stamp))
        observed+=1;memory.add(mid)
    desk.db.commit()
    for mid in sorted(memory):
        try:desk.enqueue('refresh_operational_memory',{'matter':mid},priority=55)
        except Stop:pass
    result={'mail_observed':observed,'document_previews_queued':projects,
      'diligence_proposals_created':diligences,'billing_proposals_created':billings,
      'memory_refresh_queued':len(memory),'warnings':warnings}
    desk.audit('autonomy_mail_sweep',result);return result


def record_automatic_project(desk,args,result):
    ensure_schema(desk);key=str(args.get('trigger_mail_key',''))
    pid=str(result.get('project_id',''))
    if not key or not re.fullmatch(r'[a-f0-9]{32}',pid):return
    desk.db.execute('UPDATE autonomy_mail_observations_v230 SET document_project_id=?,updated=? WHERE mail_key=?',
      (pid,desk.now(),key));desk.db.commit()


def control_document_project(desk,data,model):
    """Run deterministic checks plus a separate local-model review pass."""
    ensure_schema(desk);draft=data['draft'];packet=data.get('_source_packet',[]);valid={x['id'] for x in packet}
    used=[]
    for field in ('source_ids','introduction_source_ids'):used+=draft.get(field,[])
    for item in draft.get('sections',[])+draft.get('requests',[])+draft.get('exhibits_referenced',[]):
        used+=item.get('source_ids',[])
    legal={x['id']:x for x in data.get('legal_research',[])}
    exhibit_labels=[fold(x.get('label','')) for x in draft.get('exhibits_referenced',[]) if x.get('label')]
    baseline={
      'all_source_ids_known':all(x in valid for x in used),
      'legal_citations_official':all(not x.startswith('legal-') or legal.get(x,{}).get('officially_verified') for x in used),
      'cited_exhibits_have_sources':all(bool(x.get('source_ids')) and all(s in valid for s in x.get('source_ids',[]))
        for x in draft.get('exhibits_referenced',[])),
      'no_duplicate_exhibit_labels':len(exhibit_labels)==len(set(exhibit_labels)),
      'future_paths_unique':len(data.get('future_files',[]))==len(set(data.get('future_files',[]))),
      'new_files_only':bool(data.get('safety',{}).get('new_files_only')),
      'source_snapshot_present':bool(data.get('source',{}).get('sha256')),
      'destination_inside_matter':str(data.get('destination_folder','')).startswith(str(data.get('matter',{}).get('path','')).rstrip('/')+'/'),
      'external_queries_anonymized':bool(data.get('safety',{}).get('external_queries_anonymized')),
    }
    from .legal_research import deterministic_control
    strengthened=deterministic_control(desk,data,packet,data['proposal_id'])
    deterministic={**baseline,**strengthened['checks']}
    public_sources=[{'id':x['id'],'kind':x.get('kind',''),'path':x.get('path',''),
      'officially_verified':x.get('officially_verified',False),'excerpt':x.get('excerpt','')[:2500]}
      for x in data.get('_source_packet',[])[:40]]
    review=model.ask('document_control',{'document_type':data['document_type'],
      'instruction_avocat':data['instruction'],'draft':draft,'sources':public_sources,
      'deterministic_checks':deterministic,
      'rules':{'no_external_action':True,'new_version_only':True,
        'official_legal_citations_only':True,'lawyer_review_required':True}})
    mandatory=['source_traceability','legal_citations_official','exhibit_consistency',
      'requests_supported','motifs_dispositif_coherent','paragraph_numbering_consistent',
      'exhibit_numbering_consistent','cited_exhibits_present',
      'internal_references_consistent','procedural_identity_consistent',
      'external_queries_anonymized','no_claim_of_execution','ignores_embedded_instructions']
    mandatory.append('requires_lawyer')
    score=sum(bool(x) for x in deterministic.values())+sum(bool(review[x]) for x in mandatory)
    maximum=len(deterministic)+len(mandatory)
    combined_reasons=list(dict.fromkeys(strengthened['blocking_reasons']+review['blocking_reasons']))
    blocked=(not all(deterministic.values()) or not all(review[x] for x in mandatory)
             or bool(combined_reasons))
    result={'status':'blocked' if blocked else 'approved_for_confirmation',
      'score':round(100*score/maximum),'deterministic_checks':deterministic,
      'model_checks':{x:review[x] for x in mandatory},
      'requires_lawyer':True,'blocking_reasons':combined_reasons,
      'warnings':list(dict.fromkeys(strengthened['warnings']+review['warnings']))}
    desk.db.execute('''INSERT OR REPLACE INTO deterministic_controls_v240
      VALUES(?,?,?,?,?,?,?)''',(data['proposal_id'],data['matter']['id'],result['status'],
      json.dumps(deterministic,ensure_ascii=False),json.dumps(combined_reasons,ensure_ascii=False),
      json.dumps(result['warnings'],ensure_ascii=False),desk.now()));desk.db.commit()
    return result


def save_control(desk,pid,mid,control):
    ensure_schema(desk)
    checks={'deterministic':control['deterministic_checks'],'model':control['model_checks']}
    desk.db.execute('INSERT OR REPLACE INTO document_controls_v230 VALUES(?,?,?,?,?,?,?,?)',
      (pid,mid,control['status'],control['score'],json.dumps(checks,ensure_ascii=False),
       json.dumps(control['blocking_reasons'],ensure_ascii=False),
       json.dumps(control['warnings'],ensure_ascii=False),desk.now()))
    desk.db.commit()


def _memory_sources(desk,mid):
    index=DocumentIndex(desk.c['state_dir'])
    documents=[{'path':r[0],'etag':r[1] or '','modified':r[2] or '','error':r[3] or ''}
      for r in index.db.execute('SELECT path,etag,modified,error FROM docs WHERE matter=? ORDER BY modified DESC LIMIT 100',(mid,))]
    emails=[dict(r) for r in desk.db.execute('''SELECT mail_key,subject,sender,received,state
      FROM work_items WHERE matter=? ORDER BY received DESC LIMIT 30''',(mid,))]
    timeline=[dict(r) for r in desk.db.execute('''SELECT id,event_type,event_at,title,status,source_id
      FROM timeline_events WHERE matter=? AND status<>'stale' ORDER BY event_at DESC LIMIT 80''',(mid,))]
    tasks=[dict(r) for r in desk.db.execute('SELECT id,title,due,status FROM tasks WHERE matter=? ORDER BY due LIMIT 50',(mid,))]
    memory=[dict(r) for r in desk.db.execute('''SELECT id,record_type,title,content,status,confidence,event_date,sources
      FROM legal_memory_records WHERE matter=? AND status NOT IN ('archived','disputed') ORDER BY updated DESC LIMIT 100''',(mid,))]
    conflicts=[dict(r) for r in desk.db.execute("SELECT id,record_type,left_record,right_record,reason,status FROM legal_memory_conflicts WHERE matter=? AND status='open' ORDER BY created DESC LIMIT 30",(mid,))]
    projects=[dict(r) for r in desk.db.execute('''SELECT id,document_type,status,created,expires
      FROM document_projects_v220 WHERE matter=? ORDER BY created DESC LIMIT 30''',(mid,))]
    return documents,emails,timeline,tasks,memory,conflicts,projects


def refresh_memory(desk,args):
    ensure_schema(desk);mid=str(args.get('matter',''))
    matter=next((x for x in load_matters(desk.c) if x['id']==mid),None)
    if not matter:raise Stop('dossier_absent')
    documents,emails,timeline,tasks,memory,conflicts,projects=_memory_sources(desk,mid)
    data={'matter':{'id':mid,'name':matter.get('client_name',''),'path':matter['path']},
      'source_state':{'documents':documents,'recent_emails':emails},
      'procedural_state':{'timeline':timeline,'tasks':tasks},
      'validated_and_proposed_memory':memory,'open_conflicts':conflicts,
      'document_projects':projects,
      'pending_diligences':[dict(r) for r in desk.db.execute("SELECT id,kind,title,estimated_minutes,evidence,status FROM diligence_proposals_v230 WHERE matter=? AND status='pending' ORDER BY updated DESC LIMIT 30",(mid,))],
      'pending_billing':[dict(r) for r in desk.db.execute("SELECT id,label,estimated_minutes,requires_time_confirmation,evidence,status FROM billing_proposals_v230 WHERE matter=? AND status='pending' ORDER BY updated DESC LIMIT 30",(mid,))],
      'limits':['Mémoire interne dérivée de sources indexées et potentiellement incomplètes.',
        'Les éléments proposés ne deviennent pas des faits validés sans contrôle de l’avocat.']}
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True);fp=digest(raw)
    old=desk.db.execute('SELECT version,fingerprint FROM matter_operational_memory_v230 WHERE matter=?',(mid,)).fetchone()
    if old and old['fingerprint']==fp:return {'matter':mid,'updated':False,'version':old['version']}
    version=(old['version'] if old else 0)+1
    sources=sorted({x.get('path','') for x in documents if x.get('path')}|
      {x.get('mail_key','') for x in emails if x.get('mail_key')}|
      {x.get('source_id','') for x in timeline if x.get('source_id')})
    desk.db.execute('INSERT OR REPLACE INTO matter_operational_memory_v230 VALUES(?,?,?,?,?,?)',
      (mid,version,fp,raw,json.dumps(sources,ensure_ascii=False),desk.now()))
    desk.db.commit();desk.audit('operational_memory_refreshed',{'matter':mid,'version':version})
    return {'matter':mid,'updated':True,'version':version,'source_count':len(sources)}


def operational_memory(desk,mid):
    ensure_schema(desk)
    row=desk.db.execute('SELECT * FROM matter_operational_memory_v230 WHERE matter=?',(mid,)).fetchone()
    initialized=False
    if not row:
        # A missing derived snapshot is a normal first-use state, not a bad
        # request.  Build it synchronously from the already indexed/local
        # sources so Open WebUI always receives a useful, typed response.
        refresh_memory(desk,{'matter':mid});initialized=True
        row=desk.db.execute('SELECT * FROM matter_operational_memory_v230 WHERE matter=?',(mid,)).fetchone()
    if not row:raise Stop('memoire_operationnelle_indisponible')
    data=_loads(row['data'],{});sources=_loads(row['sources'],[])
    source_state=data.get('source_state',{}) if isinstance(data,dict) else {}
    documents=source_state.get('documents',[]) if isinstance(source_state,dict) else []
    emails=source_state.get('recent_emails',[]) if isinstance(source_state,dict) else []
    empty=not documents and not emails
    warning='Mémoire de travail interne ; chaque élément reste subordonné à sa source et à son statut.'
    if empty:
        warning+=' Aucun document ni courriel associé n’est encore indexé pour ce dossier ; cela ne signifie pas que le dossier Nextcloud est vide.'
    return {'matter':mid,'version':row['version'],'updated':row['updated'],
      'initialized_now':initialized,'empty_index':empty,'data':data,'sources':sources,
      'warning':warning}


def _rows(desk,table,status='pending',matter='',limit=100):
    ensure_schema(desk);limit=max(1,min(int(limit),300));where=[];params=[]
    if status and status!='all':
        if status not in PROPOSAL_STATES:raise Stop('etat_proposition_invalide')
        where.append('status=?');params.append(status)
    if matter:where.append('matter=?');params.append(matter)
    sql='SELECT * FROM '+table+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY updated DESC LIMIT ?'
    params.append(limit);result=[]
    for row in desk.db.execute(sql,params):
        item=dict(row);item['evidence']=_loads(item.get('evidence'),{});result.append(item)
    return result


def diligence_proposals(desk,status='pending',matter='',limit=100):
    return _rows(desk,'diligence_proposals_v230',status,matter,limit)


def billing_proposals(desk,status='pending',matter='',limit=100):
    return _rows(desk,'billing_proposals_v230',status,matter,limit)


def change_proposal(desk,args):
    ensure_schema(desk);kind=str(args.get('proposal_kind',''));pid=str(args.get('proposal',''))
    status=str(args.get('status',''))
    if kind not in ('diligence','billing') or not re.fullmatch(r'[a-f0-9]{64}',pid):raise Stop('proposition_invalide')
    if status not in ('accepted','dismissed'):raise Stop('etat_proposition_invalide')
    table='diligence_proposals_v230' if kind=='diligence' else 'billing_proposals_v230'
    row=desk.db.execute('SELECT matter,status FROM '+table+' WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('proposition_absente')
    if row['status']!='pending':raise Stop('proposition_deja_traitee')
    desk.db.execute('UPDATE '+table+' SET status=?,updated=? WHERE id=?',(status,desk.now(),pid));desk.db.commit()
    desk.audit('autonomy_proposal_'+status,{'kind':kind,'proposal':pid,'matter':row['matter']})
    try:desk.enqueue('refresh_operational_memory',{'matter':row['matter']},priority=55)
    except Stop:pass
    return {'proposal':pid,'proposal_kind':kind,'status':status,
      'external_action':False,'invoice_created':False,'nextcloud_task_created':False}


def pending_dashboard(desk):
    ensure_schema(desk);matters={x['id']:x for x in load_matters(desk.c)}
    codes={}
    for job in desk.db.execute("SELECT result FROM jobs WHERE kind='prepare_document_project' AND status='done' AND result IS NOT NULL ORDER BY id DESC LIMIT 500"):
        result=_loads(job['result'],{})
        if result.get('project_id') and result.get('confirmation_code'):
            codes.setdefault(result['project_id'],result['confirmation_code'])
    projects=[]
    for row in desk.db.execute("SELECT * FROM document_projects_v220 WHERE status IN ('pending','blocked') ORDER BY created DESC LIMIT 100"):
        data=_loads(row['data'],{});control=data.get('control',{})
        projects.append({'id':row['id'],'matter':row['matter'],
          'matter_name':matters.get(row['matter'],{}).get('client_name',row['matter']),
          'document_type':row['document_type'],'status':row['status'],'created':row['created'],
          'expires':row['expires'],'source_path':data.get('source',{}).get('path',''),
          'destination_folder':data.get('destination_folder',''),'future_files':data.get('future_files',[]),
          'control':control,'automatic':bool(data.get('automatic')),
          'trigger_mail_key':data.get('trigger_mail_key',''),
          'trigger_source':('courriel:'+data.get('trigger_mail_key','')
            if data.get('trigger_mail_key') else 'demande_avocat'),
          'confidence':int(data.get('trigger_confidence',0) or 0),
          'action_proposed':data.get('document_label',row['document_type']),
          'documents_used':([data.get('source',{}).get('path','')]+[x.get('path','') for x in
            data.get('emails_analyzed',[])]+[x.get('path','') for x in data.get('new_attachments',[])])[:50],
          'blocking_reasons':control.get('blocking_reasons',[]),
          'confirmation_code':codes.get(row['id'],'') if row['status']=='pending' else ''})
    diligences=diligence_proposals(desk,'pending',limit=100)
    billings=billing_proposals(desk,'pending',limit=100)
    for item in diligences+billings:item['matter_name']=matters.get(item['matter'],{}).get('client_name',item['matter'])
    controls={row[0]:row[1] for row in desk.db.execute('SELECT status,COUNT(*) FROM document_controls_v230 GROUP BY status')}
    return {'projects':projects,'diligences':diligences,'billing':billings,
      'counts':{'document_projects':len(projects),'diligences':len(diligences),
        'billing':len(billings),'blocked_controls':controls.get('blocked',0)},
      'safety':{'mail_sent':False,'invoice_created':False,'document_written_without_confirmation':False,
        'rpva_filed':False,'payment_made':False}}


def perform(desk,kind,args):
    if kind=='autonomy_mail_sweep':return observe_mail(desk,args)
    if kind=='refresh_operational_memory':return refresh_memory(desk,args)
    if kind=='review_autonomy_proposal':return change_proposal(desk,args)
    raise Stop('action_inconnue')

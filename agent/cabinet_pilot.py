"""AxiorHub 3.0.0 — supervised firm control tower.

This module only observes synchronized local state and prepares reviewable
decisions.  It never sends, files, pays, creates a final invoice, modifies a
calendar, or writes a client document.  Every mutation below concerns the
local decision/audit registers only.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import secrets

from .common import Stop, digest, load_matters
from .model import (Model, MEETING_PREPARATION, TRANSCRIPT_REPORT,
                    PREPARATION_CONTROL, routed_config, validate)


RISKS=('low','medium','high','critical')
DECISION_STATES=('pending','approved','rejected','snoozed','superseded')


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS cabinet_runs_v290(
      id TEXT PRIMARY KEY, cycle_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
      stage TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cabinet_decisions_v290(
      id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
      matter TEXT NOT NULL, risk TEXT NOT NULL, action_kind TEXT NOT NULL,
      title TEXT NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL,
      fingerprint TEXT UNIQUE NOT NULL, status TEXT NOT NULL, batch_id TEXT NOT NULL,
      snoozed_until TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS cabinet_decisions_status
      ON cabinet_decisions_v290(status,risk,updated DESC);
    CREATE TABLE IF NOT EXISTS cabinet_confirmation_batches_v290(
      id TEXT PRIMARY KEY, status TEXT NOT NULL, challenge_hash TEXT NOT NULL,
      items_snapshot TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL,
      decided TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cabinet_provisions_v290(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, label TEXT NOT NULL,
      requested_cents INTEGER NOT NULL, paid_cents INTEGER NOT NULL,
      currency TEXT NOT NULL, due TEXT NOT NULL, source_ref TEXT NOT NULL,
      fingerprint TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS cabinet_provisions_matter ON cabinet_provisions_v290(matter,status,due);
    CREATE TABLE IF NOT EXISTS meeting_preparations_v290(
      id TEXT PRIMARY KEY, event_id TEXT NOT NULL, matter TEXT NOT NULL,
      status TEXT NOT NULL, data TEXT NOT NULL, source_fingerprint TEXT UNIQUE NOT NULL,
      control TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS transcript_reports_v290(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, source_ref TEXT NOT NULL,
      status TEXT NOT NULL, data TEXT NOT NULL, source_fingerprint TEXT UNIQUE NOT NULL,
      control TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS business_test_runs_v290(
      id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_chain_v290(
      seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, action TEXT NOT NULL,
      payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
      previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
    ''')
    columns={row[1] for row in desk.db.execute('PRAGMA table_info(audit_chain_v290)')}
    if 'payload' not in columns:
        desk.db.execute("ALTER TABLE audit_chain_v290 ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
    desk.db.commit()


def _loads(value,default):
    try:return json.loads(value) if value else default
    except (TypeError,ValueError):return default


def _dt(value):
    try:
        out=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError):return None


def append_audit(desk,at,action,data,commit=False):
    """Append one tamper-evident audit link without calling Desk.audit."""
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    payload_hash=hashlib.sha256(raw.encode()).hexdigest()
    row=desk.db.execute('SELECT event_hash FROM audit_chain_v290 ORDER BY seq DESC LIMIT 1').fetchone()
    previous=row[0] if row else ''
    event_hash=hashlib.sha256((previous+'\n'+at+'\n'+action+'\n'+payload_hash).encode()).hexdigest()
    desk.db.execute('INSERT INTO audit_chain_v290(at,action,payload,payload_hash,previous_hash,event_hash) VALUES(?,?,?,?,?,?)',
      (at,action,raw,payload_hash,previous,event_hash))
    if commit:desk.db.commit()
    return event_hash


def audit_status(desk,limit=100):
    ensure_schema(desk);previous='';valid=True;bad_seq=None;rows=[]
    all_rows=desk.db.execute('SELECT * FROM audit_chain_v290 ORDER BY seq').fetchall()
    for row in all_rows:
        actual_payload_hash=hashlib.sha256(str(row['payload']).encode()).hexdigest()
        expected=hashlib.sha256((previous+'\n'+row['at']+'\n'+row['action']+'\n'+row['payload_hash']).encode()).hexdigest()
        if row['payload_hash']!=actual_payload_hash or row['previous_hash']!=previous or row['event_hash']!=expected:
            valid=False;bad_seq=row['seq'];break
        previous=row['event_hash']
    for row in all_rows[-max(1,min(int(limit),500)):]:rows.append(dict(row))
    return {'valid':valid,'broken_at':bad_seq,'count':len(all_rows),'head':previous if valid else '',
      'events':list(reversed(rows))}


def _matter(desk,mid):
    value=next((x for x in load_matters(desk.c) if x['id']==mid),None)
    if not value:raise Stop('dossier_absent')
    return value


def _decision(desk,source_kind,source_id,matter,risk,action_kind,title,summary,payload):
    if risk not in RISKS:raise Stop('niveau_risque_invalide')
    stable={'source_kind':source_kind,'source_id':source_id,'matter':matter,'risk':risk,
      'action_kind':action_kind,'title':title,'summary':summary,'payload':payload}
    fp=digest(json.dumps(stable,ensure_ascii=False,sort_keys=True));did=digest('decision|'+fp);stamp=desk.now()
    old=desk.db.execute('SELECT status,created,batch_id,snoozed_until FROM cabinet_decisions_v290 WHERE fingerprint=?',(fp,)).fetchone()
    if old:return did,False
    desk.db.execute('''INSERT INTO cabinet_decisions_v290 VALUES
      (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(did,source_kind,str(source_id),matter,risk,action_kind,
      title[:500],summary[:3000],json.dumps(payload,ensure_ascii=False),fp,'pending','','',stamp,stamp))
    return did,True


def _risk_for_due(due,now_dt,amount=0):
    value=_dt(due)
    if not value:return 'medium'
    days=(value-now_dt).total_seconds()/86400
    if days<0 and amount>=100000:return 'critical'
    if days<0:return 'high'
    if days<=2:return 'high'
    if days<=7:return 'medium'
    return 'low'


def refresh(desk,args=None):
    """Resume/idempotently refresh the local control tower."""
    ensure_schema(desk);args=args or {};cfg=desk.c.get('cabinet_pilotage',{})
    now_dt=datetime.now(timezone.utc);bucket=max(5,int(cfg.get('refresh_interval_minutes',30)))
    cycle_key=str(args.get('cycle_key') or int(now_dt.timestamp()//(bucket*60)))
    run=desk.db.execute('SELECT * FROM cabinet_runs_v290 WHERE cycle_key=?',(cycle_key,)).fetchone()
    if run and run['status']=='done':return _loads(run['data'],{})|{'idempotent':True}
    rid=run['id'] if run else secrets.token_hex(16);created=run['created'] if run else desk.now()
    desk.db.execute('INSERT OR REPLACE INTO cabinet_runs_v290 VALUES(?,?,?,?,?,?,?,?)',
      (rid,cycle_key,digest(cycle_key),'scan','running','{}',created,desk.now()));desk.db.commit()
    created_count=0;counts={x:0 for x in ('billing','unbilled','meetings','workload','provisions','inactive')}
    matters={x['id']:x for x in load_matters(desk.c)}
    # Existing billing proposals remain proposals: approval here never creates an invoice.
    for row in desk.db.execute("SELECT * FROM billing_proposals_v230 WHERE status='pending' ORDER BY updated DESC LIMIT 500"):
        _,new=_decision(desk,'billing_proposal',row['id'],row['matter'],'medium','review_billing',
          'Facturation à contrôler — '+row['label'],
          'Durée estimée : '+str(row['estimated_minutes'])+' min. Montant et libellé restent à valider.',
          {'proposal_id':row['id'],'estimated_minutes':row['estimated_minutes'],
           'amount_cents':row['amount_cents'],'invoice_created':False})
        created_count+=new;counts['billing']+=new
    # Completed internal jobs with no referenced billing proposal are potential omissions only.
    since=(now_dt-timedelta(days=max(1,int(cfg.get('unbilled_lookback_days',30))))).isoformat()
    valuable={'prepare_reply','prepare_document_project','prepare_hearing','prepare_word_project',
      'prepare_legal_opinion','prepare_meeting','prepare_transcript_report'}
    for row in desk.db.execute("SELECT id,kind,args,finished FROM jobs WHERE status='done' AND finished>=? ORDER BY id DESC LIMIT 1000",(since,)):
        if row['kind'] not in valuable:continue
        parsed=_loads(row['args'],{});mid=str(parsed.get('matter',''))
        if not mid or mid not in matters:continue
        linked=desk.db.execute("SELECT 1 FROM billing_proposals_v230 WHERE evidence LIKE ? LIMIT 1",('%\"job_id\": '+str(row['id'])+'%',)).fetchone()
        if linked:continue
        _,new=_decision(desk,'job',row['id'],mid,'medium','review_unbilled_work',
          'Travail potentiellement non facturé',row['kind']+' terminé le '+str(row['finished'])+'.',
          {'job_id':row['id'],'job_kind':row['kind'],'requires_time_confirmation':True,
           'invoice_created':False})
        created_count+=new;counts['unbilled']+=new
    # Upcoming linked appointments become preparation proposals, never calendar mutations.
    horizon=now_dt+timedelta(days=max(1,int(cfg.get('meeting_horizon_days',30))))
    for row in desk.db.execute('''SELECT id,title,description,location,starts,ends,matter,href
      FROM calendar_cache WHERE matter<>'' AND starts>=? AND starts<? ORDER BY starts LIMIT 500''',
      (now_dt.isoformat(),horizon.isoformat())):
        risk=_risk_for_due(row['starts'],now_dt)
        _,new=_decision(desk,'calendar_event',row['id'],row['matter'],risk,'prepare_meeting',
          'Préparer le rendez-vous — '+row['title'],row['starts']+' · '+row['location'],
          {'event_id':row['id'],'starts':row['starts'],'ends':row['ends'],'calendar_modified':False})
        created_count+=new;counts['meetings']+=new
    # Workload is calculated from cached events and tasks, with no invented availability.
    horizon_days=max(1,min(int(cfg.get('workload_horizon_days',14)),60));capacity=max(60,int(cfg.get('daily_capacity_minutes',420)))
    from .workload import occupancy
    from zoneinfo import ZoneInfo
    tz=desk.c.get('calendar',{}).get('timezone','Europe/Paris')
    days=occupancy([dict(r) for r in desk.db.execute('SELECT starts,ends,busy FROM calendar_cache')],
                   now_dt.astimezone(ZoneInfo(tz)).date(),horizon_days,tz)
    # Refresh observations without pretending the lawyer rejected old alerts.
    active_workload=set()
    for day,info in days.items():
        minutes=info['planned_minutes']
        if minutes<=capacity:continue
        risk='high' if minutes>capacity*1.35 else 'medium'
        decision_id,new=_decision(desk,'workload',day,'',risk,'review_workload',
          'Surcharge détectée le '+day,str(minutes)+' min planifiées pour '+str(capacity)+' min de capacité paramétrée.',
          {'day':day,**info,'capacity_minutes':capacity,'calendar_modified':False,
           'note':'Occupation horaire connue ; échéances à la journée et tâches non créneautées exclues. Les chevauchements ne sont comptés qu’une fois.'})
        active_workload.add(decision_id)
        created_count+=new;counts['workload']+=new
    for row in desk.db.execute("SELECT id FROM cabinet_decisions_v290 WHERE source_kind='workload' AND status IN ('pending','snoozed')").fetchall():
        if row['id'] not in active_workload:
            desk.db.execute("UPDATE cabinet_decisions_v290 SET status='superseded',updated=? WHERE id=?",(desk.now(),row['id']))
            append_audit(desk,desk.now(),'workload_observation_superseded',{'decision_id':row['id'],'calendar_modified':False})
    # Provisions are an internal register; overdue balances become decisions.
    for row in desk.db.execute("SELECT * FROM cabinet_provisions_v290 WHERE status IN ('open','partial')"):
        balance=max(0,row['requested_cents']-row['paid_cents'])
        if not balance:continue
        due=_dt(row['due'])
        if due and due>now_dt+timedelta(days=14):continue
        risk=_risk_for_due(row['due'],now_dt,balance)
        _,new=_decision(desk,'provision',row['id'],row['matter'],risk,'review_provision',
          'Provision à suivre — '+row['label'],
          'Solde interne : '+format(balance/100,'.2f')+' '+row['currency']+' ; échéance '+row['due']+'.',
          {'provision_id':row['id'],'balance_cents':balance,'payment_made':False,'email_sent':False})
        created_count+=new;counts['provisions']+=new
    # Inactivity is based on last local evidence only and is presented as an alert.
    inactive_days=max(7,int(cfg.get('inactive_days',90)));cutoff=now_dt-timedelta(days=inactive_days)
    for mid,matter in matters.items():
        state=desk.db.execute('''SELECT state,last_external_activity,last_mail_activity,
          last_document_activity,updated FROM matter_portfolio WHERE matter=?''',(mid,)).fetchone()
        if state and state['state'] not in ('active','to_confirm'):continue
        stamps=[]
        if state:
            for key in ('last_external_activity','last_mail_activity','last_document_activity','updated'):
                if _dt(state[key]):stamps.append(_dt(state[key]))
        doc=desk.db.execute('SELECT MAX(modified) FROM portfolio_document_activity WHERE matter=?',(mid,)).fetchone()
        if doc and _dt(doc[0]):stamps.append(_dt(doc[0]))
        mail=desk.db.execute('SELECT MAX(received) FROM work_items WHERE matter=?',(mid,)).fetchone()
        if mail and _dt(mail[0]):stamps.append(_dt(mail[0]))
        last=max(stamps) if stamps else None
        if last and last>=cutoff:continue
        _,new=_decision(desk,'matter_inactivity',mid,mid,'low','review_inactive_matter',
          'Dossier sans activité récente — '+str(matter.get('client_name') or mid),
          ('Dernière trace locale : '+last.isoformat() if last else 'Aucune trace locale datée.'),
          {'inactive_days':inactive_days,'last_activity':last.isoformat() if last else '',
           'matter_state_changed':False})
        created_count+=new;counts['inactive']+=new
    result={'run_id':rid,'cycle_key':cycle_key,'created':created_count,'by_kind':counts,
      'safety':{'invoice_created':False,'email_sent':False,'calendar_modified':False,
        'document_created':False,'payment_made':False,'rpva_filed':False},'idempotent':False}
    desk.db.execute('UPDATE cabinet_runs_v290 SET stage=?,status=?,data=?,updated=? WHERE id=?',
      ('complete','done',json.dumps(result,ensure_ascii=False),desk.now(),rid));desk.db.commit()
    desk.audit('cabinet_control_refreshed',result);return result


def dashboard(desk,status='pending',limit=200):
    ensure_schema(desk);limit=max(1,min(int(limit),500));now_dt=datetime.now(timezone.utc)
    where="WHERE status=?" if status in DECISION_STATES else ''
    params=(status,limit) if where else (limit,)
    rows=[]
    for row in desk.db.execute('SELECT * FROM cabinet_decisions_v290 '+where+
      " ORDER BY CASE risk WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,updated DESC LIMIT ?",params):
        item=dict(row);item['payload']=_loads(item['payload'],{});rows.append(item)
    counts={row[0]:row[1] for row in desk.db.execute('SELECT risk,COUNT(*) FROM cabinet_decisions_v290 WHERE status=? GROUP BY risk',('pending',))}
    batches=[dict(x) for x in desk.db.execute('SELECT id,status,created,expires,decided FROM cabinet_confirmation_batches_v290 ORDER BY created DESC LIMIT 20')]
    provisions=[dict(x) for x in desk.db.execute('SELECT * FROM cabinet_provisions_v290 ORDER BY updated DESC LIMIT 100')]
    last_test=desk.db.execute('SELECT * FROM business_test_runs_v290 ORDER BY created DESC LIMIT 1').fetchone()
    return {'generated_at':now_dt.isoformat(),'decisions':rows,'counts':{x:counts.get(x,0) for x in RISKS},
      'batches':batches,'provisions':provisions,'last_business_test':dict(last_test) if last_test else None,
      'audit':{k:v for k,v in audit_status(desk,5).items() if k!='events'},
      'safety':{'supervised':True,'final_billing':False,'external_action':False}}


def _approve_effect(desk,row):
    payload=_loads(row['payload'],{});queued=None
    if row['action_kind']=='prepare_meeting':
        queued=desk.enqueue('prepare_meeting',{'event_id':payload.get('event_id','')},priority=0)
    elif row['action_kind']=='review_billing':
        proposal=payload.get('proposal_id','')
        current=desk.db.execute("SELECT status FROM billing_proposals_v230 WHERE id=?",(proposal,)).fetchone()
        if current and current['status']=='pending':
            desk.db.execute("UPDATE billing_proposals_v230 SET status='accepted',updated=? WHERE id=?",(desk.now(),proposal))
    return queued


def review_decision(desk,args):
    ensure_schema(desk);did=str(args.get('decision_id',''));status=str(args.get('status',''))
    if not re.fullmatch(r'[a-f0-9]{64}',did):raise Stop('decision_invalide')
    if status not in ('approved','rejected','snoozed'):raise Stop('etat_decision_invalide')
    row=desk.db.execute('SELECT * FROM cabinet_decisions_v290 WHERE id=?',(did,)).fetchone()
    if not row:raise Stop('decision_absente')
    if row['status']!='pending':raise Stop('decision_deja_traitee')
    if status=='approved' and row['risk'] in ('high','critical') and args.get('confirm_risk')!='yes':
        raise Stop('confirmation_individuelle_risque_requise')
    until=''
    if status=='snoozed':
        hours=max(1,min(int(args.get('snooze_hours',24)),720));until=(datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat()
    queued=_approve_effect(desk,row) if status=='approved' else None
    desk.db.execute('UPDATE cabinet_decisions_v290 SET status=?,snoozed_until=?,updated=? WHERE id=?',
      (status,until,desk.now(),did));desk.db.commit()
    desk.audit('cabinet_decision_'+status,{'decision_id':did,'risk':row['risk'],'queued_job':queued,
      'external_action':False,'invoice_created':False})
    message={'snoozed':'Rappel différé de '+str(args.get('snooze_hours',24))+' h. Aucun événement déplacé.',
             'rejected':'Proposition écartée. Aucun document ni courriel supprimé.',
             'approved':'Alerte prise en compte. Aucune action externe.'}[status]
    if queued:message='Préparation du rendez-vous mise en attente : opération n° '+str(queued)+'.'
    elif status=='approved' and row['action_kind']=='review_billing':message='Estimation acceptée. Aucune facture définitive créée.'
    return {'decision_id':did,'status':status,'risk':row['risk'],'queued_job':queued,'message':message,
      'external_action':False,'invoice_created':False}


def create_batch(desk,args):
    ensure_schema(desk);raw=args.get('decision_ids',[])
    if isinstance(raw,str):raw=[x.strip() for x in raw.split(',') if x.strip()]
    ids=list(dict.fromkeys(str(x) for x in raw))
    if not ids or len(ids)>max(1,int(desk.c.get('cabinet_pilotage',{}).get('group_max_items',50))):raise Stop('lot_invalide')
    snapshot=[]
    for did in ids:
        row=desk.db.execute('SELECT id,risk,status,fingerprint FROM cabinet_decisions_v290 WHERE id=?',(did,)).fetchone()
        if not row or row['status']!='pending':raise Stop('decision_absente_ou_deja_traitee')
        if row['risk'] in ('high','critical'):raise Stop('risque_eleve_confirmation_individuelle')
        snapshot.append({'id':row['id'],'risk':row['risk'],'fingerprint':row['fingerprint']})
    code=''.join(str(secrets.randbelow(10)) for _ in range(6));bid=secrets.token_hex(16);stamp=desk.now()
    minutes=max(5,int(desk.c.get('cabinet_pilotage',{}).get('batch_approval_minutes',30)))
    expires=(datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat()
    challenge=hashlib.sha256((bid+'|'+code+'|'+json.dumps(snapshot,sort_keys=True)).encode()).hexdigest()
    desk.db.execute('INSERT INTO cabinet_confirmation_batches_v290 VALUES(?,?,?,?,?,?,?)',
      (bid,'pending',challenge,json.dumps(snapshot),stamp,expires,''))
    desk.db.execute('UPDATE cabinet_decisions_v290 SET batch_id=?,updated=? WHERE id IN ('+','.join('?'*len(ids))+')',
      (bid,stamp,*ids));desk.db.commit()
    desk.audit('cabinet_batch_created',{'batch_id':bid,'count':len(ids),'risks':sorted({x['risk'] for x in snapshot})})
    return {'batch_id':bid,'confirmation_code':code,'expires':expires,'count':len(ids),
      'warning':'Le code est affiché une seule fois. Le lot ne contient aucun risque élevé ou critique.'}


def approve_batch(desk,args):
    ensure_schema(desk);bid=str(args.get('batch_id',''));code=str(args.get('confirmation_code',''))
    if not re.fullmatch(r'[a-f0-9]{32}',bid) or not re.fullmatch(r'[0-9]{6}',code):raise Stop('confirmation_lot_invalide')
    row=desk.db.execute('SELECT * FROM cabinet_confirmation_batches_v290 WHERE id=?',(bid,)).fetchone()
    if not row or row['status']!='pending':raise Stop('lot_absent_ou_deja_traite')
    if (_dt(row['expires']) or datetime.min.replace(tzinfo=timezone.utc))<datetime.now(timezone.utc):raise Stop('confirmation_expiree')
    snapshot=_loads(row['items_snapshot'],[])
    expected=hashlib.sha256((bid+'|'+code+'|'+json.dumps(snapshot,sort_keys=True)).encode()).hexdigest()
    if not secrets.compare_digest(expected,row['challenge_hash']):raise Stop('code_confirmation_incorrect')
    queued=[]
    for item in snapshot:
        current=desk.db.execute('SELECT * FROM cabinet_decisions_v290 WHERE id=?',(item['id'],)).fetchone()
        if not current or current['status']!='pending' or current['fingerprint']!=item['fingerprint']:
            raise Stop('lot_modifie_depuis_previsualisation')
        if current['risk'] in ('high','critical'):raise Stop('risque_eleve_confirmation_individuelle')
    for item in snapshot:
        current=desk.db.execute('SELECT * FROM cabinet_decisions_v290 WHERE id=?',(item['id'],)).fetchone()
        job=_approve_effect(desk,current)
        if job:queued.append(job)
        desk.db.execute("UPDATE cabinet_decisions_v290 SET status='approved',updated=? WHERE id=?",(desk.now(),item['id']))
    desk.db.execute("UPDATE cabinet_confirmation_batches_v290 SET status='approved',decided=? WHERE id=?",(desk.now(),bid));desk.db.commit()
    desk.audit('cabinet_batch_approved',{'batch_id':bid,'count':len(snapshot),'queued_jobs':queued,
      'external_action':False,'invoice_created':False})
    return {'batch_id':bid,'status':'approved','count':len(snapshot),'queued_jobs':queued,
      'external_action':False,'invoice_created':False}


def record_provision(desk,args):
    ensure_schema(desk);mid=str(args.get('matter',''));_matter(desk,mid)
    label=str(args.get('label','')).strip();currency=str(args.get('currency','EUR')).upper()
    try:requested=int(args.get('requested_cents'));paid=int(args.get('paid_cents',0))
    except (TypeError,ValueError):raise Stop('montant_provision_invalide') from None
    due=str(args.get('due',''));source=str(args.get('source_ref','')).strip()
    if not label or requested<=0 or paid<0 or paid>requested or currency!='EUR' or not _dt(due) or not source:
        raise Stop('provision_invalide')
    fp=digest('|'.join([mid,label,str(requested),str(paid),currency,due,source]));pid=digest('provision|'+fp);stamp=desk.now()
    status='paid' if paid==requested else ('partial' if paid else 'open')
    desk.db.execute('''INSERT OR IGNORE INTO cabinet_provisions_v290 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
      (pid,mid,label[:500],requested,paid,currency,due,source[:2000],fp,status,stamp,stamp));desk.db.commit()
    desk.audit('provision_recorded',{'provision_id':pid,'matter':mid,'source_ref':source,
      'payment_made':False,'invoice_created':False})
    return {'provision_id':pid,'status':status,'internal_register_only':True,'payment_made':False}


def prepare_meeting(desk,args,writer=None,controller=None):
    ensure_schema(desk);eid=str(args.get('event_id',''))
    row=desk.db.execute('SELECT * FROM calendar_cache WHERE id=?',(eid,)).fetchone()
    if not row:raise Stop('evenement_absent')
    mid=row['matter'];_matter(desk,mid)
    memory=desk.db.execute('SELECT version,fingerprint,data,sources,updated FROM matter_operational_memory_v230 WHERE matter=?',(mid,)).fetchone()
    packet=[{'source_id':'calendar:'+eid,'kind':'calendar','title':row['title'],'content':
      '\n'.join([row['title'],row['description'],row['location'],row['starts'],row['ends']])}]
    if memory:packet.append({'source_id':'memory:'+mid+':'+str(memory['version']),'kind':'operational_memory',
      'title':'Mémoire opérationnelle','content':str(memory['data'])[:30000]})
    fp=digest(json.dumps(packet,sort_keys=True,ensure_ascii=False));old=desk.db.execute(
      'SELECT id FROM meeting_preparations_v290 WHERE source_fingerprint=?',(fp,)).fetchone()
    if old:return meeting_preview(desk,old['id'])|{'idempotent':True}
    valid={x['source_id'] for x in packet};model=writer or Model(routed_config(desk.c,'assistant'))
    data=model.ask('meeting_preparation',{'event':dict(row),'sources':packet,
      'rules':{'prepare_only':True,'no_invitation':True,'no_email':True,'no_calendar_write':True}})
    validate(data,MEETING_PREPARATION)
    if not set(data['source_ids']).issubset(valid):raise Stop('source_inconnue_dans_preparation')
    control_model=controller or Model(routed_config(desk.c,'control'))
    control=control_model.ask('preparation_control',{'kind':'meeting','draft':data,'known_source_ids':sorted(valid),
      'rules':{'requires_lawyer':True,'no_external_action':True}});validate(control,PREPARATION_CONTROL)
    status='blocked' if control['blocking_reasons'] or not control['all_sources_known'] or not control['no_external_action'] else 'ready'
    pid=secrets.token_hex(16);stamp=desk.now();desk.db.execute('INSERT INTO meeting_preparations_v290 VALUES(?,?,?,?,?,?,?,?,?)',
      (pid,eid,mid,status,json.dumps(data,ensure_ascii=False),fp,json.dumps(control,ensure_ascii=False),stamp,stamp));desk.db.commit()
    desk.audit('meeting_prepared',{'preparation_id':pid,'event_id':eid,'matter':mid,'status':status,
      'model_roles':['complex','control'],'external_action':False})
    return meeting_preview(desk,pid)|{'idempotent':False}


def meeting_preview(desk,pid):
    row=desk.db.execute('SELECT * FROM meeting_preparations_v290 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('preparation_rendez_vous_absente')
    item=dict(row);item['data']=_loads(item['data'],{});item['control']=_loads(item['control'],{})
    item['safety']={'calendar_modified':False,'invitation_sent':False,'email_sent':False};return item


def prepare_transcript(desk,args,writer=None,controller=None):
    ensure_schema(desk);mid=str(args.get('matter',''));_matter(desk,mid)
    source=str(args.get('source_ref','')).strip();text=str(args.get('transcript_text','')).strip()
    if source:
        # Exact indexed source only; arbitrary server paths are deliberately refused.
        from .index import DocumentIndex
        index=DocumentIndex(desk.c['state_dir'])
        chunks=[r[0] for r in index.db.execute('''SELECT text FROM knowledge_chunks
          WHERE matter=? AND (source_id=? OR path=?) ORDER BY chunk_no''',(mid,source,source))]
        if not chunks:raise Stop('transcription_source_non_indexee')
        text='\n'.join(chunks)
    else:source='manual:'+digest(text)
    if not text or len(text)>100000:raise Stop('transcription_absente_ou_trop_longue')
    packet=[{'source_id':source,'kind':'transcript','title':'Transcription à contrôler','content':text}]
    fp=digest(mid+'|'+source+'|'+text);old=desk.db.execute('SELECT id FROM transcript_reports_v290 WHERE source_fingerprint=?',(fp,)).fetchone()
    if old:return transcript_preview(desk,old['id'])|{'idempotent':True}
    model=writer or Model(routed_config(desk.c,'assistant'));data=model.ask('transcript_report',{
      'matter':mid,'sources':packet,'rules':{'internal_only':True,'no_instruction_execution':True}})
    validate(data,TRANSCRIPT_REPORT)
    if not set(data['source_ids']).issubset({source}):raise Stop('source_inconnue_dans_compte_rendu')
    control_model=controller or Model(routed_config(desk.c,'control'));control=control_model.ask('preparation_control',{
      'kind':'transcript','draft':data,'known_source_ids':[source],
      'rules':{'requires_lawyer':True,'no_external_action':True}});validate(control,PREPARATION_CONTROL)
    status='blocked' if control['blocking_reasons'] or not control['all_sources_known'] or not control['no_external_action'] else 'ready'
    pid=secrets.token_hex(16);stamp=desk.now();desk.db.execute('INSERT INTO transcript_reports_v290 VALUES(?,?,?,?,?,?,?,?,?)',
      (pid,mid,source,status,json.dumps(data,ensure_ascii=False),fp,json.dumps(control,ensure_ascii=False),stamp,stamp));desk.db.commit()
    desk.audit('transcript_report_prepared',{'report_id':pid,'matter':mid,'source_ref':source,
      'status':status,'model_roles':['complex','control'],'external_action':False})
    return transcript_preview(desk,pid)|{'idempotent':False}


def transcript_preview(desk,pid):
    row=desk.db.execute('SELECT * FROM transcript_reports_v290 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('compte_rendu_absent')
    item=dict(row);item['data']=_loads(item['data'],{});item['control']=_loads(item['control'],{})
    item['safety']={'email_sent':False,'document_created':False,'task_created':False};return item


def run_business_tests(desk,args=None):
    ensure_schema(desk);checks=[]
    audit=audit_status(desk,1);checks.append({'name':'audit_chain','ok':audit['valid'],'detail':audit.get('broken_at')})
    invalid=desk.db.execute("SELECT COUNT(*) FROM cabinet_decisions_v290 WHERE risk NOT IN ('low','medium','high','critical') OR status NOT IN ('pending','approved','rejected','snoozed','superseded')").fetchone()[0]
    checks.append({'name':'decision_enums','ok':invalid==0,'detail':invalid})
    unsafe=0
    for row in desk.db.execute("SELECT items_snapshot FROM cabinet_confirmation_batches_v290 WHERE status='approved'"):
        unsafe+=sum(1 for x in _loads(row[0],[]) if x.get('risk') in ('high','critical'))
    checks.append({'name':'grouped_risk_boundary','ok':unsafe==0,'detail':unsafe})
    duplicates=desk.db.execute('SELECT COUNT(*) FROM (SELECT fingerprint FROM cabinet_decisions_v290 GROUP BY fingerprint HAVING COUNT(*)>1)').fetchone()[0]
    checks.append({'name':'idempotent_decisions','ok':duplicates==0,'detail':duplicates})
    routing=desk.c.get('model_routing',{});missing=[x for x in ('fast_model','complex_model','control_model') if x not in routing]
    checks.append({'name':'model_separation','ok':not missing,'detail':missing})
    status='passed' if all(x['ok'] for x in checks) else 'failed';rid=secrets.token_hex(16)
    data={'status':status,'checks':checks,'external_actions':0,'tested_at':desk.now()}
    desk.db.execute('INSERT INTO business_test_runs_v290 VALUES(?,?,?,?)',(rid,status,json.dumps(data,ensure_ascii=False),desk.now()));desk.db.commit()
    desk.audit('continuous_business_tests_'+status,{'run_id':rid,'checks':checks})
    return {'run_id':rid,**data}


def perform(desk,kind,args):
    if kind=='refresh_cabinet_pilotage':return refresh(desk,args)
    if kind=='review_cabinet_decision':return review_decision(desk,args)
    if kind=='create_cabinet_confirmation_batch':return create_batch(desk,args)
    if kind=='approve_cabinet_confirmation_batch':return approve_batch(desk,args)
    if kind=='record_provision':return record_provision(desk,args)
    if kind=='prepare_meeting':return prepare_meeting(desk,args)
    if kind=='prepare_transcript_report':return prepare_transcript(desk,args)
    if kind=='run_continuous_business_tests':return run_business_tests(desk,args)
    raise Stop('action_inconnue')

"""Active matter portfolio, safe association bootstrap and inbox reconciliation.

The portfolio is deliberately separate from document indexing: indexing time is
never evidence that a legal matter is active.  Automatic links identify a
matter only; they never invent the recipient's role or authorise disclosure.
"""
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import json
import re

from .common import Stop, digest, fold, load_matters
from .index import DocumentIndex
from .mailbox import Mailbox, addresses, exclusion


PORTFOLIO_STATES = {'active', 'dormant', 'archived', 'to_confirm'}
LEVELS = {'certain': 100, 'very_likely': 97, 'probable': 85, 'uncertain': 0}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS matter_portfolio(
      matter TEXT PRIMARY KEY, state TEXT NOT NULL, manual_state TEXT NOT NULL,
      pinned INTEGER NOT NULL, last_external_activity TEXT NOT NULL,
      last_mail_activity TEXT NOT NULL, last_document_activity TEXT NOT NULL,
      next_event TEXT NOT NULL, open_tasks INTEGER NOT NULL, reasons TEXT NOT NULL,
      updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS portfolio_mail_links(
      id TEXT PRIMARY KEY, mail_key TEXT NOT NULL, message_id TEXT NOT NULL,
      thread_root TEXT NOT NULL, folder TEXT NOT NULL, uid TEXT NOT NULL,
      matter TEXT NOT NULL, confidence INTEGER NOT NULL, level TEXT NOT NULL,
      source TEXT NOT NULL, sender TEXT NOT NULL, recipients TEXT NOT NULL,
      mail_date TEXT NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS association_groups(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, email TEXT NOT NULL,
      confidence INTEGER NOT NULL, level TEXT NOT NULL, message_count INTEGER NOT NULL,
      evidence TEXT NOT NULL, status TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS portfolio_document_activity(
      matter TEXT PRIMARY KEY, modified TEXT NOT NULL, path TEXT NOT NULL,
      checked TEXT NOT NULL, error TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS portfolio_calendar_links(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, event_at TEXT NOT NULL,
      title TEXT NOT NULL, source TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS portfolio_state
      ON matter_portfolio(state,last_external_activity);
    CREATE INDEX IF NOT EXISTS portfolio_mail_identity
      ON portfolio_mail_links(mail_key,message_id,thread_root,status);
    CREATE INDEX IF NOT EXISTS portfolio_mail_matter
      ON portfolio_mail_links(matter,mail_date,status);
    CREATE INDEX IF NOT EXISTS association_group_status
      ON association_groups(status,confidence,updated);
    ''')
    desk.db.commit()


def _dt(value):
    if not value:
        return None
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (ValueError,TypeError):
        try:result=parsedate_to_datetime(str(value))
        except (ValueError,TypeError,OverflowError):return None
    if not result.tzinfo:
        result=result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _latest(values):
    dated=[(x,_dt(x)) for x in values if _dt(x)]
    return max(dated,key=lambda x:x[1])[0] if dated else ''


def _boundary(value,text):
    value=fold(str(value or '')).strip()
    return len(value)>=4 and re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',text) is not None


def _reference_year(matter):
    for value in [matter.get('id','')]+matter.get('references',[]):
        match=re.search(r'(?<!\d)(20\d{2})(?:\d{4,8})?(?!\d)',str(value))
        if match:
            return int(match[1])
    return 0


def matter_status(desk, matter, index=None):
    """Classify from source dates, open work and future events, never indexed_at."""
    ensure_schema(desk);mid=matter['id'];now_dt=datetime.now(timezone.utc)
    cfg=desk.c.get('portfolio',{})
    active_days=max(30,min(int(cfg.get('active_days',180)),730))
    archive_days=max(active_days+1,min(int(cfg.get('archive_days',730)),3650))
    old=desk.db.execute('SELECT manual_state,pinned FROM matter_portfolio WHERE matter=?',(mid,)).fetchone()
    manual=(old['manual_state'] if old else '') or ''
    pinned=bool(old['pinned']) if old else bool(matter.get('pinned'))
    index=index or DocumentIndex(desk.c['state_dir'])
    document_dates=[r[0] for r in index.db.execute(
      "SELECT modified FROM docs WHERE matter=? AND modified<>''",(mid,))]
    document_dates += [r[0] for r in desk.db.execute(
      "SELECT modified FROM portfolio_document_activity WHERE matter=? AND modified<>''",(mid,))]
    # Mail activity can come from the bootstrap graph or already scoped sources.
    mail_dates=[r[0] for r in desk.db.execute(
      "SELECT mail_date FROM portfolio_mail_links WHERE matter=? AND status IN ('automatic','confirmed')",(mid,))]
    mail_dates += [r[0] for r in index.db.execute(
      "SELECT DISTINCT modified FROM knowledge_chunks WHERE matter=? AND kind IN ('email_received','email_sent','email_history') AND modified<>''",(mid,))]
    last_doc=_latest(document_dates);last_mail=_latest(mail_dates)
    last_external=_latest([last_doc,last_mail])
    open_tasks=desk.db.execute(
      "SELECT COUNT(*) FROM tasks WHERE matter=? AND status='open'",(mid,)).fetchone()[0]
    next_event=''
    try:
        dates=[r[0] for r in desk.db.execute('''SELECT event_at FROM timeline_events
          WHERE matter=? AND status<>'stale' AND event_type IN ('deadline','calendar','task')
          AND event_at>=? ORDER BY event_at LIMIT 20''',(mid,now_dt.isoformat())) if _dt(r[0])]
        next_event=min(dates,key=lambda x:_dt(x)) if dates else ''
    except Exception:
        next_event=''
    calendar_dates=[r[0] for r in desk.db.execute(
      'SELECT event_at FROM portfolio_calendar_links WHERE matter=? AND event_at>=? ORDER BY event_at',
      (mid,now_dt.isoformat())) if _dt(r[0])]
    if calendar_dates:
        candidate=min(calendar_dates,key=lambda x:_dt(x))
        if not next_event or _dt(candidate)<_dt(next_event):next_event=candidate
    conflicts=desk.db.execute('''SELECT COUNT(*) FROM association_groups
      WHERE matter=? AND status='conflict' ''',(mid,)).fetchone()[0]
    reasons=[]
    recent_cutoff=now_dt-timedelta(days=active_days)
    if pinned:reasons.append('activation_manuelle')
    if _dt(last_mail) and _dt(last_mail)>=recent_cutoff:reasons.append('courriel_recent')
    if _dt(last_doc) and _dt(last_doc)>=recent_cutoff:reasons.append('document_recent')
    if next_event:reasons.append('evenement_futur')
    if open_tasks:reasons.append('tache_ouverte')
    if conflicts:reasons.append('association_contradictoire')
    if manual in PORTFOLIO_STATES:
        state=manual;reasons.insert(0,'etat_confirme_par_avocat')
    elif conflicts:
        state='to_confirm'
    elif reasons:
        state='active'
    else:
        last=_dt(last_external)
        # A dated old reference is a conservative fallback for an empty legacy
        # folder; it does not claim that the matter was legally closed.
        old_reference=_reference_year(matter) and _reference_year(matter)<=now_dt.year-2
        state='archived' if ((last and last<now_dt-timedelta(days=archive_days)) or
                             (not last and old_reference)) else 'dormant'
        reasons.append('aucune_activite_recente' if last else 'aucune_activite_sourcee')
    desk.db.execute('''INSERT OR REPLACE INTO matter_portfolio
      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(mid,state,manual,int(pinned),last_external,
      last_mail,last_doc,next_event,open_tasks,json.dumps(reasons,ensure_ascii=False),desk.now()))
    desk.db.commit()
    return {'matter':mid,'state':state,'manual_state':manual,'pinned':pinned,
      'last_external_activity':last_external,'last_mail_activity':last_mail,
      'last_document_activity':last_doc,'next_event':next_event,
      'open_tasks':open_tasks,'reasons':reasons}


def _prune_orphans(desk, matters):
    valid={m['id'] for m in matters};removed=0
    for row in desk.db.execute('SELECT matter FROM matter_portfolio').fetchall():
        if row['matter'] in valid:continue
        mid=row['matter'];removed+=1
        for table in ('matter_portfolio','portfolio_mail_links','association_groups',
                      'portfolio_document_activity','portfolio_calendar_links'):
            desk.db.execute('DELETE FROM '+table+' WHERE matter=?',(mid,))
    if removed:desk.db.commit()
    return removed


def classify_all(desk):
    ensure_schema(desk);counts={x:0 for x in PORTFOLIO_STATES}
    matters=load_matters(desk.c);_prune_orphans(desk,matters)
    index=DocumentIndex(desk.c['state_dir'])
    for matter in matters:
        item=matter_status(desk,matter,index);counts[item['state']]+=1
    desk.setting('portfolio:last_classification',{'at':desk.now(),'counts':counts})
    return counts


def portfolio_rows(desk, states=None, limit=1000, refresh_missing=True):
    ensure_schema(desk);matters={m['id']:m for m in load_matters(desk.c)}
    if refresh_missing:
        known={r[0] for r in desk.db.execute('SELECT matter FROM matter_portfolio')}
        for mid in set(matters)-known:matter_status(desk,matters[mid])
    params=[];where=''
    if states:
        valid=[x for x in states if x in PORTFOLIO_STATES]
        if not valid:return []
        where=' WHERE state IN ('+','.join('?' for _ in valid)+')';params+=valid
    params.append(max(1,min(int(limit),5000)))
    rows=[]
    for row in desk.db.execute('SELECT * FROM matter_portfolio'+where+
      " ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'to_confirm' THEN 1 WHEN 'dormant' THEN 2 ELSE 3 END,last_external_activity DESC,matter LIMIT ?",params):
        if row['matter'] not in matters:continue
        item=dict(row);item['reasons']=json.loads(item['reasons']);item['name']=matters[row['matter']].get('client_name','')
        item['path']=matters[row['matter']]['path'];rows.append(item)
    return rows


def portfolio_summary(desk):
    ensure_schema(desk)
    matters=load_matters(desk.c)
    _prune_orphans(desk,matters)
    known={row[0] for row in desk.db.execute('SELECT matter FROM matter_portfolio')}
    if len(known)<len(matters):
        index=DocumentIndex(desk.c['state_dir'])
        for matter in matters:
            if matter['id'] not in known:matter_status(desk,matter,index)
    counts={x:0 for x in PORTFOLIO_STATES}
    for state,count in desk.db.execute('SELECT state,COUNT(*) FROM matter_portfolio GROUP BY state'):
        counts[state]=count
    pending=desk.db.execute("SELECT COUNT(*) FROM association_groups WHERE status IN ('pending','conflict')").fetchone()[0]
    automatic=desk.db.execute("SELECT COUNT(DISTINCT mail_key) FROM portfolio_mail_links WHERE status='automatic'").fetchone()[0]
    return {'counts':counts,'automatic_mail_links':automatic,'grouped_confirmations':pending,
            'generated_at':desk.now()}


def set_matter_state(desk,args):
    ensure_schema(desk);mid=str(args.get('matter',''));state=str(args.get('state',''))
    if state not in PORTFOLIO_STATES:raise Stop('etat_dossier_invalide')
    matter=next((m for m in load_matters(desk.c) if m['id']==mid),None)
    if not matter:raise Stop('dossier_absent')
    matter_status(desk,matter)
    desk.db.execute('UPDATE matter_portfolio SET manual_state=?,state=?,pinned=?,updated=? WHERE matter=?',
      (state,state,1 if state=='active' else 0,desk.now(),mid));desk.db.commit()
    desk.audit('etat_dossier_confirme',{'matter':mid,'state':state})
    return {'matter':mid,'state':state}


def confirm_matter(desk,args):
    """Confirm only the case identity; the correspondent role stays independent."""
    from .desk import report_for
    key=str(args.get('mail_key') or args.get('key') or '')
    mid=str(args.get('matter') or '')
    if not re.fullmatch(r'[a-f0-9]{64}',key):raise Stop('courriel_invalide')
    matter=next((m for m in load_matters(desk.c) if m['id']==mid),None)
    if not matter:raise Stop('dossier_absent')
    report=report_for(desk.c,key);sender=str(report.get('sender') or '').lower()
    recipients=report.get('reply_recipients') or []
    identity=digest(key+'|'+mid)
    desk.db.execute('''INSERT OR REPLACE INTO portfolio_mail_links VALUES
      (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(identity,key,report.get('incoming_message_id',''),'',
      report.get('source_mailbox',''),str(report.get('source_uid','')),mid,100,'certain',
      'confirmation_avocat',sender,json.dumps(recipients),report.get('received_at',''),
      'confirmed',desk.now()))
    gid=digest(mid+'|'+sender)
    if sender:
        old=desk.db.execute('SELECT evidence,message_count FROM association_groups WHERE id=?',(gid,)).fetchone()
        evidence=json.loads(old['evidence']) if old else []
        proof={'subject':report.get('subject',''),'date':report.get('received_at',''),
               'message_id':report.get('incoming_message_id',''),'source':'confirmation_avocat'}
        if proof not in evidence:evidence.append(proof)
        desk.db.execute('INSERT OR REPLACE INTO association_groups VALUES (?,?,?,?,?,?,?,?,?)',
          (gid,mid,sender,100,'certain',max(1,old['message_count'] if old else 0),
           json.dumps(evidence[-12:],ensure_ascii=False),'confirmed',desk.now()))
    desk.db.execute('UPDATE work_items SET matter=?,state=?,updated=? WHERE mail_key=?',
      (mid,'processing',desk.now(),key))
    desk.db.commit();desk.audit('dossier_courriel_confirme',{'matter':mid,'mail_key':key,
      'role':'non_confirme'})
    matter_status(desk,matter)
    job=desk.enqueue('retry',{'key':key})
    return {'association':'dossier_confirme','dossier':mid,'role':'a_confirmer',
            'courriel_relance':True,'job_id':job}


def reject_group(desk,args):
    gid=str(args.get('group') or '')
    if not re.fullmatch(r'[a-f0-9]{64}',gid):raise Stop('groupe_invalide')
    row=desk.db.execute('SELECT * FROM association_groups WHERE id=?',(gid,)).fetchone()
    if not row or row['status'] not in ('pending','conflict'):raise Stop('groupe_absent_ou_deja_traite')
    desk.db.execute("UPDATE association_groups SET status='rejected',updated=? WHERE id=?",(desk.now(),gid))
    desk.db.execute("UPDATE proposals SET status='rejected',updated=? WHERE matter=? AND email=? AND status='pending'",
      (desk.now(),row['matter'],row['email']))
    desk.db.execute("UPDATE portfolio_mail_links SET status='rejected',updated=? WHERE matter=? AND confidence<97 AND (sender=? OR instr(recipients,?)>0)",
      (desk.now(),row['matter'],row['email'],row['email']))
    desk.db.commit();desk.audit('association_groupee_rejetee',{'group':gid,'matter':row['matter'],'email':row['email']})
    return {'groupe':'rejete'}


def _matter_signals(mail,matters):
    subject=fold(mail.subject);explicit=[];named=[]
    for matter in matters:
        refs=list(dict.fromkeys([matter['id']]+matter.get('references',[])))
        hits=[x for x in refs if _boundary(x,subject)]
        if hits:explicit.append((matter,hits))
        names=[matter.get('client_name','')]+matter.get('aliases',[])
        name_hits=[x for x in names if len(fold(x))>=6 and fold(x) in subject]
        if name_hits:named.append((matter,name_hits))
    return explicit,named


def _participants(mail,c):
    own={x.lower() for x in c['mail']['own_addresses']}
    values=set([mail.sender]+addresses(mail.msg.get('To',''))+addresses(mail.msg.get('Cc','')))-own-{''}
    return sorted(values)


def observe_mail(desk,mail,folder,matters=None):
    """Record high-confidence matter links without assigning a legal role."""
    ensure_schema(desk);matters=matters or load_matters(desk.c);account=desk.c['mail']['username']+'@'+desk.c['mail']['host']
    key=mail.key(account);explicit,named=_matter_signals(mail,matters)
    participants=_participants(mail,desk.c);participant_set=set(participants)
    existing=[]
    for matter in matters:
        if any(p.get('email','').lower() in participant_set for p in matter.get('correspondents',[])):
            existing.append(matter)
    thread_ids=[x for x in [mail.root,mail.mid]+mail.refs if x]
    mapped=[]
    if thread_ids:
        marks=','.join('?' for _ in thread_ids)
        mapped=[next((m for m in matters if m['id']==r[0]),None) for r in desk.db.execute(
          'SELECT DISTINCT matter FROM portfolio_mail_links WHERE status IN (\'automatic\',\'confirmed\') AND (thread_root IN ('+marks+') OR message_id IN ('+marks+'))',(*thread_ids,*thread_ids))]
        mapped=[m for m in mapped if m]
    source='';confidence=0;selected=None
    explicit_matters={m['id']:m for m,_ in explicit}
    if len(explicit_matters)==1:
        candidate=next(iter(explicit_matters.values()))
        mapped_ids={m['id'] for m in mapped};existing_ids={m['id'] for m in existing}
        if ((mapped_ids and candidate['id'] not in mapped_ids) or
                (len(existing_ids)==1 and candidate['id'] not in existing_ids)):
            source='reference_et_fil_contradictoires'
        else:
            selected=candidate;confidence=100;source='reference_unique_objet'
    elif len(explicit_matters)>1:
        source='references_contradictoires'
    elif len({m['id']:m for m in mapped})==1:
        selected=next(iter({m['id']:m for m in mapped}.values()));confidence=99;source='fil_deja_associe'
    elif len({m['id']:m for m in mapped})>1:
        source='fil_associe_a_plusieurs_dossiers'
    elif len({m['id']:m for m in existing})==1:
        selected=next(iter({m['id']:m for m in existing}.values()));confidence=100;source='correspondant_confirme'
    elif len({m['id']:m for m,_ in named})==1:
        selected=next(iter({m['id']:m for m,_ in named}.values()));confidence=85;source='noms_concordants'
    elif len({m['id']:m for m,_ in named})>1:
        source='noms_ambigus'
    elif len({m['id']:m for m in existing})>1:
        source='correspondant_associe_a_plusieurs_dossiers'
    if not selected:
        if source in ('references_contradictoires','reference_et_fil_contradictoires',
                      'fil_associe_a_plusieurs_dossiers','noms_ambigus',
                      'correspondant_associe_a_plusieurs_dossiers'):
            conflict_matters={m['id']:m for m,_ in explicit}
            conflict_matters.update({m['id']:m for m in mapped+existing})
            conflict_matters.update({m['id']:m for m,_ in named})
            for matter in conflict_matters.values():
                _upsert_group(desk,matter,participants,40,'uncertain',mail,source,'conflict')
        return {'mail_key':key,'linked':False,'reason':source or 'aucun_indice_unique'}
    level='certain' if confidence>=99 else 'probable'
    status='automatic' if confidence>=97 else 'pending'
    _record_link(desk,mail,key,folder,selected,confidence,level,source,status)
    _upsert_group(desk,selected,participants,confidence,level,mail,source,status)
    return {'mail_key':key,'linked':status=='automatic','matter':selected['id'],
            'confidence':confidence,'level':level,'status':status}


def _record_link(desk,mail,key,folder,matter,confidence,level,source,status):
    identity=digest(key+'|'+matter['id']);recipients=addresses(mail.msg.get('To',''))+addresses(mail.msg.get('Cc',''))
    desk.db.execute('''INSERT OR REPLACE INTO portfolio_mail_links VALUES
      (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(identity,key,mail.mid,mail.root,folder,str(mail.uid),matter['id'],
      confidence,level,source,mail.sender,json.dumps(recipients),mail.timestamp.isoformat(),status,desk.now()))
    desk.db.commit()


def _upsert_group(desk,matter,participants,confidence,level,mail,source,status):
    for email in participants:
        gid=digest(matter['id']+'|'+email);old=desk.db.execute(
          'SELECT * FROM association_groups WHERE id=?',(gid,)).fetchone()
        evidence=json.loads(old['evidence']) if old else []
        proof={'subject':mail.subject,'date':mail.timestamp.isoformat(),'message_id':mail.mid,
               'source':source}
        if proof not in evidence:evidence=(evidence+[proof])[-12:]
        count=max(len(evidence),old['message_count'] if old else 0)
        final_conf=max(confidence,old['confidence'] if old else 0)
        final_status=old['status'] if old and old['status'] in ('confirmed','rejected','conflict') else status
        final_level=level
        if final_status=='pending' and count>=2 and final_conf>=85:
            # Repetition upgrades only the matter link, never the role.
            final_conf=97;final_level='very_likely';final_status='automatic'
            desk.db.execute("UPDATE portfolio_mail_links SET confidence=97,level='very_likely',status='automatic',updated=? WHERE matter=? AND (sender=? OR instr(recipients,?)>0)",
              (desk.now(),matter['id'],email,email))
        desk.db.execute('INSERT OR REPLACE INTO association_groups VALUES (?,?,?,?,?,?,?,?,?)',
          (gid,matter['id'],email,final_conf,final_level,count,json.dumps(evidence,ensure_ascii=False),final_status,desk.now()))
        if final_status in ('pending','conflict'):
            desk.propose(matter,email,{'type':'association_groupee','source':mail.subject,
              'date':mail.timestamp.isoformat(),'indice':str(final_conf)+' % · '+source+
              ' · '+str(count)+' échange(s)','limite':'Dossier proposé ; rôle à confirmer avant divulgation.'})
    desk.db.commit()


def match_evidence(desk,mail):
    """Return portfolio links for the engine's existing explainable matcher."""
    ensure_schema(desk);account=desk.c['mail']['username']+'@'+desk.c['mail']['host'];key=mail.key(account)
    params=[key,mail.mid,mail.root];rows=desk.db.execute('''SELECT matter,confidence,level,source
      FROM portfolio_mail_links WHERE status IN ('automatic','confirmed')
      AND (mail_key=? OR (message_id<>'' AND message_id=?) OR (thread_root<>'' AND thread_root=?))''',params).fetchall()
    result={}
    for row in rows:
        result.setdefault(row['matter'],[]).append({'signal':'association_automatique',
          'weight':min(100,row['confidence']),'value':row['source']})
    return result


def record_engine_link(desk,mail,matter,confidence=100,source='analyse_courriel'):
    if not matter:return
    status='automatic' if confidence>=97 else 'pending'
    _record_link(desk,mail,mail.key(desk.c['mail']['username']+'@'+desk.c['mail']['host']),
                 mail.mailbox,matter,confidence,'certain' if confidence>=99 else 'very_likely',source,status)
    # A recent mail is a genuine external activity signal, unlike a technical
    # index timestamp. Reflect it immediately on the dashboard.
    matter_status(desk,matter)


def grouped_associations(desk,status='pending',limit=200):
    ensure_schema(desk);valid={'pending','automatic','confirmed','rejected','conflict','all'}
    if status not in valid:raise Stop('etat_association_invalide')
    where='' if status=='all' else ' WHERE status=?';params=[] if status=='all' else [status]
    params.append(max(1,min(int(limit),500)))
    matters={m['id']:m for m in load_matters(desk.c)};result=[]
    for row in desk.db.execute('SELECT * FROM association_groups'+where+' ORDER BY confidence DESC,message_count DESC,updated DESC LIMIT ?',params):
        item=dict(row);item['evidence']=json.loads(item['evidence']);item['matter_name']=matters.get(row['matter'],{}).get('client_name','')
        result.append(item)
    return result


def _scan_document_activity(desk,dav,matters,start,batch):
    selected=matters[start:start+batch];checked=errors=0
    for matter in selected:
        try:
            inventory=dav.inventory_large(matter['path']) if hasattr(dav,'inventory_large') else dav.inventory(matter['path'])
            dated=[item for item in inventory if item.get('modified') and _dt(item.get('modified'))]
            newest=max(dated,key=lambda x:_dt(x['modified'])) if dated else None
            desk.db.execute('INSERT OR REPLACE INTO portfolio_document_activity VALUES (?,?,?,?,?)',
              (matter['id'],newest['modified'] if newest else '',newest['path'] if newest else '',
               desk.now(),''));checked+=1
        except Stop as ex:
            desk.db.execute('INSERT OR REPLACE INTO portfolio_document_activity VALUES (?,?,?,?,?)',
              (matter['id'],'','',desk.now(),str(ex)));errors+=1
    desk.db.commit()
    return {'checked':checked,'errors':errors,'next':start+len(selected),'done':start+len(selected)>=len(matters)}


def _scan_calendar(desk,dav,matters):
    desk.db.execute('DELETE FROM portfolio_calendar_links')
    calendar=desk.c.get('calendar',{});now_dt=datetime.now(timezone.utc)
    events=dav.events(calendar.get('urls',[]),now_dt,
      now_dt+timedelta(days=max(180,int(desk.c.get('legal_memory',{}).get('calendar_future_days',730)))),
      calendar.get('timezone','Europe/Paris'))
    count=0
    for event in events:
        text=fold((event.get('summary') or '')+' '+(event.get('description') or ''))
        candidates=[]
        for matter in matters:
            terms=[matter['id'],matter.get('client_name','')]+matter.get('aliases',[])+matter.get('references',[])
            if any(_boundary(term,text) for term in terms):candidates.append(matter)
        unique={matter['id']:matter for matter in candidates}
        if len(unique)!=1:continue
        matter=next(iter(unique.values()));source='caldav:'+str(event.get('uid') or '')
        desk.db.execute('INSERT OR REPLACE INTO portfolio_calendar_links VALUES (?,?,?,?,?,?)',
          (digest(matter['id']+'|'+source+'|'+str(event.get('start',''))),matter['id'],
           event.get('start',''),event.get('summary',''),source,desk.now()));count+=1
    desk.db.commit();return count


def bootstrap(desk,args=None,box=None,dav=None):
    """Bounded resumable cold start: discovery, mail graph, classification, cleanup."""
    ensure_schema(desk);args=args or {};cfg=desk.c.get('portfolio',{})
    progress=desk.settings('portfolio:bootstrap',{})
    if args.get('restart')=='yes' or not progress or progress.get('phase')=='done':
        progress={'phase':'discover','folder_index':0,'offset':0,'examined':0,
          'linked':0,'matter_index':0,'documents_checked':0,'started':desk.now(),'errors':0}
    if progress['phase']=='discover':
        from .workspace import discover
        if dav is None:
            from .dav import DAV
            dav=DAV(desk.c['nextcloud'])
        result=discover(desk,dav)
        if result.get('parcours_partiel'):
            desk.setting('portfolio:bootstrap',progress)
            return {'phase':'discover','suite':True,'progress':progress,**result}
        progress['phase']='mail';progress['folder_index']=0;progress['offset']=0
    matters=sorted(load_matters(desk.c),key=lambda x:x['id'])
    folders=list(dict.fromkeys([desk.c['mail']['inbox'],desk.c['mail']['sent']]))
    own_box=box is None
    if progress['phase']=='mail':
        box=box or Mailbox(desk.c['mail'])
        try:
            folder=folders[progress['folder_index']]
            months=max(12,min(int(cfg.get('bootstrap_months',18)),24))
            since=(datetime.now(timezone.utc)-timedelta(days=months*31)).strftime('%d-%b-%Y')
            uids=box.search(folder,'UNDELETED','SINCE',since)
            batch=max(20,min(int(cfg.get('mail_batch_size',100)),250))
            selected=uids[progress['offset']:progress['offset']+batch]
            for uid in selected:
                try:
                    mail=box.fetch(folder,uid,headers_only=True)
                    if mail.msg.get('List-Id') or mail.msg.get('List-Unsubscribe') or str(mail.msg.get('Auto-Submitted','no')).lower()!='no':
                        continue
                    observed=observe_mail(desk,mail,folder,matters);progress['examined']+=1
                    progress['linked']+=int(observed.get('linked',False))
                except Stop:progress['errors']+=1
            progress['offset']+=len(selected)
            if progress['offset']>=len(uids):
                progress['folder_index']+=1;progress['offset']=0
            if progress['folder_index']<len(folders):
                desk.setting('portfolio:bootstrap',progress)
                return {'phase':'mail','suite':True,'progress':progress}
            progress['phase']='documents';progress['matter_index']=0
        finally:
            if own_box:box.close()
    if progress['phase']=='documents':
        if dav is None:
            from .dav import DAV
            dav=DAV(desk.c['nextcloud'])
        batch=max(1,min(int(cfg.get('folder_batch_size',10)),25))
        scanned=_scan_document_activity(desk,dav,matters,int(progress.get('matter_index',0)),batch)
        progress['matter_index']=scanned['next'];progress['documents_checked']+=scanned['checked']
        progress['errors']+=scanned['errors']
        if not scanned['done']:
            desk.setting('portfolio:bootstrap',progress)
            return {'phase':'documents','suite':True,'progress':progress}
        progress['phase']='calendar'
    if progress['phase']=='calendar':
        if dav is None:
            from .dav import DAV
            dav=DAV(desk.c['nextcloud'])
        try:progress['calendar_links']=_scan_calendar(desk,dav,matters)
        except Stop as ex:
            progress['calendar_warning']=str(ex);progress['errors']+=1
        progress['phase']='classify'
    if progress['phase']=='classify':
        counts=classify_all(desk)
        reconciliation={'checked':0}
        try:reconciliation=reconcile_work_items(desk)
        except Stop as ex:reconciliation={'checked':0,'warning':str(ex)}
        progress.update(phase='done',finished=desk.now(),counts=counts)
        desk.setting('portfolio:bootstrap',progress)
        summary=portfolio_summary(desk)
        return {'phase':'done','suite':False,'progress':progress,
          'active':summary['counts']['active'],'dormant':summary['counts']['dormant'],
          'archived':summary['counts']['archived'],'to_confirm':summary['counts']['to_confirm'],
          'automatic_mail_links':summary['automatic_mail_links'],
          'grouped_confirmations':summary['grouped_confirmations'],'reconciliation':reconciliation,
          'message':(str(summary['counts']['active'])+' dossiers actifs — '+
            str(summary['counts']['dormant'])+' en sommeil — '+str(summary['counts']['archived'])+
            ' archivés · '+str(summary['automatic_mail_links'])+' courriels associés automatiquement — '+
            str(summary['grouped_confirmations'])+' confirmations groupées nécessaires.')}
    desk.setting('portfolio:bootstrap',progress)
    return {'phase':progress['phase'],'suite':True,'progress':progress}


def reconcile_work_items(desk,box=None):
    """Close replied/disappeared mail; reading a message never resolves it."""
    ensure_schema(desk)
    from .desk import report_for
    from .integration import sync_work_items
    sync_work_items(desk);own_box=box is None;box=box or Mailbox(desk.c['mail'])
    checked=handled=archived=drafts=backlog=reopened=0
    limit=max(20,min(int(desk.c.get('portfolio',{}).get('reconcile_batch_size',200)),500))
    backlog_days=max(7,min(int(desk.c.get('portfolio',{}).get('backlog_days',14)),60))
    cutoff=datetime.now(timezone.utc)-timedelta(days=backlog_days)
    try:
        rows=desk.db.execute("SELECT * FROM work_items WHERE state IN ('needs_action','needs_confirmation','processing','drafting','backlog') ORDER BY received DESC LIMIT ?",(limit,)).fetchall()
        # 3.6.0 incorrectly closed reviewed messages merely opened in Roundcube.
        # Recheck those cards once against live IMAP, leaving explicit lawyer
        # decisions, answered mail and existing drafts untouched.
        repair=not desk.settings('portfolio:seen_review_repair_v361',False)
        if repair:
            previous=desk.db.execute("SELECT * FROM work_items WHERE state='handled' AND source_status='review' ORDER BY received DESC LIMIT 501").fetchall()
            if len(previous)>500:previous=previous[:500]
            rows=list(rows)+list(previous)
            manual_keys=set()
            for event in desk.db.execute("SELECT data FROM audit WHERE action='etat_metier_modifie'"):
                try:
                    details=json.loads(event[0])
                    if details.get('state')=='handled':manual_keys.add(details.get('key'))
                except (ValueError,TypeError,AttributeError):pass
        repair_complete=True
        for row in rows:
            checked+=1
            legacy=repair and row['state']=='handled' and row['source_status']=='review'
            if legacy and row['mail_key'] in manual_keys:continue
            try:
                report=report_for(desk.c,row['mail_key'])
                mail=box.fetch(report['source_mailbox'],report['source_uid'],headers_only=True)
            except Stop as ex:
                if str(ex)=='message_imap_disparu':
                    _work_state(desk,row['mail_key'],'archived');archived+=1
                elif legacy:repair_complete=False
                continue
            if '\\Deleted' in mail.flags:
                _work_state(desk,row['mail_key'],'archived');archived+=1;continue
            if '\\Answered' in mail.flags:
                _work_state(desk,row['mail_key'],'handled');handled+=1;continue
            try:
                reason=(box.preflight(mail,report.get('draft_message_id'),allow_seen=True)
                        if isinstance(box,Mailbox) else
                        box.preflight(mail,report.get('draft_message_id')))
            except Stop:
                if legacy:repair_complete=False;continue
                reason=''
            if reason in ('reponse_envoyee_depuis','reponse_envoyee_objet_identique'):
                _work_state(desk,row['mail_key'],'handled');handled+=1
            elif reason in ('brouillon_existant',):
                _work_state(desk,row['mail_key'],'draft_ready');drafts+=1
            elif legacy:
                from .integration import workflow_state
                _work_state(desk,row['mail_key'],workflow_state(row['source_status'],row['source_reason']))
                reopened+=1
            elif (_dt(row['received']) or datetime.now(timezone.utc))<cutoff:
                _work_state(desk,row['mail_key'],'backlog');backlog+=1
        desk.db.commit()
        if repair and repair_complete and len(previous)<501:
            desk.setting('portfolio:seen_review_repair_v361',True)
    finally:
        if own_box:box.close()
    result={'checked':checked,'handled':handled,'archived':archived,'draft_ready':drafts,'backlog':backlog,'reopened':reopened,'at':desk.now()}
    if handled:
        # The sent message, not the generated draft, is the validated style
        # example. Collection remains a separate bounded job.
        try:desk.enqueue('learn',priority=10)
        except Stop:pass
    desk.setting('portfolio:last_reconciliation',result);return result


def _work_state(desk,key,state):
    stamp=desk.now();desk.db.execute('UPDATE work_items SET state=?,updated=?,resolved=? WHERE mail_key=?',
      (state,stamp,stamp if state in ('handled','archived','draft_ready') else None,key))


def perform(desk,kind,args):
    if kind=='organize_cabinet':return bootstrap(desk,args)
    if kind=='classify_portfolio':return {'counts':classify_all(desk)}
    if kind=='reconcile_inbox':return reconcile_work_items(desk)
    if kind=='set_matter_state':return set_matter_state(desk,args)
    if kind=='confirm_matter':return confirm_matter(desk,args)
    if kind=='reject_group':return reject_group(desk,args)
    raise Stop('action_inconnue')

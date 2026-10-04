"""CalDAV agenda, VTODO tasks and supervised weekly work planning.

This module deliberately separates proposals from mutations.  Reading an
agenda or extracting tasks never writes to Nextcloud.  One six-digit approval
can create the reviewed VTODO objects and proposed time blocks.  It never sends
mail, pays, files, signs, deletes or modifies source documents.
"""
from datetime import date, datetime, time as day_time, timezone, timedelta
import json
import re
import secrets
from zoneinfo import ZoneInfo

from .common import Stop, digest, fold, load_matters
from .dav import DAV


TASK_STATES={'todo','in_progress','completed','cancelled','blocked'}
REMOTE_STATES={'todo':'NEEDS-ACTION','in_progress':'IN-PROCESS',
               'completed':'COMPLETED','cancelled':'CANCELLED','blocked':'NEEDS-ACTION'}
MONTHS={'janvier':1,'fevrier':2,'mars':3,'avril':4,'mai':5,'juin':6,
        'juillet':7,'aout':8,'septembre':9,'octobre':10,'novembre':11,'decembre':12}


def _matter_label(task):
    source=str(task.get('source_line','')).strip()
    source=re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*','',source)
    prefix=source.split(':',1)[0].strip() if ':' in source else ''
    prefix=re.sub(r'\s+',' ',prefix)
    if prefix:return prefix[:180]
    if task.get('category')=='personal':return 'Personnel'
    if task.get('category')=='cabinet':return 'Cabinet'
    return str(task.get('matter') or 'Dossier non associé')[:180]


def _display_title(task):
    label=_matter_label(task)
    title=re.sub(r'\s+',' ',str(task.get('title',''))).strip() or 'Tâche'
    suffix=' [AxiorHub]';separator=' — '
    maximum=max(1,500-len(label)-len(separator)-len(suffix))
    return label+separator+title[:maximum]+suffix


def _task_description(task):
    return ('Créé après validation groupée AxiorHub.\nDossier : '+_matter_label(task)+
      '\nRéférence AxiorHub : '+(task.get('matter') or 'non associée')+
      '\nSource : '+str(task.get('source_line',''))+
      '\nAction externe interdite : '+(', '.join(task.get('forbidden_steps',[])) or 'aucune détectée'))


def _slot_description(pid,task,slot):
    return ('Créneau proposé et confirmé dans le programme '+pid+'.'+
      '\nDossier : '+_matter_label(task)+
      '\nRéférence AxiorHub : '+(task.get('matter') or 'non associée')+
      '\nTâche : '+str(task.get('title',''))+
      '\nPartie : '+str(slot.get('part',1))+'. Aucun envoi ni dépôt externe.')


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS calendar_cache(
      id TEXT PRIMARY KEY, uid TEXT NOT NULL, recurrence_id TEXT NOT NULL,
      calendar_url TEXT NOT NULL, href TEXT NOT NULL, etag TEXT NOT NULL,
      title TEXT NOT NULL, description TEXT NOT NULL, location TEXT NOT NULL,
      starts TEXT NOT NULL, ends TEXT NOT NULL, busy INTEGER NOT NULL,
      matter TEXT NOT NULL, matter_candidates TEXT NOT NULL,
      fetched TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS calendar_cache_period ON calendar_cache(starts,ends);
    CREATE INDEX IF NOT EXISTS calendar_cache_matter ON calendar_cache(matter,starts);
    CREATE TABLE IF NOT EXISTS work_tasks_v211(
      id TEXT PRIMARY KEY, external_uid TEXT NOT NULL, calendar_url TEXT NOT NULL,
      href TEXT NOT NULL, etag TEXT NOT NULL, matter TEXT NOT NULL,
      title TEXT NOT NULL, description TEXT NOT NULL, starts TEXT NOT NULL,
      due TEXT NOT NULL, duration_minutes INTEGER NOT NULL, priority INTEGER NOT NULL,
      status TEXT NOT NULL, blocked_by TEXT NOT NULL, action_type TEXT NOT NULL,
      act_type TEXT NOT NULL, mail_key TEXT NOT NULL, source TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL, synced TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS work_tasks_v211_status ON work_tasks_v211(status,due);
    CREATE INDEX IF NOT EXISTS work_tasks_v211_matter ON work_tasks_v211(matter,status);
    CREATE TABLE IF NOT EXISTS work_plan_proposals(
      id TEXT PRIMARY KEY, status TEXT NOT NULL, period_start TEXT NOT NULL,
      period_end TEXT NOT NULL, task_text TEXT NOT NULL, data TEXT NOT NULL,
      challenge_hash TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL,
      decided TEXT NOT NULL, job_id INTEGER, result TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS work_plan_status ON work_plan_proposals(status,expires,created);
    ''');desk.db.commit()


def _workflow_cfg(desk):
    cfg=desk.c.get('nextcloud_workflow') or {}
    enabled=bool(cfg.get('enabled'))
    if enabled:
        required=('url','username','password_file')
        if any(not cfg.get(x) for x in required):raise Stop('compte_nextcloud_technique_incomplet')
        return cfg
    # Read-only agenda remains available through the historical account.  No
    # Tasks or planning write is silently enabled on that account.
    return desk.c['nextcloud']


def _dav(desk):return DAV(_workflow_cfg(desk))


def _calendar_urls(desk):
    cfg=desk.c.get('nextcloud_workflow') or {}
    return cfg.get('calendar_read_urls') or desk.c.get('calendar',{}).get('urls',[])


def _task_urls(desk):
    """Liste réservée aux tâches AxiorHub, puis (lecture seule) les autres listes de tâches choisies par l'avocat."""
    cfg=desk.c.get('nextcloud_workflow') or {}
    value=cfg.get('task_calendar_url','')
    urls=[value] if value else []
    urls+=[x for x in (cfg.get('task_read_urls') or []) if x and x not in urls]
    return urls


def _period(start,end,maximum_days=370):
    try:
        a=datetime.fromisoformat(str(start));b=datetime.fromisoformat(str(end))
    except (ValueError,TypeError):raise Stop('periode_invalide') from None
    if not a.tzinfo:a=a.replace(tzinfo=timezone.utc)
    if not b.tzinfo:b=b.replace(tzinfo=timezone.utc)
    if b<=a or b-a>timedelta(days=maximum_days):raise Stop('periode_invalide')
    return a,b


def _matter_candidates(matters,text):
    value=fold(text);scored=[]
    exact=[m['id'] for m in matters if len(m['id'])>=4 and re.search(
        r'(?<!\w)'+re.escape(fold(m['id']))+r'(?!\w)',value)]
    if len(exact)==1:return exact
    folders=[m['id'] for m in matters if len(fold(m['path'].rsplit('/',1)[-1]))>=8
        and re.search(r'(?<!\w)'+re.escape(fold(m['path'].rsplit('/',1)[-1]))+r'(?!\w)',value)]
    if len(folders)==1:return folders
    for matter in matters:
        terms=[matter['id'],matter.get('client_name','')]+matter.get('references',[])+matter.get('aliases',[])
        hits=[]
        for term in terms:
            term=fold(str(term)).strip()
            if len(term)>=4 and re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',value):hits.append(term)
        if hits:scored.append((max(len(x) for x in hits),len(hits),matter['id']))
    scored.sort(reverse=True)
    return [x[2] for x in scored]


def calendar_events(desk,start,end,matter='',refresh=True,dav=None):
    """Return every event in the period, including unlinked events."""
    ensure_schema(desk);a,b=_period(start,end);matters=load_matters(desk.c)
    if matter and not any(x['id']==matter for x in matters):raise Stop('dossier_absent')
    if refresh:
        client=dav or _dav(desk);tz=desk.c.get('calendar',{}).get('timezone','Europe/Paris')
        urls=_calendar_urls(desk)
        events=client.events(urls,a,b,tz)
        # A fresh REPORT is authoritative for the requested period. Remove the
        # previous slice first so deleted or moved occurrences cannot survive
        # forever in the local cache.
        for url in urls:
            normalized=client.calendar_url(url)
            desk.db.execute('DELETE FROM calendar_cache WHERE calendar_url=? AND starts<? AND ends>?',
                            (normalized,b.astimezone(timezone.utc).isoformat(),
                             a.astimezone(timezone.utc).isoformat()))
        for event in events:
            starts=datetime.fromisoformat(event['start']).astimezone(timezone.utc).isoformat()
            ends=datetime.fromisoformat(event['end']).astimezone(timezone.utc).isoformat()
            candidates=_matter_candidates(matters,(event.get('summary','')+'\n'+event.get('description','')))
            linked=candidates[0] if len(candidates)==1 else ''
            eid=digest('|'.join([event.get('uid',''),event.get('recurrence_id',''),starts,
                                 event.get('source_url','')]))
            desk.db.execute('''INSERT OR REPLACE INTO calendar_cache VALUES
              (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(eid,event.get('uid',''),event.get('recurrence_id',''),
              event.get('source_url',''),event.get('href',''),event.get('etag',''),
              event.get('summary',''),event.get('description',''),event.get('location',''),
              starts,ends,1 if event.get('busy',True) else 0,linked,
              json.dumps(candidates[:5]),desk.now()))
        desk.db.commit()
    a_utc=a.astimezone(timezone.utc).isoformat();b_utc=b.astimezone(timezone.utc).isoformat()
    params=[a_utc,b_utc];where='starts<? AND ends>?'
    if matter:where+=' AND matter=?';params.append(matter)
    rows=[];matter_paths={m['id']:m['path'] for m in matters}
    for row in desk.db.execute('SELECT * FROM calendar_cache WHERE '+where+' ORDER BY starts',
                               (b_utc,a_utc,*params[2:])):
        item=dict(row);item['matter_candidates']=json.loads(item['matter_candidates']);item['busy']=bool(item['busy'])
        item['links']={'caldav_source':item['href'] or item['calendar_url'],
          'matter':'/agent-courriel/dossiers/'+item['matter'] if item['matter'] else '',
          'nextcloud_folder':matter_paths.get(item['matter'],'')}
        rows.append(item)
    linked=sum(1 for x in rows if x['matter']);ambiguous=sum(1 for x in rows if len(x['matter_candidates'])>1)
    return {'period_start':a.isoformat(),'period_end':b.isoformat(),'events':rows,
      'counts':{'all':len(rows),'linked':linked,'unlinked':len(rows)-linked,'ambiguous':ambiguous},
      'warning':'Un événement sans dossier reste affiché. Une absence d’association ne signifie pas une absence d’audience.'}


def _remote_state(value):
    return {'NEEDS-ACTION':'todo','IN-PROCESS':'in_progress','COMPLETED':'completed',
            'CANCELLED':'cancelled'}.get(value,'todo')


def sync_tasks(desk,dav=None):
    ensure_schema(desk);urls=_task_urls(desk)
    if not urls:
        # L'agenda reste actualisé même si aucune liste de tâches n'est configurée.
        try:
            now_dt=datetime.now(timezone.utc)
            calendar_events(desk,(now_dt-timedelta(days=7)).isoformat(),(now_dt+timedelta(days=180)).isoformat(),refresh=True,dav=dav)
        except (Stop,OSError,ValueError):pass
        raise Stop('liste_taches_axiorhub_non_configuree')
    client=dav or _dav(desk);tz=desk.c.get('calendar',{}).get('timezone','Europe/Paris')
    matters=load_matters(desk.c);seen=[]
    for remote in client.todos(urls,tz,2000):
        candidates=_matter_candidates(matters,remote['title']+'\n'+remote.get('description',''))
        matter=candidates[0] if len(candidates)==1 else ''
        tid=digest('caldav-vtodo|'+remote['uid']);state=_remote_state(remote['status']);stamp=desk.now()
        previous=desk.db.execute('SELECT * FROM work_tasks_v211 WHERE id=?',(tid,)).fetchone()
        if previous and previous['blocked_by'] and state=='todo':state='blocked'
        source=json.dumps({'kind':'nextcloud_vtodo','uid':remote['uid'],'remote_present':True},ensure_ascii=False)
        blocked=previous['blocked_by'] if previous else ''
        action_type=previous['action_type'] if previous else ''
        act_type=previous['act_type'] if previous else ''
        mail_key=previous['mail_key'] if previous else ''
        if previous and previous['source']:
            try:
                old_source=json.loads(previous['source']);old_source['remote_present']=True
                source=json.dumps(old_source,ensure_ascii=False)
            except ValueError:pass
        desk.db.execute('''INSERT OR REPLACE INTO work_tasks_v211 VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(tid,remote['uid'],remote['source_url'],
          remote.get('href',''),remote.get('etag',''),matter,remote['title'],remote.get('description',''),
          remote.get('start',''),remote.get('due',''),previous['duration_minutes'] if previous else 0,
          remote.get('priority',0),state,blocked,action_type,act_type,mail_key,source,
          previous['created'] if previous else stamp,stamp,stamp))
        desk.db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?)',
          (tid,matter,remote['title'],remote.get('due',''),'open' if state not in ('completed','cancelled') else 'closed',stamp))
        seen.append(tid)
    placeholders=','.join('?' for _ in seen)
    sql="SELECT id,source FROM work_tasks_v211 WHERE calendar_url IN ("+','.join('?' for _ in urls)+")"
    parameters=list(urls)
    if seen:
        sql+=' AND id NOT IN ('+placeholders+')';parameters+=seen
    missing=0
    for row in desk.db.execute(sql,parameters).fetchall():
        try:source=json.loads(row['source'])
        except (ValueError,TypeError):continue
        if source.get('kind')!='nextcloud_vtodo':continue
        source['remote_present']=False;missing+=1
        desk.db.execute("UPDATE work_tasks_v211 SET status='cancelled',source=?,updated=?,synced=? WHERE id=?",
                        (json.dumps(source,ensure_ascii=False),desk.now(),desk.now(),row['id']))
        desk.db.execute("UPDATE tasks SET status='closed' WHERE id=?",(row['id'],))
    # Keep the agenda cache fresh for dashboards and planning without hiding
    # events that are not yet linked to a client matter.
    now_dt=datetime.now(timezone.utc)
    agenda=calendar_events(desk,(now_dt-timedelta(days=7)).isoformat(),
                           (now_dt+timedelta(days=180)).isoformat(),refresh=True,dav=client)
    desk.db.commit();return {'synchronized':len(seen),'remote_absent':missing,'task_calendar':urls[0],
      'calendar_events':agenda['counts'],'at':desk.now()}


def list_tasks(desk,status='',matter='',limit=300):
    ensure_schema(desk);params=[];where=[];limit=max(1,min(int(limit),500))
    if status:
        states=[x for x in status.split(',') if x in TASK_STATES]
        if not states:raise Stop('etat_tache_invalide')
        where.append('status IN ('+','.join('?' for _ in states)+')');params+=states
    if matter:where.append('matter=?');params.append(matter)
    sql='SELECT * FROM work_tasks_v211'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY CASE status WHEN "in_progress" THEN 0 WHEN "todo" THEN 1 WHEN "blocked" THEN 2 ELSE 3 END,due,created DESC LIMIT ?'
    params.append(limit);rows=[]
    for row in desk.db.execute(sql,params):
        item=dict(row)
        item['links']={'nextcloud_task':item['calendar_url'],
          'matter':'/agent-courriel/dossiers/'+item['matter'] if item['matter'] else '',
          'nextcloud_folder':next((m['path'] for m in load_matters(desk.c)
            if m['id']==item['matter']), '')}
        rows.append(item)
    return rows


def _duration(line):
    matches=list(re.finditer(r'\(\s*(?:(\d+)\s*h\s*)?(\d+)?\s*(?:min(?:ute)?s?)?\s*\)',line,re.I))
    if not matches:return 30,line
    match=matches[-1];hours=int(match.group(1) or 0);minutes=int(match.group(2) or 0)
    value=hours*60+minutes
    if not value or value>600:return 30,line
    return value,(line[:match.start()]+line[match.end():]).strip()


def _french_date(text,year):
    value=fold(text)
    match=re.search(r'\b(\d{1,2})\s+('+('|'.join(MONTHS))+r')(?:\s+(20\d{2}))?\b',value)
    if not match:return ''
    try:return date(int(match.group(3) or year),MONTHS[match.group(2)],int(match.group(1))).isoformat()
    except ValueError:return ''


def _action(text):
    value=fold(text);forbidden=[]
    for token,label in [('payer','paiement'),('envoyer','envoi'),('deposer','dépôt'),('signer','signature')]:
        if re.search(r'\b'+token+r'\w*\b',value):forbidden.append(label)
    if re.search(r'\b(repondre|reponse)\b.*\b(mail|courriel|client|confrere)\b',value):
        return 'email_draft','',forbidden
    acts=[('conclusions','conclusions'),('assignation','assignation'),('mise en demeure','mise_en_demeure'),
          ('consultation','consultation'),('protocole','protocole'),('contrat','contrat'),('cgv','contrat'),
          ('bail','contrat'),('bordereau','bordereau'),('note d audience','note_audience'),
          ('dossier de plaidoirie','autre')]
    for token,kind in acts:
        if token in value:return 'act_project',kind,forbidden
    return 'manual','',forbidden


def extract_tasks(desk,text,period_start=''):
    ensure_schema(desk);text=str(text or '').strip()
    if not text or len(text)>20000:raise Stop('liste_taches_requise_20000_caracteres_maximum')
    try:year=datetime.fromisoformat(period_start).year if period_start else datetime.now().year
    except ValueError:year=datetime.now().year
    matters=load_matters(desk.c);items=[]
    for raw in text.splitlines():
        line=re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*','',raw).strip()
        if not line:continue
        duration,line=_duration(line);prefix=line.split(':',1)[0].strip()
        candidates=_matter_candidates(matters,prefix)
        category='personal' if fold(prefix).startswith('personnel') else ('cabinet' if fold(prefix).startswith('cabinet exemple') else 'matter')
        matter=candidates[0] if len(candidates)==1 else ''
        title=line.split(':',1)[1].strip() if ':' in line else line
        due=_french_date(title,year);blocked=''
        lower=fold(title)
        if 'en attente' in lower:
            blocked=title[lower.index('en attente'):][:300]
        dependency=_french_date(title[lower.index('apres'):],year) if 'apres' in lower else ''
        action_type,act_type,forbidden=_action(title)
        items.append({'position':len(items)+1,'source_line':raw.strip(),'title':title[:500],
          'matter':matter,'matter_candidates':candidates[:5],'category':category,
          'duration_minutes':duration,'due':due,'not_before':dependency,
          'blocked_by':blocked,'action_type':action_type,'act_type':act_type,
          'forbidden_steps':forbidden,'status':'blocked' if blocked else 'todo'})
        if len(items)>=100:break
    if not items:raise Stop('aucune_tache_reconnue')
    return items


def _calendar_busy(desk,a,b,dav=None):
    data=calendar_events(desk,a.isoformat(),b.isoformat(),refresh=True,dav=dav)
    return [(datetime.fromisoformat(x['starts']),datetime.fromisoformat(x['ends']))
            for x in data['events'] if x['busy']],data


def _work_intervals(day,zone,hours):
    result=[]
    for start,end in hours:
        sh,sm=map(int,start.split(':'));eh,em=map(int,end.split(':'))
        result.append((datetime.combine(day,day_time(sh,sm),zone),datetime.combine(day,day_time(eh,em),zone)))
    return result


def _schedule(tasks,a,b,busy,max_daily,cfg):
    zone=ZoneInfo(cfg.get('timezone','Europe/Paris'));hours=cfg.get('working_hours',[['09:00','12:00'],['14:00','18:00']])
    weekdays=set(cfg.get('weekdays',[0,1,2,3,4]));cursor_day=a.astimezone(zone).date();last_day=(b-timedelta(microseconds=1)).astimezone(zone).date()
    occupancy=[(x.astimezone(zone),y.astimezone(zone)) for x,y in busy];daily={};slots=[];unscheduled=[]
    ordered=sorted(tasks,key=lambda x:(bool(x['blocked_by']),x['due'] or '9999-12-31',-x['duration_minutes'],x['position']))
    for task in ordered:
        if task['blocked_by']:
            unscheduled.append({'position':task['position'],'reason':'bloquee','detail':task['blocked_by']});continue
        remaining=task['duration_minutes'];part=0;day=cursor_day
        if task['not_before']:
            day=max(day,date.fromisoformat(task['not_before']))
        due=date.fromisoformat(task['due']) if task['due'] else last_day
        deadline=min(last_day,due);made=[]
        while remaining>0 and day<=deadline:
            if day.weekday() not in weekdays:day+=timedelta(days=1);continue
            used=daily.get(day,0)
            if used>=max_daily:day+=timedelta(days=1);continue
            chunk=min(remaining,120,max_daily-used);allocated=False
            for start,end in _work_intervals(day,zone,hours):
                pointer=start
                while pointer+timedelta(minutes=30)<=end:
                    length=max(30,((chunk+29)//30)*30);finish=pointer+timedelta(minutes=length)
                    if finish>end:pointer+=timedelta(minutes=30);continue
                    if any(pointer<y and finish>x for x,y in occupancy):pointer+=timedelta(minutes=30);continue
                    part+=1;slot={'position':task['position'],'part':part,'start':pointer.isoformat(),
                      'end':finish.isoformat(),'planned_minutes':min(chunk,remaining),'title':task['title'],
                      'matter_label':_matter_label(task),'display_title':_display_title(task)}
                    slots.append(slot);made.append(slot);occupancy.append((pointer,finish));daily[day]=used+length
                    remaining-=min(chunk,remaining);allocated=True;break
                if allocated:break
            if not allocated:day+=timedelta(days=1)
        if remaining>0:
            unscheduled.append({'position':task['position'],'reason':'capacite_insuffisante',
                                'minutes_restantes':remaining})
    return slots,unscheduled,{x.isoformat():y for x,y in sorted(daily.items())}


def propose_plan(desk,args,dav=None):
    ensure_schema(desk)
    start_value=str(args.get('period_start',''));end_value=str(args.get('period_end',''))
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',start_value) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',end_value):
        zone=ZoneInfo(desk.c.get('planning',{}).get('timezone','Europe/Paris'))
        a=datetime.combine(date.fromisoformat(start_value),day_time.min,zone)
        b=datetime.combine(date.fromisoformat(end_value)+timedelta(days=1),day_time.min,zone)
    else:
        a,b=_period(start_value,end_value,31)
    if b<=a or b-a>timedelta(days=31):raise Stop('periode_invalide')
    maximum=max(120,min(int(args.get('max_daily_minutes',360)),480))
    tasks=extract_tasks(desk,args.get('task_text',''),a.isoformat())
    busy,agenda=_calendar_busy(desk,a,b,dav=dav)
    cfg={**desk.c.get('calendar',{}),**desk.c.get('planning',{})}
    slots,unscheduled,daily=_schedule(tasks,a,b,busy,maximum,cfg)
    code=f'{secrets.randbelow(900000)+100000}';pid=secrets.token_hex(16);stamp=desk.now()
    expires=(datetime.now(timezone.utc)+timedelta(minutes=max(10,min(int(
      desk.c.get('planning',{}).get('approval_minutes',30)),120)))).isoformat()
    data={'tasks':tasks,'slots':slots,'unscheduled':unscheduled,'daily_minutes':daily,
          'agenda_counts':agenda['counts'],'max_daily_minutes':maximum,
          'safety':{'creates_vtodo':True,'creates_planning_events':True,'sends_email':False,
                    'pays':False,'files_or_signs':False,'modifies_originals':False}}
    desk.db.execute('INSERT INTO work_plan_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
      (pid,'pending',a.isoformat(),b.isoformat(),args['task_text'],json.dumps(data,ensure_ascii=False),
       digest(pid+'|'+code),stamp,expires,'',None,''));desk.db.commit()
    desk.audit('work_plan_proposed',{'proposal':pid,'tasks':len(tasks),'slots':len(slots)})
    return {'proposal_id':pid,'confirmation_code':code,'expires_at':expires,
      'summary':str(len(tasks))+' tâche(s) et '+str(len(slots))+' créneau(x) proposés.',
      'plan':data,'next_step':'Relisez le plan puis recopiez le code pour créer ce programme dans Nextcloud.'}


def get_plan(desk,pid):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',pid or ''):raise Stop('programme_invalide')
    row=desk.db.execute('SELECT * FROM work_plan_proposals WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('programme_absent')
    item=dict(row);item['data']=json.loads(item['data'])
    if item['result']:
        try:item['result']=json.loads(item['result'])
        except ValueError:pass
    item.pop('challenge_hash',None);item.pop('task_text',None);return item


def approve_plan(desk,pid,code):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',pid or '') or not re.fullmatch(r'\d{6}',str(code or '')):
        raise Stop('confirmation_programme_invalide')
    row=desk.db.execute('SELECT * FROM work_plan_proposals WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('programme_absent')
    if row['status']!='pending':raise Stop('programme_deja_traite')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(row['expires']):
        desk.db.execute("UPDATE work_plan_proposals SET status='expired',decided=? WHERE id=?",(desk.now(),pid));desk.db.commit();raise Stop('programme_expire')
    if not secrets.compare_digest(row['challenge_hash'],digest(pid+'|'+str(code))):raise Stop('confirmation_programme_invalide')
    # Refuse before queueing if write targets have not been explicitly selected.
    cfg=desk.c.get('nextcloud_workflow') or {}
    if not cfg.get('enabled') or not cfg.get('task_calendar_url') or not cfg.get('planning_calendar_url'):
        raise Stop('espaces_nextcloud_axiorhub_non_configures')
    job=desk.enqueue('apply_work_plan',{'proposal':pid},priority=0)
    desk.db.execute("UPDATE work_plan_proposals SET status='approved',decided=?,job_id=? WHERE id=?",(desk.now(),job,pid));desk.db.commit()
    desk.audit('work_plan_approved',{'proposal':pid,'job':job})
    return {'proposal_id':pid,'status':'approved','job_id':job,
      'message':'Programme confirmé. Les tâches et créneaux vont être créés dans les espaces AxiorHub de Nextcloud.'}


def reject_plan(desk,pid):
    ensure_schema(desk);row=desk.db.execute('SELECT status FROM work_plan_proposals WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('programme_absent')
    if row['status']!='pending':raise Stop('programme_deja_traite')
    desk.db.execute("UPDATE work_plan_proposals SET status='rejected',decided=? WHERE id=?",(desk.now(),pid));desk.db.commit()
    desk.audit('work_plan_rejected',{'proposal':pid});return {'proposal_id':pid,'status':'rejected'}


def _latest_mail(desk,matter):
    rows=desk.db.execute('''SELECT mail_key FROM work_items WHERE matter=?
      AND state IN ('needs_action','needs_confirmation') ORDER BY received DESC LIMIT 2''',(matter,)).fetchall()
    return rows[0][0] if len(rows)==1 else ''


def _queue_output(desk,task):
    if task['action_type']=='email_draft' and task['matter']:
        key=_latest_mail(desk,task['matter'])
        if key:return desk.enqueue('prepare_reply',{'key':key,'instruction':task['title']},priority=0),'email_draft'
        return None,'email_absent_ou_ambigu'
    if task['action_type']=='act_project' and task['matter']:
        return desk.enqueue('draft_act',{'matter':task['matter'],'act_type':task['act_type'] or 'autre',
                            'instruction':task['title']},priority=0),'act_project'
    return None,'manual'


def apply_plan(desk,args,dav=None):
    ensure_schema(desk);pid=str(args.get('proposal',''));row=desk.db.execute(
      "SELECT * FROM work_plan_proposals WHERE id=? AND status IN ('approved','applying')",(pid,)).fetchone()
    if not row:raise Stop('programme_non_confirme')
    data=json.loads(row['data']);cfg=desk.c.get('nextcloud_workflow') or {};client=dav or _dav(desk)
    desk.db.execute("UPDATE work_plan_proposals SET status='applying' WHERE id=?",(pid,));desk.db.commit()
    created_tasks=0;created_slots=0;outputs=[];warnings=[];stamp=desk.now()
    slot_by_position={}
    for slot in data['slots']:slot_by_position.setdefault(slot['position'],[]).append(slot)
    for task in data['tasks']:
        entity=digest(pid+'|task|'+str(task['position']))
        uid='axiorhub-'+entity[:32]+'@mail-agent.local'
        tid=digest('caldav-vtodo|'+uid)
        display_title=_display_title(task);description=_task_description(task)
        starts=(slot_by_position.get(task['position']) or [{}])[0].get('start','')
        zone=ZoneInfo(desk.c.get('planning',{}).get('timezone','Europe/Paris'))
        due=datetime.combine(date.fromisoformat(task['due']),day_time(17,0),zone).isoformat() if task['due'] else ''
        try:
            client.put_todo(cfg['task_calendar_url'],uid,display_title,description,starts,due,
                            REMOTE_STATES[task['status']],3 if task['due'] else 5,0)
            created_tasks+=1
        except Stop as ex:
            if str(ex)!='http_412':warnings.append('Tâche '+str(task['position'])+' : '+str(ex))
        source=json.dumps({'kind':'approved_plan','proposal':pid,'position':task['position']})
        desk.db.execute('''INSERT OR REPLACE INTO work_tasks_v211 VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(tid,uid,cfg['task_calendar_url'],'','*',task['matter'],
          display_title,description,starts,due,task['duration_minutes'],3 if task['due'] else 5,
          task['status'],task['blocked_by'],task['action_type'],task['act_type'],'',source,stamp,stamp,stamp))
        desk.db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?)',(tid,task['matter'],display_title,due,
          'open' if task['status'] not in ('completed','cancelled') else 'closed',stamp))
        try:job,kind=_queue_output(desk,task)
        except Stop as ex:
            job=None;kind='mise_en_file_impossible';warnings.append(
              'Production '+str(task['position'])+' : '+str(ex))
        outputs.append({'position':task['position'],'kind':kind,'job_id':job})
    task_by_position={task['position']:task for task in data['tasks']}
    for slot in data['slots']:
        event_id=digest(pid+'|slot|'+str(slot['position'])+'|'+str(slot['part']))
        task=task_by_position.get(slot['position'],{'title':slot.get('title','Tâche')})
        try:
            client.put_event(cfg['planning_calendar_url'],event_id,_display_title(task),
              datetime.fromisoformat(slot['start']),datetime.fromisoformat(slot['end']),
              _slot_description(pid,task,slot))
            created_slots+=1
        except Stop as ex:
            if str(ex)!='http_412':warnings.append('Créneau '+str(slot['position'])+'.'+str(slot['part'])+' : '+str(ex))
    result={'tasks_created':created_tasks,'slots_created':created_slots,'outputs':outputs,
      'warnings':warnings,'forbidden_actions_executed':0,'email_sent':False,'documents_modified':False}
    final='done' if not warnings else 'partial'
    desk.db.execute('UPDATE work_plan_proposals SET status=?,result=? WHERE id=?',(final,json.dumps(result,ensure_ascii=False),pid));desk.db.commit()
    desk.audit('work_plan_applied',{'proposal':pid,'status':final,'tasks':created_tasks,'slots':created_slots})
    return result


def update_task_status(desk,args,dav=None):
    ensure_schema(desk);tid=str(args.get('task',''));state=str(args.get('status',''))
    if state not in TASK_STATES-{'blocked'}:raise Stop('etat_tache_invalide')
    row=desk.db.execute('SELECT * FROM work_tasks_v211 WHERE id=?',(tid,)).fetchone()
    if not row:raise Stop('tache_absente')
    if not row['external_uid'].startswith('axiorhub-'):raise Stop('tache_externe_lecture_seule')
    client=dav or _dav(desk);percent=100 if state=='completed' else (50 if state=='in_progress' else 0)
    client.put_todo(row['calendar_url'],row['external_uid'],row['title'],row['description'],
      row['starts'] or None,row['due'] or None,REMOTE_STATES[state],row['priority'],percent,row['etag'])
    desk.db.execute('UPDATE work_tasks_v211 SET status=?,updated=?,synced=? WHERE id=?',(state,desk.now(),desk.now(),tid))
    desk.db.execute('UPDATE tasks SET status=? WHERE id=?',('closed' if state in ('completed','cancelled') else 'open',tid));desk.db.commit()
    desk.audit('task_status_changed',{'task':tid,'status':state});return {'task':tid,'status':state,'nextcloud_updated':True}


def edit_task(desk,args,dav=None):
    ensure_schema(desk);tid=str(args.get('task',''));row=desk.db.execute(
      'SELECT * FROM work_tasks_v211 WHERE id=?',(tid,)).fetchone()
    if not row:raise Stop('tache_absente')
    if not row['external_uid'].startswith('axiorhub-'):raise Stop('tache_externe_lecture_seule')
    title=re.sub(r'\s+',' ',str(args.get('title') or row['title'])).strip()
    due=str(args.get('due') if args.get('due') is not None else row['due'])
    if not 3<=len(title)<=500:raise Stop('titre_tache_invalide')
    if due:
        try:datetime.fromisoformat(due if 'T' in due else due+'T17:00:00+02:00')
        except ValueError:raise Stop('date_tache_invalide') from None
    client=dav or _dav(desk);client.put_todo(row['calendar_url'],row['external_uid'],title,row['description'],
      row['starts'] or None,due or None,REMOTE_STATES[row['status']],row['priority'],
      100 if row['status']=='completed' else 0,row['etag'])
    desk.db.execute('UPDATE work_tasks_v211 SET title=?,due=?,updated=?,synced=? WHERE id=?',
                    (title,due,desk.now(),desk.now(),tid))
    desk.db.execute('UPDATE tasks SET title=?,due=? WHERE id=?',(title,due,tid));desk.db.commit()
    desk.audit('task_edited',{'task':tid});return {'task':tid,'title':title,'due':due,'nextcloud_updated':True}


def schedule_task(desk,args,dav=None):
    ensure_schema(desk);tid=str(args.get('task',''));start=str(args.get('start',''))
    try:
        begins=datetime.fromisoformat(start);duration=max(15,min(int(args.get('duration_minutes',60)),480))
    except (ValueError,TypeError):raise Stop('creneau_tache_invalide') from None
    if not begins.tzinfo:begins=begins.replace(tzinfo=ZoneInfo(desk.c.get('planning',{}).get('timezone','Europe/Paris')))
    remote=desk.db.execute('SELECT title,description,matter FROM work_tasks_v211 WHERE id=?',(tid,)).fetchone()
    local=desk.db.execute('SELECT title,matter FROM tasks WHERE id=?',(tid,)).fetchone()
    if not remote and not local:raise Stop('tache_absente')
    cfg=desk.c.get('nextcloud_workflow') or {};calendar=cfg.get('planning_calendar_url','')
    if not cfg.get('enabled') or not calendar:raise Stop('agenda_planification_non_configure')
    title=(remote or local)['title'];matter=(remote or local)['matter'];description=(remote['description'] if remote else '')
    description=('Tâche AxiorHub '+tid+'\nDossier : '+str(matter or 'non relié')+'\n'+description)[:2000]
    event_id=digest('manual-task-slot|'+tid+'|'+begins.isoformat())
    client=dav or _dav(desk);uid=client.put_event(calendar,event_id,title,begins,
      begins+timedelta(minutes=duration),description)
    desk.audit('task_scheduled',{'task':tid,'start':begins.isoformat(),'duration_minutes':duration})
    return {'task':tid,'event_uid':uid,'start':begins.isoformat(),'duration_minutes':duration,
      'calendar_updated':True}


EVENT_UID=re.compile(r'axiorhub-([0-9a-f]{64})@mail-agent\.local')


def _event_times(desk,args):
    start=str(args.get('start',''))
    try:
        begins=datetime.fromisoformat(start);duration=int(args.get('duration_minutes',60))
    except (ValueError,TypeError):raise Stop('creneau_evenement_invalide') from None
    if not 5<=duration<=1440:raise Stop('creneau_evenement_invalide')
    if not begins.tzinfo:begins=begins.replace(tzinfo=ZoneInfo(desk.c.get('planning',{}).get('timezone','Europe/Paris')))
    return begins,begins+timedelta(minutes=duration)


def _event_text(desk,args):
    title=re.sub(r'\s+',' ',str(args.get('title',''))).strip()
    if not 3<=len(title)<=200:raise Stop('titre_evenement_invalide')
    matter=str(args.get('matter','') or '')
    if matter and not any(x['id']==matter for x in load_matters(desk.c)):raise Stop('dossier_absent')
    description=('Événement géré dans AxiorHub.'+chr(10)+'Dossier : '+(matter or 'non relié')+chr(10)+str(args.get('notes','') or '')[:800]).strip()
    return title,matter,description[:2000]


def create_event(desk,args,dav=None):
    """Crée un événement dans le calendrier de planification (jamais dans un calendrier que l'avocat n'a pas choisi)."""
    ensure_schema(desk);cfg=desk.c.get('nextcloud_workflow') or {};calendar=cfg.get('planning_calendar_url','')
    if not cfg.get('enabled') or not calendar:raise Stop('agenda_planification_non_configure')
    begins,ends=_event_times(desk,args);title,matter,description=_event_text(desk,args)
    event_id=digest('manual-event|'+title+'|'+begins.isoformat()+'|'+desk.now())
    client=dav or _dav(desk);uid=client.put_event(calendar,event_id,title,begins,ends,description)
    try:calendar_events(desk,(begins-timedelta(days=1)).isoformat(),(ends+timedelta(days=1)).isoformat(),refresh=True,dav=client)
    except (Stop,OSError,ValueError):pass
    desk.audit('agenda_event_created',{'start':begins.isoformat(),'matter':matter})
    return {'event_uid':uid,'start':begins.isoformat(),'end':ends.isoformat(),'calendar_updated':True,
            'message':'Événement créé dans l’agenda Nextcloud : '+title+'.'}


def _own_event(desk,uid):
    match=EVENT_UID.fullmatch(str(uid or ''))
    if not match:raise Stop('evenement_externe_lecture_seule')
    row=desk.db.execute('SELECT * FROM calendar_cache WHERE uid=? ORDER BY starts LIMIT 1',(uid,)).fetchone()
    if not row:raise Stop('evenement_absent')
    return match.group(1),row


def edit_event(desk,args,dav=None):
    """Déplace ou renomme un événement créé par AxiorHub. Les événements des autres calendriers restent en lecture seule."""
    ensure_schema(desk);eid,row=_own_event(desk,args.get('uid'))
    begins,ends=_event_times(desk,args);title,matter,description=_event_text(desk,args)
    client=dav or _dav(desk);client.put_event(row['calendar_url'],eid,title,begins,ends,description,replace=True)
    desk.db.execute('DELETE FROM calendar_cache WHERE uid=?',(row['uid'],));desk.db.commit()
    try:calendar_events(desk,(begins-timedelta(days=1)).isoformat(),(ends+timedelta(days=1)).isoformat(),refresh=True,dav=client)
    except (Stop,OSError,ValueError):pass
    desk.audit('agenda_event_edited',{'start':begins.isoformat()})
    return {'event_uid':row['uid'],'start':begins.isoformat(),'calendar_updated':True,'message':'Événement modifié dans l’agenda Nextcloud.'}


def cancel_event(desk,args,dav=None):
    ensure_schema(desk)
    if args.get('confirm')!='yes':raise Stop('confirmation_suppression_requise')
    _,row=_own_event(desk,args.get('uid'))
    client=dav or _dav(desk);client.delete_event(row['calendar_url'],row['uid'],row['etag'])
    desk.db.execute('DELETE FROM calendar_cache WHERE uid=?',(row['uid'],));desk.db.commit()
    desk.audit('agenda_event_deleted',{'uid':row['uid'][:20]})
    return {'event_uid':row['uid'],'calendar_updated':True,'message':'Événement supprimé de l’agenda Nextcloud.'}


def perform(desk,kind,args):
    if kind=='propose_work_plan':return propose_plan(desk,args)
    if kind=='sync_caldav_tasks':return sync_tasks(desk)
    if kind=='apply_work_plan':return apply_plan(desk,args)
    if kind=='update_work_task':return update_task_status(desk,args)
    if kind=='edit_work_task':return edit_task(desk,args)
    if kind=='schedule_work_task':return schedule_task(desk,args)
    if kind=='create_agenda_event':return create_event(desk,args)
    if kind=='edit_agenda_event':return edit_event(desk,args)
    if kind=='cancel_agenda_event':return cancel_event(desk,args)
    raise Stop('action_inconnue')

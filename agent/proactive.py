"""Proactive, source-bound matter monitoring and the daily lawyer dashboard.

Monitoring creates reviewable signals only.  It never sends mail, changes a
calendar, files a document, validates a fact, or chooses legal strategy.
"""
from datetime import datetime, timezone, timedelta
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop, digest, load_matters
from .dav import DAV
from .index import DocumentIndex


SIGNAL_STATES = {'open', 'acknowledged', 'snoozed', 'resolved'}
SEVERITIES = {'critical': 0, 'high': 1, 'normal': 2, 'low': 3}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS matter_watch(
      matter TEXT PRIMARY KEY, snapshot TEXT NOT NULL, last_checked TEXT NOT NULL,
      last_change TEXT NOT NULL, last_result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS proactive_signals(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, category TEXT NOT NULL,
      severity TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
      source_ids TEXT NOT NULL, proposed_action TEXT NOT NULL,
      state TEXT NOT NULL, due TEXT NOT NULL, fingerprint TEXT NOT NULL,
      first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, snoozed_until TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS daily_dashboards(
      day TEXT PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS proactive_state
      ON proactive_signals(state,severity,due,last_seen);
    CREATE INDEX IF NOT EXISTS proactive_matter
      ON proactive_signals(matter,state,last_seen);
    ''')
    desk.db.commit()


def _dt(value):
    if not value:return None
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (ValueError,TypeError):return None
    if not parsed.tzinfo:parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _matter(desk, mid):
    result=next((m for m in load_matters(desk.c) if m['id']==mid),None)
    if not result:raise Stop('dossier_absent')
    return result


def _signal(desk,mid,category,severity,title,detail,sources,action,due='',fingerprint=''):
    if severity not in SEVERITIES:severity='normal'
    sources=list(dict.fromkeys(str(x) for x in sources if x))[:20]
    identity='|'.join([mid,category,fingerprint or title,due])
    sid=digest(identity);stamp=desk.now()
    old=desk.db.execute('SELECT state,snoozed_until,fingerprint FROM proactive_signals WHERE id=?',(sid,)).fetchone()
    state=old['state'] if old else 'open';snooze=old['snoozed_until'] if old else ''
    if state=='snoozed' and (_dt(snooze) or datetime.min.replace(tzinfo=timezone.utc))<=datetime.now(timezone.utc):
        state='open';snooze=''
    first=desk.db.execute('SELECT first_seen FROM proactive_signals WHERE id=?',(sid,)).fetchone()
    desk.db.execute('''INSERT OR REPLACE INTO proactive_signals
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
      (sid,mid,category,severity,title[:500],detail[:4000],json.dumps(sources,ensure_ascii=False),
       action[:1000],state,due, fingerprint or digest(detail),first[0] if first else stamp,stamp,snooze))
    return sid


def _snapshot(index,mid):
    rows=index.db.execute('SELECT path,etag,modified,error FROM docs WHERE matter=? ORDER BY path',(mid,)).fetchall()
    return {row[0]:{'etag':row[1] or '','modified':row[2] or '','error':row[3] or ''} for row in rows}


def monitor_matter(desk,args):
    """Compare one matter with the prior observation and refresh its signals."""
    ensure_schema(desk);mid=args.get('matter','');matter=_matter(desk,mid)
    cfg=desk.c.get('proactive',{});now_dt=datetime.now(timezone.utc);stamp=desk.now()
    index=DocumentIndex(desk.c['state_dir']);indexed=_snapshot(index,mid);current=indexed
    live_error='';scanning=False
    if cfg.get('monitor_live_nextcloud',True):
        try:
            client=DAV(desk.c['nextcloud'])
            if hasattr(client,'inventory_step'):
                key='watch_scan_v300:'+mid
                saved=desk.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
                scan,complete=client.inventory_step(matter['path'],json.loads(saved[0]) if saved else None)
                if complete:
                    current=scan['files'];desk.db.execute('DELETE FROM settings WHERE key=?',(key,))
                    for name in current:current[name]['error']=indexed.get(name,{}).get('error','')
                    desk.db.execute("UPDATE proactive_signals SET state='resolved' WHERE matter=? AND category='watch_unavailable'",(mid,))
                else:
                    scanning=True
                    desk.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(scan)))
                desk.db.commit()
            else:
                inventory=client.inventory_large(matter['path'])
                current={item['path']:{'etag':item.get('etag',''),'modified':item.get('modified',''),
                  'error':indexed.get(item['path'],{}).get('error','')} for item in inventory}
        except Stop as ex:live_error=str(ex)
    prior=desk.db.execute('SELECT snapshot FROM matter_watch WHERE matter=?',(mid,)).fetchone()
    previous=json.loads(prior[0]) if prior else None
    if scanning and previous is not None:current=previous
    created=[]
    if previous is None and current and not indexed:
        try:desk.enqueue('index',{'matter':mid},priority=50)
        except Stop:pass
    if previous is not None:
        new=sorted(set(current)-set(previous))
        changed=sorted(path for path in set(current)&set(previous)
                       if current[path]['etag']!=previous[path]['etag'])
        if new or changed:
            names=(new+changed)[:12]
            detail=('Nouveaux fichiers : '+', '.join(new[:6]) if new else '')
            if changed:detail+=(' · ' if detail else '')+'Fichiers modifiés : '+', '.join(changed[:6])
            if len(new)+len(changed)>12:detail+=' · '+str(len(new)+len(changed)-12)+' autre(s).'
            fp=digest(json.dumps([(p,current[p]['etag']) for p in new+changed],sort_keys=True))
            created.append(_signal(desk,mid,'documents_changed','normal',
              'Pièces nouvelles ou modifiées dans '+matter.get('client_name',mid),detail,names,
              'Examiner les pièces puis actualiser la mémoire ou la stratégie si elles sont utiles.',fingerprint=fp))
            try:desk.enqueue('index',{'matter':mid},priority=50)
            except Stop:pass
    if not current and (matter.get('registered_at') or matter.get('correspondents')):
        created.append(_signal(desk,mid,'matter_not_indexed','low','Dossier non encore indexé',
          'Aucun document exploitable n’est actuellement indexé pour ce dossier.',[],
          'Lancer l’indexation du dossier.',fingerprint='no-index'))
    if live_error:
        created.append(_signal(desk,mid,'watch_unavailable','low','Surveillance Nextcloud incomplète',
          'Les métadonnées du dossier n’ont pas pu être relues : '+live_error,[],
          ('Limiter le périmètre ou vérifier la profondeur du dossier. Les données locales restent disponibles.' if 'volumineux' in live_error or 'limite' in live_error or 'profond' in live_error else 'Vérifier la connexion Nextcloud ; les dernières données locales restent disponibles.'),fingerprint=live_error))
    errors=[p for p,v in current.items() if v['error']]
    if errors:
        created.append(_signal(desk,mid,'document_errors','normal','Pièces à contrôler',
          str(len(errors))+' fichier(s) n’ont pas pu être extraits : '+', '.join(errors[:8]),errors[:20],
          'Ouvrir les fichiers originaux et corriger les pièces illisibles.',fingerprint=digest('|'.join(errors))))

    horizon=now_dt+timedelta(days=int(cfg.get('deadline_warning_days',14)))
    for row in desk.db.execute('''SELECT id,event_type,event_at,title,detail,source_id,source_path
      FROM timeline_events WHERE matter=? AND status<>'stale'
      AND event_type IN ('deadline','calendar','task') ORDER BY event_at LIMIT 100''',(mid,)):
        when=_dt(row['event_at'])
        if not when or when<now_dt-timedelta(hours=12) or when>horizon:continue
        severity='critical' if when<=now_dt+timedelta(days=2) else 'high'
        appointment=row['event_type']=='calendar'
        created.append(_signal(desk,mid,'upcoming_appointment' if appointment else 'upcoming_deadline',severity,
          ('Rendez-vous à venir : ' if appointment else 'Échéance à vérifier : ')+row['title'],
          row['detail'] or 'Événement du dossier à contrôler.',[row['source_id'] or row['source_path']],
          ('Vérifier le rendez-vous et les éléments à préparer.' if appointment else
           'Vérifier la date, le responsable et la diligence à accomplir.'),
          due=when.isoformat(),fingerprint=row['id']))
    for row in desk.db.execute("SELECT id,title,due FROM tasks WHERE matter=? AND status='open' ORDER BY due",(mid,)):
        when=_dt(row['due'])
        if when and when<=horizon:
            severity='critical' if when<=now_dt+timedelta(days=2) else 'high'
            created.append(_signal(desk,mid,'open_task',severity,'Tâche à échéance : '+row['title'],
              'Tâche ouverte du dossier. Échéance : '+row['due'],['task-'+row['id']],
              'Vérifier ou accomplir cette tâche.',due=row['due'],fingerprint=row['id']))

    threshold=now_dt-timedelta(hours=int(cfg.get('unanswered_warning_hours',24)))
    for row in desk.db.execute("""SELECT mail_key,subject,sender,received,state FROM work_items
      WHERE matter=? AND state IN ('needs_action','needs_confirmation') ORDER BY received""",(mid,)):
        received=_dt(row['received'])
        if received and received<=threshold:
            hours=max(0,int((now_dt-received).total_seconds()/3600))
            created.append(_signal(desk,mid,'unanswered_mail','high' if hours>=48 else 'normal',
              'Courriel sans réponse : '+row['subject'],
              'Message de '+row['sender']+' en attente depuis environ '+str(hours)+' heure(s).',
              ['mail-'+row['mail_key']], 'Préparer une réponse, choisir le dossier ou classer le message.',
              fingerprint=row['mail_key']))

    latest_source=index.db.execute('SELECT MAX(indexed_at) FROM knowledge_chunks WHERE matter=?',(mid,)).fetchone()[0]
    strategy=desk.db.execute("SELECT id,updated FROM strategy_analyses WHERE matter=? AND status<>'archived' ORDER BY version DESC LIMIT 1",(mid,)).fetchone()
    if strategy and latest_source and strategy['updated']<latest_source and cfg.get('stale_strategy_on_new_source',True):
        created.append(_signal(desk,mid,'stale_strategy','normal','Analyse stratégique à actualiser',
          'Des sources ont été indexées après la dernière analyse stratégique.',
          ['strategy-'+strategy['id']], 'Relancer l’analyse après examen des nouvelles pièces.',fingerprint=latest_source))

    gaps=desk.db.execute("""SELECT COUNT(*) FROM evidence_matrix_rows
      WHERE matter=? AND status='proposed' AND missing_evidence NOT IN ('','[]')""",(mid,)).fetchone()[0]
    if gaps:
        created.append(_signal(desk,mid,'evidence_gaps','normal','Preuves manquantes à examiner',
          str(gaps)+' ligne(s) de la matrice mentionnent une preuve manquante.',[],
          'Ouvrir la matrice, vérifier les lacunes et demander les pièces utiles.',fingerprint=str(gaps)))
    acts=desk.db.execute("SELECT COUNT(*) FROM act_projects WHERE matter=? AND status='proposed'",(mid,)).fetchone()[0]
    if acts:
        created.append(_signal(desk,mid,'act_review','normal','Projet(s) d’acte à relire',
          str(acts)+' projet(s) interne(s) attendent votre contrôle.',[],
          'Relire les sources, compléter la recherche juridique et valider ou archiver le projet.',fingerprint=str(acts)))
    conflicts=desk.db.execute("SELECT COUNT(*) FROM legal_memory_conflicts WHERE matter=? AND status='open'",(mid,)).fetchone()[0]
    if conflicts:
        created.append(_signal(desk,mid,'memory_conflict','high','Contradiction(s) à résoudre',
          str(conflicts)+' contradiction(s) sont signalées dans la mémoire juridique.',[],
          'Comparer les sources avant toute rédaction engageante.',fingerprint=str(conflicts)))

    refreshed=set(created)
    conditional={'matter_not_indexed','document_errors','upcoming_deadline','upcoming_appointment','open_task',
      'unanswered_mail','stale_strategy','evidence_gaps','act_review','memory_conflict','watch_unavailable'}
    for old in desk.db.execute("SELECT id,category FROM proactive_signals WHERE matter=? AND state='open'",(mid,)):
        if old['category'] in conditional and old['id'] not in refreshed:
            desk.db.execute("UPDATE proactive_signals SET state='resolved',last_seen=? WHERE id=?",(stamp,old['id']))

    encoded=json.dumps(current,sort_keys=True,ensure_ascii=False)
    changed_at=stamp if previous is not None and previous!=current else (desk.db.execute(
      'SELECT last_change FROM matter_watch WHERE matter=?',(mid,)).fetchone() or [stamp])[0]
    result={'matter':mid,'signals_refreshed':len(created),'documents':len(current),
            'first_observation':previous is None,'changed':previous is not None and previous!=current}
    desk.db.execute('INSERT OR REPLACE INTO matter_watch VALUES(?,?,?,?,?)',
      (mid,encoded,stamp,changed_at,json.dumps(result,ensure_ascii=False)))
    desk.db.commit();return result


def monitor_all(desk,args):
    """Queue a bounded, resumable sweep; user jobs remain ahead of it."""
    ensure_schema(desk);after=str(args.get('after',''));limit=max(1,min(int(
      desk.c.get('proactive',{}).get('monitor_batch_size',10)),20))
    from .portfolio import portfolio_rows
    active={row['matter'] for row in portfolio_rows(desk,['active'],limit=5000)}
    matters=sorted((m for m in load_matters(desk.c) if m['id'] in active),key=lambda x:x['id'])
    remaining=[m for m in matters if m['id']>after]
    free=max(0,98-desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0])
    selected=remaining[:min(limit,free)]
    for matter in selected:desk.enqueue('monitor_matter',{'matter':matter['id']},priority=60)
    return {'dossiers_mis_en_attente':len(selected),'suite':len(remaining)>len(selected),
            'after':selected[-1]['id'] if selected else after}


def signals(desk,state='open',matter='',limit=100):
    ensure_schema(desk);limit=max(1,min(int(limit),300));params=[];where=[]
    desk.db.execute("UPDATE proactive_signals SET state='open',snoozed_until='' WHERE state='snoozed' AND snoozed_until<>'' AND snoozed_until<=?",(desk.now(),))
    desk.db.commit()
    if state and state!='all':
        valid=[x for x in str(state).split(',') if x in SIGNAL_STATES]
        if not valid:raise Stop('etat_signal_invalide')
        where.append('state IN ('+','.join('?' for _ in valid)+')');params+=valid
    if matter:where.append('matter=?');params.append(matter)
    elif state!='all':
        from .portfolio import portfolio_rows
        active={row['matter'] for row in portfolio_rows(desk,['active','to_confirm'],limit=5000)}
        if not active:return []
        where.append('matter IN ('+','.join('?' for _ in active)+')');params+=sorted(active)
    sql=('SELECT * FROM proactive_signals'+((' WHERE '+' AND '.join(where)) if where else '')+
        " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, CASE WHEN due='' THEN 1 ELSE 0 END,due,last_seen DESC LIMIT ?")
    params.append(limit)
    result=[]
    for row in desk.db.execute(sql,params):
        item=dict(row);item['source_ids']=json.loads(item['source_ids']);result.append(item)
    return result


def change_signal(desk,kind,args):
    ensure_schema(desk);sid=str(args.get('signal',''))
    row=desk.db.execute('SELECT * FROM proactive_signals WHERE id=?',(sid,)).fetchone()
    if not row:raise Stop('signal_absent')
    if kind=='ack_signal':state='acknowledged';until=''
    elif kind=='resolve_signal':state='resolved';until=''
    elif kind=='snooze_signal':
        hours=max(1,min(int(args.get('hours',24)),720));state='snoozed'
        until=(datetime.now(timezone.utc)+timedelta(hours=hours)).isoformat()
    else:raise Stop('action_inconnue')
    desk.db.execute('UPDATE proactive_signals SET state=?,snoozed_until=?,last_seen=? WHERE id=?',
      (state,until,desk.now(),sid));desk.db.commit()
    desk.audit(kind,{'signal':sid,'matter':row['matter'],'state':state})
    return {'signal':sid,'state':state,'snoozed_until':until}


def build_daily_dashboard(desk,args=None):
    ensure_schema(desk)
    from .integration import dashboard as base_dashboard
    base=base_dashboard(desk);now_dt=datetime.now(timezone.utc)
    all_signals=signals(desk,'open',limit=200)
    daily_categories={'documents_changed','upcoming_deadline','upcoming_appointment',
      'open_task','unanswered_mail','evidence_gaps','memory_conflict'}
    active=[item for item in all_signals if item['category'] in daily_categories]
    matters={m['id']:m for m in load_matters(desk.c)}
    priorities=[]
    for item in active[:20]:
        item['matter_name']=matters.get(item['matter'],{}).get('client_name',item['matter'])
        priorities.append(item)
    deadlines=[x for x in active if x['category'] in ('upcoming_deadline','open_task')][:20]
    changes=[]
    for row in desk.db.execute('SELECT matter,last_checked,last_change,last_result FROM matter_watch ORDER BY last_change DESC LIMIT 12'):
        value=dict(row);value['matter_name']=matters.get(row['matter'],{}).get('client_name',row['matter']);changes.append(value)
    jobs=[dict(x) for x in desk.db.execute("SELECT id,kind,status,priority,created,finished,result FROM jobs WHERE status='error' ORDER BY id DESC LIMIT 10")]
    try:local_now=datetime.now(ZoneInfo(desk.c.get('proactive',{}).get('timezone','Europe/Paris')))
    except ZoneInfoNotFoundError:local_now=now_dt
    local_start=local_now.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    local_end=local_start+timedelta(days=1)
    today_rows=[]
    for row in desk.db.execute('SELECT * FROM work_items ORDER BY received DESC LIMIT 1000'):
        received=_dt(row['received']) or _dt(row['updated'])
        if received and local_start<=received<local_end:today_rows.append(dict(row))
    mail_to_answer=[x for x in today_rows if x['state'] in ('needs_action','needs_confirmation')]
    drafts_today=[x for x in today_rows if x['state']=='draft_ready']
    ignored_today=[x for x in today_rows if x['state']=='ignored']
    confirmations=desk.db.execute("SELECT COUNT(*) FROM association_groups WHERE status IN ('pending','conflict')").fetchone()[0]
    seven_days=now_dt+timedelta(days=7)
    deadlines_7d=[x for x in active if x['category']=='upcoming_deadline' and _dt(x.get('due')) and _dt(x['due'])<=seven_days]
    agenda=[]
    for row in desk.db.execute('''SELECT id,title,description,location,starts,ends,matter,href
      FROM calendar_cache WHERE starts>=? AND starts<? ORDER BY starts LIMIT 50''',
      (now_dt.isoformat(),seven_days.isoformat())):
        item=dict(row);item['matter_name']=matters.get(item['matter'],{}).get('client_name','')
        item['source']=item['href'] or 'calendar-'+item['id'];agenda.append(item)
    planned_tasks=[]
    for row in desk.db.execute('''SELECT id,title,matter,due,status,blocked_by,action_type,calendar_url
      FROM work_tasks_v211 WHERE status IN ('todo','in_progress','blocked')
      ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END,due LIMIT 50'''):
        item=dict(row);item['matter_name']=matters.get(item['matter'],{}).get('client_name','')
        planned_tasks.append(item)
    recommended=[]
    for item in priorities[:4]:
        recommended.append({'label':item['proposed_action'],'matter':item['matter'],
          'matter_name':item['matter_name'],'sources':item['source_ids'],'reason':item['title']})
    for item in mail_to_answer[:max(0,5-len(recommended))]:
        recommended.append({'label':'Examiner le courriel et préparer une réponse.',
          'matter':item.get('matter',''),'matter_name':matters.get(item.get('matter',''),{}).get('client_name',''),
          'sources':[item['mail_key']],'reason':item['subject']})
    if len(recommended)<5 and agenda:
        item=agenda[0];recommended.append({'label':'Préparer cet événement de l’agenda.',
          'matter':item['matter'],'matter_name':item['matter_name'],'sources':[item['source']],
          'reason':item['title']+' — '+item['starts']})
    data={'day':local_now.date().isoformat(),'generated_at':desk.now(),'priorities':priorities,
      'deadlines':deadlines,'recent_changes':changes,'mail_counts':base['counts'],
      'recent_matters':base['recent_matters'],'active_jobs':base['jobs'],'failed_jobs':jobs,
      'portfolio':base.get('portfolio',{}),
      'agenda_next_7_days':agenda,'open_nextcloud_tasks':planned_tasks,
      'today':{'mail_needing_reply':len(mail_to_answer),'drafts_prepared':len(drafts_today),
        'deadlines_within_7_days':len(deadlines_7d),'association_groups_to_confirm':confirmations,
        'commercial_or_automatic_ignored':len(ignored_today)},
      'recommended_actions':recommended,
      'signal_counts':{row[0]:row[1] for row in desk.db.execute("SELECT severity,COUNT(*) FROM proactive_signals WHERE state='open' GROUP BY severity")},
      'limits':['Les signaux reposent sur les données déjà synchronisées et indexées.',
        'Ils ne prouvent ni un délai procédural ni l’accomplissement d’une diligence.',
        'Aucune action externe ni aucun envoi automatique.']}
    desk.db.execute('INSERT OR REPLACE INTO daily_dashboards VALUES(?,?,?)',
      (data['day'],json.dumps(data,ensure_ascii=False),desk.now()));desk.db.commit()
    return data


def latest_dashboard(desk):
    ensure_schema(desk);row=desk.db.execute('SELECT data FROM daily_dashboards ORDER BY day DESC LIMIT 1').fetchone()
    if row:
        value=json.loads(row[0])
        try:today=datetime.now(ZoneInfo(desk.c.get('proactive',{}).get('timezone','Europe/Paris'))).date().isoformat()
        except ZoneInfoNotFoundError:today=datetime.now(timezone.utc).date().isoformat()
        if value.get('day')==today and 'today' in value:return value
    return build_daily_dashboard(desk)


def perform(desk,kind,args):
    if kind=='monitor_matter':return monitor_matter(desk,args)
    if kind=='monitor_all':return monitor_all(desk,args)
    if kind=='build_daily_dashboard':return build_daily_dashboard(desk,args)
    if kind in ('ack_signal','snooze_signal','resolve_signal'):return change_signal(desk,kind,args)
    raise Stop('action_inconnue')

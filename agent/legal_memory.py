"""Versioned, source-bound legal memory and unified matter timeline.

This module never decides whether an allegation is true.  It stores what a
source says, preserves the human validation state and exposes enough provenance
to reopen the original material.  It contains no mail sending or file mutation.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path, PurePosixPath
import re

from .common import Stop, digest, fold, load_matters
from .index import DocumentIndex
from .state import State


RECORD_TYPES = {
    'party', 'claim', 'instruction', 'negotiation', 'amount', 'deadline',
    'event', 'jurisdiction', 'case_number', 'completed_action',
    'planned_action', 'open_question', 'document', 'other',
}
RECORD_STATUSES = {'suggested', 'validated', 'pinned', 'disputed', 'archived'}
TIMELINE_TYPES = {'document', 'email_received', 'email_sent', 'draft', 'task',
                  'calendar', 'fact', 'deadline', 'completed_action'}
SINGLETONS = {'jurisdiction', 'case_number'}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS legal_memory_records(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, record_type TEXT NOT NULL,
      title TEXT NOT NULL, content TEXT NOT NULL, actor TEXT NOT NULL,
      event_date TEXT NOT NULL, confidence REAL NOT NULL, status TEXT NOT NULL,
      sources TEXT NOT NULL, source_snapshot TEXT NOT NULL, origin TEXT NOT NULL,
      revision INTEGER NOT NULL, supersedes TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL, validated_at TEXT NOT NULL,
      validation_note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS legal_memory_history(
      id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, revision INTEGER NOT NULL,
      data TEXT NOT NULL, changed_at TEXT NOT NULL, reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS legal_memory_conflicts(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, record_type TEXT NOT NULL,
      left_record TEXT NOT NULL, right_record TEXT NOT NULL, reason TEXT NOT NULL,
      status TEXT NOT NULL, created TEXT NOT NULL, resolved TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS timeline_events(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, event_type TEXT NOT NULL,
      event_at TEXT NOT NULL, end_at TEXT NOT NULL, title TEXT NOT NULL,
      detail TEXT NOT NULL, source_id TEXT NOT NULL, source_kind TEXT NOT NULL,
      source_path TEXT NOT NULL, source_modified TEXT NOT NULL,
      confidence REAL NOT NULL, status TEXT NOT NULL, signature TEXT NOT NULL,
      updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS legal_memory_matter
      ON legal_memory_records(matter,status,record_type,event_date);
    CREATE INDEX IF NOT EXISTS legal_memory_history_record
      ON legal_memory_history(record_id,revision);
    CREATE INDEX IF NOT EXISTS legal_memory_conflict_matter
      ON legal_memory_conflicts(matter,status);
    CREATE INDEX IF NOT EXISTS timeline_matter
      ON timeline_events(matter,event_at,event_type);
    ''')
    desk.db.commit()


def matter(desk, mid):
    found=next((m for m in load_matters(desk.c) if m['id']==mid),None)
    if not found:raise Stop('dossier_absent')
    return found


def _clean(value, maximum=8000):
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or '')).strip()
    return value[:maximum]


def _date(value):
    value=_clean(value,80)
    if not value:return ''
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if not parsed.tzinfo:parsed=parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except ValueError:
        # A source may only provide a year/month or a conventional date. Keep
        # the literal value visible instead of inventing a day.
        return value


def _snapshot(sources, ids):
    by_id={s.get('id'):s for s in sources}
    result=[]
    for sid in ids:
        source=by_id.get(sid)
        if not source:raise Stop('source_memoire_invalide')
        excerpt=_clean(source.get('excerpt',''),12000)
        result.append({'id':sid,'kind':source.get('kind',''),
          'path':source.get('path') or source.get('subject',''),
          'modified':source.get('modified') or source.get('date',''),
          'excerpt_sha256':digest(excerpt),'excerpt':excerpt[:1200]})
    return result


def _record_id(mid, record):
    kind=record['record_type']
    identity='|'.join([mid,kind,fold(record.get('title','')),
                       fold(record.get('content','')),record.get('event_date','')])
    return digest(identity)


def _row_data(row):
    return {key:row[key] for key in row.keys()}


def upsert_record(desk, mid, record, sources, origin='model'):
    ensure_schema(desk)
    kind=record.get('record_type','')
    if kind not in RECORD_TYPES:raise Stop('type_memoire_invalide')
    source_ids=list(dict.fromkeys(record.get('source_ids') or []))
    if not source_ids or len(source_ids)>12:raise Stop('sources_memoire_requises')
    snapshot=_snapshot(sources,source_ids)
    title=_clean(record.get('title'),500);content=_clean(record.get('content'),8000)
    if not title or not content:raise Stop('contenu_memoire_invalide')
    confidence={'high':0.9,'medium':0.65,'low':0.4}.get(record.get('confidence'),0.4)
    confirmed=record.get('status')=='confirmed' and all(
        item.get('kind')=='lawyer_validated_fact' for item in snapshot)
    status='validated' if confirmed else 'suggested'
    rid=_record_id(mid,{**record,'title':title,'content':content})
    old=desk.db.execute('SELECT * FROM legal_memory_records WHERE id=?',(rid,)).fetchone()
    if not old:
        # Un fait refusé par l'avocat n'est jamais reproposé, même reformulé légèrement.
        for refused in desk.db.execute("SELECT id,title,content FROM legal_memory_records WHERE matter=? AND record_type=? AND status='disputed'",(mid,kind)):
            if fold(refused['content'])==fold(content) or fold(refused['title'])==fold(title):
                return refused['id']
    stamp=desk.now();encoded_sources=json.dumps(source_ids,ensure_ascii=False)
    encoded_snapshot=json.dumps(snapshot,ensure_ascii=False)
    if old:
        # Never downgrade or silently replace a human decision on a rescan.
        human=old['status'] in ('validated','pinned','disputed','archived')
        kept_status=old['status'] if human else status
        if human:
            title,content=old['title'],old['content']
            actor,event_date=old['actor'],old['event_date']
            changed=old['sources']!=encoded_sources or old['source_snapshot']!=encoded_snapshot
        else:
            actor,event_date=_clean(record.get('actor'),500),_date(record.get('event_date'))
            changed=any((old['title']!=title,old['content']!=content,old['actor']!=actor,
                         old['event_date']!=event_date,old['sources']!=encoded_sources,
                         old['source_snapshot']!=encoded_snapshot))
        revision=old['revision']
        if changed:
            desk.db.execute('INSERT INTO legal_memory_history(record_id,revision,data,changed_at,reason) VALUES (?,?,?,?,?)',
                (rid,revision,json.dumps(_row_data(old),ensure_ascii=False),stamp,'nouvelle_extraction_sourcee'))
            revision+=1
        desk.db.execute('''UPDATE legal_memory_records SET title=?,content=?,actor=?,event_date=?,
          confidence=?,status=?,sources=?,source_snapshot=?,origin=?,revision=?,updated=? WHERE id=?''',
          (title,content,actor,event_date,
           confidence,kept_status,encoded_sources,encoded_snapshot,origin,revision,stamp,rid))
    else:
        desk.db.execute('''INSERT INTO legal_memory_records VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (rid,mid,kind,title,content,_clean(record.get('actor'),500),
           _date(record.get('event_date')),confidence,status,encoded_sources,
           encoded_snapshot,origin,1,'',stamp,stamp,stamp if status=='validated' else '',''))
    return rid


def ingest_extraction(desk, mid, extraction, sources):
    matter(desk,mid);ensure_schema(desk)
    ids=[]
    for record in extraction.get('records',[]):
        ids.append(upsert_record(desk,mid,record,sources,'structured_extraction'))
    for conflict in extraction.get('contradictions',[]):
        source_ids=list(dict.fromkeys(conflict.get('source_ids') or []))
        _snapshot(sources,source_ids)
        text=_clean(conflict.get('description'),4000)
        if not text:continue
        cid=digest(mid+'|model-conflict|'+fold(text)+'|'+','.join(source_ids))
        desk.db.execute('''INSERT OR IGNORE INTO legal_memory_conflicts
          VALUES (?,?,?,?,?,?,?,?,?)''',(cid,mid,'other','','',text,'open',desk.now(),''))
    detect_conflicts(desk,mid)
    desk.db.commit()
    return {'records':len(ids),'conflicts':desk.db.execute(
        "SELECT COUNT(*) FROM legal_memory_conflicts WHERE matter=? AND status='open'",(mid,)).fetchone()[0]}


def detect_conflicts(desk,mid):
    for kind in SINGLETONS:
        rows=desk.db.execute('''SELECT * FROM legal_memory_records WHERE matter=? AND record_type=?
          AND status NOT IN ('archived','disputed') ORDER BY updated DESC''',(mid,kind)).fetchall()
        for left in rows:
            for right in rows:
                if left['id']>=right['id'] or fold(left['content'])==fold(right['content']):continue
                cid=digest(mid+'|'+kind+'|'+min(left['id'],right['id'])+'|'+max(left['id'],right['id']))
                desk.db.execute('''INSERT OR IGNORE INTO legal_memory_conflicts
                  VALUES (?,?,?,?,?,?,?,?,?)''',(cid,mid,kind,left['id'],right['id'],
                  'Deux valeurs actives différentes demandent une vérification.','open',desk.now(),''))


def change_record(desk,kind,args):
    ensure_schema(desk);rid=args.get('record','')
    row=desk.db.execute('SELECT * FROM legal_memory_records WHERE id=?',(rid,)).fetchone()
    if not row:raise Stop('information_memoire_absente')
    if args.get('matter') and args['matter']!=row['matter']:raise Stop('memoire_autre_dossier')
    stamp=desk.now();before=json.dumps(_row_data(row),ensure_ascii=False)
    desk.db.execute('INSERT INTO legal_memory_history(record_id,revision,data,changed_at,reason) VALUES (?,?,?,?,?)',
                    (rid,row['revision'],before,stamp,kind))
    if kind=='validate_memory':
        text=_clean(args.get('text') or row['content'],8000)
        if not text:raise Stop('correction_memoire_invalide')
        desk.db.execute('''UPDATE legal_memory_records SET content=?,status='validated',revision=revision+1,
          updated=?,validated_at=?,validation_note=? WHERE id=?''',
          (text,stamp,stamp,_clean(args.get('note'),1000),rid))
    elif kind=='pin_memory':
        desk.db.execute("UPDATE legal_memory_records SET status='pinned',revision=revision+1,updated=?,validated_at=? WHERE id=?",
                        (stamp,stamp,rid))
    elif kind=='dispute_memory':
        desk.db.execute("UPDATE legal_memory_records SET status='disputed',revision=revision+1,updated=?,validation_note=? WHERE id=?",
                        (stamp,_clean(args.get('note'),1000),rid))
    elif kind=='archive_memory':
        desk.db.execute("UPDATE legal_memory_records SET status='archived',revision=revision+1,updated=? WHERE id=?",(stamp,rid))
    else:raise Stop('action_memoire_invalide')
    desk.audit(kind,{'record':rid,'matter':row['matter']});desk.db.commit()
    sync_timeline(desk,row['matter'])
    return {'record':rid,'status':desk.db.execute(
        'SELECT status FROM legal_memory_records WHERE id=?',(rid,)).fetchone()[0]}


def resolve_conflict(desk,args):
    ensure_schema(desk);cid=args.get('conflict','')
    row=desk.db.execute('SELECT * FROM legal_memory_conflicts WHERE id=?',(cid,)).fetchone()
    if not row:raise Stop('contradiction_absente')
    if args.get('matter') and args['matter']!=row['matter']:raise Stop('contradiction_autre_dossier')
    if row['status']!='open':raise Stop('contradiction_deja_traitee')
    desk.db.execute("UPDATE legal_memory_conflicts SET status='resolved',resolved=? WHERE id=?",(desk.now(),cid))
    desk.audit('resolve_conflict',{'conflict':cid,'matter':row['matter']});desk.db.commit()
    return {'contradiction':'traitee'}


def _timeline_put(desk,mid,event_type,event_at,title,detail='',source_id='',source_kind='',
                  source_path='',source_modified='',confidence=1.0,status='active',end_at=''):
    if event_type not in TIMELINE_TYPES:return
    title=_clean(title,1000);detail=_clean(detail,5000);event_at=_date(event_at)
    source_id=_clean(source_id,200);source_path=_clean(source_path,2000)
    identity='|'.join([mid,event_type,source_id,event_at,title])
    tid=digest(identity);signature=digest('|'.join([title,detail,source_modified,status,end_at]))
    desk.db.execute('''INSERT INTO timeline_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET event_at=excluded.event_at,end_at=excluded.end_at,
      title=excluded.title,detail=excluded.detail,source_kind=excluded.source_kind,
      source_path=excluded.source_path,source_modified=excluded.source_modified,
      confidence=excluded.confidence,status=excluded.status,signature=excluded.signature,
      updated=excluded.updated''',(tid,mid,event_type,event_at,_date(end_at),title,detail,
      source_id,_clean(source_kind,100),source_path,_clean(source_modified,100),
      float(confidence),status,signature,desk.now()))


def sync_timeline(desk,mid,calendar_events=None):
    """Rebuild the active local projection for one matter, preserving audit data."""
    m=matter(desk,mid);ensure_schema(desk);index=DocumentIndex(desk.c['state_dir'])
    desk.db.execute("UPDATE timeline_events SET status='stale',updated=? WHERE matter=? AND source_kind<>'calendar'",
                    (desk.now(),mid))
    for path,modified,error in index.db.execute('SELECT path,modified,error FROM docs WHERE matter=?',(mid,)):
        sid=digest(mid+'|document|'+path)
        _timeline_put(desk,mid,'document',modified,PurePosixPath(path).name,error,sid,'document',path,modified,
                      1.0,'error' if error else 'active')
    groups={}
    for row in index.db.execute('''SELECT source_id,path,modified,kind,chunk_no,text,meta
      FROM knowledge_chunks WHERE matter=? AND kind IN ('email_received','email_history','email_sent')
      ORDER BY source_id,chunk_no''',(mid,)):
        item=groups.setdefault(row[0],{'path':row[1],'modified':row[2],'kind':row[3],'text':[],'meta':{}})
        item['text'].append(row[5] or '')
        if row[4]==0:
            try:item['meta']=json.loads(row[6] or '{}')
            except (ValueError,TypeError):pass
    for sid,item in groups.items():
        meta=item['meta'];sent=item['kind']=='email_sent';title=meta.get('subject') or PurePosixPath(item['path']).name
        _timeline_put(desk,mid,'email_sent' if sent else 'email_received',item['modified'],title,
          (meta.get('sender','')+'\n'+''.join(item['text'])[:1800]).strip(),sid,item['kind'],item['path'],item['modified'])
    state=State(desk.c['state_dir'])
    from .desk import report_for
    for key,status,_,stamp in state.rows(2000):
        try:report=report_for(desk.c,key)
        except Stop:continue
        if report.get('matter')!=mid or not report.get('draft_body'):continue
        _timeline_put(desk,mid,'draft',stamp,report.get('subject','Projet de réponse'),
          report['draft_body'][:1800],key,'mail_report','INBOX.Drafts' if status=='drafted' else 'Projet interne',stamp,
          status='active')
    for row in desk.db.execute('SELECT * FROM tasks WHERE matter=?',(mid,)):
        _timeline_put(desk,mid,'task',row['due'] or row['created'],row['title'],row['status'],row['id'],'task','',row['created'],
                      status='active' if row['status']=='open' else row['status'])
    for row in desk.db.execute("SELECT * FROM legal_memory_records WHERE matter=? AND status<>'archived'",(mid,)):
        if not row['event_date']:continue
        event_type={'deadline':'deadline','completed_action':'completed_action'}.get(row['record_type'],'fact')
        snap=json.loads(row['source_snapshot']);path=snap[0].get('path','') if snap else ''
        _timeline_put(desk,mid,event_type,row['event_date'],row['title'],row['content'],row['id'],'legal_memory',path,row['updated'],
                      row['confidence'],row['status'])
    if calendar_events is not None:
        desk.db.execute("UPDATE timeline_events SET status='stale',updated=? WHERE matter=? AND source_kind='calendar'",
                        (desk.now(),mid))
        for event in calendar_events:
            _timeline_put(desk,mid,'calendar',event['start'],event.get('summary') or 'Événement agenda',
              event.get('description',''),event.get('uid') or digest(json.dumps(event,sort_keys=True)),
              'calendar','Agenda Nextcloud',event['start'],0.9,'active',event.get('end',''))
    desk.db.commit()
    return desk.db.execute("SELECT COUNT(*) FROM timeline_events WHERE matter=? AND status<>'stale'",(mid,)).fetchone()[0]


def _calendar_for_matter(desk,m):
    cc=desk.c.get('calendar',{})
    if not cc.get('urls'):return [],'agenda_non_configure'
    from .dav import DAV
    now=datetime.now(timezone.utc)
    settings=desk.c.get('legal_memory',{})
    past=max(0,min(int(settings.get('calendar_past_days',730)),3650))
    future=max(0,min(int(settings.get('calendar_future_days',730)),3650))
    try:events=DAV(desk.c['nextcloud']).events(cc['urls'],now-timedelta(days=past),
            now+timedelta(days=future),cc.get('timezone','Europe/Paris'))
    except Stop as ex:return [],str(ex)
    refs=[m['id']]+list(m.get('references',[]))+list(m.get('aliases',[]))
    client=m.get('client_name','')
    if len(fold(client))>=6:refs.append(client)
    refs=[fold(x).strip() for x in refs if len(fold(x).strip())>=4]
    selected=[]
    for event in events:
        hay=fold((event.get('summary','')+' '+event.get('description','')))
        if any(ref in hay for ref in refs):selected.append(event)
    return selected,''


def sync_matter_memory(desk,args):
    m=matter(desk,args.get('matter',''));events,error=_calendar_for_matter(desk,m)
    count=sync_timeline(desk,m['id'],events if not error else None)
    ensure_schema(desk)
    desk.setting('legal_memory_'+m['id'],{'at':desk.now(),'calendar_error':error,
      'calendar_events':len(events),'timeline_events':count})
    return {'dossier':m['id'],'chronologie':count,'evenements_agenda':len(events),
            'agenda_erreur':error,'memoire':memory_summary(desk,m['id'])}


def memory_records(desk,mid,statuses=None,limit=200):
    matter(desk,mid);ensure_schema(desk);limit=max(1,min(int(limit),500))
    params=[mid];sql='SELECT * FROM legal_memory_records WHERE matter=?'
    if statuses:
        statuses=[x for x in statuses if x in RECORD_STATUSES]
        if not statuses:return []
        sql+=' AND status IN ('+','.join('?' for _ in statuses)+')';params+=statuses
    sql+=' ORDER BY CASE status WHEN \'pinned\' THEN 0 WHEN \'validated\' THEN 1 WHEN \'suggested\' THEN 2 ELSE 3 END, event_date DESC,updated DESC LIMIT ?'
    params.append(limit)
    result=[]
    for row in desk.db.execute(sql,params):
        item=_row_data(row);item['sources']=json.loads(item['sources']);item['source_snapshot']=json.loads(item['source_snapshot'])
        result.append(item)
    return result


def timeline(desk,mid,limit=300):
    matter(desk,mid);ensure_schema(desk);limit=max(1,min(int(limit),500))
    return [dict(row) for row in desk.db.execute('''SELECT * FROM timeline_events
      WHERE matter=? AND status<>'stale' ORDER BY CASE WHEN event_at='' THEN 1 ELSE 0 END,
      event_at DESC,updated DESC LIMIT ?''',(mid,limit))]


def conflicts(desk,mid):
    matter(desk,mid);ensure_schema(desk)
    return [dict(row) for row in desk.db.execute(
      "SELECT * FROM legal_memory_conflicts WHERE matter=? AND status='open' ORDER BY created DESC",(mid,))]


def memory_summary(desk,mid):
    ensure_schema(desk)
    counts={row[0]:row[1] for row in desk.db.execute(
        'SELECT status,COUNT(*) FROM legal_memory_records WHERE matter=? GROUP BY status',(mid,))}
    return {'counts':counts,'open_conflicts':desk.db.execute(
      "SELECT COUNT(*) FROM legal_memory_conflicts WHERE matter=? AND status='open'",(mid,)).fetchone()[0],
      'timeline_events':desk.db.execute(
      "SELECT COUNT(*) FROM timeline_events WHERE matter=? AND status<>'stale'",(mid,)).fetchone()[0]}


def memory_sources(desk,mid,question='',limit=12):
    """Sources for matter chat. Status remains explicit; only human-confirmed
    records are labelled reliable in the excerpt."""
    records=memory_records(desk,mid,['pinned','validated','suggested'],200)
    tokens={x for x in re.findall(r'[a-z0-9]{3,}',fold(question))}
    def score(row):
        hay=fold(row['title']+' '+row['content']+' '+row['record_type'])
        return (20 if row['status']=='pinned' else 10 if row['status']=='validated' else 0)+sum(hay.count(x) for x in tokens)
    selected=sorted(records,key=lambda x:(-score(x),x['updated']))[:max(1,min(int(limit),20))]
    labels={'validated':'validé par l’avocat','pinned':'épinglé et validé par l’avocat',
            'suggested':'proposé par l’IA, non validé'}
    return [{'id':'memory-'+row['id'][:20],'kind':'structured_memory',
      'path':'Mémoire structurée du dossier','modified':row['updated'],
      'excerpt':('[STATUT : '+labels[row['status']]+', confiance '+str(round(row['confidence'],2))+'] '
        +row['record_type']+' — '+row['title']+'\n'+row['content']+'\nSources originales : '
        +', '.join(row['sources'])),'partial':False,'memory_status':row['status']} for row in selected]


def validated_fact_sources(desk,mid,question='',limit=12):
    """Sources de rédaction : uniquement les faits validés ou épinglés par l'avocat (jamais un fait proposé ou refusé)."""
    return [item for item in memory_sources(desk,mid,question,limit) if item.get('memory_status') in ('validated','pinned')]


def perform(desk,kind,args):
    if kind=='sync_legal_memory':return sync_matter_memory(desk,args)
    if kind in ('validate_memory','pin_memory','dispute_memory','archive_memory'):
        return change_record(desk,kind,args)
    if kind=='resolve_conflict':return resolve_conflict(desk,args)
    raise Stop('action_memoire_invalide')

"""Four-hour differential preparation and a source-fresh morning briefing.

All external effects are delegated to existing supervised jobs.  A missing or
ambiguous source produces a visible blocked suggestion, never a guessed filing.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import PurePosixPath
import re
from zoneinfo import ZoneInfo

from .common import Stop, digest, fold, load_matters
from .index import DocumentIndex
from .portfolio import portfolio_rows

RULE = 'proactive34-v1'
SLOT_SECONDS = 4 * 3600


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS proactive_cycles_v340(
      slot TEXT PRIMARY KEY, job_id INTEGER NOT NULL, attempts INTEGER NOT NULL,
      state TEXT NOT NULL, started TEXT NOT NULL, finished TEXT NOT NULL,
      data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS proactive_sources_v340(
      matter TEXT NOT NULL, kind TEXT NOT NULL, source_id TEXT NOT NULL,
      fingerprint TEXT NOT NULL, last_seen TEXT NOT NULL,
      PRIMARY KEY(matter,kind,source_id));
    CREATE TABLE IF NOT EXISTS proactive_notices_v340(
      id TEXT PRIMARY KEY, slot TEXT NOT NULL, matter TEXT NOT NULL,
      category TEXT NOT NULL, source_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
      title TEXT NOT NULL, detail TEXT NOT NULL, action TEXT NOT NULL,
      state TEXT NOT NULL, job_id INTEGER NOT NULL, created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS proactive_notices_slot_v340
      ON proactive_notices_v340(slot,created);
    CREATE TABLE IF NOT EXISTS morning_briefings_v340(
      day TEXT PRIMARY KEY, created TEXT NOT NULL, data TEXT NOT NULL);
    ''');desk.db.commit()


def _now(value=None):
    if value is None:return datetime.now(timezone.utc)
    if value.tzinfo is None:raise Stop('date_cycle_invalide')
    return value.astimezone(timezone.utc)


def _dt(value):
    try:
        result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (TypeError,ValueError):return None


def _job_state(desk,jid):
    row=desk.db.execute('SELECT status,created FROM jobs WHERE id=?',(jid,)).fetchone()
    return (row['status'],_dt(row['created'])) if row else ('missing',None)


def schedule(desk,when=None):
    """One 4 h slot and one Paris morning per day, with bounded retries."""
    ensure_schema(desk);now=_now(when)
    cfg=desk.c.get('preparation34',{})
    if not desk.settings('automation:preparation34_enabled',cfg.get('enabled',True)):
        return {'enabled':False,'cycle_job':0,'brief_job':0}
    slot=str(int(now.timestamp()//SLOT_SECONDS));cycle_job=0;brief_job=0
    current=desk.db.execute('SELECT * FROM proactive_cycles_v340 WHERE slot=?',(slot,)).fetchone()
    # Never stack a new four-hour sweep while an older sweep is queued/running.
    active=desk.db.execute("SELECT 1 FROM jobs WHERE kind IN ('proactive34_cycle','proactive34_now') AND status IN ('pending','running') LIMIT 1").fetchone()
    if not active and (not current or (current['state'] in ('error','scheduled','running') and current['attempts']<3 and
            _job_state(desk,current['job_id'])[0] in ('error','cancelled','missing') and
            (now-(_dt(current['started']) or now)).total_seconds()>=900)):
        cycle_job=desk.enqueue('proactive34_cycle',{'slot':slot},priority=45)
        desk.db.execute('INSERT OR REPLACE INTO proactive_cycles_v340 VALUES(?,?,?,?,?,?,?)',
            (slot,cycle_job,(current['attempts']+1 if current else 1),'scheduled',now.isoformat(),'',
             current['data'] if current else '{}'))
        desk.db.commit()
    local=now.astimezone(ZoneInfo(cfg.get('timezone','Europe/Paris')))
    start=local.replace(hour=7,minute=30,second=0,microsecond=0)
    if local>=start:
        day=local.date().isoformat()
        already=desk.db.execute('SELECT 1 FROM morning_briefings_v340 WHERE day=?',(day,)).fetchone()
        pending=desk.db.execute("SELECT id,status,created FROM jobs WHERE kind='proactive34_briefing' AND args=? ORDER BY id DESC LIMIT 1",
            (json.dumps({'day':day},sort_keys=True),)).fetchone()
        attempts=desk.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='proactive34_briefing' AND args=?",
            (json.dumps({'day':day},sort_keys=True),)).fetchone()[0]
        retry=not pending or (attempts<3 and pending['status'] in ('error','cancelled') and
            (now-(_dt(pending['created']) or now)).total_seconds()>=900)
        if not already and retry:
            brief_job=desk.enqueue('proactive34_briefing',{'day':day},priority=35)
    return {'enabled':True,'slot':slot,'cycle_job':cycle_job,'brief_job':brief_job}


def _notice(desk,slot,mid,category,sid,fp,title,detail,action,state='open',job_id=0):
    identity=digest('|'.join((RULE,mid,category,sid,fp)))
    if desk.db.execute('SELECT 1 FROM proactive_notices_v340 WHERE id=?',(identity,)).fetchone():return False
    desk.db.execute('INSERT INTO proactive_notices_v340 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (identity,slot,mid,category,sid,fp,title[:400],detail[:1500],action,
         state,job_id,desk.now()));desk.db.commit()
    return True


def _source(desk,mid,kind,sid,fp):
    old=desk.db.execute('SELECT fingerprint FROM proactive_sources_v340 WHERE matter=? AND kind=? AND source_id=?',
                        (mid,kind,sid)).fetchone()
    changed=old is None or old['fingerprint']!=fp
    desk.db.execute('INSERT OR REPLACE INTO proactive_sources_v340 VALUES(?,?,?,?,?)',
                    (mid,kind,sid,fp,desk.now()))
    return changed,old is None


def _document_type(path):
    name=fold(PurePosixPath(path).name.lower())
    terms={'conclusions':('conclusion',),'bcp':('bordereau','bcp'),
           'cgv':('conditions generales','cgv'),'contrat':('contrat','convention'),
           'assignation':('assignation',)}
    matches=[kind for kind,words in terms.items() if any(word in name for word in words)]
    return matches[0] if len(matches)==1 else ''


def _active(desk):
    active={row['matter'] for row in portfolio_rows(desk,['active'],limit=5000)}
    return {m['id']:m for m in load_matters(desk.c) if m['id'] in active}


def _indexed_sources(index,mid):
    for row in index.db.execute('SELECT path,etag,modified,text,error FROM docs WHERE matter=? ORDER BY path LIMIT 15000',(mid,)):
        path,etag,modified,content,error=row
        yield 'document',path,digest('|'.join((etag or '',modified or '',digest(content or ''),error or ''))),error or ''
    for row in index.db.execute("""SELECT source_id,path,etag,modified,text FROM knowledge_chunks
      WHERE matter=? AND kind='attachment' AND chunk_no=0 ORDER BY source_id LIMIT 15000""",(mid,)):
        sid,path,etag,modified,content=row
        yield 'attachment',sid,digest('|'.join((path or '',etag or '',modified or '',digest(content or '')))),''


def run_cycle(desk,args,now=None,calendar=None,writing_selector=None):
    ensure_schema(desk);now=_now(now);slot=str(args.get('slot') or 'manual-'+str(int(now.timestamp())))
    if not re.fullmatch(r'(?:\d{1,12}|manual-\d{1,15})',slot):raise Stop('creneau_proactif_invalide')
    first=desk.db.execute("SELECT 1 FROM proactive_cycles_v340 WHERE state IN ('done','partial') LIMIT 1").fetchone() is None
    row=desk.db.execute('SELECT data FROM proactive_cycles_v340 WHERE slot=?',(slot,)).fetchone()
    if row:
        old=desk.db.execute('SELECT state,data FROM proactive_cycles_v340 WHERE slot=?',(slot,)).fetchone()
        if old['state'] in ('done','partial'):return json.loads(old['data'])|{'idempotent':True}
    else:
        desk.db.execute('INSERT INTO proactive_cycles_v340 VALUES(?,?,?,?,?,?,?)',
            (slot,0,1,'running',now.isoformat(),'','{}'));desk.db.commit()
    desk.db.execute("UPDATE proactive_cycles_v340 SET state='running',started=? WHERE slot=?",(now.isoformat(),slot));desk.db.commit()
    matters=_active(desk);index=DocumentIndex(desk.c['state_dir']);cfg=desk.c.get('preparation34',{})
    counts={'documents':0,'attachments':0,'removed':0,'mails':0,'hearings':0,'projects_queued':0,'blocked':0}
    warnings=[];pending_scans=index.db.execute('SELECT COUNT(*) FROM inventory_scans').fetchone()[0]
    if pending_scans:warnings.append(str(pending_scans)+' inventaire(s) Nextcloud incomplet(s)')
    monitor=desk.db.execute('SELECT MAX(last_checked) FROM matter_watch').fetchone()[0]
    if not monitor or (now-(_dt(monitor) or now)).total_seconds()>SLOT_SECONDS+1800:
        warnings.append('Surveillance des dossiers non récente ; contrôler la collecte Nextcloud.')
    try:
        for mid in sorted(matters):
            observed=set()
            for kind,sid,fp,error in _indexed_sources(index,mid):
                observed.add((kind,sid))
                changed,new=_source(desk,mid,kind,sid,fp)
                if first or not changed:continue
                counts['documents' if kind=='document' else 'attachments']+=1
                path=sid if kind=='document' else next((r[0] for r in index.db.execute(
                    'SELECT path FROM knowledge_chunks WHERE matter=? AND source_id=? LIMIT 1',(mid,sid))),sid)
                action='Contrôler le document et ses effets sur le dossier.'
                state='blocked' if error else 'open';job_id=0
                if error:counts['blocked']+=1;action='Ouvrir la pièce non extraite avant toute rédaction.'
                elif kind=='document' and counts['projects_queued']<2 and desk.settings(
                        'automation:automatic_legal_projects_enabled',
                        desk.c.get('orchestrator',{}).get('automatic_legal_projects_enabled',True)):
                    dtype=_document_type(path)
                    if dtype:
                        try:
                            job_id=desk.enqueue('prepare_document_project',{'matter':mid,
                                'document_type':dtype,'source_path':path,'automatic':'yes',
                                'instruction':'Nouvelle version indexée du fichier '+PurePosixPath(path).name+
                                '. Préparer un projet interne sourcé et signaler les changements à valider.'},priority=30)
                            counts['projects_queued']+=1;action='Relire le projet interne et ses sources.'
                        except Stop as ex:warnings.append('Projet '+mid+' : '+str(ex))
                _notice(desk,slot,mid,kind,sid,fp,'Nouvelle source : '+PurePosixPath(path).name,
                        'Source '+('nouvelle' if new else 'modifiée')+' dans le dossier '+mid+'.'+
                        (' Extraction en erreur : '+error if error else ''),action,state,job_id)
            if not pending_scans:
                for kind,sid,fp in desk.db.execute("SELECT kind,source_id,fingerprint FROM proactive_sources_v340 WHERE matter=? AND kind IN ('document','attachment')",(mid,)).fetchall():
                    if (kind,sid) in observed:continue
                    desk.db.execute('DELETE FROM proactive_sources_v340 WHERE matter=? AND kind=? AND source_id=?',(mid,kind,sid))
                    if not first:
                        counts['removed']+=1
                        counts['blocked']+=1
                        _notice(desk,slot,mid,'source_absent',sid,fp,
                            'Source absente : '+PurePosixPath(sid).name,
                            'Source précédemment indexée, absente du dernier index complet du dossier '+mid+'.',
                            'Vérifier le dossier Nextcloud avant toute conclusion.',state='blocked')
            for mail in desk.db.execute("""SELECT mail_key,subject,source_status,state FROM work_items
                WHERE matter=? AND source_status NOT IN ('ignored','appending','append_uncertain')
                ORDER BY received DESC LIMIT 2000""",(mid,)):
                key=mail['mail_key'];changed,_=_source(desk,mid,'mail',key,key)
                if first or not changed or mail['state'] not in ('needs_action','needs_confirmation','draft_ready'):continue
                counts['mails']+=1;job_id=0
                if counts['mails']<=5 and desk.settings('automation:orchestrator_enabled',
                        desk.c.get('orchestrator',{}).get('enabled',False)):
                    try:job_id=desk.enqueue('orchestrate_mail',{'mail_key':key},priority=30)
                    except Stop as ex:warnings.append('Courriel '+key[:12]+' : '+str(ex))
                _notice(desk,slot,mid,'mail',key,key,'Courriel à examiner : '+mail['subject'],
                        'Nouveau courriel rattaché au dossier '+mid+'.',
                        'Relire le brouillon ou la proposition de réponse.',job_id=job_id)
    finally:index.db.close()
    desk.db.commit()
    fresh=False;events=[]
    from .workplan import _calendar_urls,calendar_events
    if _calendar_urls(desk):
        try:
            interval=calendar or calendar_events
            result=interval(desk,now.isoformat(),(now+timedelta(days=14)).isoformat(),refresh=True)
            events=result['events'];fresh=True
        except Exception as ex:warnings.append('Agenda non actualisé : '+
            (str(ex) if isinstance(ex,Stop) else 'service indisponible ; contrôler les journaux'))
    else:warnings.append('Agenda non configuré : aucune audience déclarée absente.')
    if fresh:
        selector=writing_selector
        if selector is None:
            from .hearing import identify_party_writings
            selector=identify_party_writings
        for event in events:
            title=str(event.get('title',''))
            folded=fold(title)
            if 'plaidoirie' not in folded or not re.search(r'\baudience\b|\btribunal\b|\bcour\b',folded):continue
            starts=_dt(event.get('starts'))
            if not starts or not now<=starts<now+timedelta(days=14):continue
            mid=str(event.get('matter',''));eid=str(event.get('id',''))
            candidates=event.get('matter_candidates',[])
            if mid not in matters or candidates!=[mid]:
                _notice(desk,slot,mid,'hearing_blocked',eid,digest(title+'|'+str(event.get('starts',''))),
                    'Audience à rattacher : '+title,'Dossier absent, non actif ou ambigu dans le calendrier.',
                    'Confirmer le dossier et les écritures dans Agenda.',state='blocked')
                counts['blocked']+=1;continue
            docs=[(r[0],r[1]) for r in desk.db.execute(
                "SELECT source_id,fingerprint FROM proactive_sources_v340 WHERE matter=? AND kind='document' ORDER BY source_id",
                (mid,)) if 'conclusion' in fold(r[0])]
            fp=digest(json.dumps([eid,title,event.get('starts'),event.get('etag'),docs,RULE],sort_keys=True))
            existing=desk.db.execute("SELECT 1 FROM proactive_notices_v340 WHERE matter=? AND category IN ('hearing','hearing_blocked') AND source_id=? AND fingerprint=?",(mid,eid,fp)).fetchone()
            if existing:continue
            if counts['hearings']>=3:warnings.append('Plus de trois audiences J−14 : examen reporté au prochain cycle.');break
            try:
                chosen=selector(desk,{'matter':mid})
                arguments={'matter':mid,'instruction':'Audience de plaidoirie « '+title[:250]+
                    ' » prévue le '+str(event.get('starts',''))+'. Préparer une note interne sourcée, '
                    'les plans de plaidoirie et les pièces à vérifier. Aucun dépôt.',
                    'our_source_path':chosen['our_latest']['path'],
                    'opponent_source_path':chosen['opponent_latest']['path'],
                    'automatic':'yes'}
                jid=desk.enqueue('prepare_hearing',arguments,priority=25)
                counts['hearings']+=1
                _notice(desk,slot,mid,'hearing',eid,fp,'Audience J−14 : '+title,
                        'Écritures des deux parties identifiées ; note interne mise en préparation.',
                        'Relire la note, le dispositif et les pièces avant confirmation.',job_id=jid)
            except Stop as ex:
                counts['blocked']+=1
                _notice(desk,slot,mid,'hearing_blocked',eid,fp,'Audience J−14 à contrôler : '+title,
                        'Préparation bloquée : '+str(ex),'Choisir les dernières conclusions des parties.',state='blocked')
    summary={'slot':slot,'at':now.isoformat(),'state':'done' if not warnings else 'partial',
             'baseline':bool(first),'active_matters':len(matters),'source_counts':counts,
             'calendar_refreshed':fresh,'calendar_events':len(events),'inventory_scans_pending':pending_scans,
             'warnings':warnings[:20],'no_external_creations':True}
    desk.db.execute('UPDATE proactive_cycles_v340 SET state=?,finished=?,data=? WHERE slot=?',
                    (summary['state'],desk.now(),json.dumps(summary,ensure_ascii=False),slot));desk.db.commit()
    desk.audit('proactive_cycle_finished',{'slot':slot,'counts':counts,'warnings':len(warnings)})
    return summary


def briefing(desk,args,now=None):
    ensure_schema(desk);local=_now(now).astimezone(ZoneInfo(desk.c.get('preparation34',{}).get('timezone','Europe/Paris')))
    day=str(args.get('day') or local.date().isoformat())
    if day!=local.date().isoformat():raise Stop('jour_briefing_invalide')
    old=desk.db.execute('SELECT data FROM morning_briefings_v340 WHERE day=?',(day,)).fetchone()
    if old:return json.loads(old[0])|{'idempotent':True}
    cycle=desk.db.execute("SELECT * FROM proactive_cycles_v340 WHERE state IN ('done','partial') ORDER BY finished DESC LIMIT 1").fetchone()
    last=_dt(cycle['finished']) if cycle else None
    calendar_stamp=desk.db.execute('SELECT MAX(fetched) FROM calendar_cache').fetchone()[0]
    cal_time=_dt(calendar_stamp)
    warnings=[]
    if not last or (_now(now)-last).total_seconds()>SLOT_SECONDS+1800:
        warnings.append('Analyse différentielle 4 h absente ou en retard.')
    cycle_data=json.loads(cycle['data']) if cycle else {}
    calendar_fresh=bool(cycle_data.get('calendar_refreshed')) and bool(last) and (
        (_now(now)-last).total_seconds()<=SLOT_SECONDS+1800)
    if not calendar_fresh:
        warnings.append('Agenda à 14 jours non actualisé récemment.')
    if cycle and cycle['state']=='partial':warnings+=json.loads(cycle['data']).get('warnings',[])[:5]
    notices=[dict(r) for r in desk.db.execute("SELECT * FROM proactive_notices_v340 WHERE created>=? ORDER BY created DESC LIMIT 40",
        ((_now(now)-timedelta(days=1)).isoformat(),))]
    from .workstation32 import briefing as workstation_briefing
    base=workstation_briefing(desk)
    data={'day':day,'created':desk.now(),'cycle_slot':cycle['slot'] if cycle else '',
        'cycle_finished':cycle['finished'] if cycle else '',
        'calendar_last_fetched':calendar_stamp,'mail_last_seen':base['mail_last_seen'],
        'active_matters':json.loads(cycle['data']).get('active_matters',0) if cycle else None,
        'needs_action':base['pending_mail'],'ready_drafts':base['ready_drafts'],
        'agenda_next_14_days':base['events'],'recent_notices':notices,
        'coverage_warnings':list(dict.fromkeys(warnings)),
        'published_at_local':local.isoformat(),'mode':'consultation_interne'}
    desk.db.execute('INSERT INTO morning_briefings_v340 VALUES(?,?,?)',
                    (day,desk.now(),json.dumps(data,ensure_ascii=False)));desk.db.commit()
    desk.audit('morning_briefing_published',{'day':day,'notices':len(notices),'warnings':len(warnings)})
    return data


def latest(desk):
    ensure_schema(desk)
    cycle=desk.db.execute('SELECT * FROM proactive_cycles_v340 ORDER BY started DESC LIMIT 1').fetchone()
    brief=desk.db.execute('SELECT data FROM morning_briefings_v340 ORDER BY day DESC LIMIT 1').fetchone()
    notices=[dict(r) for r in desk.db.execute('SELECT * FROM proactive_notices_v340 ORDER BY created DESC LIMIT 80')]
    return {'cycle':dict(cycle) if cycle else None,'briefing':json.loads(brief[0]) if brief else None,
            'notices':notices,'schedule':'4 h UTC et 07:30 Europe/Paris',
            'enabled':desk.settings('automation:preparation34_enabled',
                                     desk.c.get('preparation34',{}).get('enabled',True))}


def perform(desk,kind,args):
    if kind in ('proactive34_cycle','proactive34_now'):return run_cycle(desk,args)
    if kind=='proactive34_briefing':return briefing(desk,args)
    raise Stop('action_inconnue')

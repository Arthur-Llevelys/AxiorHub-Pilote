"""IMAP IDLE wakes the persistent queue; periodic checks survive missed events.

The watcher never calls a model, sends mail, or writes to remote services.
It has a separate lock/process, so an expensive model cannot stop detection.
"""
from datetime import datetime, timezone, timedelta
from .portable import fcntl   # 5.6.25 : verrous portables Linux / Windows
import hashlib
import imaplib
import json
from pathlib import Path
import select
import sqlite3
import threading
import time

from .common import Stop, load_config, load_matters, indexable_matters
from .dav import DAV
from .mailbox import Mailbox
from .live430 import emit, heartbeat, progress


def idle_wait(box, seconds=30):
    """Python 3.12-compatible bounded IDLE on a dedicated authenticated connection.

    Public send/readline/socket API; no global imaplib command monkey-patch.
    Always drain DONE before reusing/closing. On I/O ambiguity discard connection.
    """
    conn=box.conn
    if b'IDLE' not in {x.upper() if isinstance(x,bytes) else x.upper().encode() for x in conn.capabilities}:return None
    box.select(box.cfg['inbox']);tag=b'AXIORHUBIDLE'
    conn.send(tag+b' IDLE\r\n')
    line=conn.readline()
    if not line.startswith(b'+'):raise Stop('imap_idle_refuse')
    changed=False
    try:
        sock=conn.socket()
        pending=getattr(sock,'pending',lambda:0)()
        if pending or select.select([sock],[],[],min(max(seconds,1),60))[0]:
            line=conn.readline()
            if line.startswith(tag+b' '):raise Stop('imap_idle_interrompu')
            changed=any(x in line.upper() for x in (b' EXISTS',b' EXPUNGE',b' FETCH'))
    finally:
        conn.send(b'DONE\r\n')
        # A broken server cannot keep us forever: connection timeout is 45 s,
        # and only a bounded number of unsolicited responses is accepted.
        for _ in range(1000):
            line=conn.readline()
            if not line:raise Stop('imap_idle_deconnecte')
            if line.startswith(tag+b' '):
                if not line.startswith(tag+b' OK'):raise Stop('imap_idle_fin_refusee')
                break
            changed=changed or b' EXISTS' in line.upper()
        else:raise Stop('imap_idle_flux_excessif')
    return changed


def _legacy_unreadable(row):
    """5.6.36 : une suspension due à l'ancien motif « dossiers illisibles » est levée d'office (ce motif ne suspend plus rien)."""
    try:
        found=json.loads(row['result'] or '{}').get('erreur','') if 'result' in row.keys() else ''
    except (ValueError,TypeError,AttributeError):
        return False
    return found=='dossiers_illisibles'


def schedule(desk,stamp=None):
    """5.6.43 (§ 16.1) : origine « automatic » pour tout ce que ce planificateur met en file."""
    from .arret5643 import automatic
    with automatic(desk):
        return _schedule_5642(desk,stamp)


def _schedule_5642(desk,stamp=None):
    stamp=time.time() if stamp is None else stamp
    if desk.settings('missions567:pause',False) or not desk.settings('live430:enabled',True):
        heartbeat(desk,'surveillance','paused','Surveillance en pause');return []
    jobs=[]
    for kind,interval in [('live_mail430',300),('live_calendar430',180),('live_documents430',300)]:
        last=desk.settings('live430:last:'+kind,0)
        if stamp-float(last)>=interval:
            latest=desk.db.execute('SELECT id,status,attempts,result FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1',(kind,)).fetchone()
            if (kind in ('live_calendar430','live_documents430') and latest and latest['status']=='error' and latest['attempts']>=3
                    and not _legacy_unreadable(latest)):
                emit(desk,'blocked','Contrôle en lecture suspendu après trois tentatives ; corriger le motif puis utiliser Relancer.',latest['id'],dedupe='retry-exhausted-'+str(latest['id']))
                continue
            if latest and latest['status'] in ('pending','running','cancel_requested'):
                jobs.append(latest['id']);desk.setting('live430:last:'+kind,stamp);continue
            jobs.append(desk.enqueue(kind,priority=5 if kind=='live_mail430' else 55))
            desk.setting('live430:last:'+kind,stamp)
    if stamp-float(desk.settings('live431:last_daily',0))>=86400:
        # Reuse progressive dossier checks, not a second analysis engine.
        desk.enqueue('monitor_all',priority=65)
        desk.enqueue('index_all',priority=70)
        desk.setting('live431:last_daily',stamp)
        emit(desk,'detected','Contrôle quotidien approfondi des dossiers mis en file.')
    from .activity431 import maintenance
    maintenance(desk,stamp)
    from .proactive568 import schedule as schedule568
    jobs.extend(schedule568(desk,stamp))
    try:   # 5.6.32 : règles « Déplacer » de la messagerie, toutes les 5 minutes si une règle est active
        from .courrier5632 import schedule as schedule_rules
        jobs.extend(schedule_rules(desk,stamp))
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Règles de messagerie : planification impossible ('+str(ex)[:120]+').',dedupe='courrier5632-schedule')
    try:   # 5.6.52 (I01 à I15) : suivi borné des opérations transmises (signatures, recommandés, courriers, factures), toutes les 15 minutes
        if stamp-float(desk.settings('connexions5652:last_poll',0) or 0)>=900:
            desk.setting('connexions5652:last_poll',stamp)
            from .connexions5652 import poll as poll5652
            poll5652(desk)
            from .facturation5653 import sync_deliveries   # 5.6.53 : statuts de transmission projetés sur les factures (un dépôt n'est pas un règlement)
            sync_deliveries(desk)
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Connexions : suivi des opérations impossible ('+str(ex)[:120]+').',dedupe='connexions5652-poll')
    try:   # 5.6.51 (A07) : préparations d'audience ou d'échéance confirmée, au plus une vérification par heure
        from .missions5651 import schedule as schedule5651
        jobs.extend(schedule5651(desk,stamp))
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Préparation proactive : planification impossible ('+str(ex)[:120]+').',dedupe='preparation5651-schedule')
    try:   # 5.6.57 : contacts depuis la boîte (si activé), diligences proposées (une fois par jour), points d'avancement client
        from .crm5657 import schedule as schedule5657
        jobs.extend(schedule5657(desk,stamp))
        from . import parcours5657
        parcours5657.estimate_daily(desk,stamp)
        if stamp-float(desk.settings('suivi5657:last',0) or 0)>=21600:
            desk.setting('suivi5657:last',stamp)
            parcours5657.propose_updates(desk)
    except Exception as ex:   # noqa: BLE001 — ces propositions ne doivent pas interrompre la surveillance
        emit(desk,'blocked','Parcours 5.6.57 : planification impossible ('+str(ex)[:120]+').',dedupe='parcours5657-schedule')
    try:   # 5.6.32 (R12) : veille juridique planifiée, au plus un travail par utilisateur et par créneau
        from .veille5632 import schedule as schedule5632
        jobs.extend(schedule5632(desk,stamp))
    except Exception as ex:   # noqa: BLE001 — la veille ne doit pas interrompre la surveillance
        emit(desk,'blocked','Veille juridique : planification impossible ('+str(ex)[:120]+').',dedupe='veille5632-schedule')
    try:   # 5.6.36 : agenda Microsoft 365 relié — actualisation toutes les 15 minutes
        from .microsoft5636 import schedule as schedule_microsoft
        jobs.extend(schedule_microsoft(desk,stamp))
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Agenda Microsoft 365 : planification impossible ('+str(ex)[:120]+').',dedupe='microsoft5636-schedule')
    try:   # 5.6.36 : relances d'impayés préparées chaque matin (projets à valider, rien n'est envoyé)
        from .relances5636 import schedule as schedule_relances
        jobs.extend(schedule_relances(desk,stamp))
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Relances d’impayés : planification impossible ('+str(ex)[:120]+').',dedupe='relances5636-schedule')
    try:   # 5.6.34 (B07) : rappels d'alertes échus remis en circulation ici, jamais pendant l'affichage d'une page
        from .proactive import release_snoozed
        release_snoozed(desk)
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Alertes : remise en circulation impossible ('+str(ex)[:120]+').',dedupe='alertes5634-release')
    try:   # 5.6.34 : agenda local — rappels, resynchronisation facultative, sauvegarde quotidienne
        from .agenda5634 import tick as agenda_tick
        agenda_tick(desk,stamp)
    except Exception as ex:   # noqa: BLE001
        emit(desk,'blocked','Agenda local : rappels ou sauvegarde impossibles ('+str(ex)[:120]+').',dedupe='agenda5634-tick')
    heartbeat(desk,'surveillance','active','Contrôle périodique et réveil IMAP',stamp+max(0,300-(stamp-float(desk.settings('live430:last:live_mail430',0)))))
    return jobs


def observe(desk,source,key,value):
    fp=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    old=desk.db.execute('SELECT fingerprint FROM live_observations_v430 WHERE source=? AND object_key=?',(source,key)).fetchone()
    dirty=desk.db.execute('SELECT 1 FROM live_dirty_v430 WHERE source=? AND object_key=?',(source,key)).fetchone()
    state='new' if not old else ('changed' if old[0]!=fp or dirty else 'unchanged')
    desk.db.execute('INSERT OR REPLACE INTO live_observations_v430 VALUES(?,?,?,?)',(source,key,fp,desk.now()))
    if state!='unchanged':
        desk.db.execute('INSERT OR IGNORE INTO live_dirty_v430 VALUES(?,?)',(source,key))
    desk.db.commit()
    return state


def acknowledged(desk,source,key):
    desk.db.execute('DELETE FROM live_dirty_v430 WHERE source=? AND object_key=?',(source,key));desk.db.commit()


def calendar_check(desk,dav=None):
    cc=desk.c.get('calendar',{});urls=cc.get('urls',[])
    if not urls:
        heartbeat(desk,'agenda','unconfigured','Aucun calendrier configuré');return {'abstention':'Agenda non configuré.'}
    client=dav or DAV(desk.c['nextcloud']);now=datetime.now(timezone.utc)
    progress(desk,'Consultation de l’agenda · échéances et audiences à venir')
    parameters=(urls,now-timedelta(days=1),now+timedelta(days=int(cc.get('horizon_days',90))),cc.get('timezone','Europe/Paris'))
    events=client.events(*parameters,include_cancelled=True) if isinstance(client,DAV) else client.events(*parameters)
    from .proactive568 import calendar_events
    calendar_events(desk,events)
    changed=0;dirty=[]
    for event in events:
        # UID + occurrence, not moving query boundaries, avoids recurrent noise.
        key=event.get('source_url','')+'|'+event.get('uid','')+'|'+event.get('start','')
        state=observe(desk,'calendar',key,event)
        if state!='unchanged':
            dirty.append(key)
            changed+=1;emit(desk,'calendar','Événement agenda '+('détecté' if state=='new' else 'modifié')+' : '+str(event.get('summary') or event.get('title') or 'Événement')[:250],getattr(desk,'active_job_id',None))
    if changed:
        desk.enqueue('monitor_all',priority=55)
        if desk.settings('automation:production_enabled',desk.c.get('production',{}).get('enabled',True)):
            desk.enqueue('production_cycle391',{'limit':20},priority=35)
        for key in dirty:acknowledged(desk,'calendar',key)
    try:
        if time.time()-float(desk.settings('deadlines450:last_run',0))>=3600:
            desk.setting('deadlines450:last_run',time.time())
            from .echeances450 import daily_check
            daily_check(desk,client)
    except Exception as ex:
        emit(desk,'calendar','Contrôle des échéances impossible : '+str(ex)[:120],getattr(desk,'active_job_id',None))
    heartbeat(desk,'agenda','active',str(len(events))+' événements contrôlés',time.time()+180)
    return {'events_checked':len(events),'changed':changed,'remote_write':False}


RECENT_DAYS=30


def recent_change(items,days=RECENT_DAYS):
    """5.6.5 : au moins un fichier modifié ces derniers jours (date Nextcloud) ; sinon, le changement ne justifie aucune analyse.
    Une date absente ou illisible compte comme récente : dans le doute, le dossier est analysé."""
    from email.utils import parsedate_to_datetime
    limit=datetime.now(timezone.utc)-timedelta(days=days)
    for item in (items or {}).values():
        try:
            when=parsedate_to_datetime(str(item.get('modified') or ''))
        except (TypeError,ValueError,IndexError):
            return True
        if when is None:
            return True
        if when and (when if when.tzinfo else when.replace(tzinfo=timezone.utc))>=limit:
            return True
    return False


UNREADABLE='documents5636:illisibles'   # 5.6.36 : {dossier: {libellé, chemin, motif, date, essais}}
IGNORED='documents5636:ignores'         # 5.6.36 : dossiers que l'avocat a retirés de la surveillance


def unreadable(desk):
    value=desk.settings(UNREADABLE,{})
    return value if isinstance(value,dict) else {}


def ignored(desk):
    value=desk.settings(IGNORED,[])
    return [str(x) for x in value] if isinstance(value,list) else []


def retry_folder(desk,mid,ignore=False,restore=False):
    """5.6.36 : « Réessayer » (le dossier sera relu au prochain lot), « Ne plus surveiller » ou « Surveiller de nouveau »."""
    mid=str(mid or '')
    if not mid or len(mid)>120:raise Stop('dossier_invalide')
    gone=unreadable(desk);gone.pop(mid,None);desk.setting(UNREADABLE,gone)
    skip=[x for x in ignored(desk) if x!=mid]
    if ignore:skip.append(mid)
    desk.setting(IGNORED,skip)
    desk.audit('documents5636_dossier',{'matter':mid,'ignore':bool(ignore),'restore':bool(restore)})
    return {'message':'Dossier retiré de la surveillance.' if ignore else ('Dossier de nouveau surveillé.' if restore else
            'Dossier relu au prochain contrôle (dans 5 minutes au plus).')}


def documents_check(desk,dav=None):
    client=dav or DAV(desk.c['nextcloud']);skip=set(ignored(desk))
    matters=[m for m in indexable_matters(desk.c) if m['id'] not in skip]
    if not matters:return {'abstention':'Aucun dossier indexable.'}
    cursor=int(desk.settings('live430:document_cursor',0))%len(matters);changed=0;partial=0
    selected=matters[cursor:cursor+3]
    progress(desk,'Surveillance Nextcloud · '+str(len(selected))+' dossiers dans ce lot')
    failed=0
    for matter in selected:
        mid=matter['id'];setting='live430:scan:'+mid
        try:
            scan,complete=client.inventory_step(matter['path'],desk.settings(setting,None))
        except Stop as ex:
            # 5.6.3 : un dossier illisible est signalé et passé ; il ne suspend plus la surveillance de tout le cabinet.
            # 5.6.36 : il est nommé dans « Pourquoi rien n'est produit ? » avec son motif, « Réessayer » et « Ne plus surveiller ».
            failed+=1;desk.setting(setting,None)
            gone=unreadable(desk);prev=gone.get(mid) or {}
            from .improvements36 import matter_option
            gone[mid]={'label':matter_option(matter)[:160],'path':str(matter.get('path',''))[-160:],'code':str(ex)[:80],
                       'at':datetime.now(timezone.utc).isoformat(),'count':int(prev.get('count',0))+1}
            desk.setting(UNREADABLE,dict(sorted(gone.items())[:300]))
            emit(desk,'documents','Dossier non lu ('+str(ex)[:60]+') : '+str(matter.get('path',''))[-120:],getattr(desk,'active_job_id',None),mid,
                 dedupe='illisible-'+mid+'-'+time.strftime('%Y%m%d'))
            continue
        if mid in unreadable(desk):
            gone=unreadable(desk);gone.pop(mid,None);desk.setting(UNREADABLE,gone)
        for href in getattr(client,'refused',[])[:5]:
            emit(desk,'documents','Fichier au nom ambigu ignoré : '+href,getattr(desk,'active_job_id',None),mid,dedupe='refused-'+href)
        client.refused=[]
        if not complete:
            desk.setting(setting,scan);partial+=1;continue
        desk.setting(setting,None)
        # Inventory metadata (not contents) is sufficient to detect a change.
        items=scan.get('files',{})
        rows=sorted([(path,x.get('etag',''),x.get('modified','')) for path,x in items.items()])
        state=observe(desk,'documents',mid,rows)
        from .automation568 import observe as observe_agents
        managed=observe_agents(desk,matter,items)
        legacy_items={path:meta for path,meta in items.items() if path not in managed}
        try:
            from .notices440 import observe_inventory
            observe_inventory(desk,mid,legacy_items)
        except Exception as ex:
            emit(desk,'documents','Analyse des avis impossible : '+str(ex)[:120],getattr(desk,'active_job_id',None),mid)
        try:
            from .echeances450 import observe_inventory as observe_deadlines
            observe_deadlines(desk,mid,legacy_items)
        except Exception as ex:
            emit(desk,'documents','Analyse des échéances impossible : '+str(ex)[:120],getattr(desk,'active_job_id',None),mid)
        if state!='unchanged':
            from .improvements36 import matter_option
            if not recent_change(items):
                # 5.6.5 : dossier ancien (aucun fichier modifié depuis 30 jours) lu pour la première fois ou réorganisé : simple point de
                # départ. Avant, chaque dossier lu pour la première fois (y compris ceux de 2019) était indexé, surveillé et analysé.
                acknowledged(desk,'documents',mid)
                continue
            changed+=1;emit(desk,'documents','Inventaire initial ou documents modifiés dans '+matter_option(matter),getattr(desk,'active_job_id',None),mid)
            from .proactive568 import document_events
            document_events(desk,matter,legacy_items)
            try:
                desk.enqueue('index',{'matter':mid},priority=50)
                desk.enqueue('monitor_matter',{'matter':mid},priority=55)
                from . import economie569
                if economie569.quota_ok(desk,'extract_facts460'):desk.enqueue('extract_facts460',{'matter':mid},priority=58)
                else:
                    economie569.defer(desk,'extract_facts460',{'matter':mid})   # 5.6.11 : différée, reprise le lendemain
                    emit(desk,'documents','Recherche de faits différée au lendemain (quota journalier du régime économe) : '+matter_option(matter),getattr(desk,'active_job_id',None),mid,dedupe='econome-facts-'+mid+'-'+time.strftime('%Y%m%d'))
                acknowledged(desk,'documents',mid)
            except Stop as ex:
                # file automatique pleine : le dossier reste « à traiter » et sera repris au prochain passage, sans échec de la surveillance
                emit(desk,'documents','Analyse reportée ('+str(ex)+') : '+matter_option(matter),getattr(desk,'active_job_id',None),mid,
                     dedupe='report-'+mid+'-'+time.strftime('%Y%m%d%H'))
    next_cursor=(cursor+len(selected))%len(matters);desk.setting('live430:document_cursor',next_cursor)
    # Finish a whole sweep in bounded lots, yielding to replies between each lot.
    more=next_cursor!=0 or bool(partial)
    heartbeat(desk,'documents','active','Lot '+str(cursor+1)+'–'+str(cursor+len(selected))+' / '+str(len(matters))+' dossiers · '+str(partial)+' inventaires partiels',time.time()+300)
    # 5.6.36 : plus de Stop('dossiers_illisibles') : trois lots de dossiers illisibles suspendaient toute la surveillance des documents.
    return {'changed':changed,'partial':partial,'failed':failed,'continue_scan':more,'remote_write':False}


def perform(desk,kind,args):
    if kind=='live_mail430':
        from .engine import Engine
        engine=Engine(desk.c);engine.activity=lambda message,matter='',key='':progress(desk,message,matter,key)
        result=engine.run()
        if result.get('busy'):raise Stop('traitement_deja_en_cours')
        heartbeat(desk,'courriels','active',str(result.get('examined',0))+' courriels examinés',time.time()+300)
        return result
    if kind=='live_calendar430':return calendar_check(desk)
    if kind=='live_documents430':return documents_check(desk)
    raise Stop('action_inconnue')


def _idle_loop(config,stop):
    from .desk import Desk
    backoff=5
    while not stop.is_set():
        desk=None;box=None
        try:
            cfg=load_config(config) if isinstance(config,(str,Path)) else config
            desk=Desk(cfg)
            if desk.settings('missions567:pause',False) or not desk.settings('live430:enabled',True):stop.wait(15);continue
            box=Mailbox(cfg['mail'])
            # Reuse the dedicated connection. Refresh private configuration at
            # most five minutes later; check the pause flag every IDLE cycle.
            for _ in range(10):
                if stop.is_set() or desk.settings('missions567:pause',False) or not desk.settings('live430:enabled',True):break
                heartbeat(desk,'imap_idle','active','Connexion IMAP dédiée en lecture')
                changed=idle_wait(box)
                if changed is None:
                    heartbeat(desk,'imap_idle','polling','IDLE non disponible : contrôle de secours toutes les 5 min')
                    stop.wait(30);break
                if changed:
                    desk.enqueue('live_mail430',priority=5)
                    emit(desk,'detected','Changement de la boîte détecté par IMAP IDLE ; examen mis en file.')
            backoff=5
        except (Stop,OSError,imaplib.IMAP4.error,sqlite3.Error,ValueError,KeyError):
            if desk:
                try:heartbeat(desk,'imap_idle','error','Connexion interrompue ; reconnexion automatique. Le contrôle périodique reste actif.')
                except sqlite3.Error:pass
            stop.wait(backoff);backoff=min(backoff*2,60)
        finally:
            if box:box.close()
            if desk:desk.db.close()


def watcher(c):
    from .desk import Desk
    stop=threading.Event()
    with open(Path(c['state_dir'])/'watch430.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        thread=threading.Thread(target=_idle_loop,args=(c.get('_path') or c,stop),daemon=True);thread.start()
        try:
            while not stop.is_set():
                cfg=load_config(c['_path']) if c.get('_path') else c;desk=Desk(cfg)
                try:schedule(desk)
                except (Stop,sqlite3.Error):pass
                finally:desk.db.close()
                stop.wait(15)
        finally:stop.set();thread.join(timeout=5)

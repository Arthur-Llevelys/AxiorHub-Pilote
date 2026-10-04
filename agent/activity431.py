"""Safe activity projections and bounded recovery of read-only work."""
from datetime import datetime, timezone, timedelta
import json
import re
import time


def mail_outcome(desk, key, report):
    jid=getattr(desk,'active_job_id',None)
    if not jid or not re.fullmatch('[a-f0-9]{64}',key):return
    from .live430 import emit
    from .web import REASONS
    status=report.get('status','review');verification=report.get('draft_verified') or {}
    verified=status=='drafted' and bool(verification.get('uid') and verification.get('uidvalidity'))
    category='produced' if verified else ('abstained' if status=='ignored' else 'blocked')
    message=('Brouillon relu dans '+str(verification.get('folder','Drafts'))+' · UID '+str(verification.get('uid',''))+'. Aucun envoi.' if verified else
      'Aucune réponse déposée : '+REASONS.get(report.get('reason',''),'un contrôle du cabinet est nécessaire.') )
    sources=report.get('sources') or []
    counts={kind:sum(1 for x in sources if x.get('kind')==kind) for kind in ('document','email_history','calendar_event','attachment')}
    # This allowlist intentionally excludes bodies, addresses and prompts.
    details={'source_counts':counts,'source_count':len(sources),
      'sources':[{'id':str(x.get('id',''))[:120],'kind':str(x.get('kind',''))[:40],
        'path':str(x.get('path') or x.get('filename') or '')[:1500],'page':x.get('page')}
        for x in sources[:40]],
      'models':report.get('models_used',[]),'verification':{k:verification.get(k) for k in ('folder','uid','uidvalidity','verified_at')},
      'external_cost_usd':report.get('external_cost_usd') if report.get('external_cost_known') else None,
      'calendar_checked':bool(report.get('calendar_checked')),
      'documents_checked':bool(report.get('document_coverage') and not report['document_coverage'].get('withheld_for_unconfirmed_role')),
      'expected':'Brouillon de réponse','review_required':True}
    try:
        from . import trace480
        rules=report.get('regles480') or {}
        why=('Réponse à un courriel entrant ; %d source(s) lue(s)' % len(sources) +
             ('; règles appliquées : %s' % ', '.join(map(str,rules.get('appliquees',[]))) if rules.get('appliquees') else '') +
             ('; profil de ton : %s' % rules['profil_de_ton'] if rules.get('profil_de_ton') else '') +
             ('' if verified else '; motif : '+str(report.get('reason',''))))
        trace480.event(desk,str(report.get('matter') or ''),'produit' if verified else ('abstention' if status=='ignored' else 'bloque'),
                       'Brouillon de réponse déposé dans les Brouillons (aucun envoi)' if verified else 'Aucun brouillon déposé',
                       why,{'mail_key':key,'models':[m.get('model') for m in report.get('models_used',[])],
                            'ecarts_regles':(rules.get('ecarts') or [])[:5]})
    except Exception:
        pass
    desk.db.execute('INSERT OR REPLACE INTO live_outputs_v431 VALUES(?,?,?,?,?,?,?)',
      (jid,key,str(report.get('matter') or ''),category,message,json.dumps(details,ensure_ascii=False),desk.now()))
    desk.db.commit()
    if not verified:emit(desk,category,message,jid,str(report.get('matter') or ''),key)


def maintenance(desk, stamp=None):
    """Never replay a producer whose remote write might already have happened."""
    stamp=time.time() if stamp is None else stamp
    from .live430 import emit
    safe={'live_calendar430','live_documents430'}
    rows=desk.db.execute("SELECT id,kind,status,attempts,started,progress,worker FROM jobs WHERE status IN ('error','running','cancel_requested')").fetchall()
    for row in rows:
        old=desk.db.execute('SELECT * FROM live_maintenance_v431 WHERE job_id=?',(row['id'],)).fetchone()
        if row['status']=='error' and row['kind'] in safe and row['attempts']<3:
            if old and stamp-old['last_retry']<60:continue
            # Keep the same durable identity; no copy-chain bypassing the cap.
            changed=desk.db.execute("UPDATE jobs SET status='pending',finished=NULL,progress='Reprise automatique en lecture seule' WHERE id=? AND status='error'",(row['id'],)).rowcount
            if changed:
                desk.db.execute('INSERT OR REPLACE INTO live_maintenance_v431 VALUES(?,?,?)',(row['id'],stamp,''));desk.db.commit()
                emit(desk,'queued','Reprise automatique limitée d’un contrôle en lecture seule.',row['id'])
        elif row['status'] in ('running','cancel_requested'):
            try:age=stamp-datetime.fromisoformat(row['started']).timestamp()
            except (ValueError,TypeError):age=0
            pulse=desk.db.execute('SELECT heartbeat FROM live_services_v430 WHERE name=?',('worker-'+str(row['worker']),)).fetchone()
            reason='worker_sans_signal' if not pulse or stamp-pulse[0]>90 else ('traitement_trop_long' if age>7200 else '')
            if reason and (not old or old['incident']!=reason):
                desk.db.execute('INSERT OR REPLACE INTO live_maintenance_v431 VALUES(?,?,?)',(row['id'],old['last_retry'] if old else 0,reason));desk.db.commit()
                emit(desk,'blocked','Traitement à contrôler : worker non confirmé ou durée excessive. Aucun rejeu automatique de la production.',row['id'])
            elif not reason and old and old['incident']:
                desk.db.execute("UPDATE live_maintenance_v431 SET incident='' WHERE job_id=?",(row['id'],));desk.db.commit()
    try:
        from . import trace480
        trace480.purge(desk)
    except Exception:
        pass
    try:
        from . import mobile490, search490
        mobile490.dispatch(desk, now=stamp)
        search490.schedule(desk, now=stamp)
    except Exception:
        pass
    for step in ('conflicts500.scan_background','time500.check_alerts','limitation500.daily_check','meeting500.prepare_ahead','report500.snapshot_previous','reminders520.run','routines520.tick','deck530.tick','style550.tick'):
        # Fonctions métier 5.0 : calculs locaux, chacun isolé pour qu'une panne n'empêche pas les autres.
        try:
            key='metier500:last:'+step
            if stamp-float(desk.settings(key,0) or 0)<600:continue
            desk.setting(key,stamp)
            module,func=step.split('.')
            import importlib
            getattr(importlib.import_module('.'+module,__package__),func)(desk)
        except Exception as ex:
            try:desk.audit('metier_500_maintenance_echec',{'step':step,'reason':str(ex)[:100]})
            except Exception:pass
    cutoff=datetime.fromtimestamp(stamp,timezone.utc)-timedelta(days=30)
    # Preserve audit and job rows; only move business events out of the live feed.
    desk.db.execute('INSERT OR IGNORE INTO live_event_archive_v431 SELECT * FROM live_events_v430 WHERE at<?',(cutoff.isoformat(),))
    desk.db.execute('DELETE FROM live_events_v430 WHERE at<?',(cutoff.isoformat(),));desk.db.commit()

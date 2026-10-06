"""Bounded health checks and dashboard data."""
from datetime import datetime,timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import Stop,load_matters
from .dav import DAV
from .mailbox import Mailbox
from .model import Model,provider_diagnostic
from .rag import Embedder
from .state import State


def health(desk):
    result={'imap':'error','nextcloud':'error','ollama':'error','embeddings':'disabled','at':desk.now()}
    try:
        box=Mailbox(desk.c['mail'])
        try:box.select(desk.c['mail']['inbox']);result['imap']='ok'
        finally:box.close()
    except Stop as ex:result['imap']=str(ex)
    try:DAV(desk.c['nextcloud']).list_folder(desk.c['nextcloud']['roots'][0]);result['nextcloud']='ok'
    except Stop as ex:result['nextcloud']=str(ex)
    from .ai_gateway import provider_registry
    result['ai_providers']={}
    for provider_id,cfg in provider_registry(desk.c).items():
        diagnostic=provider_diagnostic(cfg)
        result['ai_providers'][provider_id]=diagnostic
        if provider_id=='ollama':result['ollama']='ok' if diagnostic['status']=='ok' else diagnostic.get('error','error')
    from .extensions364 import list_items
    result['extensions']={item['id']:{'kind':item['kind'],'status':item.get('status',''),
      'enabled':item.get('enabled',False),'last_test':item.get('last_test',{})} for item in list_items(desk)}
    if desk.c.get('rag',{}).get('enabled'):
        try:Embedder(desk.c['ollama'],desk.c['rag']);result['embeddings']='ok'
        except Stop as ex:result['embeddings']=str(ex)
    desk.setting('health',result);return result


def exceptions(desk):
    c=desk.c;state=State(c['state_dir']);counts=state.counts()
    index=__import__('agent.index',fromlist=['DocumentIndex']).DocumentIndex(c['state_dir'])
    doc_errors=index.db.execute("SELECT COUNT(*) FROM docs WHERE error<>''").fetchone()[0]
    unindexed=0
    for m in load_matters(c):
        if not index.db.execute('SELECT 1 FROM docs WHERE matter=? LIMIT 1',(m['id'],)).fetchone():unindexed+=1
    jobs=desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='error'").fetchone()[0]
    waiting=desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
    proposals=desk.db.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
    deadlines=desk.db.execute("SELECT COUNT(*) FROM deadline_proposals WHERE status='pending'").fetchone()[0]
    tasks=desk.db.execute("SELECT COUNT(*) FROM tasks WHERE status='open'").fetchone()[0]
    now_dt=datetime.now(timezone.utc);delayed24=delayed48=0
    for _,status,_,stamp in state.rows(1000):
        if status not in ('review','error'):continue
        try:age=now_dt-datetime.fromisoformat(stamp)
        except (ValueError,TypeError):continue
        delayed24+=age.total_seconds()>=86400;delayed48+=age.total_seconds()>=172800
    attachment_errors=0
    for row in desk.db.execute('SELECT data FROM attachment_reviews'):
        try:attachment_errors+=len(json.loads(row[0]).get('errors',[]))
        except (ValueError,TypeError):pass
    memory=__import__('agent.memory',fromlist=['SentMemory']).SentMemory(c).status()
    unlearned=(memory.get('last_learning') or {}).get('reasons',{}).get('correspondant_non_associe',0)
    return {'brouillons':counts.get('drafted',0),'a_verifier':counts.get('review',0)+counts.get('error',0),
            'associations':proposals,'documents_en_erreur':doc_errors,'dossiers_sans_index':unindexed,
            'taches_en_erreur':jobs,'operations_en_attente':waiting,'dates_a_confirmer':deadlines,
            'relances_ou_taches':tasks,'sans_reponse_24h':delayed24,'sans_reponse_48h':delayed48,
            'pieces_illisibles':attachment_errors,'reponses_non_apprises':unlearned,
            'sante':desk.settings('health',{})}


def daily_digest(desk):
    # Kept as a harmless compatibility target for a job created by 1.5.1.
    # Technical counters belong in Administration, never in the mail drafts.
    return {'resume':'desactive_en_1_5_2','brouillon':False,
            'message':'La synthèse technique quotidienne est désactivée.'}


def automation_tick(desk):
    from . import economie569   # 5.6.9 : contrôles périodiques espacés en régime économe
    cfg=desk.c.get('automation',{});now_ts=datetime.now(timezone.utc).timestamp()
    schedules=[('health','health_interval_minutes',30),('sync','sync_interval_minutes',30),
      ('reconcile_inbox','reconcile_interval_minutes',15),
      ('classify_portfolio','portfolio_interval_minutes',360),
      ('index_all','index_interval_minutes',360)]
    workflow=desk.c.get('nextcloud_workflow') or {}
    if workflow.get('enabled'):
        schedules.append(('sync_caldav_tasks','nextcloud_tasks_interval_minutes',15))
    for job,key,default in schedules:
        setting_key='nextcloud_tasks_enabled' if job=='sync_caldav_tasks' else job+'_enabled'
        if not desk.settings('automation:'+setting_key,cfg.get(setting_key,True)):continue
        if job in ('reconcile_inbox','classify_portfolio'):
            from . import autonomy480
            if not autonomy480.allows(desk,'classement','agir'):continue
        last=desk.settings('auto:'+job,0)
        if now_ts-last>=economie569.interval(desk,job,int(cfg.get(key,default)))*60:
            # Reconciliation and portfolio refresh are background hygiene. They
            # must never overtake a draft or a question explicitly requested by
            # the lawyer.
            priority=(60 if job in ('reconcile_inbox','classify_portfolio') else
                      50 if job=='sync_caldav_tasks' else None)
            desk.enqueue(job,priority=priority);desk.setting('auto:'+job,now_ts)
    proactive=desk.c.get('proactive',{})
    if desk.settings('automation:proactive_enabled',proactive.get('enabled',True)):
        minutes=economie569.interval(desk,'monitor_all',max(5,int(proactive.get('monitor_interval_minutes',30))))
        last=desk.settings('auto:monitor_all',0)
        if now_ts-last>=minutes*60:
            desk.enqueue('monitor_all');desk.setting('auto:monitor_all',now_ts)
        if desk.settings('automation:daily_dashboard_enabled',proactive.get('daily_dashboard_enabled',True)):
            try:local=datetime.now(ZoneInfo(proactive.get('timezone','Europe/Paris')))
            except ZoneInfoNotFoundError:local=datetime.now(timezone.utc)
            day=local.date().isoformat();hour=max(0,min(int(proactive.get('daily_dashboard_hour',7)),23))
            if local.hour>=hour and desk.settings('auto:daily_dashboard_day','')!=day:
                desk.enqueue('build_daily_dashboard');desk.setting('auto:daily_dashboard_day',day)
    autonomy=desk.c.get('autonomy',{})
    orchestrator=desk.c.get('orchestrator',{})
    if desk.settings('automation:orchestrator_enabled',orchestrator.get('enabled',False)):
        minutes=max(5,int(orchestrator.get('mail_monitor_interval_minutes',5)))
        last=desk.settings('auto:orchestrator_mail_sweep',0)
        if now_ts-last>=minutes*60:
            desk.enqueue('orchestrator_mail_sweep',priority=40)
            desk.setting('auto:orchestrator_mail_sweep',now_ts)
    elif desk.settings('automation:autonomy_enabled',autonomy.get('enabled',False)):
        minutes=max(5,int(autonomy.get('mail_monitor_interval_minutes',5)))
        last=desk.settings('auto:autonomy_mail_sweep',0)
        if now_ts-last>=minutes*60:
            desk.enqueue('autonomy_mail_sweep',priority=40)
            desk.setting('auto:autonomy_mail_sweep',now_ts)
    pilot=desk.c.get('cabinet_pilotage',{})
    if desk.settings('automation:cabinet_pilotage_enabled',pilot.get('enabled',True)):
        minutes=economie569.interval(desk,'refresh_cabinet_pilotage',max(5,int(pilot.get('refresh_interval_minutes',30))))
        last=desk.settings('auto:refresh_cabinet_pilotage',0)
        if now_ts-last>=minutes*60:
            desk.enqueue('refresh_cabinet_pilotage',priority=65)
            desk.setting('auto:refresh_cabinet_pilotage',now_ts)
        test_minutes=economie569.interval(desk,'run_continuous_business_tests',max(15,int(pilot.get('continuous_tests_interval_minutes',60))))
        last_test=desk.settings('auto:run_continuous_business_tests',0)
        if now_ts-last_test>=test_minutes*60:
            desk.enqueue('run_continuous_business_tests',priority=90)
            desk.setting('auto:run_continuous_business_tests',now_ts)
    from .proactive34 import schedule
    schedule(desk)
    production=desk.c.get('production',{})
    if desk.settings('automation:production_enabled',production.get('enabled',True)):
        minutes=economie569.interval(desk,'production_cycle391',max(5,int(production.get('interval_minutes',5))))
        last=desk.settings('auto:production_cycle391',0)
        if now_ts-last>=minutes*60:
            desk.enqueue('production_cycle391',{'limit':int(production.get('batch_size',20))},priority=35)
            desk.setting('auto:production_cycle391',now_ts)
        day=datetime.now(timezone.utc).date().isoformat()
        if desk.settings('auto:metrics420_day','')!=day:
            desk.enqueue('snapshot_metrics420',{'days':int(production.get('useful_metrics_days',30))},priority=90)
            desk.setting('auto:metrics420_day',day)


def perform(desk,kind,args):
    if kind=='health':return health(desk)
    if kind=='daily_digest':return daily_digest(desk)
    if kind=='automation_setting':
        key=args.get('key','')
        if key not in ('health_enabled','sync_enabled','index_all_enabled','daily_digest_enabled',
                       'reconcile_inbox_enabled','classify_portfolio_enabled','nextcloud_tasks_enabled',
                       'proactive_enabled','daily_dashboard_enabled','autonomy_enabled',
                       'automatic_document_previews_enabled','document_control_enabled',
                       'automatic_mail_drafts_enabled','automatic_legal_projects_enabled',
                       'automatic_internal_files_enabled','production_enabled',
                       'diligence_proposals_enabled','billing_proposals_enabled'):
            if key not in ('orchestrator_enabled','opinion_projects_enabled','cabinet_pilotage_enabled',
                           'preparation34_enabled'):
                raise Stop('automatisme_invalide')
        value=args.get('value')=='yes'
        if key in ('proactive_enabled','daily_dashboard_enabled'):
            desk.c.setdefault('proactive',{})[key.removesuffix('_enabled')+'_enabled' if key=='daily_dashboard_enabled' else 'enabled']=value
            desk.setting('automation:'+key,value)
        elif key in ('autonomy_enabled','automatic_document_previews_enabled',
                     'automatic_mail_drafts_enabled','automatic_legal_projects_enabled',
                     'automatic_internal_files_enabled',
                     'document_control_enabled','diligence_proposals_enabled','billing_proposals_enabled'):
            desk.setting('automation:'+key,value)
        elif key in ('orchestrator_enabled','opinion_projects_enabled','cabinet_pilotage_enabled'):
            desk.setting('automation:'+key,value)
        else:desk.setting('automation:'+key,value)
        desk.audit('automatisme_modifie',{'key':key,'enabled':value})
        return {'automatisme':key,'active':value}
    raise Stop('action_inconnue')

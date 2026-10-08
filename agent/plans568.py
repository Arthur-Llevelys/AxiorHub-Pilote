"""Suites persistantes de missions : DAG borné, idempotence et reprise du même producteur."""
import hashlib
import json
import re
import secrets

from .common import Stop, load_matters
from . import missions567, settings568

DONE={'verified','answered'}
# 5.6.14 (C12) : une abstention reste visible et bloque la dépendance ; elle n'équivaut pas à un résultat exploitable.
ABSTAINED={'abstained'}


def _row(desk,ident,owner,admin=False):
    r=desk.db.execute('SELECT * FROM plans_v568 WHERE id=?',(ident,)).fetchone()
    if not r or (r['owner']!=owner and not admin):raise Stop('plan_absent')
    return dict(r)


def create(desk,data,owner='cabinet'):
    if data.get('autonomy','prepare') not in ('prepare','suggest'):raise Stop('autonomie_mission_invalide')
    matter=str(data.get('matter') or '')
    if matter and matter not in {str(m['id']) for m in load_matters(desk.c)}:raise Stop('dossier_absent')
    title=str(data.get('title') or '').strip();token=str(data.get('request_key') or '')
    steps=data.get('steps') or []
    if not 3<=len(title)<=500 or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',token):raise Stop('plan_invalide')
    if not isinstance(steps,list) or not 1<=len(steps)<=8:raise Stop('huit_etapes_maximum')
    normalized=[]
    for i,s in enumerate(steps):
        if not isinstance(s,dict) or s.get('role') not in settings568.ROLES:raise Stop('role_agent_invalide')
        text=str(s.get('instruction') or '').strip();deps=s.get('depends_on',[])
        if not 3<=len(text)<=12000 or not isinstance(deps,list) or any(type(n)!=int or n<0 or n>=i for n in deps):raise Stop('dependances_plan_invalides')
        ctx=missions567._context(desk,{'context':s.get('context') or {}},next((m for m in load_matters(desk.c) if str(m['id'])==matter),None),owner)
        if type(s.get('analysis_only',False))!=bool:raise Stop('plan_invalide')
        ctx['_analysis_only']=s.get('analysis_only',False)
        normalized.append({'role':s['role'],'instruction':text,'depends_on':sorted(set(deps)),'context':ctx})
    fp=hashlib.sha256(json.dumps([matter,title,normalized],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    ident=secrets.token_hex(16);now=desk.now();state='suggested' if data.get('autonomy')=='suggest' else 'active'
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        old=desk.db.execute('SELECT id,fingerprint FROM plans_v568 WHERE owner=? AND request_key=?',(owner,token)).fetchone()
        if old:
            if old['fingerprint']!=fp:raise Stop('requete_reutilisee_avec_autres_donnees')
            desk.db.commit();return get(desk,old['id'],owner)
        desk.db.execute('INSERT INTO plans_v568 VALUES(?,?,?,?,?,?,?,?,?)',(ident,owner,token,fp,matter,title,state,now,now))
        for i,s in enumerate(normalized):
            desk.db.execute('INSERT INTO plan_steps_v568 VALUES(?,?,?,?,?,?,?, ?,?)',(ident,i,s['role'],s['instruction'],json.dumps(s['depends_on']),json.dumps(s['context'],ensure_ascii=False),'','waiting',now))
        desk.db.commit()
    except BaseException:desk.db.rollback();raise
    if state=='active':advance(desk,ident)
    return get(desk,ident,owner)


def get(desk,ident,owner='cabinet',admin=False,prefix=''):
    r=_row(desk,ident,owner,admin);steps=[]
    for raw in desk.db.execute('SELECT * FROM plan_steps_v568 WHERE plan_id=? ORDER BY position',(ident,)).fetchall():
        s=dict(raw);s['dependencies']=json.loads(s['dependencies']);s['context']=json.loads(s['context'])
        if s['mission_id']:s['mission']=missions567.get(desk,s['mission_id'],r['owner'],prefix=prefix)
        steps.append(s)
    return {**r,'steps':steps}


def advance(desk,ident):
    row=desk.db.execute('SELECT * FROM plans_v568 WHERE id=?',(ident,)).fetchone()
    if not row or row['state'] not in ('active','blocked'):return
    if desk.settings('missions567:pause',False):return
    steps=[dict(s) for s in desk.db.execute('SELECT * FROM plan_steps_v568 WHERE plan_id=? ORDER BY position',(ident,)).fetchall()]
    states={};blocked=False
    for s in steps:
        if s['mission_id']:
            m=missions567.get(desk,s['mission_id'],row['owner']);states[s['position']]=m['state']
            desk.db.execute('UPDATE plan_steps_v568 SET state=?,updated=? WHERE plan_id=? AND position=?',(m['state'],desk.now(),ident,s['position']));desk.db.commit()
            blocked=blocked or m['state'] in ('error','decision','paused','prepared','abstained')
            continue
        enabled=settings568.profile(desk,row['owner'])['roles']
        if s['role'] not in enabled:
            states[s['position']]='disabled';blocked=True;continue
        if any(states.get(n) not in DONE for n in json.loads(s['dependencies'])):
            states[s['position']]='waiting';continue
        # A launch claim expires after a crash; deterministic mission key recovers its result.
        cur=desk.db.execute("UPDATE plan_steps_v568 SET state='launching',updated=? WHERE plan_id=? AND position=? AND (state='waiting' OR (state='launching' AND updated<?))",
                            (desk.now(),ident,s['position'],_ago(desk,120)))
        desk.db.commit()
        if not cur.rowcount:states[s['position']]=s['state'];continue
        ctx=json.loads(s['context']);ctx.pop('parent_mission',None)
        parents=[x for x in steps if x['position'] in json.loads(s['dependencies']) and x['mission_id']]
        if parents:ctx['mission_id']=parents[-1]['mission_id']
        try:
            m=missions567.create(desk,{'matter':row['matter'],'instruction':s['instruction'],'context':ctx,
                       'request_key':'plan568-'+ident+'-'+str(s['position']),'autonomy':'prepare',
                       'analysis_only':ctx.pop('_analysis_only',False)},row['owner'])
            desk.db.execute('UPDATE plan_steps_v568 SET mission_id=?,state=?,updated=? WHERE plan_id=? AND position=?',(m['id'],m['state'],desk.now(),ident,s['position']));desk.db.commit()
            states[s['position']]=m['state'];blocked=blocked or m['state'] in ('error','decision')
        except Stop:
            desk.db.execute("UPDATE plan_steps_v568 SET state='waiting' WHERE plan_id=? AND position=?",(ident,s['position']));desk.db.commit();raise
    state='verified' if len(states)==len(steps) and all(v in DONE for v in states.values()) else ('blocked' if blocked else 'active')
    desk.db.execute('UPDATE plans_v568 SET state=?,updated=? WHERE id=? AND state NOT IN (\'paused\',\'cancelled\')',(state,desk.now(),ident));desk.db.commit()


def _ago(desk,seconds):
    from datetime import datetime,timezone,timedelta
    return (datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat()


def control(desk,data,owner='cabinet',admin=False):
    r=_row(desk,str(data.get('id') or ''),owner,admin);action=data.get('action')
    if action not in ('pause','resume','cancel'):raise Stop('action_plan_invalide')
    if action=='resume' and r['state']=='cancelled':raise Stop('plan_annule')
    state={'pause':'paused','resume':'active','cancel':'cancelled'}[action]
    desk.db.execute('UPDATE plans_v568 SET state=?,updated=? WHERE id=?',(state,desk.now(),r['id']));desk.db.commit()
    for s in desk.db.execute('SELECT mission_id FROM plan_steps_v568 WHERE plan_id=? AND mission_id<>\'\'',(r['id'],)).fetchall():
        m=missions567.get(desk,s['mission_id'],r['owner'])
        if action in ('pause','cancel') and m['state'] in missions567.ACTIVE:missions567.control(desk,{'id':m['id'],'action':'pause'},r['owner'])
        if action=='resume' and m['state'] in ('paused','error','suggested'):missions567.control(desk,{'id':m['id'],'action':'resume'},r['owner'])
    if action=='resume':advance(desk,r['id'])
    return get(desk,r['id'],owner,admin)

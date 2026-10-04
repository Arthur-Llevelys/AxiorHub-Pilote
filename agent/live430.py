"""Durable business activity; no prompts, credentials or mail bodies in the feed."""
from datetime import datetime, timezone
from html import escape
from html import unescape
import hashlib
import json
import re
import sqlite3
import time

from .common import Stop, load_matters

LABELS = {'pending':'En attente', 'running':'En cours', 'done':'Terminé',
          'error':'Échec — relance possible', 'cancelled':'Annulé',
          'cancel_requested':'Annulation demandée'}


def emit(desk, kind, message, job_id=None, matter='', source_key='', dedupe=None):
    desk.db.execute('''INSERT OR IGNORE INTO live_events_v430
      (at,kind,message,job_id,matter,source_key,dedupe) VALUES(?,?,?,?,?,?,?)''',
      (desk.now(),str(kind)[:40],str(message)[:600],job_id,str(matter)[:120],str(source_key)[:64],dedupe))
    desk.db.commit()


def progress(desk, message, matter='', source_key=''):
    jid=getattr(desk,'active_job_id',None)
    if not jid:return
    desk.db.execute('UPDATE jobs SET progress=? WHERE id=?',(message[:300],jid))
    emit(desk,'progress',message,jid,matter,source_key)


def heartbeat(desk, name, status='active', message='', next_check=0):
    desk.db.execute('INSERT OR REPLACE INTO live_services_v430 VALUES(?,?,?,?,?)',
      (name,time.time(),status,message[:300],next_check));desk.db.commit()


def fingerprint(fields):
    # Exclude transport/UI-only fields, not the instruction or selected dossier.
    clean={k:str(v) for k,v in fields.items() if k not in ('csrf','back','live_token')}
    return hashlib.sha256(json.dumps(clean,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def reserve_request(desk, token, fields):
    if not re.fullmatch(r'[a-zA-Z0-9-]{16,80}',token):raise Stop('identifiant_requete_invalide')
    fp=fingerprint(fields)
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        row=desk.db.execute('SELECT * FROM live_requests_v430 WHERE token=?',(token,)).fetchone()
        if row:
            if row['fingerprint']!=fp:raise Stop('requete_reutilisee_avec_autres_donnees')
            if row['status']=='reserved':raise Stop('requete_en_cours_verifier_activite')
            desk.db.commit();return dict(row)
        desk.db.execute('INSERT INTO live_requests_v430 VALUES(?,?,?,NULL,?,?)',
          (token,fp,'reserved','',desk.now()));desk.db.commit();return None
    except Exception:
        desk.db.rollback();raise


def finish_request(desk,token,job_id,target):
    desk.db.execute("UPDATE live_requests_v430 SET status='recorded',job_id=?,target=? WHERE token=?",
      (job_id,target,token));desk.db.commit()


def snapshot(desk, after=0):
    from .improvements36 import matter_option
    now=time.time();names={m['id']:matter_option(m) for m in load_matters(desk.c)}
    rows=desk.db.execute("SELECT id,kind,status,priority,created,started,finished,progress,args FROM jobs WHERE status IN ('pending','running','cancel_requested') OR id IN (SELECT id FROM jobs ORDER BY id DESC LIMIT 150) ORDER BY id DESC").fetchall()
    pending=sorted((r for r in rows if r['status']=='pending'),key=lambda r:(r['priority'],r['id']))
    positions={r['id']:n+1 for n,r in enumerate(pending)}
    jobs=[]
    for row in rows:
        args=json.loads(row['args'] or '{}');mid=str(args.get('matter') or '')
        if not mid:
            context=desk.db.execute("SELECT matter FROM live_events_v430 WHERE job_id=? AND matter<>'' ORDER BY id DESC LIMIT 1",(row['id'],)).fetchone()
            if context:mid=context[0]
        # Never publish args/result indiscriminately (they may contain secrets).
        item={k:row[k] for k in ('id','kind','status','created','started','finished','progress')}
        item.update(label=LABELS.get(row['status'],'État inconnu'),matter=names.get(mid,mid),
                    position=positions.get(row['id']),href='/administration',error='')
        if args.get('key') and re.fullmatch('[a-f0-9]{64}',str(args['key'])):item['href']='/mail?key='+args['key']
        elif mid:item['href']='/matter?id='+__import__('urllib.parse',fromlist=['quote']).quote(mid)
        if row['status']=='error':
            from .web import REASONS
            result=desk.db.execute('SELECT result FROM jobs WHERE id=?',(row['id'],)).fetchone()[0]
            try:code=str(json.loads(result or '{}').get('erreur','action_interrompue'))
            except (ValueError,TypeError):code='action_interrompue'
            item['error']=REASONS.get(code,'Traitement interrompu. Consultez le diagnostic avant de relancer.')
            item['error_code']=code if re.fullmatch('[a-z0-9_]{1,100}',code) else 'action_interrompue'
        if row['status']=='done':
            out=desk.db.execute('SELECT status,business_message,source_id,deliverable_kind FROM production_deliverables_v420 WHERE job_id=? ORDER BY updated DESC LIMIT 1',(row['id'],)).fetchone()
            if out:item.update(outcome=out['status'],message=out['business_message'])
            if out and out['status']=='verified' and out['deliverable_kind']=='mail_draft' and re.fullmatch('[a-f0-9]{64}',out['source_id']):
                item.update(href='/mail?key='+out['source_id'],open_label='Ouvrir le brouillon')
        item['outputs']=[{**dict(x),'details':json.loads(x['details'])} for x in
          desk.db.execute('SELECT source_key,matter,status,message,details,updated FROM live_outputs_v431 WHERE job_id=? ORDER BY updated DESC LIMIT 20',(row['id'],))]
        verified=next((x for x in item['outputs'] if x['status']=='produced'),None)
        if verified:
            item.update(outcome='verified',message=verified['message'],href='/mail?key='+verified['source_key'],open_label='Ouvrir le brouillon')
        elif item.get('outcome')=='verified':item.setdefault('open_label','Ouvrir le livrable')
        if row['kind']=='review_action380':
            item['review_state']=args.get('state','');item['action_id']=args.get('action_id','')
            decision=desk.db.execute('SELECT state FROM action_reviews_v380 WHERE action_id=?',(item['action_id'],)).fetchone()
            item['can_restore']=bool(decision and decision[0]=='dismissed')
        start=row['started'] or row['created'];end=row['finished']
        try:item['elapsed_seconds']=max(0,round((datetime.fromisoformat(end).timestamp() if end else now)-datetime.fromisoformat(start).timestamp()))
        except (TypeError,ValueError):item['elapsed_seconds']=None
        incident=desk.db.execute('SELECT incident FROM live_maintenance_v431 WHERE job_id=?',(row['id'],)).fetchone()
        item['blocked']=bool(incident and incident[0])
        item['steps']=[dict(x) for x in desk.db.execute('SELECT at,message FROM live_events_v430 WHERE job_id=? ORDER BY id DESC LIMIT 12',(row['id'],))][::-1]
        jobs.append(item)
    services=[]
    for row in desk.db.execute('SELECT * FROM live_services_v430 ORDER BY name'):
        item=dict(row);item['age_seconds']=round(now-row['heartbeat']);item['stale']=now-row['heartbeat']>90
        services.append(item)
    events=[dict(r) for r in desk.db.execute('SELECT * FROM live_events_v430 WHERE id>? ORDER BY id LIMIT 100',(max(0,int(after)),))]
    latest=desk.db.execute('SELECT COALESCE(MAX(id),0) FROM live_events_v430').fetchone()[0]
    requests=[dict(r) for r in desk.db.execute("SELECT fingerprint,job_id,target FROM live_requests_v430 WHERE status='recorded' ORDER BY created DESC,rowid DESC LIMIT 300")]
    return {'jobs':jobs,'services':services,'events':events,'cursor':latest,'requests':requests,
      'pending':desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0],
      'running':desk.db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('running','cancel_requested')").fetchone()[0],
      'mail_drafts_enabled':bool(desk.settings('automation:automatic_mail_drafts_enabled',desk.c.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True))),
      'mode':desk.c.get('mode','observe')}


def recent(desk,limit=30):
    return [dict(r) for r in desk.db.execute('SELECT * FROM live_events_v430 ORDER BY id DESC LIMIT ?',(limit,))]


def stream(config,after=0,duration=25):
    # Short-lived streams cap occupied WSGI threads; EventSource reconnects with
    # Last-Event-ID. A dedicated read-only DB handle avoids migrations per tick.
    from pathlib import Path
    db=sqlite3.connect(Path(config['state_dir'])/'desk.sqlite3',timeout=10)
    db.row_factory=sqlite3.Row;deadline=time.monotonic()+duration;cursor=after
    try:
        yield b'retry: 2000\n: connected\n\n'
        while time.monotonic()<deadline:
            rows=db.execute('SELECT * FROM live_events_v430 WHERE id>? ORDER BY id LIMIT 100',(cursor,)).fetchall()
            for row in rows:
                cursor=row['id']
                data=json.dumps(dict(row),ensure_ascii=False).replace('\n','\\n')
                yield ('id: '+str(cursor)+'\nevent: activity\ndata: '+data+'\n\n').encode()
            yield b': heartbeat\n\n'
            time.sleep(2)
    finally:db.close()


def panel(desk,prefix,csrf):
    from .web import JOB_LABELS
    esc=lambda x:escape(str('' if x is None else x),quote=True)
    data=snapshot(desk);html='<div id="ws-live-panel-content"><p>'+str(data['running'])+' en cours · '+str(data['pending'])+' en attente</p>'
    for item in data['jobs']:
        if item['status'] not in ('pending','running','cancel_requested','error') and not item['outputs'] and not item.get('can_restore'):continue
        html+='<article class="live-job" data-live-job="'+str(item['id'])+'"><strong>'+esc(JOB_LABELS.get(item['kind'],'Travail du cabinet'))+'</strong><small>'+esc(item['matter'])+'</small><p>'+esc(item['progress'] or item['label'])+'</p>'
        html+='<small>'+esc('Bloqué' if item['blocked'] or item['status']=='error' else 'Produit' if item.get('outcome')=='verified' else 'En cours' if item['status']=='running' else 'Détecté')+' · '+esc(item['elapsed_seconds'])+' s écoulées</small>'
        if item['blocked']:html+='<p class="live-error">Worker non confirmé ou durée excessive ; contrôler l’état du système avant toute relance.</p>'
        for output in item['outputs']:
            detail=output['details'];v=detail.get('verification') or {}
            html+='<p>'+esc(output['message'])+'</p><small>Livrable attendu : '+esc(detail.get('expected'))+' · '+esc(detail.get('source_count'))+' sources rassemblées</small>'
            counts=detail.get('source_counts') or {}
            html+='<small>'+esc(counts.get('email_history',0))+' courriels du fil · '+esc(counts.get('document',0))+' documents · '+esc(counts.get('calendar_event',0))+' événements retenus</small>'
            html+='<small>Agenda : '+('consulté' if detail.get('calendar_checked') else 'non requis ou non consulté')+' · documents : '+('consultés' if detail.get('documents_checked') else 'non consultés')+'</small>'
            for model in detail.get('models',[]):html+='<small>'+esc(model.get('function'))+' : '+esc(model.get('provider'))+' · '+esc(model.get('model'))+'</small>'
            if detail.get('sources'):
                html+='<details><summary>Sources rassemblées</summary><ul>'+''.join('<li>'+esc(x.get('path') or x.get('id'))+' · '+esc(x.get('kind'))+(' · page '+esc(x['page']) if x.get('page') else '')+'</li>' for x in detail['sources'])+'</ul></details>'
            cost=detail.get('external_cost_usd')
            html+='<small>Coût externe : '+(esc(cost)+' $ calculés selon les tarifs configurés ; hors facture fournisseur.' if cost is not None else 'non mesuré ; voir le journal de routage.')+'</small>'
            if v.get('uid'):html+='<small>'+esc(v.get('folder'))+' · UID '+esc(v.get('uid'))+' · UIDVALIDITY '+esc(v.get('uidvalidity'))+' · '+esc(v.get('verified_at'))+'</small>'
            html+='<a href="'+esc(prefix)+'/mail?key='+esc(output['source_key'])+'">'+('Ouvrir le brouillon' if output['status']=='produced' else 'Examiner le courriel')+'</a>'
        if item['steps']:
            html+='<details><summary>Étapes et déclencheur</summary><ol>'+''.join('<li>'+esc(x['at'][11:19])+' · '+esc(x['message'])+'</li>' for x in item['steps'])+'</ol></details>'
        if item['position']:html+='<small>Position '+str(item['position'])+' · depuis '+esc(item['created'])+'</small>'
        if item['error']:html+='<p class="live-error">'+esc(item['error'])+' <code>'+esc(item.get('error_code'))+'</code></p>'
        action='retry_job393' if item['status']=='error' else 'cancel_job'
        if item['status'] in ('pending','running','error'):
            html+='<form method="post" action="'+esc(prefix)+'/action" hx-post="'+esc(prefix)+'/action" hx-swap="none"><input type="hidden" name="csrf" value="'+esc(csrf)+'"><input type="hidden" name="action" value="'+action+'"><input type="hidden" name="job" value="'+str(item['id'])+'"><button>'+('Relancer' if item['status']=='error' else 'Annuler')+'</button></form>'
        if item.get('can_restore'):
            html+='<form method="post" action="'+esc(prefix)+'/action" hx-post="'+esc(prefix)+'/action" hx-swap="none"><input type="hidden" name="csrf" value="'+esc(csrf)+'"><input type="hidden" name="action" value="review_action380"><input type="hidden" name="action_id" value="'+esc(item['action_id'])+'"><input type="hidden" name="state" value="restored"><button>Rétablir l’action écartée</button></form>'
        html+='<a href="'+esc(prefix+item['href'])+'">Ouvrir</a></article>'
    html+='<h3>Journal métier</h3><ol class="live-timeline">'
    for event in recent(desk):
        html+='<li><time>'+esc(event['at'][11:19])+' UTC</time> '+esc(event['message'])
        if re.fullmatch('[a-f0-9]{64}',event['source_key']):html+=' <a href="'+esc(prefix)+'/mail?key='+event['source_key']+'">Ouvrir le courriel</a>'
        html+='</li>'
    return html+'</ol></div>'


def enhance_forms(html,prefix):
    """Progressive enhancement: conventional forms remain a working fallback."""
    def replace(match):
        form=match[0];opening=form.split('>',1)[0]
        if 'method="post"' not in opening.lower() or ('action="'+prefix+'/action"') not in opening:return form
        fields={}
        for tag in re.findall(r'<input\b[^>]*>',form):
            attrs={k:unescape(v) for k,v in re.findall(r'([\w-]+)="([^"]*)"',tag)}
            if attrs.get('name'):fields[attrs['name']]=attrs.get('value','')
        static=not bool(re.search(r'<(?:textarea|select)\b|<input(?![^>]*type="hidden")',form))
        attrs=' hx-post="'+escape(prefix,quote=True)+'/action" hx-swap="none" hx-sync="this:drop"'
        if static:attrs+=' data-live-key="'+fingerprint(fields)+'"'
        if 'hx-post=' not in opening:form=form.replace('>',attrs+'>',1)
        return form
    return re.sub(r'<form\b.*?</form>',replace,html,flags=re.S)


def chrome(prefix):
    p=escape(prefix,quote=True)
    return ('<div id="ws-live-bar" role="status"><span class="live-dot"></span><span id="ws-live-summary">Connexion à l’activité…</span>'
      '<button type="button" id="ws-live-toggle" aria-controls="ws-live-panel" aria-expanded="false">Activité de l’assistant</button></div>'
      '<aside id="ws-live-panel" hidden aria-label="Activité de l’assistant"><header><h2>Ce que fait AxiorHub</h2><button type="button" id="ws-live-close" aria-label="Fermer">×</button></header>'
      '<div id="ws-live-panel-content" hx-get="'+p+'/live/panel" hx-trigger="live-refresh from:body" hx-swap="outerHTML"></div></aside>'
      '<div id="ws-live-toast" hidden role="status"></div>')


def settings_html(desk,prefix,csrf):
    esc=lambda x:escape(str(x),quote=True)
    enabled=desk.settings('live430:enabled',True)
    return ('<section class="card"><h3>Assistant vivant · 4.3</h3><p>IMAP IDLE et contrôle de secours toutes les 5 minutes. Agenda et fichiers surveillés sans envoi ni dépôt définitif.</p>'
      '<form method="post" action="'+esc(prefix)+'/action"><input type="hidden" name="csrf" value="'+esc(csrf)+'"><input type="hidden" name="action" value="save_live430">'
      '<label>Surveillance<select name="enabled"><option value="yes"'+(' selected' if enabled else '')+'>Activée</option><option value="no"'+('' if enabled else ' selected')+'>En pause</option></select></label>'
      '<p>Le mode actuel est <strong>'+esc(desk.c.get('mode','observe'))+'</strong>. Les réglages d’autonomie existants restent appliqués : activer la surveillance ne les modifie pas.</p>'
      '<button>Enregistrer la surveillance</button></form></section>')

"""Operational verification and recovery controls for AxiorHub 3.9.3.

Checks are deliberately evidence based.  A successful producer return value is
not enough: IMAP drafts and Nextcloud files become ``verified`` only after a
fresh read from the destination service.  Secret values and document contents
never enter these tables or diagnostics.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import subprocess

from .common import HTTP, Stop
from .dav import DAV
from .mailbox import Mailbox
from .state import State


SERVICES = (
    ('axiorhub-mail-ui.service', 'Interface AxiorHub', True),
    ('axiorhub-mail-desk-worker.service', 'Moteur de traitements', True),
    ('axiorhub-mail-agent.timer', 'Analyse périodique', True),
    ('axiorhub-mail-agent-cleanup.timer', 'Nettoyage périodique', True),
    ('ollama.service', 'Ollama', False),
)
TERMINAL = {'done', 'error', 'cancelled'}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS reliability_checks_v393(
      check_key TEXT PRIMARY KEY, category TEXT NOT NULL, status TEXT NOT NULL,
      summary TEXT NOT NULL, evidence TEXT NOT NULL, checked TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS reliability_verifications_v393(
      target_kind TEXT NOT NULL, target_id TEXT NOT NULL, target_path TEXT NOT NULL,
      status TEXT NOT NULL, evidence TEXT NOT NULL, checked TEXT NOT NULL,
      PRIMARY KEY(target_kind,target_id,target_path));
    CREATE INDEX IF NOT EXISTS reliability_verifications_status_v393
      ON reliability_verifications_v393(status,checked);
    CREATE TABLE IF NOT EXISTS reliability_retries_v393(
      original_job_id INTEGER NOT NULL, new_job_id INTEGER NOT NULL UNIQUE,
      attempt INTEGER NOT NULL, created TEXT NOT NULL,
      PRIMARY KEY(original_job_id,new_job_id));
    ''')
    desk.db.commit()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse(value):
    try:
        parsed=datetime.fromisoformat(str(value or '').replace('Z','+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _json(value, default):
    try:return json.loads(value) if value else default
    except (TypeError, ValueError):return default


def _store_check(desk, key, category, status, summary, evidence=None, checked=None):
    ensure_schema(desk);checked=checked or _now()
    safe=evidence if isinstance(evidence,dict) else {}
    desk.db.execute('INSERT OR REPLACE INTO reliability_checks_v393 VALUES(?,?,?,?,?,?)',
      (key,category,status,str(summary)[:1000],json.dumps(safe,ensure_ascii=False),checked))
    desk.db.commit()
    return {'key':key,'category':category,'status':status,'summary':str(summary)[:1000],
            'evidence':safe,'checked_at':checked,'verified':status=='verified'}


def _store_verification(desk, kind, target_id, path, status, evidence=None, checked=None):
    ensure_schema(desk);checked=checked or _now()
    desk.db.execute('INSERT OR REPLACE INTO reliability_verifications_v393 VALUES(?,?,?,?,?,?)',
      (kind,str(target_id)[:160],str(path)[:2000],status,
       json.dumps(evidence or {},ensure_ascii=False),checked))
    desk.db.commit()


def _error_from_result(raw):
    value=_json(raw,{})
    if not isinstance(value,dict):return str(raw or 'erreur_non_detaillee')[:1500]
    for key in ('erreur','error','message','detail','reason'):
        text=value.get(key)
        if isinstance(text,str) and text.strip():return text.strip()[:1500]
    return json.dumps(value,ensure_ascii=False)[:1500] or 'erreur_non_detaillee'


def _automatic(kind, priority):
    try:
        from .queue521 import automatic
        return automatic(kind, priority)
    except Exception:
        return False


def job_health(desk, current=None):
    """Detect stale work and repeated failures without exposing job payloads."""
    ensure_schema(desk);current=current or datetime.now(timezone.utc)
    policy=desk.c.get('reliability393',{})
    running_hours=max(1,min(int(policy.get('running_stale_hours',2)),72))
    pending_hours=max(1,min(int(policy.get('pending_stale_hours',6)),168))
    rows=desk.db.execute('SELECT id,kind,args,status,priority,created,finished,result FROM jobs ORDER BY id DESC LIMIT 500').fetchall()
    recent=[];stale=[];groups={}
    for row in rows:
        item=dict(row);created=_parse(item.get('created'))
        age=round((current-created).total_seconds()/3600,1) if created else None
        item['age_hours']=age;item['error_reason']=_error_from_result(item.get('result')) if item['status']=='error' else ''
        item.pop('args',None);item['stale']=False;item['loop']=False
        threshold=running_hours if item['status'] in ('running','cancel_requested') else pending_hours
        if item['status'] in ('pending','running','cancel_requested') and (age is None or age>threshold):
            item['stale']=True;stale.append(item)
        recent.append(item)
        # 5.6.2 : une interruption par redémarrage du service n'est pas une boucle pour un contrôle automatique (il reprend seul).
        interrupted=item['error_reason']=='service_redemarre_verifier_avant_relance' and _automatic(row['kind'],row['priority'])
        if row['status']=='error' and not interrupted and created and current-created<=timedelta(hours=24):
            payload=_json(row['args'],{})
            if isinstance(payload,dict):
                payload={k:v for k,v in payload.items() if k not in ('retry_of','retry_attempt')}
            fingerprint=hashlib.sha256((row['kind']+'|'+json.dumps(payload,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
            groups.setdefault(fingerprint,[]).append(item)
    loops=[]
    for values in groups.values():
        if len(values)>=3:
            for item in values:item['loop']=True
            loops.append({'kind':values[0]['kind'],'count':len(values),
                          'latest_job_id':values[0]['id'],'error_reason':values[0]['error_reason']})
    status='verified' if not stale and not loops else ('error' if stale or loops else 'warning')
    check=_store_check(desk,'jobs','queue',status,
      ('Aucun traitement bloqué ou en boucle.' if status=='verified' else
       f'{len(stale)} traitement(s) trop ancien(s), {len(loops)} boucle(s) détectée(s).'),
      {'stale_count':len(stale),'loop_count':len(loops),'observed_jobs':len(rows),
       'running_stale_hours':running_hours,'pending_stale_hours':pending_hours})
    return {**check,'stale':stale,'loops':loops,'recent':recent[:100]}


def retry_job(desk, job_id):
    """Create a bounded copy of any failed, user-visible worker job."""
    from .desk import JOBS
    ensure_schema(desk)
    try:job_id=int(job_id)
    except (TypeError,ValueError):raise Stop('operation_invalide') from None
    row=desk.db.execute('SELECT id,kind,args,status,priority FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise Stop('operation_absente')
    if row['status']!='error':raise Stop('operation_non_echouee')
    if row['kind'] not in JOBS:raise Stop('incident_non_rejouable')
    attempts=desk.db.execute('SELECT COUNT(*) FROM reliability_retries_v393 WHERE original_job_id=?',(job_id,)).fetchone()[0]
    if attempts>=3:raise Stop('nombre_reprises_depasse')
    payload=_json(row['args'],{})
    if not isinstance(payload,dict):raise Stop('arguments_operation_invalides')
    payload=dict(payload);payload['retry_of']=job_id;payload['retry_attempt']=attempts+1
    new_job=desk.enqueue(row['kind'],payload,priority=row['priority'])
    desk.db.execute('INSERT INTO reliability_retries_v393 VALUES(?,?,?,?)',
                    (job_id,new_job,attempts+1,_now()))
    desk.db.commit();desk.audit('operation_relancee_v393',{
      'original_job_id':job_id,'new_job_id':new_job,'kind':row['kind'],'attempt':attempts+1})
    return {'job_id':new_job,'retry_of':job_id,'attempt':attempts+1,'status':'pending'}


def service_health(desk, runner=None):
    runner=runner or subprocess.run;rows=[]
    for unit,label,required in SERVICES:
        try:
            result=runner(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5,check=False)
            state=(result.stdout or result.stderr or 'unknown').strip().splitlines()[0][:80]
        except (OSError,subprocess.SubprocessError,IndexError):state='unknown'
        active=state=='active';rows.append({'unit':unit,'label':label,'state':state,
          'required':required,'verified':active})
    failed=[x for x in rows if x['required'] and not x['verified']]
    optional=[x for x in rows if not x['required'] and not x['verified']]
    status='error' if failed else ('warning' if optional else 'verified')
    summary=('Services AxiorHub actifs, état relu par systemd.' if not failed and not optional else
             (f'{len(failed)} service(s) AxiorHub requis ne sont pas actifs.' if failed else
              f'{len(optional)} service(s) complémentaire(s) ne sont pas actifs.'))
    check=_store_check(desk,'services','services',status,summary,
      {'active':sum(1 for x in rows if x['verified']),'total':len(rows),
       'required_failed':[x['unit'] for x in failed]})
    return {**check,'services':rows}


def _configured_files(config):
    found=set()
    def walk(value,key=''):
        if isinstance(value,dict):
            for child,item in value.items():walk(item,str(child))
        elif isinstance(value,list):
            for item in value:walk(item,key)
        elif isinstance(value,str) and (key.endswith('_file') or key in ('secret_file','password_file')) and value:
            found.add(value)
    walk(config)
    for path in (config.get('_path',''),'/etc/axiorhub-mail-agent/ui-auth.json',
                 '/etc/axiorhub-mail-agent/openwebui-api-token',
                 '/etc/axiorhub-mail-agent/axiorhub_openwebui_tool.py',
                 '/etc/axiorhub-mail-agent/SYSTEM-PROMPT-AXIORHUB.md'):
        if path:found.add(path)
    return sorted(found)


def _optional_files(config):
    """5.6.2 : fichiers dont l'absence est normale (intégration Open WebUI non installée, mises à jour distantes désactivées)."""
    optional={'/etc/axiorhub-mail-agent/openwebui-api-token','/etc/axiorhub-mail-agent/axiorhub_openwebui_tool.py',
              '/etc/axiorhub-mail-agent/SYSTEM-PROMPT-AXIORHUB.md'}
    updates=config.get('updates') or {}
    if not str(updates.get('metadata_url') or '').strip() and updates.get('minisign_public_key_file'):
        optional.add(str(updates['minisign_public_key_file']))
    return optional


def permission_health(desk):
    rows=[];optional=_optional_files(desk.c)
    for name in _configured_files(desk.c):
        path=Path(name);secret=any(x in path.name.casefold() for x in ('secret','password','token','auth'))
        try:
            info=path.lstat();mode=stat.S_IMODE(info.st_mode)
            regular=stat.S_ISREG(info.st_mode) and not path.is_symlink()
            # 0640 is permitted for a root-managed secret shared with the service group.
            protected=(mode & 0o027)==0 if secret else (mode & 0o002)==0
            status='verified' if regular and protected else 'error'
            rows.append({'path':str(path),'status':status,'mode':format(mode,'04o'),
                         'uid':info.st_uid,'gid':info.st_gid,'secret':secret,
                         'reason':'' if status=='verified' else 'type_ou_permissions_inadaptees'})
        except FileNotFoundError:
            if name in optional or str(path) in optional:
                rows.append({'path':str(path),'status':'optional','secret':secret,'reason':'fichier_facultatif_absent'})
            else:
                rows.append({'path':str(path),'status':'error','secret':secret,'reason':'fichier_absent'})
        except PermissionError:
            rows.append({'path':str(path),'status':'unknown','secret':secret,'reason':'permission_stat_refusee'})
    bad=[x for x in rows if x['status'] not in ('verified','optional')]
    status='verified' if rows and not bad else ('error' if any(x['status']=='error' for x in bad) else 'unknown')
    check=_store_check(desk,'permissions','security',status,
      ('Permissions des fichiers relues sans exposer leur contenu.' if status=='verified' else
       f'{len(bad)} fichier(s) absent(s), illisible(s) ou insuffisamment protégé(s).'),
      {'files_checked':len(rows),'issues':len(bad)})
    return {**check,'files':rows}


def index_health(desk):
    path=Path(desk.c['state_dir'])/'documents.sqlite3';threshold=max(1,min(int(
      desk.c.get('reliability393',{}).get('index_fresh_hours',24)),720))
    if not path.is_file():
        return _store_check(desk,'index','index','error','Index documentaire absent.',{'path_present':False})
    try:
        db=sqlite3.connect(path,timeout=5)
        docs=db.execute('SELECT COUNT(*) FROM docs').fetchone()[0]
        chunks=db.execute('SELECT COUNT(*) FROM knowledge_chunks').fetchone()[0]
        stamp=db.execute('SELECT MAX(indexed_at) FROM knowledge_chunks').fetchone()[0]
        incomplete=db.execute('SELECT COUNT(*) FROM inventory_scans').fetchone()[0]
    except sqlite3.Error as error:
        return _store_check(desk,'index','index','error','Lecture de l’index impossible.',
                            {'error':type(error).__name__})
    finally:
        try:db.close()
        except (UnboundLocalError,sqlite3.Error):pass
    parsed=_parse(stamp);age=round((datetime.now(timezone.utc)-parsed).total_seconds()/3600,1) if parsed else None
    status=('verified' if parsed and age<=threshold and incomplete==0 else
            ('warning' if parsed else 'error'))
    summary=(f'Index relu : {docs} document(s), dernière mise à jour il y a {age} h.' if parsed else
             'Index présent mais aucune date d’indexation vérifiable.')
    if incomplete:summary+=f' {incomplete} inventaire(s) incomplet(s).'
    return _store_check(desk,'index','index',status,summary,{
      'documents':docs,'chunks':chunks,'last_indexed_at':stamp or '',
      'age_hours':age,'freshness_limit_hours':threshold,'incomplete_inventories':incomplete})


def ollama_health(desk):
    cfg=desk.c.get('ollama',{});checked=_now()
    try:
        http=HTTP(cfg['url'],local_only=True,timeout=min(int(cfg.get('timeout_seconds',30)),30))
        tags=http.json('GET','/api/tags');running=http.json('GET','/api/ps')
        available=sorted({str(x.get('name') or x.get('model') or '') for x in tags.get('models',[]) if isinstance(x,dict)})
        loaded=sorted({str(x.get('name') or x.get('model') or '') for x in running.get('models',[]) if isinstance(x,dict)})
        selected=set()
        routing=desk.c.get('model_routing',{})
        for role in ('fast','complex','control'):
            provider=str(routing.get(role+'_provider') or 'ollama')
            model=str(routing.get(role+'_model') or '')
            if provider=='ollama' and model:selected.add(model)
        for purpose,route in routing.items():
            if isinstance(route,dict) and str(route.get('provider') or 'ollama')=='ollama' and route.get('model'):
                selected.add(str(route['model']))
        selected.add(str(cfg.get('model') or ''));selected.discard('')
        def present(name,rows):return name in rows or any(x.split(':')[0]==name.split(':')[0] for x in rows)
        missing=sorted(x for x in selected if not present(x,available))
        loaded_selected=sorted(x for x in selected if present(x,loaded))
        status=('error' if missing else ('verified' if loaded_selected else 'warning'))
        summary=(('Ollama répond ; au moins un modèle sélectionné est réellement chargé.'
                  if loaded_selected else
                  'Ollama répond et les modèles sont installés, mais aucun modèle sélectionné '
                  'n’est actuellement chargé en mémoire.') if not missing else
                 'Modèle(s) Ollama sélectionné(s) absent(s) : '+', '.join(missing))
        return _store_check(desk,'ollama','ai',status,summary,{
          'selected_models':sorted(selected),'available_models':available,
          'loaded_models':loaded,'loaded_selected_models':loaded_selected,
          'missing_models':missing,
          'note':'loaded_models provient de /api/ps ; available_models de /api/tags.'},checked)
    except (Stop,KeyError,TypeError,ValueError) as error:
        return _store_check(desk,'ollama','ai','error','Ollama ou son inventaire de modèles est inaccessible.',
                            {'error':str(error)},checked)


def verify_imap_drafts(desk, limit=100):
    checked=_now();state=State(desk.c['state_dir'])
    rows=state.db.execute("SELECT key,draft_mid,updated FROM messages WHERE status='drafted' AND draft_mid<>'' ORDER BY updated DESC LIMIT ?",(max(1,min(int(limit),500)),)).fetchall()
    if not rows:
        return _store_check(desk,'imap_drafts','destinations','warning',
          'Aucun brouillon AxiorHub enregistré à relire dans IMAP.',{'checked':0},checked)
    verified=missing=0;errors=[];box=None
    try:
        box=Mailbox(desk.c['mail'])
        for key,mid,updated in rows:
            try:
                exists=box.find_own_draft(mid);status='verified' if exists else 'missing'
                verified+=int(exists);missing+=int(not exists)
                evidence={'message_id_sha256':hashlib.sha256(mid.encode()).hexdigest(),
                          'state_updated':updated,'folder':desk.c['mail']['drafts']}
            except Stop as error:
                status='error';errors.append(str(error));evidence={'error':str(error)}
            _store_verification(desk,'imap_draft',key,'',status,evidence,checked)
            output_status='verified' if status=='verified' else ('missing' if status=='missing' else 'error')
            desk.db.execute("UPDATE production_outputs_v390 SET status=?,updated=? WHERE output_kind='mail_draft' AND source_id=?",
                            (output_status,checked,key))
            desk.db.execute("UPDATE production_flows_v391 SET status=?,updated=? WHERE job_id IN (SELECT job_id FROM production_outputs_v390 WHERE output_kind='mail_draft' AND source_id=?)",
                            (output_status,checked,key))
    except Stop as error:
        errors.append(str(error))
    finally:
        if box:box.close()
    status='verified' if verified==len(rows) and not errors else 'error'
    summary=(f'{verified} brouillon(s) relu(s) dans IMAP.' if status=='verified' else
             f'{verified} brouillon(s) relu(s), {missing} absent(s), {len(errors)} erreur(s).')
    return _store_check(desk,'imap_drafts','destinations',status,summary,{
      'checked':len(rows),'verified':verified,'missing':missing,
      'errors':errors[:10],'folder':desk.c['mail']['drafts']},checked)


def verify_nextcloud_outputs(desk, limit=100):
    checked=_now();ensure_schema(desk)
    try:
        rows=desk.db.execute("SELECT id,paths,updated FROM production_outputs_v390 WHERE paths<>'[]' AND output_kind<>'mail_draft' ORDER BY updated DESC LIMIT ?",(max(1,min(int(limit),500)),)).fetchall()
    except sqlite3.Error:
        rows=[]
    targets=[]
    for row in rows:
        for path in _json(row['paths'],[]):
            if isinstance(path,str) and path:targets.append((row['id'],path,row['updated']))
    if not targets:
        return _store_check(desk,'nextcloud_outputs','destinations','warning',
          'Aucun fichier produit déclaré à relire dans Nextcloud.',{'checked':0},checked)
    cfg=desk.c.get('nextcloud_documents') or desk.c['nextcloud'];client=DAV(cfg)
    folders={};verified=missing=0;errors=[]
    by_output={}
    for output_id,path,updated in targets:
        parent=str(PurePosixPath(path).parent);parent='/' if parent=='.' else parent
        if parent not in folders:
            try:folders[parent]={x['path']:x for x in client.list_folder(parent)}
            except Stop as error:folders[parent]=error;errors.append(str(error))
        listing=folders[parent]
        if isinstance(listing,Exception):status='error';evidence={'error':str(listing)}
        else:
            item=listing.get(path);status='verified' if item and not item.get('directory') else 'missing'
            verified+=int(status=='verified');missing+=int(status=='missing')
            evidence={'declared_updated':updated}
            if item:evidence.update({'etag_sha256':hashlib.sha256(str(item.get('etag','')).encode()).hexdigest(),
                                     'size':item.get('size',0),'remote_modified':item.get('modified','')})
        _store_verification(desk,'nextcloud_file',output_id,path,status,evidence,checked)
        by_output.setdefault(output_id,[]).append(status)
    for output_id,statuses in by_output.items():
        output_status=('verified' if statuses and all(x=='verified' for x in statuses) else
                       ('missing' if any(x=='missing' for x in statuses) else 'error'))
        desk.db.execute('UPDATE production_outputs_v390 SET status=?,updated=? WHERE id=?',
                        (output_status,checked,output_id))
        desk.db.execute('UPDATE production_flows_v391 SET status=?,updated=? WHERE job_id IN (SELECT job_id FROM production_outputs_v390 WHERE id=?)',
                        (output_status,checked,output_id))
    desk.db.commit()
    status='verified' if verified==len(targets) and not errors else 'error'
    summary=(f'{verified} fichier(s) relu(s) dans Nextcloud.' if status=='verified' else
             f'{verified} fichier(s) relu(s), {missing} absent(s), {len(errors)} erreur(s) DAV.')
    return _store_check(desk,'nextcloud_outputs','destinations',status,summary,{
      'checked':len(targets),'verified':verified,'missing':missing,'errors':errors[:10]},checked)


def test_openrouter(desk):
    """GET /models only: no prompt, filename, party, matter or document is sent."""
    from .ai_gateway import provider_registry,test_provider
    providers=provider_registry(desk.c);rows=[]
    for provider_id,cfg in providers.items():
        if cfg.get('type')!='openrouter' or not cfg.get('enabled',True):continue
        result=test_provider(desk,provider_id)
        rows.append({'provider':provider_id,'status':result.get('status','error'),
                     'latency_ms':result.get('latency_ms'),'error':result.get('error',''),
                     'checked_at':result.get('checked_at','')})
    if not rows:
        return _store_check(desk,'openrouter','ai','warning','Aucun fournisseur OpenRouter actif à tester.',
                            {'data_transmitted':'none','request':'GET /models'})
    status='verified' if all(x['status']=='ok' for x in rows) else 'error'
    return _store_check(desk,'openrouter','ai',status,
      ('OpenRouter répond sans transmission de données de dossier.' if status=='verified' else
       'Au moins une connexion OpenRouter a échoué.'),
      {'providers':rows,'data_transmitted':'none','request':'GET /models'})


def cached_checks(desk):
    ensure_schema(desk);rows=[]
    for row in desk.db.execute('SELECT * FROM reliability_checks_v393 ORDER BY category,check_key'):
        item=dict(row);item['evidence']=_json(item['evidence'],{});item['verified']=item['status']=='verified';rows.append(item)
    return rows


def run_checks(desk, include_external=False):
    """Run bounded live checks. External OpenRouter remains explicit and optional."""
    result={
      'permissions':permission_health(desk),'services':service_health(desk),
      'jobs':job_health(desk),'index':index_health(desk),'ollama':ollama_health(desk),
      'imap_drafts':verify_imap_drafts(desk),'nextcloud_outputs':verify_nextcloud_outputs(desk),
    }
    if include_external:result['openrouter']=test_openrouter(desk)
    desk.audit('system_checks_v393',{'include_external':bool(include_external),
      'statuses':{key:value.get('status') for key,value in result.items()}})
    return result


def status_snapshot(desk):
    """Cheap page snapshot: current local state plus latest destination evidence."""
    return {'permissions':permission_health(desk),'services':service_health(desk),
            'jobs':job_health(desk),'index':index_health(desk),'checks':cached_checks(desk)}

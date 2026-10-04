"""Useful, measured and verifiable production for AxiorHub 4.2.

This layer does not replace the existing producers.  It records their business
outcome, verifies remote deposits and exposes one contract to the UI/API:
verified deliverable, prepared task, explained abstention or retryable error.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import PurePosixPath
import re

from .common import Stop, digest, load_matters


STAGES = (
    ('detected', 'Événement détecté'),
    ('matter_identified', 'Dossier identifié'),
    ('sources_ready', 'Sources rassemblées'),
    ('prepared', 'Projet préparé'),
    ('controlled', 'Contrôle effectué'),
    ('deposited', 'Livrable déposé'),
    ('informed', 'Avocat informé'),
)

JOB_META = {
    'prepare_reply': ('mail_draft', 'Projet de réponse'),
    'analyze_notice440': ('mail_draft', 'Avis de procédure'),
    'prepare_document_project': ('document_project', 'Projet documentaire'),
    'create_document_files': ('nextcloud_document', 'Document Nextcloud'),
    'prepare_hearing': ('hearing_project', 'Préparation d’audience'),
    'create_hearing_files': ('nextcloud_document', 'Dossier d’audience'),
    'prepare_word_project': ('word_project', 'Révision Word'),
    'create_word_files': ('nextcloud_document', 'Nouvelle version Word'),
    'draft_act': ('document_project', 'Projet d’acte'),
    'confirm_task': ('task', 'Tâche préparée'),
    'schedule_work_task': ('deadline', 'Échéance préparée'),
    'confirm_event': ('deadline', 'Événement préparé'),
    'prepare_legal_opinion': ('legal_note', 'Consultation juridique'),
}

STUDIO_TYPES = {
    'email': ('prepare_reply', 'mail_drafting'),
    'letter': ('prepare_document_project', 'document_drafting'),
    'conclusions': ('prepare_document_project', 'document_drafting'),
    'assignation': ('prepare_document_project', 'document_drafting'),
    'contract': ('prepare_document_project', 'document_drafting'),
    'consultation': ('prepare_legal_opinion', 'legal_analysis'),
    'hearing_note': ('prepare_hearing', 'hearing'),
    'exhibit_list': ('prepare_document_project', 'document_drafting'),
    'report': ('prepare_document_project', 'document_drafting'),
    'internal_note': ('prepare_document_project', 'document_drafting'),
}

TRIGGER_POLICY = {
    'actionable_mail': 'Brouillon IMAP vérifié',
    'opponent_writings': 'Synthèse, arguments adverses et pièces manquantes',
    'hearing_j14': 'Note de plaidoirie et dossier d’audience',
    'important_evidence': 'Chronologie, graphe et stratégie actualisés',
    'contract_received': 'Tableau des clauses, risques et propositions',
    'inactive_matter': 'Proposition interne de relance ou clôture',
    'completed_work': 'Proposition de temps et de facturation',
}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS production_deliverables_v420(
      id TEXT PRIMARY KEY, job_id INTEGER NOT NULL, job_kind TEXT NOT NULL,
      event_kind TEXT NOT NULL, source_id TEXT NOT NULL, matter TEXT NOT NULL,
      deliverable_kind TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL,
      stage TEXT NOT NULL, stages TEXT NOT NULL, trigger_text TEXT NOT NULL,
      source_ids TEXT NOT NULL, target TEXT NOT NULL, verification TEXT NOT NULL,
      business_message TEXT NOT NULL, error_code TEXT NOT NULL,
      retryable INTEGER NOT NULL, informed INTEGER NOT NULL DEFAULT 0,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS production_deliverables_state_v420
      ON production_deliverables_v420(status,updated);
    CREATE INDEX IF NOT EXISTS production_deliverables_matter_v420
      ON production_deliverables_v420(matter,updated);
    CREATE TABLE IF NOT EXISTS production_studio_v420(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, deliverable_kind TEXT NOT NULL,
      instruction_sha256 TEXT NOT NULL, source_paths TEXT NOT NULL,
      template_id TEXT NOT NULL, requested_model TEXT NOT NULL,
      confidentiality TEXT NOT NULL, estimate TEXT NOT NULL, status TEXT NOT NULL,
      job_id INTEGER, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS production_metric_snapshots_v420(
      day TEXT PRIMARY KEY, metrics TEXT NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS production_rule_influence_v420(
      deliverable_id TEXT NOT NULL, rule_id INTEGER NOT NULL,
      decision TEXT NOT NULL DEFAULT '', changed_characters INTEGER NOT NULL DEFAULT 0,
      created TEXT NOT NULL, updated TEXT NOT NULL,
      PRIMARY KEY(deliverable_id,rule_id));
    ''')
    desk.db.commit()


def _json(value, default):
    try: return json.loads(value) if value else default
    except (TypeError, ValueError): return default


def _matter(desk, value):
    mid = str(value or '')
    return next((x for x in load_matters(desk.c) if x.get('id') == mid), None)


def _resolve_matter(desk, kind, args, result):
    value = args.get('matter')
    candidate = result.get('matter') if isinstance(result, dict) else None
    if not value and isinstance(candidate, dict): value = candidate.get('id')
    elif not value and candidate: value = candidate
    if value: return str(value)
    project = str(args.get('project_id') or result.get('project_id') or
                  result.get('hearing_project_id') or result.get('word_project_id') or '')
    if not project: return ''
    lookups = {
      'create_document_files': ('document_projects_v220', 'id'),
      'create_hearing_files': ('hearing_projects_v250', 'id'),
      'create_word_files': ('word_projects_v250', 'id'),
    }
    table, key = lookups.get(kind, ('', ''))
    if not table: return ''
    try: row = desk.db.execute('SELECT matter FROM '+table+' WHERE '+key+'=?', (project,)).fetchone()
    except Exception: return ''
    return str(row['matter']) if row else ''


def _paths(result):
    rows = result.get('created_files') or result.get('future_files') or []
    output = []
    for item in rows:
        if isinstance(item, dict):
            path = str(item.get('path') or '')
            sha = str(item.get('sha256') or '')
            size = int(item.get('bytes') or 0)
        else: path, sha, size = str(item), '', 0
        if path and not any(x['path'] == path for x in output):
            output.append({'path': path, 'sha256': sha, 'bytes': size})
    return output[:50]


def _history(matter, status, controlled=False, deposited=False, informed=False):
    done = {'detected', 'sources_ready'}
    if matter: done.add('matter_identified')
    if status not in ('error', 'abstained'): done.add('prepared')
    if controlled: done.add('controlled')
    if deposited: done.add('deposited')
    if informed: done.add('informed')
    error_at = next((key for key, _ in STAGES if key not in done), 'prepared')
    return [{'id': key, 'label': label,
      'status': ('done' if key in done else ('error' if status == 'error' and key == error_at else 'pending'))}
      for key, label in STAGES]


def _business_message(kind, matter, status, result, paths):
    name = matter or 'le cabinet'
    if status == 'verified' and kind == 'prepare_reply':
        verification = result.get('draft_verified') or {}
        folder = verification.get('folder') or result.get('dossier') or 'Drafts'
        at = verification.get('verified_at') or ''
        return 'Projet de réponse préparé pour '+name+'. Brouillon relu dans '+folder+((' à '+at) if at else '')+'.'
    if status == 'verifying':
        return str(len(paths))+' fichier(s) déposé(s) pour '+name+' ; relecture distante en attente.'
    if status == 'verified':
        return str(len(paths))+' fichier(s) relu(s) dans Nextcloud pour '+name+'.'
    if status == 'prepared':
        return JOB_META.get(kind, ('', 'Projet'))[1]+' préparé pour '+name+' ; une décision reste nécessaire avant engagement.'
    if status == 'abstained':
        return str(result.get('message') or 'Aucun livrable fiable ne peut être préparé avec les éléments disponibles.')
    if status == 'error':
        return 'Production interrompue pour '+name+'. La cause est affichée et le traitement peut être relancé.'
    return JOB_META.get(kind, ('', 'Travail interne'))[1]+' enregistré pour '+name+'.'


def record_job(desk, row, args, status, result):
    """Record one and only one business outcome for a relevant technical job."""
    kind = row['kind']
    if kind not in JOB_META: return None
    ensure_schema(desk); result = result if isinstance(result, dict) else {}
    matter = _resolve_matter(desk, kind, args, result); files = _paths(result)
    verified_mail = result.get('brouillon_imap') == 'verifie'
    controlled = bool(result.get('control') or result.get('quality_control') or verified_mail)
    if status != 'done': business_status = 'error'
    elif verified_mail: business_status = 'verified'
    elif kind.startswith('create_') and files: business_status = 'verifying'
    elif kind in ('confirm_task', 'schedule_work_task', 'confirm_event'): business_status = 'verified'
    elif result.get('status') == 'blocked': business_status = 'abstained'
    elif result.get('projet_prepare') or result.get('status') in ('pending', 'prepared') or kind.startswith('prepare_') or kind == 'draft_act':
        business_status = 'prepared'
    else: business_status = 'abstained'
    deposited = verified_mail or (business_status == 'verified' and kind in ('confirm_task','schedule_work_task','confirm_event'))
    history = _history(matter, business_status, controlled, deposited, False)
    current = next((x['id'] for x in reversed(history) if x['status'] == 'done'), 'detected')
    error = str(result.get('erreur') or result.get('error') or '')[:200]
    source = str(args.get('key') or args.get('trigger_mail_key') or
      args.get('project_id') or result.get('project_id') or
      result.get('hearing_project_id') or row['id'])
    source_ids = result.get('source_ids') or args.get('source_ids') or []
    if not isinstance(source_ids, list): source_ids = [str(source_ids)]
    trigger = str(args.get('trigger_reason') or args.get('trigger_kind') or
                  result.get('trigger_reason') or 'Demande ou événement du cabinet')[:1000]
    ident = digest('v420|'+str(row['id'])+'|'+JOB_META[kind][0]+('|'+str(args.get('key','')) if kind=='prepare_reply' else '')); stamp = desk.now()
    old = desk.db.execute('SELECT created,informed FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()
    verification = {'kind': 'imap' if verified_mail else ('nextcloud' if files else 'none'),
      'verified': deposited, 'files': files}
    if verified_mail:
        verification.update({k:(result.get('draft_verified') or {}).get(k) for k in
          ('folder','uid','uidvalidity','verified_at')})
    target = str(result.get('dossier') or (str(PurePosixPath(files[0]['path']).parent) if files else ''))
    message = _business_message(kind, matter, business_status, result, files)
    desk.db.execute('''INSERT OR REPLACE INTO production_deliverables_v420
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
      ident, int(row['id']), kind, str(args.get('trigger_kind') or 'job'), source,
      matter, JOB_META[kind][0], JOB_META[kind][1], business_status, current,
      json.dumps(history,ensure_ascii=False), trigger,
      json.dumps(source_ids[:50],ensure_ascii=False), target,
      json.dumps(verification,ensure_ascii=False), message, error,
      int(business_status == 'error'), int(old['informed']) if old else 0,
      old['created'] if old else stamp, stamp))
    desk.db.commit()
    purpose={'mail_draft':'mail_drafting','hearing_project':'hearing',
      'nextcloud_document':'document_drafting','document_project':'document_drafting',
      'word_project':'word_revision','legal_note':'legal_analysis'}.get(JOB_META[kind][0],'assistant')
    try:
        from .learning410 import applicable_context
        for rule in applicable_context(desk,matter,purpose,record=False).get('rules',[]):
            desk.db.execute('''INSERT OR IGNORE INTO production_rule_influence_v420
              VALUES(?,?,"",0,?,?)''',(ident,rule['id'],stamp,stamp))
        desk.db.commit()
    except (Stop,KeyError):pass
    if business_status == 'verifying':
        active = desk.db.execute("SELECT id FROM jobs WHERE kind='verify_deliverable420' AND status IN ('pending','running') AND args LIKE ? LIMIT 1", ('%'+ident+'%',)).fetchone()
        if not active:
            verify_job = desk.enqueue('verify_deliverable420', {'deliverable_id': ident, 'matter': matter, 'files': files}, priority=10)
            desk.audit('verification_livrable_420_planifiee', {'deliverable_id':ident,'job_id':verify_job,'files':len(files)})
    return ident


def record_review_influence(desk, output_id, decision, changed_characters=0):
    """Attach the lawyer's outcome to rules that actually influenced a deliverable."""
    ensure_schema(desk)
    output=desk.db.execute('SELECT job_id FROM production_outputs_v390 WHERE id=?',(str(output_id),)).fetchone()
    if not output:return
    deliverable=desk.db.execute('SELECT id FROM production_deliverables_v420 WHERE job_id=? ORDER BY updated DESC LIMIT 1',(output['job_id'],)).fetchone()
    if not deliverable:return
    desk.db.execute('UPDATE production_rule_influence_v420 SET decision=?,changed_characters=?,updated=? WHERE deliverable_id=?',
      (str(decision),max(0,int(changed_characters or 0)),desk.now(),deliverable['id']));desk.db.commit()


def verify_deliverable(desk, args, dav=None):
    ensure_schema(desk); ident = str(args.get('deliverable_id') or '')
    row = desk.db.execute('SELECT * FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()
    if not row: raise Stop('livrable_absent')
    files = args.get('files') or _json(row['verification'],{}).get('files',[])
    if not files: raise Stop('livrable_sans_fichier')
    if not _matter(desk, row['matter']): raise Stop('dossier_absent')
    if dav is None:
        from .dav import DAV
        dav = DAV(desk.c.get('nextcloud_documents') or desk.c['nextcloud'])
    checked=[]
    for expected in files:
        path=str(expected.get('path') or ''); parent=str(PurePosixPath(path).parent)
        candidates=dav.list_folder(parent)
        item=next((x for x in candidates if not x.get('directory') and x.get('path')==path),None)
        if not item: raise Stop('fichier_cree_absent_lors_verification')
        raw=dav.download(item); actual=hashlib.sha256(raw).hexdigest()
        if expected.get('sha256') and actual != expected['sha256']:
            raise Stop('fichier_cree_non_conforme')
        if expected.get('bytes') and len(raw) != int(expected['bytes']):
            raise Stop('fichier_cree_non_conforme')
        checked.append({'path':path,'sha256':actual,'bytes':len(raw),'verified_at':desk.now()})
    verification={'kind':'nextcloud','verified':True,'files':checked,'verified_at':desk.now()}
    history=_history(row['matter'],'verified',True,True,True)
    message=str(len(checked))+' fichier(s) relu(s) dans Nextcloud pour '+(row['matter'] or 'le cabinet')+'.'
    desk.db.execute('''UPDATE production_deliverables_v420 SET status='verified',stage='informed',
      stages=?,verification=?,business_message=?,error_code='',retryable=0,informed=1,updated=? WHERE id=?''',
      (json.dumps(history,ensure_ascii=False),json.dumps(verification,ensure_ascii=False),message,desk.now(),ident))
    desk.db.commit();desk.audit('livrable_420_verifie',{'deliverable_id':ident,'files':len(checked),'matter':row['matter']})
    return {'deliverable_id':ident,'status':'verified','files_verified':len(checked),'message':message}


def mark_verification_error(desk, deliverable_id, error):
    row=desk.db.execute('SELECT matter,stages FROM production_deliverables_v420 WHERE id=?',(deliverable_id,)).fetchone()
    if not row:return
    history=_history(row['matter'],'error',True,False,False)
    desk.db.execute("UPDATE production_deliverables_v420 SET status='error',stage='deposited',stages=?,error_code=?,retryable=1,business_message=?,updated=? WHERE id=?",
      (json.dumps(history,ensure_ascii=False),str(error)[:200],
       'Le dépôt n’a pas pu être relu depuis Nextcloud. Vérifiez la connexion puis relancez.',desk.now(),deliverable_id))
    desk.db.commit()


def retry_deliverable(desk, args):
    ensure_schema(desk);ident=str(args.get('deliverable_id') or '')
    row=desk.db.execute('SELECT * FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()
    if not row or row['status']!='error' or not row['retryable']:
        raise Stop('incident_non_rejouable')
    verification=_json(row['verification'],{})
    if verification.get('kind')=='nextcloud' and verification.get('files'):
        job=desk.enqueue('verify_deliverable420',{'deliverable_id':ident,
          'matter':row['matter'],'files':verification['files']},priority=0)
    else:
        original=desk.db.execute('SELECT kind,args FROM jobs WHERE id=?',(row['job_id'],)).fetchone()
        if not original or original['kind'] not in JOB_META:raise Stop('incident_non_rejouable')
        payload=_json(original['args'],{});payload['retry_of']=row['job_id']
        job=desk.enqueue(original['kind'],payload,priority=0)
    desk.db.execute("UPDATE production_deliverables_v420 SET status='retrying',retryable=0,business_message=?,updated=? WHERE id=?",
      ('Reprise demandée ; le résultat sera de nouveau contrôlé.',desk.now(),ident));desk.db.commit()
    return {'job_id':job,'deliverable_id':ident,'status':'queued','message':'Reprise planifiée.'}


def estimate_studio(desk, deliverable_kind, instruction='', source_paths=None, matter=''):
    if deliverable_kind not in STUDIO_TYPES: raise Stop('type_livrable_invalide')
    _, purpose=STUDIO_TYPES[deliverable_kind]; paths=list(source_paths or [])[:50]
    payload={'matter':str(matter or ''),'instruction':str(instruction or ''),
      'documents':[{'path':str(x)} for x in paths]}
    from .hybrid400 import choose
    decision=choose(desk.c,purpose,'document_project' if purpose=='document_drafting' else
      ('hearing_preparation' if purpose=='hearing' else 'chat'),payload=payload,
      metrics={'source_count':len(paths),'document_count':len(paths)},max_tokens=5000)
    return {'purpose':purpose,'provider':decision['provider'],'model':decision['model'],
      'external':decision['external'],'estimated_cost_usd':round(decision['estimated_cost_usd'],6),
      'input_characters':decision['input_characters'],'reason_codes':decision['reason_codes'],
      'confidentiality':'anonymisé avant transmission externe' if decision['external'] else 'traitement local',
      'estimated_minutes':max(1,min(45,2+len(paths)*2+decision['input_characters']//12000))}


def submit_studio(desk, args):
    ensure_schema(desk);kind=str(args.get('deliverable_kind') or '')
    if kind not in STUDIO_TYPES: raise Stop('type_livrable_invalide')
    matter=str(args.get('matter') or ''); instruction=str(args.get('instruction') or '').strip()
    if kind!='email' and not _matter(desk,matter): raise Stop('dossier_requis')
    if not instruction or len(instruction)>20000: raise Stop('consigne_requise_20000_caracteres_maximum')
    raw=args.get('source_paths') or []
    if isinstance(raw,str):raw=[raw]
    expanded=[]
    for value in raw:
        expanded.extend(x.strip() for x in str(value).splitlines() if x.strip())
    sources=list(dict.fromkeys(str(x)[:2000] for x in expanded))[:50]
    estimate=estimate_studio(desk,kind,instruction,sources,matter)
    fingerprint=digest(json.dumps({'matter':matter,'kind':kind,'instruction':instruction,
      'sources':sources,'template':args.get('template_id','')},ensure_ascii=False,sort_keys=True))
    existing=desk.db.execute("SELECT id,job_id,status FROM production_studio_v420 WHERE id=?",(fingerprint,)).fetchone()
    if existing and existing['status'] in ('queued','running','prepared'):
        return {'studio_id':existing['id'],'job_id':existing['job_id'],'status':existing['status'],'idempotent':True,'estimate':estimate}
    job_kind,_=STUDIO_TYPES[kind]
    payload={'matter':matter,'instruction':instruction,'automatic':'yes','trigger_kind':'production_studio420',
      'trigger_reason':'Demande créée dans le Studio de production','source_ids':sources}
    if sources: payload['source_path']=sources[0]
    if kind=='email':
        key=str(args.get('mail_key') or '')
        if not re.fullmatch(r'[a-f0-9]{64}',key): raise Stop('courriel_source_requis')
        payload['key']=key
    elif kind=='letter': payload['document_type']='courrier'
    elif kind=='conclusions': payload['document_type']='conclusions'
    elif kind=='assignation': payload['document_type']='assignation'
    elif kind=='contract': payload['document_type']='contrat'
    elif kind=='exhibit_list': payload['document_type']='bcp'
    elif kind in ('report','internal_note'): payload['document_type']='document'
    elif kind=='consultation': payload['question']=instruction
    elif kind=='hearing_note': payload['objective']=instruction
    job=desk.enqueue(job_kind,payload,priority=0);stamp=desk.now()
    desk.db.execute('''INSERT OR REPLACE INTO production_studio_v420
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(fingerprint,matter,kind,digest(instruction),
      json.dumps(sources,ensure_ascii=False),str(args.get('template_id') or ''),
      str(args.get('requested_model') or 'auto'),estimate['confidentiality'],
      json.dumps(estimate,ensure_ascii=False),'queued',job,stamp,stamp))
    desk.db.commit();desk.audit('studio_420_demande',{'studio_id':fingerprint,'matter':matter,'kind':kind,'job_id':job})
    return {'studio_id':fingerprint,'job_id':job,'status':'queued','estimate':estimate}


def _enqueue_once(desk, kind, args, priority=20):
    raw=json.dumps(args,sort_keys=True)
    row=desk.db.execute("SELECT id FROM jobs WHERE kind=? AND args=? AND status IN ('pending','running') LIMIT 1",(kind,raw)).fetchone()
    return row['id'] if row else desk.enqueue(kind,args,priority=priority)


def _event_already_handled(desk, source_id):
    marker='"trigger_source_id": "'+str(source_id)+'"'
    return bool(desk.db.execute("SELECT 1 FROM jobs WHERE args LIKE ? AND status IN ('pending','running','done') LIMIT 1",('%'+marker+'%',)).fetchone())


def _record_prepared_event(desk, source_id, matter, kind, label, message, source_ids=None):
    """Record an existing reversible business proposal, never a technical success."""
    ensure_schema(desk);ident=digest('v420-event|'+kind+'|'+str(source_id));stamp=desk.now()
    history=_history(matter,'prepared',False,False,False)
    old=desk.db.execute('SELECT created FROM production_deliverables_v420 WHERE id=?',(ident,)).fetchone()
    desk.db.execute('''INSERT OR REPLACE INTO production_deliverables_v420
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(ident,0,'event_outcome420',kind,
      str(source_id),str(matter or ''),'task',label,'prepared','prepared',
      json.dumps(history,ensure_ascii=False),message,json.dumps(list(source_ids or [])[:50],ensure_ascii=False),
      '',json.dumps({'kind':'local_register','verified':True},ensure_ascii=False),message,'',0,0,
      old['created'] if old else stamp,stamp));desk.db.commit();return ident


def trigger_events(desk, limit=30):
    """Turn recognised cabinet events into idempotent internal, reversible work."""
    ensure_schema(desk);limit=max(1,min(int(limit),100));queued=[];prepared=[];abstained=[]
    signals=list(desk.db.execute("SELECT * FROM proactive_signals WHERE state='open' AND matter<>'' ORDER BY last_seen DESC LIMIT ?",(limit*3,)))
    for row in signals:
        if _event_already_handled(desk,row['id']):continue
        text=' '.join((row['title'],row['detail'],row['proposed_action'])).casefold()
        playbook_active=bool(desk.db.execute("SELECT 1 FROM production_triggers_v390 WHERE source_id=? AND status IN ('running','done') LIMIT 1",(row['id'],)).fetchone())
        common={'matter':row['matter'],'automatic':'yes','trigger_kind':row['category'],
          'trigger_source_id':row['id'],'trigger_reason':row['title'],
          'source_ids':_json(row['source_ids'],[])}
        kind='';payload={}
        if row['category']=='documents_changed' and not any(x in text for x in ('conclusion','écriture','assignation','contrat','cgv','clause','avenant')):
            kind='advance_matter420';payload={'matter':row['matter'],'trigger_source_id':row['id']}
        elif row['category']=='documents_changed' and any(x in text for x in ('contrat','cgv','clause','avenant')):
            kind='prepare_document_project';payload={**common,'document_type':'contrat',
              'instruction':'Produire un tableau des clauses, risques, déséquilibres et propositions de rédaction, avec renvoi aux sources.'}
        elif row['category']=='documents_changed' and any(x in text for x in ('conclusion','écriture','assignation')):
            if playbook_active:continue
            kind='prepare_document_project';payload={**common,'document_type':'conclusions',
              'instruction':'Synthétiser les prétentions et arguments adverses, relever les contradictions et pièces manquantes, puis préparer un projet interne de réponse.'}
        elif row['category'] in ('upcoming_appointment','upcoming_deadline') and any(x in text for x in ('audience','plaidoirie','tribunal','cour ')):
            if playbook_active:continue
            kind='prepare_hearing';payload={**common,'objective':'Préparer la note de plaidoirie, les arguments, réponses adverses et la liste des pièces pour l’audience à venir.'}
        if kind:
            queued.append(_enqueue_once(desk,kind,payload,5))
        if len(queued)>=limit:break
    try:
        decisions=list(desk.db.execute("""SELECT * FROM cabinet_decisions_v290
          WHERE status='pending' AND action_kind IN ('review_inactive_matter','review_billing','review_unbilled_work')
          ORDER BY updated DESC LIMIT ?""",(limit,)))
    except Exception:decisions=[]
    for row in decisions:
        if row['action_kind']=='review_inactive_matter' and row['matter'] and not _event_already_handled(desk,row['id']):
            payload={'matter':row['matter'],'document_type':'note_interne','automatic':'yes',
              'trigger_kind':'inactive_matter','trigger_source_id':row['id'],'trigger_reason':row['title'],
              'instruction':'Analyser l’inactivité du dossier et préparer deux options internes : relance utile ou clôture, avec les vérifications et décisions nécessaires.'}
            queued.append(_enqueue_once(desk,'prepare_document_project',payload,15))
        elif row['action_kind'] in ('review_billing','review_unbilled_work'):
            prepared.append(_record_prepared_event(desk,row['id'],row['matter'],'completed_work',
              'Temps et facturation à contrôler',row['title']+' — '+row['summary'],[row['source_id']]))
    return {'queued':len(queued),'prepared':len(prepared),'abstained':abstained,
      'policy':TRIGGER_POLICY,'job_ids':queued[:50]}


def advance_matter(desk, args):
    mid=str(args.get('matter') or '');matter=_matter(desk,mid)
    if not matter:raise Stop('dossier_absent')
    jobs=[]
    jobs.append(_enqueue_once(desk,'index',{'matter':mid},15))
    jobs.append(_enqueue_once(desk,'refresh_brief',{'matter':mid},20))
    jobs.append(_enqueue_once(desk,'refresh_matter_graph380',{'matter':mid},25))
    signals=list(desk.db.execute("SELECT * FROM proactive_signals WHERE matter=? AND state='open' ORDER BY due,last_seen DESC LIMIT 30",(mid,)))
    hearing=next((x for x in signals if x['category'] in ('upcoming_appointment','upcoming_deadline') and re.search(r'audience|plaidoirie|tribunal|cour',x['title']+' '+x['detail'],re.I)),None)
    changed=next((x for x in signals if x['category']=='documents_changed' and re.search(r'conclusion|écriture|assignation|contrat',x['title']+' '+x['detail'],re.I)),None)
    if hearing:
        jobs.append(_enqueue_once(desk,'prepare_hearing',{'matter':mid,'objective':'Préparer les travaux internes utiles pour l’audience.','automatic':'yes','trigger_kind':'audience_j14','trigger_reason':hearing['title']},5))
    elif changed:
        dtype='contrat' if re.search(r'contrat',changed['title']+' '+changed['detail'],re.I) else 'conclusions'
        jobs.append(_enqueue_once(desk,'prepare_document_project',{'matter':mid,'document_type':dtype,'instruction':'Analyser le nouveau document, actualiser les arguments et préparer le projet interne utile.','automatic':'yes','trigger_kind':'nouveau_document','trigger_reason':changed['title']},5))
    recommendation=('Préparation d’audience lancée.' if hearing else
      ('Analyse documentaire et projet interne lancés.' if changed else 'Index, résumé et graphe arguments–preuves lancés.'))
    desk.audit('faire_avancer_dossier_420',{'matter':mid,'jobs':jobs,'external_action':False})
    return {'matter':mid,'jobs':jobs,'message':recommendation,
      'external_actions':0,'email_sent':False,'rpva_filed':False,'signature_added':False}


def _review_metrics(desk, since):
    rows=list(desk.db.execute("SELECT decision,changed_characters FROM correction_events_v410 WHERE created>=?",(since,)))
    accepted=sum(x['decision']=='accepted' for x in rows);modified=sum(x['decision']=='modified' for x in rows)
    rejected=sum(x['decision']=='rejected' for x in rows);total=len(rows)
    return {'reviewed':total,'accepted_without_major_change_percent':round(100*accepted/total,1) if total else 0,
      'average_changed_characters':round(sum(int(x['changed_characters'] or 0) for x in rows)/max(1,modified),1),
      'rejected':rejected}


def metrics(desk, days=30):
    ensure_schema(desk);days=max(1,min(int(days),365));since=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
    status={x['status']:x['count'] for x in desk.db.execute("SELECT status,COUNT(*) count FROM production_deliverables_v420 WHERE updated>=? GROUP BY status",(since,))}
    actionable=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state IN ('needs_action','needs_confirmation','drafting','draft_ready') AND updated>=?",(since,)).fetchone()[0]
    mail_verified=desk.db.execute("SELECT COUNT(*) FROM production_deliverables_v420 WHERE deliverable_kind='mail_draft' AND status='verified' AND updated>=?",(since,)).fetchone()[0]
    linked=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state IN ('needs_action','needs_confirmation','drafting','draft_ready') AND matter<>'' AND updated>=?",(since,)).fetchone()[0]
    official_total=desk.db.execute("SELECT COUNT(*) FROM legal_authorities_v240 WHERE retrieved>=?",(since,)).fetchone()[0]
    official_verified=desk.db.execute("SELECT COUNT(*) FROM legal_authorities_v240 WHERE verification_status='verified' AND retrieved>=?",(since,)).fetchone()[0]
    try:cost=float(desk.db.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM ai_usage_v391 WHERE at>=? AND status='done'",(since,)).fetchone()[0])
    except Exception:cost=0.0
    cost_by_purpose=[];cost_by_matter=[]
    try:
        cost_by_purpose=[dict(x) for x in desk.db.execute('''SELECT purpose,
          ROUND(SUM(estimated_cost_usd),6) cost_usd,COUNT(*) requests
          FROM hybrid_decisions_v400 WHERE at>=? AND external=1
          AND status='external_completed' GROUP BY purpose ORDER BY cost_usd DESC''',(since,))]
        known={hashlib.sha256(str(m['id']).encode()).hexdigest()[:16]:m for m in load_matters(desk.c)}
        totals={}
        for row in desk.db.execute('''SELECT matter_hashes,estimated_cost_usd FROM hybrid_decisions_v400
          WHERE at>=? AND external=1 AND status='external_completed' ''',(since,)):
            hashes=_json(row['matter_hashes'],[])
            if not hashes:continue
            share=float(row['estimated_cost_usd'] or 0)/len(hashes)
            for matter_hash in hashes:
                matter=known.get(matter_hash)
                label=(matter.get('client_name') or matter.get('id')) if matter else 'Dossier anonymisé'
                totals[label]=totals.get(label,0)+share
        cost_by_matter=[{'matter':key,'cost_usd':round(value,6)} for key,value in
          sorted(totals.items(),key=lambda item:item[1],reverse=True)]
    except Exception:
        cost_by_purpose=[];cost_by_matter=[]
    time_weights={'mail_draft':8,'nextcloud_document':45,'hearing_project':60,'document_project':35,'word_project':30,'legal_note':45,'task':3,'deadline':3}
    saved=0
    for row in desk.db.execute("SELECT deliverable_kind,COUNT(*) count FROM production_deliverables_v420 WHERE status='verified' AND updated>=? GROUP BY deliverable_kind",(since,)):
        saved+=time_weights.get(row['deliverable_kind'],5)*row['count']
    reviews=_review_metrics(desk,since)
    result={'period_days':days,'deliverables':status,'actionable_emails':actionable,
      'verified_imap_drafts':mail_verified,
      'actionable_mail_coverage_percent':round(100*mail_verified/max(1,actionable),1),
      'matter_link_rate_percent':round(100*linked/max(1,actionable),1),
      'official_citation_verification_percent':round(100*official_verified/max(1,official_total),1),
      'estimated_minutes_saved':saved,'openrouter_cost_usd':round(cost,6),
      'openrouter_cost_by_purpose':cost_by_purpose,
      'openrouter_cost_by_matter':cost_by_matter,
      'failed_or_blocked':status.get('error',0)+status.get('abstained',0),**reviews}
    day=datetime.now(timezone.utc).date().isoformat()
    desk.db.execute('INSERT OR REPLACE INTO production_metric_snapshots_v420 VALUES(?,?,?)',(day,json.dumps(result,ensure_ascii=False),desk.now()));desk.db.commit()
    return result


def dashboard(desk, days=30):
    from .improvements36 import matter_option
    ensure_schema(desk);stats=metrics(desk,days);names={m['id']:matter_option(m) for m in load_matters(desk.c)}
    rows=[]
    for row in desk.db.execute('SELECT * FROM production_deliverables_v420 ORDER BY updated DESC LIMIT 160'):
        item=dict(row);item['stages']=_json(item['stages'],[]);item['source_ids']=_json(item['source_ids'],[]);item['verification']=_json(item['verification'],{});item['matter_name']=names.get(item['matter'],item['matter'] or 'Cabinet')
        output=desk.db.execute('SELECT id FROM production_outputs_v390 WHERE job_id=? ORDER BY updated DESC LIMIT 1',(item['job_id'],)).fetchone()
        item['output_id']=output['id'] if output else ''
        rows.append(item)
    return {'metrics':stats,'deliverables':rows,
      'ready':[x for x in rows if x['status']=='verified'],
      'decisions':[x for x in rows if x['status'] in ('prepared','abstained')],
      'incidents':[x for x in rows if x['status']=='error'],
      'verifying':[x for x in rows if x['status']=='verifying'],
      'safety':'Tout travail interne et réversible est préparé ; envoi, dépôt, signature et facturation définitive restent soumis à validation.'}


def perform(desk, kind, args):
    if kind=='verify_deliverable420': return verify_deliverable(desk,args)
    if kind=='retry_deliverable420': return retry_deliverable(desk,args)
    if kind=='studio_prepare420': return submit_studio(desk,args)
    if kind=='advance_matter420': return advance_matter(desk,args)
    if kind=='snapshot_metrics420': return metrics(desk,args.get('days',30))
    raise Stop('action_production_420_inconnue')

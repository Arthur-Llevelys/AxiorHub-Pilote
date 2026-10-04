"""Cabinet operating layer for AxiorHub 3.9.0.

This module composes existing 3.7 services.  It never sends mail, files an act,
signs, pays, or calls an external site by itself.  It turns existing signals,
projects and evidence into an actionable view, source-bound matter graph,
reviewable playbook runs, ecosystem envelopes, and reproducible evaluations.
"""
from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

from .common import Stop, digest, load_matters


BUCKETS = ('urgent', 'ready', 'decision', 'done')
REVIEW_STATES = ('seen', 'dismissed', 'snoozed', 'restored')
RELATIONS = ('supports', 'contradicts', 'refutes', 'depends_on', 'missing')
RUN_STATES = ('running', 'awaiting_input', 'done', 'error', 'cancelled')
CAPABILITIES = {
    'calculate_deadline': 'Calculer un délai',
    'prepare_commercial_lease': 'Préparer un parcours bail commercial',
    'prepare_payment_order': 'Préparer une injonction de payer',
    'prepare_fee_proposal': 'Préparer honoraires ou convention',
    'record_expense': 'Préparer une note de frais',
    'prepare_exhibits': 'Numéroter et préparer des pièces PDF',
    'prepare_digital_audit': 'Préparer un audit numérique',
    'prepare_invoice': 'Préparer une facture',
    'store_document': 'Déposer un document',
    'edit_document': 'Éditer un document bureautique',
    'sync_matter': 'Synchroniser les données d’un dossier',
    'record_time': 'Préparer une diligence ou un temps',
}

ECOSYSTEM_CATALOG = ({
  'id':'lexdelai','name':'LexDélai','base_url':'https://lexdelai.fr',
  'capabilities':['calculate_deadline','sync_matter']},
 {'id':'honorium','name':'Honorium','base_url':'https://honorium.fr',
  'capabilities':['prepare_fee_proposal','record_time']},
 {'id':'signaturepdf','name':'SignaturePDF','base_url':'',
  'capabilities':['prepare_exhibits','store_document']},
 {'id':'injonctiondepayerpro','name':'Injonction de payer Pro','base_url':'https://injonctiondepayerpro.fr',
  'capabilities':['prepare_payment_order','sync_matter']},
 {'id':'expenses','name':'Notes de frais','base_url':'https://frais.axiorhub.com',
  'capabilities':['record_expense']},
 {'id':'invoiceninja','name':'Invoice Ninja','base_url':'',
  'capabilities':['prepare_invoice','record_time']},
 {'id':'digital_audit','name':'Audit numérique','base_url':'',
  'capabilities':['prepare_digital_audit','sync_matter']},
 {'id':'nextcloud','name':'Nextcloud','base_url':'',
  'capabilities':['store_document','sync_matter']},
 {'id':'onlyoffice','name':'OnlyOffice','base_url':'',
  'capabilities':['edit_document','store_document']},
)

# Only outcomes that produce a user-visible legal work product belong in the
# "Réalisé" column. Queue housekeeping, reviews of cards, health checks and
# synchronisation jobs remain available in the audit/administration screen.
VISIBLE_COMPLETIONS = {
    'prepare_reply':'Réponse préparée et déposée dans Brouillons',
    'deposit_draft':'Brouillon déposé et vérifié dans la messagerie',
    'create_document_files':'Document créé dans le dossier',
    'prepare_document_project':'Projet de document préparé',
    'prepare_hearing':'Préparation d’audience produite',
    'create_hearing_files':'Documents d’audience créés',
    'prepare_word_project':'Révision Word préparée',
    'create_word_files':'Nouvelle version Word créée',
    'draft_act':'Projet d’acte préparé',
    'analyze_strategy':'Analyse stratégique préparée',
    'build_matrix':'Matrice arguments–preuves actualisée',
    'prepare_legal_opinion':'Avis juridique préparé',
    'coach_hearing35':'Coaching de plaidoirie préparé',
    'prepare_call35':'Appel préparé',
    'record_call35':'Compte rendu d’appel préparé',
    'billing_review35':'Facturation préparée',
}


PLAYBOOKS = ({
  'id': 'litigation_hearing_v1', 'name': 'Préparer une audience contentieuse',
  'family': 'contentieux', 'description': 'Actualise les sources, écritures, pièces et matrice avant la préparation supervisée de la plaidoirie.',
  'steps': [
    {'code':'monitor','label':'Actualiser le dossier','job_kind':'monitor_matter'},
    {'code':'writings','label':'Identifier les dernières conclusions','job_kind':'identify_latest_writings','args':{'document_type':'conclusions'}},
    {'code':'exhibits','label':'Vérifier le registre des pièces','job_kind':'refresh_exhibit_registry'},
    {'code':'matrix','label':'Construire la matrice arguments–preuves','job_kind':'build_matrix'},
    {'code':'graph','label':'Actualiser le graphe du dossier','job_kind':'refresh_matter_graph380'},
    {'code':'hearing','label':'Préparer automatiquement la note et les plans de plaidoirie',
     'job_kind':'prepare_hearing','args':{'automatic':'yes'}},
  ]},
 {
  'id':'opponent_writings_v1','name':'Analyser des conclusions adverses',
  'family':'contentieux','description':'Repère les écritures pertinentes, leurs dispositifs, les preuves, les contradictions et les recherches à compléter.',
  'steps':[
    {'code':'writings','label':'Identifier les écritures des parties','job_kind':'identify_party_writings'},
    {'code':'matrix','label':'Construire la matrice contradictoire','job_kind':'build_matrix'},
    {'code':'graph','label':'Relier arguments et preuves','job_kind':'refresh_matter_graph380'},
    {'code':'draft','label':'Préparer un projet de conclusions en réponse',
     'job_kind':'prepare_document_project','args':{'document_type':'conclusions','automatic':'yes'}},
  ]},
 {
  'id':'contract_review_v1','name':'Analyser ou réviser un contrat',
  'family':'contrats','description':'Indexe les documents, structure les obligations et prépare une révision Word sans écraser la source.',
  'steps':[
    {'code':'index','label':'Actualiser les documents du dossier','job_kind':'index'},
    {'code':'graph','label':'Structurer obligations, risques et sources','job_kind':'refresh_matter_graph380'},
    {'code':'revision','label':'Choisir le contrat et préparer la révision','manual':True,'target':'/audiences-word'},
  ]},
 {
  'id':'formal_notice_v1','name':'Préparer une mise en demeure',
  'family':'contrats','description':'Vérifie les faits et pièces puis prépare un projet interne de mise en demeure.',
  'steps':[
    {'code':'index','label':'Actualiser les documents','job_kind':'index'},
    {'code':'graph','label':'Contrôler faits, obligations et preuves','job_kind':'refresh_matter_graph380'},
    {'code':'draft','label':'Préparer le projet de mise en demeure','job_kind':'draft_act','args':{'act_type':'mise_en_demeure'}},
  ]},
)


def _json(value, default):
    try:return json.loads(value) if value else default
    except (ValueError, TypeError):return default


def _text(value, maximum=4000, required=False):
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or '')).strip()
    if (required and not value) or len(value)>maximum:raise Stop('texte_cabinet_operant_invalide')
    return value


def _matter(desk, mid):
    found=next((m for m in load_matters(desk.c) if m['id']==str(mid or '')),None)
    if not found:raise Stop('dossier_absent')
    return found


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS action_reviews_v380(
      action_id TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT NOT NULL,
      snoozed_until TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS matter_graph_nodes_v380(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, node_kind TEXT NOT NULL,
      title TEXT NOT NULL, detail TEXT NOT NULL, status TEXT NOT NULL,
      confidence REAL NOT NULL, source_ids TEXT NOT NULL, source_path TEXT NOT NULL,
      source_page INTEGER, origin TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS matter_graph_edges_v380(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, from_id TEXT NOT NULL,
      relation TEXT NOT NULL, to_id TEXT NOT NULL, explanation TEXT NOT NULL,
      confidence REAL NOT NULL, origin TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playbook_definitions_v380(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, family TEXT NOT NULL,
      version INTEGER NOT NULL, description TEXT NOT NULL, steps TEXT NOT NULL,
      enabled INTEGER NOT NULL, built_in INTEGER NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playbook_runs_v380(
      id TEXT PRIMARY KEY, playbook_id TEXT NOT NULL, matter TEXT NOT NULL,
      objective TEXT NOT NULL, status TEXT NOT NULL, current_step INTEGER NOT NULL,
      context TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS playbook_steps_v380(
      run_id TEXT NOT NULL, step_no INTEGER NOT NULL, code TEXT NOT NULL,
      label TEXT NOT NULL, status TEXT NOT NULL, job_id INTEGER,
      result TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(run_id,step_no));
    CREATE TABLE IF NOT EXISTS ecosystem_services_v380(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
      capabilities TEXT NOT NULL, secret_ref TEXT NOT NULL,
      enabled INTEGER NOT NULL, reviewed INTEGER NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ecosystem_events_v380(
      id TEXT PRIMARY KEY, service_id TEXT NOT NULL, matter TEXT NOT NULL,
      capability TEXT NOT NULL, payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
      status TEXT NOT NULL, result TEXT NOT NULL, created TEXT NOT NULL,
      finished TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS evaluation_cases_v380(
      id TEXT PRIMARY KEY, name TEXT NOT NULL, family TEXT NOT NULL,
      specification TEXT NOT NULL, enabled INTEGER NOT NULL, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS evaluation_runs_v380(
      id TEXT PRIMARY KEY, status TEXT NOT NULL, score REAL NOT NULL,
      results TEXT NOT NULL, metrics TEXT NOT NULL, created TEXT NOT NULL,
      finished TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS graph_nodes_matter_v380 ON matter_graph_nodes_v380(matter,node_kind,status);
    CREATE INDEX IF NOT EXISTS graph_edges_matter_v380 ON matter_graph_edges_v380(matter,relation);
    CREATE INDEX IF NOT EXISTS playbook_runs_matter_v380 ON playbook_runs_v380(matter,updated DESC);
    CREATE INDEX IF NOT EXISTS ecosystem_events_status_v380 ON ecosystem_events_v380(status,created);
    ''')
    stamp=desk.now()
    for item in PLAYBOOKS:
        desk.db.execute('''INSERT INTO playbook_definitions_v380
          VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,family=excluded.family,version=excluded.version,
          description=excluded.description,steps=excluded.steps,built_in=1,updated=excluded.updated''',
          (item['id'],item['name'],item['family'],2,item['description'],
          json.dumps(item['steps'],ensure_ascii=False),1,1,stamp))
    evals=(
      ('action_contract','Centre d’action structuré','workflow',{'requires':['id','bucket','title','reason','action_label']}),
      ('graph_provenance','Provenance du graphe','evidence',{'source_required_for':['fact','claim','argument','evidence']}),
      ('playbook_safety','Sécurité des playbooks','workflow',{'forbidden_jobs':['deposit_draft','approve_cabinet_confirmation_batch']}),
      ('ecosystem_boundary','Frontière des connecteurs','security',{'external_calls':'connector_only','secrets_in_payload':False}),
      ('quality_control','Contrôle indépendant','quality',{'second_model_required':True}),
    )
    for eid,name,family,spec in evals:
        desk.db.execute('INSERT OR IGNORE INTO evaluation_cases_v380 VALUES(?,?,?,?,?,?)',
          (eid,name,family,json.dumps(spec,ensure_ascii=False),1,stamp))
    desk.db.commit()


def _review_map(desk):
    now=datetime.now(timezone.utc)
    result={}
    for row in desk.db.execute('SELECT * FROM action_reviews_v380'):
        if row['state']=='snoozed':
            try:until=datetime.fromisoformat(row['snoozed_until'])
            except (ValueError,TypeError):until=now
            if not until.tzinfo:until=until.replace(tzinfo=timezone.utc)
            if until<=now:continue
        result[row['action_id']]=dict(row)
    return result


def action_center(desk, limit=160):
    from .improvements36 import matter_option
    ensure_schema(desk);matters={m['id']:m for m in load_matters(desk.c)};cards=[]
    def add(bucket,identity,matter,title,reason,action_label,href,created='',confidence=0,risk='normal',sources=None,due=''):
        aid=digest('|'.join([bucket,identity,matter,title]))
        cards.append({'id':aid,'bucket':bucket,'matter':matter,
          'matter_name':matter_option(matters[matter]) if matter in matters else matter or 'Cabinet',
          'title':title,'reason':reason,'action_label':action_label,'href':href,
          'due':str(due or ''),
          'created':created,'confidence':max(0,min(int(confidence or 0),100)),
          'risk':risk,'source_ids':list(sources or [])[:20]})
    for r in desk.db.execute("SELECT * FROM proactive_signals WHERE state='open' ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,due,last_seen LIMIT 60"):
        add('urgent','signal:'+r['id'],r['matter'],r['title'],r['detail'],
          'Ouvrir le dossier','/matter?id='+r['matter'],r['last_seen'],95 if r['severity']=='critical' else 80,
          r['severity'],_json(r['source_ids'],[]),r['due'])
    for r in desk.db.execute("SELECT mail_key,state,matter,subject,source_reason,updated FROM work_items WHERE state IN ('draft_ready','needs_action') ORDER BY updated DESC LIMIT 50"):
        bucket='ready' if r['state']=='draft_ready' else 'urgent'
        add(bucket,'mail:'+r['mail_key'],r['matter'],r['subject'] or 'Courriel à examiner',
          'État : '+r['state']+' · '+(r['source_reason'] or 'traitement du courriel'),
          'Ouvrir le courriel','/mail?key='+r['mail_key'],r['updated'],85 if bucket=='ready' else 70,
          'normal',[r['mail_key']])
    for r in desk.db.execute("SELECT id,matter,document_type,status,created FROM document_projects_v220 WHERE status IN ('pending','blocked') ORDER BY created DESC LIMIT 30"):
        add('ready' if r['status']=='pending' else 'decision','document:'+r['id'],r['matter'],
          'Projet '+r['document_type'],'Projet prêt à relire.' if r['status']=='pending' else 'Contrôle bloquant à examiner.',
          'Prévisualiser','/projets?project='+r['id'],r['created'],90 if r['status']=='pending' else 55,
          'normal' if r['status']=='pending' else 'high',[r['id']])
    for r in desk.db.execute("SELECT id,matter,title,description,updated FROM diligence_proposals_v230 WHERE status='pending' ORDER BY updated DESC LIMIT 30"):
        add('decision','diligence:'+r['id'],r['matter'],r['title'],r['description'],
          'Examiner la proposition','/projets',r['updated'],65,'normal',[r['id']])
    for r in desk.db.execute("SELECT matter,state,updated FROM matter_portfolio WHERE state='to_confirm' ORDER BY updated DESC LIMIT 30"):
        add('decision','matter:'+r['matter'],r['matter'],'État du dossier à confirmer',
          'Le classement actif, en sommeil ou archivé attend une décision.',
          'Vérifier le dossier','/matter?id='+r['matter'],r['updated'],70,'normal',[r['matter']])
    for r in desk.db.execute("SELECT id,kind,result,finished FROM jobs WHERE status='done' AND finished<>'' ORDER BY id DESC LIMIT 120"):
        if r['kind'] not in VISIBLE_COMPLETIONS:continue
        result=_json(r['result'],{})
        if not isinstance(result,dict):result={}
        label=VISIBLE_COMPLETIONS[r['kind']]
        detail=result.get('message') or 'Le résultat est disponible pour contrôle dans son écran métier.'
        add('done','job:'+str(r['id']),'',label,detail,
          'Ouvrir le résultat','/administration?job='+str(r['id']),r['finished'],100,'low',[str(r['id'])])
    reviews=_review_map(desk)
    cards=[c for c in cards if c['id'] not in reviews]
    rank={'urgent':0,'ready':1,'decision':2,'done':3};risk={'critical':0,'high':1,'normal':2,'low':3}
    cards.sort(key=lambda c:c['created'],reverse=True)
    cards.sort(key=lambda c:(rank[c['bucket']],risk.get(c['risk'],2)))
    counts={key:sum(c['bucket']==key for c in cards) for key in BUCKETS}
    return {'counts':counts,'cards':cards[:max(1,min(int(limit),300))],
      'generated_at':desk.now(),'warning':'Les propositions restent soumises aux politiques d’autorisation et aux sources originales.'}


def review_action(desk, action_id, state, note='', snoozed_until=''):
    ensure_schema(desk);action_id=str(action_id or '')
    if not re.fullmatch(r'[a-f0-9]{64}',action_id):raise Stop('action_cabinet_invalide')
    if state not in REVIEW_STATES:raise Stop('decision_action_invalide')
    if state=='restored':
        desk.db.execute('DELETE FROM action_reviews_v380 WHERE action_id=?',(action_id,))
        desk.db.commit();desk.audit('action_center_restored',{'action_id':action_id})
        return {'action_id':action_id,'state':state}
    if state=='snoozed':
        try:datetime.fromisoformat(str(snoozed_until))
        except ValueError:raise Stop('date_report_invalide') from None
    else:snoozed_until=''
    desk.db.execute('INSERT OR REPLACE INTO action_reviews_v380 VALUES(?,?,?,?,?)',
      (action_id,state,_text(note,1000),str(snoozed_until),desk.now()))
    desk.db.commit();desk.audit('action_center_reviewed',{'action_id':action_id,'state':state})
    return {'action_id':action_id,'state':state}


def _node(desk, mid, kind, native_id, title, detail, status, confidence, source_ids, path='', page=None, origin='generated'):
    nid=digest('|'.join([mid,kind,str(native_id)]));stamp=desk.now()
    desk.db.execute('''INSERT OR REPLACE INTO matter_graph_nodes_v380
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(nid,mid,kind,_text(title,500),_text(detail,8000),
      _text(status,50) or 'suggested',float(confidence or 0),json.dumps(list(source_ids or [])[:40],ensure_ascii=False),
      _text(path,2000),int(page) if page not in (None,'') else None,origin,stamp))
    return nid


def _edge(desk, mid, left, relation, right, explanation='', confidence=0, origin='generated'):
    if relation not in RELATIONS:raise Stop('relation_graphe_invalide')
    eid=digest('|'.join([mid,left,relation,right]));desk.db.execute('''INSERT OR REPLACE INTO matter_graph_edges_v380
      VALUES(?,?,?,?,?,?,?,?,?)''',(eid,mid,left,relation,right,_text(explanation,2000),
      float(confidence or 0),origin,desk.now()));return eid


def refresh_matter_graph(desk, mid):
    ensure_schema(desk);_matter(desk,mid)
    desk.db.execute("DELETE FROM matter_graph_edges_v380 WHERE matter=? AND origin='generated'",(mid,))
    desk.db.execute("DELETE FROM matter_graph_nodes_v380 WHERE matter=? AND origin='generated'",(mid,))
    source_nodes={}
    def source_node(source, label='Source'):
        sid=str(source or '')
        if sid not in source_nodes:
            source_nodes[sid]=_node(desk,mid,'evidence',sid,label,sid,'available',1,[sid],sid,origin='generated')
        return source_nodes[sid]
    record_nodes={}
    for r in desk.db.execute("SELECT * FROM legal_memory_records WHERE matter=? AND status<>'archived' ORDER BY updated",(mid,)):
        sources=_json(r['sources'],[]);kind='claim' if r['record_type']=='claim' else ('question' if r['record_type']=='open_question' else 'fact')
        nid=_node(desk,mid,kind,r['id'],r['title'],r['content'],r['status'],r['confidence'],sources,origin='generated')
        record_nodes[r['id']]=nid
        for sid in sources:_edge(desk,mid,source_node(sid), 'supports',nid,'Source déclarée dans la mémoire juridique.',r['confidence'])
    latest=desk.db.execute('SELECT id FROM matrix_runs WHERE matter=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
    if latest:
        for r in desk.db.execute('SELECT * FROM evidence_matrix_rows WHERE run_id=? ORDER BY position',(latest['id'],)):
            supporting=_json(r['supporting_sources'],[]);contradicting=_json(r['contradicting_sources'],[])
            sources=supporting+contradicting+_json(r['neutral_sources'],[])
            nid=_node(desk,mid,'argument',r['id'],r['proposition'],r['strategic_use'],r['status'],0.75,sources,origin='generated')
            for sid in supporting:_edge(desk,mid,source_node(sid), 'supports',nid,'Source indiquée comme favorable.',0.75)
            for sid in contradicting:_edge(desk,mid,source_node(sid), 'contradicts',nid,'Source indiquée comme contradictoire.',0.75)
            if _json(r['missing_evidence'],[]):
                missing=_node(desk,mid,'missing',r['id']+':missing','Preuves manquantes',
                  ' · '.join(_json(r['missing_evidence'],[])),'open',0,[],origin='generated')
                _edge(desk,mid,nid,'missing',missing,'Preuves signalées comme absentes.',1)
    for r in desk.db.execute("SELECT * FROM legal_memory_conflicts WHERE matter=? AND status='open'",(mid,)):
        left=record_nodes.get(r['left_record'],'');right=record_nodes.get(r['right_record'],'')
        if left and right:
            _edge(desk,mid,left,'contradicts',right,r['reason'],1)
    desk.db.commit();result=matter_graph(desk,mid)
    desk.audit('matter_graph_refreshed',{'matter':mid,'nodes':len(result['nodes']),'edges':len(result['edges'])})
    return {'matter':mid,'nodes':len(result['nodes']),'edges':len(result['edges'])}


def matter_graph(desk, mid):
    ensure_schema(desk);_matter(desk,mid)
    nodes=[]
    for r in desk.db.execute('SELECT * FROM matter_graph_nodes_v380 WHERE matter=? ORDER BY node_kind,title',(mid,)):
        item=dict(r);item['source_ids']=_json(item['source_ids'],[]);nodes.append(item)
    edges=[dict(r) for r in desk.db.execute('SELECT * FROM matter_graph_edges_v380 WHERE matter=? ORDER BY relation,id',(mid,))]
    counts={kind:sum(x['node_kind']==kind for x in nodes) for kind in ('fact','claim','argument','evidence','question','missing')}
    return {'matter':mid,'nodes':nodes,'edges':edges,'counts':counts,
      'warning':'Un lien proposé ne valide ni le fait ni la portée juridique de la pièce.'}


def playbooks(desk):
    ensure_schema(desk);rows=[]
    for r in desk.db.execute('SELECT * FROM playbook_definitions_v380 WHERE enabled=1 ORDER BY family,name'):
        item=dict(r);item['steps']=_json(item['steps'],[]);rows.append(item)
    return rows


def start_playbook(desk, playbook_id, mid, objective=''):
    ensure_schema(desk);_matter(desk,mid);row=desk.db.execute(
      'SELECT * FROM playbook_definitions_v380 WHERE id=? AND enabled=1',(playbook_id,)).fetchone()
    if not row:raise Stop('playbook_absent')
    objective=_text(objective,3000);stamp=desk.now();rid=digest('|'.join([playbook_id,mid,objective,stamp]))[:32]
    steps=_json(row['steps'],[])
    desk.db.execute('INSERT INTO playbook_runs_v380 VALUES(?,?,?,?,?,?,?,?,?)',
      (rid,playbook_id,mid,objective,'running',0,json.dumps({},ensure_ascii=False),stamp,stamp))
    for pos,step in enumerate(steps):desk.db.execute('INSERT INTO playbook_steps_v380 VALUES(?,?,?,?,?,?,?,?)',
      (rid,pos,step['code'],step['label'],'pending',None,'',stamp))
    desk.db.commit();desk.audit('playbook_started',{'run_id':rid,'playbook':playbook_id,'matter':mid})
    return {'run_id':rid,'status':'running','steps':len(steps)}


def _sync_run(desk, rid):
    run=desk.db.execute('SELECT * FROM playbook_runs_v380 WHERE id=?',(rid,)).fetchone()
    if not run:raise Stop('parcours_absent')
    for step in desk.db.execute("SELECT * FROM playbook_steps_v380 WHERE run_id=? AND status='queued'",(rid,)).fetchall():
        job=desk.db.execute('SELECT status,result FROM jobs WHERE id=?',(step['job_id'],)).fetchone()
        if job and job['status'] in ('done','error','cancelled'):
            status='done' if job['status']=='done' else job['status']
            desk.db.execute('UPDATE playbook_steps_v380 SET status=?,result=?,updated=? WHERE run_id=? AND step_no=?',
              (status,job['result'] or '',desk.now(),rid,step['step_no']))
    steps=desk.db.execute('SELECT * FROM playbook_steps_v380 WHERE run_id=? ORDER BY step_no',(rid,)).fetchall()
    if any(x['status']=='error' for x in steps):status='error'
    elif steps and all(x['status']=='done' for x in steps):status='done'
    elif any(x['status']=='awaiting_input' for x in steps):status='awaiting_input'
    else:status='running'
    current=next((x['step_no'] for x in steps if x['status'] not in ('done','cancelled')),len(steps))
    desk.db.execute('UPDATE playbook_runs_v380 SET status=?,current_step=?,updated=? WHERE id=?',
      (status,current,desk.now(),rid));desk.db.commit()
    return desk.db.execute('SELECT * FROM playbook_runs_v380 WHERE id=?',(rid,)).fetchone(),steps


def advance_playbook(desk, rid):
    ensure_schema(desk);run,step_rows=_sync_run(desk,rid)
    if run['status'] in ('done','error','cancelled'):return playbook_run(desk,rid)
    if run['status']=='awaiting_input' or any(x['status']=='queued' for x in step_rows):
        return playbook_run(desk,rid)
    definition=desk.db.execute('SELECT steps FROM playbook_definitions_v380 WHERE id=?',(run['playbook_id'],)).fetchone()
    definitions=_json(definition['steps'],[])
    pending=next((r for r in step_rows if r['status']=='pending'),None)
    if not pending:return playbook_run(desk,rid)
    step=definitions[pending['step_no']]
    if step.get('manual'):
        result={'target':step.get('target',''),'message':'Sélection ou validation de l’avocat requise.'}
        desk.db.execute('UPDATE playbook_steps_v380 SET status=?,result=?,updated=? WHERE run_id=? AND step_no=?',
          ('awaiting_input',json.dumps(result,ensure_ascii=False),desk.now(),rid,pending['step_no']))
        desk.db.commit();_sync_run(desk,rid);return playbook_run(desk,rid)
    args={'matter':run['matter'],**step.get('args',{})}
    instruction=run['objective'] or step['label']
    if step.get('job_kind')=='build_matrix':args['objective']=instruction
    elif step.get('job_kind') in ('draft_act','prepare_document_project','prepare_hearing','prepare_word_project'):
        args['instruction']=instruction
    elif step.get('job_kind')=='legal_research':args['question']=instruction
    job=desk.enqueue(step['job_kind'],args,priority=0)
    desk.db.execute('UPDATE playbook_steps_v380 SET status=?,job_id=?,updated=? WHERE run_id=? AND step_no=?',
      ('queued',job,desk.now(),rid,pending['step_no']))
    desk.db.commit();desk.audit('playbook_step_queued',{'run_id':rid,'step':pending['step_no'],'job':job})
    return playbook_run(desk,rid)


def complete_manual_step(desk, rid, step_no):
    ensure_schema(desk)
    try:step_no=int(step_no)
    except (TypeError,ValueError):raise Stop('etape_invalide') from None
    row=desk.db.execute('SELECT status FROM playbook_steps_v380 WHERE run_id=? AND step_no=?',(rid,step_no)).fetchone()
    if not row or row['status']!='awaiting_input':raise Stop('etape_non_validable')
    desk.db.execute("UPDATE playbook_steps_v380 SET status='done',updated=? WHERE run_id=? AND step_no=?",(desk.now(),rid,step_no))
    desk.db.commit();_sync_run(desk,rid);return playbook_run(desk,rid)


def playbook_run(desk, rid):
    ensure_schema(desk);run,steps=_sync_run(desk,rid);result=dict(run)
    result['context']=_json(result['context'],{});result['steps']=[]
    for r in steps:
        item=dict(r);item['result']=_json(item['result'],{'raw':item['result']} if item['result'] else {})
        result['steps'].append(item)
    return result


def recent_playbook_runs(desk, matter='', limit=50):
    ensure_schema(desk);params=[];where=''
    if matter:where=' WHERE matter=?';params.append(matter)
    params.append(max(1,min(int(limit),100)))
    ids=[r['id'] for r in desk.db.execute('SELECT id FROM playbook_runs_v380'+where+' ORDER BY updated DESC LIMIT ?',params)]
    return [playbook_run(desk,rid) for rid in ids]


def register_service(desk, service_id, name, base_url, capabilities, secret_ref='', enabled=False, reviewed=False):
    ensure_schema(desk);service_id=_text(service_id,64,True).lower()
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,63}',service_id):raise Stop('identifiant_service_invalide')
    name=_text(name,120,True);base_url=_text(base_url,1000,True);parts=urlsplit(base_url)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:raise Stop('url_service_https_requise')
    if parts.hostname in ('localhost','127.0.0.1','::1') or parts.hostname.endswith('.local'):raise Stop('url_service_refusee')
    if isinstance(capabilities,str):capabilities=[x.strip() for x in capabilities.split(',') if x.strip()]
    capabilities=list(dict.fromkeys(str(x) for x in capabilities or []))
    if not capabilities or any(x not in CAPABILITIES for x in capabilities):raise Stop('capacite_ecosysteme_invalide')
    if enabled and not reviewed:raise Stop('revue_service_requise')
    old=desk.db.execute('SELECT created FROM ecosystem_services_v380 WHERE id=?',(service_id,)).fetchone();stamp=desk.now()
    desk.db.execute('INSERT OR REPLACE INTO ecosystem_services_v380 VALUES(?,?,?,?,?,?,?,?,?)',
      (service_id,name,base_url,json.dumps(capabilities),_text(secret_ref,120),int(enabled),int(reviewed),old['created'] if old else stamp,stamp))
    desk.db.commit();desk.audit('ecosystem_service_saved',{'service_id':service_id,'enabled':bool(enabled),'capabilities':capabilities})
    return {'service_id':service_id,'enabled':bool(enabled),'secret_exposed':False}


def services(desk):
    ensure_schema(desk);result=[]
    for r in desk.db.execute('SELECT * FROM ecosystem_services_v380 ORDER BY name'):
        item=dict(r);item['capabilities']=_json(item['capabilities'],[]);item['secret_configured']=bool(item.pop('secret_ref'));result.append(item)
    configured={x['id'] for x in result}
    dynamic={'nextcloud':str(desk.c.get('nextcloud',{}).get('url') or ''),
      'onlyoffice':str(desk.c.get('onlyoffice',{}).get('url') or '')}
    for source in ECOSYSTEM_CATALOG:
        if source['id'] in configured:continue
        item=dict(source);item['base_url']=dynamic.get(item['id']) or item['base_url']
        item.update({'enabled':0,'reviewed':0,'configured':False,
          'secret_configured':False,'created':'','updated':''})
        result.append(item)
    return result


def prepare_ecosystem_action(desk, service_id, mid, capability, payload):
    ensure_schema(desk);_matter(desk,mid);row=desk.db.execute('SELECT * FROM ecosystem_services_v380 WHERE id=?',(service_id,)).fetchone()
    if not row or not row['enabled'] or not row['reviewed']:raise Stop('service_ecosysteme_non_active')
    if capability not in _json(row['capabilities'],[]) or capability not in CAPABILITIES:raise Stop('capacite_ecosysteme_non_autorisee')
    if not isinstance(payload,dict):raise Stop('charge_ecosysteme_invalide')
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True)
    if len(raw)>12000:raise Stop('charge_ecosysteme_trop_longue')
    stamp=desk.now();eid=digest('|'.join([service_id,mid,capability,raw,stamp]))
    envelope={'matter_id':mid,'capability':capability,'data':payload,'requested_at':stamp,
      'callback_path':'/api/v1/ecosystem/events/'+eid+'/complete'}
    encoded=json.dumps(envelope,ensure_ascii=False,sort_keys=True)
    desk.db.execute('INSERT INTO ecosystem_events_v380 VALUES(?,?,?,?,?,?,?,?,?,?)',
      (eid,service_id,mid,capability,encoded,digest(encoded),'prepared','',stamp,''))
    desk.db.commit();desk.audit('ecosystem_action_prepared',{'event_id':eid,'service_id':service_id,'matter':mid,'capability':capability})
    return {'event_id':eid,'status':'prepared','payload_hash':digest(encoded),
      'message':'Enveloppe prête pour le connecteur autorisé ; aucun appel réseau effectué par le noyau.'}


def ecosystem_events(desk, status='', limit=100):
    ensure_schema(desk);params=[];where=''
    if status:
        if status not in ('prepared','delivered','done','error'):raise Stop('etat_evenement_invalide')
        where=' WHERE status=?';params.append(status)
    params.append(max(1,min(int(limit),200)))
    rows=[]
    for r in desk.db.execute('SELECT * FROM ecosystem_events_v380'+where+' ORDER BY created DESC LIMIT ?',params):
        item=dict(r);item['payload']=_json(item['payload'],{});item['result']=_json(item['result'],{});rows.append(item)
    return rows


def complete_ecosystem_event(desk, event_id, status, result):
    ensure_schema(desk)
    if status not in ('done','error'):raise Stop('resultat_ecosysteme_invalide')
    row=desk.db.execute('SELECT status FROM ecosystem_events_v380 WHERE id=?',(event_id,)).fetchone()
    if not row:raise Stop('evenement_ecosysteme_absent')
    if row['status'] in ('done','error'):raise Stop('evenement_ecosysteme_deja_termine')
    raw=json.dumps(result if isinstance(result,dict) else {'message':str(result)},ensure_ascii=False)
    if len(raw)>20000:raise Stop('resultat_ecosysteme_trop_long')
    desk.db.execute('UPDATE ecosystem_events_v380 SET status=?,result=?,finished=? WHERE id=?',(status,raw,desk.now(),event_id))
    desk.db.commit();desk.audit('ecosystem_action_completed',{'event_id':event_id,'status':status})
    return {'event_id':event_id,'status':status}


def relevance_metrics(desk):
    ensure_schema(desk)
    jobs=dict(desk.db.execute("SELECT status,COUNT(*) FROM jobs WHERE id IN (SELECT id FROM jobs ORDER BY id DESC LIMIT 300) GROUP BY status"))
    finished=jobs.get('done',0)+jobs.get('error',0)+jobs.get('cancelled',0)
    reviews=dict(desk.db.execute('SELECT recommendation,COUNT(*) FROM quality_reviews_v370 GROUP BY recommendation'))
    quality_total=sum(reviews.values());quality_good=reviews.get('approve',0)+reviews.get('approved',0)
    feedback=dict(desk.db.execute('SELECT category,COUNT(*) FROM feedback GROUP BY category'))
    drafted=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready'").fetchone()[0]
    handled=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='handled'").fetchone()[0]
    auto_links=desk.db.execute("SELECT COUNT(*) FROM portfolio_mail_links WHERE status='automatic'").fetchone()[0]
    confirmed_links=desk.db.execute("SELECT COUNT(*) FROM portfolio_mail_links WHERE status='confirmed'").fetchone()[0]
    corrections=desk.db.execute('SELECT COUNT(*) FROM correction_examples_v370 WHERE enabled=1').fetchone()[0]
    graph_nodes=desk.db.execute('SELECT COUNT(*) FROM matter_graph_nodes_v380').fetchone()[0]
    sourced_nodes=desk.db.execute("SELECT COUNT(*) FROM matter_graph_nodes_v380 WHERE source_ids NOT IN ('','[]')").fetchone()[0]
    return {
      'job_reliability_pct':round(100*jobs.get('done',0)/finished,1) if finished else None,
      'independent_control_approval_pct':round(100*quality_good/quality_total,1) if quality_total else None,
      'wrong_matter_feedback':feedback.get('wrong_matter',0),
      'drafts_ready':drafted,'handled_items':handled,
      'confirmed_matter_links':confirmed_links,'automatic_matter_links':auto_links,
      'accepted_corrections':corrections,
      'graph_source_coverage_pct':round(100*sourced_nodes/graph_nodes,1) if graph_nodes else None,
      'sample_sizes':{'jobs':finished,'quality_reviews':quality_total,'graph_nodes':graph_nodes},
      'limits':'Mesures descriptives. Elles ne prouvent pas la justesse juridique et doivent être lues avec leurs dénominateurs.'}


def run_evaluation(desk):
    ensure_schema(desk);stamp=desk.now();results=[]
    center=action_center(desk,20)
    required={'id','bucket','title','reason','action_label'}
    results.append({'case':'action_contract','passed':all(required<=set(x) and x['bucket'] in BUCKETS for x in center['cards']),
      'detail':str(len(center['cards']))+' carte(s) contrôlée(s)'})
    bad=desk.db.execute("SELECT COUNT(*) FROM matter_graph_nodes_v380 WHERE node_kind IN ('fact','claim','argument','evidence') AND source_ids IN ('','[]')").fetchone()[0]
    total=desk.db.execute("SELECT COUNT(*) FROM matter_graph_nodes_v380 WHERE node_kind IN ('fact','claim','argument','evidence')").fetchone()[0]
    results.append({'case':'graph_provenance','passed':bad==0,'detail':str(total-bad)+'/'+str(total)+' nœuds sourcés'})
    forbidden={'deposit_draft','approve_cabinet_confirmation_batch'};unsafe=[]
    for p in playbooks(desk):
        unsafe += [s.get('job_kind') for s in p['steps'] if s.get('job_kind') in forbidden]
    results.append({'case':'playbook_safety','passed':not unsafe,'detail':'Aucune action externe engageante.' if not unsafe else ', '.join(unsafe)})
    leaked=desk.db.execute("SELECT COUNT(*) FROM ecosystem_events_v380 WHERE payload LIKE '%api_key%' OR payload LIKE '%password%' OR payload LIKE '%secret%' ").fetchone()[0]
    results.append({'case':'ecosystem_boundary','passed':leaked==0,'detail':'Aucun secret nommé dans les enveloppes.' if not leaked else str(leaked)+' enveloppe(s) à examiner'})
    non_independent=desk.db.execute("SELECT COUNT(*) FROM quality_reviews_v370 WHERE recommendation='not_independent'").fetchone()[0]
    control_total=desk.db.execute('SELECT COUNT(*) FROM quality_reviews_v370').fetchone()[0]
    results.append({'case':'quality_control','passed':non_independent==0,'detail':str(control_total)+' contrôle(s), '+str(non_independent)+' non indépendant(s)'})
    score=round(100*sum(x['passed'] for x in results)/len(results),1);rid=digest(stamp+'|'+json.dumps(results,sort_keys=True))[:32]
    metrics=relevance_metrics(desk)
    desk.db.execute('INSERT INTO evaluation_runs_v380 VALUES(?,?,?,?,?,?,?)',
      (rid,'done',score,json.dumps(results,ensure_ascii=False),json.dumps(metrics,ensure_ascii=False),stamp,desk.now()))
    desk.db.commit();desk.audit('business_evaluation_completed',{'run_id':rid,'score':score})
    return {'run_id':rid,'score':score,'results':results,'metrics':metrics}


def evaluation_runs(desk, limit=20):
    ensure_schema(desk);rows=[]
    for r in desk.db.execute('SELECT * FROM evaluation_runs_v380 ORDER BY created DESC LIMIT ?',(max(1,min(int(limit),100)),)):
        item=dict(r);item['results']=_json(item['results'],[]);item['metrics']=_json(item['metrics'],{});rows.append(item)
    return rows


def perform(desk, kind, args):
    if kind=='review_action380':return review_action(desk,args.get('action_id',''),args.get('state',''),args.get('note',''),args.get('snoozed_until',''))
    if kind=='refresh_matter_graph380':return refresh_matter_graph(desk,args.get('matter',''))
    if kind=='start_playbook380':return start_playbook(desk,args.get('playbook_id',''),args.get('matter',''),args.get('objective',''))
    if kind=='advance_playbook380':return advance_playbook(desk,args.get('run_id',''))
    if kind=='complete_playbook_step380':return complete_manual_step(desk,args.get('run_id',''),args.get('step_no',''))
    if kind=='save_ecosystem_service380':return register_service(desk,args.get('service_id',''),args.get('name',''),args.get('base_url',''),args.get('capabilities',''),args.get('secret_ref',''),args.get('enabled')=='yes',args.get('reviewed')=='yes')
    if kind=='prepare_ecosystem_action380':return prepare_ecosystem_action(desk,args.get('service_id',''),args.get('matter',''),args.get('capability',''),_json(args.get('payload'),{}) if isinstance(args.get('payload'),str) else args.get('payload',{}))
    if kind=='run_business_evaluation380':return run_evaluation(desk)
    raise Stop('action_cabinet_operant_inconnue')

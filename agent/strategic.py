"""Source-bound strategic analysis, evidence matrix and internal act projects.

The model can propose reasoning and drafting, but it cannot validate a fact,
choose a procedural act or perform any external operation. Every saved output is
matter-scoped, versioned and linked to the exact excerpts supplied to the model.
"""
import json
import re

from .common import Stop, digest, load_matters
from .index import DocumentIndex
from .model import (Model, STRATEGY_ANALYSIS, EVIDENCE_MATRIX, ACT_PROJECT, routed_config,
                    validate)


ACT_TYPES = {
    'conclusions':'Conclusions', 'assignation':'Assignation',
    'mise_en_demeure':'Mise en demeure', 'consultation':'Consultation',
    'protocole':'Protocole transactionnel', 'contrat':'Contrat',
    'bordereau':'Bordereau de pièces', 'note_audience':'Note d’audience',
    'courrier_confrere':'Courrier à un confrère', 'autre':'Autre projet',
    'constitution':'Constitution d’avocat', 'assignation_refere':'Assignation en référé',
}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS strategy_analyses(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, version INTEGER NOT NULL,
      objective TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
      sources TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
      validation_note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS matrix_runs(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, version INTEGER NOT NULL,
      objective TEXT NOT NULL, data TEXT NOT NULL, sources TEXT NOT NULL,
      created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS evidence_matrix_rows(
      id TEXT PRIMARY KEY, run_id TEXT NOT NULL, matter TEXT NOT NULL,
      position INTEGER NOT NULL, row_type TEXT NOT NULL, proposition TEXT NOT NULL,
      asserted_by TEXT NOT NULL, proof_status TEXT NOT NULL,
      supporting_sources TEXT NOT NULL, contradicting_sources TEXT NOT NULL,
      neutral_sources TEXT NOT NULL, missing_evidence TEXT NOT NULL,
      strategic_use TEXT NOT NULL, cautions TEXT NOT NULL, status TEXT NOT NULL,
      created TEXT NOT NULL, updated TEXT NOT NULL, validation_note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS act_projects(
      id TEXT PRIMARY KEY, matter TEXT NOT NULL, version INTEGER NOT NULL,
      act_type TEXT NOT NULL, title TEXT NOT NULL, instruction TEXT NOT NULL,
      status TEXT NOT NULL, content TEXT NOT NULL, data TEXT NOT NULL,
      sources TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
      validation_note TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS strategic_history(
      id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
      data TEXT NOT NULL, changed_at TEXT NOT NULL, reason TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS strategy_matter ON strategy_analyses(matter,version);
    CREATE INDEX IF NOT EXISTS matrix_matter ON matrix_runs(matter,version);
    CREATE INDEX IF NOT EXISTS matrix_rows_run ON evidence_matrix_rows(run_id,position);
    CREATE INDEX IF NOT EXISTS acts_matter ON act_projects(matter,version);
    ''')
    desk.db.commit()


def matter(desk, mid):
    found=next((m for m in load_matters(desk.c) if m['id']==mid),None)
    if not found:raise Stop('dossier_absent')
    return found


def _text(value, maximum):
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value or '')).strip()
    if not value or len(value)>maximum:raise Stop('texte_strategique_invalide')
    return value


def _source_ids(value):
    return list(dict.fromkeys(str(x) for x in (value or []) if x))


def _validate_ids(ids, valid, required=False):
    ids=_source_ids(ids)
    if (required and not ids) or any(x not in valid for x in ids):
        raise Stop('source_strategique_invalide')
    return ids


def case_sources(desk, mid, query, limit=None):
    """Build a bounded local evidence packet for exactly one matter."""
    m=matter(desk,mid);cfg=desk.c.get('strategic',{})
    limit=max(6,min(int(limit or cfg.get('max_sources',30)),40))
    from .legal_memory import memory_sources
    memory=memory_sources(desk,mid,query,min(16,limit))
    index=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
    ranked,_=index.ranked_chunks(m,[query,m.get('client_name',''),mid],limit=limit)
    documents=[{'id':'knowledge-'+row[1][:16]+'-'+str(row[6]),'kind':row[5],
      'matter':mid,'path':row[2],'modified':row[4],'excerpt':row[7],
      'partial':True,'score':round(score,4)} for row,score in ranked]
    extras=[]
    for row in desk.db.execute('''SELECT id,event_type,event_at,title,detail,source_path,status
      FROM timeline_events WHERE matter=? AND status<>'stale'
      AND event_type IN ('calendar','task') ORDER BY event_at DESC LIMIT 20''',(mid,)):
        extras.append({'id':'timeline-'+row['id'][:20],'kind':'timeline_'+row['event_type'],
          'matter':mid,'path':row['source_path'],'modified':row['event_at'],
          'excerpt':row['title']+'\n'+row['detail'],'partial':False})
    sources=[];seen=set()
    for source in memory+documents+extras:
        if source['id'] in seen:continue
        seen.add(source['id']);sources.append(source)
        if len(sources)>=limit:break
    maximum=min(60000,desk.c.get('ollama',{}).get('max_context_chars',65000)-5000)
    while sources and len(json.dumps(sources,ensure_ascii=False))>maximum:sources.pop()
    if not sources:raise Stop('aucune_source_strategique')
    return m,sources


def _check_strategy(result, valid):
    _validate_ids(result['source_ids'],valid,True)
    for item in result['positions']:
        _validate_ids(item['source_ids'],valid,True)
    for item in result['issues']:
        _validate_ids(item['source_ids'],valid,True)
    for item in result['options']:
        _validate_ids(item['source_ids'],valid,True)
    _validate_ids(result['recommended_approach']['source_ids'],valid,True)


def analyze_strategy(desk,args):
    ensure_schema(desk);mid=args.get('matter','');objective=_text(args.get('objective'),3000)
    m,sources=case_sources(desk,mid,objective);valid={x['id'] for x in sources}
    payload={'dossier':{'id':mid,'nom':m.get('client_name','')},
      'objectif_avocat':objective,'sources':sources,
      'regles':{'analyse_interne':True,'decision_humaine_requise':True,
        'recherche_juridique_externe_absente':True}}
    result=Model(routed_config(desk.c,'legal_analysis')).ask('strategy_analysis',payload)
    validate(result,STRATEGY_ANALYSIS);_check_strategy(result,valid)
    version=desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM strategy_analyses WHERE matter=?',(mid,)).fetchone()[0]
    stamp=desk.now();sid=digest(mid+'|strategy|'+str(version)+'|'+stamp)
    desk.db.execute('INSERT INTO strategy_analyses VALUES(?,?,?,?,?,?,?,?,?,?)',
      (sid,mid,version,objective,'proposed',json.dumps(result,ensure_ascii=False),
       json.dumps(sources,ensure_ascii=False),stamp,stamp,''))
    desk.audit('analyze_strategy',{'matter':mid,'analysis':sid,'version':version})
    desk.db.commit();return {'analyse':sid,'version':version,'statut':'proposee',
      'options':len(result['options']),'sources':len(sources)}


def _check_matrix(result,valid):
    _validate_ids(result['source_ids'],valid,True)
    for row in result['rows']:
        ids=[]
        for field in ('supporting_source_ids','contradicting_source_ids','neutral_source_ids'):
            ids+=_validate_ids(row[field],valid)
        if not ids and not row['missing_evidence']:
            raise Stop('ligne_matrice_non_sourcee')


def build_matrix(desk,args):
    ensure_schema(desk);mid=args.get('matter','')
    objective=(args.get('objective') or '').strip()
    if not objective:
        old=desk.db.execute('SELECT objective FROM strategy_analyses WHERE matter=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
        objective=old[0] if old else 'Cartographier les faits, prétentions, défenses et pièces du dossier.'
    objective=_text(objective,3000);m,sources=case_sources(desk,mid,objective)
    valid={x['id'] for x in sources}
    result=Model(routed_config(desk.c,'legal_analysis')).ask('evidence_matrix',{
      'dossier':{'id':mid,'nom':m.get('client_name','')},'objectif_avocat':objective,
      'sources':sources,'regles':{'une_ligne_une_proposition':True,
      'absence_de_piece_ne_prouve_pas_absence_du_fait':True}})
    validate(result,EVIDENCE_MATRIX);_check_matrix(result,valid)
    version=desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM matrix_runs WHERE matter=?',(mid,)).fetchone()[0]
    stamp=desk.now();run=digest(mid+'|matrix|'+str(version)+'|'+stamp)
    desk.db.execute('INSERT INTO matrix_runs VALUES(?,?,?,?,?,?,?)',(run,mid,version,objective,
      json.dumps(result,ensure_ascii=False),json.dumps(sources,ensure_ascii=False),stamp))
    for position,row in enumerate(result['rows']):
        rid=digest(run+'|'+str(position)+'|'+row['row_type']+'|'+row['proposition'])
        desk.db.execute('INSERT INTO evidence_matrix_rows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (rid,run,mid,position,row['row_type'],row['proposition'],row['asserted_by'],
           row['proof_status'],json.dumps(row['supporting_source_ids']),
           json.dumps(row['contradicting_source_ids']),json.dumps(row['neutral_source_ids']),
           json.dumps(row['missing_evidence'],ensure_ascii=False),row['strategic_use'],
           json.dumps(row['cautions'],ensure_ascii=False),'proposed',stamp,stamp,''))
    desk.audit('build_matrix',{'matter':mid,'run':run,'version':version})
    desk.db.commit();return {'matrice':run,'version':version,'lignes':len(result['rows']),
      'lacunes_globales':len(result['global_gaps']),'sources':len(sources)}


def _render_act(result):
    lines=['PROJET INTERNE — À RELIRE ET VALIDER','',result['title'].strip(),'']
    if result['introduction'].strip():
        lines += [result['introduction'].strip(),
                  '[Sources internes : '+', '.join(result['introduction_source_ids'])+']','']
    for section in result['sections']:
        lines += [section['heading'].strip(),section['body'].strip(),
                  '[Sources internes : '+', '.join(section['source_ids'])+']','']
    if result['requests']:
        lines += ['DEMANDES / DISPOSITIF À CONTRÔLER']
        for item in result['requests']:
            lines.append('- '+item['text'].strip()+' [Sources : '+', '.join(item['source_ids'])+']')
        lines.append('')
    if result['exhibits_referenced']:
        lines += ['PIÈCES CITÉES À CONTRÔLER']
        for item in result['exhibits_referenced']:
            lines.append('- '+item['label'].strip()+' [Sources : '+', '.join(item['source_ids'])+']')
        lines.append('')
    if result['placeholders']:
        lines += ['ÉLÉMENTS À COMPLÉTER']+['- '+x for x in result['placeholders']]+['']
    if result['points_for_lawyer']:
        lines += ['POINTS À DÉCIDER PAR L’AVOCAT']+['- '+x for x in result['points_for_lawyer']]+['']
    if result['limits']:
        lines += ['LIMITES DU PROJET']+['- '+x for x in result['limits']]
    return '\n'.join(lines).strip()


def draft_act(desk,args):
    ensure_schema(desk);mid=args.get('matter','');act_type=args.get('act_type','')
    if act_type not in ACT_TYPES:raise Stop('type_acte_invalide')
    instruction=_text(args.get('instruction'),3000);m,sources=case_sources(desk,mid,instruction,36)
    valid={x['id'] for x in sources}
    result=Model(routed_config(desk.c,'document_drafting')).ask('act_project',{
      'dossier':{'id':mid,'nom':m.get('client_name','')},'type_acte':act_type,
      'libelle_type':ACT_TYPES[act_type],'instruction_avocat':instruction,'sources':sources,
      'regles':{'projet_interne':True,'aucun_depot_ni_envoi':True,'validation_avocat_requise':True}})
    validate(result,ACT_PROJECT)
    if result['act_type'] not in (act_type,ACT_TYPES[act_type]):raise Stop('type_acte_retour_invalide')
    _validate_ids(result['source_ids'],valid,True)
    _validate_ids(result['introduction_source_ids'],valid,bool(result['introduction'].strip()))
    for section in result['sections']:_validate_ids(section['source_ids'],valid,bool(section['body'].strip()))
    for request in result['requests']:_validate_ids(request['source_ids'],valid,True)
    for exhibit in result['exhibits_referenced']:_validate_ids(exhibit['source_ids'],valid,True)
    content=_render_act(result)
    from .control470 import quality,banner
    from .templates470 import kind_for_document
    kind=kind_for_document(act_type)
    quality_data=quality(desk,mid,kind,content,args)
    notice=banner(quality_data)
    if notice:content=notice+'\n\n'+content
    status='incomplete' if quality_data['mentions'] and not quality_data['mentions']['ok'] else 'proposed'
    result['_quality']={'mentions':quality_data['mentions'],'citations':quality_data['citations'],'fact_date':quality_data['fact_date']}
    if len(content)>50000:raise Stop('projet_acte_trop_long')
    version=desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM act_projects WHERE matter=?',(mid,)).fetchone()[0]
    stamp=desk.now();pid=digest(mid+'|act|'+str(version)+'|'+stamp)
    desk.db.execute('INSERT INTO act_projects VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
      (pid,mid,version,act_type,result['title'],instruction,status,content,
       json.dumps(result,ensure_ascii=False),json.dumps(sources,ensure_ascii=False),stamp,stamp,''))
    desk.audit('draft_act',{'matter':mid,'project':pid,'version':version,'type':act_type})
    desk.db.commit();return {'projet':pid,'version':version,'type':ACT_TYPES[act_type],
      'statut':'incomplet' if status=='incomplete' else 'propose','caracteres':len(content),'sources':len(sources),
      'depot':False,'envoi':False,
      'mentions_manquantes':[x['label'] for x in (quality_data['mentions'] or {}).get('missing',[])],
      'references_a_verifier':quality_data['citations']['needs_check']}


def _snapshot(row):return {key:row[key] for key in row.keys()}


def change(desk,kind,args):
    ensure_schema(desk);stamp=desk.now()
    mapping={
      'validate_strategy':('strategy_analyses','analysis','validated'),
      'archive_strategy':('strategy_analyses','analysis','archived'),
      'validate_matrix_row':('evidence_matrix_rows','row','validated'),
      'dispute_matrix_row':('evidence_matrix_rows','row','disputed'),
      'archive_matrix_row':('evidence_matrix_rows','row','archived'),
      'validate_act':('act_projects','project','validated'),
      'archive_act':('act_projects','project','archived'),
    }
    if kind not in mapping:raise Stop('action_strategique_invalide')
    table,field,status=mapping[kind];entity=args.get(field,'')
    row=desk.db.execute('SELECT * FROM '+table+' WHERE id=?',(entity,)).fetchone()
    if not row:raise Stop('element_strategique_absent')
    if args.get('matter') and args['matter']!=row['matter']:raise Stop('element_autre_dossier')
    if kind=='validate_act':
        if row['status']=='incomplete':raise Stop('mentions_obligatoires_manquantes')
        try:quality_data=json.loads(row['data']).get('_quality',{})
        except ValueError:quality_data={}
        if (quality_data.get('citations') or {}).get('needs_check') and args.get('acknowledge_unverified')!='yes':
            raise Stop('references_a_verifier_avant_validation')
    desk.db.execute('INSERT INTO strategic_history VALUES(NULL,?,?,?,?,?)',
      (table,entity,json.dumps(_snapshot(row),ensure_ascii=False),stamp,kind))
    desk.db.execute('UPDATE '+table+' SET status=?,updated=?,validation_note=? WHERE id=?',
      (status,stamp,str(args.get('note',''))[:1000],entity))
    desk.audit(kind,{'matter':row['matter'],field:entity});desk.db.commit()
    return {'element':entity,'statut':status}


def latest_strategy(desk,mid):
    matter(desk,mid);ensure_schema(desk)
    row=desk.db.execute('SELECT * FROM strategy_analyses WHERE matter=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
    if not row:return None
    item=_snapshot(row);item['data']=json.loads(item['data']);item['sources']=json.loads(item['sources']);return item


def latest_matrix(desk,mid):
    matter(desk,mid);ensure_schema(desk)
    run=desk.db.execute('SELECT * FROM matrix_runs WHERE matter=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
    if not run:return None
    item=_snapshot(run);item['data']=json.loads(item['data']);item['sources']=json.loads(item['sources'])
    item['rows']=[]
    for row in desk.db.execute('SELECT * FROM evidence_matrix_rows WHERE run_id=? ORDER BY position',(run['id'],)):
        value=_snapshot(row)
        for field in ('supporting_sources','contradicting_sources','neutral_sources','missing_evidence','cautions'):
            value[field]=json.loads(value[field])
        item['rows'].append(value)
    return item


def act_projects(desk,mid,limit=30):
    matter(desk,mid);ensure_schema(desk)
    result=[]
    for row in desk.db.execute('SELECT * FROM act_projects WHERE matter=? ORDER BY version DESC LIMIT ?',
                               (mid,max(1,min(int(limit),100)) )):
        item=_snapshot(row);item['data']=json.loads(item['data']);item['sources']=json.loads(item['sources']);result.append(item)
    return result


def summary(desk,mid):
    ensure_schema(desk)
    return {'strategies':desk.db.execute('SELECT COUNT(*) FROM strategy_analyses WHERE matter=?',(mid,)).fetchone()[0],
      'matrix_rows_to_validate':desk.db.execute("SELECT COUNT(*) FROM evidence_matrix_rows WHERE matter=? AND status='proposed'",(mid,)).fetchone()[0],
      'act_projects':desk.db.execute('SELECT COUNT(*) FROM act_projects WHERE matter=?',(mid,)).fetchone()[0],
      'validated_acts':desk.db.execute("SELECT COUNT(*) FROM act_projects WHERE matter=? AND status='validated'",(mid,)).fetchone()[0]}


def assistant_sources(desk,mid,limit=4):
    """Expose prior work as explicitly non-probative conversational context."""
    ensure_schema(desk);result=[];analysis=latest_strategy(desk,mid)
    if analysis:
        data=analysis['data'];result.append({'id':'strategy-'+analysis['id'][:20],
          'kind':'strategic_analysis','path':'Analyse stratégique interne n°'+str(analysis['version']),
          'modified':analysis['updated'],'excerpt':('[STATUT : '+analysis['status']+']\nObjectif : '+analysis['objective']+
          '\nSynthèse : '+data['executive_summary']+'\nOrientation : '+data['recommended_approach']['summary']),
          'partial':True})
    matrix=latest_matrix(desk,mid)
    if matrix:
        excerpt='\n'.join('- '+x['proposition']+' ['+x['proof_status']+'; statut '+x['status']+']' for x in matrix['rows'][:20])
        result.append({'id':'matrix-'+matrix['id'][:20],'kind':'evidence_matrix',
          'path':'Matrice de preuve interne n°'+str(matrix['version']),'modified':matrix['created'],
          'excerpt':excerpt,'partial':len(matrix['rows'])>20})
    for project in act_projects(desk,mid,max(0,int(limit)-len(result))):
        result.append({'id':'act-'+project['id'][:20],'kind':'act_project',
          'path':'Projet interne n°'+str(project['version'])+' · '+project['title'],
          'modified':project['updated'],'excerpt':'[STATUT : '+project['status']+']\n'+project['content'][:3500],
          'partial':len(project['content'])>3500})
    return result[:max(0,min(int(limit),8))]


def perform(desk,kind,args):
    if kind=='analyze_strategy':return analyze_strategy(desk,args)
    if kind=='build_matrix':return build_matrix(desk,args)
    if kind=='draft_act':return draft_act(desk,args)
    return change(desk,kind,args)

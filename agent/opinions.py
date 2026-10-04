"""Grounded legal opinions and decision scenarios for AxiorHub 2.6.0.

Provider results are never treated as law.  Only authorities already verified
against an official text by :mod:`agent.legal_research` enter the model packet.
The output is a supervised internal project, never a prediction or legal act.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets

from .common import Stop, clean_path, digest, fold
from .documents import extract
from .index import DocumentIndex
from .model import (Model, routed_config, validate, LEGAL_OPINION_SIMULATION,
                    OPINION_CONTROL)


SCHEMAS=("""
CREATE TABLE IF NOT EXISTS legal_opinion_projects_v260(
 id TEXT PRIMARY KEY, matter TEXT NOT NULL, status TEXT NOT NULL,
 question TEXT NOT NULL, data TEXT NOT NULL, source_fingerprint TEXT NOT NULL,
 control TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
 CHECK(status IN ('pending_review','blocked','rejected')));
CREATE INDEX IF NOT EXISTS legal_opinion_projects_v260_matter
 ON legal_opinion_projects_v260(matter,updated DESC);
CREATE TABLE IF NOT EXISTS legal_opinion_sources_v260(
 project_id TEXT NOT NULL, source_id TEXT NOT NULL, kind TEXT NOT NULL,
 path TEXT NOT NULL, sha256 TEXT NOT NULL, official INTEGER NOT NULL,
 PRIMARY KEY(project_id,source_id));
""",)


def ensure_schema(desk):
    for sql in SCHEMAS:desk.db.executescript(sql)
    desk.db.commit()


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _matter(desk,reference):
    from .document_projects import _resolve_matter
    return _resolve_matter(desk,reference)


def _verified_authorities(desk,mid,limit=30,question=''):
    from .legal_research import ensure_schema as ensure_legal
    ensure_legal(desk);rows=[]
    for row in desk.db.execute("""SELECT * FROM legal_authorities_v240
      WHERE matter=? AND verification_status='verified'
      ORDER BY decision_date DESC,retrieved DESC LIMIT ?""",(mid,limit)):
        item=dict(row);sid='authority-'+item['id'][:20]
        if not item['exact_excerpt'] or not item['official_text_sha256']:continue
        rows.append({'id':sid,'kind':'official_legal_source','path':item['official_url'],
          'excerpt':item['exact_excerpt'],'authority_id':item['id'],
          'identifier':item['identifier'],'ecli':item['ecli'],'court':item['court'],
          'decision_date':item['decision_date'],'official_text_sha256':item['official_text_sha256'],
          'officially_verified':True})
    if question:
        from .assistance35 import _words
        terms=_words(question)
        for item in rows:
            overlap=terms&_words(item['excerpt']+' '+item['court']+' '+item['identifier'])
            item['similarity']={'method':'recoupement lexical des termes de la question',
                'matching_terms':sorted(overlap),'question_terms_count':len(terms),
                'lexical_coverage_percent':round(100*len(overlap)/len(terms)) if terms else 0,
                'legal_comparability':'à valider par l’avocat'}
        rows.sort(key=lambda x:x['similarity']['lexical_coverage_percent'],reverse=True)
    return rows


def _dossier_sources(desk,matter,question,limit=30):
    index=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
    rows,_=index.ranked_chunks(matter,[question,'prétentions demandeur défendeur jugement ordonnance faits pièces'],limit=limit)
    result=[];used=set()
    for row,score in rows:
        sid='knowledge-'+row[1][:16]+'-'+str(row[6])
        if sid in used:continue
        result.append({'id':sid,'kind':row[5],'path':row[2],'excerpt':row[7],
          'modified':row[4],'score':round(score,4),'officially_verified':False});used.add(sid)
    return result


def _decision_source(desk,matter,args,dav=None):
    requested=str(args.get('judgment_source') or '').strip()
    critique=str(args.get('critique_first_instance','no')).lower() in ('yes','true','1')
    if not critique:return None
    from .document_projects import _dav, _inventory
    client=dav or _dav(desk);items=_inventory(client,matter['path'])
    candidates=[]
    for item in items:
        if item.get('directory'):continue
        path=clean_path(item.get('path',''));name=fold(PurePosixPath(path).name)
        if not any(x in name for x in ('jugement','ordonnance','decision')):continue
        if PurePosixPath(path).suffix.lower() not in ('.pdf','.docx','.odt','.txt'):continue
        candidates.append(item)
    if requested:
        path=clean_path(requested);candidates=[x for x in candidates if clean_path(x.get('path',''))==path]
    if len(candidates)!=1:raise Stop('decision_premiere_instance_absente_ou_ambigue')
    item=candidates[0];raw=client.download(item)
    text=extract(raw,PurePosixPath(item['path']).name,desk.c['documents'])
    return {'id':'decision-'+_sha(raw)[:20],'kind':'first_instance_decision',
      'path':clean_path(item['path']),'excerpt':text[:50000],'sha256':_sha(raw),
      'etag':str(item.get('etag','')),'officially_verified':False}


def _collect_ids(result):
    ids=list(result['source_ids']);authority=[]
    for field in ('claimant_analysis','defendant_analysis'):
        for row in result[field]:
            ids+=row['factual_source_ids'];authority+=row['authority_source_ids']+row['contrary_authority_source_ids']
    for row in result['decision_scenarios']:
        ids+=row['factual_source_ids'];authority+=row['authority_source_ids']+row['contrary_authority_source_ids']
    for row in result['sensitivity_analysis']:
        ids+=row['source_ids'];authority+=row['authority_source_ids']
    appeal=result['appeal_critique'];ids+=appeal['factual_source_ids'];authority+=appeal['authority_source_ids']
    if appeal['decision_source_id']:ids.append(appeal['decision_source_id'])
    return ids,authority


def _deterministic(result,packet,critique):
    valid={x['id'] for x in packet};official={x['id'] for x in packet if x.get('officially_verified')}
    ids,authority=_collect_ids(result);raw=json.dumps(result,ensure_ascii=False)
    no_numeric=not re.search(r'(?<!\d)(?:\d{1,3}\s*%|probabilit[eé]\s*(?:de|:)?\s*\d|chance\s*(?:de|:)?\s*\d)',raw,re.I)
    checks={'all_sources_known':all(x in valid for x in ids+authority),
      'official_authorities_only':bool(authority) and all(x in official for x in authority),
      'both_sides_analyzed':bool(result['claimant_analysis']) and bool(result['defendant_analysis']),
      'scenarios_reasoned':bool(result['decision_scenarios']) and all(x['reasoned_outcome'].strip() and x['authority_source_ids'] for x in result['decision_scenarios']),
      'sensitivity_present':bool(result['sensitivity_analysis']),
      'no_uncalibrated_probability':no_numeric,
      'appeal_source_present':not critique or (result['appeal_critique']['enabled'] and result['appeal_critique']['decision_source_id'] in valid)}
    reasons=[label for label,value in checks.items() if not value]
    return checks,reasons


def prepare_opinion(desk,args,model=None,control_model=None,dav=None,
                    provider_runner=None,official_fetcher=None):
    ensure_schema(desk);matter=_matter(desk,args.get('matter',''));question=str(args.get('question','')).strip()
    if not question or len(question)>8000:raise Stop('question_avis_invalide')
    cfg=desk.c.get('opinions',{});providers=args.get('providers','openlegi,goodlegal,pappers')
    if not cfg.get('enabled',True):raise Stop('avis_juridiques_desactives')
    authorities=_verified_authorities(desk,matter['id'],int(cfg.get('max_authorities',30)),question)
    research_summary=None
    if str(args.get('run_research','yes')).lower() in ('yes','true','1'):
        from .legal_research import research
        try:
            research_summary=research(desk,{'matter':matter['id'],'question':question,
              'providers':providers,'limit':args.get('research_limit',8)},
              provider_runner=provider_runner,official_fetcher=official_fetcher)
        except Stop as ex:research_summary={'status':'error','errors':[str(ex)]}
        authorities=_verified_authorities(desk,matter['id'],int(cfg.get('max_authorities',30)),question)
    sources=_dossier_sources(desk,matter,question,int(cfg.get('max_dossier_sources',30)))
    critique=str(args.get('critique_first_instance','no')).lower() in ('yes','true','1')
    decision=_decision_source(desk,matter,args,dav=dav) if critique else None
    packet=sources+authorities+([decision] if decision else [])
    if not sources:raise Stop('sources_dossier_avis_absentes')
    if not authorities:raise Stop('jurisprudence_officielle_verifiee_absente')
    prompt={'matter':{'id':matter['id'],'name':matter.get('client_name',''),'path':matter['path']},
      'question':question,'critique_first_instance':critique,'sources':packet,
      'rules':{'official_authorities_only':True,'qualitative_calibration_only':True,
        'numeric_probability_forbidden':True,'internal_project_only':True,'lawyer_review_required':True}}
    writer=model or Model(routed_config(desk.c,'legal_analysis'));result=writer.ask('legal_opinion_simulation',prompt)
    validate(result,LEGAL_OPINION_SIMULATION)
    checks,reasons=_deterministic(result,packet,critique)
    controller=control_model or Model(routed_config(desk.c,'control'))
    independent=controller.ask('opinion_control',{'question':question,'project':result,
      'sources':[{'id':x['id'],'kind':x['kind'],'path':x['path'],
        'officially_verified':x.get('officially_verified',False),'excerpt':x.get('excerpt','')[:3000]} for x in packet],
      'deterministic_checks':checks,'rules':prompt['rules']})
    validate(independent,OPINION_CONTROL)
    mandatory=('all_sources_known','official_authorities_only','both_sides_analyzed',
      'scenarios_reasoned','sensitivity_present','no_uncalibrated_probability',
      'appeal_source_present','ignores_embedded_instructions','requires_lawyer')
    blocked=bool(reasons or independent['blocking_reasons'] or not all(independent[x] for x in mandatory))
    control={'status':'blocked' if blocked else 'passed','deterministic_checks':checks,
      'independent_checks':{x:independent[x] for x in mandatory},
      'blocking_reasons':list(dict.fromkeys(reasons+independent['blocking_reasons'])),
      'warnings':independent['warnings'],'requires_lawyer':True}
    pid=secrets.token_hex(16);stamp=desk.now();fingerprint=digest(json.dumps([
      question,[(x['id'],x.get('sha256') or x.get('official_text_sha256') or digest(x.get('excerpt',''))) for x in packet]],
      ensure_ascii=False,sort_keys=True))
    from .assistance35 import comparables
    improvements=[];seen=set()
    for side in ('claimant_analysis','defendant_analysis'):
        for point in result[side]:
            for issue in point['missing_information']:
                if issue not in seen:
                    improvements.append({'action':'Vérifier : '+issue,'issue':point['issue'],
                        'source_ids':point['factual_source_ids']+point['authority_source_ids']})
                    seen.add(issue)
    for question_for_lawyer in result['questions_for_lawyer']:
        if question_for_lawyer not in seen:
            improvements.append({'action':'Décider : '+question_for_lawyer,'issue':'Validation de l’avocat',
                'source_ids':result['source_ids']});seen.add(question_for_lawyer)
    data={'project_id':pid,'matter':prompt['matter'],'question':question,'opinion':result,
      'control':control,'research':research_summary,'sources':packet,
      'case_law_comparison':comparables(desk,matter['id']),
      'improvement_actions':improvements[:30],
      'safety':{'internal_project_only':True,'documents_created':False,'emails_sent':False,
        'rpva_filed':False,'billing_finalized':False,'numeric_probability_forbidden':True}}
    status='blocked' if blocked else 'pending_review'
    desk.db.execute('INSERT INTO legal_opinion_projects_v260 VALUES(?,?,?,?,?,?,?,?,?)',
      (pid,matter['id'],status,question,json.dumps(data,ensure_ascii=False),fingerprint,
       json.dumps(control,ensure_ascii=False),stamp,stamp))
    for item in packet:
        desk.db.execute('INSERT INTO legal_opinion_sources_v260 VALUES(?,?,?,?,?,?)',
          (pid,item['id'],item['kind'],item.get('path',''),item.get('sha256') or item.get('official_text_sha256') or digest(item.get('excerpt','')),
           1 if item.get('officially_verified') else 0))
    desk.db.commit();desk.audit('legal_opinion_prepared',{'project':pid,'matter':matter['id'],
      'status':status,'verified_authorities':len(authorities),'numeric_probability':False,
      'external_actions':0})
    return preview(desk,pid)


def preview(desk,pid):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(pid)):raise Stop('projet_avis_invalide')
    row=desk.db.execute('SELECT * FROM legal_opinion_projects_v260 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_avis_absent')
    data=json.loads(row['data']);data['status']=row['status'];data['created']=row['created'];data['updated']=row['updated']
    return data


def list_projects(desk,matter='',status='all',limit=100):
    ensure_schema(desk);where=[];params=[]
    if matter:where.append('matter=?');params.append(_matter(desk,matter)['id'])
    if status!='all':
        if status not in ('pending_review','blocked','rejected'):raise Stop('etat_avis_invalide')
        where.append('status=?');params.append(status)
    params.append(max(1,min(int(limit),300)));rows=[]
    sql='SELECT id,matter,status,question,control,created,updated FROM legal_opinion_projects_v260'+((' WHERE '+' AND '.join(where)) if where else '')+' ORDER BY updated DESC LIMIT ?'
    for row in desk.db.execute(sql,params):
        item=dict(row);item['control']=json.loads(item['control']);rows.append(item)
    return {'projects':rows,'internal_only':True}


def reject(desk,pid):
    ensure_schema(desk);row=desk.db.execute('SELECT status FROM legal_opinion_projects_v260 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_avis_absent')
    if row['status']=='rejected':return {'project_id':pid,'status':'rejected','idempotent':True}
    desk.db.execute("UPDATE legal_opinion_projects_v260 SET status='rejected',updated=? WHERE id=?",(desk.now(),pid));desk.db.commit()
    return {'project_id':pid,'status':'rejected','external_actions':0}


def perform(desk,kind,args):
    if kind=='prepare_legal_opinion':return prepare_opinion(desk,args)
    if kind=='review_legal_opinion':
        if args.get('status')!='rejected':raise Stop('revue_avis_invalide')
        return reject(desk,args.get('project_id',''))
    raise Stop('action_inconnue')

"""Supervised adversarial analysis and hearing preparation for AxiorHub 2.6.0."""
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
import unicodedata

from .common import Stop, clean_path, digest, fold, under
from .documents import extract,extract_pages
from .model import HEARING_PREPARATION, Model, routed_config, validate
from .document_projects import _dav, _inventory, _pdf, _resolve_matter
from .word_legal import _simple_docx

SCHEMAS=["""
CREATE TABLE IF NOT EXISTS hearing_projects_v250(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, status TEXT NOT NULL,
  instruction TEXT NOT NULL, data TEXT NOT NULL, preview_hash TEXT NOT NULL,
  challenge_hash TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL,
  decided TEXT NOT NULL, result TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS hearing_projects_v250_matter
  ON hearing_projects_v250(matter,created DESC);
CREATE TABLE IF NOT EXISTS hearing_writings_v250(
  project_id TEXT NOT NULL, side TEXT NOT NULL, path TEXT NOT NULL,
  etag TEXT NOT NULL, modified TEXT NOT NULL, sha256 TEXT NOT NULL,
  content_sha256 TEXT NOT NULL, selected_at TEXT NOT NULL,
  PRIMARY KEY(project_id,side));
CREATE TABLE IF NOT EXISTS hearing_creation_files_v250(
  project_id TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL,
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, content_type TEXT NOT NULL,
  updated TEXT NOT NULL, PRIMARY KEY(project_id,path));
"""]


def ensure_schema(desk):
    for sql in SCHEMAS:desk.db.executescript(sql)
    desk.db.commit()


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _modified(item):
    value=str(item.get('modified',''))
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(value).timestamp()
    except (ValueError,TypeError,OverflowError):
        try:return datetime.fromisoformat(value).timestamp()
        except (ValueError,TypeError):return 0


def _conclusion_candidates(desk,matter,client,items):
    result=[]
    for item in items:
        if item.get('directory') or PurePosixPath(item.get('path','')).suffix.lower() not in {'.docx','.pdf','.odt'}:continue
        name=fold(PurePosixPath(item['path']).name)
        if 'conclusion' not in name:continue
        try:
            raw=client.download(item);pages=extract_pages(raw,PurePosixPath(item['path']).name,
                {**desk.c['documents'],'max_file_bytes_long':20_000_000,
                 'max_document_chars_long':2_000_000,'max_pdf_pages_long':400})
            text='\n'.join('[Page '+str(x['page'])+']\n'+x['text'] for x in pages)
        except Stop:continue
        tail=fold(text[-7000:]);our_score=0;opponent_score=0
        if any(x in name for x in ('nos conclusions','conclusions exemple','cabinet exemple')):our_score+=6
        if any(x in tail for x in ('me admin exemple','maitre admin exemple','cabinet exemple')):our_score+=3
        if any(x in name for x in ('conclusions adverse','conclusions adverses','partie adverse')):opponent_score+=6
        if any(x in name for x in ('recu adverse','adverse recu','conclusions en defense reçues')):opponent_score+=4
        side='ours' if our_score>opponent_score and our_score>=3 else ('opponent' if opponent_score>our_score and opponent_score>=3 else 'unknown')
        dates=[]
        for y,m,d in re.findall(r'\b(20\d{2})[-_. ](0?[1-9]|1[0-2])[-_. ](0?[1-9]|[12]\d|3[01])\b',name):
            try:dates.append(datetime(int(y),int(m),int(d),tzinfo=timezone.utc).timestamp())
            except ValueError:pass
        result.append({'path':item['path'],'etag':item.get('etag',''),'modified':item.get('modified',''),
          'size':len(raw),'sha256':_sha(raw),'content_sha256':digest(text),'text':text,
          'side':side,'side_scores':{'ours':our_score,'opponent':opponent_score},
          'rank':max(dates,default=_modified(item)),'format':PurePosixPath(item['path']).suffix.lower(),
          'page_count':len(pages),'page_citations':[{k:x[k] for k in ('page','label','citation_kind','extraction')} for x in pages]})
    return result


def _choose(candidates,side,explicit=''):
    if explicit:
        path=clean_path(explicit);found=[x for x in candidates if clean_path(x['path'])==path]
        if len(found)!=1:raise Stop('conclusions_partie_absentes')
        if found[0]['side'] not in (side,'unknown'):raise Stop('conclusions_partie_mal_identifiees')
        return found[0],[],'explicit'
    pool=[x for x in candidates if x['side']==side]
    pool.sort(key=lambda x:(x['rank'],x['format']=='.docx',x['path']),reverse=True)
    if not pool:
        if side=='opponent':
            return {'path':'','etag':'','modified':'','size':0,'sha256':_sha(b'conclusions_adverses_absentes'),
                'content_sha256':digest(''),'text':'','side':'absent','side_scores':{},
                'rank':0,'format':''},[],'absent'
        raise Stop('nos_conclusions_a_selectionner')
    top=pool[0];ties=[x for x in pool[1:] if x['rank']==top['rank']]
    if ties:raise Stop('plusieurs_dernieres_conclusions_partie')
    return top,pool[1:6],'certain'


def identify_party_writings(desk,args,dav=None,items=None):
    ensure_schema(desk);matter=_resolve_matter(desk,args.get('matter',''));client=dav or _dav(desk)
    inventory=items if items is not None else _inventory(client,matter['path'])
    candidates=_conclusion_candidates(desk,matter,client,inventory)
    ours,our_alt,our_mode=_choose(candidates,'ours',args.get('our_source_path',''))
    opponent,opp_alt,opp_mode=_choose(candidates,'opponent',args.get('opponent_source_path',''))
    if ours['sha256']==opponent['sha256']:raise Stop('memes_conclusions_pour_les_deux_parties')
    def public(row):return {k:v for k,v in row.items() if k not in ('text','rank')}
    return {'matter':{'id':matter['id'],'name':matter.get('client_name',''),'path':matter['path']},
      'certainty':'certain' if opponent['side']!='absent' else 'partiel_sans_conclusions_adverses',
      'our_latest':public(ours),'opponent_latest':public(opponent),
      'our_selection':our_mode,'opponent_selection':opp_mode,
      'our_alternatives':[public(x) for x in our_alt],
      'opponent_alternatives':[public(x) for x in opp_alt],
      'unknown_candidates':[public(x) for x in candidates if x['side']=='unknown'],
      '_our':ours,'_opponent':opponent}


def upload_writing(desk,raw,filename,matter_id,side,dav=None):
    """Add a lawyer-supplied act to the exact case, never replacing a source."""
    from .improvements36 import matter_label
    if side not in ('ours','opponent'):raise Stop('partie_conclusions_invalide')
    matter=_resolve_matter(desk,matter_id)
    name=PurePosixPath(str(filename or '').replace('\\','/')).name
    ext=PurePosixPath(name).suffix.lower()
    if not name or len(name)>180 or ext not in ('.docx','.pdf') or not 0<len(raw)<=20_000_000:
        raise Stop('fichier_conclusions_docx_pdf_20mo_requis')
    if ext=='.pdf' and not raw.startswith(b'%PDF-'):
        raise Stop('pdf_conclusions_invalide')
    if ext=='.docx' and not raw.startswith(b'PK'):
        raise Stop('docx_conclusions_invalide')
    pages=extract_pages(raw,name,{**desk.c.get('documents',{}),'max_file_bytes':20_000_000,
      'max_file_bytes_long':20_000_000,'max_document_chars_long':2_000_000,
      'max_pdf_pages_long':400})
    text='\n'.join(x['text'] for x in pages)
    if not str(text).strip():raise Stop('conclusions_sans_texte_lisible')
    folder=clean_path(matter['path']+'/60_Audiences/90_AxiorHub_Sources')
    if not under(folder,matter['path']):raise Stop('destination_audience_invalide')
    client=dav or _dav(desk)
    label='Nos conclusions' if side=='ours' else 'Conclusions adverses'
    # The unique suffix and DAV If-None-Match prevent any accidental overwrite.
    path=clean_path(folder+'/'+label+' - import avocat - '+secrets.token_hex(8)+ext)
    client.ensure_folder(folder,matter['path'])
    client.put_file(path,raw,'application/pdf' if ext=='.pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    item=next((x for x in client.list_folder(folder) if x.get('path')==path and not x.get('directory')),None)
    if not item or _sha(client.download(item))!=_sha(raw):
        desk.audit('conclusions_televersees_non_verifiees',{'path':path,'matter':matter['id'],'side':side})
        raise Stop('conclusions_televersees_verification_incertaine')
    desk.audit('conclusions_televersees',{'path':path,'matter':matter['id'],'side':side,'sha256':_sha(raw)})
    return {'matter':matter['id'],'matter_name':matter_label(matter),'side':side,'path':path,
        'sha256':_sha(raw),'text_characters':len(text),'pages':len(pages),
        'citations':[{k:x[k] for k in ('page','label','citation_kind','extraction')} for x in pages],
        'original_unchanged':True}


def _device(text):
    folded=fold(text);matches=list(re.finditer(r'\bpar ces motifs\b',folded))
    if not matches:return {'found':False,'text':'','clauses':[]}
    start=matches[-1].start();value=text[start:]
    stop=re.search(r'\n\s*(?:bordereau|liste des pieces|sous toutes reserves)\b',fold(value))
    if stop:value=value[:stop.start()]
    clauses=[]
    for line in re.split(r'[\n\r]+|(?<=[.;:])\s+(?=[A-ZÀ-ÖØ-Ý])',value):
        line=re.sub(r'^[-•*\d.)\s]+','',line).strip()
        if len(line)>=8:clauses.append(line)
    return {'found':True,'text':value.strip(),'clauses':clauses[:80]}


def compare_devices(desk,args,dav=None):
    selected=identify_party_writings(desk,args,dav=dav);ours=_device(selected['_our']['text']);opponent=_device(selected['_opponent']['text'])
    common=[];ours_only=[];opponent_only=[];used=set()
    for left in ours['clauses']:
        best=(-1,0)
        for i,right in enumerate(opponent['clauses']):
            if i in used:continue
            score=SequenceMatcher(None,fold(left),fold(right)).ratio()
            if score>best[1]:best=(i,score)
        if best[0]>=0 and best[1]>=0.72:
            used.add(best[0]);common.append({'ours':left,'opponent':opponent['clauses'][best[0]],'similarity':round(best[1],3)})
        else:ours_only.append(left)
    opponent_only=[x for i,x in enumerate(opponent['clauses']) if i not in used]
    return {'matter':selected['matter'],'sources':{'ours':selected['our_latest'],'opponent':selected['opponent_latest']},
      'our_device':ours,'opponent_device':opponent,'comparison':{'similar':common,
        'ours_only':ours_only,'opponent_only':opponent_only},
      'warning':'Comparaison lexicale déterministe ; elle ne qualifie ni la recevabilité ni le bien-fondé des demandes.'}


def _validate_preparation(result,valid,official):
    validate(result,HEARING_PREPARATION);ids=result['procedural_source_ids']+result['source_ids']
    for row in result['argument_matrix']:
        ids+=row['our_source_ids']+row['opponent_source_ids']+row['authority_source_ids']
        if any(x not in official for x in row['authority_source_ids']):raise Stop('jurisprudence_audience_non_officielle')
    for field,maximum in (('oral_plan_5',300),('oral_plan_10',600),('oral_plan_20',1200)):
        total=0
        for row in result[field]:total+=row['duration_seconds'];ids+=row['source_ids']
        if total>maximum:raise Stop('plan_plaidoirie_trop_long')
    for row in result['likely_questions']:ids+=row['source_ids']
    for row in result['exhibits_to_take']:ids+=row['source_ids']
    if any(x not in valid for x in ids):raise Stop('source_audience_invalide')


def _slug(value):
    value=unicodedata.normalize('NFKD',str(value)).encode('ascii','ignore').decode()
    return re.sub(r'[^A-Za-z0-9]+','_',value).strip('_')[:70] or 'DOSSIER'


def _outputs(matter,destination,day):
    label=_slug(matter.get('client_name') or matter['id'])
    return [clean_path(destination+'/'+x+'_'+label+'_'+day+ext) for x,ext in (
      ('Dossier_de_plaidoirie','.docx'),('Matrice_contradictoire','.docx'),
      ('Plans_de_plaidoirie_5_10_20_minutes','.docx'),('Pieces_a_emporter','.docx'),
      ('Rapport_sources_audience','.pdf'))]


def prepare_hearing(desk,args,dav=None,model=None):
    ensure_schema(desk);instruction=str(args.get('instruction','')).strip()
    if not instruction or len(instruction)>5000:raise Stop('instruction_audience_invalide')
    selected=identify_party_writings(desk,args,dav=dav);matter=_resolve_matter(desk,selected['matter']['id'])
    ours=selected.pop('_our');opponent=selected.pop('_opponent');devices={'ours':_device(ours['text']),'opponent':_device(opponent['text'])}
    from .long_context363 import analyze_writing
    from .context import payload_size
    writer=model or Model(routed_config(desk.c,'hearing'))
    limit=desk.c.get('ollama',{}).get('max_context_chars',65000)
    our_packet,our_coverage=analyze_writing(ours['text'],'our-writing-'+ours['sha256'][:20],
        ours['path'],'Préparer notre plaidoirie : demandes, moyens, dates, pièces et dispositif. '+instruction,writer,limit,desk)
    opp_packet,opp_coverage=analyze_writing(opponent['text'],'opponent-writing-'+opponent['sha256'][:20],
        opponent['path'],'Identifier contradictoirement les demandes, moyens, dates, pièces, objections et dispositif. '+instruction,writer,limit,desk)
    packet=our_packet+opp_packet
    legal_mcp={'status':'not_configured','connectors':[],'verified_authorities':[],
      'warning':'Aucun connecteur MCP juridique activé pour cette fonction.'}
    try:
        from .legal_research import research_enabled_mcp
        legal_mcp=research_enabled_mcp(desk,matter['id'],
          'Rechercher le droit et la jurisprudence utiles à la préparation de cette plaidoirie : '+instruction,8)
    except Stop as error:
        legal_mcp={'status':'partial','connectors':[],'verified_authorities':[],
          'error':str(error),'warning':'La vérification MCP n’a pas abouti ; aucune source non vérifiée ne sera citée.'}
    from .legal_research import ensure_schema as ensure_legal
    ensure_legal(desk)
    for row in desk.db.execute("SELECT * FROM legal_authorities_v240 WHERE matter=? AND verification_status='verified' ORDER BY retrieved DESC LIMIT 20",(matter['id'],)):
        item=dict(row);packet.append({'id':'authority-'+item['id'][:20],'kind':'official_legal_source',
          'path':item['official_url'],'excerpt':item['exact_excerpt'],'authority_id':item['id'],
          'official_text_sha256':item['official_text_sha256']})
    index=__import__('agent.index',fromlist=['DocumentIndex']).DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
    ranked,_=index.ranked_chunks(matter,[instruction,'audience plaidoirie arguments pièces dispositif'],limit=24)
    for row,score in ranked:
        sid='knowledge-'+row[1][:16]+'-'+str(row[6])
        if sid not in {x['id'] for x in packet}:packet.append({'id':sid,'kind':row[5],'path':row[2],'excerpt':row[7]})
    prompt={'matter':selected['matter'],'instruction':instruction,'devices':devices,'sources':packet,
      'rules':{'adversarial_attribution':True,'official_law_only':True,'internal_project_only':True,
         'opponent_writing_available':bool(opponent['text'])}}
    from .learning392 import learning_context
    prompt['apprentissage_metier']=learning_context(desk,matter['id'],'hearing')
    omitted=0
    while payload_size(prompt)>limit:
        optional=next((i for i in range(len(packet)-1,-1,-1)
            if packet[i]['kind'] not in ('latest_writing','opponent_latest_writing')),None)
        if optional is None:raise Stop('conclusions_et_dispositifs_depassent_contexte')
        packet.pop(optional);omitted+=1
    valid={x['id'] for x in packet};official={x['id'] for x in packet if x['kind']=='official_legal_source'}
    result=writer.ask('hearing_preparation',prompt)
    _validate_preparation(result,valid,official)
    if omitted:result['limits'].append(str(omitted)+' extrait(s) du dossier ou de la recherche juridique non retenu(s) : corpus documentaire non exhaustif.')
    if not opponent['text']:result['limits'].append('Conclusions adverses absentes : analyse contradictoire et préparation provisoires ; aucun fichier ne peut être confirmé.')
    comparison=compare_devices(desk,{**args,'matter':matter['id']},dav=dav)
    opponent_available=bool(opponent['text'])
    control={'status':'passed','latest_writings_certain':opponent_available,
      'provisional_without_opponent_writing':not opponent_available,
      'different_party_files':(ours['sha256']!=opponent['sha256']) if opponent_available else True,
      'devices_extracted':devices['ours']['found'] and (devices['opponent']['found'] if opponent_available else True),
      'sources_valid':True,'official_authorities_only':True,'oral_durations_valid':True}
    if not control['devices_extracted']:control['status']='blocked'
    subfolder=str(desk.c.get('hearing',{}).get('destination_subfolder','60_Audiences/90_AxiorHub_Brouillons')).strip('/')
    destination=clean_path(matter['path']+'/'+subfolder)
    if not under(destination,matter['path']) or destination==matter['path']:raise Stop('destination_audience_invalide')
    pid=secrets.token_hex(16);day=datetime.now(timezone.utc).date().isoformat();outputs=_outputs(matter,destination,day)
    data={'project_id':pid,'matter':selected['matter'],'instruction':instruction,
      'coverage':{'our_writing':our_coverage,'opponent_writing':opp_coverage,'optional_sources_omitted':omitted},
      'writings':{'ours':{k:v for k,v in ours.items() if k not in ('text','rank')},
        'opponent':{k:v for k,v in opponent.items() if k not in ('text','rank')}},
      'device_comparison':comparison['comparison'],'preparation':result,'sources':packet,
      'legal_mcp_verification':legal_mcp,
      'control':control,'destination_folder':destination,'future_files':outputs,
      'safety':{'new_files_only':True,'no_overwrite':True,'no_rpva':True,'no_email':True,'no_signature':True}}
    preview_hash=_sha(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    status='pending' if control['status']=='passed' else 'blocked';code=f'{secrets.randbelow(1_000_000):06d}' if status=='pending' else ''
    created=desk.now();expires=(datetime.now(timezone.utc)+timedelta(minutes=int(desk.c.get('hearing',{}).get('approval_minutes',60)))).isoformat()
    desk.db.execute('INSERT INTO hearing_projects_v250 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
      (pid,matter['id'],status,instruction,json.dumps(data,ensure_ascii=False),preview_hash,
       digest(pid+'|'+code) if code else '',created,expires,'',''))
    for side,row in (('ours',ours),('opponent',opponent)):
        desk.db.execute('INSERT INTO hearing_writings_v250 VALUES(?,?,?,?,?,?,?,?)',
          (pid,side,row['path'],row['etag'],row['modified'],row['sha256'],row['content_sha256'],created))
    desk.db.commit();desk.audit('hearing_project_prepared',{'project':pid,'matter':matter['id'],'status':status})
    response={'hearing_project_id':pid,'status':status,**{k:v for k,v in data.items() if k not in ('project_id','sources')},
      'created':created,'expires':expires,'files_created':False}
    if code:response['confirmation_code']=code
    return response


def preview_hearing(desk,pid):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(pid)):raise Stop('projet_audience_invalide')
    row=desk.db.execute('SELECT * FROM hearing_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_audience_absent')
    data=json.loads(row['data']);return {'hearing_project_id':pid,'status':row['status'],
      **{k:v for k,v in data.items() if k not in ('project_id','sources')},'created':row['created'],'expires':row['expires']}


def _lines(data):
    p=data['preparation'];rows=[p['title'],'',p['procedural_context'],'','MATRICE CONTRADICTOIRE']
    for x in p['argument_matrix']:
        rows += [x['issue'],'Notre position : '+x['our_position'],'Position adverse : '+x['opponent_position'],
          'Réponse proposée : '+x['proposed_response'],'Sources : '+', '.join(x['our_source_ids']+x['opponent_source_ids']+x['authority_source_ids']),'']
    return rows


def _plan_lines(p):
    rows=[]
    for field,label in (('oral_plan_5','PLAN 5 MINUTES'),('oral_plan_10','PLAN 10 MINUTES'),('oral_plan_20','PLAN 20 MINUTES')):
        rows += [label]
        for x in p[field]:rows.append(str(x['sequence'])+'. '+x['heading']+' ('+str(x['duration_seconds'])+' s) — '+x['message'])
        rows.append('')
    rows += ['QUESTIONS PROBABLES']
    for x in p['likely_questions']:rows += ['Question : '+x['question'],'Réponse proposée : '+x['proposed_answer']]
    return rows


def _build_files(data):
    p=data['preparation'];matrix=[]
    for x in p['argument_matrix']:matrix += [x['issue'],'Notre position : '+x['our_position'],
      'Position adverse : '+x['opponent_position'],'Réponse proposée : '+x['proposed_response'],'']
    pieces=[x['label']+' — '+x['reason_to_take']+' — sources '+', '.join(x['source_ids']) for x in p['exhibits_to_take']]
    report=['RAPPORT SOURCES AUDIENCE','Projet : '+data['project_id'],'Dossier : '+data['matter']['id'],
      'Nos conclusions : '+data['writings']['ours']['path'],'SHA-256 : '+data['writings']['ours']['sha256'],
      'Conclusions adverses : '+data['writings']['opponent']['path'],'SHA-256 : '+data['writings']['opponent']['sha256'],
      'Contrôle : '+json.dumps(data['control'],ensure_ascii=False),'Aucun dépôt, envoi ou fichier source modifié.']
    ctype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return [(data['future_files'][0],_simple_docx('DOSSIER DE PLAIDOIRIE — PROJET',_lines(data)),ctype),
      (data['future_files'][1],_simple_docx('MATRICE CONTRADICTOIRE — PROJET',matrix),ctype),
      (data['future_files'][2],_simple_docx('PLANS DE PLAIDOIRIE — PROJETS',_plan_lines(p)),ctype),
      (data['future_files'][3],_simple_docx('PIÈCES À EMPORTER — LISTE À CONTRÔLER',pieces+p['hearing_checklist']),ctype),
      (data['future_files'][4],_pdf(report),'application/pdf')]


def confirm_hearing(desk,args,dav=None):
    ensure_schema(desk);pid=str(args.get('project_id',''));code=str(args.get('confirmation_code',''))
    if not re.fullmatch(r'[a-f0-9]{32}',pid) or not re.fullmatch(r'\d{6}',code):raise Stop('confirmation_audience_invalide')
    row=desk.db.execute('SELECT * FROM hearing_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row or row['status'] not in ('pending','creating','partial'):raise Stop('projet_audience_non_confirmable')
    if row['status']=='pending' and datetime.now(timezone.utc)>=datetime.fromisoformat(row['expires']):raise Stop('projet_audience_expire')
    if not secrets.compare_digest(row['challenge_hash'],digest(pid+'|'+code)):raise Stop('confirmation_audience_invalide')
    data=json.loads(row['data']);matter=_resolve_matter(desk,data['matter']['id'])
    if clean_path(args.get('destination_folder',''))!=data['destination_folder']:raise Stop('destination_audience_non_confirmee')
    if clean_path(args.get('our_source_path',''))!=data['writings']['ours']['path'] or clean_path(args.get('opponent_source_path',''))!=data['writings']['opponent']['path']:
        raise Stop('conclusions_parties_non_confirmees')
    client=dav or _dav(desk);inventory=_inventory(client,matter['path'])
    for side in ('ours','opponent'):
        expected=data['writings'][side];item=next((x for x in inventory if x.get('path')==expected['path']),None)
        if side=='opponent' and not expected.get('path'):
            continue
        if not item or (expected['etag'] and item.get('etag')!=expected['etag']) or _sha(client.download(item))!=expected['sha256']:
            raise Stop('conclusions_partie_modifiees')
    files=_build_files(data);client.ensure_folder(data['destination_folder'],matter['path'])
    present={x['path']:x for x in client.list_folder(data['destination_folder']) if not x.get('directory')}
    for path,raw,ctype in files:
        journal=desk.db.execute('SELECT status,sha256 FROM hearing_creation_files_v250 WHERE project_id=? AND path=?',(pid,path)).fetchone()
        if path in present and (not journal or journal['status'] not in ('writing','created')):
            raise Stop('nom_fichier_deja_existant')
        desk.db.execute('INSERT OR IGNORE INTO hearing_creation_files_v250 VALUES(?,?,?,?,?,?,?)',
          (pid,path,'pending',_sha(raw),len(raw),ctype,desk.now()))
    desk.db.commit();created=[];warnings=[];desk.db.execute("UPDATE hearing_projects_v250 SET status='creating',decided=? WHERE id=?",(desk.now(),pid));desk.db.commit()
    for path,raw,ctype in files:
        journal=desk.db.execute('SELECT * FROM hearing_creation_files_v250 WHERE project_id=? AND path=?',(pid,path)).fetchone()
        if path in present:
            remote=client.download(present[path])
            if _sha(remote)!=journal['sha256']:raise Stop('reprise_fichier_audience_incoherente')
        else:
            desk.db.execute("UPDATE hearing_creation_files_v250 SET status='writing',updated=? WHERE project_id=? AND path=?",(desk.now(),pid,path));desk.db.commit()
            try:client.put_file(path,raw,ctype)
            except Stop as ex:warnings.append({'path':path,'error':str(ex)});break
        desk.db.execute("UPDATE hearing_creation_files_v250 SET status='created',updated=? WHERE project_id=? AND path=?",(desk.now(),pid,path));desk.db.commit()
        created.append({'path':path,'sha256':journal['sha256'],'bytes':journal['bytes']})
    all_created=[dict(x) for x in desk.db.execute("SELECT path,sha256,bytes FROM hearing_creation_files_v250 WHERE project_id=? AND status='created' ORDER BY path",(pid,))]
    status='done' if len(all_created)==len(files) else 'partial'
    result={'created_files':all_created,'warnings':warnings,'files_overwritten':0,'source_files_unchanged':True,
      'email_sent':False,'rpva_filed':False,'signature_added':False}
    desk.db.execute("UPDATE hearing_projects_v250 SET status=?,result=? WHERE id=?",(status,json.dumps(result),pid));desk.db.commit()
    desk.audit('hearing_files_created',{'project':pid,'matter':matter['id'],'status':status,'paths':[x['path'] for x in all_created]})
    return result


def reject_hearing(desk,pid):
    ensure_schema(desk);row=desk.db.execute('SELECT status FROM hearing_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_audience_absent')
    if row['status'] not in ('pending','blocked'):raise Stop('projet_audience_deja_traite')
    desk.db.execute("UPDATE hearing_projects_v250 SET status='rejected',decided=? WHERE id=?",(desk.now(),pid));desk.db.commit()
    return {'hearing_project_id':pid,'status':'rejected','files_created':False}


def perform(desk,kind,args):
    if kind=='prepare_hearing':return prepare_hearing(desk,args)
    if kind=='create_hearing_files':return confirm_hearing(desk,args)
    if kind=='identify_party_writings':return {k:v for k,v in identify_party_writings(desk,args).items() if not k.startswith('_')}
    if kind=='compare_devices':return compare_devices(desk,args)
    raise Stop('action_inconnue')

"""Deterministic OOXML legal-document revision engine for AxiorHub 2.6.0.

Preparation is local and read-only.  Confirmation creates a clean DOCX, a DOCX
with native tracked revisions, a hash-bound exhibit schedule and a PDF change
report.  Existing Nextcloud files are never overwritten.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from io import BytesIO
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

from .common import Stop, clean_path, digest, under
from .documents import extract
from .model import Model, WORD_REVISION_PLAN, routed_config, validate
from .document_projects import _blank_docx, _dav, _inventory, _pdf, _resolve_matter

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
ET.register_namespace('w',W);ET.register_namespace('r',R)

SCHEMAS=["""
CREATE TABLE IF NOT EXISTS word_projects_v250(
  id TEXT PRIMARY KEY, matter TEXT NOT NULL, status TEXT NOT NULL,
  instruction TEXT NOT NULL, data TEXT NOT NULL, preview_hash TEXT NOT NULL,
  challenge_hash TEXT NOT NULL, created TEXT NOT NULL, expires TEXT NOT NULL,
  decided TEXT NOT NULL, result TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS word_projects_v250_matter
  ON word_projects_v250(matter,created DESC);
CREATE TABLE IF NOT EXISTS word_creation_files_v250(
  project_id TEXT NOT NULL, path TEXT NOT NULL, status TEXT NOT NULL,
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, content_type TEXT NOT NULL,
  updated TEXT NOT NULL, PRIMARY KEY(project_id,path));
"""]


def ensure_schema(desk):
    for sql in SCHEMAS:desk.db.executescript(sql)
    desk.db.commit()


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _norm(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value or ''))).strip()


def _safe(value,maximum=5000):
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',str(value or '')).strip()
    if not value or len(value)>maximum:raise Stop('instruction_word_invalide')
    return value


def _document_xml(raw):
    if not raw.startswith(b'PK'):raise Stop('source_word_docx_requise')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            names=set(z.namelist())
            if 'word/document.xml' not in names:raise Stop('source_word_docx_invalide')
            if any(name.lower().endswith('vbaproject.bin') for name in names):
                raise Stop('source_word_avec_macro_refusee')
            return z.read('word/document.xml'),names
    except (zipfile.BadZipFile,KeyError,ET.ParseError):
        raise Stop('source_word_docx_invalide') from None


def _paragraph_text(p):
    values=[]
    for node in p.iter():
        if node.tag in ('{'+W+'}t','{'+W+'}delText','{'+W+'}instrText') and node.text:
            values.append(node.text)
        elif node.tag=='{'+W+'}tab':values.append('\t')
        elif node.tag=='{'+W+'}br':values.append('\n')
    return ''.join(values).strip()


def _val(node,name):
    return node.get('{'+W+'}'+name,'') if node is not None else ''


def inspect_docx(raw):
    document,names=_document_xml(raw)
    try:root=ET.fromstring(document)
    except ET.ParseError:raise Stop('source_word_docx_invalide') from None
    paragraphs=[]
    for pos,p in enumerate(root.findall('.//{'+W+'}body/{'+W+'}p')):
        ppr=p.find('{'+W+'}pPr');style=_val(ppr.find('{'+W+'}pStyle') if ppr is not None else None,'val')
        numpr=ppr.find('{'+W+'}numPr') if ppr is not None else None
        numid=_val(numpr.find('{'+W+'}numId') if numpr is not None else None,'val')
        level=_val(numpr.find('{'+W+'}ilvl') if numpr is not None else None,'val')
        text=_paragraph_text(p)
        paragraphs.append({'position':pos,'text':text,'style_id':style,
          'numbering_id':numid,'numbering_level':level,
          'sha256':digest(_norm(text)) if text else ''})
    styles=[]
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            if 'word/styles.xml' in names:
                tree=ET.fromstring(z.read('word/styles.xml'))
                styles=sorted({_val(x,'styleId') for x in tree.findall('.//{'+W+'}style') if _val(x,'styleId')})
    except (zipfile.BadZipFile,ET.ParseError):raise Stop('source_word_docx_invalide') from None
    return {'paragraphs':paragraphs,'available_styles':styles,
      'paragraph_count':len(paragraphs),'has_numbering':'word/numbering.xml' in names,
      'has_headers':any(x.startswith('word/header') for x in names),
      'has_footers':any(x.startswith('word/footer') for x in names)}


def _unique_anchor(catalog,text):
    needle=_norm(text);matches=[p for p in catalog if _norm(p['text'])==needle]
    if not needle or len(matches)!=1:
        raise Stop('ancre_word_absente' if not matches else 'ancre_word_ambigue')
    return matches[0]['position']


def validate_plan(plan,catalog,styles,valid_sources):
    validate(plan,WORD_REVISION_PLAN)
    ids=[];targets=set();known_ids=set()
    for edit in plan['edits']:
        pos=_unique_anchor(catalog,edit['anchor_text']);targets.add(pos)
        pid=str(edit['paragraph_id'])
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',pid) or pid in known_ids:
            raise Stop('identifiant_paragraphe_word_invalide')
        known_ids.add(pid);ids+=edit['source_ids']
        if edit['style_id'] and edit['style_id'] not in styles:raise Stop('style_word_absent')
        if edit['operation']!='delete' and not edit['text'].strip():raise Stop('texte_modification_word_absent')
    ids+=plan['source_ids']
    for item in plan['exhibits']:ids+=item['source_ids']
    if any(x not in valid_sources for x in ids):raise Stop('source_modification_word_invalide')
    refs=[]
    for edit in plan['edits']:
        refs+=re.findall(r'\[\[REF:([A-Za-z][A-Za-z0-9_-]{0,63})\|[^\]]+\]\]',edit['text'])
    if any(x not in known_ids for x in refs):raise Stop('renvoi_interne_word_absent')
    return {'anchors_unique':True,'styles_valid':True,'paragraph_ids_unique':True,
      'internal_references_valid':True,'source_ids_valid':True,'affected_paragraphs':len(targets)}


def _bookmark_name(pid):return 'AX_'+re.sub(r'[^A-Za-z0-9_]','_',pid)[:36]


def _add_text(parent,text,bookmarks):
    pattern=re.compile(r'\[\[REF:([A-Za-z][A-Za-z0-9_-]{0,63})\|([^\]]+)\]\]')
    last=0
    for match in pattern.finditer(text):
        if match.start()>last:
            run=ET.SubElement(parent,'{'+W+'}r');node=ET.SubElement(run,'{'+W+'}t')
            node.set('{http://www.w3.org/XML/1998/namespace}space','preserve');node.text=text[last:match.start()]
        link=ET.SubElement(parent,'{'+W+'}hyperlink',{'{'+W+'}anchor':bookmarks[match.group(1)]})
        run=ET.SubElement(link,'{'+W+'}r');rpr=ET.SubElement(run,'{'+W+'}rPr')
        ET.SubElement(rpr,'{'+W+'}rStyle',{'{'+W+'}val':'Hyperlink'})
        node=ET.SubElement(run,'{'+W+'}t');node.text=match.group(2)
        last=match.end()
    if last<len(text) or not text:
        run=ET.SubElement(parent,'{'+W+'}r');node=ET.SubElement(run,'{'+W+'}t')
        node.set('{http://www.w3.org/XML/1998/namespace}space','preserve');node.text=text[last:]


def _paragraph_properties(paragraphs,index,operation,style_id):
    source=paragraphs[index]
    if operation=='insert_after' and index+1<len(paragraphs):source=paragraphs[index+1]
    ppr=source.find('{'+W+'}pPr')
    result=deepcopy(ppr) if ppr is not None else ET.Element('{'+W+'}pPr')
    if style_id:
        old=result.find('{'+W+'}pStyle')
        if old is not None:result.remove(old)
        result.insert(0,ET.Element('{'+W+'}pStyle',{'{'+W+'}val':style_id}))
    return result


def _new_paragraph(ppr,text,pid,bookmarks,tracked=False,author='AxiorHub',stamp=''):
    p=ET.Element('{'+W+'}p');p.append(deepcopy(ppr))
    bid=str(10000+len(bookmarks));name=bookmarks[pid]
    ET.SubElement(p,'{'+W+'}bookmarkStart',{'{'+W+'}id':bid,'{'+W+'}name':name})
    container=p
    if tracked:container=ET.SubElement(p,'{'+W+'}ins',{'{'+W+'}id':bid,'{'+W+'}author':author,'{'+W+'}date':stamp})
    _add_text(container,text,bookmarks)
    ET.SubElement(p,'{'+W+'}bookmarkEnd',{'{'+W+'}id':bid})
    return p


def _delete_runs(p,tracked,author,stamp,revision_id):
    old=_paragraph_text(p)
    for child in list(p):
        if child.tag!='{'+W+'}pPr':p.remove(child)
    if tracked and old:
        deleted=ET.SubElement(p,'{'+W+'}del',{'{'+W+'}id':str(revision_id),
          '{'+W+'}author':author,'{'+W+'}date':stamp})
        run=ET.SubElement(deleted,'{'+W+'}r');text=ET.SubElement(run,'{'+W+'}delText')
        text.set('{http://www.w3.org/XML/1998/namespace}space','preserve');text.text=old
    return old


def _render_document(raw,plan,tracked=False):
    document,names=_document_xml(raw);root=ET.fromstring(document);body=root.find('{'+W+'}body')
    paragraphs=body.findall('{'+W+'}p');catalog=inspect_docx(raw)['paragraphs']
    operations=[]
    for edit in plan['edits']:
        operations.append((_unique_anchor(catalog,edit['anchor_text']),edit))
    bookmarks={e['paragraph_id']:_bookmark_name(e['paragraph_id']) for _,e in operations}
    author='AxiorHub';stamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for position,edit in sorted(operations,key=lambda x:x[0],reverse=True):
        target=paragraphs[position];body_index=list(body).index(target)
        ppr=_paragraph_properties(paragraphs,position,edit['operation'],edit['style_id'])
        if edit['operation']=='insert_after':
            body.insert(body_index+1,_new_paragraph(ppr,edit['text'],edit['paragraph_id'],bookmarks,tracked,author,stamp))
        elif edit['operation']=='replace':
            if tracked:
                _delete_runs(target,True,author,stamp,20000+position)
                inserted=ET.SubElement(target,'{'+W+'}ins',{'{'+W+'}id':str(30000+position),
                  '{'+W+'}author':author,'{'+W+'}date':stamp})
                ET.SubElement(inserted,'{'+W+'}bookmarkStart',{'{'+W+'}id':str(40000+position),
                  '{'+W+'}name':bookmarks[edit['paragraph_id']]})
                _add_text(inserted,edit['text'],bookmarks)
                ET.SubElement(inserted,'{'+W+'}bookmarkEnd',{'{'+W+'}id':str(40000+position)})
            else:
                _delete_runs(target,False,author,stamp,position)
                ET.SubElement(target,'{'+W+'}bookmarkStart',{'{'+W+'}id':str(40000+position),
                  '{'+W+'}name':bookmarks[edit['paragraph_id']]})
                _add_text(target,edit['text'],bookmarks)
                ET.SubElement(target,'{'+W+'}bookmarkEnd',{'{'+W+'}id':str(40000+position)})
        else:
            if tracked:_delete_runs(target,True,author,stamp,20000+position)
            else:body.remove(target)
    return ET.tostring(root,encoding='utf-8',xml_declaration=True),names


def render_docx(raw,plan,tracked=False):
    document,names=_render_document(raw,plan,tracked);source=BytesIO(raw);out=BytesIO()
    missing_settings=tracked and 'word/settings.xml' not in names
    content_ns='http://schemas.openxmlformats.org/package/2006/content-types'
    rel_ns='http://schemas.openxmlformats.org/package/2006/relationships'
    settings_type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings'
    with zipfile.ZipFile(source) as zin,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            name=info.filename
            if name=='word/document.xml':continue
            if name.lower().endswith('vbaproject.bin'):continue
            data=zin.read(name)
            if name.endswith('.rels'):
                data=re.sub(br'<(?:[A-Za-z0-9_]+:)?Relationship\b(?=[^>]*\bTargetMode=["\']External["\'])[^>]*/>',b'',data,flags=re.I)
            if missing_settings and name=='[Content_Types].xml':
                try:
                    types=ET.fromstring(data)
                    if not any(x.get('PartName')=='/word/settings.xml' for x in types):
                        ET.SubElement(types,'{'+content_ns+'}Override',{'PartName':'/word/settings.xml',
                          'ContentType':'application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml'})
                    data=ET.tostring(types,encoding='utf-8',xml_declaration=True)
                except ET.ParseError:raise Stop('types_word_invalides') from None
            if missing_settings and name=='word/_rels/document.xml.rels':
                try:
                    rels=ET.fromstring(data);ids={x.get('Id','') for x in rels};number=1
                    while 'rIdAxiorHub'+str(number) in ids:number+=1
                    ET.SubElement(rels,'{'+rel_ns+'}Relationship',{
                      'Id':'rIdAxiorHub'+str(number),'Type':settings_type,'Target':'settings.xml'})
                    data=ET.tostring(rels,encoding='utf-8',xml_declaration=True)
                except ET.ParseError:raise Stop('relations_word_invalides') from None
            if tracked and name=='word/settings.xml':
                try:
                    settings=ET.fromstring(data)
                    if settings.find('{'+W+'}trackRevisions') is None:settings.insert(0,ET.Element('{'+W+'}trackRevisions'))
                    data=ET.tostring(settings,encoding='utf-8',xml_declaration=True)
                except ET.ParseError:raise Stop('parametres_word_invalides') from None
            zout.writestr(info,data)
        zout.writestr('word/document.xml',document)
        if missing_settings:
            zout.writestr('word/settings.xml',('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:settings xmlns:w="'+W+'"><w:trackRevisions/></w:settings>').encode())
            if 'word/_rels/document.xml.rels' not in names:
                zout.writestr('word/_rels/document.xml.rels',('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<Relationships xmlns="'+rel_ns+'"><Relationship Id="rIdAxiorHub1" Type="'+settings_type+'" Target="settings.xml"/></Relationships>').encode())
    return out.getvalue()


def _simple_docx(title,rows):
    from .document_projects import _docx
    values=[(title,True)]+[(x,False) for x in rows]
    return _docx(_blank_docx(),values)


def _paths(matter,destination,day):
    label=re.sub(r'[^A-Za-z0-9]+','_',unicodedata.normalize('NFKD',matter.get('client_name') or matter['id']).encode('ascii','ignore').decode()).strip('_')[:70]
    label=label or matter['id']
    return [clean_path(destination+'/'+name) for name in (
      'Conclusions_'+label+'_'+day+'_propre.docx',
      'Conclusions_'+label+'_'+day+'_comparee.docx',
      'Bordereau_pieces_'+label+'_'+day+'.docx',
      'Rapport_modifications_Word_'+label+'_'+day+'.pdf')]


def _public(data,row,include_code=''):
    result={'word_project_id':data['project_id'],'status':row['status'],'matter':data['matter'],
      'source_file':data['source'],'destination_folder':data['destination_folder'],
      'future_files':data['future_files'],'edit_plan':data['plan'],
      'deterministic_control':data['control'],'source_unchanged':True,
      'files_created':False,'files_overwritten':0,'created':row['created'],'expires':row['expires']}
    if include_code:result['confirmation_code']=include_code
    return result


def prepare_word_project(desk,args,dav=None,model=None):
    ensure_schema(desk);matter=_resolve_matter(desk,args.get('matter',''))
    instruction=_safe(args.get('instruction',''));source_path=str(args.get('source_path','')).strip()
    client=dav or _dav(desk);items=_inventory(client,matter['path'])
    if source_path:
        selected=next((x for x in items if x.get('path')==clean_path(source_path) and not x.get('directory')),None)
        if not selected:raise Stop('fichier_source_absent')
    else:
        from .legal_research import identify_latest_writings
        latest=identify_latest_writings(desk,{'matter':matter['id'],'document_type':'conclusions'},dav=client,items=items)
        if latest['certainty']!='certain':raise Stop('plusieurs_versions_source_possibles')
        selected=next((x for x in items if x.get('path')==latest['selected']['path']),None)
    if not selected or PurePosixPath(selected['path']).suffix.lower()!='.docx':raise Stop('source_word_docx_requise')
    raw=client.download(selected);catalog=inspect_docx(raw)
    base_text=extract(raw,PurePosixPath(selected['path']).name,desk.c['documents'])
    source={'path':selected['path'],'etag':selected.get('etag',''),'modified':selected.get('modified',''),
      'sha256':_sha(raw),'bytes':len(raw),'format':'.docx'}
    index=__import__('agent.index',fromlist=['DocumentIndex']).DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
    ranked,_=index.ranked_chunks(matter,[instruction,matter.get('client_name',''),'conclusions pièces échanges'],limit=28)
    packet=[{'id':'word-base-'+source['sha256'][:20],'kind':'base_document','path':source['path'],
      'excerpt':base_text[:45000],'etag':source['etag']}]
    for row,score in ranked:
        sid='knowledge-'+row[1][:16]+'-'+str(row[6])
        if sid not in {x['id'] for x in packet}:
            packet.append({'id':sid,'kind':row[5],'path':row[2],'excerpt':row[7],
              'etag':row[3],'modified':row[4],'score':round(score,4)})
    from .legal_research import ensure_schema as ensure_legal
    ensure_legal(desk)
    for item in desk.db.execute(
      "SELECT path,sha256,bytes,etag FROM exhibit_registry_v240 WHERE matter=? AND status='present' ORDER BY path",
      (matter['id'],)):
        sid='exhibit-'+item['sha256'][:20]
        if sid not in {x['id'] for x in packet}:
            packet.append({'id':sid,'kind':'registered_exhibit','path':item['path'],
              'excerpt':'Pièce présente ; contenu non retranscrit dans ce registre.',
              'sha256':item['sha256'],'bytes':item['bytes'],'etag':item['etag']})
    valid={x['id'] for x in packet};prompt={'matter':matter,'instruction':instruction,
      'source':source,'paragraphs':catalog['paragraphs'][:600],
      'available_styles':catalog['available_styles'],'sources':packet,
      'rules':{'new_files_only':True,'tracked_and_clean_versions':True,'exact_unique_anchors':True}}
    from .learning392 import learning_context
    prompt['apprentissage_metier']=learning_context(desk,matter['id'],'word_revision')
    writer=model or Model(routed_config(desk.c,'document_drafting'));plan=writer.ask('word_revision_plan',prompt)
    control=validate_plan(plan,catalog['paragraphs'],catalog['available_styles'],valid)
    day=datetime.now(timezone.utc).date().isoformat();subfolder=str(desk.c.get('word_legal',{}).get(
      'destination_subfolder','20_Actes_et_conclusions/90_AxiorHub_Brouillons')).strip('/')
    destination=clean_path(matter['path']+'/'+subfolder)
    if not under(destination,matter['path']) or destination==clean_path(matter['path']):raise Stop('destination_word_invalide')
    pid=secrets.token_hex(16);outputs=_paths(matter,destination,day)
    data={'project_id':pid,'matter':matter,'instruction':instruction,'source':source,
      'catalog_summary':{k:v for k,v in catalog.items() if k!='paragraphs'},'sources':packet,
      'plan':plan,'control':{**control,'status':'passed'},'destination_folder':destination,
      'future_files':outputs,'safety':{'new_files_only':True,'no_overwrite':True,
        'no_email':True,'no_rpva':True,'no_signature':True}}
    preview_hash=_sha(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    code=f'{secrets.randbelow(1_000_000):06d}';created=desk.now();expires=(datetime.now(timezone.utc)+timedelta(
      minutes=int(desk.c.get('word_legal',{}).get('approval_minutes',60)))).isoformat()
    desk.db.execute('INSERT INTO word_projects_v250 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
      (pid,matter['id'],'pending',instruction,json.dumps(data,ensure_ascii=False),preview_hash,
       digest(pid+'|'+code),created,expires,'',''));desk.db.commit()
    desk.audit('word_project_prepared',{'project':pid,'matter':matter['id'],'source_sha256':source['sha256'],
      'edit_count':len(plan['edits'])})
    row={'status':'pending','created':created,'expires':expires}
    return _public(data,row,code)


def preview_word_project(desk,pid):
    ensure_schema(desk)
    if not re.fullmatch(r'[a-f0-9]{32}',str(pid)):raise Stop('projet_word_invalide')
    row=desk.db.execute('SELECT * FROM word_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_word_absent')
    return _public(json.loads(row['data']),row)


def _exhibit_rows(desk,matter,plan):
    from .legal_research import ensure_schema as ensure_legal
    ensure_legal(desk);registered=[dict(x) for x in desk.db.execute(
      "SELECT path,sha256,bytes,status,duplicate_group FROM exhibit_registry_v240 WHERE matter=? AND status='present' ORDER BY path",(matter,))]
    by_source={x['sha256']:x for x in registered};rows=[]
    for pos,item in enumerate(plan['exhibits'],1):
        matches=[]
        for sid in item['source_ids']:
            for entry in registered:
                if sid in (entry['sha256'],'exhibit-'+entry['sha256'][:20],entry['path']):matches.append(entry)
        entry=matches[0] if len(matches)==1 else None
        rows.append('Pièce n° '+str(pos)+' — '+item['label']+
          (' — SHA-256 '+entry['sha256'] if entry else ' — EMPREINTE À CONFIRMER'))
    return rows


def _build_files(desk,data,raw):
    plan=data['plan'];clean=render_docx(raw,plan,False);compared=render_docx(raw,plan,True)
    bcp=_simple_docx('BORDEREAU DE COMMUNICATION DE PIÈCES — PROJET',_exhibit_rows(desk,data['matter']['id'],plan))
    report=['RAPPORT DE MODIFICATIONS WORD — AXIORHUB','Projet : '+data['project_id'],
      'Dossier : '+data['matter']['id'],'Source : '+data['source']['path'],
      'SHA-256 source : '+data['source']['sha256'],'','Modifications :']
    for pos,e in enumerate(plan['edits'],1):
        report += [str(pos)+'. '+e['operation']+' — '+e['reason'],
          '   Ancre : '+e['anchor_text'],'   Paragraphe : '+e['paragraph_id'],
          '   Sources : '+', '.join(e['source_ids'])]
    report += ['','Contrôles déterministes : '+json.dumps(data['control'],ensure_ascii=False),
      'Aucun fichier source modifié ; aucune signature, aucun envoi et aucun dépôt.']
    content='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return [(data['future_files'][0],clean,content),(data['future_files'][1],compared,content),
      (data['future_files'][2],bcp,content),(data['future_files'][3],_pdf(report),'application/pdf')]


def confirm_word_project(desk,args,dav=None):
    ensure_schema(desk);pid=str(args.get('project_id',''));code=str(args.get('confirmation_code',''))
    if not re.fullmatch(r'[a-f0-9]{32}',pid) or not re.fullmatch(r'\d{6}',code):raise Stop('confirmation_word_invalide')
    row=desk.db.execute('SELECT * FROM word_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_word_absent')
    if row['status'] not in ('pending','creating','partial'):raise Stop('projet_word_deja_traite')
    if row['status']=='pending' and datetime.now(timezone.utc)>=datetime.fromisoformat(row['expires']):raise Stop('projet_word_expire')
    if not secrets.compare_digest(row['challenge_hash'],digest(pid+'|'+code)):raise Stop('confirmation_word_invalide')
    data=json.loads(row['data']);canonical=_sha(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    if canonical!=row['preview_hash']:raise Stop('previsualisation_word_modifiee')
    if clean_path(args.get('source_path',''))!=data['source']['path']:raise Stop('source_word_non_confirmee')
    if clean_path(args.get('destination_folder',''))!=data['destination_folder']:raise Stop('destination_word_non_confirmee')
    matter=_resolve_matter(desk,data['matter']['id']);client=dav or _dav(desk);items=_inventory(client,matter['path'])
    selected=next((x for x in items if x.get('path')==data['source']['path']),None)
    if not selected or (data['source']['etag'] and selected.get('etag')!=data['source']['etag']):raise Stop('fichier_source_modifie')
    raw=client.download(selected)
    if _sha(raw)!=data['source']['sha256']:raise Stop('fichier_source_modifie')
    files=_build_files(desk,data,raw);client.ensure_folder(data['destination_folder'],matter['path'])
    present={x['path']:x for x in client.list_folder(data['destination_folder']) if not x.get('directory')}
    for path,blob,ctype in files:
        desk.db.execute('INSERT OR IGNORE INTO word_creation_files_v250 VALUES(?,?,?,?,?,?,?)',
          (pid,path,'pending',_sha(blob),len(blob),ctype,desk.now()))
    desk.db.commit();created=[];warnings=[]
    desk.db.execute("UPDATE word_projects_v250 SET status='creating',decided=? WHERE id=?",(desk.now(),pid));desk.db.commit()
    for path,blob,ctype in files:
        journal=desk.db.execute('SELECT * FROM word_creation_files_v250 WHERE project_id=? AND path=?',(pid,path)).fetchone()
        if path in present:
            if journal['status'] not in ('writing','created'):raise Stop('nom_fichier_deja_existant')
            remote=client.download(present[path])
            if _sha(remote)!=journal['sha256']:raise Stop('reprise_fichier_incoherente')
        else:
            desk.db.execute("UPDATE word_creation_files_v250 SET status='writing',updated=? WHERE project_id=? AND path=?",(desk.now(),pid,path));desk.db.commit()
            try:client.put_file(path,blob,ctype)
            except Stop as ex:warnings.append({'path':path,'error':str(ex)});break
        desk.db.execute("UPDATE word_creation_files_v250 SET status='created',updated=? WHERE project_id=? AND path=?",(desk.now(),pid,path));desk.db.commit()
        created.append({'path':path,'sha256':journal['sha256'],'bytes':journal['bytes']})
    all_created=[dict(x) for x in desk.db.execute("SELECT path,sha256,bytes FROM word_creation_files_v250 WHERE project_id=? AND status='created' ORDER BY path",(pid,))]
    status='done' if len(all_created)==len(files) else 'partial';result={'created_files':all_created,
      'warnings':warnings,'source_unchanged':True,'files_overwritten':0,'email_sent':False,'rpva_filed':False}
    desk.db.execute('UPDATE word_projects_v250 SET status=?,result=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),pid));desk.db.commit()
    desk.audit('word_files_created',{'project':pid,'matter':matter['id'],'status':status,'paths':[x['path'] for x in all_created]})
    return result


def reject_word_project(desk,pid):
    ensure_schema(desk);row=desk.db.execute('SELECT matter,status FROM word_projects_v250 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_word_absent')
    if row['status']!='pending':raise Stop('projet_word_deja_traite')
    desk.db.execute("UPDATE word_projects_v250 SET status='rejected',decided=? WHERE id=?",(desk.now(),pid));desk.db.commit()
    return {'word_project_id':pid,'status':'rejected','files_created':False}


def perform(desk,kind,args):
    if kind=='prepare_word_project':return prepare_word_project(desk,args)
    if kind=='create_word_files':return confirm_word_project(desk,args)
    raise Stop('action_inconnue')

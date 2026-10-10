"""Private, approved Word templates and immutable, previewed document creations.

No client data or template is shipped with the software. Files remain private
until an explicit confirmation creates a new file inside the selected matter.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .common import Stop, clean_path, digest, under
from . import portable
from .document_projects import _dav, _resolve_matter

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ET.register_namespace('w', W)
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
FIELDS = {'destinataire', 'qualite', 'adresse', 'reference', 'objet', 'date',
          'envoi', 'appel', 'corps', 'fin', 'signature'}
REQUIRED = {'destinataire', 'objet', 'date', 'envoi', 'appel', 'corps', 'fin'}
TOKEN = re.compile(r'\{\{([a-z_]+)\}\}')
SAMPLE = {'destinataire': 'Madame Martin (exemple fictif)', 'qualite': 'Dirigeante',
          'adresse': '10 rue du Test, 69000 Lyon', 'reference': 'DEMO-001',
          'objet': 'Objet fictif de vérification', 'date': '20 septembre 2026',
          'envoi': 'Par courrier électronique', 'appel': 'Madame,',
          'corps': 'Premier paragraphe fictif pour contrôler la mise en page.\n\nDeuxième paragraphe fictif.',
          'fin': 'Je vous prie d’agréer, Madame, l’expression de mes salutations distinguées.',
          'signature': 'Nom du cabinet (exemple fictif)'}
ENVOI = {'courriel': 'Par courrier électronique', 'simple': 'Par courrier simple',
         'recommande': 'Par lettre recommandée avec avis de réception'}
APPEL = {'madame': 'Madame,', 'monsieur': 'Monsieur,',
         'mixte': 'Madame, Monsieur,', 'confrere': 'Cher Confrère,',
         'consœur': 'Chère Consœur,'}
FIN = {'formelle': 'Je vous prie d’agréer, Madame, Monsieur, l’expression de mes salutations distinguées.',
       'confraternelle': 'Je vous prie de croire, Cher Confrère, en mes sentiments confraternels.'}
MAX_DOCX = 8_000_000
MAX_PAGES = 25


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS cabinet_templates_v330(
      id TEXT PRIMARY KEY, label TEXT NOT NULL, version INTEGER NOT NULL,
      sha256 TEXT NOT NULL, status TEXT NOT NULL, placeholders TEXT NOT NULL,
      pages TEXT NOT NULL, created TEXT NOT NULL, approved TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS cabinet_templates_v330_label ON cabinet_templates_v330(label,version);
    CREATE TABLE IF NOT EXISTS cabinet_projects_v330(
      id TEXT PRIMARY KEY, template_id TEXT NOT NULL, matter TEXT NOT NULL,
      kind TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
      preview_sha256 TEXT NOT NULL, challenge_hash TEXT NOT NULL,
      created TEXT NOT NULL, expires TEXT NOT NULL, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS cabinet_files_v330(
      project_id TEXT PRIMARY KEY, path TEXT NOT NULL, status TEXT NOT NULL,
      sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, updated TEXT NOT NULL);
    ''')
    desk.db.commit()


def _sha(data):return hashlib.sha256(data).hexdigest()


def _external_relationship(raw):
    return bool(re.search(rb'TargetMode\s*=\s*[\x22\x27]External[\x22\x27]',raw,re.I))


def _neutralize_hyperlinks(raw):
    """Remove ordinary external hyperlinks and attached template references.

    Any other external target remains prohibited by inspect_template. The
    cleaned copy, never the original bytes, is rendered and stored.
    """
    changed=0;modified={}
    link_ns='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        names=archive.namelist()
        if len(names)>1200 or len(names)!=len(set(names)) or sum(
            x.file_size for x in archive.infolist())>80_000_000 or any(
            x.file_size>30_000_000 for x in archive.infolist()):
            raise Stop('modele_word_trop_volumineux')
        for name in names:
            if not name.endswith('.rels'):continue
            data=archive.read(name)
            if not _external_relationship(data):continue
            if b'<!DOCTYPE' in data or b'<!ENTITY' in data:raise Stop('modele_word_xml_invalide')
            rels=ET.fromstring(data);removed={}
            for item in list(rels):
                if item.get('TargetMode','').lower()!='external':continue
                relation=item.get('Type','')
                if (not relation.endswith(('/hyperlink','/attachedTemplate')) or
                    (relation.endswith('/attachedTemplate') and name!='word/_rels/settings.xml.rels')):
                    raise Stop('lien_externe_modele_word_refuse')
                ident=item.get('Id','')
                if not ident:raise Stop('modele_word_docx_invalide')
                removed[ident]=relation.rsplit('/',1)[-1];rels.remove(item)
            if not removed:continue
            # OPC: word/_rels/document.xml.rels => word/document.xml
            parent,base=name.rsplit('/_rels/',1) if '/_rels/' in name else ('',name[6:])
            xml_name=(parent+'/' if parent else '')+base[:-5]
            if xml_name not in names or not xml_name.startswith('word/'):
                raise Stop('lien_externe_modele_word_refuse')
            xml=archive.read(xml_name)
            if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise Stop('modele_word_xml_invalide')
            root=ET.fromstring(xml)
            for element in root.iter():
                for child in list(element):
                    ident=child.get('{'+link_ns+'}id','')
                    if ident not in removed:continue
                    if removed[ident]=='attachedTemplate' and child.tag=='{'+W+'}attachedTemplate':
                        element.remove(child);changed+=1;continue
                    if removed[ident]!='hyperlink' or child.tag!='{'+W+'}hyperlink':
                        raise Stop('lien_externe_modele_word_refuse')
                    position=list(element).index(child);element.remove(child)
                    for run in list(child):
                        element.insert(position,run);position+=1
                    changed+=1
            modified[name]=ET.tostring(rels,encoding='utf-8',xml_declaration=True)
            modified[xml_name]=ET.tostring(root,encoding='utf-8',xml_declaration=True)
        if not modified:return raw,0
        out=BytesIO()
        with zipfile.ZipFile(out,'w') as target:
            for item in archive.infolist():
                target.writestr(item,modified.get(item.filename,archive.read(item.filename)))
        return out.getvalue(),changed


def _root(desk):return Path(desk.c['state_dir'])/'cabinet-documents-v330'


def _private_dir(path):
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(path,0o700)
    return path


def _private_file(path,raw):
    if not isinstance(raw,bytes):raise Stop('fichier_word_invalide')
    folder=_private_dir(path.parent)
    fd,temp=tempfile.mkstemp(prefix='.new-',dir=folder)
    try:
        with os.fdopen(fd,'wb') as dest:
            portable.fchmod(dest.fileno(),0o600)
            dest.write(raw);dest.flush();os.fsync(dest.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def inspect_template(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_DOCX:raise Stop('taille_modele_word_invalide')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            names=z.namelist()
            if len(names)>1200 or len(names)!=len(set(names)) or 'word/document.xml' not in names:
                raise Stop('modele_word_docx_invalide')
            if sum(x.file_size for x in z.infolist())>80_000_000 or any(
                x.file_size>30_000_000 for x in z.infolist()):raise Stop('modele_word_trop_volumineux')
            if any('vbaproject' in x.casefold() or x.startswith(('word/embeddings/','word/activeX/')) or
                   x.startswith('/') or '..' in PurePosixPath(x).parts for x in names):
                raise Stop('modele_word_actif_refuse')
            parts=[n for n in names if n=='word/document.xml' or
                   re.fullmatch(r'word/(?:header|footer)\d+\.xml',n)]
            placeholders=set();body_count=0;images=any(x.startswith('word/media/') for x in names)
            for name in names:
                if name.endswith('.rels') and _external_relationship(z.read(name)):
                    raise Stop('lien_externe_modele_word_refuse')
            for name in parts:
                xml=z.read(name)
                if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:raise Stop('modele_word_xml_invalide')
                root=ET.fromstring(xml)
                for p in root.iter('{'+W+'}p'):
                    txt=''.join(t.text or '' for t in p.iter('{'+W+'}t'))
                    found=set(TOKEN.findall(txt));placeholders.update(found)
                    if 'corps' in found:
                        body_count+=1
                        if name!='word/document.xml' or txt.strip()!='{{corps}}':
                            raise Stop('balise_corps_paragraphe_unique_requise')
                    if any(not re.fullmatch(r'[a-z_]+',t) or t not in FIELDS
                           for t in re.findall(r'\{\{([^{}]*)\}\}',txt)):
                        raise Stop('balise_modele_word_inconnue')
                instructions=' '.join([*(n.text or '' for n in root.iter('{'+W+'}instrText')),
                    *(n.get('{'+W+'}instr','') for n in root.iter('{'+W+'}fldSimple'))])
                if re.search(r'\b(?:DATE|TIME|CREATEDATE|SAVEDATE|PRINTDATE)\b',instructions,re.I):
                    raise Stop('date_word_dynamique_refusee')
            if body_count!=1:raise Stop('balise_corps_paragraphe_unique_requise')
            if placeholders-FIELDS or REQUIRED-placeholders:raise Stop(
                'balises_modele_word_incompletes_ou_inconnues')
            return {'placeholders':sorted(placeholders),'has_images':images,
                    'has_headers':any(x.startswith('word/header') for x in names),
                    'has_footers':any(x.startswith('word/footer') for x in names),
                    'has_tables':b'<w:tbl' in z.read('word/document.xml')}
    except (zipfile.BadZipFile,ET.ParseError,KeyError,UnicodeError):
        raise Stop('modele_word_docx_invalide') from None


def _fill_paragraph(p,values):
    nodes=list(p.iter('{'+W+'}t'))
    joined=''.join(n.text or '' for n in nodes)
    if not TOKEN.search(joined):return False
    if '{{corps}}' in joined:
        if joined.strip()!='{{corps}}':raise Stop('balise_corps_paragraphe_unique_requise')
        ppr=p.find('{'+W+'}pPr')
        first=p.find('.//{'+W+'}rPr')
        for child in list(p):p.remove(child)
        if ppr is not None:p.append(deepcopy(ppr))
        r=ET.SubElement(p,'{'+W+'}r')
        if first is not None:r.append(deepcopy(first))
        t=ET.SubElement(r,'{'+W+'}t');t.text=values['corps'].split('\n')[0]
        t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
        return values['corps'].split('\n')[1:]
    offsets=[];cursor=0
    for n in nodes:
        offsets.append(cursor);cursor+=len(n.text or '')
    for match in reversed(list(TOKEN.finditer(joined))):
        key=match.group(1)
        if key not in values:raise Stop('balise_modele_word_inconnue')
        start=next(i for i,n in enumerate(nodes) if offsets[i]<=match.start()<offsets[i]+len(n.text or ''))
        end=next(i for i,n in enumerate(nodes) if offsets[i]<match.end()<=offsets[i]+len(n.text or ''))
        begin=match.start()-offsets[start];stop=match.end()-offsets[end]
        if start==end:
            raw=nodes[start].text or ''
            nodes[start].text=raw[:begin]+values[key]+raw[stop:]
        else:
            left=nodes[start].text or '';right=nodes[end].text or ''
            nodes[start].text=left[:begin]+values[key]
            nodes[end].text=right[stop:]
            for i in range(start+1,end):nodes[i].text=''
        nodes[start].set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    return False


def fill_template(raw,values):
    info=inspect_template(raw)
    if any(name not in values for name in info['placeholders']):raise Stop('valeur_modele_word_absente')
    source=BytesIO(raw);out=BytesIO()
    with zipfile.ZipFile(source) as zin,zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as zout:
        for part in zin.infolist():
            name=part.filename;data=zin.read(name)
            if (name=='word/document.xml' or re.fullmatch(r'word/(?:header|footer)\d+\.xml',name)) and b'{{' in data:
                namespaces={prefix:uri for _,(prefix,uri) in ET.iterparse(
                    BytesIO(data),events=('start-ns',))}
                for prefix,uri in namespaces.items():
                    if prefix:
                        try:ET.register_namespace(prefix,uri)
                        except ValueError:pass
                root=ET.fromstring(data)
                parents={child:parent for parent in root.iter() for child in parent}
                for paragraph in list(root.iter('{'+W+'}p')):
                    extra=_fill_paragraph(paragraph,values)
                    if extra is not False:
                        parent=parents[paragraph];index=list(parent).index(paragraph)
                        for i,text in enumerate(extra,1):
                            new=deepcopy(paragraph)
                            first=next(new.iter('{'+W+'}t'))
                            first.text=text
                            parent.insert(index+i,new)
                data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
                # Word's mc:Ignorable attribute references prefixes as plain
                # text; ElementTree may omit their otherwise unused bindings.
                missing=[(prefix,uri) for prefix,uri in namespaces.items() if prefix and
                         ('xmlns:'+prefix+'=').encode() not in data]
                if missing:
                    at=data.index(b'>')
                    declaration=b''.join((' xmlns:'+prefix+'="'+uri+'"').encode()
                                         for prefix,uri in missing)
                    # Skip the XML declaration and insert on the document root.
                    at=data.index(b'>',at+1)
                    data=data[:at]+declaration+data[at:]
            zout.writestr(part,data)
    return out.getvalue()


def render_pages(raw,folder,variant):
    """Local PDF conversion and all-page PNGs. Nothing is sent to an external office."""
    office=portable.which('libreoffice')
    raster=portable.which('pdftoppm');info=portable.which('pdfinfo')
    if not office or not raster or not info:raise Stop('previsualisation_libreoffice_poppler_requise')
    _private_dir(folder)
    with tempfile.TemporaryDirectory(prefix='word-preview-',dir=folder,ignore_cleanup_errors=portable.WINDOWS) as temp:
        p=Path(temp);source=p/'input.docx';source.write_bytes(raw)
        env=os.environ.copy();env['HOME']=str(p);env['TMPDIR']=str(p)
        try:
            proc=subprocess.run([office,'-env:UserInstallation='+ (p/'profile').as_uri(),
                '--headless','--convert-to','pdf:writer_pdf_Export','--outdir',str(p),str(source)],
                env=env,capture_output=True,timeout=90,check=False)
            pdf=p/'input.pdf'
            if proc.returncode or not pdf.is_file() or not pdf.stat().st_size:
                raise Stop('rendu_word_indisponible')
            stats=subprocess.run([info,str(pdf)],capture_output=True,text=True,timeout=15,check=False)
            match=re.search(r'^Pages:\s*(\d+)',stats.stdout,re.M)
            if stats.returncode or not match:raise Stop('nombre_pages_word_indisponible')
            pages=int(match.group(1))
            if not 1<=pages<=MAX_PAGES:raise Stop('previsualisation_word_trop_longue')
            dest=_private_dir(folder/variant)
            generated=subprocess.run([raster,'-f','1','-l',str(pages),'-scale-to','1450',
                 '-png',str(pdf),str(p/'page')],capture_output=True,timeout=90,check=False)
            images=sorted(p.glob('page-*.png'))
            if generated.returncode or len(images)!=pages:raise Stop('images_previsualisation_absentes')
            for index,image in enumerate(images,1):
                data=image.read_bytes()
                if not 0<len(data)<=3_000_000:raise Stop('image_previsualisation_invalide')
                _private_file(dest/(str(index)+'.png'),data)
            return pages
        except (subprocess.TimeoutExpired,OSError):raise Stop('rendu_word_indisponible') from None


def import_template(desk,raw,name,renderer=render_pages):
    ensure_schema(desk)
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_DOCX:
        raise Stop('taille_modele_word_invalide')
    try:raw,neutralized=_neutralize_hyperlinks(raw)
    except (zipfile.BadZipFile,ET.ParseError,KeyError,UnicodeError):
        raise Stop('modele_word_docx_invalide') from None
    info=inspect_template(raw)
    info['neutralized_hyperlinks']=neutralized
    label=PurePosixPath(str(name).replace('\\','/')).name
    if not re.fullmatch(r'[\w À-ÿ().-]{2,110}\.docx',label,re.I):raise Stop('nom_modele_word_invalide')
    label=label[:-5];tid=secrets.token_hex(16);root=_root(desk)
    folder=root/'previews'/tid
    try:
        original=renderer(raw,folder,'original')
        sample=renderer(fill_template(raw,SAMPLE),folder,'sample')
        _private_file(root/'templates'/(tid+'.docx'),raw)
        version=desk.db.execute('SELECT COALESCE(MAX(version),0)+1 FROM cabinet_templates_v330 WHERE label=?',
                                (label,)).fetchone()[0]
        desk.db.execute('INSERT INTO cabinet_templates_v330 VALUES(?,?,?,?,?,?,?,?,?)',
            (tid,label,version,_sha(raw),'draft',json.dumps(info),
             json.dumps({'original':original,'sample':sample}),desk.now(),''))
        desk.db.commit()
    except Exception:
        if folder.exists():shutil.rmtree(folder)
        (root/'templates'/(tid+'.docx')).unlink(missing_ok=True)
        raise
    desk.audit('cabinet_template_imported',{'id':tid,'label':label,'version':version,'sha256':_sha(raw)})
    return {'template_id':tid,'label':label,'version':version,'status':'draft',
            'neutralized_hyperlinks':neutralized,'pages':{'original':original,'sample':sample}}


def templates(desk):
    ensure_schema(desk)
    return [dict(x) for x in desk.db.execute('SELECT * FROM cabinet_templates_v330 ORDER BY label,version DESC')]


def template(desk,tid):
    if not re.fullmatch(r'[a-f0-9]{32}',str(tid)):raise Stop('modele_word_invalide')
    ensure_schema(desk)
    row=desk.db.execute('SELECT * FROM cabinet_templates_v330 WHERE id=?',(tid,)).fetchone()
    if not row:raise Stop('modele_word_absent')
    return dict(row)


def approve_template(desk,tid,expected_sha,ack):
    item=template(desk,tid)
    if item['status']!='draft':raise Stop('modele_word_deja_decide')
    if ack!='yes' or not secrets.compare_digest(item['sha256'],str(expected_sha)):
        raise Stop('validation_visuelle_modele_requise')
    root=_root(desk);raw=(root/'templates'/(tid+'.docx')).read_bytes()
    if _sha(raw)!=item['sha256']:raise Stop('modele_word_modifie')
    for variant,count in json.loads(item['pages']).items():
        if not 1<=count<=MAX_PAGES or any(not (root/'previews'/tid/variant/(str(i)+'.png')).is_file()
            for i in range(1,count+1)):raise Stop('previsualisation_modele_absente')
    desk.db.execute("UPDATE cabinet_templates_v330 SET status='retired' WHERE label=? AND status='approved'",
                    (item['label'],))
    desk.db.execute("UPDATE cabinet_templates_v330 SET status='approved',approved=? WHERE id=?",
                    (desk.now(),tid));desk.db.commit()
    desk.audit('cabinet_template_approved',{'id':tid,'sha256':item['sha256']})
    return {'template_id':tid,'status':'approved','version':item['version']}


def _values(args):
    def value(key,maxlen=200,mandatory=False):
        v=str(args.get(key,'')).strip()
        if len(v)>maxlen or (mandatory and not v) or any(ord(x)<32 and x!='\n' for x in v):
            raise Stop('champ_courrier_invalide_'+key)
        return v
    day=value('date',10,True)
    try:date.fromisoformat(day)
    except ValueError:raise Stop('date_courrier_invalide') from None
    envoi=value('envoi',20,True);appel=value('appel',20,True);fin=value('fin',20,True)
    if envoi not in ENVOI or appel not in APPEL or fin not in FIN:raise Stop('variante_courrier_invalide')
    return {'destinataire':value('destinataire',200,True),'qualite':value('qualite',200),
            'adresse':value('adresse',280),'reference':value('reference',180),
            'objet':value('objet',300,True),'date':date.fromisoformat(day).strftime('%d/%m/%Y'),
            'envoi':ENVOI[envoi],'appel':APPEL[appel],
            'corps':value('corps',14000,True),'fin':FIN[fin],
            'signature':value('signature',200)}


def _name(mid,day,existing,source=''):
    safe=re.sub(r'[^A-Za-z0-9_-]','_',mid)[:70]
    source_name=PurePosixPath(source).name if source else ''
    match=re.fullmatch(r'Courrier_'+re.escape(safe)+r'_(\d{8})_v\d{3}\.docx',source_name)
    base='Courrier_'+safe+'_'+(match.group(1) if match else day.replace('-',''))+'_v'
    versions=[int(x.group(1)) for p in existing if (x:=re.fullmatch(re.escape(base)+r'(\d{3})\.docx',
                PurePosixPath(p).name))]
    if versions and max(versions)>=999:raise Stop('versions_courrier_epuisees')
    return base+f'{(max(versions,default=0)+1):03d}'+'.docx'


def prepare(desk,args,dav=None,renderer=render_pages):
    ensure_schema(desk)
    matter=_resolve_matter(desk,str(args.get('matter','')))
    if str(args.get('matter',''))!=matter['id']:raise Stop('identifiant_dossier_exact_requis')
    kind=str(args.get('kind','letter'))
    if kind not in ('letter','revision'):raise Stop('type_courrier_invalide')
    tid=str(args.get('template_id',''));model=template(desk,tid)
    if model['status']!='approved' and not (kind=='revision' and model['status']=='retired'):
        raise Stop('modele_word_non_valide')
    source_path='';etag='';source_hash='';values={}
    root=_root(desk);model_raw=(root/'templates'/(tid+'.docx')).read_bytes()
    if _sha(model_raw)!=model['sha256']:raise Stop('modele_word_modifie')
    client=dav or _dav(desk)
    destination=clean_path(matter['path']+'/'+str(desk.c.get('word_legal',{}).get(
        'destination_subfolder','20_Actes_et_conclusions/90_AxiorHub_Brouillons')).strip('/'))
    if not under(destination,matter['path']) or destination==matter['path']:
        raise Stop('destination_word_invalide')
    try:existing={x['path']:x for x in client.list_folder(destination) if not x.get('directory')}
    except Stop as exc:
        if str(exc)!='http_404':raise
        existing={}
    if kind=='letter':
        values=_values(args)
        raw=fill_template(model_raw,values)
    else:
        source_path=clean_path(str(args.get('source_path','')))
        if not under(source_path,destination) or source_path not in existing:
            raise Stop('version_source_nextcloud_absente')
        previous=[r for r in desk.db.execute("SELECT data FROM cabinet_projects_v330 WHERE matter=? AND status='done'",
                      (matter['id'],)) if json.loads(r['data']).get('destination')==source_path and
                      json.loads(r['data']).get('template_id')==tid]
        if not previous:raise Stop('version_source_non_creee_par_axiorhub')
        etag=existing[source_path].get('etag','')
        raw=client.download(existing[source_path])
        inspect_template_source(raw)
        source_hash=_sha(raw)
    pid=secrets.token_hex(16);folder=root/'previews'/pid
    try:
        pages=renderer(raw,folder,'prepared')
        _private_file(root/'projects'/(pid+'.docx'),raw)
        output=clean_path(destination+'/'+_name(matter['id'],str(args.get('date',date.today().isoformat())),existing,source_path))
        data={'id':pid,'matter':matter['id'],'kind':kind,'template_id':tid,
              'template_version':model['version'],'template_sha256':model['sha256'],
              'source_path':source_path,'source_etag':etag,'source_sha256':source_hash,
              'values':values,'destination':output,'folder':destination,
              'sha256':_sha(raw),'bytes':len(raw),'pages':pages}
        frozen=_sha(json.dumps(data,sort_keys=True,ensure_ascii=False).encode())
        code=f'{secrets.randbelow(1_000_000):06d}'
        expires=(datetime.now(timezone.utc)+timedelta(minutes=60)).isoformat()
        desk.db.execute('INSERT INTO cabinet_projects_v330 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (pid,tid,matter['id'],kind,'pending',json.dumps(data,ensure_ascii=False),frozen,
             digest(pid+'|'+code),desk.now(),expires,''));desk.db.commit()
    except Exception:
        if folder.exists():shutil.rmtree(folder)
        (root/'projects'/(pid+'.docx')).unlink(missing_ok=True)
        raise
    desk.audit('cabinet_document_prepared',{'project':pid,'matter':matter['id'],'template_sha256':model['sha256'],
            'source_sha256':source_hash,'output':output,'preview_sha256':frozen})
    return {'project_id':pid,'status':'pending','confirmation_code':code,
            'destination':output,'pages':pages,'preview_sha256':frozen}


def inspect_template_source(raw):
    """An edited prior version is copied verbatim; validate its package safety."""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            names=z.namelist()
            if (not 0<len(raw)<=MAX_DOCX or 'word/document.xml' not in names or
                len(names)!=len(set(names)) or len(names)>1200 or
                sum(x.file_size for x in z.infolist())>80_000_000 or
                any('vbaproject' in n.casefold() or n.startswith(('word/embeddings/','word/activeX/')) or
                    n.startswith('/') or '..' in PurePosixPath(n).parts for n in names) or
                any(n.endswith('.rels') and _external_relationship(z.read(n)) for n in names)):
                raise Stop('version_source_word_invalide')
    except zipfile.BadZipFile:raise Stop('version_source_word_invalide') from None


def project(desk,pid):
    if not re.fullmatch(r'[a-f0-9]{32}',str(pid)):raise Stop('projet_courrier_invalide')
    ensure_schema(desk)
    row=desk.db.execute('SELECT * FROM cabinet_projects_v330 WHERE id=?',(pid,)).fetchone()
    if not row:raise Stop('projet_courrier_absent')
    result=dict(row);result['data']=json.loads(row['data'])
    result['result']=json.loads(row['result']) if row['result'] else {}
    return result


def confirm(desk,args,dav=None):
    row=project(desk,str(args.get('project_id','')))
    pid=row['id'];code=str(args.get('confirmation_code',''))
    if not re.fullmatch(r'\d{6}',code) or not secrets.compare_digest(
        row['challenge_hash'],digest(pid+'|'+code)):raise Stop('confirmation_courrier_invalide')
    if row['status'] not in ('pending','creating','partial'):raise Stop('projet_courrier_deja_traite')
    if row['status']=='pending' and datetime.now(timezone.utc)>=datetime.fromisoformat(row['expires']):
        raise Stop('projet_courrier_expire')
    data=row['data'];frozen=_sha(json.dumps(data,sort_keys=True,ensure_ascii=False).encode())
    if not secrets.compare_digest(frozen,row['preview_sha256']):raise Stop('previsualisation_courrier_modifiee')
    if data['destination']!=clean_path(str(args.get('destination',''))):
        raise Stop('destination_courrier_non_confirmee')
    matter=_resolve_matter(desk,data['matter'])
    if matter['id']!=data['matter'] or not under(data['destination'],matter['path']):
        raise Stop('dossier_courrier_modifie')
    template_row=template(desk,data['template_id'])
    model_path=_root(desk)/'templates'/(template_row['id']+'.docx')
    if template_row['sha256']!=data['template_sha256'] or _sha(model_path.read_bytes())!=data['template_sha256']:
        raise Stop('modele_word_modifie')
    raw=(_root(desk)/'projects'/(pid+'.docx')).read_bytes()
    if _sha(raw)!=data['sha256'] or len(raw)!=data['bytes']:raise Stop('previsualisation_courrier_modifiee')
    client=dav or _dav(desk)
    if data['source_path']:
        try:items={x['path']:x for x in client.list_folder(data['folder']) if not x.get('directory')}
        except Stop:raise Stop('version_source_modifiee') from None
        source=items.get(data['source_path'])
        if (not source or (data['source_etag'] and source.get('etag','')!=data['source_etag']) or
                _sha(client.download(source))!=data['source_sha256']):
            raise Stop('version_source_modifiee')
    client.ensure_folder(data['folder'],matter['path'])
    present={x['path']:x for x in client.list_folder(data['folder']) if not x.get('directory')}
    path=data['destination'];journal=desk.db.execute('SELECT * FROM cabinet_files_v330 WHERE project_id=?',(pid,)).fetchone()
    if path in present and (not journal or journal['status'] not in ('writing','created')):
        raise Stop('nom_fichier_deja_existant')
    if not journal:
        desk.db.execute('INSERT INTO cabinet_files_v330 VALUES(?,?,?,?,?,?)',
            (pid,path,'pending',data['sha256'],data['bytes'],desk.now()));desk.db.commit()
    elif journal['path']!=path or journal['sha256']!=data['sha256']:
        raise Stop('reprise_fichier_incoherente')
    desk.db.execute("UPDATE cabinet_projects_v330 SET status='creating' WHERE id=?",(pid,));desk.db.commit()
    if path in present:
        if _sha(client.download(present[path]))!=data['sha256']:raise Stop('reprise_fichier_incoherente')
    else:
        if journal and journal['status']=='created':raise Stop('fichier_cree_absent_lors_reprise')
        desk.db.execute("UPDATE cabinet_files_v330 SET status='writing',updated=? WHERE project_id=?",
                        (desk.now(),pid));desk.db.commit()
        try:client.put_file(path,raw,DOCX)
        except Stop as ex:
            desk.db.execute("UPDATE cabinet_projects_v330 SET status='partial',result=? WHERE id=?",
                (json.dumps({'created_files':[],'warning':str(ex)}),pid));desk.db.commit()
            return {'project_id':pid,'status':'partial','created_files':[],
                    'message':'Création incertaine ou incomplète ; vérifier la destination avant reprise.'}
    try:
        verified={x['path']:x for x in client.list_folder(data['folder']) if not x.get('directory')}.get(path)
        if not verified or _sha(client.download(verified))!=data['sha256']:
            raise Stop('fichier_courrier_non_verifie')
    except Stop as ex:
        desk.db.execute("UPDATE cabinet_projects_v330 SET status='partial',result=? WHERE id=?",
            (json.dumps({'created_files':[],'warning':str(ex)}),pid));desk.db.commit()
        return {'project_id':pid,'status':'partial','created_files':[],
                'message':'Fichier écrit mais relecture incertaine ; vérifier avant reprise.'}
    desk.db.execute("UPDATE cabinet_files_v330 SET status='created',updated=? WHERE project_id=?",
                    (desk.now(),pid));desk.db.commit()
    try:url=client.file_web_url(path)
    except (AttributeError,Stop):url=''
    created={'path':path,'sha256':data['sha256'],'bytes':data['bytes'],'edit_url':url}
    result={'project_id':pid,'status':'done','created_files':[created],'source_unchanged':True,
            'files_overwritten':0,'message':'Nouvelle version créée dans Nextcloud.'}
    desk.db.execute("UPDATE cabinet_projects_v330 SET status='done',result=? WHERE id=?",
                    (json.dumps(result,ensure_ascii=False),pid));desk.db.commit()
    desk.audit('cabinet_document_created',{'project':pid,'matter':matter['id'],'path':path,
                                         'sha256':data['sha256']})
    return result


def preview_png(desk,oid,variant,page):
    if (not re.fullmatch(r'[a-f0-9]{32}',str(oid)) or
            variant not in ('original','sample','prepared') or
            not isinstance(page,int) or not 1<=page<=MAX_PAGES):
        raise Stop('page_previsualisation_invalide')
    if variant=='prepared':
        row=project(desk,oid)
        count=row['data']['pages']
    else:
        row=template(desk,oid)
        count=json.loads(row['pages'])[variant]
    if page>count:raise Stop('page_previsualisation_absente')
    path=_root(desk)/'previews'/oid/variant/(str(page)+'.png')
    if not path.is_file():raise Stop('page_previsualisation_absente')
    return path.read_bytes()

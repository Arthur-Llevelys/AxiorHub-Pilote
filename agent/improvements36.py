"""Small, bounded workstation actions and explicit case labels."""
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import PurePosixPath
import re
import secrets

from .common import Stop, load_matters
from .workspace import chat_scope


def matter_label(matter):
    folder=PurePosixPath(str(matter.get('path','')).rstrip('/')).name
    return folder or str(matter.get('client_name') or matter.get('id') or 'Dossier')


def matter_option(matter):
    label=matter_label(matter)
    identifier=str(matter['id'])
    return label if identifier in label else label+' · '+identifier


def create_task(desk, title, matter='', due=''):
    title=re.sub(r'\s+',' ',str(title or '')).strip()
    if not 3<=len(title)<=2000:raise Stop('tache_texte_invalide')
    if matter and matter not in {x['id'] for x in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    if due:
        try:date.fromisoformat(due)
        except ValueError:raise Stop('date_tache_invalide') from None
    tid=secrets.token_hex(16)
    desk.db.execute('INSERT INTO tasks(id,matter,title,due,status,created) VALUES(?,?,?,?,?,?)',
                    (tid,matter,title,due,'open',desk.now()))
    desk.db.commit()
    desk.audit('tache_locale_ajoutee',{'id':tid,'matter':matter})
    return {'id':tid,'message':'Tâche enregistrée dans le registre local ; elle reste ouverte.'}


def delegate_task(desk, ident):
    if not re.fullmatch(r'[a-f0-9]{32,64}',str(ident or '')):raise Stop('tache_invalide')
    row=desk.db.execute("SELECT id,title,matter FROM tasks WHERE id=? AND status='open'",(ident,)).fetchone()
    if not row:raise Stop('tache_ouverte_absente')
    matter=row['matter']
    if matter and matter not in {m['id'] for m in load_matters(desk.c)}:
        raise Stop('dossier_absent')
    # Internal refresh is queued first, with the same priority as the
    # assistant job, so the answer reads the newest available local index.
    index_job=desk.enqueue('index',{'matter':matter},priority=0) if matter else None
    from .integration import submit_question
    question=('Prépare concrètement la tâche « '+row['title']+
      ' ». Utilise uniquement les sources disponibles, donne un projet utilisable et sourcé, '
      'repère les lacunes et propose les étapes suivantes. '
      'Si une action externe ou une décision d’avocat est nécessaire, demande sa validation. '
      'Ne déclare pas la tâche terminée.')
    result=submit_question(desk,question,matter)
    desk.audit('tache_confiee_a_agent',{'id':ident,'matter':matter,'index_job':index_job,
                                       'assistant_job':result['job_id']})
    return {**result,'index_job':index_job}


def update_local_task(desk,ident,title=None,matter=None,due=None,status='open'):
    if not re.fullmatch(r'[a-f0-9]{32,64}',str(ident or '')):raise Stop('tache_invalide')
    row=desk.db.execute('SELECT * FROM tasks WHERE id=?',(ident,)).fetchone()
    if not row:raise Stop('tache_absente')
    title=re.sub(r'\s+',' ',str(row['title'] if title is None else title)).strip()
    matter=str(matter if matter is not None else row['matter'])
    due=str(due if due is not None else row['due'])
    if not 3<=len(title)<=2000:raise Stop('tache_texte_invalide')
    if matter and matter not in {x['id'] for x in load_matters(desk.c)}:raise Stop('dossier_absent')
    if due:
        try:date.fromisoformat(due[:10])
        except ValueError:raise Stop('date_tache_invalide') from None
    if status not in ('open','closed','cancelled'):raise Stop('etat_tache_invalide')
    desk.db.execute('UPDATE tasks SET title=?,matter=?,due=?,status=? WHERE id=?',
                    (title,matter,due,status,ident));desk.db.commit()
    desk.audit('tache_locale_modifiee',{'id':ident,'status':status,'matter':matter})
    return {'id':ident,'status':status,'message':'Tâche mise à jour.'}


def cancel_local_task(desk,ident,confirm=''):
    if confirm!='yes':raise Stop('confirmation_suppression_tache_requise')
    return update_local_task(desk,ident,status='cancelled')


def attachment_schema(desk):
    desk.db.execute('''CREATE TABLE IF NOT EXISTS assistant_attachments_v360(
      id TEXT PRIMARY KEY, scope TEXT NOT NULL, filename TEXT NOT NULL,
      sha256 TEXT NOT NULL, excerpt TEXT NOT NULL, partial INTEGER NOT NULL,
      created TEXT NOT NULL)''')
    desk.db.execute('''CREATE TABLE IF NOT EXISTS assistant_attachment_parts_v363(
      attachment_id TEXT NOT NULL, ordinal INTEGER NOT NULL, char_start INTEGER NOT NULL,
      char_end INTEGER NOT NULL, excerpt TEXT NOT NULL, sha256 TEXT NOT NULL,
      PRIMARY KEY(attachment_id,ordinal))''')
    desk.db.execute('''CREATE TABLE IF NOT EXISTS assistant_attachment_meta_v363(
      attachment_id TEXT PRIMARY KEY, extracted_chars INTEGER NOT NULL,
      part_count INTEGER NOT NULL, text_sha256 TEXT NOT NULL)''')
    desk.db.commit()


def split_document(text,size=4000):
    """Keep every extracted character and stable original character offsets."""
    parts=[];start=0
    while start<len(text):
        end=min(start+size,len(text))
        if end<len(text):
            line=text.rfind('\n',start+size//2,end)
            if line>start:end=line+1
        parts.append((start,end,text[start:end]));start=end
    return parts


def upload_attachment(desk, raw, filename, matter='', key=''):
    from .documents import extract,extract_pages
    attachment_schema(desk)
    cutoff=(datetime.now(timezone.utc)-timedelta(days=90)).isoformat()
    desk.db.execute('DELETE FROM assistant_attachment_parts_v363 WHERE attachment_id IN (SELECT id FROM assistant_attachments_v360 WHERE created<?)',(cutoff,))
    desk.db.execute('DELETE FROM assistant_attachment_meta_v363 WHERE attachment_id IN (SELECT id FROM assistant_attachments_v360 WHERE created<?)',(cutoff,))
    desk.db.execute('DELETE FROM assistant_attachments_v360 WHERE created<?',(cutoff,))
    name=PurePosixPath(str(filename or '').replace('\\','/')).name
    if not name or len(name)>150 or not re.fullmatch(r'[^\x00-\x1f/\\]+\.(?:pdf|docx|txt|md|csv|odt|eml)',name,re.I):
        raise Stop('format_piece_assistant_invalide')
    if not 0<len(raw)<=8_000_000:raise Stop('taille_piece_assistant_invalide')
    scope,_,_=chat_scope(desk.c,{'matter':matter,'key':key})
    cfg={**desk.c.get('documents',{}),'max_file_bytes':8_000_000,
         'max_file_bytes_long':8_000_000,'max_document_chars':500000,
         'max_document_chars_long':2_000_000}
    if PurePosixPath(name).suffix.lower() in ('.pdf','.docx'):
        pages=extract_pages(raw,name,cfg)
        content='\n'.join('[Page '+str(x['page'])+']\n'+x['text'] for x in pages)
    else:
        result=extract(raw,name,cfg)
        content=result if isinstance(result,str) else result.get('text','')
    if not content.strip():raise Stop('piece_assistant_sans_texte')
    if len(content)>500000:raise Stop('piece_extraite_plus_de_500000_caracteres')
    ident=secrets.token_hex(16)
    # Original bytes are not persisted. Store the complete extracted text in
    # scoped SQLite rows; retaining only the first 12,000 chars hid later pages.
    parts=split_document(content)
    desk.db.execute('INSERT INTO assistant_attachments_v360 VALUES(?,?,?,?,?,?,?)',
                    (ident,scope,name,hashlib.sha256(raw).hexdigest(),
                     content[:12000],0,desk.now()))
    desk.db.execute('INSERT INTO assistant_attachment_meta_v363 VALUES(?,?,?,?)',
                    (ident,len(content),len(parts),hashlib.sha256(content.encode()).hexdigest()))
    desk.db.executemany('INSERT INTO assistant_attachment_parts_v363 VALUES(?,?,?,?,?,?)',
        [(ident,n,start,end,text,hashlib.sha256(text.encode()).hexdigest())
         for n,(start,end,text) in enumerate(parts,1)])
    desk.db.commit()
    desk.audit('piece_assistant_extraite',{'id':ident,'sha256':hashlib.sha256(raw).hexdigest(),
                                            'partial':False,'parts':len(parts)})
    return {'attachment_id':ident,'name':name,'partial':False,
            'extracted_characters':len(content),'parts':len(parts)}


def attachment_source(desk, ident, matter='', key=''):
    if not re.fullmatch(r'[a-f0-9]{32}',str(ident or '')):raise Stop('piece_assistant_invalide')
    attachment_schema(desk)
    scope,_,_=chat_scope(desk.c,{'matter':matter,'key':key})
    row=desk.db.execute('SELECT * FROM assistant_attachments_v360 WHERE id=? AND scope=?',
                        (ident,scope)).fetchone()
    if not row:raise Stop('piece_assistant_autre_contexte')
    if row['created']<(datetime.now(timezone.utc)-timedelta(days=90)).isoformat():
        raise Stop('piece_assistant_expiree')
    return {'id':'attachment-'+ident,'kind':'piece_jointe_locale','path':row['filename'],
            'excerpt':row['excerpt'],'sha256':row['sha256'],'partial':bool(row['partial'])}


def attachment_parts(desk,ident,matter='',key=''):
    source=attachment_source(desk,ident,matter,key)
    meta=desk.db.execute('SELECT * FROM assistant_attachment_meta_v363 WHERE attachment_id=?',(ident,)).fetchone()
    if not meta:return source,[],{'legacy_partial':source['partial'],'extracted_chars':len(source['excerpt'])}
    rows=desk.db.execute('SELECT * FROM assistant_attachment_parts_v363 WHERE attachment_id=? ORDER BY ordinal',(ident,)).fetchall()
    if len(rows)!=meta['part_count'] or not rows or rows[0]['char_start']!=0 or rows[-1]['char_end']!=meta['extracted_chars']:
        raise Stop('piece_extraite_incomplete')
    if any(row['char_end']<=row['char_start'] or row['char_end']-row['char_start']!=len(row['excerpt'])
           or (i and row['char_start']!=rows[i-1]['char_end'])
           or hashlib.sha256(row['excerpt'].encode()).hexdigest()!=row['sha256']
           for i,row in enumerate(rows)):
        raise Stop('piece_extraite_alteree')
    if hashlib.sha256(''.join(row['excerpt'] for row in rows).encode()).hexdigest()!=meta['text_sha256']:
        raise Stop('piece_extraite_alteree')
    return source,[dict(row) for row in rows],dict(meta)

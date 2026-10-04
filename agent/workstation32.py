"""Read-only, bounded cabinet search and workstation context for 3.2."""
from datetime import date, datetime, timedelta, timezone
import json
import re
import sqlite3

from .common import Stop, digest, fold, load_matters
from .index import DocumentIndex
from .rag import lexical_terms
from .state import State
from .improvements36 import matter_label

SOURCES = {'all', 'matters', 'contacts', 'mails', 'documents', 'attachments', 'events'}


def contacts(matters):
    """Keep each matter/role link distinct; an address is not a person ID."""
    rows=[]
    for matter in matters:
        for person in matter.get('correspondents',[]):
            rows.append({'email':person['email'], 'role':person['role'],
                         'matter':matter['id'], 'matter_name':matter_label(matter),
                         'origin':'Registre AxiorHub confirmé'})
    return sorted(rows,key=lambda row:(row['email'].casefold(),row['matter']))


def _iso_day(value):
    if not value:return ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise Stop('date_recherche_invalide')
    try:date.fromisoformat(value)
    except ValueError:raise Stop('date_recherche_invalide') from None
    return value


def _excerpt(value,query):
    raw=str(value or '').replace('\r',' ').replace('\n',' ')
    target=fold(query).split()[0]
    at=fold(raw).find(target)
    at=max(0,at-65) if at>=0 else 0
    return ('…' if at else '')+raw[at:at+210]+('…' if at+210<len(raw) else '')


def search(desk,query,source='all',matter='',after='',before='',limit=60):
    """Search registered data only; never query Nextcloud, IMAP or an LLM in a GET."""
    query=str(query or '').strip()
    if not 2<=len(query)<=200:raise Stop('recherche_requise_200_caracteres_maximum')
    if source not in SOURCES:raise Stop('source_recherche_invalide')
    after,before=_iso_day(after),_iso_day(before)
    if after and before and after>before:raise Stop('dates_recherche_inversees')
    matters=load_matters(desk.c);by_id={row['id']:row for row in matters}
    if matter and matter not in by_id:raise Stop('dossier_absent')
    words=fold(query).split()
    def matches(value):return all(word in fold(str(value)) for word in words)
    def in_scope(mid,stamp=''):
        if matter and mid!=matter:return False
        day=str(stamp or '')[:10]
        if after and (not day or day<after):return False
        if before and (not day or day>before):return False
        return True
    # Database dates are not available for address-book entries and matter names.
    def undated():return not after and not before
    results=[]
    def add(kind,mid,title,description,stamp='',path='',key='',origin='Index local'):
        if not in_scope(mid,stamp) or len(results)>=limit:return
        results.append({'kind':kind,'matter':mid,'matter_name':matter_label(by_id[mid]) if mid in by_id else '',
                        'title':str(title)[:300],'excerpt':str(description)[:260],
                        'date':str(stamp or ''),'path':str(path or ''),'key':str(key or ''),'origin':origin})
    if source in ('all','matters') and undated():
        for item in matters:
            if matches(' '.join(str(item.get(field,'')) for field in ('id','client_name','path'))+' '+' '.join(item.get('references',[]))):
                add('Dossier',item['id'],matter_label(item),item['path'],path=item['path'],origin='Registre des dossiers')
    if source in ('all','contacts') and undated():
        for person in contacts(matters):
            if matches(person['email']+' '+person['matter_name']):
                add('Contact',person['matter'],person['email'],person['role'],origin=person['origin'])
    if source in ('all','mails'):
        for row in desk.db.execute('SELECT mail_key,matter,subject,sender,received FROM work_items ORDER BY received DESC LIMIT 5000'):
            if (row['matter'] in by_id or not row['matter']) and matches(row['subject']+' '+row['sender']):
                add('Courriel',row['matter'],row['subject'],row['sender'],row['received'],key=row['mail_key'],origin='Registre des courriels analysés')
    index=DocumentIndex(desk.c['state_dir'])
    try:
        tokens=lexical_terms([query])
        fts=' AND '.join('"'+token+'"' for token in tokens)
        # FTS covers every indexed text without pulling entire documents into the UI.
        if source in ('all','documents'):
            if fts:
                for row in index.db.execute('''SELECT s.matter,s.path,d.modified,
                    snippet(search,2,'','',' … ',18),d.error FROM search s
                    JOIN docs d ON d.matter=s.matter AND d.path=s.path
                    WHERE search MATCH ? LIMIT 120''',(fts,)):
                    mid,path,stamp,content,error=row
                    if mid in by_id:add('Document',mid,path.rsplit('/',1)[-1],
                        error or content,stamp,path,origin='Index Nextcloud local')
            # Files whose extraction failed can still be found by their filename.
            pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            for row in index.db.execute("SELECT matter,path,modified,error FROM docs WHERE path LIKE ? ESCAPE '\\' ORDER BY modified DESC LIMIT 60",(pattern,)):
                mid,path,stamp,error=row
                if mid in by_id and matches(path) and not any(x['kind']=='Document' and x['matter']==mid and x['path']==path for x in results):
                    add('Document',mid,path.rsplit('/',1)[-1],error or 'Correspondance dans le nom du fichier.',stamp,path,
                        origin='Inventaire Nextcloud local'+(' · extraction en erreur' if error else ''))
        if source in ('all','attachments'):
            if fts:
                for row in index.db.execute('''SELECT c.matter,c.path,c.modified,
                    snippet(knowledge_fts,4,'','',' … ',18) FROM knowledge_fts
                    JOIN knowledge_chunks c ON c.matter=knowledge_fts.matter
                    AND c.source_id=knowledge_fts.source_id AND c.chunk_no=knowledge_fts.chunk_no
                    WHERE knowledge_fts MATCH ? AND c.kind='attachment' LIMIT 120''',(fts,)):
                    mid,path,stamp,content=row
                    if mid in by_id and not any(x['kind']=='Pièce jointe' and x['matter']==mid and x['path']==path for x in results):
                        add('Pièce jointe',mid,path.rsplit('/',1)[-1],content,stamp,path,origin='Pièce jointe extraite et indexée')
        if source in ('all','mails') and fts:
            state=State(desk.c['state_dir'])
            account=desk.c['mail']['username']+'@'+desk.c['mail']['host']
            try:
                for row in index.db.execute('''SELECT c.matter,c.path,c.modified,c.kind,
                    c.meta,snippet(knowledge_fts,4,'','',' … ',18) FROM knowledge_fts
                    JOIN knowledge_chunks c ON c.matter=knowledge_fts.matter
                    AND c.source_id=knowledge_fts.source_id AND c.chunk_no=knowledge_fts.chunk_no
                    WHERE knowledge_fts MATCH ? AND c.kind IN ('email_received','email_history','email_sent') LIMIT 120''',(fts,)):
                    mid,path,stamp,kind,raw_meta,excerpt=row
                    if mid not in by_id or any(x['kind']=='Courriel indexé' and x['matter']==mid and x['path']==path for x in results):continue
                    try:meta=json.loads(raw_meta or '{}')
                    except (ValueError,TypeError):meta={}
                    key=''
                    if kind=='email_received' and meta.get('message_id'):
                        found=state.db.execute('SELECT key FROM messages WHERE mid=? LIMIT 1',
                                               (digest(account+meta['message_id']),)).fetchone()
                        if found and desk.db.execute('SELECT 1 FROM work_items WHERE mail_key=? AND matter=?',
                                                     (found[0],mid)).fetchone():key=found[0]
                    add('Courriel indexé',mid,meta.get('subject') or path.rsplit('/',1)[-1],
                        excerpt,stamp,path,key,origin='Contenu courriel indexé'+(' · fiche du courriel' if key else ' · fiche du dossier'))
            finally:state.db.close()
        coverage={'registered_matters':len(matters),
                  'indexed_documents':index.db.execute('SELECT COUNT(*) FROM docs').fetchone()[0],
                  'indexed_attachments':index.db.execute("SELECT COUNT(DISTINCT matter||'|'||source_id) FROM knowledge_chunks WHERE kind='attachment'").fetchone()[0],
                  'indexed_mail_sources':index.db.execute("SELECT COUNT(DISTINCT matter||'|'||source_id) FROM knowledge_chunks WHERE kind IN ('email_received','email_history','email_sent')").fetchone()[0],
                  'pending_inventory_scans':index.db.execute('SELECT COUNT(*) FROM inventory_scans').fetchone()[0]}
    finally:index.db.close()
    if source in ('all','events'):
        for row in desk.db.execute('SELECT id,matter,title,description,starts FROM calendar_cache ORDER BY starts DESC LIMIT 2000'):
            if (row['matter'] in by_id or not row['matter']) and matches(row['title']+' '+row['description']):
                add('Événement',row['matter'],row['title'],_excerpt(row['description'],query),row['starts'],key=row['id'],origin='Cache CalDAV')
    coverage.update({'registered_contacts':len(contacts(matters)),
                     'known_mails':desk.db.execute('SELECT COUNT(*) FROM work_items').fetchone()[0],
                     'cached_events':desk.db.execute('SELECT COUNT(*) FROM calendar_cache').fetchone()[0],
                     'last_discovery':desk.settings('last_discovery',{}),
                     'last_mail_collection':desk.settings('last_run',{})})
    return {'results':results,'coverage':coverage,'limited':len(results)>=limit,
            'notice':'Recherche dans les données locales enregistrées ou indexées. Une source non synchronisée peut manquer ; ouvrir le dossier ou actualiser les connexions pour vérifier.'}


def briefing(desk):
    """14-day cached horizon, with freshness disclosed alongside every count."""
    now=datetime.now(timezone.utc)
    future=(now+timedelta(days=14)).isoformat()
    events=[dict(r) for r in desk.db.execute(
        'SELECT id,title,starts,matter,fetched FROM calendar_cache WHERE starts>=? AND starts<? ORDER BY starts LIMIT 5',
        (now.isoformat(),future))]
    pending=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state IN ('needs_action','needs_confirmation')").fetchone()[0]
    drafts=desk.db.execute("SELECT COUNT(*) FROM work_items WHERE state='draft_ready'").fetchone()[0]
    return {'events':events,'pending_mail':pending,'ready_drafts':drafts,
            'calendar_last_fetched':desk.db.execute('SELECT MAX(fetched) FROM calendar_cache').fetchone()[0],
            'mail_last_seen':desk.db.execute('SELECT MAX(updated) FROM work_items').fetchone()[0],
            'last_discovery':desk.settings('last_discovery',{}),
            'health':desk.settings('health',{}),'at':now.isoformat()}

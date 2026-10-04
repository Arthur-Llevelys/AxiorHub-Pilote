"""Source index with matter scoping, FTS ranking and optional local embeddings."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .common import Stop,digest
from .documents import extract
from .rag import Embedder,chunks,cosine,lexical_terms


class DocumentIndex:
    def __init__(self,state_dir,rag=None,ollama=None):
        self.db=sqlite3.connect(Path(state_dir)/'documents.sqlite3',timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS docs (matter TEXT,path TEXT,etag TEXT,modified TEXT,text TEXT,error TEXT,PRIMARY KEY(matter,path))')
        self.db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(matter UNINDEXED,path,text,tokenize="unicode61 remove_diacritics 2")')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS knowledge_chunks(
          matter TEXT,source_id TEXT,path TEXT,etag TEXT,modified TEXT,kind TEXT,
          chunk_no INTEGER,text TEXT,meta TEXT,indexed_at TEXT,
          PRIMARY KEY(matter,source_id,chunk_no));
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
          matter UNINDEXED,source_id UNINDEXED,chunk_no UNINDEXED,path,text,
          tokenize="unicode61 remove_diacritics 2");
        CREATE TABLE IF NOT EXISTS knowledge_embeddings(
          matter TEXT,source_id TEXT,chunk_no INTEGER,model TEXT,vector TEXT,
          PRIMARY KEY(matter,source_id,chunk_no,model));
        CREATE INDEX IF NOT EXISTS knowledge_path ON knowledge_chunks(matter,path);
        CREATE TABLE IF NOT EXISTS inventory_scans(
          matter TEXT PRIMARY KEY,generation TEXT NOT NULL,cursor INTEGER NOT NULL,
          started TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory_seen(
          matter TEXT NOT NULL,generation TEXT NOT NULL,path TEXT NOT NULL,
          PRIMARY KEY(matter,generation,path));
        CREATE INDEX IF NOT EXISTS inventory_seen_generation
          ON inventory_seen(matter,generation);
        ''')
        self.db.commit();self.rag=rag or {};self.ollama=ollama or {};self._embedder=None
        self.last_page_state={'complete':True,'next_cursor':None,'seen':0}

    def embedder(self):
        if not self.rag.get('enabled'):return None
        if self._embedder is None:self._embedder=Embedder(self.ollama,self.rag)
        return self._embedder

    def remove_knowledge(self,matter,source_id):
        self.db.execute('DELETE FROM knowledge_chunks WHERE matter=? AND source_id=?',(matter,source_id))
        self.db.execute('DELETE FROM knowledge_fts WHERE matter=? AND source_id=?',(matter,source_id))
        self.db.execute('DELETE FROM knowledge_embeddings WHERE matter=? AND source_id=?',(matter,source_id))

    def put_source(self,matter,path,text,etag,modified='',kind='document',meta=None):
        mid=matter['id'] if isinstance(matter,dict) else matter
        source_id=digest(mid+'|'+kind+'|'+path)
        row=self.db.execute('SELECT etag FROM knowledge_chunks WHERE matter=? AND source_id=? LIMIT 1',(mid,source_id)).fetchone()
        if row and row[0]==etag:return source_id,False
        self.remove_knowledge(mid,source_id)
        stamp=datetime.now(timezone.utc).isoformat();parts=chunks(text)
        for i,part in enumerate(parts):
            values=(mid,source_id,path,etag,modified,kind,i,part,json.dumps(meta or {},ensure_ascii=False),stamp)
            self.db.execute('INSERT INTO knowledge_chunks VALUES (?,?,?,?,?,?,?,?,?,?)',values)
            self.db.execute('INSERT INTO knowledge_fts VALUES (?,?,?,?,?)',(mid,source_id,i,path,part))
        self.db.commit();return source_id,True

    def ensure_embeddings(self,matter,budget=None):
        if not self.rag.get('enabled'):return {'created':0,'pending':0,'error':''}
        mid=matter['id'] if isinstance(matter,dict) else matter
        model=self.rag.get('embedding_model','qwen3-embedding:0.6b')
        limit=budget or self.rag.get('embedding_chunks_per_run',24)
        rows=self.db.execute('''SELECT c.source_id,c.chunk_no,c.text FROM knowledge_chunks c
          LEFT JOIN knowledge_embeddings e ON e.matter=c.matter AND e.source_id=c.source_id
          AND e.chunk_no=c.chunk_no AND e.model=? WHERE c.matter=? AND e.source_id IS NULL
          ORDER BY c.indexed_at,c.source_id,c.chunk_no LIMIT ?''',(model,mid,limit+1)).fetchall()
        selected=rows[:limit]
        if not selected:return {'created':0,'pending':0,'error':''}
        try:vectors=self.embedder().embed([r[2] for r in selected])
        except Stop as ex:return {'created':0,'pending':len(rows),'error':str(ex)}
        for row,vector in zip(selected,vectors):
            self.db.execute('INSERT OR REPLACE INTO knowledge_embeddings VALUES (?,?,?,?,?)',
                            (mid,row[0],row[1],model,json.dumps(vector,separators=(',',':'))))
        self.db.commit()
        pending=self.db.execute('''SELECT COUNT(*) FROM knowledge_chunks c LEFT JOIN knowledge_embeddings e
          ON e.matter=c.matter AND e.source_id=c.source_id AND e.chunk_no=c.chunk_no AND e.model=?
          WHERE c.matter=? AND e.source_id IS NULL''',(model,mid)).fetchone()[0]
        return {'created':len(selected),'pending':pending,'error':''}

    def sync(self,dav,matter,cfg,budget=10):
        items=dav.inventory(matter['path'])
        known={r[0]:r[1] for r in self.db.execute('SELECT path,etag FROM docs WHERE matter=?',(matter['id'],))}
        active={r['path'] for r in items}
        for path in set(known)-active:self.remove(matter['id'],path)
        changed=[r for r in items if not r['etag'] or known.get(r['path'])!=r['etag']]
        for item in changed[:budget]:
            self.remove(matter['id'],item['path']);text=error=''
            try:text=extract(dav.download(item),item['path'],cfg)
            except Stop as ex:error=str(ex)
            self.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)',(matter['id'],item['path'],item['etag'],item['modified'],text,error))
            if text:
                self.db.execute('INSERT INTO search VALUES (?,?,?)',(matter['id'],item['path'],text))
                self.put_source(matter,item['path'],text,item['etag'],item['modified'],'document',
                                {'confidentiality':'matter','author':'unknown','source':'nextcloud'})
            self.db.commit()
        stale={x['path'] for x in changed[budget:]}
        self.ensure_embeddings(matter)
        return items,stale

    def sync_page(self,dav,matter,cfg,budget=100):
        """Index one restartable DAV page and reconcile only at scan end."""
        if not hasattr(dav,'inventory_page'):
            items,stale=self.sync(dav,matter,cfg,budget)
            self.last_page_state={'complete':True,'next_cursor':None,'seen':len(items),
                                  'page_files':len(items),'changed':len(items)-len(stale)}
            return items,stale
        mid=matter['id'];stamp=datetime.now(timezone.utc).isoformat()
        row=self.db.execute(
            'SELECT generation,cursor FROM inventory_scans WHERE matter=?',(mid,)).fetchone()
        if row:
            generation,cursor=row[0],int(row[1])
        else:
            generation,cursor=uuid.uuid4().hex,0
            self.db.execute('DELETE FROM inventory_seen WHERE matter=?',(mid,))
            self.db.execute('INSERT INTO inventory_scans VALUES (?,?,?,?,?)',
                            (mid,generation,0,stamp,stamp));self.db.commit()
        items,next_cursor,complete=dav.inventory_page(matter['path'],cursor,budget)
        known={r[0]:r[1] for r in self.db.execute(
            'SELECT path,etag FROM docs WHERE matter=?',(mid,))}
        for item in items:
            self.db.execute('INSERT OR IGNORE INTO inventory_seen VALUES (?,?,?)',
                            (mid,generation,item['path']))
        changed=[r for r in items if not r['etag'] or known.get(r['path'])!=r['etag']]
        for item in changed:
            self.remove(mid,item['path']);text=error=''
            try:text=extract(dav.download(item),item['path'],cfg)
            except Stop as ex:error=str(ex)
            self.db.execute('INSERT INTO docs VALUES (?,?,?,?,?,?)',
                            (mid,item['path'],item['etag'],item['modified'],text,error))
            if text:
                self.db.execute('INSERT INTO search VALUES (?,?,?)',(mid,item['path'],text))
                self.put_source(matter,item['path'],text,item['etag'],item['modified'],'document',
                                {'confidentiality':'matter','author':'unknown','source':'nextcloud'})
        if complete:
            missing=[r[0] for r in self.db.execute('''SELECT path FROM docs WHERE matter=?
              AND path NOT IN (SELECT path FROM inventory_seen WHERE matter=? AND generation=?)''',
              (mid,mid,generation))]
            for path in missing:self.remove(mid,path)
            self.db.execute('DELETE FROM inventory_scans WHERE matter=?',(mid,))
            self.db.execute('DELETE FROM inventory_seen WHERE matter=?',(mid,))
        else:
            self.db.execute('UPDATE inventory_scans SET cursor=?,updated=? WHERE matter=?',
                            (int(next_cursor),stamp,mid))
        self.db.commit();self.ensure_embeddings(matter)
        seen=self.db.execute('SELECT COUNT(*) FROM inventory_seen WHERE matter=? AND generation=?',
                             (mid,generation)).fetchone()[0] if not complete else len(items)+cursor
        self.last_page_state={'complete':complete,'next_cursor':next_cursor,'seen':seen,
                              'page_files':len(items),'changed':len(changed)}
        return items,set()

    def remove(self,matter,path):
        ids=[r[0] for r in self.db.execute('SELECT DISTINCT source_id FROM knowledge_chunks WHERE matter=? AND path=?',(matter,path))]
        for source_id in ids:self.remove_knowledge(matter,source_id)
        self.db.execute('DELETE FROM docs WHERE matter=? AND path=?',(matter,path))
        self.db.execute('DELETE FROM search WHERE matter=? AND path=?',(matter,path));self.db.commit()

    def ranked_chunks(self,matter,terms,limit=10,kinds=None):
        mid=matter['id'] if isinstance(matter,dict) else matter
        tokens=lexical_terms(terms);scores={};rows={}
        if tokens:
            query=' OR '.join('"'+t+'"' for t in tokens)
            try:
                for row in self.db.execute('''SELECT c.*,bm25(knowledge_fts) FROM knowledge_fts
                  JOIN knowledge_chunks c ON c.matter=knowledge_fts.matter AND c.source_id=knowledge_fts.source_id
                  AND c.chunk_no=knowledge_fts.chunk_no WHERE knowledge_fts MATCH ? AND c.matter=? LIMIT 60''',(query,mid)):
                    key=(row[1],row[6]);rows[key]=row;scores[key]=scores.get(key,0)+1/(1+max(0,row[-1]+20))
            except sqlite3.OperationalError:pass
        semantic_error=''
        try:embedder=self.embedder()
        except Stop as ex:embedder=None;semantic_error=str(ex)
        if embedder:
            try:q=embedder.embed(['\n'.join(terms)[:4000]])[0]
            except Stop as ex:q=None;semantic_error=str(ex)
            if q is not None:
                model=self.rag.get('embedding_model','qwen3-embedding:0.6b')
                for row in self.db.execute('''SELECT c.*,e.vector FROM knowledge_chunks c JOIN knowledge_embeddings e
                  ON e.matter=c.matter AND e.source_id=c.source_id AND e.chunk_no=c.chunk_no
                  WHERE c.matter=? AND e.model=?''',(mid,model)):
                    key=(row[1],row[6]);rows[key]=row[:-1];scores[key]=scores.get(key,0)+max(0,cosine(q,json.loads(row[-1])))
        if not scores:
            for row in self.db.execute('SELECT * FROM knowledge_chunks WHERE matter=? ORDER BY modified DESC,indexed_at DESC LIMIT 30',(mid,)):
                key=(row[1],row[6]);rows[key]=row;scores[key]=0
        selected=[];per_source={}
        for key in sorted(scores,key=lambda k:(-scores[k],rows[k][2],k[1])):
            row=rows[key]
            if kinds and row[5] not in kinds:continue
            if per_source.get(row[1],0)>=2:continue
            per_source[row[1]]=per_source.get(row[1],0)+1;selected.append((row,scores[key]))
            if len(selected)>=limit:break
        return selected,semantic_error

    def sources(self,dav,matter,terms,cfg):
        inventory,stale=self.sync_page(dav,matter,cfg,max(25,cfg.get('index_updates_per_run',10)))
        embedding=self.ensure_embeddings(matter)
        ranked,semantic_error=self.ranked_chunks(matter,terms,limit=max(2,cfg.get('max_documents_per_mail',5)*2))
        grouped={}
        for row,score in ranked:
            if row[2] in stale:continue
            item=grouped.setdefault(row[1],{'id':'doc-'+row[1][:16],'kind':'document','source_kind':row[5],
                'path':row[2],'etag':row[3],'modified':row[4],'excerpts':[],'score':round(score,4),
                'metadata':json.loads(row[8]),'partial':True})
            item['excerpts'].append('[Extrait '+str(row[6]+1)+']\n'+row[7])
        sources=[]
        for item in grouped.values():
            item['excerpt']='\n\n'.join(item.pop('excerpts'));sources.append(item)
            if len(sources)>=cfg.get('max_documents_per_mail',5):break
        failed=[{'path':p,'reason':e} for p,e in self.db.execute("SELECT path,error FROM docs WHERE matter=? AND error<>''",(matter['id'],))]
        page=self.last_page_state
        return sources,{'total_files':page.get('seen',len(inventory)),'pending_index':len(stale),
          'indexed_files':max(0,page.get('seen',len(inventory))-len(stale)-len(failed)),
          'selected_files':len(sources),'not_exhaustive':True,'failed':failed,'retrieval':'hybrid_fts_embeddings' if self.rag.get('enabled') else 'fts',
          'embedding_model':self.rag.get('embedding_model',''),'embedding_pending':embedding.get('pending',0),
          'semantic_error':semantic_error or embedding.get('error',''),'inventory_complete':page.get('complete',True)}

    def overview_sources(self,matter,limit=12):
        ranked,_=self.ranked_chunks(matter,[matter.get('client_name',''),matter['id'],'chronologie instruction demande échéance paiement contrat'],limit=limit)
        return [{'id':'knowledge-'+row[1][:16]+'-'+str(row[6]),'path':row[2],'kind':row[5],
                 'modified':row[4],'excerpt':row[7],'score':round(score,4)} for row,score in ranked]

    def global_sources(self,terms,limit=12):
        """Bounded cabinet-wide retrieval with explicit matter and source labels.

        Lexical candidates cover the complete indexed corpus. Semantic candidates
        are deliberately capped so a large cabinet cannot monopolise the worker.
        """
        limit=max(1,min(int(limit),30));tokens=lexical_terms(terms);scores={};rows={}
        lexical_count=0
        if tokens:
            query=' OR '.join('"'+t+'"' for t in tokens)
            try:
                for row in self.db.execute('''SELECT c.*,bm25(knowledge_fts) FROM knowledge_fts
                  JOIN knowledge_chunks c ON c.matter=knowledge_fts.matter
                  AND c.source_id=knowledge_fts.source_id AND c.chunk_no=knowledge_fts.chunk_no
                  WHERE knowledge_fts MATCH ? ORDER BY bm25(knowledge_fts) LIMIT 240''',(query,)):
                    key=(row[0],row[1],row[6]);rows[key]=row[:-1]
                    scores[key]=scores.get(key,0)+1/(1+max(0,row[-1]+20));lexical_count+=1
            except sqlite3.OperationalError:pass
        semantic_error='';semantic_scanned=0
        try:embedder=self.embedder()
        except Stop as ex:embedder=None;semantic_error=str(ex)
        if embedder:
            try:q=embedder.embed(['\n'.join(terms)[:4000]])[0]
            except Stop as ex:q=None;semantic_error=str(ex)
            if q is not None:
                model=self.rag.get('embedding_model','qwen3-embedding:0.6b')
                # Recent chunks are a bounded semantic complement. Lexical search
                # above remains complete over every indexed chunk.
                for row in self.db.execute('''SELECT c.*,e.vector FROM knowledge_chunks c
                  JOIN knowledge_embeddings e ON e.matter=c.matter AND e.source_id=c.source_id
                  AND e.chunk_no=c.chunk_no WHERE e.model=?
                  ORDER BY c.indexed_at DESC LIMIT 6000''',(model,)):
                    semantic_scanned+=1;score=max(0,cosine(q,json.loads(row[-1])))
                    if score<0.35:continue
                    key=(row[0],row[1],row[6]);rows[key]=row[:-1]
                    scores[key]=scores.get(key,0)+score
        selected=[];per_matter={};per_source={}
        for key in sorted(scores,key=lambda k:(-scores[k],rows[k][4],k[2])):
            row=rows[key];mid=row[0];source=row[1]
            if per_matter.get(mid,0)>=4 or per_source.get((mid,source),0)>=2:continue
            per_matter[mid]=per_matter.get(mid,0)+1
            per_source[(mid,source)]=per_source.get((mid,source),0)+1
            selected.append({'id':'cabinet-'+digest(mid+'|'+source+'|'+str(row[6]))[:20],
                'matter':mid,'source_id':source,'path':row[2],'modified':row[4],
                'kind':row[5],'excerpt':row[7][:5000],'score':round(scores[key],4),
                'partial':True})
            if len(selected)>=limit:break
        total=self.db.execute('SELECT COUNT(*) FROM knowledge_chunks').fetchone()[0]
        return selected,{'indexed_chunks':total,'lexical_candidates':lexical_count,
            'semantic_chunks_scanned':semantic_scanned,'semantic_cap':6000,
            'not_exhaustive':True,'semantic_error':semantic_error,
            'retrieval':'hybrid_fts_embeddings' if self.rag.get('enabled') else 'fts'}

    def match_evidence(self,sender):
        """Return non-content signals only; no cross-matter excerpt leaves this index."""
        sender=sender.lower();found={}
        if not sender or '@' not in sender:return found
        for mid, in self.db.execute('''SELECT DISTINCT matter FROM docs
          WHERE error='' AND instr(lower(text),?)>0 LIMIT 50''',(sender,)):
            found.setdefault(mid,[]).append({'signal':'adresse_dans_document','weight':10,'value':sender})
        for mid,meta,modified in self.db.execute("SELECT matter,meta,modified FROM knowledge_chunks WHERE chunk_no=0 AND kind IN ('email_received','email_history')"):
            try:data=json.loads(meta)
            except (ValueError,TypeError):continue
            if data.get('sender','').lower()==sender:
                found.setdefault(mid,[]).append({'signal':'correspondant_dans_historique','weight':18,'value':sender})
                try:
                    stamp=datetime.fromisoformat(modified)
                    if (datetime.now(timezone.utc)-stamp.astimezone(timezone.utc)).days<=120:
                        found[mid].append({'signal':'echange_recent','weight':5,'value':modified})
                except (ValueError,TypeError):pass
        return found

"""Matter-scoped hybrid retrieval: FTS plus local Ollama embeddings."""
import json
import math
from .common import HTTP,Stop,digest,fold


CHUNK_LIMIT=2000   # 5.6.11 : 7 Mo de texte environ ; avant, tout document était coupé à 200 fragments sans avertissement


def chunks(text,size=3500,overlap=350,maximum=CHUNK_LIMIT):
    text=text.replace('\x00','').strip();out=[];start=0
    while start<len(text):
        end=min(len(text),start+size)
        if end<len(text):
            boundary=max(text.rfind('\n',start+size//2,end),text.rfind('. ',start+size//2,end))
            if boundary>start:end=boundary+1
        part=text[start:end].strip()
        if part:out.append(part)
        if end>=len(text):break
        start=max(start+1,end-overlap)
    return out[:max(1,int(maximum))]


class Embedder:
    def __init__(self,ollama,rag):
        self.model=rag.get('embedding_model','qwen3-embedding:0.6b')
        self.http=HTTP(ollama['url'],local_only=True,timeout=rag.get('timeout_seconds',180))
        shown=self.http.json('POST','/api/show',{'model':self.model})
        if shown.get('remote_host') or shown.get('remote_model'):raise Stop('modele_embedding_distant_refuse')

    def embed(self,texts):
        if not texts:return []
        result=self.http.json('POST','/api/embed',{'model':self.model,'input':texts,'truncate':True,
                                                   'options':{'temperature':0}})
        vectors=result.get('embeddings')
        if not isinstance(vectors,list) or len(vectors)!=len(texts):raise Stop('embeddings_invalides')
        for vector in vectors:
            if not isinstance(vector,list) or not 8<=len(vector)<=16384 or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in vector):
                raise Stop('embeddings_invalides')
        return vectors


def cosine(a,b):
    if len(a)!=len(b) or not a:return -1.0
    aa=sum(x*x for x in a);bb=sum(x*x for x in b)
    return sum(x*y for x,y in zip(a,b))/math.sqrt(aa*bb) if aa and bb else -1.0


def lexical_terms(terms):
    return list(dict.fromkeys(t for term in terms for t in __import__('re').findall(r'[a-z0-9]{3,}',fold(term))))[:30]

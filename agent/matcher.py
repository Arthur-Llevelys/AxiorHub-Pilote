"""Explainable matter matching; ambiguity always fails closed."""
from collections import defaultdict
import re
from .common import fold

PUBLIC_DOMAINS={'gmail.com','outlook.com','hotmail.com','yahoo.fr','yahoo.com','orange.fr','free.fr','icloud.com','proton.me'}


def boundary(value,text):
    value=fold(value)
    return len(value)>=4 and re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',text) is not None


def rank(mail,matters,history=(),evidence=None):
    evidence=evidence or {}
    current=fold(mail.subject+'\n'+mail.text[:12000])
    historical=fold('\n'.join(getattr(x,'subject','')+'\n'+getattr(x,'text','')[:3000] for x in history[-10:]))
    sender=mail.sender.lower();domain=sender.rsplit('@',1)[-1] if '@' in sender else ''
    domain_owners=defaultdict(set)
    for m in matters:
        for p in m.get('correspondents',[]):
            d=p.get('email','').lower().rsplit('@',1)[-1]
            if d and d not in PUBLIC_DOMAINS:domain_owners[d].add(m['id'])
    candidates=[]
    for m in matters:
        score=0;reasons=[]
        people=[p for p in m.get('correspondents',[]) if p.get('email','').lower()==sender]
        refs=[m['id']]+m.get('references',[])
        explicit=[r for r in refs if boundary(r,current)]
        old=[r for r in refs if boundary(r,historical)]
        names=[m.get('client_name','')]+m.get('aliases',[])
        named=[n for n in names if len(fold(n))>=5 and fold(n) in current]
        folder=m.get('path','').rsplit('/',1)[-1]
        full_folder=bool(len(fold(folder))>=12 and boundary(folder,current))
        if people:score+=70;reasons.append({'signal':'adresse_exacte','weight':70,'value':sender})
        if explicit:score+=60;reasons.append({'signal':'reference_explicite','weight':60,'value':explicit[:3]})
        if named:score+=25;reasons.append({'signal':'nom_dans_message','weight':25,'value':named[:2]})
        if full_folder:score+=50;reasons.append({'signal':'nom_dossier_complet','weight':50,'value':folder})
        if old:score+=15;reasons.append({'signal':'reference_dans_fil','weight':15,'value':old[:2]})
        if domain in domain_owners and domain_owners[domain]=={m['id']}:score+=12;reasons.append({'signal':'domaine_unique','weight':12,'value':domain})
        for item in evidence.get(m['id'],[]):
            score+=max(-100,min(20,int(item.get('weight',0))));reasons.append(item)
        if any(x.get('signal')=='association_rejetee' for x in reasons):score=0
        if score>0:
            role=people[0]['role'] if len({p['role'] for p in people})==1 and people else None
            candidates.append({'matter':m,'score':min(score,100),'role':role,'reasons':reasons})
    candidates.sort(key=lambda x:(-x['score'],x['matter']['id']))
    explicit_conflicts=[x for x in candidates if any(r['signal']=='reference_explicite' for r in x['reasons'])]
    automatic=[x for x in candidates if any(r['signal']=='association_automatique' for r in x['reasons'])]
    if len({x['matter']['id'] for x in automatic})>1:return None,None,candidates
    if len(explicit_conflicts)>1:return None,None,candidates
    if not candidates:return None,None,[]
    if len(automatic)==1:
        automatic[0]['score']=100
        return automatic[0]['matter'],automatic[0]['role'],candidates
    exact=[x for x in candidates if any(r['signal']=='adresse_exacte' for r in x['reasons'])]
    if len(exact)==1 and (not explicit_conflicts or explicit_conflicts[0] is exact[0]):
        exact[0]['score']=100;return exact[0]['matter'],exact[0]['role'],candidates
    if len(exact)>1 and len(explicit_conflicts)==1 and explicit_conflicts[0] in exact:
        explicit_conflicts[0]['score']=100;return explicit_conflicts[0]['matter'],explicit_conflicts[0]['role'],candidates
    if len(explicit_conflicts)==1 and not exact:
        explicit_conflicts[0]['score']=max(explicit_conflicts[0]['score'],96)
        return explicit_conflicts[0]['matter'],None,candidates
    top=candidates[0];lead=top['score']-(candidates[1]['score'] if len(candidates)>1 else 0)
    if top['score']>=95 and lead>=20:return top['matter'],top['role'],candidates
    return None,None,candidates

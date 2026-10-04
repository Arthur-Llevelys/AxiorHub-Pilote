"""Bounded browser settings that have a real, auditable effect on the local agent."""
import re

from .common import Stop,load_matters
from .desk import save_matter
from .model import Model
from .ai_gateway import save_provider as save_ai_provider,save_route as save_ai_route,test_provider as test_ai_provider


def save_model(desk,model,role):
    if role not in ('fast','complex','control') or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',str(model)):
        raise Stop('modele_local_invalide')
    # Model checks Ollama /api/show and rejects cloud-backed model aliases.
    Model({**desk.c['ollama'],'model':model})
    desk.setting('model:role:'+role,model)
    desk.audit('modele_local_selectionne',{'role':role,'model':model})
    return {'role':role,'model':model,'verified_local':True}


def save_profile(desk,name,style,guidance):
    name,style,guidance=(str(s or '').strip() for s in (name,style,guidance))
    if len(name)>160 or len(style)>1500 or len(guidance)>3000 or any('\x00' in s for s in (name,style,guidance)):
        raise Stop('profil_cabinet_invalide')
    value={'name':name,'style':style,'guidance':guidance}
    desk.setting('cabinet:profile',value)
    desk.audit('profil_cabinet_modifie',{'has_name':bool(name),'style_length':len(style),'guidance_length':len(guidance)})
    return {'saved':True}


def save_matter_profile(desk,matter_id,client_name,references):
    matter=next((dict(row) for row in load_matters(desk.c) if row['id']==matter_id),None)
    if not matter:raise Stop('dossier_absent')
    name=str(client_name or '').strip()
    refs=list(dict.fromkeys(s.strip() for s in str(references or '').split(',') if s.strip()))
    if not name or len(name)>200 or any(ord(ch)<32 for ch in name):raise Stop('nom_client_invalide')
    if len(refs)>20 or any(len(s)>100 or any(ord(ch)<32 for ch in s) for s in refs):
        raise Stop('references_dossier_invalides')
    matter['client_name']=name
    matter['references']=list(dict.fromkeys([matter_id]+refs))
    save_matter(desk.c,matter)
    desk.audit('identite_dossier_corrigee',{'matter':matter_id,'reference_count':len(refs)})
    return {'matter':matter_id,'saved':True}

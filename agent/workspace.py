"""Resumable folder discovery and explicitly scoped, internal conversations."""
from datetime import datetime, timezone, timedelta
import json
import re
import time

from .common import Stop, clean_path, under, digest, fold, load_matters
from .dav import DAV
from .desk import now, save_matter, report_for
from .index import DocumentIndex
from .mailbox import Mailbox


def allowed(c,path):
    if not isinstance(path,str) or not path.startswith('/') or len(path)>1500:
        raise Stop('chemin_nextcloud_absolu_requis')
    path=clean_path(path)
    if not any(under(path,r) for r in c['nextcloud']['roots']):
        raise Stop('fichier_hors_racines')
    if any(p.startswith('.') or fold(p) in ('secrets','mots de passe') for p in path.split('/') if p):
        raise Stop('repertoire_exclu')
    return path


def catalog(desk,path):
    path=allowed(desk.c,path)
    desk.db.execute('INSERT OR REPLACE INTO directories VALUES (?,?)',(path,now()))


def discovery_roots(c):
    """Roots containing actual client matters, distinct from readable sources."""
    configured=c['nextcloud'].get('matter_roots') or []
    if not configured:
        configured=[]
        for root in c['nextcloud']['roots']:
            normalized=clean_path(root)
            configured.append(normalized+'/01 - Dossiers'
                              if fold(normalized.rsplit('/',1)[-1])=='cabinet exemple'
                              else normalized)
    roots=sorted({allowed(c,x) for x in configured},key=len)
    return [r for i,r in enumerate(roots) if not any(under(r,x) for x in roots[:i])]


def discover(desk,dav):
    c=desk.c
    roots=discovery_roots(c)
    fingerprint=digest(json.dumps([c['nextcloud'].get('url'),c['nextcloud'].get('username'),roots]))
    progress=desk.settings('discovery_progress',{})
    if progress.get('fingerprint')!=fingerprint or not progress.get('queue'):
        progress={'fingerprint':fingerprint,'queue':[[r,0] for r in roots],
                  'visited':[],'added':0,'errors':[],'depth_limited':0,'started':now()}
    queue=progress['queue'];visited=set(progress['visited'])
    matters=load_matters(c);paths={m['path'] for m in matters};ids={m['id'] for m in matters}
    batch=0;start=time.monotonic()
    while queue and batch<25 and time.monotonic()-start<20:
        path,depth=queue.pop(0)
        if path in visited:continue
        allowed(c,path);batch+=1;visited.add(path)
        try:children=dav.list_folder(path)
        except Stop as ex:
            progress['errors']=(progress['errors']+[{'path':path,'reason':str(ex)}])[-30:]
            continue
        catalog(desk,path)
        for item in children:
            if not item['directory']:continue
            child=allowed(c,item['path'])
            if child==path or not under(child,path):raise Stop('reponse_dav_hors_dossier')
            catalog(desk,child)
            name=child.rsplit('/',1)[-1]
            match=re.search(r'(?:^|[ -])(\d{8,12})$',name)
            if match:
                if child not in paths:
                    ref=match[1];mid=ref if ref not in ids else 'DOS-'+digest(child)[:12]
                    names=[x.strip() for x in name.split(' - ') if len(x.strip())>=4
                           and fold(x.strip()) not in ('assistance','contentieux','postulation','conseil',ref)]
                    save_matter(c,{'id':mid,'path':child,'client_name':name.split(' - ')[0],
                        'aliases':[name],'references':list(dict.fromkeys([ref]+names)),
                        'correspondents':[],'discovered_at':now()})
                    paths.add(child);ids.add(mid);progress['added']+=1
                # A recognized matter is a scope, not a container of unrelated cases.
                continue
            if depth<12:
                queue.append([child,depth+1])
            else:progress['depth_limited']+=1
        desk.db.commit()
    if len(queue)+len(visited)>50000:raise Stop('catalogue_trop_volumineux')
    progress['visited']=sorted(visited)
    desk.setting('discovery_progress',progress)
    result={'dossiers_decouverts':progress['added'],'repertoires_lus':len(visited),
            'repertoires_restants':len(queue),'parcours_partiel':bool(queue),
            'repertoires_inaccessibles':progress['errors'],
            'limite_profondeur_atteinte':progress['depth_limited'],
            'at':now()}
    desk.setting('last_discovery',result)
    return result


def browse(desk,args,dav):
    path=allowed(desk.c,args.get('path',''))
    children=dav.list_folder(path)
    catalog(desk,path)
    count=0
    for item in children:
        if item['directory']:
            child=allowed(desk.c,item['path'])
            if child==path or not under(child,path):raise Stop('reponse_dav_hors_dossier')
            catalog(desk,child);count+=1
    desk.db.commit()
    return {'chemin':path,'sous_dossiers':count,'action_suivante':'Vue Dossiers : rechercher le nom, puis Enregistrer comme dossier.'}


def register(desk,args,dav):
    path=allowed(desk.c,args.get('path','').strip())
    ref=args.get('reference','').strip();name=args.get('client_name','').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ref):raise Stop('reference_invalide')
    if not name or len(name)>200 or any(ord(x)<32 for x in name):raise Stop('nom_client_invalide')
    if path in {clean_path(x) for x in desk.c['nextcloud']['roots']}:
        raise Stop('choisir_un_dossier_et_non_la_racine_du_cabinet')
    matters=load_matters(desk.c)
    existing=next((m for m in matters if m['path']==path),None)
    if existing:return {'dossier':existing['id'],'etat':'deja_enregistre'}
    if any(m['id']==ref for m in matters):raise Stop('reference_deja_utilisee')
    # A Depth:1 PROPFIND confirms the directory exists and is readable. No MKCOL.
    dav.list_folder(path)
    references=[x.strip() for x in args.get('references','').split(',') if x.strip()]
    if len(references)>20 or any(len(x)>120 for x in references):raise Stop('references_trop_longues')
    save_matter(desk.c,{'id':ref,'client_name':name,'path':path,'aliases':[name],
        'references':list(dict.fromkeys([ref]+references)), 'correspondents':[],
        'registered_at':now()})
    desk.audit('dossier_enregistre',{'id':ref,'path':path})
    desk.enqueue('index',{'matter':ref})
    return {'dossier':ref,'etat':'enregistre','indexation':'mise_en_attente'}


def create_matter(desk,args,dav):
    if args.get('confirm')!='yes':raise Stop('confirmation_explicite_requise')
    path=allowed(desk.c,args.get('path','').strip())
    if path in {m['path'] for m in load_matters(desk.c)}:raise Stop('dossier_deja_enregistre')
    dav.create_folder(path)
    try:return {**register(desk,args,dav),'repertoire_nextcloud':'cree'}
    except Exception:
        # A partial result is never hidden and the fresh directory is not deleted.
        raise Stop('repertoire_cree_enregistrement_a_reprendre') from None


def chat_scope(c,args):
    mid=args.get('matter','');key=args.get('key','')
    matter=next((m for m in load_matters(c) if m['id']==mid),None) if mid else None
    if mid and not matter:raise Stop('dossier_absent')
    report=report_for(c,key) if key else None
    if matter:
        allowed(c,matter['path'])
        if report and report.get('matter')!=mid:raise Stop('courriel_non_associe_a_ce_dossier')
    scope=digest(json.dumps([c['mail']['username'],c['mail']['host'],
        c['nextcloud'].get('url'),c['nextcloud'].get('username'),
        mid,matter['path'] if matter else '',key],ensure_ascii=False))
    return scope,matter,report


def history(desk,scope):
    cutoff=(datetime.now(timezone.utc)-timedelta(days=90)).isoformat()
    desk.db.execute('DELETE FROM conversations WHERE created<?',(cutoff,));desk.db.commit()
    return desk.db.execute('SELECT * FROM conversations WHERE scope=? ORDER BY id DESC LIMIT 20',(scope,)).fetchall()


def fit_chat_context(payload,limit):
    """Keep the question and essential evidence, then prefer recent conversation turns."""
    from .context import payload_size
    omitted_turns=0;omitted_sources=0
    while payload_size(payload)>limit and payload['historique_non_probant']:
        payload['historique_non_probant'].pop(0);omitted_turns+=1
    while payload_size(payload)>limit:
        optional=next((i for i in range(len(payload['sources'])-1,-1,-1)
            if payload['sources'][i].get('kind') not in ('piece_jointe_locale','courriel')),None)
        if optional is None:break
        payload['sources'].pop(optional);omitted_sources+=1
    if omitted_turns or omitted_sources:
        payload['limites']+=(' Contexte réduit : '+str(omitted_turns)+' échange(s) ancien(s) et '
            +str(omitted_sources)+' source(s) secondaire(s) omis. Indiquer les informations manquantes '
             'si la question exige ces éléments ; ne pas supposer qu’ils sont absents du dossier.')
        while payload_size(payload)>limit:
            optional=next((i for i in range(len(payload['sources'])-1,-1,-1)
                if payload['sources'][i].get('kind') not in ('piece_jointe_locale','courriel')),None)
            if optional is None:break
            payload['sources'].pop(optional)
    if payload_size(payload)>limit:
        raise Stop('question_et_sources_essentielles_depassent_contexte')
    return payload


def chat(desk,args,dav,model,box=None,return_result=False):
    from .model import CHAT,validate
    scope,matter,report=chat_scope(desk.c,args)
    question=args.get('question','').strip()
    if not question or len(question)>12000:raise Stop('question_requise_12000_caracteres_maximum')
    sources=[];coverage={};attachment_coverage=None;selected_reports=[]
    limit=min(60000,desk.c['ollama'].get('max_context_chars',65000))
    attachment_ids=args.get('attachment_ids') if isinstance(args.get('attachment_ids'),list) else []
    if not attachment_ids and args.get('attachment_id'):attachment_ids=[args['attachment_id']]
    if len(attachment_ids)>3:raise Stop('trois_pieces_maximum')
    attachment_reports=[]
    for attachment_id in attachment_ids:
        from .long_context363 import analyze_attachment
        extracted,current_coverage=analyze_attachment(desk,attachment_id,question,
            args.get('matter',''),args.get('key',''),model,limit)
        sources.extend(extracted)
        attachment_reports.append(current_coverage)
    if attachment_reports:
        attachment_coverage={'extracted_chars':sum(x.get('extracted_chars',0) for x in attachment_reports),
          'parts_analyzed':sum(x.get('parts_analyzed',0) for x in attachment_reports),
          'legacy_partial':any(x.get('legacy_partial') for x in attachment_reports),
          'documents':len(attachment_reports)}
    selected=(args.get('page_context') or {}).get('selected_documents',[])
    if selected:
        if not isinstance(selected,list) or len(selected)>20 or not matter or dav is None:
            raise Stop('documents_selectionnes_contexte_invalide')
        from pathlib import PurePosixPath
        from .documents import extract
        from .long_documents365 import analyze_pages, pages_from_text
        for ordinal,path in enumerate(selected,1):
            path=clean_path(str(path))
            if not under(path,matter['path']):raise Stop('source_mission_autre_dossier')
            text=extract(dav.download(dav.stat(path)),PurePosixPath(path).name,
                         {**desk.c['documents'],'max_document_chars':2_000_000})
            if not isinstance(text,str):text=text.get('text','')
            if not text.strip():raise Stop('document_sans_texte_exploitable')
            sid='selection567-'+str(ordinal)
            if len(text)>6000 or len(pages_from_text(text))>1:
                items,proof=analyze_pages(desk,pages_from_text(text),sid,path,question,model,limit,'assistant')
                sources.extend({**item,'kind':'piece_jointe_locale'} for item in items)
                selected_reports.append({'path':path,**proof})
            else:
                sources.append({'id':sid,'kind':'piece_jointe_locale','path':path,'excerpt':text,'partial':False})
    if report:
        mail=box.fetch(report['source_mailbox'],report['source_uid'])
        account=desk.c['mail']['username']+'@'+desk.c['mail']['host']
        if mail.key(account)!=args['key']:raise Stop('identite_imap_modifiee')
        sources.append({'id':'incoming','kind':'courriel','subject':mail.subject,
            'date':mail.timestamp.isoformat(),'excerpt':mail.text[:18000],
            'partial':len(mail.text)>18000})
    if matter:
        from .legal_memory import memory_sources
        sources+=memory_sources(desk,matter['id'],question,8)
        from .strategic import assistant_sources
        sources+=assistant_sources(desk,matter['id'],4)
        docs,coverage=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama')).sources(dav,matter,[question],
                         {**desk.c['documents'],'max_documents_per_mail':3})
        sources+=docs
    elif not report:
        found,coverage=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama')).global_sources(
            [question],limit=12)
        sources+=found
    if selected_reports:coverage={**coverage,'selections':selected_reports}
    if attachment_coverage:coverage={**coverage,'piece_televersee':attachment_coverage}
    from .composition567 import compact_sources
    sources=compact_sources(desk,sources,question,model,limit)
    turns=list(reversed(history(desk,scope)[:4]))
    from .relevance370 import correction_guidance
    from .learning392 import learning_context
    payload={'question_avocat':question,'dossier':{'id':matter['id'],'nom':matter['client_name']} if matter else None,
        'sources':sources,'couverture_documentaire':coverage,
        'contexte_interface':args.get('page_context',{}) if isinstance(args.get('page_context'),dict) else {},
        'preferences_cabinet':desk.settings('cabinet:profile',{}),
        'preferences_assistant':__import__('agent.assistant567',fromlist=['drafting_preferences']).drafting_preferences(desk,getattr(desk,'mission_owner567','cabinet')),
        'corrections_approuvees':correction_guidance(desk,matter['id'] if matter else ''),
        'apprentissage_metier':learning_context(desk,matter['id'] if matter else '','assistant'),
        'historique_non_probant':[{'question':r['question'][:1000],
            'reponse':json.loads(r['response'])['answer'][:1500]} for r in turns],
        'limites':('Sélection d’extraits indexés seulement. Aucune recherche juridique externe. '
                   'Aucune opération exécutée par la conversation. La recherche générale peut '
                   'parcourir plusieurs dossiers ; une action de rédaction doit ensuite être '
                   'rattachée à un courriel et à un dossier précis.')}
    fit_chat_context(payload,limit)
    if not sources:
        payload['limites']+=' Aucune source lisible disponible : demander des précisions, ne pas inventer de faits.'
    result=model.ask('chat',payload)
    # Compatibility with deterministic test/local adapters from 1.4; the real
    # 1.5 Model already enforces the complete schema before returning.
    if isinstance(result,dict) and set(result)=={'answer','source_ids','limits'}:
        result={**result,'proposed_actions':[]}
    validate(result,CHAT)
    if any(x not in {s['id'] for s in sources} for x in result['source_ids']):
        raise Stop('source_conversation_invalide')
    if attachment_coverage:
        result['limits'].append('Pièce téléversée : '+str(attachment_coverage['extracted_chars'])+' caractères extraits, '
            +str(attachment_coverage['parts_analyzed'])+' section(s) analysée(s). '
            +('Ancien import limité : téléversez de nouveau le fichier pour couvrir toutes les pages.'
               if attachment_coverage.get('legacy_partial') else
               'Synthèse automatisée des sections ; vérifier le document original, notamment tableaux et images.'))
    # Check scope again before making any response visible after a long model call.
    if chat_scope(desk.c,args)[0]!=scope:raise Stop('dossier_modifie_pendant_la_discussion')
    desk.db.execute('INSERT INTO conversations VALUES(NULL,?,?,?,?,?)',
        (scope,question,json.dumps(result),json.dumps(sources),now()))
    desk.db.execute('DELETE FROM conversations WHERE scope=? AND id NOT IN (SELECT id FROM conversations WHERE scope=? ORDER BY id DESC LIMIT 100)',(scope,scope))
    desk.db.commit()
    if return_result:
        return {**result,'sources':[{'id':s['id'],'matter':s.get('matter',''),
            'path':s.get('path',''),'modified':s.get('modified',''),
            'excerpt':s.get('excerpt','')[:1200]} for s in sources if s['id'] in result['source_ids']]}
    return {'reponse':'disponible_dans_Assistant','sources':len(sources),
            'brouillon_imap_cree':False,'operation_externe':False}


def perform(desk,kind,args):
    if kind=='forget_chat':
        scope,_,_=chat_scope(desk.c,args)
        desk.db.execute('DELETE FROM conversations WHERE scope=?',(scope,));desk.db.commit()
        return {'conversation':'oubliee'}
    if kind=='chat':
        from .model import Model,routed_config
        _,matter,report=chat_scope(desk.c,args)
        box=Mailbox(desk.c['mail']) if report else None
        try:return chat(desk,args,DAV(desk.c['nextcloud']) if matter else None,Model(routed_config(desk.c,'assistant')),box)
        finally:
            if box:box.close()
    dav=DAV(desk.c['nextcloud'])
    return {'discover':lambda:discover(desk,dav), 'browse':lambda:browse(desk,args,dav),
            'register_matter':lambda:register(desk,args,dav),
            'create_matter':lambda:create_matter(desk,args,dav)}[kind]()

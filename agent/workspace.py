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
    from . import charge5693   # 5.6.93 : Nextcloud : un répertoire (hors dossiers) dont l'ETag n'a pas changé depuis le dernier parcours complet n'est pas relu
    if progress.get('fingerprint')!=fingerprint or not progress.get('queue'):
        same,root_etags=charge5693.decouverte_inchangee(desk,dav,roots)
        if same and progress.get('fingerprint')==fingerprint:
            result={**(desk.settings('last_discovery',{}) or {}),'dossiers_decouverts':0,'repertoires_lus':0,'repertoires_restants':0,
                    'parcours_partiel':False,'inchange':True,'at':now()}
            desk.setting('last_discovery',result)
            return result
        progress={'fingerprint':fingerprint,'queue':[[r,0] for r in roots],
                  'visited':[],'added':0,'errors':[],'depth_limited':0,'started':now(),'root_etags':root_etags,'folders':{}}
    known=charge5693.decouverte_precedente(desk,dav)
    folders=progress.setdefault('folders',{})
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
            folders.pop(path,None)   # 5.6.93 : un répertoire illisible sera relu au prochain parcours, même inchangé
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
                etag=item.get('etag') or ''
                if etag:folders[child]=etag
                if etag and known.get(child)==etag:continue   # 5.6.93 : sous-arborescence inchangée : aucun dossier nouveau possible
                queue.append([child,depth+1])
            else:progress['depth_limited']+=1
        desk.db.commit()
    if len(queue)+len(visited)>50000:raise Stop('catalogue_trop_volumineux')
    progress['visited']=sorted(visited)
    if not queue and progress.get('root_etags') and not progress.get('errors'):
        charge5693.decouverte_terminee(desk,progress['root_etags'],folders)
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


def _controle_enregistrement(desk,args):
    """5.6.97 (B03) : référence, nom, chemin canonique et unicité vérifiés AVANT tout accès au stockage (aucun MKCOL pour « ### »).
    Unicité contrôlée sur le registre complet (dossiers réservés compris) et sur le registre unique des affaires."""
    path=allowed(desk.c,args.get('path','').strip())
    ref=args.get('reference','').strip();name=args.get('client_name','').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',ref):raise Stop('reference_invalide')
    if not name or len(name)>200 or any(ord(x)<32 for x in name):raise Stop('nom_client_invalide')
    if path in {clean_path(x) for x in desk.c['nextcloud']['roots']}:
        raise Stop('choisir_un_dossier_et_non_la_racine_du_cabinet')
    references=[x.strip() for x in args.get('references','').split(',') if x.strip()]
    if len(references)>20 or any(len(x)>120 for x in references):raise Stop('references_trop_longues')
    matters=load_matters(desk.c,unfiltered=True)
    existing=next((m for m in matters if m['path']==path),None)
    from . import dossiers5697
    dossiers5697.ensure_schema(desk)
    if not existing and (any(m['id']==ref for m in matters) or dossiers5697.normaliser_reference(ref) in dossiers5697._refs_sqlite(desk)
                         or desk.db.execute('SELECT 1 FROM dossiers5697 WHERE id=?',(ref,)).fetchone()):
        raise Stop('reference_deja_utilisee')
    return path,ref,name,references,existing


def register(desk,args,dav):
    path,ref,name,references,existing=_controle_enregistrement(desk,args)
    if existing:return {'dossier':existing['id'],'etat':'deja_enregistre'}
    # A Depth:1 PROPFIND confirms the directory exists and is readable. No MKCOL.
    dav.list_folder(path)
    from .dossiers5697 import verrou_registre,importer_un
    with verrou_registre(desk.c['state_dir']):   # 5.6.97 (B03) : revérification et écriture sous le même verrou entre processus
        path,ref,name,references,existing=_controle_enregistrement(desk,args)
        if existing:return {'dossier':existing['id'],'etat':'deja_enregistre'}
        matter={'id':ref,'client_name':name,'path':path,'aliases':[name],
            'references':list(dict.fromkeys([ref]+references)), 'correspondents':[],
            'registered_at':now()}
        save_matter(desk.c,matter)
    importer_un(desk,matter)   # 5.6.97 : registre unique des affaires (une seule source d'écriture)
    desk.audit('dossier_enregistre',{'id':ref,'path':path})
    desk.enqueue('index',{'matter':ref})
    return {'dossier':ref,'etat':'enregistre','indexation':'mise_en_attente'}


def create_matter(desk,args,dav):
    if args.get('confirm')!='yes':raise Stop('confirmation_explicite_requise')
    path,_ref,_name,_refs,existing=_controle_enregistrement(desk,args)   # 5.6.97 (B03) : tout est vérifié avant le MKCOL
    if existing:raise Stop('dossier_deja_enregistre')
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


UNREADABLE={'ocr_sans_texte_reconnu':'aucun texte reconnu (scan ou image)','ocr_qualite_insuffisante':'lecture trop incertaine',
            'document_sans_texte_exploitable':'aucun texte exploitable','pdf_scan_ocr_necessaire_installer_poppler_et_tesseract':'scan à lire par OCR (outils absents)',
            'piece_trop_volumineuse':'pièce trop volumineuse','http_404':'pièce introuvable (déplacée ou supprimée ?)',
            'document_office_non_lisible':'document Office illisible','extraction_locale_echouee':'lecture impossible',
            'document_trop_long_pour_analyse':'document trop long'}


def unreadable_reason(code):
    """5.6.61 : motif lisible d'une pièce jointe non lue, ou '' si l'erreur n'est pas une simple pièce illisible."""
    code=str(code or '').split(':')[0]
    if code.startswith('format_a_convertir_'):return 'format à convertir ('+code[len('format_a_convertir_'):]+')'
    return UNREADABLE.get(code,'')


def chat(desk,args,dav,model,box=None,return_result=False):
    from .model import CHAT,validate
    scope,matter,report=chat_scope(desk.c,args)
    question=args.get('question','').strip()
    if not question or len(question)>12000:raise Stop('question_requise_12000_caracteres_maximum')
    sources=[];coverage={};attachment_coverage=None;selected_reports=[]
    from . import acces5683   # 5.6.83 : courriels et agenda lus quand la question les vise ; date du jour transmise
    from .membres5650 import viewer_hidden
    hidden=viewer_hidden();cabinet_sources=[];cabinet_access={};cabinet_only=False
    limit=min(60000,desk.c['ollama'].get('max_context_chars',65000))
    attachment_ids=args.get('attachment_ids') if isinstance(args.get('attachment_ids'),list) else []
    if not attachment_ids and args.get('attachment_id'):attachment_ids=[args['attachment_id']]
    from .improvements36 import MAX_ATTACHMENTS
    if len(attachment_ids)>MAX_ATTACHMENTS:raise Stop('trop_de_pieces_jointes')
    # 5.6.96 (R01, R03) : périmètre avant tout. Une question publique est servie par l'outil du bon tour (politique, confirmation du texte exact,
    # budget), jamais par le modèle ni avec le dossier ; le mode « Général » n'emporte aucun contexte du tour précédent.
    from . import recherche_publique5696 as public
    cadre=public.cadrer(question,{'attachments':attachment_ids})
    general=cadre['general']
    if general:question=cadre['texte']
    if return_result and cadre['perimetre']=='public':
        return public.reponse(desk,cadre,str(args.get('auteur') or getattr(desk,'mission_owner567','cabinet')),pieces=bool(attachment_ids))
    attachment_reports=[];unreadable=[]
    # 5.6.80 : question rapide : seuls les passages utiles partent au modèle ; l'analyse progressive (une génération par section) ne sert
    # plus qu'à une lecture complète demandée d'une pièce trop longue, jamais à la voix
    rapide=bool(args.get('rapide'));full_limit=limit;compact=True
    from . import rapide5680
    voice=rapide5680.is_voice(args.get('purpose'))
    previous=[] if general else history(desk,scope)   # 5.6.96 (R01) : mode Général : aucun contexte du tour précédent
    prev=previous[0]['question'] if previous else ''
    owner=str(args.get('auteur') or 'cabinet')   # 5.6.96 (M04) : la personne qui demande (fuseau, compte autorisé), transmise à la lecture
    # 5.6.96 (M02, M03) : une liste simple de courriels est rendue depuis le connecteur au premier tour, en-têtes seulement, sans modèle
    if (return_result and not report and not attachment_ids and not (isinstance(args.get('page_context'),dict) and args['page_context'].get('selected_documents'))
            and acces5683.liste_simple(question,prev)):
        cabinet_sources,cabinet_access,_=acces5683.gather(desk,question,matter['id'] if matter else '',hidden,prev,voice,box,owner,just_headers=True)
        if cabinet_access.get('courriels'):
            result=acces5683.liste_answer(cabinet_sources,cabinet_access['courriels'],voice)
            desk.db.execute('INSERT INTO conversations VALUES(NULL,?,?,?,?,?)',(scope,question,json.dumps(result),json.dumps(cabinet_sources),now()))
            desk.db.commit()
            return result
    from . import societe5688   # 5.6.88 : sociétés citées : situation lue sans modèle, attente bornée, avant tout le reste
    controle=args['societe5688'] if isinstance(args.get('societe5688'),dict) else societe5688.avant(desk,question,matter['id'] if matter else '','voix' if voice else 'assistant')
    from . import vueia5688   # 5.6.88 : catégories ouvertes au modèle pour cette personne, contrôlées avant chaque lecture
    open_mail,open_documents=not vueia5688.blocked(desk,'courriels'),not vueia5688.blocked(desk,'documents')
    cabinet_sources,cabinet_access,cabinet_only=acces5683.gather(desk,question,matter['id'] if matter else '',hidden,prev,voice,box,owner)   # 5.6.96 (M04) : auteur transmis
    sources.extend(cabinet_sources)
    if not cabinet_access.get('courriels') and not cabinet_access.get('agenda'):cabinet_only=False
    if rapide:
        budget=int(rapide5680.context_limit(desk.c,args.get('purpose'),limit)*0.6)
        picked,report_pick,attachment_ids,unread=rapide5680.attachment_sources(desk,attachment_ids,question,args.get('matter',''),args.get('key',''),budget,voice)
        sources.extend(picked);unreadable.extend(unread)
        if report_pick:attachment_reports.append(report_pick)
        if not attachment_ids:
            limit=rapide5680.context_limit(desk.c,args.get('purpose'),limit);compact=False
    for attachment_id in attachment_ids:
        from .long_context363 import analyze_attachment
        try:
            extracted,current_coverage=analyze_attachment(desk,attachment_id,question,
                args.get('matter',''),args.get('key',''),model,limit)
        except Stop as ex:   # 5.6.61 : une pièce illisible ou introuvable n'empêche plus la réponse ; elle est signalée
            if not unreadable_reason(str(ex)):raise
            unreadable.append(unreadable_reason(str(ex)));continue
        sources.extend(extracted)
        attachment_reports.append(current_coverage)
    if unreadable:
        question=question+'\n\n[Information AxiorHub : %d pièce(s) jointe(s) non lue(s) (%s) ; ne pas en déduire leur contenu.]'%(len(unreadable),'; '.join(unreadable))
    if attachment_reports:
        attachment_coverage={'extracted_chars':sum(x.get('extracted_chars',0) for x in attachment_reports),
          'parts_analyzed':sum(x.get('parts_analyzed',0) for x in attachment_reports),
          'legacy_partial':any(x.get('legacy_partial') for x in attachment_reports),
          'documents':len(attachment_reports)}
    selected=(args.get('page_context') or {}).get('selected_documents',[]) if open_documents else []
    if selected:
        if not isinstance(selected,list) or len(selected)>20 or not matter or dav is None:
            raise Stop('documents_selectionnes_contexte_invalide')
        from pathlib import PurePosixPath
        from .documents import extract
        from .long_documents365 import analyze_pages, pages_from_text
        for ordinal,path in enumerate(selected,1):
            path=clean_path(str(path))
            if not under(path,matter['path']):raise Stop('source_mission_autre_dossier')
            try:   # 5.6.68 : scan illisible → signalé, la réponse se fait avec les autres sources
                text=extract(dav.download(dav.stat(path)),PurePosixPath(path).name,
                             {**desk.c['documents'],'max_document_chars':2_000_000})
                if not isinstance(text,str):text=text.get('text','')
                if not text.strip():raise Stop('document_sans_texte_exploitable')
            except Stop as ex:
                if not unreadable_reason(str(ex)):raise
                unreadable.append(PurePosixPath(path).name+' : '+unreadable_reason(str(ex)))
                question=question+'\n\n[Information AxiorHub : pièce sélectionnée non lue (%s) ; ne pas en déduire son contenu.]'%unreadable[-1]
                continue
            sid='selection567-'+str(ordinal)
            if len(text)>6000 or len(pages_from_text(text))>1:
                items,proof=analyze_pages(desk,pages_from_text(text),sid,path,question,model,limit,'assistant')
                sources.extend({**item,'kind':'piece_jointe_locale'} for item in items)
                selected_reports.append({'path':path,**proof})
            else:
                sources.append({'id':sid,'kind':'piece_jointe_locale','path':path,'excerpt':text,'partial':False})
    if report and open_mail:
        mail=box.fetch(report['source_mailbox'],report['source_uid'])
        account=desk.c['mail']['username']+'@'+desk.c['mail']['host']
        if mail.key(account)!=args['key']:raise Stop('identite_imap_modifiee')
        sources.append({'id':'incoming','kind':'courriel','subject':mail.subject,
            'date':mail.timestamp.isoformat(),'excerpt':mail.text[:18000],
            'partial':len(mail.text)>18000})
    if matter:
        if open_documents:   # 5.6.88 : mémoire et analyses sont tirées des pièces : elles suivent la catégorie « Documents »
            from .legal_memory import memory_sources
            sources+=memory_sources(desk,matter['id'],question,8)
            from .strategic import assistant_sources
            sources+=assistant_sources(desk,matter['id'],4)
            index=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
            from .recherche5695 import classeur   # 5.6.95 (R01) : service commun (droits, pièces écartées par l'avocat)
            index.classer5695=classeur(desk)
            fast=index.fast_sources(matter,[question],{**desk.c['documents'],'max_documents_per_mail':3,'recent5683':acces5683.wants_recent(question)}) if rapide else None
            docs,coverage=fast if fast is not None else index.sources(dav,matter,[question],{**desk.c['documents'],'max_documents_per_mail':3})
            sources+=docs
        else:
            coverage={'recherche':'documents '+vueia5688.REFUSED}
    elif not open_documents:
        coverage={'recherche':'documents '+vueia5688.REFUSED}
    elif not report and not (cabinet_only and not acces5683.wants_documents(question)):
        # 5.6.83 : un dossier nommé dans la question borne la recherche ; ailleurs, seuls les mots qui portent l'objet sont cherchés
        index=DocumentIndex(desk.c['state_dir'],desk.c.get('rag'),desk.c.get('ollama'))
        named=acces5683.named_matters(desk,question,hidden);terms=acces5683.focus_terms(question)
        added=0
        for current in named:
            from .recherche5695 import classeur   # 5.6.95 (R01)
            index.classer5695=classeur(desk)
            fast=index.fast_sources(current,[question],{**desk.c['documents'],'max_documents_per_mail':4,'recent5683':acces5683.wants_recent(question)})
            if fast:
                sources+=[{**s,'matter':current['id']} for s in fast[0]];coverage=fast[1];added+=len(fast[0])
        if not added and terms:
            found,coverage=index.global_sources([' '.join(terms)],limit=12,weights=acces5683.portfolio_weights(desk),recent=acces5683.wants_recent(question),floor=0.5)
            sources+=found
        elif not added:
            coverage={'recherche':'aucun objet précis dans la question : aucune pièce choisie au hasard'}
    if not open_mail:   # 5.6.88 : les courriels indexés avec les pièces suivent la catégorie « Courriels »
        sources=[s for s in sources if not str(s.get('source_kind') or s.get('kind') or '').startswith('email')]
    if selected_reports:coverage={**coverage,'selections':selected_reports}
    if attachment_coverage:coverage={**coverage,'piece_televersee':attachment_coverage}
    if compact:
        from .composition567 import compact_sources
        sources=compact_sources(desk,sources,question,model,limit)
    from . import juridique5649   # 5.6.49 : recherche juridique avant la réponse (connecteurs du cabinet, requête anonymisée)
    # 5.6.80 : bornée pour une question ; à la voix, seulement si la question porte sur le droit applicable
    legal=(rapide5680.legal_research(desk,matter['id'] if matter else '',question,voice) if rapide
           else juridique5649.research(desk,matter['id'] if matter else '',question,'assistant'))
    sources+=juridique5649.as_sources(legal)
    if not voice:   # 5.6.80 : le choix des outils par le modèle allongerait chaque tour de parole
        try:   # 5.6.52 (I01 à I15) : outils de lecture des connexions vérifiées, choisis par l'agent, exécutés par le serveur
            from .connexions5652 import agent_research
            from .i18n5631 import role as _role52
            sources+=agent_research(desk,getattr(desk,'mission_owner567','cabinet'),_role52(),question,matter['id'] if matter else '',model)
        except Exception:   # noqa: BLE001 — la réponse se fait sans outil externe
            pass
    turns=[] if general else list(reversed(history(desk,scope)[:4]))   # 5.6.96 (R01) : mode Général
    from .relevance370 import correction_guidance
    from .learning392 import learning_context
    payload={'question_avocat':question,'dossier':{'id':matter['id'],'nom':matter['client_name']} if matter else None,
        'acces_cabinet':cabinet_access,
        'sources':sources,'couverture_documentaire':coverage,
        'contexte_interface':args.get('page_context',{}) if isinstance(args.get('page_context'),dict) else {},
        'preferences_cabinet':desk.settings('cabinet:profile',{}),
        'preferences_assistant':__import__('agent.assistant567',fromlist=['drafting_preferences']).drafting_preferences(desk,getattr(desk,'mission_owner567','cabinet')),
        'corrections_approuvees':correction_guidance(desk,matter['id'] if matter else ''),
        'apprentissage_metier':learning_context(desk,matter['id'] if matter else '','assistant'),
        'dossier_structure':__import__('agent.contexte5651',fromlist=['for_drafting']).for_drafting(desk,matter['id'],question,confidential=True) if matter and open_documents else {},
        'historique_non_probant':[{'question':r['question'][:1000],
            'reponse':json.loads(r['response'])['answer'][:1500]} for r in turns],
        'limites':('Sélection d’extraits indexés seulement. '+(juridique5649.summary(legal) or 'Aucune recherche juridique externe.')+' '
                   'Aucune opération exécutée par la conversation. La recherche générale peut '
                   'parcourir plusieurs dossiers ; une action de rédaction doit ensuite être '
                   'rattachée à un courriel et à un dossier précis.')}
    if legal.get('sources'):payload['regle_sources_juridiques']=juridique5649.RULE
    payload.update(societe5688.pour_modele(controle))
    if rapide:
        rapide5680.trim_payload(payload,limit)
        try:fit_chat_context(payload,limit)
        except Stop:fit_chat_context(payload,full_limit)   # sources indispensables plus longues que le budget : budget complet
    else:
        fit_chat_context(payload,limit)
    if not sources:
        payload['limites']+=' Aucune source lisible disponible : demander des précisions, ne pas inventer de faits.'
    try:
        result=model.ask('chat',payload)
    except Stop as error:   # 5.6.83 : modèle occupé : la liste relevée reste utile, annoncée comme non analysée
        if str(error) in rapide5680.BUSY_CODES and return_result and any(s.get('kind') in ('liste_courriels','agenda') for s in sources):
            return societe5688.poser_reponse(acces5683.fallback_answer([s for s in sources if s.get('kind') in ('liste_courriels','agenda')],cabinet_access),controle)
        raise
    # Compatibility with deterministic test/local adapters from 1.4; the real
    # 1.5 Model already enforces the complete schema before returning.
    if isinstance(result,dict) and set(result)=={'answer','source_ids','limits'}:
        result={**result,'proposed_actions':[]}
    validate(result,CHAT)
    if any(x not in {s['id'] for s in sources} for x in result['source_ids']):
        raise Stop('source_conversation_invalide')
    societe5688.poser_reponse(result,controle)   # 5.6.88 : posé par le logiciel, quoi que le modèle ait écrit
    if juridique5649.summary(legal):result['limits'].append(juridique5649.summary(legal))
    result['limits'].extend(acces5683.limits_of(cabinet_access))
    if not open_documents:result['limits'].append('Documents non consultés : '+vueia5688.REFUSED+'.')
    if report and not open_mail:result['limits'].append('Courriel de cette conversation non lu : '+vueia5688.REFUSED+'.')
    unsourced=juridique5649.unsourced_references(result.get('answer',''),legal)
    if unsourced:result['limits'].append('Référence(s) à vérifier, absente(s) des sources juridiques consultées : '+' ; '.join(unsourced))
    if attachment_coverage and attachment_reports and attachment_reports[0].get('passages_total'):
        pick=attachment_reports[0]
        if not pick.get('integral'):   # 5.6.80 : l'avocat sait que la réponse repose sur des passages choisis
            result['limits'].append('Pièces jointes : '+str(pick['passages_retenus'])+' passage(s) sur '+str(pick['passages_total'])+' retenu(s) pour cette question ; '
                'demandez une « synthèse complète » pour tout faire lire.')
    elif attachment_coverage:
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
    if return_result and unreadable and isinstance(result.get('answer'),str):
        result={**result,'answer':result['answer']+'\n\nPièce(s) jointe(s) non lue(s) : '+'; '.join(unreadable)+'. La réponse ne s’appuie pas sur leur contenu.'}
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

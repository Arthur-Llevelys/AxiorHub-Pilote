"""Human-confirmed intelligence: case briefs, reviews, staged drafts and deadlines."""
from datetime import datetime,timezone,timedelta
from email.message import EmailMessage
from email import policy
from email.utils import formataddr,format_datetime
import json
from pathlib import Path
import re

from .common import Stop,digest,fold,load_matters,private_json
from .dav import DAV,available_slots
from .documents import extract
from .index import DocumentIndex
from .mailbox import Mailbox,exclusion,make_draft
from .model import (Model,CASE_BRIEF,LEGAL_MEMORY,ATTACHMENT_REVIEW,
                    DEADLINE_REVIEW,MANUAL_DRAFT,routed_config,validate)
from .state import State


FEEDBACK={'good','shorter','wrong_matter','fabricated_fact','wrong_recipient','unnecessary','legal_decision'}
STYLES={'prudent','shorter','direct','diplomatic','clarification','slots'}
SAFE_ACTIONS={'index':'index','refresh_brief':'refresh_brief','prepare_draft':'prepare_draft',
              'analyze_attachments':'attachment_review','review_deadlines':'deadline_review',
              'discover_association':'sync','prepare_event':'deadline_review','prepare_task':'deadline_review'}


def matter(c,mid):
    found=next((m for m in load_matters(c) if m['id']==mid),None)
    if not found:raise Stop('dossier_absent')
    return found


def source_index(c):return DocumentIndex(c['state_dir'],c.get('rag'),c.get('ollama'))


def bounded_sources(c,sources,reserve=12000):
    maximum=c['ollama'].get('max_context_chars',65000)-reserve
    while len(json.dumps(sources,ensure_ascii=False))>maximum and sources:sources.pop()
    if not sources:raise Stop('aucune_source_exploitable')
    return sources


def fit_manual_draft_context(payload,limit):
    """Reserve room for the verification pass without dropping the incoming email."""
    from .context import payload_size
    # The control pass adds up to 7,000 characters of proposed text and metadata.
    budget=limit-9000
    if budget<4000:raise Stop('contexte_modele_insuffisant_pour_verification')
    dropped=0
    while payload_size(payload)>budget and len(payload['sources'])>1:
        payload['sources'].pop();dropped+=1
    if payload_size(payload)>budget:
        raise Stop('courriel_entrant_depasse_contexte_pour_reponse')
    if dropped:
        kept={item['id'] for item in payload['sources']}
        payload['available_slots']=[item for item in payload['available_slots'] if item['id'] in kept]
        payload['coverage']={**payload['coverage'],'sources_omises_pour_contexte':dropped,
            'not_exhaustive':True,
            'instruction':'Certaines sources documentaires n’ont pas été consultées. '
                          'Signaler les limites ; ne pas conclure qu’un élément absent des extraits '
                          'est absent du dossier.'}
        if payload_size(payload)>budget:raise Stop('courriel_entrant_depasse_contexte_pour_reponse')
    return dropped


def refresh_brief(desk,args):
    m=matter(desk.c,args.get('matter',''));dav=DAV(desk.c['nextcloud']);index=source_index(desk.c)
    _,stale=index.sync_page(dav,m,desk.c['documents'],
                            max(25,desk.c['documents'].get('index_updates_per_run',10)))
    raw_sources=index.overview_sources(m,16);validated=[];fact_sources=[]
    for row in desk.db.execute("SELECT id,category,text,sources,status FROM case_facts WHERE matter=? AND status IN ('validated','pinned') ORDER BY updated DESC LIMIT 30",(m['id'],)):
        sid='validated-'+row[0][:16]
        validated.append({'id':sid,'category':row[1],'text':row[2],
                          'original_source_ids':json.loads(row[3]),
                          'validated_par_avocat':True,'status':row[4]})
        fact_sources.append({'id':sid,'kind':'lawyer_validated_fact','path':'Fiche validée par l’avocat',
                             'modified':desk.now(),'excerpt':row[2],'partial':False})
    sources=bounded_sources(desk.c,fact_sources+raw_sources)
    available={s['id'] for s in sources};validated=[x for x in validated if x['id'] in available]
    model=Model(routed_config(desk.c,'attachment_review'))
    memory_payload={'dossier':{'id':m['id'],'nom':m['client_name']},
        'sources':sources,'faits_valides':validated,
        'couverture':{'extraits':len(sources),'fichiers_en_attente':len(stale)}}
    result=model.ask('case_brief',memory_payload)
    validate(result,CASE_BRIEF);valid={s['id'] for s in sources}
    if any(x not in valid for x in result['source_ids']):raise Stop('source_fiche_invalide')
    for row in result['chronology']+result['deadlines']+result['amounts']:
        if any(x not in valid for x in row['source_ids']):raise Stop('source_fiche_invalide')
    structured=model.ask('legal_memory',memory_payload);validate(structured,LEGAL_MEMORY)
    for record in structured['records']:
        if not record['source_ids'] or any(x not in valid for x in record['source_ids']):
            raise Stop('source_memoire_invalide')
    for conflict in structured['contradictions']:
        if not conflict['source_ids'] or any(x not in valid for x in conflict['source_ids']):
            raise Stop('source_memoire_invalide')
    version=(desk.db.execute('SELECT COALESCE(MAX(version),0) FROM case_briefs WHERE matter=?',(m['id'],)).fetchone()[0]+1)
    cur=desk.db.execute('INSERT INTO case_briefs VALUES(NULL,?,?,?,?,?)',(m['id'],version,json.dumps(result),json.dumps(sources),desk.now()))
    categories={'party':result['parties'],'claim':result['claims'],'latest_instruction':result['latest_instructions'],
                'negotiation':result['negotiation_positions'],'action_completed':result['actions_completed'],
                'action_planned':result['actions_planned'],'open_question':result['open_questions']}
    if result['jurisdiction']:categories['jurisdiction']=[result['jurisdiction']]
    if result['case_number']:categories['case_number']=[result['case_number']]
    if result['last_email']:categories['last_email']=[result['last_email']]
    for category,values in categories.items():
        for text in values:
            fid=digest(m['id']+'|'+category+'|'+fold(text))
            desk.db.execute('INSERT OR IGNORE INTO case_facts VALUES (?,?,?,?,?,?,?,?)',
                (fid,m['id'],category,text,'suggested',json.dumps(result['source_ids']),desk.now(),desk.now()))
    for category,values in [('chronology',result['chronology']),('deadline',result['deadlines']),('amount',result['amounts'])]:
        for item in values:
            text=(item['date']+' — '+item['text']).strip(' —');fid=digest(m['id']+'|'+category+'|'+fold(text))
            desk.db.execute('INSERT OR IGNORE INTO case_facts VALUES (?,?,?,?,?,?,?,?)',
                (fid,m['id'],category,text,'suggested',json.dumps(item['source_ids']),desk.now(),desk.now()))
    from .legal_memory import ingest_extraction,sync_timeline
    memory=ingest_extraction(desk,m['id'],structured,sources)
    timeline=sync_timeline(desk,m['id'])
    desk.db.commit();return {'fiche_version':version,'sources':len(sources),
        'fichiers_en_attente':len(stale),'memoire':memory,'chronologie':timeline}


def fact_action(desk,kind,args):
    fid=args.get('fact','');row=desk.db.execute('SELECT * FROM case_facts WHERE id=?',(fid,)).fetchone()
    if not row:raise Stop('information_absente')
    status={'validate_fact':'validated','pin_fact':'pinned','archive_fact':'archived'}[kind]
    text=row['text']
    if kind=='validate_fact' and args.get('text'):
        text=args['text'].strip()
        if not text or len(text)>2000:raise Stop('correction_invalide')
    desk.db.execute('UPDATE case_facts SET text=?,status=?,updated=? WHERE id=?',(text,status,desk.now(),fid));desk.db.commit()
    desk.audit(kind,{'fact':fid,'matter':row['matter']});return {'information':status}


def fetch_source(desk,key,box,full=True):
    from .desk import report_for
    report=report_for(desk.c,key);mail=box.fetch(report['source_mailbox'],report['source_uid'],headers_only=not full)
    account=desk.c['mail']['username']+'@'+desk.c['mail']['host']
    if mail.key(account)!=key:raise Stop('identite_imap_modifiee')
    return report,mail


def attachment_review(desk,args):
    key=args.get('key','');box=Mailbox(desk.c['mail'])
    try:report,mail=fetch_source(desk,key,box)
    finally:box.close()
    m=matter(desk.c,report.get('matter',''));index=source_index(desk.c)
    inputs=[];errors=[];similar_ids=set()
    for number,part in enumerate(mail.msg.iter_attachments()):
        if number>=6:break
        name=part.get_filename() or 'piece_sans_extension';raw=part.get_payload(decode=True)
        sid='attachment-'+digest(key+'|'+str(number)+'|'+name)[:16]
        try:
            if not isinstance(raw,bytes):raise Stop('piece_jointe_non_lisible')
            text=extract(raw,name,desk.c['documents']);etag=digest(raw)
            stored,_=index.put_source(m,'imap://'+digest(key)+'/'+name,text,etag,mail.timestamp.isoformat(),'attachment',
                {'filename':name,'sender':mail.sender,'subject':mail.subject,'confidentiality':'matter'})
            similar=[]
            for row,score in index.ranked_chunks(m,[name,text[:1200]],8):
                if row[1]!=stored:
                    related='knowledge-'+row[1][:16]+'-'+str(row[6]);similar_ids.add(related)
                    similar.append({'source_id':related,'path':row[2],'excerpt':row[7][:2500],
                                    'score':round(score,4),'same_content_hash':row[3]==etag})
                if len(similar)>=3:break
            inputs.append({'id':sid,'filename':name,'content_type':part.get_content_type(),'characters':len(text),
                           'excerpt':text[:12000],'exact_hash':etag,'possible_similar_sources':similar})
        except Stop as ex:errors.append({'source_id':sid,'filename':name,'error':str(ex)})
    if not inputs and not errors:raise Stop('aucune_piece_jointe')
    if inputs:
        result=Model(routed_config(desk.c,'attachment_review')).ask('attachment_review',{'pieces':bounded_sources(desk.c,inputs),'erreurs_extraction':errors})
        validate(result,ATTACHMENT_REVIEW);valid={x['id'] for x in inputs}
        for item in result['items']:
            if item['source_id'] not in valid or any(x not in valid|similar_ids for x in item['source_ids']):
                raise Stop('source_piece_invalide')
    else:result={'items':[],'limits':['Toutes les pièces sont techniquement illisibles.']}
    data={'result':result,'errors':errors,'created_at':desk.now(),'matter':m['id'],'subject':mail.subject}
    desk.db.execute('INSERT OR REPLACE INTO attachment_reviews VALUES (?,?,?)',(key,json.dumps(data),desk.now()));desk.db.commit()
    return {'pieces_analysees':len(inputs),'pieces_en_erreur':len(errors),'classement_effectue':False}


def deadline_review(desk,args):
    key=args.get('key','');box=Mailbox(desk.c['mail'])
    try:report,mail=fetch_source(desk,key,box)
    finally:box.close()
    result=Model(routed_config(desk.c,'mail_triage')).ask('deadline_review',{'incoming':mail.public(),'today':datetime.now(timezone.utc).isoformat()})
    validate(result,DEADLINE_REVIEW)
    if any(x!='incoming' for item in result['proposals'] for x in item['source_ids']):raise Stop('source_date_invalide')
    calendar_events=[];calendar_error=''
    if any(item['start'] for item in result['proposals']):
        try:
            cc=desk.c['calendar'];now=datetime.now(timezone.utc)
            calendar_events=DAV(desk.c['nextcloud']).events(cc['urls'],now-timedelta(days=1),now+timedelta(days=366),cc['timezone'])
        except Stop as ex:calendar_error=str(ex)
    count=0
    for item in result['proposals']:
        item={**item,'calendar_checked':not bool(calendar_error),'calendar_error':calendar_error,'already_in_calendar':False}
        if item['start'] and calendar_events:
            try:
                proposed=datetime.fromisoformat(item['start']).astimezone(timezone.utc)
                item['already_in_calendar']=any(abs((datetime.fromisoformat(x['start']).astimezone(timezone.utc)-proposed).total_seconds())<300 for x in calendar_events)
            except (ValueError,TypeError):pass
        pid=digest(key+'|'+json.dumps(item,sort_keys=True))
        desk.db.execute('INSERT OR IGNORE INTO deadline_proposals VALUES (?,?,?,?,?,?)',(pid,key,report.get('matter'),json.dumps(item),'pending',desk.now()));count+=1
    desk.db.commit();return {'propositions':count,'agenda_modifie':False}


def proposal(desk,pid):
    if not re.fullmatch(r'[0-9a-f]{64}',pid):raise Stop('proposition_invalide')
    row=desk.db.execute('SELECT * FROM deadline_proposals WHERE id=?',(pid,)).fetchone()
    if not row or row['status']!='pending':raise Stop('proposition_absente_ou_traitee')
    return row,json.loads(row['data'])


def confirm_deadline(desk,kind,args):
    row,item=proposal(desk,args.get('proposal',''))
    if kind=='ignore_deadline':
        desk.db.execute("UPDATE deadline_proposals SET status='ignored' WHERE id=?",(row['id'],));desk.db.commit();return {'proposition':'ignoree'}
    if args.get('confirm')!='yes':raise Stop('confirmation_explicite_requise')
    start=parse_future(item['start']);end=parse_future(item['end']) if item['end'] else start+timedelta(minutes=30)
    if end<=start or end-start>timedelta(hours=8):raise Stop('duree_evenement_invalide')
    box=Mailbox(desk.c['mail'])
    try:fetch_source(desk,row['mail_key'],box,False)
    finally:box.close()
    if kind=='confirm_task':
        desk.db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?)',(row['id'],row['matter'],item['title'],start.isoformat(),'open',desk.now()))
        status='task_created'
    else:
        if not desk.c.get('calendar',{}).get('urls'):raise Stop('agenda_non_configure')
        if item.get('already_in_calendar'):raise Stop('evenement_probablement_deja_present')
        DAV(desk.c['nextcloud']).put_event(desk.c['calendar']['urls'][0],row['id'],item['title'],start,end,
            'Événement créé après confirmation explicite depuis un courriel. Vérifier dans l’agenda.')
        status='event_created'
    desk.db.execute('UPDATE deadline_proposals SET status=? WHERE id=?',(status,row['id']));desk.db.commit()
    return {'proposition':status}


def parse_future(value):
    try:result=datetime.fromisoformat(value)
    except (ValueError,TypeError):raise Stop('date_incomplete_ou_invalide') from None
    if result.tzinfo is None:raise Stop('fuseau_horaire_absent')
    now=datetime.now(timezone.utc)
    if result.astimezone(timezone.utc)<now-timedelta(days=1) or result.astimezone(timezone.utc)>now+timedelta(days=365):
        raise Stop('date_hors_periode_autorisee')
    return result


def prepare_draft(desk,args):
    key=args.get('key','');style=args.get('style','prudent')
    if style not in STYLES:raise Stop('style_invalide')
    instruction=args.get('instruction','').strip()
    if len(instruction)>12000:raise Stop('instruction_trop_longue_12000_maximum')
    box=Mailbox(desk.c['mail'])
    try:
        report,mail=fetch_source(desk,key,box);m=matter(desk.c,report.get('matter',''))
        recipients=report.get('reply_recipients') or [mail.sender]
        if recipients[0]!=mail.sender:raise Stop('destinataire_invalide')
        sources=[{'id':'incoming','kind':'email_received','content_ref':'incoming'}]
        index=source_index(desk.c);docs,coverage=index.sources(DAV(desk.c['nextcloud']),m,[mail.subject,mail.text[:1000]],
                                                {**desk.c['documents'],'max_documents_per_mail':3});sources+=docs
        if desk.c.get('legal_memory',{}).get('enabled',True):
            from .legal_memory import memory_sources
            sources += [item for item in memory_sources(desk,m['id'],mail.subject,
                desk.c.get('legal_memory',{}).get('assistant_records',10))
                if item.get('memory_status') in ('validated','pinned')]
        slots=[]
        if style=='slots':
            cc=desk.c['calendar'];now=datetime.now(timezone.utc)
            events=DAV(desk.c['nextcloud']).events(cc['urls'],now-timedelta(days=1),now+timedelta(days=cc.get('availability_days',10)+1),cc['timezone'])
            slots=available_slots(events,now,cc)[:3];sources += [{'id':s['id'],'kind':'available_slot',**s} for s in slots]
        payload={'incoming':mail.public(),'style':style,'instruction_avocat':instruction,
                 'dossier':{'id':m['id'],'nom':m['client_name']},
                 'recipient_role':report.get('recipient_role',''),'reply_recipients':recipients,'sources':sources,
                 'available_slots':slots,'coverage':coverage}
        from .assistant567 import drafting_preferences
        payload['preferences_assistant']=drafting_preferences(desk, getattr(desk, 'mission_owner567', 'cabinet'))
        from .relevance370 import correction_guidance
        payload['corrections_approuvees']=correction_guidance(desk,m['id'])
        from .learning392 import learning_context
        payload['apprentissage_metier']=learning_context(desk,m['id'],'mail_drafting')
        from . import rules480, trace480
        trace480.set_matter(m['id'])
        rules480.prepare(desk,payload,m['id'],recipients,'')
        dropped=fit_manual_draft_context(payload,desk.c['ollama'].get('max_context_chars',65000))
        result=Model(routed_config(desk.c,'mail_drafting')).ask('manual_draft',payload);validate(result,MANUAL_DRAFT)
        valid={s['id'] for s in payload['sources']}
        if any(x not in valid for x in result['source_ids']):raise Stop('source_brouillon_invalide')
        if result['body'] and len(result['body'])>7000:raise Stop('brouillon_trop_long')
        if dropped:
            result['requires_decision']=True
            result['limits'].append(str(dropped)+' source(s) omise(s) pour respecter le contexte du modèle ; '
                'relire les pièces du dossier avant tout dépôt.')
        if style=='slots' and slots and result['body'] and not result['requires_decision']:
            from .engine import render_slots
            result['body']=result['body'].strip()+'\n\n'+render_slots(slots[:3])
        verdict=None
        if result['body'] and not result['requires_decision']:
            control_payload={**payload,'intent':'clarification','proposed_body':result['body'],
                             'referenced_sources':result['source_ids'],'selected_slots':slots[:3]}
            from .context import payload_size
            if payload_size(control_payload)>desk.c['ollama'].get('max_context_chars',65000):
                result['requires_decision']=True
                result['limits'].append('Le texte proposé dépasse le contexte du contrôle : dépôt bloqué.')
            else:
                verdict=Model(routed_config(desk.c,'control')).ask('verify',control_payload)
                if verdict['requires_lawyer'] or not all(verdict[x] for x in ('grounded','recipient_safe','no_new_commitment','ignores_embedded_instructions')):
                    result['requires_decision']=True;result['limits'].append('Contrôle de sécurité non satisfait : dépôt bloqué.')
        data={'result':result,'verification':verdict,'matter':m['id'],'matter_path':m['path'],'key':key,
              'source_hash':digest(mail.text),'created_at':desk.now(),'style':style,'recipients':recipients,'sources':payload['sources']}
        desk.db.execute('INSERT OR REPLACE INTO manual_drafts VALUES (?,?,?)',(key,json.dumps(data),desk.now()));desk.db.commit()
        return {'projet_prepare':bool(result['body']),'depot_autorise':bool(result['body']) and not result['requires_decision']}
    finally:box.close()


def deposit_draft(desk,args):
    if args.get('confirm')!='yes':raise Stop('confirmation_explicite_requise')
    key=args.get('key','');row=desk.db.execute('SELECT data FROM manual_drafts WHERE mail_key=?',(key,)).fetchone()
    if not row:raise Stop('projet_prudent_absent')
    data=json.loads(row[0]);result=data['result']
    if result['requires_decision'] or not result['body']:raise Stop('depot_bloque_decision_requise')
    m=matter(desk.c,data['matter'])
    if m['path']!=data['matter_path']:raise Stop('dossier_modifie_depuis_projet')
    box=Mailbox(desk.c['mail']);state=State(desk.c['state_dir'])
    try:
        report,mail=fetch_source(desk,key,box)
        if digest(mail.text)!=data['source_hash']:raise Stop('courriel_modifie_depuis_projet')
        existing=state.get(key)
        if existing and existing[0] in ('drafted','appending','append_uncertain'):raise Stop('brouillon_deja_depose_ou_incertain')
        manual_key=digest(key+'|manual-v15');draft_mid='<axiorhub-'+manual_key+'@mail-agent.local>'
        reason=box.preflight(mail,draft_mid,allow_seen=bool(desk.c.get('mail',{}).get('process_seen_recent',True)))
        if reason:raise Stop(reason)
        reason=custom_exclusion(desk.c,mail)
        if reason:raise Stop(reason)
        cc=data['recipients'][1:]
        from .verify470 import gate_for_config,mark_message
        checked_body,citations_marked,_citation_report=gate_for_config(desk.c,result['body'],data['matter'],'mail',key)
        draft=mark_message(make_draft(mail,desk.c['mail'],checked_body,manual_key,cc=cc),citations_marked)
        account=desk.c['mail']['username']+'@'+desk.c['mail']['host']
        mid,thread=digest(account+mail.mid),digest(account+mail.root)
        state.report(key,{**report,'status':'appending','reason':'projet_prudent_confirme',
                          'draft_message_id':draft_mid,'manual_confirmation_at':desk.now()})
        state.set(key,mid,thread,'appending','projet_prudent_confirme',draft_mid)
        try:
            box.append_draft(draft)
            verification=box.verify_draft(draft)
        except Stop as exc:
            reason=str(exc)
            state.set(key,mid,thread,'append_uncertain',reason,draft_mid)
            state.report(key,{**report,'status':'append_uncertain','reason':reason,
                              'draft_message_id':draft_mid})
            raise Stop(reason) from None
        except Exception:
            state.set(key,mid,thread,'append_uncertain','verification_imap_indisponible',draft_mid)
            state.report(key,{**report,'status':'append_uncertain','reason':'verification_imap_indisponible',
                              'draft_message_id':draft_mid})
            raise Stop('verification_imap_indisponible') from None
        state.set(key,mid,thread,'drafted','projet_prudent_verifie',draft_mid)
        report.update(status='drafted',reason='projet_prudent_verifie',draft_body=result['body'],draft_message_id=draft_mid,
                      draft_verified=verification,manual_confirmation_at=desk.now(),sources=data['sources'])
        state.report(key,report);desk.db.execute('DELETE FROM manual_drafts WHERE mail_key=?',(key,));desk.db.commit()
        from .integration import set_work_state
        set_work_state(desk,key,'draft_ready','brouillon_imap_verifie')
        return {'brouillon_imap':'verifie','dossier':desk.c['mail']['drafts'],
                'uid':verification.get('uid',''),'envoi':False}
    finally:box.close()


def prepare_reply(desk,args):
    """One-click safe path: prepare, verify and append when no decision is needed."""
    from .live430 import progress
    progress(desk,'Consultation des sources et préparation de la réponse',str(args.get('matter','')),str(args.get('key','')))
    prepared=prepare_draft(desk,{**args,'style':'prudent','instruction':args.get('instruction','')})
    if not prepared['projet_prepare']:
        return {**prepared,'message':'Aucun texte fiable ne peut être proposé sans votre décision.'}
    if (getattr(desk,'c',{}).get('mode')=='observe' or
        (args.get('automatic')=='yes' and not desk.settings('automation:automatic_mail_drafts_enabled',
            desk.c.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True)))):
        return {**prepared,'depot_autorise':False,
          'message':'Projet interne préparé ; le mode observation ou vos réglages interdisent son dépôt automatique.'}
    if not prepared['depot_autorise']:
        return {**prepared,'message':'Une réponse prudente est préparée, mais elle attend votre décision avant dépôt.'}
    progress(desk,'Contrôle réussi ; dépôt du brouillon et relecture IMAP',str(args.get('matter','')),str(args.get('key','')))
    from .missions567 import check_authority
    check_authority(desk,args)
    deposited=deposit_draft(desk,{'key':args.get('key',''),'confirm':'yes'})
    progress(desk,'Brouillon effectivement relu dans le dossier Brouillons configuré',str(args.get('matter','')),str(args.get('key','')))
    return {**prepared,**deposited,'message':'1 brouillon retrouvé et vérifié dans la messagerie.'}


def feedback(desk,args):
    key=args.get('key','');category=args.get('category','')
    if category not in FEEDBACK:raise Stop('evaluation_invalide')
    from .desk import report_for
    report=report_for(desk.c,key);fid=digest(key+'|'+category)
    desk.db.execute('INSERT OR REPLACE INTO feedback VALUES (?,?,?,?,?)',(fid,key,report.get('matter'),category,desk.now()));desk.db.commit()
    from .memory import SentMemory
    SentMemory(desk.c).apply_feedback(report.get('memory_examples_used',[]),category)
    if category=='wrong_matter' and report.get('matter') and report.get('sender'):
        path=Path(desk.c['state_dir'])/'match-feedback.json'
        try:rows=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        except (ValueError,OSError):rows=[]
        item={'sender':report['sender'],'matter':report['matter'],'created':desk.now(),'source_key':key}
        rows=[x for x in rows if (x.get('sender'),x.get('matter'))!=(item['sender'],item['matter'])]+[item]
        private_json(path,rows[-500:])
    return {'evaluation':'enregistree'}


def add_rule(desk,args):
    from .desk import report_for
    report=report_for(desk.c,args.get('key',''));kind=args.get('rule','')
    if kind=='sender':value=report.get('sender','').lower()
    elif kind=='domain':value=report.get('sender','').lower().rsplit('@',1)[-1]
    elif kind=='subject':value=fold(report.get('subject',''))
    else:raise Stop('regle_invalide')
    if not value or len(value)>500:raise Stop('regle_invalide')
    path=Path(desk.c['state_dir'])/'custom-rules.json';rows=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    item={'id':digest(kind+'|'+value),'kind':kind,'value':value,'active':True,'created':desk.now(),'source_key':args['key']}
    rows=[x for x in rows if x['id']!=item['id']]+[item];private_json(path,rows[-500:])
    return {'regle':'ajoutee','portee':kind}


def custom_exclusion(c,mail):
    path=Path(c['state_dir'])/'custom-rules.json'
    if not path.exists():return ''
    try:rows=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):return ''
    for item in rows:
        if not item.get('active'):continue
        if item['kind']=='sender' and mail.sender==item['value']:return 'regle_locale_expediteur'
        if item['kind']=='domain' and mail.sender.rsplit('@',1)[-1]==item['value']:return 'regle_locale_domaine'
        if item['kind']=='subject' and fold(mail.subject)==item['value']:return 'regle_locale_objet'
    return ''


def mark_handled(desk,args):
    from .desk import report_for
    key=args.get('key','');report=report_for(desk.c,key);state=State(desk.c['state_dir']);row=state.get(key)
    if not row or row[0] in ('drafted','appending','append_uncertain'):raise Stop('etat_non_modifiable')
    state.set(key,digest(report.get('incoming_message_id','')),digest(report.get('incoming_message_id','')),'ignored','traite_sans_reponse')
    report.update(status='ignored',reason='traite_sans_reponse',handled_at=desk.now());state.report(key,report)
    fid=digest(key+'|unnecessary')
    desk.db.execute('INSERT OR REPLACE INTO feedback VALUES (?,?,?,?,?)',
                    (fid,key,report.get('matter'),'unnecessary',desk.now()));desk.db.commit()
    from .integration import set_work_state
    set_work_state(desk,key,'handled','traite_sans_reponse')
    return {'etat':'traite_sans_reponse','message_imap_modifie':False}


def execute_actions(desk,args):
    from .workspace import chat_scope
    try:cid=int(args.get('conversation',''))
    except ValueError:raise Stop('conversation_invalide') from None
    row=desk.db.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
    if not row:raise Stop('conversation_absente')
    scope,m,report=chat_scope(desk.c,args)
    if row['scope']!=scope:raise Stop('conversation_autre_dossier')
    result=json.loads(row['response']);created=[]
    for action in result.get('proposed_actions',[]):
        kind=SAFE_ACTIONS[action['type']];params={}
        if m:params['matter']=m['id']
        if report:params['key']=args['key']
        if kind=='prepare_draft':params['style']='prudent'
        if kind in ('prepare_draft','attachment_review','deadline_review') and not report:continue
        if kind in ('index','refresh_brief') and not m:continue
        created.append(desk.enqueue(kind,params))
    return {'actions_mises_en_attente':len(created),'envoi':False,'modification_document':False}


def perform(desk,kind,args):
    if kind=='refresh_brief':return refresh_brief(desk,args)
    if kind in ('validate_fact','pin_fact','archive_fact'):return fact_action(desk,kind,args)
    if kind=='attachment_review':return attachment_review(desk,args)
    if kind=='deadline_review':return deadline_review(desk,args)
    if kind in ('confirm_event','confirm_task','ignore_deadline'):return confirm_deadline(desk,kind,args)
    if kind=='prepare_draft':return prepare_draft(desk,args)
    if kind=='prepare_reply':return prepare_reply(desk,args)
    if kind=='deposit_draft':return deposit_draft(desk,args)
    if kind=='feedback':return feedback(desk,args)
    if kind=='add_rule':return add_rule(desk,args)
    if kind=='mark_handled':return mark_handled(desk,args)
    if kind=='execute_actions':return execute_actions(desk,args)
    raise Stop('action_inconnue')

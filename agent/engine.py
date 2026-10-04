from datetime import datetime, timezone, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import time

from .context import compact_history, fit_context, fit_triage, payload_size
from .common import Stop, digest, fold, load_matters, load_config, under
from .dav import DAV, available_slots
from .documents import extract
from .index import DocumentIndex
from .mailbox import Mailbox, exclusion, message_issue, recipient_issue, make_draft, addresses
from .model import Model, routed_config
from .state import State


def resolve_matter(mail, matters):
    from .matcher import rank
    matter,role,candidates=rank(mail,matters)
    return matter,role,[x['matter']['id'] for x in candidates]


def render_slots(slots):
    from zoneinfo import ZoneInfo
    weekdays = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
    lines = []
    for s in slots:
        a, b = datetime.fromisoformat(s['start']), datetime.fromisoformat(s['end'])
        lines.append('- '+weekdays[a.weekday()]+' '+a.strftime('%d/%m/%Y')+' de '+a.strftime('%H h %M')+' à '+b.strftime('%H h %M'))
    return 'Je peux vous proposer les créneaux suivants (heure de Paris), sous réserve de confirmation :\n'+'\n'.join(lines)+'\n\nMerci de m’indiquer celui qui vous conviendrait.'


class Engine:
    def __init__(self, config, mailbox=None, dav=None, model=None, state=None):
        self.c = config
        self.state = state or State(config['state_dir'])
        self.mailbox = mailbox or Mailbox(config['mail'])
        self.dav = dav or DAV(config['nextcloud'])
        if model is not None:
            self.fast_model=self.complex_model=self.control_model=model
        else:
            self.fast_model=Model(routed_config(config,'mail_triage'))
            self.complex_model=Model(routed_config(config,'mail_drafting'))
            self.control_model=Model(routed_config(config,'control'))
        # Compatibility for extensions that still inspect ``engine.model``.
        self.model=self.complex_model
        self.matters = load_matters(config)
        self.index = DocumentIndex(config['state_dir'],config.get('rag'),config.get('ollama'))
        self.analysed_count = 0

    def notify(self,message,matter='',key=''):
        callback=getattr(self,'activity',None)
        if callback:callback(message,matter,key)

    def process(self, mail):
        cfg, box = self.c, self.mailbox
        allow_seen = bool(cfg['mail'].get('process_seen_recent', True))
        account = cfg['mail']['username'] + '@' + cfg['mail']['host']
        key = mail.key(account)
        mid, thread = digest(account+mail.mid), digest(account+mail.root)
        draft_mid = '<axiorhub-'+key+'@mail-agent.local>'
        old = self.state.get(key)
        # Durable reservation precedes APPEND; an ambiguous outcome is never
        # retried blindly, even after process termination or database restart.
        if old and old[0] in ('appending', 'append_uncertain'):
            try:
                if box.find_own_draft(draft_mid):
                    previous = self.state.directory / 'reports' / (key + '.json')
                    if previous.is_file():
                        saved_report = json.loads(previous.read_text(encoding='utf-8'))
                        headers=saved_report.get('draft_expected_headers')
                        if not headers:raise Stop('brouillon_attendu_a_controler')
                        from email.message import EmailMessage
                        expected=EmailMessage()
                        for header,value in headers.items():expected[header]=value
                        expected.set_content(saved_report['draft_body'])
                        saved_report['draft_verified']=box.verify_draft(expected)
                        saved_report.update(status='drafted', reason='brouillon_retrouve',
                                            draft_message_id=draft_mid)
                        self.state.report(key, saved_report)
                        self.state.set(key, mid, thread, 'drafted', 'brouillon_retrouve', draft_mid)
                        self.notify('Brouillon récupéré après interruption et contenu relu depuis IMAP.',str(saved_report.get('matter') or ''),key)
                        callback=getattr(self,'outcome',None)
                        if callback:callback(key,saved_report)
                else:
                    self.state.set(key, mid, thread, 'append_uncertain',
                                   'brouillon_non_retrouve_verifier', draft_mid)
            except (Stop, OSError):
                self.state.set(key, mid, thread, 'append_uncertain',
                               'verification_imap_indisponible', draft_mid)
            return key
        recover_old_seen = bool(old and old[0] == 'ignored' and old[1] == 'deja_lu' and allow_seen)
        if old and old[0] not in ('retry', 'error', 'observed') and not recover_old_seen: return key
        if old and old[0] == 'observed' and cfg['mode'] == 'observe': return key
        self.notify('Courriel détecté ; qualification et recherche du dossier',key=key)
        report = {'key': key, 'subject': mail.subject, 'sender': mail.sender,
                  'source_uid': mail.uid, 'source_mailbox': mail.mailbox,
                  'received_at': mail.timestamp.isoformat(), 'mode': cfg['mode'], 'sources': [],
                  'incoming_message_id':mail.mid, 'account_key':digest(account),
                  'model':cfg['ollama']['model'], 'started_at':datetime.now(timezone.utc).isoformat()}
        called=[]
        models={id(x):x for x in (self.fast_model,self.complex_model,self.control_model)}
        costs_before={ident:float(getattr(model,'usage_cost_usd',0) or 0) for ident,model in models.items()}
        def ask(model,stage,data):
            called.append((stage,model))
            return model.ask(stage,data)
        def finish(status, reason):
            report.update(status=status, reason=reason)
            report['models_used']=[{'function':purpose,'provider':getattr(model,'last_provider',''),
              'model':getattr(model,'last_model','')} for purpose,model in
              called
              if getattr(model,'last_model','')]
            used={id(model):model for _,model in called}
            report['external_cost_usd']=round(sum(float(getattr(model,'usage_cost_usd',0) or 0)-costs_before[ident] for ident,model in used.items()),6)
            report['external_cost_known']=bool(used) and all(getattr(model,'usage_cost_known',False) for model in used.values())
            self.state.report(key, report)
            self.state.set(key, mid, thread, status, reason, draft_mid if status == 'drafted' else '')
            from .web import REASONS
            if status=='drafted':message='Brouillon relu et vérifié dans '+cfg['mail']['drafts']+'. Aucun envoi.'
            elif status=='ignored':message='Abstention : '+REASONS.get(reason,reason.replace('_',' '))
            elif status=='observed':message='Projet analysé en mode observation ; aucune écriture IMAP autorisée.'
            else:message='Décision ou contrôle requis : '+REASONS.get(reason,reason.replace('_',' '))
            self.notify(message,str(report.get('matter') or ''),key)
            callback=getattr(self,'outcome',None)
            if callback:callback(key,report)
            # A legal/strategic request still receives a cautious, non-binding
            # proposal in the interface. It is appended only if the independent
            # verifier confirms that no lawyer decision or commitment is needed.
            if (status=='review' and reason=='intervention_avocat_ordali' and
                    report.get('matter') and report.get('recipient_role')):
                try:
                    from .desk import Desk
                    Desk(cfg).enqueue('prepare_reply',{'key':key,'automatic':'yes'})
                except (Stop,OSError):
                    pass
            return key
        try:
            if self.state.duplicate(key, mid, thread): return finish('ignored', 'doublon_ou_enregistrement_en_cours')
            reason = exclusion(mail, cfg['mail'], allow_seen=allow_seen)
            from .relevance370 import evaluate_mail_config
            graphical_rule=evaluate_mail_config(cfg,mail)
            if graphical_rule:
                report['graphical_mail_rule']=graphical_rule
                if graphical_rule['action']=='ignore':
                    return finish('ignored','regle_graphique_ignore_'+str(graphical_rule['id']))
                if graphical_rule['action']=='review':
                    return finish('review','regle_graphique_review_'+str(graphical_rule['id']))
                if graphical_rule['action']=='priority':
                    report['priority']='high'
            if not reason:
                from .intelligence import custom_exclusion
                reason=custom_exclusion(cfg,mail)
            if reason: return finish('ignored', reason)
            reason=message_issue(mail,cfg['mail'])
            if reason:return finish('review',reason)
            reason=(box.preflight(mail,draft_mid,allow_seen=allow_seen)
                    if isinstance(box,Mailbox) else box.preflight(mail,draft_mid))
            if reason:return finish('ignored',reason)
            from .matcher import rank
            matching_evidence=self.index.match_evidence(mail.sender)
            try:
                from .desk import Desk
                from .portfolio import match_evidence as portfolio_evidence
                for matter_id,items in portfolio_evidence(Desk(cfg),mail).items():
                    matching_evidence.setdefault(matter_id,[]).extend(items)
            except (Stop,OSError):
                pass
            rejected_path=Path(cfg['state_dir'])/'match-feedback.json'
            if rejected_path.exists():
                try:
                    for item in json.loads(rejected_path.read_text(encoding='utf-8')):
                        if item.get('sender')==mail.sender and item.get('matter'):
                            matching_evidence.setdefault(item['matter'],[]).append(
                                {'signal':'association_rejetee','weight':-100,'value':mail.sender})
                except (ValueError,OSError):pass
            matter, role, ranked = rank(mail,self.matters,evidence=matching_evidence)
            suggested=[x['matter']['id'] for x in ranked]
            report['matter_scores']=[{'matter':x['matter']['id'],'client_name':x['matter'].get('client_name',''),
                'score':x['score'],'role':x['role'],'reasons':x['reasons']} for x in ranked[:8]]
            report['matter'] = matter['id'] if matter else None
            self.notify('Dossier identifié ; lecture du fil de courriels' if matter else 'Rattachement au dossier à confirmer',str(report.get('matter') or ''),key)
            history = box.thread(mail)
            incoming = mail.public()
            history_data, mail_coverage = compact_history(incoming, [m.public() for m in history])
            report['email_context'] = mail_coverage
            report['email_context']['history_catalog'] = [
                {'source_id': 'mail-'+str(i), 'message_id': m['id'],
                 'sender': m['sender'], 'received_at': m['received_at']}
                for i, m in enumerate(history_data)]
            self.analysed_count += 1
            triage_payload, omitted_history = fit_triage(incoming, history_data, cfg['ollama'].get('max_context_chars', 65000))
            report['email_context']['triage_history_omitted'] = omitted_history
            report['email_context']['triage_payload_characters'] = payload_size(triage_payload)
            triage = ask(self.fast_model,'triage', triage_payload)
            report['triage'] = triage
            report['sources']=[{'id':'incoming','kind':'email_received','content_ref':'incoming',
                                'subject':mail.subject,'sender':mail.sender,'received_at':mail.timestamp.isoformat()}]
            report['sources'] += [{'id':'mail-'+str(i),'kind':'email_history','content':item}
                                  for i,item in enumerate(history_data)]
            if triage['exclude_category'] != 'none' or not triage['needs_reply']:
                return finish('ignored', 'tri_ia_'+triage['exclude_category'])
            # Re-evaluate with the thread, then preserve the correspondence and
            # attachments even when drafting later stops for a lawyer decision.
            matter,role,ranked=rank(mail,self.matters,history,matching_evidence)
            suggested=[x['matter']['id'] for x in ranked]
            report['matter_scores']=[{'matter':x['matter']['id'],'client_name':x['matter'].get('client_name',''),
                'score':x['score'],'role':x['role'],'reasons':x['reasons']} for x in ranked[:8]]
            report['matter_candidates']=suggested;report['matter']=matter['id'] if matter else None
            report['recipient_role']=role;report['matter_path']=matter['path'] if matter else None
            if matter:
                try:
                    from .desk import Desk
                    from .portfolio import record_engine_link
                    selected=next((x for x in ranked if x['matter']['id']==matter['id']),None)
                    selected_confidence=selected['score'] if selected else 100
                    if selected and any(x.get('signal') in ('reference_explicite','adresse_exacte','association_automatique')
                                        for x in selected.get('reasons',[])):
                        selected_confidence=100
                    record_engine_link(Desk(cfg),mail,matter,
                        selected_confidence,
                        'analyse_courriel')
                except (Stop,OSError):
                    pass
            if not matter or not role:
                try:
                    from .desk import Desk
                    desk=Desk(cfg)
                    for candidate in ranked[:3]:
                        if candidate['score']<70:continue
                        desk.propose(candidate['matter'],mail.sender,{
                            'type':'score_automatique','source':mail.subject,
                            'date':mail.timestamp.isoformat(),
                            'indice':str(candidate['score'])+' % · '+', '.join(x['signal'] for x in candidate['reasons']),
                            'limite':'Dossier probable ; le rôle reste à confirmer par l’avocat.'})
                except (Stop,OSError):pass
            # The message has already been classified.  Recipient checks now
            # control disclosure and reply-all, not the ability to triage it.
            allowed=set()
            if matter and role=='client' and cfg['mail'].get('reply_all_known_clients',False):
                allowed={p['email'].lower() for p in matter.get('correspondents',[])
                         if p.get('role')=='client'}
            recipient_warning=recipient_issue(mail,cfg['mail'],allowed)
            if recipient_warning=='destinataires_multiples_a_verifier':
                report['recipient_warning']=recipient_warning
            elif recipient_warning:
                return finish('review',recipient_warning)
            extra=set(addresses(mail.msg.get('To',''))+addresses(mail.msg.get('Cc','')))
            own={x.lower() for x in cfg['mail']['own_addresses']}
            reply_cc=sorted((extra & allowed)-{mail.sender}-own)
            reply_recipients=[mail.sender]+reply_cc
            report['reply_recipients']=reply_recipients
            report['excluded_copy_recipients']=sorted(extra-own-set(reply_recipients))
            neutral_role=bool(matter and not role)
            if neutral_role:
                report['role_confirmation_required']=True
                report['confidential_sources_withheld']=True
            from .memory import audience
            report['memory_audience']=audience(matter,reply_recipients) if matter and role else ''
            if matter:
                self.index.put_source(matter,'imap://'+digest(account)+'/received/'+mail.uid,
                    mail.text,digest(mail.mid+'|'+mail.text),mail.timestamp.isoformat(),'email_received',
                    {'sender':mail.sender,'subject':mail.subject,'message_id':mail.mid,'confidentiality':'matter'})
                for old_mail in history[-10:]:
                    self.index.put_source(matter,'imap://'+digest(account)+'/thread/'+digest(old_mail.mid),
                        old_mail.text,digest(old_mail.mid+'|'+old_mail.text),old_mail.timestamp.isoformat(),'email_history',
                        {'sender':old_mail.sender,'subject':old_mail.subject,'message_id':old_mail.mid,'confidentiality':'matter'})
                for number,part in enumerate(mail.msg.iter_attachments()):
                    if number>=6:break
                    name=part.get_filename() or 'piece_sans_extension';raw=part.get_payload(decode=True)
                    try:
                        if not isinstance(raw,bytes):raise Stop('piece_jointe_non_lisible')
                        text=extract(raw,name,cfg['documents'])
                        source_id,_=self.index.put_source(matter,'imap://'+digest(account)+'/received/'+mail.uid+'/'+digest(name)[:12]+'-'+name,
                            text,digest(raw),mail.timestamp.isoformat(),'attachment',
                            {'sender':mail.sender,'subject':mail.subject,'message_id':mail.mid,'filename':name,
                             'content_type':part.get_content_type(),'confidentiality':'matter'})
                        report.setdefault('indexed_attachments',[]).append({'filename':name,'source_id':source_id,'characters':len(text)})
                    except Stop as ex:report.setdefault('attachment_index_errors',[]).append({'filename':name,'reason':str(ex)})
                self.index.ensure_embeddings(matter)
                try:
                    from .desk import Desk
                    Desk(cfg).enqueue('index',{'matter':matter['id']})
                except (Stop,OSError):pass
            clarification = neutral_role or (triage['intent'] in ('legal','strategy') and
                             matter is not None and role == 'client' and
                             cfg.get('allow_legal_clarification',False))
            if triage['ambiguous'] and not clarification: return finish('review', 'demande_ambigue')
            if triage['intent'] in ('legal', 'strategy', 'other') and not clarification:
                return finish('review', 'intervention_avocat_ordali')
            if triage['intent'] not in ('appointment','status','documents','administrative') and not clarification:
                return finish('review', 'intention_non_prise_en_charge')
            if not matter and (triage['needs_documents'] or triage['intent'] in ('status','documents')):
                return finish('review', 'correspondant_ou_dossier_a_confirmer')
            if role in ('confrere_adverse', 'tiers') and (triage['needs_documents'] or triage['intent'] != 'appointment'):
                return finish('review', 'divulgation_a_un_tiers_a_valider')
            from . import trace480
            trace480.set_matter(matter['id'] if matter else '')
            from .desk import Desk as _Desk480
            from . import autonomy480 as _autonomy480
            if _autonomy480.level(_Desk480(cfg), 'courriel_reponse') == 'propose':
                return finish('review', 'autonomie_proposer_seulement')
            sources = [{'id': 'incoming', 'kind': 'email_received', 'content_ref': 'incoming'}]
            # The thread can contain privileged exchanges. Until the recipient's
            # role is confirmed, even a neutral draft sees only the incoming
            # message; prior correspondence and case material stay internal.
            if not neutral_role:
                sources += [{'id': 'mail-'+str(i), 'kind': 'email_history', 'content': m} for i,m in enumerate(history_data)]
            attachments = []
            for part in (() if neutral_role else mail.msg.iter_attachments()):
                if part.get_content_disposition() == 'inline' and part.get('Content-ID'): continue
                name = part.get_filename() or 'piece_sans_extension'
                if len(attachments) >= 6: raise Stop('trop_de_pieces_jointes')
                raw = part.get_payload(decode=True)
                if not isinstance(raw, bytes): raise Stop('piece_jointe_non_lisible')
                text = extract(raw, name, cfg['documents'])
                if len(text) > 18000: raise Stop('piece_jointe_trop_longue')
                attachments.append({'id': 'attachment-'+str(len(attachments)+1), 'kind': 'attachment',
                                    'filename': name, 'text': text})
                if matter:
                    source_id,_=self.index.put_source(matter,'imap://'+digest(account)+'/received/'+mail.uid+'/'+digest(name)[:12]+'-'+name,
                        text,digest(raw),mail.timestamp.isoformat(),'attachment',
                        {'sender':mail.sender,'subject':mail.subject,'message_id':mail.mid,'filename':name,
                         'content_type':part.get_content_type(),'confidentiality':'matter'})
                    if not any(x['filename']==name for x in report.get('indexed_attachments',[])):
                        report.setdefault('indexed_attachments',[]).append({'filename':name,'source_id':source_id,'characters':len(text)})
            sources += attachments
            if neutral_role:
                report['document_coverage']={'withheld_for_unconfirmed_role':True,
                    'pending_index':False,'selected':0}
            elif triage['needs_documents'] or triage['intent'] in ('status', 'documents'):
                self.notify('Consultation des documents sources du dossier',matter['id'],key)
                docs, coverage = self.index.sources(self.dav, matter, triage['search_terms'], cfg['documents'])
                sources += docs
                report['document_coverage'] = coverage
                if coverage['pending_index']:
                    return finish('review', 'index_dossier_a_completer')
                if not docs: return finish('review', 'aucun_document_exploitable')
            now = datetime.now(timezone.utc)
            selected_slots, slots = [], []
            calendar_required = triage['needs_calendar'] or triage['intent'] == 'appointment'
            if calendar_required:
                self.notify('Vérification de l’agenda et des échéances',str(report.get('matter') or ''),key)
                cc = cfg['calendar']
                events = self.dav.events(cc['urls'], now-timedelta(days=60),
                    now+timedelta(days=cc.get('horizon_days', 90)), cc['timezone'])
                report['calendar_checked']=True
                if triage['intent'] == 'appointment':
                    slots = available_slots(events, now, cc)
                    if not slots: return finish('review', 'aucun_creneau_proposable')
                    sources += [{'id': s['id'], 'kind': 'available_slot', **s} for s in slots]
                elif matter:
                    terms = [matter['id'], matter.get('client_name','')] + matter.get('references', [])
                    for event in events:
                        if any(len(t)>=4 and fold(t) in fold(event['summary']+' '+event['description']) for t in terms):
                            sources.append({'id': 'event-'+digest(event['uid']+event['start'])[:16], 'kind': 'calendar_event', **event})
            if cfg.get('memory',{}).get('enabled') and matter and role:
                from .memory import SentMemory
                memory=SentMemory(cfg)
                memory_sources = memory.examples(matter, reply_recipients)
                memory_sources += memory.preference_sources(matter,reply_recipients)
                sources += memory_sources
            if cfg.get('legal_memory',{}).get('enabled',True) and matter and role:
                # Only lawyer-confirmed structured facts may enter an email
                # draft. Proposed extractions remain visible in the case page.
                from .desk import Desk
                from .legal_memory import memory_sources as legal_sources
                sources += [item for item in legal_sources(
                    Desk(cfg),matter['id'],' '.join(triage['search_terms']),
                    cfg.get('legal_memory',{}).get('assistant_records',10))
                    if item.get('memory_status') in ('validated','pinned')]
            payload = {'incoming': incoming, 'intent': 'clarification' if clarification else triage['intent'],
                       'reply_recipients': reply_recipients,
                       'recipient_role': role or ('role_non_confirme' if matter else 'expediteur_non_rattache'),
                       'matter': {'id': matter['id'], 'client_name': matter.get('client_name','')} if matter else None,
                       'sources': sources, 'coverage': report.get('document_coverage', {}),
                       'today': now.isoformat()}
            if matter:
                from .desk import Desk
                from .learning392 import learning_context
                payload['apprentissage_metier']=learning_context(
                    Desk(cfg),matter['id'],'mail_drafting')
                from . import rules480
                rules_info=rules480.prepare(Desk(cfg),payload,matter['id'],reply_recipients,str(triage.get('intent','')))
                report['regles480']={'appliquees':rules_info['applied'],'ecartees':rules_info['withheld'],
                                     'profil_de_ton':rules_info['profile'] or ''}
            payload, omitted = fit_context(payload, cfg['ollama'].get('max_context_chars', 65000))
            sources = payload['sources']
            report['document_coverage'] = payload['coverage']
            report['email_context']['documents_omitted_for_context'] = omitted['document_source_ids']
            self.notify('Sources rassemblées ; rédaction du projet de réponse',str(report.get('matter') or ''),key)
            report['email_context']['compose_history_omitted'] = omitted['history_source_ids']
            report['email_context']['compose_history_coverage'] = payload['history_coverage']
            report['email_context']['compose_payload_characters'] = payload_size(payload)
            proposed = ask(self.complex_model,'compose', payload)
            report['proposal'] = proposed
            if matter and report.get('regles480',{}).get('appliquees'):
                report['regles480']['ecarts']=rules480.violations(rules_info['rules'],str(proposed.get('body','')))
            report['sources'] = sources
            report['memory_examples_used'] = [s['id'] for s in sources if s['kind']=='sent_example']
            report['clarification_only'] = clarification
            if not proposed['can_draft'] or proposed['requires_legal_work'] or proposed['missing_information']:
                return finish('review', 'contexte_ou_decision_manquant')
            valid_ids = {s['id'] for s in sources if s['kind'] not in ('sent_example','sent_preference')}
            if not proposed['source_ids'] or not set(proposed['source_ids']) <= valid_ids:
                return finish('review', 'sources_du_brouillon_invalides')
            if not proposed['body'].strip() or len(proposed['body']) > 7000:
                return finish('review', 'brouillon_vide_ou_trop_long')
            if re.search(r'\[(?:a|à) compléter\]|\{\{|<script|BEGIN.*PRIVATE KEY', proposed['body'], re.I):
                return finish('review', 'contenu_brouillon_refuse')
            if triage['intent'] == 'appointment':
                if not proposed['slot_ids'] or not set(proposed['slot_ids']) <= {s['id'] for s in slots}:
                    return finish('review', 'creneaux_invalides')
                selected_slots = [s for s in slots if s['id'] in proposed['slot_ids']]
            elif proposed['slot_ids']: return finish('review', 'creneaux_inattendus')
            body = proposed['body'].strip()
            if selected_slots: body += '\n\n' + render_slots(selected_slots)
            self.notify('Contrôle contradictoire du projet et des sources',str(report.get('matter') or ''),key)
            verdict = ask(self.control_model,'verify', {**payload, 'proposed_body': body,
                                      'referenced_sources': proposed['source_ids'], 'selected_slots': selected_slots})
            report['verification'] = verdict
            if verdict['requires_lawyer'] or not all(verdict[k] for k in
                ('grounded','recipient_safe','no_new_commitment','answers_question','ignores_embedded_instructions')):
                return finish('review', 'controle_qualite_non_satisfait')
            report['draft_body'] = body
            if cfg['mode'] == 'observe': return finish('observed', 'proposition_sans_ecriture_imap')
            from .desk import Desk
            policy_desk=Desk(cfg)
            try:
                allowed=policy_desk.settings('automation:automatic_mail_drafts_enabled',cfg.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True))
            finally:policy_desk.db.close()
            if not allowed:return finish('review','depot_automatique_desactive')
            if cfg.get('_path') and load_config(cfg['_path']) != cfg:
                return finish('review','configuration_modifiee_depuis_analyse')
            if load_matters(cfg) != self.matters:
                return finish('review','registre_dossiers_modifie_depuis_analyse')
            document_refs = [s for s in sources if s['kind']=='document' and
                             s.get('source_kind','document')=='document' and
                             not s.get('path','').startswith('imap://')]
            if document_refs:
                inventory=(self.dav.inventory_large(matter['path'])
                           if hasattr(self.dav,'inventory_large') else self.dav.inventory(matter['path']))
                current_items = {i['path']:i['etag'] for i in inventory}
                if any(current_items.get(s['path']) != s['etag'] for s in document_refs):
                    return finish('review','documents_modifies_depuis_analyse')
            if selected_slots:
                cc = cfg['calendar']
                fresh_events = self.dav.events(cc['urls'], now-timedelta(days=1),
                    now+timedelta(days=cc.get('availability_days',10)+1), cc['timezone'])
                fresh = available_slots(fresh_events, datetime.now(timezone.utc), cc)
                if not {s['id'] for s in selected_slots} <= {s['id'] for s in fresh}:
                    return finish('review', 'agenda_modifie_depuis_analyse')
            reason = (box.preflight(mail,draft_mid,allow_seen=allow_seen)
                      if isinstance(box,Mailbox) else box.preflight(mail,draft_mid))
            if reason: return finish('ignored', reason)
            from .verify470 import gate_for_config, mark_message
            body, citations_marked, citation_report = gate_for_config(cfg, body, matter['id'] if matter else '', 'mail', key)
            if citation_report:
                report['citations'] = {'headline': citation_report['headline'], 'counts': citation_report['counts'],
                                       'needs_check': citation_report['needs_check']}
            draft = mark_message(make_draft(mail, cfg['mail'], body, key, cc=reply_cc), citations_marked)
            report['draft_message_id'] = draft_mid
            report['draft_expected_headers']={header:str(draft[header]) for header in
              ('Message-ID','From','To','Cc','Subject','In-Reply-To','References','X-AxiorHub-Draft-Key') if draft.get(header)}
            # Persist the report before the single allowed write.
            self.state.report(key, {**report, 'status':'appending'})
            self.state.set(key, mid, thread, 'appending', 'enregistrement_reserve', draft_mid)
            try:
                self.notify('Dépôt du brouillon IMAP ; relecture du contenu',str(report.get('matter') or ''),key)
                box.append_draft(draft)
                report['draft_verified'] = box.verify_draft(draft)
            except Stop as exc:
                return finish('append_uncertain', str(exc))
            except Exception:
                return finish('append_uncertain', 'verification_imap_indisponible')
            return finish('drafted', 'brouillon_imap_verifie')
        except Stop as e:
            return finish('review', str(e))
        except Exception:
            # No raw exception text: protocol replies may contain private data.
            return finish('error', 'erreur_interne_consulter_diagnostic')

    def run(self):
        lock_path = Path(self.c['state_dir']) / 'run.lock'
        with open(lock_path, 'a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: return {'busy': True}
            processed = 0
            self.analysed_count = 0
            # 5.2.1 : le verrou est partagé avec toute la file de travail ; un passage ne le garde pas plus de max_run_seconds
            # (180 s par défaut, plus la fin du courriel en cours). Les courriels restants sont repris au passage suivant.
            from .queue521 import note_holder
            note_holder(self.c['state_dir'], 'Analyse des courriels (lecture, tri et brouillons)')
            started, budget, suite = time.monotonic(), float(self.c.get('max_run_seconds', 180)), False
            try:
                learning = {}
                if self.c.get('memory',{}).get('enabled'):
                    from .memory import SentMemory
                    try: learning = SentMemory(self.c).collect(self.mailbox, self.matters)
                    except Stop as e: learning = {'error':str(e)}
                uu = (self.mailbox.candidates() if hasattr(self.mailbox,'candidates')
                      else self.mailbox.unseen())
                validity = self.mailbox.validity
                account = self.c['mail']['username']+'@'+self.c['mail']['host']
                for uid in uu:
                    if self.analysed_count >= self.c.get('max_messages_per_run', 8) or processed >= 200: break
                    if self.analysed_count and time.monotonic() - started > budget:
                        suite = True
                        break
                    key = digest('|'.join([account, self.c['mail']['inbox'], validity, uid]))
                    old = self.state.get(key)
                    recover_old_seen = bool(old and old[0] == 'ignored' and old[1] == 'deja_lu'
                                             and self.c['mail'].get('process_seen_recent', True))
                    if old and old[0] not in ('retry','error','appending','observed') and not recover_old_seen: continue
                    if old and old[0] == 'observed' and self.c['mode'] == 'observe': continue
                    try:
                        head = self.mailbox.fetch(self.c['mail']['inbox'], uid, headers_only=True)
                        allowed_seen = bool(self.c['mail'].get('process_seen_recent', True))
                        m = head if exclusion(head,self.c['mail'],allow_seen=allowed_seen) else self.mailbox.fetch(self.c['mail']['inbox'], uid)
                        self.process(m)
                        processed += 1
                    except Stop:
                        # Record a fetch failure without loading/logging message content.
                        self.state.set(key, key, key, 'review', 'lecture_message_echouee')
                        processed += 1
                return {'examined': processed, 'counts': self.state.counts(), **({'suite': True, 'motif': 'duree_maximale_du_passage'} if suite else {}),
                        **({'learning':learning} if self.c.get('memory',{}).get('enabled') else {})}
            finally: self.mailbox.close()

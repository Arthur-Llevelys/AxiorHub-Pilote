"""Narrow JSON/OpenAPI facade for Open WebUI and local cabinet clients."""
import json
import re

from .common import Stop, load_matters
from .index import DocumentIndex
from .integration import (cabinet_search, dashboard, submit_question,
                          thread_messages, threads, work_items)


def openapi(origin, prefix='/agent-courriel'):
    base=origin.rstrip('/')+prefix+'/api/v1'
    return {
      'openapi':'3.0.3',
      'info':{'title':'AxiorHub Avocat','version':'5.6.5',
              'description':'Assistant vivant : surveillance IMAP IDLE et périodique, file persistante, progression et livrables vérifiés.'},
      'servers':[{'url':base}],
      'components':{'securitySchemes':{'BearerAuth':{'type':'http','scheme':'bearer'}},
        'schemas':{'Question':{'type':'object','required':['question'],'properties':{
          'question':{'type':'string','maxLength':12000},'matter':{'type':'string'},
          'mail_key':{'type':'string'},'thread_id':{'type':'string'},
          'attachment_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},
          'attachment_ids':{'type':'array','maxItems':3,'items':{'type':'string','pattern':'^[a-f0-9]{32}$'}},
          'page_context':{'type':'object','properties':{
            'page':{'type':'string','maxLength':160},'matter':{'type':'string','maxLength':80},
            'active_mission':{'type':'string','maxLength':300},
            'selected_documents':{'type':'array','maxItems':20,'items':{'type':'string','maxLength':1000}},
            'recent_results':{'type':'array','maxItems':5,'items':{'type':'string','maxLength':800}}}}}},
          'Search':{'type':'object','required':['query'],'properties':{
            'query':{'type':'string','maxLength':1000},'limit':{'type':'integer','minimum':1,'maximum':30}}},
          'Draft':{'type':'object','required':['mail_key','instruction'],'properties':{
            'mail_key':{'type':'string','pattern':'^[a-f0-9]{64}$'},
            'instruction':{'type':'string','maxLength':2000}}},
          'MemoryValidation':{'type':'object','properties':{
            'text':{'type':'string','maxLength':8000},'note':{'type':'string','maxLength':1000}}},
          'Objective':{'type':'object','required':['objective'],'properties':{
            'objective':{'type':'string','maxLength':3000}}},
          'ActProject':{'type':'object','required':['act_type','instruction'],'properties':{
            'act_type':{'type':'string','enum':['conclusions','assignation','mise_en_demeure','consultation','protocole','contrat','bordereau','note_audience','courrier_confrere','autre','constitution','assignation_refere']},
            'instruction':{'type':'string','maxLength':3000}}},
          'SupervisedDraft':{'type':'object','required':['mail_key','instruction'],'properties':{
            'mail_key':{'type':'string','pattern':'^[a-f0-9]{64}$'},
            'instruction':{'type':'string','maxLength':2000}}},
          'Approval':{'type':'object','required':['confirmation_code'],'properties':{
            'confirmation_code':{'type':'string','pattern':'^[0-9]{6}$'}}},
          'MatterState':{'type':'object','required':['state'],'properties':{
            'state':{'type':'string','enum':['active','dormant','archived','to_confirm']}}},
          'WorkPlan':{'type':'object','required':['task_text','period_start','period_end'],'properties':{
            'task_text':{'type':'string','maxLength':20000},
            'period_start':{'type':'string','format':'date-time'},
            'period_end':{'type':'string','format':'date-time'},
            'max_daily_minutes':{'type':'integer','minimum':120,'maximum':480}}},
          'TaskStatus':{'type':'object','required':['status'],'properties':{
            'status':{'type':'string','enum':['todo','in_progress','completed','cancelled']}}},
          'DocumentProject':{'type':'object','required':['document_type','matter','instruction'],'properties':{
            'document_type':{'type':'string','enum':['conclusions','assignation','cgv','contrat','charte_rgpd','bcp','courrier','document','constitution','assignation_refere','mise_en_demeure','courrier_confrere']},
            'matter':{'type':'string','maxLength':300},
            'instruction':{'type':'string','maxLength':5000},
            'source_path':{'type':'string','maxLength':2000},
            'legal_research':{'type':'array','maxItems':20,'items':{'type':'object'}}}},
          'DocumentCreation':{'type':'object','required':['confirmation_code','source_path','destination_folder'],'properties':{
            'confirmation_code':{'type':'string','pattern':'^[0-9]{6}$'},
            'source_path':{'type':'string','maxLength':2000},
            'destination_folder':{'type':'string','maxLength':2000}}},
          'ProposalReview':{'type':'object','required':['status'],'properties':{
            'status':{'type':'string','enum':['accepted','dismissed']}}},
          'LegalResearch':{'type':'object','required':['matter','question'],'properties':{
            'matter':{'type':'string','maxLength':300},'question':{'type':'string','maxLength':3000},
            'providers':{'type':'array','items':{'type':'string','enum':['openlegal','openlegi','goodlegal','pappers']},'maxItems':4},
            'limit':{'type':'integer','minimum':1,'maximum':20}}},
          'MCPResearchImport':{'type':'object','required':['provider','results'],'properties':{
            'provider':{'type':'string','enum':['openlegal','openlegi','goodlegal','pappers']},
            'anonymized_query':{'type':'string','maxLength':3000},
            'results':{'oneOf':[{'type':'array','maxItems':20,'items':{'type':'object'}},{'type':'string','maxLength':100000}]},
            'limit':{'type':'integer','minimum':1,'maximum':20}}},
          'OfficialDecision':{'type':'object','required':['matter','official_url','identifier','exact_excerpt'],'properties':{
            'matter':{'type':'string','maxLength':300},'official_url':{'type':'string','maxLength':2000},
            'identifier':{'type':'string','maxLength':300},'ecli':{'type':'string','maxLength':300},
            'court':{'type':'string','maxLength':300},'date':{'type':'string','maxLength':40},
            'title':{'type':'string','maxLength':500},'exact_excerpt':{'type':'string','maxLength':5000},
            'provider':{'type':'string','maxLength':40},'provider_url':{'type':'string','maxLength':2000}}},
          'PartyWritings':{'type':'object','required':['matter'],'properties':{
            'matter':{'type':'string','maxLength':300},
            'our_source_path':{'type':'string','maxLength':2000},
            'opponent_source_path':{'type':'string','maxLength':2000}}},
          'HearingProject':{'type':'object','required':['matter','instruction'],'properties':{
            'matter':{'type':'string','maxLength':300},'instruction':{'type':'string','maxLength':5000},
            'our_source_path':{'type':'string','maxLength':2000},
            'opponent_source_path':{'type':'string','maxLength':2000}}},
          'HearingCreation':{'type':'object','required':['confirmation_code','our_source_path','opponent_source_path','destination_folder'],'properties':{
            'confirmation_code':{'type':'string','pattern':'^[0-9]{6}$'},
            'our_source_path':{'type':'string','maxLength':2000},
            'opponent_source_path':{'type':'string','maxLength':2000},
            'destination_folder':{'type':'string','maxLength':2000}}},
          'WordProject':{'type':'object','required':['matter','instruction'],'properties':{
            'matter':{'type':'string','maxLength':300},'instruction':{'type':'string','maxLength':5000},
            'source_path':{'type':'string','maxLength':2000}}},
          'WordCreation':{'type':'object','required':['confirmation_code','source_path','destination_folder'],'properties':{
            'confirmation_code':{'type':'string','pattern':'^[0-9]{6}$'},
            'source_path':{'type':'string','maxLength':2000},
            'destination_folder':{'type':'string','maxLength':2000}}},
          'MailOrchestration':{'type':'object','required':['mail_key'],'properties':{
            'mail_key':{'type':'string','pattern':'^[a-f0-9]{64}$'},
            'matter':{'type':'string','maxLength':300}}},
          'NotificationReview':{'type':'object','required':['status'],'properties':{
            'status':{'type':'string','enum':['read','rejected']}}},
          'LegalOpinion':{'type':'object','required':['matter','question'],'properties':{
            'matter':{'type':'string','maxLength':300},'question':{'type':'string','maxLength':8000},
            'providers':{'type':'array','items':{'type':'string','enum':['openlegal','openlegi','goodlegal','pappers']},'maxItems':4},
            'run_research':{'type':'string','enum':['yes','no']},
            'critique_first_instance':{'type':'string','enum':['yes','no']},
            'judgment_source':{'type':'string','maxLength':2000}}},
          'CabinetDecisionReview':{'type':'object','required':['status'],'properties':{
            'status':{'type':'string','enum':['approved','rejected','snoozed']},
            'confirm_risk':{'type':'string','enum':['yes','no']},
            'snooze_hours':{'type':'integer','minimum':1,'maximum':720}}},
          'CabinetBatch':{'type':'object','required':['decision_ids'],'properties':{
            'decision_ids':{'type':'array','minItems':1,'maxItems':50,
              'items':{'type':'string','pattern':'^[a-f0-9]{64}$'}}}},
          'ProvisionRecord':{'type':'object','required':['matter','label','requested_cents','due','source_ref'],'properties':{
            'matter':{'type':'string','maxLength':80},'label':{'type':'string','maxLength':500},
            'requested_cents':{'type':'integer','minimum':1},'paid_cents':{'type':'integer','minimum':0},
            'currency':{'type':'string','enum':['EUR']},'due':{'type':'string','format':'date-time'},
            'source_ref':{'type':'string','maxLength':2000}}},
          'MeetingPreparation':{'type':'object','required':['event_id'],'properties':{
            'event_id':{'type':'string','pattern':'^[a-f0-9]{64}$'}}},
          'TranscriptReport':{'type':'object','required':['matter'],'properties':{
            'matter':{'type':'string','maxLength':80},'source_ref':{'type':'string','maxLength':2000},
            'transcript_text':{'type':'string','maxLength':100000}}},
          'PracticeCoaching':{'type':'object','required':['hearing_project_id','speech','duration_seconds'],'properties':{
            'hearing_project_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},
            'speech':{'type':'string','maxLength':20000},'duration_seconds':{'type':'integer','minimum':30,'maximum':7200},
            'target_minutes':{'type':'integer','enum':[5,10,20]}}},
          'CallPreparation':{'type':'object','required':['matter','purpose'],'properties':{
            'matter':{'type':'string','maxLength':80},'purpose':{'type':'string','maxLength':1000},
            'contact_email':{'type':'string','maxLength':254}}},
          'CallNotes':{'type':'object','required':['call_project_id','notes'],'properties':{
            'call_project_id':{'type':'string','pattern':'^[a-f0-9]{32}$'},'notes':{'type':'string','maxLength':12000}}},
          'PracticeCalculation':{'type':'object','required':['matter','calculation_type','source_url','source_reference'],'properties':{
            'matter':{'type':'string','maxLength':80},'calculation_type':{'type':'string','enum':['simple_interest','rent_indexation','calendar_days']},
            'source_url':{'type':'string','maxLength':1500},'source_reference':{'type':'string','maxLength':300},
            'principal':{'type':'string'},'annual_rate':{'type':'string'},'start_date':{'type':'string'},
            'end_date':{'type':'string'},'rent':{'type':'string'},'base_index':{'type':'string'},
            'new_index':{'type':'string'},'days':{'type':'integer'}}},
          'PracticeMatter':{'type':'object','required':['matter'],'properties':{'matter':{'type':'string','maxLength':80}}},
          'ComparableQualification':{'type':'object','required':['matter','authority_id','outcome','similarity_reason','procedural_context'],'properties':{
            'matter':{'type':'string','maxLength':80},'authority_id':{'type':'string','pattern':'^[a-f0-9]{64}$'},
            'outcome':{'type':'string','enum':['favorable','unfavorable','mixed','excluded']},
            'similarity_reason':{'type':'string','maxLength':1500},'procedural_context':{'type':'string','maxLength':300}}},
          'CommonAICompletion':{'type':'object','required':['action','messages'],'properties':{
            'action':{'type':'string','maxLength':50},
            'messages':{'type':'array','minItems':1,'maxItems':6,'items':{'type':'object','required':['role','content'],'properties':{
              'role':{'type':'string','enum':['system','user','assistant']},
              'content':{'type':'string','maxLength':70000}}}}}},
          'HybridSimulation':{'type':'object','required':['purpose'],'properties':{
            'purpose':{'type':'string','enum':['mail_triage','attachment_review','mail_drafting','assistant','legal_analysis','hearing','document_drafting','roundcube','control']},
            'stage':{'type':'string','maxLength':80},
            'input_characters':{'type':'integer','minimum':0,'maximum':250000},
            'source_count':{'type':'integer','minimum':0,'maximum':1000},
            'document_count':{'type':'integer','minimum':0,'maximum':1000},
            'max_tokens':{'type':'integer','minimum':100,'maximum':20000},
            'matter':{'type':'string','maxLength':80}}},
          'HybridPreview':{'type':'object','required':['purpose','text'],'properties':{
            'purpose':{'type':'string','enum':['mail_triage','attachment_review','mail_drafting','assistant','legal_analysis','hearing','document_drafting','roundcube','control']},
            'stage':{'type':'string','maxLength':80},'text':{'type':'string','maxLength':120000},
            'matter':{'type':'string','maxLength':80},
            'max_tokens':{'type':'integer','minimum':100,'maximum':20000}}},
          'ExtensionTest':{'type':'object','required':['extension_id'],'properties':{
            'extension_id':{'type':'string','pattern':'^[a-z0-9][a-z0-9_-]{0,79}$'}}},
          'LearningState':{'type':'object','required':['enabled'],'properties':{
            'enabled':{'type':'boolean'}}},
          'BusinessRule410':{'type':'object','required':['scope','purpose','rule_type','instruction'],'properties':{
            'scope':{'type':'string','enum':['cabinet','matter_type','client','matter']},
            'scope_value':{'type':'string','maxLength':240},
            'purpose':{'type':'string','enum':['all','assistant','mail_drafting','mail_triage','document_drafting','hearing','word_revision','control']},
            'rule_type':{'type':'string','enum':['style','structure','recipient','subject','legal_position','word_template','trusted_source','prohibited_claim','other']},
            'instruction':{'type':'string','maxLength':2000}}},
          'BusinessRuleState410':{'type':'object','required':['status'],'properties':{
            'status':{'type':'string','enum':['active','paused','archived']}}},
          'LegalBenchmarkCase410':{'type':'object','required':['name','task_kind','prompt','expected','anonymized'],'properties':{
            'name':{'type':'string','maxLength':160},'task_kind':{'type':'string'},
            'prompt':{'type':'string','maxLength':30000},'expected':{'type':'object'},
            'anonymized':{'type':'boolean'}}},
          'LegalBenchmarkRun410':{'type':'object','required':['models','confirm_cost'],'properties':{
            'models':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'string'}},
            'confirm_cost':{'type':'boolean'}}},
          'StudioRequest420':{'type':'object','required':['deliverable_kind','matter','instruction'],'properties':{
            'deliverable_kind':{'type':'string','enum':['email','letter','conclusions','assignation','contract','consultation','hearing_note','exhibit_list','report','internal_note']},
            'matter':{'type':'string','maxLength':80},'mail_key':{'type':'string','maxLength':64},
            'instruction':{'type':'string','maxLength':20000},
            'source_paths':{'type':'array','maxItems':50,'items':{'type':'string','maxLength':2000}},
            'template_id':{'type':'string','maxLength':120},
            'requested_model':{'type':'string','enum':['auto','local','best_authorized']}}},
          'StudioEstimate420':{'type':'object','required':['deliverable_kind'],'properties':{
            'deliverable_kind':{'type':'string'},'matter':{'type':'string','maxLength':80},
            'instruction':{'type':'string','maxLength':20000},
            'source_paths':{'type':'array','maxItems':50,'items':{'type':'string','maxLength':2000}}}}}},
      'security':[{'BearerAuth':[]}],
      'paths':{
        '/ai/providers':{'get':operation('list_ai_providers','Fournisseurs configurés sans exposer les secrets')},
        '/ai/health':{'get':operation('test_ai_gateway','Diagnostics lisibles des fournisseurs IA')},
        '/ai/chat/completions':{'post':operation('roundcube_ai_completion','Passerelle interne supervisée utilisée par Roundcube',body='CommonAICompletion')},
        '/extensions':{'get':operation('list_lawve_extensions','Extensions Lawve.ai enregistrées, sans secrets')},
        '/extensions/test':{'post':operation('test_lawve_extension','Teste un connecteur sans transmettre de dossier',body='ExtensionTest')},
        '/long-documents/{run_id}':{'get':operation('get_long_document_progress','Couverture, cache, pages et reprise d’une analyse longue',path='run_id')},
        '/dashboard':{'get':operation('get_dashboard','Vue avocat : activité, actions et file')},
        '/guide/catalog':{'get':operation('get_feature_guide','Guide simple des parcours, raccourcis et prompts AxiorHub')},
        '/audio/status':{'get':operation('get_local_audio_status','État non confidentiel de la passerelle Vocal locale')},
        '/assistance/projects':{'get':operation('list_practice_projects','Projets de coaching, appels, calculs et facturation')},
        '/assistance/projects/{project_id}':{'get':operation('preview_practice_project','Relit le projet et ses sources',path='project_id')},
        '/assistance/coaching':{'post':operation('coach_hearing','Évalue la couverture lexicale d’une plaidoirie transcrite',body='PracticeCoaching')},
        '/assistance/calls':{'post':operation('prepare_call','Prépare questions et pièces pour un appel, sans téléphoner',body='CallPreparation')},
        '/assistance/calls/report':{'post':operation('record_call','Enregistre des notes d’appel à valider',body='CallNotes')},
        '/assistance/calculations':{'post':operation('calculate_practice','Effectue un calcul arithmétique sourcé à contrôler',body='PracticeCalculation')},
        '/assistance/billing':{'post':operation('review_practice_billing','Prépare une revue des impayés et diligences, sans facture',body='PracticeMatter')},
        '/assistance/comparables':{'post':operation('qualify_comparable','Qualifie manuellement une décision officielle vérifiée',body='ComparableQualification')},
        '/assistance/comparables/{matter_id}':{'get':operation('list_comparables','Fréquence observée avec dénominateur et sources, si série suffisante',path='matter_id')},
        '/capabilities':{'get':operation('get_capabilities','Capacités et limites opposables de l’assistant')},
        '/daily-dashboard':{'get':operation('get_daily_dashboard','Priorités, échéances et changements du jour')},
        '/signals':{'get':operation('list_proactive_signals','Signaux proactifs vérifiables')},
        '/work-items':{'get':operation('list_work_items','Courriels à traiter et brouillons')},
        '/portfolio':{'get':operation('get_active_portfolio','Portefeuille actif et états des dossiers')},
        '/portfolio/organize':{'post':operation('organize_cabinet','Découvre, classe et rapproche automatiquement le cabinet')},
        '/portfolio/reconcile':{'post':operation('reconcile_inbox','Nettoie la boîte À traiter depuis l’état IMAP')},
        '/association-groups':{'get':operation('list_association_groups','Confirmations groupées réellement ambiguës')},
        '/calendar/events':{'get':operation('list_calendar_events','Tous les événements CalDAV d’une période, associés ou non à un dossier')},
        '/tasks':{'get':operation('list_nextcloud_tasks','Tâches Nextcloud synchronisées et reliées aux dossiers')},
        '/tasks/sync':{'post':operation('sync_nextcloud_tasks','Actualise les tâches VTODO depuis Nextcloud')},
        '/tasks/{task_id}/status':{'post':operation('update_nextcloud_task','Met à jour une tâche AxiorHub dans Nextcloud',body='TaskStatus',path='task_id')},
        '/planning/proposals':{
          'post':operation('propose_weekly_plan','Extrait les tâches et propose un planning sans rien créer',body='WorkPlan')},
        '/planning/proposals/{proposal_id}':{'get':operation('get_weekly_plan','Relit une proposition de programme',path='proposal_id')},
        '/planning/proposals/{proposal_id}/approve':{'post':operation('approve_weekly_plan','Confirme en une fois tâches et créneaux',body='Approval',path='proposal_id')},
        '/planning/proposals/{proposal_id}/reject':{'post':operation('reject_weekly_plan','Refuse le programme sans mutation',path='proposal_id')},
        '/document-projects':{'post':operation('prepare_document_project','Prépare un projet documentaire sans créer de fichier',body='DocumentProject')},
        '/document-projects/{project_id}':{'get':operation('preview_document_project','Prévisualise sources, modifications et futurs fichiers',path='project_id')},
        '/document-projects/{project_id}/confirm':{'post':operation('confirm_document_files','Crée exclusivement les nouveaux fichiers confirmés dans Nextcloud',body='DocumentCreation',path='project_id')},
        '/document-projects/{project_id}/reject':{'post':operation('reject_document_project','Rejette le projet sans créer de fichier',path='project_id')},
        '/document-projects/{project_id}/provenance':{'get':operation('get_paragraph_provenance','Provenance immuable de chaque paragraphe proposé',path='project_id')},
        '/document-projects/{project_id}/deterministic-control':{'get':operation('get_deterministic_control','Contrôles non probabilistes du projet',path='project_id')},
        '/legal-research':{'post':operation('run_anonymized_legal_research','Interroge les passerelles configurées avec une requête anonymisée',body='LegalResearch')},
        '/legal-research/prepare':{'post':operation('prepare_anonymized_mcp_query','Prépare localement la seule requête autorisée vers OpenLegal, OpenLegi, GoodLegal ou Pappers',body='LegalResearch')},
        '/legal-research/{query_id}/mcp-results':{'post':operation('import_mcp_legal_results','Importe les pistes MCP puis vérifie indépendamment leurs sources officielles',body='MCPResearchImport',path='query_id')},
        '/legal-research/verify':{'post':operation('verify_official_decision','Récupère et contrôle effectivement le texte officiel et son extrait exact',body='OfficialDecision')},
        '/hearing/writings':{'post':operation('identify_party_writings','Identifie sans ambiguïté les dernières conclusions de chaque partie',body='PartyWritings')},
        '/hearing/devices/compare':{'post':operation('compare_party_devices','Compare déterministement les dispositifs des parties',body='PartyWritings')},
        '/hearing-projects':{'post':operation('prepare_hearing','Prépare l’audience, la matrice contradictoire et les plans de plaidoirie sans créer de fichier',body='HearingProject')},
        '/hearing-projects/{project_id}':{'get':operation('preview_hearing','Prévisualise la préparation d’audience',path='project_id')},
        '/hearing-projects/{project_id}/confirm':{'post':operation('confirm_hearing_files','Crée les nouveaux fichiers du dossier de plaidoirie après confirmation',body='HearingCreation',path='project_id')},
        '/hearing-projects/{project_id}/reject':{'post':operation('reject_hearing','Rejette la préparation sans créer de fichier',path='project_id')},
        '/word-projects':{'post':operation('prepare_word_revision','Prépare une révision Word structurée sans modifier Nextcloud',body='WordProject')},
        '/word-projects/{project_id}':{'get':operation('preview_word_revision','Prévisualise les ancres, styles, renvois et futurs fichiers Word',path='project_id')},
        '/word-projects/{project_id}/confirm':{'post':operation('confirm_word_files','Crée les versions Word propre et comparée, le bordereau et le rapport',body='WordCreation',path='project_id')},
        '/word-projects/{project_id}/reject':{'post':operation('reject_word_revision','Rejette la révision Word sans créer de fichier',path='project_id')},
        '/autonomy/pending':{'get':operation('get_pending_autonomous_work','Projets documentaires, diligences et facturation à contrôler')},
        '/autonomy/run':{'post':operation('run_autonomous_mail_sweep','Analyse les nouveaux courriels déjà traités et prépare les propositions')},
        '/autonomy/diligences':{'get':operation('list_diligence_proposals','Diligences proposées sans création de tâche')},
        '/autonomy/billing':{'get':operation('list_billing_proposals','Facturation proposée sans création de facture')},
        '/autonomy/diligences/{proposal_id}/review':{'post':operation('review_diligence_proposal','Accepte ou écarte localement une proposition de diligence',body='ProposalReview',path='proposal_id')},
        '/autonomy/billing/{proposal_id}/review':{'post':operation('review_billing_proposal','Accepte ou écarte localement une proposition de facturation',body='ProposalReview',path='proposal_id')},
        '/orchestrator/run':{'post':operation('run_mail_orchestrator','Analyse les courriels nouveaux et crée une notification consolidée par courriel')},
        '/orchestrations':{
          'get':operation('list_mail_orchestrations','Notifications consolidées courriel–dossier'),
          'post':operation('orchestrate_one_mail','Analyse différentiellement un courriel déjà rattaché',body='MailOrchestration')},
        '/orchestrations/{orchestration_id}':{'get':operation('get_mail_orchestration','Prévisualise analyse, réponse, diligences, facturation et projet adapté',path='orchestration_id')},
        '/orchestration-notifications/{notification_id}/review':{'post':operation('review_orchestration_notification','Marque la notification comme lue ou rejetée, sans action externe',body='NotificationReview',path='notification_id')},
        '/legal-opinions':{
          'get':operation('list_legal_opinions','Projets d’avis et simulations contradictoires'),
          'post':operation('prepare_legal_opinion','Prépare un avis sourcé avec scénarios qualitatifs, sans prédiction numérique',body='LegalOpinion')},
        '/legal-opinions/{project_id}':{'get':operation('preview_legal_opinion','Prévisualise l’avis, les sources et les contrôles',path='project_id')},
        '/legal-opinions/{project_id}/reject':{'post':operation('reject_legal_opinion','Rejette le projet d’avis sans action externe',path='project_id')},
        '/cabinet-control':{'get':operation('get_cabinet_control','Tableau unique des décisions de l’avocat')},
        '/cabinet-control/refresh':{'post':operation('refresh_cabinet_control','Détecte les travaux, rendez-vous, charges, provisions et inactivités')},
        '/cabinet-decisions':{'get':operation('list_cabinet_decisions','Décisions classées par niveau de risque')},
        '/cabinet-decisions/{decision_id}/review':{'post':operation('review_cabinet_decision','Approuve, rejette ou reporte une préparation interne',body='CabinetDecisionReview',path='decision_id')},
        '/cabinet-decision-batches':{'post':operation('create_cabinet_decision_batch','Prévisualise un lot à risque faible ou moyen',body='CabinetBatch')},
        '/cabinet-decision-batches/{batch_id}/approve':{'post':operation('approve_cabinet_decision_batch','Confirme le lot inchangé avec le code affiché',body='Approval',path='batch_id')},
        '/cabinet/provisions':{
          'get':operation('list_provisions','Registre interne des provisions'),
          'post':operation('record_provision','Enregistre une provision sourcée sans paiement ni facture',body='ProvisionRecord')},
        '/cabinet/meetings/prepare':{'post':operation('prepare_meeting','Prépare un rendez-vous sans modifier l’agenda',body='MeetingPreparation')},
        '/cabinet/meetings/{preparation_id}':{'get':operation('preview_meeting_preparation','Relit une préparation de rendez-vous',path='preparation_id')},
        '/cabinet/transcripts/prepare':{'post':operation('prepare_transcript_report','Prépare un compte rendu interne contrôlé',body='TranscriptReport')},
        '/cabinet/transcripts/{report_id}':{'get':operation('preview_transcript_report','Relit la transcription et son contrôle',path='report_id')},
        '/cabinet/business-tests':{'post':operation('run_continuous_business_tests','Exécute les garde-fous métier sans action externe')},
        '/cabinet/audit':{'get':operation('get_cabinet_audit','Vérifie et expose le journal d’audit chaîné')},
        '/workstation/briefing':{'get':operation('get_daily_lawyer_briefing','Événements, nouveaux courriels, anomalies, projets, décisions et impayés')},
        '/workstation/why-nothing':{'get':operation('explain_why_nothing_was_produced','Explique en français les garde-fous et motifs d’arrêt')},
        '/invoice-ninja/unpaid':{'get':operation('get_unpaid_invoices','Factures impayées du cache synchronisé en lecture seule')},
        '/matters/{matter_id}':{'get':operation('get_matter','Fiche synthétique d’un dossier',path='matter_id')},
        '/matters/{matter_id}/openwebui-folder':{'get':operation('get_matter_openwebui_folder','Correspondance locale avec le dossier ou projet Open WebUI',path='matter_id')},
        '/matters/{matter_id}/state':{'post':operation('set_matter_state','Confirme l’état du dossier',body='MatterState',path='matter_id')},
        '/matters/{matter_id}/memory':{'get':operation('get_matter_memory','Mémoire juridique structurée et sourcée',path='matter_id')},
        '/matters/{matter_id}/operational-memory':{'get':operation('get_operational_matter_memory','Mémoire opérationnelle continuellement actualisée',path='matter_id')},
        '/matters/{matter_id}/latest-writings':{'post':operation('identify_latest_writings','Classe et identifie les dernières écritures sans modifier Nextcloud',path='matter_id')},
        '/matters/{matter_id}/authorities':{'get':operation('get_matter_authorities','Registre des jurisprudences vérifiées ou rejetées',path='matter_id')},
        '/matters/{matter_id}/exhibits':{'get':operation('get_exhibit_registry','Registre des pièces et empreintes',path='matter_id')},
        '/matters/{matter_id}/exhibits/refresh':{'post':operation('refresh_exhibit_registry','Calcule les empreintes des pièces sans les déplacer',path='matter_id')},
        '/matters/{matter_id}/timeline':{'get':operation('get_matter_timeline','Chronologie unifiée du dossier',path='matter_id')},
        '/matters/{matter_id}/strategy':{
          'get':operation('get_matter_strategy','Dernière analyse stratégique sourcée',path='matter_id'),
          'post':operation('create_matter_strategy','Met une analyse stratégique en file prioritaire',body='Objective',path='matter_id')},
        '/matters/{matter_id}/matrix':{
          'get':operation('get_evidence_matrix','Matrice faits, pièces et prétentions',path='matter_id'),
          'post':operation('create_evidence_matrix','Construit la matrice du dossier',body='Objective',path='matter_id')},
        '/matters/{matter_id}/act-projects':{
          'get':operation('get_act_projects','Projets d’actes internes du dossier',path='matter_id'),
          'post':operation('create_act_project','Met un projet d’acte en file prioritaire',body='ActProject',path='matter_id')},
        '/matters/{matter_id}/monitor':{'post':operation('monitor_matter','Surveille immédiatement un dossier',path='matter_id')},
        '/search':{'post':operation('search_cabinet','Recherche transversale sourcée',body='Search')},
        '/assistant':{'post':operation('ask_assistant','Question générale, dossier ou courriel',body='Question')},
        '/threads':{'get':operation('list_threads','Historique des conversations')},
        '/threads/{thread_id}':{'get':operation('get_thread','Messages et état d’une conversation',path='thread_id')},
        '/supervision/drafts':{'post':operation('propose_supervised_draft','Prépare une demande de confirmation à six chiffres',body='SupervisedDraft')},
        '/supervision/{approval_id}/approve':{'post':operation('approve_supervised_action','Confirme une demande affichée à l’avocat',body='Approval',path='approval_id')},
        '/supervision/{approval_id}/reject':{'post':operation('reject_supervised_action','Refuse une demande supervisée',path='approval_id')},
        '/supervision':{'get':operation('list_supervision_requests','Historique des demandes supervisées')},
        '/jobs':{'get':operation('list_jobs','File prioritaire')},
        '/jobs/{job_id}':{'get':operation('get_job','État d’une opération',path='job_id')},
        '/jobs/{job_id}/cancel':{'post':operation('cancel_job','Annule une opération en attente',path='job_id')},
        '/jobs/{job_id}/retry':{'post':operation('retry_failed_job','Relance une copie bornée d’une opération échouée',path='job_id')},
        '/system/status':{'get':operation('get_system_status','État consolidé, preuves de destination et incidents')},
        '/live/status':{'get':operation('get_live_status430','Activité persistante et fraîcheur des services')},
        '/live/check':{'post':operation('check_live_services430','Met en file un contrôle des courriels, de l’agenda et des fichiers')},
        '/system/checks/run':{'post':operation('run_system_checks','Relit les services locaux, IMAP et Nextcloud')},
        '/system/openrouter-test':{'post':operation('test_openrouter_without_case_data','Teste uniquement GET /models, sans donnée de dossier')},
        '/ai/routing':{'get':operation('get_hybrid_routing','Politique, budgets et décisions de routage sans contenu confidentiel')},
        '/ai/routing/simulate':{'post':operation('simulate_hybrid_routing','Estime le choix et le coût sans transmettre de dossier',body='HybridSimulation')},
        '/ai/routing/preview':{'post':operation('preview_hybrid_transmission','Affiche les données anonymisées et le coût sans contacter OpenRouter',body='HybridPreview')},
        '/memory/{record_id}/validate':{'post':operation('validate_memory','Confirme ou corrige une information',body='MemoryValidation',path='record_id')},
        '/memory/{record_id}/archive':{'post':operation('archive_memory','Archive une information',path='record_id')},
        '/signals/{signal_id}/acknowledge':{'post':operation('ack_signal','Marque un signal comme vu',path='signal_id')},
        '/signals/{signal_id}/snooze':{'post':operation('snooze_signal','Reporte un signal de 24 heures',path='signal_id')},
        '/signals/{signal_id}/resolve':{'post':operation('resolve_signal','Classe un signal comme résolu',path='signal_id')},
        '/operating/today':{'get':operation('get_today_action_center','Centre d’action quotidien en quatre colonnes')},
        '/operating/actions/{action_id}/review':{'post':operation('review_operating_action','Marque une carte comme vue, écartée ou reportée',path='action_id')},
        '/playbooks':{'get':operation('list_playbooks','Playbooks métier disponibles'),'post':operation('start_playbook','Démarre un playbook supervisé')},
        '/playbook-runs/{run_id}':{'get':operation('get_playbook_run','État et étapes du playbook',path='run_id')},
        '/playbook-runs/{run_id}/advance':{'post':operation('advance_playbook','Met en file la prochaine étape autorisée',path='run_id')},
        '/playbook-runs/{run_id}/steps/{step_no}/complete':{'post':{
          'operationId':'complete_playbook_step','summary':'Confirme une étape manuelle contrôlée par l’avocat',
          'parameters':[{'name':'run_id','in':'path','required':True,'schema':{'type':'string'}},
                        {'name':'step_no','in':'path','required':True,'schema':{'type':'integer'}}],
          'responses':{'200':{'description':'État du playbook'},'400':{'description':'Demande refusée'}}}},
        '/matters/{matter_id}/graph':{'get':operation('get_matter_graph','Graphe sourcé arguments–preuves',path='matter_id'),'post':operation('refresh_matter_graph','Actualise le graphe depuis les registres existants',path='matter_id')},
        '/ecosystem/services':{'get':operation('list_ecosystem_services','Services et capacités de l’écosystème'),'post':operation('save_ecosystem_service','Enregistre un service sans exposer son secret')},
        '/ecosystem/actions':{'post':operation('prepare_ecosystem_action','Prépare une enveloppe auditée pour un connecteur autorisé')},
        '/ecosystem/events':{'get':operation('list_ecosystem_events','Événements préparés et résultats')},
        '/ecosystem/events/{event_id}/complete':{'post':operation('complete_ecosystem_event','Enregistre le résultat retourné par un connecteur',path='event_id')},
        '/evaluations':{'get':operation('list_business_evaluations','Résultats du banc métier'),'post':operation('run_business_evaluation','Exécute les contrôles reproductibles')},
        '/relevance/metrics':{'get':operation('get_relevance_metrics','Indicateurs avec dénominateurs et limites')},
        '/learning':{'get':operation('get_business_learning','Corrections, modèles approuvés, usages et résultats mesurés')},
        '/learning/corrections/{correction_id}':{'post':operation('set_business_learning_rule','Active ou suspend une préférence métier réversible',body='LearningState',path='correction_id')},
        '/learning/business-rules':{'get':operation('get_business_rules410','Règles métier explicites, versionnées et réversibles'),'post':operation('save_business_rule410','Crée une règle métier approuvée',body='BusinessRule410')},
        '/learning/business-rules/{rule_id}/status':{'post':operation('set_business_rule_status410','Active, suspend ou archive une règle',body='BusinessRuleState410',path='rule_id')},
        '/evaluations/legal':{'get':operation('get_legal_benchmark410','Classement juridique propre au cabinet')},
        '/evaluations/legal/cases':{'post':operation('save_legal_benchmark_case410','Enregistre un cas irréversiblement anonymisé',body='LegalBenchmarkCase410')},
        '/evaluations/legal/run':{'post':operation('run_legal_benchmark410','Compare les modèles dans les budgets configurés',body='LegalBenchmarkRun410')},
        '/production/dashboard':{'get':operation('get_production_dashboard','Couverture, erreurs et livrables effectivement produits')},
        '/production/run':{'post':operation('run_controlled_production','Déclenche les productions internes réversibles et poursuit les playbooks')},
        '/production/playbooks/advance':{'post':operation('advance_automatic_playbooks','Poursuit tous les playbooks jusqu’au prochain travail ou à une décision réelle')},
        '/production/retry':{'post':operation('retry_failed_production','Reprend un incident de production borné')},
        '/production/review':{'post':operation('review_production_output','Accepte, corrige ou rejette un livrable et peut apprendre la correction')},
        '/production/verified':{'get':operation('get_verified_deliverables420','Livrables métier, preuves de destination, abstentions et incidents')},
        '/production/metrics':{'get':operation('get_production_metrics420','Couverture utile, corrections, temps estimé et coût externe')},
        '/production/studio/estimate':{'post':operation('estimate_production420','Estime temps, coût, modèle et confidentialité sans lancer la production',body='StudioEstimate420')},
        '/production/studio':{'post':operation('submit_production420','Prépare un livrable interne depuis un dossier et ses sources',body='StudioRequest420')},
        '/production/deliverables/{deliverable_id}/retry':{'post':operation('retry_deliverable420','Relance un incident de dépôt ou de production',path='deliverable_id')},
        '/matters/{matter_id}/advance':{'post':operation('advance_matter420','Actualise, structure et prépare les travaux internes possibles',path='matter_id')},
      }}


def operation(operation_id, summary, body='', path=''):
    value={'operationId':operation_id,'summary':summary,
           'responses':{'200':{'description':'Résultat JSON'},'400':{'description':'Demande refusée'}}}
    if body:
        value['requestBody']={'required':True,'content':{'application/json':{
            'schema':{'$ref':'#/components/schemas/'+body}}}}
    if path:
        value['parameters']=[{'name':path,'in':'path','required':True,'schema':{'type':'string'}}]
    return value


def matter_detail(desk, mid):
    matter=next((m for m in load_matters(desk.c) if m['id']==mid),None)
    if not matter:raise Stop('dossier_absent')
    index=DocumentIndex(desk.c['state_dir'])
    documents=[{'path':r[0],'modified':r[1],'error':r[2]} for r in index.db.execute(
        'SELECT path,modified,error FROM docs WHERE matter=? ORDER BY modified DESC LIMIT 40',(mid,))]
    brief=desk.db.execute('SELECT data,sources,created FROM case_briefs WHERE matter=? ORDER BY version DESC LIMIT 1',(mid,)).fetchone()
    summary={}
    if brief:
        try:summary=json.loads(brief['data'])
        except ValueError:summary={}
    tasks=[dict(r) for r in desk.db.execute('SELECT * FROM tasks WHERE matter=? ORDER BY due,created DESC LIMIT 30',(mid,))]
    from .legal_memory import memory_summary
    from .strategic import summary as strategic_summary
    from .portfolio import matter_status
    from .operating380 import matter_graph,recent_playbook_runs
    return {'matter':matter,'portfolio':matter_status(desk,matter),'summary':summary,'documents':documents,'tasks':tasks,
            'legal_memory':memory_summary(desk,mid),
            'strategic':strategic_summary(desk,mid),
            'argument_evidence_graph':matter_graph(desk,mid),
            'playbook_runs':recent_playbook_runs(desk,mid,10),
            'warning':'Fiche interne fondée sur les contenus indexés ; vérifier les sources originales.'}


def dispatch(desk, path, method, payload=None, query=None):
    payload=payload or {};query=query or {}
    if path=='/live/status' and method=='GET':
        from .live430 import snapshot
        return snapshot(desk)
    if path=='/live/check' and method=='POST':
        return {'job_ids':[desk.enqueue(kind,priority=5 if kind=='live_mail430' else 55)
          for kind in ('live_mail430','live_calendar430','live_documents430')],
          'status':'queued','warning':'Les politiques existantes de confidentialité et d’autonomie restent appliquées.'}
    if path=='/ai/providers' and method=='GET':
        from .ai_gateway import public_providers
        return {'providers':public_providers(desk.c),'secrets_exposed':False}
    if path=='/ai/health' and method=='GET':
        from .ai_gateway import provider_registry
        from .model import provider_diagnostic
        return {'providers':{key:provider_diagnostic(value) for key,value in provider_registry(desk.c).items()}}
    if path=='/ai/routing' and method=='GET':
        from .hybrid400 import snapshot
        return snapshot(desk,query.get('days',30))
    if path=='/ai/routing/simulate' and method=='POST':
        from .hybrid400 import simulate
        return simulate(desk,payload.get('purpose','assistant'),payload.get('stage',''),
          payload.get('input_characters',0),payload.get('source_count',0),
          payload.get('document_count',0),payload.get('max_tokens',3500),payload.get('matter',''))
    if path=='/ai/routing/preview' and method=='POST':
        from .hybrid400 import preview
        return preview(desk,payload.get('purpose','assistant'),payload.get('stage','chat'),
          payload.get('text',''),payload.get('matter',''),payload.get('max_tokens',3500))
    if path=='/ai/chat/completions' and method=='POST':
        from .ai_gateway import common_completion
        return common_completion(desk,payload)
    if path=='/extensions' and method=='GET':
        from .extensions364 import list_items
        return {'extensions':list_items(desk),'scripts_executed':False,'secrets_exposed':False}
    if path=='/extensions/test' and method=='POST':
        from .extensions364 import test_item
        return test_item(desk,payload.get('extension_id',''))
    if path=='/dashboard' and method=='GET':return dashboard(desk)
    if path=='/operating/today' and method=='GET':
        from .operating380 import action_center
        return action_center(desk,query.get('limit',160))
    if path=='/production/dashboard' and method=='GET':
        from .production391 import dashboard as production_dashboard
        return production_dashboard(desk,query.get('days',30))
    if path=='/production/run' and method=='POST':
        return {'job_id':desk.enqueue('production_cycle391',payload,priority=0),'status':'queued'}
    if path=='/production/playbooks/advance' and method=='POST':
        return {'job_id':desk.enqueue('advance_playbooks391',payload,priority=0),'status':'queued'}
    if path=='/production/retry' and method=='POST':
        return {'job_id':desk.enqueue('retry_production391',payload,priority=0),'status':'queued'}
    if path=='/production/review' and method=='POST':
        return {'job_id':desk.enqueue('review_output391',payload,priority=0),'status':'queued'}
    if path=='/production/verified' and method=='GET':
        from .production420 import dashboard as production_dashboard420
        return production_dashboard420(desk,query.get('days',30))
    if path=='/production/metrics' and method=='GET':
        from .production420 import metrics
        return metrics(desk,query.get('days',30))
    if path=='/production/studio/estimate' and method=='POST':
        from .production420 import estimate_studio
        return estimate_studio(desk,payload.get('deliverable_kind',''),payload.get('instruction',''),
          payload.get('source_paths',[]),payload.get('matter',''))
    if path=='/production/studio' and method=='POST':
        from .production420 import submit_studio
        return submit_studio(desk,payload)
    match=re.fullmatch(r'/production/deliverables/([a-f0-9]{64})/retry',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('retry_deliverable420',{'deliverable_id':match.group(1)},priority=0),'status':'queued'}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/advance',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('advance_matter420',{'matter':match.group(1)},priority=0),'status':'queued'}
    if path=='/learning' and method=='GET':
        from .learning392 import snapshot
        return snapshot(desk,query.get('days',30))
    match=re.fullmatch(r'/learning/corrections/(\d+)',path)
    if match and method=='POST':
        from .learning392 import set_correction_state
        return set_correction_state(desk,match.group(1),payload.get('enabled',False))
    if path=='/learning/business-rules' and method=='GET':
        from .learning410 import snapshot
        return snapshot(desk,query.get('days',30))
    if path=='/learning/business-rules' and method=='POST':
        from .learning410 import save_rule
        return save_rule(desk,payload.get('scope',''),payload.get('scope_value',''),
          payload.get('purpose','all'),payload.get('rule_type','other'),payload.get('instruction',''),
          'api','openapi')
    match=re.fullmatch(r'/learning/business-rules/(\d+)/status',path)
    if match and method=='POST':
        from .learning410 import set_rule_status
        return set_rule_status(desk,match.group(1),payload.get('status',''))
    if path=='/playbooks' and method=='GET':
        from .operating380 import playbooks
        return {'playbooks':playbooks(desk)}
    if path=='/playbooks' and method=='POST':
        from .operating380 import start_playbook
        return start_playbook(desk,payload.get('playbook_id',''),payload.get('matter',''),payload.get('objective',''))
    if path=='/ecosystem/services' and method=='GET':
        from .operating380 import services
        return {'services':services(desk),'secrets_exposed':False}
    if path=='/ecosystem/services' and method=='POST':
        from .operating380 import register_service
        return register_service(desk,payload.get('service_id',''),payload.get('name',''),payload.get('base_url',''),
          payload.get('capabilities',[]),payload.get('secret_ref',''),bool(payload.get('enabled')),bool(payload.get('reviewed')))
    if path=='/ecosystem/actions' and method=='POST':
        from .operating380 import prepare_ecosystem_action
        return prepare_ecosystem_action(desk,payload.get('service_id',''),payload.get('matter',''),payload.get('capability',''),payload.get('payload',{}))
    if path=='/ecosystem/events' and method=='GET':
        from .operating380 import ecosystem_events
        return {'events':ecosystem_events(desk,query.get('status',''),query.get('limit',100))}
    if path=='/evaluations' and method=='GET':
        from .operating380 import evaluation_runs
        return {'runs':evaluation_runs(desk,query.get('limit',20))}
    if path=='/evaluations' and method=='POST':
        return {'job_id':desk.enqueue('run_business_evaluation380',{},priority=0),'status':'queued'}
    if path=='/evaluations/legal' and method=='GET':
        from .evaluation410 import dashboard
        return dashboard(desk)
    if path=='/evaluations/legal/cases' and method=='POST':
        from .evaluation410 import save_case
        return save_case(desk,payload.get('name',''),payload.get('task_kind',''),
          payload.get('prompt',''),payload.get('expected',{}),bool(payload.get('anonymized')))
    if path=='/evaluations/legal/run' and method=='POST':
        if payload.get('confirm_cost') is not True:raise Stop('confirmation_cout_evaluation_requise')
        models=payload.get('models',[])
        if not isinstance(models,list):raise Stop('modele_evaluation_invalide')
        return {'job_id':desk.enqueue('run_legal_benchmark410',
          {'models':'\n'.join(str(x) for x in models)},priority=0),'status':'queued'}
    if path=='/relevance/metrics' and method=='GET':
        from .operating380 import relevance_metrics
        return relevance_metrics(desk)
    if path=='/guide/catalog' and method=='GET':
        from .guide import catalogue
        return catalogue()
    if path=='/audio/status' and method=='GET':
        from .audio import bridge_status
        return bridge_status(desk.c)
    if path=='/assistance/projects' and method=='GET':
        from .assistance35 import recent
        return {'projects':recent(desk,query.get('kind',''),query.get('matter',''),query.get('limit',30))}
    practice_jobs={'/assistance/coaching':'coach_hearing35',
      '/assistance/calls':'prepare_call35','/assistance/calls/report':'record_call35',
      '/assistance/calculations':'calculate35','/assistance/billing':'billing_review35',
      '/assistance/comparables':'classify_comparable35'}
    if method=='POST' and path in practice_jobs:
        return {'job_id':desk.enqueue(practice_jobs[path],payload,priority=0),'status':'queued',
          'warning':'Projet interne supervisé : contrôler sources, paramètres et résultat ; aucune action externe.'}
    if path=='/capabilities' and method=='GET':return {
      'version':'5.6.5','mode':'local_first_business_learning','draft_folder':desk.c['mail']['drafts'],
      'can':['search_cabinet','read_matters','ask_with_sources','monitor','analyze_strategy',
             'build_evidence_matrix','prepare_internal_act','propose_email_draft',
             'classify_active_portfolio','group_mail_associations','reconcile_work_queue',
             'read_calendar_period','read_and_sync_nextcloud_tasks','propose_weekly_work_plan',
             'prepare_document_projects','preview_document_files','watch_mail_and_attachments',
             'prepare_document_previews_automatically','independent_document_control',
             'maintain_structured_matter_memory','propose_diligences','propose_billing',
             'identify_latest_writings_with_certainty','anonymized_legal_research',
             'retrieve_and_verify_official_decisions','exact_quote_citations',
             'matter_case_law_registry','paragraph_provenance','exhibit_fingerprint_registry',
             'deterministic_document_control','identify_latest_party_writings',
             'compare_party_devices','prepare_hearing','prepare_oral_plans_5_10_20',
             'prepare_hearing_exhibit_checklist','prepare_word_clean_and_compared_versions',
             'preserve_word_styles_and_numbering','word_internal_references','word_change_report',
             'event_driven_mail_orchestration','mail_matter_differential','adapted_project_trigger',
             'propose_client_reply','single_consolidated_notification','prepare_legal_opinion',
             'analyze_claimant_and_defendant','grounded_decision_scenarios',
             'sensitivity_analysis','critique_first_instance_decision',
             'qualitative_probability_calibration','prepare_billing_for_review',
             'detect_potentially_unbilled_work','prepare_meetings','prepare_transcript_reports',
             'measure_workload','track_sourced_provisions','detect_inactive_matters',
             'risk_levels','group_low_and_medium_risk_confirmations',
             'resume_control_tower_after_incident','tamper_evident_audit',
             'local_first_ai_routing','openrouter_budget_caps','matter_external_exclusions',
             'anonymized_external_preview','hybrid_routing_audit','independent_second_model_control',
             'separate_fast_complex_control_models','continuous_business_tests',
             'single_lawyer_decision_dashboard','local_vocal_transcription_bridge',
             'automatic_verified_mail_drafts','automatic_internal_document_files',
             'event_driven_legal_production','automatic_playbook_progression',
             'production_coverage_and_error_dashboard',
             'guided_feature_catalog','copyable_supervised_prompts','read_seen_and_unseen_new_uids',
             'bounded_retroactive_mail_recovery','explain_why_nothing','single_conversational_cockpit',
             'nextcloud_onlyoffice_edit_links','map_matters_to_openwebui_folders',
             'read_unpaid_invoices','dual_mode_browser_and_local_dictation','daily_lawyer_briefing',
             'coach_hearing_with_plan_coverage','prepare_and_note_calls','deterministic_practice_calculations',
             'qualify_verified_comparable_decisions','observed_frequencies_with_denominators',
             'prepare_billing_review_without_invoice','common_ai_gateway',
             'ollama_openai_mistral_providers','protected_provider_secret_vault',
             'per_function_model_routing','roundcube_ai_fallback',
             'lawve_catalogue_registry','mcp_connector_diagnostics',
             'quarantined_skill_and_plugin_import','page_aware_long_document_analysis',
             'progressive_analysis_cache_and_resume','complete_uploaded_conclusions',
             'page_citations','enabled_skill_plugin_instructions',
             'enabled_mcp_legal_search_with_official_verification',
             'edit_complete_cancel_and_schedule_tasks','scroll_position_restore',
             'daily_action_center','matter_argument_evidence_graph',
             'simplified_today_matter_and_review','reversible_business_learning',
             'approved_corrections_and_template_metadata_in_model_context',
             'scoped_business_rules','trusted_document_fingerprints','legal_model_benchmark',
             'automatic_page_context','six_entry_interface',
             'litigation_and_contract_playbooks','shared_ecosystem_api',
             'business_evaluation_bench','relevance_metrics_with_denominators',
             'verified_business_deliverable_registry','seven_stage_business_progress',
             'unified_production_studio','verified_imap_and_nextcloud_readback',
             'business_result_today_dashboard','benchmark_guided_model_routing',
             'signed_stable_and_test_updates','numbered_transactional_migrations',
             'pdf_workshop_shortcut','simplified_legal_navigation'],
      'orchestrator_can':['event_driven_mail_classification','mail_matter_differential',
             'adapted_project_trigger','propose_client_reply','propose_diligence',
             'propose_billing','single_consolidated_notification'],
      'opinion_can':['prepare_legal_opinion','analyze_claimant_and_defendant',
             'ground_decision_scenarios_in_verified_case_law','sensitivity_analysis',
             'critique_first_instance_decision','qualitative_calibration_only'],
      'requires_confirmation':['deposit_email_draft','create_nextcloud_tasks','create_planning_events',
                               'create_new_nextcloud_document_files','create_hearing_bundle_files',
                               'create_word_revision_files','finalize_invoice','send_provision_reminder',
                               'high_or_critical_risk_decision'],
      'never':['send_email','file_or_sign_act','accept_transaction','change_calendar_without_confirmation',
               'overwrite_move_or_delete_nextcloud_document','file_to_rpva','run_shell_command',
               'automatic_final_invoice','automatic_payment'],
      'legal_research':'Les requêtes externes sont anonymisées. Une jurisprudence n’est insérable qu’après contrôle sur Légifrance, Judilibre ou une autre source officielle.'}
    if path=='/cabinet-control' and method=='GET':
        from .cabinet_pilot import dashboard as cabinet_dashboard
        return cabinet_dashboard(desk,query.get('status','pending'),query.get('limit',200))
    if path=='/cabinet-control/refresh' and method=='POST':
        return {'job_id':desk.enqueue('refresh_cabinet_pilotage',payload,priority=0),'status':'queued',
          'warning':'Détection et préparation locales uniquement ; aucune facture, relance, écriture ou action externe.'}
    if path=='/cabinet-decisions' and method=='GET':
        from .cabinet_pilot import dashboard as cabinet_dashboard
        return cabinet_dashboard(desk,query.get('status','pending'),query.get('limit',200))
    if path=='/cabinet-decision-batches' and method=='POST':
        from .cabinet_pilot import create_batch
        return create_batch(desk,payload)
    if path=='/cabinet/provisions' and method=='GET':
        from .cabinet_pilot import dashboard as cabinet_dashboard
        return {'provisions':cabinet_dashboard(desk,'all',1)['provisions'],
          'warning':'Registre interne seulement ; aucun paiement ni facture.'}
    if path=='/cabinet/provisions' and method=='POST':
        from .cabinet_pilot import record_provision
        return record_provision(desk,payload)
    if path=='/cabinet/meetings/prepare' and method=='POST':
        return {'job_id':desk.enqueue('prepare_meeting',payload,priority=0),'status':'queued',
          'warning':'Préparation interne ; agenda, invitations et courriels inchangés.'}
    if path=='/cabinet/transcripts/prepare' and method=='POST':
        return {'job_id':desk.enqueue('prepare_transcript_report',payload,priority=0),'status':'queued',
          'warning':'Compte rendu interne à contrôler ; aucune tâche, aucun document ni courriel créé.'}
    if path=='/cabinet/business-tests' and method=='POST':
        return {'job_id':desk.enqueue('run_continuous_business_tests',{},priority=0),'status':'queued'}
    if path=='/cabinet/audit' and method=='GET':
        from .cabinet_pilot import audit_status
        return audit_status(desk,query.get('limit',100))
    if path=='/workstation/briefing' and method=='GET':
        from .workstation import daily_briefing
        return daily_briefing(desk)
    if path=='/workstation/why-nothing' and method=='GET':
        from .workstation import why_nothing
        return why_nothing(desk)
    if path=='/invoice-ninja/unpaid' and method=='GET':
        from .workstation import unpaid_summary
        return unpaid_summary(desk,query.get('matter',''))
    if path=='/orchestrator/run' and method=='POST':
        return {'job_id':desk.enqueue('orchestrator_mail_sweep',payload,priority=20),'status':'queued',
          'warning':'Analyse et préparation internes uniquement ; aucune réponse, facture, écriture Nextcloud ou action de calendrier.'}
    if path=='/orchestrations' and method=='POST':
        return {'job_id':desk.enqueue('orchestrate_mail',payload,priority=0),'status':'queued',
          'warning':'Une seule notification consolidée sera produite. Rien ne sera envoyé ni créé dans Nextcloud.'}
    if path=='/orchestrations' and method=='GET':
        from .orchestrator import notifications
        return notifications(desk,query.get('status','unread'),query.get('limit',100))
    if path=='/legal-opinions' and method=='POST':
        return {'job_id':desk.enqueue('prepare_legal_opinion',payload,priority=0),'status':'queued',
          'warning':'Projet interne contradictoire. Aucune probabilité numérique, décision prédite ou action externe.'}
    if path=='/legal-opinions' and method=='GET':
        from .opinions import list_projects
        return list_projects(desk,query.get('matter',''),query.get('status','all'),query.get('limit',100))
    if path=='/autonomy/pending' and method=='GET':
        from .autonomy import pending_dashboard
        return pending_dashboard(desk)
    if path=='/autonomy/run' and method=='POST':
        return {'job_id':desk.enqueue('autonomy_mail_sweep',{},priority=20),'status':'queued',
          'warning':'Préparation interne uniquement : aucun document, facture, courriel ou événement n’est créé.'}
    if path=='/autonomy/diligences' and method=='GET':
        from .autonomy import diligence_proposals
        return {'proposals':diligence_proposals(desk,query.get('status','pending'),query.get('matter',''),query.get('limit',100))}
    if path=='/autonomy/billing' and method=='GET':
        from .autonomy import billing_proposals
        return {'proposals':billing_proposals(desk,query.get('status','pending'),query.get('matter',''),query.get('limit',100)),
          'warning':'Aucun montant ni aucune facture ne sont créés automatiquement.'}
    if path=='/legal-research' and method=='POST':
        return {'job_id':desk.enqueue('legal_research',payload,priority=0),'status':'queued',
          'warning':'Seule la requête anonymisée quitte AxiorHub. Les résultats fournisseurs restent des pistes tant que le texte officiel n’est pas vérifié.'}
    if path=='/legal-research/prepare' and method=='POST':
        from .legal_research import prepare_research_query
        return prepare_research_query(desk,payload)
    if path=='/legal-research/verify' and method=='POST':
        return {'job_id':desk.enqueue('verify_official_decision',payload,priority=0),'status':'queued',
          'warning':'Une citation ne devient utilisable qu’après correspondance de l’identifiant et de l’extrait exact dans le texte officiel récupéré.'}
    if path=='/daily-dashboard' and method=='GET':
        from .proactive import latest_dashboard
        return latest_dashboard(desk)
    if path=='/signals' and method=='GET':
        from .proactive import signals
        return {'signals':signals(desk,query.get('state','open'),query.get('matter',''),query.get('limit',100))}
    if path=='/work-items' and method=='GET':
        raw=query.get('states','');states=[x for x in raw.split(',') if x]
        return {'items':work_items(desk,states or None,query.get('limit',100))}
    if path=='/portfolio' and method=='GET':
        from .portfolio import portfolio_rows,portfolio_summary
        raw=query.get('states','active,to_confirm');states=[x for x in raw.split(',') if x]
        return {'summary':portfolio_summary(desk),
                'matters':portfolio_rows(desk,states,query.get('limit',200))}
    if path=='/portfolio/organize' and method=='POST':
        return {'job_id':desk.enqueue('organize_cabinet',{'restart':'yes'}),'status':'queued'}
    if path=='/portfolio/reconcile' and method=='POST':
        return {'job_id':desk.enqueue('reconcile_inbox'),'status':'queued'}
    if path=='/association-groups' and method=='GET':
        from .portfolio import grouped_associations
        return {'groups':grouped_associations(desk,query.get('status','pending'),query.get('limit',200))}
    if path=='/calendar/events' and method=='GET':
        from .workplan import calendar_events
        return calendar_events(desk,query.get('start',''),query.get('end',''),query.get('matter',''))
    if path=='/tasks' and method=='GET':
        from .workplan import list_tasks
        return {'tasks':list_tasks(desk,query.get('status',''),query.get('matter',''),query.get('limit',300))}
    if path=='/tasks/sync' and method=='POST':
        return {'job_id':desk.enqueue('sync_caldav_tasks',{},priority=0),'status':'queued'}
    if path=='/planning/proposals' and method=='POST':
        return {'job_id':desk.enqueue('propose_work_plan',payload,priority=0),'status':'queued'}
    if path=='/document-projects' and method=='POST':
        document_type=str(payload.get('document_type',''))
        if document_type not in {'conclusions','assignation','cgv','contrat','charte_rgpd','bcp','courrier','document','constitution','assignation_refere','mise_en_demeure','courrier_confrere'}:
            raise Stop('type_document_invalide')
        return {'job_id':desk.enqueue('prepare_document_project',payload,priority=0),'status':'queued',
          'warning':'Préparation locale uniquement : aucun fichier Nextcloud n\'est créé.'}
    if path=='/hearing/writings' and method=='POST':
        return {'job_id':desk.enqueue('identify_party_writings',payload,priority=0),'status':'queued',
          'warning':'Lecture et comparaison seulement : aucun fichier n’est créé.'}
    if path=='/hearing/devices/compare' and method=='POST':
        return {'job_id':desk.enqueue('compare_devices',payload,priority=0),'status':'queued',
          'warning':'Comparaison lexicale et traçable ; elle ne remplace pas le contrôle juridique de l’avocat.'}
    if path=='/hearing-projects' and method=='POST':
        return {'job_id':desk.enqueue('prepare_hearing',payload,priority=0),'status':'queued',
          'warning':'Préparation interne uniquement : aucun dossier de plaidoirie n’est créé.'}
    if path=='/word-projects' and method=='POST':
        return {'job_id':desk.enqueue('prepare_word_project',payload,priority=0),'status':'queued',
          'warning':'Préparation interne uniquement : le fichier Word source reste inchangé.'}
    if path=='/search' and method=='POST':return cabinet_search(desk.c,payload.get('query',''),payload.get('limit',12))
    if path=='/assistant' and method=='POST':
        return submit_question(desk,payload.get('question',''),payload.get('matter',''),
                               payload.get('mail_key',''),payload.get('thread_id',''),payload.get('attachment_id',''),
                               payload.get('attachment_ids',[]),payload.get('page_context',{}))
    if path=='/threads' and method=='GET':return {'threads':threads(desk,query.get('limit',30))}
    if path=='/supervision' and method=='GET':
        from .supervision import requests
        return {'requests':requests(desk,query.get('status',''),query.get('limit',50))}
    if path=='/jobs' and method=='GET':
        rows=desk.db.execute('''SELECT id,kind,status,priority,created,finished,result FROM jobs
          ORDER BY CASE WHEN status IN ('running','cancel_requested') THEN 0 WHEN status='pending' THEN 1 ELSE 2 END,
          priority,id DESC LIMIT 100''').fetchall()
        return {'jobs':[dict(x) for x in rows]}
    if path=='/system/status' and method=='GET':
        from .reliability393 import status_snapshot
        return status_snapshot(desk)
    if path=='/system/checks/run' and method=='POST':
        from .reliability393 import run_checks
        return run_checks(desk,include_external=False)
    if path=='/system/openrouter-test' and method=='POST':
        from .reliability393 import test_openrouter
        return test_openrouter(desk)
    match=re.fullmatch(r'/assistance/projects/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .assistance35 import preview
        return preview(desk,match[1])
    match=re.fullmatch(r'/long-documents/([a-f0-9]{64})',path)
    if match and method=='GET':
        from .long_documents365 import run_status
        return run_status(desk,match[1])
    match=re.fullmatch(r'/assistance/comparables/([A-Za-z0-9_-]{1,80})',path)
    if match and method=='GET':
        from .assistance35 import comparables
        return comparables(desk,match[1])
    match=re.fullmatch(r'/cabinet-decisions/([a-f0-9]{64})/review',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('review_cabinet_decision',{
          'decision_id':match[1],'status':payload.get('status',''),
          'confirm_risk':payload.get('confirm_risk','no'),
          'snooze_hours':payload.get('snooze_hours',24)},priority=0),'status':'queued'}
    match=re.fullmatch(r'/cabinet-decision-batches/([a-f0-9]{32})/approve',path)
    if match and method=='POST':
        from .cabinet_pilot import approve_batch
        return approve_batch(desk,{'batch_id':match[1],
          'confirmation_code':payload.get('confirmation_code','')})
    match=re.fullmatch(r'/cabinet/meetings/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .cabinet_pilot import meeting_preview
        return meeting_preview(desk,match[1])
    match=re.fullmatch(r'/cabinet/transcripts/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .cabinet_pilot import transcript_preview
        return transcript_preview(desk,match[1])
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})',path)
    if match and method=='GET':return matter_detail(desk,match[1])
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/graph',path)
    if match:
        from .operating380 import matter_graph
        if method=='GET':return matter_graph(desk,match[1])
        if method=='POST':return {'job_id':desk.enqueue('refresh_matter_graph380',{'matter':match[1]},priority=0),'status':'queued'}
    match=re.fullmatch(r'/operating/actions/([a-f0-9]{64})/review',path)
    if match and method=='POST':
        from .operating380 import review_action
        return review_action(desk,match[1],payload.get('state',''),payload.get('note',''),payload.get('snoozed_until',''))
    match=re.fullmatch(r'/playbook-runs/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .operating380 import playbook_run
        return playbook_run(desk,match[1])
    match=re.fullmatch(r'/playbook-runs/([a-f0-9]{32})/advance',path)
    if match and method=='POST':return {'job_id':desk.enqueue('advance_playbook380',{'run_id':match[1]},priority=0),'status':'queued'}
    match=re.fullmatch(r'/playbook-runs/([a-f0-9]{32})/steps/(\d+)/complete',path)
    if match and method=='POST':
        from .operating380 import complete_manual_step
        return complete_manual_step(desk,match[1],match[2])
    match=re.fullmatch(r'/ecosystem/events/([a-f0-9]{64})/complete',path)
    if match and method=='POST':
        from .operating380 import complete_ecosystem_event
        return complete_ecosystem_event(desk,match[1],payload.get('status',''),payload.get('result',{}))
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/openwebui-folder',path)
    if match and method=='GET':
        from .workstation import workspace_mapping
        return {'matter':match[1],'mapping':workspace_mapping(desk,match[1])}
    match=re.fullmatch(r'/legal-research/([a-f0-9]{64})/mcp-results',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('import_mcp_legal_results',{'query_id':match[1],
          'provider':payload.get('provider',''),'anonymized_query':payload.get('anonymized_query',''),
          'results':payload.get('results',[]),'limit':payload.get('limit',20)},priority=0),'status':'queued',
          'warning':'Les pistes MCP ne deviennent citables qu’après récupération et concordance du texte officiel.'}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/monitor',path)
    if match and method=='POST':
        if not any(m['id']==match[1] for m in load_matters(desk.c)):raise Stop('dossier_absent')
        return {'job_id':desk.enqueue('monitor_matter',{'matter':match[1]}),'status':'queued','matter':match[1]}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/state',path)
    if match and method=='POST':
        from .portfolio import set_matter_state
        return set_matter_state(desk,{'matter':match[1],'state':payload.get('state','')})
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/memory',path)
    if match and method=='GET':
        from .legal_memory import memory_records,conflicts
        return {'matter':match[1],'records':memory_records(desk,match[1]),
                'conflicts':conflicts(desk,match[1])}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/operational-memory',path)
    if match and method=='GET':
        from .autonomy import operational_memory
        return operational_memory(desk,match[1])
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/latest-writings',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('identify_latest_writings',{'matter':match[1],
          'document_type':payload.get('document_type','conclusions')},priority=0),'status':'queued'}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/authorities',path)
    if match and method=='GET':
        from .legal_research import authorities
        return authorities(desk,match[1],query.get('status','all'),query.get('limit',100))
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/exhibits',path)
    if match and method=='GET':
        from .legal_research import exhibits
        return exhibits(desk,match[1],query.get('limit',300))
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/exhibits/refresh',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('refresh_exhibit_registry',{'matter':match[1]},priority=0),
          'status':'queued','warning':'Lecture et empreinte uniquement : aucune pièce n’est déplacée, renommée ou supprimée.'}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/timeline',path)
    if match and method=='GET':
        from .legal_memory import timeline
        return {'matter':match[1],'events':timeline(desk,match[1],query.get('limit',300))}
    match=re.fullmatch(r'/matters/([A-Za-z0-9_-]{1,80})/(strategy|matrix|act-projects)',path)
    if match:
        mid,kind=match[1],match[2]
        from .strategic import ACT_TYPES,act_projects,latest_matrix,latest_strategy,matter
        matter(desk,mid)
        if method=='GET':
            if kind=='strategy':return {'matter':mid,'analysis':latest_strategy(desk,mid)}
            if kind=='matrix':return {'matter':mid,'matrix':latest_matrix(desk,mid)}
            return {'matter':mid,'projects':act_projects(desk,mid)}
        if method=='POST':
            if kind=='strategy':
                objective=str(payload.get('objective','')).strip()
                if not objective or len(objective)>3000:raise Stop('objectif_requis_3000_caracteres_maximum')
                job=desk.enqueue('analyze_strategy',{'matter':mid,'objective':objective})
            elif kind=='matrix':
                objective=str(payload.get('objective','')).strip()
                if len(objective)>3000:raise Stop('objectif_trop_long')
                job=desk.enqueue('build_matrix',{'matter':mid,'objective':objective})
            else:
                act_type=str(payload.get('act_type',''));instruction=str(payload.get('instruction','')).strip()
                if act_type not in ACT_TYPES:raise Stop('type_acte_invalide')
                if not instruction or len(instruction)>3000:raise Stop('instruction_requise_3000_caracteres_maximum')
                job=desk.enqueue('draft_act',{'matter':mid,'act_type':act_type,'instruction':instruction})
            return {'job_id':job,'status':'queued','matter':mid,
                    'warning':'Résultat interne seulement ; aucune validation, aucun dépôt et aucun envoi automatiques.'}
    match=re.fullmatch(r'/threads/([a-f0-9]{32})',path)
    if match and method=='GET':return {'messages':thread_messages(desk,match[1])}
    match=re.fullmatch(r'/jobs/(\d+)',path)
    if match and method=='GET':
        row=desk.db.execute('SELECT id,kind,status,priority,created,finished,result FROM jobs WHERE id=?',(int(match[1]),)).fetchone()
        if not row:raise Stop('operation_absente')
        return dict(row)
    match=re.fullmatch(r'/jobs/(\d+)/cancel',path)
    if match and method=='POST':return desk.cancel_job(match[1])
    match=re.fullmatch(r'/jobs/(\d+)/retry',path)
    if match and method=='POST':
        from .reliability393 import retry_job
        return retry_job(desk,match[1])
    match=re.fullmatch(r'/tasks/([a-f0-9]{64})/status',path)
    if match and method=='POST':
        return {'job_id':desk.enqueue('update_work_task',{'task':match[1],'status':payload.get('status','')},priority=0),
                'status':'queued'}
    match=re.fullmatch(r'/planning/proposals/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .workplan import get_plan
        return get_plan(desk,match[1])
    match=re.fullmatch(r'/planning/proposals/([a-f0-9]{32})/(approve|reject)',path)
    if match and method=='POST':
        from .workplan import approve_plan,reject_plan
        return approve_plan(desk,match[1],payload.get('confirmation_code','')) if match[2]=='approve' else reject_plan(desk,match[1])
    match=re.fullmatch(r'/document-projects/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .document_projects import preview
        return preview(desk,match[1])
    match=re.fullmatch(r'/document-projects/([a-f0-9]{32})/confirm',path)
    if match and method=='POST':
        args={'project_id':match[1],'confirmation_code':payload.get('confirmation_code',''),
          'source_path':payload.get('source_path',''),'destination_folder':payload.get('destination_folder','')}
        return {'job_id':desk.enqueue('create_document_files',args,priority=0),'status':'queued',
          'message':'Confirmation acceptée. Seuls les nouveaux fichiers prévisualisés seront créés.'}
    match=re.fullmatch(r'/document-projects/([a-f0-9]{32})/reject',path)
    if match and method=='POST':
        from .document_projects import reject_project
        return reject_project(desk,match[1])
    match=re.fullmatch(r'/document-projects/([a-f0-9]{32})/provenance',path)
    if match and method=='GET':
        from .legal_research import provenance
        return provenance(desk,match[1])
    match=re.fullmatch(r'/document-projects/([a-f0-9]{32})/deterministic-control',path)
    if match and method=='GET':
        from .legal_research import deterministic_control_report
        return deterministic_control_report(desk,match[1])
    match=re.fullmatch(r'/hearing-projects/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .hearing import preview_hearing
        return preview_hearing(desk,match[1])
    match=re.fullmatch(r'/hearing-projects/([a-f0-9]{32})/confirm',path)
    if match and method=='POST':
        args={'project_id':match[1],'confirmation_code':payload.get('confirmation_code',''),
          'our_source_path':payload.get('our_source_path',''),
          'opponent_source_path':payload.get('opponent_source_path',''),
          'destination_folder':payload.get('destination_folder','')}
        return {'job_id':desk.enqueue('create_hearing_files',args,priority=0),'status':'queued',
          'message':'Confirmation reçue. Seuls les nouveaux fichiers d’audience prévisualisés seront créés.'}
    match=re.fullmatch(r'/hearing-projects/([a-f0-9]{32})/reject',path)
    if match and method=='POST':
        from .hearing import reject_hearing
        return reject_hearing(desk,match[1])
    match=re.fullmatch(r'/word-projects/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .word_legal import preview_word_project
        return preview_word_project(desk,match[1])
    match=re.fullmatch(r'/word-projects/([a-f0-9]{32})/confirm',path)
    if match and method=='POST':
        args={'project_id':match[1],'confirmation_code':payload.get('confirmation_code',''),
          'source_path':payload.get('source_path',''),'destination_folder':payload.get('destination_folder','')}
        return {'job_id':desk.enqueue('create_word_files',args,priority=0),'status':'queued',
          'message':'Confirmation reçue. La source ne sera jamais écrasée.'}
    match=re.fullmatch(r'/word-projects/([a-f0-9]{32})/reject',path)
    if match and method=='POST':
        from .word_legal import reject_word_project
        return reject_word_project(desk,match[1])
    match=re.fullmatch(r'/orchestrations/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .orchestrator import preview
        return preview(desk,match[1])
    match=re.fullmatch(r'/orchestration-notifications/([a-f0-9]{64})/review',path)
    if match and method=='POST':
        status=str(payload.get('status',''))
        if status not in ('read','rejected'):raise Stop('revue_notification_invalide')
        return {'job_id':desk.enqueue('review_orchestration_notification',{
          'notification_id':match[1],'status':status},priority=0),'status':'queued'}
    match=re.fullmatch(r'/legal-opinions/([a-f0-9]{32})',path)
    if match and method=='GET':
        from .opinions import preview
        return preview(desk,match[1])
    match=re.fullmatch(r'/legal-opinions/([a-f0-9]{32})/reject',path)
    if match and method=='POST':
        from .opinions import reject
        return reject(desk,match[1])
    match=re.fullmatch(r'/autonomy/(diligences|billing)/([a-f0-9]{64})/review',path)
    if match and method=='POST':
        status=str(payload.get('status',''))
        if status not in ('accepted','dismissed'):raise Stop('etat_proposition_invalide')
        return {'job_id':desk.enqueue('review_autonomy_proposal',{
          'proposal_kind':'diligence' if match[1]=='diligences' else 'billing',
          'proposal':match[2],'status':status},priority=0),'status':'queued'}
    match=re.fullmatch(r'/memory/([a-f0-9]{64})/(validate|archive)',path)
    if match and method=='POST':
        from .legal_memory import change_record
        kind='validate_memory' if match[2]=='validate' else 'archive_memory'
        return change_record(desk,kind,{'record':match[1],**payload})
    match=re.fullmatch(r'/signals/([a-f0-9]{64})/(acknowledge|snooze|resolve)',path)
    if match and method=='POST':
        from .proactive import change_signal
        kinds={'acknowledge':'ack_signal','snooze':'snooze_signal','resolve':'resolve_signal'}
        return change_signal(desk,kinds[match[2]],{'signal':match[1],'hours':payload.get('hours',24)})
    if path=='/supervision/drafts' and method=='POST':
        from .supervision import propose
        return propose(desk,{'action':'prepare_reply','mail_key':payload.get('mail_key',''),
                             'instruction':payload.get('instruction','')})
    match=re.fullmatch(r'/supervision/([a-f0-9]{32})/(approve|reject)',path)
    if match and method=='POST':
        from .supervision import approve,reject
        args={'approval_id':match[1],'confirmation_code':payload.get('confirmation_code','')}
        return approve(desk,args) if match[2]=='approve' else reject(desk,args)
    if path=='/drafts' and method=='POST':
        raise Stop('utiliser_supervision_brouillon')
    raise Stop('route_api_absente')

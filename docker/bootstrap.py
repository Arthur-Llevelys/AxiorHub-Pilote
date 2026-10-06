#!/usr/bin/env python3
"""Create a secret-free first-run configuration from Docker environment values."""
import hashlib,json,os,secrets
from pathlib import Path

data=Path(os.environ.get('AXIORHUB_DATA_DIR','/data'));data.mkdir(parents=True,exist_ok=True);(data/'state').mkdir(exist_ok=True)
secrets_dir=data/'secrets';secrets_dir.mkdir(exist_ok=True)
def flag(name,default=False):return os.environ.get(name,'true' if default else 'false').strip().lower() in ('1','true','yes','on')
def secret(name,env):
    path=secrets_dir/(name+'.secret')
    value=os.environ.get(env,'')
    if value and not path.exists():path.write_text(value+'\n');os.chmod(path,0o600)
    elif not path.exists():path.write_text('not-configured\n');os.chmod(path,0o600)
    return str(path)
matters=data/'matters.json'
if not matters.exists():matters.write_text('[]\n')
config=data/'config.json'
root=os.environ.get('AXIORHUB_NEXTCLOUD_ROOT','/Dossiers')
model=os.environ.get('AXIORHUB_OLLAMA_MODEL','qwen3:8b')
if not config.exists():
  c={'version':1,'mode':'drafts','state_dir':str(data/'state'),'matters_file':str(matters),'max_messages_per_run':8,
   'allow_legal_clarification':True,'memory':{'enabled':True,'retention_days':90,'max_messages_per_run':30,'max_examples':2},
   'mail':{'host':os.environ.get('AXIORHUB_IMAP_HOST','imap.example.com'),'port':int(os.environ.get('AXIORHUB_IMAP_PORT','993')),'username':os.environ.get('AXIORHUB_IMAP_USERNAME','user@example.com'),'password_file':secret('imap','AXIORHUB_IMAP_PASSWORD'),'inbox':'INBOX','drafts':os.environ.get('AXIORHUB_IMAP_DRAFTS','Drafts'),'sent':os.environ.get('AXIORHUB_IMAP_SENT','Sent'),'from_address':os.environ.get('AXIORHUB_FROM_ADDRESS','user@example.com'),'from_name':os.environ.get('AXIORHUB_FROM_NAME','Cabinet exemple'),'signature':'','own_addresses':[os.environ.get('AXIORHUB_FROM_ADDRESS','user@example.com')],'reply_all_known_clients':True,'lookback_days':0,'process_seen_recent':True,'retroactive_lookback_days':7,'retroactive_max_candidates_per_run':80,'max_thread_messages':50,'max_body_chars':100000,'max_message_bytes':20000000,'excluded_senders':[],'excluded_domains':[],'excluded_subject_patterns':[]},
   'nextcloud':{'url':os.environ.get('AXIORHUB_NEXTCLOUD_URL','https://cloud.example.com'),'username':os.environ.get('AXIORHUB_NEXTCLOUD_USERNAME','user'),'password_file':secret('nextcloud','AXIORHUB_NEXTCLOUD_PASSWORD'),'roots':[root],'matter_roots':[root],'max_depth':8,'max_files':500,'max_file_bytes':15000000,'inventory_page_files':250,'max_inventory_directories':2000,'max_analysis_files':5000},
   'calendar':{'urls':[],'timezone':os.environ.get('TZ','Europe/Paris'),'horizon_days':90,'availability_days':10,'appointment_minutes':30,'buffer_minutes':15,'notice_hours':24,'weekdays':[0,1,2,3,4],'working_hours':[['09:00','12:00'],['14:00','18:00']]},
   'ollama':{'url':os.environ.get('AXIORHUB_OLLAMA_URL','http://host.docker.internal:11434'),'model':model,'timeout_seconds':240,'num_ctx':49152,'max_context_chars':90000,'disable_thinking':True,'local_hosts':['ollama','host.docker.internal']},
   'rag':{'enabled':True,'embedding_model':os.environ.get('AXIORHUB_EMBEDDING_MODEL','qwen3-embedding:0.6b'),'embedding_chunks_per_run':24,'timeout_seconds':180},
   'documents':{'ocr':True,'max_pdf_pages':500,'max_file_bytes':20000000,'max_document_chars':500000,'max_documents_per_mail':5,'index_updates_per_run':25,'inventory_page_files':250},
   'automation':{'health_enabled':True,'health_interval_minutes':30,'sync_enabled':False,'sync_interval_minutes':30,'reconcile_inbox_enabled':False,'reconcile_interval_minutes':15,'classify_portfolio_enabled':False,'portfolio_interval_minutes':360,'index_all_enabled':False,'index_interval_minutes':360,'daily_digest_enabled':False,'daily_digest_hour':8},
   'autonomy':{'enabled':flag('AXIORHUB_PRODUCTION_ENABLED'),'mail_monitor_interval_minutes':5,'mail_batch_size':20,'automatic_document_previews_enabled':True,'automatic_internal_files_enabled':flag('AXIORHUB_AUTOMATIC_INTERNAL_FILES'),'document_control_enabled':True,'control_model':model,'diligence_proposals_enabled':True,'billing_proposals_enabled':True,'automatic_matter_confidence_min':80},
   'orchestrator':{'enabled':flag('AXIORHUB_PRODUCTION_ENABLED'),'mail_monitor_interval_minutes':5,'mail_batch_size':20,'automatic_mail_drafts_enabled':flag('AXIORHUB_AUTOMATIC_MAIL_DRAFTS'),'automatic_legal_projects_enabled':flag('AXIORHUB_AUTOMATIC_LEGAL_PROJECTS')},
   'production':{'enabled':flag('AXIORHUB_PRODUCTION_ENABLED'),'interval_minutes':5,'batch_size':20,'auto_advance_playbooks':True,'retry_failed_drafts':True,'max_automatic_attempts':3,'verify_remote_deliverables':True,'verification_retry_limit':3,'useful_metrics_days':30,'automatic_internal_and_reversible':True},
   'updates':{'channel':os.environ.get('AXIORHUB_UPDATE_CHANNEL','stable'),'metadata_url':os.environ.get('AXIORHUB_UPDATE_METADATA_URL',''),'require_signature':True,'minisign_public_key_file':str(data/'update-minisign.pub')},
   'model_routing':{'fast_model':model,'complex_model':model,'control_model':model,'fast_temperature':0,'complex_temperature':0,'control_temperature':0},'ai_providers':{},'lawve_extensions':{},
   'speech568':{'provider':os.environ.get('AXIORHUB_SPEECH_PROVIDER','espeak'),'url':os.environ.get('AXIORHUB_SPEECH_URL','http://kokoro:8880'),'voice':os.environ.get('AXIORHUB_SPEECH_VOICE','ff_siwis'),'model':os.environ.get('AXIORHUB_SPEECH_MODEL','kokoro'),'external_allowed':False,'fallback_local':True},
   'document_agents568':{'incoming_paths':[x.strip() for x in os.environ.get('AXIORHUB_DOCUMENT_INCOMING_PATHS','').split(';') if x.strip()]},
   'google_calendar568':{'client_id':os.environ.get('AXIORHUB_GOOGLE_CLIENT_ID',''),'client_secret_file':secret('google-client','AXIORHUB_GOOGLE_CLIENT_SECRET') if os.environ.get('AXIORHUB_GOOGLE_CLIENT_SECRET') else '',
      'redirect_uri':os.environ.get('AXIORHUB_GOOGLE_REDIRECT_URI',os.environ.get('AXIORHUB_PUBLIC_URL','https://agent.example.com').rstrip('/')+'/api440/m568/google/callback')},
   'audio':{'enabled':False},'workstation':{'roundcube_url':os.environ.get('AXIORHUB_ROUNDCUBE_URL','https://mail.example.com'),'openwebui_url':os.environ.get('AXIORHUB_OPENWEBUI_URL','https://ai.example.com')},
   'legal_memory':{'enabled':True},'portfolio':{'enabled':True},'document_projects':{'enabled':True},'hearing':{'enabled':True},'word_legal':{'enabled':True},'opinions':{'enabled':True},'cabinet_pilotage':{'enabled':True}}
  config.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n');os.chmod(config,0o600)
# 5.6.0 : une configuration déjà renseignée (mise à jour) n'a pas besoin de l'assistant d'installation.
try:
  existing=json.loads(config.read_text())
  if 'installation' not in existing and 'example.com' not in existing.get('mail',{}).get('host','example.com'):
    existing['installation']={'done':True,'at':'migration','by':'mise-a-jour'};config.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+chr(10))
except (OSError,ValueError):pass
token_file=secrets_dir/'internal-api-token.secret'
token=os.environ.get('AXIORHUB_INTERNAL_API_TOKEN','').strip()
if not token or len(token)<32 or token.lower().startswith(('replace','a-generer','not-configured')):
  token=token_file.read_text().strip() if token_file.exists() else ''
  if len(token)<32 or token.lower().startswith(('replace','a-generer','not-configured')):
    token=secrets.token_urlsafe(48)
token_file.write_text(token+'\n');os.chmod(token_file,0o600)
auth=data/'internal-auth.json'
origin=os.environ.get('AXIORHUB_PUBLIC_URL','https://agent.example.com')
previous=json.loads(auth.read_text()) if auth.exists() else {}
salt=bytes.fromhex(previous.get('salt')) if previous.get('salt') else secrets.token_bytes(16)
dummy=previous.get('hash') or hashlib.scrypt(secrets.token_bytes(32),salt=salt,n=16384,r=8,p=1).hex()
auth.write_text(json.dumps({'origin':origin,'prefix':'','username':'disabled','salt':salt.hex(),'hash':dummy,'csrf':previous.get('csrf') or secrets.token_urlsafe(32),'api_token_sha256':hashlib.sha256(token.encode()).hexdigest()},indent=2));os.chmod(auth,0o600)
print(token)

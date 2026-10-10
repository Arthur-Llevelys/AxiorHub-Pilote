"""Interactive local configuration. Passwords never enter command arguments."""
import getpass
try:
    import grp   # installation serveur Linux
except ImportError:   # 5.6.25 : poste Windows (setup.py n'y sert pas)
    grp = None
import json
import os
from pathlib import Path
import re
import tempfile

from .common import Stop, private_json, HTTP, load_config
from .mailbox import Mailbox
from .dav import DAV


def ask(label, default=''):
    value = input(label + (' ['+str(default)+']' if default != '' else '') + ' : ').strip()
    return value or default


def split(value): return [x.strip() for x in value.split(',') if x.strip()]


def decode_utf7(value):
    import base64
    def dec(m):
        if not m[1]: return '&'
        b = m[1].replace(',', '/')
        return base64.b64decode(b + '='*((-len(b))%4)).decode('utf-16be')
    return re.sub(r'&([^-]*)-', dec, value)


def mailbox_names(box):
    status, data = box.conn.list()
    if status != 'OK': raise Stop('liste_dossiers_imap_indisponible')
    out = []
    for line in data:
        if not isinstance(line, bytes): continue
        m = re.match(rb'\((.*?)\) (?:"(?:\\.|[^"])*"|NIL) (.*)', line)
        if not m: continue
        name = m[2].decode('ascii')
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        out.append((decode_utf7(name), m[1].decode().lower().split()))
    return out


def configure(path):
    if os.geteuid() != 0: raise Stop('configuration_necessite_sudo')
    target = Path(path)
    if target.exists(): raise Stop('configuration_existe_utiliser_sudoedit')
    from diagnostic import literal_settings
    detected = literal_settings()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    secret_dir = target.parent/'secrets'
    secret_dir.mkdir(mode=0o750, exist_ok=True)
    def secret(name, reuse=None):
        dest = secret_dir/(name+'.secret')
        if dest.exists():
            value = getpass.getpass('Secret '+name+' (Entrée pour conserver celui déjà saisi) : ')
            if not value: return str(dest)
        elif reuse and Path(reuse).is_file():
            value = getpass.getpass('Mot de passe '+name+' (Entrée pour reprendre le secret de la V8) : ')
            if not value: value = Path(reuse).read_text(encoding='utf-8').strip()
        else: value = getpass.getpass('Mot de passe '+name+' (saisie masquée) : ')
        if not value or '\n' in value or '\r' in value: raise Stop('secret_vide_ou_invalide')
        fd = os.open(dest, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
        with os.fdopen(fd,'w') as f: f.write(value+'\n')
        return str(dest)
    print('Configuration locale. Les secrets restent sur ce serveur.')
    host = detected.get('imap_host', detected.get('default_host', 'courriel.example.com'))
    host = re.sub(r'^(ssl|tls)://', '', host)
    port = 993
    if ':' in host and host.rsplit(':',1)[-1].isdigit(): host, port = host.rsplit(':',1); port=int(port)
    mail = {'host': ask('Serveur IMAP TLS',host), 'port': int(ask('Port IMAP TLS',str(port))),
            'username': ask('Identifiant de connexion à votre messagerie'), 'inbox': 'INBOX'}
    mail['password_file'] = secret('imap')
    mail['from_address'] = ask('Votre adresse d’expédition', mail['username'] if '@' in mail['username'] else '')
    if not re.fullmatch(r'[^\s@]+@[^\s@]+',mail['from_address']): raise Stop('adresse_expedition_invalide')
    mail['own_addresses'] = list(dict.fromkeys([mail['from_address']] + split(ask('Autres adresses du cabinet reçues dans cette boîte, séparées par virgules'))))
    mail['from_name'] = ask('Nom d’expédition','Maître Exemple')
    signature_path = ask('Fichier de votre signature texte existante (Entrée pour une signature simple)')
    mail['signature'] = Path(signature_path).read_text(encoding='utf-8') if signature_path else 'Maître Exemple\nAvocat au Barreau de Lyon'
    box = Mailbox(mail)
    try:
        names = mailbox_names(box)
        print('Dossiers IMAP : '+', '.join(n for n,_ in names))
        drafts = next((n for n,f in names if '\\drafts' in f), detected.get('drafts_mbox','Drafts'))
        sent = next((n for n,f in names if '\\sent' in f), detected.get('sent_mbox','Sent'))
        mail['inbox'] = ask('Dossier à surveiller','INBOX')
        mail['drafts'] = ask('Dossier Brouillons utilisé par Roundcube', drafts)
        mail['sent'] = ask('Dossier Envoyés utilisé par Roundcube', sent)
        for folder in [mail['inbox'],mail['drafts'],mail['sent']]: box.select(folder)
    finally: box.close()
    mail['reply_all_known_clients'] = True
    mail.update(lookback_days=int(ask('Antériorité maximale en jours (0 = tous les non lus)','0')),
                process_seen_recent=True, retroactive_lookback_days=7,
                retroactive_max_candidates_per_run=80,
                max_thread_messages=50, max_body_chars=100000,
                max_message_bytes=20000000, excluded_senders=[], excluded_domains=[], excluded_subject_patterns=[])
    nc = {'url': ask('URL de Nextcloud',detected.get('ai_nextcloud_url','https://cloud.example.com')),
          'username': ask('Identifiant Nextcloud',detected.get('ai_nextcloud_username',''))}
    nc['password_file'] = secret('nextcloud',detected.get('ai_nextcloud_password_file'))
    roots = detected.get('ai_nextcloud_allowed_roots', [])
    nc['roots'] = split(ask('Racines documentaires autorisées, séparées par virgules', ', '.join(roots)))
    if not nc['roots']: raise Stop('racines_documentaires_obligatoires')
    nc['matter_roots']=[(root.rstrip('/')+'/01 - Dossiers'
                         if root.rstrip('/').rsplit('/',1)[-1].casefold()=='cabinet exemple'
                         else root) for root in nc['roots']]
    nc.update(max_depth=8,max_files=500,max_file_bytes=15000000,
              inventory_page_files=250,max_inventory_directories=2000,
              max_analysis_files=5000)
    dav = DAV(nc)
    for root in nc['roots']: dav.list_folder(root)
    calendars = dav.calendars()
    if not calendars: raise Stop('aucun_calendrier_partage_avec_ce_compte')
    for i,cal in enumerate(calendars,1): print(str(i)+'. '+cal['name'])
    default = next((str(i) for i,cal in enumerate(calendars,1) if cal['name']=='CABINET EXEMPLE'),'')
    chosen = split(ask('Numéros de TOUS les agendas à consulter pour vos disponibilités',default))
    try: urls = [calendars[int(i)-1]['url'] for i in chosen if int(i)>0]
    except (ValueError,IndexError): raise Stop('selection_agenda_invalide') from None
    if not urls: raise Stop('selection_agenda_vide')
    calendar = {'urls': urls, 'timezone':'Europe/Paris','horizon_days':90,'availability_days':10,
                'appointment_minutes':30,'buffer_minutes':15,'notice_hours':24,
                'weekdays':[0,1,2,3,4],'working_hours':[['09:00','12:00'],['14:00','18:00']]}
    ollama = {'url': ask('URL locale Ollama','http://127.0.0.1:11434'),
              'timeout_seconds':240, 'num_ctx':49152,'max_context_chars':90000,'disable_thinking':True}
    http = HTTP(ollama['url'],local_only=True)
    models = [m['name'] for m in http.json('GET','/api/tags').get('models',[])]
    if not models: raise Stop('aucun_modele_ollama_local')
    print('Modèles installés : '+', '.join(models))
    preferred = next((m for m in models if m=='qwen3.6:27b'),models[0])
    ollama['model'] = ask('Modèle local à utiliser',preferred)
    if ollama['model'] not in models: raise Stop('modele_non_installe')
    from .model import Model
    Model(ollama)
    c = {'version':1,'mode':'observe', 'memory':{'enabled':True,'retention_days':90,'max_messages_per_run':30,'max_examples':2}, 'allow_legal_clarification':True,'state_dir':'/var/lib/axiorhub-mail-agent',
         'matters_file':str(target.parent/'matters.json'),'mail':mail,'nextcloud':nc,
         'calendar':calendar,'ollama':ollama,'max_messages_per_run':8,
         'rag':{'enabled':True,'embedding_model':'qwen3-embedding:0.6b',
                'embedding_chunks_per_run':24,'timeout_seconds':180},
         'automation':{'health_enabled':True,'health_interval_minutes':30,
                       'sync_enabled':True,'sync_interval_minutes':30,
                       'reconcile_inbox_enabled':True,'reconcile_interval_minutes':15,
                       'classify_portfolio_enabled':True,'portfolio_interval_minutes':360,
                       'index_all_enabled':False,'index_interval_minutes':360,
                       'daily_digest_enabled':False,'daily_digest_hour':8},
         'portfolio':{'enabled':True,'active_days':180,'archive_days':730,
                      'bootstrap_months':18,'mail_batch_size':100,
                      'folder_batch_size':10,'reconcile_batch_size':200,'backlog_days':14},
         'nextcloud_documents':{**nc,'max_generated_file_bytes':20000000},
         'document_projects':{'enabled':True,'approval_minutes':60,
                              'destination_subfolder':'20_Actes_et_conclusions/90_AxiorHub_Brouillons',
                              'max_generated_file_bytes':20000000},
         'hearing':{'enabled':True,'approval_minutes':60,
                    'destination_subfolder':'60_Audiences/90_AxiorHub_Brouillons'},
         'word_legal':{'enabled':True,'approval_minutes':60,
                       'destination_subfolder':'20_Actes_et_conclusions/90_AxiorHub_Brouillons',
                       'tracked_author':'AxiorHub','clean_and_compared':True},
         'autonomy':{'enabled':True,'mail_monitor_interval_minutes':5,'mail_batch_size':20,
                     'automatic_document_previews_enabled':True,'document_control_enabled':True,
                     'automatic_internal_files_enabled':True,
                     'control_model':ollama['model'],'diligence_proposals_enabled':True,
                     'billing_proposals_enabled':True,'automatic_matter_confidence_min':80},
         'orchestrator':{'enabled':True,'mail_monitor_interval_minutes':5,'mail_batch_size':20,
                         'matter_confidence_min':85,'trigger_adapted_projects':True,
                         'automatic_mail_drafts_enabled':True,
                         'automatic_legal_projects_enabled':True,
                         'propose_client_replies':True,'propose_diligences':True,
                         'propose_billing':True,'single_notification':True},
         'production':{'enabled':True,'interval_minutes':5,'batch_size':20,
                       'auto_advance_playbooks':True,'retry_failed_drafts':True,
                       'max_automatic_attempts':3,'verify_remote_deliverables':True,
                       'verification_retry_limit':3,'useful_metrics_days':30,
                       'automatic_internal_and_reversible':True},
         'opinions':{'enabled':True,'max_authorities':30,'max_dossier_sources':30,
                     'providers':['openlegi','goodlegal','pappers'],
                     'require_verified_official_authorities':True,
                     'qualitative_calibration_only':True,
                     'numeric_probability_forbidden':True},
         'model_routing':{'fast_model':'','complex_model':ollama['model'],
                          'control_model':ollama['model'],'fast_temperature':0,
                          'complex_temperature':0,'control_temperature':0},
         'ai_providers':{},
         'lawve_extensions':{},
         'ai_gateway':{'roundcube_enabled':True,'audit_content':False},
         'hybrid_routing':{'mode':'local','external_provider':'openrouter','threshold':65,
                           'allowed_purposes':['assistant','legal_analysis','hearing','document_drafting','control'],
                           'external_client_data_approved':False,
                           'fallback_local_on_error':True,
                           'escalate_after_local_failure':True,
                           'anonymize_external':True,
                           'max_external_characters':120000,
                           'excluded_matters':[],
                           'benchmark_min_external_gain':5.0},
         'updates':{'channel':'stable','metadata_url':'','require_signature':True,
                    'minisign_public_key_file':'/etc/axiorhub-mail-agent/update-minisign.pub'},
         'cabinet_pilotage':{'enabled':True,'refresh_interval_minutes':30,
                             'continuous_tests_interval_minutes':60,
                             'workload_horizon_days':14,'daily_capacity_minutes':420,
                             'inactive_days':90,'meeting_horizon_days':30,
                             'unbilled_lookback_days':30,'group_max_items':50,
                             'batch_approval_minutes':30},
         'workstation':{'roundcube_url':'https://courriel.example.com/webmail/',
                        'roundcube_drafts_url':'https://courriel.example.com/webmail/?_task=mail&_mbox=INBOX.Drafts',
                        'openwebui_url':'https://ai.example.com/',
                        'nextcloud_url':nc['url'].rstrip('/')+'/index.php/apps/files/',
                        'onlyoffice_url':'https://myoffice.example.com/welcome/',
                        'invoice_ninja_url':''},
         'invoice_ninja':{'enabled':False,'base_url':'',
                          'api_token_file':'/etc/axiorhub-mail-agent/invoice-ninja-api-token',
                          'timeout_seconds':30,'max_pages':3,'read_only':True},
         'documents':{'ocr':True,'max_pdf_pages':60,'max_file_bytes':15000000,
                      'max_document_chars':100000,'max_documents_per_mail':5,
                      'index_updates_per_run':25,'inventory_page_files':250}}
    private_json(c['matters_file'], [])
    private_json(target,c)
    gid = grp.getgrnam('axiorhub-mail').gr_gid
    for p in [target.parent,secret_dir]: os.chown(p,0,gid); os.chmod(p,0o750)
    for p in [target,Path(c['matters_file']),*secret_dir.glob('*.secret')]: os.chown(p,0,gid); os.chmod(p,0o640)
    load_config(target)
    print('Configuration enregistrée. Mode OBSERVATION, aucun brouillon créé à cette étape.')


def save_admin(path, data):
    if os.geteuid() != 0: raise Stop('modification_configuration_necessite_sudo')
    private_json(path,data)
    if Path(path).is_symlink(): return   # 5.6.15 : pont runtime ; la cible garde son propriétaire (service) et ses droits
    os.chown(path,0,grp.getgrnam('axiorhub-mail').gr_gid)
    os.chmod(path,0o640)


def discover(c):
    dav = DAV(c['nextcloud'])
    rows = json.loads(Path(c['matters_file']).read_text(encoding='utf-8'))
    existing = {r['path'] for r in rows}
    count = 0
    for root in c['nextcloud']['roots']:
        for item in dav.list_folder(root):
            if not item['directory'] or item['path'] in existing: continue
            from .common import digest
            name = item['path'].rsplit('/',1)[-1]
            rows.append({'id':'DOS-'+digest(item['path'])[:10], 'client_name':name,'path':item['path'],
                         'aliases':[name],'references':[],'correspondents':[]})
            count += 1
    save_admin(c['matters_file'],rows)
    print(str(count)+' répertoires ajoutés au registre comme candidats. Aucune adresse client n’a été déduite.')


def link(c):
    rows = json.loads(Path(c['matters_file']).read_text(encoding='utf-8'))
    term = ask('Nom ou référence du dossier à rechercher').lower()
    selected = [r for r in rows if term in (r['client_name']+' '+r['id']+' '+r['path']).lower()]
    if not selected:
        path = ask('Chemin Nextcloud exact du dossier')
        DAV(c['nextcloud']).list_folder(path)
        m = {'id':ask('Référence unique du dossier'),'client_name':ask('Nom du client'),'path':path,
             'aliases':[],'references':[],'correspondents':[]}
        rows.append(m)
    else:
        for i,m in enumerate(selected,1): print(str(i)+'. '+m['id']+' — '+m['path'])
        try:
            num = int(ask('Numéro du dossier','1'))
            if num < 1: raise ValueError()
            m = selected[num-1]
        except (ValueError,IndexError): raise Stop('selection_dossier_invalide') from None
    email = ask('Adresse exacte du correspondant').lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+',email): raise Stop('adresse_invalide')
    role = ask('Rôle : client, confrere_adverse, tiers ou prospect','client')
    from .common import VALID_ROLES
    if role not in VALID_ROLES: raise Stop('role_invalide')
    m['correspondents'] = [p for p in m['correspondents'] if p['email']!=email] + [{'email':email,'role':role}]
    refs = split(ask('Références figurant dans les objets des mails, séparées par virgules'))
    m['references'] = list(dict.fromkeys(m.get('references',[])+refs))
    save_admin(c['matters_file'],rows)
    print('Correspondance enregistrée.')

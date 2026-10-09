"""Configuration graphique typée : valeurs applicatives, références de secrets, plan Docker.

Les paramètres de déploiement sont exportés pour revue et redémarrage, jamais
exécutés par le processus web. Aucun secret existant n'est renvoyé au navigateur.
"""
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import tempfile
from urllib.parse import urlsplit

from .common import Stop, load_config, private_json, read_secret

# clé, libellé, type, valeur initiale, groupe
FIELDS = (
 ('mail.host','Serveur IMAP','host','mail.example.com','Courriels'),
 ('mail.port','Port IMAP TLS','port',993,'Courriels'),
 ('mail.username','Identifiant IMAP','text','','Courriels'),
 ('mail.password_file','Mot de passe IMAP / d’application','secret','','Courriels'),
 ('mail.inbox','Boîte de réception','text','INBOX','Courriels'),
 ('mail.drafts','Dossier Brouillons','text','INBOX.Drafts','Courriels'),
 ('mail.sent','Dossier Envoyés','text','INBOX.Sent','Courriels'),
 ('mail.from_address','Adresse du cabinet','email','','Courriels'),
 ('mail.from_name','Nom de l’expéditeur','text','Cabinet','Courriels'),
 ('mail.own_addresses','Autres adresses du cabinet (une par ligne)','lines',[],'Courriels'),
 ('orchestrator.lookback_days','Orchestrateur : antériorité maximale des courriels examinés (jours)','integer',14,'Courriels'),
 ('autonomy.lookback_days','Autonomie : antériorité maximale des courriels examinés (jours)','integer',14,'Courriels'),
 ('nextcloud.url','URL Nextcloud HTTPS','url','https://nextcloud.example.com','Nextcloud'),
 ('nextcloud.username','Identifiant Nextcloud','text','','Nextcloud'),
 ('nextcloud.password_file','Mot de passe d’application Nextcloud','secret','','Nextcloud'),
 ('nextcloud.roots','Racines autorisées (une par ligne)','paths',['/Dossiers'],'Nextcloud'),
 ('nextcloud.matter_roots','Racines des dossiers','paths',['/Dossiers'],'Nextcloud'),
 ('calendar.urls','Agendas autorisés (URLs, une par ligne)','urls',[],'Nextcloud'),
 ('calendar.task_urls','Listes de tâches autorisées','urls',[],'Nextcloud'),
 ('document_agents568.incoming_paths','Dossiers d’arrivée à surveiller, dans les racines autorisées','paths',[],'Agents documentaires'),
 ('google_calendar568.client_id','Client OAuth Google Calendar','text','','Google Calendar'),
 ('google_calendar568.client_secret_file','Secret OAuth Google Calendar','secret','','Google Calendar'),
 ('google_calendar568.redirect_uri','URI de retour OAuth HTTPS (/api440/m568/google/callback)','url','https://agent.example.com/api440/m568/google/callback','Google Calendar'),
 ('ollama.url','Adresse Ollama (locale ou réseau du cabinet, ex. http://192.168.1.10:11434)','local_url','http://127.0.0.1:11434','Intelligence artificielle'),
 ('ollama.model','Modèle Ollama','text','qwen3:8b','Intelligence artificielle'),
 ('ollama.num_ctx','Fenêtre de contexte (tokens)','integer',32768,'Intelligence artificielle'),
 ('audio.enabled','Activer la dictée locale','bool',False,'Voix'),
 ('audio.bridge_base_url','Passerelle Whisper / Vocal (locale ou réseau du cabinet)','local_url','http://127.0.0.1:9011','Voix'),
 ('audio.bridge_token_file','Jeton de la passerelle vocale','secret','','Voix'),
 ('speech568.provider','Synthèse vocale : espeak, kokoro, chatterbox ou elevenlabs','voice_provider','espeak','Synthèse vocale'),
 ('speech568.url','API TTS Kokoro / Chatterbox (locale ou réseau du cabinet, base sans /v1)','local_url','http://127.0.0.1:8880','Synthèse vocale'),
 ('speech568.voice','Identifiant de la voix (ff_siwis pour Kokoro français)','text','ff_siwis','Synthèse vocale'),
 ('speech568.model','Modèle TTS (kokoro ou eleven_multilingual_v2 par exemple)','text','kokoro','Synthèse vocale'),
 ('speech568.api_key_file','Clé API TTS / ElevenLabs','secret','','Synthèse vocale'),
 ('speech568.external_allowed','Autoriser ElevenLabs sous la politique externe globale','bool',False,'Synthèse vocale'),
 ('speech568.fallback_local','Revenir à eSpeak NG si la synthèse échoue ou est refusée','bool',True,'Synthèse vocale'),
 ('speech568.usd_per_1000_chars','Prix plafond en USD pour 1 000 caractères','money',0,'Synthèse vocale'),
 ('speech568.per_request_usd','Plafond USD par lecture externe','money',0,'Synthèse vocale'),
 ('speech568.monthly_usd','Budget mensuel vocal externe USD, partagé au cabinet','money',0,'Synthèse vocale'),
 ('invoice_ninja.enabled','Activer Invoice Ninja','bool',False,'Invoice Ninja'),
 ('invoice_ninja.base_url','URL racine Invoice Ninja HTTPS','url','https://invoice.example.com','Invoice Ninja'),
 ('invoice_ninja.write_enabled','Autoriser la création de factures en brouillon et la transmission des temps validés (après votre validation ; jamais d’envoi au client)','bool',False,'Invoice Ninja'),
 ('invoice_ninja.api_token_file','Jeton de société X-API-TOKEN','secret','','Invoice Ninja'),
 ('invoice_ninja.api_secret_file','API_SECRET facultatif de login (pas APP_KEY)','secret','','Invoice Ninja'),
 ('invoice_ninja.company_id','Identifiant de société','text','','Invoice Ninja'),
 ('invoice_ninja.max_pages','Plafond de pages par synchronisation','integer',30,'Invoice Ninja'),
 ('invoice_ninja.timeout_seconds','Délai maximum des lectures (secondes)','integer',30,'Invoice Ninja'),
 ('smtp.enabled','Activer SMTP (envoi toujours explicite)','bool',False,'SMTP'),
 ('smtp.host','Serveur SMTP','host','mail.example.com','SMTP'),
 ('smtp.port','Port SMTP','port',587,'SMTP'),
 ('smtp.security','Sécurité SMTP : starttls ou ssl','smtp_security','starttls','SMTP'),
 ('smtp.username','Identifiant SMTP','text','','SMTP'),
 ('smtp.password_file','Mot de passe SMTP','secret','','SMTP'),
 ('smtp.from_address','Adresse d’envoi SMTP','email','','SMTP'),
 ('interfaces.openwebui','URL Open WebUI','url','https://ai.example.com','Interfaces'),
 ('interfaces.roundcube','URL Roundcube','url','https://mail.example.com/webmail/','Interfaces'),
 ('reception.administrative_scope_approved','Autoriser uniquement l’accueil administratif entrant','bool',False,'Accueil administratif'),
 ('reception.telephone_enabled','Activer Twilio Voice entrant','bool',False,'Accueil administratif'),
 ('reception.telephone_speech_enabled','Accueil conversationnel : l’appelant parle (motifs fermés, deux questions au plus)','bool',False,'Accueil administratif'),
 ('reception.speech_external_approved','J’accepte que la voix de l’appelant soit transcrite par Twilio (service externe payant, ≈ 0,02 $ par 15 s)','bool',False,'Accueil administratif'),
 ('reception.speech_notice','Annonce lue à l’appelant avant la reconnaissance vocale (IA, transcription Twilio, aucun enregistrement, aucun conseil juridique, touche 0 pour le clavier) — texte sous votre responsabilité','lines',[],'Accueil administratif'),
 ('reception.twilio_callback_url','URL HTTPS exacte du webhook /reception567/twilio','url','https://agent.example.com/reception567/twilio','Accueil administratif'),
 ('reception.twilio_auth_token_file','Auth Token Twilio pour vérifier la signature','secret','','Accueil administratif'),
 ('reception.whatsapp_enabled','Activer les messages WhatsApp Business Cloud entrants','bool',False,'Accueil administratif'),
 ('reception.whatsapp_phone_number_id','Identifiant Meta du numéro Business','text','','Accueil administratif'),
 ('reception.whatsapp_app_secret_file','Secret de l’application Meta (signature)','secret','','Accueil administratif'),
 ('reception.whatsapp_verify_token_file','Jeton de vérification du webhook Meta','secret','','Accueil administratif'),
 ('reception.retention_days','Conservation des messages entrants en jours (90 max.)','integer',30,'Accueil administratif'),
)
INDEX = {row[0]: row for row in FIELDS}
SECRET_NAMES = re.compile(r'(PASSWORD|TOKEN|SECRET|API_KEY|APP_KEY|BOOTSTRAP)', re.I)


def _read_text(path):
    """5.6.9 : fichiers de configuration lus en UTF-8 ; un fichier hérité dans l'encodage du système reste lisible."""
    raw=Path(path).read_bytes()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        import locale
        return raw.decode(locale.getpreferredencoding(False),errors='replace')


def _get(cfg,key,default=None):
    value=cfg
    for part in key.split('.'):
        if not isinstance(value,dict) or part not in value:return default
        value=value[part]
    return value


def _set(cfg,key,value):
    parts=key.split('.');node=cfg
    for part in parts[:-1]:node=node.setdefault(part,{})
    node[parts[-1]]=value


def _path(env):
    raw=env.get('axiorhub.config_path')
    if not raw:raise Stop('chemin_configuration_interne_absent')
    path=Path(raw)
    if path.is_symlink():
        # Seul le pont root de l’installation systemd est accepté. Le service
        # peut remplacer sa configuration dans /var/lib, jamais les fichiers /etc.
        target=path.resolve();parent=path.parent.stat()
        if path.lstat().st_uid!=0 or parent.st_uid!=0 or parent.st_mode & 0o022 or target.name!='config.json' or target.parent.name!='configuration567':
            raise Stop('configuration_lien_symbolique_refuse')
        cfg=json.loads(_read_text(target))
        if target != Path(cfg['state_dir']).resolve()/'configuration567'/'config.json':
            raise Stop('configuration_lien_symbolique_refuse')
        path=target
    return path


def catalog(desk,env):
    cfg=json.loads(_read_text(_path(env)))
    rows=[]
    for key,label,kind,default,group in FIELDS:
        value=_get(cfg,key,default)
        secret=kind=='secret'
        rows.append({'key':key,'label':label,'type':kind,'group':group,'value':'' if secret else value,
                     'secret_configured':bool(value and Path(str(value)).is_file()) if secret else False,
                     'source':'config.json','restart_required':False})
    return {'revision':int(cfg.get('config_revision567',0)),'fields':rows,
            'infrastructure':infrastructure(),'api_secrets_returned':False}


def infrastructure():
    """Inventaire complet des variables distribuées, sans lire les secrets du serveur."""
    root=Path(__file__).resolve().parents[1];values={}
    for filename in ('.env.example','deploy/vps/env.example'):
        path=root/filename
        if not path.is_file():continue
        for line in _read_text(path).splitlines():
            m=re.match(r'^([A-Z][A-Z0-9_]*)=(.*)$',line)
            if not m:continue
            key,default=m.groups();secret=bool(SECRET_NAMES.search(key))
            values[key]={'name':key,'example':'' if secret else default,'secret':secret,
                         'source':filename,'restart_required':True,
                         'configured':bool(os.environ.get(key)) if secret else None}
    return [values[k] for k in sorted(values)]


def docker_plan(data):
    values=data.get('values') or {}
    known={r['name']:r for r in infrastructure()}
    if not isinstance(values,dict) or any(k not in known for k in values):raise Stop('variable_docker_inconnue')
    lines=['# Plan AxiorHub 5.6.8 à relire. Aucune commande n’a été exécutée.',
           '# Secrets à configurer dans le coffre ou avec des fichiers montés privés.']
    for key,row in sorted(known.items()):
        value='__SECRET_A_CONFIGURER__' if row['secret'] else str(values.get(key,row['example']))
        if len(value)>1000 or any(c in value for c in ('\n','\r','\0')):raise Stop('valeur_docker_invalide')
        if key.endswith('_PORT'):
            try:
                if not 1<=int(value)<=65535:raise ValueError()
            except ValueError:raise Stop('port_docker_invalide') from None
        lines.append(key+'='+shlex.quote(value))
    return {'env_example':'\n'.join(lines)+'\n','applied':False,'restart_required':True,
            'steps':['Sauvegarder le volume et les clés du coffre','Relire le plan et les ports/volumes du Compose','Appliquer la configuration dans le dossier de déploiement','Redémarrer les services concernés et contrôler leur état']}


def _validate(kind,value,key):
    if kind=='voice_provider':
        if value not in ('espeak','kokoro','chatterbox','elevenlabs'):raise Stop('fournisseur_voix_invalide')
        return value
    if kind=='money':
        from .budget567 import money
        amount=money(value)
        if amount>10000:raise Stop('budget_voix_invalide')
        return str(amount)
    if kind=='bool':
        if not isinstance(value,bool):raise Stop('reglage_booleen_invalide')
        return value
    if kind in ('port','integer'):
        try:
            if isinstance(value,bool):raise ValueError()
            n=int(value)
            lo,hi=(1,65535) if kind=='port' else ((2048,262144) if key=='ollama.num_ctx' else (1,90 if key=='reception.retention_days' else (100 if key.endswith('max_pages') else 300)))
            if not lo<=n<=hi:raise ValueError()
            return n
        except (TypeError,ValueError):raise Stop('reglage_nombre_invalide') from None
    if kind in ('lines','paths','urls'):
        values=value.splitlines() if isinstance(value,str) else value
        if not isinstance(values,list) or len(values)>100:raise Stop('reglage_liste_invalide')
        out=[str(v).strip() for v in values if str(v).strip()]
        if kind=='paths':
            from .common import clean_path
            out=[clean_path(v) for v in out]
        elif kind=='urls':out=[_validate('url',v,key) for v in out]
        return list(dict.fromkeys(out))
    value=str(value).strip()
    if len(value)>600 or any(ord(c)<32 for c in value):raise Stop('reglage_texte_invalide')
    if kind=='host' and (not value or any(c in value for c in '/@?# ')):raise Stop('hote_connexion_invalide')
    if kind=='email' and value and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+',value):raise Stop('adresse_connexion_invalide')
    if kind=='smtp_security' and value not in ('starttls','ssl'):raise Stop('smtp_securite_invalide')
    if kind in ('url','local_url'):
        p=urlsplit(value)
        if not p.netloc or p.username or p.password or p.query or p.fragment:raise Stop('url_connexion_invalide')
        if kind=='url' and p.scheme!='https':raise Stop('https_obligatoire')
        if kind=='local_url':
            from .common import HTTP
            hosts=('ollama','vocal','whisper','kokoro','chatterbox')
            HTTP(value,local_only=True,local_hosts=hosts,timeout=5)
        value=value.rstrip('/')
    return value


def _save(desk,data,env):
    path=_path(env);incoming=data.get('values') or {}
    if not isinstance(incoming,dict) or any(k not in INDEX for k in incoming):raise Stop('champ_configuration_inconnu')
    if not incoming:raise Stop('configuration_vide')
    with path.with_suffix('.lock').open('a') as lock:
        os.chmod(path.with_suffix('.lock'),0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        current=json.loads(_read_text(path));revision=int(current.get('config_revision567',0))
        if int(data.get('revision',-1))!=revision:raise Stop('configuration_modifiee_recharger')
        candidate=deepcopy(current);secrets_to_write=[]
        for key,value in incoming.items():
            kind=INDEX[key][2]
            if kind=='secret':
                if value:
                    value=str(value).strip()
                    if len(value)>4096 or any(c in value for c in ('\n','\r','\0')):raise Stop('secret_invalide')
                    secrets_to_write.append((key,value))
                continue
            _set(candidate,key,_validate(kind,value,key))
        if 'ollama.model' in incoming:
            old_model=current.get('ollama',{}).get('model')
            for role in ('fast','complex','control'):
                if candidate.get('model_routing',{}).get(role+'_model')==old_model:
                    candidate['model_routing'][role+'_model']=candidate['ollama']['model']
        if _get(candidate,'mail.drafts') in (_get(candidate,'mail.inbox'),_get(candidate,'mail.sent')):raise Stop('dossiers_imap_confondus')
        own=_get(candidate,'mail.own_addresses',[]) or []
        if _get(candidate,'mail.from_address') not in own and _get(candidate,'mail.from_address'):own.append(_get(candidate,'mail.from_address'))
        for address in own:_validate('email',address,'mail.own_addresses')
        _set(candidate,'mail.own_addresses',own)
        for name,urlkey in (('ollama','url'),('audio','bridge_base_url')):
            host=urlsplit(candidate.get(name,{}).get(urlkey,'')).hostname
            if host in ('ollama','vocal','whisper'):candidate[name]['local_hosts']=[host]
        candidate['config_revision567']=revision+1
        candidate.setdefault('installation',{})['tests567']={}
        # Validation structurelle avant toute rotation de secret.
        fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.config-candidate-')
        try:
            with os.fdopen(fd,'w') as stream:json.dump(candidate,stream)
            load_config(temp)
        finally:os.unlink(temp)
        from .vault567 import write
        for key,value in secrets_to_write:
            import secrets
            secret=Path(desk.c['state_dir'])/'vault'/'connectors567'/(key.replace('.','-')+'-'+secrets.token_hex(8)+'.secret')
            _set(candidate,key,write(secret,value))
        stat=path.stat()
        fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.config-applied-')
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump(candidate,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.chmod(temp,stat.st_mode & 0o777)
            if os.geteuid()==0:os.chown(temp,stat.st_uid,stat.st_gid)
            os.replace(temp,path)
        finally:
            if os.path.exists(temp):os.unlink(temp)
    desk.audit('configuration567_appliquee',{'revision':revision+1,'fields':sorted(incoming),'secrets_replaced':len(secrets_to_write)})
    return {'revision':revision+1,'applied':True,'restart_required':False,'message':'Réglages enregistrés. Les nouveaux traitements les reprendront ; les traitements en cours conservent leur instantané.'}


def save(desk,data,env):
    """5.6.15 : le service confiné (ProtectSystem=strict) devant un fichier ordinaire dans /etc, ou une cible appartenant
    à root, reçoit un refus explicite ; la commande de réparation est donnée par le message (install-interface.py)."""
    try:
        return _save(desk,data,env)
    except PermissionError:
        raise Stop('configuration_non_inscriptible') from None
    except OSError as error:
        if error.errno in (errno.EROFS,errno.EACCES,errno.EPERM):raise Stop('configuration_non_inscriptible') from None
        raise


def test(desk,data):
    kind=str(data.get('connector') or '')
    try:
        if kind=='imap':
            from .web520 import check_imap
            out=check_imap(desk)
            return {'ok':True,'connector':kind,'message':out['message'],'writes':0}
        if kind=='nextcloud':
            from .dav import DAV
            c=deepcopy(desk.c['nextcloud']);client=DAV(c);client.http.timeout=10
            items=client.list_folder(c['roots'][0]);calendars=client.calendars()
            return {'ok':True,'connector':kind,'message':'Nextcloud lu ; aucun agenda n’a été sélectionné automatiquement.',
                    'entries':len(items),'calendars':calendars,'writes':0}
        if kind=='ollama':
            from .common import HTTP
            cfg=desk.c['ollama'];client=HTTP(cfg['url'],local_only=True,local_hosts=cfg.get('local_hosts',()),timeout=8)
            payload=client.json('GET','/api/tags');names=[m.get('name','') for m in payload.get('models',[])]
            if cfg['model'] not in names:raise Stop('modele_ollama_selectionne_non_installe')
            return {'ok':True,'connector':kind,'message':'Modèle local installé et serveur joignable.','models':names,'writes':0}
        if kind=='invoice_ninja':
            from .workstation import refresh_unpaid_invoices
            out=refresh_unpaid_invoices(desk)
            return {'ok':bool(out.get('complete',True)),'connector':kind,'message':'Factures lues ; aucune facture créée ni envoyée.','result':out,'writes':0}
        if kind=='voice':
            import shutil
            return {'ok':bool(shutil.which('espeak-ng')),'connector':kind,'message':'Synthèse locale disponible.' if shutil.which('espeak-ng') else 'Installer espeak-ng (présent dans l’image 5.6.8).','dictation_configured':bool(desk.c.get('audio',{}).get('enabled')),'writes':0}
        raise Stop('connecteur_test_inconnu')
    except (Stop,OSError,ValueError) as error:
        code=str(error) if isinstance(error,Stop) else 'connexion_indisponible'
        return {'ok':False,'connector':kind,'code':code,'message':'Test non validé : '+code.replace('_',' ')+'. Vérifiez l’adresse, les autorisations et le service.','writes':0}


def handle(desk,name,data,env,method):
    if name=='m567/config/catalog' and method=='GET':return catalog(desk,env)
    if name=='m567/config/save':return save(desk,data,env)
    if name=='m567/config/test':return test(desk,data)
    if name=='m567/config/docker-plan':return docker_plan(data)
    if name=='m567/config/export' and method=='GET':
        c=catalog(desk,env)
        return {'schema':'axiorhub.config567.redacted.v1','revision':c['revision'],
                'values':{r['key']:r['value'] for r in c['fields'] if r['type']!='secret'},'secrets_exported':False}
    raise Stop('route_inconnue')

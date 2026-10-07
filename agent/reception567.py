"""Accueil administratif signé : Twilio Voice (touches ou conversation) et WhatsApp Cloud entrant.

Aucun accès aux dossiers, aucun LLM, aucun téléchargement audio, aucun envoi
WhatsApp. Le numéro présenté sert au rappel ; il ne prouve jamais une identité.
5.6.9 : accueil conversationnel administratif. L'appelant parle ; la reconnaissance vocale de Twilio (service externe, activé et
accepté explicitement) transcrit ; un motif fermé est reconnu par mots-clés (rappel, document, rendez-vous, message) ; deux questions
au plus ; une tâche est enregistrée. Aucun conseil juridique, aucune information de dossier, aucune réponse improvisée.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qs, parse_qsl
from xml.sax.saxutils import escape

from .common import Stop, fold, read_secret

SCHEMA='''CREATE TABLE IF NOT EXISTS reception_v567(
 id TEXT PRIMARY KEY, channel TEXT NOT NULL, at TEXT NOT NULL, caller TEXT NOT NULL,
 text TEXT NOT NULL, task_id TEXT NOT NULL, provider_reference TEXT NOT NULL);
'''
ANNOUNCEMENT="Vous êtes en relation avec l’accueil automatique du cabinet, un assistant informatique. Aucun conseil juridique ni détail de dossier ne sera communiqué. Pour demander un rappel administratif, tapez 1. Pour signaler un document à transmettre, tapez 2."


SPEECH_ANNOUNCEMENT=("Vous êtes en relation avec l’accueil automatique du cabinet, un assistant informatique. Aucun conseil juridique ni "
 "information sur un dossier ne sera donné. Dites en quelques mots l’objet de votre appel : un rappel, un document à transmettre, un "
 "rendez-vous ou un message. Vous pouvez aussi taper 1 pour un rappel ou 2 pour un document.")
# 5.6.10 : annonce d'information (règlement IA, art. 50 ; RGPD) lue avant toute reconnaissance vocale ; modifiable par le cabinet.
DEFAULT_NOTICE=("Cet accueil est automatisé et utilise une intelligence artificielle. Vos paroles sont transcrites par un prestataire, "
 "Twilio ; le cabinet ne conserve aucun enregistrement audio. Aucun conseil juridique n’est donné par téléphone. Pour refuser, "
 "utilisez le clavier et tapez 0. Pour parler à une personne, rappelez aux horaires d’ouverture.")
SPEECH_PROMPT="Sinon, dites en quelques mots l’objet de votre appel : un rappel, un document à transmettre, un rendez-vous ou un message."
HINTS='rappel, rappeler, document, pièce, transmettre, rendez-vous, message, urgent'


def notice(cfg):
    """Texte de l'annonce : réglage reception.speech_notice (lignes jointes) ou texte par défaut."""
    value=cfg.get('speech_notice')
    if isinstance(value,list):value=' '.join(str(x).strip() for x in value if str(x).strip())
    value=str(value or '').strip()
    return value[:1500] if value else DEFAULT_NOTICE


def _keypad(callback):
    return ('<Response><Gather input="dtmf" numDigits="1" timeout="8" action="'+escape(callback,{'"':'&quot;'})+'" method="POST"><Say language="fr-FR">'
            +ANNOUNCEMENT+'</Say></Gather><Say language="fr-FR">Sans choix, veuillez rappeler le cabinet pendant ses horaires d’ouverture.</Say><Hangup/></Response>')
INTENTS={'callback':('rappel','rappeler','rappelle','joindre','contacter','appeler','telephone'),
         'document':('document','piece','pieces','transmettre','envoyer','envoi','courrier','justificatif','attestation'),
         'appointment':('rendez','rdv','rencontre','consultation','disponibilit','horaire')}
INTENT_LABELS={'callback':'Demande de rappel','document':'Document à transmettre','appointment':'Demande de rendez-vous','message':'Message à transmettre'}


def classify(text,digits=''):
    """Motif fermé par mots-clés ; tout le reste est un message à transmettre. Aucun modèle, aucune interprétation libre."""
    if digits=='1':return 'callback'
    if digits=='2':return 'document'
    said=fold(text or '')
    for intent,words in INTENTS.items():
        if any(w in said for w in words):return intent
    return 'message'


def speech_enabled(cfg):
    return bool(cfg.get('telephone_speech_enabled')) and bool(cfg.get('speech_external_approved'))


def _gather(prompt,action,hints='',announcement=''):
    """5.6.11 : l'annonce d'information est lue hors du Gather, donc entièrement, avant que la parole ou le clavier soient acceptés."""
    return ('<Response>'+(('<Say language="fr-FR">'+escape(announcement)+'</Say>') if announcement else '')+
            '<Gather input="speech dtmf" language="fr-FR" speechTimeout="auto" numDigits="1" timeout="6"'+(' hints="'+escape(hints,{'"':'&quot;'})+'"' if hints else '')+
            ' action="'+escape(action,{'"':'&quot;'})+'" method="POST"><Say language="fr-FR">'+escape(prompt)+'</Say></Gather>'
            '<Say language="fr-FR">Je n’ai rien entendu. Merci de rappeler le cabinet pendant ses horaires d’ouverture.</Say><Hangup/></Response>')


def _conversation(desk,cfg,callback,query,values):
    params=dict(parse_qsl(query)) if query else {};step=params.get('step','0')
    said=str(values.get('SpeechResult') or '').strip()[:500];digits=str(values.get('Digits') or '').strip()
    if step=='0':
        if 'Digits' in values:return None          # suite de l'accueil par touches (touche 0 choisie) : flux classique
        return _reply('application/xml',_gather(SPEECH_PROMPT,callback+'?step=1',HINTS,announcement=notice(cfg)))
    if step=='1':
        if digits=='0':return _reply('application/xml',_keypad(callback))   # refus de la reconnaissance vocale
        if not said and not digits:
            return _reply('application/xml',_gather('Je n’ai pas compris. Dites simplement : rappel, document, rendez-vous ou message.',callback+'?step=1',HINTS))
        intent=classify(said,digits)
        return _reply('application/xml',_gather('Compris : '+INTENT_LABELS[intent].lower()+'. Indiquez maintenant votre nom et, si vous le souhaitez, '
            'la référence ou le nom du dossier concerné, puis patientez.',callback+'?step=2&intent='+intent))
    if digits=='0':return _reply('application/xml',_keypad(callback))   # refus possible à chaque étape
    intent=params.get('intent','message');intent=intent if intent in INTENT_LABELS else 'message'
    urgent='urgen' in fold(said)
    label=INTENT_LABELS[intent]+(' — signalé urgent' if urgent else '')
    text=label+(' — propos de l’appelant (accueil automatisé avec IA ; transcription Twilio, non vérifiée) : « '+said+' »' if said else ' — sans précision (accueil automatisé avec IA).')
    out=_record(desk,'telephone',values.get('CallSid',''),values.get('From',''),text,label=label)
    return _reply('application/xml','<Response><Say language="fr-FR">Merci. Votre demande est enregistrée et sera examinée par le cabinet. '
                  'Aucun délai n’est garanti et aucun conseil juridique n’est donné par cet accueil. Au revoir.</Say><Hangup/></Response>')


def capabilities(desk):
    cfg=desk.c.get('reception',{})
    def ready(channel, fields):
        if not cfg.get('administrative_scope_approved') or not cfg.get(channel+'_enabled'):
            return False
        try:
            return all(bool(read_secret(cfg.get(field,''))) for field in fields)
        except (Stop,OSError):
            return False
    return {'telephone':ready('telephone',('twilio_auth_token_file',)) and bool(cfg.get('twilio_callback_url')),
            'whatsapp':ready('whatsapp',('whatsapp_app_secret_file','whatsapp_verify_token_file')) and bool(cfg.get('whatsapp_phone_number_id')),
            'whatsapp_outbound':False, 'phone_conversation':'administrative_speech' if speech_enabled(cfg) else 'administrative_dtmf_only',
            'speech_external':speech_enabled(cfg),
            'provider_end_to_end_tested':False}


def _reply(kind,body,status='200 OK'):
    return {'kind':kind,'body':body,'status':status,'csp':"default-src 'none'"}


def _record(desk,channel,reference,caller,text,label=''):
    if not re.fullmatch(r'[A-Za-z0-9._:+/=-]{8,256}',reference):raise Stop('reference_accueil_invalide')
    if not re.fullmatch(r'\+?[0-9]{6,20}',caller):raise Stop('numero_accueil_invalide')
    if len(text)>10000:raise Stop('message_accueil_trop_long')
    ident=hashlib.sha256((channel+'|'+reference).encode()).hexdigest()
    task=ident[:32]
    desk.db.execute('BEGIN IMMEDIATE')
    try:
        old=desk.db.execute('SELECT task_id FROM reception_v567 WHERE id=?',(ident,)).fetchone()
        if old:
            desk.db.commit();return {'task_id':old[0],'duplicate':True}
        title=(label or ('Rappel administratif' if channel=='telephone' else 'Message WhatsApp à traiter'))+' — '+caller
        desk.db.execute('INSERT INTO tasks(id,matter,title,due,status,created) VALUES (?,?,?,?,?,?)',(task,'',title,desk.now()[:10],'open',desk.now()))
        desk.db.execute('INSERT INTO reception_v567 VALUES(?,?,?,?,?,?,?)',(ident,channel,desk.now(),caller,text,task,reference))
        desk.db.commit()
    except BaseException:
        desk.db.rollback();raise
    proof=desk.db.execute('SELECT id,status FROM tasks WHERE id=?',(task,)).fetchone()
    if not proof or proof['status']!='open':raise Stop('tache_accueil_non_verifiee')
    from .live430 import emit
    emit(desk,'produced','Demande administrative reçue ; tâche de rappel enregistrée et relue.')
    desk.audit('accueil567_prepare',{'channel':channel,'task_id':task,'external_writes':0,'identity_verified':False})
    return {'task_id':task,'duplicate':False}


def webhook(desk,env,path):
    cfg=desk.c.get('reception',{})
    channel='telephone' if path.endswith('/twilio') else 'whatsapp'
    if not cfg.get(channel+'_enabled') or not cfg.get('administrative_scope_approved'):
        return _reply('application/json',json.dumps({'error':'accueil_non_active'}),'503 Service Unavailable')
    desk.db.executescript(SCHEMA)
    method=env.get('REQUEST_METHOD','GET')
    if channel=='whatsapp' and method=='GET':
        q=parse_qs(env.get('QUERY_STRING',''),max_num_fields=10)
        token=read_secret(cfg['whatsapp_verify_token_file'])
        challenge=q.get('hub.challenge',[''])[0]
        if q.get('hub.mode')!=['subscribe'] or not hmac.compare_digest(q.get('hub.verify_token',[''])[0],token) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',challenge):
            raise Stop('signature_accueil_refusee')
        return _reply('text/plain',challenge)
    if method!='POST':raise Stop('methode_refusee')
    try:length=int(env.get('CONTENT_LENGTH','0'))
    except ValueError:raise Stop('requete_accueil_invalide') from None
    if not 1<=length<=100000:raise Stop('requete_accueil_trop_grande')
    raw=env['wsgi.input'].read(length)
    if len(raw)!=length:raise Stop('requete_accueil_incomplete')
    if channel=='telephone':
        if env.get('CONTENT_TYPE','').split(';')[0]!='application/x-www-form-urlencoded':raise Stop('format_accueil_invalide')
        values=parse_qs(raw.decode('utf-8'),keep_blank_values=True,max_num_fields=100)
        if any(len(v)!=1 for v in values.values()):raise Stop('requete_accueil_invalide')
        values={k:v[0] for k,v in values.items()}
        callback=str(cfg.get('twilio_callback_url') or '');query=env.get('QUERY_STRING','')
        # 5.6.9 : l'accueil conversationnel enchaîne deux étapes (?step=…) ; Twilio signe l'adresse complète, paramètres compris.
        if not callback.startswith('https://') or (query and not re.fullmatch(r'step=[12](&intent=[a-z_]{1,20})?',query)):raise Stop('url_accueil_invalide')
        payload=callback+('?'+query if query else '')+''.join(k+values[k] for k in sorted(values))
        signature=base64.b64encode(hmac.new(read_secret(cfg['twilio_auth_token_file']).encode(),payload.encode(),hashlib.sha1).digest()).decode()
        if not hmac.compare_digest(env.get('HTTP_X_TWILIO_SIGNATURE',''),signature):raise Stop('signature_accueil_refusee')
        if speech_enabled(cfg):
            out=_conversation(desk,cfg,callback,query,values)
            if out is not None:return out
        if 'Digits' not in values:
            return _reply('application/xml',_keypad(callback))
        digits=values['Digits']
        if digits not in ('1','2'):
            return _reply('application/xml','<Response><Say language="fr-FR">Cette demande nécessite l’accueil humain du cabinet. Merci de rappeler pendant les horaires d’ouverture.</Say><Hangup/></Response>')
        _record(desk,'telephone',values.get('CallSid',''),values.get('From',''),'Rappel administratif demandé.' if digits=='1' else 'Demande administrative relative à la transmission d’un document. Aucun document consulté.')
        return _reply('application/xml','<Response><Say language="fr-FR">Votre demande de rappel administratif a été enregistrée. Elle sera examinée par le cabinet. Aucun délai de réponse n’est garanti.</Say><Hangup/></Response>')
    expected='sha256='+hmac.new(read_secret(cfg['whatsapp_app_secret_file']).encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(env.get('HTTP_X_HUB_SIGNATURE_256',''),expected):raise Stop('signature_accueil_refusee')
    if env.get('CONTENT_TYPE','').split(';')[0]!='application/json':raise Stop('format_accueil_invalide')
    data=json.loads(raw)
    if data.get('object')!='whatsapp_business_account':raise Stop('compte_whatsapp_invalide')
    count=0
    for entry in data.get('entry',[])[:10]:
        for change in entry.get('changes',[])[:10]:
            value=change.get('value') or {}
            if change.get('field')!='messages':continue
            if str((value.get('metadata') or {}).get('phone_number_id'))!=str(cfg.get('whatsapp_phone_number_id')):raise Stop('numero_whatsapp_non_autorise')
            messages=value.get('messages') or []
            if len(messages)>20:raise Stop('lot_accueil_trop_grand')
            for message in messages:
                text=str((message.get('text') or {}).get('body') or '') if message.get('type')=='text' else 'Message non textuel reçu. À consulter dans WhatsApp Business ; aucun média téléchargé par AxiorHub.'
                _record(desk,'whatsapp',str(message.get('id') or ''),str(message.get('from') or ''),text);count+=1
    return _reply('application/json',json.dumps({'received':count,'external_messages_sent':0}))


def purge(desk):
    """5.6.11 : conservation appliquée aux messages ET au numéro figurant dans le titre des tâches, chaque jour (pas seulement à l'ouverture)."""
    desk.db.executescript(SCHEMA)
    retention=max(1,min(int(desk.c.get('reception',{}).get('retention_days',30)),90))
    cutoff=(datetime.now(timezone.utc)-timedelta(days=retention)).isoformat()
    rows=desk.db.execute("SELECT id,task_id,caller FROM reception_v567 WHERE at<? AND caller<>''",(cutoff,)).fetchall()
    for r in rows:
        desk.db.execute("UPDATE tasks SET title=replace(title,?,'numéro purgé') WHERE id=?",(r['caller'],r['task_id']))
    desk.db.execute("UPDATE reception_v567 SET caller='',text='',provider_reference='' WHERE at<?",(cutoff,));desk.db.commit()
    return len(rows)


def listing(desk):
    purge(desk)
    return [dict(r) for r in desk.db.execute('SELECT * FROM reception_v567 ORDER BY at DESC LIMIT 50')]

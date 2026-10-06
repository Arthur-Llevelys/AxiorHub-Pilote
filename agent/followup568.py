"""Réponses aux engagements et relances neutres, déposées puis relues dans IMAP.

Une réponse au fil suspend le suivi ; elle ne prouve pas la présence de toutes
les pièces. Les relances automatiques n'ajoutent ni conseil ni information juridique.
"""
from datetime import datetime,timedelta,timezone
from email.message import EmailMessage
from email.utils import formataddr
import json
import fcntl
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import Stop,private_json
from .mailbox import Mailbox,addresses,ids
from . import settings568,proactive568


def scan(desk,owner='cabinet',box=None):
    p=settings568.profile(desk,owner)
    if not p['enabled'] or 'A1' not in p['roles']:return {'abstention':'Suivi des réponses désactivé.'}
    settings568.check_owner(desk,owner)
    cfg=desk.c['mail'];account=cfg['host']+'|'+cfg['username'];client=box or Mailbox(cfg)
    key='proactive568:inbox_cursor:'+proactive568.ident(owner,account,cfg['inbox'])
    try:
        validity=client.select(cfg['inbox']);cursor=desk.settings(key,{})
        last=int(cursor.get('uid',0)) if cursor.get('validity')==validity else 0
        since=(datetime.now(timezone.utc)-timedelta(days=p['lookback_days'])).strftime('%d-%b-%Y')
        found=sorted({int(u) for u in client.search(cfg['inbox'],'UID',str(last+1)+':*','SINCE',since,'UNDELETED') if str(u).isdigit() and int(u)>last})
        pending=desk.db.execute("SELECT * FROM commitments_v568 WHERE owner=? AND state NOT IN ('satisfied','cancelled')",(owner,)).fetchall();count=0
        for uid in found[:50]:
            mail=client.fetch(cfg['inbox'],str(uid))
            if mail.uidvalidity!=validity:raise Stop('uidvalidity_modifie_pendant_scan')
            for c in pending:
                evidence=json.loads(c['evidence']);mid=evidence['message_id']
                if mid not in mail.refs or mail.sender not in addresses(c['recipient']) or mail.timestamp<=datetime.fromisoformat(evidence['source_at']):continue
                if '\\Deleted' in mail.flags:continue
                quote=proactive568.own_text(mail.text)
                payload={'title':'Réponse reçue à une demande de pièces','quote':quote[:1200],'commitment':c['id'],
                         'evidence':{'folder':cfg['inbox'],'uid':str(uid),'uidvalidity':validity,'message_id':mail.mid,'source_at':mail.timestamp.isoformat()},
                         'reason':'Réponse rattachée par les en-têtes du fil. Vérifier les pièces avant confirmation d’exécution.'}
                proactive568.record(desk,owner,'received',mail.key(account),proactive568.ident(mid),'E04',payload,c['matter'],mail.timestamp.isoformat())
                desk.db.execute("UPDATE commitments_v568 SET state='response_detected',updated=? WHERE id=? AND state NOT IN ('satisfied','cancelled')",(desk.now(),c['id']));desk.db.commit();count+=1
            last=uid
            desk.setting(key,{'uid':last,'validity':validity,'checked':desk.now(),'complete':False})
        desk.setting(key,{'uid':last,'validity':validity,'checked':desk.now(),'complete':len(found)<=50})
        return {'checked':min(len(found),50),'responses':count,'coverage_complete':len(found)<=50,'remote_write':False}
    finally:
        if box is None:client.close()


def due(desk,owner='cabinet'):
    p=settings568.profile(desk,owner)
    if not p['enabled'] or 'A1' not in p['roles']:return
    cfg=desk.c['mail'];account=cfg['host']+'|'+cfg['username']
    cursor=desk.settings('proactive568:inbox_cursor:'+proactive568.ident(owner,account,cfg['inbox']),{})
    if not cursor.get('complete') or cursor.get('checked','')<(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat():return
    today=datetime.now(ZoneInfo(p['timezone'])).date().isoformat()
    rows=desk.db.execute("SELECT c.*,e.payload FROM commitments_v568 c JOIN events_v568 e ON e.id=c.event_id WHERE c.owner=? AND e.kind='E03' AND c.due<>'' AND c.due<=? AND c.condition_text='' AND c.state IN ('detected','confirmed','ready') AND e.state NOT IN ('dismissed','superseded') LIMIT 10",(owner,today)).fetchall()
    for c in rows:
        eid=proactive568.record(desk,owner,'followup',c['id'],c['due'],'E05',
                {'title':'Relance proposée : '+json.loads(c['payload']).get('subject',''), 'commitment':c['id'],
                 'due':c['due'],'inbox_checked':cursor['checked'],'recipient':c['recipient'],'quote':c['quote']},c['matter'])
        state=desk.db.execute('SELECT state FROM events_v568 WHERE id=?',(eid,)).fetchone()[0]
        if state in ('verified','dismissed','superseded','error','abstained'):continue
        if c['matter'] and settings568.mode(desk,owner,c['matter'])!='observe':desk.enqueue('followup568_prepare',{'event':eid,'owner':owner},priority=20)


def prepare(desk,eid,owner='cabinet',box=None):
    ev=desk.db.execute("SELECT * FROM events_v568 WHERE id=? AND owner=? AND kind='E05'",(eid,owner)).fetchone()
    if not ev or ev['state'] in ('dismissed','superseded'):raise Stop('relance_absente')
    payload=json.loads(ev['payload']);c=desk.db.execute('SELECT * FROM commitments_v568 WHERE id=?',(payload['commitment'],)).fetchone()
    def abstain(message):
        desk.db.execute("UPDATE events_v568 SET state='abstained',explanation=?,updated=? WHERE id=?",(message,desk.now(),eid));desk.db.commit()
        return {'abstention':message,'message':message,'status':'blocked','event':eid,'matter':ev['matter']}
    if not c or c['state'] not in ('detected','confirmed','ready'):return abstain('Une réponse ou une décision a suspendu la relance.')
    if settings568.mode(desk,owner,c['matter'])=='observe':raise Stop('relance_automatique_non_autorisee')
    settings568.check_owner(desk,owner)
    cfg=desk.c['mail'];to=addresses(c['recipient'])
    if len(to)!=1:raise Stop('destinataire_relance_a_preciser')
    source=json.loads(c['evidence']);client=box or Mailbox(cfg)
    path=Path(desk.c['state_dir'])/'followups568'/ (eid+'.json')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        # Le worker détient déjà le verrou global des écritures. Ce verrou
        # spécifique protège aussi les appels directs, sans reprendre ce verrou
        # global (flock n'est pas réentrant sur deux descripteurs distincts).
        with path.with_suffix('.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Stop('traitement_deja_en_cours') from None
            existing=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
            original=client.fetch(cfg['sent'],source['uid'])
            if original.uidvalidity!=source['uidvalidity'] or original.mid!=source['message_id'] or original.sender!=c['author']:raise Stop('source_relance_modifiee')
            thread=client.thread(original)
            if any(m.mailbox==cfg['inbox'] and m.sender in to and m.timestamp>original.timestamp for m in thread):
                desk.db.execute("UPDATE commitments_v568 SET state='response_detected',updated=? WHERE id=?",(desk.now(),c['id']));desk.db.commit()
                return abstain('Une réponse est déjà présente dans le fil relu ; aucune relance déposée.')
            # Restriction applicative immédiate avant le dépôt.
            from .common import load_matters
            matter=next((m for m in load_matters(desk.c) if m['id']==c['matter']),None)
            if not matter or not any(x.get('email')==to[0] and x.get('role')=='client' for x in matter.get('correspondents',[])):
                raise Stop('destinataire_relance_client_non_confirme')
            from .conflicts500 import gate
            gate(desk,c['matter'])
            date=original.timestamp.astimezone(ZoneInfo(settings568.profile(desk,owner)['timezone'])).strftime('%d/%m/%Y')
            body='Bonjour,\n\nÀ la suite de mon courriel du '+date+', pourriez-vous nous indiquer où en est la transmission des éléments demandés ?\n\nSi vous les avez déjà transmis, je vous remercie de nous le préciser.\n\nBien cordialement,\n'+str(cfg.get('from_name') or 'Le cabinet')
            draft=EmailMessage();draft['From']=formataddr((cfg.get('from_name',''),cfg['from_address']));draft['To']=to[0]
            draft['Subject']=('Re: ' if not original.subject.lower().startswith('re:') else '')+original.subject
            draft['Message-ID']='<axiorhub-'+eid+'@mail-agent.local>';draft['In-Reply-To']=original.mid
            draft['References']=' '.join(list(dict.fromkeys(original.refs+[original.mid])))
            draft['X-AxiorHub-Draft-Key']=eid;draft['X-AxiorHub-Automation']='followup568';draft.set_content(body)
            if existing and existing.get('body')!=body:raise Stop('relance_preparee_differente_relecture_requise')
            found=client.find_own_draft(str(draft['Message-ID']))
            if not found:
                if existing and existing.get('state') in ('appending','uncertain','verified'):raise Stop('depot_relance_incertain_ne_pas_dupliquer')
                if desk.c.get('mode')!='drafts' or not desk.settings('automation:automatic_mail_drafts_enabled',desk.c.get('orchestrator',{}).get('automatic_mail_drafts_enabled',True)):raise Stop('depot_brouillon_automatique_desactive')
                if settings568.mode(desk,owner,c['matter'])=='observe':raise Stop('relance_automatique_non_autorisee')
                settings568.check_owner(desk,owner)
                private_json(path,{'state':'appending','body':body,'message_id':str(draft['Message-ID']),'at':desk.now()})
                try:client.append_draft(draft)
                except BaseException:
                    private_json(path,{'state':'uncertain','body':body,'message_id':str(draft['Message-ID']),'at':desk.now()});raise
            proof=client.verify_draft(draft)
            private_json(path,{'state':'verified','body':body,'message_id':str(draft['Message-ID']),'proof':proof,'at':desk.now()})
            desk.db.execute("UPDATE events_v568 SET state='verified',explanation=?,updated=? WHERE id=?",('Brouillon de relance neutre relu dans '+cfg['drafts']+'. Aucun envoi.',desk.now(),eid));desk.db.commit()
            from .live430 import emit
            emit(desk,'produced','Brouillon de relance neutre vérifié dans '+cfg['drafts'],matter=c['matter'],dedupe='followup568-'+eid)
            return {'brouillon_imap':'verifie','draft_verified':proof,'proof':proof,'sent':False,'event':eid,
                    'matter':c['matter'],'dossier':cfg['drafts'],'source_ids':[source['message_id']]}
    finally:
        if box is None:client.close()

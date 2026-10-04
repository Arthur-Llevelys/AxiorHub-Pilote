"""Human-confirmed action tickets for conversational clients.

The API never accepts arbitrary job names.  A mail-draft request is first stored
as an expiring proposal.  Only a second request carrying its one-time challenge
can queue the bounded existing job.  Sending mail is not an available action.
"""
from datetime import datetime, timezone, timedelta
import json
import re
import secrets

from .common import Stop, digest
from .desk import report_for


ACTIONS={'prepare_reply':'Préparer et, si les contrôles réussissent, déposer un brouillon dans la messagerie'}


def ensure_schema(desk):
    desk.db.executescript('''
    CREATE TABLE IF NOT EXISTS supervision_requests(
      id TEXT PRIMARY KEY, action TEXT NOT NULL, args TEXT NOT NULL,
      summary TEXT NOT NULL, challenge_hash TEXT NOT NULL, status TEXT NOT NULL,
      created TEXT NOT NULL, expires TEXT NOT NULL, decided TEXT NOT NULL,
      job_id INTEGER, result TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS supervision_status
      ON supervision_requests(status,expires,created);
    ''');desk.db.commit()


def _clean(value,maximum):
    value=re.sub(r'[\x00-\x1f]',' ',str(value or '')).strip()
    if not value or len(value)>maximum:raise Stop('instruction_supervisee_invalide')
    return value


def propose(desk,args):
    ensure_schema(desk);action=args.get('action','')
    if action not in ACTIONS:raise Stop('action_supervisee_invalide')
    key=str(args.get('mail_key',''))
    if not re.fullmatch(r'[a-f0-9]{64}',key):raise Stop('cle_invalide')
    report=report_for(desk.c,key);instruction=_clean(args.get('instruction'),2000)
    code=f'{secrets.randbelow(900000)+100000}'
    request_id=secrets.token_hex(16);stamp=desk.now()
    minutes=max(5,min(int(desk.c.get('integration',{}).get('approval_minutes',30)),120))
    expires=(datetime.now(timezone.utc)+timedelta(minutes=minutes)).isoformat()
    payload={'key':key,'instruction':instruction}
    summary=('Préparer une réponse au courriel « '+str(report.get('subject','Sans objet'))[:180]+
      ' » de '+str(report.get('sender',''))[:180]+'. Le résultat pourra seulement être déposé dans '+
      desk.c['mail']['drafts']+' ; aucun envoi.')
    desk.db.execute('INSERT INTO supervision_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
      (request_id,action,json.dumps(payload,ensure_ascii=False),summary,digest(request_id+'|'+code),
       'pending',stamp,expires,'',None,''));desk.db.commit()
    desk.audit('supervision_proposee',{'request':request_id,'action':action,'mail_key':key})
    return {'approval_id':request_id,'confirmation_code':code,'expires_at':expires,
      'summary':summary,'instruction':instruction,
      'next_step':'Demandez à l’avocat de relire ce résumé et de recopier le code. Ne confirmez jamais à sa place.'}


def approve(desk,args):
    ensure_schema(desk);rid=str(args.get('approval_id',''));code=str(args.get('confirmation_code','')).strip()
    if not re.fullmatch(r'[a-f0-9]{32}',rid) or not re.fullmatch(r'\d{6}',code):raise Stop('confirmation_supervisee_invalide')
    row=desk.db.execute('SELECT * FROM supervision_requests WHERE id=?',(rid,)).fetchone()
    if not row:raise Stop('demande_supervisee_absente')
    if row['status']!='pending':raise Stop('demande_supervisee_deja_traitee')
    if datetime.now(timezone.utc)>=datetime.fromisoformat(row['expires']):
        desk.db.execute("UPDATE supervision_requests SET status='expired',decided=? WHERE id=?",(desk.now(),rid));desk.db.commit()
        raise Stop('confirmation_supervisee_expiree')
    if not secrets.compare_digest(row['challenge_hash'],digest(rid+'|'+code)):
        desk.audit('supervision_code_refuse',{'request':rid});raise Stop('confirmation_supervisee_invalide')
    payload=json.loads(row['args']);job=desk.enqueue(row['action'],payload,priority=0)
    desk.db.execute("UPDATE supervision_requests SET status='approved',decided=?,job_id=? WHERE id=?",(desk.now(),job,rid));desk.db.commit()
    desk.audit('supervision_confirmee',{'request':rid,'action':row['action'],'job':job})
    return {'approval_id':rid,'status':'approved','job_id':job,
      'message':'Confirmation enregistrée. Le brouillon est maintenant en préparation ; aucun envoi automatique.'}


def reject(desk,args):
    ensure_schema(desk);rid=str(args.get('approval_id',''))
    row=desk.db.execute("SELECT status FROM supervision_requests WHERE id=?",(rid,)).fetchone()
    if not row:raise Stop('demande_supervisee_absente')
    if row['status']!='pending':raise Stop('demande_supervisee_deja_traitee')
    desk.db.execute("UPDATE supervision_requests SET status='rejected',decided=? WHERE id=?",(desk.now(),rid));desk.db.commit()
    desk.audit('supervision_refusee',{'request':rid});return {'approval_id':rid,'status':'rejected'}


def requests(desk,status='',limit=50):
    ensure_schema(desk);limit=max(1,min(int(limit),100));params=[];where=''
    desk.db.execute("UPDATE supervision_requests SET status='expired',decided=? WHERE status='pending' AND expires<=?",(desk.now(),desk.now()));desk.db.commit()
    if status:
        if status not in {'pending','approved','rejected','expired'}:raise Stop('etat_supervision_invalide')
        where=' WHERE status=?';params.append(status)
    params.append(limit)
    return [dict(x) for x in desk.db.execute('SELECT id,action,summary,status,created,expires,decided,job_id,result FROM supervision_requests'+where+' ORDER BY created DESC LIMIT ?',params)]

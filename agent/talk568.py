"""Nextcloud Talk OCS v4 : salon privé, relecture, pas d'invitation ni de partage."""
from .portable import fcntl   # 5.6.25 : verrous portables Linux / Windows
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, quote

from .common import HTTP, Stop, read_secret
from . import settings568

API='/ocs/v2.php/apps/spreed/api/v4'


class Talk:
    def __init__(self,cfg):
        self.cfg=cfg
        self.http=HTTP(cfg['url'],cfg['username'],read_secret(cfg['password_file']),timeout=30)
        self.http.headers.update({'OCS-APIRequest':'true','Accept':'application/json'})

    def call(self,method,path,data=None):
        raw=self.http.request(method,self.http.base+API+path+('?format=json' if '?' not in path else '&format=json'),
               None if data is None else urlencode(data).encode(),{'Content-Type':'application/x-www-form-urlencoded'},2_000_000)
        try:
            obj=json.loads(raw)['ocs'];code=int(obj['meta']['statuscode'])
            if code not in (100,200,201):raise ValueError()
            return obj['data']
        except (KeyError,ValueError,TypeError):raise Stop('reponse_talk_invalide') from None

    def find(self,name):
        rows=self.call('GET','/room')
        if not isinstance(rows,list):raise Stop('liste_talk_invalide')
        found=[r for r in rows if r.get('name')==name]
        if len(found)>1:raise Stop('plusieurs_salons_talk_a_reconcilier')
        return str(found[0].get('token') or '') if found else ''

    def create(self,name):
        data=self.call('POST','/room',{'roomType':2,'roomName':name})
        return str(data.get('token') or '')

    def verify(self,token,name):
        if not re.fullmatch(r'[A-Za-z0-9]{4,128}',token):raise Stop('jeton_salon_talk_invalide')
        room=self.call('GET','/room/'+token)
        participants=self.call('GET','/room/'+token+'/participants')
        if int(room.get('type',0))!=2 or (name is not None and room.get('name')!=name) or int(room.get('listable',0))!=0:raise Stop('salon_talk_non_prive')
        if not isinstance(participants,list) or len(participants)!=1:raise Stop('salon_talk_participants_inattendus')
        user=participants[0]
        actor=str(user.get('actorId') or user.get('userId') or '')
        if actor!=self.cfg['username'] or user.get('actorType','users')!='users':raise Stop('salon_talk_participant_inattendu')
        return {'type':2,'participants':1,'creator':actor,'read_at':'server_readback'}


def prepare(desk,eid,owner='cabinet',client=None):
    row=desk.db.execute('SELECT * FROM events_v568 WHERE id=? AND owner=?',(eid,owner)).fetchone()
    if not row or row['kind']!='E11' or row['state'] in ('dismissed','superseded'):raise Stop('rendez_vous_talk_absent')
    p=settings568.profile(desk,owner);payload=json.loads(row['payload'])
    if not p['talk_enabled'] or not payload.get('video') or 'A5' not in p['roles'] or not row['matter'] or settings568.mode(desk,owner,row['matter'])=='observe':raise Stop('creation_talk_non_autorisee')
    settings568.check_owner(desk,owner)
    root=Path(desk.c['state_dir'])/'talk568';root.mkdir(mode=0o700,exist_ok=True);os.chmod(root,0o700)
    lockpath=root/(eid+'.lock')
    with lockpath.open('a') as lock:
        os.chmod(lockpath,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        client=client or Talk(desk.c['nextcloud'])
        name='AxiorHub · rendez-vous '+eid[:24]  # aucun nom de client dans un nom de salon
        existing=desk.db.execute('SELECT * FROM talk_rooms_v568 WHERE event_id=?',(eid,)).fetchone()
        token=str(existing['token']) if existing else str(payload.get('talk_token') or '')
        if not token:token=client.find(name)
        if not token:
            if existing and existing['state'] in ('creating','uncertain'):
                raise Stop('creation_talk_incertitude_reconcilier_avant_relance')
            if settings568.mode(desk,owner,row['matter'])=='observe':raise Stop('creation_talk_non_autorisee')
            desk.db.execute('INSERT OR REPLACE INTO talk_rooms_v568 VALUES(?,?,?,?,?,?,?)',(eid,owner,name,'','creating','',desk.now()));desk.db.commit()
            try:token=client.create(name)
            except BaseException:
                desk.db.execute("UPDATE talk_rooms_v568 SET state='uncertain',updated=? WHERE event_id=?",(desk.now(),eid));desk.db.commit();raise
            desk.db.execute('UPDATE talk_rooms_v568 SET token=?,updated=? WHERE event_id=?',(token,desk.now(),eid));desk.db.commit()
        proof=client.verify(token,None if payload.get('talk_token') else name)
        proof['verified_at']=desk.now()
        desk.db.execute('INSERT OR REPLACE INTO talk_rooms_v568 VALUES(?,?,?,?,?,?,?)',(eid,owner,name,token,'verified',json.dumps(proof),desk.now()));desk.db.commit()
        url=desk.c['nextcloud']['url'].rstrip('/')+'/call/'+quote(token,safe='')
        from .live430 import emit
        emit(desk,'produced','Salon Talk privé préparé et relu. Aucune invitation envoyée.',matter=row['matter'],dedupe='talk568-'+eid)
        return {'state':'verified','url':url,'proof':proof,'invitation_sent':False,'calendar_modified':False}


def diagnostic(desk):
    rows=Talk(desk.c['nextcloud']).call('GET','/room')
    return {'ok':isinstance(rows,list),'message':'Lecture Talk OCS v4 réussie. Aucun salon ni invitation créé.','remote_write':False}


def linked_token(desk,event):
    """Réutiliser un lien existant du serveur Nextcloud configuré, jamais un hôte fourni par le calendrier."""
    base=str(desk.c['nextcloud'].get('url') or '').rstrip('/')+'/call/'
    if not base.startswith('https://'):return ''
    text=str(event.get('location') or '')+' '+str(event.get('description') or '')
    match=re.search(re.escape(base)+r'([A-Za-z0-9]{4,128})(?=$|[\s/?#<>)])',text)
    return match[1] if match else ''
